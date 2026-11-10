"""
tests/unit/test_detection_observability.py
------------------------------------------
Osservabilità della Topic Detection (PR diagnostica): categorie, metriche numeriche di degenerazione,
contabilità HTTP vs tentativi, provenienza del resume, privacy dell'export.

SOLO DATI SINTETICI e client LLM finto (esito massimo: MOCK). Nessuna inferenza reale, nessun LM Studio,
nessun database o file reale. Nessun test qui cambia o verifica una scelta di accettazione diversa da quella
storica: il vecchio controllo di degenerazione CONTINUA a produrre l'esito storico (anche se falso positivo).
"""
from __future__ import annotations

import json
import random
import re
from typing import Any

import pytest

from ai.backend import (
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiStructuredOutputError,
    LlmCompletionResponse,
)
from ai.structured import (
    detect_degenerate_repetition,
    parse_and_validate_json,
    scan_degenerate_repetition,
)
from analysis.checkpoint import AnalysisCheckpointStore
from analysis.export import (
    ALLOWED_DIAGNOSTIC_CATEGORIES,
    ALLOWED_ERROR_TYPES,
    ALLOWED_FAILURE_PHASES,
    build_topic_detection_export_report,
    sanitize_detection_diagnostics,
    serialize_report_to_json,
)
from analysis.models import (
    ChunkDiagnosticCategory,
    ChunkDiagnosticRecord,
    OperationalAnalysisFailedError,
    classify_discovery_exception,
    safe_exception_text,
)
from analysis.progress import AnalysisProgressEvent
from analysis.service import detection_failure_metrics

# Riuso degli helper sintetici dei test di resume (stessa fixture documentale e stesso client finto).
from tests.unit.test_detection_microchunk_checkpoint_resume import (  # noqa: F401  (fixture autouse inclusa)
    MODEL,
    FakeClient,
    _doc,
    _ids,
    _no_sleep,
    _resp,
    _run,
    _service,
    store,
)

# Marcatore sintetico: simula un frammento di testo del modello/della chat che NON deve mai uscire.
SECRET = "ZXQSEGRETO"


# ----------------------------------------------------------------------------
# Copia "golden" della funzione storica (prima di questa PR): oracolo del test differenziale.
# ----------------------------------------------------------------------------

def _golden_detect_degenerate_repetition(text: str, threshold: int = 15) -> str | None:
    if not text:
        return None
    m_char = re.search(r'([^\s])\1{' + str(threshold - 1) + r',}', text)
    if m_char:
        char_repr = repr(m_char.group(1))
        return f"Ripetizione ciclica anomala del carattere {char_repr} ({len(m_char.group(0))} volte consecutive)"
    m_tab = re.search(r'(\t)\1{11,}', text)
    if m_tab:
        return f"Ripetizione ciclica di caratteri tabulari ({len(m_tab.group(0))} volte consecutive)"
    text_no_aliases = re.sub(r'(?:["\']E\d{3,4}["\']\s*,\s*){2,}', "", text)
    for m in re.finditer(r"(.{2,25})\1{5,}", text_no_aliases):
        pat = m.group(1)
        if pat.strip():
            pat_repr = repr(pat)
            return f"Loop di attrattore su sequenza {pat_repr} ({len(m.group(0)) // len(pat)} ripetizioni)"
    return None


def _json_with_rationale(rationale: str, decision: str = "ABSENT") -> str:
    return json.dumps({"decision": decision, "evidence_ids": [], "rationale": rationale})


def _public_surface(res: Any, doc: Any, events: list[AnalysisProgressEvent]) -> str:
    """Tutto ciò che l'utente/export può vedere: export JSON SAFE + messaggi di progresso + diagnostica per chunk."""
    report = build_topic_detection_export_report(res, doc)
    parts = [serialize_report_to_json(report)]
    parts.extend(f"{e.status_message} {dict(e.metadata)}" for e in events)
    parts.extend(json.dumps(dict(c), default=str) for c in res.metadata.get("chunk_diagnostics", []))
    return "\n".join(parts)


# ----------------------------------------------------------------------------
# 1. Il controllo storico NON cambia; la diagnostica riporta il tipo di innesco senza testo
# ----------------------------------------------------------------------------

_DIFFERENTIAL_CORPUS = [
    "", "ok", "a" * 14, "a" * 15, "a" * 40, "." * 15, "." * 20, "\t" * 11, "\t" * 12, "\t" * 30,
    "ah" * 5, "ah" * 6, "ah" * 30, "abc" * 6, "ab " * 6, "   " * 20, "ha ha " * 8,
    '["E001", "E002", "E003", "E004", "E005", "E006", "E007", "E008"]',
    "'E001','E002','E003'," * 5,
    _json_with_rationale("a" * 30), _json_with_rationale("." * 25), _json_with_rationale("ahah" * 10),
    _json_with_rationale("Testo normale senza ripetizioni, con alcune frasi diverse."),
    "abcdefghijklmnopqrstuvwxy" * 6, "abcdefghijklmnopqrstuvwxy" * 5, "x\ty\t" * 20,
]


def test_scan_matches_historical_function_on_fixed_corpus():
    for text in _DIFFERENTIAL_CORPUS:
        golden = _golden_detect_degenerate_repetition(text)
        assert detect_degenerate_repetition(text) == golden
        scan = scan_degenerate_repetition(text)
        assert (scan.message if scan else None) == golden


def test_scan_matches_historical_function_on_seeded_fuzz():
    rng = random.Random(20261101)
    alphabet = ["a", "b", ".", " ", "\t", "E001", '"', ",", "xy", "ahah", "\n"]
    for _ in range(600):
        text = "".join(rng.choice(alphabet) * rng.randint(1, 9) for _ in range(rng.randint(1, 12)))
        golden = _golden_detect_degenerate_repetition(text)
        assert detect_degenerate_repetition(text) == golden
        scan = scan_degenerate_repetition(text)
        assert (scan.message if scan else None) == golden


@pytest.mark.parametrize(
    "rationale, kind",
    [
        ("a" * 30, "CHAR_RUN"),            # molte 'a'
        ("." * 25, "CHAR_RUN"),            # molti punti
        ("ahah" * 10, "PATTERN_LOOP"),     # pattern ciclico
    ],
)
def test_valid_json_with_repetitive_rationale_keeps_historical_rejection_and_reports_trigger(rationale, kind):
    raw = _json_with_rationale(rationale)
    assert json.loads(raw)["decision"] == "ABSENT"  # il JSON è sintatticamente e semanticamente valido
    with pytest.raises(AiStructuredOutputError) as ei:
        parse_and_validate_json(raw)
    # esito storico: scartata come degenerazione ciclica (anche se è un falso positivo)
    assert str(ei.value).startswith("Risposta del modello scartata per degenerazione ciclica: ")
    assert str(ei.value) == "Risposta del modello scartata per degenerazione ciclica: " + _golden_detect_degenerate_repetition(raw)
    meta = ei.value.degeneration
    assert meta["trigger_kind"] == kind
    assert meta["response_length"] == len(raw)
    # solo enum e numeri: nessun testo
    assert all(isinstance(v, (int, type(None))) for k, v in meta.items() if k != "trigger_kind")
    assert classify_discovery_exception(ei.value)[0] == ChunkDiagnosticCategory.DEGENERATE_OUTPUT.value


def test_failure_metrics_are_numeric_only_and_do_not_mutate_exception_message():
    raw = _json_with_rationale(SECRET * 8)
    with pytest.raises(AiStructuredOutputError) as ei:
        parse_and_validate_json(raw)
    msg_before = str(ei.value)
    assert SECRET in msg_before  # il messaggio storico (usato dai retry) resta invariato
    metrics = detection_failure_metrics(ei.value, bundle_count=3)
    assert str(ei.value) == msg_before
    assert SECRET not in json.dumps(metrics)
    assert metrics["trigger_kind"] == "PATTERN_LOOP"
    assert metrics["pattern_length"] == len(SECRET) and metrics["repetition_count"] >= 6
    assert metrics["bundle_count"] == 3
    # niente zeri inventati: finish_reason/token non disponibili restano None
    assert metrics["finish_reason"] is None and metrics["completion_tokens_cumulative"] is None


def test_metrics_for_exception_without_degeneration_are_none_not_zero():
    m = detection_failure_metrics(AiBackendTimeoutError("timeout simulato"), bundle_count=2)
    assert m["trigger_kind"] is None
    assert m["pattern_length"] is None and m["repetition_count"] is None and m["run_length"] is None
    assert detection_failure_metrics(None, 1)["trigger_kind"] is None


def test_safe_exception_text_never_contains_model_fragment():
    with pytest.raises(AiStructuredOutputError) as ei:
        parse_and_validate_json(_json_with_rationale(SECRET * 8))
    txt = safe_exception_text(ei.value)
    assert SECRET not in txt and txt.startswith("AiStructuredOutputError")
    # eccezioni non strutturate: comportamento storico (nessun frammento del modello possibile)
    assert safe_exception_text(AiBackendTimeoutError("timeout simulato")) == "timeout simulato"


def test_end_to_end_false_positive_is_still_rejected_and_retry_prompt_is_unchanged():
    """Il JSON valido con rationale ripetitivo viene ANCORA scartato (esito storico); il retry usa lo stesso testo."""
    doc = _doc("obs_fp", 20)
    client = FakeClient(lambda u, n: LlmCompletionResponse(
        content=_json_with_rationale("a" * 40), model=MODEL, raw_response={"finish_reason": "stop"}))
    events: list[AnalysisProgressEvent] = []
    with pytest.raises(OperationalAnalysisFailedError):
        _run(_service(client), doc, client, progress_callback=events.append)
    assert len(client.calls) > 1
    retry_prompts = [c for c in client.calls[1:] if "degenerazione ciclica" in c]
    assert retry_prompts, "la direttiva correttiva dei retry deve contenere ancora il messaggio storico"


# ----------------------------------------------------------------------------
# 2. Classificazione e privacy per le diverse famiglie di fallimento
# ----------------------------------------------------------------------------

def _grammar_error() -> Exception:
    err = AiBackendRequestError("Errore grammatica simulato")
    err.error_category = "INFERENCE_BACKEND_ERROR"
    return err


def _truncated() -> LlmCompletionResponse:
    return LlmCompletionResponse(content='{"decision": "PRES', model=MODEL, raw_response={"finish_reason": "length"})


_FAILURE_CASES = {
    "invalid_json": (lambda: LlmCompletionResponse(content=f"{SECRET} non è JSON", model=MODEL, raw_response={"finish_reason": "stop"}), "JSON_DECODE_ERROR"),
    "truncated_json": (_truncated, None),
    "empty_output": (lambda: LlmCompletionResponse(content="", model=MODEL, raw_response={"finish_reason": "stop"}), None),
    "invented_alias": (lambda: _resp("PRESENT", ["E999"]), None),
    "repetitive_pattern": (lambda: LlmCompletionResponse(content=SECRET * 12, model=MODEL, raw_response={"finish_reason": "stop"}), "DEGENERATE_OUTPUT"),
    "valid_json_false_positive": (lambda: LlmCompletionResponse(content=_json_with_rationale("a" * 40), model=MODEL, raw_response={"finish_reason": "stop"}), "DEGENERATE_OUTPUT"),
    "grammar": (_grammar_error, "GBNF_GRAMMAR_ERROR"),
}


@pytest.mark.parametrize("name", sorted(_FAILURE_CASES))
def test_failure_families_are_classified_in_closed_sets_and_do_not_leak(name):
    make, expected_category = _FAILURE_CASES[name]
    doc = _doc("obs_f", 20)

    def handler(user: str, n: int):
        if 9 in _ids(user, "obs_f"):
            out = make()
            if isinstance(out, Exception):
                raise out
            return out
        return _resp("ABSENT")

    client = FakeClient(handler)
    events: list[AnalysisProgressEvent] = []
    res = _run(_service(client), doc, client, progress_callback=events.append)

    assert res.metadata["status"] == "PARTIAL" and res.decision.value == "UNCERTAIN"
    failed = [dict(c) for c in res.metadata["chunk_diagnostics"] if dict(c)["status"] == "FAILED"]
    assert failed
    for d in failed:
        assert d["diagnostic_category"] in ALLOWED_DIAGNOSTIC_CATEGORIES, d["diagnostic_category"]
        assert d["failure_phase"] in ALLOWED_FAILURE_PHASES, d["failure_phase"]
        assert d["error_type"] in ALLOWED_ERROR_TYPES, d["error_type"]
        if expected_category:
            assert d["diagnostic_category"] == expected_category
    surface = _public_surface(res, doc, events)
    assert SECRET not in surface
    assert "Errore grammatica simulato" not in surface


def test_single_chunk_failure_progress_and_error_text_do_not_leak_model_fragment():
    """Documento a blocco singolo: il messaggio di progresso non riporta il frammento del modello (prima lo faceva)."""
    doc = _doc("obs_sc", 8)
    client = FakeClient(lambda u, n: LlmCompletionResponse(content=SECRET * 12, model=MODEL, raw_response={"finish_reason": "stop"}))
    events: list[AnalysisProgressEvent] = []
    with pytest.raises(AiStructuredOutputError) as ei:  # contratto invariato: stessa eccezione storica
        _run(_service(client), doc, client, progress_callback=events.append)
    assert events and events[-1].is_failed
    assert all(SECRET not in e.status_message and SECRET not in json.dumps(dict(e.metadata), default=str) for e in events)
    assert SECRET not in safe_exception_text(ei.value)  # testo che la UI deve mostrare al posto di str(exc)


def test_timeout_and_exhausted_retries_are_classified_and_private():
    doc = _doc("obs_t", 20)

    def handler(user: str, n: int):
        if 9 in _ids(user, "obs_t"):
            raise AiBackendTimeoutError(f"timeout simulato {SECRET}")
        return _resp("ABSENT")

    client = FakeClient(handler)
    events: list[AnalysisProgressEvent] = []
    res = _run(_service(client), doc, client, progress_callback=events.append)
    cats = {dict(c)["diagnostic_category"] for c in res.metadata["chunk_diagnostics"] if dict(c)["status"] == "FAILED"}
    assert cats <= {"TIMEOUT", "TIME_BUDGET_EXHAUSTED"} and "TIMEOUT" in cats
    assert SECRET not in _public_surface(res, doc, events)
    assert classify_discovery_exception(AiStructuredOutputError("Tentativi esauriti per il blocco"))[0] == "RETRY_EXHAUSTED"


def test_allowlist_covers_every_category_enum_value_and_emitted_phase():
    enum_values = {c.value for c in ChunkDiagnosticCategory}
    missing = enum_values - ALLOWED_DIAGNOSTIC_CATEGORIES
    assert not missing, f"categorie dell'enum non esportabili: {sorted(missing)}"
    for needed in ("json_parsing", "schema_validation", "budget", "checkpoint_save"):
        assert needed in ALLOWED_FAILURE_PHASES


# ----------------------------------------------------------------------------
# B. Propagazione al parent e precedenza con più fallimenti
# ----------------------------------------------------------------------------

def _multi_failure_handler(doc_id: str):
    """Microchunk 8-15: JSON non valido (primo a fallire); microchunk 16-23: degenerazione."""
    def handler(user: str, n: int):
        ids = _ids(user, doc_id)
        if any(8 <= i < 16 for i in ids):
            return LlmCompletionResponse(content="non è JSON", model=MODEL, raw_response={"finish_reason": "stop"})
        if any(16 <= i < 24 for i in ids):
            return LlmCompletionResponse(content="x" * 40, model=MODEL, raw_response={"finish_reason": "stop"})
        return _resp("ABSENT")
    return handler


def test_parent_inherits_category_and_error_type_of_first_real_failure():
    doc = _doc("obs_p", 60)
    client = FakeClient(_multi_failure_handler("obs_p"))
    svc = _service(client, max_chunk_bundles=30)
    res = _run(svc, doc, client)
    parents = [dict(c) for c in res.metadata["chunk_diagnostics"] if dict(c).get("diagnostic_metrics", {}) and "failed_leaf_count" in dict(c)["diagnostic_metrics"]]
    assert len(parents) == 1
    p = parents[0]
    assert p["status"] == "FAILED"
    assert p["diagnostic_category"] in {"JSON_DECODE_ERROR", "DEGENERATE_OUTPUT"}
    assert p["error_type"] == "AiStructuredOutputError"
    fm = p["diagnostic_metrics"]
    counts = dict(fm["failure_category_counts"])
    assert counts.get("JSON_DECODE_ERROR", 0) > 0 and counts.get("DEGENERATE_OUTPUT", 0) > 0
    # il parent riporta la categoria del PRIMO fallimento reale (ordine di esecuzione), non una a caso
    assert fm["first_failure"]["diagnostic_category"] == p["diagnostic_category"] == "JSON_DECODE_ERROR"
    assert dict(fm["degeneration_trigger_counts"]).get("CHAR_RUN", 0) > 0


def test_time_budget_leaf_does_not_mask_real_failure_category():
    from analysis.service import LocalTopicAnalysisService as S
    mk = lambda cat, st="FAILED": ChunkDiagnosticRecord(  # noqa: E731
        chunk_index=0, chunk_id="c0", status=st, diagnostic_category=cat, failure_phase="inference", error_type="X", attempts=1)
    budget = mk("TIME_BUDGET_EXHAUSTED")
    real = mk("DEGENERATE_OUTPUT")
    ok = mk("SUCCESS", "SUCCESS")
    assert S._first_real_failure([ok, budget, real]).diagnostic_category == "DEGENERATE_OUTPUT"
    assert S._first_real_failure([ok, budget]).diagnostic_category == "TIME_BUDGET_EXHAUSTED"
    assert S._first_real_failure([ok]) is None


# ----------------------------------------------------------------------------
# 3. Bisezione: tentativi a livello foglia != richieste HTTP effettive
# ----------------------------------------------------------------------------

def test_fully_degenerate_parent_has_80_leaf_attempts_but_more_http_requests():
    doc = _doc("obs_b", 80)

    def handler(user: str, n: int):
        if any(i < 40 for i in _ids(user, "obs_b")):
            return LlmCompletionResponse(content="x" * 40, model=MODEL, raw_response={"finish_reason": "stop"})
        return _resp("ABSENT")

    client = FakeClient(handler)
    events: list[AnalysisProgressEvent] = []
    res = _run(_service(client, max_chunk_bundles=40), doc, client, progress_callback=events.append)

    d = res.metadata["detection_diagnostics"]
    # parent 0: 5 microchunk x (8 foglie x 2 tentativi) = 80 tentativi foglia; 5 x 15 nodi x 2 = 150 richieste HTTP
    # parent 1: 5 microchunk validati alla prima chiamata
    assert d["leaf_attempts_total"] == 80 + 5
    assert d["http_requests_sent"] == 150 + 5 == len(client.calls) == events[-1].requests_sent
    assert d["http_requests_basis"] == "REQUEST_LISTENER"
    assert d["http_requests_sent"] > d["leaf_attempts_total"]
    assert d["node_failures"] == 5 * 15 and d["degenerate_failures"] == 5 * 15
    assert d["degeneration_trigger_char_run"] == 5 * 15
    assert d["http_requests_accounting_consistent"] is True
    p0 = next(dict(c) for c in res.metadata["chunk_diagnostics"]
              if dict(c).get("diagnostic_metrics", {}) and "failed_leaf_count" in dict(c)["diagnostic_metrics"])
    assert p0["attempts"] == 80 and p0["resume_provenance"]["current_run_http_requests"] == 150
    assert p0["diagnostic_metrics"]["failed_leaf_count"] == 40
    assert all(e.is_requests_accounting_consistent for e in events)


def test_http_requests_not_available_is_none_not_zero():
    """Nodi eseguiti ma nessuna richiesta riportata dal listener: 'non disponibile' (None), NON 0."""
    import time
    from analysis.progress import AnalysisProgressTracker
    from analysis.service import LocalTopicAnalysisService as S, new_detection_stats

    t0 = time.perf_counter()
    tracker = AnalysisProgressTracker(total_chunks=1)
    stats = new_detection_stats()
    stats["leaf_attempts"] = 3
    na = S._detection_diagnostics_summary(tracker, stats, 1, 1, t0)
    assert na["http_requests_basis"] == "NOT_AVAILABLE"
    assert na["http_requests_sent"] is None and na["http_requests_failed"] is None
    assert na["http_requests_accounting_consistent"] is None
    assert na["leaf_attempts_total"] == 3

    # nessun nodo eseguito (es. tutto recuperato da checkpoint): zero REALE, con base dichiarata
    zero = S._detection_diagnostics_summary(AnalysisProgressTracker(total_chunks=1), new_detection_stats(), 1, 1, t0)
    assert zero["http_requests_basis"] == "REQUEST_LISTENER" and zero["http_requests_sent"] == 0

    # listener attivo: valori interi e invariante verificata
    tracker.record_request_sent()
    tracker.record_request_success()
    ok = S._detection_diagnostics_summary(tracker, stats, 1, 1, t0)
    assert ok["http_requests_sent"] == 1 and ok["http_requests_successful"] == 1
    assert ok["http_requests_accounting_consistent"] is True


# ----------------------------------------------------------------------------
# 4. Parent validi + falliti, recupero da checkpoint
# ----------------------------------------------------------------------------

def _first_run_with_failed_parent(store, doc):
    bad = LlmCompletionResponse(content="x" * 40, model=MODEL, raw_response={"finish_reason": "stop"})

    def handler(user: str, n: int):
        return bad if any(24 <= i < 32 for i in _ids(user, "obs_r")) else _resp("ABSENT")

    client = FakeClient(handler)
    res = _run(_service(client, store, max_chunk_bundles=20), doc, client)
    return client, res


def test_resume_makes_no_calls_on_recovered_parents_and_reports_truthful_provenance(store):
    doc = _doc("obs_r", 60)
    client1, res1 = _first_run_with_failed_parent(store, doc)
    assert res1.metadata["status"] == "PARTIAL"
    parents1 = {dict(c)["chunk_index"]: dict(c) for c in res1.metadata["chunk_diagnostics"] if dict(c).get("resume_provenance")}
    assert {k: v["status"] for k, v in parents1.items()} == {0: "SUCCESS", 1: "FAILED", 2: "SUCCESS"}
    assert all(p["resume_provenance"]["result_origin"] == "COMPUTED_THIS_RUN" for p in parents1.values())
    # nessun doppio conteggio: la somma della spesa per parent coincide con le richieste reali
    assert sum(p["resume_provenance"]["current_run_http_requests"] for p in parents1.values()) == len(client1.calls)
    assert res1.metadata["detection_diagnostics"]["http_requests_sent"] == len(client1.calls)
    assert res1.metadata["detection_diagnostics"]["parents_recovered_from_checkpoint"] == 0

    # RESUME: tutto valido; i parent 0 e 2 sono nel checkpoint
    client2 = FakeClient(lambda u, n: _resp("ABSENT"))
    res2 = _run(_service(client2, store, max_chunk_bundles=20), doc, client2)
    assert res2.metadata["status"] == "SUCCESS"
    seen = {i for c in client2.calls for i in _ids(c, "obs_r")}
    assert seen and all(20 <= i < 36 for i in seen), "nessuna chiamata sui parent recuperati né sul microchunk 36-39 già validato"
    assert len(client2.calls) == 2

    prov = {dict(c)["chunk_index"]: dict(c)["resume_provenance"] for c in res2.metadata["chunk_diagnostics"] if dict(c).get("resume_provenance")}
    assert prov[0]["result_origin"] == prov[2]["result_origin"] == "CHECKPOINT_VALIDATED"
    assert prov[0]["current_run_http_requests"] == prov[2]["current_run_http_requests"] == 0
    assert prov[0]["original_attempts"] is None, "la spesa storica non è disponibile: None, non uno 0 inventato"
    assert prov[1]["result_origin"] == "MIXED_CHECKPOINT_AND_COMPUTED"
    assert (prov[1]["microchunks_recovered"], prov[1]["microchunks_computed"]) == (1, 2)
    assert sum(p["current_run_http_requests"] for p in prov.values()) == len(client2.calls)

    d2 = res2.metadata["detection_diagnostics"]
    assert d2["parents_recovered_from_checkpoint"] == 2 and d2["parents_mixed_checkpoint_and_computed"] == 1
    assert d2["parents_computed_this_run"] == 0
    assert d2["http_requests_sent"] == len(client2.calls)
    assert d2["microchunks_recovered_from_checkpoint"] == 1 and d2["microchunks_computed_this_run"] == 2
    assert res2.metadata["recovered_chunk_count"] == 2, "chunks_recovered non deve restare a 0 dopo un resume"


def test_export_after_resume_is_not_live_verified_and_keeps_provenance(store):
    doc = _doc("obs_r", 60)
    _first_run_with_failed_parent(store, doc)
    client2 = FakeClient(lambda u, n: _resp("ABSENT"))
    res2 = _run(_service(client2, store, max_chunk_bundles=20), doc, client2)
    report = build_topic_detection_export_report(res2, doc)
    text = serialize_report_to_json(report)
    assert "LIVE_VERIFIED" not in text
    assert report["execution"]["detection_diagnostics"]["parents_recovered_from_checkpoint"] == 2
    origins = {c.get("resume_provenance", {}).get("result_origin") for c in report["chunks"]} - {None}
    assert origins == {"CHECKPOINT_VALIDATED", "MIXED_CHECKPOINT_AND_COMPUTED"}


# ----------------------------------------------------------------------------
# 5. Export SAFE
# ----------------------------------------------------------------------------

def test_export_is_safe_additive_and_backward_compatible():
    doc = _doc(f"obs_{SECRET}", 60)
    client = FakeClient(_multi_failure_handler(f"obs_{SECRET}"))
    events: list[AnalysisProgressEvent] = []
    res = _run(_service(client, max_chunk_bundles=30), doc, client, progress_callback=events.append)
    report = build_topic_detection_export_report(res, doc)
    text = serialize_report_to_json(report)

    # nessun contenuto grezzo, nessun path personale, nessun identificativo del documento
    for forbidden in (SECRET, "Messaggio sintetico", "/test", "per verifica evidenze", "non è JSON"):
        assert forbidden not in text, forbidden

    # campi esistenti presenti e invariati nel tipo
    assert report["schema_version"] == "1.1.0"
    for key in ("chunks", "execution"):
        assert key in report
    chunk = report["chunks"][0]
    for legacy in ("chunk_index", "status", "diagnostic_category", "failure_phase", "error_type"):
        assert legacy in chunk
    # campi nuovi: additivi
    dd = report["execution"]["detection_diagnostics"]
    assert isinstance(dd["http_requests_sent"], int) and dd["http_requests_basis"] == "REQUEST_LISTENER"
    assert set(dd["degeneration_trigger_counts"]) == {"CHAR_RUN", "TAB_RUN", "PATTERN_LOOP"}
    failed_parent = next(c for c in report["chunks"] if c.get("diagnostic_metrics", {}).get("failed_leaf_count"))
    ff = failed_parent["diagnostic_metrics"]["first_failure"]
    assert set(ff) >= {"diagnostic_category", "failure_phase", "error_type", "trigger_kind", "pattern_length", "repetition_count"}
    assert all(not isinstance(v, str) or v.isupper() or v.islower() or "_" in v or v in ("AiStructuredOutputError",) for v in ff.values() if v is not None)


def test_sanitizers_reject_arbitrary_strings_and_distinguish_none_from_zero():
    dd = sanitize_detection_diagnostics({
        "http_requests_basis": f"{SECRET} libero", "checkpoint_persistence": SECRET, "http_requests_sent": None,
        "retries": 0, "degeneration_trigger_char_run": 0, "degeneration_trigger_tab_run": None,
    })
    assert dd["http_requests_basis"] is None and dd["checkpoint_persistence"] is None
    assert dd["http_requests_sent"] is None and dd["retries"] == 0
    assert dd["degeneration_trigger_counts"]["CHAR_RUN"] == 0 and dd["degeneration_trigger_counts"]["TAB_RUN"] is None
    assert SECRET not in json.dumps(dd)
    assert sanitize_detection_diagnostics(None) is None and sanitize_detection_diagnostics({}) is None

    from analysis.export import sanitize_diagnostic_metrics, sanitize_resume_provenance
    m = sanitize_diagnostic_metrics({"trigger_kind": SECRET, "finish_reason": SECRET, "pattern_length": "7", "repetition_count": True})
    assert m["trigger_kind"] is None and m["finish_reason"] == "unknown", "valore non in allowlist: mai riportato in chiaro"
    p = sanitize_resume_provenance({"result_origin": SECRET, "via_microchunks": "si", "current_run_http_requests": 0})
    assert p["result_origin"] is None and p["via_microchunks"] is None and p["current_run_http_requests"] == 0
    assert p["original_attempts"] is None


def test_chunk_diagnostic_record_roundtrip_is_backward_compatible():
    legacy = ChunkDiagnosticRecord(chunk_index=3, chunk_id="c3", status="SUCCESS", diagnostic_category="SUCCESS", failure_phase="none", error_type=None, attempts=1)
    d = legacy.to_dict()
    assert "diagnostic_metrics" not in d and "resume_provenance" not in d, "chiavi nuove solo se valorizzate"
    assert ChunkDiagnosticRecord.from_dict(d).diagnostic_metrics is None
    rich = ChunkDiagnosticRecord(chunk_index=3, chunk_id="c3", status="FAILED", diagnostic_category="DEGENERATE_OUTPUT", failure_phase="inference",
                                 error_type="AiStructuredOutputError", attempts=2, diagnostic_metrics={"trigger_kind": "CHAR_RUN"},
                                 resume_provenance={"result_origin": "COMPUTED_THIS_RUN"})
    back = ChunkDiagnosticRecord.from_dict(rich.to_dict())
    assert back.diagnostic_metrics == {"trigger_kind": "CHAR_RUN"} and back.resume_provenance["result_origin"] == "COMPUTED_THIS_RUN"


# ----------------------------------------------------------------------------
# 6. Volume 26 parent / 1.049 messaggi sintetici, senza inferenza (client finto)
# ----------------------------------------------------------------------------

def test_volume_26_parents_1049_messages_tracker_invariants():
    doc = _doc("obs_vol", 1049)
    client = FakeClient(lambda u, n: _resp("ABSENT"))
    events: list[AnalysisProgressEvent] = []
    res = _run(_service(client, max_chunk_bundles=41), doc, client, progress_callback=events.append)
    d = res.metadata["detection_diagnostics"]
    assert (d["parent_chunks_total"], d["parent_chunks_completed"], d["parent_chunks_failed"]) == (26, 26, 0)
    covered = sorted(i for c in client.calls for i in _ids(c, "obs_vol"))
    assert covered == list(range(1049)), "copertura esatta, nessun duplicato"
    assert d["microchunks_total"] == d["microchunks_completed"] == len(client.calls) == d["http_requests_sent"]
    assert d["leaf_attempts_total"] == len(client.calls)
    assert d["microchunks_failed"] == 0 and d["node_failures"] == 0
    assert d["parents_computed_this_run"] == 26 and d["parents_recovered_from_checkpoint"] == 0
    assert res.metadata["status"] == "SUCCESS"
    assert all(e.is_requests_accounting_consistent for e in events)
    assert all(e.microchunks_completed + e.microchunks_failed <= e.microchunks_total for e in events)
    assert events[-1].requests_sent == len(client.calls)
