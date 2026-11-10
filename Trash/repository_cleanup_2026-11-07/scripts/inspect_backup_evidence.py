import sqlite3
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

db_path = ".checkpoints_backup_20261025_regression/checkpoints_backup_20261025_204920.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("SELECT chunk_index, serialized_result FROM checkpoint_chunks ORDER BY chunk_index")
rows = cur.fetchall()

print(f"Total chunks in backup: {len(rows)}")
for chunk_idx, raw_json in rows:
    data = json.loads(raw_json)
    topics = data.get("topics", [])
    print(f"\n--- CHUNK #{chunk_idx} ({len(topics)} topics) ---")
    for t in topics:
        lbl = t.get("label")
        desc = t.get("short_description")
        evs = t.get("evidence_ids", [])
        print(f"  Topic: {lbl!r} | Evidence count: {len(evs)} | IDs: {evs[:5]}")

conn.close()
