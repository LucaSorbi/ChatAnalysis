"""
Verifica di consistenza semantica indipendente: rapporto autonomo, identità di topic/snapshot, origine di esecuzione,
fail-closed, privacy SAFE. Test SINTETICI e DETERMINISTICI (livello MOCK: client HTTP finto, nessun modello reale,
nessun dato reale). I test dell'interfaccia Streamlit reale sono in fondo e sono saltati se `streamlit.testing`
non e' installato (niente PASS finti).

Matrice coperta (numerazione del mandato): 1 flusso completo, 2 ABSENT, 3 PRESENT con/senza overlap, 4 UNCERTAIN/PARTIAL,
5 manuale invariato, 6 omonimi/due run, 7 A/B/A e perdita di sessione, 8 snapshot, 9 STT+OCR, 10 Discovery PARTIAL,
11 checkpoint, 12 privacy SAFE, 13 serializzazione/validatore, 14 UI, 15 errori, 17 nessuna regressione PR #10.
"""
from __future__ import annotations

import dataclasses
import json
import re
from typing import Any

import pytest

from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDecision,
)
from analysis.checkpoint import AnalysisCheckpointStore, compute_document_fingerprint
from analysis.consistency import ConsistencyService
from analysis.export import (
    SemanticVerificationExportError,
    build_semantic_verification_export_report,
    validate_semantic_verification_report,
)
from analysis.models import ConsistencyStatus, DocumentAnalysisState
from analysis.verification import (
    VERIFICATION_EXPORT_CONTRACT_VERSION,
    DocumentSnapshotMismatchError,
    ExecutionOrigin,
    SnapshotStatus,
    check_document_snapshot,
    compute_discovered_topic_uid,
    derive_execution_origin,
)
from tests.unit.test_consistency_verification_audit import (
    DESC,
    LABEL,
    MODEL,
    OCR_SECRET,
    RATIONALE,
    STT_SECRET,
    Llm,
    _derived_doc,
    _discover,
    _doc,
    _verify,
)
from tests.unit.test_discovery_json_failure_telemetry import _HttpResp, _user_of
from ui import state
from ui.application import (
    build_topic_query,
    execute_manual_topic_detection,
    execute_topic_discovery,
    export_topic_detection_json,
)
from ui.verification import (
    describe_export_error,
    describe_verification_outcome,
    evaluate_verification_preconditions,
    export_semantic_verification_json,
    find_discovery_run,
    select_verification_results,
)

SECRETS = (LABEL, DESC, RATIONALE, OCR_SECRET, STT_SECRET, "Messaggio sintetico", "whatsapp_test")


def _flow(monkeypatch, kind="OCR", decision="ABSENT", cite_idx=None, doc_id="doc_v"):
    doc = _derived_doc(doc_id, kind)
    cite = f"msg_{doc_id}_{cite_idx}" if cite_idx is not None else None
    llm = Llm(monkeypatch, disc_msg=f"msg_{doc_id}_3", decision=decision, det_cite_msg=cite)
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)
    det, cons = _verify(doc, res, llm, sess)
    return doc, llm, sess, res, det, cons


def _report(doc, cons, sess, **kw) -> dict[str, Any]:
    raw, name = export_semantic_verification_json(cons, doc, session_state=sess, **kw)
    rep = json.loads(raw)
    rep["_filename"] = name
    return rep


# ---------------------------------------------------------------------------------------------
# 1-3, 9, 13. Rapporto per tutti gli esiti, con documenti STT e OCR
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["STT", "OCR"])
@pytest.mark.parametrize("decision,cite_idx,expected,interp", [
    ("ABSENT", None, ConsistencyStatus.DISCREPANCY_ABSENT, "DISCOVERY_IDENTIFIED_TOPIC_INDEPENDENT_DETECTION_DID_NOT_DETECT_IT"),
    ("UNCERTAIN", None, ConsistencyStatus.DISCREPANCY_UNCERTAIN, "DISCOVERY_IDENTIFIED_TOPIC_INDEPENDENT_DETECTION_UNCERTAIN"),
    ("PRESENT", 3, ConsistencyStatus.CONSISTENT, "DISCOVERY_AND_DETECTION_AGREE_WITH_SHARED_EVIDENCE"),
    ("PRESENT", 0, ConsistencyStatus.PARTIALLY_CONSISTENT, "DETECTION_PRESENT_ON_DISTINCT_EVIDENCE"),
])
def test_report_is_exportable_for_every_outcome_and_coherent(kind, decision, cite_idx, expected, interp, monkeypatch):
    doc, llm, sess, res, det, cons = _flow(monkeypatch, kind, decision, cite_idx, doc_id=f"doc_{kind.lower()}_{decision.lower()}")
    assert cons.status == expected
    n_prompts = len(llm.detection_prompts) + len(llm.discovery_prompts)

    rep = _report(doc, cons, sess)

    assert len(llm.detection_prompts) + len(llm.discovery_prompts) == n_prompts, "l'export non invoca mai il modello"
    assert validate_semantic_verification_report(
        {k: v for k, v in rep.items() if k != "_filename"}) == []
    assert rep["report_type"] == "SEMANTIC_CONSISTENCY_VERIFICATION"
    assert rep["verification_contract_version"] == VERIFICATION_EXPORT_CONTRACT_VERSION
    cmp_ = rep["comparison"]
    assert cmp_["consistency_status"] == expected.value and cmp_["interpretation_code"] == interp
    assert cmp_["association"]["status"] == "EXACT_UNIQUE_MATCH"
    assert cmp_["association"]["basis"] == "EXPLICIT_RUN_IDS_AND_DOCUMENT_ID"
    assert rep["experiment"]["origin_flow"] == "DISCOVERY_VERIFICATION"
    assert rep["detection"]["query"] == {
        "query_mode": "LABEL_AND_DESCRIPTION", "is_label_only": False, "label_present": True,
        "description_present": True, "label": rep["detection"]["query"]["label"],
        "description": rep["detection"]["query"]["description"], "discovery_evidence_ids_supplied_to_model": False,
    }
    assert rep["detection"]["decision"] == decision
    # le evidenze di entrambe le inferenze e l'overlap sono SEMPRE presenti (anche vuoti), con tipi di sezione
    assert len(cmp_["discovery_evidence_ids"]) == 1 and cmp_["discovery_evidence_ids"] == rep["discovery_source"]["topic"]["evidence_ids"]
    assert cmp_["overlap_count"] == len(cmp_["overlap_evidence_ids"]) == len(cons.overlap_evidence_ids)
    derived = "OCR_TEXT" if kind == "OCR" else "STT_TRANSCRIPTION"
    assert rep["discovery_source"]["topic"]["evidence_by_source_type"] == {derived: 1}
    assert rep["document"]["evidence_by_source_type"][derived] == 1
    assert rep["document"]["snapshot"]["status"] == "MATCH" and rep["document"]["snapshot"]["fingerprints_equal"] is True
    assert rep["document"]["snapshot"]["discovery_document_fingerprint"] == compute_document_fingerprint(doc)
    assert rep["detection"]["execution"]["execution_origin"] == "COMPUTED_THIS_RUN"
    assert rep["discovery_source"]["coverage"]["is_complete"] is True
    assert rep["audit"]["checks"]["snapshot_equivalence_proven"] is True
    assert "SINGLE_MODEL_AGREEMENT_IS_NOT_GROUND_TRUTH" in cmp_["limits"]
    # nessuna falsa attestazione: la Detection non e' attestata come LIVE_AUTHORITATIVE
    assert rep["detection"]["attestation_level"] != "LIVE_AUTHORITATIVE"
    assert "DETECTION_NOT_AUTHORITATIVELY_ATTESTED" in cmp_["limits"]
    # SAFE: nessun testo, label, descrizione, motivazione, percorso
    raw = json.dumps(rep)
    for secret in SECRETS:
        assert secret not in raw, secret


def test_absent_is_exportable_with_empty_detection_evidence_and_explicit_limits(monkeypatch):
    doc, llm, sess, res, det, cons = _flow(monkeypatch, "OCR", "ABSENT")
    rep = _report(doc, cons, sess)
    assert det.evidence_ids == () and rep["detection"]["evidence_ids"] == []
    assert rep["comparison"]["detection_evidence_ids"] == [] and rep["comparison"]["overlap_evidence_ids"] == []
    assert rep["comparison"]["overlap_count"] == 0
    assert "ABSENT_IS_NOT_PROOF_OF_ABSENCE" in rep["comparison"]["limits"]
    # il topic scoperto e le sue evidenze restano documentati
    assert rep["discovery_source"]["topic"]["evidence_count"] == 1


def test_non_redacted_mode_keeps_labels_but_never_texts_from_the_conversation(monkeypatch):
    doc, llm, sess, res, det, cons = _flow(monkeypatch, "STT", "ABSENT")
    rep = _report(doc, cons, sess, redact_sensitive_topics=False)
    raw = json.dumps(rep, ensure_ascii=False)
    assert LABEL in raw  # scelta esplicita dell'utente
    for secret in (STT_SECRET, OCR_SECRET, "Messaggio sintetico"):
        assert secret not in raw


def test_report_roundtrip_is_stable_and_validator_detects_tampering(monkeypatch):
    doc, llm, sess, res, det, cons = _flow(monkeypatch, "OCR", "PRESENT", 3)
    rep = _report(doc, cons, sess)
    rep.pop("_filename")
    again = json.loads(json.dumps(rep, ensure_ascii=False, sort_keys=True))
    assert again == rep and validate_semantic_verification_report(again) == []
    broken = json.loads(json.dumps(rep))
    broken["comparison"]["association"]["status"] = "NO_MATCH"
    broken["detection"]["query"]["query_mode"] = "LABEL_ONLY"
    broken["comparison"]["overlap_count"] = 99
    probs = validate_semantic_verification_report(broken)
    assert {"association.status", "detection.query", "comparison.overlap_count"} <= set(probs)
    del broken["audit"]
    assert "missing:audit" in validate_semantic_verification_report(broken)


def test_filename_is_predictable_and_does_not_leak_topic_or_source_names(monkeypatch):
    doc, llm, sess, res, det, cons = _flow(monkeypatch, "OCR", "ABSENT", doc_id="doc_fname")
    name = _report(doc, cons, sess)["_filename"]
    assert name.endswith(".json") and name.startswith("verification")
    low = name.casefold()
    for forbidden in ("etichetta", "sintetica", "whatsapp", "doc_fname", "alfa"):
        assert forbidden not in low
    assert re.fullmatch(r"[A-Za-z0-9_.\-]+", name)


# ---------------------------------------------------------------------------------------------
# 8. Snapshot: contenuto cambiato, non verificabile, preflight identico al service
# ---------------------------------------------------------------------------------------------

def test_changed_ocr_text_with_same_ids_is_a_mismatch_and_blocks_before_model(monkeypatch):
    doc = _derived_doc("doc_snap", "OCR")
    llm = Llm(monkeypatch, disc_msg="msg_doc_snap_3")
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)
    # stesso document_id, stessi evidence_id, testo OCR diverso (rielaborazione OCR successiva)
    other = _derived_doc("doc_snap", "OCR")
    bundles = list(other.bundles)
    ocr = bundles[3].image_ocr
    bundles[3] = dataclasses.replace(bundles[3], image_ocr=dataclasses.replace(ocr, full_text="TESTO OCR DIVERSO SINTETICO"))
    changed = ConversationEvidenceDocument(document_id="doc_snap", bundles=tuple(bundles), source_name="whatsapp_test")
    assert [s.evidence_id for s in changed.all_evidence_sections] == [s.evidence_id for s in doc.all_evidence_sections]
    assert check_document_snapshot(res.metadata, changed).status == SnapshotStatus.MISMATCH
    pre = evaluate_verification_preconditions(changed, res)
    assert pre.can_verify is False and pre.block_code == "SNAPSHOT_MISMATCH" and pre.block_message
    with pytest.raises(DocumentSnapshotMismatchError):
        _verify(changed, res, llm, sess)
    assert llm.detection_prompts == []
    assert state.get_document_analysis_state("doc_snap", state=sess).consistency_runs == ()
    # il documento originale resta verificabile
    assert evaluate_verification_preconditions(doc, res).can_verify is True


def test_discovery_without_fingerprint_is_unverifiable_never_a_false_proof(monkeypatch):
    doc = _derived_doc("doc_unv", "STT")
    llm = Llm(monkeypatch, disc_msg="msg_doc_unv_3")
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)
    meta = {k: v for k, v in res.metadata.items() if k != "document_fingerprint"}
    legacy = dataclasses.replace(res, metadata=meta)  # run storica senza fingerprint
    assert check_document_snapshot(meta, doc).status == SnapshotStatus.UNVERIFIABLE
    pre = evaluate_verification_preconditions(doc, legacy)
    assert pre.can_verify is True and pre.snapshot_status == "UNVERIFIABLE" and pre.warnings
    # la run storica (senza fingerprint) sostituisce quella registrata nel registro autorevole della sessione
    state.get_discovery_run_store(sess)["doc_unv"][legacy.metadata["run_id"]] = legacy
    from ui.application import execute_discovery_verification
    det, cons = execute_discovery_verification(
        document=doc, discovered_topic=legacy.topics[0], discovery_run_id=legacy.metadata["run_id"], client=llm.client,
        model_id=MODEL, session_state=sess, enable_checkpointing=False)
    assert cons.metadata["snapshot_status"] == "UNVERIFIABLE"
    rep = build_semantic_verification_export_report(
        consistency_result=cons, detection_result=det, discovery_result=legacy, document=doc)
    assert rep["document"]["snapshot"]["status"] == "UNVERIFIABLE"
    assert rep["document"]["snapshot"]["discovery_document_fingerprint"] is None
    assert rep["document"]["snapshot"]["fingerprints_equal"] is False
    assert "DOCUMENT_SNAPSHOT_EQUIVALENCE_NOT_PROVEN" in rep["comparison"]["limits"]
    assert rep["audit"]["checks"]["snapshot_equivalence_proven"] is False


def test_preconditions_other_document_and_missing_inputs():
    doc = _derived_doc("doc_pre", "OCR")
    assert evaluate_verification_preconditions(None, None).block_code == "NO_DOCUMENT"
    assert evaluate_verification_preconditions(doc, None).block_code == "DISCOVERY_RUN_UNAVAILABLE"
    assert evaluate_verification_preconditions(doc, None, topic_provenance_document_id="altro").block_code == "DOCUMENT_MISMATCH"
    empty = ConversationEvidenceDocument(document_id="doc_empty", bundles=())
    assert evaluate_verification_preconditions(empty, None).block_code == "NO_EVIDENCE"


# ---------------------------------------------------------------------------------------------
# 10. Discovery PARTIAL: verificabile ma esplicitamente limitata (decisione di prodotto)
# ---------------------------------------------------------------------------------------------

class _PartialDiscoveryLlm(Llm):
    def _urlopen(self, req: Any, timeout: float | None = None) -> _HttpResp:
        if getattr(req, "data", None) is not None:
            u = _user_of(req)
            if '"msg_id"' in u and "TARGET TOPIC" not in u and f"msg_doc_dp_10" not in u:
                return _HttpResp(self._body("risposta non JSON"))
        return super()._urlopen(req, timeout)


def test_partial_discovery_is_verifiable_but_declared_limited_everywhere(monkeypatch):
    doc = _derived_doc("doc_dp", "OCR", at=10, n=12)
    llm = _PartialDiscoveryLlm(monkeypatch, disc_msg="msg_doc_dp_10", decision="ABSENT")
    sess: dict[str, Any] = {}
    res = execute_topic_discovery(document=doc, client=llm.client, model_id=MODEL, max_topics=5, session_state=sess,
                                  enable_checkpointing=False)
    assert res.metadata["status"] == "PARTIAL"
    pre = evaluate_verification_preconditions(doc, res)
    assert pre.can_verify is True and pre.discovery_status == "PARTIAL"
    assert any("PARTIAL" in w for w in pre.warnings)
    det, cons = _verify(doc, res, llm, sess)
    assert cons.metadata["discovery_status"] == "PARTIAL"
    rep = _report(doc, cons, sess)
    cov = rep["discovery_source"]["coverage"]
    assert cov["is_complete"] is False and cov["coverage_status"] == "PARTIAL"
    assert cov["analyzed_evidence_count"] < cov["total_evidence_count"]
    assert "DISCOVERY_SOURCE_NOT_COMPLETE" in rep["comparison"]["limits"]
    view = describe_verification_outcome(cons)
    assert any("PARTIAL" in n for n in view.notes)


def test_partial_detection_is_reported_as_partial_not_complete(monkeypatch):
    doc, llm, sess, res, det, cons = _flow(monkeypatch, "OCR", "ABSENT", doc_id="doc_dpart")
    part_meta = {**det.metadata, "status": "PARTIAL"}
    det_p = dataclasses.replace(det, metadata=part_meta)
    cons_p = ConsistencyService.evaluate(doc.document_id, res.topics[0], det_p, res.metadata["run_id"], det.metadata["run_id"])
    cons_p = dataclasses.replace(cons_p, metadata={**cons_p.metadata, **{k: v for k, v in cons.metadata.items() if k.startswith(("discovery_", "snapshot", "detection_", "query"))}})
    assert cons_p.metadata["consistency_completeness"] == "PARTIAL"
    rep = build_semantic_verification_export_report(
        consistency_result=cons_p, detection_result=det_p, discovery_result=res, document=doc)
    assert rep["comparison"]["is_partial_detection"] is True
    assert rep["comparison"]["consistency_completeness"] == "PARTIAL"
    assert "DETECTION_NOT_COMPLETE" in rep["comparison"]["limits"]
    assert any("PARZIALE" in n for n in describe_verification_outcome(cons_p).notes)


def test_outcome_texts_distinguish_absent_from_error_and_never_claim_proof(monkeypatch):
    doc, llm, sess, res, det, cons = _flow(monkeypatch, "OCR", "ABSENT", doc_id="doc_txt")
    v = describe_verification_outcome(cons)
    assert "Discovery ha identificato questo topic" in v.headline and "non lo ha rilevato" in v.headline
    assert "non un errore" in v.headline and "né una prova" in v.headline
    assert "sicuramente assente" not in v.headline.casefold()
    assert "non funziona" not in v.headline.casefold()
    assert v.execution_origin_label.startswith("Detection calcolata")


# ---------------------------------------------------------------------------------------------
# 6, 7. Identità del topic: omonimi, due run, A/B/A, rerun, perdita di sessione
# ---------------------------------------------------------------------------------------------

def test_topic_uid_depends_on_run_and_validated_content_not_on_label_alone():
    a = DiscoveredTopic(label="X", short_description="d1", evidence_ids=("e1",))
    b = DiscoveredTopic(label="X", short_description="d2", evidence_ids=("e1",))
    c = DiscoveredTopic(label="X", short_description="d1", evidence_ids=("e2",))
    uids = {compute_discovered_topic_uid("r1", t) for t in (a, b, c)} | {compute_discovered_topic_uid("r2", a)}
    assert len(uids) == 4
    assert compute_discovered_topic_uid("r1", a) == compute_discovered_topic_uid("r1", a)


class _TwoSameLabelLlm(Llm):
    """Discovery con due topic omonimi ma descrizione/evidenze diverse (messaggi 2 e 4)."""

    def _urlopen(self, req: Any, timeout: float | None = None) -> _HttpResp:
        if getattr(req, "data", None) is not None:
            u = _user_of(req)
            if '"msg_id"' in u and "TARGET TOPIC" not in u:
                self.discovery_prompts.append(u)
                from tests.unit.test_consistency_verification_audit import _msg_alias_disc
                return _HttpResp(self._body(json.dumps({"topics": [
                    {"label": self.label, "short_description": "Prima descrizione sintetica", "evidence_ids": [_msg_alias_disc(u, "msg_doc_dup_2")]},
                    {"label": self.label, "short_description": "Seconda descrizione sintetica", "evidence_ids": [_msg_alias_disc(u, "msg_doc_dup_4")]},
                ]})))
        return super()._urlopen(req, timeout)


def test_same_label_topics_are_verified_and_selected_by_identity_not_label(monkeypatch):
    doc = _derived_doc("doc_dup", "OCR")
    llm = _TwoSameLabelLlm(monkeypatch, disc_msg="msg_doc_dup_2", decision="ABSENT")
    sess: dict[str, Any] = {}
    res = execute_topic_discovery(document=doc, client=llm.client, model_id=MODEL, max_topics=5, session_state=sess,
                                  enable_checkpointing=False)
    if len(res.topics) != 2:
        pytest.skip("la Discovery ha fuso i topic omonimi: scenario non producibile con questo contratto")
    t1, t2 = res.topics
    assert t1.label == t2.label and t1.short_description != t2.short_description
    _d1, c1 = _verify(doc, res, llm, sess, topic=t1)
    _d2, c2 = _verify(doc, res, llm, sess, topic=t2)
    assert c1.metadata["discovery_topic_uid"] != c2.metadata["discovery_topic_uid"]
    allc = state.get_document_analysis_state("doc_dup", state=sess).consistency_runs
    rid = res.metadata["run_id"]
    assert select_verification_results(allc, "doc_dup", rid, t1) == (c1,)
    assert select_verification_results(allc, "doc_dup", rid, t2) == (c2,)
    # ciascun export descrive il PROPRIO topic: la descrizione (presenza) e le evidenze coincidono con quello scelto
    r1, r2 = _report(doc, c1, sess), _report(doc, c2, sess)
    assert r1["discovery_source"]["topic"]["topic_uid"] != r2["discovery_source"]["topic"]["topic_uid"]
    assert r1["discovery_source"]["topic"]["evidence_ids"] != r2["discovery_source"]["topic"]["evidence_ids"]


def test_two_discovery_runs_same_label_never_mix_results(monkeypatch):
    doc = _derived_doc("doc_2r", "STT")
    llm = Llm(monkeypatch, disc_msg="msg_doc_2r_3", decision="ABSENT")
    sess: dict[str, Any] = {}
    res_a = _discover(doc, llm, sess)
    res_b = _discover(doc, llm, sess)
    assert res_a.metadata["run_id"] != res_b.metadata["run_id"]
    _da, ca = _verify(doc, res_a, llm, sess)
    _db, cb = _verify(doc, res_b, llm, sess)
    allc = state.get_document_analysis_state("doc_2r", state=sess).consistency_runs
    assert select_verification_results(allc, "doc_2r", res_a.metadata["run_id"], res_a.topics[0]) == (ca,)
    assert select_verification_results(allc, "doc_2r", res_b.metadata["run_id"], res_b.topics[0]) == (cb,)
    ra, rb = _report(doc, ca, sess), _report(doc, cb, sess)
    assert ra["comparison"]["association"]["discovery_run_id"] != rb["comparison"]["association"]["discovery_run_id"]
    assert ra["detection"]["run_id"] != rb["detection"]["run_id"]


def test_aba_selection_and_add_consistency_result_is_idempotent(monkeypatch):
    doc = _derived_doc("doc_aba", "OCR")
    llm = Llm(monkeypatch, disc_msg="msg_doc_aba_3", decision="ABSENT")
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)
    _d, ca = _verify(doc, res, llm, sess)
    st = state.get_document_analysis_state("doc_aba", state=sess)
    n = len(st.consistency_runs)
    st.add_consistency_result(ca)  # stesso (discovery_run_id, detection_run_id): nessun duplicato
    st.add_consistency_result(ca)
    assert len(st.consistency_runs) == n == 1
    # un altro topic/documento non eredita il risultato di A
    other = DiscoveredTopic(label="Altro", short_description="Altro topic", evidence_ids=res.topics[0].evidence_ids)
    assert select_verification_results(st.consistency_runs, "doc_aba", res.metadata["run_id"], other) == ()
    assert select_verification_results(st.consistency_runs, "altro_doc", res.metadata["run_id"], res.topics[0]) == ()
    assert select_verification_results(st.consistency_runs, "doc_aba", None, res.topics[0]) == ()
    assert select_verification_results(st.consistency_runs, "doc_aba", "run_inesistente", res.topics[0]) == ()
    # la nuova Discovery sullo stesso documento non fa apparire come "corrente" il risultato precedente
    res2 = _discover(doc, llm, sess)
    assert select_verification_results(st.consistency_runs, "doc_aba", res2.metadata["run_id"], res2.topics[0]) == ()


def test_find_discovery_run_matches_run_id_and_document_only(monkeypatch):
    doc = _derived_doc("doc_fd", "OCR")
    llm = Llm(monkeypatch, disc_msg="msg_doc_fd_3")
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)
    assert find_discovery_run(res.metadata["run_id"], "doc_fd", [res]) is res
    assert find_discovery_run(res.metadata["run_id"], "altro", [res]) is None
    assert find_discovery_run(None, "doc_fd", [res]) is None
    assert find_discovery_run("x", "doc_fd", [res]) is None


# ---------------------------------------------------------------------------------------------
# 5. Il flusso manuale resta indipendente e invariato
# ---------------------------------------------------------------------------------------------

def test_manual_detection_is_never_linked_nor_exportable_as_verification(monkeypatch):
    doc = _derived_doc("doc_man", "OCR")
    llm = Llm(monkeypatch, disc_msg="msg_doc_man_3", decision="ABSENT")
    sess: dict[str, Any] = {}
    res = _discover(doc, llm, sess)
    for q in (build_topic_query(label=LABEL, description=None), build_topic_query(label=LABEL, description=DESC)):
        man = execute_manual_topic_detection(document=doc, topic=q, client=llm.client, model_id=MODEL,
                                             session_state=sess, enable_checkpointing=False)
        assert man.metadata["origin_flow"] == "MANUAL_DETECTION"
        assert state.get_document_analysis_state("doc_man", state=sess).consistency_runs == ()
        raw = export_topic_detection_json(man, doc, session_state=sess)[0]
        rep = json.loads(raw)
        assert rep["result"]["linked_discovery_run_id"] == "NOT_RECORDED"
        assert rep["experiment"]["origin_flow"] == "MANUAL_DETECTION"
        assert rep.get("report_type") != "SEMANTIC_CONSISTENCY_VERIFICATION"
        fake = ConsistencyService.evaluate(doc.document_id, res.topics[0], man, res.metadata["run_id"], man.metadata["run_id"])
        with pytest.raises(SemanticVerificationExportError) as ei:
            build_semantic_verification_export_report(
                consistency_result=fake, detection_result=man, discovery_result=res, document=doc)
        assert str(ei.value).startswith(("RUN_LINK_MISMATCH", "NOT_A_VERIFICATION_RUN"))


# ---------------------------------------------------------------------------------------------
# 15. Errori fail-closed, sessione persa, doppio click, serializzazione
# ---------------------------------------------------------------------------------------------

def test_export_fails_closed_with_closed_codes_and_understandable_messages(monkeypatch):
    doc, llm, sess, res, det, cons = _flow(monkeypatch, "OCR", "ABSENT", doc_id="doc_err")
    # sessione persa: nessuna Detection / nessuna Discovery
    with pytest.raises(SemanticVerificationExportError) as e1:
        export_semantic_verification_json(cons, doc, session_state={})
    assert str(e1.value) == "DETECTION_RESULT_MISSING" and "ripetere la verifica" in describe_export_error(str(e1.value))
    lost = {k: v for k, v in sess.items()}
    st = state.get_document_analysis_state("doc_err", state=lost)
    st.discovery_runs = ()
    lost["discovery_results"] = []
    with pytest.raises(SemanticVerificationExportError) as e2:
        export_semantic_verification_json(cons, doc, session_state=lost)
    assert str(e2.value) == "DISCOVERY_RESULT_MISSING"
    # orfano: ConsistencyResult che punta a una Detection sconosciuta
    orphan = dataclasses.replace(cons, detection_run_id="run_sconosciuta")
    with pytest.raises(SemanticVerificationExportError):
        export_semantic_verification_json(orphan, doc, session_state=sess)
    # esito registrato incoerente con il ricalcolo del comparatore
    stale = dataclasses.replace(cons, status=ConsistencyStatus.CONSISTENT)
    with pytest.raises(SemanticVerificationExportError, match="CONSISTENCY_RESULT_STALE"):
        build_semantic_verification_export_report(consistency_result=stale, detection_result=det, discovery_result=res, document=doc)
    # link Discovery->Detection alterato
    bad_det = dataclasses.replace(det, metadata={**det.metadata, "verified_discovery_run_id": "altra"})
    with pytest.raises(SemanticVerificationExportError, match="RUN_LINK_MISMATCH"):
        build_semantic_verification_export_report(consistency_result=cons, detection_result=bad_det, discovery_result=res, document=doc)
    # documento diverso
    other = _derived_doc("doc_altro", "OCR")
    with pytest.raises(SemanticVerificationExportError, match="DOCUMENT_ID_MISMATCH"):
        build_semantic_verification_export_report(consistency_result=cons, detection_result=det, discovery_result=res, document=other)
    # query a sola label registrata come verifica: rifiutata
    lo = dataclasses.replace(det, metadata={**det.metadata, "query_mode": "LABEL_ONLY"})
    with pytest.raises(SemanticVerificationExportError, match="QUERY_IS_LABEL_ONLY"):
        build_semantic_verification_export_report(consistency_result=cons, detection_result=lo, discovery_result=res, document=doc)
    for code in ("DETECTION_RESULT_MISSING", "SNAPSHOT_MISMATCH_X", "PRIVACY_CHECK_FAILED"):
        assert describe_export_error(code)


def test_serialization_error_does_not_clear_session_and_double_export_is_stable(monkeypatch):
    doc, llm, sess, res, det, cons = _flow(monkeypatch, "OCR", "ABSENT", doc_id="doc_dbl")
    st = state.get_document_analysis_state("doc_dbl", state=sess)
    before = (len(st.consistency_runs), len(st.detection_runs), len(llm.detection_prompts))
    import ui.verification as uv

    def _boom(**_kw):
        raise SemanticVerificationExportError("COMPARATOR_RECHECK_FAILED")

    monkeypatch.setattr(uv, "build_semantic_verification_export_report", _boom)
    with pytest.raises(SemanticVerificationExportError):
        export_semantic_verification_json(cons, doc, session_state=sess)
    assert (len(st.consistency_runs), len(st.detection_runs), len(llm.detection_prompts)) == before
    monkeypatch.undo()
    a = json.loads(export_semantic_verification_json(cons, doc, session_state=sess)[0])
    b = json.loads(export_semantic_verification_json(cons, doc, session_state=sess)[0])
    for k in ("generated_at", "export_id"):
        a["experiment"].pop(k), b["experiment"].pop(k)
    assert a == b, "due clic ravvicinati producono lo stesso rapporto"
    assert (len(st.consistency_runs), len(st.detection_runs), len(llm.detection_prompts)) == before


def test_authoritative_discovery_is_attested_only_when_registered_as_authoritative(monkeypatch):
    doc, llm, sess, res, det, cons = _flow(monkeypatch, "OCR", "ABSENT", doc_id="doc_auth")
    rep = _report(doc, cons, sess)
    # Detection mai LIVE_AUTHORITATIVE (nessuna attestazione per le Detection: limite dichiarato, non nascosto)
    assert rep["detection"]["attestation_level"] != "LIVE_AUTHORITATIVE"
    assert rep["discovery_source"]["attestation_level"] in {"LIVE_AUTHORITATIVE", "UNATTESTED"}


# ---------------------------------------------------------------------------------------------
# 11. Checkpoint: origine d'esecuzione dichiarata, risultato scientifico invariato
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("meta,expected", [
    ({"chunk_count": 1, "recovered_chunk_count": 0}, ExecutionOrigin.COMPUTED_THIS_RUN),
    ({"chunk_count": 1, "recovered_chunk_count": 1}, ExecutionOrigin.RESTORED_FROM_CHECKPOINT),
    ({"chunk_count": 3, "recovered_chunk_count": 1}, ExecutionOrigin.MIXED),
    ({"chunk_count": 3, "recovered_chunk_count": 3}, ExecutionOrigin.RESTORED_FROM_CHECKPOINT),
    ({"chunk_count": 1, "detection_diagnostics": {"microchunks_recovered_from_checkpoint": 2, "microchunks_computed_this_run": 0}},
     ExecutionOrigin.RESTORED_FROM_CHECKPOINT),
    ({"chunk_count": 1, "detection_diagnostics": {"microchunks_recovered_from_checkpoint": 2, "microchunks_computed_this_run": 1}},
     ExecutionOrigin.MIXED),
    ({"chunk_count": 1, "detection_diagnostics": {"microchunks_recovered_from_checkpoint": 0, "microchunks_computed_this_run": 4}},
     ExecutionOrigin.COMPUTED_THIS_RUN),
    ({}, ExecutionOrigin.UNDETERMINED),
    (None, ExecutionOrigin.UNDETERMINED),
    ({"chunk_count": "n/d"}, ExecutionOrigin.UNDETERMINED),
])
def test_execution_origin_derivation_table(meta, expected):
    assert derive_execution_origin(meta) == expected


def test_checkpoint_restored_verification_is_declared_and_scientifically_identical(monkeypatch, tmp_path):
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")
    doc = _derived_doc("doc_ck", "OCR")
    llm = Llm(monkeypatch, disc_msg="msg_doc_ck_3", decision="ABSENT")
    sess: dict[str, Any] = {}
    res = execute_topic_discovery(document=doc, client=llm.client, model_id=MODEL, max_topics=5,
                                  session_state=sess, checkpoint_store=store)
    from ui.application import execute_discovery_verification

    def _run():
        return execute_discovery_verification(
            document=doc, discovered_topic=res.topics[0], discovery_run_id=res.metadata["run_id"],
            client=llm.client, model_id=MODEL, session_state=sess, checkpoint_store=store)

    d1, c1 = _run()
    n1 = len(llm.detection_prompts)
    d2, c2 = _run()
    assert n1 == 1 and len(llm.detection_prompts) == 1, "la seconda esecuzione e' un ripristino: 0 richieste al modello"
    assert (d1.decision, d1.evidence_ids) == (d2.decision, d2.evidence_ids) and c1.status == c2.status
    assert c1.metadata["detection_execution_origin"] == "COMPUTED_THIS_RUN"
    assert c2.metadata["detection_execution_origin"] == "RESTORED_FROM_CHECKPOINT"
    r2 = _report(doc, c2, sess)
    assert r2["detection"]["execution"]["execution_origin"] == "RESTORED_FROM_CHECKPOINT"
    assert r2["detection"]["execution"]["http_requests_sent"] == 0
    assert "DETECTION_NOT_FULLY_COMPUTED_THIS_RUN" in r2["comparison"]["limits"]
    assert "recuperato" in describe_verification_outcome(c2).execution_origin_label.casefold()
    # nessun ConsistencyResult duplicato per due Detection distinte (run_id diversi)
    st = state.get_document_analysis_state("doc_ck", state=sess)
    assert len({c.detection_run_id for c in st.consistency_runs}) == len(st.consistency_runs)


# ---------------------------------------------------------------------------------------------
# 17. Nessuna regressione del difetto di telemetria individuato in PR #10 e comparatore invariato
# ---------------------------------------------------------------------------------------------

def test_comparator_is_the_single_source_of_outcome_and_report_never_overrides_it(monkeypatch):
    doc, llm, sess, res, det, cons = _flow(monkeypatch, "OCR", "PRESENT", 3, doc_id="doc_cmp")
    recomputed = ConsistencyService.evaluate(doc.document_id, res.topics[0], det, res.metadata["run_id"], det.metadata["run_id"])
    assert recomputed.status == cons.status == ConsistencyStatus.CONSISTENT
    assert _report(doc, cons, sess)["comparison"]["consistency_status"] == recomputed.status.value


def test_no_match_in_manual_export_is_not_a_discrepancy_and_unaffected(monkeypatch):
    doc = _derived_doc("doc_nm", "OCR")
    llm = Llm(monkeypatch, disc_msg="msg_doc_nm_3", decision="ABSENT")
    sess: dict[str, Any] = {}
    man = execute_manual_topic_detection(document=doc, topic=build_topic_query(label=LABEL, description=None),
                                         client=llm.client, model_id=MODEL, session_state=sess, enable_checkpointing=False)
    rep = json.loads(export_topic_detection_json(man, doc, session_state=sess)[0])
    assert rep["result"]["consistency_association_flag"] == "NO_MATCH"
    # NO_MATCH (nessuna associazione) non e' una discrepanza semantica: nessun esito di consistenza nel flusso manuale
    assert not rep["result"].get("semantic_consistency")
    assert "DISCREPANCY" not in json.dumps(rep["result"])


# ---------------------------------------------------------------------------------------------
# 14. Interfaccia Streamlit reale (AppTest): saltati se il framework di test non e' installato
# ---------------------------------------------------------------------------------------------

def _apptest_available() -> bool:
    try:
        import streamlit.testing.v1  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _apptest_available(), reason="streamlit.testing non installato: test UI NON eseguito (nessun PASS finto)")
def test_apptest_absent_verification_shows_direct_export_button_without_manual_detection():
    from tests.unit.test_apptest_flows import APP_PATH, MockAppLlmClient, _setup_app_with_doc
    from ui.demo import build_synthetic_demo_dataset
    from streamlit.testing.v1 import AppTest  # noqa: F401
    from ui.state import DatasetMode

    doc, _, _ = build_synthetic_demo_dataset()
    eid = doc.all_evidence_sections[0].evidence_id
    client = MockAppLlmClient(responses=[
        {"topics": [{"label": "Logistica Depositi", "short_description": "Ispezione casse e magazzini", "evidence_ids": [eid]}]},
        {"decision": "ABSENT", "evidence_ids": [], "rationale": "Nessuna evidenza."},
    ])
    at = _setup_app_with_doc(doc, mode=DatasetMode.FILE)
    at.session_state["lm_studio_client"] = client
    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()
    [b for b in at.button if b.key == "btn_run_live_discovery"][0].click()
    at.run()
    verify = [b for b in at.button if "verify_disc_topic" in (b.key or "")]
    assert verify and not verify[0].disabled
    verify[0].click()
    at.run()
    assert not at.exception
    assert client.call_count == 2
    cons = state.get_consistency_results(at.session_state) if hasattr(state, "get_consistency_results") else None
    if cons is not None:
        assert [c.status for c in cons] == [ConsistencyStatus.DISCREPANCY_ABSENT]
    downloads = at.get("download_button")
    assert any("Esporta verifica JSON" in str(getattr(d, "proto", d)) for d in downloads)
    # un rerun non rilancia il modello e mantiene il bottone
    at.run()
    assert client.call_count == 2
    assert any("Esporta verifica JSON" in str(getattr(d, "proto", d)) for d in at.get("download_button"))
