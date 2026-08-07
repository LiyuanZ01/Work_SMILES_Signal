#!/usr/bin/env python3
"""Archive WTO Stats public metadata and discover the current data/export API.

This is a read-only diagnostic against the public stats.wto.org web application.
It saves the exact indicator and territory catalogues, the deployed JavaScript
bundle, and all API-like endpoint strings. It does not use an API key or bypass
access controls.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

OUT = Path("external_demand_wto/wto_official_archive")
OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://stats.wto.org"
HOME = BASE + "/en"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get(session: requests.Session, url: str, name: str) -> tuple[bytes, dict]:
    r = session.get(url, timeout=180, allow_redirects=True)
    rec = {
        "name": name,
        "requested_url": url,
        "final_url": r.url,
        "status": r.status_code,
        "content_type": r.headers.get("content-type"),
        "bytes": len(r.content),
        "sha256": sha(r.content),
    }
    r.raise_for_status()
    (OUT / name).write_bytes(r.content)
    return r.content, rec


def flatten_strings(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from flatten_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from flatten_strings(v)
    elif value is not None:
        yield str(value)


def main() -> int:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept": "application/json,text/html,application/javascript,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": HOME,
    })
    records = []
    home, rec = get(session, HOME, "WTO_Stats_public_home_current.html")
    records.append(rec)
    html = home.decode("utf-8", errors="replace")
    scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, flags=re.I)
    bundle_urls = []
    for src in scripts:
        url = urljoin(HOME, src)
        if "bundle.js" in url or "main." in url or "main.bundle" in url:
            bundle_urls.append(url)
    endpoint_strings = set()
    for i, url in enumerate(dict.fromkeys(bundle_urls), 1):
        try:
            data, rec = get(session, url, f"WTO_Stats_bundle_{i:02d}.js")
            records.append(rec)
            text = data.decode("utf-8", errors="replace")
            for pat in [
                r"/api/[A-Za-z0-9_?=&{}:./-]+",
                r"api/[A-Za-z0-9_?=&{}:./-]+",
                r"https://stats\.wto\.org/api/[A-Za-z0-9_?=&{}:./-]+",
            ]:
                endpoint_strings.update(re.findall(pat, text))
        except Exception as exc:
            records.append({"name": f"bundle_{i}", "url": url, "error": repr(exc)})

    api_targets = {
        "WTO_Stats_getindicators.json": BASE + "/api/indicators/getindicators/1",
        "WTO_Stats_getreporters.json": BASE + "/api/indicators/getallterritories/1/Reporter",
        "WTO_Stats_getpartners.json": BASE + "/api/indicators/getallterritories/1/Partner",
        "WTO_Stats_getfrequency.json": BASE + "/api/indicators/getfrequency/1",
        "WTO_Stats_getyears.json": BASE + "/api/indicators/getyears",
        "WTO_Stats_getperiods.json": BASE + "/api/indicators/getdataddl/1/Period",
    }
    relevant = []
    for name, url in api_targets.items():
        try:
            data, rec = get(session, url, name)
            records.append(rec)
            payload = json.loads(data.decode("utf-8"))
            if "indicator" in name.lower():
                # Preserve complete objects whose serialized representation matches the target concept.
                def walk(x):
                    if isinstance(x, dict):
                        text = " ".join(flatten_strings(x))
                        if re.search(r"merchandise.*(export|import).*volume.*seasonally|seasonally.*merchandise.*volume", text, re.I | re.S):
                            relevant.append(x)
                        for v in x.values():
                            walk(v)
                    elif isinstance(x, list):
                        for v in x:
                            walk(v)
                walk(payload)
        except Exception as exc:
            records.append({"name": name, "url": url, "error": repr(exc)})

    endpoints = sorted(endpoint_strings)
    (OUT / "WTO_Stats_discovered_api_strings.txt").write_text("\n".join(endpoints), encoding="utf-8")
    # Deduplicate relevant objects by JSON representation.
    seen = set(); unique = []
    for obj in relevant:
        key = json.dumps(obj, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key); unique.append(obj)
    (OUT / "WTO_Stats_relevant_volume_indicators.json").write_text(json.dumps(unique, ensure_ascii=False, indent=2), encoding="utf-8")
    status = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "home": HOME,
        "records": records,
        "bundle_urls": bundle_urls,
        "api_string_count": len(endpoints),
        "relevant_indicator_object_count": len(unique),
        "next_step": "Use the archived deployed bundle and indicator objects to construct the exact public portal data/export request.",
    }
    (OUT / "WTO_Stats_public_api_discovery_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
