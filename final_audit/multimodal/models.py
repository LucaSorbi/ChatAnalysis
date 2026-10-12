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
from unified.models import UnifiedMessage


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
    provenance_message : UnifiedMessage | None
        Riferimento diretto e immutabile al UnifiedMessage d'origine (obbligatorio se prodotto dal MediaResolver).
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
    provenance_message: UnifiedMessage | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.media_kind, MediaKind):
            object.__setattr__(self, "media_kind", MediaKind(self.media_kind))
        if not isinstance(self.status, MediaResolutionStatus):
            object.__setattr__(self, "status", MediaResolutionStatus(self.status))
        if self.provenance_message is not None:
            if not isinstance(self.provenance_message, UnifiedMessage):
                raise ValueError(
                    f"provenance_message deve essere un'istanza di UnifiedMessage, ricevuto {type(self.provenance_message)}"
                )
            if (
                self.message_id != self.provenance_message.message_id
                or self.source_name != self.provenance_message.source_name
                or self.source_record_id != self.provenance_message.source_record_id
            ):
                raise ValueError(
                    f"Incoerenza di provenance tra ResolvedMediaAsset ({self.message_id}, {self.source_name}, {self.source_record_id}) "
                    f"e UnifiedMessage ({self.provenance_message.message_id}, {self.provenance_message.source_name}, {self.provenance_message.source_record_id})"
                )
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

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError(f"text deve essere una stringa, ricevuto {type(self.text)}")
        if float(self.start_seconds) < 0.0:
            raise ValueError(f"start_seconds deve essere >= 0.0, ricevuto {self.start_seconds}")
        if float(self.end_seconds) < float(self.start_seconds):
            raise ValueError(
                f"end_seconds ({self.end_seconds}) non può essere minore di start_seconds ({self.start_seconds})"
            )


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
    provenance_asset : ResolvedMediaAsset | None
        Riferimento diretto e immutabile al ResolvedMediaAsset trascritto.
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
    provenance_asset: ResolvedMediaAsset | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, TranscriptionStatus):
            object.__setattr__(self, "status", TranscriptionStatus(self.status))
        if not isinstance(self.segments, tuple):
            object.__setattr__(self, "segments", tuple(self.segments))
        if self.provenance_asset is not None:
            if not isinstance(self.provenance_asset, ResolvedMediaAsset):
                raise ValueError(
                    f"provenance_asset deve essere un'istanza di ResolvedMediaAsset, ricevuto {type(self.provenance_asset)}"
                )
            if (
                self.message_id != self.provenance_asset.message_id
                or self.source_name != self.provenance_asset.source_name
                or self.source_record_id != self.provenance_asset.source_record_id
            ):
                raise ValueError(
                    f"Incoerenza di provenance tra AudioTranscriptionResult ({self.message_id}, {self.source_name}, {self.source_record_id}) "
                    f"e ResolvedMediaAsset ({self.provenance_asset.message_id}, {self.provenance_asset.source_name}, {self.provenance_asset.source_record_id})"
                )
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))

    @property
    def asset_sha256(self) -> str | None:
        """Restituisce direttamente il digest SHA-256 dell'asset fisico processato."""
        return self.provenance_asset.sha256 if self.provenance_asset is not None else None


class OcrStatus(str, Enum):
    """
    Stato dell'elaborazione di estrazione testo da immagine (OCR).

    - SUCCESS: estrazione completata con successo e testo rilevato.
    - NO_TEXT: elaborazione completata senza errori, ma nessun testo rilevato nell'immagine.
    - FAILED: errore durante la decodifica dell'immagine o l'esecuzione del motore OCR.
    - NO_IMAGE: asset privo di file immagine o non risolto sul filesystem.
    - UNSUPPORTED: asset non supportato o tipo di media non compatibile (richiesto IMAGE).
    """
    SUCCESS = "SUCCESS"
    NO_TEXT = "NO_TEXT"
    FAILED = "FAILED"
    NO_IMAGE = "NO_IMAGE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class OcrTextRegion:
    """
    Regione di testo individuata all'interno dell'immagine dal motore OCR.

    Campi:
    ------
    text : str
        Testo rilevato nella regione.
    bounding_box : tuple[int, int, int, int] | None
        Coordinate del box (x, y, w, h) in pixel, se fornite dal motore.
    confidence : float | None
        Livello di confidenza normalizzato nell'intervallo [0.0, 1.0].
    order_index : int
        Indice di lettura o sequenza progressiva all'interno della pagina/immagine.
    """
    text: str
    bounding_box: tuple[int, int, int, int] | None = None
    confidence: float | None = None
    order_index: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.order_index, int) or self.order_index < 0:
            raise ValueError(f"order_index deve essere un intero >= 0, ricevuto {self.order_index}")
        if self.confidence is not None:
            if not (0.0 <= float(self.confidence) <= 1.0):
                raise ValueError(f"confidence deve essere compreso tra 0.0 e 1.0, ricevuto {self.confidence}")
            object.__setattr__(self, "confidence", float(self.confidence))
        if self.bounding_box is not None:
            if not isinstance(self.bounding_box, tuple):
                object.__setattr__(self, "bounding_box", tuple(self.bounding_box))
            if len(self.bounding_box) != 4 or not all(isinstance(v, int) for v in self.bounding_box):
                raise ValueError(
                    f"bounding_box deve essere una tupla di 4 interi (x, y, w, h), ricevuto {self.bounding_box}"
                )
            x, y, w, h = self.bounding_box
            if x < 0 or y < 0:
                raise ValueError(f"Coordinate (x, y) del bounding_box devono essere >= 0, ricevuto ({x}, {y})")
            if w < 0 or h < 0:
                raise ValueError(f"Dimensioni (w, h) del bounding_box devono essere >= 0, ricevuto ({w}, {h})")


@dataclass(frozen=True)
class ImageOcrResult:
    """
    Risultato immutabile dell'estrazione OCR su un asset immagine.

    Campi:
    ------
    message_id : str
        Identificativo del messaggio unificato associato.
    source_name : str
        Nome della sorgente originaria.
    source_record_id : str
        Identificativo del record originale.
    status : OcrStatus
        Stato dell'estrazione OCR (SUCCESS, NO_TEXT, FAILED, NO_IMAGE, UNSUPPORTED).
    full_text : str
        Testo integrale estratto (vuoto se NO_TEXT o errore).
    regions : tuple[OcrTextRegion, ...]
        Tupla ordinata delle regioni di testo identificate.
    language_config : str | None
        Configurazione linguistica impiegata dal motore OCR (es. 'ita', 'eng', 'ita+eng').
    engine : str
        Identificatore del motore OCR (es. 'tesseract', 'fake-ocr').
    engine_version : str | None
        Versione del motore OCR se disponibile.
    provenance_asset : ResolvedMediaAsset | None
        Riferimento diretto e immutabile al ResolvedMediaAsset analizzato.
    error_message : str | None
        Descrizione controllata dell'errore in caso di stato FAILED.
    metadata : Mapping[str, Any]
        Metadati aggiuntivi immutabili e provenance.
    """
    message_id: str
    source_name: str
    source_record_id: str
    status: OcrStatus
    full_text: str = ""
    regions: tuple[OcrTextRegion, ...] = ()
    language_config: str | None = None
    engine: str = "tesseract"
    engine_version: str | None = None
    provenance_asset: ResolvedMediaAsset | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, OcrStatus):
            object.__setattr__(self, "status", OcrStatus(self.status))
        if not isinstance(self.regions, tuple):
            object.__setattr__(self, "regions", tuple(self.regions))
        if self.provenance_asset is not None:
            if not isinstance(self.provenance_asset, ResolvedMediaAsset):
                raise ValueError(
                    f"provenance_asset deve essere un'istanza di ResolvedMediaAsset, ricevuto {type(self.provenance_asset)}"
                )
            if (
                self.message_id != self.provenance_asset.message_id
                or self.source_name != self.provenance_asset.source_name
                or self.source_record_id != self.provenance_asset.source_record_id
            ):
                raise ValueError(
                    f"Incoerenza di provenance tra ImageOcrResult ({self.message_id}, {self.source_name}, {self.source_record_id}) "
                    f"e ResolvedMediaAsset ({self.provenance_asset.message_id}, {self.provenance_asset.source_name}, {self.provenance_asset.source_record_id})"
                )
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))

    @property
    def asset_sha256(self) -> str | None:
        """Restituisce direttamente il digest SHA-256 dell'asset fisico processato."""
        return self.provenance_asset.sha256 if self.provenance_asset is not None else None


class VisionStatus(str, Enum):
    """
    Stato dell'elaborazione di analisi visiva (Image Vision).

    - SUCCESS: analisi visiva completata con successo.
    - NO_CONTENT: analisi completata, ma nessun contenuto saliente identificato.
    - FAILED: errore durante l'elaborazione o l'inferenza del modello Vision.
    - NO_IMAGE: asset privo di file immagine o non risolto sul filesystem.
    - UNSUPPORTED: asset non supportato o tipo di media non compatibile (richiesto IMAGE).
    """
    SUCCESS = "SUCCESS"
    NO_CONTENT = "NO_CONTENT"
    FAILED = "FAILED"
    NO_IMAGE = "NO_IMAGE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class ImageVisionResult:
    """
    Risultato immutabile dell'analisi visiva (Image Vision) su un asset immagine.

    Distinzione architetturale:
    - OCR risponde a: "Quale testo è scritto nell'immagine?"
    - Vision risponde a: "Che cosa è rappresentato nell'immagine?"

    Vincolo deontologico e di sicurezza forense:
    Nessun dato di face recognition, identificazione personale o profilazione biometrica.

    Campi:
    ------
    message_id : str
        Identificativo del messaggio unificato associato.
    source_name : str
        Nome della sorgente originaria.
    source_record_id : str
        Identificativo del record originale.
    status : VisionStatus
        Stato dell'analisi visiva (SUCCESS, NO_CONTENT, FAILED, NO_IMAGE, UNSUPPORTED).
    description : str
        Descrizione testuale complessiva della scena rappresentata.
    observations : tuple[str, ...]
        Elenco ordinato e immutabile di osservazioni salienti sugli elementi della scena.
    engine : str
        Identificatore del motore Vision utilizzato.
    model_name : str
        Nome del modello utilizzato.
    provenance_asset : ResolvedMediaAsset | None
        Riferimento diretto e immutabile al ResolvedMediaAsset analizzato.
    error_message : str | None
        Descrizione controllata dell'errore in caso di stato FAILED.
    metadata : Mapping[str, Any]
        Metadati aggiuntivi immutabili e provenance.
    """
    message_id: str
    source_name: str
    source_record_id: str
    status: VisionStatus
    description: str = ""
    observations: tuple[str, ...] = ()
    engine: str = "fake-vision"
    model_name: str = "mock-vision"
    provenance_asset: ResolvedMediaAsset | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, VisionStatus):
            object.__setattr__(self, "status", VisionStatus(self.status))
        if not isinstance(self.observations, tuple):
            object.__setattr__(self, "observations", tuple(self.observations))
        if not all(isinstance(obs, str) for obs in self.observations):
            raise ValueError("Tutti gli elementi di observations devono essere stringhe")
        if self.provenance_asset is not None:
            if not isinstance(self.provenance_asset, ResolvedMediaAsset):
                raise ValueError(
                    f"provenance_asset deve essere un'istanza di ResolvedMediaAsset, ricevuto {type(self.provenance_asset)}"
                )
            if (
                self.message_id != self.provenance_asset.message_id
                or self.source_name != self.provenance_asset.source_name
                or self.source_record_id != self.provenance_asset.source_record_id
            ):
                raise ValueError(
                    f"Incoerenza di provenance tra ImageVisionResult ({self.message_id}, {self.source_name}, {self.source_record_id}) "
                    f"e ResolvedMediaAsset ({self.provenance_asset.message_id}, {self.provenance_asset.source_name}, {self.provenance_asset.source_record_id})"
                )
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))

    @property
    def asset_sha256(self) -> str | None:
        """Restituisce direttamente il digest SHA-256 dell'asset fisico processato."""
        return self.provenance_asset.sha256 if self.provenance_asset is not None else None


