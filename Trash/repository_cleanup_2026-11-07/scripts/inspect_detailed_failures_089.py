import sqlite3

db = ".checkpoints/checkpoints.db"
conn = sqlite3.connect(db)
c = conn.cursor()
c.execute("""
    SELECT chunk_index, status, http_status, finish_reason, prompt_tokens, completion_tokens,
           diagnostic_category, error_type, error_detail
    FROM checkpoint_chunk_diagnostics 
    WHERE config_fingerprint = '089cb7bfe379b867574298e8dd1646bbb424bf972c5cdf3031771a7d8a50b73a'
    ORDER BY chunk_index
""")
for r in c.fetchall():
    print(f"\n================ CHUNK {r[0]:02d} ({r[1]}) ================")
    print(f"HTTP: {r[2]} | finish_reason: {r[3]} | prompt_tokens: {r[4]} | completion_tokens: {r[5]}")
    print(f"Category: {r[6]} | ErrorType: {r[7]}")
    print(f"Error Detail:\n{r[8]}")
conn.close()
