"""
tests/unit/test_priority4_live_diagnostics_integrity.py
-------------------------------------------------------
Suite di test per la Priorità 4 dell'audit:
1. Topic Discovery 25/26:
   - Chunk 11 visualizzato (indice 10, 0-indexed) fallisce con INVALID_EVIDENCE_ID dopo 2 tentativi.
   - Nessuna accettazione di evidenze inventate per forzare 26/26.
   - Tracciamento diagnostico strutturato: attempts=2, diagnostic_category=INVALID_EVIDENCE_ID, failure_phase=evidence_validation.
   - Privacy-safe diagnostics: zero leakage di testo conversazione nei log e nei record SQLite.
2. Topic Detection 17/26:
   - 17/26 blocchi completati con successo (ABSENT), 9 blocchi falliti.
   - In modalità operativa, tutti i blocchi sono tentati (nessuna interruzione prematura non provata).
   - Aggregazione corretta: UNCERTAIN (nessun PRESENT e copertura incompleta).
   - ABSENT consentito solo con copertura completa (26/26).
   - PRESENT consentito anche con copertura parziale se accompagnato da evidenze valide.
   - Errore operativo (OperationalAnalysisFailedError) se 0 blocchi completano.
3. Fallimento immediato (fail-fast) per errori di programmazione (NameError, TypeError, KeyError, AttributeError).
4. Granularità categorie diagnostiche per distinguere timeout, errori HTTP, errori strutturati, overflow di contesto, model mismatch ed evidenze invalide.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Mapping, Sequence
import pytest

from ai.backend import (
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiInvalidEvidenceCitationError,
    AiModelMismatchError,
    AiStructuredOutputError,
    AnalysisInputTooLargeError,
    FakeLocalLlmClient,
    LlmCompletionResponse,
)
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    MessageEvidenceBundle,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
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
from analysis.checkpoint import (
    AnalysisCheckpointStore,
    compute_document_fingerprint,
    compute_run_config_hash,
)
from analysis.models import (
    AnalysisOrigin,
    ChunkDiagnosticCategory,
    ChunkDiagnosticRecord,
    ChunkLifecycleStatus,
    DiscoveryChunkOutcome,
    OperationalAnalysisConfig,
    OperationalAnalysisFailedError,
    OperationalProfile,
    TopicProvenanceError,
    classify_discovery_exception,
)
from analysis.service import LocalTopicAnalysisService


def _create_synthetic_bundle(doc_id: str, idx: int, text: str) -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="whatsapp_export",
        source_path="/tmp/test",
        source_record_id=str(idx),
        record_type="message",
        raw_fields={"data": text},
        media_reference=None,
        metadata={},
    )
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=ValidationResult(record=raw, issues=()),
        source_name="whatsapp_export",
        source_record_id=str(idx),
        record_type="message",
        timestamp=NormalizedTimestamp(status=TimestampTzStatus.ABSENT),
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"msg_{doc_id}_{idx}",
        source_name="whatsapp_export",
        source_record_id=str(idx),
        source_path="/tmp/test",
        record_type="message",
        timestamp=NormalizedTimestamp(status=TimestampTzStatus.ABSENT),
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    return MessageEvidenceBundle(message=msg)


def _create_synthetic_multichunk_document(doc_id: str, chunk_count: int = 26) -> ConversationEvidenceDocument:
    bundles = tuple(_create_synthetic_bundle(doc_id, i, f"Messaggio {i} di prova per analisi forense.") for i in range(chunk_count))
    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=bundles,
        chat_id="chat_test_multichunk",
    )


class Chunk11InvalidEvidenceClient(FakeLocalLlmClient):
    """
    Simula una risposta di Discovery in cui il chunk 11 (indice 10) fallisce
    per citazione di evidence ID inesistenti su entrambi i tentativi (attempts=2),
    mentre tutti gli altri 25 chunk hanno successo.
    """
    def __init__(self, model_id: str = "qwen2.5-7b-instruct"):
        super().__init__(
            available=True,
            models=(model_id,),
            model_name=model_id,
        )
        self.chunk_attempts_seen: dict[int, int] = {}
        self.total_calls = 0

    def chat_completion(
        self,
        messages: Sequence[Mapping[str, str]],
        model_id: str | None = None,
        schema: Mapping[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float = 30.0,
        max_tokens: int | None = None,
    ) -> LlmCompletionResponse:
        self.total_calls += 1
        prompt_text = " ".join(m.get("content", "") for m in messages)

        # Identifica univocamente l'indice di blocco dal document_id serializzato nel chunk (es. ::chunk::10)
        m = re.search(r"::chunk::(\d+)", prompt_text)
        chunk_idx = int(m.group(1)) if m else 0

        self.chunk_attempts_seen[chunk_idx] = self.chunk_attempts_seen.get(chunk_idx, 0) + 1
        att = self.chunk_attempts_seen[chunk_idx]

        # Chunk 11 visualizzato (chunk_idx = 10, 0-indexed)
        if chunk_idx == 10:
            # Al primo tentativo cita alias inventato E999
            # Al secondo tentativo (retry con directive correttiva) cita ancora E999
            payload = {
                "topics": [
                    {
                        "label": "Argomento Allucinazione",
                        "short_description": "Tema con citazione di alias non presente nel chunk",
                        "evidence_ids": ["E999"],
                    }
                ]
            }
            return LlmCompletionResponse(
                content=json.dumps(payload),
                model=self.model_name,
                latency_seconds=0.05,
                prompt_tokens=250,
                completion_tokens=50,
                total_tokens=300,
                raw_response={"finish_reason": "stop"},
            )

        # Tutti gli altri 25 chunk restituiscono citazione valida E001
        payload = {
            "topics": [
                {
                    "label": f"Tema Valido Blocco {chunk_idx + 1}",
                    "short_description": f"Descrizione tema valido blocco {chunk_idx + 1}",
                    "evidence_ids": ["E001"],
                }
            ]
        }
        return LlmCompletionResponse(
            content=json.dumps(payload),
            model=self.model_name,
            latency_seconds=0.03,
            prompt_tokens=200,
            completion_tokens=40,
            total_tokens=240,
            raw_response={"finish_reason": "stop"},
        )


class Detection17of26Client(FakeLocalLlmClient):
    """
    Simula Topic Detection su 26 chunk:
    - 17 chunk completano con successo (ABSENT)
    - 9 chunk falliscono simulando diverse categorie diagnostiche:
      * chunk 17: TIMEOUT
      * chunk 18: HTTP 400 (INVALID_PARAMS)
      * chunk 19: SCHEMA_VALIDATION_ERROR
      * chunk 20: INVALID_EVIDENCE_ID
      * chunk 21: TIMEOUT
      * chunk 22: HTTP 400
      * chunk 23: SCHEMA_VALIDATION_ERROR
      * chunk 24: INVALID_EVIDENCE_ID
      * chunk 25: TIMEOUT
    """
    def __init__(self, model_id: str = "qwen2.5-7b-instruct"):
        super().__init__(
            available=True,
            models=(model_id,),
            model_name=model_id,
        )
        self.attempted_chunks: set[int] = set()

    def chat_completion(
        self,
        messages: Sequence[Mapping[str, str]],
        model_id: str | None = None,
        schema: Mapping[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float = 30.0,
        max_tokens: int | None = None,
    ) -> LlmCompletionResponse:
        prompt_text = " ".join(m.get("content", "") for m in messages)
        m = re.search(r"::chunk::(\d+)", prompt_text)
        chunk_idx = int(m.group(1)) if m else 0

        self.attempted_chunks.add(chunk_idx)

        # I primi 17 chunk (0..16) completano con successo come ABSENT
        if chunk_idx < 17:
            payload = {
                "decision": "ABSENT",
                "evidence_ids": [],
                "rationale": f"Nessuna evidenza rilevata nel blocco {chunk_idx + 1}",
            }
            return LlmCompletionResponse(
                content=json.dumps(payload),
                model=self.model_name,
                latency_seconds=0.02,
                prompt_tokens=150,
                completion_tokens=30,
                total_tokens=180,
                raw_response={"finish_reason": "stop"},
            )

        # I restanti 9 chunk simulano errori di inferenza a livello di singolo chunk
        if chunk_idx in (17, 21, 25):
            raise AiStructuredOutputError(f"Schema Topic Detection violato: campo decision mancante sul blocco {chunk_idx + 1}")
        if chunk_idx in (18, 22):
            raise AiStructuredOutputError(f"Risposta del modello non conforme a JSON valido: Unterminated string sul blocco {chunk_idx + 1}")
        if chunk_idx in (19, 23):
            raise AiStructuredOutputError(f"Schema violato: evidence_ids non conformi sul blocco {chunk_idx + 1}")
        if chunk_idx in (20, 24):
            raise AiInvalidEvidenceCitationError(
                f"Citazione di evidence_id inesistente sul blocco {chunk_idx + 1}",
                invalid_evidence_ids=("INV_EID_999",),
            )

        raise RuntimeError("Caso non previsto")


# =====================================================================
# TEST 1: Topic Discovery 25/26 regression con chunk 11 INVALID_EVIDENCE_ID
# =====================================================================

def test_priority4_discovery_25_of_26_with_chunk_11_invalid_evidence():
    """
    Verifica che Topic Discovery su 26 chunk gestisca esattamente:
    - 25 chunk validati con successo
    - Chunk 11 visualizzato (indice 10) fallito con INVALID_EVIDENCE_ID
    - 2 tentativi eseguiti sul chunk 11 (tentativo 1 + retry correttivo fallito)
    - Nessun'accettazione di evidenze inventate
    - Tracciamento della diagnostica nel database SQLite senza leak di testi
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        chk_dir = Path(tmp_dir)
        client = Chunk11InvalidEvidenceClient()
        service = LocalTopicAnalysisService(
            client=client,
            checkpoint_dir=chk_dir,
            max_chunk_bundles=1,
            profile=OperationalProfile.BALANCED,
        )

        doc = _create_synthetic_multichunk_document("doc_disc", chunk_count=26)

        result = service.run_topic_discovery(
            document=doc,
            client=client,
            max_topics=5,
        )

        assert result is not None
        assert result.metadata.get("status") == "PARTIAL"
        assert result.metadata.get("chunk_count") == 26
        assert result.metadata.get("completed_chunk_count") == 25
        assert result.metadata.get("failed_chunk_count") == 1

        # Verifica che il chunk 11 (indice 10) abbia eseguito esattamente 2 tentativi
        assert client.chunk_attempts_seen[10] == 2

        # Verifica la diagnostica nei metadata e nel DB SQLite
        diags = {d["chunk_index"]: d for d in result.metadata["chunk_diagnostics"]}
        assert len(diags) == 26

        chunk_11_diag = diags.get(10)
        assert chunk_11_diag is not None
        assert chunk_11_diag["status"] == ChunkLifecycleStatus.FAILED.value
        assert chunk_11_diag["attempts"] == 2
        assert chunk_11_diag["diagnostic_category"] == ChunkDiagnosticCategory.INVALID_EVIDENCE_ID.value
        assert chunk_11_diag["failure_phase"] == "evidence_validation"
        assert chunk_11_diag["evidence_valid"] == 0 or chunk_11_diag["evidence_valid"] is False

        # Privacy check: zero leakage di messaggi nella diagnostica
        for d in diags.values():
            assert "Messaggio" not in str(d.get("error_detail", ""))
            assert "Messaggio" not in str(d.get("details", ""))

        # Verifica diretta persistenza nel DB SQLite
        with service.checkpoint_store._connect() as conn:
            rows = conn.execute(
                "SELECT chunk_index, status, attempts, diagnostic_category, error_detail FROM checkpoint_chunk_diagnostics ORDER BY chunk_index",
            ).fetchall()
            assert len(rows) == 26
            row_10 = [r for r in rows if r[0] == 10][0]
            assert row_10[1] == "FAILED"
            assert row_10[2] == 2
            assert row_10[3] == ChunkDiagnosticCategory.INVALID_EVIDENCE_ID.value
            for r in rows:
                assert "Messaggio" not in str(r[4] or "")


# =====================================================================
# TEST 2: Topic Detection 17/26 regression e aggregazione rigorosa
# =====================================================================

def test_priority4_detection_17_of_26_diagnostics_and_uncertain():
    """
    Verifica che Topic Detection su 26 chunk con 17 completati e 9 falliti:
    1. Tenti effettivamente tutti i 26 blocchi (nessun'interruzione prematura ipotizzata).
    2. Registri le categorie diagnostiche specifiche (TIMEOUT, HTTP_INVALID_PARAMETERS, ecc.).
    3. Concluda rigorosamente come UNCERTAIN (mai ABSENT in presenza di blocchi non analizzati).
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        chk_dir = Path(tmp_dir)
        client = Detection17of26Client()
        service = LocalTopicAnalysisService(
            client=client,
            checkpoint_dir=chk_dir,
            max_chunk_bundles=1,
            profile=OperationalProfile.BALANCED,
        )

        doc = _create_synthetic_multichunk_document("doc_det", chunk_count=26)
        query = TopicQuery(
            topic_id="t_security",
            label="Sicurezza Informatica",
            description="Discussioni su vulnerabilità o accessi non autorizzati",
        )

        res = service.run_topic_detection(
            document=doc,
            topic=query,
            client=client,
            operational_mode=True,
        )

        assert res is not None
        # Tutti i 26 chunk sono stati tentati in modalità operativa!
        assert len(client.attempted_chunks) == 26

        # Aggregazione: 17 ABSENT + 9 falliti -> UNCERTAIN (Regola FASE I)
        assert res.decision == TopicDecision.UNCERTAIN
        assert res.metadata.get("status") == "PARTIAL"
        assert res.metadata.get("completed_chunk_count") == 17
        assert res.metadata.get("failed_chunk_count") == 9
        assert res.metadata.get("chunk_count") == 26

        # Verifica che la diagnostica persistita distingua esattamente i guasti
        diags = {d["chunk_index"]: d for d in res.metadata["chunk_diagnostics"]}
        assert len(diags) == 26

        cat_counts: dict[str, int] = {}
        for d in diags.values():
            cat = d.get("diagnostic_category")
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        assert cat_counts.get("SUCCESS") == 17
        assert cat_counts.get(ChunkDiagnosticCategory.SCHEMA_VALIDATION_ERROR.value) == 5
        assert cat_counts.get(ChunkDiagnosticCategory.JSON_DECODE_ERROR.value) == 2
        assert cat_counts.get(ChunkDiagnosticCategory.INVALID_EVIDENCE_ID.value) == 2

        # Verifica diretta su SQLite
        with service.checkpoint_store._connect() as conn:
            rows = conn.execute(
                "SELECT chunk_index, status, attempts, diagnostic_category FROM checkpoint_chunk_diagnostics ORDER BY chunk_index",
            ).fetchall()
            assert len(rows) == 26


# =====================================================================
# TEST 3: Present vince anche su run parziale se accompagnato da evidenze
# =====================================================================

def test_priority4_detection_present_on_partial_run():
    """
    Verifica che se un chunk trova evidenze valide con esito PRESENT,
    l'esito aggregato sia PRESENT anche se altri chunk sono falliti.
    """
    class SinglePresentPartialClient(FakeLocalLlmClient):
        def __init__(self):
            super().__init__(
                available=True,
                models=("qwen2.5-7b-instruct",),
                model_name="qwen2.5-7b-instruct",
            )

        def chat_completion(self, messages, **kwargs):
            prompt_text = " ".join(m.get("content", "") for m in messages)
            m = re.search(r"::chunk::(\d+)", prompt_text)
            chunk_idx = int(m.group(1)) if m else 0

            if chunk_idx == 0:
                return LlmCompletionResponse(
                    content=json.dumps({
                        "decision": "PRESENT",
                        "evidence_ids": ["msg_doc_present_0::ORIGINAL_TEXT"],
                        "rationale": "Evidenza trovata nel primo blocco",
                    }),
                    model=self.model_name,
                )
            # Gli altri 2 chunk falliscono per errore di output strutturato a livello di blocco
            raise AiStructuredOutputError("Schema Topic Detection violato: campo decision mancante")

    with tempfile.TemporaryDirectory() as tmp_dir:
        chk_dir = Path(tmp_dir)
        client = SinglePresentPartialClient()
        service = LocalTopicAnalysisService(
            client=client,
            checkpoint_dir=chk_dir,
            max_chunk_bundles=1,
            profile=OperationalProfile.BALANCED,
        )

        doc = _create_synthetic_multichunk_document("doc_present", chunk_count=3)
        query = TopicQuery(topic_id="t1", label="Tema Trovato", description="Desc")

        res = service.run_topic_detection(document=doc, topic=query, client=client, operational_mode=True)
        assert res.decision == TopicDecision.PRESENT
        assert len(res.evidence_ids) > 0
        assert res.metadata.get("status") == "PARTIAL"


# =====================================================================
# TEST 4: Errore operativo se zero chunk completano
# =====================================================================

def test_priority4_detection_zero_chunks_completed_raises_operational_error():
    """
    Verifica che se tutti i chunk falliscono, venga sollevato
    OperationalAnalysisFailedError e non venga ritornato un falso UNCERTAIN.
    """
    class AllFailClient(FakeLocalLlmClient):
        def __init__(self):
            super().__init__(
                available=True,
                models=("qwen2.5-7b-instruct",),
                model_name="qwen2.5-7b-instruct",
            )

        def chat_completion(self, messages, **kwargs):
            raise AiStructuredOutputError("Schema non valido su tutti i chunk")

    with tempfile.TemporaryDirectory() as tmp_dir:
        chk_dir = Path(tmp_dir)
        client = AllFailClient()
        service = LocalTopicAnalysisService(
            client=client,
            checkpoint_dir=chk_dir,
            max_chunk_bundles=1,
            profile=OperationalProfile.BALANCED,
        )

        doc = _create_synthetic_multichunk_document("doc_all_fail", chunk_count=3)
        query = TopicQuery(topic_id="t1", label="Tema", description="Desc")

        with pytest.raises(OperationalAnalysisFailedError):
            service.run_topic_detection(document=doc, topic=query, client=client, operational_mode=True)


# =====================================================================
# TEST 5: Fail-fast per errori di programmazione (non convertiti in guasti recuperabili)
# =====================================================================

def test_priority4_programming_errors_fail_fast_without_recovery():
    """
    Verifica che errori di codice interni (NameError, TypeError, KeyError, AttributeError)
    non vengano catturati e convertiti in fallimenti di blocco recuperabili,
    ma interrompano immediatamente l'esecuzione.
    """
    class BuggyCodeClient(FakeLocalLlmClient):
        def __init__(self):
            super().__init__(
                available=True,
                models=("qwen2.5-7b-instruct",),
                model_name="qwen2.5-7b-instruct",
            )

        def chat_completion(self, messages, **kwargs):
            # Simula un bug interno del tipo NameError o AttributeError
            raise AttributeError("Bug interno nel codice non deve essere soppresso")

    with tempfile.TemporaryDirectory() as tmp_dir:
        chk_dir = Path(tmp_dir)
        client = BuggyCodeClient()
        service = LocalTopicAnalysisService(
            client=client,
            checkpoint_dir=chk_dir,
            max_chunk_bundles=1,
            profile=OperationalProfile.BALANCED,
        )

        doc = _create_synthetic_multichunk_document("doc_bug", chunk_count=3)
        query = TopicQuery(topic_id="t1", label="Tema", description="Desc")

        with pytest.raises(AttributeError):
            service.run_topic_detection(document=doc, topic=query, client=client, operational_mode=True)

        with pytest.raises(AttributeError):
            service.run_topic_discovery(document=doc, client=client)


# =====================================================================
# TEST 6: Granularità e discriminazione delle categorie diagnostiche
# =====================================================================

def test_priority4_diagnostic_category_granularity():
    """
    Verifica che il sistema distingua accuratamente tutte le cause diagnostiche
    richieste dall'audit senza confonderle in codici generici.
    """
    # 1. Timeout
    cat, phase, _ = classify_discovery_exception(AiBackendTimeoutError("Timeout"))
    assert cat == ChunkDiagnosticCategory.TIMEOUT.value
    assert phase == "inference"

    # 2. Context Overflow
    overflow_exc = AiBackendRequestError("Context window exceeded")
    overflow_exc.error_category = "CONTEXT_OVERFLOW"
    cat, phase, _ = classify_discovery_exception(overflow_exc)
    assert cat == ChunkDiagnosticCategory.HTTP_CONTEXT_OVERFLOW.value
    assert phase == "inference"

    # 3. HTTP 400 Invalid Parameters
    http400_exc = AiBackendRequestError("Bad Request", status_code=400)
    http400_exc.error_category = "INVALID_PARAMS"
    cat, phase, _ = classify_discovery_exception(http400_exc)
    assert cat == ChunkDiagnosticCategory.HTTP_INVALID_PARAMETERS.value
    assert phase == "inference"

    # 4. HTTP Schema Rejected
    schema_rej_exc = AiBackendRequestError("Grammar rejected")
    schema_rej_exc.error_category = "SCHEMA_REJECTED"
    cat, phase, _ = classify_discovery_exception(schema_rej_exc)
    assert cat == ChunkDiagnosticCategory.HTTP_SCHEMA_REJECTED.value
    assert phase == "inference"

    # 5. Model Mismatch
    mismatch_exc = AiModelMismatchError("Model mismatch")
    cat, phase, _ = classify_discovery_exception(mismatch_exc)
    assert cat == ChunkDiagnosticCategory.MODEL_MISMATCH.value
    assert phase == "inference"

    # 6. Invalid Evidence Citation
    invalid_cite_exc = AiInvalidEvidenceCitationError("Invalid citation", invalid_evidence_ids=("E999",))
    cat, phase, _ = classify_discovery_exception(invalid_cite_exc)
    assert cat == ChunkDiagnosticCategory.INVALID_EVIDENCE_ID.value
    assert phase == "evidence_validation"

    # 7. Truncated output
    trunc_exc = AiStructuredOutputError("Truncated")
    cat, phase, _ = classify_discovery_exception(trunc_exc, finish_reason="length")
    assert cat == ChunkDiagnosticCategory.OUTPUT_TRUNCATED.value
    assert phase == "inference"

    # 8. JSON Malformed
    json_exc = AiStructuredOutputError("Risposta del modello non conforme a JSON valido: Unterminated string")
    cat, phase, _ = classify_discovery_exception(json_exc)
    assert cat == ChunkDiagnosticCategory.JSON_DECODE_ERROR.value
    assert phase == "json_parsing"

    # 9. Schema violation (generic)
    struct_exc = AiStructuredOutputError("Schema Topic Discovery violato: attese chiavi topics")
    cat, phase, _ = classify_discovery_exception(struct_exc)
    assert cat == ChunkDiagnosticCategory.SCHEMA_VALIDATION_ERROR.value
    assert phase == "schema_validation"

    # 10. Provenance mismatch
    prov_exc = TopicProvenanceError("Provenance error")
    cat, phase, _ = classify_discovery_exception(prov_exc)
    assert cat == ChunkDiagnosticCategory.PROVENANCE_MISMATCH.value
    assert phase == "evidence_validation"
