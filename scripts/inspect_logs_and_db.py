import sqlite3
import os
import glob

def check_dbs():
    print("=== CHECKPOINTS INVENTORY ===")
    for d in [
        '.checkpoints',
        '.checkpoints_live_recovery_work',
        '.checkpoints_reconstructed_verified_20261025',
        '.checkpoints_backup_20261025_regression'
    ]:
        db = os.path.join(d, 'checkpoints.db')
        if not os.path.exists(db):
            continue
        conn = sqlite3.connect(db)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in c.fetchall()]
        print(f"\n--- {db} ({os.path.getsize(db)} bytes) ---")
        for t in tables:
            c.execute(f"SELECT count(*) FROM {t}")
            cnt = c.fetchone()[0]
            print(f"Table '{t}': {cnt} rows")
            c.execute(f"PRAGMA table_info({t})")
            cols = [col[1] for col in c.fetchall()]
            print(f"  Columns: {cols}")
            c.execute(f"SELECT * FROM {t} LIMIT 5")
            for row in c.fetchall():
                # print truncated row
                str_row = [str(x)[:40] if x is not None else 'None' for x in row]
                print(f"    row: {str_row}")
        conn.close()

def check_lmstudio_logs():
    print("\n=== RECENT LM STUDIO SERVER LOGS (tail of today's log) ===")
    log_dir = os.path.expanduser(r"~/.lmstudio/server-logs/2026-10")
    logs = sorted(glob.glob(os.path.join(log_dir, "*.log")))
    for l in logs[-2:]:
        print(f"\n--- Log: {l} ({os.path.getsize(l)} bytes) ---")
        with open(l, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            print(f"Total lines: {len(lines)}")
            # Search for grammar, error, stack, exception
            err_lines = [line for line in lines if any(w in line.lower() for w in ["grammar", "stack", "exception", "error", "fail", "piece"])]
            print(f"Error-matching lines count: {len(err_lines)}")
            print("Last 15 error-matching lines:")
            for el in err_lines[-15:]:
                print("  ", el.strip()[:160])
            print("\nLast 15 lines of log overall:")
            for el in lines[-15:]:
                print("  ", el.strip()[:160])

if __name__ == "__main__":
    check_dbs()
    check_lmstudio_logs()
