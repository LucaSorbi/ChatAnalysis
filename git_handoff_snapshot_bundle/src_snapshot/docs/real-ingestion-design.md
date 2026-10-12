# Real File Ingestion Architecture & Design Document

## 1. Obiettivo e Visione Architetturale

Il modulo **Real File Ingestion** costituisce il ponte sicuro, disaccoppiato e rigorosamente offline tra i payload caricati dall'utente nell'interfaccia Streamlit e la pipeline forense preesistente del repository.

L'integrazione consente alla UI di operare direttamente sui formati sorgente reali supportati dagli importer approvati del progetto:
1. **WhatsApp msgstore SQLite** (`WhatsAppMsgstoreImporter`, `msgstore_db`)
2. **WhatsApp wa.db SQLite** (`WhatsAppWaDbImporter`, `wa_db`)
3. **WhatsApp export chat (TXT / ZIP)** (`WhatsAppExportImporter`, `whatsapp_export`)
4. **Cellebrite CSV** (`CellebriteCsvImporter`, `cellebrite_csv`)
5. **Cellebrite JSON** (`CellebriteJsonImporter`, `cellebrite_json`)
6. **Cellebrite XML** (`CellebriteXmlImporter`, `cellebrite_xml`)

L'intero ciclo di elaborazione rispetta la sequenza canonica approvata:
```
UPLOAD IN MEMORIA
  → ISOLAMENTO SANDBOX VOLATILE (tempfile.TemporaryDirectory)
  → HASHING STREAMING SHA-256
  → PREFLIGHT SIGNATURE CHECK (SQLite o ZIP/TXT)
  → DISPATCH ESPLICITO IMPORTER
  → RawRecord STREAM
  → VALIDAZIONE (RecordValidator)
  → NORMALIZZAZIONE (RecordNormalizer)
  → ENTITY RESOLUTION (DeterministicEntityResolver)
  → UNIFIED MESSAGES (UnifiedModelBuilder)
  → EVIDENCE BUNDLES (MessageEvidenceBundle)
  → CONVERSATION EVIDENCE DOCUMENTS (ConversationEvidenceDocument, 1 per chat)
  → SEARCH SERVICE DETERMINISTICO (SearchService in memoria)
  → PULIZIA DETERMINISTICA TEMPORARY WORKSPACE (garantita in finally)
```

---

## 2. Dispatch Esplicito per Formato Sorgente

Nessuna auto-detection euristica o silenziosa è consentita. La selezione del formato operata dall'operatore forense nella UI è autoritativa.

| SourceFormat Enum | Importer Concreto | Tipo Sorgente | Output Primario |
| :--- | :--- | :--- | :--- |
| `WHATSAPP_MSGSTORE` | `WhatsAppMsgstoreImporter` | SQLite `msgstore.db` | Messaggi, chat, allegati metadata |
| `WHATSAPP_WA` | `WhatsAppWaDbImporter` | SQLite `wa.db` | Contatti / identità (AUXILIARY_ONLY) |
| `WHATSAPP_EXPORT` | `WhatsAppExportImporter` | Export TXT o archivio ZIP | Messaggi chat WhatsApp, timeline, allegati |
| `CELLEBRITE_CSV` | `CellebriteCsvImporter` | CSV `messages.csv` | Record di messaggistica tabellare |
| `CELLEBRITE_JSON` | `CellebriteJsonImporter` | JSON strutturato | Messaggi estratti da report Cellebrite |
| `CELLEBRITE_XML` | `CellebriteXmlImporter` | XML report Cellebrite | Messaggi XML (partizione UNRESOLVED) |

Se il file caricato non corrisponde al formato selezionato o presenta anomalie, l'operazione viene interrotta con un'eccezione tipizzata controllata (`InvalidUploadedFileError` o `PipelineStageError`), senza tentativi di fallback opachi.

---

## 3. Politica di Sicurezza Upload e Gestione File Temporanei

Gli importer upstream lavorano tramite percorsi filesystem (`Path`). Poiché Streamlit gestisce i file caricati in memoria o tramite buffer volatili (`UploadedFile`), l'applicazione adotta una politica di sandbox temporanea rigorosa:

1. **Sandbox Isolato**: Ogni richiesta di ingestion istanzia un context manager stdlib `tempfile.TemporaryDirectory(prefix="forensic_ingest_")`.
2. **Nomi File Rigorosamente Controllati**: I file scritti all'interno della directory temporanea ricevono nomi fissi e controllati dall'applicazione (`primary_evidence.bin`, `companion_wa.db`).
3. **Prevenzione Path Traversal**: L'attributo `filename` dell'upload viene sanitizzato estraendo esclusivamente `Path(filename).name`. Qualsiasi sequenza di path traversal (`../../`, `..\\`) non ha effetto sul percorso di scrittura effettivo.
4. **Hashing SHA-256 Incrementale**: Durante la scrittura su disco temporaneo, l'impronta crittografica SHA-256 e la dimensione esatta in byte vengono calcolate in streaming chunk per chunk (buffer da 64 KB).
5. **Pulizia Deterministica Garantita**: L'intero workspace temporaneo viene distrutto al termine del blocco `with tempfile.TemporaryDirectory()`, sia in caso di successo che a seguito di qualsiasi eccezione. Nessun file temporaneo o dato residuo permane sul disco dell'host.
6. **Zero Logging di Dati Sensibili**: Né i percorsi temporanei, né i testi delle chat, né i metadati grezzi o query SQL vengono stampati su console o registrati su file di log.

---

## 4. Preflight Check e Gestione Errori Sanitizzata (Error Handling & Privacy)

La pipeline implementa una gerarchia di eccezioni rigorosamente tipizzate e sanitizzate che garantiscono gli attributi `safe_message` e `stage`, escludendo qualsiasi data leakage nell'interfaccia utente:

```python
class IngestionError(Exception):
    """Eccezione base: espone safe_message e stage opzionale."""

class InvalidUploadedFileError(IngestionError):
    """stage='preflight': file vuoto (0 byte) o firma SQLite mancante."""

class UnsupportedSourceFormatError(IngestionError):
    """stage='dispatch': formato non supportato o non riconosciuto."""

class PipelineStageError(IngestionError):
    """stage esplicito (es. 'importer', 'unified_building'): safe_message controllato."""
```

### Regole Fondamentali di Gestione Errori:
- **Zero Raw Message Exposure**: La UI non renderizza mai `str(exc)` dell'eccezione originaria, né traceback, percorsi di sistema, frammenti SQL o identificatori di database.
- **Causa Collegata Internamente**: L'eccezione upstream originale può essere collegata tramite `raise PipelineStageError(...) from exc` per scopi di analisi interna, ma non viene mai esposta nella presentazione grafica.
- **Rendering Tipizzato**:
  - `PipelineStageError`: Mostra `[{stage}] PipelineStageError: {safe_message}`.
  - `InvalidUploadedFileError`: Mostra `InvalidUploadedFileError: {safe_message}`.
  - `UnsupportedSourceFormatError`: Mostra `UnsupportedSourceFormatError: {safe_message}`.
  - `IngestionError`: Mostra `IngestionError: {safe_message}`.
- **Zero AttributeError Garantito**: Poiché tutte le eccezioni derivano da `IngestionError` e definiscono esplicitamente `safe_message` e `stage`, la UI non incorre mai in `AttributeError` durante la cattura degli errori.

---

## 5. Dettagli Specifici per Sorgente

### 5.1 WhatsApp msgstore SQLite
- Elabora il database dei messaggi principale.
- Estrae fedelmente record di tipo `message`, `chat` e `media_ref`.
- Preserva lo stato di cancellazione originale (`is_deleted`), i timestamp grezzi e le provenance.
- Non inventa identità o numeri se assenti.

### 5.2 Companion wa.db e Gestione Failure Non Silenziosa
- Quando l'operatore seleziona `WhatsApp msgstore`, la UI espone un secondo selettore facoltativo: *"wa.db contatti — opzionale"*.
- **Comportamento in caso di Successo**: Se fornito e valido, `wa.db` viene importato, arricchendo i `display_name` dei mittenti in `DeterministicEntityResolver`. L'arricchimento incrementa `auxiliary_record_count` (es. +4 contatti).
- **Comportamento in caso di Errore / Incompatibilità**:
  - **NON Silenzioso**: È vietato l'uso di `except Exception: pass`.
  - **Non Bloccante**: Il fallimento del companion non blocca l'ingestion principale di `msgstore.db`.
  - **Warning Non Sensibile**: Viene aggiunto un avviso formale ed esplicito in `IngestionSummary.warnings`:
    > *"Il companion wa.db non è stato utilizzato perché non è risultato compatibile o leggibile. L'analisi di msgstore è proseguita senza arricchimento contatti."*
  - **Auditabilità del Conteggio**: `companion_aux_count` rimane pari a `0`. Il summary evidenzia che l'arricchimento non è stato applicato. Nessun percorso temporaneo, frammento SQL o messaggio di eccezione upstream viene incluso nel warning.

### 5.3 wa.db Standalone (Auxiliary-Only)
- Se l'operatore seleziona direttamente `WhatsApp wa.db`, il database viene importato, validato e normalizzato.
- Poiché `wa.db` contiene unicamente contatti e non conversazioni o messaggi, l'ingestion imposta lo stato `IngestionStatus.AUXILIARY_ONLY`.
- Non viene creato alcun `ConversationEvidenceDocument` fittizio, né messaggi o topic sintetici.
- La UI mostra un messaggio informativo chiaro: *"wa.db contiene dati contatto/identità e non costituisce da solo una conversazione analizzabile."*, esponendo conteggi record, issue e SHA-256 senza attivare Ricerca o Analisi Topic.

### 5.4 Cellebrite (CSV, JSON, XML)
- Ciascuna sorgente Cellebrite attraversa la medesima catena di validazione, normalizzazione ed entity resolution.
- Per `report.xml`, dove i messaggi non contengono un `ChatId` esplicito, i record vengono associati deterministicamente alla partizione protetta `UNRESOLVED`, senza fonderli arbitrariamente con altre conversazioni.

---

## 6. Pseudonimizzazione delle Label di Conversazione e Separazione Metadati

### 6.1 Pseudonimizzazione Rigorosa delle Label UI
Nessun selettore conversazione o etichetta di navigazione espone in chiaro:
- Titoli chat (es. nomi gruppi);
- Numeri telefonici in formato internazionale;
- JID WhatsApp (es. `123456@s.whatsapp.net`, `xyz@g.us`);
- Nomi di contatto della rubrica;
- Identificativi grezzi di database;
- Anteprime dei testi dei messaggi.

Le etichette descrittive generate in `ImportedConversationInfo.display_label` adottano un formato pseudonimizzato, stabile e deterministico:
- `Conversazione 1 — 168 messaggi — [doc::<short-id>]`
- `Conversazione 2 — 170 messaggi — [doc::<short-id>]`
- `Messaggi non associati — 50 messaggi — [doc::<short-id>]`

### 6.2 Distinzione fra Identificatore Tecnico e Label UI
- **Livello Dominio e Provenance**: L'identificatore tecnico (`chat_id`, `source_record_id`, `document_id`) rimane fedelmente memorizzato nell'oggetto `ConversationEvidenceDocument` e nella catena di provenance forense.
- **Livello Presentazione Primaria**: La selectbox di selezione e i campi principali della Panoramica visualizzano esclusivamente la `display_label` pseudonimizzata e il `document_id`.
- **Expander Provenance Tecnica**: L'identificatore tecnico di chat (`chat_id`) è relegato esclusivamente all'interno dell'expander chiuso *"Provenance Tecnica (Metadati Interni)"*, evitando l'esposizione involontaria a schermo.

### 6.3 Uso del Campo Canonico `bundle_count`
Nel modello `ImportedConversationInfo`, il conteggio dei messaggi/bundle della conversazione è espresso unicamente tramite il campo canonico:
```python
bundle_count: int
```
La UI interroga unicamente `bundle_count` per qualsiasi metrica o formattazione, senza fare ricorso a proprietà inesistenti (`message_count`) o alias impropri.

---

## 7. Document ID Deterministico e Privacy-Preserving

I `document_id` non utilizzano UUID casuali né contengono dati personali in chiaro:
- **Policy**: `doc::{source_name}::{file_sha256[:12]}::{chat_hash}`
  - `source_name`: identificativo canonico dell'importer (es. `msgstore_db`, `cellebrite_csv`).
  - `file_sha256[:12]`: prefisso di 12 caratteri dell'hash SHA-256 del file caricato.
  - `chat_hash`: per `UNRESOLVED` è la stringa `"unresolved"`; per altre chat è il prefisso di 12 caratteri dello SHA-256 della chiave canonica di chat (`chat_key`).

Questa policy garantisce l'assoluta stabilità e riproducibilità forense dell'analisi tra esecuzioni successive dello stesso dataset, impedendo contemporaneamente l'esposizione di identificatori personali nei metadati globali.

---

## 8. Gestione Media Fisici Non Forniti

In questa fase, i media fisici (file audio, immagini, video) non vengono caricati:
- I riferimenti agli allegati (`media_reference`) presenti in msgstore o Cellebrite vengono integralmente preservati nei `UnifiedMessage`.
- Il bundle associa solo la sezione testuale originale (`ORIGINAL_TEXT`); non vengono generati trascrizioni STT, testi OCR o descrizioni Vision sintetiche per i file reali.
- Nella Panoramica viene esposto il contatore esatto dei media reference non risolti.
- Il motore di ricerca lessicale opera integralmente sui testi originali disponibili.

---

## 9. Assenza di Connessioni di Rete e Assenza di Componenti AI

Come stabilito dai vincoli architetturali del progetto:
1. **Zero Rete**: Nessuna richiesta HTTP, download di pesi o apertura di socket viene effettuata (`test_no_network_and_no_ai_invoked` lo garantisce categoricamente a livello di test).
2. **Zero Invocazione AI**: Né `LmStudioClient` né `FakeLocalLlmClient` vengono istanziati durante l'ingestion o l'analisi di file reali.
3. **Topic Results Vuoti**: Per i dataset reali caricati da file, `detection_results = ()` e `discovery_results = ()`. La schermata Topic informa chiaramente l'utente che l'inferenza con modelli locali è differita alla fase sperimentale finale.
4. **Ricerca Lessicale Attiva**: Il motore di ricerca deterministico (Search Layer approvato) opera pienamente sui testi dei messaggi reali estratti.

---

## 10. Limiti della Fase

1. **Media Parsing Differito**: L'estrazione e il processing multimodale da payload binari fisici (immagini e audio effettivi) è delegata a fasi successive.
2. **Benchmark LM Studio Differito**: La valutazione empirica con modelli locali (GGUF/llama.cpp) su modelli reali (Llama, Qwen, DeepSeek) è differita alla fase finale con workstation dotata di set istruzioni AVX2.
