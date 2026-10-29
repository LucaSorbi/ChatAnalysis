"""
scripts/run_limited_detection_live.py
-------------------------------------
Prova LIMITATA di Topic Detection a microchunk su un documento SINTETICO (nessun dato reale).

- Con LM Studio locale attivo esegue un'inferenza vera su poche finestre (default 24 messaggi = 3 microchunk):
  è l'unico modo per ottenere LIVE_VERIFIED sul meccanismo a microchunk senza rilanciare la Detection completa.
- Con --fake usa un client simulato deterministico: l'esito è MOCK_VERIFIED, mai LIVE_VERIFIED.
- Usa SEMPRE una cartella di checkpoint temporanea nuova (mai quella dell'app) salvo --checkpoint-dir esplicito.
- L'output contiene soli contatori, durate e token: nessun testo di conversazione né output del modello.

Codici di uscita: 0 = eseguita; 1 = fallita; 2 = LM Studio non disponibile (NOT_VERIFIED).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.backend import BaseLocalLlmClient, LlmCompletionResponse  # noqa: E402
from ai.models import ConversationEvidenceDocument, MessageEvidenceBundle, TopicQuery  # noqa: E402
from analysis.checkpoint import AnalysisCheckpointStore  # noqa: E402
from analysis.service import LocalTopicAnalysisService  # noqa: E402
from importer.models import RawRecord  # noqa: E402
from normalization.models import CanonicalMessageType, NormalizedRecord, NormalizedTimestamp, TimestampTzStatus  # noqa: E402
from unified.models import UnifiedMessage  # noqa: E402
from validation.models import ValidationResult  # noqa: E402

DEFAULT_MODEL = "qwen2.5-7b-instruct"


def build_synthetic_document(n: int) -> ConversationEvidenceDocument:
    """Documento sintetico: 1 messaggio su 5 parla di corsa, gli altri sono neutri."""
    bundles = []
    for i in range(n):
        text = f"Ci vediamo alle 18 per la corsa al parco, messaggio {i}" if i % 5 == 0 else f"Messaggio neutro numero {i} sul lavoro"
        raw = RawRecord(
            source_name="synthetic", source_path="/synthetic", source_record_id=str(i), record_type="message",
            raw_fields={"text": text}, media_reference=None, metadata={},
        )
        ts = NormalizedTimestamp(
            status=TimestampTzStatus.KNOWN_UTC, utc_datetime=datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc),
            naive_datetime=None, iso_string="2026-10-16T12:00:00Z", raw_value="2026-10-16T12:00:00Z",
        )
        norm = NormalizedRecord(
            raw_record=raw, validation_result=ValidationResult(record=raw, issues=()), source_name="synthetic",
            source_record_id=str(i), record_type="message", timestamp=ts, message_type=CanonicalMessageType.TEXT,
            text_content=text, media_reference=None,
        )
        um = UnifiedMessage(
            message_id=f"syn_{i}", source_name="synthetic", source_record_id=str(i), source_path="/synthetic",
            record_type="message", timestamp=ts, message_type=CanonicalMessageType.TEXT, text_content=text,
            media_reference=None, provenance_record=norm,
        )
        bundles.append(MessageEvidenceBundle(message=um))
    return ConversationEvidenceDocument(
        document_id="synthetic_limited_detection", bundles=tuple(bundles), source_name="synthetic",
        metadata={"data_classification": "SYNTHETIC_DEMO"},
    )


class _FakeClient(BaseLocalLlmClient):
    def __init__(self, model_id: str) -> None:
        super().__init__()
        self.model_id = model_id
        self.request_listener = None

    def is_available(self, timeout_seconds: float = 2.0) -> bool:
        return True

    def list_models(self) -> tuple[str, ...]:
        return (self.model_id,)

    def chat_completion(self, messages: Sequence[Mapping[str, str]], model_id: str | None = None, schema: Any = None,
                        temperature: float = 0.0, seed: int | None = 42, timeout_seconds: float = 60.0,
                        max_tokens: int | None = None) -> LlmCompletionResponse:
        user = next((m["content"] for m in messages if m.get("role") == "user"), "")
        if self.request_listener:
            try:
                self.request_listener.record_request_sent()
            except Exception:
                pass
        present = "corsa al parco" in user  # solo nelle evidenze sintetiche, non nell'etichetta del topic
        payload = {"decision": "PRESENT", "evidence_ids": ["E001"], "rationale": "Esito simulato."} if present else {"decision": "ABSENT", "evidence_ids": [], "rationale": "Esito simulato."}
        return LlmCompletionResponse(content=json.dumps(payload), model=self.model_id, raw_response={"finish_reason": "stop"})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Prova limitata di Topic Detection a microchunk su dati sintetici")
    ap.add_argument("--fake", action="store_true", help="Client simulato (MOCK_VERIFIED, mai LIVE)")
    ap.add_argument("--messages", type=int, default=24, help="Messaggi sintetici (24 = 3 microchunk)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--topic", default="Sport e corsa")
    ap.add_argument("--checkpoint-dir", default=None, help="Default: cartella temporanea nuova")
    args = ap.parse_args(argv)

    if args.fake:
        client: Any = _FakeClient(args.model)
        origin = "MOCK_VERIFIED"
    else:
        from ai.lmstudio import LmStudioClient
        client = LmStudioClient(base_url="http://127.0.0.1:1234")
        origin = "LIVE_VERIFIED"
        if not client.is_available():
            print("LM Studio non raggiungibile su 127.0.0.1:1234: NOT_VERIFIED")
            return 2

    chk_dir = Path(args.checkpoint_dir) if args.checkpoint_dir else Path(tempfile.mkdtemp(prefix="chatanalysis_limited_det_"))
    store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)
    service = LocalTopicAnalysisService(client=client, checkpoint_store=store, enable_checkpointing=True)
    doc = build_synthetic_document(args.messages)
    topic = TopicQuery(topic_id="limited_probe", label=args.topic, description=args.topic)
    try:
        res = service.run_topic_detection(document=doc, topic=topic, client=client, model_name=args.model)
    except Exception as exc:  # esito esplicito, senza messaggi dell'eccezione (potrebbero contenere testo)
        print(f"FALLITA ({type(exc).__name__})")
        return 1

    d = res.metadata.get("detection_diagnostics", {})
    print(f"esito={origin} decisione={res.decision.value} stato={res.metadata.get('status')} modello={res.metadata.get('model')}")
    print("contatori=" + json.dumps(dict(d), ensure_ascii=False, sort_keys=True))
    print("micro-chunk (indice parent | stato | tentativi | secondi | token prompt/completion):")
    for r in res.metadata.get("chunk_diagnostics", []):
        print(f"  {r.get('chunk_index')} | {r.get('status')} | {r.get('attempts')} | {r.get('duration_seconds')} | {r.get('prompt_tokens')}/{r.get('completion_tokens')}")
    print(f"checkpoint_dir={chk_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
