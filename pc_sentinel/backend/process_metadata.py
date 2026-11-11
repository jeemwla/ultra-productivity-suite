"""Read-only executable version labels, never a role or signature verdict.

API sequence follows Microsoft's VerQueryValueW documentation:
https://learn.microsoft.com/en-us/windows/win32/api/winver/nf-winver-verqueryvaluew
Local fixed/removable drives only. Call outside the process-sampling lock.
"""
import ctypes
import ntpath
import os
import struct
import threading
import time
from collections import OrderedDict
from .process_names import _absolute_windows_path, _plain_text

_cache = OrderedDict()
_lock = threading.RLock()
_MAX_CACHE = 512


def _local_drive(path):
    if os.name != 'nt':
        return False
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel.GetDriveTypeW.restype = ctypes.c_uint
    return kernel.GetDriveTypeW(ntpath.splitdrive(path)[0] + '\\') in (2, 3)


def _read_version(path):
    if not _local_drive(path):
        return None
    dll = ctypes.WinDLL('version', use_last_error=True)
    size_fn, read_fn, query_fn = dll.GetFileVersionInfoSizeW, dll.GetFileVersionInfoW, dll.VerQueryValueW
    size_fn.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    size_fn.restype = ctypes.c_uint32
    read_fn.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    read_fn.restype = ctypes.c_int
    query_fn.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint)]
    query_fn.restype = ctypes.c_int
    ignored = ctypes.c_uint32()
    size = size_fn(path, ctypes.byref(ignored))
    if not 0 < size <= 4 * 1024 * 1024:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not read_fn(path, 0, size, buffer):
        return None
    base = ctypes.addressof(buffer)

    def query(key, chars=False):
        pointer, length = ctypes.c_void_p(), ctypes.c_uint()
        if not query_fn(buffer, key, ctypes.byref(pointer), ctypes.byref(length)) or not pointer.value:
            return None
        count = length.value
        byte_count = count * ctypes.sizeof(ctypes.c_wchar) if chars else count
        if not count or count > 4096 or not base <= pointer.value <= base + size - byte_count:
            return None
        return ctypes.wstring_at(pointer, count).split('\x00', 1)[0] if chars else ctypes.string_at(pointer, count)

    translations = query('\\VarFileInfo\\Translation')
    if not translations or len(translations) % 4:
        return None
    for language, codepage in struct.iter_unpack('<HH', translations):
        value = query(f'\\StringFileInfo\\{language:04x}{codepage:04x}\\FileDescription', chars=True)
        text = _plain_text(value)
        if text:
            return text
    return None


def file_description(path, *, cached_only=False, with_status=False):
    """Cache by normalized path and file mtime/size; no executable is loaded."""
    result = lambda text, read: (text, read) if with_status else text
    path = _absolute_windows_path(path)
    if path is None:
        return result(None, False)
    with _lock:
        previous = _cache.get(path)
        if previous and previous[0] is None and time.monotonic() < previous[2]:
            return result(None, False)
        if cached_only:
            return result(previous[1] if previous else None, False)
    try:
        if not _local_drive(path):
            raise OSError('Only local fixed/removable executable metadata is supported.')
        stat = os.stat(path)
        identity = (stat.st_mtime_ns, stat.st_size)
        with _lock:
            previous = _cache.get(path)
            if previous and previous[0] == identity:
                _cache.move_to_end(path)
                return result(previous[1], False)
        text = _plain_text(_read_version(path))
    except (OSError, AttributeError, ValueError, OverflowError):
        identity, text = None, None
    with _lock:
        _cache[path] = (identity, text, time.monotonic() + 300 if identity is None else 0)
        _cache.move_to_end(path)
        while len(_cache) > _MAX_CACHE:
            _cache.popitem(last=False)
    return result(text, True)
