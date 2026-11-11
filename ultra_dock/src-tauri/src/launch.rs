//! Bounded launch coordination, independent of the window runtime.
use std::sync::atomic::{AtomicU32, Ordering};

#[derive(Clone, Copy)]
pub struct View { pub name: &'static str, pub title: &'static str, pub port: u16, pub probe: &'static str, pub bit: u32 }
pub fn view(name: &str) -> Option<View> {
    let (title, port, probe, bit) = match name {
        "sentinel" => ("PC Sentinel", 8084, "/api/health", 1),
        "alarm" => ("Code Red Alarm", 8081, "/api/ring", 2),
        "insights" => ("Ultra Suite Insights", 8301, "/", 4),
        _ => return None,
    };
    Some(View { name: match bit { 1 => "sentinel", 2 => "alarm", _ => "insights" }, title, port, probe, bit })
}
impl View {
    pub fn ready(self, body: Option<&str>) -> bool {
        let Some(body) = body else { return false };
        if self.name == "insights" { return body.to_ascii_lowercase().contains("<html"); }
        let Ok(value) = serde_json::from_str::<serde_json::Value>(body) else { return false };
        if self.name == "alarm" { value.get("is_ringing").and_then(|v| v.as_bool()).is_some() }
        else { value.get("ok").and_then(|v| v.as_bool()) == Some(true) }
    }
}
pub struct Launches { pending: AtomicU32, loaded: AtomicU32 }
impl Launches {
    pub const fn new() -> Self { Self { pending: AtomicU32::new(0), loaded: AtomicU32::new(0) } }
    pub fn begin(&self, view: View) -> Option<Permit<'_>> {
        if self.pending.fetch_or(view.bit, Ordering::AcqRel) & view.bit != 0 { None }
        else { Some(Permit { state: self, bit: view.bit }) }
    }
    pub fn loaded(&self, view: View) -> bool { self.loaded.load(Ordering::Acquire) & view.bit != 0 }
    pub fn set_loaded(&self, view: View, ready: bool) {
        if ready { self.loaded.fetch_or(view.bit, Ordering::Release); }
        else { self.loaded.fetch_and(!view.bit, Ordering::Release); }
    }
}
pub struct Permit<'a> { state: &'a Launches, bit: u32 }
impl Drop for Permit<'_> { fn drop(&mut self) { self.state.pending.fetch_and(!self.bit, Ordering::Release); } }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn readiness_requires_the_expected_service_response() {
        let sentinel=view("sentinel").unwrap();let alarm=view("alarm").unwrap();
        for input in [None, Some(""), Some("{}"), Some("not JSON"), Some("{\"ok\":false}"), Some("{\"ok\":\"true\"}")] {
            assert!(!sentinel.ready(input));assert!(!alarm.ready(input));
        }
        assert!(sentinel.ready(Some("{\"ok\":true,\"hardware_live\":false}")));
        assert!(alarm.ready(Some("{\"is_ringing\":false}")));
        assert!(alarm.ready(Some("{\"is_ringing\":true}")));
        assert!(!view("insights").unwrap().ready(Some("{\"detail\":\"not found\"}")));
        assert!(view("insights").unwrap().ready(Some("<!DOCTYPE html><html lang=en>")));
        for name in ["", "../sentinel", "http://example.com", "Sentinel"] { assert!(view(name).is_none()); }
    }
    #[test]
    fn requests_coalesce_per_view_and_release_after_early_return() {
        let state=Launches::new();let s=view("sentinel").unwrap();let a=view("alarm").unwrap();
        let first=state.begin(s).unwrap();assert!(state.begin(s).is_none());
        let other=state.begin(a).unwrap();drop(first);assert!(state.begin(s).is_some());
        assert!(state.begin(a).is_none());drop(other);assert!(state.begin(a).is_some());
        state.set_loaded(s,true);assert!(state.loaded(s));assert!(!state.loaded(a));
        state.set_loaded(a,true);state.set_loaded(s,false);assert!(!state.loaded(s));assert!(state.loaded(a));
    }
    #[test]
    fn concurrent_click_burst_starts_one_worker() {
        use std::sync::{Arc, Barrier};
        let state=Arc::new(Launches::new());let barrier=Arc::new(Barrier::new(33));
        let workers:Vec<_>=(0..32).map(|_| {let state=state.clone();let barrier=barrier.clone();std::thread::spawn(move||{
            let permit=state.begin(view("sentinel").unwrap());let started=permit.is_some();barrier.wait();drop(permit);started
        })}).collect();
        barrier.wait();assert_eq!(workers.into_iter().map(|t|usize::from(t.join().unwrap())).sum::<usize>(),1);
        assert!(state.begin(view("sentinel").unwrap()).is_some());
    }
}
