#!/usr/bin/env python3
"""
scripts/backup_and_verify_26of26_checkpoint.py
---------------------------------------------
Preserva con backup consistente i risultati della run 26/26 e verifica l'integrità SQLite.
1. Esegue PRAGMA integrity_check e PRAGMA foreign_key_check sul database sorgente.
2. Esegue backup online atomico con conn.backup() verso:
   - checkpoints_archive_safety_26of26_run/checkpoints_26of26_backup.db
   - .checkpoints/checkpoints_backup_26of26_verified.db
3. Verifica l'integrità SQLite sui database di backup clonati.
4. Calcola gli hash crittografici SHA-256 e registra l'inventario esatto dei 26 chunk.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

def sha256_file(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def backup_and_verify():
    sys.stdout.reconfigure(encoding="utf-8")
    repo_root = Path(__file__).resolve().parent.parent
    src_db = repo_root / ".checkpoints" / "checkpoints.db"
    
    archive_dir = repo_root / "checkpoints_archive_safety_26of26_run"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest_archive_db = archive_dir / "checkpoints_26of26_backup.db"
    dest_local_backup = repo_root / ".checkpoints" / "checkpoints_backup_26of26_verified.db"

    print("=" * 80)
    print("CHATANALYSIS — PRESERVAZIONE E VERIFICA RUN DISCOVERY 26/26")
    print("=" * 80)
    print(f"[*] Database sorgente:      {src_db} ({src_db.stat().st_size} bytes)")
    print(f"[*] Backup di archivio:     {dest_archive_db}")
    print(f"[*] Backup locale verifica: {dest_local_backup}")

    # 1. Verifica integrità sorgente
    src_conn = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    src_cur = src_conn.cursor()
    
    src_integrity = src_cur.execute("PRAGMA integrity_check;").fetchall()
    print(f"[*] PRAGMA integrity_check sorgente: {src_integrity}")
    assert src_integrity == [("ok",)], f"Integrità sorgente fallita: {src_integrity}"

    src_fk = src_cur.execute("PRAGMA foreign_key_check;").fetchall()
    print(f"[*] PRAGMA foreign_key_check sorgente: {len(src_fk)} violazioni")
    assert len(src_fk) == 0, f"Violazioni FK sorgente: {src_fk}"

    # Ispezione checkpoint_chunk_diagnostics
    target_cfg = "8528f7452a1eefaa3c14c03c4185647128977a7c30a1800ef52d214eed87426b"
    src_cur.execute("""
        SELECT chunk_index, chunk_id, status, attempts, duration_seconds, prompt_tokens, completion_tokens, checkpoint_saved, updated_at
        FROM checkpoint_chunk_diagnostics
        WHERE config_fingerprint = ?
        ORDER BY chunk_index ASC
    """, (target_cfg,))
    diag_rows = src_cur.fetchall()
    print(f"[*] Diagnostiche registrate per run 26/26: {len(diag_rows)} record.")
    assert len(diag_rows) == 26, f"Attesi 26 record di diagnostica, trovati {len(diag_rows)}"
    for r in diag_rows:
        assert r[2] == "SUCCESS", f"Chunk {r[0]} non in SUCCESS: {r[2]}"
        assert r[7] == 1, f"Chunk {r[0]} checkpoint_saved != 1"

    print("    Tutti i 26 chunk (0..25) risultano registrati con status SUCCESS e checkpoint_saved=1.")

    # 2. Backup Atomico
    for dest_path in (dest_archive_db, dest_local_backup):
        if dest_path.exists():
            dest_path.unlink()
        dest_conn = sqlite3.connect(dest_path)
        with dest_conn:
            src_conn.backup(dest_conn)
        dest_conn.close()
        print(f"[OK] Backup atomico completato -> {dest_path.name} ({dest_path.stat().st_size} bytes)")

        # Verifica integrità sul database di destinazione
        verify_conn = sqlite3.connect(f"file:{dest_path}?mode=ro", uri=True)
        verify_cur = verify_conn.cursor()
        chk_integrity = verify_cur.execute("PRAGMA integrity_check;").fetchall()
        assert chk_integrity == [("ok",)], f"Integrità fallita su backup {dest_path}: {chk_integrity}"
        chk_count = verify_cur.execute("SELECT count(*) FROM checkpoint_chunk_diagnostics WHERE config_fingerprint = ?", (target_cfg,)).fetchone()[0]
        assert chk_count == 26, f"Conteggio diagnostica errato su {dest_path}: {chk_count}"
        verify_conn.close()

    src_conn.close()

    # 3. Calcolo Checksum SHA-256
    sha_src = sha256_file(src_db)
    sha_arch = sha256_file(dest_archive_db)
    sha_loc = sha256_file(dest_local_backup)

    print("\n--- HASH CRITTOGRAFICI SHA-256 ---")
    print(f"Source DB:        {sha_src}  {src_db.name}")
    print(f"Archive Backup:   {sha_arch}  {dest_archive_db.name}")
    print(f"Local Backup:     {sha_loc}  {dest_local_backup.name}")
    assert sha_arch == sha_loc, "Discrepanza tra cloni di backup"

    # Salva un file di manifesto del backup
    manifest = {
        "source_db": str(src_db.relative_to(repo_root)).replace("\\", "/"),
        "source_sha256": sha_src,
        "backup_archive_db": str(dest_archive_db.relative_to(repo_root)).replace("\\", "/"),
        "backup_archive_sha256": sha_arch,
        "backup_local_db": str(dest_local_backup.relative_to(repo_root)).replace("\\", "/"),
        "backup_local_sha256": sha_loc,
        "config_fingerprint": target_cfg,
        "total_chunks": 26,
        "all_chunks_success": True,
        "diagnostics_count": len(diag_rows),
        "sqlite_integrity_check": "ok",
        "sqlite_foreign_key_check": "passed_0_violations",
    }
    manifest_path = archive_dir / "backup_26of26_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[OK] Manifesto di backup registrato: {manifest_path}")

    print("\n>>> PRESERVAZIONE DELLA RUN 26/26 COMPLETATA CON SUCCESSO! <<<")
    return 0

if __name__ == "__main__":
    sys.exit(backup_and_verify())
