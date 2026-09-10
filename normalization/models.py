"""
normalization/models.py
-----------------------
Modelli immutabili per il layer di Normalizzazione.

Principi:
- NORMALIZZAZIONE != VALIDAZIONE != ENTITY RESOLUTION.
- Il modello NormalizedRecord conserva una provenance completa e immutabile
  verso il RawRecord e il ValidationResult originali.
- Distingue esplicitamente valore raw e valore normalizzato.
- Gestisce in modo source-aware e rigoroso i timestamp:
  - KNOWN_UTC: timestamp con timezone noto e convertito deterministicamente in UTC;
  - NAIVE_UNKNOWN: timestamp locale/naive senza timezone (es. CSV Cellebrite),
    senza assumere fusi orari arbitrari;
  - ABSENT: timestamp mancante o non valorizzato (es. timestamp CSV vuoto).
- Non implementa Entity Resolution: nessun JID risolto a contatto, nessun
  alias trasformato in entità, nessun cross-source linkage.
- Immutabilità profonda: tutti i modelli sono frozen dataclass ed Enum.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

from core.immutability import freeze_structural
from importer.models import RawRecord
from validation.models import ValidationResult


class TimestampTzStatus(str, Enum):
    """
    Stato della normalizzazione temporale e del fuso orario.

    - KNOWN_UTC: timestamp normalizzato con timezone noto o esplicito,
      convertito deterministicamente in datetime UTC aware.
    - NAIVE_UNKNOWN: timestamp locale naive privo di informazione di fuso orario
      (es. esportazioni Cellebrite CSV). Non viene forzata alcuna conversione fittizia.
    - ABSENT: timestamp non disponibile, vuoto o anomalo (es. timestamp CSV vuoto).
      Non viene mai sostituito con epoch 0 o data odierna.
    """
    KNOWN_UTC = "KNOWN_UTC"
    NAIVE_UNKNOWN = "NAIVE_UNKNOWN"
    ABSENT = "ABSENT"


class CanonicalMessageType(str, Enum):
    """
    Tassonomia canonica candidate dei tipi di messaggio.

    Rappresenta l'unificazione conservativa dei tipi osservati nelle 5 sorgenti,
    preservando sempre il tipo nativo originale tramite raw_message_type.
    """
    TEXT = "TEXT"
    AUDIO = "AUDIO"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    SYSTEM = "SYSTEM"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class NormalizedTimestamp:
    """
    Rappresentazione immutabile e rigorosa del timestamp normalizzato.

    Campi:
    ------
    status : TimestampTzStatus
        Stato di certezza del fuso orario (KNOWN_UTC, NAIVE_UNKNOWN, ABSENT).
    utc_datetime : datetime | None
        Data/ora in UTC con tzinfo=timezone.utc se status == KNOWN_UTC, altrimenti None.
    naive_datetime : datetime | None
        Data/ora naive locale se status == NAIVE_UNKNOWN, altrimenti None.
    iso_string : str | None
        Rappresentazione ISO-8601 canonica del timestamp normalizzato (None se ABSENT).
    raw_value : Any
        Valore originale presente nel RawRecord (per audit e provenance forense).
    """
    status: TimestampTzStatus
    utc_datetime: datetime | None = None
    naive_datetime: datetime | None = None
    iso_string: str | None = None
    raw_value: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, TimestampTzStatus):
            raise ValueError(f"NormalizedTimestamp.status deve essere un TimestampTzStatus, ricevuto {type(self.status)}.")


@dataclass(frozen=True)
class NormalizedActor:
    """
    Rappresentazione normalizzata conservativa di un attore (mittente, destinatario o contatto).

    Non effettua Entity Resolution. Distingue strutturalmente la natura dell'identificativo:
    - "phone": numero telefonico internazionale ripulito deterministicamente da spazi/trattini;
    - "jid": identificativo WhatsApp con separazione lossless di local_part e domain;
    - "alias": pseudonimo opaco o alias di gruppo (es. 'group_participant_A');
    - "chat_id": identificativo di chat/conversazione (es. 'chat_1');
    - "unknown": identificativo non classificabile.
    """
    raw_value: str | None
    actor_type: str
    normalized_phone: str | None = None
    jid_local: str | None = None
    jid_domain: str | None = None
    alias: str | None = None
    chat_id: str | None = None

    def __post_init__(self) -> None:
        if self.actor_type not in ("phone", "jid", "alias", "chat_id", "local_user", "unknown"):
            raise ValueError(f"NormalizedActor.actor_type non valido: {self.actor_type}")


@dataclass(frozen=True)
class NormalizedRecord:
    """
    Modello intermedio immutabile del layer di Normalizzazione.

    Incapsula i dati normalizzati garantendo la totale provenance verso
    il RawRecord originale e il relativo ValidationResult.
    Non è un UnifiedMessage (non modella Chat canonica né Participant canonico).
    """
    raw_record: RawRecord
    validation_result: ValidationResult
    source_name: str
    source_record_id: str
    record_type: str
    timestamp: NormalizedTimestamp
    actor_from: NormalizedActor | None = None
    actor_to: NormalizedActor | None = None
    chat_id: str | None = None
    message_type: CanonicalMessageType = CanonicalMessageType.UNKNOWN
    raw_message_type: Any = None
    is_deleted: bool | None = None
    raw_deleted: Any = None
    text_content: str | None = None
    media_reference: str | None = None
    metadata: MappingProxyType[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not isinstance(self.raw_record, RawRecord):
            raise ValueError(f"raw_record deve essere un'istanza di RawRecord, ricevuto {type(self.raw_record)}.")
        if not isinstance(self.validation_result, ValidationResult):
            raise ValueError(f"validation_result deve essere un'istanza di ValidationResult, ricevuto {type(self.validation_result)}.")
        if not isinstance(self.timestamp, NormalizedTimestamp):
            raise ValueError(f"timestamp deve essere un'istanza di NormalizedTimestamp, ricevuto {type(self.timestamp)}.")
        if not isinstance(self.message_type, CanonicalMessageType):
            raise ValueError(f"message_type deve essere un CanonicalMessageType, ricevuto {type(self.message_type)}.")

        # Congela ricorsivamente i metadati per deep immutability
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))

