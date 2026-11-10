#!/usr/bin/env python3
"""
scripts/run_live_lmstudio_smoke_and_validation.py
Esecuzione LIVE delle prove di accettazione Topic Detection e Topic Discovery
contro il server reale LM Studio su http://127.0.0.1:1234 con il modello caricato.

PRIVACY: Utilizza ESCLUSIVAMENTE evidenze sintetiche generate programmaticamente.
Nessun messaggio reale né PII viene letto o trasmesso.
"""

import sys
import os
import json
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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

from ai.lmstudio import LmStudioClient
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    TopicDecision,
    TopicQuery,
)
from ai.topics import TopicDetectionAnalyzer, TopicDiscoveryAnalyzer


def make_bundle(idx: str, text: str) -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="synth_db",
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
        source_name="synth_db",
        source_record_id=idx,
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"unified:synth_db:{idx}",
        source_name="synth_db",
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


def run_live_tests():
    print("=" * 70)
    print("PROVE LIVE ACCETTAZIONE LM STUDIO — WINDOWS LOCALHOST:1234")
    print("=" * 70)

    base_url = "http://127.0.0.1:1234"
    client = LmStudioClient(base_url=base_url, default_timeout_seconds=120.0)

    # 1. Verifica disponibilità
    if not client.is_available():
        print("[-] ERRORE: LM Studio server non raggiungibile su 127.0.0.1:1234")
        return False

    model_ids = client.list_models()
    print(f"[+] Server attivo. Modelli disponibili: {model_ids}")

    if not model_ids:
        print("[-] ERRORE: Nessun modello disponibile")
        return False

    active_model = model_ids[0]
    print(f"[+] Modello selezionato per il test live: {active_model}")

    analyzer_detection = TopicDetectionAnalyzer(client=client, default_model_id=active_model, operational_mode=True)
    analyzer_discovery = TopicDiscoveryAnalyzer(client=client, default_model_id=active_model, operational_mode=True)

    results = []

    # -------------------------------------------------------------
    # TEST LIVE 1: Topic Detection — PRESENT (Argomento: Compravendita auto)
    # -------------------------------------------------------------
    print("\n--- TEST LIVE 1: Detection PRESENT (Compravendita veicoli) ---")
    b1 = make_bundle("1", "Mario: Ciao, vorrei vendere la mia auto usata per cinquemila euro.")
    b2 = make_bundle("2", "Luigi: Perfetto, accetto l'offerta per l'auto e passo a ritirarla.")
    doc_present = ConversationEvidenceDocument(document_id="doc_synth_present", bundles=(b1, b2))

    topic_auto = TopicQuery(
        topic_id="T01",
        label="Compravendita veicoli",
        description="Discussione relativa alla vendita o acquisto di automobili e veicoli.",
    )

    t0 = time.time()
    try:
        det_result_1 = analyzer_detection.detect_topic(
            document=doc_present,
            topic=topic_auto,
            strategy=AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
            timeout_seconds=90.0,
        )
        elapsed_1 = time.time() - t0
        print(f"[+] Decisione: {det_result_1.decision.value}")
        print(f"[+] Evidence IDs: {det_result_1.evidence_ids}")
        print(f"[+] Rationale: {det_result_1.rationale}")
        print(f"[+] Metadata: prompt_ver={det_result_1.metadata.get('prompt_version')}, retries={det_result_1.metadata.get('retries')}")
        print(f"[+] Tempo trascorso: {elapsed_1:.2f}s")
        assert det_result_1.decision == TopicDecision.PRESENT
        assert len(det_result_1.evidence_ids) > 0
        valid_eids = {s.evidence_id for s in doc_present.all_evidence_sections}
        assert all(eid in valid_eids for eid in det_result_1.evidence_ids)
        results.append({
            "test": "Detection_PRESENT",
            "status": "PASS",
            "decision": det_result_1.decision.value,
            "eids_count": len(det_result_1.evidence_ids),
            "elapsed": elapsed_1,
        })
    except Exception as e:
        print(f"[-] FAIL Test 1: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        results.append({"test": "Detection_PRESENT", "status": "FAIL", "error": str(e)})

    # -------------------------------------------------------------
    # TEST LIVE 2: Topic Detection — ABSENT (Argomento: Sostanze stupefacenti)
    # -------------------------------------------------------------
    print("\n--- TEST LIVE 2: Detection ABSENT (Sostanze stupefacenti) ---")
    topic_drugs = TopicQuery(
        topic_id="T02",
        label="Traffico stupefacenti",
        description="Discussione inerente acquisto, spaccio o consumo di sostanze stupefacenti illecite.",
    )

    t0 = time.time()
    try:
        det_result_2 = analyzer_detection.detect_topic(
            document=doc_present,
            topic=topic_drugs,
            strategy=AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
            timeout_seconds=90.0,
        )
        elapsed_2 = time.time() - t0
        print(f"[+] Decisione: {det_result_2.decision.value}")
        print(f"[+] Evidence IDs: {det_result_2.evidence_ids}")
        print(f"[+] Rationale: {det_result_2.rationale}")
        print(f"[+] Metadata: prompt_ver={det_result_2.metadata.get('prompt_version')}, retries={det_result_2.metadata.get('retries')}")
        print(f"[+] Tempo trascorso: {elapsed_2:.2f}s")
        assert det_result_2.decision == TopicDecision.ABSENT
        assert len(det_result_2.evidence_ids) == 0
        results.append({
            "test": "Detection_ABSENT",
            "status": "PASS",
            "decision": det_result_2.decision.value,
            "eids_count": len(det_result_2.evidence_ids),
            "elapsed": elapsed_2,
        })
    except Exception as e:
        print(f"[-] FAIL Test 2: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        results.append({"test": "Detection_ABSENT", "status": "FAIL", "error": str(e)})

    # -------------------------------------------------------------
    # TEST LIVE 3: Topic Detection — UNCERTAIN o ABSENT su testo ambiguo/parziale
    # -------------------------------------------------------------
    print("\n--- TEST LIVE 3: Detection UNCERTAIN/ABSENT su testo ambiguo ---")
    b_amb = make_bundle("10", "Forse domani ci vediamo al solito posto per quella cosa.")
    doc_amb = ConversationEvidenceDocument(document_id="doc_synth_amb", bundles=(b_amb,))
    topic_meeting = TopicQuery(
        topic_id="T03",
        label="Assemblea societaria straordinaria",
        description="Convocazione e svolgimento formale di assemblea straordinaria dei soci con notaio.",
    )

    t0 = time.time()
    try:
        det_result_3 = analyzer_detection.detect_topic(
            document=doc_amb,
            topic=topic_meeting,
            strategy=AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
            timeout_seconds=90.0,
        )
        elapsed_3 = time.time() - t0
        print(f"[+] Decisione: {det_result_3.decision.value}")
        print(f"[+] Evidence IDs: {det_result_3.evidence_ids}")
        print(f"[+] Rationale: {det_result_3.rationale}")
        print(f"[+] Tempo trascorso: {elapsed_3:.2f}s")
        assert det_result_3.decision in (TopicDecision.UNCERTAIN, TopicDecision.ABSENT)
        if det_result_3.decision == TopicDecision.ABSENT:
            assert len(det_result_3.evidence_ids) == 0
        else:
            valid_eids = {s.evidence_id for s in doc_amb.all_evidence_sections}
            assert all(eid in valid_eids for eid in det_result_3.evidence_ids)
        results.append({
            "test": "Detection_UNCERTAIN_OR_ABSENT",
            "status": "PASS",
            "decision": det_result_3.decision.value,
            "eids_count": len(det_result_3.evidence_ids),
            "elapsed": elapsed_3,
        })
    except Exception as e:
        print(f"[-] FAIL Test 3: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        results.append({"test": "Detection_UNCERTAIN_OR_ABSENT", "status": "FAIL", "error": str(e)})

    # -------------------------------------------------------------
    # TEST LIVE 4: Topic Discovery — Conversazione sintetica a 3 messaggi
    # -------------------------------------------------------------
    print("\n--- TEST LIVE 4: Topic Discovery su conversazione sintetica ---")
    b_d1 = make_bundle("101", "Alice: Hai inviato le fatture elettroniche al commercialista?")
    b_d2 = make_bundle("102", "Bob: Sì, tutte le fatture e i bilanci trimestrali sono pronti.")
    b_d3 = make_bundle("103", "Alice: Ottimo. Domenica mattina facciamo la partita a tennis al circolo.")
    doc_discovery = ConversationEvidenceDocument(document_id="doc_synth_disc", bundles=(b_d1, b_d2, b_d3))

    t0 = time.time()
    try:
        disc_result = analyzer_discovery.discover_topics(
            document=doc_discovery,
            max_topics=5,
            timeout_seconds=120.0,
        )
        elapsed_4 = time.time() - t0
        print(f"[+] Argomenti scoperti: {len(disc_result.topics)}")
        valid_disc_eids = {s.evidence_id for s in doc_discovery.all_evidence_sections}
        for i, t in enumerate(disc_result.topics):
            print(f"    - Argomento {i+1}: {t.label}")
            print(f"      Descrizione: {t.short_description}")
            print(f"      Citazioni ({len(t.evidence_ids)}): {t.evidence_ids}")
            assert len(t.evidence_ids) > 0
            assert all(eid in valid_disc_eids for eid in t.evidence_ids)
        assert len(disc_result.topics) >= 1
        results.append({
            "test": "Discovery_SYNTHETIC",
            "status": "PASS",
            "topics_count": len(disc_result.topics),
            "elapsed": elapsed_4,
        })
    except Exception as e:
        print(f"[-] FAIL Test 4: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        results.append({"test": "Discovery_SYNTHETIC", "status": "FAIL", "error": str(e)})

    print("\n" + "=" * 70)
    print("RIEPILOGO PROVE LIVE:")
    all_pass = True
    for r in results:
        status_str = "PASS" if r["status"] == "PASS" else "FAIL"
        if r["status"] != "PASS":
            all_pass = False
        print(f"  * {r['test']}: {status_str} ({r.get('elapsed', 0):.2f}s)")
    print(f"Verdetto complessivo LIVE: {'TUTTI I TEST LIVE SUPERATI' if all_pass else 'FALLIMENTO IN ALCUNI TEST'}")
    print("=" * 70)
    return all_pass


if __name__ == "__main__":
    success = run_live_tests()
    sys.exit(0 if success else 1)
