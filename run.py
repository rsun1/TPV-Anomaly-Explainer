"""
Anomaly Explainer — master entry point.

Loads ANTHROPIC_API_KEY from .env, presents a menu of the 6 seeded anomaly
windows, runs decompose -> synthesize, and prints the root cause narrative.

Usage:
    python run.py
"""

import sys
import os
sys.stdout.reconfigure(encoding="utf-8")   # handle Claude's unicode output on Windows

from pathlib import Path
from dotenv import load_dotenv
# Load .env before any module that needs ANTHROPIC_API_KEY
load_dotenv(Path(__file__).parent / ".env")

from decomposition.segment_decomposer import decompose
from narrative.llm_synthesizer import synthesize
from events import EVENTS


# ── Menu helpers ───────────────────────────────────────────────────────────────

def _pick_event() -> dict:
    print("Anomaly windows flagged by the detection layer:\n")
    for i, e in enumerate(EVENTS, 1):
        products = ", ".join(e["products"])
        print(f"  {i}.  {e['label']}  |  {products}")
    print()

    while True:
        raw = input("Select event [1-6]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(EVENTS):
            return EVENTS[int(raw) - 1]
        print(f"  Enter a number between 1 and {len(EVENTS)}.")


def _pick_product(products: list[str]) -> str:
    if len(products) == 1:
        return products[0]

    print("\nMultiple products affected. Which one to analyze?\n")
    for i, p in enumerate(products, 1):
        print(f"  {i}.  {p}")
    print()

    while True:
        raw = input(f"Select product [1-{len(products)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(products):
            return products[int(raw) - 1]
        print(f"  Enter a number between 1 and {len(products)}.")


# ── Pipeline ───────────────────────────────────────────────────────────────────

def _check_api_key() -> None:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or key == "your-key-here":
        print(
            "ERROR: ANTHROPIC_API_KEY not configured.\n"
            "  1. Open .env in the project root.\n"
            "  2. Replace 'your-key-here' with your key from console.anthropic.com.\n"
            "  3. Save and re-run.\n"
        )
        sys.exit(1)


def main() -> None:
    _check_api_key()

    print("\n" + "=" * 70)
    print("  ANOMALY EXPLAINER")
    print("=" * 70 + "\n")

    event   = _pick_event()
    product = _pick_product(event["products"])

    print(f"\nRunning pipeline for: {product}  {event['start']} to {event['end']}\n")

    # Step 1 — Decomposition
    print("Step 1/2  Decomposing ...")
    decomp = decompose(
        product=product,
        anomaly_start=event["start"],
        anomaly_end=event["end"],
        baseline_days=30,
    )
    s = decomp["summary"]
    delta_sign = "+" if s["delta_daily_tpv"] >= 0 else ""
    print(
        f"          {s['direction'].upper()}  "
        f"{delta_sign}{s['delta_pct'] * 100:.1f}%  "
        f"({delta_sign}${s['delta_daily_tpv']:,.0f}/day)\n"
    )

    # Step 2 — Narrative
    print("Step 2/2  Generating narrative ...\n")
    print("-" * 70)
    narrative = synthesize(decomp, stream_to_stdout=True)
    print("-" * 70)
    print(f"\nComplete. Model: claude-opus-4-7.\n")


if __name__ == "__main__":
    main()
