"""The 32 px shell icon of an executable, as PNG bytes, for PC Sentinel's Top apps list.

Standard library only (ctypes, zlib). The icon comes from SHGetFileInfoW, the same source Explorer uses; the color
and mask bitmaps are read with GetDIBits and encoded as RGBA PNG. Results are cached by path, modification time and
size. Local fixed or removable drives only; nothing is executed or loaded as code.
"""
import ctypes
import os
import struct
import threading
import zlib
from collections import OrderedDict
from ctypes import wintypes

_cache = OrderedDict()
_lock = threading.Lock()
_MAX = 256


def _png(width, height, rgba):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def bgra_to_rgba(bgra, mask=None):
    """Swap BGRA to RGBA. If the icon has no alpha at all, derive it from the AND mask (white = transparent)."""
    px = bytearray(bgra)
    has_alpha = any(px[i] for i in range(3, len(px), 4))
    for i in range(0, len(px), 4):
        px[i], px[i + 2] = px[i + 2], px[i]
        if not has_alpha:
            px[i + 3] = 0 if mask is not None and mask[i] else 255
    return bytes(px)


if os.name == "nt":
    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", ctypes.c_void_p), ("iIcon", ctypes.c_int), ("dwAttributes", wintypes.DWORD),
                    ("szDisplayName", ctypes.c_wchar * 260), ("szTypeName", ctypes.c_wchar * 80)]

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                    ("hbmMask", ctypes.c_void_p), ("hbmColor", ctypes.c_void_p)]

    class BITMAP(ctypes.Structure):
        _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                    ("bmWidthBytes", ctypes.c_long), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                    ("bmBits", ctypes.c_void_p)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 4)]

    def _api():
        shell32, user32, gdi32, ole32 = (ctypes.WinDLL(n, use_last_error=True) for n in ("shell32", "user32", "gdi32", "ole32"))
        shell32.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(SHFILEINFOW), wintypes.UINT, wintypes.UINT]
        shell32.SHGetFileInfoW.restype = ctypes.c_size_t
        user32.GetIconInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(ICONINFO)]
        user32.GetIconInfo.restype = wintypes.BOOL
        user32.DestroyIcon.argtypes = [ctypes.c_void_p]
        user32.GetDC.argtypes = [ctypes.c_void_p]
        user32.GetDC.restype = ctypes.c_void_p
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                    ctypes.POINTER(BITMAPINFO), wintypes.UINT]
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        return shell32, user32, gdi32, ole32

    def _bits(gdi32, hdc, hbm, width, height):
        info = BITMAPINFO()
        info.bmiHeader = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(width * height * 4)
        if gdi32.GetDIBits(hdc, hbm, 0, height, buf, ctypes.byref(info), 0) != height:
            return None
        return buf.raw

    def _extract(path):
        shell32, user32, gdi32, ole32 = _api()
        ole32.CoInitializeEx(None, 2)  # SHGetFileInfo needs COM on this thread; an earlier init is fine
        sfi = SHFILEINFOW()
        if not shell32.SHGetFileInfoW(path, 0, ctypes.byref(sfi), ctypes.sizeof(sfi), 0x100) or not sfi.hIcon:
            return None
        info = ICONINFO()
        try:
            if not user32.GetIconInfo(sfi.hIcon, ctypes.byref(info)) or not info.hbmColor:
                return None
            bm = BITMAP()
            gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(bm), ctypes.byref(bm))
            width, height = bm.bmWidth, abs(bm.bmHeight)
            if not (0 < width <= 256 and 0 < height <= 256):
                return None
            hdc = user32.GetDC(None)
            try:
                color = _bits(gdi32, hdc, info.hbmColor, width, height)
                mask = _bits(gdi32, hdc, info.hbmMask, width, height) if info.hbmMask else None
            finally:
                user32.ReleaseDC(None, hdc)
            return _png(width, height, bgra_to_rgba(color, mask)) if color else None
        finally:
            for h in (info.hbmColor, info.hbmMask):
                if h:
                    gdi32.DeleteObject(h)
            user32.DestroyIcon(sfi.hIcon)


def icon_png(path):
    """PNG bytes for the executable's icon, or None. Cached by path, mtime and size."""
    if os.name != "nt" or not path or not os.path.isabs(path):
        return None
    try:
        from .process_metadata import _local_drive
        if not _local_drive(path):
            return None
        st = os.stat(path)
    except (OSError, ImportError, ValueError):
        return None
    key = (os.path.normcase(path), st.st_mtime_ns, st.st_size)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    try:
        png = _extract(path)
    except (OSError, ValueError, ctypes.ArgumentError):
        png = None
    with _lock:
        _cache[key] = png
        while len(_cache) > _MAX:
            _cache.popitem(last=False)
    return png
