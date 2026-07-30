#!/usr/bin/env python3
"""Run v15 while treating unavailable WTO partner series as missing, never zero."""

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


pipeline.download_wto_data = download_wto_data_allowing_unavailable_series

if __name__ == "__main__":
    raise SystemExit(pipeline.main())
