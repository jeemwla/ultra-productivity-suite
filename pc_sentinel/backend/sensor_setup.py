"""First-run sensor guide: is LibreHardwareMonitor installed and running, and is its PawnIO driver present?

LibreHardwareMonitor reads temperatures, fans and power through a kernel driver. Older versions used WinRing0, which
Microsoft Defender flags as vulnerable (CVE-2020-14979); current versions use PawnIO, installed separately. Without
the driver, LibreHardwareMonitor runs but CPU temperatures, fans and power read empty, so the app says
"sensor driver missing" instead of showing blanks.

PC Sentinel's own sensor helper (sensor_helper.py) is tried first. Its states explain a missing or blocked driver in
plain words and offer "Try again"; a LibreHardwareMonitor the user runs keeps working as before.

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


RETRY_TEXT = "Try again"


def _helper_guide(helper, cpu_temp, links):
    """States for PC Sentinel's own sensor helper, or None when the helper does not apply.

    Never suggests turning off Windows security features: a blocked driver is reported as blocked, with what still
    works and a retry."""
    helper = helper or {}
    last = helper.get("last_setup") or {}
    retry = {"retry": bool(helper.get("can_set_up")), "retry_label": RETRY_TEXT}
    if helper.get("running"):
        device = helper.get("driver")
        if cpu_temp and device == "ok":
            return None
        if last.get("restart") and device != "ok":
            return {"state": "restart_needed", "links": links, "title": "Restart to finish setting up sensors",
                    "text": "The sensor driver was installed and needs one restart before temperatures, fans and power "
                            "appear. Until then PC Sentinel shows what Windows provides.", **retry}
        if device == "not_installed":
            reason = {"download_failed": " It couldn't be downloaded during setup (no internet connection?).",
                      "hash_mismatch": " The download didn't match the official release, so it wasn't run.",
                      "install_failed": " Its installer didn't finish."}.get(last.get("pawnio"), "")
            return {"state": "helper_driver_missing", "links": links, "title": "Sensor driver not installed",
                    "text": "PC Sentinel's sensor helper is running, but the PawnIO driver it reads the hardware "
                            "through isn't installed, so CPU temperatures, fans and power stay empty." + reason
                            + " Try again to install it; Windows asks for permission once.", **retry}
        if device in ("not_running", "access_denied", "error"):
            extra = (" Memory integrity is on, which can stop some drivers from loading." if helper.get("memory_integrity")
                     else "")
            return {"state": "helper_driver_blocked", "links": links, "title": "Windows didn't load the sensor driver",
                    "text": "PawnIO is installed, but Windows didn't let it start, so CPU temperatures, fans and power "
                            "stay empty. Security features or security software can block drivers." + extra
                            + " Everything else in PC Sentinel keeps working. Restart the PC, then try again.", **retry}
        if not cpu_temp and device == "ok" and (helper.get("sensor_count") or 0) > 0:
            return {"state": "cpu_unsupported", "links": links, "title": "CPU temperature isn't available here",
                    "text": "The sensor helper and its driver are working, but this processor doesn't report its "
                            "temperature through them. Other readings are shown as usual.", "retry": False}
        if not cpu_temp:
            return {"state": "helper_partial", "links": links, "title": "Some sensors aren't reporting yet",
                    "text": "The sensor helper is running, but no CPU temperature has come through yet. Wait a few "
                            "seconds; if it stays empty, try again.", **retry}
        return None
    if helper.get("installed"):
        return {"state": "helper_stopped", "links": links, "title": "The sensor helper isn't answering",
                "text": "PC Sentinel's sensor helper is installed but not responding, so it shows what Windows "
                        "provides. Try again to restart and repair it; Windows asks for permission once.", **retry}
    if helper.get("can_set_up"):
        declined = last and not last.get("elevated")
        return {"state": "helper_missing", "links": links, "title": "Set up full sensors",
                "text": ("Full sensors weren't set up because Windows permission wasn't given. " if declined else
                         "Windows doesn't report temperatures, fan speeds or power by itself. ")
                        + "PC Sentinel can set up its free sensor helper and the PawnIO driver; Windows asks for "
                          "permission once. LibreHardwareMonitor also works if you prefer it.", **retry}
    return None


def guide(monitors, drivers, level, groups, helper=None, source_id=None):
    """Plain-language state for the banner and empty readings.

    state: "ok" | "lhm_missing" | "lhm_stopped" | "driver_missing" | "legacy_driver" | "restart_needed" |
           "helper_missing" | "helper_stopped" | "helper_driver_missing" | "helper_driver_blocked" | "helper_partial" |
           "cpu_unsupported"
    Helper states carry retry: true when this install can run the sensor setup again.
    """
    monitors, drivers = monitors or {}, drivers or {}
    installed, running = bool(monitors.get("lhm_installed")), bool(monitors.get("lhm_running"))
    full = level == "full"
    cpu_temp = _has_reading(groups, {"cpu"}, {"Temperature"})
    links = {"lhm": LHM_RELEASES, "pawnio": PAWNIO_RELEASES}
    if helper and (source_id == "helper" or not (full and cpu_temp)):
        # The helper explains itself unless another source (a user's own LibreHardwareMonitor) already works.
        state = _helper_guide(helper, cpu_temp and source_id == "helper", links)
        if state:
            return state
        if source_id == "helper":
            return {"state": "ok", "links": links, "title": "Full sensors are working", "text": ""}
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
