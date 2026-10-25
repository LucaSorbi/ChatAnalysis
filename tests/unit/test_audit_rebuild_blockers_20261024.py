"""
tests/unit/test_audit_rebuild_blockers_20261024.py
-------------------------------------------------
Suite di test avversari e controllati per verificare la risoluzione definitiva
dei 5 bloccanti segnalati dall'audit indipendente del 24/10/2026:
1. Retry per eccezioni emesse dal client (AiStructuredOutputError, AiBackendRequestError transiente).
2. Fail closed su HTTP 400 permanente, model mismatch, e registrazione tentativi/token se il retry fallisce.
3. Gestione corretta degli alias di evidenza oltre la 20esima evidenza (zero cut-off a 20).
4. Pubblicazione snapshot anti short-write, fsync, controllo dimensione, e nessuna mutazione umask.
5. Pulizia non distruttiva nel finally della CLI (zero rmtree, conservazione file alieni).
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from ai.backend import (
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiInvalidEvidenceCitationError,
    AiModelMismatchError,
    AiStructuredOutputError,
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
from analysis.service import LocalTopicAnalysisService
from scripts.preflight_checkpoint_resume import (
    _OWNERSHIP_TRACKER,
    _publish_snapshot_exclusively,
    _safe_cleanup_owned_temp_dir,
    create_consistent_readonly_snapshot,
    SnapshotCreationFailedError,
    SnapshotDirectorySyncFailedError,
    SnapshotPermissionDeniedError,
)
from tests.unit.test_topic_discovery_detection_stabilization import _create_synthetic_bundle


def _make_synth_doc(n_evidences: int = 5) -> ConversationEvidenceDocument:
    bundles = tuple(
        _create_synthetic_bundle(i, f"Test evidence content message number {i}")
        for i in range(1, n_evidences + 1)
    )
    return ConversationEvidenceDocument(
        document_id=f"doc_synth_{n_evidences}",
        bundles=bundles,
    )


TEST_TOPIC = TopicQuery(topic_id="t_001", label="Test Topic", description="Descrizione del topic di test")


class MockSequenceLlmClient(BaseLlmClient):
    """Client sintetico che produce una sequenza predeterminata di risposte o eccezioni."""

    def __init__(self, actions: list[Exception | str]):
        self.actions = list(actions)
        self.call_count = 0
        self.calls: list[dict] = []

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        schema: dict | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float = 30.0,
        max_tokens: int | None = None,
    ) -> LlmCompletionResponse:
        self.call_count += 1
        self.calls.append({"messages": messages, "model_id": model_id})
        if not self.actions:
            raise RuntimeError("Nessuna azione residua nel mock client.")
        act = self.actions.pop(0)
        if isinstance(act, Exception):
            raise act
        if isinstance(act, LlmCompletionResponse):
            return act
        return LlmCompletionResponse(
            content=act,
            model=model_id or "qwen2.5-7b-instruct",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            latency_seconds=0.1,
            raw_response={"choices": [{"message": {"content": act}, "finish_reason": "stop"}]},
        )


# ==============================================================================
# BLOCCANTE 1 — RETRY PER ECCEZIONI EMESSE DAL CLIENT
# ==============================================================================


def test_detection_first_call_raises_structured_output_error_then_valid():
    """TopicDetectionAnalyzer: primo tentativo fallisce con AiStructuredOutputError dal client, secondo ha successo."""
    doc = _make_synth_doc(3)
    valid_json = json.dumps({
        "decision": "PRESENT",
        "evidence_ids": ["E001"],
        "rationale": "Evidenza chiara nel testo.",
    })
    client = MockSequenceLlmClient([
        AiStructuredOutputError("Schema invalid / bad json from backend"),
        valid_json,
    ])
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    res = analyzer.detect_topic(
        document=doc,
        topic=TEST_TOPIC,
        model_id="qwen2.5-7b-instruct",
    )
    assert res.decision.value == "PRESENT"
    assert res.evidence_ids == ("msg::1::ORIGINAL_TEXT",)
    assert client.call_count == 2
    assert res.metadata["attempts"] == 2
    assert res.metadata["retries"] == 1
    assert res.metadata["total_tokens"] == 150


def test_discovery_first_call_raises_structured_output_error_then_valid():
    """TopicDiscoveryAnalyzer: primo tentativo fallisce con AiStructuredOutputError dal client, secondo ha successo."""
    doc = _make_synth_doc(3)
    valid_json = json.dumps({
        "topics": [
            {
                "label": "Tema 1",
                "short_description": "Descrizione tema 1",
                "evidence_ids": ["E001"],
            }
        ]
    })
    client = MockSequenceLlmClient([
        AiStructuredOutputError("Truncated or malformed JSON from client"),
        valid_json,
    ])
    analyzer = TopicDiscoveryAnalyzer(client=client, operational_mode=True)
    res = analyzer.discover_topics(
        document=doc,
        model_id="qwen2.5-7b-instruct",
    )
    assert len(res.topics) == 1
    assert res.topics[0].label == "Tema 1"
    assert client.call_count == 2
    assert res.metadata["attempts"] == 2
    assert res.metadata["retries"] == 1


def test_detection_first_call_raises_backend_request_transient_then_valid():
    """TopicDetectionAnalyzer: primo tentativo fallisce con errore HTTP 503 (transiente), secondo ha successo."""
    doc = _make_synth_doc(3)
    valid_json = json.dumps({
        "decision": "ABSENT",
        "evidence_ids": [],
        "rationale": "Nessuna evidenza rilevata.",
    })
    client = MockSequenceLlmClient([
        AiBackendRequestError("Service temporarily unavailable", status_code=503, error_category="HTTP_SERVER_ERROR"),
        valid_json,
    ])
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    res = analyzer.detect_topic(
        document=doc,
        topic=TEST_TOPIC,
        model_id="qwen2.5-7b-instruct",
    )
    assert res.decision.value == "ABSENT"
    assert client.call_count == 2
    assert res.metadata["attempts"] == 2


def test_detection_permanent_http_400_fails_closed():
    """TopicDetectionAnalyzer: errore HTTP 400 permanente (parametri non validi) fallisce chiuso immediatamente."""
    doc = _make_synth_doc(3)
    client = MockSequenceLlmClient([
        AiBackendRequestError("Bad request: invalid parameter", status_code=400, error_category="HTTP_INVALID_PARAMETERS"),
        "{}",
    ])
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    with pytest.raises(AiBackendRequestError):
        analyzer.detect_topic(
            document=doc,
            topic=TEST_TOPIC,
            model_id="qwen2.5-7b-instruct",
        )
    assert client.call_count == 1  # ZERO retry per errore permanente


def test_discovery_permanent_http_400_fails_closed():
    """TopicDiscoveryAnalyzer: errore HTTP 400 permanente fallisce chiuso immediatamente."""
    doc = _make_synth_doc(3)
    client = MockSequenceLlmClient([
        AiBackendRequestError("Schema unsupported", status_code=400, error_category="HTTP_SCHEMA_REJECTED"),
        "{}",
    ])
    analyzer = TopicDiscoveryAnalyzer(client=client, operational_mode=True)
    with pytest.raises(AiBackendRequestError):
        analyzer.discover_topics(
            document=doc,
            model_id="qwen2.5-7b-instruct",
        )
    assert client.call_count == 1


def test_detection_second_call_still_fails_records_attempts_2():
    """Se entrambi i tentativi falliscono, viene sollevata l'eccezione finale con attempts=2."""
    doc = _make_synth_doc(3)
    client = MockSequenceLlmClient([
        AiStructuredOutputError("Error 1"),
        AiStructuredOutputError("Error 2"),
    ])
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    with pytest.raises(AiStructuredOutputError) as exc_info:
        analyzer.detect_topic(
            document=doc,
            topic=TEST_TOPIC,
            model_id="qwen2.5-7b-instruct",
        )
    assert client.call_count == 2
    assert getattr(exc_info.value, "attempts", None) == 2
    assert getattr(exc_info.value, "retries", None) == 1


def test_discovery_second_call_still_fails_records_attempts_2():
    """Se entrambi i tentativi falliscono in Discovery, l'eccezione reca attempts=2."""
    doc = _make_synth_doc(3)
    client = MockSequenceLlmClient([
        AiStructuredOutputError("Error 1"),
        AiStructuredOutputError("Error 2"),
    ])
    analyzer = TopicDiscoveryAnalyzer(client=client, operational_mode=True)
    with pytest.raises(AiStructuredOutputError) as exc_info:
        analyzer.discover_topics(
            document=doc,
            model_id="qwen2.5-7b-instruct",
        )
    assert client.call_count == 2
    assert getattr(exc_info.value, "attempts", None) == 2


def test_model_mismatch_fails_closed_zero_retry():
    """Model mismatch solleva AiModelMismatchError immediatamente senza tentativi ulteriori."""
    doc = _make_synth_doc(3)
    resp = LlmCompletionResponse(
        content="{}",
        model="unauthorized-model-xyz",
    )
    client = MockSequenceLlmClient([resp])
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    with pytest.raises(AiModelMismatchError):
        analyzer.detect_topic(
            document=doc,
            topic=TEST_TOPIC,
            model_id="qwen2.5-7b-instruct",
        )
    assert client.call_count == 1


def test_detection_over_20_evidences_contract_contains_all_aliases():
    """Con oltre 20 evidenze (es. 25), la direttiva di retry include tutti gli alias senza cut-off a 20."""
    doc = _make_synth_doc(25)
    # Primo tentativo: il modello cita un ID inesistente 'E999'
    invalid_first = json.dumps({
        "decision": "PRESENT",
        "evidence_ids": ["E999"],
        "rationale": "Citazione errata",
    })
    # Secondo tentativo: il modello cita 'E025' (oltre il vecchio limite di 20)
    valid_second = json.dumps({
        "decision": "PRESENT",
        "evidence_ids": ["E025"],
        "rationale": "Citazione corretta di E025",
    })
    client = MockSequenceLlmClient([invalid_first, valid_second])
    analyzer = TopicDetectionAnalyzer(client=client, operational_mode=True)
    res = analyzer.detect_topic(
        document=doc,
        topic=TEST_TOPIC,
        model_id="qwen2.5-7b-instruct",
    )
    assert res.decision.value == "PRESENT"
    assert res.evidence_ids == ("msg::25::ORIGINAL_TEXT",)
    assert client.call_count == 2

    # Verifica che il messaggio di retry contenga sia E001 sia E025
    retry_prompt = client.calls[1]["messages"][-1]["content"]
    assert "'E001'" in retry_prompt
    assert "'E021'" in retry_prompt
    assert "'E025'" in retry_prompt


# ==============================================================================
# BLOCCANTE 3 — SHORT-WRITE E PUBBLICAZIONE ESCLUSIVA SNAPSHOT
# ==============================================================================


def test_publish_snapshot_handles_short_writes_correctly(tmp_path):
    """Verifica che _publish_snapshot_exclusively completi la scrittura anche in presenza di short-write ripetuti."""
    src_data = b"X" * 1000
    src_file = tmp_path / "source.db"
    src_file.write_bytes(src_data)

    target_file = tmp_path / "published_snapshot.db"

    real_os_write = os.write

    # Simula un driver di sistema che scrive un solo byte per chiamata
    def short_write(fd, buf):
        return real_os_write(fd, buf[:1])

    with patch("os.write", side_effect=short_write):
        _publish_snapshot_exclusively(src_file, target_file, chunk_size=32)

    assert target_file.exists()
    assert target_file.read_bytes() == src_data


def test_publish_snapshot_fails_closed_on_zero_byte_write(tmp_path):
    """Zero-byte write solleva SnapshotCreationFailedError fail-closed."""
    src_file = tmp_path / "source.db"
    src_file.write_bytes(b"DATA")
    target_file = tmp_path / "target.db"

    with patch("os.write", return_value=0):
        with pytest.raises(SnapshotCreationFailedError):
            _publish_snapshot_exclusively(src_file, target_file, chunk_size=32)


def test_publish_snapshot_preexisting_target_fails_closed_without_clobber(tmp_path):
    """File target preesistente non viene toccato né troncato e solleva FileExistsError."""
    src_file = tmp_path / "source.db"
    src_file.write_bytes(b"NEW_DATA")

    target_file = tmp_path / "target.db"
    target_file.write_bytes(b"ORIGINAL_DATA_DO_NOT_CLOBBER")

    with pytest.raises(FileExistsError):
        _publish_snapshot_exclusively(src_file, target_file)

    assert target_file.read_bytes() == b"ORIGINAL_DATA_DO_NOT_CLOBBER"


# ==============================================================================
# BLOCCANTE 4 — PULIZIA NON DISTRUTTIVA SENZA RMTREE
# ==============================================================================


def test_safe_cleanup_owned_temp_dir_preserves_foreign_files(tmp_path):
    """Se una directory registrata riceve file alieni concorrenti, la pulizia non distruttiva rifiuta la cancellazione."""
    temp_dir = tmp_path / "owned_temp_dir"
    temp_dir.mkdir()
    _OWNERSHIP_TRACKER.register_temp_dir(temp_dir)

    # File alieno/concorrente non registrato nel tracker
    alien_file = temp_dir / "user_important_data.db"
    alien_file.write_text("CRITICAL_USER_DATA", encoding="utf-8")

    # Tentativo di pulizia: DEVE fallire (ritorna False) e preservare intatti directory e file
    cleaned = _safe_cleanup_owned_temp_dir(temp_dir)
    assert cleaned is False
    assert temp_dir.exists()
    assert alien_file.exists()
    assert alien_file.read_text(encoding="utf-8") == "CRITICAL_USER_DATA"


# ==============================================================================
# AUDIT 24/10/2026 — PROBLEMA A (FSYNC ERROR PROPAGATION) E PROBLEMA B (ATOMICITY)
# ==============================================================================


def test_publish_snapshot_propagates_fsync_error_and_never_declares_success(tmp_path):
    """
    PROBLEMA A: os.fsync fallito solleva SnapshotCreationFailedError e NON dichiara successo.
    Il target finale non viene creato né esposto, e nessun file residuo viene abbandonato.
    """
    src_file = tmp_path / "source_payload.db"
    src_file.write_bytes(b"VALID_SQLITE_PAYLOAD" * 50)
    target_file = tmp_path / "published_target.db"

    with patch("os.fsync", side_effect=OSError(5, "I/O error")):
        with pytest.raises(SnapshotCreationFailedError) as exc_info:
            _publish_snapshot_exclusively(src_file, target_file)

        err_msg = str(exc_info.value)
        assert "fsync fallito" in err_msg or "Errore durante la scrittura" in err_msg
        assert not target_file.exists(), "Il target_file NON deve esistere se fsync è fallito!"

    # Verifica che non rimangano file temporanei di staging orfani
    orphan_staging = [p for p in tmp_path.iterdir() if p.name.startswith(".published_target.db.tmp_")]
    assert len(orphan_staging) == 0, f"Trovati file di staging orfani: {orphan_staging}"


def test_publish_snapshot_atomic_visibility_no_partial_target_during_write(tmp_path):
    """
    PROBLEMA B: Durante la scrittura frammentata a blocchi da 16 byte di una sorgente di 4096 byte,
    il target_file NON esiste e NON è visibile a nessun lettore concorrente prima del completamento.
    Solo dopo fsync e linking atomico il file completo appare istantaneamente.
    """
    src_data = b"0123456789ABCDEF" * 256  # 4096 byte
    src_file = tmp_path / "full_source.db"
    src_file.write_bytes(src_data)

    target_file = tmp_path / "atomic_target.db"

    real_os_write = os.write
    write_observations = []

    def observing_write(fd, buf):
        # Ispezione dello stato del file di destinazione durante OGNI os.write (compresi i primi 16 byte)
        target_visible = target_file.exists()
        write_observations.append(target_visible)
        return real_os_write(fd, buf)

    with patch("os.write", side_effect=observing_write):
        _publish_snapshot_exclusively(src_file, target_file, chunk_size=16)

    # Verifica che ci siano state scritture progressive
    assert len(write_observations) > 1, "Devono essere state effettuate scritture a blocchi multipli"
    # Durante NESSUNA scrittura il target_file doveva essere visibile
    assert all(vis is False for vis in write_observations), (
        "Il target_file è risultato visibile parzialmente durante la scrittura dei chunk!"
    )

    # Al termine della funzione, il file esiste ed è integro al 100%
    assert target_file.exists()
    assert target_file.stat().st_size == 4096
    assert target_file.read_bytes() == src_data


def test_consistent_readonly_snapshot_sqlite_synthetic_cycle_propagates_fsync_error(tmp_path):
    """
    PROBLEMA A + B (CICLO SQLITE SINTETICO):
    Creazione snapshot su un database SQLite reale con WAL sintetico.
    Se fsync fallisce sul percorso di destinazione esterno, la transazione fallisce,
    il target non viene creato e il DB sorgente rimane 100% intatto.
    """
    src_dir = tmp_path / "source_workspace"
    src_dir.mkdir()
    src_db = src_dir / "chat_synthetic.db"
    conn = sqlite3.connect(str(src_db))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE test_data (id INTEGER PRIMARY KEY, note TEXT);")
    for i in range(100):
        conn.execute("INSERT INTO test_data (note) VALUES (?);", (f"record_{i}",))
    conn.commit()
    conn.close()

    assert src_db.exists()
    src_hash_before = src_db.read_bytes()

    dest_dir = tmp_path / "external_snapshots"
    dest_dir.mkdir()
    dest_snap = dest_dir / "final_snapshot.db"

    # Patch mirata di os.fsync per sollevare OSError(5, 'I/O error')
    with patch("os.fsync", side_effect=OSError(5, "I/O error")):
        with pytest.raises(SnapshotCreationFailedError):
            create_consistent_readonly_snapshot(src_db, dest_path=dest_snap)

    # Il file di snapshot esterno non deve esistere
    assert not dest_snap.exists()
    # Il DB sorgente e i suoi byte non devono aver subito alcuna alterazione distruttiva
    assert src_db.read_bytes() == src_hash_before


def test_consistent_readonly_snapshot_sqlite_synthetic_cycle_success_atomic(tmp_path):
    """
    PROBLEMA A + B (SUCCESSO END-TO-END CON CICLO SQLITE):
    Creazione snapshot su un database SQLite reale in modalità WAL verso percorso esterno esplicito.
    Verifica che il DB finale consolidato sia integro, leggibile e privo di residui staging.
    """
    src_dir = tmp_path / "source_workspace"
    src_dir.mkdir()
    src_db = src_dir / "chat_synthetic_success.db"
    conn = sqlite3.connect(str(src_db))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, text TEXT);")
    for i in range(50):
        conn.execute("INSERT INTO evidence (text) VALUES (?);", (f"evidence_sample_{i}",))
    conn.commit()
    conn.close()

    dest_dir = tmp_path / "external_snapshots"
    dest_dir.mkdir()
    dest_snap = dest_dir / "clean_snapshot_published.db"
    result_snap, created_dir = create_consistent_readonly_snapshot(src_db, dest_path=dest_snap)

    assert result_snap == dest_snap
    assert dest_snap.exists()

    # Verifica integrità SQLite sul file pubblicato
    chk_conn = sqlite3.connect(str(dest_snap))
    rows = chk_conn.execute("SELECT count(*) FROM evidence;").fetchone()[0]
    integrity = chk_conn.execute("PRAGMA integrity_check;").fetchone()[0]
    chk_conn.close()

    assert rows == 50
    assert integrity == "ok"

    # Nessun file temporaneo di staging rimasto nella directory
    staging_residues = [p for p in dest_dir.iterdir() if ".clean_snapshot_published.db.tmp_" in p.name]
    assert len(staging_residues) == 0


# ==============================================================================
# AUDIT 24/10/2026 CHIUSURA — CASI 1 (POSIX NO-RENAME) E 2 (DIRECTORY FSYNC EIO)
# ==============================================================================


def test_publish_snapshot_posix_link_eopnotsupp_fails_closed_and_preserves_concurrent_file(tmp_path):
    """
    CASO 1 / TEST A: Se os.link fallisce con EOPNOTSUPP su ambiente POSIX,
    la funzione DEVE fallire chiuso con SnapshotCreationFailedError e NON deve tentare
    un rename distruttivo su target_file creato concorrentemente.
    Il file concorrente DEVE sopravvivere intatto.
    """
    src_file = tmp_path / "source_800b.db"
    src_file.write_bytes(b"A" * 800)

    dest_dir = tmp_path / "published_dir"
    dest_dir.mkdir()
    target_file = dest_dir / "target_snapshot.db"

    def mock_link_with_concurrent_race(src, dst):
        # Simula creazione concorrente di target_file prima o durante il fallimento hardlink
        target_file.write_bytes(b"CONCURRENT_FILE_DO_NOT_OVERWRITE")
        raise OSError(errno.EOPNOTSUPP, "Operation not supported")

    with patch("os.link", side_effect=mock_link_with_concurrent_race), \
         patch("sys.platform", "linux"), \
         patch("os.rename") as mock_rename:

        with pytest.raises(SnapshotCreationFailedError) as exc_info:
            _publish_snapshot_exclusively(src_file, target_file)

        assert "hard link fallito" in str(exc_info.value).lower() or "fail-closed" in str(exc_info.value).lower()
        # os.rename non deve MAI essere stato chiamato sul branch POSIX
        mock_rename.assert_not_called()

    # Il file concorrente non deve essere stato sovrascritto
    assert target_file.exists()
    assert target_file.read_bytes() == b"CONCURRENT_FILE_DO_NOT_OVERWRITE"

    # Nessun file di staging rimasto orfano
    staging_left = [p for p in dest_dir.iterdir() if p.name.startswith(".target_snapshot.db.tmp_")]
    assert len(staging_left) == 0


def test_publish_snapshot_link_permission_or_io_fails_closed_zero_rename(tmp_path):
    """
    CASO 1 / TEST B: Se os.link fallisce con errori gravi di kernel/disco (EACCES, EIO, ENOSPC),
    la funzione non deve mascherare l'errore tentando un rename arbitrario.
    """
    src_file = tmp_path / "src_test.db"
    src_file.write_bytes(b"DATA" * 50)
    target_file = tmp_path / "target_out.db"

    for err_code in (errno.EACCES, errno.EIO, errno.ENOSPC):
        with patch("os.link", side_effect=OSError(err_code, "Kernel FS Error")), \
             patch("os.rename") as mock_rename:

            with pytest.raises(SnapshotCreationFailedError):
                _publish_snapshot_exclusively(src_file, target_file)

            mock_rename.assert_not_called()
            assert not target_file.exists()


def test_publish_snapshot_directory_fsync_eio_raises_directory_sync_failed_and_preserves_target(tmp_path):
    """
    CASO 2 / TEST F: Se os.fsync sulla directory genitrice fallisce con errore fisico reale (EIO),
    la funzione NON deve dichiarare successo durevole: DEVE sollevare SnapshotDirectorySyncFailedError.
    Il target_file (già pubblicato e integro nei suoi byte) DEVE essere preservato,
    e lo stato di durabilità incerta (DURABILITY_UNCERTAIN) deve essere osservabile.
    """
    src_file = tmp_path / "src_file.db"
    payload = b"SQLITE_VALID_PERSISTENT_DATA_12345" * 10
    src_file.write_bytes(payload)

    dest_dir = tmp_path / "target_dir"
    dest_dir.mkdir()
    target_file = dest_dir / "target_out.db"

    real_fsync = os.fsync
    real_fstat = os.fstat

    def selective_fsync(fd):
        # Ispeziona il descrittore: se è una directory, simula errore fisico EIO
        try:
            st = real_fstat(fd)
            import stat
            if stat.S_ISDIR(st.st_mode):
                raise OSError(errno.EIO, "Simulated disk I/O error on directory metadata fsync")
        except Exception as e:
            if isinstance(e, OSError) and e.errno == errno.EIO:
                raise
        return real_fsync(fd)

    # Simula apertura directory supportata
    fake_dir_fd = 9999

    def mock_open(path, flags, mode=0o777):
        p = Path(path)
        if p == dest_dir:
            return fake_dir_fd
        return real_open(path, flags, mode)

    real_open = os.open

    def mocked_fsync(fd):
        if fd == fake_dir_fd:
            raise OSError(errno.EIO, "Physical I/O failure on directory sync")
        return real_fsync(fd)

    def mocked_close(fd):
        if fd == fake_dir_fd:
            return
        real_close(fd)

    real_close = os.close

    with patch("os.open", side_effect=mock_open), \
         patch("os.fsync", side_effect=mocked_fsync), \
         patch("os.close", side_effect=mocked_close):

        with pytest.raises(SnapshotDirectorySyncFailedError) as exc_info:
            _publish_snapshot_exclusively(src_file, target_file)

        err = exc_info.value
        assert err.durability_status == "DURABILITY_UNCERTAIN"
        assert err.errno_code == errno.EIO
        assert "DURABILITY_UNCERTAIN" in str(err)
        assert "preservato" in str(err).lower() or "integro" in str(err).lower()

    # NOTA CRITICA: il target_file esterno DEVE esistere e conservare tutti i byte integri!
    assert target_file.exists()
    assert target_file.read_bytes() == payload


def test_publish_snapshot_directory_fsync_unsupported_returns_non_verified_no_exception(tmp_path):
    """
    CASO 2 / TEST G: Se il filesystem non supporta fsync su directory (es. EINVAL o ENOTSUP o PermissionError su Windows),
    la pubblicazione riesce MA i metadati documentano esplicitamente che la durabilità directory
    è NON_VERIFIED (mai presentata come DURABLE_VERIFIED).
    """
    src_file = tmp_path / "src_file.db"
    src_file.write_bytes(b"DATA" * 20)

    dest_dir = tmp_path / "dest_unsupported"
    dest_dir.mkdir()
    target_file = dest_dir / "target.db"

    fake_dir_fd = 8888
    real_open = os.open
    real_close = os.close
    real_fsync = os.fsync

    def mock_open(path, flags, mode=0o777):
        if Path(path) == dest_dir:
            return fake_dir_fd
        return real_open(path, flags, mode)

    def mocked_fsync(fd):
        if fd == fake_dir_fd:
            # Filesystem che risponde EINVAL su fsync dir
            raise OSError(errno.EINVAL, "Invalid argument for directory fsync")
        return real_fsync(fd)

    def mocked_close(fd):
        if fd == fake_dir_fd:
            return
        real_close(fd)

    with patch("os.open", side_effect=mock_open), \
         patch("os.fsync", side_effect=mocked_fsync), \
         patch("os.close", side_effect=mocked_close):

        metrics = _publish_snapshot_exclusively(src_file, target_file)

    assert target_file.exists()
    assert metrics["file_durability_verified"] is True
    assert metrics["directory_metadata_durability"] == "NON_VERIFIED"
    assert metrics["directory_fsync_supported"] is False


def test_consistent_readonly_snapshot_directory_fsync_eio_propagates_and_preserves_target(tmp_path):
    """
    CASO 2 / INTEGRAZIONE SQLITE CON ERRORE DIRECTORY FSYNC:
    In un ciclo SQLite completo, se fsync directory fallisce con EIO:
    1. Viene sollevata SnapshotDirectorySyncFailedError con DURABILITY_UNCERTAIN.
    2. Il target_file pubblicato esiste ed è leggibile da sqlite3.
    3. Il database sorgente rimane intatto.
    """
    src_dir = tmp_path / "src_space"
    src_dir.mkdir()
    src_db = src_dir / "app_source.db"
    conn = sqlite3.connect(str(src_db))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT);")
    conn.execute("INSERT INTO items (val) VALUES ('item_alpha'), ('item_beta');")
    conn.commit()
    conn.close()

    dest_dir = tmp_path / "dest_space"
    dest_dir.mkdir()
    dest_snap = dest_dir / "sqlite_snapshot.db"

    fake_dir_fd = 7777
    real_open = os.open
    real_close = os.close
    real_fsync = os.fsync

    def mock_open(path, flags, mode=0o777):
        if Path(path) == dest_dir:
            return fake_dir_fd
        return real_open(path, flags, mode)

    def mocked_fsync(fd):
        if fd == fake_dir_fd:
            raise OSError(errno.EIO, "Physical failure on directory sync")
        return real_fsync(fd)

    def mocked_close(fd):
        if fd == fake_dir_fd:
            return
        real_close(fd)

    with patch("os.open", side_effect=mock_open), \
         patch("os.fsync", side_effect=mocked_fsync), \
         patch("os.close", side_effect=mocked_close):

        with pytest.raises(SnapshotDirectorySyncFailedError) as exc_info:
            create_consistent_readonly_snapshot(src_db, dest_path=dest_snap)

        err = exc_info.value
        assert err.durability_status == "DURABILITY_UNCERTAIN"

    # Il database pubblicato deve esistere ed essere integro e consultabile
    assert dest_snap.exists()
    snap_conn = sqlite3.connect(str(dest_snap))
    count = snap_conn.execute("SELECT count(*) FROM items;").fetchone()[0]
    integrity = snap_conn.execute("PRAGMA integrity_check;").fetchone()[0]
    snap_conn.close()

    assert count == 2
    assert integrity == "ok"


def test_consistent_readonly_snapshot_directory_fsync_unsupported_records_non_verified(tmp_path):
    """
    CASO 2 / INTEGRAZIONE SQLITE CON DIRECTORY FSYNC NON SUPPORTATO:
    Se fsync dir non è supportato dal filesystem:
    1. L'operazione ritorna con successo.
    2. staged_metrics riporta 'directory_metadata_durability': 'NON_VERIFIED'.
    """
    src_dir = tmp_path / "src_space"
    src_dir.mkdir()
    src_db = src_dir / "app_source.db"
    conn = sqlite3.connect(str(src_db))
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT);")
    conn.execute("INSERT INTO items (val) VALUES ('val_1');")
    conn.commit()
    conn.close()

    dest_dir = tmp_path / "dest_space"
    dest_dir.mkdir()
    dest_snap = dest_dir / "sqlite_snap_unsupported.db"

    fake_dir_fd = 6666
    real_open = os.open
    real_close = os.close
    real_fsync = os.fsync

    def mock_open(path, flags, mode=0o777):
        if Path(path) == dest_dir:
            return fake_dir_fd
        return real_open(path, flags, mode)

    def mocked_fsync(fd):
        if fd == fake_dir_fd:
            raise OSError(errno.ENOTSUP, "Operation not supported on directory")
        return real_fsync(fd)

    def mocked_close(fd):
        if fd == fake_dir_fd:
            return
        real_close(fd)

    with patch("os.open", side_effect=mock_open), \
         patch("os.fsync", side_effect=mocked_fsync), \
         patch("os.close", side_effect=mocked_close):

        snap_file, temp_dir, metrics = create_consistent_readonly_snapshot(
            src_db, dest_path=dest_snap, return_metrics=True
        )

    assert dest_snap.exists()
    assert metrics["directory_metadata_durability"] == "NON_VERIFIED"
    assert metrics["directory_fsync_supported"] is False


def test_publish_snapshot_directory_open_eio_fails_closed_and_preserves_target(tmp_path):
    """
    TEST A (Audit 25/10/2026):
    Se os.open sulla directory genitrice solleva OSError(errno.EIO),
    la pubblicazione NON deve dichiarare successo: DEVE sollevare SnapshotDirectorySyncFailedError
    con durability_status='DURABILITY_UNCERTAIN' ed errno_code=EIO.
    Il file target (1024 byte sintetici) DEVE essere conservato intatto (nessun unlink o corruzione).
    """
    src_file = tmp_path / "src_1024.db"
    payload = b"SYNTHETIC_EIO_DATA_RECORD_1024_BYTE" * 28  # 36 * 28 = 1008
    payload += b"=" * (1024 - len(payload))
    assert len(payload) == 1024
    src_file.write_bytes(payload)

    dest_dir = tmp_path / "dest_eio_dir"
    dest_dir.mkdir()
    target_file = dest_dir / "target_published.db"

    real_open = os.open

    def mock_open(path, flags, mode=0o777):
        p = Path(path)
        if p == dest_dir:
            raise OSError(errno.EIO, "Simulated disk hardware I/O error during directory open")
        return real_open(path, flags, mode)

    with patch("os.open", side_effect=mock_open):
        with pytest.raises(SnapshotDirectorySyncFailedError) as exc_info:
            _publish_snapshot_exclusively(src_file, target_file)

        err = exc_info.value
        assert err.durability_status == "DURABILITY_UNCERTAIN"
        assert err.errno_code == errno.EIO
        assert "DURABILITY_UNCERTAIN" in str(err)
        assert "preservato" in str(err).lower() or "integro" in str(err).lower()

    # Verifica conservazione integra del file target (1024 byte esatti)
    assert target_file.exists()
    assert target_file.stat().st_size == 1024
    assert target_file.read_bytes() == payload


def test_publish_snapshot_directory_open_enospc_fails_closed_and_preserves_target(tmp_path):
    """
    TEST B (Audit 25/10/2026):
    Se os.open sulla directory genitrice solleva OSError(errno.ENOSPC),
    la pubblicazione NON deve dichiarare successo: DEVE sollevare SnapshotDirectorySyncFailedError
    con durability_status='DURABILITY_UNCERTAIN' ed errno_code=ENOSPC.
    Il file target DEVE essere conservato intatto.
    """
    src_file = tmp_path / "src_1024.db"
    payload = b"ENOSPC_TEST_PAYLOAD_1024_BYTES_RECORD" * 27
    payload += b"X" * (1024 - len(payload))
    assert len(payload) == 1024
    src_file.write_bytes(payload)

    dest_dir = tmp_path / "dest_enospc_dir"
    dest_dir.mkdir()
    target_file = dest_dir / "target_published.db"

    real_open = os.open

    def mock_open(path, flags, mode=0o777):
        p = Path(path)
        if p == dest_dir:
            raise OSError(errno.ENOSPC, "No space left on device during directory open")
        return real_open(path, flags, mode)

    with patch("os.open", side_effect=mock_open):
        with pytest.raises(SnapshotDirectorySyncFailedError) as exc_info:
            _publish_snapshot_exclusively(src_file, target_file)

        err = exc_info.value
        assert err.durability_status == "DURABILITY_UNCERTAIN"
        assert err.errno_code == errno.ENOSPC
        assert "DURABILITY_UNCERTAIN" in str(err)

    assert target_file.exists()
    assert target_file.stat().st_size == 1024
    assert target_file.read_bytes() == payload


def test_publish_snapshot_directory_open_eacces_fails_closed_and_preserves_target(tmp_path, monkeypatch):
    """
    TEST C (Audit 25/10/2026):
    Su sistemi POSIX, se os.open sulla directory genitrice solleva PermissionError/EACCES/EPERM,
    tale errore rappresenta un fallimento operativo reale di autorizzazione/permessi.
    NON deve essere confuso con 'filesystem non supportato' restituendo successo falso.
    DEVE sollevare SnapshotDirectorySyncFailedError con DURABILITY_UNCERTAIN,
    preservando il file target integro.
    """
    src_file = tmp_path / "src_1024.db"
    payload = b"EACCES_PERMISSION_PAYLOAD_1024_BYTES" * 28
    payload += b"#" * (1024 - len(payload))
    assert len(payload) == 1024
    src_file.write_bytes(payload)

    dest_dir = tmp_path / "dest_eacces_dir"
    dest_dir.mkdir()
    target_file = dest_dir / "target_published.db"

    real_open = os.open

    def mock_open(path, flags, mode=0o777):
        p = Path(path)
        if p == dest_dir:
            raise PermissionError(errno.EACCES, "Permission denied for directory opening")
        return real_open(path, flags, mode)

    # Simula piattaforma POSIX per testare la specifica policy
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "name", "posix")

    with patch("os.open", side_effect=mock_open):
        with pytest.raises(SnapshotDirectorySyncFailedError) as exc_info:
            _publish_snapshot_exclusively(src_file, target_file)

        err = exc_info.value
        assert err.durability_status == "DURABILITY_UNCERTAIN"
        assert err.errno_code == errno.EACCES
        assert "DURABILITY_UNCERTAIN" in str(err)

    assert target_file.exists()
    assert target_file.stat().st_size == 1024
    assert target_file.read_bytes() == payload


def test_consistent_readonly_snapshot_directory_open_eio_propagates_and_preserves_target(tmp_path):
    """
    TEST INTEGRATIVO SQLITE (Audit 25/10/2026):
    In un ciclo completo create_consistent_readonly_snapshot su database SQLite in WAL mode:
    se os.open sulla directory genitrice solleva EIO:
    1. Viene sollevata SnapshotDirectorySyncFailedError con DURABILITY_UNCERTAIN.
    2. Il target_file pubblicato esiste, è completo e passa PRAGMA integrity_check='ok'.
    3. Il database sorgente e i relativi sidecar (-wal, -shm) non subiscono alcuna alterazione.
    4. Nessuna cartella o file di staging orfano permane nel sistema.
    """
    src_workspace = tmp_path / "src_workspace"
    src_workspace.mkdir()
    src_db = src_workspace / "evidence_audit.db"

    conn = sqlite3.connect(str(src_db))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, entry TEXT);")
    for i in range(100):
        conn.execute("INSERT INTO audit_log (entry) VALUES (?);", (f"record_{i}",))
    conn.commit()
    conn.close()

    wal_file = Path(str(src_db) + "-wal")
    src_sha_before = hashlib.sha256(src_db.read_bytes()).hexdigest()
    wal_sha_before = hashlib.sha256(wal_file.read_bytes()).hexdigest() if wal_file.exists() else None

    dest_dir = tmp_path / "ext_snapshots"
    dest_dir.mkdir()
    dest_snap = dest_dir / "audit_snapshot.db"

    real_open = os.open

    def mock_open(path, flags, mode=0o777):
        p = Path(path)
        if p == dest_dir:
            raise OSError(errno.EIO, "Physical disk failure during parent directory open")
        return real_open(path, flags, mode)

    with patch("os.open", side_effect=mock_open):
        with pytest.raises(SnapshotDirectorySyncFailedError) as exc_info:
            create_consistent_readonly_snapshot(src_db, dest_path=dest_snap)

        err = exc_info.value
        assert err.durability_status == "DURABILITY_UNCERTAIN"
        assert err.errno_code == errno.EIO

    # Target preservato integro
    assert dest_snap.exists()
    snap_conn = sqlite3.connect(str(dest_snap))
    try:
        check = snap_conn.execute("PRAGMA integrity_check;").fetchone()[0]
        assert check == "ok"
        count = snap_conn.execute("SELECT count(*) FROM audit_log;").fetchone()[0]
        assert count == 100
    finally:
        snap_conn.close()

    # Sorgenti intatti al 100%
    assert hashlib.sha256(src_db.read_bytes()).hexdigest() == src_sha_before
    if wal_sha_before is not None:
        assert hashlib.sha256(wal_file.read_bytes()).hexdigest() == wal_sha_before



