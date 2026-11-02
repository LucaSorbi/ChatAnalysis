"""
tests/unit/test_consistency_verification_audit.py
-------------------------------------------------
Audit mirato della verifica di consistenza semantica indipendente (UI «Verifica con Topic Detection»), 02/11/2026.

SOLO dati sintetici. Esito massimo: MOCK. Il client è il VERO `LmStudioClient.chat_completion` con
`urllib.request.urlopen` sostituito da risposte HTTP sintetiche: nessuna rete, nessun modello, nessuna chat reale.
Questi test NON provano nulla sul comportamento di Qwen né sulla UI Streamlit (AppTest non disponibile nella sandbox):
provano, a livello di funzioni applicative (`ui.application`) e di export, che

  1. il percorso ASSOCIATO (Discovery -> «Verifica con Topic Detection») e il percorso MANUALE (Detection a sola label)
     lasciano tracce DIVERSE e deterministiche negli export (origin_flow, query_mode, link, consistency_association_flag);
  2. `NO_MATCH` / `NOT_RECORDED` / `MANUAL_DETECTION` sono la firma legittima di una Detection manuale e NON indicano
     un errore del comparatore; `UNATTESTED` NON discrimina i due percorsi (le Detection non sono mai certificate);
  3. la Detection associata è una inferenza NUOVA e indipendente: riceve solo label+descrizione, mai gli evidence_id
     della Discovery; il comparatore non promuove mai ABSENT a PRESENT;
  4. la provenienza è fail-closed (documento diverso, run inesistente) e l'export SAFE non contiene testo.
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest

import ai.lmstudio as lms
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    MessageEvidenceBundle,
    TopicDecision,
    TopicDetectionResult,
    TopicQuery,
)
from analysis.consistency import ConsistencyService
from analysis.models import ConsistencyStatus, TopicProvenanceError
from multimodal.models import (
    AudioTranscriptionResult,
    ImageOcrResult,
    MediaKind,
    MediaResolutionStatus,
    OcrStatus,
    ResolvedMediaAsset,
    TranscriptionStatus,
)
from tests.unit.test_detection_microchunk_checkpoint_resume import MODEL, _doc, _no_sleep  # noqa: F401
from tests.unit.test_discovery_json_failure_telemetry import _HttpResp, _user_of
from ui import state
from ui.application import (
    TopicProvenanceMismatchError,
    build_topic_query,
    execute_discovery_verification,
    execute_manual_topic_detection,
    execute_topic_discovery,
    export_topic_detection_json,
    validate_discovery_provenance,
)
from ui.demo import _create_synthetic_message
from unified.models import CanonicalMessageType

LABEL = "Etichetta Sintetica Alfa"
DESC = "Descrizione sintetica alfa del tema"
RATIONALE = "Motivazione sintetica riservata"
OCR_SECRET = "TESTO OCR SINTETICO RISERVATO"
STT_SECRET = "TRASCRIZIONE SINTETICA RISERVATA"


# ---------------------------------------------------------------------------------------------
# Fixture sintetiche: documenti con sezione derivata STT / OCR (ID diversi), client HTTP finto
# ---------------------------------------------------------------------------------------------

def _derived_doc(doc_id: str, kind: str, at: int = 3, n: int = 6) -> ConversationEvidenceDocument:
    """`n` messaggi sintetici; il messaggio `at` è sostituito da un messaggio audio (STT) o foto (OCR) con sezione derivata."""
    base = _doc(doc_id, n)
    msg_id = f"msg_{doc_id}_{at}"
    is_audio = kind == "STT"
    msg = _create_synthetic_message(
        msg_id=msg_id, source_record_id=str(at), text_content="", source_name="whatsapp_test",
        message_type=CanonicalMessageType.AUDIO if is_audio else CanonicalMessageType.IMAGE,
        media_reference="a.opus" if is_audio else "f.jpg",
    )
    asset = ResolvedMediaAsset(
        message_id=msg_id, source_name="whatsapp_test", source_record_id=str(at), raw_reference=None,
        resolved_path=None, status=MediaResolutionStatus.RESOLVED,
        media_kind=MediaKind.AUDIO if is_audio else MediaKind.IMAGE, provenance_message=msg,
    )
    if is_audio:
        bundle = MessageEvidenceBundle(message=msg, audio_transcription=AudioTranscriptionResult(
            message_id=msg_id, source_name="whatsapp_test", source_record_id=str(at),
            status=TranscriptionStatus.SUCCESS, full_transcript=STT_SECRET, provenance_asset=asset))
    else:
        bundle = MessageEvidenceBundle(message=msg, image_ocr=ImageOcrResult(
            message_id=msg_id, source_name="whatsapp_test", source_record_id=str(at), status=OcrStatus.SUCCESS,
            full_text=OCR_SECRET, engine="tesseract", provenance_asset=asset))
    bundles = list(base.bundles)
    bundles[at] = bundle
    return ConversationEvidenceDocument(document_id=doc_id, bundles=tuple(bundles), source_name="whatsapp_test")


def _msg_alias_disc(user: str, msg_id: str) -> str:
    """Alias (E00x) del messaggio `msg_id` nel prompt di Discovery (formato JSONL compatto)."""
    m = re.search(rf'"id": "(E\d+)", "msg_id": "{re.escape(msg_id)}"', user)
    assert m, "messaggio assente dal prompt di Discovery"
    return m.group(1)


class Llm:
    """
    Registro + responder HTTP finto. `disc_msg` = msg_id citato dal topic di Discovery (alias risolto dal prompt);
    `decision`/`det_cite_msg` governano la Detection: msg_id il cui alias viene citato (formato JSONL compatto anche in Detection).
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, disc_msg: str, decision: str = "ABSENT",
                 det_cite_msg: str | None = None, label: str = LABEL, desc: str = DESC) -> None:
        self.disc_msg, self.decision, self.det_cite_msg = disc_msg, decision, det_cite_msg
        self.label, self.desc = label, desc
        self.discovery_prompts: list[str] = []
        self.detection_prompts: list[str] = []
        monkeypatch.setattr(lms.urllib.request, "urlopen", self._urlopen)
        self.client = lms.LmStudioClient(model_id=MODEL)

    def _body(self, content: str) -> bytes:
        return json.dumps({
            "model": MODEL,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 60, "total_tokens": 560},
        }).encode()

    def _urlopen(self, req: Any, timeout: float | None = None) -> _HttpResp:
        if getattr(req, "data", None) is None:  # GET di disponibilità / elenco modelli
            ok = _HttpResp(json.dumps({"data": [{"id": MODEL, "state": "loaded", "object": "model"}]}).encode())
            ok.status = 200  # type: ignore[attr-defined]
            return ok
        user = _user_of(req)
        if '"msg_id"' in user and "TARGET TOPIC TO DETECT" not in user:  # prompt di Discovery
            self.discovery_prompts.append(user)
            alias = _msg_alias_disc(user, self.disc_msg)
            return _HttpResp(self._body(json.dumps({"topics": [
                {"label": self.label, "short_description": self.desc, "evidence_ids": [alias]}]})))
        self.detection_prompts.append(user)
        cites = [_msg_alias_disc(user, self.det_cite_msg)] if (self.decision == "PRESENT" and self.det_cite_msg) else []
        return _HttpResp(self._body(json.dumps(
            {"decision": self.decision, "evidence_ids": cites, "rationale": RATIONALE})))


def _discover(doc: ConversationEvidenceDocument, llm: Llm, sess: dict[str, Any]):
    res = execute_topic_discovery(document=doc, client=llm.client, model_id=MODEL, max_topics=5, session_state=sess,
                                 enable_checkpointing=False)
    assert res.metadata["status"] == "COMPLETE" and len(res.topics) == 1
    return res


def _verify(doc, res, llm, sess, topic=None):
    return execute_discovery_verification(
        document=doc, discovered_topic=topic or res.topics[0], discovery_run_id=res.metadata["run_id"],
        client=llm.client, model_id=MODEL, session_state=sess, enable_checkpointing=False)


def _export(det, doc, sess) -> dict[str, Any]:
    return json.loads(export_topic_detection_json(det, doc, session_state=sess)[0])


# ---------------------------------------------------------------------------------------------
# 1. Percorso ASSOCIATO vs percorso MANUALE: firme diverse e deterministiche
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["STT", "OCR"])
def test_associated_flow_leaves_a_verifiable_link_end_to_end(kind, monkeypatch):
    """Discovery X -> «Verifica con Topic Detection» -> export SAFE: link, flow, query_mode e consistenza coerenti."""
    doc = _derived_doc(f"doc_{kind.lower()}_a", kind)
    llm = Llm(monkeypatch, disc_msg=f"msg_doc_{kind.lower()}_a_3", decision="ABSENT")
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)
    det, cons = _verify(doc, res, llm, sess)

    # run nuova e indipendente, collegata per ID alla run di Discovery
    assert det.metadata["run_id"] != res.metadata["run_id"]
    assert det.metadata["origin_flow"] == "DISCOVERY_VERIFICATION"
    assert det.metadata["query_mode"] == "LABEL_AND_DESCRIPTION"
    assert det.metadata["verified_discovery_run_id"] == res.metadata["run_id"]
    assert det.provenance_document_id == doc.document_id
    assert (cons.document_id, cons.discovery_run_id, cons.detection_run_id) == (
        doc.document_id, res.metadata["run_id"], det.metadata["run_id"])
    # la query è la coppia label+descrizione del risultato VALIDATED, non una label nuda
    assert det.topic.label == res.topics[0].label and det.topic.description == res.topics[0].short_description
    assert det.topic.is_label_only is False

    rep = _export(det, doc, sess)
    assert rep["experiment"]["origin_flow"] == "DISCOVERY_VERIFICATION"
    assert rep["result"]["consistency_association_flag"] == "EXACT_UNIQUE_MATCH"
    assert rep["result"]["linked_discovery_run_id"] != "NOT_RECORDED"
    assert rep["result"]["topic"]["is_label_only"] is False
    sc = rep["result"]["semantic_consistency"]
    assert sc["origin_flow"] == "DISCOVERY_VERIFICATION"
    assert sc["consistency_status"] == "DISCREPANCY_ABSENT"  # ABSENT COMPLETE: disaccordo, MAI promosso


def test_manual_label_only_flow_legitimately_has_no_link(monkeypatch):
    """Detection manuale a sola label: MANUAL_DETECTION + is_label_only + NOT_RECORDED + NO_MATCH, per costruzione."""
    doc = _derived_doc("doc_man", "OCR")
    llm = Llm(monkeypatch, disc_msg="msg_doc_man_3", decision="ABSENT")
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)  # Discovery presente nella stessa sessione, con la STESSA label
    n_cons_before = len(state.get_consistency_results(doc.document_id, state=sess))

    det = execute_manual_topic_detection(
        document=doc, topic=build_topic_query(label=LABEL, description=None), client=llm.client,
        model_id=MODEL, session_state=sess, enable_checkpointing=False)

    assert det.metadata["origin_flow"] == "MANUAL_DETECTION"
    assert det.topic.is_label_only is True
    assert "verified_discovery_run_id" not in det.metadata and "discovery_run_id" not in det.metadata
    # nessun ConsistencyResult creato e nessuna associazione a posteriori per coincidenza di label
    assert len(state.get_consistency_results(doc.document_id, state=sess)) == n_cons_before == 0
    rep = _export(det, doc, sess)
    assert rep["experiment"]["origin_flow"] == "MANUAL_DETECTION"
    assert rep["result"]["topic"]["is_label_only"] is True
    assert rep["result"]["linked_discovery_run_id"] == "NOT_RECORDED"
    assert rep["result"]["consistency_association_flag"] == "NO_MATCH"
    assert "semantic_consistency" not in rep["result"] or rep["result"]["semantic_consistency"] is None
    assert det.metadata["run_id"] != res.metadata["run_id"]
    # il topic_id manuale non è uno dei topic_id della Discovery (e non deve esserlo)
    assert det.topic.topic_id not in {t.topic_id for t in getattr(res, "topics", ()) if hasattr(t, "topic_id")}


def test_unattested_does_not_discriminate_between_the_two_paths(monkeypatch):
    """Con client LIVE (HTTP finto) NESSUNA Detection è certificata: UNATTESTED vale per entrambi i percorsi."""
    doc = _derived_doc("doc_att", "STT")
    llm = Llm(monkeypatch, disc_msg="msg_doc_att_3", decision="ABSENT")
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)
    det_a, _ = _verify(doc, res, llm, sess)
    det_m = execute_manual_topic_detection(
        document=doc, topic=build_topic_query(label=LABEL, description=DESC), client=llm.client,
        model_id=MODEL, session_state=sess, enable_checkpointing=False)
    ra, rm = _export(det_a, doc, sess), _export(det_m, doc, sess)
    assert det_a.metadata.get("provenance_verified") is None and det_m.metadata.get("provenance_verified") is None
    assert ra["experiment"]["attestation_level"] == rm["experiment"]["attestation_level"] == "UNATTESTED"
    # la Discovery della stessa sessione, invece, è certificata dal percorso autoritativo
    assert res.metadata["provenance_verified"] is True
    assert res.metadata["run_id"] in state.get_authoritative_live_run_ids(sess)
    assert det_a.metadata["run_id"] not in state.get_authoritative_live_run_ids(sess)
    assert det_m.metadata["run_id"] not in state.get_authoritative_live_run_ids(sess)


def test_label_plus_description_manual_run_is_still_not_linked(monkeypatch):
    """Anche una Detection manuale con label+descrizione IDENTICHE alla Discovery resta MANUAL/NOT_RECORDED/NO_MATCH."""
    doc = _derived_doc("doc_man2", "STT")
    llm = Llm(monkeypatch, disc_msg="msg_doc_man2_3", decision="PRESENT", det_cite_msg="msg_doc_man2_0")
    sess: dict[str, Any] = {}
    _discover(doc, llm, sess)
    det = execute_manual_topic_detection(
        document=doc, topic=build_topic_query(label=LABEL, description=DESC), client=llm.client,
        model_id=MODEL, session_state=sess, enable_checkpointing=False)
    rep = _export(det, doc, sess)
    assert rep["experiment"]["origin_flow"] == "MANUAL_DETECTION"
    assert rep["result"]["topic"]["is_label_only"] is False
    assert (rep["result"]["linked_discovery_run_id"], rep["result"]["consistency_association_flag"]) == ("NOT_RECORDED", "NO_MATCH")
    assert rep["result"]["decision"] == "PRESENT"


def test_no_match_also_appears_when_session_is_lost_even_for_an_associated_run(monkeypatch):
    """
    Caratterizzazione: NO_MATCH dice solo «nessun ConsistencyResult in sessione per questo detection_run_id».
    Una run ASSOCIATA esportata da una sessione vuota ha NO_MATCH ma il link `linked_discovery_run_id` resta registrato.
    """
    doc = _derived_doc("doc_lost", "OCR")
    llm = Llm(monkeypatch, disc_msg="msg_doc_lost_3", decision="ABSENT")
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)
    det, _ = _verify(doc, res, llm, sess)
    fresh: dict[str, Any] = {}  # sessione Streamlit persa / ricaricata
    rep = _export(det, doc, fresh)
    assert rep["result"]["consistency_association_flag"] == "NO_MATCH"
    assert rep["result"]["linked_discovery_run_id"] != "NOT_RECORDED"
    assert rep["experiment"]["origin_flow"] == "DISCOVERY_VERIFICATION"
    # senza session_state il flag non è nemmeno calcolato
    rep2 = json.loads(export_topic_detection_json(det, doc)[0])
    assert "consistency_association_flag" not in rep2["result"]
    assert rep2["result"]["linked_discovery_run_id"] != "NOT_RECORDED"


# ---------------------------------------------------------------------------------------------
# 2. Indipendenza della Detection associata: nessun evidence_id / hint della Discovery
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["STT", "OCR"])
def test_verification_prompt_is_identical_to_a_manual_full_query_and_carries_no_discovery_hint(kind, monkeypatch):
    doc = _derived_doc(f"doc_ind_{kind.lower()}", kind)
    llm = Llm(monkeypatch, disc_msg=f"msg_doc_ind_{kind.lower()}_3", decision="ABSENT")
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)
    _verify(doc, res, llm, sess)
    execute_manual_topic_detection(
        document=doc, topic=build_topic_query(label=LABEL, description=DESC), client=llm.client,
        model_id=MODEL, session_state=sess, enable_checkpointing=False)
    assoc, manual = llm.detection_prompts[0], llm.detection_prompts[1]
    assert assoc == manual, "la Detection associata deve ricevere esattamente label+descrizione, nient'altro"
    # la sezione derivata è presente nel documento arricchito (stessa istanza), con il suo testo
    assert (STT_SECRET if kind == "STT" else OCR_SECRET) in assoc
    assert "::" not in assoc  # nessun evidence_id reale (né di Discovery né del documento) nel prompt
    for t in res.topics:
        assert not any(eid in assoc for eid in t.evidence_ids)
    assert "TARGET TOPIC TO DETECT" in assoc and LABEL in assoc and DESC in assoc
    # nessun campo dedicato a hint/evidenze attese
    assert not re.search(r"expected|hint|discovery", assoc, re.IGNORECASE)


# ---------------------------------------------------------------------------------------------
# 3. Comparatore: PRESENT / ABSENT / UNCERTAIN / PARTIAL, mai promozione di ABSENT
# ---------------------------------------------------------------------------------------------

def _det(doc_id: str, decision: TopicDecision, eids: tuple[str, ...], meta: dict[str, Any] | None = None) -> TopicDetectionResult:
    return TopicDetectionResult(
        topic=TopicQuery(topic_id="t", label="L", description="D"), decision=decision, evidence_ids=eids,
        rationale="r", provenance_document_id=doc_id, metadata=dict(meta or {}))


_DISC = DiscoveredTopic(label="L", short_description="D", evidence_ids=("e1", "e2"))


@pytest.mark.parametrize("decision,eids,expected", [
    (TopicDecision.PRESENT, ("e2", "e9"), ConsistencyStatus.CONSISTENT),
    (TopicDecision.PRESENT, ("e9",), ConsistencyStatus.PARTIALLY_CONSISTENT),
    (TopicDecision.ABSENT, (), ConsistencyStatus.DISCREPANCY_ABSENT),
    (TopicDecision.UNCERTAIN, (), ConsistencyStatus.DISCREPANCY_UNCERTAIN),
])
def test_comparator_status_matrix(decision, eids, expected):
    c = ConsistencyService.evaluate("d", _DISC, _det("d", decision, eids), "run_a", "run_b")
    assert c.status == expected
    assert c.metadata["consistency_completeness"] == "COMPLETE" and c.metadata["is_partial_detection"] is False


def test_comparator_never_promotes_absent_and_marks_partial_detection():
    absent_partial = _det("d", TopicDecision.ABSENT, (), {"status": "PARTIAL"})
    c = ConsistencyService.evaluate("d", _DISC, absent_partial, "run_a", "run_b")
    assert c.status == ConsistencyStatus.DISCREPANCY_ABSENT
    assert c.metadata["consistency_completeness"] == "PARTIAL" and "PARZIALE" in c.rationale
    # il modello dati stesso impedisce ABSENT con evidenze: non esiste uno stato ambiguo da promuovere
    with pytest.raises(ValueError):
        _det("d", TopicDecision.ABSENT, ("e1",))


def test_comparator_rejects_document_mismatch_and_blank_ids():
    with pytest.raises(TopicProvenanceError):
        ConsistencyService.evaluate("doc_a", _DISC, _det("doc_b", TopicDecision.PRESENT, ("e1",)), "r1", "r2")
    with pytest.raises(TopicProvenanceError):
        ConsistencyService.evaluate("d", _DISC, _det("d", TopicDecision.PRESENT, ("e1",)), "  ", "r2")


# ---------------------------------------------------------------------------------------------
# 4. Provenienza fail-closed: documento diverso, topic stale, run inesistente, reimport
# ---------------------------------------------------------------------------------------------

def test_stale_selection_other_document_is_rejected_by_ui_and_service(monkeypatch):
    doc_a = _derived_doc("doc_a_stale", "STT")
    doc_b = _derived_doc("doc_b_stale", "OCR")
    llm = Llm(monkeypatch, disc_msg="msg_doc_a_stale_3", decision="PRESENT", det_cite_msg="msg_doc_a_stale_0")
    sess: dict[str, Any] = {}
    res = _discover(doc_a, llm, sess)
    with pytest.raises(TopicProvenanceMismatchError):
        validate_discovery_provenance(discovery_provenance_id=res.provenance_document_id, active_document_id=doc_b.document_id)
    n = len(llm.detection_prompts)
    with pytest.raises(TopicProvenanceError):
        _verify(doc_b, res, llm, sess)
    assert len(llm.detection_prompts) == n, "nessuna Detection deve partire se la provenienza non è verificabile"
    assert not state.get_consistency_results(doc_b.document_id, state=sess)


def test_unknown_run_id_and_missing_run_id_are_rejected_without_detection(monkeypatch):
    doc = _derived_doc("doc_unk", "STT")
    llm = Llm(monkeypatch, disc_msg="msg_doc_unk_3")
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)
    n = len(llm.detection_prompts)
    for bad in ("run_inesistente000", None):
        with pytest.raises(Exception):
            execute_discovery_verification(
                document=doc, discovered_topic=res.topics[0], discovery_run_id=bad, client=llm.client,
                model_id=MODEL, session_state={}, enable_checkpointing=False)  # registro vuoto: run non certificata in questa sessione
    assert len(llm.detection_prompts) == n


def test_latent_gap_characterization_same_document_id_with_changed_content_is_accepted_without_fingerprint(monkeypatch):
    """
    CARATTERIZZAZIONE di un gap LATENTE (NON corretto qui, NON causa del sintomo segnalato): per una run di Discovery
    in memoria il service non confronta la fingerprint del documento attivo; stesso `document_id` + stessi evidence_id ma
    contenuto diverso (reimport / arricchimento successivo) viene verificato, e la Detection non registra la fingerprint
    del documento realmente analizzato. Se un giorno il gap verrà chiuso, questo test va aggiornato di conseguenza.
    """
    doc_v1 = _derived_doc("doc_re", "STT")
    llm = Llm(monkeypatch, disc_msg="msg_doc_re_3")
    sess: dict[str, Any] = {}
    res = _discover(doc_v1, llm, sess)
    b = list(_derived_doc("doc_re", "STT").bundles)
    b[0] = _doc("doc_re", 6, text_suffix="contenuto diverso dopo reimport").bundles[0]
    doc_v2 = ConversationEvidenceDocument(document_id="doc_re", bundles=tuple(b), source_name="whatsapp_test")
    from analysis.checkpoint import compute_document_fingerprint
    assert compute_document_fingerprint(doc_v1) != compute_document_fingerprint(doc_v2)
    det, cons = _verify(doc_v2, res, llm, sess)  # nessuna eccezione
    assert cons.discovery_run_id == res.metadata["run_id"]
    assert det.metadata.get("document_fingerprint") is None
    assert res.metadata.get("document_fingerprint") is None


def test_partial_discovery_run_can_be_verified_and_consistency_does_not_record_the_source_status(monkeypatch):
    """
    CARATTERIZZAZIONE (gap di telemetria LATENTE, non corretto): la verifica è consentita anche su topic provenienti da
    una Discovery PARTIAL; `consistency_completeness` riguarda solo la Detection e l'export non riporta lo stato della
    Discovery sorgente. Non è il caso dei quattro export segnalati (Discovery COMPLETE).
    """
    doc = _derived_doc("doc_part", "OCR", at=10, n=12)

    class _PartialLlm(Llm):
        def _urlopen(self, req: Any, timeout: float | None = None) -> _HttpResp:
            if getattr(req, "data", None) is not None:
                u = _user_of(req)
                if '"msg_id"' in u and "TARGET TOPIC" not in u and "msg_doc_part_10" not in u:
                    return _HttpResp(self._body("risposta non JSON"))
            return super()._urlopen(req, timeout)

    llm = _PartialLlm(monkeypatch, disc_msg="msg_doc_part_10", decision="ABSENT")
    sess: dict[str, Any] = {}
    res = execute_topic_discovery(document=doc, client=llm.client, model_id=MODEL, max_topics=5, session_state=sess,
                                  enable_checkpointing=False)
    assert res.metadata["status"] == "PARTIAL" and len(res.topics) == 1
    det, cons = _verify(doc, res, llm, sess)
    assert cons.status == ConsistencyStatus.DISCREPANCY_ABSENT
    rep = _export(det, doc, sess)
    sc = rep["result"]["semantic_consistency"]
    assert sc["consistency_completeness"] == "COMPLETE"  # riferita alla Detection
    assert not any("discovery_status" in k or "source_discovery_status" in k for k in sc)


def test_uncertain_detection_is_a_discrepancy_never_a_confirmation(monkeypatch):
    doc = _derived_doc("doc_unc", "STT")
    llm = Llm(monkeypatch, disc_msg="msg_doc_unc_3", decision="UNCERTAIN")
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)
    det, cons = _verify(doc, res, llm, sess)
    assert det.decision == TopicDecision.UNCERTAIN and cons.status == ConsistencyStatus.DISCREPANCY_UNCERTAIN
    assert _export(det, doc, sess)["result"]["semantic_consistency"]["consistency_status"] == "DISCREPANCY_UNCERTAIN"


def test_two_runs_same_label_are_linked_by_run_id_not_by_label(monkeypatch):
    """Due Discovery della stessa chat con label identica: ogni verifica si collega SOLO alla propria run."""
    doc = _derived_doc("doc_dup", "OCR")
    sess: dict[str, Any] = {}
    llm1 = Llm(monkeypatch, disc_msg="msg_doc_dup_1", decision="ABSENT")
    res1 = _discover(doc, llm1, sess)
    llm2 = Llm(monkeypatch, disc_msg="msg_doc_dup_3", decision="ABSENT", desc="Descrizione diversa dello stesso tema")
    res2 = execute_topic_discovery(document=doc, client=llm2.client, model_id=MODEL, max_topics=5, session_state=sess, enable_checkpointing=False)
    assert res1.metadata["run_id"] != res2.metadata["run_id"]
    det1, c1 = _verify(doc, res1, llm2, sess)
    det2, c2 = _verify(doc, res2, llm2, sess)
    assert (c1.discovery_run_id, c2.discovery_run_id) == (res1.metadata["run_id"], res2.metadata["run_id"])
    assert (c1.detection_run_id, c2.detection_run_id) == (det1.metadata["run_id"], det2.metadata["run_id"])
    r1, r2 = _export(det1, doc, sess), _export(det2, doc, sess)
    assert r1["result"]["consistency_association_flag"] == r2["result"]["consistency_association_flag"] == "EXACT_UNIQUE_MATCH"
    assert r1["result"]["linked_discovery_run_id"] != r2["result"]["linked_discovery_run_id"]


# ---------------------------------------------------------------------------------------------
# 5. Evidenza originale vs derivata, PRESENT con evidenze valide
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind,cite_idx,expected", [
    ("OCR", 3, ConsistencyStatus.CONSISTENT),             # Discovery cita la derivata, Detection cita la derivata
    ("OCR", 0, ConsistencyStatus.PARTIALLY_CONSISTENT),   # Detection cita solo un'evidenza originale: evidenze distinte
    ("STT", 3, ConsistencyStatus.CONSISTENT),
    ("STT", 0, ConsistencyStatus.PARTIALLY_CONSISTENT),
])
def test_present_with_original_vs_derived_evidence(kind, cite_idx, expected, monkeypatch):
    did = f"doc_ev_{kind.lower()}_{cite_idx}"
    doc = _derived_doc(did, kind)
    llm = Llm(monkeypatch, disc_msg=f"msg_{did}_3", decision="PRESENT", det_cite_msg=f"msg_{did}_{cite_idx}")
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)
    assert res.topics[0].evidence_ids[0].endswith("::OCR_TEXT" if kind == "OCR" else "::STT_TRANSCRIPTION")
    det, cons = _verify(doc, res, llm, sess)
    assert det.decision == TopicDecision.PRESENT and det.evidence_ids, "PRESENT richiede evidenze valide del documento"
    assert set(det.evidence_ids) <= {e.evidence_id for e in doc.all_evidence_sections}
    assert cons.status == expected


# ---------------------------------------------------------------------------------------------
# 6. Privacy dell'export SAFE
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("flow", ["associated", "manual_label_only"])
def test_safe_export_contains_no_text_label_description_or_rationale(flow, monkeypatch):
    doc = _derived_doc("doc_priv", "OCR")
    llm = Llm(monkeypatch, disc_msg="msg_doc_priv_3", decision="ABSENT")
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)
    if flow == "associated":
        det, _ = _verify(doc, res, llm, sess)
    else:
        det = execute_manual_topic_detection(
            document=doc, topic=build_topic_query(label=LABEL, description=None), client=llm.client,
            model_id=MODEL, session_state=sess, enable_checkpointing=False)
    raw = export_topic_detection_json(det, doc, session_state=sess)[0]
    for secret in (LABEL, DESC, RATIONALE, OCR_SECRET, "Messaggio sintetico", "whatsapp_test", "/test"):
        assert secret not in raw, secret


# ---------------------------------------------------------------------------------------------
# 7. Ripristino da checkpoint di una Detection a parent singolo: deve essere dichiarato, non presentato come nuova inferenza
# ---------------------------------------------------------------------------------------------

def _two_identical_verifications(monkeypatch, tmp_path, flow: str):
    from analysis.checkpoint import AnalysisCheckpointStore

    store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")
    doc = _derived_doc("doc_ckpt", "OCR")
    llm = Llm(monkeypatch, disc_msg="msg_doc_ckpt_3", decision="ABSENT")
    sess: dict[str, Any] = {}
    res = execute_topic_discovery(document=doc, client=llm.client, model_id=MODEL, max_topics=5,
                                  session_state=sess, checkpoint_store=store)
    out = []
    for _ in range(2):
        if flow == "associated":
            det, _c = execute_discovery_verification(
                document=doc, discovered_topic=res.topics[0], discovery_run_id=res.metadata["run_id"],
                client=llm.client, model_id=MODEL, session_state=sess, checkpoint_store=store)
        else:
            det = execute_manual_topic_detection(
                document=doc, topic=build_topic_query(label=LABEL, description=DESC), client=llm.client,
                model_id=MODEL, session_state=sess, checkpoint_store=store)
        out.append((det, _export(det, doc, sess), len(llm.detection_prompts)))
    return out


@pytest.mark.parametrize("flow", ["associated", "manual"])
def test_single_parent_checkpoint_restore_is_declared_in_the_export(flow, monkeypatch, tmp_path):
    (det1, rep1, n1), (det2, rep2, n2) = _two_identical_verifications(monkeypatch, tmp_path, flow)
    assert n1 == 1 and n2 == 1, "la seconda esecuzione identica non invia richieste: è un ripristino da checkpoint"
    # risultato scientifico invariato
    assert (det1.decision, det1.evidence_ids) == (det2.decision, det2.evidence_ids)
    # prima esecuzione: inferenza nuova, nessun ripristino
    assert rep1["experiment"]["loading_mode"] == "IN_MEMORY_EXECUTION"
    assert rep1["checkpoints"]["recovered_count"] == 0
    # seconda: DEVE dichiarare il ripristino (non una nuova inferenza in memoria)
    assert rep2["execution"]["detection_diagnostics"]["http_requests_sent"] == 0
    assert rep2["experiment"]["loading_mode"] == "CHECKPOINT_RESTORE"
    assert rep2["checkpoints"]["recovered_count"] == 1
    assert rep2["coverage"]["chunks_recovered"] == 1
    assert det2.metadata["recovered_chunk_count"] == 1
    # il ripristino non cambia la firma di flusso né il link
    assert rep2["experiment"]["origin_flow"] == rep1["experiment"]["origin_flow"]
    assert rep2["result"]["linked_discovery_run_id"] == rep1["result"]["linked_discovery_run_id"]
