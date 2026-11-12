//! Only one Ultra Dock (Public or Dev edition) manages the taskbar at a time.
//!
//! The lease is a session-wide named mutex. It is created, taken and released only on the organizer thread, because
//! Windows lets only the owning thread release a mutex. The Dock the user turned on most recently wins: when the other
//! edition asks for the taskbar, the holder turns itself off, puts every button back, and only then releases the lease.
//! A Dock without the lease never hides a button and keeps its strip hidden.
//! Older Docks (2.2.16 and earlier) do not know the lease; while one of them shows its strip, no new Dock takes over.
use std::sync::atomic::{AtomicBool, Ordering};

pub const LEASE_NAME: &str = "Local\\UltraSuite.DockLease";
/// Titles of the strip windows of both editions (older Docks used the first one).
pub const STRIP_TITLES: [&str; 2] = ["Ultra Dock", "Ultra Dock (Dev)"];
/// After taking over from a Dock that stopped without releasing, wait this long before hiding anything, so that
/// Dock's guard process can put its buttons back first.
pub const ABANDONED_SETTLE_MS: u64 = 3_000;

static HELD: AtomicBool = AtomicBool::new(false);
static WAITING_FOR_OTHER: AtomicBool = AtomicBool::new(false);
/// True while this Dock holds the lease (read by the strip placement and the hub).
pub fn held() -> bool { HELD.load(Ordering::Relaxed) }
/// True while this Dock is on but another Dock manages the taskbar.
pub fn waiting_for_other() -> bool { WAITING_FOR_OTHER.load(Ordering::Relaxed) }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    /// Do nothing this pass (lease elsewhere, or settling after an abandoned lease).
    Wait,
    /// Try to take the lease.
    Acquire,
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
    pub settle_left_ms: u64,
}

pub fn decide(i: Inputs) -> Step {
    if i.held {
        if !i.want { return Step::Release; }
        if i.other_requested_later || i.other_strip_visible { return Step::Yield; }
        if i.settle_left_ms > 0 { return Step::Wait; }
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
    pub struct Lease { handle: HANDLE, held: bool, acquired_ms: u128, settle_until: Option<std::time::Instant> }

    impl Lease {
        pub fn open(name: &str) -> Option<Self> {
            let wide: Vec<u16> = name.encode_utf16().chain(std::iter::once(0)).collect();
            let handle = unsafe { CreateMutexW(None, false, PCWSTR(wide.as_ptr())).ok()? };
            Some(Self { handle, held: false, acquired_ms: 0, settle_until: None })
        }
        pub fn held(&self) -> bool { self.held }
        pub fn acquired_ms(&self) -> u128 { self.acquired_ms }
        pub fn settle_left_ms(&self) -> u64 {
            self.settle_until.map(|t| t.saturating_duration_since(std::time::Instant::now()).as_millis() as u64).unwrap_or(0)
        }
        /// Never blocks. True when this thread now holds the lease.
        pub fn try_take(&mut self) -> bool {
            if self.held { return true; }
            let result = unsafe { WaitForSingleObject(self.handle, 0) };
            if result == WAIT_OBJECT_0 || result == WAIT_ABANDONED {
                self.held = true;
                self.acquired_ms = now_ms();
                self.settle_until = (result == WAIT_ABANDONED)
                    .then(|| std::time::Instant::now() + std::time::Duration::from_millis(ABANDONED_SETTLE_MS));
                if result == WAIT_ABANDONED { println!("[dock] took over the taskbar from a Dock that stopped; waiting for its buttons to come back"); }
            }
            self.held
        }
        pub fn release(&mut self) {
            if self.held {
                unsafe { let _ = ReleaseMutex(self.handle); }
                self.held = false;
                self.settle_until = None;
            }
        }
    }

    impl Drop for Lease {
        fn drop(&mut self) {
            self.release();
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
        assert_eq!(decide(Inputs { want: true, held: true, settle_left_ms: 500, ..base }), Step::Wait);
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
        first.release();
        go_tx.send(()).unwrap();
        assert_eq!(rx.recv().unwrap(), true, "after release the other Dock takes over");
        other.join().unwrap();
        // the other Dock's cleanup (Drop) releases the lease, so taking it back is a clean handover with no wait
        assert!(first.try_take());
        assert_eq!(first.settle_left_ms(), 0);
    }
}
