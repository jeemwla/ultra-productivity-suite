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
pub struct Placement { pub x: i32, pub y: i32, pub width: i32, pub height: i32, pub shelf: bool }

/// Insufficient native space latches a shelf above the taskbar. Hiding buttons cannot move the Dock back and
/// forth between lanes. Only a monitor/taskbar geometry change starts a new placement session.
pub fn place(left: i32, right: i32, top: i32, bottom: i32, taskbar_top: i32,
             gap_x: i32, gap_right: i32, scale: f64, requested_w: u32, requested_h: u32,
             shelf: &mut bool) -> Placement {
    let scale = if scale.is_finite() && scale > 0.0 { scale } else { 1.0 };
    let pad = (8.0 * scale).round() as i32;
    let screen_w = (right - left).max(1);
    let base_h = (48.0 * scale).round() as i32;
    let width = ((requested_w as f64 * scale).ceil() as i32).clamp(1, (screen_w - 2 * pad).max(1));
    let height = ((requested_h as f64 * scale).ceil() as i32).clamp(1, (bottom - top - 2 * pad).max(1));
    if width > gap_right - gap_x || height > base_h { *shelf = true; }
    let (x, y) = if *shelf {
        (left + pad, taskbar_top - height - pad)
    } else { (gap_x, taskbar_top) };
    Placement { x: x.clamp(left, (right - width).max(left)), y: y.clamp(top, (bottom - height).max(top)), width, height, shelf: *shelf }
}

#[cfg(test)]
mod tests {
    use super::*;
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
    fn crowded_taskbar_does_not_make_dock_vanish_or_oscillate() {
        let mut shelf = false;
        let p = place(0, 1280, 0, 720, 672, 1100, 1120, 1.0, 400, 48, &mut shelf);
        assert!(p.shelf); assert_eq!(p.width, 400); assert!(p.y + p.height < 672);
        for gap in [300, 1100, 500, 1100] {
            assert_eq!(place(0, 1280, 0, 720, 672, gap, 1120, 1.0, 400, 48, &mut shelf), p);
        }
    }
    #[test]
    fn seven_apps_and_overflow_folder_remain_available_on_two_crowded_taskbars() {
        let apps: Vec<String> = (0..12).map(|n| format!("app{n}.exe")).collect();
        let plan = crate::dock_layout::plan_capped(&[("Tools".into(), apps.clone())], f64::INFINITY, Some(7));
        assert_eq!(plan.items.iter().filter(|i| !i.folder).count(), 7);
        assert_eq!(plan.items.iter().filter(|i| i.folder).count(), 1);
        assert!(plan.left_out.is_empty());
        let mut surfaces = vec![];
        for (n, width) in [1280, 1920].into_iter().enumerate() {
            let p = place(0, width, 0, 1080, 1032, width-300, width-280, 1.0, plan.width.ceil() as u32, 48, &mut false);
            assert!(p.shelf);
            assert_eq!(p.width, plan.width.ceil() as i32, "cap is not silently compacted away by the small native gap");
            let mut s = Surface::new(n as isize, 10+n as isize, format!("taskbar-{n}"));
            s.shown = true; s.rendered_at = Some(1); s.rendered.extend(100..112);
            surfaces.push(s);
        }
        assert_eq!(acknowledged(&surfaces, 2).len(), 12);
        surfaces[1].shown = false;
        assert!(acknowledged(&surfaces, 2).is_empty(), "native buttons return if either screen loses its Dock");
    }
    #[test]
    fn menu_and_strip_stay_inside_every_monitor_at_all_dpis() {
        for width in [1024,1280,1366,1440,1920,2560,3840] {
            for scale in [1.0,1.25,1.5,1.75,2.0] {
                for origin in [-1920,0,2560] {
                    for wanted in [30,400,700,1800] {
                        let p = place(origin,origin+width,-400,1040,992,origin+width-300,origin+width-280,scale,wanted,240,&mut false);
                        assert!(p.x >= origin && p.x+p.width <= origin+width);
                        assert!(p.y >= -400 && p.y+p.height <= 1040);
                    }
                }
            }
        }
    }
}
