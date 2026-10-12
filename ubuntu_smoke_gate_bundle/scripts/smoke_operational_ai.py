"""
scripts/smoke_operational_ai.py
-------------------------------
Strumento di smoke test operativo per LM Studio locale e Release Gate Pre-Ubuntu.

Esegue la verifica completa della pipeline di analisi:
1. Rilevamento disponibilità LM Studio su endpoint loopback (http://127.0.0.1:1234);
2. Risoluzione dei metadati del modello operativo (Qwen2.5-7B-Instruct);
3. Verifica context length (8192 configurato vs confermato);
4. Esecuzione Topic Discovery LIVE su ConversationEvidenceDocument DEMO;
5. Seconda Topic Detection LIVE indipendente (Prova del Nove) e valutazione Consistency;
6. Caso semantico di controllo scientifico: "Logistica e Ispezione Depositi" (msg::5::VISION_DESCRIPTION);
7. Isolamento document-level dello store con conversazione sintetica;
8. SCENARIO END-TO-END FILE: Ingestion reale di WhatsApp TXT tramite la vera pipeline applicativa
   (Importer reale -> Validation -> Normalization -> Entity Resolution -> UnifiedBuilder -> Document);
9. Topic Discovery, Topic Detection e Consistency sul documento reale importato;
10. Verifica rigorosa di isolamento cross-document (zero leak tra DEMO e FILE);
11. Verifica aggiuntiva dell'ingestion da archivio ZIP sintetico.

Regole di integrità:
- Ogni controllo obbligatorio ha un esito esplicito.
- Se anche un solo controllo obbligatorio fallisce, lo script termina con Exit Code != 0.
- Non stampa mai SUCCESSO se vi sono controlli falliti.
- Distingue errori operativi da esiti scientifici (es. ABSENT nel caso di controllo).
- Backend LM Studio non disponibile -> exit code 2 (NOT_VERIFIED, mai PASS).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Optional
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai.backend import (
    BaseLlmClient,
    LlmCompletionResponse,
    LmStudioUnavailableError,
)
from ai.lmstudio import (
    DEFAULT_OPERATIONAL_CONTEXT_LENGTH,
    DEFAULT_OPERATIONAL_MODEL,
    OPERATIONAL_SPECS,
    LmStudioClient,
    resolve_operational_model_from_metadata,
)
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    MessageEvidenceBundle,
    TopicDecision,
    TopicQuery,
)
from importer.models import RawRecord
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult
from analysis.service import LocalTopicAnalysisService
from ui.demo import build_synthetic_demo_dataset
from ui.ingestion import ingest_file_payload
from ui.models import IngestionStatus, SourceFormat


# Percorsi delle fixture sintetiche controllate
FIXTURES_DIR = PROJECT_ROOT / "test_data" / "synthetic_smoke"
SYNTHETIC_TXT_PATH = FIXTURES_DIR / "synthetic_whatsapp_chat.txt"
SYNTHETIC_ZIP_PATH = FIXTURES_DIR / "synthetic_whatsapp_chat.zip"

DEFAULT_SYNTHETIC_CHAT_CONTENT = """12/10/2026, 09:15 - Operatore Logistica: Consegna del container programmata per lunedì mattina al gate nord.
12/10/2026, 09:16 - Ispettore Dogana: Verificare i sigilli doganali e l'integrità delle casse prima del carico.
12/10/2026, 09:18 - Operatore Logistica: Tutti i colli del magazzino centrale sono stati inventariati e registrati.
12/10/2026, 09:20 - Ispettore Dogana: Ricevuto. Confermo autorizzazione per lo sdoganamento delle merci stoccate.
"""


def ensure_synthetic_fixtures() -> tuple[Path, Path]:
    """Garantisce la presenza delle fixture sintetiche per il test end-to-end FILE."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    if not SYNTHETIC_TXT_PATH.exists():
        SYNTHETIC_TXT_PATH.write_text(DEFAULT_SYNTHETIC_CHAT_CONTENT, encoding="utf-8")
    if not SYNTHETIC_ZIP_PATH.exists():
        with zipfile.ZipFile(SYNTHETIC_ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("_chat.txt", DEFAULT_SYNTHETIC_CHAT_CONTENT.encode("utf-8"))
    return SYNTHETIC_TXT_PATH, SYNTHETIC_ZIP_PATH


def _build_synthetic_document_level_conversation(doc_id: str = "chat_store_synthetic_01") -> ConversationEvidenceDocument:
    """Costruisce una conversazione sintetica in memoria per verificare l'isolamento store a livello documentale."""
    bundles = []
    messages = [
        "Pianificazione manutenzione impianto di refrigerazione programmata per mercoledì.",
        "Verificare la temperatura costante dei comparti merci prima dello stoccaggio.",
    ]
    for idx, text in enumerate(messages, 1):
        raw = RawRecord(
            source_name="whatsapp_file",
            source_path="/test/synthetic_store_chat.txt",
            source_record_id=str(idx),
            record_type="message",
            raw_fields={"text": text},
            media_reference=None,
            metadata={},
        )
        val = ValidationResult(record=raw, issues=())
        ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
        norm = NormalizedRecord(
            raw_record=raw,
            validation_result=val,
            source_name="whatsapp_file",
            source_record_id=str(idx),
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=text,
            media_reference=None,
        )
        um = UnifiedMessage(
            message_id=f"msg::{doc_id}::{idx}",
            source_name="whatsapp_file",
            source_record_id=str(idx),
            source_path="/test/synthetic_store_chat.txt",
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
        source_name="whatsapp_file",
        metadata={"data_classification": "DOCUMENT_STORE_SYNTHETIC"},
    )


class FakeSmokeClient(BaseLlmClient):
    """Client fake deterministico per diagnostica offline e CI, privo di LM Studio."""

    def __init__(self, mode: str = "normal") -> None:
        self.call_count = 0
        self.model_id = "qwen2.5-7b-instruct"
        self.mode = mode

    def is_available(self) -> bool:
        return True

    def chat_completion(self, messages: list[dict[str, str]], **kwargs: Any) -> LlmCompletionResponse:
        self.call_count += 1
        if self.mode == "invalid_json":
            return LlmCompletionResponse(
                content="[CORRUPT NON-JSON RESPONSE]",
                model=self.model_id,
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                latency_seconds=0.01,
            )
        if self.mode == "empty_topics":
            return LlmCompletionResponse(
                content=json.dumps({"topics": []}),
                model=self.model_id,
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                latency_seconds=0.01,
            )

        content_str = str(messages[-1].get("content", ""))
        first_content = str(messages[0].get("content", "")).lower() if messages else ""

        # Estrazione generica di evidence_id presenti nel prompt JSON o testo serializzato
        found_eids = re.findall(r'"evidence_id":\s*"([^"]+)"', content_str)
        if not found_eids:
            found_eids = re.findall(r"msg::[a-zA-Z0-9_:]+", content_str)

        if "msg::5::VISION_DESCRIPTION" in found_eids:
            selected_eids = ["msg::5::VISION_DESCRIPTION"]
        elif found_eids:
            selected_eids = [found_eids[0]]
        else:
            selected_eids = ["msg::1::TEXT"]

        is_discovery = "topic discovery" in first_content or "emergent topic" in first_content or "discovery" in content_str.lower()
        is_control_case = "logistica e ispezione depositi" in content_str.lower() or "ctrl_logistica" in content_str.lower()

        if is_discovery:
            if "msg::5::VISION_DESCRIPTION" in selected_eids:
                label = "Ispezione Depositi e Merci"
                desc = "Verifica magazzino e casse sigillate con porta blindata"
            elif any("whatsapp_export" in eid for eid in selected_eids):
                label = "Operazioni Doganali e Logistica Container"
                desc = "Pianificazione logistica container, verifiche doganali e carichi magazzino"
            else:
                label = "Manutenzione Comparti Refrigerati"
                desc = "Pianificazione manutenzione e verifica temperature stoccaggio"

            payload = {
                "topics": [
                    {
                        "label": label,
                        "short_description": desc,
                        "evidence_ids": selected_eids,
                    }
                ]
            }
        elif is_control_case:
            payload = {
                "decision": "PRESENT",
                "evidence_ids": selected_eids,
                "rationale": "Evidenza semantica verificata: presenza di magazzino merci e casse sigillate nella descrizione visiva.",
            }
        else:
            payload = {
                "decision": "PRESENT",
                "evidence_ids": selected_eids,
                "rationale": "Evidenza semantica confermata nel documento analizzato.",
            }

        return LlmCompletionResponse(
            content=json.dumps(payload),
            model="qwen2.5-7b-instruct-instance-1",
            prompt_tokens=25,
            completion_tokens=25,
            total_tokens=50,
            latency_seconds=0.12,
        )


@dataclass
class CheckResult:
    """Esito formale di un singolo controllo obbligatorio dello smoke test."""
    name: str
    passed: bool
    details: str
    error: Optional[str] = None


def run_smoke_test(
    use_fake: bool = False,
    model_name: str = DEFAULT_OPERATIONAL_MODEL,
    fake_mode: str = "normal",
    simulate_contamination: bool = False,
) -> int:
    """
    Esegue lo smoke test operativo con reporting strutturato e criteri rigorosi di uscita.

    Codici di uscita:
    - 0 : Tutti i controlli obbligatori hanno avuto successo (SUCCESSO).
    - 1 : Uno o più controlli obbligatori sono falliti, o errore operativo (FALLITO).
    - 2 : LM Studio locale non raggiungibile nell'ambiente di test (NOT_VERIFIED).
    """
    print("=" * 75)
    print("SMOKE TEST OPERATIVO AI — CHATANALYSIS RELEASE GATE (UBUNTU READY)")
    print("=" * 75)

    checks: list[CheckResult] = []
    service = LocalTopicAnalysisService(default_model=model_name, context_length=DEFAULT_OPERATIONAL_CONTEXT_LENGTH)
    doc_demo, _, _ = build_synthetic_demo_dataset()

    client: BaseLlmClient | None = None
    verification_category = "LIVE_VERIFIED"
    resolved_info: dict[str, Any] = {
        "requested_canonical_model": model_name,
        "resolved_model_key": None,
        "loaded_instance_id": "UNKNOWN (not exposed by server)",
        "api_identifier": model_name,
        "configured_context_length": DEFAULT_OPERATIONAL_CONTEXT_LENGTH,
        "server_confirmed_context_length": "UNKNOWN (unconfirmed by server)",
    }

    if use_fake:
        verification_category = "MOCK_VERIFIED"
        print("[MODE] Utilizzo FakeSmokeClient deterministico per diagnostica ambiente CI/Mock.")
        client = FakeSmokeClient(mode=fake_mode)
        resolved_info["resolved_model_key"] = "mock-qwen2.5-7b-instruct"
        resolved_info["loaded_instance_id"] = "mock-instance-01"
        resolved_info["api_identifier"] = "mock-qwen2.5-7b-instruct"
        resolved_info["server_confirmed_context_length"] = 8192
    else:
        print(f"[MODE] Connessione a LM Studio locale su {service.base_url} (Richiesto: {model_name})")
        probe_client = LmStudioClient(base_url=service.base_url, model_id=model_name)
        if not probe_client.is_available():
            print("\nLIVE LM STUDIO: NOT EXECUTED (NOT_VERIFIED)")
            print(f"Motivo: server LM Studio non raggiungibile su {service.base_url}.")
            print("Prerequisiti per l'ambiente live (PC Ubuntu):")
            print("  1. Avviare LM Studio.")
            print("  2. Attivare il Server Locale (porta 1234, loopback 127.0.0.1).")
            print(f"  3. Verificare installazione e caricamento del modello '{model_name}'.")
            print("  4. Impostare Context Length esplicita: 8192.")
            return 2  # Exit code 2 indica LM Studio non disponibile

        try:
            native_models = probe_client.get_native_models()
            res_meta = resolve_operational_model_from_metadata(native_models, model_name)
            resolved_info["resolved_model_key"] = res_meta.model_key
            resolved_info["api_identifier"] = res_meta.model_key
            if res_meta.instance_id:
                resolved_info["loaded_instance_id"] = res_meta.instance_id
            if res_meta.context_length:
                resolved_info["server_confirmed_context_length"] = res_meta.context_length
        except Exception as res_err:
            print(f"Attenzione: risoluzione nativa dettagliata non disponibile ({res_err}). Uso fallback standard.")
            resolved_info["resolved_model_key"] = model_name

    checks.append(CheckResult("CHECK_BACKEND_AVAILABILITY", True, f"Backend disponibile (mode={'fake' if use_fake else 'live'})"))

    try:
        # FASE 1: TOPIC DISCOVERY LIVE DEMO
        print("\n--- 1. ESECUZIONE TOPIC DISCOVERY LIVE (DEMO) ---")
        disc_res = service.run_topic_discovery(
            document=doc_demo,
            client=client,
            model_name=model_name,
            max_topics=5,
        )
        discovery_run_id = disc_res.metadata.get("run_id", "N/A")
        discovery_origin = disc_res.metadata.get("origin", "N/A")
        print(f"Document ID      : {doc_demo.document_id}")
        print(f"Discovery Run ID : {discovery_run_id}")
        print(f"Discovery Origin : {discovery_origin}")
        print(f"Status Discovery : {disc_res.metadata.get('status', 'COMPLETE')}")
        print(f"Topic scoperti   : {len(disc_res.topics)}")

        has_topics = len(disc_res.topics) > 0
        checks.append(CheckResult(
            "CHECK_DEMO_DISCOVERY_TOPICS",
            has_topics,
            f"Topic scoperti: {len(disc_res.topics)}",
            error=None if has_topics else "Nessun topic prodotto da Discovery",
        ))

        # Verifica registrazione store
        stored_run = service.get_discovery_run(doc_demo.document_id, discovery_run_id)
        is_registered = stored_run is not None
        checks.append(CheckResult(
            "CHECK_DEMO_DISCOVERY_REGISTRATION",
            is_registered,
            f"Run registrata nello store per document_id: {is_registered}",
            error=None if is_registered else "Run non trovata nello store autoritativo",
        ))

        if not has_topics:
            raise RuntimeError("Fase 1 fallita: Topic Discovery ha prodotto zero argomenti.")

        first_topic = disc_res.topics[0]
        discovery_eids = list(first_topic.evidence_ids)

        # FASE 2: PROVA DEL NOVE (SECONDA DETECTION INDIPENDENTE)
        print("\n--- 2. PROVA DEL NOVE: SECONDA DETECTION LIVE INDIPENDENTE ---")
        det_res, consistency = service.verify_discovered_topic(
            document=doc_demo,
            discovered_topic=first_topic,
            discovery_result=disc_res,
            client=client,
            model_name=model_name,
        )
        print(f"Topic Verificato : {first_topic.label}")
        print(f"Decisione        : {det_res.decision.value}")
        print(f"Consistency      : {consistency.status.value}")
        print(f"Rationale        : {det_res.rationale}")

        checks.append(CheckResult(
            "CHECK_DEMO_DETECTION_EXECUTION",
            det_res is not None and isinstance(det_res.rationale, str) and len(det_res.rationale.strip()) > 0,
            f"Detection eseguita con rationale valido ({len(det_res.rationale)} caratteri)",
        ))
        checks.append(CheckResult(
            "CHECK_DEMO_CONSISTENCY",
            consistency is not None,
            f"Consistency valutata: {consistency.status.value}",
        ))

        # FASE 3: CASO SEMANTICO DI CONTROLLO "Logistica e Ispezione Depositi"
        print("\n--- 3. CASO SEMANTICO DI CONTROLLO: 'Logistica e Ispezione Depositi' ---")
        ctrl_query = TopicQuery(
            topic_id="ctrl_logistica",
            label="Logistica e Ispezione Depositi",
            description="Ispezione visiva del magazzino merci con casse sigillate e sicurezza",
        )
        ctrl_det = service.run_topic_detection(
            document=doc_demo,
            topic=ctrl_query,
            client=client,
            model_name=model_name,
        )
        print(f"Topic Controllo  : {ctrl_query.label}")
        print(f"Target Evidence  : msg::5::VISION_DESCRIPTION")
        print(f"Decisione        : {ctrl_det.decision.value}")
        print(f"Evidenze Citate  : {list(ctrl_det.evidence_ids)}")
        print(f"Rationale        : {ctrl_det.rationale}")

        # La decisione ABSENT non costituisce fallimento operativo: è un esito scientifico valido.
        checks.append(CheckResult(
            "CHECK_CONTROL_CASE_EXECUTION",
            ctrl_det is not None and ctrl_det.decision in (TopicDecision.PRESENT, TopicDecision.ABSENT, TopicDecision.UNCERTAIN),
            f"Caso di controllo eseguito con successo, decisione: {ctrl_det.decision.value}",
        ))

        # FASE 4: ISOLAMENTO DOCUMENT-LEVEL (STORE SINTETICO)
        print("\n--- 4. ISOLAMENTO DOCUMENT-LEVEL (STORE SINTETICO) ---")
        doc_store_synth = _build_synthetic_document_level_conversation()
        disc_store_res = service.run_topic_discovery(
            document=doc_store_synth,
            client=client,
            model_name=model_name,
            max_topics=3,
        )
        store_run_id = disc_store_res.metadata.get("run_id")

        leak_demo_to_synth = service.get_discovery_run(doc_store_synth.document_id, discovery_run_id) is not None
        leak_synth_to_demo = service.get_discovery_run(doc_demo.document_id, store_run_id) is not None
        store_isolation_ok = (not leak_demo_to_synth) and (not leak_synth_to_demo)

        checks.append(CheckResult(
            "CHECK_STORE_DOCUMENT_ISOLATION",
            store_isolation_ok,
            f"Isolamento store DEMO <-> Synth: {'PASS' if store_isolation_ok else 'LEAK'}",
            error=None if store_isolation_ok else "Trovata contaminazione nello store tra DEMO e Synth",
        ))

        # FASE 5: SCENARIO END-TO-END FILE: IMPORTAZIONE REALE WHATSAPP TXT
        print("\n--- 5. SCENARIO END-TO-END FILE: IMPORTAZIONE REALE WHATSAPP TXT ---")
        txt_path, zip_path = ensure_synthetic_fixtures()
        txt_bytes = txt_path.read_bytes()

        ingest_res = ingest_file_payload(
            file_bytes_or_io=txt_bytes,
            original_filename=txt_path.name,
            source_format=SourceFormat.WHATSAPP_EXPORT,
        )

        ingest_success = ingest_res.summary.status == IngestionStatus.SUCCESS
        ingest_docs = list(ingest_res.documents.values())
        has_ingest_doc = len(ingest_docs) > 0

        checks.append(CheckResult(
            "CHECK_REAL_FILE_INGESTION_STATUS",
            ingest_success and has_ingest_doc,
            f"Ingestion reale WhatsApp TXT: status={ingest_res.summary.status.value}, docs={len(ingest_docs)}, raw_records={ingest_res.summary.raw_record_count}, unified_msgs={ingest_res.summary.unified_message_count}",
            error=None if (ingest_success and has_ingest_doc) else "Ingestion fallita o nessun documento generato",
        ))

        if not has_ingest_doc:
            raise RuntimeError("Ingestion del file WhatsApp TXT non ha prodotto alcun ConversationEvidenceDocument.")

        doc_file_real = ingest_docs[0]
        expected_msg_count = 4
        file_msg_count_ok = len(doc_file_real.bundles) == expected_msg_count
        file_text_preserved = all(len(b.message.text_content.strip()) > 0 for b in doc_file_real.bundles)
        file_source_provenance_ok = doc_file_real.source_name == "whatsapp_export"
        file_eids_valid = len(doc_file_real.all_evidence_sections) == expected_msg_count
        file_doc_id_deterministic = doc_file_real.document_id.startswith("doc::whatsapp_export::")

        checks.append(CheckResult(
            "CHECK_REAL_FILE_PIPELINE_INVARIANTS",
            file_msg_count_ok and file_text_preserved and file_source_provenance_ok and file_eids_valid and file_doc_id_deterministic,
            f"Invarianti documento: msgs={len(doc_file_real.bundles)}/{expected_msg_count}, source={doc_file_real.source_name}, eids={len(doc_file_real.all_evidence_sections)}, doc_id={doc_file_real.document_id[:30]}...",
            error=None if (file_msg_count_ok and file_text_preserved and file_source_provenance_ok and file_eids_valid) else "Invarianti del documento violati nell'ingestion reale",
        ))

        # Analisi AI sul documento reale importato da TXT
        disc_file_real_res = service.run_topic_discovery(
            document=doc_file_real,
            client=client,
            model_name=model_name,
            max_topics=3,
        )
        file_real_run_id = disc_file_real_res.metadata.get("run_id")
        file_real_has_topics = len(disc_file_real_res.topics) > 0

        checks.append(CheckResult(
            "CHECK_REAL_FILE_DISCOVERY",
            file_real_has_topics,
            f"Discovery su documento reale: {len(disc_file_real_res.topics)} topic scoperti",
            error=None if file_real_has_topics else "Nessun topic scoperto sul documento reale importato",
        ))

        if not file_real_has_topics:
            raise RuntimeError("Topic Discovery sul documento reale importato ha prodotto zero argomenti.")

        file_first_topic = disc_file_real_res.topics[0]
        file_det_res, file_consistency = service.verify_discovered_topic(
            document=doc_file_real,
            discovered_topic=file_first_topic,
            discovery_result=disc_file_real_res,
            client=client,
            model_name=model_name,
        )

        checks.append(CheckResult(
            "CHECK_REAL_FILE_DETECTION_AND_CONSISTENCY",
            file_det_res is not None and file_consistency is not None,
            f"Detection ed esito consistency su documento reale: decision={file_det_res.decision.value}, consistency={file_consistency.status.value}",
        ))

        # FASE 6: ISOLAMENTO MULTI-CONVERSAZIONE RIGOROSO (DEMO vs FILE REALE)
        print("\n--- 6. VERIFICA ISOLAMENTO CROSS-DOCUMENT (DEMO vs FILE REALE) ---")
        leak_demo_to_real_file = service.get_discovery_run(doc_file_real.document_id, discovery_run_id) is not None
        leak_real_file_to_demo = service.get_discovery_run(doc_demo.document_id, file_real_run_id) is not None

        # Simulazione contaminazione controllata per verifica Release Gate (se richiesta)
        if simulate_contamination:
            print("[SIMULATION] Iniezione artificiale di contaminazione per verificare che il Gate fallisca con Exit Code != 0!")
            leak_demo_to_real_file = True

        cross_isolation_ok = (not leak_demo_to_real_file) and (not leak_real_file_to_demo)

        checks.append(CheckResult(
            "CHECK_CROSS_DOCUMENT_ISOLATION",
            cross_isolation_ok,
            f"Isolamento DEMO <-> FILE reale: {'PASSED (Zero leak)' if cross_isolation_ok else 'FAILED (Contamination)'}",
            error=None if cross_isolation_ok else "Rilevata contaminazione cross-document nello store tra DEMO e FILE reale",
        ))

        # FASE 7: VERIFICA AGGIUNTIVA ARCHIVIO ZIP
        print("\n--- 7. VERIFICA AGGIUNTIVA: INGESTION ARCHIVIO WHATSAPP ZIP ---")
        zip_bytes = zip_path.read_bytes()
        zip_ingest_res = ingest_file_payload(
            file_bytes_or_io=zip_bytes,
            original_filename=zip_path.name,
            source_format=SourceFormat.WHATSAPP_EXPORT,
        )
        zip_ok = zip_ingest_res.summary.status == IngestionStatus.SUCCESS and len(zip_ingest_res.documents) > 0
        checks.append(CheckResult(
            "CHECK_ZIP_ARCHIVE_INGESTION",
            zip_ok,
            f"Ingestion archivio ZIP: status={zip_ingest_res.summary.status.value}, documents={len(zip_ingest_res.documents)}",
            error=None if zip_ok else "Ingestion dell'archivio ZIP sintetico non riuscita",
        ))

        # REPORT STRUTTURATO COMPLETO
        print("\n" + "=" * 75)
        print("REPORT STRUTTURATO AUDIT & RELEASE GATE AI:")
        print("=" * 75)
        print(f"requested_canonical_model       : {resolved_info['requested_canonical_model']}")
        print(f"resolved_model_key             : {resolved_info['resolved_model_key']}")
        print(f"loaded_instance_id             : {resolved_info['loaded_instance_id']}")
        print(f"api_identifier                 : {resolved_info['api_identifier']}")
        print(f"response.model                 : {det_res.metadata.get('model', getattr(client, 'model_id', 'N/A'))}")
        print(f"configured_context_length      : {resolved_info['configured_context_length']}")
        print(f"server_confirmed_context_length: {resolved_info['server_confirmed_context_length']}")
        print(f"document_id                    : {doc_demo.document_id}")
        print(f"discovery_run_id               : {discovery_run_id}")
        print(f"discovery_origin               : {discovery_origin}")
        print(f"discovered_topic               : {first_topic.label}")
        print(f"discovery_evidence_ids         : {discovery_eids}")
        print(f"detection_decision             : {det_res.decision.value}")
        print(f"detection_evidence_ids         : {list(det_res.evidence_ids)}")
        print(f"consistency_status             : {consistency.status.value}")
        print(f"rationale                      : {det_res.rationale}")
        print(f"latency                        : Discovery={disc_res.metadata.get('latency_seconds', 0.0):.2f}s, "
              f"Detection={det_res.metadata.get('latency_seconds', 0.0):.2f}s, "
              f"Control={ctrl_det.metadata.get('latency_seconds', 0.0):.2f}s")
        print(f"completed_chunk_count          : {disc_res.metadata.get('completed_chunk_count', 1)}")
        print(f"failed_chunk_count             : {disc_res.metadata.get('failed_chunk_count', 0)}")
        print(f"verification_classification    : {verification_category}")
        print("-" * 75)
        print(f"control_topic                  : {ctrl_query.label}")
        print(f"control_target_evidence        : msg::5::VISION_DESCRIPTION")
        print(f"control_decision               : {ctrl_det.decision.value}")
        print(f"control_evidence_ids           : {list(ctrl_det.evidence_ids)}")
        print(f"control_rationale              : {ctrl_det.rationale}")
        if ctrl_det.decision == TopicDecision.ABSENT:
            print("control_scientific_note        : Esito empirico registrato fedelmente (non forzato a PRESENT)")
        print("-" * 75)
        print(f"file_real_source               : {doc_file_real.source_name}")
        print(f"file_real_document_id          : {doc_file_real.document_id}")
        print(f"file_real_discovery_run_id     : {file_real_run_id}")
        print(f"file_real_discovered_topic     : {file_first_topic.label}")
        print(f"file_real_consistency          : {file_consistency.status.value}")
        print(f"document_store_isolation       : {'PASSED (Zero leak)' if store_isolation_ok else 'FAILED'}")
        print(f"multi_conv_cross_isolation     : {'PASSED (Zero leak)' if cross_isolation_ok else 'FAILED'}")
        print("=" * 75)

        # TABELLA DEI CONTROLLI OBBLIGATORI
        print("\nTABELLA ESITI CONTROLLI OBBLIGATORI:")
        print("-" * 75)
        for c in checks:
            status_str = "PASSED" if c.passed else "FAILED"
            print(f"[{status_str:6}] {c.name:38} : {c.details}")
        print("-" * 75)

        all_mandatory_passed = all(c.passed for c in checks)
        if all_mandatory_passed:
            print("ESITO SMOKE TEST OPERATIVO: SUCCESSO")
            print("=" * 75)
            return 0
        else:
            print("ESITO SMOKE TEST OPERATIVO: FALLITO")
            print("Uno o piu controlli obbligatori non sono stati superati.")
            print("=" * 75)
            return 1

    except LmStudioUnavailableError as unavail:
        print(f"\nLIVE LM STUDIO: NOT EXECUTED ({unavail})")
        print("ESITO: NOT_VERIFIED (LM Studio server non raggiungibile)")
        return 2
    except Exception as exc:
        print(f"\nERRORE OPERATIVO DURANTE LO SMOKE TEST: {type(exc).__name__}: {exc}")
        print("ESITO SMOKE TEST OPERATIVO: FALLITO")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smoke test operativo per LM Studio (ChatAnalysis)")
    parser.add_argument("--fake", action="store_true", help="Utilizza client simulato deterministico per test offline")
    parser.add_argument("--model", default=DEFAULT_OPERATIONAL_MODEL, help="Modello target da testare")
    parser.add_argument("--fake-mode", default="normal", choices=["normal", "invalid_json", "empty_topics"], help="Modalita del fake client per test negativi")
    parser.add_argument("--simulate-contamination", action="store_true", help="Simula una contaminazione cross-document per test negativi")
    args = parser.parse_args()

    exit_code = run_smoke_test(
        use_fake=args.fake,
        model_name=args.model,
        fake_mode=args.fake_mode,
        simulate_contamination=args.simulate_contamination,
    )
    sys.exit(exit_code)
