"""
tests/unit/test_ui_state.py
---------------------------
Test unitari per la gestione dello stato della UI (ui/state.py).
"""
from __future__ import annotations

import pytest

from ui import state
from ui.models import DatasetMode


@pytest.mark.unit
class TestUiState:

    def test_init_session_state_defaults(self):
        s: dict = {}
        state.init_session_state(s)

        assert state.is_dataset_loaded(s) is False
        assert state.get_dataset_mode(s) == DatasetMode.NONE
        assert state.get_conversation_document(s) is None
        assert state.get_detection_results(s) == ()
        assert state.get_discovery_results(s) == ()
        assert state.get_search_service(s) is None
        assert state.get_selected_evidence_id(s) is None
        assert state.get_error_message(s) is None

    def test_load_and_reset_dataset(self):
        s: dict = {}
        state.init_session_state(s)

        # Caricamento demo
        state.load_demo_dataset(s)
        assert state.is_dataset_loaded(s) is True
        assert state.get_dataset_mode(s) == DatasetMode.DEMO

        doc = state.get_conversation_document(s)
        assert doc is not None
        assert doc.document_id == "doc::demo_forensic_chat"

        # Risultati live iniziali puliti (FASE 3)
        assert state.get_detection_results(s) == ()
        assert state.get_discovery_results(s) == ()

        # Risultati di riferimento sintetici accessibili per verifica
        ref_det = state.get_demo_reference_detection_results(s)
        assert len(ref_det) >= 3
        ref_disc = state.get_demo_reference_discovery_results(s)
        assert len(ref_disc) >= 1

        service = state.get_search_service(s)
        assert service is not None
        assert len(service.index) == len(doc.all_evidence_sections)

        # Reset sessione
        state.reset_dataset(s)
        assert state.is_dataset_loaded(s) is False
        assert state.get_dataset_mode(s) == DatasetMode.NONE
        assert state.get_conversation_document(s) is None
        assert state.get_detection_results(s) == ()
        assert state.get_discovery_results(s) == ()
        assert state.get_search_service(s) is None

    def test_selected_evidence_id(self):
        s: dict = {}
        state.init_session_state(s)
        assert state.get_selected_evidence_id(s) is None

        state.set_selected_evidence_id("msg::1::ORIGINAL_TEXT", s)
        assert state.get_selected_evidence_id(s) == "msg::1::ORIGINAL_TEXT"

        state.set_selected_evidence_id(None, s)
        assert state.get_selected_evidence_id(s) is None

    def test_error_message_handling(self):
        s: dict = {}
        state.init_session_state(s)
        assert state.get_error_message(s) is None

        state.set_error_message("Errore di validazione", s)
        assert state.get_error_message(s) == "Errore di validazione"

        state.clear_error_message(s)
        assert state.get_error_message(s) is None

    def test_real_ingestion_state_multi_conversation(self):
        from ai.models import ConversationEvidenceDocument
        from ui.models import (
            ImportedConversationInfo,
            IngestionResult,
            IngestionStatus,
            IngestionSummary,
            SourceFormat,
        )

        s: dict = {}
        state.init_session_state(s)

        doc1 = ConversationEvidenceDocument(
            document_id="doc::chat1",
            bundles=(),
            chat_id="chat1",
            source_name="whatsapp",
        )
        doc2 = ConversationEvidenceDocument(
            document_id="doc::chat2",
            bundles=(),
            chat_id="chat2",
            source_name="whatsapp",
        )

        cinfo1 = ImportedConversationInfo(
            document_id="doc::chat1",
            chat_id="chat1",
            display_label="Chat 1",
            bundle_count=1,
            section_count=1,
            languages=("en",),
            source_name="whatsapp",
        )
        cinfo2 = ImportedConversationInfo(
            document_id="doc::chat2",
            chat_id="chat2",
            display_label="Chat 2",
            bundle_count=1,
            section_count=1,
            languages=("en",),
            source_name="whatsapp",
        )

        summary = IngestionSummary(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            original_filename="msgstore.db",
            sha256="123456",
            file_size_bytes=2048,
            raw_record_count=2,
            validation_issue_count=0,
            normalized_record_count=2,
            unified_message_count=2,
            conversation_count=2,
            auxiliary_record_count=0,
            available_conversations=(cinfo1, cinfo2),
            selected_document_id="doc::chat1",
            status=IngestionStatus.SUCCESS,
        )

        res = IngestionResult(
            summary=summary,
            documents={"doc::chat1": doc1, "doc::chat2": doc2},
        )

        state.set_real_ingestion_result(res, s)

        assert state.is_dataset_loaded(s) is True
        assert state.get_dataset_mode(s) == DatasetMode.FILE
        assert state.get_selected_document_id(s) == "doc::chat1"
        assert state.get_conversation_document(s) == doc1
        assert state.get_detection_results(s) == ()
        assert state.get_discovery_results(s) == ()

        svc = state.get_search_service(s)
        assert svc is not None

        # Cambio conversazione
        switched = state.select_conversation("doc::chat2", s)
        assert switched is True
        assert state.get_selected_document_id(s) == "doc::chat2"
        assert state.get_conversation_document(s) == doc2
        assert state.get_search_service(s) is not None

        # Conversazione non esistente
        assert state.select_conversation("doc::unknown", s) is False

        # Reset pulisce tutto
        state.reset_dataset(s)
        assert state.is_dataset_loaded(s) is False
        assert state.get_real_ingestion_result(s) is None
        assert state.get_available_documents(s) == {}
        assert state.get_selected_document_id(s) is None
        assert state.get_ingestion_summary(s) is None

    def test_real_ingestion_auxiliary_only(self):
        from ui.models import (
            IngestionResult,
            IngestionStatus,
            IngestionSummary,
            SourceFormat,
        )

        s: dict = {}
        state.init_session_state(s)

        summary = IngestionSummary(
            source_format=SourceFormat.WHATSAPP_WA,
            original_filename="wa.db",
            sha256="654321",
            file_size_bytes=512,
            raw_record_count=4,
            validation_issue_count=0,
            normalized_record_count=4,
            unified_message_count=0,
            conversation_count=0,
            auxiliary_record_count=4,
            status=IngestionStatus.AUXILIARY_ONLY,
        )
        res = IngestionResult(summary=summary, documents={})

        state.set_real_ingestion_result(res, s)
        assert state.is_dataset_loaded(s) is True
        assert state.get_dataset_mode(s) == DatasetMode.FILE
        assert state.get_conversation_document(s) is None
        assert state.get_search_service(s) is None
        assert state.get_available_documents(s) == {}
        assert state.get_ingestion_summary(s) == summary

    def test_multi_conversation_preserves_analysis_results(self):
        """Verifica che cambiare conversazione (Chat A -> Chat B -> Chat A) preservi i risultati (FASE 25)."""
        from ai.models import (
            ConversationEvidenceDocument,
            TopicDecision,
            TopicDetectionResult,
            TopicQuery,
        )
        from ui.models import (
            ImportedConversationInfo,
            IngestionResult,
            IngestionStatus,
            IngestionSummary,
            SourceFormat,
        )

        s: dict = {}
        state.init_session_state(s)

        doc_a = ConversationEvidenceDocument(document_id="doc::chatA", bundles=(), chat_id="chatA", source_name="src")
        doc_b = ConversationEvidenceDocument(document_id="doc::chatB", bundles=(), chat_id="chatB", source_name="src")

        cinfo_a = ImportedConversationInfo(document_id="doc::chatA", chat_id="chatA", display_label="Chat A", bundle_count=0, section_count=0, languages=(), source_name="src")
        cinfo_b = ImportedConversationInfo(document_id="doc::chatB", chat_id="chatB", display_label="Chat B", bundle_count=0, section_count=0, languages=(), source_name="src")

        summary = IngestionSummary(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            original_filename="msgstore.db",
            sha256="123",
            file_size_bytes=100,
            raw_record_count=0,
            validation_issue_count=0,
            normalized_record_count=0,
            unified_message_count=0,
            conversation_count=2,
            auxiliary_record_count=0,
            available_conversations=(cinfo_a, cinfo_b),
            status=IngestionStatus.SUCCESS,
        )
        res = IngestionResult(summary=summary, documents={"doc::chatA": doc_a, "doc::chatB": doc_b})
        state.set_real_ingestion_result(res, s)

        # 1. Attivo Chat A ed eseguo una Topic Detection
        state.select_conversation("doc::chatA", s)
        det_a = TopicDetectionResult(
            topic=TopicQuery(topic_id="T_A", label="Topic Chat A", description="Desc A"),
            decision=TopicDecision.ABSENT,
            evidence_ids=(),
            rationale="Assente in A",
            provenance_document_id="doc::chatA",
        )
        state.add_detection_result(det_a, s)
        assert len(state.get_detection_results(s)) == 1
        assert state.get_detection_results(s)[0].topic.topic_id == "T_A"

        # 2. Passo a Chat B
        state.select_conversation("doc::chatB", s)
        # In Chat B i risultati di Chat A non devono apparire
        assert state.get_detection_results(s) == ()

        # Eseguo Topic Detection su Chat B
        det_b = TopicDetectionResult(
            topic=TopicQuery(topic_id="T_B", label="Topic Chat B", description="Desc B"),
            decision=TopicDecision.ABSENT,
            evidence_ids=(),
            rationale="Assente in B",
            provenance_document_id="doc::chatB",
        )
        state.add_detection_result(det_b, s)
        assert len(state.get_detection_results(s)) == 1
        assert state.get_detection_results(s)[0].topic.topic_id == "T_B"

        # 3. Torno a Chat A
        state.select_conversation("doc::chatA", s)
        # I risultati di Chat A devono essere ancora intatti!
        results_a = state.get_detection_results(s)
        assert len(results_a) == 1
        assert results_a[0].topic.topic_id == "T_A"

        # 4. Torno a Chat B
        state.select_conversation("doc::chatB", s)
        results_b = state.get_detection_results(s)
        assert len(results_b) == 1
        assert results_b[0].topic.topic_id == "T_B"

    def test_failed_ingestion_preserves_previous_dataset(self):
        """Verifica che un'ingestion FAILED non cancelli il dataset precedentemente caricato."""
        from ai.models import ConversationEvidenceDocument
        from ui.models import (
            ImportedConversationInfo,
            IngestionResult,
            IngestionStatus,
            IngestionSummary,
            SourceFormat,
        )

        s: dict = {}
        state.init_session_state(s)

        # 1. Carica un dataset iniziale con successo
        doc = ConversationEvidenceDocument(document_id="doc::chat1", bundles=(), chat_id="chat1", source_name="src")
        cinfo = ImportedConversationInfo(document_id="doc::chat1", chat_id="chat1", display_label="Chat 1", bundle_count=0, section_count=0, languages=(), source_name="src")
        summary_ok = IngestionSummary(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            original_filename="msgstore.db",
            sha256="111",
            file_size_bytes=100,
            raw_record_count=1,
            validation_issue_count=0,
            normalized_record_count=1,
            unified_message_count=1,
            conversation_count=1,
            auxiliary_record_count=0,
            available_conversations=(cinfo,),
            status=IngestionStatus.SUCCESS,
        )
        res_ok = IngestionResult(summary=summary_ok, documents={"doc::chat1": doc})
        state.set_real_ingestion_result(res_ok, s)

        assert state.is_dataset_loaded(s) is True
        assert state.get_selected_document_id(s) == "doc::chat1"

        # 2. Simula una successiva ingestion FALLITA
        summary_failed = IngestionSummary(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            original_filename="corrupted.db",
            sha256="000",
            file_size_bytes=10,
            raw_record_count=0,
            validation_issue_count=1,
            normalized_record_count=0,
            unified_message_count=0,
            conversation_count=0,
            auxiliary_record_count=0,
            status=IngestionStatus.FAILED,
        )
        res_failed = IngestionResult(
            summary=summary_failed,
            documents={},
            error_message="Formato file non valido o database corrotto",
        )
        state.set_real_ingestion_result(res_failed, s)

        # Il dataset precedente DEVE essere intatto e non cancellato!
        assert state.is_dataset_loaded(s) is True
        assert state.get_selected_document_id(s) == "doc::chat1"
        assert state.get_conversation_document(s) is doc
        assert state.get_error_message(s) == "Formato file non valido o database corrotto"


