#!/usr/bin/env python3
"""
scripts/generate_synthetic_export_examples.py
---------------------------------------------
Genera gli esempi JSON sintetici conformi a schema 1.1.0 e safe-by-default
per la documentazione scientifica di ChatAnalysis in docs/examples/.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from ai.models import (
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from analysis.export import (
    build_topic_detection_export_report,
    build_topic_discovery_export_report,
    serialize_report_to_json,
)
from analysis.models import (
    AnalysisOrigin,
    AttestationLevel,
    ConsistencyResult,
    ConsistencyStatus,
)
from ui.demo import build_synthetic_demo_dataset


def generate_all_examples() -> None:
    out_dir = repo_root / "docs" / "examples"
    out_dir.mkdir(parents=True, exist_ok=True)

    doc, det_list, disc_list = build_synthetic_demo_dataset()

    # 1. Detection Complete
    base_det = det_list[0]
    meta_det = dict(base_det.metadata or {})
    meta_det.update({
        "run_id": "run_detection_synth_c1",
        "origin": AnalysisOrigin.SYNTHETIC_REFERENCE.value,
        "attestation_level": AttestationLevel.MOCK_VERIFIED.value,
        "model": "qwen2.5-7b-instruct",
        "requested_model": "qwen2.5-7b-instruct",
        "temperature": 0.0,
        "max_tokens": 512,
        "timeout_seconds": 240.0,
        "chunk_count": 3,
        "completed_chunk_count": 3,
        "failed_chunk_count": 0,
        "status": "COMPLETE",
        "start_timestamp": "2026-10-21T10:00:00Z",
        "end_timestamp": "2026-10-21T10:00:05Z",
        "duration_seconds": 5.12,
    })
    det_c1 = TopicDetectionResult(
        topic=base_det.topic,
        decision=base_det.decision,
        rationale=base_det.rationale,
        evidence_ids=base_det.evidence_ids,
        provenance_document_id=doc.document_id,
        metadata=meta_det,
    )
    cons_res = ConsistencyResult(
        document_id=doc.document_id,
        discovery_run_id="run_discovery_synth_c1",
        detection_run_id="run_detection_synth_c1",
        topic_label=base_det.topic.label,
        status=ConsistencyStatus.CONSISTENT,
        rationale="Correlazione convalidata tra Discovery e Detection.",
        discovery_evidence_ids=base_det.evidence_ids,
        detection_evidence_ids=base_det.evidence_ids,
        overlap_evidence_ids=base_det.evidence_ids,
    )
    rep_det = build_topic_detection_export_report(
        det_c1,
        doc,
        consistency_result=cons_res,
        redact_sensitive_topics=True,
        custom_commit="20261021abcdef0123456789abcdef0123456789",
    )
    (out_dir / "example_topic_detection_export_synthetic.json").write_text(
        serialize_report_to_json(rep_det), encoding="utf-8"
    )

    # 2. Discovery Complete
    base_disc = disc_list[0]
    meta_disc = dict(base_disc.metadata or {})
    meta_disc.update({
        "run_id": "run_discovery_synth_c1",
        "origin": AnalysisOrigin.SYNTHETIC_REFERENCE.value,
        "attestation_level": AttestationLevel.MOCK_VERIFIED.value,
        "model": "qwen2.5-7b-instruct",
        "requested_model": "qwen2.5-7b-instruct",
        "temperature": 0.0,
        "max_tokens": 1024,
        "timeout_seconds": 240.0,
        "chunk_count": 3,
        "completed_chunk_count": 3,
        "failed_chunk_count": 0,
        "status": "COMPLETE",
        "start_timestamp": "2026-10-21T09:50:00Z",
        "end_timestamp": "2026-10-21T09:50:12Z",
        "duration_seconds": 12.35,
    })
    disc_c1 = TopicDiscoveryResult(
        topics=base_disc.topics,
        provenance_document_id=doc.document_id,
        metadata=meta_disc,
    )
    rep_disc = build_topic_discovery_export_report(
        disc_c1,
        doc,
        redact_sensitive_topics=True,
        custom_commit="20261021abcdef0123456789abcdef0123456789",
    )
    (out_dir / "example_topic_discovery_export_synthetic.json").write_text(
        serialize_report_to_json(rep_disc), encoding="utf-8"
    )

    # 3. Detection Partial 17/26
    diag_17 = []
    for i in range(17):
        diag_17.append({"chunk_index": i, "status": "SUCCESS", "attempts": 1, "duration_seconds": 1.2, "model_id": "qwen2.5-7b-instruct"})
    for i in range(17, 26):
        diag_17.append({
            "chunk_index": i,
            "status": "FAILED",
            "attempts": 2,
            "duration_seconds": 2.0,
            "model_id": "qwen2.5-7b-instruct",
            "error_type": "AiStructuredOutputError",
            "diagnostic_category": "JSON_SCHEMA_ERROR",
            "failure_phase": "structured_parsing",
            "error_detail": "[REDACTED_DIAGNOSTIC_DETAIL]",
        })
    p_meta_det = {
        "run_id": "run_detection_partial_17of26",
        "origin": AnalysisOrigin.SYNTHETIC_REFERENCE.value,
        "attestation_level": AttestationLevel.MOCK_VERIFIED.value,
        "model": "qwen2.5-7b-instruct",
        "chunk_count": 26,
        "completed_chunk_count": 17,
        "failed_chunk_count": 9,
        "status": "PARTIAL",
        "duration_seconds": 38.4,
        "chunk_diagnostics": diag_17,
    }
    p_det_res = TopicDetectionResult(
        topic=TopicQuery(topic_id="top_narc", label="Sostanze stupefacenti", description="Spaccio"),
        decision=TopicDecision.UNCERTAIN,
        rationale="17 chunk completati senza riscontro, 9 chunk non analizzabili per errori di schema.",
        evidence_ids=(),
        provenance_document_id=doc.document_id,
        metadata=p_meta_det,
    )
    rep_p_det = build_topic_detection_export_report(
        p_det_res,
        doc,
        redact_sensitive_topics=True,
        custom_commit="20261021abcdef0123456789abcdef0123456789",
    )
    (out_dir / "example_topic_detection_partial_17of26_synthetic.json").write_text(
        serialize_report_to_json(rep_p_det), encoding="utf-8"
    )

    # 4. Discovery Partial 25/26
    diag_25 = []
    for i in range(26):
        if i == 10:
            diag_25.append({
                "chunk_index": 10,
                "status": "FAILED",
                "attempts": 2,
                "duration_seconds": 1.8,
                "model_id": "qwen2.5-7b-instruct",
                "error_type": "AiInvalidEvidenceCitationError",
                "diagnostic_category": "INVALID_EVIDENCE_ID",
                "failure_phase": "evidence_validation",
                "error_detail": "[REDACTED_DIAGNOSTIC_DETAIL]",
            })
        else:
            diag_25.append({"chunk_index": i, "status": "SUCCESS", "attempts": 1, "duration_seconds": 1.1, "model_id": "qwen2.5-7b-instruct"})
    p_meta_disc = {
        "run_id": "run_discovery_partial_25of26",
        "origin": AnalysisOrigin.SYNTHETIC_REFERENCE.value,
        "attestation_level": AttestationLevel.MOCK_VERIFIED.value,
        "model": "qwen2.5-7b-instruct",
        "chunk_count": 26,
        "completed_chunk_count": 25,
        "failed_chunk_count": 1,
        "status": "PARTIAL",
        "duration_seconds": 29.3,
        "chunk_diagnostics": diag_25,
    }
    p_disc_res = TopicDiscoveryResult(
        topics=(DiscoveredTopic(label="Tema Valido 1", short_description="Descrizione 1", evidence_ids=("E001",)),),
        provenance_document_id=doc.document_id,
        metadata=p_meta_disc,
    )
    rep_p_disc = build_topic_discovery_export_report(
        p_disc_res,
        doc,
        redact_sensitive_topics=True,
        custom_commit="20261021abcdef0123456789abcdef0123456789",
    )
    (out_dir / "example_topic_discovery_partial_25of26_synthetic.json").write_text(
        serialize_report_to_json(rep_p_disc), encoding="utf-8"
    )

    print("[+] Esempi sintetici generati con successo in docs/examples/ (schema 1.1.0)")


if __name__ == "__main__":
    generate_all_examples()
