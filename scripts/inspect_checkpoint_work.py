import sqlite3

conn = sqlite3.connect(".checkpoints_live_recovery_work/checkpoints.db")
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables:", tables)

for (tbl,) in tables:
    count = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    print(f"Table '{tbl}': {count} rows")
    cur = conn.execute(f"PRAGMA table_info({tbl})")
    cols = [c[1] for c in cur.fetchall()]
    print(f"  Columns: {cols}")
    sample = conn.execute(f"SELECT * FROM {tbl} LIMIT 2").fetchall()
    print(f"  Sample row count: {len(sample)}")

conn.close()
