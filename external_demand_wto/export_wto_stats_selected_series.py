#!/usr/bin/env python3
"""Export the paper's exact WTO Stats volume selections through the public UI.

Two independent single-indicator selections are made because the WTO API panel
accepts one indicator at a time:
1. China merchandise export volume index, seasonally adjusted, quarterly;
2. Selected destination economies' merchandise import volume indices,
   seasonally adjusted, quarterly.

Years are set to 2005–2024. Browser downloads, screenshots, selection summaries,
and full request/response metadata for WTO data endpoints are archived. The
script never substitutes mirror observations.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright, Page

OUT = Path("external_demand_wto/wto_exact_export")
OUT.mkdir(parents=True, exist_ok=True)
PORTAL = "https://stats.wto.org/en"

EXPORT_LABEL = "Merchandise export volume indices, seasonally adjusted - quarterly (2005Q1=100)"
IMPORT_LABEL = "Merchandise import volume indices, seasonally adjusted - quarterly (2005Q1=100)"

EXPORT_REPORTERS = {"China": 61}
IMPORT_REPORTERS = {
    "United States of America": 304,
    "Japan": 147,
    "Germany": 114,
    "Korea, Republic of": 153,
    "Netherlands": 195,
    "United Kingdom": 300,
    "Canada": 50,
    "Singapore": 254,
    "Mexico": 182,
    "Italy": 143,
    "Australia": 18,
    "France": 102,
    "India": 137,
    "Spain": 266,
    "Malaysia": 174,
    "Russian Federation": 239,
    "Thailand": 280,
    "Belgium": 27,
    "Türkiye": 288,
    "United Arab Emirates": 285,
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def expand_node(page: Page, text: str) -> None:
    nodes = page.locator("div.ui-treenode-content").filter(has_text=text)
    count = await nodes.count()
    if count == 0:
        raise RuntimeError(f"Tree node not found: {text}")
    node = nodes.first
    toggler = node.locator(".ui-tree-toggler")
    if await toggler.count():
        cls = await toggler.get_attribute("class") or ""
        if "fa-caret-right" in cls:
            await toggler.click(timeout=20000)
            await page.wait_for_timeout(1500)


async def select_indicator(page: Page, label: str) -> None:
    await expand_node(page, "Merchandise trade statistics")
    await expand_node(page, "Merchandise trade indices and prices")
    nodes = page.locator("div.ui-treenode-content").filter(has_text=label)
    if await nodes.count() == 0:
        # Diagnose loaded leaf labels.
        labels = await page.locator("span.ui-treenode-label").all_inner_texts()
        raise RuntimeError(f"Indicator leaf not found: {label}; loaded labels={labels[:200]}")
    node = nodes.first
    box = node.locator(".ui-chkbox-box")
    if await box.count() == 0:
        raise RuntimeError(f"Indicator checkbox not found: {label}")
    await box.click(timeout=20000)
    await page.wait_for_timeout(3000)


async def open_accordion(page: Page, text: str) -> None:
    headers = page.locator(".panel-heading").filter(has_text=text)
    if await headers.count() == 0:
        raise RuntimeError(f"Accordion not found: {text}")
    header = headers.first
    expanded = await header.locator("[role=button]").get_attribute("aria-expanded")
    if expanded != "true":
        await header.click(timeout=20000)
        await page.wait_for_timeout(1500)


async def select_reporters(page: Page, reporters: dict[str, int]) -> list[dict[str, Any]]:
    await open_accordion(page, "Reporting Economies")
    selected = []
    for name, rid in reporters.items():
        locator = page.locator(f"#country_reporter_{rid}")
        if await locator.count() == 0:
            raise RuntimeError(f"Reporter input not found: {name} / {rid}")
        await locator.check(force=True)
        selected.append({"name": name, "id": rid, "checked": await locator.is_checked()})
        await page.wait_for_timeout(150)
    return selected


async def select_years(page: Page) -> None:
    await open_accordion(page, "Years")
    start = page.locator("#ddlYearStart")
    end = page.locator("#ddlYearEnd")
    if await start.count() == 0 or await end.count() == 0:
        raise RuntimeError("Year range controls not found")
    await start.select_option(label="2005")
    await page.wait_for_timeout(750)
    await end.select_option(label="2024")
    await page.wait_for_timeout(2500)


async def click_apply(page: Page) -> None:
    candidates = [
        page.locator("#applyPivot"),
        page.get_by_role("button", name=re.compile(r"^\s*Apply\s*$", re.I)),
        page.get_by_text("Apply", exact=True),
        page.locator("button, a").filter(has_text=re.compile(r"^\s*Apply\s*$", re.I)),
    ]
    errors = []
    for locator in candidates:
        try:
            n = await locator.count()
            for i in range(n):
                loc = locator.nth(i)
                if await loc.is_visible():
                    await loc.click(timeout=20000)
                    await page.wait_for_timeout(15000)
                    return
        except Exception as exc:
            errors.append(repr(exc))
    controls = await page.locator("button, a").all_inner_texts()
    raise RuntimeError(f"Visible Apply control not found; controls={controls[-200:]}; errors={errors}")


async def download_link(page: Page, label: str, prefix: str) -> dict[str, Any] | None:
    candidates = [
        page.locator("a.linkAppereance").filter(has_text=re.compile(rf"\b{label}\b", re.I)),
        page.get_by_text(label, exact=True),
        page.locator(f"[title*='{label}' i], [aria-label*='{label}' i]"),
    ]
    for locator in candidates:
        n = await locator.count()
        for i in range(n):
            loc = locator.nth(i)
            try:
                if not await loc.is_visible():
                    continue
                async with page.expect_download(timeout=90000) as info:
                    await loc.click(timeout=30000)
                dl = await info.value
                suggested = dl.suggested_filename or f"{prefix}_{label.lower()}.bin"
                suffix = Path(suggested).suffix or (".zip" if label.upper() == "CSV" else ".xlsx")
                target = OUT / f"{prefix}_official_portal_{label.lower()}{suffix}"
                await dl.save_as(str(target))
                body = target.read_bytes()
                return {
                    "label": label,
                    "suggested_filename": suggested,
                    "file": target.name,
                    "bytes": len(body),
                    "sha256": sha(body),
                }
            except Exception:
                continue
    return None


async def run_selection(browser, prefix: str, indicator_label: str, reporters: dict[str, int]) -> dict[str, Any]:
    context = await browser.new_context(
        accept_downloads=True,
        viewport={"width": 1700, "height": 1100},
        locale="en-US",
    )
    page = await context.new_page()
    requests_log: list[dict[str, Any]] = []
    responses_log: list[dict[str, Any]] = []

    def on_request(req):
        low = req.url.lower()
        if "stats.wto.org/api/" in low:
            requests_log.append({
                "method": req.method,
                "url": req.url,
                "post_data": req.post_data,
                "headers": {k: v for k, v in req.headers.items() if k.lower() not in {"cookie", "authorization"}},
            })

    async def on_response(resp):
        if "stats.wto.org/api/" in resp.url.lower():
            entry = {
                "status": resp.status,
                "url": resp.url,
                "content_type": resp.headers.get("content-type"),
                "content_disposition": resp.headers.get("content-disposition"),
            }
            try:
                body = await resp.body()
                entry["bytes"] = len(body)
                entry["sha256"] = sha(body)
                # Save direct official data/export responses.
                ctype = (entry.get("content_type") or "").lower()
                disp = (entry.get("content_disposition") or "").lower()
                if len(body) > 100 and (
                    any(x in resp.url.lower() for x in ["downloadpivot", "executequery", "pivottable/view"])
                    or "attachment" in disp or "csv" in ctype or "excel" in ctype
                ):
                    ext = ".json"
                    if "csv" in ctype: ext = ".csv"
                    elif "excel" in ctype or "spreadsheet" in ctype: ext = ".xlsx"
                    elif "zip" in ctype or "octet-stream" in ctype: ext = ".bin"
                    file = OUT / f"{prefix}_network_{len(responses_log)+1:03d}{ext}"
                    file.write_bytes(body)
                    entry["file"] = file.name
            except Exception as exc:
                entry["body_error"] = repr(exc)
            responses_log.append(entry)

    page.on("request", on_request)
    page.on("response", on_response)

    result: dict[str, Any] = {
        "prefix": prefix,
        "indicator": indicator_label,
        "reporters": reporters,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await page.goto(PORTAL, wait_until="domcontentloaded", timeout=180000)
        await page.wait_for_timeout(30000)
        await select_indicator(page, indicator_label)
        result["selected_reporters"] = await select_reporters(page, reporters)
        await select_years(page)
        await page.screenshot(path=str(OUT / f"{prefix}_selection.png"), full_page=True)
        (OUT / f"{prefix}_selection.html").write_text(await page.content(), encoding="utf-8")
        await click_apply(page)
        await page.screenshot(path=str(OUT / f"{prefix}_results.png"), full_page=True)
        (OUT / f"{prefix}_results.html").write_text(await page.content(), encoding="utf-8")
        result["csv_download"] = await download_link(page, "CSV", prefix)
        result["excel_download"] = await download_link(page, "Excel", prefix)
        result["success"] = bool(result["csv_download"] or result["excel_download"])
    except Exception as exc:
        result["success"] = False
        result["error"] = repr(exc)
        try:
            await page.screenshot(path=str(OUT / f"{prefix}_failure.png"), full_page=True)
            (OUT / f"{prefix}_failure.html").write_text(await page.content(), encoding="utf-8")
        except Exception:
            pass
    finally:
        result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        (OUT / f"{prefix}_requests.json").write_text(json.dumps(requests_log, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT / f"{prefix}_responses.json").write_text(json.dumps(responses_log, ensure_ascii=False, indent=2), encoding="utf-8")
        await context.close()
    return result


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        export_result = await run_selection(browser, "china_export", EXPORT_LABEL, EXPORT_REPORTERS)
        import_result = await run_selection(browser, "partner_imports", IMPORT_LABEL, IMPORT_REPORTERS)
        await browser.close()
    status = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "portal": PORTAL,
        "year_range": [2005, 2024],
        "export": export_result,
        "imports": import_result,
        "complete": bool(export_result.get("success") and import_result.get("success")),
        "statement": "All downloaded data files in this folder originate from the public WTO Stats portal. Mirror data are not substituted.",
    }
    (OUT / "WTO_exact_export_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
