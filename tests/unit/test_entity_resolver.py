"""
tests/unit/test_entity_resolver.py
----------------------------------
Test unitari per DeterministicEntityResolver:
- Exact JID match tra sorgenti differenti
- JID differenti rimangono entità distinte
- Phone canonical match tra sorgenti
- Phone <-> JID local part deterministic match
- Alias 'group_participant_A' marcato UNRESOLVED (nessuna euristica AI)
- ChatId 'chat_1' marcato UNRESOLVED (nessun mapping inventato)
- Testo o timestamp simile da solo NON unisce entità
- Provenance preservata: NormalizedRecord e RawRecord inalterati
- DuplicateCandidate rileva duplicati SENZA cancellare record
- Determinismo: esecuzioni successive generano risultati identici
"""
from __future__ import annotations

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
from entity_resolution.models import (
    EvidenceLevel,
    EvidenceType,
)
from entity_resolution.resolver import DeterministicEntityResolver


def make_norm_record(
    source_name: str,
    source_record_id: str,
    record_type: str = "message",
    actor_from: NormalizedActor | None = None,
    actor_to: NormalizedActor | None = None,
    text_content: str | None = None,
    iso_timestamp: str | None = None,
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
    ts = (
        NormalizedTimestamp(
            status=TimestampTzStatus.KNOWN_UTC,
            utc_datetime=datetime.fromisoformat(iso_timestamp),
            iso_string=iso_timestamp,
            raw_value=iso_timestamp,
        )
        if iso_timestamp
        else NormalizedTimestamp(status=TimestampTzStatus.ABSENT, raw_value=None)
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
        message_type=CanonicalMessageType.TEXT,
        raw_message_type="Text",
        is_deleted=False,
        raw_deleted="0",
        text_content=text_content,
    )


@pytest.fixture
def resolver() -> DeterministicEntityResolver:
    return DeterministicEntityResolver()


@pytest.mark.unit
class TestDeterministicEntityResolver:

    def test_exact_jid_match_across_sources(self, resolver):
        """
        Requisito B3: WhatsApp msgstore.db e wa.db contengono lo stesso identico JID.
        Devono essere raggruppati nella stessa CandidateEntity con EvidenceLevel.EXACT.
        """
        actor_jid = NormalizedActor(
            raw_value="+390000000001@s.whatsapp.net",
            actor_type="jid",
            jid_local="+390000000001",
            jid_domain="s.whatsapp.net",
        )
        r_msg = make_norm_record("msgstore_db", "101", actor_from=actor_jid)
        r_wa = make_norm_record(
            "wa_db",
            "1",
            record_type="contact",
            actor_from=actor_jid,
            raw_fields={"display_name": "Contatto_001", "jid": "+390000000001@s.whatsapp.net"},
        )

        res = resolver.resolve([r_msg, r_wa])
        assert len(res.candidate_entities) == 1
        ent = res.candidate_entities[0]
        assert ent.canonical_identifier == "+390000000001@s.whatsapp.net"
        assert ent.entity_type == "jid"
        assert len(ent.references) == 2
        assert any(ev.evidence_type == EvidenceType.JID_EXACT for ev in ent.evidence_chain)
        assert any(ev.evidence_level == EvidenceLevel.EXACT for ev in ent.evidence_chain)
        assert "Contatto_001" in ent.display_names

    def test_different_jids_remain_distinct(self, resolver):
        actor1 = NormalizedActor(
            raw_value="+390000000001@s.whatsapp.net",
            actor_type="jid",
            jid_local="+390000000001",
            jid_domain="s.whatsapp.net",
        )
        actor2 = NormalizedActor(
            raw_value="+390000000002@s.whatsapp.net",
            actor_type="jid",
            jid_local="+390000000002",
            jid_domain="s.whatsapp.net",
        )
        r1 = make_norm_record("msgstore_db", "1", actor_from=actor1)
        r2 = make_norm_record("msgstore_db", "2", actor_from=actor2)

        res = resolver.resolve([r1, r2])
        assert len(res.candidate_entities) == 2
        ids = {e.canonical_identifier for e in res.candidate_entities}
        assert ids == {"+390000000001@s.whatsapp.net", "+390000000002@s.whatsapp.net"}

    def test_canonical_phone_match_across_cellebrite_sources(self, resolver):
        """
        Requisito B4 / B12: numero telefonico canonicalizzato comune a più record.
        """
        actor1 = NormalizedActor(
            raw_value="+39 000 0000001",
            actor_type="phone",
            normalized_phone="+390000000001",
        )
        actor2 = NormalizedActor(
            raw_value="+390000000001",
            actor_type="phone",
            normalized_phone="+390000000001",
        )
        r_csv = make_norm_record("cellebrite_csv", "row:1", actor_from=actor1)
        r_json = make_norm_record("cellebrite_json", "msg_00001", actor_from=actor2)

        res = resolver.resolve([r_csv, r_json])
        assert len(res.candidate_entities) == 1
        ent = res.candidate_entities[0]
        assert ent.canonical_identifier == "+390000000001"
        assert ent.entity_type == "phone"
        assert len(ent.references) == 2
        assert any(ev.evidence_type == EvidenceType.PHONE_CANONICAL for ev in ent.evidence_chain)

    def test_phone_and_jid_local_deterministic_link(self, resolver):
        """
        Requisito B4: local part numerico del JID collegato deterministicamente a numero canonico.
        """
        actor_jid = NormalizedActor(
            raw_value="+390000000001@s.whatsapp.net",
            actor_type="jid",
            jid_local="+390000000001",
            jid_domain="s.whatsapp.net",
        )
        actor_phone = NormalizedActor(
            raw_value="+39 000 0000001",
            actor_type="phone",
            normalized_phone="+390000000001",
        )
        r_wa = make_norm_record("msgstore_db", "1", actor_from=actor_jid)
        r_csv = make_norm_record("cellebrite_csv", "row:10", actor_from=actor_phone)

        res = resolver.resolve([r_wa, r_csv])
        assert len(res.candidate_entities) == 1
        ent = res.candidate_entities[0]
        assert ent.canonical_identifier == "+390000000001@s.whatsapp.net"
        assert len(ent.references) == 2
        # Contiene sia evidenza JID che evidenza PHONE_JID_LOCAL
        ev_types = {ev.evidence_type for ev in ent.evidence_chain}
        assert EvidenceType.JID_EXACT in ev_types
        assert EvidenceType.PHONE_JID_LOCAL in ev_types

    def test_group_participant_alias_remains_unresolved(self, resolver):
        """
        Requisito B5: 'group_participant_A' NON viene risolto automaticamente.
        """
        actor_alias = NormalizedActor(
            raw_value="group_participant_A",
            actor_type="alias",
            alias="group_participant_A",
        )
        r = make_norm_record("cellebrite_csv", "row:5", actor_from=actor_alias)

        res = resolver.resolve([r])
        assert len(res.candidate_entities) == 0
        assert len(res.unresolved_references) == 1
        unres = res.unresolved_references[0]
        assert unres.actor_type == "alias"
        assert unres.raw_value == "group_participant_A"

    def test_chat_id_remains_unresolved(self, resolver):
        """
        Requisito B6: ChatId come 'chat_1' senza mapping deterministico resta UNRESOLVED.
        """
        actor_chat = NormalizedActor(
            raw_value="chat_1",
            actor_type="chat_id",
            chat_id="chat_1",
        )
        r = make_norm_record("cellebrite_csv", "row:7", actor_from=actor_chat)

        res = resolver.resolve([r])
        assert len(res.candidate_entities) == 0
        assert len(res.unresolved_references) == 1
        assert res.unresolved_references[0].actor_type == "chat_id"

    def test_no_fusion_from_similar_timestamp_or_text_alone(self, resolver):
        """
        Requisito B2: timestamp identico o testo identico da soli NON devono fondere entità differenti.
        """
        actor1 = NormalizedActor(raw_value="+3901", actor_type="phone", normalized_phone="+3901")
        actor2 = NormalizedActor(raw_value="+3902", actor_type="phone", normalized_phone="+3902")

        # Stesso testo e stesso timestamp ma mittenti differenti
        r1 = make_norm_record("src1", "1", actor_from=actor1, text_content="Ciao", iso_timestamp="2024-04-23T20:30:15+00:00")
        r2 = make_norm_record("src2", "2", actor_from=actor2, text_content="Ciao", iso_timestamp="2024-04-23T20:30:15+00:00")

        res = resolver.resolve([r1, r2])
        # Devono rimanere 2 entità distinte!
        assert len(res.candidate_entities) == 2
        identifiers = {e.canonical_identifier for e in res.candidate_entities}
        assert identifiers == {"+3901", "+3902"}

    def test_duplicate_candidates_detected_without_deleting_records(self, resolver):
        """
        Requisito B8: i duplicati candidati sono rilevati e segnalati, MA nessun record viene eliminato.
        """
        actor = NormalizedActor(raw_value="+3901", actor_type="phone", normalized_phone="+3901")
        r1 = make_norm_record("cellebrite_csv", "1", actor_from=actor, text_content="Messaggio identico", iso_timestamp="2024-04-23T20:30:15+00:00")
        r2 = make_norm_record("cellebrite_json", "msg_01", actor_from=actor, text_content="Messaggio identico", iso_timestamp="2024-04-23T20:30:15+00:00")

        res = resolver.resolve([r1, r2])
        # Entità creata
        assert len(res.candidate_entities) == 1
        # Duplicato candidato rilevato
        assert len(res.duplicate_candidates) == 1
        dup = res.duplicate_candidates[0]
        assert len(dup.records) == 2
        assert ("cellebrite_csv", "1") in dup.records
        assert ("cellebrite_json", "msg_01") in dup.records
        assert res.total_records_processed == 2

    def test_records_and_provenance_remain_completely_unmodified(self, resolver):
        """
        Requisito B13: RawRecord e NormalizedRecord non vengono alterati.
        """
        actor = NormalizedActor(raw_value="+3901", actor_type="phone", normalized_phone="+3901")
        r = make_norm_record("cellebrite_csv", "1", actor_from=actor, text_content="Hello")
        raw_fields_before = dict(r.raw_record.raw_fields)

        res = resolver.resolve([r])
        assert dict(r.raw_record.raw_fields) == raw_fields_before
        assert r.text_content == "Hello"

    def test_determinism_and_reproducibility(self, resolver):
        """
        Requisito B9: due esecuzioni sugli stessi dati producono output identico.
        """
        actor1 = NormalizedActor(raw_value="user1@s.whatsapp.net", actor_type="jid", jid_local="user1", jid_domain="s.whatsapp.net")
        actor2 = NormalizedActor(raw_value="+3901", actor_type="phone", normalized_phone="+3901")
        records = [
            make_norm_record("wa", "1", actor_from=actor1),
            make_norm_record("csv", "2", actor_from=actor2),
        ]

        res1 = resolver.resolve(records)
        res2 = resolver.resolve(records)

        assert len(res1.candidate_entities) == len(res2.candidate_entities)
        for e1, e2 in zip(res1.candidate_entities, res2.candidate_entities):
            assert e1.candidate_id == e2.candidate_id
            assert e1.canonical_identifier == e2.canonical_identifier
            assert e1.entity_type == e2.entity_type

    def test_group_jid_is_classified_as_group_and_never_merged_with_phone(self, resolver):
        """
        Requisito A1: I JID @g.us rappresentano una chat/gruppo, NON una persona.
        Non devono mai essere uniti a numeri telefonici anche in caso di local part numerico.
        """
        group_jid = "1234567890-9876543210@g.us"
        actor_group = NormalizedActor(
            raw_value=group_jid,
            actor_type="jid",
            jid_local="1234567890-9876543210",
            jid_domain="g.us",
        )
        actor_phone = NormalizedActor(
            raw_value="+1234567890",
            actor_type="phone",
            normalized_phone="+1234567890",
        )
        r_group = make_norm_record("msgstore_db", "g1", actor_from=actor_group)
        r_phone = make_norm_record("cellebrite_csv", "p1", actor_from=actor_phone)

        res = resolver.resolve([r_group, r_phone])
        entities = {e.candidate_identifier: e for e in res.candidate_entities}
        assert group_jid in entities
        group_ent = entities[group_jid]
        assert group_ent.entity_type == "group"
        assert any(ev.evidence_type == EvidenceType.GROUP_JID_EXACT for ev in group_ent.evidence_chain)
        # Il telefono rimane una entità distinta
        assert "+1234567890" in entities
        phone_ent = entities["+1234567890"]
        assert phone_ent.entity_type == "phone"

    def test_local_user_entity_created(self, resolver):
        """
        Requisito A1: Il proprietario del dispositivo (LOCAL_USER) viene tracciato deterministicamente.
        """
        actor_local = NormalizedActor(
            raw_value="LOCAL_USER",
            actor_type="local_user",
        )
        r1 = make_norm_record("msgstore_db", "1", actor_from=actor_local)
        r2 = make_norm_record("msgstore_db", "2", actor_to=actor_local)

        res = resolver.resolve([r1, r2])
        entities = {e.candidate_identifier: e for e in res.candidate_entities}
        assert "LOCAL_USER" in entities
        local_ent = entities["LOCAL_USER"]
        assert local_ent.entity_type == "local_user"
        assert len(local_ent.references) == 2
        assert any(ev.evidence_type == EvidenceType.LOCAL_USER_EXACT for ev in local_ent.evidence_chain)

    def test_chat_and_media_ref_records_do_not_produce_person_entities(self, resolver):
        """
        Requisito A1: I record di tipo 'chat' o 'media_ref' non generano entità persona.
        """
        actor = NormalizedActor(
            raw_value="+390000000001@s.whatsapp.net",
            actor_type="jid",
            jid_local="+390000000001",
            jid_domain="s.whatsapp.net",
        )
        r_chat = make_norm_record("msgstore_db", "c1", record_type="chat", actor_from=actor)
        r_media = make_norm_record("msgstore_db", "m1", record_type="media_ref", actor_from=actor)

        res = resolver.resolve([r_chat, r_media])
        assert len(res.candidate_entities) == 0
        assert len(res.unresolved_references) == 0
