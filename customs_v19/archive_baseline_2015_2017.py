#!/usr/bin/env python3
"""Archive the official 2015-2017 provincial baseline tables only.

Uses the exact table asset naming exposed by the official yearbook directories.
This is deliberately small so that the identification baseline is not delayed by
later outcome years. It records every status and hash and does not extract or
impute values.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

OUT = Path("customs_v19/baseline_2015_2017_assets")
OUT.mkdir(parents=True, exist_ok=True)

URLS = {
    # Edition 2016 reports 2015. The regional-trade table was still a single 11-9 entry.
    "2015_gdp": "https://www.stats.gov.cn/sj/ndsj/2016/html/C03-09.jpg",
    "2015_trade_11_9": "https://www.stats.gov.cn/sj/ndsj/2016/html/C11-09.jpg",
    # Editions 2017 and 2018 publish two separate regional-trade concepts.
    "2016_gdp": "https://www.stats.gov.cn/sj/ndsj/2017/html/C03-09.jpg",
    "2016_trade_11_8": "https://www.stats.gov.cn/sj/ndsj/2017/html/C11-08.jpg",
    "2016_trade_11_9": "https://www.stats.gov.cn/sj/ndsj/2017/html/C11-09.jpg",
    "2017_gdp": "https://www.stats.gov.cn/sj/ndsj/2018/html/C03-09.jpg",
    "2017_trade_11_8": "https://www.stats.gov.cn/sj/ndsj/2018/html/C11-08.jpg",
    "2017_trade_11_9": "https://www.stats.gov.cn/sj/ndsj/2018/html/C11-09.jpg",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept": "image/*,text/html,*/*",
        "Referer": "https://www.stats.gov.cn/sj/ndsj/",
    })
    records = []
    failures = []
    for name, url in URLS.items():
        try:
            response = session.get(url, timeout=60)
            record = {
                "name": name, "url": url, "status": response.status_code,
                "final_url": response.url,
                "content_type": response.headers.get("content-type"),
                "bytes": len(response.content), "sha256": sha(response.content),
            }
            if response.status_code == 200 and len(response.content) > 1000 and "image" in (response.headers.get("content-type") or "").lower():
                path = OUT / f"{name}.jpg"
                path.write_bytes(response.content)
                record["file"] = str(path)
            else:
                failures.append(name)
            records.append(record)
        except Exception as exc:
            records.append({"name": name, "url": url, "error": repr(exc)})
            failures.append(name)
    status = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "COMPLETE" if not failures else "PARTIAL",
        "records": records,
        "failures": failures,
        "rules": [
            "11-8 and 11-9 remain separate until the official table headings/notes are visually verified.",
            "The 2015 single 11-9 table is not assumed to equal either later concept without inspection.",
            "No OCR or value extraction is performed by this archive step.",
        ],
    }
    (OUT / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
