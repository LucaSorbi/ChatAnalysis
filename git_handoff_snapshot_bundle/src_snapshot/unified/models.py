"""
unified/models.py
-----------------
Modelli unificati immutabili per il layer Unified Model Foundation:
- Participant: attore della conversazione associato a un'identità deterministica.
- Chat: contenitore di conversazione (1-to-1 o di gruppo) con metadati e partecipanti.
- UnifiedMessage: rappresentazione canonica immutabile del messaggio con piena provenance.

Principi architetturali:
- Piena provenance: ogni UnifiedMessage mantiene il riferimento al NormalizedRecord
  (e a cascata al RawRecord e al ValidationResult d'origine).
- Immutabilità profonda: frozen dataclass, tuple per collezioni, MappingProxyType per metadati.
- Integrità temporale: NormalizedTimestamp è preservato con il suo stato di certezza del fuso orario
  (KNOWN_UTC, NAIVE_UNKNOWN, ABSENT). Nessuna fusione o conversione fittizia di timestamp naive.
- Non-distruttività: nessun messaggio duplicato viene scartato o rimosso. I messaggi candidati
  duplicati sono esplicitamente collegati tramite duplicate_candidate_ids.
- Identificatore deterministico: message_id ha formato standard 'unified:{source_name}:{source_record_id}'.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from core.immutability import freeze_structural
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
)


@dataclass(frozen=True)
class Participant:
    """
    Rappresentazione unificata e immutabile di un partecipante a una conversazione.

    Campi:
    ------
    participant_id : str
        Identificatore univoco del partecipante (es. 'participant:+390000000001' o 'participant:LOCAL_USER').
    identifier : str
        Valore dell'identificativo principale (es. numero di telefono, JID, 'LOCAL_USER', alias).
    display_name : str | None
        Nome descrittivo o di rubrica (es. da wa.db contacts o vCard), se disponibile.
    entity_candidate_id : str | None
        ID dell'entità candidata (es. 'entity_candidate:1') dal layer di Entity Resolution, se risolto.
    is_local_user : bool
        True se il partecipante è il proprietario del dispositivo forense (LOCAL_USER).
    metadata : MappingProxyType[str, Any]
        Metadati aggiuntivi immutabili.
    """
    participant_id: str
    identifier: str
    display_name: str | None = None
    entity_candidate_id: str | None = None
    is_local_user: bool = False
    metadata: MappingProxyType[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.participant_id or not str(self.participant_id).strip():
            raise ValueError("participant_id non può essere vuoto.")
        if not self.identifier or not str(self.identifier).strip():
            raise ValueError("identifier non può essere vuoto.")
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))


@dataclass(frozen=True)
class Chat:
    """
    Rappresentazione unificata e immutabile di un contenitore di chat o conversazione.

    Campi:
    ------
    chat_id : str
        Identificatore canonico della chat (es. 'chat:msgstore_db:00000000001-0000000000@g.us').
    chat_type : str
        Tipologia di chat: 'direct' (1-to-1), 'group' (gruppo), 'unknown'.
    title : str | None
        Titolo della chat o oggetto del gruppo (es. 'Gruppo_Sintetico_01'), se noto.
    participants : tuple[Participant, ...]
        Partecipanti noti alla conversazione.
    source_name : str
        Nome della sorgente da cui la chat è stata originata o rilevata.
    metadata : MappingProxyType[str, Any]
        Metadati immutabili della chat.
    """
    chat_id: str
    chat_type: str                                # 'direct', 'group', 'unknown'
    title: str | None = None
    participants: tuple[Participant, ...] = ()
    source_name: str = ""
    metadata: MappingProxyType[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.chat_id or not str(self.chat_id).strip():
            raise ValueError("chat_id non può essere vuoto.")
        if self.chat_type not in ("direct", "group", "unknown"):
            raise ValueError(f"chat_type '{self.chat_type}' non valido. Valori ammessi: 'direct', 'group', 'unknown'.")
        if not isinstance(self.participants, tuple):
            object.__setattr__(self, "participants", tuple(self.participants))
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))


@dataclass(frozen=True)
class UnifiedMessage:
    """
    Rappresentazione canonica immutabile del singolo messaggio forense.

    Mantiene la completa catena di provenance verso il NormalizedRecord d'origine
    e il rispettivo RawRecord, senza alcuna perdita d'informazione forense.

    Campi:
    ------
    message_id : str
        Identificatore deterministico nel formato 'unified:{source_name}:{source_record_id}'.
    source_name : str
        Nome della sorgente (es. 'msgstore_db', 'cellebrite_csv', 'cellebrite_json', ...).
    source_record_id : str
        Identificativo del record all'interno della sorgente originale.
    source_path : str
        Percorso assoluto del file sorgente originale.
    record_type : str
        Tipologia di record sorgente ('message').
    timestamp : NormalizedTimestamp
        Rappresentazione rigorosa del timestamp (stato UTC / NAIVE / ABSENT, naive_datetime, utc_datetime, raw_value).
    sender : Participant | None
        Partecipante mittente del messaggio.
    recipient : Participant | None
        Partecipante destinatario del messaggio (se 1-to-1 o noto).
    chat : Chat | None
        Contenitore di chat o conversazione a cui appartiene il messaggio.
    message_type : CanonicalMessageType
        Tipo canonico del messaggio (TEXT, IMAGE, AUDIO, VIDEO, SYSTEM, OTHER, UNKNOWN).
    raw_message_type : Any
        Valore nativo non interpretato del tipo messaggio.
    text_content : str | None
        Testo del messaggio, preservato fedelmente.
    media_reference : str | None
        Riferimento ad allegati o file multimediali, se presente.
    is_deleted : bool | None
        Indicazione booleana di cancellazione, o None se non determinabile dalla sorgente.
    raw_deleted : Any
        Valore grezzo del campo cancellazione.
    duplicate_candidate_ids : tuple[str, ...]
        Tupla di identificatori di DuplicateCandidate (es. ('duplicate_candidate:1',)) di cui
        questo messaggio fa parte, senza che sia avvenuta alcuna deduplicazione distruttiva.
    provenance_record : NormalizedRecord
        Riferimento diretto al NormalizedRecord originale.
    metadata : MappingProxyType[str, Any]
        Dizionario di metadati immutabile.
    """
    message_id: str
    source_name: str
    source_record_id: str
    source_path: str
    record_type: str
    timestamp: NormalizedTimestamp
    provenance_record: NormalizedRecord
    sender: Participant | None = None
    recipient: Participant | None = None
    chat: Chat | None = None
    message_type: CanonicalMessageType = CanonicalMessageType.UNKNOWN
    raw_message_type: Any = None
    text_content: str | None = None
    media_reference: str | None = None
    is_deleted: bool | None = None
    raw_deleted: Any = None
    duplicate_candidate_ids: tuple[str, ...] = ()
    metadata: MappingProxyType[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.message_id or not str(self.message_id).strip():
            raise ValueError("message_id non può essere vuoto.")
        if not self.source_name or not str(self.source_name).strip():
            raise ValueError("source_name non può essere vuoto.")
        if not self.source_record_id or not str(self.source_record_id).strip():
            raise ValueError("source_record_id non può essere vuoto.")
        if not isinstance(self.timestamp, NormalizedTimestamp):
            raise ValueError(f"timestamp deve essere un NormalizedTimestamp, ricevuto {type(self.timestamp)}.")
        if not isinstance(self.provenance_record, NormalizedRecord):
            raise ValueError(f"provenance_record deve essere un NormalizedRecord, ricevuto {type(self.provenance_record)}.")
        if not isinstance(self.message_type, CanonicalMessageType):
            raise ValueError(f"message_type deve essere un CanonicalMessageType, ricevuto {type(self.message_type)}.")
        if not isinstance(self.duplicate_candidate_ids, tuple):
            object.__setattr__(self, "duplicate_candidate_ids", tuple(self.duplicate_candidate_ids))
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))
