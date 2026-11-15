"""Pure parsers for the store reports (no Home Assistant imports, so they can be tested on their own)."""

from __future__ import annotations

import csv
import gzip
import io
import re

# App Store "Product Type Identifier" values
APPLE_FIRST_DOWNLOAD = {"1", "1F", "1T", "F1", "1E", "1EP", "1EU", "1-B", "1F-B", "1T-B", "F1-B"}
APPLE_UPDATE = {"7", "7F", "7T", "F7"}
APPLE_REDOWNLOAD = {"3", "3F", "3T", "F3"}

GOOGLE_OVERVIEW_RE = re.compile(r"^stats/installs/installs_(.+)_(\d{6})_overview\.csv$")


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip().replace(",", "")
    if value == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def parse_apple_sales_report(raw: bytes) -> dict[str, dict]:
    """Parse an App Store Connect SALES/SUMMARY report (gzip TSV) into per-app sums.

    Returns {apple_id: {"title", "installs", "updates", "redownloads"}}. In-app purchases are ignored.
    """
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if not lines:
        return {}
    header = [h.strip() for h in lines[0].split("\t")]
    try:
        i_type = header.index("Product Type Identifier")
        i_units = header.index("Units")
        i_id = header.index("Apple Identifier")
    except ValueError:
        return {}
    i_title = header.index("Title") if "Title" in header else None

    apps: dict[str, dict] = {}
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) <= max(i_type, i_units, i_id):
            continue
        ptype = cols[i_type].strip()
        if ptype in APPLE_FIRST_DOWNLOAD:
            kind = "installs"
        elif ptype in APPLE_UPDATE:
            kind = "updates"
        elif ptype in APPLE_REDOWNLOAD:
            kind = "redownloads"
        else:
            continue  # in-app purchases and other products
        app_id = cols[i_id].strip()
        if not app_id:
            continue
        rec = apps.setdefault(
            app_id,
            {"title": (cols[i_title].strip() if i_title is not None else app_id), "installs": 0, "updates": 0, "redownloads": 0},
        )
        rec[kind] += _int(cols[i_units]) or 0
    return apps


def parse_google_overview(raw: bytes) -> dict:
    """Parse a Google Play installs overview CSV (usually UTF-16) for one app and one month."""
    text = None
    for enc in ("utf-16", "utf-8-sig"):
        try:
            text = raw.decode(enc)
            if "Date" in text[:200]:
                break
        except UnicodeDecodeError:
            continue
    if text is None:
        return {}
    rows = list(csv.DictReader(io.StringIO(text)))
    rows = [r for r in rows if (r.get("Date") or "").strip()]
    rows.sort(key=lambda r: r["Date"])
    if not rows:
        return {}

    def column(*names: str) -> str | None:
        for name in names:
            if any(_int(r.get(name)) is not None for r in rows):
                return name
        return None

    upd_col = column("Update events", "Daily Device Upgrades")
    inst_col = column("Daily User Installs", "Install events", "Daily Device Installs")
    total_col = column("Total User Installs")
    active_col = column("Active Device Installs")

    def last(col: str | None) -> int | None:
        if col is None:
            return None
        for r in reversed(rows):
            v = _int(r.get(col))
            if v is not None:
                return v
        return None

    last_row = rows[-1]
    return {
        "first_date": rows[0]["Date"].strip(),
        "last_date": last_row["Date"].strip(),
        "updates": sum(_int(r.get(upd_col)) or 0 for r in rows) if upd_col else 0,
        "installs": sum(_int(r.get(inst_col)) or 0 for r in rows) if inst_col else 0,
        "total_user_installs": last(total_col),
        "active_installs": last(active_col),
        "last_day_installs": (_int(last_row.get(inst_col)) or 0) if inst_col else 0,
        "last_day_updates": (_int(last_row.get(upd_col)) or 0) if upd_col else 0,
    }


def google_bucket_name(value: str) -> str:
    """Accept 'gs://pubsite_prod_xxx/stats/...' or a bare bucket name."""
    value = value.strip()
    if value.startswith("gs://"):
        value = value[5:]
    return value.split("/", 1)[0]
