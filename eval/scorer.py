"""
eval/scorer.py — the ONLY file in this project that reads anomaly_ground_truth.

Scores the full pipeline on two dimensions:

  1. Detection recall  — did Prophet flag days inside every ground-truth window?
  2. Attribution accuracy — did the decomposer identify the correct dimension(s)?

Detection results are cached to eval/detection_cache.json so Prophet doesn't
re-fit on every run (~5 min). Pass --fresh to re-run detection from scratch.

Usage:
    python -m eval.scorer           # use cached detection if available
    python -m eval.scorer --fresh   # re-run Prophet, overwrite cache
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

# ── Project imports ────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from detection.prophet_model import build_holidays_df, detect
from decomposition.segment_decomposer import decompose

DB_URL     = "postgresql://postgres:olist123@localhost:5432/transactions"
CACHE_PATH = Path(__file__).parent / "detection_cache.json"

# How many days past an event's end we'll still call it "detected"
DETECTION_TOLERANCE = 2


# ── Ground truth ───────────────────────────────────────────────────────────────

def load_ground_truth(engine) -> pd.DataFrame:
    """
    Read the label table. This is the one place in the project that is
    allowed to do this — all other modules query payment_daily_tpv only.
    """
    df = pd.read_sql("SELECT * FROM anomaly_ground_truth ORDER BY start_date", engine)
    df["start_date"] = pd.to_datetime(df["start_date"]).dt.date
    df["end_date"]   = pd.to_datetime(df["end_date"]).dt.date
    return df


# ── Detection cache ────────────────────────────────────────────────────────────

def _results_to_dict(df: pd.DataFrame) -> list[dict]:
    """Serialize a detection results DataFrame to JSON-safe format."""
    out = df.copy()
    out["ds"] = out["ds"].astype(str)
    records = out[["ds", "actual", "predicted", "z_score", "is_anomaly", "anomaly_direction"]].to_dict(orient="records")
    # Replace NaN (from rolling warm-up period) with None for valid JSON.
    return [{k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()} for r in records]


def _dict_to_results(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    df["ds"] = pd.to_datetime(df["ds"])
    return df


def run_detection(engine, holidays_df, fresh: bool = False) -> dict[str, pd.DataFrame]:
    """
    Run Prophet for all products. Returns {product: results_df}.
    Caches to detection_cache.json; loads from cache unless fresh=True.
    """
    if not fresh and CACHE_PATH.exists():
        print(f"Loading cached detection results from {CACHE_PATH.name} ...")
        with open(CACHE_PATH) as f:
            raw = json.load(f)
        return {product: _dict_to_results(records) for product, records in raw.items()}

    print("Running Prophet detection (this takes ~5 minutes) ...")
    products   = ["regular_ach", "check", "two_day_ach", "one_day_ach"]
    all_results: dict[str, pd.DataFrame] = {}
    for product in products:
        print(f"  Fitting {product} ...", end=" ", flush=True)
        results, _ = detect(product, engine, holidays_df, z_threshold=2.5)
        n = results["is_anomaly"].sum()
        print(f"{n} anomalous days")
        all_results[product] = results

    cache = {p: _results_to_dict(df) for p, df in all_results.items()}
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)
    print(f"Cached to {CACHE_PATH.name}\n")
    return all_results


# ── Window grouping ────────────────────────────────────────────────────────────

def _group_flagged_days(results: pd.DataFrame, gap_days: int = 3) -> list[dict]:
    """
    Collapse consecutive (or near-consecutive) anomalous days into windows.
    Two flags are merged into one window if they are within gap_days of each other.
    """
    flagged = results[results["is_anomaly"]].copy()
    flagged["date"] = flagged["ds"].dt.date
    flagged = flagged.sort_values("date")

    windows: list[dict] = []
    for _, row in flagged.iterrows():
        d = row["date"]
        if windows and (d - windows[-1]["end"]).days <= gap_days:
            windows[-1]["end"]   = d
            windows[-1]["peak_z"] = max(windows[-1]["peak_z"], abs(row["z_score"]))
        else:
            windows.append({
                "start":  d,
                "end":    d,
                "peak_z": abs(row["z_score"]),
            })
    return windows


# ── Detection scoring ──────────────────────────────────────────────────────────

def _overlaps(window: dict, gt_start: date, gt_end: date, tolerance: int) -> bool:
    """True if a detected window overlaps with the ground-truth range (+ tolerance)."""
    w_start = window["start"] - timedelta(days=tolerance)
    w_end   = window["end"]   + timedelta(days=tolerance)
    return w_start <= gt_end and w_end >= gt_start


def score_detection(
    gt_start: date,
    gt_end: date,
    detected_windows: list[dict],
) -> tuple[bool, int | None, float | None]:
    """
    Returns (detected, lag_days, peak_z).
    lag_days = calendar days from gt_start to the first flagged day inside the window.
    """
    for w in detected_windows:
        if _overlaps(w, gt_start, gt_end, DETECTION_TOLERANCE):
            first_flag = w["start"]
            lag = max(0, (first_flag - gt_start).days)
            return True, lag, w["peak_z"]
    return False, None, None


# ── Attribution scoring ────────────────────────────────────────────────────────

def score_attribution(
    gt_start: date,
    gt_end: date,
    product: str,
    gt_dimensions: str,
    engine,
) -> tuple[str, bool]:
    """
    Run the decomposer (blind — no ground truth access inside decompose()),
    find the dimension that explains the most total delta, and compare to
    the ground-truth key dimension(s).

    Returns (top_dimension_name, attribution_correct).
    """
    decomp = decompose(
        product=product,
        anomaly_start=gt_start,
        anomaly_end=gt_end,
        baseline_days=30,
        engine=engine,
    )

    # Sum |delta| across all segments per dimension
    dim_totals: dict[str, float] = {}
    for dim, segments in decomp["by_dimension"].items():
        dim_totals[dim] = sum(abs(s["delta_daily_tpv"]) for s in segments)

    top_dim = max(dim_totals, key=lambda d: dim_totals[d])

    if gt_dimensions.strip().lower() == "all":
        # Systemic event: no specific dimension is correct.
        # Check for uniform distribution: no single dimension explains >50% of total.
        total = sum(dim_totals.values())
        max_share = max(dim_totals.values()) / total if total else 0
        uniform = max_share < 0.50
        return top_dim, uniform
    else:
        gt_dims = {d.strip() for d in gt_dimensions.split(",")}
        return top_dim, top_dim in gt_dims


# ── Report printer ─────────────────────────────────────────────────────────────

def _fmt(b: bool) -> str:
    return "PASS" if b else "FAIL"


def print_report(rows: list[dict]) -> None:
    print()
    print("=" * 72)
    print("  ANOMALY EXPLAINER  —  EVAL REPORT")
    print("=" * 72)

    # ── Detection ──────────────────────────────────────────────────────────────
    print("\nDETECTION  (did Prophet flag the right windows?)\n")
    detected_count = 0
    total_count    = 0
    for r in rows:
        tag = _fmt(r["detected"])
        lag = f"lag {r['lag_days']}d" if r["lag_days"] is not None else "not detected"
        z   = f"z={r['peak_z']:.1f}"  if r["peak_z"]   is not None else ""
        print(f"  [{tag}]  {r['event_name']:<42}  {r['product']:<14}  {lag}  {z}")
        detected_count += r["detected"]
        total_count    += 1

    recall = detected_count / total_count if total_count else 0
    print(f"\n  Recall: {detected_count}/{total_count} product-event pairs detected "
          f"({recall*100:.0f}%)")

    # ── False positives ────────────────────────────────────────────────────────
    print("\nFALSE POSITIVES  (flagged windows not matching any ground-truth event)\n")
    total_fp = sum(r["false_positive_count"] for r in rows if r.get("_first_for_product"))
    for r in rows:
        if r.get("_first_for_product"):
            fp = r["false_positive_count"]
            print(f"  {r['product']:<14}  {fp} false positive window(s)")
    print(f"\n  Total false positive windows: {total_fp}")
    if total_fp == 0:
        print("  Note: zero false positives can indicate an overly conservative threshold"
              " or over-fitted seasonality. Some FPs are normal on real data.")

    # ── Attribution ────────────────────────────────────────────────────────────
    print("\nATTRIBUTION  (did decompose() identify the right dimension?)\n")
    detected_rows = [r for r in rows if r["detected"]]
    attr_correct  = sum(r["attribution_correct"] for r in detected_rows)
    for r in detected_rows:
        tag = _fmt(r["attribution_correct"])
        gt  = r["gt_dimensions"]
        top = r["top_dimension"]
        print(f"  [{tag}]  {r['event_name']:<42}  {r['product']:<14}"
              f"  top={top}  gt={gt}")

    attr_pct = attr_correct / len(detected_rows) * 100 if detected_rows else 0
    print(f"\n  Attribution accuracy: {attr_correct}/{len(detected_rows)} "
          f"({attr_pct:.0f}%)")

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\nSUMMARY\n")
    print(f"  Detection recall   : {recall*100:.0f}%  ({detected_count}/{total_count})")
    print(f"  False positive wdws: {total_fp}")
    print(f"  Attribution acc    : {attr_pct:.0f}%  ({attr_correct}/{len(detected_rows)} detected events)")
    print()
    print("  Anti-leakage audit:")
    print("  - detection/prophet_model.py   : reads payment_daily_tpv only")
    print("  - decomposition/               : reads payment_daily_tpv only")
    print("  - narrative/llm_synthesizer.py : reads decomposition dict only")
    print("  - anomaly_ground_truth         : read HERE (scorer.py) and nowhere else")
    print("=" * 72)


# ── Main ───────────────────────────────────────────────────────────────────────

def main(fresh: bool = False) -> None:
    engine      = create_engine(DB_URL)
    holidays_df = build_holidays_df()

    print("\nLoading ground truth ...")
    gt = load_ground_truth(engine)
    print(f"  {len(gt)} events\n")

    all_results = run_detection(engine, holidays_df, fresh=fresh)

    # Pre-compute detected windows per product (for false positive counting)
    all_windows: dict[str, list[dict]] = {
        product: _group_flagged_days(df)
        for product, df in all_results.items()
    }

    report_rows: list[dict] = []
    seen_products: set[str] = set()

    for _, gt_row in gt.iterrows():
        products = [p.strip() for p in gt_row["affected_products"].split(",")]

        for product in products:
            if product not in all_results:
                continue

            detected_windows = all_windows[product]
            detected, lag, peak_z = score_detection(
                gt_row["start_date"], gt_row["end_date"], detected_windows
            )

            top_dim, attr_ok = score_attribution(
                gt_row["start_date"], gt_row["end_date"],
                product, gt_row["affected_dimensions"], engine,
            ) if detected else (None, False)

            # Count false positives for this product (once per product)
            fp_count = 0
            if product not in seen_products:
                for w in detected_windows:
                    matched = any(
                        _overlaps(w, r["start_date"], r["end_date"], DETECTION_TOLERANCE)
                        for _, r in gt[gt["affected_products"].str.contains(product)].iterrows()
                    )
                    if not matched:
                        fp_count += 1

            report_rows.append({
                "event_name":          gt_row["event_name"],
                "product":             product,
                "gt_dimensions":       gt_row["affected_dimensions"],
                "detected":            detected,
                "lag_days":            lag,
                "peak_z":              peak_z,
                "top_dimension":       top_dim,
                "attribution_correct": attr_ok,
                "false_positive_count": fp_count,
                "_first_for_product":  product not in seen_products,
            })
            seen_products.add(product)

    print_report(report_rows)


if __name__ == "__main__":
    fresh = "--fresh" in sys.argv
    main(fresh=fresh)
