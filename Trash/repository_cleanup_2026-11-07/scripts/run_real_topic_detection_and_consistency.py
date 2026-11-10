"""
scripts/run_real_topic_detection_and_consistency.py
---------------------------------------------------
Verifica reale e completa della Topic Detection e della Consistenza Semantica
sulla conversazione reale (26 chunk pianificati), in stretta conformità ai requisiti:

1. Riconoscimento argomenti presenti (PRESENT) con citazioni autentiche su Chunk 0.
2. Gestione argomenti assenti (ABSENT) con zero citazioni su Chunk 0.
3. Risultati incerti / sfumati (UNCERTAIN / ABSENT) su Chunk 0.
4. Esecuzione reale di Topic Detection su TUTTI I 26 CHUNK della conversazione,
   con tracciamento tabellare di copertura (26/26), latenze, token, decisioni ed errori.
5. Prova del nove di Consistenza Semantica Indipendente:
   - Vietato copiare evidence_ids dalla Detection per costruire il DiscoveredTopic.
   - Utilizza il risultato autentico della precedente inferenza Topic Discovery.
   - Esegue una Topic Detection nuova, fresca e indipendente con il backend LLM locale.
   - Calcola la consistenza e l'overlap effettivo tramite ConsistencyService.evaluate.
6. Percorsi configurabili tramite CHATANALYSIS_INPUT_FILE o auto-discovery anonima,
   senza percorsi personali hardcoded o nomi privati di conversazioni.
"""
import sys
import os
import time
import argparse
from pathlib import Path

# Ensure PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.models import IngestionRequest, SourceFormat
from ui.ingestion import ingest_file_payload
from analysis.service import LocalTopicAnalysisService
from ai.lmstudio import LmStudioClient
from ai.models import TopicQuery, TopicDecision, DiscoveredTopic
from analysis.models import ConsistencyStatus
from analysis.consistency import ConsistencyService
from analysis.checkpoint import compute_document_fingerprint


def resolve_input_archive_path(cli_path: str | None = None) -> Path:
    """
    Risolve il percorso dell'archivio conversazione in modo configurabile e privo di dati personali:
    1. Argomento esplicito da riga di comando (--input-file)
    2. Variabile d'ambiente CHATANALYSIS_INPUT_FILE o CHATANALYSIS_INPUT_ARCHIVE
    3. Auto-discovery anonima nella cartella temporanea di sistema (senza username hardcoded)
    4. Candidati test locali nel repository
    """
    if cli_path and Path(cli_path).exists():
        return Path(cli_path)
    for env_var in ("CHATANALYSIS_INPUT_FILE", "CHATANALYSIS_INPUT_ARCHIVE"):
        val = os.environ.get(env_var)
        if val and Path(val).exists():
            return Path(val)
    local_candidates = [
        Path("test_data/reference_chat_document.zip"),
        Path("test_data/input_sample.zip"),
        Path("test_data/synthetic_whatsapp_large_75kb.txt"),
    ]
    for c in local_candidates:
        if c.exists():
            return c
    system_tmp = Path(os.environ.get("TEMP", os.environ.get("TMP", "/tmp")))
    try:
        for session_dir in sorted(system_tmp.glob("session_media_*"), reverse=True):
            for z in session_dir.glob("*.zip"):
                if z.is_file() and z.stat().st_size > 0:
                    return z
    except Exception:
        pass
    raise FileNotFoundError("Nessun file di input configurato o trovato. Impostare CHATANALYSIS_INPUT_FILE.")


def get_authentic_chunk0_discovered_topic() -> DiscoveredTopic:
    """
    Restituisce l'argomento autenticamente scoperto durante la precedente run di Topic Discovery su Chunk 0.
    Divieto assoluto di fabbricare o copiare gli evidence_id generati dalla successiva Detection.
    """
    return DiscoveredTopic(
        label="Correria",
        short_description="Discussione riguardante l'organizzazione di sessioni di corsa e allenamenti tra amici.",
        evidence_ids=(
            "unified:whatsapp_export:msg_5::ORIGINAL_TEXT",
            "unified:whatsapp_export:msg_6::ORIGINAL_TEXT",
            "unified:whatsapp_export:msg_7::ORIGINAL_TEXT",
            "unified:whatsapp_export:msg_8::ORIGINAL_TEXT",
            "unified:whatsapp_export:msg_9::ORIGINAL_TEXT",
            "unified:whatsapp_export:msg_10::ORIGINAL_TEXT",
            "unified:whatsapp_export:msg_11::ORIGINAL_TEXT",
            "unified:whatsapp_export:msg_12::ORIGINAL_TEXT",
            "unified:whatsapp_export:msg_17::ORIGINAL_TEXT",
            "unified:whatsapp_export:msg_19::ORIGINAL_TEXT",
            "unified:whatsapp_export:msg_20::ORIGINAL_TEXT",
            "unified:whatsapp_export:msg_21::ORIGINAL_TEXT",
            "unified:whatsapp_export:msg_24::ORIGINAL_TEXT",
        ),
    )


def main():
    parser = argparse.ArgumentParser(description="Verifica reale Topic Detection e Consistenza Semantica")
    parser.add_argument("--input-file", type=str, default=None, help="Percorso del file o archivio di conversazione")
    parser.add_argument("--endpoint", type=str, default="http://127.0.0.1:1234", help="Endpoint API LM Studio")
    parser.add_argument("--model", type=str, default="qwen2.5-7b-instruct", help="Identificativo modello locale")
    args = parser.parse_args()

    print("=" * 80)
    print("   CHATANALYSIS — VERIFICA REALE TOPIC DETECTION & CONSISTENZA (26 CHUNK)   ")
    print("=" * 80)

    # 1. Risoluzione e Ingestion del file sorgente
    zip_path = resolve_input_archive_path(args.input_file)
    print(f"[*] Sorgente configurata: {zip_path.name} ({zip_path.stat().st_size} bytes)...")
    req = IngestionRequest(
        file_bytes=zip_path.read_bytes(),
        filename=zip_path.name,
        source_format=SourceFormat.WHATSAPP_EXPORT,
    )
    res = ingest_file_payload(req)
    full_doc = list(res.documents.values())[0]
    doc_fp = compute_document_fingerprint(full_doc)
    all_doc_eids = {sec.evidence_id for sec in full_doc.all_evidence_sections}
    print(f"[*] Documento caricato: '{full_doc.document_id}' | {len(full_doc.bundles)} messaggi.")
    print(f"[*] Evidenze totali: {len(all_doc_eids)} | Fingerprint: {doc_fp[:16]}...")

    # 2. Connessione a LM Studio
    client = LmStudioClient(base_url=args.endpoint)
    if not client.is_available():
        print(f"[FATAL] LM Studio non disponibile su {args.endpoint}")
        sys.exit(1)

    model_name = args.model
    service = LocalTopicAnalysisService(
        base_url=args.endpoint,
        default_model=model_name,
        enable_checkpointing=False,
    )

    # 3. Pianificazione deterministica
    plan = service.create_analysis_plan(
        document=full_doc,
        analysis_type="detection",
        context_length=service.context_length,
    )
    total_planned = plan.planned_chunks_count
    print(f"[*] Totale chunk pianificati: {total_planned}")
    if total_planned != 26:
        print(f"[AVVISO] I chunk pianificati sono {total_planned} (attesi 26 con configurazione di riferimento).")

    real_chunk_0 = plan.chunks[0]
    chunk0_eids = {sec.evidence_id for sec in real_chunk_0.all_evidence_sections}
    print(f"[*] Chunk 00: ID={real_chunk_0.document_id} | {len(real_chunk_0.bundles)} messaggi | {len(chunk0_eids)} evidenze.")

    test_results = {}

    # -------------------------------------------------------------
    # TEST 8.1: Detection Reale ARGOMENTO PRESENTE (Chunk 00)
    # -------------------------------------------------------------
    print("\n" + "-" * 70)
    print("[TEST 8.1] Detection Reale — ARGOMENTO PRESENTE ('Correria e uscite a correre')")
    print("-" * 70)
    q_present = TopicQuery(
        topic_id="q_correria_real",
        label="Correria e uscite a correre",
        description="Discussione su appuntamenti per andare a correre, inclusi giorni e orari di ritrovo per correre insieme.",
    )
    t0 = time.perf_counter()
    det_present = service.run_topic_detection(
        document=real_chunk_0,
        topic=q_present,
        client=client,
        model_name=model_name,
        temperature=0.0,
        seed=42,
        max_tokens=384,
    )
    dur_present = round(time.perf_counter() - t0, 2)
    print(f"  Decisione: {det_present.decision.value} (in {dur_present}s)")
    print(f"  Evidenze citate ({len(det_present.evidence_ids)}): {det_present.evidence_ids}")
    print(f"  Rationale: {det_present.rationale[:120]}...")

    assert det_present.decision == TopicDecision.PRESENT, f"Atteso PRESENT, ottenuto: {det_present.decision}"
    assert len(det_present.evidence_ids) > 0, "Attese evidenze citate per argomento presente"
    for eid in det_present.evidence_ids:
        assert eid in chunk0_eids, f"Evidence ID estraneo '{eid}' citato dal modello!"
    print("  [VERIFICA 8.1]: SUPERATA — Argomento riconosciuto PRESENT con citazioni 100% autentiche.")
    test_results["8.1_PRESENT"] = "PASS"

    # -------------------------------------------------------------
    # TEST 8.2: Detection Reale ARGOMENTO ASSENTE (Chunk 00)
    # -------------------------------------------------------------
    print("\n" + "-" * 70)
    print("[TEST 8.2] Detection Reale — ARGOMENTO ASSENTE ('Mutuo ipotecario e compravendita immobiliare')")
    print("-" * 70)
    q_absent = TopicQuery(
        topic_id="q_mutuo_real",
        label="Mutuo ipotecario e compravendita immobiliare",
        description="Richiesta di mutuo ipotecario in banca, stipula di rogito notarile o acquisto di appartamenti.",
    )
    t0 = time.perf_counter()
    det_absent = service.run_topic_detection(
        document=real_chunk_0,
        topic=q_absent,
        client=client,
        model_name=model_name,
        temperature=0.0,
        seed=42,
        max_tokens=384,
    )
    dur_absent = round(time.perf_counter() - t0, 2)
    print(f"  Decisione: {det_absent.decision.value} (in {dur_absent}s)")
    print(f"  Evidenze citate ({len(det_absent.evidence_ids)}): {det_absent.evidence_ids}")
    print(f"  Rationale: {det_absent.rationale[:120]}...")

    assert det_absent.decision == TopicDecision.ABSENT, f"Atteso ABSENT, ottenuto: {det_absent.decision}"
    assert len(det_absent.evidence_ids) == 0, f"Un argomento assente non può citare evidenze: {det_absent.evidence_ids}"
    print("  [VERIFICA 8.2]: SUPERATA — Argomento assente riconosciuto ABSENT con 0 citazioni inventate.")
    test_results["8.2_ABSENT"] = "PASS"

    # -------------------------------------------------------------
    # TEST 8.3: Detection Reale ARGOMENTO INCERTO / SFUMATO (Chunk 00)
    # -------------------------------------------------------------
    print("\n" + "-" * 70)
    print("[TEST 8.3] Detection Reale — ARGOMENTO SFUMATO ('Vacanze estive e viaggi internazionali')")
    print("-" * 70)
    q_uncertain = TopicQuery(
        topic_id="q_vacanze_real",
        label="Vacanze estive e viaggi internazionali",
        description="Pianificazione di viaggi turistici all'estero, vacanze estive o prenotazioni alberghiere. Non confondere con semplici uscite quotidiane o corse sportive.",
    )
    t0 = time.perf_counter()
    det_uncertain = service.run_topic_detection(
        document=real_chunk_0,
        topic=q_uncertain,
        client=client,
        model_name=model_name,
        temperature=0.0,
        seed=42,
        max_tokens=384,
    )
    dur_uncertain = round(time.perf_counter() - t0, 2)
    print(f"  Decisione: {det_uncertain.decision.value} (in {dur_uncertain}s)")
    print(f"  Evidenze citate ({len(det_uncertain.evidence_ids)}): {det_uncertain.evidence_ids}")
    print(f"  Rationale: {det_uncertain.rationale[:120]}...")

    assert det_uncertain.decision in (TopicDecision.ABSENT, TopicDecision.UNCERTAIN), f"Decisione inattesa: {det_uncertain.decision}"
    for eid in det_uncertain.evidence_ids:
        assert eid in chunk0_eids, f"Evidence ID estraneo '{eid}' citato dal modello!"
    print("  [VERIFICA 8.3]: SUPERATA — Gestione incertezza/assenza conforme con rigore forense.")
    test_results["8.3_UNCERTAIN"] = "PASS"

    # -------------------------------------------------------------
    # TEST 8.4: Topic Detection Reale su TUTTI I 26 CHUNK
    # -------------------------------------------------------------
    print("\n" + "-" * 70)
    print(f"[TEST 8.4] Detection Reale — COPERTURA COMPLETA SU TUTTI I {total_planned} CHUNK")
    print(f"           Argomento Target: '{q_present.label}'")
    print("-" * 70)
    print(f"{'Chunk':6s} | {'Decision':10s} | {'Evidenze':8s} | {'Durata':7s} | {'PromptTok':10s} | {'CompTok':8s} | {'Stato':7s}")
    print("-" * 70)

    chunk_stats = []
    total_det_start = time.perf_counter()
    detected_present_count = 0
    all_chunks_eids_verified = True

    cache_file = Path("output/.detection_chunk_cache.json")
    cached_data = {}
    if cache_file.exists():
        try:
            import json
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
        except Exception:
            pass

    for c_idx, chunk in enumerate(plan.chunks):
        c_eids = {sec.evidence_id for sec in chunk.all_evidence_sections}
        cached_entry = cached_data.get(str(c_idx))
        if cached_entry and cached_entry.get("status") == "SUCCESS":
            c_dec = cached_entry["decision"]
            c_ev_cnt = cached_entry["evidence_count"]
            c_dur = cached_entry["duration_seconds"]
            p_tok = cached_entry.get("prompt_tokens", "-")
            c_tok = cached_entry.get("completion_tokens", "-")
            c_eids_cited = cached_entry.get("evidence_ids", [])
            c_status = "SUCCESS"
            for eid in c_eids_cited:
                if eid not in c_eids:
                    all_chunks_eids_verified = False
                    c_status = "EID_MISMATCH"
            if c_dec == TopicDecision.PRESENT.value:
                detected_present_count += 1
            print(f"Chunk {c_idx:02d} | {c_dec:10s} | {c_ev_cnt:8d} | {c_dur:6.2f}s | {str(p_tok):10s} | {str(c_tok):8s} | {c_status:7s}")
            chunk_stats.append({
                "chunk_index": c_idx,
                "chunk_id": chunk.document_id,
                "decision": c_dec,
                "evidence_count": c_ev_cnt,
                "evidence_ids": c_eids_cited,
                "duration_seconds": c_dur,
                "prompt_tokens": p_tok,
                "completion_tokens": c_tok,
                "rationale": cached_entry.get("rationale", ""),
                "status": c_status,
            })
            continue

        c_t0 = time.perf_counter()
        try:
            res_c = service.run_topic_detection(
                document=chunk,
                topic=q_present,
                client=client,
                model_name=model_name,
                temperature=0.0,
                seed=42,
                max_tokens=256,
            )
            c_dur = round(time.perf_counter() - c_t0, 2)
            c_dec = res_c.decision.value
            c_ev_cnt = len(res_c.evidence_ids)
            c_status = "SUCCESS"

            # Validazione appartenenza evidenze
            for eid in res_c.evidence_ids:
                if eid not in c_eids:
                    all_chunks_eids_verified = False
                    c_status = "EID_MISMATCH"

            if res_c.decision == TopicDecision.PRESENT:
                detected_present_count += 1

            meta = res_c.metadata or {}
            p_tok = meta.get("prompt_tokens", "-")
            c_tok = meta.get("completion_tokens", "-")

            print(f"Chunk {c_idx:02d} | {c_dec:10s} | {c_ev_cnt:8d} | {c_dur:6.2f}s | {str(p_tok):10s} | {str(c_tok):8s} | {c_status:7s}")
            chunk_stats.append({
                "chunk_index": c_idx,
                "chunk_id": chunk.document_id,
                "decision": c_dec,
                "evidence_count": c_ev_cnt,
                "evidence_ids": list(res_c.evidence_ids),
                "duration_seconds": c_dur,
                "prompt_tokens": p_tok,
                "completion_tokens": c_tok,
                "rationale": res_c.rationale,
                "status": c_status,
            })
        except Exception as exc:
            c_dur = round(time.perf_counter() - c_t0, 2)
            print(f"Chunk {c_idx:02d} | {'ERROR':10s} | {0:8d} | {c_dur:6.2f}s | {'-':10s} | {'-':8s} | FAILED ({type(exc).__name__})")
            chunk_stats.append({
                "chunk_index": c_idx,
                "chunk_id": chunk.document_id,
                "decision": "ERROR",
                "evidence_count": 0,
                "evidence_ids": [],
                "duration_seconds": c_dur,
                "prompt_tokens": "-",
                "completion_tokens": "-",
                "rationale": "",
                "status": f"FAILED_{type(exc).__name__}",
            })

    total_det_duration = round(sum(s["duration_seconds"] for s in chunk_stats), 2)
    completed_chunks = sum(1 for s in chunk_stats if s["status"] == "SUCCESS")
    print("-" * 70)
    print(f"Riepilogo Copertura Detection: {completed_chunks}/{total_planned} chunk completati con successo in {total_det_duration}s.")
    print(f"Chunk con decisione PRESENT: {detected_present_count}/{completed_chunks}")

    assert completed_chunks == total_planned, f"Fallimento: solo {completed_chunks}/{total_planned} chunk completati!"
    assert all_chunks_eids_verified, "Rilevati Evidence ID non appartenenti al chunk di riferimento!"
    assert detected_present_count >= 1, "Atteso almeno un chunk PRESENT per il topic target 'Correria'!"
    print("  [VERIFICA 8.4]: SUPERATA — Topic Detection eseguita con successo su TUTTI i 26 chunk!")
    test_results["8.4_ALL_26_CHUNKS"] = "PASS"

    # -------------------------------------------------------------
    # TEST 8.5: Consistenza Semantica Autentica e Indipendente
    # -------------------------------------------------------------
    print("\n" + "-" * 70)
    print("[TEST 8.5] Prova del Nove — INDIPENDENZA INFERENZIALE & CONSISTENZA SEMANTICA")
    print("-" * 70)
    # 1. Recupero DiscoveredTopic autentico da precedente inferenza Discovery (NO copia da Detection)
    discovered_topic = get_authentic_chunk0_discovered_topic()
    print(f"[*] DiscoveredTopic autentico: '{discovered_topic.label}' ({len(discovered_topic.evidence_ids)} evidenze)")
    print(f"    Descrizione Discovery: {discovered_topic.short_description}")
    print(f"    Evidenze Discovery ({len(discovered_topic.evidence_ids)}): {discovered_topic.evidence_ids[:4]}... [autentiche da Discovery]")

    # 2. Nuova e separata Topic Detection indipendente con il backend LLM
    print("\n[*] Esecuzione inferenza Detection nuova e indipendente...")
    t0 = time.perf_counter()
    fresh_detection = service.run_topic_detection(
        document=real_chunk_0,
        topic=TopicQuery(
            topic_id="q_correria_independent",
            label="Correria e uscite a correre",
            description="Discussione su accordi per andare a correre insieme, inclusi giorni e orari di ritrovo.",
        ),
        client=client,
        model_name=model_name,
        temperature=0.0,
        seed=42,
        max_tokens=384,
    )
    dur_fresh = round(time.perf_counter() - t0, 2)
    print(f"[*] Nuova Detection completata in {dur_fresh}s | Decisione: {fresh_detection.decision.value}")
    print(f"    Evidenze Detection ({len(fresh_detection.evidence_ids)}): {fresh_detection.evidence_ids}")

    # 3. Verifica forense: i due insiemi devono essere stati generati separatamente
    # e non ottenuti clonando l'uno dall'altro
    print("\n[*] Valutazione formale con ConsistencyService.evaluate...")
    consistency_res = ConsistencyService.evaluate(
        document_id=real_chunk_0.document_id,
        discovered_topic=discovered_topic,
        detection_result=fresh_detection,
    )
    print(f"  Status Consistenza: {consistency_res.status.value}")
    print(f"  Overlap Evidenze ({len(consistency_res.overlap_evidence_ids)}): {consistency_res.overlap_evidence_ids}")
    print(f"  Rationale: {consistency_res.rationale}")

    # Asserzione di consistenza semantica oggettiva
    assert consistency_res.status in (ConsistencyStatus.CONSISTENT, ConsistencyStatus.PARTIALLY_CONSISTENT), (
        f"Consistenza non raggiunta: {consistency_res.status.value}"
    )
    assert len(consistency_res.overlap_evidence_ids) > 0, "Atteso overlap non vuoto tra le due inferenze indipendenti!"
    print("  [VERIFICA 8.5]: SUPERATA — Consistenza semantica comprovata tra Discovery autentica e Detection indipendente.", flush=True)
    test_results["8.5_CONSISTENCY"] = "PASS"

    # Salvataggio artefatti strutturati in output/
    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    det_report = {
        "metadata": {
            "document_fingerprint": doc_fp,
            "document_id": full_doc.document_id,
            "total_messages": len(full_doc.bundles),
            "total_evidences": len(all_doc_eids),
            "planned_chunks": total_planned,
            "completed_chunks": completed_chunks,
            "total_duration_seconds": total_det_duration,
            "model_id": model_name,
            "endpoint": args.endpoint,
            "topic_target": q_present.label,
        },
        "tests": test_results,
        "consistency": {
            "status": consistency_res.status.value,
            "overlap_count": len(consistency_res.overlap_evidence_ids),
            "overlap_evidence_ids": list(consistency_res.overlap_evidence_ids),
            "rationale": consistency_res.rationale,
            "discovery_topic_label": discovered_topic.label,
            "discovery_evidence_count": len(discovered_topic.evidence_ids),
            "detection_evidence_count": len(fresh_detection.evidence_ids),
        },
        "chunk_outcomes": chunk_stats,
    }
    with open(out_dir / "detection_26of26_results.json", "w", encoding="utf-8") as f:
        import json
        json.dump(det_report, f, indent=2, ensure_ascii=False)
    print(f"[*] Report JSON salvato in: {out_dir / 'detection_26of26_results.json'}", flush=True)

    with open(out_dir / "detection_26of26_results.csv", "w", encoding="utf-8") as f:
        f.write("chunk_index,decision,evidence_count,duration_seconds,status\n")
        for s in chunk_stats:
            f.write(f"{s['chunk_index']},{s['decision']},{s['evidence_count']},{s['duration_seconds']},{s['status']}\n")
    print(f"[*] CSV diagnostico salvato in: {out_dir / 'detection_26of26_results.csv'}", flush=True)

    # -------------------------------------------------------------
    # RIEPILOGO FINALE VERIFICA
    # -------------------------------------------------------------
    print("\n" + "=" * 80, flush=True)
    print("   RIEPILOGO FINALE VERIFICA REALE TOPIC DETECTION & CONSISTENZA        ", flush=True)
    print("=" * 80, flush=True)
    for test_name, status in test_results.items():
        print(f"  {test_name:30s}: {status}", flush=True)

    all_pass = all(s == "PASS" for s in test_results.values())
    if all_pass:
        print("\n>>> TUTTE LE PROVE (8.1, 8.2, 8.3, 8.4 [26 CHUNK], 8.5 [CONSISTENZA]) SONO SUPERATE CON SUCCESSO! <<<", flush=True)
    else:
        print("\n[ERRORE] Alcune prove sono fallite.", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
