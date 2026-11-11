"""Read-only Windows source/target identities. No enumeration-order matching.

CCD source names join GDI views to active targets. Monitor interface paths are
used only for exact joins and EDID lookup, and are not returned in public rows.
Windows/EDID names are reported identities, not independently certified models.
"""
import ctypes
import os
import re


IS_WIN = os.name == "nt"
U32, I32 = ctypes.c_uint32, ctypes.c_int32


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", U32), ("HighPart", I32)]


class _RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", U32), ("Denominator", U32)]


class _SOURCE_INFO(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", U32), ("modeInfoIdx", U32), ("statusFlags", U32)]


class _TARGET_INFO(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", U32), ("modeInfoIdx", U32),
                ("outputTechnology", U32), ("rotation", U32), ("scaling", U32),
                ("refreshRate", _RATIONAL), ("scanLineOrdering", U32),
                ("targetAvailable", I32), ("statusFlags", U32)]


class _PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", _SOURCE_INFO), ("targetInfo", _TARGET_INFO), ("flags", U32)]


class _MODE_PAYLOAD(ctypes.Union):
    # The unused DISPLAYCONFIG_MODE_INFO union is 48 bytes, aligned to UINT64.
    _fields_ = [("alignment", ctypes.c_uint64), ("data", ctypes.c_ubyte * 48)]


class _MODE_INFO(ctypes.Structure):
    _fields_ = [("infoType", U32), ("id", U32), ("adapterId", _LUID), ("payload", _MODE_PAYLOAD)]


class _HEADER(ctypes.Structure):
    _fields_ = [("type", U32), ("size", U32), ("adapterId", _LUID), ("id", U32)]


class _SOURCE_NAME(ctypes.Structure):
    _fields_ = [("header", _HEADER), ("viewGdiDeviceName", ctypes.c_wchar * 32)]


class _TARGET_NAME(ctypes.Structure):
    _fields_ = [("header", _HEADER), ("flags", U32), ("outputTechnology", U32),
                ("edidManufactureId", ctypes.c_uint16), ("edidProductCodeId", ctypes.c_uint16),
                ("connectorInstance", U32), ("monitorFriendlyDeviceName", ctypes.c_wchar * 64),
                ("monitorDevicePath", ctypes.c_wchar * 128)]


def _text(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and len(value) <= 256 and value.isprintable() else None


def _model(value):
    value = _text(value)
    if value and not re.fullmatch(r"(?:generic(?: pnp| non-pnp)? monitor|monitor|display|digital display|lcd monitor)", value, re.I):
        return value
    return None


def _key(value):
    return value.strip().casefold() if isinstance(value, str) else ""


def edid_model(data):
    """Read only a checked base-block monitor-name descriptor, never serials."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 128:
        return None
    block = data[:128]
    if block[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00" or sum(block) % 256:
        return None
    names = set()
    for offset in (54, 72, 90, 108):
        descriptor = block[offset:offset + 18]
        if descriptor[:5] == b"\x00\x00\x00\xfc\x00":
            try:
                name = _model(descriptor[5:18].split(b"\n", 1)[0].rstrip(b"\x00 \r").decode("ascii"))
            except UnicodeError:
                continue
            if name:
                names.add(name)
    return next(iter(names)) if len(names) == 1 else None


def _read_edid(interface_path):
    if not IS_WIN or not isinstance(interface_path, str):
        return None
    # An exact GUID_DEVINTERFACE_MONITOR path, not a model substring or index.
    match = re.fullmatch(r"\\\\\?\\DISPLAY#([a-z0-9&_.-]{1,128})#([a-z0-9&_.-]{1,128})#\{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7\}", interface_path, re.I)
    if not match:
        return None
    try:
        import winreg
        key = "SYSTEM\\CurrentControlSet\\Enum\\DISPLAY\\" + match[1] + "\\" + match[2] + "\\Device Parameters"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ) as handle:
            data, kind = winreg.QueryValueEx(handle, "EDID")
        return edid_model(data) if kind == winreg.REG_BINARY else None
    except OSError:
        return None


def _luid(value):
    return f"{value.HighPart & 0xffffffff:08x}:{value.LowPart:08x}"


def _query_targets(user32):
    flags = 2  # QDC_ONLY_ACTIVE_PATHS; no topology or mode changes.
    sizes, query, info = user32.GetDisplayConfigBufferSizes, user32.QueryDisplayConfig, user32.DisplayConfigGetDeviceInfo
    sizes.argtypes = [U32, ctypes.POINTER(U32), ctypes.POINTER(U32)]
    sizes.restype = I32
    query.argtypes = [U32, ctypes.POINTER(U32), ctypes.POINTER(_PATH_INFO),
                      ctypes.POINTER(U32), ctypes.POINTER(_MODE_INFO), ctypes.c_void_p]
    query.restype = I32
    info.argtypes, info.restype = [ctypes.POINTER(_HEADER)], I32
    for _ in range(3):
        path_count, mode_count = U32(), U32()
        if sizes(flags, ctypes.byref(path_count), ctypes.byref(mode_count)) != 0:
            return {"status": "unavailable", "targets": []}
        if path_count.value > 256 or mode_count.value > 2048:
            return {"status": "invalid_buffer_size", "targets": []}
        paths = (_PATH_INFO * max(1, path_count.value))()
        modes = (_MODE_INFO * max(1, mode_count.value))()
        result = query(flags, ctypes.byref(path_count), paths, ctypes.byref(mode_count), modes, None)
        if result == 122:  # ERROR_INSUFFICIENT_BUFFER: topology changed; retry boundedly.
            continue
        if result != 0:
            return {"status": "unavailable", "targets": []}
        if path_count.value > len(paths) or mode_count.value > len(modes):
            return {"status": "invalid_buffer_size", "targets": []}
        targets = []
        for path in paths[:path_count.value]:
            if not path.flags & 1 or not path.targetInfo.targetAvailable:
                continue
            source, target = _SOURCE_NAME(), _TARGET_NAME()
            source.header = _HEADER(1, ctypes.sizeof(source), path.sourceInfo.adapterId, path.sourceInfo.id)
            target.header = _HEADER(2, ctypes.sizeof(target), path.targetInfo.adapterId, path.targetInfo.id)
            source_ok = info(ctypes.byref(source.header)) == 0
            target_ok = info(ctypes.byref(target.header)) == 0
            targets.append({"gdi_device": source.viewGdiDeviceName if source_ok else None,
                            "adapter_id": _luid(path.targetInfo.adapterId), "target_id": path.targetInfo.id,
                            "source_id": path.sourceInfo.id,
                            "device_path": target.monitorDevicePath if target_ok else None,
                            "friendly_name": target.monitorFriendlyDeviceName if target_ok else None,
                            "friendly_from_edid": bool(target_ok and target.flags & 1),
                            "connector_instance": target.connectorInstance if target_ok else None})
        return {"status": "ok", "targets": targets}
    return {"status": "topology_changed", "targets": []}


def active_targets():
    if not IS_WIN:
        return {"status": "unsupported", "targets": []}
    try:
        return _query_targets(ctypes.WinDLL("user32", use_last_error=True))
    except (OSError, AttributeError):
        return {"status": "unavailable", "targets": []}


def _identity(target, mapping):
    model = _model(target.get("friendly_name")) if target.get("friendly_from_edid") is True else None
    source = "windows_target_edid" if model else None
    if not model:
        model = _read_edid(target.get("device_path"))
        source = "mapped_interface_edid" if model else None
    return {"model": model, "name": model or "Unknown monitor", "identity_source": source,
            "identity_status": "reported" if model else "unknown", "mapping": mapping,
            "adapter_id": target.get("adapter_id"), "source_id": target.get("source_id"),
            "target_id": target.get("target_id"), "connector_instance": target.get("connector_instance"),
            "description": _text(target.get("friendly_name")),
            "note": "Windows/EDID-reported model." if model else "A monitor model could not be established from the mapped Windows target or EDID."}


def describe_display(device, monitors, targets):
    """Resolve one GDI view. Cloned views retain all targets, never the first one."""
    interfaces = {_key(row.get("device_path")) for row in monitors if row.get("device_path")}
    matched = [row for row in targets if _key(row.get("gdi_device")) == _key(device)]
    if not matched and interfaces:
        matched = [row for row in targets if not row.get("gdi_device") and _key(row.get("device_path")) in interfaces]
    identities = []
    seen = {}
    for target in matched:
        identity = _identity(target, "windows_source_target")
        path = _key(target.get("device_path"))
        if interfaces and path and path not in interfaces:
            identity.update(model=None, name="Unknown monitor", identity_source=None, identity_status="topology_changed",
                            note="GDI monitor and active target paths disagree; identity is withheld until a fresh matching query.")
        key = (target.get("adapter_id"), target.get("target_id"))
        if key in seen:
            previous = seen[key]
            if previous != identity:
                previous.update(model=None, name="Unknown monitor", identity_source=None, identity_status="ambiguous",
                                note="Conflicting records for one Windows target; no model was selected.")
            continue
        seen[key] = identity
        identities.append(identity)
    if not identities:
        # Exact per-monitor GDI interface is a fallback, not a positional match.
        for monitor in monitors:
            if not monitor.get("device_path"):
                continue
            identity = _identity({"device_path": monitor["device_path"], "friendly_name": monitor.get("description")},
                                 "gdi_monitor_interface")
            identities.append(identity)
    if len(identities) == 1:
        item = identities[0]
        return {"name": item["name"], "model": item["model"], "identity_status": item["identity_status"],
                "identity_source": item["identity_source"], "monitors": identities}
    if len(identities) > 1:
        return {"name": "Multiple monitors (mirrored view)", "model": None,
                "identity_status": "multiple_targets", "identity_source": None, "monitors": identities}
    return {"name": "Unknown monitor", "model": None, "identity_status": "unknown", "identity_source": None, "monitors": []}


def computer_identity():
    """Read the Windows computer name, independently of CPU or system model."""
    unknown = {"hostname": None, "hostname_source": "unavailable"}
    if not IS_WIN:
        return unknown
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = kernel.GetComputerNameExW
        fn.argtypes, fn.restype = [I32, ctypes.c_wchar_p, ctypes.POINTER(U32)], I32
        size = U32()
        fn(5, None, ctypes.byref(size))  # ComputerNamePhysicalDnsHostname
        if 0 < size.value <= 256:
            buffer = ctypes.create_unicode_buffer(size.value)
            if fn(5, buffer, ctypes.byref(size)) and _text(buffer.value):
                return {"hostname": buffer.value, "hostname_source": "GetComputerNameExW"}
    except (OSError, AttributeError):
        pass
    try:
        fn = ctypes.WinDLL("kernel32", use_last_error=True).GetComputerNameW
        fn.argtypes, fn.restype = [ctypes.c_wchar_p, ctypes.POINTER(U32)], I32
        size, buffer = U32(256), ctypes.create_unicode_buffer(256)
        if fn(buffer, ctypes.byref(size)) and _text(buffer.value):
            return {"hostname": buffer.value, "hostname_source": "GetComputerNameW"}
    except (OSError, AttributeError):
        pass
    return unknown
