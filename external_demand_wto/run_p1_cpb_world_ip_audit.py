#!/usr/bin/env python3
"""P1 robustness using CPB World Trade Monitor world industrial production.

Downloads an official CPB WTM workbook that covers 2024Q4, extracts the monthly
seasonally adjusted World industrial-production index, converts monthly levels to
quarterly mean levels, computes q/q growth, and re-estimates the fixed-weight model.
"""
from __future__ import annotations
import argparse, hashlib, json, math, re
from pathlib import Path
from urllib.parse import urljoin
import numpy as np
import pandas as pd
import requests
import statsmodels.api as sm
from bs4 import BeautifulSoup
from scipy import stats

CPB_PAGES=[
 'https://www.cpb.nl/wereldhandelsmonitor-december-2024',
 'https://www.cpb.nl/wereldhandelsmonitor-januari-2025',
 'https://www.cpb.nl/cpb-wereldhandelsmonitor-februari-2025',
]

def sha(b): return hashlib.sha256(b).hexdigest()

def download_database(out:Path):
 s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0','Accept-Language':'en-US,en;q=0.9'})
 attempts=[]
 for page in CPB_PAGES:
  try:
   r=s.get(page,timeout=90,allow_redirects=True); rec={'page':page,'status':r.status_code,'final_url':r.url,'bytes':len(r.content),'sha256':sha(r.content)}; attempts.append(rec)
   if r.status_code!=200: continue
   soup=BeautifulSoup(r.content,'html.parser'); links=[]
   for a in soup.find_all('a'):
    href=a.get('href'); text=' '.join(a.get_text(' ',strip=True).split())
    if not href: continue
    u=urljoin(r.url,href); low=(text+' '+u).lower()
    if ('database cpb world trade monitor' in low or 'database cpb wereldhandelsmonitor' in low) and ('.xlsx' in low or '.xls' in low): links.append((text,u))
   rec['database_links']=[{'text':t,'url':u} for t,u in links]
   for text,u in links:
    rr=s.get(u,timeout=120,allow_redirects=True); c=(rr.headers.get('content-type') or '').lower(); rec2={'url':u,'final_url':rr.url,'status':rr.status_code,'content_type':c,'bytes':len(rr.content),'sha256':sha(rr.content)}; attempts.append(rec2)
    if rr.status_code==200 and len(rr.content)>20000:
     ext='.xlsx' if ('spreadsheetml' in c or rr.url.lower().endswith('.xlsx')) else '.xls'; fp=out/f'CPB_WTM_2024Q4{ext}'; fp.write_bytes(rr.content); return fp,attempts,page
  except Exception as e: attempts.append({'page':page,'error':repr(e)})
 raise RuntimeError('Could not download a CPB database covering 2024Q4; attempts='+json.dumps(attempts))

def extract_world_ip(book:Path):
 xls=pd.ExcelFile(book)
 if 'inpro_out' not in xls.sheet_names: raise RuntimeError(f'inpro_out missing; sheets={xls.sheet_names}')
 df=pd.read_excel(book,sheet_name='inpro_out',header=None)
 dates=df.iloc[3,5:]
 world=df.iloc[7,5:]
 rows=[]
 for d,v in zip(dates,world):
  d=str(d).strip()
  if not re.fullmatch(r'\d{4}m\d{2}',d): continue
  try: val=float(v)
  except Exception: continue
  rows.append({'month':d,'world_ip_index':val})
 m=pd.DataFrame(rows); m['year']=m.month.str[:4].astype(int); m['month_num']=m.month.str[-2:].astype(int); m=m[m.year.between(2004,2025)].copy(); m['quarter_num']=((m.month_num-1)//3)+1; m['quarter']=m.year.astype(str)+'Q'+m.quarter_num.astype(str)
 q=m.groupby('quarter',as_index=False).agg(world_ip_index=('world_ip_index','mean'),months=('world_ip_index','size'))
 q=q[q.months.eq(3)].copy(); q['world_ip_qoq']=q.world_ip_index.pct_change(fill_method=None)*100; q['world_ip_lag1']=q.world_ip_qoq.shift(1); return m,q

def fit(data,regs):
 f=data.dropna(subset=['export_qoq',*regs]).copy(); X=sm.add_constant(f[regs],has_constant='add'); r=sm.OLS(f.export_qoq,X).fit(cov_type='HAC',cov_kwds={'maxlags':4,'use_correction':True},use_t=True); return f,r

def combo(r,a,b):
 c=r.cov_params(); eff=float(r.params[a]+r.params[b]); var=float(c.loc[a,a]+c.loc[b,b]+2*c.loc[a,b]); se=math.sqrt(max(var,0)); p=float(2*stats.t.sf(abs(eff/se),r.df_resid)); return eff,se,p

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--model-data',required=True); ap.add_argument('--output-dir',required=True); a=ap.parse_args(); out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
 book,attempts,page=download_database(out); monthly,q=extract_world_ip(book); data=pd.read_csv(a.model_data).merge(q[['quarter','world_ip_index','world_ip_qoq','world_ip_lag1']],on='quarter',how='left')
 specs=[('Baseline on CPB common sample',['fixed_demand_qoq','fixed_demand_lag1','reer_lag1','export_qoq_lag1']),('CPB world IP current',['fixed_demand_qoq','fixed_demand_lag1','world_ip_qoq','reer_lag1','export_qoq_lag1']),('CPB world IP current+lag',['fixed_demand_qoq','fixed_demand_lag1','world_ip_qoq','world_ip_lag1','reer_lag1','export_qoq_lag1'])]
 # Force identical common sample for all comparisons.
 common=data.dropna(subset=['export_qoq','fixed_demand_qoq','fixed_demand_lag1','reer_lag1','export_qoq_lag1','world_ip_qoq','world_ip_lag1']).copy()
 summary=[]; estimates=[]
 for name,regs in specs:
  f,r=fit(common,regs); eff,se,p=combo(r,'fixed_demand_qoq','fixed_demand_lag1'); summary.append({'model':name,'current':float(r.params.fixed_demand_qoq),'current_se':float(r.bse.fixed_demand_qoq),'current_p':float(r.pvalues.fixed_demand_qoq),'lag1':float(r.params.fixed_demand_lag1),'lag1_p':float(r.pvalues.fixed_demand_lag1),'current_plus_lag':eff,'sum_se':se,'sum_p':p,'n':int(r.nobs),'adj_r2':float(r.rsquared_adj)})
  for par in r.params.index: estimates.append({'model':name,'parameter':par,'coefficient':float(r.params[par]),'se':float(r.bse[par]),'p':float(r.pvalues[par])})
 corr=float(common[['fixed_demand_qoq','world_ip_qoq']].corr().iloc[0,1]); monthly.to_csv(out/'CPB_world_IP_monthly.csv',index=False); q.to_csv(out/'CPB_world_IP_quarterly.csv',index=False); data.to_csv(out/'P1_CPB_model_data.csv',index=False); pd.DataFrame(summary).to_csv(out/'P1_CPB_model_summary.csv',index=False); pd.DataFrame(estimates).to_csv(out/'P1_CPB_model_estimates.csv',index=False); status={'source_page':page,'source_workbook':book.name,'source_sha256':sha(book.read_bytes()),'monthly_start':monthly.month.min(),'monthly_end':monthly.month.max(),'complete_quarters_start':q.quarter.min(),'complete_quarters_end':q.quarter.max(),'common_sample_start':common.quarter.min(),'common_sample_end':common.quarter.max(),'corr_partner_demand_cpb_world_ip':corr,'models':summary,'download_attempts':attempts,'interpretation':'CPB world industrial production is a broader common-activity control and is less mechanically nested with the partner import-volume index than world merchandise imports.'}; (out/'P1_CPB_status.json').write_text(json.dumps(status,indent=2),encoding='utf-8'); print(json.dumps(status,indent=2))
if __name__=='__main__': main()
