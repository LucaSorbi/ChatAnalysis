"""
tests/unit/test_smoke_operational_ai.py
---------------------------------------
Suite di test obbligatoria per il Release Gate e l'Hardening dello Smoke Test Operativo AI.

Copre in modo esaustivo:
1. Smoke test positivo in modalità simulata (--fake) -> Exit Code 0, esito SUCCESSO.
2. Smoke test con contaminazione simulata -> Exit Code != 0 (1), esito FALLITO, nessuna falsa riuscita.
3. Smoke test con zero topic scoperti -> errore operativo esplicito, Exit Code 1.
4. Smoke test con JSON/completion invalida -> errore operativo, Exit Code 1.
5. LM Studio non disponibile in modalità live -> Exit Code 2, NOT_VERIFIED, mai PASS.
6. Ingestion reale WhatsApp TXT end-to-end attraverso la vera pipeline applicativa.
7. Ingestion reale archivio WhatsApp ZIP attraverso la vera pipeline applicativa.
8. Isolamento cross-document rigoroso (DEMO vs FILE reale).
9. Accesso corretto al campo .rationale di TopicDetectionResult e tracciamento metadata.
10. Preservazione fedele del risultato scientifico per il caso di controllo (incluso ABSENT).
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ai.backend import BaseLlmClient, LlmCompletionResponse
from ai.models import (
    ConversationEvidenceDocument,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from analysis.service import LocalTopicAnalysisService
from scripts.smoke_operational_ai import (
    DEFAULT_SYNTHETIC_CHAT_CONTENT,
    FakeSmokeClient,
    ensure_synthetic_fixtures,
    run_smoke_test,
)
from ui.demo import build_synthetic_demo_dataset
from ui.ingestion import ingest_file_payload
from ui.models import IngestionStatus, SourceFormat


class TestSmokeOperationalAiHardening:
    """Verifica e hardening dei release gate dello smoke test operativo."""

    def test_smoke_test_fake_normal_passes_exit_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lo smoke test simulato deterministico deve superare tutti i 13 controlli e terminare con Exit 0."""
        exit_code = run_smoke_test(use_fake=True)
        captured = capsys.readouterr().out

        assert exit_code == 0
        assert "ESITO SMOKE TEST OPERATIVO: SUCCESSO" in captured
        assert "multi_conv_cross_isolation     : PASSED (Zero leak)" in captured
        assert "verification_classification    : MOCK_VERIFIED" in captured
        assert "[PASSED] CHECK_BACKEND_AVAILABILITY" in captured
        assert "[PASSED] CHECK_CROSS_DOCUMENT_ISOLATION" in captured
        assert "[PASSED] CHECK_REAL_FILE_INGESTION_STATUS" in captured
        assert "[PASSED] CHECK_REAL_FILE_PIPELINE_INVARIANTS" in captured

    def test_smoke_test_simulated_contamination_fails_exit_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """La contaminazione simulata DEVE far fallire il test con Exit Code 1 e stampare FALLITO (mai SUCCESSO)."""
        exit_code = run_smoke_test(use_fake=True, simulate_contamination=True)
        captured = capsys.readouterr().out

        assert exit_code == 1
        assert "ESITO SMOKE TEST OPERATIVO: FALLITO" in captured
        assert "ESITO SMOKE TEST OPERATIVO: SUCCESSO" not in captured
        assert "multi_conv_cross_isolation     : FAILED" in captured
        assert "[FAILED] CHECK_CROSS_DOCUMENT_ISOLATION" in captured
        assert "Uno o piu controlli obbligatori non sono stati superati." in captured

    def test_smoke_test_empty_topics_fails_exit_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Se Discovery non produce argomenti, lo smoke test deve terminare con Exit 1."""
        exit_code = run_smoke_test(use_fake=True, fake_mode="empty_topics")
        captured = capsys.readouterr().out

        assert exit_code == 1
        assert "ESITO SMOKE TEST OPERATIVO: FALLITO" in captured
        assert "zero argomenti" in captured.lower()

    def test_smoke_test_invalid_json_fails_exit_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Una risposta corrotta non-JSON deve essere trattata come errore operativo con Exit 1."""
        exit_code = run_smoke_test(use_fake=True, fake_mode="invalid_json")
        captured = capsys.readouterr().out

        assert exit_code == 1
        assert "ESITO SMOKE TEST OPERATIVO: FALLITO" in captured
        assert "ERRORE OPERATIVO" in captured

    def test_smoke_test_unreachable_lmstudio_returns_exit_2_not_verified(self, capsys: pytest.CaptureFixture[str]) -> None:
        """In modalità live senza LM Studio attivo, deve restituire Exit 2 e marcare NOT_VERIFIED."""
        exit_code = run_smoke_test(use_fake=False)
        captured = capsys.readouterr().out

        assert exit_code == 2
        assert "LIVE LM STUDIO: NOT EXECUTED" in captured
        assert "NOT_VERIFIED" in captured
        assert "ESITO SMOKE TEST OPERATIVO: SUCCESSO" not in captured

    def test_real_whatsapp_txt_ingestion_e2e_pipeline(self) -> None:
        """Verifica che la vera pipeline applicativa ingestion WhatsApp TXT operi end-to-end senza perdite."""
        txt_path, _ = ensure_synthetic_fixtures()
        raw_bytes = txt_path.read_bytes()

        res = ingest_file_payload(
            file_bytes_or_io=raw_bytes,
            original_filename="synthetic_whatsapp_chat.txt",
            source_format=SourceFormat.WHATSAPP_EXPORT,
        )

        assert res.summary.status == IngestionStatus.SUCCESS
        assert res.summary.raw_record_count == 4
        assert res.summary.unified_message_count == 4
        assert len(res.documents) == 1

        doc = next(iter(res.documents.values()))
        assert doc.source_name == "whatsapp_export"
        assert doc.document_id.startswith("doc::whatsapp_export::")
        assert len(doc.bundles) == 4
        assert len(doc.all_evidence_sections) == 4

        # Verifica preservazione dei testi originali
        texts = [b.message.text_content for b in doc.bundles]
        assert any("container" in t.lower() for t in texts)
        assert any("sigilli" in t.lower() for t in texts)
        assert any("inventariati" in t.lower() for t in texts)
        assert any("sdoganamento" in t.lower() for t in texts)

    def test_real_whatsapp_zip_ingestion_e2e_pipeline(self) -> None:
        """Verifica che la vera pipeline applicativa supporti anche l'archivio ZIP sintetico."""
        _, zip_path = ensure_synthetic_fixtures()
        raw_bytes = zip_path.read_bytes()

        res = ingest_file_payload(
            file_bytes_or_io=raw_bytes,
            original_filename="synthetic_whatsapp_chat.zip",
            source_format=SourceFormat.WHATSAPP_EXPORT,
        )

        assert res.summary.status == IngestionStatus.SUCCESS
        assert res.summary.raw_record_count == 4
        assert res.summary.unified_message_count == 4
        assert len(res.documents) == 1

    def test_cross_document_isolation_demo_vs_file(self) -> None:
        """Verifica rigorosa di isolamento: nessuna run registrata per un documento è visibile nell'altro."""
        service = LocalTopicAnalysisService()
        doc_demo, _, _ = build_synthetic_demo_dataset()
        txt_path, _ = ensure_synthetic_fixtures()

        ingest_res = ingest_file_payload(
            file_bytes_or_io=txt_path.read_bytes(),
            original_filename=txt_path.name,
            source_format=SourceFormat.WHATSAPP_EXPORT,
        )
        doc_file = next(iter(ingest_res.documents.values()))

        client = FakeSmokeClient()
        disc_demo = service.run_topic_discovery(document=doc_demo, client=client, max_topics=2)
        disc_file = service.run_topic_discovery(document=doc_file, client=client, max_topics=2)

        demo_run_id = disc_demo.metadata["run_id"]
        file_run_id = disc_file.metadata["run_id"]

        # Isolamento perfetto
        assert service.get_discovery_run(doc_demo.document_id, demo_run_id) is not None
        assert service.get_discovery_run(doc_file.document_id, file_run_id) is not None
        assert service.get_discovery_run(doc_demo.document_id, file_run_id) is None
        assert service.get_discovery_run(doc_file.document_id, demo_run_id) is None

    def test_topic_detection_rationale_field_and_metadata(self) -> None:
        """Verifica l'accesso diretto all'attributo .rationale di TopicDetectionResult e integrità metadata."""
        service = LocalTopicAnalysisService()
        doc_demo, _, _ = build_synthetic_demo_dataset()
        client = FakeSmokeClient()

        query = TopicQuery(
            topic_id="q1",
            label="Ispezione Depositi",
            description="Verifica magazzino e casse sigillate",
        )
        det_res = service.run_topic_detection(document=doc_demo, topic=query, client=client)

        # Il campo rationale deve essere accessibile come attributo diretto e valorizzato
        assert hasattr(det_res, "rationale")
        assert isinstance(det_res.rationale, str)
        assert len(det_res.rationale.strip()) > 0
        assert det_res.decision == TopicDecision.PRESENT
        assert len(det_res.evidence_ids) > 0

    def test_control_case_absent_decision_preserves_faithful_scientific_record(self) -> None:
        """Se l'LLM restituisce ABSENT per il caso di controllo, l'esito scientifico deve essere registrato fedelmente."""
        service = LocalTopicAnalysisService()
        doc_demo, _, _ = build_synthetic_demo_dataset()

        # Mock client che risponde ABSENT
        class AbsentClient(BaseLlmClient):
            def is_available(self) -> bool:
                return True

            def chat_completion(self, messages: list[dict[str, str]], **kwargs: Any) -> LlmCompletionResponse:
                payload = {
                    "decision": "ABSENT",
                    "evidence_ids": [],
                    "rationale": "Nessuna evidenza sufficiente riscontrata per comprovare il tema.",
                }
                return LlmCompletionResponse(
                    content=json.dumps(payload),
                    model="qwen2.5-7b-instruct",
                    prompt_tokens=20,
                    completion_tokens=20,
                    total_tokens=40,
                    latency_seconds=0.1,
                )

        absent_client = AbsentClient()
        ctrl_query = TopicQuery(
            topic_id="ctrl_logistica",
            label="Logistica e Ispezione Depositi",
            description="Ispezione visiva del magazzino merci con casse sigillate e sicurezza",
        )
        ctrl_det = service.run_topic_detection(document=doc_demo, topic=ctrl_query, client=absent_client)

        assert ctrl_det.decision == TopicDecision.ABSENT
        assert len(ctrl_det.evidence_ids) == 0
        assert "Nessuna evidenza" in ctrl_det.rationale
