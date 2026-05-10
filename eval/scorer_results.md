## Results from scorer
- First run:Detection recall: 75% (9/12), False positive wdws: 55, Attribution acc: 78% (7/9 detected events)

## Results from narrative scorer
- First run (post fresh run of detection):
============================================================================
  ANOMALY EXPLAINER  —  NARRATIVE EVAL  (LLM-as-judge: claude-sonnet-4-6)
============================================================================

  Event                                   Product          Hyp  Evid   Dim   Cal   Act  Avg
  ──────────────────────────────────────────────────────────────────────────
  svb_collapse_bank_flight_2023           check              1     5     3     2     4  3.0
    The true root cause — SVB collapse triggering a systemic bank flight to check payments — is entirely absent from all four hypotheses, which instead focus on AP cycles, tax season, enterprise concentration, and settlement artifacts, missing the exogenous banking crisis event completely.
  nacha_processing_delay_2023             regular_ach        5     5     5     5     4  4.8
    The true root cause (systemic NACHA/ACH rail-level processing disruption) is correctly identified as hypothesis #1 with high confidence, supported by the uniform proportional drop across all dimensions as the key diagnostic signal.
  ecommerce_enterprise_fraud_ring_2024    one_day_ach        1     5     5     2     4  3.4
    The true root cause is an ecommerce enterprise fraud ring, but the AI narrative never mentions fraud as a primary hypothesis — it buries a brief fraud mention as the last bullet in 'Recommended Next Steps' rather than ranking it as a leading hypothesis, instead favoring merchant adoption/campaign surge as high-confidence hypothesis #1.
  cyber_monday_surge_2024                 one_day_ach        5     5     4     4     4  4.4
    The true root cause (Cyber Monday/Black Friday surge concentrated in ecommerce and tech payer industries) is correctly identified as hypothesis #1 with high confidence, directly matching the ground truth dimensions of merchant_industry and payer_industry during the exact anomaly window.
  platform_outage_one_day_ach_2025        one_day_ach        5     5     4     5     5  4.8
    The true root cause (platform/rail-level outage affecting all of one_day_ach) is correctly identified as hypothesis #1 with high confidence, supported by the observation that every segment dropped proportionally to its baseline share — the defining signature of a systemic event.
  year_end_enterprise_rush_2025           check              5     5     5     5     4  4.8
    The true root cause (year-end enterprise rush driving a check spike concentrated in merchant_size=enterprise) is correctly identified as hypothesis #1 with high confidence, matching the ground truth event name and dimension exactly.

AVERAGES
  hypothesis_accuracy           3.7  ███░░
  evidence_specificity          5.0  █████
  dimension_identification      4.3  ████░
  confidence_calibration        3.8  ███░░
  actionability                 4.2  ████░

  Overall average: 4.2 / 5.0