"""
tests/unit/test_checkpoint_safe_diagnostics.py
----------------------------------------------
Test unitari per lo strumento di diagnostica sicura dei checkpoint:
scripts/diagnose_checkpoints_safe.py.

Verifica le correzioni e i requisiti di sicurezza/affidabilità dell'audit conclusivo:
1. Risoluzione crash su chunk 10 malformato: nessun KeyError('evidence_validation_notice').
2. Allineamento al modello TopicDiscoveryResult: topics vuota consentita; short_description non vuota obbligatoria.
3. Privacy rigorosa e allowlist chiusa: nessun marcatore fittizio o metadato arbitrario esposto
   (diagnostic_category, failure_phase, finish_reason, created_at, updated_at, model_id, prompt_version).
4. Confronto sui metadati ORIGINALI per la compatibilità prima dell'applicazione della sanificazione.
5. Robusteza su schemi SQLite incompleti: gestione di colonne mancanti con codice standard SCHEMA_MISMATCH
   senza messaggi d'errore grezzi e senza interrompere l'analisi multi-database.
6. Query statiche su tabelle note senza interpolazione da sqlite_master.
7. Rilevazione anomalie di metadati (METADATA_DISCORDANCE_ANOMALY) a parità di config_fingerprint.
8. Descrizione neutrale dello stato osservato dei checkpoint (nessuna etichetta automatica Run 1/Run 2).
9. Modalità read-only rigorosa senza fallback in scrittura.
10. Analisi multi-database con etichette opache.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch
import pytest

from scripts.diagnose_checkpoints_safe import (
    SafeCheckpointAuditor,
    _sanitize_path,
    _sanitize_doc_id,
    _abbreviate_fp,
    _safe_int_range,
    _sanitize_model_id,
    _sanitize_prompt_version,
    _sanitize_timestamp,
    _sanitize_diagnostic_category,
    _sanitize_failure_phase,
    _sanitize_finish_reason,
    _strip_and_sanitize_recursively,
    _verify_chunk_payload_structure,
)


def _populate_mock_checkpoint_db(
    db_path: Path,
    doc_fp: str = "a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
    cfg_fp_1: str = "1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
    cfg_fp_2: str = "9999888877776666555544443333222211110000ffffeeeeddddccccbbbbaaaa",
    doc_id: str = "doc_whatsapp_export_forensic_case_1049",
) -> None:
    """Crea uno scenario SQLite con Sessione 1 (20/26) e Sessione 2 (24/26)."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE checkpoints (
            document_fingerprint TEXT NOT NULL,
            config_fingerprint TEXT NOT NULL,
            document_id TEXT NOT NULL,
            analysis_type TEXT NOT NULL,
            model_id TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            total_planned_chunks INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (document_fingerprint, config_fingerprint)
        );
    """)

    cur.execute("""
        CREATE TABLE checkpoint_chunks (
            document_fingerprint TEXT NOT NULL,
            config_fingerprint TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            serialized_result TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (document_fingerprint, config_fingerprint, chunk_index)
        );
    """)

    cur.execute("""
        CREATE TABLE checkpoint_chunk_diagnostics (
            document_fingerprint TEXT NOT NULL,
            config_fingerprint TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_id TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 1,
            duration_seconds REAL NOT NULL DEFAULT 0.0,
            model_id TEXT NOT NULL DEFAULT '',
            http_status INTEGER,
            finish_reason TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            error_type TEXT,
            diagnostic_category TEXT NOT NULL DEFAULT 'NONE',
            failure_phase TEXT,
            error_detail TEXT,
            json_valid INTEGER NOT NULL DEFAULT 0,
            schema_valid INTEGER NOT NULL DEFAULT 0,
            evidence_valid INTEGER NOT NULL DEFAULT 0,
            checkpoint_saved INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (document_fingerprint, config_fingerprint, chunk_index)
        );
    """)

    # Testata 1: 20/26
    cur.execute("""
        INSERT INTO checkpoints VALUES (?, ?, ?, 'discovery', 'qwen2.5-7b-instruct', 'discovery_operational', 26, '2026-10-15T10:00:00Z', '2026-10-15T10:30:00Z')
    """, (doc_fp, cfg_fp_1, doc_id))

    # Testata 2: 24/26
    cur.execute("""
        INSERT INTO checkpoints VALUES (?, ?, ?, 'discovery', 'qwen2.5-7b-instruct', 'discovery_operational', 26, '2026-10-18T14:00:00Z', '2026-10-18T14:45:00Z')
    """, (doc_fp, cfg_fp_2, doc_id))

    # Testata 1 chunks: 20 completati (mancanti: 2, 4, 19, 20, 21, 25; Chunk 10 presente e integro!)
    run1_completed = [i for i in range(26) if i not in (2, 4, 19, 20, 21, 25)]
    for idx in run1_completed:
        payload = {
            "topics": [
                {
                    "label": "Private Topic Label",
                    "short_description": "Private Description Valid",
                    "evidence_ids": ["unified:priv:1", "unified:priv:2"],
                }
            ],
            "provenance_document_id": doc_id,
            "metadata": {"finish_reason": "stop"}
        }
        cur.execute("""
            INSERT INTO checkpoint_chunks VALUES (?, ?, ?, ?, '2026-10-15T10:15:00Z')
        """, (doc_fp, cfg_fp_1, idx, json.dumps(payload)))

    # Testata 2 chunks: 24 completati (mancanti: 10, 19; Chunk 10 e 19 assenti!)
    run2_completed = [i for i in range(26) if i not in (10, 19)]
    for idx in run2_completed:
        payload = {
            "topics": [
                {
                    "label": "Private Topic Label Run 2",
                    "short_description": "Private Description Valid 2",
                    "evidence_ids": ["unified:priv:3"],
                }
            ],
            "provenance_document_id": doc_id,
            "metadata": {"finish_reason": "stop"}
        }
        cur.execute("""
            INSERT INTO checkpoint_chunks VALUES (?, ?, ?, ?, '2026-10-18T14:20:00Z')
        """, (doc_fp, cfg_fp_2, idx, json.dumps(payload)))

    # Diagnostica per Testata 2: chunk 10 e 19 falliti con INVALID_EVIDENCE_ID
    for failed_idx in (10, 19):
        cur.execute("""
            INSERT INTO checkpoint_chunk_diagnostics VALUES (
                ?, ?, ?, ?, 'FAILED', 2, 4.5, 'qwen2.5-7b-instruct', 200, 'stop',
                420, 65, 'AiInvalidEvidenceCitationError', 'INVALID_EVIDENCE_ID',
                'evidence_validation', 'Citazione di evidence_id non presente',
                1, 1, 0, 0, '2026-10-18T14:25:00Z'
            )
        """, (doc_fp, cfg_fp_2, failed_idx, f"{doc_id}::chunk::{failed_idx}"))

    conn.commit()
    conn.close()


@pytest.mark.unit
class TestCheckpointSafeDiagnostics:
    """Suite di test per la diagnostica sicura dei checkpoint su SQLite."""

    def test_01_sanitization_helpers(self):
        """Verifica che gli helper di sanificazione non espongano percorsi, doc_id o hash completi."""
        # Sanificazione percorsi (etichette opache, zero nomi di file o directory esposti - Audit Fix B1)
        san_path = _sanitize_path(Path("/var/opt/secret_folder/checkpoints.db"))
        assert "/var/opt" not in san_path
        assert "secret_folder" not in san_path
        assert "checkpoints.db" not in san_path
        assert san_path == "DATABASE_REDACTED"

        # Riproduzione audit indipendente: percorso Linux con nome utente e DB sensibile
        audit_case_linux = _sanitize_path("/home/Mario_Rossi/CASE_INDIVIDUAL_2026.db")
        assert audit_case_linux == "DATABASE_REDACTED"
        assert "Mario_Rossi" not in audit_case_linux
        assert "CASE_INDIVIDUAL_2026" not in audit_case_linux

        # Percorso Windows testato su ambiente Linux/cross-platform
        audit_case_win = _sanitize_path(r"C:\Users\Mario_Rossi\CASE_INDIVIDUAL_2026.db")
        assert audit_case_win == "DATABASE_REDACTED"
        assert "Mario_Rossi" not in audit_case_win
        assert "CASE_INDIVIDUAL_2026" not in audit_case_win

        # File chat ed export forense
        audit_chat = _sanitize_path("/home/Mario_Rossi/chat_export.txt")
        assert audit_chat == "CHAT_FILE_REDACTED"
        assert "Mario_Rossi" not in audit_chat
        assert "chat_export" not in audit_chat

        # Percorso generico
        audit_folder = _sanitize_path("/home/Mario_Rossi/secret_directory")
        assert audit_folder == "PATH_REDACTED"
        assert "Mario_Rossi" not in audit_folder
        assert "secret_directory" not in audit_folder

        # Sanificazione doc_id: NESSUN prefisso o suffisso originale esposto
        raw_doc = "confidential_investigation_case_12345"
        san_doc = _sanitize_doc_id(raw_doc)
        assert "confidential" not in san_doc
        assert "investigation" not in san_doc
        assert "case" not in san_doc
        assert "12345" not in san_doc
        assert san_doc.startswith("doc_***[token=")
        assert f"len={len(raw_doc)}]" in san_doc

        # Abbreviazione hash e validazione fingerprint canonico SHA-256
        abbrev = _abbreviate_fp("a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0")
        assert len(abbrev) == 15  # 12 char + "..."
        assert abbrev == "a1b2c3d4e5f6..."
        # Valori non canonici o arbitrari -> FINGERPRINT_INVALID_OPAQUE (nessun prefisso esposto)
        assert _abbreviate_fp("PERSONAL_NAME_LUCA_FIRSTLAST") == "FINGERPRINT_INVALID_OPAQUE"
        assert _abbreviate_fp("short_non_hex") == "FINGERPRINT_INVALID_OPAQUE"
        assert _abbreviate_fp("z" * 64) == "FINGERPRINT_INVALID_OPAQUE"
        assert _abbreviate_fp(None) == "FINGERPRINT_INVALID_OPAQUE"

        # Validazione contatori numerici SQLite
        assert _safe_int_range("26", 1, 100_000) == 26
        assert _safe_int_range(26, 1, 100_000) == 26
        assert _safe_int_range("MALFORMED_VALUE", 1, 100_000) == "NUMERIC_VALUE_MALFORMED"
        assert _safe_int_range(-10, 1, 100_000) == "NUMERIC_VALUE_MALFORMED"
        assert _safe_int_range(10**12, 1, 100_000) == "NUMERIC_VALUE_MALFORMED"
        assert _safe_int_range(True, 1, 100_000) == "NUMERIC_VALUE_MALFORMED"

        # Sanificazione model_id (allowlist chiusa)
        assert _sanitize_model_id("qwen2.5-7b-instruct") == "qwen2.5-7b-instruct"
        assert _sanitize_model_id("C:/models/secret_weights.bin") == "MODEL_OPAQUE"
        assert _sanitize_model_id("arbitrary_unknown_model_identifier") == "MODEL_OPAQUE"

        # Sanificazione prompt_version (allowlist chiusa)
        assert _sanitize_prompt_version("discovery_operational") == "discovery_operational"
        assert _sanitize_prompt_version("arbitrary_prompt_v99_injected") == "PROMPT_VERSION_OPAQUE"

        # Sanificazione timestamp (validazione rigorosa ISO 8601)
        assert _sanitize_timestamp("2026-10-18T14:00:00Z").startswith("2026-10-18T14:00:00")
        assert _sanitize_timestamp("2026-10-18T14:00:00Z_MARKER_EXTRA") == "TIMESTAMP_OPAQUE"
        assert _sanitize_timestamp("invalid_timestamp_string") == "TIMESTAMP_OPAQUE"

        # Sanificazione ricorsiva: rimozione di chiavi _full, troncamento hash e mascheramento percorsi
        sample_dict = {
            "document_fingerprint_full": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
            "nested": {
                "config_fingerprint_full": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
                "raw_hash": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
                "raw_path": "/home/investigator/cases/db.sqlite",
                "safe_label": "valid_value",
            },
        }
        cleaned = _strip_and_sanitize_recursively(sample_dict)
        assert "document_fingerprint_full" not in cleaned
        assert "config_fingerprint_full" not in cleaned["nested"]
        assert cleaned["nested"]["raw_hash"] == "ffffffffffff..."
        assert "<PATH_REDACTED>" in cleaned["nested"]["raw_path"]
        assert "/home/investigator" not in cleaned["nested"]["raw_path"]
        assert cleaned["nested"]["safe_label"] == "valid_value"

    def test_02_full_audit_on_two_sessions(self):
        """Verifica i quesiti 1, 2, 3, 4, 6 con descrizione neutrale dello stato osservato."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "checkpoints.db"
            _populate_mock_checkpoint_db(db_path)

            auditor = SafeCheckpointAuditor(target_db_path=db_path)
            res = auditor.run_full_audit(specific_db=db_path)

            assert res["status"] == "SUCCESS"
            assert res["sessions_found_count"] == 2

            # Quesito 1: Censimento testate
            s1 = res["sessions"][0]
            s2 = res["sessions"][1]
            assert s1["prompt_version"] == "discovery_operational"
            assert s2["prompt_version"] == "discovery_operational"
            assert s1["total_planned_chunks"] == 26
            assert s2["total_planned_chunks"] == 26

            # Quesito 2: Conteggio chunk per sessione e stato osservato (no 'Run 1' o 'Run 2')
            chunks_s = res["chunks_per_session"]
            assert len(chunks_s) == 2
            assert chunks_s[0]["physical_chunks_count"] == 20
            assert chunks_s[0]["structurally_intact_chunks_count"] == 20
            assert chunks_s[0]["observed_state"] == "OBSERVED_20_OF_26_CHUNKS"
            assert chunks_s[0]["evidence_validation"] == "EVIDENCE_VALIDATION_NOT_PERFORMED"

            assert chunks_s[1]["physical_chunks_count"] == 24
            assert chunks_s[1]["structurally_intact_chunks_count"] == 24
            assert chunks_s[1]["observed_state"] == "OBSERVED_24_OF_26_CHUNKS"
            assert set(chunks_s[1]["missing_indices"]) == {10, 19}

            # Quesito 3: Verifica Chunk 10
            c10_status = res["chunk_10_status"]
            assert len(c10_status) == 1
            assert c10_status[0]["chunk_index"] == 10
            assert c10_status[0]["physical_presence"] is True
            assert c10_status[0]["structural_integrity"] == "STRUCTURALLY_INTACT"
            assert "evidence_validation_notice" in c10_status[0]
            assert c10_status[0]["evidence_validation"] == "EVIDENCE_VALIDATION_NOT_PERFORMED"

            # Quesito 4: Compatibilità tra configurazioni
            comp = res["compatibility_analysis"]
            assert comp["same_document_fingerprint"] is True
            assert comp["same_config_fingerprint"] is False
            assert comp["config_fingerprint_mismatch_detected"] is True
            assert comp["runtime_interchangeable"] is False
            assert comp["status"] == "CONFIG_FINGERPRINT_MISMATCH"

            # Quesito 6: Verdetto di recupero rigoroso
            verdict = res["recovery_verdict"]
            assert verdict["safe_recovery_verified"] is False
            assert verdict["safe_recovery_possible"] is False
            assert verdict["chunk_10_reconcilable_from_prior_session"] is False
            assert verdict["evidence_validation"] == "EVIDENCE_VALIDATION_NOT_PERFORMED"

    def test_03_zero_leak_and_no_identifying_metadata(self):
        """Verifica l'assenza totale di dati investigativi, prefissi identificativi o hash a 64 char."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "checkpoints.db"
            _populate_mock_checkpoint_db(db_path)

            auditor = SafeCheckpointAuditor(target_db_path=db_path)
            res = auditor.run_multi_database_audit()
            res_str = json.dumps(res)

            # 1. Nessun dato investigativo o personale
            assert "Private Topic Label" not in res_str
            assert "Private Description" not in res_str
            assert "unified:priv:1" not in res_str
            assert "doc_whatsapp_export_forensic_case_1049" not in res_str

            # 2. Nessuna chiave '_full'
            assert "_full" not in res_str

            # 3. Nessun hash SHA-256 esadecimale a 64 caratteri intatto
            hex_64_matches = re.findall(r'"[0-9a-fA-F]{64}"', res_str)
            assert len(hex_64_matches) == 0

            # 4. Nessun percorso assoluto utente esposto
            assert "C:\\" not in res_str
            assert "/home/" not in res_str
            assert "/Users/" not in res_str

    def test_04_missing_database_graceful_handling(self):
        """Verifica la gestione trasparente con codici standard quando checkpoints.db non esiste."""
        auditor = SafeCheckpointAuditor(target_db_path="/tmp/non_existent_checkpoints.db")
        res = auditor.run_full_audit(specific_db=Path("/tmp/non_existent_checkpoints.db"))
        assert res["status"] == "ERROR_NO_DATABASE_FOUND"
        assert res["error_code"] == "DATABASE_NOT_FOUND"
        assert res["recovery_verdict"]["safe_recovery_verified"] is False

    def test_05_strictly_read_only_no_fallback(self):
        """Apertura SQLite esclusivamente con mode=ro; se fallisce, codice standard e nessun fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "checkpoints.db"
            _populate_mock_checkpoint_db(db_path)

            auditor = SafeCheckpointAuditor(target_db_path=db_path)

            with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("Simulated mode=ro failure")):
                res = auditor.audit_single_database(db_path)
                assert res["status"] == "ERROR_READ_ONLY_ACCESS_FAILED"
                assert res["error_code"] == "SQLITE_READ_ONLY_MODE_FAILED"
                assert "Audit interrotto" in res["safety_notice"]

    def test_06_malformed_chunk_payload_no_crash(self):
        """PROBLEMA 1 RISOLTO: Payload malformato non deve provocare KeyError('evidence_validation_notice')."""
        doc_id = "doc_target_case_42"

        # 1. Payload non valido / broken JSON
        res_broken = _verify_chunk_payload_structure("{invalid_json_bytes", doc_id, 10)
        assert res_broken["structural_integrity"] == "INVALID_JSON_SYNTAX"
        assert "evidence_validation_notice" in res_broken
        assert res_broken["evidence_validation"] == "EVIDENCE_VALIDATION_NOT_PERFORMED"

        # 2. Payload vuoto
        res_empty = _verify_chunk_payload_structure("", doc_id, 10)
        assert res_empty["structural_integrity"] == "INVALID_EMPTY_PAYLOAD"
        assert "evidence_validation_notice" in res_empty

        # 3. Root non dizionario
        res_non_dict = _verify_chunk_payload_structure(json.dumps(["topics"]), doc_id, 10)
        assert res_non_dict["structural_integrity"] == "INVALID_ROOT_NOT_DICT"
        assert "evidence_validation_notice" in res_non_dict

        # 4. Verifica in scenario SQLite reale con payload corrotto nel chunk 10
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "checkpoints.db"
            _populate_mock_checkpoint_db(db_path)

            # Inserisce un chunk 10 con JSON corrotto
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("""
                UPDATE checkpoint_chunks SET serialized_result = '{CORRUPTED_JSON_UNCLOSED'
                WHERE chunk_index = 10
            """)
            conn.commit()
            conn.close()

            auditor = SafeCheckpointAuditor(target_db_path=db_path)
            res = auditor.run_full_audit(specific_db=db_path)
            assert res["status"] == "SUCCESS"
            c10_status = res["chunk_10_status"][0]
            assert c10_status["structural_integrity"] == "INVALID_JSON_SYNTAX"
            assert "evidence_validation_notice" in c10_status

    def test_07_topic_discovery_model_alignment(self):
        """PROBLEMA 2 RISOLTO: Allineamento a TopicDiscoveryResult (topics vuota ammessa; short_description non vuota)."""
        doc_id = "doc_case_test_alignment"

        # 1. TopicDiscoveryResult ammette lista 'topics' vuota (0 argomenti scoperti)
        payload_topics_empty = json.dumps({
            "topics": [],
            "provenance_document_id": doc_id,
        })
        res_t_empty = _verify_chunk_payload_structure(payload_topics_empty, doc_id, 10)
        assert res_t_empty["structural_integrity"] == "STRUCTURALLY_INTACT"
        assert res_t_empty["schema_status"] == "CONFORMANT_EMPTY_TOPICS_LIST"
        assert res_t_empty["topic_count"] == 0

        # 2. short_description vuota o solo spazi: DEVE essere rifiutata per invarianti DiscoveredTopic
        payload_empty_desc = json.dumps({
            "topics": [
                {"label": "Valid Label", "short_description": "   ", "evidence_ids": ["ev:1"]}
            ],
            "provenance_document_id": doc_id,
        })
        res_bad_desc = _verify_chunk_payload_structure(payload_empty_desc, doc_id, 10)
        assert res_bad_desc["structural_integrity"] == "COMPROMISED_INVALID_SCHEMA"
        assert res_bad_desc["schema_status"] == "INVALID_TOPIC_FIELDS_OR_TYPES"

        # 3. label vuota o solo spazi: rifiutata
        payload_empty_label = json.dumps({
            "topics": [
                {"label": "   ", "short_description": "Valid Desc", "evidence_ids": ["ev:1"]}
            ],
            "provenance_document_id": doc_id,
        })
        res_bad_label = _verify_chunk_payload_structure(payload_empty_label, doc_id, 10)
        assert res_bad_label["structural_integrity"] == "COMPROMISED_INVALID_SCHEMA"

        # 4. Citazioni duplicate: rifiutate
        payload_dup = json.dumps({
            "topics": [
                {"label": "Valid Label", "short_description": "Valid Desc", "evidence_ids": ["ev:1", "ev:1"]}
            ],
            "provenance_document_id": doc_id,
        })
        res_dup = _verify_chunk_payload_structure(payload_dup, doc_id, 10)
        assert res_dup["structural_integrity"] == "COMPROMISED_DUPLICATE_CITATIONS"

        # 5. Provenance aliena: rifiutata
        payload_alien = json.dumps({
            "topics": [
                {"label": "Valid Label", "short_description": "Valid Desc", "evidence_ids": ["ev:1"]}
            ],
            "provenance_document_id": "alien_doc_999",
        })
        res_alien = _verify_chunk_payload_structure(payload_alien, doc_id, 10)
        assert res_alien["structural_integrity"] == "COMPROMISED_ALIEN_PROVENANCE"

    def test_08_static_table_queries_and_missing_tables(self):
        """Interrogazione statica delle sole tabelle note senza interpolazione da sqlite_master."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "checkpoints.db"
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()

            # Crea tabella legittima checkpoints con tutte le colonne
            cur.execute("""
                CREATE TABLE checkpoints (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    analysis_type TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    total_planned_chunks INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (document_fingerprint, config_fingerprint)
                );
            """)
            # Crea tabella estranea utente (non deve essere interrogata né elencata)
            cur.execute("CREATE TABLE secret_arbitrary_user_table (id INT, secret_text TEXT);")
            cur.execute("INSERT INTO secret_arbitrary_user_table VALUES (1, 'sensitive_information');")

            conn.commit()
            conn.close()

            auditor = SafeCheckpointAuditor(target_db_path=db_path)
            res = auditor.audit_single_database(db_path)

            assert res["status"] == "SUCCESS"
            assert "secret_arbitrary_user_table" not in res["tables_present"]
            assert "secret_arbitrary_user_table" not in res["row_counts"]
            assert "checkpoints" in res["tables_present"]

    def test_09_metadata_discordance_anomaly(self):
        """Rilevazione METADATA_DISCORDANCE_ANOMALY a parità di config_fingerprint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "checkpoints.db"
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE checkpoints (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    analysis_type TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    total_planned_chunks INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (document_fingerprint, config_fingerprint)
                );
            """)

            doc_fp = "same_doc_hash_11111111111111111111111111111111111111111111111111111111"
            cfg_fp = "same_cfg_hash_22222222222222222222222222222222222222222222222222222222"

            cur.execute("""
                INSERT INTO checkpoints VALUES (?, ?, 'doc_1', 'discovery', 'qwen2.5-7b-instruct', 'discovery_operational', 26, '2026-10-18T14:00:00Z', '2026-10-18T14:30:00Z')
            """, (doc_fp, cfg_fp))
            conn.commit()
            conn.close()

            auditor = SafeCheckpointAuditor(target_db_path=db_path)

            # Test diretto con metadati originali discordanti a parità di cfg_fp
            session_a = {
                "doc_fp": doc_fp, "cfg_fp": cfg_fp, "doc_id": "doc_1",
                "raw_model_id": "qwen2.5-7b-instruct", "raw_prompt_version": "discovery_operational",
                "raw_analysis_type": "discovery", "total_planned": 26
            }
            session_b = {
                "doc_fp": doc_fp, "cfg_fp": cfg_fp, "doc_id": "doc_1",
                "raw_model_id": "llama-3.1-8b-instruct", "raw_prompt_version": "discovery_operational",
                "raw_analysis_type": "discovery", "total_planned": 26
            }

            comp = auditor._evaluate_compatibility_rigorous([session_a, session_b])
            assert comp["same_config_fingerprint"] is True
            assert comp["same_model_id"] is False
            assert comp["status"] == "METADATA_DISCORDANCE_ANOMALY"
            assert comp["runtime_interchangeable"] is False

    def test_10_multi_database_opaque_labels(self):
        """Multi-database con etichette opache e nessun percorso esposto."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db1 = root / "workspace" / "checkpoints.db"
            db2 = root / "home" / "checkpoints.db"
            db1.parent.mkdir(parents=True, exist_ok=True)
            db2.parent.mkdir(parents=True, exist_ok=True)

            _populate_mock_checkpoint_db(db1)
            _populate_mock_checkpoint_db(db2)

            auditor = SafeCheckpointAuditor()
            auditor.candidate_paths_with_scope = [
                ("WORKSPACE_LOCAL", db1),
                ("USER_HOME", db2),
            ]
            auditor.candidate_paths = [db1, db2]

            multi_res = auditor.run_multi_database_audit()
            summary = multi_res["multi_database_summary"]

            assert summary["total_databases_found"] == 2
            assert summary["different_paths_detected"] is True
            assert summary["database_references"] == ["database_1", "database_2"]
            assert "database_1" in multi_res["databases"]
            assert "database_2" in multi_res["databases"]
            assert multi_res["databases"]["database_1"]["location_scope"] == "WORKSPACE_LOCAL"
            assert multi_res["databases"]["database_2"]["location_scope"] == "USER_HOME"

    def test_11_adversarial_markers_in_diagnostic_metadata_sanitized(self):
        """
        REQUISITO 1: Verifica che marcatori fittizi inseriti nei campi diagnostici e temporali
        (diagnostic_category, failure_phase, finish_reason, created_at, updated_at, model_id, prompt_version)
        non compaiano in NESSUNA forma nell'output JSON o testuale.
        """
        marker_diag_cat = "MARKER_SECRET_DIAG_CAT_XYZ"
        marker_phase = "MARKER_SECRET_FAILURE_PHASE_123"
        marker_finish = "MARKER_SECRET_FINISH_REASON_ABC"
        marker_created = "MARKER_SECRET_CREATED_AT_DATE"
        marker_updated = "MARKER_SECRET_UPDATED_AT_DATE"
        marker_model = "MARKER_SECRET_MODEL_CUSTOM_WEIGHTS"
        marker_prompt = "MARKER_SECRET_PROMPT_VERSION_EXPLOIT"

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "checkpoints.db"
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()

            cur.execute("""
                CREATE TABLE checkpoints (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    analysis_type TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    total_planned_chunks INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (document_fingerprint, config_fingerprint)
                );
            """)
            cur.execute("""
                CREATE TABLE checkpoint_chunks (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    serialized_result TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (document_fingerprint, config_fingerprint, chunk_index)
                );
            """)
            cur.execute("""
                CREATE TABLE checkpoint_chunk_diagnostics (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    chunk_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    duration_seconds REAL NOT NULL DEFAULT 0.0,
                    model_id TEXT NOT NULL DEFAULT '',
                    http_status INTEGER,
                    finish_reason TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    error_type TEXT,
                    diagnostic_category TEXT NOT NULL DEFAULT 'NONE',
                    failure_phase TEXT,
                    error_detail TEXT,
                    json_valid INTEGER NOT NULL DEFAULT 0,
                    schema_valid INTEGER NOT NULL DEFAULT 0,
                    evidence_valid INTEGER NOT NULL DEFAULT 0,
                    checkpoint_saved INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (document_fingerprint, config_fingerprint, chunk_index)
                );
            """)

            doc_fp = "1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"
            cfg_fp = "222233334444555566667777888899990000aaaabbbbccccddddeeeeffff1111"

            # Inserisce testata con model_id, prompt_version e timestamp avversari
            cur.execute("""
                INSERT INTO checkpoints VALUES (?, ?, 'doc_adversarial_1', 'discovery', ?, ?, 26, ?, ?)
            """, (doc_fp, cfg_fp, marker_model, marker_prompt, marker_created, marker_updated))

            # Inserisce chunk 10 con timestamp avversario
            payload_c10 = json.dumps({
                "topics": [{"label": "L", "short_description": "D", "evidence_ids": ["e:1"]}],
                "provenance_document_id": "doc_adversarial_1",
            })
            cur.execute("""
                INSERT INTO checkpoint_chunks VALUES (?, ?, 10, ?, ?)
            """, (doc_fp, cfg_fp, payload_c10, marker_created))

            # Inserisce diagnostica con finish_reason, diagnostic_category, failure_phase, updated_at avversari
            cur.execute("""
                INSERT INTO checkpoint_chunk_diagnostics VALUES (
                    ?, ?, 10, 'doc_adversarial_1::chunk::10', 'FAILED', 1, 1.0,
                    ?, 200, ?, 10, 10, 'Exception', ?, ?,
                    'Confidential internal detail text that should never leak',
                    1, 1, 0, 0, ?
                )
            """, (doc_fp, cfg_fp, marker_model, marker_finish, marker_diag_cat, marker_phase, marker_updated))

            conn.commit()
            conn.close()

            auditor = SafeCheckpointAuditor(target_db_path=db_path)
            res = auditor.run_multi_database_audit()
            res_str = json.dumps(res)

            # Verifica tassativa: NESSUN marcatore fittizio deve comparire nell'output
            assert marker_diag_cat not in res_str, f"Trapelato marker_diag_cat: {marker_diag_cat}"
            assert marker_phase not in res_str, f"Trapelato marker_phase: {marker_phase}"
            assert marker_finish not in res_str, f"Trapelato marker_finish: {marker_finish}"
            assert marker_created not in res_str, f"Trapelato marker_created: {marker_created}"
            assert marker_updated not in res_str, f"Trapelato marker_updated: {marker_updated}"
            assert marker_model not in res_str, f"Trapelato marker_model: {marker_model}"
            assert marker_prompt not in res_str, f"Trapelato marker_prompt: {marker_prompt}"
            assert "Confidential internal detail text" not in res_str

            # Verifica sostituzione con codici opachi conformi
            db_res = next(iter(res["databases"].values()))
            session_0 = db_res["sessions"][0]
            assert session_0["model_id"] == "MODEL_OPAQUE"
            assert session_0["prompt_version"] == "PROMPT_VERSION_OPAQUE"
            assert session_0["created_at"] == "TIMESTAMP_OPAQUE"
            assert session_0["updated_at"] == "TIMESTAMP_OPAQUE"

            diag_0 = db_res["diagnostics_summary"][0]
            assert diag_0["diagnostic_category"] == "DIAGNOSTIC_CATEGORY_OPAQUE"
            assert diag_0["failure_phase"] == "failure_phase_opaque"
            assert diag_0["finish_reason"] == "finish_reason_opaque"
            assert diag_0["updated_at"] == "TIMESTAMP_OPAQUE"

    def test_12_incomplete_schema_and_missing_columns_handling(self):
        """
        REQUISITO 2: Database con schema incompleto (es. CREATE TABLE checkpoints (dummy TEXT);)
        deve restituire SCHEMA_MISMATCH senza crash e senza bloccare altri database candidati.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_incomplete = root / "incomplete_schema.db"
            db_valid = root / "valid_schema.db"

            # 1. Database con colonna mancante in checkpoints
            conn_inc = sqlite3.connect(str(db_incomplete))
            conn_inc.execute("CREATE TABLE checkpoints (dummy TEXT);")
            conn_inc.execute("INSERT INTO checkpoints VALUES ('invalid_row');")
            conn_inc.commit()
            conn_inc.close()

            # 2. Database valido di controllo
            _populate_mock_checkpoint_db(db_valid)

            auditor = SafeCheckpointAuditor()
            auditor.candidate_paths_with_scope = [
                ("WORKSPACE_LOCAL", db_incomplete),
                ("USER_HOME", db_valid),
            ]
            auditor.candidate_paths = [db_incomplete, db_valid]

            multi_res = auditor.run_multi_database_audit()
            databases = multi_res["databases"]

            assert len(databases) == 2
            # Database 1 (incompleto): deve restituire ERROR_SCHEMA_MISMATCH e codice SCHEMA_MISMATCH
            db1 = databases["database_1"]
            assert db1["status"] == "ERROR_SCHEMA_MISMATCH"
            assert db1["error_code"] == "SCHEMA_MISMATCH"
            assert "colonne richieste" in db1["safety_notice"]

            # Database 2 (valido): non deve essere bloccato e deve completare con SUCCESS!
            db2 = databases["database_2"]
            assert db2["status"] == "SUCCESS"
            assert db2["sessions_found_count"] == 2

    def test_13_raw_metadata_compatibility_before_sanitization(self):
        """
        REQUISITO 1: Verifica che la compatibilità confronti i metadati ORIGINALI
        prima dell'applicazione della sanificazione a codici opachi.
        """
        doc_fp = "1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"
        cfg_fp = "222233334444555566667777888899990000aaaabbbbccccddddeeeeffff1111"

        auditor = SafeCheckpointAuditor()

        # Due sessioni con modelli sconosciuti differenti (es. custom_model_alpha vs custom_model_beta)
        # Se confrontate dopo la sanificazione, entrambe sarebbero "MODEL_OPAQUE" (falsamente compatibili)
        session_a = {
            "doc_fp": doc_fp, "cfg_fp": cfg_fp, "doc_id": "doc_1",
            "raw_model_id": "custom_model_alpha", "raw_prompt_version": "discovery_operational",
            "raw_analysis_type": "discovery", "total_planned": 26
        }
        session_b = {
            "doc_fp": doc_fp, "cfg_fp": cfg_fp, "doc_id": "doc_1",
            "raw_model_id": "custom_model_beta", "raw_prompt_version": "discovery_operational",
            "raw_analysis_type": "discovery", "total_planned": 26
        }

        comp = auditor._evaluate_compatibility_rigorous([session_a, session_b])
        # A parità di cfg_fp, il modello originale è differente -> anomalia metadati!
        assert comp["same_model_id"] is False
        assert comp["status"] == "METADATA_DISCORDANCE_ANOMALY"
        assert comp["runtime_interchangeable"] is False

        # Verifica caso opposto: stesso modello sconosciuto (custom_model_alpha vs custom_model_alpha)
        session_c = {
            "doc_fp": doc_fp, "cfg_fp": cfg_fp, "doc_id": "doc_1",
            "raw_model_id": "custom_model_alpha", "raw_prompt_version": "discovery_operational",
            "raw_analysis_type": "discovery", "total_planned": 26
        }
        comp_identical = auditor._evaluate_compatibility_rigorous([session_a, session_c])
        assert comp_identical["same_model_id"] is True
        assert comp_identical["status"] == "FULLY_COMPATIBLE_IDENTICAL_CONFIGURATION"

    def test_14_adversarial_non_canonical_fingerprint_sanitized(self):
        """
        REQUISITO 1 HARDENING: Verifica che fingerprint non canonici (es. PERSONAL_NAME_LUCA_FIRSTLAST)
        non vengano abbreviati con esposizione del prefisso, ma sostituiti con FINGERPRINT_INVALID_OPAQUE,
        e che il marcatore fittizio non compaia né nel JSON né nel report testuale formattato.
        """
        import io
        import sys
        from scripts.diagnose_checkpoints_safe import print_formatted_report

        marker_doc_fp = "PERSONAL_NAME_LUCA_FIRSTLAST"
        marker_cfg_fp = "CONFIG_FP_SENSITIVE_INVESTIGATION_SECRET_999"

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "checkpoints.db"
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()

            cur.execute("""
                CREATE TABLE checkpoints (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    analysis_type TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    total_planned_chunks INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (document_fingerprint, config_fingerprint)
                );
            """)

            cur.execute("""
                CREATE TABLE checkpoint_chunks (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    serialized_result TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (document_fingerprint, config_fingerprint, chunk_index)
                );
            """)

            cur.execute("""
                CREATE TABLE checkpoint_chunk_diagnostics (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    chunk_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    duration_seconds REAL NOT NULL DEFAULT 0.0,
                    model_id TEXT NOT NULL DEFAULT '',
                    http_status INTEGER,
                    finish_reason TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    error_type TEXT,
                    diagnostic_category TEXT NOT NULL DEFAULT 'NONE',
                    failure_phase TEXT,
                    error_detail TEXT,
                    json_valid INTEGER NOT NULL DEFAULT 0,
                    schema_valid INTEGER NOT NULL DEFAULT 0,
                    evidence_valid INTEGER NOT NULL DEFAULT 0,
                    checkpoint_saved INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (document_fingerprint, config_fingerprint, chunk_index)
                );
            """)

            cur.execute("""
                INSERT INTO checkpoints VALUES (?, ?, 'doc_test', 'discovery', 'qwen2.5-7b-instruct', 'discovery_operational', 26, '2026-10-18T10:00:00Z', '2026-10-18T10:30:00Z')
            """, (marker_doc_fp, marker_cfg_fp))

            payload = json.dumps({
                "topics": [{"label": "T", "short_description": "D", "evidence_ids": ["e:1"]}],
                "provenance_document_id": "doc_test",
            })
            cur.execute("""
                INSERT INTO checkpoint_chunks VALUES (?, ?, 0, ?, '2026-10-18T10:10:00Z')
            """, (marker_doc_fp, marker_cfg_fp, payload))

            cur.execute("""
                INSERT INTO checkpoint_chunk_diagnostics VALUES (
                    ?, ?, 10, 'doc_test::chunk::10', 'FAILED', 1, 1.0,
                    'qwen2.5-7b-instruct', 200, 'stop', 10, 10, 'Exception', 'INVALID_EVIDENCE_ID',
                    'evidence_validation', 'details', 1, 1, 0, 0, '2026-10-18T10:15:00Z'
                )
            """, (marker_doc_fp, marker_cfg_fp))

            conn.commit()
            conn.close()

            auditor = SafeCheckpointAuditor(target_db_path=db_path)
            res = auditor.run_multi_database_audit()
            json_str = json.dumps(res)

            buf = io.StringIO()
            old_stdout = sys.stdout
            try:
                sys.stdout = buf
                print_formatted_report(res)
            finally:
                sys.stdout = old_stdout
            text_str = buf.getvalue()

            # Asserzioni su JSON: nessun marcatore o prefisso arbitrario esposto
            assert marker_doc_fp not in json_str
            assert marker_cfg_fp not in json_str
            assert "PERSONAL_NAM" not in json_str
            assert "CONFIG_FP" not in json_str
            assert "FINGERPRINT_INVALID_OPAQUE" in json_str

            # Asserzioni su Report testuale
            assert marker_doc_fp not in text_str
            assert marker_cfg_fp not in text_str
            assert "PERSONAL_NAM" not in text_str
            assert "CONFIG_FP" not in text_str
            assert "FINGERPRINT_INVALID_OPAQUE" in text_str

    def test_15_adversarial_malformed_numeric_sqlite_values(self):
        """
        REQUISITO 2 HARDENING: Se SQLite contiene valori non numerici in total_planned_chunks,
        chunk_index, attempts, http_status, lo script non deve terminare con ValueError né
        interrompere l'analisi degli altri database candidati, e deve restituire il codice
        standard NUMERIC_VALUE_MALFORMED senza esporre il marcatore fittizio originale.
        """
        import io
        import sys
        from scripts.diagnose_checkpoints_safe import print_formatted_report

        marker_planned = "MALFORMED_PLANNED_STRING_MARKER_999"
        marker_chunk_idx = "MALFORMED_CHUNK_INDEX_MARKER_777"
        marker_attempts = "MALFORMED_ATTEMPTS_MARKER_888"
        marker_http = "MALFORMED_HTTP_STATUS_MARKER_555"

        with tempfile.TemporaryDirectory() as tmpdir:
            db_malformed = Path(tmpdir) / "db_malformed.db"
            db_valid = Path(tmpdir) / "db_valid.db"

            # DB 1: valori numerici malformati
            conn = sqlite3.connect(str(db_malformed))
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE checkpoints (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    analysis_type TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    total_planned_chunks TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE checkpoint_chunks (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    chunk_index TEXT NOT NULL,
                    serialized_result TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE checkpoint_chunk_diagnostics (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    chunk_index TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts TEXT NOT NULL,
                    duration_seconds REAL NOT NULL DEFAULT 0.0,
                    model_id TEXT NOT NULL DEFAULT '',
                    http_status TEXT,
                    finish_reason TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    error_type TEXT,
                    diagnostic_category TEXT NOT NULL DEFAULT 'NONE',
                    failure_phase TEXT,
                    error_detail TEXT,
                    json_valid INTEGER NOT NULL DEFAULT 0,
                    schema_valid INTEGER NOT NULL DEFAULT 0,
                    evidence_valid INTEGER NOT NULL DEFAULT 0,
                    checkpoint_saved INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
            """)

            doc_fp = "1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"
            cfg_fp = "222233334444555566667777888899990000aaaabbbbccccddddeeeeffff1111"

            cur.execute("""
                INSERT INTO checkpoints VALUES (?, ?, 'doc_m', 'discovery', 'qwen2.5-7b-instruct', 'discovery_operational', ?, '2026-10-18T10:00:00Z', '2026-10-18T10:30:00Z')
            """, (doc_fp, cfg_fp, marker_planned))

            payload = json.dumps({"topics": [], "provenance_document_id": "doc_m"})
            cur.execute("""
                INSERT INTO checkpoint_chunks VALUES (?, ?, ?, ?, '2026-10-18T10:10:00Z')
            """, (doc_fp, cfg_fp, marker_chunk_idx, payload))

            cur.execute("""
                INSERT INTO checkpoint_chunk_diagnostics VALUES (
                    ?, ?, 10, 'doc_m::chunk::10', 'FAILED', ?, 1.0,
                    'qwen2.5-7b-instruct', ?, 'stop', 10, 10, 'Exception', 'INVALID_EVIDENCE_ID',
                    'evidence_validation', 'details', 1, 1, 0, 0, '2026-10-18T10:15:00Z'
                )
            """, (doc_fp, cfg_fp, marker_attempts, marker_http))
            conn.commit()
            conn.close()

            # DB 2: database valido conforme
            _populate_mock_checkpoint_db(db_valid)

            auditor = SafeCheckpointAuditor()
            auditor.candidate_paths_with_scope = [
                ("WORKSPACE_LOCAL", db_malformed),
                ("USER_HOME", db_valid),
            ]
            auditor.candidate_paths = [db_malformed, db_valid]

            multi_res = auditor.run_multi_database_audit()
            databases = multi_res["databases"]

            # Entrambi i database analizzati
            assert len(databases) == 2

            db1_data = databases["database_1"]
            assert db1_data["status"] == "SUCCESS"
            assert db1_data["sessions"][0]["total_planned_chunks"] == "NUMERIC_VALUE_MALFORMED"
            assert db1_data["diagnostics_summary"][0]["attempts"] == "NUMERIC_VALUE_MALFORMED"
            assert db1_data["diagnostics_summary"][0]["http_status"] is None

            db2_data = databases["database_2"]
            assert db2_data["status"] == "SUCCESS"
            assert db2_data["sessions_found_count"] == 2

            json_str = json.dumps(multi_res)
            buf = io.StringIO()
            old_stdout = sys.stdout
            try:
                sys.stdout = buf
                print_formatted_report(multi_res)
            finally:
                sys.stdout = old_stdout
            text_str = buf.getvalue()

            assert marker_planned not in json_str
            assert marker_chunk_idx not in json_str
            assert marker_attempts not in json_str
            assert marker_http not in json_str

            assert marker_planned not in text_str
            assert marker_chunk_idx not in text_str
            assert marker_attempts not in text_str
            assert marker_http not in text_str

            assert "NUMERIC_VALUE_MALFORMED" in json_str
            assert "NUMERIC_VALUE_MALFORMED" in text_str

    def test_16_adversarial_negative_and_overflow_numeric_values(self):
        """
        Verifica la gestione di valori numerici negativi o irragionevolmente grandi (> 100_000)
        senza crash e con assegnazione del codice standard NUMERIC_VALUE_MALFORMED.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "checkpoints.db"
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE checkpoints (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    analysis_type TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    total_planned_chunks INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE checkpoint_chunks (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    serialized_result TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE checkpoint_chunk_diagnostics (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    chunk_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    duration_seconds REAL NOT NULL DEFAULT 0.0,
                    model_id TEXT NOT NULL DEFAULT '',
                    http_status INTEGER,
                    finish_reason TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    error_type TEXT,
                    diagnostic_category TEXT NOT NULL DEFAULT 'NONE',
                    failure_phase TEXT,
                    error_detail TEXT,
                    json_valid INTEGER NOT NULL DEFAULT 0,
                    schema_valid INTEGER NOT NULL DEFAULT 0,
                    evidence_valid INTEGER NOT NULL DEFAULT 0,
                    checkpoint_saved INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
            """)

            doc_fp = "1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"
            cfg_fp = "222233334444555566667777888899990000aaaabbbbccccddddeeeeffff1111"

            # total_planned_chunks negativo (-10) e attempts irragionevolmente grande (999999999)
            cur.execute("""
                INSERT INTO checkpoints VALUES (?, ?, 'doc_neg', 'discovery', 'qwen2.5-7b-instruct', 'discovery_operational', -10, '2026-10-18T10:00:00Z', '2026-10-18T10:30:00Z')
            """, (doc_fp, cfg_fp))

            cur.execute("""
                INSERT INTO checkpoint_chunk_diagnostics VALUES (
                    ?, ?, 10, 'doc_neg::chunk::10', 'FAILED', 999999999, 1.0,
                    'qwen2.5-7b-instruct', 9999, 'stop', 10, 10, 'Exception', 'INVALID_EVIDENCE_ID',
                    'evidence_validation', 'details', 1, 1, 0, 0, '2026-10-18T10:15:00Z'
                )
            """, (doc_fp, cfg_fp))
            conn.commit()
            conn.close()

            auditor = SafeCheckpointAuditor(target_db_path=db_path)
            res = auditor.run_multi_database_audit()
            db_res = next(iter(res["databases"].values()))

            assert db_res["status"] == "SUCCESS"
            assert db_res["sessions"][0]["total_planned_chunks"] == "NUMERIC_VALUE_MALFORMED"
            assert db_res["diagnostics_summary"][0]["attempts"] == "NUMERIC_VALUE_MALFORMED"
            assert db_res["diagnostics_summary"][0]["http_status"] is None

    def test_17_adversarial_arbitrary_synthetic_markers_cross_diagnostic_entrypoints(self):
        """
        B5 — UNIFICAZIONE DIAGNOSTICHE SQLITE E ZERO LEAKAGE:
        Verifica adversarial sia per scripts/diagnose_checkpoints_safe.py sia per
        scripts/diagnose_ubuntu_live_24of26.py (Python API e CLI subprocess).
        Inietta in SQLite marcatori sintetici arbitrari, token non classificati,
        categorie inventate, canarini investigativi e percorsi malevoli.
        Verifica che NESSUN marcatore arbitrario compaia in:
        - Dizionari Python restituiti
        - Output serializzato JSON
        - stdout dei processi CLI
        - stderr dei processi CLI
        """
        from scripts.diagnose_ubuntu_live_24of26 import run_diagnostics

        adversarial_markers = [
            "X9K2P_CUSTOM_INJECTION_47",
            "PROMPT_VERSION_SECRET_INJECTION_99",
            "CUSTOM_CATEGORY_SECRET_MARKER",
            "PHASE_ARBITRARY_UNCLASSIFIED",
            "FINISH_REASON_MALICIOUS_TOKEN",
            "LEAK_DETAIL_INVESTIGATIVE_CANARY_42",
            "CANARY_SECRET_DOCUMENT_999",
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "checkpoints.db"
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()

            cur.execute("""
                CREATE TABLE checkpoints (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    analysis_type TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    total_planned_chunks INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE checkpoint_chunks (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    serialized_result TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE checkpoint_chunk_diagnostics (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    chunk_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    duration_seconds REAL NOT NULL DEFAULT 0.0,
                    model_id TEXT NOT NULL DEFAULT '',
                    http_status INTEGER,
                    finish_reason TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    error_type TEXT,
                    diagnostic_category TEXT NOT NULL DEFAULT 'NONE',
                    failure_phase TEXT,
                    error_detail TEXT,
                    json_valid INTEGER NOT NULL DEFAULT 0,
                    schema_valid INTEGER NOT NULL DEFAULT 0,
                    evidence_valid INTEGER NOT NULL DEFAULT 0,
                    checkpoint_saved INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE checkpoint_subchunks (
                    document_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    subchunk_index INTEGER NOT NULL,
                    serialized_result TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

            doc_fp = "1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"
            cfg_fp = "222233334444555566667777888899990000aaaabbbbccccddddeeeeffff1111"

            # Inserimento con tutti i marcatori arbitrari
            cur.execute("""
                INSERT INTO checkpoints VALUES (
                    ?, ?, 'CANARY_SECRET_DOCUMENT_999', 'discovery',
                    'X9K2P_CUSTOM_INJECTION_47', 'PROMPT_VERSION_SECRET_INJECTION_99',
                    26, '2026-10-18T10:00:00Z', '2026-10-18T10:30:00Z'
                )
            """, (doc_fp, cfg_fp))

            cur.execute("""
                INSERT INTO checkpoint_chunk_diagnostics VALUES (
                    ?, ?, 10, 'doc::chunk::10', 'FAILED', 2, 3.5,
                    'X9K2P_CUSTOM_INJECTION_47', 500, 'FINISH_REASON_MALICIOUS_TOKEN',
                    100, 50, 'CustomException', 'CUSTOM_CATEGORY_SECRET_MARKER',
                    'PHASE_ARBITRARY_UNCLASSIFIED', 'LEAK_DETAIL_INVESTIGATIVE_CANARY_42',
                    0, 0, 0, 0, '2026-10-18T10:15:00Z'
                )
            """, (doc_fp, cfg_fp))

            conn.commit()
            conn.close()

            # 1. Test API Python: SafeCheckpointAuditor
            auditor = SafeCheckpointAuditor(target_db_path=db_path)
            safe_res = auditor.run_multi_database_audit()
            safe_json = json.dumps(safe_res)
            for marker in adversarial_markers:
                assert marker not in safe_json, f"Marker '{marker}' leaked in SafeCheckpointAuditor JSON!"

            # 2. Test API Python: run_diagnostics (diagnose_ubuntu_live_24of26)
            ubuntu_res = run_diagnostics(db_path=db_path)
            ubuntu_json = json.dumps(ubuntu_res)
            for marker in adversarial_markers:
                assert marker not in ubuntu_json, f"Marker '{marker}' leaked in diagnose_ubuntu_live_24of26 JSON!"

            # 3. Test CLI Subprocess: scripts/diagnose_checkpoints_safe.py
            cli_safe = subprocess.run(
                [sys.executable, "scripts/diagnose_checkpoints_safe.py", "--db", str(db_path), "--json"],
                capture_output=True,
                text=True,
            )
            assert cli_safe.returncode == 0
            safe_stdout = cli_safe.stdout
            safe_stderr = cli_safe.stderr
            for marker in adversarial_markers:
                assert marker not in safe_stdout, f"Marker '{marker}' leaked in scripts/diagnose_checkpoints_safe.py stdout!"
                assert marker not in safe_stderr, f"Marker '{marker}' leaked in scripts/diagnose_checkpoints_safe.py stderr!"

            # 4. Test CLI Subprocess: scripts/diagnose_ubuntu_live_24of26.py
            cli_ubuntu = subprocess.run(
                [sys.executable, "scripts/diagnose_ubuntu_live_24of26.py", str(db_path)],
                capture_output=True,
                text=True,
            )
            assert cli_ubuntu.returncode == 0
            ubuntu_stdout = cli_ubuntu.stdout
            ubuntu_stderr = cli_ubuntu.stderr
            for marker in adversarial_markers:
                assert marker not in ubuntu_stdout, f"Marker '{marker}' leaked in scripts/diagnose_ubuntu_live_24of26.py stdout!"
                assert marker not in ubuntu_stderr, f"Marker '{marker}' leaked in scripts/diagnose_ubuntu_live_24of26.py stderr!"

