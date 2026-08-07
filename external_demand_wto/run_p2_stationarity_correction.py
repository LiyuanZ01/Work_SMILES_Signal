#!/usr/bin/env python3
from __future__ import annotations
import json, math, hashlib, os
from pathlib import Path
import numpy as np, pandas as pd
import statsmodels.api as sm
from scipy import stats
from statsmodels.stats.stattools import durbin_watson, jarque_bera
from statsmodels.stats.diagnostic import het_breuschpagan, linear_reset

INPUT=Path(os.environ.get('P2_CURRENT_INPUT','external_demand_wto/p2_current_results'))
OUT=Path(os.environ.get('P2_CORRECTED_OUTPUT','external_demand_wto/p2_corrected_results'))
OUT.mkdir(parents=True,exist_ok=True)
HAC=4; SEED=20260730; REPS=int(os.environ.get('P2_CORRECTED_REPS','3000'))

def fit(df,name,dep,regs,mask=None):
    d=df.copy()
    if mask is not None: d=d.loc[mask(d)].copy()
    d=d.dropna(subset=[dep,*regs]).copy()
    X=sm.add_constant(d[regs],has_constant='add')
    ols=sm.OLS(d[dep],X).fit()
    r=ols.get_robustcov_results(cov_type='HAC',maxlags=HAC,use_correction=True,use_t=True)
    names=list(X.columns); ci=np.asarray(r.conf_int())
    rows=[]
    for i,p in enumerate(names):
        rows.append(dict(model=name,dependent=dep,parameter=p,coefficient=float(r.params[i]),std_error_hac4=float(r.bse[i]),p_value=float(r.pvalues[i]),ci_low_95=float(ci[i,0]),ci_high_95=float(ci[i,1]),n=int(r.nobs),r_squared=float(ols.rsquared),sample_start=d.quarter.iloc[0],sample_end=d.quarter.iloc[-1]))
    terms=[p for p in regs if (p.startswith('export_') or p.startswith('d_export_')) and 'post2018' not in p]
    idx=[names.index(p) for p in terms]
    eff=float(np.asarray(r.params)[idx].sum()) if idx else np.nan
    cov=np.asarray(r.cov_params()); var=float(cov[np.ix_(idx,idx)].sum()) if idx else np.nan
    se=math.sqrt(max(var,0)) if idx else np.nan
    pv=float(2*stats.t.sf(abs(eff/se),r.df_resid)) if idx and se>0 else np.nan
    try: reset=float(linear_reset(ols,power=2,use_f=True).pvalue)
    except Exception: reset=np.nan
    jb=jarque_bera(ols.resid); bp=het_breuschpagan(ols.resid,X)
    summ=dict(model=name,dependent=dep,regressors=regs,n=int(r.nobs),sample_start=d.quarter.iloc[0],sample_end=d.quarter.iloc[-1],r_squared=float(ols.rsquared),export_cumulative_effect=eff,export_cumulative_se=se,export_cumulative_p=pv,durbin_watson=float(durbin_watson(ols.resid)),jarque_bera_p=float(jb[1]),breusch_pagan_p=float(bp[1]),reset_p=reset)
    return pd.DataFrame(rows),summ,d

def boot(d,dep,regs,terms,name,block=4,reps=REPS):
    x=d.dropna(subset=[dep,*regs]).reset_index(drop=True); n=len(x); rng=np.random.default_rng(SEED+n+len(regs))
    starts=np.arange(max(n-block+1,1)); vals=[]
    for _ in range(reps):
        inds=[]
        while len(inds)<n:
            s=int(rng.choice(starts)); inds.extend(range(s,min(s+block,n)))
        b=x.iloc[inds[:n]]
        try:
            X=sm.add_constant(b[regs],has_constant='add'); f=sm.OLS(b[dep],X).fit()
            vals.append([float(f.params[t]) for t in terms])
        except Exception: pass
    a=np.asarray(vals); cum=a.sum(axis=1)
    out=dict(model=name,dependent=dep,reps=len(a),block=block,cumulative_median=float(np.median(cum)),cumulative_ci_low_95=float(np.quantile(cum,.025)),cumulative_ci_high_95=float(np.quantile(cum,.975)))
    for j,t in enumerate(terms): out.update({f'{t}_median':float(np.median(a[:,j])),f'{t}_ci_low_95':float(np.quantile(a[:,j],.025)),f'{t}_ci_high_95':float(np.quantile(a[:,j],.975))})
    return out

d=pd.read_csv(INPUT/'P2_current_model_data.csv')
d['trend']=np.arange(len(d),dtype=float); d['trend2']=d['trend']**2
for col in ['gdp_yoy_real','export_yoy','reer_yoy']:
    d[f'd_{col}']=d[col].diff()
    for lag in range(1,4): d[f'd_{col}_lag{lag}']=d[f'd_{col}'].shift(lag)
for col in ['gdp_qoq_sa','export_qoq']:
    lo,hi=d[col].quantile([.025,.975]); d[f'{col}_winsor']=d[col].clip(lo,hi)
for lag in range(1,3): d[f'export_qoq_winsor_lag{lag}']=d['export_qoq_winsor'].shift(lag)

specs=[
('Y_trend','gdp_yoy_real',['export_yoy','export_yoy_lag1','export_yoy_lag2','gdp_yoy_real_lag1','reer_yoy_lag1','trend'],None),
('Y_trend_excl2020H1','gdp_yoy_real',['export_yoy','export_yoy_lag1','export_yoy_lag2','gdp_yoy_real_lag1','reer_yoy_lag1','trend'],lambda x:~x.quarter.isin(['2020Q1','2020Q2'])),
('Y_trend_quadratic','gdp_yoy_real',['export_yoy','export_yoy_lag1','export_yoy_lag2','gdp_yoy_real_lag1','reer_yoy_lag1','trend','trend2'],None),
('DY_main','d_gdp_yoy_real',['d_export_yoy','d_export_yoy_lag1','d_export_yoy_lag2','d_gdp_yoy_real_lag1','d_reer_yoy_lag1'],None),
('DY_excl2020H1','d_gdp_yoy_real',['d_export_yoy','d_export_yoy_lag1','d_export_yoy_lag2','d_gdp_yoy_real_lag1','d_reer_yoy_lag1'],lambda x:~x.quarter.isin(['2020Q1','2020Q2'])),
('DY_lag_only','d_gdp_yoy_real',['d_export_yoy_lag1','d_export_yoy_lag2','d_gdp_yoy_real_lag1','d_reer_yoy_lag1'],None),
('Q_pandemic_window_dummy','gdp_qoq_sa',['export_qoq','export_qoq_lag1','export_qoq_lag2','gdp_qoq_sa_lag1','reer_qoq_lag1','pandemic_h1'],None),
('Q_winsor','gdp_qoq_sa_winsor',['export_qoq_winsor','export_qoq_winsor_lag1','export_qoq_winsor_lag2','gdp_qoq_sa_lag1','reer_qoq_lag1'],None),
]
rows=[]; sums=[]; frames={}
for s in specs:
    r,smry,fr=fit(d,*s); rows.append(r); sums.append(smry); frames[s[0]]=(fr,s[1],s[2])
coef=pd.concat(rows,ignore_index=True); smry=pd.DataFrame(sums)
boots=[]
for name,terms in [('Y_trend',['export_yoy','export_yoy_lag1','export_yoy_lag2']),('DY_main',['d_export_yoy','d_export_yoy_lag1','d_export_yoy_lag2']),('DY_excl2020H1',['d_export_yoy','d_export_yoy_lag1','d_export_yoy_lag2']),('Q_winsor',['export_qoq_winsor','export_qoq_winsor_lag1','export_qoq_winsor_lag2'])]:
    fr,dep,regs=frames[name]; boots.append(boot(fr,dep,regs,terms,name))
loo=[]; regs=['export_qoq','export_qoq_lag1','export_qoq_lag2','gdp_qoq_sa_lag1','reer_qoq_lag1']; base=d.dropna(subset=['gdp_qoq_sa',*regs]).copy()
for q in base.quarter:
    r,_,_=fit(base,'tmp','gdp_qoq_sa',regs,lambda x,q=q:x.quarter.ne(q)); z=r[r.parameter.eq('export_qoq')].iloc[0]
    loo.append(dict(omitted_quarter=q,coefficient=z.coefficient,p_value=z.p_value,ci_low_95=z.ci_low_95,ci_high_95=z.ci_high_95))
loo=pd.DataFrame(loo)
coef.to_csv(OUT/'P2_corrected_models.csv',index=False); smry.to_csv(OUT/'P2_corrected_summaries.csv',index=False); pd.DataFrame(boots).to_csv(OUT/'P2_corrected_bootstrap.csv',index=False); loo.to_csv(OUT/'P2_qoq_leave_one_quarter_out.csv',index=False)
status={'status':'complete','interpretation':'stationarity- and pandemic-sensitive national association audit; no causal multiplier','key':{},'limitations':['The q/q result is dominated by 2020H1 and is not stable under exclusion or winsorization.','The y/y level relationship must be interpreted with trend controls because real-GDP y/y growth is nonstationary over 2005-2024.','First-difference models estimate co-movement in growth accelerations, not output multipliers.','Aggregate simultaneity remains unresolved.']}
for m,p in [('Y_trend','export_yoy'),('Y_trend_excl2020H1','export_yoy'),('DY_main','d_export_yoy'),('DY_excl2020H1','d_export_yoy'),('DY_lag_only','d_export_yoy_lag1'),('Q_pandemic_window_dummy','export_qoq'),('Q_winsor','export_qoq_winsor')]:
    z=coef[(coef.model.eq(m))&(coef.parameter.eq(p))].iloc[0]; status['key'][m]={'coefficient':float(z.coefficient),'p_value':float(z.p_value),'n':int(z.n)}
status['qoq_loo_coefficient_range']=[float(loo.coefficient.min()),float(loo.coefficient.max())]; status['qoq_loo_p_above_0_05_count']=int((loo.p_value>.05).sum())
json.dump(status,open(OUT/'P2_corrected_status.json','w'),indent=2)
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
fig,ax=plt.subplots(figsize=(10,5)); ax.errorbar(loo.coefficient,np.arange(len(loo)),xerr=np.vstack([loo.coefficient-loo.ci_low_95,loo.ci_high_95-loo.coefficient]),fmt='o',markersize=3,capsize=2); ax.axvline(0,lw=.8); ax.set_yticks(np.arange(0,len(loo),4)); ax.set_yticklabels(loo.omitted_quarter.iloc[::4]); ax.set_xlabel('Current export coefficient after omitting one quarter'); ax.set_title('P2 q/q GDP association: leave-one-quarter-out sensitivity'); fig.tight_layout(); fig.savefig(OUT/'Figure_P2_QoQ_LOO.png',dpi=220); plt.close(fig)
manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.iterdir() if p.is_file()}; json.dump(manifest,open(OUT/'P2_corrected_manifest.json','w'),indent=2)
print(json.dumps(status,indent=2))
