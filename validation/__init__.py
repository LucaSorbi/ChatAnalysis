"""
validation package
------------------
Foundation per il layer di Validazione della pipeline forense:

DATI ORIGINALI
  → IMPORTER
  → RawRecord
  → VALIDAZIONE    <-- questo layer
  → NORMALIZZAZIONE (futuro)
  → ENTITY RESOLUTION (futuro)
  → UnifiedMessage (futuro)
  → ANALISI AI (futuro)

Contratti e modelli esposti:
- ValidationSeverity: enum con livelli INFO, WARNING, ERROR
- ValidationIssue: dataclass immutabile descrittiva di un'anomalia
- ValidationResult: contenitore del RawRecord invariato e delle relative issue
- BaseValidator: classe base astratta con validate() e validate_stream()
- RecordValidator: validatore concreto source-aware
"""
from __future__ import annotations

from validation.base import BaseValidator
from validation.models import ValidationIssue, ValidationResult, ValidationSeverity
from validation.validator import RecordValidator

__all__ = [
    "BaseValidator",
    "RecordValidator",
    "ValidationIssue",
    "ValidationResult",
    "ValidationSeverity",
]
