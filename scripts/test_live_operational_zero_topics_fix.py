#!/usr/bin/env python3
"""
scripts/test_live_operational_zero_topics_fix.py
------------------------------------------------
Verifica operativa LIVE contro LM Studio locale (http://127.0.0.1:1234)
su conversazione sintetica con temi inequivocabili:
1. Organizzazione di un viaggio
2. Preparazione di un esame
3. Allenamento sportivo

Dimostra end-to-end:
- Generazione topic coerenti e non vuoti
- Evidence ID autentici e validati
- Conservazione nell'aggregazione
- Ricerca corretta (total_discovered_topics vs matching hits)
- Persistenza durevole in SQLite (nessuna cancellazione da clear_checkpoint)
- Ripristino integrale dopo nuova istanza di LocalTopicAnalysisService
- Stabilità operativa su ripetizione multi-chunk
"""

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from datetime import datetime, timezone
from ai.lmstudio import LmStudioClient
from ai.models import ConversationEvidenceDocument
from analysis.checkpoint import AnalysisCheckpointStore, compute_document_fingerprint
from analysis.service import LocalTopicAnalysisService
from importer.models import RawRecord
from multimodal.evidence import MessageEvidenceBundle
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from search.index import EvidenceIndex
from search.service import SearchService
from ui.application import search_topic_discoveries
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def make_bundle(doc_id: str, idx: int, text: str) -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="synth_export",
        source_path="/synth/path",
        source_record_id=str(idx),
        record_type="message",
        raw_fields={"data": text, "timestamp": "2026-10-26T10:00:00Z"},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(
        status=TimestampTzStatus.KNOWN_UTC,
        utc_datetime=datetime(2026, 10, 26, 10, 0, 0, tzinfo=timezone.utc),
        naive_datetime=None,
        iso_string="2026-10-26T10:00:00Z",
        raw_value="2026-10-26T10:00:00Z",
    )
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name="synth_export",
        source_record_id=str(idx),
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"msg_{doc_id}_{idx:04d}",
        source_name="synth_export",
        source_record_id=str(idx),
        source_path="/synth/path",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    return MessageEvidenceBundle(message=msg)


def build_synthetic_document(doc_id: str = "doc_live_demo") -> ConversationEvidenceDocument:
    # 3 blocchi tematici con 3 messaggi ciascuno (totale 9 messaggi sostanziali)
    messages = [
        # Tema 1: Organizzazione di un viaggio a Madrid
        "Ciao Marco! Ho appena controllato i voli per il nostro viaggio a Madrid il mese prossimo.",
        "Ottimo, ho trovato un hotel in centro vicino a Puerta del Sol e al Museo del Prado.",
        "Perfetto! Prenotiamo subito i biglietti aerei Ryanair e prepariamo le valigie per il weekend.",
        # Tema 2: Preparazione esame di algoritmi
        "Per l'esame di algoritmi dobbiamo finire di studiare i grafi, Dijkstra e gli alberi binari di ricerca.",
        "Sì, ho fatto tutti gli esercizi pratici del laboratorio di informatica per la prova scritta.",
        "L'appello d'esame è fissato per il 15 del mese: ripassiamo insieme gli schemi e la complessità computazionale.",
        # Tema 3: Allenamento sportivo e maratona
        "Domattina usciamo presto per l'allenamento di corsa: dobbiamo fare 15 chilometri di fondo lento.",
        "Porto io le borracce e i gel energetici per la sessione di ripetute in pista atletica.",
        "Con questa tabella di allenamento sportivo saremo prontissimi per la mezza maratona di fine mese.",
    ]
    bundles = [make_bundle(doc_id, i + 1, msg) for i, msg in enumerate(messages)]
    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=tuple(bundles),
        source_name="synth_export",
        metadata={"data_classification": "SYNTHETIC_DEMO"},
    )


def run_live_operational_proof():
    print("=" * 80)
    print("PROVA OPERATIVA LIVE: VERIFICA RISOLUZIONE ZERO TOPICS SU LM STUDIO")
    print("=" * 80)

    client = LmStudioClient(base_url="http://127.0.0.1:1234", default_timeout_seconds=120.0)
    if not client.is_available():
        print("[-] LM Studio non disponibile su http://127.0.0.1:1234")
        sys.exit(1)

    models = client.list_models()
    print(f"[+] Modelli rilevati: {models}")
    active_model = models[0] if models else "qwen2.5-7b-instruct"
    print(f"[+] Modello attivo: {active_model}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        chk_dir = Path(tmp_dir)
        doc = build_synthetic_document("doc_synth_test_3chunks")
        print(f"[+] Documento sintetico creato con {len(doc.bundles)} messaggi.")

        # Configuriamo max_chunk_bundles=3 per creare esattamente 3 chunk (1 per tema)
        service = LocalTopicAnalysisService(checkpoint_dir=chk_dir, max_chunk_bundles=3, timeout_seconds=120.0)

        print("\n--- ESECUZIONE LIVE DISCOVERY (RUN 1: 3 CHUNK) ---")
        t0 = datetime.now()
        res1 = service.run_topic_discovery(document=doc, client=client, max_topics=6)
        elapsed1 = (datetime.now() - t0).total_seconds()

        print(f"[+] Discovery completata in {elapsed1:.2f}s")
        print(f"    - Status: {res1.metadata.get('status')}")
        print(f"    - Completed chunks: {res1.metadata.get('completed_chunk_count')}")
        print(f"    - Failed chunks: {res1.metadata.get('failed_chunk_count')}")
        print(f"    - Topic scoperti: {len(res1.topics)}")

        for idx, top in enumerate(res1.topics, 1):
            print(f"    Topic {idx}: '{top.label}' (evidenze: {top.evidence_ids})")

        print("    - Chunk Diagnostics:")
        for d in res1.metadata.get("chunk_diagnostics", []):
            print(f"      * Chunk {d.get('chunk_index')} ({d.get('chunk_id')}): status={d.get('status')}, thematic_status={d.get('thematic_status')}, error={d.get('error_detail')}")

        # 1. Verifica che i topic siano generati e non vuoti
        assert len(res1.topics) > 0, "ERRORE CRITICO: Discovery ha restituito 0 topic su testo sostanziale!"
        assert res1.metadata.get("status") == "COMPLETE", f"Status inatteso: {res1.metadata.get('status')}"

        # 2. Verifica evidence IDs autentici
        doc_eids = {sec.evidence_id for b in doc.bundles for sec in b.text_evidence_sections}
        for top in res1.topics:
            for eid in top.evidence_ids:
                assert eid in doc_eids, f"Evidence ID non autentico trovato: {eid}"
        print("[+] Tutti gli evidence IDs dei topic scoperti appartengono al documento.")

        # 3. Verifica persistenza in SQLite (il checkpoint NON deve essere cancellato)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)
        doc_fp = compute_document_fingerprint(doc)
        chk_meta = store.get_latest_checkpoint_metadata_for_document(doc.document_id, document_fingerprint=doc_fp)
        assert chk_meta is not None, "ERRORE CRITICO: Checkpoint cancellato dopo il completamento!"
        print(f"[+] Checkpoint SQLite intatto. Metadati: status={chk_meta.get('status')}, completed_chunks={chk_meta.get('completed_chunk_count')}")

        # 4. Verifica ripristino con NUOVA istanza service (simulazione riavvio Streamlit)
        fresh_service = LocalTopicAnalysisService(checkpoint_dir=chk_dir, max_chunk_bundles=3)
        recovered = fresh_service.load_persisted_discovery_result(doc)
        assert recovered is not None, "ERRORE: Impossibile ripristinare il risultato da SQLite!"
        assert len(recovered.topics) == len(res1.topics), "Discrepanza nel numero di topic ripristinati!"
        print(f"[+] Ripristino da SQLite verificato con successo: {len(recovered.topics)} topic caricati.")

        # 5. Verifica SearchService e UI Distinction
        idx = EvidenceIndex.from_document(doc)
        search_svc = SearchService(index=idx, discovery_results=(res1,))

        # Caso query vuota: tutti i topic
        s_all = search_topic_discoveries(search_svc, query_text="")
        assert s_all.metadata.get("total_discovered_topics") == len(res1.topics)
        assert len(s_all.hits) == len(res1.topics)
        print(f"[+] Ricerca query vuota: {len(s_all.hits)}/{s_all.metadata.get('total_discovered_topics')} argomenti visualizzati.")

        # Caso query specifica per un tema
        s_viaggio = search_topic_discoveries(search_svc, query_text="viaggio")
        assert s_viaggio.metadata.get("total_discovered_topics") == len(res1.topics)
        print(f"[+] Ricerca 'viaggio': {len(s_viaggio.hits)} corrispondenti su {s_viaggio.metadata.get('total_discovered_topics')} totali.")

        # Caso query senza match
        s_nomatch = search_topic_discoveries(search_svc, query_text="astronomia galattica")
        assert s_nomatch.metadata.get("total_discovered_topics") == len(res1.topics)
        assert len(s_nomatch.hits) == 0
        print(f"[+] Ricerca 'astronomia galattica' (nessun match): 0 corrispondenti su {s_nomatch.metadata.get('total_discovered_topics')} estratti totali (distinzione UI verificata).")

        # 6. Ripetizione per verificare stabilità operativa (Run 2 su nuova conversazione)
        print("\n--- ESECUZIONE LIVE DISCOVERY (RUN 2: RIPETIZIONE STABILITÀ) ---")
        doc2 = build_synthetic_document("doc_synth_test_run2")
        res2 = service.run_topic_discovery(document=doc2, client=client, max_topics=6)
        print(f"[+] Run 2 completata con successo: status={res2.metadata.get('status')}, topic scoperti={len(res2.topics)}")
        assert len(res2.topics) > 0, "Run 2 ha restituito 0 argomenti!"

        print("\n" + "=" * 80)
        print("TUTTE LE PROVE OPERATIVE LIVE HANNO AVUTO ESITO POSITIVO CON SUCCESSO!")
        print("=" * 80)


if __name__ == "__main__":
    run_live_operational_proof()
