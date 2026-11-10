import sqlite3
import os

db_path = ".checkpoints/checkpoints.db"
conn = sqlite3.connect(db_path)
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print(f"Tables in {db_path}: {tables}")

for t in tables:
    c.execute(f"SELECT count(*) FROM {t}")
    cnt = c.fetchone()[0]
    print(f"\n--- Table {t}: {cnt} rows ---")
    c.execute(f"PRAGMA table_info({t})")
    cols = [col[1] for col in c.fetchall()]
    print(f"Columns: {cols}")
    c.execute(f"SELECT * FROM {t}")
    rows = c.fetchall()
    for r in rows:
        # truncate serialized or huge fields
        display_r = []
        for val in r:
            s = str(val)
            if len(s) > 50:
                s = s[:45] + "..."
            display_r.append(s)
        print("  ", display_r)
conn.close()
