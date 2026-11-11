"""Copy an exact allowlist of repository files into a clean package stage, with a SHA-256 inventory.

Standard library only. Meant to run before PyInstaller so it reads only listed files, never whole folders.

    python -I tools/package_stage.py --repo D:\\path\\repo --manifest D:\\path\\repo\\tools\\package_manifest.json
                                     --stage D:\\path\\repo\\build\\stage

Layout of a finished stage (the stage folder is the unit that is promoted atomically):

    <stage>\\payload\\...          staged files, at the same relative paths as in the repository
    <stage>\\inventory.sha256     "<sha256>  <bytes>  <relative path>" per file, sorted, outside the payload

Prints only the file count and the inventory path. Never prints file contents.

After PyInstaller, check that the packaged app folders hold exactly the staged files, byte for byte:

    python -I tools/package_stage.py --stage <repo>\\build\\stage --verify-output <repo>\\build\\dist\\ultrasuite-core\\_internal

This checks file names, locations, link status and hashes. It does not inspect whether file contents are secret-free.

Exit codes: 0 staged, 2 refused (bad input, unsafe path, forbidden or missing file, source changed while copying).
"""
import argparse
import hashlib
import json
import ntpath
import os
import re
import stat
import sys
import uuid

MANIFEST_VERSION = 1
PAYLOAD = "payload"
INVENTORY = "inventory.sha256"
INVENTORY_HEADER = "# ultrasuite package inventory v1: sha256  bytes  path\n"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 2000
MAX_CLEANUP_ENTRIES = 5000
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

# Refused even when the manifest lists them. Compared case-insensitively.
FORBIDDEN_SEGMENTS = {
    "private", "data", ".devdata", "_progress", "logs", "__pycache__", "node_modules", ".git", ".venv", "venv",
    "build", "dist", "target", "gen", "appicons", ".pytest_cache", ".mypy_cache",
}
FORBIDDEN_NAMES = {
    ".env", "settings.env", "device_config.json", "code_red_siren.wav", ".netrc", ".npmrc", ".pypirc", ".git-credentials",
    "id_rsa", "id_ecdsa", "id_ed25519", "known_hosts", "thumbs.db", "desktop.ini",
}
FORBIDDEN_SUFFIXES = (
    ".env", ".pyc", ".pyo", ".orig", ".rej", ".bak", ".old", ".tmp", ".temp", ".swp", ".log", ".db", ".db-wal",
    ".db-shm", ".db-journal", ".sqlite", ".sqlite3", ".wal", ".shm", ".pem", ".key", ".pfx", ".p12", ".crt", ".cer",
    ".kdbx", ".spec", "~",
)
FORBIDDEN_NAME_PARTS = ("credential", "secret", "password", "passwd", "token", ".env.")
WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?$", re.IGNORECASE)
BAD_CHARS = set('<>:"|?*\\') | {chr(c) for c in range(32)}
SHORT_NAME = re.compile(r"~[0-9]")


class StageError(Exception):
    """A refusal. The message names paths and reasons only, never file contents."""


# ---------------------------------------------------------------- paths

def _is_reparse(st):
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT) or stat.S_ISLNK(st.st_mode)


def _key(path):
    return os.path.normcase(os.path.normpath(path))


def _absolute(path, what):
    """Accept only a fully qualified local or UNC path; refuse relative and drive-relative forms."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise StageError(f"{what}: path is empty or invalid")
    if os.name == "nt":
        drive, rest = ntpath.splitdrive(path)
        if not drive or not rest.startswith(("\\", "/")):
            raise StageError(f"{what}: must be a full path such as D:\\folder (got a relative or drive-relative path)")
        if path.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
            raise StageError(f"{what}: device or extended-length paths are not accepted")
    elif not os.path.isabs(path):
        raise StageError(f"{what}: must be an absolute path")
    return os.path.normpath(path)


def _strict_child(child, parent):
    c, p = _key(child), _key(parent)
    return c != p and c.startswith(p.rstrip("\\/") + os.sep)


def _lstat(path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _assert_real_dir(path, what):
    st = _lstat(path)
    if st is None:
        raise StageError(f"{what} does not exist: {path}")
    if _is_reparse(st):
        raise StageError(f"{what} is a link, junction or other reparse point: {path}")
    if not stat.S_ISDIR(st.st_mode):
        raise StageError(f"{what} is not a folder: {path}")
    return st


def _assert_no_link_ancestry(path, what):
    """The resolved path must equal the given path, so no link or junction sits anywhere above it."""
    if _key(os.path.realpath(path)) != _key(path):
        raise StageError(f"{what} resolves elsewhere (a link, junction or short name is in its path): {path}")


def _walk_components(base, relative_parts, what):
    """lstat every component below base; refuse reparse points. Returns the stat of the last one (or None)."""
    current, st = base, None
    for part in relative_parts:
        current = os.path.join(current, part)
        st = _lstat(current)
        if st is None:
            return None
        if _is_reparse(st):
            raise StageError(f"{what}: {current} is a link, junction or other reparse point")
    return st


# ---------------------------------------------------------------- manifest

def _no_duplicate_keys(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise StageError(f"manifest has a duplicate key: {k}")
        seen[k] = v
    return seen


def check_entry(entry):
    """Validate one manifest entry; returns its path segments. Purely name-based (no file system access)."""
    if not isinstance(entry, str) or not entry:
        raise StageError("manifest entry is not a non-empty string")
    if entry != entry.strip():
        raise StageError(f"manifest entry has leading or trailing spaces: {entry!r}")
    if entry.startswith("/") or "\\" in entry or ":" in entry:
        raise StageError(f"manifest entry must be repository-relative with forward slashes: {entry!r}")
    parts = entry.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise StageError(f"manifest entry has an empty, '.' or '..' segment: {entry!r}")
        if set(part) & BAD_CHARS:
            raise StageError(f"manifest entry has a character Windows does not allow: {entry!r}")
        if part[-1] in ". ":
            raise StageError(f"manifest entry has a segment ending in a dot or space: {entry!r}")
        if WINDOWS_RESERVED.match(part):
            raise StageError(f"manifest entry uses a reserved Windows device name: {entry!r}")
        if SHORT_NAME.search(part):
            raise StageError(f"manifest entry looks like a short (8.3) name alias: {entry!r}")
    lowered = [p.lower() for p in parts]
    for seg in lowered[:-1]:
        if seg in FORBIDDEN_SEGMENTS:
            raise StageError(f"manifest entry is inside a forbidden folder ({seg}): {entry!r}")
    name = lowered[-1]
    if name in FORBIDDEN_SEGMENTS or name in FORBIDDEN_NAMES or name.endswith(FORBIDDEN_SUFFIXES) \
            or any(part in name for part in FORBIDDEN_NAME_PARTS) or name.startswith(".env"):
        raise StageError(f"manifest entry names a forbidden file type (private, data, cache, credential, log, "
                         f"database or backup): {entry!r}")
    return parts


def load_manifest(manifest_path):
    st = _lstat(manifest_path)
    if st is None or not stat.S_ISREG(st.st_mode) or _is_reparse(st):
        raise StageError(f"manifest is missing or not a regular file: {manifest_path}")
    if st.st_size > 1024 * 1024:
        raise StageError("manifest is larger than 1 MB")
    with open(manifest_path, "rb") as f:
        raw = f.read()
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as exc:
        raise StageError(f"manifest is not valid UTF-8 JSON: {exc.__class__.__name__}") from None
    if not isinstance(data, dict) or set(data) != {"version", "files"}:
        raise StageError('manifest must be an object with exactly "version" and "files"')
    if data["version"] != MANIFEST_VERSION or isinstance(data["version"], bool):
        raise StageError(f"manifest version must be {MANIFEST_VERSION}")
    files = data["files"]
    if not isinstance(files, list) or not files:
        raise StageError('manifest "files" must be a non-empty list')
    if len(files) > MAX_FILES:
        raise StageError(f"manifest lists more than {MAX_FILES} files")
    entries, seen = [], {}
    for entry in files:
        parts = check_entry(entry)
        folded = entry.casefold()
        if folded in seen:
            raise StageError(f"manifest lists the same destination twice (case-insensitive): {seen[folded]!r} and {entry!r}")
        seen[folded] = entry
        entries.append((entry, parts))
    for folded, entry in seen.items():
        parts = folded.split("/")
        for i in range(1, len(parts)):
            ancestor = "/".join(parts[:i])
            if ancestor in seen:
                raise StageError(f"manifest uses one path as both a file and a folder: {seen[ancestor]!r} and {entry!r}")
    return entries


# ---------------------------------------------------------------- safe cleanup

def _scan_tree(root, build_dir, limit=MAX_CLEANUP_ENTRIES):
    """List a tree for deletion without following anything. Refuses links anywhere and trees over the limit."""
    if not _strict_child(root, build_dir):
        raise StageError(f"refusing to delete outside the build folder: {root}")
    _assert_real_dir(root, "folder to delete")
    files, dirs, pending = [], [root], [root]
    while pending:
        current = pending.pop()
        with os.scandir(current) as it:
            for item in it:
                st = item.stat(follow_symlinks=False)
                if _is_reparse(st):
                    raise StageError(f"refusing to delete a tree that contains a link or junction: {item.path}")
                (dirs if stat.S_ISDIR(st.st_mode) else files).append(item.path)
                if stat.S_ISDIR(st.st_mode):
                    pending.append(item.path)
                if len(files) + len(dirs) > limit:
                    raise StageError(f"refusing to delete more than {limit} entries under {root}")
    return files, dirs


def remove_tree(root, build_dir, limit=MAX_CLEANUP_ENTRIES):
    """Delete a checked tree bottom-up. Nothing is deleted unless the whole tree passes the scan first."""
    files, dirs = _scan_tree(root, build_dir, limit)
    for path in files:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        os.unlink(path)
    for path in sorted(dirs, key=len, reverse=True):
        os.rmdir(path)


def _remove_file(path, build_dir):
    if not _strict_child(path, build_dir):
        raise StageError(f"refusing to delete outside the build folder: {path}")
    st = _lstat(path)
    if st is None:
        return
    if _is_reparse(st) or not stat.S_ISREG(st.st_mode):
        raise StageError(f"refusing to delete something that is not a plain file: {path}")
    os.unlink(path)


# ---------------------------------------------------------------- inventory

def format_inventory(rows):
    lines = [INVENTORY_HEADER] + [f"{digest}  {size}  {rel}\n" for rel, size, digest in sorted(rows)]
    return "".join(lines).encode("utf-8")


def _payload_files(payload):
    found = set()
    for current, dirnames, filenames in os.walk(payload):
        for name in filenames:
            found.add(os.path.relpath(os.path.join(current, name), payload).replace(os.sep, "/"))
    return found


def _check_existing_stage(stage, build_dir):
    """An existing stage may be replaced only if it looks exactly like one this tool produced."""
    _assert_real_dir(stage, "existing stage")
    names = set(os.listdir(stage))
    if names != {PAYLOAD, INVENTORY}:
        raise StageError(f"existing stage does not look like a package stage (expected only {PAYLOAD}\\ and "
                         f"{INVENTORY}); refusing to replace it: {stage}")
    _scan_tree(stage, build_dir)  # no links anywhere, bounded size
    inventory = os.path.join(stage, INVENTORY)
    st = _lstat(inventory)
    if st is None or not stat.S_ISREG(st.st_mode):
        raise StageError(f"existing stage inventory is not a plain file: {inventory}")
    if set(read_inventory(inventory)) != _payload_files(os.path.join(stage, PAYLOAD)):
        raise StageError(f"existing stage payload does not match its inventory; refusing to replace it: {stage}")


# ---------------------------------------------------------------- staging

def _read_source(path, before):
    """Read a source file once, refusing if the opened file is not the one that was checked."""
    with open(path, "rb") as f:
        opened = os.fstat(f.fileno())
        if (opened.st_ino, opened.st_dev) != (before.st_ino, before.st_dev) or not stat.S_ISREG(opened.st_mode):
            raise StageError(f"source changed identity between check and open: {path}")
        data = f.read(MAX_FILE_BYTES + 1)
    return data


def _plan_sources(repo, entries):
    planned = []
    for rel, parts in entries:
        st = _walk_components(repo, parts, f"source {rel}")
        if st is None:
            raise StageError(f"listed file is missing: {rel}")
        if not stat.S_ISREG(st.st_mode):
            raise StageError(f"listed path is not a regular file: {rel}")
        if st.st_size > MAX_FILE_BYTES:
            raise StageError(f"listed file is larger than {MAX_FILE_BYTES} bytes: {rel}")
        if getattr(st, "st_nlink", 1) > 1:
            raise StageError(f"listed file has more than one hard link (its contents may live elsewhere): {rel}")
        planned.append((rel, parts, os.path.join(repo, *parts), st))
    return planned


def stage_package(repo, manifest, stage):
    """Stage the listed files. Returns (file_count, inventory_path). Raises StageError on any refusal."""
    repo = _absolute(repo, "--repo")
    manifest = _absolute(manifest, "--manifest")
    stage = _absolute(stage, "--stage")

    _assert_real_dir(repo, "repository folder")
    _assert_no_link_ancestry(repo, "repository folder")
    build_dir = os.path.join(repo, "build")
    _assert_real_dir(build_dir, "repository build folder")
    if not _strict_child(stage, build_dir):
        raise StageError(f"--stage must be a folder inside {build_dir}: {stage}")
    if not _strict_child(manifest, repo) or _strict_child(manifest, build_dir):
        raise StageError("--manifest must be a file in the repository, outside the build folder")
    _walk_components(repo, os.path.relpath(manifest, repo).split(os.sep), "manifest")

    stage_parts = os.path.relpath(stage, build_dir).split(os.sep)
    if any(p in ("", ".", "..") for p in stage_parts):
        raise StageError(f"--stage path is not a plain child of the build folder: {stage}")
    parent = build_dir
    for part in stage_parts[:-1]:  # create missing intermediate folders one checked step at a time
        parent = os.path.join(parent, part)
        if _lstat(parent) is None:
            os.mkdir(parent)
        _assert_real_dir(parent, "stage parent folder")
    _assert_no_link_ancestry(parent, "stage parent folder")

    entries = load_manifest(manifest)
    planned = _plan_sources(repo, entries)

    existing = _lstat(stage)
    if existing is not None:
        _check_existing_stage(stage, build_dir)

    token = uuid.uuid4().hex
    name = stage_parts[-1]
    work = os.path.join(parent, f".{name}.tmp-{token}")
    old = os.path.join(parent, f".{name}.old-{token}")
    os.mkdir(work)
    promoted = False
    try:
        payload = os.path.join(work, PAYLOAD)
        os.mkdir(payload)
        rows = []
        for rel, parts, source, before in planned:
            data = _read_source(source, before)
            if len(data) > MAX_FILE_BYTES:
                raise StageError(f"listed file grew past {MAX_FILE_BYTES} bytes while staging: {rel}")
            after = _walk_components(repo, parts, f"source {rel}")
            if after is None or (after.st_size, after.st_mtime_ns, after.st_ino) != (before.st_size, before.st_mtime_ns, before.st_ino) \
                    or len(data) != before.st_size:
                raise StageError(f"source changed while it was being staged: {rel}")
            digest = hashlib.sha256(data).hexdigest()
            target = os.path.join(payload, *parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "xb") as out:
                out.write(data)
            with open(target, "rb") as check:
                if hashlib.sha256(check.read()).hexdigest() != digest:
                    raise StageError(f"staged copy does not match its source digest: {rel}")
            rows.append((rel, len(data), digest))

        _scan_tree(work, build_dir)
        if _payload_files(payload) != {rel for rel, _, _ in rows}:
            raise StageError("staged payload contains files that were not listed")
        inventory_bytes = format_inventory(rows)
        with open(os.path.join(work, INVENTORY), "xb") as inv:
            inv.write(inventory_bytes)

        if existing is not None:
            os.rename(stage, old)
        try:
            os.rename(work, stage)
        except OSError:
            if existing is not None:
                os.rename(old, stage)
            raise
        promoted = True
    finally:
        if not promoted and _lstat(work) is not None:
            remove_tree(work, build_dir)
    if existing is not None:
        remove_tree(old, build_dir)
    return len(rows), os.path.join(stage, INVENTORY)


def read_inventory(path):
    """Full rows {relative path: (bytes, sha256)} of an inventory this tool wrote."""
    rows = {}
    with open(path, "r", encoding="utf-8", newline="\n") as f:
        lines = f.read().split("\n")
    if not lines or lines[0] + "\n" != INVENTORY_HEADER:
        raise StageError(f"inventory has an unexpected format: {path}")
    for line in lines[1:]:
        if not line:
            continue
        pieces = line.split("  ", 2)
        if len(pieces) != 3 or not re.fullmatch(r"[0-9a-f]{64}", pieces[0]) or not pieces[1].isdigit():
            raise StageError(f"inventory has an unexpected line format: {path}")
        check_entry(pieces[2])
        rows[pieces[2]] = (int(pieces[1]), pieces[0])
    if not rows:
        raise StageError(f"inventory lists no files: {path}")
    return rows


def verify_output(stage, output):
    """Check a packaged app folder (PyInstaller's _internal) against the stage inventory.

    Every top-level app folder named in the inventory must hold exactly the inventoried files with the same bytes, and
    every inventoried top-level file must match. Other folders (Python runtime, libraries) are not judged here.
    Returns the number of verified files. Refusals list file names only, never contents.
    """
    stage = _absolute(stage, "--stage")
    output = _absolute(output, "--verify-output")
    _assert_real_dir(output, "packaged output folder")
    inventory = os.path.join(stage, INVENTORY)
    st = _lstat(inventory)
    if st is None or not stat.S_ISREG(st.st_mode) or _is_reparse(st):
        raise StageError(f"stage inventory is missing or not a plain file: {inventory}")
    rows = read_inventory(inventory)
    tops = sorted({rel.split("/", 1)[0] for rel in rows if "/" in rel})
    found = {}
    for top in tops:
        top_path = os.path.join(output, top)
        _assert_real_dir(top_path, f"packaged folder {top}")
        pending = [top_path]
        while pending:
            current = pending.pop()
            with os.scandir(current) as it:
                for item in it:
                    ist = item.stat(follow_symlinks=False)
                    rel = os.path.relpath(item.path, output).replace(os.sep, "/")
                    if _is_reparse(ist):
                        raise StageError(f"packaged output contains a link or junction: {rel}")
                    if stat.S_ISDIR(ist.st_mode):
                        pending.append(item.path)
                    else:
                        found[rel] = item.path
                    if len(found) > MAX_FILES:
                        raise StageError(f"packaged app folders hold more than {MAX_FILES} files")
    for rel in rows:
        if "/" not in rel:
            path = os.path.join(output, rel)
            rst = _lstat(path)
            if rst is not None and stat.S_ISREG(rst.st_mode) and not _is_reparse(rst):
                found[rel] = path
    extra = sorted(set(found) - set(rows))
    missing = sorted(set(rows) - set(found))
    if extra or missing:
        parts = []
        if extra:
            parts.append(f"{len(extra)} unlisted file(s): " + ", ".join(extra[:25]) + (" ..." if len(extra) > 25 else ""))
        if missing:
            parts.append(f"{len(missing)} missing file(s): " + ", ".join(missing[:25]) + (" ..." if len(missing) > 25 else ""))
        raise StageError("packaged output does not match the stage inventory; " + "; ".join(parts))
    for rel, (size, digest) in sorted(rows.items()):
        with open(found[rel], "rb") as f:
            data = f.read(MAX_FILE_BYTES + 1)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise StageError(f"packaged file differs from the staged file: {rel}")
    return len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stage an exact allowlist of files for packaging, or verify a "
                                                 "packaged output folder against a stage inventory.")
    parser.add_argument("--repo", help="full path of the repository root (staging)")
    parser.add_argument("--manifest", help="full path of the package manifest JSON (staging)")
    parser.add_argument("--stage", required=True, help="full path of the stage folder, inside <repo>\\build")
    parser.add_argument("--verify-output", help="full path of a packaged folder (PyInstaller _internal) to verify")
    args = parser.parse_args(argv)
    try:
        if args.verify_output:
            if args.repo or args.manifest:
                raise StageError("--verify-output takes only --stage")
            count = verify_output(args.stage, args.verify_output)
            print(f"verified {count} packaged files")
            return 0
        if not args.repo or not args.manifest:
            raise StageError("staging needs --repo, --manifest and --stage")
        count, inventory = stage_package(args.repo, args.manifest, args.stage)
    except StageError as exc:
        print(f"package stage refused: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"package stage failed: {exc.__class__.__name__}: {exc.filename or ''}", file=sys.stderr)
        return 2
    print(f"staged {count} files")
    print(f"inventory {inventory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
