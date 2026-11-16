"""
tests/unit/test_normalization_models.py
---------------------------------------
Test unitari per i modelli del layer di Normalizzazione:
- TimestampTzStatus
- CanonicalMessageType
- NormalizedTimestamp
- NormalizedActor
- NormalizedRecord
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from importer.models import RawRecord
from normalization.models import (
    CanonicalMessageType,
    NormalizedActor,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from validation.models import ValidationResult


@pytest.fixture
def sample_raw_record() -> RawRecord:
    return RawRecord(
        source_name="cellebrite_csv",
        source_path="/path/to/messages.csv",
        source_record_id="row:1",
        record_type="message",
        raw_fields={"TimeStamp": "2024-04-23 20:30:15", "From": "+3901", "Body": "Test"},
        media_reference=None,
        metadata={"line": 1},
    )


@pytest.fixture
def sample_validation_result(sample_raw_record) -> ValidationResult:
    return ValidationResult(record=sample_raw_record, issues=())


@pytest.fixture
def sample_timestamp() -> NormalizedTimestamp:
    dt = datetime(2024, 4, 23, 20, 30, 15, tzinfo=timezone.utc)
    return NormalizedTimestamp(
        status=TimestampTzStatus.KNOWN_UTC,
        utc_datetime=dt,
        iso_string=dt.isoformat(),
        raw_value="2024-04-23T20:30:15+00:00",
    )


@pytest.fixture
def sample_actor() -> NormalizedActor:
    return NormalizedActor(
        raw_value="+39 000 0000001",
        actor_type="phone",
        normalized_phone="+390000000001",
    )


# ---------------------------------------------------------------------------
# Test Enum
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestNormalizationEnums:

    def test_timestamp_tz_status_values(self):
        assert TimestampTzStatus.KNOWN_UTC.value == "KNOWN_UTC"
        assert TimestampTzStatus.NAIVE_UNKNOWN.value == "NAIVE_UNKNOWN"
        assert TimestampTzStatus.ABSENT.value == "ABSENT"

    def test_canonical_message_type_values(self):
        assert CanonicalMessageType.TEXT.value == "TEXT"
        assert CanonicalMessageType.AUDIO.value == "AUDIO"
        assert CanonicalMessageType.IMAGE.value == "IMAGE"
        assert CanonicalMessageType.VIDEO.value == "VIDEO"
        assert CanonicalMessageType.SYSTEM.value == "SYSTEM"
        assert CanonicalMessageType.OTHER.value == "OTHER"
        assert CanonicalMessageType.UNKNOWN.value == "UNKNOWN"


# ---------------------------------------------------------------------------
# Test NormalizedTimestamp
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestNormalizedTimestamp:

    def test_create_known_utc(self, sample_timestamp):
        assert sample_timestamp.status == TimestampTzStatus.KNOWN_UTC
        assert sample_timestamp.utc_datetime is not None
        assert sample_timestamp.utc_datetime.tzinfo == timezone.utc
        assert sample_timestamp.naive_datetime is None
        assert sample_timestamp.raw_value == "2024-04-23T20:30:15+00:00"

    def test_create_naive_unknown(self):
        naive_dt = datetime(2024, 4, 23, 22, 30, 15)
        ts = NormalizedTimestamp(
            status=TimestampTzStatus.NAIVE_UNKNOWN,
            naive_datetime=naive_dt,
            iso_string=naive_dt.isoformat(),
            raw_value="2024-04-23 22:30:15",
        )
        assert ts.status == TimestampTzStatus.NAIVE_UNKNOWN
        assert ts.utc_datetime is None
        assert ts.naive_datetime == naive_dt
        assert ts.raw_value == "2024-04-23 22:30:15"

    def test_create_absent(self):
        ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT, raw_value="")
        assert ts.status == TimestampTzStatus.ABSENT
        assert ts.utc_datetime is None
        assert ts.naive_datetime is None
        assert ts.iso_string is None

    def test_is_immutable(self, sample_timestamp):
        with pytest.raises(FrozenInstanceError):
            sample_timestamp.status = TimestampTzStatus.ABSENT  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Test NormalizedActor
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestNormalizedActor:

    def test_phone_actor(self, sample_actor):
        assert sample_actor.actor_type == "phone"
        assert sample_actor.normalized_phone == "+390000000001"
        assert sample_actor.raw_value == "+39 000 0000001"

    def test_jid_actor(self):
        actor = NormalizedActor(
            raw_value="123456789@s.whatsapp.net",
            actor_type="jid",
            jid_local="123456789",
            jid_domain="s.whatsapp.net",
        )
        assert actor.actor_type == "jid"
        assert actor.jid_local == "123456789"
        assert actor.jid_domain == "s.whatsapp.net"

    def test_alias_actor(self):
        actor = NormalizedActor(
            raw_value="group_participant_A",
            actor_type="alias",
            alias="group_participant_A",
        )
        assert actor.actor_type == "alias"
        assert actor.alias == "group_participant_A"

    def test_chat_id_actor(self):
        actor = NormalizedActor(
            raw_value="chat_1",
            actor_type="chat_id",
            chat_id="chat_1",
        )
        assert actor.actor_type == "chat_id"
        assert actor.chat_id == "chat_1"

    def test_is_immutable(self, sample_actor):
        with pytest.raises(FrozenInstanceError):
            sample_actor.actor_type = "unknown"  # type: ignore[misc]

    def test_invalid_actor_type_raises(self):
        with pytest.raises(ValueError, match="actor_type"):
            NormalizedActor(raw_value="x", actor_type="invalid_type")


# ---------------------------------------------------------------------------
# Test NormalizedRecord
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestNormalizedRecord:

    def test_create_valid_record(
        self, sample_raw_record, sample_validation_result, sample_timestamp, sample_actor
    ):
        rec = NormalizedRecord(
            raw_record=sample_raw_record,
            validation_result=sample_validation_result,
            source_name=sample_raw_record.source_name,
            source_record_id=sample_raw_record.source_record_id,
            record_type=sample_raw_record.record_type,
            timestamp=sample_timestamp,
            actor_from=sample_actor,
            actor_to=None,
            message_type=CanonicalMessageType.TEXT,
            raw_message_type="Text",
            is_deleted=False,
            raw_deleted="False",
            text_content="Test",
            media_reference=None,
        )

        # Provenance intatta
        assert rec.raw_record is sample_raw_record
        assert rec.validation_result is sample_validation_result
        assert rec.source_name == "cellebrite_csv"
        assert rec.source_record_id == "row:1"
        assert rec.record_type == "message"
        assert rec.timestamp is sample_timestamp
        assert rec.actor_from is sample_actor
        assert rec.message_type == CanonicalMessageType.TEXT
        assert rec.raw_message_type == "Text"
        assert rec.is_deleted is False
        assert rec.raw_deleted == "False"
        assert rec.text_content == "Test"

    def test_record_is_immutable(
        self, sample_raw_record, sample_validation_result, sample_timestamp
    ):
        rec = NormalizedRecord(
            raw_record=sample_raw_record,
            validation_result=sample_validation_result,
            source_name="src",
            source_record_id="1",
            record_type="message",
            timestamp=sample_timestamp,
        )
        with pytest.raises(FrozenInstanceError):
            rec.source_name = "tampered"  # type: ignore[misc]

    def test_rejects_invalid_provenance_types(self, sample_validation_result, sample_timestamp):
        with pytest.raises(ValueError, match="raw_record"):
            NormalizedRecord(
                raw_record="not_a_raw_record",  # type: ignore[arg-type]
                validation_result=sample_validation_result,
                source_name="src",
                source_record_id="1",
                record_type="message",
                timestamp=sample_timestamp,
            )

        with pytest.raises(ValueError, match="validation_result"):
            NormalizedRecord(
                raw_record=sample_validation_result.record,
                validation_result="not_a_val_result",  # type: ignore[arg-type]
                source_name="src",
                source_record_id="1",
                record_type="message",
                timestamp=sample_timestamp,
            )

    def test_metadata_is_frozen_mappingproxy(
        self, sample_raw_record, sample_validation_result, sample_timestamp
    ):
        from types import MappingProxyType
        rec = NormalizedRecord(
            raw_record=sample_raw_record,
            validation_result=sample_validation_result,
            source_name="src",
            source_record_id="1",
            record_type="message",
            timestamp=sample_timestamp,
            metadata={"key": "value"},
        )
        assert isinstance(rec.metadata, MappingProxyType)
        with pytest.raises(TypeError):
            rec.metadata["key"] = "tampered"  # type: ignore[index]

    def test_raw_record_and_validation_result_remain_unmodified(
        self, sample_raw_record, sample_validation_result, sample_timestamp
    ):
        raw_fields_before = dict(sample_raw_record.raw_fields)
        val_issues_before = sample_validation_result.issues

        rec = NormalizedRecord(
            raw_record=sample_raw_record,
            validation_result=sample_validation_result,
            source_name="src",
            source_record_id="1",
            record_type="message",
            timestamp=sample_timestamp,
        )

        assert dict(rec.raw_record.raw_fields) == raw_fields_before
        assert rec.validation_result.issues == val_issues_before
        assert rec.raw_record is sample_raw_record
        assert rec.validation_result is sample_validation_result
