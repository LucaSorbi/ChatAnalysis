"""
ui/ingestion.py
---------------
Bridge applicativo tra l'upload dei file utente e la pipeline forense approvata.

Pipeline integrata:
UPLOAD LOCALE (tempfile sicuro)
  → HASHING SHA-256
  → IMPORTER REALE (dispatch esplicito per SourceFormat)
  → RawRecord
  → VALIDAZIONE (RecordValidator)
  → NORMALIZZAZIONE (RecordNormalizer)
  → ENTITY RESOLUTION (DeterministicEntityResolver)
  → UNIFIED MESSAGES (UnifiedModelBuilder)
  → EVIDENCE BUNDLES (MessageEvidenceBundle)
  → CONVERSATION DOCUMENTS (ConversationEvidenceDocument)
  → SEARCH SERVICE (SearchService deterministico per conversazione)

Principi di sicurezza e riservatezza:
- CLEANUP RIGOROSO: File temporanei isolati in tempfile.TemporaryDirectory()
  con eliminazione garantita in blocco try/finally.
- ZERO DATA LEAKAGE: Nessun dump di testi chat, query o percorsi di sistema nei log.
- ZERO AI INVOCATION: Nessun client LLM istanziato per file reali; Topic results vuoti.
- ZERO AUTO-DETECTION: Scelta del formato autoritativa dall'utente.
- ZERO PATH TRAVERSAL: Nomi di file controllati dall'applicazione.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any, BinaryIO, Iterable, Mapping, Optional, Sequence

from ai.models import ConversationEvidenceDocument
from entity_resolution.resolver import DeterministicEntityResolver
from importer.base import BaseImporter
from importer.cellebrite_csv import CellebriteCsvImporter
from importer.cellebrite_json import CellebriteJsonImporter
from importer.cellebrite_xml import CellebriteXmlImporter
from importer.whatsapp_export import WhatsAppExportImporter
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter
from importer.whatsapp_wa import WhatsAppWaDbImporter
from multimodal.evidence import MessageEvidenceBundle
from normalization.normalizer import RecordNormalizer
from unified.builder import UnifiedModelBuilder
from unified.context import UnifiedBuildContext
from unified.models import UnifiedMessage
from validation.validator import RecordValidator

from ui.models import (
    ImportedConversationInfo,
    IngestionRequest,
    IngestionResult,
    IngestionStatus,
    IngestionSummary,
    SourceFormat,
)

SQLITE_HEADER = b"SQLite format 3\x00"


class IngestionError(Exception):
    """Eccezione base per anomalie nel layer di ingestion con messaggio sicuro per la UI."""
    def __init__(self, safe_message: str, stage: Optional[str] = None) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.stage = stage


class InvalidUploadedFileError(IngestionError):
    """File caricato non valido, vuoto o non conforme alla signature attesa."""
    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message, stage="preflight")


class UnsupportedSourceFormatError(IngestionError):
    """Formato sorgente non supportato o non riconosciuto."""
    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message, stage="dispatch")


class PipelineStageError(IngestionError):
    """Errore durante l'esecuzione di uno stage della pipeline forense."""
    def __init__(self, stage: str, safe_message: str) -> None:
        super().__init__(safe_message, stage=stage)


def get_importer_for_format(source_format: SourceFormat | str) -> BaseImporter:
    """Restituisce l'importer reale associato al formato sorgente selezionato."""
    fmt_val = source_format.value if isinstance(source_format, SourceFormat) else str(source_format)

    if fmt_val == SourceFormat.WHATSAPP_MSGSTORE.value:
        return WhatsAppMsgstoreImporter()
    elif fmt_val == SourceFormat.WHATSAPP_WA.value:
        return WhatsAppWaDbImporter()
    elif fmt_val == SourceFormat.CELLEBRITE_CSV.value:
        return CellebriteCsvImporter()
    elif fmt_val == SourceFormat.CELLEBRITE_JSON.value:
        return CellebriteJsonImporter()
    elif fmt_val == SourceFormat.CELLEBRITE_XML.value:
        return CellebriteXmlImporter()
    elif fmt_val == SourceFormat.WHATSAPP_EXPORT.value:
        return WhatsAppExportImporter()
    else:
        raise UnsupportedSourceFormatError(f"Formato sorgente non supportato: {fmt_val}")


def _compute_sha256_and_write(
    source_bytes_or_io: bytes | BinaryIO,
    dest_path: Path,
) -> tuple[str, int]:
    """Scrive i byte nella destinazione sicura calcolando incrementale SHA-256 e dimensione."""
    hasher = hashlib.sha256()
    total_bytes = 0

    with open(dest_path, "wb") as f_out:
        if isinstance(source_bytes_or_io, (bytes, bytearray)):
            f_out.write(source_bytes_or_io)
            hasher.update(source_bytes_or_io)
            total_bytes = len(source_bytes_or_io)
        else:
            source_bytes_or_io.seek(0)
            while chunk := source_bytes_or_io.read(65536):
                f_out.write(chunk)
                hasher.update(chunk)
                total_bytes += len(chunk)

    return hasher.hexdigest(), total_bytes


def _validate_sqlite_signature(path: Path) -> None:
    """Verifica non distruttiva dei primi 16 byte per la signature SQLite."""
    with open(path, "rb") as f:
        header = f.read(16)
        if header != SQLITE_HEADER:
            raise InvalidUploadedFileError("Il file fornito non possiede una firma valida di database SQLite 3.")


def _validate_whatsapp_export_preflight(path: Path) -> None:
    """Verifica preliminare che il file sia un archivio ZIP o un file testuale valido."""
    import zipfile
    if zipfile.is_zipfile(path):
        try:
            with zipfile.ZipFile(path, "r") as zf:
                from importer.whatsapp_export import _validate_zip_archive
                _validate_zip_archive(zf)
                txt_members = [
                    m for m in zf.infolist()
                    if m.filename.lower().endswith(".txt") and not m.is_dir()
                ]
                if not txt_members:
                    raise InvalidUploadedFileError("L'archivio ZIP non contiene alcun file transcript '.txt'.")
        except ValueError as v_err:
            raise InvalidUploadedFileError(f"Archivio ZIP non conforme ai requisiti di sicurezza: {v_err}") from v_err
        except zipfile.BadZipFile as bz_err:
            raise InvalidUploadedFileError("Archivio ZIP corrotto o non leggibile.") from bz_err
    else:
        try:
            with open(path, "rb") as f:
                sample = f.read(4096)
            if not sample:
                raise InvalidUploadedFileError("Il file di transcript è vuoto (0 byte).")
            if b"\x00" in sample:
                raise InvalidUploadedFileError(
                    "Il file selezionato è un formato binario non riconosciuto per l'export WhatsApp (atteso TXT o ZIP)."
                )
        except OSError as e:
            raise InvalidUploadedFileError(f"Impossibile leggere il file caricato: {e}") from e


def derive_deterministic_document_id(
    source_name: str,
    file_sha256: str,
    chat_key: str,
) -> str:
    """
    Costruisce un document_id deterministico, stabile e privo di dati personali.
    Policy: doc::{source_name}::{sha256[:12]}::{chat_hash}
    """
    if chat_key == "UNRESOLVED":
        chat_hash = "unresolved"
    else:
        chat_hash = hashlib.sha256(chat_key.encode("utf-8")).hexdigest()[:12]
    return f"doc::{source_name}::{file_sha256[:12]}::{chat_hash}"


def ingest_file_payload(
    file_bytes_or_io: bytes | BinaryIO | IngestionRequest,
    original_filename: str | None = None,
    source_format: SourceFormat | str | None = None,
    companion_bytes_or_io: bytes | BinaryIO | None = None,
    companion_filename: str | None = None,
) -> IngestionResult:
    """
    Esegue l'intero ciclo di ingestion su un upload reale in un'area temporanea isolata.
    Accetta o un oggetto IngestionRequest o i singoli parametri.
    """
    if isinstance(file_bytes_or_io, IngestionRequest):
        req = file_bytes_or_io
        actual_bytes = req.file_bytes
        actual_filename = req.filename
        actual_format = req.source_format
        actual_comp_bytes = req.companion_bytes
        actual_comp_filename = req.companion_filename
    else:
        actual_bytes = file_bytes_or_io
        actual_filename = original_filename or "upload.bin"
        if source_format is None:
            raise UnsupportedSourceFormatError("source_format non specificato.")
        actual_format = source_format
        actual_comp_bytes = companion_bytes_or_io
        actual_comp_filename = companion_filename

    enum_format = SourceFormat(actual_format) if not isinstance(actual_format, SourceFormat) else actual_format
    importer = get_importer_for_format(enum_format)

    # Context manager per directory temporanea: pulizia automatica garantita al 100%
    with tempfile.TemporaryDirectory(prefix="forensic_ingest_") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        safe_primary_path = temp_dir / "primary_evidence.bin"

        # 1. Scrittura e calcolo hash primario
        file_sha256, file_size = _compute_sha256_and_write(actual_bytes, safe_primary_path)

        if file_size == 0:
            raise InvalidUploadedFileError("Il file caricato è vuoto (0 byte).")

        # Verifica preliminare signature per database SQLite o export WhatsApp
        if enum_format in (SourceFormat.WHATSAPP_MSGSTORE, SourceFormat.WHATSAPP_WA):
            _validate_sqlite_signature(safe_primary_path)
        elif enum_format == SourceFormat.WHATSAPP_EXPORT:
            _validate_whatsapp_export_preflight(safe_primary_path)

        # 2. Gestione eventuale file companion wa.db
        companion_sha256: str | None = None
        safe_companion_path: Path | None = None
        companion_init_failed = False
        if actual_comp_bytes is not None:
            safe_companion_path = temp_dir / "companion_wa.db"
            companion_sha256, companion_size = _compute_sha256_and_write(actual_comp_bytes, safe_companion_path)
            if companion_size > 0:
                try:
                    _validate_sqlite_signature(safe_companion_path)
                except Exception:
                    companion_init_failed = True
                    safe_companion_path = None
            else:
                safe_companion_path = None
                companion_sha256 = None

        # 3. Importazione Record
        validator = RecordValidator()
        normalizer = RecordNormalizer()
        resolver = DeterministicEntityResolver()

        try:
            raw_records = list(importer.import_records(safe_primary_path))
        except Exception as exc:
            raise PipelineStageError(
                stage="importer",
                safe_message="Il file non è compatibile con il formato selezionato o presenta una struttura corrotta.",
            ) from exc

        val_results = list(validator.validate_stream(raw_records))
        norm_records = list(normalizer.normalize_stream(val_results))

        total_validation_issues = sum(len(v.issues) for v in val_results)

        # Caso wa.db Standalone: sola gestione dati contatto
        if enum_format == SourceFormat.WHATSAPP_WA:
            summary = IngestionSummary(
                source_format=enum_format,
                original_filename=Path(actual_filename).name,
                sha256=file_sha256,
                file_size_bytes=file_size,
                raw_record_count=len(raw_records),
                validation_issue_count=total_validation_issues,
                normalized_record_count=len(norm_records),
                unified_message_count=0,
                conversation_count=0,
                auxiliary_record_count=len(raw_records),
                warnings=("wa.db contiene dati contatto/identità e non costituisce da solo una conversazione analizzabile.",),
                available_conversations=(),
                selected_document_id=None,
                companion_filename=None,
                companion_sha256=None,
                status=IngestionStatus.AUXILIARY_ONLY,
            )
            return IngestionResult(summary=summary, documents={})

        # Integrazione companion wa.db se fornito con msgstore
        companion_aux_count = 0
        all_norm_records = list(norm_records)
        ingestion_warnings: list[str] = []

        if companion_init_failed:
            ingestion_warnings.append(
                "Il companion wa.db non è stato utilizzato perché non è risultato compatibile o leggibile. "
                "L'analisi di msgstore è proseguita senza arricchimento contatti."
            )
        elif safe_companion_path is not None and enum_format == SourceFormat.WHATSAPP_MSGSTORE:
            wa_importer = WhatsAppWaDbImporter()
            try:
                raw_companion = list(wa_importer.import_records(safe_companion_path))
                val_companion = list(validator.validate_stream(raw_companion))
                norm_companion = list(normalizer.normalize_stream(val_companion))
                companion_aux_count = len(raw_companion)
                total_validation_issues += sum(len(v.issues) for v in val_companion)
                all_norm_records.extend(norm_companion)
            except Exception:
                companion_aux_count = 0
                ingestion_warnings.append(
                    "Il companion wa.db non è stato utilizzato perché non è risultato compatibile o leggibile. "
                    "L'analisi di msgstore è proseguita senza arricchimento contatti."
                )

        # 4. Entity Resolution & Unified Messages
        try:
            resolution = resolver.resolve(all_norm_records)
            build_ctx = UnifiedBuildContext.from_records(all_norm_records, resolution=resolution)
            builder = UnifiedModelBuilder(context=build_ctx)
            unified_messages = builder.build_all(all_norm_records)
        except Exception as exc:
            raise PipelineStageError(
                stage="unified_building",
                safe_message="Errore durante la correlazione e costruzione dei messaggi unificati.",
            ) from exc

        # 5. Raggruppamento per conversazione
        chat_groups: dict[str, list[UnifiedMessage]] = {}
        for msg in unified_messages:
            cid = msg.chat.chat_id if msg.chat and msg.chat.chat_id else None
            key = cid if cid is not None else "UNRESOLVED"
            if key not in chat_groups:
                chat_groups[key] = []
            chat_groups[key].append(msg)

        # Ordinamento deterministico delle conversazioni (UNRESOLVED in coda se presente)
        standard_keys = sorted([k for k in chat_groups.keys() if k != "UNRESOLVED"])
        ordered_keys = list(standard_keys)
        if "UNRESOLVED" in chat_groups:
            ordered_keys.append("UNRESOLVED")

        # 6. Costruzione dei ConversationEvidenceDocument con label pseudonimizzate
        documents: dict[str, ConversationEvidenceDocument] = {}
        conversations_info: list[ImportedConversationInfo] = []

        regular_chat_idx = 1
        for chat_key in ordered_keys:
            group_msgs = chat_groups[chat_key]
            bundles = [MessageEvidenceBundle(message=m) for m in group_msgs]
            doc_id = derive_deterministic_document_id(
                source_name=importer.source_name,
                file_sha256=file_sha256,
                chat_key=chat_key,
            )

            doc = ConversationEvidenceDocument(
                document_id=doc_id,
                bundles=tuple(bundles),
                chat_id=chat_key if chat_key != "UNRESOLVED" else None,
                source_name=importer.source_name,
                metadata={
                    "source_format": enum_format.value,
                    "original_filename": Path(actual_filename).name,
                    "sha256": file_sha256,
                    "is_unresolved": (chat_key == "UNRESOLVED"),
                },
            )
            documents[doc_id] = doc

            # Etichetta pseudonimizzata non sensibile: nessuna chat_id raw, titolo, JID o telefono
            short_id = doc_id.rsplit("::", 1)[-1][:8]
            if chat_key == "UNRESOLVED":
                label = f"Messaggi non associati — {len(bundles)} messaggi — [{short_id}]"
            else:
                label = f"Conversazione {regular_chat_idx} — {len(bundles)} messaggi — [{short_id}]"
                regular_chat_idx += 1

            languages = tuple(sorted({s.language for s in doc.all_evidence_sections if s.language}))

            conversations_info.append(
                ImportedConversationInfo(
                    document_id=doc_id,
                    chat_id=chat_key if chat_key != "UNRESOLVED" else None,
                    display_label=label,
                    bundle_count=len(bundles),
                    section_count=len(doc.all_evidence_sections),
                    languages=languages,
                    source_name=importer.source_name,
                )
            )

        auxiliary_count = companion_aux_count + sum(1 for r in raw_records if r.record_type != "message")
        selected_id = conversations_info[0].document_id if conversations_info else None

        summary = IngestionSummary(
            source_format=enum_format,
            original_filename=Path(actual_filename).name,
            sha256=file_sha256,
            file_size_bytes=file_size,
            raw_record_count=len(raw_records),
            validation_issue_count=total_validation_issues,
            normalized_record_count=len(norm_records),
            unified_message_count=len(unified_messages),
            conversation_count=len(documents),
            auxiliary_record_count=auxiliary_count,
            warnings=tuple(ingestion_warnings),
            available_conversations=tuple(conversations_info),
            selected_document_id=selected_id,
            companion_filename=Path(actual_comp_filename).name if actual_comp_filename else None,
            companion_sha256=companion_sha256,
            status=IngestionStatus.SUCCESS,
        )

        return IngestionResult(summary=summary, documents=documents)
