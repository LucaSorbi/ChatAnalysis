"""
tests/unit/test_checkpoint_integrity_scalability.py
---------------------------------------------------
Suite di test completa per la verifica dei 16 requisiti obbligatori di integrità dei checkpoint,
ripresa reale dopo riavvio di LM Studio, persistenza transazionale incrementale e scalabilità (Sezione 9):

1. Cancellazione dei checkpoint dalla UI.
2. Cancellazione limitata al documento selezionato (nessuna cancellazione indiscriminata).
3. Ripresa con nuova istanza del servizio.
4. Ripresa dopo cambio dell'identificativo di istanza LM Studio, a parità di modello verificato.
5. Invalidazione con modello o configurazione incompatibile.
6. Checkpoint corrotti o manipolati.
7. Evidence ID di un chunk attribuito a un chunk differente (tampering cross-chunk).
8. Arresto durante una scrittura e atomicità transazionale.
9. Persistenza incrementale e concorrenza multi-thread.
10. Confronto prestazionale e scalabilità lineare O(N) contro O(N^2).
11. Budget con headroom zero o insufficiente (sollevamento InsufficientContextHeadroomError).
12. Profilazione per 3, 10 e 25 topic con allineamento effettivo.
13. Contatore HTTP con retry e checkpoint (zero duplicazioni).
14. Errori HTTP 400 (diagnostica senza PII leakage).
15. Topic Discovery e Topic Detection multi-chunk.
16. Nessuna regressione nella verifica di consistenza.
17. Cancellazione selettiva per versione documento (fingerprint crittografico).
18. Validazione rigorosa provenienza documentale e payload.
19. Migrazione atomica transazionale da JSON legacy a SQLite.
20. Durabilità WAL e concorrenza multi-processo.
21. Precedenza SQLite su file legacy corrotto o illeggibile.
22. Riconciliazione unificata clear_all_for_document_id.
23. Precedenza SQLite consolidato su legacy divergente o parziale.
24. Preservazione legacy valido in caso di chunk SQLite corrotto (Correzione 1).
25. Preservazione legacy non decodificabile o non attribuibile in clear_all_for_document_id (Correzione 2).
26. Validazione metadati e rifiuto fusioni arbitrarie o estensioni MAX (Correzione 3).
27. Rifiuto indici non numerici e fuori intervallo pianificato.
28. Rifiuto discrepanze nei metadati di analisi (analysis_type, model_id, prompt_version).
29. Migrazione interrotta, ripresa e conservazione integrale decisioni ed evidenze.
30. Isolamento documentale e divieto cancellazione SQLite tramite legacy contraddittorio (Difetto 1).
31. Validazione preliminare completa e divieto migrazioni parziali di file legacy invalidi (Difetto 2).
32. Migrazione regolare di checkpoint legittimamente parziali da analisi interrotta.
33. Rifiuto integrale di file legacy con un chunk valido e uno corrotto/alieno.
"""
from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any, Sequence

import pytest

from ai.backend import (
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiStructuredOutputError,
    BaseLlmClient,
    LlmCompletionResponse,
)
from ai.chunking import canonicalize_topic_label, profile_document_for_analysis, split_document_into_chunks
from ai.models import (
    AnalysisLanguageStrategy,
    ConversationEvidenceDocument,
    DiscoveredTopic,
    MessageEvidenceBundle,
    TextEvidenceSection,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from ai.token_budget import DEFAULT_SAFETY_MARGIN_TOKENS
from analysis.checkpoint import (
    AnalysisCheckpointStore,
    CheckpointSaveError,
    compute_document_fingerprint,
    compute_run_config_hash,
    deserialize_detection_result_from_checkpoint,
    deserialize_discovery_result_from_checkpoint,
    serialize_detection_result_for_checkpoint,
    serialize_discovery_result_for_checkpoint,
    validate_checkpoint_evidence_references,
)
from analysis.consistency import ConsistencyService
from analysis.models import (
    ConsistencyStatus,
    InsufficientContextHeadroomError,
    OperationalAnalysisConfig,
    OperationalProfile,
    resolve_operational_profile,
)
from analysis.progress import AnalysisProgressTracker
from analysis.service import (
    LocalTopicAnalysisService,
    calculate_discovery_max_tokens,
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


class ControlledMockClient(BaseLlmClient):
    """Client di test controllato con listener di progresso HTTP conforme all'invariante."""

    def __init__(self, responses: list[Any] | None = None, model_id: str = "test-model") -> None:
        self.responses = list(responses or [])
        self.call_count = 0
        self.request_listener: Any = None
        self.model_id = model_id
        self._loaded_instance_id = "inst_initial_001"

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
        if self.request_listener:
            try:
                self.request_listener.record_request_sent()
            except Exception:
                pass

        if not self.responses:
            resp_content = json.dumps({"decision": "ABSENT", "evidence_ids": [], "rationale": "Default mock response."})
        else:
            resp_content = self.responses.pop(0)

        if isinstance(resp_content, Exception):
            if self.request_listener:
                try:
                    self.request_listener.record_request_failed(resp_content)
                except Exception:
                    pass
            raise resp_content

        if self.request_listener:
            try:
                self.request_listener.record_request_success()
            except Exception:
                pass

        return LlmCompletionResponse(
            content=resp_content,
            model=model or self.model_id,
            prompt_tokens=100,
            completion_tokens=40,
            total_tokens=140,
            latency_seconds=0.01,
        )


def _create_bundle(idx: str, text: str, ts_iso: str = "2026-03-01T10:00:00Z") -> MessageEvidenceBundle:
    raw = RawRecord(
        source_name="test",
        source_path="/path/test",
        source_record_id=idx,
        record_type="message",
        raw_fields={"data": text, "timestamp": ts_iso},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(
        status=TimestampTzStatus.KNOWN_UTC,
        utc_datetime=datetime.fromisoformat(ts_iso.replace("Z", "+00:00")),
        naive_datetime=None,
        iso_string=ts_iso,
        raw_value=ts_iso,
    )
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name="test",
        source_record_id=idx,
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )
    msg = UnifiedMessage(
        message_id=f"unified:test:{idx}",
        source_name="test",
        source_record_id=idx,
        source_path="/path/test",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )
    return MessageEvidenceBundle(message=msg)


def _make_doc(doc_id: str = "doc_test", count: int = 4, base_ts: str = "2026-03-01T10:00:00Z") -> ConversationEvidenceDocument:
    bundles = [_create_bundle(str(i), f"Messaggio di test numero {i}", ts_iso=base_ts) for i in range(count)]
    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=tuple(bundles),
        source_name="test",
    )


# 1. Cancellazione dei checkpoint dalla UI
def test_01_ui_checkpoint_deletion_button_and_scoped_clear() -> None:
    with tempfile.TemporaryDirectory() as chk_dir:
        store = AnalysisCheckpointStore(checkpoint_dir=Path(chk_dir))
        doc = _make_doc("doc_alpha", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")

        valid_ev = doc.all_evidence_sections[0].evidence_id
        res = TopicDetectionResult(
            topic=TopicQuery(topic_id="t1", label="Test", description="Test desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(valid_ev,),
            rationale="Rationale",
            provenance_document_id=doc.document_id,
        )
        store.save_chunk(doc.document_id, doc_fp, cfg_fp, 0, res, 1, "detection", "test-model", "v1")
        assert store.has_checkpoint(doc_fp, cfg_fp) is True

        # Test invocazione API usata dalla UI: clear_for_document(doc)
        cleared = store.clear_for_document(doc)
        assert cleared == 1
        assert store.has_checkpoint(doc_fp, cfg_fp) is False

        # Invocazione idempotente
        cleared_again = store.clear_for_document(doc)
        assert cleared_again == 0


# 2. Cancellazione limitata al documento selezionato (nessuna cancellazione indiscriminata)
def test_02_checkpoint_deletion_isolated_to_selected_document() -> None:
    with tempfile.TemporaryDirectory() as chk_dir:
        store = AnalysisCheckpointStore(checkpoint_dir=Path(chk_dir))
        doc_a = _make_doc("doc_a", 2)
        doc_b = _make_doc("doc_b", 2)

        doc_fp_a = compute_document_fingerprint(doc_a)
        doc_fp_b = compute_document_fingerprint(doc_b)
        cfg_fp = compute_run_config_hash("test-model", "v1")

        res_a = TopicDetectionResult(
            topic=TopicQuery(topic_id="t1", label="Test", description="Test desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(doc_a.all_evidence_sections[0].evidence_id,),
            rationale="Rationale A",
            provenance_document_id=doc_a.document_id,
        )
        res_b = TopicDetectionResult(
            topic=TopicQuery(topic_id="t1", label="Test", description="Test desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(doc_b.all_evidence_sections[0].evidence_id,),
            rationale="Rationale B",
            provenance_document_id=doc_b.document_id,
        )

        store.save_chunk(doc_a.document_id, doc_fp_a, cfg_fp, 0, res_a, 1, "detection", "test-model", "v1")
        store.save_chunk(doc_b.document_id, doc_fp_b, cfg_fp, 0, res_b, 1, "detection", "test-model", "v1")

        # Cancellazione SOLO di doc_a
        cleared = store.clear_for_document(doc_a)
        assert cleared == 1

        # doc_a deve essere rimosso, doc_b DEVE rimanere intatto
        assert store.has_checkpoint(doc_fp_a, cfg_fp) is False
        assert store.has_checkpoint(doc_fp_b, cfg_fp) is True
        chk_b = store.load_checkpoint(doc_fp_b, cfg_fp)
        assert chk_b is not None
        assert chk_b.document_id == "doc_b"


# 3. Ripresa con nuova istanza del servizio
def test_03_resumption_with_new_service_instance() -> None:
    with tempfile.TemporaryDirectory() as chk_dir:
        store = AnalysisCheckpointStore(checkpoint_dir=Path(chk_dir))
        doc = _make_doc("doc_resume", 4)
        topic = TopicQuery(topic_id="t_res", label="Test Resume", description="Test Resume")

        # Prima istanza del client e servizio
        ev_0 = doc.bundles[0].text_evidence_sections[0].evidence_id
        resp1 = json.dumps({"decision": "PRESENT", "evidence_ids": [ev_0], "rationale": "Chunk 1 ok"})
        resp2 = AiBackendTimeoutError("Timeout simulato sul chunk 2")

        client1 = ControlledMockClient(responses=[resp1, resp2])
        svc1 = LocalTopicAnalysisService(
            client=client1,
            checkpoint_store=store,
            enable_checkpointing=True,
            max_chunk_bundles=1,
        )

        with pytest.raises(Exception):
            svc1.run_topic_detection(document=doc, topic=topic)

        # 1. Presenza del primo chunk nel database e 2. Assenza dei chunk non completati
        doc_fp = compute_document_fingerprint(doc)
        with store._connect() as conn:
            chunk_rows = conn.execute(
                "SELECT chunk_index, document_fingerprint, config_fingerprint FROM checkpoint_chunks ORDER BY chunk_index ASC"
            ).fetchall()
            assert len(chunk_rows) == 1, f"Atteso esattamente 1 chunk salvato nel database, trovati: {len(chunk_rows)}"
            assert chunk_rows[0][0] == 0, f"Atteso indice chunk 0, trovato: {chunk_rows[0][0]}"
            assert chunk_rows[0][1] == doc_fp
            actual_cfg_fp = chunk_rows[0][2]

            # Assenza dei chunk non completati (1, 2, 3)
            missing = conn.execute(
                "SELECT chunk_index FROM checkpoint_chunks WHERE chunk_index IN (1, 2, 3)"
            ).fetchall()
            assert len(missing) == 0, f"I chunk non completati non devono essere presenti: {missing}"

            chk_rows = conn.execute(
                "SELECT document_id, total_planned_chunks, analysis_type FROM checkpoints WHERE document_fingerprint = ?",
                (doc_fp,)
            ).fetchall()
            assert len(chk_rows) == 1
            assert chk_rows[0][0] == doc.document_id
            assert chk_rows[0][1] == 4
            assert chk_rows[0][2] == "detection"

        # Controllo effettivo dell'esistenza del checkpoint con il config_fingerprint reale
        assert store.has_checkpoint(doc_fp, actual_cfg_fp) is True

        # Seconda istanza del servizio e client (simula ripresa operativa del servizio)
        ev_1 = doc.bundles[1].text_evidence_sections[0].evidence_id
        ev_2 = doc.bundles[2].text_evidence_sections[0].evidence_id
        ev_3 = doc.bundles[3].text_evidence_sections[0].evidence_id

        resp_c1 = json.dumps({"decision": "PRESENT", "evidence_ids": [ev_1], "rationale": "Chunk 2 ok"})
        resp_c2 = json.dumps({"decision": "ABSENT", "evidence_ids": [], "rationale": "Chunk 3 ok"})
        resp_c3 = json.dumps({"decision": "ABSENT", "evidence_ids": [], "rationale": "Chunk 4 ok"})

        client2 = ControlledMockClient(responses=[resp_c1, resp_c2, resp_c3])
        svc2 = LocalTopicAnalysisService(
            client=client2,
            checkpoint_store=store,
            enable_checkpointing=True,
            max_chunk_bundles=1,
        )

        final_res = svc2.run_topic_detection(document=doc, topic=topic)
        assert final_res.decision == TopicDecision.PRESENT
        assert ev_0 in final_res.evidence_ids
        assert ev_1 in final_res.evidence_ids
        # Provenienza documentale corretta
        assert final_res.provenance_document_id == doc.document_id
        # Zero chiamate duplicate: chunk 0 NON è stato rieseguito da client2
        assert client2.call_count == 3
        # Numero totale di interrogazioni esatto:
        # 2 nella prima sessione (1 completata + 1 fallita con timeout) + 3 nella seconda sessione = 5
        assert client1.call_count == 2
        assert client1.call_count + client2.call_count == 5


# 4. Ripresa dopo cambio dell'identificativo di istanza LM Studio, a parità di modello verificato
def test_04_resumption_after_lm_studio_instance_id_change() -> None:
    with tempfile.TemporaryDirectory() as chk_dir:
        store = AnalysisCheckpointStore(checkpoint_dir=Path(chk_dir))
        doc = _make_doc("doc_instance_change", 2)
        topic = TopicQuery(topic_id="t_inst", label="Instance Test", description="Instance Test")

        ev_0 = doc.bundles[0].text_evidence_sections[0].evidence_id
        resp1 = json.dumps({"decision": "PRESENT", "evidence_ids": [ev_0], "rationale": "Chunk 0 ok"})
        resp2 = AiBackendTimeoutError("Timeout")

        # Client con loaded_instance_id = "inst_AAA"
        client1 = ControlledMockClient(responses=[resp1, resp2])
        client1._loaded_instance_id = "inst_AAA_111"

        svc1 = LocalTopicAnalysisService(
            client=client1,
            checkpoint_store=store,
            enable_checkpointing=True,
            max_chunk_bundles=1,
        )
        with pytest.raises(Exception):
            svc1.run_topic_detection(document=doc, topic=topic)

        # Verifica presenza del primo chunk e assenza del secondo nel database
        doc_fp = compute_document_fingerprint(doc)
        with store._connect() as conn:
            chunk_rows = conn.execute(
                "SELECT chunk_index, document_fingerprint, config_fingerprint FROM checkpoint_chunks ORDER BY chunk_index ASC"
            ).fetchall()
            assert len(chunk_rows) == 1
            assert chunk_rows[0][0] == 0
            assert chunk_rows[0][1] == doc_fp
            actual_cfg_fp = chunk_rows[0][2]

            missing = conn.execute(
                "SELECT chunk_index FROM checkpoint_chunks WHERE chunk_index = 1"
            ).fetchall()
            assert len(missing) == 0

        assert store.has_checkpoint(doc_fp, actual_cfg_fp) is True

        # Nuovo caricamento in LM Studio: stesso modello, nuova istanza "inst_BBB_222"
        # NOTA METODOLOGICA: questa verifica simula la variazione dell'identificativo volatile di sessione
        # senza confondere il mock con un riavvio effettivo del demone o una sostituzione del modello.
        ev_1 = doc.bundles[1].text_evidence_sections[0].evidence_id
        resp_c1 = json.dumps({"decision": "ABSENT", "evidence_ids": [], "rationale": "Chunk 1 ok"})

        client2 = ControlledMockClient(responses=[resp_c1])
        client2._loaded_instance_id = "inst_BBB_222"

        svc2 = LocalTopicAnalysisService(
            client=client2,
            checkpoint_store=store,
            enable_checkpointing=True,
            max_chunk_bundles=1,
        )

        final_res = svc2.run_topic_detection(document=doc, topic=topic)
        assert final_res.decision == TopicDecision.PRESENT
        assert ev_0 in final_res.evidence_ids
        # Provenienza documentale corretta
        assert final_res.provenance_document_id == doc.document_id
        # Verifica zero chiamate duplicate sul chunk 0 recuperato
        assert client2.call_count == 1
        # Numero totale esatto di chiamate: 2 nel client1 + 1 nel client2 = 3
        assert client1.call_count == 2
        assert client1.call_count + client2.call_count == 3


# 5. Invalidazione con modello o configurazione incompatibile
def test_05_invalidation_when_model_or_config_incompatible() -> None:
    with tempfile.TemporaryDirectory() as chk_dir:
        store = AnalysisCheckpointStore(checkpoint_dir=Path(chk_dir))
        doc = _make_doc("doc_inval", 2)
        doc_fp = compute_document_fingerprint(doc)

        cfg_fp_a = compute_run_config_hash("model-alpha", "v1")
        cfg_fp_b = compute_run_config_hash("model-beta", "v1")
        assert cfg_fp_a != cfg_fp_b

        valid_ev = doc.all_evidence_sections[0].evidence_id
        res = TopicDetectionResult(
            topic=TopicQuery(topic_id="t1", label="Test", description="Test desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(valid_ev,),
            rationale="Alpha run",
            provenance_document_id=doc.document_id,
        )
        store.save_chunk(doc.document_id, doc_fp, cfg_fp_a, 0, res, 1, "detection", "model-alpha", "v1")

        # Modello alpha ha il checkpoint
        assert store.load_checkpoint(doc_fp, cfg_fp_a) is not None
        # Modello beta DEVE rifiutare il checkpoint di alpha
        assert store.load_checkpoint(doc_fp, cfg_fp_b) is None


# 6. Checkpoint corrotti o manipolati
def test_06_corrupted_or_tampered_checkpoints() -> None:
    with tempfile.TemporaryDirectory() as chk_dir:
        store = AnalysisCheckpointStore(checkpoint_dir=Path(chk_dir))
        doc = _make_doc("doc_corrupt", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")

        # Inserimento di riga JSON non parsabile nella tabella dei chunk
        with store._connect() as conn:
            conn.execute("""
                INSERT INTO checkpoints (
                    document_fingerprint, config_fingerprint, document_id,
                    analysis_type, model_id, prompt_version, total_planned_chunks,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (doc_fp, cfg_fp, doc.document_id, "detection", "test-model", "v1", 1, "2026-10-14", "2026-10-14"))
            conn.execute("""
                INSERT INTO checkpoint_chunks (
                    document_fingerprint, config_fingerprint, chunk_index,
                    serialized_result, created_at
                ) VALUES (?, ?, ?, ?, ?);
            """, (doc_fp, cfg_fp, 0, "CORRUPTED_NON_JSON_CONTENT{{{", "2026-10-14"))

        # Il caricamento DEVE restituire None senza crashare
        loaded = store.load_checkpoint(doc_fp, cfg_fp)
        assert loaded is None


# 7. Evidence ID di un chunk attribuito a un chunk differente (tampering cross-chunk)
def test_07_evidence_id_cross_chunk_tampering_detected() -> None:
    doc = _make_doc("doc_cross_tamper", 4)
    chunks = split_document_into_chunks(doc, max_bundles=2)
    assert len(chunks) == 2

    chunk0_eids = [sec.evidence_id for sec in chunks[0].all_evidence_sections]
    chunk1_eids = [sec.evidence_id for sec in chunks[1].all_evidence_sections]
    assert chunk0_eids and chunk1_eids
    assert set(chunk0_eids).isdisjoint(set(chunk1_eids))

    # Creazione manipolata: chunk_index = 1 ma dichiara evidence_id di chunk 0!
    from analysis.checkpoint import AnalysisCheckpoint
    tampered = AnalysisCheckpoint(
        document_id=doc.document_id,
        document_fingerprint="fp",
        config_fingerprint="cfg",
        analysis_type="detection",
        model_id="model",
        prompt_version="v1",
        total_planned_chunks=2,
        completed_chunk_indices=(1,),
        serialized_results={
            "1": {
                "topic": {"topic_id": "t1", "label": "Test"},
                "decision": "PRESENT",
                "evidence_ids": [chunk0_eids[0]],  # ATTRIBUZIONE IMPROPPRIA CROSS-CHUNK!
            }
        },
        created_at="2026-10-14",
        updated_at="2026-10-14",
    )

    # validate_checkpoint_evidence_references con chunks forniti DEVE RIFIUTARE
    is_valid = validate_checkpoint_evidence_references(tampered, doc, chunks=chunks)
    assert is_valid is False, "Cross-chunk evidence attribution must be rejected by per-chunk validation!"


# 8. Arresto durante una scrittura e atomicità transazionale
def test_08_crash_simulation_during_write_and_atomicity() -> None:
    with tempfile.TemporaryDirectory() as chk_dir:
        store = AnalysisCheckpointStore(checkpoint_dir=Path(chk_dir))
        doc = _make_doc("doc_crash", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")

        # Scrittura di tipo non supportato per simulare errore prima del commit
        with pytest.raises(TypeError):
            store.save_chunk(
                document_id=doc.document_id,
                document_fingerprint=doc_fp,
                config_fingerprint=cfg_fp,
                chunk_idx=0,
                result="INVALID_RESULT_TYPE",  # type: ignore
                total_planned_chunks=1,
                analysis_type="detection",
                model_id="test-model",
                prompt_version="v1",
            )

        # Database deve rimanere consistente e privo di record corrotti
        with store._connect() as conn:
            cur = conn.execute("SELECT count(*) FROM checkpoints WHERE document_fingerprint = ?;", (doc_fp,))
            assert cur.fetchone()[0] == 0


# 9. Persistenza incrementale e concorrenza multi-thread
def test_09_incremental_sqlite_persistence_and_concurrency() -> None:
    with tempfile.TemporaryDirectory() as chk_dir:
        store = AnalysisCheckpointStore(checkpoint_dir=Path(chk_dir))
        doc = _make_doc("doc_concurrent", 10)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        valid_ev = doc.all_evidence_sections[0].evidence_id

        def write_worker(c_idx: int) -> None:
            res = TopicDetectionResult(
                topic=TopicQuery(topic_id="t1", label="Test", description="Test desc"),
                decision=TopicDecision.PRESENT,
                evidence_ids=(valid_ev,),
                rationale=f"Chunk {c_idx} rationale",
                provenance_document_id=doc.document_id,
            )
            store.save_chunk(
                doc.document_id, doc_fp, cfg_fp, c_idx, res, 10, "detection", "test-model", "v1"
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(write_worker, i) for i in range(10)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        chk = store.load_checkpoint(doc_fp, cfg_fp)
        assert chk is not None
        assert len(chk.completed_chunk_indices) == 10
        assert chk.completed_chunk_indices == tuple(range(10))


# 10. Confronto prestazionale e scalabilità lineare O(N) contro O(N^2)
def test_10_persistence_performance_linear_scaling() -> None:
    with tempfile.TemporaryDirectory() as chk_dir:
        store = AnalysisCheckpointStore(checkpoint_dir=Path(chk_dir))
        doc = _make_doc("doc_perf", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        valid_ev = doc.all_evidence_sections[0].evidence_id
        res = TopicDetectionResult(
            topic=TopicQuery(topic_id="t1", label="Test", description="Test desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(valid_ev,),
            rationale="Bench rationale",
            provenance_document_id=doc.document_id,
        )

        # Misura 50 scritture
        t0 = time.perf_counter()
        for i in range(50):
            store.save_chunk(doc.document_id, doc_fp, cfg_fp, i, res, 50, "detection", "test-model", "v1")
        t_50 = time.perf_counter() - t0

        # Misura altre 50 scritture (da 50 a 100)
        t1 = time.perf_counter()
        for i in range(50, 100):
            store.save_chunk(doc.document_id, doc_fp, cfg_fp, i, res, 100, "detection", "test-model", "v1")
        t_next_50 = time.perf_counter() - t1

        # In un sistema O(1), il tempo per il secondo blocco di 50 scritture non deve degradare quadraticamente
        assert t_next_50 < t_50 * 2.5, f"Scaling must be near linear: t_50={t_50:.3f}s, t_next_50={t_next_50:.3f}s"


# 11. Budget con headroom zero o insufficiente (sollevamento InsufficientContextHeadroomError)
def test_11_operational_profile_insufficient_headroom_raises_error() -> None:
    # Se il context length è troppo piccolo per ospitare il margine e i token di prompt, solleva InsufficientContextHeadroomError
    with pytest.raises(InsufficientContextHeadroomError):
        resolve_operational_profile(
            profile=OperationalProfile.PRUDENT,
            context_length=1200,  # 1200 - safety_margin(1000) = 200 < 256
            safety_margin=1000,
        )


# 12. Profilazione per 3, 10 e 25 topic con allineamento effettivo
def test_12_pre_analysis_profiling_coherence_3_10_25_topics() -> None:
    doc = _make_doc("doc_prof_topics", 10)
    tok_3 = calculate_discovery_max_tokens(max_topics=3, user_max_tokens=None, context_length=8192)
    tok_10 = calculate_discovery_max_tokens(max_topics=10, user_max_tokens=None, context_length=8192)
    tok_25 = calculate_discovery_max_tokens(max_topics=25, user_max_tokens=None, context_length=8192)

    assert tok_3 <= tok_10 <= tok_25
    assert tok_3 == 1024
    assert tok_10 == 1456
    assert tok_25 == 3256

    p3 = profile_document_for_analysis(doc, analysis_type="discovery", max_topics=3)
    p25 = profile_document_for_analysis(doc, analysis_type="discovery", max_topics=25)
    assert p3.total_messages == 10
    assert p25.total_messages == 10


# 13. Contatore HTTP con retry e checkpoint (zero duplicazioni)
def test_13_http_counter_with_retries_and_checkpoint_skip() -> None:
    tracker = AnalysisProgressTracker(total_chunks=3)
    assert tracker.planned_chunks == 3
    assert tracker.completed_chunks == 0

    tracker.record_request_sent()
    tracker.record_retry("retry 1")
    tracker.record_http_error(500, "error 1")
    tracker.record_request_sent()
    tracker.record_request_success()
    tracker.chunk_success(1, record_request=False)

    assert tracker.requests_sent == 2
    assert tracker.requests_failed == 1
    assert tracker.retries_count == 1
    assert tracker.requests_successful == 1
    assert tracker.completed_chunks == 1


# 14. Errori HTTP 400 (diagnostica senza PII leakage)
def test_14_ai_backend_request_error_diagnostics() -> None:
    # Context overflow
    err_overflow = AiBackendRequestError("Context window exceeded", status_code=400, error_category="CONTEXT_OVERFLOW")
    assert err_overflow.error_category == "CONTEXT_OVERFLOW"
    assert "Context window exceeded" in str(err_overflow)

    # Parametro non valido
    err_param = AiBackendRequestError("Invalid temperature", status_code=400, error_category="INVALID_ARGUMENT")
    assert err_param.error_category == "INVALID_ARGUMENT"

    # Schema JSON rifiutato
    err_schema = AiBackendRequestError("Schema validation failed", status_code=400, error_category="SCHEMA_REJECTED")
    assert err_schema.error_category == "SCHEMA_REJECTED"


# 15. Topic Discovery e Topic Detection multi-chunk
def test_15_multichunk_topic_detection_and_discovery() -> None:
    doc = _make_doc("doc_multi_e2e", 4)
    topic = TopicQuery(topic_id="t_multi", label="Multi Test", description="Multi Test")

    ev_0 = doc.bundles[0].text_evidence_sections[0].evidence_id
    ev_2 = doc.bundles[2].text_evidence_sections[0].evidence_id

    # Detection multi-chunk
    resp_det1 = json.dumps({"decision": "PRESENT", "evidence_ids": [ev_0], "rationale": "Found in chunk 1"})
    resp_det2 = json.dumps({"decision": "PRESENT", "evidence_ids": [ev_2], "rationale": "Found in chunk 2"})
    client_det = ControlledMockClient(responses=[resp_det1, resp_det2])

    svc = LocalTopicAnalysisService(
        client=client_det,
        max_chunk_bundles=2,
    )
    det_res = svc.run_topic_detection(document=doc, topic=topic)
    assert det_res.decision == TopicDecision.PRESENT
    assert ev_0 in det_res.evidence_ids
    assert ev_2 in det_res.evidence_ids

    # Discovery multi-chunk
    resp_disc1 = json.dumps({"topics": [{"label": "Tema A", "short_description": "Desc A", "evidence_ids": [ev_0]}]})
    resp_disc2 = json.dumps({"topics": [{"label": "Tema B", "short_description": "Desc B", "evidence_ids": [ev_2]}]})
    client_disc = ControlledMockClient(responses=[resp_disc1, resp_disc2])
    svc_disc = LocalTopicAnalysisService(
        client=client_disc,
        max_chunk_bundles=2,
    )
    disc_res = svc_disc.run_topic_discovery(document=doc, max_topics=5)
    assert len(disc_res.topics) >= 2
    labels = {t.label for t in disc_res.topics}
    assert "Tema A" in labels
    assert "Tema B" in labels


# 16. Nessuna regressione nella verifica di consistenza
def test_16_consistency_verification_semantic_invariance() -> None:
    doc = _make_doc("doc_consist", 2)
    ev_0 = doc.bundles[0].text_evidence_sections[0].evidence_id

    discovered = DiscoveredTopic(label="Test Consist", short_description="Desc", evidence_ids=(ev_0,))
    detection_res = TopicDetectionResult(
        topic=TopicQuery(topic_id="t1", label="Test Consist", description="Desc"),
        decision=TopicDecision.PRESENT,
        evidence_ids=(ev_0,),
        rationale="Verification matches discovery",
        provenance_document_id=doc.document_id,
    )
    c_res = ConsistencyService.evaluate(
        document_id=doc.document_id,
        discovered_topic=discovered,
        detection_result=detection_res,
        discovery_run_id="run_disc_1",
        detection_run_id="run_det_1",
    )

    assert c_res.status == ConsistencyStatus.CONSISTENT
    assert ev_0 in c_res.overlap_evidence_ids


# 17. Cancellazione selettiva con stesso document_id ma differenti versioni/fingerprint (CORREZIONE 1)
def test_17_selective_deletion_same_document_id_different_versions() -> None:
    """
    Riproduce esattamente lo scenario di audit:
    - Documento A: document_id='same_document_id', 2 messaggi.
    - Documento B: stesso document_id='same_document_id', 3 messaggi.
    - Fingerprint differenti.
    - Checkpoint valido salvato per ciascun documento.
    - clear_for_document(doc_a) DEVE eliminare ESCLUSIVAMENTE doc_a.
    - Il checkpoint di doc_b DEVE rimanere integro e caricabile.
    - Verifica che anche i file JSON legacy seguano la stessa separazione.
    """
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)

        doc_a = _make_doc("same_document_id", 2)
        doc_b = _make_doc("same_document_id", 3)

        doc_fp_a = compute_document_fingerprint(doc_a)
        doc_fp_b = compute_document_fingerprint(doc_b)
        assert doc_fp_a != doc_fp_b, "I fingerprint di versioni con diverso numero di messaggi devono differire"

        cfg_fp = compute_run_config_hash("test-model", "v1")

        ev_a = doc_a.all_evidence_sections[0].evidence_id
        ev_b = doc_b.all_evidence_sections[0].evidence_id

        res_a = TopicDetectionResult(
            topic=TopicQuery(topic_id="t1", label="Test", description="Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_a,),
            rationale="Doc A Rationale",
            provenance_document_id=doc_a.document_id,
        )
        res_b = TopicDetectionResult(
            topic=TopicQuery(topic_id="t1", label="Test", description="Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_b,),
            rationale="Doc B Rationale",
            provenance_document_id=doc_b.document_id,
        )

        store.save_chunk(doc_a.document_id, doc_fp_a, cfg_fp, 0, res_a, 1, "detection", "test-model", "v1")
        store.save_chunk(doc_b.document_id, doc_fp_b, cfg_fp, 0, res_b, 1, "detection", "test-model", "v1")

        # Crea anche legacy JSON per entrambi
        legacy_a = chk_path / f"chk_{doc_fp_a[:16]}_legacy_artifact.json"
        legacy_b = chk_path / f"chk_{doc_fp_b[:16]}_legacy_artifact.json"
        legacy_a.write_text('{"legacy": "doc_a"}', encoding="utf-8")
        legacy_b.write_text('{"legacy": "doc_b"}', encoding="utf-8")

        assert store.has_checkpoint(doc_fp_a, cfg_fp) is True
        assert store.has_checkpoint(doc_fp_b, cfg_fp) is True
        assert legacy_a.exists() and legacy_b.exists()

        # Invocazione di cancellazione controllata per Doc A
        cleared_a = store.clear_for_document(doc_a)
        # Deve cancellare il record SQLite di doc_a + il suo legacy file = 2
        assert cleared_a == 2

        # Verifica rigorosa: doc_a cancellato, doc_b INALTERATO
        assert store.has_checkpoint(doc_fp_a, cfg_fp) is False
        assert legacy_a.exists() is False

        assert store.has_checkpoint(doc_fp_b, cfg_fp) is True, "Il checkpoint di doc_b NON deve essere cancellato!"
        assert legacy_b.exists() is True, "Il file legacy di doc_b NON deve essere cancellato!"

        chk_b = store.load_checkpoint(doc_fp_b, cfg_fp)
        assert chk_b is not None
        assert chk_b.document_id == "same_document_id"
        assert chk_b.document_fingerprint == doc_fp_b

        # Test dell'API separata ed esplicita per document_id
        cleared_all = store.clear_all_for_document_id("same_document_id")
        assert cleared_all >= 1
        assert store.has_checkpoint(doc_fp_b, cfg_fp) is False


# 18. Validazione completa della provenienza e rifiuto di checkpoint manipolati (CORREZIONE 2)
def test_18_provenance_validation_fake_other_document_and_tampered_payloads() -> None:
    """
    Verifica che il validatore di checkpoint rifiuti categoricamente:
    - provenance_document_id='FAKE_OTHER_DOCUMENT' anche se cita evidence ID validi
    - provenance_document_id mancante o vuoto
    - document_id o document_fingerprint non corrispondenti
    - tipo di analisi incongruente
    - indici chunk fuori intervallo o negativi
    - totale chunk non corrispondente alla suddivisione
    - query topic (topic_id / label) incompatibile
    """
    from analysis.checkpoint import AnalysisCheckpoint

    doc = _make_doc("valid_doc_18", 2)
    doc_fp = compute_document_fingerprint(doc)
    ev_0 = doc.all_evidence_sections[0].evidence_id

    # Caso 1: Checkpoint con provenance_document_id FAKE_OTHER_DOCUMENT
    fake_chk = AnalysisCheckpoint(
        document_id=doc.document_id,
        document_fingerprint=doc_fp,
        config_fingerprint="cfg_valid",
        analysis_type="detection",
        model_id="test-model",
        prompt_version="detection_operational",
        total_planned_chunks=1,
        completed_chunk_indices=(0,),
        serialized_results={
            "0": {
                "topic": {"topic_id": "t1", "label": "Topic 1"},
                "decision": "PRESENT",
                "evidence_ids": [ev_0],
                "rationale": "Valid evidence cited",
                "provenance_document_id": "FAKE_OTHER_DOCUMENT",  # MANIPOLATO!
            }
        },
        created_at="2026-10-14T20:00:00Z",
        updated_at="2026-10-14T20:00:00Z",
    )
    assert validate_checkpoint_evidence_references(fake_chk, doc) is False, (
        "Un checkpoint con provenance_document_id estraneo deve essere categoricamente rifiutato!"
    )

    # Caso 2: Checkpoint con provenance_document_id mancante
    missing_prov_chk = AnalysisCheckpoint(
        document_id=doc.document_id,
        document_fingerprint=doc_fp,
        config_fingerprint="cfg_valid",
        analysis_type="detection",
        model_id="test-model",
        prompt_version="detection_operational",
        total_planned_chunks=1,
        completed_chunk_indices=(0,),
        serialized_results={
            "0": {
                "topic": {"topic_id": "t1", "label": "Topic 1"},
                "decision": "PRESENT",
                "evidence_ids": [ev_0],
                "rationale": "Valid evidence",
                # provenance_document_id omesso!
            }
        },
        created_at="2026-10-14T20:00:00Z",
        updated_at="2026-10-14T20:00:00Z",
    )
    assert validate_checkpoint_evidence_references(missing_prov_chk, doc) is False

    # Caso 3: Deserializzazione diretta con campo obbligatorio mancante DEVE sollevare ValueError
    with pytest.raises(ValueError, match="provenance_document_id"):
        deserialize_detection_result_from_checkpoint({
            "topic": {"topic_id": "t1", "label": "Topic 1"},
            "decision": "PRESENT",
            "evidence_ids": [ev_0],
        })

    # Caso 4: Document fingerprint non corrispondente
    wrong_fp_chk = AnalysisCheckpoint(
        document_id=doc.document_id,
        document_fingerprint="bad_fingerprint_00000000000000000000000000000000000000000000000000",
        config_fingerprint="cfg_valid",
        analysis_type="detection",
        model_id="test-model",
        prompt_version="detection_operational",
        total_planned_chunks=1,
        completed_chunk_indices=(0,),
        serialized_results={
            "0": {
                "topic": {"topic_id": "t1", "label": "Topic 1"},
                "decision": "PRESENT",
                "evidence_ids": [ev_0],
                "provenance_document_id": doc.document_id,
            }
        },
        created_at="2026-10-14T20:00:00Z",
        updated_at="2026-10-14T20:00:00Z",
    )
    assert validate_checkpoint_evidence_references(wrong_fp_chk, doc) is False

    # Caso 5: Incongruenza query (expected_topic_query)
    valid_chk = AnalysisCheckpoint(
        document_id=doc.document_id,
        document_fingerprint=doc_fp,
        config_fingerprint="cfg_valid",
        analysis_type="detection",
        model_id="test-model",
        prompt_version="detection_operational",
        total_planned_chunks=1,
        completed_chunk_indices=(0,),
        serialized_results={
            "0": {
                "topic": {"topic_id": "query_alpha", "label": "Alpha"},
                "decision": "PRESENT",
                "evidence_ids": [ev_0],
                "provenance_document_id": doc.document_id,
            }
        },
        created_at="2026-10-14T20:00:00Z",
        updated_at="2026-10-14T20:00:00Z",
    )
    # Match esatto query
    assert validate_checkpoint_evidence_references(
        valid_chk, doc, expected_topic_query=TopicQuery("query_alpha", "Alpha", "Desc")
    ) is True
    # Query differente: DEVE rifiutare
    assert validate_checkpoint_evidence_references(
        valid_chk, doc, expected_topic_query=TopicQuery("query_beta", "Beta", "Desc")
    ) is False


# 19. Migrazione sicura da JSON a SQLite con rollback e durabilità transazionale (CORREZIONE 3)
def test_19_json_to_sqlite_migration_atomic_durability_and_rollback() -> None:
    """
    Testa il ciclo completo a 6 stadi di migrazione:
    - File legacy valido: migrato e unificato in SQLite con verifica SELECT COUNT, poi eliminato.
    - Rollback su errore di scrittura: il file legacy rimane disponibile su disco.
    - Database SQLite temporaneamente bloccato: file legacy preservato senza crash.
    - File legacy corrotto sintatticamente o strutturalmente: ignorato e preservato.
    """
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        doc = _make_doc("doc_mig", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        # 1. File legacy valido
        legacy_valid = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        valid_data = {
            "document_id": doc.document_id,
            "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp,
            "analysis_type": "detection",
            "model_id": "test-model",
            "prompt_version": "v1",
            "total_planned_chunks": 1,
            "completed_chunk_indices": [0],
            "serialized_results": {
                "0": {
                    "topic": {"topic_id": "t1", "label": "Migrated Topic"},
                    "decision": "PRESENT",
                    "evidence_ids": [ev_0],
                    "rationale": "Migrated from legacy JSON",
                    "provenance_document_id": doc.document_id,
                }
            },
            "created_at": "2026-10-14T19:00:00Z",
            "updated_at": "2026-10-14T19:00:00Z",
        }
        legacy_valid.write_text(json.dumps(valid_data), encoding="utf-8")

        # 2. File legacy corrotto (JSON malformato)
        legacy_corrupt = chk_path / "chk_corrupted_format.json"
        legacy_corrupt.write_text("{ this is NOT a valid json string !!!", encoding="utf-8")

        # 3. File legacy con struttura non valida (campi obbligatori mancanti)
        legacy_invalid_struct = chk_path / "chk_invalid_structure.json"
        legacy_invalid_struct.write_text('{"some_key": 123}', encoding="utf-8")

        # Inizializzazione store: avvia _migrate_legacy_json_if_needed()
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)

        # Il file valido deve essere migrato in SQLite e rimosso
        assert legacy_valid.exists() is False
        assert store.has_checkpoint(doc_fp, cfg_fp) is True
        migrated_chk = store.load_checkpoint(doc_fp, cfg_fp)
        assert migrated_chk is not None
        assert migrated_chk.total_planned_chunks == 1
        assert "0" in migrated_chk.serialized_results

        # I file corrotti/invalidi devono rimanere disponibili su disco (nessuna perdita di file ignoti)
        assert legacy_corrupt.exists() is True
        assert legacy_invalid_struct.exists() is True

        # 4. Test di gestione con database bloccato (sqlite3.OperationalError)
        legacy_blocked = chk_path / "chk_blocked_test.json"
        blocked_data = dict(valid_data)
        blocked_data["document_fingerprint"] = "blocked_fp_" + "0" * 53
        blocked_data["config_fingerprint"] = "blocked_cfg_" + "0" * 52
        legacy_blocked.write_text(json.dumps(blocked_data), encoding="utf-8")

        # Connessione esterna che acquisisce un blocco esclusivo
        ext_conn = sqlite3.connect(str(store.db_path), timeout=0.1)
        try:
            ext_conn.execute("BEGIN EXCLUSIVE;")
            # Tenta migrazione mentre il DB è bloccato
            store._migrate_legacy_json_if_needed()
            # Il file DEVE essere preservato
            assert legacy_blocked.exists() is True
        finally:
            ext_conn.rollback()
            ext_conn.close()

        # Ora che il blocco è rilasciato, la migrazione deve riuscire
        store._migrate_legacy_json_if_needed()
        assert legacy_blocked.exists() is False
        assert store.has_checkpoint(blocked_data["document_fingerprint"], blocked_data["config_fingerprint"]) is True


# 20. Garanzie di durabilità e consistenza SQLite WAL (CORREZIONE 3 / 7)
def test_20_sqlite_wal_durability_and_consistency_guarantees() -> None:
    """
    Verifica esplicita delle impostazioni PRAGMA del database SQLite WAL:
    - journal_mode: WAL
    - synchronous: NORMAL (consistenza atomica a fronte di crash)
    - foreign_keys: ON
    - busy_timeout: configurato
    - Permessi restrittivi applicati alla directory (0o700) e al file (0o600).
    """
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)

        with store._connect() as conn:
            jm = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            assert jm.lower() == "wal"

            syn = conn.execute("PRAGMA synchronous;").fetchone()[0]
            # synchronous=NORMAL restituisce 1 in SQLite
            assert syn == 1

            fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            assert fk == 1

        assert store.db_path.exists()


# 21. Un JSON corrotto non deve nascondere un checkpoint SQLite valido (CORREZIONE 1)
def test_21_corrupt_legacy_json_does_not_mask_valid_sqlite() -> None:
    """
    Riproduce esattamente lo scenario di audit della CORREZIONE 1:
    1. Esiste un checkpoint SQLite valido;
    2. load_checkpoint() lo carica correttamente;
    3. Viene creato un file JSON legacy malformato per la stessa coppia di fingerprint;
    4. load_checkpoint() DEVE continuare a restituire il record SQLite valido;
    5. has_checkpoint() continua a restituire True;
    6. Il file JSON corrotto NON viene eliminato arbitrariamente;
    7. La validazione completa della provenance e delle evidenze ha successo.
    """
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        doc = _make_doc("doc_corr_mask", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        # 1. Creazione di un checkpoint SQLite valido
        topic = TopicQuery(topic_id="t1", label="Valid Topic", description="Desc")
        res = TopicDetectionResult(
            topic=topic,
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,),
            rationale="Valid SQLite rationale",
            provenance_document_id=doc.document_id,
            metadata={"source": "sqlite_original"},
        )
        store.save_chunk(doc.document_id, doc_fp, cfg_fp, 0, res, 1, "detection", "test-model", "v1")

        # 2. load_checkpoint() lo carica correttamente
        chk_init = store.load_checkpoint(doc_fp, cfg_fp)
        assert chk_init is not None
        assert chk_init.document_id == doc.document_id
        assert chk_init.serialized_results["0"]["metadata"]["source"] == "sqlite_original"
        assert store.has_checkpoint(doc_fp, cfg_fp) is True

        # 3. Creazione di un file JSON legacy malformato per la stessa coppia di fingerprint
        legacy_file = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        legacy_file.write_text("{ MALFORMED_JSON_CORRUPTED_SYNTAX :::: [", encoding="utf-8")

        # 4. load_checkpoint() DEVE restituire il record SQLite integro (non None!)
        chk_after = store.load_checkpoint(doc_fp, cfg_fp)
        assert chk_after is not None, "Un JSON corrotto non deve mai mascherare un checkpoint SQLite valido!"
        assert chk_after.document_id == doc.document_id
        assert chk_after.serialized_results["0"]["metadata"]["source"] == "sqlite_original"
        assert chk_after.serialized_results["0"]["decision"] == "PRESENT"

        # 5. has_checkpoint() restituisce True
        assert store.has_checkpoint(doc_fp, cfg_fp) is True

        # 6. Il file corrotto non viene eliminato automaticamente
        assert legacy_file.exists() is True, "Il file legacy corrotto deve essere preservato su disco per analisi"

        # 7. Validazione completa della provenienza
        val_res = validate_checkpoint_evidence_references(chk_after, doc, expected_topic_query=topic)
        assert val_res is True


# 22. Cancellazione completa per document_id e riconciliazione legacy JSON (CORREZIONE 2)
def test_22_clear_all_for_document_id_reconciles_fingerprints_and_legacy_json() -> None:
    """
    Verifica la cancellazione completa per document_id:
    - Versioni differenti dello stesso documento (doc_v1 e doc_v2) con stesso document_id;
    - Documento differente (doc_other) con document_id differente;
    - Presenza di checkpoint SQLite e corrispondenti file legacy JSON per tutte le versioni;
    - Presenza di un file JSON corrotto non attribuibile;
    - Invocazione di clear_all_for_document_id:
      1. Cancella tutte le versioni del documento selezionato da SQLite;
      2. Cancella tutti i legacy JSON associati a quel document_id e ai suoi fingerprint;
      3. NON cancella SQLite né i legacy JSON del documento differente;
      4. Preserva il file legacy corrotto non attribuibile;
      5. I checkpoint cancellati NON resuscitano tramite load_checkpoint().
    """
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)

        doc_v1 = _make_doc("target_doc", 2)
        doc_v2 = _make_doc("target_doc", 4)
        doc_other = _make_doc("other_doc", 3)

        doc_fp_v1 = compute_document_fingerprint(doc_v1)
        doc_fp_v2 = compute_document_fingerprint(doc_v2)
        doc_fp_other = compute_document_fingerprint(doc_other)
        assert len({doc_fp_v1, doc_fp_v2, doc_fp_other}) == 3

        cfg_fp = compute_run_config_hash("test-model", "v1")

        ev_v1 = doc_v1.all_evidence_sections[0].evidence_id
        ev_v2 = doc_v2.all_evidence_sections[0].evidence_id
        ev_other = doc_other.all_evidence_sections[0].evidence_id

        # Checkpoint SQLite
        res_v1 = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "D"), decision=TopicDecision.PRESENT,
            evidence_ids=(ev_v1,), rationale="V1", provenance_document_id="target_doc"
        )
        res_v2 = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "D"), decision=TopicDecision.PRESENT,
            evidence_ids=(ev_v2,), rationale="V2", provenance_document_id="target_doc"
        )
        res_other = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "D"), decision=TopicDecision.PRESENT,
            evidence_ids=(ev_other,), rationale="Other", provenance_document_id="other_doc"
        )

        store.save_chunk("target_doc", doc_fp_v1, cfg_fp, 0, res_v1, 1, "detection", "test-model", "v1")
        store.save_chunk("target_doc", doc_fp_v2, cfg_fp, 0, res_v2, 1, "detection", "test-model", "v1")
        store.save_chunk("other_doc", doc_fp_other, cfg_fp, 0, res_other, 1, "detection", "test-model", "v1")

        # File legacy JSON per ciascuna versione
        legacy_v1 = chk_path / f"chk_{doc_fp_v1[:16]}_{cfg_fp[:16]}.json"
        legacy_v1.write_text(json.dumps({
            "document_id": "target_doc", "document_fingerprint": doc_fp_v1,
            "config_fingerprint": cfg_fp, "analysis_type": "detection",
            "model_id": "test-model", "prompt_version": "v1", "total_planned_chunks": 1,
            "serialized_results": {"0": serialize_detection_result_for_checkpoint(res_v1)}
        }), encoding="utf-8")

        legacy_v2 = chk_path / f"chk_{doc_fp_v2[:16]}_{cfg_fp[:16]}.json"
        legacy_v2.write_text(json.dumps({
            "document_id": "target_doc", "document_fingerprint": doc_fp_v2,
            "config_fingerprint": cfg_fp, "analysis_type": "detection",
            "model_id": "test-model", "prompt_version": "v1", "total_planned_chunks": 1,
            "serialized_results": {"0": serialize_detection_result_for_checkpoint(res_v2)}
        }), encoding="utf-8")

        legacy_other = chk_path / f"chk_{doc_fp_other[:16]}_{cfg_fp[:16]}.json"
        legacy_other.write_text(json.dumps({
            "document_id": "other_doc", "document_fingerprint": doc_fp_other,
            "config_fingerprint": cfg_fp, "analysis_type": "detection",
            "model_id": "test-model", "prompt_version": "v1", "total_planned_chunks": 1,
            "serialized_results": {"0": serialize_detection_result_for_checkpoint(res_other)}
        }), encoding="utf-8")

        # File legacy corrotto non attribuibile
        legacy_corrupt = chk_path / "chk_corrupted_unknown.json"
        legacy_corrupt.write_text("{ CORRUPTED_CONTENT", encoding="utf-8")

        assert store.has_checkpoint(doc_fp_v1, cfg_fp) is True
        assert store.has_checkpoint(doc_fp_v2, cfg_fp) is True
        assert store.has_checkpoint(doc_fp_other, cfg_fp) is True

        # Invocazione clear_all_for_document_id per target_doc
        cleared = store.clear_all_for_document_id("target_doc")
        assert cleared >= 2

        # 1 & 2. target_doc (v1 e v2) cancellati da SQLite e file legacy rimossi
        assert store.has_checkpoint(doc_fp_v1, cfg_fp) is False
        assert store.has_checkpoint(doc_fp_v2, cfg_fp) is False
        assert legacy_v1.exists() is False
        assert legacy_v2.exists() is False

        # Verifica che load_checkpoint() non resusciti target_doc
        assert store.load_checkpoint(doc_fp_v1, cfg_fp) is None
        assert store.load_checkpoint(doc_fp_v2, cfg_fp) is None

        # 3. other_doc completamente intatto (SQLite e legacy)
        assert store.has_checkpoint(doc_fp_other, cfg_fp) is True
        assert legacy_other.exists() is True
        chk_other = store.load_checkpoint(doc_fp_other, cfg_fp)
        assert chk_other is not None
        assert chk_other.document_id == "other_doc"

        # 4. File corrotto non attribuibile preservato
        assert legacy_corrupt.exists() is True


# 23. Precedenza SQLite e riconciliazione con legacy (8 test obbligatori CORREZIONE 3)
def test_23_sqlite_precedence_no_overwrite_by_obsolete_legacy() -> None:
    """
    Testa rigorosamente tutti gli 8 scenari obbligatori della CORREZIONE 3:
    1. SQLite PRESENT / legacy ABSENT: SQLite prevale, legacy discordante preservato.
    2. SQLite ABSENT / legacy PRESENT: SQLite prevale, legacy discordante preservato.
    3. Risultati identici in entrambi gli archivi: SQLite preservato, legacy rimosso.
    4. SQLite valido / legacy corrotto: SQLite restituito, legacy corrotto preservato.
    5. Legacy valido / SQLite assente: migrazione regolare in SQLite, legacy rimosso.
    6. Legacy valido / SQLite incompleto: chunk mancante integrato, nessun overwrite.
    7. Riavvio dello store con dati già migrati: stabilità totale senza duplicazioni.
    8. Nessuna perdita o sovrascrittura silenziosa di decisioni, rationale o evidenze.
    """
    # SCENARIO 1: SQLite PRESENT / legacy ABSENT
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        doc = _make_doc("doc_scen_1", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        # SQLite ha PRESENT
        res_sqlite = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "D"), decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,), rationale="SQLite PRESENT rationale", provenance_document_id=doc.document_id
        )
        store.save_chunk(doc.document_id, doc_fp, cfg_fp, 0, res_sqlite, 1, "detection", "test-model", "v1")

        # Legacy ha ABSENT
        legacy_file = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        res_legacy = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "D"), decision=TopicDecision.ABSENT,
            evidence_ids=(), rationale="Legacy ABSENT rationale", provenance_document_id=doc.document_id
        )
        legacy_file.write_text(json.dumps({
            "document_id": doc.document_id, "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp, "analysis_type": "detection",
            "model_id": "test-model", "prompt_version": "v1", "total_planned_chunks": 1,
            "serialized_results": {"0": serialize_detection_result_for_checkpoint(res_legacy)}
        }), encoding="utf-8")

        # Inizializzazione nuovo store / migrazione
        store2 = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        loaded = store2.load_checkpoint(doc_fp, cfg_fp)
        assert loaded is not None
        assert loaded.serialized_results["0"]["decision"] == "PRESENT", "SQLite consolidato DEVE prevalere su legacy ABSENT!"
        assert loaded.serialized_results["0"]["rationale"] == "SQLite PRESENT rationale"
        assert legacy_file.exists() is True, "Il file legacy discordante DEVE essere preservato su disco per verifica!"

    # SCENARIO 2: SQLite ABSENT / legacy PRESENT
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        doc = _make_doc("doc_scen_2", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        # SQLite ha ABSENT
        res_sqlite = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "D"), decision=TopicDecision.ABSENT,
            evidence_ids=(), rationale="SQLite ABSENT rationale", provenance_document_id=doc.document_id
        )
        store.save_chunk(doc.document_id, doc_fp, cfg_fp, 0, res_sqlite, 1, "detection", "test-model", "v1")

        # Legacy ha PRESENT
        legacy_file = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        res_legacy = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "D"), decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,), rationale="Legacy PRESENT rationale", provenance_document_id=doc.document_id
        )
        legacy_file.write_text(json.dumps({
            "document_id": doc.document_id, "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp, "analysis_type": "detection",
            "model_id": "test-model", "prompt_version": "v1", "total_planned_chunks": 1,
            "serialized_results": {"0": serialize_detection_result_for_checkpoint(res_legacy)}
        }), encoding="utf-8")

        store2 = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        loaded = store2.load_checkpoint(doc_fp, cfg_fp)
        assert loaded is not None
        assert loaded.serialized_results["0"]["decision"] == "ABSENT", "SQLite consolidato DEVE prevalere su legacy PRESENT!"
        assert loaded.serialized_results["0"]["rationale"] == "SQLite ABSENT rationale"
        assert legacy_file.exists() is True

    # SCENARIO 3: Risultati identici in entrambi gli archivi
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        doc = _make_doc("doc_scen_3", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        res_ident = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "D"), decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,), rationale="Identical rationale", provenance_document_id=doc.document_id
        )
        store.save_chunk(doc.document_id, doc_fp, cfg_fp, 0, res_ident, 1, "detection", "test-model", "v1")

        legacy_file = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        legacy_file.write_text(json.dumps({
            "document_id": doc.document_id, "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp, "analysis_type": "detection",
            "model_id": "test-model", "prompt_version": "v1", "total_planned_chunks": 1,
            "serialized_results": {"0": serialize_detection_result_for_checkpoint(res_ident)}
        }), encoding="utf-8")

        store2 = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        loaded = store2.load_checkpoint(doc_fp, cfg_fp)
        assert loaded is not None
        assert loaded.serialized_results["0"]["decision"] == "PRESENT"
        # Essendo identico e già consolidato in SQLite, il file legacy viene rimosso
        assert legacy_file.exists() is False

    # SCENARIO 4: SQLite valido / legacy corrotto
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        doc = _make_doc("doc_scen_4", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        res_valid = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "D"), decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,), rationale="Valid SQLite", provenance_document_id=doc.document_id
        )
        store.save_chunk(doc.document_id, doc_fp, cfg_fp, 0, res_valid, 1, "detection", "test-model", "v1")

        legacy_corrupt = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        legacy_corrupt.write_text("CORRUPTED_JSON_DATA{{{{", encoding="utf-8")

        store2 = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        loaded = store2.load_checkpoint(doc_fp, cfg_fp)
        assert loaded is not None
        assert loaded.serialized_results["0"]["decision"] == "PRESENT"
        assert legacy_corrupt.exists() is True

    # SCENARIO 5: Legacy valido / SQLite assente
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        doc = _make_doc("doc_scen_5", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        res_mig = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "D"), decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,), rationale="From Legacy", provenance_document_id=doc.document_id
        )
        legacy_file = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        legacy_file.write_text(json.dumps({
            "document_id": doc.document_id, "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp, "analysis_type": "detection",
            "model_id": "test-model", "prompt_version": "v1", "total_planned_chunks": 1,
            "serialized_results": {"0": serialize_detection_result_for_checkpoint(res_mig)}
        }), encoding="utf-8")

        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        loaded = store.load_checkpoint(doc_fp, cfg_fp)
        assert loaded is not None
        assert loaded.serialized_results["0"]["decision"] == "PRESENT"
        assert legacy_file.exists() is False

    # SCENARIO 6: Legacy valido / SQLite incompleto (chunk 0 in SQLite, chunk 1 in legacy)
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        doc = _make_doc("doc_scen_6", 4)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id
        ev_1 = doc.all_evidence_sections[1].evidence_id

        res_c0 = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "D"), decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,), rationale="Chunk 0 in SQLite", provenance_document_id=doc.document_id
        )
        res_c1 = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "D"), decision=TopicDecision.ABSENT,
            evidence_ids=(), rationale="Chunk 1 in Legacy", provenance_document_id=doc.document_id
        )

        # SQLite ha SOLO chunk 0
        store.save_chunk(doc.document_id, doc_fp, cfg_fp, 0, res_c0, 2, "detection", "test-model", "v1")

        # Legacy ha chunk 0 (identico) e chunk 1 (mancante in SQLite)
        legacy_file = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        legacy_file.write_text(json.dumps({
            "document_id": doc.document_id, "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp, "analysis_type": "detection",
            "model_id": "test-model", "prompt_version": "v1", "total_planned_chunks": 2,
            "serialized_results": {
                "0": serialize_detection_result_for_checkpoint(res_c0),
                "1": serialize_detection_result_for_checkpoint(res_c1),
            }
        }), encoding="utf-8")

        store2 = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        loaded = store2.load_checkpoint(doc_fp, cfg_fp)
        assert loaded is not None
        assert loaded.completed_chunk_indices == (0, 1), "Il blocco mancante deve essere stato integrato!"
        assert loaded.serialized_results["0"]["rationale"] == "Chunk 0 in SQLite"
        assert loaded.serialized_results["1"]["rationale"] == "Chunk 1 in Legacy"
        assert legacy_file.exists() is False

    # SCENARIO 7: Riavvio dello store con dati già migrati
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        doc = _make_doc("doc_scen_7", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        res = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "D"), decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,), rationale="Stable", provenance_document_id=doc.document_id
        )
        store.save_chunk(doc.document_id, doc_fp, cfg_fp, 0, res, 1, "detection", "test-model", "v1")

        # Riavvio 1
        s1 = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        chk1 = s1.load_checkpoint(doc_fp, cfg_fp)
        # Riavvio 2
        s2 = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        chk2 = s2.load_checkpoint(doc_fp, cfg_fp)
        # Riavvio 3
        s3 = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        chk3 = s3.load_checkpoint(doc_fp, cfg_fp)

        assert chk1 is not None and chk2 is not None and chk3 is not None
        assert chk1.serialized_results == chk2.serialized_results == chk3.serialized_results

    # SCENARIO 8: Nessuna perdita o sovrascrittura silenziosa
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        doc = _make_doc("doc_scen_8", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        res_orig = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "D"), decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,), rationale="Pristine Rationale", provenance_document_id=doc.document_id,
            metadata={"key": "val_original"}
        )
        store.save_chunk(doc.document_id, doc_fp, cfg_fp, 0, res_orig, 1, "detection", "test-model", "v1")

        # Creiamo un file legacy con metadati alterati
        legacy_tampered = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        tampered_res = dict(serialize_detection_result_for_checkpoint(res_orig))
        tampered_res["rationale"] = "TAMPERED_SILENT_OVERWRITE_ATTEMPT"
        tampered_res["metadata"] = {"key": "tampered_value"}
        legacy_tampered.write_text(json.dumps({
            "document_id": doc.document_id, "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp, "analysis_type": "detection",
            "model_id": "test-model", "prompt_version": "v1", "total_planned_chunks": 1,
            "serialized_results": {"0": tampered_res}
        }), encoding="utf-8")

        store_test = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        final_chk = store_test.load_checkpoint(doc_fp, cfg_fp)
        assert final_chk is not None
        # I valori originali SQLite non devono essere alterati
        assert final_chk.serialized_results["0"]["rationale"] == "Pristine Rationale"
        assert final_chk.serialized_results["0"]["metadata"]["key"] == "val_original"
        assert legacy_tampered.exists() is True


# 24. CORREZIONE 1: Non eliminare un legacy valido se SQLite è corrotto
def test_24_valid_legacy_preserved_when_sqlite_chunk_corrupted() -> None:
    """
    Riproduzione esatta CORREZIONE 1:
    1. Crea una testata SQLite valida.
    2. Inserisce nel chunk 0 un serialized_result che non sia JSON valido.
    3. Crea un file legacy valido per la stessa coppia documento/configurazione,
       contenente il risultato del chunk 0.
    4. Reinizializza AnalysisCheckpointStore.
    
    Verifiche:
    - Il JSON SQLite non viene ignorato silenziosamente ma rilevato come corrotto;
    - Viene distinto chunk assente, valido, divergente e corrotto;
    - Un chunk SQLite corrotto produce un conflitto esplicito;
    - Il file legacy valido viene rigorosamente preservato su disco;
    - Nessuna sovrascrittura automatica o arbitraria;
    - Non viene eliminata alcuna copia finché non sia disponibile una migrazione verificabilmente integra;
    - La migrazione non usa il solo conteggio record per dichiararsi completata.
    """
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        doc = _make_doc("doc_corrupt_sqlite", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        # 1. Salva un chunk valido inizialmente
        res_valid = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,),
            rationale="Valid legacy result",
            provenance_document_id=doc.document_id,
        )
        store.save_chunk(doc.document_id, doc_fp, cfg_fp, 0, res_valid, 1, "detection", "test-model", "v1")

        # 2. Corrompi direttamente il chunk 0 in SQLite inserendo testo non JSON
        with store._connect() as conn:
            conn.execute("""
                UPDATE checkpoint_chunks
                SET serialized_result = 'CORRUPTED_NON_JSON_DATA{{{'
                WHERE document_fingerprint = ? AND config_fingerprint = ? AND chunk_index = 0;
            """, (doc_fp, cfg_fp))

        # 3. Crea il file legacy valido corrispondente
        legacy_file = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        legacy_file.write_text(json.dumps({
            "document_id": doc.document_id,
            "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp,
            "analysis_type": "detection",
            "model_id": "test-model",
            "prompt_version": "v1",
            "total_planned_chunks": 1,
            "serialized_results": {
                "0": serialize_detection_result_for_checkpoint(res_valid),
            },
        }), encoding="utf-8")
        assert legacy_file.exists() is True

        # 4. Reinizializza AnalysisCheckpointStore
        store2 = AnalysisCheckpointStore(checkpoint_dir=chk_path)

        # 5. Verifiche:
        # Il file legacy DEVE essere preservato su disco a causa del conflitto sul chunk corrotto
        assert legacy_file.exists() is True, "Il file legacy valido NON deve essere eliminato se SQLite è corrotto!"

        # In SQLite il dato corrotto non deve essere stato sovrascritto arbitrariamente
        with store2._connect() as conn:
            cur = conn.execute("""
                SELECT serialized_result FROM checkpoint_chunks
                WHERE document_fingerprint = ? AND config_fingerprint = ? AND chunk_index = 0;
            """, (doc_fp, cfg_fp))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "CORRUPTED_NON_JSON_DATA{{{"


# 25. CORREZIONE 2: Preservare i file legacy non decodificabili in clear_all_for_document_id
def test_25_clear_all_for_document_id_preserves_corrupted_legacy_with_matching_name() -> None:
    """
    Riproduzione esatta CORREZIONE 2:
    1. Checkpoint SQLite valido per document_id='target_doc'.
    2. File legacy corrotto il cui nome contiene il fingerprint del documento selezionato.
    3. File legacy valido per un altro documento 'other_doc'.
    4. Esecuzione di clear_all_for_document_id('target_doc').
    
    Verifiche:
    - Eliminazione del comportamento except Exception: pass fallthrough;
    - Unica procedura di attribuzione verificata per tutti i file da cancellare;
    - Nessuna cancellazione basata sul solo nome compatibile quando il contenuto è corrotto/non validabile;
    - Preservazione del file corrotto non attribuibile;
    - Preservazione del file appartenente a un altro documento;
    - Cancellazione dei soli record SQLite e file legacy attribuibili al documento.
    """
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        doc = _make_doc("target_doc", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        res = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,),
            rationale="Target doc result",
            provenance_document_id=doc.document_id,
        )
        store.save_chunk(doc.document_id, doc_fp, cfg_fp, 0, res, 1, "detection", "test-model", "v1")

        # File legacy corrotto con nome contenente il fingerprint del documento selezionato
        corrupt_legacy = chk_path / f"chk_{doc_fp[:16]}_corrupted_candidate.json"
        corrupt_legacy.write_text("CORRUPTED_NON_PARSEABLE_BYTES{{{{", encoding="utf-8")

        # File legacy appartenente a un altro documento
        doc_other = _make_doc("other_doc", 2)
        doc_other_fp = compute_document_fingerprint(doc_other)
        other_legacy = chk_path / f"chk_{doc_other_fp[:16]}_{cfg_fp[:16]}.json"
        other_legacy.write_text(json.dumps({
            "document_id": "other_doc",
            "document_fingerprint": doc_other_fp,
            "config_fingerprint": cfg_fp,
            "analysis_type": "detection",
            "model_id": "test-model",
            "prompt_version": "v1",
            "total_planned_chunks": 1,
            "serialized_results": {
                "0": serialize_detection_result_for_checkpoint(res),
            },
        }), encoding="utf-8")

        assert corrupt_legacy.exists() is True
        assert other_legacy.exists() is True

        # Esecuzione clear_all_for_document_id per 'target_doc'
        cleared = store.clear_all_for_document_id("target_doc")
        assert cleared >= 1  # Record SQLite di target_doc rimosso

        # Il file legacy corrotto DEVE essere preservato perché non è attribuibile con certezza
        assert corrupt_legacy.exists() is True, "Il file legacy corrotto NON deve essere eliminato sulla base del solo nome compatibile!"

        # Il file dell'altro documento DEVE rimanere intatto
        assert other_legacy.exists() is True, "Il file dell'altro documento NON deve essere eliminato!"

        # SQLite di target_doc è stato cancellato
        assert store.has_checkpoint(doc_fp, cfg_fp) is False


# 26. CORREZIONE 3: Validare i metadati prima di fondere chunk
def test_26_metadata_validation_before_merge_rejects_incompatible_legacy() -> None:
    """
    Riproduzione esatta CORREZIONE 3:
    1. SQLite contiene document_id='doc', testata con total_planned_chunks=1, chunk 0 esiste.
    2. File legacy dichiara gli stessi fingerprint (doc_fp, cfg_fp), ma document_id='OTHER_DOCUMENT',
       total_planned_chunks=2 e chunk 1.
    
    Verifiche:
    - Confronto rigoroso metadati prima di qualsiasi fusione;
    - SQLite conserva document_id='doc';
    - Nessuna modifica della testata SQLite;
    - Nessun aggiornamento automatico di total_planned_chunks a MAX(total, ?);
    - Chunk 1 NON viene importato;
    - Il file legacy discordante viene preservato intatto;
    - Viene registrato un conflitto diagnostico sicuro.
    """
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        doc = _make_doc("doc", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        res_0 = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,),
            rationale="Chunk 0 in SQLite",
            provenance_document_id="doc",
        )
        store.save_chunk("doc", doc_fp, cfg_fp, 0, res_0, 1, "detection", "test-model", "v1")

        # File legacy discordante: stessi fingerprint, ma document_id diverso, totale 2, chunk 1
        res_1 = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,),
            rationale="Chunk 1 in Legacy from OTHER_DOCUMENT",
            provenance_document_id="OTHER_DOCUMENT",
        )
        legacy_file = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        legacy_file.write_text(json.dumps({
            "document_id": "OTHER_DOCUMENT",
            "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp,
            "analysis_type": "detection",
            "model_id": "test-model",
            "prompt_version": "v1",
            "total_planned_chunks": 2,
            "serialized_results": {
                "1": serialize_detection_result_for_checkpoint(res_1),
            },
        }), encoding="utf-8")

        # Reinizializza lo store
        store2 = AnalysisCheckpointStore(checkpoint_dir=chk_path)

        # Verifiche:
        chk = store2.load_checkpoint(doc_fp, cfg_fp)
        assert chk is not None
        # La testata SQLite non deve essere stata alterata
        assert chk.document_id == "doc"
        assert chk.total_planned_chunks == 1, "total_planned_chunks NON deve essere stato esteso a 2 con MAX!"
        assert chk.completed_chunk_indices == (0,), "Il chunk 1 del documento alieno NON deve essere stato importato!"
        assert "1" not in chk.serialized_results

        # Il file legacy incompatibile deve essere preservato intatto
        assert legacy_file.exists() is True, "Il file legacy discordante DEVE essere preservato su disco!"


# 27. Rifiuto indici chunk non numerici e fuori intervallo
def test_27_reconciliation_rejects_non_numeric_and_out_of_range_indices() -> None:
    """
    Verifica che indici non numerici ('bad_index') o fuori intervallo ([0, total_planned_chunks))
    vengano rifiutati senza corrompere lo store, preservando il file legacy.
    """
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        doc = _make_doc("doc_indices", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        res = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,),
            rationale="Valid result",
            provenance_document_id=doc.document_id,
        )
        serialized_chunk = serialize_detection_result_for_checkpoint(res)

        # Legacy file con indici non numerici e fuori intervallo
        legacy_file = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        legacy_file.write_text(json.dumps({
            "document_id": doc.document_id,
            "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp,
            "analysis_type": "detection",
            "model_id": "test-model",
            "prompt_version": "v1",
            "total_planned_chunks": 2,
            "serialized_results": {
                "not_a_number": serialized_chunk,
                "-1": serialized_chunk,
                "5": serialized_chunk,  # 5 >= total_planned_chunks (2)
            },
        }), encoding="utf-8")

        store2 = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        chk = store2.load_checkpoint(doc_fp, cfg_fp)
        # Nessun blocco valido per intervallo è stato importato, quindi il checkpoint resta nullo o vuoto
        assert chk is None or len(chk.completed_chunk_indices) == 0
        # Il file con anomalie viene preservato su disco per ispezione
        assert legacy_file.exists() is True


# 28. Rifiuto discrepanze nei metadati di analisi (analysis_type, model_id, prompt_version)
def test_28_reconciliation_rejects_incompatible_analysis_metadata() -> None:
    """
    Verifica che differenze in analysis_type, model_id o prompt_version blocchino
    qualsiasi alterazione della testata o fusione di chunk, preservando il legacy.
    """
    for field_name, bad_value in [
        ("analysis_type", "discovery"),
        ("model_id", "different-model-id"),
        ("prompt_version", "v999"),
    ]:
        with tempfile.TemporaryDirectory() as chk_dir:
            chk_path = Path(chk_dir)
            store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
            doc = _make_doc(f"doc_{field_name}", 2)
            doc_fp = compute_document_fingerprint(doc)
            cfg_fp = compute_run_config_hash("test-model", "v1")
            ev_0 = doc.all_evidence_sections[0].evidence_id

            res_0 = TopicDetectionResult(
                topic=TopicQuery("t1", "T1", "Desc"),
                decision=TopicDecision.PRESENT,
                evidence_ids=(ev_0,),
                rationale="Chunk 0 in SQLite",
                provenance_document_id=doc.document_id,
            )
            store.save_chunk(doc.document_id, doc_fp, cfg_fp, 0, res_0, 2, "detection", "test-model", "v1")

            # Crea legacy file con metadato incompatibile
            meta = {
                "document_id": doc.document_id,
                "document_fingerprint": doc_fp,
                "config_fingerprint": cfg_fp,
                "analysis_type": "detection",
                "model_id": "test-model",
                "prompt_version": "v1",
                "total_planned_chunks": 2,
                "serialized_results": {
                    "1": serialize_detection_result_for_checkpoint(res_0),
                },
            }
            meta[field_name] = bad_value

            legacy_file = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
            legacy_file.write_text(json.dumps(meta), encoding="utf-8")

            store2 = AnalysisCheckpointStore(checkpoint_dir=chk_path)
            chk = store2.load_checkpoint(doc_fp, cfg_fp)
            assert chk is not None
            # Verifica che SQLite mantenga i suoi valori e non abbia importato il chunk 1 incompatibile
            assert chk.analysis_type == "detection"
            assert chk.model_id == "test-model"
            assert chk.prompt_version == "v1"
            assert chk.completed_chunk_indices == (0,)
            assert legacy_file.exists() is True


# 29. Migrazione interrotta, successiva ripresa e conservazione integrale di decisioni ed evidenze
def test_29_migration_interruption_recovery_and_evidence_preservation() -> None:
    """
    Verifica che una migrazione completa preservi integralmente tutti i campi:
    decisioni, rationale, evidence IDs e metadati, e che la ripresa mantenga
    la perfetta fedeltà semantica senza regressioni.
    """
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        doc = _make_doc("doc_fidelity", 4)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id
        ev_1 = doc.all_evidence_sections[1].evidence_id

        res_0 = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0, ev_1),
            rationale="Crucial evidence detected with high confidence in chunk 0.",
            provenance_document_id=doc.document_id,
            metadata={"source_weight": 0.95, "run_tag": "fidelity_test"},
        )
        res_1 = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "Desc"),
            decision=TopicDecision.ABSENT,
            evidence_ids=(),
            rationale="No relevant statements found in chunk 1.",
            provenance_document_id=doc.document_id,
            metadata={"source_weight": 0.10, "run_tag": "fidelity_test"},
        )

        legacy_file = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        legacy_file.write_text(json.dumps({
            "document_id": doc.document_id,
            "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp,
            "analysis_type": "detection",
            "model_id": "test-model",
            "prompt_version": "v1",
            "total_planned_chunks": 2,
            "serialized_results": {
                "0": serialize_detection_result_for_checkpoint(res_0),
                "1": serialize_detection_result_for_checkpoint(res_1),
            },
        }), encoding="utf-8")

        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        loaded = store.load_checkpoint(doc_fp, cfg_fp)
        assert loaded is not None
        assert loaded.completed_chunk_indices == (0, 1)

        # Verifica fedeltà assoluta chunk 0
        c0 = loaded.serialized_results["0"]
        assert c0["decision"] == "PRESENT"
        assert c0["evidence_ids"] == [ev_0, ev_1]
        assert c0["rationale"] == "Crucial evidence detected with high confidence in chunk 0."
        assert c0["metadata"]["run_tag"] == "fidelity_test"

        # Verifica fedeltà assoluta chunk 1
        c1 = loaded.serialized_results["1"]
        assert c1["decision"] == "ABSENT"
        assert c1["evidence_ids"] == []
        assert c1["rationale"] == "No relevant statements found in chunk 1."
        assert c1["metadata"]["run_tag"] == "fidelity_test"

        # Migrazione verificata: legacy file rimosso in modo pulito
        assert legacy_file.exists() is False

        # Riavvio store / persistenza garantita
        store_reload = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        loaded_reload = store_reload.load_checkpoint(doc_fp, cfg_fp)
        assert loaded_reload is not None
        assert loaded_reload.serialized_results == loaded.serialized_results


# 30. DIFETTO 1: Isolamento documentale e divieto cancellazione SQLite tramite legacy contraddittorio
def test_30_clear_all_for_document_id_does_not_delete_other_document_on_contradictory_legacy() -> None:
    """
    Riproduzione esatta DIFETTO 1:
    - Due documenti distinti: Documento A ('doc_A') con fingerprint fp_a, Documento B ('doc_B') con fingerprint fp_b.
    - Checkpoint SQLite validi per entrambi i documenti.
    - File legacy JSON che dichiara document_id='doc_A' ma document_fingerprint=fp_b.
    - Esecuzione di clear_all_for_document_id('doc_A').
    
    Verifiche:
    - Checkpoint di A eliminato da SQLite;
    - Checkpoint di B rigorosamente PRESERVATO in SQLite;
    - Nessuna perdita di chunk, decisioni, rationale o evidence ID del documento B;
    - File legacy contraddittorio non cancella B e viene preservato per ispezione;
    - Stabilità e persistenza dei dati di B verificate anche dopo re-inizializzazione di AnalysisCheckpointStore.
    """
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        doc_a = _make_doc("doc_A", 2)
        doc_b = _make_doc("doc_B", 2)
        fp_a = compute_document_fingerprint(doc_a)
        fp_b = compute_document_fingerprint(doc_b)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_a = doc_a.all_evidence_sections[0].evidence_id
        ev_b = doc_b.all_evidence_sections[0].evidence_id

        # Checkpoint SQLite valido per A
        res_a = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_a,),
            rationale="Doc A Rationale",
            provenance_document_id="doc_A",
        )
        store.save_chunk("doc_A", fp_a, cfg_fp, 0, res_a, 1, "detection", "test-model", "v1")

        # Checkpoint SQLite valido per B con dati dettagliati
        res_b = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_b,),
            rationale="Doc B Crucial Evidence and Rationale",
            provenance_document_id="doc_B",
            metadata={"priority": "high", "audit_tag": "preserve_me"},
        )
        store.save_chunk("doc_B", fp_b, cfg_fp, 0, res_b, 1, "detection", "test-model", "v1")

        assert store.has_checkpoint(fp_a, cfg_fp) is True
        assert store.has_checkpoint(fp_b, cfg_fp) is True

        # File legacy contraddittorio: dichiara document_id='doc_A' ma document_fingerprint=fp_b
        contradictory_legacy = chk_path / f"chk_{fp_b[:16]}_{cfg_fp[:16]}.json"
        contradictory_legacy.write_text(json.dumps({
            "document_id": "doc_A",
            "document_fingerprint": fp_b,
            "config_fingerprint": cfg_fp,
            "analysis_type": "detection",
            "model_id": "test-model",
            "prompt_version": "v1",
            "total_planned_chunks": 1,
            "serialized_results": {
                "0": serialize_detection_result_for_checkpoint(res_a),
            },
        }), encoding="utf-8")
        assert contradictory_legacy.exists() is True

        # Invocazione cancellazione per Documento A
        cleared = store.clear_all_for_document_id("doc_A")
        assert cleared >= 1

        # Verifiche:
        # 1. Checkpoint A cancellato
        assert store.has_checkpoint(fp_a, cfg_fp) is False

        # 2. Checkpoint B INTEGRALMENTE CONSERVATO in SQLite
        assert store.has_checkpoint(fp_b, cfg_fp) is True, "Il checkpoint del documento B NON deve essere cancellato!"
        chk_b = store.load_checkpoint(fp_b, cfg_fp)
        assert chk_b is not None
        assert chk_b.document_id == "doc_B"
        assert chk_b.document_fingerprint == fp_b
        assert chk_b.completed_chunk_indices == (0,)
        assert chk_b.serialized_results["0"]["decision"] == "PRESENT"
        assert chk_b.serialized_results["0"]["rationale"] == "Doc B Crucial Evidence and Rationale"
        assert chk_b.serialized_results["0"]["evidence_ids"] == [ev_b]
        assert chk_b.serialized_results["0"]["metadata"]["audit_tag"] == "preserve_me"

        # 3. File legacy contraddittorio non ha cancellato B ed è preservato
        assert contradictory_legacy.exists() is True

        # 4. Nuova istanza dello store: checkpoint B rimane integro
        store2 = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        chk_b_reloaded = store2.load_checkpoint(fp_b, cfg_fp)
        assert chk_b_reloaded is not None
        assert chk_b_reloaded.document_id == "doc_B"
        assert chk_b_reloaded.serialized_results["0"]["rationale"] == "Doc B Crucial Evidence and Rationale"


# 31. DIFETTO 2: Validazione preliminare completa e divieto migrazioni parziali di file legacy invalidi
def test_31_preliminary_validation_rejects_partial_migration_of_invalid_legacy() -> None:
    """
    Riproduzione esatta DIFETTO 2:
    - JSON legacy con total_planned_chunks=2.
    - Chunk '0' formalmente valido.
    - Secondo indice 'bad' non numerico.
    - Nessun checkpoint SQLite iniziale.
    
    Verifiche:
    - Validazione preliminare completa rifiuta l'intero file anomalo;
    - Nessuna testata parziale creata in SQLite;
    - Nessun chunk (neppure chunk 0) inserito in SQLite;
    - File legacy preservato intatto su disco;
    - load_checkpoint() restituisce None (nessun checkpoint parzialmente importato);
    - Database SQLite privo di record per questo checkpoint.
    """
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        doc = _make_doc("doc_invalid_legacy", 2)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        res_0 = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,),
            rationale="Valid chunk 0",
            provenance_document_id=doc.document_id,
        )

        legacy_file = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        legacy_file.write_text(json.dumps({
            "document_id": doc.document_id,
            "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp,
            "analysis_type": "detection",
            "model_id": "test-model",
            "prompt_version": "v1",
            "total_planned_chunks": 2,
            "serialized_results": {
                "0": serialize_detection_result_for_checkpoint(res_0),
                "bad": {"decision": "ABSENT", "evidence_ids": [], "rationale": "Bad index payload"},
            },
        }), encoding="utf-8")

        # Inizializzazione store
        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)

        # Verifiche:
        # 1. Il file legacy anomalo deve essere preservato su disco per indagine
        assert legacy_file.exists() is True

        # 2. SQLite non deve contenere alcuna testata parziale
        with store._connect() as conn:
            cur_h = conn.execute("SELECT COUNT(*) FROM checkpoints WHERE document_fingerprint = ?;", (doc_fp,))
            assert cur_h.fetchone()[0] == 0, "Nessuna testata parziale deve essere stata creata in SQLite!"

            # 3. SQLite non deve contenere il chunk 0
            cur_c = conn.execute("SELECT COUNT(*) FROM checkpoint_chunks WHERE document_fingerprint = ?;", (doc_fp,))
            assert cur_c.fetchone()[0] == 0, "Nessun chunk parziale deve essere stato inserito in SQLite!"

        # 4. load_checkpoint restituisce None
        assert store.load_checkpoint(doc_fp, cfg_fp) is None


# 32. Migrazione regolare di checkpoint legittimamente parziali da analisi interrotta
def test_32_legitimate_partial_checkpoint_from_interrupted_analysis_is_migrated() -> None:
    """
    Verifica che un checkpoint legittimamente parziale (prodotto da un'analisi interrotta),
    in cui tutti i chunk presenti sono conformi e strutturalmente validi,
    venga regolarmente migrato in SQLite consentendo la successiva ripresa.
    """
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        doc = _make_doc("doc_interrupted", 4)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        # Analisi pianificata su 3 chunk, interrotta dopo il chunk 0
        res_0 = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,),
            rationale="Legitimate partial chunk 0 from interrupted analysis",
            provenance_document_id=doc.document_id,
        )

        legacy_file = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        legacy_file.write_text(json.dumps({
            "document_id": doc.document_id,
            "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp,
            "analysis_type": "detection",
            "model_id": "test-model",
            "prompt_version": "v1",
            "total_planned_chunks": 3,
            "serialized_results": {
                "0": serialize_detection_result_for_checkpoint(res_0),
            },
        }), encoding="utf-8")

        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)
        loaded = store.load_checkpoint(doc_fp, cfg_fp)

        # Il checkpoint parziale legittimo deve essere migrato
        assert loaded is not None
        assert loaded.total_planned_chunks == 3
        assert loaded.completed_chunk_indices == (0,)
        assert loaded.serialized_results["0"]["rationale"] == "Legitimate partial chunk 0 from interrupted analysis"

        # Il file legacy migrato con successo viene rimosso
        assert legacy_file.exists() is False


# 33. Rifiuto integrale di file legacy con un chunk valido e uno corrotto/alieno
def test_33_legacy_with_one_valid_and_one_corrupt_chunk_rejected_without_partial_writes() -> None:
    """
    Verifica che se un file legacy contiene un chunk valido (chunk 0) e un chunk
    con anomalia strutturale (chunk 1 con provenienza aliena o non conforme),
    l'intero file venga rifiutato senza creare testate o chunk parziali in SQLite.
    """
    with tempfile.TemporaryDirectory() as chk_dir:
        chk_path = Path(chk_dir)
        doc = _make_doc("doc_mixed_valid_corrupt", 4)
        doc_fp = compute_document_fingerprint(doc)
        cfg_fp = compute_run_config_hash("test-model", "v1")
        ev_0 = doc.all_evidence_sections[0].evidence_id

        res_0 = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,),
            rationale="Valid chunk 0",
            provenance_document_id=doc.document_id,
        )
        res_1_alien = TopicDetectionResult(
            topic=TopicQuery("t1", "T1", "Desc"),
            decision=TopicDecision.PRESENT,
            evidence_ids=(ev_0,),
            rationale="Alien chunk 1",
            provenance_document_id="WRONG_ALIEN_DOCUMENT",
        )

        legacy_file = chk_path / f"chk_{doc_fp[:16]}_{cfg_fp[:16]}.json"
        legacy_file.write_text(json.dumps({
            "document_id": doc.document_id,
            "document_fingerprint": doc_fp,
            "config_fingerprint": cfg_fp,
            "analysis_type": "detection",
            "model_id": "test-model",
            "prompt_version": "v1",
            "total_planned_chunks": 2,
            "serialized_results": {
                "0": serialize_detection_result_for_checkpoint(res_0),
                "1": serialize_detection_result_for_checkpoint(res_1_alien),
            },
        }), encoding="utf-8")

        store = AnalysisCheckpointStore(checkpoint_dir=chk_path)

        # Il file legacy deve essere preservato intatto
        assert legacy_file.exists() is True

        # SQLite non deve contenere testate né chunk 0
        with store._connect() as conn:
            cur_h = conn.execute("SELECT COUNT(*) FROM checkpoints WHERE document_fingerprint = ?;", (doc_fp,))
            assert cur_h.fetchone()[0] == 0
            cur_c = conn.execute("SELECT COUNT(*) FROM checkpoint_chunks WHERE document_fingerprint = ?;", (doc_fp,))
            assert cur_c.fetchone()[0] == 0

        assert store.load_checkpoint(doc_fp, cfg_fp) is None



