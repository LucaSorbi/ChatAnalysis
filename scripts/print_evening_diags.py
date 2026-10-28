import sqlite3
import json

db_evening = "checkpoints_archive_safety_zero_topics_run_20261026/checkpoints_zero_topics_backup.db"

conn_e = sqlite3.connect(db_evening)
cur_e = conn_e.cursor()
cur_e.execute("SELECT chunk_index, chunk_id, status, attempts, duration_seconds, model_id, http_status, finish_reason, prompt_tokens, completion_tokens, error_type, diagnostic_category, failure_phase, error_detail, json_valid, schema_valid, evidence_valid, checkpoint_saved FROM checkpoint_chunk_diagnostics ORDER BY chunk_index")
diag_rows_e = cur_e.fetchall()
print(f"Evening diagnostics count: {len(diag_rows_e)}")

print("\n--- Evening Chunks Diagnostics Breakdown ---")
for r in diag_rows_e:
    print(f"Chunk {r[0]}: id={r[1]}, status={r[2]}, cat={r[11]}, phase={r[12]}, prompt_tok={r[8]}, comp_tok={r[9]}, err_type={r[10]}, err_detail={r[13]}".encode("ascii", errors="replace").decode("ascii"))
