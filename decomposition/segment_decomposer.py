"""
Custom segment decomposition for payment TPV anomalies.

Given an anomaly window (product + date range), compares it against a baseline
window across all 5 dimensions plus their top pairwise interactions.

Why custom instead of wise-pizza:
  - Schema is known — no need for a library to search for dimensions
  - Pure SQL GROUP BY aggregations: transparent, fast, debuggable
  - Output is a clean dict that maps directly to the LLM prompt
  - No dependency risk from a niche, underactive library

Output structure is ready to JSON-serialize straight into the Claude API call.
anomaly_ground_truth is never read here — decomposition is fully blind to injected events.
"""

from __future__ import annotations
from itertools import combinations
from datetime import date, timedelta
import json
import pandas as pd
from sqlalchemy import create_engine, text

DB_URL = "postgresql://postgres:olist123@localhost:5432/transactions"

DIMENSIONS = [
    "merchant_industry",
    "merchant_size",
    "payer_industry",
    "payer_size",
    "payer_tenure_bucket",
]
DIMENSION_PAIRS = list(combinations(DIMENSIONS, 2))   # 10 pairs


# ── Data helpers ──────────────────────────────────────────────────────────────

def _load_window(
    engine,
    product: str,
    dims: list[str],
    start: date,
    end: date,
) -> pd.DataFrame:
    """
    Average daily TPV per segment combination for a given date window.
    Dividing by distinct days normalises for windows of different lengths.
    """
    dim_cols = ", ".join(dims)
    df = pd.read_sql(
        text(f"""
            SELECT {dim_cols},
                   SUM(tpv_scheduled)::float / COUNT(DISTINCT date) AS avg_daily_tpv
            FROM payment_daily_tpv
            WHERE product    = :product
              AND is_complete = TRUE
              AND date BETWEEN :start AND :end
            GROUP BY {dim_cols}
        """),
        engine,
        params={"product": product, "start": str(start), "end": str(end)},
    )
    return df


def _load_totals(engine, product: str, start: date, end: date) -> float:
    """Total average daily TPV for the product across the window."""
    row = pd.read_sql(
        text("""
            SELECT SUM(tpv_scheduled)::float / COUNT(DISTINCT date) AS avg_daily
            FROM payment_daily_tpv
            WHERE product    = :product
              AND is_complete = TRUE
              AND date BETWEEN :start AND :end
        """),
        engine,
        params={"product": product, "start": str(start), "end": str(end)},
    )
    return float(row["avg_daily"].iloc[0] or 0)


# ── Core decomposition ────────────────────────────────────────────────────────

def _decompose_dims(
    engine,
    product: str,
    dims: list[str],
    baseline: tuple[date, date],
    anomaly: tuple[date, date],
    total_delta: float,
) -> list[dict]:
    """
    For a set of dimensions (1 or 2), return segments ranked by |delta|.
    Each item: segment label, baseline daily TPV, anomaly daily TPV,
               delta, pct of total product delta, direction.
    """
    base_df  = _load_window(engine, product, dims, *baseline)
    anom_df  = _load_window(engine, product, dims, *anomaly)

    merged = base_df.merge(
        anom_df, on=dims, how="outer", suffixes=("_base", "_anom")
    ).fillna(0)

    merged["delta"] = merged["avg_daily_tpv_anom"] - merged["avg_daily_tpv_base"]
    merged["pct_of_total_delta"] = (
        (merged["delta"] / total_delta).round(4) if total_delta != 0 else 0.0
    )
    merged["direction"] = merged["delta"].apply(
        lambda x: "spike" if x > 0 else ("drop" if x < 0 else "flat")
    )

    # Human-readable segment label
    if len(dims) == 1:
        merged["segment"] = merged[dims[0]].astype(str)
    else:
        merged["segment"] = merged[dims].apply(
            lambda r: " × ".join(r.astype(str)), axis=1
        )

    merged = merged.sort_values("delta", key=abs, ascending=False)

    return [
        {
            "segment":            row["segment"],
            "baseline_daily_tpv": round(row["avg_daily_tpv_base"], 2),
            "anomaly_daily_tpv":  round(row["avg_daily_tpv_anom"], 2),
            "delta_daily_tpv":    round(row["delta"], 2),
            "pct_of_total_delta": float(row["pct_of_total_delta"]),
            "direction":          row["direction"],
        }
        for _, row in merged.iterrows()
    ]


# ── Public API ────────────────────────────────────────────────────────────────

def decompose(
    product: str,
    anomaly_start: date,
    anomaly_end: date,
    baseline_days: int = 30,
    top_segments: int = 5,
    top_interactions: int = 5,
    engine=None,
) -> dict:
    """
    Decompose a TPV anomaly across all dimensions and top pairwise interactions.

    Parameters
    ----------
    product          : one of regular_ach, check, two_day_ach, one_day_ach
    anomaly_start/end: the flagged date range (inclusive)
    baseline_days    : how many days before the anomaly to use as baseline
                       (a gap of 1 day is left so the anomaly does not contaminate it)
    top_segments     : how many segments to return per dimension
    top_interactions : how many pairwise interaction cells to return overall

    Returns
    -------
    dict with keys: product, anomaly_window, baseline_window, summary,
                    by_dimension, top_interactions
    Ready to JSON-serialize into the Claude API prompt.
    """
    if engine is None:
        engine = create_engine(DB_URL)

    baseline_end   = anomaly_start - timedelta(days=1)
    baseline_start = baseline_end  - timedelta(days=baseline_days - 1)

    # ── Summary ───────────────────────────────────────────────────────────────
    base_total = _load_totals(engine, product, baseline_start, baseline_end)
    anom_total = _load_totals(engine, product, anomaly_start, anomaly_end)
    total_delta = anom_total - base_total
    delta_pct   = (total_delta / base_total) if base_total else 0.0

    summary = {
        "baseline_daily_tpv": round(base_total, 2),
        "anomaly_daily_tpv":  round(anom_total, 2),
        "delta_daily_tpv":    round(total_delta, 2),
        "delta_pct":          round(delta_pct, 4),
        "direction":          "spike" if total_delta > 0 else "drop",
    }

    # ── 1-D decomposition across each dimension ────────────────────────────
    by_dimension = {}
    for dim in DIMENSIONS:
        segments = _decompose_dims(
            engine, product, [dim],
            (baseline_start, baseline_end),
            (anomaly_start, anomaly_end),
            total_delta,
        )
        by_dimension[dim] = segments[:top_segments]

    # ── 2-D interactions: top cell per pair, then rank all pairs ──────────
    interaction_rows = []
    for dim_a, dim_b in DIMENSION_PAIRS:
        segments = _decompose_dims(
            engine, product, [dim_a, dim_b],
            (baseline_start, baseline_end),
            (anomaly_start, anomaly_end),
            total_delta,
        )
        if segments:
            best = segments[0]   # highest |delta| cell for this pair
            interaction_rows.append({
                "dimensions": f"{dim_a} × {dim_b}",
                **best,
            })

    # Sort all pairs by |delta| of their best cell, return top N
    interaction_rows.sort(key=lambda r: abs(r["delta_daily_tpv"]), reverse=True)
    top_ix = interaction_rows[:top_interactions]

    return {
        "product": product,
        "anomaly_window": {
            "start": str(anomaly_start),
            "end":   str(anomaly_end),
            "days":  (anomaly_end - anomaly_start).days + 1,
        },
        "baseline_window": {
            "start": str(baseline_start),
            "end":   str(baseline_end),
            "days":  baseline_days,
        },
        "summary":          summary,
        "by_dimension":     by_dimension,
        "top_interactions": top_ix,
    }


def decompose_to_json(product: str, anomaly_start: date, anomaly_end: date, **kwargs) -> str:
    """Convenience wrapper — returns JSON string ready for the LLM prompt."""
    result = decompose(product, anomaly_start, anomaly_end, **kwargs)
    return json.dumps(result, indent=2, default=str)


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Smoke test against the NACHA outage event (event 2)
    print("Decomposing NACHA outage — regular_ach, Sep 15-17 2023\n")
    result = decompose(
        product="regular_ach",
        anomaly_start=date(2023, 9, 15),
        anomaly_end=date(2023, 9, 17),
        baseline_days=30,
    )

    print(f"Summary:")
    s = result["summary"]
    print(f"  Baseline daily TPV : ${s['baseline_daily_tpv']:>12,.0f}")
    print(f"  Anomaly  daily TPV : ${s['anomaly_daily_tpv']:>12,.0f}")
    print(f"  Delta              : ${s['delta_daily_tpv']:>12,.0f}  ({s['delta_pct']*100:.1f}%)")
    print(f"  Direction          : {s['direction']}\n")

    for dim, segs in result["by_dimension"].items():
        print(f"{dim}:")
        for seg in segs:
            arrow = "DROP" if seg["direction"] == "drop" else "SPIKE"
            print(f"  [{arrow}] {seg['segment']:<30}  delta ${seg['delta_daily_tpv']:>10,.0f}"
                  f"  ({seg['pct_of_total_delta']*100:+.1f}% of total)")
        print()

    print("Top 2-way interactions:")
    for ix in result["top_interactions"]:
        arrow = "DROP" if ix["direction"] == "drop" else "SPIKE"
        print(f"  [{arrow}] {ix['dimensions']}: {ix['segment']:<40}"
              f"  delta ${ix['delta_daily_tpv']:>10,.0f}  ({ix['pct_of_total_delta']*100:+.1f}%)")
