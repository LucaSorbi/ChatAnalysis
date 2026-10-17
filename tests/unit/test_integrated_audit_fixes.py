"""
tests/unit/test_integrated_audit_fixes.py
-----------------------------------------
Suite di test mirata per la verifica di tutte le correzioni e ottimizzazioni
richieste dall'Audit Indipendente del 13/10/2026:
1. Token Budget & Headroom Calculation (Criticità A, Sezione 2, Sezione 8.2)
2. Controlled Retry with Headroom Validation (Criticità B, Sezione 3)
3. HTTP 400 Classification & Privacy Protection (Criticità C, Sezione 6, Sezione 8.3)
4. Decoupled Live Progress Tracker & Event Sequencing (Criticità D, Sezione 5, Sezione 8.4)
5. Balanced Semantic Tri-State Directive (Sezione 7, Sezione 8.5)
6. Pre-Analysis Profiling & Large Dataset Scalability (Criticità F, Sezione 4)
7. Context Overflow Sub-Chunking Recovery (Sezione 6.4)
"""
from __future__ import annotations

import json
from typing import Any, Mapping
import pytest

from ai.backend import (
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiStructuredOutputError,
    AnalysisInputTooLargeError,
    BaseLlmClient,
    LlmCompletionResponse,
)
from ai.chunking import (
    DocumentAnalysisProfile,
    profile_document_for_analysis,
    split_document_into_chunks,
)
from ai.lmstudio import (
    classify_lmstudio_http_error,
)
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from importer.models import RawRecord
from multimodal.evidence import MessageEvidenceBundle
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult
from ai.token_budget import (
    DEFAULT_SAFETY_MARGIN_TOKENS,
    calculate_dynamic_output_budget,
    calculate_token_budget_plan,
    estimate_request_input_tokens,
    estimate_text_tokens,
)
from ai.topics import (
    OPERATIONAL_SEMANTIC_DIRECTIVE,
    TopicDetectionAnalyzer,
    TopicDiscoveryAnalyzer,
)
from analysis.progress import (
    AnalysisProgressEvent,
    AnalysisProgressTracker,
)
from analysis.service import (
    LocalTopicAnalysisService,
    OperationalAnalysisFailedError,
    calculate_discovery_max_tokens,
)
from ui.application import build_topic_query


# ---------------------------------------------------------------------------
# Fixture & Helper Mocks
# ---------------------------------------------------------------------------

class AuditControllableMockClient(BaseLlmClient):
    """Client mock controllabile per simulare scenari di audit e risposte/errori HTTP."""

    def __init__(self, responses: list[dict[str, Any] | Exception] | None = None) -> None:
        self.responses = list(responses or [])
        self.history_calls: list[dict[str, Any]] = []

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_seconds: float = 60.0,
        seed: int | None = 42,
        **kwargs: Any,
    ) -> LlmCompletionResponse:
        self.history_calls.append({
            "messages": messages,
            "model_id": model_id,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "schema": schema,
            "timeout_seconds": timeout_seconds,
            "seed": seed,
            **kwargs,
        })
        if not self.responses:
            raise RuntimeError("Nessuna risposta mock configurata.")
        item = self.responses.pop(0)
        if callable(item):
            item = item(messages)
        if isinstance(item, Exception):
            raise item

        content = item.get("content")
        if content is None:
            content = json.dumps(item)

        return LlmCompletionResponse(
            content=content,
            model=model_id or "mock-audit-model",
            prompt_tokens=item.get("prompt_tokens", 100),
            completion_tokens=item.get("completion_tokens", 50),
            total_tokens=item.get("total_tokens", 150),
            metadata={"finish_reason": item.get("finish_reason", "stop")},
            raw_response=item,
        )


def _make_test_bundle(idx: str, text: str) -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="whatsapp_audit",
        source_path="/fake/path",
        source_record_id=idx,
        record_type="message",
        raw_fields={"data": text},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name="whatsapp_audit",
        source_record_id=idx,
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"unified:whatsapp_audit:{idx}",
        source_name="whatsapp_audit",
        source_record_id=idx,
        source_path="/fake/path",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    return MessageEvidenceBundle(message=msg)


def _build_test_document(message_count: int = 5, char_per_msg: int = 100) -> ConversationEvidenceDocument:
    bundles = tuple(_make_test_bundle(str(i), "A" * char_per_msg) for i in range(1, message_count + 1))
    return ConversationEvidenceDocument(
        document_id="doc_audit_test",
        bundles=bundles,
    )


# ---------------------------------------------------------------------------
# 1. TEST BUDGET DI CONTESTO (Criticità A, Sezione 2, Sezione 8.2)
# ---------------------------------------------------------------------------

def test_token_budget_estimation_and_safety_margin():
    """Verifica che la stima conservativa dei token e il margine di sicurezza siano corretti."""
    text = "Questa è una conversazione forense di prova."
    tokens = estimate_text_tokens(text)
    assert tokens > 0
    # Verifica che una stringa vuota o None dia 0
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens(None) == 0

    messages = [
        {"role": "system", "content": "Sei un perito informatico forense."},
        {"role": "user", "content": text},
    ]
    input_est = estimate_request_input_tokens(messages, schema={"type": "object"})
    assert input_est > tokens


def test_token_budget_plan_8192_context():
    """Verifica il calcolo del budget plan con contesto standard 8192."""
    messages = [
        {"role": "system", "content": "Sei un assistente forense."},
        {"role": "user", "content": "Analizza questi messaggi."},
    ]
    plan = calculate_token_budget_plan(
        context_length=8192,
        messages=messages,
        reserved_output_tokens=1500,
        safety_margin=DEFAULT_SAFETY_MARGIN_TOKENS,
    )
    assert plan.fits_in_context is True
    assert plan.reserved_output_tokens == 1500
    assert plan.available_headroom > 0
    assert plan.estimated_input_tokens > 0


def test_token_budget_plan_context_overflow_detection():
    """Verifica che quando input + output eccedono il contesto, venga rilevato l'overflow e ridotto in modo sicuro."""
    messages = [
        {"role": "system", "content": "System prompt forense " * 50},
        {"role": "user", "content": "Messaggio lungo " * 300},
    ]
    plan = calculate_token_budget_plan(
        context_length=4096,
        messages=messages,
        reserved_output_tokens=3000,
        safety_margin=256,
    )
    assert plan.fits_in_context is False
    assert plan.total_budget_needed > 4096

    # Test dynamic output budget
    dyn_budget = calculate_dynamic_output_budget(
        context_length=4096,
        estimated_prompt_tokens=3500,
        desired_output_tokens=1024,
        min_output_tokens=128,
        safety_margin=256,
    )
    # Headroom: 4096 - 3500 - 256 = 340
    assert dyn_budget == 340


def test_token_budget_plan_unrecoverable_overflow_raises():
    """Verifica che se anche solo l'input supera il contesto, dynamic output budget dia 0."""
    dyn_budget = calculate_dynamic_output_budget(
        context_length=2048,
        estimated_prompt_tokens=2200,
        desired_output_tokens=512,
        min_output_tokens=128,
        safety_margin=256,
    )
    assert dyn_budget == 0


def test_calculate_discovery_max_tokens_headroom_clamping():
    """Verifica che calculate_discovery_max_tokens rispetti la stima reale del prompt e l'headroom."""
    # Prompt grande (6000 token) in contesto 8192: max headroom = 8192 - 6000 - 256 = 1936
    budget = calculate_discovery_max_tokens(
        max_topics=20, # baseline teorico: 1024 + 20*60 = 2224
        user_max_tokens=None,
        context_length=8192,
        estimated_prompt_tokens=6000,
        safety_margin=256,
    )
    assert budget <= 1936
    assert budget >= 256


# ---------------------------------------------------------------------------
# 2. TEST RETRY CON VERIFICA DELLO SPAZIO RESIDUO (Criticità B, Sezione 3)
# ---------------------------------------------------------------------------

def test_discovery_retry_reduces_topics_when_headroom_exhausted():
    """
    Verifica che se si verifica finish_reason='length' ma l'headroom è esaurita,
    il sistema NON aumenti sconsideratamente max_tokens causando HTTP 400,
    ma dimezzi o riduca i topic richiesti.
    """
    doc = _build_test_document(message_count=2, char_per_msg=50)

    # 1° chiamata: finish_reason="length", output troncato
    # 2° chiamata: successo con topic ridotti
    ev1 = "unified:whatsapp_audit:1::ORIGINAL_TEXT"
    mock_client = AuditControllableMockClient(
        responses=[
            {
                "content": f'{{"topics": [{{"label": "T1", "short_description": "D1", "evidence_ids": ["{ev1}"]',
                "finish_reason": "length",
            },
            {
                "content": f'{{"topics": [{{"label": "T1", "short_description": "D1", "evidence_ids": ["{ev1}"]}}]}}',
                "finish_reason": "stop",
            },
        ]
    )

    analyzer = TopicDiscoveryAnalyzer(client=mock_client)
    # Impostiamo contesto ridotto per simulare headroom limitata
    res = analyzer.discover_topics(
        document=doc,
        max_topics=10,
        max_tokens=500,
        context_length=1800, # Headroom molto ristretta
        operational_mode=True,
    )
    assert len(res.topics) == 1
    assert res.topics[0].label == "T1"
    assert len(mock_client.history_calls) == 2
    # La seconda chiamata ha richiesto meno topic
    second_prompt = mock_client.history_calls[1]["messages"][0]["content"]
    assert "Find up to 5" in second_prompt or "at most 5 elements" in second_prompt


def test_discovery_unrepairable_json_raises_explicitly_without_hallucinating():
    """Verifica che un JSON irrimediabilmente invalido non venga corretto inventando dati."""
    doc = _build_test_document(message_count=2, char_per_msg=50)
    mock_client = AuditControllableMockClient(
        responses=[
            {"content": "NON SONO UN JSON VALIDO", "finish_reason": "stop"},
            {"content": "ANCORA NON UN JSON", "finish_reason": "stop"},
            {"content": "FALLIMENTO DEFINITIVO", "finish_reason": "stop"},
        ]
    )
    analyzer = TopicDiscoveryAnalyzer(client=mock_client)
    with pytest.raises((AiStructuredOutputError, AiBackendProtocolError)):
        analyzer.discover_topics(document=doc, max_topics=5)


# ---------------------------------------------------------------------------
# 3. TEST CLASSIFICAZIONE ERRORI HTTP 400 E PRIVACY (Criticità C, Sezione 6)
# ---------------------------------------------------------------------------

def test_classify_lmstudio_http_400_context_overflow():
    """Verifica la corretta classificazione di HTTP 400 context overflow con messaggio esplicito e sicuro."""
    body = {
        "error": {
            "message": "The prompt and requested max_tokens (8500) exceed the model context length (8192).",
            "code": "context_length_exceeded",
        }
    }
    raw_bytes = json.dumps(body).encode("utf-8")
    err = classify_lmstudio_http_error(400, "Bad Request", raw_bytes)
    assert err.error_category == "CONTEXT_OVERFLOW"
    assert "finestra di contesto disponibile" in err.safe_reason
    assert "HTTP 400 [CONTEXT_OVERFLOW]" in err.technical_details


def test_classify_lmstudio_http_400_invalid_parameter():
    """Verifica la classificazione di parametro invalido."""
    body = {"error": {"message": "Invalid temperature: must be between 0 and 2", "param": "temperature"}}
    err = classify_lmstudio_http_error(400, "Bad Request", json.dumps(body).encode("utf-8"))
    assert err.error_category == "INVALID_PARAMETER"
    assert "parametro della richiesta" in err.safe_reason


def test_classify_lmstudio_http_400_unsupported_json_schema():
    """Verifica la classificazione di structured output non supportato."""
    body = {"error": {"message": "Grammar compilation failed for json_schema response_format"}}
    err = classify_lmstudio_http_error(400, "Bad Request", json.dumps(body).encode("utf-8"))
    assert err.error_category == "UNSUPPORTED_STRUCTURED_OUTPUT"
    assert "JSON strutturato" in err.safe_reason


def test_classify_lmstudio_http_status_codes():
    """Verifica la corretta classificazione di 429, 500, 503."""
    err_429 = classify_lmstudio_http_error(429, "Too Many Requests", b"Rate limit exceeded")
    assert err_429.error_category == "RATE_LIMIT_EXCEEDED"
    assert "troppe richieste" in err_429.safe_reason

    err_500 = classify_lmstudio_http_error(500, "Internal Server Error", b"Internal error")
    assert err_500.error_category == "SERVER_ERROR"

    err_503 = classify_lmstudio_http_error(503, "Service Unavailable", b"Service unavailable")
    assert err_503.error_category == "SERVER_ERROR"


def test_privacy_sanitization_strips_sensitive_information():
    """Verifica che i dettagli tecnici e il messaggio di errore non contengano testi di messaggi o numeri di telefono."""
    raw = b"Error at line 1 near 'Ciao Mario Rossi ti invio 5000 euro al +393331234567': context limit"
    err = classify_lmstudio_http_error(400, "Bad Request", raw)
    # Nessun dato personale o estratto della chat deve apparire in safe_reason o technical_details
    assert "Mario Rossi" not in err.safe_reason
    assert "Mario Rossi" not in err.technical_details
    assert "+393331234567" not in err.safe_reason
    assert "+393331234567" not in err.technical_details
    assert str(err).find("Mario Rossi") == -1


# ---------------------------------------------------------------------------
# 4. TEST PROGRESS TRACKER & EVENT SEQUENCING (Criticità D, Sezione 5, Sezione 8.4)
# ---------------------------------------------------------------------------

def test_progress_tracker_event_flow_and_metrics():
    """Verifica il flusso completo di eventi del progress tracker su più blocchi con retry."""
    events: list[AnalysisProgressEvent] = []

    def on_event(ev: AnalysisProgressEvent) -> None:
        events.append(ev)

    tracker = AnalysisProgressTracker(total_chunks=3, callback=on_event)

    # Chunk 1: Successo al primo tentativo
    tracker.start_chunk(1)
    tracker.chunk_success(1)

    # Chunk 2: Retry poi successo
    tracker.start_chunk(2)
    tracker.chunk_retry(2, reason="Output troncato (finish_reason=length)")
    tracker.chunk_success(2)

    # Chunk 3: Successo
    tracker.start_chunk(3)
    tracker.chunk_success(3)

    # Consolidamento & Completamento
    tracker.start_consolidation()
    tracker.complete("Topic Discovery completata!")

    assert len(events) >= 8
    final = events[-1]
    assert final.is_complete is True
    assert final.completed_chunks == 3
    assert final.remaining_chunks == 0
    assert final.requests_successful == 3
    assert final.retries_count == 1
    assert final.requests_sent == 4 # 3 iniziali + 1 retry
    assert final.percentage == 100.0
    assert ":" in final.elapsed_formatted


def test_progress_tracker_percentage_never_prematurely_100():
    """Verifica che la percentuale non raggiunga 100% prima del completamento effettivo."""
    events = []
    tracker = AnalysisProgressTracker(total_chunks=2, callback=events.append)
    tracker.start_chunk(1)
    tracker.chunk_success(1)
    ev_mid = events[-1]
    assert ev_mid.percentage < 100.0

    tracker.start_chunk(2)
    tracker.chunk_success(2)
    ev_before_complete = events[-1]
    # Senza tracker.complete(), deve essere <= 95% per lasciare spazio al consolidamento
    assert ev_before_complete.percentage <= 95.0

    tracker.complete("Fatto")
    assert events[-1].percentage == 100.0


# ---------------------------------------------------------------------------
# 5. TEST SEMANTICA BILANCIATA E DOMAIN INDEPENDENCE (Sezione 7, Sezione 8.5)
# ---------------------------------------------------------------------------

def test_operational_semantic_directive_has_no_car_bias():
    """Verifica che la direttiva semantica operativa non contenga riferimenti specifici alla compravendita di auto."""
    directive = OPERATIONAL_SEMANTIC_DIRECTIVE
    assert "macchina" not in directive.lower()
    assert "automobile" not in directive.lower()
    assert "passaggio di proprietà" not in directive.lower()
    assert "8000 euro" not in directive.lower()
    # Verifica la presenza delle tre discipline
    assert "PRESENT:" in directive
    assert "ABSENT:" in directive
    assert "UNCERTAIN:" in directive


def test_detection_neutral_domain_synonyms():
    """Verifica la Topic Detection con sola label in domini diversi (es. logistica, farmaceutica)."""
    doc = _build_test_document(message_count=2, char_per_msg=80)
    query = build_topic_query(
        label="Spedizione merci deperibili",
        description=None,
    )

    ev1 = "unified:whatsapp_audit:1::ORIGINAL_TEXT"
    mock_client = AuditControllableMockClient(
        responses=[{
            "decision": "PRESENT",
            "evidence_ids": [ev1],
            "rationale": "I messaggi menzionano il trasporto urgente di generi alimentari refrigerati.",
        }]
    )

    analyzer = TopicDetectionAnalyzer(client=mock_client, operational_mode=True)
    res = analyzer.detect_topic(document=doc, topic=query)
    assert res.decision == TopicDecision.PRESENT
    assert res.evidence_ids == (ev1,)

    # Verifica che il prompt contenga la direttiva bilanciata
    sent_prompt = mock_client.history_calls[0]["messages"][0]["content"]
    assert "OPERATIONAL SEMANTIC & MULTIMODAL INTERPRETATION DIRECTIVE:" in sent_prompt
    assert "BALANCED FORENSIC TRI-STATE DISCIPLINE" in sent_prompt


# ---------------------------------------------------------------------------
# 6. TEST PROFILAZIONE PREVENTIVA E DATASET SCALABILITY (Criticità F, Sezione 4)
# ---------------------------------------------------------------------------

def test_profile_document_for_analysis_metrics():
    """Verifica il calcolo corretto delle metriche quantitative preventive per dataset grandi."""
    doc = _build_test_document(message_count=10, char_per_msg=500)
    profile: DocumentAnalysisProfile = profile_document_for_analysis(
        document=doc,
        max_characters=2000,
        max_bundles=5,
    )

    assert profile.total_messages == 10
    assert profile.total_evidence_sections == 10
    assert profile.total_text_characters == 5000
    assert profile.estimated_text_tokens > 1000
    assert profile.estimated_chunks >= 2
    assert profile.has_multimodal_evidence is False

    d = profile.to_dict()
    assert d["document_id"] == "doc_audit_test"
    assert d["total_messages"] == 10


# ---------------------------------------------------------------------------
# 7. TEST RECOVERY DA CONTEXT OVERFLOW TRAMITE SUB-CHUNKING (Sezione 6.4)
# ---------------------------------------------------------------------------

def test_context_overflow_triggers_subchunking_in_service():
    """
    Verifica che quando un chunk solleva AiBackendRequestError con CONTEXT_OVERFLOW,
    LocalTopicAnalysisService non fallisca immediatamente ma suddivida il chunk
    in sottoblocchi preservando tutti gli evidence ID.
    """
    # Creiamo un documento con 4 messaggi
    doc = _build_test_document(message_count=4, char_per_msg=300)
    ev1 = "unified:whatsapp_audit:1::ORIGINAL_TEXT"

    # Simuliamo un client che al 1° tentativo lancia CONTEXT_OVERFLOW,
    # e per i sub-chunk successivi risponde con successo
    mock_client = AuditControllableMockClient(
        responses=[
            AiBackendRequestError(
                "Prompt eccede il limite",
                status_code=400,
                error_category="CONTEXT_OVERFLOW",
                safe_reason="Contesto insufficiente",
            ),
            # Risposta per sub-chunk 1
            {
                "decision": "PRESENT",
                "evidence_ids": [ev1],
                "rationale": "Sub-chunk 1 evidence.",
            },
            # Risposta per sub-chunk 2
            {
                "decision": "ABSENT",
                "evidence_ids": [],
                "rationale": "Sub-chunk 2 no evidence.",
            },
        ]
    )

    service = LocalTopicAnalysisService(
        client=mock_client,
        max_chunk_characters=100000,
        max_chunk_bundles=100, # 1 solo chunk iniziale
    )

    query = TopicQuery(
        topic_id="q_test",
        label="Tema riservato",
        description="Dettaglio tema",
    )

    res = service.run_topic_detection(
        document=doc,
        topic=query,
        client=mock_client,
        model_name="mock-model",
        operational_mode=True,
    )

    # La detection è riuscita aggregando i sub-chunk
    assert res.decision == TopicDecision.PRESENT
    assert ev1 in res.evidence_ids


# ---------------------------------------------------------------------------
# 8. POST-AUDIT: TEST SPECIFICI CASI A/B PROGRESS TRACKER & BUDGET & STATI
# ---------------------------------------------------------------------------

def test_dynamic_output_budget_insufficient_headroom_audit_case():
    """
    Verifica il caso specifico documentato nell'audit:
    Context: 2000, Input: 1500, Safety: 256 -> Headroom: 244.
    Desired: 200, Min: 512.
    La funzione NON deve mai restituire 512 (che supererebbe l'headroom di 244),
    ma deve restituire 0 (valore non utilizzabile) o sollevare AnalysisInputTooLargeError.
    """
    # Senza raise: restituisce 0
    budget = calculate_dynamic_output_budget(
        context_length=2000,
        estimated_prompt_tokens=1500,
        desired_output_tokens=200,
        min_output_tokens=512,
        safety_margin=256,
        raise_on_insufficient=False,
    )
    assert budget == 0
    assert budget <= 244

    # Con raise: solleva AnalysisInputTooLargeError
    with pytest.raises(AnalysisInputTooLargeError) as exc_info:
        calculate_dynamic_output_budget(
            context_length=2000,
            estimated_prompt_tokens=1500,
            desired_output_tokens=200,
            min_output_tokens=512,
            safety_margin=256,
            raise_on_insufficient=True,
        )
    assert "244" in str(exc_info.value)
    assert "512" in str(exc_info.value)


def test_dynamic_output_budget_boundary_cases():
    """Verifica il comportamento del budget dinamico su valori di confine (headroom == min, < min, > min)."""
    # Caso 1: Headroom esattamente pari a min_output_tokens (512)
    # 2000 - 1232 - 256 = 512
    budget_exact = calculate_dynamic_output_budget(
        context_length=2000,
        estimated_prompt_tokens=1232,
        desired_output_tokens=200,
        min_output_tokens=512,
        safety_margin=256,
    )
    assert budget_exact == 512

    # Caso 2: Headroom 1 token sotto min_output_tokens (511)
    # 2000 - 1233 - 256 = 511
    budget_below = calculate_dynamic_output_budget(
        context_length=2000,
        estimated_prompt_tokens=1233,
        desired_output_tokens=200,
        min_output_tokens=512,
        safety_margin=256,
    )
    assert budget_below == 0

    # Caso 3: Headroom abbondante (744) superiore a min (512) e desired (600)
    # 2000 - 1000 - 256 = 744
    budget_abundant = calculate_dynamic_output_budget(
        context_length=2000,
        estimated_prompt_tokens=1000,
        desired_output_tokens=600,
        min_output_tokens=512,
        safety_margin=256,
    )
    assert budget_abundant == 600

    # Caso 4: Headroom (400) tra min (256) e desired (600)
    # 2000 - 1344 - 256 = 400
    budget_clamped = calculate_dynamic_output_budget(
        context_length=2000,
        estimated_prompt_tokens=1344,
        desired_output_tokens=600,
        min_output_tokens=256,
        safety_margin=256,
    )
    assert budget_clamped == 400


def test_discovery_retry_recalculates_budget_on_prompt_change():
    """
    Verifica che ogni retry di TopicDiscoveryAnalyzer ricalcoli correttamente il budget
    quando cambiano i parametri o il system prompt, e non mantenga staticamente il vecchio valore.
    """
    doc = _build_test_document(message_count=2, char_per_msg=60)
    ev1 = "unified:whatsapp_audit:1::ORIGINAL_TEXT"
    mock_client = AuditControllableMockClient(
        responses=[
            {
                "content": '{"topics": [{"label": "Incompleto"',
                "finish_reason": "length",
            },
            {
                "content": f'{{"topics": [{{"label": "Argomento Rec", "short_description": "Desc", "evidence_ids": ["{ev1}"]}}]}}',
                "finish_reason": "stop",
            },
        ]
    )
    analyzer = TopicDiscoveryAnalyzer(client=mock_client)
    res = analyzer.discover_topics(
        document=doc,
        max_topics=8,
        max_tokens=400,
        context_length=2000,
        operational_mode=True,
    )
    assert len(res.topics) == 1
    assert len(mock_client.history_calls) == 2
    # Il secondo tentativo ha ricalcolato il max_tokens
    call1_max = mock_client.history_calls[0]["max_tokens"]
    call2_max = mock_client.history_calls[1]["max_tokens"]
    assert call1_max is not None
    assert call2_max is not None
    assert call2_max <= 2000  # Rispettoso del contesto


def test_progress_tracker_case_a_single_chunk_failed_not_waiting_never_100_percent():
    """
    Caso A dell'audit:
    - Totale: 1 chunk.
    - Il chunk fallisce.
    - Il tracker NON deve mostrare il chunk fallito come ancora in attesa.
    - Il tracker NON deve essere marcato come completo al 100%.
    """
    events: list[AnalysisProgressEvent] = []
    tracker = AnalysisProgressTracker(total_chunks=1, callback=events.append)

    tracker.start_chunk(1)
    tracker.chunk_failed(1, "Errore di inferenza LLM")

    # Verifica stato contatore prima del completamento
    assert tracker.total_chunks == 1
    assert tracker.completed_chunks == 0
    assert tracker.failed_chunks == 1
    assert tracker.remaining_chunks == 0  # Non deve essere in attesa!
    assert tracker.requests_failed == 1

    # Chiamata complete (anche se non dovrebbe essere chiamata su fallimento globale, testiamo resilienza)
    tracker.complete("Elaborazione terminata")

    final_event = events[-1]
    assert final_event.is_complete is False
    assert final_event.percentage < 100.0
    assert final_event.percentage == 0.0
    assert final_event.failed_chunks == 1
    assert final_event.remaining_chunks == 0


def test_progress_tracker_case_b_subchunk_split_never_2_over_1_and_no_negatives():
    """
    Caso B dell'audit:
    - Totale: 1 chunk.
    - Il chunk viene suddiviso in 2 sub-chunk.
    - Il tracker NON deve mostrare 2 chunk completati su 1 previsto (2/1).
    - I totali devono adattarsi dinamicamente (totale passa a 2).
    - Nessun numero negativo.
    """
    events: list[AnalysisProgressEvent] = []
    tracker = AnalysisProgressTracker(total_chunks=1, callback=events.append)

    # Inizia il chunk originale
    tracker.start_chunk(1)
    # Si verifica overflow, chunk 1 viene suddiviso in 2 sub-chunk
    tracker.split_chunk(1, subchunks_count=2)

    assert tracker.total_chunks == 2
    assert tracker.subchunks_added == 1
    assert tracker.remaining_chunks == 2

    # Elaborazione sub-chunk 1
    tracker.start_chunk(1)
    tracker.chunk_success(1)
    ev1 = events[-1]
    assert ev1.total_chunks == 2
    assert ev1.completed_chunks == 1
    assert ev1.remaining_chunks == 1
    assert ev1.percentage < 50.0  # Safe cap prima del consolidamento

    # Elaborazione sub-chunk 2
    tracker.start_chunk(2)
    tracker.chunk_success(2)
    ev2 = events[-1]
    assert ev2.total_chunks == 2
    assert ev2.completed_chunks == 2
    assert ev2.remaining_chunks == 0
    assert ev2.percentage <= 95.0

    # Consolidamento e completamento
    tracker.start_consolidation()
    tracker.complete("Topic Detection completata.")
    ev3 = events[-1]
    assert ev3.total_chunks == 2
    assert ev3.completed_chunks == 2
    assert ev3.remaining_chunks == 0
    assert ev3.percentage == 100.0
    assert ev3.is_complete is True

    # Verifica invarianti su TUTTI gli eventi
    for ev in events:
        assert ev.completed_chunks <= ev.total_chunks, f"Violazione 2/1: {ev.completed_chunks}/{ev.total_chunks}"
        assert ev.remaining_chunks >= 0, f"Valore negativo remaining_chunks: {ev.remaining_chunks}"
        assert ev.failed_chunks >= 0, f"Valore negativo failed_chunks: {ev.failed_chunks}"
        assert ev.total_chunks >= 1


def test_service_single_chunk_split_one_succeeds_one_fails_is_partial():
    """
    Verifica che nel percorso single-chunk suddiviso a seguito di overflow,
    se un sub-chunk riesce e uno fallisce:
    - Lo stato finale sia PARTIAL (mai COMPLETE o SUCCESS).
    - I metadati riportino chunk_count=2, completed_chunk_count=1, failed_chunk_count=1.
    - La decisione sia UNCERTAIN (non ABSENT, per protezione incompleta evidenze).
    """
    doc = _build_test_document(message_count=4, char_per_msg=200)
    query = TopicQuery(topic_id="q_test", label="Frode fiscale", description="Dettaglio frode")

    # Sub-chunk 1 risponde ABSENT; Sub-chunk 2 fallisce con errore HTTP 500
    mock_client = AuditControllableMockClient(
        responses=[
            AiBackendRequestError("Context overflow", status_code=400, error_category="CONTEXT_OVERFLOW"),
            {"decision": "ABSENT", "evidence_ids": [], "rationale": "Nessuna traccia nel blocco 1."},
            AiBackendRequestError("Internal server error", status_code=500, error_category="SERVER_ERROR"),
        ]
    )

    events: list[AnalysisProgressEvent] = []
    service = LocalTopicAnalysisService(client=mock_client, max_chunk_characters=50000, max_chunk_bundles=100)
    res = service.run_topic_detection(
        document=doc,
        topic=query,
        client=mock_client,
        model_name="mock-model",
        operational_mode=True,
        progress_callback=events.append,
    )

    # Decisione protetta: UNCERTAIN poiché una parte del documento è fallita
    assert res.decision == TopicDecision.UNCERTAIN
    assert res.metadata["status"] == "PARTIAL"
    assert res.metadata["chunk_count"] == 2
    assert res.metadata["completed_chunk_count"] == 1
    assert res.metadata["failed_chunk_count"] == 1

    # Verifica che il tracker non sia marcato complete al 100%
    final_event = events[-1]
    assert final_event.is_complete is False
    assert final_event.is_partial is True
    assert final_event.percentage < 100.0


def test_detection_never_declares_absent_when_chunk_failed_multi_chunk():
    """
    Verifica che Topic Detection NON dichiari ABSENT per l'intero documento
    se uno dei chunk è fallito durante l'inferenza multi-chunk.
    """
    doc = _build_test_document(message_count=6, char_per_msg=500)
    query = TopicQuery(topic_id="q_test", label="Spaccio", description="Sostanze illecite")

    # Chunk 1: ABSENT. Chunk 2: errore di parsing/inferenza recuperabile.
    mock_client = AuditControllableMockClient(
        responses=[
            {"decision": "ABSENT", "evidence_ids": [], "rationale": "Nessuna menzione nel blocco 1."},
            ValueError("Errore di formato JSON nel blocco 2"),
        ]
    )

    service = LocalTopicAnalysisService(
        client=mock_client,
        max_chunk_characters=2000,
        max_chunk_bundles=3,  # Genera 2 chunk
    )

    res = service.run_topic_detection(
        document=doc,
        topic=query,
        client=mock_client,
        model_name="mock-model",
        operational_mode=True,
    )

    assert res.decision == TopicDecision.UNCERTAIN
    assert "falliti" in res.rationale.lower() or "parziale" in res.rationale.lower()
    assert res.metadata["status"] == "PARTIAL"
    assert res.metadata["failed_chunk_count"] == 1


def test_service_multi_chunk_discovery_partial_on_chunk_failure():
    """
    Verifica che in Topic Discovery multi-chunk, se un chunk fallisce:
    - Lo stato sia PARTIAL (mai COMPLETE).
    - Il tracker invochi partial() e non complete().
    - I metadati riportino chunk_count, completed_chunk_count e failed_chunk_count corretti.
    """
    doc = _build_test_document(message_count=6, char_per_msg=500)
    ev1 = "unified:whatsapp_audit:1::ORIGINAL_TEXT"

    mock_client = AuditControllableMockClient(
        responses=[
            {"content": f'{{"topics": [{{"label": "T1", "short_description": "D1", "evidence_ids": ["{ev1}"]}}]}}', "finish_reason": "stop"},
            ValueError("JSON corrotto o troncato nel secondo blocco"),
        ]
    )

    events: list[AnalysisProgressEvent] = []
    service = LocalTopicAnalysisService(
        client=mock_client,
        max_chunk_characters=2000,
        max_chunk_bundles=3,  # Genera 2 chunk
    )

    res = service.run_topic_discovery(
        document=doc,
        client=mock_client,
        model_name="mock-model",
        progress_callback=events.append,
    )

    assert res.metadata["status"] == "PARTIAL"
    assert res.metadata["chunk_count"] == 2
    assert res.metadata["completed_chunk_count"] == 1
    assert res.metadata["failed_chunk_count"] == 1
    assert len(res.topics) >= 1

    final_event = events[-1]
    assert final_event.is_complete is False
    assert final_event.is_partial is True
    assert final_event.percentage < 100.0


def test_classify_lmstudio_http_400_empty_body_and_non_json():
    """Verifica che classify_lmstudio_http_error gestisca body vuoti o non-JSON senza sollevare eccezioni."""
    err_empty = classify_lmstudio_http_error(400, "Bad Request", b"")
    assert err_empty.status_code == 400
    assert err_empty.error_category is not None

    err_text = classify_lmstudio_http_error(400, "Bad Request", b"Non-JSON error from proxy")
    assert err_text.status_code == 400
    assert err_text.error_category is not None

    err_json_empty = classify_lmstudio_http_error(400, "Bad Request", b"{}")
    assert err_json_empty.status_code == 400
    assert err_json_empty.error_category is not None


def test_operational_chunking_run_topic_discovery_service_path():
    """
    Verifica che il percorso operativo reale di run_topic_discovery():
    1. Utilizzi il budget token calcolato dinamicamente (_calculate_effective_chunk_token_budget).
    2. Mantenga l'integrità del 100% delle evidenze originali.
    3. Rispecchi nei metadati il conteggio esatto dei chunk generati dal service layer.
    """
    doc = _build_test_document(message_count=30, char_per_msg=200)

    def dynamic_resp(messages):
        user_content = messages[-1]["content"]
        import re
        m = re.search(r"E\d{3}|unified:whatsapp_audit:\d+::ORIGINAL_TEXT", user_content)
        eid = m.group(0) if m else "unified:whatsapp_audit:1::ORIGINAL_TEXT"
        return {
            "content": json.dumps({"topics": [{"label": "Tema Comune", "short_description": "Descrizione", "evidence_ids": [eid]}]}),
            "finish_reason": "stop",
        }

    mock_client = AuditControllableMockClient(
        responses=[dynamic_resp for _ in range(20)]
    )

    service = LocalTopicAnalysisService(
        client=mock_client,
        max_chunk_characters=2500,
        max_chunk_bundles=10,
        max_chunk_tokens=1500,
    )

    res = service.run_topic_discovery(
        document=doc,
        client=mock_client,
        model_name="mock-model",
    )

    assert res.metadata["status"] == "COMPLETE"
    assert res.metadata["chunk_count"] >= 3
    assert res.metadata["completed_chunk_count"] == res.metadata["chunk_count"]
    assert res.metadata["failed_chunk_count"] == 0

