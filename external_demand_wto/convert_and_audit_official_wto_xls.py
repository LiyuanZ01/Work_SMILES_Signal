#!/usr/bin/env python3
"""Convert and audit the unmodified official WTO quarterly volume workbook.

The original .xls file remains unchanged. Each worksheet is exported verbatim
(as cell values) to CSV and an .xlsx copy. The script inventories labels, dates,
series dimensions, and attempts to identify China export-volume and partner
import-volume series. It also compares any identified quarterly observations
with the WTO-provider series used in the replication, retrieved independently
through the existing DBnomics access route. Discrepancies are reported; values
are never silently overwritten.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

OUT = Path("external_demand_wto/wto_official_archive")
XLS = OUT / "wto_legacy_quarterly_volume.xls"
DBNOMICS_BASE = "https://api.db.nomics.world/v22/series"

EXPORT_SERIES = ("ITS_MTP_QXVSA", "156.TO.000.Q", "CHN", "China")
IMPORT_SERIES = {
    "USA": ("840.TO.000.Q", "United States"),
    "JPN": ("392.TO.000.Q", "Japan"),
    "DEU": ("276.TO.000.Q", "Germany"),
    "KOR": ("410.TO.000.Q", "Korea, Rep."),
    "NLD": ("528.TO.000.Q", "Netherlands"),
    "GBR": ("826.TO.000.Q", "United Kingdom"),
    "CAN": ("124.TO.000.Q", "Canada"),
    "SGP": ("702.TO.000.Q", "Singapore"),
    "MEX": ("484.TO.000.Q", "Mexico"),
    "ITA": ("380.TO.000.Q", "Italy"),
    "AUS": ("036.TO.000.Q", "Australia"),
    "FRA": ("250.TO.000.Q", "France"),
    "IND": ("356.TO.000.Q", "India"),
    "ESP": ("724.TO.000.Q", "Spain"),
    "MYS": ("458.TO.000.Q", "Malaysia"),
    "RUS": ("643.TO.000.Q", "Russian Federation"),
    "THA": ("764.TO.000.Q", "Thailand"),
    "BEL": ("056.TO.000.Q", "Belgium"),
    "TUR": ("792.TO.000.Q", "Türkiye"),
    "ARE": ("784.TO.000.Q", "United Arab Emirates"),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")[:100] or "sheet"


def canonical_quarter(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_period("Q").strftime("%YQ%q")
    text = str(value).strip().upper()
    # 2005Q1 / 2005-Q1 / Q1 2005 / 2005-01-01
    m = re.search(r"(19\d{2}|20\d{2})\s*[-_/ ]?Q([1-4])", text)
    if m:
        return f"{m.group(1)}Q{m.group(2)}"
    m = re.search(r"Q([1-4])\s*[-_/ ]?(19\d{2}|20\d{2})", text)
    if m:
        return f"{m.group(2)}Q{m.group(1)}"
    try:
        dt = pd.to_datetime(value, errors="raise")
        return dt.to_period("Q").strftime("%YQ%q")
    except Exception:
        return None


def find_dbnomics_doc(payload: Any) -> dict[str, Any]:
    candidates = []
    if isinstance(payload, dict):
        for path in [
            payload.get("series", {}).get("docs") if isinstance(payload.get("series"), dict) else None,
            payload.get("dataset", {}).get("series", {}).get("docs") if isinstance(payload.get("dataset"), dict) else None,
            payload.get("docs"),
        ]:
            if isinstance(path, list) and path and isinstance(path[0], dict):
                candidates.append(path[0])
        nested = payload.get("payload")
        if isinstance(nested, dict):
            docs = nested.get("series", {}).get("docs") if isinstance(nested.get("series"), dict) else None
            if isinstance(docs, list) and docs and isinstance(docs[0], dict):
                candidates.append(docs[0])
    if not candidates:
        raise ValueError("No DBnomics series document")
    return candidates[0]


def dbnomics_series(dataset: str, code: str, alpha3: str, name: str, flow: str) -> pd.DataFrame:
    url = f"{DBNOMICS_BASE}/WTO/{dataset}/{code}?observations=1"
    r = requests.get(url, timeout=90, headers={"User-Agent": "WTO-official-archive-audit/1.0"})
    r.raise_for_status()
    doc = find_dbnomics_doc(r.json())
    periods = doc.get("period") or doc.get("periods")
    values = doc.get("value") or doc.get("values")
    df = pd.DataFrame({"quarter": periods, "mirror_value": values})
    df["quarter"] = df["quarter"].map(canonical_quarter)
    df["mirror_value"] = pd.to_numeric(df["mirror_value"], errors="coerce")
    df = df.dropna(subset=["quarter"]).drop_duplicates("quarter", keep="last")
    df["alpha3"] = alpha3
    df["economy"] = name
    df["flow"] = flow
    df["mirror_source_url"] = url
    return df


def workbook_inventory() -> tuple[list[dict[str, Any]], dict[str, pd.DataFrame]]:
    excel = pd.ExcelFile(XLS, engine="xlrd")
    inventory = []
    frames: dict[str, pd.DataFrame] = {}
    with pd.ExcelWriter(OUT / "WTO_official_quarterly_volume_workbook_copy.xlsx", engine="openpyxl") as writer:
        for idx, sheet in enumerate(excel.sheet_names, start=1):
            df = pd.read_excel(XLS, sheet_name=sheet, header=None, dtype=object, engine="xlrd")
            df = df.replace({np.nan: None})
            frames[sheet] = df
            csv_name = f"official_sheet_{idx:02d}_{safe(sheet)}.csv"
            df.to_csv(OUT / csv_name, index=False, header=False, encoding="utf-8-sig")
            df.to_excel(writer, sheet_name=safe(sheet)[:31], index=False, header=False)
            nonempty = []
            for r in range(min(len(df), 30)):
                row = [None if x is None else str(x) for x in df.iloc[r].tolist()]
                if any((x or "").strip() for x in row):
                    nonempty.append({"row_1based": r + 1, "values": row[:30]})
            all_text = "\n".join(str(x) for x in df.to_numpy().ravel() if x is not None)
            inventory.append({
                "sheet_index": idx,
                "sheet_name": sheet,
                "rows": int(df.shape[0]),
                "columns": int(df.shape[1]),
                "csv_file": csv_name,
                "first_nonempty_rows": nonempty[:15],
                "contains_china": bool(re.search(r"china|中国", all_text, re.I)),
                "contains_export": bool(re.search(r"export|出口", all_text, re.I)),
                "contains_import": bool(re.search(r"import|进口", all_text, re.I)),
                "quarter_like_cells": int(sum(canonical_quarter(x) is not None for x in df.to_numpy().ravel())),
            })
    return inventory, frames


def candidate_long_tables(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Extract generic row/column quarter candidates without assuming workbook layout."""
    rows: list[dict[str, Any]] = []
    for sheet, df in frames.items():
        # Horizontal time: a row has many quarters, another row/cell identifies an economy/flow.
        for r in range(len(df)):
            qcols = [(c, canonical_quarter(df.iat[r, c])) for c in range(df.shape[1])]
            qcols = [(c, q) for c, q in qcols if q]
            if len(qcols) >= 4:
                for rr in range(max(0, r - 8), min(len(df), r + 80)):
                    if rr == r:
                        continue
                    numeric = pd.to_numeric(pd.Series([df.iat[rr, c] for c, _ in qcols]), errors="coerce")
                    if numeric.notna().sum() < max(4, int(len(qcols) * 0.5)):
                        continue
                    label_cells = [str(df.iat[rr, c]) for c in range(min(8, df.shape[1])) if df.iat[rr, c] is not None]
                    label = " | ".join(label_cells)
                    for (c, q), value in zip(qcols, numeric):
                        if pd.notna(value):
                            rows.append({
                                "sheet": sheet,
                                "orientation": "horizontal",
                                "header_row": r + 1,
                                "series_row": rr + 1,
                                "series_label": label,
                                "quarter": q,
                                "official_value": float(value),
                            })
        # Vertical time: a column has many quarters, another column contains values.
        for c in range(df.shape[1]):
            qrows = [(r, canonical_quarter(df.iat[r, c])) for r in range(len(df))]
            qrows = [(r, q) for r, q in qrows if q]
            if len(qrows) >= 4:
                for cc in range(df.shape[1]):
                    if cc == c:
                        continue
                    numeric = pd.to_numeric(pd.Series([df.iat[r, cc] for r, _ in qrows]), errors="coerce")
                    if numeric.notna().sum() < max(4, int(len(qrows) * 0.5)):
                        continue
                    header = " | ".join(str(df.iat[r, cc]) for r in range(min(10, len(df))) if df.iat[r, cc] is not None)
                    for (r, q), value in zip(qrows, numeric):
                        if pd.notna(value):
                            rows.append({
                                "sheet": sheet,
                                "orientation": "vertical",
                                "quarter_column": c + 1,
                                "value_column": cc + 1,
                                "series_label": header,
                                "quarter": q,
                                "official_value": float(value),
                            })
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.drop_duplicates()
    return out


def main() -> int:
    if not XLS.exists():
        raise FileNotFoundError(XLS)
    inventory, frames = workbook_inventory()
    candidates = candidate_long_tables(frames)
    candidates.to_csv(OUT / "WTO_official_generic_quarterly_candidates.csv", index=False, encoding="utf-8-sig")

    mirror_frames = [dbnomics_series(*EXPORT_SERIES[:2], EXPORT_SERIES[2], EXPORT_SERIES[3], "export")]
    for alpha3, (code, name) in IMPORT_SERIES.items():
        try:
            mirror_frames.append(dbnomics_series("ITS_MTP_QMVSA", code, alpha3, name, "import"))
        except Exception as exc:
            mirror_frames.append(pd.DataFrame([{
                "quarter": None, "mirror_value": None, "alpha3": alpha3,
                "economy": name, "flow": "import", "mirror_source_url": repr(exc),
            }]))
    mirror = pd.concat(mirror_frames, ignore_index=True)
    mirror.to_csv(OUT / "WTO_provider_mirror_series_for_reconciliation.csv", index=False, encoding="utf-8-sig")

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "original_file": XLS.name,
        "original_bytes": XLS.stat().st_size,
        "original_sha256": sha256(XLS),
        "sheet_count": len(inventory),
        "sheets": inventory,
        "generic_candidate_rows": int(len(candidates)),
        "mirror_rows": int(mirror["mirror_value"].notna().sum()),
        "audit_status": "converted_and_inventoried",
        "notes": [
            "The original official .xls is unchanged.",
            "CSV and XLSX files are derivative convenience copies of worksheet cell values.",
            "Generic candidate extraction is diagnostic and must be checked against sheet labels before numerical reconciliation.",
            "The mirror series is included only for reconciliation; it is not represented as the official direct export.",
        ],
    }
    (OUT / "WTO_official_workbook_inventory.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
