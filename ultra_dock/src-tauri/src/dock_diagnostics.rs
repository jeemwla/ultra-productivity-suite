//! Diagnostic observations never authorize taskbar changes.

use serde_json::{json, Value};
use std::sync::{atomic::{AtomicU64,Ordering},mpsc::{sync_channel,SyncSender},OnceLock,Arc};
use std::{io::Write,path::PathBuf};

pub fn failed_checks(checks: &[bool; 7]) -> Vec<&'static str> {
    const NAMES:[&str;7]=["enabled","guard_ready","main_responsive","strip_visible",
        "strip_fits","all_taskbars_covered","rendered_age_ok"];
    NAMES.into_iter().zip(checks).filter_map(|(name,ok)|(!ok).then_some(name)).collect()
}
pub fn transition(previous: Option<bool>, healthy: bool) -> Option<&'static str> {
    match (previous,healthy) {
        (None,_)=>Some("initial"),(Some(true),false)=>Some("healthy_to_native"),
        (Some(false),true)=>Some("native_to_healthy"),_=>None,
    }
}

/// One watchdog producer; readers never wait for first-failure initialization.
pub struct Capture {
    sender:SyncSender<Value>,
    first:OnceLock<Value>,
    dropped:AtomicU64,
    output_errors:Arc<AtomicU64>,
    candidate:Value,
}
impl Capture {
    pub fn start(path:PathBuf,candidate:Value)->std::io::Result<Self> {
        let (sender,receiver)=sync_channel::<Value>(8);
        let header=candidate.clone();
        let output_errors=Arc::new(AtomicU64::new(0));
        let errors=output_errors.clone();
        std::thread::Builder::new().name("dock-health-output".into()).spawn(move||{
            if let Some(parent)=path.parent(){let _=std::fs::create_dir_all(parent);}
            let first_path=path.with_file_name(format!("dock_health_first_failure-{}-{}.json",
                std::process::id(),header["started_unix_ms"]));
            let mut first_written=false;
            for observation in receiver {
                let first=!first_written&&observation["kind"]=="healthy_to_native";
                let record=json!({"candidate":header,"observation":observation});
                let Ok(mut bytes)=serde_json::to_vec(&record) else {continue};bytes.push(b'\n');
                if bytes.len()>131_072 {errors.fetch_add(1,Ordering::Relaxed);continue;}
                if first {first_written=true;if std::fs::write(&first_path,&bytes).is_err(){errors.fetch_add(1,Ordering::Relaxed);}}
                // Only this background worker touches the bounded rolling file.
                let size=std::fs::metadata(&path).map(|m|m.len()).unwrap_or(0);
                let result=if size+bytes.len() as u64>131_072 {std::fs::write(&path,&bytes)}
                    else {std::fs::OpenOptions::new().create(true).append(true).open(&path).and_then(|mut file|file.write_all(&bytes))};
                if result.is_err(){errors.fetch_add(1,Ordering::Relaxed);}
            }
        })?;
        Ok(Self{sender,first:OnceLock::new(),dropped:AtomicU64::new(0),output_errors,candidate})
    }
    pub fn emit(&self,mut record:Value) {
        if record["kind"]=="healthy_to_native"&&self.first.get().is_none(){let _=self.first.set(record.clone());}
        record["dropped_before"]=json!(self.dropped.load(Ordering::Relaxed));
        if self.sender.try_send(record).is_err(){self.dropped.fetch_add(1,Ordering::Relaxed);}
    }
    pub fn snapshot(&self)->Value {
        json!({"candidate":self.candidate,"first_observed_health_loss":self.first.get(),
            "dropped_records":self.dropped.load(Ordering::Relaxed),"output_errors":self.output_errors.load(Ordering::Relaxed)})
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn watchdog_names_match_the_actual_decision_fields() {
        assert_eq!(failed_checks(&[true,false,false,true,false,true,true]),
            ["guard_ready","main_responsive","strip_fits"]);
        assert!(failed_checks(&[true;7]).is_empty());
        assert_eq!(failed_checks(&[false;7]).len(),7);
    }
    #[test]
    fn startup_failure_is_not_a_loss_of_observed_health() {
        assert_eq!(transition(None,false),Some("initial"));
        assert_eq!(transition(None,true),Some("initial"));
        assert_eq!(transition(Some(true),false),Some("healthy_to_native"));
        assert_eq!(transition(Some(false),true),Some("native_to_healthy"));
        assert_eq!(transition(Some(false),false),None);
        assert_eq!(transition(Some(true),true),None);
    }
    #[test]
    fn a_full_output_queue_retains_the_first_real_failure() {
        let (sender,_receiver)=sync_channel(1);
        let capture=Capture{sender,first:OnceLock::new(),dropped:AtomicU64::new(0),output_errors:Arc::new(AtomicU64::new(0)),candidate:json!({})};
        capture.emit(json!({"kind":"initial"}));
        capture.emit(json!({"kind":"healthy_to_native","at_ms":42}));
        capture.emit(json!({"kind":"healthy_to_native","at_ms":99}));
        let snapshot=capture.snapshot();
        assert_eq!(snapshot["first_observed_health_loss"]["at_ms"],42);
        assert_eq!(snapshot["dropped_records"],2);
    }
}
