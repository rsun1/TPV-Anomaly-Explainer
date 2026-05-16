"""
Prophet-based anomaly detection for payment TPV.

For each product:
  1. Load daily aggregate TPV (is_complete = TRUE only — no partial settlement data)
  2. Fit Prophet with US holiday calendar, weekly + yearly seasonality
  3. Compare actuals to the forecast uncertainty interval
  4. Flag anomalies; compute residual z-scores for ranking severity

Prophet is the right choice here because it decomposes the signal into
trend + weekly seasonality + yearly seasonality + holiday effects, giving
the LLM narrative layer interpretable components to reason about.

The anomaly_ground_truth table is never read here — detection is fully blind
to injected events. That table is only used by eval/scorer.py.
"""

import json
import logging
import os
import warnings
import numpy as np
import pandas as pd
from datetime import date, timedelta
from pathlib import Path
from dotenv import load_dotenv
from prophet import Prophet
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).parent.parent / ".env")

logging.getLogger("prophet").setLevel(logging.WARNING)
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
warnings.filterwarnings("ignore")

DB_URL     = os.environ["DATABASE_URL"]
CACHE_PATH = Path(__file__).parent / "detection_cache.json"
PRODUCTS = ["regular_ach", "check", "two_day_ach", "one_day_ach"]


# ── Holiday calendar ──────────────────────────────────────────────────────────

def build_holidays_df() -> pd.DataFrame:
    """
    Prophet-compatible US federal holiday DataFrame (2021-2027 inclusive).

    lower_window / upper_window let Prophet model the day-before suppression
    and day-after catchup burst we built into the synthetic data — so the model
    learns these as expected patterns rather than flagging them as anomalies.
    """
    def _nth_weekday(y, month, weekday, n):
        first = date(y, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (n - 1))

    def _last_weekday(y, month, weekday):
        next_mo = date(y, month % 12 + 1, 1) if month < 12 else date(y + 1, 1, 1)
        last = next_mo - timedelta(days=1)
        return last - timedelta(days=(last.weekday() - weekday) % 7)

    rows = []
    for y in range(2021, 2027):
        # Fixed-date holidays
        for mo, dy, name in [
            (1,  1,  "new_years_day"),
            (6,  19, "juneteenth"),
            (7,  4,  "independence_day"),
            (11, 11, "veterans_day"),
            (12, 25, "christmas"),
        ]:
            rows.append({"holiday": name, "ds": date(y, mo, dy),
                         "lower_window": -1, "upper_window": 1})

        # Floating holidays
        for name, d in [
            ("mlk_day",        _nth_weekday(y, 1,  0, 3)),
            ("presidents_day", _nth_weekday(y, 2,  0, 3)),
            ("memorial_day",   _last_weekday(y, 5, 0)),
            ("labor_day",      _nth_weekday(y, 9,  0, 1)),
            ("columbus_day",   _nth_weekday(y, 10, 0, 2)),
            ("thanksgiving",   _nth_weekday(y, 11, 3, 4)),
        ]:
            rows.append({"holiday": name, "ds": d,
                         "lower_window": -1, "upper_window": 1})

    df = pd.DataFrame(rows)
    df["ds"] = pd.to_datetime(df["ds"])
    return df


# ── Data loading ──────────────────────────────────────────────────────────────

def load_series(product: str, engine) -> pd.DataFrame:
    """
    Daily aggregate TPV for one product, complete rows only.
    Returns Prophet-ready DataFrame: columns ds (datetime), y (float).
    """
    df = pd.read_sql(
        text("""
            SELECT date AS ds, SUM(tpv_scheduled) AS y
            FROM payment_daily_tpv
            WHERE product  = :product
              AND is_complete = TRUE
            GROUP BY date
            ORDER BY date
        """),
        engine,
        params={"product": product},
    )
    df["ds"] = pd.to_datetime(df["ds"])
    return df


# ── Model ─────────────────────────────────────────────────────────────────────

def build_model(holidays_df: pd.DataFrame) -> Prophet:
    """
    Construct (but do not fit) a Prophet model configured for B2B payment TPV.

    Seasonality notes:
      weekly_seasonality  — captures Tue/Wed peak, weekend suppression
      yearly_seasonality  — captures Nov/Dec surge, Jul/Aug slowdown
      holidays            — US federal holidays with ±1 day window effects

    Prior scales:
      seasonality_prior_scale = 10   stronger than default (5); our weekly pattern
                                     is pronounced so we want the model to fit it well
      holidays_prior_scale    = 10   same reasoning; holiday effects are strong
      changepoint_prior_scale = 0.05 default; flexible enough to track YoY growth
                                     without over-fitting to anomaly weeks
    """
    model = Prophet(
        growth="linear",
        yearly_seasonality=True,
        weekly_seasonality=True,
        daily_seasonality=False,
        holidays=holidays_df,
        interval_width=0.95,
        seasonality_mode="multiplicative",
        changepoint_prior_scale=0.05,
        # Lowered from 10.0 to the Prophet default (5.0): multiplicative mode
        # expresses seasonality as a fraction of the trend, so prior_scale=10
        # allows ±10x swings — far too flexible, causing seasonality overfitting
        # and inflated false positives.
        seasonality_prior_scale=5.0,
        # Lowered from 10.0 to 5.0 for the same reason: a barely-regularized
        # holiday effect could swing to an extreme negative multiplier, which
        # combined with a weekend effect drove predicted TPV below zero on
        # New Year's Day when it fell on a Sunday.
        holidays_prior_scale=5.0,
    )
    # B2B payments have strong month-end AP billing cycle spikes that neither
    # weekly nor yearly seasonality captures. fourier_order=2 fits a smooth
    # month-end ramp without overfitting mid-month noise.
    model.add_seasonality(name="monthly", period=30.5, fourier_order=2)
    return model


# ── Detection ─────────────────────────────────────────────────────────────────

def detect(
    product: str,
    engine,
    holidays_df: pd.DataFrame,
    z_threshold: float = 2.5,
) -> tuple[pd.DataFrame, Prophet]:
    """
    Fit Prophet on one product's TPV, flag anomalous days.

    Returns
    -------
    results : DataFrame with columns —
        ds, product, actual, predicted, lower, upper,
        residual, residual_pct, z_score, is_anomaly, anomaly_direction
    model : fitted Prophet instance (for component plots)
    """
    series = load_series(product, engine)
    model  = build_model(holidays_df)
    model.fit(series)

    forecast = model.predict(series[["ds"]])

    results = series.rename(columns={"y": "actual"}).merge(
        forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]],
        on="ds",
    ).rename(columns={
        "yhat":       "predicted",
        "yhat_lower": "lower",
        "yhat_upper": "upper",
    })

    # TPV cannot be negative. In multiplicative mode a strong holiday effect
    # stacked on a weekend effect can push the prediction below zero (e.g.
    # New Year's Day on a Sunday) — a negative prediction is always a model
    # artifact, so floor predictions and interval bounds at 0.
    for col in ["predicted", "lower", "upper"]:
        results[col] = results[col].clip(lower=0)

    results["residual"]     = results["actual"] - results["predicted"]
    # Divide by |predicted| so the sign stays meaningful even when Prophet
    # produces negative warm-up predictions in the first ~60 days.
    results["residual_pct"] = results["residual"] / results["predicted"].abs().clip(lower=1.0)

    # Rolling 90-day std of residuals for z-score.
    # Using a trailing window (not centered) so each day's z-score is only
    # informed by prior history — mimics real-time detection.
    rolling_std        = results["residual"].rolling(90, min_periods=30).std()
    results["z_score"] = results["residual"] / rolling_std.clip(lower=1.0)

    # Dual-gate anomaly flag: must breach Prophet's 95% prediction interval AND
    # exceed the z-score threshold. The interval gate eliminates days that score
    # z > 2.5 purely due to a tight rolling-std window but are still within the
    # model's expected uncertainty band.
    outside_interval          = (results["actual"] < results["lower"]) | (results["actual"] > results["upper"])
    results["is_anomaly"]     = outside_interval & (results["z_score"].abs() > z_threshold)

    results["is_anomaly"]        = results["z_score"].abs() > z_threshold
    results["anomaly_direction"] = None
    results.loc[results["is_anomaly"] & (results["z_score"] > 0), "anomaly_direction"] = "spike"
    results.loc[results["is_anomaly"] & (results["z_score"] < 0), "anomaly_direction"] = "drop"
    results["product"] = product

    return results[[
        "ds", "product", "actual", "predicted", "lower", "upper",
        "residual", "residual_pct", "z_score", "is_anomaly", "anomaly_direction",
    ]], model


def detect_all(
    z_threshold: float = 2.5,
) -> dict[str, tuple[pd.DataFrame, Prophet]]:
    """
    Run detection for all products.
    Returns {product: (results_df, fitted_model)}.
    """
    engine      = create_engine(DB_URL)
    holidays_df = build_holidays_df()
    output      = {}
    for product in PRODUCTS:
        print(f"  Fitting {product}...", end=" ", flush=True)
        results, model = detect(product, engine, holidays_df, z_threshold)
        n = results["is_anomaly"].sum()
        print(f"{n} anomalous days flagged")
        output[product] = (results, model)
    return output


# ── Cache helpers ────────────────────────────────────────────────────────────

def _results_to_dict(df: pd.DataFrame) -> list[dict]:
    out = df.copy()
    out["ds"] = out["ds"].astype(str)
    records = out[["ds", "actual", "predicted", "z_score", "is_anomaly", "anomaly_direction"]].to_dict(orient="records")
    return [{k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()} for r in records]


def _dict_to_results(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    df["ds"] = pd.to_datetime(df["ds"])
    return df


def load_or_detect_all(fresh: bool = False) -> dict[str, pd.DataFrame]:
    """
    Load cached detection results or refit Prophet for all products.
    Cache lives at detection/detection_cache.json; pass fresh=True to refit.
    """
    if not fresh and CACHE_PATH.exists():
        with open(CACHE_PATH) as f:
            raw = json.load(f)
        return {product: _dict_to_results(records) for product, records in raw.items()}

    print("Running Prophet detection (first run ~1 min) ...")
    engine      = create_engine(DB_URL)
    holidays_df = build_holidays_df()
    all_results: dict[str, pd.DataFrame] = {}
    for product in PRODUCTS:
        print(f"  Fitting {product} ...", end=" ", flush=True)
        results, _ = detect(product, engine, holidays_df)
        n = results["is_anomaly"].sum()
        print(f"{n} anomalous days")
        all_results[product] = results

    cache = {p: _results_to_dict(df) for p, df in all_results.items()}
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)
    print(f"Cached to {CACHE_PATH.name}\n")
    return all_results


# ── Window grouping ───────────────────────────────────────────────────────────

def group_flagged_days(
    results: pd.DataFrame,
    gap_days: int = 3,
    min_days: int = 2,
) -> list[dict]:
    """
    Collapse consecutive anomalous days into windows.
    Windows with fewer than min_days flagged days are dropped unless peak_z >= 3.0.
    Returns list of dicts with start, end, peak_z, n_days, direction.
    """
    flagged = results[results["is_anomaly"]].copy()
    flagged["date"] = flagged["ds"].dt.date
    flagged = flagged.sort_values("date")

    windows: list[dict] = []
    for _, row in flagged.iterrows():
        d = row["date"]
        z = abs(row["z_score"]) if row["z_score"] is not None else 0.0
        if windows and (d - windows[-1]["end"]).days <= gap_days:
            windows[-1]["end"]    = d
            windows[-1]["n_days"] += 1
            if z > windows[-1]["peak_z"]:
                windows[-1]["peak_z"]    = z
                windows[-1]["direction"] = row["anomaly_direction"] or "unknown"
        else:
            windows.append({
                "start":     d,
                "end":       d,
                "peak_z":    z,
                "n_days":    1,
                "direction": row["anomaly_direction"] or "unknown",
            })
    return [w for w in windows if w["n_days"] >= min_days or w["peak_z"] >= 3.0]


# ── Component decomposition ───────────────────────────────────────────────────

def get_components(product: str) -> tuple[Prophet, pd.DataFrame]:
    """
    Return (fitted_model, forecast_df) for a product.
    forecast_df includes trend, weekly, yearly, and holiday component columns,
    useful for the dashboard and LLM narrative layer.
    """
    engine      = create_engine(DB_URL)
    holidays_df = build_holidays_df()
    series      = load_series(product, engine)
    model       = build_model(holidays_df)
    model.fit(series)
    forecast = model.predict(series[["ds"]])
    return model, forecast


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running Prophet anomaly detection (z_threshold=3.0)...\n")
    all_results = detect_all(z_threshold=3.0)

    for product, (df, _) in all_results.items():
        anomalies = df[df["is_anomaly"]].copy()
        print(f"\n{'─'*60}")
        print(f"{product.upper()}  —  {len(anomalies)} flagged days")
        if not anomalies.empty:
            print(anomalies[[
                "ds", "actual", "predicted", "residual_pct", "z_score", "anomaly_direction"
            ]].assign(
                residual_pct=lambda x: (x["residual_pct"] * 100).round(1).astype(str) + "%",
                z_score=lambda x: x["z_score"].round(2),
            ).to_string(index=False))
