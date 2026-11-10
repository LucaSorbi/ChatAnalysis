"""
tests/unit/test_whatsapp_strict_encoding.py
-------------------------------------------
Test di integrità forense per WhatsAppExportImporter:
- Criterio 31, 33: Nessun errors="replace" sui dati forensi (nessun U+FFFD silenzioso).
- Criterio 31: Sequenze di byte non decodificabili sollevano un ValueError controllato.
- Criterio 32: Streaming riga per riga preservato, nessun buffer globale dell'intero file in memoria.
"""
from __future__ import annotations

import io
from pathlib import Path
import pytest

from importer.whatsapp_export import WhatsAppExportImporter


@pytest.mark.unit
def test_invalid_utf8_bytes_raises_controlled_error(tmp_path: Path):
    """Verifica che un file TXT con byte non validi sollevi ValueError e non mascheri con replacement char."""
    invalid_file = tmp_path / "chat_corrupt.txt"
    # Byte caotici non decodificabili
    invalid_file.write_bytes(b"\x80\x81\x82\x83\xff\xfe\xca\xfe\xba\xbe")

    importer = WhatsAppExportImporter()
    with pytest.raises(ValueError) as exc_info:
        list(importer.import_records(invalid_file))

    assert "non presenta la struttura" in str(exc_info.value) or "decodificare" in str(exc_info.value)


@pytest.mark.unit
def test_invalid_utf8_mid_stream_raises_controlled_error(tmp_path: Path):
    """Verifica che un errore di decodifica durante lo streaming del file sollevi ValueError forense controllato."""
    chat_file = tmp_path / "chat_mid_corrupt.txt"
    # Campione iniziale >64KB di puro testo valido UTF-8 per determinare l'encoding UTF-8
    valid_lines = [
        f"12/03/2024, 10:15 - Utente: Messaggio lungo di test forense con testo valido #{i}\n".encode("utf-8")
        for i in range(1200)
    ]
    # Poi aggiungiamo una riga con byte esplicitamente invalidi in UTF-8
    corrupt_line = b"12/03/2024, 10:16 - Attaccante: byte corrotto \x80\x81 fine.\n"
    chat_file.write_bytes(b"".join(valid_lines) + corrupt_line)

    importer = WhatsAppExportImporter()
    with pytest.raises(ValueError) as exc_info:
        # Consuma il generatore streaming
        list(importer.import_records(chat_file))

    assert "Errore di decodifica forense" in str(exc_info.value)
    assert "utf-8" in str(exc_info.value)


@pytest.mark.unit
def test_valid_utf8_with_bom_supported(tmp_path: Path):
    """Verifica che un file UTF-8 con BOM sia decodificato correttamente senza esporre il carattere BOM."""
    chat_file = tmp_path / "chat_bom.txt"
    content = "\ufeff12/03/2024, 10:15 - Alice: Primo messaggio con BOM\n12/03/2024, 10:16 - Bob: Risposta\n"
    chat_file.write_text(content, encoding="utf-8-sig")

    importer = WhatsAppExportImporter()
    records = list(importer.import_records(chat_file))
    assert len(records) == 2
    assert "\ufeff" not in records[0].raw_fields["sender"]
    assert records[0].raw_fields["sender"] == "Alice"
    assert records[0].raw_fields["text"] == "Primo messaggio con BOM"


@pytest.mark.unit
def test_line_by_line_streaming_generator(tmp_path: Path):
    """Verifica che import_records sia un generatore che produce record sequenzialmente senza caricare tutto in memoria."""
    chat_file = tmp_path / "chat_stream.txt"
    lines = [
        f"12/03/2024, 10:{i:02d} - User{i}: Messaggio {i}\n"
        for i in range(10)
    ]
    chat_file.write_text("".join(lines), encoding="utf-8")

    importer = WhatsAppExportImporter()
    stream = importer.import_records(chat_file)
    assert iter(stream) is stream  # Deve essere un iterator / generator

    first_record = next(stream)
    assert first_record.raw_fields["sender"] == "User0"
    assert first_record.raw_fields["text"] == "Messaggio 0"

    # Consuma il resto
    remaining = list(stream)
    assert len(remaining) == 9
