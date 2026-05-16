"""
Anomaly Explainer — interactive CLI.

Runs Prophet detection (or loads cache), lists every detected anomaly window,
lets the user pick one, then runs decompose -> synthesize and prints the
root cause narrative.

Usage:
    python run.py           # use cached detection if available
    python run.py --fresh   # refit Prophet, overwrite cache
"""

import sys
import os
sys.stdout.reconfigure(encoding="utf-8")

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from decomposition.segment_decomposer import decompose
from narrative.llm_synthesizer import synthesize
from detection.prophet_model import load_or_detect_all, group_flagged_days


# ── Window loader ──────────────────────────────────────────────────────────────

def _load_all_windows(fresh: bool) -> list[dict]:
    """
    Detect anomalies for all products, flatten into a date-sorted list.
    Each item: {product, start, end, peak_z, direction}.
    """
    all_results = load_or_detect_all(fresh=fresh)
    items = []
    for product, results in all_results.items():
        for w in group_flagged_days(results):
            items.append({
                "product":   product,
                "start":     w["start"],
                "end":       w["end"],
                "peak_z":    w["peak_z"],
                "direction": w["direction"],
            })
    return sorted(items, key=lambda x: x["start"])


# ── Menu ───────────────────────────────────────────────────────────────────────

def _pick_window(windows: list[dict]) -> dict:
    arrow = {"spike": "↑", "drop": "↓"}

    # Product breakdown summary
    from collections import Counter
    counts = Counter(w["product"] for w in windows)
    print(f"  {len(windows)} anomaly windows detected across {len(counts)} products:")
    for product, n in sorted(counts.items()):
        print(f"    {product:<16}  {n} window(s)")
    print()

    # Full window list
    print(f"  {'#':>4}  {'Product':<16}  {'Start':<12}  {'End':<12}  {'Dir':<5}  z-score")
    print("  " + "-" * 64)
    for i, w in enumerate(windows, 1):
        a = arrow.get(w["direction"], " ")
        print(f"  {i:>4}.  {w['product']:<16}  {w['start']}  {w['end']}  {a:<5}  {w['peak_z']:.1f}")
    print()

    while True:
        raw = input(f"Select window [1-{len(windows)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(windows):
            return windows[int(raw) - 1]
        print(f"  Enter a number between 1 and {len(windows)}.")


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
    fresh = "--fresh" in sys.argv

    print("\n" + "=" * 70)
    print("  ANOMALY EXPLAINER")
    print("=" * 70 + "\n")

    print("Loading anomaly windows ...")
    windows = _load_all_windows(fresh)
    if not windows:
        print("No anomaly windows detected. Try running with --fresh.")
        sys.exit(0)
    print()

    window = _pick_window(windows)
    product = window["product"]
    start   = window["start"]
    end     = window["end"]

    print(f"\nAnalyzing: {product}  {start} → {end}  (peak z={window['peak_z']:.1f})\n")

    # Step 1 — Decomposition
    print("Step 1/2  Decomposing ...")
    decomp = decompose(
        product=product,
        anomaly_start=start,
        anomaly_end=end,
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
    synthesize(decomp, stream_to_stdout=True)
    print("-" * 70)
    print(f"\nComplete. Model: claude-opus-4-7.\n")


if __name__ == "__main__":
    main()
