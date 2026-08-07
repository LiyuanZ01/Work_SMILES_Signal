#!/usr/bin/env python3
"""Archive static official yearbook provincial trade and GDP tables (v2).

The visible yearbook index is a frameset; the actual table directory is
`left.htm`. This revision reads that directory directly, identifies only the
required provincial GDP and regional trade tables, downloads the linked assets,
and probes a short, documented list of text/Excel companions. It does not use
OCR and never treats an HTTP error page as data.
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

OUT = Path("customs_v19/yearbook_discovery_v2")
OUT.mkdir(parents=True, exist_ok=True)
YEARS = range(2016, 2026)
DIRECTORY_URL = "https://www.stats.gov.cn/sj/ndsj/{edition}/left.htm"


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")[:120]


def fetch(session: requests.Session, url: str) -> tuple[requests.Response | None, dict]:
    record = {"url": url}
    try:
        response = session.get(url, timeout=60, allow_redirects=True)
        record.update({
            "status": response.status_code,
            "final_url": response.url,
            "content_type": response.headers.get("content-type"),
            "bytes": len(response.content),
            "sha256": digest(response.content),
        })
        return response, record
    except Exception as exc:
        record["error"] = repr(exc)
        return None, record


def classify(title: str) -> str | None:
    name = compact(title)
    if re.match(r"^3-9地区生产总值", name):
        return "regional_gdp"
    if re.match(r"^11-8分地区货物进出口总额", name):
        return "regional_trade_11_8"
    if re.match(r"^11-9分地区货物进出口总额", name):
        return "regional_trade_11_9"
    # Before 2017 the single regional-trade table was usually numbered 11-9.
    if "分地区货物进出口总额" in name:
        return "regional_trade_other"
    return None


def probe_companions(url: str) -> list[str]:
    parsed = urlparse(url)
    path = parsed.path
    suffix = Path(path).suffix
    stem = path[: -len(suffix)] if suffix else path
    basename = Path(stem).name
    parent = str(Path(stem).parent).replace("\\", "/")
    variants = [
        f"{stem}.xls", f"{stem}.xlsx", f"{stem}C.HTM", f"{stem}C.htm",
        f"{parent}/{basename.replace('-', '')}C.HTM",
        f"{parent}/{basename.replace('-', '')}C.htm",
        f"{parent.replace('/html', '/excel')}/{basename}.xls",
        f"{parent.replace('/html', '/excel')}/{basename}.xlsx",
    ]
    urls = []
    for variant in variants:
        candidate = parsed._replace(path=variant, query="", fragment="").geturl()
        if candidate != url and candidate not in urls:
            urls.append(candidate)
    return urls


def plausible(response: requests.Response) -> bool:
    if response.status_code != 200 or len(response.content) < 300:
        return False
    ctype = (response.headers.get("content-type") or "").lower()
    prefix = response.content[:300].lower()
    if b"404" in prefix and b"not found" in prefix:
        return False
    if b"502 bad gateway" in prefix:
        return False
    return any(token in ctype for token in ["image", "excel", "spreadsheet", "html", "octet-stream"])


def main() -> int:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/vnd.ms-excel,application/octet-stream,image/*,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    })
    attempts = []
    tables = []

    for edition in YEARS:
        directory_url = DIRECTORY_URL.format(edition=edition)
        response, record = fetch(session, directory_url)
        record.update({"edition": edition, "kind": "directory"})
        attempts.append(record)
        if response is None or response.status_code != 200 or len(response.content) < 5000:
            continue
        directory_file = OUT / f"{edition}_left.htm"
        directory_file.write_bytes(response.content)
        soup = BeautifulSoup(response.content, "html.parser")
        for anchor in soup.find_all("a"):
            title = " ".join(anchor.get_text(" ", strip=True).split())
            table_kind = classify(title)
            href = anchor.get("href")
            if not table_kind or not href:
                continue
            linked = urljoin(response.url, href)
            asset, asset_record = fetch(session, linked)
            asset_record.update({
                "edition": edition, "data_year_expected": edition - 1,
                "kind": "linked_asset", "table_kind": table_kind,
                "title": title, "href": href,
            })
            attempts.append(asset_record)
            saved = []
            if asset is not None and plausible(asset):
                ext = Path(urlparse(asset.url).path).suffix or ".bin"
                path = OUT / f"{edition}_{table_kind}_linked{ext}"
                path.write_bytes(asset.content)
                saved.append({
                    "role": "linked", "file": str(path), "url": asset.url,
                    "content_type": asset.headers.get("content-type"),
                    "bytes": len(asset.content), "sha256": digest(asset.content),
                })

            for candidate in probe_companions(linked):
                companion, companion_record = fetch(session, candidate)
                companion_record.update({
                    "edition": edition, "data_year_expected": edition - 1,
                    "kind": "companion", "table_kind": table_kind,
                    "title": title, "parent_url": linked,
                })
                attempts.append(companion_record)
                if companion is None or not plausible(companion):
                    continue
                ext = Path(urlparse(companion.url).path).suffix or ".bin"
                file = OUT / f"{edition}_{table_kind}_companion_{safe(Path(urlparse(companion.url).path).stem)}{ext}"
                file.write_bytes(companion.content)
                saved.append({
                    "role": "companion", "file": str(file), "url": companion.url,
                    "content_type": companion.headers.get("content-type"),
                    "bytes": len(companion.content), "sha256": digest(companion.content),
                })

            tables.append({
                "edition": edition,
                "data_year_expected": edition - 1,
                "table_kind": table_kind,
                "title": title,
                "directory_url": response.url,
                "linked_url": linked,
                "saved_assets": saved,
                "has_machine_readable_candidate": any(
                    item["file"].lower().endswith((".xls", ".xlsx", ".htm", ".html"))
                    for item in saved
                ),
            })

    (OUT / "attempt_log.json").write_text(json.dumps(attempts, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "table_inventory.json").write_text(json.dumps(tables, ensure_ascii=False, indent=2), encoding="utf-8")
    status = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "official_source": "China Statistical Yearbook, National Bureau of Statistics",
        "edition_range": [min(YEARS), max(YEARS)],
        "table_records": len(tables),
        "editions_with_trade": sorted({x["edition"] for x in tables if "trade" in x["table_kind"]}),
        "editions_with_gdp": sorted({x["edition"] for x in tables if x["table_kind"] == "regional_gdp"}),
        "machine_readable_records": sum(bool(x["has_machine_readable_candidate"]) for x in tables),
        "table_inventory": tables,
        "interpretation_limits": [
            "11-8 and 11-9 are retained as separate official trade concepts until table notes identify registration-location versus domestic-origin/destination.",
            "The reported data year is checked from the title; edition-1 is only the expected value.",
            "Images are archived but not converted to data by this discovery step.",
            "No missing province value is filled with zero.",
        ],
    }
    (OUT / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if tables else 2


if __name__ == "__main__":
    raise SystemExit(main())
