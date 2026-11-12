"""One shared laptop or desktop decision for every PC Sentinel page.

Inputs are facts Windows reports, read without PowerShell and without admin rights:
  battery        GetSystemPowerStatus (from max_power.detect_device)
  modern_standby SYSTEM_POWER_CAPABILITIES.AoAc (from max_power.detect_device)
  chassis_types  SMBIOS type 3 "System Enclosure" from GetSystemFirmwareTable('RSMB')
  platform_role  PowerDeterminePlatformRoleEx(POWER_PLATFORM_ROLE_V2), the firmware's own "mobile or desktop" claim

The chassis is trusted first because a battery alone is not proof of a laptop: a desktop on a USB UPS reports one.
"""
import os

# SMBIOS System Enclosure types (DMTF DSP0134, table 17)
PORTABLE_CHASSIS = {8: "Portable", 9: "Laptop", 10: "Notebook", 11: "Hand held", 14: "Sub notebook",
                    30: "Tablet", 31: "Convertible", 32: "Detachable"}
DESKTOP_CHASSIS = {3: "Desktop", 4: "Low profile desktop", 5: "Pizza box", 6: "Mini tower", 7: "Tower",
                   13: "All in one", 15: "Space-saving", 16: "Lunch box", 17: "Main server chassis",
                   23: "Rack mount", 24: "Sealed-case PC", 34: "Embedded PC", 35: "Mini PC", 36: "Stick PC"}
# POWER_PLATFORM_ROLE values
MOBILE_ROLES = {2: "Mobile", 8: "Slate"}
DESKTOP_ROLES = {1: "Desktop", 3: "Workstation", 4: "Enterprise server", 5: "Small office server",
                 6: "Appliance PC", 7: "Performance server"}


def chassis_from_smbios(raw: bytes) -> list:
    """Chassis type codes from a RawSMBIOSData buffer (8-byte header, then the structure table)."""
    if len(raw) < 8:
        return []
    length = int.from_bytes(raw[4:8], "little")
    table = raw[8:8 + length]
    found, off = [], 0
    while off + 4 <= len(table):
        kind, size = table[off], table[off + 1]
        if size < 4 or off + size > len(table):
            break
        if kind == 3 and size > 5:
            found.append(table[off + 5] & 0x7F)  # bit 7 is the chassis lock flag
        if kind == 127:  # end-of-table marker
            break
        end = table.find(b"\x00\x00", off + size)  # string set ends with a double NUL
        if end < 0:
            break
        off = end + 2
    return found


def firmware_facts() -> dict:
    """SMBIOS chassis types and the firmware platform role. Missing values stay empty or None."""
    facts = {"chassis_types": [], "platform_role": None}
    if os.name != "nt":
        return facts
    import ctypes
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        rsmb = 0x52534D42
        need = k32.GetSystemFirmwareTable(rsmb, 0, None, 0)
        if need:
            buf = (ctypes.c_ubyte * need)()
            if k32.GetSystemFirmwareTable(rsmb, 0, ctypes.byref(buf), need) == need:
                facts["chassis_types"] = chassis_from_smbios(bytes(buf))
    except (OSError, AttributeError, ValueError):
        pass
    try:
        role = ctypes.WinDLL("powrprof").PowerDeterminePlatformRoleEx(2)  # POWER_PLATFORM_ROLE_V2
        facts["platform_role"] = int(role) if 0 < int(role) < 16 else None
    except (OSError, AttributeError, ValueError):
        pass
    return facts


def classify(info: dict) -> dict:
    """{"type": "laptop" | "desktop", "basis": plain-language reason}. Never raises on missing facts."""
    battery = bool(info.get("battery"))
    chassis = [c for c in (info.get("chassis_types") or []) if isinstance(c, int)]
    role = info.get("platform_role")
    portable = [PORTABLE_CHASSIS[c] for c in chassis if c in PORTABLE_CHASSIS]
    fixed = [DESKTOP_CHASSIS[c] for c in chassis if c in DESKTOP_CHASSIS]
    if portable:
        return {"type": "laptop", "basis": f"{portable[0]} chassis reported by the firmware"}
    if fixed:
        extra = "; the reported battery is likely a UPS" if battery else ""
        return {"type": "desktop", "basis": f"{fixed[0]} chassis reported by the firmware{extra}"}
    if role in MOBILE_ROLES:
        return {"type": "laptop", "basis": f"Firmware power role: {MOBILE_ROLES[role]}"}
    if role in DESKTOP_ROLES and not battery:
        return {"type": "desktop", "basis": f"Firmware power role: {DESKTOP_ROLES[role]}"}
    if battery:
        return {"type": "laptop", "basis": "A system battery is reported and the chassis type is unknown"}
    if info.get("modern_standby"):
        return {"type": "laptop", "basis": "Modern Standby without a desktop chassis"}
    return {"type": "desktop", "basis": "No battery and no portable chassis reported"}
