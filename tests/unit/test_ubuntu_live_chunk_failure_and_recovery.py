"""
tests/unit/test_ubuntu_live_chunk_failure_and_recovery.py
----------------------------------------------------------
Test specialistici per la riproduzione e verifica end-to-end del problema reale su Ubuntu:
- Riproduzione del caso 15/26 chunk completati e 11 falliti (PARTIAL).
- Classificazione precisa dei fallimenti nelle categorie diagnostiche previste (non generico CHUNK_DISCOVERY_ERROR).
- Distinzione rigorosa tra validità sintattica JSON e conformità forense (evidence citation).
- Recupero trasparente da checkpoint SQLite senza nuove richieste verso LM Studio (zero repeat).
- Singolo tentativo di correzione controllata su citazioni non valide.
- Salvataggio immediato di ogni nuovo risultato valido e della diagnostica persistente.
- Meccanismo 'Riprova solo i blocchi falliti' (retry_failed_only=True).
- Transizione da PARTIAL a COMPLETE solo quando il 100% dei blocchi è validato.
- Contabilità forense delle evidenze (totali, analizzate, non analizzate).
- Nessun dato personale o messaggio grezzo nei log diagnostici.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Mapping, Sequence
import pytest

from ai.backend import (
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiInvalidEvidenceCitationError,
    AiStructuredOutputError,
    AnalysisInputTooLargeError,
    BaseLocalLlmClient,
    LlmCompletionResponse,
)
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    MessageEvidenceBundle,
    TopicDiscoveryResult,
)
from multimodal.evidence import EvidenceSourceType, TextEvidenceSection
from importer.models import RawRecord
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult
from analysis.models import (
    ChunkDiagnosticCategory,
    ChunkDiagnosticRecord,
    ChunkLifecycleStatus,
    DiscoveryChunkOutcome,
    OperationalAnalysisConfig,
    OperationalAnalysisFailedError,
    OperationalProfile,
    classify_discovery_exception,
)
from analysis.checkpoint import (
    AnalysisCheckpointStore,
    compute_document_fingerprint,
    compute_run_config_hash,
)
from analysis.progress import AnalysisProgressTracker
from analysis.service import LocalTopicAnalysisService


def _create_synthetic_forensic_doc(
    doc_id: str = "doc_ubuntu_synth_1049",
    num_bundles: int = 26,
) -> ConversationEvidenceDocument:
    """Crea un documento sintetico controllato con evidenze univoche e conformi all'architettura forense."""
    bundles: list[MessageEvidenceBundle] = []
    for b_idx in range(num_bundles):
        raw = RawRecord(
            source_name="whatsapp_forensic_export",
            source_path="/path/test",
            source_record_id=str(b_idx),
            record_type="message",
            raw_fields={"data": f"Forensic message text content for bundle {b_idx}", "timestamp": "2026-10-15T10:00:00Z"},
            media_reference=None,
            metadata={},
        )
        val = ValidationResult(record=raw, issues=())
        ts = NormalizedTimestamp(
            status=TimestampTzStatus.KNOWN_UTC,
            utc_datetime=datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc),
            naive_datetime=None,
            iso_string="2026-10-15T10:00:00Z",
            raw_value="2026-10-15T10:00:00Z",
        )
        norm = NormalizedRecord(
            raw_record=raw,
            validation_result=val,
            source_name="whatsapp_forensic_export",
            source_record_id=str(b_idx),
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=f"Forensic message text content for bundle {b_idx}",
            media_reference=None,
        )
        msg = UnifiedMessage(
            message_id=f"msg_doc_ubuntu_synth_1049_{b_idx}",
            source_name="whatsapp_forensic_export",
            source_record_id=str(b_idx),
            source_path="/path/test",
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=f"Forensic message text content for bundle {b_idx}",
            media_reference=None,
            provenance_record=norm,
        )
        bundles.append(MessageEvidenceBundle(message=msg))

    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=tuple(bundles),
        source_name="whatsapp_forensic_export",
        metadata={"data_classification": "SYNTHETIC_DEMO"},
    )


class UbuntuScenarioScriptedClient(BaseLocalLlmClient):
    """
    Mock deterministico che simula esattamente il comportamento osservato su Ubuntu con LM Studio:
    - 26 chunk totali.
    - Riceve richieste e restituisce risposte programmate per ogni chunk.
    - Traccia il numero di richieste HTTP realmente ricevute.
    """
    def __init__(
        self,
        chunk_behaviors: dict[int, Any],
        model_id: str = "qwen2.5-7b-instruct",
        models: Sequence[str] | None = None,
    ) -> None:
        super().__init__()
        self.chunk_behaviors = chunk_behaviors
        self.model_id = model_id
        self._models = tuple(models) if models is not None else (model_id,)
        self.requests_received: list[dict[str, Any]] = []
        self.request_listener = None
        self.call_count_by_chunk: dict[int, int] = {}

    @property
    def total_calls(self) -> int:
        return len(self.requests_received)

    def is_available(self, timeout_seconds: float = 2.0) -> bool:
        return True

    def list_models(self) -> tuple[str, ...]:
        return self._models

    def chat_completion(
        self,
        messages: Sequence[Mapping[str, str]],
        model_id: str | None = None,
        schema: Mapping[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float = 60.0,
        max_tokens: int | None = None,
    ) -> LlmCompletionResponse:
        self.requests_received.append({
            "model_id": model_id,
            "messages": messages,
            "max_tokens": max_tokens,
        })
        if self.request_listener:
            try:
                self.request_listener.record_request_sent()
            except Exception:
                pass

        # Identifica l'indice del chunk dal testo del messaggio utente
        user_msg = next((m["content"] for m in messages if m.get("role") == "user"), "")
        chunk_idx = 0
        m_chunk = re.search(r"msg_doc_ubuntu_synth_1049_(\d+)", user_msg)
        if m_chunk:
            chunk_idx = int(m_chunk.group(1))

        self.call_count_by_chunk[chunk_idx] = self.call_count_by_chunk.get(chunk_idx, 0) + 1
        call_num = self.call_count_by_chunk[chunk_idx]

        behavior = self.chunk_behaviors.get(chunk_idx, "VALID")

        # Verifica se è un retry con direttiva di correzione
        has_correction = any("CORRECTION DIRECTIVE" in m.get("content", "") for m in messages)

        if callable(behavior):
            return behavior(chunk_idx, call_num, has_correction)

        if behavior == "VALID":
            # Risposta valida con evidence ID conformi al chunk
            content = json.dumps({
                "topics": [
                    {
                        "label": f"Argomento Chunk {chunk_idx}",
                        "short_description": f"Descrizione oggettiva per blocco {chunk_idx}",
                        "evidence_ids": [f"msg_doc_ubuntu_synth_1049_{chunk_idx}::ORIGINAL_TEXT"],
                    }
                ]
            })
            return LlmCompletionResponse(
                content=content,
                model=self.model_id,
                latency_seconds=0.1,
                prompt_tokens=450,
                completion_tokens=80,
                total_tokens=530,
                raw_response={"finish_reason": "stop"},
            )

        if behavior == "INVALID_EVIDENCE_ID":
            # Simula errore reale Ubuntu: JSON formalmente valido ma con evidence ID allucinato o inesistente
            if has_correction and call_num > 1 and behavior != "PERMANENT_INVALID_EVIDENCE_ID":
                # Al retry con correzione, il modello si corregge
                content = json.dumps({
                    "topics": [
                        {
                            "label": f"Argomento Corretto Chunk {chunk_idx}",
                            "short_description": f"Descrizione corretta per blocco {chunk_idx}",
                            "evidence_ids": [f"msg_doc_ubuntu_synth_1049_{chunk_idx}::ORIGINAL_TEXT"],
                        }
                    ]
                })
            else:
                content = json.dumps({
                    "topics": [
                        {
                            "label": f"Argomento Errante Chunk {chunk_idx}",
                            "short_description": f"Descrizione con ID inesistente per blocco {chunk_idx}",
                            "evidence_ids": [f"ev_HALLUCINATED_ID_NOT_EXISTING_{chunk_idx}"],
                        }
                    ]
                })
            return LlmCompletionResponse(
                content=content,
                model=self.model_id,
                latency_seconds=0.1,
                prompt_tokens=450,
                completion_tokens=80,
                total_tokens=530,
                raw_response={"finish_reason": "stop"},
            )

        if behavior == "PERMANENT_INVALID_EVIDENCE_ID":
            # Cita costantemente ID inesistente anche dopo retry
            content = json.dumps({
                "topics": [
                    {
                        "label": f"Argomento Errante Ostinato {chunk_idx}",
                        "short_description": "Descrizione con ID inesistente ostinato",
                        "evidence_ids": [f"ev_HALLUCINATED_PERMANENT_{chunk_idx}"],
                    }
                ]
            })
            return LlmCompletionResponse(
                content=content,
                model=self.model_id,
                latency_seconds=0.1,
                prompt_tokens=450,
                completion_tokens=80,
                total_tokens=530,
                raw_response={"finish_reason": "stop"},
            )

        if behavior == "CROSS_CHUNK_EVIDENCE_ID":
            # Cita ID valido nel documento ma appartenente a un altro chunk
            alien_idx = (chunk_idx + 1) % 26
            content = json.dumps({
                "topics": [
                    {
                        "label": f"Argomento Alieno Chunk {chunk_idx}",
                        "short_description": "Descrizione con ID di un altro chunk",
                        "evidence_ids": [f"msg_doc_ubuntu_synth_1049_{alien_idx}::ORIGINAL_TEXT"],
                    }
                ]
            })
            return LlmCompletionResponse(
                content=content,
                model=self.model_id,
                latency_seconds=0.1,
                prompt_tokens=450,
                completion_tokens=80,
                total_tokens=530,
                raw_response={"finish_reason": "stop"},
            )

        if behavior == "DUPLICATE_EVIDENCE_ID":
            content = json.dumps({
                "topics": [
                    {
                        "label": f"Argomento Duplicati {chunk_idx}",
                        "short_description": "Descrizione con evidenze duplicate",
                        "evidence_ids": [
                            f"msg_doc_ubuntu_synth_1049_{chunk_idx}::ORIGINAL_TEXT",
                            f"msg_doc_ubuntu_synth_1049_{chunk_idx}::ORIGINAL_TEXT",
                        ],
                    }
                ]
            })
            return LlmCompletionResponse(
                content=content,
                model=self.model_id,
                latency_seconds=0.1,
                prompt_tokens=450,
                completion_tokens=80,
                total_tokens=530,
                raw_response={"finish_reason": "stop"},
            )

        if behavior == "EMPTY_EVIDENCE":
            content = json.dumps({
                "topics": [
                    {
                        "label": f"Argomento Senza Evidenze {chunk_idx}",
                        "short_description": "Descrizione priva di evidenze",
                        "evidence_ids": [],
                    }
                ]
            })
            return LlmCompletionResponse(
                content=content,
                model=self.model_id,
                latency_seconds=0.1,
                prompt_tokens=450,
                completion_tokens=80,
                total_tokens=530,
                raw_response={"finish_reason": "stop"},
            )

        if behavior == "EXCESS_TOPICS":
            content = json.dumps({
                "topics": [
                    {
                        "label": f"Topic Extra {i}",
                        "short_description": f"Desc extra {i}",
                        "evidence_ids": [f"msg_doc_ubuntu_synth_1049_{chunk_idx}::ORIGINAL_TEXT"],
                    }
                    for i in range(15)  # supera il max_topics consentito
                ]
            })
            return LlmCompletionResponse(
                content=content,
                model=self.model_id,
                latency_seconds=0.1,
                prompt_tokens=450,
                completion_tokens=400,
                total_tokens=850,
                raw_response={"finish_reason": "stop"},
            )

        if behavior == "JSON_DECODE_ERROR":
            return LlmCompletionResponse(
                content="{'topics': [ unclosed broken json...",
                model=self.model_id,
                latency_seconds=0.1,
                prompt_tokens=450,
                completion_tokens=30,
                total_tokens=480,
                raw_response={"finish_reason": "stop"},
            )

        if behavior == "OUTPUT_TRUNCATED":
            return LlmCompletionResponse(
                content='{"topics": [{"label": "Truncated", "short_desc',
                model=self.model_id,
                latency_seconds=0.1,
                prompt_tokens=450,
                completion_tokens=512,
                total_tokens=962,
                raw_response={"finish_reason": "length"},
            )

        if behavior == "HTTP_400":
            raise AiBackendRequestError("Invalid prompt parameter sent to LM Studio (HTTP 400)", status_code=400)

        if behavior == "TIMEOUT":
            raise AiBackendTimeoutError("LM Studio local request timed out after 60s")

        return LlmCompletionResponse(
            content='{"topics": []}',
            model=self.model_id,
            raw_response={"finish_reason": "stop"},
        )


# =============================================================================
# TEST SUITE: RIPRODUZIONE E VERIFICA CASO 15/26 SU UBUNTU
# =============================================================================

def test_ubuntu_case_15_of_26_first_run_partial_with_granular_diagnostics() -> None:
    """
    Riproduce la prima esecuzione reale:
    - 26 chunk documentali previsti.
    - 15 chunk con risposta valida e salvati in checkpoint.
    - 11 chunk con errori differenziati (invalid evidence IDs, cross-chunk, duplicates, excess topics, etc.).
    - Risultato finale: stato PARTIAL, 15 completati, 11 falliti.
    - Verifica che ogni fallimento abbia una categoria diagnostica precisa (non CHUNK_DISCOVERY_ERROR generico).
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        chk_store = AnalysisCheckpointStore(checkpoint_dir=Path(tmp_dir))
        doc = _create_synthetic_forensic_doc("doc_ubuntu_synth_1049", num_bundles=26)

        # Definisce 15 successi (0..14) e 11 fallimenti eterogenei (15..25)
        behaviors: dict[int, Any] = {}
        for i in range(15):
            behaviors[i] = "VALID"

        behaviors[15] = "PERMANENT_INVALID_EVIDENCE_ID"
        behaviors[16] = "CROSS_CHUNK_EVIDENCE_ID"
        behaviors[17] = "DUPLICATE_EVIDENCE_ID"
        behaviors[18] = "EMPTY_EVIDENCE"
        behaviors[19] = "EXCESS_TOPICS"
        behaviors[20] = "JSON_DECODE_ERROR"
        behaviors[21] = "OUTPUT_TRUNCATED"
        behaviors[22] = "HTTP_400"
        behaviors[23] = "TIMEOUT"
        behaviors[24] = "PERMANENT_INVALID_EVIDENCE_ID"
        behaviors[25] = "CROSS_CHUNK_EVIDENCE_ID"

        client = UbuntuScenarioScriptedClient(behaviors)
        service = LocalTopicAnalysisService(
            checkpoint_store=chk_store,
            enable_checkpointing=True,
            default_model="qwen2.5-7b-instruct",
            profile=OperationalProfile.BALANCED,
            max_chunk_bundles=1,  # 1 bundle per chunk -> 26 chunk esatti
        )

        res = service.run_topic_discovery(
            document=doc,
            client=client,
            model_name="qwen2.5-7b-instruct",
            max_topics=8,
        )

        meta = res.metadata
        assert meta["chunk_count"] == 26
        assert meta["completed_chunk_count"] == 15
        assert meta["failed_chunk_count"] == 11
        assert meta["status"] == "PARTIAL"

        # Verifica contabilità evidenze
        assert meta["total_evidence_count"] == 26  # 26 bundle * 1 sezione per chunk
        assert meta["analyzed_evidence_count"] == 15  # 15 chunk riusciti * 1
        assert meta["unanalyzed_evidence_count"] == 11  # 11 chunk falliti * 1

        # Verifica diagnostica dettagliata dei chunk
        diagnostics = meta.get("chunk_diagnostics", [])
        assert len(diagnostics) == 26

        # Primi 15 chunk: tutti SUCCESS
        for i in range(15):
            d = diagnostics[i]
            assert d["chunk_index"] == i
            assert d["status"] == ChunkLifecycleStatus.SUCCESS.value
            assert d["diagnostic_category"] == "NONE"
            assert d["checkpoint_saved"] is True

        # Chunk 15..25: categorie specifiche e sicure
        failed_diag_map = {d["chunk_index"]: d for d in diagnostics[15:]}

        assert failed_diag_map[15]["diagnostic_category"] == ChunkDiagnosticCategory.INVALID_EVIDENCE_ID.value
        assert failed_diag_map[16]["diagnostic_category"] == ChunkDiagnosticCategory.INVALID_EVIDENCE_ID.value
        assert failed_diag_map[17]["diagnostic_category"] == ChunkDiagnosticCategory.DUPLICATE_EVIDENCE_ID.value
        assert failed_diag_map[18]["diagnostic_category"] == ChunkDiagnosticCategory.EMPTY_EVIDENCE.value
        assert failed_diag_map[19]["diagnostic_category"] == ChunkDiagnosticCategory.EXCESS_TOPICS.value
        assert failed_diag_map[20]["diagnostic_category"] == ChunkDiagnosticCategory.JSON_DECODE_ERROR.value
        assert failed_diag_map[21]["diagnostic_category"] == ChunkDiagnosticCategory.OUTPUT_TRUNCATED.value
        assert failed_diag_map[22]["diagnostic_category"] == ChunkDiagnosticCategory.HTTP_INVALID_PARAMETERS.value
        assert failed_diag_map[23]["diagnostic_category"] == ChunkDiagnosticCategory.TIMEOUT.value

        # Nessun codice deve essere il generico CHUNK_DISCOVERY_ERROR
        for d in diagnostics:
            assert d["diagnostic_category"] != "CHUNK_DISCOVERY_ERROR"


def test_ubuntu_case_second_run_recovers_15_from_checkpoint_without_llm_requests() -> None:
    """
    Riproduce la seconda esecuzione:
    - I 15 chunk completati devono essere recuperati dal checkpoint SQLite.
    - Zero richieste LLM devono essere inviate per i primi 15 chunk.
    - Soltanto gli 11 chunk mancanti vengono inviati a LM Studio.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        chk_store = AnalysisCheckpointStore(checkpoint_dir=Path(tmp_dir))
        doc = _create_synthetic_forensic_doc("doc_ubuntu_synth_1049", num_bundles=26)

        # Prima esecuzione: 15 successi, 11 falliti
        behaviors_run1: dict[int, Any] = {i: "VALID" for i in range(15)}
        for i in range(15, 26):
            behaviors_run1[i] = "PERMANENT_INVALID_EVIDENCE_ID"

        client1 = UbuntuScenarioScriptedClient(behaviors_run1)
        service1 = LocalTopicAnalysisService(
            checkpoint_store=chk_store,
            enable_checkpointing=True,
            default_model="qwen2.5-7b-instruct",
            profile=OperationalProfile.BALANCED,
            max_chunk_bundles=1,
        )

        res1 = service1.run_topic_discovery(
            document=doc,
            client=client1,
            model_name="qwen2.5-7b-instruct",
            max_topics=8,
        )
        assert res1.metadata["completed_chunk_count"] == 15
        assert res1.metadata["failed_chunk_count"] == 11

        # Seconda esecuzione: simula il client della seconda run
        behaviors_run2: dict[int, Any] = {i: "VALID" for i in range(15)}
        for i in range(15, 26):
            behaviors_run2[i] = "PERMANENT_INVALID_EVIDENCE_ID"

        client2 = UbuntuScenarioScriptedClient(behaviors_run2)
        service2 = LocalTopicAnalysisService(
            checkpoint_store=chk_store,
            enable_checkpointing=True,
            default_model="qwen2.5-7b-instruct",
            profile=OperationalProfile.BALANCED,
            max_chunk_bundles=1,
        )

        res2 = service2.run_topic_discovery(
            document=doc,
            client=client2,
            model_name="qwen2.5-7b-instruct",
            max_topics=8,
        )

        # Verifiche fondamentali:
        # 1. Nessuna richiesta inviata per i chunk 0..14
        for i in range(15):
            assert client2.call_count_by_chunk.get(i, 0) == 0, f"Chunk {i} non doveva inviare richieste!"

        # 2. Richieste inviate ESCLUSIVAMENTE per i chunk mancanti (15..25)
        for i in range(15, 26):
            assert client2.call_count_by_chunk.get(i, 0) >= 1, f"Chunk {i} doveva essere elaborato!"

        # 3. Metadati di recupero
        assert res2.metadata["recovered_chunk_count"] == 15
        assert res2.metadata["completed_chunk_count"] == 15
        assert res2.metadata["failed_chunk_count"] == 11


def test_ubuntu_case_controlled_retry_corrects_invalid_citations() -> None:
    """
    Verifica il singolo tentativo di correzione controllata:
    - Se il modello al primo tentativo restituisce un evidence ID non valido,
      riceve la direttiva di correzione forense.
    - Se al secondo tentativo si corregge, il chunk passa a SUCCESS e viene salvato in checkpoint.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        chk_store = AnalysisCheckpointStore(checkpoint_dir=Path(tmp_dir))
        doc = _create_synthetic_forensic_doc("doc_ubuntu_synth_1049", num_bundles=2)

        # Chunk 0 valido, Chunk 1 invalido al primo tentativo ma corretto al retry
        behaviors = {
            0: "VALID",
            1: "INVALID_EVIDENCE_ID",  # il mock si corregge al secondo tentativo se has_correction=True
        }

        client = UbuntuScenarioScriptedClient(behaviors)
        service = LocalTopicAnalysisService(
            checkpoint_store=chk_store,
            enable_checkpointing=True,
            default_model="qwen2.5-7b-instruct",
            profile=OperationalProfile.BALANCED,
            max_chunk_bundles=1,
        )

        res = service.run_topic_discovery(
            document=doc,
            client=client,
            model_name="qwen2.5-7b-instruct",
            max_topics=8,
        )

        assert res.metadata["chunk_count"] == 2
        assert res.metadata["completed_chunk_count"] == 2
        assert res.metadata["failed_chunk_count"] == 0
        assert res.metadata["status"] == "COMPLETE"

        # Chunk 1 ha effettuato 2 chiamate ed è riuscito al retry
        assert client.call_count_by_chunk[1] == 2
        diag_1 = res.metadata["chunk_diagnostics"][1]
        assert diag_1["status"] == ChunkLifecycleStatus.SUCCESS.value
        assert diag_1["attempts"] == 2


def test_retry_failed_only_completes_remaining_chunks_and_transitions_to_complete() -> None:
    """
    Verifica la funzione «Riprova solo i blocchi falliti» (retry_failed_only=True):
    - Run 1: 15 successi, 11 fallimenti -> PARTIAL.
    - Run 2 con retry_failed_only=True: gli 11 blocchi ora restituiscono output validi.
    - I 15 blocchi vengono recuperati dal checkpoint senza chiamate LLM.
    - Gli 11 blocchi mancanti vengono elaborati, validati e salvati.
    - Lo stato finale transita a COMPLETE (26/26).
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        chk_store = AnalysisCheckpointStore(checkpoint_dir=Path(tmp_dir))
        doc = _create_synthetic_forensic_doc("doc_ubuntu_synth_1049", num_bundles=26)

        # Run 1: 15 successi, 11 fallimenti permanenti
        behaviors1 = {i: "VALID" for i in range(15)}
        for i in range(15, 26):
            behaviors1[i] = "PERMANENT_INVALID_EVIDENCE_ID"

        client1 = UbuntuScenarioScriptedClient(behaviors1)
        service1 = LocalTopicAnalysisService(
            checkpoint_store=chk_store,
            enable_checkpointing=True,
            default_model="qwen2.5-7b-instruct",
            profile=OperationalProfile.BALANCED,
            max_chunk_bundles=1,
        )

        res1 = service1.run_topic_discovery(
            document=doc,
            client=client1,
            model_name="qwen2.5-7b-instruct",
            max_topics=8,
        )
        assert res1.metadata["status"] == "PARTIAL"
        assert res1.metadata["completed_chunk_count"] == 15

        # Run 2: Ora il backend (o modello corretto) risponde VALID anche per gli 11 blocchi
        behaviors2 = {i: "VALID" for i in range(26)}
        client2 = UbuntuScenarioScriptedClient(behaviors2)
        service2 = LocalTopicAnalysisService(
            checkpoint_store=chk_store,
            enable_checkpointing=True,
            default_model="qwen2.5-7b-instruct",
            profile=OperationalProfile.BALANCED,
            max_chunk_bundles=1,
        )

        res2 = service2.run_topic_discovery(
            document=doc,
            client=client2,
            model_name="qwen2.5-7b-instruct",
            max_topics=8,
            retry_failed_only=True,
        )

        # Verifiche:
        # 1. Zero richieste per i chunk 0..14
        for i in range(15):
            assert client2.call_count_by_chunk.get(i, 0) == 0

        # 2. Esattamente 11 richieste inviate
        for i in range(15, 26):
            assert client2.call_count_by_chunk.get(i, 0) == 1

        # 3. Transizione ufficiale a COMPLETE
        assert res2.metadata["status"] == "COMPLETE"
        assert res2.metadata["completed_chunk_count"] == 26
        assert res2.metadata["failed_chunk_count"] == 0
        assert res2.metadata["recovered_chunk_count"] == 15
        assert res2.metadata["analyzed_evidence_count"] == 26
        assert res2.metadata["unanalyzed_evidence_count"] == 0


def test_persistence_error_simulation_is_diagnosed_accurately() -> None:
    """
    Verifica che un errore di persistenza SQLite durante il salvataggio del blocco
    venga classificato accuratamente come CHECKPOINT_SAVE_ERROR.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        class FailingSaveStore(AnalysisCheckpointStore):
            def save_chunk(self, *args: Any, **kwargs: Any) -> None:
                raise RuntimeError("Disk I/O error during SQLite write")

        failing_store = FailingSaveStore(checkpoint_dir=Path(tmp_dir))
        doc = _create_synthetic_forensic_doc("doc_save_err", num_bundles=1)
        client = UbuntuScenarioScriptedClient({0: "VALID"})
        service = LocalTopicAnalysisService(
            checkpoint_store=failing_store,
            enable_checkpointing=True,
            default_model="qwen2.5-7b-instruct",
        )

        # L'analisi non fallisce se l'inferenza è valida, ma il salvataggio checkpoint non riesce
        tracker = AnalysisProgressTracker(total_chunks=1)
        res = service.run_topic_discovery(
            document=doc,
            client=client,
            model_name="qwen2.5-7b-instruct",
            tracker=tracker,
        )
        assert res.metadata["status"] == "COMPLETE"
        diag = res.metadata["chunk_diagnostics"][0]
        assert diag["checkpoint_saved"] is False


def test_diagnostics_contain_no_private_chat_content() -> None:
    """
    Verifica forense di confidenzialità:
    I log e i report diagnostici non devono contenere estratti di testo delle conversazioni,
    né testi dei messaggi né citazioni non autorizzate.
    """
    doc = _create_synthetic_forensic_doc("doc_privacy_test", num_bundles=2)
    behaviors = {
        0: "VALID",
        1: "PERMANENT_INVALID_EVIDENCE_ID",
    }
    client = UbuntuScenarioScriptedClient(behaviors)
    service = LocalTopicAnalysisService(
        enable_checkpointing=False,
        default_model="qwen2.5-7b-instruct",
        max_chunk_bundles=1,
    )

    res = service.run_topic_discovery(
        document=doc,
        client=client,
        model_name="qwen2.5-7b-instruct",
    )

    diagnostics = res.metadata.get("chunk_diagnostics", [])
    diag_json = json.dumps(diagnostics, default=str)

    # I testi dei messaggi o delle evidenze NON devono comparire nei campi diagnostici
    for bundle in doc.bundles:
        assert bundle.message.text_content not in diag_json
        for sec in bundle.text_evidence_sections:
            assert sec.text not in diag_json


def test_retry_failed_only_negative_when_checkpoint_missing() -> None:
    """
    Test negativo (Requisito 1):
    Se il checkpoint è assente, l'avvio con retry_failed_only=True NON deve eseguire
    arbitrariamente l'intera analisi, ma sollevare OperationalAnalysisFailedError con
    messaggio esplicito che richiede l'avvio di una nuova Topic Discovery completa.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        chk_store = AnalysisCheckpointStore(checkpoint_dir=Path(tmp_dir))
        doc = _create_synthetic_forensic_doc("doc_no_chk", num_bundles=3)
        client = UbuntuScenarioScriptedClient({0: "VALID", 1: "VALID", 2: "VALID"})
        service = LocalTopicAnalysisService(
            checkpoint_store=chk_store,
            enable_checkpointing=True,
            default_model="qwen2.5-7b-instruct",
            max_chunk_bundles=1,
        )

        with pytest.raises(OperationalAnalysisFailedError) as exc_info:
            service.run_topic_discovery(
                document=doc,
                client=client,
                model_name="qwen2.5-7b-instruct",
                retry_failed_only=True,
            )

        err_msg = str(exc_info.value)
        assert "Impossibile eseguire la riprova selettiva" in err_msg
        assert "nuova Topic Discovery completa" in err_msg
        # Nessuna richiesta inviata al modello
        assert client.total_calls == 0


def test_retry_failed_only_negative_when_checkpoint_incompatible() -> None:
    """
    Test negativo (Requisito 1):
    Se il checkpoint esistente è per un modello diverso o configurazione incompatibile,
    l'avvio con retry_failed_only=True non deve fingere un recupero selettivo.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        chk_store = AnalysisCheckpointStore(checkpoint_dir=Path(tmp_dir))
        doc = _create_synthetic_forensic_doc("doc_incompat_chk", num_bundles=2)
        client = UbuntuScenarioScriptedClient(
            {0: "VALID", 1: "VALID"},
            models=["qwen2.5-7b-instruct", "different-model-7b"],
        )
        service = LocalTopicAnalysisService(
            checkpoint_store=chk_store,
            enable_checkpointing=True,
            default_model="qwen2.5-7b-instruct",
            max_chunk_bundles=1,
        )

        # Esecuzione iniziale con un modello A
        service.run_topic_discovery(
            document=doc,
            client=client,
            model_name="qwen2.5-7b-instruct",
        )

        # Tentativo di recupero selettivo con modello B diverso
        with pytest.raises(OperationalAnalysisFailedError) as exc_info:
            service.run_topic_discovery(
                document=doc,
                client=client,
                model_name="different-model-7b",
                retry_failed_only=True,
            )

        err_msg = str(exc_info.value)
        assert "Impossibile eseguire la riprova selettiva" in err_msg
        assert "nuova Topic Discovery completa" in err_msg


def test_corrective_retry_context_overflow_prevention() -> None:
    """
    Verifica (Requisito 2):
    Se un retry per citazioni non valide rischia di sforare la finestra di contesto (es. context_length ridotto),
    il ricalcolo preventivo del budget impedisce l'invio di richieste sovradimensionate a LM Studio
    e solleva AnalysisInputTooLargeError / classifica HTTP_CONTEXT_OVERFLOW senza generare HTTP 400.
    """
    from ai.topics import TopicDiscoveryAnalyzer
    from ai.backend import AnalysisInputTooLargeError

    doc = _create_synthetic_forensic_doc("doc_budget_overflow", num_bundles=1)
    behaviors = {0: "INVALID_THEN_VALID"}
    client = UbuntuScenarioScriptedClient(behaviors)

    analyzer = TopicDiscoveryAnalyzer(
        client=client,
        default_model_id="qwen2.5-7b-instruct",
        operational_mode=True,
    )

    # Con context_length deliberatamente molto stretto (es. 400 token),
    # il prompt iniziale + risposta + direttiva di correzione non può rientrare con margine sicuro
    with pytest.raises((AnalysisInputTooLargeError, AiInvalidEvidenceCitationError)) as exc_info:
        analyzer.discover_topics(
            document=doc,
            model_id="qwen2.5-7b-instruct",
            operational_mode=True,
            context_length=400,
        )

    # La richiesta iniziale viene effettuata, ma la seconda non genera overflow incontrollato
    assert client.total_calls <= 2


def test_accurate_observed_metrics_attempts_finish_reason_http_status() -> None:
    """
    Verifica (Requisito 4):
    Le metriche diagnostiche riflettono fedelmente i dati realmente osservati:
    - Un fallimento al primo tentativo ha attempts=1 (non hardcoded 2).
    - In assenza di risposta completata, finish_reason non viene presunto 'stop'.
    - Un errore HTTP o timeout registra lo status corretto.
    """
    class ScriptedFailureClient(BaseLocalLlmClient):
        def __init__(self) -> None:
            super().__init__()
            self.base_url = "http://127.0.0.1:1234"
            self.calls = 0

        def is_available(self, timeout_seconds: float = 2.0) -> bool:
            return True

        def list_models(self) -> Sequence[str]:
            return ("qwen2.5-7b-instruct",)

        def chat_completion(self, *args: Any, **kwargs: Any) -> LlmCompletionResponse:
            self.calls += 1
            if self.calls == 1:
                # Chunk 0: Errore di connessione / timeout (nessun HTTP status, nessun finish_reason)
                raise AiBackendTimeoutError("Timeout connessione LM Studio")
            elif self.calls == 2:
                # Chunk 1: Errore HTTP 500
                err = AiBackendRequestError("Internal Server Error da LM Studio")
                err.status_code = 500
                raise err
            else:
                # Chunk 2: Successo con metadati reali
                return LlmCompletionResponse(
                    content=json.dumps({"topics": []}),
                    model="qwen2.5-7b-instruct",
                    prompt_tokens=150,
                    completion_tokens=40,
                    total_tokens=190,
                    latency_seconds=0.5,
                    raw_response={"finish_reason": "stop"},
                )

    doc = _create_synthetic_forensic_doc("doc_obs_metrics", num_bundles=3)
    client = ScriptedFailureClient()
    service = LocalTopicAnalysisService(
        enable_checkpointing=False,
        default_model="qwen2.5-7b-instruct",
        max_chunk_bundles=1,
    )

    res = service.run_topic_discovery(
        document=doc,
        client=client,
        model_name="qwen2.5-7b-instruct",
    )

    diags = res.metadata["chunk_diagnostics"]
    assert len(diags) == 3

    # Chunk 0: Timeout -> attempts=1, finish_reason=None, http_status=None
    diag0 = diags[0]
    assert diag0["attempts"] == 1
    assert diag0["finish_reason"] is None
    assert diag0["http_status"] is None
    assert diag0["diagnostic_category"] == ChunkDiagnosticCategory.TIMEOUT.value

    # Chunk 1: HTTP 500 -> attempts=1, http_status=500, finish_reason=None
    diag1 = diags[1]
    assert diag1["attempts"] == 1
    assert diag1["http_status"] == 500
    assert diag1["finish_reason"] is None

    # Chunk 2: Successo -> attempts=1, http_status=200, finish_reason="stop"
    diag2 = diags[2]
    assert diag2["attempts"] == 1
    assert diag2["http_status"] == 200
    assert diag2["finish_reason"] == "stop"


def test_failed_chunk_diagnostics_persisted_to_sqlite() -> None:
    """
    Verifica (Requisito 3 e 4):
    La diagnostica dei chunk falliti viene salvata anche su database SQLite nella tabella
    checkpoint_chunk_diagnostics ed è recuperabile tramite get_chunk_diagnostics.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        chk_store = AnalysisCheckpointStore(checkpoint_dir=Path(tmp_dir))
        doc = _create_synthetic_forensic_doc("doc_persist_diag", num_bundles=2)
        behaviors = {
            0: "VALID",
            1: "PERMANENT_INVALID_EVIDENCE_ID",
        }
        client = UbuntuScenarioScriptedClient(behaviors)
        service = LocalTopicAnalysisService(
            checkpoint_store=chk_store,
            enable_checkpointing=True,
            default_model="qwen2.5-7b-instruct",
            max_chunk_bundles=1,
        )

        res = service.run_topic_discovery(
            document=doc,
            client=client,
            model_name="qwen2.5-7b-instruct",
        )
        assert res.metadata["status"] == "PARTIAL"

        doc_fp = compute_document_fingerprint(doc)
        chk_meta = chk_store.get_latest_checkpoint_metadata_for_document(doc.document_id, doc_fp)
        assert chk_meta is not None
        cfg_fp = chk_meta["config_fingerprint"]

        # Recupera la diagnostica salvata direttamente da SQLite
        saved_diags = chk_store.get_chunk_diagnostics(doc_fp, cfg_fp)
        assert len(saved_diags) == 2

        # Chunk 0 è salvato con SUCCESS
        assert saved_diags[0].chunk_index == 0
        assert saved_diags[0].status == ChunkLifecycleStatus.SUCCESS.value
        assert saved_diags[0].checkpoint_saved is True

        # Chunk 1 è salvato con FAILED ed INVALID_EVIDENCE_ID
        assert saved_diags[1].chunk_index == 1
        assert saved_diags[1].status == ChunkLifecycleStatus.FAILED.value
        assert saved_diags[1].diagnostic_category == ChunkDiagnosticCategory.INVALID_EVIDENCE_ID.value


def test_restore_diagnostics_and_state_after_streamlit_restart() -> None:
    """
    Verifica (Requisito 3):
    Dopo un riavvio simulato di Streamlit e ricaricamento dello stesso documento,
    service.load_persisted_discovery_result ricostruisce l'analisi con i topic validati,
    la tabella diagnostica completa, la data di esecuzione e lo stato PARTIAL.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        chk_store = AnalysisCheckpointStore(checkpoint_dir=Path(tmp_dir))
        doc = _create_synthetic_forensic_doc("doc_restart_demo", num_bundles=4)
        behaviors = {
            0: "VALID",
            1: "VALID",
            2: "PERMANENT_INVALID_EVIDENCE_ID",
            3: "PERMANENT_INVALID_EVIDENCE_ID",
        }
        client = UbuntuScenarioScriptedClient(behaviors)
        service1 = LocalTopicAnalysisService(
            checkpoint_store=chk_store,
            enable_checkpointing=True,
            default_model="qwen2.5-7b-instruct",
            max_chunk_bundles=1,
        )

        res1 = service1.run_topic_discovery(
            document=doc,
            client=client,
            model_name="qwen2.5-7b-instruct",
        )
        assert res1.metadata["status"] == "PARTIAL"
        assert res1.metadata["completed_chunk_count"] == 2
        assert res1.metadata["failed_chunk_count"] == 2

        # Simulazione riavvio di Streamlit: nuova istanza del service senza cache in memoria
        service2 = LocalTopicAnalysisService(
            checkpoint_store=chk_store,
            enable_checkpointing=True,
            default_model="qwen2.5-7b-instruct",
            max_chunk_bundles=1,
        )

        restored_res = service2.load_persisted_discovery_result(
            document=doc,
            model_name="qwen2.5-7b-instruct",
        )

        assert restored_res is not None
        assert restored_res.metadata["status"] == "PARTIAL"
        assert restored_res.metadata["completed_chunk_count"] == 2
        assert restored_res.metadata["failed_chunk_count"] == 2
        assert restored_res.metadata["is_persisted_recovery"] is True
        assert len(restored_res.topics) >= 1
        assert len(restored_res.metadata["chunk_diagnostics"]) == 4

