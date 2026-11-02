"""
tests/unit/test_discovery_description_contract.py
--------------------------------------------------
Audit mirato 02/11/2026 — contratto di output di Topic Discovery e `DESCRIPTION_INVALID`.
SOLO dati sintetici, client LLM finto (vero `LmStudioClient.chat_completion` su HTTP sintetico). Esito massimo: MOCK.

Domanda: prompt, `json_schema` strict e validatore esprimono lo STESSO contratto per `short_description`?
Risposta dimostrata dai test: NO, per un solo punto. Il validatore (ai/structured.py, validate_topic_discovery_payload)
rifiuta una `short_description` vuota o di soli spazi (DESCRIPTION_INVALID), mentre lo schema storico la ammette
(`{"type": "string"}` senza lunghezza minima): la grammatica non impediva al modello di emetterla. La variante
operativa dello schema aggiunge SOLO `minLength: 1`. Nessuna accettazione nuova, nessun testo inventato.

Questi test NON dimostrano che LM Studio applichi `minLength` né che Qwen smetta di emettere descrizioni vuote.
"""
from __future__ import annotations

import copy
import json
from typing import Any

import pytest

import ai.lmstudio as lms
import analysis.service as service_mod
from ai.backend import AiInvalidEvidenceCitationError, AiStructuredOutputError
from ai.structured import (
    DEGENERATION_POLICY_STRUCTURAL,
    DISCOVERY_OUTPUT_CONTRACT_VERSION,
    TOPIC_DISCOVERY_SCHEMA,
    TOPIC_DISCOVERY_SCHEMA_OPERATIONAL,
    validate_topic_discovery_payload,
)
from ai.topics import TopicDiscoveryAnalyzer
from analysis.checkpoint import AnalysisCheckpointStore
from analysis.export import build_topic_discovery_export_report, serialize_report_to_json
from tests.unit.test_detection_microchunk_checkpoint_resume import MODEL, _doc, _no_sleep  # noqa: F401
from tests.unit.test_discovery_json_failure_telemetry import MID_STRING, SECRET, http  # noqa: F401
from tests.unit.test_discovery_micro_budget_and_validation_kind import (  # noqa: F401
    _doc_36_with_ocr,
    _length_body,
    _ok,
    _run,
    _stop_body,
    _topics,
)
from tests.unit.test_detection_microchunk_checkpoint_resume import _service  # noqa: F401
from tests.unit.test_discovery_json_failure_telemetry import _aliases

ALIASES = {"E001": "ev1", "E002": "ev2", "E003": "ev3"}
VALID_IDS = {"ev1", "ev2", "ev3"}


def _validate(topics: list[Any], max_topics: int = 5):
    return validate_topic_discovery_payload(
        json.dumps({"topics": topics}), VALID_IDS, max_topics=max_topics, alias_map=ALIASES,
        degeneration_policy=DEGENERATION_POLICY_STRUCTURAL,
    )


def _topic(**over: Any) -> dict[str, Any]:
    t: dict[str, Any] = {"label": "Tema " + SECRET, "short_description": "Descrizione " + SECRET, "evidence_ids": ["E001"]}
    t.update(over)
    return t


def _kind(topics: list[Any], max_topics: int = 5) -> str | None:
    try:
        _validate(topics, max_topics)
    except AiInvalidEvidenceCitationError:
        return "CITATION"
    except AiStructuredOutputError as exc:
        return getattr(exc, "validation_kind", None)
    return None


# ----------------------------------------------------------------------------
# Verificatore minimale dello schema (sottoinsieme usato da TOPIC_DISCOVERY_SCHEMA): nessuna dipendenza esterna
# ----------------------------------------------------------------------------

def _conforms(schema: dict[str, Any], value: Any) -> bool:
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            return False
        props = schema.get("properties", {})
        if any(k not in value for k in schema.get("required", [])):
            return False
        if schema.get("additionalProperties") is False and any(k not in props for k in value):
            return False
        return all(_conforms(props[k], v) for k, v in value.items() if k in props)
    if t == "array":
        return isinstance(value, list) and all(_conforms(schema.get("items", {}), v) for v in value)
    if t == "string":
        return isinstance(value, str) and len(value) >= schema.get("minLength", 0)
    return True


# ============================================================================
# 1. Condizioni ESATTE che producono DESCRIPTION_INVALID (validatore)
# ============================================================================

@pytest.mark.parametrize("value", [None, 5, 1.5, True, [], {}, ["x"], "", " ", "\n\t  "])
def test_description_invalid_for_null_wrong_type_empty_or_whitespace(value):
    assert _kind([_topic(short_description=value)]) == "DESCRIPTION_INVALID"


def test_missing_description_key_is_a_different_kind_not_description_invalid():
    t = _topic()
    del t["short_description"]
    assert _kind([t]) == "TOPIC_KEYS_MISMATCH"


def test_extra_field_such_as_rationale_is_rejected_as_keys_mismatch():
    """Topic Discovery non ha `rationale` (è della Detection): un campo extra viola il contratto."""
    assert _kind([_topic(rationale="x")]) == "TOPIC_KEYS_MISMATCH"


@pytest.mark.parametrize("value, expected", [("Descrizione", "Descrizione"), ("  Descrizione con spazi  ", "Descrizione con spazi")])
def test_valid_description_is_accepted_and_stripped(value, expected):
    (t,) = _validate([_topic(short_description=value)])
    assert t.short_description == expected and t.evidence_ids == ("ev1",)


@pytest.mark.parametrize("label", [None, "", "  ", 7])
def test_label_with_valid_evidence_is_label_invalid_not_description_invalid(label):
    assert _kind([_topic(label=label)]) == "LABEL_INVALID"


def test_label_check_precedes_description_check():
    assert _kind([_topic(label="", short_description="")]) == "LABEL_INVALID"


def test_fully_empty_dummy_record_is_tolerated_but_not_a_description_exception():
    """Record dummy (label vuota + nessuna evidenza) è scartato dal validatore PRIMA del controllo sulla descrizione."""
    dummy = {"label": "", "short_description": "", "evidence_ids": []}
    assert len(_validate([_topic(), dummy])) == 1


@pytest.mark.parametrize("n_topics", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("bad_pos", ["first", "middle", "last"])
def test_one_invalid_description_rejects_the_whole_response_all_or_nothing(n_topics, bad_pos):
    """Nessuna accettazione parziale e nessun riempimento: i topic validi vicini NON salvano la risposta."""
    idx = {"first": 0, "middle": n_topics // 2, "last": n_topics - 1}[bad_pos]
    topics = [_topic(label=f"T{i}") for i in range(n_topics)]
    topics[idx]["short_description"] = ""
    assert _kind(topics) == "DESCRIPTION_INVALID"
    good = [t for i, t in enumerate(topics) if i != idx]
    assert len(_validate(good)) == n_topics - 1  # la stessa risposta senza il topic invalido sarebbe valida


def test_check_order_description_before_citation_and_citations_stay_strict():
    assert _kind([_topic(short_description="", evidence_ids=["E999"])]) == "DESCRIPTION_INVALID"
    assert _kind([_topic(evidence_ids=["E999"])]) == "CITATION"  # alias inventato: sempre rifiutato
    assert _kind([_topic(evidence_ids=["E001", "E999"])]) == "CITATION"  # niente fuzzy / nessun riuso parziale


# ============================================================================
# 2. Disallineamento schema strict ↔ validatore (la causa DETERMINISTICA di codice)
# ============================================================================

_STRINGS = ["", " ", "\n\t  ", "x", "Descrizione"]


def _accepted_by_schema(schema: dict[str, Any], s: str) -> bool:
    return _conforms(schema, {"topics": [{"label": "T", "short_description": s, "evidence_ids": ["E001"]}]})


def test_historical_schema_allows_strings_the_validator_rejects_gap_is_proven():
    gap = [s for s in _STRINGS if _accepted_by_schema(TOPIC_DISCOVERY_SCHEMA, s) and _kind([_topic(short_description=s)]) == "DESCRIPTION_INVALID"]
    assert gap == ["", " ", "\n\t  "]


def test_operational_schema_closes_the_empty_string_gap_only():
    gap = [s for s in _STRINGS if _accepted_by_schema(TOPIC_DISCOVERY_SCHEMA_OPERATIONAL, s) and _kind([_topic(short_description=s)]) == "DESCRIPTION_INVALID"]
    assert gap == [" ", "\n\t  "], "residuo dichiarato: le stringhe di soli spazi restano conformi allo schema ma rifiutate dal validatore"


def test_operational_schema_never_accepts_more_than_the_historical_one():
    for s in _STRINGS:
        if _accepted_by_schema(TOPIC_DISCOVERY_SCHEMA_OPERATIONAL, s):
            assert _accepted_by_schema(TOPIC_DISCOVERY_SCHEMA, s)
    for bad in (None, 3, [], {}, True):
        for sch in (TOPIC_DISCOVERY_SCHEMA, TOPIC_DISCOVERY_SCHEMA_OPERATIONAL):
            assert not _conforms(sch, {"topics": [{"label": "T", "short_description": bad, "evidence_ids": ["E001"]}]})
    for sch in (TOPIC_DISCOVERY_SCHEMA, TOPIC_DISCOVERY_SCHEMA_OPERATIONAL):  # chiavi mancanti/extra vietate da entrambi
        assert not _conforms(sch, {"topics": [{"label": "T", "evidence_ids": ["E001"]}]})
        assert not _conforms(sch, {"topics": [{"label": "T", "short_description": "d", "evidence_ids": ["E001"], "rationale": "x"}]})


def test_operational_schema_differs_from_historical_only_by_min_length_on_description():
    expected = copy.deepcopy(TOPIC_DISCOVERY_SCHEMA)
    expected["properties"]["topics"]["items"]["properties"]["short_description"]["minLength"] = 1
    assert TOPIC_DISCOVERY_SCHEMA_OPERATIONAL == expected
    assert "minLength" not in json.dumps(TOPIC_DISCOVERY_SCHEMA), "schema storico (modalità scientifica) invariato"
    assert TOPIC_DISCOVERY_SCHEMA_OPERATIONAL["required"] == ["topics"] and TOPIC_DISCOVERY_SCHEMA_OPERATIONAL["additionalProperties"] is False


def test_prompt_and_schema_describe_the_same_fields_and_prompt_does_not_ask_for_rationale(http):
    log_holder: dict[str, Any] = {}

    def resp(i, user):
        return _stop_body(_topics(user, 1))

    log = http(resp)
    an = TopicDiscoveryAnalyzer(client=lms.LmStudioClient(model_id=MODEL), default_model_id=MODEL, operational_mode=True)
    captured: list[dict[str, Any]] = []
    orig = lms.urllib.request.urlopen

    def spy(req, timeout=None):
        captured.append(json.loads(req.data.decode()))
        return orig(req, timeout)

    lms.urllib.request.urlopen = spy
    try:
        an.discover_topics(document=_doc("t", 6), model_id=MODEL, max_topics=5, max_tokens=640, operational_mode=True, context_length=8192)
    finally:
        lms.urllib.request.urlopen = orig
    sysmsg = captured[0]["messages"][0]["content"]
    assert '"label"' in sysmsg and '"short_description"' in sysmsg and '"evidence_ids"' in sysmsg
    assert "rationale" not in sysmsg
    assert "non-empty" not in sysmsg.lower() and "not empty" not in sysmsg.lower(), "il prompt NON è stato modificato"
    del log, log_holder


# ============================================================================
# 3. HTTP finto: STOP + JSON sintatticamente valido + descrizione non valida
# ============================================================================

def _capture(monkeypatch: pytest.MonkeyPatch, content_for: Any) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def fake(req: Any, timeout: float | None = None) -> Any:
        from tests.unit.test_discovery_json_failure_telemetry import _HttpResp

        payload = json.loads(req.data.decode())
        seen.append(payload)
        return _HttpResp(content_for(len(seen) - 1, payload["messages"][1]["content"]))

    monkeypatch.setattr(lms.urllib.request, "urlopen", fake)
    return seen


@pytest.mark.parametrize("bundles", [1, 4, 8])
@pytest.mark.parametrize("n_topics", [1, 3, 5])
@pytest.mark.parametrize("bad", ["", "   "])
def test_stop_with_invalid_description_is_rejected_with_validation_kind_and_no_leak(monkeypatch, bundles, n_topics, bad):
    doc = _doc("t", bundles)

    def content(i, user):
        al = _aliases(user)
        topics = [{"label": f"T{k} {SECRET}", "short_description": f"D{k} {SECRET}", "evidence_ids": al[:1]} for k in range(n_topics)]
        topics[n_topics - 1]["short_description"] = bad
        return _stop_body(json.dumps({"topics": topics}), completion=61)

    seen = _capture(monkeypatch, content)
    an = TopicDiscoveryAnalyzer(client=lms.LmStudioClient(model_id=MODEL), default_model_id=MODEL, operational_mode=True)
    with pytest.raises(AiStructuredOutputError) as ei:
        an.discover_topics(document=doc, model_id=MODEL, max_topics=5, max_tokens=856, operational_mode=True, context_length=8192)
    tel = ei.value.attempt_telemetry
    assert len(seen) == 2 and len(tel) == 2, "un solo retry: nessun aumento del numero di tentativi"
    for t in tel:
        assert (t["outcome"], t["finish_reason_kind"], t["validation_kind"], t["completion_tokens"]) == ("OTHER_STRUCTURED", "STOP", "DESCRIPTION_INVALID", 61)
        assert t["json_failure_kind"] is None, "JSON sintatticamente valido: non è un errore di parsing"
    assert [p["max_tokens"] for p in seen] == [856, 856], "il retry per errore di validazione NON aumenta i token"
    assert SECRET not in json.dumps(tel) and SECRET not in str(ei.value)
    # il secondo tentativo contiene la direttiva di correzione (nessun testo di chat nel messaggio d'errore)
    assert "CORRECTION DIRECTIVE" in seen[1]["messages"][-1]["content"]


def test_response_format_sent_to_lm_studio_is_strict_json_schema_with_the_operational_contract(monkeypatch):
    seen = _capture(monkeypatch, lambda i, u: _stop_body(_topics(u, 1)))
    an = TopicDiscoveryAnalyzer(client=lms.LmStudioClient(model_id=MODEL), default_model_id=MODEL, operational_mode=True)
    an.discover_topics(document=_doc("t", 6), model_id=MODEL, max_topics=5, max_tokens=640, operational_mode=True, context_length=8192)
    rf = seen[0]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == TOPIC_DISCOVERY_SCHEMA_OPERATIONAL
    assert "stream" not in seen[0] and "stop" not in seen[0]


def test_scientific_mode_keeps_the_historical_schema(monkeypatch):
    seen = _capture(monkeypatch, lambda i, u: _stop_body(_topics(u, 1)))
    an = TopicDiscoveryAnalyzer(client=lms.LmStudioClient(model_id=MODEL), default_model_id=MODEL, operational_mode=False)
    try:
        an.discover_topics(document=_doc("t", 6), model_id=MODEL, max_topics=5, max_tokens=640, operational_mode=False, context_length=8192)
    except Exception:  # noqa: BLE001 - la modalità scientifica ha regole proprie sugli alias; conta solo lo schema inviato
        pass
    assert seen and seen[0]["response_format"]["json_schema"]["schema"] == TOPIC_DISCOVERY_SCHEMA
    assert "minLength" not in json.dumps(seen[0]["response_format"])


# ============================================================================
# 4. Service-level: 36 msg + OCR sintetico — i tre esiti di errore restano SEPARATI, nessun COMPLETE finto
# ============================================================================

def test_service_keeps_description_citation_and_truncation_failures_separate_and_never_completes(monkeypatch):
    doc = _doc_36_with_ocr()

    def resp(call, user):
        pos = call["pos"]
        al = _aliases(user)
        if pos and min(pos) <= 7:  # parent 1: descrizione vuota (STOP, pochi token)
            return _stop_body(json.dumps({"topics": [{"label": "T " + SECRET, "short_description": "", "evidence_ids": al[:1]}]}), completion=57)
        if pos and 8 <= min(pos) <= 15:  # alias inventato
            return _stop_body(json.dumps({"topics": [{"label": "T", "short_description": "D", "evidence_ids": ["E999"]}]}))
        if pos and 16 <= min(pos) <= 23:  # troncamento al tetto
            return _length_body(MID_STRING, call["max_tokens"])
        return _ok(call, user)

    res, client = _run(doc, resp, monkeypatch)
    m = res.metadata
    assert m["status"] == "PARTIAL" and m["status"] != "COMPLETE"
    assert m["analyzed_evidence_count"] < m["total_evidence_count"] == 37
    txt = serialize_report_to_json(build_topic_discovery_export_report(res, doc))
    assert SECRET not in txt
    rows = json.loads(txt)["diagnostics_export"]["attempt_log"]
    by_micro: dict[int, set[tuple[str, Any]]] = {}
    for r in rows:
        by_micro.setdefault(r["micro_index"], set()).add((r["outcome"], r["validation_kind"]))
    assert ("OTHER_STRUCTURED", "DESCRIPTION_INVALID") in by_micro[0] and not any(o == "INVALID_CITATION" for o, _ in by_micro[0])
    assert ("INVALID_CITATION", None) in by_micro[1]
    assert ("TRUNCATED_LENGTH", None) in by_micro[2]
    desc_rows = [r for r in rows if r["validation_kind"] == "DESCRIPTION_INVALID"]
    assert desc_rows and all(r["finish_reason_kind"] == "STOP" and r["completion_tokens"] == 57 for r in desc_rows)
    # nessuna evidenza del blocco con descrizione vuota risulta analizzata: nessun SUCCESS fabbricato
    first_block_ids = {s.evidence_id for s in doc.all_evidence_sections[:8]}
    assert not (first_block_ids & set(m.get("analyzed_evidence_ids", ()) or ()))
    assert not any(t.short_description == "" for t in res.topics)


# ============================================================================
# 5. Identità / checkpoint / export
# ============================================================================

def test_contract_version_is_part_of_run_identity_and_old_checkpoints_are_not_reused(monkeypatch, tmp_path):
    doc = _doc("pho", 19)
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")
    extras: list[dict[str, Any]] = []
    real_hash = service_mod.compute_run_config_hash

    def spy(*a: Any, **k: Any) -> str:
        extras.append(dict(k.get("extra_params") or {}))
        return real_hash(*a, **k)

    monkeypatch.setattr(service_mod, "compute_run_config_hash", spy)
    res1, c1 = _run(doc, _ok, monkeypatch, store=store)
    assert res1.metadata["status"] == "COMPLETE" and len(c1.log) == 3
    assert extras[-1]["output_contract_version"] == DISCOVERY_OUTPUT_CONTRACT_VERSION == "discovery_contract_v2_nonempty_description"
    assert extras[-1]["micro_output_budget_policy"] == "micro_budget_formula_v2"  # identità di PR #8 conservata
    _, c2 = _run(doc, _ok, monkeypatch, store=store)
    assert len(c2.log) == 0
    monkeypatch.setattr(service_mod, "OUTPUT_CONTRACT_VERSION", "discovery_contract_v1_historical_schema")
    _, c3 = _run(doc, _ok, monkeypatch, store=store)
    assert len(c3.log) == 3, "nessun riuso silenzioso di un checkpoint creato con un altro contratto di output"


def test_export_declares_the_output_contract_and_keeps_previous_blocks(monkeypatch):
    doc = _doc("pho", 19)
    res, _ = _run(doc, _ok, monkeypatch)
    out = json.loads(serialize_report_to_json(build_topic_discovery_export_report(res, doc)))
    assert out["output_contract"] == {"schema_contract_version": "discovery_contract_v2_nonempty_description"}
    assert out["output_budget_policy"] == {"micro_output_budget_policy": "micro_budget_formula_v2"}
    assert out["analysis_policy"] == {"acceptance_policy": "degeneration_v2_structural"}
    legacy = type(res)(
        topics=res.topics, provenance_document_id=res.provenance_document_id,
        metadata={k: v for k, v in res.metadata.items() if k != "output_contract_version"},
    )
    assert "output_contract" not in build_topic_discovery_export_report(legacy, doc)
