import sqlite3
import json
from pathlib import Path

db_path = Path(".checkpoints/checkpoints.db")
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
cur = conn.cursor()

print("=" * 100)
print("1. RUN METADATA IN 'checkpoints'")
print("=" * 100)
cur.execute("SELECT * FROM checkpoints")
cols = [d[0] for d in cur.description]
rows = cur.fetchall()
for r in rows:
    row_dict = dict(zip(cols, r))
    for k, v in row_dict.items():
        print(f"  {k}: {v}")

print("\n" + "=" * 100)
print("2. CHUNKS IN 'checkpoint_chunks'")
print("=" * 100)
cur.execute("SELECT document_fingerprint, config_fingerprint, chunk_index, created_at, length(serialized_result) FROM checkpoint_chunks ORDER BY chunk_index")
chunks = cur.fetchall()
for c in chunks:
    print(f"Chunk #{c[2]}: created_at={c[3]}, serialized_bytes={c[4]}")

print("\n" + "=" * 100)
print("3. DIAGNOSTICS IN 'checkpoint_chunk_diagnostics'")
print("=" * 100)
cur.execute("SELECT * FROM checkpoint_chunk_diagnostics ORDER BY chunk_index")
cols = [d[0] for d in cur.description]
diags = cur.fetchall()

header = f"{'Chunk':<6} | {'Status':<7} | {'Attempts':<8} | {'HTTP':<5} | {'Finish':<7} | {'Tokens':<7} | {'Dur(s)':<8} | {'Phase':<12} | {'Category':<24} | {'ErrorType':<26}"
print(header)
print("-" * len(header))

error_categories = {}
failure_phases = {}

for d in diags:
    d_dict = dict(zip(cols, d))
    chunk_idx = d_dict.get('chunk_index')
    status = d_dict.get('status')
    attempts = d_dict.get('attempts')
    http_s = d_dict.get('http_status')
    finish = str(d_dict.get('finish_reason'))
    tokens = str(d_dict.get('completion_tokens'))
    dur = f"{d_dict.get('duration_seconds', 0.0):.2f}"
    phase = str(d_dict.get('failure_phase'))
    cat = str(d_dict.get('diagnostic_category'))
    err_t = str(d_dict.get('error_type'))
    err_det = str(d_dict.get('error_detail'))
    
    if status == 'FAILED':
        error_categories[cat] = error_categories.get(cat, 0) + 1
        failure_phases[phase] = failure_phases.get(phase, 0) + 1

    print(f"{chunk_idx:<6} | {status:<7} | {attempts:<8} | {http_s:<5} | {finish:<7} | {tokens:<7} | {dur:<8} | {phase:<12} | {cat:<24} | {err_t:<26}")

print("\n" + "=" * 100)
print("4. ERROR CATEGORIES BREAKDOWN (19 FAILURES)")
print("=" * 100)
for cat, count in error_categories.items():
    print(f"  * {cat}: {count} chunks")

print("\n" + "=" * 100)
print("5. FAILURE PHASES BREAKDOWN (19 FAILURES)")
print("=" * 100)
for phase, count in failure_phases.items():
    print(f"  * {phase}: {count} chunks")

print("\n" + "=" * 100)
print("6. DETAILS OF HTTP 400 AND INFERENCE ERRORS (NO PII)")
print("=" * 100)
for d in diags:
    d_dict = dict(zip(cols, d))
    if d_dict.get('status') == 'FAILED':
        print(f"Chunk #{d_dict.get('chunk_index')}: HTTP={d_dict.get('http_status')}, Phase={d_dict.get('failure_phase')}, "
              f"Cat={d_dict.get('diagnostic_category')}, ErrType={d_dict.get('error_type')}")
        det = str(d_dict.get('error_detail'))
        # Sanitize any potential PII from detail: only show first 120 chars if technical error
        if len(det) > 120:
            det = det[:120] + "..."
        print(f"   Detail: {det}")

conn.close()
