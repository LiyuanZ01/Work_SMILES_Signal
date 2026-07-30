#!/usr/bin/env python3
"""Discover current NBS EasyQuery indicator codes for the v18 provincial module.

Read-only, sequential requests to the official National Data portal. The output
contains full raw metadata, flattened catalogs, keyword candidates, hashes, and
an explicit ambiguity status. No indicator is selected merely because it is the
first search result.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

BASE = "https://data.stats.gov.cn/easyquery.htm"
OUT = Path("customs_v19/nbs_discovery")
OUT.mkdir(parents=True, exist_ok=True)

TASKS = [
    ("NQ1", "fsjd", "地区生产总值", ["亿元", "万元"]),
    ("NQ2", "fsjd", "国内生产总值增长速度", ["%", "上年同期"]),
    ("NQ2B", "fsjd", "地区生产总值指数", ["上年同期", "%"]),
    ("NM1", "fsyd", "各地区工业增加值增长速度", ["%", "上年同月", "上年同期"]),
    ("NM1B", "fsyd", "规模以上工业增加值增长速度", ["%", "上年同月", "上年同期"]),
    ("NM2", "fsyd", "各地区固定资产投资增长速度", ["%"]),
    ("NM3", "fsyd", "各地区社会消费品零售总额", ["亿元", "%"]),
    ("NA1", "fsnd", "地区生产总值增长速度", ["%", "上年"]),
    ("NA2", "fsnd", "人均地区生产总值", ["元", "万元"]),
    ("NA3", "fsnd", "货物出口额", ["亿元", "万美元"]),
    ("NA3B", "fsnd", "出口总额", ["亿元", "万美元"]),
]


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value).lower())


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blocked(text: str, status: int) -> str | None:
    if status in {401, 403, 429}:
        return f"HTTP_{status}"
    markers = ["验证码", "访问过于频繁", "安全验证", "请登录", "captcha", "access denied"]
    low = norm(text)
    for marker in markers:
        if norm(marker) in low:
            return marker
    return None


def request_json(session: requests.Session, params: dict[str, Any]) -> tuple[dict[str, Any], str, bytes]:
    last: Exception | None = None
    for attempt in range(5):
        try:
            response = session.get(BASE, params=params, timeout=60)
            hit = blocked(response.text, response.status_code)
            if hit:
                raise RuntimeError(f"portal_block:{hit}")
            response.raise_for_status()
            raw = response.content
            payload = response.json()
            time.sleep(1.5 + random.uniform(0, 0.35))
            return payload, response.url, raw
        except Exception as exc:
            last = exc
            if "portal_block:" in str(exc):
                raise
            if attempt < 4:
                time.sleep(min(20, 2 ** attempt + random.random()))
    raise RuntimeError(f"request_failed:{last}")


def collect_nodes(obj: Any, path: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        code = obj.get("id") or obj.get("code") or obj.get("valuecode") or obj.get("nodeid")
        name = obj.get("name") or obj.get("cname") or obj.get("nodename") or obj.get("value")
        if code is not None and name is not None:
            rows.append({
                "indicator_code": str(code),
                "indicator_name": str(name),
                "unit": obj.get("unit"),
                "memo": obj.get("memo") or obj.get("exp"),
                "is_parent": obj.get("isParent") or obj.get("isparent"),
                "parent_id": obj.get("pid") or obj.get("parentid"),
                "json_path": path,
                "raw_node": json.dumps(obj, ensure_ascii=False, sort_keys=True),
            })
        for key, value in obj.items():
            rows.extend(collect_nodes(value, f"{path}/{key}"))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            rows.extend(collect_nodes(value, f"{path}/{index}"))
    return rows


def candidate_score(row: pd.Series, keyword: str, units: list[str]) -> tuple[int, int, int]:
    name = norm(row.get("indicator_name"))
    target = norm(keyword)
    exact = int(name == target)
    contains = int(target in name or name in target)
    text = norm(" ".join(str(row.get(x) or "") for x in ["unit", "memo", "indicator_name"]))
    unit = int(any(norm(u) in text for u in units))
    return exact, contains, unit


def main() -> int:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        "Referer": "https://data.stats.gov.cn/",
    })

    catalogs: dict[str, pd.DataFrame] = {}
    requests_log = []
    for dbcode in sorted({task[1] for task in TASKS}):
        params = {
            "m": "getTree", "dbcode": dbcode, "wdcode": "zb", "id": "zb",
            "k1": int(time.time() * 1000),
        }
        payload, url, raw = request_json(session, params)
        (OUT / f"{dbcode}_tree_raw.json").write_bytes(raw)
        rows = collect_nodes(payload)
        catalog = pd.DataFrame(rows)
        if catalog.empty:
            catalog = pd.DataFrame(columns=[
                "indicator_code", "indicator_name", "unit", "memo", "is_parent",
                "parent_id", "json_path", "raw_node",
            ])
        catalog.insert(0, "dbcode", dbcode)
        catalog = catalog.drop_duplicates(["indicator_code", "indicator_name"])
        catalog.to_csv(OUT / f"{dbcode}_indicator_catalog.csv", index=False, encoding="utf-8-sig")
        catalogs[dbcode] = catalog
        requests_log.append({
            "dbcode": dbcode, "url": url, "bytes": len(raw), "sha256": digest(raw),
            "catalog_rows": len(catalog),
        })

    resolution = []
    for task_id, dbcode, keyword, units in TASKS:
        catalog = catalogs[dbcode].copy()
        target = norm(keyword)
        mask = catalog["indicator_name"].map(norm).apply(
            lambda name: bool(target and (target in name or name in target))
        )
        candidates = catalog.loc[mask].copy()
        if not candidates.empty:
            scores = candidates.apply(lambda row: candidate_score(row, keyword, units), axis=1)
            candidates["exact_name"] = [x[0] for x in scores]
            candidates["keyword_overlap"] = [x[1] for x in scores]
            candidates["unit_or_memo_match"] = [x[2] for x in scores]
            candidates = candidates.sort_values(
                ["exact_name", "keyword_overlap", "unit_or_memo_match", "indicator_name"],
                ascending=[False, False, False, True],
            )
        candidates.to_csv(OUT / f"{task_id}_candidates.csv", index=False, encoding="utf-8-sig")
        if len(candidates) == 1:
            state = "UNIQUE_CANDIDATE_REQUIRES_METADATA_REVIEW"
            selected = str(candidates.iloc[0]["indicator_code"])
        elif len(candidates) == 0:
            state = "NO_CANDIDATE"
            selected = None
        else:
            top = candidates.iloc[0] if not candidates.empty else None
            top_score = (
                int(top["exact_name"]), int(top["keyword_overlap"]), int(top["unit_or_memo_match"])
            ) if top is not None else None
            tied = 0 if top_score is None else int((
                (candidates["exact_name"] == top_score[0])
                & (candidates["keyword_overlap"] == top_score[1])
                & (candidates["unit_or_memo_match"] == top_score[2])
            ).sum())
            state = "TOP_CANDIDATE_REQUIRES_REVIEW" if tied == 1 else "AMBIGUOUS"
            selected = str(top["indicator_code"]) if top is not None and tied == 1 else None
        resolution.append({
            "task_id": task_id, "dbcode": dbcode, "keyword": keyword,
            "expected_units": " | ".join(units), "candidate_count": len(candidates),
            "status": state, "provisional_code_not_frozen": selected,
        })

    pd.DataFrame(requests_log).to_csv(OUT / "request_log.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(resolution).to_csv(OUT / "candidate_resolution.csv", index=False, encoding="utf-8-sig")
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "official_portal": "https://data.stats.gov.cn/",
        "endpoint": BASE,
        "status": "DISCOVERY_COMPLETE",
        "requests": requests_log,
        "tasks": resolution,
        "rule": "No code is frozen without inspecting indicator name, unit, memo, frequency, and observation coverage.",
    }
    (OUT / "discovery_status.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
