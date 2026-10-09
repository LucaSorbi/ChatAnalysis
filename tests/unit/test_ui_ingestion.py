"""
tests/unit/test_ui_ingestion.py
-------------------------------
Test unitari per l'ingestion application bridge (ui/ingestion.py).

Verifiche obbligatorie (Fase V):
1. temp file esiste durante l'import (osservato tramite hook);
2. temp directory non esiste più dopo success;
3. temp directory non esiste più dopo eccezione;
4. uploaded filename con '..\\..\\evil.db' NON causa path traversal;
5. file vuoto rifiutato;
6. SQLite non valido rifiutato prima dell'importer se signature check attivo;
7. Unsupported format e eccezioni tipizzate.
"""
from __future__ import annotations

import os
from pathlib import Path
import pytest

from ui.ingestion import (
    IngestionError,
    InvalidUploadedFileError,
    PipelineStageError,
    UnsupportedSourceFormatError,
    derive_deterministic_document_id,
    get_importer_for_format,
    ingest_file_payload,
)
from ui.models import (
    IngestionRequest,
    IngestionStatus,
    SourceFormat,
)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "test_data"
MSGSTORE_PATH = FIXTURE_DIR / "whatsapp_export" / "msgstore.db"
WA_PATH = FIXTURE_DIR / "whatsapp_export" / "wa.db"


@pytest.mark.unit
class TestUiIngestionUnit:

    def test_importer_dispatch_explicit(self):
        """Verifica che il dispatch degli importer sia esplicito e non permetta formati non supportati."""
        assert get_importer_for_format(SourceFormat.WHATSAPP_MSGSTORE).source_name == "msgstore_db"
        assert get_importer_for_format(SourceFormat.WHATSAPP_WA).source_name == "wa_db"
        assert get_importer_for_format(SourceFormat.CELLEBRITE_CSV).source_name == "cellebrite_csv"
        assert get_importer_for_format(SourceFormat.CELLEBRITE_JSON).source_name == "cellebrite_json"
        assert get_importer_for_format(SourceFormat.CELLEBRITE_XML).source_name == "cellebrite_xml"
        assert get_importer_for_format(SourceFormat.WHATSAPP_EXPORT).source_name == "whatsapp_export"

        with pytest.raises(UnsupportedSourceFormatError):
            get_importer_for_format("UNSUPPORTED_FORMAT")

    def test_empty_file_rejected(self):
        """Verifica che un file vuoto (0 byte) venga rifiutato con InvalidUploadedFileError."""
        req = IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_JSON,
            filename="empty.json",
            file_bytes=b"",
        )
        with pytest.raises(InvalidUploadedFileError) as exc_info:
            ingest_file_payload(req)
        assert "vuoto" in str(exc_info.value).lower()

    def test_invalid_sqlite_signature_rejected(self):
        """Verifica che un file non SQLite spacciato per msgstore o wa.db venga rifiutato prima dell'importer."""
        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename="fake_msgstore.db",
            file_bytes=b"This is not a sqlite db at all!",
        )
        with pytest.raises(InvalidUploadedFileError) as exc_info:
            ingest_file_payload(req)
        assert "SQLite" in str(exc_info.value)

    def test_path_traversal_prevention(self):
        """
        Verifica che un filename con '..\\..\\evil.db' venga gestito in modo sicuro
        senza scrivere file fuori dalla directory temporanea isolata.
        """
        assert WA_PATH.exists(), f"Fixture wa.db mancante: {WA_PATH}"
        content = WA_PATH.read_bytes()

        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_WA,
            filename="../../evil.db",
            file_bytes=content,
        )
        res = ingest_file_payload(req)
        assert res.summary.original_filename == "evil.db"
        assert res.status == IngestionStatus.AUXILIARY_ONLY

        # Verifica che nessun file evil.db sia stato creato in posizioni traverse
        assert not Path("../../evil.db").exists()
        assert not Path("evil.db").exists()

    def test_temp_file_exists_during_import_and_deleted_after_success(self, monkeypatch):
        """
        Verifica che il file temporaneo esista su disco durante l'import
        e che la directory temporanea venga completamente rimossa al termine (success).
        """
        assert WA_PATH.exists()
        content = WA_PATH.read_bytes()

        temp_paths_observed: list[Path] = []
        temp_dir_observed: list[Path] = []

        from importer.whatsapp_wa import WhatsAppWaDbImporter
        orig_import = WhatsAppWaDbImporter.import_records

        def spy_import_records(self, file_path: Path):
            p = Path(file_path)
            temp_paths_observed.append(p)
            temp_dir_observed.append(p.parent)
            assert p.exists(), "Il file temporaneo deve esistere durante l'esecuzione dell'importer!"
            assert p.parent.exists(), "La cartella temporanea deve esistere durante l'esecuzione!"
            assert p.name == "primary_evidence.bin"
            return orig_import(self, file_path)

        monkeypatch.setattr(WhatsAppWaDbImporter, "import_records", spy_import_records)

        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_WA,
            filename="wa.db",
            file_bytes=content,
        )
        res = ingest_file_payload(req)
        assert res.status == IngestionStatus.AUXILIARY_ONLY

        # Verifiche post-success
        assert len(temp_paths_observed) == 1
        observed_file = temp_paths_observed[0]
        observed_dir = temp_dir_observed[0]

        assert not observed_file.exists(), "Il file temporaneo deve essere eliminato dopo il successo!"
        assert not observed_dir.exists(), "La directory temporanea deve essere eliminata dopo il successo!"

    def test_temp_directory_deleted_after_exception(self, monkeypatch):
        """
        Verifica che la directory temporanea venga eliminata anche in caso di eccezione
        durante l'esecuzione dell'importer o degli stage successivi.
        """
        assert WA_PATH.exists()
        content = WA_PATH.read_bytes()

        temp_dir_observed: list[Path] = []

        from importer.whatsapp_wa import WhatsAppWaDbImporter

        def failing_import_records(self, file_path: Path):
            p = Path(file_path)
            temp_dir_observed.append(p.parent)
            assert p.exists()
            raise RuntimeError("Simulazione crash imprevisto durante import_records")

        monkeypatch.setattr(WhatsAppWaDbImporter, "import_records", failing_import_records)

        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_WA,
            filename="wa.db",
            file_bytes=content,
        )

        with pytest.raises(PipelineStageError) as exc_info:
            ingest_file_payload(req)
        assert exc_info.value.stage == "importer"
        assert "Simulazione crash" not in exc_info.value.safe_message
        assert "non è compatibile" in exc_info.value.safe_message

        assert len(temp_dir_observed) == 1
        observed_dir = temp_dir_observed[0]
        assert not observed_dir.exists(), "La directory temporanea deve essere eliminata anche in caso di eccezione!"

    def test_error_hierarchy_attributes(self):
        """Verifica che tutte le eccezioni di ingestion espongano safe_message e stage."""
        e_base = IngestionError("Errore generico")
        assert e_base.safe_message == "Errore generico"
        assert e_base.stage is None

        e_upload = InvalidUploadedFileError("File non valido")
        assert e_upload.safe_message == "File non valido"
        assert e_upload.stage == "preflight"

        e_format = UnsupportedSourceFormatError("Formato sconosciuto")
        assert e_format.safe_message == "Formato sconosciuto"
        assert e_format.stage == "dispatch"

        e_stage = PipelineStageError("validator", "Validazione fallita")
        assert e_stage.safe_message == "Validazione fallita"
        assert e_stage.stage == "validator"

    def test_companion_wa_db_failure_adds_warning_and_preserves_msgstore(self):
        """
        Verifica che un companion wa.db non compatibile:
        1. Non blocchi l'import principale di msgstore;
        2. Mantenga status SUCCESS;
        3. Aggiunga un warning esplicito e non sensibile;
        4. Non incrementi companion_aux_count (118 anziché 122);
        5. Non esponga dettagli di traceback o percorsi interni.
        """
        assert MSGSTORE_PATH.exists()
        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_PATH.name,
            file_bytes=MSGSTORE_PATH.read_bytes(),
            companion_filename="corrupted_wa.db",
            companion_bytes=b"corrupted_non_sqlite_bytes_for_testing",
        )

        res = ingest_file_payload(req)
        assert res.status == IngestionStatus.SUCCESS
        assert res.summary.unified_message_count == 504
        assert res.summary.auxiliary_record_count == 118  # non arricchito
        assert len(res.summary.warnings) == 1
        warning = res.summary.warnings[0]
        assert "wa.db non è stato utilizzato perché non è risultato compatibile o leggibile" in warning
        assert "corrupted_non_sqlite_bytes" not in warning
        assert "primary_evidence.bin" not in warning

    def test_conversation_display_labels_pseudonymized(self):
        """
        Verifica che le etichette delle conversazioni siano rigorosamente pseudonimizzate
        e non espongano JID, numeri telefonici o titoli raw.
        """
        assert MSGSTORE_PATH.exists()
        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_PATH.name,
            file_bytes=MSGSTORE_PATH.read_bytes(),
        )

        res = ingest_file_payload(req)
        assert len(res.summary.conversations) == 3

        for idx, c in enumerate(res.summary.conversations, start=1):
            assert hasattr(c, "bundle_count")
            assert not hasattr(c, "message_count"), "ImportedConversationInfo non deve esporre message_count"
            assert c.bundle_count > 0

            # Verifica assenza JID, titoli chat o dati personali
            assert "@s.whatsapp.net" not in c.display_label
            assert "@g.us" not in c.display_label
            assert "+39" not in c.display_label
            assert "Gruppo_Sintetico" not in c.display_label
            assert c.display_label.startswith(f"Conversazione {idx} — ")
            assert "messaggi — [" in c.display_label

        # Le etichette e gli short ID devono essere distinti tra conversazioni diverse
        labels = [c.display_label for c in res.summary.conversations]
        assert len(set(labels)) == len(labels)
        short_ids = [c.display_label.split("[")[-1].rstrip("]") for c in res.summary.conversations]
        assert len(set(short_ids)) == len(short_ids)

    def test_derive_deterministic_document_id(self):
        """Verifica la costruzione deterministica e non sensibile del document_id."""
        doc_id1 = derive_deterministic_document_id("whatsapp_msgstore", "abcdef1234567890", "chat_42")
        doc_id2 = derive_deterministic_document_id("whatsapp_msgstore", "abcdef1234567890", "chat_42")
        doc_id3 = derive_deterministic_document_id("whatsapp_msgstore", "abcdef1234567890", "chat_99")

        assert doc_id1 == doc_id2, "document_id deve essere deterministico e stabile"
        assert doc_id1 != doc_id3, "chat differenti devono avere document_id differenti"
        assert "chat_42" not in doc_id1, "document_id non deve contenere identificatori raw non sottoposti ad hash"
        assert doc_id1.startswith("doc::whatsapp_msgstore::abcdef123456::")

        # UNRESOLVED bucket
        doc_unres = derive_deterministic_document_id("cellebrite_xml", "1122334455667788", "UNRESOLVED")
        assert "unresolved" in doc_unres

