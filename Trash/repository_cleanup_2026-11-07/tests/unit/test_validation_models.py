"""
tests/unit/test_validation_models.py
------------------------------------
Test unitari per i modelli del layer di Validazione:
- ValidationSeverity
- ValidationIssue
- ValidationResult
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from importer.models import RawRecord
from validation.models import ValidationIssue, ValidationResult, ValidationSeverity


@pytest.fixture
def sample_raw_record() -> RawRecord:
    return RawRecord(
        source_name="cellebrite_csv",
        source_path="/path/to/messages.csv",
        source_record_id="row:2",
        record_type="message",
        raw_fields={"Source": "SMS", "Body": "Test message", "TimeStamp": ""},
        media_reference=None,
        metadata={"line": 2},
    )


# ---------------------------------------------------------------------------
# Test ValidationSeverity
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestValidationSeverity:

    def test_severity_values(self):
        assert ValidationSeverity.INFO.value == "INFO"
        assert ValidationSeverity.WARNING.value == "WARNING"
        assert ValidationSeverity.ERROR.value == "ERROR"

    def test_severity_string_compatibility(self):
        assert ValidationSeverity.INFO == "INFO"
        assert ValidationSeverity.WARNING == "WARNING"
        assert ValidationSeverity.ERROR == "ERROR"


# ---------------------------------------------------------------------------
# Test ValidationIssue
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestValidationIssue:

    def test_create_valid_issue(self):
        issue = ValidationIssue(
            code="CSV_EMPTY_TIMESTAMP",
            severity=ValidationSeverity.WARNING,
            message="Timestamp non presente",
            source_name="cellebrite_csv",
            source_record_id="row:2",
            field_path="TimeStamp",
        )
        assert issue.code == "CSV_EMPTY_TIMESTAMP"
        assert issue.severity == ValidationSeverity.WARNING
        assert issue.message == "Timestamp non presente"
        assert issue.source_name == "cellebrite_csv"
        assert issue.source_record_id == "row:2"
        assert issue.field_path == "TimeStamp"

    def test_issue_is_immutable(self):
        issue = ValidationIssue(
            code="TEST_CODE",
            severity=ValidationSeverity.INFO,
            message="Info msg",
            source_name="src",
            source_record_id="1",
        )
        with pytest.raises(FrozenInstanceError):
            issue.code = "MODIFIED"  # type: ignore[misc]

    def test_issue_invalid_inputs(self):
        with pytest.raises(ValueError, match="code"):
            ValidationIssue(
                code="",
                severity=ValidationSeverity.INFO,
                message="msg",
                source_name="src",
                source_record_id="1",
            )

        with pytest.raises(ValueError, match="severity"):
            ValidationIssue(
                code="CODE",
                severity="NOT_A_SEVERITY",  # type: ignore[arg-type]
                message="msg",
                source_name="src",
                source_record_id="1",
            )

        with pytest.raises(ValueError, match="message"):
            ValidationIssue(
                code="CODE",
                severity=ValidationSeverity.INFO,
                message="",
                source_name="src",
                source_record_id="1",
            )

        with pytest.raises(ValueError, match="source_name"):
            ValidationIssue(
                code="CODE",
                severity=ValidationSeverity.INFO,
                message="msg",
                source_name="",
                source_record_id="1",
            )

        with pytest.raises(ValueError, match="source_record_id"):
            ValidationIssue(
                code="CODE",
                severity=ValidationSeverity.INFO,
                message="msg",
                source_name="src",
                source_record_id="",
            )


# ---------------------------------------------------------------------------
# Test ValidationResult
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestValidationResult:

    def test_create_result_without_issues(self, sample_raw_record):
        result = ValidationResult(record=sample_raw_record)
        assert result.record is sample_raw_record
        assert result.issues == ()
        assert result.is_valid is True
        assert result.has_errors is False
        assert result.has_warnings is False
        assert result.has_issues is False

    def test_create_result_with_warning(self, sample_raw_record):
        issue = ValidationIssue(
            code="CSV_EMPTY_TIMESTAMP",
            severity=ValidationSeverity.WARNING,
            message="Timestamp vuoto",
            source_name="cellebrite_csv",
            source_record_id="row:2",
            field_path="TimeStamp",
        )
        result = ValidationResult(record=sample_raw_record, issues=(issue,))
        assert result.record is sample_raw_record
        assert len(result.issues) == 1
        assert result.is_valid is True  # Warning non rende il record invalido
        assert result.has_errors is False
        assert result.has_warnings is True
        assert result.has_issues is True

    def test_create_result_with_error(self, sample_raw_record):
        issue = ValidationIssue(
            code="FATAL_ANOMALY",
            severity=ValidationSeverity.ERROR,
            message="Errore grave",
            source_name="cellebrite_csv",
            source_record_id="row:2",
        )
        result = ValidationResult(record=sample_raw_record, issues=(issue,))
        assert result.is_valid is False
        assert result.has_errors is True

    def test_issues_by_severity(self, sample_raw_record):
        i1 = ValidationIssue("I1", ValidationSeverity.INFO, "info", "src", "1")
        i2 = ValidationIssue("W1", ValidationSeverity.WARNING, "warn", "src", "1")
        i3 = ValidationIssue("E1", ValidationSeverity.ERROR, "err", "src", "1")
        result = ValidationResult(record=sample_raw_record, issues=(i1, i2, i3))

        assert result.issues_by_severity(ValidationSeverity.INFO) == (i1,)
        assert result.issues_by_severity(ValidationSeverity.WARNING) == (i2,)
        assert result.issues_by_severity(ValidationSeverity.ERROR) == (i3,)

    def test_result_is_immutable(self, sample_raw_record):
        result = ValidationResult(record=sample_raw_record)
        with pytest.raises(FrozenInstanceError):
            result.record = sample_raw_record  # type: ignore[misc]

    def test_result_rejects_invalid_record_type(self):
        with pytest.raises(ValueError, match="RawRecord"):
            ValidationResult(record="not_a_raw_record")  # type: ignore[arg-type]

    def test_result_rejects_non_issue_elements(self, sample_raw_record):
        with pytest.raises(ValueError, match="ValidationIssue"):
            ValidationResult(record=sample_raw_record, issues=("not_an_issue",))  # type: ignore[arg-type]
