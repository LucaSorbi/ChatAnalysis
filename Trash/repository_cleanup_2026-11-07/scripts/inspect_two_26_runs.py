import sqlite3
import json

db_path = "checkpoints_archive_safety_zero_topics_run_20261026/checkpoints_zero_topics_backup.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cfg_089c = "089cb7bfe379b867574298e8dd1646bbb424bf972c5cdf3031771a7d8a50b73a"
cfg_8528 = "8528f7452a1eefaa3c14c03c4185647128977a7c30a1800ef52d214eed87426b"

print("==================== RUN 1: 089c (First 26-chunk run) ====================")
cur.execute("SELECT chunk_index, status, prompt_tokens, completion_tokens, error_type, error_detail, updated_at FROM checkpoint_chunk_diagnostics WHERE config_fingerprint=? ORDER BY chunk_index", (cfg_089c,))
diags_089c = cur.fetchall()
for d in diags_089c:
    print(f"Chunk {d[0]:02d}: status={d[1]}, prompt_tok={d[2]}, comp_tok={d[3]}, err={d[4]}, detail={d[5]}, time={d[6]}")

print("\n==================== RUN 2: 8528 (Second 26-chunk run) ====================")
cur.execute("SELECT chunk_index, status, prompt_tokens, completion_tokens, error_type, error_detail, updated_at FROM checkpoint_chunk_diagnostics WHERE config_fingerprint=? ORDER BY chunk_index", (cfg_8528,))
diags_8528 = cur.fetchall()
for d in diags_8528:
    print(f"Chunk {d[0]:02d}: status={d[1]}, prompt_tok={d[2]}, comp_tok={d[3]}, err={d[4]}, detail={d[5]}, time={d[6]}")

# Also check checkpoint_chunks table for both configs
cur.execute("SELECT config_fingerprint, chunk_index, length(serialized_result) FROM checkpoint_chunks WHERE config_fingerprint IN (?, ?) ORDER BY config_fingerprint, chunk_index", (cfg_089c, cfg_8528))
chk_chunks = cur.fetchall()
print(f"\nSaved checkpoint_chunks: {len(chk_chunks)} total")
for c in chk_chunks:
    print(f"Config: {c[0][:8]}..., chunk: {c[1]}, size: {c[2]}")

# Also check checkpoints table (the main run record)
cur.execute("SELECT config_fingerprint, status, chunk_count, completed_chunks, error_count, length(serialized_result) FROM checkpoints")
runs = cur.fetchall()
print("\nCheckpoints table:")
for r in runs:
    print(f"Config: {r[0][:8]}..., status={r[1]}, count={r[2]}, completed={r[3]}, errors={r[4]}, res_len={r[5]}")
