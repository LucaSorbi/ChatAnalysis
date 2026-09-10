"""
tests/unit/test_unified_models.py
---------------------------------
Test unitari per i modelli di Unified Model Foundation:
- Participant
- Chat
- UnifiedMessage
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from types import MappingProxyType
import pytest

from importer.models import RawRecord
from normalization.models import (
    CanonicalMessageType,
    NormalizedActor,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import Chat, Participant, UnifiedMessage
from validation.models import ValidationResult


def _make_dummy_normalized_record(
    source_name: str = "msgstore_db",
    source_record_id: str = "1",
) -> NormalizedRecord:
    raw = RawRecord(
        source_name=source_name,
        source_path=f"/path/{source_name}",
        source_record_id=source_record_id,
        record_type="message",
        raw_fields={"text": "test"},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(
        status=TimestampTzStatus.KNOWN_UTC,
        utc_datetime=datetime(2024, 4, 23, 20, 30, 0, tzinfo=timezone.utc),
        iso_string="2024-04-23T20:30:00+00:00",
        raw_value=1713904200000,
    )
    return NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name=source_name,
        source_record_id=source_record_id,
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
    )


@pytest.mark.unit
class TestParticipant:

    def test_create_valid_participant(self):
        p = Participant(
            participant_id="participant:+390000000001",
            identifier="+390000000001",
            display_name="Contatto_001",
            entity_candidate_id="entity_candidate:1",
            is_local_user=False,
            metadata={"source": "wa_db"},
        )
        assert p.participant_id == "participant:+390000000001"
        assert p.identifier == "+390000000001"
        assert p.display_name == "Contatto_001"
        assert p.entity_candidate_id == "entity_candidate:1"
        assert p.is_local_user is False
        assert isinstance(p.metadata, MappingProxyType)
        assert p.metadata["source"] == "wa_db"

    def test_local_user_participant(self):
        p = Participant(
            participant_id="participant:LOCAL_USER",
            identifier="LOCAL_USER",
            display_name="LOCAL_USER",
            is_local_user=True,
        )
        assert p.is_local_user is True
        assert p.identifier == "LOCAL_USER"

    def test_immutability(self):
        p = Participant(
            participant_id="p1",
            identifier="id1",
        )
        with pytest.raises(FrozenInstanceError):
            p.identifier = "new_id"  # type: ignore[misc]

    def test_empty_id_or_identifier_raises(self):
        with pytest.raises(ValueError, match="participant_id"):
            Participant(participant_id="", identifier="id1")
        with pytest.raises(ValueError, match="identifier"):
            Participant(participant_id="p1", identifier="")


@pytest.mark.unit
class TestChat:

    def test_create_valid_chat(self):
        p1 = Participant(participant_id="p1", identifier="user1")
        p2 = Participant(participant_id="p2", identifier="user2")
        chat = Chat(
            chat_id="chat:msgstore_db:001@g.us",
            chat_type="group",
            title="Gruppo Test",
            participants=(p1, p2),
            source_name="msgstore_db",
            metadata={"is_active": True},
        )
        assert chat.chat_id == "chat:msgstore_db:01@g.us" or "001@g.us" in chat.chat_id
        assert chat.chat_type == "group"
        assert chat.title == "Gruppo Test"
        assert len(chat.participants) == 2
        assert isinstance(chat.metadata, MappingProxyType)

    def test_invalid_chat_type_raises(self):
        with pytest.raises(ValueError, match="chat_type"):
            Chat(chat_id="c1", chat_type="invalid_type")

    def test_immutability(self):
        chat = Chat(chat_id="c1", chat_type="direct")
        with pytest.raises(FrozenInstanceError):
            chat.title = "new_title"  # type: ignore[misc]


@pytest.mark.unit
class TestUnifiedMessage:

    def test_create_valid_message(self):
        rec = _make_dummy_normalized_record()
        p_from = Participant(participant_id="p_from", identifier="LOCAL_USER", is_local_user=True)
        p_to = Participant(participant_id="p_to", identifier="+390000000001")
        chat = Chat(chat_id="chat:1", chat_type="direct", participants=(p_from, p_to))

        msg = UnifiedMessage(
            message_id="unified:msgstore_db:1",
            source_name="msgstore_db",
            source_record_id="1",
            source_path="/path/msgstore.db",
            record_type="message",
            timestamp=rec.timestamp,
            sender=p_from,
            recipient=p_to,
            chat=chat,
            message_type=CanonicalMessageType.TEXT,
            raw_message_type=0,
            text_content="Messaggio testuale",
            media_reference=None,
            is_deleted=False,
            raw_deleted=0,
            duplicate_candidate_ids=("duplicate_candidate:1",),
            provenance_record=rec,
        )

        assert msg.message_id == "unified:msgstore_db:1"
        assert msg.source_name == "msgstore_db"
        assert msg.source_record_id == "1"
        assert msg.sender.is_local_user is True
        assert msg.recipient.identifier == "+390000000001"
        assert msg.text_content == "Messaggio testuale"
        assert msg.duplicate_candidate_ids == ("duplicate_candidate:1",)
        assert msg.provenance_record is rec
        assert msg.timestamp.status == TimestampTzStatus.KNOWN_UTC

    def test_immutability(self):
        rec = _make_dummy_normalized_record()
        msg = UnifiedMessage(
            message_id="unified:msgstore_db:1",
            source_name="msgstore_db",
            source_record_id="1",
            source_path="/path",
            record_type="message",
            timestamp=rec.timestamp,
            provenance_record=rec,
        )
        with pytest.raises(FrozenInstanceError):
            msg.text_content = "modified"  # type: ignore[misc]

    def test_empty_fields_raise(self):
        rec = _make_dummy_normalized_record()
        with pytest.raises(ValueError, match="message_id"):
            UnifiedMessage(
                message_id="",
                source_name="src",
                source_record_id="1",
                source_path="/path",
                record_type="message",
                timestamp=rec.timestamp,
                provenance_record=rec,
            )
