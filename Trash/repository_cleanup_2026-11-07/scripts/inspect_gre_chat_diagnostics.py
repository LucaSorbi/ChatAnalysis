import sqlite3

db = ".checkpoints/checkpoints.db"
conn = sqlite3.connect(db)
c = conn.cursor()
c.execute("""
    SELECT config_fingerprint, chunk_index, status, attempts, duration_seconds, http_status, 
           finish_reason, prompt_tokens, completion_tokens, error_type, diagnostic_category, failure_phase, error_detail, updated_at
    FROM checkpoint_chunk_diagnostics 
    WHERE document_fingerprint LIKE 'e78e13%'
    ORDER BY updated_at DESC
""")
rows = c.fetchall()
print(f"Total diagnostic rows for Gre chat: {len(rows)}")

configs = {}
for r in rows:
    cfg = r[0]
    configs.setdefault(cfg, []).append(r)

print(f"Distinct configurations: {len(configs)}")
for cfg, cfg_rows in configs.items():
    successes = [r for r in cfg_rows if r[2] == 'SUCCESS']
    failures = [r for r in cfg_rows if r[2] == 'FAILED']
    print(f"\nConfig {cfg}: Total {len(cfg_rows)} rows | {len(successes)} SUCCESS | {len(failures)} FAILED")
    print("Chunks breakdown:")
    for r in sorted(cfg_rows, key=lambda x: int(x[1])):
        chunk_idx = r[1]
        st = r[2]
        err_cat = r[10] or 'NONE'
        err_msg = (r[12][:60] + '...') if r[12] else ''
        print(f"  Chunk {int(chunk_idx):02d}: {st} | {r[4]}s | tokens={r[8]} | cat={err_cat} | err={err_msg}")
conn.close()
