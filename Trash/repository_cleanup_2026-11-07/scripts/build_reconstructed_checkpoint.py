#!/usr/bin/env python3
"""
scripts/build_reconstructed_checkpoint.py
-----------------------------------------
Crea e valida il nuovo database di checkpoint isolato in conformità agli OBIETTIVI 3 e 5:
- Directory separata e isolata: .checkpoints_reconstructed_verified_20261025
- NON tocca .checkpoints/checkpoints.db né i backup esistenti
- Recupera e valida solo i payload completi effettivamente verificati e integri (compreso chunk 25)
- Documenta l'interruzione del recupero per i payload non presenti nei log
- Esegue controlli di integrità SQLite, schema, consistenza evidenze e stato PARTIAL.
"""
import json
from pathlib import Path
import shutil
import sqlite3
import sys

from analysis.checkpoint import AnalysisCheckpointStore

def build_reconstructed_checkpoint():
    sys.stdout.reconfigure(encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[1]
    
    source_backup = repo_root / ".checkpoints_backup_20261025_regression" / "checkpoints_backup_20261025_204920.db"
    dest_dir = repo_root / ".checkpoints_reconstructed_verified_20261025"
    dest_db = dest_dir / "checkpoints.db"

    print("=" * 80)
    print("COSTRUZIONE E VERIFICA NUOVO CHECKPOINT ISOLATO")
    print("=" * 80)
    print(f"[*] Origine backup forense: {source_backup}")
    print(f"[*] Destinazione isolata:   {dest_db}")

    assert source_backup.exists(), f"Backup di origine non trovato: {source_backup}"

    dest_dir.mkdir(parents=True, exist_ok=True)
    if dest_db.exists():
        dest_db.unlink()

    # Copia controllata senza toccare né l'originale né il backup
    shutil.copy2(source_backup, dest_db)
    print(f"[*] Database clonato con successo ({dest_db.stat().st_size} bytes)")

    # 1. Verifica Integrità SQLite
    with sqlite3.connect(dest_db) as conn:
        cur = conn.cursor()
        integrity = cur.execute("PRAGMA integrity_check").fetchone()[0]
        print(f"[*] SQLite PRAGMA integrity_check: {integrity}")
        assert integrity == "ok", f"Errore integrità SQLite: {integrity}"

        fk_check = cur.execute("PRAGMA foreign_key_check").fetchall()
        print(f"[*] SQLite foreign_key_check: {len(fk_check)} violazioni")
        assert len(fk_check) == 0, f"Violazioni foreign key: {fk_check}"

        # 2. Verifica Metadati Run
        cur.execute("SELECT run_id, document_fingerprint, config_fingerprint, total_planned_chunks FROM checkpoints")
        run_row = cur.fetchone()
        print(f"[*] Run registrata: run_id={run_row[0]}, doc_fp={run_row[1][:16]}..., chunks_planned={run_row[3]}")
        assert run_row[3] == 26, f"Pianificati errati: {run_row[3]}"

        # 3. Verifica Chunk Validati Presenti
        cur.execute("SELECT chunk_index, length(serialized_result), serialized_result FROM checkpoint_chunks ORDER BY chunk_index")
        chunks = cur.fetchall()
        chunk_indices = [c[0] for c in chunks]
        print(f"[*] Chunk validati con payload integrale ({len(chunks)} chunk): {chunk_indices}")
        assert 25 in chunk_indices, "Chunk 25 NON presente nel checkpoint!"
        assert chunk_indices == [1, 3, 4, 9, 16, 18, 25], f"Indici inattesi: {chunk_indices}"

        # 4. Validazione Rigorosa Evidenze del Chunk 25
        chunk25_raw = [c[2] for c in chunks if c[0] == 25][0]
        chunk25_data = json.loads(chunk25_raw)
        topics = chunk25_data.get("topics", [])
        print(f"[*] Validazione Chunk 25: {len(topics)} topic estratti.")
        for t in topics:
            evs = t.get("evidence_ids", [])
            assert len(evs) > 0, f"Topic '{t.get('label')}' senza citazioni!"
            for ev in evs:
                assert ev.startswith("unified:whatsapp_export:msg_"), f"Evidence ID non conforme nel chunk 25: {ev}"

    # 5. Verifica Compatibilità con AnalysisCheckpointStore
    store = AnalysisCheckpointStore(checkpoint_dir=dest_dir)
    loaded_checkpoint = store.load_checkpoint(run_row[1], run_row[2])
    assert loaded_checkpoint is not None, "Checkpoint non caricato!"
    print(f"[*] AnalysisCheckpointStore.load_checkpoint() restituisce {len(loaded_checkpoint.completed_chunk_indices)} chunk validati: {loaded_checkpoint.completed_chunk_indices}")
    assert len(loaded_checkpoint.completed_chunk_indices) == 7

    # 6. Verifica dello Stato: Deve essere PARTIAL
    is_complete = len(loaded_checkpoint.completed_chunk_indices) == run_row[3]
    state_str = "SUCCESS" if is_complete else f"PARTIAL ({len(loaded_checkpoint.completed_chunk_indices)}/{run_row[3]})"
    print(f"[*] Stato calcolato del checkpoint: {state_str}")
    assert not is_complete, "ERRORE: Lo stato non deve essere SUCCESS per un'analisi incompleta!"
    assert state_str == "PARTIAL (7/26)"

    # 7. Documentazione Gap Forense
    missing_indices = sorted(set(range(26)) - set(chunk_indices))
    print(f"[*] Chunk con payload assente da ri-eseguire ({len(missing_indices)} chunk): {missing_indices}")
    print("\n[OK] TUTTE LE VERIFICHE SUPERATE NEL DATABASE ISOLATO.")
    print("=" * 80)

if __name__ == "__main__":
    build_reconstructed_checkpoint()
