"""
scripts/run_phase2_live_detection_and_consistency.py
---------------------------------------------------
FASE 2: Verifica di Topic Detection LIVE indipendente e calcolo consistenza.

Requisiti da prompt operativo:
1. Detection LIVE sul tema reale meglio supportato dalla Discovery (positivo)
   e Detection LIVE su un tema assente (negativo, es. "mutuo bancario immobiliare").
2. Prompt e inferenza rigorosamente indipendenti dalla Discovery (nessuna copia di evidence_ids).
3. Calcolo consistenza (Jaccard similarity e sovrapposizione forense) tra Discovery e Detection.
4. Tracciamento PRESENT/ABSENT/UNCERTAIN, evidenze e provenance forense al 100%.
5. Non compromettere né mescolare i checkpoint di Discovery.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ai.backend import BaseLlmClient, LlmCompletionResponse
from ai.lmstudio import LmStudioClient, DEFAULT_OPERATIONAL_MODEL
from ai.models import ConversationEvidenceDocument, DiscoveredTopic, TopicDecision, TopicQuery
from analysis.checkpoint import AnalysisCheckpointStore
from analysis.consistency import ConsistencyService
from analysis.models import OperationalProfile
from analysis.service import LocalTopicAnalysisService
from ui.ingestion import ingest_file_payload
from ui.models import IngestionRequest, SourceFormat


class AuditingLmStudioClient(BaseLlmClient):
    """Wrapper di audit per registrare le risposte HTTP reali di Detection in locale privato."""

    def __init__(self, inner: LmStudioClient, audit_dir: Path):
        self.inner = inner
        self.audit_dir = audit_dir
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.calls_count = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.audit_records: list[dict[str, Any]] = []

    @property
    def model_id(self) -> str:
        return self.inner.model_id

    @property
    def base_url(self) -> str:
        return self.inner.base_url

    def is_available(self) -> bool:
        return self.inner.is_available()

    def list_models(self) -> tuple[str, ...]:
        return self.inner.list_models()

    def get_models_detailed(self) -> list[dict[str, Any]]:
        return self.inner.get_models_detailed()

    def get_binding(self, model_id: str) -> Any:
        return self.inner.get_binding(model_id)

    def is_equivalent_model(self, target_model: str, response_model: str) -> bool:
        return self.inner.is_equivalent_model(target_model, response_model)

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float = 240.0,
        **kwargs: Any,
    ) -> LlmCompletionResponse:
        self.calls_count += 1
        t_start = time.perf_counter()
        req_hash = hashlib.sha256(json.dumps(messages, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        resp = self.inner.chat_completion(
            messages=messages,
            model_id=model_id,
            schema=schema,
            temperature=temperature,
            seed=seed,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            **kwargs,
        )
        dur = round(time.perf_counter() - t_start, 3)

        p_tok = getattr(resp, "prompt_tokens", 0) or 0
        c_tok = getattr(resp, "completion_tokens", 0) or 0
        self.total_prompt_tokens += p_tok
        self.total_completion_tokens += c_tok

        raw_meta = getattr(resp, "metadata", {}) or {}
        raw_resp = getattr(resp, "raw_response", {}) or {}
        f_reason = (
            raw_meta.get("finish_reason")
            or (raw_resp.get("finish_reason") if isinstance(raw_resp, Mapping) else None)
        )

        audit_entry = {
            "call_index": self.calls_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_hash": req_hash,
            "model": resp.model,
            "duration_seconds": dur,
            "prompt_tokens": p_tok,
            "completion_tokens": c_tok,
            "finish_reason": f_reason,
            "response_content": resp.content,
        }
        self.audit_records.append(audit_entry)

        audit_file = self.audit_dir / f"detection_call_{self.calls_count:04d}_{req_hash}.json"
        audit_file.write_text(json.dumps(audit_entry, ensure_ascii=False, indent=2), encoding="utf-8")

        return resp


def run_phase2_live_detection():
    print("=" * 80)
    print("FASE 2: TOPIC DETECTION LIVE INDIPENDENTE E CONSISTENZA FORENSE")
    print("=" * 80)

    # 1. Configura directory di esecuzione isolata per la detection
    det_dir = Path("output/live_detection_phase2")
    chk_dir = det_dir / "checkpoints"
    audit_dir = det_dir / "raw_completions_audit"
    chk_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    # 2. Ingestione documento autentico da 1.049 messaggi
    zip_path = Path("test_data/reference_chat_document.zip")
    if not zip_path.exists():
        raise FileNotFoundError(f"File {zip_path} non trovato!")

    print(f"[*] Ingestione chat di riferimento: {zip_path}...")
    with open(zip_path, "rb") as f:
        zip_bytes = f.read()

    req = IngestionRequest(
        source_format=SourceFormat.WHATSAPP_EXPORT,
        filename=zip_path.name,
        file_bytes=zip_bytes,
    )
    doc = list(ingest_file_payload(req).documents.values())[0]
    valid_eids = {s.evidence_id for s in doc.all_evidence_sections}
    print(f"[OK] Documento ingerito: {len(doc.bundles)} bundle, {len(valid_eids)} sezioni evidenza.")

    # 3. Identifica il miglior tema supportato emerso dalla Discovery (26/26 consolidato)
    disc_summary_path = Path("output/live_run_1049_26chunks/live_discovery_26of26_summary.json")
    disc_db = Path("output/live_run_1049_26chunks/checkpoints/checkpoints.db")
    positive_topic = "incontro"
    positive_description = "Organizzazione e coordinamento di incontri e appuntamenti tra i partecipanti."
    discovery_eids_for_topic: set[str] = set()

    if disc_summary_path.exists():
        disc_summary = json.loads(disc_summary_path.read_text(encoding="utf-8"))
        topics_list = disc_summary.get("topics") or []
        if topics_list:
            top_t = topics_list[0]
            positive_topic = top_t["label"]
            positive_description = top_t.get("short_description") or positive_description
            discovery_eids_for_topic = set(top_t.get("evidence_ids") or [])
            print(f"[OK] Tema positivo consolidato #1 dalla Discovery 26/26: '{positive_topic}' ({len(discovery_eids_for_topic)} evidenze scoperte)")
    elif disc_db.exists():
        import sqlite3
        conn = sqlite3.connect(disc_db)
        c = conn.cursor()
        rows = c.execute("SELECT serialized_result FROM checkpoint_chunks").fetchall()
        topic_counts: dict[str, set[str]] = {}
        for r in rows:
            data = json.loads(r[0])
            for t in data.get("topics", []):
                lbl = t.get("label", "").strip().lower()
                eids = set(t.get("evidence_ids", []))
                if lbl:
                    if lbl not in topic_counts:
                        topic_counts[lbl] = set()
                    topic_counts[lbl].update(eids)
        if topic_counts:
            sorted_topics = sorted(topic_counts.items(), key=lambda x: len(x[1]), reverse=True)
            positive_topic = sorted_topics[0][0]
            discovery_eids_for_topic = sorted_topics[0][1]
            print(f"[OK] Tema positivo selezionato dalla Discovery: '{positive_topic}' ({len(discovery_eids_for_topic)} evidenze scoperte)")
        conn.close()

    negative_topic = "mutuo bancario e acquisto immobile residenziale"
    negative_description = "Richiesta di mutuo ipotecario bancario, tassi di interesse e rogito notarile per acquisto di immobile residenziale."
    print(f"[*] Tema negativo (assente): '{negative_topic}'")

    pos_query = TopicQuery(
        topic_id="det_pos_incontro",
        label=positive_topic,
        description=positive_description,
    )
    neg_query = TopicQuery(
        topic_id="det_neg_mutuo",
        label=negative_topic,
        description=negative_description,
    )

    # 4. Setup client LM Studio
    raw_client = LmStudioClient(
        base_url="http://127.0.0.1:1234",
        model_id=DEFAULT_OPERATIONAL_MODEL,
        default_timeout_seconds=240.0,
    )
    if not raw_client.is_available():
        raise RuntimeError("LM Studio non e raggiungibile su http://127.0.0.1:1234!")

    auditing_client = AuditingLmStudioClient(raw_client, audit_dir=audit_dir)

    # 5. Inizializzazione servizio per Detection
    chk_store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)
    service = LocalTopicAnalysisService(
        base_url="http://127.0.0.1:1234",
        default_model=DEFAULT_OPERATIONAL_MODEL,
        timeout_seconds=240.0,
        checkpoint_store=chk_store,
        enable_checkpointing=True,
        profile=OperationalProfile.BALANCED,
    )

    # Selezione rappresentativa multi-chunk (inizio, centro, fine: chunk 0, 12, 25 -> 3/26 parent chunk)
    full_plan = service.create_analysis_plan(doc, analysis_type="discovery", max_topics=10)
    all_parent_chunks = list(full_plan.chunks)
    selected_indices = [0, 12, 25]
    selected_bundles = []
    for c_idx in selected_indices:
        selected_bundles.extend(all_parent_chunks[c_idx].bundles)

    subset_doc = ConversationEvidenceDocument(
        document_id=f"{doc.document_id}::detection_subset_3of26",
        bundles=tuple(selected_bundles),
        chat_id=doc.chat_id,
        source_name=doc.source_name,
    )
    subset_valid_eids = {s.evidence_id for s in subset_doc.all_evidence_sections}
    discovery_eids_in_subset = discovery_eids_for_topic & subset_valid_eids
    print(
        f"[*] Finestra rappresentativa multi-chunk: {len(selected_indices)}/{len(all_parent_chunks)} parent chunk "
        f"(indici {selected_indices}), {len(selected_bundles)}/{len(doc.bundles)} messaggi "
        f"({len(selected_bundles)/len(doc.bundles):.2%}). Evidenze Discovery in finestra: {len(discovery_eids_in_subset)}"
    )

    print("\n" + "=" * 80)
    print(f"ESECUZIONE DETECTION POSITIVA LIVE SUL TEMA: '{positive_topic}'...")
    print("=" * 80)
    t_pos_start = time.perf_counter()
    pos_result = service.run_topic_detection(
        document=subset_doc,
        topic=pos_query,
        client=auditing_client,
        model_name=DEFAULT_OPERATIONAL_MODEL,
    )
    pos_duration = round(time.perf_counter() - t_pos_start, 2)

    pos_decision = pos_result.decision
    pos_eids = set(pos_result.evidence_ids)
    print(f"[OK] Detection Positiva completata in {pos_duration} s.")
    print(f"     Decisione: {pos_decision.value if hasattr(pos_decision, 'value') else str(pos_decision)}")
    print(f"     Evidenze rilevate: {len(pos_eids)}")
    print(f"     Stato run: {pos_result.metadata.get('status')}")

    # Verifica forense: zero evidenze inventate
    hallucinated_pos = pos_eids - subset_valid_eids
    assert len(hallucinated_pos) == 0, f"Evidenze inventate in Detection positiva: {hallucinated_pos}"

    # Eseguiamo la Detection negativa
    print("\n" + "=" * 80)
    print(f"ESECUZIONE DETECTION NEGATIVA LIVE SUL TEMA: '{negative_topic}'...")
    print("=" * 80)
    t_neg_start = time.perf_counter()
    neg_result = service.run_topic_detection(
        document=subset_doc,
        topic=neg_query,
        client=auditing_client,
        model_name=DEFAULT_OPERATIONAL_MODEL,
    )
    neg_duration = round(time.perf_counter() - t_neg_start, 2)

    neg_decision = neg_result.decision
    neg_eids = set(neg_result.evidence_ids)
    print(f"[OK] Detection Negativa completata in {neg_duration} s.")
    print(f"     Decisione (sui 3/26 chunk analizzati): {neg_decision.value if hasattr(neg_decision, 'value') else str(neg_decision)}")
    print(f"     Evidenze rilevate: {len(neg_eids)} (atteso: 0)")
    print(f"     Stato run: {neg_result.metadata.get('status')}")

    # 6. Calcolo consistenza indipendente Discovery vs Detection (Prova del Nove)
    print("\n" + "=" * 80)
    print("CALCOLO CONSISTENZA INDIPENDENTE (DISCOVERY vs DETECTION)")
    print("=" * 80)

    disc_subset_topic = DiscoveredTopic(
        label=positive_topic,
        short_description=positive_description,
        evidence_ids=tuple(sorted(discovery_eids_in_subset)) if discovery_eids_in_subset else tuple(sorted(discovery_eids_for_topic)),
    )
    cons_eval = ConsistencyService.evaluate(
        document_id=subset_doc.document_id,
        discovered_topic=disc_subset_topic,
        detection_result=pos_result,
        discovery_run_id="run_disc_26of26_canonical",
        detection_run_id=str(pos_result.metadata.get("run_id") or "run_det_pos"),
    )

    overlap = discovery_eids_in_subset & pos_eids
    union = discovery_eids_in_subset | pos_eids
    jaccard_subset = round(len(overlap) / len(union), 4) if union else 0.0
    recall_subset = round(len(overlap) / len(discovery_eids_in_subset), 4) if discovery_eids_in_subset else 0.0
    precision_subset = round(len(overlap) / len(pos_eids), 4) if pos_eids else 0.0

    global_overlap = discovery_eids_for_topic & pos_eids
    global_union = discovery_eids_for_topic | pos_eids
    jaccard_global = round(len(global_overlap) / len(global_union), 4) if global_union else 0.0

    print(f"  - Stato ConsistencyService: {cons_eval.status.value}")
    print(f"  - Evidenze scoperte da Topic Discovery (finestra 3/26 chunk): {len(discovery_eids_in_subset)} (globali 26/26: {len(discovery_eids_for_topic)})")
    print(f"  - Evidenze confermate indipendentemente da Topic Detection: {len(pos_eids)}")
    print(f"  - Evidenze comuni (intersezione autentica): {len(overlap)}")
    print(f"  - Indice di similarita Jaccard sulla finestra 3/26: {jaccard_subset:.2%}")
    print(f"  - Copertura di riscontro (Overlap / Discovery in finestra): {recall_subset:.2%}")
    print(f"  - Precisione di riscontro (Overlap / Detection): {precision_subset:.2%}")

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coverage": {
            "scope": "REPRESENTATIVE_MULTI_CHUNK_SUBSET",
            "selected_parent_chunk_indices": selected_indices,
            "selected_parent_chunks": len(selected_indices),
            "total_parent_chunks": len(all_parent_chunks),
            "chunk_coverage_ratio": round(len(selected_indices) / len(all_parent_chunks), 4),
            "selected_messages": len(selected_bundles),
            "total_messages": len(doc.bundles),
            "message_coverage_ratio": round(len(selected_bundles) / len(doc.bundles), 4),
            "note": f"Il verdetto negativo ABSENT è rigorosamente riferito alla finestra testata ({len(selected_indices)}/{len(all_parent_chunks)} chunk, {len(selected_bundles)}/{len(doc.bundles)} messaggi) e non attribuito globalmente a dati non ispezionati.",
        },
        "positive_topic": positive_topic,
        "positive_decision": pos_decision.value if hasattr(pos_decision, "value") else str(pos_decision),
        "positive_status": pos_result.metadata.get("status"),
        "positive_completed_chunks": pos_result.metadata.get("completed_chunk_count"),
        "positive_total_chunks": pos_result.metadata.get("chunk_count"),
        "positive_evidence_count": len(pos_eids),
        "positive_duration_seconds": pos_duration,
        "negative_topic": negative_topic,
        "negative_decision": neg_decision.value if hasattr(neg_decision, "value") else str(neg_decision),
        "negative_decision_scope": "ABSENT_ON_TESTED_SUBSET_3_OF_26_CHUNKS",
        "negative_status": neg_result.metadata.get("status"),
        "negative_completed_chunks": neg_result.metadata.get("completed_chunk_count"),
        "negative_total_chunks": neg_result.metadata.get("chunk_count"),
        "negative_evidence_count": len(neg_eids),
        "negative_duration_seconds": neg_duration,
        "consistency": {
            "consistency_service_status": cons_eval.status.value,
            "discovery_global_eids_count": len(discovery_eids_for_topic),
            "discovery_subset_eids_count": len(discovery_eids_in_subset),
            "detection_eids_count": len(pos_eids),
            "common_eids_count": len(overlap),
            "subset_jaccard_similarity": jaccard_subset,
            "subset_recall": recall_subset,
            "subset_precision": precision_subset,
            "global_jaccard_similarity": jaccard_global,
        },
        "total_http_calls": auditing_client.calls_count,
        "total_prompt_tokens": auditing_client.total_prompt_tokens,
        "total_completion_tokens": auditing_client.total_completion_tokens,
    }

    summary_file = det_dir / "detection_phase2_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[OK] Riepilogo salvato in: {summary_file}")
    return summary


if __name__ == "__main__":
    run_phase2_live_detection()
