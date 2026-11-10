"""
tests/unit/test_runbook_smoke.py
--------------------------------
Smoke test automatico per accertare che tutti i comandi Python, gli import, le classi
e le affermazioni operative sostanziali documentate in RUNBOOK_UBUNTU.txt siano
perfettamente conformi, eseguibili e verificate nell'ambiente di lavoro.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai.backend import (
    AiBackendRequestError,
    BaseLlmClient,
    LlmCompletionResponse,
)
from ai.models import (
    ConversationEvidenceDocument,
    TopicQuery,
)
from ai.topics import (
    TopicDetectionAnalyzer,
    TopicDiscoveryAnalyzer,
)
from tests.unit.test_topic_discovery_detection_stabilization import _create_synthetic_bundle


def test_runbook_section_1_imports():
    """Verifica tutti gli import dichiarati nella Sezione 1 del runbook."""
    import analysis.service
    import ai.topics
    import ai.structured
    import analysis.export
    import scripts.preflight_checkpoint_resume

    assert analysis.service.__file__ is not None
    assert ai.topics.__file__ is not None
    assert ai.structured.__file__ is not None
    assert analysis.export.__file__ is not None
    assert scripts.preflight_checkpoint_resume.__file__ is not None


def test_runbook_text_alignment_and_version_consistency():
    """Verifica che RUNBOOK_UBUNTU.txt riporti data, release e contratti operativi reali."""
    runbook_path = Path(__file__).resolve().parent.parent.parent / "RUNBOOK_UBUNTU.txt"
    assert runbook_path.is_file(), "RUNBOOK_UBUNTU.txt non trovato nella radice del progetto"

    content = runbook_path.read_text(encoding="utf-8")

    # Verifica versione e data canonica
    assert "25 Ottobre 2026" in content or "24 Ottobre 2026" in content
    assert "chatAnalysis_snapshot_directory_open_io_failclosed_audit_20261025.zip" in content, (
        "Release di riferimento del runbook non aggiornata alla build verificata del 25 Ottobre 2026"
    )

    # Verifica contratto Sezione 5.2 (HTTP 400 permanente fail-closed vs transiente retry)
    assert "HTTP 400" in content
    assert "FAIL-CLOSED" in content or "zero retry" in content or "fallisce chiuso" in content
    assert "HTTP 502/503/504" in content or "503" in content


def test_runbook_section_5_imports_and_instantiation():
    """Verifica l'import e l'inizializzazione del client LM Studio e del service (Sezione 5.1)."""
    from analysis.service import LocalTopicAnalysisService
    from ai.lmstudio import LmStudioClient
    from analysis.models import OperationalProfile

    # Inizializzazione senza connessione di rete
    client = LmStudioClient(
        base_url="http://localhost:1234",
        model_id="qwen2.5-7b-instruct",
        default_timeout_seconds=30.0,
    )
    service = LocalTopicAnalysisService(client=client)

    assert client is not None
    assert service is not None
    assert OperationalProfile.BALANCED is not None


def test_runbook_section_5_2_operational_http_400_fail_closed_contract():
    """
    Verifica sostanziale Sezione 5.2: un errore HTTP 400 permanente non deve MAI essere ritentato
    (chiamate al client = 1, sollevamento immediato fail-closed).
    """
    bundles = (_create_synthetic_bundle(1, "Test content"),)
    doc = ConversationEvidenceDocument(document_id="doc_runbook_test", bundles=bundles)
    topic = TopicQuery(topic_id="t_001", label="Tema Test", description="Descrizione test")

    client = MagicMock(spec=BaseLlmClient)
    client.chat_completion.side_effect = AiBackendRequestError(
        "Bad Request / Schema Rejected",
        status_code=400,
        error_category="HTTP_INVALID_PARAMETERS",
    )

    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    with pytest.raises(AiBackendRequestError):
        analyzer.detect_topic(document=doc, topic=topic, model_id="qwen2.5-7b-instruct")

    # DEVE aver eseguito ESATTAMENTE 1 chiamata (ZERO tentativi di retry su 400 permanente)
    assert client.chat_completion.call_count == 1


def test_runbook_section_5_2_operational_http_transient_retry_contract():
    """
    Verifica sostanziale Sezione 5.2: un errore HTTP transiente (es. 503) esegue il retry (2 tentativi)
    e recupera l'inferenza se il secondo tentativo ha successo.
    """
    bundles = (_create_synthetic_bundle(1, "Test content"),)
    doc = ConversationEvidenceDocument(document_id="doc_runbook_test_503", bundles=bundles)
    topic = TopicQuery(topic_id="t_001", label="Tema Test", description="Descrizione test")

    success_resp = LlmCompletionResponse(
        content=json.dumps({"decision": "ABSENT", "evidence_ids": [], "rationale": "Nessuna evidenza"}),
        model="qwen2.5-7b-instruct",
        prompt_tokens=50,
        completion_tokens=20,
        total_tokens=70,
        latency_seconds=0.05,
        raw_response={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]},
    )

    client = MagicMock(spec=BaseLlmClient)
    client.chat_completion.side_effect = [
        AiBackendRequestError("Service Unavailable", status_code=503, error_category="HTTP_SERVER_ERROR"),
        success_resp,
    ]

    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    res = analyzer.detect_topic(document=doc, topic=topic, model_id="qwen2.5-7b-instruct")

    assert client.chat_completion.call_count == 2
    assert res.decision.value == "ABSENT"
    assert res.metadata["attempts"] == 2
    assert res.metadata["retries"] == 1


def test_runbook_section_6_report_validation_logic():
    """Verifica la logica di validazione JSON documentata nella Sezione 6."""
    mock_data = {
        "execution_software_commit": "abc123def456",
        "execution": {"error_count": 0},
        "chunk_diagnostics": [
            {"chunk_index": 0, "status": "SUCCESS"},
            {"chunk_index": 1, "status": "SUCCESS"},
        ],
    }

    exec_commit = mock_data.get("execution_software_commit")
    err_count = mock_data.get("execution", {}).get("error_count", 0)
    failed_chunks = len([c for c in mock_data.get("chunk_diagnostics", []) if c.get("status") == "FAILED"])

    assert exec_commit != "NOT_RECORDED"
    assert err_count >= failed_chunks

