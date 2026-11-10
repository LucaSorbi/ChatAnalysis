import sqlite3
from pathlib import Path

db_path = Path("test_data/whatsapp_export/msgstore.db")
con = sqlite3.connect(db_path)
cur = con.cursor()
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables in msgstore.db:", len(tables), tables[:10])
for t in ["messages", "message", "chat"]:
    if t in tables:
        cnt = cur.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"Count in {t}: {cnt}")
