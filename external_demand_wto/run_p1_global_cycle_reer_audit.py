#!/usr/bin/env python3
"""P1 robustness: common global trade-cycle and REER specification sensitivity.

The world merchandise import-volume index is a deliberately stringent common-cycle
control. It overlaps conceptually with partner import demand and is therefore not
interpreted as an exogenous control or a preferred structural model. The purpose is
to assess how much of the fixed-weight coefficient survives conditioning on the
contemporaneous global merchandise-trade cycle.
"""
from __future__ import annotations
import argparse, csv, io, json, math, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats
from statsmodels.stats.outliers_influence import variance_inflation_factor


def read_portal_csv(zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as z:
        names=[n for n in z.namelist() if n.lower().endswith('.csv')]
        if len(names)!=1:
            raise RuntimeError(f'Expected one CSV in {zip_path}, found {names}')
        raw=z.read(names[0])
    last=None
    for enc in ['utf-8-sig','cp1252','latin1']:
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=enc)
        except Exception as exc:
            last=exc
    raise last


def fit(data: pd.DataFrame, regs: list[str], name: str):
    frame=data.dropna(subset=['export_qoq',*regs]).copy()
    X=sm.add_constant(frame[regs],has_constant='add')
    res=sm.OLS(frame['export_qoq'],X).fit(cov_type='HAC',cov_kwds={'maxlags':4,'use_correction':True},use_t=True)
    return frame,res


def combo(res,a,b):
    cov=res.cov_params(); eff=float(res.params[a]+res.params[b]); var=float(cov.loc[a,a]+cov.loc[b,b]+2*cov.loc[a,b]); se=math.sqrt(max(var,0)); p=float(2*stats.t.sf(abs(eff/se),res.df_resid)); return eff,se,p


def model_rows(name, frame, res):
    ci=res.conf_int(); out=[]
    for par in res.params.index:
        out.append({'model':name,'parameter':par,'coefficient':float(res.params[par]),'hac_se':float(res.bse[par]),'p_value':float(res.pvalues[par]),'ci_low':float(ci.loc[par,0]),'ci_high':float(ci.loc[par,1]),'n':int(res.nobs),'adjusted_r_squared':float(res.rsquared_adj),'sample_start':str(frame.quarter.iloc[0]),'sample_end':str(frame.quarter.iloc[-1])})
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--model-data',required=True)
    ap.add_argument('--world-import-zip',required=True)
    ap.add_argument('--output-dir',required=True)
    a=ap.parse_args(); out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
    data=pd.read_csv(a.model_data)
    world=read_portal_csv(Path(a.world_import_zip))
    needed={'Year','Period Code','Value'}
    if not needed.issubset(world.columns):
        raise RuntimeError(f'Unexpected WTO export columns: {list(world.columns)}')
    world=world.copy(); world['quarter']=world['Year'].astype(int).astype(str)+world['Period Code'].astype(str); world['world_import_index']=pd.to_numeric(world['Value'],errors='coerce')
    world=world[['quarter','world_import_index']].sort_values('quarter').drop_duplicates('quarter')
    world['world_import_qoq']=world['world_import_index'].pct_change(fill_method=None)*100
    world['world_import_lag1']=world['world_import_qoq'].shift(1)
    data=data.merge(world,on='quarter',how='left')
    data['reer_log_qoq']=100*np.log(data['reer_index']/data['reer_index'].shift(1))
    data['reer_log_lag1']=data['reer_log_qoq'].shift(1)

    specs=[
      ('Baseline simple-REER',['fixed_demand_qoq','fixed_demand_lag1','reer_lag1','export_qoq_lag1']),
      ('No REER',['fixed_demand_qoq','fixed_demand_lag1','export_qoq_lag1']),
      ('Log-REER',['fixed_demand_qoq','fixed_demand_lag1','reer_log_lag1','export_qoq_lag1']),
      ('World current control',['fixed_demand_qoq','fixed_demand_lag1','world_import_qoq','reer_lag1','export_qoq_lag1']),
      ('World current+lag controls',['fixed_demand_qoq','fixed_demand_lag1','world_import_qoq','world_import_lag1','reer_lag1','export_qoq_lag1']),
      ('World current control, no REER',['fixed_demand_qoq','fixed_demand_lag1','world_import_qoq','export_qoq_lag1']),
    ]
    allrows=[]; summary=[]
    for name,regs in specs:
        frame,res=fit(data,regs,name); allrows+=model_rows(name,frame,res)
        eff,se,p=combo(res,'fixed_demand_qoq','fixed_demand_lag1')
        summary.append({'model':name,'current':float(res.params.fixed_demand_qoq),'current_se':float(res.bse.fixed_demand_qoq),'current_p':float(res.pvalues.fixed_demand_qoq),'lag1':float(res.params.fixed_demand_lag1),'lag1_p':float(res.pvalues.fixed_demand_lag1),'current_plus_lag':eff,'sum_se':se,'sum_p':p,'n':int(res.nobs),'adjusted_r_squared':float(res.rsquared_adj)})

    # Correlation/VIF for the stringent world-current model.
    vif_regs=['fixed_demand_qoq','fixed_demand_lag1','world_import_qoq','reer_lag1','export_qoq_lag1']
    vif_frame=data.dropna(subset=vif_regs+['export_qoq']).copy()
    corr=vif_frame[['fixed_demand_qoq','world_import_qoq']].corr().iloc[0,1]
    X=vif_frame[vif_regs].copy()
    vif=[{'variable':c,'vif':float(variance_inflation_factor(X.values,i))} for i,c in enumerate(X.columns)]

    pd.DataFrame(allrows).to_csv(out/'P1_model_estimates.csv',index=False)
    pd.DataFrame(summary).to_csv(out/'P1_model_summary.csv',index=False)
    pd.DataFrame(vif).to_csv(out/'P1_world_control_vif.csv',index=False)
    data.to_csv(out/'P1_model_data.csv',index=False)
    status={'world_import_rows':int(world.world_import_index.notna().sum()),'world_period_start':str(world.dropna(subset=['world_import_index']).quarter.min()),'world_period_end':str(world.dropna(subset=['world_import_index']).quarter.max()),'corr_fixed_demand_world_import':float(corr),'models':summary,'interpretation':'The world import-volume control is a stringent common-trade-cycle robustness check with substantial conceptual overlap with the partner-demand measure. A weakened partner coefficient under this control should not be interpreted as a causal decomposition.'}
    (out/'P1_status.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
    print(json.dumps(status,indent=2))

if __name__=='__main__':
    main()
