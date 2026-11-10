"""
tests/unit/test_media_registry_session_isolation.py
----------------------------------------------------
Test di regressione e audit forense per BLOCKER 1:
- Session A registra un file, Session B non lo vede.
- Session A e B importano documenti con document_id identico: nessuna contaminazione.
- Reset A cancella soltanto risorse di A.
- Reset B non modifica le risorse di A.
- File temporanei realmente eliminati dopo cleanup.
- Nessun accesso tramite registri globali o class methods (_fallback_default e _HybridRegistryMethod rimossi).
- Multi-conversation A -> B -> A nella stessa sessione continua a funzionare.
- L'ingestion ZIP conserva l'accesso autorizzato ai media dopo il cleanup dello staging.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import zipfile
import pytest

from multimodal.asset_access import (
    MediaAssetRegistry,
    MediaAssetAccessService,
    MediaSourceUnavailableError,
)
from ui import state
from ui.ingestion import ingest_file_payload
from ui.models import IngestionRequest, SourceFormat


def test_session_a_registers_file_session_b_cannot_see_it(tmp_path: Path):
    """Session A registra un file, Session B non lo vede."""
    file_a = tmp_path / "chat_a.zip"
    file_a.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    reg_a = MediaAssetRegistry(document_id="doc_123")
    reg_b = MediaAssetRegistry(document_id="doc_123")

    reg_a.register_archive("doc_123", file_a)

    assert reg_a.get_archive("doc_123") == file_a
    assert reg_b.get_archive("doc_123") is None


def test_identical_document_ids_in_separate_sessions_no_contamination(tmp_path: Path):
    """Session A e B importano documenti con document_id identico: nessuna contaminazione."""
    file_a = tmp_path / "archive_session_a.zip"
    file_b = tmp_path / "archive_session_b.zip"
    file_a.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    file_b.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    session_state_a: dict = {}
    session_state_b: dict = {}

    state.init_session_state(session_state_a)
    state.init_session_state(session_state_b)

    reg_a = state.get_media_asset_registry("shared_doc_id", state=session_state_a)
    reg_b = state.get_media_asset_registry("shared_doc_id", state=session_state_b)

    reg_a.register_archive("shared_doc_id", file_a)
    reg_b.register_archive("shared_doc_id", file_b)

    assert reg_a.get_archive("shared_doc_id") == file_a
    assert reg_b.get_archive("shared_doc_id") == file_b
    assert reg_a.get_archive("shared_doc_id") != reg_b.get_archive("shared_doc_id")


def test_reset_a_only_clears_a_resources(tmp_path: Path):
    """Reset A cancella soltanto le risorse di A; Reset B non modifica le risorse di A."""
    file_a = tmp_path / "file_a.zip"
    file_b = tmp_path / "file_b.zip"
    file_a.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    file_b.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    session_a: dict = {}
    session_b: dict = {}
    state.init_session_state(session_a)
    state.init_session_state(session_b)

    reg_a = state.get_media_asset_registry("doc_a", state=session_a)
    reg_b = state.get_media_asset_registry("doc_b", state=session_b)

    reg_a.register_archive("doc_a", file_a)
    reg_b.register_archive("doc_b", file_b)

    # Reset A
    state.reset_dataset(state=session_a)

    assert reg_a.get_archive("doc_a") is None
    # Risorse di B rimangono intatte
    assert reg_b.get_archive("doc_b") == file_b

    # Reset B
    state.reset_dataset(state=session_b)
    assert reg_b.get_archive("doc_b") is None


def test_temp_files_actually_deleted_after_cleanup():
    """File temporanei e cartelle realmente eliminati dopo cleanup del registry."""
    reg = MediaAssetRegistry(document_id="doc_cleanup")
    managed_dir = reg.managed_temp_dir
    assert managed_dir.exists()

    # Crea file gestito nel registry
    temp_file = managed_dir / "test_artifact.bin"
    temp_file.write_bytes(b"temporary forensic payload")
    assert temp_file.exists()

    # Esegui cleanup
    reg.clear()

    # Verifica rimozione fisica
    assert not temp_file.exists()
    assert not managed_dir.exists()


def test_no_global_registry_or_class_methods():
    """Nessun accesso consentito tramite registri globali o class methods su MediaAssetRegistry."""
    # Verifica che _fallback_default non esista
    assert not hasattr(MediaAssetRegistry, "_fallback_default")
    assert not hasattr(MediaAssetRegistry, "_HybridRegistryMethod")

    # Invocazione di metodi come class method deve fallire o richiedere istanza
    with pytest.raises(TypeError):
        MediaAssetRegistry.register_archive("doc_x", Path("some/path"))  # type: ignore

    with pytest.raises(TypeError):
        MediaAssetRegistry.get_archive("doc_x")  # type: ignore


def test_multi_conversation_a_to_b_to_a_in_same_session(tmp_path: Path):
    """Multi-conversation A -> B -> A nella stessa sessione continua a funzionare con isolamento."""
    session: dict = {}
    state.init_session_state(session)

    file_a = tmp_path / "chat_alpha.zip"
    file_b = tmp_path / "chat_beta.zip"
    file_a.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    file_b.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    reg_a = state.get_media_asset_registry("chat_alpha", state=session)
    reg_b = state.get_media_asset_registry("chat_beta", state=session)

    reg_a.register_archive("chat_alpha", file_a)
    reg_b.register_archive("chat_beta", file_b)

    # Selezione Alpha -> Beta -> Alpha
    assert state.get_media_asset_registry("chat_alpha", state=session).get_archive("chat_alpha") == file_a
    assert state.get_media_asset_registry("chat_beta", state=session).get_archive("chat_beta") == file_b
    assert state.get_media_asset_registry("chat_alpha", state=session).get_archive("chat_alpha") == file_a


def test_zip_ingestion_preserves_authorized_media_access_after_staging_cleanup(tmp_path: Path):
    """L'ingestion ZIP conserva l'accesso autorizzato ai media dopo il cleanup dello staging."""
    zip_bytes_io = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    zip_path = Path(zip_bytes_io.name)
    zip_bytes_io.close()

    try:
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("_chat.txt", "10/05/2024, 10:00 - Alice: Guarda questa foto: photo.jpg\n".encode("utf-8"))
            zf.writestr("photo.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01_test_jpg")

        payload = zip_path.read_bytes()
    finally:
        if zip_path.exists():
            zip_path.unlink()

    session_reg = MediaAssetRegistry(document_id="session_reg_test")
    res = ingest_file_payload(
        IngestionRequest(
            source_format=SourceFormat.WHATSAPP_EXPORT,
            filename="chat_export.zip",
            file_bytes=payload,
        ),
        media_registry=session_reg,
    )

    doc = next(iter(res.documents.values()))
    archive_path = session_reg.get_archive(doc.document_id)
    assert archive_path is not None
    assert archive_path.exists()

    # Il service media con il registry esplicito accede al membro
    service = MediaAssetAccessService(archive_path=archive_path, registry=session_reg)
    with service.materialize_member("photo.jpg") as photo_path:
        assert photo_path.exists()
        assert b"JFIF" in photo_path.read_bytes()

    # Pulizia
    session_reg.clear()
    assert not archive_path.exists()


# ==============================================================================
# BLOCKER 2 — TEST OBBLIGATORI (DATASET REPLACEMENT TRANSACTIONAL LIFECYCLE)
# ==============================================================================

def test_dataset_replacement_transactional_lifecycle_and_cleanup(tmp_path: Path):
    """
    Verifica il ciclo di vita transazionale della sostituzione del dataset:
    - Import dataset A con media temporanei.
    - Fallimento import B: comportamento precedente conservato coerentemente (A resta intatto).
    - Import dataset B nella stessa sessione: B diventa attivo, media di A eliminati, media di B conservati.
    - Reset ripetuto senza eccezioni.
    """
    from ui.models import IngestionResult, IngestionSummary, IngestionStatus, DatasetMode
    from ai.models import ConversationEvidenceDocument, MessageEvidenceBundle

    session: dict = {}
    state.init_session_state(session)

    # 1. Dataset A con asset temporaneo gestito
    doc_a = ConversationEvidenceDocument(document_id="doc_a", bundles=(), source_name="src_a")
    reg_a = MediaAssetRegistry(document_id="doc_a")
    temp_file_a = reg_a.register_bytes("source_a", b"PAYLOAD_A", filename="archive_a.zip")
    assert temp_file_a.exists()

    summary_a = IngestionSummary(
        source_format=SourceFormat.WHATSAPP_EXPORT,
        original_filename="a.zip",
        sha256="hash_a",
        file_size_bytes=100,
        raw_record_count=1,
        validation_issue_count=0,
        normalized_record_count=1,
        unified_message_count=1,
        conversation_count=1,
        auxiliary_record_count=0,
    )
    res_a = IngestionResult(
        summary=summary_a,
        documents={"doc_a": doc_a},
        media_registry=reg_a,
    )

    state.set_real_ingestion_result(res_a, state=session)

    assert state.get_selected_document_id(state=session) == "doc_a"
    assert state.get_media_asset_registry("doc_a", state=session) is reg_a
    assert temp_file_a.exists()

    # 2. Fallimento import B: fallimento transazionale, dataset A rimane intatto
    summary_b_fail = IngestionSummary(
        source_format=SourceFormat.WHATSAPP_EXPORT,
        original_filename="b_corrupt.zip",
        sha256="hash_b_fail",
        file_size_bytes=50,
        raw_record_count=0,
        validation_issue_count=1,
        normalized_record_count=0,
        unified_message_count=0,
        conversation_count=0,
        auxiliary_record_count=0,
        status=IngestionStatus.FAILED,
    )
    res_b_fail = IngestionResult(
        summary=summary_b_fail,
        documents={},
        error_stage="parsing",
        error_message="Corrupted archive structure",
    )

    state.set_real_ingestion_result(res_b_fail, state=session)

    # Dataset A e le sue risorse devono essere ANCORA attive e integre
    assert state.get_selected_document_id(state=session) == "doc_a"
    assert state.get_media_asset_registry("doc_a", state=session) is reg_a
    assert temp_file_a.exists()
    assert state.get_error_message(state=session) == "Corrupted archive structure"

    # 3. Import dataset B con successo: sostituzione transazionale
    doc_b = ConversationEvidenceDocument(document_id="doc_b", bundles=(), source_name="src_b")
    reg_b = MediaAssetRegistry(document_id="doc_b")
    temp_file_b = reg_b.register_bytes("source_b", b"PAYLOAD_B", filename="archive_b.zip")
    assert temp_file_b.exists()

    summary_b = IngestionSummary(
        source_format=SourceFormat.WHATSAPP_EXPORT,
        original_filename="b.zip",
        sha256="hash_b",
        file_size_bytes=200,
        raw_record_count=2,
        validation_issue_count=0,
        normalized_record_count=2,
        unified_message_count=2,
        conversation_count=1,
        auxiliary_record_count=0,
    )
    res_b = IngestionResult(
        summary=summary_b,
        documents={"doc_b": doc_b},
        media_registry=reg_b,
    )

    state.set_real_ingestion_result(res_b, state=session)

    # Dataset B deve essere attivo
    assert state.get_selected_document_id(state=session) == "doc_b"
    assert state.get_media_asset_registry("doc_b", state=session) is reg_b
    assert temp_file_b.exists()

    # Le risorse di A devono essere state eliminate fisicamente
    assert not temp_file_a.exists()
    # Il registry A non deve più essere raggiungibile dallo stato di sessione
    session_regs = session.get(state.KEY_MEDIA_REGISTRIES_BY_DOCUMENT, {})
    assert "doc_a" not in session_regs
    assert "doc_b" in session_regs
    assert reg_a.get_source("source_a") is None

    # 4. Multi-conversation nello stesso dataset: A -> B -> A conserva le risorse
    doc_multi_1 = ConversationEvidenceDocument(document_id="conv_1", bundles=(), source_name="src_m")
    doc_multi_2 = ConversationEvidenceDocument(document_id="conv_2", bundles=(), source_name="src_m")
    reg_m = MediaAssetRegistry(document_id="conv_shared")
    temp_m = reg_m.register_bytes("source_m", b"PAYLOAD_MULTI", filename="archive_m.zip")

    summary_m = IngestionSummary(
        source_format=SourceFormat.WHATSAPP_EXPORT,
        original_filename="multi.zip",
        sha256="hash_m",
        file_size_bytes=300,
        raw_record_count=4,
        validation_issue_count=0,
        normalized_record_count=4,
        unified_message_count=4,
        conversation_count=2,
        auxiliary_record_count=0,
    )
    res_m = IngestionResult(
        summary=summary_m,
        documents={"conv_1": doc_multi_1, "conv_2": doc_multi_2},
        media_registry=reg_m,
    )

    state.set_real_ingestion_result(res_m, state=session)
    assert not temp_file_b.exists()  # Il vecchio dataset B è stato pulito
    assert temp_m.exists()

    # Passa a conv_2 e poi torna a conv_1
    state.select_conversation("conv_2", state=session)
    assert state.get_selected_document_id(state=session) == "conv_2"
    assert temp_m.exists()

    state.select_conversation("conv_1", state=session)
    assert state.get_selected_document_id(state=session) == "conv_1"
    assert temp_m.exists()

    # 5. Reset ripetuto senza eccezioni
    state.reset_dataset(state=session)
    assert not temp_m.exists()
    assert state.get_selected_document_id(state=session) is None

    # Secondo reset idempotente
    state.reset_dataset(state=session)
    assert state.get_selected_document_id(state=session) is None

