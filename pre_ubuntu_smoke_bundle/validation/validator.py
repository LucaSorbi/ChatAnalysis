"""
validation/validator.py
-----------------------
Implementazione concreta del validatore source-aware: RecordValidator.

Principi:
- VALIDAZIONE != NORMALIZZAZIONE:
  Osserva il RawRecord, rileva anomalie o caratteristiche strutturali, e produce
  un ValidationResult contenente il RawRecord invariato e le relative ValidationIssue.
- Non altera, non corregge, non normalizza valori.
- Non scarta o filtra record.
- Non effettua I/O o interrogazioni esterne.
- Streaming: supporta validate_stream() lazy su iteratori.
"""
from __future__ import annotations

from typing import Any

from importer.models import RawRecord
from validation.base import BaseValidator
from validation.models import ValidationIssue, ValidationResult, ValidationSeverity


class RecordValidator(BaseValidator):
    """
    Validatore concreto per RawRecord prodotti dai 5 importer forensi strutturati.

    Applica controlli strutturali minimi e regole source-aware calibrate sulle
    evidenze realmente osservate nei dataset del progetto.
    """

    def validate(self, record: RawRecord) -> ValidationResult:
        """
        Valida un singolo RawRecord in modo strettamente non-distruttivo.

        Restituisce un ValidationResult contenente il RawRecord identico all'originale
        e una tupla di ValidationIssue riscontrate.
        """
        issues: list[ValidationIssue] = []

        # 1. Controlli generici a livello di RawRecord
        self._validate_generic(record, issues)

        # 2. Controlli source-aware specifici
        if record.source_name == "cellebrite_csv":
            self._validate_cellebrite_csv(record, issues)
        elif record.source_name == "cellebrite_json":
            self._validate_cellebrite_json(record, issues)
        elif record.source_name == "cellebrite_xml":
            self._validate_cellebrite_xml(record, issues)
        elif record.source_name == "msgstore_db":
            self._validate_whatsapp_msgstore(record, issues)
        elif record.source_name == "wa_db":
            self._validate_whatsapp_wa(record, issues)
        elif record.source_name == "whatsapp_export":
            self._validate_whatsapp_export(record, issues)

        return ValidationResult(record=record, issues=tuple(issues))

    # ------------------------------------------------------------------
    # Regole generiche
    # ------------------------------------------------------------------

    def _validate_generic(self, record: RawRecord, issues: list[ValidationIssue]) -> None:
        """Verifiche strutturali minime comuni a tutti i RawRecord."""
        if len(record.raw_fields) == 0:
            issues.append(
                ValidationIssue(
                    code="GENERIC_EMPTY_RAW_FIELDS",
                    severity=ValidationSeverity.WARNING,
                    message="Il record non contiene alcun campo in raw_fields.",
                    source_name=record.source_name,
                    source_record_id=record.source_record_id,
                    field_path="raw_fields",
                )
            )

    # ------------------------------------------------------------------
    # Regole Cellebrite CSV
    # ------------------------------------------------------------------

    def _validate_cellebrite_csv(self, record: RawRecord, issues: list[ValidationIssue]) -> None:
        """
        Regole di validazione per Cellebrite CSV.

        Nel dataset CSV osservato è presente almeno un timestamp vuoto.
        Questo costituisce una ValidationIssue (WARNING), ma il dato non viene
        normalizzato, trasformato né scartato.
        """
        raw = record.raw_fields

        # Timestamp vuoto o non valorizzato
        ts = raw.get("TimeStamp")
        if ts is None or (isinstance(ts, str) and not ts.strip()):
            issues.append(
                ValidationIssue(
                    code="CSV_EMPTY_TIMESTAMP",
                    severity=ValidationSeverity.WARNING,
                    message="Il campo TimeStamp nel record CSV è vuoto o non specificato.",
                    source_name=record.source_name,
                    source_record_id=record.source_record_id,
                    field_path="TimeStamp",
                )
            )

    # ------------------------------------------------------------------
    # Regole Cellebrite JSON
    # ------------------------------------------------------------------

    def _validate_cellebrite_json(self, record: RawRecord, issues: list[ValidationIssue]) -> None:
        """
        Regole di validazione per Cellebrite JSON.

        Distingue campi obbligatori da campi opzionali.
        """
        raw = record.raw_fields

        # Timestamp mancante o vuoto
        ts = raw.get("timestamp")
        if ts is None or (isinstance(ts, str) and not ts.strip()):
            issues.append(
                ValidationIssue(
                    code="JSON_MISSING_TIMESTAMP",
                    severity=ValidationSeverity.WARNING,
                    message="Il campo 'timestamp' è assente o vuoto nell'oggetto JSON.",
                    source_name=record.source_name,
                    source_record_id=record.source_record_id,
                    field_path="timestamp",
                )
            )

    # ------------------------------------------------------------------
    # Regole Cellebrite XML
    # ------------------------------------------------------------------

    def _validate_cellebrite_xml(self, record: RawRecord, issues: list[ValidationIssue]) -> None:
        """
        Regole di validazione per Cellebrite XML (UFDR).

        L'assenza di ChatId nel formato XML osservato è una caratteristica
        strutturale globale della sorgente (non un'anomalia per-record);
        viene documentata a livello di design e non emessa come issue per ogni messaggio.
        """
        raw = record.raw_fields

        # Ricerca timestamp con o senza namespace Clark
        ts_val: Any = None
        for k, v in raw.items():
            if k == "Timestamp" or k.endswith("}Timestamp"):
                ts_val = v
                break

        if ts_val is None or (isinstance(ts_val, str) and not ts_val.strip()):
            issues.append(
                ValidationIssue(
                    code="XML_EMPTY_TIMESTAMP",
                    severity=ValidationSeverity.WARNING,
                    message="Il tag Timestamp nel record XML è assente o vuoto.",
                    source_name=record.source_name,
                    source_record_id=record.source_record_id,
                    field_path="Timestamp",
                )
            )

    # ------------------------------------------------------------------
    # Regole WhatsApp msgstore.db
    # ------------------------------------------------------------------

    def _validate_whatsapp_msgstore(self, record: RawRecord, issues: list[ValidationIssue]) -> None:
        """
        Regole di validazione per WhatsApp msgstore.db.

        I JID non risolti (es. '@s.whatsapp.net', '@g.us') appartengono alla successiva
        fase di Entity Resolution e NON sono considerati errori di validazione.
        """
        raw = record.raw_fields

        # Se il record è di tipo message, verifica che il timestamp sia positivo
        if record.record_type == "message":
            ts = raw.get("timestamp")
            if ts is not None and isinstance(ts, (int, float)) and ts <= 0:
                issues.append(
                    ValidationIssue(
                        code="MSGSTORE_ANOMALOUS_TIMESTAMP",
                        severity=ValidationSeverity.WARNING,
                        message=f"Il timestamp SQLite per il messaggio è anomalo o non positivo ({ts}).",
                        source_name=record.source_name,
                        source_record_id=record.source_record_id,
                        field_path="timestamp",
                    )
                )

    # ------------------------------------------------------------------
    # Regole WhatsApp wa.db
    # ------------------------------------------------------------------

    def _validate_whatsapp_wa(self, record: RawRecord, issues: list[ValidationIssue]) -> None:
        """
        Regole di validazione per WhatsApp wa.db (rubrica/contatti).

        Lo 'status' nullo (None) è un valore perfettamente valido e osservato;
        non costituisce anomalia. I numeri telefonici non formattati in E.164
        verranno normalizzati nei layer successivi.
        """
        raw = record.raw_fields
        jid = raw.get("jid")
        if jid is None or (isinstance(jid, str) and not jid.strip()):
            issues.append(
                ValidationIssue(
                    code="WA_DB_EMPTY_JID",
                    severity=ValidationSeverity.WARNING,
                    message="Il record contatto in wa.db presenta un JID vuoto o non specificato.",
                    source_name=record.source_name,
                    source_record_id=record.source_record_id,
                    field_path="jid",
                )
            )

    # ------------------------------------------------------------------
    # Regole WhatsApp Export (TXT / ZIP)
    # ------------------------------------------------------------------

    def _validate_whatsapp_export(self, record: RawRecord, issues: list[ValidationIssue]) -> None:
        """
        Regole di validazione per esportazioni WhatsApp (TXT / ZIP).

        Segnala warning non bloccanti se il timestamp è assente o vuoto.
        """
        raw = record.raw_fields
        ts = raw.get("raw_timestamp")
        if ts is None or (isinstance(ts, str) and not ts.strip()):
            issues.append(
                ValidationIssue(
                    code="WHATSAPP_EXPORT_EMPTY_TIMESTAMP",
                    severity=ValidationSeverity.WARNING,
                    message="Il record WhatsApp export presenta un raw_timestamp vuoto o non specificato.",
                    source_name=record.source_name,
                    source_record_id=record.source_record_id,
                    field_path="raw_timestamp",
                )
            )
