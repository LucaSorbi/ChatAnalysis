"""
tests/unit/test_search_models.py
--------------------------------
Test unitari per i modelli immutabili del package search:
- Immutabilità profonda e frozen dataclass
- Validazione rigida della query (rifiuto query vuota con ValueError, limit > 0, match_mode enum)
- Invarianti completi di provenance in EvidenceSearchHit contro la TextEvidenceSection:
  evidence_id, source_type, message_id, source_name, source_record_id, language, original_text
- Invarianti di corrispondenza e ordine in TopicSearchHit (matched_sections vs evidence_ids)
- Semantica SearchViewResult (display_text, original_text condizionale)
"""
from dataclasses import FrozenInstanceError
import pytest

from ai.models import TopicDecision
from multimodal.evidence import EvidenceSourceType, TextEvidenceSection
from search.models import (
    EvidenceSearchHit,
    EvidenceSearchQuery,
    EvidenceSearchResult,
    MatchMode,
    SearchViewResult,
    TopicSearchHit,
    TopicSearchResult,
)


def _create_sample_section(
    evidence_id: str = "msg::1::ORIGINAL_TEXT",
    text: str = "Testo di esempio",
    source_type: EvidenceSourceType = EvidenceSourceType.ORIGINAL_TEXT,
    source_name: str = "msgstore_db",
    message_id: str = "msg::1",
    source_record_id: str = "1",
    language: str | None = "it",
) -> TextEvidenceSection:
    return TextEvidenceSection(
        evidence_id=evidence_id,
        source_type=source_type,
        text=text,
        language=language,
        message_id=message_id,
        source_name=source_name,
        source_record_id=source_record_id,
    )


@pytest.mark.unit
class TestSearchModels:

    def test_query_valid_defaults(self):
        q = EvidenceSearchQuery(query_text="frode fiscale")
        assert q.query_text == "frode fiscale"
        assert q.match_mode == MatchMode.PHRASE
        assert q.source_types is None
        assert q.language is None
        assert q.source_name is None
        assert q.limit is None

    def test_query_empty_or_whitespace_raises_value_error(self):
        with pytest.raises(ValueError, match="non può essere vuoto"):
            EvidenceSearchQuery(query_text="")
        with pytest.raises(ValueError, match="non può essere vuoto"):
            EvidenceSearchQuery(query_text="   \t\n  ")

    def test_query_invalid_limit_raises_value_error(self):
        with pytest.raises(ValueError, match="limit deve essere un intero strettamente positivo"):
            EvidenceSearchQuery(query_text="test", limit=0)
        with pytest.raises(ValueError, match="limit deve essere un intero strettamente positivo"):
            EvidenceSearchQuery(query_text="test", limit=-5)
        with pytest.raises(ValueError, match="limit deve essere un intero strettamente positivo"):
            EvidenceSearchQuery(query_text="test", limit=True)

    def test_query_match_mode_string_coercion(self):
        q = EvidenceSearchQuery(query_text="test", match_mode="ALL_TERMS")
        assert q.match_mode == MatchMode.ALL_TERMS

    def test_query_source_types_coercion_and_validation(self):
        q = EvidenceSearchQuery(
            query_text="test",
            source_types=["ORIGINAL_TEXT", EvidenceSourceType.OCR_TEXT],
        )
        assert isinstance(q.source_types, tuple)
        assert q.source_types == (EvidenceSourceType.ORIGINAL_TEXT, EvidenceSourceType.OCR_TEXT)

    def test_query_immutability(self):
        q = EvidenceSearchQuery(query_text="test")
        with pytest.raises(FrozenInstanceError):
            q.query_text = "altro"

    def test_evidence_search_hit_coherence_and_immutability(self):
        sec = _create_sample_section(text="Originale", evidence_id="msg::1::ORIGINAL_TEXT")
        hit = EvidenceSearchHit(
            evidence_id="msg::1::ORIGINAL_TEXT",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            message_id="msg::1",
            source_name="msgstore_db",
            source_record_id="1",
            language="it",
            original_text="Originale",
            section=sec,
            matched_terms=("originale",),
        )
        assert hit.original_text == "Originale"
        assert hit.section is sec

        with pytest.raises(FrozenInstanceError):
            hit.original_text = "mutato"

    # --- Test Negativi Indipendenti Invarianti di Provenance (Point D) ---

    def test_evidence_search_hit_wrong_evidence_id_raises_value_error(self):
        sec = _create_sample_section()
        with pytest.raises(ValueError, match="Discordanza evidence_id"):
            EvidenceSearchHit(
                evidence_id="wrong_id",
                source_type=sec.source_type,
                message_id=sec.message_id,
                source_name=sec.source_name,
                source_record_id=sec.source_record_id,
                language=sec.language,
                original_text=sec.text,
                section=sec,
            )

    def test_evidence_search_hit_wrong_source_type_raises_value_error(self):
        sec = _create_sample_section(source_type=EvidenceSourceType.ORIGINAL_TEXT)
        with pytest.raises(ValueError, match="Discordanza source_type"):
            EvidenceSearchHit(
                evidence_id=sec.evidence_id,
                source_type=EvidenceSourceType.OCR_TEXT,
                message_id=sec.message_id,
                source_name=sec.source_name,
                source_record_id=sec.source_record_id,
                language=sec.language,
                original_text=sec.text,
                section=sec,
            )

    def test_evidence_search_hit_wrong_message_id_raises_value_error(self):
        sec = _create_sample_section(message_id="msg::orig")
        with pytest.raises(ValueError, match="Discordanza message_id"):
            EvidenceSearchHit(
                evidence_id=sec.evidence_id,
                source_type=sec.source_type,
                message_id="msg::wrong",
                source_name=sec.source_name,
                source_record_id=sec.source_record_id,
                language=sec.language,
                original_text=sec.text,
                section=sec,
            )

    def test_evidence_search_hit_wrong_source_name_raises_value_error(self):
        sec = _create_sample_section(source_name="msgstore_db")
        with pytest.raises(ValueError, match="Discordanza source_name"):
            EvidenceSearchHit(
                evidence_id=sec.evidence_id,
                source_type=sec.source_type,
                message_id=sec.message_id,
                source_name="cellebrite_csv",
                source_record_id=sec.source_record_id,
                language=sec.language,
                original_text=sec.text,
                section=sec,
            )

    def test_evidence_search_hit_wrong_source_record_id_raises_value_error(self):
        sec = _create_sample_section(source_record_id="100")
        with pytest.raises(ValueError, match="Discordanza source_record_id"):
            EvidenceSearchHit(
                evidence_id=sec.evidence_id,
                source_type=sec.source_type,
                message_id=sec.message_id,
                source_name=sec.source_name,
                source_record_id="999",
                language=sec.language,
                original_text=sec.text,
                section=sec,
            )

    def test_evidence_search_hit_wrong_language_raises_value_error(self):
        sec = _create_sample_section(language="it")
        with pytest.raises(ValueError, match="Discordanza language"):
            EvidenceSearchHit(
                evidence_id=sec.evidence_id,
                source_type=sec.source_type,
                message_id=sec.message_id,
                source_name=sec.source_name,
                source_record_id=sec.source_record_id,
                language="en",
                original_text=sec.text,
                section=sec,
            )

    def test_evidence_search_hit_wrong_original_text_raises_value_error(self):
        sec = _create_sample_section(text="Testo Corretto")
        with pytest.raises(ValueError, match="Discordanza original_text"):
            EvidenceSearchHit(
                evidence_id=sec.evidence_id,
                source_type=sec.source_type,
                message_id=sec.message_id,
                source_name=sec.source_name,
                source_record_id=sec.source_record_id,
                language=sec.language,
                original_text="Testo Alterato",
                section=sec,
            )

    # --- TopicSearchHit Invariants (Point H) ---

    def test_topic_search_hit_matched_sections_invariants(self):
        sec1 = _create_sample_section(evidence_id="e1")
        sec2 = _create_sample_section(evidence_id="e2")

        # Cardinalità coerente e ordine identico: OK
        hit_ok = TopicSearchHit(
            topic_id="T01",
            label="Label",
            description="Desc",
            source_kind="TOPIC_DETECTION",
            evidence_ids=("e1", "e2"),
            matched_sections=(sec1, sec2),
        )
        assert len(hit_ok.matched_sections) == 2

        # 3 evidence_ids ma solo 2 matched_sections: Errore
        with pytest.raises(ValueError, match="Discordanza tra evidence_ids"):
            TopicSearchHit(
                topic_id="T01",
                label="Label",
                description="Desc",
                source_kind="TOPIC_DETECTION",
                evidence_ids=("e1", "e2", "e3"),
                matched_sections=(sec1, sec2),
            )

        # Ordine invertito rispetto a evidence_ids: Errore
        with pytest.raises(ValueError, match="Disallineamento d'ordine"):
            TopicSearchHit(
                topic_id="T01",
                label="Label",
                description="Desc",
                source_kind="TOPIC_DETECTION",
                evidence_ids=("e1", "e2"),
                matched_sections=(sec2, sec1),
            )

    # --- SearchViewResult Semantics (Point G) ---

    def test_search_view_result_display_text_and_immutability(self):
        view = SearchViewResult(
            result_type="EVIDENCE",
            title="Titolo",
            display_text="Testo da visualizzare",
            original_text="Testo da visualizzare",
            evidence_id="msg::1::ORIGINAL_TEXT",
            provenance={"chiave": "valore"},
        )
        assert view.result_type == "EVIDENCE"
        assert view.display_text == "Testo da visualizzare"
        assert view.original_text == "Testo da visualizzare"
        assert view.provenance["chiave"] == "valore"
        with pytest.raises(FrozenInstanceError):
            view.title = "Altro"

        # Topic view: display_text è la description, original_text è None
        topic_view = SearchViewResult(
            result_type="TOPIC_DETECTION",
            title="Topic",
            display_text="Descrizione generata dall'AI",
            original_text=None,
        )
        assert topic_view.display_text == "Descrizione generata dall'AI"
        assert topic_view.original_text is None
