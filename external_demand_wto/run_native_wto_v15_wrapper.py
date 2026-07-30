#!/usr/bin/env python3
"""Run v15 with explicit handling for unavailable partner and REER sources."""

from __future__ import annotations

import numpy as np
import pandas as pd

import run_native_wto_v15 as pipeline


def download_wto_data_allowing_unavailable_series(dl):
    dataset, code, alpha3, economy = pipeline.EXPORT_SERIES
    exports = pipeline.fetch_dbnomics_series(
        dl, dataset, code, alpha3, economy, "export"
    )
    import_frames = []
    for partner, (series_code, partner_name) in pipeline.IMPORT_SERIES.items():
        try:
            frame = pipeline.fetch_dbnomics_series(
                dl,
                "ITS_MTP_QMVSA",
                series_code,
                partner,
                partner_name,
                "import",
            )
            frame["source_status"] = "available"
        except RuntimeError as exc:
            if "404" not in str(exc):
                raise
            url = (
                f"{pipeline.DBNOMICS_BASE}/WTO/ITS_MTP_QMVSA/"
                f"{series_code}?observations=1"
            )
            frame = pd.DataFrame(
                {
                    "quarter": pipeline.quarter_range(),
                    "value": np.nan,
                    "alpha3": partner,
                    "economy": partner_name,
                    "flow": "import",
                    "dataset": "ITS_MTP_QMVSA",
                    "series_code": series_code,
                    "source_url": url,
                    "source_status": "series_not_available_404",
                }
            )
        import_frames.append(frame)
    return exports, pd.concat(import_frames, ignore_index=True)


def download_reer_from_bis_dbnomics(dl):
    url = "https://api.db.nomics.world/v22/series/BIS/WS_EER/M.R.B.CN?observations=1"
    payload = dl.get_json("BIS_WS_EER_M.R.B.CN", url)
    doc = pipeline.find_dbnomics_doc(payload)
    periods = doc.get("period") or doc.get("periods")
    values = doc.get("value") or doc.get("values")
    if not isinstance(periods, list) or not isinstance(values, list):
        raise ValueError("Missing BIS REER periods or values")
    frame = pd.DataFrame({"month": periods, "reer_index": values})
    frame["date"] = pd.to_datetime(
        frame["month"].astype(str).str.slice(0, 7) + "-01",
        errors="coerce",
    )
    frame["reer_index"] = pd.to_numeric(frame["reer_index"], errors="coerce")
    frame = frame.dropna(subset=["date"]).copy()
    frame["quarter"] = frame["date"].dt.to_period("Q").astype(str)
    quarterly = frame.groupby("quarter", as_index=False)["reer_index"].mean()
    quarterly["reer_qoq"] = quarterly["reer_index"].pct_change(fill_method=None) * 100.0
    quarterly["source_url"] = url
    return quarterly[quarterly["quarter"].isin(pipeline.quarter_range())].copy()


pipeline.download_wto_data = download_wto_data_allowing_unavailable_series
pipeline.download_reer = download_reer_from_bis_dbnomics

if __name__ == "__main__":
    raise SystemExit(pipeline.main())
