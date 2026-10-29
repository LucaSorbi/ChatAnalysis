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
    assert "esito=MOCK_VERIFIED" in out and "LIVE_VERIFIED" not in out
    assert "decisione=PRESENT" in out and "stato=SUCCESS" in out
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
