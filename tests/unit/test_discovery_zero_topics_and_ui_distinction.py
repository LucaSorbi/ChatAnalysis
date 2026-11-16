"""
tests/unit/test_discovery_zero_topics_and_ui_distinction.py
-----------------------------------------------------------
Test suite a prova di bomba per la risoluzione del bug critico "Zero Argomenti":
1. 26 chunk completati con almeno un topic per alcuni chunk.
2. 26 chunk completati, tutti con output tematico vuoto (COMPLETE_EMPTY_UNVERIFIED).
3. Distinzione netta tra argomenti estratti (N) e corrispondenti al filtro di ricerca (M).
4. Ricerca con filtro vuoto vs ricerca con filtro senza corrispondenze.
5. Persistenza durevole in SQLite (nessuna cancellazione accidentale via clear_checkpoint).
6. Ripristino integro dopo riavvio del servizio / Streamlit.
7. Isolamento rigoroso e conservazione dei topic al cambio di conversazione.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from ai.backend import BaseLocalLlmClient, LlmCompletionResponse
from ai.models import ConversationEvidenceDocument, DiscoveredTopic, TopicDiscoveryResult
from analysis.checkpoint import AnalysisCheckpointStore, compute_document_fingerprint
from analysis.models import ChunkDiagnosticCategory, ChunkLifecycleStatus, DocumentAnalysisState
from analysis.service import LocalTopicAnalysisService
from importer.models import RawRecord
from validation.models import ValidationResult
from multimodal.evidence import EvidenceSourceType, MessageEvidenceBundle, TextEvidenceSection
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from search.index import EvidenceIndex
from search.service import SearchService
from search.topics import TopicSearchEngine
from ui.application import search_topic_discoveries
from unified.models import UnifiedMessage


def _make_mock_evidence_doc(doc_id: str, num_chunks: int = 26) -> ConversationEvidenceDocument:
    bundles: list[MessageEvidenceBundle] = []
    for i in range(num_chunks):
        raw = RawRecord(
            source_name="whatsapp",
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
            source_name="whatsapp",
            source_record_id=str(i),
            record_type="message",
            timestamp=ts,
            message_type=CanonicalMessageType.TEXT,
            text_content=f"Messaggio {i+1}: abbiamo implementato crittografia avanzata e sicurezza dei dati nel sistema.",
            media_reference=None,
        )
        msg = UnifiedMessage(
            message_id=f"msg_{doc_id}_{i+1:04d}",
            source_name="whatsapp",
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
        source_name="whatsapp",
        metadata={"data_classification": "SYNTHETIC_DEMO"},
    )


class MockScriptedLlmClient(BaseLocalLlmClient):
    """Client simulato controllato per test deterministici di Discovery."""

    def __init__(self, responses_by_chunk: Mapping[int, list[dict[str, Any]]], model_id: str = "qwen2.5-7b-instruct"):
        super().__init__()
        self.responses_by_chunk = dict(responses_by_chunk)
        self.model_id = model_id
        self._models = (model_id,)
        self.call_history: list[int] = []
        self.request_listener = None

    def is_available(self, timeout_seconds: float = 2.0) -> bool:
        return True

    def list_models(self) -> tuple[str, ...]:
        return self._models

    def chat_completion(self, messages: Sequence[Mapping[str, str]], **kwargs: Any) -> LlmCompletionResponse:
        chunk_idx = len(self.call_history)
        self.call_history.append(chunk_idx)
        topics = self.responses_by_chunk.get(chunk_idx, [])
        payload = {"topics": topics}
        return LlmCompletionResponse(
            content=json.dumps(payload),
            model=self.model_id,
            prompt_tokens=120,
            completion_tokens=40 + len(topics) * 30,
            total_tokens=160 + len(topics) * 30,
            raw_response={"finish_reason": "stop"},
        )


class TestDiscoveryZeroTopicsAndUiDistinction:
    """Test suite di verifica formale per bug zero argomenti e requisiti UI."""

    def test_01_discovery_26_chunks_with_topics_preserves_checkpoints(self):
        """Requisito 7.1 & 6: 26 chunk con alcuni topic, persistenza durevole SQLite."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            chk_dir = Path(tmp_dir)
            doc = _make_mock_evidence_doc("doc_26_with_topics", num_chunks=26)

            # Primi 5 chunk estraggono topic, restanti 21 vuoti
            responses: dict[int, list[dict[str, Any]]] = {}
            for i in range(26):
                if i < 5:
                    responses[i] = [
                        {
                            "label": f"Argomento Reale {i + 1}",
                            "short_description": f"Descrizione tema {i + 1}",
                            "evidence_ids": ["E001"],
                        }
                    ]
                else:
                    responses[i] = []

            client = MockScriptedLlmClient(responses)
            service = LocalTopicAnalysisService(checkpoint_dir=chk_dir, max_chunk_bundles=1)

            res = service.run_topic_discovery(document=doc, client=client, max_topics=8)

            assert res.metadata["status"] == "COMPLETE"
            assert len(res.topics) == 5
            assert res.metadata["completed_chunk_count"] == 26
            assert res.metadata["failed_chunk_count"] == 0

            # Verifica semantica diagnostica chunk
            diags = res.metadata["chunk_diagnostics"]
            assert len(diags) == 26
            for idx, d in enumerate(diags):
                if idx < 5:
                    assert d["thematic_status"] == "TOPICS_EXTRACTED"
                    assert d["topics_count"] == 1
                else:
                    assert d["thematic_status"] == "EMPTY_TOPICS"
                    assert d["topics_count"] == 0

            # Verifica che il checkpoint NON sia stato cancellato via clear_checkpoint!
            store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)
            doc_fp = compute_document_fingerprint(doc)
            meta = store.get_latest_checkpoint_metadata_for_document(doc.document_id, document_fingerprint=doc_fp)
            assert meta is not None, "Il checkpoint non deve essere eliminato al completamento della Discovery!"

            # Nuova istanza service carica da SQLite
            fresh_svc = LocalTopicAnalysisService(checkpoint_dir=chk_dir, max_chunk_bundles=1)
            recovered = fresh_svc.load_persisted_discovery_result(doc)
            assert recovered is not None
            assert len(recovered.topics) == 5
            assert recovered.metadata["status"] == "COMPLETE"

    def test_02_discovery_26_chunks_all_empty_flags_complete_empty_unverified(self):
        """Requisito 5 & 7.2: 26 chunk tutti vuoti su testo sostanziale -> COMPLETE_EMPTY_UNVERIFIED."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            chk_dir = Path(tmp_dir)
            doc = _make_mock_evidence_doc("doc_26_all_empty", num_chunks=26)

            # Tutti i 26 chunk producono output tematico vuoto
            responses = {i: [] for i in range(26)}
            client = MockScriptedLlmClient(responses)
            service = LocalTopicAnalysisService(checkpoint_dir=chk_dir, max_chunk_bundles=1)

            res = service.run_topic_discovery(document=doc, client=client, max_topics=8)

            # Verifica semantica del successo: NON deve essere dichiarato successo tematico pieno
            assert res.metadata["status"] == "COMPLETE_EMPTY_UNVERIFIED"
            assert len(res.topics) == 0
            assert res.metadata["completed_chunk_count"] == 26
            assert res.metadata["failed_chunk_count"] == 0

            # Checkpoint deve essere conservato per audit
            store = AnalysisCheckpointStore(checkpoint_dir=chk_dir)
            doc_fp = compute_document_fingerprint(doc)
            meta = store.get_latest_checkpoint_metadata_for_document(doc.document_id, document_fingerprint=doc_fp)
            assert meta is not None

            # Riavvio service ripristina fedelmente lo stato COMPLETE_EMPTY_UNVERIFIED
            fresh_svc = LocalTopicAnalysisService(checkpoint_dir=chk_dir, max_chunk_bundles=1)
            recovered = fresh_svc.load_persisted_discovery_result(doc)
            assert recovered is not None
            assert len(recovered.topics) == 0
            assert recovered.metadata["status"] == "COMPLETE_EMPTY_UNVERIFIED"

    def test_03_ui_and_search_distinguishes_extracted_from_matching_counts(self):
        """Requisito 7: UI e SearchService distinguono Argomenti estratti: N da Corrispondenti: M."""
        doc = _make_mock_evidence_doc("doc_search_distinction", num_chunks=3)
        topics = (
            DiscoveredTopic(label="Organizzazione Viaggio", short_description="Pianificazione vacanze e voli", evidence_ids=("msg_doc_search_distinction_0001::ORIGINAL_TEXT",)),
            DiscoveredTopic(label="Preparazione Esami", short_description="Studio universitario e parziali", evidence_ids=("msg_doc_search_distinction_0002::ORIGINAL_TEXT",)),
            DiscoveredTopic(label="Allenamento Sportivo", short_description="Corsa e sessioni in palestra", evidence_ids=("msg_doc_search_distinction_0003::ORIGINAL_TEXT",)),
        )
        disc_res = TopicDiscoveryResult(
            topics=topics,
            provenance_document_id=doc.document_id,
            metadata={"status": "COMPLETE", "run_id": "run_ui_dist_001"},
        )

        idx = EvidenceIndex.from_document(doc)
        search_svc = SearchService(index=idx, discovery_results=(disc_res,))

        # Caso A: Query vuota -> tutti gli argomenti corrispondono
        res_empty_q = search_topic_discoveries(search_svc, query_text="")
        assert res_empty_q.metadata.get("total_discovered_topics") == 3
        assert len(res_empty_q.hits) == 3

        # Caso B: Query specifica per "Esami" -> 3 estratti in totale, 1 corrispondente al filtro
        res_filter_match = search_topic_discoveries(search_svc, query_text="Esami")
        assert res_filter_match.metadata.get("total_discovered_topics") == 3
        assert len(res_filter_match.hits) == 1
        assert res_filter_match.hits[0].label == "Preparazione Esami"

        # Caso C: Query senza corrispondenza ("Logistica Inesistente") -> 3 estratti in totale, 0 corrispondenti
        res_filter_nomatch = search_topic_discoveries(search_svc, query_text="Logistica Inesistente")
        assert res_filter_nomatch.metadata.get("total_discovered_topics") == 3
        assert len(res_filter_nomatch.hits) == 0

    def test_04_multi_conversation_switching_preserves_isolated_discovery(self):
        """Requisito 6 & 7: Cambio di conversazione e ritorno alla prima preserva i topic."""
        doc_a = _make_mock_evidence_doc("doc_conv_A", num_chunks=2)
        doc_b = _make_mock_evidence_doc("doc_conv_B", num_chunks=2)

        topic_a = DiscoveredTopic(label="Tema Chat A", short_description="Descrizione Chat A", evidence_ids=("msg_doc_conv_A_0001::ORIGINAL_TEXT",))
        topic_b = DiscoveredTopic(label="Tema Chat B", short_description="Descrizione Chat B", evidence_ids=("msg_doc_conv_B_0001::ORIGINAL_TEXT",))

        res_a = TopicDiscoveryResult(topics=(topic_a,), provenance_document_id=doc_a.document_id, metadata={"run_id": "run_a"})
        res_b = TopicDiscoveryResult(topics=(topic_b,), provenance_document_id=doc_b.document_id, metadata={"run_id": "run_b"})

        state_a = DocumentAnalysisState(document_id=doc_a.document_id)
        state_b = DocumentAnalysisState(document_id=doc_b.document_id)

        state_a.add_discovery_result(res_a)
        state_b.add_discovery_result(res_b)

        assert len(state_a.discovery_results) == 1
        assert state_a.discovery_results[0].topics[0].label == "Tema Chat A"

        assert len(state_b.discovery_results) == 1
        assert state_b.discovery_results[0].topics[0].label == "Tema Chat B"

        # Simulazione passaggio a B e ritorno ad A
        active_state = state_b
        assert active_state.discovery_results[0].topics[0].label == "Tema Chat B"
        active_state = state_a
        assert active_state.discovery_results[0].topics[0].label == "Tema Chat A"
