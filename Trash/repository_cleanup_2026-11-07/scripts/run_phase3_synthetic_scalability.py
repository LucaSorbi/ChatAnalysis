"""
scripts/run_phase3_synthetic_scalability.py
-------------------------------------------
FASE 3: Scalabilità sulla chat sintetica di grandi dimensioni (2.100 messaggi).

Caratteristiche:
1. Ingestione del dataset sintetico `test_data/synthetic_large/synthetic_chat_2100_messages.txt`
   (generato con seed 42, 8 temi, variazioni di lunghezza, emoji, accenti, media placeholder, injection avversarie).
2. Verifica dell'invariante di copertura e assenza di perdite/duplicazioni su tutti i chunk e microchunk.
3. PROVA LIVE PARZIALE: inferenza reale su LM Studio (Qwen2.5-7B) su chunk significativi (>= 3 parent chunk sintetici).
4. SIMULAZIONE COMPLETA: test end-to-end su tutti i 2.100 messaggi (tutti i ~52 chunk parent) con mock strutturato
   per verificare convergenza, aggregazione dei topic, memoria e persistenza SQLite su larga scala.
5. Registrazione separata ed esplicita dei risultati per il capitolo Risultati/Discussione della tesi.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ai.backend import BaseLlmClient, LlmCompletionResponse
from ai.chunking import split_chunk_into_microchunks, aggregate_topic_discovery
from ai.lmstudio import LmStudioClient, DEFAULT_OPERATIONAL_MODEL
from analysis.checkpoint import AnalysisCheckpointStore
from analysis.models import OperationalProfile
from analysis.service import LocalTopicAnalysisService
from ui.ingestion import ingest_file_payload
from ui.models import IngestionRequest, SourceFormat


class MockSyntheticLlmClient(BaseLlmClient):
    """Client di simulazione per testare l'intera chat da 2.100 messaggi (52 parent chunk) offline."""

    def __init__(self):
        self.calls_count = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def is_available(self) -> bool:
        return True

    def list_models(self) -> tuple[str, ...]:
        return ("qwen2.5-7b-instruct",)

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
        self.total_prompt_tokens += 600
        self.total_completion_tokens += 120

        # Risposta strutturata valida con alias deterministici compatibili
        simulated_response = json.dumps({
            "topics": [
                {
                    "label": "Organizzazione e pianificazione",
                    "short_description": "Discussione sintetica su attività, calendari e scadenze condivise.",
                    "evidence_ids": ["E001"],
                },
                {
                    "label": "Attività e logistica",
                    "short_description": "Scambio di dettagli pratici, orari e comunicazioni operative.",
                    "evidence_ids": ["E001"],
                }
            ]
        })
        return LlmCompletionResponse(
            content=simulated_response,
            model="qwen2.5-7b-instruct",
            prompt_tokens=600,
            completion_tokens=120,
            raw_response={"choices": [{"finish_reason": "stop"}]},
        )

    def complete(self, prompt_or_messages, **kwargs) -> LlmCompletionResponse:
        return self.chat_completion(messages=[{"role": "user", "content": str(prompt_or_messages)}], **kwargs)


def run_phase3_scalability():
    print("=" * 80)
    print("FASE 3: TEST DI SCALABILITÀ SU CHAT SINTETICA DA 2.100 MESSAGGI")
    print("=" * 80)

    synth_path = Path("test_data/synthetic_large/synthetic_chat_2100_messages.txt")
    if not synth_path.exists():
        raise FileNotFoundError(f"File sintetico {synth_path} non trovato!")

    out_dir = Path("output/synthetic_scalability_phase3")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Ingestione del corpus sintetico integrale
    print(f"[*] Ingestione dataset sintetico: {synth_path}...")
    txt_bytes = synth_path.read_bytes()
    req = IngestionRequest(
        source_format=SourceFormat.WHATSAPP_EXPORT,
        filename=synth_path.name,
        file_bytes=txt_bytes,
    )
    doc = list(ingest_file_payload(req).documents.values())[0]

    total_bundles = len(doc.bundles)
    total_sections = len(doc.all_evidence_sections)
    print(f"[OK] Dataset ingerito con successo: {total_bundles} messaggi, {total_sections} sezioni evidenza.")
    assert total_bundles >= 2000, f"Attesi >= 2000 messaggi, trovati {total_bundles}"

    # 2. Pianificazione canonica
    chk_dir = out_dir / "checkpoints"
    chk_dir.mkdir(parents=True, exist_ok=True)
    chk_store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)

    service = LocalTopicAnalysisService(
        base_url="http://127.0.0.1:1234",
        default_model=DEFAULT_OPERATIONAL_MODEL,
        timeout_seconds=240.0,
        checkpoint_store=chk_store,
        enable_checkpointing=True,
        profile=OperationalProfile.BALANCED,
    )

    plan = service.create_analysis_plan(doc, analysis_type="discovery", max_topics=10)
    total_planned_chunks = plan.planned_chunks_count
    chunks = plan.chunks
    print(f"[OK] Piano creato: {total_planned_chunks} parent chunk pianificati.")

    # 3. Verifica invarianti di partizionamento e microchunking su TUTTO il corpus
    print("\n[*] Verifica invarianti di copertura microchunking su tutti i 2.100 messaggi...")
    all_microchunks = []
    seen_section_ids = set()
    duplicate_section_ids = set()

    for p_idx, chunk in enumerate(chunks):
        subs = split_chunk_into_microchunks(chunk, max_substantive_messages=8)
        all_microchunks.extend(subs)
        for s in subs:
            for sec in s.all_evidence_sections:
                if sec.evidence_id in seen_section_ids:
                    duplicate_section_ids.add(sec.evidence_id)
                seen_section_ids.add(sec.evidence_id)

    total_micros = len(all_microchunks)
    doc_sections_set = {s.evidence_id for s in doc.all_evidence_sections}
    missing_sections = doc_sections_set - seen_section_ids

    print(f"[OK] Totale microchunk generati su 2.100 messaggi: {total_micros}")
    print(f"     Sezioni coperte: {len(seen_section_ids)}/{len(doc_sections_set)}")
    print(f"     Sezioni mancanti: {len(missing_sections)} (atteso: 0)")
    print(f"     Sezioni duplicate: {len(duplicate_section_ids)} (atteso: 0)")
    assert len(missing_sections) == 0, f"Perdita di sezioni rilevata: {len(missing_sections)}"
    assert len(duplicate_section_ids) == 0, f"Duplicazione di sezioni rilevata: {len(duplicate_section_ids)}"

    # 4. SIMULAZIONE COMPLETA SU TUTTI I 2.100 MESSAGGI (Mock end-to-end)
    print("\n" + "=" * 80)
    print(f"SIMULAZIONE COMPLETA END-TO-END SULL'INTERO CORPUS (TUTTI I {total_planned_chunks} CHUNK)...")
    print("=" * 80)
    mock_client = MockSyntheticLlmClient()
    t_sim_start = time.perf_counter()
    sim_result = service.run_topic_discovery(
        document=doc,
        client=mock_client,
        model_name=DEFAULT_OPERATIONAL_MODEL,
        max_topics=10,
    )
    sim_duration = round(time.perf_counter() - t_sim_start, 2)
    sim_topics = sim_result.topics
    sim_meta = sim_result.metadata

    print(f"[OK] Simulazione completa completata in {sim_duration} s.")
    print(f"     Stato: {sim_meta.get('status')}")
    print(f"     Chunk completati: {sim_meta.get('completed_chunk_count')}/{total_planned_chunks}")
    print(f"     Chiamate mock simulate: {mock_client.calls_count}")
    print(f"     Topic finali consolidati: {len(sim_topics)} (max richiesto: 10)")
    for idx, t in enumerate(sim_topics, 1):
        print(f"       {idx}. [{len(t.evidence_ids)} evidenze] {t.label}")

    assert sim_meta.get("status") == "COMPLETE"
    assert sim_meta.get("completed_chunk_count") == total_planned_chunks

    # 5. PROVA LIVE PARZIALE SU LM STUDIO (Qwen2.5-7B reale) su sottoinsieme significativo
    # Elaboriamo i primi 3 chunk reali (circa 150 messaggi)
    live_chunks_count = 3
    print("\n" + "=" * 80)
    print(f"PROVA LIVE PARZIALE SU LM STUDIO: primi {live_chunks_count} parent chunk sintetici ({len(chunks[0].bundles) + len(chunks[1].bundles) + len(chunks[2].bundles)} messaggi)...")
    print("=" * 80)

    raw_client = LmStudioClient(
        base_url="http://127.0.0.1:1234",
        model_id=DEFAULT_OPERATIONAL_MODEL,
        default_timeout_seconds=240.0,
    )

    live_summary = {}
    if raw_client.is_available():
        live_sub_doc_bundles = []
        for ch in chunks[:live_chunks_count]:
            live_sub_doc_bundles.extend(ch.bundles)

        from ai.models import ConversationEvidenceDocument
        sub_synth_doc = ConversationEvidenceDocument(
            document_id=f"{doc.document_id}::sub_live_sample",
            bundles=tuple(live_sub_doc_bundles),
            chat_id=doc.chat_id,
            source_name=doc.source_name,
        )

        chk_dir_live = out_dir / "checkpoints_live_sample"
        chk_store_live = AnalysisCheckpointStore(checkpoint_dir=chk_dir_live)
        service_live = LocalTopicAnalysisService(
            base_url="http://127.0.0.1:1234",
            default_model=DEFAULT_OPERATIONAL_MODEL,
            timeout_seconds=240.0,
            checkpoint_store=chk_store_live,
            enable_checkpointing=True,
            profile=OperationalProfile.BALANCED,
        )

        t_live_start = time.perf_counter()
        live_res = service_live.run_topic_discovery(
            document=sub_synth_doc,
            client=raw_client,
            model_name=DEFAULT_OPERATIONAL_MODEL,
            max_topics=10,
        )
        live_duration = round(time.perf_counter() - t_live_start, 2)
        live_meta = live_res.metadata
        print(f"[OK] Prova LIVE parziale completata in {live_duration} s!")
        print(f"     Stato: {live_meta.get('status')}")
        print(f"     Chunk completati: {live_meta.get('completed_chunk_count')}")
        print(f"     Topic estratti LIVE su chat sintetica: {len(live_res.topics)}")
        for idx, t in enumerate(live_res.topics, 1):
            print(f"       {idx}. [{len(t.evidence_ids)} evidenze] {t.label}")

        live_summary = {
            "tested_live": True,
            "sample_chunks": live_chunks_count,
            "sample_messages": len(live_sub_doc_bundles),
            "status": live_meta.get("status"),
            "duration_seconds": live_duration,
            "topics_count": len(live_res.topics),
            "topics": [
                {
                    "label": t.label,
                    "short_description": t.short_description,
                    "evidence_count": len(t.evidence_ids),
                    "evidence_ids": list(t.evidence_ids),
                }
                for t in live_res.topics
            ],
        }
    else:
        print("[AVVISO] LM Studio non disponibile per la prova live sintetica, registrata solo simulazione completa.")
        live_summary = {"tested_live": False, "reason": "LM Studio non raggiungibile"}

    scalability_report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "path": str(synth_path),
            "seed": 42,
            "total_messages": total_bundles,
            "total_sections": total_sections,
            "planned_parent_chunks": total_planned_chunks,
            "total_microchunks": total_micros,
            "coverage_invariants": {
                "missing_sections": len(missing_sections),
                "duplicate_sections": len(duplicate_section_ids),
                "verified": True,
            },
        },
        "full_simulation_mock": {
            "status": sim_meta.get("status"),
            "completed_chunks": sim_meta.get("completed_chunk_count"),
            "total_chunks": total_planned_chunks,
            "duration_seconds": sim_duration,
            "topics_count": len(sim_topics),
            "calls_count": mock_client.calls_count,
        },
        "partial_live_proof": live_summary,
    }

    report_file = out_dir / "scalability_summary.json"
    report_file.write_text(json.dumps(scalability_report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[OK] Riepilogo scalabilita salvato in: {report_file}")
    return scalability_report


if __name__ == "__main__":
    run_phase3_scalability()
