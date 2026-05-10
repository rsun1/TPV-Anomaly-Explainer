import sqlite3
import pandas as pd
from sqlalchemy import create_engine

sqlite_path = r"C:\Users\sunru\Documents\Anomaly Explainer\olist.sqlite"
pg_engine = create_engine("postgresql://postgres:olist123@localhost:5432/transactions")

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
