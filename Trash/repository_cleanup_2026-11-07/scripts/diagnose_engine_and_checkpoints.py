import os
import sys
import sqlite3
import json
import subprocess
import urllib.request

def inspect_databases():
    print("=== INSPECTING CHECKPOINT DATABASES ===")
    dirs = [
        '.checkpoints',
        '.checkpoints_live_recovery_work',
        '.checkpoints_reconstructed_verified_20261025',
        '.checkpoints_backup_20261025_regression'
    ]
    for d in dirs:
        db_path = os.path.join(d, 'checkpoints.db')
        if not os.path.exists(db_path):
            print(f"Directory {d}: no checkpoints.db")
            continue
        print(f"\n--- Database: {db_path} ({os.path.getsize(db_path)} bytes) ---")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        print(f"Tables: {tables}")
        for t in tables:
            cur.execute(f"PRAGMA table_info({t})")
            cols = [r[1] for r in cur.fetchall()]
            cur.execute(f"SELECT count(*) FROM {t}")
            cnt = cur.fetchone()[0]
            print(f"  Table '{t}' ({cnt} rows): {cols}")
            
            # Print sample rows
            if 'checkpoints' in t:
                cur.execute(f"SELECT * FROM {t} LIMIT 10")
                rows = cur.fetchall()
                for r in rows:
                    print(f"    row: {r[:6]}")
        conn.close()

def inspect_lmstudio_version_and_runtime():
    print("\n=== INSPECTING LM STUDIO & LLAMA.CPP VERSION ===")
    # 1. LMS CLI
    try:
        res = subprocess.run(["lms", "version"], capture_output=True, text=True)
        print("lms version output:\n", res.stdout.strip() or res.stderr.strip())
    except Exception as e:
        print("lms version error:", e)

    # 2. LM Studio installation directory and logs
    home = os.path.expanduser("~")
    possible_dirs = [
        os.path.join(home, ".cache", "lm-studio"),
        os.path.join(home, ".lmstudio"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "LM-Studio"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "LM-Studio"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "lm-studio-backend"),
        os.path.join(os.environ.get("APPDATA", ""), "LM Studio"),
    ]
    for p in possible_dirs:
        if os.path.exists(p):
            print(f"Found path: {p}")
            # check for log files
            for root, dirs_list, files in os.walk(p):
                for f in files:
                    if f.endswith(".log") or "server" in f.lower() or "llama" in f.lower():
                        full_f = os.path.join(root, f)
                        try:
                            sz = os.path.getsize(full_f)
                            print(f"  Log/runtime file: {full_f} ({sz} bytes)")
                        except Exception:
                            pass

    # 3. Process details for port 1234
    try:
        netstat = subprocess.run("netstat -ano | findstr :1234", shell=True, capture_output=True, text=True)
        print("\nNetstat port 1234:\n", netstat.stdout.strip())
        lines = [line.strip() for line in netstat.stdout.splitlines() if "LISTENING" in line]
        if lines:
            pid = lines[0].split()[-1]
            print(f"Listening PID: {pid}")
            tasklist = subprocess.run(f"tasklist /FI \"PID eq {pid}\" /V /FO LIST", shell=True, capture_output=True, text=True)
            print("Tasklist:\n", tasklist.stdout.strip())
            wmic = subprocess.run(f"wmic process where processid={pid} get commandline,executablepath", shell=True, capture_output=True, text=True)
            print("Process Commandline & Path:\n", wmic.stdout.strip())
    except Exception as e:
        print("Error checking process:", e)

if __name__ == "__main__":
    inspect_databases()
    inspect_lmstudio_version_and_runtime()
