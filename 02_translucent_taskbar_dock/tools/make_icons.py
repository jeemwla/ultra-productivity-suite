"""Generates the four Start menu app icons (.ico with embedded PNGs) without external libraries."""
import math, os, struct, zlib, sys

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "src-tauri", "appicons")
os.makedirs(OUT, exist_ok=True)


def png(size, pixel):
    rows = []
    for y in range(size):
        r = bytearray([0])
        for x in range(size):
            r += bytes(pixel(x / size, y / size))
        rows.append(bytes(r))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def rounded(u, v, r=0.22):
    dx, dy = max(abs(u - .5) - (.5 - r), 0), max(abs(v - .5) - (.5 - r), 0)
    return math.hypot(dx, dy) <= r


def make(c1, c2, glyph):
    def pixel(u, v):
        if not rounded(u, v):
            return (0, 0, 0, 0)
        t = (u + v) / 2
        base = [int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)]
        if glyph(u, v):
            return (255, 255, 255, 255)
        return (*base, 255)
    return pixel


def ring(cx, cy, r, w):
    return lambda u, v: abs(math.hypot(u - cx, v - cy) - r) < w


GLYPHS = {
    "alarm": lambda u, v: ring(.5, .54, .24, .045)(u, v) or (abs(u - .5) < .03 and .36 < v < .55) or (abs(v - .54) < .03 and .5 < u < .62),
    "sentinel": lambda u, v: any(abs(v - y) < .035 and a <= u <= b for a, b, y in ((.2, .36, .58), (.64, .8, .5))) or
                               any(abs((v - (y0 + (y1 - y0) * (u - x0) / (x1 - x0)))) < .04 and x0 <= u <= x1
                                   for x0, x1, y0, y1 in ((.36, .44, .58, .3), (.44, .56, .3, .72), (.56, .64, .72, .5))),
    "dock": lambda u, v: any(abs(u - x) < .07 and abs(v - .5) < .07 for x in (.27, .5, .73)) or (abs(v - .7) < .03 and .2 < u < .8),
    "deck": lambda u, v: ring(.5, .5, .2, .05)(u, v) or math.hypot(u - .5, v - .3) < .05,
}
COLORS = {"alarm": ((229, 72, 77), (255, 120, 90)), "sentinel": ((42, 120, 214), (91, 155, 240)),
          "dock": ((98, 80, 214), (144, 133, 233)), "deck": ((16, 150, 110), (27, 175, 122))}

for name, glyph in GLYPHS.items():
    imgs = [png(s, make(*COLORS[name], glyph)) for s in (256, 64, 32)]
    header = struct.pack("<HHH", 0, 1, len(imgs))
    offset, entries, data = 6 + 16 * len(imgs), b"", b""
    for s, img in zip((256, 64, 32), imgs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(img), offset + len(data))
        data += img
    open(os.path.join(OUT, f"{name}.ico"), "wb").write(header + entries + data)
    print("wrote", name)
