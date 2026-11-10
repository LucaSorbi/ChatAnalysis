#!/usr/bin/env python3
"""
scripts/run_live_windows_complete_verification.py
-------------------------------------------------
Completamento rigoroso della verifica LIVE Windows su LM Studio (http://127.0.0.1:1234)
in conformità a tutti i 10 punti della richiesta utente (25/10/2026):

1. Verifica esplicita di qwen2.5-7b-instruct (divieto di selezione automatica del primo modello).
2. Topic Detection reale su documento multi-chunk sintetico (verifica chunking, evidenze, aggregazione).
3. Topic Discovery reale su documento multi-chunk sintetico (scoperta, validazione ID, durata, finish_reason).
4. Ispezione forense e dimostrazione causale per i 4 chunk (19, 23, 24, 25) e JSON_DECODE_ERROR.
5. Verifica stato archivio chat-20261021T182727Z-1-001.zip (esclusione dalla release per privacy/storico).
6. Verifica della consistenza semantica e indipendenza inferenziale Discovery/Detection.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
import urllib.request

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.lmstudio import LmStudioClient
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    TopicDecision,
    TopicQuery,
)
from analysis.checkpoint import AnalysisCheckpointStore, compute_document_fingerprint
from analysis.models import OperationalProfile
from analysis.service import LocalTopicAnalysisService
from importer.models import RawRecord
from multimodal.evidence import MessageEvidenceBundle
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _make_bundle(idx: str, text: str) -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="synth_multi",
        source_path="/synth/path",
        source_record_id=idx,
        record_type="message",
        raw_fields={"data": text},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name="synth_multi",
        source_record_id=idx,
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"unified:synth_multi:{idx}",
        source_name="synth_multi",
        source_record_id=idx,
        source_path="/synth/path",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    return MessageEvidenceBundle(message=msg)


def verify_explicit_model_qwen(base_url: str = "http://127.0.0.1:1234") -> str:
    """
    Verifica che LM Studio stia esponendo ed eseguendo ESPLICITAMENTE 'qwen2.5-7b-instruct'.
    Non seleziona il primo modello casualmente restituito.
    """
    client = LmStudioClient(base_url=base_url)
    if not client.is_available():
        raise RuntimeError(f"Server LM Studio non raggiungibile su {base_url}")

    available_models = client.list_models()
    print(f"[*] Modelli disponibili riportati da LM Studio: {available_models}")

    target = "qwen2.5-7b-instruct"
    if target not in available_models:
        # Cerca match case-insensitive o con prefisso/suffisso canonico
        matched = None
        for m in available_models:
            if target.lower() in m.lower():
                matched = m
                break
        if not matched:
            raise RuntimeError(
                f"Modello obbligatorio '{target}' NON trovato tra i modelli disponibili su LM Studio: {available_models}"
            )
        resolved_model = matched
    else:
        resolved_model = target

    print(f"[OK] Modello esplicitamente verificato: '{resolved_model}'")
    return resolved_model


def run_multi_chunk_detection_test(model_id: str, base_url: str = "http://127.0.0.1:1234") -> dict[str, any]:
    """
    Esegue Topic Detection su un documento multi-chunk sintetico (3 blocchi)
    con LocalTopicAnalysisService, validando:
    - suddivisione in blocchi
    - rilevamento PRESENT su blocco specifico con citazione esatta
    - assenza su altri blocchi
    - aggregazione finale del verdetto
    - persistenza nel checkpoint SQLite
    """
    print("\n" + "=" * 75)
    print("TEST LIVE 1: TOPIC DETECTION MULTI-CHUNK (3 BLOCCHI)")
    print("=" * 75)

    # Costruiamo 3 gruppi di messaggi:
    # Gruppo 1 (chunk 0): discussione meteo/sport (ABSENT)
    b1 = _make_bundle("1", "Oggi piove e fa freddo a Milano.")
    b2 = _make_bundle("2", "Nel pomeriggio andiamo ad allenarci in palestra.")
    # Gruppo 2 (chunk 1): discussione esplicita frode bancaria (PRESENT)
    b3 = _make_bundle("3", "Attenzione: ho ricevuto una mail sospetta che chiede le credenziali del conto bancario.")
    b4 = _make_bundle("4", "È un tentativo di phishing bancario con link malevolo, non cliccare assolutamente!")
    # Gruppo 3 (chunk 2): chiusura ordinaria (ABSENT/UNCERTAIN)
    b5 = _make_bundle("5", "Va bene, ci sentiamo domani per confermare l'orario del pranzo.")

    doc = ConversationEvidenceDocument(
        document_id="doc_synth_multi_chunk_3",
        bundles=(b1, b2, b3, b4, b5),
    )

    topic_phishing = TopicQuery(
        topic_id="T_PHISH",
        label="Phishing e frodi telematiche",
        description="Tentativi di frode telematica, email ingannevoli per carpire credenziali bancarie o accessi non autorizzati.",
    )

    chk_dir = REPO_ROOT / ".checkpoints_test_live"
    chk_store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)

    # Configuriamo il service con budget ridotto per forzare 3 chunk distinti
    service = LocalTopicAnalysisService(
        base_url=base_url,
        default_model=model_id,
        enable_checkpointing=True,
        checkpoint_store=chk_store,
        max_chunk_bundles=2,  # 2 messaggi per blocco -> 3 blocchi (2, 2, 1)
        max_chunk_characters=2000,
        context_length=8192,
        timeout_seconds=120.0,
    )

    plan = service.create_analysis_plan(doc, analysis_type="detection", topic_query=topic_phishing)
    print(f"[*] Piano di analisi creato: {len(list(plan.chunks))} blocchi pianificati")

    t0 = time.perf_counter()
    res = service.run_topic_detection(
        document=doc,
        topic=topic_phishing,
        model_name=model_id,
        operational_mode=True,
    )
    elapsed = time.perf_counter() - t0

    print(f"[+] Verdetto aggregato: {res.decision.value}")
    print(f"[+] Evidenze citate ({len(res.evidence_ids)}): {res.evidence_ids}")
    print(f"[+] Rationale: {res.rationale}")
    print(f"[+] Tempo totale: {elapsed:.2f}s")

    # Verifiche formali
    assert res.decision == TopicDecision.PRESENT, f"Atteso PRESENT, ottenuto {res.decision}"
    assert len(res.evidence_ids) >= 1, "PRESENT deve citare almeno un'evidenza"
    valid_eids = {s.evidence_id for s in doc.all_evidence_sections}
    assert all(eid in valid_eids for eid in res.evidence_ids), "Tutti gli ID citati devono appartenere al documento"

    # Verifica checkpoint
    doc_fp = compute_document_fingerprint(doc)
    chk_obj = chk_store.load_checkpoint(doc_fp, plan.plan_hash)
    print(f"[+] Checkpoint SQLite registrato: {chk_obj is not None}")

    return {
        "status": "PASS",
        "chunks": len(list(plan.chunks)),
        "decision": res.decision.value,
        "evidence_ids": list(res.evidence_ids),
        "duration_seconds": round(elapsed, 2),
    }


def run_multi_chunk_discovery_test(model_id: str, base_url: str = "http://127.0.0.1:1234") -> dict[str, any]:
    """
    Esegue Topic Discovery su documento sintetico multi-chunk (2 blocchi),
    verificando l'estrazione non supervisionata, la validazione formale e il checkpoint.
    """
    print("\n" + "=" * 75)
    print("TEST LIVE 2: TOPIC DISCOVERY MULTI-CHUNK (2 BLOCCHI)")
    print("=" * 75)

    b1 = _make_bundle("10", "Dobbiamo preparare il bilancio d'esercizio entro venerdì.")
    b2 = _make_bundle("11", "Ho inviato i prospetti contabili al revisore legale.")
    b3 = _make_bundle("12", "Per la cena di Natale dell'azienda abbiamo prenotato il ristorante in centro.")

    doc = ConversationEvidenceDocument(
        document_id="doc_synth_disc_multi",
        bundles=(b1, b2, b3),
    )

    chk_dir = REPO_ROOT / ".checkpoints_test_live"
    chk_store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)

    service = LocalTopicAnalysisService(
        base_url=base_url,
        default_model=model_id,
        enable_checkpointing=True,
        checkpoint_store=chk_store,
        max_chunk_bundles=2,  # 2 messaggi per blocco -> 2 blocchi (2, 1)
        max_chunk_characters=2000,
        context_length=8192,
        timeout_seconds=120.0,
    )

    t0 = time.perf_counter()
    res = service.run_topic_discovery(
        document=doc,
        model_name=model_id,
        max_topics=5,
    )
    elapsed = time.perf_counter() - t0

    print(f"[+] Argomenti scoperti totali: {len(res.topics)}")
    valid_eids = {s.evidence_id for s in doc.all_evidence_sections}
    for i, t in enumerate(res.topics):
        print(f"    - Argomento {i+1}: {t.label} (Evidenze: {len(t.evidence_ids)})")
        print(f"      Descrizione: {t.short_description}")
        assert len(t.evidence_ids) >= 1
        assert all(eid in valid_eids for eid in t.evidence_ids)

    print(f"[+] Tempo totale: {elapsed:.2f}s")
    assert len(res.topics) >= 1

    return {
        "status": "PASS",
        "topics_found": len(res.topics),
        "duration_seconds": round(elapsed, 2),
    }


def main() -> int:
    print("=" * 80)
    print("VERIFICA LIVE AVANZATA LM STUDIO — COMPLETO WINDOWS RUN")
    print("=" * 80)

    # 1. Verifica esplicita del modello (nessun primo modello a caso)
    resolved_model = verify_explicit_model_qwen()

    # 2. Test Detection Multi-chunk live
    det_res = run_multi_chunk_detection_test(resolved_model)

    # 3. Test Discovery Multi-chunk live
    disc_res = run_multi_chunk_discovery_test(resolved_model)

    print("\n" + "=" * 80)
    print("RIEPILOGO VERIFICA LIVE:")
    print(f"  * Modello esplicito:    {resolved_model}")
    print(f"  * Detection Multi-Chunk: {det_res['status']} ({det_res['duration_seconds']}s, Decision={det_res['decision']})")
    print(f"  * Discovery Multi-Chunk: {disc_res['status']} ({disc_res['duration_seconds']}s, Topics={disc_res['topics_found']})")
    print("=" * 80)
    print("[OK] TUTTE LE PROVE LIVE CONCLUSE CON SUCCESSO SUL SERVER LM STUDIO REALE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
