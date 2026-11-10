"""
tests/unit/test_topic_provenance_verification_fix.py
-----------------------------------------------------
Test riproducibile (RED -> GREEN) per l'incidente di provenienza Discovery -> Topic Detection:
"TopicProvenanceError: Topic modificato: la descrizione di '...' è stata alterata rispetto al checkpoint."

Scenario autentico multi-chunk:
In una Discovery multi-chunk parzialmente o totalmente recuperata, un topic appare in più blocchi
(es. Chunk 0 con descrizione breve da 70 caratteri, Chunk 1 con descrizione estesa da 117 caratteri).
Entrambe le descrizioni e i relativi evidence_ids sono autentici, generati dal modello e validati in SQLite.
Durante l'aggregazione, aggregate_topic_discovery seleziona la descrizione più lunga (Chunk 1).
Se la run registrata o ripristinata contiene la variante di descrizione autentica del Chunk 0
(es. da consolidamento parziale o ripresa incrementale), il controllo di provenienza in
_authenticate_recovered_discovery_run_for_verification non deve respingerla erroneamente come "alterata",
purché provenga effettivamente dai blocchi validati del checkpoint SQLite.

Al contempo, qualunque alterazione fraudolenta o non presente in alcun blocco del checkpoint SQLite
DEVE categoricamente fallire con TopicProvenanceError (fail-closed security).
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from typing import Any
import pytest

from ai.backend import BaseLlmClient, LlmCompletionResponse
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    MessageEvidenceBundle,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
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
    AnalysisOrigin,
    AttestationLevel,
    ConsistencyResult,
    ConsistencyStatus,
    LoadingMode,
    OperationalAnalysisConfig,
    TopicProvenanceError,
)
from analysis.checkpoint import (
    AnalysisCheckpointStore,
    compute_document_fingerprint,
    compute_run_config_hash,
)
from analysis.service import LocalTopicAnalysisService


class SyntheticControlledLlmClient(BaseLlmClient):
    """Client sintetico controllato per test isolati e riproducibili."""

    def __init__(self, model_id: str = "qwen2.5-7b-instruct") -> None:
        self.model_id = model_id
        self.calls: list[dict[str, Any]] = []

    def is_available(self) -> bool:
        return True

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        seed: int | None = None,
        response_format: Any = None,
        **kwargs: Any,
    ) -> LlmCompletionResponse:
        self.calls.append({
            "messages": messages,
            "model_id": model_id,
            "max_tokens": max_tokens,
        })
        payload = {
            "decision": "PRESENT",
            "evidence_ids": ["msg_0001::ORIGINAL_TEXT"],
            "rationale": "Topic rilevato in modo consistente nelle evidenze sintetiche.",
        }
        return LlmCompletionResponse(
            content=json.dumps(payload),
            model=model_id or self.model_id,
            prompt_tokens=40,
            completion_tokens=25,
            total_tokens=65,
            latency_seconds=0.01,
        )


def _make_synthetic_document(doc_id: str, num_messages: int = 6) -> ConversationEvidenceDocument:
    bundles = []
    for i in range(num_messages):
        raw = RawRecord(
            source_name="synthetic_export",
            source_path="/path/test",
            source_record_id=str(i + 1),
            record_type="message",
            raw_fields={"text": f"Messaggio sintetico {i+1} sullo sviluppo software."},
            media_reference=None,
            metadata={},
        )
        val = ValidationResult(record=raw, issues=())
        ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
        norm = NormalizedRecord(
            raw_record=raw,
            validation_result=val,
            source_name="synthetic_export",
            source_record_id=str(i + 1),
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=f"Messaggio sintetico {i+1} sullo sviluppo software e architettura.",
            media_reference=None,
        )
        msg = UnifiedMessage(
            message_id=f"msg_{i+1:04d}",
            source_name="synthetic_export",
            source_record_id=str(i + 1),
            source_path="/path/test",
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=norm.text_content,
            media_reference=None,
            provenance_record=norm,
        )
        bundles.append(MessageEvidenceBundle(message=msg))

    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=tuple(bundles),
        source_name="synthetic_export",
        metadata={"data_classification": "SYNTHETIC_DEMO"},
    )


class TestTopicProvenanceDiscoveryVerificationFix:
    """Verifica riproducibile per la gestione della provenienza multi-chunk."""

    @pytest.fixture
    def temp_checkpoint_dir(self):
        d = tempfile.mkdtemp(prefix="test_topic_prov_fix_")
        yield Path(d)
        shutil.rmtree(d, ignore_errors=True)

    def test_reproduction_multichunk_recovered_variant_description_verification(self, temp_checkpoint_dir):
        """
        RIPRODUZIONE:
        In un documento multi-chunk (2 blocchi salvati in SQLite):
        - Chunk 0 produce il topic 'Sviluppo architettura' con descrizione D0 (70 caratteri) ed evidence e0.
        - Chunk 1 produce lo stesso topic 'Sviluppo architettura' con descrizione D1 (117 caratteri) ed evidence e1.
        Entrambi i blocchi sono salvati nel checkpoint SQLite autentico.
        La run registrata ha il topic con la descrizione autentica D0.
        L'aggregazione SQLite consolidata calcolerebbe la descrizione più lunga D1.
        La verifica con Topic Detection deve AUTENTICARE con successo il topic perché D0
        proviene legittimamente da un blocco validato nel checkpoint.
        """
        doc = _make_synthetic_document("doc_multichunk_prov_01", num_messages=6)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)

        cfg = OperationalAnalysisConfig(max_chunk_bundles=3)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        desc_chunk0 = "Discussione sintetica breve sull'avanzamento dei moduli del progetto."
        desc_chunk1 = "Discussione sintetica estesa e approfondita sull'avanzamento dei moduli del progetto e delle milestone pianificate."

        eid0 = "msg_0001::ORIGINAL_TEXT"
        eid1 = "msg_0004::ORIGINAL_TEXT"

        topic_chunk0 = DiscoveredTopic(
            label="Sviluppo architettura",
            short_description=desc_chunk0,
            evidence_ids=(eid0,),
        )
        chunk0_res = TopicDiscoveryResult(
            topics=(topic_chunk0,),
            provenance_document_id=doc.document_id,
            metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value, "run_id": "chk_run_part_01"},
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=chunk0_res,
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_part_01",
        )

        topic_chunk1 = DiscoveredTopic(
            label="Sviluppo architettura",
            short_description=desc_chunk1,
            evidence_ids=(eid1,),
        )
        chunk1_res = TopicDiscoveryResult(
            topics=(topic_chunk1,),
            provenance_document_id=doc.document_id,
            metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value, "run_id": "chk_run_part_01"},
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=1,
            result=chunk1_res,
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_part_01",
        )

        # Costruisce la run registrata avente la descrizione autentica del Chunk 0 (len 70)
        registered_run = TopicDiscoveryResult(
            topics=(topic_chunk0,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_recovered_test_01",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )

        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        # Esecuzione verifica con Prova del Nove: deve avere successo
        det_res, cons_res = service.verify_discovered_topic(
            document=doc,
            discovered_topic=topic_chunk0,
            discovery_result=registered_run,
            discovery_run_id="run_recovered_test_01",
            client=client,
        )
        assert det_res.decision == TopicDecision.PRESENT
        assert cons_res.status in (ConsistencyStatus.CONSISTENT, ConsistencyStatus.PARTIALLY_CONSISTENT)

    def test_negative_tampered_description_categorically_rejected(self, temp_checkpoint_dir):
        """
        TEST NEGATIVO:
        Se la descrizione del topic viene alterata rispetto a TUTTI i blocchi validati in SQLite
        (anche solo di un singolo carattere o prefisso non autentico),
        la verifica DEVE fallire tassativamente con TopicProvenanceError.
        """
        doc = _make_synthetic_document("doc_neg_tamper_01", num_messages=4)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig()
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        authentic_desc = "Descrizione autentica presente nel blocco SQLite."
        eid = "msg_0001::ORIGINAL_TEXT"

        topic_auth = DiscoveredTopic(
            label="Sviluppo architettura",
            short_description=authentic_desc,
            evidence_ids=(eid,),
        )
        chunk_res = TopicDiscoveryResult(
            topics=(topic_auth,),
            provenance_document_id=doc.document_id,
            metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value, "run_id": "chk_run_01"},
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=chunk_res,
            total_planned_chunks=1,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_01",
        )

        # Descrizione manomessa (alterazione non presente in SQLite)
        tampered_topic = DiscoveredTopic(
            label="Sviluppo architettura",
            short_description=authentic_desc + " [TEST_TAMPERING_PAYLOAD]",
            evidence_ids=(eid,),
        )
        tampered_run = TopicDiscoveryResult(
            topics=(tampered_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_tampered_desc",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )

        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        with pytest.raises(TopicProvenanceError) as exc_info:
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=tampered_topic,
                discovery_result=tampered_run,
                discovery_run_id="run_tampered_desc",
                client=client,
            )
        assert "alterata rispetto al checkpoint" in str(exc_info.value)

    def test_negative_alien_evidence_id_categorically_rejected(self, temp_checkpoint_dir):
        """
        TEST NEGATIVO:
        Se vengono iniettati evidence_id non appartenenti al blocco o al documento attivo,
        la verifica DEVE fallire tassativamente con TopicProvenanceError.
        """
        doc = _make_synthetic_document("doc_neg_alien_01", num_messages=4)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig()
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        authentic_desc = "Descrizione autentica presente nel blocco SQLite."
        eid = "msg_0001::ORIGINAL_TEXT"

        topic_auth = DiscoveredTopic(
            label="Sviluppo architettura",
            short_description=authentic_desc,
            evidence_ids=(eid,),
        )
        chunk_res = TopicDiscoveryResult(
            topics=(topic_auth,),
            provenance_document_id=doc.document_id,
            metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value, "run_id": "chk_run_01"},
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=chunk_res,
            total_planned_chunks=1,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_01",
        )

        # Evidenza aliena non appartenente al documento
        alien_topic = DiscoveredTopic(
            label="Sviluppo architettura",
            short_description=authentic_desc,
            evidence_ids=(eid, "msg_alien_9999::ORIGINAL_TEXT"),
        )
        alien_run = TopicDiscoveryResult(
            topics=(alien_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_alien_evidence",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )

        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        with pytest.raises(TopicProvenanceError):
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=alien_topic,
                discovery_result=alien_run,
                discovery_run_id="run_alien_evidence",
                client=client,
            )

    def test_negative_unauthentic_topic_label_rejected(self, temp_checkpoint_dir):
        """
        TEST NEGATIVO:
        Se il topic ha una label che non esiste in alcun blocco del checkpoint,
        deve fallire tassativamente con TopicProvenanceError.
        """
        doc = _make_synthetic_document("doc_neg_lbl_01", num_messages=4)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig()
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        authentic_desc = "Descrizione autentica presente nel blocco SQLite."
        eid = "msg_0001::ORIGINAL_TEXT"

        topic_auth = DiscoveredTopic(
            label="Sviluppo architettura",
            short_description=authentic_desc,
            evidence_ids=(eid,),
        )
        chunk_res = TopicDiscoveryResult(
            topics=(topic_auth,),
            provenance_document_id=doc.document_id,
            metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value, "run_id": "chk_run_01"},
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=chunk_res,
            total_planned_chunks=1,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_01",
        )

        fake_topic = DiscoveredTopic(
            label="Argomento Fabbricato Inesistente",
            short_description=authentic_desc,
            evidence_ids=(eid,),
        )
        fake_run = TopicDiscoveryResult(
            topics=(fake_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_fake_topic",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )

        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        with pytest.raises(TopicProvenanceError):
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=fake_topic,
                discovery_result=fake_run,
                discovery_run_id="run_fake_topic",
                client=client,
            )

    def test_red_patch_currently_allows_cross_chunk_chimeric_pairing(self, temp_checkpoint_dir):
        """
        TEST RED (FAIL-CLOSED INTEGRITY):
        Dimostra che la patch corrente accetta erroneamente una coppia chimerica
        composta dalla descrizione del Chunk 0 e la sequenza esatta di evidence_ids del Chunk 1.
        Questa combinazione non è mai stata emessa dal modello per alcun record autonomo,
        né costituisce la sequenza aggregata legittima. Deve fallire tassativamente con TopicProvenanceError.
        """
        doc = _make_synthetic_document("doc_chimeric_01", num_messages=6)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig(max_chunk_bundles=3)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        desc_c0 = "Descrizione autentica specifica del blocco zero."
        desc_c1 = "Descrizione autentica estesa specifica del blocco uno."
        eids_c0 = ("msg_0001::ORIGINAL_TEXT", "msg_0002::ORIGINAL_TEXT")
        eids_c1 = ("msg_0004::ORIGINAL_TEXT", "msg_0005::ORIGINAL_TEXT")

        # Chunk 0
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c0, evidence_ids=eids_c0),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_chim",
        )

        # Chunk 1
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=1,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c1, evidence_ids=eids_c1),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_chim",
        )

        # Run con accoppiamento chimerico: Descrizione di Chunk 0 + Evidenze di Chunk 1
        chimeric_topic = DiscoveredTopic(
            label="Architettura Sistema",
            short_description=desc_c0,
            evidence_ids=eids_c1,
        )
        chimeric_run = TopicDiscoveryResult(
            topics=(chimeric_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_chimeric_test",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        with pytest.raises(TopicProvenanceError) as exc_info:
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=chimeric_topic,
                discovery_result=chimeric_run,
                discovery_run_id="run_chimeric_test",
                client=client,
            )
        assert "alterata" in str(exc_info.value).lower() or "modificato" in str(exc_info.value).lower()

    def test_red_patch_currently_allows_cross_chunk_collage_evidence(self, temp_checkpoint_dir):
        """
        TEST RED (FAIL-CLOSED INTEGRITY):
        Dimostra che la patch corrente accetta erroneamente un collage trans-chunk di evidence_id:
        un ID preso dal Chunk 0 e un ID preso dal Chunk 1, mai apparsi insieme come record unitario.
        Deve fallire tassativamente con TopicProvenanceError.
        """
        doc = _make_synthetic_document("doc_collage_01", num_messages=6)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig(max_chunk_bundles=3)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        desc_c0 = "Descrizione autentica specifica del blocco zero."
        desc_c1 = "Descrizione autentica estesa specifica del blocco uno."
        eids_c0 = ("msg_0001::ORIGINAL_TEXT", "msg_0002::ORIGINAL_TEXT")
        eids_c1 = ("msg_0004::ORIGINAL_TEXT", "msg_0005::ORIGINAL_TEXT")

        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c0, evidence_ids=eids_c0),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_col",
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=1,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c1, evidence_ids=eids_c1),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_col",
        )

        # Collage: 1 ID da Chunk 0 + 1 ID da Chunk 1
        collage_topic = DiscoveredTopic(
            label="Architettura Sistema",
            short_description=desc_c0,
            evidence_ids=("msg_0001::ORIGINAL_TEXT", "msg_0005::ORIGINAL_TEXT"),
        )
        collage_run = TopicDiscoveryResult(
            topics=(collage_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_collage_test",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        with pytest.raises(TopicProvenanceError) as exc_info:
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=collage_topic,
                discovery_result=collage_run,
                discovery_run_id="run_collage_test",
                client=client,
            )
        assert "evidence_id" in str(exc_info.value).lower() or "alterati" in str(exc_info.value).lower()

    def test_red_patch_currently_allows_canonical_collision_distinct_label(self, temp_checkpoint_dir):
        """
        TEST RED (FAIL-CLOSED INTEGRITY):
        Se un topic presenta una label differente da quelle salvate in SQLite, anche se condivide
        lo stesso stem o chiave canonica (es. singolare vs plurale), deve essere rifiutato
        senza ereditare le descrizioni o evidenze dell'altro topic per collisione di canonical_key.
        """
        doc = _make_synthetic_document("doc_canon_col_01", num_messages=4)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig()
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        desc = "Descrizione autentica presente nel blocco SQLite."
        eid = "msg_0001::ORIGINAL_TEXT"

        # Salvataggio con label esatta al singolare
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Sessione di allenamento", short_description=desc, evidence_ids=(eid,)),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=1,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_can",
        )

        # Run con label differente (es. "Sessioni di allenamento") che potrebbe collidere sulla stessa canonical key
        collided_topic = DiscoveredTopic(
            label="Sessioni di allenamento",
            short_description=desc,
            evidence_ids=(eid,),
        )
        collided_run = TopicDiscoveryResult(
            topics=(collided_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_canon_test",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        with pytest.raises(TopicProvenanceError) as exc_info:
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=collided_topic,
                discovery_result=collided_run,
                discovery_run_id="run_canon_test",
                client=client,
            )

    def test_positive_recovered_run_with_exact_aggregate_description_and_evidence(self, temp_checkpoint_dir):
        """
        TEST POSITIVO OBBLIGATORIO:
        Run recuperata con descrizione aggregata ed evidenze aggregate esatte -> accettata con successo.
        """
        doc = _make_synthetic_document("doc_pos_agg_01", num_messages=6)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig(max_chunk_bundles=3)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        desc_c0 = "Descrizione specifica del blocco zero."
        desc_c1 = "Descrizione estesa e approfondita specifica del blocco uno."
        eids_c0 = ("msg_0001::ORIGINAL_TEXT", "msg_0002::ORIGINAL_TEXT")
        eids_c1 = ("msg_0004::ORIGINAL_TEXT", "msg_0005::ORIGINAL_TEXT")

        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c0, evidence_ids=eids_c0),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_pos",
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=1,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c1, evidence_ids=eids_c1),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_pos",
        )

        # L'aggregato consolidato deterministico contiene desc_c1 (la più estesa) ed eids_c0 + eids_c1
        agg_topic = DiscoveredTopic(
            label="Architettura Sistema",
            short_description=desc_c1,
            evidence_ids=eids_c0 + eids_c1,
        )
        agg_run = TopicDiscoveryResult(
            topics=(agg_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_pos_agg",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        det_res, cons_res = service.verify_discovered_topic(
            document=doc,
            discovered_topic=agg_topic,
            discovery_result=agg_run,
            discovery_run_id="run_pos_agg",
            client=client,
        )
        assert det_res.decision == TopicDecision.PRESENT
        assert cons_res.status in (ConsistencyStatus.CONSISTENT, ConsistencyStatus.PARTIALLY_CONSISTENT)

    def test_positive_recovered_run_with_authentic_single_chunk_pair(self, temp_checkpoint_dir):
        """
        TEST POSITIVO OBBLIGATORIO:
        Coppia autentica interamente di un singolo chunk (descrizione + evidenze esatte di quel chunk)
        -> accettata nel percorso autorizzato.
        """
        doc = _make_synthetic_document("doc_pos_chunk_01", num_messages=6)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig(max_chunk_bundles=3)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        desc_c0 = "Descrizione specifica del blocco zero."
        desc_c1 = "Descrizione estesa e approfondita specifica del blocco uno."
        eids_c0 = ("msg_0001::ORIGINAL_TEXT", "msg_0002::ORIGINAL_TEXT")
        eids_c1 = ("msg_0004::ORIGINAL_TEXT", "msg_0005::ORIGINAL_TEXT")

        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c0, evidence_ids=eids_c0),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_sc",
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=1,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c1, evidence_ids=eids_c1),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_sc",
        )

        # Run con la coppia autentica congiunta esatta del Chunk 0: (desc_c0, eids_c0)
        chunk0_topic = DiscoveredTopic(
            label="Architettura Sistema",
            short_description=desc_c0,
            evidence_ids=eids_c0,
        )
        chunk0_run = TopicDiscoveryResult(
            topics=(chunk0_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_pos_chunk0",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        det_res, cons_res = service.verify_discovered_topic(
            document=doc,
            discovered_topic=chunk0_topic,
            discovery_result=chunk0_run,
            discovery_run_id="run_pos_chunk0",
            client=client,
        )
        assert det_res.decision == TopicDecision.PRESENT
        assert cons_res.status in (ConsistencyStatus.CONSISTENT, ConsistencyStatus.PARTIALLY_CONSISTENT)

    def test_positive_authoritative_registration_of_recovered_run_preserved(self, temp_checkpoint_dir):
        """
        TEST POSITIVO OBBLIGATORIO:
        Registrazione autoritativa di run recuperata resta corretta, idempotente e immutabile.
        """
        doc = _make_synthetic_document("doc_pos_reg_01", num_messages=4)
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig()
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        topic = DiscoveredTopic(
            label="Architettura Sistema",
            short_description="Descrizione autentica di test.",
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(topic,),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=1,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_reg",
        )

        rec_run = TopicDiscoveryResult(
            topics=(topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_reg_test_01",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        # Prima registrazione: successo
        registered_id = service.register_discovery_run(doc.document_id, rec_run)
        assert registered_id == "run_reg_test_01"
        assert service.get_discovery_run(doc.document_id, "run_reg_test_01") is not None

        # Registrazione idempotente con stesso oggetto: successo
        idemp_id = service.register_discovery_run(doc.document_id, rec_run)
        assert idemp_id == "run_reg_test_01"

        # Tentativo di sovrascrittura con run differente: rifiuto per violazione immutabilità
        tampered_run = TopicDiscoveryResult(
            topics=(DiscoveredTopic(label="Altro", short_description="Desc", evidence_ids=("msg_0001::ORIGINAL_TEXT",)),),
            provenance_document_id=doc.document_id,
            metadata=dict(rec_run.metadata),
        )
        with pytest.raises(TopicProvenanceError) as exc_info:
            service.register_discovery_run(doc.document_id, tampered_run)
        assert "immutabilità violata" in str(exc_info.value).lower()



    def test_red_patch_currently_allows_arbitrary_subset_evidence(self, temp_checkpoint_dir):
        """
        TEST RED (FAIL-CLOSED INTEGRITY):
        Dimostra che la patch corrente accetta erroneamente un sottoinsieme arbitrario di evidenze
        (es. un singolo ID quando il chunk ne conteneva due), mai registrato autonomamente.
        Deve fallire tassativamente con TopicProvenanceError.
        """
        doc = _make_synthetic_document("doc_subset_01", num_messages=4)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig()
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        desc_c0 = "Descrizione autentica specifica del blocco zero."
        eids_c0 = ("msg_0001::ORIGINAL_TEXT", "msg_0002::ORIGINAL_TEXT")

        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c0, evidence_ids=eids_c0),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=1,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_sub",
        )

        # Sottoinsieme arbitrario: solo il primo ID
        subset_topic = DiscoveredTopic(
            label="Architettura Sistema",
            short_description=desc_c0,
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
        )
        subset_run = TopicDiscoveryResult(
            topics=(subset_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_subset_test",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        with pytest.raises(TopicProvenanceError) as exc_info:
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=subset_topic,
                discovery_result=subset_run,
                discovery_run_id="run_subset_test",
                client=client,
            )
        assert "evidence_id" in str(exc_info.value).lower() or "alterati" in str(exc_info.value).lower()

    def test_red_patch_currently_allows_canonical_collision_distinct_label(self, temp_checkpoint_dir):
        """
        TEST RED (FAIL-CLOSED INTEGRITY):
        Se un topic presenta una label differente da quelle salvate in SQLite, anche se condivide
        lo stesso stem o chiave canonica (es. singolare vs plurale), deve essere rifiutato
        senza ereditare le descrizioni o evidenze dell'altro topic per collisione di canonical_key.
        """
        doc = _make_synthetic_document("doc_canon_col_01", num_messages=4)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig()
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        desc = "Descrizione autentica presente nel blocco SQLite."
        eid = "msg_0001::ORIGINAL_TEXT"

        # Salvataggio con label esatta al singolare
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Sessione di allenamento", short_description=desc, evidence_ids=(eid,)),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=1,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_can",
        )

        # Run con label differente (es. "Sessioni di allenamento") che potrebbe collidere sulla stessa canonical key
        collided_topic = DiscoveredTopic(
            label="Sessioni di allenamento",
            short_description=desc,
            evidence_ids=(eid,),
        )
        collided_run = TopicDiscoveryResult(
            topics=(collided_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_canon_test",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        with pytest.raises(TopicProvenanceError) as exc_info:
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=collided_topic,
                discovery_result=collided_run,
                discovery_run_id="run_canon_test",
                client=client,
            )

    def test_positive_recovered_run_with_exact_aggregate_description_and_evidence(self, temp_checkpoint_dir):
        """
        TEST POSITIVO OBBLIGATORIO:
        Run recuperata con descrizione aggregata ed evidenze aggregate esatte -> accettata con successo.
        """
        doc = _make_synthetic_document("doc_pos_agg_01", num_messages=6)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig(max_chunk_bundles=3)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        desc_c0 = "Descrizione specifica del blocco zero."
        desc_c1 = "Descrizione estesa e approfondita specifica del blocco uno."
        eids_c0 = ("msg_0001::ORIGINAL_TEXT", "msg_0002::ORIGINAL_TEXT")
        eids_c1 = ("msg_0004::ORIGINAL_TEXT", "msg_0005::ORIGINAL_TEXT")

        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c0, evidence_ids=eids_c0),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_pos",
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=1,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c1, evidence_ids=eids_c1),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_pos",
        )

        # L'aggregato consolidato deterministico contiene desc_c1 (la più estesa) ed eids_c0 + eids_c1
        agg_topic = DiscoveredTopic(
            label="Architettura Sistema",
            short_description=desc_c1,
            evidence_ids=eids_c0 + eids_c1,
        )
        agg_run = TopicDiscoveryResult(
            topics=(agg_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_pos_agg",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        det_res, cons_res = service.verify_discovered_topic(
            document=doc,
            discovered_topic=agg_topic,
            discovery_result=agg_run,
            discovery_run_id="run_pos_agg",
            client=client,
        )
        assert det_res.decision == TopicDecision.PRESENT
        assert cons_res.status in (ConsistencyStatus.CONSISTENT, ConsistencyStatus.PARTIALLY_CONSISTENT)

    def test_positive_recovered_run_with_authentic_single_chunk_pair(self, temp_checkpoint_dir):
        """
        TEST POSITIVO OBBLIGATORIO:
        Coppia autentica interamente di un singolo chunk (descrizione + evidenze esatte di quel chunk)
        -> accettata nel percorso autorizzato.
        """
        doc = _make_synthetic_document("doc_pos_chunk_01", num_messages=6)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig(max_chunk_bundles=3)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        desc_c0 = "Descrizione specifica del blocco zero."
        desc_c1 = "Descrizione estesa e approfondita specifica del blocco uno."
        eids_c0 = ("msg_0001::ORIGINAL_TEXT", "msg_0002::ORIGINAL_TEXT")
        eids_c1 = ("msg_0004::ORIGINAL_TEXT", "msg_0005::ORIGINAL_TEXT")

        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c0, evidence_ids=eids_c0),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_sc",
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=1,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c1, evidence_ids=eids_c1),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_sc",
        )

        # Run con la coppia autentica congiunta esatta del Chunk 0: (desc_c0, eids_c0)
        chunk0_topic = DiscoveredTopic(
            label="Architettura Sistema",
            short_description=desc_c0,
            evidence_ids=eids_c0,
        )
        chunk0_run = TopicDiscoveryResult(
            topics=(chunk0_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_pos_chunk0",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        det_res, cons_res = service.verify_discovered_topic(
            document=doc,
            discovered_topic=chunk0_topic,
            discovery_result=chunk0_run,
            discovery_run_id="run_pos_chunk0",
            client=client,
        )
        assert det_res.decision == TopicDecision.PRESENT
        assert cons_res.status in (ConsistencyStatus.CONSISTENT, ConsistencyStatus.PARTIALLY_CONSISTENT)

    def test_positive_authoritative_registration_of_recovered_run_preserved(self, temp_checkpoint_dir):
        """
        TEST POSITIVO OBBLIGATORIO:
        Registrazione autoritativa di run recuperata resta corretta, idempotente e immutabile.
        """
        doc = _make_synthetic_document("doc_pos_reg_01", num_messages=4)
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig()
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        topic = DiscoveredTopic(
            label="Architettura Sistema",
            short_description="Descrizione autentica di test.",
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(topic,),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=1,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_reg",
        )

        rec_run = TopicDiscoveryResult(
            topics=(topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_reg_test_01",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        # Prima registrazione: successo
        registered_id = service.register_discovery_run(doc.document_id, rec_run)
        assert registered_id == "run_reg_test_01"
        assert service.get_discovery_run(doc.document_id, "run_reg_test_01") is not None

        # Registrazione idempotente con stesso oggetto: successo
        idemp_id = service.register_discovery_run(doc.document_id, rec_run)
        assert idemp_id == "run_reg_test_01"

        # Tentativo di sovrascrittura con run differente: rifiuto per violazione immutabilità
        tampered_run = TopicDiscoveryResult(
            topics=(DiscoveredTopic(label="Altro", short_description="Desc", evidence_ids=("msg_0001::ORIGINAL_TEXT",)),),
            provenance_document_id=doc.document_id,
            metadata=dict(rec_run.metadata),
        )
        with pytest.raises(TopicProvenanceError) as exc_info:
            service.register_discovery_run(doc.document_id, tampered_run)
        assert "immutabilità violata" in str(exc_info.value).lower()



    def test_red_patch_currently_allows_reordered_evidence_list(self, temp_checkpoint_dir):
        """
        TEST RED (FAIL-CLOSED INTEGRITY):
        Dimostra che la patch corrente accetta erroneamente una lista riordinata/rimescolata
        degli evidence_id, violando il contratto posizionale deterministico della sequenza.
        Deve fallire tassativamente con TopicProvenanceError.
        """
        doc = _make_synthetic_document("doc_reorder_01", num_messages=4)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig()
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        desc_c0 = "Descrizione autentica specifica del blocco zero."
        eids_c0 = ("msg_0001::ORIGINAL_TEXT", "msg_0002::ORIGINAL_TEXT")

        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c0, evidence_ids=eids_c0),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=1,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_ord",
        )

        # Sequenza invertita
        reordered_topic = DiscoveredTopic(
            label="Architettura Sistema",
            short_description=desc_c0,
            evidence_ids=("msg_0002::ORIGINAL_TEXT", "msg_0001::ORIGINAL_TEXT"),
        )
        reordered_run = TopicDiscoveryResult(
            topics=(reordered_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_reorder_test",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        with pytest.raises(TopicProvenanceError) as exc_info:
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=reordered_topic,
                discovery_result=reordered_run,
                discovery_run_id="run_reorder_test",
                client=client,
            )
        assert "evidence_id" in str(exc_info.value).lower() or "alterati" in str(exc_info.value).lower()

    def test_red_patch_currently_allows_canonical_collision_distinct_label(self, temp_checkpoint_dir):
        """
        TEST RED (FAIL-CLOSED INTEGRITY):
        Se un topic presenta una label differente da quelle salvate in SQLite, anche se condivide
        lo stesso stem o chiave canonica (es. singolare vs plurale), deve essere rifiutato
        senza ereditare le descrizioni o evidenze dell'altro topic per collisione di canonical_key.
        """
        doc = _make_synthetic_document("doc_canon_col_01", num_messages=4)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig()
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        desc = "Descrizione autentica presente nel blocco SQLite."
        eid = "msg_0001::ORIGINAL_TEXT"

        # Salvataggio con label esatta al singolare
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Sessione di allenamento", short_description=desc, evidence_ids=(eid,)),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=1,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_can",
        )

        # Run con label differente (es. "Sessioni di allenamento") che potrebbe collidere sulla stessa canonical key
        collided_topic = DiscoveredTopic(
            label="Sessioni di allenamento",
            short_description=desc,
            evidence_ids=(eid,),
        )
        collided_run = TopicDiscoveryResult(
            topics=(collided_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_canon_test",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        with pytest.raises(TopicProvenanceError) as exc_info:
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=collided_topic,
                discovery_result=collided_run,
                discovery_run_id="run_canon_test",
                client=client,
            )

    def test_positive_recovered_run_with_exact_aggregate_description_and_evidence(self, temp_checkpoint_dir):
        """
        TEST POSITIVO OBBLIGATORIO:
        Run recuperata con descrizione aggregata ed evidenze aggregate esatte -> accettata con successo.
        """
        doc = _make_synthetic_document("doc_pos_agg_01", num_messages=6)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig(max_chunk_bundles=3)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        desc_c0 = "Descrizione specifica del blocco zero."
        desc_c1 = "Descrizione estesa e approfondita specifica del blocco uno."
        eids_c0 = ("msg_0001::ORIGINAL_TEXT", "msg_0002::ORIGINAL_TEXT")
        eids_c1 = ("msg_0004::ORIGINAL_TEXT", "msg_0005::ORIGINAL_TEXT")

        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c0, evidence_ids=eids_c0),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_pos",
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=1,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c1, evidence_ids=eids_c1),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_pos",
        )

        # L'aggregato consolidato deterministico contiene desc_c1 (la più estesa) ed eids_c0 + eids_c1
        agg_topic = DiscoveredTopic(
            label="Architettura Sistema",
            short_description=desc_c1,
            evidence_ids=eids_c0 + eids_c1,
        )
        agg_run = TopicDiscoveryResult(
            topics=(agg_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_pos_agg",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        det_res, cons_res = service.verify_discovered_topic(
            document=doc,
            discovered_topic=agg_topic,
            discovery_result=agg_run,
            discovery_run_id="run_pos_agg",
            client=client,
        )
        assert det_res.decision == TopicDecision.PRESENT
        assert cons_res.status in (ConsistencyStatus.CONSISTENT, ConsistencyStatus.PARTIALLY_CONSISTENT)

    def test_positive_recovered_run_with_authentic_single_chunk_pair(self, temp_checkpoint_dir):
        """
        TEST POSITIVO OBBLIGATORIO:
        Coppia autentica interamente di un singolo chunk (descrizione + evidenze esatte di quel chunk)
        -> accettata nel percorso autorizzato.
        """
        doc = _make_synthetic_document("doc_pos_chunk_01", num_messages=6)
        client = SyntheticControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig(max_chunk_bundles=3)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        desc_c0 = "Descrizione specifica del blocco zero."
        desc_c1 = "Descrizione estesa e approfondita specifica del blocco uno."
        eids_c0 = ("msg_0001::ORIGINAL_TEXT", "msg_0002::ORIGINAL_TEXT")
        eids_c1 = ("msg_0004::ORIGINAL_TEXT", "msg_0005::ORIGINAL_TEXT")

        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c0, evidence_ids=eids_c0),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_sc",
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=1,
            result=TopicDiscoveryResult(
                topics=(DiscoveredTopic(label="Architettura Sistema", short_description=desc_c1, evidence_ids=eids_c1),),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_sc",
        )

        # Run con la coppia autentica congiunta esatta del Chunk 0: (desc_c0, eids_c0)
        chunk0_topic = DiscoveredTopic(
            label="Architettura Sistema",
            short_description=desc_c0,
            evidence_ids=eids_c0,
        )
        chunk0_run = TopicDiscoveryResult(
            topics=(chunk0_topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_pos_chunk0",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        det_res, cons_res = service.verify_discovered_topic(
            document=doc,
            discovered_topic=chunk0_topic,
            discovery_result=chunk0_run,
            discovery_run_id="run_pos_chunk0",
            client=client,
        )
        assert det_res.decision == TopicDecision.PRESENT
        assert cons_res.status in (ConsistencyStatus.CONSISTENT, ConsistencyStatus.PARTIALLY_CONSISTENT)

    def test_positive_authoritative_registration_of_recovered_run_preserved(self, temp_checkpoint_dir):
        """
        TEST POSITIVO OBBLIGATORIO:
        Registrazione autoritativa di run recuperata resta corretta, idempotente e immutabile.
        """
        doc = _make_synthetic_document("doc_pos_reg_01", num_messages=4)
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        cfg = OperationalAnalysisConfig()
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        topic = DiscoveredTopic(
            label="Architettura Sistema",
            short_description="Descrizione autentica di test.",
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=TopicDiscoveryResult(
                topics=(topic,),
                provenance_document_id=doc.document_id,
                metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
            ),
            total_planned_chunks=1,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
            run_id="chk_run_reg",
        )

        rec_run = TopicDiscoveryResult(
            topics=(topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "run_reg_test_01",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "loading_mode": LoadingMode.CHECKPOINT_RESTORE.value,
                "is_persisted_recovery": True,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "max_topics": 10,
            },
        )
        service = LocalTopicAnalysisService(
            checkpoint_dir=temp_checkpoint_dir,
            config=cfg,
            enable_checkpointing=True,
            checkpoint_store=store,
        )

        # Prima registrazione: successo
        registered_id = service.register_discovery_run(doc.document_id, rec_run)
        assert registered_id == "run_reg_test_01"
        assert service.get_discovery_run(doc.document_id, "run_reg_test_01") is not None

        # Registrazione idempotente con stesso oggetto: successo
        idemp_id = service.register_discovery_run(doc.document_id, rec_run)
        assert idemp_id == "run_reg_test_01"

        # Tentativo di sovrascrittura con run differente: rifiuto per violazione immutabilità
        tampered_run = TopicDiscoveryResult(
            topics=(DiscoveredTopic(label="Altro", short_description="Desc", evidence_ids=("msg_0001::ORIGINAL_TEXT",)),),
            provenance_document_id=doc.document_id,
            metadata=dict(rec_run.metadata),
        )
        with pytest.raises(TopicProvenanceError) as exc_info:
            service.register_discovery_run(doc.document_id, tampered_run)
        assert "immutabilità violata" in str(exc_info.value).lower()




