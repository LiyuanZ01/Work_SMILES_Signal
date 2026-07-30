#!/usr/bin/env python3
"""Diagnose current access to the public China Customs statistics portal.

This script makes only a few sequential read-only requests. It does not bypass
CAPTCHA, login, access controls, or rate limiting. Raw responses and hashes are
saved so the collection route can be frozen before the full v17 run.
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests

BASE = "https://stats.customs.gov.cn"
OUT = Path("external_demand_wto/customs_diagnostic")
OUT.mkdir(parents=True, exist_ok=True)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def summarize_response(name: str, response: requests.Response) -> dict:
    raw_path = OUT / f"{name}.bin"
    raw_path.write_bytes(response.content)
    text = response.text
    (OUT / f"{name}.txt").write_text(text, encoding="utf-8", errors="replace")
    table_summaries = []
    try:
        frames = pd.read_html(io.StringIO(text))
        for i, frame in enumerate(frames[:5]):
            table_summaries.append({
                "index": i,
                "rows": int(len(frame)),
                "columns": [str(x) for x in frame.columns],
                "head": frame.head(3).astype(str).to_dict(orient="records"),
            })
    except Exception as exc:
        table_summaries.append({"parse_error": repr(exc)})
    return {
        "name": name,
        "requested_url": response.request.url,
        "method": response.request.method,
        "status_code": response.status_code,
        "final_url": response.url,
        "content_type": response.headers.get("content-type"),
        "content_length": len(response.content),
        "sha256": sha256(response.content),
        "cookies": list(response.cookies.keys()),
        "text_prefix": text[:1000],
        "table_summaries": table_summaries,
    }


def main() -> int:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        "Referer": BASE + "/",
    })
    rows = []

    def attempt(name: str, method: str, url: str, **kwargs) -> None:
        try:
            response = session.request(method, url, timeout=90, allow_redirects=True, **kwargs)
            rows.append(summarize_response(name, response))
        except Exception as exc:
            rows.append({
                "name": name,
                "method": method,
                "url": url,
                "error": repr(exc),
            })

    attempt("home", "GET", BASE + "/")
    attempt("partner_codes_get", "GET", BASE + "/queryData/selectOriginCountry", params={"countryList": ""})

    common = {
        "pageSize": 20000,
        "pageNum": 1,
        "iEType": 1,
        "currencyType": "rmb",
        "year": 2017,
        "startMonth": 1,
        "endMonth": 1,
        "monthFlag": 1,
        "unitFlag": "false",
        "unitFlag1": "false",
        "codeLength": 8,
        "outerField3": "",
        "outerField4": "",
        "outerValue1": "",
        "outerValue2": "",
        "outerValue3": "",
        "outerValue4": "",
        "orderType": "CODE ASC DEFAULT",
        "selectTableState": 2,
        "currentStartTime": "201701",
        "currentEndTime": "201701",
        "currentDateBySource": "201701",
    }
    b1 = {**common, "outerField1": "ORIGIN_COUNTRY", "outerField2": "TRADE_CO_PORT"}
    b1t = {**common, "outerField1": "TRADE_CO_PORT", "outerField2": ""}

    attempt("b1_get", "GET", BASE + "/queryData/queryDataList", params=b1)
    attempt("b1_post", "POST", BASE + "/queryData/queryDataList", data=b1)
    attempt("b1t_get", "GET", BASE + "/queryData/queryDataList", params=b1t)
    attempt("b1t_post", "POST", BASE + "/queryData/queryDataList", data=b1t)

    status = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE,
        "request_count": len(rows),
        "requests": rows,
        "interpretation": (
            "Diagnostic only. A successful HTTP response is not treated as valid data unless "
            "the returned table contains the requested customs dimensions and monetary fields."
        ),
    }
    (OUT / "diagnostic_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
