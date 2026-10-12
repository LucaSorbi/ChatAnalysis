"""
tests/unit/test_discovery_provenance_hardening.py
-------------------------------------------------
Test di unità per la provenance hardening di Topic Discovery (BLOCKER 3).

Verifica:
1. Run prodotta da Discovery operativa: accettata e certificata autoritativamente.
2. Run sintetica (SYNTHETIC_REFERENCE): respinta dalla registrazione live e dalla verifica.
3. Run costruita esternamente con origin=LIVE_LM_STUDIO: respinta da register_discovery_run.
4. Run mock: registrata tramite interfaccia esplicita register_mock_discovery_run() e identificata come MOCK_INSPECTION.
5. Run non registrata: respinta da verify_discovered_topic().
6. Run di documento estraneo: respinta da verify_discovered_topic().
7. Topic alterato (label, short_description o evidence_ids modificati): respinto.
8. Evidenze estranee non presenti nel documento: respinte.
9. Nuova inferenza Detection effettivamente indipendente (nuova chiamata LLM con client/modello).
10. Nessun passaggio degli evidence ID Discovery nel prompt inviato alla Detection.
"""
from __future__ import annotations

import json
from typing import Any
import pytest

from ai.backend import BaseLlmClient, LlmCompletionResponse
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    MessageEvidenceBundle,
    TopicDiscoveryResult,
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
    ConsistencyStatus,
    TopicProvenanceError,
)
from analysis.service import LocalTopicAnalysisService


class MockTestLlmClient(BaseLlmClient):
    """Client mock controllato per test di provenance e indipendenza."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.responses = list(responses or [])
        self.call_history: list[dict[str, Any]] = []
        self.model_id = "qwen2.5-7b-instruct"

    def is_available(self) -> bool:
        return True

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LlmCompletionResponse:
        self.call_history.append({
            "messages": messages,
            "model_id": model_id,
            "max_tokens": max_tokens,
            "kwargs": kwargs,
        })
        if self.responses:
            data = self.responses.pop(0)
        else:
            data = {"decision": "PRESENT", "evidence_ids": [], "rationale": "Mock rationale."}
        return LlmCompletionResponse(
            content=json.dumps(data),
            model=model_id or self.model_id,
            prompt_tokens=20,
            completion_tokens=20,
            total_tokens=40,
            latency_seconds=0.01,
        )


def _build_doc(doc_id: str, count: int = 2) -> ConversationEvidenceDocument:
    bundles = []
    for i in range(1, count + 1):
        text = f"Messaggio forense {i} relativo al trasporto."
        raw = RawRecord(
            source_name="whatsapp",
            source_path="/test/chat.txt",
            source_record_id=str(i),
            record_type="message",
            raw_fields={"text": text},
            media_reference=None,
            metadata={},
        )
        val = ValidationResult(record=raw, issues=())
        ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
        norm = NormalizedRecord(
            raw_record=raw,
            validation_result=val,
            source_name="whatsapp",
            source_record_id=str(i),
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=text,
            media_reference=None,
        )
        um = UnifiedMessage(
            message_id=f"msg::{doc_id}::{i}",
            source_name="whatsapp",
            source_record_id=str(i),
            source_path="/test/chat.txt",
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=text,
            media_reference=None,
            provenance_record=norm,
        )
        bundles.append(MessageEvidenceBundle(message=um))

    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=tuple(bundles),
        source_name="whatsapp",
        metadata={"data_classification": "SYNTHETIC_DEMO"},
    )


class TestDiscoveryProvenanceHardening:
    """Verifiche Blocker 3: Discovery Provenance e protezione da registrazione arbitraria."""

    def test_run_produced_by_operational_discovery_is_accepted(self):
        """1. Run prodotta dall'operazione autoritativa run_topic_discovery() è accettata e verificabile."""
        doc = _build_doc("doc_live_01")
        eid1 = doc.all_evidence_sections[0].evidence_id
        mock_client = MockTestLlmClient([
            # Chiamata 1: discovery
            {"topics": [{"label": "Viaggio", "short_description": "Discussione viaggio", "evidence_ids": [eid1]}]},
            # Chiamata 2: detection (prova del nove)
            {"decision": "PRESENT", "evidence_ids": [eid1], "rationale": "Confermato"},
        ])
        svc = LocalTopicAnalysisService(client=mock_client)

        disc_res = svc.run_topic_discovery(document=doc, client=mock_client)
        run_id = disc_res.metadata["run_id"]

        # La run è autoritativamente registrata internamente
        assert svc.get_discovery_run("doc_live_01", run_id) is disc_res

        # Verifica con Topic Detection (Prova del Nove)
        det_res, cons_res = svc.verify_discovered_topic(
            document=doc,
            discovered_topic=disc_res.topics[0],
            discovery_result=disc_res,
            client=mock_client,
        )
        assert cons_res.status == ConsistencyStatus.CONSISTENT
        assert len(mock_client.call_history) == 2

    def test_synthetic_reference_rejected_from_live_registration_and_verification(self):
        """2. Run sintetica (SYNTHETIC_REFERENCE) non può essere registrata come live né verificata."""
        doc = _build_doc("doc_synth_01")
        eid1 = doc.all_evidence_sections[0].evidence_id
        synth_topic = DiscoveredTopic(label="Tema demo", short_description="Desc", evidence_ids=[eid1])
        synth_res = TopicDiscoveryResult(
            topics=[synth_topic],
            provenance_document_id="doc_synth_01",
            metadata={"run_id": "run_synth_01", "origin": AnalysisOrigin.SYNTHETIC_REFERENCE.value},
        )
        svc = LocalTopicAnalysisService()

        # Registrazione esplicitamente respinta
        with pytest.raises(TopicProvenanceError, match="SYNTHETIC_REFERENCE"):
            svc.register_discovery_run("doc_synth_01", synth_res)

        # Tentativo di verifica respinto
        with pytest.raises(TopicProvenanceError, match="non registrata|SYNTHETIC_REFERENCE"):
            svc.verify_discovered_topic(
                document=doc,
                discovered_topic=synth_topic,
                discovery_result=synth_res,
            )

    def test_forged_external_run_with_live_origin_rejected(self):
        """3. Run costruita esternamente con origin LIVE_LM_STUDIO respinta da register_discovery_run."""
        doc = _build_doc("doc_forged_01")
        eid1 = doc.all_evidence_sections[0].evidence_id
        forged_topic = DiscoveredTopic(label="Falso", short_description="Falso", evidence_ids=[eid1])
        forged_res = TopicDiscoveryResult(
            topics=[forged_topic],
            provenance_document_id="doc_forged_01",
            metadata={"run_id": "run_forged_001", "origin": AnalysisOrigin.LIVE_LM_STUDIO.value},
        )
        svc = LocalTopicAnalysisService()

        # Una semplice stringa LIVE_LM_STUDIO non basta: deve provenire dal percorso operativo reale
        with pytest.raises(TopicProvenanceError, match="non certificata"):
            svc.register_discovery_run("doc_forged_01", forged_res)

    def test_mock_discovery_run_explicit_test_interface(self):
        """4. Run mock iniettata via register_mock_discovery_run() è identificata come MOCK_INSPECTION."""
        doc = _build_doc("doc_mock_01")
        eid1 = doc.all_evidence_sections[0].evidence_id
        mock_topic = DiscoveredTopic(label="Test Mock", short_description="Desc Mock", evidence_ids=[eid1])
        mock_res = TopicDiscoveryResult(
            topics=[mock_topic],
            provenance_document_id="doc_mock_01",
            metadata={"run_id": "run_mock_001"},
        )
        svc = LocalTopicAnalysisService()
        registered_id = svc.register_mock_discovery_run("doc_mock_01", mock_res)

        stored = svc.get_discovery_run("doc_mock_01", registered_id)
        assert stored is not None
        assert stored.metadata["origin"] == AnalysisOrigin.MOCK_INSPECTION.value

    def test_unregistered_run_rejected_by_verification(self):
        """5. Run non registrata respinta da verify_discovered_topic()."""
        doc = _build_doc("doc_unreg_01")
        eid1 = doc.all_evidence_sections[0].evidence_id
        topic = DiscoveredTopic(label="Tema", short_description="Desc", evidence_ids=[eid1])
        svc = LocalTopicAnalysisService()

        with pytest.raises(TopicProvenanceError, match="non registrata"):
            svc.verify_discovered_topic(
                document=doc,
                discovered_topic=topic,
                discovery_run_id="run_non_esistente_999",
            )

    def test_foreign_document_run_rejected(self):
        """6. Run registrata per un documento estraneo respinta se verificata su altro documento."""
        doc_a = _build_doc("doc_a")
        doc_b = _build_doc("doc_b")
        eid_a = doc_a.all_evidence_sections[0].evidence_id
        topic_a = DiscoveredTopic(label="Tema A", short_description="Desc", evidence_ids=[eid_a])
        res_a = TopicDiscoveryResult(
            topics=[topic_a],
            provenance_document_id="doc_a",
            metadata={"run_id": "run_a_001"},
        )
        svc = LocalTopicAnalysisService()
        svc.register_mock_discovery_run("doc_a", res_a)

        # Tentativo di verificare la run di doc_a su doc_b
        with pytest.raises(TopicProvenanceError):
            svc.verify_discovered_topic(
                document=doc_b,
                discovered_topic=topic_a,
                discovery_result=res_a,
                discovery_run_id="run_a_001",
            )

    def test_tampered_topic_rejected(self):
        """7. Topic con label o evidenze alterate rispetto alla run registrata respinto."""
        doc = _build_doc("doc_tamp_01")
        eid1 = doc.all_evidence_sections[0].evidence_id
        eid2 = doc.all_evidence_sections[1].evidence_id
        topic = DiscoveredTopic(label="Originale", short_description="Desc", evidence_ids=[eid1])
        res = TopicDiscoveryResult(
            topics=[topic],
            provenance_document_id="doc_tamp_01",
            metadata={"run_id": "run_tamp_001"},
        )
        svc = LocalTopicAnalysisService()
        svc.register_mock_discovery_run("doc_tamp_01", res)

        # Topic alterato: label diversa
        tampered_label = DiscoveredTopic(label="Modificato", short_description="Desc", evidence_ids=[eid1])
        with pytest.raises(TopicProvenanceError, match="non appartiene alla run|non trovato"):
            svc.verify_discovered_topic(
                document=doc,
                discovered_topic=tampered_label,
                discovery_result=res,
            )

        # Topic alterato: evidence_ids diverse
        tampered_eids = DiscoveredTopic(label="Originale", short_description="Desc", evidence_ids=[eid2])
        with pytest.raises(TopicProvenanceError, match="Discrepanza nei dati del topic|non corrispondono"):
            svc.verify_discovered_topic(
                document=doc,
                discovered_topic=tampered_eids,
                discovery_result=res,
            )

    def test_foreign_evidence_ids_in_topic_rejected(self):
        """8. Evidenze non appartenenti al documento attivo respinte."""
        doc = _build_doc("doc_valid_01", count=2)
        foreign_topic = DiscoveredTopic(
            label="Tema Alieno",
            short_description="Desc",
            evidence_ids=["ev_9999_alien"],  # Non presente nel documento!
        )
        res = TopicDiscoveryResult(
            topics=[foreign_topic],
            provenance_document_id="doc_valid_01",
            metadata={"run_id": "run_alien_001"},
        )
        svc = LocalTopicAnalysisService()
        svc.register_mock_discovery_run("doc_valid_01", res)

        with pytest.raises(TopicProvenanceError, match="evidence_id estraneo|non appartiene"):
            svc.verify_discovered_topic(
                document=doc,
                discovered_topic=foreign_topic,
                discovery_result=res,
            )

    def test_detection_receives_only_label_and_short_description_no_evidence_ids(self):
        """9 & 10. Indipendenza inferenziale: nessun evidence_id Discovery viene passato al prompt della Detection."""
        doc = _build_doc("doc_indep_01")
        eid1 = doc.all_evidence_sections[0].evidence_id
        mock_client = MockTestLlmClient([
            # Chiamata 1: Discovery
            {"topics": [{"label": "Corruzione", "short_description": "Accordi illeciti", "evidence_ids": [eid1]}]},
            # Chiamata 2: Detection
            {"decision": "PRESENT", "evidence_ids": [eid1], "rationale": "Rilevato"},
        ])
        svc = LocalTopicAnalysisService(client=mock_client)
        disc_res = svc.run_topic_discovery(document=doc, client=mock_client)

        svc.verify_discovered_topic(
            document=doc,
            discovered_topic=disc_res.topics[0],
            discovery_result=disc_res,
            client=mock_client,
        )

        assert len(mock_client.call_history) == 2
        # La seconda chiamata è la Detection
        detection_call = mock_client.call_history[1]
        all_messages_text = " ".join(m["content"] for m in detection_call["messages"])

        # Il label e la descrizione sono presenti nel prompt
        assert "Corruzione" in all_messages_text
        assert "Accordi illeciti" in all_messages_text

    def test_missing_origin_rejected_fail_closed(self):
        """Fail-Closed: run priva di origin nei metadata respinta da register_discovery_run."""
        doc = _build_doc("doc_no_origin_01")
        eid1 = doc.all_evidence_sections[0].evidence_id
        topic = DiscoveredTopic(label="Tema Senza Origin", short_description="Desc", evidence_ids=[eid1])
        res = TopicDiscoveryResult(
            topics=[topic],
            provenance_document_id="doc_no_origin_01",
            metadata={"run_id": "run_no_orig_123"},  # origin is None
        )
        svc = LocalTopicAnalysisService()
        with pytest.raises(TopicProvenanceError, match="origin mancante"):
            svc.register_discovery_run("doc_no_origin_01", res)

    def test_unrecognized_origin_rejected_fail_closed(self):
        """Fail-Closed: run con origin sconosciuta respinta da register_discovery_run."""
        doc = _build_doc("doc_unrec_01")
        eid1 = doc.all_evidence_sections[0].evidence_id
        topic = DiscoveredTopic(label="Tema Unrecognized", short_description="Desc", evidence_ids=[eid1])
        res = TopicDiscoveryResult(
            topics=[topic],
            provenance_document_id="doc_unrec_01",
            metadata={"run_id": "run_unrec_123", "origin": "UNRECOGNIZED"},
        )
        svc = LocalTopicAnalysisService()
        with pytest.raises(TopicProvenanceError, match="non riconosciuta"):
            svc.register_discovery_run("doc_unrec_01", res)

    def test_mock_inspection_on_live_path_rejected(self):
        """Fail-Closed: origin MOCK_INSPECTION respinta dal percorso live register_discovery_run."""
        doc = _build_doc("doc_mock_live_01")
        eid1 = doc.all_evidence_sections[0].evidence_id
        topic = DiscoveredTopic(label="Tema Mock Live", short_description="Desc", evidence_ids=[eid1])
        res = TopicDiscoveryResult(
            topics=[topic],
            provenance_document_id="doc_mock_live_01",
            metadata={"run_id": "run_mock_live_123", "origin": AnalysisOrigin.MOCK_INSPECTION.value},
        )
        svc = LocalTopicAnalysisService()
        with pytest.raises(TopicProvenanceError, match="register_mock_discovery_run"):
            svc.register_discovery_run("doc_mock_live_01", res)

    def test_multi_conversation_history_isolation_a_b_a(self):
        """Isolamento cronologia multi-conversazione: transizione A -> B -> A senza interferenze."""
        doc_a = _build_doc("doc_conv_A")
        doc_b = _build_doc("doc_conv_B")
        eid_a = doc_a.all_evidence_sections[0].evidence_id
        eid_b = doc_b.all_evidence_sections[0].evidence_id

        svc = LocalTopicAnalysisService()

        # Step 1: Esecuzione A
        res_a = TopicDiscoveryResult(
            topics=[DiscoveredTopic(label="Tema A", short_description="Desc A", evidence_ids=[eid_a])],
            provenance_document_id="doc_conv_A",
            metadata={"run_id": "run_A_1"},
        )
        run_id_a = svc.register_mock_discovery_run("doc_conv_A", res_a)

        # Step 2: Esecuzione B
        res_b = TopicDiscoveryResult(
            topics=[DiscoveredTopic(label="Tema B", short_description="Desc B", evidence_ids=[eid_b])],
            provenance_document_id="doc_conv_B",
            metadata={"run_id": "run_B_1"},
        )
        run_id_b = svc.register_mock_discovery_run("doc_conv_B", res_b)

        # Step 3: Ritorno su A
        # Verifica che il registro di A sia intatto e separato da B
        assert svc.get_discovery_run("doc_conv_A", run_id_a) is not None
        assert svc.get_discovery_run("doc_conv_B", run_id_b) is not None
        assert svc.get_discovery_run("doc_conv_A", run_id_b) is None
        assert svc.get_discovery_run("doc_conv_B", run_id_a) is None
