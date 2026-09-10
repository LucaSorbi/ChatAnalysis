"""
multimodal/resolver.py
----------------------
Risoluzione e validazione sicura degli asset multimediali forensi.

Principi di sicurezza:
1. I percorsi estratti da report e database forensi sono INPUT NON FIDATO.
2. Risoluzione canonica (resolve) e verifica di confinamento entro allowed_roots.
3. Blocco categorico di path traversal ('..') e tentativi di accesso a file di sistema.
4. Nessun download da remoto: i riferimenti URL (HTTP/HTTPS) producono REMOTE_REFERENCE.
5. Calcolo incrementale dello SHA-256 a blocchi (64KB), senza caricare l'intero file in memoria.
6. Linkage intra-source deterministico per WhatsApp msgstore.db (media_refs.message_row_id <-> messages._id).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Iterator, Sequence

from core.immutability import freeze_structural
from multimodal.models import MediaKind, MediaResolutionStatus, ResolvedMediaAsset
from unified.models import UnifiedMessage

# Estensioni per classificazione del tipo media
_AUDIO_EXTENSIONS = {".opus", ".ogg", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".wma", ".amr"}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"}
_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".3gp", ".m4v"}
_DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt", ".xlsx", ".zip", ".csv"}


def compute_sha256_chunked(file_path: Path, chunk_size: int = 65536) -> str:
    """
    Calcola l'hash SHA-256 di un file in sola lettura procedendo per chunk di 64KB.
    Non carica mai l'intero file in memoria, garantendo efficienza su file di grandi dimensioni.
    """
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


class MediaResolutionContext:
    """
    Contesto immutabile per il collegamento intra-source dei metadati multimediali.

    In WhatsApp msgstore.db, la tabella media_refs mappa ogni file_path al message_row_id.
    Questo contesto indicizza tali collegamenti per arricchire la risoluzione con piena provenance.
    """

    def __init__(self, msgstore_refs: dict[str, dict[str, Any]] | None = None) -> None:
        self._msgstore_refs: MappingProxyType[str, MappingProxyType[str, Any]] = (
            freeze_structural(msgstore_refs or {})
        )

    @classmethod
    def from_records(cls, records: Sequence[Any]) -> MediaResolutionContext:
        """
        Estrae e indicizza i record di tipo 'media_ref' da una sequenza di record (Raw o Normalized).
        """
        refs: dict[str, dict[str, Any]] = {}
        for rec in records:
            source_name = getattr(rec, "source_name", None)
            record_type = getattr(rec, "record_type", None)
            if source_name == "msgstore_db" and record_type == "media_ref":
                raw_fields = getattr(rec, "raw_fields", None)
                if raw_fields is None and hasattr(rec, "raw_record"):
                    raw_fields = getattr(rec.raw_record, "raw_fields", {})
                if isinstance(raw_fields, (dict, MappingProxyType)):
                    msg_row_id = raw_fields.get("message_row_id")
                    if msg_row_id is not None:
                        row_key = str(msg_row_id)
                        refs[row_key] = {
                            "media_ref_id": str(getattr(rec, "source_record_id", "")),
                            "file_path": raw_fields.get("file_path"),
                            "file_size": raw_fields.get("file_size"),
                            "media_type": raw_fields.get("media_type"),
                            "media_job_uuid": raw_fields.get("media_job_uuid"),
                        }
        return cls(refs)

    def get_msgstore_ref(self, source_record_id: str) -> MappingProxyType[str, Any] | None:
        """Restituisce il metadato media_ref associato al message _id, se presente."""
        return self._msgstore_refs.get(str(source_record_id))


class MediaResolver:
    """
    Risolutore di asset multimediali con validazione di sicurezza dei percorsi.
    """

    def __init__(
        self,
        allowed_roots: Sequence[str | Path],
        context: MediaResolutionContext | None = None,
    ) -> None:
        if not allowed_roots:
            raise ValueError("MediaResolver richiede almeno una root in allowed_roots per motivi di sicurezza.")
        # Risolve e normalizza canonicamente tutte le allowed_roots
        self._allowed_roots: tuple[Path, ...] = tuple(
            Path(r).resolve() for r in allowed_roots
        )
        self._context: MediaResolutionContext = context or MediaResolutionContext()

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        return self._allowed_roots

    @property
    def context(self) -> MediaResolutionContext:
        return self._context

    def _classify_media_kind(self, reference_str: str | None, message_type: Any) -> MediaKind:
        """Determina il MediaKind dall'estensione del file o dal tipo di messaggio."""
        if reference_str:
            # Estrazione estensione (in minuscolo)
            ext = Path(reference_str).suffix.lower()
            if ext in _AUDIO_EXTENSIONS:
                return MediaKind.AUDIO
            if ext in _IMAGE_EXTENSIONS:
                return MediaKind.IMAGE
            if ext in _VIDEO_EXTENSIONS:
                return MediaKind.VIDEO
            if ext in _DOCUMENT_EXTENSIONS:
                return MediaKind.DOCUMENT

        # Fallback al CanonicalMessageType se presente
        type_str = getattr(message_type, "value", str(message_type)).upper()
        if type_str == "AUDIO":
            return MediaKind.AUDIO
        elif type_str == "IMAGE":
            return MediaKind.IMAGE
        elif type_str == "VIDEO":
            return MediaKind.VIDEO
        elif type_str == "DOCUMENT":
            return MediaKind.DOCUMENT
        return MediaKind.UNKNOWN

    def _is_safe_under_roots(self, target_path: Path) -> Path | None:
        """
        Verifica se target_path si trova all'interno di una delle allowed_roots.
        Restituisce la root genitrice se sicuro, altrimenti None.
        """
        target_resolved = target_path.resolve()
        for root in self._allowed_roots:
            try:
                if target_resolved.is_relative_to(root):
                    return root
            except (ValueError, AttributeError):
                continue
        return None

    def resolve(self, message: UnifiedMessage) -> ResolvedMediaAsset:
        """
        Risolve un UnifiedMessage in un ResolvedMediaAsset immutabile.
        """
        source_name = message.source_name
        source_record_id = message.source_record_id
        message_id = message.message_id
        raw_ref = message.media_reference

        metadata_dict: dict[str, Any] = {
            "source_message_type": getattr(message.message_type, "value", str(message.message_type)),
        }

        # 1. Controlla eventuale arricchimento intra-source da media_refs (WhatsApp)
        if source_name == "msgstore_db":
            ref_data = self._context.get_msgstore_ref(source_record_id)
            if ref_data:
                metadata_dict["whatsapp_media_ref"] = dict(ref_data)
                if not raw_ref and ref_data.get("file_path"):
                    raw_ref = str(ref_data["file_path"])
                    metadata_dict["resolved_from_media_ref_fallback"] = True

        # 2. Nessun riferimento multimediale dichiarato
        if not raw_ref or not raw_ref.strip():
            media_kind = self._classify_media_kind(None, message.message_type)
            return ResolvedMediaAsset(
                message_id=message_id,
                source_name=source_name,
                source_record_id=source_record_id,
                raw_reference=None,
                resolved_path=None,
                media_kind=media_kind,
                status=MediaResolutionStatus.NO_REFERENCE,
                metadata=metadata_dict,
            )

        raw_ref_clean = raw_ref.strip()
        media_kind = self._classify_media_kind(raw_ref_clean, message.message_type)

        # 3. Controllo URL remoto (Fase E: nessun fetch di rete)
        lower_ref = raw_ref_clean.lower()
        if lower_ref.startswith(("http://", "https://", "ftp://", "ftps://")):
            metadata_dict["remote_url"] = raw_ref_clean
            return ResolvedMediaAsset(
                message_id=message_id,
                source_name=source_name,
                source_record_id=source_record_id,
                raw_reference=raw_ref_clean,
                resolved_path=None,
                media_kind=media_kind,
                status=MediaResolutionStatus.REMOTE_REFERENCE,
                metadata=metadata_dict,
            )

        # 4. Risoluzione percorso e verifica di sicurezza (Fase D)
        candidate_p = Path(raw_ref_clean)
        resolved_file: Path | None = None
        is_outside = False

        if candidate_p.is_absolute():
            # Percorso assoluto: verifica se cade dentro allowed_roots
            if self._is_safe_under_roots(candidate_p):
                if candidate_p.is_file():
                    resolved_file = candidate_p.resolve()
            else:
                is_outside = True
        else:
            # Percorso relativo: tenta risoluzione rispetto alla cartella della sorgente (se sicura) e poi a ciascuna allowed_root
            search_roots: list[Path] = []
            if message.source_path:
                try:
                    src_dir = Path(message.source_path).resolve().parent
                    if self._is_safe_under_roots(src_dir):
                        search_roots.append(src_dir)
                except Exception:
                    pass
            for r in self._allowed_roots:
                if r not in search_roots:
                    search_roots.append(r)

            for root in search_roots:
                tentative = (root / candidate_p).resolve()
                if self._is_safe_under_roots(tentative):
                    if tentative.is_file():
                        resolved_file = tentative
                        break
                else:
                    is_outside = True

        if is_outside and resolved_file is None:
            metadata_dict["security_violation"] = "Path escapes allowed roots"
            return ResolvedMediaAsset(
                message_id=message_id,
                source_name=source_name,
                source_record_id=source_record_id,
                raw_reference=raw_ref_clean,
                resolved_path=None,
                media_kind=media_kind,
                status=MediaResolutionStatus.OUTSIDE_ALLOWED_ROOT,
                metadata=metadata_dict,
            )

        # 5. File non trovato fisicamente -> MISSING
        if resolved_file is None:
            return ResolvedMediaAsset(
                message_id=message_id,
                source_name=source_name,
                source_record_id=source_record_id,
                raw_reference=raw_ref_clean,
                resolved_path=None,
                media_kind=media_kind,
                status=MediaResolutionStatus.MISSING,
                metadata=metadata_dict,
            )

        # 6. File trovato fisicamente e validato -> RESOLVED (Fase G: SHA-256 chunked)
        try:
            file_size = resolved_file.stat().st_size
            sha256_digest = compute_sha256_chunked(resolved_file)
            return ResolvedMediaAsset(
                message_id=message_id,
                source_name=source_name,
                source_record_id=source_record_id,
                raw_reference=raw_ref_clean,
                resolved_path=str(resolved_file),
                media_kind=media_kind,
                status=MediaResolutionStatus.RESOLVED,
                file_size_bytes=file_size,
                sha256=sha256_digest,
                metadata=metadata_dict,
            )
        except OSError as e:
            metadata_dict["read_error"] = str(e)
            return ResolvedMediaAsset(
                message_id=message_id,
                source_name=source_name,
                source_record_id=source_record_id,
                raw_reference=raw_ref_clean,
                resolved_path=None,
                media_kind=media_kind,
                status=MediaResolutionStatus.UNSUPPORTED,
                metadata=metadata_dict,
            )

    def resolve_batch(self, messages: Iterable[UnifiedMessage]) -> Iterator[ResolvedMediaAsset]:
        """Elabora in streaming un iteratore di UnifiedMessage generando ResolvedMediaAsset."""
        for msg in messages:
            yield self.resolve(msg)
