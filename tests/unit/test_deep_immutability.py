"""
tests/unit/test_deep_immutability.py
------------------------------------
Test di unità dedicati alla verifica della Deep Immutability (D4):
- freeze_structural() ricorsivo su dizionari, liste e collezioni eterogenee
- Immutabilità profonda su CandidateEntity, DuplicateCandidate, ResolutionResult
- Immutabilità profonda su NormalizedRecord
- Immutabilità profonda su Participant, Chat, UnifiedMessage
- Immutabilità profonda su UnifiedBuildContext
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
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
from core.immutability import freeze_structural
from importer.models import RawRecord, freeze_structural as importer_freeze_structural
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.context import UnifiedBuildContext
from unified.models import Chat, Participant, UnifiedMessage
from validation.models import ValidationResult


@pytest.mark.unit
class TestFreezeStructural:

    def test_primitives_remain_unchanged(self):
        assert freeze_structural(42) == 42
        assert freeze_structural("hello") == "hello"
        assert freeze_structural(3.14) == 3.14
        assert freeze_structural(True) is True
        assert freeze_structural(None) is None

    def test_reexport_backward_compatibility(self):
        assert freeze_structural is importer_freeze_structural

    def test_nested_dict_deep_freezing(self):
        mutable_data = {
            "level1": {
                "level2": {
                    "key": "val"
                }
            }
        }
        frozen = freeze_structural(mutable_data)
        assert isinstance(frozen, MappingProxyType)
        assert isinstance(frozen["level1"], MappingProxyType)
        assert isinstance(frozen["level1"]["level2"], MappingProxyType)
        assert frozen["level1"]["level2"]["key"] == "val"

        # Tentativo di mutazione fallisce
        with pytest.raises(TypeError):
            frozen["level1"]["level2"]["key"] = "hacked"  # type: ignore[index]

        # Mutazione del dizionario originale non impatta la struttura congelata
        mutable_data["level1"]["level2"]["key"] = "changed_in_original"
        assert frozen["level1"]["level2"]["key"] == "val"

    def test_nested_list_and_dict_deep_freezing(self):
        mutable_data = {
            "items": [
                {"name": "item1"},
                {"name": "item2", "subitems": [1, 2, 3]},
            ]
        }
        frozen = freeze_structural(mutable_data)
        assert isinstance(frozen, MappingProxyType)
        assert isinstance(frozen["items"], tuple)
        assert isinstance(frozen["items"][0], MappingProxyType)
        assert isinstance(frozen["items"][1]["subitems"], tuple)

        with pytest.raises(TypeError):
            frozen["items"][0]["name"] = "mutated"  # type: ignore[index]


@pytest.mark.unit
class TestEntityResolutionDeepImmutability:

    def test_candidate_entity_collections_and_metadata(self):
        ref_list = [
            EntityReference(
                source_name="src",
                source_record_id="1",
                actor_role="sender",
                raw_value="+3901",
                actor_type="phone",
            )
        ]
        disp_list = ["Contatto 1"]
        cand = CandidateEntity(
            candidate_id="cand:1",
            candidate_identifier="+3901",
            entity_type="phone",
            references=ref_list,  # type: ignore[arg-type]
            display_names=disp_list,  # type: ignore[arg-type]
        )
        # Deve essere una tupla
        assert isinstance(cand.references, tuple)
        assert isinstance(cand.display_names, tuple)

        # Mutazione della lista originaria non altera il CandidateEntity
        disp_list.append("Contatto 2")
        assert len(cand.display_names) == 1

        with pytest.raises(FrozenInstanceError):
            cand.candidate_identifier = "mutated"  # type: ignore[misc]

    def test_resolution_result_nested_metadata(self):
        res = ResolutionResult(
            candidate_entities=(),
            unresolved_references=(),
            duplicate_candidates=(),
            total_records_processed=0,
            metadata={"config": {"clustering": "strict", "thresholds": [0.9, 0.95]}},
        )
        assert isinstance(res.metadata, MappingProxyType)
        assert isinstance(res.metadata["config"], MappingProxyType)
        assert isinstance(res.metadata["config"]["thresholds"], tuple)

        with pytest.raises(TypeError):
            res.metadata["config"]["clustering"] = "lenient"  # type: ignore[index]


@pytest.mark.unit
class TestUnifiedModelsDeepImmutability:

    def _make_dummy_norm_record(self) -> NormalizedRecord:
        raw = RawRecord(
            source_name="src",
            source_path="/path",
            source_record_id="1",
            record_type="message",
            raw_fields={},
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
            source_name="src",
            source_record_id="1",
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            metadata={"nested": {"field": "val"}},
        )

    def test_participant_nested_metadata_frozen(self):
        p = Participant(
            participant_id="p1",
            identifier="id1",
            metadata={"tags": ["tag1", "tag2"], "info": {"rank": 1}},
        )
        assert isinstance(p.metadata, MappingProxyType)
        assert isinstance(p.metadata["tags"], tuple)
        assert isinstance(p.metadata["info"], MappingProxyType)

        with pytest.raises(TypeError):
            p.metadata["info"]["rank"] = 2  # type: ignore[index]

    def test_chat_nested_metadata_and_participants_frozen(self):
        p1 = Participant(participant_id="p1", identifier="id1")
        plist = [p1]
        c = Chat(
            chat_id="c1",
            chat_type="direct",
            participants=plist,  # type: ignore[arg-type]
            metadata={"details": {"network": "whatsapp"}},
        )
        assert isinstance(c.participants, tuple)
        plist.append(Participant(participant_id="p2", identifier="id2"))
        assert len(c.participants) == 1

        assert isinstance(c.metadata, MappingProxyType)
        assert isinstance(c.metadata["details"], MappingProxyType)
        with pytest.raises(TypeError):
            c.metadata["details"]["network"] = "telegram"  # type: ignore[index]

    def test_unified_message_nested_metadata_and_duplicates_frozen(self):
        rec = self._make_dummy_norm_record()
        dup_list = ["dup:1", "dup:2"]
        msg = UnifiedMessage(
            message_id="unified:src:1",
            source_name="src",
            source_record_id="1",
            source_path="/path",
            record_type="message",
            timestamp=rec.timestamp,
            provenance_record=rec,
            duplicate_candidate_ids=dup_list,  # type: ignore[arg-type]
            metadata={"flags": {"forensic_verified": True, "notes": ["note1"]}},
        )
        assert isinstance(msg.duplicate_candidate_ids, tuple)
        dup_list.append("dup:3")
        assert len(msg.duplicate_candidate_ids) == 2

        assert isinstance(msg.metadata, MappingProxyType)
        assert isinstance(msg.metadata["flags"], MappingProxyType)
        assert isinstance(msg.metadata["flags"]["notes"], tuple)

        with pytest.raises(TypeError):
            msg.metadata["flags"]["forensic_verified"] = False  # type: ignore[index]

    def test_unified_build_context_deep_immutability(self):
        ctx = UnifiedBuildContext.create(
            chat_titles={"c1": "Titolo 1"},
            metadata={"settings": {"mode": "strict"}},
        )
        assert isinstance(ctx.chat_titles, MappingProxyType)
        assert isinstance(ctx.metadata, MappingProxyType)
        assert isinstance(ctx.metadata["settings"], MappingProxyType)

        with pytest.raises(TypeError):
            ctx.chat_titles["c1"] = "Nuovo Titolo"  # type: ignore[index]
        with pytest.raises(TypeError):
            ctx.metadata["settings"]["mode"] = "loose"  # type: ignore[index]
