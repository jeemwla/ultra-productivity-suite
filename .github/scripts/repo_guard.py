"""Repository hygiene checks run by CI before the test suites.

Fails (exit 1) when a commit would publish something that must stay local, or when
the release version is out of step between VERSION and the desktop app manifests.
Standard library only; run from anywhere with:  python -I .github/scripts/repo_guard.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]

# Folders and files that are private, generated or per-PC (see .gitignore). They must never be tracked.
BLOCKED_PREFIXES = ("private/", "build/", ".devdata/", "logs/", "_progress/",
                    "ultra_dock/src-tauri/target/", "ultra_dock/src-tauri/gen/", "node_modules/")
BLOCKED_NAMES = {".env", "device_config.json", "code_red_siren.wav"}
# Installers and native binaries come from the build, never from source control.
BLOCKED_SUFFIXES = {".exe", ".msi", ".dll", ".pdb", ".pyd", ".log", ".orig"}
MAX_TRACKED_BYTES = 5 * 1024 * 1024


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], check=True,
                         capture_output=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def check_paths(paths: list[str]) -> list[str]:
    problems = []
    for p in paths:
        pure = PurePosixPath(p)
        lower = p.lower()
        if any(lower.startswith(prefix) or f"/{prefix}" in lower for prefix in BLOCKED_PREFIXES):
            problems.append(f"{p}: private, generated or build output must not be tracked")
        elif pure.name.lower() in BLOCKED_NAMES or "/data/" in f"/{lower}":
            problems.append(f"{p}: per-PC data or private file must not be tracked")
        elif pure.suffix.lower() in BLOCKED_SUFFIXES:
            problems.append(f"{p}: binaries, installers and logs come from the build, not from git")
        elif "dev-setup" in lower or "(dev)_" in lower:
            problems.append(f"{p}: Dev installers are owner-only and must never be uploaded")
        else:
            full = ROOT / p
            if full.is_file() and full.stat().st_size > MAX_TRACKED_BYTES:
                problems.append(f"{p}: larger than {MAX_TRACKED_BYTES // (1024 * 1024)} MB")
    return problems


def check_versions() -> list[str]:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        return [f"VERSION: '{version}' is not MAJOR.MINOR.PATCH"]
    tauri_dir = ROOT / "ultra_dock" / "src-tauri"
    found = {
        "ultra_dock/src-tauri/tauri.conf.json": json.loads(
            (tauri_dir / "tauri.conf.json").read_text(encoding="utf-8"))["version"],
        "ultra_dock/src-tauri/Cargo.toml": tomllib.loads(
            (tauri_dir / "Cargo.toml").read_text(encoding="utf-8"))["package"]["version"],
    }
    lock = tomllib.loads((tauri_dir / "Cargo.lock").read_text(encoding="utf-8"))
    name = tomllib.loads((tauri_dir / "Cargo.toml").read_text(encoding="utf-8"))["package"]["name"]
    found["ultra_dock/src-tauri/Cargo.lock"] = next(
        (pkg["version"] for pkg in lock.get("package", []) if pkg.get("name") == name), "missing")
    return [f"{path}: version {value} does not match VERSION {version}"
            for path, value in found.items() if value != version]


def check_json(paths: list[str]) -> list[str]:
    problems = []
    for p in paths:
        if p.endswith(".json"):
            try:
                json.loads((ROOT / p).read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                problems.append(f"{p}: not valid JSON ({exc.__class__.__name__}: {exc})")
    return problems


def main() -> int:
    paths = tracked_files()
    problems = check_paths(paths) + check_versions() + check_json(paths)
    for line in problems:
        print(f"::error::{line}")
    print(f"Checked {len(paths)} tracked files: {len(problems)} problem(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
