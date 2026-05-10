"""
eval/narrative_scorer.py — LLM-as-judge evaluation of root cause narratives.

Runs the full pipeline (decompose → synthesize) for one product per ground-truth
event, then calls a separate Claude model (Sonnet) as judge. Using a different
model avoids the model judging its own outputs on an identical prompt.

Narratives are cached to eval/narrative_cache.json so only the judge re-runs
on repeated calls (judge calls are fast and cheap).

Usage:
    python -m eval.narrative_scorer           # use cached narratives
    python -m eval.narrative_scorer --fresh   # re-generate all narratives + re-judge
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import anthropic
from sqlalchemy import create_engine

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from decomposition.segment_decomposer import decompose
from narrative.llm_synthesizer import synthesize
from eval.scorer import load_ground_truth

DB_URL       = "postgresql://postgres:olist123@localhost:5432/transactions"
CACHE_PATH   = Path(__file__).parent / "narrative_cache.json"
JUDGE_MODEL  = "claude-sonnet-4-6"

CRITERIA = [
    "hypothesis_accuracy",
    "evidence_specificity",
    "dimension_identification",
    "confidence_calibration",
    "actionability",
]

# ── Judge prompt ───────────────────────────────────────────────────────────────

_JUDGE_SYSTEM = """\
You are a rigorous evaluator of AI-generated payment anomaly root cause narratives.
You receive: (1) the narrative, (2) the verified ground truth, (3) the decomposition
data the AI was given. Score strictly — do not reward vague or generic text.
"""

_JUDGE_TEMPLATE = """\
## Ground Truth

Event          : {event_name}
Product scored : {product}
True dimension : {dimensions}
  ("all" means no single dimension — event is systemic / rail-level)
Direction      : {direction}
Anomaly window : {start} to {end}

## Decomposition Data Given to the AI

```json
{decomp_json}
```

## AI-Generated Narrative

{narrative}

---

Score on 5 criteria (integer 1–5 each):

**hypothesis_accuracy**
  Was the true root cause present in the ranked hypotheses?
  5 = true cause is hypothesis #1; 4 = #2; 3 = #3 or clearly implied; 2 = barely present; 1 = absent

**evidence_specificity**
  Did the narrative cite concrete numbers (%, $, segment names) from the decomposition?
  5 = 4+ specific data points with context; 4 = 2–3; 3 = 1; 2 = vague references; 1 = no data cited

**dimension_identification**
  Did it name the correct most-diagnostic dimension?
  If true dimension = "all": correct answer is "no single dimension dominates — systemic event."
  5 = exactly right; 3 = correct but not highlighted; 1 = wrong dimension called out

**confidence_calibration**
  Is the confidence level appropriate for the evidence quality?
  Systemic events (uniform distribution) warrant lower certainty than concentrated segment events.
  5 = well-calibrated; 3 = slightly over/under-confident; 1 = badly miscalibrated

**actionability**
  Are next steps specific — naming systems, logs, queries, or teams to contact?
  5 = highly specific (e.g., "check NACHA status page", "query fraud_alerts table"); 3 = relevant but generic; 1 = vague or off-target

Return ONLY valid JSON — no markdown fences, no commentary outside the object:
{{
  "scores": {{
    "hypothesis_accuracy": <int 1-5>,
    "evidence_specificity": <int 1-5>,
    "dimension_identification": <int 1-5>,
    "confidence_calibration": <int 1-5>,
    "actionability": <int 1-5>
  }},
  "overall": <float>,
  "reasoning": "<one sentence explaining the hypothesis_accuracy score>"
}}
"""


# ── Narrative cache ────────────────────────────────────────────────────────────

def _cache_key(product: str, start: date, end: date) -> str:
    return f"{product}_{start}_{end}"


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    with open(CACHE_PATH) as f:
        return json.load(f)


def _save_cache(cache: dict) -> None:
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, default=str)


# ── Pipeline helpers ───────────────────────────────────────────────────────────

def _generate_narrative(
    product: str,
    start: date,
    end: date,
    engine,
    client: anthropic.Anthropic,
) -> tuple[dict, str]:
    """Run decompose() + synthesize() for one event. Returns (decomp_dict, narrative_text)."""
    decomp    = decompose(product=product, anomaly_start=start, anomaly_end=end,
                          baseline_days=30, engine=engine)
    narrative = synthesize(decomp, client=client)
    return decomp, narrative


def _judge_narrative(
    narrative: str,
    decomp: dict,
    event_name: str,
    product: str,
    gt_dimensions: str,
    client: anthropic.Anthropic,
) -> dict:
    """
    Call JUDGE_MODEL to score one narrative. Returns the parsed scores dict.
    Uses a non-streaming call — response is short and structured.
    """
    s      = decomp["summary"]
    prompt = _JUDGE_TEMPLATE.format(
        event_name=event_name,
        product=product,
        dimensions=gt_dimensions,
        direction=s["direction"].upper(),
        start=decomp["anomaly_window"]["start"],
        end=decomp["anomaly_window"]["end"],
        decomp_json=json.dumps(decomp, indent=2, default=str),
        narrative=narrative,
    )

    response = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=512,
        system=_JUDGE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    # Strip markdown fences if the model includes them despite instructions
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])   # drop first line (``` or ```json)
        text = text.rsplit("```", 1)[0].strip()

    result = json.loads(text)
    if "overall" not in result:
        result["overall"] = round(sum(result["scores"].values()) / len(result["scores"]), 2)
    return result


# ── Report printer ─────────────────────────────────────────────────────────────

def _print_report(rows: list[dict]) -> None:
    print()
    print("=" * 76)
    print("  ANOMALY EXPLAINER  —  NARRATIVE EVAL  (LLM-as-judge: claude-sonnet-4-6)")
    print("=" * 76)

    short = {"hypothesis_accuracy": "Hyp", "evidence_specificity": "Evid",
             "dimension_identification": "Dim", "confidence_calibration": "Cal",
             "actionability": "Act"}

    # Header
    print()
    hdr = f"  {'Event':<38}  {'Product':<14}  " + "  ".join(f"{short[c]:>4}" for c in CRITERIA) + "  Avg"
    print(hdr)
    print("  " + "─" * 74)

    for r in rows:
        sc     = r["scores"]
        scores = "  ".join(f"{sc[c]:>4}" for c in CRITERIA)
        print(f"  {r['event_name']:<38}  {r['product']:<14}  {scores}  {r['overall']:>3.1f}")
        print(f"    {r['reasoning']}")

    print()
    print("AVERAGES")
    for c in CRITERIA:
        avg = sum(r["scores"][c] for r in rows) / len(rows)
        bar = "█" * int(avg) + "░" * (5 - int(avg))
        print(f"  {c:<28}  {avg:.1f}  {bar}")

    overall = sum(r["overall"] for r in rows) / len(rows)
    print(f"\n  Overall average: {overall:.1f} / 5.0")
    print("=" * 76)


# ── Main ───────────────────────────────────────────────────────────────────────

def main(fresh: bool = False) -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or api_key == "your-key-here":
        raise EnvironmentError(
            "ANTHROPIC_API_KEY not set. Export it or add it to .env before running."
        )

    engine = create_engine(DB_URL)
    client = anthropic.Anthropic(api_key=api_key)

    print("\nLoading ground truth ...")
    gt = load_ground_truth(engine)
    print(f"  {len(gt)} events\n")

    cache = {} if fresh else _load_cache()
    rows: list[dict] = []

    for _, gt_row in gt.iterrows():
        # For multi-product events use the primary (first) affected product.
        # To score all products, loop over affected_products.split(",").
        product = gt_row["affected_products"].split(",")[0].strip()
        key     = _cache_key(product, gt_row["start_date"], gt_row["end_date"])

        if key in cache and not fresh:
            print(f"  [cached]    {gt_row['event_name']}  ({product})")
            entry     = cache[key]
            decomp    = entry["decomp"]
            narrative = entry["narrative"]
        else:
            print(f"  [generating] {gt_row['event_name']}  ({product}) ...")
            decomp, narrative = _generate_narrative(
                product, gt_row["start_date"], gt_row["end_date"], engine, client,
            )
            cache[key] = {"decomp": decomp, "narrative": narrative}
            _save_cache(cache)

        print(f"  [judging]   {gt_row['event_name']}  ({product}) ...")
        result = _judge_narrative(
            narrative, decomp,
            gt_row["event_name"], product,
            gt_row["affected_dimensions"], client,
        )

        rows.append({
            "event_name": gt_row["event_name"],
            "product":    product,
            "scores":     result["scores"],
            "overall":    result["overall"],
            "reasoning":  result.get("reasoning", ""),
        })

    _print_report(rows)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    fresh = "--fresh" in sys.argv
    main(fresh=fresh)
