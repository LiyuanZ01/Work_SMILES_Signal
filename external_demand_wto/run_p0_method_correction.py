#!/usr/bin/env python3
"""P0 method correction for the China external-demand paper.

This audit addresses four submission-critical issues:
1. Correct rolling weights by aggregating partner q/q growth rates directly,
   avoiding mechanical jumps when the annual rolling-weight vector changes.
2. Add a quadratic-demand sensitivity because the linear baseline fails RESET.
3. Quantify influential observations, especially 2020Q2.
4. Compare post-2018 interaction estimates with and without observation-specific
   pandemic adjustment.

The script consumes the direct-WTO official-results folder produced by
build_official_wto_results.py. It does not alter the fixed-weight baseline.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats
from statsmodels.stats.diagnostic import linear_reset
from statsmodels.stats.outliers_influence import OLSInfluence

HAC_LAGS = 4
IN_DIR = Path(os.environ.get("P0_INPUT_DIR", "external_demand_wto/official_results"))
OUT = Path(os.environ.get("P0_OUTPUT_DIR", "external_demand_wto/p0_corrected_results"))
OUT.mkdir(parents=True, exist_ok=True)


def fit_hac(data: pd.DataFrame, dependent: str, regressors: list[str], name: str) -> tuple[pd.DataFrame, sm.regression.linear_model.RegressionResultsWrapper, sm.regression.linear_model.RegressionResultsWrapper]:
    frame = data.dropna(subset=[dependent, *regressors]).copy()
    x = sm.add_constant(frame[regressors], has_constant="add")
    ols = sm.OLS(frame[dependent], x).fit()
    fit = sm.OLS(frame[dependent], x).fit(
        cov_type="HAC",
        cov_kwds={"maxlags": HAC_LAGS, "use_correction": True},
        use_t=True,
    )
    return frame, ols, fit


def parameter_rows(name: str, fit, frame: pd.DataFrame) -> list[dict]:
    conf = fit.conf_int()
    rows = []
    for parameter in fit.params.index:
        rows.append({
            "model": name,
            "parameter": parameter,
            "coefficient": float(fit.params[parameter]),
            "std_error_hac4": float(fit.bse[parameter]),
            "p_value": float(fit.pvalues[parameter]),
            "ci_low_95": float(conf.loc[parameter, 0]),
            "ci_high_95": float(conf.loc[parameter, 1]),
            "n": int(fit.nobs),
            "adjusted_r_squared": float(fit.rsquared_adj),
            "sample_start": str(frame["quarter"].iloc[0]),
            "sample_end": str(frame["quarter"].iloc[-1]),
        })
    return rows


def linear_combo(fit, a: str, b: str) -> dict:
    cov = fit.cov_params()
    effect = float(fit.params[a] + fit.params[b])
    variance = float(cov.loc[a, a] + cov.loc[b, b] + 2 * cov.loc[a, b])
    se = math.sqrt(max(variance, 0.0))
    t = effect / se if se else np.nan
    p = float(2 * stats.t.sf(abs(t), fit.df_resid)) if np.isfinite(t) else np.nan
    return {"effect": effect, "se": se, "p_value": p}


def build_corrected_rolling(model: pd.DataFrame, imports: pd.DataFrame, rolling: pd.DataFrame) -> pd.DataFrame:
    quarters = model["quarter"].tolist()
    wide = imports.pivot(index="quarter", columns="alpha3", values="value").reindex(quarters)
    partner_growth = wide.pct_change(fill_method=None) * 100.0
    rolling_map = rolling.pivot(
        index="sample_year", columns="alpha3", values="rolling_weight_ex_hk_mac"
    )
    rows = []
    for quarter in quarters:
        year = int(str(quarter)[:4])
        weights = rolling_map.loc[year].astype(float)
        values = partner_growth.loc[quarter].reindex(weights.index)
        valid = values.notna() & weights.notna() & weights.gt(0)
        coverage = float(weights[valid].sum())
        growth = float((values[valid] * weights[valid]).sum() / coverage) if coverage > 0 else np.nan
        rows.append({
            "quarter": quarter,
            "rolling_demand_growth_corrected": growth,
            "rolling_growth_coverage": coverage,
            "rolling_growth_market_count": int(valid.sum()),
        })
    corrected = model.merge(pd.DataFrame(rows), on="quarter", how="left")
    corrected["rolling_demand_growth_corrected_lag1"] = corrected["rolling_demand_growth_corrected"].shift(1)
    corrected["rolling_growth_eligible70"] = corrected["rolling_growth_coverage"].ge(0.70)
    corrected["fixed_demand_qoq_sq"] = corrected["fixed_demand_qoq"] ** 2
    corrected["fixed_demand_x_post2018"] = corrected["fixed_demand_qoq"] * corrected["post2018"]
    corrected["rolling_change_error"] = corrected["rolling_demand_qoq"] - corrected["rolling_demand_growth_corrected"]
    corrected["is_q1"] = corrected["quarter"].astype(str).str.endswith("Q1")
    return corrected


def main() -> int:
    model = pd.read_csv(IN_DIR / "v15_model_data.csv")
    imports = pd.read_csv(IN_DIR / "wto_partner_import_volume.csv")
    rolling = pd.read_csv(IN_DIR / "rolling_weights_top20_2005_2024.csv")
    corrected = build_corrected_rolling(model, imports, rolling)

    all_rows = []
    summaries = {}

    # Fixed baseline retained unchanged.
    fixed_regs = ["fixed_demand_qoq", "fixed_demand_lag1", "reer_lag1", "export_qoq_lag1"]
    f0, ols0, fit0 = fit_hac(corrected, "export_qoq", fixed_regs, "Fixed 2005-2007 baseline")
    all_rows += parameter_rows("Fixed 2005-2007 baseline", fit0, f0)
    summaries["fixed_baseline"] = {
        "current": float(fit0.params["fixed_demand_qoq"]),
        "current_se": float(fit0.bse["fixed_demand_qoq"]),
        "current_p": float(fit0.pvalues["fixed_demand_qoq"]),
        "lag1": float(fit0.params["fixed_demand_lag1"]),
        "lag1_p": float(fit0.pvalues["fixed_demand_lag1"]),
        "current_plus_lag1": linear_combo(fit0, "fixed_demand_qoq", "fixed_demand_lag1"),
        "n": int(fit0.nobs),
    }

    # Corrected rolling-growth aggregation.
    rolling_regs = [
        "rolling_demand_growth_corrected", "rolling_demand_growth_corrected_lag1",
        "reer_lag1", "export_qoq_lag1",
    ]
    fr, olsr, fitr = fit_hac(corrected, "export_qoq", rolling_regs, "Corrected rolling lagged weights")
    all_rows += parameter_rows("Corrected rolling lagged weights", fitr, fr)
    summaries["rolling_corrected"] = {
        "current": float(fitr.params["rolling_demand_growth_corrected"]),
        "current_se": float(fitr.bse["rolling_demand_growth_corrected"]),
        "current_p": float(fitr.pvalues["rolling_demand_growth_corrected"]),
        "lag1": float(fitr.params["rolling_demand_growth_corrected_lag1"]),
        "lag1_p": float(fitr.pvalues["rolling_demand_growth_corrected_lag1"]),
        "current_plus_lag1": linear_combo(fitr, "rolling_demand_growth_corrected", "rolling_demand_growth_corrected_lag1"),
        "n": int(fitr.nobs),
        "adjusted_r_squared": float(fitr.rsquared_adj),
    }

    threshold = corrected[corrected["rolling_growth_eligible70"]].copy()
    ft, olst, fitt = fit_hac(threshold, "export_qoq", rolling_regs, "Corrected rolling weights, coverage >=70%")
    all_rows += parameter_rows("Corrected rolling weights, coverage >=70%", fitt, ft)
    summaries["rolling_corrected_coverage70"] = {
        "current": float(fitt.params["rolling_demand_growth_corrected"]),
        "current_se": float(fitt.bse["rolling_demand_growth_corrected"]),
        "current_p": float(fitt.pvalues["rolling_demand_growth_corrected"]),
        "current_plus_lag1": linear_combo(fitt, "rolling_demand_growth_corrected", "rolling_demand_growth_corrected_lag1"),
        "n": int(fitt.nobs),
    }

    # Quadratic sensitivity for RESET failure.
    quad_regs = [
        "fixed_demand_qoq", "fixed_demand_qoq_sq", "fixed_demand_lag1",
        "reer_lag1", "export_qoq_lag1",
    ]
    fq, olsq, fitq = fit_hac(corrected, "export_qoq", quad_regs, "Quadratic current-demand sensitivity")
    all_rows += parameter_rows("Quadratic current-demand sensitivity", fitq, fq)
    reset0 = linear_reset(ols0, power=2, use_f=True)
    resetq = linear_reset(olsq, power=2, use_f=True)
    summaries["quadratic_sensitivity"] = {
        "linear_current": float(fitq.params["fixed_demand_qoq"]),
        "linear_current_se": float(fitq.bse["fixed_demand_qoq"]),
        "linear_current_p": float(fitq.pvalues["fixed_demand_qoq"]),
        "squared_current": float(fitq.params["fixed_demand_qoq_sq"]),
        "squared_current_se": float(fitq.bse["fixed_demand_qoq_sq"]),
        "squared_current_p": float(fitq.pvalues["fixed_demand_qoq_sq"]),
        "baseline_reset_p": float(reset0.pvalue),
        "quadratic_reset_p": float(resetq.pvalue),
        "n": int(fitq.nobs),
        "adjusted_r_squared": float(fitq.rsquared_adj),
    }

    # Influence diagnostics from conventional OLS leverage/influence formulas.
    influence = OLSInfluence(ols0)
    current_idx = list(ols0.params.index).index("fixed_demand_qoq")
    influence_frame = pd.DataFrame({
        "quarter": f0["quarter"].to_numpy(),
        "cooks_d": influence.cooks_distance[0],
        "studentized_residual": influence.resid_studentized_external,
        "leverage": influence.hat_matrix_diag,
        "dfbeta_current_demand": influence.dfbetas[:, current_idx],
    }).sort_values("cooks_d", ascending=False)
    top = influence_frame.iloc[0]
    summaries["influence"] = {
        "largest_quarter": str(top["quarter"]),
        "largest_cooks_d": float(top["cooks_d"]),
        "largest_studentized_residual": float(top["studentized_residual"]),
        "largest_leverage": float(top["leverage"]),
        "largest_dfbeta_current": float(top["dfbeta_current_demand"]),
    }

    # Post-2018 interaction, with and without pandemic saturation.
    post_regs = [
        "fixed_demand_qoq", "fixed_demand_lag1", "reer_lag1", "export_qoq_lag1",
        "post2018", "fixed_demand_x_post2018",
    ]
    fp, olsp, fitp = fit_hac(corrected, "export_qoq", post_regs, "Post-2018 interaction, conservative")
    all_rows += parameter_rows("Post-2018 interaction, conservative", fitp, fp)
    post_combo = linear_combo(fitp, "fixed_demand_qoq", "fixed_demand_x_post2018")
    summaries["post2018_conservative"] = {
        "pre2018_current": float(fitp.params["fixed_demand_qoq"]),
        "pre2018_current_p": float(fitp.pvalues["fixed_demand_qoq"]),
        "interaction": float(fitp.params["fixed_demand_x_post2018"]),
        "interaction_se": float(fitp.bse["fixed_demand_x_post2018"]),
        "interaction_p": float(fitp.pvalues["fixed_demand_x_post2018"]),
        "implied_post2018_current": post_combo,
    }

    post_adj_regs = post_regs + ["dummy_2020q1", "dummy_2020q2"]
    fpa, olspa, fitpa = fit_hac(corrected, "export_qoq", post_adj_regs, "Post-2018 interaction, pandemic adjusted")
    all_rows += parameter_rows("Post-2018 interaction, pandemic adjusted", fitpa, fpa)
    post_adj_combo = linear_combo(fitpa, "fixed_demand_qoq", "fixed_demand_x_post2018")
    summaries["post2018_pandemic_adjusted"] = {
        "pre2018_current": float(fitpa.params["fixed_demand_qoq"]),
        "pre2018_current_p": float(fitpa.pvalues["fixed_demand_qoq"]),
        "interaction": float(fitpa.params["fixed_demand_x_post2018"]),
        "interaction_se": float(fitpa.bse["fixed_demand_x_post2018"]),
        "interaction_p": float(fitpa.pvalues["fixed_demand_x_post2018"]),
        "implied_post2018_current": post_adj_combo,
    }

    # Mechanical weight-change contamination diagnostic.
    error = corrected["rolling_change_error"]
    q1 = corrected["is_q1"]
    summaries["rolling_construction_error"] = {
        "mean_abs_difference_all_quarters": float(error.abs().mean()),
        "mean_abs_difference_q1": float(error[q1].abs().mean()),
        "mean_abs_difference_non_q1": float(error[~q1].abs().mean()),
        "maximum_abs_difference": float(error.abs().max()),
        "maximum_difference_quarter": str(corrected.loc[error.abs().idxmax(), "quarter"]),
        "interpretation": "Old rolling-index growth mixes partner volume changes with annual changes in the weight vector; corrected growth directly aggregates partner q/q growth using lagged rolling weights.",
    }

    # A concise distinct-sensitivity table; duplicate fixed renormalized/unnormalized rows are intentionally not repeated.
    distinct = pd.DataFrame([
        {
            "specification": "Fixed 2005-2007 baseline",
            "current": summaries["fixed_baseline"]["current"],
            "se": summaries["fixed_baseline"]["current_se"],
            "p": summaries["fixed_baseline"]["current_p"],
            "n": summaries["fixed_baseline"]["n"],
            "status": "primary",
        },
        {
            "specification": "Corrected rolling lagged weights",
            "current": summaries["rolling_corrected"]["current"],
            "se": summaries["rolling_corrected"]["current_se"],
            "p": summaries["rolling_corrected"]["current_p"],
            "n": summaries["rolling_corrected"]["n"],
            "status": "weight robustness",
        },
        {
            "specification": "Corrected rolling weights, coverage >=70%",
            "current": summaries["rolling_corrected_coverage70"]["current"],
            "se": summaries["rolling_corrected_coverage70"]["current_se"],
            "p": summaries["rolling_corrected_coverage70"]["current_p"],
            "n": summaries["rolling_corrected_coverage70"]["n"],
            "status": "coverage robustness",
        },
        {
            "specification": "Quadratic current-demand sensitivity: linear term",
            "current": summaries["quadratic_sensitivity"]["linear_current"],
            "se": summaries["quadratic_sensitivity"]["linear_current_se"],
            "p": summaries["quadratic_sensitivity"]["linear_current_p"],
            "n": summaries["quadratic_sensitivity"]["n"],
            "status": "functional-form sensitivity",
        },
    ])

    # Plots.
    plot = corrected.dropna(subset=["rolling_demand_qoq", "rolling_demand_growth_corrected"])
    fig, ax = plt.subplots(figsize=(8, 4.6))
    ax.plot(plot["quarter"], plot["rolling_demand_qoq"], label="Old rolling-index growth", linewidth=1.1)
    ax.plot(plot["quarter"], plot["rolling_demand_growth_corrected"], label="Corrected rolling weighted growth", linewidth=1.1)
    ax.set_ylabel("Quarter-on-quarter percent")
    ax.set_xlabel("Quarter")
    ax.tick_params(axis="x", labelrotation=90)
    step = max(1, len(plot) // 12)
    for label in ax.get_xticklabels():
        label.set_visible(False)
    for i in range(0, len(plot), step):
        ax.get_xticklabels()[i].set_visible(True)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(OUT / "Figure_P0_Rolling_Weight_Correction.png", dpi=300)
    plt.close(fig)

    top10 = influence_frame.head(10).sort_values("cooks_d")
    fig, ax = plt.subplots(figsize=(7, 4.4))
    ax.barh(top10["quarter"], top10["cooks_d"])
    ax.set_xlabel("Cook's distance")
    ax.set_ylabel("Quarter")
    fig.tight_layout()
    fig.savefig(OUT / "Figure_P0_Influence_Diagnostics.png", dpi=300)
    plt.close(fig)

    corrected.to_csv(OUT / "P0_corrected_model_data.csv", index=False)
    pd.DataFrame(all_rows).to_csv(OUT / "P0_corrected_model_estimates.csv", index=False)
    influence_frame.to_csv(OUT / "P0_influence_diagnostics.csv", index=False)
    distinct.to_csv(OUT / "P0_distinct_sensitivity_summary.csv", index=False)
    pd.DataFrame([
        {
            "specification": "Conservative",
            "pre2018_current": summaries["post2018_conservative"]["pre2018_current"],
            "interaction": summaries["post2018_conservative"]["interaction"],
            "interaction_se": summaries["post2018_conservative"]["interaction_se"],
            "interaction_p": summaries["post2018_conservative"]["interaction_p"],
            "implied_post2018_current": summaries["post2018_conservative"]["implied_post2018_current"]["effect"],
            "implied_post2018_se": summaries["post2018_conservative"]["implied_post2018_current"]["se"],
            "implied_post2018_p": summaries["post2018_conservative"]["implied_post2018_current"]["p_value"],
        },
        {
            "specification": "Pandemic adjusted",
            "pre2018_current": summaries["post2018_pandemic_adjusted"]["pre2018_current"],
            "interaction": summaries["post2018_pandemic_adjusted"]["interaction"],
            "interaction_se": summaries["post2018_pandemic_adjusted"]["interaction_se"],
            "interaction_p": summaries["post2018_pandemic_adjusted"]["interaction_p"],
            "implied_post2018_current": summaries["post2018_pandemic_adjusted"]["implied_post2018_current"]["effect"],
            "implied_post2018_se": summaries["post2018_pandemic_adjusted"]["implied_post2018_current"]["se"],
            "implied_post2018_p": summaries["post2018_pandemic_adjusted"]["implied_post2018_current"]["p_value"],
        },
    ]).to_csv(OUT / "P0_post2018_interaction_sensitivity.csv", index=False)

    status = {
        "status": "complete",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_dir": str(IN_DIR),
        "correction": "Rolling quarterly demand growth is now the lagged rolling-weight average of partner q/q import-volume growth rates; annual weight changes no longer enter as artificial quarterly growth.",
        "summaries": summaries,
        "submission_actions": [
            "Replace the old rolling-weight coefficient with the corrected result.",
            "Do not count fixed renormalized and fixed unnormalized specifications as two independent robustness checks when coverage is constant.",
            "Report the quadratic sensitivity and the RESET result.",
            "Disclose 2020Q2 as the dominant influence observation.",
            "Describe post-2018 instability as treatment-sensitive rather than a confirmed structural break.",
        ],
    }
    (OUT / "P0_method_correction_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(json.dumps(status, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
