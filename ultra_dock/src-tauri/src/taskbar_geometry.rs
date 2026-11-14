//! Read-only geometry adapter. Unknown providers/orientations return no usable
//! layout rather than guessing where native buttons or system controls are.
fn coherent_cluster(legacy:(i32,i32), sampled:Option<(i32,i32)>, buttons:&[(i32,i32)])->(i32,i32) {
    buttons.iter().fold(sampled.unwrap_or(legacy),|(left,right),&(a,b)|(left.min(a),right.max(b)))
}
fn centered_capability(setting:Option<u32>)->Option<bool> {
    match setting {Some(1)=>Some(true),Some(0)=>Some(false),_=>None}
}
#[cfg(windows)]
unsafe fn taskbar_alignment()->Option<bool> {
    use windows::{core::w,Win32::System::Registry::*};
    let mut value=0u32;let mut length=std::mem::size_of::<u32>() as u32;
    let result=RegGetValueW(HKEY_CURRENT_USER,w!("Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced"),
        w!("TaskbarAl"),RRF_RT_REG_DWORD,None,Some((&mut value as *mut u32).cast()),Some(&mut length));
    centered_capability((result.is_ok() && length==4).then_some(value))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn start_and_app_buttons_must_use_the_same_animation_snapshot() {
        assert_eq!(coherent_cluster((972,1017),Some((1126,1171)),&[(1171,1215),(1215,1434)]),(1126,1434));
        assert_eq!(coherent_cluster((-830,-774),Some((-776,-720)),&[(-720,-665)]),(-776,-665));
        assert_eq!(coherent_cluster((0,40),None,&[(40,80)]),(0,80));
    }
    #[test]
    fn alignment_setting_is_a_capability_and_does_not_change_during_animation() {
        assert_eq!(centered_capability(Some(1)),Some(true));
        assert_eq!(centered_capability(Some(0)),Some(false));
        assert_eq!(centered_capability(None),None);
        assert_eq!(centered_capability(Some(2)),None);
    }
}
#[cfg(windows)]
pub unsafe fn measure(
    uia: &windows::Win32::UI::Accessibility::IUIAutomation,
    taskbar: windows::Win32::Foundation::HWND,
    bounds: windows::Win32::Foundation::RECT,
    scale: f64,
    now_ms: u64,
) -> windows::core::Result<crate::dock_layout::TaskbarGeom> {
    use windows::{core::w,Win32::{Foundation::RECT,System::Variant::VARIANT,
        UI::{Accessibility::*,WindowsAndMessaging::{FindWindowExW,GetWindowRect}}}};
    let unsupported = || windows::core::Error::from_hresult(windows::core::HRESULT(0x80004001u32 as i32));
    if !scale.is_finite() || scale<=0.0 || bounds.right<=bounds.left
        || bounds.bottom<=bounds.top || bounds.right-bounds.left<=bounds.bottom-bounds.top {return Err(unsupported());}
    let inside = |r:&RECT| r.right>r.left && r.bottom>r.top && r.left>=bounds.left && r.right<=bounds.right
        && r.top>=bounds.top && r.bottom<=bounds.bottom;
    let overlaps = |a:&RECT,b:&RECT| a.left<b.right && a.right>b.left;
    let root=uia.ElementFromHandle(taskbar)?;
    let cache=uia.CreateCacheRequest()?;
    cache.AddProperty(UIA_BoundingRectanglePropertyId)?;
    cache.AddProperty(UIA_AutomationIdPropertyId)?;
    cache.AddProperty(UIA_ClassNamePropertyId)?;
    cache.SetTreeScope(TreeScope_Element)?;
    let start_condition=uia.CreatePropertyCondition(UIA_AutomationIdPropertyId,&VARIANT::from("StartButton"))?;
    let mut start=root.FindFirstBuildCache(TreeScope_Descendants,&start_condition,&cache)
        .and_then(|e|e.CachedBoundingRectangle()).ok().filter(&inside);
    if start.is_none() {
        if let Ok(handle)=FindWindowExW(Some(taskbar),None,w!("Start"),None) {
            let mut rect=RECT::default();
            if GetWindowRect(handle,&mut rect).is_ok() && inside(&rect) {start=Some(rect);}
        }
    }
    let start=start.ok_or_else(unsupported)?;
    let mut obstacles=Vec::<RECT>::new();
    for id in ["SystemTrayIcon","NotifyItemIcon","WidgetsButton"] {
        let condition=uia.CreatePropertyCondition(UIA_AutomationIdPropertyId,&VARIANT::from(id))?;
        if let Ok(rect)=root.FindFirstBuildCache(TreeScope_Descendants,&condition,&cache).and_then(|e|e.CachedBoundingRectangle()) {
            if inside(&rect) && !overlaps(&rect,&start) {obstacles.push(rect);}
        }
    }
    if let Ok(tray)=FindWindowExW(Some(taskbar),None,w!("TrayNotifyWnd"),None) {
        let mut rect=RECT::default();
        if GetWindowRect(tray,&mut rect).is_ok() && inside(&rect) {obstacles.push(rect);}
    }
    let buttons=uia.CreatePropertyCondition(UIA_ControlTypePropertyId,&VARIANT::from(UIA_ButtonControlTypeId.0))?;
    let edits=uia.CreatePropertyCondition(UIA_ControlTypePropertyId,&VARIANT::from(UIA_EditControlTypeId.0))?;
    let button_or_edit=uia.CreateOrCondition(&buttons,&edits)?;
    // Start's provider control type varies across Windows versions. Include its
    // identity explicitly so it always comes from this same cached enumeration.
    let condition=uia.CreateOrCondition(&button_or_edit,&start_condition)?;
    let elements=root.FindAllBuildCache(TreeScope_Descendants,&condition,&cache)?;
    let length=elements.Length()?;
    if length<=0 || length>1024 {return Err(unsupported());}
    let mut controls=vec![];
    let mut sampled_start=None;
    let mut native_buttons=vec![];
    for index in 0..length {
        let element=elements.GetElement(index)?;
        let rect=element.CachedBoundingRectangle()?;
        if !inside(&rect) || obstacles.iter().any(|o|overlaps(o,&rect)) {continue;}
        let class=element.CachedClassName().unwrap_or_default().to_string();
        let id=element.CachedAutomationId().unwrap_or_default().to_string();
        if id=="StartButton" {sampled_start=Some((rect.left,rect.right));}
        if class=="Taskbar.TaskListButtonAutomationPeer" || id.to_ascii_lowercase().starts_with("appid:") {
            native_buttons.push((rect.left,rect.right));
        }
        controls.push(rect);
    }
    // Windows animates Start and app buttons together. The separately queried
    // legacy Start fallback must not seed a newer cached button snapshot.
    let (mut cluster_left,mut cluster_right)=coherent_cluster((start.left,start.right),sampled_start,&native_buttons);
    // Connectivity derives from measured control size, not a device-specific x offset.
    let current_start=sampled_start.unwrap_or((start.left,start.right));
    let gap=(current_start.1-current_start.0)/2;
    loop {
        let before=(cluster_left,cluster_right);
        for rect in &controls {
            if i64::from(rect.left)<=i64::from(cluster_right)+i64::from(gap)
                && i64::from(rect.right)>=i64::from(cluster_left)-i64::from(gap) {
                cluster_left=cluster_left.min(rect.left);cluster_right=cluster_right.max(rect.right);
            }
        }
        if before==(cluster_left,cluster_right) {break;}
    }
    for rect in controls {
        if rect.right<=cluster_left || rect.left>=cluster_right {obstacles.push(rect);}
    }
    let intervals:Vec<_>=obstacles.iter().map(|r|(r.left,r.right)).collect();
    let edges=intervals.iter().filter(|(left,_)|*left>=cluster_right).map(|(left,_)|*left).collect();
    Ok(crate::dock_layout::TaskbarGeom {
        left:bounds.left,right:bounds.right,start_left:Some(cluster_left),start_right:Some(current_start.1),
        buttons_right:Some(cluster_right),obstacles:edges,obstacle_intervals:intervals,scale,
        measured_at:Some(now_ms),left_obstacles_complete:true,
        // Alignment is an OS setting, not an inference from animated positions.
        // Unknown/legacy settings fail closed; physical bounds still gate fit.
        centered:taskbar_alignment(),
        icon_size_css:Some(crate::icon_geometry::frame_css(bounds.bottom-bounds.top,scale)),
    })
}
