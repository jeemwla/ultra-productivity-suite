"""Check the prepared build interpreter against exact build-tool pins, and record the build inputs.

Standard library only. Installs nothing: it reads installed package metadata of the interpreter that runs it, so
run it with the same Python the build uses.

    python -I tools/check_build_env.py --repo <repo> --requirements <repo>\\requirements-build.txt
                                       --record <repo>\\build\\build-environment.json [--allow-dirty]

Refuses (exit 2) when a pinned package is missing or has another version, when the pins file is malformed, when
node, npm, rustc or cargo cannot be run, or when tracked files have uncommitted changes (unless --allow-dirty). The record holds the source commit, tracked changes,
interpreter version, every installed distribution as name==version, and tool versions. It holds no environment
variables, and the user profile prefix is replaced with %USERPROFILE%.
"""
import argparse
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys

PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.+!_-]*)$")


class EnvError(Exception):
    pass


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(path):
    pins = {}
    with open(path, encoding="utf-8") as f:
        for number, raw in enumerate(f, 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            m = PIN.match(line)
            if not m:
                raise EnvError(f"{os.path.basename(path)} line {number}: expected an exact pin such as name==1.2.3")
            key = canonical(m.group(1))
            if key in pins:
                raise EnvError(f"{os.path.basename(path)} line {number}: {m.group(1)} is pinned twice")
            pins[key] = (m.group(1), m.group(2))
    if not pins:
        raise EnvError(f"{os.path.basename(path)} has no pins")
    return pins


def installed():
    found = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            found.setdefault(canonical(name), (name, dist.version))
    return found


def check_pins(pins, have):
    problems = []
    for key, (name, version) in sorted(pins.items()):
        if key not in have:
            problems.append(f"{name} is not installed (pinned {version})")
        elif have[key][1] != version:
            problems.append(f"{name} is {have[key][1]}, pinned {version}")
    if problems:
        raise EnvError("build tools do not match requirements-build.txt: " + "; ".join(problems))


def _private(text):
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    if home and home != "~":
        text = re.sub(re.escape(home), "%USERPROFILE%", text, flags=re.IGNORECASE)
    return text


def tool_version(args):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (r.stdout or r.stderr).strip().splitlines()
    return _private(out[0]) if r.returncode == 0 and out else None


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise EnvError(f"git {' '.join(args)} failed")
    return r.stdout


def build_record(repo, pins, have):
    commit = git(repo, "rev-parse", "HEAD").strip()
    changed = [line[3:] for line in git(repo, "status", "--porcelain", "--untracked-files=no").splitlines() if line]
    return {
        "format": 1,
        "source": {"commit": commit, "trackedChanges": changed},
        "python": {"executable": _private(sys.executable), "version": sys.version.split()[0],
                   "implementation": platform.python_implementation()},
        "platform": platform.platform(),
        "pinned": {name: version for name, version in sorted(pins.values())},
        "installed": sorted(f"{name}=={version}" for name, version in have.values()),
        "tools": {"node": tool_version(["node", "--version"]), "npm": tool_version(["npm.cmd" if os.name == "nt" else "npm", "--version"]),
                  "rustc": tool_version(["rustc", "--version"]), "cargo": tool_version(["cargo", "--version"])},
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Check exact build-tool pins and record build inputs.")
    p.add_argument("--repo", required=True)
    p.add_argument("--requirements", required=True)
    p.add_argument("--record", required=True)
    p.add_argument("--allow-dirty", action="store_true")
    a = p.parse_args(argv)
    try:
        for value, flag in ((a.repo, "--repo"), (a.requirements, "--requirements"), (a.record, "--record")):
            if not re.match(r"^[A-Za-z]:[\\/]", value):
                raise EnvError(f"{flag} must be a full path such as D:\\folder")
        pins = read_pins(a.requirements)
        have = installed()
        check_pins(pins, have)
        record = build_record(a.repo, pins, have)
        missing = sorted(name for name, version in record["tools"].items() if not version)
        if missing:
            raise EnvError("required build tool(s) not found on PATH: " + ", ".join(missing))
        if record["source"]["trackedChanges"] and not a.allow_dirty:
            raise EnvError(f"{len(record['source']['trackedChanges'])} tracked file(s) have uncommitted changes; commit "
                           "them or pass -AllowDirty (the record then lists them)")
        tmp = a.record + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(record, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, a.record)
    except (EnvError, OSError) as exc:
        print(f"build environment refused: {exc}", file=sys.stderr)
        return 2
    print(f"build tools match {len(pins)} pins; source {record['source']['commit'][:12]}"
          + (" with tracked changes" if record["source"]["trackedChanges"] else ""))
    print(f"record {a.record}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
