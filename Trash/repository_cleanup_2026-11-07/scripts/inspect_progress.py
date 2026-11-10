import sqlite3
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('output/live_run_1049_26chunks/checkpoints/checkpoints.db')
c = conn.cursor()

print("--- PARENT CHUNKS ---")
chunks = c.execute('SELECT chunk_index, length(serialized_result), created_at FROM checkpoint_chunks ORDER BY chunk_index').fetchall()
for ch in chunks:
    res = json.loads(c.execute('SELECT serialized_result FROM checkpoint_chunks WHERE chunk_index = ?', (ch[0],)).fetchone()[0])
    topics = res.get('topics', [])
    print(f"Parent {ch[0]}: {len(topics)} topics, created_at={ch[2]}")
    for t in topics:
        print(f"   - {t.get('label')} (eids: {len(t.get('evidence_ids', []))})")

print("\n--- SUBCHUNKS ---")
subs = c.execute('SELECT parent_chunk_index, subchunk_index, total_subchunks, subchunk_id, length(serialized_result), created_at FROM checkpoint_subchunks ORDER BY parent_chunk_index, subchunk_index').fetchall()
for s in subs:
    res = json.loads(c.execute('SELECT serialized_result FROM checkpoint_subchunks WHERE parent_chunk_index = ? AND subchunk_index = ?', (s[0], s[1])).fetchone()[0])
    topics = res.get('topics', [])
    print(f"Parent {s[0]}, Sub {s[1]}/{s[2]} ({s[3]}): {len(topics)} topics, created_at={s[5]}")

print("\n--- DIAGNOSTICS ---")
diags = c.execute('SELECT chunk_index, chunk_id, status, attempts, error_type, diagnostic_category, error_detail FROM checkpoint_chunk_diagnostics ORDER BY chunk_index').fetchall()
for d in diags:
    print(d)
