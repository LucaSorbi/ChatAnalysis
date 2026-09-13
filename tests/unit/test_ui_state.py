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

        det = state.get_detection_results(s)
        assert len(det) >= 3

        disc = state.get_discovery_results(s)
        assert len(disc) >= 1

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

