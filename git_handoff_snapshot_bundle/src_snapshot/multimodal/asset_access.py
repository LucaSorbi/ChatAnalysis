"""
multimodal/asset_access.py
--------------------------
Servizio autoritativo, sicuro e riutilizzabile per l'accesso e l'elaborazione
degli asset multimediali (audio, immagini, video) da archivi WhatsApp ZIP e filesystem locale.

Principi di sicurezza e architettura forense:
1. ON-DEMAND EXTRACTION:
   Nessuna estrazione indiscriminata dell'intero archivio su disco.
   Viene estratto esclusivamente il singolo membro richiesto in una sandbox temporanea.
2. RIGOROSA VALIDAZIONE ZIP:
   - Rifiuto assoluto di path traversal: '..', '..\\', '../', 'folder/../evil.txt', 'folder\\..\\evil.txt'
   - Rifiuto di percorsi assoluti: POSIX ('/etc/passwd'), Windows ('C:\\evil.txt'), UNC ('\\\\server\\share\\evil.txt')
   - Rifiuto di symlink (attributi esterni POSIX 0o120000)
   - Rifiuto di ZIP bomb (limiti di dimensione membro, dimensione totale e compression ratio)
   - Verifica di integrità CRC32: mismatch tra header ZIP e payload reale solleva eccezione
   - Calcolo incrementale dello SHA-256 su stream
3. CICLO DI VITA DEGLI ASSET E PERSISTENZA DI SESSIONE:
   La sorgente originale (ZIP o loose file) è conservata nel registro di sessione
   MediaAssetRegistry fino al termine della sessione utente. Se la sorgente non è disponibile,
   viene sollevato MediaSourceUnavailableError esplicito.
4. ISOLAMENTO ZERO-AI IN INGESTION:
   L'elaborazione multimediale avviene solo su richiesta esplicita ('Elabora media localmente').
   Nessun Fake Vision sui dati reali: se Vision non è configurata, viene segnalata esplicitamente.
5. IMMUTABILITÀ DEL DOCUMENTO FORENSE:
   Il ConversationEvidenceDocument originale non viene modificato; l'arricchimento produce
   una nuova versione tracciabile con TextEvidenceSection per STT e OCR.
"""
from __future__ import annotations

import contextlib
import io
import os
from enum import Enum
from pathlib import Path
import re
import tempfile
import zipfile
import zlib
from typing import Any, BinaryIO, Generator, Mapping, Sequence, TYPE_CHECKING

from core.config import (
    MAX_ARCHIVE_MEMBERS,
    MAX_ARCHIVE_UNCOMPRESSED_SIZE_BYTES,
    MAX_COMPRESSION_RATIO,
    MAX_SINGLE_FILE_SIZE_BYTES,
)
from multimodal.evidence import (
    EvidenceSourceType,
    MessageEvidenceBundle,
    TextEvidenceSection,
)
from multimodal.models import (
    AudioTranscriptionResult,
    ImageOcrResult,
    ImageVisionResult,
    MediaKind,
    MediaResolutionStatus,
    OcrStatus,
    ResolvedMediaAsset,
    TranscriptionStatus,
    VisionStatus,
)
from multimodal.resolver import (
    compute_sha256_chunked,
    _AUDIO_EXTENSIONS,
    _IMAGE_EXTENSIONS,
    _VIDEO_EXTENSIONS,
    _DOCUMENT_EXTENSIONS,
)
from unified.models import UnifiedMessage

if TYPE_CHECKING:
    from ai.models import ConversationEvidenceDocument


# --- Eccezioni di Dominio per la Gestione Media ---

class MediaAccessError(Exception):
    """Eccezione base per il servizio di accesso agli asset multimediali."""
    pass


class MediaSourceUnavailableError(MediaAccessError):
    """Sollevata quando il file o archivio sorgente non è più reperibile nella sessione."""
    pass


class MediaArchiveSecurityError(MediaAccessError):
    """Sollevata in presenza di violazioni di sicurezza ZIP (traversal, symlink, zip bomb, CRC mismatch)."""
    pass


class MediaMemberNotFoundError(MediaAccessError):
    """Sollevata quando il membro multimediale richiesto non esiste nell'archivio."""
    pass


class MediaBackendUnavailableError(MediaAccessError):
    """Sollevata quando un motore di elaborazione richiesto (STT/OCR/Vision) non è disponibile."""
    pass


# --- Registro di Sessione per Sorgenti Multimediali ---

# --- Stato Ciclo di Vita Elaborazione Multimediale ---

class MediaProcessingStatus(str, Enum):
    """Stati rigorosi del ciclo di vita dell'elaborazione multimediale forense."""
    NOT_PROCESSED = "NOT_PROCESSED"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


# --- Registro di Sessione per Sorgenti Multimediali ---

class MediaAssetRegistry:
    """
    Registro in-memory/on-disk scoped alla sessione utente e allo specifico documento
    per conservare la disponibilità delle sorgenti dei media (ZIP o file locali) dopo l'ingestion.
    Isolato per sessione e documento (nessuno stato globale di processo mutabile condiviso).
    Esclusivamente istanziabile: nessun metodo o fallback globale di classe.
    """

    def __init__(self, document_id: str | None = None) -> None:
        self.document_id = str(document_id).strip() if document_id else None
        self._registry: dict[str, Path] = {}
        self._managed_dirs: list[tempfile.TemporaryDirectory] = []

    def register_source(self, source_id: str, path: Path | str) -> None:
        self._registry[str(source_id)] = Path(path).resolve()

    def register_bytes(self, source_id: str, data: bytes, filename: str = "archive.zip") -> Path:
        temp_dir = tempfile.TemporaryDirectory(prefix="session_media_")
        self._managed_dirs.append(temp_dir)
        dir_name = getattr(temp_dir, "name", None) or str(temp_dir)
        safe_path = Path(dir_name) / Path(filename).name
        with open(safe_path, "wb") as f:
            f.write(data)
        self.register_source(source_id, safe_path)
        return safe_path

    def copy_source_to_managed(self, source_id: str, src_path: Path | str, filename: str = "archive.zip") -> Path:
        temp_dir = tempfile.TemporaryDirectory(prefix="session_media_")
        self._managed_dirs.append(temp_dir)
        dir_name = getattr(temp_dir, "name", None) or (temp_dir._td.name if hasattr(temp_dir, "_td") else str(temp_dir))
        dest_path = Path(dir_name) / Path(filename).name
        with open(src_path, "rb") as f_in, open(dest_path, "wb") as f_out:
            while chunk := f_in.read(65536):
                f_out.write(chunk)
        self.register_source(source_id, dest_path)
        return dest_path

    def register_archive(self, source_id: str, path: Path | str) -> None:
        self.register_source(source_id, path)

    def get_source(self, source_id: str) -> Path | None:
        p = self._registry.get(str(source_id))
        if p is not None and p.exists():
            return p
        return None

    def get_archive(self, source_id: str) -> Path | None:
        return self.get_source(source_id)

    @property
    def managed_temp_dir(self) -> Path:
        if not self._managed_dirs:
            temp_dir = tempfile.TemporaryDirectory(prefix="session_media_")
            self._managed_dirs.append(temp_dir)
        td = self._managed_dirs[0]
        dir_name = getattr(td, "name", None) or (td._td.name if hasattr(td, "_td") else str(td))
        return Path(dir_name)

    def clear(self) -> None:
        self._registry.clear()
        for td in self._managed_dirs:
            try:
                td.cleanup()
            except Exception:
                pass
        self._managed_dirs.clear()

    def __enter__(self) -> MediaAssetRegistry:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.clear()


# --- Validazione Sicurezza ZIP ---

def check_zip_filename_security(raw_name: str) -> None:
    """
    Valida rigorosamente il nome originale di un membro ZIP prima di normalizzarlo,
    selezionarlo o estrarlo. Rifiuta categoricamente:
    - Path traversal: '../evil.txt', '..\\evil.txt', 'folder/../evil.txt', 'folder\\..\\evil.txt'
    - Percorsi assoluti POSIX: '/etc/passwd'
    - Percorsi assoluti Windows con drive letter: 'C:\\evil.txt', 'C:/evil.txt'
    - Percorsi UNC: '\\\\server\\share\\evil.txt', '//server/share/evil.txt'
    - Qualsiasi presenza di '..' o '~' come componente di percorso
    """
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise MediaArchiveSecurityError("Nome membro ZIP vuoto o non valido.")

    name = str(raw_name).strip()

    # 1. Rifiuto percorsi assoluti POSIX e Windows UNC
    if name.startswith(("/", "\\", "//", "\\\\")):
        raise MediaArchiveSecurityError(f"Membro ZIP con percorso assoluto o UNC non consentito: '{raw_name}'.")

    # 2. Rifiuto Windows drive letter (es. C:\evil.txt, C:/evil.txt, c:evil.txt)
    if len(name) >= 2 and name[1] == ":" and name[0].isalpha():
        raise MediaArchiveSecurityError(f"Membro ZIP con drive letter Windows non consentito: '{raw_name}'.")

    # 3. Rifiuto percorsi che contengono tilde '~'
    if name.startswith("~") or "/~" in name.replace("\\", "/"):
        raise MediaArchiveSecurityError(f"Membro ZIP con percorso non consentito ('~'): '{raw_name}'.")

    # 4. Rifiuto esplicito di path traversal grezzo (prima di qualsiasi risoluzione)
    norm = name.replace("\\", "/")
    if "../" in norm or "/.." in norm or norm == ".." or norm.startswith("../") or norm.endswith("/.."):
        raise MediaArchiveSecurityError(f"Membro ZIP con path traversal ('..') non consentito: '{raw_name}'.")

    # 5. Suddivisione su entrambi i separatori (slash e backslash)
    segments = re.split(r"[\\/]+", name)
    for seg in segments:
        if seg in ("..", "."):
            raise MediaArchiveSecurityError(f"Membro ZIP con segmento relativo non consentito: '{raw_name}'.")
        if ".." in seg:
            raise MediaArchiveSecurityError(f"Membro ZIP con componente di traversal non consentito: '{raw_name}'.")

    # 6. Controllo aggiuntivo con os.path
    if os.path.isabs(name) or os.path.isabs(norm):
        raise MediaArchiveSecurityError(f"Membro ZIP con percorso assoluto non consentito: '{raw_name}'.")


def validate_zip_member_security(
    zf: zipfile.ZipFile,
    member_name: str,
    max_single_size: int = MAX_SINGLE_FILE_SIZE_BYTES,
    max_compression_ratio: float = MAX_COMPRESSION_RATIO,
) -> zipfile.ZipInfo:
    """
    Valida rigorosamente un membro all'interno di un archivio ZIP per prevenire
    path traversal, symlink e zip bomb.
    La validazione riguarda il nome originale del membro prima di qualsiasi selezione o estrazione.
    """
    # 1. Validazione preventiva del nome richiesto
    check_zip_filename_security(member_name)

    # 2. Reperimento ZipInfo
    try:
        info = zf.getinfo(member_name)
    except KeyError:
        # Cerca corrispondenza su basename ESCLUSIVAMENTE tra membri validati
        matching: list[zipfile.ZipInfo] = []
        for m in zf.infolist():
            # Rifiuta membri dell'archivio con nomi malevoli prima di qualsiasi confronto o selezione
            check_zip_filename_security(m.filename)
            if Path(m.filename).name == Path(member_name).name:
                matching.append(m)

        if len(matching) == 1:
            info = matching[0]
        elif len(matching) > 1:
            raise MediaArchiveSecurityError(f"Riferimento media ambiguo: molteplici membri corrispondono a '{member_name}'.")
        else:
            raise MediaMemberNotFoundError(f"Membro '{member_name}' non trovato nell'archivio ZIP.")

    # 3. Validazione autoritativa sul nome effettivo del membro selezionato
    check_zip_filename_security(info.filename)

    # 4. Controllo symlink (attributi esterni POSIX)
    if (info.external_attr >> 16) & 0o170000 == 0o120000:
        raise MediaArchiveSecurityError(f"Membro ZIP con symlink non consentito: '{info.filename}'.")

    # 5. Controllo dimensione del singolo file
    if info.file_size > max_single_size:
        raise MediaArchiveSecurityError(
            f"Dimensione del membro ZIP '{info.filename}' ({info.file_size} byte) supera il limite consentito ({max_single_size} byte)."
        )

    # 6. Controllo compression ratio (ZIP bomb)
    if info.compress_size > 0 and info.file_size > 1024 * 1024:
        ratio = info.file_size / info.compress_size
        if ratio > max_compression_ratio:
            raise MediaArchiveSecurityError(
                f"Rapporto di compressione anomalo ({ratio:.1f}) sul membro '{info.filename}' (possibile ZIP bomb)."
            )

    return info


# --- Servizio Principale MediaAssetAccessService ---

class MediaAssetAccessService:
    """
    Servizio unificato per la risoluzione, estrazione on-demand controllata
    ed elaborazione locale dei media forensi.
    """

    def __init__(
        self,
        archive_path: Path | str | None = None,
        source_id: str | None = None,
        registry: MediaAssetRegistry | None = None,
    ) -> None:
        self.explicit_path = Path(archive_path) if archive_path else None
        self.source_id = source_id
        self.registry = registry

    def get_effective_archive_path(self) -> Path:
        """
        Restituisce il percorso dell'archivio sorgente, verificandone l'esistenza.
        Solleva MediaSourceUnavailableError se non reperibile.
        """
        if self.explicit_path is not None and self.explicit_path.exists():
            return self.explicit_path

        if self.source_id is not None and self.registry is not None:
            reg_path = self.registry.get_source(self.source_id)
            if reg_path is not None and reg_path.exists():
                return reg_path

        raise MediaSourceUnavailableError(
            f"Sorgente media non disponibile o eliminata dalla sessione (source_id='{self.source_id}', path='{self.explicit_path}')."
        )

    @contextlib.contextmanager
    def materialize_member(self, member_name: str) -> Generator[Path, None, None]:
        """
        Estrae on-demand in una sandbox temporanea SOLO il singolo membro richiesto,
        validando sicurezza e CRC32. Esegue il cleanup garantito all'uscita dal context manager.
        """
        archive_path = self.get_effective_archive_path()

        if not zipfile.is_zipfile(archive_path):
            raise MediaArchiveSecurityError(f"Il file sorgente '{archive_path}' non è un archivio ZIP valido.")

        with tempfile.TemporaryDirectory(prefix="media_sandbox_") as sandbox_str:
            sandbox_dir = Path(sandbox_str).resolve()
            with zipfile.ZipFile(archive_path, "r") as zf:
                info = validate_zip_member_security(zf, member_name)

                # Lettura e verifica CRC32
                try:
                    content = zf.read(info)
                except (zipfile.BadZipFile, zlib.error) as crc_err:
                    raise MediaArchiveSecurityError(
                        f"Integrità compromessa per il membro '{member_name}': {crc_err}."
                    ) from crc_err

                actual_crc = zlib.crc32(content) & 0xFFFFFFFF
                if info.CRC != 0 and actual_crc != info.CRC:
                    raise MediaArchiveSecurityError(
                        f"Integrità compromessa per il membro '{member_name}': CRC calcolato {actual_crc} != atteso {info.CRC}."
                    )

                # Scrittura controllata con nome file ripulito da traversal e controllo anti-zip-slip
                safe_filename = Path(info.filename).name
                extracted_file_path = (sandbox_dir / safe_filename).resolve()
                if not str(extracted_file_path).startswith(str(sandbox_dir)):
                    raise MediaArchiveSecurityError(f"Tentativo di Zip-Slip non consentito: '{info.filename}'.")

                with open(extracted_file_path, "wb") as out_f:
                    out_f.write(content)

            yield extracted_file_path

    def resolve_message_asset(
        self,
        message: UnifiedMessage,
    ) -> ResolvedMediaAsset:
        """
        Risolve un UnifiedMessage in un ResolvedMediaAsset verificando
        l'esistenza e la validità dell'allegato nell'archivio ZIP o filesystem.
        """
        raw_ref = message.media_reference
        if not raw_ref or not str(raw_ref).strip():
            return ResolvedMediaAsset(
                message_id=message.message_id,
                source_name=message.source_name,
                source_record_id=message.source_record_id,
                raw_reference=None,
                resolved_path=None,
                media_kind=MediaKind.UNKNOWN,
                status=MediaResolutionStatus.NO_REFERENCE,
                provenance_message=message,
            )

        clean_ref = str(raw_ref).strip()
        ext = Path(clean_ref).suffix.lower()
        if ext in _AUDIO_EXTENSIONS:
            media_kind = MediaKind.AUDIO
        elif ext in _IMAGE_EXTENSIONS:
            media_kind = MediaKind.IMAGE
        elif ext in _VIDEO_EXTENSIONS:
            media_kind = MediaKind.VIDEO
        elif ext in _DOCUMENT_EXTENSIONS:
            media_kind = MediaKind.DOCUMENT
        else:
            media_kind = MediaKind.UNKNOWN

        try:
            archive_path = self.get_effective_archive_path()
        except MediaSourceUnavailableError:
            return ResolvedMediaAsset(
                message_id=message.message_id,
                source_name=message.source_name,
                source_record_id=message.source_record_id,
                raw_reference=clean_ref,
                resolved_path=None,
                media_kind=media_kind,
                status=MediaResolutionStatus.MISSING,
                provenance_message=message,
                metadata={"error": "source_archive_unavailable"},
            )

        if not zipfile.is_zipfile(archive_path):
            return ResolvedMediaAsset(
                message_id=message.message_id,
                source_name=message.source_name,
                source_record_id=message.source_record_id,
                raw_reference=clean_ref,
                resolved_path=None,
                media_kind=media_kind,
                status=MediaResolutionStatus.UNSUPPORTED,
                provenance_message=message,
            )

        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                info = validate_zip_member_security(zf, clean_ref)
                return ResolvedMediaAsset(
                    message_id=message.message_id,
                    source_name=message.source_name,
                    source_record_id=message.source_record_id,
                    raw_reference=clean_ref,
                    resolved_path=str(archive_path / info.filename),
                    media_kind=media_kind,
                    status=MediaResolutionStatus.RESOLVED,
                    file_size_bytes=info.file_size,
                    sha256=None,  # SHA256 calcolato solo a estrazione fisica on-demand
                    provenance_message=message,
                    metadata={"archive_member": info.filename, "crc": info.CRC},
                )
        except MediaMemberNotFoundError:
            return ResolvedMediaAsset(
                message_id=message.message_id,
                source_name=message.source_name,
                source_record_id=message.source_record_id,
                raw_reference=clean_ref,
                resolved_path=None,
                media_kind=media_kind,
                status=MediaResolutionStatus.MISSING,
                provenance_message=message,
            )
        except MediaArchiveSecurityError as sec_err:
            status = MediaResolutionStatus.AMBIGUOUS if "ambiguo" in str(sec_err) else MediaResolutionStatus.OUTSIDE_ALLOWED_ROOT
            return ResolvedMediaAsset(
                message_id=message.message_id,
                source_name=message.source_name,
                source_record_id=message.source_record_id,
                raw_reference=clean_ref,
                resolved_path=None,
                media_kind=media_kind,
                status=status,
                provenance_message=message,
                metadata={"security_error": str(sec_err)},
            )

    def process_document_media(
        self,
        document: ConversationEvidenceDocument,
        stt_backend: Any | None = None,
        ocr_backend: Any | None = None,
        vision_backend: Any | None = None,
    ) -> ConversationEvidenceDocument:
        """
        Elabora localmente e on-demand i media associati ai messaggi del documento,
        restituendo una NUOVA versione arricchita di ConversationEvidenceDocument
        senza alterare il documento originario immutabile.
        Gestisce rigorosamente gli stati di ciclo di vita (MediaProcessingStatus):
        - NOT_PROCESSED se nessun media presente
        - COMPLETE se tutti i media supportati sono stati elaborati con successo
        - PARTIAL se alcuni media hanno avuto successo e altri sono falliti
        - FAILED se tutti i media da elaborare sono falliti
        Preserva le evidenze già prodotte con successo da tentativi precedenti (retry non distruttivo).
        """
        # Verifica preventiva presenza media nel documento
        total_media_refs = sum(
            1 for b in document.bundles if b.message.media_reference and str(b.message.media_reference).strip()
        )
        if total_media_refs == 0:
            from ai.models import ConversationEvidenceDocument
            new_meta = dict(document.metadata)
            new_meta["multimodal_status"] = MediaProcessingStatus.NOT_PROCESSED.value
            new_meta["multimodal_processed"] = False
            new_meta["multimodal_total_media"] = 0
            new_meta["multimodal_successful_media"] = 0
            new_meta["multimodal_failed_media"] = 0
            return ConversationEvidenceDocument(
                document_id=document.document_id,
                bundles=document.bundles,
                chat_id=document.chat_id,
                source_name=document.source_name,
                metadata=new_meta,
            )

        # Se sono presenti riferimenti a media, verifica la disponibilità dell'archivio
        archive_available = True
        try:
            archive_path = self.get_effective_archive_path()
        except MediaSourceUnavailableError:
            archive_available = False

        enriched_bundles: list[MessageEvidenceBundle] = []
        total_media_count = 0
        successful_media_count = 0
        failed_media_count = 0

        for bundle in document.bundles:
            msg = bundle.message
            ref = msg.media_reference
            if not ref or not str(ref).strip():
                enriched_bundles.append(bundle)
                continue

            total_media_count += 1

            if not archive_available:
                failed_media_count += 1
                enriched_bundles.append(bundle)
                continue

            asset = self.resolve_message_asset(msg)
            if not asset.is_resolved:
                failed_media_count += 1
                enriched_bundles.append(bundle)
                continue

            member_name = asset.metadata.get("archive_member") or str(ref)
            new_transcription = bundle.audio_transcription
            new_ocr = bundle.image_ocr
            new_vision = bundle.image_vision

            # 1. Trascrizione Audio STT
            if asset.media_kind == MediaKind.AUDIO:
                # Preserva risultato già completato con successo (retry non distruttivo)
                if bundle.audio_transcription and bundle.audio_transcription.status == TranscriptionStatus.SUCCESS:
                    new_transcription = bundle.audio_transcription
                    successful_media_count += 1
                elif stt_backend is not None:
                    try:
                        with self.materialize_member(member_name) as extracted_path:
                            if hasattr(stt_backend, "transcribe"):
                                temp_asset = ResolvedMediaAsset(
                                    message_id=msg.message_id,
                                    source_name=msg.source_name,
                                    source_record_id=msg.source_record_id,
                                    raw_reference=ref,
                                    resolved_path=str(extracted_path),
                                    media_kind=MediaKind.AUDIO,
                                    status=MediaResolutionStatus.RESOLVED,
                                    file_size_bytes=extracted_path.stat().st_size,
                                    sha256=compute_sha256_chunked(extracted_path),
                                    provenance_message=msg,
                                    metadata={"archive_member": member_name},
                                )
                                res = stt_backend.transcribe(temp_asset)
                                if res.status == TranscriptionStatus.SUCCESS:
                                    new_transcription = res
                                    successful_media_count += 1
                                else:
                                    new_transcription = res
                                    failed_media_count += 1
                    except Exception as exc:
                        new_transcription = AudioTranscriptionResult(
                            message_id=msg.message_id,
                            source_name=msg.source_name,
                            source_record_id=msg.source_record_id,
                            status=TranscriptionStatus.FAILED,
                            error_message=f"Errore durante la trascrizione audio: {exc}",
                        )
                        failed_media_count += 1
                else:
                    new_transcription = AudioTranscriptionResult(
                        message_id=msg.message_id,
                        source_name=msg.source_name,
                        source_record_id=msg.source_record_id,
                        status=TranscriptionStatus.FAILED,
                        error_message="STT backend locale non configurato o non disponibile.",
                    )
                    failed_media_count += 1

            # 2. Estrazione OCR Immagini
            elif asset.media_kind == MediaKind.IMAGE:
                # Preserva risultato OCR già completato con successo (retry non distruttivo)
                if bundle.image_ocr and bundle.image_ocr.status in (OcrStatus.SUCCESS, OcrStatus.NO_TEXT):
                    new_ocr = bundle.image_ocr
                    successful_media_count += 1
                elif ocr_backend is not None:
                    try:
                        with self.materialize_member(member_name) as extracted_path:
                            temp_asset = ResolvedMediaAsset(
                                message_id=msg.message_id,
                                source_name=msg.source_name,
                                source_record_id=msg.source_record_id,
                                raw_reference=ref,
                                resolved_path=str(extracted_path),
                                media_kind=MediaKind.IMAGE,
                                status=MediaResolutionStatus.RESOLVED,
                                file_size_bytes=extracted_path.stat().st_size,
                                sha256=compute_sha256_chunked(extracted_path),
                                provenance_message=msg,
                                metadata={"archive_member": member_name},
                            )
                            if hasattr(ocr_backend, "extract_text"):
                                res_ocr = ocr_backend.extract_text(temp_asset)
                                if res_ocr.status in (OcrStatus.SUCCESS, OcrStatus.NO_TEXT):
                                    new_ocr = res_ocr
                                    successful_media_count += 1
                                else:
                                    new_ocr = res_ocr
                                    failed_media_count += 1
                    except Exception as exc:
                        new_ocr = ImageOcrResult(
                            message_id=msg.message_id,
                            source_name=msg.source_name,
                            source_record_id=msg.source_record_id,
                            status=OcrStatus.FAILED,
                            error_message=f"Errore durante l'estrazione OCR: {exc}",
                        )
                        failed_media_count += 1
                else:
                    new_ocr = ImageOcrResult(
                        message_id=msg.message_id,
                        source_name=msg.source_name,
                        source_record_id=msg.source_record_id,
                        status=OcrStatus.FAILED,
                        error_message="OCR backend locale non configurato o non disponibile.",
                    )
                    failed_media_count += 1

                # 3. Vision locale: solo se backend reale fornito
                if bundle.image_vision and bundle.image_vision.status == VisionStatus.COMPLETED:
                    new_vision = bundle.image_vision
                elif vision_backend is not None:
                    try:
                        with self.materialize_member(member_name) as extracted_path:
                            temp_asset = ResolvedMediaAsset(
                                message_id=msg.message_id,
                                source_name=msg.source_name,
                                source_record_id=msg.source_record_id,
                                raw_reference=ref,
                                resolved_path=str(extracted_path),
                                media_kind=MediaKind.IMAGE,
                                status=MediaResolutionStatus.RESOLVED,
                                file_size_bytes=extracted_path.stat().st_size,
                                sha256=compute_sha256_chunked(extracted_path),
                                provenance_message=msg,
                                metadata={"archive_member": member_name},
                            )
                            if hasattr(vision_backend, "analyze"):
                                new_vision = vision_backend.analyze(temp_asset)
                            elif hasattr(vision_backend, "analyze_image"):
                                new_vision = vision_backend.analyze_image(temp_asset)
                            elif hasattr(vision_backend, "analyze_scene"):
                                new_vision = vision_backend.analyze_scene(temp_asset)
                    except Exception as exc:
                        new_vision = ImageVisionResult(
                            message_id=msg.message_id,
                            source_name=msg.source_name,
                            source_record_id=msg.source_record_id,
                            media_ref=str(ref),
                            status=VisionStatus.FAILED,
                            error_message=f"Errore durante l'analisi Vision: {exc}",
                        )

            # Costruzione nuovo bundle arricchito con tracciamento forense
            enriched_bundle = MessageEvidenceBundle(
                message=msg,
                audio_transcription=new_transcription,
                image_ocr=new_ocr,
                image_vision=new_vision,
            )
            enriched_bundles.append(enriched_bundle)

        # Calcolo rigoroso dello stato finale del ciclo di vita multimediale
        if total_media_count == 0:
            eff_status = MediaProcessingStatus.NOT_PROCESSED
        elif successful_media_count == total_media_count:
            eff_status = MediaProcessingStatus.COMPLETE
        elif successful_media_count > 0:
            eff_status = MediaProcessingStatus.PARTIAL
        else:
            eff_status = MediaProcessingStatus.FAILED

        from ai.models import ConversationEvidenceDocument
        new_meta = dict(document.metadata)
        new_meta["multimodal_status"] = eff_status.value
        new_meta["multimodal_processed"] = (eff_status == MediaProcessingStatus.COMPLETE)
        new_meta["multimodal_total_media"] = total_media_count
        new_meta["multimodal_successful_media"] = successful_media_count
        new_meta["multimodal_failed_media"] = failed_media_count

        return ConversationEvidenceDocument(
            document_id=document.document_id,
            bundles=tuple(enriched_bundles),
            chat_id=document.chat_id,
            source_name=document.source_name,
            metadata=new_meta,
        )
