import sqlite3

conn = sqlite3.connect('.checkpoints/checkpoints.db')
c = conn.cursor()
c.execute("""
    SELECT chunk_index, status, checkpoint_saved, diagnostic_category, error_detail
    FROM checkpoint_chunk_diagnostics 
    WHERE config_fingerprint = '8528f7452a1eefaa3c14c03c4185647128977a7c30a1800ef52d214eed87426b' 
    ORDER BY chunk_index
""")
for r in c.fetchall():
    print(f"Chunk {r[0]:02d}: {r[1]} | chk_saved={r[2]} | cat={r[3]} | err={r[4]}")
conn.close()
