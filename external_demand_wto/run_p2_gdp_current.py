#!/usr/bin/env python3
"""P2 national export–GDP association audit using current OECD G20 QNA data.

This module estimates conditional associations only. It does not identify a
causal export multiplier and does not replace the planned provincial design.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import scipy
import statsmodels
import statsmodels.api as sm
from scipy import stats
from statsmodels.stats.diagnostic import het_breuschpagan, linear_reset
from statsmodels.stats.stattools import durbin_watson, jarque_bera
from statsmodels.tsa.stattools import adfuller, kpss

INPUT_DIR = Path(os.environ.get('V15_OUTPUT_DIR', 'external_demand_wto/results'))
OUTPUT_DIR = Path(os.environ.get('P2_CURRENT_OUTPUT_DIR', 'external_demand_wto/p2_current_results'))
BOOT_REPS = int(os.environ.get('P2_CURRENT_BOOT_REPS', '2500'))
HAC_LAGS = 4
SEED = 20260730

OECD_URL = (
    'https://sdmx.oecd.org/public/rest/data/'
    'OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA_EXPENDITURE_GROWTH_G20,1.1/'
    'Q...S1..B1GQ.......?startPeriod=2005-Q1&endPeriod=2024-Q4'
    '&dimensionAtObservation=AllDimensions&format=csvfilewithlabels'
)


def download(url: str, attempts: int = 5) -> bytes:
    session = requests.Session()
    session.headers.update({'User-Agent': 'China-external-demand-P2-GDP/2.0', 'Accept': 'text/csv,*/*'})
    last = None
    for i in range(attempts):
        try:
            r = session.get(url, timeout=180)
            r.raise_for_status()
            return r.content
        except Exception as exc:
            last = exc
            if i + 1 < attempts:
                time.sleep(2 ** i)
    raise RuntimeError(f'Failed to download OECD current GDP data: {last}')


def clean_quarter(value: object) -> str:
    return str(value).replace('-', '').strip()


def fetch_oecd_gdp() -> tuple[pd.DataFrame, dict]:
    raw = download(OECD_URL)
    df = pd.read_csv(BytesIO(raw), low_memory=False)
    required = {'REF_AREA', 'FREQ', 'ADJUSTMENT', 'TRANSACTION', 'PRICE_BASE',
                'TRANSFORMATION', 'TIME_PERIOD', 'OBS_VALUE'}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f'OECD response lacks required fields: {sorted(missing)}')
    filt = df[
        df['REF_AREA'].astype(str).eq('CHN')
        & df['FREQ'].astype(str).eq('Q')
        & df['ADJUSTMENT'].astype(str).eq('Y')
        & df['TRANSACTION'].astype(str).eq('B1GQ')
        & df['PRICE_BASE'].astype(str).eq('L')
        & df['TRANSFORMATION'].astype(str).isin(['G1', 'GY'])
    ].copy()
    filt['quarter'] = filt['TIME_PERIOD'].map(clean_quarter)
    filt['OBS_VALUE'] = pd.to_numeric(filt['OBS_VALUE'], errors='coerce')
    filt = filt.dropna(subset=['quarter', 'OBS_VALUE'])
    pivot = filt.pivot_table(index='quarter', columns='TRANSFORMATION', values='OBS_VALUE', aggfunc='last')
    pivot = pivot.rename(columns={'G1': 'gdp_qoq_sa', 'GY': 'gdp_yoy_real'}).reset_index()
    for col in ['gdp_qoq_sa', 'gdp_yoy_real']:
        if col not in pivot:
            pivot[col] = np.nan
    audit = {
        'url': OECD_URL,
        'sha256': hashlib.sha256(raw).hexdigest(),
        'downloaded_at_utc': datetime.now(timezone.utc).isoformat(),
        'raw_bytes': len(raw),
        'raw_rows': int(len(df)),
        'filtered_rows': int(len(filt)),
        'qoq_observations': int(pivot['gdp_qoq_sa'].notna().sum()),
        'qoq_start': pivot.loc[pivot['gdp_qoq_sa'].notna(), 'quarter'].min(),
        'qoq_end': pivot.loc[pivot['gdp_qoq_sa'].notna(), 'quarter'].max(),
        'yoy_observations': int(pivot['gdp_yoy_real'].notna().sum()),
        'yoy_start': pivot.loc[pivot['gdp_yoy_real'].notna(), 'quarter'].min(),
        'yoy_end': pivot.loc[pivot['gdp_yoy_real'].notna(), 'quarter'].max(),
        'definition_qoq': 'OECD G20 QNA, real GDP, calendar and seasonally adjusted, period-on-period growth',
        'definition_yoy': 'OECD G20 QNA, real GDP, calendar and seasonally adjusted, growth over one year',
    }
    if audit['qoq_end'] != '2024Q4' or audit['yoy_end'] != '2024Q4':
        raise RuntimeError(f'Current OECD GDP series do not reach 2024Q4: {audit}')
    return pivot.sort_values('quarter'), audit


def prepare_data(gdp: pd.DataFrame) -> pd.DataFrame:
    path = INPUT_DIR / 'v15_model_data.csv'
    if not path.exists():
        raise FileNotFoundError(path)
    model = pd.read_csv(path)
    model['quarter'] = model['quarter'].astype(str)
    data = model.merge(gdp, on='quarter', how='left', validate='one_to_one')
    data['post2018'] = data['quarter'].str[:4].astype(int).ge(2018).astype(int)
    data['pandemic_h1'] = data['quarter'].isin(['2020Q1', '2020Q2']).astype(int)
    data['reer_yoy'] = data['reer_index'].pct_change(4, fill_method=None) * 100
    for stem in ['export_qoq', 'export_yoy', 'gdp_qoq_sa', 'gdp_yoy_real', 'reer_qoq', 'reer_yoy']:
        for lag in range(1, 5):
            data[f'{stem}_lag{lag}'] = data[stem].shift(lag)
    data['export_qoq_post2018'] = data['export_qoq'] * data['post2018']
    data['export_yoy_post2018'] = data['export_yoy'] * data['post2018']
    return data


def fit_model(data: pd.DataFrame, name: str, dependent: str, regressors: list[str], mask=None):
    frame = data.copy()
    if mask is not None:
        frame = frame.loc[mask(frame)].copy()
    frame = frame.dropna(subset=[dependent, *regressors]).copy()
    x = sm.add_constant(frame[regressors], has_constant='add')
    ols = sm.OLS(frame[dependent], x).fit()
    fit = ols.get_robustcov_results(cov_type='HAC', maxlags=HAC_LAGS, use_correction=True, use_t=True)
    names = list(x.columns)
    conf = np.asarray(fit.conf_int())
    rows = []
    for i, parameter in enumerate(names):
        rows.append({
            'model': name, 'dependent': dependent, 'parameter': parameter,
            'coefficient': float(fit.params[i]), 'std_error_hac4': float(fit.bse[i]),
            't_stat': float(fit.tvalues[i]), 'p_value': float(fit.pvalues[i]),
            'ci_low_95': float(conf[i, 0]), 'ci_high_95': float(conf[i, 1]),
            'n': int(fit.nobs), 'r_squared': float(ols.rsquared),
            'adjusted_r_squared': float(ols.rsquared_adj),
            'sample_start': frame['quarter'].iloc[0], 'sample_end': frame['quarter'].iloc[-1],
        })
    jb = jarque_bera(ols.resid)
    bp = het_breuschpagan(ols.resid, x)
    try:
        reset = linear_reset(ols, power=2, use_f=True)
        reset_p = float(reset.pvalue)
    except Exception:
        reset_p = np.nan
    influence = ols.get_influence()
    summary = {
        'model': name, 'dependent': dependent, 'regressors': regressors,
        'n': int(fit.nobs), 'r_squared': float(ols.rsquared),
        'adjusted_r_squared': float(ols.rsquared_adj),
        'sample_start': frame['quarter'].iloc[0], 'sample_end': frame['quarter'].iloc[-1],
        'durbin_watson': float(durbin_watson(ols.resid)),
        'jarque_bera_p': float(jb[1]), 'breusch_pagan_p': float(bp[1]),
        'reset_p': reset_p, 'max_cooks_distance': float(np.nanmax(influence.cooks_distance[0])),
    }
    export_terms = [c for c in regressors if c.startswith('export_') and 'post2018' not in c]
    if export_terms:
        idx = [names.index(c) for c in export_terms]
        effect = float(np.asarray(fit.params)[idx].sum())
        cov = np.asarray(fit.cov_params())
        variance = float(cov[np.ix_(idx, idx)].sum())
        se = math.sqrt(max(variance, 0))
        tval = effect / se if se else np.nan
        pval = float(2 * stats.t.sf(abs(tval), fit.df_resid)) if np.isfinite(tval) else np.nan
        summary.update({'export_cumulative_effect': effect, 'export_cumulative_se': se,
                        'export_cumulative_p': pval, 'export_terms': export_terms})
    return pd.DataFrame(rows), summary, frame


def stationary(series: pd.Series, name: str) -> dict:
    x = pd.to_numeric(series, errors='coerce').dropna()
    out = {'series': name, 'n': int(len(x))}
    try:
        a = adfuller(x, autolag='AIC')
        out.update({'adf_stat': float(a[0]), 'adf_p': float(a[1])})
    except Exception:
        out.update({'adf_stat': np.nan, 'adf_p': np.nan})
    try:
        k = kpss(x, regression='c', nlags='auto')
        out.update({'kpss_stat': float(k[0]), 'kpss_p': float(k[1])})
    except Exception:
        out.update({'kpss_stat': np.nan, 'kpss_p': np.nan})
    return out


def block_bootstrap(data: pd.DataFrame, dependent: str, regressors: list[str], terms: list[str],
                    reps: int, block: int = 4) -> dict:
    frame = data.dropna(subset=[dependent, *regressors]).reset_index(drop=True)
    n = len(frame)
    rng = np.random.default_rng(SEED + n + len(regressors))
    estimates = []
    starts = np.arange(0, max(n - block + 1, 1))
    for _ in range(reps):
        indices = []
        while len(indices) < n:
            s = int(rng.choice(starts))
            indices.extend(range(s, min(s + block, n)))
        boot = frame.iloc[indices[:n]].reset_index(drop=True)
        try:
            x = sm.add_constant(boot[regressors], has_constant='add')
            fit = sm.OLS(boot[dependent], x).fit()
            estimates.append([float(fit.params[t]) for t in terms])
        except Exception:
            continue
    arr = np.asarray(estimates)
    result = {'dependent': dependent, 'block': block, 'requested_reps': reps,
              'successful_reps': int(len(arr))}
    for j, term in enumerate(terms):
        result[f'{term}_median'] = float(np.median(arr[:, j]))
        result[f'{term}_ci_low_95'] = float(np.quantile(arr[:, j], .025))
        result[f'{term}_ci_high_95'] = float(np.quantile(arr[:, j], .975))
    cumulative = arr.sum(axis=1)
    result.update({'cumulative_median': float(np.median(cumulative)),
                   'cumulative_ci_low_95': float(np.quantile(cumulative, .025)),
                   'cumulative_ci_high_95': float(np.quantile(cumulative, .975))})
    return result


def make_figures(data: pd.DataFrame, coef: pd.DataFrame) -> None:
    plot = data.dropna(subset=['gdp_yoy_real', 'export_yoy']).copy()
    fig, ax = plt.subplots(figsize=(11, 5.5))
    x = np.arange(len(plot))
    ax.plot(x, plot['export_yoy'], label='WTO merchandise export volume, y/y')
    ax.plot(x, plot['gdp_yoy_real'], label='Real GDP, y/y')
    ticks = np.arange(0, len(plot), 8)
    ax.set_xticks(ticks)
    ax.set_xticklabels(plot['quarter'].iloc[ticks], rotation=45, ha='right')
    ax.axhline(0, linewidth=.8)
    ax.set_ylabel('Percent')
    ax.set_title('China: export-volume and real-GDP growth')
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / 'Figure_P2_Export_and_GDP_Growth.png', dpi=220)
    plt.close(fig)

    terms = coef[(coef['parameter'].isin(['export_qoq', 'export_yoy'])) &
                 (coef['model'].isin(['Q_main', 'Q_exclude_2020H1', 'Q_pre2018', 'Q_post2018',
                                      'Y_main', 'Y_exclude_2020H1', 'Y_pre2018', 'Y_post2018']))].copy()
    labels = terms['model'].tolist()
    fig, ax = plt.subplots(figsize=(9, 5.5))
    y = np.arange(len(terms))
    values = terms['coefficient'].to_numpy()
    low = values - terms['ci_low_95'].to_numpy()
    high = terms['ci_high_95'].to_numpy() - values
    ax.errorbar(values, y, xerr=np.vstack([low, high]), fmt='o', capsize=3)
    ax.axvline(0, linewidth=.8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlabel('Conditional export-growth coefficient (95% HAC interval)')
    ax.set_title('National export–GDP association: specification sensitivity')
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / 'Figure_P2_GDP_Coefficients.png', dpi=220)
    plt.close(fig)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    gdp, source_audit = fetch_oecd_gdp()
    data = prepare_data(gdp)
    specs = [
        ('Q_main', 'gdp_qoq_sa', ['export_qoq', 'export_qoq_lag1', 'export_qoq_lag2', 'gdp_qoq_sa_lag1', 'reer_qoq_lag1'], None),
        ('Q_current_only', 'gdp_qoq_sa', ['export_qoq', 'gdp_qoq_sa_lag1', 'reer_qoq_lag1'], None),
        ('Q_lag_only', 'gdp_qoq_sa', ['export_qoq_lag1', 'export_qoq_lag2', 'gdp_qoq_sa_lag1', 'reer_qoq_lag1'], None),
        ('Q_exclude_2020H1', 'gdp_qoq_sa', ['export_qoq', 'export_qoq_lag1', 'export_qoq_lag2', 'gdp_qoq_sa_lag1', 'reer_qoq_lag1'], lambda d: ~d['quarter'].isin(['2020Q1', '2020Q2'])),
        ('Q_pre2018', 'gdp_qoq_sa', ['export_qoq', 'export_qoq_lag1', 'export_qoq_lag2', 'gdp_qoq_sa_lag1', 'reer_qoq_lag1'], lambda d: d['post2018'].eq(0)),
        ('Q_post2018', 'gdp_qoq_sa', ['export_qoq', 'export_qoq_lag1', 'export_qoq_lag2', 'gdp_qoq_sa_lag1', 'reer_qoq_lag1'], lambda d: d['post2018'].eq(1)),
        ('Q_interaction', 'gdp_qoq_sa', ['export_qoq', 'export_qoq_post2018', 'post2018', 'gdp_qoq_sa_lag1', 'reer_qoq_lag1'], None),
        ('Y_main', 'gdp_yoy_real', ['export_yoy', 'export_yoy_lag1', 'export_yoy_lag2', 'gdp_yoy_real_lag1', 'reer_yoy_lag1'], None),
        ('Y_current_only', 'gdp_yoy_real', ['export_yoy', 'gdp_yoy_real_lag1', 'reer_yoy_lag1'], None),
        ('Y_lag_only', 'gdp_yoy_real', ['export_yoy_lag1', 'export_yoy_lag2', 'gdp_yoy_real_lag1', 'reer_yoy_lag1'], None),
        ('Y_exclude_2020H1', 'gdp_yoy_real', ['export_yoy', 'export_yoy_lag1', 'export_yoy_lag2', 'gdp_yoy_real_lag1', 'reer_yoy_lag1'], lambda d: ~d['quarter'].isin(['2020Q1', '2020Q2'])),
        ('Y_pre2018', 'gdp_yoy_real', ['export_yoy', 'export_yoy_lag1', 'export_yoy_lag2', 'gdp_yoy_real_lag1', 'reer_yoy_lag1'], lambda d: d['post2018'].eq(0)),
        ('Y_post2018', 'gdp_yoy_real', ['export_yoy', 'export_yoy_lag1', 'export_yoy_lag2', 'gdp_yoy_real_lag1', 'reer_yoy_lag1'], lambda d: d['post2018'].eq(1)),
        ('Y_interaction', 'gdp_yoy_real', ['export_yoy', 'export_yoy_post2018', 'post2018', 'gdp_yoy_real_lag1', 'reer_yoy_lag1'], None),
    ]
    coef_frames, summaries = [], []
    fitted = {}
    for name, dep, regs, mask in specs:
        rows, summary, frame = fit_model(data, name, dep, regs, mask)
        coef_frames.append(rows)
        summaries.append(summary)
        fitted[name] = (frame, regs)
    coefs = pd.concat(coef_frames, ignore_index=True)
    summary_df = pd.DataFrame(summaries)

    boot = []
    for name, dep, terms in [
        ('Q_main', 'gdp_qoq_sa', ['export_qoq', 'export_qoq_lag1', 'export_qoq_lag2']),
        ('Q_exclude_2020H1', 'gdp_qoq_sa', ['export_qoq', 'export_qoq_lag1', 'export_qoq_lag2']),
        ('Y_main', 'gdp_yoy_real', ['export_yoy', 'export_yoy_lag1', 'export_yoy_lag2']),
        ('Y_exclude_2020H1', 'gdp_yoy_real', ['export_yoy', 'export_yoy_lag1', 'export_yoy_lag2']),
    ]:
        frame, regs = fitted[name]
        result = block_bootstrap(frame, dep, regs, terms, BOOT_REPS, block=4)
        result['model'] = name
        boot.append(result)
    boot_df = pd.DataFrame(boot)

    stationarity = pd.DataFrame([
        stationary(data['export_qoq'], 'export_qoq'),
        stationary(data['export_yoy'], 'export_yoy'),
        stationary(data['gdp_qoq_sa'], 'gdp_qoq_sa'),
        stationary(data['gdp_yoy_real'], 'gdp_yoy_real'),
    ])

    data.to_csv(OUTPUT_DIR / 'P2_current_model_data.csv', index=False)
    coefs.to_csv(OUTPUT_DIR / 'P2_current_GDP_models.csv', index=False)
    summary_df.to_csv(OUTPUT_DIR / 'P2_current_model_summaries.csv', index=False)
    boot_df.to_csv(OUTPUT_DIR / 'P2_current_block_bootstrap.csv', index=False)
    stationarity.to_csv(OUTPUT_DIR / 'P2_current_stationarity.csv', index=False)
    with open(OUTPUT_DIR / 'P2_current_source_audit.json', 'w', encoding='utf-8') as f:
        json.dump(source_audit, f, indent=2, ensure_ascii=False)
    make_figures(data, coefs)

    def get(model, parameter):
        row = coefs[(coefs['model'].eq(model)) & (coefs['parameter'].eq(parameter))]
        return None if row.empty else {'coefficient': float(row.iloc[0]['coefficient']),
                                      'p_value': float(row.iloc[0]['p_value']),
                                      'n': int(row.iloc[0]['n'])}

    status = {
        'status': 'complete_with_explicit_sample_limits',
        'analysis_scope': 'national conditional export–GDP association; not a causal multiplier',
        'source_audit': source_audit,
        'qoq_main_current_export': get('Q_main', 'export_qoq'),
        'qoq_exclude_2020H1_current_export': get('Q_exclude_2020H1', 'export_qoq'),
        'qoq_lag_only_export_lag1': get('Q_lag_only', 'export_qoq_lag1'),
        'yoy_main_current_export': get('Y_main', 'export_yoy'),
        'yoy_exclude_2020H1_current_export': get('Y_exclude_2020H1', 'export_yoy'),
        'yoy_lag_only_export_lag1': get('Y_lag_only', 'export_yoy_lag1'),
        'bootstrap_reps': BOOT_REPS,
        'started_at_utc': started.isoformat(),
        'finished_at_utc': datetime.now(timezone.utc).isoformat(),
        'limitations': [
            'The OECD seasonally adjusted q/q GDP series for China begins in 2011Q1.',
            'The y/y GDP series provides the longer 2005–2024 validation sample.',
            'Exports are not externally instrumented; simultaneous shocks remain possible.',
            'GDP mechanically includes net exports, so contemporaneous coefficients are not multipliers.',
            'This aggregate module cannot replace province-level shift-share identification.',
        ],
    }
    with open(OUTPUT_DIR / 'P2_current_status.json', 'w', encoding='utf-8') as f:
        json.dump(status, f, indent=2, ensure_ascii=False)

    manifest = {'python': sys.version, 'platform': platform.platform(),
                'pandas': pd.__version__, 'numpy': np.__version__,
                'statsmodels': statsmodels.__version__, 'scipy': scipy.__version__,
                'outputs': {}}
    for path in sorted(OUTPUT_DIR.iterdir()):
        if path.is_file():
            manifest['outputs'][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    with open(OUTPUT_DIR / 'P2_current_manifest.json', 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    print(json.dumps(status, indent=2, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
