"""
scripts/diagnose_ubuntu_live_24of26.py
--------------------------------------
Script diagnostico di sola lettura per l'ambiente Ubuntu:
Ispezione forense sanitizzata del database SQLite .checkpoints/checkpoints.db
per l'audit della sessione Topic Discovery reale (24 SUCCESS, 2 FAILED: chunk 10 e 19).

UNIFICAZIONE DIAGNOSTICA FORENSE E PRIVACY (B5):
- Riutilizza rigorosamente il motore di sanitizzazione e le allowlist chiuse
  di scripts/diagnose_checkpoints_safe.py (zero duplicazione della logica di sicurezza).
- Sola lettura rigorosa (URI mode=ro, PRAGMA query_only = ON).
- ZERO LEAKAGE: model_id, diagnostic_category, failure_phase, finish_reason,
  timestamp e percorsi sono rigorosamente validati tramite allowlist chiuse prima
  di raggiungere stdout, stderr o il dizionario dei risultati.
- Nessun marcatore sintetico arbitrario o canary transita nei report esportabili.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

# Assicura che la root del repository sia nel PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Importazione autoritativa delle funzioni di sanitizzazione dal motore diagnostico unificato (B5)
from scripts.diagnose_checkpoints_safe import (
    _abbreviate_fp,
    _sanitize_analysis_type,
    _sanitize_diagnostic_category,
    _sanitize_doc_id,
    _sanitize_failure_phase,
    _sanitize_finish_reason,
    _sanitize_model_id,
    _sanitize_path,
    _sanitize_prompt_version,
    _sanitize_timestamp,
)


def _format_fp_safe(fp: str | None) -> str:
    """Mostra un fingerprint abbreviato delegando al formatter sicuro del motore diagnostico."""
    return _abbreviate_fp(fp, length=12)


def run_diagnostics(db_path: Path | str = ".checkpoints/checkpoints.db") -> dict[str, Any]:
    """
    Esegue l'ispezione forense sanitizzata del database SQLite, producendo un report privo di PII
    e privo di marcatori sintetici non approvati.
    """
    target_db = Path(db_path)
    if not target_db.is_file():
        safe_p = _sanitize_path(target_db)
        print(f"[ERRORE] File di database checkpoint non trovato: {safe_p}")
        return {"error": "database_not_found", "path": safe_p}

    safe_db_label = _sanitize_path(target_db)
    print("=" * 80)
    print("CHATANALYSIS — AUDIT DIAGNOSTICO FORENSE CHECKPOINT SQLITE (UBUNTU LIVE 24/26)")
    print(f"Target DB: {safe_db_label}")
    print("=" * 80)

    results: dict[str, Any] = {"path": safe_db_label}
    conn = sqlite3.connect(f"file:{target_db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON;")
        cur = conn.cursor()

        # 1. Ispezione tabelle e conteggi righe
        raw_tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        allowed_table_names = {"checkpoints", "checkpoint_chunks", "checkpoint_chunk_diagnostics", "checkpoint_subchunks"}
        tables = [t for t in raw_tables if t in allowed_table_names]
        print(f"\n[1] TABELLE PRESENTI: {tables}")
        row_counts = {t: cur.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
        for t, cnt in row_counts.items():
            print(f"    - {t}: {cnt} righe")
        results["row_counts"] = row_counts

        # 2. Ispezione testate checkpoint (checkpoints)
        print("\n[2] TESTATE DI CHECKPOINT REGISTRATE (checkpoints):")
        headers = cur.execute("""
            SELECT document_fingerprint, config_fingerprint, document_id,
                   analysis_type, model_id, prompt_version, total_planned_chunks,
                   created_at, updated_at
            FROM checkpoints
            ORDER BY updated_at DESC
        """).fetchall()
        results["checkpoints"] = []
        for h in headers:
            doc_fp, cfg_fp, doc_id, a_type, m_id, p_ver, t_chunks, c_at, u_at = h
            safe_model = _sanitize_model_id(m_id)
            item = {
                "document_fingerprint": _format_fp_safe(doc_fp),
                "config_fingerprint": _format_fp_safe(cfg_fp),
                "document_id": _sanitize_doc_id(doc_id),
                "analysis_type": _sanitize_analysis_type(a_type),
                "model_id": safe_model,
                "prompt_version": _sanitize_prompt_version(p_ver),
                "total_planned_chunks": int(t_chunks),
                "created_at": _sanitize_timestamp(c_at),
                "updated_at": _sanitize_timestamp(u_at),
            }
            results["checkpoints"].append(item)
            print(f"    • doc_id='{item['document_id']}' | model='{safe_model}' | chunks_previsti={t_chunks}")
            print(f"      doc_fp={item['document_fingerprint']} | cfg_fp={item['config_fingerprint']}")

        # 3. Ripartizione chunk completati per config_fingerprint
        print("\n[3] CHUNK COMPLETATI E SALVATI IN checkpoint_chunks:")
        cfg_chunks = cur.execute("""
            SELECT config_fingerprint, count(*), group_concat(chunk_index)
            FROM checkpoint_chunks
            GROUP BY config_fingerprint
            ORDER BY count(*) DESC
        """).fetchall()
        results["chunks_per_config"] = []
        for row in cfg_chunks:
            cfg_fp, cnt, indices_str = row
            indices = [int(x) for x in indices_str.split(",")] if indices_str else []
            safe_cfg = _format_fp_safe(cfg_fp)
            item = {
                "config_fingerprint": safe_cfg,
                "count": int(cnt),
                "indices": indices,
                "has_chunk_10": 10 in indices,
                "has_chunk_19": 19 in indices,
            }
            results["chunks_per_config"].append(item)
            print(f"    • cfg_fp={safe_cfg}: {cnt} chunk salvati")
            print(f"      Indici completati: {indices}")
            print(f"      Chunk 10 presente: {10 in indices} | Chunk 19 presente: {19 in indices}")

        # 4. Verifica specifica del Chunk 10 in tutte le configurazioni
        print("\n[4] VERIFICA CHUNK 10 TRA LE CONFIGURAZIONI:")
        c10_rows = cur.execute("""
            SELECT config_fingerprint, chunk_index, length(serialized_result), created_at
            FROM checkpoint_chunks
            WHERE chunk_index = 10
        """).fetchall()
        results["chunk_10_records"] = []
        for r in c10_rows:
            cfg_fp, c_idx, s_len, c_at = r
            safe_cfg = _format_fp_safe(cfg_fp)
            results["chunk_10_records"].append({
                "config_fingerprint": safe_cfg,
                "chunk_index": int(c_idx),
                "payload_bytes": int(s_len),
                "created_at": _sanitize_timestamp(c_at),
            })
            print(f"    • Chunk 10 VALIDO trovato sotto cfg_fp={safe_cfg} ({s_len} bytes)")
        if not c10_rows:
            print("    [!] Nessun record di Chunk 10 presente in checkpoint_chunks.")

        # 5. Ispezione diagnostica dei fallimenti (checkpoint_chunk_diagnostics)
        print("\n[5] DIAGNOSTICA DETTAGLIATA FALLIMENTI (checkpoint_chunk_diagnostics):")
        diag_rows = cur.execute("""
            SELECT config_fingerprint, chunk_index, chunk_id, status, attempts,
                   duration_seconds, model_id, http_status, finish_reason,
                   error_type, diagnostic_category, failure_phase, error_detail,
                   json_valid, schema_valid, evidence_valid, checkpoint_saved, updated_at
            FROM checkpoint_chunk_diagnostics
            WHERE status = 'FAILED' OR chunk_index IN (10, 19)
            ORDER BY chunk_index ASC, updated_at DESC
        """).fetchall()
        results["diagnostics_records"] = []
        for d in diag_rows:
            (
                cfg_fp, c_idx, c_id, st_val, att, dur, m_id, http_st, f_reason,
                err_type, diag_cat, fail_phase, err_detail, j_val, s_val, e_val, chk_sav, u_at
            ) = d
            safe_cfg = _format_fp_safe(cfg_fp)
            safe_model = _sanitize_model_id(m_id)
            safe_diag_cat = _sanitize_diagnostic_category(diag_cat)
            safe_fail_phase = _sanitize_failure_phase(fail_phase)
            safe_finish = _sanitize_finish_reason(f_reason)
            d_item = {
                "config_fingerprint": safe_cfg,
                "chunk_index": int(c_idx),
                "chunk_id": f"chunk_{c_idx}",
                "status": "FAILED" if str(st_val).upper() == "FAILED" else "SUCCESS",
                "attempts": int(att),
                "duration_seconds": round(float(dur), 2) if dur is not None else 0.0,
                "model_id": safe_model,
                "http_status": int(http_st) if http_st is not None else None,
                "finish_reason": safe_finish,
                "error_type": "ERROR" if err_type else None,
                "diagnostic_category": safe_diag_cat,
                "failure_phase": safe_fail_phase,
                "error_detail": f"[DIAGNOSTIC_CATEGORY={safe_diag_cat}]",
                "json_valid": bool(j_val),
                "schema_valid": bool(s_val),
                "evidence_valid": bool(e_val),
                "checkpoint_saved": bool(chk_sav),
                "updated_at": _sanitize_timestamp(u_at),
            }
            results["diagnostics_records"].append(d_item)
            print(f"    • Chunk {c_idx}: status={d_item['status']} | tentativi={att} | dur={d_item['duration_seconds']}s")
            print(f"      cfg_fp={safe_cfg} | HTTP={d_item['http_status']} | finish_reason={safe_finish}")
            print(f"      categoria={safe_diag_cat} | fase={safe_fail_phase}")
            print(f"      dettaglio=[DIAGNOSTIC_CATEGORY={safe_diag_cat}]")
            print(f"      validazione: json={bool(j_val)}, schema={bool(s_val)}, evidence={bool(e_val)}")

        # 6. Verifica sotto-blocchi (checkpoint_subchunks)
        print("\n[6] SOTTO-BLOCCHI IN checkpoint_subchunks:")
        sub_count = cur.execute("SELECT count(*) FROM checkpoint_subchunks").fetchone()[0]
        print(f"    Totale sotto-blocchi salvati: {sub_count}")
        results["subchunks_count"] = int(sub_count)

    finally:
        conn.close()

    print("\n" + "=" * 80)
    print("AUDIT DIAGNOSTICO COMPLETATO — SANITIZZATO CON ALLOWLIST RIGOROSA")
    print("=" * 80)
    return results


if __name__ == "__main__":
    db_arg = sys.argv[1] if len(sys.argv) > 1 else ".checkpoints/checkpoints.db"
    run_diagnostics(db_arg)
