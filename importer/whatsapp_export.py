"""
importer/whatsapp_export.py
---------------------------
Importer concreto per esportazioni chat di WhatsApp in formato TXT e ZIP.

Sorgenti supportate:
1. File TXT esportato direttamente da WhatsApp (Android o iOS);
2. File ZIP esportato direttamente da WhatsApp contenente il transcript .txt
   e gli allegati multimediali opzionali (immagini, audio, video, documenti).

Requisiti forensi e di sicurezza:
- READ-ONLY: nessuna modifica al file sorgente;
- ZERO AI / ZERO NETWORK durante l'ingestion;
- Streaming: import_records produce generatori lazy di RawRecord;
- Protezione ZIP: no path traversal, no symlink, limiti zip bomb (numero membri,
  dimensione totale decompressa, compression ratio);
- Lettura diretta dall'archivio ZIP senza estrazione indiscriminata su disco;
- Parsing conservativo: layout Android e iOS, date 24h e 12h AM/PM, secondi opzionali,
  caratteri Unicode direzionali, messaggi multilinea, messaggi di sistema.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import re
from typing import Any, Iterator
import zipfile

from importer.base import BaseImporter
from importer.models import RawRecord

logger = logging.getLogger(__name__)

_SOURCE_NAME = "whatsapp_export"
_IMPORTER_VERSION = "0.1.0"

# Limiti di sicurezza per archivi ZIP
_MAX_ZIP_ENTRIES = 10_000
_MAX_UNCOMPRESSED_SIZE = 500 * 1024 * 1024  # 500 MB
_MAX_COMPRESSION_RATIO = 100.0
_MAX_SINGLE_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

# Caratteri invisibili e direzionali Unicode inseriti da WhatsApp
_DIR_CHARS = r"[\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
_DIR_CHARS_RE = re.compile(_DIR_CHARS)
_SPACE_NORM_RE = re.compile(r"[\u202f\xa0]")

# Regex per header Android:
# <data>, <ora> - <mittente / sistema>
_ANDROID_HEADER_RE = re.compile(
    r"^" + _DIR_CHARS + r"*"
    r"(?P<date>\d{1,4}[/\-. ]\d{1,2}[/\-. ]\d{2,4})"
    r"[, \t\u202f\xa0]+"
    r"(?P<time>\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:[\s\u202f\xa0]*(?:[aApP]\.?[mM]\.?|[aApP]))?)"
    r"\s*[-–—]\s*"
    r"(?P<rem>.*)$"
)

# Regex per header iOS:
# [<data>, <ora>] <mittente / sistema>
_IOS_HEADER_RE = re.compile(
    r"^" + _DIR_CHARS + r"*\s*\["
    r"(?P<date>\d{1,4}[/\-. ]\d{1,2}[/\-. ]\d{2,4})"
    r"[, \t\u202f\xa0]+"
    r"(?P<time>\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:[\s\u202f\xa0]*(?:[aApP]\.?[mM]\.?|[aApP]))?)"
    r"\]\s*"
    r"(?P<rem>.*)$"
)

# Frasi caratteristiche di messaggi di sistema (multilingua)
_SYSTEM_PHRASES = (
    "crittografati end-to-end",
    "crittografia end-to-end",
    "end-to-end encrypted",
    "cifrado de extremo a extremo",
    "chiffrés de bout en bout",
    "cambiato l'oggetto in",
    "cambiato l'oggetto",
    "changed the subject to",
    "changed the subject",
    "cambió el asunto a",
    "cambió el asunto",
    "a changé le sujet",
    "cambiato l'immagine",
    "changed this group's icon",
    "cambiato l'icona",
    "cambió el icono",
    "a changé l'icône",
    "ha aggiunto",
    "hai aggiunto",
    "added",
    "añadió a",
    "a ajouté",
    "ha rimosso",
    "hai rimosso",
    "removed",
    "eliminó a",
    "a retiré",
    "ha abbandonato",
    "hai abbandonato",
    "left",
    "salió del grupo",
    "a quitté",
    "creato il gruppo",
    "created group",
    "creó el grupo",
    "a créé le groupe",
    "il codice di sicurezza",
    "security code changed",
    "código de seguridad",
    "code de sécurité",
    "questo messaggio è stato eliminato",
    "this message was deleted",
    "este mensaje fue eliminado",
    "ce message a été supprimé",
    "hai eliminato questo messaggio",
    "you deleted this message",
    "chiamata persa",
    "missed voice call",
    "missed video call",
    "llamada perdida",
    "appel manqué",
)

# Regex per estrazione del nome file allegato nel testo del messaggio
_ATTACHMENT_RE = re.compile(
    r"(?:<allegato:\s*|<attached:\s*)(?P<fn1>[^>]+)>|"
    r"(?P<fn2>[^\r\n:]+?)\s*\((?:file allegato|file attached|archivo adjunto|fichier attaché)\)|"
    r"\b(?P<fn3>[a-zA-Z0-9_\-.]+\.(?:jpg|jpeg|png|webp|gif|opus|mp3|wav|ogg|m4a|aac|mp4|3gp|mov|pdf|docx?|xlsx?|txt|vcf))\b",
    re.IGNORECASE,
)


def _strip_invisible(s: str) -> str:
    """Rimuove caratteri di controllo bidirezionali e BOM."""
    return _DIR_CHARS_RE.sub("", s)


def _detect_and_decode_text(raw_bytes: bytes) -> tuple[str, str]:
    """
    Decodifica in modo conservativo un buffer di byte verificando UTF-8, UTF-8 BOM
    o charset-normalizer come fallback sicuro. Non sostituisce caratteri invalidi.
    """
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        try:
            return raw_bytes[3:].decode("utf-8"), "utf-8-sig"
        except UnicodeDecodeError:
            pass

    try:
        return raw_bytes.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    try:
        from charset_normalizer import from_bytes

        results = from_bytes(raw_bytes)
        best = results.best()
        if best and best.encoding:
            return str(best), best.encoding
    except Exception:
        pass

    raise ValueError("Impossibile decodificare il transcript WhatsApp con un encoding forense valido.")


def _validate_zip_archive(zf: zipfile.ZipFile) -> None:
    """
    Esegue controlli di sicurezza rigorosi su un archivio ZIP:
    - Path traversal ('..')
    - Percorsi assoluti
    - Symlink pericolosi
    - Limite numero membri
    - Limite dimensione decompressa totale e per file (ZIP bomb)
    - Rapporto di compressione anomalo
    """
    infolist = zf.infolist()
    if len(infolist) > _MAX_ZIP_ENTRIES:
        raise ValueError(f"Archivio ZIP non sicuro: contiene {len(infolist)} membri (max {_MAX_ZIP_ENTRIES}).")

    total_uncompressed = 0

    for info in infolist:
        name = info.filename

        # Verifica path assoluti
        if os.path.isabs(name) or name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
            raise ValueError(f"Membro ZIP con percorso assoluto non consentito: {name}")

        # Verifica path traversal
        norm_parts = Path(name).parts
        if ".." in norm_parts or any(p in ("..", "~") for p in norm_parts):
            raise ValueError(f"Membro ZIP con path traversal ('..') non consentito: {name}")

        # Verifica symlink (attributi POSIX in zip)
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError(f"Membro ZIP con symlink non consentito: {name}")

        # Verifica dimensioni e compression ratio
        if info.file_size > _MAX_SINGLE_FILE_SIZE:
            raise ValueError(f"Membro ZIP supera la dimensione massima consentita: {name} ({info.file_size} byte).")

        total_uncompressed += info.file_size
        if total_uncompressed > _MAX_UNCOMPRESSED_SIZE:
            raise ValueError("Dimensione decompressa totale supera il limite di sicurezza (possibile ZIP bomb).")

        if info.compress_size > 0 and info.file_size > 1024 * 1024:
            ratio = info.file_size / info.compress_size
            if ratio > _MAX_COMPRESSION_RATIO:
                raise ValueError(f"Rapporto di compressione anomalo ({ratio:.1f}) sul file {name} (possibile ZIP bomb).")


def _match_header_line(line: str) -> tuple[str, str, str] | None:
    """
    Riconosce se una riga rappresenta un header di messaggio WhatsApp.

    Returns
    -------
    tuple[str, str, str] | None
        (formato_tipo, raw_timestamp, remainder) se corrisponde ad Android o iOS,
        None altrimenti.
    """
    m_and = _ANDROID_HEADER_RE.match(line)
    if m_and:
        raw_ts = f"{m_and.group('date')}, {m_and.group('time')}"
        return "android", raw_ts, m_and.group("rem")

    m_ios = _IOS_HEADER_RE.match(line)
    if m_ios:
        raw_ts = f"{m_ios.group('date')}, {m_ios.group('time')}"
        return "ios", raw_ts, m_ios.group("rem")

    return None


def _is_transcript_content(sample_text: str) -> bool:
    """
    Verifica strutturalmente se un testo campionario contiene un numero
    minimo ragionevole di entry di esportazione WhatsApp.
    """
    lines = [ln for ln in sample_text.splitlines() if ln.strip()]
    if not lines:
        return False

    header_matches = 0
    for ln in lines[:100]:
        if _match_header_line(ln) is not None:
            header_matches += 1

    if len(lines) <= 3:
        return header_matches >= 1
    return header_matches >= 2


def _find_unique_transcript(zf: zipfile.ZipFile) -> zipfile.ZipInfo:
    """
    Identifica in modo univoco il file di transcript WhatsApp (.txt) all'interno dello ZIP.
    Se vi sono ambiguità strutturali, fallisce esplicitamente invece di scegliere a caso.
    """
    txt_members = [
        m for m in zf.infolist()
        if m.filename.lower().endswith(".txt") and not m.is_dir()
    ]

    if not txt_members:
        raise ValueError("Nessun file .txt presente nell'archivio ZIP.")

    candidates: list[zipfile.ZipInfo] = []

    for tm in txt_members:
        # Legge un campione di massimo 64KB per il controllo strutturale
        try:
            with zf.open(tm, "r") as f:
                sample_bytes = f.read(65536)
            sample_str, _ = _detect_and_decode_text(sample_bytes)
            if _is_transcript_content(sample_str):
                candidates.append(tm)
        except Exception:
            continue

    if not candidates:
        raise ValueError("Nessun file .txt nell'archivio ZIP presenta la struttura di un export WhatsApp.")

    if len(candidates) > 1:
        raise ValueError(
            f"Ambiguità nell'archivio ZIP: rilevati molteplici ({len(candidates)}) file .txt "
            f"compatibili come transcript WhatsApp ({', '.join(c.filename for c in candidates)}). "
            f"Impossibile selezionare arbitrariamente."
        )

    return candidates[0]


class WhatsAppExportImporter(BaseImporter):
    """
    Importer per esportazioni chat di WhatsApp in formato TXT o archivio ZIP.
    """

    @property
    def source_name(self) -> str:
        return _SOURCE_NAME

    def can_import(self, source_path: Path) -> bool:
        """
        Verifica non distruttiva se il file è un export WhatsApp TXT o ZIP valido.
        """
        if not source_path.exists() or not source_path.is_file():
            return False

        # 1. Caso archivio ZIP
        if zipfile.is_zipfile(source_path):
            try:
                with zipfile.ZipFile(source_path, "r") as zf:
                    _validate_zip_archive(zf)
                    # Verifica che esista un transcript univoco compatibile
                    _find_unique_transcript(zf)
                    return True
            except Exception:
                return False

        # 2. Caso file TXT
        try:
            with open(source_path, "rb") as f:
                header_bytes = f.read(65536)
            if not header_bytes:
                return False
            sample_str, _ = _detect_and_decode_text(header_bytes)
            return _is_transcript_content(sample_str)
        except Exception:
            return False

    def import_records(self, source_path: Path) -> Iterator[RawRecord]:
        """
        Legge il transcript WhatsApp (da TXT o da ZIP) e produce RawRecord in streaming.
        """
        self.validate_source(source_path)

        is_archive = zipfile.is_zipfile(source_path)
        archive_members_by_name: dict[str, zipfile.ZipInfo] = {}

        if is_archive:
            with zipfile.ZipFile(source_path, "r") as zf:
                _validate_zip_archive(zf)
                transcript_info = _find_unique_transcript(zf)
                transcript_name = transcript_info.filename
                raw_bytes = zf.read(transcript_info)

                # Mappa dei file allegati presenti nell'archivio (escludendo il transcript stesso)
                for info in zf.infolist():
                    if not info.is_dir() and info.filename != transcript_name:
                        # Mappa sia il percorso relativo completo che il solo nome file
                        archive_members_by_name[info.filename] = info
                        archive_members_by_name[Path(info.filename).name] = info
        else:
            transcript_name = source_path.name
            raw_bytes = source_path.read_bytes()

        decoded_text, encoding = _detect_and_decode_text(raw_bytes)

        lines = decoded_text.splitlines()
        msg_idx = 0

        # Buffer per il messaggio corrente (per gestire messaggi multilinea)
        cur_header_line: str | None = None
        cur_raw_ts: str | None = None
        cur_sender: str | None = None
        cur_is_system: bool = False
        cur_body_lines: list[str] = []
        cur_line_start: int = 0
        cur_line_end: int = 0

        for line_idx, line in enumerate(lines, start=1):
            matched = _match_header_line(line)

            if matched is not None:
                # Emette il messaggio precedente se presente
                if cur_raw_ts is not None:
                    msg_idx += 1
                    yield self._create_raw_record(
                        source_path=source_path,
                        msg_idx=msg_idx,
                        raw_timestamp=cur_raw_ts,
                        sender=cur_sender,
                        body_lines=cur_body_lines,
                        raw_line=cur_header_line or "",
                        line_start=cur_line_start,
                        line_end=cur_line_end,
                        is_system=cur_is_system,
                        encoding=encoding,
                        is_archive=is_archive,
                        transcript_name=transcript_name,
                        archive_members=archive_members_by_name,
                    )

                # Inizializza il nuovo messaggio
                _, raw_ts, rem = matched
                cur_header_line = line
                cur_raw_ts = raw_ts
                cur_line_start = line_idx
                cur_line_end = line_idx

                # Separazione sender / body e classificazione messaggi di sistema
                sender, body, is_system = self._parse_sender_and_body(rem)
                cur_sender = sender
                cur_is_system = is_system
                cur_body_lines = [body]
            else:
                # Riga di continuazione (messaggio multilinea)
                if cur_raw_ts is not None:
                    cur_body_lines.append(line)
                    cur_line_end = line_idx

        # Emette l'ultimo messaggio accumulato
        if cur_raw_ts is not None:
            msg_idx += 1
            yield self._create_raw_record(
                source_path=source_path,
                msg_idx=msg_idx,
                raw_timestamp=cur_raw_ts,
                sender=cur_sender,
                body_lines=cur_body_lines,
                raw_line=cur_header_line or "",
                line_start=cur_line_start,
                line_end=cur_line_end,
                is_system=cur_is_system,
                encoding=encoding,
                is_archive=is_archive,
                transcript_name=transcript_name,
                archive_members=archive_members_by_name,
            )

    def _parse_sender_and_body(self, remainder: str) -> tuple[str | None, str, bool]:
        """
        Separa sender e body dopo l'header WhatsApp, riconoscendo messaggi di sistema.
        Non fa split(':') ingenuo per non rompere URL, nomi o punteggiatura.
        """
        rem_clean = remainder.strip()

        # Se non è presente ": ", è sicuramente un messaggio di sistema privo di sender
        if ": " not in rem_clean:
            return None, rem_clean, True

        # Verifica se la stringa corrisponde a un'azione di sistema nota
        rem_lower = rem_clean.lower()
        if any(phrase in rem_lower for phrase in _SYSTEM_PHRASES):
            # Alcune notifiche di sistema possono contenere ": " (es. "cambiato l'oggetto in: ...")
            # Se la parte prima di ": " è un'azione di sistema, classifichiamo come system
            sender_cand, sep, body_cand = rem_clean.partition(": ")
            cand_lower = sender_cand.lower()
            if any(phrase in cand_lower for phrase in _SYSTEM_PHRASES):
                return None, rem_clean, True

        # Separazione al PRIMO ": "
        sender_part, _, body_part = rem_clean.partition(": ")
        clean_sender = _strip_invisible(sender_part).strip()

        if not clean_sender:
            return None, body_part, True

        return clean_sender, body_part, False

    def _create_raw_record(
        self,
        source_path: Path,
        msg_idx: int,
        raw_timestamp: str,
        sender: str | None,
        body_lines: list[str],
        raw_line: str,
        line_start: int,
        line_end: int,
        is_system: bool,
        encoding: str,
        is_archive: bool,
        transcript_name: str,
        archive_members: dict[str, zipfile.ZipInfo],
    ) -> RawRecord:
        """Costruisce un'istanza immutabile di RawRecord per un singolo messaggio."""
        text_content = "\n".join(body_lines)

        # Ricerca eventuale allegato
        attachment_name: str | None = None
        matched_att = _ATTACHMENT_RE.search(text_content)
        if matched_att:
            raw_fn = matched_att.group("fn1") or matched_att.group("fn2") or matched_att.group("fn3")
            if raw_fn:
                clean_fn = _strip_invisible(raw_fn).strip()
                if clean_fn:
                    attachment_name = clean_fn

        # Riconoscimento media reference con membro ZIP
        media_reference: str | None = None
        archive_member_name: str | None = None
        archive_member_size: int | None = None
        archive_member_crc: int | None = None

        if is_archive and attachment_name:
            member_info = archive_members.get(attachment_name) or archive_members.get(Path(attachment_name).name)
            if member_info is not None:
                media_reference = member_info.filename
                archive_member_name = member_info.filename
                archive_member_size = member_info.file_size
                archive_member_crc = member_info.CRC

        raw_fields: dict[str, Any] = {
            "raw_timestamp": raw_timestamp,
            "sender": sender,
            "text": text_content,
            "raw_line": raw_line,
            "line_start": line_start,
            "line_end": line_end,
            "is_system_message": is_system,
            "attachment_name": attachment_name,
            "chat_id": "chat_1",
        }

        metadata: dict[str, Any] = {
            "importer_version": _IMPORTER_VERSION,
            "encoding": encoding,
            "is_archive": is_archive,
            "transcript_filename": transcript_name,
        }

        if media_reference is not None:
            metadata["archive_member_name"] = archive_member_name
            metadata["archive_member_size"] = archive_member_size
            metadata["archive_member_crc"] = archive_member_crc

        return RawRecord(
            source_name=self.source_name,
            source_path=str(source_path),
            source_record_id=f"msg_{msg_idx}",
            record_type="message",
            raw_fields=raw_fields,
            media_reference=media_reference,
            metadata=metadata,
        )
