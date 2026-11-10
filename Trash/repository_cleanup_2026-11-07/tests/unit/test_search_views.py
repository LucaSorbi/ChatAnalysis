"""
tests/unit/test_search_views.py
-------------------------------
Test unitari per gli adapter di vista (search/views.py) e SearchViewResult (Point G):
- display_text valorizzato sia per EVIDENCE che per TOPIC
- original_text valorizzato ESCLUSIVAMENTE per EVIDENCE (uguale a display_text)
- original_text rigorosamente None per TOPIC_DETECTION e TOPIC_DISCOVERY
- Assenza assoluta di HTML o markup dipendente da Streamlit
- Preservazione della catena di provenance
- Batch conversion di sequenze di hit
"""
import pytest

from ai.models import TopicDecision
from multimodal.evidence import EvidenceSourceType, TextEvidenceSection
from search.models import EvidenceSearchHit, SearchViewResult, TopicSearchHit
from search.views import hit_to_view_result, hits_to_view_results


def _sample_evidence_hit() -> EvidenceSearchHit:
    sec = TextEvidenceSection(
        evidence_id="msg::10::ORIGINAL_TEXT",
        source_type=EvidenceSourceType.ORIGINAL_TEXT,
        text="Contenuto probatorio intatto",
        message_id="msg::10",
        source_name="msgstore_db",
        source_record_id="10",
        language="it",
    )
    return EvidenceSearchHit(
        evidence_id=sec.evidence_id,
        source_type=sec.source_type,
        message_id=sec.message_id,
        source_name=sec.source_name,
        source_record_id=sec.source_record_id,
        language="it",
        original_text=sec.text,
        section=sec,
        matched_terms=("probatorio",),
    )


@pytest.mark.unit
class TestSearchViews:

    def test_evidence_hit_to_view_result(self):
        hit = _sample_evidence_hit()
        view = hit_to_view_result(hit)

        assert isinstance(view, SearchViewResult)
        assert view.result_type == "EVIDENCE"
        assert view.display_text == "Contenuto probatorio intatto"
        assert view.original_text == "Contenuto probatorio intatto"
        assert view.evidence_id == "msg::10::ORIGINAL_TEXT"
        assert view.source_type == EvidenceSourceType.ORIGINAL_TEXT
        assert view.language == "it"
        assert view.topic_decision is None
        assert view.provenance["source_name"] == "msgstore_db"
        assert view.provenance["matched_terms"] == ("probatorio",)

        # Nessun HTML presente
        assert "<" not in view.title
        assert ">" not in view.title
        assert "<" not in view.display_text

    def test_topic_hit_to_view_result_detection(self):
        hit = TopicSearchHit(
            topic_id="T01",
            label="Evasione",
            description="Presunta evasione dell'IVA",
            source_kind="TOPIC_DETECTION",
            decision=TopicDecision.PRESENT,
            evidence_ids=("msg::1::ORIGINAL_TEXT",),
            matched_sections=(),
            metadata={"rationale": "Fatture anomale"},
        )
        view = hit_to_view_result(hit)

        assert view.result_type == "TOPIC_DETECTION"
        assert "Evasione" in view.title
        assert "[PRESENT]" in view.title
        # display_text contiene la descrizione, original_text è rigorosamente None
        assert view.display_text == "Presunta evasione dell'IVA"
        assert view.original_text is None
        assert view.topic_decision == TopicDecision.PRESENT
        assert view.evidence_ids == ("msg::1::ORIGINAL_TEXT",)
        assert view.provenance["topic_id"] == "T01"

    def test_topic_hit_to_view_result_discovery(self):
        hit = TopicSearchHit(
            topic_id="Logistica",
            label="Logistica",
            description="Descrizione scoperta emergente",
            source_kind="TOPIC_DISCOVERY",
            decision=None,
            evidence_ids=("msg::2::ORIGINAL_TEXT",),
            matched_sections=(),
        )
        view = hit_to_view_result(hit)

        assert view.result_type == "TOPIC_DISCOVERY"
        assert view.display_text == "Descrizione scoperta emergente"
        assert view.original_text is None

    def test_batch_conversion(self):
        hit1 = _sample_evidence_hit()
        hit2 = TopicSearchHit(
            topic_id="T_DISC",
            label="Nuovo Contatto",
            description="Discussione di un nuovo contatto estero",
            source_kind="TOPIC_DISCOVERY",
            evidence_ids=("e1",),
        )
        views = hits_to_view_results([hit1, hit2])
        assert len(views) == 2
        assert views[0].result_type == "EVIDENCE"
        assert views[0].display_text == "Contenuto probatorio intatto"
        assert views[1].result_type == "TOPIC_DISCOVERY"
        assert views[1].display_text == "Discussione di un nuovo contatto estero"
