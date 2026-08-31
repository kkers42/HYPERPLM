"""
HYPERPLM — Import existing team data (CSV / Excel).

One module, one responsibility (rule 3). Raised in testing: teams already hold
seasons of setups, lap times and part records in spreadsheets, and nobody
retypes that by hand. This is the difference between trying the product and
moving into it.

Design decisions worth knowing:

* **Preview, then commit.** Parsing and applying are separate calls. You always
  see what will happen — row counts, what matched, what will be skipped and why
  — before anything is written. An import that silently half-applies is worse
  than one that refuses.
* **Per-row errors, not per-file.** One malformed lap does not reject a season.
  Bad rows are reported with their line number and left out; good rows import.
* **Forgiving headers.** Teams' columns are named whatever the engineer typed:
  `Lap`, `lap #`, `LapNo` all mean the same thing. Matching is case- and
  punctuation-insensitive against a list of known aliases.
* **Lap times in any sane format.** `1:41.208`, `101.208`, `1:41` all parse.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Optional

MAX_ROWS = 20000


class ImportError_(ValueError):
    """A problem with the file as a whole (not a single row)."""


# ── reading ───────────────────────────────────────────────────────────────────

def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


def read_table(data: bytes, filename: str = "") -> list[dict]:
    """Return a list of row dicts from CSV or XLSX bytes."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        return _read_excel(data)
    return _read_csv(data)


def _read_csv(data: bytes) -> list[dict]:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportError_("Could not read the file as text")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if len(rows) > MAX_ROWS:
        raise ImportError_(f"That file has {len(rows)} rows; the limit is {MAX_ROWS}")
    return rows


def _read_excel(data: bytes) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    try:
        header = [str(h) if h is not None else "" for h in next(rows)]
    except StopIteration:
        return []
    out = []
    for r in rows:
        if all(v is None or str(v).strip() == "" for v in r):
            continue
        out.append({header[i]: r[i] for i in range(min(len(header), len(r)))})
        if len(out) > MAX_ROWS:
            raise ImportError_(f"That file exceeds the {MAX_ROWS} row limit")
    return out


def pick(row: dict, *aliases: str) -> Optional[Any]:
    """Find a column by any of its known names, ignoring case and punctuation."""
    want = {_norm(a) for a in aliases}
    for k, v in row.items():
        if _norm(k) in want:
            return v
    return None


# ── value parsing ─────────────────────────────────────────────────────────────

def parse_lap_time(v: Any) -> Optional[int]:
    """'1:41.208' | '101.208' | '1:41' | 101208 -> milliseconds."""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        # a bare number is seconds unless it is implausibly large
        secs = float(v)
        return int(round(secs * 1000)) if secs < 3600 else int(secs)
    s = str(v).strip()
    if not s:
        return None
    m = re.fullmatch(r"(?:(\d+):)?(\d+(?:[.,]\d+)?)", s)
    if not m:
        return None
    mins = int(m.group(1) or 0)
    secs = float(m.group(2).replace(",", "."))
    total = mins * 60000 + secs * 1000
    return int(round(total)) if total > 0 else None


def parse_number(v: Any) -> Optional[float]:
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(str(v).strip().replace(",", ""))
    except ValueError:
        return None


# ── laps ──────────────────────────────────────────────────────────────────────

LAP_COLUMNS = "lap / lap no / lap number / #, time / laptime / lap time, driver, tire / tyre / compound"


def parse_laps(rows: list[dict]) -> dict:
    """Rows -> {'rows': [...ready to insert...], 'errors': [...]}"""
    ok, errors = [], []
    for i, r in enumerate(rows, start=2):          # row 1 is the header
        raw_time = pick(r, "time", "laptime", "lap time", "lap_time", "besttime", "best")
        ms = parse_lap_time(raw_time)
        if ms is None:
            errors.append({"row": i, "problem": "no readable lap time",
                           "value": str(raw_time or "")[:40]})
            continue
        lap_no = parse_number(pick(r, "lap", "lapno", "lap no", "lap number", "#", "no"))
        ok.append({
            "lap_no": int(lap_no) if lap_no else len(ok) + 1,
            "lap_time_ms": ms,
            "driver_name": (str(pick(r, "driver", "drivername") or "").strip() or None),
            "tire_compound": (str(pick(r, "tire", "tyre", "compound") or "").strip()),
        })
    return {"rows": ok, "errors": errors}


# ── setup sheets ──────────────────────────────────────────────────────────────

SETUP_COLUMNS = "group / section / category, field / name / parameter, value, unit"


def parse_setup(rows: list[dict]) -> dict:
    ok, errors = [], []
    for i, r in enumerate(rows, start=2):
        key = pick(r, "field", "name", "parameter", "setting", "item", "attribute")
        if key is None or not str(key).strip():
            errors.append({"row": i, "problem": "no field name", "value": ""})
            continue
        val = pick(r, "value", "setting", "val")
        ok.append({
            "group_name": (str(pick(r, "group", "section", "category", "area") or "").strip()),
            "attr_key": str(key).strip(),
            "attr_value": "" if val is None else str(val).strip(),
            "unit": (str(pick(r, "unit", "units") or "").strip()),
        })
    return {"rows": ok, "errors": errors}


# ── parts / service life ──────────────────────────────────────────────────────

PART_COLUMNS = ("part number / part no / pn, description / name, "
                "hours used / hours, hours limit / life / limit")


def parse_parts(rows: list[dict]) -> dict:
    ok, errors = [], []
    for i, r in enumerate(rows, start=2):
        num = pick(r, "partnumber", "part no", "partno", "pn", "part", "number")
        if num is None or not str(num).strip():
            errors.append({"row": i, "problem": "no part number", "value": ""})
            continue
        used = parse_number(pick(r, "hoursused", "hours used", "hours", "used", "runtime"))
        limit = parse_number(pick(r, "hourslimit", "hours limit", "limit", "life", "lifelimit"))
        ok.append({
            "part_number": str(num).strip(),
            "part_name": (str(pick(r, "description", "name", "partname") or "").strip()
                          or str(num).strip()),
            "hours_used": used or 0.0,
            "hours_limit": limit,
        })
    return {"rows": ok, "errors": errors}


PARSERS = {"laps": parse_laps, "setup": parse_setup, "parts": parse_parts}
COLUMNS = {"laps": LAP_COLUMNS, "setup": SETUP_COLUMNS, "parts": PART_COLUMNS}


def preview(kind: str, data: bytes, filename: str = "") -> dict:
    if kind not in PARSERS:
        raise ImportError_(f"Unknown import type: {kind}")
    rows = read_table(data, filename)
    if not rows:
        raise ImportError_("That file has no data rows")
    parsed = PARSERS[kind](rows)
    return {
        "kind": kind,
        "detected_columns": list(rows[0].keys()),
        "expected_columns": COLUMNS[kind],
        "total_rows": len(rows),
        "ready": len(parsed["rows"]),
        "skipped": len(parsed["errors"]),
        "errors": parsed["errors"][:25],
        "sample": parsed["rows"][:10],
        "rows": parsed["rows"],
    }
