"""
scripts/execute_selective_live_recovery.py
-----------------------------------------
Esecuzione del recupero selettivo live dei chunk mancanti sulla copia isolata
del checkpoint (.checkpoints_live_recovery_work).

Requisiti forensi e di sicurezza:
1. Lavora ESCLUSIVAMENTE sulla copia isolata .checkpoints_live_recovery_work.
2. Recupera i 7 chunk validati [1, 3, 4, 9, 16, 18, 25] SENZA chiamate HTTP.
3. Esegue i chunk mancanti uno ad uno, preservando ogni avanzamento su SQLite.
4. ZERO dump di contenuti privati della conversazione nei log.
5. Registra finish_reason, completion_tokens, durata ed eventuali retry/subchunk.
"""
import os
import sys
import time
from pathlib import Path

# Imposta checkpoint dir isolata PRIMA di qualsiasi import applicativo
os.environ["CHATANALYSIS_CHECKPOINT_DIR"] = ".checkpoints_live_recovery_work"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.lmstudio import LmStudioClient
from analysis.checkpoint import compute_document_fingerprint
from analysis.service import LocalTopicAnalysisService
from ui.ingestion import ingest_file_payload
from ui.models import IngestionRequest, SourceFormat


def main():
    print("=" * 60)
    print("AVVIO RECUPERO SELETTIVO LIVE DEI CHUNK MANCANTI")
    print("=" * 60)

    # 1. Verifica disponibilità LM Studio
    client = LmStudioClient()
    if not client.is_available():
        print("[ERRORE FATALE] LM Studio non disponibile su http://127.0.0.1:1234")
        sys.exit(1)

    model_id = "qwen2.5-7b-instruct"
    print(f"[PREFLIGHT] Backend LM Studio online. Modello target: {model_id}")

    from scripts.export_discovery_26of26 import resolve_input_archive_path
    zip_path = resolve_input_archive_path()

    with open(zip_path, "rb") as f:
        raw_data = f.read()

    req = IngestionRequest(
        source_format=SourceFormat.WHATSAPP_EXPORT,
        filename=zip_path.name,
        file_bytes=raw_data,
    )
    ingest_res = ingest_file_payload(req)
    doc_id = ingest_res.summary.selected_document_id
    doc = ingest_res.documents[doc_id]

    doc_fp = compute_document_fingerprint(doc)
    print(f"[DOCUMENTO] Document ID: {doc.document_id}")
    print(f"[DOCUMENTO] Fingerprint SHA-256: {doc_fp}")
    print(f"[DOCUMENTO] Totale bundles: {len(doc.bundles)}, Evidenze: {len(doc.all_evidence_sections)}")

    expected_fp = "e78e13f0e01c4962912bcc3ee5cc1e4733388d952bf9fe140430dd04d5337478"
    if doc_fp != expected_fp:
        print(f"[ERRORE FATALE] Fingerprint non corrispondente! Atteso: {expected_fp}, Rilevato: {doc_fp}")
        sys.exit(1)

    # 3. Inizializzazione Service con checkpointing attivo su cartella di lavoro
    service = LocalTopicAnalysisService(
        base_url="http://127.0.0.1:1234",
        default_model=model_id,
        enable_checkpointing=True,
        checkpoint_dir=".checkpoints_live_recovery_work",
    )

    plan = service.create_analysis_plan(
        document=doc,
        analysis_type="discovery",
        max_topics=service.config.max_topics,
        context_length=service.context_length,
    )
    print(f"[PLAN] Totale chunk pianificati: {plan.planned_chunks_count}")

    # Callback di avanzamento per monitoraggio trasparente
    t_start = time.perf_counter()

    def on_progress(event):
        # Stampa solo metadati di progresso operativi, mai testo privato
        print(
            f" [PROCESSO] Chunk {event.completed_chunks}/{event.total_chunks} | "
            f"Percentuale: {event.percentage:.1f}% | Fase: {event.phase}",
            flush=True,
        )

    print("\n[ESECUZIONE] Avvio Topic Discovery con retry_failed_only=True...", flush=True)
    try:
        disc_result = service.run_topic_discovery(
            document=doc,
            client=client,
            model_name=model_id,
            max_topics=service.config.max_topics,
            temperature=0.0,
            seed=42,
            retry_failed_only=True,
            progress_callback=on_progress,
        )
        total_dur = round(time.perf_counter() - t_start, 2)
        print("\n" + "=" * 60)
        print(f"RECUPERO COMPLETATO IN {total_dur}s")
        print("=" * 60)
        print(f"Status finale: {disc_result.metadata.get('status')}")
        print(f"Chunk completati: {disc_result.metadata.get('completed_chunk_count')}/{disc_result.metadata.get('chunk_count')}")
        print(f"Errori residui: {disc_result.metadata.get('error_count')}")
        print(f"Topic totali scoperti: {len(disc_result.topics)}")

        # Verifica diagnostica dei chunk
        diags = disc_result.metadata.get("chunk_diagnostics", [])
        recovered_from_cp = sum(1 for d in diags if d.get("status") == "RECOVERED_FROM_CHECKPOINT")
        new_successes = sum(1 for d in diags if d.get("status") == "SUCCESS")
        failures = sum(1 for d in diags if d.get("status") == "FAILED")
        print(f"\nBreakdown diagnostico:")
        print(f" - Recuperati da checkpoint (0 HTTP calls): {recovered_from_cp}")
        print(f" - Nuovi chunk completati con successo: {new_successes}")
        print(f" - Chunk falliti: {failures}")

        for d in diags:
            c_idx = d.get("chunk_index")
            c_st = d.get("status")
            dur = d.get("duration_seconds")
            f_reason = d.get("finish_reason")
            c_tokens = d.get("completion_tokens")
            err = d.get("error_detail")
            if c_st == "FAILED":
                print(f"   [CHUNK {c_idx+1:02d}] FAILED | Durata: {dur}s | Errore: {err}")
            elif c_st == "RECOVERED_FROM_CHECKPOINT":
                print(f"   [CHUNK {c_idx+1:02d}] RECOVERED (0 HTTP requests)")
            else:
                print(f"   [CHUNK {c_idx+1:02d}] SUCCESS | Durata: {dur}s | finish_reason={f_reason} | tokens={c_tokens}")

    except Exception as e:
        print(f"\n[INTERRUZIONE O ECCEZIONE DURANTE IL RECUPERO]: {e}")
        import traceback
        traceback.print_exc()

    # Verifica finale integrità SQLite del checkpoint aggiornato
    import sqlite3
    conn = sqlite3.connect(".checkpoints_live_recovery_work/checkpoints.db")
    c_check = conn.execute("PRAGMA integrity_check;").fetchone()[0]
    total_saved = conn.execute("SELECT COUNT(*) FROM checkpoint_chunks;").fetchone()[0]
    sub_saved = conn.execute("SELECT COUNT(*) FROM checkpoint_subchunks;").fetchone()[0]
    conn.close()
    print(f"\n[INTEGRITÀ SQLITE FINALE]: {c_check} | Chunk salvati: {total_saved} | Subchunks: {sub_saved}")


if __name__ == "__main__":
    main()
