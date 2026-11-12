//! Bounded launch coordination, independent of the window runtime.
use std::sync::atomic::{AtomicU32, Ordering};
use crate::edition::{self, EditionProfile};

#[derive(Clone, Copy)]
pub struct View { pub name: &'static str, pub title: &'static str, pub port: u16, pub probe: &'static str, pub bit: u32, profile: &'static EditionProfile }
pub fn view(name: &str) -> Option<View> {
    view_for(edition::current().ok()?, name)
}
pub fn view_for(profile: &'static EditionProfile, name: &str) -> Option<View> {
    let (name, service, probe, bit) = match name {
        "sentinel" => ("sentinel", "sentinel", "/api/health", 1),
        "alarm" => ("alarm", "alarm", "/api/ring", 2),
        "insights" => ("insights", "insights_dashboard", "/", 4),
        _ => return None,
    };
    Some(View { name, title: profile.app_name(name).ok()?, port: profile.port(service).ok()?, probe, bit, profile })
}
impl View {
    pub fn ready(self, body: Option<&str>, about: Option<&str>) -> bool {
        identity_matches(self.profile, about) && self.response_ready(body)
    }
    fn response_ready(self, body: Option<&str>) -> bool {
        let Some(body) = body else { return false };
        if self.name == "insights" { return body.to_ascii_lowercase().contains("<html"); }
        let Ok(value) = serde_json::from_str::<serde_json::Value>(body) else { return false };
        if self.name == "alarm" { value.get("is_ringing").and_then(|v| v.as_bool()).is_some() }
        else { value.get("ok").and_then(|v| v.as_bool()) == Some(true) }
    }
}

// Keep the legacy exception narrow: installed 2.2.16 has these four required
// fields but no edition. Deserialize a struct to reject duplicate identity keys.
#[derive(Default)]
enum Identity { #[default] Missing, Named(String) }
impl<'de> serde::Deserialize<'de> for Identity {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        <String as serde::Deserialize>::deserialize(d).map(Self::Named)
    }
}
#[derive(serde::Deserialize)]
struct About {
    version: String,
    #[serde(rename = "dev")]
    _dev: bool,
    data: String,
    repo: String,
    #[serde(default)]
    edition: Identity,
}
pub fn identity_matches(profile: &EditionProfile, body: Option<&str>) -> bool {
    let Some(body) = body else { return false };
    let Ok(about) = serde_json::from_str::<About>(body) else { return false };
    let version: Vec<_> = about.version.split('.').collect();
    if version.len() != 3 || version.iter().any(|s| s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()))
        || about.data.trim().is_empty() || about.repo.trim().is_empty() { return false; }
    match about.edition {
        Identity::Named(name) => name == profile.edition(),
        Identity::Missing => profile.edition() == "Public" && about.version == "2.2.16",
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CoreReadiness { Absent, Unidentified, Starting, Ready, Conflict }

/// An occupied owned port is never permission to start another core. Inspect
/// both primary services before attaching, and all owned ports before spawning.
/// A missing response is Unidentified, so callers can keep a bounded loading
/// window without attaching or spawning. Only observed invalid metadata conflicts.
/// Closures allow tests to model startup without sockets or child processes.
pub fn core_readiness(
    profile: &'static EditionProfile,
    mut occupied: impl FnMut(u16) -> bool,
    mut get: impl FnMut(u16, &str) -> Option<String>,
) -> CoreReadiness {
    let ports: Vec<_> = profile.ports().values().copied().filter(|p| occupied(*p)).collect();
    if ports.is_empty() { return CoreReadiness::Absent; }
    let mut identified = 0;
    let mut ready = 0;
    let mut unidentified = false;
    for name in ["alarm", "sentinel"] {
        let view = view_for(profile, name).expect("validated owned service");
        if !ports.contains(&view.port) { continue; }
        let about = get(view.port, "/api/about");
        if about.is_none() { unidentified = true; continue; }
        if !identity_matches(profile, about.as_deref()) { return CoreReadiness::Conflict; }
        identified += 1;
        if view.response_ready(get(view.port, view.probe).as_deref()) { ready += 1; }
    }
    if ready == 2 { CoreReadiness::Ready }
    else if unidentified { CoreReadiness::Unidentified }
    else if identified > 0 { CoreReadiness::Starting }
    else { CoreReadiness::Unidentified }
}

#[derive(Debug, PartialEq, Eq)]
pub struct BackgroundWorkers { pub alarm_watch: bool, pub wake_timer: bool, pub update_notifier: bool }
impl BackgroundWorkers {
    pub fn for_profile(profile: &EditionProfile) -> Self {
        Self::for_capabilities(profile.is_dev(), profile.capabilities())
    }
    fn for_capabilities(is_dev: bool, caps: &edition::Capabilities) -> Self {
        Self {
            alarm_watch: !is_dev && caps.alarm_delivery_enabled,
            wake_timer: !is_dev && caps.wake_enabled,
            update_notifier: !is_dev && caps.github_self_update_enabled,
        }
    }
}

pub fn http_body(response: &str) -> Option<&str> {
    let (headers, body) = response.split_once("\r\n\r\n")?;
    let mut status = headers.lines().next()?.split_ascii_whitespace();
    if !matches!(status.next()?, "HTTP/1.0" | "HTTP/1.1") || status.next()? != "200" { return None; }
    Some(body)
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
    use serde_json::json;

    fn about(edition: Option<&str>) -> String {
        let mut body = json!({"version":"2.2.16", "dev":false, "data":"fixture/data", "repo":"fixture/suite"});
        if let Some(name) = edition { body["edition"] = json!(name); }
        body.to_string()
    }

    #[test]
    fn profile_routes_keep_public_compatible_and_dev_separate() {
        for (edition, ports, titles) in [
            ("Public", [8084, 8081, 8301], ["PC Sentinel", "Code Red Alarm", "Ultra Suite Insights"]),
            ("Dev", [18084, 18081, 18301], ["PC Sentinel (Dev)", "Code Red Alarm (Dev)", "Insights (Dev)"]),
        ] {
            let profile = edition::profile(edition).unwrap();
            for ((name, port), title) in ["sentinel", "alarm", "insights"].into_iter().zip(ports).zip(titles) {
                let view = view_for(profile, name).unwrap();
                assert_eq!((view.name, view.port, view.title), (name, port, title));
            }
            for name in ["hub", "", "../alarm", "Alarm", "http://example.com"] { assert!(view_for(profile, name).is_none()); }
        }
        let current = edition::current().unwrap();
        assert_eq!(current.edition(), if cfg!(feature = "dev-edition") { "Dev" } else { "Public" });
        assert_eq!(view("alarm").unwrap().port, current.port("alarm").unwrap());
    }

    #[test]
    fn readiness_requires_identity_on_every_view() {
        for edition in ["Public", "Dev"] {
            let profile = edition::profile(edition).unwrap();
            for (name, response) in [("alarm", r#"{"is_ringing":false}"#), ("sentinel", r#"{"ok":true}"#), ("insights", "<html lang=en>")] {
                let view = view_for(profile, name).unwrap();
                assert!(view.ready(Some(response), Some(&about(Some(edition)))));
                assert!(!view.ready(Some(response), Some(&about(Some(if edition == "Dev" { "Public" } else { "Dev" })))));
                assert!(!view.ready(Some(response), None));
                assert!(!view.ready(Some(response), Some("{}")));
                assert!(!view.ready(Some("{}"), Some(&about(Some(edition)))));
                assert_eq!(view.ready(Some(response), Some(&about(None))), edition == "Public");
            }
        }
    }

    #[test]
    fn malformed_foreign_and_incompatible_legacy_about_fail_closed() {
        let public = edition::profile("Public").unwrap();
        let dev = edition::profile("Dev").unwrap();
        assert!(identity_matches(public, Some(&about(None))));
        assert!(!identity_matches(dev, Some(&about(None))));
        for body in ["", "{}", "[]", "null", "not JSON", r#"{"edition":"Public"}"#] {
            assert!(!identity_matches(public, Some(body)), "{body}");
            assert!(!identity_matches(dev, Some(body)), "{body}");
        }
        for edition in [None, Some("Public"), Some("Dev")] {
            let valid: serde_json::Value = serde_json::from_str(&about(edition)).unwrap();
            for key in ["version", "dev", "data", "repo"] {
                let mut body = valid.clone();
                body.as_object_mut().unwrap().remove(key);
                assert!(!identity_matches(public, Some(&body.to_string())), "missing {key}");
                body[key] = json!(null);
                assert!(!identity_matches(public, Some(&body.to_string())), "null {key}");
            }
        }
        for (key, invalid) in [
            ("version", json!("2")), ("version", json!("2.2.x")), ("version", json!("2.2.16 ")),
            ("dev", json!("false")), ("data", json!(" ")), ("repo", json!("")),
            ("edition", json!(null)), ("edition", json!(1)), ("edition", json!("public")), ("edition", json!("Other")),
        ] {
            let mut body: serde_json::Value = serde_json::from_str(&about(Some("Public"))).unwrap();
            body[key] = invalid;
            assert!(!identity_matches(public, Some(&body.to_string())), "{body}");
        }
        let mut legacy: serde_json::Value = serde_json::from_str(&about(None)).unwrap();
        for version in ["2.2.15", "2.2.17", "9.9.9"] {
            legacy["version"] = json!(version);
            assert!(!identity_matches(public, Some(&legacy.to_string())));
        }
        let duplicate = about(Some("Public")).replacen('{', r#"{"edition":"Dev","#, 1);
        assert!(!identity_matches(public, Some(&duplicate)));
        let trailing = format!("{}{{}}", about(Some("Public")));
        assert!(!identity_matches(public, Some(&trailing)));
    }

    #[test]
    fn fake_core_readiness_never_treats_occupied_unknown_ports_as_absent() {
        use CoreReadiness::*;
        for edition in ["Public", "Dev"] {
            let profile = edition::profile(edition).unwrap();
            let alarm = profile.port("alarm").unwrap();
            let sentinel = profile.port("sentinel").unwrap();
            let get = |port, path: &str| Some(if path == "/api/about" { about(Some(edition)) }
                else if port == alarm { r#"{"is_ringing":false}"#.into() } else { r#"{"ok":true}"#.into() });
            assert_eq!(core_readiness(profile, |_| false, |_, _| panic!("absent ports must not be queried")), Absent);
            assert_eq!(core_readiness(profile, |p| p == alarm || p == sentinel, get), Ready);
            assert_eq!(core_readiness(profile, |p| p == alarm, get), Starting);
            assert_eq!(core_readiness(profile, |p| p == sentinel, get), Starting);
            assert_eq!(core_readiness(profile, |p| p == alarm || p == sentinel,
                |_, path| Some(if path == "/api/about" { about(Some(edition)) } else { "{}".into() })), Starting);
            assert_eq!(core_readiness(profile, |p| p == alarm || p == sentinel, |_, _| None), Unidentified);
            for body in [Some(String::new()), Some("{}".into()), Some("not JSON".into()), Some(about(Some("Other"))),
                Some(about(Some(if edition == "Public" { "Dev" } else { "Public" })))] {
                assert_eq!(core_readiness(profile, |p| p == alarm || p == sentinel, |_, _| body.clone()), Conflict);
            }
            for service in profile.ports().keys() {
                let port = profile.port(service).unwrap();
                assert_eq!(core_readiness(profile, |p| p == port, |_, _| None), Unidentified, "{edition} {service}");
            }
            // One correct service cannot hide a foreign sibling.
            assert_eq!(core_readiness(profile, |_| true, |port, path| {
                if port == sentinel && path == "/api/about" { Some(about(Some("Other"))) } else { get(port, path) }
            }), Conflict);
            assert_eq!(core_readiness(profile, |p| p == alarm || p == sentinel, |port, path| {
                if path == "/api/about" { Some(about(None)) } else { get(port, path) }
            }), if edition == "Public" { Ready } else { Conflict });
        }
    }

    #[test]
    fn unidentified_startup_retries_until_metadata_and_service_probes_are_ready() {
        use CoreReadiness::*;
        for edition in ["Public", "Dev"] {
            let profile = edition::profile(edition).unwrap();
            let alarm = profile.port("alarm").unwrap();
            let mut fake_spawns = 0;
            let mut fake_navigations = 0;
            // A single child binds before serving metadata, then metadata and
            // normal service responses become available on separate ticks.
            for (tick, expected) in [Absent, Unidentified, Unidentified, Starting, Ready].into_iter().enumerate() {
                let get = |port, path: &str| {
                    if path == "/api/about" {
                        if tick < 2 || (tick == 2 && port != alarm) { None } else { Some(about(Some(edition))) }
                    } else if tick < 4 { Some("{}".into()) }
                    else if port == alarm { Some(r#"{"is_ringing":false}"#.into()) }
                    else { Some(r#"{"ok":true}"#.into()) }
                };
                let state = core_readiness(profile, |_| tick > 0, get);
                assert_eq!(state, expected, "{edition} startup tick {tick}");
                if state == Absent { fake_spawns += 1; }
                assert_ne!(state, Conflict, "missing transport responses must retain the loading window");
                let view = view_for(profile, "alarm").unwrap();
                if state != Conflict && view.ready(get(alarm, view.probe).as_deref(), get(alarm, "/api/about").as_deref()) {
                    fake_navigations += 1;
                }
                assert_eq!(fake_spawns, 1, "never spawn another child into occupied ports");
                assert_eq!(fake_navigations, usize::from(tick == 4), "never navigate before identity and probe validation");
            }
        }
    }

    #[test]
    fn unidentified_ports_cannot_attach_and_do_not_hide_observed_foreign_metadata() {
        use CoreReadiness::*;
        for edition in ["Public", "Dev"] {
            let profile = edition::profile(edition).unwrap();
            let alarm = profile.port("alarm").unwrap();
            let sentinel = profile.port("sentinel").unwrap();
            let view = view_for(profile, "alarm").unwrap();
            let probe = Some(r#"{"is_ringing":false}"#);
            // Persistent unidentified occupancy never becomes permission to
            // spawn or attach, including after a previously valid response.
            for available in [false, false, true, false, false] {
                let metadata = available.then(|| about(Some(edition)));
                let state = core_readiness(profile, |p| p == alarm || p == sentinel, |port, path| {
                    if path == "/api/about" { metadata.clone() }
                    else if port == alarm { probe.map(str::to_string) }
                    else { Some(r#"{"ok":true}"#.into()) }
                });
                assert_eq!(state, if available { Ready } else { Unidentified });
                assert_ne!(state, Absent, "an occupied port must never allow a second spawn");
                assert_eq!(view.ready(probe, metadata.as_deref()), available);
            }
            // Check both primary ports even when the first is unidentified.
            // Explicitly malformed/foreign metadata remains a prompt conflict.
            for bad in ["{}".to_string(), "not JSON".to_string(), about(Some("Other")),
                about(Some(if edition == "Public" { "Dev" } else { "Public" }))] {
                assert_eq!(core_readiness(profile, |p| p == alarm || p == sentinel, |port, path| {
                    assert_eq!(path, "/api/about", "unidentified/foreign services must not be probed as ready");
                    if port == alarm { None } else { Some(bad.clone()) }
                }), Conflict);
            }
        }
    }

    #[test]
    fn dev_background_workers_remain_disabled_independent_of_runtime_readiness() {
        let public = edition::profile("Public").unwrap();
        let dev = edition::profile("Dev").unwrap();
        assert_eq!(BackgroundWorkers::for_profile(public), BackgroundWorkers { alarm_watch: true, wake_timer: true, update_notifier: true });
        assert_eq!(BackgroundWorkers::for_profile(dev), BackgroundWorkers { alarm_watch: false, wake_timer: false, update_notifier: false });
        assert!(!dev.runtime_ready());
        // Even incorrectly enabled future capability inputs must not activate
        // Dev workers. This fixture does not modify either immutable contract.
        let mut caps = edition::Capabilities {
            alarm_delivery_enabled: true, wake_enabled: true, github_self_update_enabled: true,
            dock_enabled_by_default: true, production_telemetry_allowed: true,
        };
        assert_eq!(BackgroundWorkers::for_capabilities(true, &caps), BackgroundWorkers { alarm_watch: false, wake_timer: false, update_notifier: false });
        caps.alarm_delivery_enabled = false;
        assert_eq!(BackgroundWorkers::for_capabilities(false, &caps), BackgroundWorkers { alarm_watch: false, wake_timer: true, update_notifier: true });
        caps.wake_enabled = false;
        caps.github_self_update_enabled = false;
        assert_eq!(BackgroundWorkers::for_capabilities(false, &caps), BackgroundWorkers { alarm_watch: false, wake_timer: false, update_notifier: false });
        // The worker plan never consults runtime_ready, so removing the native
        // startup refusal in a future milestone cannot enable these workers.
    }

    #[test]
    fn readiness_http_requires_success_and_preserves_complete_body() {
        for response in ["", "{}", "HTTP/1.1 404 Not Found\r\n\r\n{}", "HTTP/1.1 500 Error\r\n\r\n{}",
            "HTTP/1.1 200 OK\r\n{}", "HTTP/1.1 2000 OK\r\n\r\n{}", "OTHER 200 OK\r\n\r\n{}"] {
            assert_eq!(http_body(response), None);
        }
        for protocol in ["HTTP/1.0", "HTTP/1.1"] {
            let response = format!("{protocol} 200 OK\r\nContent-Type: text/html\r\n\r\n<html>\r\n\r\nbody</html>");
            assert_eq!(http_body(&response), Some("<html>\r\n\r\nbody</html>"));
        }
    }
    #[test]
    fn readiness_requires_the_expected_service_response() {
        let sentinel=view("sentinel").unwrap();let alarm=view("alarm").unwrap();
        for input in [None, Some(""), Some("{}"), Some("not JSON"), Some("{\"ok\":false}"), Some("{\"ok\":\"true\"}")] {
            assert!(!sentinel.response_ready(input));assert!(!alarm.response_ready(input));
        }
        assert!(sentinel.response_ready(Some("{\"ok\":true,\"hardware_live\":false}")));
        assert!(alarm.response_ready(Some("{\"is_ringing\":false}")));
        assert!(alarm.response_ready(Some("{\"is_ringing\":true}")));
        assert!(!view("insights").unwrap().response_ready(Some("{\"detail\":\"not found\"}")));
        assert!(view("insights").unwrap().response_ready(Some("<!DOCTYPE html><html lang=en>")));
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
