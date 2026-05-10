# Synthetic Payment Data — Description & Dictionary

> Reference document for the Anomaly Explainer synthetic dataset.
> All design decisions are recorded here so the anomaly detection and
> decomposition layers can be evaluated against known ground truth.

---

## Overview

| Attribute | Value |
|---|---|
| Date range | 2022-01-01 → 2026-05-09 |
| Granularity | Daily, per (product × 5 dimensions) |
| Row count | ~7.15 million rows |
| Total platform TPV at launch (Jan 2022) | ~$5 million / day |
| Total platform TPV at data end (May 2026) | ~$14.6 million / day |
| Postgres table | `payment_daily_tpv` |

---

## Products

| Product | Column value | Settlement delay | Jan 2022 base TPV/day | YoY growth |
|---|---|---|---|---|
| Regular ACH | `regular_ach` | 7 days | $2,500,000 | +15% |
| Check | `check` | 14 days | $1,250,000 | −8% (declining) |
| 2-Day ACH | `two_day_ach` | 7 days | $750,000 | +45% |
| 1-Day ACH | `one_day_ach` | 7 days | $500,000 | +70% (newest product) |

**Design rationale:**
- Regular ACH is the largest, most mature product — slow growth as it is already near-saturation.
- Check is declining: a realistic reflection of paper-check secular decline in B2B payments.
- 2-Day and 1-Day ACH are the growth engines; 1-Day ACH has the highest growth rate because it is the newest product and enterprises are actively migrating time-sensitive payments to it.
- Blended platform growth rate ≈ 28% YoY (compounded), consistent with a Series B → growth-stage fintech.

---

## Settlement Delay & Partial TPV

ACH products (regular, 2-day, 1-day) have a **7-day settlement delay**.
Check has a **14-day settlement delay**.

This models the real-world reality that payment processors do not see final, confirmed TPV
until the settlement window closes. The columns are:

| Column | Meaning |
|---|---|
| `tpv_scheduled` | Full TPV scheduled on that date (the "true" value if fully settled) |
| `tpv_settled` | TPV confirmed as settled as of `TODAY` (partial for recent dates) |
| `payment_count_scheduled` | Number of payments scheduled on that date |
| `payment_count_settled` | Payments confirmed settled |
| `is_complete` | `TRUE` if `(TODAY - date) >= settlement_delay_days` |
| `settlement_delay_days` | 7 (ACH) or 14 (check) |

**Partial settlement ramp:** uses a smooth cubic curve.
At `t = days_old / settle_days`:
```
tpv_settled = tpv_scheduled × [0.05 + 0.90 × (3t² − 2t³)]
```
This gives ~5% settled on day 0, ~50% at the halfway mark, and ~100% at the settle deadline.

**Implication for anomaly detection:**
Only use rows where `is_complete = TRUE` for baseline modeling.
The most recent 7 days (ACH) or 14 days (check) are partially observed and should be
treated as right-censored when fitting statsforecast models.

---

## Dimensions

### merchant_industry
The industry vertical of the merchant (payment recipient):

| Value | Description |
|---|---|
| `ecommerce` | Online retail platforms |
| `healthcare` | Medical billing, insurance payors |
| `payroll_staffing` | Payroll processors, staffing agencies |
| `professional_services` | Law firms, accounting, consulting |
| `retail` | Brick-and-mortar retail chains |

### merchant_size
Size of the merchant by annual TPV processed through the platform:

| Value | Annual TPV range | Avg payment size multiplier |
|---|---|---|
| `smb` | < $1M | 0.55× baseline |
| `mid_market` | $1M – $50M | 1.0× baseline |
| `enterprise` | > $50M | 2.5× baseline |

### payer_industry
The industry vertical of the payer (payment sender):

| Value | Description |
|---|---|
| `tech` | Software, SaaS, hardware companies |
| `healthcare` | Hospitals, insurers, pharma |
| `retail_consumer` | Consumer-facing retail companies |
| `manufacturing` | Industrial manufacturers, distributors |
| `other` | Catch-all for uncategorized payers |

### payer_size
Size of the payer entity (mirrors merchant_size tiers):

| Value | Description |
|---|---|
| `smb` | Small business payers |
| `mid_market` | Mid-sized companies |
| `enterprise` | Large enterprise payers |

### payer_tenure_bucket
Number of months the payer has been a platform customer, bucketed:

| Value | Tenure range | Notes |
|---|---|---|
| `new_0_3mo` | 0–3 months | Onboarding cohort; higher churn risk |
| `early_4_12mo` | 4–12 months | Ramping usage |
| `growing_1_3yr` | 13–36 months | Established relationship |
| `established_3_5yr` | 37–60 months | High-value, stable |
| `loyal_5plus_yr` | 61–120 months | Most tenured (max 10 years) |

**Time-varying distribution:** As the platform matures from 2022 to 2026, the share of
long-tenure payers (`established_3_5yr`, `loyal_5plus_yr`) grows modestly (+3% and +6%
respectively), while the shorter-tenure buckets shrink. This reflects a realistic platform
that is converting early adopters into long-term customers.

---

## Day-of-Week Patterns

B2B payment volume follows a Tuesday-peak pattern. Design rationale: most businesses
schedule vendor payments and payroll runs mid-week (Tuesday–Thursday) to hit
Wednesday–Friday bank settlement windows. Fridays are lighter as businesses defer
non-urgent payments to avoid weekend accumulation.

| Day | Regular ACH | Check | 2-Day ACH | 1-Day ACH |
|---|---|---|---|---|
| Monday | 0.95× | 1.00× | 0.95× | 0.92× |
| **Tuesday** | **1.15×** | **1.10×** | **1.15×** | 1.12× |
| Wednesday | 1.10× | 1.05× | 1.12× | **1.15×** |
| Thursday | 1.05× | 1.05× | 1.05× | 1.10× |
| Friday | 0.90× | 0.95× | 0.88× | 0.92× |
| Saturday | 0.35× | 0.40× | 0.32× | 0.28× |
| Sunday | 0.25× | 0.30× | 0.22× | 0.18× |

**Notes:**
- 1-Day ACH peaks on Wednesday because time-sensitive payments are initiated Wednesday to
  guarantee same-day-next-day settlement before the weekend.
- Check is less day-sensitive because physical processing pipelines are smoother.
- Weekends are non-zero (smoothed) rather than zero. Real payment platforms pre-schedule
  weekend transactions; some ACH batches run on Saturday mornings.

---

## Monthly Seasonality

| Month | Multiplier | Reason |
|---|---|---|
| January | 0.85× | Post-holiday slowdown; businesses reset budgets |
| February | 0.88× | Short month; budgets not yet fully deployed |
| March | 1.00× | Baseline |
| April | 1.02× | Q2 start; new budget cycle |
| May | 1.00× | Normal |
| June | 1.05× | Mid-year push; H1 vendor payments accelerate |
| July | 0.92× | Summer slowdown; reduced staffing at payers |
| August | 0.95× | Late summer; recovering toward Q3 close |
| September | 1.05× | Q3 close; end-of-quarter payment acceleration |
| October | 1.08× | Q4 ramp-up; budget spend acceleration |
| November | 1.12× | Pre-Thanksgiving vendor payments; holiday ramp |
| December | 1.15× | Year-end payments; largest month of the year |

---

## Holiday Effects

US Federal holidays are modeled with a **smoothed** (not zero) suppression,
plus before/after spillover:

| Timing | Multiplier | Reasoning |
|---|---|---|
| On holiday | 0.30× | Pre-scheduled payments still process; staff absent reduces initiations |
| Day before holiday | 0.85× | Businesses pull forward or defer payments |
| Day after holiday | 1.12× | Catchup burst from deferred payments |

**Holiday list (per year):** New Year's Day, MLK Day, Presidents' Day, Memorial Day,
Juneteenth, Independence Day, Labor Day, Columbus Day, Veterans Day, Thanksgiving, Christmas.

---

## Noise Model

Each row's final TPV is multiplied by a log-normal noise draw:

```
noise ~ LogNormal(μ=0, σ=product_sigma)
```

| Product | σ (sigma) | Rationale |
|---|---|---|
| regular_ach | 0.14 | Most stable; large volume averages out variance |
| check | 0.18 | Higher variance; large individual payments skew daily totals |
| two_day_ach | 0.16 | Moderate |
| one_day_ach | 0.20 | Highest variance; time-sensitive payments are lumpy |

Payment counts use an independent log-normal noise draw with σ=0.10.

---

## Average Payment Size by Product and Merchant Size

`avg_payment_size` is the baseline (mid_market). SMB and enterprise are scaled:

| Product | SMB | Mid-Market | Enterprise |
|---|---|---|---|
| regular_ach | ~$1,650 | $3,000 | ~$7,500 |
| check | ~$4,400 | $8,000 | ~$20,000 |
| two_day_ach | ~$1,375 | $2,500 | ~$6,250 |
| one_day_ach | ~$2,750 | $5,000 | ~$12,500 |

---

## Injected Anomaly Events (Ground Truth)

These are the 6 events seeded into the data for detection and attribution validation.
Stored in the `anomaly_ground_truth` Postgres table.

| # | Event | Date Range | Product(s) | Key Dimension | Direction | Magnitude |
|---|---|---|---|---|---|---|
| 1 | SVB Bank Collapse | 2023-03-10 → 2023-03-17 | check, regular_ach | all | mixed | Check +40%, ACH −25% |
| 2 | NACHA Processing Delay | 2023-09-15 → 2023-09-19 | regular_ach, two_day_ach | all | drop then spike | −45% (3 days), +30% (2 days) |
| 3 | E-commerce Enterprise Fraud | 2024-02-21 → 2024-02-25 | one_day_ach | ecommerce × enterprise | spike | +80% |
| 4 | Cyber Monday Surge | 2024-11-25 → 2024-11-29 | regular_ach, two_day_ach, one_day_ach | retail_consumer payers, ecommerce merchants | spike | +35–45% |
| 5 | Platform Outage (1-day ACH) | 2025-04-02 → 2025-04-03 | one_day_ach | all | drop | −65% |
| 6 | Year-End Enterprise Rush | 2025-12-22 → 2025-12-31 | regular_ach, check, two_day_ach | enterprise merchants | spike | +30–45% |

**Eval use:** The anomaly detection pipeline should flag these windows. The decomposition
layer (wise-pizza) should then identify the dimension(s) listed in "Key Dimension."
Attribution accuracy = did the system correctly name the dimension(s)?

---

## Schema Reference

```sql
-- Fact table
SELECT * FROM payment_daily_tpv LIMIT 5;

-- date                  DATE
-- product               VARCHAR(20)   -- regular_ach | check | two_day_ach | one_day_ach
-- merchant_industry     VARCHAR(30)
-- merchant_size         VARCHAR(20)   -- smb | mid_market | enterprise
-- payer_industry        VARCHAR(30)
-- payer_size            VARCHAR(20)   -- smb | mid_market | enterprise
-- payer_tenure_bucket   VARCHAR(20)
-- tpv_scheduled         NUMERIC(15,2) -- full day TPV if fully settled
-- payment_count_scheduled INTEGER
-- tpv_settled           NUMERIC(15,2) -- partial if is_complete = FALSE
-- payment_count_settled INTEGER
-- is_complete           BOOLEAN       -- TRUE when settlement window has elapsed
-- settlement_delay_days INTEGER       -- 7 (ACH) | 14 (check)

-- Ground truth
SELECT * FROM anomaly_ground_truth;
```

---

## Useful Queries

```sql
-- Daily total TPV by product (complete data only)
SELECT date, product, SUM(tpv_scheduled) AS daily_tpv
FROM payment_daily_tpv
WHERE is_complete = TRUE
GROUP BY date, product
ORDER BY date, product;

-- What you'd see in a real dashboard (settled view, includes partial)
SELECT date, product, SUM(tpv_settled) AS visible_tpv
FROM payment_daily_tpv
GROUP BY date, product
ORDER BY date, product;

-- Decompose a date range by merchant_industry for anomaly analysis
SELECT merchant_industry, SUM(tpv_scheduled) AS tpv,
       COUNT(*) AS payments
FROM payment_daily_tpv
WHERE date BETWEEN '2023-09-15' AND '2023-09-19'
  AND product IN ('regular_ach', 'two_day_ach')
  AND is_complete = TRUE
GROUP BY merchant_industry
ORDER BY tpv DESC;
```
