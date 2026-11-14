//! Read-only geometry adapter. Unknown providers/orientations return no usable
//! layout rather than guessing where native buttons or system controls are.
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
    let condition=uia.CreateOrCondition(&buttons,&edits)?;
    let elements=root.FindAllBuildCache(TreeScope_Descendants,&condition,&cache)?;
    let length=elements.Length()?;
    if length<=0 || length>1024 {return Err(unsupported());}
    let mut controls=vec![];
    let (mut cluster_left,mut cluster_right)=(start.left,start.right);
    for index in 0..length {
        let element=elements.GetElement(index)?;
        let rect=element.CachedBoundingRectangle()?;
        if !inside(&rect) || obstacles.iter().any(|o|overlaps(o,&rect)) {continue;}
        let class=element.CachedClassName().unwrap_or_default().to_string();
        let id=element.CachedAutomationId().unwrap_or_default().to_string();
        if class=="Taskbar.TaskListButtonAutomationPeer" || id.to_ascii_lowercase().starts_with("appid:") {
            cluster_left=cluster_left.min(rect.left);cluster_right=cluster_right.max(rect.right);
        }
        controls.push(rect);
    }
    // Connectivity derives from measured control size, not a device-specific x offset.
    let gap=(start.right-start.left)/2;
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
    let native_center=(f64::from(cluster_left)+f64::from(cluster_right))/2.0;
    let bar_center=(f64::from(bounds.left)+f64::from(bounds.right))/2.0;
    Ok(crate::dock_layout::TaskbarGeom {
        left:bounds.left,right:bounds.right,start_left:Some(cluster_left),start_right:Some(start.right),
        buttons_right:Some(cluster_right),obstacles:edges,obstacle_intervals:intervals,scale,
        measured_at:Some(now_ms),left_obstacles_complete:true,
        // One physical pixel only accounts for integer rectangle quantization.
        centered:Some((native_center-bar_center).abs()<=1.0),
        icon_size_css:Some(crate::icon_geometry::frame_css(bounds.bottom-bounds.top,scale)),
    })
}
