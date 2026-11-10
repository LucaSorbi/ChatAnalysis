import sqlite3

for p in ['.checkpoints/checkpoints.db', '.checkpoints_live_recovery_work/checkpoints.db']:
    print(f"=== {p} ===")
    conn = sqlite3.connect(p)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in c.fetchall()]
    for t in tables:
        c.execute(f"SELECT count(*) FROM {t}")
        cnt = c.fetchone()[0]
        print(f"  {t}: {cnt}")
        if 'chunks' in t and 'sub' not in t and cnt > 0:
            c.execute(f"SELECT chunk_index FROM {t} ORDER BY chunk_index")
            print(f"    indices: {[r[0] for r in c.fetchall()]}")
    conn.close()
