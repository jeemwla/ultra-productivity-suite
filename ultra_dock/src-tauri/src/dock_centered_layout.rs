//! Standalone design prototype, intentionally not wired into the application.
//! Coordinates are physical pixels; item widths are CSS pixels, rounded up per item.
//! Rectangles are horizontal half-open intervals within the taskbar row.
//! Equal allocation includes blank OUTER padding, not equal visible artwork mass.
//! Caller includes the menu as an ordinary item and supplies a coherent snapshot.

use std::collections::HashSet;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect { pub left: i32, pub right: i32 }
impl Rect {
    pub fn width(self) -> i64 { i64::from(self.right) - i64::from(self.left) }
    fn overlaps(self, other: Self) -> bool { self.left < other.right && other.left < self.right }
}
#[derive(Clone, Debug)]
pub struct Item { pub id: String, pub width_css: f64 }
#[derive(Clone, Debug)]
pub struct Input {
    pub taskbar: Rect,
    /// Complete protected native cluster, including Start and native controls.
    pub native: Rect,
    pub obstacles: Vec<Rect>,
    pub scale: f64,
    pub items: Vec<Item>,
    /// Physical coordinate. Equal flank allocation can only retain native center.
    pub requested_center: f64,
    pub gap_css: f64,
    pub generation: u64,
    pub expected_generation: u64,
    pub measured_at_ms: u64,
    pub now_ms: u64,
    pub max_age_ms: u64,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PlacedItem { pub id: String, pub rect: Rect }
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Flank {
    pub rect: Rect,
    pub items: Vec<PlacedItem>,
    /// Blank space at the outer edge, away from the native cluster.
    pub outer_padding: i64,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Plan { pub left: Flank, pub right: Flank, pub generation: u64 }
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Infeasible {
    InvalidGeometry, InvalidScale, InvalidGap, InvalidItems, StaleSnapshot,
    CenterRequiresMovingNative, NoSymmetricSpace,
}

/// Tries every contiguous split, preserving order left-to-right across the native row.
/// Chooses minimum symmetric width, then minimum padding imbalance, then earliest split.
/// No projection of future native-button removal or obstacle motion is permitted.
pub fn plan(input: &Input) -> Result<Plan, Infeasible> {
    plan_reserved(input, 0.0, 0, input.items.len())
}

/// Constrained split for paired runtime planning: reserve outer padding on BOTH
/// sides and restrict the split so the final menu item stays on the right.
pub fn plan_reserved(input: &Input, reserve_css: f64, min_left: usize, max_left: usize) -> Result<Plan, Infeasible> {
    let i = input;
    if !reserve_css.is_finite() || reserve_css < 0.0 || min_left > max_left || max_left > i.items.len() {
        return Err(Infeasible::InvalidItems);
    }
    if i.taskbar.width() <= 0 || i.native.width() <= 0
        || i.native.left < i.taskbar.left || i.native.right > i.taskbar.right
        || i.obstacles.iter().any(|r| r.width() <= 0 || r.left < i.taskbar.left
            || r.right > i.taskbar.right || r.overlaps(i.native)) {
        return Err(Infeasible::InvalidGeometry);
    }
    if !i.scale.is_finite() || i.scale <= 0.0 { return Err(Infeasible::InvalidScale); }
    if !i.gap_css.is_finite() || i.gap_css < 0.0 { return Err(Infeasible::InvalidGap); }
    if i.generation != i.expected_generation || i.measured_at_ms > i.now_ms
        || i.now_ms - i.measured_at_ms > i.max_age_ms {
        return Err(Infeasible::StaleSnapshot);
    }
    let center = (f64::from(i.native.left) + f64::from(i.native.right)) / 2.0;
    if !i.requested_center.is_finite() || (i.requested_center - center).abs() > 1e-6 {
        return Err(Infeasible::CenterRequiresMovingNative);
    }
    let gap = (i.gap_css * i.scale).ceil();
    if !gap.is_finite() || gap > i.taskbar.width() as f64 { return Err(Infeasible::InvalidGap); }
    let gap = gap as i64;
    let reserve = (reserve_css * i.scale).ceil();
    if !reserve.is_finite() || reserve > i.taskbar.width() as f64 { return Err(Infeasible::NoSymmetricSpace); }
    let reserve = reserve as i64;
    let mut ids = HashSet::new();
    let mut widths = Vec::new();
    let mut total = 0i64;
    if i.items.is_empty() { return Err(Infeasible::InvalidItems); }
    for item in &i.items {
        let w = (item.width_css * i.scale).ceil();
        if item.id.is_empty() || !ids.insert(&item.id) || !item.width_css.is_finite()
            || item.width_css <= 0.0 || !w.is_finite() || w > i.taskbar.width() as f64 {
            return Err(Infeasible::InvalidItems);
        }
        let w = w as i64;
        total = total.checked_add(w).ok_or(Infeasible::InvalidItems)?;
        widths.push(w);
    }
    let left_limit = i.obstacles.iter().filter(|r| r.right <= i.native.left)
        .map(|r| r.right).max().unwrap_or(i.taskbar.left);
    let right_limit = i.obstacles.iter().filter(|r| r.left >= i.native.right)
        .map(|r| r.left).min().unwrap_or(i.taskbar.right);
    let left_end = i64::from(i.native.left) - gap;
    let right_start = i64::from(i.native.right) + gap;
    let capacity = (left_end - i64::from(left_limit))
        .min(i64::from(right_limit) - right_start);
    let mut best: Option<(i64, i64, usize, i64, i64)> = None;
    let mut left_width = 0;
    for split in 0..=widths.len() {
        let right_width = total - left_width;
        let extent = left_width.max(right_width).checked_add(reserve).ok_or(Infeasible::InvalidItems)?;
        let candidate = (extent, (left_width - right_width).abs(), split, left_width, right_width);
        if split >= min_left && split <= max_left && extent <= capacity && best.as_ref().is_none_or(|b| candidate < *b) { best = Some(candidate); }
        if split < widths.len() { left_width += widths[split]; }
    }
    let Some((extent, _, split, lw, rw)) = best else { return Err(Infeasible::NoSymmetricSpace); };
    // Feasibility establishes that these endpoints are inside validated i32 bounds.
    let left_rect = Rect { left: (left_end - extent) as i32, right: left_end as i32 };
    let right_rect = Rect { left: right_start as i32, right: (right_start + extent) as i32 };
    let place = |start: i64, range: std::ops::Range<usize>| {
        let mut x = start;
        range.map(|n| {
            let rect = Rect { left: x as i32, right: (x + widths[n]) as i32 };
            x += widths[n];
            PlacedItem { id: i.items[n].id.clone(), rect }
        }).collect()
    };
    Ok(Plan {
        left: Flank { rect: left_rect, items: place(left_end - lw, 0..split), outer_padding: extent - lw },
        right: Flank { rect: right_rect, items: place(right_start, split..widths.len()), outer_padding: extent - rw },
        generation: i.generation,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn input(widths: &[f64]) -> Input {
        Input { taskbar: Rect {left:0,right:1000}, native:Rect {left:400,right:600},
            obstacles:vec![Rect {left:0,right:100},Rect {left:900,right:1000}], scale:1.0,
            items:widths.iter().enumerate().map(|(n,w)|Item{id:format!("item-{n}"),width_css:*w}).collect(),
            requested_center:500.0,gap_css:2.0,generation:7,expected_generation:7,
            measured_at_ms:100,now_ms:150,max_age_ms:100 }
    }
    fn verify(i: &Input, p: &Plan) {
        assert_eq!(p.left.rect.width(),p.right.rect.width());
        assert_eq!(i64::from(p.left.rect.left)+i64::from(p.right.rect.right),
            i64::from(i.native.left)+i64::from(i.native.right));
        let actual:Vec<_>=p.left.items.iter().chain(&p.right.items).map(|v|v.id.as_str()).collect();
        assert_eq!(actual,i.items.iter().map(|v|v.id.as_str()).collect::<Vec<_>>());
        for f in [&p.left,&p.right] {
            assert!(f.rect.left>=i.taskbar.left && f.rect.right<=i.taskbar.right);
            assert!(!f.rect.overlaps(i.native));
            assert!(i.obstacles.iter().all(|o|!o.overlaps(f.rect)));
            assert_eq!(f.items.iter().map(|v|v.rect.width()).sum::<i64>()+f.outer_padding,f.rect.width());
            for (v, original) in f.items.iter().zip(i.items.iter().filter(|v|f.items.iter().any(|x|x.id==v.id))) {
                assert_eq!(v.rect.width(),(original.width_css*i.scale).ceil() as i64);
                assert!(v.rect.left>=f.rect.left && v.rect.right<=f.rect.right);
            }
            assert!(f.items.windows(2).all(|a|a[0].rect.right==a[1].rect.left));
        }
    }
    #[test] fn balanced_whole_items_and_menu_order() {
        let mut i=input(&[40.,44.,40.]);i.items[2].id="menu".into();
        let p=plan(&i).unwrap();verify(&i,&p);assert_eq!(p.left.items.len(),1);
        assert_eq!(p.left.outer_padding,44);assert_eq!(p.right.items.last().unwrap().id,"menu");
    }
    #[test] fn widgets_and_tray_limit_both_flanks() {
        let mut i=input(&[100.,100.]);i.obstacles.push(Rect{left:200,right:350});
        assert_eq!(plan(&i),Err(Infeasible::NoSymmetricSpace));
        i.obstacles.pop();i.obstacles.push(Rect{left:650,right:700});
        assert_eq!(plan(&i),Err(Infeasible::NoSymmetricSpace));
    }
    #[test] fn negative_origin_and_fractional_dpi() {
        let mut i=input(&[31.5,40.,17.]);i.scale=1.25;
        i.taskbar=Rect{left:-1000,right:0};i.native=Rect{left:-600,right:-400};
        i.obstacles=vec![Rect{left:-1000,right:-900},Rect{left:-100,right:0}];i.requested_center=-500.;
        verify(&i,&plan(&i).unwrap());
    }
    #[test] fn portrait_insufficient_space_is_explicit() {
        let mut i=input(&[100.,100.]);i.taskbar=Rect{left:0,right:300};
        i.native=Rect{left:100,right:200};i.requested_center=150.;i.obstacles.clear();
        assert_eq!(plan(&i),Err(Infeasible::NoSymmetricSpace));
    }
    #[test] fn cannot_recenter_native_row_without_moving_it() {
        let mut i=input(&[40.,40.]);i.requested_center=510.;
        assert_eq!(plan(&i),Err(Infeasible::CenterRequiresMovingNative));
    }
    #[test] fn stale_future_and_wrong_generation_rejected() {
        let mut i=input(&[40.]);i.now_ms=201;assert_eq!(plan(&i),Err(Infeasible::StaleSnapshot));
        i.now_ms=99;assert_eq!(plan(&i),Err(Infeasible::StaleSnapshot));
        i.now_ms=150;i.expected_generation=8;assert_eq!(plan(&i),Err(Infeasible::StaleSnapshot));
    }
    #[test] fn invalid_geometry_and_obstacles_rejected() {
        let mut i=input(&[40.]);i.native.left=-1;assert_eq!(plan(&i),Err(Infeasible::InvalidGeometry));
        let mut i=input(&[40.]);i.obstacles.push(Rect{left:450,right:460});assert_eq!(plan(&i),Err(Infeasible::InvalidGeometry));
        let mut i=input(&[40.]);i.obstacles.push(Rect{left:100,right:50});assert_eq!(plan(&i),Err(Infeasible::InvalidGeometry));
    }
    #[test] fn bad_numeric_inputs_and_ids_rejected() {
        for scale in [0.,-1.,f64::NAN,f64::INFINITY] {let mut i=input(&[40.]);i.scale=scale;assert_eq!(plan(&i),Err(Infeasible::InvalidScale));}
        for width in [0.,-1.,f64::NAN,f64::INFINITY] {let i=input(&[width]);assert_eq!(plan(&i),Err(Infeasible::InvalidItems));}
        let mut i=input(&[40.,40.]);i.items[1].id=i.items[0].id.clone();assert_eq!(plan(&i),Err(Infeasible::InvalidItems));
        let mut i=input(&[40.]);i.gap_css=-1.;assert_eq!(plan(&i),Err(Infeasible::InvalidGap));
        assert_eq!(plan(&input(&[])),Err(Infeasible::InvalidItems));
    }
    #[test] fn single_item_keeps_blank_symmetric_flank() {let i=input(&[40.]);let p=plan(&i).unwrap();verify(&i,&p);assert!(p.left.items.is_empty());assert_eq!(p.left.outer_padding,40);}
    #[test] fn extreme_i32_bounds_do_not_overflow() {
        let mut i=input(&[40.,44.]);i.taskbar=Rect{left:i32::MIN,right:i32::MAX};
        i.native=Rect{left:-100,right:100};i.obstacles.clear();i.requested_center=0.;verify(&i,&plan(&i).unwrap());
    }
    #[test] fn exact_capacity_and_touching_obstacles_are_safe() {
        let mut i=input(&[40.,40.]);i.obstacles=vec![Rect{left:0,right:358},Rect{left:642,right:1000}];
        verify(&i,&plan(&i).unwrap());i.obstacles[0].right+=1;assert_eq!(plan(&i),Err(Infeasible::NoSymmetricSpace));
    }
    #[test] fn deterministic_sweep_checks_safety_and_optimal_contiguous_split() {
        for n in 1..=8 {for seed in 0..40 {for scale in [1.,1.25,1.5,2.] {
            let widths:Vec<_>=(0..n).map(|k|20.+((seed*13+k*17)%50) as f64).collect();
            let mut i=input(&widths);i.scale=scale;
            let gap=(i.gap_css*scale).ceil() as i64;let capacity=300-gap;
            let px:Vec<_>=widths.iter().map(|w|(w*scale).ceil() as i64).collect();
            let optimum=(0..=n).map(|k|px[..k].iter().sum::<i64>().max(px[k..].iter().sum::<i64>())).min().unwrap();
            match plan(&i) {Ok(p)=>{verify(&i,&p);assert_eq!(p.left.rect.width(),optimum);assert!(optimum<=capacity);},Err(e)=>{assert_eq!(e,Infeasible::NoSymmetricSpace);assert!(optimum>capacity);}}
        }}}
    }
}

#[cfg(test)]
mod reserved_tests {
    use super::*;
    fn input()->Input{Input{
        taskbar:Rect{left:-1440,right:0},native:Rect{left:-776,right:-665},
        obstacles:vec![Rect{left:-1440,right:-1340},Rect{left:-100,right:0}],scale:1.25,
        items:vec![Item{id:"app".into(),width_css:40.},Item{id:"menu".into(),width_css:40.}],
        requested_center:-720.5,gap_css:2.,generation:1,expected_generation:1,
        measured_at_ms:100,now_ms:150,max_age_ms:1000}}
    #[test]fn reserve_padding_keeps_menu_on_right_and_equal_physical_spans(){
        let i=input();let p=plan_reserved(&i,8.,1,1).unwrap();
        assert_eq!(p.left.rect.width(),60);assert_eq!(p.right.rect.width(),60);
        assert_eq!(p.left.outer_padding,10);assert_eq!(p.right.outer_padding,10);
        assert_eq!(p.left.items[0].id,"app");assert_eq!(p.right.items[0].id,"menu");
        assert_eq!(p.left.rect.right,-779);assert_eq!(p.right.rect.left,-662);
        assert_eq!(i64::from(p.left.rect.left)+i64::from(p.right.rect.right),-1441);
    }
    #[test]fn split_constraints_and_invalid_reserve_fail_explicitly(){
        let i=input();assert_eq!(plan_reserved(&i,8.,2,1),Err(Infeasible::InvalidItems));
        assert_eq!(plan_reserved(&i,f64::NAN,1,1),Err(Infeasible::InvalidItems));
        assert_eq!(plan_reserved(&i,1000.,1,1),Err(Infeasible::NoSymmetricSpace));
    }
}
