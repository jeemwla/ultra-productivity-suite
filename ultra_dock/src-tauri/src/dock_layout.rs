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
pub const MENU_W: f64 = 40.0;
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
pub fn plan(groups: &[(String, Vec<String>)], avail: f64) -> Plan { plan_capped(groups, avail, None) }

/// The items before any squeezing for space.
/// - No cap ("Automatic"): every category with two or more apps is a folder, a category with one app shows it.
/// - Cap N (the owner's "show N apps" setting): the first N apps show as their own buttons, the rest go into
///   folders by category (single leftovers share "More apps" folders). Never a one-app folder: when exactly one
///   app is left over, the Nth app joins it, so the strip never shows more than N separate apps.
fn base_items(groups: &[(String, Vec<String>)], cap: Option<usize>) -> Vec<(String, Item)> {
    let mut base: Vec<(String, Item)> = vec![];
    let Some(cap) = cap else {
        for (cat, exes) in groups {
            if exes.is_empty() { continue; }
            for it in items_for(cat, cat, exes, MAX_PER_FOLDER) { base.push((cat.clone(), it)); }
        }
        return base;
    };
    let flat: Vec<(String, String)> = groups.iter().flat_map(|(c, es)| es.iter().map(move |e| (c.clone(), e.clone()))).collect();
    let mut loose = cap.min(flat.len());
    if flat.len() - loose == 1 && loose > 0 { loose -= 1; }
    // A category cut by the cap with a single app left over keeps that app with its own kind: the category's last
    // separate app joins it, so the folder is "Chat" rather than a Chat app inside another category's folder.
    if loose > 0 && loose < flat.len() && flat[loose - 1].0 == flat[loose].0
        && flat[loose..].iter().filter(|(c, _)| *c == flat[loose].0).count() == 1 {
        loose -= 1;
    }
    for (cat, exe) in &flat[..loose] {
        base.push((cat.clone(), Item { key: format!("app:{exe}"), label: cat.clone(), folder: false, exes: vec![exe.clone()] }));
    }
    let rest = &flat[loose..];
    if rest.len() == 1 {  // one app in all, and a cap of 0: a one-app folder would only add a click
        base.push((rest[0].0.clone(), Item { key: format!("app:{}", rest[0].1), label: rest[0].0.clone(), folder: false, exes: vec![rest[0].1.clone()] }));
        return base;
    }
    let mut cats: Vec<(String, Vec<String>)> = vec![];
    for (cat, exe) in rest {
        match cats.iter_mut().find(|c| c.0 == *cat) { Some(c) => c.1.push(exe.clone()), None => cats.push((cat.clone(), vec![exe.clone()])) }
    }
    let mut pool: Vec<String> = cats.iter().filter(|c| c.1.len() == 1).map(|c| c.1[0].clone()).collect();
    let mut folders: Vec<(String, Vec<String>)> = cats.into_iter().filter(|c| c.1.len() > 1).collect();
    if pool.len() == 1 {
        // a single leftover joins the last category folder rather than standing alone
        if let Some(last) = folders.last_mut() { last.1.push(pool.pop().unwrap()); }
    }
    for (cat, exes) in &folders {
        for it in items_for(cat, cat, exes, MAX_PER_FOLDER) { base.push((cat.clone(), it)); }
    }
    if !pool.is_empty() {
        for it in items_for("__more", MORE_LABEL, &pool, MAX_PER_FOLDER) { base.push(("__more".to_string(), it)); }
    }
    base
}

pub fn plan_capped(groups: &[(String, Vec<String>)], avail: f64, cap: Option<usize>) -> Plan {
    let avail = if avail.is_finite() { avail.max(0.0) } else { f64::MAX };
    let base = base_items(groups, cap);
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
            let (key, label) = if one_cat && tail[0].0 != "__more" { (tail[0].1.key.clone(), tail[0].0.clone()) }
                else if one_cat { (tail[0].1.key.clone(), MORE_LABEL.to_string()) } else { ("__more".to_string(), MORE_LABEL.to_string()) };
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

/// Hysteresis for the room the folder plan uses, so a reading that wobbles by a few pixels (UI Automation
/// rounding, a clock that gets one digit wider) cannot regroup the folders back and forth. The plan follows a
/// change of `ROOM_STEP` CSS pixels or more, and follows at once whenever the current plan no longer fits.
pub const ROOM_STEP: f64 = 12.0;
pub fn settle_room(used: Option<f64>, new: f64, plan_width: f64) -> f64 {
    match used {
        Some(u) if new + 0.5 >= plan_width && (new - u).abs() < ROOM_STEP => u,
        _ => new,
    }
}

/// Taskbar geometry in physical pixels, read with UI Automation and window rectangles.
#[derive(Clone, Debug, Default, serde::Serialize)]
pub struct TaskbarGeom {
    pub icon_size_css: Option<f64>,
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
    /// Complete physical obstacle extents, including Widgets left of Start.
    pub obstacle_intervals: Vec<(i32, i32)>,
    /// Monotonic milliseconds, same clock as paired_plan's `now`.
    pub measured_at: Option<u64>,
    pub left_obstacles_complete: bool,
    /// Caller-validated native alignment capability, never inferred from position.
    /// Some(true) permits paired planning; false/unknown use safe fallback.
    pub centered: Option<bool>,
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

/// Physical allocations include at least 8 CSS pixels of report padding per side.
/// `Plan.width` is the side's content + 8 CSS pixels; LEFT HAS NO MENU,
/// right includes MENU_W. Equal physical spans may include additional outer padding.
#[derive(Clone, Debug, PartialEq)]
pub struct PairedPlan {
    pub left: Plan,
    pub right: Plan,
    pub left_span: Span,
    pub right_span: Span,
    pub generation: u64,
}
pub const PAIRED_GEOMETRY_MAX_AGE_MS: u64 = 1_000;
pub const PAIRED_PADDING_CSS: f64 = 8.0;

/// Shared measured geometry for initial planning and fixed-partition reflow.
/// The centered planner validates the full obstacle intervals before placement.
fn paired_input(g: &TaskbarGeom, now: u64, generation: u64) -> Option<crate::dock_centered_layout::Input> {
    use crate::dock_centered_layout as centered;
    let at = g.measured_at?;
    if !g.left_obstacles_complete || at > now || now - at > PAIRED_GEOMETRY_MAX_AGE_MS
        || !g.scale.is_finite() || g.scale <= 0.0
        || g.centered != Some(true) { return None; }
    let nl = g.start_left?;
    let sr = g.start_right?;
    let nr = g.buttons_right?.max(sr);
    if g.left >= g.right || nl < g.left || nl >= sr || nr > g.right { return None; }
    let native = centered::Rect { left: nl, right: nr };
    let mut obstacles: Vec<_> = g.obstacle_intervals.iter().map(|&(left,right)| centered::Rect {left,right}).collect();
    // Legacy right-edge obstacles remain conservative even if no full interval
    // was supplied for that particular notification-area measurement.
    for &edge in &g.obstacles {
        if edge >= nr && edge < g.right { obstacles.push(centered::Rect {left:edge,right:g.right}); }
    }
    Some(centered::Input {
        taskbar:centered::Rect{left:g.left,right:g.right}, native,
        obstacles,scale:g.scale,items:vec![],
        requested_center:(f64::from(nl)+f64::from(nr))/2.0,gap_css:2.0,
        generation,expected_generation:generation,measured_at_ms:at,now_ms:now,
        max_age_ms:PAIRED_GEOMETRY_MAX_AGE_MS,
    })
}

/// Fresh complete centered geometry only. Unknown space never authorizes hiding.
/// Cap and compaction use the existing planner, followed by contiguous whole-item
/// partitioning. Both allocations are active: left apps and right apps/menu.
pub fn paired_plan(groups: &[(String, Vec<String>)], g: &TaskbarGeom, cap: Option<usize>, now: u64) -> Option<PairedPlan> {
    use crate::dock_centered_layout as centered;
    let mut input = paired_input(g, now, 1)?;
    let nl = input.native.left;
    let nr = input.native.right;
    let left_edge = input.obstacles.iter().filter(|r|r.right <= nl).map(|r|r.right).max().unwrap_or(g.left);
    let right_edge = input.obstacles.iter().filter(|r|r.left >= nr).map(|r|r.left).min().unwrap_or(g.right);
    let gap = (2.0*g.scale).ceil();
    let capacity = (f64::from(nl)-f64::from(left_edge)-gap)
        .min(f64::from(right_edge)-f64::from(nr)-gap);
    if capacity <= 0.0 { return None; }
    // Legacy plan width includes 6 CSS slack; paired sides replace it with 8 each.
    let mut budget = 2.0*capacity/g.scale - 2.0*PAIRED_PADDING_CSS + SLACK_W;
    loop {
        let whole = plan_capped(groups, budget, cap);
        if whole.items.is_empty() || !whole.left_out.is_empty() { return None; }
        let mut items: Vec<_> = whole.items.iter().enumerate().map(|(n,it)|centered::Item {
            id: n.to_string(), width_css: if it.folder {FOLDER_W} else {APP_W},
        }).collect();
        let count = items.len();
        items.push(centered::Item {id:"menu".into(),width_css:MENU_W});
        input.items = items;
        if let Ok(placed) = centered::plan_reserved(&input, PAIRED_PADDING_CSS, 1, count) {
            let split = placed.left.items.len();
            let make = |items: Vec<Item>, menu: bool| Plan {
                width:items.iter().map(|i|if i.folder {FOLDER_W} else {APP_W}).sum::<f64>()
                    + if menu {MENU_W} else {0.0} + PAIRED_PADDING_CSS,
                items,compacted:whole.compacted,over_six:whole.over_six,left_out:vec![],
            };
            let left = make(whole.items[..split].to_vec(),false);
            let right = make(whole.items[split..].to_vec(),true);
            let generation = paired_generation(&left,&right);
            return Some(PairedPlan {left,right,
                left_span:Span{x:placed.left.rect.left,limit:placed.left.rect.right},
                right_span:Span{x:placed.right.rect.left,limit:placed.right.rect.right},generation});
        }
        let next = whole.width - 1.0;
        if next >= budget || next < MENU_W + SLACK_W { return None; }
        budget = next;
    }
}

/// Explicit stable FNV-1a encoding, independent of positions, DPI, clock and HashMap seeds.
fn paired_generation(left: &Plan, right: &Plan) -> u64 {
    let mut hash = 0xcbf29ce484222325u64;
    let mut feed = |bytes: &[u8]| { for b in bytes { hash ^= u64::from(*b); hash = hash.wrapping_mul(0x100000001b3); } };
    for (side,plan) in [(0u8,left),(1u8,right)] {
        feed(&[side]);feed(&(plan.items.len() as u64).to_le_bytes());
        for it in &plan.items {
            feed(&[u8::from(it.folder)]);
            feed(&(it.key.len() as u64).to_le_bytes());feed(it.key.as_bytes());
            feed(&(it.exes.len() as u64).to_le_bytes());
            for exe in &it.exes {feed(&(exe.len() as u64).to_le_bytes());feed(exe.as_bytes());}
        }
    }
    hash.max(1)
}

/// Reposition an existing pair against a fresh measured native cluster. Retains
/// both logical plans and their generation exactly, including compacted folders.
/// Each item and the 8 CSS pixels of outer padding round up separately, as in
/// paired_plan. No extra room authorizes expansion, repartitioning or hiding.
/// A changed candidate signature requires initial planning instead of reuse.
pub fn reflow_pair(old: &PairedPlan, g: &TaskbarGeom, now: u64) -> Option<PairedPlan> {
    use crate::dock_centered_layout as centered;
    if old.generation == 0 || old.left.items.is_empty()
        || !old.left.left_out.is_empty() || !old.right.left_out.is_empty() { return None; }
    let mut input = paired_input(g, now, old.generation)?;
    input.items = old.left.items.iter().chain(&old.right.items).enumerate()
        .map(|(n, it)| centered::Item {
            id: n.to_string(), width_css: if it.folder { FOLDER_W } else { APP_W },
        }).collect();
    input.items.push(centered::Item { id: "menu".into(), width_css: MENU_W });
    let split = old.left.items.len();
    let placed = centered::plan_reserved(&input, PAIRED_PADDING_CSS, split, split).ok()?;
    Some(PairedPlan {
        left_span: Span { x: placed.left.rect.left, limit: placed.left.rect.right },
        right_span: Span { x: placed.right.rect.left, limit: placed.right.rect.right },
        ..old.clone()
    })
}

/// Stable identity of the complete ordered candidate and exact cap setting.
/// Cache this beside a PairedPlan: while it matches, try reflow_pair instead of
/// replanning after native buttons disappear. Geometry, DPI and time are absent.
/// This identifies candidate inputs, not a rendered partition generation.
pub fn paired_candidate_signature(groups: &[(String, Vec<String>)], cap: Option<usize>) -> u64 {
    let mut hash = 0xcbf29ce484222325u64;
    let mut feed = |bytes: &[u8]| { for b in bytes { hash ^= u64::from(*b); hash = hash.wrapping_mul(0x100000001b3); } };
    feed(&[u8::from(cap.is_some())]);
    feed(&(cap.unwrap_or(0) as u64).to_le_bytes());
    feed(&(groups.len() as u64).to_le_bytes());
    for (label, exes) in groups {
        feed(&(label.len() as u64).to_le_bytes());feed(label.as_bytes());
        feed(&(exes.len() as u64).to_le_bytes());
        for exe in exes {feed(&(exe.len() as u64).to_le_bytes());feed(exe.as_bytes());}
    }
    hash.max(1)
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
/// only when hiding the native buttons of apps the plan shows will make the room (`gain`). If that promise is not
/// kept within `GRACE_MS`, projections stay off for this situation (`sig`: the apps, the taskbar and the tray,
/// which do not change when the Dock hides or restores buttons) until it changes or `COOLDOWN_MS` passes, so the
/// Dock settles in one mode instead of switching on and off.
#[derive(Clone, Copy, Debug, Default)]
pub struct FitGate { clipped_since: Option<u64>, failed: Option<(u64, u64)> }
impl FitGate {
    pub const GRACE_MS: u64 = 6_000;
    pub const COOLDOWN_MS: u64 = 1_800_000;
    pub fn projection_on(&self, now_ms: u64, sig: u64) -> bool {
        match self.failed { Some((s, at)) => s != sig || now_ms >= at + Self::COOLDOWN_MS, None => true }
    }
    pub fn update(&mut self, need: f64, avail: f64, gain: f64, now_ms: u64, sig: u64) -> bool {
        if need <= avail + 0.5 { self.clipped_since = None; return true; }
        if !self.projection_on(now_ms, sig) || need > avail + gain.max(0.0) + 0.5 { self.clipped_since = None; return false; }
        let since = *self.clipped_since.get_or_insert(now_ms);
        if now_ms.saturating_sub(since) < Self::GRACE_MS { return true; }
        self.fail(now_ms, sig);
        false
    }
    /// The projected room did not arrive: no projections for this situation for a while.
    pub fn fail(&mut self, now_ms: u64, sig: u64) { self.clipped_since = None; self.failed = Some((sig, now_ms)); }
}

/// One reading of the taskbar and the apps, as the tracker sees it every tick.
pub struct Reading<'a> {
    /// Every app the Dock may group (not kept, not pinned), by category in display order.
    pub groups: &'a [(String, Vec<String>)],
    /// Those apps whose native button is still showing.
    pub pending: &'a std::collections::HashSet<String>,
    /// Measured free space, physical pixels.
    pub room: f64,
    pub buttons: usize,
    pub button_w: f64,
    pub centered: bool,
    pub scale: f64,
    /// Collapsed strip width the page measured, CSS pixels.
    pub page_base: f64,
    pub sig: u64,
    pub now_ms: u64,
    /// The owner's "show N apps" setting (None: Automatic).
    pub cap: Option<usize>,
}
pub struct Assessment { pub layout_room: f64, pub fits: bool }

/// The one decision the tracker makes every tick: how much room the folder plan may use, and whether the Dock
/// fits. It judges the plan for every app it may group (not only those already in the strip), so a crowded
/// taskbar can still switch to folders, and it counts only room that hiding the plan's own apps frees.
#[derive(Clone, Copy, Debug, Default)]
pub struct Assessor { pub gate: FitGate, used: Option<f64>, last_plan_w: f64, page_over_since: Option<u64>, projected_at: Option<u64> }
impl Assessor {
    pub fn assess(&mut self, r: &Reading) -> Assessment {
        let scale = if r.scale.is_finite() && r.scale > 0.0 { r.scale } else { 1.0 };
        let on = self.gate.projection_on(r.now_ms, r.sig);
        let gain_all = if on { projected_gain(r.buttons, r.pending.len(), r.button_w, r.centered) } else { 0.0 };
        let room_css = settle_room(self.used, ((r.room + gain_all) / scale).floor(), self.last_plan_w);
        let p = plan_capped(r.groups, room_css, r.cap);
        let hidden_apps = r.groups.iter().flat_map(|g| g.1.iter()).filter(|e| !r.pending.contains(*e)).count();
        let recently_projected = self.projected_at.is_some_and(|t| r.now_ms.saturating_sub(t) <= FitGate::GRACE_MS);
        if gain_all > 0.0 { self.projected_at = Some(r.now_ms); }
        if p.items.is_empty() && hidden_apps > 0 && recently_projected && gain_all == 0.0 {
            // Buttons were hidden on a projection and the room that actually came is too small to show them:
            // a broken promise, so stop projecting here instead of hiding and restoring them in a loop.
            self.gate.fail(r.now_ms, r.sig);
        }
        self.used = Some(room_css);
        self.last_plan_w = p.width;
        let pending_in_plan = p.items.iter().flat_map(|i| i.exes.iter()).filter(|e| r.pending.contains(*e)).count();
        let gain = if on { projected_gain(r.buttons, pending_in_plan, r.button_w, r.centered) } else { 0.0 };
        let fits = self.gate.update(p.width * scale, r.room, gain, r.now_ms, r.sig);
        // The page redraws a moment after the plan changes, so its own measurement may be one plan behind. Only a
        // page that stays wider than the room for the whole grace counts against the fit (a drawing problem).
        let page_ok = if r.page_base * scale <= r.room + 0.5 { self.page_over_since = None; true } else {
            let since = *self.page_over_since.get_or_insert(r.now_ms);
            r.now_ms.saturating_sub(since) < FitGate::GRACE_MS
        };
        Assessment { layout_room: room_css, fits: fits && page_ok }
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
            obstacles: vec![width - tray_w, width - tray_w + 32], scale, ..Default::default() }
    }

    #[test]
    fn this_desktop_matches_the_measured_taskbar() {
        // Measured on the owner's 2560 px desktop at 100%: Start 994-1039, last button 1567, chevron 2222.
        let g = TaskbarGeom { left: 0, right: 2560, start_left: Some(994), start_right: Some(1039), buttons_right: Some(1567),
            obstacles: vec![2222, 2222, 2254], scale: 1.0, ..Default::default() };
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
        let g = TaskbarGeom { left: 0, right: 1920, start_left: Some(12), start_right: Some(56), buttons_right: Some(400), obstacles: vec![], scale: 1.5, ..Default::default() };
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
    fn fit_gate_allows_a_short_promised_overflow_then_stays_off_for_that_situation() {
        let g0 = FitGate::GRACE_MS;
        let mut g = FitGate::default();
        assert!(g.update(100.0, 120.0, 0.0, 0, 1));
        assert!(!g.update(200.0, 120.0, 40.0, 10, 1), "no promise of enough room");
        assert!(g.update(150.0, 120.0, 40.0, 100, 1), "room is coming");
        assert!(g.update(150.0, 120.0, 40.0, 100 + g0 - 1, 1));
        assert!(!g.update(150.0, 120.0, 40.0, 100 + g0, 1), "promise not kept");
        assert!(!g.projection_on(200 + g0, 1));
        assert!(!g.update(150.0, 120.0, 40.0, 300 + g0, 1), "no projection for the same situation");
        assert!(g.projection_on(300 + g0, 2), "a new app or a different taskbar gets a fresh try");
        assert!(g.update(110.0, 120.0, 0.0, 400 + g0, 1), "a strip that fits always fits");
        assert!(g.projection_on(100 + g0 + FitGate::COOLDOWN_MS, 1), "the same situation is retried much later");
        // room arriving in time resets the grace clock
        let mut g = FitGate::default();
        assert!(g.update(150.0, 120.0, 40.0, 0, 1));
        assert!(g.update(150.0, 160.0, 0.0, 1000, 1));
        assert!(g.update(170.0, 160.0, 40.0, 2000, 1));
        assert!(g.update(170.0, 160.0, 40.0, 2000 + g0 - 1, 1));
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

    #[test]
    fn settle_room_ignores_wobble_but_follows_real_changes() {
        assert_eq!(settle_room(None, 300.0, 0.0), 300.0);
        assert_eq!(settle_room(Some(300.0), 305.0, 200.0), 300.0);
        assert_eq!(settle_room(Some(300.0), 290.0, 200.0), 300.0);
        assert_eq!(settle_room(Some(300.0), 320.0, 200.0), 320.0);
        assert_eq!(settle_room(Some(300.0), 295.0, 298.0), 295.0, "the plan no longer fits: follow at once");
    }

    /// A simplified Dock on a centered taskbar (CSS pixels at scale 1), ticking every 700 ms: the organizer hides
    /// what the strip renders while healthy (and restores everything when not), the tracker measures the room left
    /// by the remaining native buttons, and the fit check decides health. Returns the health and strip history.
    fn simulate(screen: f64, tray: f64, pinned: usize, apps: usize, new_logic: bool, ticks: usize) -> (Vec<bool>, Vec<usize>) {
        let mut cats: Vec<(String, Vec<String>)> = vec![];
        for i in 0..apps {
            let c = format!("Cat{}", i % 4);
            match cats.iter_mut().find(|g| g.0 == c) {
                Some(g) => g.1.push(format!("app{i}.exe")),
                None => cats.push((c, vec![format!("app{i}.exe")])),
            }
        }
        let mut hidden: std::collections::HashSet<String> = Default::default();
        let (mut healthy, mut dock, mut page_base) = (false, Assessor::default(), 0.0);
        let (mut history, mut shown) = (vec![], vec![]);
        for t in 0..ticks {
            let now = t as u64 * 700;
            let natives = pinned + apps - hidden.len();
            let row = 44.0 * (natives + 1) as f64;  // Start plus the buttons, centered
            let start_left = ((screen - row) / 2.0).max(0.0);
            // Windows moves its own extra buttons into an overflow menu before they reach the tray
            let buttons_right = (start_left + row).min(screen - tray - 12.0);
            let g = TaskbarGeom { left: 0, right: screen as i32, start_left: Some(start_left as i32),
                start_right: Some((start_left + 44.0) as i32), buttons_right: Some(buttons_right as i32),
                obstacles: vec![(screen - tray) as i32], scale: 1.0, ..Default::default() };
            let room = free_span(&g).width() as f64;
            let listed: Vec<(String, Vec<String>)> = if healthy { cats.clone() } else { vec![] };
            let rendered: Vec<String> = if new_logic {
                let pending: std::collections::HashSet<String> =
                    cats.iter().flat_map(|c| c.1.iter().cloned()).filter(|e| !hidden.contains(e)).collect();
                let a = dock.assess(&Reading { groups: &cats, pending: &pending, room, buttons: natives, button_w: 44.0,
                    centered: is_centered(&g), scale: 1.0, page_base, sig: (apps * 1000 + pinned) as u64, now_ms: now, cap: None });
                let p = plan(&listed, a.layout_room);
                page_base = p.width - 4.0;
                healthy = a.fits;
                p.items.iter().flat_map(|i| i.exes.clone()).collect()
            } else {
                // 2.2.15: every listed app rendered; healthy only while that strip is not clipped
                let w = listed.iter().map(|(_, e)| if e.len() > 1 { FOLDER_W } else { APP_W }).sum::<f64>() + MENU_W + SLACK_W;
                healthy = w - 4.0 <= room;
                listed.iter().flat_map(|(_, e)| e.clone()).collect()
            };
            hidden = if healthy { rendered.iter().cloned().collect() } else { Default::default() };
            history.push(healthy);
            shown.push(if healthy { rendered.len() } else { 0 });
        }
        (history, shown)
    }
    fn flips(h: &[bool]) -> usize { h.windows(2).filter(|w| w[0] != w[1]).count() }

    #[test]
    fn old_logic_flaps_on_a_crowded_laptop_taskbar_and_new_logic_settles() {
        // G14-like: 1440 CSS px wide, a 330 px tray, 4 pinned apps, 8 open apps
        let (h, _) = simulate(1440.0, 330.0, 4, 8, false, 200);
        assert!(flips(&h[20..]) > 20, "the 2.2.15 model keeps switching modes: {}", flips(&h[20..]));
        let (h, shown) = simulate(1440.0, 330.0, 4, 8, true, 200);
        assert_eq!(flips(&h[20..]), 0);
        assert!(h[199] && shown[199] == 8, "all 8 apps end up in the strip");
    }

    #[test]
    fn new_logic_settles_on_every_small_and_large_screen() {
        let mut checked = 0;
        for screen in [1097.0, 1280.0, 1366.0, 1440.0, 1536.0, 1707.0, 1920.0, 2560.0] {
            for tray in [220.0, 330.0, 420.0] {
                for pinned in [0, 3, 6, 10] {
                    for apps in [0, 1, 2, 5, 9, 14, 20, 30] {
                        let (h, shown) = simulate(screen, tray, pinned, apps, true, 900);
                        let tail = &h[h.len() - 450..];
                        assert_eq!(flips(tail), 0, "screen {screen} tray {tray} pinned {pinned} apps {apps}: {} flips", flips(tail));
                        let st = &shown[shown.len() - 450..];
                        assert!(st.windows(2).all(|w| w[0] == w[1]), "screen {screen} tray {tray} pinned {pinned} apps {apps}: strip keeps changing");
                        checked += 1;
                    }
                }
            }
        }
        assert_eq!(checked, 8 * 3 * 4 * 8);
    }

    fn loose(p: &Plan) -> usize { p.items.iter().filter(|i| !i.folder).count() }

    #[test]
    fn cap_shows_the_first_apps_and_folders_the_rest() {
        // 12 apps in 4 categories, owner picks 10: 10 buttons, 11th and 12th in one folder
        let g = [apps("Browsers", 3), apps("Chat", 3), apps("Games", 3), apps("Music", 3)];
        let p = plan_capped(&g, 1e9, Some(10));
        assert_eq!(loose(&p), 10);
        let f: Vec<_> = p.items.iter().filter(|i| i.folder).collect();
        assert_eq!(f.len(), 1);
        assert_eq!(f[0].exes, apps("Music", 3).1[1..].to_vec());
        assert_eq!(f[0].label, "Music");
        // 4: the first four apps, then folders by category
        let p = plan_capped(&g, 1e9, Some(4));
        assert_eq!(loose(&p), 4);
        assert_eq!(p.items.iter().filter(|i| i.folder).map(|i| i.label.as_str()).collect::<Vec<_>>(), vec!["Chat", "Games", "Music"]);
        // 5 would leave one Chat app alone, so the fifth (also Chat) joins it: 4 buttons and a Chat folder of 2
        let p = plan_capped(&g, 1e9, Some(5));
        assert_eq!(loose(&p), 4);
        assert_eq!(p.items.iter().find(|i| i.folder && i.label == "Chat").unwrap().exes.len(), 2);
        assert_eq!(p.items.iter().filter(|i| i.folder).map(|i| i.label.as_str()).collect::<Vec<_>>(), vec!["Chat", "Games", "Music"]);
        // a cap at or above the app count shows every app
        assert_eq!(loose(&plan_capped(&g, 1e9, Some(12))), 12);
        assert_eq!(loose(&plan_capped(&g, 1e9, Some(40))), 12);
        // 0: everything in folders
        let p = plan_capped(&g, 1e9, Some(0));
        assert_eq!(loose(&p), 0);
        assert_eq!(p.items.len(), 4);
    }

    #[test]
    fn cap_never_makes_a_one_app_folder() {
        let g = [apps("A", 4), apps("B", 2)];
        // 6 apps, cap 5: one left over, so the 5th joins it
        let p = plan_capped(&g, 1e9, Some(5));
        assert_eq!(loose(&p), 4);
        assert!(p.items.iter().filter(|i| i.folder).all(|i| i.exes.len() >= 2));
        // single leftovers from different categories share a More apps folder
        let g = [apps("A", 2), apps("B", 1), apps("C", 1), apps("D", 1)];
        let p = plan_capped(&g, 1e9, Some(2));
        assert_eq!(loose(&p), 2);
        assert_eq!(p.items.last().unwrap().label, MORE_LABEL);
        assert_eq!(p.items.last().unwrap().exes.len(), 3);
        // one leftover single next to a category folder joins that folder
        let g = [apps("A", 1), apps("B", 2), apps("C", 1)];
        let p = plan_capped(&g, 1e9, Some(1));
        assert_eq!(loose(&p), 1);
        assert_eq!(p.items.len(), 2);
        assert_eq!(p.items[1].exes.len(), 3);
        // a single app with cap 0 still shows (a folder of one would only add a click)
        assert_eq!(loose(&plan_capped(&[apps("A", 1)], 1e9, Some(0))), 1);
    }

    #[test]
    fn cap_follows_every_rule_at_every_width() {
        let mut cases = 0;
        for cats in 1..=6 {
            for per in [1usize, 2, 3, 6, 7] {
                let g: Vec<_> = (0..cats).map(|c| apps(&format!("Cat{c}"), if c % 2 == 0 { per } else { 1 + per / 3 })).collect();
                let total: usize = g.iter().map(|x| x.1.len()).sum();
                for cap in [0usize, 1, 2, 3, 5, 8, 10, 15, 25] {
                    for avail in (0..=1400).step_by(13) {
                        let p = plan_capped(&g, avail as f64, Some(cap));
                        check_rules(&p, total, avail as f64);
                        let l = loose(&p);
                        assert!(l <= cap.max(usize::from(total == 1)), "cap {cap}: {l} separate apps");
                        cases += 1;
                    }
                }
            }
        }
        assert!(cases > 25_000);
    }
}

#[cfg(test)]
mod paired_tests {
    use super::*;
    fn geometry(portrait: bool) -> TaskbarGeom {
        let (left,right,nl,nr,scale)=if portrait {(-1440,0,-776,-665,1.25)} else {(0,2560,1126,1435,1.0)};
        TaskbarGeom {left,right,start_left:Some(nl),start_right:Some(nl+40),buttons_right:Some(nr),scale,
            measured_at:Some(100),left_obstacles_complete:true,centered:Some(true),
            obstacle_intervals:vec![(left,left+100),(right-100,right)],..Default::default()}
    }
    fn groups(n:usize)->Vec<(String,Vec<String>)>{vec![("Apps".into(),(0..n).map(|n|format!("app{n}")).collect())]}
    fn check(g:&TaskbarGeom,p:&PairedPlan){
        assert!(!p.left.items.is_empty());assert_ne!(p.generation,0);
        assert_eq!(p.left_span.width(),p.right_span.width());
        assert_eq!(i64::from(p.left_span.x)+i64::from(p.right_span.limit),i64::from(g.start_left.unwrap())+i64::from(g.buttons_right.unwrap()));
        assert!(p.left_span.limit<=g.start_left.unwrap()-(2.0*g.scale).ceil() as i32);
        assert!(p.right_span.x>=g.buttons_right.unwrap()+(2.0*g.scale).ceil() as i32);
        for span in [p.left_span,p.right_span]{assert!(span.x>=g.left&&span.limit<=g.right);
            for &(a,b) in &g.obstacle_intervals {assert!(span.limit<=a||span.x>=b);}}
        for (side,menu,span) in [(&p.left,false,p.left_span),(&p.right,true,p.right_span)]{
            let content=side.items.iter().map(|i|if i.folder{FOLDER_W}else{APP_W}).sum::<f64>()+if menu{MENU_W}else{0.0};
            assert_eq!(side.width,content+8.0);assert!(side.width*g.scale<=f64::from(span.width())+0.001);
        }
    }
    #[test] fn measured_primary_and_portrait_are_balanced(){for portrait in [false,true]{let g=geometry(portrait);let p=paired_plan(&groups(10),&g,Some(7),150).unwrap();check(&g,&p);}}
    #[test] fn generation_survives_translation_and_changes_with_executable_identity(){
        let g=geometry(false);let p=paired_plan(&groups(10),&g,Some(7),150).unwrap();
        let mut moved=g.clone();moved.left-=3000;moved.right-=3000;
        moved.start_left=moved.start_left.map(|x|x-3000);moved.start_right=moved.start_right.map(|x|x-3000);moved.buttons_right=moved.buttons_right.map(|x|x-3000);
        moved.obstacle_intervals.iter_mut().for_each(|(a,b)|{*a-=3000;*b-=3000;});
        let q=paired_plan(&groups(10),&moved,Some(7),150).unwrap();assert_eq!(p.generation,q.generation);
        let mut other=groups(10);other[0].1[0]="replacement".into();assert_ne!(p.generation,paired_plan(&other,&g,Some(7),150).unwrap().generation);
    }
    #[test] fn missing_stale_left_aligned_and_menu_only_return_none(){
        for field in 0..6 {let mut g=geometry(false);match field {0=>g.measured_at=None,1=>g.measured_at=Some(0),2=>g.left_obstacles_complete=false,3=>g.start_left=None,4=>g.buttons_right=None,_=>g.centered=Some(false)};
            assert!(paired_plan(&groups(4),&g,None,1500).is_none());}
        assert!(paired_plan(&[],&geometry(false),None,150).is_none());
        let mut g=geometry(false);g.measured_at=Some(200);assert!(paired_plan(&groups(3),&g,None,150).is_none());
    }
    #[test] fn paired_alignment_requires_explicit_capability_not_start_offset(){
        let mut g=geometry(false);
        for centered in [None,Some(false)] {g.centered=centered;assert!(paired_plan(&groups(3),&g,None,150).is_none());}
        g.centered=Some(true);assert!(paired_plan(&groups(3),&g,None,150).is_some());
        // A caller-validated capability is independent of a fixed Start offset.
        g.start_left=Some(100);g.start_right=Some(140);g.buttons_right=Some(210);
        g.obstacle_intervals=vec![(0,10),(2460,2560)];
        assert!(paired_plan(&groups(1),&g,None,150).is_some());
        assert_eq!(TaskbarGeom::default().centered,None);
    }
    #[test] fn compaction_preserves_existing_order_and_cap_policy(){
        for portrait in [false,true]{for n in 1..40{for cap in [None,Some(0),Some(7),Some(30)]{
            let g=geometry(portrait);let p=paired_plan(&groups(n),&g,cap,150).unwrap();check(&g,&p);
            let exes:Vec<_>=p.left.items.iter().chain(&p.right.items).flat_map(|i|i.exes.iter().cloned()).collect();
            assert_eq!(exes,groups(n)[0].1);assert!(p.left.items.iter().chain(&p.right.items).filter(|i|!i.folder).count()<=cap.unwrap_or(1).max(1));
        }}}
    }
    #[test] fn obstacles_in_native_or_unsafe_narrow_flanks_reject(){
        let mut g=geometry(true);g.obstacle_intervals.push((-700,-680));assert!(paired_plan(&groups(5),&g,None,150).is_none());
        let mut g=geometry(true);g.obstacle_intervals.push((-800,-778));assert!(paired_plan(&groups(5),&g,None,150).is_none());
    }

    #[test]
    fn reflow_shrunk_native_keeps_compacted_partition_instead_of_expanding() {
        let mut g = geometry(false);
        g.obstacle_intervals = vec![(0, 950), (1611, 2560)];
        let old = paired_plan(&groups(10), &g, Some(7), 150).unwrap();
        g.start_left = Some(1206);
        g.start_right = Some(1246);
        g.buttons_right = Some(1355);
        g.measured_at = Some(200);
        let fresh = paired_plan(&groups(10), &g, Some(7), 200).unwrap();
        assert_ne!(fresh.left.items.len() + fresh.right.items.len(),
            old.left.items.len() + old.right.items.len());
        let moved = reflow_pair(&old, &g, 200).unwrap();
        check(&g, &moved);
        assert_eq!(moved.left, old.left);
        assert_eq!(moved.right, old.right);
        assert_eq!(moved.generation, old.generation);
        assert_eq!(moved.left_span.x, old.left_span.x + 80);
        assert_eq!(moved.right_span.x, old.right_span.x - 80);
        assert_eq!(reflow_pair(&moved, &g, 200), Some(moved));
    }

    #[test]
    fn reflow_recenter_has_exact_symmetric_spans_and_right_only_menu() {
        let mut g = geometry(false);
        let old = paired_plan(&groups(3), &g, None, 150).unwrap();
        g.start_left = Some(1200);
        g.start_right = Some(1240);
        g.buttons_right = Some(1360);
        let moved = reflow_pair(&old, &g, 150).unwrap();
        assert_eq!(moved.left_span, Span { x: 1146, limit: 1198 });
        assert_eq!(moved.right_span, Span { x: 1362, limit: 1414 });
        assert_eq!(moved.left.width, 52.0);
        assert_eq!(moved.right.width, 48.0);
        assert_eq!(moved.left, old.left);
        assert_eq!(moved.right, old.right);
        assert!(moved.right.items.is_empty());
        assert_eq!(moved.generation, old.generation);
    }

    #[test]
    fn reflow_negative_portrait_rounds_each_item_and_padding_at_new_dpi() {
        let mut g = geometry(true);
        let old = paired_plan(&groups(3), &g, None, 150).unwrap();
        g.start_left = Some(-751);
        g.start_right = Some(-711);
        g.buttons_right = Some(-690);
        g.scale = 1.3;
        let moved = reflow_pair(&old, &g, 150).unwrap();
        // ceil(44 * 1.3) + ceil(8 * 1.3) = 58 + 11 = 69.
        assert_eq!(moved.left_span, Span { x: -823, limit: -754 });
        assert_eq!(moved.right_span, Span { x: -687, limit: -618 });
        check(&g, &moved);
        assert_eq!(moved.left, old.left);
        assert_eq!(moved.right, old.right);
        assert_eq!(moved.generation, old.generation);
    }

    #[test]
    fn reflow_requires_fresh_complete_explicitly_centered_geometry() {
        let good = geometry(false);
        let old = paired_plan(&groups(10), &good, Some(7), 150).unwrap();
        assert!(reflow_pair(&old, &good, 1100).is_some());
        assert!(reflow_pair(&old, &good, 1101).is_none());
        for field in 0..8 {
            let mut g = good.clone();
            match field {
                0 => g.measured_at = None,
                1 => g.measured_at = Some(151),
                2 => g.left_obstacles_complete = false,
                3 => g.centered = None,
                4 => g.centered = Some(false),
                5 => g.start_left = None,
                6 => g.start_right = None,
                _ => g.buttons_right = None,
            }
            assert!(reflow_pair(&old, &g, 150).is_none(), "field {field}");
        }
    }

    #[test]
    fn reflow_rejects_bad_bounds_scale_and_obstacles_without_recompacting() {
        let good = geometry(false);
        let old = paired_plan(&groups(10), &good, Some(7), 150).unwrap();
        for scale in [0.0, -1.0, f64::NAN, f64::INFINITY, f64::MAX] {
            let mut g = good.clone();
            g.scale = scale;
            assert!(reflow_pair(&old, &g, 150).is_none());
        }
        for field in 0..8 {
            let mut g = good.clone();
            match field {
                0 => g.left = g.right,
                1 => g.start_left = Some(-1),
                2 => g.start_right = g.start_left,
                3 => g.buttons_right = Some(2561),
                4 => g.obstacle_intervals.push((1200, 1210)),
                5 => g.obstacle_intervals.push((-1, 10)),
                6 => g.obstacle_intervals.push((900, 800)),
                _ => g.obstacle_intervals.push((1100, 1124)),
            }
            assert!(reflow_pair(&old, &g, 150).is_none(), "field {field}");
        }
        let mut g = good.clone();
        g.obstacles.push(1438);
        assert!(reflow_pair(&old, &g, 150).is_none());
        g.obstacles = vec![old.right_span.limit];
        assert!(reflow_pair(&old, &g, 150).is_some());
        g.obstacles[0] -= 1;
        assert!(reflow_pair(&old, &g, 150).is_none());
        assert_eq!(reflow_pair(&old, &good, 150), Some(old.clone()));
    }

    #[test]
    fn reflow_does_not_rebalance_a_frozen_asymmetric_item_partition() {
        let g = geometry(false);
        let mut old = paired_plan(&groups(10), &g, Some(7), 150).unwrap();
        let item = old.left.items.pop().unwrap();
        old.right.items.insert(0, item);
        old.left.width -= APP_W;
        old.right.width += APP_W;
        old.generation = paired_generation(&old.left, &old.right);
        let moved = reflow_pair(&old, &g, 150).unwrap();
        check(&g, &moved);
        assert_eq!(moved.left, old.left);
        assert_eq!(moved.right, old.right);
        assert_eq!(moved.generation, old.generation);
    }

    #[test]
    fn candidate_signature_tracks_exact_ordered_groups_and_cap() {
        let source = vec![("Apps".into(), vec!["a".into(), "bc".into()]),
            ("Tools".into(), vec!["d".into()])];
        let sig = paired_candidate_signature(&source, Some(7));
        assert_ne!(sig, 0);
        assert_eq!(sig, paired_candidate_signature(&source.clone(), Some(7)));
        for cap in [None, Some(0), Some(6), Some(8)] {
            assert_ne!(sig, paired_candidate_signature(&source, cap));
        }
        for field in 0..5 {
            let mut changed = source.clone();
            match field {
                0 => changed.reverse(),
                1 => changed[0].1.reverse(),
                2 => changed[0].0 = "Renamed".into(),
                3 => changed[0].1 = vec!["ab".into(), "c".into()],
                _ => changed[1].1.push("extra".into()),
            }
            assert_ne!(sig, paired_candidate_signature(&changed, Some(7)), "field {field}");
        }
        assert_ne!(paired_candidate_signature(&source, None),
            paired_candidate_signature(&source, Some(0)));
    }
}
