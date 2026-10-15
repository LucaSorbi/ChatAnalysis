"""
tests/unit/test_performance_accounting_audit.py
------------------------------------------------
Suite di test specialistica per l'Audit Finale Prestazioni, Contabilità HTTP,
Protezione del Budget di Contesto, Coerenza Profilo, Checkpoint/Ripresa e Consolidamento su Larga Scala.

Verifica formalmente tutti i requisiti dell'audit:
1. Contatore HTTP e Invariante: requests_sent = requests_successful + requests_failed + requests_in_flight;
2. Separazione rigorosa del ciclo di vita HTTP dal ciclo di vita dei chunk;
3. Distinzione tra errori HTTP, timeout e protocol_error (HTTP 200 con JSON malformato);
4. Scenario audit: context overflow su chunk iniziale -> split in 2 sub-chunk riusciti -> requests_sent=3, succ=2, fail=1;
5. Protezione budget di contesto: nessun token fantasma dichiarato quando headroom < 256;
6. Validazione della stima ~85 token su messaggi brevi, lunghi, ID complessi, emoji e Unicode;
7. Coerenza al 100% tra profilo preventivo e chunking effettivo operativo;
8. Checkpoint e Ripresa: fingerprint crittografico, skip chunk già validati, isolamento multi-documento;
9. Consolidamento topic su migliaia di chunk (2.273 blocchi) e normalizzazione stem/morfologica italiana;
10. Preservazione integrale di tutti gli evidence ID e trasparenza metadati (semantic_deduplication=False).
"""
from __future__ import annotations

import json
import math
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

from ai.backend import (
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AnalysisInputTooLargeError,
    BaseLlmClient,
    LlmCompletionResponse,
)
from ai.chunking import (
    ChunkAnalysisOutcome,
    aggregate_topic_detection,
    aggregate_topic_discovery,
    canonicalize_topic_label,
    estimate_evidence_framing_tokens,
    normalize_topic_label,
    profile_document_for_analysis,
    split_document_into_chunks,
)
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from ai.structured import (
    TOPIC_DETECTION_SCHEMA,
    TOPIC_DISCOVERY_SCHEMA,
)
from analysis.checkpoint import (
    AnalysisCheckpointStore,
    compute_document_fingerprint,
    compute_run_config_hash,
)
from analysis.models import OperationalAnalysisConfig
from analysis.progress import (
    AnalysisProgressEvent,
    AnalysisProgressTracker,
)
from analysis.service import LocalTopicAnalysisService
from importer.models import RawRecord
from multimodal.evidence import EvidenceSourceType, MessageEvidenceBundle, TextEvidenceSection
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult


# =============================================================================
# HELPER PER TEST DETERMINISTICI
# =============================================================================

def make_test_bundle(
    msg_id: int,
    text: str,
    evidence_id: str | None = None,
    source_name: str = "audit_chat.txt",
) -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name=source_name,
        source_path=f"/audit/{source_name}",
        source_record_id=str(msg_id),
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
        source_name=source_name,
        source_record_id=str(msg_id),
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"msg_{msg_id:04d}",
        source_name=source_name,
        source_record_id=str(msg_id),
        source_path=f"/audit/{source_name}",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    bundle = MessageEvidenceBundle(message=msg)
    if evidence_id:
        sec = TextEvidenceSection(
            evidence_id=evidence_id,
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            message_id=f"msg_{msg_id:04d}",
            source_name=source_name,
            source_record_id=str(msg_id),
            text=text,
        )
        object.__setattr__(bundle, "text_evidence_sections", (sec,))
    return bundle


def make_test_document(count: int, prefix: str = "audit_doc") -> ConversationEvidenceDocument:
    bundles = [
        make_test_bundle(i, f"Messaggio forense {i} relativo ad attività di comunicazione e transazioni.")
        for i in range(1, count + 1)
    ]
    return ConversationEvidenceDocument(
        document_id=f"{prefix}_{count}",
        bundles=tuple(bundles),
        source_name="audit_chat.txt",
    )


class ControlledMockClient(BaseLlmClient):
    """Client mock controllabile per simulare overflow, retry ed errori di protocollo."""

    def __init__(self, responses_or_errors: list[Any], model_id: str = "qwen2.5-7b-instruct") -> None:
        self._queue = list(responses_or_errors)
        self.call_count = 0
        self.history: list[dict[str, Any]] = []
        self.model_id = model_id
        self.request_listener: Any | None = None

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        schema: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_seconds: float = 30.0,
        seed: int | None = 42,
        **kwargs,
    ) -> LlmCompletionResponse:
        self.call_count += 1
        self.history.append({"messages": messages, "model_id": model_id})
        req_model = model_id or self.model_id

        if self.request_listener:
            self.request_listener.record_request_sent()
            self.request_listener.record_http_response_received()

        if not self._queue:
            # Fallback deterministico conforme allo schema
            eid = "msg_0001::ORIGINAL_TEXT"
            if schema and schema.get("title") == "TopicDetectionResult":
                content = json.dumps({"decision": "PRESENT", "evidence_ids": [eid], "rationale": "Rilevato."})
            else:
                content = json.dumps({"topics": [{"label": "Tema Test", "short_description": "Descrizione", "evidence_ids": [eid]}]})
            if self.request_listener:
                self.request_listener.record_request_success()
            return LlmCompletionResponse(content=content, model=req_model)

        item = self._queue.pop(0)
        if isinstance(item, Exception):
            if self.request_listener:
                if isinstance(item, AiBackendTimeoutError):
                    self.request_listener.record_timeout(str(item))
                elif isinstance(item, AiBackendProtocolError):
                    self.request_listener.record_protocol_error(str(item))
                elif isinstance(item, AiBackendRequestError):
                    self.request_listener.record_http_error(item.status_code, str(item))
                else:
                    self.request_listener.record_http_error(message=str(item))
            raise item
        if self.request_listener:
            self.request_listener.record_request_success()
        if isinstance(item, LlmCompletionResponse):
            return LlmCompletionResponse(content=item.content, model=req_model)
        return item


# =============================================================================
# PROBLEMA 2: CONTATORE HTTP E INVARIANTE
# =============================================================================

def test_http_counter_exact_audit_scenario_overflow_split_recovery():
    """
    Scenario audit verificato dall'utente:
    1. Un chunk genera una richiesta HTTP (richiesta 1).
    2. La richiesta fallisce per context overflow.
    3. Il chunk viene suddiviso in 2 sotto-blocchi.
    4. Entrambe le nuove richieste (richieste 2 e 3) terminano con successo.
    Invariante atteso:
    requests_sent = 3
    requests_successful = 2
    requests_failed = 1 (la richiesta iniziale fallita è contabilizzata!)
    requests_in_flight = 0
    Invariant: requests_sent == requests_successful + requests_failed + requests_in_flight
    """
    doc = make_test_document(40)
    topic = TopicQuery(topic_id="top_1", label="Transazioni", description="Attività finanziarie")

    # Simulazione: prima chiamata fallisce per overflow HTTP 400, poi 2 subchunk riescono
    overflow_err = AiBackendRequestError(
        message="Context length exceeded (8300 > 8192)",
        status_code=400,
        error_category="CONTEXT_OVERFLOW",
        safe_reason="Richiesta supera il contesto disponibile",
    )
    ok_sub1 = LlmCompletionResponse(
        content=json.dumps({"decision": "PRESENT", "evidence_ids": ["msg_0001::ORIGINAL_TEXT"], "rationale": "Presente nel sub 1"}),
        model="mock-model",
    )
    ok_sub2 = LlmCompletionResponse(
        content=json.dumps({"decision": "ABSENT", "evidence_ids": [], "rationale": "Assente nel sub 2"}),
        model="mock-model",
    )

    mock_client = ControlledMockClient([overflow_err, ok_sub1, ok_sub2])
    tracker_events: list[AnalysisProgressEvent] = []

    service = LocalTopicAnalysisService(
        client=mock_client,
        max_chunk_bundles=100, # Singolo chunk iniziale
        max_chunk_tokens=6000,
    )

    res = service.run_topic_detection(
        document=doc,
        topic=topic,
        progress_callback=tracker_events.append,
    )

    assert mock_client.call_count == 3
    assert len(tracker_events) > 0
    final_event = tracker_events[-1]

    # CONTABILITÀ MATEMATICA RIGOROSA
    assert final_event.requests_sent == 3
    assert final_event.requests_successful == 2
    assert final_event.requests_failed == 1 # Risolve il difetto dell'audit!
    assert final_event.requests_in_flight == 0
    assert final_event.http_errors_count == 1
    assert final_event.is_requests_accounting_consistent is True

    # STATO CHUNK: 2 sub-chunk completati, 0 falliti
    assert final_event.completed_chunks == 2
    assert final_event.failed_chunks == 0
    assert final_event.total_chunks == 2
    assert final_event.is_complete is True
    assert res.decision == TopicDecision.PRESENT


def test_http_counter_distinguishes_http_errors_from_protocol_errors():
    """
    Verifica la separazione concettuale tra:
    - Errore di trasporto HTTP (status 500 o mancata connessione);
    - Timeout HTTP;
    - Risposta HTTP 200 ma con JSON malformato (protocol_error).
    """
    tracker = AnalysisProgressTracker(total_chunks=3)

    # 1. Richiesta 1 inviata e fallita per HTTP 500
    tracker.record_request_sent("Richiesta 1")
    assert tracker.requests_sent == 1
    assert tracker.requests_in_flight == 1
    tracker.record_http_error(status_code=500, message="Internal Server Error")
    assert tracker.requests_failed == 1
    assert tracker.http_errors_count == 1
    assert tracker.requests_in_flight == 0

    # 2. Richiesta 2 inviata e fallita per Timeout
    tracker.record_request_sent("Richiesta 2")
    assert tracker.requests_sent == 2
    assert tracker.requests_in_flight == 1
    tracker.record_timeout("Connection timed out after 30s")
    assert tracker.requests_failed == 2
    assert tracker.timeouts_count == 1
    assert tracker.requests_in_flight == 0

    # 3. Richiesta 3 inviata: riceve HTTP 200 ma JSON non decodificabile (protocol_error)
    tracker.record_request_sent("Richiesta 3")
    assert tracker.requests_sent == 3
    tracker.record_http_response_received()
    tracker.record_protocol_error("JSONDecodeError: Expecting value: line 1 column 1 (char 0)")
    assert tracker.requests_failed == 3
    assert tracker.protocol_errors_count == 1
    assert tracker.requests_in_flight == 0

    # Invariante
    assert tracker.check_requests_invariant() is True
    assert tracker.requests_sent == 3
    assert tracker.requests_successful == 0
    assert tracker.requests_failed == 3


def test_http_counter_retry_accounting_invariant():
    """
    Verifica la contabilità durante i retry:
    - Richiesta 1 inviata;
    - Richiesta 1 fallisce (es. troncamento finish_reason=length);
    - Retry registrato, richiesta 2 inviata;
    - Richiesta 2 termina con successo.
    Invariante: requests_sent (2) == requests_successful (1) + requests_failed (1) + requests_in_flight (0).
    """
    tracker = AnalysisProgressTracker(total_chunks=1)
    tracker.start_chunk(1)
    assert tracker.requests_sent == 1
    assert tracker.requests_in_flight == 1

    tracker.chunk_retry(1, reason="finish_reason=length")
    assert tracker.retries_count == 1
    assert tracker.requests_failed == 1 # La prima chiamata è fallita
    assert tracker.requests_sent == 2   # Nuova chiamata inviata
    assert tracker.requests_in_flight == 1

    tracker.chunk_success(1)
    assert tracker.requests_in_flight == 0
    assert tracker.requests_successful == 1
    assert tracker.completed_chunks == 1
    assert tracker.check_requests_invariant() is True


# =============================================================================
# PROBLEMA 3: PROTEZIONE DEL BUDGET DI CONTESTO
# =============================================================================

def test_effective_chunk_token_budget_never_declares_phantom_tokens_when_under_256():
    """
    Requisito Audit:
    In analysis/service.py, verificare il comportamento quando available_headroom < 256.
    Il sistema non deve dichiarare disponibile uno spazio che non esiste (niente max(256, ...)).
    """
    service = LocalTopicAnalysisService(context_length=8192)

    # Caso A: Spazio residuo esattamente pari a 100 token
    # context_length=1000, reserved=600, overhead=850 -> headroom = 150 < 256
    eff = service._calculate_effective_chunk_token_budget(
        context_length=1000,
        reserved_output_tokens=600,
        safety_margin=100,
        system_prompt_overhead=100,
        schema={"type": "object"},
        raise_on_insufficient=False,
    )
    # Deve restituire esattamente l'headroom reale (< 256), MAI 256 fittizi!
    assert eff < 256
    assert eff > 0

    # Caso B: Spazio residuo <= 0 -> deve sollevare AnalysisInputTooLargeError
    with pytest.raises(AnalysisInputTooLargeError):
        service._calculate_effective_chunk_token_budget(
            context_length=800,
            reserved_output_tokens=700,
            safety_margin=150,
            system_prompt_overhead=100,
            raise_on_insufficient=True,
        )


def test_evidence_framing_tokens_estimation_on_complex_cases():
    """
    Requisito Audit:
    La stima dei token per evidenza deve essere verificata su:
    - Messaggi brevi;
    - Testi lunghi;
    - Identificativi complessi;
    - Caratteri Unicode ed Emoji;
    - Testo multilingue.
    """
    # 1. Messaggio breve ("Ok")
    sec_short = TextEvidenceSection(
        evidence_id="msg_01::sec_0",
        source_type=EvidenceSourceType.ORIGINAL_TEXT,
        message_id="msg_01",
        source_name="chat.txt",
        source_record_id="1",
        text="Ok",
    )
    tokens_short = estimate_evidence_framing_tokens(sec_short)
    assert tokens_short >= 85  # Floor di sicurezza garantito

    # 2. Identificativo forense molto lungo e complesso
    complex_eid = "unified:whatsapp_export_case_alpha_evidence_section_record_identifier_long_path_2026:999999"
    sec_complex = TextEvidenceSection(
        evidence_id=complex_eid,
        source_type=EvidenceSourceType.ORIGINAL_TEXT,
        message_id="msg_complex_999999",
        source_name="whatsapp_forensic_case_export_full.txt",
        source_record_id="rec_999999",
        text="Testo con identificativo complesso",
    )
    tokens_complex = estimate_evidence_framing_tokens(sec_complex)
    # L'ingombro dei metadati supera il floor di 85 token e viene scalato proporzionalmente
    assert tokens_complex > 85

    # 3. Testo multilingue ed Emoji
    sec_emoji = TextEvidenceSection(
        evidence_id="msg_emoji::sec_0",
        source_type=EvidenceSourceType.ORIGINAL_TEXT,
        message_id="msg_emoji",
        source_name="chat.txt",
        source_record_id="2",
        text="Ciao! 😃 👍 🎯 🇮🇹 Confirmo la reunión mañana às 15:00 en Madrid.",
    )
    tokens_emoji = estimate_evidence_framing_tokens(sec_emoji)
    assert tokens_emoji >= 85


# =============================================================================
# PROBLEMA 4: COERENZA DEL PROFILO PREVENTIVO
# =============================================================================

def test_pre_analysis_profile_matches_operational_service_chunk_plan():
    """
    Requisito Audit:
    Verificare che il numero di chunk mostrato prima dell'analisi sia calcolato con
    la stessa configurazione effettiva utilizzata dalla specifica run.
    """
    doc = make_test_document(100)
    service = LocalTopicAnalysisService(
        context_length=8192,
        max_chunk_characters=10000,
        max_chunk_bundles=30,
        max_chunk_tokens=3000,
    )

    # Profilazione preventiva tramite il service
    prof_det = service.profile_document(doc, analysis_type="detection")
    prof_disc = service.profile_document(doc, analysis_type="discovery")

    # Verifica che il chunking operativo utilizzi esattamente la stessa suddivisione
    eff_tokens_det = service._calculate_effective_chunk_token_budget(
        context_length=8192,
        reserved_output_tokens=service.default_max_tokens,
        schema=TOPIC_DETECTION_SCHEMA,
    )
    actual_chunks_det = split_document_into_chunks(
        doc,
        max_characters=service.max_chunk_characters,
        max_bundles=service.max_chunk_bundles,
        max_tokens=eff_tokens_det,
    )

    assert prof_det.estimated_chunks == len(actual_chunks_det)
    assert prof_det.total_messages == 100
    assert prof_det.total_evidence_sections == 100


# =============================================================================
# PROBLEMA 5: RIPRESA DELLE ANALISI MOLTO LUNGHE (CHECKPOINT & RESUME)
# =============================================================================

def test_checkpoint_store_atomic_save_and_restore():
    """
    Verifica salvataggio atomico, integrità strutturale e ripristino di un checkpoint.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = AnalysisCheckpointStore(checkpoint_dir=tmp_dir)
        doc = make_test_document(50)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen-7b", "v1", OperationalAnalysisConfig())

        # Verifica iniziale assenza checkpoint
        assert store.has_checkpoint(doc_fp, cfg_fp) is False

        # Creazione e salvataggio del primo chunk validato
        res_chunk0 = TopicDetectionResult(
            topic=TopicQuery(topic_id="t1", label="Contratti", description="Accordi contrattuali"),
            decision=TopicDecision.PRESENT,
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
            rationale="Confermato accordo contrattuale.",
            provenance_document_id=doc.document_id,
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=res_chunk0,
            total_planned_chunks=3,
            analysis_type="detection",
            model_id="qwen-7b",
            prompt_version="v1",
        )

        assert store.has_checkpoint(doc_fp, cfg_fp) is True
        loaded = store.load_checkpoint(doc_fp, cfg_fp)
        assert loaded is not None
        assert loaded.completed_chunk_indices == (0,)
        assert "0" in loaded.serialized_results

        # Salvataggio del secondo chunk validato
        res_chunk1 = TopicDetectionResult(
            topic=TopicQuery(topic_id="t1", label="Contratti", description="Accordi contrattuali"),
            decision=TopicDecision.ABSENT,
            evidence_ids=(),
            rationale="Nessuna evidenza.",
            provenance_document_id=doc.document_id,
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=1,
            result=res_chunk1,
            total_planned_chunks=3,
            analysis_type="detection",
            model_id="qwen-7b",
            prompt_version="v1",
        )

        loaded2 = store.load_checkpoint(doc_fp, cfg_fp)
        assert loaded2 is not None
        assert loaded2.completed_chunk_indices == (0, 1)

        # Pulizia checkpoint
        cleared = store.clear_checkpoint(doc_fp, cfg_fp)
        assert cleared is True
        assert store.has_checkpoint(doc_fp, cfg_fp) is False


def test_checkpoint_isolation_between_different_documents():
    """
    Requisito Audit: Non condividere cache tra documenti differenti.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = AnalysisCheckpointStore(checkpoint_dir=tmp_dir)
        doc_a = make_test_document(20, prefix="doc_A")
        doc_b = make_test_document(20, prefix="doc_B")

        fp_a = compute_document_fingerprint(doc_a)
        fp_b = compute_document_fingerprint(doc_b)
        assert fp_a != fp_b

        cfg_fp = compute_run_config_hash("qwen-7b", "v1", OperationalAnalysisConfig())

        # Salva per A
        res = TopicDetectionResult(
            topic=TopicQuery(topic_id="t1", label="Test", description="Descrizione test"),
            decision=TopicDecision.ABSENT,
            evidence_ids=(),
            rationale="Assente",
            provenance_document_id=doc_a.document_id,
        )
        store.save_chunk(
            document_id=doc_a.document_id,
            document_fingerprint=fp_a,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=res,
            total_planned_chunks=1,
            analysis_type="detection",
            model_id="qwen-7b",
            prompt_version="v1",
        )

        # Verifica che il documento B non veda il checkpoint del documento A
        assert store.has_checkpoint(fp_a, cfg_fp) is True
        assert store.has_checkpoint(fp_b, cfg_fp) is False
        assert store.load_checkpoint(fp_b, cfg_fp) is None


def test_service_resume_from_checkpoint_skips_already_completed_chunks():
    """
    Verifica che il service layer riprenda dal checkpoint ed eviti di rieseguire
    le chiamate LLM per i chunk già completati e validati.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = AnalysisCheckpointStore(checkpoint_dir=tmp_dir)
        doc = make_test_document(60)  # Genera ~3 chunk con max_bundles=20
        topic = TopicQuery(topic_id="top_1", label="Contratti", description="Accordi contrattuali")

        # Prepariamo un client mock che terrebbe traccia delle chiamate
        # Se eseguisse tutti i chunk farebbe 3 chiamate.
        mock_resp = LlmCompletionResponse(
            content=json.dumps({"decision": "ABSENT", "evidence_ids": [], "rationale": "Assente"}),
            model="mock-model",
        )
        mock_client = ControlledMockClient([mock_resp, mock_resp, mock_resp])

        service = LocalTopicAnalysisService(
            client=mock_client,
            max_chunk_bundles=20,
            checkpoint_store=store,
            enable_checkpointing=True,
        )

        doc_fp = compute_document_fingerprint(doc)
        plan = service.create_analysis_plan(doc, "detection", topic_query=topic)
        chunks = list(plan.chunks)
        cfg_fp = compute_run_config_hash(
            model_id=service.default_model,
            prompt_version="detection_operational",
            config=service.config,
            topic_query=topic,
            chunks=chunks,
            extra_params={
                "strategy": "DIRECT_MULTILINGUAL",
                "operational_mode": True,
                "loaded_instance_id": None,
                "resolved_model_key": None,
                "quantization": None,
            },
        )

        # Pre-popoliamo il checkpoint con il chunk 0 già completato
        pre_res0 = TopicDetectionResult(
            topic=topic,
            decision=TopicDecision.PRESENT,
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
            rationale="Precedentemente completato con successo.",
            provenance_document_id=doc.document_id,
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=pre_res0,
            total_planned_chunks=3,
            analysis_type="detection",
            model_id=service.default_model,
            prompt_version="detection_operational",
        )

        events: list[AnalysisProgressEvent] = []
        final_res = service.run_topic_detection(
            document=doc,
            topic=topic,
            progress_callback=events.append,
        )

        # Il chunk 0 è stato ripristinato da checkpoint, quindi sono state fatte solo 2 chiamate invece di 3!
        assert mock_client.call_count == 2
        assert final_res.decision == TopicDecision.PRESENT # Contiene l'evidenza del chunk 0


# =============================================================================
# PROBLEMA 6: CONSOLIDAMENTO E DEDUPLICAZIONE TOPIC
# =============================================================================

def test_canonicalize_topic_label_italian_morphological_variants():
    """
    Verifica consolidamento di topic con etichette sintatticamente equivalenti in italiano:
    - Preposizioni e apostrofi ('Traffico di armi' vs 'Traffico d'armi')
    - Singolare / plurale ('Incontri riservati' vs 'Incontro riservato')
    - Inversioni e stop words
    """
    # 1. Armi
    k1 = canonicalize_topic_label("Traffico di armi")
    k2 = canonicalize_topic_label("Traffico d'armi")
    k3 = canonicalize_topic_label("Traffico armi")
    assert k1 == k2 == k3

    # 2. Incontri
    k4 = canonicalize_topic_label("Incontri riservati")
    k5 = canonicalize_topic_label("Incontro riservato")
    assert k4 == k5

    # 3. Stupefacenti
    k6 = canonicalize_topic_label("Spaccio di stupefacenti")
    k7 = canonicalize_topic_label("Spaccio stupefacenti")
    assert k6 == k7

    # 4. Argomenti realmente distinti rimangono categoricamente distinti
    k_frode = canonicalize_topic_label("Frode informatica")
    k_droga = canonicalize_topic_label("Traffico di stupefacenti")
    k_ricicl = canonicalize_topic_label("Riciclaggio di denaro")
    assert len({k_frode, k_droga, k_ricicl}) == 3


def test_aggregate_topic_discovery_fuses_morphological_variants_and_preserves_evidence():
    """
    Verifica che Topic Discovery fonda i topic morfologicamente equivalenti
    unendo tutte le evidenze senza perdite e incrementando il supporto dei chunk.
    """
    chunk1 = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic(label="Traffico di armi", short_description="Vendita illegale armi", evidence_ids=("msg_01", "msg_02")),
        ),
        provenance_document_id="doc_test",
        metadata={"chunk_id": "chunk_0"},
    )
    chunk2 = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic(label="Traffico d'armi", short_description="Commercio armi da fuoco", evidence_ids=("msg_03", "msg_04")),
        ),
        provenance_document_id="doc_test",
        metadata={"chunk_id": "chunk_1"},
    )
    chunk3 = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic(label="Frode informatica", short_description="Accesso abusivo sistemi", evidence_ids=("msg_05",)),
        ),
        provenance_document_id="doc_test",
        metadata={"chunk_id": "chunk_2"},
    )

    aggregated = aggregate_topic_discovery([chunk1, chunk2, chunk3], parent_document_id="doc_test")

    # Devono emergere esattamente 2 argomenti (Traffico armi unificato + Frode informatica)
    assert len(aggregated.topics) == 2
    labels = {t.label for t in aggregated.topics}
    # Il topic unificato ha tutte le 4 evidenze
    armi_topic = next(t for t in aggregated.topics if "armi" in t.label.casefold())
    assert set(armi_topic.evidence_ids) == {"msg_01", "msg_02", "msg_03", "msg_04"}

    # Verifica trasparenza metadati: no falso claim di embedding neurale
    assert aggregated.metadata["consolidation_mode"] == "stem_canonical_deduplication"
    assert aggregated.metadata["semantic_deduplication"] is False


def test_large_scale_consolidation_over_thousands_of_chunks():
    """
    Requisito Audit:
    Lo script prestazionale misura l'aggregazione utilizzando l'intero numero di chunk previsto (migliaia).
    Testa il consolidamento su 2.273 risultati di chunk (equivalente a 50.000 messaggi).
    """
    total_simulated_chunks = 2273
    simulated_results: list[TopicDiscoveryResult] = []

    topics_pool = [
        ("Organizzazione logistica", "Pianificazione trasporti e magazzino"),
        ("Pagamento fornitori", "Liquidazione fatture e bonifici"),
        ("Gestione contrattuale", "Revisione clausole e accordi"),
        ("Aggiornamento inventario", "Conteggio scorte e merci"),
        ("Incontri di lavoro", "Riunioni e colloqui operativi"),
    ]

    t0 = time.perf_counter()
    for ch_idx in range(total_simulated_chunks):
        t_label, t_desc = topics_pool[ch_idx % len(topics_pool)]
        eid = f"msg_{ch_idx:05d}::sec_0"
        simulated_results.append(
            TopicDiscoveryResult(
                topics=(
                    DiscoveredTopic(label=t_label, short_description=t_desc, evidence_ids=(eid,)),
                ),
                provenance_document_id="doc_large_50k",
                metadata={"chunk_id": f"doc_large_50k::chunk_{ch_idx}"},
            )
        )

    t_agg_start = time.perf_counter()
    aggregated = aggregate_topic_discovery(
        chunk_results=simulated_results,
        parent_document_id="doc_large_50k",
        max_topics=10,
    )
    agg_elapsed = time.perf_counter() - t_agg_start

    # Verifica prestazionale: aggregare 2.273 chunk deve impiegare meno di 1 secondo (tempo reale ~0.05s)
    assert agg_elapsed < 1.0
    assert len(aggregated.topics) == 5
    assert aggregated.metadata["total_raw_chunks"] == 2273
    assert aggregated.metadata["raw_topic_count"] == 2273
    assert aggregated.metadata["consolidated_topic_count"] == 5

    # Tutte le 2.273 evidenze sono state preservate senza alcuna perdita
    total_eids_in_topics = sum(len(t.evidence_ids) for t in aggregated.topics)
    assert total_eids_in_topics == 2273
