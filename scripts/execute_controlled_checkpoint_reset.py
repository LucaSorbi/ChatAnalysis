"""
scripts/execute_controlled_checkpoint_reset.py
----------------------------------------------
Executes the controlled reset of operational checkpoints in full accordance with Section 6:
1. Inventories existing database and WAL/SHM in .checkpoints.
2. Backs up database consistently using SQLite Backup API to an external archive directory:
   'checkpoints_archive_safety_pre_engine_reset_20261026/'
3. Verifies PRAGMA integrity_check on the backup.
4. Preserves all historical backups, datasets, benchmarks, and reconstructed DBs intact.
5. Initializes a clean, empty operational checkpoint database in .checkpoints/checkpoints.db.
6. Verifies that the new operational database is completely empty (0 chunks, 0 runs, 0 subchunks).
"""
import os
import sys
import sqlite3
import shutil
from pathlib import Path

# Ensure PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.checkpoint import AnalysisCheckpointStore

def main():
    print("=== EXECUTING CONTROLLED OPERATIONAL CHECKPOINT RESET ===")
    prod_dir = Path(".checkpoints").resolve()
    archive_dir = Path("checkpoints_archive_safety_pre_engine_reset_20261026").resolve()
    archive_dir.mkdir(parents=True, exist_ok=True)

    prod_db = prod_dir / "checkpoints.db"
    if not prod_db.exists():
        print(f"[WARN] No existing checkpoints.db found at {prod_db}")
        sys.exit(1)

    print(f"Operational DB path: {prod_db} ({prod_db.stat().st_size} bytes)")
    
    # 1. Inventory DB and WAL/SHM
    related_files = list(prod_dir.glob("checkpoints.db*"))
    print(f"Associated files: {[f.name for f in related_files]}")

    # 2. Consistent backup using SQLite Online Backup API
    backup_db_path = archive_dir / "checkpoints_pre_reset_backup.db"
    print(f"Creating consistent backup to: {backup_db_path}...")
    src_conn = sqlite3.connect(str(prod_db))
    dst_conn = sqlite3.connect(str(backup_db_path))
    with dst_conn:
        src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()

    # 3. Verify integrity of backup
    chk_conn = sqlite3.connect(str(backup_db_path))
    c = chk_conn.cursor()
    c.execute("PRAGMA integrity_check")
    res = c.fetchone()[0]
    assert res == "ok", f"Integrity check failed: {res}"
    c.execute("SELECT count(*) FROM checkpoint_chunks")
    saved_chunks = c.fetchone()[0]
    c.execute("SELECT count(*) FROM checkpoint_chunk_diagnostics")
    saved_diags = c.fetchone()[0]
    print(f"[OK] Backup verified: PRAGMA integrity_check = {res}")
    print(f"     Archived: {saved_chunks} completed chunks, {saved_diags} diagnostic records.")
    chk_conn.close()

    # Also copy any WAL/SHM or metadata if present
    for f in related_files:
        if f.name != "checkpoints.db":
            shutil.copy2(f, archive_dir / f.name)

    # 4. Remove old operational database and initialize clean empty store
    print("\nInitializing new clean operational checkpoint database...")
    for f in related_files:
        try:
            f.unlink()
            print(f"Removed operational file: {f.name}")
        except Exception as e:
            print(f"Error removing {f.name}: {e}")

    # Initialize store schema
    clean_store = AnalysisCheckpointStore(checkpoint_dir=str(prod_dir))
    
    # Verify completely empty
    verify_conn = sqlite3.connect(str(prod_db))
    vc = verify_conn.cursor()
    vc.execute("PRAGMA integrity_check")
    assert vc.fetchone()[0] == "ok"
    vc.execute("SELECT count(*) FROM checkpoints")
    assert vc.fetchone()[0] == 0, "Checkpoints table must have 0 rows"
    vc.execute("SELECT count(*) FROM checkpoint_chunks")
    assert vc.fetchone()[0] == 0, "Checkpoint_chunks table must have 0 rows"
    vc.execute("SELECT count(*) FROM checkpoint_chunk_diagnostics")
    assert vc.fetchone()[0] == 0, "Checkpoint_chunk_diagnostics table must have 0 rows"
    vc.execute("SELECT count(*) FROM checkpoint_subchunks")
    assert vc.fetchone()[0] == 0, "Checkpoint_subchunks table must have 0 rows"
    verify_conn.close()

    print("[SUCCESS] Operational checkpoint database successfully reset to clean state.")
    print("          Current state: 0/26 chunks, 0 runs, 0 subchunks.")
    print(f"          Safe backup preserved in: {archive_dir}")

if __name__ == "__main__":
    main()
