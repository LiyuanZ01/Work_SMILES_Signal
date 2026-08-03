#!/usr/bin/env python3
from __future__ import annotations
import asyncio, json, re, hashlib, os, time
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(os.environ.get('WTO_PORTAL_OUT','wto_official_portal/results'))
OUT.mkdir(parents=True, exist_ok=True)
PORTAL='https://stats.wto.org/en'
META='https://www.wto.org/english/res_e/statis_e/merch_trade_stat_e.htm'

def safe(s:str)->str:
    s=re.sub(r'[^A-Za-z0-9._-]+','_',s).strip('_')
    return s[:120] or 'file'

def sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()

async def visible_texts(page):
    data={}
    for kind,sel in {'buttons':'button','links':'a','inputs':'input','selects':'select','checkboxes':'input[type=checkbox]'}.items():
        vals=[]
        try:
            loc=page.locator(sel); n=min(await loc.count(),500)
            for i in range(n):
                el=loc.nth(i)
                try:
                    vals.append({'text':(await el.inner_text(timeout=1000)).strip(),'aria':await el.get_attribute('aria-label'),'title':await el.get_attribute('title'),'placeholder':await el.get_attribute('placeholder'),'type':await el.get_attribute('type'),'value':await el.get_attribute('value'),'checked':await el.is_checked() if kind=='checkboxes' else None,'visible':await el.is_visible()})
                except Exception as e: vals.append({'error':repr(e)})
        except Exception as e: vals=[{'error':repr(e)}]
        data[kind]=vals
    return data

async def screenshot(page,name):
    p=OUT/f'{name}.png'
    try: await page.screenshot(path=str(p), full_page=True)
    except Exception: await page.screenshot(path=str(p))
    return p

async def click_text(page, patterns, name, exact=False):
    attempts=[]
    for pat in patterns:
        for method in ['get_by_text','get_by_role_button','locator_text']:
            try:
                if method=='get_by_text': loc=page.get_by_text(pat, exact=exact)
                elif method=='get_by_role_button': loc=page.get_by_role('button', name=re.compile(pat,re.I))
                else: loc=page.locator(f'text=/{pat}/i')
                n=await loc.count(); attempts.append({'pattern':pat,'method':method,'count':n})
                for i in range(min(n,20)):
                    el=loc.nth(i)
                    if await el.is_visible():
                        await el.scroll_into_view_if_needed(); await el.click(timeout=8000); await page.wait_for_timeout(2500); await screenshot(page,name); return True,attempts
            except Exception as e: attempts.append({'pattern':pat,'method':method,'error':repr(e)})
    return False,attempts

async def main():
    log={'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'portal':PORTAL,'actions':[],'network':[],'downloads':[]}
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--disable-dev-shm-usage','--no-sandbox'])
        context=await browser.new_context(accept_downloads=True, viewport={'width':1680,'height':1050}, locale='en-US')
        page=await context.new_page()
        async def on_request(req):
            u=req.url
            if any(x in u.lower() for x in ['api.wto','stats.wto','download','export','csv','excel','timeseries']): log['network'].append({'event':'request','method':req.method,'url':u,'post_data':req.post_data})
        async def on_response(resp):
            u=resp.url; h=await resp.all_headers()
            if any(x in u.lower() for x in ['api.wto','stats.wto','download','export','csv','excel','timeseries']) or 'content-disposition' in h:
                item={'event':'response','status':resp.status,'url':u,'content_type':h.get('content-type'),'content_disposition':h.get('content-disposition')}; log['network'].append(item)
                cd=h.get('content-disposition',''); ct=h.get('content-type','')
                if cd or any(x in ct.lower() for x in ['csv','excel','spreadsheet']):
                    try:
                        body=await resp.body(); ext='.bin'
                        if 'csv' in ct.lower(): ext='.csv'
                        elif 'spreadsheetml' in ct.lower(): ext='.xlsx'
                        elif 'ms-excel' in ct.lower(): ext='.xls'
                        m=re.search(r"filename\*?=(?:UTF-8'')?[\"']?([^\"';]+)",cd,re.I)
                        fn=safe(m.group(1) if m else f'network_export_{len(log["downloads"])+1}{ext}'); path=OUT/fn; path.write_bytes(body)
                        log['downloads'].append({'source':'network_response','url':u,'file':str(path),'bytes':len(body)})
                    except Exception as e: item['save_error']=repr(e)
        page.on('request', on_request); page.on('response', on_response)
        async def handle_download(download):
            try:
                fn=safe(download.suggested_filename); path=OUT/fn; await download.save_as(str(path)); log['downloads'].append({'source':'download_event','url':download.url,'suggested_filename':download.suggested_filename,'file':str(path),'bytes':path.stat().st_size})
            except Exception as e: log['downloads'].append({'source':'download_event','error':repr(e)})
        page.on('download',handle_download)
        meta=await context.new_page()
        try:
            await meta.goto(META, wait_until='domcontentloaded', timeout=120000); await meta.wait_for_timeout(3000); await screenshot(meta,'00_WTO_official_metadata_page'); (OUT/'00_WTO_official_metadata_page.html').write_text(await meta.content(),encoding='utf-8')
        except Exception as e: log['actions'].append({'metadata_page_error':repr(e)})
        await meta.close()
        try:
            resp=await page.goto(PORTAL, wait_until='domcontentloaded', timeout=180000); log['actions'].append({'goto':PORTAL,'status':resp.status if resp else None,'final_url':page.url}); await page.wait_for_timeout(15000)
            try: await page.wait_for_load_state('networkidle',timeout=30000)
            except Exception: pass
            await screenshot(page,'01_portal_landing'); (OUT/'01_portal_landing.html').write_text(await page.content(),encoding='utf-8'); (OUT/'01_portal_landing_text.txt').write_text(await page.locator('body').inner_text(),encoding='utf-8'); (OUT/'01_portal_elements.json').write_text(json.dumps(await visible_texts(page),ensure_ascii=False,indent=2),encoding='utf-8')
            try:
                storage=await page.evaluate("() => ({local:{...localStorage},session:{...sessionStorage},href:location.href})"); (OUT/'01_storage.json').write_text(json.dumps(storage,ensure_ascii=False,indent=2),encoding='utf-8')
            except Exception as e: log['actions'].append({'storage_error':repr(e)})
            for pats,nm in [([r'accept all',r'accept',r'agree',r'close'],'02_after_banner'),([r'International Trade Statistics'],'03_international_trade_statistics'),([r'Merchandise trade.*indices.*prices',r'indices and prices'],'04_indices_prices'),([r'Merchandise export or import volume indices.*seasonally adjusted.*quarterly',r'seasonally adjusted.*quarterly'],'05_quarterly_sa_volume'),([r'Apply'],'06_apply'),([r'CSV'],'07_csv_click'),([r'Excel'],'08_excel_click')]:
                ok,att=await click_text(page,pats,nm); log['actions'].append({'step':nm,'success':ok,'attempts':att,'url':page.url})
                if nm=='06_apply' and ok:
                    await page.wait_for_timeout(15000)
                    try: await page.wait_for_load_state('networkidle',timeout=30000)
                    except Exception: pass
                    await screenshot(page,'06b_after_apply_wait'); (OUT/'06b_after_apply.html').write_text(await page.content(),encoding='utf-8'); (OUT/'06b_after_apply_text.txt').write_text(await page.locator('body').inner_text(),encoding='utf-8'); (OUT/'06b_after_apply_elements.json').write_text(json.dumps(await visible_texts(page),ensure_ascii=False,indent=2),encoding='utf-8')
                await page.wait_for_timeout(3000)
            await screenshot(page,'09_final_state'); (OUT/'09_final_state.html').write_text(await page.content(),encoding='utf-8'); (OUT/'09_final_state_text.txt').write_text(await page.locator('body').inner_text(),encoding='utf-8'); (OUT/'09_final_elements.json').write_text(json.dumps(await visible_texts(page),ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception as e:
            log['fatal_error']=repr(e)
            try: await screenshot(page,'99_fatal_state')
            except Exception: pass
        await context.close(); await browser.close()
    inv=[]
    for path in sorted(OUT.iterdir()):
        if path.is_file(): inv.append({'file':path.name,'bytes':path.stat().st_size,'sha256':sha256(path)})
    log['finished_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()); log['inventory']=inv; (OUT/'portal_capture_status.json').write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(log,ensure_ascii=False,indent=2))

if __name__=='__main__': asyncio.run(main())
