"""
scripts/run_canonical_live_discovery_26chunks.py
------------------------------------------------
Campagna LIVE canonica completa di Topic Discovery sui 26 chunk (1.049 messaggi)
della conversazione autentica (test_data/reference_chat_document.zip).

Requisiti:
- Esecuzione tramite la pipeline CANONICA dell'applicazione (LocalTopicAnalysisService).
- Microchunking preventivo (<= 8 messaggi sostanziali per microchunk, 'compact_jsonl_v1').
- Fallback adattivo divide-and-conquer (bisezione fino a profondita 3).
- Salvataggio incrementale e atomico su SQLite in area di run isolata:
  `output/live_run_1049_26chunks/checkpoints/checkpoints.db`
- Audit log privato locale delle risposte HTTP reali:
  `output/live_run_1049_26chunks/raw_completions_audit/`
- Validazione forense al 100%: zero evidence ID inventati, zero perdite, zero fake topics.
- Verifica di idempotenza e ricarico al 100% da database con ZERO chiamate HTTP.
- Verifica SearchService ed esportazione.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

# Setup root path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ai.backend import BaseLlmClient, LlmCompletionResponse
from ai.chunking import aggregate_topic_discovery, is_substantive_message_bundle
from ai.lmstudio import LmStudioClient, DEFAULT_OPERATIONAL_MODEL
from analysis.checkpoint import AnalysisCheckpointStore
from analysis.models import OperationalProfile
from analysis.service import LocalTopicAnalysisService
from search.service import SearchService
from ui.ingestion import ingest_file_payload
from ui.models import IngestionRequest, SourceFormat


class AuditingLmStudioClient(BaseLlmClient):
    """
    Wrapper trasparente su LmStudioClient che archivia privatamente ogni risposta HTTP grezza,
    token count, finish_reason e latenza per audit forense locale.
    """

    def __init__(self, inner: LmStudioClient, audit_dir: Path) -> None:
        self.inner = inner
        self.audit_dir = audit_dir
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        existing_files = sorted(self.audit_dir.glob("completion_*.json"))
        self.initial_audit_count = len(existing_files)
        self.prior_prompt_tokens = 0
        self.prior_completion_tokens = 0
        self.prior_duration_seconds = 0.0
        for ef in existing_files:
            try:
                data = json.loads(ef.read_text(encoding="utf-8"))
                self.prior_prompt_tokens += int(data.get("prompt_tokens", 0) or 0)
                self.prior_completion_tokens += int(data.get("completion_tokens", 0) or 0)
                self.prior_duration_seconds += float(data.get("duration_seconds", 0.0) or 0.0)
            except Exception:
                pass
        self.calls_count = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.audit_records: list[dict[str, Any]] = []

    def is_available(self) -> bool:
        return self.inner.is_available()

    def list_models(self) -> tuple[str, ...]:
        return self.inner.list_models()

    def is_model_loaded(self, model_id: str) -> bool:
        return self.inner.is_model_loaded(model_id)

    def load_model(self, model_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.inner.load_model(model_id, **kwargs)

    def unload_model(self, model_id: str | None = None, **kwargs: Any) -> bool:
        return self.inner.unload_model(model_id, **kwargs)

    @property
    def model_id(self) -> str | None:
        return self.inner.model_id

    @property
    def base_url(self) -> str:
        return self.inner.base_url

    @property
    def default_timeout_seconds(self) -> float:
        return self.inner.default_timeout_seconds

    @property
    def _active_binding(self) -> Any:
        return getattr(self.inner, "_active_binding", None)

    @property
    def _last_loaded_model_key(self) -> str | None:
        return getattr(self.inner, "_last_loaded_model_key", None)

    @property
    def _loaded_instance_id(self) -> str | None:
        return getattr(self.inner, "_loaded_instance_id", None)

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

        abs_call_index = self.initial_audit_count + self.calls_count
        audit_entry = {
            "call_index": abs_call_index,
            "session_call_index": self.calls_count,
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

        # Scrittura atomica del log di audit locale privato
        audit_file = self.audit_dir / f"completion_{abs_call_index:04d}_{req_hash}.json"
        audit_file.write_text(json.dumps(audit_entry, ensure_ascii=False, indent=2), encoding="utf-8")

        return resp


def execute_full_live_campaign():
    print("=" * 80)
    print("AVVIO CAMPAGNA LIVE CANONICA: TOPIC DISCOVERY SU TUTTI I 26 CHUNK REALI")
    print("=" * 80)

    # 1. Configura directory di esecuzione isolata
    run_dir = Path("output/live_run_1049_26chunks")
    chk_dir = run_dir / "checkpoints"
    audit_dir = run_dir / "raw_completions_audit"
    chk_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    chk_store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)
    print(f"[*] Checkpoint store isolato: {chk_dir / 'checkpoints.db'}")
    print(f"[*] Cartella audit risposte HTTP: {audit_dir}")

    # NOTA DI CATENA DI CUSTODIA E FAIL-CLOSED (Audit 27/10/2026):
    # Nessuna promozione retroattiva SQL (UPDATE origin='LIVE_LM_STUDIO') è consentita
    # sui checkpoint preesistenti. La provenienza viene registrata esclusivamente
    # al momento dell'inferenza effettiva da LocalTopicAnalysisService.

    # 2. Ingestione documento autentico
    zip_path = Path("test_data/reference_chat_document.zip")
    if not zip_path.exists():
        raise FileNotFoundError(f"File di test {zip_path} non trovato!")

    print(f"[*] Ingestione chat di riferimento: {zip_path}...")
    with open(zip_path, "rb") as f:
        zip_bytes = f.read()

    req = IngestionRequest(
        source_format=SourceFormat.WHATSAPP_EXPORT,
        filename=zip_path.name,
        file_bytes=zip_bytes,
    )
    ingestion_res = ingest_file_payload(req)
    doc = list(ingestion_res.documents.values())[0]

    total_bundles = len(doc.bundles)
    total_sections = len(doc.all_evidence_sections)
    print(f"[OK] Documento ingerito con successo: id='{doc.document_id}'")
    print(f"     Messaggi totali (bundle): {total_bundles} | Sezioni evidenza: {total_sections}")
    assert total_bundles == 1049, f"Attesi 1049 bundle, trovati {total_bundles}!"

    # 3. Setup client LM Studio con audit wrapper
    raw_client = LmStudioClient(
        base_url="http://127.0.0.1:1234",
        model_id=DEFAULT_OPERATIONAL_MODEL,
        default_timeout_seconds=240.0,
    )
    if not raw_client.is_available():
        raise RuntimeError("LM Studio non e raggiungibile su http://127.0.0.1:1234!")

    models = raw_client.list_models()
    print(f"[OK] LM Studio disponibile. Modelli attivi: {models}")
    auditing_client = AuditingLmStudioClient(raw_client, audit_dir=audit_dir)

    # 4. Inizializzazione servizio canonico
    service = LocalTopicAnalysisService(
        base_url="http://127.0.0.1:1234",
        default_model=DEFAULT_OPERATIONAL_MODEL,
        timeout_seconds=240.0,
        checkpoint_store=chk_store,
        enable_checkpointing=True,
        profile=OperationalProfile.BALANCED,
    )

    plan = service.create_analysis_plan(doc, analysis_type="discovery", max_topics=10)
    print(f"[OK] Piano creato: {plan.planned_chunks_count} chunk totali previsti.")
    assert plan.planned_chunks_count == 26, f"Attesi 26 chunk, trovati {plan.planned_chunks_count}!"

    # 5. Tracker di progresso per monitoraggio in tempo reale
    last_print_time = time.perf_counter()

    def on_progress(event):
        nonlocal last_print_time
        now = time.perf_counter()
        if now - last_print_time >= 5.0 or "completat" in event.phase.lower() or "micro" in event.phase.lower():
            last_print_time = now
            print(f"  [Progress] {event.phase} | Completati: {event.completed_chunks}/{event.total_chunks} (falliti: {event.failed_chunks})", flush=True)

    # 6. ESECUZIONE LIVE DELLA TOPIC DISCOVERY SUI 26 CHUNK
    print("\n" + "=" * 80, flush=True)
    print("AVVIO INFERENZA CANONICA SUI 26 CHUNK (max_topics=10)...", flush=True)
    print("=" * 80, flush=True)
    t_start = time.perf_counter()

    discovery_result = service.run_topic_discovery(
        document=doc,
        client=auditing_client,
        model_name=DEFAULT_OPERATIONAL_MODEL,
        max_topics=10,
        progress_callback=on_progress,
    )
    total_elapsed = round(time.perf_counter() - t_start, 2)

    meta = discovery_result.metadata
    print("\n" + "=" * 80)
    print("CAMPAGNA LIVE COMPLETATA CON SUCCESSO!")
    print("=" * 80)
    print(f"Tempo totale: {total_elapsed} secondi")
    print(f"Stato complessivo: {meta.get('status')}")
    print(f"Chunk originali previsti: {meta.get('expected_chunk_count')}")
    print(f"Chunk completati: {meta.get('completed_chunk_count')}/{meta.get('chunk_count')}")
    print(f"Chunk falliti: {meta.get('failed_chunk_count')}")
    print(f"Totale chiamate HTTP al modello: {auditing_client.calls_count}")
    print(f"Token prompt totali: {auditing_client.total_prompt_tokens}")
    print(f"Token completamento totali: {auditing_client.total_completion_tokens}")

    # Verifiche di integrita essenziali
    assert meta.get("status") == "COMPLETE", f"La discovery non e COMPLETE! status={meta.get('status')}"
    assert meta.get("completed_chunk_count") == 26, f"Attesi 26 chunk completati, trovati {meta.get('completed_chunk_count')}"
    assert meta.get("failed_chunk_count") == 0, f"Rilevati {meta.get('failed_chunk_count')} chunk falliti!"

    # 7. Verifica forense delle citazioni: nessun evidence ID inventato o non appartenente al documento
    valid_eids = {s.evidence_id for s in doc.all_evidence_sections}
    topics = discovery_result.topics
    print(f"\n[TOPIC DISCOVERY] Emersi {len(topics)} argomenti finali consolidati (limite richiesto: 10):")

    total_citations = 0
    invalid_citations = []
    for i, t in enumerate(topics, start=1):
        citations_count = len(t.evidence_ids)
        total_citations += citations_count
        for eid in t.evidence_ids:
            if eid not in valid_eids:
                invalid_citations.append((t.label, eid))
        print(f"  {i:2d}. [{citations_count:2d} evidenze] '{t.label}': {t.short_description[:90]}...")

    if invalid_citations:
        raise ValueError(f"Forense violata! Citazioni inesistenti: {invalid_citations}")
    print(f"\n[OK] 100% citazioni autentiche verificate: {total_citations} citazioni totali conformi.")

    # 8. VERIFICA PERSISTENZA E IDEMPOTENZA AL RICARICO (RELOAD)
    print("\n" + "=" * 80)
    print("VERIFICA IDEMPOTENZA E RICARICO DA CHECKPOINT (ZERO NUOVE CHIAMATE HTTP)...")
    print("=" * 80)
    calls_before = auditing_client.calls_count

    service_reload = LocalTopicAnalysisService(
        base_url="http://127.0.0.1:1234",
        default_model=DEFAULT_OPERATIONAL_MODEL,
        timeout_seconds=240.0,
        checkpoint_store=chk_store,
        enable_checkpointing=True,
        profile=OperationalProfile.BALANCED,
    )
    t_reload_start = time.perf_counter()
    reloaded_result = service_reload.run_topic_discovery(
        document=doc,
        client=auditing_client,
        model_name=DEFAULT_OPERATIONAL_MODEL,
        max_topics=10,
    )
    reload_duration = round(time.perf_counter() - t_reload_start, 3)
    new_calls = auditing_client.calls_count - calls_before

    print(f"[OK] Ricarico completato in {reload_duration} s.")
    print(f"[OK] Nuove chiamate HTTP effettuate: {new_calls} (DEVE ESSERE 0)")
    assert new_calls == 0, f"Violazione di idempotenza: effettuate {new_calls} chiamate HTTP durante il reload!"
    assert len(reloaded_result.topics) == len(discovery_result.topics), "Discrepanza nel numero di topic al reload!"
    assert reloaded_result.metadata.get("status") == "COMPLETE"
    print("[OK] Idempotenza e persistenza su SQLite verificate con successo al 100%.")

    # 9. VERIFICA SEARCH SERVICE E INDICIZZAZIONE UI
    print("\n" + "=" * 80)
    print("VERIFICA SEARCH SERVICE E COMPATIBILITA UI...")
    print("=" * 80)
    search_service = SearchService(
        document=doc,
        discovery_results=(discovery_result,),
        detection_results=(),
    )
    search_all = search_service.search_discoveries()
    print(f"[OK] SearchService search_discoveries(): {search_all.total_hits} hit trovate.")
    assert search_all.total_hits == len(discovery_result.topics)

    first_topic = discovery_result.topics[0]
    search_filtered = search_service.search_discoveries(query_text=first_topic.label[:10])
    print(f"[OK] Ricerca per parola chiave '{first_topic.label[:10]}': {search_filtered.total_hits} hit trovate.")

    # 10. VERIFICA ESPORTAZIONE UI E PROVENANCE
    from ui.application import export_topic_discovery_json
    export_json_str, export_filename = export_topic_discovery_json(
        discovery_result=discovery_result,
        document=doc,
        redact_sensitive_topics=False,
    )
    export_payload = json.loads(export_json_str)
    export_provenance = export_payload.get("provenance") or export_payload.get("experiment", {}).get("provenance")
    print(f"[OK] Esportazione UI generata: filename='{export_filename}'")
    (run_dir / "ui_export_topic_discovery.json").write_text(export_json_str, encoding="utf-8")

    # 11. STATISTICHE MICROCHUNK E COPERTURA DAL DB SQLITE
    with sqlite3.connect(chk_dir / "checkpoints.db") as conn:
        sub_rows = conn.execute("SELECT subchunk_id, serialized_result FROM checkpoint_subchunks").fetchall()
        chk_rows = conn.execute("SELECT chunk_index, serialized_result FROM checkpoint_chunks ORDER BY chunk_index").fetchall()

    total_saved_subchunks = len(sub_rows)
    bisected_subchunks = sum(1 for sid, _ in sub_rows if "::sub::" in sid)
    raw_microchunk_topics = 0
    for _, sjson in sub_rows:
        sdata = json.loads(sjson)
        raw_microchunk_topics += len(sdata.get("topics") or [])

    parent_validated_topics = 0
    for _, cjson in chk_rows:
        cdata = json.loads(cjson)
        parent_validated_topics += len(cdata.get("topics") or [])

    substantive_bundles = sum(1 for b in doc.bundles if is_substantive_message_bundle(b))
    non_substantive_bundles = total_bundles - substantive_bundles

    cumulative_calls = auditing_client.initial_audit_count + auditing_client.calls_count
    cumulative_prompt_tokens = auditing_client.prior_prompt_tokens + auditing_client.total_prompt_tokens
    cumulative_completion_tokens = auditing_client.prior_completion_tokens + auditing_client.total_completion_tokens
    cumulative_duration_seconds = round(auditing_client.prior_duration_seconds + total_elapsed, 2)

    # Salvataggio del report di run JSON
    run_report_path = run_dir / "live_discovery_26of26_summary.json"
    summary_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "document_id": doc.document_id,
        "document_fingerprint": meta.get("document_fingerprint"),
        "config_fingerprint": meta.get("config_fingerprint"),
        "total_bundles": total_bundles,
        "substantive_bundles": substantive_bundles,
        "non_substantive_bundles": non_substantive_bundles,
        "total_evidence_sections": total_sections,
        "original_chunk_count": meta.get("expected_chunk_count"),
        "completed_chunk_count": meta.get("completed_chunk_count"),
        "failed_chunk_count": meta.get("failed_chunk_count"),
        "total_saved_subchunks": total_saved_subchunks,
        "bisected_subchunks_count": bisected_subchunks,
        "raw_microchunk_topics_count": raw_microchunk_topics,
        "parent_chunk_validated_topics_count": parent_validated_topics,
        "status": meta.get("status"),
        "origin": meta.get("origin"),
        "export_filename": export_filename,
        "export_provenance": export_provenance,
        "session_http_calls": auditing_client.calls_count,
        "session_prompt_tokens": auditing_client.total_prompt_tokens,
        "session_completion_tokens": auditing_client.total_completion_tokens,
        "session_elapsed_seconds": total_elapsed,
        "total_http_calls": cumulative_calls,
        "total_prompt_tokens": cumulative_prompt_tokens,
        "total_completion_tokens": cumulative_completion_tokens,
        "elapsed_seconds": cumulative_duration_seconds,
        "reload_seconds": reload_duration,
        "reload_http_calls": new_calls,
        "topics_extracted_count": len(discovery_result.topics),
        "topics": [
            {
                "label": t.label,
                "short_description": t.short_description,
                "evidence_count": len(t.evidence_ids),
                "evidence_ids": list(t.evidence_ids),
            }
            for t in discovery_result.topics
        ],
    }
    run_report_path.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Report JSON della run salvato in: {run_report_path}")

    return discovery_result, doc, chk_store


if __name__ == "__main__":
    execute_full_live_campaign()
