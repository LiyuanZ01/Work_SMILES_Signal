#!/usr/bin/env python3
"""Download and inspect the official CPB World Trade Monitor Excel database.

The goal is to identify the monthly world industrial-production series for a
common-global-activity robustness check. No values are imputed or guessed.
"""
from __future__ import annotations
import hashlib, json, re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
import pandas as pd
import requests
from bs4 import BeautifulSoup

OUT=Path('external_demand_wto/cpb_wtm_discovery'); OUT.mkdir(parents=True,exist_ok=True)
PAGES=[
 'https://www.cpb.nl/cpb-wereldhandelsmonitor-maart-2025',
 'https://www.cpb.nl/en/world-trade-monitor-may-2024',
 'https://www.cpb.nl/wereldhandelsmonitor-november-2024',
]

def sha(b:bytes): return hashlib.sha256(b).hexdigest()

def main():
 s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0','Accept-Language':'en-US,en;q=0.9'})
 log={'generated_at_utc':datetime.now(timezone.utc).isoformat(),'pages':[],'downloads':[]}
 workbook=None
 for pageurl in PAGES:
  rec={'url':pageurl}
  try:
   r=s.get(pageurl,timeout=90,allow_redirects=True); rec.update({'status':r.status_code,'final_url':r.url,'bytes':len(r.content),'sha256':sha(r.content),'content_type':r.headers.get('content-type')})
   if r.status_code==200:
    soup=BeautifulSoup(r.content,'html.parser'); links=[]
    for a in soup.find_all('a'):
     href=a.get('href'); text=' '.join(a.get_text(' ',strip=True).split())
     if not href: continue
     u=urljoin(r.url,href); low=(text+' '+u).lower()
     if any(x in low for x in ['world trade monitor','wereldhandelsmonitor','database']) and any(ext in low for ext in ['.xls','.xlsx','.ods','download']):
      links.append({'text':text,'url':u})
    rec['candidate_links']=links
    for link in links:
     try:
      rr=s.get(link['url'],timeout=120,allow_redirects=True); c=(rr.headers.get('content-type') or '').lower(); path=rr.url.lower()
      d={'source_page':pageurl,'link_text':link['text'],'requested_url':link['url'],'final_url':rr.url,'status':rr.status_code,'content_type':c,'bytes':len(rr.content),'sha256':sha(rr.content)}
      if rr.status_code==200 and len(rr.content)>20000 and ('excel' in c or 'spreadsheet' in c or path.endswith('.xls') or path.endswith('.xlsx')):
       ext='.xlsx' if ('spreadsheetml' in c or path.endswith('.xlsx')) else '.xls'; fn=OUT/f'CPB_World_Trade_Monitor_database{ext}'; fn.write_bytes(rr.content); d['saved']=str(fn); workbook=fn; log['downloads'].append(d); break
      log['downloads'].append(d)
     except Exception as e: log['downloads'].append({'url':link['url'],'error':repr(e)})
  except Exception as e: rec['error']=repr(e)
  log['pages'].append(rec)
  if workbook: break
 if workbook:
  xls=pd.ExcelFile(workbook); log['workbook']={'file':str(workbook),'sheet_names':xls.sheet_names}
  previews={}
  matches=[]
  for sh in xls.sheet_names:
   try:
    df=pd.read_excel(workbook,sheet_name=sh,header=None,nrows=60)
    previews[sh]=df.fillna('').astype(str).iloc[:30,:15].values.tolist()
    for i,row in df.iterrows():
     for j,val in enumerate(row):
      txt=str(val).strip()
      if re.search(r'world.*industrial.*production|industrial.*production.*world',txt,re.I): matches.append({'sheet':sh,'row':int(i),'col':int(j),'text':txt})
   except Exception as e: previews[sh]=[['ERROR',repr(e)]]
  log['workbook']['previews']=previews; log['workbook']['industrial_production_matches']=matches
 (OUT/'CPB_WTM_discovery_status.json').write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(log,ensure_ascii=False,indent=2))
 return 0 if workbook else 2
if __name__=='__main__': raise SystemExit(main())
