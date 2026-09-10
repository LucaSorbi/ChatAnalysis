"""
tests/unit/test_unified_builder.py
----------------------------------
Test unitari per UnifiedModelBuilder:
- Generazione ID deterministico 'unified:{source_name}:{source_record_id}'
- Preservazione totale (non-distruttiva) di tutti i messaggi
- Preservazione dello stato di fuso orario (KNOWN_UTC, NAIVE_UNKNOWN, ABSENT)
- Collegamento deterministico a CandidateEntity ed evidenze
- Popolamento non distruttivo di duplicate_candidate_ids
- Tracciamento LOCAL_USER (is_local_user=True)
- Distinzione Chat diretta vs Chat di gruppo (@g.us)
- Arricchimento dei titoli chat da record 'chat'
- Streaming lazy
"""
from __future__ import annotations

from datetime import datetime, timezone
import pytest

from entity_resolution.models import (
    CandidateEntity,
    DuplicateCandidate,
    EntityReference,
    EvidenceLevel,
    EvidenceType,
    ResolutionEvidence,
    ResolutionResult,
)
from importer.models import RawRecord
from normalization.models import (
    CanonicalMessageType,
    NormalizedActor,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.builder import UnifiedModelBuilder
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _make_record(
    source_name: str,
    source_record_id: str,
    record_type: str = "message",
    actor_from: NormalizedActor | None = None,
    actor_to: NormalizedActor | None = None,
    chat_id: str | None = None,
    text_content: str | None = None,
    ts_status: TimestampTzStatus = TimestampTzStatus.KNOWN_UTC,
    raw_fields: dict | None = None,
) -> NormalizedRecord:
    raw = RawRecord(
        source_name=source_name,
        source_path=f"/path/{source_name}",
        source_record_id=source_record_id,
        record_type=record_type,
        raw_fields=raw_fields if raw_fields is not None else {},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    if ts_status == TimestampTzStatus.KNOWN_UTC:
        ts = NormalizedTimestamp(
            status=TimestampTzStatus.KNOWN_UTC,
            utc_datetime=datetime(2024, 4, 23, 20, 30, 0, tzinfo=timezone.utc),
            iso_string="2024-04-23T20:30:00+00:00",
            raw_value=1713904200000,
        )
    elif ts_status == TimestampTzStatus.NAIVE_UNKNOWN:
        ts = NormalizedTimestamp(
            status=TimestampTzStatus.NAIVE_UNKNOWN,
            naive_datetime=datetime(2024, 4, 23, 20, 30, 0),
            iso_string="2024-04-23T20:30:00",
            raw_value="23/04/2024 20:30:00",
        )
    else:
        ts = NormalizedTimestamp(
            status=TimestampTzStatus.ABSENT,
            raw_value=None,
        )

    return NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name=source_name,
        source_record_id=source_record_id,
        record_type=record_type,
        timestamp=ts,
        actor_from=actor_from,
        actor_to=actor_to,
        chat_id=chat_id,
        text_content=text_content,
        message_type=CanonicalMessageType.TEXT,
    )


@pytest.mark.unit
class TestUnifiedModelBuilder:

    def test_deterministic_message_id(self):
        builder = UnifiedModelBuilder()
        rec = _make_record("msgstore_db", "101")
        msgs = builder.build_all([rec])
        assert len(msgs) == 1
        assert msgs[0].message_id == "unified:msgstore_db:101"

    def test_non_destructive_all_messages_preserved(self):
        """
        Tutti i record sorgente producono un UnifiedMessage: nessuna deduplicazione distruttiva.
        """
        builder = UnifiedModelBuilder()
        rec1 = _make_record("msgstore_db", "1", text_content="Messaggio identico")
        rec2 = _make_record("cellebrite_csv", "row:5", text_content="Messaggio identico")
        rec3 = _make_record("cellebrite_json", "msg_1", text_content="Altro testo")

        msgs = builder.build_all([rec1, rec2, rec3])
        assert len(msgs) == 3
        ids = [m.message_id for m in msgs]
        assert ids == [
            "unified:msgstore_db:1",
            "unified:cellebrite_csv:row:5",
            "unified:cellebrite_json:msg_1",
        ]

    def test_timezone_status_preserved(self):
        """
        Verifica che i tre stati temporali rimangano intatti senza forzature.
        """
        builder = UnifiedModelBuilder()
        r_utc = _make_record("src1", "1", ts_status=TimestampTzStatus.KNOWN_UTC)
        r_naive = _make_record("src2", "2", ts_status=TimestampTzStatus.NAIVE_UNKNOWN)
        r_absent = _make_record("src3", "3", ts_status=TimestampTzStatus.ABSENT)

        msgs = builder.build_all([r_utc, r_naive, r_absent])
        assert msgs[0].timestamp.status == TimestampTzStatus.KNOWN_UTC
        assert msgs[0].timestamp.utc_datetime is not None
        assert msgs[1].timestamp.status == TimestampTzStatus.NAIVE_UNKNOWN
        assert msgs[1].timestamp.utc_datetime is None
        assert msgs[1].timestamp.naive_datetime is not None
        assert msgs[2].timestamp.status == TimestampTzStatus.ABSENT
        assert msgs[2].timestamp.utc_datetime is None
        assert msgs[2].timestamp.naive_datetime is None

    def test_duplicate_candidate_linking(self):
        """
        Verifica che duplicate_candidate_ids colleghi i candidati senza eliminarli.
        """
        dup = DuplicateCandidate(
            candidate_id="duplicate_candidate:1",
            records=(("msgstore_db", "1"), ("cellebrite_csv", "5")),
            reason="Test duplicato",
            confidence=EvidenceLevel.EXACT,
        )
        res = ResolutionResult(
            candidate_entities=(),
            unresolved_references=(),
            duplicate_candidates=(dup,),
            total_records_processed=2,
        )

        builder = UnifiedModelBuilder(resolution=res)
        rec1 = _make_record("msgstore_db", "1")
        rec2 = _make_record("cellebrite_csv", "5")
        rec3 = _make_record("msgstore_db", "2")

        msgs = builder.build_all([rec1, rec2, rec3])
        assert len(msgs) == 3
        assert msgs[0].duplicate_candidate_ids == ("duplicate_candidate:1",)
        assert msgs[1].duplicate_candidate_ids == ("duplicate_candidate:1",)
        assert msgs[2].duplicate_candidate_ids == ()

    def test_entity_resolution_linking_and_local_user(self):
        """
        Verifica la risoluzione dei partecipanti tramite Entity Resolution e tracciamento LOCAL_USER.
        """
        ref_local = EntityReference(
            source_name="msgstore_db",
            source_record_id="1",
            actor_role="sender",
            raw_value="LOCAL_USER",
            actor_type="local_user",
        )
        cand_local = CandidateEntity(
            candidate_id="entity_candidate:1",
            candidate_identifier="LOCAL_USER",
            entity_type="local_user",
            references=(ref_local,),
            evidence_chain=(),
            display_names=("LOCAL_USER",),
        )

        ref_peer = EntityReference(
            source_name="msgstore_db",
            source_record_id="1",
            actor_role="recipient",
            raw_value="+390000000001@s.whatsapp.net",
            actor_type="jid",
            normalized_value="+390000000001@s.whatsapp.net",
        )
        cand_peer = CandidateEntity(
            candidate_id="entity_candidate:2",
            candidate_identifier="+390000000001@s.whatsapp.net",
            entity_type="jid",
            references=(ref_peer,),
            evidence_chain=(),
            display_names=("Contatto_001",),
        )

        res = ResolutionResult(
            candidate_entities=(cand_local, cand_peer),
            unresolved_references=(),
            duplicate_candidates=(),
            total_records_processed=1,
        )

        builder = UnifiedModelBuilder(resolution=res)
        rec = _make_record(
            "msgstore_db",
            "1",
            actor_from=NormalizedActor(raw_value="LOCAL_USER", actor_type="local_user"),
            actor_to=NormalizedActor(raw_value="+390000000001@s.whatsapp.net", actor_type="jid"),
            chat_id="+390000000001@s.whatsapp.net",
        )

        msgs = builder.build_all([rec])
        assert len(msgs) == 1
        msg = msgs[0]

        # Mittente
        assert msg.sender is not None
        assert msg.sender.is_local_user is True
        assert msg.sender.entity_candidate_id == "entity_candidate:1"

        # Destinatario
        assert msg.recipient is not None
        assert msg.recipient.is_local_user is False
        assert msg.recipient.display_name == "Contatto_001"
        assert msg.recipient.entity_candidate_id == "entity_candidate:2"

        # Chat
        assert msg.chat is not None
        assert msg.chat.chat_type == "direct"

    def test_group_chat_and_title_enrichment(self):
        """
        Verifica che i record 'chat' arricchiscano i titoli dei gruppi e che
        i JID @g.us siano classificati come 'group'.
        """
        builder = UnifiedModelBuilder()
        # Record descrittore di chat (non deve produrre UnifiedMessage)
        rec_chat = _make_record(
            "msgstore_db",
            "c_1",
            record_type="chat",
            raw_fields={"key_remote_jid": "group1@g.us", "subject": "Gruppo Sintetico Test"},
        )
        # Messaggio appartenente al gruppo
        rec_msg = _make_record(
            "msgstore_db",
            "m_1",
            record_type="message",
            chat_id="group1@g.us",
            actor_from=NormalizedActor(raw_value="+3901", actor_type="phone"),
        )

        msgs = builder.build_all([rec_chat, rec_msg])
        assert len(msgs) == 1  # Solo il record 'message' produce UnifiedMessage
        assert msgs[0].message_id == "unified:msgstore_db:m_1"
        assert msgs[0].chat is not None
        assert msgs[0].chat.chat_type == "group"
        assert msgs[0].chat.title == "Gruppo Sintetico Test"

    def test_streaming_execution(self):
        builder = UnifiedModelBuilder()
        recs = (_make_record("src", str(i)) for i in range(5))
        stream = builder.build_stream(recs)
        import types
        assert isinstance(stream, types.GeneratorType)
        msgs = list(stream)
        assert len(msgs) == 5
