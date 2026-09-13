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
