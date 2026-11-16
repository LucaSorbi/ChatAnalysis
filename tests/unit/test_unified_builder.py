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
        assert msg.sender.participant_id == "participant:local_user:LOCAL_USER"

        # Destinatario
        assert msg.recipient is not None
        assert msg.recipient.is_local_user is False
        assert msg.recipient.display_name == "Contatto_001"
        assert msg.recipient.entity_candidate_id == "entity_candidate:2"
        assert msg.recipient.participant_id == "participant:jid:+390000000001@s.whatsapp.net"

        # Chat
        assert msg.chat is not None
        assert msg.chat.chat_type == "direct"

    def test_group_chat_and_title_enrichment(self):
        """
        Verifica che i record 'chat' arricchiscano i titoli dei gruppi e che
        i JID @g.us siano classificati come 'group'.
        """
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

        builder = UnifiedModelBuilder.from_records([rec_chat, rec_msg])
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

    def test_stream_all_strict_equivalence_with_generator_chat_after_messages(self):
        """
        D1 & D2: Verifica che list(builder.build_stream(records)) e builder.build_all(records)
        producano output identici quando records è un generatore monouso in cui i messaggi
        precedono i record chat.
        """
        rec_msg = _make_record(
            "msgstore_db",
            "m_1",
            record_type="message",
            chat_id="group1@g.us",
            text_content="Messaggio prima del descrittore chat",
        )
        rec_chat = _make_record(
            "msgstore_db",
            "c_1",
            record_type="chat",
            raw_fields={"key_remote_jid": "group1@g.us", "subject": "Titolo Gruppo Post-Messaggio"},
        )

        # Il contesto pre-estrae i metadati delle chat
        from unified.context import UnifiedBuildContext
        context = UnifiedBuildContext.from_records([rec_msg, rec_chat])
        builder = UnifiedModelBuilder(context=context)

        # Generatore 1 per stream
        gen_stream = (r for r in [rec_msg, rec_chat])
        msgs_stream = list(builder.build_stream(gen_stream))

        # Generatore 2 per all
        gen_all = (r for r in [rec_msg, rec_chat])
        msgs_all = builder.build_all(gen_all)

        assert len(msgs_stream) == len(msgs_all) == 1
        assert msgs_stream[0].message_id == msgs_all[0].message_id == "unified:msgstore_db:m_1"
        assert msgs_stream[0].chat.title == msgs_all[0].chat.title == "Titolo Gruppo Post-Messaggio"
        assert msgs_stream[0].chat.chat_type == msgs_all[0].chat.chat_type == "group"

    def test_chat_fallback_classification_d5(self):
        """
        D5: Verifica classificazione chat eterogenee:
        - chat_1 in Cellebrite CSV ha chat_type = 'unknown'
        - messaggio senza chat_id con destinatario generico ha chat_type = 'unknown'
        - messaggio senza chat_id con destinatario @s.whatsapp.net ha chat_type = 'direct'
        """
        builder = UnifiedModelBuilder()

        # 1. Cellebrite CSV con chat_1
        rec_csv = _make_record(
            "cellebrite_csv",
            "row_1",
            chat_id="chat_1",
            actor_from=NormalizedActor(raw_value="Alice", actor_type="alias"),
        )
        msgs = builder.build_all([rec_csv])
        assert len(msgs) == 1
        assert msgs[0].chat is not None
        assert msgs[0].chat.chat_id == "chat:cellebrite_csv:chat_1"
        assert msgs[0].chat.chat_type == "unknown"

        # 2. Senza chat_id, destinatario senza JID WhatsApp -> unknown
        rec_no_cid = _make_record(
            "cellebrite_xml",
            "msg_1",
            chat_id=None,
            actor_to=NormalizedActor(raw_value="+3901234567", normalized_phone="+3901234567", actor_type="phone"),
        )
        msgs_xml = builder.build_all([rec_no_cid])
        assert len(msgs_xml) == 1
        assert msgs_xml[0].chat is not None
        assert msgs_xml[0].chat.chat_type == "unknown"

        # 3. Senza chat_id, destinatario con JID individuale WhatsApp -> direct
        rec_jid = _make_record(
            "msgstore_db",
            "msg_2",
            chat_id=None,
            actor_to=NormalizedActor(raw_value="+3901234567@s.whatsapp.net", actor_type="jid"),
        )
        msgs_jid = builder.build_all([rec_jid])
        assert len(msgs_jid) == 1
        assert msgs_jid[0].chat is not None
        assert msgs_jid[0].chat.chat_type == "direct"

    def test_unresolved_participant_actor_d6(self):
        """
        D6: Attori non risolti (es. group_participant_A o senza telefono/JID)
        devono avere display_name = None, entity_candidate_id = None, metadata['unresolved'] = True.
        """
        builder = UnifiedModelBuilder()
        rec = _make_record(
            "cellebrite_csv",
            "row_10",
            actor_from=NormalizedActor(raw_value="group_participant_A", actor_type="alias"),
        )
        msgs = builder.build_all([rec])
        assert len(msgs) == 1
        sender = msgs[0].sender
        assert sender is not None
        assert sender.identifier == "group_participant_A"
        assert sender.display_name is None
        assert sender.entity_candidate_id is None
        assert sender.is_local_user is False
        assert sender.metadata.get("unresolved") is True

    def test_local_user_participant_properties_d7_d3(self):
        """
        D3 & D7: LOCAL_USER deve avere display_name = None, is_local_user = True,
        identifier = 'LOCAL_USER', e marcatura del ruolo tecnico.
        """
        builder = UnifiedModelBuilder()
        rec = _make_record(
            "msgstore_db",
            "msg_10",
            actor_from=NormalizedActor(raw_value="LOCAL_USER", actor_type="local_user"),
        )
        msgs = builder.build_all([rec])
        assert len(msgs) == 1
        sender = msgs[0].sender
        assert sender is not None
        assert sender.identifier == "LOCAL_USER"
        assert sender.display_name is None
        assert sender.is_local_user is True
        assert sender.metadata.get("technical_role") is True
        assert sender.participant_id == "participant:local_user:LOCAL_USER"

    def test_group_jid_actor_not_promoted_to_person_participant(self):
        """
        Fase B2: Se un record ha come actor un JID di gruppo (@g.us),
        NON deve essere promosso a persona:
        - entity_candidate_id = None
        - display_name = None
        - metadata['group_jid_as_actor'] = True
        """
        group_jid = "1234567890-9876543210@g.us"
        cand_group = CandidateEntity(
            candidate_id="entity_candidate:grp_1",
            candidate_identifier=group_jid,
            entity_type="group",
            references=(),
            evidence_chain=(),
            display_names=("Nome Gruppo Errato",),
        )
        res = ResolutionResult(
            candidate_entities=(cand_group,),
            unresolved_references=(),
            duplicate_candidates=(),
            total_records_processed=1,
        )
        builder = UnifiedModelBuilder(resolution=res)
        rec = _make_record(
            "msgstore_db",
            "msg_grp_actor",
            actor_from=NormalizedActor(raw_value=group_jid, actor_type="jid", jid_domain="g.us"),
        )
        msgs = builder.build_all([rec])
        assert len(msgs) == 1
        sender = msgs[0].sender
        assert sender is not None
        assert sender.entity_candidate_id == "entity_candidate:grp_1"
        assert sender.display_name is None
        assert sender.metadata.get("group_jid_as_actor") is True
        assert sender.metadata.get("is_group") is True
        assert sender.participant_id == f"participant:group:{group_jid}"

    def test_unresolved_participants_distinct_across_sources_and_records(self):
        """
        Fase B1: Partecipanti non risolti con lo stesso alias (es. group_participant_A)
        in record diversi o sorgenti diverse hanno participant_id distinti con prefisso
        participant:unresolved:<source_name>:<record_id>:<role>.
        """
        builder = UnifiedModelBuilder()
        r1 = _make_record(
            "cellebrite_csv",
            "row:10",
            actor_from=NormalizedActor(raw_value="group_participant_A", actor_type="alias"),
        )
        r2 = _make_record(
            "cellebrite_json",
            "msg_20",
            actor_from=NormalizedActor(raw_value="group_participant_A", actor_type="alias"),
        )
        msgs = builder.build_all([r1, r2])
        assert len(msgs) == 2
        p1 = msgs[0].sender
        p2 = msgs[1].sender
        assert p1 is not None and p2 is not None
        assert p1.identifier == "group_participant_A"
        assert p2.identifier == "group_participant_A"
        assert p1.participant_id == "participant:unresolved:cellebrite_csv:row:10:sender"
        assert p2.participant_id == "participant:unresolved:cellebrite_json:msg_20:sender"
        assert p1.participant_id != p2.participant_id

    def test_oneshot_generator_and_context_semantics_c5(self):
        """
        Fase C5: Test completo della semantica di UnifiedBuildContext e one-shot generator:
        1. Messaggi prima dei descrittori di chat in un generatore monouso.
        2. from_records_and_stream() consuma il generatore una sola volta,
           costruisce il context (arricchendo il titolo della chat) e restituisce
           una sequenza riutilizzabile.
        3. list(builder.build_stream(seq)) == builder.build_all(seq).
        """
        from unified.context import UnifiedBuildContext

        rec_msg = _make_record(
            "msgstore_db",
            "msg_stream_1",
            record_type="message",
            chat_id="g123@g.us",
            text_content="Hello in group",
        )
        rec_chat = _make_record(
            "msgstore_db",
            "chat_stream_1",
            record_type="chat",
            raw_fields={"key_remote_jid": "g123@g.us", "subject": "Gruppo Stream Titolo"},
        )

        # Generatore monouso vero e proprio
        def one_shot_gen():
            yield rec_msg
            yield rec_chat

        # 1. Usa from_records_and_stream
        ctx, buffered_records = UnifiedBuildContext.from_records_and_stream(one_shot_gen())
        assert ctx.get_chat_title("g123@g.us") == "Gruppo Stream Titolo"
        assert len(buffered_records) == 2

        builder = UnifiedModelBuilder(context=ctx)
        msgs_stream = list(builder.build_stream(buffered_records))
        msgs_all = builder.build_all(buffered_records)

        assert len(msgs_stream) == len(msgs_all) == 1
        assert msgs_stream[0].message_id == msgs_all[0].message_id == "unified:msgstore_db:msg_stream_1"
        assert msgs_stream[0].chat.title == msgs_all[0].chat.title == "Gruppo Stream Titolo"
        assert msgs_stream == msgs_all

