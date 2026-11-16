"""
tests/unit/test_ui_demo.py
--------------------------
Test unitari per il generatore del dataset sintetico dimostrativo (ui/demo.py).
"""
from __future__ import annotations

import pytest

from ai.models import TopicDecision
from multimodal.evidence import EvidenceSourceType
from search.index import EvidenceIndex
from search.topics import TopicSearchEngine
from ui.demo import build_synthetic_demo_dataset


@pytest.mark.unit
class TestUiDemoDataset:

    def test_build_synthetic_demo_dataset_structure(self):
        doc, detections, discoveries = build_synthetic_demo_dataset()

        assert doc.document_id == "doc::demo_forensic_chat"
        assert len(doc.bundles) >= 8
        assert len(doc.all_evidence_sections) >= 10
        assert len(detections) >= 3
        assert len(discoveries) >= 1

    def test_multilingual_coverage(self):
        doc, _, _ = build_synthetic_demo_dataset()
        languages: set[str] = set()
        for sec in doc.all_evidence_sections:
            if sec.language:
                languages.add(sec.language)

        # Deve coprire almeno 'it', 'en', 'es'
        assert "it" in languages
        assert "en" in languages
        assert "es" in languages

    def test_evidence_source_types_coverage(self):
        doc, _, _ = build_synthetic_demo_dataset()
        source_types = {sec.source_type for sec in doc.all_evidence_sections}

        assert EvidenceSourceType.ORIGINAL_TEXT in source_types
        assert EvidenceSourceType.STT_TRANSCRIPTION in source_types
        assert EvidenceSourceType.OCR_TEXT in source_types
        assert EvidenceSourceType.VISION_DESCRIPTION in source_types
        assert EvidenceSourceType.VISION_OBSERVATION in source_types

    def test_topic_decision_variety(self):
        _, detections, _ = build_synthetic_demo_dataset()
        decisions = {d.decision for d in detections}

        assert TopicDecision.PRESENT in decisions
        assert TopicDecision.ABSENT in decisions
        assert TopicDecision.UNCERTAIN in decisions

    def test_topic_discovery_content(self):
        _, _, discoveries = build_synthetic_demo_dataset()
        assert len(discoveries) >= 1
        all_topics = []
        for d in discoveries:
            all_topics.extend(d.topics)

        assert len(all_topics) >= 2
        labels = [t.label for t in all_topics]
        assert any("Logistica" in lbl for lbl in labels)
        assert any("Contratti" in lbl for lbl in labels)

    def test_all_evidence_ids_exist_and_resolve_strictly(self):
        doc, detections, discoveries = build_synthetic_demo_dataset()
        index = EvidenceIndex.from_document(doc)

        # Ogni evidence_id in detection deve esistere nell'indice
        for det in detections:
            assert det.provenance_document_id == doc.document_id
            for eid in det.evidence_ids:
                resolved = index.resolve(eid)
                assert resolved.evidence_id == eid

        # Ogni evidence_id in discovery deve esistere nell'indice
        for disc in discoveries:
            assert disc.provenance_document_id == doc.document_id
            for top in disc.topics:
                for eid in top.evidence_ids:
                    resolved = index.resolve(eid)
                    assert resolved.evidence_id == eid

        # Verifica assenza di errori in TopicSearchEngine
        engine = TopicSearchEngine(
            evidence_index=index,
            detection_results=detections,
            discovery_results=discoveries,
        )
        res_det = engine.search_detections()
        assert len(res_det.hits) == len(detections)

        res_disc = engine.search_discoveries()
        assert len(res_disc.hits) == sum(len(d.topics) for d in discoveries)
