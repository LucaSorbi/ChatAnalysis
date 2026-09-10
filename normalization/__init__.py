"""
normalization package
---------------------
Foundation per il layer di Normalizzazione della pipeline forense:

DATI ORIGINALI
  → IMPORTER
  → RawRecord
  → VALIDAZIONE
  → NORMALIZZAZIONE    <-- questo layer
  → ENTITY RESOLUTION (futuro)
  → UnifiedMessage (futuro)
  → ANALISI AI (futuro)

Modelli e classi esposti:
- NormalizedRecord: contenitore immutabile con provenance verso RawRecord e ValidationResult
- NormalizedTimestamp: rappresentazione rigorosa (KNOWN_UTC, NAIVE_UNKNOWN, ABSENT)
- TimestampTzStatus: enum dello stato di fuso orario
- NormalizedActor: attore normalizzato deterministicamente (phone, jid, alias, chat_id, unknown)
- CanonicalMessageType: enum con i tipi di messaggio unificati
- BaseNormalizer: interfaccia astratta con normalize() e normalize_stream()
- RecordNormalizer: implementazione concreta source-aware
"""
from __future__ import annotations

from normalization.base import BaseNormalizer
from normalization.models import (
    CanonicalMessageType,
    NormalizedActor,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from normalization.normalizer import RecordNormalizer

__all__ = [
    "BaseNormalizer",
    "CanonicalMessageType",
    "NormalizedActor",
    "NormalizedRecord",
    "NormalizedTimestamp",
    "RecordNormalizer",
    "TimestampTzStatus",
]
