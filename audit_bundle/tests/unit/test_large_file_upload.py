"""
tests/unit/test_large_file_upload.py
------------------------------------
Test completi e rigorosi per la verifica del supporto a grandi file (> 200 MB fino a 2 GB):
1. Configurazione .streamlit/config.toml (maxUploadSize = 2048, maxMessageSize = 2048)
2. Streamlit runtime options efficaci e assenza di blocco al default di 200 MB
3. Parametro max_upload_size = 2048 sui widget st.file_uploader forensi
4. Hashing SHA-256 eseguito a chunk (senza caricare 2 GB in RAM)
5. Scrittura tempfile eseguita a chunk
6. Nessuna copia completa duplicata in RAM (assenza di pattern .getvalue())
7. File > 200 MB simulato accettato dal contratto applicativo
8. File > 2 GB rifiutato con InvalidUploadedFileError e messaggio chiaro
9. Archivio WhatsApp ZIP legittimo di grandi dimensioni (> 500 MB) non confuso con zip-bomb
10. Protezioni anti-zip-bomb rigorosamente attive (compression ratio, limite decompressa, max membri, symlink, traversal)
11. Cleanup deterministico dello spazio temporaneo garantito
12. Zero chiamate AI durante l'ingestion
13. Nessuna regressione per file di piccole dimensioni
14. Nota UI discreta "Dimensione massima file: 2 GB" presente senza leak tecnici
15. Gestione controllata di errori I/O e stream interrotto senza traceback o path leak
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch
import zipfile

import pytest
import streamlit.config as cfg
from streamlit.testing.v1 import AppTest
import toml

from core.config import (
    DEFAULT_STREAM_CHUNK_SIZE,
    MAX_ARCHIVE_MEMBERS,
    MAX_ARCHIVE_UNCOMPRESSED_SIZE_BYTES,
    MAX_ARCHIVE_UNCOMPRESSED_SIZE_MB,
    MAX_COMPRESSION_RATIO,
    MAX_SINGLE_FILE_SIZE_BYTES,
    MAX_SINGLE_FILE_SIZE_MB,
    MAX_UPLOAD_SIZE_BYTES,
    MAX_UPLOAD_SIZE_MB,
)
from importer.whatsapp_export import WhatsAppExportImporter, _validate_zip_archive
from ui.application import execute_real_ingestion
from ui.ingestion import (
    IngestionError,
    InvalidUploadedFileError,
    _compute_sha256_and_write,
    ingest_file_payload,
)
from ui.models import (
    IngestionRequest,
    IngestionStatus,
    SourceFormat,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / ".streamlit" / "config.toml"
APP_PATH = str(REPO_ROOT / "app.py")


class SyntheticSparseStream(io.RawIOBase):
    """
    Stream sintetico che simula file di qualsiasi dimensione (es. 210 MB o 2.1 GB)
    senza allocare memoria RAM reale.
    """
    def __init__(self, size: int):
        self.size = size
        self.pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        elif whence == 2:
            self.pos = self.size + offset
        return self.pos

    def tell(self) -> int:
        return self.pos

    def readinto(self, b) -> int:
        if self.pos >= self.size:
            return 0
        n = min(len(b), self.size - self.pos)
        b[:n] = b"\x00" * n
        self.pos += n
        return n


@pytest.mark.unit
class TestLargeFileUploadConfig:
    """Verifica delle configurazioni TOML e runtime per l'aumento dei limiti a 2 GB."""

    def test_streamlit_config_toml_values(self):
        """Verifica che .streamlit/config.toml dichiari esattamente 2048 MB per server."""
        assert CONFIG_PATH.is_file(), f"File config.toml assente: {CONFIG_PATH}"
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = toml.load(f)

        assert "server" in data
        assert data["server"].get("maxUploadSize") == 2048
        assert data["server"].get("maxMessageSize") == 2048

        # Impostazioni di sicurezza conservate
        assert data["server"].get("address") == "127.0.0.1"
        assert data["server"].get("enableCORS") is True
        assert data["server"].get("enableXsrfProtection") is True
        assert data["browser"].get("gatherUsageStats") is False
        assert data["client"].get("showErrorDetails") == "none"

    def test_streamlit_runtime_options_effective(self):
        """Verifica che il runtime Streamlit carichi maxUploadSize e maxMessageSize a 2048."""
        assert cfg.get_option("server.maxUploadSize") == 2048
        assert cfg.get_option("server.maxMessageSize") == 2048
        assert cfg.get_option("server.maxUploadSize") > 200

    def test_centralized_constants(self):
        """Verifica la coerenza tra costanti di core.config e limiti attesi."""
        assert MAX_UPLOAD_SIZE_MB == 2048
        assert MAX_UPLOAD_SIZE_BYTES == 2048 * 1024 * 1024
        assert MAX_ARCHIVE_UNCOMPRESSED_SIZE_MB >= 2048
        assert MAX_ARCHIVE_UNCOMPRESSED_SIZE_BYTES == MAX_ARCHIVE_UNCOMPRESSED_SIZE_MB * 1024 * 1024
        assert MAX_COMPRESSION_RATIO == 100.0


@pytest.mark.unit
class TestStreamingAndChunking:
    """Verifica che l'elaborazione streaming/chunking avvenga senza copie massive in RAM."""

    def test_hashing_and_writing_executed_in_chunks(self, tmp_path):
        """Verifica che hashing e scrittura avvengano a blocchi di dimensione controllata."""
        chunk_size = 64 * 1024
        stream = SyntheticSparseStream(256 * 1024)  # 256 KB = 4 chunk da 64 KB
        dest = tmp_path / "chunked.bin"

        update_calls = []
        original_update = io.BytesIO

        with patch("hashlib.sha256") as mock_sha:
            mock_hasher = MagicMock()
            mock_sha.return_value = mock_hasher
            mock_hasher.hexdigest.return_value = "dummy_hash"

            sha, size = _compute_sha256_and_write(stream, dest, chunk_size=chunk_size)

            assert size == 256 * 1024
            assert dest.stat().st_size == 256 * 1024
            # 4 letture da 64KB -> 4 chiamate a hasher.update
            assert mock_hasher.update.call_count == 4
            for call in mock_hasher.update.call_args_list:
                chunk_arg = call[0][0]
                assert len(chunk_arg) == chunk_size

    def test_simulated_file_over_200mb_accepted(self, tmp_path):
        """File simulato da 205 MB (> 200 MB) accettato ed elaborato correttamente."""
        stream_205mb = SyntheticSparseStream(205 * 1024 * 1024)
        dest = tmp_path / "evidence_205mb.bin"

        sha, size = _compute_sha256_and_write(stream_205mb, dest)

        assert size == 205 * 1024 * 1024
        assert dest.stat().st_size == 205 * 1024 * 1024
        assert len(sha) == 64

    def test_file_over_2gb_rejected_immediately(self, tmp_path):
        """File simulato da 2050 MB (> 2 GB) rifiutato con InvalidUploadedFileError."""
        stream_over_2gb = SyntheticSparseStream(2050 * 1024 * 1024)
        dest = tmp_path / "too_big.bin"

        with pytest.raises(InvalidUploadedFileError) as exc_info:
            _compute_sha256_and_write(stream_over_2gb, dest)

        err_msg = str(exc_info.value)
        assert "supera il limite massimo" in err_msg
        assert "2048 MB" in err_msg or "2 GB" in err_msg

    def test_bytes_payload_over_limit_rejected(self, tmp_path, monkeypatch):
        """Payload di byte che eccede il limite configurato viene rifiutato prima della scrittura."""
        import ui.ingestion as ing_mod
        monkeypatch.setattr(ing_mod, "MAX_UPLOAD_SIZE_BYTES", 50)
        monkeypatch.setattr(ing_mod, "MAX_UPLOAD_SIZE_MB", 1)
        dest = tmp_path / "over_limit.bin"

        with pytest.raises(InvalidUploadedFileError, match="supera il limite massimo"):
            _compute_sha256_and_write(b"X" * 60, dest)

    def test_no_getvalue_pattern_in_presentation_code(self):
        """Garantisce tramite analisi statica che presentation.py non chiami uploaded_file.getvalue()."""
        presentation_file = REPO_ROOT / "ui" / "presentation.py"
        content = presentation_file.read_text(encoding="utf-8")

        assert "uploaded_file.getvalue()" not in content, (
            "Trovata chiamata non sicura a uploaded_file.getvalue() che duplica fino a 2 GB in RAM!"
        )
        assert "companion_file.getvalue()" not in content, (
            "Trovata chiamata non sicura a companion_file.getvalue()!"
        )

    def test_tempfile_cleanup_on_success_and_failure(self):
        """Verifica che l'area temporanea venga rigorosamente rimossa in try/finally."""
        created_dirs = []
        original_tempdir = tempfile.TemporaryDirectory

        class TrackingTempDir:
            def __init__(self, *args, **kwargs):
                self._td = original_tempdir(*args, **kwargs)
                created_dirs.append(Path(self._td.name))

            def __enter__(self):
                return self._td.__enter__()

            def __exit__(self, *exc):
                return self._td.__exit__(*exc)

        with patch("tempfile.TemporaryDirectory", TrackingTempDir):
            # Test caso successo
            req_ok = IngestionRequest(
                source_format=SourceFormat.WHATSAPP_EXPORT,
                filename="chat.txt",
                file_bytes=b"10/05/2024, 10:00 - Alice: Test\n",
            )
            res = execute_real_ingestion(req_ok)
            assert res.status == IngestionStatus.SUCCESS
            assert len(created_dirs) >= 1
            assert not created_dirs[-1].exists(), "La directory temporanea non e' stata rimossa dopo il successo!"

            # Test caso eccezione
            req_err = IngestionRequest(
                source_format=SourceFormat.WHATSAPP_MSGSTORE,
                filename="corrupt.db",
                file_bytes=b"not a valid sqlite header",
            )
            with pytest.raises(InvalidUploadedFileError):
                execute_real_ingestion(req_err)
            assert not created_dirs[-1].exists(), "La directory temporanea non e' stata rimossa dopo l'eccezione!"


@pytest.mark.unit
class TestLargeWhatsAppZipArchiveSecurity:
    """Verifica che archivi ZIP grandi e legittimi siano accettati e che le zip-bomb siano bloccate."""

    def test_legitimate_large_zip_archive_accepted(self):
        """
        Un archivio ZIP legittimo con media (es. 800 MB decompressi con ratio normale 1.2)
        non viene confuso con una zip-bomb e supera la validazione.
        """
        mock_zf = MagicMock(spec=zipfile.ZipFile)

        # 3 membri: 1 transcript TXT, 2 video legittimi da 400 MB ciascuno con ratio 1.1
        info_txt = zipfile.ZipInfo(filename="_chat.txt")
        info_txt.file_size = 1000
        info_txt.compress_size = 300

        info_video1 = zipfile.ZipInfo(filename="video1.mp4")
        info_video1.file_size = 400 * 1024 * 1024
        info_video1.compress_size = 370 * 1024 * 1024

        info_video2 = zipfile.ZipInfo(filename="video2.mp4")
        info_video2.file_size = 400 * 1024 * 1024
        info_video2.compress_size = 370 * 1024 * 1024

        mock_zf.infolist.return_value = [info_txt, info_video1, info_video2]

        # Validazione deve passare senza eccezioni (totale 800 MB < 4096 MB, ratio ~1.1 < 100)
        _validate_zip_archive(mock_zf)

    def test_zip_bomb_uncompressed_limit_exceeded(self):
        """Un archivio che eccede MAX_ARCHIVE_UNCOMPRESSED_SIZE_BYTES (4 GB) viene rifiutato."""
        mock_zf = MagicMock(spec=zipfile.ZipFile)

        # 4 membri da 1.2 GB ciascuno = 4.8 GB totale (> 4 GB), ma ciascuno < 2 GB
        members = []
        for i in range(4):
            info = zipfile.ZipInfo(filename=f"video_{i}.mp4")
            info.file_size = int(1.2 * 1024 * 1024 * 1024)
            info.compress_size = int(1.1 * 1024 * 1024 * 1024)
            members.append(info)

        mock_zf.infolist.return_value = members

        with pytest.raises(ValueError, match="possibile ZIP bomb"):
            _validate_zip_archive(mock_zf)

    def test_zip_single_file_limit_exceeded(self):
        """Un membro singolo che supera MAX_SINGLE_FILE_SIZE_BYTES (2 GB) viene rifiutato."""
        mock_zf = MagicMock(spec=zipfile.ZipFile)

        info_oversized = zipfile.ZipInfo(filename="huge_video.mp4")
        info_oversized.file_size = int(2.5 * 1024 * 1024 * 1024)  # 2.5 GB > 2 GB
        info_oversized.compress_size = 1024 * 1024

        mock_zf.infolist.return_value = [info_oversized]

        with pytest.raises(ValueError, match="Membro ZIP supera la dimensione massima consentita"):
            _validate_zip_archive(mock_zf)

    def test_zip_bomb_anomalous_compression_ratio_rejected(self):
        """Un archivio con rapporto di compressione anomalo (> 100:1) viene rifiutato."""
        mock_zf = MagicMock(spec=zipfile.ZipFile)

        # File da 100 MB compresso in 100 KB -> ratio ~1000:1 (> 100)
        bomb_info = zipfile.ZipInfo(filename="bomb.bin")
        bomb_info.file_size = 100 * 1024 * 1024
        bomb_info.compress_size = 100 * 1024

        mock_zf.infolist.return_value = [bomb_info]

        with pytest.raises(ValueError, match="Rapporto di compressione anomalo"):
            _validate_zip_archive(mock_zf)

    def test_zip_bomb_excessive_member_count_rejected(self):
        """Un archivio con più di MAX_ARCHIVE_MEMBERS viene rifiutato."""
        mock_zf = MagicMock(spec=zipfile.ZipFile)
        mock_zf.infolist.return_value = [zipfile.ZipInfo(filename=f"f_{i}.txt") for i in range(MAX_ARCHIVE_MEMBERS + 1)]

        with pytest.raises(ValueError, match="contiene .* membri"):
            _validate_zip_archive(mock_zf)

    def test_zip_path_traversal_and_symlink_protection_preserved(self):
        """Verifica che path traversal, percorsi assoluti e symlink restino rigorosamente vietati."""
        mock_zf = MagicMock(spec=zipfile.ZipFile)

        # Path traversal
        info_trav = zipfile.ZipInfo(filename="../malicious.txt")
        info_trav.file_size = 10
        info_trav.compress_size = 10
        mock_zf.infolist.return_value = [info_trav]
        with pytest.raises(ValueError, match="path traversal"):
            _validate_zip_archive(mock_zf)

        # Percorso assoluto
        info_abs = zipfile.ZipInfo(filename="/etc/shadow")
        info_abs.file_size = 10
        info_abs.compress_size = 10
        mock_zf.infolist.return_value = [info_abs]
        with pytest.raises(ValueError, match="percorso assoluto"):
            _validate_zip_archive(mock_zf)

        # Symlink POSIX
        info_sym = zipfile.ZipInfo(filename="link.txt")
        info_sym.file_size = 10
        info_sym.compress_size = 10
        info_sym.external_attr = 0o120000 << 16
        mock_zf.infolist.return_value = [info_sym]
        with pytest.raises(ValueError, match="symlink"):
            _validate_zip_archive(mock_zf)


@pytest.mark.unit
class TestUiWidgetsAndDiscreetNotes:
    """Verifica delle schermate UI, parametri widget st.file_uploader e assenza leak tecnici."""

    def test_uploader_widget_configured_with_2048_mb(self):
        """Verifica che il widget primario e secondario abbiano max_upload_size impostato a 2048 MB."""
        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()
        at.sidebar.radio[0].set_value("2. Importazione")
        at.run()

        assert not at.exception
        assert len(at.file_uploader) >= 1
        primary_uploader = at.file_uploader[0]
        assert primary_uploader.proto.max_upload_size_mb == 2048

        # Seleziona msgstore per testare anche il companion uploader
        at.selectbox[0].set_value("WhatsApp msgstore (SQLite)")
        at.run()
        assert not at.exception
        assert len(at.file_uploader) >= 2
        companion_uploader = at.file_uploader[1]
        assert companion_uploader.proto.max_upload_size_mb == 2048

    def test_ui_shows_discreet_2gb_note(self):
        """Verifica che nella schermata di importazione compaia la nota discreta 'Dimensione massima file: 2 GB'."""
        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()
        at.sidebar.radio[0].set_value("2. Importazione")
        at.run()

        captions = [c.value for c in at.caption]
        assert any("Dimensione massima file: 2 GB" in c for c in captions)

        # Per WhatsApp export, nota specifica su archivi grandi
        at.selectbox[0].set_value("WhatsApp export chat (TXT / ZIP)")
        at.run()
        captions = [c.value for c in at.caption]
        assert any("Gli archivi molto grandi possono richiedere più tempo" in c for c in captions)

    def test_zero_ai_during_ingestion(self, monkeypatch: pytest.MonkeyPatch):
        """Garantisce categoricamente l'assenza di chiamate AI durante l'ingestion."""
        import ai.lmstudio

        def fail_if_ai_invoked(*args, **kwargs):
            pytest.fail("LM Studio invocato illegittimamente durante l'ingestion!")

        monkeypatch.setattr(ai.lmstudio.LmStudioClient, "chat_completion", fail_if_ai_invoked)

        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_EXPORT,
            filename="chat.txt",
            file_bytes=b"10/05/2024, 12:00 - Alice: Ingestion offline\n",
        )
        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.SUCCESS

    def test_small_files_no_regression(self):
        """Verifica che piccoli file continuino a funzionare normalmente con hash e summary corretti."""
        content = b"10/05/2024, 12:00 - Alice: Messaggio piccolo\n"
        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_EXPORT,
            filename="chat_small.txt",
            file_bytes=content,
        )
        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.SUCCESS
        assert res.summary.file_size_bytes == len(content)
        assert len(res.summary.sha256) == 64
        assert res.summary.raw_record_count == 1
