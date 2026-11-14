//! A native button may disappear only when its replacement is usable.
#[derive(Clone, Copy, Debug)]
pub struct Visibility {
    pub enabled: bool,
    pub guard_ready: bool,
    pub main_responsive: bool,
    pub strip_visible: bool,
    pub strip_fits: bool,
    pub all_taskbars_covered: bool,
    pub rendered_age: u64,
}
impl Visibility {
    pub fn may_hide(self) -> bool {
        self.enabled && self.guard_ready && self.main_responsive && self.strip_visible
            && self.strip_fits && self.all_taskbars_covered && self.rendered_age < 5
    }
}

/// Decides which other-app windows the strip may list, so an app never shows twice (native button plus strip copy).
/// A window is listed only while its native taskbar button is actually hidden, or briefly (`GRACE_MS`) while the
/// Dock first tries to hide it. If the button is still there after that, the window goes back to native-only and is
/// retried after a delay that doubles each time (up to `MAX_BACKOFF_MS`), so the strip cannot flicker in a loop.
/// While grouping is not active (off, an uncovered taskbar, or the Dock not healthy) nothing is listed, because every
/// native button is visible then.
#[derive(Default)]
pub struct StripListing { windows: std::collections::HashMap<isize, Attempt> }

#[derive(Default, Clone, Copy)]
struct Attempt { first_try: Option<u64>, retry_at: u64, backoff: u64, preparing_since: Option<u64> }

pub const GRACE_MS: u64 = 3_000;
pub const FIRST_BACKOFF_MS: u64 = 10_000;
pub const MAX_BACKOFF_MS: u64 = 300_000;

impl StripListing {
    /// Layout preparation must survive asynchronous sibling creation and acknowledgements.
    /// It is bounded separately from an actual native-hide attempt, avoiding health/layout feedback.
    pub fn offer(&mut self, hwnd: isize, now_ms: u64, requested: bool, attempted: bool, native_hidden: bool) -> bool {
        if !requested {
            if let Some(a) = self.windows.get_mut(&hwnd) { a.preparing_since = None; a.first_try = None; }
            return false;
        }
        if native_hidden { return self.decide(hwnd,now_ms,true,true); }
        let a = self.windows.entry(hwnd).or_default();
        if now_ms < a.retry_at { return false; }
        let since = *a.preparing_since.get_or_insert(now_ms);
        if now_ms.saturating_sub(since) >= 8_000 {
            a.backoff = if a.backoff == 0 { FIRST_BACKOFF_MS } else { (a.backoff * 2).min(MAX_BACKOFF_MS) };
            a.retry_at = now_ms.saturating_add(a.backoff); a.first_try = None; a.preparing_since = None;
            return false;
        }
        if attempted { self.decide(hwnd,now_ms,true,false) } else { true }
    }
    pub fn decide(&mut self, hwnd: isize, now_ms: u64, grouping_active: bool, native_hidden: bool) -> bool {
        if !grouping_active { return false; }
        let a = self.windows.entry(hwnd).or_default();
        if native_hidden {
            *a = Attempt::default();
            return true;
        }
        if now_ms < a.retry_at { return false; }
        match a.first_try {
            None => { a.first_try = Some(now_ms); true }
            Some(t) if now_ms.saturating_sub(t) < GRACE_MS => true,
            Some(_) => {
                a.backoff = if a.backoff == 0 { FIRST_BACKOFF_MS } else { (a.backoff * 2).min(MAX_BACKOFF_MS) };
                a.retry_at = now_ms + a.backoff;
                a.first_try = None;
                a.preparing_since = None;
                false
            }
        }
    }
    /// Forget windows that no longer exist.
    pub fn retain(&mut self, alive: &std::collections::HashSet<isize>) { self.windows.retain(|h, _| alive.contains(h)); }
}

#[cfg(test)]
mod listing_tests {
    use super::*;
    #[test]
    fn preparing_a_new_surface_does_not_destroy_its_own_candidate_layout() {
        let mut listing = StripListing::default();
        for now in (0..4_000).step_by(100) { assert!(listing.offer(1,now,true,false,false)); }
        assert!(listing.offer(1,4_000,true,true,false));
        assert!(listing.offer(1,4_700,true,true,true));
        assert!(listing.offer(1,20_000,true,true,true));
    }
    #[test]
    fn unacknowledged_preparation_is_bounded_and_retry_starts_fresh() {
        let mut listing = StripListing::default();
        assert!(listing.offer(1,0,true,false,false));
        assert!(!listing.offer(1,8_000,true,false,false));
        assert!(!listing.offer(1,17_999,true,false,false));
        assert!(listing.offer(1,18_000,true,false,false));
        assert!(listing.offer(1,18_700,true,true,true));
    }

    #[test]
    fn hidden_windows_are_listed_and_native_ones_only_briefly() {
        let mut l = StripListing::default();
        assert!(l.decide(1, 0, true, true), "a hidden native button is replaced by the strip");
        assert!(l.decide(2, 0, true, false), "a new window may be listed while the Dock tries to hide it");
        assert!(l.decide(2, GRACE_MS - 1, true, false));
        assert!(!l.decide(2, GRACE_MS, true, false), "still visible natively after the grace period: native only");
        assert!(!l.decide(2, GRACE_MS + FIRST_BACKOFF_MS - 1, true, false), "no retry before the backoff ends");
        assert!(l.decide(2, GRACE_MS + FIRST_BACKOFF_MS, true, false), "retried after the backoff");
    }

    #[test]
    fn backoff_doubles_up_to_the_cap_and_resets_after_success() {
        let mut l = StripListing::default();
        let mut now = 0;
        let mut expected = FIRST_BACKOFF_MS;
        for _ in 0..12 {
            assert!(l.decide(7, now, true, false));
            now += GRACE_MS;
            assert!(!l.decide(7, now, true, false));
            assert!(!l.decide(7, now + expected - 1, true, false));
            now += expected;
            expected = (expected * 2).min(MAX_BACKOFF_MS);
        }
        assert!(l.decide(7, now, true, true), "hiding succeeded");
        assert!(l.decide(7, now + 1, true, false), "a later failure starts a fresh grace period");
        assert!(!l.decide(7, now + 1 + GRACE_MS, true, false));
        assert!(l.decide(7, now + 1 + GRACE_MS + FIRST_BACKOFF_MS, true, false), "backoff restarted at the first step");
    }

    #[test]
    fn nothing_is_listed_while_grouping_is_inactive() {
        let mut l = StripListing::default();
        for now in [0, 1, GRACE_MS, 10 * MAX_BACKOFF_MS] {
            assert!(!l.decide(3, now, false, false));
            assert!(!l.decide(3, now, false, true));
        }
    }

    #[test]
    fn a_flap_back_to_active_does_not_relist_a_window_that_failed_to_hide() {
        let mut l = StripListing::default();
        assert!(l.decide(4, 0, true, false));
        assert!(!l.decide(4, 1_000, false, false), "health dip: native mode");
        assert!(!l.decide(4, GRACE_MS + 1, true, false), "the failed attempt is not repeated at once");
    }

    /// 50,000 random steps with up to 12 windows: windows open and close, hides succeed or fail at random, grouping
    /// switches on and off (Dock toggled, health dips, taskbar coverage lost). After every step: a window whose
    /// native button is visible is never listed for longer than the grace period in a row, nothing is listed while
    /// grouping is inactive, and a hidden window is always listed (its only route).
    #[test]
    fn randomized_soak_never_shows_an_app_twice_for_longer_than_the_grace_period() {
        let mut seed: u64 = 0x5eed_1234_abcd_0001;
        let mut rnd = move |n: u64| { seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17; seed % n };
        let mut l = StripListing::default();
        let mut alive: std::collections::HashSet<isize> = std::collections::HashSet::new();
        let mut dup_since: std::collections::HashMap<isize, u64> = std::collections::HashMap::new();
        let mut now = 0u64;
        let mut active = true;
        let (mut listed_hidden, mut listed_native, mut steps) = (0u64, 0u64, 0u64);
        for _ in 0..50_000 {
            now += 100 + rnd(1_400);                      // the organizer runs about every 0.7 s
            if rnd(50) == 0 { active = !active; }         // Dock off/on, health dip or coverage change
            if rnd(10) == 0 { alive.insert(1 + rnd(12) as isize); }
            if rnd(25) == 0 { if let Some(&h) = alive.iter().next() { alive.remove(&h); dup_since.remove(&h); } }
            for &h in alive.iter() {
                let hidden = active && rnd(4) != 0;        // a quarter of hide attempts fail
                let listed = l.decide(h, now, active, hidden);
                if !active { assert!(!listed, "nothing may be listed while grouping is inactive"); }
                if hidden { assert!(listed, "a hidden window must stay reachable from the strip"); listed_hidden += 1; }
                if listed && !hidden {
                    listed_native += 1;
                    let since = *dup_since.entry(h).or_insert(now);
                    assert!(now - since <= GRACE_MS + 1_500, "window {h} shown twice for {} ms", now - since);
                } else {
                    dup_since.remove(&h);
                }
            }
            l.retain(&alive);
            steps += 1;
        }
        assert_eq!(steps, 50_000);
        assert!(listed_hidden > 10_000 && listed_native > 1_000, "the soak exercised both paths: {listed_hidden} {listed_native}");
    }

    #[test]
    fn closed_windows_are_forgotten() {
        let mut l = StripListing::default();
        l.decide(5, 0, true, false);
        l.decide(6, 0, true, false);
        l.retain(&[6].into_iter().collect());
        assert!(l.decide(5, GRACE_MS + 1, true, false), "a reused handle starts fresh");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exhaustively_checks_visibility_and_render_age_combinations() {
        let mut cases = 0;
        for bits in 0..64 {
            for age in 0..=30 {
                let state = Visibility { enabled: bits & 1 != 0, guard_ready: bits & 2 != 0,
                    main_responsive: bits & 4 != 0, strip_visible: bits & 8 != 0,
                    strip_fits: bits & 16 != 0, all_taskbars_covered: bits & 32 != 0, rendered_age: age };
                assert_eq!(state.may_hide(), bits == 63 && age < 5, "{state:?}");
                cases += 1;
            }
        }
        assert_eq!(cases, 1984);
    }
}
