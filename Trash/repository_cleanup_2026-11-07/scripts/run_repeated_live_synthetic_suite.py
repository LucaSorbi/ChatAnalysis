"""
scripts/run_repeated_live_synthetic_suite.py
--------------------------------------------
Complete synthetic live validation suite with real LM Studio (qwen2.5-7b-instruct)
verifying all Section 5 requirements across 3 complete cycles:
1. Detection: PRESENT
2. Detection: ABSENT
3. Detection: UNCERTAIN
4. Discovery: Single Chunk
5. Discovery: Multi-Chunk
6. Discovery: High JSON volume
7. GBNF crash non-repetition verification
8. Controlled subchunk split on truncation
9. Interruption and subchunk resume from SQLite
10. Execution across 3 complete cycles
"""
import sys
import os
import time
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone

# Ensure project root in PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.backend import (
    AiBackendRequestError,
    AiStructuredOutputError,
    BaseLocalLlmClient,
    LlmCompletionResponse,
)
from ai.lmstudio import LmStudioClient
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    DiscoveredTopic,
    MessageEvidenceBundle,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from ai.topics import (
    TopicDetectionAnalyzer,
    TopicDiscoveryAnalyzer,
)
from analysis.checkpoint import AnalysisCheckpointStore
from analysis.models import OperationalAnalysisConfig
from analysis.service import LocalTopicAnalysisService
from importer.models import RawRecord
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult

def build_synthetic_doc(doc_id: str, texts: list[tuple[str, str, str]]) -> ConversationEvidenceDocument:
    """Helper to build a ConversationEvidenceDocument from list of (sender, timestamp_str, text)."""
    bundles = []
    for i, (sender, ts_str, text) in enumerate(texts):
        raw = RawRecord(
            source_name="whatsapp_synth",
            source_path="/synth",
            source_record_id=str(i),
            record_type="message",
            raw_fields={"text": text, "sender": sender, "timestamp": ts_str},
            media_reference=None,
            metadata={},
        )
        val = ValidationResult(record=raw, issues=())
        ts = NormalizedTimestamp(
            status=TimestampTzStatus.KNOWN_UTC,
            utc_datetime=datetime(2026, 1, 1, 10, i, 0, tzinfo=timezone.utc),
            naive_datetime=None,
            iso_string=ts_str,
            raw_value=ts_str,
        )
        norm = NormalizedRecord(
            raw_record=raw,
            validation_result=val,
            source_name="whatsapp_synth",
            source_record_id=str(i),
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=text,
            media_reference=None,
        )
        um = UnifiedMessage(
            message_id=f"msg_{doc_id}_{i:03d}",
            source_name="whatsapp_synth",
            source_record_id=str(i),
            source_path="/synth",
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=text,
            media_reference=None,
            provenance_record=norm,
        )
        bundles.append(MessageEvidenceBundle(message=um))
    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=tuple(bundles),
        source_name="whatsapp_synth",
    )

def run_single_cycle(cycle_num: int, client: LmStudioClient, model_name: str):
    print(f"\n========================================================")
    print(f"   STARTING SYNTHETIC VALIDATION CYCLE {cycle_num}/3")
    print(f"========================================================")
    t_start = time.perf_counter()
    cycle_results = {}

    det_analyzer = TopicDetectionAnalyzer(client=client, default_model_id=model_name, operational_mode=True)
    disc_analyzer = TopicDiscoveryAnalyzer(client=client, default_model_id=model_name, operational_mode=True)

    # 1. Detection: PRESENT
    doc_car = build_synthetic_doc("car_synth", [
        ("Alice", "2026-01-01T10:00:00Z", "Ho visto l'annuncio per quella Fiat 500 usata."),
        ("Bob", "2026-01-01T10:01:00Z", "Sì, possiamo andare dal concessionario oggi pomeriggio a provarla."),
        ("Alice", "2026-01-01T10:02:00Z", "Perfetto, il venditore chiede 8500 euro trattabili."),
    ])
    q_car = TopicQuery(topic_id="q_car", label="Acquisto automobile", description="Trattativa o compravendita di una vettura")
    res_pres = det_analyzer.detect_topic(document=doc_car, topic=q_car, model_id=model_name, temperature=0.0, seed=42)
    assert res_pres.decision == TopicDecision.PRESENT, f"Atteso PRESENT, ottenuto {res_pres.decision}"
    assert len(res_pres.evidence_ids) >= 1, "PRESENT deve citare evidenze"
    cycle_results["1_detection_present"] = "PASS"
    print(f"[{cycle_num}.1] Detection PRESENT: PASS ({res_pres.decision.value}, {len(res_pres.evidence_ids)} evidenze)")

    # 2. Detection: ABSENT
    q_holiday = TopicQuery(topic_id="q_holiday", label="Vacanze al mare", description="Viaggio e spiaggia")
    res_abs = det_analyzer.detect_topic(document=doc_car, topic=q_holiday, model_id=model_name, temperature=0.0, seed=42)
    assert res_abs.decision == TopicDecision.ABSENT, f"Atteso ABSENT, ottenuto {res_abs.decision}"
    assert len(res_abs.evidence_ids) == 0, "ABSENT deve avere 0 evidenze"
    cycle_results["2_detection_absent"] = "PASS"
    print(f"[{cycle_num}.2] Detection ABSENT: PASS ({res_abs.decision.value}, 0 evidenze)")

    # 3. Detection: UNCERTAIN
    doc_ambig = build_synthetic_doc("ambig_synth", [
        ("Alice", "2026-01-01T10:00:00Z", "Oggi non sono molto in forma."),
        ("Bob", "2026-01-01T10:01:00Z", "Forse hai solo dormito poco o sei stanca per il lavoro."),
    ])
    q_health = TopicQuery(topic_id="q_health", label="Grave patologia medica", description="Diagnosi di una malattia seria o cronica")
    res_unc = det_analyzer.detect_topic(document=doc_ambig, topic=q_health, model_id=model_name, temperature=0.0, seed=42)
    # L'argomento è ambiguo/non confermato: ABSENT o UNCERTAIN sono validi, MAI PRESENT
    assert res_unc.decision in (TopicDecision.UNCERTAIN, TopicDecision.ABSENT), f"Non deve essere PRESENT: {res_unc.decision}"
    cycle_results["3_detection_uncertain"] = "PASS"
    print(f"[{cycle_num}.3] Detection UNCERTAIN/ABSENT: PASS ({res_unc.decision.value})")

    # 4. Discovery: Single Chunk
    res_disc_single = disc_analyzer.discover_topics(document=doc_car, model_id=model_name, max_topics=3, temperature=0.0, seed=42)
    assert len(res_disc_single.topics) >= 1, "Deve scoprire almeno 1 topic"
    for t in res_disc_single.topics:
        assert len(t.evidence_ids) >= 1, "Ogni topic deve avere evidenze"
    cycle_results["4_discovery_single"] = "PASS"
    print(f"[{cycle_num}.4] Discovery Single Chunk: PASS ({len(res_disc_single.topics)} topic scoperti)")

    # 5. Discovery: Multi-Chunk via LocalTopicAnalysisService
    doc_multi = build_synthetic_doc("multi_synth", [
        ("Alice", "2026-01-01T10:00:00Z", "Dobbiamo ordinare il materiale per la ristrutturazione della cucina."),
        ("Bob", "2026-01-01T10:01:00Z", "Ho contattato il fornitore di piastrelle e il falegname."),
        ("Alice", "2026-01-01T10:02:00Z", "Poi ricordati della prenotazione della visita medica per giovedì."),
        ("Bob", "2026-01-01T10:03:00Z", "Fatto, appuntamento confermato per le 15:30."),
        ("Alice", "2026-01-01T10:04:00Z", "Stasera cosa cuciniamo per cena? Possiamo fare una pasta semplice."),
        ("Bob", "2026-01-01T10:05:00Z", "Ottimo, compro io gli ingredienti tornando dall'ufficio."),
    ])
    tmp_dir = tempfile.mkdtemp(prefix=f"chk_cycle_{cycle_num}_")
    try:
        service = LocalTopicAnalysisService(
            base_url="http://127.0.0.1:1234",
            default_model=model_name,
            enable_checkpointing=True,
            checkpoint_dir=tmp_dir,
        )
        res_multi = service.run_topic_discovery(
            document=doc_multi,
            client=client,
            model_name=model_name,
            max_topics=4,
            temperature=0.0,
            seed=42,
        )
        assert len(res_multi.topics) >= 1, "Multi-chunk discovery deve completare con successo"
        cycle_results["5_discovery_multi"] = "PASS"
        print(f"[{cycle_num}.5] Discovery Multi-Chunk: PASS ({len(res_multi.topics)} topic)")

        # 6. Discovery: High JSON Volume
        doc_dense = build_synthetic_doc("dense_synth", [
            ("Alice", "2026-01-01T10:00:00Z", "Tema 1: Abbiamo approvato il budget annuale per il reparto IT."),
            ("Bob", "2026-01-01T10:01:00Z", "Tema 2: La migrazione al nuovo cloud server avverrà venerdì notte."),
            ("Alice", "2026-01-01T10:02:00Z", "Tema 3: Il corso di sicurezza informatica è obbligatorio per tutti i dipendenti."),
            ("Bob", "2026-01-01T10:03:00Z", "Tema 4: Abbiamo rinnovato i contratti dei consulenti esterni."),
        ])
        res_dense = service.run_topic_discovery(
            document=doc_dense,
            client=client,
            model_name=model_name,
            max_topics=4,
            temperature=0.0,
            seed=42,
            max_tokens=1280,
        )
        assert len(res_dense.topics) >= 2, "Dense discovery deve trovare più topic distinti"
        cycle_results["6_discovery_dense"] = "PASS"
        print(f"[{cycle_num}.6] Discovery Dense/High JSON: PASS ({len(res_dense.topics)} topic scoperti)")

        # 7. GBNF Non-Repetition: Multiple fast back-to-back checks
        for test_rep in range(3):
            det_res = service.run_topic_detection(
                document=doc_car,
                topic=q_car,
                client=client,
                model_name=model_name,
                operational_mode=True,
            )
            assert det_res.decision == TopicDecision.PRESENT
        cycle_results["7_gbnf_stability"] = "PASS"
        print(f"[{cycle_num}.7] GBNF Non-Repetition: PASS (3/3 rapide senza errori)")

        # 8 & 9. Controlled Subchunk Split, Persistence & Resume Test
        # Test subchunk splitting and resume on SQLite
        chk_store = AnalysisCheckpointStore(checkpoint_dir=tmp_dir)
        doc_split_test = build_synthetic_doc("split_resume_test", [
            ("Alice", "2026-01-01T10:00:00Z", "Messaggio parte A: Compravendita immobile."),
            ("Bob", "2026-01-01T10:01:00Z", "Messaggio parte B: Pratica notarile e mutuo."),
        ])
        
        # Test explicit subchunk processing via service internal method
        from analysis.progress import AnalysisProgressTracker
        tracker = AnalysisProgressTracker(total_chunks=1)
        
        # Sotto-blocco 1
        sub_outs, sub_res = service._process_subchunks_discovery(
            chunk=doc_split_test,
            analyzer=disc_analyzer,
            resolved_model=model_name,
            eff_temp=0.0,
            eff_seed=42,
            chunk_target_topics=2,
            tracker=tracker,
            chunk_idx_1based=1,
            chk_store=chk_store,
            doc_fp="test_doc_fp_split",
            cfg_fp="test_cfg_fp_split",
            parent_chunk_index=0,
            parent_document_id=doc_split_test.document_id,
            total_planned_chunks=1,
            eff_max_tokens=1280,
        )
        assert len(sub_outs) == 2, "Devono essere prodotti 2 sotto-blocchi"
        assert all(so.status == "SUCCESS" for so in sub_outs), "Tutti i sotto-blocchi devono avere successo"
        cycle_results["8_subchunk_split"] = "PASS"
        print(f"[{cycle_num}.8] Controlled Subchunk Split: PASS (2 sotto-blocchi completati e salvati)")

        # Test Resume dei subchunks salvati (Zero nuove inferenze per i subchunk cached)
        # Verifichiamo che get_subchunk legga i dati
        cached_sub0 = chk_store.get_subchunk("test_doc_fp_split", "test_cfg_fp_split", parent_chunk_index=0, subchunk_index=0)
        cached_sub1 = chk_store.get_subchunk("test_doc_fp_split", "test_cfg_fp_split", parent_chunk_index=0, subchunk_index=1)
        assert cached_sub0 is not None and cached_sub1 is not None, "I subchunks devono essere persistiti in SQLite"
        
        # Nuova esecuzione: deve riprendere da checkpoint con status RECOVERED_FROM_CHECKPOINT
        tracker2 = AnalysisProgressTracker(total_chunks=1)
        sub_outs2, sub_res2 = service._process_subchunks_discovery(
            chunk=doc_split_test,
            analyzer=disc_analyzer,
            resolved_model=model_name,
            eff_temp=0.0,
            eff_seed=42,
            chunk_target_topics=2,
            tracker=tracker2,
            chunk_idx_1based=1,
            chk_store=chk_store,
            doc_fp="test_doc_fp_split",
            cfg_fp="test_cfg_fp_split",
            parent_chunk_index=0,
            parent_document_id=doc_split_test.document_id,
            total_planned_chunks=1,
            eff_max_tokens=1280,
        )
        assert len(sub_outs2) == 2
        assert all(so.status == "RECOVERED_FROM_CHECKPOINT" for so in sub_outs2), "I subchunks devono essere ripristinati senza nuova inferenza"
        cycle_results["9_subchunk_resume"] = "PASS"
        print(f"[{cycle_num}.9] Subchunk Resume: PASS (recuperati 2/2 sotto-blocchi senza nuova inferenza)")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    elapsed_cycle = round(time.perf_counter() - t_start, 2)
    print(f"=== CYCLE {cycle_num} COMPLETED IN {elapsed_cycle}s — ALL 9 TESTS PASSED ===")
    return cycle_results

def main():
    print("=================================================================")
    print("  CHATANALYSIS — RUNNING 3 COMPLETE SYNTHETIC VALIDATION CYCLES  ")
    print("=================================================================")
    client = LmStudioClient()
    if not client.is_available():
        print("[FATAL] LM Studio non raggiungibile su http://127.0.0.1:1234")
        sys.exit(1)

    model_name = "qwen2.5-7b-instruct"
    all_cycles = []
    t_global_start = time.perf_counter()

    for c in range(1, 4):
        res = run_single_cycle(c, client, model_name)
        all_cycles.append(res)
        time.sleep(1.0)

    total_time = round(time.perf_counter() - t_global_start, 2)
    print("\n=================================================================")
    print(f"  ALL 3 CYCLES COMPLETED SUCCESSFULLY IN {total_time}s!")
    print("=================================================================")
    for idx, c_res in enumerate(all_cycles, 1):
        print(f"Cycle {idx}: {len(c_res)}/9 tests PASSED")
    sys.exit(0)

if __name__ == "__main__":
    main()
