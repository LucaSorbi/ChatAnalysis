"""
tests/unit/test_ui_models.py
----------------------------
Test unitari per i DTO e modelli del layer UI (ui/models.py).
"""
from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from ui.models import (
    DatasetMode,
    DocumentSummary,
    EvidenceFilterCriteria,
    ImportedConversationInfo,
    IngestionRequest,
    IngestionResult,
    IngestionStatus,
    IngestionSummary,
    SourceFormat,
    TopicFilterDecision,
)


@pytest.mark.unit
class TestUiModels:

    def test_dataset_mode_enum(self):
        assert DatasetMode.NONE.value == "NONE"
        assert DatasetMode.DEMO.value == "DEMO"
        assert DatasetMode.FILE.value == "FILE"

    def test_source_format_enum(self):
        assert "msgstore" in SourceFormat.WHATSAPP_MSGSTORE.value
        assert "wa.db" in SourceFormat.WHATSAPP_WA.value
        assert "CSV" in SourceFormat.CELLEBRITE_CSV.value
        assert "JSON" in SourceFormat.CELLEBRITE_JSON.value
        assert "XML" in SourceFormat.CELLEBRITE_XML.value

    def test_topic_filter_decision_enum(self):
        assert TopicFilterDecision.ALL.value == "ALL"
        assert TopicFilterDecision.PRESENT.value == "PRESENT"
        assert TopicFilterDecision.ABSENT.value == "ABSENT"
        assert TopicFilterDecision.UNCERTAIN.value == "UNCERTAIN"

    def test_document_summary_immutable(self):
        summary = DocumentSummary(
            document_id="doc::test",
            bundle_count=5,
            section_count=10,
            counts_by_source_type={"ORIGINAL_TEXT": 8, "STT_TRANSCRIPTION": 2},
            languages=("it", "en"),
            topic_detection_count=2,
            topic_discovery_count=1,
        )
        assert summary.document_id == "doc::test"
        assert summary.bundle_count == 5
        assert summary.section_count == 10
        assert summary.counts_by_source_type["ORIGINAL_TEXT"] == 8
        assert summary.languages == ("it", "en")
        assert summary.topic_detection_count == 2
        assert summary.topic_discovery_count == 1

        with pytest.raises(FrozenInstanceError):
            summary.bundle_count = 10  # type: ignore

    def test_evidence_filter_criteria_defaults_and_immutable(self):
        crit = EvidenceFilterCriteria()
        assert crit.source_type is None
        assert crit.language is None
        assert crit.source_name is None

        crit_custom = EvidenceFilterCriteria(
            source_type="ORIGINAL_TEXT",
            language="it",
            source_name="demo_db",
        )
        assert crit_custom.source_type == "ORIGINAL_TEXT"
        assert crit_custom.language == "it"
        assert crit_custom.source_name == "demo_db"

        with pytest.raises(FrozenInstanceError):
            crit_custom.language = "en"  # type: ignore

    def test_ingestion_status_enum(self):
        assert IngestionStatus.SUCCESS.value == "SUCCESS"
        assert IngestionStatus.FAILED.value == "FAILED"
        assert IngestionStatus.AUXILIARY_ONLY.value == "AUXILIARY_ONLY"

    def test_ingestion_request_immutable(self):
        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename="msgstore.db",
            file_bytes=b"sample",
            companion_filename="wa.db",
            companion_bytes=b"companion",
        )
        assert req.source_format == SourceFormat.WHATSAPP_MSGSTORE
        assert req.filename == "msgstore.db"
        assert req.file_bytes == b"sample"
        assert req.companion_filename == "wa.db"
        assert req.companion_bytes == b"companion"

        with pytest.raises(FrozenInstanceError):
            req.filename = "other.db"  # type: ignore

    def test_imported_conversation_info_immutable(self):
        info = ImportedConversationInfo(
            document_id="doc::test::123",
            chat_id="chat_1",
            display_label="Chat 1 (5 msg)",
            bundle_count=5,
            section_count=10,
            languages=("it",),
            source_name="whatsapp_msgstore",
        )
        assert info.document_id == "doc::test::123"
        assert info.chat_id == "chat_1"
        assert info.bundle_count == 5
        assert info.section_count == 10
        assert info.languages == ("it",)

        with pytest.raises(FrozenInstanceError):
            info.bundle_count = 10  # type: ignore

    def test_ingestion_summary_and_result_immutable(self):
        info = ImportedConversationInfo(
            document_id="doc::test::123",
            chat_id="chat_1",
            display_label="Chat 1",
            bundle_count=2,
            section_count=2,
            languages=("it",),
            source_name="whatsapp",
        )
        summary = IngestionSummary(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            original_filename="msgstore.db",
            sha256="abcdef123456",
            file_size_bytes=1024,
            raw_record_count=10,
            validation_issue_count=0,
            normalized_record_count=10,
            unified_message_count=2,
            conversation_count=1,
            auxiliary_record_count=8,
            warnings=("test warning",),
            available_conversations=(info,),
            selected_document_id="doc::test::123",
            companion_filename="wa.db",
            companion_sha256="fedcba654321",
            status=IngestionStatus.SUCCESS,
        )
        assert summary.source_format == SourceFormat.WHATSAPP_MSGSTORE
        assert summary.conversations == (info,)
        assert summary.warnings == ("test warning",)
        assert summary.status == IngestionStatus.SUCCESS

        res = IngestionResult(
            summary=summary,
            documents={"doc::test::123": "dummy_doc"},
        )
        assert res.summary == summary
        assert "doc::test::123" in res.documents
        assert res.document_list == ("dummy_doc",)
        assert res.status == IngestionStatus.SUCCESS

        with pytest.raises(FrozenInstanceError):
            res.summary = None  # type: ignore

