# Streamlit UI Foundation Design

## 1. Visione d'Insieme & Obiettivi

La **Streamlit UI Foundation** costituisce il layer di presentazione e interazione locale per la pipeline di analisi forense e ricerca chat. Essa fornisce agli analisti un'interfaccia grafica web locale, fluida e sicura, mantenendo al tempo stesso una separazione categorica rispetto ai contratti di dominio, alla logica di business e ai motori di ricerca sottostanti.

### Obiettivi Primari:
- **Operatività 100% Locale e Offline**: Nessuna connessione remota, nessuna dipendenza da CDN esterne, nessun codice JavaScript custom, nessun impiego di `unsafe_allow_html=True`.
- **Disaccoppiamento Rigoroso**: La logica applicativa, l'aggregazione dei dati e i filtri risiedono in moduli puri e testabili (`ui.application`, `ui.demo`, `ui.models`), senza dipendenza dal framework Streamlit.
- **Integrazione Trasparente con Search Layer**: L'interfaccia delega interamente l'indicizzazione e l'interrogazione delle evidenze e dei topic al facade deterministico `search.SearchService`.
- **Modalità Dimostrativa Sintetica**: L'app si avvia e opera immediatamente anche in assenza di file reali, mediante un generatore di conversazioni sintetiche multilingua (`ui.demo`) conforme a tutti i contratti e alle invarianti di provenance.
- **Preparazione Disaccoppiata all'Ingestion Reale**: Presenza di un'interfaccia preparatoria per il caricamento futuro di archivi WhatsApp (msgstore, wa.db) e Cellebrite (CSV, JSON, XML), senza simulare ingestion non ancora collegate.
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
├── application.py         # Application layer puro e testabile (funzioni pure di ricerca, filtri, riepilogo)
└── presentation.py        # Componenti grafici e renderer Streamlit per ciascuna schermata
```

### Separazione UI vs Domain
I moduli del core forense (`core/`, `importer/`, `validation/`, `normalization/`, `entity_resolution/`, `unified/`, `multimodal/`, `ai/`, `search/`) **non importano Streamlit** né conoscono costrutti dell'interfaccia utente. Streamlit è configurato come dipendenza opzionale in `pyproject.toml` sotto il gruppo `[project.optional-dependencies].ui`.

---

## 3. Modalità Dimostrativa Sintetica (`ui/demo.py`)

La modalità demo costruisce in memoria:
- Un `ConversationEvidenceDocument` con 8 messaggi e 12 `TextEvidenceSection` conformi a:
  - Lingua Italiana (`it`), Inglese (`en`), Spagnolo (`es`) e testo misto (`it-en`).
  - Sorgenti probatorie tipizzate: `ORIGINAL_TEXT`, `STT_TRANSCRIPTION`, `OCR_TEXT`, `VISION_DESCRIPTION`, `VISION_OBSERVATION`.
- 3 risultati di **Topic Detection** mirata (`TopicDetectionResult`):
  - `PRESENT`: "Accordo Riservato" (collegato al testo msg::1 e alla trascrizione audio msg::6).
  - `ABSENT`: "Attività di Contrabbando" (nessun evidence_id collegato, stringa rationale esplicita).
  - `UNCERTAIN`: "Transazioni Finanziarie Sospette" (collegato a OCR fattura msg::3 e budget msg::4).
- 1 risultato di **Topic Discovery** aperta (`TopicDiscoveryResult`):
  - "Logistica e Ispezione Depositi" (collegato a osservazioni Vision msg::5).
  - "Gestione Contratti e Bozze" (collegato a STT msg::2 e testo spagnolo msg::8).
- **Integrità Referenziale Rigorosa**: Tutti gli `evidence_id` citati sono realmente indicizzati e risolvibili tramite `EvidenceIndex.resolve()`. `provenance_document_id` corrisponde esattamente al `document_id` del documento.

---

## 4. Gestione dello Stato di Sessione (`ui/state.py`)

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
| `error_message` | `str \| None` | Ultimo messaggio di errore applicativo controllato |

Tutte le funzioni di `ui/state.py` accettano un parametro opzionale `state: MutableMapping[str, Any]`, consentendo test unitari con normali dizionari Python senza avviare il server Streamlit.

---

## 5. Pagine e Flusso di Navigazione

1. **Panoramica**:
   - Indicatori numerici principali: messaggi (bundle), sezioni di evidenza, topic detection, argomenti scoperti.
   - Ripartizione per tipologia di evidenza (`ORIGINAL_TEXT`, `STT_TRANSCRIPTION`, `OCR_TEXT`, `VISION_DESCRIPTION`, `VISION_OBSERVATION`).
   - Lingue identificate nel corpus.
   - Call to action immediata per caricare il dataset demo se non presente.
2. **Importazione**:
   - Area Demo: caricamento e reset istantaneo del dataset sintetico.
   - Area File: selettore del formato sorgente (`WhatsApp msgstore`, `WhatsApp wa.db`, `Cellebrite CSV`, `Cellebrite JSON`, `Cellebrite XML`) e file uploader preparatorio con notifica di differimento al prossimo step.
3. **Esplora conversazione**:
   - Navigazione delle sezioni nell'ordine naturale di acquisizione forense (nessun riordinamento arbitrario per timestamp).
   - Filtri combinabili per tipologia di sorgente probatoria, lingua e sorgente tecnica.
   - Scheda di dettaglio espandibile con l'intera catena di provenance (`evidence_id`, `message_id`, `source_record_id`, `source_name`, `ordinal`).
4. **Ricerca**:
   - Delega esclusiva a `SearchService.search_evidence()`.
   - Supporto alle 4 modalità deterministiche: `PHRASE`, `ALL_TERMS`, `ANY_TERM`, `EXACT`.
   - Parametro `limit` con conteggio trasparente di `total_hits` vs `returned_hits` e tracciamento di eventuale troncamento (`truncated`).
   - Gestione sicura di query vuote senza sollevare eccezioni a runtime.
5. **Analisi topic**:
   - Tab "Topic Detection": filtro per decisione (`ALL`, `PRESENT`, `ABSENT`, `UNCERTAIN`) e ricerca testuale su label/descrizione.
   - Tab "Topic Discovery": argomenti emergenti e sintesi.
   - Distinzione visiva immediata: badge di stato (`🟢 PRESENTE`, `⚪ ASSENTE`, `🟡 INCERTO`), testo descrittivo AI isolato e contenitori dedicati per le **Evidenze Originali Collegate**.
6. **Sistema / Stato**:
   - Scheda diagnostica delle componenti.
   - Tracciamento della versione di Streamlit installata.
   - Documentazione trasparente dell'incompatibilità hardware AVX2 della CPU AMD A8-7410 per il benchmark GGUF reale.

---

## 6. Privacy & Sicurezza Forense (Hardening Locale)

La configurazione di sicurezza è formalizzata ed esecutiva tramite `.streamlit/config.toml`:
- **Binding Loopback-Only (127.0.0.1)**: L'applicazione è vincolata esclusivamente all'interfaccia locale (`server.address = "127.0.0.1"`, `browser.serverAddress = "127.0.0.1"`). È categoricamente escluso qualsiasi binding su `0.0.0.0` o interfaccia broadcast LAN.
- **Telemetria e Statistiche d'Uso Disabilitate**: Raccolta statistiche categoricamente disattivata (`browser.gatherUsageStats = false`). Nessun ping o dato d'uso viene trasmesso a server Streamlit/terzi.
- **Mascheramento dei Dettagli di Errore**: Configurazione `client.showErrorDetails = "none"` per impedire data leakage di traceback, percorsi di sistema o stringhe di query nell'interfaccia grafica client.
- **Preservazione Protezioni Web (CORS & XSRF)**: Protezioni attive e intatte (`server.enableCORS = true`, `server.enableXsrfProtection = true`) contro attacchi cross-origin o richieste forzate non autorizzate.
- **Zero Logging Sensibile**: Nessun testo di chat, trascrizione vocale, OCR o query viene registrato nei log di console o su file.
- **Nessuna Rete Esterna**: L'applicazione non esegue chiamate HTTP/HTTPS in uscita né utilizza CDN remote.
- **Nessuna Persistenza Non Autorizzata**: Le evidenze e i file caricati non vengono scritti su disco.
- **Nessun HTML Incontrollato**: Assenza totale di `unsafe_allow_html=True` e di script JavaScript iniettati.

---

## 7. Rinvio Benchmark LM Studio Reale (Hardware Limitation)

- **Processore host**: AMD A8-7410 APU with AMD Radeon R5 Graphics.
- **Causa tecnica**: Il runtime GGUF/llama.cpp integrato nelle versioni correnti di LM Studio richiede istruzioni AVX2 per la decodifica dei pesi quantizzati. La CPU in uso supporta esclusivamente AVX, provocando l'errore `Invalid CPU architecture`.
- **Stato**: Il benchmark reale con pesi modello (Llama, Qwen, DeepSeek) è categoricamente **DEFERRED** alla fase finale del progetto su workstation/server idoneo.
- **AI Layer Status**: L'architettura del Local AI Layer (`ai/`) è strutturalmente e funzionalmente **REAL PILOT READY**.

---

## 8. Limiti della Foundation Attuale & Prossimi Passi

- **Real File Ingestion**: L'ingestion reale dei database WhatsApp e dei report Cellebrite tramite gli importer è preparata a livello di interfaccia ma differita alla fase successiva (**DEFERRED TO NEXT PHASE**).
- La ricerca è puramente lessicale e deterministica (nessun vettore, nessun embedding, nessun database vettoriale).
