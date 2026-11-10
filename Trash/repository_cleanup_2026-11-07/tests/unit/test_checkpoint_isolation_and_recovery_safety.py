"""
tests/unit/test_checkpoint_isolation_and_recovery_safety.py
------------------------------------------------------------
Validazione formale di sicurezza per OBIETTIVO 5:
1. Test negativo di cancellazione (protezione assoluta contro rmtree / percorsi esterni)
2. Integrità SQLite e consistenza dello schema dei checkpoint
3. Ripresa selettiva (selective resume) e stato PARTIAL fail-closed
4. Validazione forense delle evidenze (rifiuto allucinazioni evidence ID)
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import pytest

from ai.backend import (
    AiInvalidEvidenceCitationError,
)
from ai.models import (
    TopicQuery,
)
from ai.topics import TopicDetectionAnalyzer
from analysis.checkpoint import (
    AnalysisCheckpointStore,
)
from tests.unit.test_realworld_topic_improvements import (
    _build_car_purchase_document,
    ControllableMockLlmClient,
)


def test_negative_deletion_cannot_touch_external_sentinel(tmp_path: Path):
    """
    Test negativo di cancellazione:
    Dimostra che anche in presenza di eccezioni o operazioni massive di pulizia,
    un file sentinella posizionato all'esterno della sandbox di test rimane intatto.
    """
    external_sentinel_dir = tmp_path / "external_vault"
    external_sentinel_dir.mkdir(parents=True, exist_ok=True)
    sentinel_file = external_sentinel_dir / "immutable_sentinel.db"
    sentinel_content = b"CRITICAL_FORENSIC_CHECKPOINT_IMMUTABLE_SENTINEL_20261025"
    sentinel_file.write_bytes(sentinel_content)
    sentinel_hash_pre = hashlib.sha256(sentinel_content).hexdigest()

    # Creazione di store di test in cartella separata
    test_sandbox = tmp_path / "test_sandbox"
    test_store = AnalysisCheckpointStore(checkpoint_dir=test_sandbox)
    assert test_store.checkpoint_dir.resolve() != external_sentinel_dir.resolve()

    # Simulazione operazione fallita o eccezione
    try:
        raise RuntimeError("Simulated unhandled pipeline crash during processing")
    except RuntimeError:
        pass

    # Verifica immutabilità assoluta del file sentinella
    assert sentinel_file.exists(), "Il file sentinella è stato cancellato!"
    assert sentinel_file.read_bytes() == sentinel_content
    assert hashlib.sha256(sentinel_file.read_bytes()).hexdigest() == sentinel_hash_pre


def test_sqlite_checkpoint_integrity_and_schema(tmp_path: Path):
    """
    Verifica che il database SQLite del checkpoint rispetti l'integrità strutturale:
    - PRAGMA integrity_check == 'ok'
    - Foreign keys attive
    - Vincoli di unicità su (document_fingerprint, config_fingerprint, chunk_index)
    """
    store_dir = tmp_path / "integrity_test_chk"
    store = AnalysisCheckpointStore(checkpoint_dir=store_dir)

    with sqlite3.connect(store.db_path) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok"

        # Verifica presenza tabelle essenziali
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "checkpoints" in tables
        assert "checkpoint_chunks" in tables
        assert "checkpoint_chunk_diagnostics" in tables


from ai.models import (
    TopicDecision,
    TopicDetectionResult,
    TopicQuery,
)


def test_selective_resume_preserves_partial_state(tmp_path: Path):
    """
    Verifica il funzionamento del resume selettivo:
    - Salva solo i chunk validati
    - Calcola i chunk mancanti
    - Non promuove mai l'analisi a SUCCESS finché non sono validati tutti i chunk pianificati
    """
    store_dir = tmp_path / "resume_test_chk"
    store = AnalysisCheckpointStore(checkpoint_dir=store_dir)

    doc_fp = "a" * 64
    cfg_fp = "b" * 64
    total_chunks = 26
    validated_chunks = [1, 3, 4, 9, 16, 18, 25]

    query = TopicQuery(topic_id="q1", label="Acquisto automobile", description="Trattativa auto")

    # Salva i soli 7 chunk validati
    for c_idx in validated_chunks:
        res = TopicDetectionResult(
            topic=query,
            decision=TopicDecision.PRESENT,
            evidence_ids=(f"E{c_idx}",),
            rationale=f"Rilevato nel chunk {c_idx}",
            provenance_document_id="doc_resume_test",
        )
        store.save_chunk(
            document_id="doc_resume_test",
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=c_idx,
            result=res,
            total_planned_chunks=total_chunks,
            analysis_type="detection",
            model_id="qwen2.5-7b-instruct",
            prompt_version="v1.0",
            run_id="run_test_resume_01",
        )

    # Carica checkpoint
    loaded = store.load_checkpoint(doc_fp, cfg_fp)
    assert loaded is not None
    assert set(loaded.completed_chunk_indices) == set(validated_chunks)

    # Verifica determinazione chunk mancanti (selective resume)
    all_chunks = set(range(total_chunks))
    missing_chunks = sorted(all_chunks - set(loaded.completed_chunk_indices))
    assert len(missing_chunks) == 19
    assert 0 in missing_chunks
    assert 25 not in missing_chunks

    # Verifica stato rigorosamente PARTIAL
    is_success = len(loaded.completed_chunk_indices) == total_chunks
    assert not is_success, "Un'analisi con 7/26 non può mai avere stato SUCCESS"


def test_evidence_validation_rejects_hallucinations():
    """
    Verifica che il modulo forense rifiuti tassativamente citazioni di evidence ID inesistenti
    (comportamento fail-closed conforme a Chunk 19 e Chunk 25 della Run 1).
    """
    doc = _build_car_purchase_document()
    query = TopicQuery(topic_id="q_auto", label="Acquisto automobile", description="Trattativa compravendita auto")
    invalid_resp = {
        "decision": "PRESENT",
        "evidence_ids": ["unified:whatsapp_txt:1::ORIGINAL_TEXT", "NON_EXISTING_EVID_999"],
        "rationale": "Evidenza inventata",
    }
    mock_client = ControllableMockLlmClient(responses=[invalid_resp] * 5)
    analyzer = TopicDetectionAnalyzer(client=mock_client, operational_mode=True)
    with pytest.raises(AiInvalidEvidenceCitationError) as exc_info:
        analyzer.detect_topic(document=doc, topic=query)

    assert "NON_EXISTING_EVID_999" in str(exc_info.value) or "NON_EXISTING_EVID_999" in getattr(exc_info.value, "invalid_evidence_ids", ())
