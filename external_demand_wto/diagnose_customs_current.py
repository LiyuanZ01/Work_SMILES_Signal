#!/usr/bin/env python3
"""Diagnose current access to the public China Customs statistics portal.

The script first uses normal TLS verification. If that fails specifically because
of the portal's certificate chain, it repeats the small diagnostic request set
with verification disabled and marks every such response as transport-unverified.
It never bypasses CAPTCHA, login, access controls, or rate limiting.
"""
from __future__ import annotations

import hashlib
import io
import json
import ssl
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import urllib3

BASE = "https://stats.customs.gov.cn"
OUT = Path("external_demand_wto/customs_diagnostic")
OUT.mkdir(parents=True, exist_ok=True)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def summarize_response(name: str, response: requests.Response, verify_tls: bool) -> dict:
    suffix = "strict" if verify_tls else "unverified_tls"
    raw_path = OUT / f"{name}_{suffix}.bin"
    raw_path.write_bytes(response.content)
    text = response.text
    (OUT / f"{name}_{suffix}.txt").write_text(text, encoding="utf-8", errors="replace")
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
    lower = text.lower()
    block_markers = [
        marker for marker in ["验证码", "访问过于频繁", "安全验证", "请登录", "captcha", "access denied"]
        if marker.lower() in lower
    ]
    return {
        "name": name,
        "tls_verification": verify_tls,
        "transport_authenticated": verify_tls,
        "requested_url": response.request.url,
        "method": response.request.method,
        "status_code": response.status_code,
        "final_url": response.url,
        "content_type": response.headers.get("content-type"),
        "content_length": len(response.content),
        "sha256": sha256(response.content),
        "cookies": list(response.cookies.keys()),
        "block_markers": block_markers,
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
    tests = [
        ("home", "GET", BASE + "/", {}),
        ("partner_codes_get", "GET", BASE + "/queryData/selectOriginCountry", {"params": {"countryList": ""}}),
        ("b1_get", "GET", BASE + "/queryData/queryDataList", {"params": b1}),
        ("b1_post", "POST", BASE + "/queryData/queryDataList", {"data": b1}),
        ("b1t_get", "GET", BASE + "/queryData/queryDataList", {"params": b1t}),
        ("b1t_post", "POST", BASE + "/queryData/queryDataList", {"data": b1t}),
    ]

    for name, method, url, kwargs in tests:
        strict_failed_tls = False
        try:
            response = session.request(method, url, timeout=90, allow_redirects=True, verify=True, **kwargs)
            rows.append(summarize_response(name, response, True))
        except requests.exceptions.SSLError as exc:
            strict_failed_tls = True
            rows.append({
                "name": name,
                "tls_verification": True,
                "transport_authenticated": False,
                "method": method,
                "url": url,
                "error": repr(exc),
            })
        except Exception as exc:
            rows.append({
                "name": name,
                "tls_verification": True,
                "transport_authenticated": False,
                "method": method,
                "url": url,
                "error": repr(exc),
            })

        if strict_failed_tls:
            try:
                response = session.request(method, url, timeout=90, allow_redirects=True, verify=False, **kwargs)
                rows.append(summarize_response(name, response, False))
            except Exception as exc:
                rows.append({
                    "name": name,
                    "tls_verification": False,
                    "transport_authenticated": False,
                    "method": method,
                    "url": url,
                    "error": repr(exc),
                })

    status = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE,
        "request_record_count": len(rows),
        "requests": rows,
        "interpretation": [
            "Diagnostic only; no response is accepted as data unless requested dimensions and monetary fields are present.",
            "Responses obtained with TLS verification disabled are transport-unverified and cannot by themselves satisfy the final replication archive gate.",
            "No CAPTCHA, login, or rate-limit control is bypassed.",
        ],
    }
    (OUT / "diagnostic_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
