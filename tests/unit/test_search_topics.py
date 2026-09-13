"""
tests/unit/test_search_topics.py
--------------------------------
Test unitari per TopicSearchEngine (FASE F & Acceptance Gate A/B):
- Lookup TopicDetectionResult per topic_id
- Filtraggio per decisione PRESENT / ABSENT / UNCERTAIN
- Ricerca testuale su label e description dei topic
- Strict Topic → Evidence Provenance: sollevamento TopicEvidenceIntegrityError per ID inesistenti
- Document Provenance: validazione che detection/discovery appartengano al document_id dell'indice
- Risoluzione deterministica e ordinata da topic a evidence originali
"""
import pytest

from ai.models import (
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from multimodal.evidence import EvidenceSourceType, TextEvidenceSection
from search.index import EvidenceIndex
from search.topics import TopicEvidenceIntegrityError, TopicSearchEngine


def _make_evidence_section(eid: str, text: str) -> TextEvidenceSection:
    return TextEvidenceSection(
        evidence_id=eid,
        source_type=EvidenceSourceType.ORIGINAL_TEXT,
        text=text,
        message_id="msg::1",
        source_name="msgstore_db",
        source_record_id="1",
    )


@pytest.mark.unit
class TestSearchTopics:

    def test_topic_id_lookup_and_resolution(self):
        sec1 = _make_evidence_section("eid::1", "Accordo preliminare firmato")
        index = EvidenceIndex([sec1], document_id="doc::1")

        t1 = TopicDetectionResult(
            topic=TopicQuery("T01", "Accordo Segreto", "Patto riservato tra le parti"),
            decision=TopicDecision.PRESENT,
            evidence_ids=("eid::1",),
            rationale="L'evidenza eid::1 menziona l'accordo firmato.",
            provenance_document_id="doc::1",
        )
        t2 = TopicDetectionResult(
            topic=TopicQuery("T02", "Fuga Notizie", "Divulgazione non autorizzata"),
            decision=TopicDecision.ABSENT,
            evidence_ids=(),
            rationale="Nessuna evidenza rilevata.",
            provenance_document_id="doc::1",
        )

        engine = TopicSearchEngine(
            evidence_index=index,
            detection_results=[t1, t2],
        )

        # Lookup per topic_id esatto
        res = engine.search_detections(topic_id="T01")
        assert res.total_hits == 1
        hit = res.hits[0]
        assert hit.topic_id == "T01"
        assert hit.label == "Accordo Segreto"
        assert hit.decision == TopicDecision.PRESENT
        assert hit.evidence_ids == ("eid::1",)
        assert len(hit.matched_sections) == 1
        assert hit.matched_sections[0].text == "Accordo preliminare firmato"

    # --- Strict Provenance & Missing Evidence ID (Point A) ---

    def test_missing_topic_evidence_raises_topic_evidence_integrity_error(self):
        sec1 = _make_evidence_section("eid::1", "Testo valido")
        index = EvidenceIndex([sec1], document_id="doc::1")

        # Cita 'eid::inesistente' non presente nell'index
        bad_detection = TopicDetectionResult(
            topic=TopicQuery("T01", "Topic Invalido", "Descrizione"),
            decision=TopicDecision.PRESENT,
            evidence_ids=("eid::inesistente",),
            rationale="Cita evidenza non presente.",
            provenance_document_id="doc::1",
        )

        with pytest.raises(TopicEvidenceIntegrityError, match="non è presente nell'EvidenceIndex"):
            TopicSearchEngine(evidence_index=index, detection_results=[bad_detection])

    def test_missing_discovery_evidence_raises_topic_evidence_integrity_error(self):
        sec1 = _make_evidence_section("eid::1", "Testo valido")
        index = EvidenceIndex([sec1], document_id="doc::1")

        bad_discovery = TopicDiscoveryResult(
            topics=(
                DiscoveredTopic(
                    label="Discovery Invalida",
                    short_description="Desc",
                    evidence_ids=("eid::inesistente_disc",),
                ),
            ),
            provenance_document_id="doc::1",
        )

        with pytest.raises(TopicEvidenceIntegrityError, match="non è presente nell'EvidenceIndex"):
            TopicSearchEngine(evidence_index=index, discovery_results=bad_discovery)

    # --- Document Provenance Validation (Point B) ---

    def test_detection_same_document_id_ok(self):
        sec = _make_evidence_section("e1", "Testo")
        index = EvidenceIndex([sec], document_id="doc::correct")
        t = TopicDetectionResult(
            topic=TopicQuery("T1", "Label", "Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=("e1",),
            rationale="OK",
            provenance_document_id="doc::correct",
        )
        engine = TopicSearchEngine(evidence_index=index, detection_results=[t])
        assert len(engine.search_detections().hits) == 1

    def test_detection_wrong_document_id_raises_integrity_error(self):
        sec = _make_evidence_section("e1", "Testo")
        index = EvidenceIndex([sec], document_id="doc::correct")
        t_wrong = TopicDetectionResult(
            topic=TopicQuery("T1", "Label", "Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=("e1",),
            rationale="Doc errato",
            provenance_document_id="doc::alien",
        )
        with pytest.raises(TopicEvidenceIntegrityError, match="Disallineamento document provenance in TopicDetectionResult"):
            TopicSearchEngine(evidence_index=index, detection_results=[t_wrong])

    def test_discovery_same_document_id_ok(self):
        sec = _make_evidence_section("e1", "Testo")
        index = EvidenceIndex([sec], document_id="doc::correct")
        disc = TopicDiscoveryResult(
            topics=(
                DiscoveredTopic(label="L", short_description="D", evidence_ids=("e1",)),
            ),
            provenance_document_id="doc::correct",
        )
        engine = TopicSearchEngine(evidence_index=index, discovery_results=disc)
        assert len(engine.search_discoveries().hits) == 1

    def test_discovery_wrong_document_id_raises_integrity_error(self):
        sec = _make_evidence_section("e1", "Testo")
        index = EvidenceIndex([sec], document_id="doc::correct")
        disc_wrong = TopicDiscoveryResult(
            topics=(
                DiscoveredTopic(label="L", short_description="D", evidence_ids=("e1",)),
            ),
            provenance_document_id="doc::alien",
        )
        with pytest.raises(TopicEvidenceIntegrityError, match="Disallineamento document provenance in TopicDiscoveryResult"):
            TopicSearchEngine(evidence_index=index, discovery_results=disc_wrong)

    # --- Filtering and Text Lookup ---

    def test_topic_detection_filtering_by_decision(self):
        sec1 = _make_evidence_section("eid::1", "Evidenza 1")
        sec3 = _make_evidence_section("eid::3", "Evidenza 3")
        index = EvidenceIndex([sec1, sec3], document_id="doc::1")

        t_pres = TopicDetectionResult(
            topic=TopicQuery("T_P", "Frodi", "Frode bancaria"),
            decision=TopicDecision.PRESENT,
            evidence_ids=("eid::1",),
            rationale="Confermata.",
            provenance_document_id="doc::1",
        )
        t_abs = TopicDetectionResult(
            topic=TopicQuery("T_A", "Violenza", "Minacce fisiche"),
            decision=TopicDecision.ABSENT,
            evidence_ids=(),
            rationale="Nessuna minaccia.",
            provenance_document_id="doc::1",
        )
        t_unc = TopicDetectionResult(
            topic=TopicQuery("T_U", "Riciclaggio", "Operazioni anomale"),
            decision=TopicDecision.UNCERTAIN,
            evidence_ids=("eid::3",),
            rationale="Indizi parziali.",
            provenance_document_id="doc::1",
        )

        engine = TopicSearchEngine(
            evidence_index=index,
            detection_results=[t_pres, t_abs, t_unc],
        )

        # Filtro PRESENT
        res_p = engine.search_detections(decision=TopicDecision.PRESENT)
        assert res_p.total_hits == 1
        assert res_p.hits[0].topic_id == "T_P"

        # Filtro ABSENT
        res_a = engine.search_detections(decision=TopicDecision.ABSENT)
        assert res_a.total_hits == 1
        assert res_a.hits[0].topic_id == "T_A"
        assert res_a.hits[0].evidence_ids == ()

        # Filtro UNCERTAIN
        res_u = engine.search_detections(decision=TopicDecision.UNCERTAIN)
        assert res_u.total_hits == 1
        assert res_u.hits[0].topic_id == "T_U"

    def test_search_detections_by_label_and_description(self):
        t1 = TopicDetectionResult(
            topic=TopicQuery("T01", "Corruzione Pubblica", "Tangenti a funzionari"),
            decision=TopicDecision.PRESENT,
            evidence_ids=("eid::1",),
            rationale="Rilevato.",
            provenance_document_id="doc::1",
        )
        t2 = TopicDetectionResult(
            topic=TopicQuery("T02", "Fatture False", "Emissione per operazioni inesistenti"),
            decision=TopicDecision.PRESENT,
            evidence_ids=("eid::2",),
            rationale="Rilevato.",
            provenance_document_id="doc::1",
        )
        # Senza indice, ricerca solo testuale
        engine = TopicSearchEngine(detection_results=[t1, t2])

        # Match sulla label (case-insensitive)
        r1 = engine.search_detections(query_text="corruzione")
        assert r1.total_hits == 1
        assert r1.hits[0].topic_id == "T01"

        # Match sulla description
        r2 = engine.search_detections(query_text="operazioni inesistenti")
        assert r2.total_hits == 1
        assert r2.hits[0].topic_id == "T02"

    def test_topic_discovery_lookup_and_evidence_resolution(self):
        sec = _make_evidence_section("eid::disc_1", "Parliamo del progetto Phoenix")
        sec2 = _make_evidence_section("eid::disc_2", "Spedizione magazzino")
        index = EvidenceIndex([sec, sec2], document_id="doc::disc")

        disc = TopicDiscoveryResult(
            topics=(
                DiscoveredTopic(
                    label="Progetto Phoenix",
                    short_description="Discussione su una nuova iniziativa riservata denominata Phoenix",
                    evidence_ids=("eid::disc_1",),
                ),
                DiscoveredTopic(
                    label="Logistica Magazzino",
                    short_description="Gestione spedizioni merci e logistica dei depositi",
                    evidence_ids=("eid::disc_2",),
                ),
            ),
            provenance_document_id="doc::disc",
        )

        engine = TopicSearchEngine(
            evidence_index=index,
            discovery_results=disc,
        )

        # Ricerca per label
        r_label = engine.search_discoveries(query_text="phoenix")
        assert r_label.total_hits == 1
        hit = r_label.hits[0]
        assert hit.label == "Progetto Phoenix"
        assert hit.source_kind == "TOPIC_DISCOVERY"
        assert hit.evidence_ids == ("eid::disc_1",)
        assert len(hit.matched_sections) == 1
        assert hit.matched_sections[0].text == "Parliamo del progetto Phoenix"

        # Ricerca per short_description
        r_desc = engine.search_discoveries(query_text="spedizioni merci")
        assert r_desc.total_hits == 1
        assert r_desc.hits[0].label == "Logistica Magazzino"

    def test_search_all_combines_detections_and_discoveries(self):
        t_det = TopicDetectionResult(
            topic=TopicQuery("T01", "Criptovalute", "Transazioni in Bitcoin"),
            decision=TopicDecision.PRESENT,
            evidence_ids=("e1",),
            rationale="Rilevato.",
            provenance_document_id="doc::1",
        )
        disc = TopicDiscoveryResult(
            topics=(
                DiscoveredTopic(
                    label="Wallet Cripto",
                    short_description="Indirizzi per trasferimento fondi cripto",
                    evidence_ids=("e2",),
                ),
            ),
            provenance_document_id="doc::1",
        )

        engine = TopicSearchEngine(
            detection_results=[t_det],
            discovery_results=disc,
        )

        res = engine.search_all(query_text="cripto")
        assert res.total_hits == 2
        kinds = {h.source_kind for h in res.hits}
        assert kinds == {"TOPIC_DETECTION", "TOPIC_DISCOVERY"}
