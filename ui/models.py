"""
ui/models.py
------------
Data Transfer Objects (DTO), enum e modelli di stato per il layer UI Streamlit.

Principi:
- Disaccoppiamento rigoroso: i modelli UI incapsulano solo concetti di presentazione
  e filtri, senza alterare i modelli di dominio sottostanti.
- Immutabilità: i DTO usano dataclass(frozen=True).
- Nessuna dipendenza diretta da Streamlit: questo modulo è puro Python e testabile isolatamente.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Sequence


class DatasetMode(str, Enum):
    """Modalità del dataset attualmente in memoria."""
    NONE = "NONE"
    DEMO = "DEMO"
    FILE = "FILE"


class SourceFormat(str, Enum):
    """Formati di sorgente previsti per l'ingestion forense."""
    WHATSAPP_MSGSTORE = "WhatsApp msgstore (SQLite)"
    WHATSAPP_WA = "WhatsApp wa.db (SQLite)"
    CELLEBRITE_CSV = "Cellebrite (CSV)"
    CELLEBRITE_JSON = "Cellebrite (JSON)"
    CELLEBRITE_XML = "Cellebrite (XML)"


class TopicFilterDecision(str, Enum):
    """Opzioni di filtro per le decisioni dei Topic Detection."""
    ALL = "ALL"
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class DocumentSummary:
    """Riepilogo aggregato del documento e dei risultati tematici per la Panoramica."""
    document_id: str
    bundle_count: int
    section_count: int
    counts_by_source_type: Mapping[str, int]
    languages: tuple[str, ...]
    topic_detection_count: int
    topic_discovery_count: int


@dataclass(frozen=True)
class EvidenceFilterCriteria:
    """Criteri di filtraggio per l'esplorazione delle sezioni di evidenza."""
    source_type: Optional[str] = None
    language: Optional[str] = None
    source_name: Optional[str] = None


class IngestionStatus(str, Enum):
    """Stato del processo di ingestion di un file reale."""
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    AUXILIARY_ONLY = "AUXILIARY_ONLY"


@dataclass(frozen=True)
class ImportedConversationInfo:
    """Informazioni descrittive non sensibili per una singola conversazione importata."""
    document_id: str
    chat_id: Optional[str]
    display_label: str
    bundle_count: int
    section_count: int
    languages: tuple[str, ...]
    source_name: str


@dataclass(frozen=True)
class IngestionRequest:
    """Richiesta di ingestion di un file caricato dall'utente."""
    source_format: SourceFormat
    filename: str
    file_bytes: bytes | Any
    companion_filename: Optional[str] = None
    companion_bytes: Optional[bytes | Any] = None


@dataclass(frozen=True)
class IngestionSummary:
    """Sintesi non sensibile dell'ingestion eseguita su un file reale."""
    source_format: SourceFormat
    original_filename: str
    sha256: str
    file_size_bytes: int
    raw_record_count: int
    validation_issue_count: int
    normalized_record_count: int
    unified_message_count: int
    conversation_count: int
    auxiliary_record_count: int
    warnings: tuple[str, ...] = ()
    available_conversations: tuple[ImportedConversationInfo, ...] = ()
    selected_document_id: Optional[str] = None
    companion_filename: Optional[str] = None
    companion_sha256: Optional[str] = None
    status: IngestionStatus = IngestionStatus.SUCCESS

    @property
    def conversations(self) -> tuple[ImportedConversationInfo, ...]:
        """Alias compatibile per available_conversations."""
        return self.available_conversations


@dataclass(frozen=True)
class IngestionResult:
    """Risultato completo dell'ingestion contenente summary e documenti associati."""
    summary: IngestionSummary
    documents: Mapping[str, Any]
    error_stage: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def document_list(self) -> tuple[Any, ...]:
        """Elenco ordinato dei documenti estratti."""
        return tuple(self.documents.values())

    @property
    def status(self) -> IngestionStatus:
        """Alias per summary.status."""
        return self.summary.status


