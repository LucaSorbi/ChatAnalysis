"""
tests/integration/test_full_pipeline_e2e.py
-------------------------------------------
FULL E2E SYSTEM ACCEPTANCE TEST SUITE — PRE-HARDWARE GATE.

Verifiche E2E complete:
- C: Real Ingestion per tutti i 6 formati (msgstore, msgstore+wa, wa standalone, CSV, JSON, XML)
- D: Determinismo verificato sui dataset di test su reimport identico (stessi record, id, evidenze, query)
- E: Cross-format regression counts approvati
- F: Provenance Evidence invariants e assenza di derived evidence mascherata
- G: Multimodal handoff audit (nessun physical media processing fittizio su real file)
- H: Demo E2E (struttura multimodale completa, topic detection PRESENT/ABSENT/UNCERTAIN, discovery)
- I: Real file AI isolation (nessun LLM client istanziato, topic differiti)
- J: Search E2E su tutte le 4 modalità di matching (PHRASE, ALL_TERMS, ANY_TERM, EXACT) non-vacuo
- K: Session state transitions complete (NONE -> DEMO -> FILE -> FILE -> DEMO -> RESET)
- L: Failed import transactionality (il dataset precedente rimane intatto se l'import fallisce)
- M: Temp cleanup system test (eliminazione workspace verificata su success e failure, no path traversal)
- N: No external network required (nessuna connessione a reti esterne richiesta durante ingestion, search, UI)
- O: Reproducible labels (label pseudonimizzate, uniche e distinguibili fra conversazioni)
- P: Streamlit AppTest full acceptance walkthrough (nessuna eccezione su tutte le pagine e azioni)
- Q: Performance observation diagnostica (ingestion time, search time senza soglie bloccanti)
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import socket
import time
from typing import Any, Mapping
import pytest

from streamlit.testing.v1 import AppTest

from ai.models import (
    ConversationEvidenceDocument,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
)
from multimodal.evidence import EvidenceSourceType, MessageEvidenceBundle
from search.models import EvidenceSearchQuery, MatchMode
from search.service import SearchService
from ui import presentation, state
from ui.application import (
    build_demo_session,
    build_search_service_for_conversation,
    execute_evidence_search,
    execute_real_ingestion,
    filter_evidence_sections,
    get_evidence_filter_options,
    get_system_status_info,
    summarize_document,
)
from ui.ingestion import (
    IngestionError,
    InvalidUploadedFileError,
    PipelineStageError,
    UnsupportedSourceFormatError,
    derive_deterministic_document_id,
    ingest_file_payload,
)
from ui.models import (
    DatasetMode,
    EvidenceFilterCriteria,
    ImportedConversationInfo,
    IngestionRequest,
    IngestionResult,
    IngestionStatus,
    IngestionSummary,
    SourceFormat,
)

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "test_data"
MSGSTORE_FILE = FIXTURES_DIR / "whatsapp_export" / "msgstore.db"
WA_FILE = FIXTURES_DIR / "whatsapp_export" / "wa.db"
CELLEBRITE_CSV_FILE = FIXTURES_DIR / "cellebrite_export" / "messages.csv"
CELLEBRITE_JSON_FILE = FIXTURES_DIR / "cellebrite_export" / "messages.json"
CELLEBRITE_XML_FILE = FIXTURES_DIR / "cellebrite_export" / "report.xml"
APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")


@pytest.mark.integration
class TestFullPipelineE2E:

    # =========================================================================
    # C — E2E REAL INGESTION & CROSS-LAYER INTEGRATION
    # =========================================================================

    def test_e2e_whatsapp_msgstore(self):
        """Verifica la catena completa su WhatsApp msgstore.db."""
        assert MSGSTORE_FILE.exists()
        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_FILE.name,
            file_bytes=MSGSTORE_FILE.read_bytes(),
        )
        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.SUCCESS
        assert res.summary.raw_record_count == 622
        assert res.summary.unified_message_count == 504
        assert res.summary.conversation_count == 3
        assert len(res.documents) == 3

        for doc in res.documents.values():
            assert isinstance(doc, ConversationEvidenceDocument)
            assert doc.source_name == "msgstore_db"
            assert len(doc.bundles) > 0
            svc = build_search_service_for_conversation(doc)
            assert len(svc.index) == len(doc.all_evidence_sections)
            search_res = execute_evidence_search(svc, query_text="sintetico", match_mode=MatchMode.PHRASE)
            assert search_res.total_hits >= 0

    def test_e2e_whatsapp_msgstore_with_wa_companion(self):
        """Verifica la catena msgstore + wa.db companion con arricchimento contatti."""
        assert MSGSTORE_FILE.exists() and WA_FILE.exists()
        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_FILE.name,
            file_bytes=MSGSTORE_FILE.read_bytes(),
            companion_filename=WA_FILE.name,
            companion_bytes=WA_FILE.read_bytes(),
        )
        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.SUCCESS
        assert res.summary.companion_filename == "wa.db"
        assert res.summary.auxiliary_record_count == 122  # 118 msgstore aux + 4 wa.db contacts

        all_msgs = [b.message for doc in res.documents.values() for b in doc.bundles]
        named = [m for m in all_msgs if m.sender and m.sender.display_name]
        assert len(named) > 0, "I contatti di wa.db devono arricchire i display_name dei sender"

    def test_e2e_whatsapp_wa_standalone(self):
        """Verifica wa.db standalone: AUXILIARY_ONLY con 0 documenti conversazione."""
        assert WA_FILE.exists()
        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_WA,
            filename=WA_FILE.name,
            file_bytes=WA_FILE.read_bytes(),
        )
        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.AUXILIARY_ONLY
        assert res.summary.raw_record_count == 4
        assert res.summary.unified_message_count == 0
        assert res.summary.conversation_count == 0
        assert len(res.documents) == 0

    def test_e2e_cellebrite_csv(self):
        """Verifica la catena completa su Cellebrite messages.csv."""
        assert CELLEBRITE_CSV_FILE.exists()
        req = IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_CSV,
            filename=CELLEBRITE_CSV_FILE.name,
            file_bytes=CELLEBRITE_CSV_FILE.read_bytes(),
        )
        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.SUCCESS
        assert res.summary.raw_record_count == 302
        assert res.summary.unified_message_count == 302
        assert res.summary.conversation_count == 3
        for doc in res.documents.values():
            svc = build_search_service_for_conversation(doc)
            assert len(svc.index) > 0

    def test_e2e_cellebrite_json(self):
        """Verifica la catena completa su Cellebrite messages.json."""
        assert CELLEBRITE_JSON_FILE.exists()
        req = IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_JSON,
            filename=CELLEBRITE_JSON_FILE.name,
            file_bytes=CELLEBRITE_JSON_FILE.read_bytes(),
        )
        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.SUCCESS
        assert res.summary.raw_record_count == 100
        assert res.summary.unified_message_count == 100
        assert res.summary.conversation_count == 3

    def test_e2e_cellebrite_xml(self):
        """Verifica la catena completa su Cellebrite report.xml (partizione UNRESOLVED)."""
        assert CELLEBRITE_XML_FILE.exists()
        req = IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_XML,
            filename=CELLEBRITE_XML_FILE.name,
            file_bytes=CELLEBRITE_XML_FILE.read_bytes(),
        )
        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.SUCCESS
        assert res.summary.raw_record_count == 50
        assert res.summary.unified_message_count == 50
        assert res.summary.conversation_count == 1
        doc = next(iter(res.documents.values()))
        assert doc.metadata.get("is_unresolved") is True

    # =========================================================================
    # D — DETERMINISMO
    # =========================================================================

    def test_e2e_determinism_reimport_identical_results(self):
        """Importa due volte lo STESSO file e verifica identità al 100% (Gate D)."""
        req1 = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_FILE.name,
            file_bytes=MSGSTORE_FILE.read_bytes(),
        )
        req2 = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_FILE.name,
            file_bytes=MSGSTORE_FILE.read_bytes(),
        )

        res1 = execute_real_ingestion(req1)
        res2 = execute_real_ingestion(req2)

        # 1. Metriche globali identiche
        assert res1.summary.raw_record_count == res2.summary.raw_record_count
        assert res1.summary.unified_message_count == res2.summary.unified_message_count
        assert res1.summary.conversation_count == res2.summary.conversation_count
        assert res1.summary.sha256 == res2.summary.sha256

        # 2. Document IDs identici e nello stesso ordine
        doc_ids_1 = list(res1.documents.keys())
        doc_ids_2 = list(res2.documents.keys())
        assert doc_ids_1 == doc_ids_2
        assert len(doc_ids_1) == 3

        # 3. Label di conversazione identiche
        labels_1 = [c.display_label for c in res1.summary.conversations]
        labels_2 = [c.display_label for c in res2.summary.conversations]
        assert labels_1 == labels_2

        # 4. Evidence IDs e ordinamento identici per ciascuna conversazione
        for doc_id in doc_ids_1:
            d1 = res1.documents[doc_id]
            d2 = res2.documents[doc_id]
            assert d1.document_id == d2.document_id
            eids_1 = [s.evidence_id for s in d1.all_evidence_sections]
            eids_2 = [s.evidence_id for s in d2.all_evidence_sections]
            assert eids_1 == eids_2
            assert len(eids_1) == len(set(eids_1))  # tutti univoci

            # 5. Risultati della stessa EvidenceSearchQuery identici al 100%
            svc1 = build_search_service_for_conversation(d1)
            svc2 = build_search_service_for_conversation(d2)

            q_phrase = EvidenceSearchQuery(query_text="sintetico", match_mode=MatchMode.PHRASE)
            s_res1 = svc1.search_evidence(q_phrase)
            s_res2 = svc2.search_evidence(q_phrase)

            assert s_res1.total_hits == s_res2.total_hits
            assert [h.evidence_id for h in s_res1.hits] == [h.evidence_id for h in s_res2.hits]

    # =========================================================================
    # E — CROSS-FORMAT REGRESSION COUNTS
    # =========================================================================

    def test_e2e_cross_format_regression_counts(self):
        """Verifica i conteggi di riferimento approvati per tutte le sorgenti (Gate E)."""
        # WhatsApp msgstore
        r_msg = execute_real_ingestion(IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_FILE.name,
            file_bytes=MSGSTORE_FILE.read_bytes(),
        ))
        assert r_msg.summary.raw_record_count == 622
        assert r_msg.summary.unified_message_count == 504
        assert r_msg.summary.conversation_count == 3
        assert r_msg.summary.auxiliary_record_count == 118

        # WhatsApp wa.db standalone
        r_wa = execute_real_ingestion(IngestionRequest(
            source_format=SourceFormat.WHATSAPP_WA,
            filename=WA_FILE.name,
            file_bytes=WA_FILE.read_bytes(),
        ))
        assert r_wa.summary.raw_record_count == 4
        assert r_wa.summary.status == IngestionStatus.AUXILIARY_ONLY

        # Cellebrite CSV
        r_csv = execute_real_ingestion(IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_CSV,
            filename=CELLEBRITE_CSV_FILE.name,
            file_bytes=CELLEBRITE_CSV_FILE.read_bytes(),
        ))
        assert r_csv.summary.raw_record_count == 302
        assert r_csv.summary.unified_message_count == 302

        # Cellebrite JSON
        r_json = execute_real_ingestion(IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_JSON,
            filename=CELLEBRITE_JSON_FILE.name,
            file_bytes=CELLEBRITE_JSON_FILE.read_bytes(),
        ))
        assert r_json.summary.raw_record_count == 100
        assert r_json.summary.unified_message_count == 100

        # Cellebrite XML
        r_xml = execute_real_ingestion(IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_XML,
            filename=CELLEBRITE_XML_FILE.name,
            file_bytes=CELLEBRITE_XML_FILE.read_bytes(),
        ))
        assert r_xml.summary.raw_record_count == 50
        assert r_xml.summary.unified_message_count == 50
        doc_xml = next(iter(r_xml.documents.values()))
        assert doc_xml.metadata.get("is_unresolved") is True

    # =========================================================================
    # F — PROVENANCE EVIDENCE INVARIANTS
    # =========================================================================

    def test_e2e_provenance_and_evidence_invariants(self):
        """Verifica la piena coerenza della catena probatoria su ogni documento (Gate F)."""
        sources = [
            (SourceFormat.WHATSAPP_MSGSTORE, MSGSTORE_FILE),
            (SourceFormat.CELLEBRITE_CSV, CELLEBRITE_CSV_FILE),
            (SourceFormat.CELLEBRITE_JSON, CELLEBRITE_JSON_FILE),
            (SourceFormat.CELLEBRITE_XML, CELLEBRITE_XML_FILE),
        ]

        for s_fmt, s_file in sources:
            res = execute_real_ingestion(IngestionRequest(
                source_format=s_fmt,
                filename=s_file.name,
                file_bytes=s_file.read_bytes(),
            ))
            for doc in res.documents.values():
                for bundle in doc.bundles:
                    assert bundle.message is not None
                    assert bundle.message.message_id is not None
                    for sec in bundle.text_evidence_sections:
                        assert sec.message_id == bundle.message.message_id
                        assert sec.source_name == doc.source_name
                        assert sec.source_record_id == bundle.message.source_record_id
                        assert "::" in sec.evidence_id
                        # Nessun Derived Evidence deve essere presentato come ORIGINAL_TEXT
                        if sec.source_type == EvidenceSourceType.ORIGINAL_TEXT:
                            assert sec.text == bundle.message.text_content

    # =========================================================================
    # G — MULTIMODAL HANDOFF AUDIT
    # =========================================================================

    def test_e2e_multimodal_handoff_audit(self):
        """
        Verifica che nei dataset reali NON vengano generati artefatti multimodali fittizi
        quando i file fisici non sono forniti (Gate G).
        """
        res = execute_real_ingestion(IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_FILE.name,
            file_bytes=MSGSTORE_FILE.read_bytes(),
        ))

        # In msgstore ci sono 115 record con media_reference
        all_bundles = [b for doc in res.documents.values() for b in doc.bundles]
        media_bundles = [b for b in all_bundles if b.message.media_reference]
        assert len(media_bundles) == 115

        # Per tutti i documenti REAL FILE:
        # NON devono esistere sezioni derivate fittizie (STT, OCR, VISION)
        for doc in res.documents.values():
            for sec in doc.all_evidence_sections:
                assert sec.source_type == EvidenceSourceType.ORIGINAL_TEXT, (
                    f"Rilevata sezione {sec.source_type} non consentita in REAL FILE mode senza file fisico!"
                )

        # Al contrario, la modalità DEMO deve esporre tutti i 5 tipi probatori
        doc_demo, _, _, _ = build_demo_session()
        demo_types = {s.source_type for s in doc_demo.all_evidence_sections}
        assert EvidenceSourceType.ORIGINAL_TEXT in demo_types
        assert EvidenceSourceType.STT_TRANSCRIPTION in demo_types
        assert EvidenceSourceType.OCR_TEXT in demo_types
        assert EvidenceSourceType.VISION_DESCRIPTION in demo_types
        assert EvidenceSourceType.VISION_OBSERVATION in demo_types

    # =========================================================================
    # H — DEMO E2E
    # =========================================================================

    def test_e2e_demo_pipeline_and_precomputed_topics(self):
        """Verifica la sessione dimostrativa sintetica con Topic precomputati (Gate H)."""
        doc, detections, discoveries, service = build_demo_session()

        assert doc is not None
        assert len(doc.bundles) == 8
        assert len(doc.all_evidence_sections) == 12

        # Verifica presenza decisioni PRESENT, ABSENT, UNCERTAIN
        decisions = {d.decision for d in detections}
        assert TopicDecision.PRESENT in decisions
        assert TopicDecision.ABSENT in decisions
        assert TopicDecision.UNCERTAIN in decisions

        # Verifica Topic Discovery
        assert len(discoveries) >= 1
        assert len(discoveries[0].topics) >= 2

        # Risoluzione rigorosa di tutti gli evidence_ids nei topic
        for det in detections:
            for eid in det.evidence_ids:
                resolved = service.index.resolve(eid)
                assert resolved is not None
                assert resolved.evidence_id == eid

        for disc in discoveries:
            for top in disc.topics:
                for eid in top.evidence_ids:
                    resolved = service.index.resolve(eid)
                    assert resolved is not None
                    assert resolved.evidence_id == eid

    # =========================================================================
    # I — REAL FILE AI ISOLATION
    # =========================================================================

    def test_e2e_real_file_ai_isolation(self, monkeypatch):
        """Verifica che nessun client AI sia istanziato durante l'ingestion reale (Gate I)."""
        from ai import lmstudio, backend

        def forbidden_lmstudio(*args, **kwargs):
            pytest.fail("Istanziazione LmStudioClient non consentita in Real File mode!")

        def forbidden_fake_ai(*args, **kwargs):
            pytest.fail("Istanziazione FakeLocalLlmClient non consentita in Real File mode!")

        monkeypatch.setattr(lmstudio, "LmStudioClient", forbidden_lmstudio)
        monkeypatch.setattr(backend, "FakeLocalLlmClient", forbidden_fake_ai)

        res = execute_real_ingestion(IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_JSON,
            filename=CELLEBRITE_JSON_FILE.name,
            file_bytes=CELLEBRITE_JSON_FILE.read_bytes(),
        ))
        assert res.status == IngestionStatus.SUCCESS

        # Iniezione nello stato Streamlit e verifica che non ci siano risultati AI
        state_dict: dict[str, Any] = {}
        state.set_real_ingestion_result(res, state=state_dict)

        assert state.get_detection_results(state=state_dict) == ()
        assert state.get_discovery_results(state=state_dict) == ()

    # =========================================================================
    # J — SEARCH E2E
    # =========================================================================

    def test_e2e_search_across_formats_and_match_modes(self):
        """Verifica SearchService su tutti i formati con le 4 modalità di matching reali (Gate J)."""
        sources = [
            (SourceFormat.WHATSAPP_MSGSTORE, MSGSTORE_FILE),
            (SourceFormat.CELLEBRITE_CSV, CELLEBRITE_CSV_FILE),
            (SourceFormat.CELLEBRITE_JSON, CELLEBRITE_JSON_FILE),
            (SourceFormat.CELLEBRITE_XML, CELLEBRITE_XML_FILE),
        ]

        for s_fmt, s_file in sources:
            res = execute_real_ingestion(IngestionRequest(
                source_format=s_fmt,
                filename=s_file.name,
                file_bytes=s_file.read_bytes(),
            ))
            first_doc = next(iter(res.documents.values()))
            svc = build_search_service_for_conversation(first_doc)

            # 1. Selezione deterministica di una TextEvidenceSection non vuota per EXACT
            target_sec = next(s for s in first_doc.all_evidence_sections if s.text and s.text.strip())

            # Query con termini realmente presenti nelle fixture
            modes_and_queries = [
                (MatchMode.PHRASE, "test sintetico"),
                (MatchMode.ALL_TERMS, "Messaggio sintetico"),
                (MatchMode.ANY_TERM, "sintetico termine_inesistente_xyz"),
                (MatchMode.EXACT, target_sec.text),
            ]

            # Test delle 4 modalità di matching con verifiche non vacue e determinismo
            for mode, q_text in modes_and_queries:
                s_res1 = execute_evidence_search(svc, query_text=q_text, match_mode=mode)
                s_res2 = execute_evidence_search(svc, query_text=q_text, match_mode=mode)

                # Verifiche non vacue
                assert s_res1.total_hits > 0, f"Nessun hit per {mode} con query {q_text!r} su {s_fmt}"
                assert len(s_res1.hits) > 0
                assert len(s_res1.hits) <= s_res1.total_hits

                # Proprietà di ciascun hit restituito
                for hit in s_res1.hits:
                    assert "::" in hit.evidence_id
                    assert hit.source_name == first_doc.source_name
                    assert hit.source_record_id is not None
                    assert hit.original_text is not None
                    assert len(hit.original_text.strip()) > 0

                # Ordine deterministico identico su query ripetuta
                assert [h.evidence_id for h in s_res1.hits] == [h.evidence_id for h in s_res2.hits]

                # Per EXACT: la sezione target reale deve essere presente negli hit
                if mode == MatchMode.EXACT:
                    hit_eids = {h.evidence_id for h in s_res1.hits}
                    assert target_sec.evidence_id in hit_eids, (
                        f"L'evidence_id target {target_sec.evidence_id} non è presente negli hit EXACT"
                    )

    # =========================================================================
    # K — SESSION STATE TRANSITIONS
    # =========================================================================

    def test_e2e_session_state_transitions(self):
        """Verifica il ciclo di vita completo delle transizioni di stato (Gate K)."""
        s: dict[str, Any] = {}
        state.init_session_state(s)

        # 1. Stato iniziale vuoto
        assert not state.is_dataset_loaded(s)
        assert state.get_dataset_mode(s) == DatasetMode.NONE
        assert state.get_conversation_document(s) is None

        # 2. NONE -> DEMO
        state.load_demo_dataset(s)
        assert state.is_dataset_loaded(s)
        assert state.get_dataset_mode(s) == DatasetMode.DEMO
        assert state.get_conversation_document(s) is not None
        assert len(state.get_detection_results(s)) == 3
        assert len(state.get_discovery_results(s)) == 1
        assert state.get_search_service(s) is not None

        # 3. DEMO -> FILE (WhatsApp msgstore)
        res_msg = execute_real_ingestion(IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_FILE.name,
            file_bytes=MSGSTORE_FILE.read_bytes(),
        ))
        state.set_real_ingestion_result(res_msg, s)
        assert state.is_dataset_loaded(s)
        assert state.get_dataset_mode(s) == DatasetMode.FILE
        assert state.get_detection_results(s) == ()
        assert state.get_discovery_results(s) == ()
        doc_msg = state.get_conversation_document(s)
        assert doc_msg is not None
        assert doc_msg.source_name == "msgstore_db"

        # 4. FILE A -> FILE B (Cellebrite CSV)
        res_csv = execute_real_ingestion(IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_CSV,
            filename=CELLEBRITE_CSV_FILE.name,
            file_bytes=CELLEBRITE_CSV_FILE.read_bytes(),
        ))
        state.set_real_ingestion_result(res_csv, s)
        assert state.get_dataset_mode(s) == DatasetMode.FILE
        doc_csv = state.get_conversation_document(s)
        assert doc_csv is not None
        assert doc_csv.source_name == "cellebrite_csv"
        # SearchService deve riferirsi rigorosamente al nuovo documento
        current_svc = state.get_search_service(s)
        assert current_svc.index.document_id == doc_csv.document_id
        assert len(current_svc.index) == len(doc_csv.all_evidence_sections)
        assert all(sec.source_name == "cellebrite_csv" for sec in current_svc.index.all_sections)

        # 5. FILE -> DEMO
        state.load_demo_dataset(s)
        assert state.get_dataset_mode(s) == DatasetMode.DEMO
        assert state.get_real_ingestion_result(s) is None
        assert state.get_ingestion_summary(s) is None
        assert len(state.get_detection_results(s)) == 3

        # 6. DEMO -> RESET / NONE
        state.reset_dataset(s)
        assert not state.is_dataset_loaded(s)
        assert state.get_dataset_mode(s) == DatasetMode.NONE
        assert state.get_conversation_document(s) is None
        assert state.get_search_service(s) is None
        assert state.get_detection_results(s) == ()
        assert state.get_discovery_results(s) == ()
        assert state.get_ingestion_summary(s) is None

    # =========================================================================
    # L — FAILED IMPORT TRANSACTIONALITY
    # =========================================================================

    def test_e2e_failed_import_transactionality(self):
        """Verifica che un import fallito NON alteri il dataset precedentemente caricato (Gate L)."""
        s: dict[str, Any] = {}
        state.load_demo_dataset(s)
        orig_doc = state.get_conversation_document(s)
        assert orig_doc is not None

        # Tentativo di ingestion di file non valido
        bad_req = IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_JSON,
            filename="empty.json",
            file_bytes=b"",
        )

        with pytest.raises(InvalidUploadedFileError):
            # In ui/presentation.py, execute_real_ingestion viene chiamato prima di state.set_real_ingestion_result
            res = execute_real_ingestion(bad_req)
            state.set_real_ingestion_result(res, s)

        # La sessione precedente è rimasta intatta al 100%
        assert state.is_dataset_loaded(s)
        assert state.get_dataset_mode(s) == DatasetMode.DEMO
        assert state.get_conversation_document(s).document_id == orig_doc.document_id
        assert len(state.get_detection_results(s)) == 3

    # =========================================================================
    # M — TEMP CLEANUP SYSTEM TEST
    # =========================================================================

    def test_e2e_temp_cleanup_system_test(self):
        """Verifica che nessun file o cartella temporanea rimanga dopo success o failure (Gate M)."""
        import tempfile
        before_entries = set(Path(tempfile.gettempdir()).glob("forensic_ingest_*"))

        # Successo
        res = execute_real_ingestion(IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_JSON,
            filename=CELLEBRITE_JSON_FILE.name,
            file_bytes=CELLEBRITE_JSON_FILE.read_bytes(),
        ))
        after_success_entries = set(Path(tempfile.gettempdir()).glob("forensic_ingest_*"))
        assert after_success_entries == before_entries

        # Fallimento
        with pytest.raises(InvalidUploadedFileError):
            execute_real_ingestion(IngestionRequest(
                source_format=SourceFormat.WHATSAPP_MSGSTORE,
                filename="fake.db",
                file_bytes=b"not_a_sqlite_header",
            ))
        after_fail_entries = set(Path(tempfile.gettempdir()).glob("forensic_ingest_*"))
        assert after_fail_entries == before_entries

    # =========================================================================
    # N — NO EXTERNAL NETWORK REQUIRED
    # =========================================================================

    def test_e2e_no_network_system_wide(self, monkeypatch):
        """
        Verifica che l'intero sistema (ingestion, SearchService, application layer
        e navigazione completa Streamlit AppTest su tutte le 6 pagine) non richieda
        alcuna connessione verso reti esterne (Gate N: EXTERNAL NETWORK REQUIRED: NO).
        """
        import streamlit
        allowed_loopback = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}
        real_connect = socket.socket.connect

        def guarded_connect(sock, address):
            host = address[0] if isinstance(address, tuple) and len(address) > 0 else str(address)
            if host not in allowed_loopback:
                pytest.fail(f"Tentativo di connessione verso rete esterna bloccato: {address}")
            return real_connect(sock, address)

        monkeypatch.setattr(socket.socket, "connect", guarded_connect)

        # 1. Ingestion
        res = execute_real_ingestion(IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_FILE.name,
            file_bytes=MSGSTORE_FILE.read_bytes(),
        ))
        assert res.status == IngestionStatus.SUCCESS

        # 2. SearchService across conversations
        for doc in res.documents.values():
            svc = build_search_service_for_conversation(doc)
            s_res = execute_evidence_search(svc, query_text="sintetico", match_mode=MatchMode.PHRASE)
            assert s_res.total_hits > 0

        # 3. Application layer
        first_doc = next(iter(res.documents.values()))
        doc_summary = summarize_document(first_doc)
        assert doc_summary.bundle_count > 0
        sys_status = get_system_status_info(streamlit.__version__)
        assert sys_status["streamlit_version"] == streamlit.__version__

        # 4. Streamlit AppTest: startup and all 6 pages
        at = AppTest.from_file(APP_PATH, default_timeout=20)
        at.run()
        assert not at.exception

        pages = [
            "1. Panoramica",
            "2. Importazione",
            "3. Esplora conversazione",
            "4. Ricerca",
            "5. Analisi topic",
            "6. Sistema / Stato",
        ]
        for p in pages:
            at.sidebar.radio[0].set_value(p)
            at.run()
            assert not at.exception

    # =========================================================================
    # O — REPRODUCIBLE LABELS
    # =========================================================================

    def test_e2e_reproducible_and_distinguishable_labels(self):
        """Verifica che le label siano prive di dati personali, deterministiche e distinguibili (Gate O)."""
        res = execute_real_ingestion(IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_FILE.name,
            file_bytes=MSGSTORE_FILE.read_bytes(),
        ))
        convs = res.summary.conversations
        assert len(convs) == 3

        labels = [c.display_label for c in convs]
        # Tutte le label devono essere distinte
        assert len(set(labels)) == len(labels)

        # Gli short ID estratti tra le parentesi devono essere distinti
        short_ids = [l.split("[")[-1].rstrip("]") for l in labels]
        assert len(set(short_ids)) == len(short_ids)

        for idx, c in enumerate(convs, start=1):
            assert "@s.whatsapp.net" not in c.display_label
            assert "@g.us" not in c.display_label
            assert "+39" not in c.display_label
            assert "Gruppo_Sintetico" not in c.display_label
            assert c.display_label.startswith(f"Conversazione {idx} — ")

    # =========================================================================
    # P — STREAMLIT APPTEST FULL WALKTHROUGH
    # =========================================================================

    def test_e2e_streamlit_apptest_full_walkthrough(self):
        """Verifica l'intera interfaccia grafica Streamlit con AppTest (Gate P)."""
        at = AppTest.from_file(APP_PATH, default_timeout=20)
        at.run()
        assert not at.exception

        # Navigazione sequenziale tra tutte le pagine
        pages = [
            "1. Panoramica",
            "2. Importazione",
            "3. Esplora conversazione",
            "4. Ricerca",
            "5. Analisi topic",
            "6. Sistema / Stato",
        ]
        for p in pages:
            at.sidebar.radio[0].set_value(p)
            at.run()
            assert not at.exception, f"Crash rilevato durante la navigazione su {p}"

        # Carica demo mode
        at.sidebar.radio[0].set_value("2. Importazione")
        at.run()
        demo_btn = [b for b in at.button if "Carica Dataset Dimostrativo" in b.label][0]
        demo_btn.click()
        at.run()
        assert not at.exception

        # Panoramica demo
        at.sidebar.radio[0].set_value("1. Panoramica")
        at.run()
        assert not at.exception

        # Esplora conversazione demo
        at.sidebar.radio[0].set_value("3. Esplora conversazione")
        at.run()
        assert not at.exception

        # Ricerca demo
        at.sidebar.radio[0].set_value("4. Ricerca")
        at.run()
        assert not at.exception

        # Analisi topic demo
        at.sidebar.radio[0].set_value("5. Analisi topic")
        at.run()
        assert not at.exception

        # Iniezione stato REAL FILE con conversazioni reali
        res_real = execute_real_ingestion(IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_FILE.name,
            file_bytes=MSGSTORE_FILE.read_bytes(),
        ))
        state.set_real_ingestion_result(res_real, state=at.session_state)

        # Pagina 1: Panoramica Real File (verifica assenza JID grezzo nei top inputs)
        at.sidebar.radio[0].set_value("1. Panoramica")
        at.run()
        assert not at.exception
        top_inputs = [ti.value for ti in at.text_input]
        for v in top_inputs:
            assert "@s.whatsapp.net" not in v
            assert "@g.us" not in v

        # Pagina 2: Importazione Real File (selectbox presente e attiva)
        at.sidebar.radio[0].set_value("2. Importazione")
        at.run()
        assert not at.exception
        conv_sb = [sb for sb in at.selectbox if sb.key == "conv_selectbox"]
        assert len(conv_sb) == 1
        assert len(conv_sb[0].options) == 3

        # Click apri conversazione
        open_btn = [b for b in at.button if b.key == "open_conv_btn"][0]
        open_btn.click()
        at.run()
        assert not at.exception

        # Pagina 5: Analisi topic in Real File mode (mostra rinvio)
        at.sidebar.radio[0].set_value("5. Analisi topic")
        at.run()
        assert not at.exception
        assert len(at.info) >= 1
        assert "differito alla fase sperimentale finale" in at.info[0].value

        # Reset sessione
        at.sidebar.radio[0].set_value("1. Panoramica")
        at.run()
        reset_btns = [b for b in at.button if "Reimposta Sessione" in b.label]
        if reset_btns:
            reset_btns[0].click()
            at.run()
            assert not at.exception

    # =========================================================================
    # Q — PERFORMANCE OBSERVATION
    # =========================================================================

    def test_e2e_performance_observation(self):
        """Rilevazione diagnostica dei tempi di esecuzione per msgstore e ricerca (Gate Q)."""
        t0 = time.perf_counter()
        res = execute_real_ingestion(IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_FILE.name,
            file_bytes=MSGSTORE_FILE.read_bytes(),
        ))
        t1 = time.perf_counter()
        ingest_time = t1 - t0

        first_doc = next(iter(res.documents.values()))
        svc = build_search_service_for_conversation(first_doc)

        t2 = time.perf_counter()
        s_res = execute_evidence_search(svc, query_text="sintetico", match_mode=MatchMode.PHRASE)
        t3 = time.perf_counter()
        search_time = t3 - t2

        print(f"\n[DIAGNOSTICA PRE-HARDWARE] Ingestion msgstore.db (622 raw): {ingest_time:.3f} s")
        print(f"[DIAGNOSTICA PRE-HARDWARE] Search query lessicale ({s_res.total_hits} hits): {search_time:.4f} s")

        # Verifiche puramente funzionali: i tempi restano una mera osservazione diagnostica
        # per non legare l'esito PASS/FAIL alla frequenza o potenza della CPU host.
        assert res.status == IngestionStatus.SUCCESS
        assert len(res.documents) == 3
        assert len(svc.index) > 0
        assert s_res.total_hits > 0
        assert len(s_res.hits) > 0
