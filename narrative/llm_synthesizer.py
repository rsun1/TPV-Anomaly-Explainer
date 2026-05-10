"""
LLM narrative synthesis for payment TPV anomalies.

Takes a decomposition dict from segment_decomposer.decompose() and calls Claude
to produce a structured root cause narrative a VP of Finance could act on.

Design notes:
  - System prompt is prompt-cached (static schema + interpretation guide)
  - Uses claude-opus-4-7 with adaptive thinking for multi-step reasoning
  - Streams to avoid timeout; returns final text content only
  - Never reads anomaly_ground_truth — fully blind to injected events
"""

from __future__ import annotations

import json
import os
from datetime import date

import anthropic

MODEL = "claude-opus-4-7"

# ── Cached system prompt ───────────────────────────────────────────────────────
# All static context lives here so it is cached across calls.

_SYSTEM_PROMPT = """\
You are a senior payment operations analyst at a B2B fintech company.
Your job is to investigate TPV (Total Payment Volume) anomalies and produce
structured root cause narratives that a VP of Finance or Head of Payments
could act on immediately.

## Payment Products

| Product       | Description                          | Settle | Growth  |
|---------------|--------------------------------------|--------|---------|
| regular_ach   | Standard ACH, largest volume         | 7 days | +15% YoY |
| check         | Paper check, declining               | 14 days| -8% YoY  |
| two_day_ach   | Expedited ACH                        | 7 days | +45% YoY |
| one_day_ach   | Next-day ACH, newest product         | 7 days | +70% YoY |

Settlement note: `is_complete = TRUE` marks payments past their settlement window.
Recent dates show partial TPV — this is expected behavior, not an anomaly.

## Decomposition Dimensions

| Dimension           | Values                                                              |
|---------------------|---------------------------------------------------------------------|
| merchant_industry   | ecommerce, healthcare, payroll_staffing, professional_services, retail |
| merchant_size       | smb, mid_market, enterprise                                         |
| payer_industry      | tech, healthcare, retail_consumer, manufacturing, other             |
| payer_size          | smb, mid_market, enterprise                                         |
| payer_tenure_bucket | new_0_3mo, early_4_12mo, growing_1_3yr, established_3_5yr, loyal_5plus_yr |

## How to Read the Decomposition

Each segment row shows:
  - baseline_daily_tpv: avg daily TPV in the 30-day pre-anomaly window
  - anomaly_daily_tpv:  avg daily TPV during the flagged window
  - delta_daily_tpv:    difference (positive = spike, negative = drop)
  - pct_of_total_delta: this segment's share of the product-level change

### Systemic vs. segment-specific signals

**Uniform distribution** — pct_of_total_delta spread proportionally across all segments,
no single segment explains >40% of the delta:
=> Systemic cause: payment rail failure, banking outage, platform-wide incident,
  holiday or macro shock affecting all customers equally.

**Concentrated distribution** — 1–3 segments account for >70% of total delta:
=> Segment-specific cause: targeted fraud, industry event, cohort adoption wave,
  merchant category surge, onboarding spike or churn.

### Payer tenure signals
- Drop in new_0_3mo / early_4_12mo  -> acquisition slowdown or early churn
- Drop in loyal_5plus_yr             -> established-customer risk, competitive loss
- Spike in new_0_3mo                 -> rapid onboarding or fraud using new accounts

### Pairwise interaction signals
When a specific cell in the top_interactions table dominates (high |delta|, high pct),
the anomaly is at the *intersection* of both dimensions — neither alone explains it.
This is strong evidence of a targeted-segment event.

### Multi-product signal
If the same dimension pattern appears across multiple products, it strengthens
the systemic hypothesis (the cause is customer-level, not product-level).

## Output Format

Respond using exactly this structure (Markdown):

---

**TL;DR:** [One sentence: product, direction, magnitude, and most likely cause.]

**Ranked Hypotheses:**

1. **[Hypothesis name]** — Confidence: [High / Medium / Low]
   - Evidence: [specific segments, percentages, or patterns from the decomposition]
   - Mechanism: [how this cause produces the observed distribution]

2. **[Hypothesis name]** — Confidence: [High / Medium / Low]
   - Evidence: ...
   - Mechanism: ...

[Include 2–4 hypotheses, ranked by confidence. Be specific — reference actual numbers.]

**Most Diagnostic Dimension:** `[dimension_name]`
[1–2 sentences: why this dimension was most informative and what it revealed.]

**Key Segment Findings:**
- `[dimension]` — **[segment]**: [+/-X]% of total delta ([+/-$Y/day])
- `[dimension]` — **[segment]**: ...
- Top interaction: `[dim_a x dim_b]` — **[segment]**: [context]

[List the 3–5 most informative findings. Use actual numbers from the data.]

**Recommended Next Steps:**
- [ ] [Specific action — name the system, log, or query to check]
- [ ] [Another action]
- [ ] [Rule out an alternative explanation]
- [ ] [Stakeholder to notify or escalate to, if warranted]

---
"""


# ── Internal helpers ───────────────────────────────────────────────────────────

def _build_user_message(decomp: dict) -> str:
    s  = decomp["summary"]
    aw = decomp["anomaly_window"]
    bw = decomp["baseline_window"]

    delta_sign    = "+" if s["delta_daily_tpv"] >= 0 else ""
    direction_tag = "SPIKE" if s["direction"] == "spike" else "DROP"

    lines = [
        f"## Anomaly Report - {decomp['product'].upper()}",
        "",
        f"**Direction:** {direction_tag}  "
        f"|  **Magnitude:** {delta_sign}{s['delta_pct'] * 100:.1f}%  "
        f"|  **Delta:** {delta_sign}${s['delta_daily_tpv']:,.0f}/day",
        "",
        f"**Anomaly window:**  {aw['start']} to {aw['end']} "
        f"({aw['days']} day{'s' if aw['days'] != 1 else ''})",
        f"**Baseline window:** {bw['start']} to {bw['end']} ({bw['days']} days)",
        "",
        f"Baseline avg daily TPV : ${s['baseline_daily_tpv']:>14,.0f}",
        f"Anomaly  avg daily TPV : ${s['anomaly_daily_tpv']:>14,.0f}",
        "",
        "## Full Decomposition (JSON)",
        "",
        "```json",
        json.dumps(decomp, indent=2, default=str),
        "```",
        "",
        "Generate the root cause narrative following your output format.",
    ]
    return "\n".join(lines)


def _extract_text(message: anthropic.types.Message) -> str:
    """Pull only the text content blocks from a Claude response."""
    return "\n".join(
        block.text for block in message.content
        if hasattr(block, "text") and block.type == "text"
    ).strip()


# ── Public API ─────────────────────────────────────────────────────────────────

def synthesize(
    decomposition: dict,
    client: anthropic.Anthropic | None = None,
    stream_to_stdout: bool = False,
) -> str:
    """
    Generate a root cause narrative from a segment decomposition.

    Parameters
    ----------
    decomposition    : dict returned by segment_decomposer.decompose()
    client           : reuse an existing Anthropic client; creates one if None
    stream_to_stdout : print tokens as they arrive (useful for CLI)

    Returns
    -------
    Formatted narrative string (Markdown).
    """
    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. "
                "Export it before running: set ANTHROPIC_API_KEY=sk-ant-..."
            )
        client = anthropic.Anthropic(api_key=api_key)

    with client.messages.stream(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {"role": "user", "content": _build_user_message(decomposition)}
        ],
    ) as stream:
        if stream_to_stdout:
            for chunk in stream.text_stream:
                print(chunk, end="", flush=True)
            print()

        final = stream.get_final_message()

    return _extract_text(final)


def synthesize_for_event(
    product: str,
    anomaly_start: date,
    anomaly_end: date,
    baseline_days: int = 30,
    client: anthropic.Anthropic | None = None,
    stream_to_stdout: bool = False,
    engine=None,
) -> tuple[dict, str]:
    """
    Convenience wrapper: run decomposition then synthesize.

    Returns (decomposition_dict, narrative_string).
    """
    from decomposition.segment_decomposer import decompose

    decomp = decompose(
        product=product,
        anomaly_start=anomaly_start,
        anomaly_end=anomaly_end,
        baseline_days=baseline_days,
        engine=engine,
    )
    narrative = synthesize(decomp, client=client, stream_to_stdout=stream_to_stdout)
    return decomp, narrative


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")  # Windows CP1252 -> UTF-8 for streaming output

    from decomposition.segment_decomposer import decompose

    # Smoke test: NACHA outage — regular_ach, Sep 15-17 2023
    # Expected: uniform drop across all dimensions (systemic/infrastructure cause)
    PRODUCT       = "regular_ach"
    ANOMALY_START = date(2023, 9, 15)
    ANOMALY_END   = date(2023, 9, 17)

    print(f"Step 1/2  Decomposing {PRODUCT}  {ANOMALY_START} to {ANOMALY_END} ...")
    decomp = decompose(
        product=PRODUCT,
        anomaly_start=ANOMALY_START,
        anomaly_end=ANOMALY_END,
        baseline_days=30,
    )
    s = decomp["summary"]
    delta_sign = "+" if s["delta_daily_tpv"] >= 0 else ""
    print(
        f"          {s['direction'].upper()}  "
        f"{delta_sign}{s['delta_pct']*100:.1f}%  "
        f"({delta_sign}${s['delta_daily_tpv']:,.0f}/day)\n"
    )

    print("Step 2/2  Generating narrative (streaming) ...\n")
    print("=" * 70)
    narrative = synthesize(decomp, stream_to_stdout=True)
    print("=" * 70)
    print(f"\nDone. {len(narrative)} characters of narrative generated.")
