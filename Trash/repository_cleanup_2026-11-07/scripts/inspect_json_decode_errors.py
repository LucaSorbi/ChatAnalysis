import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
conn = sqlite3.connect(".checkpoints_backup_20261025_regression/checkpoints_backup_20261025_204920.db")
cur = conn.cursor()

query = """
SELECT chunk_index, status, attempts, prompt_tokens, completion_tokens, finish_reason, duration_seconds, error_type, error_detail
FROM checkpoint_chunk_diagnostics
WHERE diagnostic_category = 'JSON_DECODE_ERROR'
ORDER BY chunk_index
"""

for row in cur.execute(query).fetchall():
    print(f"Chunk #{row[0]}: status={row[1]}, attempts={row[2]}, prompt_tok={row[3]}, compl_tok={row[4]}, finish={row[5]}, dur={row[6]:.1f}s, err={row[7]}")
    print(f"   Detail: {row[8]}")

conn.close()
