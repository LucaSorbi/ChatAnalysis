"""
tests/unit/test_discovery_micro_budget_and_validation_kind.py
--------------------------------------------------------------
Correzione mirata Topic Discovery dopo la telemetria della PR #7 (02/11/2026) — SOLO dati sintetici, client LLM finto.

Esito massimo: MOCK. Il client è il VERO `LmStudioClient.chat_completion` con `urllib.request.urlopen` sostituito da una
risposta HTTP sintetica. Nessuna rete, nessun modello, nessuna chat reale: questi test NON dimostrano che la run
LIVE dell'utente sia risolta; dimostrano (a) quali budget d'uscita il codice invia davvero, (b) che il budget dei
microchunk è coerente con la formula del codice stesso e con il `max_tokens` dell'utente, (c) che l'errore
`OTHER_STRUCTURED` è ora scomponibile in un enum chiuso privo di contenuti, (d) che l'identità del checkpoint include la
versione della policy di budget (nessun riuso silenzioso di checkpoint creati con i vecchi tetti 640/448).
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest

import ai.lmstudio as lms
import analysis.service as service_mod
from ai.backend import AiInvalidEvidenceCitationError, AiStructuredOutputError, AnalysisInputTooLargeError
from ai.json_failure import VALIDATION_KINDS
from ai.structured import TOPIC_DISCOVERY_SCHEMA
from ai.token_budget import DEFAULT_SAFETY_MARGIN_TOKENS, estimate_request_input_tokens
from ai.topics import TopicDiscoveryAnalyzer
from analysis.checkpoint import AnalysisCheckpointStore
from analysis.export import (
    ALLOWED_VALIDATION_KINDS,
    build_topic_discovery_export_report,
    sanitize_attempt_log_row,
    serialize_report_to_json,
)
from analysis.service import calculate_micro_output_budget
from ai.models import ConversationEvidenceDocument, MessageEvidenceBundle
from multimodal.models import ImageOcrResult, MediaKind, MediaResolutionStatus, OcrStatus, ResolvedMediaAsset
from ui.demo import _create_synthetic_message
from unified.models import CanonicalMessageType
from tests.unit.test_detection_microchunk_checkpoint_resume import (  # noqa: F401  (fixture autouse inclusa)
    MODEL,
    FakeClient,
    _doc,
    _no_sleep,
    _service,
)
from tests.unit.test_discovery_json_failure_telemetry import (  # noqa: F401  (fixture `http` inclusa)
    AFTER_COMMA,
    MID_STRING,
    SECRET,
    _HttpResp,
    _aliases,
    _good,
    _user_of,
    http,
)

UI_MAX_TOKENS = 1456  # valore di configurazione UI del run reale (max_topics=10)
UI_MAX_TOPICS = 10


def _formula(max_topics: int) -> int:
    """Formula del codice stesso (analysis.service.calculate_discovery_max_tokens): topic*120 + 256."""
    return max_topics * 120 + 256


def _stop_body(content: str, completion: int = 123) -> bytes:
    return json.dumps({
        "model": MODEL,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 800, "completion_tokens": completion, "total_tokens": 800 + completion},
    }).encode()


def _length_body(content: str, completion: int) -> bytes:
    """Risposta troncata dal server per esaurimento del budget: completion_tokens == max_tokens richiesto."""
    return json.dumps({
        "model": MODEL,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 800, "completion_tokens": completion, "total_tokens": 800 + completion},
    }).encode()


def _topics(user: str, n: int, per_topic_ids: int = 1) -> str:
    al = _aliases(user)
    return json.dumps({"topics": [
        {"label": f"Tema {i}", "short_description": f"Descrizione {i}", "evidence_ids": al[i:i + per_topic_ids] or al[:1]}
        for i in range(n)
    ]})


# ============================================================================
# Infrastruttura service-level: client reale su HTTP finto, con registro completo di ogni richiesta
# ============================================================================

class _Mock(FakeClient):
    def __init__(self, responder: Any, doc_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__(handler=None)
        self.real = lms.LmStudioClient(model_id=MODEL)
        self.responder, self.doc_id, self.log = responder, doc_id, []
        monkeypatch.setattr(lms.urllib.request, "urlopen", self._urlopen)

    def _urlopen(self, req: Any, timeout: float | None = None) -> _HttpResp:
        payload = json.loads(req.data.decode())
        msgs = payload["messages"]
        user = msgs[1]["content"]
        pos = sorted({int(m) for m in re.findall(rf"msg_{re.escape(self.doc_id)}_(\d+)", user)})
        m = re.search(r"at most (\d+) elements", msgs[0]["content"])
        self.log.append({
            "pos": pos,
            "max_tokens": payload.get("max_tokens"),
            "max_topics": int(m.group(1)) if m else None,
            "messages": msgs,
            "n_aliases": len(set(_aliases(user))),
        })
        return _HttpResp(self.responder(self.log[-1], user))

    def chat_completion(self, messages, model_id=None, schema=None, temperature=0.0, seed=42, timeout_seconds=60.0, max_tokens=None, **kw):  # type: ignore[override]
        return self.real.chat_completion(messages, model_id=model_id, schema=schema, temperature=temperature, seed=seed,
                                         max_tokens=max_tokens, timeout_seconds=timeout_seconds, **kw)


def _run(doc: Any, responder: Any, monkeypatch: pytest.MonkeyPatch, store: Any = None, max_topics: int = UI_MAX_TOPICS,
         max_tokens: int = UI_MAX_TOKENS, **svc_kw: Any):
    client = _Mock(responder, doc.document_id, monkeypatch)
    svc = _service(client, store, **svc_kw)
    res = svc.run_topic_discovery(document=doc, client=client, model_name=MODEL, max_topics=max_topics, max_tokens=max_tokens)
    return res, client


def _ok(call: dict[str, Any], user: str) -> bytes:
    return _stop_body(_topics(user, 1))


def _doc_36_with_ocr() -> ConversationEvidenceDocument:
    """36 messaggi / 37 evidenze: 35 messaggi testuali + 1 messaggio-foto con testo e sezione OCR sintetica."""
    base = _doc("pho", 36)
    msg_id = "msg_pho_17"
    msg = _create_synthetic_message(
        msg_id=msg_id, source_record_id="17", text_content="Invio foto allegata", source_name="whatsapp_test",
        message_type=CanonicalMessageType.IMAGE, media_reference="test.jpg",
    )
    asset = ResolvedMediaAsset(
        message_id=msg_id, source_name=msg.source_name, source_record_id="17", raw_reference="test.jpg",
        resolved_path="/path/test.jpg", media_kind=MediaKind.IMAGE, status=MediaResolutionStatus.RESOLVED,
        file_size_bytes=1000, sha256="91a82c478104d9b8e72c8194a0293847e6182903847bca8291048b29c8192039",
        provenance_message=msg,
    )
    ocr = ImageOcrResult(
        message_id=msg_id, source_name=msg.source_name, source_record_id="17", status=OcrStatus.SUCCESS,
        full_text="TESTO OCR SINTETICO " + SECRET, engine="tesseract", provenance_asset=asset,
    )
    bundles = list(base.bundles)
    bundles[17] = MessageEvidenceBundle(message=msg, image_ocr=ocr)
    return ConversationEvidenceDocument(document_id="pho", bundles=tuple(bundles), source_name="whatsapp_test")


# ============================================================================
# 1. BUDGET D'USCITA DEI MICROCHUNK
# ============================================================================

def test_ui_values_reproduce_the_observed_request_budgets_and_formula_gap_is_explicit(monkeypatch):
    """
    Riproduce la forma della run reale (UI max_tokens=1456, max_topics=10, 36 msg): ogni nodo riceve un `max_topics`
    nel prompt (5/4/2) e un `max_tokens` che DEVE essere coerente con la formula del codice (topic*120+256) e mai
    superare il tetto scelto dall'utente. Prima della correzione: 640 a depth 0 e 448 sulle bisezioni (< formula).
    """
    doc = _doc("pho", 36)
    res, client = _run(doc, _ok, monkeypatch)
    assert res.metadata["status"] == "COMPLETE"
    assert [len(c["pos"]) for c in client.log] == [8, 8, 8, 8, 4]
    for c in client.log:
        assert c["max_tokens"] == min(UI_MAX_TOKENS, _formula(c["max_topics"])), c["max_topics"]


def test_bisected_nodes_get_budget_from_their_own_topic_limit_and_retry_growth_follows(monkeypatch):
    """Primo microchunk sempre troncato: la catena di bisezione usa la formula del NODO, il retry cresce ×1,5."""
    doc = _doc("pho", 36)

    def resp(call, user):
        if call["pos"] and min(call["pos"]) <= 7:
            return _length_body(MID_STRING, call["max_tokens"])
        return _ok(call, user)

    res, client = _run(doc, resp, monkeypatch)
    assert res.metadata["status"] == "PARTIAL"
    chain = [c for c in client.log if c["pos"] and min(c["pos"]) <= 7]
    assert len(chain) == 8 and [len(c["pos"]) for c in chain] == [8, 8, 4, 4, 2, 2, 1, 1]
    for first, retry in zip(chain[0::2], chain[1::2]):
        base = min(UI_MAX_TOKENS, _formula(first["max_topics"]))
        assert first["max_tokens"] == base
        assert retry["max_tokens"] == int(base * 1.5), "retry per troncamento: ×1,5 quando c'è spazio nel contesto"
    for c in chain:
        assert c["max_tokens"] <= UI_MAX_TOKENS * 2  # nessun aumento massivo


def test_user_max_tokens_remains_a_hard_ceiling(monkeypatch):
    doc = _doc("pho", 36)
    res, client = _run(doc, _ok, monkeypatch, max_tokens=700)
    assert res.metadata["status"] == "COMPLETE"
    for c in client.log:
        assert c["max_tokens"] == min(700, _formula(c["max_topics"]))
        assert c["max_tokens"] <= 700


@pytest.mark.parametrize("ctx", [4096, 6144, 8192])
def test_every_request_fits_the_context_window_for_several_window_sizes(monkeypatch, ctx):
    """Qualunque finestra accettata dal piano: input stimato + max_tokens + margine <= contesto, per OGNI richiesta."""
    doc = _doc("pho", 36)

    def resp(call, user):  # primo microchunk sempre troncato → anche i retry ×1,5 e le bisezioni sono verificati
        if call["pos"] and min(call["pos"]) <= 7:
            return _length_body(MID_STRING, call["max_tokens"])
        return _ok(call, user)

    res, client = _run(doc, resp, monkeypatch, context_length=ctx)
    assert client.log
    for c in client.log:
        est = estimate_request_input_tokens(c["messages"], schema=TOPIC_DISCOVERY_SCHEMA)
        assert est + c["max_tokens"] + DEFAULT_SAFETY_MARGIN_TOKENS <= ctx, (ctx, est, c["max_tokens"])
        assert c["max_tokens"] <= UI_MAX_TOKENS * 2


@pytest.mark.parametrize("ctx", [900, 1700])
def test_window_too_small_for_the_plan_fails_explicitly_before_any_request(monkeypatch, ctx):
    """Contesto senza spazio per prompt+uscita: errore esplicito PRIMA di qualunque richiesta, nessun risultato parziale."""
    doc = _doc("pho", 36)
    client = _Mock(_ok, doc.document_id, monkeypatch)
    svc = _service(client, None, context_length=ctx)
    with pytest.raises(AnalysisInputTooLargeError):
        svc.run_topic_discovery(document=doc, client=client, model_name=MODEL, max_topics=UI_MAX_TOPICS, max_tokens=UI_MAX_TOKENS)
    assert client.log == []


def _capture_messages(monkeypatch: pytest.MonkeyPatch, content_for: Any) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def fake(req: Any, timeout: float | None = None) -> _HttpResp:
        payload = json.loads(req.data.decode())
        seen.append({"max_tokens": payload.get("max_tokens"), "messages": payload["messages"]})
        return _HttpResp(content_for(payload["messages"][1]["content"]))

    monkeypatch.setattr(lms.urllib.request, "urlopen", fake)
    return seen


def test_analyzer_clamps_output_to_real_headroom_or_fails_explicitly(monkeypatch):
    """discover_topics: spazio >=128 → uscita ridotta all'headroom; spazio <128 → AnalysisInputTooLargeError (no richiesta)."""
    doc = _doc("t", 8)
    seen = _capture_messages(monkeypatch, lambda u: _stop_body(_topics(u, 1)))
    an = TopicDiscoveryAnalyzer(client=lms.LmStudioClient(model_id=MODEL), default_model_id=MODEL, operational_mode=True)
    an.discover_topics(document=doc, model_id=MODEL, max_topics=5, max_tokens=856, operational_mode=True, context_length=8192)
    est = estimate_request_input_tokens(seen[0]["messages"], schema=TOPIC_DISCOVERY_SCHEMA)
    assert seen[0]["max_tokens"] == 856

    # headroom = 300 (>=128, < 856): richiesta emessa con max_tokens ridotto a 300
    ctx_clamp = est + DEFAULT_SAFETY_MARGIN_TOKENS + 300
    an.discover_topics(document=doc, model_id=MODEL, max_topics=5, max_tokens=856, operational_mode=True, context_length=ctx_clamp)
    assert seen[1]["max_tokens"] == 300 and est + seen[1]["max_tokens"] + DEFAULT_SAFETY_MARGIN_TOKENS <= ctx_clamp

    # headroom = 100 (<128): nessuna richiesta, errore esplicito
    n_before = len(seen)
    with pytest.raises(AnalysisInputTooLargeError):
        an.discover_topics(document=doc, model_id=MODEL, max_topics=5, max_tokens=856, operational_mode=True,
                           context_length=est + DEFAULT_SAFETY_MARGIN_TOKENS + 100)
    assert len(seen) == n_before


def test_synthetic_ocr_section_36_messages_37_evidences_budget_and_alias_protocol(monkeypatch):
    """Documento sintetico con sezione OCR: 37 evidenze, alias deterministici, budget coerente, citazione OCR accettata."""
    doc = _doc_36_with_ocr()
    assert len(doc.bundles) == 36 and len(doc.all_evidence_sections) == 37
    cited: list[str] = []

    def resp(call, user):
        al = _aliases(user)
        cited.extend(al)
        return _stop_body(json.dumps({"topics": [{"label": "Tema", "short_description": "Descrizione", "evidence_ids": al}]}))

    res, client = _run(doc, resp, monkeypatch)
    m = res.metadata
    assert (m["status"], m["total_evidence_count"], m["analyzed_evidence_count"]) == ("COMPLETE", 37, 37)
    for c in client.log:
        assert c["max_tokens"] == min(UI_MAX_TOKENS, _formula(c["max_topics"]))
    assert len(client.log[0]["messages"][1]["content"]) > 0
    assert SECRET not in serialize_report_to_json(build_topic_discovery_export_report(res, doc))


def test_micro_budget_helper_values():
    """topic*120+256 (come nei retry/dimezzamenti di topics.py), limitato dal max_tokens dell'utente."""
    assert [calculate_micro_output_budget(t, UI_MAX_TOKENS) for t in (2, 3, 4, 5)] == [496, 616, 736, 856]
    assert calculate_micro_output_budget(5, 700) == 700 and calculate_micro_output_budget(2, 300) == 300
    assert calculate_micro_output_budget(5, None) == 856 and calculate_micro_output_budget(5, 0) == 856


# ============================================================================
# 2. TRONCAMENTO: caratterizzazione a 640 / 448 / 960 (comportamento del retry, invariato)
# ============================================================================

@pytest.mark.parametrize("budget, grown", [(640, 960), (448, 672), (960, 1440)])
def test_length_with_open_string_is_rejected_and_retry_grows_by_half(http, budget, grown):
    def resp(i, user):
        return _length_body(MID_STRING, budget if i == 0 else grown)

    log = http(resp)
    analyzer = TopicDiscoveryAnalyzer(client=lms.LmStudioClient(model_id=MODEL), default_model_id=MODEL, operational_mode=True)
    with pytest.raises(AiStructuredOutputError) as ei:
        analyzer.discover_topics(document=_doc("t", 6), model_id=MODEL, max_topics=5, max_tokens=budget,
                                 operational_mode=True, context_length=8192)
    assert [r["max_tokens"] for r in log] == [budget, grown]
    tel = ei.value.attempt_telemetry
    assert [(t["outcome"], t["finish_reason_kind"], t["json_failure_kind"]) for t in tel] == [
        ("TRUNCATED_LENGTH", "LENGTH", "UNTERMINATED_STRING")] * 2
    assert [t["completion_tokens"] for t in tel] == [budget, grown]
    assert SECRET not in json.dumps(tel)


# ============================================================================
# 3. OTHER_STRUCTURED: enum chiuso (nessun testo) — cause distinte basate sul codice
# ============================================================================

def _payload_cases(al: list[str]) -> list[tuple[str, Any, str]]:
    a0 = al[0]
    one = {"label": "Tema " + SECRET, "short_description": "Descrizione " + SECRET, "evidence_ids": [a0]}
    return [
        ("TOP_KEYS_MISMATCH", {"topics": [], "extra": 1}, "TOP_KEYS_MISMATCH"),
        ("TOPICS_NOT_LIST", {"topics": {"a": 1}}, "TOPICS_NOT_LIST"),
        ("COUNT_EXCEEDS", {"topics": [one, one, one]}, "TOPIC_COUNT_EXCEEDS_LIMIT"),
        ("TOPIC_NOT_OBJECT", {"topics": ["x"]}, "TOPIC_NOT_OBJECT"),
        ("TOPIC_KEYS", {"topics": [{"label": "Tema", "evidence_ids": [a0]}]}, "TOPIC_KEYS_MISMATCH"),
        ("LABEL", {"topics": [{**one, "label": "  "}]}, "LABEL_INVALID"),
        ("DESC", {"topics": [{**one, "short_description": ""}]}, "DESCRIPTION_INVALID"),
        ("EIDS_NOT_LIST", {"topics": [{**one, "evidence_ids": a0}]}, "EVIDENCE_IDS_NOT_LIST"),
        ("EIDS_DUP", {"topics": [{**one, "evidence_ids": ["a::b", "a::b"]}]}, "EVIDENCE_IDS_DUPLICATED"),
        ("EIDS_EMPTY", {"topics": [{**one, "evidence_ids": []}]}, "EVIDENCE_IDS_EMPTY"),
    ]


@pytest.mark.parametrize("idx", range(10))
def test_other_structured_is_split_into_closed_enum_kinds(http, idx):
    doc = _doc("t", 6)
    al_cache: dict[str, list[str]] = {}

    def resp(i, user):
        al_cache["a"] = _aliases(user)
        _, payload, _ = _payload_cases(al_cache["a"])[idx]
        return _stop_body(json.dumps(payload))

    log = http(resp)
    analyzer = TopicDiscoveryAnalyzer(client=lms.LmStudioClient(model_id=MODEL), default_model_id=MODEL, operational_mode=True)
    with pytest.raises(AiStructuredOutputError) as ei:
        analyzer.discover_topics(document=doc, model_id=MODEL, max_topics=2, max_tokens=640, operational_mode=True,
                                 context_length=8192)
    expected = _payload_cases(al_cache["a"])[idx][2]
    tel = ei.value.attempt_telemetry
    assert len(tel) == 2 and len(log) == 2
    for t in tel:
        assert t["outcome"] == "OTHER_STRUCTURED" and t["finish_reason_kind"] == "STOP"
        assert t["validation_kind"] == expected and expected in VALIDATION_KINDS
        assert t["limit_requested"] == 2
        assert t["returned_count"] == (3 if expected == "TOPIC_COUNT_EXCEEDS_LIMIT" else None)
    assert SECRET not in json.dumps(tel)  # nessun testo del modello nella telemetria


def test_at_least_two_distinct_structured_causes_stay_distinguishable_in_the_export(monkeypatch):
    """Due nodi con cause diverse (troppi topic / label vuota) → due `validation_kind` diversi nell'attempt_log SAFE."""
    doc = _doc("pho", 19)

    def resp(call, user):
        al = _aliases(user)
        one = {"label": "Tema " + SECRET, "short_description": "D " + SECRET, "evidence_ids": al[:1]}
        if call["pos"] and min(call["pos"]) == 0:
            return _stop_body(json.dumps({"topics": [one] * (call["max_topics"] + 1)}))
        if call["pos"] and min(call["pos"]) == 8:
            return _stop_body(json.dumps({"topics": [{**one, "label": " "}]}))
        return _stop_body(json.dumps({"topics": [one]}))

    res, client = _run(doc, resp, monkeypatch)
    txt = serialize_report_to_json(build_topic_discovery_export_report(res, doc))
    assert SECRET not in txt
    rows = json.loads(txt)["diagnostics_export"]["attempt_log"]
    kinds = {r["validation_kind"] for r in rows if r["outcome"] == "OTHER_STRUCTURED"}
    assert {"TOPIC_COUNT_EXCEEDS_LIMIT", "LABEL_INVALID"} <= kinds
    for r in rows:
        assert r["validation_kind"] is None or r["validation_kind"] in ALLOWED_VALIDATION_KINDS
        if r["outcome"] != "OTHER_STRUCTURED":
            assert r["validation_kind"] is None
    assert all("limit_requested" in r and "returned_count" in r for r in rows)


def test_export_sanitizer_never_lets_free_text_through_validation_fields():
    row = sanitize_attempt_log_row({"seq": 1, "outcome": "OTHER_STRUCTURED", "validation_kind": "x " + SECRET,
                                    "limit_requested": SECRET, "returned_count": -4})
    assert row is not None and SECRET not in json.dumps(row)
    assert row["validation_kind"] == "OTHER_VALIDATION"
    assert row["limit_requested"] is None and row["returned_count"] is None
    assert sanitize_attempt_log_row({"seq": 1, "validation_kind": None})["validation_kind"] is None


# ============================================================================
# 4. Casi che NON sono OTHER_STRUCTURED / che devono restare rifiutati (invarianti di correttezza forense)
# ============================================================================

def test_stop_with_invented_alias_stays_rejected_and_is_not_other_structured(http):
    def resp(i, user):
        return _stop_body(json.dumps({"topics": [{"label": "Tema", "short_description": "D", "evidence_ids": ["E999"]}]}))

    http(resp)
    analyzer = TopicDiscoveryAnalyzer(client=lms.LmStudioClient(model_id=MODEL), default_model_id=MODEL, operational_mode=True)
    with pytest.raises(AiInvalidEvidenceCitationError) as ei:
        analyzer.discover_topics(document=_doc("t", 6), model_id=MODEL, max_topics=5, max_tokens=640, operational_mode=True,
                                 context_length=8192)
    tel = ei.value.attempt_telemetry
    assert [t["outcome"] for t in tel] == ["INVALID_CITATION", "INVALID_CITATION"]
    assert all(t["validation_kind"] is None for t in tel)


def test_stop_with_valid_json_and_correct_aliases_is_accepted_with_real_ids(http):
    doc = _doc("t", 6)
    http(lambda i, user: _stop_body(_topics(user, 2, per_topic_ids=2)))
    analyzer = TopicDiscoveryAnalyzer(client=lms.LmStudioClient(model_id=MODEL), default_model_id=MODEL, operational_mode=True)
    res = analyzer.discover_topics(document=doc, model_id=MODEL, max_topics=5, max_tokens=640, operational_mode=True,
                                   context_length=8192)
    valid = {s.evidence_id for s in doc.all_evidence_sections}
    assert len(res.topics) == 2 and all(set(t.evidence_ids) <= valid for t in res.topics)
    assert [t["outcome"] for t in analyzer.last_attempt_telemetry] == ["OK"]
    assert analyzer.last_attempt_telemetry[0]["validation_kind"] is None
    assert analyzer.last_attempt_telemetry[0]["limit_requested"] == 5


def test_lawful_empty_topics_and_dummy_records_are_not_structured_errors(http):
    state = {"n": 0}

    def resp(i, user):
        state["n"] += 1
        return _stop_body(json.dumps({"topics": []}))

    http(resp)
    analyzer = TopicDiscoveryAnalyzer(client=lms.LmStudioClient(model_id=MODEL), default_model_id=MODEL, operational_mode=True)
    res = analyzer.discover_topics(document=_doc("t", 6), model_id=MODEL, max_topics=5, max_tokens=640, operational_mode=True,
                                   context_length=8192)
    assert res.topics == ()
    assert [t["outcome"] for t in analyzer.last_attempt_telemetry] == ["EMPTY_TOPICS", "OK"]
    assert all(t["validation_kind"] is None for t in analyzer.last_attempt_telemetry)


# ============================================================================
# 5. IDENTITÀ / CHECKPOINT: la policy di budget fa parte del fingerprint e dell'export
# ============================================================================

def test_budget_policy_is_part_of_run_identity_and_old_checkpoints_are_not_reused(monkeypatch, tmp_path):
    doc = _doc("pho", 19)
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")
    seen_extra: list[dict[str, Any]] = []
    real_hash = service_mod.compute_run_config_hash

    def spy(*a: Any, **k: Any) -> str:
        seen_extra.append(dict(k.get("extra_params") or {}))
        return real_hash(*a, **k)

    monkeypatch.setattr(service_mod, "compute_run_config_hash", spy)
    res1, c1 = _run(doc, _ok, monkeypatch, store=store)
    assert res1.metadata["status"] == "COMPLETE" and len(c1.log) == 3
    assert seen_extra[-1]["micro_output_budget_policy"] == service_mod.MICRO_OUTPUT_BUDGET_POLICY
    assert seen_extra[-1]["acceptance_policy"] == "degeneration_v2_structural"

    # Stessa policy → ripresa completa dal checkpoint, nessuna richiesta
    _, c2 = _run(doc, _ok, monkeypatch, store=store)
    assert len(c2.log) == 0

    # Policy storica/diversa → identità diversa: il checkpoint NON viene riusato in silenzio
    monkeypatch.setattr(service_mod, "MICRO_OUTPUT_BUDGET_POLICY", "micro_budget_fixed_caps_640_448")
    res3, c3 = _run(doc, _ok, monkeypatch, store=store)
    assert len(c3.log) == 3, "tutti i blocchi ricalcolati: nessun riuso silenzioso del checkpoint con altra policy di budget"
    assert res3.metadata["status"] == "COMPLETE"


def test_identity_hash_differs_between_historical_and_current_budget_policy():
    from analysis.checkpoint import compute_run_config_hash
    base = dict(model_id=MODEL, prompt_version="discovery_operational")
    old = compute_run_config_hash(**base, extra_params={"max_topics": 10, "acceptance_policy": "degeneration_v2_structural"})
    new = compute_run_config_hash(**base, extra_params={"max_topics": 10, "acceptance_policy": "degeneration_v2_structural",
                                                         "micro_output_budget_policy": "micro_budget_formula_v2"})
    assert old != new


def test_export_declares_the_budget_policy_next_to_the_acceptance_policy(monkeypatch):
    doc = _doc("pho", 19)
    res, _ = _run(doc, _ok, monkeypatch)
    out = json.loads(serialize_report_to_json(build_topic_discovery_export_report(res, doc)))
    assert out["analysis_policy"] == {"acceptance_policy": "degeneration_v2_structural"}  # blocco storico invariato
    assert out["output_budget_policy"] == {"micro_output_budget_policy": "micro_budget_formula_v2"}
    assert service_mod.MICRO_OUTPUT_BUDGET_POLICY == "micro_budget_formula_v2"
