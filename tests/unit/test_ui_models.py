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
