"""
tests/unit/test_discovery_progress_unification.py
-------------------------------------------------
Unificazione del progresso Topic Discovery / Topic Detection (parte A del prompt 30/10/2026).

Dati SINTETICI, client finto che riporta le richieste al tracker come fa il vero client LM Studio
(`request_listener`): esito massimo MOCK VERIFIED. Nessuna inferenza reale, nessun database reale.
Il rendering Streamlit è provato con un recorder al posto di `st` (NON è un AppTest con streamlit vero).
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Mapping, Sequence

import pytest

from ai.backend import (
    AiStructuredOutputError,
    BaseLocalLlmClient,
    LlmCompletionResponse,
)
from analysis.checkpoint import AnalysisCheckpointStore
from analysis.progress import AnalysisProgressEvent, AnalysisProgressTracker
from tests.unit.test_detection_microchunk_checkpoint_resume import (
    MODEL,
    TOPIC,
    _doc,
    _resp,
    _service,
)

pytest.importorskip("ui.progress_view")
from ui.progress_view import (  # noqa: E402
    STATUS_FAILED,
    STATUS_NO_TOPICS,
    STATUS_PARTIAL,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    STATUS_SUCCESS_UNVERIFIED,
    build_progress_view,
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ai.topics.time.sleep", lambda s: None)


# ----------------------------------------------------------------------------
# Client finto che riporta le richieste al listener, come il vero client
# ----------------------------------------------------------------------------

def _topics_resp(n_topics: int = 1) -> LlmCompletionResponse:
    topics = [
        {"label": f"Tema {i}", "short_description": f"Descrizione del tema {i}", "evidence_ids": ["E001"]}
        for i in range(n_topics)
    ]
    return LlmCompletionResponse(
        content=json.dumps({"topics": topics}), model=MODEL, raw_response={"finish_reason": "stop"}
    )


class ReportingClient(BaseLocalLlmClient):
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
        ln = self.request_listener
        if ln:
            ln.record_request_sent()
        try:
            out = self.handler(user, len(self.calls))
        except AiStructuredOutputError as exc:
            if ln:
                ln.record_protocol_error(type(exc).__name__)
            raise
        if ln:
            ln.record_request_success()
        return out


def _msg_ids(user: str, doc_id: str) -> list[int]:
    return sorted({int(m) for m in re.findall(rf"msg_{re.escape(doc_id)}_(\d+)", user)})


def _discover(svc, doc, client):
    events: list[AnalysisProgressEvent] = []
    res = svc.run_topic_discovery(document=doc, client=client, model_name=MODEL, progress_callback=events.append)
    return res, events


# ----------------------------------------------------------------------------
# 1. 26 parent: il denominatore resta 26 mentre i microchunk aumentano
# ----------------------------------------------------------------------------

def test_26_parents_keep_total_stable_while_microchunks_grow(tmp_path):
    doc = _doc("p26", 520)  # 26 parent da 20 messaggi -> 3 microchunk ciascuno
    client = ReportingClient(lambda u, n: _topics_resp())
    svc = _service(client, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk"), max_chunk_bundles=20)
    res, events = _discover(svc, doc, client)

    assert {e.total_chunks for e in events} == {26}, "il totale dei blocchi principali non deve mai cambiare"
    totals = [e.microchunks_total for e in events]
    assert totals == sorted(totals), "il totale microchunk è dinamico ma non decresce"
    assert totals[-1] == 78 and max(totals) > 3, "cresce con la pianificazione dei blocchi principali"
    last = events[-1]
    assert last.is_complete and last.completed_chunks == 26 and last.failed_chunks == 0
    assert (last.microchunks_completed, last.microchunks_failed, last.microchunks_recovered) == (78, 0, 0)
    assert all(e.is_requests_accounting_consistent for e in events)
    assert last.requests_in_flight == 0 and last.requests_sent == last.requests_successful == len(client.calls) == 78
    # vista: "Blocco principale corrente" in numerazione da 1 e sempre entro 26
    for e in events:
        v = build_progress_view(e)
        if v.current_line:
            cur = int(v.current_line.split(":")[1].split("/")[0])
            assert 1 <= cur <= 26
    assert res.metadata.get("status", "SUCCESS") != "FAILED"


def test_main_progress_bar_is_not_full_before_final_event(tmp_path):
    doc = _doc("pbar", 40)
    client = ReportingClient(lambda u, n: _topics_resp())
    svc = _service(client, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk"), max_chunk_bundles=20)
    _, events = _discover(svc, doc, client)
    views = [build_progress_view(e) for e in events]
    assert all(v.parent_fraction < 1.0 for v in views[:-1] if v.status == STATUS_RUNNING)
    assert views[-1].status == STATUS_SUCCESS and views[-1].parent_fraction == 1.0


# ----------------------------------------------------------------------------
# 2. Piano da 5 microchunk: 1 recuperato, 3 validati, 1 fallito
# ----------------------------------------------------------------------------

def test_five_microchunks_one_recovered_three_validated_one_failed(tmp_path):
    chk = tmp_path / "chk"
    doc = _doc("p5", 40)  # un solo parent, 5 microchunk da 8

    # run 1: solo il microchunk 0 riesce (gli altri falliscono) -> viene salvato come sub-checkpoint
    def only_first(u, n):
        if 0 in _msg_ids(u, "p5"):
            return _topics_resp()
        raise AiStructuredOutputError("JSONDecodeError simulato")

    c1 = ReportingClient(only_first)
    _discover(_service(c1, AnalysisCheckpointStore(checkpoint_dir=chk)), doc, c1)

    # run 2: il microchunk 4 (messaggi 32-39) fallisce, 1..3 riescono, 0 è recuperato
    def last_fails(u, n):
        if 39 in _msg_ids(u, "p5"):
            raise AiStructuredOutputError("JSONDecodeError simulato")
        return _topics_resp()

    c2 = ReportingClient(last_fails)
    res, events = _discover(_service(c2, AnalysisCheckpointStore(checkpoint_dir=chk)), doc, c2)
    last = events[-1]
    assert last.microchunks_total == 5
    assert (last.microchunks_completed, last.microchunks_recovered, last.microchunks_failed) == (4, 1, 1)
    assert last.microchunks_completed + last.microchunks_failed <= last.microchunks_total
    assert last.completed_chunks == 0 and last.failed_chunks == 1, "il parent NON è SUCCESS con un microchunk non validato"
    assert last.is_partial and not last.is_complete
    assert 0 not in _msg_ids(" ".join(c2.calls), "p5"), "il microchunk recuperato non fa nuove richieste"
    view = build_progress_view(last)
    assert view.status == STATUS_PARTIAL and view.parent_fraction < 1.0
    assert last.is_requests_accounting_consistent and last.requests_in_flight == 0


# ----------------------------------------------------------------------------
# 3. Bisezione e retry: contatori corretti, nessun doppio conteggio
# ----------------------------------------------------------------------------

def test_bisection_and_retry_are_counted_once_and_do_not_create_units(tmp_path):
    doc = _doc("pbis", 16)  # 2 microchunk da 8

    def bad_when_full(u, n):
        ids = _msg_ids(u, "pbis")
        if len(ids) == 8 and 0 in ids:  # il primo microchunk intero fallisce; le metà da 4 riescono
            raise AiStructuredOutputError("JSONDecodeError simulato")
        return _topics_resp()

    client = ReportingClient(bad_when_full)
    svc = _service(client, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk"))
    _, events = _discover(svc, doc, client)
    last = events[-1]
    assert last.is_complete and last.failed_chunks == 0
    assert last.microchunks_total == 2, "la bisezione NON crea unità: il totale resta quello pianificato"
    assert (last.microchunks_completed, last.microchunks_failed) == (2, 0)
    assert last.bisections_count == 1
    assert last.retries_count >= 1, "il tentativo corretto dopo l'errore è contato come retry"
    # nessun conteggio doppio delle richieste: tutto ciò che il client ha ricevuto è inviato una sola volta
    assert last.requests_sent == len(client.calls)
    assert last.requests_sent == last.requests_successful + last.requests_failed + last.requests_in_flight
    assert last.requests_in_flight == 0, "nessuna richiesta 'in corso' fantasma dopo i retry"
    assert last.requests_failed >= 2 and last.protocol_errors_count >= 2
    assert last.microchunks_completed + last.microchunks_failed <= last.microchunks_total


# ----------------------------------------------------------------------------
# 4. Recupero 100% da checkpoint: zero nuove richieste HTTP
# ----------------------------------------------------------------------------

def test_full_recovery_from_microchunk_checkpoints_makes_no_http_requests(tmp_path):
    chk = tmp_path / "chk"
    doc = _doc("prec", 24)  # 3 microchunk
    c1 = ReportingClient(lambda u, n: _topics_resp())
    _discover(_service(c1, AnalysisCheckpointStore(checkpoint_dir=chk)), doc, c1)
    # il parent consolidato viene rimosso dal DB di TEST: restano solo i 3 sub-checkpoint
    conn = sqlite3.connect(str(chk / "checkpoints.db"))
    conn.execute("DELETE FROM checkpoint_chunks")
    conn.commit()
    conn.close()

    c2 = ReportingClient(lambda u, n: (_ for _ in ()).throw(AssertionError("nessuna richiesta attesa")))
    _, events = _discover(_service(c2, AnalysisCheckpointStore(checkpoint_dir=chk)), doc, c2)
    last = events[-1]
    assert c2.calls == [] and last.requests_sent == 0
    assert (last.microchunks_total, last.microchunks_completed, last.microchunks_recovered, last.microchunks_failed) == (3, 3, 3, 0)
    assert last.is_complete and last.completed_chunks == last.total_chunks == 1
    v = build_progress_view(last)
    assert "Recuperati da checkpoint: 3" in v.micro_detail_line


# ----------------------------------------------------------------------------
# 5. Zero topic: nessun successo scientifico
# ----------------------------------------------------------------------------

def test_zero_topics_is_never_presented_as_scientific_success(tmp_path):
    doc = _doc("pzero", 24)
    client = ReportingClient(lambda u, n: LlmCompletionResponse(
        content=json.dumps({"topics": []}), model=MODEL, raw_response={"finish_reason": "stop"}))
    svc = _service(client, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk"))
    res, events = _discover(svc, doc, client)
    last = events[-1]
    assert last.is_complete and last.metadata.get("thematic_unverified") is True
    v = build_progress_view(last)
    assert v.status == STATUS_SUCCESS_UNVERIFIED
    assert "🟩" not in v.badge and any("NON è verificato" in w for w in v.warnings)


# ----------------------------------------------------------------------------
# 6. Rendering: callback con recorder al posto di streamlit + regressione Detection
# ----------------------------------------------------------------------------

class _Rec:
    def __init__(self) -> None:
        self.out: list[tuple[str, str]] = []

    def container(self):  # context manager e contenitore
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def markdown(self, t, *a, **k): self.out.append(("markdown", str(t)))
    def caption(self, t, *a, **k): self.out.append(("caption", str(t)))
    def warning(self, t, *a, **k): self.out.append(("warning", str(t)))
    def info(self, t, *a, **k): self.out.append(("info", str(t)))
    def progress(self, v, text=None, *a, **k): self.out.append(("progress", f"{v:.3f}|{text}"))


def test_streamlit_callback_renders_same_panel_for_discovery_and_detection(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    import ui.presentation as pres

    rec = _Rec()
    monkeypatch.setattr(pres, "st", rec)
    cb = pres.create_streamlit_progress_callback(rec, title="TOPIC DISCOVERY IN CORSO")

    doc = _doc("prend", 40)
    client = ReportingClient(lambda u, n: _topics_resp())
    svc = _service(client, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk"), max_chunk_bundles=20)
    svc.run_topic_discovery(document=doc, client=client, model_name=MODEL, progress_callback=cb)
    text = "\n".join(v for _, v in rec.out)
    for needle in ("Blocchi principali:", "Microchunk completati:", "totale dinamico", "Richieste LLM: inviate", "Tempo trascorso"):
        assert needle in text, needle

    rec.out.clear()
    dclient = ReportingClient(lambda u, n: _resp("ABSENT"))
    dsvc = _service(dclient, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk2"), max_chunk_bundles=20)
    dsvc.run_topic_detection(document=doc, topic=TOPIC, client=dclient, model_name=MODEL, progress_callback=pres.create_streamlit_progress_callback(rec, title="TOPIC DETECTION IN CORSO"))
    dtext = "\n".join(v for _, v in rec.out)
    for needle in ("Blocchi principali:", "Microchunk completati:", "Richieste LLM: inviate"):
        assert needle in dtext, needle


# ----------------------------------------------------------------------------
# 7. Stati finali PARTIAL / FAILED: barra e badge veritieri
# ----------------------------------------------------------------------------

def test_final_partial_and_failed_views_are_truthful():
    t = AnalysisProgressTracker(total_chunks=26)
    for i in range(1, 14):
        t.begin_microchunks(i, 3)
        for _ in range(3):
            t.microchunk_done()
        t.end_microchunks()
        t.chunk_success(i, record_request=False)
    t.begin_microchunks(14, 3)
    t.microchunk_done(); t.microchunk_failed(); t.microchunk_failed()
    t.end_microchunks()
    t.chunk_failed(14, "micro-chunk non validati", record_request=False)
    ev = t.partial("parziale")
    v = build_progress_view(ev)
    assert v.status == STATUS_PARTIAL and "🟩" not in v.badge
    assert v.parent_fraction == pytest.approx(13 / 26) and v.parent_fraction < 1.0
    assert "13 / 26" in v.parent_line and "non validati: 1" in v.parent_line
    assert ev.percentage < 100.0

    t2 = AnalysisProgressTracker(total_chunks=4)
    t2.begin_microchunks(1, 2); t2.microchunk_failed(); t2.microchunk_failed(); t2.end_microchunks()
    t2.chunk_failed(1, "micro-chunk non validati", record_request=False)
    ev2 = t2.fail("Tutti i blocchi falliti")
    v2 = build_progress_view(ev2)
    assert v2.status == STATUS_FAILED and v2.parent_fraction == 0.0 and "🟥" in v2.badge


# ----------------------------------------------------------------------------
# 8. Compatibilità di tracker ed eventi esistenti
# ----------------------------------------------------------------------------

def test_event_and_tracker_remain_backward_compatible():
    ev = AnalysisProgressEvent(
        phase="x", total_chunks=3, completed_chunks=1, current_chunk_index=2, remaining_chunks=2,
        requests_sent=2, requests_successful=1, retries_count=0, requests_failed=0, elapsed_seconds=1.0, status_message="m",
        requests_in_flight=1,
    )
    assert ev.microchunks_total == 0 and ev.current_micro_total == 0 and ev.metadata == {}
    d = ev.to_dict()
    assert d["current_micro_total"] == 0 and "microchunks_total" in d
    assert build_progress_view(ev).micro_line is None, "senza microchunk la riga non compare"

    t = AnalysisProgressTracker(total_chunks=2)
    t.split_chunk(1, subchunks_count=3, failed_prior_request=False)  # comportamento storico: il totale cresce
    assert t.total_chunks == 4
    t2 = AnalysisProgressTracker(total_chunks=2)
    t2.split_chunk(1, subchunks_count=3, failed_prior_request=False, grow_total=False)
    assert t2.total_chunks == 2


def test_view_never_shows_100_percent_for_incomplete_and_handles_unknown_current_block():
    t = AnalysisProgressTracker(total_chunks=3)
    for i in (1, 2, 3):
        t.chunk_success(i, record_request=False)  # tutti i parent validati ma evento finale non ancora emesso
    ev = t._emit("consolidamento")
    v = build_progress_view(ev)
    assert v.status == STATUS_RUNNING and v.parent_fraction < 1.0


# ============================================================================
# Revisione P0 del 30/10/2026: semantica dei contatori
# ============================================================================
import ast
import io
import socket
import urllib.error
from pathlib import Path

from ai.chunking import split_chunk_into_microchunks, split_document_into_chunks
from ai.lmstudio import LmStudioClient
from analysis.models import AnalysisNoEvidenceError  # noqa: E402


def _discovery_split_calls() -> list[ast.Call]:
    tree = ast.parse((Path(__file__).resolve().parent.parent.parent / "analysis" / "service.py").read_text(encoding="utf-8"))
    out: list[ast.Call] = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == "run_topic_discovery":
            out += [c for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "split_chunk"]
    return out


def test_every_discovery_split_chunk_callsite_keeps_parent_total_fixed():
    """STATICALLY_VERIFIED: nessun callsite di split_chunk in run_topic_discovery fa crescere total_chunks."""
    calls = _discovery_split_calls()
    assert len(calls) >= 2
    for c in calls:
        kw = {k.arg: k.value for k in c.keywords}
        assert "grow_total" in kw and isinstance(kw["grow_total"], ast.Constant) and kw["grow_total"].value is False
        # il messaggio mostrato all'utente riporta solo il nome della classe dell'errore, mai il testo
        assert "str(" not in ast.unparse(kw["prior_error_msg"])


def test_reactive_single_parent_split_keeps_one_parent_and_completes_it(tmp_path):
    doc = _doc("rx1", 8)  # <=8 messaggi sostanziali: percorso reattivo (nessun pre-split)

    def handler(u, n):
        if len(_msg_ids(u, "rx1")) == 8:
            raise AiStructuredOutputError("JSONDecodeError simulato")
        return _topics_resp()

    client = ReportingClient(handler)
    res, events = _discover(_service(client, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")), doc, client)
    last = events[-1]
    assert {e.total_chunks for e in events} == {1}, "il totale dei parent non cresce con la suddivisione reattiva"
    assert last.is_complete and (last.completed_chunks, last.failed_chunks) == (1, 0)
    assert res.metadata["status"] == "COMPLETE", "stato del servizio e stato del pannello devono coincidere"
    assert last.microchunks_total == last.microchunks_completed == 2
    assert last.requests_sent == len(client.calls) == last.requests_successful + last.requests_failed
    # la chiamata sul parent intero fallisce e viene ritentata una volta (2 chiamate fallite), poi 2 sub-chunk riusciti
    assert len(client.calls) == 4 and last.requests_failed == 2 and last.protocol_errors_count == 2
    assert last.requests_successful == 2 and last.requests_in_flight == 0 and last.retries_count >= 1


def test_reactive_split_inside_26_parent_run_never_renumbers_or_grows(tmp_path):
    doc = _doc("rx26", 208)  # 26 parent da 8 messaggi (nessun pre-split)
    boom_parent = {"hit": False}

    def handler(u, n):
        ids = _msg_ids(u, "rx26")
        if not boom_parent["hit"] and len(ids) == 8 and ids[0] == 8 * 12:  # il 13° parent fallisce alla prima chiamata
            boom_parent["hit"] = True
            raise AiStructuredOutputError("JSONDecodeError simulato")
        return _topics_resp()

    client = ReportingClient(handler)
    _, events = _discover(_service(client, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk"), max_chunk_bundles=8), doc, client)
    assert boom_parent["hit"]
    assert {e.total_chunks for e in events} == {26}
    cur = [e.current_chunk_index for e in events if e.current_chunk_index]
    assert max(cur) <= 26 and min(cur) >= 1
    assert cur == sorted(cur), "i blocchi originali non vengono rinumerati"
    last = events[-1]
    assert last.is_complete and last.completed_chunks == 26 and last.failed_chunks == 0


def test_microchunk_bar_counts_only_validated_units_four_validated_one_failed_is_partial():
    t = AnalysisProgressTracker(total_chunks=1)
    t.begin_microchunks(1, 5)
    for _ in range(4):
        t.microchunk_done()
    t.microchunk_failed()
    t.end_microchunks()
    t.chunk_failed(1, "micro-chunk non validati", record_request=False)
    ev = t.partial("parziale")
    v = build_progress_view(ev)
    assert v.status == STATUS_PARTIAL
    assert v.micro_fraction == pytest.approx(0.8) and v.micro_fraction < 1.0, "il fallimento non porta la barra al 100%"
    assert v.micro_processed_fraction == pytest.approx(1.0)
    assert "validate 4, fallite 1" in v.micro_processed_line and v.micro_processed_line.startswith("Unità processate: 5 / 5")
    assert v.micro_line.startswith("Microchunk completati: 4 / 5")
    # 5 fallimenti su 5: barra a zero, stato FAILED
    t2 = AnalysisProgressTracker(total_chunks=1)
    t2.begin_microchunks(1, 5)
    for _ in range(5):
        t2.microchunk_failed()
    t2.chunk_failed(1, "x", record_request=False)
    v2 = build_progress_view(t2.fail("tutti falliti"))
    assert v2.micro_fraction == 0.0 and v2.status == STATUS_FAILED


def test_missing_transport_data_is_shown_as_unavailable_not_zero():
    t = AnalysisProgressTracker(total_chunks=1)  # nessun client collegato come listener: nessun dato di trasporto
    t.begin_microchunks(1, 2)
    t.microchunk_done()
    ev = t.microchunk_done()
    v = build_progress_view(ev)
    assert v.transport_available is False
    assert "non disponibile" in v.requests_line and "inviate 0" not in v.requests_line
    # microchunk interamente recuperati: zero richieste è un dato vero, non "non disponibile"
    t2 = AnalysisProgressTracker(total_chunks=1)
    t2.begin_microchunks(1, 2)
    t2.microchunk_done(recovered=True)
    v2 = build_progress_view(t2.microchunk_done(recovered=True))
    assert v2.transport_available is True and "inviate 0" in v2.requests_line


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._b = body

    def read(self) -> bytes:
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok_body() -> bytes:
    return json.dumps({
        "model": MODEL,
        "choices": [{"message": {"content": "{\"topics\": []}"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }).encode()


@pytest.mark.parametrize(
    "scenario",
    ["ok", "http500", "timeout", "connection", "malformed_json"],
)
def test_real_client_closes_every_sent_request_exactly_once(monkeypatch, scenario):
    """TEST_VERIFIED sul client reale (urlopen sostituito, nessuna rete): ogni richiesta inviata ha UN solo esito."""
    def fake_urlopen(req, timeout=None):
        if scenario == "ok":
            return _Resp(_ok_body())
        if scenario == "http500":
            raise urllib.error.HTTPError("http://x", 500, "err", {}, io.BytesIO(b"{}"))
        if scenario == "timeout":
            raise socket.timeout("timed out")
        if scenario == "connection":
            raise urllib.error.URLError(ConnectionRefusedError("rifiutata"))
        return _Resp(b"non e' json")

    monkeypatch.setattr("ai.lmstudio.urllib.request.urlopen", fake_urlopen)
    tracker = AnalysisProgressTracker(total_chunks=1)
    client = LmStudioClient(base_url="http://127.0.0.1:1234", model_id=MODEL)
    client.request_listener = tracker
    try:
        client.chat_completion([{"role": "user", "content": "x"}], model_id=MODEL, timeout_seconds=1.0)
    except Exception:  # noqa: BLE001
        assert scenario != "ok"
    ev = tracker._emit("fine")
    assert ev.requests_sent == 1 and ev.requests_in_flight == 0
    assert ev.requests_sent == ev.requests_successful + ev.requests_failed + ev.requests_in_flight
    assert ev.requests_successful == (1 if scenario == "ok" else 0)
    assert ev.timeouts_count == (1 if scenario == "timeout" else 0)
    assert ev.http_errors_count == (1 if scenario in ("http500", "connection") else 0)
    assert ev.protocol_errors_count == (1 if scenario == "malformed_json" else 0)


@pytest.mark.parametrize("n,expected", [(9, "COMPLETE_NO_TOPICS"), (10, "COMPLETE_EMPTY_UNVERIFIED"), (11, "COMPLETE_EMPTY_UNVERIFIED"),
                                        (5, "COMPLETE_NO_TOPICS"), (8, "COMPLETE_NO_TOPICS")])
def test_zero_topic_states_for_evidence_counts_around_the_threshold(tmp_path, n, expected):
    doc = _doc(f"zt{n}", n)
    client = ReportingClient(lambda u, k: LlmCompletionResponse(content=json.dumps({"topics": []}), model=MODEL, raw_response={"finish_reason": "stop"}))
    res, events = _discover(_service(client, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")), doc, client)
    last = events[-1]
    assert res.metadata["total_evidence_count"] == n, "denominatore = sezioni di evidenza del documento"
    assert res.metadata["status"] == expected
    v = build_progress_view(last)
    assert v.status == (STATUS_SUCCESS_UNVERIFIED if expected == "COMPLETE_EMPTY_UNVERIFIED" else STATUS_NO_TOPICS)
    assert "🟩" not in v.badge, "nessun banner verde per zero topic"
    assert v.parent_fraction == 1.0 and last.is_complete  # tecnicamente concluso, ma non "successo scientifico"


def test_zero_evidence_document_is_rejected_not_reported_as_complete(tmp_path):
    doc = _doc("zt0", 0)
    client = ReportingClient(lambda u, k: _topics_resp())
    with pytest.raises(AnalysisNoEvidenceError):
        _service(client, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")).run_topic_discovery(
            document=doc, client=client, model_name=MODEL)
    assert client.calls == []


def test_ten_plus_evidences_with_a_failed_microchunk_is_partial_never_unverified_empty(tmp_path):
    doc = _doc("zneg", 16)  # 2 microchunk da 8; il secondo non viene mai validato, il primo da' zero topic

    def handler(u, n):
        if 15 in _msg_ids(u, "zneg"):
            raise AiStructuredOutputError("JSONDecodeError simulato")
        return LlmCompletionResponse(content=json.dumps({"topics": []}), model=MODEL, raw_response={"finish_reason": "stop"})

    client = ReportingClient(handler)
    res, events = _discover(_service(client, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")), doc, client)
    last = events[-1]
    assert res.metadata["status"] == "PARTIAL" and last.is_partial and not last.is_complete
    v = build_progress_view(last)
    assert v.status == STATUS_PARTIAL and v.parent_fraction < 1.0
    assert "🟩" not in v.badge and not last.metadata.get("thematic_unverified")


def test_bisection_keeps_four_planned_units_formal_and_legacy_scenario_is_documented(tmp_path):
    """
    Dimostrazione dell'unità di lavoro: il totale dei microchunk e' il numero PIANIFICATO di primo livello
    (split_chunk_into_microchunks), qui 32 messaggi / 8 = 4. Una bisezione sostituisce un'unita' con due meta'
    che appartengono alla STESSA unita': non puo' produrre una quinta unita'.
    Scenario storico (modello precedente): begin(4) + done + done(recovered) + bisected + failed dava totale 5
    con soli 3 esiti terminali, cioe' un'unita' che non poteva mai concludersi (2 + 1 su 5 ≠ 4 pianificati).
    """
    doc = _doc("bis4", 32)
    plan = split_chunk_into_microchunks(split_document_into_chunks(doc, max_bundles=40)[0], max_substantive_messages=8)
    assert len(plan) == 4

    def handler(u, n):
        ids = _msg_ids(u, "bis4")
        if len(ids) == 8 and 0 in ids:
            raise AiStructuredOutputError("JSONDecodeError simulato")  # forza la bisezione del primo microchunk
        return _topics_resp()

    client = ReportingClient(handler)
    _, events = _discover(_service(client, AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk"), max_chunk_bundles=40), doc, client)
    last = events[-1]
    assert max(e.microchunks_total for e in events) == len(plan) == 4
    assert last.bisections_count >= 1 and (last.microchunks_completed, last.microchunks_failed) == (4, 0)
    # scenario storico riprodotto sul solo tracker (nuova regola): resta 4, e 2+1 esiti terminali su 4 pianificati
    t = AnalysisProgressTracker(total_chunks=2)
    t.begin_microchunks(1, 4)
    t.microchunk_done(); t.microchunk_done(recovered=True); t.microchunk_bisected(); t.microchunk_failed()
    assert t.microchunks_total == 4 != 5
    assert t.microchunks_completed + t.microchunks_failed == 3 < t.microchunks_total, "resta un'unita' ancora da concludere"
