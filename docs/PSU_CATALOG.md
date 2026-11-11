# PSU catalog

PC Sentinel's optional desktop picker uses a bundled offline catalog of factual PSU specifications.
Choosing a catalog item fills a draft. Saving stores only the selected wattage, model and form factor on that PC.
Laptop views hide the desktop picker. Manual entry remains available if a model is missing.

Source: [SPL's PSU Tier List](https://psutierlist.org),
[Full List sheet](https://docs.google.com/spreadsheets/d/1akCHL7Vhzk_EhrpIGkz8zTEvYfLDcaSpZRB6Xt6JWkc/edit#gid=931697732).
The bundled snapshot was retrieved on 2026-11-11. It contains 1,413 series from 64 brands, not 1,413 individually verified models.
The source does not state a reuse license. Attribution does not establish permission. Editorial tier ratings,
notes and brand-wide judgments are excluded. Manufacturer accuracy has not been checked for every record.

Exact lists and series ranges are different: `650/750W` lists two variants, while `550-750W` does not prove
which individual models exist. Selecting a range clears the rated-output draft and asks for the actual label.
Year, ATX generation and platform help distinguish similarly named revisions. Uncertain wattages are labeled.

The app never downloads the sheet in its sensor loop. A validated local cache at
`%APPDATA%\UltraSuite\psu_catalog.json` can override the bundle. Missing, invalid or oversized caches fall back
to the bundle. A missing bundle gives an explicit catalog error; the manual fields still work.

Maintainers can refresh a catalog explicitly from the public CSV endpoint with Python, without signing in:

```powershell
python -I tools/psu_catalog_import.py --fetch pc_sentinel/backend/psu_catalog.json
python -I tools/psu_catalog_import.py --self-test
python -W ignore -m unittest discover -s tests
```

For an owner-only scheduled refresh, the same importer can target the dedicated local-cache file instead.
No periodic task is installed by the public installer. Header drift, malformed data, excessive downloads or
validation failures preserve the prior destination. Updates use a flushed temporary file and atomic replacement.
The importer requires the repository's production validator, so moving the script alone is insufficient.

Catalog entries cannot identify installed PSUs automatically. The rating is capacity, not current consumption.
Component sensor subtotals cannot establish PSU adequacy or a need to upgrade.
