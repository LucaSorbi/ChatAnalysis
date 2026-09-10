# Importer Layer — Design Document

**Fase**: 3 — Layer di ingestion  
**Data**: 2026-09-10  
**Stato**: Primo importer concreto validato — `WhatsAppMsgstoreImporter` (msgstore.db)

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
- apertura della sorgente in modalità read-only (SQLite: `?mode=ro`)
- lettura riga per riga senza caricare tutto in memoria (generatore)
- mappatura fedele dei campi originali in `raw_fields`
- identificazione del riferimento media grezzo (se presente)
- aggiunta di metadati di provenienza (nome tabella, versione importer, ecc.)
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
    source_name: str           # "msgstore_db" | "cellebrite_csv" | ...
    source_path: str           # path assoluto del file sorgente
    source_record_id: str      # ID nativo (stringa) — sempre str per uniformità
    record_type: str           # "message" | "contact" | "chat" | "media_ref"
    raw_fields: MappingProxyType[str, Any]  # campi originali non modificati
    media_reference: str|None  # path/ref raw al media (non normalizzato)
    metadata: MappingProxyType[str, Any]    # info ausiliarie sulla provenienza
```

> **Nota implementativa**: i campi `raw_fields` e `metadata` sono dichiarati
> `dict` nella firma del costruttore, ma vengono immediatamente avvolti in
> `MappingProxyType` in `__post_init__` tramite `object.__setattr__`.
> Il tipo effettivo a runtime è `MappingProxyType`.

### 2.3 Immutabilità — strategia shallow (strutturale)

`RawRecord` usa `frozen=True` + `MappingProxyType`:

| Livello | Comportamento |
|---|---|
| Attributi dell'oggetto | Completamente immutabili (`frozen=True` → `FrozenInstanceError`) |
| `raw_fields` (top-level) | Immutabile: `MappingProxyType` → `TypeError` su qualsiasi modifica |
| `metadata` (top-level) | Immutabile: `MappingProxyType` → `TypeError` su qualsiasi modifica |
| Valori annidati | **Mutabili** — limitazione intrinseca di Python |

**Limitazione residua — shallow immutability:**  
Gli oggetti annidati all'interno di `raw_fields` (es. un `dict` o `list`
come valore di un campo) rimangono tecnicamente mutabili in Python.
Per l'attuale importer SQLite, i valori raw sono principalmente
scalari (`int`, `str`, `float`), `None`, o `bytes` (BLOB) — tutti
immutabili per natura.

> ⚠️ **La strategia di immutabilità deve essere rivalutata prima
> di implementare importer JSON e XML**, dove i valori dei campi
> possono contenere strutture annidate arbitrarie (liste, dict annidati).
> Si dovrà decidere se applicare `MappingProxyType` ricorsivo o
> adottare una conversione a tipi immutabili prima della costruzione.
> **Non implementare JSON/XML importer prima di questa valutazione.**

**Regola di progetto**: nessun codice deve modificare i valori annidati
in `raw_fields` dopo la costruzione, anche dove tecnicamente possibile.

### 2.4 Campi — Dettaglio

| Campo | Tipo | Obbligatorio | Scopo |
|---|---|---|---|
| `source_name` | `str` | ✅ | Identifica la sorgente; corrisponde all'`source_name` dell'importer |
| `source_path` | `str` | ✅ | Path assoluto del file sorgente (str, non Path, per serializzabilità) |
| `source_record_id` | `str` | ✅ | ID nativo come stringa (es. "42", "msg_00123", "15") |
| `record_type` | `str` | ✅ | Tipo logico nella sorgente: "message", "contact", "chat", "media_ref" |
| `raw_fields` | `MappingProxyType` | ✅ (può essere `{}`) | Tutti i campi originali, tipi Python nativi, nessuna modifica |
| `media_reference` | `str\|None` | No | Riferimento raw al media; None se assente |
| `metadata` | `MappingProxyType` | ✅ (può essere `{}`) | Provenienza tecnica: nome tabella, riga, encoding, versione importer |

### 2.5 Convenzioni per `source_name`

| Sorgente | `source_name` |
|---|---|
| `msgstore.db` (backup WhatsApp Android) | `"msgstore_db"` |
| `messages.csv` Cellebrite | `"cellebrite_csv"` *(futuro)* |
| `messages.json` Cellebrite | `"cellebrite_json"` *(futuro)* |
| `report.xml` Cellebrite UFDR | `"cellebrite_xml"` *(futuro)* |

### 2.6 Convenzioni per `record_type`

| Tipo | Quando usarlo |
|---|---|
| `"message"` | Record che rappresenta un messaggio (tabella `messages`) |
| `"contact"` | Record che rappresenta un contatto (es. `wa.db` contacts) |
| `"chat"` | Record che rappresenta una conversazione (tabella `chat_list`) |
| `"media_ref"` | Record che descrive un media allegato (tabella `media_refs`) |

---

## 3. Interfaccia `BaseImporter`

### 3.1 Scelta: ABC vs Protocol

Si usa **ABC** (`abc.ABCMeta`) invece di `Protocol` per le seguenti ragioni:

1. **Fail-fast**: errore all'istanziazione se un metodo astratto non è implementato
2. **Ereditarietà esplicita**: `issubclass()` e `isinstance()` funzionano correttamente
3. **Semantica forense**: tutti gli importer sono interni e controllati; non serve duck-typing
4. **`validate_source()`**: metodo concreto condiviso tra tutti gli importer, naturale in ABC

### 3.2 Interfaccia completa

```python
class BaseImporter(ABC):
    @property
    @abstractmethod
    def source_name(self) -> str: ...

    @abstractmethod
    def can_import(self, source_path: Path) -> bool: ...

    @abstractmethod
    def import_records(self, source_path: Path) -> Iterator[RawRecord]: ...

    def validate_source(self, source_path: Path) -> None:
        """Verifica esistenza e compatibilità. Chiamare all'inizio di import_records()."""
        ...
```

### 3.3 Contratto degli importer concreti

Un importer concreto **deve**:
1. Definire `source_name` come stringa costante
2. Implementare `can_import()` con riconoscimento strutturale (vedi §4)
3. Chiamare `self.validate_source(source_path)` all'inizio di `import_records()`
4. Usare `yield` (generatore) — nessun dataset caricato in memoria
5. Gestire eccezioni per-record con logging osservabile (vedi §5)
6. Non modificare mai il file sorgente (read-only)

Un importer concreto **non deve**:
- Normalizzare timestamp, numeri di telefono, JID
- Risolvere riferimenti cross-source
- Applicare deduplicazione
- Aprire file diversi dalla sorgente dichiarata

---

## 4. Riconoscimento Sorgente — `can_import()`

### 4.1 Strategia: strutturale con fast-reject per estensione

In contesti forensi, un file può essere rinominato, copiato o privo di
estensione. Il riconoscimento **non deve** dipendere rigidamente dal nome file.

**Algoritmo applicato da `WhatsAppMsgstoreImporter.can_import()`:**

1. Il file deve esistere ed essere un file regolare (non directory)
2. **Fast reject**: se l'estensione appartiene inequivocabilmente a un formato
   non-SQLite (`.csv`, `.json`, `.xml`, `.jpg`, `.mp4`, …) → `False` immediato
3. Apertura in read-only (`sqlite3.connect(uri, uri=True)` con `?mode=ro`)
4. Verifica presenza delle tabelle caratteristiche: `messages`, `chat_list`
5. Verifica presenza colonne chiave: `key_remote_jid`, `key_from_me`, `key_id`
   nella tabella `messages`
6. Qualsiasi eccezione → `False` (never raise)

**Comportamento:**

| Input | Risultato | Motivo |
|---|---|---|
| `msgstore.db` con schema WhatsApp | `True` | Schema confermato |
| `evidence_backup` (nessuna estensione) | `True` | Struttura > estensione |
| `evidence.bak` | `True` | Struttura > estensione |
| `other.db` SQLite generico | `False` | Schema incompatibile |
| `data.csv` | `False` | Fast reject estensione |
| `fake.db` (testo, non SQLite) | `False` | Apertura SQLite fallisce |
| path inesistente | `False` | File non esiste |
| directory | `False` | Non è un file |

### 4.2 Riconoscimento strutturale — costanti

```python
_REQUIRED_TABLES   = frozenset({"messages", "chat_list"})
_REQUIRED_MSG_COLS = frozenset({"key_remote_jid", "key_from_me", "key_id"})
_NON_SQLITE_EXTS   = frozenset({".csv", ".json", ".xml", ".txt", ".pdf", ...})
```

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

### 5.2 Record-level errors (logging + skip)

Gli errori su singoli record **non interrompono l'iterazione**. Vengono
loggati con `logger.warning` e il generatore continua con il record successivo.

```python
try:
    yield RawRecord(...)
except Exception as exc:
    logger.warning(
        "Record saltato [source=%s table=%s _id=%s]: %s",
        source_str, table_name, record_id, exc
    )
    # continua — non re-raise, non silenzia senza logging
```

**Non è mai accettabile `except Exception: pass`.**

**Nota pragmatica — importer SQLite:**  
Per `WhatsAppMsgstoreImporter`, il percorso record→RawRecord è un
passthrough quasi diretto: `sqlite3.Row` → `dict` → `RawRecord`. I campi
sono scalari Python nativi letti dal driver SQLite. In pratica, è estremamente
difficile che un singolo record fallisca senza che l'intera connessione sia
compromessa. Il `try/except` per-record è comunque presente per:
1. Garantire il contratto `BaseImporter` (requisito architetturale)
2. Coprire casi edge imprevisti (es. record con `source_record_id` vuoto)
3. Mantenere il pattern uniforme per tutti gli importer futuri

### 5.3 Osservabilità

Gli errori di singoli record sono osservabili tramite il logging standard
Python (`logging.WARNING`). Il consumer può configurare il logger
`importer.whatsapp_msgstore` per indirizzarli verso un file di audit.

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

## 7. Read-Only — Garanzie

Sia `can_import()` che `import_records()` aprono SQLite esclusivamente con:

```python
uri = db_path.as_uri() + "?mode=ro"
conn = sqlite3.connect(uri, uri=True)
```

Il parametro `?mode=ro` è gestito dal driver SQLite a livello C-library:
qualsiasi tentativo di scrittura (INSERT, UPDATE, CREATE, DROP, modifica
di WAL/journal) genera `sqlite3.OperationalError` prima ancora che
l'operazione venga eseguita.

**Garanzie**:
- Nessuna modifica al database sorgente
- Nessun journal file (`.db-journal`) creato
- Nessun WAL file (`.db-wal`, `.db-shm`) creato
- `mtime` e dimensione del file invariati dopo import
- Test di verifica: `test_db_mtime_unchanged_after_import`,
  `test_db_size_unchanged_after_import`, `test_cannot_write_via_readonly_connection`

---

## 8. Streaming

`import_records()` è un **generatore Python** (`yield`):

- Nessun `fetchall()` sull'intero dataset
- I cursor SQLite vengono iterati riga per riga (`for row in cursor:`)
- La connessione viene aperta all'inizio e chiusa nel blocco `finally`
  alla fine dell'iterazione (anche se il consumer esce prematuramente)
- I record sono disponibili incrementalmente (il primo record è pronto
  prima che gli altri vengano letti dal DB)

---

## 9. Limiti Attuali

1. **Shallow immutability**: i valori annidati in `raw_fields` sono mutabili.
   Da rivalutare per importer JSON/XML. (vedi §2.3)
2. **Record-level errors quasi impossibili per SQLite**: il `try/except`
   per-record è presente per contratto, ma non esistono test di failure
   artificiale per-row perché il percorso row→RawRecord è un passthrough diretto.
3. **Nessun contatore di record skippati**: se dei record vengono saltati,
   il consumer non riceve un conteggio; può solo leggere i log.
4. **Nessuna validazione del contenuto dei campi**: `raw_fields` può contenere
   qualsiasi valore nativo SQLite; non si verifica se `timestamp` è positivo,
   se `key_remote_jid` ha formato JID valido, ecc. — deliberato.

---

## 10. Distinzione `RawRecord` vs `UnifiedMessage` (futuro)

| Caratteristica | `RawRecord` | `UnifiedMessage` (futuro) |
|---|---|---|
| **Scopo** | Fedeltà alla sorgente | Modello di dominio unificato |
| **Sorgente** | Una sola sorgente | Potenzialmente più sorgenti |
| **Normalizzazione** | Nessuna | Timestamp UTC, E.164, ecc. |
| **Campi** | Come nella sorgente originale | Modello canonico |
| **Immutabilità** | `frozen=True` + `MappingProxyType` | Da definire |
| **Deduplicazione** | No | Sì |
| **Entity resolution** | No | Sì |
| **Relazione** | N `RawRecord` → 1 `UnifiedMessage` | 1 `UnifiedMessage` ← N `SourceRecord` |

---

## 11. Riferimenti

- [`importer/models.py`](../importer/models.py) — implementazione `RawRecord`
- [`importer/base.py`](../importer/base.py) — implementazione `BaseImporter`
- [`importer/whatsapp_msgstore.py`](../importer/whatsapp_msgstore.py) — primo importer concreto
- [`tests/unit/test_raw_record.py`](../tests/unit/test_raw_record.py) — test unitari RawRecord
- [`tests/unit/test_base_importer.py`](../tests/unit/test_base_importer.py) — test unitari BaseImporter
- [`tests/unit/test_whatsapp_msgstore_importer.py`](../tests/unit/test_whatsapp_msgstore_importer.py) — test unitari importer
- [`tests/integration/test_whatsapp_msgstore_integration.py`](../tests/integration/test_whatsapp_msgstore_integration.py) — test integrazione
- [`tests/fixtures/sample_records.py`](../tests/fixtures/sample_records.py) — factory record sintetici

---

*Documento in sola lettura. Nessun dato originale modificato.*
