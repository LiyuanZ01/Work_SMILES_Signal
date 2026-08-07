#!/usr/bin/env python3
"""Clean P0 method-correction runner without fragile plotting logic."""
from __future__ import annotations
import json, math, os
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats
from statsmodels.stats.diagnostic import linear_reset
from statsmodels.stats.outliers_influence import OLSInfluence

HAC_LAGS=4
IN_DIR=Path(os.environ.get('P0_INPUT_DIR','external_demand_wto/official_results'))
OUT=Path(os.environ.get('P0_OUTPUT_DIR','external_demand_wto/p0_corrected_results'))
OUT.mkdir(parents=True,exist_ok=True)

def fit(data, regs):
    frame=data.dropna(subset=['export_qoq',*regs]).copy()
    X=sm.add_constant(frame[regs],has_constant='add')
    ols=sm.OLS(frame['export_qoq'],X).fit()
    hac=sm.OLS(frame['export_qoq'],X).fit(cov_type='HAC',cov_kwds={'maxlags':HAC_LAGS,'use_correction':True},use_t=True)
    return frame,ols,hac

def combo(fit,a,b):
    cov=fit.cov_params(); eff=float(fit.params[a]+fit.params[b]); var=float(cov.loc[a,a]+cov.loc[b,b]+2*cov.loc[a,b]); se=math.sqrt(max(var,0)); t=eff/se if se else np.nan; p=float(2*stats.t.sf(abs(t),fit.df_resid)) if np.isfinite(t) else np.nan
    return {'effect':eff,'se':se,'p_value':p}

def rows(name,fit,frame):
    ci=fit.conf_int(); out=[]
    for p in fit.params.index:
        out.append({'model':name,'parameter':p,'coefficient':float(fit.params[p]),'std_error_hac4':float(fit.bse[p]),'p_value':float(fit.pvalues[p]),'ci_low_95':float(ci.loc[p,0]),'ci_high_95':float(ci.loc[p,1]),'n':int(fit.nobs),'adjusted_r_squared':float(fit.rsquared_adj),'sample_start':str(frame['quarter'].iloc[0]),'sample_end':str(frame['quarter'].iloc[-1])})
    return out

def main():
    model=pd.read_csv(IN_DIR/'v15_model_data.csv')
    imports=pd.read_csv(IN_DIR/'wto_partner_import_volume.csv')
    rolling=pd.read_csv(IN_DIR/'rolling_weights_top20_2005_2024.csv')
    qs=model['quarter'].tolist(); wide=imports.pivot(index='quarter',columns='alpha3',values='value').reindex(qs); pg=wide.pct_change(fill_method=None)*100
    rmap=rolling.pivot(index='sample_year',columns='alpha3',values='rolling_weight_ex_hk_mac')
    rr=[]
    for q in qs:
        w=rmap.loc[int(q[:4])].astype(float); v=pg.loc[q].reindex(w.index); valid=v.notna()&w.notna()&w.gt(0); cov=float(w[valid].sum()); g=float((v[valid]*w[valid]).sum()/cov) if cov>0 else np.nan
        rr.append({'quarter':q,'rolling_demand_growth_corrected':g,'rolling_growth_coverage':cov,'rolling_growth_market_count':int(valid.sum())})
    data=model.merge(pd.DataFrame(rr),on='quarter',how='left')
    data['rolling_demand_growth_corrected_lag1']=data['rolling_demand_growth_corrected'].shift(1)
    data['rolling_growth_eligible70']=data['rolling_growth_coverage'].ge(.70)
    data['fixed_demand_qoq_sq']=data['fixed_demand_qoq']**2
    data['fixed_demand_x_post2018']=data['fixed_demand_qoq']*data['post2018']
    data['rolling_change_error']=data['rolling_demand_qoq']-data['rolling_demand_growth_corrected']
    data['is_q1']=data['quarter'].str.endswith('Q1')
    outrows=[]; S={}
    fixed=['fixed_demand_qoq','fixed_demand_lag1','reer_lag1','export_qoq_lag1']; f0,o0,h0=fit(data,fixed); outrows+=rows('Fixed 2005-2007 baseline',h0,f0)
    S['fixed_baseline']={'current':float(h0.params.fixed_demand_qoq),'current_se':float(h0.bse.fixed_demand_qoq),'current_p':float(h0.pvalues.fixed_demand_qoq),'lag1':float(h0.params.fixed_demand_lag1),'lag1_p':float(h0.pvalues.fixed_demand_lag1),'current_plus_lag1':combo(h0,'fixed_demand_qoq','fixed_demand_lag1'),'n':int(h0.nobs),'adjusted_r_squared':float(h0.rsquared_adj)}
    rregs=['rolling_demand_growth_corrected','rolling_demand_growth_corrected_lag1','reer_lag1','export_qoq_lag1']; fr,orr,hr=fit(data,rregs); outrows+=rows('Corrected rolling lagged weights',hr,fr)
    S['rolling_corrected']={'current':float(hr.params.rolling_demand_growth_corrected),'current_se':float(hr.bse.rolling_demand_growth_corrected),'current_p':float(hr.pvalues.rolling_demand_growth_corrected),'lag1':float(hr.params.rolling_demand_growth_corrected_lag1),'lag1_p':float(hr.pvalues.rolling_demand_growth_corrected_lag1),'current_plus_lag1':combo(hr,'rolling_demand_growth_corrected','rolling_demand_growth_corrected_lag1'),'n':int(hr.nobs),'adjusted_r_squared':float(hr.rsquared_adj)}
    ft,ot,ht=fit(data[data.rolling_growth_eligible70],rregs); outrows+=rows('Corrected rolling weights, coverage >=70%',ht,ft)
    S['rolling_corrected_coverage70']={'current':float(ht.params.rolling_demand_growth_corrected),'current_se':float(ht.bse.rolling_demand_growth_corrected),'current_p':float(ht.pvalues.rolling_demand_growth_corrected),'current_plus_lag1':combo(ht,'rolling_demand_growth_corrected','rolling_demand_growth_corrected_lag1'),'n':int(ht.nobs),'adjusted_r_squared':float(ht.rsquared_adj)}
    qregs=['fixed_demand_qoq','fixed_demand_qoq_sq','fixed_demand_lag1','reer_lag1','export_qoq_lag1']; fq,oq,hq=fit(data,qregs); outrows+=rows('Quadratic current-demand sensitivity',hq,fq); reset0=linear_reset(o0,power=2,use_f=True); resetq=linear_reset(oq,power=2,use_f=True)
    S['quadratic_sensitivity']={'linear_current':float(hq.params.fixed_demand_qoq),'linear_current_se':float(hq.bse.fixed_demand_qoq),'linear_current_p':float(hq.pvalues.fixed_demand_qoq),'squared_current':float(hq.params.fixed_demand_qoq_sq),'squared_current_se':float(hq.bse.fixed_demand_qoq_sq),'squared_current_p':float(hq.pvalues.fixed_demand_qoq_sq),'baseline_reset_p':float(reset0.pvalue),'quadratic_reset_p':float(resetq.pvalue),'n':int(hq.nobs),'adjusted_r_squared':float(hq.rsquared_adj)}
    infl=OLSInfluence(o0); idx=list(o0.params.index).index('fixed_demand_qoq'); inf=pd.DataFrame({'quarter':f0.quarter.to_numpy(),'cooks_d':infl.cooks_distance[0],'studentized_residual':infl.resid_studentized_external,'leverage':infl.hat_matrix_diag,'dfbeta_current_demand':infl.dfbetas[:,idx]}).sort_values('cooks_d',ascending=False); top=inf.iloc[0]
    S['influence']={'largest_quarter':str(top.quarter),'largest_cooks_d':float(top.cooks_d),'largest_studentized_residual':float(top.studentized_residual),'largest_leverage':float(top.leverage),'largest_dfbeta_current':float(top.dfbeta_current_demand)}
    preg=['fixed_demand_qoq','fixed_demand_lag1','reer_lag1','export_qoq_lag1','post2018','fixed_demand_x_post2018']; fp,op,hp=fit(data,preg); outrows+=rows('Post-2018 interaction, conservative',hp,fp); pc=combo(hp,'fixed_demand_qoq','fixed_demand_x_post2018')
    S['post2018_conservative']={'pre2018_current':float(hp.params.fixed_demand_qoq),'pre2018_current_p':float(hp.pvalues.fixed_demand_qoq),'interaction':float(hp.params.fixed_demand_x_post2018),'interaction_se':float(hp.bse.fixed_demand_x_post2018),'interaction_p':float(hp.pvalues.fixed_demand_x_post2018),'implied_post2018_current':pc}
    pa=preg+['dummy_2020q1','dummy_2020q2']; fa,oa,ha=fit(data,pa); outrows+=rows('Post-2018 interaction, pandemic adjusted',ha,fa); pac=combo(ha,'fixed_demand_qoq','fixed_demand_x_post2018')
    S['post2018_pandemic_adjusted']={'pre2018_current':float(ha.params.fixed_demand_qoq),'pre2018_current_p':float(ha.pvalues.fixed_demand_qoq),'interaction':float(ha.params.fixed_demand_x_post2018),'interaction_se':float(ha.bse.fixed_demand_x_post2018),'interaction_p':float(ha.pvalues.fixed_demand_x_post2018),'implied_post2018_current':pac}
    e=data.rolling_change_error; q1=data.is_q1; S['rolling_construction_error']={'mean_abs_difference_all_quarters':float(e.abs().mean()),'mean_abs_difference_q1':float(e[q1].abs().mean()),'mean_abs_difference_non_q1':float(e[~q1].abs().mean()),'maximum_abs_difference':float(e.abs().max()),'maximum_difference_quarter':str(data.loc[e.abs().idxmax(),'quarter'])}
    distinct=pd.DataFrame([
      {'specification':'Fixed 2005-2007 baseline','current':S['fixed_baseline']['current'],'se':S['fixed_baseline']['current_se'],'p':S['fixed_baseline']['current_p'],'n':S['fixed_baseline']['n'],'status':'primary'},
      {'specification':'Corrected rolling lagged weights','current':S['rolling_corrected']['current'],'se':S['rolling_corrected']['current_se'],'p':S['rolling_corrected']['current_p'],'n':S['rolling_corrected']['n'],'status':'weight robustness'},
      {'specification':'Corrected rolling weights, coverage >=70%','current':S['rolling_corrected_coverage70']['current'],'se':S['rolling_corrected_coverage70']['current_se'],'p':S['rolling_corrected_coverage70']['current_p'],'n':S['rolling_corrected_coverage70']['n'],'status':'coverage robustness'},
      {'specification':'Quadratic sensitivity: linear current term','current':S['quadratic_sensitivity']['linear_current'],'se':S['quadratic_sensitivity']['linear_current_se'],'p':S['quadratic_sensitivity']['linear_current_p'],'n':S['quadratic_sensitivity']['n'],'status':'functional-form sensitivity'}])
    data.to_csv(OUT/'P0_corrected_model_data.csv',index=False); pd.DataFrame(outrows).to_csv(OUT/'P0_corrected_model_estimates.csv',index=False); inf.to_csv(OUT/'P0_influence_diagnostics.csv',index=False); distinct.to_csv(OUT/'P0_distinct_sensitivity_summary.csv',index=False)
    pd.DataFrame([{'specification':'Conservative','pre2018_current':S['post2018_conservative']['pre2018_current'],'interaction':S['post2018_conservative']['interaction'],'interaction_se':S['post2018_conservative']['interaction_se'],'interaction_p':S['post2018_conservative']['interaction_p'],'implied_post2018_current':pc['effect'],'implied_post2018_se':pc['se'],'implied_post2018_p':pc['p_value']},{'specification':'Pandemic adjusted','pre2018_current':S['post2018_pandemic_adjusted']['pre2018_current'],'interaction':S['post2018_pandemic_adjusted']['interaction'],'interaction_se':S['post2018_pandemic_adjusted']['interaction_se'],'interaction_p':S['post2018_pandemic_adjusted']['interaction_p'],'implied_post2018_current':pac['effect'],'implied_post2018_se':pac['se'],'implied_post2018_p':pac['p_value']}]).to_csv(OUT/'P0_post2018_interaction_sensitivity.csv',index=False)
    status={'status':'complete','generated_at_utc':datetime.now(timezone.utc).isoformat(),'input_dir':str(IN_DIR),'correction':'Rolling quarterly demand growth is the lagged rolling-weight average of partner q/q import-volume growth rates; annual changes in the weight vector are not counted as quarterly demand growth.','summaries':S}
    (OUT/'P0_method_correction_status.json').write_text(json.dumps(status,indent=2),encoding='utf-8'); print(json.dumps(status,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
