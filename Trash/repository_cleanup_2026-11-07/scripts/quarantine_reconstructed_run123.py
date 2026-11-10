#!/usr/bin/env python3
"""
scripts/quarantine_reconstructed_run123.py
-----------------------------------------
Mette in quarantena rigorosa e verificabile la run artificiale 'run_123'
(config_fingerprint '8528f7452a1eefaa3c14c03c4185647128977a7c30a1800ef52d214eed87426b')
nel database .checkpoints/checkpoints.db.

OPERAZIONI ESEGUITE:
1. Copia forense integrale dei record nei registri di quarantena dedicati:
   - quarantine_reconstructed_checkpoints
   - quarantine_reconstructed_chunks
   - quarantine_reconstructed_diagnostics
2. Aggiornamento esplicito dell'origine e dello status a 'RECONSTRUCTED_UNVERIFIED'
   (nessuna attribuzione ingannevole a LIVE_LM_STUDIO o SUCCESS autentico).
3. Preservazione totale di tutti i dati storici senza cancellazioni fisiche.
"""

import sqlite3
import json
from pathlib import Path

def quarantine_run123(db_path: str = ".checkpoints/checkpoints.db"):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    cfg_fp = "8528f7452a1eefaa3c14c03c4185647128977a7c30a1800ef52d214eed87426b"
    
    # 1. Popola tabelle di quarantena
    cur.execute("DELETE FROM quarantine_reconstructed_checkpoints WHERE config_fingerprint=?", (cfg_fp,))
    cur.execute("INSERT INTO quarantine_reconstructed_checkpoints SELECT * FROM checkpoints WHERE config_fingerprint=?", (cfg_fp,))
    
    cur.execute("DELETE FROM quarantine_reconstructed_chunks WHERE config_fingerprint=?", (cfg_fp,))
    cur.execute("INSERT INTO quarantine_reconstructed_chunks SELECT * FROM checkpoint_chunks WHERE config_fingerprint=?", (cfg_fp,))
    
    cur.execute("DELETE FROM quarantine_reconstructed_diagnostics WHERE config_fingerprint=?", (cfg_fp,))
    cur.execute("INSERT INTO quarantine_reconstructed_diagnostics SELECT * FROM checkpoint_chunk_diagnostics WHERE config_fingerprint=?", (cfg_fp,))
    
    # 2. Aggiorna lo stato nelle tabelle attive per riflettere RECONSTRUCTED_UNVERIFIED
    cur.execute("UPDATE checkpoints SET origin='RECONSTRUCTED_UNVERIFIED' WHERE config_fingerprint=?", (cfg_fp,))

    
    cur.execute("SELECT chunk_index, serialized_result FROM checkpoint_chunks WHERE config_fingerprint=?", (cfg_fp,))
    chunks = cur.fetchall()
    for idx, s_res in chunks:
        p = json.loads(s_res)
        if "metadata" not in p:
            p["metadata"] = {}
        p["metadata"]["origin"] = "RECONSTRUCTED_UNVERIFIED"
        p["metadata"]["status"] = "RECONSTRUCTED_UNVERIFIED"
        p["metadata"]["reconstructed_flag"] = True
        p["metadata"]["provenance_note"] = "Quarantined by forensic audit 2026-10-27: topics compiled manually, not direct model payloads"
        new_res = json.dumps(p)
        cur.execute("UPDATE checkpoint_chunks SET serialized_result=? WHERE config_fingerprint=? AND chunk_index=?", (new_res, cfg_fp, idx))
        
    cur.execute('''
        UPDATE checkpoint_chunk_diagnostics 
        SET status='RECONSTRUCTED_UNVERIFIED', 
            diagnostic_category='RECONSTRUCTED_UNVERIFIED'
        WHERE config_fingerprint=?
    ''', (cfg_fp,))

    
    conn.commit()
    
    # Conteggi di verifica
    cur.execute("SELECT COUNT(*) FROM quarantine_reconstructed_chunks WHERE config_fingerprint=?", (cfg_fp,))
    q_chunks_cnt = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM quarantine_reconstructed_diagnostics WHERE config_fingerprint=?", (cfg_fp,))
    q_diags_cnt = cur.fetchone()[0]
    
    conn.close()
    print(f"[OK] Run run_123 ({cfg_fp[:12]}...) posta in quarantena con successo.")
    print(f"     Record in quarantena: {q_chunks_cnt} chunk, {q_diags_cnt} diagnostiche.")

if __name__ == "__main__":
    quarantine_run123()
