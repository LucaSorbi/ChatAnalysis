"""
tests/integration/test_cloud_e2e_dataset.py
-------------------------------------------
Validation tests for the synthetic cloud E2E benchmark dataset.

Verifies that:
1. The dataset is importable via CellebriteJsonImporter.
2. The full pipeline (RawRecord → Validation → Normalization → Entity Resolution
   → UnifiedMessage → ConversationEvidenceDocument) succeeds.
3. All 6 expected cases/conversations are produced.
4. All evidence_ids declared in ground_truth.json exist in the pipeline output.
5. No duplicate evidence_ids within any document.
6. Ground truth content is NOT included in the ConversationEvidenceDocument.
7. The pipeline is deterministic across two consecutive imports.
8. No external network connections are made.
9. No modification to existing layer semantics (no LLM calls).

DATA POLICY: SYNTHETIC DATA ONLY — no real personal data.
"""
from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from ai.models import ConversationEvidenceDocument
from ai.serializer import serialize_document_for_llm
from importer.cellebrite_json import CellebriteJsonImporter
from ui.ingestion import ingest_file_payload
from ui.models import IngestionStatus, SourceFormat

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_CLOUD_E2E_DIR = Path(__file__).resolve().parents[2] / "test_data" / "cloud_e2e"
_DATASET_FILE = _CLOUD_E2E_DIR / "messages.json"
_GROUND_TRUTH_FILE = _CLOUD_E2E_DIR / "ground_truth.json"

# Expected conversation chat_ids (from the pipeline)
_EXPECTED_CHAT_IDS = {
    "chat:cellebrite_json:cloud_case_1",
    "chat:cellebrite_json:cloud_case_2",
    "chat:cellebrite_json:cloud_case_3",
    "chat:cellebrite_json:cloud_case_4",
    "chat:cellebrite_json:cloud_case_5",
    "chat:cellebrite_json:cloud_case_6",
}


def _load_ground_truth() -> dict:
    """Load and parse the ground truth JSON file."""
    with open(_GROUND_TRUTH_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _run_pipeline() -> tuple:
    """Run the full ingestion pipeline and return (result, documents_dict)."""
    file_bytes = _DATASET_FILE.read_bytes()
    result = ingest_file_payload(
        file_bytes_or_io=file_bytes,
        original_filename="messages.json",
        source_format=SourceFormat.CELLEBRITE_JSON,
    )
    return result


# ===========================================================================
# Test class
# ===========================================================================


@pytest.mark.integration
class TestCloudE2EDataset:
    """Validation tests for the synthetic cloud E2E benchmark dataset."""

    # -----------------------------------------------------------------------
    # 1. Dataset is importable via existing importer
    # -----------------------------------------------------------------------

    def test_dataset_file_exists(self):
        """The synthetic dataset file must exist."""
        assert _DATASET_FILE.exists(), f"Dataset file not found: {_DATASET_FILE}"

    def test_ground_truth_file_exists(self):
        """The ground truth file must exist."""
        assert _GROUND_TRUTH_FILE.exists(), f"Ground truth file not found: {_GROUND_TRUTH_FILE}"

    def test_cellebrite_json_importer_can_import(self):
        """CellebriteJsonImporter must recognize the dataset."""
        importer = CellebriteJsonImporter()
        assert importer.can_import(_DATASET_FILE), \
            "CellebriteJsonImporter.can_import() returned False for synthetic dataset"

    def test_cellebrite_json_importer_produces_records(self):
        """CellebriteJsonImporter must produce at least 31 RawRecords (31 messages in dataset)."""
        importer = CellebriteJsonImporter()
        records = list(importer.import_records(_DATASET_FILE))
        assert len(records) == 31, f"Expected 31 records, got {len(records)}"

    # -----------------------------------------------------------------------
    # 2. Full pipeline succeeds
    # -----------------------------------------------------------------------

    def test_full_pipeline_succeeds(self):
        """The full ingestion pipeline must succeed on the synthetic dataset."""
        result = _run_pipeline()
        assert result.status == IngestionStatus.SUCCESS, \
            f"Pipeline status: {result.status}"

    def test_pipeline_produces_correct_counts(self):
        """Pipeline must produce exactly 31 raw records, 31 unified messages, 6 conversations."""
        result = _run_pipeline()
        assert result.summary.raw_record_count == 31
        assert result.summary.unified_message_count == 31
        assert result.summary.conversation_count == 6

    # -----------------------------------------------------------------------
    # 3. All expected cases/conversations exist
    # -----------------------------------------------------------------------

    def test_all_six_conversations_present(self):
        """All 6 expected conversation chat_ids must be present."""
        result = _run_pipeline()
        actual_chat_ids = set()
        for doc in result.documents.values():
            if doc.chat_id:
                actual_chat_ids.add(doc.chat_id)
        assert actual_chat_ids == _EXPECTED_CHAT_IDS, \
            f"Missing or unexpected chat_ids.\nExpected: {_EXPECTED_CHAT_IDS}\nActual: {actual_chat_ids}"

    def test_case_ids_match_ground_truth(self):
        """Each ground truth case must map to an existing conversation."""
        gt = _load_ground_truth()
        result = _run_pipeline()
        doc_by_chat = {doc.chat_id: doc for doc in result.documents.values() if doc.chat_id}
        for case in gt["cases"]:
            chat_id = case["conversation_chat_id"]
            assert chat_id in doc_by_chat, \
                f"Case {case['case_id']}: chat_id '{chat_id}' not found in pipeline output"

    # -----------------------------------------------------------------------
    # 4. All evidence_ids from ground truth exist in pipeline output
    # -----------------------------------------------------------------------

    def test_ground_truth_evidence_ids_exist(self):
        """Every evidence_id declared in ground_truth.json must exist in the pipeline output."""
        gt = _load_ground_truth()
        result = _run_pipeline()

        # Build a master set of all evidence_ids from all documents
        all_evidence_ids = set()
        for doc in result.documents.values():
            for section in doc.all_evidence_sections:
                all_evidence_ids.add(section.evidence_id)

        for case in gt["cases"]:
            for eid in case.get("expected_evidence_ids", []):
                assert eid in all_evidence_ids, \
                    f"Case {case['case_id']}: evidence_id '{eid}' declared in ground truth but NOT found in pipeline output"

    def test_ground_truth_document_ids_exist(self):
        """Every document_id declared in ground_truth.json must exist in the pipeline output."""
        gt = _load_ground_truth()
        result = _run_pipeline()
        actual_doc_ids = set(result.documents.keys())
        for case in gt["cases"]:
            doc_id = case["document_id"]
            assert doc_id in actual_doc_ids, \
                f"Case {case['case_id']}: document_id '{doc_id}' not found in pipeline output"

    # -----------------------------------------------------------------------
    # 5. No duplicate evidence_ids
    # -----------------------------------------------------------------------

    def test_no_duplicate_evidence_ids_within_documents(self):
        """No document may contain duplicate evidence_ids."""
        result = _run_pipeline()
        for doc_id, doc in result.documents.items():
            eids = [s.evidence_id for s in doc.all_evidence_sections]
            assert len(eids) == len(set(eids)), \
                f"Duplicate evidence_ids in document '{doc_id}': {eids}"

    def test_no_duplicate_evidence_ids_across_all_documents(self):
        """No evidence_id may appear in more than one document."""
        result = _run_pipeline()
        seen: dict[str, str] = {}
        for doc_id, doc in result.documents.items():
            for section in doc.all_evidence_sections:
                eid = section.evidence_id
                assert eid not in seen, \
                    f"Evidence_id '{eid}' found in both '{seen[eid]}' and '{doc_id}'"
                seen[eid] = doc_id

    # -----------------------------------------------------------------------
    # 6. Ground truth NOT included in ConversationEvidenceDocument
    # -----------------------------------------------------------------------

    def test_ground_truth_not_in_document_metadata(self):
        """Ground truth fields must NOT be serialized in document metadata."""
        result = _run_pipeline()
        gt = _load_ground_truth()

        gt_keywords = {"expected_decision", "human_rationale", "expected_discovery_topics",
                        "target_topic_id", "difficulty"}

        for doc in result.documents.values():
            meta_str = str(doc.metadata)
            for kw in gt_keywords:
                assert kw not in meta_str, \
                    f"Ground truth keyword '{kw}' found in document metadata of '{doc.document_id}'"

    def test_ground_truth_not_in_serialized_prompt(self):
        """Ground truth must NOT appear in the LLM-serialized evidence."""
        result = _run_pipeline()
        gt = _load_ground_truth()

        for doc in result.documents.values():
            serialized = serialize_document_for_llm(doc)
            # Check no ground truth rationale appears in serialized text
            for case in gt["cases"]:
                rationale = case.get("human_rationale", "")
                if rationale and len(rationale) > 20:
                    assert rationale not in serialized, \
                        f"Human rationale from case {case['case_id']} found in serialized LLM prompt"

    # -----------------------------------------------------------------------
    # 7. Deterministic across two imports
    # -----------------------------------------------------------------------

    def test_deterministic_import(self):
        """Two consecutive pipeline runs must produce identical outputs."""
        result1 = _run_pipeline()
        result2 = _run_pipeline()

        # Same number of documents
        assert len(result1.documents) == len(result2.documents)

        # Same document_ids
        assert set(result1.documents.keys()) == set(result2.documents.keys())

        # Same evidence_ids per document
        for doc_id in result1.documents:
            eids1 = sorted(s.evidence_id for s in result1.documents[doc_id].all_evidence_sections)
            eids2 = sorted(s.evidence_id for s in result2.documents[doc_id].all_evidence_sections)
            assert eids1 == eids2, \
                f"Non-deterministic evidence_ids in document '{doc_id}'"

        # Same evidence texts per document
        for doc_id in result1.documents:
            texts1 = [s.text for s in result1.documents[doc_id].all_evidence_sections]
            texts2 = [s.text for s in result2.documents[doc_id].all_evidence_sections]
            assert texts1 == texts2, \
                f"Non-deterministic evidence texts in document '{doc_id}'"

    # -----------------------------------------------------------------------
    # 8. No external network connections
    # -----------------------------------------------------------------------

    def test_no_external_network_during_import(self):
        """The pipeline must not make any external network connections."""
        original_connect = socket.socket.connect

        connections_made = []

        def _mock_connect(self_socket, address):
            connections_made.append(address)
            raise ConnectionRefusedError(f"Network blocked for test: {address}")

        with patch.object(socket.socket, "connect", _mock_connect):
            try:
                _run_pipeline()
            except ConnectionRefusedError:
                pass

        assert len(connections_made) == 0, \
            f"External network connections attempted: {connections_made}"

    # -----------------------------------------------------------------------
    # 9. No modification to existing layer semantics
    # -----------------------------------------------------------------------

    def test_no_llm_client_instantiated(self):
        """No LLM client must be instantiated during pipeline execution."""
        from ai.backend import BaseLocalLlmClient

        original_init = BaseLocalLlmClient.__init__
        llm_created = []

        def _tracking_init(self, *args, **kwargs):
            llm_created.append(type(self).__name__)
            original_init(self, *args, **kwargs)

        with patch.object(BaseLocalLlmClient, "__init__", _tracking_init):
            _run_pipeline()

        assert len(llm_created) == 0, \
            f"LLM clients instantiated during pipeline: {llm_created}"

    # -----------------------------------------------------------------------
    # Additional structural checks
    # -----------------------------------------------------------------------

    def test_all_records_have_correct_source_name(self):
        """All documents must have source_name='cellebrite_json'."""
        result = _run_pipeline()
        for doc in result.documents.values():
            assert doc.source_name == "cellebrite_json", \
                f"Document '{doc.document_id}' has source_name='{doc.source_name}'"

    def test_ground_truth_structure_valid(self):
        """Ground truth JSON must have valid structure with required fields."""
        gt = _load_ground_truth()
        assert "cases" in gt
        assert len(gt["cases"]) == 6

        required_fields = {
            "case_id", "conversation_chat_id", "document_id",
            "language_profile", "difficulty", "expected_evidence_ids",
            "expected_source_record_ids", "human_rationale",
        }
        for case in gt["cases"]:
            missing = required_fields - set(case.keys())
            assert not missing, \
                f"Case {case.get('case_id', '?')}: missing fields {missing}"

    def test_difficulty_values_correct(self):
        """Each case must have the correct difficulty classification."""
        gt = _load_ground_truth()
        expected_difficulties = {
            "CASE_1_EXPLICIT_PRESENT": "explicit",
            "CASE_2_IMPLICIT_PRESENT": "implicit",
            "CASE_3_AMBIGUOUS_UNCERTAIN": "ambiguous",
            "CASE_4_ABSENT": "absent",
            "CASE_5_MULTILINGUAL_PRESENT": "multilingual",
            "CASE_6_TOPIC_DISCOVERY": "discovery",
        }
        for case in gt["cases"]:
            cid = case["case_id"]
            assert cid in expected_difficulties, f"Unexpected case_id: {cid}"
            assert case["difficulty"] == expected_difficulties[cid], \
                f"Case {cid}: expected difficulty '{expected_difficulties[cid]}', got '{case['difficulty']}'"

    def test_expected_decisions_consistent(self):
        """Expected decisions must be consistent with difficulty type."""
        gt = _load_ground_truth()
        for case in gt["cases"]:
            diff = case["difficulty"]
            decision = case.get("expected_decision")
            if diff in ("explicit", "implicit", "multilingual"):
                assert decision == "PRESENT", \
                    f"Case {case['case_id']}: {diff} difficulty but expected_decision is '{decision}'"
            elif diff == "absent":
                assert decision == "ABSENT", \
                    f"Case {case['case_id']}: absent difficulty but expected_decision is '{decision}'"
            elif diff == "ambiguous":
                assert decision == "UNCERTAIN", \
                    f"Case {case['case_id']}: ambiguous difficulty but expected_decision is '{decision}'"
            elif diff == "discovery":
                assert decision is None, \
                    f"Case {case['case_id']}: discovery difficulty should have null expected_decision"

    def test_absent_case_has_empty_evidence(self):
        """The ABSENT case must declare empty expected_evidence_ids."""
        gt = _load_ground_truth()
        for case in gt["cases"]:
            if case["difficulty"] == "absent":
                assert case["expected_evidence_ids"] == [], \
                    f"Case {case['case_id']}: ABSENT case should have empty evidence_ids"

    def test_discovery_case_has_expected_topics(self):
        """The DISCOVERY case must declare expected_discovery_topics."""
        gt = _load_ground_truth()
        for case in gt["cases"]:
            if case["difficulty"] == "discovery":
                topics = case.get("expected_discovery_topics")
                assert topics is not None, \
                    f"Case {case['case_id']}: DISCOVERY case must have expected_discovery_topics"
                assert len(topics) >= 3, \
                    f"Case {case['case_id']}: DISCOVERY case must have at least 3 expected topics"

    def test_multilingual_case_has_multiple_languages(self):
        """The MULTILINGUAL case must declare at least 2 languages."""
        gt = _load_ground_truth()
        for case in gt["cases"]:
            if case["difficulty"] == "multilingual":
                langs = case.get("language_profile", [])
                assert len(langs) >= 2, \
                    f"Case {case['case_id']}: MULTILINGUAL case must have at least 2 languages"

    def test_case_bundle_counts(self):
        """Each case must have the expected number of bundles."""
        result = _run_pipeline()
        expected_counts = {
            "chat:cellebrite_json:cloud_case_1": 5,
            "chat:cellebrite_json:cloud_case_2": 5,
            "chat:cellebrite_json:cloud_case_3": 5,
            "chat:cellebrite_json:cloud_case_4": 5,
            "chat:cellebrite_json:cloud_case_5": 5,
            "chat:cellebrite_json:cloud_case_6": 6,
        }
        for doc in result.documents.values():
            if doc.chat_id in expected_counts:
                expected = expected_counts[doc.chat_id]
                actual = doc.message_count
                assert actual == expected, \
                    f"Chat '{doc.chat_id}': expected {expected} bundles, got {actual}"
