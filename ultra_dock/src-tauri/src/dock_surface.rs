//! Per-taskbar render acknowledgements and stable placement, without Windows calls.
use std::collections::HashSet;
#[derive(Default)]
pub struct ShellRefresh { active: bool, until: u64 }
impl ShellRefresh {
    pub fn update(&mut self, shell: bool, now_ms: u64) -> bool {
        if shell && !self.active { self.until = now_ms.saturating_add(600); }
        self.active = shell;
        shell && now_ms <= self.until
    }
}
/// Only Windows' own Start/search/notification hosts qualify, never arbitrary full-screen apps.
pub fn shell_surface_path(path: &str, windows: &str) -> bool {
    let path = path.replace('/', "\\").to_ascii_lowercase();
    let root = windows.replace('/', "\\").trim_end_matches('\\').to_ascii_lowercase();
    path.starts_with(&(root + "\\systemapps\\")) && matches!(path.rsplit('\\').next(),
        Some("startmenuexperiencehost.exe" | "searchhost.exe" | "shellexperiencehost.exe"))
}
pub fn task_view_surface(path: &str, windows: &str, class: &str) -> bool {
    let path = path.replace('/', "\\").to_ascii_lowercase();
    let root = windows.replace('/', "\\").trim_end_matches('\\').to_ascii_lowercase();
    path == root + "\\explorer.exe"
        && matches!(class, "MultitaskingViewFrame" | "XamlExplorerHostIslandWindow")
}
pub fn switcher_requested(event_pending: bool, visible: bool, alt_down: bool, _win_down: bool) -> bool {
    // Alt preflight precedes Windows building its switcher; Start/Win alone must not restore.
    event_pending || visible || alt_down
}

/// Physical-pixel popup bounds on the button's monitor, including negative monitor origins.
pub fn menu_rect(anchor: (i32,i32), work: (i32,i32,i32,i32), wanted: (i32,i32), gap: i32) -> (i32,i32,i32,i32) {
    let (left,top,right,bottom) = (i64::from(work.0),i64::from(work.1),i64::from(work.2),i64::from(work.3));
    let w = i64::from(wanted.0).max(1).min((right-left).max(1));
    let h = i64::from(wanted.1).max(1).min((bottom-top).max(1));
    let x = (i64::from(anchor.0)-w).clamp(left,(right-w).max(left));
    let y = (i64::from(anchor.1)-h-i64::from(gap.max(0))).clamp(top,(bottom-h).max(top));
    (x as i32,y as i32,w as i32,h as i32)
}

/// Stabilizes recovery, while visibility failures always restore native buttons immediately.
#[derive(Default)]
pub struct HidingStability { ready_since: Option<u64> }
impl HidingStability {
    pub const RECOVERY_MS: u64 = 1500;
    pub fn allow(&mut self, ready: bool, now_ms: u64) -> bool {
        if !ready { self.ready_since = None; return false; }
        let since = self.ready_since.get_or_insert(now_ms);
        now_ms.saturating_sub(*since) >= Self::RECOVERY_MS
    }
    pub fn active(&self, now_ms: u64) -> bool {
        self.ready_since.is_some_and(|since| now_ms.saturating_sub(since) >= Self::RECOVERY_MS)
    }
}

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
    pub geometry: Option<crate::dock_layout::TaskbarGeom>,
    pub expected_generation: u64,
    pub rendered_generation: u64,
    pub required: bool,
}
impl Surface {
    pub fn new(taskbar: isize, hwnd: isize, label: String) -> Self {
        Self { required: !label.ends_with("-left"), expected_generation: 0, rendered_generation: 0, taskbar, hwnd, label, width: 60, height: 48, rendered: HashSet::new(), rendered_at: None, shown: false, shelf: false, room: 0, geometry: None }
    }
    pub fn fresh(&self, now: u64) -> bool {
        self.rendered_at.is_some_and(|at| now.saturating_sub(at) < 5)
    }
}

/// Verify the complete native-taskbar topology before global acknowledgement.
/// `expected` entries are (taskbar HWND, expected generation, paired mode).
/// This checks expected_generation only; caller checks native rectangles,
/// visibility, freshness and rendered_generation separately.
/// Optional LEFT surfaces are ignored in fallback while the tracker retires them.
pub fn topology_matches(surfaces: &[Surface], expected: &[(isize, u64, bool)]) -> bool {
    if expected.is_empty() { return false; }
    let mut taskbars = HashSet::new();
    if expected.iter().any(|&(tb, _, _)| !taskbars.insert(tb))
        || surfaces.iter().any(|s| !taskbars.contains(&s.taskbar)) { return false; }
    expected.iter().all(|&(tb, generation, paired)| {
        if !paired && generation != 0 { return false; }
        let required: Vec<_> = surfaces.iter().filter(|s| s.taskbar == tb && s.required).collect();
        let left = required.iter().filter(|s| s.label.ends_with("-left")).count();
        let right = required.len() - left;
        right == 1 && left == usize::from(paired)
            && required.iter().all(|s| s.expected_generation == generation)
    })
}

/// Union the required siblings within each physical taskbar, then intersect
/// across taskbars. Caller must separately verify expected labels exist.
/// A known taskbar with no required surface, any stale required sibling, or a
/// mixed/mismatched generation fails closed. Optional surfaces contribute nothing.
pub fn acknowledged(surfaces: &[Surface], now: u64) -> Vec<isize> {
    let mut groups = std::collections::BTreeMap::<isize, Vec<&Surface>>::new();
    for s in surfaces { groups.entry(s.taskbar).or_default().push(s); }
    let mut intersection: Option<HashSet<isize>> = None;
    for siblings in groups.values() {
        let required: Vec<_> = siblings.iter().copied().filter(|s|s.required).collect();
        let Some(first) = required.first() else {return vec![]};
        let generation = first.expected_generation;
        if required.iter().any(|s| !s.shown || !s.fresh(now)
            || s.rendered_at.is_some_and(|at|at > now)
            || s.expected_generation != generation || s.rendered_generation != generation) { return vec![]; }
        let union: HashSet<_> = required.iter().flat_map(|s|s.rendered.iter().copied()).collect();
        match &mut intersection {None=>intersection=Some(union),Some(result)=>result.retain(|h|union.contains(h))}
    }
    let mut result:Vec<_> = intersection.unwrap_or_default().into_iter().collect();
    result.sort_unstable();result
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
#[cfg(windows)]
impl Placement {
    pub fn matches_native_rect(&self, (left,top,right,bottom):(i32,i32,i32,i32))->bool {
        left==self.x && top==self.y && i64::from(right)-i64::from(left)==i64::from(self.width)
            && i64::from(bottom)-i64::from(top)==i64::from(self.height)
    }
}

/// A persistent strip follows the native taskbar and yields to full-screen windows.
pub fn strip_visible(taskbar_visible: bool, fullscreen: bool, fits: bool, requested: bool) -> bool {
    taskbar_visible && !fullscreen && fits && requested
}

/// Place the persistent strip in physical taskbar coordinates. Width requests are
/// CSS pixels; page overflow handles requests larger than the safe native gap.
/// A usable menu-only strip needs 44 CSS pixels (a native-sized 40px button plus padding). Smaller
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
    let minimum = (44.0 * scale).ceil();
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
    #[cfg(windows)]
    #[test]
    fn dpi_suggested_resize_must_not_count_as_the_requested_physical_placement() {
        let wanted=Placement{x:-1016,y:2054,width:210,height:60,fits:true,shelf:false};
        assert!(!wanted.matches_native_rect((-1016,2054,-753,2129)),"125-percent suggested resize extends outside the measured 60px taskbar");
        assert!(wanted.matches_native_rect((-1016,2054,-806,2114)));
        assert!(!wanted.matches_native_rect((-1015,2054,-805,2114)),"moved native controls also require reflow");
    }
    #[test]
    fn shell_z_order_refresh_is_bounded_and_restarts_on_next_start_open() {
        let mut refresh = ShellRefresh::default();
        assert!(!refresh.update(false,0));
        assert!(refresh.update(true,10));
        assert!(refresh.update(true,610));
        assert!(!refresh.update(true,611));
        assert!(!refresh.update(false,700));
        assert!(refresh.update(true,800));
        assert!(!refresh.update(true,1401));
    }
    #[test]
    fn start_is_a_shell_surface_but_fullscreen_applications_are_not() {
        assert!(shell_surface_path("C:\\Windows\\SystemApps\\StartPackage\\StartMenuExperienceHost.exe", "C:\\Windows"));
        assert!(shell_surface_path("C:\\WINDOWS\\SystemApps\\CBS\\SearchHost.exe", "C:\\Windows"));
        assert!(!shell_surface_path("C:\\Games\\game.exe", "C:\\Windows"));
        assert!(!shell_surface_path("C:\\Temp\\StartMenuExperienceHost.exe", "C:\\Windows"));
        assert!(!shell_surface_path("C:\\Windows\\SystemAppsFake\\StartMenuExperienceHost.exe", "C:\\Windows"));
    }
    #[test]
    fn task_view_is_distinct_from_fullscreen_file_explorer_and_other_apps() {
        for class in ["MultitaskingViewFrame","XamlExplorerHostIslandWindow"] {
            assert!(task_view_surface("C:\\Windows\\explorer.exe","C:\\Windows",class));
            assert!(!task_view_surface("C:\\Temp\\explorer.exe","C:\\Windows",class));
            assert!(!task_view_surface("C:\\Games\\game.exe","C:\\Windows",class));
        }
        assert!(!task_view_surface("C:\\Windows\\explorer.exe","C:\\Windows","CabinetWClass"));
    }
    #[test]
    fn windows_key_does_not_restore_but_alt_preflights_before_switcher_activation() {
        assert!(!switcher_requested(false,false,false,true));
        assert!(switcher_requested(false,false,true,false));
        assert!(switcher_requested(true,false,false,false));
        assert!(switcher_requested(false,true,false,false));
        let mut pause = SwitcherPause::default();
        for now in (0..200_000).step_by(20) {
            let modifier = now % 500 < 200;
            assert_eq!(pause.update(switcher_requested(false,false,false,modifier),now),(false,false));
        }
    }
    #[test]
    fn transient_menu_is_bounded_on_each_monitors_work_area() {
        for left in [-3840,-1920,0,2560] {
            for scale in [1.0,1.25,1.5,2.0] {
                for (width,height) in [(1920,1032),(800,552),(280,240)] {
                    let (x,y,w,h) = menu_rect((left+width-4,height),(left,0,left+width,height),
                        ((320.0*scale) as i32,(440.0*scale) as i32),8);
                    assert!(x>=left && y>=0 && x+w<=left+width && y+h<=height);
                    assert!(w>0 && h>0);
                }
            }
        }
    }
    #[test]
    fn transient_health_and_overlay_changes_cannot_repeatedly_rehide_native_buttons() {
        let mut gate = HidingStability::default();
        let mut last = false;
        let mut transitions = 0;
        for now in (0..5000).step_by(100) {
            let ready = now % 400 >= 200;
            let hide = gate.allow(ready, now);
            if hide != last { transitions += 1; }
            assert!(!hide || ready, "unavailable replacement must restore immediately");
            last = hide;
        }
        assert_eq!(transitions, 0, "short recovery intervals cannot trigger restore/hide storms");
    }
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
            for gap in [0, 14, 43] {
                let p = place_in_taskbar(0, 1000, 1920, 1100, 100, 100 + gap, scale, 400);
                assert!(!p.fits);
                assert_eq!(p.width, 0);
                assert!(!p.shelf);
                assert!(p.height >= 0 && p.height <= 100);
            }
            let minimum = (44.0_f64 * scale).ceil() as i32;
            assert!(!place_in_taskbar(0, 0, 1920, 100, 100, 100 + minimum - 1, scale, 400).fits);
            let p = place_in_taskbar(0, 0, 1920, 100, 100, 100 + minimum, scale, 0);
            assert!(p.fits);
            assert_eq!(p.width, minimum);
        }
    }

    #[test]
    fn restored_buttons_leave_a_usable_menu_in_a_sixty_pixel_gap() {
        let p = place_in_taskbar(0, 0, 1920, 48, 100, 160, 1.0, 400);
        assert!(p.fits);
        assert_eq!(p.width, 60);
        // Placement alone never acknowledges replacement app buttons.
        assert!(acknowledged(&[Surface::new(1, 11, "taskbar".into())], 0).is_empty());
    }
    #[test]
    fn a_complete_switcher_cycle_between_slow_passes_restarts_recovery() {
        let mut gate = HidingStability::default();
        assert!(!gate.allow(true, 0));
        assert!(gate.allow(true, 2100));
        // The fast loop sees the entire pause before the next 700ms pass.
        assert!(!gate.allow(false, 2120));
        assert!(!gate.active(2500));
        assert!(!gate.allow(true, 2800));
        assert!(!gate.active(3500));
        assert!(!gate.allow(true, 4200));
        assert!(gate.allow(true, 4900));
    }
    #[test]
    fn recovery_policy_survives_a_million_simulated_shell_samples() {
        let mut gate = HidingStability::default();
        let mut seed = 0x1357_2468_u64;
        let mut stable_since = None;
        for sample in 0..1_000_000_u64 {
            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
            let ready = seed >> 60 != 0;
            let now = sample * 20;
            if !ready { stable_since = None; }
            let expected = ready && now.saturating_sub(*stable_since.get_or_insert(now)) >= 1500;
            assert_eq!(gate.allow(ready, now), expected);
        }
    }
    #[test]
    fn stable_recovery_waits_then_restores_immediately_on_failure() {
        let mut gate = HidingStability::default();
        assert!(!gate.allow(true, 100));
        assert!(!gate.allow(true, 1599));
        assert!(gate.allow(true, 1600));
        assert!(!gate.allow(false, 1601));
        assert!(!gate.active(5000));
        assert!(!gate.allow(true, 5000));
        assert!(gate.allow(true, 6500));
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

#[cfg(test)]
mod paired_ack_tests {
    use super::*;
    fn surface(tb:isize,label:&str,ids:&[isize])->Surface{
        let mut s=Surface::new(tb,tb*10,label.into());s.required=true;s.shown=true;s.rendered_at=Some(10);
        s.expected_generation=7;s.rendered_generation=7;s.rendered.extend(ids);s
    }
    #[test] fn sibling_union_then_monitor_intersection(){
        let a=surface(1,"taskbar-left",&[1,2]);let b=surface(1,"taskbar",&[3]);
        let c=surface(2,"taskbar-2-left",&[1,3]);let d=surface(2,"taskbar-2",&[4]);
        assert_eq!(acknowledged(&[a,b,c,d],11),vec![1,3]);
    }
    #[test] fn missing_generation_and_stale_partial_side_fail_closed(){
        for mutation in 0..5{
            let a=surface(1,"taskbar-left",&[1]);let mut b=surface(1,"taskbar",&[2]);
            match mutation{0=>b.rendered_generation=0,1=>b.expected_generation=8,2=>b.rendered_at=None,3=>b.shown=false,_=>b.rendered_at=Some(12)};
            assert!(acknowledged(&[a,b],11).is_empty());
        }
    }
    #[test] fn optional_left_is_ignored_and_empty_required_group_rejects(){
        let optional=Surface::new(1,11,"taskbar-left".into());assert!(!optional.required);
        assert!(acknowledged(&[optional.clone()],11).is_empty());
        let right=surface(1,"taskbar",&[5]);assert_eq!(acknowledged(&[optional.clone(),right.clone()],11),vec![5]);
        let mut other=optional;other.taskbar=2;assert!(acknowledged(&[right,other],11).is_empty());
    }
    #[test] fn legacy_zero_generation_and_required_defaults_work(){
        let mut s=Surface::new(1,11,"taskbar".into());assert!(s.required);assert_eq!(s.expected_generation,0);assert_eq!(s.rendered_generation,0);
        s.shown=true;s.rendered_at=Some(10);s.rendered.insert(9);assert_eq!(acknowledged(&[s],11),vec![9]);
    }
}

#[cfg(test)]
mod topology_tests {
    use super::*;
    fn s(tb:isize,left:bool,generation:u64)->Surface {
        let mut s=Surface::new(tb,tb*10,if left{format!("taskbar-{tb}-left")}else{format!("taskbar-{tb}")});
        s.required=true;s.expected_generation=generation;s
    }
    #[test] fn paired_requires_exactly_one_required_sibling_of_each_side(){
        let right=s(1,false,7);let left=s(1,true,7);
        assert!(!topology_matches(&[right.clone()],&[(1,7,true)]));
        assert!(!topology_matches(&[left.clone()],&[(1,7,true)]));
        assert!(topology_matches(&[left.clone(),right.clone()],&[(1,7,true)]));
        assert!(!topology_matches(&[left,right.clone(),right],&[(1,7,true)]));
    }
    #[test] fn missing_native_taskbar_and_unpruned_removed_taskbar_fail(){
        let right=s(1,false,0);
        assert!(!topology_matches(&[right.clone()],&[(1,0,false),(2,0,false)]));
        assert!(!topology_matches(&[right,s(2,false,0)],&[(1,0,false)]));
        assert!(!topology_matches(&[],&[]));
        assert!(!topology_matches(&[],&[(1,0,false)]));
    }
    #[test] fn generations_must_match_expected_on_both_siblings(){
        assert!(!topology_matches(&[s(1,true,7),s(1,false,8)],&[(1,7,true)]));
        assert!(!topology_matches(&[s(1,true,8),s(1,false,8)],&[(1,7,true)]));
        assert!(!topology_matches(&[s(1,false,7)],&[(1,7,false)]));
        assert!(!topology_matches(&[s(1,false,7)],&[(1,0,false)]));
    }
    #[test] fn fallback_ignores_optional_left_but_rejects_required_left(){
        let right=s(1,false,0);let mut left=s(1,true,99);left.required=false;
        assert!(topology_matches(&[right.clone(),left.clone()],&[(1,0,false)]));
        left.required=true;assert!(!topology_matches(&[right,left],&[(1,0,false)]));
    }
    #[test] fn duplicate_expectations_and_optional_replacement_sibling_fail(){
        assert!(!topology_matches(&[s(1,false,0)],&[(1,0,false),(1,0,false)]));
        let mut left=s(1,true,7);left.required=false;
        assert!(!topology_matches(&[left,s(1,false,7)],&[(1,7,true)]));
        assert!(topology_matches(&[s(1,true,7),s(1,false,7),s(2,false,0)],&[(1,7,true),(2,0,false)]));
    }
}
