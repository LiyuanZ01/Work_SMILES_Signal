#!/usr/bin/env python3
"""Export direct WTO China-to-partner annual merchandise values, 2005-2007.

V2 differs only in using a forced product-checkbox click because the WTO portal
accordion can overlay the visible Total merchandise checkbox in headless runs.
"""
from __future__ import annotations
import asyncio, json, re
from datetime import datetime, timezone
from pathlib import Path
from playwright.async_api import async_playwright
from export_wto_stats_selected_series import PORTAL, IMPORT_REPORTERS, expand_node, open_accordion, click_apply, download_link

OUT=Path('external_demand_wto/wto_bilateral_baseline'); OUT.mkdir(parents=True,exist_ok=True)

async def select_indicator(page):
    await expand_node(page,'Merchandise trade statistics'); await expand_node(page,'Merchandise trade values')
    texts=await page.locator('span.ui-treenode-label').all_inner_texts()
    c=[t.strip() for t in texts if re.search(r'Merchandise exports by product group.*annual.*Million US dollar',t,re.I)]
    if not c: raise RuntimeError('Annual merchandise export-value indicator not found')
    node=page.locator('div.ui-treenode-content').filter(has_text=c[0]).first
    await node.locator('.ui-chkbox-box').click(force=True,timeout=20000); await page.wait_for_timeout(2500); return c[0]

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        ctx=await browser.new_context(accept_downloads=True,viewport={'width':1700,'height':1100},locale='en-US')
        page=await ctx.new_page(); reqs=[]; resps=[]
        def on_req(req):
            if 'stats.wto.org/api/' in req.url.lower(): reqs.append({'method':req.method,'url':req.url,'post_data':req.post_data})
        async def on_resp(resp):
            if 'stats.wto.org/api/' in resp.url.lower(): resps.append({'status':resp.status,'url':resp.url,'content_type':resp.headers.get('content-type')})
        page.on('request',on_req); page.on('response',on_resp)
        st={'generated_at_utc':datetime.now(timezone.utc).isoformat(),'portal':PORTAL}
        try:
            await page.goto(PORTAL,wait_until='domcontentloaded',timeout=180000); await page.wait_for_timeout(30000)
            st['indicator']=await select_indicator(page)
            await open_accordion(page,'Reporting Economies'); await page.locator('#country_reporter_61').check(force=True)
            await open_accordion(page,'Partner Economies'); selected=[]
            for name,pid in IMPORT_REPORTERS.items():
                loc=page.locator(f'#country_partner_{pid}');
                if await loc.count()==0: raise RuntimeError(f'Partner control missing: {name}/{pid}')
                await loc.check(force=True); selected.append({'name':name,'id':pid,'checked':await loc.is_checked()})
            st['partners']=selected
            await open_accordion(page,'Products / Sectors')
            node=page.locator('div.ui-treenode-content').filter(has_text=re.compile(r'^\s*Total merchandise\s*$',re.I)).first
            if await node.count()==0: raise RuntimeError('Total merchandise node missing')
            await node.locator('.ui-chkbox-box').click(force=True,timeout=20000); await page.wait_for_timeout(1000)
            await open_accordion(page,'Years'); await page.locator('#ddlYearStart').select_option(label='2005'); await page.wait_for_timeout(500); await page.locator('#ddlYearEnd').select_option(label='2007'); await page.wait_for_timeout(1500)
            await page.screenshot(path=str(OUT/'bilateral_baseline_selection.png'),full_page=True)
            (OUT/'bilateral_baseline_selection.html').write_text(await page.content(),encoding='utf-8')
            await click_apply(page); await page.wait_for_timeout(10000)
            await page.screenshot(path=str(OUT/'bilateral_baseline_results.png'),full_page=True)
            (OUT/'bilateral_baseline_results.html').write_text(await page.content(),encoding='utf-8')
            csv_dl=await download_link(page,'CSV','bilateral_baseline'); xls_dl=await download_link(page,'Excel','bilateral_baseline')
            st.update({'csv_download':csv_dl,'excel_download':xls_dl,'complete':bool(csv_dl or xls_dl)})
        except Exception as exc:
            st.update({'complete':False,'error':repr(exc)})
            try: await page.screenshot(path=str(OUT/'bilateral_baseline_failure.png'),full_page=True)
            except Exception: pass
        (OUT/'bilateral_baseline_requests.json').write_text(json.dumps(reqs,ensure_ascii=False,indent=2),encoding='utf-8'); (OUT/'bilateral_baseline_responses.json').write_text(json.dumps(resps,ensure_ascii=False,indent=2),encoding='utf-8'); (OUT/'bilateral_baseline_status.json').write_text(json.dumps(st,ensure_ascii=False,indent=2),encoding='utf-8')
        await ctx.close(); await browser.close(); print(json.dumps(st,ensure_ascii=False,indent=2)); return 0 if st.get('complete') else 2
if __name__=='__main__': raise SystemExit(asyncio.run(main()))
