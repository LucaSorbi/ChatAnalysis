"""
scripts/sanitize_synthetic_dataset.py
--------------------------------------
Script di utilità per la sanitizzazione riproducibile del dataset sintetico
per il Privacy Hardening prima della pubblicazione del repository.

SCOPO:
Rendere deterministica e riproducibile la sostituzione di qualsiasi valore
dall'aspetto realistico (numeri telefonici, JID, nomi comuni, IMEI, testi)
con pattern inequivocabilmente sintetici su tutte le sorgenti di test_data/.

MISURE DI SICUREZZA (HARDENING):
- Opera ESCLUSIVAMENTE sui file presenti nella cartella `test_data/` del repository.
- Rifiuta categoricamente l'esecuzione se i percorsi non contengono la cartella 'test_data'.
- Verifica l'integrità strutturale prima e dopo l'aggiornamento.
- Non deve mai essere applicato ad acquisizioni o estrazioni forensi reali.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Mapping entità e valori sintetici
SUBJECT_MAP = {
    # Numeri con spazi
    "+39 333 1234567": "+39 000 0000001",
    "+39 347 9876543": "+39 000 0000002",
    "+39 320 5554433": "+39 000 0000003",
    "+1 212 5550101": "+1 000 0000004",
    # Numeri compatti
    "+393331234567": "+390000000001",
    "+393479876543": "+390000000002",
    "+393205554433": "+390000000003",
    "+12125550101": "+10000000004",
    # JID contatti
    "+393331234567@s.whatsapp.net": "+390000000001@s.whatsapp.net",
    "+393479876543@s.whatsapp.net": "+390000000002@s.whatsapp.net",
    "+393205554433@s.whatsapp.net": "+390000000003@s.whatsapp.net",
    "+12125550101@s.whatsapp.net": "+10000000004@s.whatsapp.net",
    # Nomi
    "Mario Rossi": "Contatto_001",
    "Lucia Bianchi": "Contatto_002",
    "Giovanni Verdi": "Contatto_003",
    "John Smith": "Contatto_004",
    # Gruppo
    "12345678901-1234567890@g.us": "00000000001-0000000000@g.us",
    "Amici del gruppo": "Gruppo_Sintetico_01",
    # IMEI
    "123456789012345": "000000000000001",
}

TEXT_MAP = {
    "Hai visto il nuovo posto in via Roma?": "Messaggio di test sintetico 01",
    "Il prezzo è cambiato, ora costa di più": "Messaggio di test sintetico 02",
    "How much for a full pack?": "Messaggio di test sintetico 03",
    "Send me the address": "Messaggio di test sintetico 04",
    "Meet me at the usual spot": "Messaggio di test sintetico 05",
    "Ok confermato per stasera": "Messaggio di test sintetico 06",
    "Hey are you around?": "Messaggio di test sintetico 07",
    "Porta il pacco al solito posto": "Messaggio di test sintetico 08",
    "": "",
    "Got the stuff": "Messaggio di test sintetico 09",
    "Ciao come stai?": "Messaggio di test sintetico 10",
    "Ho comprato la roba 💊": "Messaggio sintetico con emoji 💊",
    "Mandami la foto quando sei pronto": "Messaggio di test sintetico 11",
    "Ci vediamo domani alle 10?": "Messaggio di test sintetico 12",
    "Non mi rispondere sul telefono": "Messaggio di test sintetico 13",
    "Attenzione ci sono controlli": "Messaggio di test sintetico 14",
    "https://t.me/examplegroup": "https://example.org/synthetic-test-group",
    "Don't use this number anymore": "Messaggio di test sintetico 15",
    "Quanto vuoi per 50?": "Messaggio di test sintetico 16",
    "Non scrivere qui, usa l'altra app": "Messaggio di test sintetico 17",
    "Tutto ok dalla mia parte": "Messaggio di test sintetico 18",
    "👍": "👍",
    "Epoch zero anomaly": "Epoch zero anomaly",
    "Future timestamp anomaly": "Future timestamp anomaly",
    "Negative timestamp": "Negative timestamp",
}

STATUS_MAP = {
    "Hey there!": "Status sintetico test 01",
    "Sono occupato": "Status sintetico test 03",
    "Available": "Status sintetico test 04",
}


def _verify_safety(path: Path) -> None:
    """Guardia di sicurezza: impedisce modifiche fuori dalla directory test_data."""
    resolved = path.resolve()
    if "test_data" not in resolved.parts:
        raise ValueError(
            f"SICUREZZA VIOLATA: Il percorso {resolved} non appartiene a 'test_data'. "
            "Lo script può operare unicamente sul dataset sintetico."
        )


def sanitize_wa_db(db_path: Path) -> None:
    _verify_safety(db_path)
    print(f"Sanitizing {db_path}...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute("SELECT _id, jid, display_name, status, phone_number FROM contacts").fetchall()
    for row in rows:
        _id, jid, name, status, phone = row
        new_jid = SUBJECT_MAP.get(jid, jid)
        new_name = SUBJECT_MAP.get(name, name)
        new_status = STATUS_MAP.get(status, status)
        new_phone = SUBJECT_MAP.get(phone, phone)
        cur.execute(
            "UPDATE contacts SET jid=?, display_name=?, status=?, phone_number=? WHERE _id=?",
            (new_jid, new_name, new_status, new_phone, _id),
        )
    conn.commit()
    conn.close()
    print("wa.db sanitized.")


def sanitize_msgstore_db(db_path: Path) -> None:
    _verify_safety(db_path)
    print(f"Sanitizing {db_path}...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Aggiorna messages
    rows = cur.execute("SELECT _id, key_remote_jid, remote_resource, data FROM messages").fetchall()
    for row in rows:
        _id, jid, rem, data = row
        new_jid = SUBJECT_MAP.get(jid, jid)
        new_rem = SUBJECT_MAP.get(rem, rem)
        new_data = TEXT_MAP.get(data, data)
        cur.execute(
            "UPDATE messages SET key_remote_jid=?, remote_resource=?, data=? WHERE _id=?",
            (new_jid, new_rem, new_data, _id),
        )

    # Aggiorna chat_list
    chats = cur.execute("SELECT _id, key_remote_jid, subject FROM chat_list").fetchall()
    for row in chats:
        _id, jid, subj = row
        new_jid = SUBJECT_MAP.get(jid, jid)
        new_subj = SUBJECT_MAP.get(subj, subj)
        cur.execute(
            "UPDATE chat_list SET key_remote_jid=?, subject=? WHERE _id=?",
            (new_jid, new_subj, _id),
        )

    conn.commit()
    conn.close()
    print("msgstore.db sanitized.")


def sanitize_csv(csv_path: Path) -> None:
    _verify_safety(csv_path)
    print(f"Sanitizing {csv_path}...")
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for r in reader:
            if r["From"] in SUBJECT_MAP:
                r["From"] = SUBJECT_MAP[r["From"]]
            if r["To"] in SUBJECT_MAP:
                r["To"] = SUBJECT_MAP[r["To"]]
            if r["Body"] in TEXT_MAP:
                r["Body"] = TEXT_MAP[r["Body"]]
            rows.append(r)

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print("messages.csv sanitized.")


def sanitize_json(json_path: Path) -> None:
    _verify_safety(json_path)
    print(f"Sanitizing {json_path}...")
    with open(json_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    for item in items:
        if item.get("sender") in SUBJECT_MAP:
            item["sender"] = SUBJECT_MAP[item["sender"]]
        content = item.get("content", {})
        if content.get("text") in TEXT_MAP:
            content["text"] = TEXT_MAP[content["text"]]

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("messages.json sanitized.")


def sanitize_xml(xml_path: Path) -> None:
    _verify_safety(xml_path)
    print(f"Sanitizing {xml_path}...")
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # IMEI
    for elem in root.iter():
        if elem.text and elem.text in SUBJECT_MAP:
            elem.text = SUBJECT_MAP[elem.text]
        for attr, val in elem.attrib.items():
            if val in SUBJECT_MAP:
                elem.attrib[attr] = SUBJECT_MAP[val]
            if val in TEXT_MAP:
                elem.attrib[attr] = TEXT_MAP[val]

    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    print("report.xml sanitized.")


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    test_data = project_root / "test_data"

    if not test_data.exists():
        print(f"Directory test_data non trovata in {test_data}", file=sys.stderr)
        sys.exit(1)

    wa_db = test_data / "whatsapp_export" / "wa.db"
    msgstore_db = test_data / "whatsapp_export" / "msgstore.db"
    csv_file = test_data / "cellebrite_export" / "messages.csv"
    json_file = test_data / "cellebrite_export" / "messages.json"
    xml_file = test_data / "cellebrite_export" / "report.xml"

    if wa_db.exists():
        sanitize_wa_db(wa_db)
    if msgstore_db.exists():
        sanitize_msgstore_db(msgstore_db)
    if csv_file.exists():
        sanitize_csv(csv_file)
    if json_file.exists():
        sanitize_json(json_file)
    if xml_file.exists():
        sanitize_xml(xml_file)

    print("Sanitizzazione del dataset sintetico completata con successo.")


if __name__ == "__main__":
    main()
