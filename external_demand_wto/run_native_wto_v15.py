#!/usr/bin/env python3
"""Reproduce the frozen v15 WTO native-volume export models.

Data are downloaded at run time from public primary/official mirrors:
- WTO quarterly seasonally adjusted merchandise volume indices via DBnomics;
- IMF IMTS/WDI-derived imports-from-China shares via Our World in Data;
- World Bank WDI current-US-dollar GDP;
- BIS broad real effective exchange rate via FRED.

The script never replaces missing partner observations with zero. It reports
selected-market weight coverage quarter by quarter and treats all coefficients
as conditional associations rather than causal elasticities.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import scipy
import statsmodels
import statsmodels.api as sm
from scipy import stats
from statsmodels.stats.stattools import durbin_watson

SAMPLE_START = "2005Q1"
SAMPLE_END = "2024Q4"
COVERAGE_THRESHOLD = 0.70
HAC_LAGS = 4
OUTPUT_DIR = Path(os.environ.get("V15_OUTPUT_DIR", "results"))

DBNOMICS_BASE = "https://api.db.nomics.world/v22/series"
OWID_SHARE_URL = (
    "https://ourworldindata.org/grapher/"
    "china-imports-as-share-of-gdp.csv?v=1&csvType=full&useColumnShortNames=false"
)
WORLD_BANK_GDP_URL = (
    "https://api.worldbank.org/v2/country/all/indicator/NY.GDP.MKTP.CD"
    "?format=json&per_page=20000&date=2000:2024"
)
FRED_REER_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=RBCNBIS"

EXPORT_SERIES = ("ITS_MTP_QXVSA", "156.TO.000.Q", "CHN", "China")
IMPORT_SERIES = {
    "USA": ("840.TO.000.Q", "United States"),
    "JPN": ("392.TO.000.Q", "Japan"),
    "DEU": ("276.TO.000.Q", "Germany"),
    "KOR": ("410.TO.000.Q", "Korea, Rep."),
    "NLD": ("528.TO.000.Q", "Netherlands"),
    "GBR": ("826.TO.000.Q", "United Kingdom"),
    "CAN": ("124.TO.000.Q", "Canada"),
    "SGP": ("702.TO.000.Q", "Singapore"),
    "MEX": ("484.TO.000.Q", "Mexico"),
    "ITA": ("380.TO.000.Q", "Italy"),
    "AUS": ("036.TO.000.Q", "Australia"),
    "FRA": ("250.TO.000.Q", "France"),
    "IND": ("356.TO.000.Q", "India"),
    "ESP": ("724.TO.000.Q", "Spain"),
    "MYS": ("458.TO.000.Q", "Malaysia"),
    "RUS": ("643.TO.000.Q", "Russian Federation"),
    "THA": ("764.TO.000.Q", "Thailand"),
    "BEL": ("056.TO.000.Q", "Belgium"),
    "TUR": ("792.TO.000.Q", "Türkiye"),
    "ARE": ("784.TO.000.Q", "United Arab Emirates"),
}

FIXED_WEIGHTS = {
    "USA": 0.2913526987559264,
    "JPN": 0.11407902634234628,
    "DEU": 0.05296011071247362,
    "KOR": 0.0483257263268586,
    "NLD": 0.039543210243881945,
    "GBR": 0.036108082748993596,
    "CAN": 0.03095513705866783,
    "SGP": 0.025623935134601136,
    "MEX": 0.02450854494096548,
    "ITA": 0.022449893302308184,
    "AUS": 0.020423745671078856,
    "FRA": 0.020204018178815508,
    "IND": 0.016221944916758636,
    "ESP": 0.015774453144272057,
    "MYS": 0.015431287889145393,
    "RUS": 0.014327203198263442,
    "THA": 0.013296495349218231,
    "BEL": 0.013157054062079315,
    "TUR": 0.00956934869399737,
    "ARE": 0.008910915425325058,
}

ROLLING_2005_BENCHMARK = {
    "USA": 0.3303763901336818,
    "JPN": 0.1508927305071378,
    "DEU": 0.05151988154404218,
    "KOR": 0.044869221829958825,
    "NLD": 0.033748871408689485,
    "CAN": 0.029043061374504322,
    "GBR": 0.028658329091202583,
}


@dataclass(frozen=True)
class DownloadRecord:
    name: str
    url: str
    downloaded_at_utc: str
    sha256: str
    bytes: int


class DownloadSession:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "China-external-demand-v15-replication/1.0",
            "Accept": "application/json,text/csv,*/*",
        })
        self.records: list[DownloadRecord] = []

    def get_bytes(self, name: str, url: str, attempts: int = 5) -> bytes:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self.session.get(url, timeout=90)
                response.raise_for_status()
                content = response.content
                self.records.append(DownloadRecord(
                    name=name,
                    url=url,
                    downloaded_at_utc=datetime.now(timezone.utc).isoformat(),
                    sha256=hashlib.sha256(content).hexdigest(),
                    bytes=len(content),
                ))
                return content
            except Exception as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"Failed to download {name} from {url}: {last_error}")

    def get_json(self, name: str, url: str) -> Any:
        return json.loads(self.get_bytes(name, url).decode("utf-8"))


def quarter_range() -> pd.Index:
    return pd.period_range(SAMPLE_START, SAMPLE_END, freq="Q").astype(str)


def canonical_quarter(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip().upper()
    match = re.search(r"(\d{4})\s*[-_/ ]?Q?([1-4])", text)
    if match:
        return f"{match.group(1)}Q{match.group(2)}"
    return None


def find_dbnomics_doc(payload: Any) -> dict[str, Any]:
    candidates: list[Any] = []
    if isinstance(payload, dict):
        candidates.extend([
            payload.get("series", {}).get("docs") if isinstance(payload.get("series"), dict) else None,
            payload.get("dataset", {}).get("series", {}).get("docs")
            if isinstance(payload.get("dataset"), dict) else None,
            payload.get("docs"),
        ])
        nested = payload.get("payload")
        if isinstance(nested, dict):
            candidates.extend([
                nested.get("series", {}).get("docs")
                if isinstance(nested.get("series"), dict) else None,
                nested.get("docs"),
            ])
    for docs in candidates:
        if isinstance(docs, list) and docs and isinstance(docs[0], dict):
            return docs[0]
    raise ValueError("DBnomics response did not contain a series document")


def fetch_dbnomics_series(
    dl: DownloadSession,
    dataset: str,
    series_code: str,
    alpha3: str,
    economy: str,
    flow: str,
) -> pd.DataFrame:
    url = f"{DBNOMICS_BASE}/WTO/{dataset}/{series_code}?observations=1"
    payload = dl.get_json(f"WTO_{dataset}_{series_code}", url)
    doc = find_dbnomics_doc(payload)
    periods = doc.get("period") or doc.get("periods")
    values = doc.get("value") or doc.get("values")
    if not isinstance(periods, list) or not isinstance(values, list):
        raise ValueError(f"Missing periods/values for WTO series {series_code}")
    if len(periods) != len(values):
        raise ValueError(f"Period/value length mismatch for WTO series {series_code}")
    frame = pd.DataFrame({"quarter": periods, "value": values})
    frame["quarter"] = frame["quarter"].map(canonical_quarter)
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["quarter"]).drop_duplicates("quarter", keep="last")
    frame = frame[frame["quarter"].isin(quarter_range())].copy()
    frame["alpha3"] = alpha3
    frame["economy"] = economy
    frame["flow"] = flow
    frame["dataset"] = dataset
    frame["series_code"] = series_code
    frame["source_url"] = url
    return frame.sort_values("quarter").reset_index(drop=True)


def download_wto_data(dl: DownloadSession) -> tuple[pd.DataFrame, pd.DataFrame]:
    dataset, code, alpha3, economy = EXPORT_SERIES
    exports = fetch_dbnomics_series(dl, dataset, code, alpha3, economy, "export")
    import_frames = []
    for partner, (series_code, partner_name) in IMPORT_SERIES.items():
        import_frames.append(fetch_dbnomics_series(
            dl, "ITS_MTP_QMVSA", series_code, partner, partner_name, "import"
        ))
    imports = pd.concat(import_frames, ignore_index=True)
    return exports, imports


def read_csv_bytes(content: bytes) -> pd.DataFrame:
    from io import BytesIO
    return pd.read_csv(BytesIO(content))


def infer_owid_value_column(frame: pd.DataFrame) -> str:
    excluded = {"Entity", "Code", "Year"}
    candidates = [column for column in frame.columns if column not in excluded]
    if len(candidates) != 1:
        numeric = [
            column for column in candidates
            if pd.api.types.is_numeric_dtype(frame[column])
        ]
        if len(numeric) == 1:
            return numeric[0]
        raise ValueError(f"Could not infer OWID value column from {candidates}")
    return candidates[0]


def download_mirror_import_panel(dl: DownloadSession) -> tuple[pd.DataFrame, dict[str, Any]]:
    share_raw = dl.get_bytes("OWID_imports_from_China_share_GDP", OWID_SHARE_URL)
    share = read_csv_bytes(share_raw)
    value_col = infer_owid_value_column(share)
    share = share.rename(columns={value_col: "imports_from_china_share_gdp_pct"})
    share["Code"] = share["Code"].astype(str).str.upper().str.strip()
    share["Year"] = pd.to_numeric(share["Year"], errors="coerce").astype("Int64")
    share["imports_from_china_share_gdp_pct"] = pd.to_numeric(
        share["imports_from_china_share_gdp_pct"], errors="coerce"
    )
    share = share[
        share["Code"].str.fullmatch(r"[A-Z]{3}", na=False)
        & share["Year"].between(2000, 2024)
        & ~share["Code"].eq("CHN")
    ].copy()

    wb_payload = dl.get_json("WDI_NY_GDP_MKTP_CD", WORLD_BANK_GDP_URL)
    if not isinstance(wb_payload, list) or len(wb_payload) < 2:
        raise ValueError("Unexpected World Bank API response")
    gdp_rows = []
    for row in wb_payload[1]:
        code = str(row.get("countryiso3code") or "").upper().strip()
        year = pd.to_numeric(row.get("date"), errors="coerce")
        value = pd.to_numeric(row.get("value"), errors="coerce")
        if re.fullmatch(r"[A-Z]{3}", code) and pd.notna(year):
            gdp_rows.append({"Code": code, "Year": int(year), "gdp_current_usd": value})
    gdp = pd.DataFrame(gdp_rows)

    panel = share.merge(gdp, on=["Code", "Year"], how="left", validate="many_to_one")
    panel["imports_from_china_usd"] = (
        panel["imports_from_china_share_gdp_pct"] / 100.0
        * panel["gdp_current_usd"]
    )
    panel["imports_from_china_usd_bn"] = panel["imports_from_china_usd"] / 1e9
    panel = panel.rename(columns={"Entity": "economy", "Code": "alpha3", "Year": "year"})
    panel = panel[[
        "year", "alpha3", "economy", "imports_from_china_share_gdp_pct",
        "gdp_current_usd", "imports_from_china_usd", "imports_from_china_usd_bn",
    ]].sort_values(["year", "alpha3"])

    baseline = panel[panel["year"].between(2005, 2007)]
    universe = sorted(baseline.loc[baseline["imports_from_china_usd"].notna(), "alpha3"].unique())
    audit = {
        "owid_value_column": value_col,
        "fixed_universe_rule": "valid ISO3 with at least one nonmissing 2005-2007 observation",
        "fixed_universe_count": len(universe),
        "panel_observations": int(panel["imports_from_china_usd"].notna().sum()),
        "panel_start_year": int(panel.loc[panel["imports_from_china_usd"].notna(), "year"].min()),
        "panel_end_year": int(panel.loc[panel["imports_from_china_usd"].notna(), "year"].max()),
    }
    return panel[panel["alpha3"].isin(universe)].copy(), audit


def build_weights(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    universe = sorted(panel["alpha3"].unique())
    fixed_average = (
        panel[panel["year"].between(2005, 2007)]
        .groupby(["alpha3", "economy"], as_index=False)["imports_from_china_usd"]
        .mean()
    )
    fixed_average["weight"] = (
        fixed_average["imports_from_china_usd"]
        / fixed_average["imports_from_china_usd"].sum()
    )
    fixed_average["weight_ex_hk_mac"] = fixed_average["weight"]
    fixed_average.loc[fixed_average["alpha3"].isin(["HKG", "MAC"]), "weight_ex_hk_mac"] = 0.0
    fixed_average["weight_ex_hk_mac"] /= fixed_average["weight_ex_hk_mac"].sum()

    hardcoded = pd.Series(FIXED_WEIGHTS, name="audited_weight")
    reconstructed_fixed = fixed_average.set_index("alpha3")["weight_ex_hk_mac"].reindex(hardcoded.index)
    fixed_diff = (reconstructed_fixed - hardcoded).abs()

    rolling_rows: list[dict[str, Any]] = []
    name_map = panel.drop_duplicates("alpha3").set_index("alpha3")["economy"].to_dict()
    for sample_year in range(2005, 2025):
        window_start, window_end = sample_year - 3, sample_year - 1
        window = panel[panel["year"].between(window_start, window_end)]
        average = window.groupby("alpha3")["imports_from_china_usd"].mean().reindex(universe)
        raw_weight = average / average.sum(skipna=True)
        ex_weight = raw_weight.copy()
        for code in ["HKG", "MAC"]:
            if code in ex_weight.index:
                ex_weight.loc[code] = 0.0
        ex_weight = ex_weight / ex_weight.sum(skipna=True)
        for alpha3 in universe:
            rolling_rows.append({
                "sample_year": sample_year,
                "window_start": window_start,
                "window_end": window_end,
                "alpha3": alpha3,
                "economy": name_map.get(alpha3, alpha3),
                "rolling_weight": raw_weight.get(alpha3, np.nan),
                "rolling_weight_ex_hk_mac": ex_weight.get(alpha3, np.nan),
                "valid_years": int(
                    window.loc[window["alpha3"].eq(alpha3), "imports_from_china_usd"].notna().sum()
                ),
            })
    rolling = pd.DataFrame(rolling_rows)
    rolling_top20 = rolling[rolling["alpha3"].isin(IMPORT_SERIES)].copy()

    y2005 = rolling_top20[rolling_top20["sample_year"].eq(2005)].set_index("alpha3")
    benchmark = pd.Series(ROLLING_2005_BENCHMARK, name="benchmark")
    reconstructed_2005 = y2005["rolling_weight_ex_hk_mac"].reindex(benchmark.index)
    rolling_diff = (reconstructed_2005 - benchmark).abs()

    audit = {
        "universe_count": len(universe),
        "fixed_top20_reconstruction_max_abs_diff": float(fixed_diff.max()),
        "fixed_top20_reconstruction_mean_abs_diff": float(fixed_diff.mean()),
        "rolling_2005_benchmark_max_abs_diff": float(rolling_diff.max()),
        "rolling_2005_benchmark_mean_abs_diff": float(rolling_diff.mean()),
        "fixed_reconstruction_tolerance": 0.01,
        "rolling_reconstruction_tolerance": 0.015,
        "fixed_validation_pass": bool(fixed_diff.max() <= 0.01),
        "rolling_validation_pass": bool(rolling_diff.max() <= 0.015),
    }
    if not audit["fixed_validation_pass"]:
        raise RuntimeError(
            "The reconstructed 2005-2007 weights differ from the audited benchmark "
            f"by as much as {fixed_diff.max():.6f}, above the 0.01 tolerance."
        )
    if not audit["rolling_validation_pass"]:
        raise RuntimeError(
            "The reconstructed 2005 rolling weights differ from the audited benchmark "
            f"by as much as {rolling_diff.max():.6f}, above the 0.015 tolerance."
        )
    return fixed_average, rolling_top20, audit


def download_reer(dl: DownloadSession) -> pd.DataFrame:
    raw = dl.get_bytes("FRED_RBCNBIS", FRED_REER_URL)
    frame = read_csv_bytes(raw)
    date_col = "DATE" if "DATE" in frame.columns else frame.columns[0]
    value_col = "RBCNBIS" if "RBCNBIS" in frame.columns else frame.columns[1]
    frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
    frame[value_col] = pd.to_numeric(frame[value_col], errors="coerce")
    frame = frame.dropna(subset=[date_col]).copy()
    frame["quarter"] = frame[date_col].dt.to_period("Q").astype(str)
    quarterly = frame.groupby("quarter", as_index=False)[value_col].mean()
    quarterly = quarterly.rename(columns={value_col: "reer_index"})
    quarterly["reer_qoq"] = quarterly["reer_index"].pct_change(fill_method=None) * 100.0
    return quarterly[quarterly["quarter"].isin(quarter_range())].copy()


def weighted_index(
    wide: pd.DataFrame,
    weights_by_quarter: Iterable[pd.Series],
    quarters: Iterable[str],
) -> pd.DataFrame:
    rows = []
    for quarter, weights in zip(quarters, weights_by_quarter):
        values = wide.loc[quarter].reindex(weights.index)
        valid = values.notna() & weights.notna() & weights.gt(0)
        coverage = float(weights[valid].sum())
        index_value = float((values[valid] * weights[valid]).sum() / coverage) if coverage > 0 else np.nan
        rows.append({
            "quarter": quarter,
            "index": index_value,
            "coverage": coverage,
            "market_count": int(valid.sum()),
        })
    return pd.DataFrame(rows)


def prepare_model_data(
    exports: pd.DataFrame,
    imports: pd.DataFrame,
    rolling_weights: pd.DataFrame,
    reer: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    quarters = list(quarter_range())
    wide = imports.pivot(index="quarter", columns="alpha3", values="value").reindex(quarters)

    fixed = pd.Series(FIXED_WEIGHTS, dtype=float)
    fixed_by_quarter = [fixed for _ in quarters]
    fixed_index = weighted_index(wide, fixed_by_quarter, quarters).rename(columns={
        "index": "fixed_external_demand_index",
        "coverage": "fixed_coverage",
        "market_count": "fixed_market_count",
    })

    rolling_map = rolling_weights.pivot(
        index="sample_year", columns="alpha3", values="rolling_weight_ex_hk_mac"
    )
    rolling_by_quarter = [
        rolling_map.loc[int(quarter[:4])].dropna().astype(float)
        for quarter in quarters
    ]
    rolling_index = weighted_index(wide, rolling_by_quarter, quarters).rename(columns={
        "index": "rolling_external_demand_index",
        "coverage": "rolling_coverage",
        "market_count": "rolling_market_count",
    })

    model = pd.DataFrame({"quarter": quarters})
    model["year"] = model["quarter"].str[:4].astype(int)
    export_series = exports.set_index("quarter")["value"].reindex(quarters)
    model["wto_export_index"] = export_series.to_numpy()
    model = model.merge(fixed_index, on="quarter", how="left")
    model = model.merge(rolling_index, on="quarter", how="left")
    model = model.merge(reer[["quarter", "reer_index", "reer_qoq"]], on="quarter", how="left")

    model["export_qoq"] = model["wto_export_index"].pct_change(fill_method=None) * 100.0
    model["export_yoy"] = model["wto_export_index"].pct_change(4, fill_method=None) * 100.0
    model["fixed_demand_qoq"] = model["fixed_external_demand_index"].pct_change(fill_method=None) * 100.0
    model["rolling_demand_qoq"] = model["rolling_external_demand_index"].pct_change(fill_method=None) * 100.0
    model["export_qoq_lag1"] = model["export_qoq"].shift(1)
    model["fixed_demand_lag1"] = model["fixed_demand_qoq"].shift(1)
    model["rolling_demand_lag1"] = model["rolling_demand_qoq"].shift(1)
    for lag in range(2, 5):
        model[f"fixed_demand_lag{lag}"] = model["fixed_demand_qoq"].shift(lag)
    model["reer_lag1"] = model["reer_qoq"].shift(1)
    model["post2018"] = model["year"].ge(2018).astype(int)
    model["dummy_2020q1"] = model["quarter"].eq("2020Q1").astype(int)
    model["dummy_2020q2"] = model["quarter"].eq("2020Q2").astype(int)
    model["fixed_demand_x_post2018"] = model["fixed_demand_qoq"] * model["post2018"]
    model["fixed_model_eligible"] = model["fixed_coverage"].ge(COVERAGE_THRESHOLD)
    model["rolling_model_eligible"] = model["rolling_coverage"].ge(COVERAGE_THRESHOLD)

    coverage = model[[
        "quarter", "fixed_coverage", "fixed_market_count",
        "rolling_coverage", "rolling_market_count",
        "fixed_model_eligible", "rolling_model_eligible",
    ]].copy()
    return model, coverage


def fit_hac(
    data: pd.DataFrame,
    model_name: str,
    dependent: str,
    regressors: list[str],
    exclude_2020: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = data.copy()
    if exclude_2020:
        frame = frame[~frame["year"].eq(2020)].copy()
    frame = frame.dropna(subset=[dependent, *regressors]).copy()
    if len(frame) <= len(regressors) + 3:
        return pd.DataFrame(), {
            "model": model_name,
            "status": "insufficient_observations",
            "n": int(len(frame)),
        }
    x = sm.add_constant(frame[regressors], has_constant="add")
    fit = sm.OLS(frame[dependent], x).fit(
        cov_type="HAC",
        cov_kwds={"maxlags": HAC_LAGS, "use_correction": True},
        use_t=True,
    )
    conf = fit.conf_int()
    rows = []
    for parameter in fit.params.index:
        rows.append({
            "model": model_name,
            "dependent": dependent,
            "parameter": parameter,
            "coefficient": float(fit.params[parameter]),
            "std_error_hac4": float(fit.bse[parameter]),
            "t_stat": float(fit.tvalues[parameter]),
            "p_value": float(fit.pvalues[parameter]),
            "ci_low_95": float(conf.loc[parameter, 0]),
            "ci_high_95": float(conf.loc[parameter, 1]),
            "n": int(fit.nobs),
            "r_squared": float(fit.rsquared),
            "adjusted_r_squared": float(fit.rsquared_adj),
            "durbin_watson": float(durbin_watson(fit.resid)),
            "sample_start": frame["quarter"].iloc[0],
            "sample_end": frame["quarter"].iloc[-1],
            "covariance": "HAC(4), finite-sample correction, t reference",
        })
    summary: dict[str, Any] = {
        "model": model_name,
        "status": "estimated",
        "dependent": dependent,
        "regressors": regressors,
        "n": int(fit.nobs),
        "r_squared": float(fit.rsquared),
        "adjusted_r_squared": float(fit.rsquared_adj),
        "durbin_watson": float(durbin_watson(fit.resid)),
        "sample_start": frame["quarter"].iloc[0],
        "sample_end": frame["quarter"].iloc[-1],
    }
    if {"fixed_demand_qoq", "fixed_demand_lag1"}.issubset(fit.params.index):
        names = list(fit.params.index)
        i, j = names.index("fixed_demand_qoq"), names.index("fixed_demand_lag1")
        covariance = np.asarray(fit.cov_params())
        effect = float(fit.params.iloc[i] + fit.params.iloc[j])
        variance = covariance[i, i] + covariance[j, j] + 2 * covariance[i, j]
        se = math.sqrt(max(float(variance), 0.0))
        t_value = effect / se if se > 0 else np.nan
        p_value = float(2 * stats.t.sf(abs(t_value), fit.df_resid)) if np.isfinite(t_value) else np.nan
        summary.update({
            "current_plus_lag1_effect": effect,
            "current_plus_lag1_se": se,
            "current_plus_lag1_p_value": p_value,
            "effect_interpretation": "two-quarter conditional association; not a causal elasticity",
        })
    return pd.DataFrame(rows), summary


def estimate_models(model_data: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    specs = [
        (
            "M_fixed_baseline",
            ["fixed_demand_qoq", "fixed_demand_lag1", "reer_lag1", "export_qoq_lag1", "dummy_2020q1", "dummy_2020q2"],
            False,
            "fixed",
        ),
        (
            "M_rolling_baseline",
            ["rolling_demand_qoq", "rolling_demand_lag1", "reer_lag1", "export_qoq_lag1", "dummy_2020q1", "dummy_2020q2"],
            False,
            "rolling",
        ),
        (
            "M_fixed_lag1_only",
            ["fixed_demand_lag1", "reer_lag1", "export_qoq_lag1", "dummy_2020q1", "dummy_2020q2"],
            False,
            "fixed",
        ),
        (
            "M_fixed_lags1_4",
            ["fixed_demand_lag1", "fixed_demand_lag2", "fixed_demand_lag3", "fixed_demand_lag4", "reer_lag1", "export_qoq_lag1", "dummy_2020q1", "dummy_2020q2"],
            False,
            "fixed",
        ),
        (
            "M_fixed_exclude2020",
            ["fixed_demand_qoq", "fixed_demand_lag1", "reer_lag1", "export_qoq_lag1"],
            True,
            "fixed",
        ),
        (
            "M_fixed_post2018",
            ["fixed_demand_qoq", "fixed_demand_lag1", "reer_lag1", "export_qoq_lag1", "post2018", "fixed_demand_x_post2018", "dummy_2020q1", "dummy_2020q2"],
            False,
            "fixed",
        ),
    ]
    rows = []
    summaries = []
    for name, regressors, exclude_2020, coverage_type in specs:
        eligible_col = "fixed_model_eligible" if coverage_type == "fixed" else "rolling_model_eligible"
        frame = model_data[model_data[eligible_col]].copy()
        result, summary = fit_hac(frame, name, "export_qoq", regressors, exclude_2020)
        if not result.empty:
            rows.append(result)
        summaries.append(summary)
    return (pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()), summaries


def make_table2(results: pd.DataFrame) -> pd.DataFrame:
    parameters = {
        "fixed_demand_qoq", "fixed_demand_lag1", "fixed_demand_lag2",
        "fixed_demand_lag3", "fixed_demand_lag4", "rolling_demand_qoq",
        "rolling_demand_lag1", "reer_lag1", "fixed_demand_x_post2018",
        "post2018", "export_qoq_lag1",
    }
    table = results[results["parameter"].isin(parameters)].copy()
    return table[[
        "model", "parameter", "coefficient", "std_error_hac4", "p_value",
        "n", "r_squared", "sample_start", "sample_end",
    ]]


def save_figure(model: pd.DataFrame, path: Path) -> None:
    plot_data = model.dropna(subset=["wto_export_index", "fixed_external_demand_index"]).copy()
    figure, axis = plt.subplots(figsize=(11, 6.2))
    axis.plot(plot_data["quarter"], plot_data["wto_export_index"], label="China merchandise export volume")
    axis.plot(plot_data["quarter"], plot_data["fixed_external_demand_index"], label="Selected-market external demand")
    axis.axhline(100.0, linewidth=0.8, linestyle="--")
    axis.set_title("China Export Volume and Selected-Market External Demand")
    axis.set_xlabel("Quarter")
    axis.set_ylabel("Index, 2005Q1 = 100")
    step = max(1, len(plot_data) // 12)
    axis.set_xticks(plot_data["quarter"].iloc[::step])
    axis.tick_params(axis="x", rotation=45)
    axis.legend(frameon=False)
    figure.tight_layout()
    figure.savefig(path, dpi=300)
    plt.close(figure)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dl = DownloadSession()

    exports, imports = download_wto_data(dl)
    mirror_panel, mirror_audit = download_mirror_import_panel(dl)
    reconstructed_fixed, rolling_weights, weight_audit = build_weights(mirror_panel)
    reer = download_reer(dl)
    model_data, coverage = prepare_model_data(exports, imports, rolling_weights, reer)
    results, summaries = estimate_models(model_data)
    table2 = make_table2(results) if not results.empty else pd.DataFrame()

    exports.to_csv(OUTPUT_DIR / "wto_china_export_volume.csv", index=False, encoding="utf-8-sig")
    imports.to_csv(OUTPUT_DIR / "wto_partner_import_volume.csv", index=False, encoding="utf-8-sig")
    mirror_panel.to_csv(OUTPUT_DIR / "mirror_imports_from_china_panel.csv", index=False, encoding="utf-8-sig")
    reconstructed_fixed.to_csv(OUTPUT_DIR / "reconstructed_fixed_weights_all_markets.csv", index=False, encoding="utf-8-sig")
    rolling_weights.to_csv(OUTPUT_DIR / "rolling_weights_top20_2005_2024.csv", index=False, encoding="utf-8-sig")
    reer.to_csv(OUTPUT_DIR / "china_reer_quarterly.csv", index=False, encoding="utf-8-sig")
    model_data.to_csv(OUTPUT_DIR / "v15_model_data.csv", index=False, encoding="utf-8-sig")
    coverage.to_csv(OUTPUT_DIR / "v15_coverage_audit.csv", index=False, encoding="utf-8-sig")
    results.to_csv(OUTPUT_DIR / "v15_export_models.csv", index=False, encoding="utf-8-sig")
    table2.to_csv(OUTPUT_DIR / "Table2_WTO_export_models.csv", index=False, encoding="utf-8-sig")
    save_figure(model_data, OUTPUT_DIR / "Figure1_WTO_Export_and_External_Demand.png")

    export_count = int(model_data["wto_export_index"].notna().sum())
    fixed_eligible = int(model_data["fixed_model_eligible"].sum())
    rolling_eligible = int(model_data["rolling_model_eligible"].sum())
    status = {
        "run_completed_utc": datetime.now(timezone.utc).isoformat(),
        "sample": f"{SAMPLE_START}-{SAMPLE_END}",
        "wto_export_observations": export_count,
        "wto_export_complete_80_quarters": export_count == 80,
        "wto_import_markets_requested": len(IMPORT_SERIES),
        "wto_import_markets_with_any_observation": int(imports.groupby("alpha3")["value"].count().gt(0).sum()),
        "fixed_weight_top20_sum": float(sum(FIXED_WEIGHTS.values())),
        "fixed_coverage_mean": float(model_data["fixed_coverage"].mean()),
        "fixed_coverage_min": float(model_data["fixed_coverage"].min()),
        "fixed_coverage_max": float(model_data["fixed_coverage"].max()),
        "rolling_coverage_mean": float(model_data["rolling_coverage"].mean()),
        "rolling_coverage_min": float(model_data["rolling_coverage"].min()),
        "rolling_coverage_max": float(model_data["rolling_coverage"].max()),
        "coverage_threshold": COVERAGE_THRESHOLD,
        "fixed_eligible_quarters": fixed_eligible,
        "rolling_eligible_quarters": rolling_eligible,
        "estimated_model_count": sum(item.get("status") == "estimated" for item in summaries),
        "weight_reconstruction": {**mirror_audit, **weight_audit},
        "interpretation_limits": [
            "Contemporaneous and lag coefficients are conditional associations, not causal elasticities.",
            "Missing partner observations are retained as missing and are never set to zero.",
            "The selected-market coverage threshold is applied before estimation.",
            "The GDP-channel model is not estimated in this run because the current task is the frozen WTO export equation.",
        ],
    }
    write_json(OUTPUT_DIR / "v15_model_summaries.json", summaries)
    write_json(OUTPUT_DIR / "v15_status.json", status)
    manifest = {
        "pipeline": "v15 WTO native-volume replacement and frozen export models",
        "python": sys.version,
        "platform": platform.platform(),
        "packages": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
            "statsmodels": statsmodels.__version__,
            "matplotlib": matplotlib.__version__,
            "requests": requests.__version__,
        },
        "downloads": [record.__dict__ for record in dl.records],
        "fixed_weights": FIXED_WEIGHTS,
        "wto_series": {
            "export": {"dataset": EXPORT_SERIES[0], "series": EXPORT_SERIES[1]},
            "imports": {code: series for code, (series, _) in IMPORT_SERIES.items()},
        },
        "model_summaries": summaries,
    }
    write_json(OUTPUT_DIR / "v15_manifest.json", manifest)

    print(json.dumps(status, ensure_ascii=False, indent=2))
    if export_count < 76:
        raise RuntimeError(f"China WTO export series has only {export_count} observations in the sample")
    if fixed_eligible < 40:
        raise RuntimeError(f"Only {fixed_eligible} quarters meet fixed-weight coverage threshold")
    if not any(item.get("status") == "estimated" for item in summaries):
        raise RuntimeError("No frozen v15 export model could be estimated")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
