# AUDIT UPLOAD GRANDI FILE (>200 MB) E SICUREZZA ZIP

Analisi tecnica dettagliata dei limiti dimensionali per il caricamento di file forensi, del consumo di memoria RAM, delle strategie di streaming e delle difese anti-zip-bomb.

---

## 1. Configurazione Server e Limiti Applicativi

### `.streamlit/config.toml` (Valori Reali)
```toml
[server]
address = "127.0.0.1"
enableCORS = true
enableXsrfProtection = true
maxUploadSize = 2048
maxMessageSize = 2048
```

### Parametri Widget Uploader (`ui/presentation.py:249-259`)
* `max_upload_size`: passato dinamicamente come `MAX_UPLOAD_SIZE_MB = 2048` a `st.file_uploader()` tramite reflection su `inspect.signature()`.
* **Limite Effettivo Corrente**: **2048 MB (2 GB)**.
* **Supporto Testato e Teorico**:
  - `> 200 MB`: **SÌ** (supera il limite di default originario di Streamlit);
  - `500 MB`: **SÌ**;
  - `1 GB`: **SÌ**;
  - `2 GB`: **SÌ** (limite massimo prefissato).
  - `> 2 GB`: Rifiutato preliminarmente da Streamlit o da `_compute_sha256_and_write()` con `InvalidUploadedFileError`.

---

## 2. Percorso dei Dati ed Efficienza di Memoria RAM

### Tracciamento:
```text
UploadedFile (Stream da Streamlit)
          ↓
IngestionRequest(primary_file=uploaded_file) [nessun .getvalue()]
          ↓
ingest_file_payload()
          ↓
_compute_sha256_and_write(chunk_size=1MB)
          ↓
File Temporaneo su Disco (tempfile.TemporaryDirectory) + SHA-256 cumulativo
          ↓
Importer (legge dal file sicuro su disco)
          ↓
Cleanup deterministico blocco finally (rimozione disco temporaneo)
```

### Analisi Duplicazioni RAM e Anti-Pattern:
1. **Assenza di `.getvalue()`**: `ui/presentation.py` non esegue più `.getvalue()`, evitando l'allocazione immediata di un buffer continuo da 2 GB in heap.
2. **Streaming Chunked**: Il file viene letto e riversato a blocchi da 1 MB (`DEFAULT_STREAM_CHUNK_SIZE`).
3. **Memoryview per Bytes**: Nei test in cui il payload è fornito come `bytes`, viene impiegato un `memoryview` con slicing a finestra scorrevole per eliminare allocazioni ridondanti.
4. **Early Size Check**: Per stream con supporto `.seek()`, la dimensione complessiva viene verificata a tempo zero prima del ciclo di lettura.

---

## 3. Limiti e Protezioni Archivi ZIP WhatsApp

I limiti sono centralizzati in `core/config.py` e verificati da `importer/whatsapp_export.py:_validate_zip_archive()`:

| Parametro Forense | Valore nel Codice | Descrizione / Giustificazione |
| :--- | :---: | :--- |
| `MAX_UPLOAD_SIZE_MB` | **2048 MB (2 GB)** | Dimensione massima file ZIP compresso caricato |
| `MAX_ARCHIVE_UNCOMPRESSED_SIZE_MB` | **4096 MB (4 GB)** | Dimensione massima complessiva decompressa consentita |
| `MAX_ARCHIVE_MEMBERS` | **50.000** | Massimo numero di file/allegati presenti nello ZIP |
| `MAX_COMPRESSION_RATIO` | **100.0** | Soglia anti zip-bomb (ratio su file > 1 MB) |
| `MAX_SINGLE_FILE_SIZE_MB` | **2048 MB (2 GB)** | Dimensione massima ammessa per un singolo membro (es. video) |

### Verifica Conflitti Dimensionali:
* Nessun conflitto rilevato: l'upload compresso ammette fino a 2 GB e l'espansione decompressa ammette fino a 4 GB (ratio 2:1 coerente con media pre-compressi WhatsApp).

---

## 4. Difese di Sicurezza Implementate su Archivi ZIP

1. **Anti Zip-Slip (Directory Traversal)**:
   - Rifiuta membri con percorsi contenenti `..` o `../` o `..\\`.
   - Rifiuta percorsi assoluti Unix (`/etc/...`) e Windows (`C:\...`, `\\unc\...`).
2. **Anti Symlink (POSIX)**:
   - Ispezione dell'attributo `external_attr >> 16 & 0o170000 == 0o120000`; rifiuto immediato di link simbolici.
3. **Nessuna Estrazione Arbitraria a Disco**:
   - L'importer non esegue `zf.extractall()`.
   - Il file di testo della chat (`_chat.txt` o transcript) viene aperto e letto in streaming direttamente dall'archivio compresso in sola lettura (`zf.open()`).
4. **Riconoscimento Transcript**:
   - Riconosce `_chat.txt` (convenzione standard WhatsApp iOS/Android) oppure l'unico file `.txt` presente nella radice.
   - Rifiuta archivi con transcript multipli ambigui nella root (`InvalidUploadedFileError`).
