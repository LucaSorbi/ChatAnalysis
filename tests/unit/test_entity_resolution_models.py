"""
tests/unit/test_entity_resolution_models.py
-------------------------------------------
Test unitari per i modelli di Entity Resolution:
- EvidenceLevel
- EvidenceType
- EntityReference
- ResolutionEvidence
- CandidateEntity
- DuplicateCandidate
- ResolutionResult
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType

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


@pytest.mark.unit
class TestEntityResolutionEnums:

    def test_evidence_level_values(self):
        assert EvidenceLevel.EXACT.value == "EXACT"
        assert EvidenceLevel.STRONG.value == "STRONG"
        assert EvidenceLevel.WEAK.value == "WEAK"
        assert EvidenceLevel.UNRESOLVED.value == "UNRESOLVED"

    def test_evidence_type_values(self):
        assert EvidenceType.JID_EXACT.value == "JID_EXACT"
        assert EvidenceType.PHONE_CANONICAL.value == "PHONE_CANONICAL"
        assert EvidenceType.PHONE_JID_LOCAL.value == "PHONE_JID_LOCAL"
        assert EvidenceType.UNRESOLVED_ALIAS.value == "UNRESOLVED_ALIAS"
        assert EvidenceType.UNRESOLVED_CHAT_ID.value == "UNRESOLVED_CHAT_ID"
        assert EvidenceType.UNRESOLVED_HEURISTIC.value == "UNRESOLVED_HEURISTIC"


@pytest.mark.unit
class TestEntityReference:

    def test_create_valid_reference(self):
        ref = EntityReference(
            source_name="msgstore_db",
            source_record_id="1",
            actor_role="sender",
            raw_value="+390000000001@s.whatsapp.net",
            actor_type="jid",
            normalized_value="+390000000001@s.whatsapp.net",
        )
        assert ref.source_name == "msgstore_db"
        assert ref.source_record_id == "1"
        assert ref.actor_role == "sender"
        assert ref.actor_type == "jid"
        assert ref.normalized_value == "+390000000001@s.whatsapp.net"

    def test_is_immutable(self):
        ref = EntityReference(
            source_name="src",
            source_record_id="1",
            actor_role="contact",
            raw_value="+3901",
            actor_type="phone",
        )
        with pytest.raises(FrozenInstanceError):
            ref.source_name = "tampered"  # type: ignore[misc]

    def test_invalid_role_raises(self):
        with pytest.raises(ValueError, match="actor_role"):
            EntityReference(
                source_name="src",
                source_record_id="1",
                actor_role="invalid_role",
                raw_value="+3901",
                actor_type="phone",
            )


@pytest.mark.unit
class TestResolutionEvidence:

    def test_create_and_immutability(self):
        ev = ResolutionEvidence(
            evidence_type=EvidenceType.JID_EXACT,
            evidence_level=EvidenceLevel.EXACT,
            matched_value="+390000000001@s.whatsapp.net",
            reason="Exact JID match",
            source_records=(("msgstore_db", "1"), ("wa_db", "1")),
        )
        assert ev.evidence_level == EvidenceLevel.EXACT
        assert ev.matched_value == "+390000000001@s.whatsapp.net"
        assert len(ev.source_records) == 2
        with pytest.raises(FrozenInstanceError):
            ev.matched_value = "other"  # type: ignore[misc]


@pytest.mark.unit
class TestCandidateEntity:

    def test_create_and_immutability(self):
        ref1 = EntityReference(
            source_name="msgstore_db",
            source_record_id="1",
            actor_role="sender",
            raw_value="123@s.whatsapp.net",
            actor_type="jid",
            normalized_value="123@s.whatsapp.net",
        )
        ev = ResolutionEvidence(
            evidence_type=EvidenceType.JID_EXACT,
            evidence_level=EvidenceLevel.EXACT,
            matched_value="123@s.whatsapp.net",
            reason="match",
            source_records=(("msgstore_db", "1"),),
        )
        ent = CandidateEntity(
            candidate_id="entity_candidate:1",
            canonical_identifier="123@s.whatsapp.net",
            entity_type="jid",
            references=(ref1,),
            evidence_chain=(ev,),
            display_names=("Contatto_001",),
        )
        assert ent.candidate_id == "entity_candidate:1"
        assert ent.canonical_identifier == "123@s.whatsapp.net"
        assert ent.display_names == ("Contatto_001",)
        with pytest.raises(FrozenInstanceError):
            ent.candidate_id = "entity_candidate:2"  # type: ignore[misc]


@pytest.mark.unit
class TestDuplicateCandidate:

    def test_create_and_immutability(self):
        dup = DuplicateCandidate(
            candidate_id="duplicate_candidate:1",
            records=(("msgstore_db", "1"), ("cellebrite_csv", "row:5")),
            reason="Shared text and timestamp",
            confidence=EvidenceLevel.EXACT,
        )
        assert dup.candidate_id == "duplicate_candidate:1"
        assert len(dup.records) == 2
        assert dup.confidence == EvidenceLevel.EXACT
        with pytest.raises(FrozenInstanceError):
            dup.candidate_id = "duplicate_candidate:2"  # type: ignore[misc]


@pytest.mark.unit
class TestResolutionResult:

    def test_create_with_frozen_metadata(self):
        res = ResolutionResult(
            candidate_entities=(),
            unresolved_references=(),
            duplicate_candidates=(),
            total_records_processed=0,
            metadata={"status": "ok"},
        )
        assert isinstance(res.metadata, MappingProxyType)
        assert res.metadata["status"] == "ok"
        with pytest.raises(TypeError):
            res.metadata["status"] = "tampered"  # type: ignore[index]
