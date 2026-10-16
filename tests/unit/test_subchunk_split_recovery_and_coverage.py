"""
tests/unit/test_subchunk_split_recovery_and_coverage.py
-------------------------------------------------------
Test specialistici per le quattro correzioni mirate:
1. Copertura forense esatta dopo split (nessuna evidenza fantasma attribuita da sotto-blocchi falliti).
2. Salvataggio e recupero incrementale dei sotto-blocchi riusciti su SQLite (parent/child relationship, zero duplicate inference).
3. Ripristino storico coerente con modello e configurazione (distinzione tra storico e modello operativo).
4. Preservazione fedele del finish_reason (nessun default presunto "stop" quando assente o None).
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping, Sequence
import pytest

from ai.backend import (
    AiBackendRequestError,
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
)
from analysis.checkpoint import (
    AnalysisCheckpointStore,
    compute_document_fingerprint,
    compute_run_config_hash,
)
from analysis.progress import AnalysisProgressTracker
from analysis.service import LocalTopicAnalysisService


@pytest.fixture(autouse=True)
def clean_test_checkpoints():
    chk_dir = Path(".checkpoints")
    if chk_dir.exists():
        shutil.rmtree(chk_dir, ignore_errors=True)
    yield
    if chk_dir.exists():
        shutil.rmtree(chk_dir, ignore_errors=True)


def _build_test_document(doc_id: str, num_messages: int) -> ConversationEvidenceDocument:
    bundles: list[MessageEvidenceBundle] = []
    for i in range(num_messages):
        raw = RawRecord(
            source_name="whatsapp_test",
            source_path="/test",
            source_record_id=str(i),
            record_type="message",
            raw_fields={"text": f"Messaggio forense {i}", "timestamp": "2026-10-16T12:00:00Z"},
            media_reference=None,
            metadata={},
        )
        val = ValidationResult(record=raw, issues=())
        ts = NormalizedTimestamp(
            status=TimestampTzStatus.KNOWN_UTC,
            utc_datetime=datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc),
            naive_datetime=None,
            iso_string="2026-10-16T12:00:00Z",
            raw_value="2026-10-16T12:00:00Z",
        )
        norm = NormalizedRecord(
            raw_record=raw,
            validation_result=val,
            source_name="whatsapp_test",
            source_record_id=str(i),
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=f"Messaggio forense {i} per verifica evidenze",
            media_reference=None,
        )
        um = UnifiedMessage(
            message_id=f"msg_{doc_id}_{i}",
            source_name="whatsapp_test",
            source_record_id=str(i),
            source_path="/test",
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=f"Messaggio forense {i} per verifica evidenze",
            media_reference=None,
            provenance_record=norm,
        )
        bundles.append(MessageEvidenceBundle(message=um))
    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=tuple(bundles),
        source_name="whatsapp_test",
    )


class ScriptableMockLlmClient(BaseLocalLlmClient):
    """Client mock deterministico per testare split, fallimenti e finish_reason."""
    def __init__(
        self,
        handler: Any,
        model_id: str = "qwen2.5-7b-instruct",
        models: Sequence[str] | None = None,
    ) -> None:
        super().__init__()
        self.handler = handler
        self.model_id = model_id
        self._models = tuple(models) if models is not None else (model_id,)
        self.call_history: list[dict[str, Any]] = []
        self.request_listener = None

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
        call_info = {
            "model_id": model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "user_content": next((m["content"] for m in messages if m.get("role") == "user"), ""),
        }
        self.call_history.append(call_info)
        if self.request_listener:
            try:
                self.request_listener.record_request_sent()
            except Exception:
                pass
        return self.handler(call_info, len(self.call_history))


# ==============================================================================
# 1. TEST COPERTURA DELLE EVIDENZE DOPO SPLIT (MULTI-CHUNK E SINGLE-CHUNK)
# ==============================================================================

def test_evidence_coverage_after_subchunk_split_multichunk():
    """
    Riproduce il difetto 1:
    - Documento sintetico di 4 messaggi.
    - 2 chunk iniziali da 2 messaggi: Chunk 0 (msg 0, 1) e Chunk 1 (msg 2, 3).
    - Chunk 0 fallisce per CONTEXT_OVERFLOW e viene suddiviso in Sub0 (msg 0) e Sub1 (msg 1).
    - Sub0: SUCCESS (evidenza msg_doc_split_0::ORIGINAL_TEXT).
    - Sub1: FAILED (errore backend simulato).
    - Chunk 1: SUCCESS (evidenze msg_doc_split_2 e msg_doc_split_3).
    - Risultato PARTIAL.
    - Verifica che la copertura dichiarata sia ESATTAMENTE 3/4 (75%), NON 4/4!
    - Non dichiarare esaminate evidenze appartenenti a sotto-blocchi falliti.
    """
    doc = _build_test_document("doc_split_4", num_messages=4)
    tmp_dir = tempfile.mkdtemp(prefix="test_chk_split_")
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_dir)

    def _mock_handler(call_info: dict[str, Any], call_count: int) -> LlmCompletionResponse:
        user_text = call_info["user_content"]
        # Chunk 0 iniziale ha sia msg 0 che msg 1
        if "msg_doc_split_4_0" in user_text and "msg_doc_split_4_1" in user_text:
            err = AiBackendRequestError("Simulated context overflow on Chunk 0")
            err.error_category = "CONTEXT_OVERFLOW"
            raise err

        # Sub0 ha solo msg 0
        if "msg_doc_split_4_0" in user_text and "msg_doc_split_4_1" not in user_text:
            return LlmCompletionResponse(
                content=json.dumps({
                    "topics": [
                        {
                            "label": "Topic da Sub0",
                            "short_description": "Descrizione Sub0",
                            "evidence_ids": ["msg_doc_split_4_0::ORIGINAL_TEXT"],
                        }
                    ]
                }),
                model="qwen2.5-7b-instruct",
                raw_response={"finish_reason": "stop"},
            )

        # Sub1 ha solo msg 1 -> Simula fallimento permanente
        if "msg_doc_split_4_1" in user_text and "msg_doc_split_4_0" not in user_text:
            raise AiBackendRequestError("Simulated permanent failure on Sub1")

        # Chunk 1 ha msg 2 e msg 3 -> SUCCESS
        if "msg_doc_split_4_2" in user_text:
            return LlmCompletionResponse(
                content=json.dumps({
                    "topics": [
                        {
                            "label": "Topic da Chunk 1",
                            "short_description": "Descrizione Chunk 1",
                            "evidence_ids": ["msg_doc_split_4_2::ORIGINAL_TEXT", "msg_doc_split_4_3::ORIGINAL_TEXT"],
                        }
                    ]
                }),
                model="qwen2.5-7b-instruct",
                raw_response={"finish_reason": "stop"},
            )

        raise ValueError(f"Unexpected user text: {user_text}")

    client = ScriptableMockLlmClient(handler=_mock_handler)
    service = LocalTopicAnalysisService(
        client=client,
        checkpoint_store=store,
        enable_checkpointing=True,
        max_chunk_bundles=2,
    )

    tracker = AnalysisProgressTracker(total_chunks=2)
    result = service.run_topic_discovery(
        document=doc,
        client=client,
        model_name="qwen2.5-7b-instruct",
        tracker=tracker,
    )

    meta = result.metadata
    assert meta["status"] == "PARTIAL"
    assert meta["total_evidence_count"] == 4
    # DIFETTO 1 RISOLTO: esattamente 3 evidenze analizzate su 4!
    assert meta["analyzed_evidence_count"] == 3
    assert meta["unanalyzed_evidence_count"] == 1
    assert "msg_doc_split_4_1::ORIGINAL_TEXT" not in meta.get("analyzed_evidence_ids", [])


def test_evidence_coverage_after_subchunk_split_singlechunk():
    """
    Verifica il difetto 1 sul percorso single-chunk con suddivisione dinamica per CONTEXT_OVERFLOW:
    - 1 chunk con 2 messaggi (msg 0 e msg 1).
    - Chiamata iniziale genera CONTEXT_OVERFLOW.
    - Suddivisione dinamica in Sub0 (msg 0) e Sub1 (msg 1).
    - Sub0: SUCCESS. Sub1: FAILED.
    - Copertura dichiarata: esattamente 1/2 (50%), non 2/2!
    """
    doc = _build_test_document("doc_single_split_2", num_messages=2)
    tmp_dir = tempfile.mkdtemp(prefix="test_chk_single_")
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_dir)

    def _mock_handler(call_info: dict[str, Any], call_count: int) -> LlmCompletionResponse:
        user_text = call_info["user_content"]
        if "msg_doc_single_split_2_0" in user_text and "msg_doc_single_split_2_1" in user_text:
            err = AiBackendRequestError("Simulated context overflow on single chunk")
            err.error_category = "CONTEXT_OVERFLOW"
            raise err

        if "msg_doc_single_split_2_0" in user_text:
            return LlmCompletionResponse(
                content=json.dumps({
                    "topics": [
                        {
                            "label": "Topic Sub0",
                            "short_description": "Sub0 topic",
                            "evidence_ids": ["msg_doc_single_split_2_0::ORIGINAL_TEXT"],
                        }
                    ]
                }),
                model="qwen2.5-7b-instruct",
                raw_response={"finish_reason": "stop"},
            )

        if "msg_doc_single_split_2_1" in user_text:
            raise AiBackendRequestError("Permanent failure on Sub1")

        raise ValueError("Unexpected call")

    client = ScriptableMockLlmClient(handler=_mock_handler)
    service = LocalTopicAnalysisService(
        client=client,
        checkpoint_store=store,
        enable_checkpointing=True,
    )
    service.context_length = 4096  # 1 singolo chunk

    tracker = AnalysisProgressTracker(total_chunks=1)
    result = service.run_topic_discovery(
        document=doc,
        client=client,
        model_name="qwen2.5-7b-instruct",
        tracker=tracker,
    )

    meta = result.metadata
    assert meta["status"] == "PARTIAL"
    assert meta["total_evidence_count"] == 2
    # Esattamente 1 analizzata su 2
    assert meta["analyzed_evidence_count"] == 1
    assert meta["unanalyzed_evidence_count"] == 1


# ==============================================================================
# 2. SALVATAGGIO E RECUPERO DEI SOTTO-BLOCCHI RIUSCITI SU SQLITE
# ==============================================================================

def test_subchunk_persistence_and_recovery_without_duplicate_inference():
    """
    Riproduce e valida il difetto 2:
    - Run 1: Sub0 SUCCESS, Sub1 FAILED, Chunk 1 SUCCESS.
    - Checkpoint SQLite conserva Sub0 nella tabella `checkpoint_subchunks`.
    - Run 2 (Riavvio / Resumption):
      * Sub0 viene recuperato da SQLite con status RECOVERED_FROM_CHECKPOINT.
      * Nessuna richiesta LLM duplicata per Sub0 (0 repeat).
      * Sub1 viene ora eseguito con successo.
      * Chunk 1 viene recuperato da `checkpoint_chunks` senza chiamare l'LLM.
      * Conteggio esatto: 4/4 evidenze, stato COMPLETE.
    """
    doc = _build_test_document("doc_sub_rec_4", num_messages=4)
    tmp_dir = tempfile.mkdtemp(prefix="test_chk_sub_rec_")
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_dir)

    sub1_should_succeed = False
    calls_for_sub0 = 0
    calls_for_sub1 = 0
    calls_for_chunk1 = 0

    def _mock_handler(call_info: dict[str, Any], call_count: int) -> LlmCompletionResponse:
        nonlocal calls_for_sub0, calls_for_sub1, calls_for_chunk1
        user_text = call_info["user_content"]

        if "msg_doc_sub_rec_4_0" in user_text and "msg_doc_sub_rec_4_1" in user_text:
            err = AiBackendRequestError("Context overflow on Chunk 0")
            err.error_category = "CONTEXT_OVERFLOW"
            raise err

        if "msg_doc_sub_rec_4_0" in user_text and "msg_doc_sub_rec_4_1" not in user_text:
            calls_for_sub0 += 1
            return LlmCompletionResponse(
                content=json.dumps({
                    "topics": [
                        {
                            "label": "Topic Sub0",
                            "short_description": "Desc Sub0",
                            "evidence_ids": ["msg_doc_sub_rec_4_0::ORIGINAL_TEXT"],
                        }
                    ]
                }),
                model="qwen2.5-7b-instruct",
                raw_response={"finish_reason": "stop"},
            )

        if "msg_doc_sub_rec_4_1" in user_text and "msg_doc_sub_rec_4_0" not in user_text:
            calls_for_sub1 += 1
            if not sub1_should_succeed:
                raise AiBackendRequestError("Temporary network glitch on Sub1")
            return LlmCompletionResponse(
                content=json.dumps({
                    "topics": [
                        {
                            "label": "Topic Sub1",
                            "short_description": "Desc Sub1",
                            "evidence_ids": ["msg_doc_sub_rec_4_1::ORIGINAL_TEXT"],
                        }
                    ]
                }),
                model="qwen2.5-7b-instruct",
                raw_response={"finish_reason": "stop"},
            )

        if "msg_doc_sub_rec_4_2" in user_text:
            calls_for_chunk1 += 1
            return LlmCompletionResponse(
                content=json.dumps({
                    "topics": [
                        {
                            "label": "Topic Chunk 1",
                            "short_description": "Desc Chunk 1",
                            "evidence_ids": ["msg_doc_sub_rec_4_2::ORIGINAL_TEXT", "msg_doc_sub_rec_4_3::ORIGINAL_TEXT"],
                        }
                    ]
                }),
                model="qwen2.5-7b-instruct",
                raw_response={"finish_reason": "stop"},
            )

        raise ValueError("Unexpected prompt")

    client = ScriptableMockLlmClient(handler=_mock_handler)
    service = LocalTopicAnalysisService(
        client=client,
        checkpoint_store=store,
        enable_checkpointing=True,
        max_chunk_bundles=2,
    )

    # RUN 1: Sub0 SUCCESS, Sub1 FAILED, Chunk 1 SUCCESS
    res1 = service.run_topic_discovery(
        document=doc,
        client=client,
        model_name="qwen2.5-7b-instruct",
    )
    assert res1.metadata["status"] == "PARTIAL"
    assert calls_for_sub0 == 1
    assert calls_for_sub1 == 1
    assert calls_for_chunk1 == 1

    # Verifica persistenza in SQLite: Sub0 salvato con parent_chunk_index=0, subchunk_index=0
    doc_fp = compute_document_fingerprint(doc)
    chk_meta = store.get_latest_checkpoint_metadata_for_document(doc.document_id, doc_fp)
    assert chk_meta is not None
    cfg_fp = chk_meta["config_fingerprint"]
    saved_subs = store.get_subchunks(doc_fp, cfg_fp, parent_chunk_index=0)
    assert len(saved_subs) == 1
    assert saved_subs[0]["subchunk_index"] == 0
    assert saved_subs[0]["parent_chunk_index"] == 0
    assert "msg_doc_sub_rec_4_0::ORIGINAL_TEXT" in saved_subs[0]["evidence_ids"]

    # RUN 2: Resumption con sub1 che ora ha successo
    sub1_should_succeed = True
    # Creiamo un nuovo service con lo stesso store per simulare riavvio
    service2 = LocalTopicAnalysisService(
        client=client,
        checkpoint_store=store,
        enable_checkpointing=True,
        max_chunk_bundles=2,
    )

    res2 = service2.run_topic_discovery(
        document=doc,
        client=client,
        model_name="qwen2.5-7b-instruct",
        retry_failed_only=True,
    )

    # Verifica zero repeat su Sub0 e su Chunk 1!
    assert calls_for_sub0 == 1, "Sub0 non doveva essere richiamato (zero repeat da checkpoint)"
    assert calls_for_chunk1 == 1, "Chunk 1 non doveva essere richiamato (zero repeat da checkpoint)"
    assert calls_for_sub1 == 2, "Sub1 doveva essere ritentato ed eseguito"

    assert res2.metadata["status"] == "COMPLETE"
    assert res2.metadata["total_evidence_count"] == 4
    assert res2.metadata["analyzed_evidence_count"] == 4
    assert res2.metadata["unanalyzed_evidence_count"] == 0


# ==============================================================================
# 3. RIPRISTINO STORICO COERENTE CON MODELLO E CONFIGURAZIONE
# ==============================================================================

def test_historical_restore_distinguishes_models_and_blocks_incompatible_retry():
    """
    Riproduce e valida il difetto 3:
    - Risultato salvato con Qwen2.5-7B.
    - Ripristino richiesto con Llama 3.1:
      * default (allow_incompatible_model=False) -> restituisce None.
      * allow_incompatible_model=True -> restituisce il risultato contrassegnato con:
        is_model_compatible=False, is_compatible_for_retry=False.
    - Tentativo di riprova selettiva con modello incompatibile solleva OperationalAnalysisFailedError.
    """
    doc = _build_test_document("doc_model_incompat", num_messages=2)
    tmp_dir = tempfile.mkdtemp(prefix="test_chk_model_")
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_dir)

    def _mock_handler(call_info: dict[str, Any], call_count: int) -> LlmCompletionResponse:
        return LlmCompletionResponse(
            content=json.dumps({
                "topics": [
                    {
                        "label": "Qwen Topic",
                        "short_description": "Found by Qwen",
                        "evidence_ids": ["msg_doc_model_incompat_0::ORIGINAL_TEXT"],
                    }
                ]
            }),
            model="qwen2.5-7b-instruct",
            raw_response={"finish_reason": "stop"},
        )

    client = ScriptableMockLlmClient(handler=_mock_handler, model_id="qwen2.5-7b-instruct")
    service = LocalTopicAnalysisService(
        client=client,
        checkpoint_store=store,
        enable_checkpointing=True,
    )

    # Esegui discovery con Qwen
    res_qwen = service.run_topic_discovery(
        document=doc,
        client=client,
        model_name="qwen2.5-7b-instruct",
    )
    assert res_qwen.metadata["model"] == "qwen2.5-7b-instruct"

    # 1. Ripristino chiedendo Llama 3.1 senza allow_incompatible_model -> deve restituire None!
    res_llama_strict = service.load_persisted_discovery_result(
        document=doc,
        model_name="llama-3.1-8b-instruct",
        allow_incompatible_model=False,
    )
    assert res_llama_strict is None, "Non deve restituire un checkpoint Qwen se è richiesto Llama!"

    # 2. Ripristino chiedendo Llama 3.1 con allow_incompatible_model=True (per visualizzazione UI dello storico)
    res_llama_history = service.load_persisted_discovery_result(
        document=doc,
        model_name="llama-3.1-8b-instruct",
        allow_incompatible_model=True,
    )
    assert res_llama_history is not None
    assert res_llama_history.metadata["is_model_compatible"] is False
    assert res_llama_history.metadata["is_compatible_for_retry"] is False
    assert res_llama_history.metadata["historical_model"] == "qwen2.5-7b-instruct"
    assert res_llama_history.metadata["requested_model"] == "llama-3.1-8b-instruct"

    # 3. Riprova selettiva con modello diverso (Llama) deve fallire in modo controllato
    llama_client = ScriptableMockLlmClient(handler=_mock_handler, model_id="llama-3.1-8b-instruct")
    with pytest.raises(OperationalAnalysisFailedError) as exc_info:
        service.run_topic_discovery(
            document=doc,
            client=llama_client,
            model_name="llama-3.1-8b-instruct",
            retry_failed_only=True,
        )
    assert "nessun checkpoint compatibile trovato" in str(exc_info.value).lower()


# ==============================================================================
# 4. NON PRESUMERE IL FINISH REASON
# ==============================================================================

def test_finish_reason_preservation_when_backend_omits_or_null():
    """
    Riproduce e valida il difetto 4:
    - Backend restituisce risposta valida senza campo finish_reason (o con None).
    - La diagnostica e TopicDiscoveryResult.metadata registrano finish_reason=None (NON 'stop').
    - Salvataggio e caricamento da checkpoint preservano finish_reason=None senza inventare 'stop'.
    """
    doc = _build_test_document("doc_finish_reason_test", num_messages=2)
    tmp_dir = tempfile.mkdtemp(prefix="test_chk_finish_")
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_dir)

    def _mock_handler(call_info: dict[str, Any], call_count: int) -> LlmCompletionResponse:
        return LlmCompletionResponse(
            content=json.dumps({
                "topics": [
                    {
                        "label": "Topic No Finish Reason",
                        "short_description": "Valid topic",
                        "evidence_ids": ["msg_doc_finish_reason_test_0::ORIGINAL_TEXT"],
                    }
                ]
            }),
            model="qwen2.5-7b-instruct",
            # Nessun finish_reason fornito da backend
            raw_response={"model": "qwen2.5-7b-instruct"},
        )

    client = ScriptableMockLlmClient(handler=_mock_handler)
    service = LocalTopicAnalysisService(
        client=client,
        checkpoint_store=store,
        enable_checkpointing=True,
    )

    res = service.run_topic_discovery(
        document=doc,
        client=client,
        model_name="qwen2.5-7b-instruct",
    )

    # 1. Verifica TopicDiscoveryResult.metadata
    assert res.metadata.get("finish_reason") is None, "finish_reason non deve assumere 'stop' se il backend non lo fornisce!"

    # 2. Verifica ChunkDiagnosticRecord
    diags = res.metadata.get("chunk_diagnostics", [])
    assert len(diags) > 0
    assert diags[0].get("finish_reason") is None

    # 3. Verifica diagnostica persistita su SQLite
    doc_fp = compute_document_fingerprint(doc)
    chk_meta = store.get_latest_checkpoint_metadata_for_document(doc.document_id, doc_fp)
    assert chk_meta is not None
    cfg_fp = chk_meta["config_fingerprint"]
    sqlite_diags = store.get_chunk_diagnostics(doc_fp, cfg_fp)
    assert len(sqlite_diags) > 0
    assert sqlite_diags[0].finish_reason is None

    # 4. Verifica recupero da checkpoint: non deve inventare 'stop'
    recovered_res = service.load_persisted_discovery_result(
        document=doc,
        model_name="qwen2.5-7b-instruct",
    )
    assert recovered_res is not None
    rec_diags = recovered_res.metadata.get("chunk_diagnostics", [])
    assert len(rec_diags) > 0
    assert rec_diags[0].get("finish_reason") is None


def test_subchunk_revalidation_tampered_evidence_rejected():
    """
    Verifica che un sotto-blocco persistito ma con evidenze manomesse o appartenenti
    ad altri blocchi venga tassativamente rigettato (fail-closed) durante la rivalidazione forense.
    """
    doc = _build_test_document("doc_tamper_test", num_messages=4)
    tmp_dir = tempfile.mkdtemp(prefix="test_chk_tamper_")
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_dir)

    doc_fp = compute_document_fingerprint(doc)
    cfg_fp = "test_config_fingerprint_tamper_123"

    # Salva un sotto-blocco con evidenza legittima
    legit_sub_res = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic(
                label="Legit Topic",
                short_description="Legit description",
                evidence_ids=("msg_doc_tamper_test_0::ORIGINAL_TEXT",),
            ),
        ),
        provenance_document_id=doc.document_id,
        metadata={"finish_reason": "stop"},
    )
    store.save_subchunk(
        document_id=doc.document_id,
        document_fingerprint=doc_fp,
        config_fingerprint=cfg_fp,
        parent_chunk_index=0,
        subchunk_index=0,
        total_subchunks=2,
        result=legit_sub_res,
        subchunk_id="subchunk_0",
        evidence_ids=("msg_doc_tamper_test_0::ORIGINAL_TEXT",),
        total_planned_chunks=2,
        analysis_type="discovery",
        model_id="qwen2.5-7b-instruct",
        prompt_version="discovery_operational",
    )

    chunks = [
        ConversationEvidenceDocument(
            document_id="doc_tamper_test_c0",
            bundles=doc.bundles[:2],
            source_name=doc.source_name,
        ),
        ConversationEvidenceDocument(
            document_id="doc_tamper_test_c1",
            bundles=doc.bundles[2:],
            source_name=doc.source_name,
        ),
    ]
    chk_valid = store.load_and_validate_checkpoint(
        document=doc,
        config_fingerprint=cfg_fp,
        chunks=chunks,
        expected_analysis_type="discovery",
    )
    assert chk_valid is not None
    assert len(chk_valid.subchunks) == 1

    # Manomissione in SQLite: inseriamo un evidence ID allucinato/alieno che non appartiene al chunk 0
    with store._connect() as conn:
        conn.execute("""
            UPDATE checkpoint_subchunks
            SET evidence_ids = ?
            WHERE document_fingerprint = ? AND config_fingerprint = ?;
        """, (json.dumps(["ev_ALIEN_HALLUCINATED_ID::ORIGINAL_TEXT"]), doc_fp, cfg_fp))

    # Rivalidazione: deve fallire e restituire None!
    chk_tampered = store.load_and_validate_checkpoint(
        document=doc,
        config_fingerprint=cfg_fp,
        chunks=chunks,
        expected_analysis_type="discovery",
    )
    assert chk_tampered is None, "Il checkpoint con sotto-blocco manomesso deve essere rigettato (fail-closed)!"

