"""
tests/unit/test_ui_application.py
---------------------------------
Test unitari per l'application layer della UI (ui/application.py).
"""
from __future__ import annotations

import pytest

from ai.models import TopicDecision
from search.models import MatchMode
from ui.application import (
    build_demo_session,
    execute_evidence_search,
    filter_evidence_sections,
    get_evidence_filter_options,
    get_system_status_info,
    search_all_topic_views,
    search_topic_detections,
    search_topic_discoveries,
    summarize_document,
)
from ui.models import EvidenceFilterCriteria, TopicFilterDecision


@pytest.mark.unit
class TestUiApplicationLayer:

    @pytest.fixture
    def demo_session(self):
        return build_demo_session()

    def test_build_demo_session(self, demo_session):
        doc, det, disc, service = demo_session
        assert doc is not None
        assert len(det) >= 3
        assert len(disc) >= 1
        assert service is not None
        assert len(service.index) == len(doc.all_evidence_sections)

    def test_summarize_document(self, demo_session):
        doc, det, disc, _ = demo_session
        summary = summarize_document(doc, det, disc)

        assert summary.document_id == "doc::demo_forensic_chat"
        assert summary.bundle_count == 8
        assert summary.section_count == len(doc.all_evidence_sections)
        assert summary.counts_by_source_type["ORIGINAL_TEXT"] >= 5
        assert summary.counts_by_source_type["STT_TRANSCRIPTION"] >= 2
        assert summary.counts_by_source_type["OCR_TEXT"] >= 1
        assert summary.counts_by_source_type["VISION_DESCRIPTION"] >= 1
        assert "it" in summary.languages
        assert "en" in summary.languages
        assert "es" in summary.languages
        assert summary.topic_detection_count == len(det)
        assert summary.topic_discovery_count >= 2

    def test_get_evidence_filter_options(self, demo_session):
        doc, _, _, _ = demo_session
        opts = get_evidence_filter_options(doc)

        assert "source_types" in opts
        assert "languages" in opts
        assert "source_names" in opts

        assert "TUTTI" in opts["source_types"]
        assert "ORIGINAL_TEXT" in opts["source_types"]
        assert "STT_TRANSCRIPTION" in opts["source_types"]

        assert "TUTTE" in opts["languages"]
        assert "it" in opts["languages"]
        assert "en" in opts["languages"]
        assert "es" in opts["languages"]

    def test_filter_evidence_sections(self, demo_session):
        doc, _, _, _ = demo_session

        # Nessun filtro: restituisce tutte le sezioni mantenendo l'ordine naturale
        all_crit = EvidenceFilterCriteria()
        res_all = filter_evidence_sections(doc, all_crit)
        assert len(res_all) == len(doc.all_evidence_sections)
        assert [s.evidence_id for s in res_all] == [s.evidence_id for s in doc.all_evidence_sections]

        # Filtro per source_type
        stt_crit = EvidenceFilterCriteria(source_type="STT_TRANSCRIPTION")
        res_stt = filter_evidence_sections(doc, stt_crit)
        assert len(res_stt) >= 2
        assert all(s.source_type.value == "STT_TRANSCRIPTION" for s in res_stt)

        # Filtro per lingua
        es_crit = EvidenceFilterCriteria(language="es")
        res_es = filter_evidence_sections(doc, es_crit)
        assert len(res_es) >= 1
        assert all(s.language == "es" for s in res_es)

    def test_execute_evidence_search_empty_query(self, demo_session):
        _, _, _, service = demo_session
        res = execute_evidence_search(service, "")
        assert res.total_hits == 0
        assert len(res.hits) == 0

        res_ws = execute_evidence_search(service, "   ")
        assert res_ws.total_hits == 0

    def test_execute_evidence_search_phrase(self, demo_session):
        _, _, _, service = demo_session
        res = execute_evidence_search(service, "Milano", match_mode=MatchMode.PHRASE)
        assert res.total_hits >= 1
        assert any("Milano" in h.original_text for h in res.hits)

    def test_execute_evidence_search_all_terms(self, demo_session):
        _, _, _, service = demo_session
        # "accordo riservato": presente sia nel msg 1 sia nel vocale msg 6
        res = execute_evidence_search(service, "accordo riservato", match_mode=MatchMode.ALL_TERMS)
        assert res.total_hits >= 1
        for hit in res.hits:
            assert "accordo" in hit.matched_terms
            assert "riservato" in hit.matched_terms

    def test_execute_evidence_search_limit_and_total_hits(self, demo_session):
        _, _, _, service = demo_session
        # Ricerca per parola comune come 'di' o 'la' presente in molte sezioni
        res_full = execute_evidence_search(service, "di", match_mode=MatchMode.ALL_TERMS)
        if res_full.total_hits > 1:
            res_lim = execute_evidence_search(service, "di", match_mode=MatchMode.ALL_TERMS, limit=1)
            assert len(res_lim.hits) == 1
            assert res_lim.total_hits == res_full.total_hits
            assert res_lim.metadata.get("truncated") is True
            assert res_lim.metadata.get("returned_hits") == 1

    def test_search_topic_detections_filtering(self, demo_session):
        _, _, _, service = demo_session

        # ALL
        res_all = search_topic_detections(service, decision_filter=TopicFilterDecision.ALL)
        assert res_all.total_hits == 3

        # PRESENT
        res_p = search_topic_detections(service, decision_filter=TopicFilterDecision.PRESENT)
        assert res_p.total_hits >= 1
        assert all(h.decision == TopicDecision.PRESENT for h in res_p.hits)

        # ABSENT
        res_a = search_topic_detections(service, decision_filter=TopicFilterDecision.ABSENT)
        assert res_a.total_hits >= 1
        assert all(h.decision == TopicDecision.ABSENT for h in res_a.hits)

        # UNCERTAIN
        res_u = search_topic_detections(service, decision_filter=TopicFilterDecision.UNCERTAIN)
        assert res_u.total_hits >= 1
        assert all(h.decision == TopicDecision.UNCERTAIN for h in res_u.hits)

        # Text search
        res_txt = search_topic_detections(service, query_text="Contrabbando")
        assert res_txt.total_hits >= 1
        assert any("Contrabbando" in h.label for h in res_txt.hits)

    def test_search_topic_discoveries(self, demo_session):
        _, _, _, service = demo_session
        res = search_topic_discoveries(service)
        assert res.total_hits >= 2

        res_filtered = search_topic_discoveries(service, query_text="Logistica")
        assert res_filtered.total_hits >= 1
        assert any("Logistica" in h.label for h in res_filtered.hits)

    def test_search_all_topic_views(self, demo_session):
        _, _, _, service = demo_session
        views = search_all_topic_views(service)
        assert len(views) >= 5  # 3 detections + 2 discoveries
        for v in views:
            assert v.display_text is not None
            assert len(v.display_text) > 0
            # Per topic, original_text deve essere None
            assert v.original_text is None
            assert v.result_type in ("TOPIC_DETECTION", "TOPIC_DISCOVERY")
            assert v.source_type is None

    def test_get_system_status_info(self):
        info = get_system_status_info(streamlit_version="1.63.0")
        assert info["search_layer"] == "READY"
        assert info["local_ai_architecture"] == "REAL PILOT READY"
        assert info["real_lm_studio_benchmark"] == "DEFERRED"
        assert "AMD A8-7410" in info["hardware_rationale"]
        assert "AVX2" in info["hardware_rationale"]
        assert info["streamlit_version"] == "1.63.0"
        assert "NOT REQUIRED" in info["network_status"]
        assert "NOT IMPLEMENTED" in info["semantic_search"]
        assert "NOT IMPLEMENTED" in info["embeddings"]
