"""
scripts/smoke_operational_ai.py
-------------------------------
Strumento di smoke test operativo per LM Studio locale (FASE 36, FASE 37, FASE 38).

Utilizza rigorosamente il nuovo LocalTopicAnalysisService e il dataset sintetico DEMO.

Passi eseguiti:
1. Verifica loopback su http://127.0.0.1:1234.
2. Se LM Studio non è disponibile, riporta "LIVE LM STUDIO: NOT EXECUTED" ed esce con codice appropriato.
3. Se disponibile (o con flag --fake per test deterministico):
   - Esegue Topic Discovery LIVE su ConversationEvidenceDocument sintetico.
   - Verifica che emerga almeno un topic valido.
   - Esegue la Prova del Nove (Detection indipendente sul topic scoperto).
   - Calcola e stampa ConsistencyStatus.
   - Esegue il caso di controllo diagnostico "Logistica e Ispezione Depositi".
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Inclusione radice del progetto nel PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai.backend import (
    BaseLlmClient,
    LlmCompletionResponse,
    LmStudioUnavailableError,
)
from ai.lmstudio import DEFAULT_OPERATIONAL_MODEL, LmStudioClient
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
        # Se è discovery
        if "topic discovery" in str(messages[0].get("content", "")).lower() or "emergent topic" in str(messages[0].get("content", "")).lower():
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
            model=self.model_id,
            prompt_tokens=20,
            completion_tokens=20,
            total_tokens=40,
            latency_seconds=0.15,
        )


def run_smoke_test(use_fake: bool = False, model_name: str = DEFAULT_OPERATIONAL_MODEL) -> int:
    print("=" * 60)
    print("SMOKE TEST OPERATIVO AI (LM STUDIO / CHATANALYSIS)")
    print("=" * 60)

    service = LocalTopicAnalysisService(default_model=model_name)
    doc, _, _ = build_synthetic_demo_dataset()

    client: BaseLlmClient | None = None
    if use_fake:
        print("[MODE] Utilizzo FakeLocalLlmClient deterministico per diagnostica.")
        client = FakeSmokeClient()
    else:
        print(f"[MODE] Connessione a LM Studio locale su {service.base_url} (Modello: {model_name})")
        probe_client = LmStudioClient(base_url=service.base_url, model_id=model_name)
        if not probe_client.is_available():
            print("\nLIVE LM STUDIO: NOT EXECUTED")
            print(f"Motivo: server LM Studio non raggiungibile su {service.base_url}.")
            print("Prerequisito per esecuzione reale:")
            print("  1. Avviare LM Studio.")
            print("  2. Attivare il Server Locale (porta 1234, loopback 127.0.0.1).")
            print(f"  3. Caricare il modello '{model_name}'.")
            return 2  # Codice speciale che indica test saltato per assenza di LM Studio

    try:
        # FASE A: TOPIC DISCOVERY LIVE
        print("\n--- 1. ESECUZIONE TOPIC DISCOVERY LIVE ---")
        disc_res = service.run_topic_discovery(
            document=doc,
            client=client,
            model_name=model_name,
            max_topics=5,
        )

        print(f"Topic scoperti: {len(disc_res.topics)}")
        if not disc_res.topics:
            print("ERRORE: Topic Discovery non ha prodotto alcun topic.")
            return 1

        first_topic = disc_res.topics[0]
        print(f"  Topic selezionato : {first_topic.label}")
        print(f"  Descrizione       : {first_topic.short_description}")
        print(f"  Evidenze citate   : {first_topic.evidence_ids}")

        # FASE B: PROVA DEL NOVE (VERIFICA CON TOPIC DETECTION)
        print("\n--- 2. PROVA DEL NOVE: SECONDA INFERENZA INDIPENDENTE ---")
        det_res, consistency = service.verify_discovered_topic(
            document=doc,
            discovered_topic=first_topic,
            discovery_run_id=disc_res.metadata.get("run_id"),
            client=client,
            model_name=model_name,
        )

        print(f"  Decisione Detection : {det_res.decision.value}")
        print(f"  Evidenze Detection  : {det_res.evidence_ids}")
        print(f"  Rationale           : {det_res.rationale}")
        print(f"  Esito Consistency   : {consistency.status.value}")
        print(f"  Evidenze in overlap : {consistency.overlap_evidence_ids}")
        print(f"  Latenza complessiva : {det_res.metadata.get('latency_seconds', 0.0):.2f}s")

        # FASE C: CASO DI CONTROLLO "LOGISTICA E ISPEZIONE DEPOSITI"
        print("\n--- 3. CASO DI CONTROLLO DIAGNOSTICO: 'Logistica e Ispezione Depositi' ---")
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
        print(f"  Decisione : {ctrl_det.decision.value}")
        print(f"  Evidenze  : {ctrl_det.evidence_ids}")
        print(f"  Rationale : {ctrl_det.rationale}")
        print(f"  Latenza   : {ctrl_det.metadata.get('latency_seconds', 0.0):.2f}s")

        print("\n" + "=" * 60)
        print("ESITO SMOKE TEST: COMPLETATO CON SUCCESSO")
        print("=" * 60)
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
