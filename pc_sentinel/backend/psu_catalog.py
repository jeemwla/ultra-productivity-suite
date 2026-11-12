"""Read-only PSU facts, with a validated local cache and bundled offline fallback.

No download runs in the telemetry loop. The optional maintenance importer replaces
the dedicated cache atomically; malformed or truncated updates cannot replace the
bundled fallback. All strings are data, never executable HTML.
"""
import json
import os
from pathlib import Path

MAX_BYTES = 2_000_000
SCHEMA = "psu_catalog_factual_candidate/2"


def validate(document):
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise ValueError("Unsupported PSU catalog schema")
    source, records = document.get("source"), document.get("records")
    if not isinstance(source, dict) or not isinstance(source.get("retrieved_utc"), str):
        raise ValueError("Missing PSU catalog provenance")
    if not isinstance(records, list) or not 1 <= len(records) <= 10_000:
        raise ValueError("Invalid PSU catalog count")
    ids = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("wattage"), dict):
            raise ValueError("Invalid PSU record")
        for field in ("id", "brand", "series", "sub_series", "variant", "form_factor", "atx_version", "platform"):
            value = record.get(field)
            if not isinstance(value, str) or len(value) > 240 or not value.isprintable() and value:
                raise ValueError("Invalid PSU text")
        if not record["id"] or record["id"] in ids or not record["brand"]:
            raise ValueError("Duplicate or missing PSU identity")
        ids.add(record["id"])
        watts = record["wattage"]
        variants, span = watts.get("variants_w"), watts.get("range")
        valid_w = lambda n: type(n) is int and 50 <= n <= 5000
        if not isinstance(variants, list) or len(variants) > 40 or any(not valid_w(n) for n in variants):
            raise ValueError("Invalid PSU wattages")
        if len(variants) != len(set(variants)) or type(watts.get("uncertain")) is not bool:
            raise ValueError("Invalid PSU wattage metadata")
        if span is not None and (not isinstance(span, dict) or set(span) != {"min_w", "max_w"}
                                 or not all(valid_w(span[k]) for k in span) or span["min_w"] >= span["max_w"]):
            raise ValueError("Invalid PSU wattage range")
        if bool(variants) == bool(span):
            raise ValueError("A PSU must specify exact variants or a range, never both")
        if not isinstance(watts.get("raw"), str) or len(watts["raw"]) > 100:
            raise ValueError("Missing raw PSU wattage")
        if record.get("intro_year") is not None and (type(record["intro_year"]) is not int or not 1900 <= record["intro_year"] <= 2100):
            raise ValueError("Invalid PSU year")
    # Serve only the picker fields, no editorial ratings, topology or import reports.
    fields = ("id", "brand", "series", "sub_series", "variant", "wattage", "intro_year", "form_factor", "atx_version", "platform", "flags", "provenance")
    return {"schema": SCHEMA, "source": source,
            "records": [{k: r[k] for k in fields if k in r} for r in records]}


def read(path):
    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("PSU catalog is too large")
    return validate(json.loads(raw.decode("utf-8-sig")))


def cache_path():
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "UltraSuite/psu_catalog.json"


def load(*, _cache=None, _bundle=None):
    cache = Path(_cache) if _cache is not None else cache_path()
    bundle = Path(_bundle) if _bundle is not None else Path(__file__).with_name("psu_catalog.json")
    cache_status = "not_configured"
    try:
        return {**read(cache), "storage": "local_cache", "cache_status": "ok"}
    except FileNotFoundError:
        pass
    except (OSError, ValueError, UnicodeError, RecursionError):
        cache_status = "invalid_or_unavailable"
    return {**read(bundle), "storage": "bundled", "cache_status": cache_status}


def register(app):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    @app.get("/api/power/catalog")
    def catalog():
        try:
            return load()
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise HTTPException(503, "PSU catalog unavailable. Manual entry still works.") from None

    @app.get("/psu_picker.js", include_in_schema=False)
    def picker_script():
        return FileResponse(Path(__file__).parent.parent / "frontend/psu_picker.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})
