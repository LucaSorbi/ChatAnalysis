"""
tests/fixtures/sample_records.py
----------------------------------
Factory di RawRecord sintetici per i test.

PRIVACY: tutti i dati qui presenti sono completamente inventati.
- Nessun numero di telefono reale
- Nessun JID reale
- Nessun IMEI reale
- Nessun nome reale
- Nessun testo reale di chat
- Nessun dato forense reale

I nomi (Alice, Bob, Carol) e i numeri (+39 000 0000001 ecc.) sono
placeholder riconoscibili come sintetici.
"""
from __future__ import annotations

from importer.models import RawRecord


# ---------------------------------------------------------------------------
# Record sintetici rappresentativi di ciascuna sorgente
# ---------------------------------------------------------------------------

def make_sqlite_message_record(
    record_id: str = "1",
    key_from_me: int = 0,
    timestamp: int = 1704067200000,  # 2024-01-01 00:00:00 UTC (unix_ms)
    data: str | None = "Messaggio di testo sintetico",
    media_wa_type: int = 0,
    media_url: str | None = None,
    source_path: str = "/synthetic/msgstore.db",
) -> RawRecord:
    """
    Simula un record della tabella `messages` di msgstore.db (WhatsApp nativo).
    I valori sono sintetici e non corrispondono a dati reali.
    """
    return RawRecord(
        source_name="msgstore_db",
        source_path=source_path,
        source_record_id=record_id,
        record_type="message",
        raw_fields={
            "_id": int(record_id),
            "key_remote_jid": "chat_synthetic_001@s.whatsapp.net",  # JID sintetico
            "key_from_me": key_from_me,
            "key_id": f"KEY_{record_id.zfill(6)}",
            "status": 5,
            "data": data,
            "timestamp": timestamp,
            "media_wa_type": media_wa_type,
            "media_url": media_url,
            "deleted": 0,
            "forwarded": 0,
            "starred": 0,
            "quoted_row_id": None,
        },
        media_reference=media_url,
        metadata={
            "table": "messages",
            "importer_version": "0.1.0",
        },
    )


def make_csv_message_record(
    record_id: str = "row_1",
    direction: str = "Incoming",
    timestamp_str: str = "2024-01-02 10:00:00",
    body: str | None = "Messaggio CSV sintetico",
    deleted: str = "False",
    source_path: str = "/synthetic/messages.csv",
) -> RawRecord:
    """
    Simula un record del CSV Cellebrite.
    I valori sono sintetici: nomi, numeri e testi inventati.
    """
    return RawRecord(
        source_name="cellebrite_csv",
        source_path=source_path,
        source_record_id=record_id,
        record_type="message",
        raw_fields={
            "Source": "WhatsApp",
            "MessageType": "Text",
            "TimeStamp": timestamp_str,
            "Direction": direction,
            "From": "+39 000 0000001",   # numero sintetico
            "To": "+39 000 0000002",     # numero sintetico
            "Body": body,
            "Attachments": None,
            "Status": "Delivered",
            "Deleted": deleted,
            "Forwarded": "False",
            "ApplicationId": "com.whatsapp",
            "ChatId": "chat_synthetic_1",
        },
        media_reference=None,
        metadata={
            "row_number": 1,
            "encoding": "utf-8",
            "delimiter": ",",
        },
    )


def make_json_message_record(
    record_id: str = "msg_00001",
    sender: str = "+39 000 0000001",  # numero sintetico
    timestamp_iso: str = "2024-01-03T10:00:00+00:00",
    content_text: str = "Messaggio JSON sintetico",
    msg_type: str = "text",
    source_path: str = "/synthetic/messages.json",
) -> RawRecord:
    """
    Simula un record del JSON Cellebrite.
    """
    return RawRecord(
        source_name="cellebrite_json",
        source_path=source_path,
        source_record_id=record_id,
        record_type="message",
        raw_fields={
            "id": record_id,
            "chat_id": "chat_synthetic_1",
            "sender": sender,
            "timestamp": timestamp_iso,
            "type": msg_type,
            "content": {
                "text": content_text,
                "media_path": None,
            },
            "metadata": {
                "deleted": False,
                "forwarded": False,
                "starred": False,
            },
        },
        media_reference=None,
        metadata={
            "array_index": 0,
            "format": "json_array",
        },
    )


def make_xml_message_record(
    record_id: str = "0",
    sender: str = "+39 000 0000001",  # numero sintetico
    timestamp_iso: str = "2024-01-01T12:00:00+00:00",
    body: str | None = "Messaggio XML sintetico",
    deleted: str = "false",
    source_path: str = "/synthetic/report.xml",
) -> RawRecord:
    """
    Simula un record del report XML UFDR Cellebrite.
    """
    return RawRecord(
        source_name="cellebrite_xml",
        source_path=source_path,
        source_record_id=record_id,
        record_type="message",
        raw_fields={
            "id": record_id,
            "Timestamp": timestamp_iso,
            "Sender": sender,
            "Body": body,
            "Deleted": deleted,
        },
        media_reference=None,
        metadata={
            "xml_tag": "Message",
            "element_index": int(record_id),
        },
    )


def make_media_record(
    record_id: str = "1",
    media_type: int = 1,
    file_path: str = "WhatsApp Images/IMG_synth_001.jpg",  # path sintetico
    source_path: str = "/synthetic/msgstore.db",
) -> RawRecord:
    """
    Simula un record della tabella `media_refs` di msgstore.db.
    """
    return RawRecord(
        source_name="msgstore_db",
        source_path=source_path,
        source_record_id=record_id,
        record_type="media_ref",
        raw_fields={
            "_id": int(record_id),
            "message_row_id": 10,
            "file_path": file_path,
            "file_size": 12345,
            "media_type": media_type,
            "media_job_uuid": "00000000-0000-0000-0000-000000000001",
        },
        media_reference=file_path,
        metadata={
            "table": "media_refs",
        },
    )


def make_contact_record(
    record_id: str = "1",
    display_name: str = "Alice Sintetica",   # nome inventato
    phone_number: str = "+39 000 0000001",   # numero sintetico
    source_path: str = "/synthetic/wa.db",
) -> RawRecord:
    """
    Simula un record della tabella `contacts` di wa.db.
    """
    return RawRecord(
        source_name="msgstore_db",
        source_path=source_path,
        source_record_id=record_id,
        record_type="contact",
        raw_fields={
            "_id": int(record_id),
            "jid": "chat_synthetic_001@s.whatsapp.net",
            "display_name": display_name,
            "status": None,
            "phone_number": phone_number,
        },
        media_reference=None,
        metadata={
            "table": "contacts",
            "db_file": "wa.db",
        },
    )
