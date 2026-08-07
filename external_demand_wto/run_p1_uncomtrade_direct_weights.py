#!/usr/bin/env python3
"""P1 direct bilateral weight sensitivity using official UN Comtrade data.

For each selected destination and each year 2005-2007, this script queries the
non-authenticated UN Comtrade Preview API for China's annual TOTAL merchandise
exports (reporter 156, flow X). The resulting direct bilateral export values are
used only to construct early-sample destination weights. No missing value is
replaced by zero.
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
SELECTED=['USA','JPN','DEU','KOR','NLD','GBR','CAN','SGP','MEX','ITA','AUS','FRA','IND','ESP','MYS','RUS','THA','BEL','TUR','ARE']

def sha(b:bytes): return hashlib.sha256(b).hexdigest()

def load_partner_reference(session:requests.Session, out:Path):
    r=session.get(PARTNER_REF,timeout=90); r.raise_for_status(); raw=r.content; (out/'UNComtrade_partnerAreas.json').write_bytes(raw)
    obj=r.json(); items=obj.get('results',obj) if isinstance(obj,dict) else obj
    # discover code / ISO fields robustly
    mapping={}
    for item in items:
        if not isinstance(item,dict): continue
        vals={str(k).lower():v for k,v in item.items()}
        iso=None
        for k in ['partneriso','iso3','iso3digit','isoalpha3','entrycode','codeisoalpha3','iso']:
            if k in vals and vals[k]:
                s=str(vals[k]).strip().upper()
                if len(s)==3 and s.isalpha(): iso=s; break
        if not iso:
            # inspect any 3-letter alphabetic field
            for k,v in item.items():
                s=str(v).strip().upper()
                if len(s)==3 and s.isalpha() and 'text' not in k.lower() and 'name' not in k.lower():
                    iso=s; break
        code=None
        for k in ['id','partnercode','m49','code','entryid']:
            if k in vals:
                try: code=int(vals[k]); break
                except Exception: pass
        name=None
        for k in ['text','partnerdesc','name','entryname']:
            if k in vals and vals[k]: name=str(vals[k]); break
        if iso and code is not None: mapping[iso]={'code':code,'name':name,'raw':item}
    missing=[x for x in SELECTED if x not in mapping]
    if missing:
        raise RuntimeError(f'Could not map selected ISO3 codes from UN Comtrade reference: {missing}; sample={items[:5]}')
    return mapping,sha(raw)

def fetch_one(session, year:int, partner_code:int, iso:str, out:Path):
    params={'period':str(year),'reporterCode':'156','partnerCode':str(partner_code),'flowCode':'X','cmdCode':'TOTAL','partner2Code':'0','maxRecords':'500'}
    last=None
    for attempt in range(6):
        r=session.get(API,params=params,timeout=90)
        if r.status_code==429:
            time.sleep(1.5*(attempt+1)); last=f'HTTP429 attempt {attempt+1}'; continue
        if r.status_code>=500:
            time.sleep(1.0*(attempt+1)); last=f'HTTP{r.status_code}'; continue
        r.raise_for_status(); raw=r.content; fp=out/f'raw_{year}_{iso}.json'; fp.write_bytes(raw)
        obj=r.json(); data=obj.get('data',[]) if isinstance(obj,dict) else []
        # exact target rows only
        valid=[]
        for row in data:
            if str(row.get('period'))!=str(year): continue
            if str(row.get('flowCode','')).upper() not in {'X','X '} and str(row.get('flowDesc','')).lower()!='export': continue
            cmd=str(row.get('cmdCode','')).upper()
            desc=str(row.get('cmdDesc','')).lower()
            if cmd not in {'TOTAL','00'} and 'total' not in desc: continue
            valid.append(row)
        if len(valid)==0:
            return {'year':year,'alpha3':iso,'partner_code':partner_code,'status':'missing','api_url':r.url,'http_status':r.status_code,'response_sha256':sha(raw)}
        # Prefer partner-specific row and primaryValue; preview should be one record.
        row=valid[0]
        value=row.get('primaryValue')
        if value is None: value=row.get('TradeValue')
        if value is None: value=row.get('tradeValue')
        try: value=float(value)
        except Exception: raise RuntimeError(f'No numeric trade value for {year} {iso}: {row}')
        return {'year':year,'alpha3':iso,'partner_code':partner_code,'partner_desc':row.get('partnerDesc'),'flow_desc':row.get('flowDesc'),'cmd_code':row.get('cmdCode'),'cmd_desc':row.get('cmdDesc'),'trade_value_usd':value,'status':'ok','api_url':r.url,'http_status':r.status_code,'response_sha256':sha(raw)}
    raise RuntimeError(f'UN Comtrade request failed {year} {iso}: {last}')

def fit(data,regs):
    f=data.dropna(subset=['export_qoq',*regs]).copy(); X=sm.add_constant(f[regs],has_constant='add'); r=sm.OLS(f.export_qoq,X).fit(cov_type='HAC',cov_kwds={'maxlags':4,'use_correction':True},use_t=True); return f,r

def combo(r,a,b):
    c=r.cov_params(); eff=float(r.params[a]+r.params[b]); var=float(c.loc[a,a]+c.loc[b,b]+2*c.loc[a,b]); se=math.sqrt(max(var,0)); p=float(2*stats.t.sf(abs(eff/se),r.df_resid)); return eff,se,p

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--partner-imports',required=True); ap.add_argument('--model-data',required=True); ap.add_argument('--old-weights',required=True); ap.add_argument('--output-dir',required=True); a=ap.parse_args(); out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
    s=requests.Session(); s.headers.update({'User-Agent':'China-external-demand-academic-replication/1.0'})
    mapping,refsha=load_partner_reference(s,out)
    rows=[]
    for year in [2005,2006,2007]:
        for iso in SELECTED:
            rec=fetch_one(s,year,mapping[iso]['code'],iso,out); rows.append(rec); time.sleep(1.05)
    raw=pd.DataFrame(rows); raw.to_csv(out/'UNComtrade_China_exports_selected_2005_2007.csv',index=False)
    ok=raw[raw.status.eq('ok')].copy(); availability=ok.groupby('alpha3').year.nunique(); missing_years={iso:sorted(set([2005,2006,2007])-set(ok.loc[ok.alpha3.eq(iso),'year'].astype(int))) for iso in SELECTED}; missing_years={k:v for k,v in missing_years.items() if v}
    # Average only available annual values; report incomplete baselines rather than filling them.
    weights=ok.groupby('alpha3',as_index=False).agg(mean_export_value_usd=('trade_value_usd','mean'),years_available=('year','nunique'))
    weights=weights[weights.mean_export_value_usd.gt(0)].copy(); weights['direct_weight_selected']=weights.mean_export_value_usd/weights.mean_export_value_usd.sum()
    weights.to_csv(out/'UNComtrade_direct_weights_2005_2007.csv',index=False)
    imports=pd.read_csv(a.partner_imports); model=pd.read_csv(a.model_data); wide=imports.pivot(index='quarter',columns='alpha3',values='value').reindex(model.quarter)
    w=weights.set_index('alpha3').direct_weight_selected.reindex(wide.columns).fillna(0.0)
    levels=[]; covs=[]; counts=[]
    for q,row in wide.iterrows():
        valid=row.notna()&w.gt(0); cov=float(w[valid].sum()); covs.append(cov); counts.append(int(valid.sum())); levels.append(float((row[valid]*w[valid]).sum()/cov) if cov>0 else np.nan)
    d=pd.DataFrame({'quarter':model.quarter,'uncomtrade_demand_index':levels,'uncomtrade_coverage':covs,'uncomtrade_market_count':counts}); d['uncomtrade_demand_qoq']=d.uncomtrade_demand_index.pct_change(fill_method=None)*100; d['uncomtrade_demand_lag1']=d.uncomtrade_demand_qoq.shift(1); data=model.merge(d,on='quarter',how='left')
    regs=['uncomtrade_demand_qoq','uncomtrade_demand_lag1','reer_lag1','export_qoq_lag1']; f,r=fit(data,regs); eff,se,p=combo(r,'uncomtrade_demand_qoq','uncomtrade_demand_lag1')
    result={'current':float(r.params.uncomtrade_demand_qoq),'current_se':float(r.bse.uncomtrade_demand_qoq),'current_p':float(r.pvalues.uncomtrade_demand_qoq),'lag1':float(r.params.uncomtrade_demand_lag1),'lag1_p':float(r.pvalues.uncomtrade_demand_lag1),'current_plus_lag':eff,'sum_se':se,'sum_p':p,'n':int(r.nobs),'adjusted_r_squared':float(r.rsquared_adj),'mean_quarterly_weight_coverage':float(np.mean(covs))}
    old=pd.read_csv(a.old_weights); oldcol='fixed_weight_ex_hk_mac' if 'fixed_weight_ex_hk_mac' in old.columns else [c for c in old.columns if 'fixed_weight' in c][0]; comp=old[['alpha3',oldcol]].drop_duplicates().merge(weights,on='alpha3',how='inner'); comp['old_weight_common']=comp[oldcol]/comp[oldcol].sum(); comp['uncomtrade_weight_common']=comp.direct_weight_selected/comp.direct_weight_selected.sum(); corr=float(comp[['old_weight_common','uncomtrade_weight_common']].corr().iloc[0,1]); result['weight_correlation_old_vs_uncomtrade']=corr; comp.to_csv(out/'UNComtrade_vs_reconstructed_weight_comparison.csv',index=False); data.to_csv(out/'P1_UNComtrade_model_data.csv',index=False); pd.DataFrame([result]).to_csv(out/'P1_UNComtrade_model_summary.csv',index=False)
    status={'source':'UN Comtrade public preview API','api':API,'partner_reference_sha256':refsha,'requested_partners':SELECTED,'complete_three_year_partners':int((availability==3).sum()),'missing_years':missing_years,'result':result,'interpretation':'Direct UN Comtrade China-to-destination values replace the reconstructed 2005-2007 exposure vector as a weight-source sensitivity. The quarterly outcome and partner import-volume series remain WTO Stats data; the regression remains a conditional aggregate association.'}; (out/'P1_UNComtrade_status.json').write_text(json.dumps(status,indent=2),encoding='utf-8'); print(json.dumps(status,indent=2))
if __name__=='__main__': main()
