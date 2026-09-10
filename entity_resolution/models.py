"""
entity_resolution/models.py
---------------------------
Modelli di dati per il layer di Entity Resolution Foundation.

Principi:
- Immutabilità profonda: tutti i modelli sono frozen dataclass con tuple e MappingProxyType.
- Provenance tracciabile: ogni riferimento preserva source_name e source_record_id.
- Determinismo: livelli di evidenza discreti (EXACT, STRONG, WEAK, UNRESOLVED), nessuna probabilità AI.
- Nessun UnifiedMessage: non produce messaggi unificati né Participant/Chat definitivi.
- Nessuna eliminazione: DuplicateCandidate raggruppa candidati duplicati senza cancellarli.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

from normalization.models import NormalizedActor, NormalizedRecord


class EvidenceLevel(str, Enum):
    """
    Livello di affidabilità deterministica dell'evidenza di risoluzione.
    """
    EXACT = "EXACT"              # Corrispondenza identica su identificatore univoco (es. stesso JID completo)
    STRONG = "STRONG"            # Corrispondenza forte (es. numero telefonico canonicalizzato compatibile)
    WEAK = "WEAK"                # Indizio debole insufficiente da solo (es. testo simile, timestamp contiguo)
    UNRESOLVED = "UNRESOLVED"    # Entità o riferimento non risolto (es. group_participant_A, ChatId senza mapping)


class EvidenceType(str, Enum):
    """
    Tipologia di evidenza deterministica utilizzata per il collegamento.
    """
    JID_EXACT = "JID_EXACT"                      # JID WhatsApp completo identico tra due sorgenti
    PHONE_CANONICAL = "PHONE_CANONICAL"          # Numero telefonico canonicalizzato identico
    PHONE_JID_LOCAL = "PHONE_JID_LOCAL"          # Numero telefonico coincidente con local part numerico del JID
    GROUP_JID_EXACT = "GROUP_JID_EXACT"          # JID gruppo WhatsApp (@g.us) tracciato come chat/gruppo
    LOCAL_USER_EXACT = "LOCAL_USER_EXACT"        # Identificativo proprietario dispositivo (LOCAL_USER)
    UNRESOLVED_ALIAS = "UNRESOLVED_ALIAS"        # Alias pseudonimizzato senza collegamento dimostrabile
    UNRESOLVED_CHAT_ID = "UNRESOLVED_CHAT_ID"    # Identificatore chat privo di metadati di associazione
    UNRESOLVED_HEURISTIC = "UNRESOLVED_HEURISTIC"# Euristica debole rifiutata per prevenire false unificazioni


@dataclass(frozen=True)
class EntityReference:
    """
    Puntatore immutabile all'attore di un record sorgente specifico.
    """
    source_name: str
    source_record_id: str
    actor_role: str                              # 'sender', 'recipient', 'contact', 'local_user', 'group_participant', 'chat', 'group', 'unknown'
    raw_value: str
    actor_type: str                              # 'jid', 'phone', 'alias', 'chat_id', 'local_user', 'group', 'unknown'
    normalized_value: str | None = None          # Valore standardizzato (es. JID completo o telefono ripulito)

    def __post_init__(self) -> None:
        if not self.source_name or not str(self.source_name).strip():
            raise ValueError("source_name non può essere vuoto.")
        if not self.source_record_id or not str(self.source_record_id).strip():
            raise ValueError("source_record_id non può essere vuoto.")
        valid_roles = (
            "sender", "recipient", "contact", "local_user",
            "group_participant", "chat", "group", "unknown",
        )
        if self.actor_role not in valid_roles:
            raise ValueError(f"actor_role '{self.actor_role}' non valido.")
        valid_types = ("phone", "jid", "alias", "chat_id", "local_user", "group", "unknown")
        if self.actor_type not in valid_types:
            raise ValueError(f"actor_type '{self.actor_type}' non valido.")


@dataclass(frozen=True)
class ResolutionEvidence:
    """
    Tracciamento immutabile dell'evidenza deterministica tra riferimenti.
    """
    evidence_type: EvidenceType
    evidence_level: EvidenceLevel
    matched_value: str
    reason: str
    source_records: tuple[tuple[str, str], ...]  # Tupla di (source_name, source_record_id)


@dataclass(frozen=True)
class CandidateEntity:
    """
    Entità candidata provvisoria (NON è un Participant canonico definitivo).
    Identificata da un ID tecnico non persistente 'entity_candidate:<n>'.
    """
    candidate_id: str                            # Es. 'entity_candidate:1'
    candidate_identifier: str                    # Identificatore principale (JID, telefono canonico, LOCAL_USER)
    entity_type: str                             # 'jid', 'phone', 'group', 'local_user', 'unresolved'
    references: tuple[EntityReference, ...]
    evidence_chain: tuple[ResolutionEvidence, ...]
    display_names: tuple[str, ...] = ()          # Eventuali nomi associati (es. da wa.db contacts)

    def __init__(
        self,
        candidate_id: str,
        candidate_identifier: str | None = None,
        entity_type: str = "unresolved",
        references: tuple[EntityReference, ...] = (),
        evidence_chain: tuple[ResolutionEvidence, ...] = (),
        display_names: tuple[str, ...] = (),
        *,
        canonical_identifier: str | None = None,
    ) -> None:
        ident = candidate_identifier if candidate_identifier is not None else canonical_identifier
        if ident is None:
            raise ValueError("candidate_identifier o canonical_identifier deve essere specificato.")
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "candidate_identifier", ident)
        object.__setattr__(self, "entity_type", entity_type)
        object.__setattr__(self, "references", references)
        object.__setattr__(self, "evidence_chain", evidence_chain)
        object.__setattr__(self, "display_names", display_names)

    @property
    def canonical_identifier(self) -> str:
        """Alias retrocompatibile per candidate_identifier."""
        return self.candidate_identifier


@dataclass(frozen=True)
class DuplicateCandidate:
    """
    Raggruppamento immutabile di record candidati duplicati.
    I record d'origine rimangono TUTTI preservati e NON vengono eliminati.
    """
    candidate_id: str                            # Es. 'duplicate_candidate:1'
    records: tuple[tuple[str, str], ...]         # Tupla di (source_name, source_record_id)
    reason: str
    confidence: EvidenceLevel


@dataclass(frozen=True)
class ResolutionResult:
    """
    Risultato globale dell'esecuzione della Entity Resolution Foundation.
    """
    candidate_entities: tuple[CandidateEntity, ...]
    unresolved_references: tuple[EntityReference, ...]
    duplicate_candidates: tuple[DuplicateCandidate, ...]
    total_records_processed: int
    metadata: MappingProxyType[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
