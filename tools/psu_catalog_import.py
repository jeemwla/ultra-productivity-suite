"""Normalize the factual specification fields of SPL's PSU Tier List into a candidate catalog (research, v2).

Maintenance importer. Standard library only. No editorial tier ratings are included.

    python -I psu_sheet_normalize_v2.py --fetch OUT.json            fetch the public Full List tab (no sign-in)
    python -I psu_sheet_normalize_v2.py --csv FULL.csv OUT.json     use a saved copy of that tab
    python -I psu_sheet_normalize_v2.py --self-test                 run the parser tests

Corrections over v1:
- Wattage: "650/750W" and "1000W/1300W" are explicit variants; "300-600W" is a range kept in its own field and never
  turned into endpoint models; "850W?" is kept and flagged uncertain; the raw text is always preserved.
- Names: columns 1-3 are a hierarchy (series, sub-series, variant; "-" means none). v1 mislabeled column 3 as the
  efficiency rating. Efficiency is the letter code in column 11.
- Merged cells: brand, series, sub-series and variant appear only on the first row of a block and are filled down,
  deeper levels reset when a higher level changes, and every inherited field is recorded per record.
- Only factual specification fields are output. The Tier column (editorial) and Notes (prose) are dropped, as are
  brand-wide rating rows ("All PSUs") that carry no specifications.
- Each record has provenance (source URL, tab, gid, sheet row, retrieval time) and a stable id; exact duplicates are
  merged with all their source rows.
"""
import csv
import hashlib
import io
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone

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


def summarize(doc, report):
    recs = doc["records"]
    out = {
        "rows_total": report["rows_total"], "records": len(recs),
        "brands": len({key_text(r["brand"]) for r in recs}),
        "wattage_single": sum(1 for r in recs if len(r["wattage"]["variants_w"]) == 1),
        "wattage_list": sum(1 for r in recs if len(r["wattage"]["variants_w"]) > 1),
        "wattage_range": sum(1 for r in recs if r["wattage"]["range"]),
        "wattage_uncertain": sum(1 for r in recs if r["wattage"]["uncertain"]),
        "explicit_variant_wattages": sum(len(r["wattage"]["variants_w"]) for r in recs),
        "with_inherited_fields": sum(1 for r in recs if r["provenance"]["inherited"]),
        "flagged_records": sum(1 for r in recs if r["flags"]),
        "excluded_rows": len(report["excluded"]),
        "duplicates_merged": report["duplicates_merged"], "id_collisions": report["id_collisions"],
        "header_problems": report["header_problems"],
        "form_factors": sorted({r["form_factor"] for r in recs}),
        "efficiency_codes": sorted({r["efficiency"]["code"] for r in recs}),
    }
    return out


def fetch():
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "psu-catalog-research/2"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        if "text/csv" not in resp.headers.get("Content-Type", ""):
            raise SystemExit("The public sheet did not return CSV (it may now require sign-in); stopping.")
        raw = resp.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError("PSU sheet is too large")
        return raw.decode("utf-8")


# ----------------------------------------------------------------------------------------------- tests
def _self_test():
    import unittest

    H = ["a Brand", "Series", "", "", "Wattages", "Tier", "Intro. Year", "Specifications Size", "ATX Ver.",
         "Input Range", "Modularity", "Efficiency", "Topology Prim. Conv.", "Secondary Rect.", "Reg.",
         "Manufacturer ODM", "Platform Code", "Notes"]

    def row(brand="", series="", sub="", variant="", watt="850W", tier="A", year="2024", size="ATX", atx="ATX 3.x",
            inp="Full", mod="Full", eff="G", odm="CWT", platform="X1", notes="some prose"):
        return [brand, series, sub, variant, watt, tier, year, size, atx, inp, mod, eff, "LLC", "SR", "DC-DC",
                odm, platform, notes]

    def run(*rows):
        return normalize([H, *rows], "2026-01-01T00:00:00Z")

    class T(unittest.TestCase):
        def test_range_is_a_range_not_endpoint_models(self):
            doc, _ = run(row("Acme", "R", watt="300-600W"))
            w = doc["records"][0]["wattage"]
            self.assertEqual(w["range"], {"min_w": 300, "max_w": 600})
            self.assertEqual(w["variants_w"], [])
            self.assertEqual(w["raw"], "300-600W")

        def test_lists_are_explicit_variants_in_both_spellings(self):
            doc, _ = run(row("Acme", "L", watt="650/750W"), row(watt="1000W/1300W"), row(watt="550/650/750W"))
            self.assertEqual([r["wattage"]["variants_w"] for r in doc["records"]], [[650, 750], [1000, 1300], [550, 650, 750]])
            self.assertTrue(all(r["wattage"]["range"] is None for r in doc["records"]))

        def test_uncertain_and_malformed_wattage(self):
            doc, rep = run(row("Acme", "U", watt="850W?"), row(watt=""), row(watt="lots"), row(watt="600-300W"),
                           row(watt="650/650W"))
            self.assertEqual(len(doc["records"]), 1)
            self.assertTrue(doc["records"][0]["wattage"]["uncertain"])
            self.assertIn("wattage marked uncertain in the source ('?')", doc["records"][0]["flags"])
            reasons = sorted(e["reason"].split(":")[0] for e in rep["excluded"])
            self.assertEqual(reasons, ["empty wattage", "range not increasing", "repeated wattage", "unrecognized wattage"])

        def test_fill_down_resets_deeper_levels_and_records_inheritance(self):
            doc, _ = run(row("Acme", "Alpha", "Gold", "V1", watt="550W"), row(watt="650W"),
                         row(series="Beta", watt="750W"), row(brand="Zed", series="Z", watt="850W"))
            r = doc["records"]
            self.assertEqual((r[1]["series"], r[1]["sub_series"], r[1]["variant"]), ("Alpha", "Gold", "V1"))
            self.assertEqual({i["field"] for i in r[1]["provenance"]["inherited"]}, {"brand", "series", "sub_series", "variant"})
            self.assertEqual((r[2]["series"], r[2]["sub_series"], r[2]["variant"]), ("Beta", "", ""), "new series resets")
            self.assertEqual((r[3]["brand"], r[3]["series"]), ("Zed", "Z"))

        def test_dash_means_none_and_brand_without_series_is_flagged(self):
            doc, _ = run(row("Acme", "S", "-", "-"), row(brand="Lone", series="", watt="500W"))
            self.assertEqual((doc["records"][0]["sub_series"], doc["records"][0]["variant"]), ("", ""))
            self.assertIn("brand row without a series", doc["records"][1]["flags"])

        def test_editorial_fields_are_never_output(self):
            doc, rep = run(row("Acme", "All PSUs", watt="", tier="E", year=""), row("Acme", "Real"))
            blob = json.dumps(doc["records"])
            self.assertNotIn("some prose", blob)
            self.assertNotIn('"tier"', blob)
            self.assertEqual(len(doc["records"]), 1)
            self.assertEqual(rep["excluded"][0]["reason"], "brand-wide rating row without specifications")

        def test_exact_duplicates_merge_and_ids_are_stable(self):
            a = run(row("Acme", "D"), row(), row(watt="750W"))
            b = run(row("Acme", "D"), row(), row(watt="750W"))
            self.assertEqual(len(a[0]["records"]), 2)
            self.assertEqual(a[0]["records"][0]["provenance"]["rows"], [2, 3])
            self.assertEqual(a[1]["duplicates_merged"], 1)
            self.assertEqual([r["id"] for r in a[0]["records"]], [r["id"] for r in b[0]["records"]])

        def test_same_identity_with_different_specs_is_kept_and_flagged(self):
            doc, rep = run(row("Acme", "C", mod="Full"), row(mod="Semi"))
            self.assertEqual(len(doc["records"]), 2)
            self.assertEqual(rep["id_collisions"], 1)
            self.assertNotEqual(doc["records"][0]["id"], doc["records"][1]["id"])

        def test_unicode_names_and_cell_line_breaks(self):
            doc, _ = run(row("Thermaltake\n(TT)", "Tough Power", "Café", watt="1000–1200W"))
            r = doc["records"][0]
            self.assertEqual(r["brand"], "Thermaltake / (TT)")
            self.assertEqual(r["series"], "Tough Power")
            self.assertEqual(r["sub_series"], "Café", "NFC normalization")
            self.assertEqual(r["wattage"]["range"], {"min_w": 1000, "max_w": 1200}, "en dash range")

        def test_year_atx_and_efficiency_normalization(self):
            doc, _ = run(row("Acme", "Y", year="", atx="ATX 2.X", eff="N"), row(year="20x4", eff="g"))
            a, b = doc["records"]
            self.assertIsNone(a["intro_year"])
            self.assertEqual(a["atx_version"], "ATX 2.x")
            self.assertEqual(a["efficiency"], {"code": "N", "label": None, "label_basis": None})
            self.assertIn("unrecognized year: 20x4", b["flags"])
            self.assertEqual(b["efficiency"]["label"], "Gold")

        def test_short_rows_and_header_drift_are_reported(self):
            doc, rep = normalize([H[:5], ["Acme", "Short", "", "", "500W"]], "t")
            self.assertTrue(rep["header_problems"])
            self.assertTrue(any("cells, expected" in f["issue"] for f in rep["flagged"] if "issue" in f))
            self.assertEqual(len(doc["records"]), 1)

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(T))
    return 0 if result.wasSuccessful() else 1


def main(argv):
    if argv[:1] == ["--self-test"]:
        return _self_test()
    if len(argv) == 2 and argv[0] == "--fetch":
        text, out = fetch(), argv[1]
    elif len(argv) == 3 and argv[0] == "--csv":
        text, out = open(argv[1], encoding="utf-8").read(), argv[2]
    else:
        print(__doc__)
        return 2
    retrieved = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = list(csv.reader(io.StringIO(text)))
    doc, report = normalize(rows, retrieved)
    if report["header_problems"]:
        raise ValueError("Sheet layout changed; existing catalog preserved: " + str(report["header_problems"]))
    import importlib.util
    import os
    import tempfile
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("psu_catalog", root / "pc_sentinel/backend/psu_catalog.py")
    catalog = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(catalog)
    catalog.validate(doc)
    destination = Path(out).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=".psu-catalog-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(doc, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        catalog.read(temporary)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(json.dumps(summarize(doc, report), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
