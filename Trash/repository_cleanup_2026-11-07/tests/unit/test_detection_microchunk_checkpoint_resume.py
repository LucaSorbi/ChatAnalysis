"""
tests/unit/test_detection_microchunk_checkpoint_resume.py
---------------------------------------------------------
Regressione della Topic Detection a microchunk (pre-splitting, checkpoint, resume, failure handling).

Tutti i test usano DATI SINTETICI e un client LLM finto (nessuna inferenza reale, nessun LM Studio,
nessun database reale): esito massimo MOCK VERIFIED.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping, Sequence

import pytest

from ai.backend import (
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiStructuredOutputError,
    BaseLocalLlmClient,
    LlmCompletionResponse,
)
from ai.models import (
    ConversationEvidenceDocument,
    MessageEvidenceBundle,
    TopicDecision,
    TopicQuery,
)
from analysis.checkpoint import AnalysisCheckpointStore, compute_document_fingerprint
from analysis.models import OperationalAnalysisFailedError
from analysis.progress import AnalysisProgressEvent, AnalysisProgressTracker
from analysis.service import LocalTopicAnalysisService
from importer.models import RawRecord
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult

MODEL = "qwen2.5-7b-instruct"
TOPIC = TopicQuery(topic_id="top_sport", label="Sport", description="Attività sportive")


# ----------------------------------------------------------------------------
# Fixture sintetiche
# ----------------------------------------------------------------------------

def _doc(doc_id: str, n: int, text_suffix: str = "per verifica evidenze") -> ConversationEvidenceDocument:
    bundles: list[MessageEvidenceBundle] = []
    for i in range(n):
        raw = RawRecord(
            source_name="whatsapp_test",
            source_path="/test",
            source_record_id=str(i),
            record_type="message",
            raw_fields={"text": f"Messaggio sintetico {i}", "timestamp": "2026-10-16T12:00:00Z"},
            media_reference=None,
            metadata={},
        )
        ts = NormalizedTimestamp(
            status=TimestampTzStatus.KNOWN_UTC,
            utc_datetime=datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc),
            naive_datetime=None,
            iso_string="2026-10-16T12:00:00Z",
            raw_value="2026-10-16T12:00:00Z",
        )
        txt = f"Messaggio sintetico {i} {text_suffix}"
        norm = NormalizedRecord(
            raw_record=raw,
            validation_result=ValidationResult(record=raw, issues=()),
            source_name="whatsapp_test",
            source_record_id=str(i),
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=txt,
            media_reference=None,
        )
        um = UnifiedMessage(
            message_id=f"msg_{doc_id}_{i}",
            source_name="whatsapp_test",
            source_record_id=str(i),
            source_path="/test",
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=txt,
            media_reference=None,
            provenance_record=norm,
        )
        bundles.append(MessageEvidenceBundle(message=um))
    return ConversationEvidenceDocument(document_id=doc_id, bundles=tuple(bundles), source_name="whatsapp_test")


class FakeClient(BaseLocalLlmClient):
    """Client finto deterministico: nessuna rete."""

    def __init__(self, handler: Any, model_id: str = MODEL) -> None:
        super().__init__()
        self.handler = handler
        self.model_id = model_id
        self.calls: list[str] = []
        self.request_listener = None

    def is_available(self, timeout_seconds: float = 2.0) -> bool:
        return True

    def list_models(self) -> tuple[str, ...]:
        return (self.model_id,)

    def chat_completion(
        self,
        messages: Sequence[Mapping[str, str]],
        model_id: str | None = None,
        schema: Mapping[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float = 60.0,
        max_tokens: int | None = None,
    ) -> LlmCompletionResponse:
        user = next((m["content"] for m in messages if m.get("role") == "user"), "")
        self.calls.append(user)
        if self.request_listener:
            try:
                self.request_listener.record_request_sent()
            except Exception:
                pass
        return self.handler(user, len(self.calls))


def _ids(user_text: str, doc_id: str) -> list[int]:
    """Indici dei messaggi sintetici presenti in UNA richiesta (identità dei messaggi, non alias)."""
    return sorted({int(m) for m in re.findall(rf"msg_{re.escape(doc_id)}_(\d+)", user_text)})


def _resp(decision: str, eids: Sequence[str] = (), model: str = MODEL) -> LlmCompletionResponse:
    return LlmCompletionResponse(
        content=json.dumps({"decision": decision, "evidence_ids": list(eids), "rationale": "Esito sintetico di test."}),
        model=model,
        raw_response={"finish_reason": "stop"},
    )


def _service(client: FakeClient, store: AnalysisCheckpointStore | None = None, **kw: Any) -> LocalTopicAnalysisService:
    return LocalTopicAnalysisService(
        client=client,
        checkpoint_store=store,
        enable_checkpointing=store is not None,
        **kw,
    )


def _run(svc: LocalTopicAnalysisService, doc: ConversationEvidenceDocument, client: FakeClient, topic: TopicQuery = TOPIC, model: str = MODEL, **kw: Any):
    return svc.run_topic_detection(document=doc, topic=topic, client=client, model_name=model, **kw)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Il retry interno attende 0.5 s: nei test si elimina l'attesa."""
    monkeypatch.setattr("ai.topics.time.sleep", lambda s: None)


@pytest.fixture
def store(tmp_path):
    return AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")


def _cfg_fp(store: AnalysisCheckpointStore, doc: ConversationEvidenceDocument) -> tuple[str, str]:
    doc_fp = compute_document_fingerprint(doc)
    meta = store.get_latest_checkpoint_metadata_for_document(doc.document_id, doc_fp, analysis_type="detection")
    assert meta is not None
    return doc_fp, meta["config_fingerprint"]


# ----------------------------------------------------------------------------
# 1-2. Pre-splitting preventivo e copertura esatta delle evidenze
# ----------------------------------------------------------------------------

def test_presplit_avoids_huge_call_and_covers_every_message_exactly_once():
    doc = _doc("det_a", 20)
    client = FakeClient(lambda u, n: _resp("ABSENT"))
    res = _run(_service(client), doc, client)

    per_call = [_ids(u, "det_a") for u in client.calls]
    assert max(len(c) for c in per_call) <= 8, "nessuna chiamata deve contenere il parent intero"
    assert len(client.calls) == 3  # 8 + 8 + 4
    flat = [i for c in per_call for i in c]
    assert sorted(flat) == list(range(20)), "copertura completa, senza duplicati né omissioni"
    assert res.decision == TopicDecision.ABSENT  # ABSENT solo perché TUTTI i microchunk sono validati
    assert res.metadata["status"] == "SUCCESS"
    diag = res.metadata["detection_diagnostics"]
    assert diag["microchunks_total"] == 3 and diag["microchunks_completed"] == 3
    assert diag["detect_topic_calls"] == 3 and diag["microchunks_failed"] == 0


def test_present_evidence_ids_are_real_ids_of_the_microchunk():
    doc = _doc("det_p", 20)

    def handler(user: str, n: int):
        ids = _ids(user, "det_p")
        return _resp("PRESENT", ["E001"]) if 8 in ids else _resp("ABSENT")

    client = FakeClient(handler)
    res = _run(_service(client), doc, client)
    assert res.decision == TopicDecision.PRESENT
    assert res.evidence_ids == ("msg_det_p_8::ORIGINAL_TEXT",)
    assert set(res.evidence_ids) <= {s.evidence_id for s in doc.all_evidence_sections}


def test_presplit_also_applies_to_multichunk_documents(store):
    doc = _doc("det_m", 40)
    client = FakeClient(lambda u, n: _resp("ABSENT"))
    svc = _service(client, store, max_chunk_bundles=20)
    res = _run(svc, doc, client)
    per_call = [_ids(u, "det_m") for u in client.calls]
    assert max(len(c) for c in per_call) <= 8
    assert sorted(i for c in per_call for i in c) == list(range(40))
    assert res.decision == TopicDecision.ABSENT
    assert res.metadata["detection_diagnostics"]["parent_chunks_total"] == 2
    assert res.metadata["detection_diagnostics"]["parent_chunks_completed"] == 2


# ----------------------------------------------------------------------------
# 3. Un microchunk fallito non certifica il parent
# ----------------------------------------------------------------------------

def _fail_on_message(bad: int, doc_id: str):
    def handler(user: str, n: int):
        if bad in _ids(user, doc_id):
            raise AiStructuredOutputError("JSONDecodeError simulato: risposta non conforme a JSON")
        return _resp("ABSENT")
    return handler


def test_failed_microchunk_never_certifies_parent_single_chunk(store):
    doc = _doc("det_f", 20)
    client = FakeClient(_fail_on_message(9, "det_f"))
    res = _run(_service(client, store), doc, client)

    assert res.metadata["status"] == "PARTIAL"
    assert res.decision == TopicDecision.UNCERTAIN, "nessun ABSENT globale con copertura incompleta"
    assert res.metadata["failed_chunk_count"] >= 1
    doc_fp, cfg_fp = _cfg_fp(store, doc)
    chk = store.load_checkpoint(doc_fp, cfg_fp)
    assert chk is not None and dict(chk.serialized_results) == {}, "il parent NON deve essere salvato come completato"
    saved = {s["subchunk_index"] for s in store.get_subchunks(doc_fp, cfg_fp, parent_chunk_index=0)}
    assert saved == {0, 2}, "sono salvati solo i microchunk validati (il microchunk 1 contiene il messaggio difettoso)"


def test_failed_microchunk_never_certifies_parent_multichunk(store):
    doc = _doc("det_fm", 40)
    client = FakeClient(_fail_on_message(25, "det_fm"))
    res = _run(_service(client, store, max_chunk_bundles=20), doc, client)
    assert res.metadata["status"] == "PARTIAL"
    assert res.decision == TopicDecision.UNCERTAIN
    d = res.metadata["detection_diagnostics"]
    assert d["parent_chunks_total"] == 2 and d["parent_chunks_completed"] == 1
    doc_fp, cfg_fp = _cfg_fp(store, doc)
    assert set(dict(store.load_checkpoint(doc_fp, cfg_fp).serialized_results)) == {"0"}


def test_present_survives_partial_coverage_but_status_stays_partial(store):
    doc = _doc("det_pp", 20)

    def handler(user: str, n: int):
        ids = _ids(user, "det_pp")
        if 9 in ids and len(ids) == 1:
            raise AiStructuredOutputError("JSONDecodeError simulato")
        if 9 in ids:
            raise AiStructuredOutputError("JSONDecodeError simulato")
        return _resp("PRESENT", ["E001"]) if 0 in ids else _resp("ABSENT")

    client = FakeClient(handler)
    res = _run(_service(client, store), doc, client)
    assert res.decision == TopicDecision.PRESENT
    assert res.metadata["status"] == "PARTIAL"
    assert res.metadata["failed_chunk_count"] >= 1


# ----------------------------------------------------------------------------
# 4. Interruzione e resume: solo i microchunk mancanti sono re-inferiti
# ----------------------------------------------------------------------------

def test_resume_reinfers_only_missing_microchunks(store):
    doc = _doc("det_r", 20)
    c1 = FakeClient(_fail_on_message(9, "det_r"))
    r1 = _run(_service(c1, store), doc, c1)
    assert r1.metadata["status"] == "PARTIAL"

    c2 = FakeClient(lambda u, n: _resp("ABSENT"))
    r2 = _run(_service(c2, store), doc, c2)  # nuovo service, stesso store: simula il riavvio
    per_call = [_ids(u, "det_r") for u in c2.calls]
    assert per_call == [list(range(8, 16))], "soltanto il microchunk mancante deve essere re-inferito"
    assert r2.metadata["status"] == "SUCCESS"
    assert r2.decision == TopicDecision.ABSENT
    assert r2.metadata["detection_diagnostics"]["microchunks_recovered_from_checkpoint"] == 2


def test_abort_on_unresponsive_backend_keeps_progress_and_resumes(store):
    doc = _doc("det_i", 40)  # 5 microchunk

    def dying(user: str, n: int):
        if 0 in _ids(user, "det_i"):
            return _resp("ABSENT")
        raise AiBackendTimeoutError("timeout simulato")

    c1 = FakeClient(dying)
    with pytest.raises(AiBackendTimeoutError):
        _run(_service(c1, store), doc, c1)
    # circuit breaker: 1 chiamata riuscita + 3 microchunk consecutivi in timeout (2 tentativi ciascuno)
    assert len(c1.calls) == 1 + 3 * 2

    doc_fp, cfg_fp = _cfg_fp(store, doc)
    assert {s["subchunk_index"] for s in store.get_subchunks(doc_fp, cfg_fp, parent_chunk_index=0)} == {0}

    c2 = FakeClient(lambda u, n: _resp("ABSENT"))
    r2 = _run(_service(c2, store), doc, c2)
    assert [_ids(u, "det_i")[0] for u in c2.calls] == [8, 16, 24, 32]
    assert r2.metadata["status"] == "SUCCESS"


# ----------------------------------------------------------------------------
# 5. Configurazione / documento / modello diversi non riusano checkpoint incompatibili
# ----------------------------------------------------------------------------

def test_incompatible_runs_never_reuse_checkpoint(store):
    doc = _doc("det_c", 20)
    prime = FakeClient(_fail_on_message(9, "det_c"))
    assert _run(_service(prime, store), doc, prime).metadata["status"] == "PARTIAL"

    # (a) modello diverso
    llama = "llama-3.1-8b-instruct"
    ca = FakeClient(lambda u, n: _resp("ABSENT", model=llama), model_id=llama)
    _run(_service(ca, store), doc, ca, model=llama)
    assert len(ca.calls) == 3

    # (b) documento con contenuto diverso (stesso document_id)
    doc_b = _doc("det_c", 20, text_suffix="contenuto alterato")
    cb = FakeClient(lambda u, n: _resp("ABSENT"))
    _run(_service(cb, store), doc_b, cb)
    assert len(cb.calls) == 3

    # (c) topic diverso
    other = TopicQuery(topic_id="top_altro", label="Lavoro", description="Attività lavorative")
    cc = FakeClient(lambda u, n: _resp("ABSENT"))
    _run(_service(cc, store), doc, cc, topic=other)
    assert len(cc.calls) == 3

    # controllo: stessa identità => soltanto il microchunk mancante
    cd = FakeClient(lambda u, n: _resp("ABSENT"))
    _run(_service(cd, store), doc, cd)
    assert len(cd.calls) == 1


def test_tampered_subchunk_is_rejected_fail_closed(store):
    doc = _doc("det_t", 20)
    prime = FakeClient(_fail_on_message(9, "det_t"))
    _run(_service(prime, store), doc, prime)
    doc_fp, cfg_fp = _cfg_fp(store, doc)
    sub0 = store.get_subchunk(doc_fp, cfg_fp, 0, 0)
    assert sub0 is not None

    truncated = json.dumps(sub0["evidence_ids"][:-1])  # insieme di evidenze diverso dal piano corrente
    with store._connect() as conn:
        conn.execute(
            "UPDATE checkpoint_subchunks SET evidence_ids = ? WHERE document_fingerprint = ? AND config_fingerprint = ? "
            "AND parent_chunk_index = 0 AND subchunk_index = 0;",
            (truncated, doc_fp, cfg_fp),
        )
    c = FakeClient(lambda u, n: _resp("ABSENT"))
    _run(_service(c, store), doc, c)
    ids = [_ids(u, "det_t") for u in c.calls]
    assert ids == [list(range(0, 8)), list(range(8, 16))], "micro 0 manomesso e micro 1 mancante vanno re-inferiti; micro 2 no"


def test_store_rejects_detection_subchunk_with_foreign_evidence(store):
    doc = _doc("det_s", 20)
    prime = FakeClient(_fail_on_message(9, "det_s"))
    _run(_service(prime, store), doc, prime)
    doc_fp, cfg_fp = _cfg_fp(store, doc)
    sub = store.get_subchunk(doc_fp, cfg_fp, 0, 0)
    payload = dict(sub["serialized_result"])
    payload["decision"] = "PRESENT"
    payload["evidence_ids"] = ["ev_ESTRANEA::ORIGINAL_TEXT"]
    with store._connect() as conn:
        conn.execute(
            "UPDATE checkpoint_subchunks SET serialized_result = ? WHERE document_fingerprint = ? AND config_fingerprint = ? "
            "AND parent_chunk_index = 0 AND subchunk_index = 0;",
            (json.dumps(payload), doc_fp, cfg_fp),
        )
    from analysis.service import LocalTopicAnalysisService as _S  # noqa: F401
    plan = _service(FakeClient(lambda u, n: _resp("ABSENT")), store).create_analysis_plan(doc, "detection", topic_query=TOPIC)
    chk = store.load_and_validate_checkpoint(
        document=doc, config_fingerprint=cfg_fp, chunks=list(plan.chunks), expected_topic_query=TOPIC, expected_analysis_type="detection"
    )
    assert chk is None, "una evidenza estranea al sotto-blocco invalida il checkpoint"


# ----------------------------------------------------------------------------
# 6. Errori GBNF / timeout / JSON malformato: limiti definiti, nessun loop infinito
# ----------------------------------------------------------------------------

# Limite teorico per un microchunk da 8 messaggi: 15 nodi di bisezione (profondità 3) x 2 tentativi = 30 chiamate.
MAX_CALLS_8 = 30


def test_gbnf_errors_are_bounded_and_end_in_coherent_failure():
    doc = _doc("det_g", 20)

    def handler(user: str, n: int):
        err = AiBackendRequestError("Errore grammatica simulato")
        err.error_category = "INFERENCE_BACKEND_ERROR"
        raise err

    client = FakeClient(handler)
    events: list[AnalysisProgressEvent] = []
    with pytest.raises(OperationalAnalysisFailedError):
        _run(_service(client), doc, client, progress_callback=events.append)
    assert len(client.calls) <= 2 * MAX_CALLS_8 + 14  # microchunk da 8, 8 e 4 messaggi
    assert events[-1].is_failed and not events[-1].is_complete


def test_malformed_json_is_bounded_and_ends_in_coherent_failure():
    doc = _doc("det_j", 20)
    client = FakeClient(lambda u, n: LlmCompletionResponse(content="questo non è JSON", model=MODEL, raw_response={"finish_reason": "stop"}))
    events: list[AnalysisProgressEvent] = []
    with pytest.raises(OperationalAnalysisFailedError):
        _run(_service(client), doc, client, progress_callback=events.append)
    assert len(client.calls) <= 2 * MAX_CALLS_8 + 14
    assert events[-1].is_failed


def test_consecutive_timeouts_stop_after_defined_limit():
    doc = _doc("det_to", 20)

    def handler(user: str, n: int):
        raise AiBackendTimeoutError("timeout simulato")

    client = FakeClient(handler)
    with pytest.raises(AiBackendTimeoutError):
        _run(_service(client), doc, client)
    assert len(client.calls) == 3 * 2  # 3 microchunk x 2 tentativi, poi interruzione


def test_isolated_timeout_does_not_abort_and_never_certifies(store):
    doc = _doc("det_it", 40)

    def handler(user: str, n: int):
        if 20 in _ids(user, "det_it"):
            raise AiBackendTimeoutError("timeout isolato")
        return _resp("ABSENT")

    client = FakeClient(handler)
    res = _run(_service(client, store), doc, client)
    assert res.metadata["status"] == "PARTIAL"
    assert res.decision == TopicDecision.UNCERTAIN
    assert res.metadata["detection_diagnostics"]["timeouts"] == 1


# ----------------------------------------------------------------------------
# 7. PRESENT / ABSENT / UNCERTAIN
# ----------------------------------------------------------------------------

def test_uncertain_microchunk_prevents_global_absent():
    doc = _doc("det_u", 20)

    def handler(user: str, n: int):
        return _resp("UNCERTAIN", ["E001"]) if 9 in _ids(user, "det_u") else _resp("ABSENT")

    client = FakeClient(handler)
    res = _run(_service(client), doc, client)
    assert res.decision == TopicDecision.UNCERTAIN


def test_unverified_evidence_never_produces_present():
    doc = _doc("det_v", 20)
    client = FakeClient(lambda u, n: _resp("PRESENT", ["E999"]))  # alias inesistente nel blocco
    with pytest.raises(OperationalAnalysisFailedError):
        _run(_service(client), doc, client)

    def partial(user: str, n: int):
        return _resp("PRESENT", ["E999"]) if 9 in _ids(user, "det_v") else _resp("ABSENT")

    client2 = FakeClient(partial)
    res = _run(_service(client2), doc, client2)
    assert res.decision != TopicDecision.PRESENT and res.decision != TopicDecision.ABSENT
    assert res.evidence_ids == ()


# ----------------------------------------------------------------------------
# 8. Callback UI che fallisce: stato intatto, diagnostica sanificata
# ----------------------------------------------------------------------------

def test_failing_progress_callback_keeps_state_and_logs_sanitized(caplog):
    doc = _doc("det_cb", 20)
    client = FakeClient(lambda u, n: _resp("ABSENT"))

    def bad_cb(ev: AnalysisProgressEvent) -> None:
        raise RuntimeError("TESTO PRIVATO DELLA CHAT")

    with caplog.at_level("WARNING"):
        res = _run(_service(client), doc, client, progress_callback=bad_cb)
    assert res.decision == TopicDecision.ABSENT
    assert res.metadata["detection_diagnostics"]["callback_errors"] > 0
    assert "TESTO PRIVATO" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_tracker_counts_callback_errors_without_losing_state():
    def bad_cb(ev: AnalysisProgressEvent) -> None:
        raise ValueError("segreto")

    t = AnalysisProgressTracker(total_chunks=2, callback=bad_cb)
    t.begin_microchunks(1, 4)
    t.microchunk_done()
    t.microchunk_done(recovered=True)
    t.microchunk_bisected()
    t.microchunk_failed()
    assert (t.microchunks_total, t.microchunks_completed, t.microchunks_recovered, t.microchunks_failed, t.bisections_count) == (4, 2, 1, 1, 1)  # la bisezione NON crea unità
    assert t.callback_errors_count == 5 and t.last_callback_error == "ValueError"  # 5 eventi emessi
    ev = t.start_consolidation()
    # l'evento riporta gli errori avvenuti PRIMA della propria consegna; il tracker conta anche il proprio
    assert ev.callback_errors_count == 5 and ev.microchunks_total == 4
    assert t.callback_errors_count == 6
    assert "segreto" not in json.dumps(ev.to_dict())


def test_progress_percentage_moves_with_microchunks_before_any_parent_completes():
    seen: list[float] = []
    t = AnalysisProgressTracker(total_chunks=2, callback=lambda e: seen.append(e.percentage))
    t.begin_microchunks(1, 4)
    base = t.start_consolidation().percentage  # solo per leggere lo stato corrente
    t.microchunk_done()
    t.microchunk_done()
    assert t._calc_percentage() > 0.0, "con 0 parent completati, i microchunk devono già far avanzare la percentuale"
    assert t._calc_percentage() < 95.0
