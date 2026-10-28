import sqlite3

db_path = "checkpoints_archive_safety_zero_topics_run_20261026/checkpoints_zero_topics_backup.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cols = [d[1] for d in cur.execute("PRAGMA table_info(checkpoints)").fetchall()]
print("Checkpoints columns:", cols)

cur.execute("SELECT * FROM checkpoints")
for row in cur.fetchall():
    d = dict(zip(cols, row))
    for k, v in d.items():
        if k != "serialized_result":
            print(f"  {k}: {v}")
        else:
            print(f"  {k}: len={len(v) if v else 0}")
    print("-" * 50)
