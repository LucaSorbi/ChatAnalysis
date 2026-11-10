"""
scripts/evaluate_semantic_benchmark_live.py
-------------------------------------------
Strumento ripetibile di valutazione scientifica LIVE (su LM Studio locale) per il
corpus sintetico italiano annotato (DEV + HOLDOUT).

Misura end-to-end:
1. Topic Discovery (COMPLETE/PARTIAL, copertura evidenze, recall/precision rispetto ai gold topic,
   lingua italiana, assenza di meta-label generiche, duplicati, precisione citazioni).
2. Verifica Indipendente di Consistenza Semantica (`verify_discovered_topic` con `LABEL_AND_DESCRIPTION`
   senza passare gli `evidence_id` della Discovery).
3. Topic Detection mirata su query positive, negative, con negazione, multimodali (STT/OCR),
   foto senza OCR e input avversari.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.lmstudio import DEFAULT_OPERATIONAL_MODEL, LmStudioClient
from ai.models import AnalysisLanguageStrategy, TopicDecision, TopicQuery
from analysis.checkpoint import AnalysisCheckpointStore
from analysis.models import OperationalProfile
from analysis.service import LocalTopicAnalysisService
from tests.fixtures.synthetic_semantic_corpus import (
    SyntheticBenchmarkCase,
    build_synthetic_semantic_corpus,
    evaluate_case_discovery,
)


def _compute_jaccard(cons_res: Any) -> float:
    union_set = set(cons_res.discovery_evidence_ids).union(cons_res.detection_evidence_ids)
    if not union_set:
        return 0.0
    return round(len(set(cons_res.overlap_evidence_ids)) / len(union_set), 4)


def run_benchmark(
    split_filter: str = "ALL",
    case_filter: str | None = None,
    model_id: str = DEFAULT_OPERATIONAL_MODEL,
    base_url: str = "http://127.0.0.1:1234",
    max_verify_per_case: int = 2,
    output_path: Path | None = None,
    checkpoint_dir: Path | None = None,
) -> dict[str, Any]:
    corpus = build_synthetic_semantic_corpus()
    selected_cases: list[SyntheticBenchmarkCase] = []
    for c in corpus:
        if split_filter != "ALL" and c.split != split_filter:
            continue
        if case_filter and c.case_id != case_filter:
            continue
        selected_cases.append(c)

    client = LmStudioClient(base_url=base_url, model_id=model_id, default_timeout_seconds=240.0)

    case_results: list[dict[str, Any]] = []
    t_global_start = time.perf_counter()

    tmp_ctx = tempfile.TemporaryDirectory(prefix="chatanalysis_eval_ckpt_") if checkpoint_dir is None else None
    try:
        eff_ckpt_dir = checkpoint_dir if checkpoint_dir is not None else Path(tmp_ctx.name)  # type: ignore[union-attr]
        eff_ckpt_dir.mkdir(parents=True, exist_ok=True)
        ckpt_store = AnalysisCheckpointStore(checkpoint_dir=eff_ckpt_dir)
        service = LocalTopicAnalysisService(
            base_url=base_url,
            default_model=model_id,
            timeout_seconds=240.0,
            checkpoint_store=ckpt_store,
            enable_checkpointing=True,
            profile=OperationalProfile.BALANCED,
        )

        for idx, case in enumerate(selected_cases, start=1):
            print(f"\n[{idx}/{len(selected_cases)}] Caso {case.case_id} ({case.split} - Scenario {case.scenario}): {case.title}", flush=True)
            t0 = time.perf_counter()
            disc_res = service.run_topic_discovery(
                document=case.document,
                client=client,
                model_name=model_id,
                max_topics=case.max_topics,
                temperature=0.0,
                seed=42,
            )
            disc_elapsed = round(time.perf_counter() - t0, 3)

            disc_meta = dict(disc_res.metadata) if disc_res.metadata else {}
            disc_eval = evaluate_case_discovery(case, disc_res.topics)
            disc_eval["status"] = disc_meta.get("status", "UNKNOWN")
            disc_eval["completed_chunks"] = disc_meta.get("completed_chunk_count", 0)
            disc_eval["total_chunks"] = disc_meta.get("chunk_count", 0)
            disc_eval["discovery_duration_seconds"] = disc_elapsed
            disc_eval["topics_detail"] = [
                {
                    "label": t.label,
                    "short_description": t.short_description,
                    "evidence_ids": list(t.evidence_ids),
                }
                for t in disc_res.topics
            ]
            print(
                f"  -> Discovery: status={disc_eval['status']} | topics={len(disc_res.topics)} (gold={len(case.gold_topics)}) "
                f"| recall={disc_eval['topic_recall']} | precision={disc_eval['topic_precision']} "
                f"| generic={disc_eval['generic_or_nonsense_count']} | wrong_lang={disc_eval['wrong_language_count']} "
                f"| dup={disc_eval['duplicate_topics_count']} | {disc_elapsed:.2f}s",
                flush=True,
            )
            for t in disc_res.topics:
                print(f"     * [{t.label}] -> {t.short_description} (eids={list(t.evidence_ids)})", flush=True)

            # Verifica indipendente sui topic scoperti (fino a max_verify_per_case)
            verifications: list[dict[str, Any]] = []
            disc_run_id = disc_meta.get("run_id")
            for v_idx, topic in enumerate(disc_res.topics[:max_verify_per_case]):
                tv0 = time.perf_counter()
                det_v_res, cons_res = service.verify_discovered_topic(
                    document=case.document,
                    discovered_topic=topic,
                    discovery_run_id=disc_run_id,
                    discovery_result=disc_res,
                    client=client,
                    model_name=model_id,
                    temperature=0.0,
                    seed=42,
                )
                tv_elapsed = round(time.perf_counter() - tv0, 3)
                jacc = _compute_jaccard(cons_res)
                verifications.append(
                    {
                        "topic_label": topic.label,
                        "topic_description": topic.short_description,
                        "discovery_evidence_ids": list(topic.evidence_ids),
                        "detection_decision": det_v_res.decision.value,
                        "detection_evidence_ids": list(det_v_res.evidence_ids),
                        "detection_rationale": det_v_res.rationale,
                        "consistency_status": cons_res.status.value,
                        "jaccard_similarity": jacc,
                        "duration_seconds": tv_elapsed,
                    }
                )
                print(
                    f"     [Verify] '{topic.label}' -> decision={det_v_res.decision.value}, "
                    f"status={cons_res.status.value}, jaccard={jacc:.2f} ({tv_elapsed:.2f}s)",
                    flush=True,
                )

            # Detection mirata sulle query gold
            detections: list[dict[str, Any]] = []
            for q in case.detection_queries:
                td0 = time.perf_counter()
                q_obj = TopicQuery(
                    topic_id=q.query_id,
                    label=q.label,
                    description=q.description,
                )
                det_res = service.run_topic_detection(
                    document=case.document,
                    topic=q_obj,
                    client=client,
                    model_name=model_id,
                    temperature=0.0,
                    seed=42,
                )
                td_elapsed = round(time.perf_counter() - td0, 3)
                decision_match = det_res.decision == q.expected_decision
                eid_overlap = len(set(det_res.evidence_ids).intersection(q.expected_evidence_ids))
                if q.expected_decision == TopicDecision.PRESENT:
                    evidence_valid = eid_overlap >= 1
                else:
                    evidence_valid = len(det_res.evidence_ids) == 0

                detections.append(
                    {
                        "query_id": q.query_id,
                        "category": q.category,
                        "label": q.label,
                        "expected_decision": q.expected_decision.value,
                        "actual_decision": det_res.decision.value,
                        "decision_correct": decision_match,
                        "expected_evidence_ids": list(q.expected_evidence_ids),
                        "actual_evidence_ids": list(det_res.evidence_ids),
                        "evidence_valid": evidence_valid,
                        "rationale": det_res.rationale,
                        "duration_seconds": td_elapsed,
                    }
                )
                print(
                    f"     [Detect:{q.category}] '{q.label}' -> atteso={q.expected_decision.value}, "
                    f"ottenuto={det_res.decision.value} (correct={decision_match}, ev_valid={evidence_valid}, {td_elapsed:.2f}s)",
                    flush=True,
                )

            disc_eval["independent_verifications"] = verifications
            disc_eval["gold_detections"] = detections
            case_results.append(disc_eval)
            if output_path is not None:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps({"partial": True, "completed_cases": len(case_results), "cases": case_results}, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()

    total_elapsed = round(time.perf_counter() - t_global_start, 3)

    # Calcolo aggregati per split
    summary_by_split: dict[str, Any] = {}
    for sp in ("DEV", "HOLDOUT", "ALL"):
        sub = [r for r in case_results if sp == "ALL" or r["split"] == sp]
        if not sub:
            continue
        all_ver = [v for r in sub for v in r["independent_verifications"]]
        all_det = [d for r in sub for d in r["gold_detections"]]
        summary_by_split[sp] = {
            "cases_count": len(sub),
            "complete_rate": round(
                sum(1 for r in sub if r["status"] in ("COMPLETE", "COMPLETE_NO_TOPICS")) / len(sub), 4
            ),
            "mean_topic_recall": round(sum(r["topic_recall"] for r in sub) / len(sub), 4),
            "mean_topic_precision": round(sum(r["topic_precision"] for r in sub) / len(sub), 4),
            "mean_evidence_precision": round(sum(r["evidence_precision"] for r in sub) / len(sub), 4),
            "total_discovered_topics": sum(r["discovered_topics_count"] for r in sub),
            "total_gold_topics": sum(r["gold_topics_count"] for r in sub),
            "total_covered_gold_topics": sum(r["covered_gold_topics"] for r in sub),
            "total_generic_or_nonsense": sum(r["generic_or_nonsense_count"] for r in sub),
            "total_wrong_language": sum(r["wrong_language_count"] for r in sub),
            "total_duplicates": sum(r["duplicate_topics_count"] for r in sub),
            "total_forbidden_hits": sum(r["forbidden_topic_hits"] for r in sub),
            "verifications_total": len(all_ver),
            "verifications_consistent_or_overlap": sum(
                1 for v in all_ver if v["consistency_status"] in ("CONSISTENT", "PARTIAL_OVERLAP", "WEAK_OVERLAP")
            ),
            "verifications_discrepancy_absent": sum(
                1 for v in all_ver if v["consistency_status"] == "DISCREPANCY_ABSENT"
            ),
            "detections_total": len(all_det),
            "detections_decision_correct": sum(1 for d in all_det if d["decision_correct"]),
            "detections_fully_valid": sum(1 for d in all_det if d["decision_correct"] and d["evidence_valid"]),
            "detections_accuracy": round(
                sum(1 for d in all_det if d["decision_correct"] and d["evidence_valid"]) / len(all_det), 4
            )
            if all_det
            else 1.0,
        }

    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": model_id,
        "base_url": base_url,
        "context_length": 8192,
        "temperature": 0.0,
        "seed": 42,
        "total_duration_seconds": total_elapsed,
        "summary_by_split": summary_by_split,
        "cases": case_results,
    }

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n[OK] Risultati salvati in: {output_path}", flush=True)

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Esegue il benchmark semantico sintetico LIVE su LM Studio.")
    parser.add_argument("--split", choices=["DEV", "HOLDOUT", "ALL"], default="ALL")
    parser.add_argument("--case", default=None, help="Filtra per singolo case_id")
    parser.add_argument("--model", default=DEFAULT_OPERATIONAL_MODEL)
    parser.add_argument("--max-verify", type=int, default=2)
    parser.add_argument("--output", default=None, help="Percorso file JSON di output")
    parser.add_argument("--checkpoint-dir", default=None, help="Cartella checkpoint isolata opzionale")
    args = parser.parse_args()

    out = Path(args.output) if args.output else None
    ckpt = Path(args.checkpoint_dir) if args.checkpoint_dir else None
    run_benchmark(
        split_filter=args.split,
        case_filter=args.case,
        model_id=args.model,
        max_verify_per_case=args.max_verify,
        output_path=out,
        checkpoint_dir=ckpt,
    )


if __name__ == "__main__":
    main()
