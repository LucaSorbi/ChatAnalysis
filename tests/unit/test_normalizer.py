"""
tests/unit/test_normalizer.py
-----------------------------
Test unitari per RecordNormalizer:
- Normalizzazione timestamp con offset (JSON, XML -> KNOWN_UTC)
- Normalizzazione timestamp CSV privo di timezone (NAIVE_UNKNOWN, nessun offset forzato)
- Timestamp CSV vuoto (ABSENT, nessun epoch 0)
- Timestamp WhatsApp millisecondi Unix (KNOWN_UTC)
- Normalizzazione attori (telefoni con prefisso, alias group_participant_A, JID, chat_id)
- Semantica deleted conservativa
- Tassonomia canonica candidate dei message type
- Streaming lazy (normalize_stream)
- Conservazione provenance e immutabilità di RawRecord e ValidationResult
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest

from importer.models import RawRecord
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    TimestampTzStatus,
)
from normalization.normalizer import RecordNormalizer
from validation.models import ValidationResult


def make_raw_record(
    source_name: str,
    source_path: str = "/path",
    source_record_id: str = "1",
    record_type: str = "message",
    raw_fields: dict | None = None,
    media_reference: str | None = None,
    metadata: dict | None = None,
) -> RawRecord:
    return RawRecord(
        source_name=source_name,
        source_path=source_path,
        source_record_id=source_record_id,
        record_type=record_type,
        raw_fields=raw_fields if raw_fields is not None else {},
        media_reference=media_reference,
        metadata=metadata if metadata is not None else {},
    )


def make_val_result(record: RawRecord) -> ValidationResult:
    return ValidationResult(record=record, issues=())


@pytest.fixture
def normalizer() -> RecordNormalizer:
    return RecordNormalizer()


# ---------------------------------------------------------------------------
# Test Normalizzazione Timestamp (B3, B4, B5, B6, B7)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestTimestampNormalization:

    def test_json_timestamp_with_zero_offset(self, normalizer):
        rec = make_raw_record(
            "cellebrite_json",
            raw_fields={"timestamp": "2024-04-23T20:30:15+00:00"},
        )
        norm = normalizer.normalize(make_val_result(rec))
        assert norm.timestamp.status == TimestampTzStatus.KNOWN_UTC
        assert norm.timestamp.utc_datetime == datetime(2024, 4, 23, 20, 30, 15, tzinfo=timezone.utc)
        assert norm.timestamp.iso_string == "2024-04-23T20:30:15+00:00"

    def test_json_timestamp_with_positive_offset(self, normalizer):
        # +02:00: 22:30:15 a +02:00 corrisponde a 20:30:15 UTC
        rec = make_raw_record(
            "cellebrite_json",
            raw_fields={"timestamp": "2024-04-23T22:30:15+02:00"},
        )
        norm = normalizer.normalize(make_val_result(rec))
        assert norm.timestamp.status == TimestampTzStatus.KNOWN_UTC
        assert norm.timestamp.utc_datetime == datetime(2024, 4, 23, 20, 30, 15, tzinfo=timezone.utc)
        assert norm.timestamp.utc_datetime.hour == 20

    def test_json_timestamp_with_negative_offset(self, normalizer):
        # -05:00: 15:30:15 a -05:00 corrisponde a 20:30:15 UTC
        rec = make_raw_record(
            "cellebrite_json",
            raw_fields={"timestamp": "2024-04-23T15:30:15-05:00"},
        )
        norm = normalizer.normalize(make_val_result(rec))
        assert norm.timestamp.status == TimestampTzStatus.KNOWN_UTC
        assert norm.timestamp.utc_datetime == datetime(2024, 4, 23, 20, 30, 15, tzinfo=timezone.utc)
        assert norm.timestamp.utc_datetime.hour == 20

    def test_xml_timestamp_with_offset(self, normalizer):
        rec = make_raw_record(
            "cellebrite_xml",
            raw_fields={"Timestamp": "2024-04-23T20:30:15+00:00"},
        )
        norm = normalizer.normalize(make_val_result(rec))
        assert norm.timestamp.status == TimestampTzStatus.KNOWN_UTC
        assert norm.timestamp.utc_datetime == datetime(2024, 4, 23, 20, 30, 15, tzinfo=timezone.utc)

    def test_csv_timestamp_without_timezone_is_naive_unknown(self, normalizer):
        """
        Requisito B5: per il CSV Cellebrite non deve essere assunto né UTC né Europe/Rome.
        Il timestamp è conservato come datetime naive locale marcato NAIVE_UNKNOWN.
        """
        rec = make_raw_record(
            "cellebrite_csv",
            raw_fields={"TimeStamp": "2024-04-23 22:30:15"},
        )
        norm = normalizer.normalize(make_val_result(rec))
        assert norm.timestamp.status == TimestampTzStatus.NAIVE_UNKNOWN
        assert norm.timestamp.utc_datetime is None
        assert norm.timestamp.naive_datetime == datetime(2024, 4, 23, 22, 30, 15)
        assert norm.timestamp.iso_string == "2024-04-23T22:30:15"

    def test_csv_empty_timestamp_is_absent(self, normalizer):
        """
        Requisito B6: timestamp vuoto rimane ABSENT. Nessun epoch 0 o data odierna.
        """
        rec = make_raw_record(
            "cellebrite_csv",
            raw_fields={"TimeStamp": ""},
        )
        norm = normalizer.normalize(make_val_result(rec))
        assert norm.timestamp.status == TimestampTzStatus.ABSENT
        assert norm.timestamp.utc_datetime is None
        assert norm.timestamp.naive_datetime is None
        assert norm.timestamp.iso_string is None
        assert norm.timestamp.raw_value == ""

    def test_whatsapp_timestamp_milliseconds_unix(self, normalizer):
        """
        Requisito B7: timestamp WhatsApp msgstore in millisecondi Unix.
        """
        # 1713904215000 ms = 2024-04-23 20:30:15 UTC
        rec = make_raw_record(
            "msgstore_db",
            raw_fields={"timestamp": 1713904215000},
        )
        norm = normalizer.normalize(make_val_result(rec))
        assert norm.timestamp.status == TimestampTzStatus.KNOWN_UTC
        assert norm.timestamp.utc_datetime == datetime(2024, 4, 23, 20, 30, 15, tzinfo=timezone.utc)

    def test_whatsapp_anomalous_timestamp_is_absent(self, normalizer):
        rec = make_raw_record(
            "msgstore_db",
            raw_fields={"timestamp": -1000},
        )
        norm = normalizer.normalize(make_val_result(rec))
        assert norm.timestamp.status == TimestampTzStatus.ABSENT
        assert norm.timestamp.utc_datetime is None


# ---------------------------------------------------------------------------
# Test Normalizzazione Attori (B8, B9)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestActorNormalization:

    def test_international_phone_number_cleaned(self, normalizer):
        rec = make_raw_record(
            "cellebrite_csv",
            raw_fields={"From": "+39 000 0000001", "To": "+1 000 0000004"},
        )
        norm = normalizer.normalize(make_val_result(rec))
        assert norm.actor_from is not None
        assert norm.actor_from.actor_type == "phone"
        assert norm.actor_from.normalized_phone == "+390000000001"
        assert norm.actor_from.raw_value == "+39 000 0000001"

        assert norm.actor_to is not None
        assert norm.actor_to.actor_type == "phone"
        assert norm.actor_to.normalized_phone == "+10000000004"

    def test_group_participant_alias_preserved_not_phone(self, normalizer):
        """
        Requisito B8: group_participant_A non è un numero e non deve essere convertito.
        """
        rec = make_raw_record(
            "cellebrite_xml",
            raw_fields={"Sender": "group_participant_A"},
        )
        norm = normalizer.normalize(make_val_result(rec))
        assert norm.actor_from is not None
        assert norm.actor_from.actor_type == "alias"
        assert norm.actor_from.alias == "group_participant_A"
        assert norm.actor_from.normalized_phone is None

    def test_chat_id_preserved_as_chat_id(self, normalizer):
        rec = make_raw_record(
            "cellebrite_csv",
            raw_fields={"From": "chat_1"},
        )
        norm = normalizer.normalize(make_val_result(rec))
        assert norm.actor_from is not None
        assert norm.actor_from.actor_type == "chat_id"
        assert norm.actor_from.chat_id == "chat_1"
        assert norm.actor_from.normalized_phone is None

    def test_whatsapp_jid_decomposed_without_entity_resolution(self, normalizer):
        """
        Requisito B9: JID decomposto in local_part e domain in modo lossless, senza Entity Resolution.
        """
        rec = make_raw_record(
            "msgstore_db",
            raw_fields={"key_remote_jid": "123456789@s.whatsapp.net"},
        )
        norm = normalizer.normalize(make_val_result(rec))
        assert norm.actor_from is not None
        assert norm.actor_from.actor_type == "jid"
        assert norm.actor_from.jid_local == "123456789"
        assert norm.actor_from.jid_domain == "s.whatsapp.net"
        assert norm.actor_from.raw_value == "123456789@s.whatsapp.net"


# ---------------------------------------------------------------------------
# Test Deleted Semantics (B10)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDeletedNormalization:

    def test_msgstore_deleted(self, normalizer):
        rec0 = make_raw_record("msgstore_db", raw_fields={"deleted": 0})
        rec1 = make_raw_record("msgstore_db", raw_fields={"deleted": 1})
        assert normalizer.normalize(make_val_result(rec0)).is_deleted is False
        assert normalizer.normalize(make_val_result(rec1)).is_deleted is True

    def test_csv_deleted(self, normalizer):
        rec_f = make_raw_record("cellebrite_csv", raw_fields={"Deleted": "False"})
        rec_t = make_raw_record("cellebrite_csv", raw_fields={"Deleted": "True"})
        assert normalizer.normalize(make_val_result(rec_f)).is_deleted is False
        assert normalizer.normalize(make_val_result(rec_t)).is_deleted is True

    def test_json_deleted(self, normalizer):
        rec_f = make_raw_record("cellebrite_json", raw_fields={"metadata": {"deleted": False}})
        rec_t = make_raw_record("cellebrite_json", raw_fields={"metadata": {"deleted": True}})
        assert normalizer.normalize(make_val_result(rec_f)).is_deleted is False
        assert normalizer.normalize(make_val_result(rec_t)).is_deleted is True

    def test_xml_deleted(self, normalizer):
        rec_f = make_raw_record("cellebrite_xml", raw_fields={"Deleted": "false"})
        rec_t = make_raw_record("cellebrite_xml", raw_fields={"Deleted": "true"})
        assert normalizer.normalize(make_val_result(rec_f)).is_deleted is False
        assert normalizer.normalize(make_val_result(rec_t)).is_deleted is True


# ---------------------------------------------------------------------------
# Test Message Type Taxonomy (B11)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMessageTypeNormalization:

    def test_msgstore_types(self, normalizer):
        for raw_val, expected in [
            (0, CanonicalMessageType.TEXT),
            (1, CanonicalMessageType.IMAGE),
            (2, CanonicalMessageType.AUDIO),
            (3, CanonicalMessageType.VIDEO),
            (99, CanonicalMessageType.UNKNOWN),
        ]:
            rec = make_raw_record("msgstore_db", raw_fields={"media_wa_type": raw_val})
            norm = normalizer.normalize(make_val_result(rec))
            assert norm.message_type == expected
            assert norm.raw_message_type == raw_val

    def test_csv_types(self, normalizer):
        rec_t = make_raw_record("cellebrite_csv", raw_fields={"MessageType": "Text"})
        rec_i = make_raw_record("cellebrite_csv", raw_fields={"MessageType": "Image"})
        assert normalizer.normalize(make_val_result(rec_t)).message_type == CanonicalMessageType.TEXT
        assert normalizer.normalize(make_val_result(rec_i)).message_type == CanonicalMessageType.IMAGE

    def test_json_types(self, normalizer):
        rec_a = make_raw_record("cellebrite_json", raw_fields={"type": "audio"})
        rec_t = make_raw_record("cellebrite_json", raw_fields={"type": "text"})
        assert normalizer.normalize(make_val_result(rec_a)).message_type == CanonicalMessageType.AUDIO
        assert normalizer.normalize(make_val_result(rec_t)).message_type == CanonicalMessageType.TEXT


# ---------------------------------------------------------------------------
# Test Streaming & Provenance (B1, B2, B15)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestStreamingAndProvenance:

    def test_normalize_stream_is_generator(self, normalizer):
        r1 = make_raw_record("src1", source_record_id="1")
        r2 = make_raw_record("src2", source_record_id="2")
        val_stream = iter([make_val_result(r1), make_val_result(r2)])

        norm_stream = normalizer.normalize_stream(val_stream)
        assert inspect.isgenerator(norm_stream)

        n1 = next(norm_stream)
        assert isinstance(n1, NormalizedRecord)
        assert n1.source_record_id == "1"
        assert n1.raw_record is r1

        n2 = next(norm_stream)
        assert isinstance(n2, NormalizedRecord)
        assert n2.source_record_id == "2"
        assert n2.raw_record is r2

        with pytest.raises(StopIteration):
            next(norm_stream)

    def test_raw_record_unmodified(self, normalizer):
        rec = make_raw_record(
            "cellebrite_csv",
            raw_fields={"TimeStamp": "2024-04-23 22:30:15", "From": "+39 000 0000001"},
        )
        fields_before = dict(rec.raw_fields)
        norm = normalizer.normalize(make_val_result(rec))

        # RawRecord integro e inalterato
        assert dict(rec.raw_fields) == fields_before
        assert norm.raw_record is rec
