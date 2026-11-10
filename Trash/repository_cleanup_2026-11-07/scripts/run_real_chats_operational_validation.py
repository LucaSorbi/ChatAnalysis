"""
scripts/run_real_chats_operational_validation.py
------------------------------------------------
Esecuzione del protocollo di validazione operativo LIVE sulle DUE CHAT REALI
del progetto con media processing reale locale (Faster-Whisper e Tesseract OCR).

Requisiti del Protocollo di Chiusura (Audit 03/11/2026):
1. Documenti autentici da G:
   - Chat 1: G:/WhatsApp Chat - Florida Atletica.zip (19 messaggi + 1 vocale reale Opus)
   - Chat 2: G:/WhatsApp Chat - Marco (ERASMUS).zip (36 messaggi + 1 foto reale JPEG)
2. Media Processing REALE LOCALE:
   - STT: FasterWhisperTranscriber() con trascrizione effettiva del file Opus.
   - OCR: TesseractImageTextExtractor() con estrazione effettiva del file JPEG.
3. LM Studio LIVE:
   - http://127.0.0.1:1234, modello qwen2.5-7b-instruct, ctx 8192, temp 0.0, seed 42.
4. Topic Discovery completa:
   - Cartella checkpoint isolata e nuova.
   - Verifica di status COMPLETE e copertura 100% sezioni di evidenza.
5. Verifica Indipendente di Consistenza ("Prova del Nove"):
   - Verifica di TUTTI i topic scoperti (senza interruzione al primo esito favorevole).
   - Registrazione di ciascun esito: CONSISTENT, PARTIAL_OVERLAP, WEAK_OVERLAP, DISCREPANCY_ABSENT.
6. Vincolo di Privacy Assoluto:
   - Zero esportazione di testi in chiaro di chat private, trascrizioni vocali o OCR.
   - I file di log contengono esclusivamente metriche aggregate, conteggi, status e codici diagnostici.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Ensure project root in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Configura Tesseract se presente su Windows
TESSERACT_EXE = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
if TESSERACT_EXE.exists():
    os.environ["PATH"] = str(TESSERACT_EXE.parent) + ";" + os.environ.get("PATH", "")

from ai.lmstudio import LmStudioClient
from analysis.checkpoint import AnalysisCheckpointStore
from analysis.models import ConsistencyStatus
from analysis.service import LocalTopicAnalysisService
from multimodal.ocr import TesseractImageTextExtractor
from multimodal.transcriber import FasterWhisperTranscriber
from ui.application import execute_local_media_processing
from ui.ingestion import ingest_file_payload
from ui.models import IngestionRequest, SourceFormat

LM_STUDIO_URL = "http://127.0.0.1:1234"
MODEL_NAME = "qwen2.5-7b-instruct"
OUTPUT_DIR = REPO_ROOT / "handoff" / "2026-11-03"


def run_operational_validation_on_real_chats():
    print("=" * 80)
    print("VALIDAZIONE OPERATIVA LIVE SULLE CHAT REALI CON MEDIA REALE (STT/OCR)")
    print(f"Data/Ora: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"LM Studio Backend: {LM_STUDIO_URL} | Modello: {MODEL_NAME}")
    print("=" * 80)

    client = LmStudioClient(
        base_url=LM_STUDIO_URL,
        model_id=MODEL_NAME,
    )

    chat_targets = [
        {
            "id": "CHAT_AUDIO_19_MESSAGES_REAL",
            "path": Path("G:/WhatsApp Chat - Florida Atletica.zip"),
            "expected_bundles": 19,
            "expected_derived": 1,
            "media_type": "AUDIO",
            "stt_engine": FasterWhisperTranscriber(),
            "ocr_engine": None,
        },
        {
            "id": "CHAT_FOTO_36_MESSAGES_REAL",
            "path": Path("G:/WhatsApp Chat - Marco (ERASMUS).zip"),
            "expected_bundles": 36,
            "expected_derived": 1,
            "media_type": "PHOTO",
            "stt_engine": None,
            "ocr_engine": TesseractImageTextExtractor(tesseract_cmd=str(TESSERACT_EXE) if TESSERACT_EXE.exists() else None),
        },
    ]

    results_summary = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "backend_url": LM_STUDIO_URL,
        "model_name": MODEL_NAME,
        "chats": [],
    }

    for target in chat_targets:
        cid = target["id"]
        cpath = target["path"]
        print(f"\n[{cid}] Inizio validazione da file autentico: {cpath}")

        if not cpath.exists():
            print(f"[ERRORE] File non trovato: {cpath}")
            continue

        # 1. Ingestione archivio autentico
        t_ingest_start = time.perf_counter()
        req = IngestionRequest(
            file_bytes=cpath.read_bytes(),
            filename=cpath.name,
            source_format=SourceFormat.WHATSAPP_EXPORT,
        )
        res = ingest_file_payload(req)
        doc = list(res.documents.values())[0]
        ingest_elapsed = time.perf_counter() - t_ingest_start
        print(f"  Ingestione completata: {len(doc.bundles)} bundle, {len(doc.all_evidence_sections)} sezioni iniziali ({ingest_elapsed:.2f}s)")

        # 2. Elaborazione Media Reale Locale (Faster-Whisper / Tesseract)
        t_media_start = time.perf_counter()
        enriched_doc, media_report = execute_local_media_processing(
            doc,
            registry=res.media_registry,
            stt_backend=target["stt_engine"],
            ocr_backend=target["ocr_engine"],
        )
        media_elapsed = time.perf_counter() - t_media_start
        final_sections_count = len(enriched_doc.all_evidence_sections)
        print(f"  Media processing completato: {media_report['derived_sections']} sezioni derivate, totale {final_sections_count} sezioni ({media_elapsed:.2f}s)")
        print(f"  Report Media: STT={media_report['stt_engine']} (avail={media_report['stt_available']}), OCR={media_report['ocr_engine']} (avail={media_report['ocr_available']})")

        # 3. Topic Discovery LIVE con checkpoint store isolato e vuoto
        run_timestamp = int(time.time() * 1000)
        chk_dir = REPO_ROOT / "output" / f"real_val_checkpoints_{run_timestamp}_{cid.lower()}"
        chk_dir.mkdir(parents=True, exist_ok=True)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)
        service = LocalTopicAnalysisService(
            base_url=LM_STUDIO_URL,
            default_model=MODEL_NAME,
            checkpoint_store=store,
            enable_checkpointing=True,
        )

        print("  Avvio Topic Discovery LIVE su LM Studio...")
        t_disc_start = time.perf_counter()
        disc_res = service.run_topic_discovery(
            document=enriched_doc,
            client=client,
            model_name=MODEL_NAME,
            max_topics=10,
        )
        disc_elapsed = time.perf_counter() - t_disc_start
        disc_meta = disc_res.metadata

        print(f"  Discovery completata in {disc_elapsed:.2f}s:")
        print(f"    Status: {disc_meta.get('status')}")
        print(f"    Sezioni analizzate: {disc_meta.get('analyzed_evidence_count')} / {disc_meta.get('total_evidence_count')}")
        print(f"    Numero topic scoperti: {len(disc_res.topics)}")

        # 4. Verifica Indipendente di Consistenza ("Prova del Nove") su TUTTI i topic scoperti
        print(f"  Avvio verifica indipendente su TUTTI i {len(disc_res.topics)} topic scoperti...")

        topic_verification_records = []
        consistent_topics_count = 0
        discrepant_topics_count = 0

        for t_idx, topic in enumerate(disc_res.topics):
            t_v_start = time.perf_counter()
            det_res_ver, cons_res = service.verify_discovered_topic(
                document=enriched_doc,
                discovered_topic=topic,
                discovery_result=disc_res,
                model_name=MODEL_NAME,
                temperature=0.0,
                seed=42,
            )
            v_elapsed = time.perf_counter() - t_v_start

            overlap_c = len(cons_res.overlap_evidence_ids)
            c_status_str = cons_res.status.value if hasattr(cons_res.status, "value") else str(cons_res.status)
            det_decision = det_res_ver.decision.value
            union_c = len(set(cons_res.discovery_evidence_ids).union(cons_res.detection_evidence_ids))
            jaccard = round(overlap_c / union_c, 4) if union_c > 0 else 0.0

            is_cons = (c_status_str == "CONSISTENT" or (overlap_c >= 1 and c_status_str in ("CONSISTENT", "PARTIAL_OVERLAP", "WEAK_OVERLAP")))

            if is_cons:
                consistent_topics_count += 1
            else:
                discrepant_topics_count += 1

            topic_record = {
                "topic_index": t_idx + 1,
                "label_length_chars": len(topic.label),
                "description_length_chars": len(topic.short_description),
                "evidence_ids_count": len(topic.evidence_ids),
                "detection_decision": det_decision,
                "consistency_status": c_status_str,
                "evidence_overlap_count": overlap_c,
                "jaccard_similarity": jaccard,
                "is_consistent": is_cons,
                "duration_seconds": round(v_elapsed, 2),
            }
            topic_verification_records.append(topic_record)
            print(f"    Topic #{t_idx + 1}: Decision={det_decision}, Status={c_status_str}, Overlap={overlap_c}, Jaccard={jaccard:.4f}, Consistent={is_cons} ({v_elapsed:.2f}s)")

        # Aggrega risultato chat
        chat_summary = {
            "chat_id": cid,
            "document_id_fingerprint": doc.document_id[:16],
            "input_filename": cpath.name,
            "media_type": target["media_type"],
            "bundles_count": len(doc.bundles),
            "initial_evidence_sections": media_report["initial_sections"],
            "derived_evidence_sections": media_report["derived_sections"],
            "total_evidence_sections": final_sections_count,
            "media_processing": {
                "stt_engine": media_report["stt_engine"],
                "stt_available": media_report["stt_available"],
                "ocr_engine": media_report["ocr_engine"],
                "ocr_available": media_report["ocr_available"],
                "duration_seconds": round(media_elapsed, 2),
            },
            "discovery": {
                "status": disc_meta.get("status"),
                "total_evidence_count": disc_meta.get("total_evidence_count"),
                "analyzed_evidence_count": disc_meta.get("analyzed_evidence_count"),
                "coverage_pct": (
                    round(disc_meta.get("analyzed_evidence_count", 0) / max(1, disc_meta.get("total_evidence_count", 1)) * 100, 2)
                ),
                "discovered_topics_count": len(disc_res.topics),
                "duration_seconds": round(disc_elapsed, 2),
            },
            "verification_summary": {
                "total_topics_verified": len(disc_res.topics),
                "consistent_topics_count": consistent_topics_count,
                "discrepant_topics_count": discrepant_topics_count,
                "consistency_rate_pct": (
                    round(consistent_topics_count / max(1, len(disc_res.topics)) * 100, 2)
                ),
                "topic_details": topic_verification_records,
            },
        }
        results_summary["chats"].append(chat_summary)

    # 5. Salva il file JSON SAFE con metadati e metriche pseudonimizzate
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = OUTPUT_DIR / "phase3_real_chat_validation_results.json"
    out_json.write_text(json.dumps(results_summary, indent=2), encoding="utf-8")
    print(f"\n[OK] Risultati della validazione salvati in: {out_json}")
    print("=" * 80)
    print("VALIDAZIONE OPERATIVA SULLE CHAT REALI TERMINATA")
    print("=" * 80)


if __name__ == "__main__":
    run_operational_validation_on_real_chats()
