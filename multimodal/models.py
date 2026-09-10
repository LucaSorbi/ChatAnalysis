"""
multimodal/models.py
--------------------
Modelli immutabili per il layer di Multimodal Processing (Media Resolution & Audio STT).

Principi architetturali:
1. Il UnifiedMessage originale e i modelli dei layer precedenti rimangono categoricamente IMMUTABILI.
2. ResolvedMediaAsset traccia la risoluzione fisica di un riferimento multimediale (o il motivo del suo mancato reperimento).
3. AudioTranscriptionResult incapsula il risultato della trascrizione vocale, preservando segmenti temporali, lingua rilevata e piena provenance forense.
4. Deep immutability: tutti i modelli sono frozen dataclass e utilizzano core.immutability.freeze_structural per le strutture dati nidificate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from core.immutability import freeze_structural


class MediaKind(str, Enum):
    """
    Tipologia di contenuto multimediale rilevata o attesa.
    """
    AUDIO = "AUDIO"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"
    UNKNOWN = "UNKNOWN"


class MediaResolutionStatus(str, Enum):
    """
    Stato della risoluzione fisica dell'asset multimediale.

    - RESOLVED: file localizzato fisicamente e validato all'interno delle root consentite.
    - NO_REFERENCE: il record non dichiara alcun riferimento multimediale (es. messaggio puramente testuale).
    - MISSING: riferimento dichiarato ma file non presente sul filesystem.
    - OUTSIDE_ALLOWED_ROOT: path traversal o percorso che tenta di accedere a percorsi esterni alle root consentite.
    - REMOTE_REFERENCE: riferimento a risorsa remota (URL HTTP/HTTPS); nessun fetch di rete viene effettuato.
    - AMBIGUOUS: risoluzione non univoca o conflittuale.
    - UNSUPPORTED: sintassi di riferimento o formato non supportato.
    """
    RESOLVED = "RESOLVED"
    NO_REFERENCE = "NO_REFERENCE"
    MISSING = "MISSING"
    OUTSIDE_ALLOWED_ROOT = "OUTSIDE_ALLOWED_ROOT"
    REMOTE_REFERENCE = "REMOTE_REFERENCE"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class ResolvedMediaAsset:
    """
    Rappresentazione immutabile della risoluzione fisica di un asset multimediale.

    Campi:
    ------
    message_id : str
        Identificativo canonico del messaggio originario (es. 'unified:msgstore_db:101').
    source_name : str
        Nome della sorgente forense d'origine (es. 'msgstore_db', 'cellebrite_csv').
    source_record_id : str
        Identificativo del record nella sorgente originale.
    raw_reference : str | None
        Valore originario del riferimento multimediale (es. 'WhatsApp Audio/AUD_00015.opus').
    resolved_path : str | None
        Percorso canonico assoluto del file se RESOLVED, altrimenti None.
    media_kind : MediaKind
        Tipologia di media rilevata (AUDIO, IMAGE, VIDEO, DOCUMENT, UNKNOWN).
    status : MediaResolutionStatus
        Esito della risoluzione (RESOLVED, MISSING, REMOTE_REFERENCE, OUTSIDE_ALLOWED_ROOT, ecc.).
    file_size_bytes : int | None
        Dimensione del file in byte se RESOLVED, altrimenti None.
    sha256 : str | None
        Digest SHA-256 esadecimale calcolato in sola lettura se RESOLVED, altrimenti None.
    metadata : MappingProxyType[str, Any]
        Metadati aggiuntivi immutabili (mime_type, note forensi, linkage info).
    """
    message_id: str
    source_name: str
    source_record_id: str
    raw_reference: str | None
    resolved_path: str | None
    media_kind: MediaKind
    status: MediaResolutionStatus
    file_size_bytes: int | None = None
    sha256: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.media_kind, MediaKind):
            object.__setattr__(self, "media_kind", MediaKind(self.media_kind))
        if not isinstance(self.status, MediaResolutionStatus):
            object.__setattr__(self, "status", MediaResolutionStatus(self.status))
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))

    @property
    def is_resolved(self) -> bool:
        """True se l'asset è stato localizzato fisicamente e in sicurezza."""
        return self.status == MediaResolutionStatus.RESOLVED and self.resolved_path is not None


@dataclass(frozen=True)
class AudioTranscriptSegment:
    """
    Segmento temporale trascritto prodotto dal motore Speech-to-Text.

    Campi:
    ------
    start_seconds : float
        Inizio del segmento vocale in secondi dall'avvio dell'audio.
    end_seconds : float
        Fine del segmento vocale in secondi.
    text : str
        Trascrizione testuale del segmento vocale nella lingua originale.
    """
    start_seconds: float
    end_seconds: float
    text: str


class TranscriptionStatus(str, Enum):
    """
    Stato dell'elaborazione di trascrizione vocale.

    - SUCCESS: trascrizione completata con successo.
    - FAILED: errore durante la decodifica audio o l'inferenza STT.
    - UNSUPPORTED: asset non supportato o tipo di media non compatibile.
    - NO_AUDIO: asset privo di contenuto audio o non risolto.
    """
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"
    NO_AUDIO = "NO_AUDIO"


@dataclass(frozen=True)
class AudioTranscriptionResult:
    """
    Risultato immutabile della trascrizione audio di un messaggio.

    Campi:
    ------
    message_id : str
        Identificativo del messaggio unificato associato.
    source_name : str
        Nome della sorgente originaria.
    source_record_id : str
        Identificativo del record originale.
    status : TranscriptionStatus
        Stato della trascrizione (SUCCESS, FAILED, UNSUPPORTED, NO_AUDIO).
    full_transcript : str
        Testo integrale della trascrizione derivato dai segmenti (vuoto se fallito/non-audio).
    segments : tuple[AudioTranscriptSegment, ...]
        Tupla ordinata dei segmenti temporali rilevati.
    detected_language : str | None
        Codice lingua ISO rilevato dal motore STT (es. 'it', 'en', 'es').
    language_probability : float | None
        Probabilità stimata per la lingua rilevata (0.0 - 1.0).
    engine : str
        Identificatore del motore utilizzato (es. 'faster-whisper', 'fake-whisper').
    model_name : str
        Nome del modello utilizzato (es. 'tiny', 'base', 'small').
    device : str
        Dispositivo di calcolo effettivamente impiegato ('cpu', 'cuda').
    compute_type : str
        Precisione di calcolo impiegata ('int8', 'float16', 'float32').
    error_message : str | None
        Descrizione controllata dell'errore in caso di stato FAILED.
    metadata : MappingProxyType[str, Any]
        Metadati aggiuntivi immutabili e provenance.
    """
    message_id: str
    source_name: str
    source_record_id: str
    status: TranscriptionStatus
    full_transcript: str = ""
    segments: tuple[AudioTranscriptSegment, ...] = ()
    detected_language: str | None = None
    language_probability: float | None = None
    engine: str = "faster-whisper"
    model_name: str = "tiny"
    device: str = "cpu"
    compute_type: str = "int8"
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, TranscriptionStatus):
            object.__setattr__(self, "status", TranscriptionStatus(self.status))
        if not isinstance(self.segments, tuple):
            object.__setattr__(self, "segments", tuple(self.segments))
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))
