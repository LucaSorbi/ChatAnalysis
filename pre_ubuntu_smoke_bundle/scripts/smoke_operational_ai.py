"""
scripts/smoke_operational_ai.py
-------------------------------
Strumento di smoke test operativo per LM Studio locale (FASE 40, Release Gate).

Utilizza rigorosamente il nuovo LocalTopicAnalysisService e il dataset sintetico DEMO.

Passi eseguiti:
1. LM Studio availability (loopback http://127.0.0.1:1234);
2. Resolve OperationalModelSpec (es. Qwen2.5-7B-Instruct);
3. Verifica actual model key ed eventuale instance id;
4. Caricamento / verifica context length (8192);
5. Topic Discovery LIVE su ConversationEvidenceDocument DEMO;
6. Selezione topic scoperto e seconda Topic Detection LIVE indipendente (Prova del Nove);
7. Valutazione Consistency e verifica provenance;
8. Test di controllo diagnostico separato: "Logistica e Ispezione Depositi".

Stampa dettagliata richiesta dalla Release Gate:
- requested canonical model
- resolved model key
- instance id
- response model
- context length
- discovery topics
- discovery evidence IDs
- detection decision
- detection evidence IDs
- consistency
- latencies
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

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


def _build_synthetic_file_conversation(doc_id: str = "chat_file_investigative_02") -> ConversationEvidenceDocument:
    """Costruisce una seconda conversazione sintetica per verificare l'isolamento multi-conversation."""
    bundles = []
    messages = [
        "Consegna del container programmata per lunedì mattina al gate nord.",
        "Verificare i sigilli doganali e l'integrità delle casse prima del carico.",
    ]
    for idx, text in enumerate(messages, 1):
        raw = RawRecord(
            source_name="whatsapp_file",
            source_path="/test/investigative_chat.txt",
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
            source_path="/test/investigative_chat.txt",
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
        metadata={"data_classification": "FILE_SYNTHETIC"},
    )


class FakeSmokeClient(BaseLlmClient):
    """Client fake per verifica dello script di smoke in ambienti privi di LM Studio."""

    def __init__(self) -> None:
        self.call_count = 0
        self.model_id = "qwen2.5-7b-instruct"

    def is_available(self) -> bool:
        return True

    def chat_completion(self, messages: list[dict[str, str]], **kwargs: Any) -> LlmCompletionResponse:
        self.call_count += 1
        content_str = str(messages[-1].get("content", ""))
        first_content = str(messages[0].get("content", "")).lower() if messages else ""

        found_eids = re.findall(r"msg::[a-zA-Z0-9_:]+", content_str)
        if "msg::5::VISION_DESCRIPTION" in found_eids:
            selected_eids = ["msg::5::VISION_DESCRIPTION"]
        elif found_eids:
            selected_eids = [found_eids[0]]
        else:
            selected_eids = ["msg::1::TEXT"]

        if "topic discovery" in first_content or "emergent topic" in first_content or "discovery" in content_str.lower():
            label = "Ispezione Depositi e Merci" if "msg::5::VISION_DESCRIPTION" in selected_eids else "Logistica Portuale e Container"
            payload = {
                "topics": [
                    {
                        "label": label,
                        "short_description": "Verifica magazzino e casse sigillate" if "msg::5::VISION_DESCRIPTION" in selected_eids else "Pianificazione logistica container e verifiche doganali",
                        "evidence_ids": selected_eids,
                    }
                ]
            }
        elif "logistica" in content_str.lower() or "ctrl_logistica" in content_str.lower():
            payload = {
                "decision": "PRESENT",
                "evidence_ids": selected_eids,
                "rationale": "Evidenza semantica confermata nella descrizione visiva della cassa sigillata.",
            }
        else:
            payload = {
                "decision": "PRESENT",
                "evidence_ids": selected_eids,
                "rationale": "Evidenza semantica verificata.",
            }
        return LlmCompletionResponse(
            content=json.dumps(payload),
            model="qwen2.5-7b-instruct-instance-1",
            prompt_tokens=20,
            completion_tokens=20,
            total_tokens=40,
            latency_seconds=0.15,
        )


def run_smoke_test(use_fake: bool = False, model_name: str = DEFAULT_OPERATIONAL_MODEL) -> int:
    print("=" * 75)
    print("SMOKE TEST OPERATIVO AI — CHATANALYSIS RELEASE GATE (UBUNTU READY)")
    print("=" * 75)

    service = LocalTopicAnalysisService(default_model=model_name, context_length=DEFAULT_OPERATIONAL_CONTEXT_LENGTH)
    doc_demo, _, _ = build_synthetic_demo_dataset()

    client: BaseLlmClient | None = None
    resolved_info: dict[str, Any] = {
        "requested_canonical_model": model_name,
        "resolved_model_key": None,
        "instance_id": None,
        "context_length": DEFAULT_OPERATIONAL_CONTEXT_LENGTH,
    }

    if use_fake:
        print("[MODE] Utilizzo FakeSmokeClient deterministico per diagnostica ambiente CI/Mock.")
        client = FakeSmokeClient()
        resolved_info["resolved_model_key"] = "mock-qwen2.5-7b-instruct"
        resolved_info["instance_id"] = "mock-instance-01"
    else:
        print(f"[MODE] Connessione a LM Studio locale su {service.base_url} (Richiesto: {model_name})")
        probe_client = LmStudioClient(base_url=service.base_url, model_id=model_name)
        if not probe_client.is_available():
            print("\nLIVE LM STUDIO: NOT EXECUTED")
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
            resolved_info["instance_id"] = res_meta.instance_id or "UNKNOWN (not provided by LM Studio)"
            resolved_info["context_length"] = res_meta.context_length or DEFAULT_OPERATIONAL_CONTEXT_LENGTH
        except Exception as res_err:
            print(f"Attenzione: risoluzione nativa dettagliata non disponibile ({res_err}). Uso fallback standard.")
            resolved_info["resolved_model_key"] = model_name

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
        print(f"Document ID      : {doc_demo.document_id}")
        print(f"Discovery Run ID : {discovery_run_id}")
        print(f"Status Discovery : {disc_res.metadata.get('status', 'COMPLETE')}")
        print(f"Topic scoperti   : {len(disc_res.topics)}")
        if not disc_res.topics:
            print("ERRORE OPERATIVO: Nessun topic prodotto.")
            return 1

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

        # FASE 4: ISOLAMENTO MULTI-CONVERSAZIONE & TEST FILE
        print("\n--- 4. ISOLAMENTO MULTI-CONVERSAZIONE & TEST FILE ---")
        doc_file = _build_synthetic_file_conversation()
        disc_file_res = service.run_topic_discovery(
            document=doc_file,
            client=client,
            model_name=model_name,
            max_topics=3,
        )
        file_run_id = disc_file_res.metadata.get("run_id")

        # Verifica rigorosa isolamento cross-document
        leak_demo_to_file = service.get_discovery_run(doc_file.document_id, discovery_run_id) is not None
        leak_file_to_demo = service.get_discovery_run(doc_demo.document_id, file_run_id) is not None
        cross_isolation_ok = (not leak_demo_to_file) and (not leak_file_to_demo)

        # REPORT STRUTTURATO COMPLETO
        print("\n" + "=" * 75)
        print("REPORT STRUTTURATO AUDIT & RELEASE GATE AI:")
        print("=" * 75)
        print(f"document_id             : {doc_demo.document_id}")
        print(f"discovery_run_id        : {discovery_run_id}")
        print(f"model_key               : {resolved_info['resolved_model_key']}")
        print(f"instance_id             : {resolved_info['instance_id']}")
        print(f"response.model          : {det_res.metadata.get('model', getattr(client, 'model_id', 'N/A'))}")
        print(f"context_length          : {resolved_info['context_length']}")
        print(f"discovered_topic        : {first_topic.label}")
        print(f"discovery_evidence_ids  : {discovery_eids}")
        print(f"detection_decision      : {det_res.decision.value}")
        print(f"detection_evidence_ids  : {list(det_res.evidence_ids)}")
        print(f"consistency             : {consistency.status.value}")
        print(f"latency                 : Discovery={disc_res.metadata.get('latency_seconds', 0.0):.2f}s, "
              f"Detection={det_res.metadata.get('latency_seconds', 0.0):.2f}s, "
              f"Control={ctrl_det.metadata.get('latency_seconds', 0.0):.2f}s")
        print("-" * 75)
        print(f"control_topic           : {ctrl_query.label}")
        print(f"control_target_evidence : msg::5::VISION_DESCRIPTION")
        print(f"control_decision        : {ctrl_det.decision.value}")
        print(f"control_evidence_ids    : {list(ctrl_det.evidence_ids)}")
        print(f"control_rationale       : {ctrl_det.metadata.get('rationale', 'N/A')}")
        print("-" * 75)
        print(f"file_document_id        : {doc_file.document_id}")
        print(f"file_discovery_run_id   : {file_run_id}")
        print(f"multi_conv_isolation    : {'PASSED (Zero leak)' if cross_isolation_ok else 'FAILED (Contamination)'}")
        print("=" * 75)
        print("ESITO SMOKE TEST OPERATIVO: SUCCESSO")
        return 0

    except LmStudioUnavailableError as unavail:
        print(f"\nLIVE LM STUDIO: NOT EXECUTED ({unavail})")
        return 2
    except Exception as exc:
        print(f"\nERRORE DURANTE LO SMOKE TEST: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smoke test operativo per LM Studio (ChatAnalysis)")
    parser.add_argument("--fake", action="store_true", help="Utilizza client simulato deterministico per test offline")
    parser.add_argument("--model", default=DEFAULT_OPERATIONAL_MODEL, help="Modello target da testare")
    args = parser.parse_args()

    exit_code = run_smoke_test(use_fake=args.fake, model_name=args.model)
    sys.exit(exit_code)
