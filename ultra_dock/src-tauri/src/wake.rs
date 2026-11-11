//! Schedule an absolute UTC wake deadline. Network failures are not cancellations.
use serde_json::Value;

const SECOND: i64 = 10_000_000;
#[derive(Debug, PartialEq)]
pub enum Observation { Unavailable, NoAlarm, Alarm(i64) }
#[derive(Debug, PartialEq)]
pub enum Action { Keep, Cancel, Arm(i64) }

pub fn decide(observed: Observation, now: i64, armed: Option<i64>) -> Action {
    match observed {
        Observation::Unavailable => Action::Keep,
        Observation::NoAlarm => if armed.is_some() { Action::Cancel } else { Action::Keep },
        Observation::Alarm(alarm) if alarm > now => {
            let target = (alarm - 60 * SECOND).max(now.saturating_add(SECOND)).min(alarm);
            if armed == Some(target) { Action::Keep } else { Action::Arm(target) }
        }
        Observation::Alarm(_) => Action::Keep,
    }
}

#[cfg(windows)]
pub fn observe(body: Option<&str>) -> Observation {
    let Some(value) = body.and_then(|body| serde_json::from_str::<Value>(body).ok()) else { return Observation::Unavailable; };
    if value.get("in_sec").is_some_and(Value::is_null) { return Observation::NoAlarm; }
    match value.get("at").and_then(Value::as_str).and_then(local_filetime) {
        Some(at) => Observation::Alarm(at),
        None => Observation::Unavailable,
    }
}

#[cfg(windows)]
fn local_filetime(text: &str) -> Option<i64> {
    use windows::Win32::Foundation::{FILETIME, SYSTEMTIME};
    use windows::Win32::System::Time::{SystemTimeToFileTime, TzSpecificLocalTimeToSystemTimeEx};
    if text.len() != 19 { return None; }
    let b = text.as_bytes();
    if b[4] != b'-' || b[7] != b'-' || b[10] != b'T' || b[13] != b':' || b[16] != b':' { return None; }
    let part = |start: usize, end: usize| -> Option<u16> { text.get(start..end)?.parse().ok() };
    let local = SYSTEMTIME { wYear: part(0,4)?, wMonth: part(5,7)?, wDay: part(8,10)?,
        wHour: part(11,13)?, wMinute: part(14,16)?, wSecond: part(17,19)?, ..Default::default() };
    let (mut utc, mut filetime, mut validation) = (SYSTEMTIME::default(), FILETIME::default(), FILETIME::default());
    unsafe {
        // Validate the calendar fields before asking Windows for the active zone's
        // dynamic rules. This does not depend on the launcher's C-runtime TZ.
        SystemTimeToFileTime(&local, &mut validation).ok()?;
        TzSpecificLocalTimeToSystemTimeEx(None, &local, &mut utc).ok()?;
        SystemTimeToFileTime(&utc, &mut filetime).ok()?;
    }
    i64::try_from(((filetime.dwHighDateTime as u64) << 32) | filetime.dwLowDateTime as u64).ok()
}

pub fn now_filetime() -> i64 {
    let since_epoch = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    ((since_epoch.as_secs() as i64) + 11_644_473_600) * SECOND + since_epoch.subsec_nanos() as i64 / 100
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn short_horizons_never_cancel_a_real_alarm() {
        let now = 1_000_000 * SECOND;
        for seconds in 1..=180 {
            let action = decide(Observation::Alarm(now + seconds * SECOND), now, None);
            match action { Action::Arm(at) => assert!(at > now && at <= now + seconds * SECOND), _ => panic!("alarm in {seconds}s was not armed") }
        }
    }
    #[test]
    fn stable_future_deadline_is_not_rearmed_and_transport_failure_preserves_it() {
        let now = 1_000_000 * SECOND;
        let alarm = now + 300 * SECOND;
        let wake = alarm - 60 * SECOND;
        for elapsed in 0..240 {
            assert_eq!(decide(Observation::Alarm(alarm), now + elapsed * SECOND, Some(wake)), Action::Keep);
        }
        assert_eq!(decide(Observation::Unavailable, now, Some(wake)), Action::Keep);
        assert_eq!(decide(Observation::NoAlarm, now, Some(wake)), Action::Cancel);
        assert_eq!(decide(Observation::NoAlarm, now, None), Action::Keep);
    }
    #[test]
    fn edited_alarm_replaces_deadline_in_both_directions() {
        let now = 1_000_000 * SECOND;
        for seconds in [120, 600, 86400, 86400 * 400] {
            assert_eq!(decide(Observation::Alarm(now + seconds * SECOND), now, Some(now + 300 * SECOND)), Action::Arm(now + (seconds - 60) * SECOND));
        }
    }
    #[cfg(windows)]
    #[test]
    fn parser_distinguishes_empty_schedule_and_unavailable_data() {
        assert_eq!(observe(None), Observation::Unavailable);
        for input in ["", "{}", "bad", "{\"in_sec\":123}", "{\"at\":\"2027-02-29T07:15:00\"}"] {
            assert_eq!(observe(Some(input)), Observation::Unavailable);
        }
        assert_eq!(observe(Some("{\"in_sec\":null}")), Observation::NoAlarm);
        assert!(matches!(observe(Some("{\"in_sec\":1000,\"at\":\"2028-02-29T07:15:00\"}")), Observation::Alarm(_)));
    }
}
