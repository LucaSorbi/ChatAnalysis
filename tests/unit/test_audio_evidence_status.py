"""
tests/unit/test_audio_evidence_status.py
----------------------------------------
Stato veritiero dei messaggi vocali (.opus) in UI/export: AUDIO_NOT_FOUND, STT_NOT_RUN, STT_UNAVAILABLE,
TRANSCRIPTION_FAILED, TRANSCRIBED(_EMPTY).

SOLO dati sintetici e motori STT finti (esito massimo: MOCK). Nessun audio reale, nessun faster-whisper,
nessuna rete. Il "file audio" negli ZIP è un finto header OGG/Opus: il motore finto non lo decodifica.
"""
from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest

from analysis.checkpoint import compute_document_fingerprint
from analysis.export import build_topic_detection_export_report, serialize_report_to_json
from multimodal.asset_access import MediaAssetRegistry
from multimodal.audio_status import (
    AudioEvidenceState,
    classify_audio_bundle,
    format_audio_status_message,
    summarize_audio_evidence,
)
from multimodal.models import AudioTranscriptSegment, ResolvedMediaAsset
from multimodal.transcriber import (
    AudioModelLoadError,
    BaseAudioTranscriber,
    FakeAudioTranscriber,
)
from tests.unit.test_detection_microchunk_checkpoint_resume import (  # noqa: F401  (fixture autouse inclusa)
    FakeClient,
    _no_sleep,
    _resp,
    _run,
    _service,
)
from ui.application import execute_local_media_processing
from ui.ingestion import ingest_file_payload
from ui.models import SourceFormat

SECRET = "ZXQSEGRETO"
VOICE_FILE = "PTT-20261101-WA0001.opus"
CHAT = (
    "[01/11/2026, 10:00:00] Mario: Ciao, ci vediamo domani\n"
    f"[01/11/2026, 10:01:00] Anna: {VOICE_FILE} (file allegato)\n"
    "[01/11/2026, 10:02:00] Mario: ok\n"
)


def _zip(with_audio: bool) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("_chat.txt", CHAT)
        if with_audio:
            zf.writestr(VOICE_FILE, b"OggS\x00\x02\x00\x00OpusHead")  # finto: non è audio decodificabile
    return buf.getvalue()


def _ingest(payload: bytes, filename: str):
    reg = MediaAssetRegistry()
    res = ingest_file_payload(payload, original_filename=filename, source_format=SourceFormat.WHATSAPP_EXPORT, media_registry=reg)
    return res.document_list[0], reg


class CountingStt(FakeAudioTranscriber):
    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.calls = 0

    def transcribe(self, asset: ResolvedMediaAsset):
        self.calls += 1
        return super().transcribe(asset)


class RaisingStt(BaseAudioTranscriber):
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def transcribe(self, asset: ResolvedMediaAsset):
        raise self.exc


def _voice_state(doc) -> AudioEvidenceState | None:
    states = [classify_audio_bundle(b) for b in doc.bundles]
    return next(s for s in states if s is not None)


# ----------------------------------------------------------------------------
# AUDIO_NOT_FOUND: export senza il file audio (stato oggi invisibile)
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("label, payload, name", [
    ("solo .txt", CHAT.encode("utf-8"), "_chat.txt"),
    ("ZIP senza l'allegato", None, "chat.zip"),
])
def test_voice_without_audio_file_is_audio_not_found(label, payload, name):
    doc, reg = _ingest(payload if payload is not None else _zip(False), name)
    voice = next(b for b in doc.bundles if classify_audio_bundle(b) is not None)
    assert voice.message.media_reference is None  # nessun file: nessun pulsante "Elabora media" in UI
    assert classify_audio_bundle(voice) is AudioEvidenceState.AUDIO_NOT_FOUND
    summary = summarize_audio_evidence(doc)
    assert summary["voice_messages_total"] == 1 and summary["AUDIO_NOT_FOUND"] == 1
    msg = format_audio_status_message(summary)
    assert "NON presente nell'export" in msg and "NON è stato analizzato" in msg
    assert VOICE_FILE not in msg, "il messaggio UI non riporta il nome del file"
    # l'elaborazione on-demand non può inventare una trascrizione per un audio assente
    enriched, _ = execute_local_media_processing(doc, registry=reg, stt_backend=CountingStt())
    assert summarize_audio_evidence(enriched)["AUDIO_NOT_FOUND"] == 1
    assert all(s.source_type.value == "ORIGINAL_TEXT" for s in enriched.all_evidence_sections)


def test_documents_without_voice_messages_have_no_audio_block():
    doc, _ = _ingest("[01/11/2026, 10:00:00] Mario: solo testo\n".encode("utf-8"), "_chat.txt")
    assert summarize_audio_evidence(doc)["voice_messages_total"] == 0
    assert format_audio_status_message(summarize_audio_evidence(doc)) is None


# ----------------------------------------------------------------------------
# Nessuna STT automatica; stati STT_NOT_RUN / STT_UNAVAILABLE / TRANSCRIPTION_FAILED
# ----------------------------------------------------------------------------

def test_ingestion_never_starts_stt_and_reports_not_run():
    stt = CountingStt()
    doc, _ = _ingest(_zip(True), "chat.zip")
    assert stt.calls == 0
    assert all(b.audio_transcription is None for b in doc.bundles)
    assert _voice_state(doc) is AudioEvidenceState.STT_NOT_RUN
    assert summarize_audio_evidence(doc)["STT_NOT_RUN"] == 1


def test_stt_not_installed_is_stt_unavailable_not_transcribed():
    doc, reg = _ingest(_zip(True), "chat.zip")
    enriched, report = execute_local_media_processing(doc, registry=reg, stt_backend=None)
    # in questo ambiente faster-whisper non è installato (come su un PC senza il pacchetto)
    assert report["stt_available"] is False
    assert _voice_state(enriched) is AudioEvidenceState.STT_UNAVAILABLE
    assert enriched.metadata["multimodal_status"] == "FAILED"
    assert not any(s.source_type.value == "STT_TRANSCRIPTION" for s in enriched.all_evidence_sections)


@pytest.mark.parametrize("exc, expected", [
    (AudioModelLoadError("modello non caricabile"), AudioEvidenceState.STT_UNAVAILABLE),
    (RuntimeError("errore di decodifica sul singolo file"), AudioEvidenceState.TRANSCRIPTION_FAILED),
])
def test_backend_exceptions_are_classified_global_vs_per_file(exc, expected):
    doc, reg = _ingest(_zip(True), "chat.zip")
    enriched, _ = execute_local_media_processing(doc, registry=reg, stt_backend=RaisingStt(exc))
    assert _voice_state(enriched) is expected
    assert not any(s.source_type.value == "STT_TRANSCRIPTION" for s in enriched.all_evidence_sections)


def test_per_file_failure_reported_by_engine_is_transcription_failed():
    doc, reg = _ingest(_zip(True), "chat.zip")
    enriched, _ = execute_local_media_processing(doc, registry=reg, stt_backend=FakeAudioTranscriber(simulate_failure=True))
    assert _voice_state(enriched) is AudioEvidenceState.TRANSCRIPTION_FAILED


# ----------------------------------------------------------------------------
# TRANSCRIBED: testo derivato, nessuna duplicazione, nessun doppio conteggio
# ----------------------------------------------------------------------------

def _stt_with_text(text: str = f"{SECRET} testo sintetico") -> CountingStt:
    return CountingStt(canned_segments=(AudioTranscriptSegment(start_seconds=0.0, end_seconds=1.0, text=text),))


def test_transcribed_adds_one_derived_section_without_duplicating_the_message():
    doc, reg = _ingest(_zip(True), "chat.zip")
    n_msgs = len(doc.bundles)
    stt = _stt_with_text()
    enriched, report = execute_local_media_processing(doc, registry=reg, stt_backend=stt)

    assert len(enriched.bundles) == n_msgs, "nessun messaggio duplicato"
    assert _voice_state(enriched) is AudioEvidenceState.TRANSCRIBED
    voice = next(b for b in enriched.bundles if classify_audio_bundle(b) is not None)
    kinds = [s.source_type.value for s in voice.text_evidence_sections]
    assert kinds == ["ORIGINAL_TEXT", "STT_TRANSCRIPTION"], kinds
    # il testo originale del messaggio resta quello dell'export (il nome file NON viene trasformato in contenuto)
    assert voice.message.text_content == f"{VOICE_FILE} (file allegato)"
    # provenienza del dato derivato: id messaggio, stato, motore/modello
    tr = voice.audio_transcription
    assert tr.message_id == voice.message.message_id and tr.status.value == "SUCCESS"
    assert tr.engine and tr.model_name and tr.provenance_asset is not None
    assert enriched.metadata["multimodal_status"] == "COMPLETE"
    assert report["derived_sections"] == 1 and stt.calls == 1


def test_retry_does_not_double_count_or_retranscribe():
    doc, reg = _ingest(_zip(True), "chat.zip")
    stt = _stt_with_text()
    first, _ = execute_local_media_processing(doc, registry=reg, stt_backend=stt)
    second, _ = execute_local_media_processing(first, registry=reg, stt_backend=stt)
    s = summarize_audio_evidence(second)
    assert s["TRANSCRIBED"] == 1 and s["voice_messages_total"] == 1
    assert sum(1 for x in second.all_evidence_sections if x.source_type.value == "STT_TRANSCRIPTION") == 1
    assert stt.calls == 1


def test_summary_states_are_disjoint_and_sum_to_total():
    doc, reg = _ingest(_zip(True), "chat.zip")
    for backend in (None, _stt_with_text(), FakeAudioTranscriber(simulate_failure=True)):
        enriched, _ = execute_local_media_processing(doc, registry=reg, stt_backend=backend)
        s = summarize_audio_evidence(enriched)
        assert s["voice_messages_total"] == sum(v for k, v in s.items() if k != "voice_messages_total") == 1


# ----------------------------------------------------------------------------
# Checkpoint e privacy dell'export
# ----------------------------------------------------------------------------

def test_failed_or_unavailable_stt_does_not_change_document_fingerprint():
    """Nessun cambio ai checkpoint esistenti: un tentativo STT fallito non altera l'impronta del documento."""
    doc, reg = _ingest(_zip(True), "chat.zip")
    base = compute_document_fingerprint(doc)
    for backend in (None, FakeAudioTranscriber(simulate_failure=True), RaisingStt(AudioModelLoadError("x"))):
        enriched, _ = execute_local_media_processing(doc, registry=reg, stt_backend=backend)
        assert compute_document_fingerprint(enriched) == base
    ok, _ = execute_local_media_processing(doc, registry=reg, stt_backend=_stt_with_text())
    assert compute_document_fingerprint(ok) != base, "una trascrizione riuscita è un nuovo dato: nuovo documento, vecchi checkpoint intatti"


@pytest.mark.parametrize("variant", ["not_found", "unavailable", "transcribed"])
def test_safe_export_has_counts_only_and_no_transcript_or_filename(variant):
    if variant == "not_found":
        doc, reg = _ingest(CHAT.encode("utf-8"), "_chat.txt")
        backend = None
    else:
        doc, reg = _ingest(_zip(True), "chat.zip")
        backend = None if variant == "unavailable" else _stt_with_text()
    doc, _ = execute_local_media_processing(doc, registry=reg, stt_backend=backend)

    client = FakeClient(lambda u, n: _resp("ABSENT"))
    from ai.models import TopicQuery
    res = _run(_service(client), doc, client, topic=TopicQuery(topic_id="t", label="Sport", description="Attività sportive"))
    report = build_topic_detection_export_report(res, doc)
    text = serialize_report_to_json(report)

    block = report["document"]["audio_evidence"]
    assert all(isinstance(v, int) for v in block.values())
    assert block["voice_messages_total"] == 1
    assert block[{"not_found": "AUDIO_NOT_FOUND", "unavailable": "STT_UNAVAILABLE", "transcribed": "TRANSCRIBED"}[variant]] == 1
    for forbidden in (SECRET, VOICE_FILE, "PTT-", "testo sintetico", "LIVE_VERIFIED"):
        assert forbidden not in text, forbidden
    json.loads(text)


def test_export_without_voice_messages_is_unchanged():
    doc, _ = _ingest("[01/11/2026, 10:00:00] Mario: solo testo\n".encode("utf-8"), "_chat.txt")
    client = FakeClient(lambda u, n: _resp("ABSENT"))
    from ai.models import TopicQuery
    res = _run(_service(client), doc, client, topic=TopicQuery(topic_id="t", label="Sport", description="Attività sportive"))
    assert "audio_evidence" not in build_topic_detection_export_report(res, doc)["document"]


# ----------------------------------------------------------------------------
# UI: funzione di rendering (con `st` registratore: nessuna dipendenza da streamlit reale)
# ----------------------------------------------------------------------------

class _RecordingSt:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __getattr__(self, name: str):
        def _rec(text: str = "", *a: Any, **k: Any) -> None:
            self.calls.append((name, str(text)))
        return _rec


@pytest.mark.parametrize("variant, level", [("not_found", "warning"), ("transcribed", "success")])
def test_ui_render_shows_state_without_filename_or_transcript(monkeypatch, variant, level):
    import ui.presentation as pres

    if variant == "not_found":
        doc, _ = _ingest(CHAT.encode("utf-8"), "_chat.txt")
    else:
        d, reg = _ingest(_zip(True), "chat.zip")
        doc, _ = execute_local_media_processing(d, registry=reg, stt_backend=_stt_with_text())
    rec = _RecordingSt()
    monkeypatch.setattr(pres, "st", rec)
    pres._render_audio_evidence_status(doc)
    assert len(rec.calls) == 1 and rec.calls[0][0] == level
    shown = rec.calls[0][1]
    assert "Messaggi vocali: 1" in shown
    assert VOICE_FILE not in shown and SECRET not in shown


def test_ui_render_is_silent_without_voice_messages(monkeypatch):
    import ui.presentation as pres

    doc, _ = _ingest("[01/11/2026, 10:00:00] Mario: solo testo\n".encode("utf-8"), "_chat.txt")
    rec = _RecordingSt()
    monkeypatch.setattr(pres, "st", rec)
    pres._render_audio_evidence_status(doc)
    assert rec.calls == []
