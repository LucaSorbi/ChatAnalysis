"""
tests/unit/test_validator.py
----------------------------
Test unitari per RecordValidator:
- Non-distruttività (RawRecord identico, nessun campo alterato o normalizzato)
- Streaming incrementale (validate_stream su generatore)
- Regole strutturali minime e source-aware (CSV timestamp vuoto, JSON, XML, WhatsApp)
- Questioni aperte non trattate come errori (JID non risolti, assenza ChatId in XML)
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from importer.models import RawRecord
from validation.base import BaseValidator
from validation.models import ValidationResult, ValidationSeverity
from validation.validator import RecordValidator


def make_record(
    source_name: str,
    source_path: str = "/dummy/path",
    source_record_id: str = "1",
    record_type: str = "message",
    raw_fields: dict | None = None,
    media_reference: str | None = None,
    metadata: dict | None = None,
) -> RawRecord:
    """Helper per costruire RawRecord nei test unitari di validazione."""
    return RawRecord(
        source_name=source_name,
        source_path=source_path,
        source_record_id=source_record_id,
        record_type=record_type,
        raw_fields=raw_fields if raw_fields is not None else {},
        media_reference=media_reference,
        metadata=metadata if metadata is not None else {},
    )


@pytest.fixture
def validator() -> RecordValidator:
    return RecordValidator()


# ---------------------------------------------------------------------------
# Test Non-Distruttività (C7)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestValidatorNonDestructive:

    def test_record_identity_preserved(self, validator):
        record = make_record(
            source_name="cellebrite_csv",
            source_path="/test/path.csv",
            source_record_id="row:1",
            record_type="message",
            raw_fields={"TimeStamp": "", "Body": "Raw content", "From": "123"},
            media_reference=None,
            metadata={"row": 1},
        )
        fields_before = dict(record.raw_fields)
        meta_before = dict(record.metadata)

        result = validator.validate(record)

        # Il record restituito è lo stesso oggetto invariato
        assert result.record is record
        assert dict(record.raw_fields) == fields_before
        assert dict(record.metadata) == meta_before
        assert record.raw_fields["TimeStamp"] == ""  # Non normalizzato in datetime o None
        assert record.raw_fields["Body"] == "Raw content"

    def test_no_filesystem_mutation_or_network(self, validator, tmp_path):
        target_file = tmp_path / "evidence.txt"
        target_file.write_text("IMMUTABLE FORENSIC CONTENT", encoding="utf-8")
        mtime_before = target_file.stat().st_mtime_ns

        record = make_record(
            source_name="generic",
            source_path=str(target_file),
            source_record_id="id:1",
            record_type="message",
            raw_fields={"data": "test"},
        )
        validator.validate(record)

        assert target_file.stat().st_mtime_ns == mtime_before
        assert target_file.read_text(encoding="utf-8") == "IMMUTABLE FORENSIC CONTENT"


# ---------------------------------------------------------------------------
# Test Streaming (C8)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestValidatorStreaming:

    def test_validate_stream_is_generator(self, validator):
        records = [
            make_record("src", "/p", "1", "msg", {"k": "v1"}),
            make_record("src", "/p", "2", "msg", {"k": "v2"}),
        ]
        stream = validator.validate_stream(iter(records))
        assert inspect.isgenerator(stream)

        first_res = next(stream)
        assert isinstance(first_res, ValidationResult)
        assert first_res.record.source_record_id == "1"

        second_res = next(stream)
        assert isinstance(second_res, ValidationResult)
        assert second_res.record.source_record_id == "2"

        with pytest.raises(StopIteration):
            next(stream)


# ---------------------------------------------------------------------------
# Test Regole Generiche RawRecord (C5)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestGenericRules:

    def test_empty_raw_fields_triggers_warning(self, validator):
        record = make_record("src", "/p", "1", "msg", {})
        result = validator.validate(record)
        assert result.has_warnings is True
        codes = [i.code for i in result.issues]
        assert "GENERIC_EMPTY_RAW_FIELDS" in codes


# ---------------------------------------------------------------------------
# Test Cellebrite CSV Rules (C5)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCellebriteCsvValidation:

    def test_empty_timestamp_produces_warning_issue(self, validator):
        """
        Nel dataset CSV osservato esiste almeno un timestamp vuoto.
        Deve produrre CSV_EMPTY_TIMESTAMP (WARNING), senza normalizzare o scartare.
        """
        record = make_record(
            source_name="cellebrite_csv",
            source_path="/path/to/messages.csv",
            source_record_id="row:15",
            record_type="message",
            raw_fields={
                "TimeStamp": "",
                "From": "+390000000001",
                "To": "+390000000002",
                "Body": "Messaggio senza data",
            },
        )
        result = validator.validate(record)
        assert result.is_valid is True  # WARNING non rende invalido
        assert result.has_warnings is True

        issue = next(i for i in result.issues if i.code == "CSV_EMPTY_TIMESTAMP")
        assert issue.severity == ValidationSeverity.WARNING
        assert issue.field_path == "TimeStamp"
        # Il valore grezzo in raw_fields è intatto
        assert result.record.raw_fields["TimeStamp"] == ""

    def test_populated_timestamp_has_no_timestamp_warning(self, validator):
        record = make_record(
            source_name="cellebrite_csv",
            source_path="/path/to/messages.csv",
            source_record_id="row:1",
            record_type="message",
            raw_fields={
                "TimeStamp": "2024-04-23 22:30:15",
                "From": "+390000000001",
                "To": "+390000000002",
                "Body": "Messaggio con data",
            },
        )
        result = validator.validate(record)
        assert not any(i.code == "CSV_EMPTY_TIMESTAMP" for i in result.issues)

    # ---------------------------------------------------------------------------
# Test Cellebrite JSON Rules (C5)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCellebriteJsonValidation:

    def test_missing_timestamp_produces_warning(self, validator):
        record = make_record(
            source_name="cellebrite_json",
            source_path="/path/to/messages.json",
            source_record_id="0",
            record_type="message",
            raw_fields={"sender": "+3901", "content": {"text": "test", "media_path": None}},
        )
        result = validator.validate(record)
        assert any(i.code == "JSON_MISSING_TIMESTAMP" for i in result.issues)

    def test_valid_json_record_produces_no_warning(self, validator):
        record = make_record(
            source_name="cellebrite_json",
            source_path="/path/to/messages.json",
            source_record_id="1",
            record_type="message",
            raw_fields={
                "id": "msg_00001",
                "chat_id": "chat_1",
                "sender": "+3901",
                "timestamp": "2024-10-31T16:50:00+00:00",
                "type": "text",
                "content": {"text": "test", "media_path": None},
                "metadata": {"deleted": False, "forwarded": False, "starred": False},
            },
        )
        result = validator.validate(record)
        assert result.is_valid is True
        assert len(result.issues) == 0


# ---------------------------------------------------------------------------
# Test Cellebrite XML Rules (C5)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCellebriteXmlValidation:

    def test_standard_xml_record_is_valid_without_chat_id_issues(self, validator):
        """
        L'assenza di ChatId nel formato XML UFDR osservato è una caratteristica strutturale
        globale del formato e non genera anomalie per-record.
        """
        record = make_record(
            source_name="cellebrite_xml",
            source_path="/path/to/report.xml",
            source_record_id="0",
            record_type="message",
            raw_fields={
                "Timestamp": "2024-04-23T20:30:15+00:00",
                "Sender": "+390000000001",
                "Body": "Test message",
                "Deleted": "false",
            },
        )
        result = validator.validate(record)
        assert result.is_valid is True
        assert result.has_errors is False
        assert len(result.issues) == 0

    def test_empty_timestamp_produces_warning(self, validator):
        record = make_record(
            source_name="cellebrite_xml",
            source_path="/path/to/report.xml",
            source_record_id="1",
            record_type="message",
            raw_fields={"Sender": "+3901", "Body": "No ts", "Deleted": "false"},
        )
        result = validator.validate(record)
        assert any(i.code == "XML_EMPTY_TIMESTAMP" for i in result.issues)

    def test_namespaced_timestamp_recognized(self, validator):
        record = make_record(
            source_name="cellebrite_xml",
            source_path="/path/to/report.xml",
            source_record_id="2",
            record_type="message",
            raw_fields={
                "{urn:ufdr}Timestamp": "2024-01-01T00:00:00+00:00",
                "{urn:ufdr}Sender": "+3901",
            },
        )
        result = validator.validate(record)
        assert not any(i.code == "XML_EMPTY_TIMESTAMP" for i in result.issues)


# ---------------------------------------------------------------------------
# Test WhatsApp Msgstore & Wa.db Rules (C5, C6)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestWhatsAppValidation:

    def test_unresolved_jid_is_not_validation_error(self, validator):
        """
        I JID non risolti (@s.whatsapp.net, @g.us) appartengono all'Entity Resolution.
        Non devono generare errori di validazione.
        """
        record = make_record(
            source_name="msgstore_db",
            source_path="/path/to/msgstore.db",
            source_record_id="101",
            record_type="message",
            raw_fields={
                "key_remote_jid": "123456789@s.whatsapp.net",
                "timestamp": 1713904215000,
                "data": "Hello WhatsApp",
            },
        )
        result = validator.validate(record)
        assert result.is_valid is True
        assert result.has_errors is False

    def test_anomalous_timestamp_produces_warning(self, validator):
        record = make_record(
            source_name="msgstore_db",
            source_path="/path/to/msgstore.db",
            source_record_id="102",
            record_type="message",
            raw_fields={
                "key_remote_jid": "123456789@s.whatsapp.net",
                "timestamp": -1,
                "data": "Corrupted timestamp",
            },
        )
        result = validator.validate(record)
        assert any(i.code == "MSGSTORE_ANOMALOUS_TIMESTAMP" for i in result.issues)

    def test_wa_db_null_status_is_valid(self, validator):
        """
        In wa.db, status=None è un valore osservato e valido.
        Non deve essere trattato come anomalia.
        """
        record = make_record(
            source_name="wa_db",
            source_path="/path/to/wa.db",
            source_record_id="1",
            record_type="contact",
            raw_fields={
                "jid": "123456789@s.whatsapp.net",
                "display_name": "Mario Rossi",
                "status": None,
                "phone_number": "0039 123 456789",
            },
        )
        result = validator.validate(record)
        assert result.is_valid is True
        assert result.has_errors is False
        assert result.has_warnings is False


# ---------------------------------------------------------------------------
# Test Semantica Severity e is_valid (A6, A7)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestValidationSeveritySemantics:

    def test_no_issues_is_valid(self):
        record = make_record("test", "/p", "1", "msg")
        result = ValidationResult(record=record, issues=())
        assert result.is_valid is True
        assert result.has_errors is False
        assert result.has_warnings is False

    def test_only_info_is_valid(self):
        from validation.models import ValidationIssue
        record = make_record("test", "/p", "1", "msg")
        issue = ValidationIssue("INFO_CODE", ValidationSeverity.INFO, "info msg", "test", "1")
        result = ValidationResult(record=record, issues=(issue,))
        assert result.is_valid is True
        assert result.has_errors is False
        assert result.has_warnings is False

    def test_only_warning_is_valid(self):
        from validation.models import ValidationIssue
        record = make_record("test", "/p", "1", "msg")
        issue = ValidationIssue("WARN_CODE", ValidationSeverity.WARNING, "warn msg", "test", "1")
        result = ValidationResult(record=record, issues=(issue,))
        assert result.is_valid is True
        assert result.has_errors is False
        assert result.has_warnings is True

    def test_at_least_error_makes_invalid(self):
        from validation.models import ValidationIssue
        record = make_record("test", "/p", "1", "msg")
        issue_w = ValidationIssue("WARN_CODE", ValidationSeverity.WARNING, "warn msg", "test", "1")
        issue_e = ValidationIssue("ERR_CODE", ValidationSeverity.ERROR, "err msg", "test", "1")
        result = ValidationResult(record=record, issues=(issue_w, issue_e))
        assert result.is_valid is False
        assert result.has_errors is True

