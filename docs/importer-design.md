# Importer Layer — Design Document

**Fase**: 3 — Layer di ingestion  
**Data**: 2026-09-10  
**Stato**: Importer concreti validati — `WhatsAppMsgstoreImporter` (msgstore.db), `WhatsAppWaDbImporter` (wa.db), `CellebriteCsvImporter` (messages.csv) e `CellebriteJsonImporter` (messages.json)

---

## 1. Responsabilità dell'Importer Layer

L'importer layer ha un'unica responsabilità:

> **Leggere una sorgente forense e produrre una sequenza di `RawRecord`
> fedeli all'originale, senza trasformazioni semantiche.**

Il layer di ingestion **non fa**:
- normalizzazione di timestamp, numeri di telefono, identità
- entity resolution o cross-source linkage
- deduplicazione
- unificazione dei modelli di dati tra sorgenti diverse
- arricchimento semantico
- qualsiasi scrittura sulla sorgente

Il layer di ingestion **fa**:
- apertura della sorgente in modalità read-only (SQLite: `?mode=ro`, CSV: `"r"`, JSON: `"rb"`)
- lettura riga per riga senza caricare tutto in memoria (generatore streaming via cursore SQLite, `csv.DictReader`, o `ijson.items`)
- mappatura fedele dei campi originali in `raw_fields`
- identificazione del riferimento media grezzo (se presente)
- aggiunta di metadati di provenienza (nome tabella/file, numero riga/indice array, versione importer, ecc.)
- gestione degli errori su singoli record con logging osservabile

---

## 2. Definizione di `RawRecord`

### 2.1 Principio

```
SOURCE
  ↓
RawRecord  ← questo layer
  ↓
(futuro) UnifiedMessage
```

`RawRecord` è un contenitore **fedele** alla sorgente.
Non è un modello di dominio: non ha significato semantico al di fuori
del contesto della sorgente da cui proviene.

### 2.2 Struttura

```python
@dataclass(frozen=True)
class RawRecord:
    source_name: str           # "msgstore_db" | "wa_db" | "cellebrite_csv" | "cellebrite_json" | ...
    source_path: str           # path assoluto del file sorgente
    source_record_id: str      # ID nativo (stringa) — sempre str per uniformità
    record_type: str           # "message" | "contact" | "chat" | "media_ref"
    raw_fields: MappingProxyType[str, Any]  # campi originali congelati ricorsivamente
    media_reference: str|None  # path/ref raw al media (non normalizzato)
    metadata: MappingProxyType[str, Any]    # info ausiliarie sulla provenienza
```

> **Nota implementativa**: i campi `raw_fields` e `metadata` sono dichiarati
> `dict` nella firma del costruttore, ma vengono ricorsivamente congelati tramite
> `_freeze_structural()` in `__post_init__` via `object.__setattr__`.
> Il tipo effettivo a runtime è `MappingProxyType`.

### 2.3 Immutabilità — Deep Immutability (Strutturale Ricorsiva)

Per garantire che un `RawRecord` non possa essere alterato a nessun livello dopo la costruzione,
il modello implementa **deep immutability strutturale**:

| Livello | Meccanismo | Comportamento |
|---|---|---|
| Attributi dell'oggetto | `frozen=True` | Riassegnazione attributi solleva `FrozenInstanceError` |
| `raw_fields` (top-level) | `MappingProxyType` | Mutazione solleva `TypeError` |
| `metadata` (top-level) | `MappingProxyType` | Mutazione solleva `TypeError` |
| Dizionari annidati | `_freeze_structural` ricorsivo | Convertiti in `MappingProxyType`: mutazione solleva `TypeError` |
| Liste e tuple annidate | `_freeze_structural` ricorsivo | Convertite in `tuple`: mutazione solleva `TypeError`/`AttributeError` |
| Scalari | Invariati | Tipi primitivi immutabili (`int`, `str`, `float`, `bool`, `None`, `bytes`) |

Questa strategia risolve definitivamente il rischio di mutazioni accidentali da parte dei consumer,
senza alterare la semantica, l'ordine o la cardinalità delle strutture dati originali.

### 2.4 Campi — Dettaglio

| Campo | Tipo | Obbligatorio | Scopo |
|---|---|---|---|
| `source_name` | `str` | Sì | Identificativo univoco della classe di sorgente (`"msgstore_db"`, `"wa_db"`, `"cellebrite_csv"`, `"cellebrite_json"`) |
| `source_path` | `str` | Sì | Percorso del file letto (per audit forense) |
| `source_record_id` | `str` | Sì | ID univoco del record nella sorgente (`str(row["_id"])`, `f"row:{start_line}"`, o `str(item["id"])`) |
| `record_type` | `str` | Sì | Tipologia del record (`"message"`, `"contact"`, `"chat"`, `"media_ref"`) |
| `raw_fields` | `MappingProxyType` | Sì | Snapshot esatto dei campi della riga con tipi nativi e strutture annidate congelate |
| `media_reference` | `str` o `None` | No | Riferimento raw al file multimediale associato |
| `metadata` | `MappingProxyType` | No (default vuoto) | Info contestuali di provenienza (tabella, riga/indice, versione importer) |

### 2.5 Convenzioni per `source_name`

| Sorgente | `source_name` |
|---|---|
| `msgstore.db` (backup WhatsApp Android) | `"msgstore_db"` |
| `wa.db` (contatti WhatsApp Android) | `"wa_db"` |
| `messages.csv` Cellebrite | `"cellebrite_csv"` |
| `messages.json` Cellebrite | `"cellebrite_json"` |
| `report.xml` Cellebrite UFDR | `"cellebrite_xml"` |

### 2.6 Convenzioni per `record_type`

| Tipo | Quando usarlo |
|---|---|
| `"message"` | Record che rappresenta un messaggio (tabella `messages`, righe CSV, elementi array JSON) |
| `"contact"` | Record che rappresenta un contatto (es. `wa.db` contacts) |
| `"chat"` | Record che rappresenta una conversazione (tabella `chat_list`) |
| `"media_ref"` | Record che descrive un media allegato (tabella `media_refs`) |

---

## 3. L'Interfaccia `BaseImporter`

### 3.1 Contratto

```python
class BaseImporter(ABC):
    @property
    @abstractmethod
    def source_name(self) -> str:
        """Nome identificativo della sorgente."""
        ...

    @abstractmethod
    def can_import(self, file_path: Path) -> bool:
        """Verifica se il file è gestibile da questo importer.
        Non solleva eccezioni: restituisce False se non gestibile."""
        ...

    @abstractmethod
    def import_records(self, file_path: Path) -> Iterator[RawRecord]:
        """Estrae i record dal file sorgente come generatore.
        Solleva FileNotFoundError se il file non esiste,
        ValueError se il file non è supportato."""
        ...
```

### 3.2 Invarianti di `can_import()`

1. **Mai sollevare eccezioni**: qualunque file (inesistente, corrotto, formato errato,
   directory, binario sconosciuto) deve restituire `False`, mai crashare.
2. **Nessun side-effect**: nessuna operazione di scrittura, lock permanente o
   modifica dello stato del file.
3. **Deterministico**: chiamate ripetute sullo stesso file producono lo stesso risultato.
4. **Non dipende dall'estensione**: il controllo è sul **contenuto** del file
   (struttura SQLite, intestazione CSV o elementi JSON array), non sui suffissi `.db`, `.csv` o `.json`.

---

## 4. Riconoscimento delle Sorgenti (`can_import`)

### 4.1 Strategia: riconoscimento puramente strutturale

In contesti forensi, un database SQLite o un export CSV/JSON può essere rinominato con estensioni
arbitrarie (es. `.bak`, `.txt`), copiato o privo di estensione. Pertanto,
**né il nome del file né l'estensione costituiscono un hard gate**.
La decisione definitiva dipende esclusivamente dall'apertura in sola lettura
e dalla validazione strutturale dello schema, dell'intestazione o degli oggetti JSON.

**Algoritmo generale:**

1. Il file deve esistere ed essere un file regolare (non directory).
2. Per database SQLite: apertura in read-only (`?mode=ro`) e verifica schema tabelle/colonne.
3. Per file CSV: lettura sicura della sola prima riga di intestazione via `csv.reader` (con `utf-8-sig`) e verifica del set di colonne attese.
4. Per file JSON: lettura in streaming del solo primo elemento dell'array top-level via `ijson.items(f, 'item')` e verifica del set di chiavi caratteristiche.
5. Qualsiasi eccezione (file corrotto, testo non conforme, schema incompatibile) → `False` (mai sollevare eccezioni).

**Criteri per importer:**

| Importer | Struttura / Tabelle | Colonne / Chiavi minime richieste |
|---|---|---|
| `WhatsAppMsgstoreImporter` | SQLite: `messages`, `chat_list` | `key_remote_jid`, `key_from_me`, `key_id` (in `messages`) |
| `WhatsAppWaDbImporter` | SQLite: `contacts` | `jid`, `display_name`, `phone_number` (in `contacts`) |
| `CellebriteCsvImporter` | CSV con header | `Source`, `MessageType`, `TimeStamp`, `Direction`, `ChatId` |
| `CellebriteJsonImporter` | JSON array di oggetti | `chat_id`, `sender`, `timestamp`, `type` |

**Comportamento verificato (Discriminazione a 4 vie):**

| Input | `Msgstore` | `WaDb` | `CellebriteCsv` | `CellebriteJson` | Motivo |
|---|---|---|---|---|---|
| `msgstore.db` WhatsApp | `True` | `False` | `False` | `False` | Schema msgstore confermato; no contatti / no CSV / no JSON |
| `wa.db` WhatsApp | `False` | `True` | `False` | `False` | Schema wa confermato; no messaggi / no CSV / no JSON |
| `messages.csv` Cellebrite | `False` | `False` | `True` | `False` | Header CSV Cellebrite rilevato; non è SQLite né JSON |
| `messages.json` Cellebrite | `False` | `False` | `False` | `True` | Array JSON con chiavi Cellebrite; non è SQLite né CSV |
| File valido rinominato `.txt` / `.bak` / no ext | `True` (se compatibile) | `True` | `True` | `True` | Contenuto strutturale prevale sull'estensione |
| File generico / XML / directory / assente | `False` | `False` | `False` | `False` | Rifiuto strutturale sicuro senza eccezioni |

---

## 5. Gestione Errori

### 5.1 Source-level errors (propagati)

Errori che impediscono completamente l'accesso alla sorgente vengono
propagati come eccezioni dal metodo `import_records()`:

| Condizione | Eccezione | Origine |
|---|---|---|
| Path non esiste | `FileNotFoundError` | `validate_source()` |
| File non riconoscibile dalla sorgente | `ValueError` | `validate_source()` |
| File SQLite corrotto | `sqlite3.DatabaseError` | apertura connessione |
| Permessi insufficienti | `PermissionError` | apertura connessione |

Questi errori vengono propagati al consumer: è sua responsabilità gestirli.

### 5.2 Record-level errors — Decisione architetturale (Hardening A2 / B9)

Nel mapping SQLite row → `RawRecord`, l'estrazione dei campi è un
**passthrough diretto 1:1** di tipi nativi Python (`sqlite3.Row` → `dict` → `RawRecord`).
Non ci sono trasformazioni complesse, parsing JSON, né conversioni semantiche.

**Regola forense stabilita**:
- **Nessuna perdita silenziosa o semi-silenziosa di record**: il pattern precedente
  `except Exception as exc: logger.warning(...)` rischiava di mascherare bug di codice
  o corruzioni di schema trasformandoli in record saltati.
- **Nessun `except Exception` generico** utilizzato per proseguire automaticamente nel loop riga per riga.
- **Nessun `except:`, nessun `pass`**.
- Gli errori inattesi o programmatici (es. `KeyError`, `TypeError`) vengono lasciati
  **propagare immediatamente**, garantendo l'integrità dei dati e la piena osservabilità.

---

## 6. `WhatsAppMsgstoreImporter` — Tabelle e Record Emessi

### 6.1 Tabelle trasformate in RawRecord

| Tabella | `record_type` | # record (campione) | Motivo |
|---|---|---|---|
| `messages` | `"message"` | 504 | Contenuto primario: messaggi inviati/ricevuti |
| `chat_list` | `"chat"` | 3 | Metadati delle conversazioni (JID, soggetto, creazione) |
| `media_refs` | `"media_ref"` | 115 | Riferimenti ai file media allegati |

**Ordine di emissione**: `messages` → `chat_list` → `media_refs`

### 6.2 Tabelle solo consultate (nessun record emesso)

| Tabella | Uso |
|---|---|
| `sqlite_master` | Usata esclusivamente in `can_import()` per verificare il schema |
| `sqlite_sequence` | Non utilizzata |

### 6.3 Join

**Nessun join eseguito.** Ogni tabella viene letta con `SELECT * FROM <table>
ORDER BY _id`. Non ci sono JOIN inter-tabella nell'importer.

La relazione tra `media_refs.message_row_id` e `messages._id` è strutturalmente
presente nel DB, ma **non viene risolta** a questo livello. Il consumer o la
fase di normalizzazione decideranno se e come fare il join semantico.

### 6.4 Motivazione delle scelte

- `messages`: obbligatoria — è il contenuto primario di ogni backup WhatsApp
- `chat_list`: emessa perché contiene metadati di contesto (soggetto gruppo,
  timestamp creazione, JID) che non sono presenti nella tabella `messages`.
  Il record raw viene emesso fedelmente; non è un'entità `Chat` normalizzata.
- `media_refs`: emessa perché associa ogni messaggio al percorso fisico del
  file media su disco — informazione forense utile per la media discovery.

### 6.5 Cosa NON viene fatto

- Nessuna normalizzazione timestamp (unix_ms conservato tal quale)
- Nessuna normalizzazione JID o numeri di telefono
- Nessuna risoluzione contatti tramite `wa.db`
- Nessuna verifica esistenza file media su disco
- Nessuna deduplicazione
- Nessuna entity resolution
- Nessun cross-source linkage
- Nessun mapping dei `deleted=1` verso un modello canonico
- Nessuna tassonomia dei `media_wa_type` (0=text, 1=image, 2=audio, 3=video)
  — il valore intero originale viene conservato

---

## 7. `WhatsAppWaDbImporter` — Tabella Contacts e Record Emessi

### 7.1 Tabella trasformata in RawRecord

| Tabella | `record_type` | # record (campione) | Motivo |
|---|---|---|---|
| `contacts` | `"contact"` | 4 | Informazioni di rubrica e profilo contatti WhatsApp Android |

### 7.2 Schema osservato di `wa.db`

Dall'ispezione diretta del file sintetico `test_data/whatsapp_export/wa.db`:
- **Tabelle osservate**: 1 sola tabella (`contacts`)
- **Viste**: 0 | **Indici**: 1
- **Colonne rilevanti**:
  - `_id` (INTEGER PRIMARY KEY) → chiave primaria locale
  - `jid` (TEXT) → identificativo WhatsApp del contatto (es. `+390000000001@s.whatsapp.net`)
  - `display_name` (TEXT) → nome visualizzato in rubrica (es. `Contatto_001`)
  - `status` (TEXT) → messaggio di stato WhatsApp (può essere `NULL`, es. contatto 2 Contatto_002)
  - `phone_number` (TEXT) → numero telefonico in formato sorgente (es. `+39 000 0000001`)

### 7.3 Identificatori e Mappatura RawRecord

- `source_name`: `"wa_db"`
- `source_path`: percorso assoluto del file `wa.db`
- `source_record_id`: `str(row["_id"])` (progressivo stringa: `"1"`, `"2"`, `"3"`, `"4"`)
- `record_type`: `"contact"` (source-oriented, NON l'entità canonica finale `Participant`)
- `raw_fields`: dizionario completo dei valori SQLite grezzi (`_id`, `jid`, `display_name`, `status`, `phone_number`)
- `media_reference`: `None` (la tabella contatti non referenzia file multimediali)
- `metadata`: `{"table": "contacts", "importer": "WhatsAppWaDbImporter", "importer_version": "0.1.0"}`

### 7.4 Separazione Assoluta da `msgstore.db` (Vincolo Non Negoziabile)

`WhatsAppWaDbImporter` e `WhatsAppMsgstoreImporter` sono rigorosamente disaccoppiati:
- **Nessun arricchimento cross-database**: `wa.db` non viene utilizzato per associare nomi o contatti ai messaggi di `msgstore.db`.
- **Nessuna risoluzione JID**: i JID non vengono convertiti in partecipanti o contatti unificati.
- **Nessuna costruzione di Participant o Chat canonica**: la trasformazione in modelli di dominio è differita alle future fasi di normalizzazione ed entity resolution.
- **Nessun join**: nessun accesso a `msgstore.db` durante l'import di `wa.db`, e viceversa.

### 7.5 Fedeltà Source-Level e Assenza di Normalizzazione

- **Numeri telefonici**: conservati identici all'originale con spazi e prefissi (es. `+39 000 0000001`, `+1 000 0000004`), senza conversione a E.164.
- **JID**: conservati inalterati (nessuna rimozione di `@s.whatsapp.net`, nessuna canonicalizzazione).
- **Valori NULL**: preservati come `None` (es. `status` del contatto 2).

---

## 8. `CellebriteCsvImporter` — File CSV e Record Emessi

### 8.1 Struttura del file `messages.csv`

Dall'ispezione diretta del file sintetico `test_data/cellebrite_export/messages.csv`:
- **Totale record**: 302 righe di messaggi (riga 1 = intestazione, righe 2..303 = dati)
- **Colonne rilevate** (13 colonne):
  `Source`, `MessageType`, `TimeStamp`, `Direction`, `From`, `To`, `Body`, `Attachments`, `Status`, `Deleted`, `Forwarded`, `ApplicationId`, `ChatId`
- **Encoding e Dialect**:
  - Encoding: `utf-8-sig` (permette la rimozione trasparente dell'eventuale BOM UTF-8 iniziale senza contaminare i nomi dei campi con `\ufeff`)
  - Delimitatore: virgola `,`
  - Quotechar: doppio apice `"`
  - Modalità apertura: `newline=""` (standard raccomandato per modulo `csv` Python)

### 8.2 Identificatori e Mappatura RawRecord

- `source_name`: `"cellebrite_csv"`
- `source_path`: percorso assoluto del file `messages.csv`
- `source_record_id`: `f"row:{start_line}"` (es. `"row:2"`, `"row:3"`, ..., `"row:303"`)
  - **Motivazione architetturale**: l'export CSV di Cellebrite non contiene un ID nativo per ogni riga/messaggio. L'uso della linea fisica di inizio del record logico (`start_line = prev_line_num + 1`) garantisce l'univocità, l'immutabilità e la tracciabilità forense esatta anche in presenza di campi multiline racchiusi da virgolette (es. `Body` con newline interne).
- `record_type`: `"message"` (tutte le 302 righe rappresentano record di messaggistica)
- `raw_fields`: dizionario completo dei valori riga grezzi (`Source`, `MessageType`, `TimeStamp`, `Direction`, `From`, `To`, `Body`, `Attachments`, `Status`, `Deleted`, `Forwarded`, `ApplicationId`, `ChatId`)
- `media_reference`: `row["Attachments"] or None` (popolato con la stringa grezza dell'allegato se presente; `None` se stringa vuota)
- `metadata`: `{"table": "messages", "row_number": start_line, "importer": "CellebriteCsvImporter", "importer_version": "0.1.0"}`

### 8.3 Fedeltà dei dati e assenza di normalizzazione semantica

L'importer applica rigorosamente il principio di fedeltà alla sorgente forense:
- **Preservazione stringhe vuote**: campi vuoti nel CSV (es. `Body=""`, `Attachments=""`, `Status=""`) rimangono stringhe vuote `""` all'interno di `raw_fields`, senza trasformazioni artificiali in `None` o `False`.
- **Timestamp grezzi**: il campo `TimeStamp` viene preservato come stringa testuale non parsata (es. `"2024-11-12 03:09:27"` o `""`), senza conversione a `datetime` e senza applicazione o inferenza di timezone / UTC.
- **Valori booleani grezzi**: campi come `Deleted` e `Forwarded` sono conservati come stringhe (`"True"`, `"False"`), senza cast a tipo `bool`.
- **Riconoscimento e malformazioni**:
  - File non validi o con header mancanti sollevano `ValueError`.
  - Righe con campi in eccesso o mancanti rispetto all'intestazione sollevano `ValueError` con il numero di riga dettagliato (nessun silent skipping).

### 8.4 Questioni lasciate deliberatamente IRRISOLTE (Differite a Fasi Successive)

In accordo con la pipeline vincolante (`DATI ORIGINALI → IMPORTER → RawRecord → VALIDAZIONE → NORMALIZZAZIONE → ENTITY RESOLUTION → UnifiedMessage → ANALISI AI`):
1. **Mappatura `ChatId` (`chat_1`, `chat_2`, `chat_3`) verso JID WhatsApp**: nessuna risoluzione o unificazione con i JID di `msgstore.db` o `wa.db`.
2. **Identificatori partecipanti aliasati (es. `group_participant_A`)**: nessun tentativo di associazione ai numeri di telefono o contatti di `wa.db`.
3. **Timezone del CSV**: nessuna assunzione su fuso orario locale vs UTC.
4. **Semantica cross-source di `Deleted`**: nessuna equivalenza semantica definita tra il flag `Deleted="True"` di Cellebrite e `deleted=1` di WhatsApp.

---

## 9. `CellebriteJsonImporter` — File JSON e Record Emessi

### 9.1 Struttura del file `messages.json`

Dall'ispezione diretta del file sintetico `test_data/cellebrite_export/messages.json`:
- **Formato top-level**: Array JSON (`list`) contenente oggetti messaggio.
- **Totale record**: 100 oggetti messaggio.
- **Chiavi per ciascun elemento**:
  `id`, `chat_id`, `sender`, `timestamp`, `type`, `content`, `metadata`
- **Struttura dei sotto-oggetti**:
  - `content`: `{"text": str | null, "media_path": str | null}`
  - `metadata`: `{"deleted": bool, "forwarded": bool, "starred": bool}`
- **Encoding**: UTF-8 standard.

### 9.2 Streaming con `ijson`

Per evitare di caricare l'intero file JSON in memoria (`json.load()` saturerebbe la RAM su export forensi multi-gigabyte),
l'importer adotta la libreria specializzata `ijson`:
- `ijson.items(f, 'item')` itera progressivamente gli oggetti dell'array top-level uno ad uno.
- Nessun caricamento dell'intero dataset in memoria: l'elaborazione è strettamente incrementale, con consumo di memoria limitato principalmente al record corrente e ai buffer interni del parser (non proporzionale al numero complessivo dei record).
- Dipendenza dichiarata nel manifesto di progetto: `ijson>=3.3` in `pyproject.toml`.

### 9.3 Identificatori e Mappatura RawRecord

- `source_name`: `"cellebrite_json"`
- `source_path`: percorso assoluto del file `messages.json`
- `source_record_id`: priorità a identificatore nativo (`str(item["id"])`, es. `"msg_00000"`, `"msg_00001"`). Se assente o nullo, fallback strutturale a `f"item:{idx}"` (0-indexed).
- `record_type`: `"message"`
- `raw_fields`: mapping completo con tipi nativi e strutture annidate congelate ricorsivamente via `_freeze_structural()`.
- `media_reference`: stringa da `content.get("media_path")` se presente e non nulla, altrimenti `None`.
- `metadata`: `{"table": "messages", "format": "json_array", "array_index": idx, "importer": "CellebriteJsonImporter", "importer_version": "0.1.0"}`

### 9.4 Fedeltà dei dati: Tipi Nativi JSON e Deep Immutability

- **Tipi nativi preservati**:
  - Booleani: `deleted`, `forwarded`, `starred` restano booleani nativi Python (`True`/`False`), non convertiti in stringhe.
  - Valori nulli: `null` JSON preservato come `None` Python.
  - Numeri e stringhe: preservati nei rispettivi tipi nativi.
- **Deep Immutability**:
  - I dizionari annidati (`content`, `metadata`) sono congelati in `MappingProxyType`.
  - Eventuali liste/tuple annidate sono congelate in `tuple`.
  - Tentativi di mutazione a qualsiasi livello di profondità sollevano `TypeError`.

### 9.5 Differenze Strutturali Chiave: CSV vs JSON

| Aspetto | `CellebriteCsvImporter` | `CellebriteJsonImporter` |
|---|---|---|
| **Tipo `Deleted`** | Stringa grezza (`"True"`, `"False"`) | Booleano nativo (`True`, `False`) |
| **Campi vuoti / nulli** | Stringa vuota `""` | `None` (da `null`) |
| **Struttura campi** | Flat (13 colonne di primo livello) | Gerarchica (nested `content`, `metadata`) |
| **Identificatore nativo** | Assente (usato `row:<start_line>`) | Presente (`msg_00000`, ...) |
| **Streaming backend** | `csv.DictReader` stdlib | `ijson.items` streaming |

### 9.6 Questioni lasciate deliberatamente IRRISOLTE

1. `chat_id` non mappato a JID WhatsApp.
2. `group_participant_A` conservato verbatim.
3. Timestamp ISO-8601 conservato come stringa senza forzare UTC.
4. Nessun arricchimento cross-source con `msgstore.db` o `wa.db`.

---

## 10. `CellebriteXmlImporter` (`report.xml`)

### 10.1 Struttura Reale Osservata nel Campione UFDR
- **File sorgente**: `test_data/cellebrite_export/report.xml` (~10 KB, 50 messaggi).
- **Dichiarazione XML ed Encoding**: `<?xml version='1.0' encoding='utf-8'?>`
- **Root element**: `<DumpFile type="UFDR" version="2.0">`
- **Elementi di dispositivo (ignorati dall'importer messaggi)**: `<DeviceInfo>` contenente `<Model>`, `<OS>`, `<IMEI>`. Questi campi costituiscono metadati di estrazione a livello di apparato e non record di messaggi.
- **Contenitore messaggi**: `<InstantMessages>`
- **Elementi messaggio**: 50 elementi `<Message id="0">` .. `<Message id="49">`.
- **Campi interni a `<Message>`**:
  - `<Timestamp>`: stringa ISO 8601 con offset esplicito (`2024-04-23T20:30:15+00:00`).
  - `<Sender>`: identificativo mittente (numero telefonico internazionale o alias `group_participant_A`).
  - `<Body>`: contenuto testuale o elemento vuoto `<Body />` (restituito come `None`).
  - `<Deleted>`: stringa testuale `"false"` o `"true"` (1 solo messaggio eliminato: id="35").

### 10.2 Sicurezza del Parsing per Input Non Fidato
Poiché le esportazioni forensi costituiscono input non fidato proveniente dall'esterno, il parsing è affidato alla libreria specializzata `defusedxml` con `forbid_dtd=True`:
- **Divieto DTD/DOCTYPE**: solleva `DTDForbidden` in presenza di dichiarazioni di DTD.
- **Protezione contro DoS / Billion Laughs**: l'espansione delle entità interne è categoricamente vietata.
- **Protezione XXE (XML External Entity)**: il caricamento di risorse locali (`file:///`) o remote è completamente disabilitato.
- **Dipendenza**: `defusedxml>=0.7.1` in `pyproject.toml` (libreria pura Python, ~25 KB).

### 10.3 Streaming Incrementale e Rilascio Memoria
- L'importer impiega `defusedxml.ElementTree.iterparse(source, events=("end",), forbid_dtd=True)`.
- Nessun caricamento dell'intero albero DOM in memoria.
- Evento utilizzato: `"end"` su elemento `Message`. Al completamento dell'elemento, i campi vengono mappati nel `RawRecord`, emessi via `yield`, e la memoria dell'elemento viene immediatamente liberata invocando `elem.clear()`.
- Il consumo di memoria è contenuto e non proporzionale al numero complessivo dei messaggi nel report.

### 10.4 Identificatori e Mappatura RawRecord
- `source_name`: `"cellebrite_xml"`
- `source_path`: percorso assoluto del file `report.xml`
- `source_record_id`: priorità 1 all'attributo nativo `id` (`str(elem.attrib["id"])`, es. `"0"`, `"1"`, ...). Se assente, fallback strutturale deterministico `f"message:{idx}"` (0-based).
- `record_type`: `"message"`
- `raw_fields`: mappatura gerarchica fedele con `@attributes`, `Timestamp`, `Sender`, `Body`, `Deleted`. Elementi ripetuti preservati come liste ordinate. Tutte le strutture annidate sono congelate ricorsivamente tramite `_freeze_structural()`.
- `media_reference`: `None` per i messaggi testuali standard di `report.xml`. Se in future estrazioni è presente un tag esplicito (`Attachment`, `Media`), viene estratto in modo sicuro.
- `metadata`: `{"table": "InstantMessages", "format": "xml_ufdr", "xml_index": idx, "importer": "CellebriteXmlImporter", "importer_version": "0.1.0"}`

### 10.5 Preservazione Tipi Nativi XML e Differenze Cross-Source
XML fornisce nativamente testo e nodi vuoti (`None`). Nessuna coercizione euristica viene applicata:
- `<Deleted>false</Deleted>` rimane stringa `"false"` (e non booleano `False`).
- Differenza deliberata osservata e testata tra sorgenti:
  - **CSV**: `Deleted` = stringa `"False"` o `"True"`
  - **JSON**: `metadata.deleted` = booleano nativo `False` o `True`
  - **XML**: `Deleted` = stringa `"false"` o `"true"`

### 10.6 Questioni Aperte Rimandate ai Layer Successivi
1. `ChatId` / `JID`: assenti nel report XML; il raggruppamento delle conversazioni e l'entity resolution spetteranno alle fasi a valle.
2. `group_participant_A`: alias mittente non risolto e non alterato.
3. Timezone: timestamp ISO 8601 conservato come stringa grezza senza conversioni forzate.
4. Nessun arricchimento o incrocio con `wa.db`, `msgstore.db`, CSV o JSON.

---

## 11. Read-Only — Garanzie

### 11.1 Database SQLite (`msgstore.db`, `wa.db`)

Apertura esclusiva in URI read-only:

```python
uri = db_path.resolve().as_uri() + "?mode=ro"
conn = sqlite3.connect(uri, uri=True)
```

Qualsiasi tentativo di scrittura solleva `sqlite3.OperationalError`. Nessun file WAL o journal creato.

### 11.2 File Testuali e Dati (`messages.csv`, `messages.json`, `report.xml`)

- CSV aperto con flag `"r"` (`open(file_path, "r", encoding="utf-8-sig", newline="")`).
- JSON aperto con flag `"rb"` (`open(file_path, "rb")`).
- XML aperto con flag `"rb"` (`open(file_path, "rb")`).
- Nessuna scrittura, rinomina, rimozione o creazione di file temporanei.
- `mtime` e dimensione del file verificati costanti prima e dopo l'importazione.

---

## 12. Streaming

Tutti i metodi `import_records()` sono **generatori Python** (`yield`):

- **Nessun caricamento completo in memoria**:
  - SQLite: cursore iterato riga per riga (`for row in cursor:`)
  - CSV: `csv.DictReader` iterato riga per riga con `start_line` tracciato progressivamente
  - JSON: `ijson.items(f, 'item')` itera progressivamente gli oggetti dell'array
  - XML: `defusedxml.ElementTree.iterparse(f, events=('end',))` con `elem.clear()` immediato
- **Rilascio immediato delle risorse**: connessioni e file handle racchiusi in blocchi `with` o `try...finally`.
- **Disponibilità incrementale**: il primo record è pronto e consumabile prima che i record successivi vengano letti dalla sorgente.

---

## 13. Limiti Attuali

1. **Immutabilità**: la shallow immutability iniziale è stata superata mediante `_freeze_structural()`, che garantisce deep immutability su dizionari e sequenze annidate.
2. **Nessun framework complesso di error management**: in conformità all'Hardening forense, errori inattesi o corruzioni strutturali si propagano immediatamente (nessun silent skipping con `except Exception: pass`).
3. **Nessuna validazione semantica del contenuto**: `raw_fields` contiene i dati così come presenti nella sorgente, senza verifiche di plausibilità (es. validità regex di JID, normalizzazione fuso orario). Deliberato per fedeltà forense.

---

## 14. Distinzione `RawRecord` vs `UnifiedMessage` (futuro)

| Caratteristica | `RawRecord` | `UnifiedMessage` (futuro) |
|---|---|---|
| **Scopo** | Fedeltà alla sorgente | Modello di dominio unificato |
| **Sorgente** | Una sola sorgente | Potenzialmente più sorgenti |
| **Normalizzazione** | Nessuna | Timestamp UTC, E.164, ecc. |
| **Campi** | Come nella sorgente originale | Modello canonico |
| **Immutabilità** | `frozen=True` + `_freeze_structural` | Da definire |
| **Deduplicazione** | No | Sì |
| **Entity resolution** | No | Sì |
| **Relazione** | N `RawRecord` → 1 `UnifiedMessage` | 1 `UnifiedMessage` ← N `SourceRecord` |

---

## 15. Riferimenti

- [`importer/models.py`](../importer/models.py) — implementazione `RawRecord` e `_freeze_structural`
- [`importer/base.py`](../importer/base.py) — interfaccia astratta `BaseImporter`
- [`importer/whatsapp_msgstore.py`](../importer/whatsapp_msgstore.py) — importer per `msgstore.db`
- [`importer/whatsapp_wa.py`](../importer/whatsapp_wa.py) — importer per `wa.db`
- [`importer/cellebrite_csv.py`](../importer/cellebrite_csv.py) — importer per `messages.csv`
- [`importer/cellebrite_json.py`](../importer/cellebrite_json.py) — importer per `messages.json`
- [`importer/cellebrite_xml.py`](../importer/cellebrite_xml.py) — importer per `report.xml`
- [`scripts/sanitize_synthetic_dataset.py`](../scripts/sanitize_synthetic_dataset.py) — script di sanitizzazione sicura del dataset sintetico
- [`tests/unit/test_raw_record.py`](../tests/unit/test_raw_record.py) — test unitari RawRecord e deep immutability
- [`tests/unit/test_base_importer.py`](../tests/unit/test_base_importer.py) — test unitari BaseImporter
- [`tests/unit/test_whatsapp_msgstore_importer.py`](../tests/unit/test_whatsapp_msgstore_importer.py) — test unitari msgstore
- [`tests/unit/test_whatsapp_wa_importer.py`](../tests/unit/test_whatsapp_wa_importer.py) — test unitari wa.db
- [`tests/unit/test_cellebrite_csv_importer.py`](../tests/unit/test_cellebrite_csv_importer.py) — test unitari Cellebrite CSV e multiline
- [`tests/unit/test_cellebrite_json_importer.py`](../tests/unit/test_cellebrite_json_importer.py) — test unitari Cellebrite JSON
- [`tests/unit/test_cellebrite_xml_importer.py`](../tests/unit/test_cellebrite_xml_importer.py) — test unitari Cellebrite XML
- [`tests/unit/test_sanitize_dataset.py`](../tests/unit/test_sanitize_dataset.py) — test unitari sicurezza sanitizer
- [`tests/integration/test_whatsapp_msgstore_integration.py`](../tests/integration/test_whatsapp_msgstore_integration.py) — test integrazione msgstore
- [`tests/integration/test_whatsapp_wa_integration.py`](../tests/integration/test_whatsapp_wa_integration.py) — test integrazione wa.db
- [`tests/integration/test_cellebrite_csv_integration.py`](../tests/integration/test_cellebrite_csv_integration.py) — test integrazione Cellebrite CSV
- [`tests/integration/test_cellebrite_json_integration.py`](../tests/integration/test_cellebrite_json_integration.py) — test integrazione Cellebrite JSON
- [`tests/integration/test_cellebrite_xml_integration.py`](../tests/integration/test_cellebrite_xml_integration.py) — test integrazione Cellebrite XML
- [`tests/fixtures/sample_records.py`](../tests/fixtures/sample_records.py) — factory record sintetici

---

*Documento in sola lettura. Nessun dato originale modificato.*
