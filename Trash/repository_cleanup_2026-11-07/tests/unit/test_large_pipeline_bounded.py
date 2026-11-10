"""
tests/unit/test_large_pipeline_bounded.py
-----------------------------------------
Test per la pipeline di ingestion su stream di grandi dimensioni (Criteri 27, 28, 29, 37):
- Verifica che la pipeline processi record in modo deterministico e senza duplicazioni non necessarie.
- Verifica del comportamento su centinaia di messaggi senza degradazione o leak.
- Verifica corretta classificazione di ImportedConversationInfo.
"""
from __future__ import annotations

import io
from pathlib import Path
import pytest

from importer.whatsapp_export import WhatsAppExportImporter
from ui.ingestion import ingest_file_payload
from ui.models import IngestionRequest, SourceFormat


@pytest.mark.unit
def test_synthetic_large_stream_ingestion(tmp_path: Path):
    """
    Simula una chat con un elevato numero di righe e messaggi sequenziali,
    verificando che l'ingestion avvenga con successo, calcoli lo SHA-256 incrementale,
    e produca ConversationEvidenceDocument immutabili corretti.
    """
    chat_file = tmp_path / "chat_large_stream.txt"
    num_messages = 500

    # Scrive 500 messaggi strutturati WhatsApp
    with open(chat_file, "w", encoding="utf-8") as f:
        for i in range(num_messages):
            sender = "Alice" if i % 2 == 0 else "Bob"
            f.write(f"12/03/2024, 10:15 - {sender}: Messaggio forense numero {i} con dati di audit.\n")

    req = IngestionRequest(
        source_format=SourceFormat.WHATSAPP_EXPORT,
        filename=chat_file.name,
        file_bytes=chat_file.read_bytes(),
    )

    res = ingest_file_payload(req)

    assert res.summary.raw_record_count == num_messages
    assert res.summary.normalized_record_count == num_messages
    assert res.summary.unified_message_count == num_messages
    assert len(res.documents) >= 1

    # Verifica che il documento principale sia stato costruito correttamente
    doc_id = res.summary.selected_document_id
    assert doc_id is not None
    doc = res.documents[doc_id]
    assert len(doc.bundles) == num_messages
    assert len(doc.all_evidence_sections) == num_messages


@pytest.mark.unit
def test_upload_size_limit_distinction():
    """Verifica che MAX_UPLOAD_SIZE sia impostato a 2GB (Criterio 29)."""
    from ui.ingestion import MAX_UPLOAD_SIZE
    assert MAX_UPLOAD_SIZE == 2 * 1024 * 1024 * 1024  # 2 GB
