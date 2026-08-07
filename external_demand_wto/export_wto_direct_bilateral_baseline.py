#!/usr/bin/env python3
"""Export direct WTO annual China-to-partner merchandise values for 2005-2007.

This provides a P1 sensitivity weight vector based on direct bilateral trade values
rather than reconstructed imports-from-China shares times partner GDP.
"""
from __future__ import annotations
import asyncio, json, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from playwright.async_api import async_playwright
from export_wto_stats_selected_series import (
    PORTAL, IMPORT_REPORTERS, expand_node, open_accordion, click_apply, download_link
)

OUT=Path('external_demand_wto/wto_bilateral_baseline')
OUT.mkdir(parents=True,exist_ok=True)

async def select_export_value_indicator(page):
    await expand_node(page,'Merchandise trade statistics')
    await expand_node(page,'Merchandise trade values')
    labels=page.locator('span.ui-treenode-label')
    texts=await labels.all_inner_texts()
    candidates=[t.strip() for t in texts if re.search(r'Merchandise exports by product group.*annual.*Million US dollar',t,re.I)]
    if not candidates:
        raise RuntimeError('Annual merchandise export-value indicator not found; labels='+repr(texts[:250]))
    target=candidates[0]
    nodes=page.locator('div.ui-treenode-content').filter(has_text=target)
    if await nodes.count()==0:
        raise RuntimeError('Indicator node not found: '+target)
    box=nodes.first.locator('.ui-chkbox-box')
    await box.click(timeout=20000); await page.wait_for_timeout(3000)
    return target

async def select_reporter_china(page):
    await open_accordion(page,'Reporting Economies')
    loc=page.locator('#country_reporter_61')
    if await loc.count()==0: raise RuntimeError('China reporter control missing')
    await loc.check(force=True); await page.wait_for_timeout(500)

async def select_partners(page):
    await open_accordion(page,'Partner Economies')
    selected=[]
    for name,pid in IMPORT_REPORTERS.items():
        loc=page.locator(f'#country_partner_{pid}')
        if await loc.count()==0:
            raise RuntimeError(f'Partner control missing: {name}/{pid}')
        await loc.check(force=True); selected.append({'name':name,'id':pid,'checked':await loc.is_checked()}); await page.wait_for_timeout(100)
    return selected

async def select_total_merchandise(page):
    await open_accordion(page,'Products / Sectors')
    nodes=page.locator('div.ui-treenode-content').filter(has_text=re.compile(r'^\s*Total merchandise\s*$',re.I))
    if await nodes.count()==0:
        labels=await page.locator('span.ui-treenode-label').all_inner_texts()
        raise RuntimeError('Total merchandise product node not found; labels='+repr(labels[-100:]))
    node=nodes.first; box=node.locator('.ui-chkbox-box')
    if await box.count()==0: raise RuntimeError('Total merchandise checkbox missing')
    await box.click(timeout=20000); await page.wait_for_timeout(1000)

async def select_years(page):
    await open_accordion(page,'Years')
    start=page.locator('#ddlYearStart'); end=page.locator('#ddlYearEnd')
    await start.select_option(label='2005'); await page.wait_for_timeout(500)
    await end.select_option(label='2007'); await page.wait_for_timeout(1500)

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        context=await browser.new_context(accept_downloads=True,viewport={'width':1700,'height':1100},locale='en-US')
        page=await context.new_page(); requests=[]; responses=[]
        def on_req(req):
            if 'stats.wto.org/api/' in req.url.lower(): requests.append({'method':req.method,'url':req.url,'post_data':req.post_data})
        async def on_resp(resp):
            if 'stats.wto.org/api/' in resp.url.lower(): responses.append({'status':resp.status,'url':resp.url,'content_type':resp.headers.get('content-type')})
        page.on('request',on_req); page.on('response',on_resp)
        status={'generated_at_utc':datetime.now(timezone.utc).isoformat(),'portal':PORTAL}
        try:
            await page.goto(PORTAL,wait_until='domcontentloaded',timeout=180000); await page.wait_for_timeout(30000)
            status['indicator']=await select_export_value_indicator(page)
            await select_reporter_china(page)
            status['partners']=await select_partners(page)
            await select_total_merchandise(page)
            await select_years(page)
            await page.screenshot(path=str(OUT/'bilateral_baseline_selection.png'),full_page=True)
            (OUT/'bilateral_baseline_selection.html').write_text(await page.content(),encoding='utf-8')
            await click_apply(page); await page.wait_for_timeout(10000)
            await page.screenshot(path=str(OUT/'bilateral_baseline_results.png'),full_page=True)
            (OUT/'bilateral_baseline_results.html').write_text(await page.content(),encoding='utf-8')
            csv_dl=await download_link(page,'CSV','bilateral_baseline')
            xls_dl=await download_link(page,'Excel','bilateral_baseline')
            status.update({'csv_download':csv_dl,'excel_download':xls_dl,'complete':bool(csv_dl or xls_dl)})
        except Exception as exc:
            status.update({'complete':False,'error':repr(exc)})
            try: await page.screenshot(path=str(OUT/'bilateral_baseline_failure.png'),full_page=True)
            except Exception: pass
        (OUT/'bilateral_baseline_requests.json').write_text(json.dumps(requests,ensure_ascii=False,indent=2),encoding='utf-8')
        (OUT/'bilateral_baseline_responses.json').write_text(json.dumps(responses,ensure_ascii=False,indent=2),encoding='utf-8')
        (OUT/'bilateral_baseline_status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
        await context.close(); await browser.close()
        print(json.dumps(status,ensure_ascii=False,indent=2))
        return 0 if status.get('complete') else 2

if __name__=='__main__': raise SystemExit(asyncio.run(main()))
