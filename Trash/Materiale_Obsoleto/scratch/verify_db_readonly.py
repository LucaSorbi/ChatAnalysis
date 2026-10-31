import sqlite3
uri = "file:.checkpoints/checkpoints.db?mode=ro"
conn = sqlite3.connect(uri, uri=True)
cur = conn.cursor()

print("=== CHECKING CHECKPOINTS TABLE ===")
cur.execute("SELECT document_fingerprint, config_fingerprint, document_id, origin, run_id FROM checkpoints")
for row in cur.fetchall():
    print("Row:", row)

print("=== CHECKING CHECKPOINT_CHUNK_DIAGNOSTICS TABLE FOR RUN_123 ===")
cur.execute("SELECT status, count(*) FROM checkpoint_chunk_diagnostics GROUP BY status")
for row in cur.fetchall():
    print("Status count:", row)

print("=== CHECKING QUARANTINE TABLES ===")
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'quarantine%'")
tables = cur.fetchall()
print("Quarantine tables:", tables)

for t in tables:
    cur.execute(f"SELECT count(*) FROM {t[0]}")
    print(f"Table {t[0]} count: {cur.fetchone()[0]}")

cur.execute("SELECT chunk_index, status, error_detail FROM checkpoint_chunk_diagnostics LIMIT 3")
print("Diagnostics sample:")
for row in cur.fetchall():
    print(" ", row)

conn.close()
