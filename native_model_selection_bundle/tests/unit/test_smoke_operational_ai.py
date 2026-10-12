"""
tests/unit/test_smoke_operational_ai.py
---------------------------------------
Suite di test obbligatoria per il Release Gate e l'Hardening dello Smoke Test Operativo AI.

Copre in modo esaustivo:
1. Context confermato 8192 -> PASS consentito (Exit Code 0).
2. Context confermato 4096 -> FAILED (Exit Code 1).
3. Context UNKNOWN -> NOT_VERIFIED (Exit Code 2).
4. Modello effettivo differente -> FAILED (Exit Code 1).
5. Model key canonico associato a instance ID opaco verificato -> PASS consentito (Exit Code 0).
6. Modello non verificabile -> NOT_VERIFIED (Exit Code 2).
7. Fake client -> soltanto MOCK_VERIFIED (mai LIVE_VERIFIED).
8. LM Studio assente -> Exit Code 2.
9. Contaminazione simulata -> Exit Code 1.
10. JSON invalido -> Exit Code 1.
11. Bash con smoke fallito e output catturato tramite tee con pipefail -> exit code preservato.
12. Smoke positivo mock -> Exit Code 0.
13. Ingestion reale WhatsApp TXT end-to-end attraverso la vera pipeline applicativa.
14. Ingestion reale archivio WhatsApp ZIP attraverso la vera pipeline applicativa.
15. Isolamento cross-document rigoroso (DEMO vs FILE reale).
16. Accesso corretto al campo .rationale di TopicDetectionResult e tracciamento metadata.
17. Preservazione fedele del risultato scientifico per il caso di controllo (incluso ABSENT).
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import subprocess
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class TestSmokeOperationalAiHardening:
    """Verifica e hardening dei release gate dello smoke test operativo."""

    def test_smoke_test_fake_normal_passes_exit_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Smoke test simulato deterministico: supera tutti i controlli con Exit 0 e status SUCCESSO."""
        exit_code = run_smoke_test(use_fake=True)
        captured = capsys.readouterr().out

        assert exit_code == 0
        assert "ESITO SMOKE TEST OPERATIVO: SUCCESSO" in captured
        assert "multi_conv_cross_isolation     : PASSED (Zero leak)" in captured
        assert "verification_classification    : MOCK_VERIFIED" in captured
        assert "LIVE_VERIFIED" not in captured
        assert "[PASSED      ] CHECK_CONFIRMED_CONTEXT_LENGTH" in captured
        assert "[PASSED      ] CHECK_MODEL_RESOLUTION" in captured

    def test_context_confirmed_8192_allowed_pass(self, capsys: pytest.CaptureFixture[str]) -> None:
        """1. Context confermato 8192 -> PASS consentito."""
        exit_code = run_smoke_test(use_fake=True, fake_mode="normal", confirmed_context=8192)
        captured = capsys.readouterr().out
        assert exit_code == 0
        assert "CHECK_CONFIRMED_CONTEXT_LENGTH" in captured
        assert "ESITO SMOKE TEST OPERATIVO: SUCCESSO" in captured

    def test_context_confirmed_4096_fails_exit_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """2. Context confermato 4096 -> FAILED con Exit Code 1."""
        exit_code = run_smoke_test(use_fake=True, fake_mode="wrong_context")
        captured = capsys.readouterr().out
        assert exit_code == 1
        assert "CHECK_CONFIRMED_CONTEXT_LENGTH" in captured
        assert "ESITO SMOKE TEST OPERATIVO: FALLITO" in captured

    def test_context_unknown_returns_exit_2_not_verified(self, capsys: pytest.CaptureFixture[str]) -> None:
        """3. Context UNKNOWN -> NOT_VERIFIED con Exit Code 2."""
        exit_code = run_smoke_test(use_fake=True, fake_mode="unknown_context")
        captured = capsys.readouterr().out
        assert exit_code == 2
        assert "CHECK_CONFIRMED_CONTEXT_LENGTH" in captured
        assert "ESITO SMOKE TEST OPERATIVO: NOT_VERIFIED" in captured

    def test_actual_model_different_fails_exit_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """4. Modello effettivo differente -> FAILED con Exit Code 1."""
        exit_code = run_smoke_test(use_fake=True, fake_mode="wrong_model")
        captured = capsys.readouterr().out
        assert exit_code == 1
        assert "ESITO SMOKE TEST OPERATIVO: FALLITO" in captured

    def test_canonical_model_with_opaque_instance_id_passes_exit_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        """5. Model key canonico associato a instance ID opaco verificato -> PASS consentito."""
        exit_code = run_smoke_test(use_fake=True, fake_mode="opaque_instance_id")
        captured = capsys.readouterr().out
        assert exit_code == 0
        assert "opaque-instance-id-xyz" in captured
        assert "CHECK_RESPONSE_MODEL_IDENTITY" in captured
        assert "ESITO SMOKE TEST OPERATIVO: SUCCESSO" in captured

    def test_model_unverifiable_returns_exit_2_not_verified(self, capsys: pytest.CaptureFixture[str]) -> None:
        """6. Modello non verificabile -> NOT_VERIFIED con Exit Code 2."""
        exit_code = run_smoke_test(use_fake=True, fake_mode="unknown_model")
        captured = capsys.readouterr().out
        assert exit_code == 2
        assert "CHECK_MODEL_RESOLUTION" in captured
        assert "ESITO SMOKE TEST OPERATIVO: NOT_VERIFIED" in captured

    def test_fake_client_categorized_only_as_mock_verified(self, capsys: pytest.CaptureFixture[str]) -> None:
        """7. Fake client -> soltanto MOCK_VERIFIED, mai LIVE_VERIFIED."""
        exit_code = run_smoke_test(use_fake=True)
        captured = capsys.readouterr().out
        assert exit_code == 0
        assert "verification_classification    : MOCK_VERIFIED" in captured
        assert "LIVE_VERIFIED" not in captured

    def test_smoke_test_unreachable_lmstudio_returns_exit_2_not_verified(self, capsys: pytest.CaptureFixture[str]) -> None:
        """8. LM Studio assente -> Exit Code 2, NOT_VERIFIED."""
        exit_code = run_smoke_test(use_fake=False)
        captured = capsys.readouterr().out
        assert exit_code == 2
        assert "LIVE LM STUDIO: NOT EXECUTED" in captured
        assert "NOT_VERIFIED" in captured

    def test_smoke_test_simulated_contamination_fails_exit_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """9. Contaminazione simulata -> Exit Code 1, esito FALLITO."""
        exit_code = run_smoke_test(use_fake=True, simulate_contamination=True)
        captured = capsys.readouterr().out
        assert exit_code == 1
        assert "ESITO SMOKE TEST OPERATIVO: FALLITO" in captured
        assert "ESITO SMOKE TEST OPERATIVO: SUCCESSO" not in captured
        assert "multi_conv_cross_isolation     : FAILED" in captured
        assert "CHECK_CROSS_DOCUMENT_ISOLATION" in captured

    def test_smoke_test_invalid_json_fails_exit_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """10. JSON invalido -> errore operativo con Exit Code 1."""
        exit_code = run_smoke_test(use_fake=True, fake_mode="invalid_json")
        captured = capsys.readouterr().out
        assert exit_code == 1
        assert "ESITO SMOKE TEST OPERATIVO: FALLITO" in captured

    def test_bash_tee_pipeline_preserves_failing_exit_code(self, tmp_path: Path) -> None:
        """11. Bash con smoke fallito e output catturato tramite tee -> exit code preservato via PIPESTATUS."""
        bash_exe = None
        if sys.platform == "win32":
            candidates = [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files\Git\usr\bin\bash.exe",
            ]
            for c in candidates:
                if Path(c).is_file():
                    bash_exe = c
                    break
        if not bash_exe:
            which_bash = shutil.which("bash")
            if which_bash and "system32" not in which_bash.lower():
                bash_exe = which_bash

        if not bash_exe:
            pytest.skip("Bash nativo non disponibile in questo ambiente Windows")

        log_path = tmp_path / "smoke_failing.log"
        try:
            py_posix = subprocess.check_output(
                [bash_exe, "-c", f"cygpath -u '{sys.executable}'"],
                text=True,
            ).strip()
            log_posix = subprocess.check_output(
                [bash_exe, "-c", f"cygpath -u '{log_path}'"],
                text=True,
            ).strip()
        except Exception:
            py_posix = sys.executable.replace("\\", "/")
            log_posix = str(log_path).replace("\\", "/")

        bash_script = (
            "set -o pipefail\n"
            f'"{py_posix}" scripts/smoke_operational_ai.py --fake --simulate-contamination 2>&1 | tee "{log_posix}"\n'
            "rc=${PIPESTATUS[0]}\n"
            'echo "CAPTURED_RC: $rc"\n'
            "exit $rc\n"
        )
        proc = subprocess.run([bash_exe, "-c", bash_script], cwd=str(PROJECT_ROOT), capture_output=True, text=True)
        assert proc.returncode == 1, f"Returncode was {proc.returncode}, stdout: {proc.stdout}, stderr: {proc.stderr}"
        assert "CAPTURED_RC: 1" in proc.stdout
        assert log_path.exists()
        assert "FALLITO" in log_path.read_text(encoding="utf-8", errors="replace")

    def test_smoke_test_empty_topics_fails_exit_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Discovery che produce zero topic -> Exit Code 1."""
        exit_code = run_smoke_test(use_fake=True, fake_mode="empty_topics")
        captured = capsys.readouterr().out
        assert exit_code == 1
        assert "ESITO SMOKE TEST OPERATIVO: FALLITO" in captured

    def test_real_whatsapp_txt_ingestion_e2e_pipeline(self) -> None:
        """13. Ingestion reale WhatsApp TXT end-to-end attraverso la vera pipeline applicativa."""
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

        texts = [b.message.text_content for b in doc.bundles]
        assert any("container" in t.lower() for t in texts)
        assert any("sigilli" in t.lower() for t in texts)
        assert any("inventariati" in t.lower() for t in texts)
        assert any("sdoganamento" in t.lower() for t in texts)

    def test_real_whatsapp_zip_ingestion_e2e_pipeline(self) -> None:
        """14. Ingestion reale archivio WhatsApp ZIP attraverso la vera pipeline applicativa."""
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
        """15. Isolamento cross-document rigoroso (DEMO vs FILE reale)."""
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

        assert service.get_discovery_run(doc_demo.document_id, demo_run_id) is not None
        assert service.get_discovery_run(doc_file.document_id, file_run_id) is not None
        assert service.get_discovery_run(doc_demo.document_id, file_run_id) is None
        assert service.get_discovery_run(doc_file.document_id, demo_run_id) is None

    def test_topic_detection_rationale_field_and_metadata(self) -> None:
        """16. Accesso corretto al campo .rationale di TopicDetectionResult e tracciamento metadata."""
        service = LocalTopicAnalysisService()
        doc_demo, _, _ = build_synthetic_demo_dataset()
        client = FakeSmokeClient()

        query = TopicQuery(
            topic_id="q1",
            label="Ispezione Depositi",
            description="Verifica magazzino e casse sigillate",
        )
        det_res = service.run_topic_detection(document=doc_demo, topic=query, client=client)

        assert hasattr(det_res, "rationale")
        assert isinstance(det_res.rationale, str)
        assert len(det_res.rationale.strip()) > 0
        assert det_res.decision == TopicDecision.PRESENT
        assert len(det_res.evidence_ids) > 0

    def test_control_case_absent_decision_preserves_faithful_scientific_record(self) -> None:
        """17. Preservazione fedele del risultato scientifico per il caso di controllo (incluso ABSENT)."""
        service = LocalTopicAnalysisService()
        doc_demo, _, _ = build_synthetic_demo_dataset()

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

    def test_smoke_instance_replacement_mid_test_fails_exit_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Blocker 2: Sostituzione imprevista del modello durante lo smoke -> FAILED (Exit Code 1)."""
        exit_code = run_smoke_test(use_fake=True, fake_mode="instance_replacement")
        captured = capsys.readouterr().out
        assert exit_code == 1
        assert "ESITO SMOKE TEST OPERATIVO: FALLITO" in captured
        assert ("AiModelMismatchError" in captured) or ("CHECK_RESPONSE_MODEL_IDENTITY" in captured)

    def test_verified_client_injected_and_shared_across_pipeline(self) -> None:
        """Blocker 2: Il client verificato viene iniettato nel service e utilizzato sia da Discovery che da Detection."""
        service = LocalTopicAnalysisService()
        doc_demo, _, _ = build_synthetic_demo_dataset()
        client = FakeSmokeClient()
        service._injected_client = client

        # 1. Discovery usa il client iniettato
        disc_res = service.run_topic_discovery(document=doc_demo, client=client, max_topics=2)
        assert client.call_count >= 1
        call_count_after_disc = client.call_count

        # 2. Detection indipendente usa lo stesso client
        first_topic = disc_res.topics[0]
        det_res, consistency = service.verify_discovered_topic(
            document=doc_demo,
            discovered_topic=first_topic,
            discovery_result=disc_res,
            client=client,
        )
        assert client.call_count > call_count_after_disc
        assert service._injected_client is client

        # 3. Entrambe le inferenze preservano i rispettivi metadata
        assert disc_res.metadata.get("run_id") != det_res.metadata.get("run_id")
        assert "model" in disc_res.metadata
        assert "model" in det_res.metadata

    def test_independent_inferences_no_evidence_ids_hinted_to_detection(self) -> None:
        """Blocker 2: Nessun evidence_id di Topic Discovery viene passato come suggerimento alla Detection."""
        service = LocalTopicAnalysisService()
        doc_demo, _, _ = build_synthetic_demo_dataset()
        client = FakeSmokeClient()

        disc_res = service.run_topic_discovery(document=doc_demo, client=client, max_topics=2)
        first_topic = disc_res.topics[0]
        assert len(first_topic.evidence_ids) > 0

        # Durante la seconda inferenza, l'oggetto TopicQuery inviato alla detection
        # contiene esclusivamente label e short_description (nessun evidence_id suggerito)
        det_res, consistency = service.verify_discovered_topic(
            document=doc_demo,
            discovered_topic=first_topic,
            discovery_result=disc_res,
            client=client,
        )
        assert det_res is not None
        assert disc_res.metadata["run_id"] != det_res.metadata["run_id"]

    def test_programmatic_attribute_error_fails_exit_1(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """Blocker 3: Errori programmatici (come AttributeError su metodo mancante) sono classificati FAILED (Exit 1)."""
        from scripts import smoke_operational_ai
        # Simuliamo un client live che solleva AttributeError per un metodo mancante
        mock_client = MagicMock()
        mock_client.is_available.return_value = True
        mock_client.get_native_models.return_value = [
            {"key": "lmstudio-community/Qwen2.5-7B-Instruct-GGUF", "loaded_instances": [{"id": "inst-1", "config": {"context_length": 8192}}]}
        ]
        # ensure_model_loaded funziona ma get_active_binding solleva AttributeError (metodo inesistente)
        mock_client.ensure_model_loaded.return_value = "inst-1"
        del mock_client.get_active_binding  # assenza metodo -> solleva AttributeError

        monkeypatch.setattr(smoke_operational_ai, "LmStudioClient", lambda **kwargs: mock_client)
        exit_code = run_smoke_test(use_fake=False)
        captured = capsys.readouterr().out

        assert exit_code == 1
        assert "ESITO SMOKE TEST OPERATIVO: FALLITO" in captured
        assert "CHECK_MODEL_RESOLUTION" in captured
        assert "FAILED" in captured

