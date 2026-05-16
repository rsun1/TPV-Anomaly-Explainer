"""
FastAPI backend for the Anomaly Explainer.

Endpoints:
  GET  /events               — list of the 6 seeded anomaly windows
  GET  /timeseries/{product} — daily TPV + anomaly window markers for the trend view
  POST /analyze              — SSE stream: decomposition JSON, then Claude narrative chunks

Run from the project root:
    uvicorn api.main:app --reload --port 8000
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

# Make project root importable regardless of working directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import anthropic
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DB_URL = os.environ["DATABASE_URL"]
_engine = None

def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DB_URL)
    return _engine

from decomposition.segment_decomposer import decompose
from narrative.llm_synthesizer import _SYSTEM_PROMPT, _build_user_message, MODEL
from detection.prophet_model import load_or_detect_all, group_flagged_days

app = FastAPI(title="Anomaly Explainer API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ──────────────────────────────────────────────────────────────────

def _get_windows() -> list[dict]:
    """Flat list of all detected anomaly windows across all products, date-sorted."""
    all_results = load_or_detect_all()
    items = []
    for product, results in all_results.items():
        for i, w in enumerate(group_flagged_days(results)):
            items.append({
                "id":        f"{product}_{i}",
                "product":   product,
                "products":  [product],
                "label":     f"{product}  {w['start']} → {w['end']}",
                "start":     str(w["start"]),
                "end":       str(w["end"]),
                "peak_z":    round(w["peak_z"], 1),
                "direction": w["direction"],
            })
    return sorted(items, key=lambda x: x["start"])


@app.get("/events")
def get_events():
    return _get_windows()


@app.get("/timeseries/{product}")
def get_timeseries(product: str):
    """
    Daily aggregate TPV for one product (complete rows only).
    Also returns detected anomaly windows for this product so the
    frontend can draw bands — no labels, no explanations, just markers.
    """
    engine = _get_engine()
    df = pd.read_sql(
        text("""
            SELECT date::text AS date,
                   SUM(tpv_scheduled)::float AS tpv
            FROM payment_daily_tpv
            WHERE product    = :product
              AND is_complete = TRUE
            GROUP BY date
            ORDER BY date
        """),
        engine,
        params={"product": product},
    )
    all_results = load_or_detect_all()
    product_windows = []
    if product in all_results:
        product_windows = [
            {"start": str(w["start"]), "end": str(w["end"])}
            for w in group_flagged_days(all_results[product])
        ]
    return {
        "product":        product,
        "series":         df.to_dict(orient="records"),
        "anomaly_windows": product_windows,
    }


class AnalyzeRequest(BaseModel):
    product: str
    start: str   # YYYY-MM-DD
    end:   str   # YYYY-MM-DD


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    """
    Server-Sent Events stream.
    Event sequence:
      1. {"type": "decomposition", "data": {...}}   — chart data, arrives fast
      2. {"type": "chunk",         "text": "..."}   — narrative tokens from Claude
      3. {"type": "done"}
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or api_key == "your-key-here":
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured.")

    def generate():
        # Phase 1 — decomposition (fast, ~1-2 s)
        decomp = decompose(
            product=req.product,
            anomaly_start=date.fromisoformat(req.start),
            anomaly_end=date.fromisoformat(req.end),
            baseline_days=30,
        )
        yield f"data: {json.dumps({'type': 'decomposition', 'data': decomp}, default=str)}\n\n"

        # Phase 2 — narrative (streams Claude tokens)
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
            messages=[{"role": "user", "content": _build_user_message(decomp)}],
        ) as stream:
            for chunk in stream.text_stream:
                yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
