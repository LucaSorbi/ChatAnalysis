# AUDIT DEL DEBITO ARCHITETTURALE E VALUTAZIONE STRUTTURALE (FASE 26-29)

Questo documento costituisce l'analisi critica, scientifica e formale del debito tecnico accumulato nel repository ChatAnalysis. Valuta ciascun sottosistema secondo la classificazione tassativa: **KEEP**, **REFACTOR** o **REWRITE**, analizza i sintomi di patchwork e propone un'architettura target pulita.

---

## 1. Valutazione Sistematica per Aree (KEEP / REFACTOR / REWRITE)

### 1. `ui/presentation.py`
* **STATUS**: **REWRITE**
* **WHY**: File monolitico di oltre 1050 righe. Mescola layout Streamlit, logica di business, orchestrazione dei modelli LM Studio, calcolo al volo della consistency, gestione delle eccezioni e mutazione dello stato. Contiene l'obsoleto banner di blocco (*"Il benchmark/inference con modelli locali è differito alla fase sperimentale finale"*), non espone pulsanti per la Discovery live e impedisce una reale separazione tra View e Controller.
* **CURRENT RESPONSIBILITIES**: Rendering di tutte le 6 pagine, dispatch ingestion, avvio topic detection, ricerca topic, calcolo consistency, UX progression modello.
* **DESIRED RESPONSIBILITIES**: Puro strato di presentazione (View). Riceve ViewModel tipizzati e delega qualsiasi azione a un Application Service.
* **COUPLING**: Accoppiato direttamente a `st.session_state`, `ui/state.py`, `ui/application.py`, `search.service`, `ai.backend`, `ai.lmstudio`.
* **DUPLICATION**: Duplicazione di accessi di stato, controlli di disponibilità e logica di formattazione.
* **LEGACY**: Banner fase sperimentale differita (riga 544), vecchie sezioni rimosse a pezzi.
* **SAFE CONTRACTS TO PRESERVE**: Nomi e layout delle pagine, badge `PRESENT`/`ABSENT`/`UNCERTAIN`, expander evidenze originali.
* **TESTS TO SURVIVE**: `test_ui_smoke.py`, AppTest di navigazione.
* **TESTS OBSOLETE**: Test che pretendono la comparsa di stringhe storiche di differimento.

---

### 2. `ui/application.py`
* **STATUS**: **REFACTOR**
* **WHY**: Svolge un ruolo di orchestrazione ibrido e incompleto: gestisce il preflight del modello e l'avvio della sola Detection manuale, ma è privo di qualsiasi supporto per la Topic Discovery, non espone un servizio di consistenza e mescola funzioni utility con logica applicativa.
* **CURRENT RESPONSIBILITIES**: `prepare_operational_model()`, `build_topic_query()`, `validate_discovery_provenance()`, `execute_manual_topic_detection()`.
* **DESIRED RESPONSIBILITIES**: Application Service / Facade unificato che orchestra `TopicDetectionService`, `TopicDiscoveryService` e `ConsistencyService`.
* **COUPLING**: Accoppiato a `ai/lmstudio.py` e `ai/topics.py`.
* **SAFE CONTRACTS TO PRESERVE**: `build_topic_query()`, `validate_discovery_provenance()`, parametri operativi (timeout 240s, max_tokens 512).

---

### 3. `ui/state.py`
* **STATUS**: **REWRITE**
* **WHY**: 17 chiavi piatte globali in `st.session_state`. Il grave limite strutturale è che `detection_results` e `discovery_results` sono liste piatte non indicizzate per `document_id`. Per evitare che i risultati della Chat A compaiano nella Chat B, `select_conversation()` azzera brutalmente tutti i risultati (`target[KEY_DETECTION_RESULTS] = ()`), causando la perdita totale delle analisi al cambio di conversazione.
* **CURRENT RESPONSIBILITIES**: Wrapper dizionario intorno a `st.session_state`.
* **DESIRED RESPONSIBILITIES**: Mantenere un oggetto di sessione strutturato (`AnalysisSession`) contenente repository in-memory con risultati indicizzati per `document_id`.
* **COUPLING**: Accoppiato a Streamlit.

---

### 4. `ui/ingestion.py`
* **STATUS**: **KEEP**
* **WHY**: Componente solido, ben ingegnerizzato, privo di AI, con sandbox temporanea isolata, calcolo streaming SHA-256, chunking a 1 MB, verifica preliminare delle signature e supporto a file fino a 2 GB.
* **CURRENT RESPONSIBILITIES**: Riceve stream, scrive temporaneo, valida signature, invoca importer, valida, normalizza, risolve entità e costruisce `ConversationEvidenceDocument`.
* **DESIRED RESPONSIBILITIES**: Invariate (strato ingestion computazionale offline deterministico).

---

### 5. `ai/lmstudio.py`
* **STATUS**: **KEEP**
* **WHY**: Recentemente ripulito e reso rigoroso: payload conforme allo schema REST nativo v1 di LM Studio (`model`, `context_length`, `echo_load_config`), assenza tassativa di chiavi CLI non valide (`identifier`, `gpu_offload`), validazione loopback rigorosa, auto-load deterministico senza download automatici da remoto.

---

### 6. `ai/topics.py`
* **STATUS**: **REFACTOR**
* **WHY**: Contiene la logica fondamentale di `TopicDetectionAnalyzer` e `TopicDiscoveryAnalyzer`. La logica di prompt operativo multimodale è stata innestata tramite flag condizionale `operational_mode`. Si raccomanda di separare formalmente i prompt builder operativi da quelli del benchmark e integrare il chunking per conversazioni che eccedono il context window.

---

### 7. `ai/serializer.py`
* **STATUS**: **KEEP**
* **WHY**: Componente deterministico e sicuro: serializza tutte le 5 sorgenti di evidenza, applica delimitatori anti-injection ed esegue escaping JSON rigoroso senza alcun fallback degradato.

---

### 8. `ai/chunking.py`
* **STATUS**: **REWRITE / REFACTOR PROFONDO**
* **WHY**: **Componente orfano / disconnesso**. Esiste a livello di codice ed è testato in isolamento, ma non viene MAI invocato né da `TopicDetectionAnalyzer` né da `TopicDiscoveryAnalyzer`. Quando una chat reale supera gli 8192 token, l'intero payload viene inviato in blocco, rischiando troncamenti silenziosi o errori HTTP sul backend LLM. Deve essere riscritto o integrato attivamente nella pipeline di analisi.

---

### 9. `SearchService` Integration
* **STATUS**: **KEEP**
* **WHY**: Servizio di ricerca deterministico offline con supporto multi-modalità (`EXACT`, `PHRASE`, `ALL_TERMS`, `ANY_TERM`), indicizzazione immediata dei topic e zero dipendenze da LLM.

---

### 10. Topic Analysis Orchestration
* **STATUS**: **REWRITE**
* **WHY**: Attualmente frammentata e parziale: la Detection è invocabile dalla UI, la Discovery è precalcolata solo in DEMO e assente in FILE, e la consistency è un blocco di visualizzazione IF/ELSE in `presentation.py`. Deve convergere in orchestratori dedicati.

---

### 11. Gestione `DatasetMode` (DEMO vs FILE)
* **STATUS**: **REFACTOR**
* **WHY**: Asimmetria ingiustificata: una volta creato un `ConversationEvidenceDocument`, il layer di analisi e i servizi dovrebbero essere **identici al 100%** sia per DEMO che per FILE. Attualmente FILE subisce controlli speciali, banner di differimento e l'esclusione della Topic Discovery.

---

### 12. Workflow Discovery -> Detection e Consistency
* **STATUS**: **REFACTOR**
* **WHY**: Il principio della "prova del nove" (nuova inferenza indipendente) è valido e implementato correttamente, ma è circoscritto alla sola modalità DEMO e manca di una classe di dominio formale (`ConsistencyResult`, `ConsistencyService`).

---

### 13. WhatsApp Export Ingestion (`importer/whatsapp_export.py`)
* **STATUS**: **KEEP**
* **WHY**: Parsing robusto per esportazioni TXT (Android/iOS) e archivi ZIP, con difese anti zip-slip, anti zip-bomb, gestione media references e streaming crittografico.

---

## 2. Diagnosi del Patchwork: Sintomi di Sviluppo Incrementale

L'audit del codice evidenzia inequivocabilmente che il progetto ha subito uno sviluppo per fasi successive mediante stratificazione di patch cumulative:
1. **Banner Obsoleto Non Rimosso**: Il messaggio *"Il benchmark/inference con modelli locali è differito alla fase sperimentale finale"* (`presentation.py:544`) è un residuo delle prime fasi che blocca psicologicamente l'utente su file reali.
2. **Chunking "Sulla Carta"**: `ai/chunking.py` è stato sviluppato e testato per soddisfare una specifica fase, ma non è mai stato collegato agli analizzatori attivi.
3. **Reset Distruttivo dello Stato**: Invece di creare un dizionario indicizzato per conversazione, `select_conversation()` resetta i risultati a tupla vuota per evitare la comparsa di evidenze di altre chat.
4. **Discovery Assente su File Reali**: Topic Discovery è stata implementata a livello di motore AI (`ai/topics.py`), ma nella UI è stata collegata solo la lettura dei risultati precalcolati della demo.

---

## 3. Architettura Target Proposta (Pulita e Disaccoppiata)

```text
                      FONTE DATI (FILE REALE o DEMO SINTETICO)
                                         |
                                         v
                         INGESTION SERVICE (Offline & Deterministico)
                                         |
                                         v
                           ConversationEvidenceDocument
                                         |
                                         v
                                  ANALYSIS SESSION
                   (Repository in-memory scoped per document_id)
                                         |
                      +------------------+------------------+
                      |                                     |
                      v                                     v
            TOPIC DISCOVERY SERVICE               TOPIC DETECTION SERVICE
             (Chiamata LLM Locale)                 (Chiamata LLM Locale)
                      |                                     |
                      +------------------+------------------+
                                         |
                                         v
                                CONSISTENCY SERVICE
                  (Confronto formale indipendente Discovery -> Detection)
                                         |
                                         v
                                   SEARCH SERVICE
                     (Indicizzazione evidenze e verdetti AI)
                                         |
                                         v
                              STREAMLIT PRESENTATION
                       (Solo Rendering View & Event Dispatcher)
```

### Vantaggi dell'Architettura Target:
* **Zero Patchwork**: Elimina gli `if DatasetMode.FILE` sparsi nella UI; dopo la creazione del documento, la pipeline è rigorosamente identica.
* **Preservazione Dati Multi-Chat**: I risultati sono memorizzati per `document_id`; cambiare chat non cancella nulla.
* **Discovery Completa su File Reali**: Permette di avviare Topic Discovery su qualsiasi acquisizione reale.
* **Separazione dei Compiti**: `ui/presentation.py` si riduce da 1050 a meno di 300 righe di puro layout.

---

## 4. Safe Contracts da Preservare Assolutamente

Qualsiasi futuro refactoring o riscrittura deve mantenere intatti i seguenti contratti di dominio già validati:
1. `ConversationEvidenceDocument` immutabile (`frozen dataclass`).
2. Schema `RawRecord`, `ValidationResult`, `NormalizedRecord`, `UnifiedMessage`, `MessageEvidenceBundle`.
3. Ingestion offline e deterministica (zero chiamate AI durante l'importazione).
4. Indicizzazione e ricerca deterministica in `SearchService`.
5. Topic Detection tri-stato (`PRESENT`, `ABSENT`, `UNCERTAIN`) con citazioni obbligatorie.
6. Topic Discovery separata e con inferenze indipendenti per la verifica ("prova del nove").
7. Loopback-only security su `127.0.0.1:1234` in `LmStudioClient`.
8. Sicurezza sandbox e anti-zip-bomb per upload grandi file.
9. **Isolamento totale del benchmark**: `ai/experiment.py`, prompt benchmark `topic_detection_v1`, dataset `cloud_e2e` e ground truth congelati.
