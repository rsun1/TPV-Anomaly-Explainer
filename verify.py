"""
Verification script for the synthetic payment dataset.
Confirms row counts, date range, TPV scale, settlement logic,
and that all 6 anomaly events are loaded.
"""

import os
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).parent / ".env")
engine = create_engine(os.environ["DATABASE_URL"])

def run(label, sql):
    df = pd.read_sql(text(sql), engine)
    print(f"\n── {label} ─────────────────────────────────────")
    print(df.to_string(index=False))
    return df

# ── Basic shape ───────────────────────────────────────────────────────────────
run("Row count by product",
    """
    SELECT product,
           COUNT(*) AS rows,
           MIN(date) AS first_date,
           MAX(date) AS last_date
    FROM payment_daily_tpv
    GROUP BY product
    ORDER BY product
    """)

# ── TPV scale check ───────────────────────────────────────────────────────────
run("Daily platform TPV (complete data, first and last month averages)",
    """
    SELECT
        CASE WHEN date < '2022-02-01' THEN 'Jan 2022 (launch)'
             WHEN date > '2026-04-01' THEN 'Apr 2026 (recent)'
        END AS period,
        ROUND(SUM(tpv_scheduled) / COUNT(DISTINCT date), 0) AS avg_daily_tpv
    FROM payment_daily_tpv
    WHERE is_complete = TRUE
      AND (date < '2022-02-01' OR date > '2026-04-01')
    GROUP BY 1
    ORDER BY 1
    """)

# ── Product TPV trend ─────────────────────────────────────────────────────────
run("Annual TPV by product (complete rows, $M)",
    """
    SELECT EXTRACT(YEAR FROM date)::int AS year,
           product,
           ROUND(SUM(tpv_scheduled) / 1e6, 1) AS tpv_millions
    FROM payment_daily_tpv
    WHERE is_complete = TRUE
    GROUP BY 1, 2
    ORDER BY 1, 2
    """)

# ── Settlement logic ──────────────────────────────────────────────────────────
run("Recent dates — partial settlement (last 7 days)",
    """
    SELECT date, product,
           ROUND(AVG(tpv_settled / NULLIF(tpv_scheduled,0)) * 100, 1) AS pct_settled,
           BOOL_AND(is_complete) AS all_complete
    FROM payment_daily_tpv
    WHERE date >= CURRENT_DATE - INTERVAL '7 days'
    GROUP BY date, product
    ORDER BY date DESC, product
    LIMIT 20
    """)

# ── Day-of-week pattern ───────────────────────────────────────────────────────
run("Day-of-week TPV index (Tue=1.0 baseline, regular_ach, complete data)",
    """
    WITH tue AS (
        SELECT AVG(daily_tpv) AS tue_avg
        FROM (
            SELECT date, SUM(tpv_scheduled) AS daily_tpv
            FROM payment_daily_tpv
            WHERE product = 'regular_ach' AND is_complete = TRUE
              AND EXTRACT(DOW FROM date) = 2
            GROUP BY date
        ) t
    )
    SELECT
        TO_CHAR(DATE '2024-01-01' + (n || ' days')::interval, 'Day') AS day_name,
        ROUND(AVG(daily_tpv) / (SELECT tue_avg FROM tue), 2) AS tpv_index
    FROM (
        SELECT EXTRACT(DOW FROM date)::int AS n,
               SUM(tpv_scheduled) AS daily_tpv
        FROM payment_daily_tpv
        WHERE product = 'regular_ach' AND is_complete = TRUE
        GROUP BY date
    ) d
    GROUP BY n
    ORDER BY n
    """)

# ── Monthly seasonality ───────────────────────────────────────────────────────
run("Monthly TPV index (all products, complete data)",
    """
    WITH base AS (
        SELECT date, SUM(tpv_scheduled) AS daily_tpv
        FROM payment_daily_tpv
        WHERE is_complete = TRUE
        GROUP BY date
    ),
    mar AS (SELECT AVG(daily_tpv) AS mar_avg FROM base WHERE EXTRACT(MONTH FROM date) = 3)
    SELECT EXTRACT(MONTH FROM date)::int AS month,
           TO_CHAR(date, 'Month') AS month_name,
           ROUND(AVG(b.daily_tpv) / (SELECT mar_avg FROM mar), 3) AS tpv_index
    FROM base b
    GROUP BY 1, 2
    ORDER BY 1
    """)

# ── Anomaly ground truth ──────────────────────────────────────────────────────
run("Anomaly ground truth events",
    """
    SELECT event_id, event_name, start_date, end_date,
           affected_products, direction
    FROM anomaly_ground_truth
    ORDER BY start_date
    """)

# ── Spot-check anomaly 2 (NACHA outage) ──────────────────────────────────────
run("Anomaly 2 spot-check: NACHA outage (regular_ach daily TPV around Sep 15 2023)",
    """
    SELECT date,
           ROUND(SUM(tpv_scheduled)) AS daily_tpv,
           ROUND(SUM(tpv_scheduled) / AVG(avg_7d), 2) AS vs_7d_avg
    FROM (
        SELECT date, tpv_scheduled,
               AVG(SUM(tpv_scheduled)) OVER (
                   PARTITION BY product
                   ORDER BY date
                   ROWS BETWEEN 14 PRECEDING AND 8 PRECEDING
               ) AS avg_7d
        FROM payment_daily_tpv
        WHERE product = 'regular_ach' AND is_complete = TRUE
        GROUP BY date, tpv_scheduled, product
    ) sub
    WHERE date BETWEEN '2023-09-08' AND '2023-09-22'
    GROUP BY date
    ORDER BY date
    """)

print("\n\nVerification complete.")
