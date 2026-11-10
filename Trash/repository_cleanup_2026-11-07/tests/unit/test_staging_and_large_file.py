"""
tests/unit/test_staging_and_large_file.py
-----------------------------------------
Test di conformità per FASE BLOCKER 3:
LARGE-FILE INGESTION & LOCAL SQLITE STAGING.

Verifiche:
1. IngestionStagingStore: inserimento a batch e flush automatico.
2. IngestionStagingStore: recupero ordinato deterministico per sequenza (seq_order).
3. IngestionStagingStore: partizionamento per chat e isolamento.
4. IngestionStagingStore: estrazione titoli chat da record ausiliari.
5. IngestionStagingStore: context manager e chiusura sicura delle risorse SQLite.
6. Ingestion completa di stream sintetico tramite ingest_file_payload con staging attivo.
7. Preservazione dell'integrità del ConversationEvidenceDocument e dell'EvidenceIndex.
"""
from __future__ import annotations

import io
from pathlib import Path
import pytest

from ui.models import SourceFormat
from importer.models import RawRecord
from multimodal.evidence import EvidenceSourceType, TextEvidenceSection
from normalization.models import (
    CanonicalMessageType,
    NormalizedActor,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from ui.ingestion import ingest_file_payload
from ui.staging import IngestionStagingStore
from validation.models import ValidationResult


def _make_dummy_norm_record(
    seq: int,
    chat_id: str = "chat_1",
    sender: str = "Alice",
    text: str = "Test",
    record_type: str = "message",
) -> NormalizedRecord:
    raw = RawRecord(
        source_name="whatsapp_test",
        source_path="/dummy/path.txt",
        source_record_id=f"rec_{seq}",
        record_type=record_type,
        raw_fields={"subject": f"Title {chat_id}"} if record_type == "chat" else {},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    return NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name="whatsapp_test",
        source_record_id=f"rec_{seq}",
        record_type=record_type,
        chat_id=chat_id,
        actor_from=NormalizedActor(raw_value=sender, actor_type="alias"),
        actor_to=None,
        timestamp=NormalizedTimestamp(
            status=TimestampTzStatus.KNOWN_UTC,
            raw_value="2024-01-01 10:00:00",
            iso_string="2024-01-01T10:00:00Z",
        ),
        message_type=CanonicalMessageType.TEXT,
        text_content=text,
        media_reference=None,
    )


def test_staging_store_batch_insert_and_count(tmp_path: Path):
    db_path = tmp_path / "staging_test.sqlite"
    with IngestionStagingStore(db_path, batch_size=5) as store:
        for i in range(12):
            rec = _make_dummy_norm_record(seq=i, text=f"Message {i}")
            store.stage_record(rec)

        assert store.count() == 12
        assert store.count("message") == 12
        assert store.count("chat") == 0


def test_staging_store_deterministic_order_preserved(tmp_path: Path):
    db_path = tmp_path / "staging_test.sqlite"
    with IngestionStagingStore(db_path, batch_size=10) as store:
        for i in range(25):
            store.stage_record(_make_dummy_norm_record(seq=i, text=f"Message {i}"))

        retrieved = list(store.iter_all_records(chunk_size=7))
        assert len(retrieved) == 25
        for i, rec in enumerate(retrieved):
            assert rec.source_record_id == f"rec_{i}"
            assert rec.text_content == f"Message {i}"


def test_staging_store_chat_partitioning_and_isolation(tmp_path: Path):
    db_path = tmp_path / "staging_test.sqlite"
    with IngestionStagingStore(db_path) as store:
        # Chat 1: 5 messaggi
        for i in range(5):
            store.stage_record(_make_dummy_norm_record(seq=i, chat_id="chat_1"))
        # Chat 2: 7 messaggi
        for i in range(5, 12):
            store.stage_record(_make_dummy_norm_record(seq=i, chat_id="chat_2"))

        distinct_keys = store.get_distinct_chat_keys()
        assert distinct_keys == ["chat_1", "chat_2"]

        c1_records = list(store.iter_records_for_chat("chat_1"))
        assert len(c1_records) == 5
        assert all(r.chat_id == "chat_1" for r in c1_records)

        c2_records = list(store.iter_records_for_chat("chat_2"))
        assert len(c2_records) == 7
        assert all(r.chat_id == "chat_2" for r in c2_records)


def test_staging_store_extract_chat_titles(tmp_path: Path):
    db_path = tmp_path / "staging_test.sqlite"
    with IngestionStagingStore(db_path) as store:
        chat_rec = _make_dummy_norm_record(seq=1, chat_id="chat_alpha", record_type="chat")
        store.stage_record(chat_rec)

        titles = store.extract_chat_titles()
        assert titles.get("chat_alpha") == "Title chat_alpha"


def test_synthetic_stream_ingestion_with_staging(tmp_path: Path):
    """Verifica l'intero ciclo di ingest_file_payload con staging SQLite su un export WhatsApp valido."""
    content = (
        "10/01/2024, 09:00 - I messaggi e le chiamate sono crittografati end-to-end.\n"
        "10/01/2024, 09:01 - Alice: Primo messaggio di test\n"
        "10/01/2024, 09:02 - Bob: Secondo messaggio di risposta\n"
        "10/01/2024, 09:03 - Alice: Terzo messaggio con evidenza\n"
    )
    raw_bytes = content.encode("utf-8")
    result = ingest_file_payload(
        file_bytes_or_io=raw_bytes,
        original_filename="chat.txt",
        source_format=SourceFormat.WHATSAPP_EXPORT,
    )

    from ui.models import IngestionStatus
    assert result.summary.status == IngestionStatus.SUCCESS
    assert result.summary.raw_record_count == 4
    assert result.summary.unified_message_count == 4
    assert len(result.documents) == 1

    doc = list(result.documents.values())[0]
    assert len(doc.bundles) == 4
    assert len(doc.all_evidence_sections) == 4
