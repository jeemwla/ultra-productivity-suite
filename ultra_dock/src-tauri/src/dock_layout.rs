//! Pure layout math for the Dock strip, kept free of Windows calls so it can be tested at any screen size.
//! - `free_span`: where the strip may sit on a taskbar: after the last native button, before the notification
//!   area (or the Widgets button when the taskbar is left-aligned), in physical pixels.
//! - `plan`: how open apps are grouped so the strip fits that space: folders of at most 6 apps (a bigger
//!   category splits into several folders of the same category), never a one-app folder, and when space is
//!   short the lowest-priority items are merged into "More apps" folders, sooner the less room there is.
//! - `FitGate`: whether the strip counts as fitting, with a short grace while native buttons are being hidden.
//! - `RehideBurst`: Windows puts a hidden taskbar button back when its window is activated, so after every
//!   foreground change the new foreground window is hidden again for a short burst.

/// Collapsed widths in CSS pixels, matching ultra_dock/ui/index.html (.win, .grp, .more and strip padding).
pub const APP_W: f64 = 40.0;
pub const FOLDER_W: f64 = 44.0;
pub const MENU_W: f64 = 28.0;
pub const SLACK_W: f64 = 6.0;
pub const MAX_PER_FOLDER: usize = 6;
pub const MORE_LABEL: &str = "More apps";

#[derive(Clone, Debug, PartialEq)]
pub struct Item {
    /// Stable id the UI uses to remember which folder is open ("Browsers", "Browsers#2", "__more", ...).
    pub key: String,
    pub label: String,
    pub folder: bool,
    pub exes: Vec<String>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Plan {
    pub items: Vec<Item>,
    /// Collapsed width of the items plus the menu, in CSS pixels.
    pub width: f64,
    /// Some items were merged into "More apps" folders to make room.
    pub compacted: bool,
    /// Last resort: so little room that a folder had to hold more than 6 apps.
    pub over_six: bool,
    /// Apps that could not be placed at all (no room even for one folder); they keep their native buttons.
    pub left_out: Vec<String>,
}

/// Balanced folder sizes for `n` apps with at most `max` per folder. Never produces a 1 unless n == 1.
pub fn chunk_sizes(n: usize, max: usize) -> Vec<usize> {
    if n == 0 { return vec![]; }
    let max = max.max(2);
    let k = n.div_ceil(max);
    let (base, extra) = (n / k, n % k);
    (0..k).map(|i| base + usize::from(i < extra)).collect()
}

fn items_for(key: &str, label: &str, exes: &[String], max: usize) -> Vec<Item> {
    if exes.len() == 1 {
        return vec![Item { key: key.to_string(), label: label.to_string(), folder: false, exes: exes.to_vec() }];
    }
    let mut out = vec![];
    let mut at = 0;
    for (i, size) in chunk_sizes(exes.len(), max).into_iter().enumerate() {
        let (k, l) = if i == 0 { (key.to_string(), label.to_string()) } else { (format!("{key}#{}", i + 1), format!("{label} {}", i + 1)) };
        out.push(Item { key: k, label: l, folder: true, exes: exes[at..at + size].to_vec() });
        at += size;
    }
    out
}

fn width_of(items: &[Item]) -> f64 {
    items.iter().map(|i| if i.folder { FOLDER_W } else { APP_W }).sum::<f64>() + MENU_W + SLACK_W
}

/// `groups`: categories in display order, each with its distinct app exes in order. `avail`: CSS pixels the
/// strip may use (taskbar free space); infinite or NaN means "not measured yet".
pub fn plan(groups: &[(String, Vec<String>)], avail: f64) -> Plan {
    let avail = if avail.is_finite() { avail.max(0.0) } else { f64::MAX };
    let mut base: Vec<(String, Item)> = vec![];
    for (cat, exes) in groups {
        if exes.is_empty() { continue; }
        for it in items_for(cat, cat, exes, MAX_PER_FOLDER) { base.push((cat.clone(), it)); }
    }
    let all: Vec<String> = base.iter().flat_map(|(_, i)| i.exes.iter().cloned()).collect();
    // Merge the last k items into "More apps" folders, smallest k first, so higher-priority categories keep
    // their own folders as long as possible.
    for k in 0..=base.len() {
        let head = &base[..base.len() - k];
        let tail = &base[base.len() - k..];
        let mut items: Vec<Item> = head.iter().map(|(_, i)| i.clone()).collect();
        if !tail.is_empty() {
            let exes: Vec<String> = tail.iter().flat_map(|(_, i)| i.exes.iter().cloned()).collect();
            let one_cat = tail.iter().all(|(c, _)| *c == tail[0].0);
            let (key, label) = if one_cat { (tail[0].1.key.clone(), tail[0].0.clone()) } else { ("__more".to_string(), MORE_LABEL.to_string()) };
            items.extend(items_for(&key, &label, &exes, MAX_PER_FOLDER));
        }
        let width = width_of(&items);
        if width <= avail {
            let compacted = items.len() < base.len();
            return Plan { items, width, compacted, over_six: false, left_out: vec![] };
        }
    }
    // Last resort: as many folders as fit, each holding more than 6 apps if it must.
    let room = avail - MENU_W - SLACK_W;
    if all.len() == 1 && room >= APP_W {
        let items = items_for(&base[0].0, &base[0].0, &all, MAX_PER_FOLDER);
        return Plan { width: width_of(&items), items, compacted: false, over_six: false, left_out: vec![] };
    }
    let folders = ((room / FOLDER_W).floor().max(0.0) as usize).min(all.len() / 2);
    if folders == 0 {
        return Plan { items: vec![], width: MENU_W + SLACK_W, compacted: true, over_six: false, left_out: all };
    }
    let per = all.len().div_ceil(folders);
    let items = items_for("__more", MORE_LABEL, &all, per);
    let over_six = items.iter().any(|i| i.exes.len() > MAX_PER_FOLDER);
    Plan { width: width_of(&items), items, compacted: true, over_six, left_out: vec![] }
}

/// Taskbar geometry in physical pixels, read with UI Automation and window rectangles.
#[derive(Clone, Debug, Default)]
pub struct TaskbarGeom {
    pub left: i32,
    pub right: i32,
    pub start_left: Option<i32>,
    pub start_right: Option<i32>,
    /// Right edge of the last native taskbar button.
    pub buttons_right: Option<i32>,
    /// Left edges of things the strip must stay left of: the notification area, the "Show hidden icons"
    /// chevron, the Widgets button on a left-aligned taskbar. Edges left of Start are ignored.
    pub obstacles: Vec<i32>,
    pub scale: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Span { pub x: i32, pub limit: i32 }
impl Span { pub fn width(self) -> i32 { (self.limit - self.x).max(0) } }

pub fn free_span(g: &TaskbarGeom) -> Span {
    let scale = if g.scale.is_finite() && g.scale > 0.0 { g.scale } else { 1.0 };
    let icons_right = match (g.start_right, g.buttons_right) {
        (Some(a), Some(b)) => a.max(b),
        (a, b) => a.or(b).unwrap_or(g.left + (g.right - g.left) / 2),
    };
    let gap = (2.0 * scale).round() as i32;
    let margin = (8.0 * scale).round() as i32;
    let x = (icons_right + gap).clamp(g.left, g.right);
    // Anything left of Start (the Widgets button on a centered taskbar) is not in the way. Anything between Start
    // and the end of the buttons means there is no room at all, which is the safe answer.
    let floor = g.start_left.unwrap_or(icons_right);
    let edge = g.obstacles.iter().copied().filter(|&o| o > floor && o <= g.right).min()
        .unwrap_or(g.right - (480.0 * scale).round() as i32);
    Span { x, limit: (edge - margin).clamp(x, g.right) }
}

/// Start sits near the middle on a centered taskbar and at the left edge on a left-aligned one.
pub fn is_centered(g: &TaskbarGeom) -> bool {
    let scale = if g.scale > 0.0 { g.scale } else { 1.0 };
    g.start_left.map(|s| (s - g.left) as f64 > 120.0 * scale).unwrap_or(false)
}

/// Room the strip will gain once the Dock hides the native buttons of apps it is about to group
/// (physical pixels). On a centered taskbar the button row shrinks from both sides, so the right edge moves by
/// half a button per hidden button.
pub fn projected_gain(visible_buttons: usize, pending_apps: usize, button_w: f64, centered: bool) -> f64 {
    let n = visible_buttons.min(pending_apps) as f64;
    n * button_w.max(0.0) * if centered { 0.5 } else { 1.0 }
}

/// "Does the strip fit?" for the watchdog. A strip wider than today's free space is allowed only briefly, and
/// only when hiding the pending native buttons will make the room (`gain`). If that promise is not kept within
/// `GRACE_MS`, projections are switched off for `COOLDOWN_MS`, so the Dock cannot flap between modes.
#[derive(Clone, Copy, Debug, Default)]
pub struct FitGate { clipped_since: Option<u64>, projection_off_until: u64 }
impl FitGate {
    pub const GRACE_MS: u64 = 2_500;
    pub const COOLDOWN_MS: u64 = 300_000;
    pub fn projection_on(&self, now_ms: u64) -> bool { now_ms >= self.projection_off_until }
    pub fn update(&mut self, need: f64, avail: f64, gain: f64, now_ms: u64) -> bool {
        if need <= avail + 0.5 { self.clipped_since = None; return true; }
        if !self.projection_on(now_ms) || need > avail + gain.max(0.0) + 0.5 { self.clipped_since = None; return false; }
        let since = *self.clipped_since.get_or_insert(now_ms);
        if now_ms.saturating_sub(since) < Self::GRACE_MS { return true; }
        self.clipped_since = None;
        self.projection_off_until = now_ms + Self::COOLDOWN_MS;
        false
    }
}

/// After the foreground window changes, hide its native button again on every tick for `BURST_MS`
/// (Windows re-adds the button asynchronously, sometimes a few hundred milliseconds after activation).
#[derive(Clone, Copy, Debug, Default)]
pub struct RehideBurst { last_fg: isize, until: u64 }
impl RehideBurst {
    pub const BURST_MS: u64 = 1_500;
    /// Returns the window to hide again now, if any.
    pub fn tick(&mut self, fg: isize, now_ms: u64, is_hidden: impl Fn(isize) -> bool) -> Option<isize> {
        if fg != self.last_fg { self.last_fg = fg; self.until = now_ms + Self::BURST_MS; }
        (fg != 0 && now_ms <= self.until && is_hidden(fg)).then_some(fg)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn apps(cat: &str, n: usize) -> (String, Vec<String>) {
        (cat.to_string(), (0..n).map(|i| format!("C:\\{cat}\\app{i}.exe")).collect())
    }
    fn check_rules(p: &Plan, total: usize, avail: f64) {
        let placed: usize = p.items.iter().map(|i| i.exes.len()).sum::<usize>() + p.left_out.len();
        assert_eq!(placed, total, "every app is placed exactly once or left native");
        let mut seen = std::collections::HashSet::new();
        for i in &p.items {
            assert!(!i.exes.is_empty());
            if i.folder { assert!(i.exes.len() >= 2, "never a one-app folder: {i:?}"); } else { assert_eq!(i.exes.len(), 1); }
            if !p.over_six { assert!(i.exes.len() <= MAX_PER_FOLDER, "at most 6 apps per folder: {i:?}"); }
            assert!(seen.insert(i.key.clone()), "keys are unique: {}", i.key);
            for e in &i.exes { assert!(!p.left_out.contains(e)); }
        }
        if !p.items.is_empty() { assert!(p.width <= avail + 1e-9, "the plan fits: {} > {avail}", p.width); }
        assert!((p.width - width_of(&p.items)).abs() < 1e-9);
    }

    #[test]
    fn chunks_are_balanced_and_never_one() {
        for n in 2..=200 {
            let c = chunk_sizes(n, 6);
            assert_eq!(c.iter().sum::<usize>(), n);
            assert!(c.iter().all(|&s| (2..=6).contains(&s)), "n={n} {c:?}");
            assert!(c.iter().max().unwrap() - c.iter().min().unwrap() <= 1);
            assert_eq!(c.len(), n.div_ceil(6));
        }
        assert_eq!(chunk_sizes(1, 6), vec![1]);
        assert_eq!(chunk_sizes(7, 6), vec![4, 3]);
        assert_eq!(chunk_sizes(13, 6), vec![5, 4, 4]);
    }

    #[test]
    fn big_category_overflows_into_another_folder_of_the_same_category() {
        let p = plan(&[apps("Browsers", 8)], 1e9);
        assert_eq!(p.items.len(), 2);
        assert_eq!(p.items[0].key, "Browsers");
        assert_eq!(p.items[1].key, "Browsers#2");
        assert_eq!(p.items[1].label, "Browsers 2");
        assert!(p.items.iter().all(|i| i.folder && i.exes.len() == 4));
        assert!(!p.compacted);
    }

    #[test]
    fn single_apps_show_directly_with_plenty_of_room() {
        let p = plan(&[apps("Chat", 1), apps("Music", 1), apps("Games", 2)], 1e9);
        assert_eq!(p.items.iter().map(|i| i.folder).collect::<Vec<_>>(), vec![false, false, true]);
        assert_eq!(p.width, 40.0 + 40.0 + 44.0 + MENU_W + SLACK_W);
    }

    #[test]
    fn short_space_merges_lowest_priority_items_first() {
        let g = [apps("Browsers", 3), apps("Chat", 1), apps("Music", 1), apps("Games", 1)];
        let full = plan(&g, 1e9).width;
        let p = plan(&g, full - 1.0);
        assert!(p.compacted);
        assert_eq!(p.items[0].key, "Browsers", "the top category keeps its own folder");
        let last = p.items.last().unwrap();
        assert!(last.folder && last.label == MORE_LABEL);
        check_rules(&p, 6, full - 1.0);
    }

    #[test]
    fn tail_from_one_category_keeps_its_name() {
        let g = [apps("Chat", 1), apps("Browsers", 9)];
        let p = plan(&g, width_of(&[]) + 40.0 + 44.0 + 44.0);
        assert!(p.items.iter().skip(1).all(|i| i.label.starts_with("Browsers")));
    }

    #[test]
    fn no_room_leaves_apps_native_and_one_folder_room_holds_everything() {
        let g = [apps("A", 5), apps("B", 5), apps("C", 5)];
        let p = plan(&g, MENU_W + SLACK_W + 10.0);
        assert!(p.items.is_empty() && p.left_out.len() == 15);
        let p = plan(&g, MENU_W + SLACK_W + FOLDER_W);
        assert_eq!(p.items.len(), 1);
        assert!(p.over_six && p.items[0].exes.len() == 15);
        check_rules(&p, 15, MENU_W + SLACK_W + FOLDER_W);
    }

    #[test]
    fn many_widths_and_app_counts_always_fit_and_follow_the_rules() {
        let mut cases = 0;
        for cats in 1..=9 {
            for per in [1usize, 2, 3, 5, 6, 7, 12, 13] {
                let g: Vec<_> = (0..cats).map(|c| apps(&format!("Cat{c}"), if c % 2 == 0 { per } else { 1 + per / 2 })).collect();
                let total: usize = g.iter().map(|x| x.1.len()).sum();
                for avail in (0..=1600).step_by(7) {
                    let avail = avail as f64;
                    let p = plan(&g, avail);
                    check_rules(&p, total, avail);
                    // max 6 only breaks when even ceil(n/6) folders cannot fit
                    if p.over_six { assert!(avail < total.div_ceil(6) as f64 * FOLDER_W + MENU_W + SLACK_W); }
                    if !p.left_out.is_empty() { assert!(avail < MENU_W + SLACK_W + APP_W.min(FOLDER_W) + if total > 1 { 4.0 } else { 0.0 }); }
                    cases += 1;
                }
            }
        }
        assert!(cases > 15_000);
    }

    #[test]
    fn more_room_never_means_fewer_items() {
        let g = [apps("A", 4), apps("B", 1), apps("C", 2), apps("D", 1), apps("E", 7)];
        let mut prev = 0;
        for avail in (0..1200).step_by(3) {
            let n = plan(&g, avail as f64).items.len();
            assert!(n >= prev, "avail {avail}: {n} < {prev}");
            prev = n;
        }
    }

    fn geom(width: i32, scale: f64, start_left: i32, buttons: usize, tray_w: i32) -> TaskbarGeom {
        let btn = (44.0 * scale).round() as i32;
        let start_right = start_left + btn;
        TaskbarGeom { left: 0, right: width, start_left: Some(start_left), start_right: Some(start_right),
            // Windows moves extra buttons into its own overflow menu before they reach the tray
            buttons_right: (buttons > 0).then(|| (start_right + btn * buttons as i32).min(width - tray_w - (12.0 * scale) as i32)),
            obstacles: vec![width - tray_w, width - tray_w + 32], scale }
    }

    #[test]
    fn this_desktop_matches_the_measured_taskbar() {
        // Measured on the owner's 2560 px desktop at 100%: Start 994-1039, last button 1567, chevron 2222.
        let g = TaskbarGeom { left: 0, right: 2560, start_left: Some(994), start_right: Some(1039), buttons_right: Some(1567),
            obstacles: vec![2222, 2222, 2254], scale: 1.0 };
        assert_eq!(free_span(&g), Span { x: 1569, limit: 2214 });
        assert!(is_centered(&g));
    }

    #[test]
    fn the_strip_never_passes_the_tray_at_any_scale_or_screen() {
        // 13 to 16 inch laptop panels and desktops, 100 to 200 percent
        let screens = [1280, 1366, 1440, 1536, 1920, 2048, 2256, 2560, 2880, 3000, 3200, 3840];
        let scales = [1.0, 1.25, 1.5, 1.75, 2.0];
        for &w in &screens { for &s in &scales { for buttons in 0..30 { for centered in [false, true] {
            let tray = (300.0 * s) as i32;
            let start = if centered { w / 2 - (44.0 * s * (buttons as f64 + 1.0) / 2.0) as i32 } else { (12.0 * s) as i32 };
            let g = geom(w, s, start.max(0), buttons, tray);
            let span = free_span(&g);
            assert!(span.x >= g.start_right.unwrap(), "starts after the native buttons");
            // (a panel too small for its scale can leave no room at all: then nothing is drawn)
            assert!(span.width() == 0 || span.limit <= w - tray, "w={w} s={s} b={buttons}: limit {} passes tray at {}", span.limit, w - tray);
            assert!(span.limit >= span.x);
            // whatever the plan decides fits inside the span once scaled
            let avail_css = span.width() as f64 / s;
            let p = plan(&[apps("A", buttons.max(1)), apps("B", 3), apps("C", 1)], avail_css);
            if !p.items.is_empty() { assert!((p.width * s).floor() as i32 <= span.width()); }
        }}}}
    }

    #[test]
    fn missing_tray_reading_falls_back_to_a_safe_margin() {
        let g = TaskbarGeom { left: 0, right: 1920, start_left: Some(12), start_right: Some(56), buttons_right: Some(400), obstacles: vec![], scale: 1.5 };
        assert_eq!(free_span(&g).limit, 1920 - 720 - 12);
        // the Widgets button left of Start on a centered taskbar is ignored
        let c = TaskbarGeom { start_left: Some(900), start_right: Some(966), buttons_right: Some(1100), obstacles: vec![10, 1500], ..g.clone() };
        assert_eq!(free_span(&c).limit, 1488);
        // something between Start and the last button: no room rather than overlap
        let g2 = TaskbarGeom { obstacles: vec![300], ..g.clone() };
        assert_eq!(free_span(&g2).width(), 0);
        // buttons right up against the tray: no room, never negative
        let g3 = TaskbarGeom { buttons_right: Some(1500), obstacles: vec![1502], ..g };
        assert_eq!(free_span(&g3).width(), 0);
        assert!(!is_centered(&g3));
    }

    #[test]
    fn projected_gain_counts_only_buttons_that_will_go() {
        assert_eq!(projected_gain(10, 4, 44.0, false), 176.0);
        assert_eq!(projected_gain(10, 4, 44.0, true), 88.0);
        assert_eq!(projected_gain(2, 9, 66.0, false), 132.0);
        assert_eq!(projected_gain(5, 0, 44.0, true), 0.0);
    }

    #[test]
    fn fit_gate_allows_a_short_promised_overflow_then_cools_down() {
        let mut g = FitGate::default();
        assert!(g.update(100.0, 120.0, 0.0, 0));
        assert!(!g.update(200.0, 120.0, 40.0, 10), "no promise of enough room");
        assert!(g.update(150.0, 120.0, 40.0, 100), "room is coming");
        assert!(g.update(150.0, 120.0, 40.0, 100 + FitGate::GRACE_MS - 1));
        assert!(!g.update(150.0, 120.0, 40.0, 100 + FitGate::GRACE_MS), "promise not kept");
        assert!(!g.projection_on(200 + FitGate::GRACE_MS));
        assert!(!g.update(150.0, 120.0, 40.0, 300 + FitGate::GRACE_MS), "no projection during cooldown");
        assert!(g.update(110.0, 120.0, 0.0, 400 + FitGate::GRACE_MS), "a strip that fits always fits");
        assert!(g.projection_on(100 + FitGate::GRACE_MS + FitGate::COOLDOWN_MS));
        // room arriving in time resets the grace clock
        let mut g = FitGate::default();
        assert!(g.update(150.0, 120.0, 40.0, 0));
        assert!(g.update(150.0, 160.0, 0.0, 1000));
        assert!(g.update(170.0, 160.0, 40.0, 2000));
        assert!(g.update(170.0, 160.0, 40.0, 2000 + FitGate::GRACE_MS - 1));
    }

    #[test]
    fn rehide_burst_follows_foreground_changes() {
        let hidden = |h: isize| h == 7 || h == 9;
        let mut b = RehideBurst::default();
        assert_eq!(b.tick(7, 0, hidden), Some(7));
        assert_eq!(b.tick(7, RehideBurst::BURST_MS, hidden), Some(7));
        assert_eq!(b.tick(7, RehideBurst::BURST_MS + 1, hidden), None, "burst ends");
        assert_eq!(b.tick(5, 5000, hidden), None, "a window with a visible button is left alone");
        assert_eq!(b.tick(9, 5100, hidden), Some(9), "a new foreground window starts a new burst");
        assert_eq!(b.tick(0, 5200, hidden), None);
    }
}
