#!/usr/bin/env python3
"""Quickly archive only the linked official yearbook assets needed for v19.

This avoids speculative companion probing. It reads each official `left.htm`,
selects regional GDP and regional trade entries, downloads exactly the linked
asset, and records hashes. The resulting images/pages are the authoritative
source archive for the next extraction step.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

OUT = Path("customs_v19/yearbook_linked_assets")
OUT.mkdir(parents=True, exist_ok=True)


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def classify(title: str) -> str | None:
    value = compact(title)
    if value.startswith("3-9地区生产总值"):
        return "regional_gdp"
    if value.startswith("11-8分地区货物进出口总额"):
        return "trade_11_8"
    if value.startswith("11-9分地区货物进出口总额"):
        return "trade_11_9"
    return None


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept": "text/html,image/*,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    })
    records = []
    for edition in range(2016, 2026):
        directory_url = f"https://www.stats.gov.cn/sj/ndsj/{edition}/left.htm"
        directory = session.get(directory_url, timeout=30)
        directory.raise_for_status()
        directory_path = OUT / f"{edition}_left.htm"
        directory_path.write_bytes(directory.content)
        soup = BeautifulSoup(directory.content, "html.parser")
        for anchor in soup.find_all("a"):
            title = " ".join(anchor.get_text(" ", strip=True).split())
            kind = classify(title)
            href = anchor.get("href")
            if not kind or not href:
                continue
            url = urljoin(directory.url, href)
            response = session.get(url, timeout=30)
            response.raise_for_status()
            extension = Path(urlparse(response.url).path).suffix or ".bin"
            path = OUT / f"{edition}_{edition-1}_{kind}{extension}"
            path.write_bytes(response.content)
            records.append({
                "edition": edition,
                "expected_data_year": edition - 1,
                "kind": kind,
                "title": title,
                "directory_url": directory.url,
                "href": href,
                "url": response.url,
                "content_type": response.headers.get("content-type"),
                "bytes": len(response.content),
                "sha256": sha(response.content),
                "file": str(path),
            })
    status = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "COMPLETE" if records else "NO_RECORDS",
        "records": records,
        "counts_by_kind": {
            kind: sum(x["kind"] == kind for x in records)
            for kind in sorted({x["kind"] for x in records})
        },
        "note": "Linked assets only; no OCR or value extraction is performed here.",
    }
    (OUT / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if records else 2


if __name__ == "__main__":
    raise SystemExit(main())
