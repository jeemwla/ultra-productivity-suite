"""First-run sensor guide: is LibreHardwareMonitor installed and running, and is its PawnIO driver present?

LibreHardwareMonitor reads temperatures, fans and power through a kernel driver. Older versions used WinRing0, which
Microsoft Defender flags as vulnerable (CVE-2020-14979); current versions use PawnIO, installed separately. Without
the driver, LibreHardwareMonitor runs but CPU temperatures, fans and power read empty, so the app says
"sensor driver missing" instead of showing blanks.

Read-only: registry and file checks, no admin rights, nothing is installed or changed here.
"""
import os

LHM_RELEASES = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases"
PAWNIO_RELEASES = "https://github.com/namazso/PawnIO.Setup/releases"
LEGACY_DRIVERS = ("WinRing0_1_2_0", "WinRing0x64", "WinRing0")


def _service_present(name):
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Services\{name}"):
            return True
    except OSError:
        return False


def driver_facts():
    """PawnIO and legacy WinRing0 presence (service keys), plus PawnIO's install folder."""
    pawn_dir = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "PawnIO")
    return {"pawnio": _service_present("PawnIO") or os.path.isfile(os.path.join(pawn_dir, "PawnIOLib.dll")),
            "winring0": any(_service_present(n) for n in LEGACY_DRIVERS)}


def _has_reading(groups, kinds, types):
    return any(g.get("kind") in kinds and any(s.get("type") in types and s.get("value") is not None for s in g.get("sensors", []))
               for g in groups or [])


def guide(monitors, drivers, level, groups):
    """Plain-language state for the banner and empty readings.

    state: "ok" | "lhm_missing" | "lhm_stopped" | "driver_missing" | "legacy_driver"
    """
    monitors, drivers = monitors or {}, drivers or {}
    installed, running = bool(monitors.get("lhm_installed")), bool(monitors.get("lhm_running"))
    full = level == "full"
    cpu_temp = _has_reading(groups, {"cpu"}, {"Temperature"})
    links = {"lhm": LHM_RELEASES, "pawnio": PAWNIO_RELEASES}
    if full and cpu_temp:
        if drivers.get("winring0") and not drivers.get("pawnio"):
            return {"state": "legacy_driver", "links": links, "title": "Sensors work through the old WinRing0 driver",
                    "text": "This LibreHardwareMonitor uses WinRing0, which Microsoft Defender flags as vulnerable. "
                            "Updating to a current LibreHardwareMonitor and installing PawnIO replaces it."}
        return {"state": "ok", "links": links, "title": "Full sensors are working", "text": ""}
    if not installed and not running and not full:
        return {"state": "lhm_missing", "links": links, "title": "Set up full sensors",
                "text": "Windows doesn't report temperatures, fan speeds or power by itself. The free, open-source "
                        "LibreHardwareMonitor reads them, using the PawnIO driver to talk to the hardware."}
    if not full and installed and not running:
        return {"state": "lhm_stopped", "links": links, "title": "LibreHardwareMonitor is installed but not running",
                "text": "Start it (it needs administrator rights once) to add temperatures, fans and power."}
    if not drivers.get("pawnio") and not drivers.get("winring0"):
        return {"state": "driver_missing", "links": links, "title": "Sensor driver missing",
                "text": "LibreHardwareMonitor is running, but its PawnIO driver isn't installed, so CPU temperatures, "
                        "fans and power stay empty. Install PawnIO from its official page, then restart "
                        "LibreHardwareMonitor."}
    if full and not cpu_temp:
        return {"state": "driver_missing", "links": links, "title": "Sensor driver not responding",
                "text": "PawnIO is installed, but no CPU temperature is coming through. Restart LibreHardwareMonitor "
                        "as administrator; if that doesn't help, update both LibreHardwareMonitor and PawnIO."}
    return {"state": "lhm_stopped", "links": links, "title": "LibreHardwareMonitor isn't sending readings",
            "text": "Start LibreHardwareMonitor and turn on its web server or WMI output, then wait a few seconds."}
