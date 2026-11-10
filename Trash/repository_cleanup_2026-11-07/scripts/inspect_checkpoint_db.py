import sqlite3
from pathlib import Path
import json

db_path = Path(".checkpoints/checkpoints.db")
con = sqlite3.connect(db_path)
cur = con.cursor()

tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables:", tables)

for (t,) in tables:
    count = cur.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    cols = [col[1] for col in cur.execute(f"PRAGMA table_info({t})").fetchall()]
    print(f"\nTable {t}: {count} rows")
    print(f"  Cols: {cols}")
    rows = cur.execute(f"SELECT * FROM {t} LIMIT 5").fetchall()
    for r in rows:
        # Sanitized print (no full text)
        print("   ", [str(x)[:60] for x in r])
