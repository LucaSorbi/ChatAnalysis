import sqlite3

conn = sqlite3.connect('.checkpoints/checkpoints.db')
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print("Tables in .checkpoints/checkpoints.db:", tables)

for t in tables:
    c.execute(f"SELECT count(*) FROM {t}")
    cnt = c.fetchone()[0]
    print(f"\n--- {t} ({cnt} rows) ---")
    c.execute(f"PRAGMA table_info({t})")
    cols = [col[1] for col in c.fetchall()]
    print("Columns:", cols)
    c.execute(f"SELECT * FROM {t} LIMIT 5")
    for r in c.fetchall():
        print("  ", [str(x)[:40] for x in r])
conn.close()
