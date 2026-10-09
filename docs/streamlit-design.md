# Streamlit UI Foundation Design

## 1. Visione d'Insieme & Obiettivi

La **Streamlit UI Foundation** costituisce il layer di presentazione e interazione locale per la pipeline di analisi forense e ricerca chat. Essa fornisce agli analisti un'interfaccia grafica web locale, fluida e sicura, mantenendo al tempo stesso una separazione categorica rispetto ai contratti di dominio, alla logica di business e ai motori di ricerca sottostanti.

### Obiettivi Primari:
- **Operatività 100% Locale e Offline**: Nessuna connessione remota, nessuna dipendenza da CDN esterne, nessun codice JavaScript custom, nessun impiego di `unsafe_allow_html=True`.
- **Disaccoppiamento Rigoroso**: La logica applicativa, l'aggregazione dei dati e i filtri risiedono in moduli puri e testabili (`ui.application`, `ui.demo`, `ui.models`), senza dipendenza dal framework Streamlit.
- **Integrazione Trasparente con Search Layer**: L'interfaccia delega interamente l'indicizzazione e l'interrogazione delle evidenze e dei topic al facade deterministico `search.SearchService`.
- **Modalità Dimostrativa Sintetica**: L'app opera immediatamente anche in assenza di file reali, mediante un generatore di conversazioni sintetiche multilingua (`ui.demo`) conforme a tutti i contratti e alle invarianti di provenance.
- **Modalità Real File Ingestion (OPERATIVA)**: Supporto nativo ed end-to-end al caricamento sicuro di file reali WhatsApp (`msgstore.db` con eventuale companion `wa.db`, `wa.db` standalone, oppure esportazioni native in formato **TXT** o **ZIP** con allegati) e Cellebrite (CSV, JSON, XML), instradati attraverso la pipeline completa di validazione, normalizzazione, entity resolution ed evidence bundling, con selezione multi-conversazione e ricerca deterministica.
- **Privacy Hardening e Pseudonimizzazione**: Nessuna informazione identificativa (JID, numeri telefonici, titoli chat grezzi) viene esposta nei selettori UI o nei campi principali di riepilogo. Gli identificatori tecnici sono isolati in sezioni espandibili di provenance interna.
- **Rinvio Trasparente del Benchmark Reale LM Studio**: Dichiarazione esplicita del rinvio dell'inferenza con modelli GGUF/llama.cpp a causa dei limiti di set istruzioni della macchina host (AMD A8-7410 priva di AVX2), con conferma dello stato **REAL PILOT READY** per l'architettura AI.

---

## 2. Architettura del Package `ui`

```
app.py                     # Entry point dell'applicazione (configurazione pagina, sidebar e routing)
ui/
├── __init__.py            # Export pubblici dei modelli e costrutti UI
├── models.py              # DTO, enum e strutture dati di presentazione pure (frozen dataclass)
├── state.py               # Adapter centralizzato e tipizzato per st.session_state
├── demo.py                # Generatore deterministico del dataset sintetico conforme ai modelli dominio
├── ingestion.py           # Application bridge e pipeline runner offline per i file forensi reali
├── application.py         # Application layer puro e testabile (funzioni pure di ricerca, filtri, riepilogo)
└── presentation.py        # Componenti grafici e renderer Streamlit per ciascuna schermata
```

### Separazione UI vs Domain
I moduli del core forense (`core/`, `importer/`, `validation/`, `normalization/`, `entity_resolution/`, `unified/`, `multimodal/`, `ai/`, `search/`) **non importano Streamlit** né conoscono costrutti dell'interfaccia utente. Streamlit è configurato come dipendenza opzionale in `pyproject.toml` sotto il gruppo `[project.optional-dependencies].ui`.

---

## 3. Gestione dello Stato di Sessione (`ui/state.py`)

Lo stato di sessione Streamlit è incapsulato da un adapter tipizzato che bandisce l'uso di stringhe magiche sparse nel codice.

| Chiave di Stato | Tipo | Descrizione |
|---|---|---|
| `dataset_loaded` | `bool` | Flag indicante se un dataset è attualmente disponibile in memoria |
| `dataset_mode` | `DatasetMode` | Enum (`NONE`, `DEMO`, `FILE`) indicante la modalità corrente |
| `conversation_document` | `ConversationEvidenceDocument \| None` | Documento forense attivo |
| `detection_results` | `tuple[TopicDetectionResult, ...]` | Risultati tematici verificati |
| `discovery_results` | `tuple[TopicDiscoveryResult, ...]` | Argomenti emersi da discovery |
| `search_service` | `SearchService \| None` | Facade autoritativo di ricerca |
| `selected_evidence_id` | `str \| None` | Evidenza selezionata per ispezione forense |
| `real_ingestion_result` | `IngestionResult \| None` | Risultato completo dell'ingestion reale |
| `ingestion_summary` | `IngestionSummary \| None` | Metadati aggregati dell'ingestion reale |
| `available_documents` | `dict[str, ConversationEvidenceDocument]` | Documenti conversazione estratti |
| `selected_document_id` | `str \| None` | Document ID correntemente visualizzato |
| `error_message` | `str \| None` | Ultimo messaggio di errore applicativo controllato |
| `custom_topic_label` | `str` | Argomento libero inserito nell'Area 1 di Topic Detection |
| `custom_topic_description` | `str` | Descrizione opzionale dell'argomento per la verifica mirata |
| `last_manual_detection` | `TopicDetectionResult \| None` | Ultimo verdetto di Topic Detection generato on-demand |
| `lm_studio_client` | `BaseLlmClient \| None` | Istanza client LM Studio (o mock di test iniettato) |
| `lm_studio_model` | `str \| None` | Modello LM Studio configurato per l'inferenza |

---

## 4. Pagine e Flusso di Navigazione

### 1. Panoramica (`render_overview`):
- Indicatori numerici principali: messaggi (bundle tramite `bundle_count`), sezioni di evidenza, topic detection, argomenti scoperti.
- Ripartizione per tipologia di evidenza (`ORIGINAL_TEXT`, `STT_TRANSCRIPTION`, `OCR_TEXT`, `VISION_DESCRIPTION`, `VISION_OBSERVATION`).
- Lingue identificate nel corpus.
- **Identificativi e Provenance Protetta**:
  - `Document ID` e `Sorgente Forense` in campi disabilitati a sola lettura.
  - `Conversazione`: mostra la `display_label` rigorosamente pseudonimizzata (es. `Conversazione 1 — 168 messaggi — [doc::...]`).
  - **Nessun JID, titolo raw o numero telefonico** compare nei controlli top-level.
  - L'eventuale `chat_id` tecnico di dominio è accessibile esclusivamente all'interno dell'expander dedicato: *"Provenance Tecnica (Metadati Interni)"*.

### 2. Importazione (`render_import`):
- Area Demo: caricamento e reset istantaneo del dataset sintetico multilingua.
- Area File Reale:
  - Selettore del formato sorgente (`WhatsApp msgstore`, `WhatsApp wa.db`, `WhatsApp export chat (TXT / ZIP)`, `Cellebrite CSV`, `Cellebrite JSON`, `Cellebrite XML`).
  - Uploader primario per il file di archivio/database/testo.
  - Uploader secondario opzionale per il companion `wa.db` (visibile solo se `WHATSAPP_MSGSTORE`).
  - Riepilogo post-ingestion: record raw, normalizzati, messaggi unificati, conversazioni estratte, validation issues, SHA-256 e avvisi non bloccanti.
  - **Gestione Companion wa.db Failure**: se il file companion è incompatibile o corrotto, non blocca msgstore; viene visualizzato un warning esplicito e non sensibile negli avvisi.
  - **Selettore Conversazione Pseudonimizzato**: selectbox formattata con etichette pseudonimizzate stabili basate sul campo canonico `c.bundle_count`.

### 3. Esplora conversazione (`render_explore`):
- Navigazione delle sezioni nell'ordine naturale di acquisizione forense.
- Filtri combinabili per tipologia di sorgente probatoria, lingua e sorgente tecnica.
- Scheda di dettaglio espandibile con la catena di provenance completa.

### 4. Ricerca (`render_search`):
- Delega esclusiva a `SearchService.search_evidence()`.
- Supporto alle 4 modalità deterministiche: `PHRASE`, `ALL_TERMS`, `ANY_TERM`, `EXACT`.
- Parametro `limit` con tracciamento trasparente di `total_hits` vs `returned_hits`.

### 5. Analisi topic (`render_topics`):
- **Tab Topic Detection (Verifica Mirata)** strutturata in due aree:
  - **Area 1: Verifica un argomento**: input per argomento libero, textarea per descrizione opzionale, selettore modello LM Studio, pulsante "Esegui Topic Detection". Esegue una vera inferenza locale su LM Studio (`TopicDetectionAnalyzer.detect_topic()`), visualizzando esito (`PRESENT`, `ABSENT`, `UNCERTAIN`), rationale in italiano, modello, latenza ed evidenze originali espandibili. I risultati vengono registrati nello stato di sessione e indicizzati nel Search Service senza alterare il documento originale.
  - **Area 2: Filtra risultati Detection già eseguiti**: filtri decisionali e ricerca testuale sui risultati preesistenti o generati nella sessione tramite `search_topic_detections()`.
- **Tab Topic Discovery (Argomenti Emersi)**:
  - Visualizzazione dei cluster e temi estratti autonomamente;
  - Per ciascun argomento emerso, pulsante dedicato **"Verifica con Topic Detection"** che preleva label e descrizione e avvia una nuova inferenza mirata sul documento forense attivo.
- In modalità `FILE`, informa chiaramente l'operatore che l'inferenza AI batch precalcolata non è stata eseguita durante l'ingestion, lasciando piena operatività alla verifica on-demand.

### 6. Sistema / Stato (`render_status`):
- Scheda diagnostica delle componenti forensi e della versione di Streamlit.
- Dichiarazione trasparente dell'incompatibilità hardware AVX2 della CPU AMD A8-7410 per il runtime LM Studio.

---

## 5. Gestione Errori Sanitizzata e Privacy Forense

### 5.1 Error Handling Rigorosamente Sanitizzato
La UI adotta una cattura differenziata e sicura delle eccezioni sollevate dal bridge di ingestion (`ui.ingestion`), eliminando qualsiasi rischio di `AttributeError` o fuga di informazioni:

- **PipelineStageError**: Mostra lo stage fallito e il tipo di errore con il messaggio sicuro associato:
  `[{err.stage}] PipelineStageError: {err.safe_message}`.
  Il testo raw dell'eccezione originaria (`str(exc)`), percorsi filesystem temporanei e dettagli interni non vengono mai renderizzati a video.
- **InvalidUploadedFileError**: Mostra il tipo e il messaggio utente controllato:
  `InvalidUploadedFileError: {err.safe_message}`.
- **UnsupportedSourceFormatError**: Mostra il tipo e il messaggio controllato:
  `UnsupportedSourceFormatError: {err.safe_message}`.
- **IngestionError Generico**: Mostra il messaggio controllato:
  `IngestionError: {err.safe_message}`.
- **Eccezioni Impreviste**: Mascheramento automatico del contenuto, visualizzando unicamente il nome della classe dell'eccezione (`type(exc).__name__`).

### 5.2 Hardening di Configurazione Locale
Configurazione formalizzata in `.streamlit/config.toml`:
- `server.address = "127.0.0.1"` e `browser.serverAddress = "127.0.0.1"` (loopback-only assoluto).
- `browser.gatherUsageStats = false` (telemetria disabilitata).
- `client.showErrorDetails = "none"` (soppressione traceback a schermo).
- `server.enableCORS = true` e `server.enableXsrfProtection = true` (protezioni web standard attive).

---

## 6. Rinvio Benchmark LM Studio Reale (Hardware Limitation)

- **Processore host**: AMD A8-7410 APU with AMD Radeon R5 Graphics.
- **Causa tecnica**: Il runtime GGUF/llama.cpp integrato nelle versioni correnti di LM Studio richiede istruzioni AVX2 per la decodifica dei pesi quantizzati. La CPU in uso supporta esclusivamente AVX, provocando l'errore `Invalid CPU architecture`.
- **Stato**: Il benchmark reale con pesi modello (Llama, Qwen, DeepSeek) è categoricamente **DEFERRED** alla fase finale del progetto su workstation/server idoneo.
- **AI Layer Status**: L'architettura del Local AI Layer (`ai/`) è strutturalmente e funzionalmente **REAL PILOT READY**.
