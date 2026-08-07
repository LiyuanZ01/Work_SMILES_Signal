#!/usr/bin/env python3
"""P1 sensitivity using direct WTO 2005-2007 bilateral China export-value weights."""
from __future__ import annotations
import argparse, io, json, math, re, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats


def read_portal_csv(zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as z:
        names=[n for n in z.namelist() if n.lower().endswith('.csv')]
        if len(names)!=1: raise RuntimeError(f'Expected one CSV in {zip_path}: {names}')
        raw=z.read(names[0])
    last=None
    for enc in ['utf-8-sig','cp1252','latin1']:
        try: return pd.read_csv(io.BytesIO(raw),encoding=enc)
        except Exception as exc: last=exc
    raise last


def norm(s):
    return re.sub(r'[^a-z0-9]+',' ',str(s).lower()).strip()


def fit(data, regs):
    f=data.dropna(subset=['export_qoq',*regs]).copy(); X=sm.add_constant(f[regs],has_constant='add')
    r=sm.OLS(f.export_qoq,X).fit(cov_type='HAC',cov_kwds={'maxlags':4,'use_correction':True},use_t=True); return f,r


def combo(r,a,b):
    c=r.cov_params(); eff=float(r.params[a]+r.params[b]); var=float(c.loc[a,a]+c.loc[b,b]+2*c.loc[a,b]); se=math.sqrt(max(var,0)); p=float(2*stats.t.sf(abs(eff/se),r.df_resid)); return eff,se,p


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bilateral-zip',required=True); ap.add_argument('--partner-imports',required=True); ap.add_argument('--model-data',required=True); ap.add_argument('--old-weights',required=True); ap.add_argument('--output-dir',required=True); a=ap.parse_args(); out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
    b=read_portal_csv(Path(a.bilateral_zip)); imports=pd.read_csv(a.partner_imports); model=pd.read_csv(a.model_data); old=pd.read_csv(a.old_weights)
    required={'Year','Partner Economy','Value'}
    if not required.issubset(b.columns): raise RuntimeError(f'Unexpected bilateral columns: {list(b.columns)}')
    b=b[b['Year'].astype(int).between(2005,2007)].copy(); b['value_usd_million']=pd.to_numeric(b['Value'],errors='coerce'); b['partner_norm']=b['Partner Economy'].map(norm)
    mapdf=imports[['economy','alpha3']].drop_duplicates().copy(); mapdf['partner_norm']=mapdf.economy.map(norm)
    # Explicit aliases for WTO naming variants.
    aliases={'united states of america':'united states of america','korea republic of':'korea republic of','russian federation':'russian federation','turkiye':'türkiye','türkiye':'türkiye'}
    b=b.merge(mapdf[['partner_norm','alpha3','economy']],on='partner_norm',how='left')
    unmatched=b[b.alpha3.isna()]['Partner Economy'].drop_duplicates().tolist()
    if unmatched: raise RuntimeError(f'Unmatched partners: {unmatched}')
    vals=b.groupby(['alpha3','economy'],as_index=False).agg(mean_2005_2007_value=('value_usd_million','mean'),years=('Year','nunique'))
    vals=vals[vals.mean_2005_2007_value.gt(0)].copy(); vals['direct_weight_selected20']=vals.mean_2005_2007_value/vals.mean_2005_2007_value.sum()
    # Direct weights are applied only to available quarterly volume series; missing UAE remains absent and coverage is recorded.
    wide=imports.pivot(index='quarter',columns='alpha3',values='value').reindex(model.quarter)
    w=vals.set_index('alpha3').direct_weight_selected20.reindex(wide.columns).fillna(0.0)
    level=[]; cov=[]; count=[]
    for q,row in wide.iterrows():
        valid=row.notna()&w.gt(0); c=float(w[valid].sum()); cov.append(c); count.append(int(valid.sum())); level.append(float((row[valid]*w[valid]).sum()/c) if c>0 else np.nan)
    direct=pd.DataFrame({'quarter':model.quarter,'direct_bilateral_demand_index':level,'direct_bilateral_coverage':cov,'direct_bilateral_market_count':count})
    direct['direct_bilateral_demand_qoq']=direct.direct_bilateral_demand_index.pct_change(fill_method=None)*100
    direct['direct_bilateral_demand_lag1']=direct.direct_bilateral_demand_qoq.shift(1)
    data=model.merge(direct,on='quarter',how='left')
    regs=['direct_bilateral_demand_qoq','direct_bilateral_demand_lag1','reer_lag1','export_qoq_lag1']; f,r=fit(data,regs); eff,se,p=combo(r,'direct_bilateral_demand_qoq','direct_bilateral_demand_lag1')
    result={'current':float(r.params.direct_bilateral_demand_qoq),'current_se':float(r.bse.direct_bilateral_demand_qoq),'current_p':float(r.pvalues.direct_bilateral_demand_qoq),'lag1':float(r.params.direct_bilateral_demand_lag1),'lag1_p':float(r.pvalues.direct_bilateral_demand_lag1),'current_plus_lag':eff,'sum_se':se,'sum_p':p,'n':int(r.nobs),'adjusted_r_squared':float(r.rsquared_adj),'mean_direct_coverage':float(np.mean(cov))}
    # Compare direct selected-market weights to reconstructed fixed market weights after renormalizing both over the same available alpha3 set.
    oldcol='fixed_weight_ex_hk_mac' if 'fixed_weight_ex_hk_mac' in old.columns else [c for c in old.columns if 'fixed_weight' in c][0]
    old2=old[['alpha3',oldcol]].drop_duplicates().merge(vals[['alpha3','economy','direct_weight_selected20']],on='alpha3',how='inner'); old2['old_selected_weight']=old2[oldcol]/old2[oldcol].sum(); old2['direct_selected_weight']=old2.direct_weight_selected20/old2.direct_weight_selected20.sum(); corr=float(old2[['old_selected_weight','direct_selected_weight']].corr().iloc[0,1]); result['weight_correlation']=corr
    vals.to_csv(out/'P1_direct_bilateral_weights.csv',index=False); old2.to_csv(out/'P1_weight_comparison.csv',index=False); data.to_csv(out/'P1_direct_weight_model_data.csv',index=False); pd.DataFrame([result]).to_csv(out/'P1_direct_weight_model_summary.csv',index=False); (out/'P1_direct_weight_status.json').write_text(json.dumps({'result':result,'bilateral_rows':int(len(b)),'partner_count':int(vals.alpha3.nunique()),'unmatched':unmatched,'interpretation':'Direct WTO bilateral 2005-2007 export values are used only as a weight-source sensitivity. The model remains a conditional aggregate association.'},indent=2),encoding='utf-8'); print(json.dumps(result,indent=2))

if __name__=='__main__': main()
