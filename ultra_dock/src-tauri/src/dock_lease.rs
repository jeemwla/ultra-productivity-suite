//! Only one Ultra Dock (Public or Dev edition) manages the taskbar at a time.
//!
//! The lease is a session-wide named mutex. It is created, taken and released only on the organizer thread, because
//! Windows lets only the owning thread release a mutex. The Dock the user turned on most recently wins: when the other
//! edition asks for the taskbar, the holder turns itself off, puts every button back, and only then releases the lease.
//! A Dock without the lease never hides a button and keeps its strip hidden.
//! Visible legacy strips block takeover, but this is not proof of safety against pre-lease Docks or their guards.
use std::sync::atomic::{AtomicBool, Ordering};

pub const LEASE_NAME: &str = "Local\\UltraSuite.DockLease";
/// Titles of the strip windows of both editions (older Docks used the first one).
pub const STRIP_TITLES: [&str; 2] = ["Ultra Dock", "Ultra Dock (Dev)"];

/// Organizer-owned state. Neither elapsed time nor a new enable request acknowledges restoration.
#[derive(Debug, Default)]
pub struct RecoveryGate { recovery_pending: bool, release_pending: bool }
impl RecoveryGate {
    fn acquired(&mut self) { self.recovery_pending = true; self.release_pending = false; }
    pub fn recovery_pending(&self) -> bool { self.recovery_pending }
    pub fn release_pending(&self) -> bool { self.release_pending }
    pub fn require_recovery(&mut self) { self.recovery_pending = true; }
    pub fn acknowledge(&mut self, result: std::io::Result<()>) {
        if result.is_ok() { self.recovery_pending = false; }
    }
    fn begin_release(&mut self) { self.release_pending = true; }
}

static HELD: AtomicBool = AtomicBool::new(false);
static WAITING_FOR_OTHER: AtomicBool = AtomicBool::new(false);
/// True while this Dock holds the lease (read by the strip placement and the hub).
pub fn held() -> bool { HELD.load(Ordering::Relaxed) }
/// True while this Dock is on but another Dock manages the taskbar.
pub fn waiting_for_other() -> bool { WAITING_FOR_OTHER.load(Ordering::Relaxed) }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    /// Do nothing this pass (lease elsewhere).
    Wait,
    /// Try to take the lease.
    Acquire,
    /// Lease held, but checked recovery has not acknowledged durable completion.
    Recover,
    /// Lease held: organize normally.
    Organize,
    /// Another Dock was turned on later (or an older Dock is showing its strip): turn this one off.
    Yield,
    /// This Dock was turned off: restore every button, then release the lease.
    Release,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct Inputs {
    pub want: bool,
    pub held: bool,
    pub other_strip_visible: bool,
    pub other_requested_later: bool,
    pub recovery_pending: bool,
    pub release_pending: bool,
}

pub fn decide(i: Inputs) -> Step {
    if i.held {
        if !i.want || i.release_pending { return Step::Release; }
        if i.other_requested_later || i.other_strip_visible { return Step::Yield; }
        if i.recovery_pending { return Step::Recover; }
        return Step::Organize;
    }
    if i.want && !i.other_strip_visible { Step::Acquire } else { Step::Wait }
}

/// "<edition> <unix ms>": which edition the user turned on last.
pub fn parse_request(text: &str) -> Option<(String, u128)> {
    let mut parts = text.split_whitespace();
    let edition = parts.next()?;
    let at = parts.next()?.parse().ok()?;
    if parts.next().is_some() || !matches!(edition, "Public" | "Dev") { return None; }
    Some((edition.to_string(), at))
}

/// Another edition asked for the taskbar after this Dock took it.
pub fn requested_by_other(request: Option<(String, u128)>, mine: &str, acquired_ms: u128) -> bool {
    matches!(request, Some((edition, at)) if edition != mine && at > acquired_ms)
}

pub fn now_ms() -> u128 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0)
}

/// Shared by both editions: %LOCALAPPDATA%\UltraSuite-Shared\dock_request.txt
pub fn request_path() -> Option<std::path::PathBuf> {
    std::env::var("LOCALAPPDATA").ok().map(|d| std::path::PathBuf::from(d).join("UltraSuite-Shared").join("dock_request.txt"))
}

/// The user turned this edition's Dock on: ask the other edition to hand the taskbar over.
pub fn request_taskbar(edition: &str) {
    if let Some(p) = request_path() {
        if let Some(d) = p.parent() { let _ = std::fs::create_dir_all(d); }
        let _ = std::fs::write(&p, format!("{edition} {}", now_ms()));
    }
}

pub fn read_request() -> Option<(String, u128)> {
    request_path().and_then(|p| std::fs::read_to_string(p).ok()).and_then(|s| parse_request(&s))
}

#[cfg(windows)]
pub use win::{other_strip_visible, Lease};

#[cfg(windows)]
mod win {
    use super::*;
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::{CloseHandle, HANDLE, HWND, WAIT_ABANDONED, WAIT_OBJECT_0};
    use windows::Win32::System::Threading::{CreateMutexW, ReleaseMutex, WaitForSingleObject};
    use windows::Win32::UI::WindowsAndMessaging::{FindWindowExW, GetWindowThreadProcessId, IsWindowVisible};

    /// Must stay on the thread that created it (the organizer thread).
    pub struct Lease {
        handle: HANDLE, held: bool, acquired_ms: u128, pub gate: RecoveryGate,
        // Windows mutex ownership belongs to the acquiring thread.
        _thread: std::marker::PhantomData<std::rc::Rc<()>>,
    }

    impl Lease {
        pub fn open(name: &str) -> Option<Self> {
            let wide: Vec<u16> = name.encode_utf16().chain(std::iter::once(0)).collect();
            let handle = unsafe { CreateMutexW(None, false, PCWSTR(wide.as_ptr())).ok()? };
            Some(Self { handle, held: false, acquired_ms: 0, gate: RecoveryGate::default(), _thread: std::marker::PhantomData })
        }
        pub fn held(&self) -> bool { self.held }
        pub fn acquired_ms(&self) -> u128 { self.acquired_ms }
        /// Never blocks. True when this thread now holds the lease.
        pub fn try_take(&mut self) -> bool {
            if self.held { return true; }
            let result = unsafe { WaitForSingleObject(self.handle, 0) };
            if result == WAIT_OBJECT_0 || result == WAIT_ABANDONED {
                self.held = true;
                self.acquired_ms = now_ms();
                // Also gate clean acquisitions: the old mutex may have disappeared after a crash.
                self.gate.acquired();
            }
            self.held
        }
        pub fn release_when_restored(&mut self, restore: impl FnOnce() -> std::io::Result<()>) {
            self.gate.begin_release();
            if self.held && restore().is_ok() && unsafe { ReleaseMutex(self.handle).is_ok() } {
                self.held = false;
                self.gate = RecoveryGate::default();
            }
        }
    }

    impl Drop for Lease {
        fn drop(&mut self) {
            // Do not advertise a clean handoff on an unchecked unwind. Every new holder must recover.
            unsafe { let _ = CloseHandle(self.handle); }
        }
    }

    /// A visible Dock strip owned by another process (an older Dock, or the other edition).
    pub fn other_strip_visible() -> bool {
        let me = std::process::id();
        for title in STRIP_TITLES {
            let wide: Vec<u16> = title.encode_utf16().chain(std::iter::once(0)).collect();
            let mut after: Option<HWND> = None;
            for _ in 0..32 {
                let Ok(h) = (unsafe { FindWindowExW(None, after, PCWSTR::null(), PCWSTR(wide.as_ptr())) }) else { break };
                if h.is_invalid() { break; }
                let mut pid = 0u32;
                unsafe { GetWindowThreadProcessId(h, Some(&mut pid)); }
                if pid != me && unsafe { IsWindowVisible(h) }.as_bool() { return true; }
                after = Some(h);
            }
        }
        false
    }
}

/// Organizer-thread bookkeeping: publish state for the other threads.
pub fn publish(held_now: bool, want: bool) {
    HELD.store(held_now, Ordering::Relaxed);
    WAITING_FOR_OTHER.store(want && !held_now, Ordering::Relaxed);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decisions_cover_every_state() {
        let base = Inputs::default();
        assert_eq!(decide(base), Step::Wait, "off and not held");
        assert_eq!(decide(Inputs { want: true, ..base }), Step::Acquire);
        assert_eq!(decide(Inputs { want: true, other_strip_visible: true, ..base }), Step::Wait, "an older Dock shows its strip");
        assert_eq!(decide(Inputs { want: true, held: true, ..base }), Step::Organize);
        assert_eq!(decide(Inputs { want: true, held: true, recovery_pending: true, ..base }), Step::Recover);
        assert_eq!(decide(Inputs { want: false, held: true, ..base }), Step::Release);
        assert_eq!(decide(Inputs { want: true, held: true, other_requested_later: true, ..base }), Step::Yield);
        assert_eq!(decide(Inputs { want: true, held: true, other_strip_visible: true, ..base }), Step::Yield);
        // turning off always wins, even while the other edition asks
        assert_eq!(decide(Inputs { want: false, held: true, other_requested_later: true, ..base }), Step::Release);
    }

    #[test]
    fn only_a_later_request_from_the_other_edition_counts() {
        assert_eq!(parse_request("Dev 1700000000000"), Some(("Dev".into(), 1_700_000_000_000)));
        for bad in ["", "Dev", "Dev x", "Other 1", "Dev 1 2"] { assert_eq!(parse_request(bad), None, "{bad}"); }
        let req = |e: &str, t| Some((e.to_string(), t));
        assert!(requested_by_other(req("Dev", 20), "Public", 10));
        assert!(!requested_by_other(req("Dev", 5), "Public", 10), "older than our takeover");
        assert!(!requested_by_other(req("Public", 20), "Public", 10), "our own request");
        assert!(!requested_by_other(None, "Public", 10));
    }

    #[test]
    fn recovery_acknowledgement_is_required_on_every_acquisition() {
        let mut gate = RecoveryGate::default();
        gate.acquired();
        let input = |gate: &RecoveryGate| Inputs { want: true, held: true,
            recovery_pending: gate.recovery_pending(), release_pending: gate.release_pending(), ..Inputs::default() };
        for _ in 0..100 {
            gate.acknowledge(Err(std::io::Error::other("restoration or state/transaction lock failed")));
            assert_eq!(decide(input(&gate)), Step::Recover);
        }
        gate.acknowledge(Ok(()));
        assert_eq!(decide(input(&gate)), Step::Organize);
        gate.acquired();
        assert_eq!(decide(input(&gate)), Step::Recover, "no timeout can acknowledge a new holder");
    }

    #[test]
    fn pending_release_cannot_be_cancelled_by_enabling_again_or_recovery_ack() {
        let mut gate = RecoveryGate::default();
        gate.acquired();
        gate.begin_release();
        gate.acknowledge(Ok(()));
        assert_eq!(decide(Inputs { want: true, held: true, release_pending: gate.release_pending(), ..Inputs::default() }), Step::Release);
    }

    #[cfg(windows)]
    #[test]
    fn failed_restoration_keeps_mutex_until_a_successful_retry() {
        let name = format!("Local\\UltraSuite.DockLease.RetryTest.{}", std::process::id());
        let mut lease = Lease::open(&name).unwrap();
        assert!(lease.try_take());
        for failure in ["AddTab", "persistence", "state lock", "transaction lock"] {
            lease.release_when_restored(|| Err(std::io::Error::other(failure)));
            assert!(lease.held());
            assert!(lease.gate.release_pending());
            let other_name = name.clone();
            assert!(!std::thread::spawn(move || Lease::open(&other_name).unwrap().try_take()).join().unwrap());
        }
        lease.release_when_restored(|| Ok(()));
        assert!(!lease.held());
        assert!(std::thread::spawn(move || Lease::open(&name).unwrap().try_take()).join().unwrap());
    }

    #[cfg(windows)]
    #[test]
    fn the_lease_allows_one_holder_and_hands_over_after_release() {
        use std::sync::mpsc;
        let name = format!("Local\\UltraSuite.DockLease.Test.{}", std::process::id());
        let mut first = Lease::open(&name).unwrap();
        assert!(first.try_take());
        let (tx, rx) = mpsc::channel();
        let (go_tx, go_rx) = mpsc::channel::<()>();
        let n2 = name.clone();
        let other = std::thread::spawn(move || {
            let mut second = Lease::open(&n2).unwrap();
            tx.send(second.try_take()).unwrap();
            go_rx.recv().unwrap();
            tx.send(second.try_take()).unwrap();
        });
        assert_eq!(rx.recv().unwrap(), false, "a second Dock must not organize while the first holds the lease");
        first.release_when_restored(|| Ok(()));
        go_tx.send(()).unwrap();
        assert_eq!(rx.recv().unwrap(), true, "after release the other Dock takes over");
        other.join().unwrap();
        // Thread exit abandons an unchecked holder. Reacquisition always needs checked recovery.
        assert!(first.try_take());
        assert!(first.gate.recovery_pending());
    }
}
