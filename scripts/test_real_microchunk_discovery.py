"""
Test di validazione reale mirata della Topic Discovery con microchunking preventivo e compatto.
Verifica sui chunk reali 0, 12 e 25 della conversazione di riferimento.
"""

import os
import sys
import time
import zipfile
import shutil
from pathlib import Path

# Assicura import da root del workspace
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.lmstudio import LmStudioClient
from ai.chunking import aggregate_topic_discovery, is_substantive_message_bundle
from analysis.checkpoint import AnalysisCheckpointStore
from analysis.progress import AnalysisProgressTracker
from analysis.service import LocalTopicAnalysisService
from ui.ingestion import ingest_file_payload


def run_targeted_real_microchunk_test():
    print("=" * 80)
    print("AVVIO TEST MIRATO DELLA DISCOVERY CON MICROCHUNKING PREVENTIVO SU CHUNK 0, 12, 25")
    print("=" * 80)

    # 1. Configura directory database isolata
    test_db_dir = Path("output/isolated_microchunk_test_db")
    reset_db = "--reset" in sys.argv
    if reset_db and test_db_dir.exists():
        shutil.rmtree(test_db_dir, ignore_errors=True)
    test_db_dir.mkdir(parents=True, exist_ok=True)

    chk_store = AnalysisCheckpointStore(checkpoint_dir=test_db_dir)
    print(f"[OK] Checkpoint store isolato inizializzato: {test_db_dir / 'checkpoints.db'} (reset={reset_db})")

    # 2. Ingestione documento reale da zip
    zip_path = Path("test_data/reference_chat_document.zip")
    if not zip_path.exists():
        raise FileNotFoundError(f"Archivio di test non trovato: {zip_path}")

    with zipfile.ZipFile(zip_path) as z:
        txt_names = [n for n in z.namelist() if n.endswith(".txt")]
        if not txt_names:
            raise FileNotFoundError("Nessun file .txt trovato nell'archivio di test.")
        raw_bytes = z.read(txt_names[0])

    from ui.models import SourceFormat
    ingestion_res = ingest_file_payload(raw_bytes, "reference_chat.txt", SourceFormat.WHATSAPP_EXPORT)
    doc = ingestion_res.document_list[0]
    print(f"[OK] Documento ingerito: id='{doc.document_id}', bundle={len(doc.bundles)}, evidenze={len(doc.all_evidence_sections)}")

    # 3. Pianificazione chunk autoritativa
    service = LocalTopicAnalysisService(
        base_url="http://127.0.0.1:1234",
        default_model="qwen2.5-7b-instruct",
        enable_checkpointing=True,
        checkpoint_store=chk_store,
    )
    plan = service.create_analysis_plan(doc, analysis_type="discovery", max_topics=10)
    all_chunks = list(plan.chunks)
    print(f"[OK] Piano creato: {len(all_chunks)} chunk totali pianificati.")

    target_indices = [0, 12, 25]
    target_chunks = {idx: all_chunks[idx] for idx in target_indices}

    for idx, c in target_chunks.items():
        substantive = sum(1 for b in c.bundles if is_substantive_message_bundle(b))
        print(f"  - Chunk {idx:02d}: {len(c.bundles)} bundle totali, {substantive} messaggi sostanziali, {len(c.all_evidence_sections)} evidenze.")

    # 4. Esecuzione inferenze reali chunk per chunk
    results_by_chunk = {}
    stats_by_chunk = {}

    for idx in target_indices:
        chunk = target_chunks[idx]
        print("\n" + "-" * 60)
        print(f"ESECUZIONE DISCOVERY REALE SUL CHUNK {idx}...")
        print("-" * 60)

        events = []
        def _on_progress(event):
            events.append(event)
            print(f"  [Progress] {event.phase} | completati: {event.completed_chunks}/{event.total_chunks}")

        t0 = time.perf_counter()
        disc_res = service.run_topic_discovery(
            document=chunk,
            model_name="qwen2.5-7b-instruct",
            max_topics=8,
            progress_callback=_on_progress,
        )
        elapsed = round(time.perf_counter() - t0, 3)

        # Validazione esito
        meta = disc_res.metadata
        chunk_outcomes = meta.get("chunk_outcomes", [])
        chunk_diags = meta.get("chunk_diagnostics", [])
        topics = disc_res.topics

        print(f"\n[RISULTATO CHUNK {idx}]:")
        print(f"  - Tempo impiegato: {elapsed} s")
        print(f"  - Status: {meta.get('status')}")
        print(f"  - Microchunk elaborati: {len(chunk_diags)}")
        print(f"  - Errori: {meta.get('error_count', 0)}")
        print(f"  - Topics estratti: {len(topics)}")

        assert meta.get("status") == "COMPLETE", f"Chunk {idx} non completato al 100%: status={meta.get('status')}"
        assert meta.get("error_count", 0) == 0, f"Chunk {idx} ha {meta.get('error_count')} errori non risolti!"

        # Verifica forense: tutte le evidenze citate appartengono al chunk originale
        chunk_eids = {s.evidence_id for s in chunk.all_evidence_sections}
        invalid_citations = []
        for t in topics:
            for eid in t.evidence_ids:
                if eid not in chunk_eids:
                    invalid_citations.append((t.label, eid))

        if invalid_citations:
            raise ValueError(f"Forense violata nel chunk {idx}! Citazioni inesistenti: {invalid_citations}")
        print(f"  [OK] Validazione forense superata: 100% citazioni autentiche ({sum(len(t.evidence_ids) for t in topics)} citazioni verificate).")

        # Verifica assenza di errori GBNF
        for d in chunk_diags:
            cat = d.get("diagnostic_category")
            if cat in ("GBNF_GRAMMAR_ERROR", "DEGENERATE_OUTPUT"):
                raise RuntimeError(f"Errore {cat} riscontrato nel microchunk {d.get('chunk_id')}!")

        for t_idx, t in enumerate(topics, 1):
            print(f"    Topic {t_idx}: '{t.label}' (evidenze: {len(t.evidence_ids)}) -> {t.short_description[:80]}...")

        results_by_chunk[idx] = disc_res
        stats_by_chunk[idx] = {
            "elapsed_seconds": elapsed,
            "microchunks": len(chunk_diags),
            "topics_count": len(topics),
            "status": meta.get("status"),
        }

    # 5. Consolidamento globale dei 3 chunk
    print("\n" + "=" * 80)
    print("CONSOLIDAMENTO GLOBALE DEI RISULTATI DEI 3 CHUNK...")
    print("=" * 80)
    consolidated = aggregate_topic_discovery(
        chunk_results=list(results_by_chunk.values()),
        parent_document_id=doc.document_id,
        max_topics=10,
    )
    print(f"[OK] Consolidamento completato con successo: {len(consolidated.topics)} argomenti globali:")
    all_target_eids = set()
    for c in target_chunks.values():
        all_target_eids.update(s.evidence_id for s in c.all_evidence_sections)

    for i, t in enumerate(consolidated.topics, 1):
        for eid in t.evidence_ids:
            if eid not in all_target_eids:
                raise ValueError(f"Consolidamento ha generato evidenza non autentica: {eid}")
        print(f"  {i}. '{t.label}': {len(t.evidence_ids)} evidenze | {t.short_description}")

    # 6. Prova di Ricarica e Idempotenza (zero inferenze)
    print("\n" + "=" * 80)
    print("VERIFICA DI PERSISTENZA E RICARICA (ZERO NUOVE INFERENZE)...")
    print("=" * 80)

    # Nuovo client con tracker per contare le richieste HTTP inviate
    fresh_service = LocalTopicAnalysisService(
        base_url="http://127.0.0.1:1234",
        default_model="qwen2.5-7b-instruct",
        enable_checkpointing=True,
        checkpoint_dir=test_db_dir,
    )

    for idx in target_indices:
        chunk = target_chunks[idx]
        reload_tracker = AnalysisProgressTracker(total_chunks=1)

        t0 = time.perf_counter()
        reloaded_res = fresh_service.run_topic_discovery(
            document=chunk,
            model_name="qwen2.5-7b-instruct",
            max_topics=8,
            tracker=reload_tracker,
        )
        t_reload = round(time.perf_counter() - t0, 3)

        rel_meta = reloaded_res.metadata
        print(f"  Chunk {idx} ricaricato in {t_reload}s | Richieste HTTP inviate: {reload_tracker.requests_sent} | Status: {rel_meta.get('status')}")
        assert reload_tracker.requests_sent == 0, f"Violazione idempotenza! Inviate {reload_tracker.requests_sent} richieste HTTP durante il reload del chunk {idx}"
        assert len(reloaded_res.topics) == len(results_by_chunk[idx].topics), "Discrepanza argomenti post-reload!"
        print(f"  [OK] Chunk {idx} recuperato al 100% da checkpoint senza nuove inferenze.")

    print("\n" + "=" * 80)
    print("TUTTE LE VERIFICHE SU CHUNK 0, 12, 25 SONO STATE COMPLETATE CON SUCCESSO!")
    print("=" * 80)
    return stats_by_chunk, results_by_chunk, consolidated


if __name__ == "__main__":
    run_targeted_real_microchunk_test()
