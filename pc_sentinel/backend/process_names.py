"""Conservative, offline process labels with explicit provenance.

The glossary is selected only for a matching executable name directly inside
this Windows installation's System32 or SysWOW64 directory. A path match is a
labeling hint, not signature verification or a determination that a process is
safe. No process is opened, stopped, uploaded, or queried by this module.
"""
import ntpath
import os
import unicodedata
from collections.abc import Mapping


_GLOSSARY = {
    "dwm.exe": (
        "Desktop Window Manager",
        "Combines app windows into the desktop image, including visual effects.",
        "https://learn.microsoft.com/en-us/windows/win32/directcomp/architecture-and-components",
    ),
    "svchost.exe": (
        "Windows Service Host",
        "Hosts Windows services. Multiple instances can run different services; the filename alone does not identify which services this instance hosts.",
        "https://learn.microsoft.com/en-us/windows/win32/services/service-programs",
    ),
    "runtimebroker.exe": (
        "Runtime Broker",
        "Helps manage device permissions for Microsoft Store apps.",
        "https://support.microsoft.com/en-au/windows/runtime-broker-is-using-too-much-memory-ca6ed4e3-2a36-964c-4d2e-8c93980d8a98",
    ),
}


def _plain_text(value, limit=160):
    """Metadata stays plain text. Reject controls, including bidi overrides."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or any(unicodedata.category(char).startswith("C") for char in value):
        return None
    return value[:limit]


def _absolute_windows_path(value):
    if not isinstance(value, str) or not value:
        return None
    value = value.replace("/", "\\")
    drive, tail = ntpath.splitdrive(value)
    # Do not interpret relative, UNC, device namespace, ADS, quoted, or shell paths.
    if (len(drive) != 2 or drive[1] != ":" or not drive[0].isalpha()
            or not tail.startswith("\\") or ":" in tail
            or any(ord(char) < 32 or char == '"' for char in value)):
        return None
    pieces = tail.split("\\")[1:]
    if not pieces or any(not part or part in (".", "..") or part.endswith((".", " ")) for part in pieces):
        return None
    return ntpath.normcase(value)


def _name_key(value):
    text = _plain_text(value)
    if not text or ntpath.basename(text) != text:
        return None
    return (text if text.lower().endswith(".exe") else text + ".exe").lower()


def describe_process(record, *, windows_dir=None):
    """Copy a process/group row and add readable fields without losing details.

    ``name`` may be the raw filename or the existing top_apps executable stem.
    ``exe`` must be the OS-reported full executable path. Optional
    ``file_description`` is executable metadata supplied by the caller, never a
    verified role. The caller must HTML-escape all text when rendering it.

    ``windows_dir`` is a programmatic test/integration seam, not a user request
    field. Production defaults to the Windows SystemRoot environment value.
    Grouping must keep executable paths separate before calling this helper:
    merging a user-directory svchost with System32 svchost loses that distinction.
    """
    if not isinstance(record, Mapping):
        raise TypeError("A process row must be a mapping.")
    out = dict(record)
    raw_name = record.get("name")
    exe = record.get("exe")
    raw_filename = raw_name if isinstance(raw_name, str) and raw_name else (
        ntpath.basename(exe) if isinstance(exe, str) else "")
    name_key = _name_key(raw_filename)
    path = _absolute_windows_path(exe)
    root = _absolute_windows_path(windows_dir if windows_dir is not None else os.environ.get("SystemRoot"))
    file_key = ntpath.basename(path) if path else None
    known = _GLOSSARY.get(name_key)
    matched = bool(known and root and path and file_key == name_key
                   and ntpath.dirname(path) in (root + "\\system32", root + "\\syswow64"))
    fallback = _plain_text(raw_filename) or "Unknown process"
    if fallback.lower().endswith(".exe"):
        fallback = fallback[:-4] or "Unknown process"
    metadata = _plain_text(record.get("file_description"))
    visible = lambda value: ''.join(ascii(c)[1:-1] if unicodedata.category(c).startswith('C') else c for c in value) if isinstance(value, str) else None
    out.update(raw_filename=raw_filename, raw_filename_display=visible(raw_filename), exe_display=visible(exe), display_name=fallback, explanation=None,
               name_source="filename", explanation_source=None, source_url=None,
               system_path_match=False, security_assessment="not_performed")
    if matched:
        out.update(display_name=known[0], explanation=known[1],
                   name_source="windows_glossary", explanation_source="windows_glossary",
                   source_url=known[2], system_path_match=True)
    elif metadata:
        out.update(display_name=metadata, name_source="file_description",
                   explanation="Name supplied by executable file metadata; the app's role has not been independently identified.",
                   explanation_source="metadata_notice")
    return out
