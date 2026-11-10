"""
tests/unit/test_detection_ui_and_discovery_minor_fixes.py
---------------------------------------------------------
Regressioni mirate (dati sintetici, client finto):
- banner Discovery: COMPLETE_EMPTY_UNVERIFIED non è mai presentato come successo;
- callback UI di avanzamento: un errore di rendering non viene taciuto né fa perdere lo stato;
- denominatore dei microchunk della Discovery: `else 2` coincide con i sotto-blocchi realmente elaborati
  (verificato: NON è un difetto, nessuna modifica al codice).
"""
from __future__ import annotations

import json

import pytest

from ai.backend import LlmCompletionResponse
from ai.chunking import split_chunk_into_microchunks
from ai.topics import TopicDiscoveryAnalyzer
from analysis.progress import AnalysisProgressTracker
from tests.unit.test_detection_microchunk_checkpoint_resume import MODEL, FakeClient, _doc, _service


def test_empty_unverified_discovery_is_never_shown_as_success():
    pytest.importorskip("streamlit")
    from ui.presentation import discovery_completion_banner

    level, text = discovery_completion_banner("COMPLETE_EMPTY_UNVERIFIED", 26, 26, 0)
    assert level == "warning"
    assert "NON verificato" in text and "completa" not in text.lower().split(":")[0]

    assert discovery_completion_banner("COMPLETE", 26, 26, 0)[0] == "success"
    assert discovery_completion_banner("PARTIAL", 20, 26, 6)[0] == "warning"
    assert discovery_completion_banner("COMPLETE", 25, 26, 1)[0] == "warning"  # blocchi falliti => mai successo


def test_failing_ui_container_is_counted_by_tracker_not_silenced():
    pytest.importorskip("streamlit")
    from ui.presentation import create_streamlit_progress_callback

    class BrokenContainer:
        def container(self):
            raise RuntimeError("contenitore non disponibile: testo riservato")

    tracker = AnalysisProgressTracker(total_chunks=2, callback=create_streamlit_progress_callback(BrokenContainer()))
    tracker.begin_microchunks(1, 3)
    tracker.microchunk_done()
    # lo stato dell'analisi resta integro e l'errore è contabilizzato in forma sanificata
    assert tracker.microchunks_total == 3 and tracker.microchunks_completed == 1
    assert tracker.callback_errors_count == 2
    assert tracker.last_callback_error == "RuntimeError"


def test_discovery_planned_denominator_matches_processed_subchunks():
    """Piano di 1 microchunk con più bundle: la Discovery elabora 2 metà, come dichiara `else 2`."""
    doc = _doc("disc_den", 6)
    assert len(split_chunk_into_microchunks(doc, max_substantive_messages=8)) == 1
    assert len(doc.bundles) > 1

    client = FakeClient(
        lambda u, n: LlmCompletionResponse(
            content=json.dumps({"topics": [{"label": "Tema", "short_description": "Descrizione", "evidence_ids": ["E001"]}]}),
            model=MODEL,
            raw_response={"finish_reason": "stop"},
        )
    )
    svc = _service(client)
    analyzer = TopicDiscoveryAnalyzer(client=client, default_model_id=MODEL, operational_mode=True)
    outcomes, _results = svc._process_subchunks_discovery(
        chunk=doc,
        analyzer=analyzer,
        resolved_model=MODEL,
        eff_temp=0.0,
        eff_seed=42,
        chunk_target_topics=3,
        tracker=AnalysisProgressTracker(total_chunks=1),
        chunk_idx_1based=1,
    )
    planned = split_chunk_into_microchunks(doc, max_substantive_messages=8)
    total_planned_subs = len(planned) if len(planned) > 1 else 2  # espressione presente in run_topic_discovery
    assert len(outcomes) == total_planned_subs == 2


# ----------------------------------------------------------------------------
# Script diagnostici del runbook Windows: validati su fixture (nessun dato reale, nessun LM Studio)
# ----------------------------------------------------------------------------

def test_limited_detection_script_fake_mode_is_mock_verified(capsys, monkeypatch):
    monkeypatch.setattr("ai.topics.time.sleep", lambda s: None)
    from scripts.run_limited_detection_live import main

    assert main(["--fake", "--messages", "24"]) == 0
    out = capsys.readouterr().out
    assert "origine=MOCK_VERIFIED" in out and "LIVE_VERIFIED" not in out
    assert "decisione=PRESENT" in out and "status=SUCCESS" in out and "esito_tecnico=PASS" in out
    counters = json.loads(next(l for l in out.splitlines() if l.startswith("contatori=")).split("=", 1)[1])
    assert counters["microchunks_total"] == 3 and counters["detect_topic_calls"] == 3
    assert "Messaggio neutro" not in out and "corsa al parco" not in out, "nessun testo di conversazione nell'output"


def test_status_script_is_read_only_and_leaks_no_text(tmp_path, capsys):
    import hashlib
    from scripts.detection_microchunk_status import collect_status, main
    from tests.unit.test_detection_microchunk_checkpoint_resume import _doc, _fail_on_message, _run, _service

    store_dir = tmp_path / "chk"
    from analysis.checkpoint import AnalysisCheckpointStore
    store = AnalysisCheckpointStore(checkpoint_dir=store_dir)
    doc = _doc("stat_doc", 20)
    c = FakeClient(_fail_on_message(9, "stat_doc"))
    _run(_service(c, store), doc, c)  # run parziale: 2 microchunk su 3 salvati, nessun parent completato

    db = store_dir / "checkpoints.db"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    st = collect_status(db)
    assert len(st) == 1
    s = st[0]
    assert s["parent_chunks_completed"] == 0 and s["parent_chunks_planned"] == 1
    assert s["microchunks_saved_total"] == 2 and s["parents_with_partial_microchunks"] == 1
    assert s["per_parent"] == [{"parent_chunk_index": 0, "microchunks_saved": 2, "microchunks_planned": 3}]
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before, "il database non deve essere modificato"

    assert main(["--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "Messaggio sintetico" not in out and "stat_doc" not in out and "ORIGINAL_TEXT" not in out


def test_limited_script_exit_zero_requires_technical_success_not_just_execution(capsys, monkeypatch):
    """exit 0 NON basta a provare SUCCESS: un microchunk fallito deve dare exit 1 e esito_tecnico=FAIL."""
    monkeypatch.setattr("ai.topics.time.sleep", lambda s: None)
    import scripts.run_limited_detection_live as mod
    from ai.backend import AiStructuredOutputError

    class FailingFake(mod._FakeClient):
        def chat_completion(self, messages, model_id=None, schema=None, temperature=0.0, seed=42,
                            timeout_seconds=60.0, max_tokens=None):
            user = next((m["content"] for m in messages if m.get("role") == "user"), "")
            if "messaggio 10" in user or "numero 10 " in user or "numero 11 " in user:
                raise AiStructuredOutputError("JSONDecodeError simulato")
            return super().chat_completion(messages, model_id, schema, temperature, seed, timeout_seconds, max_tokens)

    monkeypatch.setattr(mod, "_FakeClient", FailingFake)
    assert mod.main(["--fake", "--messages", "24"]) == 1
    out = capsys.readouterr().out
    assert "esito_tecnico=FAIL" in out and "esito_tecnico=PASS" not in out
    assert "status=PARTIAL" in out
    assert "origine=MOCK_VERIFIED" in out and "LIVE_VERIFIED" not in out


def test_limited_script_without_lmstudio_is_not_verified_exit_2(capsys, monkeypatch):
    import scripts.run_limited_detection_live as mod
    import ai.lmstudio as lm

    class Down:
        def __init__(self, *a, **k): ...
        def is_available(self, *a, **k): return False

    monkeypatch.setattr(lm, "LmStudioClient", Down)
    assert mod.main(["--messages", "24"]) == 2
    assert "NOT_VERIFIED" in capsys.readouterr().out


def test_evaluate_acceptance_rejects_foreign_evidence_and_write_failures():
    import scripts.run_limited_detection_live as mod
    from types import SimpleNamespace

    doc = mod.build_synthetic_document(8)
    good_d = {"parent_chunks_total": 1, "parent_chunks_completed": 1, "microchunks_total": 1, "microchunks_completed": 1,
              "microchunks_failed": 0, "checkpoint_persistence": "OK", "checkpoint_write_failures": 0}
    ok = SimpleNamespace(metadata={"status": "SUCCESS", "detection_diagnostics": good_d}, evidence_ids=())
    assert mod.evaluate_acceptance(ok, doc, chat_calls=1) == []
    bad = SimpleNamespace(
        metadata={"status": "SUCCESS", "detection_diagnostics": dict(good_d, checkpoint_persistence="WRITE_FAILURES", checkpoint_write_failures=1)},
        evidence_ids=("inesistente::ORIGINAL_TEXT",),
    )
    reasons = mod.evaluate_acceptance(bad, doc, chat_calls=1)
    assert any("persistenza" in r for r in reasons) and any("evidenze" in r for r in reasons)
    assert mod.evaluate_acceptance(ok, doc, chat_calls=0) != [], "0 chiamate con 1 microchunk non è plausibile"


def test_snapshot_uses_backup_api_verifies_integrity_and_never_touches_original(tmp_path, capsys):
    import hashlib
    import sqlite3
    from analysis.checkpoint import AnalysisCheckpointStore
    from scripts.detection_microchunk_status import main, snapshot_db
    from tests.unit.test_detection_microchunk_checkpoint_resume import _fail_on_message, _run

    store_dir = tmp_path / "chk"
    store = AnalysisCheckpointStore(checkpoint_dir=store_dir)
    doc = _doc("snap_doc", 20)
    c = FakeClient(_fail_on_message(9, "snap_doc"))
    _run(_service(c, store), doc, c)
    db = store_dir / "checkpoints.db"

    # un'altra connessione tiene aperta una transazione di scrittura: lo snapshot resta consistente
    writer = sqlite3.connect(str(db))
    writer.execute("BEGIN IMMEDIATE")
    try:
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        snap = snapshot_db(db, tmp_path / "snap")
        assert hashlib.sha256(db.read_bytes()).hexdigest() == before, "l'originale non deve cambiare"
    finally:
        writer.rollback()
        writer.close()
    assert snap.name == "checkpoints_snapshot.db" and snap.parent == (tmp_path / "snap").resolve()
    chk = sqlite3.connect(str(snap))
    assert chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert chk.execute("SELECT COUNT(*) FROM checkpoint_subchunks").fetchone()[0] == 2
    chk.close()

    # CLI: snapshot + stato dallo snapshot; secondo snapshot nella stessa cartella è rifiutato (nessuna sovrascrittura)
    assert main(["--db", str(db), "--snapshot-dir", str(tmp_path / "snap2")]) == 0
    assert "microchunk salvati 2" in capsys.readouterr().out
    assert main(["--db", str(db), "--snapshot-dir", str(tmp_path / "snap2")]) == 2
