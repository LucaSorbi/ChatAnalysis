"""
multimodal/audio_status.py
--------------------------
Stato VERITIERO dei messaggi vocali nel documento (solo derivazione, nessuna esecuzione STT).

Scopo: distinguere in modo esplicito, in UI ed export, ciò che oggi appare solo come nome di file `.opus`:

- AUDIO_NOT_FOUND       il messaggio è un vocale ma l'export non contiene il file audio (nessun `media_reference`,
                        es. export WhatsApp solo .txt o ZIP senza l'allegato): il contenuto audio NON è analizzabile.
- STT_NOT_RUN           il file audio è presente ma nessun risultato STT è associato (l'elaborazione è on-demand).
- STT_UNAVAILABLE       l'elaborazione è stata tentata ma il motore STT locale non è disponibile / non caricabile.
- TRANSCRIPTION_FAILED  il motore è disponibile ma la trascrizione di QUEL file è fallita (decodifica/inferenza).
- TRANSCRIBED_EMPTY     trascrizione riuscita ma senza testo (es. silenzio): nessuna evidenza testuale prodotta.
- TRANSCRIBED           trascrizione riuscita con testo: esiste una sezione STT_TRANSCRIPTION (testo DERIVATO).

Il testo STT è un dato derivato e NON ha attendibilità forense automatica. Questo modulo non legge né restituisce mai
testo, nomi di file o trascrizioni: solo etichette e conteggi interi.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from multimodal.models import TranscriptionStatus
from multimodal.resolver import _AUDIO_EXTENSIONS

# Chiave dei metadati di AudioTranscriptionResult che registra il MOTIVO strutturato di un fallimento.
STT_REASON_METADATA_KEY = "stt_reason"


class AudioEvidenceState(str, Enum):
    AUDIO_NOT_FOUND = "AUDIO_NOT_FOUND"
    STT_NOT_RUN = "STT_NOT_RUN"
    STT_UNAVAILABLE = "STT_UNAVAILABLE"
    TRANSCRIPTION_FAILED = "TRANSCRIPTION_FAILED"
    TRANSCRIBED_EMPTY = "TRANSCRIBED_EMPTY"
    TRANSCRIBED = "TRANSCRIBED"


def _is_voice_message(bundle: Any) -> bool:
    """Un messaggio è 'vocale' se tipizzato AUDIO, oppure ha un riferimento con estensione audio, oppure ha un risultato STT."""
    msg = bundle.message
    mtype = getattr(getattr(msg, "message_type", None), "value", getattr(msg, "message_type", None))
    if str(mtype).upper() == "AUDIO":
        return True
    if getattr(bundle, "audio_transcription", None) is not None:
        return True
    ref = getattr(msg, "media_reference", None)
    if ref and isinstance(ref, str):
        dot = ref.rfind(".")
        return dot >= 0 and ref[dot:].lower() in _AUDIO_EXTENSIONS
    return False


def classify_audio_bundle(bundle: Any) -> AudioEvidenceState | None:
    """Stato audio di un bundle, oppure None se il messaggio non è un vocale."""
    if not _is_voice_message(bundle):
        return None
    tr = getattr(bundle, "audio_transcription", None)
    if tr is None:
        ref = getattr(bundle.message, "media_reference", None)
        if not ref or not str(ref).strip():
            return AudioEvidenceState.AUDIO_NOT_FOUND
        return AudioEvidenceState.STT_NOT_RUN
    if tr.status == TranscriptionStatus.SUCCESS:
        if tr.full_transcript and tr.full_transcript.strip():
            return AudioEvidenceState.TRANSCRIBED
        return AudioEvidenceState.TRANSCRIBED_EMPTY
    reason = dict(tr.metadata or {}).get(STT_REASON_METADATA_KEY)
    if reason == AudioEvidenceState.STT_UNAVAILABLE.value:
        return AudioEvidenceState.STT_UNAVAILABLE
    return AudioEvidenceState.TRANSCRIPTION_FAILED


def summarize_audio_evidence(document: Any) -> dict[str, int]:
    """
    Conteggi (solo interi) dei vocali del documento per stato. `voice_messages_total` è la somma degli stati:
    nessun doppio conteggio (ogni messaggio ha esattamente uno stato).
    """
    counts = {s.value: 0 for s in AudioEvidenceState}
    for bundle in document.bundles:
        state = classify_audio_bundle(bundle)
        if state is not None:
            counts[state.value] += 1
    total = sum(counts.values())
    return {"voice_messages_total": total, **counts}


def format_audio_status_message(summary: Mapping[str, int]) -> str | None:
    """
    Messaggio statico per la UI (italiano). Contiene SOLO conteggi ed etichette: mai nomi di file né testo.
    None se il documento non contiene vocali.
    """
    total = int(summary.get("voice_messages_total", 0) or 0)
    if total <= 0:
        return None
    labels = (
        (AudioEvidenceState.TRANSCRIBED, "trascritti (testo derivato STT)"),
        (AudioEvidenceState.TRANSCRIBED_EMPTY, "trascritti senza testo"),
        (AudioEvidenceState.AUDIO_NOT_FOUND, "audio NON presente nell'export"),
        (AudioEvidenceState.STT_NOT_RUN, "audio presente, non ancora trascritti"),
        (AudioEvidenceState.STT_UNAVAILABLE, "motore STT non disponibile"),
        (AudioEvidenceState.TRANSCRIPTION_FAILED, "trascrizione fallita"),
    )
    parts = [f"{int(summary.get(s.value, 0))} {label}" for s, label in labels if int(summary.get(s.value, 0))]
    msg = f"Messaggi vocali: {total} — " + "; ".join(parts) + "."
    not_analyzed = total - int(summary.get(AudioEvidenceState.TRANSCRIBED.value, 0))
    if not_analyzed > 0:
        msg += (
            f" Per {not_analyzed} vocali il contenuto audio NON è stato analizzato: "
            "l'analisi vede solo la riga di testo dell'export (es. il nome del file), non ciò che è stato detto."
        )
    if int(summary.get(AudioEvidenceState.AUDIO_NOT_FOUND.value, 0)):
        msg += " Per includere l'audio, esportare la chat da WhatsApp «con media» (archivio ZIP)."
    return msg
