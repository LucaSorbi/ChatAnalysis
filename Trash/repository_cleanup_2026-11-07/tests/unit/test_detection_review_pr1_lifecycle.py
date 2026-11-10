"""
tests/unit/test_detection_review_pr1_lifecycle.py
-------------------------------------------------
Correzioni della revisione indipendente della PR #1 (dati SINTETICI, client finto, DB temporanei):
ciclo di vita dei checkpoint, errori di scrittura osservabili, contabilità parent/microchunk, budget finiti.
Esito massimo: MOCK VERIFIED.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ai.backend import AiStructuredOutputError
from ai.models import TopicDecision
from analysis.checkpoint import AnalysisCheckpointStore
from tests.unit.test_detection_microchunk_checkpoint_resume import (  # noqa: F401  (fixture autouse inclusa)
    FakeClient,
    _doc,
    _fail_on_message,
    _ids,
    _no_sleep,
    _resp,
    _run,
    _service,
)


def _counts(db: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("checkpoints", "checkpoint_chunks", "checkpoint_subchunks")
        }
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Problema 1: nessuna cancellazione automatica dei checkpoint dopo un SUCCESS
# ----------------------------------------------------------------------------

def test_multichunk_success_keeps_all_completed_checkpoints(tmp_path):
    chk_dir = tmp_path / "chk"
    store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)
    doc = _doc("lc_a", 40)
    client = FakeClient(lambda u, n: _resp("ABSENT"))
    res = _run(_service(client, store, max_chunk_bundles=20), doc, client)

    assert res.metadata["status"] == "SUCCESS" and res.decision == TopicDecision.ABSENT
    c = _counts(chk_dir / "checkpoints.db")
    assert c["checkpoints"] == 1
    assert c["checkpoint_chunks"] == 2, "i 2 parent completati devono restare salvati"
    assert c["checkpoint_subchunks"] == 6, "i 6 microchunk validati devono restare salvati"


def test_success_then_reload_and_restart_reuse_checkpoint_without_http_calls(tmp_path):
    chk_dir = tmp_path / "chk"
    doc = _doc("lc_b", 40)
    c1 = FakeClient(lambda u, n: _resp("ABSENT"))
    r1 = _run(_service(c1, AnalysisCheckpointStore(checkpoint_dir=chk_dir), max_chunk_bundles=20), doc, c1)
    assert r1.metadata["status"] == "SUCCESS" and len(c1.calls) == 6

    # "riavvio": nuovo oggetto store sulla stessa cartella, nuovo service, nuovo client
    c2 = FakeClient(lambda u, n: _resp("PRESENT", ["E001"]))  # se fosse chiamato cambierebbe l'esito
    r2 = _run(_service(c2, AnalysisCheckpointStore(checkpoint_dir=chk_dir), max_chunk_bundles=20), doc, c2)
    assert c2.calls == [], "con checkpoint completi non deve partire alcuna chiamata"
    assert r2.metadata["status"] == "SUCCESS" and r2.decision == TopicDecision.ABSENT
    assert _counts(chk_dir / "checkpoints.db")["checkpoint_chunks"] == 2


def test_single_chunk_success_keeps_checkpoint(tmp_path):
    chk_dir = tmp_path / "chk"
    doc = _doc("lc_c", 20)
    client = FakeClient(lambda u, n: _resp("ABSENT"))
    res = _run(_service(client, AnalysisCheckpointStore(checkpoint_dir=chk_dir)), doc, client)
    assert res.metadata["status"] == "SUCCESS"
    c = _counts(chk_dir / "checkpoints.db")
    assert c["checkpoints"] == 1 and c["checkpoint_chunks"] == 1 and c["checkpoint_subchunks"] == 3


def test_service_never_calls_clear_checkpoint_on_success(tmp_path, monkeypatch):
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")

    def boom(*a, **k):  # pragma: no cover - deve restare non invocata
        raise AssertionError("clear_checkpoint non deve essere invocato dal service")

    monkeypatch.setattr(store, "clear_checkpoint", boom)
    doc = _doc("lc_d", 40)
    client = FakeClient(lambda u, n: _resp("ABSENT"))
    assert _run(_service(client, store, max_chunk_bundles=20), doc, client).metadata["status"] == "SUCCESS"


# ----------------------------------------------------------------------------
# Problema 2: il fallimento di scrittura è osservabile e distingue inferenza da persistenza
# ----------------------------------------------------------------------------

def test_all_writes_ok_is_reported_as_persistent(tmp_path):
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")
    doc = _doc("wf_ok", 20)
    client = FakeClient(lambda u, n: _resp("ABSENT"))
    d = _run(_service(client, store), doc, client).metadata["detection_diagnostics"]
    assert d["checkpoint_write_failures"] == 0 and d["checkpoint_writes_ok"] == 4  # 3 microchunk + 1 parent
    assert d["checkpoint_persistence"] == "OK" and d["resume_guaranteed"] is True


def test_microchunk_write_failure_is_counted_and_resume_not_promised(tmp_path):
    chk_dir = tmp_path / "chk"
    store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)

    def failing_save(*a, **k):
        raise sqlite3.OperationalError("database is locked: testo riservato")

    store.save_subchunk = failing_save  # type: ignore[method-assign]
    doc = _doc("wf_sub", 20)
    client = FakeClient(lambda u, n: _resp("ABSENT"))
    res = _run(_service(client, store), doc, client)

    # l'inferenza resta valida: nessuna alterazione silenziosa del risultato semantico...
    assert res.metadata["status"] == "SUCCESS" and res.decision == TopicDecision.ABSENT
    # ...ma la persistenza NON è dichiarata riuscita
    d = res.metadata["detection_diagnostics"]
    assert d["checkpoint_write_failures"] == 3
    assert d["checkpoint_persistence"] == "WRITE_FAILURES" and d["resume_guaranteed"] is False
    assert all(r["checkpoint_saved"] is False for r in res.metadata["chunk_diagnostics"])
    assert "testo riservato" not in repr(d), "nessun messaggio dell'eccezione nella diagnostica"
    assert _counts(chk_dir / "checkpoints.db")["checkpoint_subchunks"] == 0

    # il parent è stato comunque salvato (solo save_subchunk falliva): un nuovo run lo riusa legittimamente
    c2 = FakeClient(lambda u, n: _resp("ABSENT"))
    r2 = _run(_service(c2, AnalysisCheckpointStore(checkpoint_dir=chk_dir)), doc, c2)
    assert c2.calls == [] and r2.metadata["status"] == "SUCCESS"


def test_total_write_failure_leaves_nothing_to_resume_and_rerun_reinfers_everything(tmp_path):
    chk_dir = tmp_path / "chk"
    store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)

    def failing(*a, **k):
        raise sqlite3.OperationalError("disk full")

    store.save_subchunk = failing  # type: ignore[method-assign]
    store.save_chunk = failing  # type: ignore[method-assign]
    doc = _doc("wf_all", 20)
    c1 = FakeClient(lambda u, n: _resp("ABSENT"))
    r1 = _run(_service(c1, store), doc, c1)
    d = r1.metadata["detection_diagnostics"]
    assert r1.metadata["status"] == "SUCCESS" and d["checkpoint_write_failures"] == 4
    assert d["checkpoint_persistence"] == "WRITE_FAILURES" and d["resume_guaranteed"] is False
    assert _counts(chk_dir / "checkpoints.db")["checkpoint_chunks"] == 0

    # fail-closed: nulla è stato persistito, quindi nulla viene dato per ripreso
    c2 = FakeClient(lambda u, n: _resp("ABSENT"))
    r2 = _run(_service(c2, AnalysisCheckpointStore(checkpoint_dir=chk_dir)), doc, c2)
    assert len(c2.calls) == 3 and r2.metadata["detection_diagnostics"]["checkpoint_persistence"] == "OK"


def test_parent_write_failure_is_counted_but_saved_microchunks_still_resume(tmp_path):
    chk_dir = tmp_path / "chk"
    store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)

    def failing_save_chunk(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    store.save_chunk = failing_save_chunk  # type: ignore[method-assign]
    doc = _doc("wf_par", 20)
    client = FakeClient(lambda u, n: _resp("ABSENT"))
    res = _run(_service(client, store), doc, client)
    d = res.metadata["detection_diagnostics"]
    assert res.metadata["status"] == "SUCCESS"
    assert d["checkpoint_write_failures"] == 1 and d["checkpoint_writes_ok"] == 3
    assert d["checkpoint_persistence"] == "WRITE_FAILURES" and d["resume_guaranteed"] is False

    c2 = FakeClient(lambda u, n: _resp("ABSENT"))
    r2 = _run(_service(c2, AnalysisCheckpointStore(checkpoint_dir=chk_dir)), doc, c2)
    assert c2.calls == [], "i 3 microchunk salvati permettono la ripresa senza nuove chiamate"
    assert r2.metadata["status"] == "SUCCESS"


def test_without_checkpoint_store_persistence_is_not_attempted(tmp_path):
    doc = _doc("wf_none", 20)
    client = FakeClient(lambda u, n: _resp("ABSENT"))
    d = _run(_service(client, None), doc, client).metadata["detection_diagnostics"]
    assert d["checkpoint_persistence"] == "NOT_ATTEMPTED" and d["checkpoint_write_failures"] == 0


# ----------------------------------------------------------------------------
# Problema 6: contabilità parent separata dai microchunk
# ----------------------------------------------------------------------------

def test_parent_total_stays_two_with_one_failed_microchunk(tmp_path):
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")
    doc = _doc("pa_a", 40)  # 2 parent da 20 messaggi
    client = FakeClient(_fail_on_message(25, "pa_a"))
    res = _run(_service(client, store, max_chunk_bundles=20), doc, client)
    m, d = res.metadata, res.metadata["detection_diagnostics"]
    assert m["status"] == "PARTIAL" and res.decision == TopicDecision.UNCERTAIN
    assert (m["chunk_count"], m["completed_chunk_count"], m["failed_chunk_count"]) == (2, 1, 1)
    assert (d["parent_chunks_total"], d["parent_chunks_completed"]) == (2, 1)
    assert m["microchunk_count"] > m["chunk_count"], "i microchunk non devono essere scambiati per parent"
    assert m["microchunk_failed_count"] >= 1 and m["microchunk_completed_count"] >= 5


def test_parent_and_microchunk_totals_on_full_success(tmp_path):
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")
    doc = _doc("pa_b", 40)
    client = FakeClient(lambda u, n: _resp("ABSENT"))
    m = _run(_service(client, store, max_chunk_bundles=20), doc, client).metadata
    assert (m["chunk_count"], m["completed_chunk_count"], m["failed_chunk_count"]) == (2, 2, 0)
    assert (m["microchunk_count"], m["microchunk_completed_count"], m["microchunk_failed_count"]) == (6, 6, 0)


def test_parent_totals_are_stable_when_resuming_a_partial_run(tmp_path):
    chk_dir = tmp_path / "chk"
    doc = _doc("pa_c", 40)
    c1 = FakeClient(_fail_on_message(25, "pa_c"))
    _run(_service(c1, AnalysisCheckpointStore(checkpoint_dir=chk_dir), max_chunk_bundles=20), doc, c1)
    c2 = FakeClient(lambda u, n: _resp("ABSENT"))
    m = _run(_service(c2, AnalysisCheckpointStore(checkpoint_dir=chk_dir), max_chunk_bundles=20), doc, c2).metadata
    assert m["status"] == "SUCCESS" and (m["chunk_count"], m["completed_chunk_count"], m["failed_chunk_count"]) == (2, 2, 0)
    assert m["detection_diagnostics"]["parent_chunks_total"] == 2


# ----------------------------------------------------------------------------
# Problema 3: budget finiti oltre il breaker dei timeout consecutivi
# ----------------------------------------------------------------------------
from ai.backend import AiBackendTimeoutError  # noqa: E402


def test_time_budget_stops_bisection_and_bounds_calls(monkeypatch, tmp_path):
    """Errore strutturale permanente: con budget esaurito niente bisezione (1 chiamata/microchunk, non fino a 15 nodi)."""
    import analysis.service as svc_mod
    from analysis.models import OperationalAnalysisFailedError

    doc = _doc("bd_a", 16)  # 2 microchunk da 8 messaggi

    def always_bad(u, n):
        raise AiStructuredOutputError("JSONDecodeError simulato")

    c_default = FakeClient(always_bad)
    with pytest.raises(OperationalAnalysisFailedError):  # nessun microchunk validato: fallimento esplicito, mai ABSENT
        _run(_service(c_default, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "a")), doc, c_default)
    assert 2 * 2 < len(c_default.calls) <= 2 * 15 * 2, "senza budget: bisezione limitata da profondità (<=15 nodi x 2 tentativi)"

    monkeypatch.setattr(svc_mod, "DETECTION_MICROCHUNK_TIME_BUDGET_SECONDS", 0.0)
    c_budget = FakeClient(always_bad)
    with pytest.raises(OperationalAnalysisFailedError):
        _run(_service(c_budget, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "b")), doc, c_budget)
    assert len(c_budget.calls) == 2 * 2, "budget esaurito: 1 nodo x 2 tentativi per microchunk, nessuna bisezione"


def test_total_timeout_budget_stops_mixed_pattern_that_resets_the_consecutive_breaker(monkeypatch, tmp_path):
    import analysis.service as svc_mod

    monkeypatch.setattr(svc_mod, "DETECTION_MAX_TOTAL_TIMEOUTS", 2)
    doc = _doc("bd_b", 48)  # 6 microchunk
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")

    def alternating(u, n):
        ids = _ids(u, "bd_b")
        # microchunk pari (0,16,32): successo; dispari (8,24,40): timeout -> il successo azzera i consecutivi
        if (ids[0] // 8) % 2 == 1:
            raise AiBackendTimeoutError("timeout simulato")
        return _resp("ABSENT")

    c1 = FakeClient(alternating)
    with pytest.raises(AiBackendTimeoutError):
        _run(_service(c1, store), doc, c1)
    seen = sorted({_ids(u, "bd_b")[0] // 8 for u in c1.calls})
    assert seen == [0, 1, 2, 3], "interruzione al secondo timeout complessivo, senza arrivare agli ultimi microchunk"

    # resume: i microchunk validati prima dell'arresto non sono re-inferiti
    c2 = FakeClient(lambda u, n: _resp("ABSENT"))
    r2 = _run(_service(c2, store), doc, c2)
    assert r2.metadata["status"] == "SUCCESS"
    reinferred = {_ids(u, "bd_b")[0] // 8 for u in c2.calls}
    assert reinferred == {1, 3, 4, 5} and not ({0, 2} & reinferred), "i microchunk già validati non sono re-inferiti"


def test_http_call_counters_reconcile_with_real_client_calls(tmp_path):
    """Invariante: chiamate reali del client = detect_topic_calls + retry applicativi = http_requests_sent."""
    doc = _doc("rc_a", 24)

    def flaky(u, n):
        if n == 2:
            raise AiStructuredOutputError("JSONDecodeError simulato")  # 1 retry interno, poi successo
        return _resp("ABSENT")

    client = FakeClient(flaky)
    res = _run(_service(client, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")), doc, client)
    d = res.metadata["detection_diagnostics"]
    assert res.metadata["status"] == "SUCCESS"
    assert d["retries"] == 1
    assert len(client.calls) == d["detect_topic_calls"] + d["retries"] == d["http_requests_sent"]


# ----------------------------------------------------------------------------
# Problema 7: copertura dei casi 40/60 messaggi sotto il nuovo regime di pre-splitting.
# Le due fixture storiche (40 -> 6, 60 -> 24 messaggi) restano valide per il percorso reattivo/non pre-diviso
# (parent <= 8 messaggi sostanziali); qui si riprovano gli STESSI invarianti con i volumi originali.
# ----------------------------------------------------------------------------
import json  # noqa: E402

from ai.backend import AiBackendRequestError, LlmCompletionResponse  # noqa: E402
from ai.models import TopicQuery  # noqa: E402
from analysis.checkpoint import compute_document_fingerprint, compute_run_config_hash  # noqa: E402
from analysis.models import TopicDetectionResult  # noqa: E402
from analysis.service import LocalTopicAnalysisService  # noqa: E402
from tests.unit.test_performance_accounting_audit import ControlledMockClient, make_test_document  # noqa: E402


def _ok(decision: str, eids=()):
    return LlmCompletionResponse(
        content=json.dumps({"decision": decision, "evidence_ids": list(eids), "rationale": "Esito sintetico."}),
        model="mock-model",
    )


def test_http_accounting_invariant_with_40_messages_overflow_inside_presplit_microchunk():
    """Stesso invariante del test storico (40 messaggi): requests_sent == successful + failed + in_flight."""
    doc = make_test_document(40)
    topic = TopicQuery(topic_id="top_1", label="Transazioni", description="Attività finanziarie")
    overflow = AiBackendRequestError(
        message="Context length exceeded (8300 > 8192)", status_code=400,
        error_category="CONTEXT_OVERFLOW", safe_reason="Richiesta supera il contesto disponibile",
    )
    # 5 microchunk da 8; il primo va in overflow e viene bisecato in 2 metà (che riescono): 1 + 2 + 4 = 7 chiamate
    client = ControlledMockClient([overflow, _ok("PRESENT", ["msg_0001::ORIGINAL_TEXT"]), _ok("ABSENT")] + [_ok("ABSENT")] * 4)
    events: list = []
    svc = LocalTopicAnalysisService(client=client, max_chunk_bundles=100, max_chunk_tokens=6000)
    res = svc.run_topic_detection(document=doc, topic=topic, progress_callback=events.append)

    assert client.call_count == 7
    f = events[-1]
    assert (f.requests_sent, f.requests_successful, f.requests_failed, f.requests_in_flight) == (7, 6, 1, 0)
    assert f.http_errors_count == 1 and f.is_requests_accounting_consistent is True
    assert f.is_complete is True
    assert res.decision == TopicDecision.PRESENT and res.metadata["status"] == "SUCCESS"


def test_resume_with_60_messages_skips_completed_parent_and_reinfers_only_remaining_microchunks():
    """Stesso scenario storico (60 messaggi, max_bundles=20, parent 0 già nel checkpoint)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        store = AnalysisCheckpointStore(checkpoint_dir=tmp_dir)
        doc = make_test_document(60)
        topic = TopicQuery(topic_id="top_1", label="Contratti", description="Accordi contrattuali")
        client = ControlledMockClient([_ok("ABSENT")] * 6)  # 2 parent rimasti x 3 microchunk (invece di 9)
        svc = LocalTopicAnalysisService(client=client, max_chunk_bundles=20, checkpoint_store=store, enable_checkpointing=True)

        doc_fp = compute_document_fingerprint(doc)
        plan = svc.create_analysis_plan(doc, "detection", topic_query=topic)
        chunks = list(plan.chunks)
        assert len(chunks) == 3
        cfg_fp = compute_run_config_hash(
            model_id=svc.default_model, prompt_version="detection_operational", config=svc.config,
            topic_query=topic, chunks=chunks,
            extra_params={"strategy": "DIRECT_MULTILINGUAL", "operational_mode": True, "loaded_instance_id": None,
                          "resolved_model_key": None, "quantization": None},
        )
        store.save_chunk(
            document_id=doc.document_id, document_fingerprint=doc_fp, config_fingerprint=cfg_fp, chunk_idx=0,
            result=TopicDetectionResult(
                topic=topic, decision=TopicDecision.PRESENT, evidence_ids=("msg_0001::ORIGINAL_TEXT",),
                rationale="Precedentemente completato.", provenance_document_id=doc.document_id,
            ),
            total_planned_chunks=3, analysis_type="detection", model_id=svc.default_model,
            prompt_version="detection_operational",
        )
        res = svc.run_topic_detection(document=doc, topic=topic)
        assert client.call_count == 6, "il parent 0 salvato non viene re-inferito: 2 parent x 3 microchunk"
        assert res.decision == TopicDecision.PRESENT
        m = res.metadata
        assert (m["chunk_count"], m["completed_chunk_count"], m["failed_chunk_count"]) == (3, 3, 0)
