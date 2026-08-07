#!/usr/bin/env python3
"""Clean UN Comtrade direct bilateral weight audit for 2005-2007.

Uses the UN Comtrade partner codes valid for the 2005-2007 reporting period.
The reference file contains historical duplicate ISO3 entries, so codes are
frozen explicitly after checking their effective periods.
"""
from __future__ import annotations
import argparse, hashlib, json, math, time
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import statsmodels.api as sm
from scipy import stats

API='https://comtradeapi.un.org/public/v1/preview/C/A/HS'
PARTNER_REF='https://comtradeapi.un.org/files/v1/app/reference/partnerAreas.json'
PARTNER_CODES={
 'USA':842,'JPN':392,'DEU':276,'KOR':410,'NLD':528,'GBR':826,'CAN':124,
 'SGP':702,'MEX':484,'ITA':380,'AUS':36,'FRA':251,'IND':699,'ESP':724,
 'MYS':458,'RUS':643,'THA':764,'BEL':56,'TUR':792,'ARE':784,
}

def sha(b:bytes): return hashlib.sha256(b).hexdigest()

def fetch_one(session,year,iso,code,out):
    params={'period':str(year),'reporterCode':'156','partnerCode':str(code),'flowCode':'X','cmdCode':'TOTAL','partner2Code':'0','maxRecords':'500'}
    last=None
    for attempt in range(6):
        r=session.get(API,params=params,timeout=90)
        if r.status_code==429:
            time.sleep(1.5*(attempt+1)); last='HTTP429'; continue
        if r.status_code>=500:
            time.sleep(attempt+1); last=f'HTTP{r.status_code}'; continue
        r.raise_for_status(); raw=r.content; (out/f'raw_{year}_{iso}.json').write_bytes(raw); obj=r.json(); rows=obj.get('data',[]) if isinstance(obj,dict) else []
        valid=[]
        for row in rows:
            if str(row.get('period'))!=str(year): continue
            cmd=str(row.get('cmdCode','')).upper(); desc=str(row.get('cmdDesc','')).lower()
            if cmd not in {'TOTAL','00'} and 'total' not in desc: continue
            valid.append(row)
        if not valid:
            return {'year':year,'alpha3':iso,'partner_code':code,'status':'missing','api_url':r.url,'http_status':r.status_code,'response_sha256':sha(raw)}
        row=valid[0]; val=row.get('primaryValue',row.get('TradeValue',row.get('tradeValue')))
        try: val=float(val)
        except Exception: raise RuntimeError(f'No numeric trade value for {year} {iso}: {row}')
        return {'year':year,'alpha3':iso,'partner_code':code,'partner_desc':row.get('partnerDesc'),'trade_value_usd':val,'status':'ok','api_url':r.url,'http_status':r.status_code,'response_sha256':sha(raw)}
    raise RuntimeError(f'UN Comtrade request failed {year} {iso}: {last}')

def fit(data,regs):
    f=data.dropna(subset=['export_qoq',*regs]).copy(); X=sm.add_constant(f[regs],has_constant='add'); r=sm.OLS(f.export_qoq,X).fit(cov_type='HAC',cov_kwds={'maxlags':4,'use_correction':True},use_t=True); return f,r

def combo(r,a,b):
    c=r.cov_params(); eff=float(r.params[a]+r.params[b]); var=float(c.loc[a,a]+c.loc[b,b]+2*c.loc[a,b]); se=math.sqrt(max(var,0)); p=float(2*stats.t.sf(abs(eff/se),r.df_resid)); return eff,se,p

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--partner-imports',required=True); ap.add_argument('--model-data',required=True); ap.add_argument('--old-weights',required=True); ap.add_argument('--output-dir',required=True); a=ap.parse_args(); out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
    s=requests.Session(); s.headers.update({'User-Agent':'China-external-demand-academic-replication/1.0'})
    ref=s.get(PARTNER_REF,timeout=90); ref.raise_for_status(); (out/'UNComtrade_partnerAreas.json').write_bytes(ref.content)
    records=[]
    for year in [2005,2006,2007]:
        for iso,code in PARTNER_CODES.items():
            records.append(fetch_one(s,year,iso,code,out)); time.sleep(1.05)
    raw=pd.DataFrame(records); raw.to_csv(out/'UNComtrade_China_exports_selected_2005_2007.csv',index=False)
    ok=raw[raw.status.eq('ok')].copy(); avail=ok.groupby('alpha3').year.nunique(); missing={iso:sorted(set([2005,2006,2007])-set(ok.loc[ok.alpha3.eq(iso),'year'].astype(int))) for iso in PARTNER_CODES}; missing={k:v for k,v in missing.items() if v}
    if missing:
        raise RuntimeError('Missing official bilateral years; no imputation allowed: '+json.dumps(missing))
    weights=ok.groupby('alpha3',as_index=False).agg(mean_export_value_usd=('trade_value_usd','mean'),years_available=('year','nunique')); weights['direct_weight_selected']=weights.mean_export_value_usd/weights.mean_export_value_usd.sum(); weights.to_csv(out/'UNComtrade_direct_weights_2005_2007.csv',index=False)
    imports=pd.read_csv(a.partner_imports); model=pd.read_csv(a.model_data); wide=imports.pivot(index='quarter',columns='alpha3',values='value').reindex(model.quarter); w=weights.set_index('alpha3').direct_weight_selected.reindex(wide.columns).fillna(0.0)
    lev=[]; cov=[]; cnt=[]
    for q,row in wide.iterrows():
        valid=row.notna()&w.gt(0); c=float(w[valid].sum()); cov.append(c); cnt.append(int(valid.sum())); lev.append(float((row[valid]*w[valid]).sum()/c) if c>0 else np.nan)
    dd=pd.DataFrame({'quarter':model.quarter,'uncomtrade_demand_index':lev,'uncomtrade_coverage':cov,'uncomtrade_market_count':cnt}); dd['uncomtrade_demand_qoq']=dd.uncomtrade_demand_index.pct_change(fill_method=None)*100; dd['uncomtrade_demand_lag1']=dd.uncomtrade_demand_qoq.shift(1); data=model.merge(dd,on='quarter',how='left')
    f,r=fit(data,['uncomtrade_demand_qoq','uncomtrade_demand_lag1','reer_lag1','export_qoq_lag1']); eff,se,p=combo(r,'uncomtrade_demand_qoq','uncomtrade_demand_lag1')
    result={'current':float(r.params.uncomtrade_demand_qoq),'current_se':float(r.bse.uncomtrade_demand_qoq),'current_p':float(r.pvalues.uncomtrade_demand_qoq),'lag1':float(r.params.uncomtrade_demand_lag1),'lag1_p':float(r.pvalues.uncomtrade_demand_lag1),'current_plus_lag':eff,'sum_se':se,'sum_p':p,'n':int(r.nobs),'adjusted_r_squared':float(r.rsquared_adj),'mean_quarterly_weight_coverage':float(np.mean(cov))}
    old=pd.read_csv(a.old_weights); oldcol='weight_ex_hk_mac'; comp=old[['alpha3',oldcol]].drop_duplicates().merge(weights,on='alpha3',how='inner'); comp['old_weight_common']=comp[oldcol]/comp[oldcol].sum(); comp['uncomtrade_weight_common']=comp.direct_weight_selected/comp.direct_weight_selected.sum(); corr=float(comp[['old_weight_common','uncomtrade_weight_common']].corr().iloc[0,1]); result['weight_correlation_old_vs_uncomtrade']=corr; comp.to_csv(out/'UNComtrade_vs_reconstructed_weight_comparison.csv',index=False); data.to_csv(out/'P1_UNComtrade_model_data.csv',index=False); pd.DataFrame([result]).to_csv(out/'P1_UNComtrade_model_summary.csv',index=False)
    status={'source':'UN Comtrade public preview API','api':API,'partner_reference_sha256':sha(ref.content),'partner_codes_2005_2007':PARTNER_CODES,'complete_three_year_partners':int((avail==3).sum()),'missing_years':missing,'result':result,'interpretation':'Direct UN Comtrade China-to-destination annual export values replace the reconstructed 2005-2007 exposure vector as a weight-source sensitivity. Quarterly volume series remain direct WTO Stats data. No missing bilateral value is imputed.'}; (out/'P1_UNComtrade_status.json').write_text(json.dumps(status,indent=2),encoding='utf-8'); print(json.dumps(status,indent=2))
if __name__=='__main__': main()
