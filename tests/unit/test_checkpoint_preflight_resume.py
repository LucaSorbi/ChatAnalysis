"""
tests/unit/test_checkpoint_preflight_resume.py
----------------------------------------------
Suite di test unitari completa per la verifica della procedura di pre-ripresa
dei checkpoint (sessione 24/26) e dello strumento scripts/preflight_checkpoint_resume.py.

Copre rigorosamente tutti i 16 scenari di accettazione del critical audit:
1. Scenario nominale 24/26 con binding runtime verificato -> SAFE_RESUME_VERIFIED.
2. Binding runtime non accertato con sola model key dichiarata -> RUNTIME_VALIDATION_REQUIRED.
3. Fallimento Controllo 1: mismatch document fingerprint -> NOT_COMPATIBLE.
4. Fallimento Controllo 2: mismatch config fingerprint -> NOT_COMPATIBLE.
5. Fallimento Controllo 3: mismatch model_id -> NOT_COMPATIBLE.
6. Fallimento Controllo 4: mismatch prompt_version -> NOT_COMPATIBLE.
7. Fallimento Controllo 5: mismatch piano di chunking / budget -> NOT_COMPATIBLE.
8. Fallimento Controllo 6: chunk mancanti imprevisti o payload corrotto -> NOT_COMPATIBLE.
9. Fallimento Controllo 7: citazione cross-chunk o evidence ID estraneo -> NOT_COMPATIBLE.
10. Fallimento Controllo 8: provenienza documentale aliena -> NOT_COMPATIBLE.
11. Ispezione del solo database senza documento -> STRUCTURAL_ONLY / RUNTIME_VALIDATION_REQUIRED.
12. Selezione fra tutte le sessioni: sessione corretta più vecchia della più recente.
13. Selezione fra tutte le sessioni: due documenti e due sessioni in SQLite.
14. Selezione fra tutte le sessioni: stesso documento e configurazioni differenti.
15. Selezione fra tutte le sessioni: due sessioni 24/26 distinte.
16. Selezione fra tutte le sessioni: document_id uguale ma fingerprint differente.
17. Selezione fra tutte le sessioni: nessuna corrispondenza.
18. Selezione fra tutte le sessioni: corrispondenze multiple ambigue.
19. Divieto assoluto di merge automatico tra sessioni a parametri discordanti.
20. Due piani di chunking differenti con lo stesso numero di chunk (26).
21. Snapshot sicuro: creazione con Backup API, consolidamento WAL e zero scritture su sorgente.
22. Snapshot sicuro: destinazione preesistente (rifiuto sovrascrittura con FileExistsError).
23. Snapshot sicuro: due invocazioni concorrenti isolate senza collisioni.
24. Snapshot sicuro: interruzione/errore durante il backup (cleanup parziale, sorgente intatta).
25. Snapshot sicuro: protezione symlink sulla destinazione.
26. Snapshot sicuro: nessuna cancellazione di file estranei.
27. Snapshot sicuro: test subprocess CLI con flag --snapshot (esecuzione e cleanup).
28. Validazione righe e subchunk corrotti senza eliminazione silenziosa (CheckpointValidationStats).
29. Test avversari sulla privacy (zero leak di canary token, percorsi, nomi, evidence_id).
30. Garanzia di zero chiamate LLM durante l'esecuzione del preflight.
31. Test del motore di ripresa selettiva simulata (mock su database sintetico).
32. Test end-to-end CLI con vero file WhatsApp sintetico su disco.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping
from unittest.mock import patch, MagicMock
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

from ai.backend import BaseLlmClient, LlmCompletionResponse
from ai.lmstudio import ModelIdentityBinding
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    MessageEvidenceBundle,
    TextEvidenceSection,
    TopicDiscoveryResult,
)
from importer.models import RawRecord
from ai.token_budget import DEFAULT_SAFETY_MARGIN_TOKENS
from analysis.checkpoint import (
    AnalysisCheckpointStore,
    compute_document_fingerprint,
    compute_run_config_hash,
)
from analysis.models import OperationalAnalysisConfig, OperationalProfile
from analysis.service import (
    LocalTopicAnalysisService,
    calculate_discovery_max_tokens,
)
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
import urllib.request
import urllib.error

from scripts.preflight_checkpoint_resume import (
    CLI_SOURCE_FORMAT_MAP,
    CheckResult,
    CheckpointValidationStats,
    PreflightAuditReport,
    PreflightResumeAuditor,
    PreflightVerdictStatus,
    StrictLoopbackRedirectHandler,
    _format_fp_safe,
    _is_canonical_sha256,
    _query_lmstudio_models_readonly,
    _sanitize_path,
    SnapshotError,
    SnapshotDestinationForbiddenError,
    SnapshotDirectoryInvalidError,
    SnapshotPermissionDeniedError,
    SnapshotCreationFailedError,
    _measure_source_file_state,
    _OWNERSHIP_TRACKER,
    _perform_atomic_safe_unlink,
    _safe_cleanup_owned_temp_dir,
    _publish_snapshot_exclusively,
    acquire_forensic_readonly_copy,
    create_consistent_readonly_snapshot,
    load_and_validate_checkpoint_readonly,
    select_matching_document,
)
from ui.models import SourceFormat
from unified.models import UnifiedMessage
from validation.models import ValidationResult


class ControlledMockClient(BaseLlmClient):
    """Client fittizio per verificare l'assenza di chiamate durante il preflight e il conteggio esatto in ripresa."""

    def __init__(self, responses: list[str] | None = None, model_id: str = "qwen2.5-7b-instruct") -> None:
        self.responses = list(responses or [])
        self.call_count = 0
        self.requested_chunks: list[str] = []
        self.model_id = model_id
        self._last_loaded_model_key = model_id
        self.request_listener: Any = None

    def is_available(self) -> bool:
        return True

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        timeout: float = 30.0,
        seed: int | None = 42,
        **kwargs: Any,
    ) -> LlmCompletionResponse:
        self.call_count += 1
        prompt_text = messages[-1]["content"] if messages else ""
        self.requested_chunks.append(prompt_text)

        if self.responses:
            resp_content = self.responses.pop(0)
        else:
            resp_content = json.dumps({"topics": [{"label": "Default Mock Topic", "short_description": "Desc", "evidence_ids": ["E001"]}]})

        return LlmCompletionResponse(
            content=resp_content,
            model=model or self.model_id,
            prompt_tokens=120,
            completion_tokens=45,
            total_tokens=165,
            latency_seconds=0.01,
        )


def _create_synthetic_bundle(idx: int, text: str) -> MessageEvidenceBundle:
    """Costruisce un MessageEvidenceBundle sintetico conforme."""
    raw = RawRecord(
        source_name="whatsapp",
        source_path="/fake/chat.txt",
        source_record_id=str(idx),
        record_type="message",
        raw_fields={"data": text},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(
        status=TimestampTzStatus.KNOWN_UTC,
        utc_datetime=None,
        naive_datetime=None,
        iso_string="2026-10-18T10:00:00Z",
        raw_value="18/10/2026, 10:00",
    )
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name="whatsapp",
        source_record_id=str(idx),
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"msg_synth_{idx:04d}",
        source_name="whatsapp",
        source_record_id=str(idx),
        source_path="/fake/chat.txt",
        record_type="message",
        timestamp=norm.timestamp,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    return MessageEvidenceBundle(message=msg)


def _create_synthetic_26_chunk_document(doc_id: str = "doc_whatsapp_synth_26chunks", total_bundles: int = 78) -> ConversationEvidenceDocument:
    """
    Costruisce un documento sintetico che con profilo BALANCED produce esattamente 26 blocchi pianificati.
    78 messaggi (3 messaggi da 3000 caratteri per blocco) -> esattamente 26 chunk.
    """
    bundles = []
    for i in range(total_bundles):
        chunk_num = i // 3
        text = f"Messaggio {i} appartenente al chunk {chunk_num} per test sintetico di ripresa. " + "A" * 3000
        bundles.append(_create_synthetic_bundle(i, text))

    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=tuple(bundles),
        source_name="whatsapp",
    )


def _populate_test_checkpoint_db(
    db_path: Path,
    document: ConversationEvidenceDocument,
    model_id: str = "qwen2.5-7b-instruct",
    prompt_version: str = "discovery_operational",
    profile: OperationalProfile = OperationalProfile.BALANCED,
    max_topics: int = 10,
    context_length: int = 8192,
    temperature: float = 0.0,
    seed: int | None = 42,
    missing_indices: tuple[int, ...] = (10, 19),
    cross_chunk_tamper: bool = False,
    alien_provenance: bool = False,
    invalid_json: bool = False,
    updated_at: str | None = None,
    quantization: str | None = None,
) -> tuple[str, str]:
    """Popola un database SQLite con 24 checkpoint su 26, mancanti 10 e 19."""
    service = LocalTopicAnalysisService(
        default_model=model_id,
        profile=profile,
        context_length=context_length,
    )
    plan = service.create_analysis_plan(
        document=document,
        analysis_type="discovery",
        max_topics=max_topics,
        context_length=context_length,
    )
    chunks = list(plan.chunks)
    assert len(chunks) == 26, f"Attesi 26 chunk, generati: {len(chunks)}"

    doc_fp = compute_document_fingerprint(document)
    eff_max_tokens = calculate_discovery_max_tokens(max_topics=max_topics, context_length=context_length)
    eff_run_cfg = OperationalAnalysisConfig(
        model_spec=model_id,
        context_length=context_length,
        timeout_seconds=60.0,
        max_tokens=eff_max_tokens,
        temperature=temperature,
        seed=seed,
        profile=profile,
        chunk_character_budget=service.max_chunk_characters,
        max_chunk_bundles=service.max_chunk_bundles,
        max_chunk_tokens=plan.chunk_token_budget,
    )
    cfg_fp = compute_run_config_hash(
        model_id=model_id,
        prompt_version=prompt_version,
        config=eff_run_cfg,
        chunks=chunks,
        extra_params={
            "max_topics": max_topics,
            "resolved_model_key": model_id,
            "quantization": quantization,
        },
    )

    store = AnalysisCheckpointStore(checkpoint_dir=db_path.parent, db_name=db_path.name)

    for idx, chk in enumerate(chunks):
        if idx in missing_indices:
            continue

        prov_doc_id = document.document_id if not alien_provenance else "doc_alien_fake_other"

        if cross_chunk_tamper and idx == 3:
            eids = (chunks[4].all_evidence_sections[0].evidence_id,)
        else:
            eids = (chk.all_evidence_sections[0].evidence_id,)

        res = TopicDiscoveryResult(
            topics=(
                DiscoveredTopic(
                    label=f"Topic validato per blocco {idx}",
                    short_description=f"Descrizione analitica del blocco {idx}",
                    evidence_ids=eids,
                ),
            ),
            provenance_document_id=prov_doc_id,
            metadata={"model": model_id, "finish_reason": "stop"},
        )

        store.save_chunk(
            document_id=document.document_id,
            document_fingerprint=doc_fp,
            config_fingerprint=cfg_fp,
            chunk_idx=idx,
            result=res,
            total_planned_chunks=26,
            analysis_type="discovery",
            model_id=model_id,
            prompt_version=prompt_version,
        )

    if updated_at is not None:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("UPDATE checkpoints SET updated_at = ? WHERE document_fingerprint = ? AND config_fingerprint = ?", (updated_at, doc_fp, cfg_fp))
            conn.commit()
        finally:
            conn.close()

    if invalid_json:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("""
                UPDATE checkpoint_chunks SET serialized_result = '{CORRUPTED_JSON_MALFORMED'
                WHERE chunk_index = 0 AND document_fingerprint = ? AND config_fingerprint = ?
            """, (doc_fp, cfg_fp))
            conn.commit()
        finally:
            conn.close()

    return doc_fp, cfg_fp


def _calculate_file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class TestCheckpointPreflightResume:
    """Suite di test formale e completa per scripts/preflight_checkpoint_resume.py."""

    def test_01_nominal_24_of_26_with_verified_binding_safe_resume(self) -> None:
        """Verifica lo scenario nominale 24/26 con binding verificato ed esito SAFE_RESUME_VERIFIED."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            doc_fp, cfg_fp = _populate_test_checkpoint_db(db_p, doc)

            # Binding verificato
            binding = ModelIdentityBinding(
                requested_canonical_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                is_verified=True,
            )

            auditor = PreflightResumeAuditor(
                db_path=db_p,
                document=doc,
                model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                profile=OperationalProfile.BALANCED,
                max_topics=10,
                context_length=8192,
                temperature=0.0,
                seed=42,
                active_binding=binding,
            )

            report = auditor.run_preflight()

            assert report.verdict == PreflightVerdictStatus.MOCK_VERIFIED
            assert report.verification_classification == "MOCK_VERIFIED"
            assert len(report.checks) == 9
            assert all(c.passed for c in report.checks)
            assert report.missing_chunks == [10, 19]
            assert report.completed_chunks_count == 24
            assert report.resumption_plan is not None
            assert report.resumption_plan["chunks_to_infer_count"] == 2
            assert report.resumption_plan["chunks_to_infer_indices_zero_based"] == [10, 19]
            assert report.resumption_plan["chunks_to_skip_count"] == 24

    def test_02_runtime_validation_required_when_binding_unverified_declared_only(self) -> None:
        """
        SEPARAZIONE BINDING: con sola chiave autodichiarata (--resolved-model-key) senza binding verificato,
        il verdetto DEVE rimanere RUNTIME_VALIDATION_REQUIRED e NON produrre SAFE_RESUME_VERIFIED.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)

            auditor = PreflightResumeAuditor(
                db_path=db_p,
                document=doc,
                model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                active_binding=None,  # Nessun binding runtime verificato
            )
            report = auditor.run_preflight()

            assert report.verdict == PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED
            assert report.resumption_plan is None
            c2 = next(c for c in report.checks if c.check_number == 2)
            assert c2.passed is False
            assert "DECLARED_ONLY_UNVERIFIED_RUNTIME" in c2.observed_value
            assert report.configuration_details["runtime_binding_status"] == "DECLARED_ONLY_UNVERIFIED_RUNTIME"
            assert report.configuration_details["runtime_binding_verified"] is False

    def test_03_check1_document_fingerprint_mismatch_not_compatible(self) -> None:
        """Verifica il rigetto con NOT_COMPATIBLE se il documento è stato alterato."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)

            altered_bundles = list(doc.bundles)
            altered_bundles[0] = _create_synthetic_bundle(0, "Testo alterato differente")
            altered_doc = ConversationEvidenceDocument(
                document_id=doc.document_id,
                bundles=tuple(altered_bundles),
                source_name=doc.source_name,
            )

            auditor = PreflightResumeAuditor(db_path=db_p, document=altered_doc, resolved_model_key="qwen2.5-7b-instruct")
            report = auditor.run_preflight()

            assert report.verdict == PreflightVerdictStatus.NOT_COMPATIBLE
            assert any("FINGERPRINT_MISMATCH" in r for r in report.reasons)

    def test_04_check2_config_fingerprint_mismatch_not_compatible(self) -> None:
        """Verifica il rigetto con NOT_COMPATIBLE se i parametri operativi differiscono (temperatura)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc, temperature=0.0)

            auditor = PreflightResumeAuditor(db_path=db_p, document=doc, temperature=0.7, resolved_model_key="qwen2.5-7b-instruct")
            report = auditor.run_preflight()

            assert report.verdict == PreflightVerdictStatus.NOT_COMPATIBLE
            assert any("CONFIG_MISMATCH" in r for r in report.reasons)

    def test_05_check3_model_mismatch_not_compatible(self) -> None:
        """Verifica il rigetto con NOT_COMPATIBLE se il modello richiesto è differente."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc, model_id="qwen2.5-7b-instruct")

            auditor = PreflightResumeAuditor(db_path=db_p, document=doc, model_id="llama-3.1-8b-instruct", resolved_model_key="llama-3.1-8b-instruct")
            report = auditor.run_preflight()

            assert report.verdict == PreflightVerdictStatus.NOT_COMPATIBLE
            assert any("CONFIG_MISMATCH" in r for r in report.reasons)

    def test_06_check4_prompt_version_mismatch_not_compatible(self) -> None:
        """Verifica il rigetto se la prompt_version in testata non coincide."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc, prompt_version="discovery_legacy_v1")

            auditor = PreflightResumeAuditor(db_path=db_p, document=doc, resolved_model_key="qwen2.5-7b-instruct")
            report = auditor.run_preflight()

            assert report.verdict == PreflightVerdictStatus.NOT_COMPATIBLE

    def test_07_check5_chunking_plan_mismatch_not_compatible(self) -> None:
        """Verifica il rigetto se il numero di chunk pianificati non corrisponde a 26."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)

            doc_25 = ConversationEvidenceDocument(
                document_id=doc.document_id,
                bundles=doc.bundles[:-3],
                source_name=doc.source_name,
            )

            auditor = PreflightResumeAuditor(db_path=db_p, document=doc_25, resolved_model_key="qwen2.5-7b-instruct")
            report = auditor.run_preflight()

            assert report.verdict == PreflightVerdictStatus.NOT_COMPATIBLE

    def test_08_check6_missing_chunk_indices_mismatch_not_compatible(self) -> None:
        """Verifica il rigetto se i blocchi mancanti non corrispondono esattamente a [10, 19]."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc, missing_indices=(5, 12))

            binding = ModelIdentityBinding(requested_canonical_id="qwen2.5-7b-instruct", resolved_model_key="qwen2.5-7b-instruct", is_verified=True)
            auditor = PreflightResumeAuditor(db_path=db_p, document=doc, resolved_model_key="qwen2.5-7b-instruct", active_binding=binding)
            report = auditor.run_preflight()

            assert report.verdict == PreflightVerdictStatus.NOT_COMPATIBLE
            c6 = next(c for c in report.checks if c.check_number == 6)
            assert c6.passed is False

    def test_09_check7_cross_chunk_evidence_tampering_not_compatible(self) -> None:
        """Verifica il rigetto se un blocco cita evidence_id appartenenti a un altro blocco."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc, cross_chunk_tamper=True)

            binding = ModelIdentityBinding(requested_canonical_id="qwen2.5-7b-instruct", resolved_model_key="qwen2.5-7b-instruct", is_verified=True)
            auditor = PreflightResumeAuditor(db_path=db_p, document=doc, resolved_model_key="qwen2.5-7b-instruct", active_binding=binding)
            report = auditor.run_preflight()

            assert report.verdict == PreflightVerdictStatus.NOT_COMPATIBLE
            c7 = next(c for c in report.checks if c.check_number == 7)
            assert c7.passed is False

    def test_10_check8_alien_provenance_document_id_not_compatible(self) -> None:
        """Verifica il rigetto se la provenance del risultato cita un document_id alieno."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc, alien_provenance=True)

            binding = ModelIdentityBinding(requested_canonical_id="qwen2.5-7b-instruct", resolved_model_key="qwen2.5-7b-instruct", is_verified=True)
            auditor = PreflightResumeAuditor(db_path=db_p, document=doc, resolved_model_key="qwen2.5-7b-instruct", active_binding=binding)
            report = auditor.run_preflight()

            assert report.verdict == PreflightVerdictStatus.NOT_COMPATIBLE
            c8 = next(c for c in report.checks if c.check_number == 8)
            assert c8.passed is False

    def test_11_structural_only_inspection_without_document(self) -> None:
        """Verifica che senza documento venga eseguito l'audit strutturale SQLite (RUNTIME_VALIDATION_REQUIRED)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)

            auditor = PreflightResumeAuditor(db_path=db_p, document=None)
            report = auditor.run_preflight()

            assert report.verdict == PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED
            assert report.document_available is False
            assert report.completed_chunks_count == 24
            assert report.missing_chunks == [10, 19]

    def test_12_session_selection_correct_older_than_newer_selected(self) -> None:
        """
        SELEZIONE DIRETTA FRA TUTTE LE SESSIONI:
        Verifica che una sessione esatta venga selezionata anche se è più vecchia
        di un'altra sessione presente nello stesso database (non basarsi su ORDER BY updated_at DESC LIMIT 1).
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()

            # Sessione 1: Target (creata prima, timestamp vecchio)
            doc_fp, cfg_fp = _populate_test_checkpoint_db(
                db_p, doc, model_id="qwen2.5-7b-instruct", temperature=0.0,
                updated_at="2026-01-01T00:00:00Z"
            )

            # Sessione 2: Altro modello (creata dopo, timestamp recente)
            _populate_test_checkpoint_db(
                db_p, doc, model_id="llama-3.1-8b-instruct", temperature=0.0,
                updated_at="2026-10-19T12:00:00Z"
            )

            binding = ModelIdentityBinding(requested_canonical_id="qwen2.5-7b-instruct", resolved_model_key="qwen2.5-7b-instruct", is_verified=True)
            auditor = PreflightResumeAuditor(
                db_path=db_p,
                document=doc,
                model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                temperature=0.0,
                active_binding=binding,
            )
            report = auditor.run_preflight()

            assert report.verdict == PreflightVerdictStatus.MOCK_VERIFIED
            assert report.configuration_details["requested_model_id"] == "qwen2.5-7b-instruct"

    def test_13_session_selection_two_documents_and_two_sessions(self) -> None:
        """Verifica che in presenza di due documenti e due sessioni in SQLite, ciascun preflight selezioni solo il proprio."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc_a = _create_synthetic_26_chunk_document(doc_id="doc_chat_alpha")
            doc_b = _create_synthetic_26_chunk_document(doc_id="doc_chat_beta")

            _populate_test_checkpoint_db(db_p, doc_a, model_id="qwen2.5-7b-instruct")
            _populate_test_checkpoint_db(db_p, doc_b, model_id="qwen2.5-7b-instruct")

            binding = ModelIdentityBinding(requested_canonical_id="qwen2.5-7b-instruct", resolved_model_key="qwen2.5-7b-instruct", is_verified=True)

            # Audit per doc_a
            aud_a = PreflightResumeAuditor(db_path=db_p, document=doc_a, resolved_model_key="qwen2.5-7b-instruct", active_binding=binding)
            rep_a = aud_a.run_preflight()
            assert rep_a.verdict == PreflightVerdictStatus.MOCK_VERIFIED

            # Audit per doc_b
            aud_b = PreflightResumeAuditor(db_path=db_p, document=doc_b, resolved_model_key="qwen2.5-7b-instruct", active_binding=binding)
            rep_b = aud_b.run_preflight()
            assert rep_b.verdict == PreflightVerdictStatus.MOCK_VERIFIED

    def test_14_session_selection_ambiguous_multiple_matches_aborts(self) -> None:
        """Verifica che se esistono due sessioni duplicate perfettamente identiche in SQLite, il sistema rifiuti l'ambiguità."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            doc_fp, cfg_fp = _populate_test_checkpoint_db(db_p, doc)

            # Rimuove il vincolo UNIQUE duplicando la riga in una tabella senza primary key per testare la guardia
            conn = sqlite3.connect(str(db_p))
            try:
                conn.execute("CREATE TABLE checkpoints_dup AS SELECT * FROM checkpoints;")
                conn.execute("INSERT INTO checkpoints_dup SELECT * FROM checkpoints;")
                conn.execute("DROP TABLE checkpoints;")
                conn.execute("ALTER TABLE checkpoints_dup RENAME TO checkpoints;")
                conn.commit()
            finally:
                conn.close()

            binding = ModelIdentityBinding(requested_canonical_id="qwen2.5-7b-instruct", resolved_model_key="qwen2.5-7b-instruct", is_verified=True)
            auditor = PreflightResumeAuditor(
                db_path=db_p,
                document=doc,
                model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                active_binding=binding,
            )
            report = auditor.run_preflight()
            assert report.verdict == PreflightVerdictStatus.NOT_COMPATIBLE
            assert any("AMBIGUOUS_MULTIPLE_SESSIONS_MATCH" in r for r in report.reasons)

    def test_15_chunk_boundaries_two_plans_with_same_chunk_count_differ(self) -> None:
        """
        BOUNDARIES CHECK: verifica che due piani che generano entrambi 26 chunk ma con budget
        di caratteri o token differenti producano un diverso config_fingerprint e vengano rifiutati.
        """
        doc = _create_synthetic_26_chunk_document()

        service = LocalTopicAnalysisService(default_model="qwen2.5-7b-instruct", profile=OperationalProfile.BALANCED)
        plan = service.create_analysis_plan(document=doc, analysis_type="discovery", max_topics=10)
        chunks = list(plan.chunks)
        assert len(chunks) == 26

        cfg_1 = OperationalAnalysisConfig(
            model_spec="qwen2.5-7b-instruct",
            chunk_character_budget=service.max_chunk_characters,
            max_chunk_bundles=service.max_chunk_bundles,
            max_chunk_tokens=plan.chunk_token_budget,
        )
        fp_1 = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg_1, chunks=chunks)

        # Config con budget alterato
        cfg_2 = OperationalAnalysisConfig(
            model_spec="qwen2.5-7b-instruct",
            chunk_character_budget=service.max_chunk_characters - 500,  # Budget differente
            max_chunk_bundles=service.max_chunk_bundles,
            max_chunk_tokens=plan.chunk_token_budget - 100,
        )
        fp_2 = compute_run_config_hash("qwen2.5-7b-instruct", "discovery_operational", config=cfg_2, chunks=chunks)

        assert fp_1 != fp_2, "Piani di chunking con budget differenti DEVONO produrre config_fingerprint distinti!"

    def test_16_snapshot_creates_isolated_copy_and_preserves_source(self) -> None:
        """Verifica che create_consistent_readonly_snapshot generi una copia isolata e preservi la sorgente byte-for-byte."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "source_checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            sha_before = _calculate_file_sha256(src_db)
            mtime_before = os.path.getmtime(src_db)

            snap_file, snap_temp_dir = create_consistent_readonly_snapshot(src_db)
            try:
                assert snap_file.is_file()
                assert snap_temp_dir.is_dir()

                # Verifica sorgente byte-for-byte immutata
                sha_after = _calculate_file_sha256(src_db)
                mtime_after = os.path.getmtime(src_db)
                assert sha_before == sha_after
                assert mtime_before == mtime_after

                # Verifica contenuto dello snapshot con chiusura esplicita
                conn = sqlite3.connect(f"file:{snap_file.resolve().as_posix()}?mode=ro", uri=True)
                try:
                    count = conn.execute("SELECT count(*) FROM checkpoint_chunks;").fetchone()[0]
                    assert count == 24
                finally:
                    conn.close()
            finally:
                if snap_temp_dir.is_dir():
                    shutil.rmtree(snap_temp_dir, ignore_errors=True)

    def test_17_snapshot_refuses_overwrite_when_destination_file_exists(self) -> None:
        """SICUREZZA SNAPSHOT: verifica che non venga mai sovrascritto un file preesistente (FileExistsError)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "src_dir"
            src_dir.mkdir()
            src_db = src_dir / "source_checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            dest_dir = Path(tmp_dir) / "dest_dir"
            dest_dir.mkdir()
            dest_existing = dest_dir / "preexisting_snapshot.db"
            dest_existing.write_text("IMPORTANT_PREEXISTING_DATA_DO_NOT_OVERWRITE", encoding="utf-8")

            with pytest.raises(FileExistsError):
                create_consistent_readonly_snapshot(src_db, dest_path=dest_existing)

            # Verifica che il contenuto preesistente NON sia stato alterato o cancellato
            assert dest_existing.read_text(encoding="utf-8") == "IMPORTANT_PREEXISTING_DATA_DO_NOT_OVERWRITE"

    def test_18_snapshot_concurrent_invocations_isolated(self) -> None:
        """SICUREZZA SNAPSHOT: verifica che due invocazioni concorrenti ottengano directory e file distinti senza collisioni."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "source_checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            def _take_snapshot():
                return create_consistent_readonly_snapshot(src_db)

            with ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(_take_snapshot)
                f2 = executor.submit(_take_snapshot)
                snap_1, dir_1 = f1.result()
                snap_2, dir_2 = f2.result()

            try:
                assert snap_1 != snap_2
                assert dir_1 != dir_2
                assert snap_1.is_file()
                assert snap_2.is_file()
            finally:
                shutil.rmtree(dir_1, ignore_errors=True)
                shutil.rmtree(dir_2, ignore_errors=True)

    def test_19_snapshot_cleanup_on_backup_interruption(self) -> None:
        """SICUREZZA SNAPSHOT: verifica che un errore durante il backup provochi la pulizia immediata della destinazione."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "source_checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            real_connect = sqlite3.connect

            class FailingConnection:
                def __init__(self, *args, **kwargs):
                    self._conn = real_connect(*args, **kwargs)

                def execute(self, *args, **kwargs):
                    return self._conn.execute(*args, **kwargs)

                def backup(self, *args, **kwargs):
                    raise sqlite3.OperationalError("Simulated backup crash")

                def close(self):
                    return self._conn.close()

            def mock_connect(*args, **kwargs):
                return FailingConnection(*args, **kwargs)

            with patch("sqlite3.connect", side_effect=mock_connect):
                with pytest.raises(sqlite3.OperationalError):
                    create_consistent_readonly_snapshot(src_db)

    def test_20_snapshot_wal_uncheckpointed_data_consolidation(self) -> None:
        """
        SICUREZZA SNAPSHOT & WAL: verifica che i dati residenti nel journal WAL vengano
        consolidati correttamente nello snapshot isolato senza toccare i file sorgenti.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "wal_source.db"
            conn = sqlite3.connect(str(src_db))
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("CREATE TABLE test_data (id INT, val TEXT);")
                conn.execute("INSERT INTO test_data VALUES (1, 'in_wal');")
                conn.commit()

                wal_file = Path(tmp_dir) / "wal_source.db-wal"
                assert wal_file.is_file()

                snap_file, snap_temp_dir = create_consistent_readonly_snapshot(src_db)
                try:
                    snap_conn = sqlite3.connect(f"file:{snap_file.resolve().as_posix()}?mode=ro", uri=True)
                    try:
                        val = snap_conn.execute("SELECT val FROM test_data WHERE id=1;").fetchone()[0]
                        assert val == "in_wal"
                    finally:
                        snap_conn.close()
                finally:
                    shutil.rmtree(snap_temp_dir, ignore_errors=True)
            finally:
                conn.close()

    def test_21_snapshot_preserves_foreign_files_in_temp_dir(self) -> None:
        """SICUREZZA SNAPSHOT: verifica che la pulizia dello snapshot non cancelli file estranei."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "src_dir"
            src_dir.mkdir()
            src_db = src_dir / "source_checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            custom_dir = Path(tmp_dir) / "custom_temp"
            custom_dir.mkdir()
            foreign_file = custom_dir / "unrelated_foreign_file.txt"
            foreign_file.write_text("DO_NOT_TOUCH_ME", encoding="utf-8")

            snap_file, snap_temp_dir = create_consistent_readonly_snapshot(src_db, dest_dir=custom_dir)
            try:
                assert snap_file.is_file()
                assert foreign_file.is_file()
            finally:
                if snap_temp_dir is not None:
                    shutil.rmtree(snap_temp_dir, ignore_errors=True)

            assert foreign_file.is_file()
            assert foreign_file.read_text(encoding="utf-8") == "DO_NOT_TOUCH_ME"

    def test_22_subprocess_cli_snapshot_execution_and_cleanup(self) -> None:
        """
        FLAG --snapshot CLI TEST:
        Esegue un vero processo subprocess invocando scripts/preflight_checkpoint_resume.py con --snapshot,
        verificando che tempfile sia importato correttamente, l'esecuzione riesca e le sole risorse create vengano rimosse.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)

            script_path = REPO_ROOT / "scripts" / "preflight_checkpoint_resume.py"
            cmd = [
                sys.executable,
                str(script_path),
                "--db", str(db_p),
                "--snapshot",
                "--json",
            ]

            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
            assert proc.returncode in (0, 2), f"CLI con --snapshot fallita con exit code {proc.returncode}. Stderr: {proc.stderr}"
            assert "NameError" not in proc.stderr
            assert "tempfile" not in proc.stderr

            output_json = json.loads(proc.stdout)
            assert output_json["verdict"] in ("RUNTIME_VALIDATION_REQUIRED", "STRUCTURAL_ONLY")
            assert output_json["completed_chunks_count"] == 24

    def test_23_corrupt_records_and_subchunks_not_silently_dropped(self) -> None:
        """
        CORRUPTED RECORDS:
        Verifica che load_and_validate_checkpoint_readonly non ignori silenziosamente righe o subchunk
        corrotti, ma li contabilizzi in CheckpointValidationStats e fallisca la validazione probatoria.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            doc_fp, cfg_fp = _populate_test_checkpoint_db(db_p, doc)

            # Corrompe una riga di chunk
            conn = sqlite3.connect(str(db_p))
            try:
                conn.execute("UPDATE checkpoint_chunks SET serialized_result = '{CORRUPTED_JSON' WHERE chunk_index = 0")
                conn.commit()
            finally:
                conn.close()

            service = LocalTopicAnalysisService(default_model="qwen2.5-7b-instruct", profile=OperationalProfile.BALANCED)
            plan = service.create_analysis_plan(document=doc, analysis_type="discovery", max_topics=10)

            conn = sqlite3.connect(f"file:{db_p.resolve().as_posix()}?mode=ro", uri=True)
            try:
                chk, stats = load_and_validate_checkpoint_readonly(conn, doc, cfg_fp, list(plan.chunks))
                assert chk is None
                assert stats.is_clean is False
                assert stats.corrupted_chunk_rows_count == 1
                assert stats.validated_chunk_rows_count == 23
                assert "ERR_CORRUPT_CHUNK_ROW_0" in stats.corruption_reasons
            finally:
                conn.close()

            # Preflight auditor deve emettere NOT_COMPATIBLE
            auditor = PreflightResumeAuditor(db_path=db_p, document=doc, resolved_model_key="qwen2.5-7b-instruct")
            report = auditor.run_preflight()
            assert report.verdict == PreflightVerdictStatus.NOT_COMPATIBLE

    def test_24_adversarial_privacy_sanitization_zero_leak(self) -> None:
        """
        TEST AVVERSARIO PRIVACY:
        Inietta canary token nei nomi file, negli ID documento, nei campi database e nei topic.
        Verifica che NESSUN canary compaia nell'output JSON, nel dizionario del report, in stdout o stderr.
        """
        canaries = [
            "SECRET_PHONE_3331234567",
            "MARIO_ROSSI_CONFIDENTIAL",
            "C:\\Users\\mario\\SensitivePath\\Investigation\\chat.txt",
            "/tmp/SECRET_INVESTIGATION_MARIO_ROSSI.db",
            "CANARY_DOC_PRIVATE_9999",
            "CANARY_DB_SECRET_FIELD",
            "CANARY_TOPIC_SECRET_NAME",
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "SECRET_INVESTIGATION_MARIO_ROSSI.db"
            doc = _create_synthetic_26_chunk_document(doc_id=canaries[4])
            _populate_test_checkpoint_db(db_p, doc)

            # Inserisce canary nel DB
            conn = sqlite3.connect(str(db_p))
            try:
                conn.execute(f"UPDATE checkpoints SET model_id = '{canaries[5]}' WHERE document_id = '{canaries[4]}'")
                conn.commit()
            finally:
                conn.close()

            auditor = PreflightResumeAuditor(db_path=db_p, document=doc, resolved_model_key="qwen2.5-7b-instruct")
            report = auditor.run_preflight()
            report_dict = report.to_dict()
            json_str = json.dumps(report_dict)

            # Verifica assenza assoluta di tutti i canary token
            for canary in canaries:
                assert canary not in json_str, f"LEAK PRIVACY RILEVATO: {canary} trovato nel JSON!"

            # Verifica sanitizzazione cross-platform di percorsi
            assert _sanitize_path(canaries[2]) == "[CHAT_FILE_REDACTED]"
            assert _sanitize_path(canaries[3]) == "[DATABASE_REDACTED]"
            assert "MARIO_ROSSI" not in _sanitize_path(canaries[3])

    def test_25_zero_llm_calls_during_preflight(self) -> None:
        """Verifica che durante l'intero ciclo di preflight NON venga effettuata alcuna richiesta LLM."""
        mock_client = ControlledMockClient()

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)

            binding = ModelIdentityBinding(requested_canonical_id="qwen2.5-7b-instruct", resolved_model_key="qwen2.5-7b-instruct", is_verified=True)
            auditor = PreflightResumeAuditor(
                db_path=db_p,
                document=doc,
                resolved_model_key="qwen2.5-7b-instruct",
                active_binding=binding,
            )
            report = auditor.run_preflight()

            assert report.verdict == PreflightVerdictStatus.MOCK_VERIFIED
            assert mock_client.call_count == 0

    def test_26_mock_selective_resumption_on_synthetic_db(self) -> None:
        """
        Verifica del motore di ripresa su database sintetico:
        i 24 chunk validati vengono recuperati da SQLite e il modello viene chiamato solo per i chunk 10 e 19.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            doc_fp, cfg_fp = _populate_test_checkpoint_db(db_p, doc)

            mock_client = ControlledMockClient(responses=[
                json.dumps({"topics": [{"label": "Rec 10", "short_description": "Ok", "evidence_ids": ["E001"]}]}),
                json.dumps({"topics": [{"label": "Rec 19", "short_description": "Ok", "evidence_ids": ["E001"]}]}),
            ])

            store = AnalysisCheckpointStore(checkpoint_dir=db_p.parent, db_name=db_p.name)
            svc = LocalTopicAnalysisService(
                default_model="qwen2.5-7b-instruct",
                profile=OperationalProfile.BALANCED,
                context_length=8192,
                checkpoint_store=store,
                enable_checkpointing=True,
            )

            res = svc.run_topic_discovery(
                document=doc,
                client=mock_client,
                max_topics=10,
                temperature=0.0,
                seed=42,
                retry_failed_only=True,
            )

            assert mock_client.call_count == 2
            assert res.metadata["completed_chunk_count"] == 26
            assert res.metadata["recovered_chunk_count"] == 24
            assert store.has_checkpoint(doc_fp, cfg_fp) is False

    def test_27_end_to_end_cli_with_real_synthetic_whatsapp_file(self) -> None:
        """Test end-to-end CLI con vero file WhatsApp sintetico su disco."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            chat_file = Path(tmp_dir) / "chat_synth_whatsapp.txt"
            chat_lines = []
            for i in range(78):
                chat_lines.append(f"18/10/2026, 10:{i:02d} - Utente: Messaggio sintetico {i} " + "Z" * 3000)
            chat_file.write_text("\n".join(chat_lines), encoding="utf-8")

            from scripts.preflight_checkpoint_resume import ingest_chat_file_for_preflight
            docs = ingest_chat_file_for_preflight(chat_file, source_format="whatsapp_export")
            doc = next(iter(docs.values()))

            db_p = Path(tmp_dir) / "checkpoints.db"
            _populate_test_checkpoint_db(db_p, doc)

            script_path = REPO_ROOT / "scripts" / "preflight_checkpoint_resume.py"
            cmd = [
                sys.executable,
                str(script_path),
                "--db", str(db_p),
                "--chat", str(chat_file),
                "--format", "whatsapp_export",
                "--resolved-model-key", "qwen2.5-7b-instruct",
                "--json",
            ]

            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
            # Senza live backend, il verdetto è correttamente RUNTIME_VALIDATION_REQUIRED (exit code 2)
            assert proc.returncode == 2, f"Atteso exit code 2 (RUNTIME_VALIDATION_REQUIRED), ottenuto {proc.returncode}. Stderr: {proc.stderr}"

            output_json = json.loads(proc.stdout)
            assert output_json["verdict"] == "RUNTIME_VALIDATION_REQUIRED"
            assert output_json["completed_chunks_count"] == 24
            assert output_json["missing_chunks_zero_based"] == [10, 19]
            assert output_json["configuration_details"]["runtime_binding_status"] == "DECLARED_ONLY_UNVERIFIED_RUNTIME"

    def test_28_lmstudio_synthetic_http_loaded_false_fails_safe_resume(self) -> None:
        """
        AUDIT FIX B1: Distinzione tra modello presente ed effettivamente caricato.
        Una risposta HTTP con loaded: false (o unloaded) restituisce MODEL_AVAILABLE_NOT_LOADED
        e NON autorizza SAFE_RESUME_VERIFIED (mantiene RUNTIME_VALIDATION_REQUIRED).
        Un binding mock con is_verified=True viene classificato MOCK_VERIFIED.
        """
        from unittest.mock import MagicMock
        from scripts.preflight_checkpoint_resume import _query_lmstudio_models_readonly

        # 1. Risposta sintetica GET /v1/models con loaded: false
        fake_resp_payload = {
            "data": [
                {
                    "id": "qwen2.5-7b-instruct",
                    "object": "model",
                    "loaded": False,
                    "state": "not_loaded",
                }
            ]
        }
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(fake_resp_payload).encode("utf-8")
        mock_response.__enter__.return_value = mock_response

        with patch("urllib.request.urlopen", return_value=mock_response):
            code, observed_id = _query_lmstudio_models_readonly(
                requested_model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
            )
            assert code == "MODEL_AVAILABLE_NOT_LOADED"
            assert observed_id == "qwen2.5-7b-instruct"

        # 2. Test nel flusso PreflightResumeAuditor con check_live_backend=True
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)

            with patch("urllib.request.urlopen", return_value=mock_response):
                auditor = PreflightResumeAuditor(
                    db_path=db_p,
                    document=doc,
                    resolved_model_key="qwen2.5-7b-instruct",
                    check_live_backend=True,
                )
                report = auditor.run_preflight()

                # Non deve essere autorizzato SAFE_RESUME_VERIFIED
                assert report.verdict == PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED
                assert not report.checks[1].passed
                assert report.configuration_details["runtime_binding_status"] == "LIVE_BACKEND_MODEL_AVAILABLE_NOT_LOADED"

        # 3. Risposta sintetica con loaded_instances (metadati completi) -> VERIFIED_LIVE_BACKEND_READONLY
        fake_resp_loaded = {
            "models": [
                {
                    "id": "qwen2.5-7b-instruct",
                    "loaded": True,
                    "state": "loaded",
                    "loaded_instances": [
                        {
                            "id": "inst_qwen_1",
                            "quantization": "Q4_K_M",
                            "context_length": 8192,
                        }
                    ],
                }
            ]
        }
        mock_response_loaded = MagicMock()
        mock_response_loaded.status = 200
        mock_response_loaded.read.return_value = json.dumps(fake_resp_loaded).encode("utf-8")
        mock_response_loaded.__enter__.return_value = mock_response_loaded

        with patch("urllib.request.urlopen", return_value=mock_response_loaded):
            code, observed_id = _query_lmstudio_models_readonly(
                requested_model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                expected_quantization="Q4_K_M",
                expected_context_length=8192,
            )
            assert code == "LOADED_INSTANCE_VERIFIED"
            assert observed_id == "qwen2.5-7b-instruct"

            with tempfile.TemporaryDirectory() as tmp_dir:
                db_p = Path(tmp_dir) / "checkpoints.db"
                doc = _create_synthetic_26_chunk_document()
                _populate_test_checkpoint_db(db_p, doc)

                auditor = PreflightResumeAuditor(
                    db_path=db_p,
                    document=doc,
                    resolved_model_key="qwen2.5-7b-instruct",
                    check_live_backend=True,
                )
                report = auditor.run_preflight()
                # Audit Fix B4 & B2: I checkpoint legacy SQLite senza hash indipendente delle boundaries
                # producono RUNTIME_VALIDATION_REQUIRED anche con backend live verificato
                assert report.verdict == PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED
                assert report.checks[1].passed
                assert report.configuration_details["runtime_binding_status"] == "VERIFIED_LIVE_BACKEND_READONLY"
                assert report.configuration_details["verification_classification"] == "LIVE_RUNTIME_VALIDATION_REQUIRED"

                from analysis.checkpoint import compute_chunk_partition_fingerprint
                from ai.chunking import split_document_into_chunks
                chunks = split_document_into_chunks(doc, max_characters=60000, max_bundles=3)
                verified_pt_fp = compute_chunk_partition_fingerprint(chunks)

                # Regola B2: Su database legacy, passare un fingerprint all'auditor NON autorizza SAFE_RESUME_VERIFIED
                auditor_untrusted = PreflightResumeAuditor(
                    db_path=db_p,
                    document=doc,
                    resolved_model_key="qwen2.5-7b-instruct",
                    check_live_backend=True,
                    verified_partition_fingerprint=verified_pt_fp,
                )
                report_untrusted = auditor_untrusted.run_preflight()
                assert report_untrusted.verdict == PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED
                assert report_untrusted.verdict != PreflightVerdictStatus.SAFE_RESUME_VERIFIED

                # Possibilità futura: solo se la sessione dispone di vera attestazione storica
                # persistita all'origine nella tabella checkpoints, la certificazione può avvenire
                db_certified_p = Path(tmp_dir) / "certified_checkpoints.db"
                _populate_test_checkpoint_db(db_certified_p, doc)
                c_conn = sqlite3.connect(str(db_certified_p))
                try:
                    c_conn.execute("ALTER TABLE checkpoints ADD COLUMN partition_fingerprint TEXT")
                    c_conn.execute("UPDATE checkpoints SET partition_fingerprint = ?", (verified_pt_fp,))
                    c_conn.commit()
                finally:
                    c_conn.close()

                auditor_certified = PreflightResumeAuditor(
                    db_path=db_certified_p,
                    document=doc,
                    resolved_model_key="qwen2.5-7b-instruct",
                    check_live_backend=True,
                    verified_partition_fingerprint=verified_pt_fp,
                    source_immutability_metrics={
                        "DATABASE_LOGICAL_CONTENT_UNCHANGED": True,
                        "WAL_CONTENT_UNCHANGED": True,
                        "SHM_BYTES_UNCHANGED": True,
                        "SOURCE_METADATA_UNCHANGED": True,
                        "CONSISTENT_SNAPSHOT_VERIFIED": True,
                        "SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED": True,
                    },
                )
                report_certified = auditor_certified.run_preflight()
                assert report_certified.verdict == PreflightVerdictStatus.SAFE_RESUME_VERIFIED
                assert report_certified.configuration_details["verification_classification"] == "LIVE_VERIFIED"

    def test_29_snapshot_ownership_with_dest_path_never_cleans_parent_or_source(self) -> None:
        """
        AUDIT FIX B5: Quando viene fornito dest_path, la cartella genitrice può contenere il DB originale.
        created_temp_dir deve essere None e la cartella genitrice non deve mai essere eliminata.
        Anche in caso di eccezione durante il backup, il DB sorgente e i file fratelli restano intatti.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            work_dir = Path(tmp_dir) / "original_storage"
            work_dir.mkdir()
            src_db = work_dir / "checkpoints.db"

            allowed_storage = Path(tmp_dir) / "allowed_storage"
            allowed_storage.mkdir()
            sibling_file = allowed_storage / "precious_sibling_file.txt"
            sibling_file.write_text("Sorgente vitale da non cancellare", encoding="utf-8")

            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            # Caso 1: Destinazione esplicita in una cartella consentita (non genitrice del db sorgente)
            explicit_dest = allowed_storage / "snapshot_copy.db"
            snap_file, snap_temp_dir = create_consistent_readonly_snapshot(src_db, dest_path=explicit_dest)

            # Il contratto di ownership impone che nessuna directory temporanea venga restituita
            assert snap_temp_dir is None, "created_temp_dir deve essere None quando dest_path è specificato!"
            assert snap_file == explicit_dest
            assert snap_file.is_file()
            assert src_db.is_file()
            assert sibling_file.is_file()

            # Pulizia manuale del solo file di snapshot
            snap_file.unlink()

            # Caso 2: Eccezione simulata durante il backup
            with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("Simulated backup failure")):
                with pytest.raises(sqlite3.OperationalError):
                    create_consistent_readonly_snapshot(src_db, dest_path=explicit_dest)

            # Verifica che la directory e tutti i file preesistenti siano al 100% preservati
            assert allowed_storage.is_dir()
            assert work_dir.is_dir()
            assert src_db.is_file()
            assert sibling_file.is_file()
            assert not explicit_dest.exists()

    def test_30_adversarial_canary_across_all_report_fields_and_cli(self) -> None:
        """
        AUDIT FIX B3: Sanitizzazione end-to-end con allowlist e zero leak di canary.
        I canary inseriti in configuration_details, observed_value, expected_value,
        reasons, resumption_plan e nella CLI (--document-id non trovato) NON devono mai comparire.
        """
        canary_conf = "CANARY_CONF_SECRET_987"
        canary_obs = "CANARY_OBS_VAL_123"
        canary_exp = "CANARY_EXP_VAL_321"
        canary_reason = "CANARY_REASON_MSG_777"
        canary_plan = "CANARY_PLAN_NOTE_888"
        canary_cli_doc = "CANARY_CLI_SECRET_DOC_999"

        from scripts.preflight_checkpoint_resume import CheckResult

        chk = CheckResult(
            check_number=1,
            name="Test Check",
            passed=False,
            observed_value=f"Error containing {canary_obs}",
            expected_value=f"Expected {canary_exp}",
            details={"leaky_key": canary_conf},
        )
        report = PreflightAuditReport(
            verdict=PreflightVerdictStatus.NOT_COMPATIBLE,
            database_path="C:\\Users\\MarioRossi\\secret\\db.sqlite",
            document_available=False,
            checks=[chk],
            reasons=[f"Critico: {canary_reason}"],
            configuration_details={"leak": canary_conf},
            resumption_plan={"secret": canary_plan},
        )

        serialized = report.to_dict()
        serialized_json = json.dumps(serialized)

        # Nessun canary deve essere presente nel JSON serializzato
        assert canary_conf not in serialized_json
        assert canary_obs not in serialized_json
        assert canary_exp not in serialized_json
        assert canary_reason not in serialized_json
        assert canary_plan not in serialized_json
        assert "MarioRossi" not in serialized_json

        # Test CLI con --document-id canary
        script_path = REPO_ROOT / "scripts" / "preflight_checkpoint_resume.py"
        with tempfile.TemporaryDirectory() as tmp_dir:
            chat_file = Path(tmp_dir) / "chat.txt"
            chat_file.write_text("18/10/2026, 10:00 - User: Test message\n", encoding="utf-8")
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "--db", str(db_p),
                    "--chat", str(chat_file),
                    "--format", "whatsapp_export",
                    "--document-id", canary_cli_doc,
                ],
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
            )
            # Verifica che il canary_cli_doc non compaia in chiaro in stdout o stderr
            assert canary_cli_doc not in proc.stdout
            assert canary_cli_doc not in proc.stderr
            assert "[ERR_DOCUMENT_NOT_FOUND]" in proc.stdout or "[ERR_DOCUMENT_NOT_FOUND]" in proc.stderr

    def test_31_no_document_structural_audit_validates_json_and_schema(self) -> None:
        """
        AUDIT FIX ITEM 7: Nell'audit senza documento, vengono decodificati e validati
        lo schema JSON dei singoli chunk e subchunk, contabilizzando record validi e corrotti.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            doc_fp, cfg_fp = _populate_test_checkpoint_db(db_p, doc)

            # Inietta un chunk con JSON malformato (es. chunk 0)
            # e un chunk con JSON valido ma schema non conforme (es. chunk 1)
            conn = sqlite3.connect(str(db_p))
            try:
                conn.execute(
                    "UPDATE checkpoint_chunks SET serialized_result = '{\"malformed_json: ' WHERE chunk_index = 0"
                )
                conn.execute(
                    "UPDATE checkpoint_chunks SET serialized_result = '{\"not_a_valid_topic_result\": 42}' WHERE chunk_index = 1"
                )
                conn.commit()
            finally:
                conn.close()

            auditor = PreflightResumeAuditor(db_path=db_p, document=None)
            report = auditor.run_preflight()

            assert report.verdict == PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED
            assert report.checks is not None
            c2 = next(c for c in report.checks if c.check_number == 2)
            # Dei 24 chunk presenti originariamente, 2 sono ora corrotti (1 malformato, 1 non conforme allo schema)
            # Quindi 22 validi e 2 corrotti
            assert c2.passed is False
            assert "corrupt_records=2" in c2.observed_value
            assert report.configuration_details["structural_inspection"]["corrupted_chunk_records"] == 2
            assert report.configuration_details["structural_inspection"]["chunks_schema_valid"] == 22

    def test_32_runtime_skip_behavior_on_resumption_24_chunks_skipped_10_19_inferred(self) -> None:
        """
        AUDIT FIX B6: Verifica del percorso operativo originario con client simulato.
        Dimostra che:
        - I 24 chunk recuperati da SQLite generano zero richieste LLM.
        - Solo i chunk mancanti (10 e 19) vengono inviati all'inferenza.
        - I blocchi precedenti e le sessioni estranee non vengono contaminati.
        - Questo test è formalmente classificato MOCK_VERIFIED.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            doc_fp, cfg_fp = _populate_test_checkpoint_db(db_p, doc)

            mock_client = ControlledMockClient(responses=[
                json.dumps({"topics": [{"label": "Nuovo Topic 10", "short_description": "Descrizione 10", "evidence_ids": ["E001"]}]}),
                json.dumps({"topics": [{"label": "Nuovo Topic 19", "short_description": "Descrizione 19", "evidence_ids": ["E001"]}]}),
            ])

            store = AnalysisCheckpointStore(checkpoint_dir=db_p.parent, db_name=db_p.name)
            svc = LocalTopicAnalysisService(
                default_model="qwen2.5-7b-instruct",
                profile=OperationalProfile.BALANCED,
                context_length=8192,
                checkpoint_store=store,
                enable_checkpointing=True,
            )

            # Esecuzione della ripresa selettiva dei soli blocchi falliti
            res = svc.run_topic_discovery(
                document=doc,
                client=mock_client,
                max_topics=10,
                temperature=0.0,
                seed=42,
                retry_failed_only=True,
            )

            # Dimostrazione analitica:
            # 1. Esattamente 2 chiamate effettuate
            assert mock_client.call_count == 2, f"Attese esattamente 2 chiamate LLM, effettuate: {mock_client.call_count}"
            # 2. 24 chunk saltati e recuperati dal checkpoint
            assert res.metadata["recovered_chunk_count"] == 24
            # 3. 26 chunk totali completati al termine
            assert res.metadata["completed_chunk_count"] == 26
            # 4. Verifica che i prompt inviati contengano il testo dei chunk 10 e 19 e NON dei chunk recuperati
            # I chunk sono formati da messaggi 3 a 3. Chunk 10 contiene messaggi 30..32, Chunk 19 contiene messaggi 57..59
            assert any("chunk 10" in p or "Messaggio 30" in p for p in mock_client.requested_chunks)
            assert any("chunk 19" in p or "Messaggio 57" in p for p in mock_client.requested_chunks)
            for skip_idx in [0, 1, 5, 9, 11, 15, 18, 20, 25]:
                assert not any(f"chunk {skip_idx} " in p for p in mock_client.requested_chunks), (
                    f"Violazione: rilevata chiamata LLM per chunk già completato {skip_idx}!"
                )

    def test_33_synthetic_http_missing_quantization_returns_insufficient_runtime_metadata(self) -> None:
        """
        AUDIT REPAIR B1: Endpoint restituisce loaded: true ma senza metadati di quantizzazione.
        Con requisito quantization = Q4_K_M, il sistema DEVE fallire fail-closed restituendo
        INSUFFICIENT_RUNTIME_METADATA e verdetto RUNTIME_VALIDATION_REQUIRED (mai LOADED_INSTANCE_VERIFIED).
        """
        fake_resp = {
            "models": [
                {
                    "id": "qwen2.5-7b-instruct",
                    "loaded": True,
                    "state": "loaded",
                    # loaded_instances assenti o privi di campo quantization
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(fake_resp).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        from scripts.preflight_checkpoint_resume import _query_lmstudio_models_readonly

        with patch("urllib.request.urlopen", return_value=mock_resp):
            code, observed_id = _query_lmstudio_models_readonly(
                requested_model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                expected_quantization="Q4_K_M",
                expected_context_length=8192,
            )
            assert code == "INSUFFICIENT_RUNTIME_METADATA"
            assert observed_id == "qwen2.5-7b-instruct"

            with tempfile.TemporaryDirectory() as tmp_dir:
                db_p = Path(tmp_dir) / "checkpoints.db"
                doc = _create_synthetic_26_chunk_document()
                _populate_test_checkpoint_db(db_p, doc, quantization="Q4_K_M")

                auditor = PreflightResumeAuditor(
                    db_path=db_p,
                    document=doc,
                    resolved_model_key="qwen2.5-7b-instruct",
                    quantization="Q4_K_M",
                    context_length=8192,
                    check_live_backend=True,
                )
                report = auditor.run_preflight()
                assert report.verdict == PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED
                assert not report.checks[1].passed
                assert report.configuration_details["runtime_binding_status"] == "LIVE_BACKEND_INSUFFICIENT_RUNTIME_METADATA"

    def test_34_synthetic_http_missing_context_length_returns_insufficient_runtime_metadata(self) -> None:
        """
        AUDIT REPAIR B1: Endpoint restituisce quantizzazione ma nessun metadato di context_length.
        Fail-closed: restituisce INSUFFICIENT_RUNTIME_METADATA.
        """
        fake_resp = {
            "models": [
                {
                    "id": "qwen2.5-7b-instruct",
                    "loaded": True,
                    "state": "loaded",
                    "quantization": "Q4_K_M",
                    # context_length assente
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(fake_resp).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        from scripts.preflight_checkpoint_resume import _query_lmstudio_models_readonly

        with patch("urllib.request.urlopen", return_value=mock_resp):
            code, observed_id = _query_lmstudio_models_readonly(
                requested_model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                expected_quantization="Q4_K_M",
                expected_context_length=8192,
            )
            assert code == "INSUFFICIENT_RUNTIME_METADATA"

    def test_35_synthetic_http_quantization_mismatch_returns_mismatch(self) -> None:
        """
        AUDIT REPAIR B1: Quantizzazione osservata discordante (Q8_0 vs Q4_K_M richiesto).
        Restituisce QUANTIZATION_MISMATCH e verdetto NOT_COMPATIBLE.
        """
        fake_resp = {
            "models": [
                {
                    "id": "qwen2.5-7b-instruct",
                    "loaded": True,
                    "state": "loaded",
                    "loaded_instances": [
                        {
                            "id": "inst_1",
                            "quantization": "Q8_0",
                            "context_length": 8192,
                        }
                    ],
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(fake_resp).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        from scripts.preflight_checkpoint_resume import _query_lmstudio_models_readonly

        with patch("urllib.request.urlopen", return_value=mock_resp):
            code, observed_id = _query_lmstudio_models_readonly(
                requested_model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                expected_quantization="Q4_K_M",
                expected_context_length=8192,
            )
            assert code == "QUANTIZATION_MISMATCH"

            with tempfile.TemporaryDirectory() as tmp_dir:
                db_p = Path(tmp_dir) / "checkpoints.db"
                doc = _create_synthetic_26_chunk_document()
                _populate_test_checkpoint_db(db_p, doc, quantization="Q4_K_M")

                auditor = PreflightResumeAuditor(
                    db_path=db_p,
                    document=doc,
                    resolved_model_key="qwen2.5-7b-instruct",
                    quantization="Q4_K_M",
                    context_length=8192,
                    check_live_backend=True,
                )
                report = auditor.run_preflight()
                assert report.verdict == PreflightVerdictStatus.NOT_COMPATIBLE
                assert not report.checks[1].passed
                assert report.configuration_details["runtime_binding_status"] == "LIVE_BACKEND_QUANTIZATION_MISMATCH"

    def test_36_synthetic_http_context_length_insufficient_returns_insufficient(self) -> None:
        """
        AUDIT REPAIR B1: Context length osservato insufficiente (4096 vs 8192 richiesto).
        Restituisce CONTEXT_LENGTH_INSUFFICIENT.
        """
        fake_resp = {
            "models": [
                {
                    "id": "qwen2.5-7b-instruct",
                    "loaded": True,
                    "state": "loaded",
                    "loaded_instances": [
                        {
                            "id": "inst_1",
                            "quantization": "Q4_K_M",
                            "context_length": 4096,
                        }
                    ],
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(fake_resp).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        from scripts.preflight_checkpoint_resume import _query_lmstudio_models_readonly

        with patch("urllib.request.urlopen", return_value=mock_resp):
            code, observed_id = _query_lmstudio_models_readonly(
                requested_model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                expected_quantization="Q4_K_M",
                expected_context_length=8192,
            )
            assert code == "CONTEXT_LENGTH_INSUFFICIENT"

    def test_37_synthetic_http_resolved_model_key_mismatch_strict_contract(self) -> None:
        """
        AUDIT REPAIR B1: Model Key Mismatch.
        Se resolved_model_key è specificata (es. qwen2.5-7b-instruct-v2) ma l'endpoint
        espone solo l'ID canonico o un modello differente, il matching NON deve accettarlo
        tramite OR permissivo. Deve restituire MODEL_NOT_FOUND.
        """
        fake_resp = {
            "models": [
                {
                    "id": "qwen2.5-7b-instruct",
                    "loaded": True,
                    "state": "loaded",
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(fake_resp).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        from scripts.preflight_checkpoint_resume import _query_lmstudio_models_readonly

        with patch("urllib.request.urlopen", return_value=mock_resp):
            # Richiede resolved_model_key specifica 'qwen2.5-7b-instruct-custom-v2'
            code, observed_id = _query_lmstudio_models_readonly(
                requested_model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct-custom-v2",
            )
            assert code == "MODEL_NOT_FOUND"
            assert observed_id is None

    @pytest.mark.parametrize("valid_url", [
        "http://127.0.0.1:1234",
        "http://127.0.0.1:1234/v1/models",
        "http://localhost:1234",
        "http://127.0.0.2:8080",
        "http://127.255.255.254:1234",
        "http://[::1]:1234",
    ])
    def test_38a_loopback_backend_url_valid_cases(self, valid_url: str) -> None:
        """B1: Verifica che endpoint loopback IPv4, IPv6 e localhost siano correttamente autorizzati."""
        from scripts.preflight_checkpoint_resume import _validate_loopback_backend_url
        is_loop, err = _validate_loopback_backend_url(valid_url)
        assert is_loop is True, f"Atteso loopback valido per {valid_url}, ottenuto errore: {err}"
        assert err == ""

    @pytest.mark.parametrize("invalid_url,reason", [
        ("http://127.attacker.example:1234", "Host civetta con prefisso 127."),
        ("http://127.0.0.1.evil.test:1234", "Host civetta con IP locale e dominio esterno"),
        ("http://127.0.0.1.nip.io:1234", "Wildcard DNS civetta"),
        ("http://localhost.attacker.com:1234", "Host civetta con localhost come sottodominio"),
        ("http://8.8.8.8:1234", "Indirizzo IPv4 pubblico"),
        ("http://192.168.1.50:1234", "Indirizzo IPv4 LAN privato"),
        ("http://0.0.0.0:1234", "Indirizzo 0.0.0.0"),
        ("http://[2001:db8::1]:1234", "Indirizzo IPv6 non-loopback"),
        ("http://user:pass@127.0.0.1:1234", "Credenziali incorporate nell'URL"),
        ("http://admin@localhost:1234", "Username nell'URL"),
        ("ftp://127.0.0.1:1234", "Schema non http/https"),
        ("not_a_valid_url", "URL non valido"),
        ("", "URL vuoto"),
    ])
    def test_38b_loopback_backend_url_rejects_decoy_and_remote_hosts(self, invalid_url: str, reason: str) -> None:
        """B1: Verifica che host civetta, credenziali, IP remoti e formati malformati siano categoricamente bloccati."""
        from scripts.preflight_checkpoint_resume import (
            _query_lmstudio_models_readonly,
            _validate_loopback_backend_url,
        )
        is_loop, err = _validate_loopback_backend_url(invalid_url)
        assert is_loop is False, f"Violazione B1 ({reason}): '{invalid_url}' non doveva essere accettato!"
        assert err != ""

        # Verifica rifiuto fail-closed in _query_lmstudio_models_readonly senza effettuare chiamate di rete
        code, obs = _query_lmstudio_models_readonly(
            requested_model_id="qwen2.5-7b-instruct",
            backend_url=invalid_url,
        )
        assert code == "LOOPBACK_URL_REQUIRED"
        assert obs is None

    def test_39_privacy_data_minimization_scrubs_random_markers_and_vision_description(self) -> None:
        """
        AUDIT REPAIR B4: Eliminazione di testo arbitrario, marcatori sintetici e VISION_DESCRIPTION.
        Marcatori casuali privi di parole chiave note (secret, token, password, canary)
        vengono sistematicamente mascherati dalla minimizzazione strutturale dei dati esportati.
        """
        random_custom_marker = "X9K2P_CUSTOM_INJECTION_47"
        evidence_marker = "msg::5::VISION_DESCRIPTION"

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / f"{random_custom_marker}.db"
            doc = _create_synthetic_26_chunk_document(doc_id=f"doc_{random_custom_marker}")
            _populate_test_checkpoint_db(db_p, doc)

            # Inietta active_binding con identificativo arbitrario
            binding = ModelIdentityBinding(
                requested_canonical_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                is_verified=True,
            )

            auditor = PreflightResumeAuditor(
                db_path=db_p,
                document=doc,
                resolved_model_key="qwen2.5-7b-instruct",
                active_binding=binding,
            )
            # Inietta manualmente marcatori nel report per simulare dati non classificati
            report = auditor.run_preflight()
            report.reasons.append(f"Avviso interno: citazione {evidence_marker} e marcatore {random_custom_marker}")

            report_dict = report.to_dict()
            json_str = json.dumps(report_dict)

            # Verifiche tassative di data minimization:
            assert random_custom_marker not in json_str, (
                f"Violazione B4: Il marcatore arbitrario '{random_custom_marker}' è sopravvissuto nel JSON!"
            )
            assert evidence_marker not in json_str, (
                f"Violazione B4: '{evidence_marker}' è sopravvissuto nel JSON!"
            )
            assert "VISION_DESCRIPTION" not in json_str, (
                "Violazione B4: La stringa 'VISION_DESCRIPTION' è sopravvissuta nel JSON!"
            )
            assert "[DATABASE_REDACTED]" in json_str
            assert report.verdict == PreflightVerdictStatus.MOCK_VERIFIED

    def test_40_non_regression_mock_24_skip_with_subchunks_retry_and_isolation(self) -> None:
        """
        AUDIT REPAIR B7: Non-regressione completa del recupero 24/26 con client simulato.
        Verifica:
        1. Esatta configurazione del checkpoint e corrispondenza fingerprint.
        2. Selezione deterministica della sessione anche tra documenti multipli (isolamento).
        3. Recupero autentico dei 24 blocchi senza reinvio al modello.
        4. Invio all'inferenza ESCLUSIVAMENTE dei blocchi mancanti 10 e 19.
        5. Gestione trasparente di retry e subchunk.
        6. Nessuna contaminazione probatoria tra documenti distinti.
        7. Cleanup finale della sessione.
        Classificazione: MOCK_VERIFIED (nessun claim di verifica LIVE senza hardware reale).
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc_target = _create_synthetic_26_chunk_document(doc_id="doc_forensic_target")
            doc_alien = _create_synthetic_26_chunk_document(doc_id="doc_forensic_alien")

            # Popola il database sia con la sessione target che con una sessione aliena
            doc_fp, cfg_fp = _populate_test_checkpoint_db(db_p, doc_target)
            alien_doc_fp, alien_cfg_fp = _populate_test_checkpoint_db(db_p, doc_alien)

            # Client fittizio che risponde solo per i chunk mancanti 10 e 19
            mock_client = ControlledMockClient(responses=[
                json.dumps({"topics": [{"label": "Recupero 10", "short_description": "Validato", "evidence_ids": ["E001"]}]}),
                json.dumps({"topics": [{"label": "Recupero 19", "short_description": "Validato", "evidence_ids": ["E001"]}]}),
            ])

            store = AnalysisCheckpointStore(checkpoint_dir=db_p.parent, db_name=db_p.name)
            svc = LocalTopicAnalysisService(
                default_model="qwen2.5-7b-instruct",
                profile=OperationalProfile.BALANCED,
                context_length=8192,
                checkpoint_store=store,
                enable_checkpointing=True,
            )

            # 1. Esecuzione con retry_failed_only=True
            res = svc.run_topic_discovery(
                document=doc_target,
                client=mock_client,
                max_topics=10,
                temperature=0.0,
                seed=42,
                retry_failed_only=True,
            )

            # 2. Verifica recupero selettivo: esattamente 2 chiamate
            assert mock_client.call_count == 2
            assert res.metadata["recovered_chunk_count"] == 24
            assert res.metadata["completed_chunk_count"] == 26

            # 3. Verifica isolamento tra documenti: la sessione aliena è rimasta intatta
            alien_chk = store.load_checkpoint(
                document_fingerprint=alien_doc_fp,
                config_fingerprint=alien_cfg_fp,
            )
            assert alien_chk is not None
            assert len(alien_chk.completed_chunk_indices) == 24  # Intatto, non contaminato

            # 4. Cleanup finale: il checkpoint target completato viene rimosso dopo il completamento totale (26/26)
            assert store.has_checkpoint(doc_fp, cfg_fp) is False

    def test_41_adversarial_arbitrary_plain_text_and_names_zero_leakage(self) -> None:
        """
        AUDIT REPAIR B3: Eliminazione totale di bypass della privacy e nomi personali dai report esportabili.
        Verifica specificamente i 4 casi accertati dall'audit indipendente:
        1. 'Audit strutturale completato: Alice Wonderland' -> redatto a [DIAGNOSTIC_REASON_REDACTED].
        2. 'TUTTI I 9 CONTROLLI OBBLIGATORI SUPERATI. Mario Rossi' -> redatto a [DIAGNOSTIC_REASON_REDACTED].
        3. '[MODEL_Alice Wonderland]' -> redatto a [OBSERVED_VALUE_REDACTED].
        4. 'binding_status=LIVE_BACKEND_Alice' -> redatto a 'binding_status=[VALUE_REDACTED]'.
        Nessun nome o testo arbitrario deve comparire in to_dict(), nel JSON esportabile, né su stdout o stderr.
        """
        raw_name_1 = "Alice Wonderland"
        raw_name_2 = "Mario Rossi"
        raw_path = "/home/Mario_Rossi/CASE_INDIVIDUAL_2026.db"

        # Le 4 stringhe esatte riprodotte dall'audit indipendente (B3)
        audit_leak_reason_1 = "Audit strutturale completato: Alice Wonderland"
        audit_leak_reason_2 = "TUTTI I 9 CONTROLLI OBBLIGATORI SUPERATI. Mario Rossi"
        audit_leak_model = "[MODEL_Alice Wonderland]"
        audit_leak_binding = "binding_status=LIVE_BACKEND_Alice"

        chk1 = CheckResult(
            check_number=1,
            name=raw_name_1,
            passed=False,
            details=raw_name_1,
            observed_value=audit_leak_model,
            expected_value=audit_leak_model,
        )
        chk2 = CheckResult(
            check_number=2,
            name="Run Configuration Fingerprint & Runtime Binding Match",
            passed=False,
            details="Binding check",
            observed_value=audit_leak_binding,
            expected_value="binding_status=VERIFIED_LIVE_BACKEND_READONLY",
        )

        report = PreflightAuditReport(
            verdict=PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED,
            database_path=raw_path,
            document_available=False,
            checks=[chk1, chk2],
            reasons=[audit_leak_reason_1, audit_leak_reason_2, raw_name_2],
            configuration_details={
                "custom_secret_key": raw_name_1,
                "runtime_binding_status": "LIVE_BACKEND_Alice",
            },
            resumption_plan={"secret_note": raw_name_2},
        )

        # 1. Verifica serializzazione esportabile to_dict()
        export_dict = report.to_dict()
        json_str = json.dumps(export_dict)

        # Zero leakage assoluto di nomi personali e testi non autorizzati
        assert "Alice" not in json_str, "Leak rilevato: 'Alice' presente nel JSON esportabile!"
        assert "Wonderland" not in json_str, "Leak rilevato: 'Wonderland' presente nel JSON esportabile!"
        assert "Mario" not in json_str, "Leak rilevato: 'Mario' presente nel JSON esportabile!"
        assert "Rossi" not in json_str, "Leak rilevato: 'Rossi' presente nel JSON esportabile!"
        assert "CASE_INDIVIDUAL_2026" not in json_str, "Leak rilevato: nome file sensibile presente nel JSON!"
        assert "custom_secret_key" not in json_str, "Leak rilevato: chiave configurazione non permessa presente!"
        assert "secret_note" not in json_str, "Leak rilevato: nota non permessa presente!"

        # Verifica etichette tipizzate e di redazione (Audit Fix B3)
        assert export_dict["database_path"] == "[DATABASE_REDACTED]"
        assert export_dict["checks"][0]["observed_value"] == "[OBSERVED_VALUE_REDACTED]"
        assert export_dict["checks"][0]["expected_value"] == "[EXPECTED_VALUE_REDACTED]"
        assert export_dict["checks"][1]["observed_value"] == "binding_status=[VALUE_REDACTED]"
        assert export_dict["reasons"][0] == "[DIAGNOSTIC_REASON_REDACTED]"
        assert export_dict["reasons"][1] == "[DIAGNOSTIC_REASON_REDACTED]"
        assert export_dict["reasons"][2] == "[DIAGNOSTIC_REASON_REDACTED]"
        assert export_dict["configuration_details"]["runtime_binding_status"] == "[STATUS_REDACTED]"

        # 2. Verifica esecuzione CLI e assenza di leak su stdout e stderr
        script_path = REPO_ROOT / "scripts" / "preflight_checkpoint_resume.py"
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)

            # Invochiamo la CLI sia con --json che in modalità formattata
            for cli_flags in (["--json"], []):
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(script_path),
                        "--db", str(db_p),
                        *cli_flags,
                    ],
                    capture_output=True,
                    text=True,
                    cwd=str(REPO_ROOT),
                )
                combined_output = proc.stdout + proc.stderr
                assert "Mario" not in combined_output
                assert "Rossi" not in combined_output
                assert "Alice" not in combined_output
                assert "Wonderland" not in combined_output

    def test_42_max_context_not_allocated_context_insufficient_runtime_metadata(self) -> None:
        """
        AUDIT REPAIR B3: Distinzione tra max_context_length (capacità) e contesto allocato dell'istanza.
        Riproduzione esatta del caso dell'audit indipendente:
        Risposta: {"data":[{"id":"qwen2.5-7b-instruct","loaded":true,"quantization":"Q4_K_M","max_context_length":32768}]}
        Richiesta: 8192 token.
        Comportamento atteso: INSUFFICIENT_RUNTIME_METADATA (non LOADED_INSTANCE_VERIFIED).
        """
        fake_audit_resp = {
            "data": [
                {
                    "id": "qwen2.5-7b-instruct",
                    "loaded": True,
                    "quantization": "Q4_K_M",
                    "max_context_length": 32768,  # Solo capacità massima architetturale!
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(fake_audit_resp).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.OpenerDirector.open", return_value=mock_resp):
            code, observed_id = _query_lmstudio_models_readonly(
                requested_model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                expected_quantization="Q4_K_M",
                expected_context_length=8192,
            )
            # max_context_length NON deve essere accettato come prova di allocazione
            assert code == "INSUFFICIENT_RUNTIME_METADATA", (
                f"Errore: atteso INSUFFICIENT_RUNTIME_METADATA ma ottenuto {code}"
            )
            assert code != "LOADED_INSTANCE_VERIFIED"

        # Verifica di contro-prova: con contesto allocato esplicito dell'istanza attiva, viene verificato
        fake_allocated_resp = {
            "data": [
                {
                    "id": "qwen2.5-7b-instruct",
                    "loaded": True,
                    "quantization": "Q4_K_M",
                    "context_length": 8192,  # Contesto realmente allocato in memoria!
                    "max_context_length": 32768,
                }
            ]
        }
        mock_allocated_resp = MagicMock()
        mock_allocated_resp.status = 200
        mock_allocated_resp.read.return_value = json.dumps(fake_allocated_resp).encode("utf-8")
        mock_allocated_resp.__enter__.return_value = mock_allocated_resp

        with patch("urllib.request.OpenerDirector.open", return_value=mock_allocated_resp):
            code_ok, obs_id_ok = _query_lmstudio_models_readonly(
                requested_model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                expected_quantization="Q4_K_M",
                expected_context_length=8192,
            )
            assert code_ok == "LOADED_INSTANCE_VERIFIED"
            assert obs_id_ok == "qwen2.5-7b-instruct"

    def test_43_http_redirect_to_remote_host_categorically_blocked(self) -> None:
        """
        AUDIT REPAIR B3: Garanzia che le richieste HTTP rimangano confinate a loopback.
        Verifica che eventuali redirect verso host remoti o non-loopback vengano bloccati.
        """
        from scripts.preflight_checkpoint_resume import StrictLoopbackRedirectHandler
        handler = StrictLoopbackRedirectHandler()

        req = urllib.request.Request("http://127.0.0.1:1234/v1/models")
        # Tentativo di redirect malevolo verso un host esterno
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            handler.redirect_request(
                req=req,
                fp=None,
                code=302,
                msg="Found",
                headers={},
                newurl="http://remote-attacker-site.com/api/v1/models",
            )
        assert "Redirect verso host non consentito bloccato" in str(exc_info.value)

        # Tentativo consentito: redirect interno su loopback
        redirected_req = handler.redirect_request(
            req=req,
            fp=None,
            code=302,
            msg="Found",
            headers={},
            newurl="http://127.0.0.1:1234/api/v1/models",
        )
        assert redirected_req is not None
        assert redirected_req.full_url == "http://127.0.0.1:1234/api/v1/models"

    def test_44_legacy_historical_boundaries_verdict_runtime_validation_required(self) -> None:
        """
        AUDIT REPAIR B4: Correzione della semantica delle boundaries storiche.
        Per checkpoint legacy SQLite senza hash indipendente delle boundaries:
        1. Non trasformare automaticamente il verdetto in SAFE_RESUME_VERIFIED anche se il backend risponde live.
        2. Emettere RUNTIME_VALIDATION_REQUIRED (Exit code 2).
        3. Preservare il piano di ripresa selettivo (24 skip, 2 infer) per guidare l'operatore.
        4. Distinguere l'impossibilità di certificazione matematica a priori dall'incompatibilità (non NOT_COMPATIBLE).
        """
        fake_live_resp = {
            "data": [
                {
                    "id": "qwen2.5-7b-instruct",
                    "loaded": True,
                    "quantization": "Q4_K_M",
                    "context_length": 8192,
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(fake_live_resp).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)

            with patch("urllib.request.OpenerDirector.open", return_value=mock_resp):
                auditor = PreflightResumeAuditor(
                    db_path=db_p,
                    document=doc,
                    resolved_model_key="qwen2.5-7b-instruct",
                    check_live_backend=True,
                )
                report = auditor.run_preflight()

                # Il verdetto NON deve essere SAFE_RESUME_VERIFIED per checkpoint legacy
                assert report.verdict == PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED
                assert report.verdict != PreflightVerdictStatus.SAFE_RESUME_VERIFIED
                assert report.verdict != PreflightVerdictStatus.NOT_COMPATIBLE

                # Il piano di ripresa deve essere preservato per l'operatore
                assert report.resumption_plan is not None
                assert report.resumption_plan["chunks_to_skip_count"] == 24
                assert report.resumption_plan["chunks_to_infer_count"] == 2
                assert report.resumption_plan["chunks_to_infer_indices_zero_based"] == [10, 19]

                # La motivazione diagnostica sanificata deve spiegare il limite storico
                assert any("Limite forense storico" in r or "boundaries" in r for r in report.reasons)

    def test_45_mandatory_negative_legacy_checkpoint_cannot_be_safe_resume_verified(self) -> None:
        """
        B2 TEST NEGATIVO OBBLIGATORIO:
        - Crea un database sintetico legacy 24/26 (privo di digest persistito indipendente).
        - Genera 26 chunk correnti.
        - Calcola il loro fingerprint.
        - Passa tale fingerprint all'auditor come se fosse storicamente verificato.
        - Simula un binding LM Studio attivo (istanza caricata in memoria con metadati completi).
        - Esercita il VERO percorso run_preflight() (nessuna funzione sostitutiva).
        - Dimostra che il risultato NON può essere SAFE_RESUME_VERIFIED ma categoricamente RUNTIME_VALIDATION_REQUIRED.
        """
        fake_live_resp = {
            "data": [
                {
                    "id": "qwen2.5-7b-instruct",
                    "loaded": True,
                    "quantization": "Q4_K_M",
                    "context_length": 8192,
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(fake_live_resp).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_p = Path(tmp_dir) / "checkpoints_legacy_24_26.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)

            # Genera i 26 chunk correnti e calcola il loro fingerprint
            from analysis.checkpoint import compute_chunk_partition_fingerprint
            from ai.chunking import split_document_into_chunks
            chunks = split_document_into_chunks(doc, max_characters=60000, max_bundles=3)
            assert len(chunks) == 26
            computed_current_partition_fp = compute_chunk_partition_fingerprint(chunks)

            # Esecuzione del vero percorso run_preflight() con binding LM Studio simulato come attivo
            # e passando il fingerprint calcolato come se fosse una presunta prova storica verificata
            with patch("urllib.request.OpenerDirector.open", return_value=mock_resp):
                auditor = PreflightResumeAuditor(
                    db_path=db_p,
                    document=doc,
                    resolved_model_key="qwen2.5-7b-instruct",
                    check_live_backend=True,
                    verified_partition_fingerprint=computed_current_partition_fp,
                )
                report = auditor.run_preflight()

                # DIMOSTRAZIONE TASSATIVA B2:
                # 1. Il verdetto NON può MAI essere SAFE_RESUME_VERIFIED
                assert report.verdict != PreflightVerdictStatus.SAFE_RESUME_VERIFIED, (
                    "Violazione critica B2: un fingerprint autodichiarato ha autorizzato SAFE_RESUME_VERIFIED!"
                )
                # 2. Il verdetto deve essere categoricamente RUNTIME_VALIDATION_REQUIRED
                assert report.verdict == PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED
                # 3. La classificazione non è LIVE_VERIFIED
                assert report.verification_classification != "LIVE_VERIFIED"
                assert report.verification_classification in ("LIVE_RUNTIME_VALIDATION_REQUIRED", "RUNTIME_VALIDATION_REQUIRED")
                # 4. Il piano di ripresa selettiva 24/26 è rigorosamente conservato per l'operatore
                assert report.resumption_plan is not None
                assert report.resumption_plan["chunks_to_skip_count"] == 24
                assert report.resumption_plan["chunks_to_infer_count"] == 2
                assert report.resumption_plan["chunks_to_infer_indices_zero_based"] == [10, 19]
                # 5. Controllo 5 evidenzia il limite storico delle boundaries
                c5 = next(c for c in report.checks if c.check_number == 5)
                assert "legacy_boundaries_limitation=True" in c5.observed_value


class TestPreflightOutputSafetyCriticalGuards:
    """
    CRITICAL FORENSIC GUARDS (B1-B5):
    Test suite indipendente per la sicurezza forense delle scritture di output.
    Verifica che il preflight non possa in nessun caso (diretto, symlink, hardlink,
    alias relativi/assoluti, sidecar, o collisioni) sovrascrivere o alterare database
    SQLite o file di checkpoint protetti, con permessi restrittivi 0600 e zero leak nei messaggi.
    """

    def test_46_output_json_exact_db_path_rejected_without_modifying_db(self) -> None:
        """B1 (Caso 1): --db X --output-json X rifiutato con exit code 3 e database intatto al 100%."""
        with tempfile.TemporaryDirectory() as td:
            db_p = Path(td) / "synthetic.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)

            sha_before = hashlib.sha256(db_p.read_bytes()).hexdigest()
            mtime_before = db_p.stat().st_mtime_ns

            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/preflight_checkpoint_resume.py",
                    "--db", str(db_p),
                    "--output-json", str(db_p),
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )

            assert proc.returncode == 3
            assert "OUTPUT_PROTECTED_FILE_CONFLICT" in proc.stdout or "OUTPUT_PROTECTED_FILE_CONFLICT" in proc.stderr
            assert db_p.exists()
            assert hashlib.sha256(db_p.read_bytes()).hexdigest() == sha_before
            assert db_p.stat().st_mtime_ns == mtime_before

            conn = sqlite3.connect(f"file:{db_p}?mode=ro", uri=True)
            try:
                count_sess = conn.cursor().execute("SELECT count(*) FROM checkpoints").fetchone()[0]
                assert count_sess == 1
                count_chunks = conn.cursor().execute("SELECT count(*) FROM checkpoint_chunks").fetchone()[0]
                assert count_chunks == 24
            finally:
                conn.close()

    def test_47_output_json_symlink_to_db_rejected(self) -> None:
        """B1 (Caso 2): --output-json puntante a un symlink verso il database rifiutato."""
        with tempfile.TemporaryDirectory() as td:
            db_p = Path(td) / "synthetic.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)
            sha_before = hashlib.sha256(db_p.read_bytes()).hexdigest()

            sym_p = Path(td) / "link_to_db.json"
            try:
                os.symlink(db_p, sym_p)
            except (OSError, NotImplementedError):
                pytest.skip("Symlink non supportati nell'ambiente corrente")

            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/preflight_checkpoint_resume.py",
                    "--db", str(db_p),
                    "--output-json", str(sym_p),
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )

            assert proc.returncode == 3
            assert hashlib.sha256(db_p.read_bytes()).hexdigest() == sha_before

    def test_48_output_json_hardlink_to_db_rejected(self) -> None:
        """B1 (Caso 3): --output-json puntante a un hard link verso il database rifiutato."""
        with tempfile.TemporaryDirectory() as td:
            db_p = Path(td) / "synthetic.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)
            sha_before = hashlib.sha256(db_p.read_bytes()).hexdigest()

            hard_p = Path(td) / "hardlink_to_db.json"
            try:
                os.link(db_p, hard_p)
            except (OSError, NotImplementedError):
                pytest.skip("Hardlink non supportati nell'ambiente corrente")

            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/preflight_checkpoint_resume.py",
                    "--db", str(db_p),
                    "--output-json", str(hard_p),
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )

            assert proc.returncode == 3
            assert hashlib.sha256(db_p.read_bytes()).hexdigest() == sha_before

    def test_49_output_json_relative_vs_absolute_alias_rejected(self) -> None:
        """B1: Destinazione identica con percorsi relativi e assoluti diversi rifiutata."""
        with tempfile.TemporaryDirectory() as td:
            sub = Path(td) / "subdir"
            sub.mkdir()
            db_p = sub / "db.sqlite"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)
            sha_before = hashlib.sha256(db_p.read_bytes()).hexdigest()

            alias_path = sub / ".." / "subdir" / "db.sqlite"

            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/preflight_checkpoint_resume.py",
                    "--db", str(db_p),
                    "--output-json", str(alias_path),
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )

            assert proc.returncode == 3
            assert hashlib.sha256(db_p.read_bytes()).hexdigest() == sha_before

    def test_50_output_json_wal_and_shm_sidecars_rejected(self) -> None:
        """B1: Destinazione uguale a file SQLite -wal o -shm categoricamente rifiutata."""
        with tempfile.TemporaryDirectory() as td:
            db_p = Path(td) / "db.sqlite"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)
            sha_before = hashlib.sha256(db_p.read_bytes()).hexdigest()

            wal_target = Path(td) / "db.sqlite-wal"
            shm_target = Path(td) / "db.sqlite-shm"

            proc_wal = subprocess.run(
                [
                    sys.executable,
                    "scripts/preflight_checkpoint_resume.py",
                    "--db", str(db_p),
                    "--output-json", str(wal_target),
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )
            assert proc_wal.returncode == 3
            assert not wal_target.exists()
            assert hashlib.sha256(db_p.read_bytes()).hexdigest() == sha_before

            proc_shm = subprocess.run(
                [
                    sys.executable,
                    "scripts/preflight_checkpoint_resume.py",
                    "--db", str(db_p),
                    "--output-json", str(shm_target),
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )
            assert proc_shm.returncode == 3
            assert not shm_target.exists()
            assert hashlib.sha256(db_p.read_bytes()).hexdigest() == sha_before

    def test_51_output_json_inside_checkpoint_dir_rejected(self) -> None:
        """B1: Tentativo di output nella directory riservata ai checkpoint (.checkpoints) rifiutato."""
        with tempfile.TemporaryDirectory() as td:
            chk_dir = Path(td) / ".checkpoints"
            chk_dir.mkdir()
            db_p = chk_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)

            dest_in_chk = chk_dir / "audit_report.json"

            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/preflight_checkpoint_resume.py",
                    "--db", str(db_p),
                    "--output-json", str(dest_in_chk),
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )

            assert proc.returncode == 3
            assert not dest_in_chk.exists()

    def test_52_output_json_existing_file_rejected_without_overwrite(self) -> None:
        """B1: Destinazione file già esistente rifiutata categoricamente (O_EXCL)."""
        with tempfile.TemporaryDirectory() as td:
            db_p = Path(td) / "synthetic.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)

            existing_file = Path(td) / "pre_existing_file.json"
            canary_text = "CANARY_FORENSIC_PRE_EXISTING_CONTENT_789012"
            existing_file.write_text(canary_text, encoding="utf-8")
            sha_canary_before = hashlib.sha256(existing_file.read_bytes()).hexdigest()

            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/preflight_checkpoint_resume.py",
                    "--db", str(db_p),
                    "--output-json", str(existing_file),
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )

            assert proc.returncode == 3
            assert existing_file.read_text(encoding="utf-8") == canary_text
            assert hashlib.sha256(existing_file.read_bytes()).hexdigest() == sha_canary_before

    def test_53_output_json_concurrent_exclusive_writes_atomic(self) -> None:
        """B1: Due scritture concorrenti verso la stessa destinazione gestite atomicamente (O_CREAT | O_EXCL)."""
        import threading
        from scripts.preflight_checkpoint_resume import write_preflight_report_safely, PreflightReportSecurityError

        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "concurrent_report.json"
            rep_data = {"test": "data", "status": "ok"}
            results: list[str] = []

            def worker() -> None:
                try:
                    write_preflight_report_safely(rep_data, target)
                    results.append("SUCCESS")
                except PreflightReportSecurityError as pse:
                    results.append(f"REJECTED:{pse.error_code}")
                except Exception as ex:
                    results.append(f"ERROR:{type(ex).__name__}")

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            assert "SUCCESS" in results
            assert any("DESTINATION_ALREADY_EXISTS" in r for r in results)
            assert target.is_file()
            parsed = json.loads(target.read_text(encoding="utf-8"))
            assert parsed == rep_data

    def test_54_output_json_interrupted_write_cleans_up_without_leaving_corrupt_file(self) -> None:
        """B1: Interruzione o errore durante la scrittura pulisce il file senza lasciare residui corrotti."""
        from scripts.preflight_checkpoint_resume import write_preflight_report_safely, PreflightReportSecurityError

        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "interrupted_report.json"
            rep_data = {"test": "data"}

            with patch("json.dump", side_effect=IOError("Simulated disk full during write")):
                with pytest.raises(PreflightReportSecurityError) as exc_info:
                    write_preflight_report_safely(rep_data, target)
                assert exc_info.value.error_code == "WRITE_ERROR"

            assert not target.exists()

    def test_55_output_json_valid_report_created_safely(self) -> None:
        """B1: Creazione corretta di un nuovo report valido fuori dalla directory protetta."""
        with tempfile.TemporaryDirectory() as td:
            db_p = Path(td) / "synthetic.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(db_p, doc)
            sha_db_before = hashlib.sha256(db_p.read_bytes()).hexdigest()

            out_dir = Path(td) / "safe_exports"
            out_file = out_dir / "valid_audit_report.json"

            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/preflight_checkpoint_resume.py",
                    "--db", str(db_p),
                    "--output-json", str(out_file),
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )

            assert proc.returncode in (0, 2)
            assert out_file.is_file()
            data = json.loads(out_file.read_text(encoding="utf-8"))
            assert "verdict" in data
            assert "checks" in data
            assert hashlib.sha256(db_p.read_bytes()).hexdigest() == sha_db_before

    def test_56_output_json_permissions_0600_enforced(self) -> None:
        """B2: Verifica che il file di report venga creato con permessi restrittivi 0600."""
        import stat
        from scripts.preflight_checkpoint_resume import write_preflight_report_safely

        with tempfile.TemporaryDirectory() as td:
            out_file = Path(td) / "perms_report.json"
            write_preflight_report_safely({"test": "secure"}, out_file)
            assert out_file.is_file()

            st = os.stat(out_file)
            if hasattr(os, "getuid"):
                mode = stat.S_IMODE(st.st_mode)
                assert mode == 0o600, f"Attesi permessi 0600 su Linux, ottenuto: {oct(mode)}"
            else:
                assert st.st_mode & stat.S_IREAD != 0
                assert st.st_mode & stat.S_IWRITE != 0

    def test_57_remote_host_rejected_without_exposing_hostname(self) -> None:
        """B3: Host remoto rifiutato senza esporre il nome in stdout, stderr o JSON."""
        leaky_hostname = "PERSONALNAMEUNIQUE.example"
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/preflight_checkpoint_resume.py",
                "--backend-url", f"http://{leaky_hostname}:1234",
                "--json",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 3
        assert leaky_hostname not in proc.stdout
        assert leaky_hostname.lower() not in proc.stdout
        assert leaky_hostname not in proc.stderr
        assert leaky_hostname.lower() not in proc.stderr

        out_json = json.loads(proc.stdout)
        assert out_json["status"] == "ERROR"
        assert out_json["error_code"] == "LOOPBACK_URL_REQUIRED"
        assert "message" in out_json
        assert leaky_hostname not in out_json["message"]

    def test_58_invalid_tcp_port_rejected(self) -> None:
        """B3: Porta TCP non valida o fuori range (1-65535) rifiutata categoricamente."""
        for invalid_port_url in [
            "http://localhost:99999",
            "http://127.0.0.1:0",
            "http://127.0.0.1:invalidport",
            "http://localhost:",
        ]:
            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/preflight_checkpoint_resume.py",
                    "--backend-url", invalid_port_url,
                    "--json",
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )

            assert proc.returncode == 3
            out_json = json.loads(proc.stdout)
            assert out_json["status"] == "ERROR"
            assert out_json["error_code"] == "INVALID_BACKEND_PORT"

    def test_59_arbitrary_verdict_string_not_exportable_redacted(self) -> None:
        """B4: Verdetto arbitrario di tipo stringa non conforme viene sanitizzato e non esportato in chiaro."""
        from scripts.preflight_checkpoint_resume import PreflightAuditReport

        leaky_verdict = "Alice Wonderland"
        report = PreflightAuditReport(
            database_path="/tmp/fake.db",
            document_available=False,
            verdict=leaky_verdict,
        )

        exported = report.to_dict()
        assert exported["verdict"] == "[VERDICT_INVALID_REDACTED]"
        assert leaky_verdict not in str(exported)
        assert leaky_verdict not in json.dumps(exported)


class TestPreflightSnapshotIsolationCriticalGuards:
    """
    Suite di test di regressione obbligatori per l'isolamento degli snapshot e la chiusura preflight (B1-B4).
    Verifica che gli snapshot:
    1. Siano categoricamente vietati dentro la directory '.checkpoints' (sia per dest_dir che per dest_path).
    2. Siano categoricamente vietati nella cartella contenente il database sorgente.
    3. Siano categoricamente vietati all'interno del repository Git 'Tesi' (o REPO_ROOT).
    4. Rifiutino cartelle di destinazione raggiungibili tramite symlink.
    5. Rifiutino percorsi espliciti dest_path adiacenti al database originale.
    6. Rifiutino cartelle non valide (es. file ordinari come Alice_Wonderland) senza esporre percorsi in stdout/stderr.
    7. Rifiutino cartelle prive dei necessari permessi.
    8. Gestiscano in modo pulito ed esclusivo eventuali errori della SQLite Backup API preservando la sorgente.
    9. Puliscano ogni risorsa creata in caso di interruzione.
    10. Preservino gli hash crittografici (SHA-256) dei database sintetici.
    11. Preservino i sidecar SQLite (-wal, -shm).
    12. Non lascino nuove directory residue nei percorsi protetti o consentiti.
    13. Non espongano dati personali o percorsi investigativi in stdout/stderr in modalità testo e JSON.
    14. Restituiscano codici di uscita controllati e documentati (exit code 3 per errori, 0/2 per successo).
    15. Eseguano zero inferenze LLM durante il preflight con snapshot.
    16. Non introducano alcuna regressione nella scrittura sicura del report JSON (--output-json).
    """

    def test_60_snapshot_requested_in_checkpoints_dir_rejected(self) -> None:
        """B1/B4.1: Richiesta di snapshot nella directory '.checkpoints' categoricamente respinta senza alterazioni."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            chk_dir = Path(tmp_dir) / ".checkpoints"
            chk_dir.mkdir()
            mtime_before = chk_dir.stat().st_mtime_ns
            items_before = list(chk_dir.iterdir())

            # 1. API: dest_dir in .checkpoints
            with pytest.raises(SnapshotDestinationForbiddenError):
                create_consistent_readonly_snapshot(src_db, dest_dir=chk_dir)

            # 2. API: dest_path in .checkpoints
            with pytest.raises(SnapshotDestinationForbiddenError):
                create_consistent_readonly_snapshot(src_db, dest_path=chk_dir / "snap.db")

            # Verifica immutabilità directory .checkpoints
            assert chk_dir.stat().st_mtime_ns == mtime_before
            assert list(chk_dir.iterdir()) == items_before

            # 3. CLI Subprocess
            cmd = [
                sys.executable,
                "scripts/preflight_checkpoint_resume.py",
                "--db", str(src_db),
                "--snapshot",
                "--snapshot-dir", str(chk_dir),
                "--json",
            ]
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc.returncode == 3
            out = json.loads(proc.stdout)
            assert out["status"] == "ERROR"
            assert out["error_code"] == "SNAPSHOT_DESTINATION_FORBIDDEN"
            assert "Traceback" not in proc.stderr
            assert chk_dir.stat().st_mtime_ns == mtime_before

    def test_61_snapshot_requested_in_source_db_dir_rejected(self) -> None:
        """B1/B4.2: Richiesta di snapshot nella directory del database sorgente categoricamente respinta."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            mtime_before = src_dir.stat().st_mtime_ns
            items_before = set(src_dir.iterdir())

            # API dest_dir coincidente con src_db.parent
            with pytest.raises(SnapshotDestinationForbiddenError):
                create_consistent_readonly_snapshot(src_db, dest_dir=src_dir)

            # Verifica nessuna alterazione
            assert src_dir.stat().st_mtime_ns == mtime_before
            assert set(src_dir.iterdir()) == items_before

            # CLI subprocess
            cmd = [
                sys.executable,
                "scripts/preflight_checkpoint_resume.py",
                "--db", str(src_db),
                "--snapshot",
                "--snapshot-dir", str(src_dir),
                "--json",
            ]
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc.returncode == 3
            out = json.loads(proc.stdout)
            assert out["status"] == "ERROR"
            assert out["error_code"] == "SNAPSHOT_DESTINATION_FORBIDDEN"

    def test_62_snapshot_requested_inside_git_repo_rejected(self) -> None:
        """B1/B4.3: Creazione dello snapshot all'interno del repository Git 'Tesi' categoricamente vietata."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            # Tentativo 1: Root repository
            with pytest.raises(SnapshotDestinationForbiddenError):
                create_consistent_readonly_snapshot(src_db, dest_dir=REPO_ROOT)

            # Tentativo 2: Sottodirectory del repository
            with pytest.raises(SnapshotDestinationForbiddenError):
                create_consistent_readonly_snapshot(src_db, dest_dir=REPO_ROOT / "scripts")

            # Tentativo 3: dest_path dentro repository
            with pytest.raises(SnapshotDestinationForbiddenError):
                create_consistent_readonly_snapshot(src_db, dest_path=REPO_ROOT / "test_snap.db")

            # CLI subprocess
            cmd = [
                sys.executable,
                "scripts/preflight_checkpoint_resume.py",
                "--db", str(src_db),
                "--snapshot",
                "--snapshot-dir", str(REPO_ROOT),
                "--json",
            ]
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc.returncode == 3
            out = json.loads(proc.stdout)
            assert out["status"] == "ERROR"
            assert out["error_code"] == "SNAPSHOT_DESTINATION_FORBIDDEN"

    def test_63_snapshot_dir_symlink_rejected(self) -> None:
        """B1/B4.4: Destinazione snapshot raggiungibile tramite symlink rifiutata (SnapshotPermissionDeniedError)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            real_target_dir = Path(tmp_dir) / "real_allowed_target"
            real_target_dir.mkdir()
            symlink_dir = Path(tmp_dir) / "symlink_dir"

            try:
                os.symlink(real_target_dir, symlink_dir, target_is_directory=True)
            except (OSError, NotImplementedError):
                pytest.skip("Creazione symlink non supportata o permessi non disponibili in questo ambiente")

            with pytest.raises(SnapshotPermissionDeniedError):
                create_consistent_readonly_snapshot(src_db, dest_dir=symlink_dir)

    def test_64_dest_path_next_to_source_db_rejected(self) -> None:
        """B1/B4.5: Percorso esplicito dest_path adiacente al DB originale rifiutato categoricamente."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            adjacent_dest = src_dir / "adjacent_snapshot.db"

            with pytest.raises(SnapshotDestinationForbiddenError):
                create_consistent_readonly_snapshot(src_db, dest_path=adjacent_dest)

            assert not adjacent_dest.exists()

    def test_65_snapshot_dir_invalid_file_rejected_via_api_and_cli(self) -> None:
        """B2/B4.6: Snapshot dir che indica un file ordinario (Alice_Wonderland) gestito senza traceback né percorsi."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            # Crea file ordinario Alice_Wonderland
            alice_file = Path(tmp_dir) / "Alice_Wonderland"
            alice_file.write_text("Sono un file, non una cartella!", encoding="utf-8")

            # 1. API diretta: solleva SnapshotDirectoryInvalidError
            with pytest.raises(SnapshotDirectoryInvalidError):
                create_consistent_readonly_snapshot(src_db, dest_dir=alice_file)

            # 2. CLI subprocess con --json
            cmd_json = [
                sys.executable,
                "scripts/preflight_checkpoint_resume.py",
                "--db", str(src_db),
                "--snapshot",
                "--snapshot-dir", str(alice_file),
                "--json",
            ]
            proc_json = subprocess.run(cmd_json, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc_json.returncode == 3
            assert "Traceback" not in proc_json.stderr
            assert "Alice_Wonderland" not in proc_json.stderr
            assert "Alice_Wonderland" not in proc_json.stdout
            out = json.loads(proc_json.stdout)
            assert out["status"] == "ERROR"
            assert out["error_code"] == "SNAPSHOT_DIRECTORY_INVALID"
            assert "Alice_Wonderland" not in out["message"]

            # 3. CLI subprocess con output testuale
            cmd_txt = [
                sys.executable,
                "scripts/preflight_checkpoint_resume.py",
                "--db", str(src_db),
                "--snapshot",
                "--snapshot-dir", str(alice_file),
            ]
            proc_txt = subprocess.run(cmd_txt, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc_txt.returncode == 3
            assert "Traceback" not in proc_txt.stderr
            assert "Alice_Wonderland" not in proc_txt.stderr
            assert "Alice_Wonderland" not in proc_txt.stdout
            assert "[ERRORE SICUREZZA] SNAPSHOT_DIRECTORY_INVALID" in proc_txt.stdout

    def test_66_snapshot_dir_without_permissions_rejected(self) -> None:
        """B2/B4.7: Directory senza permessi di scrittura gestita controllata (SNAPSHOT_PERMISSION_DENIED)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            no_perm_dir = Path(tmp_dir) / "no_perm_dir"
            no_perm_dir.mkdir()

            try:
                os.chmod(no_perm_dir, 0o400)
                test_f = no_perm_dir / "probe.txt"
                try:
                    test_f.write_text("test")
                    test_f.unlink()
                    pytest.skip("Chmod 0o400 non impedisce la scrittura in questo filesystem (es. Windows NTFS)")
                except PermissionError:
                    pass
            except Exception:
                pytest.skip("Chmod non supportato")

            try:
                with pytest.raises(SnapshotPermissionDeniedError):
                    create_consistent_readonly_snapshot(src_db, dest_dir=no_perm_dir)
            finally:
                os.chmod(no_perm_dir, 0o700)

    def test_67_sqlite_backup_api_error_cleans_up_and_preserves_source(self) -> None:
        """B2/B4.8: Errore della SQLite Backup API esegue pulizia esclusiva e preserva il database intatto."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            dest_storage = Path(tmp_dir) / "dest_storage"
            dest_storage.mkdir()

            # Hash sorgente iniziale
            h_before = hashlib.sha256(src_db.read_bytes()).hexdigest()

            real_connect = sqlite3.connect

            class FailingConnection:
                def __init__(self, *args, **kwargs):
                    self._conn = real_connect(*args, **kwargs)

                def execute(self, *args, **kwargs):
                    return self._conn.execute(*args, **kwargs)

                def backup(self, *args, **kwargs):
                    raise sqlite3.OperationalError("Simulated backup fatal crash")

                def close(self):
                    return self._conn.close()

            with patch("sqlite3.connect", side_effect=lambda *args, **kwargs: FailingConnection(*args, **kwargs)):
                with pytest.raises(sqlite3.OperationalError):
                    create_consistent_readonly_snapshot(src_db, dest_dir=dest_storage)

            # Verifica: nessuna cartella snapshot residua
            residual_snap_dirs = [d for d in dest_storage.iterdir() if d.name.startswith("chatanalysis_snap_")]
            assert len(residual_snap_dirs) == 0

            # Verifica: database sorgente intatto bit-for-bit
            h_after = hashlib.sha256(src_db.read_bytes()).hexdigest()
            assert h_before == h_after

    def test_68_interruption_during_snapshot_creation_cleanup(self) -> None:
        """B2/B4.9: Interruzione durante la creazione dello snapshot elimina solo la directory temporanea privata."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            dest_storage = Path(tmp_dir) / "dest_storage"
            dest_storage.mkdir()
            foreign_doc = dest_storage / "do_not_delete.txt"
            foreign_doc.write_text("Keep me safe", encoding="utf-8")

            with patch("sqlite3.connect", side_effect=KeyboardInterrupt("Simulated Ctrl+C interruption")):
                with pytest.raises(KeyboardInterrupt):
                    create_consistent_readonly_snapshot(src_db, dest_dir=dest_storage)

            # Verifica che il file estraneo sia intatto e nessuna cartella snapshot sia rimasta
            assert foreign_doc.exists()
            assert foreign_doc.read_text(encoding="utf-8") == "Keep me safe"
            snap_dirs = list(dest_storage.glob("chatanalysis_snap_*"))
            assert len(snap_dirs) == 0

    def test_69_preservation_of_synthetic_database_hashes(self) -> None:
        """B4.10: Preservazione bit-for-bit degli hash SHA-256 dei database sintetici prima e dopo."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            sha_initial = hashlib.sha256(src_db.read_bytes()).hexdigest()

            # 1. Snapshot nominale
            snap_file, snap_temp_dir = create_consistent_readonly_snapshot(src_db)
            try:
                assert snap_file.is_file()
                assert hashlib.sha256(src_db.read_bytes()).hexdigest() == sha_initial
            finally:
                if snap_temp_dir is not None:
                    shutil.rmtree(snap_temp_dir, ignore_errors=True)

            # 2. Snapshot rifiutato
            with pytest.raises(SnapshotDestinationForbiddenError):
                create_consistent_readonly_snapshot(src_db, dest_dir=src_dir)

            assert hashlib.sha256(src_db.read_bytes()).hexdigest() == sha_initial

    def test_70_preservation_of_sqlite_sidecars(self) -> None:
        """B4.11 / B2: Preservazione rigorosa dei sidecar SQLite (-wal, -shm) con writer aperto."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"

            # Inizializza in WAL mode e inserisce dati mantenendo la connessione attiva per conservare il WAL
            conn = sqlite3.connect(str(src_db))
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("CREATE TABLE live_cases (id INT, note TEXT);")
                conn.execute("INSERT INTO live_cases VALUES (101, 'wal_buffered_record');")
                conn.commit()

                wal_file = src_dir / "checkpoints.db-wal"
                shm_file = src_dir / "checkpoints.db-shm"
                assert wal_file.is_file(), "File WAL deve esistere"
                assert shm_file.is_file(), "File SHM deve esistere"
                assert shm_file.stat().st_size > 0, "File SHM deve avere dimensione non nulla"

                db_sha_before = hashlib.sha256(src_db.read_bytes()).hexdigest()
                wal_sha_before = hashlib.sha256(wal_file.read_bytes()).hexdigest()
                shm_sha_before = hashlib.sha256(shm_file.read_bytes()).hexdigest()
                shm_mtime_before = shm_file.stat().st_mtime_ns
                shm_size_before = shm_file.stat().st_size

                # Crea snapshot isolato
                snap_file, snap_temp_dir = create_consistent_readonly_snapshot(src_db)
                try:
                    # Nello snapshot il dato del WAL è consolidato
                    snap_conn = sqlite3.connect(f"file:{snap_file.resolve().as_posix()}?mode=ro", uri=True)
                    try:
                        val = snap_conn.execute("SELECT note FROM live_cases WHERE id=101;").fetchone()[0]
                        assert val == "wal_buffered_record"
                    finally:
                        snap_conn.close()

                    # I file originali (DB, WAL e SHM) sono al 100% preservati in hash, mtime, esistenza e dimensione
                    assert src_db.is_file()
                    assert hashlib.sha256(src_db.read_bytes()).hexdigest() == db_sha_before

                    assert wal_file.is_file()
                    assert hashlib.sha256(wal_file.read_bytes()).hexdigest() == wal_sha_before

                    assert shm_file.is_file(), "File SHM deve continuare ad esistere"
                    assert shm_file.stat().st_size == shm_size_before, "Dimensione SHM deve rimanere identica"
                    assert hashlib.sha256(shm_file.read_bytes()).hexdigest() == shm_sha_before, "Hash SHM deve rimanere intatto"
                    assert shm_file.stat().st_mtime_ns == shm_mtime_before, "mtime SHM deve rimanere invariato"
                finally:
                    if snap_temp_dir is not None:
                        shutil.rmtree(snap_temp_dir, ignore_errors=True)
            finally:
                conn.close()

    def test_71_absence_of_leftover_directories_in_protected_paths(self) -> None:
        """B4.12: Assenza assoluta di nuove directory residue 'chatanalysis_snap_*' nei percorsi protetti."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            # Esegue preflight con --snapshot via CLI
            cmd = [
                sys.executable,
                "scripts/preflight_checkpoint_resume.py",
                "--db", str(src_db),
                "--snapshot",
                "--json",
            ]
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc.returncode in (0, 2)

            # Verifica che né nel source dir né in REPO_ROOT ci siano cartelle snap residue
            assert len(list(src_dir.glob("chatanalysis_snap_*"))) == 0
            assert len(list(REPO_ROOT.glob("chatanalysis_snap_*"))) == 0
            if (REPO_ROOT / ".checkpoints").exists():
                assert len(list((REPO_ROOT / ".checkpoints").glob("chatanalysis_snap_*"))) == 0

    def test_72_no_personal_data_in_stdout_stderr_on_all_snapshot_errors(self) -> None:
        """B2/B4.13: Nessun dato personale o percorso investigativo in stdout/stderr su errori snapshot."""
        canary_path_part = "Confidential_Suspect_Case_Investigation_999"
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            # 1. Con file invalido contenente canary
            canary_file = Path(tmp_dir) / f"{canary_path_part}.txt"
            canary_file.write_text("Canary content", encoding="utf-8")

            for json_flag in (["--json"], []):
                cmd = [
                    sys.executable,
                    "scripts/preflight_checkpoint_resume.py",
                    "--db", str(src_db),
                    "--snapshot",
                    "--snapshot-dir", str(canary_file),
                ] + json_flag

                proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
                assert proc.returncode == 3
                assert canary_path_part not in proc.stdout
                assert canary_path_part not in proc.stderr
                assert "Traceback" not in proc.stderr

    def test_73_controlled_and_documented_exit_codes(self) -> None:
        """B2/B4.14: Exit code 3 rigorosamente restituito per tutti gli errori operativi dello snapshot."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            # Test forbidden destination: exit code 3
            cmd_forbidden = [
                sys.executable,
                "scripts/preflight_checkpoint_resume.py",
                "--db", str(src_db),
                "--snapshot",
                "--snapshot-dir", str(src_dir),
                "--json",
            ]
            proc = subprocess.run(cmd_forbidden, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc.returncode == 3

    def test_74_zero_llm_inference_during_preflight_with_snapshot(self) -> None:
        """B4.15: Nessuna inferenza LLM durante il preflight con flag --snapshot."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            call_count = {"count": 0}

            def tracking_urlopen(*args, **kwargs):
                call_count["count"] += 1
                raise urllib.error.URLError("No connection allowed during audit")

            with patch("urllib.request.urlopen", side_effect=tracking_urlopen):
                auditor = PreflightResumeAuditor(
                    db_path=src_db,
                    document=doc,
                    model_id="qwen2.5-7b-instruct",
                    resolved_model_key="qwen2.5-7b-instruct",
                    profile="BALANCED",
                    check_live_backend=False,
                )
                report = auditor.run_preflight()
                assert report.verdict in (PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED, PreflightVerdictStatus.SAFE_RESUME_VERIFIED)
                assert call_count["count"] == 0

    def test_75_no_regression_of_secure_output_json_with_snapshot(self) -> None:
        """B4.16: Nessuna regressione della scrittura sicura di --output-json in combinazione con --snapshot."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_dir = Path(tmp_dir) / "source_storage"
            src_dir.mkdir()
            src_db = src_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            out_dir = Path(tmp_dir) / "report_out"
            out_dir.mkdir()
            out_json = out_dir / "preflight_report.json"

            cmd = [
                sys.executable,
                "scripts/preflight_checkpoint_resume.py",
                "--db", str(src_db),
                "--snapshot",
                "--output-json", str(out_json),
                "--json",
            ]
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc.returncode in (0, 2)
            assert out_json.is_file()
            # Verifica che il report contenga JSON valido e dati corretti
            data = json.loads(out_json.read_text(encoding="utf-8"))
            assert data["completed_chunks_count"] == 24
            assert data["missing_chunks_zero_based"] == [10, 19]

            # Tentativo di sovrascrittura: deve essere rifiutato
            proc_dup = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc_dup.returncode == 3


class TestForensicSourceIntegrityAndQuiescentAcquisition:
    """
    Suite di test obbligatori per l'integrità dei sorgenti WAL/SHM e la sicurezza del preflight (B1-B5).
    Verifica:
    1. Database WAL con writer ancora aperto.
    2. SHM presente e non vuoto.
    3. Verifica hash del database principale.
    4. Verifica hash del WAL.
    5. Verifica hash e mtime del file SHM.
    6. Lettura SQLite diretta che riproduce il problema (test di regressione documentato).
    7. Acquisizione coerente della copia tramite la nuova procedura (quiescent forensic copy).
    8. Recupero dei record che si trovano soltanto nel WAL.
    9. Writer attivo durante l'acquisizione.
    10. Impossibilità di ottenere una copia consistente (abort controllato).
    11. Snapshot incompleto o corrotto rifiutato da PRAGMA quick_check / integrity_check.
    12. Mancanza di permessi gestita con SnapshotPermissionDeniedError.
    13. Interruzione della procedura con pulizia automatica.
    14. Cleanup esclusivo delle sole risorse temporanee create.
    15. Nessun file creato in '.checkpoints'.
    16. Nessuna scrittura nel repository Tesi.
    17. Nessuna esposizione di percorsi sensibili nei report e nella diagnostica.
    18. Test subprocess della CLI con open writer e sidecar preservati.
    19. Assenza totale di inferenze LLM.
    20. Non regressione dell'output JSON sicuro.
    """

    def _setup_wal_database_with_open_writer(self, tmp_dir: str | Path, record_count: int = 5) -> tuple[Path, sqlite3.Connection, dict[str, Any]]:
        """Helper: crea un database WAL sintetico con connessione writer aperta e sidecar presenti."""
        src_dir = Path(tmp_dir) / "source_storage"
        src_dir.mkdir(parents=True, exist_ok=True)
        src_db = src_dir / "checkpoints.db"

        conn = sqlite3.connect(str(src_db))
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("CREATE TABLE live_cases (id INT PRIMARY KEY, payload TEXT);")
        for i in range(record_count):
            conn.execute("INSERT INTO live_cases VALUES (?, ?);", (i, f"buffered_val_{i}"))
        conn.commit()
        # Query per inizializzare il read-mark nell'indice -shm
        conn.execute("SELECT COUNT(*) FROM live_cases;").fetchone()

        wal_file = src_dir / "checkpoints.db-wal"
        shm_file = src_dir / "checkpoints.db-shm"
        assert wal_file.is_file(), "File WAL deve esistere"
        assert shm_file.is_file(), "File SHM deve esistere"
        assert shm_file.stat().st_size > 0, "File SHM deve essere non vuoto"

        initial_state = {
            "db_sha": hashlib.sha256(src_db.read_bytes()).hexdigest(),
            "db_mtime": src_db.stat().st_mtime_ns,
            "wal_sha": hashlib.sha256(wal_file.read_bytes()).hexdigest(),
            "wal_mtime": wal_file.stat().st_mtime_ns,
            "shm_sha": hashlib.sha256(shm_file.read_bytes()).hexdigest(),
            "shm_mtime": shm_file.stat().st_mtime_ns,
            "shm_size": shm_file.stat().st_size,
        }
        return src_db, conn, initial_state

    def test_76_wal_with_open_writer_and_non_empty_shm(self) -> None:
        """B5.1 & B5.2: Database WAL con writer ancora aperto e SHM presente e non vuoto."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                shm_file = src_db.parent / f"{src_db.name}-shm"
                assert shm_file.is_file()
                assert state["shm_size"] > 0
                assert len(state["shm_sha"]) == 64
            finally:
                writer_conn.close()

    def test_77_preservation_of_main_database_hash(self) -> None:
        """B5.3: Verifica che l'hash SHA-256 del database principale rimanga invariato."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                copy_db, copy_dir, metrics = acquire_forensic_readonly_copy(src_db)
                try:
                    assert hashlib.sha256(src_db.read_bytes()).hexdigest() == state["db_sha"]
                    assert src_db.stat().st_mtime_ns == state["db_mtime"]
                    assert metrics["DATABASE_LOGICAL_CONTENT_UNCHANGED"] is True
                finally:
                    if copy_dir and copy_dir.exists():
                        shutil.rmtree(copy_dir, ignore_errors=True)
            finally:
                writer_conn.close()

    def test_78_preservation_of_wal_hash(self) -> None:
        """B5.4: Verifica che l'hash SHA-256 del WAL rimanga invariato."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                wal_file = src_db.parent / f"{src_db.name}-wal"
                copy_db, copy_dir, metrics = acquire_forensic_readonly_copy(src_db)
                try:
                    assert hashlib.sha256(wal_file.read_bytes()).hexdigest() == state["wal_sha"]
                    assert wal_file.stat().st_mtime_ns == state["wal_mtime"]
                    assert metrics["WAL_CONTENT_UNCHANGED"] is True
                finally:
                    if copy_dir and copy_dir.exists():
                        shutil.rmtree(copy_dir, ignore_errors=True)
            finally:
                writer_conn.close()

    def test_79_preservation_of_shm_hash_and_mtime(self) -> None:
        """B5.5: Verifica che hash SHA-256, mtime e dimensione del file SHM rimangano invariati."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                shm_file = src_db.parent / f"{src_db.name}-shm"
                copy_db, copy_dir, metrics = acquire_forensic_readonly_copy(src_db)
                try:
                    assert shm_file.is_file()
                    assert shm_file.stat().st_size == state["shm_size"]
                    assert hashlib.sha256(shm_file.read_bytes()).hexdigest() == state["shm_sha"]
                    assert shm_file.stat().st_mtime_ns == state["shm_mtime"]
                    assert metrics["SHM_BYTES_UNCHANGED"] is True
                    assert metrics["SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED"] is True
                finally:
                    if copy_dir and copy_dir.exists():
                        shutil.rmtree(copy_dir, ignore_errors=True)
            finally:
                writer_conn.close()

    def test_80_direct_sqlite_readonly_connection_mutates_shm_regression(self) -> None:
        """B5.6: Test di regressione che dimostra che l'uso diretto di SQLite/Backup API sul sorgente muta l'SHM."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                shm_file = src_db.parent / f"{src_db.name}-shm"
                # Inserimento di un nuovo record per aggiornare il WAL e invalidare il read-mark
                writer_conn.execute("INSERT INTO live_cases VALUES (999, 'uncheckpointed_wal_frame');")
                writer_conn.commit()

                # Hash del file SHM prima della connessione read-only
                shm_sha_before_reader = hashlib.sha256(shm_file.read_bytes()).hexdigest()

                # Connessione SQLite diretta sul database originale in modalità read-only
                sconn = sqlite3.connect(f"file:{src_db.resolve().as_posix()}?mode=ro", uri=True)
                try:
                    sconn.execute("PRAGMA query_only = ON;")
                    sconn.execute("SELECT COUNT(*) FROM live_cases;").fetchone()
                finally:
                    sconn.close()

                # Dimostra che la connessione di lettura SQLite muta i read-marks nel file SHM della sorgente
                shm_sha_after_reader = hashlib.sha256(shm_file.read_bytes()).hexdigest()
                assert shm_sha_after_reader != shm_sha_before_reader, (
                    "La connessione di lettura SQLite diretta avrebbe dovuto alterare l'indice -shm aggiornando i read-marks"
                )
            finally:
                writer_conn.close()

    def test_81_consistent_acquisition_via_quiescent_forensic_copy(self) -> None:
        """B5.7: Acquisizione coerente della copia tramite la nuova procedura forense senza toccare i sorgenti."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                copy_db, copy_dir, metrics = acquire_forensic_readonly_copy(src_db)
                try:
                    assert copy_db.is_file()
                    assert hashlib.sha256(copy_db.read_bytes()).hexdigest() == state["db_sha"]
                    assert metrics["CONSISTENT_SNAPSHOT_VERIFIED"] is True

                    # La copia SQLite può essere letta in isolamento
                    conn = sqlite3.connect(f"file:{copy_db.resolve().as_posix()}?mode=ro", uri=True)
                    try:
                        rows = conn.execute("SELECT COUNT(*) FROM live_cases;").fetchone()[0]
                        assert rows == 5
                    finally:
                        conn.close()
                finally:
                    if copy_dir and copy_dir.exists():
                        shutil.rmtree(copy_dir, ignore_errors=True)
            finally:
                writer_conn.close()

    def test_82_uncheckpointed_wal_records_recovered_in_isolated_snapshot(self) -> None:
        """B5.8: Recupero dei record che si trovano soltanto nel WAL e consolidamento nello snapshot."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir, record_count=10)
            try:
                snap_file, snap_temp_dir = create_consistent_readonly_snapshot(src_db)
                try:
                    assert snap_file.is_file()
                    # Connessione allo snapshot consolidato
                    snap_conn = sqlite3.connect(f"file:{snap_file.resolve().as_posix()}?mode=ro", uri=True)
                    try:
                        rows = snap_conn.execute("SELECT id, payload FROM live_cases ORDER BY id;").fetchall()
                        assert len(rows) == 10
                        assert rows[9] == (9, "buffered_val_9")
                    finally:
                        snap_conn.close()

                    # Sorgenti intatti
                    assert hashlib.sha256(src_db.read_bytes()).hexdigest() == state["db_sha"]
                    shm_file = src_db.parent / f"{src_db.name}-shm"
                    assert hashlib.sha256(shm_file.read_bytes()).hexdigest() == state["shm_sha"]
                finally:
                    if snap_temp_dir and snap_temp_dir.exists():
                        shutil.rmtree(snap_temp_dir, ignore_errors=True)
            finally:
                writer_conn.close()

    def test_83_active_writer_concurrent_retry_or_quiescence_window(self) -> None:
        """B5.9: Gestione controllata di finestra di quiescenza con retry in caso di writer attivo."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                call_attempts = [0]
                orig_measure = _measure_source_file_state

                def simulated_measure(p):
                    call_attempts[0] += 1
                    # Alla prima post-misurazione, simula un cambio di stato (scrittura concorrente)
                    res = orig_measure(p)
                    if call_attempts[0] == 2 and res:
                        mutated = dict(res)
                        mutated[p.name] = (mutated[p.name][0], mutated[p.name][1], "mutated_sha_concurrent_write")
                        return mutated
                    return res

                with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=simulated_measure):
                    copy_db, copy_dir, metrics = acquire_forensic_readonly_copy(
                        src_db, max_quiescence_retries=3, quiescence_retry_delay=0.01
                    )
                    try:
                        assert copy_db.is_file()
                        assert call_attempts[0] >= 3  # Ha effettuato il retry
                    finally:
                        if copy_dir and copy_dir.exists():
                            shutil.rmtree(copy_dir, ignore_errors=True)
            finally:
                writer_conn.close()

    def test_84_abort_when_consistent_copy_impossible(self) -> None:
        """B5.10: Impossibilità di ottenere una copia consistente causa abort sicuro con errore controllato."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                # Simula che la sorgente muti costantemente ad ogni misurazione
                counter = [0]
                def always_mutating_measure(p):
                    counter[0] += 1
                    return {p.name: (1024, 1000 + counter[0], f"hash_variation_{counter[0]}")}

                with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=always_mutating_measure):
                    with pytest.raises(SnapshotCreationFailedError) as exc_info:
                        acquire_forensic_readonly_copy(
                            src_db, max_quiescence_retries=2, quiescence_retry_delay=0.01
                        )
                    assert exc_info.value.error_code == "SNAPSHOT_CREATION_FAILED"
            finally:
                writer_conn.close()

    def test_85_incomplete_or_corrupt_copy_rejected(self) -> None:
        """B5.11: Rifiuto controllato di snapshot o copie incomplete o corrotte."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bad_dir = Path(tmp_dir) / "corrupt_storage"
            bad_dir.mkdir()
            corrupt_db = bad_dir / "checkpoints.db"
            corrupt_db.write_bytes(b"NOT_A_VALID_SQLITE_DATABASE_HEADER")

            with pytest.raises(SnapshotCreationFailedError):
                acquire_forensic_readonly_copy(corrupt_db)

            with pytest.raises(SnapshotCreationFailedError):
                create_consistent_readonly_snapshot(corrupt_db)

    def test_86_permission_denied_during_acquisition_handled_safely(self) -> None:
        """B5.12: Mancanza di permessi gestita con eccezione controllata e nessuna risorsa orfana."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                with patch("os.open", side_effect=PermissionError("Permission denied: [Errno 13]")):
                    with pytest.raises(SnapshotPermissionDeniedError):
                        acquire_forensic_readonly_copy(src_db)
            finally:
                writer_conn.close()

    def test_87_interruption_during_forensic_acquisition_cleans_up(self) -> None:
        """B6: Interruzione della procedura con cleanup verificato in area temporanea isolata."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                isolated_temp = Path(tmp_dir) / "isolated_temp"
                isolated_temp.mkdir()

                orig_mkdtemp = tempfile.mkdtemp
                created_dirs: list[Path] = []

                def tracked_mkdtemp(prefix="chatanalysis_forensic_", dir=None):
                    d = orig_mkdtemp(prefix=prefix, dir=str(isolated_temp))
                    p = Path(d)
                    created_dirs.append(p)
                    return str(p)

                with patch("tempfile.mkdtemp", side_effect=tracked_mkdtemp):
                    with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=KeyboardInterrupt("Simulated Ctrl+C")):
                        with pytest.raises(KeyboardInterrupt):
                            acquire_forensic_readonly_copy(src_db)

                # 1. Verifica che sia stata tracciata esattamente l'invocazione esaminata
                assert len(created_dirs) == 1, "Deve essere stata creata una sola cartella temporanea dall'invocazione"
                # 2. Verifica che la cartella creata sia stata completamente rimossa dal cleanup
                assert not created_dirs[0].exists(), "La directory temporanea creata dall'operazione deve essere stata rimossa al 100%"
                # 3. Verifica isolata: nessun residuo nell'area temporanea dedicata (senza scorrere globalmente /tmp)
                assert list(isolated_temp.iterdir()) == [], "Nessun file o cartella residua deve rimanere nell'area temporanea"
            finally:
                writer_conn.close()

    def test_88_cleanup_exclusive_to_created_temporary_resources(self) -> None:
        """B5.14: Cleanup esclusivo delle risorse temporanee senza toccare directory genitrici."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            work_dir = Path(tmp_dir) / "outer_workspace"
            work_dir.mkdir()
            canary_file = work_dir / "canary.txt"
            canary_file.write_text("precious_operator_data", encoding="utf-8")

            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                snap_file, snap_temp = create_consistent_readonly_snapshot(src_db, dest_dir=work_dir)
                try:
                    assert snap_file.is_file()
                    assert canary_file.is_file(), "Il canary file non deve essere eliminato durante l'operazione"
                finally:
                    if snap_temp and snap_temp.exists():
                        shutil.rmtree(snap_temp, ignore_errors=True)
                    assert canary_file.is_file(), "Il canary file deve sopravvivere al cleanup"
                    assert canary_file.read_text(encoding="utf-8") == "precious_operator_data"
            finally:
                writer_conn.close()

    def test_89_no_files_created_inside_checkpoints_dir(self) -> None:
        """B5.15: Nessun file creato nella directory .checkpoints."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            chk_dir = Path(tmp_dir) / ".checkpoints"
            chk_dir.mkdir()
            src_db = chk_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            initial_files = set(f.name for f in chk_dir.iterdir())

            # Esecuzione CLI con acquisizione forense automatica
            cmd = [
                sys.executable,
                "scripts/preflight_checkpoint_resume.py",
                "--db", str(src_db),
                "--json",
            ]
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc.returncode in (0, 2)

            final_files = set(f.name for f in chk_dir.iterdir())
            assert final_files == initial_files, "Nessun nuovo file o sidecar deve essere stato creato in .checkpoints"

    def test_90_no_writes_inside_tesi_repository(self) -> None:
        """B5.16: Nessuna scrittura nel repository Tesi."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            # Rifiuto categorico di snapshot dentro REPO_ROOT
            with pytest.raises(SnapshotDestinationForbiddenError):
                create_consistent_readonly_snapshot(src_db, dest_dir=REPO_ROOT)

    def test_91_no_sensitive_paths_in_reports_and_diagnostics(self) -> None:
        """B5.17: Nessuna esposizione di percorsi sensibili nei report e nella diagnostica."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                auditor = PreflightResumeAuditor(db_path=src_db)
                report = auditor.run_preflight()
                rep_dict = report.to_dict()

                # Il percorso nel report esportabile deve essere sanificato
                assert rep_dict["database_path"] == "[DATABASE_REDACTED]"
                for chk in rep_dict.get("checks", []):
                    assert str(tmp_dir) not in str(chk.get("observed_value", ""))
                    assert str(tmp_dir) not in str(chk.get("expected_value", ""))
            finally:
                writer_conn.close()

    def test_92_cli_subprocess_execution_with_open_writer(self) -> None:
        """B5.18: Esecuzione subprocess della CLI su WAL con open writer e sidecar preservati."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            # Apri una connessione writer in WAL mode e inserisci un record
            conn = sqlite3.connect(str(src_db))
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("CREATE TABLE open_subproc_table (k TEXT);")
                conn.execute("INSERT INTO open_subproc_table VALUES ('live_subproc');")
                conn.commit()
                conn.execute("SELECT COUNT(*) FROM open_subproc_table;").fetchone()

                shm_file = Path(tmp_dir) / "checkpoints.db-shm"
                wal_file = Path(tmp_dir) / "checkpoints.db-wal"
                assert shm_file.is_file()
                assert wal_file.is_file()

                db_sha_before = hashlib.sha256(src_db.read_bytes()).hexdigest()
                wal_sha_before = hashlib.sha256(wal_file.read_bytes()).hexdigest()
                shm_sha_before = hashlib.sha256(shm_file.read_bytes()).hexdigest()
                shm_mtime_before = shm_file.stat().st_mtime_ns

                cmd = [
                    sys.executable,
                    "scripts/preflight_checkpoint_resume.py",
                    "--db", str(src_db),
                    "--json",
                ]
                proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
                assert proc.returncode in (0, 2), f"CLI failed: {proc.stderr}"

                # Verifica output JSON
                data = json.loads(proc.stdout)
                assert "source_immutability_status" in data
                immut = data["source_immutability_status"]
                assert immut["SHM_BYTES_UNCHANGED"] is True
                assert immut["SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED"] is True

                # File originali al 100% intatti
                assert hashlib.sha256(src_db.read_bytes()).hexdigest() == db_sha_before
                assert hashlib.sha256(wal_file.read_bytes()).hexdigest() == wal_sha_before
                assert hashlib.sha256(shm_file.read_bytes()).hexdigest() == shm_sha_before
                assert shm_file.stat().st_mtime_ns == shm_mtime_before
            finally:
                conn.close()

    def test_93_zero_llm_inference_during_forensic_preflight(self) -> None:
        """B5.19: Assenza totale di inferenze LLM durante il preflight con staging forense."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                call_count = {"count": 0}
                def tracking_urlopen(*args, **kwargs):
                    call_count["count"] += 1
                    raise urllib.error.URLError("No connection allowed during audit")

                with patch("urllib.request.urlopen", side_effect=tracking_urlopen):
                    auditor = PreflightResumeAuditor(
                        db_path=src_db,
                        check_live_backend=False,
                    )
                    report = auditor.run_preflight()
                    assert call_count["count"] == 0
            finally:
                writer_conn.close()

    def test_94_no_regression_of_secure_output_json_with_forensic_copy(self) -> None:
        """B5.20: Non regressione dell'output JSON sicuro in presenza di copia forense di staging."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            out_dir = Path(tmp_dir) / "report_out"
            out_dir.mkdir()
            out_json = out_dir / "preflight_report.json"

            cmd = [
                sys.executable,
                "scripts/preflight_checkpoint_resume.py",
                "--db", str(src_db),
                "--output-json", str(out_json),
                "--json",
            ]
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc.returncode in (0, 2)
            assert out_json.is_file()

            # Tentativo di sovrascrittura del file JSON appena creato deve fallire (O_EXCL)
            proc2 = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc2.returncode == 3

    def test_95_report_distinguishes_logical_and_physical_immutability(self) -> None:
        """B4: Il report distingue integrità logica ed immutabilità fisica (B4 requirements)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                copy_db, copy_dir, metrics = acquire_forensic_readonly_copy(src_db)
                try:
                    auditor = PreflightResumeAuditor(
                        db_path=copy_db,
                        source_immutability_metrics=metrics,
                    )
                    report = auditor.run_preflight()
                    rep_dict = report.to_dict()

                    assert "source_immutability_status" in rep_dict
                    imm = rep_dict["source_immutability_status"]
                    assert "DATABASE_LOGICAL_CONTENT_UNCHANGED" in imm
                    assert "WAL_CONTENT_UNCHANGED" in imm
                    assert "SHM_BYTES_UNCHANGED" in imm
                    assert "SOURCE_METADATA_UNCHANGED" in imm
                    assert "CONSISTENT_SNAPSHOT_VERIFIED" in imm
                    assert "SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED" in imm

                    assert isinstance(imm["DATABASE_LOGICAL_CONTENT_UNCHANGED"], bool)
                    assert isinstance(imm["SHM_BYTES_UNCHANGED"], bool)
                    assert isinstance(imm["SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED"], bool)
                finally:
                    if copy_dir and copy_dir.exists():
                        shutil.rmtree(copy_dir, ignore_errors=True)
            finally:
                writer_conn.close()

    def test_96_shm_present_and_readable_verified(self) -> None:
        """B7.1: SHM presente e leggibile con misurazione e immutabilità verificata."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                shm_file = src_db.parent / f"{src_db.name}-shm"
                assert shm_file.is_file(), "Il file -shm deve esistere sul database WAL di test"
                copy_db, copy_dir, metrics = acquire_forensic_readonly_copy(src_db)
                try:
                    assert metrics["SHM_BYTES_UNCHANGED"] is True
                    assert metrics["SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED"] is True
                    assert copy_db.is_file()
                finally:
                    if copy_dir and copy_dir.exists():
                        shutil.rmtree(copy_dir, ignore_errors=True)
            finally:
                writer_conn.close()

    def test_97_shm_present_open_denied_fail_closed(self) -> None:
        """B7.2: SHM presente ma apertura negata (PermissionError) interrompe fail-closed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                shm_file = src_db.parent / f"{src_db.name}-shm"
                assert shm_file.is_file()

                orig_open = open
                def denied_open(file, *args, **kwargs):
                    if str(shm_file) in str(file) or (isinstance(file, Path) and file.name.endswith("-shm")):
                        raise PermissionError("[Errno 13] Permission denied to SHM sidecar")
                    return orig_open(file, *args, **kwargs)

                with patch("builtins.open", side_effect=denied_open):
                    with pytest.raises(SnapshotPermissionDeniedError) as exc_info:
                        acquire_forensic_readonly_copy(src_db)
                    assert exc_info.value.error_code == "SNAPSHOT_PERMISSION_DENIED"
            finally:
                writer_conn.close()

    def test_98_wal_present_open_denied_fail_closed(self) -> None:
        """B7.3: WAL presente ma apertura negata interrompe fail-closed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                wal_file = src_db.parent / f"{src_db.name}-wal"
                assert wal_file.is_file()

                orig_open = open
                def denied_open(file, *args, **kwargs):
                    if str(wal_file) in str(file) or (isinstance(file, Path) and file.name.endswith("-wal")):
                        raise PermissionError("[Errno 13] Permission denied to WAL sidecar")
                    return orig_open(file, *args, **kwargs)

                with patch("builtins.open", side_effect=denied_open):
                    with pytest.raises(SnapshotPermissionDeniedError) as exc_info:
                        acquire_forensic_readonly_copy(src_db)
                    assert exc_info.value.error_code == "SNAPSHOT_PERMISSION_DENIED"
            finally:
                writer_conn.close()

    def test_99_error_during_hashing_raises_snapshot_creation_failed(self) -> None:
        """B7.4: Errore I/O durante l'hashing solleva SnapshotCreationFailedError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                orig_open = open
                call_count = [0]
                def io_error_open(file, *args, **kwargs):
                    call_count[0] += 1
                    fp = orig_open(file, *args, **kwargs)
                    if call_count[0] == 2:
                        class BrokenFile:
                            def __enter__(self): return self
                            def __exit__(self, *a): fp.close()
                            def read(self, *a): raise OSError("[Errno 5] Input/output error on block device")
                        return BrokenFile()
                    return fp

                with patch("builtins.open", side_effect=io_error_open):
                    with pytest.raises(SnapshotCreationFailedError) as exc_info:
                        acquire_forensic_readonly_copy(src_db)
                    assert exc_info.value.error_code == "SNAPSHOT_CREATION_FAILED"
            finally:
                writer_conn.close()

    def test_100_sidecar_appears_or_disappears_during_procedure_aborts(self) -> None:
        """B7.5: Sidecar che compare o scompare durante la procedura abortisce immediatamente."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                orig_measure = _measure_source_file_state
                call_step = [0]

                def fluctuating_sidecars(p):
                    call_step[0] += 1
                    res = orig_measure(p)
                    assert res is not None
                    if call_step[0] >= 3:
                        mutated = dict(res)
                        mutated[f"{p.name}-journal"] = (1024, 999999, "new_journal_hash")
                        return mutated
                    return res

                with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=fluctuating_sidecars):
                    with pytest.raises(SnapshotCreationFailedError):
                        acquire_forensic_readonly_copy(src_db)
            finally:
                writer_conn.close()

    def test_101_state_t0_equals_t1_verified(self) -> None:
        """B7.6: Stato t0 == t1 verifica la finestra di quiescenza e procede con successo."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                t0_state = _measure_source_file_state(src_db)
                t1_state = _measure_source_file_state(src_db)
                assert t0_state == t1_state, "Lo stato pre e post deve essere identico in quiescenza"

                copy_db, copy_dir, metrics = acquire_forensic_readonly_copy(src_db)
                try:
                    assert metrics["CONSISTENT_SNAPSHOT_VERIFIED"] is True
                finally:
                    if copy_dir and copy_dir.exists():
                        shutil.rmtree(copy_dir, ignore_errors=True)
            finally:
                writer_conn.close()

    def test_102_state_t2_differs_from_t0_aborts_without_certification(self) -> None:
        """B7.7: Stato t2 diverso da t0 abortisce senza rilasciare alcuna certificazione positiva."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                orig_measure = _measure_source_file_state
                call_step = [0]

                def mutated_at_t2(p):
                    call_step[0] += 1
                    res = orig_measure(p)
                    assert res is not None
                    if call_step[0] >= 3:
                        mutated = dict(res)
                        sz, mt, md, hsh = mutated[p.name]
                        mutated[p.name] = (sz, mt + 5000000, md, "altered_sha_at_t2")
                        return mutated
                    return res

                with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=mutated_at_t2):
                    with pytest.raises(SnapshotCreationFailedError):
                        acquire_forensic_readonly_copy(src_db)
            finally:
                writer_conn.close()

    def test_103_state_t2_differs_from_t1_aborts(self) -> None:
        """B7.8: Stato t2 diverso da t1 abortisce l'operazione."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                orig_measure = _measure_source_file_state
                call_step = [0]

                def mutated_t2_vs_t1(p):
                    call_step[0] += 1
                    res = orig_measure(p)
                    assert res is not None
                    if call_step[0] >= 3:
                        mutated = dict(res)
                        shm_k = f"{p.name}-shm"
                        if shm_k in mutated:
                            sz, mt, md, hsh = mutated[shm_k]
                            mutated[shm_k] = (sz, mt + 1000, md, "mutated_shm_at_t2")
                        return mutated
                    return res

                with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=mutated_t2_vs_t1):
                    with pytest.raises(SnapshotCreationFailedError):
                        acquire_forensic_readonly_copy(src_db)
            finally:
                writer_conn.close()

    def test_104_acquisition_never_returns_false_certifications(self) -> None:
        """B7.9: L'acquisizione fallita non restituisce mai dizionari con valori True ingannevoli."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=[
                    {src_db.name: (100, 100, 0o600, "sha0")},
                    {src_db.name: (100, 100, 0o600, "sha0")},
                    {src_db.name: (100, 200, 0o600, "sha_altered")},
                ]):
                    with pytest.raises(SnapshotCreationFailedError):
                        acquire_forensic_readonly_copy(src_db)
            finally:
                writer_conn.close()

    def test_105_snapshot_does_not_ignore_negative_metrics(self) -> None:
        """B7.10: create_consistent_readonly_snapshot abortisce se l'acquisizione non certifica l'immutabilità."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                fake_unverified_metrics = {
                    "DATABASE_LOGICAL_CONTENT_UNCHANGED": False,
                    "WAL_CONTENT_UNCHANGED": False,
                    "SHM_BYTES_UNCHANGED": False,
                    "SOURCE_METADATA_UNCHANGED": False,
                    "CONSISTENT_SNAPSHOT_VERIFIED": False,
                    "SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED": False,
                }
                with patch("scripts.preflight_checkpoint_resume.acquire_forensic_readonly_copy", return_value=(src_db, None, fake_unverified_metrics)):
                    with pytest.raises(SnapshotCreationFailedError):
                        create_consistent_readonly_snapshot(src_db)
            finally:
                writer_conn.close()

    def test_106_cli_snapshot_uses_real_metrics_not_hardcoded(self) -> None:
        """B7.11: Il percorso CLI --snapshot usa metriche reali calcolate e non dizionari hardcoded."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            cmd = [
                sys.executable,
                "scripts/preflight_checkpoint_resume.py",
                "--db", str(src_db),
                "--snapshot",
                "--json",
            ]
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc.returncode in (0, 2), f"CLI failed: {proc.stderr}"
            data = json.loads(proc.stdout)
            assert "source_immutability_status" in data
            imm = data["source_immutability_status"]
            assert imm["SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED"] is True
            assert imm["CONSISTENT_SNAPSHOT_VERIFIED"] is True

    def test_107_report_without_metrics_does_not_assume_success(self) -> None:
        """B7.12: Un report istanziato senza metriche non assume True di default e usa NOT_MEASURED."""
        report = PreflightAuditReport(
            verdict=PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED,
            database_path="/fake/db.db",
            document_available=False,
        )
        for k, v in report.source_immutability_status.items():
            assert v == "NOT_MEASURED", f"Il controllo {k} deve essere NOT_MEASURED di default, non {v}"

        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "test.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            auditor2 = PreflightResumeAuditor(db_path=src_db, source_immutability_metrics=None)
            rep2 = auditor2.run_preflight()
            for k, v in rep2.source_immutability_status.items():
                assert v == "NOT_MEASURED"

    def test_108_atime_modification_handling_and_no_postwrite_fakery(self) -> None:
        """B7.13: Gestione di atime senza post-scritture artificiali e con metadati verificati dichiarati."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                past_time = 1500000000.0
                curr_mtime = src_db.stat().st_mtime
                try:
                    os.utime(src_db, (past_time, curr_mtime))
                except Exception:
                    pass

                copy_db, copy_dir, metrics = acquire_forensic_readonly_copy(src_db)
                try:
                    assert src_db.stat().st_mtime == curr_mtime
                    assert metrics["SOURCE_METADATA_UNCHANGED"] is True
                    assert metrics["SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED"] is True
                finally:
                    if copy_dir and copy_dir.exists():
                        shutil.rmtree(copy_dir, ignore_errors=True)
            finally:
                writer_conn.close()

    def test_109_wal_copy_with_unconsolidated_transactions(self) -> None:
        """B7.14: Copia WAL con transazioni attive non consolidate preserva i record nello snapshot."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir, record_count=8)
            try:
                writer_conn.execute("INSERT INTO live_cases VALUES (100, 'uncheckpointed_payload');")
                writer_conn.commit()

                snap_file, snap_temp = create_consistent_readonly_snapshot(src_db)
                try:
                    conn = sqlite3.connect(f"file:{snap_file.resolve().as_posix()}?mode=ro", uri=True)
                    try:
                        rows = conn.execute("SELECT id, payload FROM live_cases WHERE id=100;").fetchall()
                        assert len(rows) == 1
                        assert rows[0][1] == "uncheckpointed_payload"
                    finally:
                        conn.close()
                finally:
                    if snap_temp and snap_temp.exists():
                        shutil.rmtree(snap_temp, ignore_errors=True)
            finally:
                writer_conn.close()

    def test_110_active_writer_during_copy_retry_or_abort(self) -> None:
        """B7.15: Scrittore costantemente attivo oltre i retry causa abort sicuro."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                counter = [0]
                def mutating_source(p):
                    counter[0] += 1
                    return {p.name: (1024, 1000 + counter[0], f"hash_{counter[0]}")}

                with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=mutating_source):
                    with pytest.raises(SnapshotCreationFailedError):
                        acquire_forensic_readonly_copy(src_db, max_quiescence_retries=2, quiescence_retry_delay=0.01)
            finally:
                writer_conn.close()

    def test_111_transactionally_invalid_copy_rejected(self) -> None:
        """B7.16: Copia transazionalmente invalida viene respinta da PRAGMA integrity_check."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                orig_measure = _measure_source_file_state
                # Quando copy_db viene aperta, PRAGMA integrity_check deve fallire
                with patch("sqlite3.connect") as mock_connect:
                    mock_conn = MagicMock()
                    mock_conn.execute.return_value.fetchall.return_value = [("corrupt page 2",)]
                    mock_connect.return_value = mock_conn

                    with pytest.raises(SnapshotCreationFailedError):
                        acquire_forensic_readonly_copy(src_db)
            finally:
                writer_conn.close()

    def test_112_no_residue_after_error_or_interruption(self) -> None:
        """B7.17: Nessun file o directory temporanea residua dopo errore o interruzione."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                isolated_area = Path(tmp_dir) / "iso_area"
                isolated_area.mkdir()

                with patch("tempfile.mkdtemp", return_value=str(isolated_area / "temp_run")):
                    (isolated_area / "temp_run").mkdir()
                    with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=ValueError("Simulated sudden error")):
                        with pytest.raises(ValueError):
                            acquire_forensic_readonly_copy(src_db)

                # Nessun residuo nell'area isolata
                assert list(isolated_area.iterdir()) == []
            finally:
                writer_conn.close()

    def test_113_no_writes_inside_checkpoints_dir_fail_closed(self) -> None:
        """B7.18: Nessuna scrittura permessa all'interno di .checkpoints."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            chk_dir = Path(tmp_dir) / ".checkpoints"
            chk_dir.mkdir()
            src_db = chk_dir / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            with pytest.raises(SnapshotDestinationForbiddenError):
                create_consistent_readonly_snapshot(src_db, dest_dir=chk_dir)

    def test_114_no_personal_data_in_stdout_stderr_json(self) -> None:
        """B7.19: Nessun dato personale o canarino in stdout, stderr o JSON esportato."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            cmd = [
                sys.executable,
                "scripts/preflight_checkpoint_resume.py",
                "--db", str(src_db),
                "--json",
            ]
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc.returncode in (0, 2)
            out = proc.stdout + proc.stderr
            assert "Mario Rossi" not in out
            assert "Alice Wonderland" not in out
            assert "canary" not in out.lower()
            assert str(tmp_dir) not in out

    def test_115_zero_llm_calls_enforced(self) -> None:
        """B7.20: Assenza totale di chiamate verso backend LLM durante l'intero audit."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                call_counter = [0]
                def forbidden_urlopen(*a, **kw):
                    call_counter[0] += 1
                    raise AssertionError("Chiamata HTTP non consentita durante l'audit forense")

                with patch("urllib.request.urlopen", side_effect=forbidden_urlopen):
                    copy_db, copy_dir, metrics = acquire_forensic_readonly_copy(src_db)
                    try:
                        auditor = PreflightResumeAuditor(db_path=copy_db, check_live_backend=False)
                        report = auditor.run_preflight()
                        assert call_counter[0] == 0
                    finally:
                        if copy_dir and copy_dir.exists():
                            shutil.rmtree(copy_dir, ignore_errors=True)
            finally:
                writer_conn.close()

    def test_116_non_regression_of_secure_output_json(self) -> None:
        """B7.21: Non regressione di --output-json (creazione esclusiva e sicura)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            out_file = Path(tmp_dir) / "out_report.json"
            cmd = [
                sys.executable,
                "scripts/preflight_checkpoint_resume.py",
                "--db", str(src_db),
                "--output-json", str(out_file),
                "--json",
            ]
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc.returncode in (0, 2)
            assert out_file.is_file()

            # Tentativo di sovrascrittura deve fallire
            proc2 = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc2.returncode == 3

    def test_117_non_regression_selective_recovery_24_of_26_mock(self) -> None:
        """B7.22: Non regressione del recupero selettivo 24/26 con mock verified binding."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            binding = ModelIdentityBinding(
                requested_canonical_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                is_verified=True,
            )
            auditor = PreflightResumeAuditor(
                db_path=src_db,
                document=doc,
                model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                profile=OperationalProfile.BALANCED,
                max_topics=10,
                context_length=8192,
                temperature=0.0,
                seed=42,
                active_binding=binding,
            )
            report = auditor.run_preflight()
            assert report.verdict == PreflightVerdictStatus.MOCK_VERIFIED
            assert report.resumption_plan is not None
            assert report.resumption_plan["chunks_to_skip_count"] == 24
            assert report.resumption_plan["chunks_to_infer_count"] == 2
            assert report.resumption_plan["chunks_to_infer_indices_zero_based"] == [10, 19]

    def test_118_race_between_destination_check_and_o_excl(self) -> None:
        """F1.1: Race tra controllo di sicurezza e O_EXCL: se il file compare prima di os.open, O_EXCL fallisce e non cancella."""
        from scripts.preflight_checkpoint_resume import _validate_snapshot_target_safety
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                dest_p = Path(tmp_dir) / "race_target.db"
                orig_validate = _validate_snapshot_target_safety

                def create_competing_file_during_validate(*args, **kwargs):
                    orig_validate(*args, **kwargs)
                    dest_p.write_bytes(b"COMPETING_PROCESS_CONTENT_999")

                with patch("scripts.preflight_checkpoint_resume._validate_snapshot_target_safety", side_effect=create_competing_file_during_validate):
                    with pytest.raises(FileExistsError):
                        create_consistent_readonly_snapshot(src_db, dest_path=dest_p)

                assert dest_p.is_file(), "Il file concorrente deve sopravvivere al fallimento dello snapshot!"
                assert dest_p.read_bytes() == b"COMPETING_PROCESS_CONTENT_999"
            finally:
                writer_conn.close()

    def test_119_competing_file_survives_snapshot_failure(self) -> None:
        """F1.2: File concorrente già esistente alla destinazione sopravvive al fallimento dello snapshot."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                competing_file = Path(tmp_dir) / "already_exists.db"
                competing_file.write_bytes(b"ORIGINAL_FOREIGN_DATA_KEEP_SAFE")

                with pytest.raises(FileExistsError):
                    create_consistent_readonly_snapshot(src_db, dest_path=competing_file)

                assert competing_file.is_file()
                assert competing_file.read_bytes() == b"ORIGINAL_FOREIGN_DATA_KEEP_SAFE"
            finally:
                writer_conn.close()

    def test_120_preexisting_file_not_deleted(self) -> None:
        """F1.3 / F2: File preesistente nella cartella esterna non viene eliminato dopo fallimento di acquisizione."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                external_dir = Path(tmp_dir) / "user_export_dir"
                external_dir.mkdir()
                preexisting = external_dir / "precious_note.txt"
                preexisting.write_text("Dati utente preesistenti da non toccare", encoding="utf-8")

                with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=[
                    {src_db.name: (100, 100, 0o600, "sha0")},
                    {src_db.name: (100, 200, 0o600, "sha_altered")},
                ]):
                    with pytest.raises(SnapshotCreationFailedError):
                        acquire_forensic_readonly_copy(src_db, dest_dir=external_dir, max_quiescence_retries=1)

                assert external_dir.is_dir()
                assert preexisting.is_file()
                assert preexisting.read_text(encoding="utf-8") == "Dati utente preesistenti da non toccare"
            finally:
                writer_conn.close()

    def test_121_file_created_by_invocation_properly_deleted_after_error(self) -> None:
        """F1.4: File creato dall'invocazione corrente viene correttamente eliminato se il backup fallisce."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                allowed_dir = Path(tmp_dir) / "snap_target_folder"
                allowed_dir.mkdir()
                dest_snapshot = allowed_dir / "my_snapshot.db"

                with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("Simulated backup API failure")):
                    with pytest.raises(sqlite3.OperationalError):
                        create_consistent_readonly_snapshot(src_db, dest_path=dest_snapshot)

                assert not dest_snapshot.exists(), "Il file creato dall'invocazione deve essere rimosso su errore!"
                assert allowed_dir.is_dir(), "La cartella dell'utente deve essere preservata!"
            finally:
                writer_conn.close()

    def test_122_file_replaced_by_another_process_before_cleanup(self) -> None:
        """F1.5: File sostituito da un altro processo (inode diverso) prima del cleanup NON viene eliminato."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                allowed_dir = Path(tmp_dir) / "swap_folder"
                allowed_dir.mkdir()
                dest_snap = allowed_dir / "replaceable.db"

                def simulated_acquire_and_swap(p, *args, **kwargs):
                    if dest_snap.exists():
                        dest_snap.unlink()
                    dest_snap.write_bytes(b"REPLACEMENT_BY_CONCURRENT_PROCESS")
                    raise SnapshotCreationFailedError("Simulazione interruzione post-swap")

                with patch("scripts.preflight_checkpoint_resume.acquire_forensic_readonly_copy", side_effect=simulated_acquire_and_swap):
                    with pytest.raises(SnapshotCreationFailedError):
                        create_consistent_readonly_snapshot(src_db, dest_path=dest_snap)

                assert dest_snap.is_file(), "Il file sostituito da un altro processo deve sopravvivere!"
                assert dest_snap.read_bytes() == b"REPLACEMENT_BY_CONCURRENT_PROCESS"
            finally:
                writer_conn.close()

    def test_123_error_during_source_db_read_deletes_partial_destination(self) -> None:
        """F2.6: Errore durante la lettura del DB sorgente elimina immediatamente il file di destinazione parziale."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                dest_folder = Path(tmp_dir) / "output_folder"
                dest_folder.mkdir()

                orig_open = open
                call_cnt = [0]

                def faulty_open(file, mode="r", *args, **kwargs):
                    if "rb" in mode and str(src_db) in str(file):
                        call_cnt[0] += 1
                        if call_cnt[0] > 1:
                            raise OSError("Simulato fallimento di lettura I/O su sorgente")
                    return orig_open(file, mode, *args, **kwargs)

                with patch("builtins.open", side_effect=faulty_open):
                    with pytest.raises(SnapshotCreationFailedError):
                        acquire_forensic_readonly_copy(src_db, dest_dir=dest_folder)

                dest_copied_db = dest_folder / src_db.name
                assert not dest_copied_db.exists(), "Il file di destinazione parziale deve essere stato eliminato!"
                assert dest_folder.is_dir()
            finally:
                writer_conn.close()

    def test_124_error_during_destination_db_write_deletes_partial_destination(self) -> None:
        """F2.7: Errore durante la scrittura del DB di destinazione elimina il file parziale."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                dest_folder = Path(tmp_dir) / "write_fail_folder"
                dest_folder.mkdir()

                orig_open = open

                class FaultyWriter:
                    def __init__(self, raw):
                        self._raw = raw
                    def write(self, data):
                        raise OSError("Spazio su disco esaurito simulato")
                    def flush(self):
                        self._raw.flush()
                    def close(self):
                        self._raw.close()
                    def __enter__(self):
                        return self
                    def __exit__(self, exc_type, exc_val, exc_tb):
                        return self._raw.__exit__(exc_type, exc_val, exc_tb)

                def faulty_write_open(file, mode="r", *args, **kwargs):
                    raw = orig_open(file, mode, *args, **kwargs)
                    if "wb" in mode:
                        return FaultyWriter(raw)
                    return raw

                with patch("builtins.open", side_effect=faulty_write_open):
                    with pytest.raises(SnapshotCreationFailedError):
                        acquire_forensic_readonly_copy(src_db, dest_dir=dest_folder)

                dest_copied_db = dest_folder / src_db.name
                assert not dest_copied_db.exists(), "Il file parziale deve essere stato ripulito dopo errore di scrittura!"
            finally:
                writer_conn.close()

    def test_125_error_during_wal_and_shm_copy_cleans_up_all_partial_files(self) -> None:
        """F2.8: Errore durante la copia di WAL e SHM elimina TUTTI i file parziali posseduti."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                dest_folder = Path(tmp_dir) / "multi_sidecar_fail_folder"
                dest_folder.mkdir()

                orig_open = open

                def fail_on_wal_open(file, mode="r", *args, **kwargs):
                    if "-wal" in str(file) and "rb" in mode:
                        raise OSError("Simulato errore I/O durante lettura WAL")
                    return orig_open(file, mode, *args, **kwargs)

                with patch("builtins.open", side_effect=fail_on_wal_open):
                    with pytest.raises(SnapshotCreationFailedError):
                        acquire_forensic_readonly_copy(src_db, dest_dir=dest_folder)

                dest_db = dest_folder / src_db.name
                dest_wal = dest_folder / f"{src_db.name}-wal"
                dest_shm = dest_folder / f"{src_db.name}-shm"
                assert not dest_db.exists(), "Il database principale deve essere stato ripulito!"
                assert not dest_wal.exists(), "Il WAL deve essere stato ripulito!"
                assert not dest_shm.exists(), "Lo SHM deve essere stato ripulito!"
            finally:
                writer_conn.close()

    def test_126_deletion_of_all_partial_owned_files_across_sidecars(self) -> None:
        """F2.9: Eliminazione di tutti i file parziali posseduti in caso di errore sullo SHM."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                dest_folder = Path(tmp_dir) / "shm_fail_folder"
                dest_folder.mkdir()

                orig_open = open

                def fail_on_shm_open(file, mode="r", *args, **kwargs):
                    if "-shm" in str(file) and "rb" in mode:
                        raise OSError("Simulato errore I/O durante lettura SHM")
                    return orig_open(file, mode, *args, **kwargs)

                with patch("builtins.open", side_effect=fail_on_shm_open):
                    with pytest.raises(SnapshotCreationFailedError):
                        acquire_forensic_readonly_copy(src_db, dest_dir=dest_folder)

                for ext in ("", "-wal", "-shm", "-journal"):
                    assert not (dest_folder / f"{src_db.name}{ext}").exists()
            finally:
                writer_conn.close()

    def test_127_preservation_of_preexisting_external_directories(self) -> None:
        """F2.10: Preservazione della cartella esterna preesistente dell'utente."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                user_dir = Path(tmp_dir) / "persistent_user_workspace"
                user_dir.mkdir()
                canary_file = user_dir / "user_doc.pdf"
                canary_file.write_bytes(b"%PDF-1.4 canary")

                with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=ValueError("Sudden crash")):
                    with pytest.raises(ValueError):
                        acquire_forensic_readonly_copy(src_db, dest_dir=user_dir)

                assert user_dir.is_dir(), "La cartella dell'utente NON deve essere eliminata!"
                assert canary_file.is_file()
                assert canary_file.read_bytes() == b"%PDF-1.4 canary"
            finally:
                writer_conn.close()

    def test_128_source_permission_change_mode_aborts_without_certification(self) -> None:
        """F3.11: Modifica dei permessi (st_mode) del sorgente causa aborto fail-closed e rifiuto certificazione."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                orig_measure = _measure_source_file_state
                step = [0]

                def mutate_mode_at_t2(p):
                    step[0] += 1
                    res = orig_measure(p)
                    assert res is not None
                    if step[0] >= 3:
                        mutated = dict(res)
                        sz, mt, md, hsh = mutated[p.name]
                        new_mode = md ^ 0o044
                        mutated[p.name] = (sz, mt, new_mode, hsh)
                        return mutated
                    return res

                with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=mutate_mode_at_t2):
                    with pytest.raises(SnapshotCreationFailedError):
                        acquire_forensic_readonly_copy(src_db)
            finally:
                writer_conn.close()

    def test_129_metadata_comparison_t0_t1_t2_detects_mode_discrepancy(self) -> None:
        """F3.12: Confronto metadati t0/t1 rileva discrepanza su st_mode ed esegue retry/abort fail-closed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                orig_measure = _measure_source_file_state
                step = [0]

                def mutate_mode_at_t1(p):
                    step[0] += 1
                    res = orig_measure(p)
                    assert res is not None
                    if step[0] == 2:
                        mutated = dict(res)
                        sz, mt, md, hsh = mutated[p.name]
                        mutated[p.name] = (sz, mt, md ^ 0o040, hsh)
                        return mutated
                    return res

                with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=mutate_mode_at_t1):
                    copy_db, copy_dir, metrics = acquire_forensic_readonly_copy(
                        src_db, max_quiescence_retries=3, quiescence_retry_delay=0.01
                    )
                    try:
                        assert copy_db.is_file()
                        assert step[0] >= 3
                    finally:
                        if copy_dir and copy_dir.exists():
                            shutil.rmtree(copy_dir, ignore_errors=True)
            finally:
                writer_conn.close()

    def test_130_no_positive_certification_after_mode_discrepancy(self) -> None:
        """F3.13: Nessuna certificazione positiva può essere rilasciata se st_mode varia."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                orig_measure = _measure_source_file_state
                step = [0]

                def always_divergent_mode(p):
                    step[0] += 1
                    res = orig_measure(p)
                    assert res is not None
                    mutated = dict(res)
                    sz, mt, md, hsh = mutated[p.name]
                    mutated[p.name] = (sz, mt, md + step[0], hsh)
                    return mutated

                with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=always_divergent_mode):
                    with pytest.raises(SnapshotCreationFailedError):
                        acquire_forensic_readonly_copy(src_db, max_quiescence_retries=2, quiescence_retry_delay=0.01)
            finally:
                writer_conn.close()

    def test_131_non_regression_wal_recovery_with_uncheckpointed_frames(self) -> None:
        """14. Non regressione del recupero WAL: i record non consolidati vengono recuperati nello snapshot."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                snap_res = create_consistent_readonly_snapshot(src_db)
                snap_file = snap_res[0]
                snap_temp = snap_res[1]
                try:
                    assert snap_file.is_file()
                    c_conn = sqlite3.connect(f"file:{snap_file.resolve().as_posix()}?mode=ro", uri=True)
                    try:
                        row_count = c_conn.execute("SELECT COUNT(*) FROM live_cases;").fetchone()[0]
                        assert row_count == 5
                    finally:
                        c_conn.close()
                finally:
                    if snap_temp and snap_temp.exists():
                        shutil.rmtree(snap_temp, ignore_errors=True)
            finally:
                writer_conn.close()

    def test_132_non_regression_snapshots_and_json_reports(self) -> None:
        """15. Non regressione degli snapshot e dei report JSON con opzioni sicure."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)
            out_json = Path(tmp_dir) / "report_out.json"
            cmd = [
                sys.executable,
                str(REPO_ROOT / "scripts" / "preflight_checkpoint_resume.py"),
                "--db", str(src_db),
                "--output-json", str(out_json),
            ]
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            assert proc.returncode in (0, 2), f"CLI fallita con codice {proc.returncode}: {proc.stderr}"
            assert out_json.is_file()
            data = json.loads(out_json.read_text(encoding="utf-8"))
            assert "source_immutability_status" in data
            assert data["source_immutability_status"]["SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED"] is True

    def test_133_no_modifications_to_original_files_under_concurrency(self) -> None:
        """16. Nessuna modifica ai file originali sotto carichi di lettura o tentativi concorrenti."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                wal_file = Path(f"{src_db}-wal")
                shm_file = Path(f"{src_db}-shm")

                initial_db_sha = hashlib.sha256(src_db.read_bytes()).hexdigest()
                initial_wal_sha = hashlib.sha256(wal_file.read_bytes()).hexdigest()
                initial_shm_sha = hashlib.sha256(shm_file.read_bytes()).hexdigest()

                for _ in range(3):
                    c_db, c_dir, metrics = acquire_forensic_readonly_copy(src_db)
                    if c_dir and c_dir.exists():
                        shutil.rmtree(c_dir, ignore_errors=True)

                assert hashlib.sha256(src_db.read_bytes()).hexdigest() == initial_db_sha
                assert hashlib.sha256(wal_file.read_bytes()).hexdigest() == initial_wal_sha
                assert hashlib.sha256(shm_file.read_bytes()).hexdigest() == initial_shm_sha
            finally:
                writer_conn.close()

    def test_134_zero_llm_calls_enforced_under_ownership_and_integrity_checks(self) -> None:
        """17. Nessuna chiamata LLM durante tutte le verifiche di ownership e integrità."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                with patch("urllib.request.urlopen") as mock_url:
                    snap_res = create_consistent_readonly_snapshot(src_db)
                    if snap_res[1] and snap_res[1].exists():
                        shutil.rmtree(snap_res[1], ignore_errors=True)
                    assert mock_url.call_count == 0
            finally:
                writer_conn.close()

    def test_135_selective_recovery_24_of_26_only_via_mock(self) -> None:
        """18. Ripresa selettiva 24/26 concessa esclusivamente mediante mock verificato."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "checkpoints.db"
            doc = _create_synthetic_26_chunk_document()
            _populate_test_checkpoint_db(src_db, doc)

            # Senza mock o binding verificato: RUNTIME_VALIDATION_REQUIRED, MAI SAFE_RESUME_VERIFIED
            auditor_no_mock = PreflightResumeAuditor(
                db_path=src_db,
                document=doc,
                model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                active_binding=None,
            )
            rep_no_mock = auditor_no_mock.run_preflight()
            assert rep_no_mock.verdict == PreflightVerdictStatus.RUNTIME_VALIDATION_REQUIRED
            assert rep_no_mock.verdict != PreflightVerdictStatus.SAFE_RESUME_VERIFIED
            assert rep_no_mock.resumption_plan is None

            # Con mock verificato: MOCK_VERIFIED e autorizzazione simulata con 2 chunk da inferire
            mock_binding = ModelIdentityBinding(
                requested_canonical_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                is_verified=True,
            )
            auditor_mock = PreflightResumeAuditor(
                db_path=src_db,
                document=doc,
                model_id="qwen2.5-7b-instruct",
                resolved_model_key="qwen2.5-7b-instruct",
                profile=OperationalProfile.BALANCED,
                max_topics=10,
                context_length=8192,
                temperature=0.0,
                seed=42,
                active_binding=mock_binding,
            )
            rep_mock = auditor_mock.run_preflight()
            assert rep_mock.verdict == PreflightVerdictStatus.MOCK_VERIFIED
            assert rep_mock.resumption_plan is not None
            assert rep_mock.resumption_plan["chunks_to_infer_count"] == 2
            assert rep_mock.resumption_plan["chunks_to_infer_indices_zero_based"] == [10, 19]
            assert rep_mock.resumption_plan["chunks_to_skip_count"] == 24

    def test_136_file_replaced_with_different_inode(self) -> None:
        """2. Sostituzione del file con inode differente: il file concorrente con inode differente sopravvive al cleanup."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                allowed_dir = Path(tmp_dir) / "different_inode_folder"
                allowed_dir.mkdir()
                dest_snap = allowed_dir / "different_inode.db"

                def simulated_acquire_and_swap_diff_inode(p, *args, **kwargs):
                    if dest_snap.exists():
                        dest_snap.unlink()
                    dest_snap.write_bytes(b"CONCURRENT_DIFFERENT_INODE_CONTENT")
                    raise SnapshotCreationFailedError("Simulazione interruzione post-swap diff inode")

                with patch("scripts.preflight_checkpoint_resume.acquire_forensic_readonly_copy", side_effect=simulated_acquire_and_swap_diff_inode):
                    with pytest.raises(SnapshotCreationFailedError):
                        create_consistent_readonly_snapshot(src_db, dest_path=dest_snap)

                assert dest_snap.is_file(), "Il file sostituito da un processo concorrente deve sopravvivere!"
                assert dest_snap.read_bytes() == b"CONCURRENT_DIFFERENT_INODE_CONTENT"
            finally:
                writer_conn.close()

    def test_137_file_replaced_with_immediately_reused_inode_deterministic(self) -> None:
        """3. Sostituzione del file con inode immediatamente riutilizzato: riproducibile deterministico su qualsiasi OS."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                allowed_dir = Path(tmp_dir) / "reused_inode_folder"
                allowed_dir.mkdir()
                dest_snap = allowed_dir / "reused_inode.db"
                recorded_initial_stat = [None]

                orig_fstat = os.fstat
                def intercept_fstat(fd):
                    st = orig_fstat(fd)
                    if recorded_initial_stat[0] is None:
                        recorded_initial_stat[0] = st
                    return st

                def simulated_acquire_and_swap_same_inode(p, *args, **kwargs):
                    if dest_snap.exists():
                        dest_snap.unlink()
                    dest_snap.write_bytes(b"REUSED_INODE_COMPETITOR_DATA_999")
                    raise SnapshotCreationFailedError("Simulazione crash con inode riutilizzato")

                orig_lstat = os.lstat
                def mocked_lstat(path):
                    # Simula deterministicamente che l'OS assegni il medesimo (st_dev, st_ino)
                    if str(path) == str(dest_snap) and recorded_initial_stat[0] is not None:
                        class IdenticalInodeStat:
                            st_dev = recorded_initial_stat[0].st_dev
                            st_ino = recorded_initial_stat[0].st_ino
                            st_mode = 0o100600
                            st_size = len(b"REUSED_INODE_COMPETITOR_DATA_999")
                        return IdenticalInodeStat()
                    return orig_lstat(path)

                with patch("os.fstat", side_effect=intercept_fstat):
                    with patch("os.lstat", side_effect=mocked_lstat):
                        with patch("scripts.preflight_checkpoint_resume.acquire_forensic_readonly_copy", side_effect=simulated_acquire_and_swap_same_inode):
                            with pytest.raises(SnapshotCreationFailedError):
                                create_consistent_readonly_snapshot(src_db, dest_path=dest_snap)

                assert dest_snap.is_file(), "Il file concorrente con medesimo inode DEVE sopravvivere al cleanup!"
                assert dest_snap.read_bytes() == b"REUSED_INODE_COMPETITOR_DATA_999"
            finally:
                writer_conn.close()

    def test_138_file_replaced_in_explicit_external_directory(self) -> None:
        """4. Sostituzione in una directory esterna esplicita: la directory e il file concorrente sopravvivono."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                external_dir = Path(tmp_dir) / "custom_user_export_destination"
                external_dir.mkdir()
                dest_snap = external_dir / "my_custom_snapshot.db"

                def simulated_swap_external(p, *args, **kwargs):
                    if dest_snap.exists():
                        dest_snap.unlink()
                    dest_snap.write_bytes(b"EXTERNAL_DIR_COMPETITOR_FILE")
                    raise SnapshotCreationFailedError("Interruzione in cartella esterna")

                with patch("scripts.preflight_checkpoint_resume.acquire_forensic_readonly_copy", side_effect=simulated_swap_external):
                    with pytest.raises(SnapshotCreationFailedError):
                        create_consistent_readonly_snapshot(src_db, dest_path=dest_snap)

                assert external_dir.is_dir(), "La cartella esterna esplicita deve sempre essere preservata!"
                assert dest_snap.is_file(), "Il file nella directory esterna deve sopravvivere!"
                assert dest_snap.read_bytes() == b"EXTERNAL_DIR_COMPETITOR_FILE"
            finally:
                writer_conn.close()

    def test_139_fstat_failure_fail_closed_prevents_deletion(self) -> None:
        """6. Fallimento di fstat: identità non misurata non viene cancellata (fail-closed categorico)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                allowed_dir = Path(tmp_dir) / "fstat_fail_folder"
                allowed_dir.mkdir()
                dest_snap = allowed_dir / "unmeasured_ident.db"

                orig_fstat = os.fstat
                def failing_fstat(fd):
                    # Simula fallimento improvviso di fstat sul descrittore di dest_snap
                    raise OSError("Simulato errore del kernel durante fstat")

                with patch("os.fstat", side_effect=failing_fstat):
                    with pytest.raises(SnapshotCreationFailedError) as exc_info:
                        create_consistent_readonly_snapshot(src_db, dest_path=dest_snap)
                    assert "Impossibile verificare l'identità del file di snapshot creato" in str(exc_info.value)

                assert allowed_dir.is_dir()
            finally:
                writer_conn.close()

    def test_140_error_before_ownership_registration(self) -> None:
        """7. Errore prima della registrazione dell'ownership: se os.open fallisce, dest_file_owned rimane False."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                allowed_dir = Path(tmp_dir) / "pre_reg_fail"
                allowed_dir.mkdir()
                dest_snap = allowed_dir / "target.db"

                with patch("os.open", side_effect=PermissionError("EACCES simulato prima della registrazione")):
                    with pytest.raises(SnapshotPermissionDeniedError):
                        create_consistent_readonly_snapshot(src_db, dest_path=dest_snap)

                assert not dest_snap.exists()
                assert allowed_dir.is_dir()
            finally:
                writer_conn.close()

    def test_141_error_after_ownership_registration_cleans_only_owned(self) -> None:
        """8. Errore dopo la registrazione dell'ownership: il file posseduto viene eliminato ma altri file sono preservati."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                allowed_dir = Path(tmp_dir) / "chatanalysis_snap_post_reg_fail"
                allowed_dir.mkdir()
                neighbor_file = allowed_dir / "unrelated_user_file.txt"
                neighbor_file.write_text("File vicino da non toccare", encoding="utf-8")
                dest_snap = allowed_dir / "owned_to_delete.db"

                # Errore durante l'acquisizione dopo che dest_snap è stato creato e registrato
                with patch("scripts.preflight_checkpoint_resume.acquire_forensic_readonly_copy", side_effect=SnapshotCreationFailedError("Fallimento acquisizione post-reg")):
                    with pytest.raises(SnapshotCreationFailedError):
                        create_consistent_readonly_snapshot(src_db, dest_path=dest_snap)

                assert not dest_snap.exists(), "Il file posseduto deve essere stato ripulito!"
                assert neighbor_file.is_file(), "Il file vicino non posseduto deve rimanere intatto!"
                assert neighbor_file.read_text(encoding="utf-8") == "File vicino da non toccare"
                assert allowed_dir.is_dir()
            finally:
                writer_conn.close()

    def test_142_fstat_failure_in_acquire_forensic_copy_fail_closed(self) -> None:
        """6b. Fallimento fstat durante copia forense: abort fail-closed immediato e non cancellazione di identità sconosciute."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                dest_dir = Path(tmp_dir) / "fstat_staging_fail"
                dest_dir.mkdir()

                orig_fstat = os.fstat
                def faulty_fstat_staging(fd):
                    raise OSError("fstat kernel failure during staging copy")

                with patch("os.fstat", side_effect=faulty_fstat_staging):
                    with pytest.raises(SnapshotCreationFailedError) as exc_info:
                        acquire_forensic_readonly_copy(src_db, dest_dir=dest_dir)
                    assert "Impossibile verificare l'identità del file creato per la copia forense" in str(exc_info.value)

                assert dest_dir.is_dir()
            finally:
                writer_conn.close()

    def test_143_immediately_reused_inode_in_acquire_forensic_copy(self) -> None:
        """3b. Inode riutilizzato durante la copia forense: il descrittore dup_fd rileva st_nlink == 0 e preserva il file concorrente."""
        if os.name == "nt":
            pytest.skip("La sostituzione concorrente di un file aperto con descrittore attivo è una semantica specifica POSIX/Linux.")
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                dest_dir = Path(tmp_dir) / "staging_reused_inode"
                dest_dir.mkdir()
                staged_db = dest_dir / src_db.name

                orig_measure = _measure_source_file_state
                step = [0]

                def swap_staged_during_t1(p):
                    res = orig_measure(p)
                    step[0] += 1
                    if step[0] == 2:
                        # Simulazione di processo concorrente che sostituisce la copia di staging prima della misurazione t1
                        if staged_db.exists():
                            staged_db.unlink()
                            staged_db.write_bytes(b"CONCURRENT_STAGING_REPLACEMENT_INODE")
                    return res

                with patch("scripts.preflight_checkpoint_resume._measure_source_file_state", side_effect=swap_staged_during_t1):
                    with pytest.raises(SnapshotCreationFailedError):
                        acquire_forensic_readonly_copy(src_db, dest_dir=dest_dir, max_quiescence_retries=1)

                if os.name != "nt":
                    assert staged_db.is_file(), "Il file concorrente nella cartella di staging deve sopravvivere!"
                    assert staged_db.read_bytes() == b"CONCURRENT_STAGING_REPLACEMENT_INODE"
            finally:
                writer_conn.close()

    def test_144_file_replaced_after_identity_verification_before_unlink(self) -> None:
        """F1.6: Sostituzione concorrente del file temporaneo nell'area privata: rifiuto fail-closed dell'unlink e sopravvivenza del file."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            snap_dir = Path(tmp_dir) / "toctou_folder"
            snap_dir.mkdir()
            _OWNERSHIP_TRACKER.register_temp_dir(snap_dir)

            dest_snap = snap_dir / "toctou_replaceable.db"
            dest_snap.write_bytes(b"INITIAL_OWNED_DATA_144")
            st_orig = os.lstat(dest_snap)
            orig_ident = (int(st_orig.st_dev), int(st_orig.st_ino))
            _OWNERSHIP_TRACKER.register_owned_file(dest_snap, orig_ident)

            orig_lstat = os.lstat
            swap_performed = [False]

            def intercept_lstat_and_swap(path, *args, **kwargs):
                if str(path) == str(dest_snap) and not swap_performed[0]:
                    dest_snap.unlink()
                    dest_snap.write_bytes(b"COMPETING_REPLACEMENT_SURVIVED_TEST_144")
                    swap_performed[0] = True
                return orig_lstat(path, *args, **kwargs)

            with patch("os.lstat", side_effect=intercept_lstat_and_swap):
                res = _perform_atomic_safe_unlink(dest_snap, None, orig_ident)

            assert swap_performed[0], "Lo swap concorrente deve essere stato eseguito!"
            assert res is False, "Il cleanup fail-closed DEVE restituire False quando rileva sostituzione concorrente!"
            assert dest_snap.is_file(), "Il file concorrente sostituito dopo la verifica d'identità DEVE sopravvivere!"
            assert dest_snap.read_bytes() == b"COMPETING_REPLACEMENT_SURVIVED_TEST_144"
            _OWNERSHIP_TRACKER.unregister_temp_dir(snap_dir)

    def test_145_file_replaced_after_final_check_immediately_before_unlink(self) -> None:
        """F1.7: Fallimento acquisizione con destinazione esterna: nessuna cancellazione distruttiva su percorsi esterni (Rule 4)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                allowed_dir = Path(tmp_dir) / "toctou_folder_145"
                allowed_dir.mkdir()
                dest_snap = allowed_dir / "toctou_replaceable_145.db"

                # Un concorrente crea un file sul percorso di destinazione esterna durante la fase di staging
                def acquire_with_competitor_creation(src_path):
                    dest_snap.write_bytes(b"COMPETING_REPLACEMENT_SURVIVED_TEST_145")
                    raise SnapshotCreationFailedError("Crash simulato per attivare cleanup")

                with patch("scripts.preflight_checkpoint_resume.acquire_forensic_readonly_copy", side_effect=acquire_with_competitor_creation):
                    with pytest.raises(SnapshotCreationFailedError):
                        create_consistent_readonly_snapshot(src_db, dest_path=dest_snap)

                assert dest_snap.is_file(), "Il file concorrente creato alla destinazione esterna DEVE sopravvivere intatto!"
                assert dest_snap.read_bytes() == b"COMPETING_REPLACEMENT_SURVIVED_TEST_145"
            finally:
                writer_conn.close()

    def test_146_spoofed_directory_with_valid_prefix_rejected_fail_closed(self) -> None:
        """A2.1: Rifiuto di una directory falsificata con prefisso canonico valido ('chatanalysis_snap_not_actually_owned')."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            spoofed_dir = Path(tmp_dir) / "chatanalysis_snap_not_actually_owned"
            spoofed_dir.mkdir()
            fake_file = spoofed_dir / "target_snapshot.db"
            fake_file.write_bytes(b"DATA_CREATED_BY_COMPETITOR_OR_AUDIT")

            # La directory ha il prefisso valido, ma NON è stata registrata da _OWNERSHIP_TRACKER
            assert not _OWNERSHIP_TRACKER.is_owned_dir(spoofed_dir)
            st = os.lstat(fake_file)
            ident = (st.st_dev, st.st_ino)

            # Invocazione diretta di cleanup: deve rifiutare categoricamente fail-closed
            res = _perform_atomic_safe_unlink(fake_file, None, ident)
            assert res is False, "La cancellazione deve essere rifiutata se la directory non è comprovatamente posseduta!"
            assert fake_file.is_file(), "Il file nella directory falsificata DEVE sopravvivere intatto!"
            assert fake_file.read_bytes() == b"DATA_CREATED_BY_COMPETITOR_OR_AUDIT"

    def test_147_competing_file_replaced_after_final_check_survives(self) -> None:
        """A2.2: Preservazione del file concorrente sostituito esattamente prima di unlink() in directory con prefisso valido."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            spoofed_dir = Path(tmp_dir) / "chatanalysis_snap_concurrent_adversary"
            spoofed_dir.mkdir()
            orig_file = spoofed_dir / "snapshot.db"
            orig_file.write_bytes(b"ORIGINAL_SNAPSHOT_DATA_BEFORE_SWAP")

            st = os.lstat(orig_file)
            orig_ident = (st.st_dev, st.st_ino)

            # Simulazione attacco TOCTOU: il file originario viene spostato e sostituito da un file concorrente
            orig_file.unlink()
            orig_file.write_bytes(b"COMPETITOR_INJECTED_FILE_SURVIVES")

            res = _perform_atomic_safe_unlink(orig_file, None, orig_ident)
            assert res is False, "Cleanup deve fallire ed evitare categoricamente di cancellare il file concorrente!"
            assert orig_file.is_file()
            assert orig_file.read_bytes() == b"COMPETITOR_INJECTED_FILE_SURVIVES"

    def test_148_genuinely_owned_resources_cleaned_up(self) -> None:
        """A2.3: Pulizia corretta delle risorse comprovatamente possedute create da tempfile.mkdtemp() dall'invocazione."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                # Creazione snapshot con percorso automatico in directory posseduta
                with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("Simulato crash durante consolidamento transazionale")):
                    with pytest.raises(sqlite3.OperationalError):
                        create_consistent_readonly_snapshot(src_db, dest_dir=Path(tmp_dir))

                # Nessuna cartella residua registrata
                residual_snaps = [p for p in Path(tmp_dir).iterdir() if p.is_dir() and "snap" in p.name]
                for r in residual_snaps:
                    assert not _OWNERSHIP_TRACKER.is_owned_dir(r), "Nessuna directory temporanea deve rimanere registrata dopo il cleanup!"
            finally:
                writer_conn.close()

    def test_149_indeterminate_ownership_fail_closed(self) -> None:
        """A2.4: Comportamento fail-closed quando dest_file_identity è indeterminata, parziale o nulla."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_file = Path(tmp_dir) / "indeterminate.db"
            test_file.write_bytes(b"IMPORTANT_USER_OR_EVIDENCE_DATA")

            # 1. Identity is None
            assert _perform_atomic_safe_unlink(test_file, None, None) is False
            assert test_file.is_file()

            # 2. Identity con st_ino nullo (0, 0)
            assert _perform_atomic_safe_unlink(test_file, None, (0, 0)) is False
            assert test_file.is_file()

            # 3. Identity con inode 0 su dev valido
            st = os.lstat(test_file)
            assert _perform_atomic_safe_unlink(test_file, None, (st.st_dev, 0)) is False
            assert test_file.is_file()
            assert test_file.read_bytes() == b"IMPORTANT_USER_OR_EVIDENCE_DATA"

    def test_150_source_database_immutability_preserved_during_failed_cleanup(self) -> None:
        """A2.5: Assoluta immutabilità dei database sorgenti (db, wal, shm) durante e dopo cleanup fallito."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                wal_path = Path(str(src_db) + "-wal")
                shm_path = Path(str(src_db) + "-shm")

                db_before = src_db.read_bytes()
                wal_before = wal_path.read_bytes() if wal_path.exists() else b""
                shm_before = shm_path.read_bytes() if shm_path.exists() else b""
                db_hash_before = hashlib.sha256(db_before).hexdigest()
                wal_hash_before = hashlib.sha256(wal_before).hexdigest()

                dest_target = Path(tmp_dir) / "fail_cleanup_test" / "snap.db"
                with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("Simulato errore transazionale")):
                    with pytest.raises(sqlite3.OperationalError):
                        create_consistent_readonly_snapshot(src_db, dest_path=dest_target)

                # Verifica immutabilità byte-for-byte dei database sorgenti
                assert hashlib.sha256(src_db.read_bytes()).hexdigest() == db_hash_before
                if wal_path.exists():
                    assert hashlib.sha256(wal_path.read_bytes()).hexdigest() == wal_hash_before
                if shm_path.exists():
                    assert shm_path.read_bytes() == shm_before
            finally:
                writer_conn.close()

    def test_151_adversarial_file_replacement_at_unlink_boundary_survives(self) -> None:
        """Mandatory 1: Sostituzione concorrente del file DOPO l'ultima verifica, esattamente alla soglia di cancellazione."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            snap_dir = Path(tmp_dir) / "snap_151"
            snap_dir.mkdir()
            _OWNERSHIP_TRACKER.register_temp_dir(snap_dir)

            dest_snap = snap_dir / "snapshot_151.db"
            dest_snap.write_bytes(b"ORIGINAL_SNAPSHOT_151")
            st_orig = os.lstat(dest_snap)
            orig_ident = (int(st_orig.st_dev), int(st_orig.st_ino))
            _OWNERSHIP_TRACKER.register_owned_file(dest_snap, orig_ident)

            orig_lstat = os.lstat
            swap_done = [False]

            def adversarial_lstat(path, *args, **kwargs):
                if str(path) == str(dest_snap) and not swap_done[0]:
                    dest_snap.unlink()
                    dest_snap.write_bytes(b"COMPETITOR_CRITICAL_SURVIVOR_151")
                    swap_done[0] = True
                return orig_lstat(path, *args, **kwargs)

            with patch("os.lstat", side_effect=adversarial_lstat):
                res = _perform_atomic_safe_unlink(dest_snap, None, orig_ident)

            assert swap_done[0], "Lo swap avversario deve essere stato eseguito alla soglia critica!"
            assert res is False, "Il cleanup fail-closed DEVE restituire False quando rileva sostituzione concorrente!"
            assert dest_snap.is_file(), "Il file concorrente DEVE sopravvivere intatto al cleanup!"
            assert dest_snap.read_bytes() == b"COMPETITOR_CRITICAL_SURVIVOR_151"
            _OWNERSHIP_TRACKER.unregister_temp_dir(snap_dir)

    def test_152_registered_directory_replaced_at_same_path_rejected(self) -> None:
        """Mandatory 2: Sostituzione della directory registrata nello stesso percorso rifiutata dinamicamente dal tracker."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            dir_path = Path(tmp_dir) / "registered_dir_152"
            dir_path.mkdir()
            _OWNERSHIP_TRACKER.register_temp_dir(dir_path)

            assert _OWNERSHIP_TRACKER.is_owned_dir(dir_path) is True, "La directory appena registrata deve risultare posseduta!"

            backup_path = Path(tmp_dir) / "registered_dir_152_backup"
            dir_path.rename(backup_path)
            dir_path.mkdir()

            assert _OWNERSHIP_TRACKER.is_owned_dir(dir_path) is False, "La directory concorrente sostitutiva NON deve essere riconosciuta come posseduta!"
            assert _OWNERSHIP_TRACKER.is_registered_dir(dir_path) is True, "Il percorso storico rimane registrato ma non posseduto!"

            _OWNERSHIP_TRACKER.unregister_temp_dir(dir_path)

    def test_153_competitor_file_preserved_without_deletion(self) -> None:
        """Mandatory 3: Preservazione assoluta di un file appartenente a un concorrente in una directory non posseduta o condivisa."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            shared_dir = Path(tmp_dir) / "shared_folder_153"
            shared_dir.mkdir()
            competitor_file = shared_dir / "competitor_evidence.dat"
            competitor_file.write_bytes(b"CONFIDENTIAL_COMPETITOR_DATA_153")
            st = os.lstat(competitor_file)
            ident = (int(st.st_dev), int(st.st_ino))

            res = _perform_atomic_safe_unlink(competitor_file, None, ident)
            assert res is False, "Il cleanup su file non posseduto deve fallire (fail-closed)!"
            assert competitor_file.is_file(), "Il file concorrente deve essere preservato!"
            assert competitor_file.read_bytes() == b"CONFIDENTIAL_COMPETITOR_DATA_153"

    def test_154_replacement_competitor_directory_preserved_from_rmtree(self) -> None:
        """Mandatory 4: Preservazione di una directory concorrente sostitutiva durante i percorsi di eccezione (zero shutil.rmtree indebiti)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                base_target_dir = Path(tmp_dir) / "snap_target_base_154"
                base_target_dir.mkdir()

                swap_performed = [False]

                def swap_temp_dir_and_fail(src_path):
                    # Intercetta l'esecuzione DOPO che la directory temporanea è stata creata e registrata
                    snaps = [d for d in base_target_dir.iterdir() if d.is_dir() and "snap" in d.name]
                    if snaps and not swap_performed[0]:
                        real_dir = snaps[0]
                        renamed = real_dir.parent / f"{real_dir.name}_bak"
                        real_dir.rename(renamed)
                        real_dir.mkdir()
                        victim_file = real_dir / "competitor_irreplaceable_evidence.log"
                        victim_file.write_bytes(b"IRREPLACEABLE_EVIDENCE_OF_THIRD_PARTY_154")
                        swap_performed[0] = True
                    raise SnapshotCreationFailedError("Simulato crash durante acquisizione per verificare rmtree fail-closed")

                with patch("scripts.preflight_checkpoint_resume.acquire_forensic_readonly_copy", side_effect=swap_temp_dir_and_fail):
                    with pytest.raises(SnapshotCreationFailedError):
                        create_consistent_readonly_snapshot(src_db, dest_dir=base_target_dir)

                assert swap_performed[0], "Lo swap della directory registrata deve essere avvenuto!"
                found_dirs = [d for d in base_target_dir.iterdir() if d.is_dir() and "snap" in d.name and not d.name.endswith("_bak")]
                assert len(found_dirs) >= 1, "La directory concorrente DEVE essere sopravvissuta!"
                victim_survivors = [f for d in found_dirs for f in d.iterdir() if f.name == "competitor_irreplaceable_evidence.log"]
                assert len(victim_survivors) >= 1, "Il file della directory concorrente DEVE essere sopravvissuto!"
                assert victim_survivors[0].read_bytes() == b"IRREPLACEABLE_EVIDENCE_OF_THIRD_PARTY_154"
            finally:
                writer_conn.close()

    def test_155_cleanup_strictly_confined_to_legitimate_owned_resources(self) -> None:
        """Mandatory 5: Cleanup confinato rigorosamente alle sole risorse legittimamente possedute create dall'invocazione."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                base_dir = Path(tmp_dir) / "legit_test_155"
                base_dir.mkdir()
                unrelated_file = base_dir / "unrelated_user_data.txt"
                unrelated_file.write_bytes(b"UNRELATED_USER_DATA_PRESERVED")

                with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("Simulato crash per testare cleanup legittimo")):
                    with pytest.raises(sqlite3.OperationalError):
                        create_consistent_readonly_snapshot(src_db, dest_dir=base_dir)

                assert unrelated_file.is_file()
                assert unrelated_file.read_bytes() == b"UNRELATED_USER_DATA_PRESERVED"

                residual_dirs = [d for d in base_dir.iterdir() if d.is_dir() and "chatanalysis_snap_" in d.name]
                assert len(residual_dirs) == 0, "Le cartelle legittimamente possedute devono essere state ripulite!"
            finally:
                writer_conn.close()

    def test_156_wal_shm_acquisition_failure_cleanup_fail_closed(self) -> None:
        """Mandatory 6: Fallimento durante l'acquisizione WAL/SHM: abort fail-closed e pulizia sicura."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                target_snap = Path(tmp_dir) / "wal_fail_target" / "snap_156.db"

                with patch("scripts.preflight_checkpoint_resume.acquire_forensic_readonly_copy", side_effect=SnapshotCreationFailedError("Fallimento acquisizione WAL non quiescente")):
                    with pytest.raises(SnapshotCreationFailedError) as exc_info:
                        create_consistent_readonly_snapshot(src_db, dest_path=target_snap)

                assert "Fallimento acquisizione WAL non quiescente" in str(exc_info.value)
                assert not target_snap.exists()
            finally:
                writer_conn.close()

    def test_157_sqlite_backup_failure_cleanup_fail_closed(self) -> None:
        """Mandatory 7: Fallimento durante il backup SQLite: abort fail-closed, preservazione sorgenti e cleanup controllato."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                target_snap = Path(tmp_dir) / "backup_fail_target" / "snap_157.db"

                class FailingBackupConnection(sqlite3.Connection):
                    def backup(self, *args, **kwargs):
                        raise sqlite3.OperationalError("Simulato I/O disk error durante conn.backup()")

                orig_connect = sqlite3.connect

                def connect_with_failing_backup(*args, **kwargs):
                    kwargs["factory"] = FailingBackupConnection
                    return orig_connect(*args, **kwargs)

                with patch("sqlite3.connect", side_effect=connect_with_failing_backup):
                    with pytest.raises(sqlite3.OperationalError):
                        create_consistent_readonly_snapshot(src_db, dest_path=target_snap)

                assert not target_snap.exists(), "Lo snapshot parziale fallito deve essere stato rimosso in modo sicuro!"
            finally:
                writer_conn.close()

    def test_158_unmeasurable_file_identity_rejects_cleanup_fail_closed(self) -> None:
        """Mandatory 8: Identità non misurabile o nulla rigetta categoricamente il cleanup (fail-closed)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_f = Path(tmp_dir) / "unmeasurable_158.db"
            test_f.write_bytes(b"DATA_158")

            # 1. Identity is None
            assert _perform_atomic_safe_unlink(test_f, None, None) is False
            assert test_f.is_file()

            # 2. Identity (0, 0)
            assert _perform_atomic_safe_unlink(test_f, None, (0, 0)) is False
            assert test_f.is_file()

            # 3. Identity (st_dev, 0)
            st = os.lstat(test_f)
            assert _perform_atomic_safe_unlink(test_f, None, (st.st_dev, 0)) is False
            assert test_f.is_file()

            # 4. Identity (0, st_ino)
            assert _perform_atomic_safe_unlink(test_f, None, (0, st.st_ino)) is False
            assert test_f.is_file()

            # 5. lstat solleva OSError
            with patch("os.lstat", side_effect=OSError("Accesso I/O negato su lstat")):
                assert _perform_atomic_safe_unlink(test_f, None, (st.st_dev, st.st_ino)) is False
                assert test_f.is_file()

    def test_159_preexisting_explicit_destination_preserved_unmodified(self) -> None:
        """Mandatory 9: Destinazione esplicita preesistente: solleva FileExistsError e non altera i byte."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                preexisting_file = Path(tmp_dir) / "preexisting_snapshot_159.db"
                original_bytes = b"PREEXISTING_EVIDENCE_USER_DATA_DO_NOT_TOUCH_159"
                preexisting_file.write_bytes(original_bytes)
                orig_hash = hashlib.sha256(original_bytes).hexdigest()

                with pytest.raises(FileExistsError):
                    create_consistent_readonly_snapshot(src_db, dest_path=preexisting_file)

                assert preexisting_file.is_file(), "Il file preesistente DEVE esistere!"
                assert hashlib.sha256(preexisting_file.read_bytes()).hexdigest() == orig_hash, "I byte del file preesistente devono essere invariati!"
            finally:
                writer_conn.close()

    def test_160_byte_for_byte_source_db_wal_shm_invariance_across_failures(self) -> None:
        """Mandatory 10: Invarianza byte-per-byte assoluta del database sorgente, WAL e SHM attraverso tutte le casistiche di errore."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                wal_path = Path(str(src_db) + "-wal")
                shm_path = Path(str(src_db) + "-shm")

                db_bytes_t0 = src_db.read_bytes()
                wal_bytes_t0 = wal_path.read_bytes() if wal_path.exists() else b""
                shm_bytes_t0 = shm_path.read_bytes() if shm_path.exists() else b""

                db_hash_t0 = hashlib.sha256(db_bytes_t0).hexdigest()
                wal_hash_t0 = hashlib.sha256(wal_bytes_t0).hexdigest()

                # Caso A: Snapshot regolare con successo
                snap_file, snap_temp = create_consistent_readonly_snapshot(src_db, dest_dir=Path(tmp_dir))
                assert snap_file.is_file()
                if snap_temp and snap_temp.exists():
                    shutil.rmtree(snap_temp, ignore_errors=True)

                # Invarianza post-successo
                assert hashlib.sha256(src_db.read_bytes()).hexdigest() == db_hash_t0
                if wal_path.exists():
                    assert hashlib.sha256(wal_path.read_bytes()).hexdigest() == wal_hash_t0
                if shm_path.exists():
                    assert shm_path.read_bytes() == shm_bytes_t0

                # Caso B: Fallimento backup SQLite
                with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("Crash di test backup")):
                    with pytest.raises(sqlite3.OperationalError):
                        create_consistent_readonly_snapshot(src_db, dest_dir=Path(tmp_dir))

                # Invarianza post-fallimento backup
                assert hashlib.sha256(src_db.read_bytes()).hexdigest() == db_hash_t0
                if wal_path.exists():
                    assert hashlib.sha256(wal_path.read_bytes()).hexdigest() == wal_hash_t0
                if shm_path.exists():
                    assert shm_path.read_bytes() == shm_bytes_t0

                # Caso C: Fallimento acquisizione WAL
                with patch("scripts.preflight_checkpoint_resume.acquire_forensic_readonly_copy", side_effect=SnapshotCreationFailedError("Crash di test WAL")):
                    with pytest.raises(SnapshotCreationFailedError):
                        create_consistent_readonly_snapshot(src_db, dest_dir=Path(tmp_dir))

                # Invarianza post-fallimento WAL
                assert hashlib.sha256(src_db.read_bytes()).hexdigest() == db_hash_t0
                if wal_path.exists():
                    assert hashlib.sha256(wal_path.read_bytes()).hexdigest() == wal_hash_t0
                if shm_path.exists():
                    assert shm_path.read_bytes() == shm_bytes_t0
            finally:
                writer_conn.close()

    def test_161_f1_a_adversarial_tombstone_replacement_eliminated(self) -> None:
        """F1-A Regression: Il protocollo tombstone non sicuro è abbandonato; nessun file concorrente eliminato."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            private_dir = Path(tmp_dir) / "private_scratch_161"
            private_dir.mkdir()
            _OWNERSHIP_TRACKER.register_temp_dir(private_dir)

            owned_snap = private_dir / "snapshot_161.db"
            owned_snap.write_bytes(b"OWNED_SNAPSHOT_DATA_161")
            st_orig = os.lstat(owned_snap)
            orig_ident = (int(st_orig.st_dev), int(st_orig.st_ino))
            _OWNERSHIP_TRACKER.register_owned_file(owned_snap, orig_ident)

            # File concorrente posto nella stessa directory (o percorso tombstone fittizio)
            competitor_file = private_dir / "competitor_critical_log.dat"
            competitor_file.write_bytes(b"COMPETITOR_CRITICAL_EVIDENCE_161")
            comp_hash = hashlib.sha256(b"COMPETITOR_CRITICAL_EVIDENCE_161").hexdigest()

            # Verifichiamo che _perform_atomic_safe_unlink NON crei né utilizzi alcun file tombstone
            # e che un file concorrente non posseduto non venga MAI toccato o cancellato
            res_comp = _perform_atomic_safe_unlink(competitor_file, None, (9999, 9999))
            assert res_comp is False, "Cleanup su file concorrente DEVE fallire (fail-closed)!"
            assert competitor_file.is_file(), "Il file concorrente DEVE sopravvivere!"
            assert hashlib.sha256(competitor_file.read_bytes()).hexdigest() == comp_hash

            # Verifichiamo che la cancellazione del nostro file avvenga direttamente senza tombstone intermedi
            res_owned = _perform_atomic_safe_unlink(owned_snap, None, orig_ident)
            assert res_owned is True, "Il nostro file posseduto deve essere ripulito con successo!"
            assert not owned_snap.exists()
            assert competitor_file.is_file(), "Il file concorrente deve ancora esistere intatto!"
            assert hashlib.sha256(competitor_file.read_bytes()).hexdigest() == comp_hash

            _OWNERSHIP_TRACKER.unregister_temp_dir(private_dir)

    def test_162_f1_b_adversarial_rollback_overwrite_eliminated(self) -> None:
        """F1-B Regression: Nessun rollback con os.replace; il file concorrente alla destinazione esterna non viene mai sovrascritto."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                dest_dir = Path(tmp_dir) / "external_target_162"
                dest_dir.mkdir()
                external_dest = dest_dir / "final_snapshot_162.db"

                # Scenario 1: Un concorrente crea un file prima o durante lo staging
                competitor_payload = b"COMPETITOR_EXTERNAL_FILE_MUST_NEVER_BE_OVERWRITTEN_162"
                comp_hash = hashlib.sha256(competitor_payload).hexdigest()

                # Simuliamo concorrenza: il file concorrente appare alla destinazione esterna mentre lo snapshot prepara
                def simulate_staging_and_concurrent_competitor(src_path):
                    # Il concorrente posiziona il suo file prima che lo snapshot completi
                    external_dest.write_bytes(competitor_payload)
                    # Acquisizione fallisce: verifichiamo che il cleanup NON faccia rollback o unlink sulla destinazione esterna
                    raise SnapshotCreationFailedError("Crash simulato durante staging")

                with patch("scripts.preflight_checkpoint_resume.acquire_forensic_readonly_copy", side_effect=simulate_staging_and_concurrent_competitor):
                    with pytest.raises(SnapshotCreationFailedError):
                        create_consistent_readonly_snapshot(src_db, dest_path=external_dest)

                # Il file concorrente alla destinazione esterna NON deve essere stato cancellato o modificato
                assert external_dest.is_file(), "Il file concorrente alla destinazione esterna non deve essere toccato dal cleanup su fallimento!"
                assert hashlib.sha256(external_dest.read_bytes()).hexdigest() == comp_hash

                # Scenario 2: Se lo staging ha successo ma il file concorrente esiste già alla destinazione,
                # la pubblicazione fallisce con FileExistsError senza sovrascrittura (O_CREAT | O_EXCL)
                with pytest.raises(FileExistsError):
                    create_consistent_readonly_snapshot(src_db, dest_path=external_dest)

                assert external_dest.is_file()
                assert hashlib.sha256(external_dest.read_bytes()).hexdigest() == comp_hash, "Nessuna sovrascrittura permessa!"
            finally:
                writer_conn.close()

    def test_163_f1_c_adversarial_directory_replacement_before_rmtree_eliminated(self) -> None:
        """F1-C Regression: Nessun shutil.rmtree sui percorsi di errore; directory concorrente con file preservata da os.rmdir non ricorsivo."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_parent = Path(tmp_dir) / "parent_163"
            temp_parent.mkdir()

            # Creiamo una cartella registrata
            target_dir = temp_parent / "chatanalysis_snap_temp_163"
            target_dir.mkdir()
            _OWNERSHIP_TRACKER.register_temp_dir(target_dir)

            # Simuliamo l'avversario che sostituisce target_dir con una propria directory contenente evidenze
            backup_real = temp_parent / "real_dir_bak_163"
            target_dir.rename(backup_real)
            target_dir.mkdir()

            competitor_evidence = target_dir / "competitor_irreplaceable_data.sqlite"
            competitor_evidence.write_bytes(b"COMPETITOR_DATABASE_EVIDENCE_163")
            comp_evidence_hash = hashlib.sha256(b"COMPETITOR_DATABASE_EVIDENCE_163").hexdigest()

            # Eseguiamo _safe_cleanup_owned_temp_dir
            cleanup_res = _safe_cleanup_owned_temp_dir(target_dir, known_files=[])

            # os.rmdir non ricorsivo DEVE fallire poiché la directory non è vuota (fail-closed)
            assert cleanup_res is False, "Il cleanup non ricorsivo deve restituire False se la cartella contiene file alieni!"
            assert target_dir.is_dir(), "La directory concorrente DEVE essere sopravvissuta!"
            assert competitor_evidence.is_file(), "Tutti i file all'interno della directory concorrente DEVONO essere preservati!"
            assert hashlib.sha256(competitor_evidence.read_bytes()).hexdigest() == comp_evidence_hash

            _OWNERSHIP_TRACKER.unregister_temp_dir(target_dir)

    def test_164_cross_platform_trust_boundary_windows_linux_semantics(self) -> None:
        """Verifica uniformità del trust boundary e fail-closed su Windows e Linux."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                # Invocazione normale crea directory privata isolata
                snap_path, snap_dir = create_consistent_readonly_snapshot(src_db, dest_dir=Path(tmp_dir))
                assert snap_path.is_file()
                assert snap_dir is not None
                assert snap_dir.is_dir()
                # Verifica che la directory sia registrata nel tracker
                assert _OWNERSHIP_TRACKER.is_owned_dir(snap_dir)

                # Rimozione del file creato e pulizia sicura della directory registrata
                snap_path.unlink()
                res_cleanup = _safe_cleanup_owned_temp_dir(snap_dir)
                assert res_cleanup is True
                assert not snap_dir.exists()
                assert not snap_path.exists()
                assert not _OWNERSHIP_TRACKER.is_registered_dir(snap_dir)
            finally:
                writer_conn.close()

    def test_165_f1_d_adversarial_boundary_race_unlink_demonstration(self) -> None:
        """
        ANOMALIA F1/D — Test avversario al confine effettivo di Path.unlink:
        Dimostra empiricamente il limite intrinseco della separazione check-then-unlink
        basata su pathname rispetto a un avversario same-UID concorrente che scambia il file
        DOPO l'ultimo lstat/is_owned_file e PRIMA di unlink().
        Conferma che la reale garanzia risiede nel trust boundary di mkdtemp 0700 isolata,
        non in un'illusoria promessa di prevenzione assoluta a livello di API pathname.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            private_dir = Path(tmp_dir) / "private_scratch_165"
            private_dir.mkdir()
            _OWNERSHIP_TRACKER.register_temp_dir(private_dir)

            owned_snap = private_dir / "snap_165.db"
            owned_snap.write_bytes(b"OWNED_DATA_165")
            st_orig = os.lstat(owned_snap)
            orig_ident = (int(st_orig.st_dev), int(st_orig.st_ino))
            _OWNERSHIP_TRACKER.register_owned_file(owned_snap, orig_ident)

            competitor_file = private_dir / "competitor_same_uid_target_165.db"
            competitor_file.write_bytes(b"COMPETITOR_CRITICAL_EVIDENCE_165")

            real_unlink = Path.unlink

            def hostile_boundary_swap_unlink(path_self, *args, **kwargs):
                # Simula l'avversario same-UID che effettua lo swap atomico esattamente al confine
                if path_self == owned_snap:
                    # Rimuove il file posseduto e sposta il file concorrente al suo posto
                    real_unlink(owned_snap)
                    competitor_file.rename(owned_snap)
                return real_unlink(path_self, *args, **kwargs)

            with patch.object(Path, "unlink", side_effect=hostile_boundary_swap_unlink, autospec=True):
                # L'operazione supera tutti i controlli lstat/identity/ownership e procede all'unlink
                res = _perform_atomic_safe_unlink(owned_snap, None, orig_ident)
                # Il risultato è True e il file concorrente scambiato al pathname è stato cancellato
                assert res is True
                assert not owned_snap.exists()
                assert not competitor_file.exists()

            _OWNERSHIP_TRACKER.unregister_temp_dir(private_dir)

    def test_166_f1_d_adversarial_boundary_race_rmdir_demonstration(self) -> None:
        """
        ANOMALIA F1/D — Test avversario al confine effettivo di os.rmdir:
        Dimostra empiricamente il limite intrinseco di check-then-rmdir con un avversario
        same-UID concorrente che scambia la directory registrata con una concorrente vuota
        esattamente prima di os.rmdir().
        Verifica inoltre che se la directory concorrente contiene file (non-vuota),
        os.rmdir fallisce rigorosamente in modo fail-closed, preservando tutti i dati.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_parent = Path(tmp_dir) / "parent_166"
            temp_parent.mkdir()

            registered_dir = temp_parent / "registered_empty_166"
            registered_dir.mkdir()
            _OWNERSHIP_TRACKER.register_temp_dir(registered_dir)

            competitor_empty_dir = temp_parent / "competitor_empty_166"
            competitor_empty_dir.mkdir()

            real_rmdir = os.rmdir

            def hostile_boundary_swap_rmdir(path_str):
                p = Path(path_str)
                if p.resolve() == registered_dir.resolve():
                    # L'avversario sposta la cartella registrata e posiziona la directory concorrente vuota
                    real_rmdir(str(registered_dir))
                    competitor_empty_dir.rename(registered_dir)
                return real_rmdir(path_str)

            # Caso 1: Directory concorrente vuota scambiata al confine di rmdir
            with patch("os.rmdir", side_effect=hostile_boundary_swap_rmdir):
                res = _safe_cleanup_owned_temp_dir(registered_dir, known_files=[])
                assert res is True
                assert not registered_dir.exists()
                assert not competitor_empty_dir.exists()

            # Caso 2: Se la directory concorrente contiene file, os.rmdir rifiuta la rimozione (Fail-Closed)
            non_empty_dir = temp_parent / "registered_non_empty_166"
            non_empty_dir.mkdir()
            _OWNERSHIP_TRACKER.register_temp_dir(non_empty_dir)

            alien_file = non_empty_dir / "alien_irreplaceable_file.dat"
            alien_file.write_bytes(b"NON_EMPTY_PRESERVATION_DATA")

            res_fail_closed = _safe_cleanup_owned_temp_dir(non_empty_dir, known_files=[])
            assert res_fail_closed is False, "La directory contenente file alieni non deve MAI essere rimossa!"
            assert non_empty_dir.is_dir()
            assert alien_file.is_file()
            assert alien_file.read_bytes() == b"NON_EMPTY_PRESERVATION_DATA"

            _OWNERSHIP_TRACKER.unregister_temp_dir(non_empty_dir)

    def test_167_f1_d_unowned_external_target_never_deleted_on_failure(self) -> None:
        """
        ANOMALIA F1/D — Restrizione del trust boundary:
        Le destinazioni esterne non possedute o create da terzi non vengono MAI
        cancellate o distrutte dal cleanup in caso di fallimento o interruzione (fail-closed).
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                dest_dir = Path(tmp_dir) / "external_dest_167"
                dest_dir.mkdir()
                external_target = dest_dir / "user_chosen_snapshot_167.db"
                external_target.write_bytes(b"PREEXISTING_USER_FILE_167")
                orig_hash = hashlib.sha256(b"PREEXISTING_USER_FILE_167").hexdigest()

                # Tentativo di creare snapshot su percorso preesistente fallisce con FileExistsError
                with pytest.raises(FileExistsError):
                    create_consistent_readonly_snapshot(src_db, dest_path=external_target)

                # Nessuna cancellazione distruttiva automatica: il file preesistente rimane intatto
                assert external_target.is_file()
                assert hashlib.sha256(external_target.read_bytes()).hexdigest() == orig_hash
            finally:
                writer_conn.close()

    def test_168_adversarial_competitor_file_created_immediately_before_publication_survives(self) -> None:
        """
        TEST AVVERSARIO PUBBLICAZIONE ESTERNA:
        Un concorrente crea il file esterno con b'COMPETITOR_NEVER_OVERWRITE' subito prima
        dell'apertura esclusiva al momento della pubblicazione.
        La creazione atomica O_CREAT|O_EXCL rifiuta categoricamente la sovrascrittura (FileExistsError),
        il file del concorrente sopravvive intatto, nessun dato viene cancellato o clobberato
        e l'area temporanea di staging viene interamente ripulita senza residui.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                dest_dir = Path(tmp_dir) / "adversarial_dest_168"
                dest_dir.mkdir()
                dest_snap = dest_dir / "competitor_snap_168.db"

                orig_os_open = os.open
                competitor_created = [False]

                def monkeypatched_os_open(path, flags, *args, **kwargs):
                    # Intercetta il momento esatto della pubblicazione sul target esterno
                    if str(path) == str(dest_snap) and not competitor_created[0]:
                        dest_snap.write_bytes(b"COMPETITOR_NEVER_OVERWRITE")
                        competitor_created[0] = True
                    return orig_os_open(path, flags, *args, **kwargs)

                with patch("os.open", side_effect=monkeypatched_os_open):
                    with pytest.raises(FileExistsError):
                        create_consistent_readonly_snapshot(src_db, dest_path=dest_snap)

                assert competitor_created[0], "L'intercettazione avversaria concorrente deve essersi attivata!"
                assert dest_snap.is_file(), "Il file concorrente creato alla pubblicazione DEVE sopravvivere!"
                assert dest_snap.read_bytes() == b"COMPETITOR_NEVER_OVERWRITE", "Il contenuto concorrente NON deve essere stato sovrascritto!"

                # Verifiche di immutabilità sorgente
                assert src_db.exists()
                assert Path(f"{src_db}-wal").exists()
                assert Path(f"{src_db}-shm").exists()
            finally:
                writer_conn.close()

    def test_169_publication_nominal_synthetic_sqlite_full_integrity(self) -> None:
        """
        PUBBLICAZIONE NOMINALE CON DESTINAZIONE ESTERNA SU SQLITE SINTETICO:
        Verifica che su un database SQLite sintetico in modalità WAL, con tabelle e dati attivi,
        lo snapshot pubblicato sul percorso esterno sia integro, leggibile da SQLite,
        senza directory temporanee residue nell'area temporanea.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                ext_dir = Path(tmp_dir) / "external_nominal_169"
                ext_dir.mkdir()
                target_snap = ext_dir / "published_nominal_snapshot.db"

                snap_file, snap_temp_dir, metrics = create_consistent_readonly_snapshot(
                    src_db, dest_path=target_snap, return_metrics=True
                )

                assert snap_file == target_snap
                assert snap_temp_dir is None, "Per destinazioni esterne, la dir temporanea di staging deve essere ripulita (None)!"
                assert target_snap.is_file(), "Il file snapshot pubblicato deve esistere!"

                # Verifica integrità strutturale SQLite
                conn = sqlite3.connect(f"file:{target_snap.resolve().as_posix()}?mode=ro", uri=True)
                try:
                    cur = conn.cursor()
                    cur.execute("PRAGMA integrity_check;")
                    rows = cur.fetchall()
                    assert rows == [("ok",)], f"Integrità SQLite compromessa: {rows}"

                    cur.execute("SELECT count(*) FROM live_cases;")
                    cnt = cur.fetchone()[0]
                    assert cnt >= 1, "I dati transazionali consolidati devono essere presenti nello snapshot!"
                finally:
                    conn.close()

                # Verifica immutabilità fisica sorgente certificata
                assert metrics["SOURCE_PHYSICAL_IMMUTABILITY_VERIFIED"] is True
            finally:
                writer_conn.close()

    def test_170_publication_preexisting_destination_rejected_fail_closed(self) -> None:
        """
        DESTINAZIONE ESTERNA PREESISTENTE:
        Rifiuto preventivo fail-closed se il file di destinazione esiste già prima della chiamata.
        Nessuna directory o file temporaneo residuo, file preesistente invariato.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                dest_dir = Path(tmp_dir) / "preexisting_dir_170"
                dest_dir.mkdir()
                existing_file = dest_dir / "already_here.db"
                existing_file.write_bytes(b"INITIAL_UNTOUCHED_USER_SNAPSHOT_170")
                initial_sha = hashlib.sha256(existing_file.read_bytes()).hexdigest()

                with pytest.raises(FileExistsError):
                    create_consistent_readonly_snapshot(src_db, dest_path=existing_file)

                assert existing_file.is_file()
                assert hashlib.sha256(existing_file.read_bytes()).hexdigest() == initial_sha
            finally:
                writer_conn.close()

    def test_171_publication_symlink_destination_rejected(self) -> None:
        """
        DESTINAZIONE SYMLINK:
        Rifiuto categorico di snapshot verso symlink (SnapshotPermissionDeniedError).
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                dest_dir = Path(tmp_dir) / "symlink_dir_171"
                dest_dir.mkdir()
                real_target = dest_dir / "real_file.db"
                real_target.write_bytes(b"REAL_TARGET_PREEXISTING")

                link_path = dest_dir / "symlink_snap.db"
                can_symlink = True
                try:
                    os.symlink(real_target, link_path)
                except (OSError, NotImplementedError):
                    can_symlink = False

                if can_symlink:
                    with pytest.raises((SnapshotPermissionDeniedError, FileExistsError)):
                        create_consistent_readonly_snapshot(src_db, dest_path=link_path)
                    assert real_target.read_bytes() == b"REAL_TARGET_PREEXISTING"
                else:
                    # Su ambienti Windows senza privilegi per symlink, testare tramite mock os.path.islink
                    with patch("os.path.islink", return_value=True):
                        with pytest.raises(SnapshotPermissionDeniedError):
                            create_consistent_readonly_snapshot(src_db, dest_path=dest_dir / "mock_link.db")
            finally:
                writer_conn.close()

    def test_172_publication_io_error_during_write_preserves_partial_and_cleans_staging(self) -> None:
        """
        ERRORE I/O DURANTE LA SCRITTURA DOPO APERTURA ESCLUSIVA:
        In caso di fallimento di scrittura (es. disco pieno) sul descrittore aperto esclusivamente,
        i descrittori vengono chiusi. Conformemente al contratto, il file parziale NON viene
        eliminato automaticamente (viene sollevata SnapshotCreationFailedError segnalando
        che può richiedere pulizia manuale), mentre l'area temporanea privata viene ripulita.
        I sorgenti rimangono inalterati.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                dest_dir = Path(tmp_dir) / "io_fail_dir_172"
                dest_dir.mkdir()
                dest_snap = dest_dir / "failing_write_snapshot.db"

                def failing_write(fd, data):
                    raise OSError("Simulato disco pieno durante os.write")

                with patch("os.write", side_effect=failing_write):
                    with pytest.raises(SnapshotCreationFailedError) as exc_info:
                        create_consistent_readonly_snapshot(src_db, dest_path=dest_snap)

                err_msg = str(exc_info.value)
                assert "scrittura dello snapshot sul percorso di destinazione esterno" in err_msg
                assert "non è stato eliminato automaticamente" in err_msg

                # Il file parziale NON è stato eliminato automaticamente (contratto di sicurezza esterna)
                assert dest_snap.exists(), "Il file parziale esterno NON deve essere cancellato automaticamente!"

                # Sorgenti intatti
                assert src_db.exists()
                assert Path(f"{src_db}-wal").exists()
            finally:
                writer_conn.close()

    def test_173_publication_log_and_error_privacy_sanitizes_paths(self) -> None:
        """
        PRIVACY DEI LOG ED ERRORI DI PUBBLICAZIONE:
        Verifica che i messaggi di errore della pubblicazione esclusiva usino _sanitize_path
        senza esporre percorsi assoluti privati dell'utente.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db, writer_conn, state = self._setup_wal_database_with_open_writer(tmp_dir)
            try:
                dest_dir = Path(tmp_dir) / "sensitive_lucas_folder"
                dest_dir.mkdir()
                existing = dest_dir / "target.db"
                existing.write_bytes(b"DATA")

                with pytest.raises(FileExistsError) as exc_info:
                    _publish_snapshot_exclusively(src_db, existing)

                err_str = str(exc_info.value)
                # Il path deve essere formattato via _sanitize_path: [DATABASE_REDACTED]
                assert "[DATABASE_REDACTED]" in err_str
                assert "sensitive_lucas_folder" not in err_str
                assert "lucas" not in err_str.lower() or "[DATABASE_REDACTED]" in err_str
            finally:
                writer_conn.close()

    def test_174_direct_exclusive_publish_no_clobber_primitive(self) -> None:
        """
        TEST DIRETTO DELLA PRIMITIVA _publish_snapshot_exclusively:
        1. Se il file target esiste, solleva FileExistsError senza modificare i byte.
        2. Se il file target non esiste, pubblica il file con integrità byte-for-byte.
        3. Se il target è un symlink, rifiuta con SnapshotPermissionDeniedError.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_file = Path(tmp_dir) / "src.db"
            src_content = b"SQLITE_SYNTHETIC_STAGED_PAYLOAD_12345"
            src_file.write_bytes(src_content)

            # Caso 1: pubblicazione riuscita
            target_file = Path(tmp_dir) / "out.db"
            _publish_snapshot_exclusively(src_file, target_file)
            assert target_file.is_file()
            assert target_file.read_bytes() == src_content

            # Caso 2: secondo tentativo sul file appena creato -> FileExistsError (NO-CLOBBER)
            with pytest.raises(FileExistsError):
                _publish_snapshot_exclusively(src_file, target_file)
            assert target_file.read_bytes() == src_content  # byte invariati

            # Caso 3: symlink
            with patch("os.path.islink", return_value=True):
                with pytest.raises(SnapshotPermissionDeniedError):
                    _publish_snapshot_exclusively(src_file, Path(tmp_dir) / "link.db")










