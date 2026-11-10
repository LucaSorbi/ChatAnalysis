"""
tests/unit/test_priority1_recovered_discovery_prova_del_nove.py
---------------------------------------------------------------
Test suite obbligatoria per Priorità 1 (Windows Live Readiness Audit):
- Discovery LIVE registrata: verifica consentita.
- Discovery MOCK autorizzata per test: verifica simulata consentita.
- Discovery recuperata da SQLite autentico: verifica consentita dopo validazione.
- Run recuperata alterata: rifiutata.
- Documento diverso: rifiutato.
- Fingerprint non compatibile: rifiutato.
- Topic modificato: rifiutato.
- Evidenze estranee: rifiutate.
- Origine mancante e non ricostruibile: comportamento fail-closed.

Test end-to-end obbligatorio:
run_topic_discovery -> checkpoint SQLite -> nuova istanza service -> load_persisted_discovery_result -> verify_discovered_topic -> ConsistencyService
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
    AnalysisOrigin,
    AttestationLevel,
    ConsistencyResult,
    ConsistencyStatus,
    DiscoveredTopicRef,
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
from analysis.consistency import ConsistencyService


class FakeControlledLlmClient(BaseLlmClient):
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
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float = 30.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LlmCompletionResponse:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        prompt_str = " ".join(m.get("content", "") for m in messages)

        # Risposta per Topic Discovery
        if "topic" in prompt_str.lower() and "discovery" in prompt_str.lower():
            content = json.dumps({
                "topics": [
                    {
                        "label": "Sicurezza Informatica",
                        "short_description": "Discussione su crittografia e protezione dati",
                        "evidence_ids": ["E001"],
                    }
                ]
            })
            return LlmCompletionResponse(
                content=content,
                model=model_id or self.model_id,
                latency_seconds=0.1,
                prompt_tokens=50,
                completion_tokens=40,
                total_tokens=90,
            )

        # Risposta per Topic Detection (Prova del Nove)
        eid = "msg_0001::ORIGINAL_TEXT" if "ORIGINAL_TEXT" in prompt_str else "E001"
        content = json.dumps({
            "decision": "PRESENT",
            "evidence_ids": [eid],
            "rationale": "Il messaggio discute esplicitamente di protezione dati crittografica.",
        })
        return LlmCompletionResponse(
            content=content,
            model=model_id or self.model_id,
            latency_seconds=0.1,
            prompt_tokens=40,
            completion_tokens=30,
            total_tokens=70,
        )


def _make_test_document(doc_id: str = "doc_p1_test", num_messages: int = 4) -> ConversationEvidenceDocument:
    """Costruisce un documento ConversationEvidenceDocument sintetico con evidenze forensi stabili."""
    from datetime import datetime, timezone
    bundles: list[MessageEvidenceBundle] = []
    for i in range(num_messages):
        raw = RawRecord(
            source_name="whatsapp_forensic_export",
            source_path="/path/test",
            source_record_id=str(i),
            record_type="message",
            raw_fields={"data": f"Messaggio {i+1} sulla sicurezza informatica crittografata", "timestamp": "2026-10-20T10:00:00Z"},
            media_reference=None,
            metadata={},
        )
        val = ValidationResult(record=raw, issues=())
        ts = NormalizedTimestamp(
            status=TimestampTzStatus.KNOWN_UTC,
            utc_datetime=datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc),
            naive_datetime=None,
            iso_string="2026-10-20T10:00:00Z",
            raw_value="2026-10-20T10:00:00Z",
        )
        norm = NormalizedRecord(
            raw_record=raw,
            validation_result=val,
            source_name="whatsapp_forensic_export",
            source_record_id=str(i),
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=f"Messaggio {i+1}: abbiamo implementato crittografia avanzata e sicurezza dei dati.",
            media_reference=None,
        )
        msg = UnifiedMessage(
            message_id=f"msg_{i+1:04d}",
            source_name="whatsapp_forensic_export",
            source_record_id=str(i),
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
        source_name="whatsapp_forensic_export",
        metadata={"data_classification": "SYNTHETIC_DEMO"},
    )


class TestPriority1RecoveredDiscoveryProvaDelNove:
    """Suite completa per i test obbligatori di Priorità 1."""

    @pytest.fixture
    def temp_checkpoint_dir(self):
        d = tempfile.mkdtemp(prefix="test_chk_p1_")
        yield Path(d)
        shutil.rmtree(d, ignore_errors=True)

    def test_01_discovery_live_registered_verification_allowed(self, temp_checkpoint_dir):
        """Discovery LIVE registrata: verifica consentita."""
        doc = _make_test_document("doc_live_01")
        client = FakeControlledLlmClient()
        service = LocalTopicAnalysisService(checkpoint_dir=temp_checkpoint_dir)

        # Simula esecuzione live autoritativa
        run_res = service.run_topic_discovery(document=doc, client=client)
        assert len(run_res.topics) >= 1
        live_run_id = run_res.metadata["run_id"]
        topic = run_res.topics[0]

        # Verifica Prova del Nove consentita
        det_res, cons_res = service.verify_discovered_topic(
            document=doc,
            discovered_topic=topic,
            discovery_run_id=live_run_id,
            client=client,
        )
        assert det_res.decision == TopicDecision.PRESENT
        assert cons_res.status in (ConsistencyStatus.CONSISTENT, ConsistencyStatus.PARTIALLY_CONSISTENT)

    def test_02_discovery_mock_authorized_verification_allowed(self, temp_checkpoint_dir):
        """Discovery MOCK autorizzata per test: verifica simulata consentita."""
        doc = _make_test_document("doc_mock_01")
        client = FakeControlledLlmClient()
        service = LocalTopicAnalysisService(checkpoint_dir=temp_checkpoint_dir)

        topic = DiscoveredTopic(
            label="Sicurezza Informatica",
            short_description="Discussione su crittografia e protezione dati",
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
        )
        disc_res = TopicDiscoveryResult(
            topics=(topic,),
            provenance_document_id=doc.document_id,
            metadata={"run_id": "run_mock_auth_01", "origin": AnalysisOrigin.MOCK_INSPECTION.value},
        )
        # Registra tramite test seam esplicito autorizzato
        service.register_mock_discovery_run(doc.document_id, disc_res)

        det_res, cons_res = service.verify_discovered_topic(
            document=doc,
            discovered_topic=topic,
            discovery_run_id="run_mock_auth_01",
            client=client,
        )
        assert det_res.decision == TopicDecision.PRESENT

    def test_03_discovery_recovered_from_authentic_sqlite_verification_allowed(self, temp_checkpoint_dir):
        """Discovery recuperata da SQLite autentico: verifica consentita dopo validazione."""
        doc = _make_test_document("doc_recov_01")
        client = FakeControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)

        doc_fp = compute_document_fingerprint(doc)
        cfg = OperationalAnalysisConfig()
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)

        topic = DiscoveredTopic(
            label="Sicurezza Informatica",
            short_description="Discussione su crittografia e protezione dati",
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
        )
        chunk_res = TopicDiscoveryResult(
            topics=(topic,),
            provenance_document_id=doc.document_id,
            metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value, "run_id": "chk_run_123"},
        )
        # Salvataggio nel checkpoint SQLite
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
            run_id="chk_run_123",
        )

        # Nuova istanza di service priva di registrazioni live in-memory
        fresh_service = LocalTopicAnalysisService(checkpoint_dir=temp_checkpoint_dir)
        recovered = fresh_service.load_persisted_discovery_result(doc)
        assert recovered is not None
        assert recovered.metadata["origin"] == AnalysisOrigin.LIVE_LM_STUDIO.value
        assert recovered.metadata["loading_mode"] == LoadingMode.CHECKPOINT_RESTORE.value
        assert recovered.metadata["attestation_level"] == AttestationLevel.CHECKPOINT_AUTHENTICATED.value

        rec_topic = recovered.topics[0]
        # La Prova del Nove deve validare contro SQLite e consentire l'esecuzione
        det_res, cons_res = fresh_service.verify_discovered_topic(
            document=doc,
            discovered_topic=rec_topic,
            discovery_result=recovered,
            client=client,
        )
        assert det_res.decision == TopicDecision.PRESENT

    def test_04_run_recuperata_alterata_rifiutata(self, temp_checkpoint_dir):
        """Run recuperata alterata: rifiutata."""
        doc = _make_test_document("doc_alter_01")
        client = FakeControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)

        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational")

        topic = DiscoveredTopic(
            label="Sicurezza Informatica",
            short_description="Discussione originale",
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
        )
        chunk_res = TopicDiscoveryResult(
            topics=(topic,),
            provenance_document_id=doc.document_id,
            metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
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
        )

        service = LocalTopicAnalysisService(checkpoint_dir=temp_checkpoint_dir)
        recovered = service.load_persisted_discovery_result(doc)
        assert recovered is not None

        # Alteriamo la run recuperata aggiungendo un topic mai esistito nel checkpoint SQLite
        forged_topic = DiscoveredTopic(
            label="Topic Forgiato Estraneo",
            short_description="Descrizione forgiata",
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
        )
        altered_res = TopicDiscoveryResult(
            topics=(topic, forged_topic),
            provenance_document_id=doc.document_id,
            metadata=dict(recovered.metadata),
        )

        with pytest.raises(TopicProvenanceError, match="Topic modificato o non autentico|Integrit|non appartiene alla run"):
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=forged_topic,
                discovery_result=altered_res,
                client=client,
            )

    def test_05_documento_diverso_rifiutato(self, temp_checkpoint_dir):
        """Documento diverso: rifiutato."""
        doc_a = _make_test_document("doc_doc_a")
        doc_b = _make_test_document("doc_doc_b")
        client = FakeControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)

        doc_a_fp = compute_document_fingerprint(doc_a)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational")

        topic = DiscoveredTopic(
            label="Sicurezza Informatica",
            short_description="Discussione sicurezza",
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
        )
        chunk_res = TopicDiscoveryResult(
            topics=(topic,),
            provenance_document_id=doc_a.document_id,
            metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
        )
        store.save_chunk(
            document_id=doc_a.document_id,
            document_fingerprint=doc_a_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=0,
            result=chunk_res,
            total_planned_chunks=1,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.LIVE_LM_STUDIO.value,
        )

        service = LocalTopicAnalysisService(checkpoint_dir=temp_checkpoint_dir)
        recovered_a = service.load_persisted_discovery_result(doc_a)
        assert recovered_a is not None

        # Tentativo di verificare la run di doc_a contro doc_b
        with pytest.raises(TopicProvenanceError, match="Discrepanza di provenienza|Documento diverso"):
            service.verify_discovered_topic(
                document=doc_b,
                discovered_topic=recovered_a.topics[0],
                discovery_result=recovered_a,
                client=client,
            )

    def test_06_fingerprint_non_compatibile_rifiutato(self, temp_checkpoint_dir):
        """Fingerprint non compatibile: rifiutato."""
        doc = _make_test_document("doc_fp_mismatch")
        client = FakeControlledLlmClient()
        service = LocalTopicAnalysisService(checkpoint_dir=temp_checkpoint_dir)

        topic = DiscoveredTopic(
            label="Sicurezza Informatica",
            short_description="Discussione sicurezza",
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
        )
        fake_rec = TopicDiscoveryResult(
            topics=(topic,),
            provenance_document_id=doc.document_id,
            metadata={
                "run_id": "persisted_deadbeef",
                "origin": AnalysisOrigin.LIVE_LM_STUDIO.value,
                "is_persisted_recovery": True,
                "document_fingerprint": "incompatible_fake_fingerprint_1234567890",
                "config_fingerprint": "fake_cfg_1234",
            },
        )

        with pytest.raises(TopicProvenanceError, match="document fingerprint|non compatibile|fail-closed|non autenticata"):
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=topic,
                discovery_result=fake_rec,
                client=client,
            )

    def test_07_topic_modificato_rifiutato(self, temp_checkpoint_dir):
        """Topic modificato (descrizione o evidenze alterate): rifiutato."""
        doc = _make_test_document("doc_mod_topic")
        client = FakeControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)

        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational")

        orig_topic = DiscoveredTopic(
            label="Sicurezza Informatica",
            short_description="Descrizione autentica",
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
        )
        chunk_res = TopicDiscoveryResult(
            topics=(orig_topic,),
            provenance_document_id=doc.document_id,
            metadata={"origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
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
        )

        service = LocalTopicAnalysisService(checkpoint_dir=temp_checkpoint_dir)
        recovered = service.load_persisted_discovery_result(doc)
        assert recovered is not None

        # Modifica della descrizione
        tampered_topic = DiscoveredTopic(
            label="Sicurezza Informatica",
            short_description="Descrizione ALTERATA e contraffatta",
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
        )
        with pytest.raises(TopicProvenanceError, match="Topic modificato|Discrepanza nei dati del topic|non autentico"):
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=tampered_topic,
                discovery_result=recovered,
                client=client,
            )

    def test_08_evidenze_estranee_rifiutate(self, temp_checkpoint_dir):
        """Evidenze estranee al documento: rifiutate."""
        doc = _make_test_document("doc_foreign_eids")
        client = FakeControlledLlmClient()
        service = LocalTopicAnalysisService(checkpoint_dir=temp_checkpoint_dir)

        foreign_topic = DiscoveredTopic(
            label="Sicurezza Informatica",
            short_description="Discussione sicurezza",
            evidence_ids=("msg_9999_foreign::text",),  # Non appartiene a doc
        )
        disc_res = TopicDiscoveryResult(
            topics=(foreign_topic,),
            provenance_document_id=doc.document_id,
            metadata={"run_id": "run_foreign_eids", "origin": AnalysisOrigin.MOCK_INSPECTION.value},
        )
        service.register_mock_discovery_run(doc.document_id, disc_res)

        with pytest.raises(TopicProvenanceError, match="Evidence ID estraneo"):
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=foreign_topic,
                discovery_run_id="run_foreign_eids",
                client=client,
            )

    def test_09_origine_mancante_e_non_ricostruibile_fail_closed(self, temp_checkpoint_dir):
        """Origine mancante e non ricostruibile: comportamento fail-closed."""
        doc = _make_test_document("doc_missing_origin")
        client = FakeControlledLlmClient()
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)

        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational")

        topic = DiscoveredTopic(
            label="Sicurezza Informatica",
            short_description="Discussione senza origine",
            evidence_ids=("msg_0001::ORIGINAL_TEXT",),
        )
        # Salvataggio privo di metadati origin
        chunk_res = TopicDiscoveryResult(
            topics=(topic,),
            provenance_document_id=doc.document_id,
            metadata={},
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
            origin="",
            run_id="",
        )

        service = LocalTopicAnalysisService(checkpoint_dir=temp_checkpoint_dir)
        recovered = service.load_persisted_discovery_result(doc)
        assert recovered is not None
        assert recovered.metadata.get("origin") is None
        assert recovered.metadata.get("attestation_level") == AttestationLevel.UNATTESTED.value

        # La verifica deve rifiutare con fail-closed
        with pytest.raises(TopicProvenanceError, match="origin mancante"):
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=recovered.topics[0],
                discovery_result=recovered,
                client=client,
            )

    def test_10_mandatory_e2e_discovery_to_checkpoint_to_new_service_to_verify(self, temp_checkpoint_dir):
        """
        TEST END-TO-END OBBLIGATORIO:
        run_topic_discovery -> checkpoint SQLite -> nuova istanza service -> load_persisted_discovery_result -> verify_discovered_topic -> ConsistencyService
        Non sostituisce questo percorso con la registrazione manuale di una run MOCK_INSPECTION.
        """
        doc = _make_test_document("doc_e2e_mandatory")
        client = FakeControlledLlmClient()

        # 1. Istanza 1: run_topic_discovery
        service_instance_1 = LocalTopicAnalysisService(checkpoint_dir=temp_checkpoint_dir)
        discovery_res = service_instance_1.run_topic_discovery(
            document=doc,
            client=client,
        )
        assert len(discovery_res.topics) >= 1
        discovered_topic = discovery_res.topics[0]
        original_run_id = discovery_res.metadata["run_id"]

        # 2. Verifica che il checkpoint SQLite sia stato effettivamente creato
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)
        doc_fp = compute_document_fingerprint(doc)
        chk_meta = store.get_latest_checkpoint_metadata_for_document(doc.document_id, document_fingerprint=doc_fp)
        assert chk_meta is not None
        assert chk_meta["document_fingerprint"] == doc_fp

        # 3. Istanza 2: NUOVA ISTANZA del service (memoria vergine, live runs vuote)
        service_instance_2 = LocalTopicAnalysisService(checkpoint_dir=temp_checkpoint_dir)
        assert service_instance_2.get_discovery_run(doc.document_id, original_run_id) is None

        # 4. Recupero da checkpoint tramite load_persisted_discovery_result()
        recovered_result = service_instance_2.load_persisted_discovery_result(doc)
        assert recovered_result is not None
        assert recovered_result.metadata["loading_mode"] == LoadingMode.CHECKPOINT_RESTORE.value
        assert recovered_result.metadata["attestation_level"] == AttestationLevel.CHECKPOINT_AUTHENTICATED.value
        assert recovered_result.metadata["origin"] in (AnalysisOrigin.LIVE_LM_STUDIO.value, AnalysisOrigin.MOCK_INSPECTION.value)
        assert len(recovered_result.topics) >= 1

        recovered_topic_to_verify = recovered_result.topics[0]
        assert recovered_topic_to_verify.label == discovered_topic.label

        # 5. verify_discovered_topic() sulla nuova istanza con il topic recuperato
        detection_res, consistency_res = service_instance_2.verify_discovered_topic(
            document=doc,
            discovered_topic=recovered_topic_to_verify,
            discovery_result=recovered_result,
            client=client,
        )

        # 6. Valutazione di consistenza
        assert isinstance(detection_res, TopicDetectionResult)
        assert isinstance(consistency_res, ConsistencyResult)
        assert detection_res.decision == TopicDecision.PRESENT
        assert consistency_res.status in (ConsistencyStatus.CONSISTENT, ConsistencyStatus.PARTIALLY_CONSISTENT)
        assert consistency_res.discovery_run_id == recovered_result.metadata["run_id"]
        assert consistency_res.detection_run_id == detection_res.metadata["run_id"]

    def test_11_multi_chunk_aggregated_topic_authenticated_for_verification(self, temp_checkpoint_dir):
        """
        A3: Autenticazione rigorosa di una Discovery run recuperata con topic aggregato da più chunk.
        Chunk 1: Topic 'Deposito', Evidence ID: E001
        Chunk 2: Topic 'Deposito', Evidence ID: E002
        Aggregazione deterministica: Topic 'Deposito', Evidence IDs: E001, E002
        Dimostra che la Prova del Nove NON fallisce erroneamente per discrepanza evidence_id
        rispetto a un singolo chunk, ma ricostruisce determinististicamente l'aggregato.
        """
        doc = _make_test_document("doc_multi_chunk_a3", num_messages=4)
        doc_fp = compute_document_fingerprint(doc)
        client = FakeControlledLlmClient()

        cfg = OperationalAnalysisConfig(max_chunk_bundles=2)
        service = LocalTopicAnalysisService(checkpoint_dir=temp_checkpoint_dir, config=cfg)
        cfg_hash = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)

        # Preparazione chunk 0 (messaggi 1-2) e chunk 1 (messaggi 3-4)
        e001 = doc.all_evidence_sections[0].evidence_id
        e002 = doc.all_evidence_sections[2].evidence_id

        chunk0_res = TopicDiscoveryResult(
            topics=(
                DiscoveredTopic(
                    label="Deposito",
                    short_description="Discussione sul deposito cauzionale iniziale",
                    evidence_ids=(e001,),
                ),
            ),
            provenance_document_id=doc.document_id,
            metadata={"origin": AnalysisOrigin.MOCK_INSPECTION.value, "chunk_id": f"{doc.document_id}::chunk_0"},
        )
        chunk1_res = TopicDiscoveryResult(
            topics=(
                DiscoveredTopic(
                    label="Deposito",
                    short_description="Discussione sul deposito cauzionale e saldo finale completo",
                    evidence_ids=(e002,),
                ),
            ),
            provenance_document_id=doc.document_id,
            metadata={"origin": AnalysisOrigin.MOCK_INSPECTION.value, "chunk_id": f"{doc.document_id}::chunk_1"},
        )

        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_hash,
            chunk_idx=0,
            result=chunk0_res,
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.MOCK_INSPECTION.value,
            run_id="run_a3_test",
        )
        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_hash,
            chunk_idx=1,
            result=chunk1_res,
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.MOCK_INSPECTION.value,
            run_id="run_a3_test",
        )

        # Caricamento della run recuperata da SQLite
        recovered_run = service.load_persisted_discovery_result(doc)
        assert recovered_run is not None
        assert len(recovered_run.topics) == 1
        agg_topic = recovered_run.topics[0]
        assert agg_topic.label == "Deposito"
        assert agg_topic.evidence_ids == (e001, e002)
        assert "saldo finale completo" in agg_topic.short_description

        # Verifica di consistenza semantica mediante inferenze distinte con topic aggregato multi-chunk
        detection_res, consistency_res = service.verify_discovered_topic(
            document=doc,
            discovered_topic=agg_topic,
            discovery_result=recovered_run,
            client=client,
        )
        assert isinstance(detection_res, TopicDetectionResult)
        assert isinstance(consistency_res, ConsistencyResult)
        assert consistency_res.status in (ConsistencyStatus.CONSISTENT, ConsistencyStatus.PARTIALLY_CONSISTENT)

        # Verifica protezione da topic alterato (es. evidence IDs manipolati)
        altered_topic = DiscoveredTopic(
            label="Deposito",
            short_description=agg_topic.short_description,
            evidence_ids=(e001,),
        )
        with pytest.raises(TopicProvenanceError) as exc_info:
            service.verify_discovered_topic(
                document=doc,
                discovered_topic=altered_topic,
                discovery_result=recovered_run,
                client=client,
            )
        assert "evidence_id" in str(exc_info.value).lower() or "alterati" in str(exc_info.value).lower()

    def test_12_multi_chunk_partial_discovery_preserves_partial_and_authenticates(self, temp_checkpoint_dir):
        """
        A3: Discovery parziale preserva lo stato PARTIAL,
        ricostruisce deterministicamente l'aggregato dei soli chunk validati
        e ne consente l'autenticazione senza mai promuoverla a COMPLETE.
        """
        doc = _make_test_document("doc_partial_a3", num_messages=4)
        doc_fp = compute_document_fingerprint(doc)
        client = FakeControlledLlmClient()

        cfg = OperationalAnalysisConfig(max_chunk_bundles=2)
        service = LocalTopicAnalysisService(checkpoint_dir=temp_checkpoint_dir, config=cfg)
        cfg_hash = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg)
        store = AnalysisCheckpointStore(checkpoint_dir=temp_checkpoint_dir)

        e001 = doc.all_evidence_sections[0].evidence_id

        chunk0_res = TopicDiscoveryResult(
            topics=(
                DiscoveredTopic(
                    label="Contratto",
                    short_description="Termini del contratto di locazione",
                    evidence_ids=(e001,),
                ),
            ),
            provenance_document_id=doc.document_id,
            metadata={"origin": AnalysisOrigin.MOCK_INSPECTION.value, "chunk_id": f"{doc.document_id}::chunk_0"},
        )

        store.save_chunk(
            document_id=doc.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_hash,
            chunk_idx=0,
            result=chunk0_res,
            total_planned_chunks=2,
            analysis_type="discovery",
            model_id="qwen2.5-7b-instruct",
            prompt_version="discovery_operational",
            origin=AnalysisOrigin.MOCK_INSPECTION.value,
            run_id="run_a3_partial_test",
        )

        recovered_run = service.load_persisted_discovery_result(doc)
        assert recovered_run is not None
        # La run deve rimanere rigorosamente PARTIAL
        assert recovered_run.metadata.get("status") == "PARTIAL"

        # Autenticazione del topic parziale deve avere successo per le evidenze autentiche
        part_topic = recovered_run.topics[0]
        det_res, cons_res = service.verify_discovered_topic(
            document=doc,
            discovered_topic=part_topic,
            discovery_result=recovered_run,
            client=client,
        )
        assert isinstance(det_res, TopicDetectionResult)
        assert isinstance(cons_res, ConsistencyResult)

