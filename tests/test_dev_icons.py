"""Dev icon source, shipped pixels, and explicit small ICO frame regressions."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageStat

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("make_icons", ROOT / "ultra_dock/tools/make_icons.py")
ICONS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ICONS)
DEV = ROOT / "ultra_dock/src-tauri/icons/dev"
LANDMARKS = {
    "sentinel": ((512, 190), (460, 400), (540, 650), (512, 820)),
    "alarm": ((512, 290), (512, 420), (600, 600), (512, 770)),
    "insights": ((310, 700), (480, 650), (650, 550), (420, 400)),
    "dock": ((381, 381), (643, 381), (381, 643), (643, 643)),
}


def luminance(value):
    value /= 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def contrast(a, b):
    low, high = sorted((luminance(a), luminance(b)))
    return (high + 0.05) / (low + 0.05)


def on_background(image, gray):
    return Image.alpha_composite(Image.new("RGBA", image.size, (gray, gray, gray, 255)), image)


class DevIcons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        # Public output sentinels prove Dev generation does not overwrite any
        # Public directory, including the ignored shortcut ICO directory.
        cls.public = {}
        for directory in ("ultra_dock/src-tauri/icons/apps", "ultra_dock/src-tauri/appicons",
                          "ultra_dock/ui/hubicons", "core/static/icons"):
            path = cls.root / directory / "sentinel.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"Public output fixture: do not overwrite")
            cls.public[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch("sys.stdout", new=io.StringIO()):
            ICONS.make_dev_icons(cls.root, cls.root / "previews")

    def test_generation_is_scoped_and_complete_and_preserves_public_outputs(self):
        expected = set(self.public)
        icons = self.root / "ultra_dock/src-tauri/icons/dev"
        for name in ICONS.APPS:
            expected.add(self.root / "ultra_dock/src-tauri/appicons-dev" / f"{name}.ico")
            expected.add(icons / "apps" / f"{name}.png")
            for size in ICONS.ICO_SIZES:
                expected.add(icons / "apps" / name / f"{size}.png")
            for directory in ("core/static/icons/dev", "ultra_dock/ui/hubicons/dev"):
                expected.add(self.root / directory / f"{name}.png")
        expected.update(icons / name for name in ICONS.DEV_SUITE_PNGS)
        expected.add(icons / "icon.ico")
        expected.update(self.root / "previews" / f"dev-{size}.png" for size in ("small", "large"))
        self.assertEqual({p for p in self.root.rglob("*") if p.is_file()}, expected)
        for path, before in self.public.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_every_committed_dev_png_matches_fresh_generation(self):
        count = 0
        for directory in ("ultra_dock/src-tauri/icons/dev", "ultra_dock/ui/hubicons/dev", "core/static/icons/dev"):
            generated = self.root / directory
            self.assertEqual({p.relative_to(generated) for p in generated.rglob("*.png")},
                             {p.relative_to(ROOT / directory) for p in (ROOT / directory).rglob("*.png")})
            for expected in generated.rglob("*.png"):
                actual = ROOT / expected.relative_to(self.root)
                self.assertEqual(actual.read_bytes(), expected.read_bytes(), str(actual))
                count += 1
        self.assertEqual(count, 55)
        self.assertEqual((DEV / "icon.ico").read_bytes(),
                         (self.root / "ultra_dock/src-tauri/icons/dev/icon.ico").read_bytes())

    def test_grayscale_sizes_transparency_and_matching_outputs(self):
        for name in ICONS.APPS:
            for size in ICONS.ICO_SIZES:
                with self.subTest(app=name, size=size), Image.open(DEV / "apps" / name / f"{size}.png") as image:
                    self.assertEqual(image.size, (size, size))
                    self.assertEqual(image.mode, "RGBA")
                    # Lanczos can leave up to 2/255 coverage in the corner.
                    self.assertLessEqual(image.getpixel((0, 0))[3], 2)
                    self.assertEqual(image.getpixel((size // 2, size // 2))[3], 255)
            for directory, size in ((DEV / "apps", 256), (ROOT / "core/static/icons/dev", 64),
                                    (ROOT / "ultra_dock/ui/hubicons/dev", 256)):
                self.assertEqual((directory / f"{name}.png").read_bytes(),
                                 (DEV / "apps" / name / f"{size}.png").read_bytes())
        for path in DEV.rglob("*.png"):
            with Image.open(path) as image:
                red, green, blue, alpha = image.split()
                self.assertEqual(ImageChops.difference(red, green).getextrema(), (0, 0), str(path))
                self.assertEqual(ImageChops.difference(red, blue).getextrema(), (0, 0), str(path))
        for filename, size in ICONS.DEV_SUITE_PNGS.items():
            with Image.open(DEV / filename) as image:
                self.assertEqual(image.size, (size, size))

    def test_ico_contains_exact_independent_png_frames(self):
        for name in ICONS.APPS:
            path = self.root / "ultra_dock/src-tauri/appicons-dev" / f"{name}.ico"
            with Image.open(path) as ico:
                self.assertEqual(ico.ico.sizes(), {(s, s) for s in ICONS.ICO_SIZES})
                for size in ICONS.ICO_SIZES:
                    with Image.open(DEV / "apps" / name / f"{size}.png") as png:
                        self.assertEqual(ico.ico.getimage((size, size)).convert("RGBA").tobytes(), png.tobytes())
        with Image.open(DEV / "icon.ico") as ico:
            for size in ICONS.ICO_SIZES:
                with Image.open(DEV / "apps/dock" / f"{size}.png") as png:
                    self.assertEqual(ico.ico.getimage((size, size)).convert("RGBA").tobytes(), png.tobytes())

    def test_glyph_geometry_and_distinct_gray_details_are_preserved(self):
        for name, (_, _, draw) in ICONS.APPS.items():
            self.assertEqual(draw().getchannel("A").tobytes(), ICONS.dev_glyph(name).getchannel("A").tobytes(), name)
        glyph = ICONS.dev_glyph("dock")
        levels = [glyph.getpixel(p)[0] for p in LANDMARKS["dock"]]
        self.assertEqual(levels, [255, 224, 192, 160])
        insights = ICONS.dev_glyph("insights")
        self.assertGreater(insights.getpixel((780, 230))[0], 144)
        self.assertLess(insights.getpixel((780, 230))[0], insights.getpixel((780, 190))[0])

    def test_ico_writer_fails_if_pillow_discards_explicit_small_frames(self):
        frames = {}
        for size in ICONS.ICO_SIZES:
            with Image.open(DEV / "apps/dock" / f"{size}.png") as image:
                frames[size] = image.copy()
        original_save = Image.Image.save

        def discard_frames(image, path, *args, **kwargs):
            kwargs.pop("append_images", None)
            return original_save(image, path, *args, **kwargs)

        with patch.object(Image.Image, "save", discard_frames):
            with self.assertRaisesRegex(RuntimeError, "preserve the supplied 16px"):
                ICONS.save_dev_ico(self.root / "discarded-frames.ico", frames)
        (self.root / "discarded-frames.ico").unlink()

    def test_small_rim_interior_and_separate_glyph_landmarks_have_contrast(self):
        # Individual probes, never an average across an icon. Landmark checks
        # detect missing strokes, but visual review is still required for reading.
        for name in ICONS.APPS:
            for size in (16, 24, 32):
                with self.subTest(app=name, size=size), Image.open(DEV / "apps" / name / f"{size}.png") as image:
                    dark = on_background(image, 32)
                    light = on_background(image, 243)
                    for point in ((size // 2, 1), (1, size // 2), (size - 2, size // 2), (size // 2, size - 2)):
                        self.assertGreaterEqual(contrast(dark.getpixel(point)[0], 32), 3, (name, size, "rim", point))
                    interior = (size // 2, round(size * 0.14))
                    self.assertGreaterEqual(contrast(light.getpixel(interior)[0], 243), 3, (name, size, "interior"))
                    tile = ICONS.dev_base(True).resize((size, size), Image.Resampling.LANCZOS)
                    for x, y in LANDMARKS[name]:
                        x, y = int(x * size / ICONS.S), int(y * size / ICONS.S)
                        box = (max(0, x - 1), max(0, y - 1), min(size, x + 2), min(size, y + 2))
                        peak = max(dark.getpixel((px, py))[0] for py in range(box[1], box[3]) for px in range(box[0], box[2]))
                        self.assertGreaterEqual(contrast(peak, tile.getpixel((x, y))[0]), 3, (name, size, "glyph", x, y))

    def test_small_frames_are_not_resized_256_frames_and_public_is_colored(self):
        for name in ICONS.APPS:
            with Image.open(DEV / "apps" / f"{name}.png") as large:
                for size in (16, 24, 32):
                    with Image.open(DEV / "apps" / name / f"{size}.png") as small:
                        self.assertNotEqual(large.resize(small.size, Image.Resampling.LANCZOS).tobytes(), small.tobytes())
            with Image.open(ROOT / "ultra_dock/src-tauri/icons/apps" / f"{name}.png") as public:
                red, green, blue, _ = public.split()
                self.assertGreater(ImageStat.Stat(ImageChops.difference(red, blue)).mean[0], 10)


if __name__ == "__main__":
    unittest.main()
