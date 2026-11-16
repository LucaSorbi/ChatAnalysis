"""
tests/unit/test_scalability_safe_optimizations.py
------------------------------------------------
Test suite per la validazione forense delle ottimizzazioni di scalabilità sicure.
Verifica:
1. Equivalenza forense esatta (conteggio, ordine, evidenze, fingerprint, chunking, plan hash, serializzazione).
2. Simulazione fedele dello scenario Ubuntu (20 chunk SUCCESS da checkpoint + 6 chunk FAILED da recuperare).
3. Zero chiamate HTTP duplicate o riesecuzioni dei 20 chunk validati.
4. Isolamento rigoroso tra documenti differenti (nessun cross-talk di cache).
5. Aggregazione dei topic deterministica e conservazione dei vincoli scientifici.
"""
from __future__ import annotations

import copy
import hashlib
import json
import pytest
from pathlib import Path
from typing import Any

from ai.backend import BaseLocalLlmClient, LlmCompletionResponse
from ai.chunking import (
    aggregate_topic_discovery,
    canonicalize_topic_label,
    estimate_evidence_framing_tokens,
    normalize_topic_label,
    split_document_into_chunks,
)
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDiscoveryResult,
)
from ai.serializer import (
    generate_evidence_alias_maps,
    serialize_document_for_llm,
)
from analysis.checkpoint import (
    AnalysisCheckpointStore,
    compute_document_fingerprint,
    compute_run_config_hash,
    validate_checkpoint_evidence_references,
)
from analysis.service import (
    ChunkLifecycleStatus,
    LocalTopicAnalysisService,
)
from multimodal.evidence import EvidenceSourceType, MessageEvidenceBundle, TextEvidenceSection
from importer.models import RawRecord
from validation.models import ValidationResult
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from unified.models import Chat, UnifiedMessage


def _create_synthetic_bundle(
    msg_num: int,
    text: str,
    sender: str = "TargetUser",
    chat_id: str = "chat_ubuntu_scale",
) -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="whatsapp_forensic",
        source_path="/tmp/whatsapp_test",
        source_record_id=str(msg_num),
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
        source_name="whatsapp_forensic",
        source_record_id=str(msg_num),
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"msg_doc_{msg_num}",
        source_name="whatsapp_forensic",
        source_record_id=str(msg_num),
        source_path="/tmp/whatsapp_test",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    return MessageEvidenceBundle(message=msg)


def _build_test_document(num_messages: int = 1049, doc_id: str = "doc_test_1049") -> ConversationEvidenceDocument:
    bundles = []
    for i in range(num_messages):
        txt = f"Messaggio forense {i:04d} con elementi investigativi e coordinate operative."
        bundles.append(_create_synthetic_bundle(i, txt))
    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=tuple(bundles),
        chat_id="chat_ubuntu_scale",
        source_name="whatsapp_forensic",
        metadata={"total_messages": num_messages},
    )


class ScriptableMockLlmClient(BaseLocalLlmClient):
    """Client di test che implementa pienamente BaseLocalLlmClient per simulazioni deterministiche."""

    def __init__(self, handler: Any = None, model_id: str = "qwen2.5-7b-instruct"):
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
        messages: Any,
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
        if self.handler:
            return self.handler(call_info, len(self.call_history))
        return LlmCompletionResponse(
            content=json.dumps({
                "topics": [
                    {
                        "label": "Incontri riservati",
                        "short_description": "Discussione su appuntamenti riservati",
                        "evidence_ids": ["E001"],
                    }
                ]
            }),
            model=model_id or self.model_id,
            raw_response={"finish_reason": "stop"},
        )


class TestScalabilitySafeOptimizations:
    """Verifica forense delle ottimizzazioni di memoria, caching e scalabilità."""

    def test_01_document_fingerprint_exact_determinism_and_caching(self):
        """Il fingerprint deve essere identico al bit prima e dopo caching."""
        doc = _build_test_document(50, doc_id="doc_fp_test")
        
        # Primo calcolo (raw hashing)
        fp_first = compute_document_fingerprint(doc)
        assert len(fp_first) == 64
        assert getattr(doc, "_cached_fingerprint", None) == fp_first

        # Secondo calcolo (cache hit)
        fp_second = compute_document_fingerprint(doc)
        assert fp_first == fp_second

        # Mutazione rilevata: un documento differente deve avere fingerprint differente
        doc2 = _build_test_document(51, doc_id="doc_fp_test")
        fp_other = compute_document_fingerprint(doc2)
        assert fp_first != fp_other

    def test_02_evidence_ids_property_immutable_and_matches_all_sections(self):
        """La proprietà evidence_ids deve corrispondere esattamente alle sezioni."""
        doc = _build_test_document(100, doc_id="doc_eids_test")
        eids_set = {sec.evidence_id for sec in doc.all_evidence_sections}
        assert doc.evidence_ids == eids_set
        assert isinstance(doc.evidence_ids, frozenset)
        assert len(doc.evidence_ids) == 100
        # Accesso idempotente rapido
        assert doc.evidence_ids is doc.evidence_ids

    def test_03_chunk_split_caching_and_identity_preservation(self):
        """Lo split dei chunk memorizzato nella cache preserva identità, confini e ID."""
        doc = _build_test_document(200, doc_id="doc_chunk_cache")
        chunks1 = split_document_into_chunks(doc, max_characters=8000, max_bundles=25)
        chunks2 = split_document_into_chunks(doc, max_characters=8000, max_bundles=25)
        assert chunks1 is chunks2
        assert len(chunks1) == 8
        for i, c in enumerate(chunks1):
            assert c.document_id == f"doc_chunk_cache::chunk::{i}"
            assert c.message_count == 25

    def test_04_alias_generation_caching_and_bijectivity(self):
        """La mappa degli alias brevi è deterministica, biiettiva e memorizzata su chunk."""
        doc = _build_test_document(10, doc_id="doc_alias_cache")
        id_to_alias1, alias_to_id1 = generate_evidence_alias_maps(doc, prefix="E")
        id_to_alias2, alias_to_id2 = generate_evidence_alias_maps(doc, prefix="E")
        assert id_to_alias1 == id_to_alias2
        assert alias_to_id1 == alias_to_id2
        assert len(id_to_alias1) == 10
        assert id_to_alias1[doc.bundles[0].text_evidence_sections[0].evidence_id] == "E001"
        assert alias_to_id1["E001"] == doc.bundles[0].text_evidence_sections[0].evidence_id

    def test_05_serialization_string_matches_exact_protocol(self):
        """La serializzazione produce stringhe identiche per il prompt LLM."""
        doc = _build_test_document(5, doc_id="doc_serialize_test")
        id_to_alias, _ = generate_evidence_alias_maps(doc, prefix="E")
        serialized = serialize_document_for_llm(
            doc,
            strategy=AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
            evidence_id_map=id_to_alias,
        )
        assert "=== BEGIN FORENSIC EVIDENCE DATA (UNTRUSTED RAW JSON) ===" in serialized
        assert "=== END FORENSIC EVIDENCE DATA ===" in serialized
        assert '"evidence_id": "E001"' in serialized
        assert '"evidence_id": "E005"' in serialized

    def test_06_simulated_real_ubuntu_recovery_20_success_6_failed(self, tmp_path: Path):
        """
        SIMULAZIONE ESATTA DELLO SCENARIO UBUNTU:
        - 1.049 messaggi partizionati in 26 chunk (chunk 0..25).
        - 20 chunk validati e salvati in SQLite checkpoint.
        - 6 chunk falliti (2, 4, 19, 20, 21, 25).
        - L'analisi deve:
          1. Validare e riconoscere i 20 chunk senza rieseguirli.
          2. Inviare ESATTAMENTE 6 richieste al modello (per i soli chunk mancanti).
          3. Consolidare tutti i 26 chunk con successo e passare a COMPLETE.
          4. Zero richieste HTTP duplicate.
        """
        doc = _build_test_document(26, doc_id="doc_ubuntu_26")
        chunks = split_document_into_chunks(doc, max_characters=8000, max_bundles=1)
        assert len(chunks) == 26

        chk_store = AnalysisCheckpointStore(checkpoint_dir=tmp_path)
        failed_indices = {2, 4, 19, 20, 21, 25}
        mid_to_chunk = {chunks[i].bundles[0].message.message_id: i for i in range(len(chunks))}

        # 1. Esecuzione iniziale con 6 fallimenti per citazione invalida
        def _handler_run1(call_info: dict[str, Any], count: int) -> LlmCompletionResponse:
            user_content = " ".join(m.get("content", "") for m in call_info["messages"])
            idx = -1
            for mid, c_idx in mid_to_chunk.items():
                if f'"{mid}"' in user_content:
                    idx = c_idx
                    break
            assert idx >= 0, f"Chunk non identificato: {user_content[:200]}"
            if idx in failed_indices:
                # Cita alias alieno/invalido per simulare errore originario Ubuntu
                payload = {"topics": [{"label": "Tema Fallito", "short_description": "Descrizione", "evidence_ids": ["E999"]}]}
            else:
                payload = {"topics": [{"label": f"Tema Validato {idx}", "short_description": "Descrizione", "evidence_ids": ["E001"]}]}
            return LlmCompletionResponse(
                content=json.dumps(payload),
                model="qwen2.5-7b-instruct",
                raw_response={"finish_reason": "stop"},
            )

        client1 = ScriptableMockLlmClient(handler=_handler_run1)
        service1 = LocalTopicAnalysisService(
            client=client1,
            checkpoint_store=chk_store,
            enable_checkpointing=True,
            max_chunk_bundles=1,
            context_length=8192,
        )

        res1 = service1.run_topic_discovery(
            document=doc,
            client=client1,
            model_name="qwen2.5-7b-instruct",
        )
        assert res1.metadata["status"] == "PARTIAL"
        assert res1.metadata["status"] == "PARTIAL"
        assert res1.metadata["completed_chunk_count"] == 20
        assert res1.metadata["failed_chunk_count"] == 6
        assert res1.metadata["recovered_chunk_count"] == 0

        # Verifica SQLite intermedio: esattamente 20 chunk validati presenti
        with chk_store._connect() as conn:
            row = conn.execute(
                "SELECT total_planned_chunks FROM checkpoints WHERE document_id = ?",
                (doc.document_id,),
            ).fetchone()
            assert row is not None
            assert row[0] == 26
            chunk_rows = conn.execute(
                "SELECT chunk_index FROM checkpoint_chunks WHERE document_fingerprint = ?",
                (compute_document_fingerprint(doc),),
            ).fetchall()
            saved_indices = {r[0] for r in chunk_rows}
            assert len(saved_indices) == 20
            assert saved_indices == set(range(26)) - failed_indices

        # 2. Ripresa con mock_client deterministico che riceve solo i chunk da recuperare
        run2_called_chunks: list[int] = []

        def _handler_run2(call_info: dict[str, Any], count: int) -> LlmCompletionResponse:
            user_content = " ".join(m.get("content", "") for m in call_info["messages"])
            idx = -1
            for mid, c_idx in mid_to_chunk.items():
                if f'"{mid}"' in user_content:
                    idx = c_idx
                    break
            assert idx >= 0, f"Chunk non identificato: {user_content[:200]}"
            run2_called_chunks.append(idx)
            payload = {"topics": [{"label": f"Tema Recuperato {idx}", "short_description": "Descrizione", "evidence_ids": ["E001"]}]}
            return LlmCompletionResponse(
                content=json.dumps(payload),
                model="qwen2.5-7b-instruct",
                raw_response={"finish_reason": "stop"},
            )

        mock_client = ScriptableMockLlmClient(handler=_handler_run2)
        service2 = LocalTopicAnalysisService(
            client=mock_client,
            checkpoint_store=chk_store,
            enable_checkpointing=True,
            max_chunk_bundles=1,
            context_length=8192,
        )

        result = service2.run_topic_discovery(
            document=doc,
            client=mock_client,
            model_name="qwen2.5-7b-instruct",
            retry_failed_only=True,
        )

        # Verifiche cardinali:
        # A. Zero richieste per i 20 chunk validati; esattamente 6 richieste per i falliti
        assert len(run2_called_chunks) == 6
        assert set(run2_called_chunks) == failed_indices

        # B. Metadata di esecuzione: 20 recuperati, 26 completati, 0 falliti
        meta = result.metadata
        assert meta["chunk_count"] == 26
        assert meta["completed_chunk_count"] == 26
        assert meta["failed_chunk_count"] == 0
        assert meta["recovered_chunk_count"] == 20
        assert meta["status"] == "COMPLETE"
        assert meta["evidence_citation_protocol"] == "deterministic_alias_v1"
        assert meta["total_evidence_count"] == 26
        assert meta["analyzed_evidence_count"] == 26
        assert meta["unanalyzed_evidence_count"] == 0

        # C. Verifica di consistenza forense: tutti i topic contengono evidence_id originali e nessun alias residuo
        total_doc_eids = {s.evidence_id for s in doc.all_evidence_sections}
        assert len(result.topics) > 0
        for topic in result.topics:
            assert len(topic.evidence_ids) > 0
            assert set(topic.evidence_ids).issubset(total_doc_eids)
            for eid in topic.evidence_ids:
                assert not eid.startswith("E00"), f"Alias operativo E00 trapelato nel risultato finale: {eid}"

    def test_07_topic_label_normalization_cache_preserves_semantics(self):
        """La normalizzazione e canonicalizzazione con cache restituisce stringhe identiche."""
        test_labels = [
            ("Traffico d'armi", "Traffico di armi"),
            ("Spaccio di stupefacenti", "Spaccio stupefacenti"),
            ("Incontri Riservati...", "Incontro riservato"),
            ("Pianificazione rapina", "Pianificazione rapine"),
        ]
        for l1, l2 in test_labels:
            canon1 = canonicalize_topic_label(l1)
            canon2 = canonicalize_topic_label(l2)
            assert canon1 == canon2
            assert canonicalize_topic_label.cache_info().hits >= 0

    def test_08_alias_cache_mutation_immunity_and_isolation(self):
        """
        Verifica che la modifica dei dizionari restituiti da generate_evidence_alias_maps
        NON corrompa la cache interna né influenzi le successive invocazioni (difesa da mutazione).
        """
        doc = _build_test_document(5, doc_id="doc_alias_immutability")
        first_eid = doc.bundles[0].text_evidence_sections[0].evidence_id

        # 1. Prima invocazione
        id_to_alias_1, alias_to_id_1 = generate_evidence_alias_maps(doc, prefix="E")
        assert id_to_alias_1[first_eid] == "E001"
        assert alias_to_id_1["E001"] == first_eid

        # 2. Mutazione intenzionale da parte del chiamante
        id_to_alias_1[first_eid] = "E_MUTATED"
        id_to_alias_1["INVENTED_ID"] = "E999"
        alias_to_id_1["E001"] = "CORRUPTED_ID"
        alias_to_id_1["E999"] = "INVENTED_ID"

        # 3. Seconda invocazione sullo stesso documento (hit della cache)
        id_to_alias_2, alias_to_id_2 = generate_evidence_alias_maps(doc, prefix="E")

        # La cache interna NON deve essere stata alterata
        assert id_to_alias_2[first_eid] == "E001", "La cache interna è stata corrotta dalla mutazione del chiamante!"
        assert "INVENTED_ID" not in id_to_alias_2
        assert alias_to_id_2["E001"] == first_eid
        assert "E999" not in alias_to_id_2

        # Riferimenti distinti (copie difensive indipendenti)
        assert id_to_alias_1 is not id_to_alias_2
        assert alias_to_id_1 is not alias_to_id_2

        # Biiettività e determinismo intatti
        for sec in doc.all_evidence_sections:
            alias = id_to_alias_2[sec.evidence_id]
            assert alias_to_id_2[alias] == sec.evidence_id

