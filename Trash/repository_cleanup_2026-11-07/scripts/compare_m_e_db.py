import sqlite3
import json

db_morning = ".checkpoints_live_recovery_work/checkpoints.db"
db_evening = "checkpoints_archive_safety_zero_topics_run_20261026/checkpoints_zero_topics_backup.db"

conn_m = sqlite3.connect(db_morning)
cur_m = conn_m.cursor()
cur_m.execute("SELECT config_fingerprint, document_fingerprint, chunk_index, serialized_result, created_at FROM checkpoint_chunks ORDER BY chunk_index")
rows_m = cur_m.fetchall()
print(f"Morning chunks count: {len(rows_m)}")
if rows_m:
    print("Morning config_fp:", rows_m[0][0])
    print("Morning doc_fp:", rows_m[0][1])

conn_e = sqlite3.connect(db_evening)
cur_e = conn_e.cursor()
cur_e.execute("SELECT config_fingerprint, document_fingerprint, chunk_index, serialized_result, created_at FROM checkpoint_chunks ORDER BY chunk_index")
rows_e = cur_e.fetchall()
print(f"Evening chunks count: {len(rows_e)}")
if rows_e:
    print("Evening config_fp:", rows_e[0][0])
    print("Evening doc_fp:", rows_e[0][1])

cur_e.execute("SELECT chunk_index, chunk_id, status, attempts, duration_seconds, model_id, http_status, finish_reason, prompt_tokens, completion_tokens, error_type, diagnostic_category, failure_phase, error_detail, json_valid, schema_valid, evidence_valid, checkpoint_saved FROM checkpoint_chunk_diagnostics ORDER BY chunk_index")
diag_rows_e = cur_e.fetchall()
print(f"Evening diagnostics count: {len(diag_rows_e)}")

total_m_topics = 0
for r in rows_m:
    res = json.loads(r[3])
    topics = res.get("topics", [])
    total_m_topics += len(topics)
print(f"Morning total topics across chunks: {total_m_topics}")
for r in rows_m:
    res = json.loads(r[3])
    topics = res.get("topics", [])
    if topics:
        labels = [t.get("label") for t in topics]
        print(f"  Chunk {r[2]}: {labels}")

total_e_topics = 0
for r in rows_e:
    res = json.loads(r[3])
    topics = res.get("topics", [])
    total_e_topics += len(topics)
print(f"Evening total topics across chunks: {total_e_topics}")

print("\n--- Evening 26 Chunks Diagnostics Breakdown ---")
for r in diag_rows_e:
    print(f"Chunk {r[0]}: status={r[2]}, cat={r[11]}, phase={r[12]}, prompt_tok={r[8]}, comp_tok={r[9]}, err_type={r[10]}, err_detail={r[13]}")
