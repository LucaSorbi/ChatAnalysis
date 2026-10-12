"""
multimodal/evidence.py
----------------------
Modello aggregatore per le evidenze multimodali associate a un UnifiedMessage (MessageEvidenceBundle).

Principi architetturali:
1. MessageEvidenceBundle collega in sola lettura e in modo immutabile:
   - Il messaggio unificato originale (UnifiedMessage);
   - L'eventuale trascrizione vocale (AudioTranscriptionResult);
   - L'eventuale testo estratto da OCR (ImageOcrResult);
   - L'eventuale descrizione visiva (ImageVisionResult).
2. RIGOROSA SEPARAZIONE DELLE FONTI:
   Le evidenze testuali non vengono MAI concatenate silenziosamente all'interno di UnifiedMessage.text_content.
   La property text_evidence_sections restituisce sezioni distinte e tipizzate (TextEvidenceSection)
   etichettate con EvidenceSourceType (ORIGINAL_TEXT, STT_TRANSCRIPTION, OCR_TEXT, VISION_DESCRIPTION, VISION_OBSERVATION).
3. PROVENANCE FORENSE DIRETTA:
   Ciascuna sezione testuale mantiene un identificatore deterministico (evidence_id),
   il riferimento univoco al proprio messaggio (message_id, source_name, source_record_id).
4. PRESERVAZIONE FEDELE DEI DATI E MULTILINGUA:
   Nessuna alterazione di spazi o maiuscole (.strip() solo per verificare se non vuoto, ma il testo restituito
   è esattamente quello sorgente). Nessuna traduzione automatica: ogni sezione conserva la lingua o config originaria.
5. VALIDAZIONE COMPLETA DELLA CATENA DI PROVENANCE:
   Tutte le evidenze derivate devono corrispondere contemporaneamente per message_id, source_name e source_record_id.
   Per risultati con stato SUCCESS è obbligatoria la catena completa (provenance_asset e provenance_message).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from core.immutability import freeze_structural
from multimodal.models import (
    AudioTranscriptionResult,
    ImageOcrResult,
    ImageVisionResult,
    OcrStatus,
    TranscriptionStatus,
    VisionStatus,
)
from unified.models import UnifiedMessage


class EvidenceSourceType(str, Enum):
    """
    Tipologia di fonte probatoria della sezione testuale.
    """
    ORIGINAL_TEXT = "ORIGINAL_TEXT"
    STT_TRANSCRIPTION = "STT_TRANSCRIPTION"
    OCR_TEXT = "OCR_TEXT"
    VISION_DESCRIPTION = "VISION_DESCRIPTION"
    VISION_OBSERVATION = "VISION_OBSERVATION"


@dataclass(frozen=True)
class TextEvidenceSection:
    """
    Singola sezione di evidenza testuale con provenienza esplicita, deterministica e tipizzata.

    Campi:
    ------
    evidence_id : str
        Identificatore deterministico univoco per l'evidenza
        (es. '<msg_id>::ORIGINAL_TEXT', '<msg_id>::VISION_OBSERVATION::0').
    source_type : EvidenceSourceType
        Tipologia di origine dell'evidenza (testo originale, STT, OCR, Vision description, Vision observation).
    text : str
        Contenuto testuale ESATTO dell'evidenza nella lingua originaria, senza alterazioni o strip.
    language : str | None
        Codice lingua o configurazione linguistica associata (se disponibile).
    message_id : str
        Identificativo del messaggio unificato correlato.
    source_name : str
        Nome della sorgente forense d'origine (es. 'msgstore_db', 'cellebrite_csv').
    source_record_id : str
        Identificativo del record d'origine.
    ordinal : int | None
        Indice ordinale deterministico per collezioni ordinate (es. observations visive).
    provenance_source : str
        Alias di retrocompatibilità per source_name.
    provenance_id : str
        Alias di retrocompatibilità per message_id.
    """
    evidence_id: str
    source_type: EvidenceSourceType
    text: str
    language: str | None = None
    message_id: str = ""
    source_name: str = ""
    source_record_id: str = ""
    ordinal: int | None = None
    provenance_source: str = ""
    provenance_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.source_type, EvidenceSourceType):
            object.__setattr__(self, "source_type", EvidenceSourceType(self.source_type))
        if not isinstance(self.text, str):
            raise ValueError(f"text deve essere una stringa, ricevuto {type(self.text)}")
        if not isinstance(self.evidence_id, str) or not self.evidence_id:
            raise ValueError("evidence_id deve essere una stringa non vuota")

        # Sincronizzazione campi provenance
        if not self.source_name and self.provenance_source:
            object.__setattr__(self, "source_name", self.provenance_source)
        elif not self.provenance_source and self.source_name:
            object.__setattr__(self, "provenance_source", self.source_name)

        if not self.message_id and self.provenance_id:
            object.__setattr__(self, "message_id", self.provenance_id)
        elif not self.provenance_id and self.message_id:
            object.__setattr__(self, "provenance_id", self.message_id)


@dataclass(frozen=True)
class MessageEvidenceBundle:
    """
    Bundle immutabile di tutte le evidenze raccolte ed estratte per un singolo UnifiedMessage.

    Campi:
    ------
    message : UnifiedMessage
        Messaggio unificato originario (completamente immutato).
    audio_transcription : AudioTranscriptionResult | None
        Risultato della trascrizione vocale, se applicabile.
    image_ocr : ImageOcrResult | None
        Risultato dell'estrazione OCR da immagine, se applicabile.
    image_vision : ImageVisionResult | None
        Risultato dell'analisi visiva della scena, se applicabile.
    metadata : MappingProxyType[str, Any]
        Metadati aggiuntivi immutabili del bundle.
    """
    message: UnifiedMessage
    audio_transcription: AudioTranscriptionResult | None = None
    image_ocr: ImageOcrResult | None = None
    image_vision: ImageVisionResult | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.message, UnifiedMessage):
            raise ValueError(f"message deve essere un'istanza di UnifiedMessage, ricevuto {type(self.message)}")

        # Helper di validazione simultanea della provenance
        def _validate_evidence(name: str, evidence: Any, success_status: Any) -> None:
            if evidence is None:
                return

            # 1. Verifica concordanza SIMULTANEA di message_id, source_name, source_record_id
            if evidence.message_id != self.message.message_id:
                raise ValueError(
                    f"Discordanza message_id tra {name} ({evidence.message_id}) "
                    f"e message ({self.message.message_id})"
                )
            if evidence.source_name != self.message.source_name:
                raise ValueError(
                    f"Discordanza source_name tra {name} ({evidence.source_name}) "
                    f"e message ({self.message.source_name})"
                )
            if evidence.source_record_id != self.message.source_record_id:
                raise ValueError(
                    f"Discordanza source_record_id tra {name} ({evidence.source_record_id}) "
                    f"e message ({self.message.source_record_id})"
                )

            # 2. Catena di provenance obbligatoria per risultati SUCCESS
            if evidence.status == success_status:
                if evidence.provenance_asset is None:
                    raise ValueError(
                        f"{name} con stato SUCCESS deve possedere un provenance_asset valido"
                    )
                if evidence.provenance_asset.provenance_message is None:
                    raise ValueError(
                        f"provenance_asset di {name} con stato SUCCESS deve possedere un provenance_message collegato"
                    )
                if evidence.provenance_asset.provenance_message.message_id != self.message.message_id:
                    raise ValueError(
                        f"provenance_message di {name} ({evidence.provenance_asset.provenance_message.message_id}) "
                        f"non corrisponde al message del bundle ({self.message.message_id})"
                    )

        if self.audio_transcription is not None:
            if not isinstance(self.audio_transcription, AudioTranscriptionResult):
                raise ValueError(
                    f"audio_transcription deve essere un'istanza di AudioTranscriptionResult, ricevuto {type(self.audio_transcription)}"
                )
            _validate_evidence("audio_transcription", self.audio_transcription, TranscriptionStatus.SUCCESS)

        if self.image_ocr is not None:
            if not isinstance(self.image_ocr, ImageOcrResult):
                raise ValueError(
                    f"image_ocr deve essere un'istanza di ImageOcrResult, ricevuto {type(self.image_ocr)}"
                )
            _validate_evidence("image_ocr", self.image_ocr, OcrStatus.SUCCESS)

        if self.image_vision is not None:
            if not isinstance(self.image_vision, ImageVisionResult):
                raise ValueError(
                    f"image_vision deve essere un'istanza di ImageVisionResult, ricevuto {type(self.image_vision)}"
                )
            _validate_evidence("image_vision", self.image_vision, VisionStatus.SUCCESS)

        object.__setattr__(self, "metadata", freeze_structural(self.metadata))

    @property
    def message_id(self) -> str:
        """Identificativo del messaggio unificato."""
        return self.message.message_id

    @property
    def source_name(self) -> str:
        """Sorgente forense d'origine del messaggio."""
        return self.message.source_name

    @property
    def source_record_id(self) -> str:
        """Identificativo record d'origine del messaggio."""
        return self.message.source_record_id

    @property
    def text_evidence_sections(self) -> tuple[TextEvidenceSection, ...]:
        """
        Restituisce in ordine deterministico le sezioni di evidenza testuale disponibili.

        Ordine fisso e deterministico:
        1. ORIGINAL_TEXT (se il messaggio unificato contiene testo non vuoto)
        2. STT_TRANSCRIPTION (se presente con stato SUCCESS e trascrizione non vuota)
        3. OCR_TEXT (se presente con stato SUCCESS e testo non vuoto)
        4. VISION_DESCRIPTION (se presente con stato SUCCESS e descrizione non vuota)
        5. VISION_OBSERVATION (per ciascuna observation non vuota nell'ordine originario)

        IMPORTANTE:
        - Il testo restituito è ESATTAMENTE quello sorgente, senza modifiche, strip o alterazioni.
        - Nessuna sezione viene concatenata nel campo message.text_content.
        """
        sections: list[TextEvidenceSection] = []

        # 1. Testo originale del messaggio
        if self.message.text_content and self.message.text_content.strip():
            sections.append(
                TextEvidenceSection(
                    evidence_id=f"{self.message.message_id}::ORIGINAL_TEXT",
                    source_type=EvidenceSourceType.ORIGINAL_TEXT,
                    text=self.message.text_content,
                    language=None,
                    message_id=self.message.message_id,
                    source_name=self.message.source_name,
                    source_record_id=self.message.source_record_id,
                    ordinal=None,
                    provenance_source=self.message.source_name,
                    provenance_id=self.message.message_id,
                )
            )

        # 2. Trascrizione audio STT
        if (
            self.audio_transcription is not None
            and self.audio_transcription.status == TranscriptionStatus.SUCCESS
            and self.audio_transcription.full_transcript
            and self.audio_transcription.full_transcript.strip()
        ):
            sections.append(
                TextEvidenceSection(
                    evidence_id=f"{self.message.message_id}::STT_TRANSCRIPTION",
                    source_type=EvidenceSourceType.STT_TRANSCRIPTION,
                    text=self.audio_transcription.full_transcript,
                    language=self.audio_transcription.detected_language,
                    message_id=self.message.message_id,
                    source_name=self.message.source_name,
                    source_record_id=self.message.source_record_id,
                    ordinal=None,
                    provenance_source=self.audio_transcription.source_name,
                    provenance_id=self.audio_transcription.message_id,
                )
            )

        # 3. Testo estratto da OCR
        if (
            self.image_ocr is not None
            and self.image_ocr.status == OcrStatus.SUCCESS
            and self.image_ocr.full_text
            and self.image_ocr.full_text.strip()
        ):
            sections.append(
                TextEvidenceSection(
                    evidence_id=f"{self.message.message_id}::OCR_TEXT",
                    source_type=EvidenceSourceType.OCR_TEXT,
                    text=self.image_ocr.full_text,
                    language=self.image_ocr.language_config,
                    message_id=self.message.message_id,
                    source_name=self.message.source_name,
                    source_record_id=self.message.source_record_id,
                    ordinal=None,
                    provenance_source=self.image_ocr.source_name,
                    provenance_id=self.image_ocr.message_id,
                )
            )

        # 4. Descrizione visiva della scena
        if (
            self.image_vision is not None
            and self.image_vision.status == VisionStatus.SUCCESS
            and self.image_vision.description
            and self.image_vision.description.strip()
        ):
            sections.append(
                TextEvidenceSection(
                    evidence_id=f"{self.message.message_id}::VISION_DESCRIPTION",
                    source_type=EvidenceSourceType.VISION_DESCRIPTION,
                    text=self.image_vision.description,
                    language=None,
                    message_id=self.message.message_id,
                    source_name=self.message.source_name,
                    source_record_id=self.message.source_record_id,
                    ordinal=None,
                    provenance_source=self.image_vision.source_name,
                    provenance_id=self.image_vision.message_id,
                )
            )

        # 5. Vision observations distinte nell'ordine originale
        if (
            self.image_vision is not None
            and self.image_vision.status == VisionStatus.SUCCESS
            and self.image_vision.observations
        ):
            for idx, obs in enumerate(self.image_vision.observations):
                if obs and obs.strip():
                    sections.append(
                        TextEvidenceSection(
                            evidence_id=f"{self.message.message_id}::VISION_OBSERVATION::{idx}",
                            source_type=EvidenceSourceType.VISION_OBSERVATION,
                            text=obs,
                            language=None,
                            message_id=self.message.message_id,
                            source_name=self.message.source_name,
                            source_record_id=self.message.source_record_id,
                            ordinal=idx,
                            provenance_source=self.image_vision.source_name,
                            provenance_id=self.image_vision.message_id,
                        )
                    )

        return tuple(sections)
