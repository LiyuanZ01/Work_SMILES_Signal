#!/usr/bin/env python3
"""Archive a direct WTO Stats portal export and its metadata.

The script first tries the official legacy short-term volume workbook URL exposed
by the WTO statistics page. It then launches a real Chromium browser against
stats.wto.org, records relevant network responses, captures full-page evidence,
and attempts a direct CSV/XLSX export from the interactive portal.

No DBnomics data are used to create the official export. If the portal export
cannot be completed, the script records an explicit failure status rather than
substituting mirror observations.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from playwright.async_api import async_playwright

OUT = Path("external_demand_wto/wto_official_archive")
OUT.mkdir(parents=True, exist_ok=True)
PORTAL = "https://stats.wto.org/en"
LEGACY_XLS = "https://www.wto.org/english/res_e/statis_e/daily_update_e/quarterly_merch_trade_volume_e.xls"
SHORT_TERM_PAGE = "https://www.wto.org/english/res_e/statis_e/short_term_stats_e.htm"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_bytes(name: str, data: bytes) -> dict[str, Any]:
    path = OUT / name
    path.write_bytes(data)
    return {"file": name, "bytes": len(data), "sha256": sha256(data)}


def direct_http_archive() -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept": "application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv,text/html,*/*",
    })
    records: list[dict[str, Any]] = []
    for name, url in [("wto_short_term_page.html", SHORT_TERM_PAGE), ("wto_legacy_quarterly_volume.xls", LEGACY_XLS)]:
        rec: dict[str, Any] = {"url": url}
        try:
            r = session.get(url, timeout=120, allow_redirects=True)
            rec.update({
                "status": r.status_code,
                "final_url": r.url,
                "content_type": r.headers.get("content-type"),
                "content_disposition": r.headers.get("content-disposition"),
                "bytes": len(r.content),
                "sha256": sha256(r.content),
                "prefix_hex": r.content[:16].hex(),
            })
            if r.status_code == 200 and len(r.content) > 0:
                rec.update(save_bytes(name, r.content))
        except Exception as exc:
            rec["error"] = repr(exc)
        records.append(rec)
    return records


async def portal_archive() -> dict[str, Any]:
    network: list[dict[str, Any]] = []
    downloads: list[dict[str, Any]] = []
    console: list[str] = []
    status: dict[str, Any] = {"portal": PORTAL, "attempts": []}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            accept_downloads=True,
            viewport={"width": 1600, "height": 1000},
            locale="en-US",
        )
        page = await context.new_page()

        page.on("console", lambda msg: console.append(f"{msg.type}: {msg.text}"))

        async def on_response(response):
            url = response.url
            low = url.lower()
            if any(token in low for token in ["api", "csv", "xlsx", "excel", "download", "export", "timeseries"]):
                entry = {
                    "url": url,
                    "status": response.status,
                    "content_type": response.headers.get("content-type"),
                    "content_disposition": response.headers.get("content-disposition"),
                }
                try:
                    body = await response.body()
                    entry["bytes"] = len(body)
                    entry["sha256"] = sha256(body)
                    ctype = (entry.get("content_type") or "").lower()
                    disp = (entry.get("content_disposition") or "").lower()
                    if len(body) > 100 and (
                        "csv" in ctype or "excel" in ctype or "spreadsheet" in ctype
                        or "attachment" in disp or url.lower().endswith((".csv", ".xlsx", ".xls"))
                    ):
                        ext = ".csv" if "csv" in ctype or url.lower().endswith(".csv") else ".xlsx"
                        filename = f"portal_network_export_{len(downloads)+1}{ext}"
                        entry.update(save_bytes(filename, body))
                        downloads.append(entry.copy())
                except Exception as exc:
                    entry["body_error"] = repr(exc)
                network.append(entry)

        page.on("response", on_response)

        try:
            await page.goto(PORTAL, wait_until="domcontentloaded", timeout=180000)
            await page.wait_for_timeout(30000)
        except Exception as exc:
            status["navigation_error"] = repr(exc)

        await page.screenshot(path=str(OUT / "WTO_Stats_portal_home.png"), full_page=True)
        html = await page.content()
        (OUT / "WTO_Stats_portal_home.html").write_text(html, encoding="utf-8")
        try:
            text = await page.locator("body").inner_text(timeout=30000)
        except Exception:
            text = ""
        (OUT / "WTO_Stats_portal_visible_text.txt").write_text(text, encoding="utf-8")

        # Inventory interactive controls for reproducible selector development.
        controls = await page.locator("button, [role=button], a, input, select").evaluate_all(
            "els => els.slice(0,1000).map((e,i)=>({i,tag:e.tagName,role:e.getAttribute('role'),text:(e.innerText||e.value||e.getAttribute('aria-label')||e.title||'').trim(),id:e.id,cls:e.className,name:e.getAttribute('name'),type:e.getAttribute('type')}))"
        )
        (OUT / "WTO_Stats_portal_controls.json").write_text(json.dumps(controls, ensure_ascii=False, indent=2), encoding="utf-8")

        async def try_click(label: str, exact: bool = False) -> bool:
            patterns = [
                page.get_by_text(label, exact=exact),
                page.get_by_role("button", name=re.compile(re.escape(label), re.I)),
                page.get_by_role("link", name=re.compile(re.escape(label), re.I)),
            ]
            for loc in patterns:
                try:
                    if await loc.count() > 0:
                        await loc.first.click(timeout=15000)
                        await page.wait_for_timeout(2500)
                        status["attempts"].append({"click": label, "success": True})
                        return True
                except Exception as exc:
                    status["attempts"].append({"click": label, "success": False, "error": repr(exc)})
            return False

        # Best-effort path matching the official WTO extraction instructions.
        for label in [
            "International Trade Statistics",
            "Merchandise trade — indices and prices",
            "Merchandise trade - indices and prices",
            "Merchandise export or import volume indices, seasonally adjusted",
            "Merchandise export volume indices, seasonally adjusted",
            "Apply",
        ]:
            await try_click(label)

        await page.screenshot(path=str(OUT / "WTO_Stats_portal_selection_attempt.png"), full_page=True)
        html2 = await page.content()
        (OUT / "WTO_Stats_portal_selection_attempt.html").write_text(html2, encoding="utf-8")

        # Try common export labels/icons. Downloads are captured natively.
        for export_label in ["CSV", "Excel", "XLSX", "Export", "Download"]:
            locators = [
                page.get_by_role("button", name=re.compile(export_label, re.I)),
                page.get_by_role("link", name=re.compile(export_label, re.I)),
                page.get_by_text(export_label, exact=True),
                page.locator(f"[title*='{export_label}' i], [aria-label*='{export_label}' i]"),
            ]
            for loc in locators:
                try:
                    if await loc.count() == 0:
                        continue
                    async with page.expect_download(timeout=20000) as info:
                        await loc.first.click(timeout=15000)
                    dl = await info.value
                    suggested = dl.suggested_filename or f"wto_portal_{export_label.lower()}.bin"
                    target = OUT / suggested
                    await dl.save_as(str(target))
                    body = target.read_bytes()
                    downloads.append({
                        "source": "browser_download",
                        "label": export_label,
                        "suggested_filename": suggested,
                        "file": target.name,
                        "bytes": len(body),
                        "sha256": sha256(body),
                    })
                    status["attempts"].append({"export": export_label, "success": True, "file": target.name})
                    break
                except Exception as exc:
                    status["attempts"].append({"export": export_label, "success": False, "error": repr(exc)})

        await context.close()
        await browser.close()

    (OUT / "WTO_Stats_network_log.json").write_text(json.dumps(network, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "WTO_Stats_console_log.txt").write_text("\n".join(console), encoding="utf-8")
    status["downloads"] = downloads
    status["network_entries"] = len(network)
    status["complete_direct_portal_export"] = any(
        str(d.get("file", "")).lower().endswith((".csv", ".xlsx", ".xls")) and d.get("bytes", 0) > 500
        for d in downloads
    )
    return status


async def main() -> int:
    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "official_http": direct_http_archive(),
        "portal_browser": await portal_archive(),
        "statement": (
            "A direct WTO-origin file is accepted only when downloaded from a wto.org or stats.wto.org URL. "
            "Mirror data are never substituted for this archival requirement."
        ),
    }
    (OUT / "WTO_official_archive_status.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["portal_browser"].get("complete_direct_portal_export") or any(
        x.get("file", "").endswith(".xls") and x.get("bytes", 0) > 500 for x in result["official_http"]
    ) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
