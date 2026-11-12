"""Optional PSU catalog refresh from the public SPL's PSU Tier List sheet (Settings / Extras, off by default).

When turned on, Sentinel downloads the sheet's public CSV export at most once every 14 days while it runs, keeps
only factual specification fields (the Tier rating and Notes are dropped), validates the result and replaces the
local cache atomically. Any failure keeps the previous cache or the bundled catalog. Source credit is kept in the
catalog's "source" field and shown in the app. The parser below is the reviewed research normalizer (v2).
"""
import asyncio
import csv
import hashlib
import io
import json
import os
import re
import tempfile
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import psu_catalog

INTERVAL_S = 14 * 86400
RETRY_S = 6 * 3600          # after a failed attempt
MIN_SHARE = 0.5             # a refresh must keep at least half the bundled record count
MAX_CSV_BYTES = 8_000_000

SHEET_ID = "1akCHL7Vhzk_EhrpIGkz8zTEvYfLDcaSpZRB6Xt6JWkc"
TAB_NAME = "Full List (With Limited Confidence Ratings)"
TAB_GID = "931697732"
SOURCE_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&gid={TAB_GID}"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
WIDTH = 18
# Column positions in the Full List tab (header row checked by expected_header()).
C_BRAND, C_SERIES, C_SUB, C_VARIANT, C_WATT, C_TIER, C_YEAR, C_SIZE, C_ATX, C_INPUT, C_MOD, C_EFF, \
    C_PRIM, C_SEC, C_REG, C_ODM, C_PLATFORM, C_NOTES = range(18)
HEADER_HINTS = {C_BRAND: "brand", C_SERIES: "series", C_WATT: "wattage", C_TIER: "tier", C_YEAR: "year",
                C_SIZE: "size", C_ATX: "atx", C_INPUT: "input", C_MOD: "modularity", C_EFF: "efficiency",
                C_PRIM: "topology", C_ODM: "odm", C_PLATFORM: "platform", C_NOTES: "notes"}
# Letter codes are not defined in the sheet's About tab; these are the usual 80 PLUS names (inferred).
EFFICIENCY_LABELS = {"W": "80 Plus (White)", "B": "Bronze", "S": "Silver", "G": "Gold", "P": "Platinum", "T": "Titanium"}
NAME_LIMIT = 200


def clean(text):
    """NFC-normalize, turn line breaks inside cells into ' / ', collapse spaces."""
    text = unicodedata.normalize("NFC", text or "")
    text = re.sub(r"\s*\n\s*", " / ", text)
    return re.sub(r"[ \t ]+", " ", text).strip()


def key_text(text):
    return unicodedata.normalize("NFKC", clean(text)).casefold()


def parse_wattage(raw):
    """Return (variants, range, uncertain, error). Never invents models inside a range."""
    text = clean(raw)
    uncertain = text.endswith("?")
    body = text.rstrip("?").strip()
    if not body:
        return [], None, uncertain, "empty wattage"
    m = re.fullmatch(r"(\d{2,4})\s*W?\s*[-–—]\s*(\d{2,4})\s*W", body, re.I)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo >= hi:
            return [], None, uncertain, f"range not increasing: {text}"
        return [], {"min_w": lo, "max_w": hi}, uncertain, None
    parts = [p.strip() for p in body.split("/")]
    if not parts[-1].upper().endswith("W"):
        return [], None, uncertain, f"unrecognized wattage: {text}"
    values = []
    for p in parts:
        mm = re.fullmatch(r"(\d{2,4})\s*W?", p, re.I)
        if not mm:
            return [], None, uncertain, f"unrecognized wattage: {text}"
        values.append(int(mm.group(1)))
    if len(set(values)) != len(values):
        return [], None, uncertain, f"repeated wattage: {text}"
    if values != sorted(values):
        return sorted(values), None, uncertain, None  # keep, but note order in raw
    return values, None, uncertain, None


def parse_year(raw):
    text = clean(raw)
    if re.fullmatch(r"(19|20)\d\d", text):
        return int(text), None
    return None, (None if text == "" else f"unrecognized year: {text}")


def expected_header(header):
    """Return a list of header columns that do not contain their expected word."""
    bad = []
    for col, word in HEADER_HINTS.items():
        if col >= len(header) or word not in key_text(header[col]):
            bad.append(f"column {col} expected '{word}', found {clean(header[col] if col < len(header) else '')!r}")
    return bad


def normalize(rows, retrieved):
    """rows: list of CSV rows including the header. Returns (doc, report)."""
    header = rows[0] if rows else []
    report = {"header_problems": expected_header(header), "rows_total": max(0, len(rows) - 1), "excluded": [],
              "flagged": [], "duplicates_merged": 0, "id_collisions": 0}
    level = {"brand": "", "series": "", "sub_series": "", "variant": ""}
    origin = {k: None for k in level}
    records, by_key = [], {}
    for index, raw_row in enumerate(rows[1:], start=2):  # sheet row numbers, header is row 1
        if len(raw_row) != WIDTH:
            report["flagged"].append({"row": index, "issue": f"row has {len(raw_row)} cells, expected {WIDTH}"})
        r = (list(raw_row) + [""] * WIDTH)[:WIDTH]
        if not any(c.strip() for c in r):
            report["excluded"].append({"row": index, "reason": "blank row"})
            continue
        cells = {"brand": clean(r[C_BRAND]), "series": clean(r[C_SERIES]), "sub_series": clean(r[C_SUB]),
                 "variant": clean(r[C_VARIANT])}
        inherited = []
        for depth, name in enumerate(level):
            if cells[name]:
                level[name], origin[name] = cells[name], index
                for deeper in list(level)[depth + 1:]:
                    level[deeper], origin[deeper] = "", None  # a new parent resets deeper levels
        for name in level:
            if not cells[name] and level[name]:
                inherited.append({"field": name, "from_row": origin[name]})
        issues = []
        if not level["brand"]:
            report["excluded"].append({"row": index, "reason": "no brand above this row"})
            continue
        if cells["brand"] and not cells["series"]:
            issues.append("brand row without a series")
        if not level["series"]:
            issues.append("no series in this brand block")
        if any(len(level[n]) > NAME_LIMIT for n in level):
            issues.append("name longer than 200 characters")
        raw_watt = clean(r[C_WATT])
        if key_text(level["series"]) == "all psus" and not raw_watt:
            report["excluded"].append({"row": index, "reason": "brand-wide rating row without specifications",
                                       "brand": level["brand"]})
            continue
        variants, wrange, uncertain, werr = parse_wattage(r[C_WATT])
        if werr:
            report["excluded"].append({"row": index, "reason": werr, "brand": level["brand"], "series": level["series"]})
            continue
        year, yerr = parse_year(r[C_YEAR])
        if yerr:
            issues.append(yerr)
        if uncertain:
            issues.append("wattage marked uncertain in the source ('?')")
        eff = clean(r[C_EFF]).upper()
        atx = clean(r[C_ATX])
        atx = re.sub(r"(?i)^atx\s*([23])\.x$", lambda m: f"ATX {m.group(1)}.x", atx)
        rec = {
            "brand": level["brand"],
            "series": level["series"] if level["series"] != "-" else "",
            "sub_series": level["sub_series"] if level["sub_series"] != "-" else "",
            "variant": level["variant"] if level["variant"] != "-" else "",
            "wattage": {"raw": raw_watt, "variants_w": variants, "range": wrange, "uncertain": uncertain},
            "intro_year": year,
            "form_factor": clean(r[C_SIZE]),
            "atx_version": atx,
            "input_range": clean(r[C_INPUT]),
            "modularity": clean(r[C_MOD]),
            "efficiency": {"code": eff, "label": EFFICIENCY_LABELS.get(eff), "label_basis": "inferred" if eff in EFFICIENCY_LABELS else None},
            "topology": {"primary": clean(r[C_PRIM]), "secondary": clean(r[C_SEC]), "regulation": clean(r[C_REG])},
            "odm": clean(r[C_ODM]),
            "platform": clean(r[C_PLATFORM]),
        }
        identity = "|".join(key_text(x) for x in (rec["brand"], rec["series"], rec["sub_series"], rec["variant"],
                                                 raw_watt, rec["form_factor"], rec["atx_version"], str(year or ""),
                                                 rec["platform"], rec["odm"]))
        full_key = json.dumps(rec, sort_keys=True, ensure_ascii=False)
        if full_key in by_key:  # exact duplicate of every factual field
            by_key[full_key]["provenance"]["rows"].append(index)
            report["duplicates_merged"] += 1
            continue
        pid = "psu_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        if any(x["id"] == pid for x in records):
            report["id_collisions"] += 1
            issues.append("same identity as another row with different specifications")
            pid = f"{pid}_{index}"
        rec = {"id": pid, **rec, "flags": issues,
               "provenance": {"rows": [index], "inherited": inherited}}
        records.append(rec)
        by_key[full_key] = rec
        if issues:
            report["flagged"].append({"row": index, "id": pid, "issues": issues})
    doc = {
        "schema": "psu_catalog_factual_candidate/2",
        "source": {"name": "SPL's PSU Tier List", "sheet": SHEET_URL, "tab": TAB_NAME, "gid": TAB_GID,
                   "export_url": SOURCE_URL, "retrieved_utc": retrieved,
                   "license": "unspecified: no license or reuse grant found in the sheet; credit alone does not "
                              "establish permission"},
        "fields_excluded": ["Tier (editorial rating)", "Notes (prose)", "brand-wide 'All PSUs' rating rows"],
        "records": records,
    }
    return doc, report


# ----------------------------------------------------------------------------- refresh
def state_path():
    return psu_catalog.cache_path().with_name("psu_catalog_refresh.json")


def read_state(path=None):
    try:
        with Path(path or state_path()).open("rb") as fh:
            value = json.loads(fh.read(65_536).decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def _write_atomic(destination, text):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=destination.parent, prefix=".psu-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, destination)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def fetch_csv(timeout=60):
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "UltraSuite-PCSentinel/psu-catalog"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if "text/csv" not in resp.headers.get("Content-Type", ""):
            raise ValueError("The public sheet did not return CSV (it may now require sign-in).")
        raw = resp.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise ValueError("The sheet export is unexpectedly large.")
    return raw.decode("utf-8-sig")


def build(csv_text, retrieved, bundled_count):
    """CSV text -> validated catalog document, or ValueError with a plain reason."""
    rows = list(csv.reader(io.StringIO(csv_text)))
    doc, report = normalize(rows, retrieved)
    if report["header_problems"]:
        raise ValueError("The sheet's columns changed: " + "; ".join(report["header_problems"][:3]))
    if len(doc["records"]) < max(1, int(bundled_count * MIN_SHARE)):
        raise ValueError(f"Only {len(doc['records'])} models found (bundled has {bundled_count}); keeping the current catalog.")
    psu_catalog.validate(doc)
    return doc


def due(state, now, enabled):
    if not enabled:
        return False
    last_ok, last_try = state.get("last_success_ts"), state.get("last_attempt_ts") or 0
    if state.get("last_error") and now - last_try < RETRY_S:
        return False
    return last_ok is None or now - last_ok >= INTERVAL_S


def refresh(fetch=fetch_csv, now=None, cache=None, state_file=None, bundle=None):
    now = time.time() if now is None else now
    state = read_state(state_file)
    state["last_attempt_ts"] = now
    try:
        bundled = psu_catalog.read(bundle or Path(psu_catalog.__file__).with_name("psu_catalog.json"))
        retrieved = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        doc = build(fetch(), retrieved, len(bundled["records"]))
        _write_atomic(cache or psu_catalog.cache_path(), json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
        state.update(last_success_ts=now, last_error=None, records=len(doc["records"]), retrieved_utc=retrieved)
    except Exception as exc:  # network, parse or validation: the current catalog stays in use
        state["last_error"] = str(exc)[:300] or exc.__class__.__name__
    _write_atomic(state_file or state_path(), json.dumps(state))
    return state


async def loop():
    from . import preferences
    while True:
        try:
            if due(read_state(), time.time(), preferences.load().get("psu_refresh") is True):
                await asyncio.to_thread(refresh)
        except Exception as exc:
            print(f"[psu refresh] {exc}")
        await asyncio.sleep(1800)


def register(app):
    from fastapi import HTTPException

    @app.get("/api/power/catalog/refresh")
    def _status():
        s = read_state()
        return {"interval_days": INTERVAL_S // 86400, "last_success_ts": s.get("last_success_ts"),
                "last_attempt_ts": s.get("last_attempt_ts"), "last_error": s.get("last_error"),
                "records": s.get("records"), "retrieved_utc": s.get("retrieved_utc"),
                "source": {"name": "SPL's PSU Tier List", "sheet": SHEET_URL}}

    @app.post("/api/power/catalog/refresh")
    async def _now():
        from . import preferences
        if preferences.load().get("psu_refresh") is not True:
            raise HTTPException(409, "Turn on PSU catalog refresh in Settings / Extras first.")
        return await asyncio.to_thread(refresh)
