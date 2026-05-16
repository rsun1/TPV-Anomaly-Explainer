import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import pandas as pd

load_dotenv(Path(__file__).parent.parent / ".env")
engine = create_engine(os.environ["DATABASE_URL"])

DIMENSIONS = [
    "merchant_industry",
    "merchant_size",
    "payer_industry",
    "payer_size",
    "payer_tenure_bucket",
]

def inject_anomaly(target_month: str, dimension: str, dimension_value: str, drop_pct: float = 0.4):
    """
    Reduce tpv_scheduled by drop_pct for rows matching the dimension filter
    in the target month. Writes to a separate table so raw data stays clean.

    target_month : '2024-02-01'  (first day of the month)
    dimension    : one of merchant_industry | merchant_size | payer_industry |
                   payer_size | payer_tenure_bucket
    dimension_value : e.g. 'ecommerce' | 'enterprise' | 'tech' |
                      'new_0_3mo' | 'smb' etc.
    drop_pct     : 0.4 = 40% drop in tpv_scheduled
    """
    pass

def reset_injections():
    """Drop the injected anomaly table and restore the clean baseline."""
    pass