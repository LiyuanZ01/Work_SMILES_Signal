#!/usr/bin/env python3
"""Discover and archive static China Statistical Yearbook provincial tables.

The National Data EasyQuery endpoint may reject cloud requests, while the
published yearbook pages are static public documents. This script tries the
current and legacy official yearbook paths for editions 2016-2025, identifies
regional trade and GDP tables by their Chinese titles, downloads linked pages
or images, and probes predictable official Excel companions. It records every
attempt, response hash, content type, and table title. No values are imputed.
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

OUT = Path("customs_v19/yearbook_discovery")
OUT.mkdir(parents=True, exist_ok=True)

YEARS = range(2016, 2026)
INDEX_CANDIDATES = [
    "https://www.stats.gov.cn/sj/ndsj/{year}/indexch.htm",
    "https://www.stats.gov.cn/tjsj/ndsj/{year}/indexch.htm",
    "https://www.stats.gov.cn/sj/ndsj/{year}/indexeh.htm",
]
TARGETS = [
    "分地区货物进出口总额",
    "地区生产总值",
    "地区生产总值指数",
    "地区生产总值增长速度",
    "按地区分货物进出口总额",
    "各地区货物进出口总额",
]


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")[:160]


def request(session: requests.Session, url: str) -> tuple[requests.Response | None, dict]:
    record = {"url": url}
    try:
        response = session.get(url, timeout=90, allow_redirects=True)
        record.update({
            "status": response.status_code,
            "final_url": response.url,
            "content_type": response.headers.get("content-type"),
            "bytes": len(response.content),
            "sha256": sha(response.content),
        })
        return response, record
    except Exception as exc:
        record["error"] = repr(exc)
        return None, record


def companion_urls(url: str) -> list[str]:
    parsed = urlparse(url)
    path = parsed.path
    stems = []
    lower = path.lower()
    for extension in [".jpg", ".jpeg", ".png", ".htm", ".html"]:
        if lower.endswith(extension):
            stems.append(path[: -len(extension)])
            break
    if not stems:
        stems.append(path)
    candidates = []
    for stem in stems:
        variants = {stem, stem.replace("/html/", "/excel/"), stem.replace("/html/", "/xls/")}
        for variant in variants:
            for extension in [".xls", ".xlsx", ".csv", ".htm", ".html", ".jpg", ".png"]:
                candidates.append(parsed._replace(path=variant + extension, query="", fragment="").geturl())
    unique = []
    for value in candidates:
        if value not in unique and value != url:
            unique.append(value)
    return unique


def main() -> int:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/vnd.ms-excel,application/octet-stream,image/*,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    })
    attempts = []
    matched = []

    for edition in YEARS:
        index_response = None
        index_url = None
        for template in INDEX_CANDIDATES:
            candidate = template.format(year=edition)
            response, record = request(session, candidate)
            record.update({"edition": edition, "kind": "index_probe"})
            attempts.append(record)
            if response is not None and response.status_code == 200 and len(response.content) > 1000:
                index_response = response
                index_url = response.url
                break
        if index_response is None:
            continue

        index_path = OUT / f"yearbook_{edition}_index.html"
        index_path.write_bytes(index_response.content)
        soup = BeautifulSoup(index_response.content, "html.parser")
        anchors = soup.find_all("a")
        for anchor in anchors:
            title = " ".join(anchor.get_text(" ", strip=True).split())
            compact = norm(title)
            if not title or not any(norm(target) in compact for target in TARGETS):
                continue
            href = anchor.get("href")
            if not href:
                continue
            linked_url = urljoin(index_url, href)
            response, record = request(session, linked_url)
            record.update({
                "edition": edition,
                "kind": "matched_link",
                "title": title,
                "href": href,
                "index_url": index_url,
            })
            attempts.append(record)
            local_files = []
            if response is not None and response.status_code == 200:
                extension = Path(urlparse(response.url).path).suffix or ".bin"
                local = OUT / f"{edition}_{safe_name(title)}_{safe_name(Path(urlparse(response.url).path).stem)}{extension}"
                local.write_bytes(response.content)
                local_files.append(str(local))

                # Pages may embed table images or Excel downloads not visible in the index.
                ctype = (response.headers.get("content-type") or "").lower()
                if "html" in ctype or extension.lower() in {".htm", ".html"}:
                    page = BeautifulSoup(response.content, "html.parser")
                    for tag in page.find_all(["a", "img"]):
                        source = tag.get("href") or tag.get("src")
                        if not source:
                            continue
                        embedded = urljoin(response.url, source)
                        if any(x in embedded.lower() for x in [".xls", ".xlsx", ".csv", ".jpg", ".png"]):
                            embedded_response, embedded_record = request(session, embedded)
                            embedded_record.update({
                                "edition": edition,
                                "kind": "embedded_asset",
                                "title": title,
                                "parent_url": response.url,
                            })
                            attempts.append(embedded_record)
                            if embedded_response is not None and embedded_response.status_code == 200:
                                ext = Path(urlparse(embedded_response.url).path).suffix or ".bin"
                                file = OUT / f"{edition}_{safe_name(title)}_embedded_{safe_name(Path(urlparse(embedded_response.url).path).stem)}{ext}"
                                file.write_bytes(embedded_response.content)
                                local_files.append(str(file))

            # Probe predictable Excel/static companions.
            companion_hits = []
            for companion in companion_urls(linked_url):
                companion_response, companion_record = request(session, companion)
                companion_record.update({
                    "edition": edition,
                    "kind": "companion_probe",
                    "title": title,
                    "parent_url": linked_url,
                })
                attempts.append(companion_record)
                if companion_response is None or companion_response.status_code != 200:
                    continue
                ctype = (companion_response.headers.get("content-type") or "").lower()
                size = len(companion_response.content)
                # Keep only plausible non-error assets.
                if size < 200:
                    continue
                ext = Path(urlparse(companion_response.url).path).suffix or ".bin"
                if ext.lower() in {".xls", ".xlsx", ".csv", ".jpg", ".png"} or "excel" in ctype or "image" in ctype:
                    file = OUT / f"{edition}_{safe_name(title)}_companion_{safe_name(Path(urlparse(companion_response.url).path).stem)}{ext}"
                    file.write_bytes(companion_response.content)
                    companion_hits.append({
                        "url": companion_response.url,
                        "file": str(file),
                        "bytes": size,
                        "content_type": companion_response.headers.get("content-type"),
                        "sha256": sha(companion_response.content),
                    })
                    local_files.append(str(file))

            matched.append({
                "edition": edition,
                "data_year": edition - 1,
                "title": title,
                "index_url": index_url,
                "linked_url": linked_url,
                "linked_status": None if response is None else response.status_code,
                "linked_content_type": None if response is None else response.headers.get("content-type"),
                "local_files": local_files,
                "companion_hits": companion_hits,
            })

    (OUT / "attempt_log.json").write_text(json.dumps(attempts, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "matched_tables.json").write_text(json.dumps(matched, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "official_domain": "www.stats.gov.cn",
        "editions_attempted": list(YEARS),
        "matched_table_count": len(matched),
        "matched_tables": matched,
        "interpretation": [
            "Yearbook edition usually reports the preceding data year; data_year is recorded as edition-1 and must be checked against each table title.",
            "Registration-location and domestic-origin trade tables are kept separate.",
            "No missing province value is replaced with zero.",
        ],
    }
    (OUT / "yearbook_discovery_status.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if matched else 2


if __name__ == "__main__":
    raise SystemExit(main())
