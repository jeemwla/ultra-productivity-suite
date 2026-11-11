"""Draws the Ultra Suite app icons (one look per app) with Pillow.

Outputs, for each app:
  src-tauri/appicons/<app>.ico      Start menu shortcut icon (256, 128, 64, 48, 32, 24, 16)
  src-tauri/icons/apps/<app>.png    256 px window icon, compiled into the desktop app
  core/static/icons/<app>.png       64 px favicon served to each app page

Run:  python ultra_dock/tools/make_icons.py
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
TAURI = os.path.join(HERE, "..", "src-tauri")
ROOT = os.path.join(HERE, "..", "..")
S = 1024  # drawn large, scaled down for crisp edges


def gradient(c1, c2):
    g = Image.new("RGBA", (S, S))
    px = g.load()
    for y in range(S):
        for x in range(S):
            t = (x * 0.35 + y * 0.65) / S
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)) + (255,)
    return g


def squircle_mask(r=0.235):
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle((24, 24, S - 24, S - 24), radius=int(S * r), fill=255)
    return m


def base(c1, c2):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bg = gradient(c1, c2)
    # soft light from the top left, like a real lit surface
    glow = Image.new("L", (S, S), 0)
    ImageDraw.Draw(glow).ellipse((-S * 0.35, -S * 0.55, S * 0.95, S * 0.55), fill=42)
    glow = glow.filter(ImageFilter.GaussianBlur(S * 0.08))
    bg = Image.composite(Image.new("RGBA", (S, S), (255, 255, 255, 255)), bg, glow)
    img.paste(bg, (0, 0), squircle_mask())
    # thin inner edge highlight
    edge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle((26, 26, S - 26, S - 26), radius=int(S * 0.235), outline=(255, 255, 255, 46), width=6)
    return Image.alpha_composite(img, edge)


def shadowed(glyph, blur=18, alpha=70, dy=14):
    """Adds a soft drop shadow under a white glyph layer."""
    a = glyph.split()[3]
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sh.putalpha(a.point(lambda v: v * alpha // 255).filter(ImageFilter.GaussianBlur(blur)))
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(sh, (0, dy), sh)
    return Image.alpha_composite(out, glyph)


W = (255, 255, 255, 255)
W2 = (255, 255, 255, 150)


def sentinel():
    g = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(g)
    # shield
    pts = [(512, 190), (790, 290), (770, 560), (512, 840), (254, 560), (234, 290)]
    d.polygon(pts, fill=(255, 255, 255, 60))
    d.line(pts + [pts[0]], fill=W, width=46, joint="curve")
    # heartbeat line across the shield
    line = [(330, 520), (410, 520), (460, 400), (540, 650), (600, 460), (635, 520), (695, 520)]
    d.line(line, fill=W, width=50, joint="curve")
    for p in (line[0], line[-1]):
        d.ellipse((p[0] - 25, p[1] - 25, p[0] + 25, p[1] + 25), fill=W)
    return shadowed(g)


def alarm():
    g = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(g)
    # bells
    d.chord((215, 200, 445, 430), 150, 300, fill=W)
    d.chord((579, 200, 809, 430), 240, 30, fill=W)
    # legs
    d.line([(355, 770), (300, 835)], fill=W, width=46); d.line([(669, 770), (724, 835)], fill=W, width=46)
    # face
    d.ellipse((262, 290, 762, 790), fill=(255, 255, 255, 60), outline=W, width=46)
    # hands
    d.line([(512, 540), (512, 395)], fill=W, width=44); d.line([(512, 540), (615, 605)], fill=W, width=44)
    d.ellipse((482, 510, 542, 570), fill=W)
    return shadowed(g)


def insights():
    g = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(g)
    bars = [(250, 600), (420, 480), (590, 380)]
    for x, top in bars:
        d.rounded_rectangle((x, top, x + 120, 790), radius=34, fill=W2)
    # rising trend with a highlighted point
    pts = [(250, 520), (420, 400), (590, 300), (780, 230)]
    d.line(pts, fill=W, width=48, joint="curve")
    for p in pts[:-1]:
        d.ellipse((p[0] - 30, p[1] - 30, p[0] + 30, p[1] + 30), fill=W)
    d.ellipse((780 - 58, 230 - 58, 780 + 58, 230 + 58), fill=W)
    d.ellipse((780 - 26, 230 - 26, 780 + 26, 230 + 26), fill=(124, 58, 237, 255))
    return shadowed(g)


def dock():
    g = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(g)
    # phone-style folder: four app tiles in a frosted plate
    d.rounded_rectangle((210, 210, 814, 814), radius=150, fill=(255, 255, 255, 52), outline=(255, 255, 255, 120), width=10)
    colors = [(255, 255, 255, 255), (255, 214, 102, 255), (125, 211, 252, 255), (252, 165, 165, 255)]
    for i, (x, y) in enumerate([(275, 275), (537, 275), (275, 537), (537, 537)]):
        d.rounded_rectangle((x, y, x + 212, y + 212), radius=58, fill=colors[i])
    return shadowed(g)


APPS = {
    "sentinel": ((29, 78, 216), (6, 182, 212), sentinel),
    "alarm": ((220, 38, 38), (249, 115, 22), alarm),
    "insights": ((124, 58, 237), (236, 72, 153), insights),
    "dock": ((17, 24, 39), (79, 70, 229), dock),
}


def main():
    ico_dir = os.path.join(TAURI, "appicons"); png_dir = os.path.join(TAURI, "icons", "apps"); fav_dir = os.path.join(ROOT, "core", "static", "icons")
    for p in (ico_dir, png_dir, fav_dir):
        os.makedirs(p, exist_ok=True)
    for name, (c1, c2, glyph) in APPS.items():
        img = Image.alpha_composite(base(c1, c2), glyph())
        big = img.resize((256, 256), Image.LANCZOS)
        big.save(os.path.join(png_dir, f"{name}.png"))
        img.resize((64, 64), Image.LANCZOS).save(os.path.join(fav_dir, f"{name}.png"))
        big.save(os.path.join(ico_dir, f"{name}.ico"), sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])
        print("wrote", name)
    # the suite's own app icon (installer, main exe) uses the Dock design
    dock_img = Image.open(os.path.join(png_dir, "dock.png"))
    full = Image.alpha_composite(base(*APPS["dock"][:2]), APPS["dock"][2]())
    icons = os.path.join(TAURI, "icons")
    for fname, px in (("32x32.png", 32), ("64x64.png", 64), ("128x128.png", 128), ("128x128@2x.png", 256), ("icon.png", 512),
                      ("Square30x30Logo.png", 30), ("Square44x44Logo.png", 44), ("Square71x71Logo.png", 71), ("Square89x89Logo.png", 89),
                      ("Square107x107Logo.png", 107), ("Square142x142Logo.png", 142), ("Square150x150Logo.png", 150),
                      ("Square284x284Logo.png", 284), ("Square310x310Logo.png", 310), ("StoreLogo.png", 50)):
        full.resize((px, px), Image.LANCZOS).save(os.path.join(icons, fname))
    dock_img.save(os.path.join(icons, "icon.ico"), sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])
    # a contact sheet to look at all icons together
    sheet = Image.new("RGBA", (5 * 280 + 20, 300), (24, 24, 27, 255))
    for i, name in enumerate(APPS):
        sheet.alpha_composite(Image.open(os.path.join(png_dir, f"{name}.png")), (20 + i * 280, 22))
    sheet.save(os.path.join(png_dir, "_preview.png"))


if __name__ == "__main__":
    main()
