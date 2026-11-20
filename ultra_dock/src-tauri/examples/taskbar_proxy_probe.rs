//! Disposable manual Shell-layout probe, independent of the Dock runtime.
//! Default: unowned TOOLWINDOW + AddTab. --noactivate adds NOACTIVATE;
//! --appwindow replaces TOOLWINDOW with APPWINDOW as a positive control.
//! Observe only these windows. Do not manipulate user apps or their buttons.
//! Normal exits unregister the proxy before destroying either top-level window.
//! Forced process termination cannot run cleanup. No hooks or persistent changes.

#[cfg(not(windows))]
fn main() {
    eprintln!("taskbar_proxy_probe requires Windows");
}

#[cfg(windows)]
fn main() -> windows::core::Result<()> {
    probe::run()
}

#[cfg(windows)]
mod probe {
    use std::{cell::Cell, io::Write, time::{Duration, Instant}};
    use windows::{
        core::{w, Error, GUID, PCWSTR, Result},
        Win32::{
            Foundation::{HWND, LPARAM, LRESULT, PROPERTYKEY, WAIT_FAILED, WPARAM},
            Graphics::Gdi::{COLOR_WINDOW, HBRUSH},
            System::{Com::{*, StructuredStorage::PROPVARIANT}, LibraryLoader::GetModuleHandleW, Variant::VT_LPWSTR},
            UI::{Shell::{ITaskbarList, TaskbarList, SHStrDupW,
                PropertiesSystem::{IPropertyStore, SHGetPropertyStoreForWindow}}, WindowsAndMessaging::*},
        },
    };

    const LIFETIME: Duration = Duration::from_secs(85);
    const APP_ID: PROPERTYKEY = PROPERTYKEY {
        fmtid: GUID::from_u128(0x9f4c2855_9f79_4b39_a8d0_e1d42de1d5f3), pid: 5,
    };
    const PREVENT_PINNING: PROPERTYKEY = PROPERTYKEY { fmtid: APP_ID.fmtid, pid: 9 };
    thread_local! {
        static EXIT: Cell<bool> = const { Cell::new(false) };
        static PROXY: Cell<HWND> = const { Cell::new(HWND(std::ptr::null_mut())) };
        static BUTTON_MESSAGE: Cell<u32> = const { Cell::new(0) };
        static SHELL_MESSAGE: Cell<u32> = const { Cell::new(0) };
        static BUTTON_SEEN: Cell<bool> = const { Cell::new(false) };
        static REGISTER: Cell<bool> = const { Cell::new(false) };
        static RECONNECT: Cell<bool> = const { Cell::new(false) };
    }

    // Ignore closed stdout rather than panicking across a Win32 callback.
    fn log(args: std::fmt::Arguments<'_>) {
        let _ = writeln!(std::io::stdout().lock(), "{args}");
    }

    fn result_log(label: &str, hwnd: HWND, result: &Result<()>) {
        match result {
            Ok(()) => log(format_args!("{label} HWND={hwnd:?} HRESULT=0x00000000")),
            Err(e) => log(format_args!("{label} HWND={hwnd:?} HRESULT=0x{:08X}", e.code().0 as u32)),
        }
    }

    unsafe extern "system" fn window_proc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
        if msg != 0 && msg == SHELL_MESSAGE.get() {
            log(format_args!("TaskbarCreated HWND={hwnd:?}"));
            BUTTON_SEEN.set(false);
            RECONNECT.set(true);
            REGISTER.set(true);
        } else if msg != 0 && msg == BUTTON_MESSAGE.get() {
            log(format_args!("TaskbarButtonCreated HWND={hwnd:?}"));
            // AddTab can itself cause this notification. Retry only once per
            // Shell generation, avoiding a registration/notification loop.
            if hwnd == PROXY.get() && !BUTTON_SEEN.replace(true) {
                REGISTER.set(true);
            }
        }
        match msg {
            WM_ACTIVATE | WM_LBUTTONUP | WM_CLOSE => {
                let name = match msg {
                    WM_ACTIVATE => "WM_ACTIVATE",
                    WM_LBUTTONUP => "WM_LBUTTONUP",
                    _ => "WM_CLOSE",
                };
                log(format_args!("{name} HWND={hwnd:?} wParam=0x{:X} lParam=0x{:X}", wp.0, lp.0));
                if msg == WM_CLOSE {
                    // Defer destruction to the single DeleteTab-first path.
                    EXIT.set(true);
                    return LRESULT(0);
                }
            }
            WM_DESTROY => EXIT.set(true),
            _ => {}
        }
        DefWindowProcW(hwnd, msg, wp, lp)
    }

    struct Apartment;
    impl Drop for Apartment {
        fn drop(&mut self) { unsafe { CoUninitialize(); } }
    }

    #[derive(Default)]
    struct Windows {
        control: Option<HWND>,
        proxy: Option<HWND>,
        taskbar: Option<ITaskbarList>,
    }

    unsafe fn connect() -> Result<ITaskbarList> {
        let taskbar: ITaskbarList = CoCreateInstance(&TaskbarList, None, CLSCTX_INPROC_SERVER)?;
        taskbar.HrInit()?;
        Ok(taskbar)
    }

    unsafe fn set_identity(hwnd: HWND, id: PCWSTR) -> Result<()> {
        let store: IPropertyStore = SHGetPropertyStoreForWindow(hwnd)?;
        // Set before the ID, and only on our own ephemeral windows.
        store.SetValue(&PREVENT_PINNING, &PROPVARIANT::from(true))?;
        let mut value = PROPVARIANT::default();
        (*value.Anonymous.Anonymous).vt = VT_LPWSTR;
        (*value.Anonymous.Anonymous).Anonymous.pwszVal = SHStrDupW(id)?;
        store.SetValue(&APP_ID, &value)?;
        store.Commit()
    }

    unsafe fn clear_identity(hwnd: HWND) {
        if let Ok(store) = SHGetPropertyStoreForWindow::<IPropertyStore>(hwnd) {
            let empty = PROPVARIANT::default();
            result_log("clear AppUserModel.ID", hwnd, &store.SetValue(&APP_ID, &empty));
            result_log("clear PreventPinning", hwnd, &store.SetValue(&PREVENT_PINNING, &empty));
            result_log("property Commit", hwnd, &store.Commit());
        }
    }

    impl Windows {
        unsafe fn register(&mut self, reconnect: bool) {
            if reconnect || self.taskbar.is_none() {
                match connect() {
                    Ok(taskbar) => self.taskbar = Some(taskbar),
                    Err(e) => {
                        self.taskbar = None;
                        log(format_args!("TaskbarList/HrInit HRESULT=0x{:08X}", e.code().0 as u32));
                    }
                }
            }
            if let (Some(taskbar), Some(hwnd)) = (&self.taskbar, self.proxy) {
                result_log("AddTab", hwnd, &taskbar.AddTab(hwnd));
            }
        }
    }

    impl Drop for Windows {
        fn drop(&mut self) {
            unsafe {
                if let Some(hwnd) = self.proxy.take() {
                    clear_identity(hwnd);
                    // Also try a fresh interface if Explorer invalidated the old one.
                    let removed = self.taskbar.as_ref().map(|t| {
                        let result = t.DeleteTab(hwnd);
                        result_log("DeleteTab", hwnd, &result);
                        result.is_ok()
                    }).unwrap_or(false);
                    if !removed {
                        match connect() {
                            Ok(t) => result_log("DeleteTab retry", hwnd, &t.DeleteTab(hwnd)),
                            Err(e) => log(format_args!("cleanup TaskbarList/HrInit HRESULT=0x{:08X}", e.code().0 as u32)),
                        }
                    }
                    result_log("DestroyWindow proxy", hwnd, &DestroyWindow(hwnd));
                    PROXY.set(HWND::default());
                }
                if let Some(hwnd) = self.control.take() {
                    clear_identity(hwnd);
                    result_log("DestroyWindow control", hwnd, &DestroyWindow(hwnd));
                }
            }
        }
    }

    unsafe fn register_class(name: PCWSTR, icon: PCWSTR) -> Result<()> {
        let class = WNDCLASSW {
            lpfnWndProc: Some(window_proc),
            hInstance: GetModuleHandleW(None)?.into(),
            hIcon: LoadIconW(None, icon)?,
            hCursor: LoadCursorW(None, IDC_ARROW)?,
            hbrBackground: HBRUSH((COLOR_WINDOW.0 + 1) as usize as *mut _),
            lpszClassName: name,
            ..Default::default()
        };
        if RegisterClassW(&class) == 0 { return Err(Error::from_win32()); }
        Ok(())
    }

    unsafe fn styles(label: &str, hwnd: HWND) {
        log(format_args!("{label} HWND={hwnd:?} style=0x{:08X} exstyle=0x{:08X}",
            GetWindowLongPtrW(hwnd, GWL_STYLE) as u32,
            GetWindowLongPtrW(hwnd, GWL_EXSTYLE) as u32));
    }

    pub fn run() -> Result<()> {
        let mut exstyle = WS_EX_TOOLWINDOW;
        let mut noactivate = false;
        for arg in std::env::args().skip(1) {
            match arg.as_str() {
                "--appwindow" => exstyle = WS_EX_APPWINDOW,
                "--noactivate" => noactivate = true,
                "--help" | "-h" => {
                    log(format_args!("taskbar_proxy_probe [--noactivate] [--appwindow]\nDefault: TOOLWINDOW + AddTab. --appwindow replaces TOOLWINDOW.\nVisible control and proxy close after 85 seconds or when either is closed.\nObserve only probe windows; do not manipulate user apps."));
                    return Ok(());
                }
                _ => {
                    log(format_args!("Unknown argument: {arg}; use --help"));
                    return Err(Error::from_hresult(windows::core::HRESULT(0x80070057u32 as i32)));
                }
            }
        }
        if noactivate { exstyle |= WS_EX_NOACTIVATE; }
        let deadline = Instant::now() + LIFETIME;
        unsafe {
            CoInitializeEx(None, COINIT_APARTMENTTHREADED).ok()?;
            let _apartment = Apartment;
            let mut windows = Windows::default();
            let button_message = RegisterWindowMessageW(w!("TaskbarButtonCreated"));
            let shell_message = RegisterWindowMessageW(w!("TaskbarCreated"));
            if button_message == 0 || shell_message == 0 { return Err(Error::from_win32()); }
            BUTTON_MESSAGE.set(button_message);
            SHELL_MESSAGE.set(shell_message);
            register_class(w!("UltraDockProbeControl"), IDI_APPLICATION)?;
            register_class(w!("UltraDockProbeFolder"), IDI_INFORMATION)?;
            let instance = GetModuleHandleW(None)?.into();
            let style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
            let control = CreateWindowExW(WS_EX_APPWINDOW, w!("UltraDockProbeControl"),
                w!("Ultra Dock proxy probe"), style, CW_USEDEFAULT, CW_USEDEFAULT,
                500, 220, None, None, Some(instance), None)?;
            windows.control = Some(control);
            set_identity(control, w!("UltraDock.ProxyProbe.Control"))?;
            CreateWindowExW(WINDOW_EX_STYLE::default(), w!("STATIC"),
                w!("Manual probe, closes automatically after 85 seconds.\r\nThe small information-icon window is the test folder proxy.\r\nObserve taskbar reflow and Alt+Tab; click its button and client area.\r\nWatch stdout for activation/click events.\r\nDo not manipulate user apps. Close either probe window to stop."),
                WS_CHILD | WS_VISIBLE, 12, 12, 460, 150, Some(control), None, Some(instance), None)?;
            let proxy = CreateWindowExW(exstyle, w!("UltraDockProbeFolder"),
                w!("Ultra Dock probe folder"), style, CW_USEDEFAULT, CW_USEDEFAULT,
                320, 130, None, None, Some(instance), None)?;
            windows.proxy = Some(proxy);
            PROXY.set(proxy);
            set_identity(proxy, w!("UltraDock.ProxyProbe.Folder"))?;
            let _ = ShowWindow(control, SW_SHOWNORMAL);
            let _ = ShowWindow(proxy, SW_SHOWNOACTIVATE);
            styles("ordinary APPWINDOW control", control);
            styles("unowned folder proxy", proxy);
            log(format_args!("Observe only probe windows. NOACTIVATE={noactivate}. Timeout: 85 seconds.\nTaskbar clicks need not generate WM_LBUTTONUP; compare WM_ACTIVATE with direct client clicks."));
            windows.register(false);

            // A deadline checked between messages avoids depending on an idle
            // WM_TIMER. No worker thread, hooks, or foreign-window operations.
            while !EXIT.get() && Instant::now() < deadline {
                let mut message = MSG::default();
                while PeekMessageW(&mut message, None, 0, 0, PM_REMOVE).as_bool() {
                    if message.message == WM_QUIT { EXIT.set(true); break; }
                    let _ = TranslateMessage(&message);
                    DispatchMessageW(&message);
                    if EXIT.get() || Instant::now() >= deadline { break; }
                }
                if EXIT.get() || Instant::now() >= deadline { break; }
                if REGISTER.replace(false) {
                    windows.register(RECONNECT.replace(false));
                }
                let wait_ms = deadline.saturating_duration_since(Instant::now()).as_millis().min(250) as u32;
                if MsgWaitForMultipleObjectsEx(None, wait_ms, QS_ALLINPUT, MWMO_INPUTAVAILABLE) == WAIT_FAILED {
                    return Err(Error::from_win32());
                }
            }
            log(format_args!("Stopping: {}", if EXIT.get() { "window close/quit" } else { "automatic timeout" }));
            // Drop order: proxy DeleteTab, both windows, COM interface, apartment.
        }
        Ok(())
    }
}
