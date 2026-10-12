"""
validation/models.py
--------------------
Modelli immutabili per il layer di Validazione.

Principi:
- VALIDAZIONE != NORMALIZZAZIONE: il validator osserva il RawRecord,
  rileva anomalie o incompletezze e produce un ValidationResult.
- Non altera, non corregge, non normalizza e non scarta il RawRecord originale.
- Immutabilità profonda: ValidationIssue e ValidationResult sono congelati (frozen=True).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from importer.models import RawRecord


class ValidationSeverity(str, Enum):
    """
    Livello di severità di un'anomalia rilevata durante la validazione.

    - INFO: osservazione informativa o caratteristica strutturale della sorgente
      (es. assenza di ChatId in esportazioni XML UFDR Cellebrite).
    - WARNING: anomalia o dato incompleto che non impedisce l'elaborazione
      ma richiede attenzione a valle (es. timestamp vuoto in una riga CSV).
    - ERROR: difetto grave a livello di record che rende l'informazione incoerente
      o strutturalmente non utilizzabile.
    """
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ValidationIssue:
    """
    Descrizione immutabile e machine-readable di un'anomalia rilevata in un RawRecord.

    Campi:
    ------
    code : str
        Identificatore stabile e machine-readable (es. "CSV_EMPTY_TIMESTAMP").
    severity : ValidationSeverity
        Livello di gravità (INFO, WARNING, ERROR).
    message : str
        Descrizione human-readable dell'anomalia osservata.
    source_name : str
        Identificativo della sorgente di provenienza (ereditato dal RawRecord).
    source_record_id : str
        Identificativo del record nella sorgente (ereditato dal RawRecord).
    field_path : str | None
        Percorso o nome del campo coinvolto nell'anomalia (es. "TimeStamp").
    """
    code: str
    severity: ValidationSeverity
    message: str
    source_name: str
    source_record_id: str
    field_path: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("ValidationIssue.code deve essere una stringa non vuota.")
        if not isinstance(self.severity, ValidationSeverity):
            raise ValueError(f"ValidationIssue.severity deve essere un'istanza di ValidationSeverity, ricevuto {type(self.severity)}.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("ValidationIssue.message deve essere una stringa non vuota.")
        if not isinstance(self.source_name, str) or not self.source_name.strip():
            raise ValueError("ValidationIssue.source_name deve essere una stringa non vuota.")
        if not isinstance(self.source_record_id, str) or not self.source_record_id.strip():
            raise ValueError("ValidationIssue.source_record_id deve essere una stringa non vuota.")


@dataclass(frozen=True)
class ValidationResult:
    """
    Risultato immutabile della validazione di un RawRecord.

    Mantiene il riferimento al RawRecord ORIGINALE invariato insieme
    alla sequenza immutabile di problemi (ValidationIssue) rilevati.

    Campi:
    ------
    record : RawRecord
        Il RawRecord originale, non modificato in alcun campo.
    issues : tuple[ValidationIssue, ...]
        Sequenza immutabile di anomalie riscontrate durante la validazione.
    """
    record: RawRecord
    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.record, RawRecord):
            raise ValueError(f"ValidationResult.record deve essere un'istanza di RawRecord, ricevuto {type(self.record)}.")

        # Assicura che issues sia sempre una tupla immutabile di ValidationIssue
        if not isinstance(self.issues, tuple):
            validated_issues = tuple(self.issues)
            object.__setattr__(self, "issues", validated_issues)

        for issue in self.issues:
            if not isinstance(issue, ValidationIssue):
                raise ValueError(f"Tutti gli elementi di issues devono essere ValidationIssue, ricevuto {type(issue)}.")

    @property
    def is_valid(self) -> bool:
        """True se il record non presenta issue con severità ERROR."""
        return not self.has_errors

    @property
    def has_errors(self) -> bool:
        """True se è presente almeno una issue con severità ERROR."""
        return any(issue.severity == ValidationSeverity.ERROR for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        """True se è presente almeno una issue con severità WARNING."""
        return any(issue.severity == ValidationSeverity.WARNING for issue in self.issues)

    @property
    def has_issues(self) -> bool:
        """True se è presente almeno una issue di qualsiasi severità."""
        return len(self.issues) > 0

    def issues_by_severity(self, severity: ValidationSeverity) -> tuple[ValidationIssue, ...]:
        """Restituisce le sole issue con la severità specificata."""
        return tuple(issue for issue in self.issues if issue.severity == severity)
