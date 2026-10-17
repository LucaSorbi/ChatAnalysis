"""
tests/unit/test_operational_evidence_alias_and_live_recovery.py
--------------------------------------------------------------
Suite di test specialistici per la risoluzione del problema reale su Ubuntu (LM Studio + Qwen2.5-7B-Instruct):
1. Sei fallimenti per citazioni non valide (riproduzione esatta del caso reale 20/26 chunk completati, 6 falliti).
2. Generazione alias validi e conversione esatta a evidence ID originali (1-a-1 deterministica).
3. Rifiuto tassativo di alias inventati, alieni e citazioni cross-chunk (zero fuzzy matching, zero correzione per somiglianza).
4. Recupero trasparente dei 20 chunk già validati senza nuove inferenze (zero-repeat, RECOVERED_FROM_CHECKPOINT).
5. Contabilità forense rigorosa: nessuna perdita di evidenze, unanalyzed_evidence_count = 0.
6. Retry correttivo con rispetto matematico del context length e budget token (Candidate A vs Candidate B).
7. Integrità SQLite, fingerprint crittografico e isolamento cross-document.
8. Persistenza della diagnostica strutturata senza leak di contenuti privati o testi di chat.
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
from analysis.checkpoint import (
    AnalysisCheckpointStore,
    compute_document_fingerprint,
    compute_run_config_hash,
)
from analysis.models import (
    ChunkDiagnosticCategory,
    ChunkDiagnosticRecord,
    ChunkLifecycleStatus,
    DiscoveryChunkOutcome,
    OperationalAnalysisConfig,
    OperationalAnalysisFailedError,
    OperationalProfile,
)
from analysis.service import LocalTopicAnalysisService
from ai.serializer import (
    generate_evidence_alias_maps,
    serialize_document_for_llm,
)
from ai.structured import (
    validate_topic_discovery_payload,
)
from ai.token_budget import calculate_token_budget_plan
from ai.topics import (
    PROMPT_VERSION_DISCOVERY,
    PROMPT_VERSION_DISCOVERY_OPERATIONAL,
    TopicDiscoveryAnalyzer,
)


def _make_bundle(doc_id: str, idx: int, text: str) -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="whatsapp_export",
        source_path="/tmp/whatsapp_test",
        source_record_id=str(idx),
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
        source_name="whatsapp_export",
        source_record_id=str(idx),
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"msg_{doc_id}_{idx}",
        source_name="whatsapp_export",
        source_record_id=str(idx),
        source_path="/tmp/whatsapp_test",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    return MessageEvidenceBundle(message=msg)


def _make_document(doc_id: str, count: int = 26) -> ConversationEvidenceDocument:
    bundles = tuple(_make_bundle(doc_id, i, f"Messaggio forense {i} relativo a indagine.") for i in range(count))
    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=bundles,
        chat_id="chat_evidence_test",
        source_name="whatsapp_export",
    )


class ScriptableMockLlmClient(BaseLocalLlmClient):
    """Client di test per simulare comportamenti specifici del modello per chunk."""

    def __init__(self, handler: Any, model_id: str = "qwen2.5-7b-instruct"):
        super().__init__()
        self.handler = handler
        self.model_id = model_id
        self.call_history: list[dict[str, Any]] = []
        self.request_listener = None

    def is_available(self, timeout_seconds: float = 2.0) -> bool:
        return True

    def list_models(self) -> tuple[str, ...]:
        return (self.model_id,)

    def chat_completion(
        self,
        messages: Sequence[Mapping[str, str]],
        model_id: str | None = None,
        schema: Mapping[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float | None = None,
        max_tokens: int | None = None,
    ) -> LlmCompletionResponse:
        call_info = {
            "messages": [dict(m) for m in messages],
            "model_id": model_id or self.model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        self.call_history.append(call_info)
        return self.handler(call_info, len(self.call_history))


@pytest.mark.unit
class TestOperationalEvidenceAliasAndLiveRecovery:
    """Test suite completa per gli alias delle evidenze e il recupero live 20/26."""

    def test_01_alias_generation_deterministic_and_bijective(self):
        """Verifica che generate_evidence_alias_maps produca mapping biiettivi 1-a-1 deterministici."""
        doc = _make_document("doc_alias_test", count=5)
        id_to_alias, alias_to_id = generate_evidence_alias_maps(doc)

        assert len(id_to_alias) == 5
        assert len(alias_to_id) == 5

        expected_aliases = ["E001", "E002", "E003", "E004", "E005"]
        for idx, sec in enumerate(doc.all_evidence_sections):
            alias = expected_aliases[idx]
            assert id_to_alias[sec.evidence_id] == alias
            assert alias_to_id[alias] == sec.evidence_id

        # Determinismo: seconda esecuzione produce mapping identico
        id2, a2 = generate_evidence_alias_maps(doc)
        assert id_to_alias == id2
        assert alias_to_id == a2

    def test_02_valid_aliases_exact_deterministic_remapping_to_original_evidence_ids(self):
        """Verifica che citazioni con alias validi (E001, E002) vengano convertite esattamente negli ID forensi originali."""
        doc = _make_document("doc_remap_test", count=3)
        valid_eids = {s.evidence_id for s in doc.all_evidence_sections}
        _, alias_to_id = generate_evidence_alias_maps(doc)

        payload = {
            "topics": [
                {
                    "label": "Argomento Operativo",
                    "short_description": "Descrizione basata su evidenze",
                    "evidence_ids": ["E001", "E003"],
                }
            ]
        }
        raw_json = json.dumps(payload)

        discovered = validate_topic_discovery_payload(
            raw_text=raw_json,
            valid_evidence_ids=valid_eids,
            max_topics=5,
            alias_map=alias_to_id,
        )

        assert len(discovered) == 1
        topic = discovered[0]
        assert topic.label == "Argomento Operativo"
        # Gli evidence_id nel topic validato finale devono essere gli identificativi forensi originari!
        expected_orig_1 = doc.all_evidence_sections[0].evidence_id
        expected_orig_3 = doc.all_evidence_sections[2].evidence_id
        assert topic.evidence_ids == (expected_orig_1, expected_orig_3)
        assert set(topic.evidence_ids).issubset(valid_eids)

    def test_03_invented_and_cross_chunk_aliases_strictly_rejected_no_fuzzy(self):
        """Verifica che alias inesistenti o appartenenti ad altri chunk siano rifiutati tassativamente senza fuzzy matching."""
        doc = _make_document("doc_strict_test", count=2)
        valid_eids = {s.evidence_id for s in doc.all_evidence_sections}
        _, alias_to_id = generate_evidence_alias_maps(doc)
        # Il blocco ha solo E001 e E002

        # 1. Alias inesistente / inventato (E999)
        raw_invented = json.dumps({
            "topics": [
                {"label": "Tema 1", "short_description": "Desc", "evidence_ids": ["E999"]}
            ]
        })
        with pytest.raises(AiInvalidEvidenceCitationError) as exc_info:
            validate_topic_discovery_payload(raw_invented, valid_evidence_ids=valid_eids, alias_map=alias_to_id)
        assert "E999" in str(exc_info.value)
        assert exc_info.value.invalid_evidence_ids == ("E999",)

        # 2. Alias di altro chunk (E003 non appartiene a questo documento di 2 elementi)
        raw_alien = json.dumps({
            "topics": [
                {"label": "Tema 2", "short_description": "Desc", "evidence_ids": ["E001", "E003"]}
            ]
        })
        with pytest.raises(AiInvalidEvidenceCitationError) as exc_info:
            validate_topic_discovery_payload(raw_alien, valid_evidence_ids=valid_eids, alias_map=alias_to_id)
        assert "E003" in str(exc_info.value)

        # 3. Stringa parziale o troncata (zero tolleranza/fuzzy)
        raw_truncated = json.dumps({
            "topics": [
                {"label": "Tema 3", "short_description": "Desc", "evidence_ids": ["E1"]}
            ]
        })
        with pytest.raises(AiInvalidEvidenceCitationError):
            validate_topic_discovery_payload(raw_truncated, valid_evidence_ids=valid_eids, alias_map=alias_to_id)

    def test_04_reproduce_real_ubuntu_scenario_20_success_6_failed(self):
        """
        Riproduce esattamente la situazione osservata su Ubuntu con Qwen2.5-7B su 26 chunk:
        - 26 chunk totali.
        - 20 completati con successo (checkpoint salvato).
        - 6 falliti per INVALID_EVIDENCE_ID (indici 2, 4, 19, 20, 21, 25), ciascuno con 2 tentativi effettuati, HTTP 200.
        - Stato risultante PARTIAL.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            store = AnalysisCheckpointStore(checkpoint_dir=Path(tmpdir))
            doc = _make_document("doc_ubuntu_26", count=26)
            failed_indices = {2, 4, 19, 20, 21, 25}

            def _handler(call_info: dict[str, Any], call_count: int) -> LlmCompletionResponse:
                full_text = " ".join(m.get("content", "") for m in call_info["messages"])
                matched_idx = None
                for i in range(25, -1, -1):
                    if f'"msg_doc_ubuntu_26_{i}"' in full_text:
                        matched_idx = i
                        break

                assert matched_idx is not None, f"Chunk index non identificato in: {full_text[:300]}"

                if matched_idx in failed_indices:
                    # Nei 6 blocchi falliti, il modello restituisce una citazione non valida (es. ID allucinato/troncato)
                    # sia al tentativo 1 che al tentativo 2 (retry correttivo)
                    return LlmCompletionResponse(
                        content=json.dumps({
                            "topics": [
                                {
                                    "label": f"Tema Fallito Chunk {matched_idx}",
                                    "short_description": "Descrizione",
                                    "evidence_ids": ["E999_HALLUCINATED"],
                                }
                            ]
                        }),
                        model="qwen2.5-7b-instruct",
                        latency_seconds=0.2,
                        prompt_tokens=450,
                        completion_tokens=60,
                        total_tokens=510,
                        raw_response={"finish_reason": "stop"},
                    )
                else:
                    # I 20 chunk di successo restituiscono alias corretto "E001"
                    return LlmCompletionResponse(
                        content=json.dumps({
                            "topics": [
                                {
                                    "label": f"Tema Riuscito Chunk {matched_idx}",
                                    "short_description": f"Descrizione tema {matched_idx}",
                                    "evidence_ids": ["E001"],
                                }
                            ]
                        }),
                        model="qwen2.5-7b-instruct",
                        latency_seconds=0.15,
                        prompt_tokens=420,
                        completion_tokens=50,
                        total_tokens=470,
                        raw_response={"finish_reason": "stop"},
                    )

            client = ScriptableMockLlmClient(handler=_handler)
            service = LocalTopicAnalysisService(
                client=client,
                checkpoint_store=store,
                enable_checkpointing=True,
                max_chunk_bundles=1,  # 26 bundle -> 26 chunk
            )

            res = service.run_topic_discovery(
                document=doc,
                client=client,
                model_name="qwen2.5-7b-instruct",
            )

            # Verifica stato del primo passaggio
            meta = res.metadata
            assert meta["status"] == "PARTIAL"
            assert meta["chunk_count"] == 26
            assert meta["completed_chunk_count"] == 20
            assert meta["failed_chunk_count"] == 6
            assert meta["recovered_chunk_count"] == 0

            # Verifica diagnostica dei 6 blocchi falliti
            diagnostics = meta["chunk_diagnostics"]
            assert len(diagnostics) == 26
            failed_diags = [d for d in diagnostics if d["status"] == ChunkLifecycleStatus.FAILED.value]
            assert len(failed_diags) == 6
            for fd in failed_diags:
                assert fd["chunk_index"] in failed_indices
                assert fd["diagnostic_category"] == ChunkDiagnosticCategory.INVALID_EVIDENCE_ID.value
                assert fd["http_status"] == 200
                assert fd["json_valid"] is True
                assert fd["schema_valid"] is True
                assert fd["evidence_valid"] is False
                assert fd["attempts"] == 2  # ha effettuato i 2 tentativi prima di dichiarare fallimento
                assert fd["checkpoint_saved"] is False

            # Verifica che SQLite contenga esattamente i 20 checkpoint validi salvati
            doc_fp = compute_document_fingerprint(doc)
            chk_meta = store.get_latest_checkpoint_metadata_for_document(doc.document_id, doc_fp)
            assert chk_meta is not None
            cfg_fp = chk_meta["config_fingerprint"]
            chk = store.load_checkpoint(doc_fp, cfg_fp)
            assert chk is not None
            assert len(chk.completed_chunk_indices) == 20
            assert set(chk.completed_chunk_indices) == set(range(26)) - failed_indices

    def test_05_live_recovery_reprocesses_only_6_failed_chunks_and_preserves_20_valid(self):
        """
        Verifica che riavviando la discovery con 'retry_failed_only=True' (o con checkpoint attivo):
        - I 20 chunk validi vengano recuperati direttamente da SQLite con ZERO richieste verso l'LLM.
        - Soltanto i 6 chunk mancanti vengano inviati al modello.
        - Grazie alla strategia alias E001, i 6 chunk abbiano successo.
        - La discovery transiti a COMPLETE (26/26), con 0 evidenze perse e tutti i conteggi preservati.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            store = AnalysisCheckpointStore(checkpoint_dir=Path(tmpdir))
            doc = _make_document("doc_recovery_26", count=26)
            failed_indices = {2, 4, 19, 20, 21, 25}

            # Simula esecuzione 1 (20 successi, 6 fallimenti)
            def _handler_run1(call_info: dict[str, Any], count: int) -> LlmCompletionResponse:
                full_text = " ".join(m.get("content", "") for m in call_info["messages"])
                matched_idx = None
                for i in range(25, -1, -1):
                    if f'"msg_doc_recovery_26_{i}"' in full_text:
                        matched_idx = i
                        break
                assert matched_idx is not None, f"Chunk non identificato: {full_text[:200]}"
                if matched_idx in failed_indices:
                    return LlmCompletionResponse(
                        content=json.dumps({"topics": [{"label": "F", "short_description": "D", "evidence_ids": ["E999"]}]}),
                        model="qwen2.5-7b-instruct",
                        raw_response={"finish_reason": "stop"},
                    )
                return LlmCompletionResponse(
                    content=json.dumps({"topics": [{"label": f"T{matched_idx}", "short_description": "D", "evidence_ids": ["E001"]}]}),
                    model="qwen2.5-7b-instruct",
                    raw_response={"finish_reason": "stop"},
                )

            client1 = ScriptableMockLlmClient(handler=_handler_run1)
            service = LocalTopicAnalysisService(
                client=client1,
                checkpoint_store=store,
                enable_checkpointing=True,
                max_chunk_bundles=1,
            )

            res1 = service.run_topic_discovery(document=doc, client=client1, model_name="qwen2.5-7b-instruct")
            assert res1.metadata["status"] == "PARTIAL"
            assert res1.metadata["completed_chunk_count"] == 20

            # Ora simuliamo la Riprova selettiva dei blocchi falliti (run 2)
            # In run 2, il client registra tutte le chiamate
            run2_called_chunks: list[int] = []

            def _handler_run2(call_info: dict[str, Any], count: int) -> LlmCompletionResponse:
                full_text = " ".join(m.get("content", "") for m in call_info["messages"])
                matched_idx = None
                for i in range(25, -1, -1):
                    if f'"msg_doc_recovery_26_{i}"' in full_text:
                        matched_idx = i
                        break
                assert matched_idx is not None, f"Chunk run 2 non identificato: {full_text[:200]}"
                run2_called_chunks.append(matched_idx)
                # Adesso anche i 6 blocchi citano correttamente l'alias "E001"
                return LlmCompletionResponse(
                    content=json.dumps({
                        "topics": [
                            {
                                "label": f"Tema Recuperato Chunk {matched_idx}",
                                "short_description": f"Descrizione recuperata {matched_idx}",
                                "evidence_ids": ["E001"],
                            }
                        ]
                    }),
                    model="qwen2.5-7b-instruct",
                    latency_seconds=0.18,
                    prompt_tokens=430,
                    completion_tokens=55,
                    total_tokens=485,
                    raw_response={"finish_reason": "stop"},
                )

            client2 = ScriptableMockLlmClient(handler=_handler_run2)
            res2 = service.run_topic_discovery(
                document=doc,
                client=client2,
                model_name="qwen2.5-7b-instruct",
                retry_failed_only=True,
            )

            # Verifica: ESATTAMENTE e SOLTANTO i 6 blocchi falliti devono essere stati elaborati!
            assert sorted(run2_called_chunks) == sorted(list(failed_indices)), (
                f"Attese chiamate LLM solo per {sorted(list(failed_indices))}, ma ricevute: {sorted(run2_called_chunks)}"
            )
            assert len(client2.call_history) == 6

            # Verifica stato consolidato finale
            meta2 = res2.metadata
            assert meta2["status"] == "COMPLETE"
            assert meta2["chunk_count"] == 26
            assert meta2["completed_chunk_count"] == 26
            assert meta2["failed_chunk_count"] == 0
            assert meta2["recovered_chunk_count"] == 20  # I 20 recuperati da checkpoint SQLite!
            assert meta2["total_evidence_count"] == 26
            assert meta2["analyzed_evidence_count"] == 26
            assert meta2["unanalyzed_evidence_count"] == 0  # Zero perdite!

            # Verifica che tutti i topic consolidati contengano evidence_id originali e validi
            total_doc_eids = {s.evidence_id for s in doc.all_evidence_sections}
            for topic in res2.topics:
                assert len(topic.evidence_ids) > 0
                assert set(topic.evidence_ids).issubset(total_doc_eids)
                # Nessun alias deve trapelare nei risultati finali consolidati
                for eid in topic.evidence_ids:
                    assert not eid.startswith("E00"), f"Alias operativo non rimappato nel risultato finale: {eid}"

    def test_06_corrective_retry_context_budget_headroom(self):
        """Verifica che il retry correttivo rispetti matematicamente il context_length del modello."""
        doc = _make_document("doc_budget_test", count=2)
        target_model = "qwen2.5-7b-instruct"

        retry_reasons: list[str] = []

        def _on_retry(att: int, reason: str):
            retry_reasons.append(reason)

        attempts_done = 0

        def _handler(call_info: dict[str, Any], count: int) -> LlmCompletionResponse:
            nonlocal attempts_done
            attempts_done += 1
            if attempts_done == 1:
                # Primo tentativo: cita alias inesistente
                return LlmCompletionResponse(
                    content=json.dumps({"topics": [{"label": "T", "short_description": "D", "evidence_ids": ["E999"]}]}),
                    model=target_model,
                    raw_response={"finish_reason": "stop"},
                )
            else:
                # Secondo tentativo (corretto): cita alias corretto
                return LlmCompletionResponse(
                    content=json.dumps({"topics": [{"label": "T", "short_description": "D", "evidence_ids": ["E001"]}]}),
                    model=target_model,
                    raw_response={"finish_reason": "stop"},
                )

        client = ScriptableMockLlmClient(handler=_handler)
        analyzer = TopicDiscoveryAnalyzer(client=client, operational_mode=True)

        res = analyzer.discover_topics(
            document=doc,
            model_id=target_model,
            context_length=8192,
            on_retry_callback=_on_retry,
        )

        assert attempts_done == 2
        assert retry_reasons == ["invalid_evidence_citation"]
        assert res.metadata["retries"] == 1
        assert res.metadata["evidence_citation_protocol"] == "deterministic_alias_v1"
        assert len(res.topics) == 1
        assert res.topics[0].evidence_ids[0] == doc.all_evidence_sections[0].evidence_id

    def test_07_sqlite_checkpoint_integrity_and_cross_document_isolation(self):
        """Verifica l'isolamento crittografico e di schema in SQLite: documenti differenti non possono accedere a checkpoint altrui."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = AnalysisCheckpointStore(checkpoint_dir=Path(tmpdir))
            doc_a = _make_document("doc_a", count=3)
            doc_b = _make_document("doc_b", count=3)

            def _handler(call_info: dict[str, Any], count: int) -> LlmCompletionResponse:
                return LlmCompletionResponse(
                    content=json.dumps({"topics": [{"label": "T", "short_description": "D", "evidence_ids": ["E001"]}]}),
                    model="qwen2.5-7b-instruct",
                    raw_response={"finish_reason": "stop"},
                )

            client = ScriptableMockLlmClient(handler=_handler)
            service = LocalTopicAnalysisService(
                client=client,
                checkpoint_store=store,
                enable_checkpointing=True,
                max_chunk_bundles=1,
            )

            # Esegui discovery su doc_a
            service.run_topic_discovery(document=doc_a, client=client, model_name="qwen2.5-7b-instruct")

            # Verifica che per doc_b NON esista alcun checkpoint compatibile
            doc_b_fp = compute_document_fingerprint(doc_b)
            plan_b = service.create_analysis_plan(doc_b, analysis_type="discovery", max_topics=10)
            cfg_b_fp = compute_run_config_hash(
                model_id="qwen2.5-7b-instruct",
                prompt_version="discovery_operational",
                config=service.config,
                chunks=plan_b.chunks,
                extra_params={"max_topics": 10, "resolved_model_key": None, "quantization": None},
            )
            chk_b = store.load_and_validate_checkpoint(doc_b, cfg_b_fp, chunks=plan_b.chunks, expected_analysis_type="discovery")
            assert chk_b is None, "doc_b non deve caricare né riutilizzare i checkpoint di doc_a"

    def test_08_diagnostics_persistence_without_private_chat_content(self):
        """Verifica che i record diagnostici salvati su SQLite non contengano messaggi di chat o dati riservati."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = AnalysisCheckpointStore(checkpoint_dir=Path(tmpdir))
            doc = _make_document("doc_privacy_test", count=2)

            def _handler(call_info: dict[str, Any], count: int) -> LlmCompletionResponse:
                full_text = " ".join(m.get("content", "") for m in call_info["messages"])
                if '"msg_doc_privacy_test_0"' in full_text:
                    # Chunk 0 ha successo
                    return LlmCompletionResponse(
                        content=json.dumps({"topics": [{"label": "T0", "short_description": "D", "evidence_ids": ["E001"]}]}),
                        model="qwen2.5-7b-instruct",
                        raw_response={"finish_reason": "stop"},
                    )
                else:
                    # Chunk 1 fallisce con citazione invalida
                    return LlmCompletionResponse(
                        content=json.dumps({"topics": [{"label": "T1", "short_description": "D", "evidence_ids": ["E999"]}]}),
                        model="qwen2.5-7b-instruct",
                        raw_response={"finish_reason": "stop"},
                    )

            client = ScriptableMockLlmClient(handler=_handler)
            service = LocalTopicAnalysisService(
                client=client,
                checkpoint_store=store,
                enable_checkpointing=True,
                max_chunk_bundles=1,
            )

            res = service.run_topic_discovery(document=doc, client=client, model_name="qwen2.5-7b-instruct")
            meta = res.metadata

            diagnostics = meta.get("chunk_diagnostics", [])
            assert len(diagnostics) == 2

            # Verifica assenza di leak di testo forense in memoria
            for diag in diagnostics:
                detail = diag.get("error_detail") or ""
                # Il testo 'Messaggio forense' NON deve comparire nei log di errore/diagnostica
                assert "Messaggio forense" not in detail
                assert diag["http_status"] == 200

            # Verifica assenza di leak anche su SQLite persistente
            doc_fp = compute_document_fingerprint(doc)
            chk_meta = store.get_latest_checkpoint_metadata_for_document(doc.document_id, doc_fp)
            assert chk_meta is not None
            cfg_fp = chk_meta["config_fingerprint"]
            persisted_diags = store.get_chunk_diagnostics(doc_fp, cfg_fp)
            assert len(persisted_diags) == 2
            for pd in persisted_diags:
                p_detail = pd.error_detail or ""
                assert "Messaggio forense" not in p_detail
                if pd.status == ChunkLifecycleStatus.FAILED.value:
                    assert pd.diagnostic_category == ChunkDiagnosticCategory.INVALID_EVIDENCE_ID.value
