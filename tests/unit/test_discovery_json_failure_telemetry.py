"""
tests/unit/test_discovery_json_failure_telemetry.py
---------------------------------------------------
Diagnosi del JSON_DECODE_ERROR su Topic Discovery (audit 02/11/2026) — SOLO dati sintetici, client LLM finto.

Esito massimo: MOCK. Il client è il VERO `LmStudioClient.chat_completion` con `urllib.request.urlopen` sostituito da una
risposta HTTP sintetica (stesso parsing di choices/finish_reason/usage/troncamento del percorso reale), ma nessuna
rete, nessun modello, nessuna chat reale. Questi test NON dimostrano nulla su Qwen/LM Studio/Windows né che il bug reale
dell'utente sia risolto: dimostrano (a) come il codice classifica e registra ciascun tipo di risposta sintetica,
(b) che la nuova telemetria è additiva, numerica e priva di contenuti.

Alcuni test sono di CARATTERIZZAZIONE: fissano il comportamento ATTUALE (anche quando è ambiguo, es. un troncamento per
`max_tokens` a metà stringa etichettato JSON_DECODE_ERROR) così che un cambiamento futuro sia deliberato.
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest

import ai.lmstudio as lms
import ai.topics as topics_mod
from ai.backend import AiInvalidEvidenceCitationError, AiStructuredOutputError
from ai.json_failure import (
    ATTEMPT_OUTCOMES,
    FINISH_REASON_KINDS,
    JSON_FAILURE_KINDS,
    describe_json_failure,
    finish_reason_from_kind,
    finish_reason_kind,
)
from ai.topics import TopicDetectionAnalyzer, TopicDiscoveryAnalyzer
from analysis.checkpoint import AnalysisCheckpointStore
from analysis.export import (
    ALLOWED_ATTEMPT_OUTCOMES,
    ALLOWED_FINISH_REASON_KINDS,
    ALLOWED_JSON_FAILURE_KINDS,
    build_topic_discovery_export_report,
    sanitize_diagnostic_metrics,
    serialize_report_to_json,
)
from analysis.models import classify_discovery_exception
from analysis.service import discovery_failure_metrics
from tests.unit.test_detection_microchunk_checkpoint_resume import (  # noqa: F401  (fixture autouse inclusa)
    MODEL,
    FakeClient,
    _doc,
    _no_sleep,
    _service,
)

SECRET = "ZXQSEGRETO"  # marcatore: testo di chat / risposta del modello che NON deve mai uscire dalla telemetria


# ============================================================================
# Infrastruttura: risposta HTTP sintetica consumata dal client LM Studio REALE
# ============================================================================

class _HttpResp:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_HttpResp":
        return self

    def __exit__(self, *a: Any) -> bool:
        return False


def _body(content: str, finish: str | None = "stop", usage: bool = True) -> bytes:
    choice: dict[str, Any] = {"index": 0, "message": {"role": "assistant", "content": content}}
    if finish is not None:
        choice["finish_reason"] = finish
    data: dict[str, Any] = {"model": MODEL, "choices": [choice]}
    if usage:
        data["usage"] = {"prompt_tokens": 800, "completion_tokens": 321, "total_tokens": 1121}
    return json.dumps(data).encode()


def _aliases(user: str) -> list[str]:
    return re.findall(r'"id": "(E\d{3,4})"', user)


def _user_of(req: Any) -> str:
    return json.loads(req.data.decode())["messages"][1]["content"]


def _good(user: str) -> str:
    return json.dumps({"topics": [{"label": "Tema", "short_description": "Descrizione", "evidence_ids": _aliases(user)[:1]}]})


MID_STRING = '{"topics": [{"label": "Tema", "short_description": "Descrizione ' + SECRET + ' molto lu'
AFTER_COMMA = '{"topics": [{"label": "Tema", '


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch):
    """Installa un responder: responder(call_index, user_text) -> bytes. Restituisce il registro delle richieste."""
    log: list[dict[str, Any]] = []

    def install(responder: Any) -> list[dict[str, Any]]:
        def fake_urlopen(req: Any, timeout: float | None = None) -> _HttpResp:
            payload = json.loads(req.data.decode())
            log.append({"max_tokens": payload.get("max_tokens"), "response_format": "response_format" in payload,
                        "stream": payload.get("stream"), "stop": payload.get("stop"), "user": _user_of(req)})
            return _HttpResp(responder(len(log) - 1, _user_of(req)))

        monkeypatch.setattr(lms.urllib.request, "urlopen", fake_urlopen)
        return log

    return install


def _discover(responder: Any, http: Any, bundles: int = 6, max_tokens: int = 640):
    log = http(responder)
    analyzer = TopicDiscoveryAnalyzer(client=lms.LmStudioClient(model_id=MODEL), default_model_id=MODEL, operational_mode=True)
    try:
        res = analyzer.discover_topics(
            document=_doc("probe", bundles), model_id=MODEL, max_topics=5, max_tokens=max_tokens,
            operational_mode=True, context_length=8192,
        )
        return res, None, log
    except Exception as exc:  # noqa: BLE001 - l'eccezione è l'oggetto del test
        return None, exc, log


# ============================================================================
# 1. ai/json_failure.py — enum chiusi, interi, nessun testo
# ============================================================================

@pytest.mark.parametrize("text, kind", [
    ("", "EMPTY_RESPONSE"),
    ("   \n", "EMPTY_RESPONSE"),
    ('{"a": "abc', "UNTERMINATED_STRING"),
    ('{"a": ', "EXPECTING_VALUE"),
    ('{"a": 1 "b": 2}', "EXPECTING_DELIMITER"),
    ('{"a": 1} testo extra', "EXTRA_DATA"),
    ('{"a": "x\\qy"}', "INVALID_ESCAPE"),
    ('{"a": "x\ny"}', "INVALID_CONTROL_CHAR"),
    ("Ecco il JSON: {}", "EXPECTING_VALUE"),
])
def test_json_failure_kinds_are_a_closed_enum_with_numeric_positions(text, kind):
    try:
        json.loads(text)
        err = None
    except json.JSONDecodeError as e:
        err = e
    info = describe_json_failure(text, err)
    assert info["json_failure_kind"] == kind and kind in JSON_FAILURE_KINDS
    assert info["response_chars"] == len(text)
    assert all(v is None or isinstance(v, (int, bool, str)) for v in info.values())
    assert set(ALLOWED_JSON_FAILURE_KINDS) == set(JSON_FAILURE_KINDS)


def test_json_failure_reports_position_not_text():
    text = '{"topics": [{"label": "' + SECRET + ' non chiusa'
    try:
        json.loads(text)
    except json.JSONDecodeError as e:
        info = describe_json_failure(text, e)
    assert info["json_error_offset"] is not None and info["json_error_line"] == 1 and info["json_error_col"] >= 1
    assert SECRET not in json.dumps(info)


def test_not_an_object_and_finish_reason_kinds():
    assert describe_json_failure("[1]", None, not_an_object=True)["json_failure_kind"] == "NOT_AN_OBJECT"
    assert [finish_reason_kind(v) for v in ("stop", "LENGTH", "tool_calls", None)] == ["STOP", "LENGTH", "OTHER", "MISSING"]
    assert set(FINISH_REASON_KINDS) == set(ALLOWED_FINISH_REASON_KINDS)
    assert set(ATTEMPT_OUTCOMES) == set(ALLOWED_ATTEMPT_OUTCOMES)
    assert [finish_reason_from_kind(k) for k in FINISH_REASON_KINDS] == ["stop", "length", "unknown", None]


# ============================================================================
# 2. Singola chiamata Discovery: ogni tipo di risposta sintetica (client LM Studio REALE, HTTP finto)
# ============================================================================

def test_valid_json_succeeds_without_usage_or_finish_reason(http):
    res, exc, log = _discover(lambda i, u: _body(_good(u), finish=None, usage=False), http)
    assert exc is None and len(res.topics) == 1 and len(log) == 1
    assert res.metadata["finish_reason"] is None  # assente → None, mai inventato


def test_request_parameters_sent_to_lm_studio_are_pinned(http):
    _, _, log = _discover(lambda i, u: _body(_good(u)), http)
    assert log[0]["response_format"] is True and log[0]["stream"] is None and log[0]["stop"] is None
    assert log[0]["max_tokens"] == 640


def test_markdown_fence_is_accepted_by_the_existing_strict_parser(http):
    res, exc, _ = _discover(lambda i, u: _body("```json\n" + _good(u) + "\n```"), http)
    assert exc is None and len(res.topics) == 1


@pytest.mark.parametrize("name, responder, category, kind, finish_kind", [
    # troncamento da max_tokens: l'ETICHETTA dipende da DOVE cade il taglio (caratterizzazione dell'ambiguità)
    ("length_mid_string", lambda i, u: _body(MID_STRING, "length"), "JSON_DECODE_ERROR", "UNTERMINATED_STRING", "LENGTH"),
    ("length_after_comma", lambda i, u: _body(AFTER_COMMA, "length"), "OUTPUT_TRUNCATED", "EXPECTING_DELIMITER", "LENGTH"),
    # stesso testo troncato ma finish_reason=stop: resta JSON_DECODE_ERROR
    ("stop_mid_string", lambda i, u: _body(MID_STRING, "stop"), "JSON_DECODE_ERROR", "UNTERMINATED_STRING", "STOP"),
    ("stop_after_comma", lambda i, u: _body(AFTER_COMMA, "stop"), "JSON_DECODE_ERROR", "EXPECTING_DELIMITER", "STOP"),
    ("extra_text_after_json", lambda i, u: _body(_good(u) + "\nSpero sia utile", "stop"), "JSON_DECODE_ERROR", "EXTRA_DATA", "STOP"),
    ("prefix_before_json", lambda i, u: _body("Ecco il JSON:\n" + _good(u), "stop"), "JSON_DECODE_ERROR", "EXPECTING_VALUE", "STOP"),
    ("invalid_escape", lambda i, u: _body('{"topics": [{"label": "Te\\qma", "short_description": "d", "evidence_ids": ["E001"]}]}'), "JSON_DECODE_ERROR", "INVALID_ESCAPE", "STOP"),
    ("empty_message", lambda i, u: _body(""), "JSON_DECODE_ERROR", "EMPTY_RESPONSE", "STOP"),
    ("valid_json_but_list", lambda i, u: _body("[" + _good(u) + "]"), "JSON_DECODE_ERROR", "NOT_AN_OBJECT", "STOP"),
])
def test_each_json_failure_type_keeps_its_label_and_gets_numeric_telemetry(http, name, responder, category, kind, finish_kind):
    res, exc, log = _discover(responder, http)
    assert res is None and isinstance(exc, AiStructuredOutputError) and not isinstance(exc, AiInvalidEvidenceCitationError)
    assert classify_discovery_exception(exc)[0] == category, name
    tel = exc.attempt_telemetry
    assert exc.llm_calls == len(log) == len(tel) == 2  # 2 chiamate HTTP effettive = 2 tentativi (nessuna bisezione qui)
    last = tel[-1]
    assert (last["json_failure_kind"], last["finish_reason_kind"]) == (kind, finish_kind), name
    assert last["response_received"] is True and last["completion_tokens"] == 321 and isinstance(last["response_chars"], int)
    # nessun contenuto del modello nella telemetria
    assert SECRET not in json.dumps(tel, default=str)


def test_length_cut_is_distinguishable_only_through_the_new_telemetry(http):
    """Prima: stessa etichetta JSON_DECODE_ERROR per taglio da max_tokens e per JSON malformato. Ora: finish_reason_kind."""
    _, trunc, _ = _discover(lambda i, u: _body(MID_STRING, "length"), http)
    _, malformed, _ = _discover(lambda i, u: _body(MID_STRING, "stop"), http)
    assert classify_discovery_exception(trunc)[0] == classify_discovery_exception(malformed)[0] == "JSON_DECODE_ERROR"
    assert trunc.attempt_telemetry[-1]["outcome"] == "TRUNCATED_LENGTH" and malformed.attempt_telemetry[-1]["outcome"] == "JSON_SYNTAX"
    assert trunc.attempt_telemetry[-1]["finish_reason_kind"] == "LENGTH" and malformed.attempt_telemetry[-1]["finish_reason_kind"] == "STOP"


def test_length_truncation_grows_max_tokens_on_the_second_attempt(http):
    _, exc, log = _discover(lambda i, u: _body(MID_STRING, "length"), http)
    assert [r["max_tokens"] for r in log] == [640, 960]
    assert [a["max_tokens_requested"] for a in exc.attempt_telemetry] == [640, 960]


def test_valid_json_with_invented_alias_is_invalid_evidence_not_json_error(http):
    res, exc, log = _discover(lambda i, u: _body(json.dumps({"topics": [{"label": "T", "short_description": "d", "evidence_ids": ["E990"]}]})), http)
    assert isinstance(exc, AiInvalidEvidenceCitationError) and classify_discovery_exception(exc)[0] == "INVALID_EVIDENCE_ID"
    assert [a["outcome"] for a in exc.attempt_telemetry] == ["INVALID_CITATION", "INVALID_CITATION"]
    assert all(a["json_failure_kind"] is None for a in exc.attempt_telemetry)


def test_degenerate_loop_is_still_degenerate_output(http):
    _, exc, log = _discover(lambda i, u: _body('{"topics": [{"label": "' + "ab" * 200, "stop"), http)
    assert classify_discovery_exception(exc)[0] == "DEGENERATE_OUTPUT"
    assert exc.attempt_telemetry[-1]["outcome"] == "DEGENERATE" and len(log) == 1


def test_first_failure_is_no_longer_lost_when_the_last_attempt_has_a_different_failure(http):
    """Tentativo 1: citazione inventata; tentativo 2: troncato. L'etichetta finale resta JSON_DECODE_ERROR (storico),
    ma il tentativo 1 ora è visibile nella telemetria."""
    def responder(i: int, u: str) -> bytes:
        if i == 0:
            return _body(json.dumps({"topics": [{"label": "T", "short_description": "d", "evidence_ids": ["E990"]}]}))
        return _body(MID_STRING, "length")
    _, exc, _ = _discover(responder, http)
    assert classify_discovery_exception(exc)[0] == "JSON_DECODE_ERROR"
    assert [a["outcome"] for a in exc.attempt_telemetry] == ["INVALID_CITATION", "TRUNCATED_LENGTH"]


def test_no_permissive_parser_was_added(http):
    """Testo extra e prefissi continuano a essere RIFIUTATI (nessuna riparazione euristica, nessun strip)."""
    for resp in (lambda i, u: _body(_good(u) + " extra"), lambda i, u: _body("prefisso " + _good(u))):
        res, exc, _ = _discover(resp, http)
        assert res is None and isinstance(exc, AiStructuredOutputError)


def test_second_attempt_can_still_recover(http):
    res, exc, log = _discover(lambda i, u: _body(MID_STRING, "length") if i == 0 else _body(_good(u)), http)
    assert exc is None and res.metadata["attempts"] == 2 and len(log) == 2


def test_detection_analyzer_is_untouched_by_the_telemetry(http):
    from ai.models import TopicQuery
    log = http(lambda i, u: _body("{"))
    an = TopicDetectionAnalyzer(client=lms.LmStudioClient(model_id=MODEL), default_model_id=MODEL, operational_mode=True)
    with pytest.raises(Exception) as ei:
        an.detect_topic(document=_doc("det", 4), topic=TopicQuery(topic_id="t", label="Sport", description="d"), model_id=MODEL, max_tokens=256, operational_mode=True)
    assert not hasattr(ei.value, "attempt_telemetry") and not hasattr(ei.value, "llm_calls")


# ============================================================================
# 3. Servizio Discovery: audio 19 msg / 3 microchunk, foto 36 msg / 2 parent
# ============================================================================

class _HttpMockClient(FakeClient):
    """FakeClient che delega al vero LmStudioClient.chat_completion (HTTP finto). Registra posizioni dei messaggi."""

    def __init__(self, responder: Any, doc_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__(handler=None)
        self.real = lms.LmStudioClient(model_id=MODEL)
        self.responder, self.doc_id, self.log = responder, doc_id, []
        monkeypatch.setattr(lms.urllib.request, "urlopen", self._urlopen)

    def _urlopen(self, req: Any, timeout: float | None = None) -> _HttpResp:
        user = _user_of(req)
        pos = sorted({int(m) for m in re.findall(rf"msg_{re.escape(self.doc_id)}_(\d+)", user)})
        self.log.append({"pos": pos, "max_tokens": json.loads(req.data.decode()).get("max_tokens")})
        return _HttpResp(self.responder(pos, user))

    def chat_completion(self, messages, model_id=None, schema=None, temperature=0.0, seed=42, timeout_seconds=60.0, max_tokens=None, **kw):  # type: ignore[override]
        return self.real.chat_completion(messages, model_id=model_id, schema=schema, temperature=temperature, seed=seed,
                                         max_tokens=max_tokens, timeout_seconds=timeout_seconds, **kw)


def _run_service(doc: Any, responder: Any, monkeypatch: pytest.MonkeyPatch, store: Any = None, **svc_kw: Any):
    client = _HttpMockClient(responder, doc.document_id, monkeypatch)
    svc = _service(client, store, **svc_kw)
    events: list[Any] = []
    res = svc.run_topic_discovery(document=doc, client=client, model_name=MODEL, progress_callback=events.append)
    return res, client, events


def _failed(res: Any) -> list[dict[str, Any]]:
    return [dict(d) for d in res.metadata["chunk_diagnostics"] if d["status"] == "FAILED"]


def test_audio_19_messages_two_microchunks_ok_one_json_failure(monkeypatch):
    doc = _doc("aud", 19)
    res, client, _ = _run_service(doc, lambda pos, u: _body(MID_STRING, "length") if pos and max(pos) >= 16 else _body(_good(u)), monkeypatch)
    m = res.metadata
    assert (m["status"], m["completed_chunk_count"], m["failed_chunk_count"], m["chunk_count"]) == ("PARTIAL", 2, 1, 3)
    assert (m["total_evidence_count"], m["analyzed_evidence_count"]) == (19, 16)
    (f,) = _failed(res)
    assert f["diagnostic_category"] == "JSON_DECODE_ERROR" and f["failure_phase"] == "json_parsing" and f["attempts"] == 2
    # telemetria nuova: prima http_status/finish_reason/token erano None
    assert (f["http_status"], f["finish_reason"], f["completion_tokens"]) == (200, "length", 642)
    dm = f["diagnostic_metrics"]
    calls_for_failed = [c for c in client.log if c["pos"] and max(c["pos"]) >= 16]
    assert dm["telemetry_provenance"] == "MEASURED"
    assert dm["llm_calls_own"] == 2 and dm["llm_calls_total"] == len(calls_for_failed) == 4 and dm["bisection_depth_max"] == 1
    assert f["attempts"] == dm["llm_calls_own"] != dm["llm_calls_total"], "attempts NON conta le chiamate di bisezione"
    assert [a["finish_reason_kind"] for a in dm["attempts_detail"]] == ["LENGTH", "LENGTH"]
    # nessuno SUCCESS falso: i 2 microchunk riusciti non coprono il terzo
    assert [d["status"] for d in m["chunk_diagnostics"]] == ["SUCCESS", "SUCCESS", "FAILED"]


def test_audio_successful_half_of_a_failed_bisection_is_validated_but_not_credited(monkeypatch):
    """Caratterizzazione della semantica di copertura: metà riuscite dentro un microchunk fallito NON entrano in
    analyzed_evidence_count; la telemetria ora dichiara quante evidenze erano state validate ma scartate."""
    doc = _doc("aud", 19)
    res, _, _ = _run_service(doc, lambda pos, u: _body(MID_STRING, "length") if 18 in pos else _body(_good(u)), monkeypatch)
    m = res.metadata
    assert m["analyzed_evidence_count"] == 16 and m["total_evidence_count"] == 19 and m["status"] == "PARTIAL"
    (f,) = _failed(res)
    assert f["diagnostic_metrics"]["discarded_success_evidence_count"] == 2  # messaggi 16 e 17 validati ma non accreditati
    assert f["diagnostic_metrics"]["llm_calls_total"] == 8


def test_photo_36_messages_two_parents_export_hides_microchunk_records_and_now_says_so(monkeypatch):
    doc = _doc("pho", 36)
    res, client, _ = _run_service(doc, lambda pos, u: _body(MID_STRING, "length") if pos and min(pos) <= 7 else _body(_good(u)), monkeypatch, max_chunk_bundles=18)
    m = res.metadata
    assert (m["status"], m["expected_chunk_count"], m["completed_chunk_count"], m["failed_chunk_count"]) == ("PARTIAL", 2, 1, 1)
    assert m["microchunk_count"] == 6 and len(m["chunk_diagnostics"]) == 6
    # più chiamate HTTP dei "chunk" visibili: 13 richieste per 2 slot esportati
    assert len(client.log) == 13
    report = json.loads(serialize_report_to_json(build_topic_discovery_export_report(res, doc)))
    assert report["diagnostics_export"] == {"records_recorded": 6, "records_exported": 2, "records_not_exported": 4}
    shown = []
    def walk(o: Any) -> None:
        if isinstance(o, dict):
            if "chunk_index" in o and "diagnostic_category" in o:
                shown.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(report)
    uniq = {(c["chunk_index"], c["status"], c["diagnostic_category"]) for c in shown}
    assert uniq == {(0, "FAILED", "JSON_DECODE_ERROR"), (1, "SUCCESS", "NONE")}
    failed = next(c for c in shown if c["status"] == "FAILED")
    assert failed["attempts"] == 2 and failed["finish_reason"] == "length" and failed["http_status"] == 200
    assert failed["diagnostic_metrics"]["llm_calls_total"] == 8


def test_new_telemetry_never_changes_status_coverage_or_categories(monkeypatch):
    """Confronto con il comportamento senza telemetria: gli esiti scientifici sono funzione della sola risposta."""
    doc = _doc("aud", 19)
    res, _, _ = _run_service(doc, lambda pos, u: _body(_good(u)), monkeypatch)
    assert res.metadata["status"] == "COMPLETE" and res.metadata["analyzed_evidence_count"] == 19
    assert all(d["diagnostic_category"] == "NONE" for d in res.metadata["chunk_diagnostics"])


def test_checkpoint_identity_and_resume_are_unaffected(monkeypatch, tmp_path):
    doc = _doc("aud", 19)
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")
    first, _, _ = _run_service(doc, lambda pos, u: _body(MID_STRING, "length") if pos and max(pos) >= 16 else _body(_good(u)), monkeypatch, store)
    assert first.metadata["status"] == "PARTIAL"
    second, client2, _ = _run_service(doc, lambda pos, u: _body(_good(u)), monkeypatch, store)
    assert second.metadata["status"] == "COMPLETE" and second.metadata["analyzed_evidence_count"] == 19
    statuses = [d["status"] for d in second.metadata["chunk_diagnostics"]]
    assert statuses.count("RECOVERED_FROM_CHECKPOINT") == 2 and statuses.count("SUCCESS") == 1
    assert all(c["pos"] and min(c["pos"]) >= 16 for c in client2.log), "solo il microchunk mancante viene ricalcolato"


# ============================================================================
# 4. Privacy e allowlist dell'export
# ============================================================================

def test_export_logs_and_telemetry_contain_no_model_text(monkeypatch, caplog):
    doc = _doc("aud", 19)
    with caplog.at_level("DEBUG"):
        res, _, events = _run_service(doc, lambda pos, u: _body(MID_STRING, "length") if pos and max(pos) >= 16 else _body(_good(u)), monkeypatch)
    text = serialize_report_to_json(build_topic_discovery_export_report(res, doc))
    text += json.dumps([dict(d) for d in res.metadata["chunk_diagnostics"]], default=str)
    text += "\n".join(f"{e.status_message} {dict(e.metadata)}" for e in events) + caplog.text
    assert SECRET not in text


def test_sanitizer_drops_unknown_keys_and_maps_unknown_enums():
    hostile = {
        "telemetry_provenance": f"{SECRET}-x", "llm_calls_total": 7, f"{SECRET}_key": SECRET,
        "attempts_detail": [{
            "attempt": 1, "outcome": f"{SECRET}-outcome", "finish_reason_kind": f"{SECRET}-fr", "json_failure_kind": f"{SECRET}-k",
            "response_chars": f"{SECRET}", "completion_tokens": -5, "json_error_offset": "12", "json_error_at_end": "si",
            "extra_text": SECRET,
        }],
    }
    clean = sanitize_diagnostic_metrics(hostile)
    dumped = json.dumps(clean)
    assert SECRET not in dumped
    assert clean["telemetry_provenance"] == "NOT_AVAILABLE" and clean["llm_calls_total"] == 7
    (a,) = clean["attempts_detail"]
    assert (a["outcome"], a["finish_reason_kind"], a["json_failure_kind"]) == ("OTHER", "OTHER", "OTHER_SYNTAX")
    assert a["json_error_at_end"] is None and "extra_text" not in a


def test_missing_telemetry_is_reported_as_unavailable_never_as_zero():
    m = discovery_failure_metrics(RuntimeError("x"))
    assert m["telemetry_provenance"] == "NOT_AVAILABLE"
    assert m["llm_calls_own"] is None and m["llm_calls_total"] is None and m["attempts_detail"] == []
    assert discovery_failure_metrics(None)["llm_calls_total"] is None


def test_historical_export_without_policy_has_no_new_block(monkeypatch):
    from ai.models import TopicDiscoveryResult
    doc = _doc("aud", 19)
    res, _, _ = _run_service(doc, lambda pos, u: _body(_good(u)), monkeypatch)
    meta = dict(res.metadata)
    meta.pop("acceptance_policy")
    legacy = TopicDiscoveryResult(topics=res.topics, provenance_document_id=res.provenance_document_id, metadata=meta)
    report = build_topic_discovery_export_report(legacy, doc)
    assert "diagnostics_export" not in report and "analysis_policy" not in report
