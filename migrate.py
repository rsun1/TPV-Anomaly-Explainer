import os
import sqlite3
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv(Path(__file__).parent / ".env")

sqlite_path = r"C:\Users\sunru\Documents\Anomaly Explainer\olist.sqlite"
pg_engine = create_engine(os.environ["DATABASE_URL"])

sqlite_conn = sqlite3.connect(sqlite_path)

tables = sqlite_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
tables = [t[0] for t in tables]
print(tables)
print(f"Found tables: {tables}")

for table in tables:
    print(f"Migrating {table}...")
    df = pd.read_sql(f"SELECT * FROM {table}", sqlite_conn)
    df.to_sql(table, pg_engine, if_exists="replace", index=False)
    print(f"  Done — {len(df)} rows")

sqlite_conn.close()
print("Migration complete.")
