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
from ai.models import TopicDecision, TopicQuery
from analysis.service import LocalTopicAnalysisService
from ui.demo import build_synthetic_demo_dataset


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
        if "topic discovery" in first_content or "emergent topic" in first_content or "discovery" in content_str.lower():
            payload = {
                "topics": [
                    {
                        "label": "Ispezione Depositi e Merci",
                        "short_description": "Verifica magazzino e casse sigillate",
                        "evidence_ids": ["msg::5::VISION_DESCRIPTION"],
                    }
                ]
            }
        else:
            payload = {
                "decision": "PRESENT",
                "evidence_ids": ["msg::5::VISION_DESCRIPTION"],
                "rationale": "Evidenza semantica confermata nella descrizione visiva.",
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
    print("=" * 70)
    print("SMOKE TEST OPERATIVO AI (LM STUDIO / CHATANALYSIS RELEASE GATE)")
    print("=" * 70)

    service = LocalTopicAnalysisService(default_model=model_name, context_length=DEFAULT_OPERATIONAL_CONTEXT_LENGTH)
    doc, _, _ = build_synthetic_demo_dataset()

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
            print(f"  3. Verificare installazione del modello canonico '{model_name}'.")
            return 2  # Exit code 2 indica LM Studio non disponibile

        try:
            native_models = probe_client.get_native_models()
            res_meta = resolve_operational_model_from_metadata(native_models, model_name)
            resolved_info["resolved_model_key"] = res_meta.model_key
            resolved_info["instance_id"] = res_meta.instance_id or "auto"
            resolved_info["context_length"] = res_meta.context_length or DEFAULT_OPERATIONAL_CONTEXT_LENGTH
        except Exception as res_err:
            print(f"Attenzione: risoluzione nativa dettagliata non disponibile ({res_err}). Uso fallback standard.")
            resolved_info["resolved_model_key"] = model_name

    try:
        # FASE 1: TOPIC DISCOVERY LIVE DEMO
        print("\n--- 1. ESECUZIONE TOPIC DISCOVERY LIVE DEMO ---")
        disc_res = service.run_topic_discovery(
            document=doc,
            client=client,
            model_name=model_name,
            max_topics=5,
        )

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
            document=doc,
            discovered_topic=first_topic,
            discovery_result=disc_res,
            client=client,
            model_name=model_name,
        )

        # FASE 3: CONTROLLO SEPARATO "Logistica e Ispezione Depositi"
        print("\n--- 3. TEST CONTROLLO DIAGNOSTICO: 'Logistica e Ispezione Depositi' ---")
        ctrl_query = TopicQuery(
            topic_id="ctrl_logistica",
            label="Logistica e Ispezione Depositi",
            description="Ispezione visiva del magazzino merci con casse sigillate e sicurezza",
        )
        ctrl_det = service.run_topic_detection(
            document=doc,
            topic=ctrl_query,
            client=client,
            model_name=model_name,
        )

        # OUTPUT STRUTTURATO RICHIESTO
        print("\n" + "=" * 70)
        print("REPORT STRUTTURATO RELEASE GATE AI:")
        print("=" * 70)
        print(f"requested canonical model : {resolved_info['requested_canonical_model']}")
        print(f"resolved model key        : {resolved_info['resolved_model_key']}")
        print(f"instance id               : {resolved_info['instance_id']}")
        print(f"response model            : {det_res.metadata.get('model', 'N/A')}")
        print(f"context length            : {resolved_info['context_length']}")
        print(f"discovery topics          : {[t.label for t in disc_res.topics]}")
        print(f"discovery evidence IDs    : {discovery_eids}")
        print(f"detection decision        : {det_res.decision.value}")
        print(f"detection evidence IDs    : {list(det_res.evidence_ids)}")
        print(f"consistency               : {consistency.status.value}")
        print(f"consistency overlap IDs   : {list(consistency.overlap_evidence_ids)}")
        print(f"latencies                 : Discovery={disc_res.metadata.get('latency_seconds', 0.0):.2f}s, "
              f"Detection={det_res.metadata.get('latency_seconds', 0.0):.2f}s, "
              f"Control={ctrl_det.metadata.get('latency_seconds', 0.0):.2f}s")
        print("=" * 70)
        print("ESITO SMOKE TEST: SUCCESSO")
        return 0

    except LmStudioUnavailableError as unavail:
        print(f"\nLIVE LM STUDIO: NOT EXECUTED ({unavail})")
        return 2
    except Exception as exc:
        print(f"\nERRORE DURANTE LO SMOKE TEST: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smoke test operativo per LM Studio")
    parser.add_argument("--fake", action="store_true", help="Utilizza client simulato deterministico")
    parser.add_argument("--model", default=DEFAULT_OPERATIONAL_MODEL, help="Modello target da testare")
    args = parser.parse_args()

    exit_code = run_smoke_test(use_fake=args.fake, model_name=args.model)
    sys.exit(exit_code)
