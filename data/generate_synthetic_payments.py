#!/usr/bin/env python3
"""
Synthetic payment data generator for Anomaly Explainer.

Generates daily TPV and payment-count data (2022-01-01 → 2026-05-09) across:
  products       : regular_ach, check, two_day_ach, one_day_ach
  merchant_industry, merchant_size, payer_industry, payer_size, payer_tenure_bucket

Output tables (Postgres):
  payment_daily_tpv     — ~7 M rows fact table
  anomaly_ground_truth  — 6 injected events

See data/DATA_DESCRIPTION.md for full design rationale.
"""

import io
import os
from pathlib import Path
import numpy as np
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv
from itertools import product as cartesian
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).parent.parent / ".env")

np.random.seed(42)

# ── Date range ────────────────────────────────────────────────────────────────

START_DATE = date(2022, 1, 1)
END_DATE   = date(2026, 5, 9)   # today; partial-settlement window applies near this edge
TODAY      = END_DATE
DB_URL     = os.environ["DATABASE_URL"]

# ── Products ──────────────────────────────────────────────────────────────────
# base_tpv     : daily $ volume at Jan 1 2022 (before growth/seasonality)
# yoy_growth   : compound annual growth rate (negative = decline)
# settle_days  : settlement latency for is_complete / tpv_settled logic
# avg_pmt_size : $/payment at mid_market size; SMB × 0.55, enterprise × 2.5
# sigma        : log-normal noise std-dev

PRODUCTS = {
    "regular_ach": dict(base_tpv=2_500_000, yoy_growth= 0.15, settle_days= 7, avg_pmt_size=3_000, sigma=0.14),
    "check":       dict(base_tpv=1_250_000, yoy_growth=-0.08, settle_days=14, avg_pmt_size=8_000, sigma=0.18),
    "two_day_ach": dict(base_tpv=  750_000, yoy_growth= 0.45, settle_days= 7, avg_pmt_size=2_500, sigma=0.16),
    "one_day_ach": dict(base_tpv=  500_000, yoy_growth= 0.70, settle_days= 7, avg_pmt_size=5_000, sigma=0.20),
}

# ── Dimensions ────────────────────────────────────────────────────────────────

MERCHANT_INDUSTRIES  = ["ecommerce", "healthcare", "payroll_staffing", "professional_services", "retail"]
MERCHANT_SIZES       = ["smb", "mid_market", "enterprise"]
PAYER_INDUSTRIES     = ["tech", "healthcare", "retail_consumer", "manufacturing", "other"]
PAYER_SIZES          = ["smb", "mid_market", "enterprise"]
PAYER_TENURE_BUCKETS = ["new_0_3mo", "early_4_12mo", "growing_1_3yr", "established_3_5yr", "loyal_5plus_yr"]

MERCHANT_SIZE_PMT_MULTIPLIER = {"smb": 0.55, "mid_market": 1.0, "enterprise": 2.5}

# ── Dimension weight tables ───────────────────────────────────────────────────
# Each sub-list must sum to 1.0.  Combined weight for a row =
# w_mi × w_ms × w_pi × w_ps × w_pt  (which sums to 1 across all 1,125 combos).

DIM_WEIGHTS = {
    "regular_ach": {
        "merchant_industry": [0.20, 0.15, 0.30, 0.15, 0.20],
        "merchant_size":     [0.20, 0.40, 0.40],
        "payer_industry":    [0.20, 0.20, 0.25, 0.20, 0.15],
        "payer_size":        [0.25, 0.40, 0.35],
        "payer_tenure":      [0.08, 0.22, 0.30, 0.22, 0.18],
    },
    "check": {
        "merchant_industry": [0.10, 0.30, 0.10, 0.35, 0.15],
        "merchant_size":     [0.15, 0.35, 0.50],
        "payer_industry":    [0.10, 0.30, 0.20, 0.25, 0.15],
        "payer_size":        [0.15, 0.35, 0.50],
        "payer_tenure":      [0.05, 0.15, 0.27, 0.28, 0.25],
    },
    "two_day_ach": {
        "merchant_industry": [0.35, 0.05, 0.20, 0.15, 0.25],
        "merchant_size":     [0.35, 0.45, 0.20],
        "payer_industry":    [0.25, 0.15, 0.30, 0.15, 0.15],
        "payer_size":        [0.35, 0.40, 0.25],
        "payer_tenure":      [0.12, 0.25, 0.30, 0.20, 0.13],
    },
    "one_day_ach": {
        "merchant_industry": [0.40, 0.20, 0.05, 0.25, 0.10],
        "merchant_size":     [0.10, 0.35, 0.55],
        "payer_industry":    [0.30, 0.20, 0.20, 0.15, 0.15],
        "payer_size":        [0.15, 0.35, 0.50],
        "payer_tenure":      [0.15, 0.30, 0.30, 0.15, 0.10],
    },
}

# Payer tenure shift: as platform matures (2022→2026), long-tenure share grows.
# Applied as an additive delta to "payer_tenure" weights, linearly interpolated.
TENURE_DELTA_2022 = [ 0.00,  0.00,  0.00,  0.00,  0.00]
TENURE_DELTA_2026 = [-0.01, -0.04, -0.04,  0.03,  0.06]

# ── Seasonality ───────────────────────────────────────────────────────────────

MONTHLY_MULT = {
    1: 0.85, 2: 0.88, 3: 1.00, 4: 1.02,  5: 1.00, 6: 1.05,
    7: 0.92, 8: 0.95, 9: 1.05, 10: 1.08, 11: 1.12, 12: 1.15,
}

# Day-of-week multipliers (0=Monday … 6=Sunday).
# Tuesday is the heaviest B2B payment day; weekends are smoothed-but-low.
DOW_MULT = {
    "regular_ach": {0: 0.95, 1: 1.15, 2: 1.10, 3: 1.05, 4: 0.90, 5: 0.35, 6: 0.25},
    "check":       {0: 1.00, 1: 1.10, 2: 1.05, 3: 1.05, 4: 0.95, 5: 0.40, 6: 0.30},
    "two_day_ach": {0: 0.95, 1: 1.15, 2: 1.12, 3: 1.05, 4: 0.88, 5: 0.32, 6: 0.22},
    "one_day_ach": {0: 0.92, 1: 1.12, 2: 1.15, 3: 1.10, 4: 0.92, 5: 0.28, 6: 0.18},
}

# ── US Federal Holidays (2022-2026) ───────────────────────────────────────────

def _us_holidays(years):
    """Return set of federal holiday dates for the given years."""
    holidays = set()
    for y in years:
        # Fixed-date holidays (with observed adjustment skipped for simplicity)
        for mo, dy in [(1,1),(6,19),(7,4),(11,11),(12,25)]:
            holidays.add(date(y, mo, dy))

        # Floating holidays (nth weekday of month)
        def nth_weekday(year, month, weekday, n):
            """Return the nth occurrence of weekday (0=Mon) in given month."""
            first = date(year, month, 1)
            offset = (weekday - first.weekday()) % 7
            return first + timedelta(days=offset + 7 * (n - 1))

        def last_weekday(year, month, weekday):
            """Return the last occurrence of weekday in given month."""
            next_mo = date(year, month % 12 + 1, 1) if month < 12 else date(year + 1, 1, 1)
            last_day = next_mo - timedelta(days=1)
            offset = (last_day.weekday() - weekday) % 7
            return last_day - timedelta(days=offset)

        holidays.add(nth_weekday(y,  1, 0, 3))   # MLK Day — 3rd Mon Jan
        holidays.add(nth_weekday(y,  2, 0, 3))   # Presidents' Day — 3rd Mon Feb
        holidays.add(last_weekday(y, 5, 0))       # Memorial Day — last Mon May
        holidays.add(nth_weekday(y,  9, 0, 1))   # Labor Day — 1st Mon Sep
        holidays.add(nth_weekday(y, 10, 0, 2))   # Columbus Day — 2nd Mon Oct
        holidays.add(nth_weekday(y, 11, 3, 4))   # Thanksgiving — 4th Thu Nov
    return holidays

HOLIDAYS = _us_holidays(range(2022, 2027))

def _holiday_mult(d: date) -> float:
    """
    Holiday volume shaping:
      on holiday          → 0.30 (smoothed, not zero — pre-scheduled payments still run)
      day before holiday  → 0.85
      day after holiday   → 1.12 (catchup burst)
    """
    if d in HOLIDAYS:
        return 0.30
    if (d + timedelta(days=1)) in HOLIDAYS:
        return 0.85
    if (d - timedelta(days=1)) in HOLIDAYS:
        return 1.12
    return 1.00

# ── Anomaly Events ────────────────────────────────────────────────────────────
# Each effect: product, date_range (inclusive), optional dimension filters, multiplier.

def _daterange(start, end):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)

ANOMALY_EVENTS = [
    {
        "event_id": 1,
        "event_name": "svb_collapse_bank_flight_2023",
        "start_date": date(2023, 3, 10),
        "end_date":   date(2023, 3, 17),
        "direction": "mixed",
        "description": (
            "Silicon Valley Bank collapse (Mar 10 2023). Businesses rushed to move funds via "
            "check as ACH bank trust eroded. Check TPV +40%; Regular ACH -25%."
        ),
        "effects": [
            {"product": "check",       "filters": {},                         "mult": 1.40,
             "dates": set(_daterange(date(2023,3,10), date(2023,3,17)))},
            {"product": "regular_ach", "filters": {},                         "mult": 0.75,
             "dates": set(_daterange(date(2023,3,10), date(2023,3,17)))},
        ],
    },
    {
        "event_id": 2,
        "event_name": "nacha_processing_delay_2023",
        "start_date": date(2023, 9, 15),
        "end_date":   date(2023, 9, 19),
        "direction": "drop_then_spike",
        "description": (
            "NACHA network disruption (Sep 15-17 2023). Regular ACH and 2-day ACH dropped 45% "
            "for 3 days, followed by +30% catchup surge on Sep 18-19."
        ),
        "effects": [
            {"product": "regular_ach", "filters": {}, "mult": 0.55,
             "dates": {date(2023,9,15), date(2023,9,16), date(2023,9,17)}},
            {"product": "two_day_ach", "filters": {}, "mult": 0.55,
             "dates": {date(2023,9,15), date(2023,9,16), date(2023,9,17)}},
            {"product": "regular_ach", "filters": {}, "mult": 1.30,
             "dates": {date(2023,9,18), date(2023,9,19)}},
            {"product": "two_day_ach", "filters": {}, "mult": 1.30,
             "dates": {date(2023,9,18), date(2023,9,19)}},
        ],
    },
    {
        "event_id": 3,
        "event_name": "ecommerce_enterprise_fraud_ring_2024",
        "start_date": date(2024, 2, 21),
        "end_date":   date(2024, 2, 25),
        "direction": "spike",
        "description": (
            "Fraudulent enterprise e-commerce merchant ring (Feb 21-25 2024). "
            "1-day ACH spiked +80% in ecommerce × enterprise segment. Fraud remediated Feb 26."
        ),
        "effects": [
            {"product": "one_day_ach",
             "filters": {"merchant_industry": "ecommerce", "merchant_size": "enterprise"},
             "mult": 1.80,
             "dates": set(_daterange(date(2024,2,21), date(2024,2,25)))},
        ],
    },
    {
        "event_id": 4,
        "event_name": "cyber_monday_surge_2024",
        "start_date": date(2024, 11, 25),
        "end_date":   date(2024, 11, 29),
        "direction": "spike",
        "description": (
            "Cyber Monday / Black Friday 2024. Retail-consumer payers and ecommerce merchants "
            "drove 35-45% spikes across ACH products."
        ),
        "effects": [
            {"product": "regular_ach", "filters": {"payer_industry": "retail_consumer"},
             "mult": 1.35, "dates": set(_daterange(date(2024,11,25), date(2024,11,29)))},
            {"product": "two_day_ach", "filters": {"payer_industry": "retail_consumer"},
             "mult": 1.40, "dates": set(_daterange(date(2024,11,25), date(2024,11,29)))},
            {"product": "one_day_ach", "filters": {"merchant_industry": "ecommerce"},
             "mult": 1.45, "dates": set(_daterange(date(2024,11,25), date(2024,11,29)))},
        ],
    },
    {
        "event_id": 5,
        "event_name": "platform_outage_one_day_ach_2025",
        "start_date": date(2025, 4, 2),
        "end_date":   date(2025, 4, 3),
        "direction": "drop",
        "description": (
            "Infrastructure outage (Apr 2-3 2025). Routing service failure dropped 1-day ACH "
            "by 65% across all dimensions. Resolved Apr 4."
        ),
        "effects": [
            {"product": "one_day_ach", "filters": {}, "mult": 0.35,
             "dates": {date(2025,4,2), date(2025,4,3)}},
        ],
    },
    {
        "event_id": 6,
        "event_name": "year_end_enterprise_rush_2025",
        "start_date": date(2025, 12, 22),
        "end_date":   date(2025, 12, 31),
        "direction": "spike",
        "description": (
            "Year-end enterprise payment rush (Dec 22-31 2025). Enterprise merchants accelerated "
            "vendor payments and payroll ahead of Jan 1. ACH and check products up 30-45% "
            "in the enterprise segment."
        ),
        "effects": [
            {"product": "regular_ach", "filters": {"merchant_size": "enterprise"}, "mult": 1.45,
             "dates": set(_daterange(date(2025,12,22), date(2025,12,31)))},
            {"product": "check",       "filters": {"merchant_size": "enterprise"}, "mult": 1.30,
             "dates": set(_daterange(date(2025,12,22), date(2025,12,31)))},
            {"product": "two_day_ach", "filters": {"merchant_size": "enterprise"}, "mult": 1.35,
             "dates": set(_daterange(date(2025,12,22), date(2025,12,31)))},
        ],
    },
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _growth_factor(d: date, yoy_rate: float) -> float:
    """Compound growth from START_DATE to d at the given annual rate."""
    years_elapsed = (d - START_DATE).days / 365.25
    return (1 + yoy_rate) ** years_elapsed


def _settlement_frac(days_old: int, settle_days: int) -> float:
    """
    Smooth cubic ramp: fraction of TPV visible 'days_old' days after scheduling.
    0 days old → ~5% settled; settle_days days old → 100%.
    """
    if days_old >= settle_days:
        return 1.0
    t = days_old / settle_days
    return 0.05 + 0.90 * (3 * t**2 - 2 * t**3)


def _build_dim_grid(product: str, progress_frac: float) -> pd.DataFrame:
    """
    Build a 1,125-row DataFrame (5×3×5×3×5) of dimension combos with combined weights.
    progress_frac ∈ [0,1] shifts the payer_tenure distribution toward the mature state.
    """
    w = DIM_WEIGHTS[product]

    # Time-interpolated tenure weights
    tenure_base = np.array(w["payer_tenure"])
    delta = np.array(TENURE_DELTA_2022) + progress_frac * (
        np.array(TENURE_DELTA_2026) - np.array(TENURE_DELTA_2022)
    )
    tenure_w = np.clip(tenure_base + delta, 0.001, None)
    tenure_w = tenure_w / tenure_w.sum()  # re-normalise after delta

    combos = list(cartesian(
        zip(MERCHANT_INDUSTRIES,  w["merchant_industry"]),
        zip(MERCHANT_SIZES,       w["merchant_size"]),
        zip(PAYER_INDUSTRIES,     w["payer_industry"]),
        zip(PAYER_SIZES,          w["payer_size"]),
        zip(PAYER_TENURE_BUCKETS, tenure_w),
    ))

    rows = []
    for (mi, wmi), (ms, wms), (pi, wpi), (ps, wps), (pt, wpt) in combos:
        rows.append({
            "merchant_industry":  mi,
            "merchant_size":      ms,
            "payer_industry":     pi,
            "payer_size":         ps,
            "payer_tenure_bucket": pt,
            "dim_weight": wmi * wms * wpi * wps * wpt,
        })

    return pd.DataFrame(rows)


def _anomaly_mult(d: date, product: str, row_filters: dict) -> float:
    """Return the combined anomaly multiplier for a given (date, product, dimension dict)."""
    mult = 1.0
    for event in ANOMALY_EVENTS:
        for effect in event["effects"]:
            if effect["product"] != product:
                continue
            if d not in effect["dates"]:
                continue
            filters = effect["filters"]
            if all(row_filters.get(k) == v for k, v in filters.items()):
                mult *= effect["mult"]
    return mult

# ── Main generation ───────────────────────────────────────────────────────────

def generate_product(product: str, cfg: dict) -> pd.DataFrame:
    """Generate the full daily-dimensional DataFrame for one product."""
    print(f"  Generating {product}...")

    dates = pd.date_range(start=str(START_DATE), end=str(END_DATE), freq="D")
    n_dates = len(dates)
    total_days = (END_DATE - START_DATE).days

    # ── Date-level scalars ────────────────────────────────────────────────────
    date_rows = []
    for ts in dates:
        d = ts.date()
        days_elapsed = (d - START_DATE).days
        progress   = days_elapsed / total_days      # 0 → 1 over the full window
        days_old   = (TODAY - d).days

        base = (
            cfg["base_tpv"]
            * _growth_factor(d, cfg["yoy_growth"])
            * MONTHLY_MULT[d.month]
            * DOW_MULT[product][d.weekday()]
            * _holiday_mult(d)
        )
        sfrac      = _settlement_frac(days_old, cfg["settle_days"])
        is_complete = days_old >= cfg["settle_days"]

        date_rows.append({
            "date":        d,
            "progress":    progress,
            "base_tpv":    base,
            "sfrac":       sfrac,
            "is_complete": is_complete,
            "days_old":    days_old,
        })

    date_df = pd.DataFrame(date_rows)

    # ── Expand across dimension grid ─────────────────────────────────────────
    # We use a representative progress value (midpoint) for the dim-grid weight
    # then apply the per-row dim_weight to base_tpv.
    mid_progress = float(date_df["progress"].median())
    dim_df = _build_dim_grid(product, mid_progress)

    # Cross-join: date_df (n_dates rows) × dim_df (1125 rows)
    date_df["_key"] = 0
    dim_df["_key"]  = 0
    full = date_df.merge(dim_df, on="_key").drop(columns=["_key"])

    # ── Noise (log-normal, unique per row) ───────────────────────────────────
    noise = np.random.lognormal(mean=0.0, sigma=cfg["sigma"], size=len(full))

    # ── Anomaly multipliers ───────────────────────────────────────────────────
    print(f"    Applying anomaly multipliers...")
    anm = np.ones(len(full), dtype=np.float64)
    for event in ANOMALY_EVENTS:
        for effect in event["effects"]:
            if effect["product"] != product:
                continue
            date_mask = full["date"].isin(effect["dates"])
            if not date_mask.any():
                continue
            filters = effect["filters"]
            dim_mask = pd.Series(True, index=full.index)
            for col, val in filters.items():
                dim_mask &= (full[col] == val)
            combined_mask = date_mask & dim_mask
            anm[combined_mask.values] *= effect["mult"]

    # ── Compute TPV and payment counts ───────────────────────────────────────
    tpv_sched = full["base_tpv"] * full["dim_weight"] * noise * anm

    # Payment size scaled by merchant size
    pmt_size_mult = full["merchant_size"].map(MERCHANT_SIZE_PMT_MULTIPLIER)
    avg_size       = cfg["avg_pmt_size"] * pmt_size_mult
    pmt_noise      = np.random.lognormal(0, 0.10, size=len(full))
    pmt_count      = np.maximum(1, (tpv_sched / avg_size * pmt_noise).round().astype(int))

    tpv_settled    = tpv_sched * full["sfrac"]
    pmt_settled    = np.maximum(0, (pmt_count * full["sfrac"]).round().astype(int))

    full["product"]               = product
    full["tpv_scheduled"]         = tpv_sched.round(2)
    full["payment_count_scheduled"] = pmt_count
    full["tpv_settled"]           = tpv_settled.round(2)
    full["payment_count_settled"] = pmt_settled
    full["is_complete"]           = full["is_complete"]
    full["settlement_delay_days"] = cfg["settle_days"]

    keep = [
        "date", "product",
        "merchant_industry", "merchant_size",
        "payer_industry", "payer_size", "payer_tenure_bucket",
        "tpv_scheduled", "payment_count_scheduled",
        "tpv_settled",   "payment_count_settled",
        "is_complete", "settlement_delay_days",
    ]
    return full[keep]


def _pg_copy(df: pd.DataFrame, engine, table: str) -> None:
    """Load a DataFrame into Postgres using COPY FROM STDIN (CSV).
    ~20-50× faster than to_sql with multi-row INSERT for large frames."""
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, na_rep="")
    buf.seek(0)
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cols = ", ".join(df.columns)
        cur.copy_expert(
            f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT CSV, NULL '')",
            buf,
        )
        raw.commit()
    finally:
        raw.close()


def load_ground_truth(engine):
    records = []
    for ev in ANOMALY_EVENTS:
        all_products = list({e["product"] for e in ev["effects"]})
        all_dims     = list({k for e in ev["effects"] for k in e["filters"]})
        records.append({
            "event_id":            ev["event_id"],
            "event_name":          ev["event_name"],
            "start_date":          ev["start_date"],
            "end_date":            ev["end_date"],
            "affected_products":   ", ".join(sorted(all_products)),
            "affected_dimensions": ", ".join(sorted(all_dims)) if all_dims else "all",
            "direction":           ev["direction"],
            "description":         ev["description"],
        })
    gt = pd.DataFrame(records)
    gt.to_sql("anomaly_ground_truth", engine, if_exists="replace", index=False)
    print(f"  Loaded anomaly_ground_truth ({len(gt)} events)")


def main():
    engine = create_engine(DB_URL)

    # Drop and recreate target table
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS payment_daily_tpv"))
        conn.execute(text("DROP TABLE IF EXISTS anomaly_ground_truth"))
        conn.execute(text("""
            CREATE TABLE payment_daily_tpv (
                date                     DATE,
                product                  VARCHAR(20),
                merchant_industry        VARCHAR(30),
                merchant_size            VARCHAR(20),
                payer_industry           VARCHAR(30),
                payer_size               VARCHAR(20),
                payer_tenure_bucket      VARCHAR(20),
                tpv_scheduled            NUMERIC(15,2),
                payment_count_scheduled  INTEGER,
                tpv_settled              NUMERIC(15,2),
                payment_count_settled    INTEGER,
                is_complete              BOOLEAN,
                settlement_delay_days    INTEGER
            )
        """))
    print("Tables created.")

    for product, cfg in PRODUCTS.items():
        df = generate_product(product, cfg)
        print(f"    Writing {len(df):,} rows to Postgres...", end=" ", flush=True)
        _pg_copy(df, engine, "payment_daily_tpv")
        print("done.")

    print("Loading ground truth...")
    load_ground_truth(engine)

    # Add indexes for fast query performance
    with engine.begin() as conn:
        conn.execute(text("CREATE INDEX ON payment_daily_tpv (date)"))
        conn.execute(text("CREATE INDEX ON payment_daily_tpv (product, date)"))
        conn.execute(text("CREATE INDEX ON payment_daily_tpv (merchant_industry)"))
        conn.execute(text("CREATE INDEX ON payment_daily_tpv (payer_tenure_bucket)"))
    print("Indexes created.")
    print("\nDone. Run verify.py to confirm.")


if __name__ == "__main__":
    main()
