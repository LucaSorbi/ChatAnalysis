# Data Analysis — Analisi Semantica dei Dati Forensi

**Fase**: 2 — Analisi semantica (sola lettura)
**Data**: 2026-09-09
**Basato su**: `data_profile.md` + ispezione diretta delle sorgenti
**Stato**: COMPLETO — nessun dato originale modificato

---

## 1. Fonti Individuate

Il campione è composto da **due directory di provenienza** con struttura e origine distinte.

### 1.1 Struttura delle sorgenti

| Sorgente | File | Provenienza | Record |
|---|---|---|---|
| `whatsapp_export/msgstore.db` | SQLite (100 KB) | Backup diretto WhatsApp | 504 messaggi |
| `whatsapp_export/wa.db` | SQLite (12 KB) | Backup diretto WhatsApp | 4 contatti |
| `cellebrite_export/messages.csv` | CSV UTF-8 (41 KB) | Esportazione Cellebrite UFED | 302 messaggi |
| `cellebrite_export/messages.json` | JSON array (35 KB) | Esportazione Cellebrite | 100 messaggi |
| `cellebrite_export/report.xml` | XML UFDR (10 KB) | Esportazione Cellebrite (report ufficiale) | 50 messaggi |
| `whatsapp_export/WhatsApp Audio/` | 10 file `.opus` | Estratti da backup WhatsApp | — |
| `whatsapp_export/WhatsApp Images/` | 20 file `.jpg` | Estratti da backup WhatsApp | — |
| `whatsapp_export/WhatsApp Video/` | 5 file `.mp4` | Estratti da backup WhatsApp | — |

### 1.2 Campi semantici per sorgente

#### `msgstore.db` — tabella `messages`

| Campo semantico | Campo DB | Note |
|---|---|---|
| Identificativo messaggio | `_id` (INT autoincrement), `key_id` (TEXT) | `key_id` è l'ID globale WhatsApp; `_id` è locale al device |
| Chat/conversazione | `key_remote_jid` | JID WhatsApp: `+39xxx@s.whatsapp.net` (privata) o `xxx@g.us` (gruppo) |
| Mittente | `key_remote_jid` + `key_from_me` | `key_from_me=1` = inviato dal device; `key_from_me=0` = ricevuto |
| Mittente gruppo | `remote_resource` | JID del partecipante in chat di gruppo (null per chat private) |
| Destinatario | Implicito: chat + `key_from_me` | Non esiste un campo destinatario esplicito |
| Timestamp | `timestamp` (unix_ms) | Timestamp di creazione/invio del messaggio |
| Timestamp ricezione | `received_timestamp` (unix_ms) | Momento in cui il device ha ricevuto il messaggio (con ms) |
| Testo | `data` (TEXT) | Null per messaggi media-only |
| Tipo messaggio | `media_wa_type` (INT) | 0=testo, 1=immagine, 2=audio, 3=video |
| Stato | `status` (INT) | Solo valore osservato: 5 (consegnato/letto) |
| Eliminato | `deleted` (INT 0/1) | Flag: 14 messaggi eliminati nel campione |
| Inoltrato | `forwarded` (INT 0/1) | Flag: 25 messaggi inoltrati |
| Citato/reply | `quoted_row_id` (INT) | Riferimento a `_id` del messaggio originale — sempre null nel campione |
| Riferimento media | `media_url` (TEXT) | Path relativo al file media (es. `WhatsApp Images/IMG_00003.jpg`) |
| MIME media | `media_mime_type` (TEXT) | `image/jpeg`, `audio/ogg; codecs=opus`, `video/mp4` |
| Dimensione media | `media_size` (INT) | Byte (come da server WhatsApp, NON dimensione file su disco) |
| Caption media | `media_caption` (TEXT) | Sempre null nel campione |
| Stella | `starred` (INT 0/1) | 8 messaggi con stella |
| Menzionati | `mentioned_jids` (TEXT) | Sempre null nel campione |
| Versione edit | `edit_version` (INT) | Sempre 0 nel campione |

#### `msgstore.db` — tabella `chat_list`

| Campo semantico | Campo DB | Note |
|---|---|---|
| ID chat | `_id` | Chiave primaria locale |
| JID chat | `key_remote_jid` | Corrisponde a `messages.key_remote_jid` |
| Ultimo messaggio | `message_table_id` | FK verso `messages._id` dell'ultimo messaggio |
| Nome gruppo | `subject` | Non null solo per chat di gruppo (1 su 3 nel campione) |
| Data creazione | `creation` | Unix timestamp ms; stesso valore per tutte le chat (anomalia campione) |

#### `wa.db` — tabella `contacts`

| Campo semantico | Campo DB | Note |
|---|---|---|
| ID contatto | `_id` | Locale |
| JID | `jid` | Corrisponde a `messages.key_remote_jid` — chiave di join |
| Nome visualizzato | `display_name` | Nome in rubrica del device |
| Status WhatsApp | `status` | Testo dello stato WhatsApp (25% null) |
| Numero telefonico | `phone_number` | Formato internazionale |

4 contatti nel campione: Contatto_001, Contatto_002, Contatto_003, Contatto_004

#### `cellebrite_export/messages.csv`

| Campo semantico | Campo CSV | Note |
|---|---|---|
| Sorgente app | `Source` | Sempre `WhatsApp` |
| Tipo messaggio | `MessageType` | `Text` (277), `Image` (25) |
| Timestamp | `TimeStamp` | Formato `YYYY-MM-DD HH:MM:SS` — senza timezone |
| Direzione | `Direction` | `Outgoing` (159) / `Incoming` (143) |
| Mittente | `From` | Numero internazionale o alias (`group_participant_A`) |
| Destinatario | `To` | Numero internazionale o alias |
| Testo | `Body` | 21.5% null (messaggi media) |
| Allegato | `Attachments` | Path stile Cellebrite `Files\IMG_XXXX.jpg` (8.3% presenti) |
| Stato | `Status` | `Delivered`, `Received`, `Sent`, `Read` |
| Eliminato | `Deleted` | `True`/`False` — 69 messaggi eliminati (22.8%) |
| Inoltrato | `Forwarded` | `True`/`False` — 105 messaggi inoltrati (34.8%) |
| App ID | `ApplicationId` | Sempre `com.whatsapp` |
| ID chat | `ChatId` | `chat_1` (104), `chat_2` (107), `chat_3` (91) — opachi |

#### `cellebrite_export/messages.json`

| Campo semantico | Campo JSON | Note |
|---|---|---|
| Identificativo | `id` | Formato `msg_NNNNN` |
| Chat | `chat_id` | `chat_1`/`chat_2`/`chat_3` — opachi (non JID) |
| Mittente | `sender` | Numero internazionale o alias |
| Timestamp | `timestamp` | ISO 8601 con timezone (`+00:00`) |
| Tipo | `type` | `text` (58), `image` (28), `audio` (14) |
| Testo | `content.text` | Sempre presente (anche per media) |
| Path media | `content.media_path` | Sempre null nel campione |
| Eliminato | `metadata.deleted` | Boolean — 3 messaggi eliminati |
| Inoltrato | `metadata.forwarded` | Boolean |
| Stella | `metadata.starred` | Boolean |

#### `cellebrite_export/report.xml` (UFDR)

| Campo semantico | Tag/Attributo XML | Note |
|---|---|---|
| Identificativo | `Message[id]` (attributo) | Intero progressivo 0-49 |
| Timestamp | `<Timestamp>` | ISO 8601 con timezone (`+00:00`) |
| Mittente | `<Sender>` | Numero internazionale o alias |
| Testo | `<Body>` | Null per messaggi media (nessun tag per allegati) |
| Eliminato | `<Deleted>` | `true`/`false` — 1 messaggio eliminato |
| Info dispositivo | `<DeviceInfo>` | Contiene `<Model>`, `<OS>`, `<IMEI>` |

> [!CAUTION]
> Il tag `<IMEI>` in `report.xml` contiene il numero IMEI del dispositivo.
> Questo campo **non deve mai essere incluso nei report** né nei log di analisi.

---

## 2. Confronto tra Fonti

### 2.1 Conclusione: Dataset parzialmente sovrapposti (caso C)

Le quattro sorgenti strutturate non rappresentano la stessa estrazione.
La verifica per sovrapposizione di timestamp (risoluzione al secondo, UTC) ha prodotto:

| Coppia | Overlap timestamp |
|---|---|
| DB ∩ CSV | 0 |
| DB ∩ JSON | 0 |
| DB ∩ XML | 0 |
| CSV ∩ JSON | 0 |
| CSV ∩ XML | 0 |
| JSON ∩ XML | 0 |

**Nessun timestamp condiviso** tra le quattro sorgenti su scala al secondo.
Le finestre temporali si sovrappongono per intervallo (2024-2025) ma i valori puntuali non coincidono.

### 2.2 Caratterizzazione delle sorgenti

| Sorgente | Origine | Intervallo temporale | Record |
|---|---|---|---|
| `msgstore.db` | Backup WhatsApp nativo (Android) | 2024-01-01 → 2035-01-01* | 504 |
| `messages.csv` | Cellebrite UFED — estrazione fisica/logica | 2024-01-02 → 2025-06-28 | 302 |
| `messages.json` | Cellebrite — formato strutturato alternativo | 2024-01-03 → 2025-06-27 | 100 |
| `report.xml` | Cellebrite — report UFDR ufficiale | 2024-01-01 → 2025-06-23 | 50 |

*Include anomalie: timestamp futuro 2035-01-01, timestamp zero, timestamp negativo.

### 2.3 Relazione tra le tre sorgenti Cellebrite

CSV, JSON e XML condividono gli stessi chat ID (`chat_1`, `chat_2`, `chat_3`) e partecipanti,
ma il numero di record differisce radicalmente (302 vs 100 vs 50).
Sono **estrazioni diverse dello stesso dataset Cellebrite**:

- **CSV**: estrazione completa (302 record) con tutti i campi
- **JSON**: sottoinsieme strutturato (100 record) — probabile export selettivo
- **XML (UFDR)**: report formale ridotto (50 record) — messaggi selezionati dall'analista

### 2.4 Relazione tra Cellebrite e msgstore.db

`msgstore.db` è una fonte **completamente indipendente** da Cellebrite.
Cellebrite ha estratto i dati dal device e li ha re-esportati nel proprio formato.
Il DB nativo ha 504 record vs 302 del CSV Cellebrite — contiene più messaggi.

> [!IMPORTANT]
> Le sorgenti Cellebrite e il backup WhatsApp nativo non possono essere collegati
> tramite timestamp o ID senza un campo chiave comune.
> Il campo candidato più promettente è il **numero di telefono normalizzato**.

### 2.5 Possibili chiavi di collegamento

| Campo | DB (msgstore) | CSV (Cellebrite) | JSON (Cellebrite) | XML (Cellebrite) |
|---|---|---|---|---|
| ID univoco | `key_id` (KEY_000010) | assente | `id` (msg_NNNNN) | attributo `id` (0-49) |
| Numero telefono | `key_remote_jid` (JID) | `From`/`To` (numero) | `sender` (numero) | `Sender` (numero) |
| Chat | `key_remote_jid` | `ChatId` (opaco) | `chat_id` (opaco) | assente |
| Timestamp | unix_ms | stringa senza TZ | ISO 8601+TZ | ISO 8601+TZ |

**Strategia di linkage consigliata** (futura implementazione):

1. Normalizzare tutti i numeri di telefono in formato E.164
2. Normalizzare tutti i timestamp in UTC ISO 8601 con millisecondo
3. Usare `(sender_normalized, timestamp_utc_sec, body_hash)` come fingerprint
4. Linkage probabilistico per record senza corrispondenza esatta

---

## 3. Analisi dei Duplicati

### 3.1 Duplicati in `msgstore.db`

**1 gruppo** di duplicati identificato:

| Campo | Riga A | Riga B |
|---|---|---|
| `_id` | 11 | 501 |
| `key_id` | KEY_000010 | KEY_000010 |
| Tutti gli altri campi | identici | identici |

- **Tipo**: duplicato esatto (stesso `key_id`, stesso contenuto, stesso timestamp)
- **Campo differente**: solo `_id` (autoincrement locale)
- **Ipotesi**: il record è stato reinserito durante un ripristino del backup

### 3.2 Duplicati in `messages.csv`

**1 gruppo** di duplicati identificato:

| Campo | Riga 5 | Riga 300 |
|---|---|---|
| TimeStamp | 2025-05-16 07:04:58 | 2025-05-16 07:04:58 |
| Body | Ci vediamo domani alle 10? | Ci vediamo domani alle 10? |
| Tutti i 13 campi | identici | identici |

- **Tipo**: duplicato esatto
- **Ipotesi**: errore di export Cellebrite

### 3.3 Duplicati in `messages.json` e `report.xml`

Nessun duplicato rilevato.

### 3.4 Strategia futura di deduplicazione (proposta, non implementare ora)

1. **Per `msgstore.db`**: deduplicare su `key_id`; tenere il record con `_id` minore
2. **Per CSV**: deduplicare su fingerprint `(TimeStamp, From, To, Body, ChatId)`; tenere il primo
3. **Per JSON e XML**: nessuna azione necessaria al momento
4. **Cross-source**: richiede prima la normalizzazione delle chiavi

---

## 4. Analisi dei Media

### 4.1 Schema di identificazione

I media sono identificati da due meccanismi paralleli nel backup WhatsApp nativo:

```
messages._id
    |
    +-- messages.media_url       -> path relativo (es. WhatsApp Images/IMG_00003.jpg)
    |
    +-- media_refs.message_row_id (FK -> messages._id)
            |
            +-- media_refs.file_path      -> stesso path di media_url (ridondante)
            +-- media_refs.file_size      -> dimensione attesa (dal server WhatsApp)
            +-- media_refs.media_type     -> 1=immagine, 2=audio, 3=video
            +-- media_refs.media_job_uuid -> UUID del job di download
```

**Osservazione chiave**: `messages.media_url` e `media_refs.file_path` contengono
esattamente lo stesso valore per ogni messaggio con media.
La tabella `media_refs` è ridondante per il path ma aggiunge `file_size` e `media_job_uuid`.

### 4.2 Mapping DB refs vs file su disco

| Categoria | Numero |
|---|---|
| Riferimenti in `media_refs` | 115 |
| File su disco (totale) | 35 (10 audio + 20 immagini + 5 video) |
| Riferimenti DB con file corrispondente su disco | 2 |
| Riferimenti DB senza file su disco | 113 |
| File su disco non presenti in `media_refs` | 33 |

> [!WARNING]
> Solo 2 file su 115 referenziati sono presenti fisicamente su disco.
> Questa è un'incongruenza del campione sintetico.
> In un caso reale `media_url` deve puntare a un file esistente su disco.
> I file presenti (IMG_00000-IMG_00019, AUD_00000-AUD_00009, VID_00000-VID_00004)
> hanno una numerazione diversa dai file referenziati nel DB
> (IMG_00003, IMG_00012, IMG_00025...).

### 4.3 Schema di identificazione media Cellebrite (CSV)

Il CSV Cellebrite usa una convenzione di path completamente diversa:

```
Cellebrite: Files\IMG_0012.jpg
DB nativo:  WhatsApp Images/IMG_00012.jpg
```

- **Differenza directory**: `Files\` vs `WhatsApp Images/`
- **Differenza nome**: `IMG_0012` vs `IMG_00012` (padding diverso)
- I 25 riferimenti in `Attachments` del CSV non sono mappabili direttamente ai file su disco

Il JSON Cellebrite ha `content.media_path` sempre null nel campione.
Il report XML non contiene riferimenti media.

### 4.4 Tabella di mapping media

| Sorgente | Campo link | Valore esempio | Stato |
|---|---|---|---|
| `msgstore.db`.`messages` | `media_url` | `WhatsApp Images/IMG_00003.jpg` | 2/115 file presenti nel campione |
| `msgstore.db`.`media_refs` | `file_path` | `WhatsApp Images/IMG_00003.jpg` | Ridondante con `media_url` |
| `messages.csv` | `Attachments` | `Files\IMG_0012.jpg` | Schema diverso, nessun match diretto |
| `messages.json` | `content.media_path` | null | Sempre null nel campione |
| `report.xml` | — | — | Assente |

### 4.5 Relazioni di cardinalità

- **messages ↔ media_refs**: 1:1 (ogni riga in `media_refs` ha un `message_row_id` unico)
- **Un messaggio ↔ più file media**: non osservato nel campione
- **Un file media ↔ più messaggi**: non osservato nel campione

---

## 5. Analisi dei Timestamp

### 5.1 Campi timestamp per sorgente

#### `msgstore.db` — tabella `messages`

| Campo | Formato | Non-null | Note |
|---|---|---|---|
| `timestamp` | unix_ms (13 cifre) | 504 | Timestamp principale del messaggio |
| `received_timestamp` | unix_ms (13 cifre) | 504 | Timestamp di ricezione locale (con ms) |
| `send_timestamp` | unix_ms | 0 (sempre null) | Non valorizzato nel campione |
| `receipt_server_timestamp` | unix_ms | 0 (sempre null) | Non valorizzato |
| `receipt_device_timestamp` | unix_ms | 0 (sempre null) | Non valorizzato |
| `read_device_timestamp` | unix_ms | 0 (sempre null) | Non valorizzato |
| `played_device_timestamp` | unix_ms | 0 (sempre null) | Non valorizzato |

**Differenza `timestamp` vs `received_timestamp`**:
`timestamp` è arrotondato al secondo; `received_timestamp` include i millisecondi.
Esempio: `1704105579000` vs `1704105579574`.

#### `cellebrite_export/messages.csv`

| Campo | Formato | Timezone | Note |
|---|---|---|---|
| `TimeStamp` | `YYYY-MM-DD HH:MM:SS` | Assente | Ambiguo: UTC o ora locale del device |

#### `cellebrite_export/messages.json`

| Campo | Formato | Timezone | Note |
|---|---|---|---|
| `timestamp` | ISO 8601 | `+00:00` (UTC) | Sempre con timezone esplicita |

#### `cellebrite_export/report.xml`

| Campo | Formato | Timezone | Note |
|---|---|---|---|
| `<Timestamp>` | ISO 8601 | `+00:00` (UTC) | Sempre con timezone esplicita |

### 5.2 Anomalie timestamp rilevate (msgstore.db)

| `_id` | `timestamp` | Valore decodificato | Tipo anomalia |
|---|---|---|---|
| 502 | 0 | 1970-01-01 00:00:00 UTC | Zero epoch |
| 504 | -1000 | Prima dell'epoch | Negativo |
| n.d. | 2082758400000 | 2035-01-01 00:00:00 UTC | Timestamp futuro |

Totale anomalie: 3 su 504 (0.6%)

### 5.3 Strategia di normalizzazione futura (proposta, non implementare ora)

1. **Campo principale**: `messages.timestamp` (unix_ms) come timestamp canonico
2. **Conversione**: dividere per 1000 per ottenere Unix seconds, poi `datetime` UTC
3. **Timezone**: normalizzare sempre in UTC; per CSV assumere UTC (default sicuro per UFED)
4. **Anomalie**: timestamp ≤ 0 o > anno 2030 → marcare con flag `timestamp_anomaly=True`, non eliminare
5. **Campo per UnifiedMessage**: `timestamp_utc` (datetime aware UTC) + `timestamp_anomaly` (bool)
6. **Conservare**: `received_timestamp` come campo separato `received_at_utc`

---

## 6. Tipi di Messaggio

### 6.1 Tabella completa dei tipi osservati

| Tipo sorgente | Fonte | Valore | Significato | Testo disponibile | Media | Gestione futura |
|---|---|---|---|---|---|---|
| `media_wa_type=0` | DB | 0 | Messaggio testo | in `data` | no | Tipo `text` |
| `media_wa_type=1` | DB | 1 | Immagine | caption (null nel campione) | `.jpg` | Tipo `image` |
| `media_wa_type=2` | DB | 2 | Audio/Voicenote | no | `.opus` | Tipo `audio` |
| `media_wa_type=3` | DB | 3 | Video | no | `.mp4` | Tipo `video` |
| `MessageType=Text` | CSV | Text | Messaggio testo | in `Body` | no | Tipo `text` |
| `MessageType=Image` | CSV | Image | Immagine | body null | in `Attachments` | Tipo `image` |
| `type=text` | JSON | text | Messaggio testo | in `content.text` | no | Tipo `text` |
| `type=image` | JSON | image | Immagine | in `content.text` | media_path null | Tipo `image` |
| `type=audio` | JSON | audio | Audio | in `content.text` | media_path null | Tipo `audio` |
| XML implicito | XML | — | Solo testo | `<Body>` può essere null | nessun tag media | Tipo `text` o `unknown` |

### 6.2 Distribuzione per sorgente

| Tipo | DB (504) | CSV (302) | JSON (100) | XML (50) |
|---|---|---|---|---|
| Testo | 389 (77.2%) | 277 (91.7%) | 58 (58%) | ~50 (100%) |
| Immagine | 72 (14.3%) | 25 (8.3%) | 28 (28%) | — |
| Audio | 31 (6.2%) | — | 14 (14%) | — |
| Video | 12 (2.4%) | — | — | — |

### 6.3 Messaggi eliminati per sorgente

| Sorgente | Messaggi eliminati | Percentuale |
|---|---|---|
| DB (`msgstore.db`) | 14 / 504 | 2.8% |
| CSV (Cellebrite) | 69 / 302 | 22.8% |
| JSON (Cellebrite) | 3 / 100 | 3.0% |
| XML (Cellebrite) | 1 / 50 | 2.0% |

> [!IMPORTANT]
> Il CSV Cellebrite riporta 69 messaggi eliminati (22.8%), molto più degli altri.
> Questo suggerisce che Cellebrite recupera messaggi eliminati non presenti nel backup nativo,
> o usa una definizione diversa di "eliminato".
> Questa discrepanza è forensicamente rilevante e richiede investigazione.

---

## 7. Modello Dati — Proposta Preliminare di `UnifiedMessage`

> Modello concettuale basato sui dati osservati. Non implementare ancora.

### 7.1 Gerarchia degli oggetti

```
UnifiedMessage
    +-- Chat
    +-- Participant (sender)
    +-- Media (0..1)
    +-- SourceRecord (1..N)
```

### 7.2 Chat

```
Chat:
  unified_id:    str          # UUID generato
  chat_type:     enum         # "private" | "group"
  jid:           str | None   # JID WhatsApp (es. +390000000001@s.whatsapp.net)
  cellebrite_id: str | None   # ID opaco Cellebrite (es. "chat_1")
  subject:       str | None   # Nome gruppo (solo per chat di gruppo)
  participants:  List[Participant]
  created_at:    datetime | None
  sources:       List[str]
```

### 7.3 Participant

```
Participant:
  unified_id:      str
  jid:             str | None   # JID WhatsApp
  phone_e164:      str | None   # Numero E.164 normalizzato
  display_name:    str | None   # Da wa.db.contacts.display_name
  wa_status:       str | None   # Da wa.db.contacts.status
  is_device_owner: bool         # True se key_from_me=1 per i suoi messaggi
  alias:           str | None   # Per alias come "group_participant_A"
```

### 7.4 Media

```
Media:
  unified_id:      str
  media_type:      enum         # "image" | "audio" | "video" | "document"
  file_path_db:    str | None   # Path da msgstore.media_url / media_refs.file_path
  file_path_disk:  str | None   # Path assoluto del file su disco (se esiste)
  file_exists:     bool
  file_size_db:    int | None   # Dimensione attesa (dal DB WhatsApp)
  file_size_disk:  int | None   # Dimensione effettiva su disco
  mime_type:       str | None
  media_job_uuid:  str | None   # Da media_refs
  cellebrite_path: str | None   # Path Cellebrite (da CSV Attachments)
```

### 7.5 UnifiedMessage

```
UnifiedMessage:
  unified_id:             str
  message_type:           enum    # "text" | "image" | "audio" | "video" | "unknown"
  chat:                   Chat
  sender:                 Participant
  timestamp_utc:          datetime       # Timestamp canonico UTC
  received_at_utc:        datetime | None
  timestamp_anomaly:      bool
  text:                   str | None
  media:                  Media | None
  is_from_device_owner:   bool
  is_deleted:             bool
  is_forwarded:           bool
  is_starred:             bool
  quoted_message_id:      str | None     # ID del messaggio citato (reply)
  status:                 str | None     # "sent" | "delivered" | "read" | "received"
  edit_version:           int            # 0 = non modificato
  sources:                List[SourceRecord]
  # Campi futuri (sempre null nel campione attuale)
  mentioned_participants: List[Participant]
  location:               tuple | None   # (lat, lon)
  media_caption:          str | None
```

### 7.6 SourceRecord

```
SourceRecord:
  source_name: str    # "msgstore_db" | "cellebrite_csv" | "cellebrite_json" | "cellebrite_xml"
  source_path: str    # Path del file sorgente
  source_id:   str    # ID nativo (es. _id, msg_NNNNN, attributo id XML)
  raw_fields:  dict   # Dizionario dei campi originali (per tracciabilità)
  confidence:  float  # 0.0-1.0
```

### 7.7 Fonte più affidabile per campo

| Campo | Fonte raccomandata | Motivazione |
|---|---|---|
| Testo messaggio | `msgstore.db` (`data`) | Sorgente nativa, non rielaborata |
| Timestamp | `msgstore.db` (`timestamp`) | unix_ms, preciso, UTC implicito |
| Chat/conversazione | `msgstore.db` (`key_remote_jid`) | JID univoco e strutturato |
| Mittente | `msgstore.db` + `wa.db` join | JID + risoluzione nome da rubrica |
| Direzione | `msgstore.db` (`key_from_me`) | Flag booleano affidabile |
| Tipo media | `msgstore.db` (`media_wa_type`) | Codifica numerica consistente |
| Path media | `msgstore.db` (`media_url`) | Path relativo diretto |
| Messaggi eliminati | CSV Cellebrite (`Deleted=True`) | Cellebrite recupera più messaggi eliminati |
| Messaggi inoltrati | `msgstore.db` (`forwarded`) | Flag nativo WhatsApp |
| Nomi contatti | `wa.db` (`display_name`) | Unica sorgente con nomi leggibili |
| Numero telefono | `wa.db` (`phone_number`) | Formato internazionale già leggibile |

---

## 8. Privacy — Analisi del Profiler

### 8.1 Verifica del `data_profile.json` attuale

**Risultato**: 0 istanze di PII (numeri di telefono, JID, IMEI) trovate nel `data_profile.json`.

Il profiler non include `sample_values` nell'output strutturato.
I valori dei dati originali non vengono esportati nei report aggregati.

### 8.2 Punti di rischio residui

| Rischio | Dove | Gravità |
|---|---|---|
| Sample values se abilitati in futuro | `data_profile.json` | Alta |
| Nomi colonna rivelano formato JID | `data_profile.md` | Media |
| Tag IMEI visibile in `tag_frequency` XML | `data_profile.json` | Bassa (solo presenza, non valore) |
| Emoji campione (potenzialmente sensibili nel contesto) | `data_profile.md` sezione 7 | Bassa |

**`report.xml`**: il tag `<IMEI>` contiene il numero IMEI del dispositivo.
Il profiler attuale non espone il valore, ma rivela la presenza del tag.

### 8.3 Modifiche proposte al profiler (non implementare ora)

1. **Flag `--anonymize`** che:
   - Sostituisce JID con hash SHA-256 (8 caratteri hex)
   - Sostituisce numeri di telefono con token (`PHONE_001`)
   - Redige il valore IMEI come `[REDACTED]`
   - Omette `display_name` dalla descrizione colonne

2. **`--no-sample-text` come default sicuro** (attualmente `include_sample_text: True`)

3. **Redazione automatica** per pattern regex noti:
   - Numeri di telefono: `\+?\d{10,15}`
   - JID WhatsApp: `[\d+]+@[sg]\.(whatsapp\.net|us)`
   - IMEI: `\b\d{15}\b`
   - Codice fiscale IT: `[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]`

4. **Due livelli di output**:
   - `data_profile_full.json`: dati completi, accesso ristretto
   - `data_profile_public.md`: solo statistiche aggregate, sicuro per condivisione

---

## 9. Anomalie

| Anomalia | Fonte | Gravità | Note |
|---|---|---|---|
| 3 timestamp anomali (0, negativo, futuro 2035) | `msgstore.db` | Media | Probabili righe di sistema o errori di scrittura |
| 1 immagine 0 byte (`IMG_00019.jpg`) | Disco | Bassa | File corrotto o placeholder |
| `key_id` duplicato (`KEY_000010`) | `msgstore.db` | Media | Record identico con `_id` diverso |
| 1 riga CSV duplicata esatta | `messages.csv` | Bassa | Righe 5 e 300 identiche |
| 113/115 file referenziati non su disco | `media_refs` vs disco | Alta* | Artefatto campione sintetico |
| 33 file su disco non nel DB | Disco vs `media_refs` | Alta* | Artefatto campione sintetico |
| CSV Attachments con schema nome diverso | `messages.csv` | Media | `Files\IMG_0012.jpg` vs `WhatsApp Images/IMG_00012.jpg` |
| Timestamp secondari sempre null | `msgstore.db` | Bassa | `send_timestamp`, `read_device_timestamp` ecc. |
| `chat_list.creation` stesso valore per tutte le chat | `msgstore.db` | Bassa | Valore di default nel campione sintetico |
| 0 overlap di timestamp tra le 4 sorgenti | Cross-source | Alta | Conferma che le sorgenti sono dataset distinti |
| `content.media_path` sempre null | `messages.json` | Media | Nessun riferimento media dalla sorgente JSON |
| `quoted_row_id` sempre null | `msgstore.db` | Bassa | Nessun messaggio di reply nel campione |

*Artefatto del campione sintetico; in produzione il match sarà diverso.

---

## 10. Problemi Aperti

1. **Come normalizzare `ChatId` Cellebrite (`chat_1`, `chat_2`, `chat_3`) al JID WhatsApp?**
   I chat ID Cellebrite sono opachi senza file di mapping aggiuntivi.

2. **Cosa rappresenta `group_participant_A`?**
   Alias non mappabile a un JID o numero specifico senza una tabella di risoluzione.

3. **La timeline CSV senza timezone è UTC o ora locale del device?**
   Cellebrite UFED può esportare in ora locale o UTC. Critico per correlazione temporale.

4. **Perché il CSV ha 69 messaggi eliminati e il DB solo 14?**
   Cellebrite potrebbe recuperare da aree non allocate del DB SQLite,
   avere accesso a log di sistema aggiuntivi,
   o usare una definizione diversa di "eliminato".

5. **Esistono messaggi di tipo sistema (notifiche di gruppo, chiamate) nel DB?**
   Il campione ha solo `media_wa_type` 0-3. WhatsApp usa altri tipi non presenti nel campione.

6. **Il campo `key_id` è veramente l'ID globale WhatsApp?**
   I valori osservati (`KEY_000001`-`KEY_000503`) sembrano ID sintetici del campione.

7. **Quanti messaggi ci sono in ciascuna chat?**
   La distribuzione per `key_remote_jid` richiede una query aggiuntiva.

---

## 11. Decisioni Consigliate

### Decisioni che possono essere prese ora

| # | Decisione | Motivazione |
|---|---|---|
| D1 | Usare `msgstore.db` come sorgente primaria | Fonte nativa, 504 record, schema più ricco |
| D2 | Usare `wa.db` per risoluzione nomi contatti | Unica sorgente con `display_name` e `phone_number` |
| D3 | Timestamp canonico = `messages.timestamp` (unix_ms → UTC) | Presente, non null, alta cardinalità |
| D4 | Chiave identità messaggio = `key_id` (con fallback a hash) | Identificativo globale WhatsApp |
| D5 | Tipo messaggio da `media_wa_type` | Enum più pulito e consistente |
| D6 | Sorgente per eliminati = priorità CSV Cellebrite | Recupera più messaggi eliminati |
| D7 | `media_url` = campo primario per media reference | Ridondante con `media_refs.file_path`, più semplice |
| D8 | `key_from_me=1` → mittente = proprietario device | Semantica chiara e affidabile |

### Decisioni che richiedono ulteriori verifiche

| # | Questione | Verifica necessaria |
|---|---|---|
| V1 | Normalizzazione `ChatId` Cellebrite → JID | Ispezione file Cellebrite aggiuntivi |
| V2 | Timezone CSV (UTC vs locale) | Confronto con evento noto su timestamp reale |
| V3 | Significato esatto dei 69 `Deleted` in CSV | Analisi su campione reale |
| V4 | Risoluzione alias `group_participant_A` | Campione con mapping alias→numero |
| V5 | Validità campione media (113/115 mancanti) | Verifica su dati reali |
| V6 | Tipi messaggio mancanti (chiamate, documenti, posizioni) | Campione più ampio |

---

## 12. Mapping tra Formati

Vedi file correlato: [data-mapping.json](data-mapping.json)

---

*Report generato in sola lettura. Nessun dato originale è stato modificato, spostato o copiato.*
