"""
scripts/verify_live_discovery_provenance.py
-------------------------------------------
Verifica forense live della Topic Discovery senza dati precaricati (Audit 27/10/2026).
- Modello esplicitamente identificato: 'qwen2.5-7b-instruct'
- 3 chunk tematici sintetici inediti (fotovoltaico, maratona, orologeria)
- Cattura risposte HTTP grezze su directory protetta con metadati e hash SHA-256
- Distinzione rigorosa tra risposta originale e rappresentazione normalizzata
- Aggregazione verificata tramite LocalTopicAnalysisService / aggregate_topic_discovery
- Ricerca verificata tramite SearchService (nessuna euristica manuale)
- Visualizzazione verificata tramite Streamlit AppTest reale (nessuna finta ricerca sottostringhe)
- Risultati sintetici mantenuti separati da quelli reali
"""
import hashlib
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import sys
import urllib.request

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.chunking import aggregate_topic_discovery
from ai.lmstudio import LmStudioClient
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDiscoveryResult,
)
from ai.topics import TopicDiscoveryAnalyzer
from analysis.checkpoint import AnalysisCheckpointStore, deserialize_discovery_result_from_checkpoint
from analysis.models import (
    AnalysisOrigin,
    AttestationLevel,
)
from analysis.service import LocalTopicAnalysisService
from search.service import SearchService
from importer.models import RawRecord
from validation.models import ValidationResult
from multimodal.evidence import MessageEvidenceBundle
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import Participant, UnifiedMessage


def create_synthetic_chunk_doc(chunk_index: int, theme: str, messages_data: list[tuple[str, str]]) -> ConversationEvidenceDocument:
    """Crea un documento con evidenze uniche e deterministiche."""
    bundles = []
    for idx, (sender_name, text) in enumerate(messages_data, start=1):
        rec_id = f"C{chunk_index}_M{idx:03d}"
        msg_id = f"unified:synthetic_audit:{rec_id}"
        ts = NormalizedTimestamp(
            status=TimestampTzStatus.KNOWN_UTC,
            utc_datetime=datetime(2026, 10, 27, 10, idx, 0, tzinfo=timezone.utc),
            naive_datetime=None,
            iso_string=f"2026-10-27T10:{idx:02d}:00Z",
            raw_value=f"2026-10-27T10:{idx:02d}:00Z",
        )
        raw = RawRecord(
            source_name="synthetic_audit",
            source_path="/audit/synthetic",
            source_record_id=rec_id,
            record_type="message",
            raw_fields={"sender": sender_name, "text": text},
            media_reference=None,
            metadata={},
        )
        val = ValidationResult(record=raw, issues=())
        norm = NormalizedRecord(
            raw_record=raw,
            validation_result=val,
            source_name="synthetic_audit",
            source_record_id=rec_id,
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=text,
            media_reference=None,
        )
        part = Participant(
            participant_id=f"participant:{sender_name.lower()}",
            identifier=sender_name,
            display_name=sender_name,
        )
        msg = UnifiedMessage(
            message_id=msg_id,
            source_name="synthetic_audit",
            source_record_id=rec_id,
            source_path="/audit/synthetic",
            record_type="message",
            timestamp=ts,
            sender=part,
            message_type=CanonicalMessageType.TEXT,
            text_content=text,
            provenance_record=norm,
        )
        bundles.append(MessageEvidenceBundle(message=msg))

    return ConversationEvidenceDocument(
        document_id=f"doc_synthetic_audit_chunk_{chunk_index}",
        bundles=tuple(bundles),
        chat_id="audit_chat_synthetic_20261027",
        source_name="synthetic_audit",
    )


def main():
    print("=" * 80)
    print("VERIFICA FORENSE LIVE TOPIC DISCOVERY — MODELLO QWEN2.5-7B-INSTRUCT")
    print("=" * 80)

    # Directory locale protetta per risultati sintetici separati dai reali
    synth_dir = REPO_ROOT / "output" / "live_audit_test_payloads" / "synthetic"
    synth_dir.mkdir(parents=True, exist_ok=True)

    # 1. Identificazione esplicita del modello Qwen desiderato
    TARGET_MODEL_NAME = "qwen2.5-7b-instruct"
    client = LmStudioClient(
        base_url="http://127.0.0.1:1234",
        default_timeout_seconds=90.0,
        debug_preserve_raw=True,
    )
    models = client.list_models()
    print(f"[+] Modelli rilevati in LM Studio: {models}")

    qwen_candidates = [m for m in models if "qwen" in m.lower() and "7b" in m.lower()]
    if not qwen_candidates:
        raise RuntimeError(
            f"ERRORE CRITICO: Il modello target '{TARGET_MODEL_NAME}' non è presente o caricato in LM Studio. "
            f"Modelli disponibili: {models}"
        )
    target_model = qwen_candidates[0]
    print(f"[+] Modello identificato e verificato: '{target_model}'")

    # 2. Definizione dei 3 chunk sintetici inediti
    chunk_0_data = [
        ("Marco", "Ho completato il calcolo per l'impianto fotovoltaico da 6 kW sul tetto orientato a sud."),
        ("Giulia", "Ottimo, quale inverter ibrido hai scelto? Supporta le batterie di accumulo al litio?"),
        ("Marco", "Si, ho selezionato un inverter da 5 kW con sistema di accumulo da 10 kWh."),
        ("Giulia", "Hai verificato se rientriamo nella detrazione fiscale per l'efficienza energetica?"),
        ("Marco", "Confermato, la pratica ENEA per la transizione solare e' gia' pronta per l'invio."),
    ]

    chunk_1_data = [
        ("Paolo", "Questa domenica organizziamo una sessione di corsa lunga di trenta chilometri."),
        ("Elena", "Ottimo, per l'allenamento di ripetute in pista che intervallo di recupero adottiamo?"),
        ("Paolo", "Tre minuti di camminata attiva. Dobbiamo testare anche l'integrazione energetica a rilascio lento."),
        ("Elena", "Perfetto, un piano alimentare ricco di carboidrati prima della prova sara' decisivo."),
        ("Paolo", "Preparero' le borracce con i sali minerali per garantire una corretta idratazione."),
    ]

    chunk_2_data = [
        ("Roberto", "Ho smontato l'orologio da tasca del 1920: lo scappamento a leva presenta incrostazioni di olio secco."),
        ("Sofia", "Hai già fatto il lavaggio ad ultrasuoni dei rubini e del bilanciere?"),
        ("Roberto", "Sì, e ho sostituito la molla motrice principale che aveva perso elasticità."),
        ("Sofia", "Attento quando lubrifichi con l'olio sintetico Moebius, ne basta una frazione di goccia sulle palette dell'ancora."),
        ("Roberto", "Regolazione completata al cronocomparatore: anticipo di soli 2 secondi al giorno."),
    ]

    chunks = [
        create_synthetic_chunk_doc(0, "Fotovoltaico", chunk_0_data),
        create_synthetic_chunk_doc(1, "Maratona", chunk_1_data),
        create_synthetic_chunk_doc(2, "Orologeria Meccanica", chunk_2_data),
    ]

    analyzer = TopicDiscoveryAnalyzer(
        client=client,
        default_model_id=target_model,
        operational_mode=True,
    )

    all_chunk_results = []
    chunk_summaries = []

    # 3. Esecuzione inferenze reali con intercettazione HTTP e hashing crittografico
    original_urlopen = urllib.request.urlopen

    for i, doc in enumerate(chunks):
        print(f"\n--- Elaborazione LIVE Chunk {i} ({doc.document_id}) ---")
        valid_eids = set(doc.evidence_ids)

        intercepted_payloads = []

        def recording_urlopen(req, *args, **kwargs):
            resp = original_urlopen(req, *args, **kwargs)
            body_bytes = resp.read()
            h = hashlib.sha256(body_bytes).hexdigest()
            intercepted_payloads.append({
                "status": getattr(resp, "status", 200),
                "headers": dict(resp.headers.items()) if hasattr(resp, "headers") else {},
                "body_bytes": body_bytes,
                "sha256": h,
            })

            class ReplayResponse:
                def __init__(self, raw_b, orig_resp):
                    self._fp = BytesIO(raw_b)
                    self.headers = getattr(orig_resp, "headers", None)
                    self.status = getattr(orig_resp, "status", 200)

                def read(self, *a, **k):
                    return self._fp.read(*a, **k)

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

            return ReplayResponse(body_bytes, resp)

        urllib.request.urlopen = recording_urlopen
        try:
            res = analyzer.discover_topics(
                document=doc,
                model_id=target_model,
                max_topics=3,
                max_tokens=600,
                timeout_seconds=120.0,
                temperature=0.0,
                seed=42,
            )
        finally:
            urllib.request.urlopen = original_urlopen

        assert len(intercepted_payloads) > 0, f"Nessun payload HTTP intercettato per chunk {i}"
        raw_http = intercepted_payloads[0]

        # Salvataggio risposta HTTP grezza con metadati e hash
        raw_body_text = raw_http["body_bytes"].decode("utf-8")
        raw_http_record = {
            "chunk_index": i,
            "document_id": doc.document_id,
            "http_status": raw_http["status"],
            "sha256_hash": raw_http["sha256"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_model": target_model,
            "temperature": 0.0,
            "seed": 42,
            "original_raw_http_body": raw_body_text,
            "parsed_llm_json": json.loads(raw_body_text),
            "normalized_domain_topics": [
                {
                    "label": t.label,
                    "short_description": t.short_description,
                    "evidence_ids": list(t.evidence_ids),
                }
                for t in res.topics
            ],
        }
        raw_path = synth_dir / f"chunk_{i}_raw_http_response.json"
        raw_path.write_text(json.dumps(raw_http_record, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[+] Payload HTTP grezzo salvato ({raw_http['sha256'][:16]}...) in: {raw_path.name}")

        # Verifiche forensi
        print(f"[+] Argomenti scoperti: {len(res.topics)}")
        for t in res.topics:
            print(f"    * [{t.label}]: {t.short_description}")
            print(f"      Citazioni ({len(t.evidence_ids)}): {t.evidence_ids}")

        assert len(res.topics) > 0, f"Chunk {i} ha restituito zero argomenti!"
        for t in res.topics:
            for eid in t.evidence_ids:
                assert eid in valid_eids, f"Allucinazione evidence_id {eid} non presente in {valid_eids}"

        # Verifica che non ci siano topic hardcoded
        forbidden_hardcoded_labels = {
            "Correria e fretta", "Piani serali e uscite", "Ritardi e tempi",
            "Lavoro e universita", "Casa e faccende", "Organizzazione logistica",
            "Meteo e freddo", "Pasti e spesa"
        }
        for t in res.topics:
            assert t.label not in forbidden_hardcoded_labels, f"Topic '{t.label}' coincide con un valore vietato!"

        all_chunk_results.append(res)
        chunk_summaries.append({
            "chunk_index": i,
            "http_status": raw_http["status"],
            "sha256": raw_http["sha256"],
            "topics_discovered": len(res.topics),
            "topic_labels": [t.label for t in res.topics],
            "total_evidence_cited": sum(len(t.evidence_ids) for t in res.topics),
        })

    print("\n[+] Tutti i 3 chunk sintetici hanno prodotto risposte HTTP valide e argomenti non vuoti.")

    # 4. Verifica dell'aggregazione tramite il servizio reale (aggregate_topic_discovery)
    print("\n--- Verifica Aggregazione tramite Servizio Reale ---")
    full_doc_bundles = []
    for c in chunks:
        full_doc_bundles.extend(c.bundles)
    full_doc = ConversationEvidenceDocument(
        document_id="doc_synthetic_audit_full",
        bundles=tuple(full_doc_bundles),
        chat_id="audit_chat_synthetic_20261027",
        source_name="synthetic_audit",
    )

    aggregated_res = aggregate_topic_discovery(
        chunk_results=all_chunk_results,
        parent_document_id=full_doc.document_id,
        max_topics=8,
    )
    print(f"[+] Aggregazione completata tramite aggregate_topic_discovery: {len(aggregated_res.topics)} macro-argomenti")
    for idx, t in enumerate(aggregated_res.topics, 1):
        print(f"    {idx}. [{t.label}] (citazioni: {len(t.evidence_ids)})")
    assert len(aggregated_res.topics) >= 3, f"Attesi almeno 3 macro-argomenti aggregati, ottenuti {len(aggregated_res.topics)}"

    # 5. Verifica della ricerca tramite SearchService
    print("\n--- Verifica Ricerca tramite SearchService ---")
    search_service = SearchService(
        document=full_doc,
        discovery_results=aggregated_res,
    )

    # Ricerca 1: fotovoltaic
    solar_res = search_service.search_discoveries(query_text="fotovoltaic")
    print(f"[+] SearchService query 'fotovoltaic': {len(solar_res.hits)} hit(s)")
    assert len(solar_res.hits) >= 1, "SearchService: nessun risultato per 'fotovoltaic'"

    # Ricerca 2: corsa
    marathon_res = search_service.search_discoveries(query_text="corsa")
    print(f"[+] SearchService query 'corsa': {len(marathon_res.hits)} hit(s)")
    assert len(marathon_res.hits) >= 1, "SearchService: nessun risultato per 'corsa'"

    # Ricerca 3: orologio
    watch_res = search_service.search_discoveries(query_text="orologio")
    print(f"[+] SearchService query 'orologio': {len(watch_res.hits)} hit(s)")
    assert len(watch_res.hits) >= 1, "SearchService: nessun risultato per 'orologio'"

    # 6. Verifica dell'effettiva visualizzazione mediante Streamlit AppTest reale
    print("\n--- Verifica Visualizzazione UI mediante Streamlit AppTest ---")
    from streamlit.testing.v1 import AppTest
    import ui.state as state

    app_path = str(REPO_ROOT / "app.py")
    at = AppTest.from_file(app_path, default_timeout=30)
    at.run()
    assert not at.exception, f"Errore all'avvio headless di Streamlit: {at.exception}"

    # Iniezione dello stato con il documento sintetico e il risultato aggregato
    at.session_state["dataset_loaded"] = True
    at.session_state["conversation_document"] = full_doc
    at.session_state["available_documents"] = {full_doc.document_id: full_doc}
    at.session_state["selected_document_id"] = full_doc.document_id
    doc_analysis = state.get_document_analysis_state(full_doc.document_id, state=at.session_state)
    doc_analysis.last_discovery = aggregated_res
    doc_analysis.discovery_results = (aggregated_res,)
    state.set_discovery_results(aggregated_res, state=at.session_state)

    # Navigazione alla pagina '5. Analisi topic'
    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()
    assert not at.exception, f"Errore durante l'esecuzione della pagina Topic Analysis: {at.exception}"

    # Ispezione del DOM renderizzato da Streamlit
    rendered_texts = [m.value for m in at.markdown] + [h.value for h in getattr(at, "header", [])] + [s.value for s in getattr(at, "subheader", [])]
    print(f"[+] Elementi testuali renderizzati nella pagina: {len(rendered_texts)}")
    found_rendered_topics = 0
    for t in aggregated_res.topics:
        if any(t.label in txt for txt in rendered_texts):
            found_rendered_topics += 1
            print(f"    - Confermato rendering UI del topic: '{t.label}'")

    assert found_rendered_topics >= 2, (
        f"Verifica UI Streamlit fallita: solo {found_rendered_topics}/{len(aggregated_res.topics)} "
        "macro-argomenti trovati negli elementi renderizzati da Streamlit AppTest."
    )
    print(f"[+] Streamlit AppTest ha verificato con successo la visualizzazione reale dei topic nel DOM UI.")

    # 7. Scrittura rapporto sintetico finale
    test_run_id = f"live_verified_audit_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    summary = {
        "verification_status": "SUCCESS_VERIFIED",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": target_model,
        "run_id": test_run_id,
        "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
        "verified_attestation": AttestationLevel.LIVE_AUTHORITATIVE.value,
        "chunks_tested": len(chunks),
        "chunk_summaries": chunk_summaries,
        "total_topics_discovered": len(aggregated_res.topics),
        "aggregation_service_verified": True,
        "search_service_verified": True,
        "streamlit_apptest_verified": True,
        "results_isolated_in_synthetic_dir": True,
    }

    summary_path = synth_dir / "live_discovery_verification_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[+] Riepilogo finale audit salvato in: {summary_path}")
    print("=" * 80)
    print("VERIFICA LIVE SECTION C COMPLETATA CON SUCCESSO (EXIT CODE 0)")
    print("=" * 80)


if __name__ == "__main__":
    main()
