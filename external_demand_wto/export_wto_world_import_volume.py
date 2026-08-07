#!/usr/bin/env python3
"""Export the WTO Stats world merchandise import-volume index, SA quarterly."""
from __future__ import annotations
import asyncio, json
from datetime import datetime, timezone
from pathlib import Path
from playwright.async_api import async_playwright
from export_wto_stats_selected_series import IMPORT_LABEL, run_selection

OUT = Path("external_demand_wto/wto_exact_export")

async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        result = await run_selection(browser, "world_import", IMPORT_LABEL, {"World": 1})
        await browser.close()
    status = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection": result,
        "complete": bool(result.get("success")),
        "purpose": "Stringent common-global-trade-cycle control for P1 robustness; not a causal shifter.",
    }
    (OUT / "WTO_world_import_export_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status["complete"] else 2

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
