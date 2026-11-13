//! Per-taskbar render acknowledgements and stable placement, without Windows calls.
use std::collections::HashSet;

#[derive(Clone, Debug)]
pub struct Surface {
    pub taskbar: isize,
    pub hwnd: isize,
    pub label: String,
    pub width: u32,
    pub height: u32,
    pub rendered: HashSet<isize>,
    pub rendered_at: Option<u64>,
    pub shown: bool,
    pub shelf: bool,
    pub room: u32,
}
impl Surface {
    pub fn new(taskbar: isize, hwnd: isize, label: String) -> Self {
        Self { taskbar, hwnd, label, width: 60, height: 48, rendered: HashSet::new(), rendered_at: None, shown: false, shelf: false, room: 0 }
    }
    pub fn fresh(&self, now: u64) -> bool {
        self.rendered_at.is_some_and(|at| now.saturating_sub(at) < 5)
    }
}

/// An app may lose its global native button only after every taskbar renders it.
pub fn acknowledged(surfaces: &[Surface], now: u64) -> Vec<isize> {
    let Some(first) = surfaces.first() else { return vec![] };
    if surfaces.iter().any(|s| !s.shown || !s.fresh(now)) { return vec![]; }
    first.rendered.iter().copied().filter(|h| surfaces.iter().all(|s| s.rendered.contains(h))).collect()
}

#[derive(Default)]
pub struct SwitcherPause { until: Option<u64>, down: bool }
impl SwitcherPause {
    /// Restore on the first modifier sample and keep native buttons until the switcher's selection settles.
    pub fn update(&mut self, down: bool, now_ms: u64) -> (bool, bool) {
        let restore = down && !self.down;
        self.down = down;
        if down { self.until = Some(now_ms.saturating_add(300)); }
        (restore, self.until.is_some_and(|until| now_ms <= until))
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Placement {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    pub fits: bool,
    pub shelf: bool,
}

/// A persistent strip follows the native taskbar and yields to full-screen windows.
pub fn strip_visible(taskbar_visible: bool, fullscreen: bool, fits: bool, requested: bool) -> bool {
    taskbar_visible && !fullscreen && fits && requested
}

/// Place the persistent strip in physical taskbar coordinates. Width requests are
/// CSS pixels; page overflow handles requests larger than the safe native gap.
/// A usable strip needs 78 CSS pixels (folder 44, menu 28, slack 6). Smaller
/// requests are raised to that minimum. Unusable spans return zero width and
/// `fits = false`, so callers must retain native buttons and hide the strip.
/// Menu heights deliberately have no input here: transient popups cannot resize
/// or relocate the persistent strip. No placement ever authorizes a shelf.
pub fn place_in_taskbar(left: i32, top: i32, right: i32, bottom: i32,
                        gap_x: i32, gap_right: i32, scale: f64,
                        requested_w: u32) -> Placement {
    let scale = if scale.is_finite() && scale > 0.0 { scale } else { 1.0 };
    // Widen before subtraction, including for malformed or extreme rectangles.
    let end = right.max(left);
    let x = gap_x.clamp(left, end);
    let safe_end = gap_right.clamp(left, end).max(x);
    let span = (i64::from(safe_end) - i64::from(x)).min(i64::from(i32::MAX));
    let native_height = (i64::from(bottom) - i64::from(top)).clamp(0, i64::from(i32::MAX));
    let minimum = (78.0 * scale).ceil();
    let height = native_height.min((48.0 * scale).floor() as i64) as i32;
    let fits = span as f64 >= minimum && height > 0;
    let width = if fits {
        ((f64::from(requested_w) * scale).ceil().max(minimum).min(span as f64)) as i32
    } else { 0 };
    Placement { x, y: top, width, height, fits, shelf: false }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn hidden_taskbars_and_fullscreen_apps_never_get_an_independent_overlay() {
        for taskbar in [false, true] {
            for fullscreen in [false, true] {
                for fits in [false, true] {
                    for requested in [false, true] {
                        assert_eq!(strip_visible(taskbar, fullscreen, fits, requested),
                            taskbar && !fullscreen && fits && requested);
                    }
                }
            }
        }
        assert!(!strip_visible(false, false, true, true), "auto-hide cannot create a floating Dock");
        assert!(!strip_visible(true, true, true, true), "full-screen apps retain their screen");
    }
    #[test]
    fn all_screens_must_acknowledge_before_global_hiding() {
        let mut a = Surface::new(1, 11, "taskbar".into());
        let mut b = Surface::new(2, 22, "taskbar-2".into());
        a.shown = true; a.rendered_at = Some(10); a.rendered.extend([1, 2]);
        b.shown = true; b.rendered_at = Some(10); b.rendered.extend([2, 3]);
        assert_eq!(acknowledged(&[a.clone(), b.clone()], 11), vec![2]);
        b.rendered_at = None;
        assert!(acknowledged(&[a.clone(), b], 11).is_empty());
        assert!(acknowledged(&[a], 15).is_empty());
        assert!(acknowledged(&[], 10).is_empty());
    }
    #[test]
    fn windows_switcher_restores_once_and_blocks_rehiding_through_key_release() {
        let mut pause = SwitcherPause::default();
        assert_eq!(pause.update(false, 0), (false, false));
        assert_eq!(pause.update(true, 20), (true, true));
        assert_eq!(pause.update(true, 40), (false, true));
        assert_eq!(pause.update(false, 60), (false, true));
        assert_eq!(pause.update(false, 339), (false, true));
        assert_eq!(pause.update(false, 341), (false, false));
        assert_eq!(pause.update(true, 360), (true, true));
    }
    #[test]
    fn narrow_gaps_fail_closed_and_exact_minimum_fits_at_all_dpis() {
        for scale in [1.0, 1.25, 1.5, 1.75, 2.0] {
            for gap in [0, 14, 70] {
                let p = place_in_taskbar(0, 1000, 1920, 1100, 100, 100 + gap, scale, 400);
                assert!(!p.fits);
                assert_eq!(p.width, 0);
                assert!(!p.shelf);
                assert!(p.height >= 0 && p.height <= 100);
            }
            let minimum = (78.0_f64 * scale).ceil() as i32;
            assert!(!place_in_taskbar(0, 0, 1920, 100, 100, 100 + minimum - 1, scale, 400).fits);
            let p = place_in_taskbar(0, 0, 1920, 100, 100, 100 + minimum, scale, 0);
            assert!(p.fits);
            assert_eq!(p.width, minimum);
        }
    }

    #[test]
    fn wide_gap_clamps_width_and_has_no_shelf_latch() {
        assert!(!place_in_taskbar(0, 672, 1280, 720, 100, 114, 1.0, 400).fits);
        let p = place_in_taskbar(0, 672, 1280, 720, 100, 600, 1.0, 400);
        assert_eq!(p, Placement { x: 100, y: 672, width: 400, height: 48, fits: true, shelf: false });
        let overflow = place_in_taskbar(0, 672, 1280, 720, 100, 600, 1.0, u32::MAX);
        assert_eq!(overflow.width, 500);
        assert_eq!(overflow.height, 48);
    }

    #[test]
    fn strip_stays_inside_taskbar_and_safe_gap_on_both_monitors() {
        for origin in [-3840, -1920, 0, 2560] {
            for scale in [1.0, 1.25, 1.5, 1.75, 2.0] {
                for native_height in [0, 1, 32, 48, 72, 120] {
                    for wanted in [0, 30, 78, 400, 1800, u32::MAX] {
                        let left = origin;
                        let right = origin + 1920;
                        let top = -400;
                        let bottom = top + native_height;
                        let p = place_in_taskbar(left, top, right, bottom, left + 100, right - 200, scale, wanted);
                        assert!(p.x >= left + 100);
                        assert!(i64::from(p.x) + i64::from(p.width) <= i64::from(right - 200));
                        assert_eq!(p.y, top);
                        assert!(p.height >= 0 && p.y + p.height <= bottom);
                        assert!(f64::from(p.height) <= 48.0 * scale);
                        assert_eq!(p.fits, native_height > 0);
                        assert!(!p.shelf);
                    }
                }
            }
        }
    }

    #[test]
    fn gap_is_intersected_with_native_bounds() {
        let p = place_in_taskbar(-100, -48, 500, 0, -500, 900, 1.0, 1000);
        assert_eq!(p, Placement { x: -100, y: -48, width: 600, height: 48, fits: true, shelf: false });
        for (gap_x, gap_right) in [(600, 900), (-500, -200), (200, 100)] {
            let p = place_in_taskbar(-100, -48, 500, 0, gap_x, gap_right, 1.0, 400);
            assert!(!p.fits);
            assert_eq!(p.width, 0);
            assert!((-100..=500).contains(&p.x));
        }
    }

    #[test]
    fn invalid_geometry_and_scale_are_safe() {
        for scale in [f64::NAN, f64::INFINITY, 0.0, -1.0] {
            assert_eq!(place_in_taskbar(0, 0, 500, 48, 0, 500, scale, 100),
                       place_in_taskbar(0, 0, 500, 48, 0, 500, 1.0, 100));
        }
        for (left, top, right, bottom) in [(100, 50, 0, 0), (0, 0, 0, 0),
                                         (i32::MIN, i32::MIN, i32::MAX, i32::MAX)] {
            let p = place_in_taskbar(left, top, right, bottom, left, right, 2.0, u32::MAX);
            assert!(p.width >= 0 && p.height >= 0);
            assert!(i64::from(p.x) + i64::from(p.width) <= i64::from(right.max(left)));
            assert!(i64::from(p.y) + i64::from(p.height) <= i64::from(bottom.max(top)));
            assert!(!p.shelf);
        }
    }
}
