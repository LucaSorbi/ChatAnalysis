# AGENT REPORT — STATO ATTUALE DEL PROGETTO CHATANALYSIS

> **Documento**: Report Tecnico Autoritativo di Avanzamento e Stato del Sistema  
> **Repository**: `ChatAnalysis`  
> **Data Aggiornamento**: Ottobre 2026  
> **Stato Pipeline**: REAL PILOT READY & FULLY OPERATIONAL (100% Offline; Ingestion & Search Deterministici; Inferenza LLM Locale e Controllata On-Demand)

---

## 1. Executive Summary & Stato Corrente del Repository

Il presente report documenta lo stato tecnico, architetturale e operativo corrente del progetto di tesi **ChatAnalysis**.

Il sistema realizza una pipeline modulare, verificabile e interamente locale per l'acquisizione, normalizzazione, correlazione multimodale, ricerca deterministica e analisi semantica (Topic Detection e Topic Discovery) di conversazioni forensi.

### Punti Chiave dello Stato Corrente:
1. **Real File Ingestion Multiformato**: Supporto nativo ed end-to-end per database WhatsApp (`msgstore.db` e `wa.db`), export WhatsApp diretti (formati **TXT** e **ZIP** con allegati) e reportistica Cellebrite (**CSV**, **JSON**, **XML**).
2. **Architettura AI a Due Livelli**:
   - **Topic Discovery**: scansione open-ended / open exploration dei temi emergenti (`DiscoveredTopic`).
   - **Topic Detection**: verifica mirata, controllata e on-demand a tre stati (`PRESENT`, `ABSENT`, `UNCERTAIN`) con citazioni vincolanti delle evidenze.
3. **Workflow Operativo Discovery → Detection**: possibilità di selezionare qualsiasi argomento emerso in Topic Discovery e sottoporlo a una *nuova inferenza indipendente* di Topic Detection, oppure di inserire un argomento libero direttamente nella UI.
4. **Isolamento LM Studio Locale (Loopback Only)**: l'inferenza opera esclusivamente su server locale LM Studio (`http://127.0.0.1:1234`). Nessun servizio cloud, nessuna telemetria e nessuna chiamata AI automatica durante l'ingestion o l'importazione dei file.
5. **Integrità e Immutabilità Forense**: tutti i documenti forensi (`ConversationEvidenceDocument`) rimangono congelati e immutabili. I risultati di Topic Detection generati manualmente vengono archiviati nello stato di sessione e indicizzati in tempo reale nel Search Service senza alterare il dato originale.
6. **Validazione Rigorosa**: la suite di test conta **1226 test superati con successo** (**0 failed, 0 errors**), coprendo l'intera catena da ingestion a UI, sicurezza sandbox, assenza di chiamate di rete e isolamento dei benchmark.

---

## 2. Architettura della Pipeline Forense

Il sistema adotta una pipeline unidirezionale, disaccoppiata e a responsabilità singola, dove ogni fase consuma e produce modelli di dominio immutabili (`frozen dataclass`).

### 2.1 Diagramma della Pipeline Completa

```
+-------------------------------------------------------------------------------+
|                     DATI ORIGINALI FORENSI (FILE REALI)                       |
|   - WhatsApp msgstore.db (SQLite)       - WhatsApp export chat (.txt)         |
|   - WhatsApp wa.db (SQLite contatti)    - WhatsApp export archivio (.zip)     |
|   - Cellebrite messages.csv             - Cellebrite report.json / report.xml |
+-------------------------------------------------------------------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
|                           1. IMPORTER SPECIALIZZATI                           |
|   - WhatsAppMsgstoreImporter            - WhatsAppExportImporter              |
|   - WhatsAppWaDbImporter                - CellebriteCsv/Json/XmlImporter      |
|   OUTPUT: Stream di RawRecord (provenance originale e campi grezzi;           |
|           SHA-256 calcolato sul file sorgente nel riepilogo di importazione)  |
+-------------------------------------------------------------------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
|                           2. VALIDAZIONE FORENSE                              |
|   - RecordValidator: validazione strutturale, schema, verifica integrità      |
|   OUTPUT: ValidationResult (issues tracciati, nessuna alterazione dato)       |
+-------------------------------------------------------------------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
|                           3. NORMALIZZAZIONE CANONICA                         |
|   - RecordNormalizer: parsing timestamp, canonizzazione tipo messaggio       |
|   OUTPUT: NormalizedRecord (timestamp canonico, testo, media references)      |
+-------------------------------------------------------------------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
|                           4. ENTITY RESOLUTION                                |
|   - DeterministicEntityResolver: risoluzione partecipanti, pseudonimizzazione  |
|   - UnifiedModelBuilder: costruzione messaggi unificati                       |
|   OUTPUT: UnifiedMessage (modello canonico trasversale a tutti i formati)     |
+-------------------------------------------------------------------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
|                           5. STRATO MULTIMODALE                               |
|   - MessageEvidenceBundle: unione messaggio testuale e derivati multimodali   |
|     (Audio Transcription STT, Image OCR Text, Image Vision Description)       |
|   OUTPUT: ConversationEvidenceDocument (1 per conversazione, immutabile)       |
+-------------------------------------------------------------------------------+
                                      |
                 +--------------------+--------------------+
                 |                                         |
                 v (Offline & Deterministico)              v (Locale, Controllato & On-Demand)
+----------------------------------+     +--------------------------------------+
|        6. SEARCH SERVICE         |     |        7. LOCAL AI LAYER             |
|   - Indicizzazione evidenze      |     |   - LM Studio locale (127.0.0.1:1234)|
|   - Ricerca deterministica       |     |   - TopicDetectionAnalyzer           |
|   - Filtro decisioni Topic       |     |   - TopicDiscoveryAnalyzer           |
|   - Matching esatto/frase/termini|     |   - Inferenza SOLO su click utente   |
+----------------------------------+     +--------------------------------------+
                 |                                         |
                 +--------------------+--------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
|                         8. STREAMLIT UI (INTERFACCIA)                         |
|   - Panoramica: riepilogo metriche forensi e conteggi sezioni                 |
|   - Importazione: ingestion TXT, ZIP, SQLite, CSV, JSON, XML                  |
|   - Esplora Conversazione: scorrimento ordine naturale, filtri sorgente       |
|   - Ricerca: scansione deterministica per parola chiave e modalità matching   |
|   - Analisi Topic:                                                            |
|       * Area 1: Verifica argomento libera (nuova inferenza LLM on-demand)    |
|       * Area 2: Filtro risultati Detection già esistenti                     |
|       * Discovery: card argomenti con pulsante "Verifica con Topic Detection" |
|   - Sistema / Stato: diagnostica architetturale e vincoli hardware            |
+-------------------------------------------------------------------------------+
```

### 2.2 Distinzione Categorica: Ingestion e Ricerca vs Analisi Topic

- **Ingestion File e SearchService (Offline & Deterministici)**:
  L'importazione dei file reali e l'indicizzazione/ricerca nel `SearchService` sono al 100% offline, deterministiche e rigorose. L'ingestion non effettua chiamate a LM Studio né a modelli linguistici; elabora i dati grezzi in modo computazionale, ripetibile e privo di componenti generative fino alla generazione dei `ConversationEvidenceDocument` e al popolamento del `SearchService`.
- **Analisi Topic (Inferenza LLM Locale, Controllata e On-Demand)**:
  L'inferenza LLM è locale, controllata e attivata on-demand, operando all'interno di vincoli strutturali formali (JSON schema, citazioni obbligatorie delle evidenze), ma non deve essere qualificata come deterministica in senso rigoroso. L'attivazione del motore LLM avviene **esclusivamente su esplicita richiesta dell'operatore**, tramite click sui pulsanti dedicati nella UI ("Esegui Topic Detection" o "Verifica con Topic Detection").

---

## 3. Real File Ingestion: Formati e Funzionalità

### 3.1 Formati Sorgente Supportati

Il sistema riconosce ed elabora i seguenti formati tramite dispatch esplicito (`SourceFormat` enum):

| SourceFormat Enum | Importer Concreto | Tipo Sorgente | Contenuto e Output |
| :--- | :--- | :--- | :--- |
| `WHATSAPP_MSGSTORE` | `WhatsAppMsgstoreImporter` | SQLite `msgstore.db` | Messaggi, chat, metadati multimediali |
| `WHATSAPP_WA` | `WhatsAppWaDbImporter` | SQLite `wa.db` | Contatti e identità (companion o standalone) |
| `WHATSAPP_EXPORT` | `WhatsAppExportImporter` | TXT o archivio ZIP | Conversazione chat esportata nativamente da WhatsApp |
| `CELLEBRITE_CSV` | `CellebriteCsvImporter` | Tabellare CSV | Estrazione messaggistica UFED Cellebrite |
| `CELLEBRITE_JSON` | `CellebriteJsonImporter` | JSON strutturato | Report estrazione Cellebrite |
| `CELLEBRITE_XML` | `CellebriteXmlImporter` | XML strutturato | Report estrazione UFDR/Cellebrite |

### 3.2 Importer Dedicato WhatsApp Export (`WhatsAppExportImporter`)

Il nuovo componente `importer/whatsapp_export.py` implementa il supporto completo per le esportazioni dirette di chat WhatsApp, sia come singolo file `.txt` sia come archivio compresso `.zip`:

1. **Parsing TXT Robusto e Multiformato**:
   - Riconoscimento delle convenzioni di timestamp standard internazionali e italiane:
     - Formato standard con trattino: `DD/MM/YYYY, HH:MM - Mittente: Testo` o `DD/MM/YY, HH:MM - Mittente: Testo`
     - Formato con parentesi quadre: `[DD/MM/YYYY, HH:MM:SS] Mittente: Testo` o `[DD/MM/YY, HH:MM:SS] Mittente: Testo`
     - Formati a 12 ore con indicatore AM/PM: `M/D/YY, H:MM AM - Mittente: Testo`
   - Normalizzazione degli spazi non standard (spazi indivisibili Unicode `\u00a0`, narrow no-break space `\u202f`).
2. **Messaggi Multilinea**:
   - Le righe di testo prive di intestazione timestamp vengono identificate come prosecuzione del messaggio precedente e accumulate preservando ritorni a capo e punteggiatura.
3. **Messaggi di Sistema**:
   - Messaggi informativi generati da WhatsApp (es. *"I messaggi e le chiamate sono crittografati end-to-end"*, *"Tizio ha cambiato il suo numero"*, notifiche di gruppo) vengono rilevati e classificati con metadato `system_event = True`, preservando la cronologia senza generare mittenti fittizi.
4. **Supporto Archivi ZIP con Media**:
   - Capacità di elaborare file `.zip` generati dall'opzione "Esporta chat (Includi media)" di WhatsApp.
   - Individuazione automatica del file di testo principale (`_chat.txt` o file `.txt` radice con contenuto conversazione).
5. **Riferimenti Media (Media References)**:
   - Identificazione delle occorrenze di allegati nel testo del messaggio (es. `<allegato: audio.opus>`, `<Media omesso>`, `IMG-20240510-WA0001.jpg (file allegato)`).
   - Collegamento deterministico tra il record del messaggio e il file multimediale presente nell'archivio ZIP, popolando `media_reference`.
6. **Sicurezza Sandbox ZIP e Protezione da Attacchi**:
   - **Anti Zip-Slip**: verifica che ogni elemento estratto dall'archivio abbia un percorso strettamente relativo all'interno della cartella di destinazione. Tentativi di directory traversal (`../`, `..\\`) o percorsi assoluti provocano il rifiuto immediato del file.
   - **Anti Zip-Bomb**: limiti restrittivi sulla dimensione massima decompressa consentita e sul numero di file contenuti nell'archivio.
7. **Provenance Forense e Hashing SHA-256**:
   - Calcolo incrementale in streaming dell'hash SHA-256 e della dimensione in byte del file sorgente di ingestion.
   - L'impronta SHA-256 riguarda specificamente la sorgente/file di importazione ed è registrata nella provenance e nel riepilogo di importazione (`IngestionSummary`), non all'interno di ciascun singolo `RawRecord`.
   - Mappatura completa da `RawRecord` a `UnifiedMessage` con conservazione della sorgente originale.
8. **Sandbox Temporanea Volatile**:
   - L'ingestion opera all'interno di un contesto isolato `tempfile.TemporaryDirectory(prefix="forensic_ingest_")`.
   - La pulizia completa dei file temporanei è garantita dal blocco `finally`, assicurando l'assenza di file residui sul disco.

---

## 4. Topic Discovery e Topic Detection: Motori e Workflow

Nel sistema ChatAnalysis, l'analisi semantica delle conversazioni si fonda sulla netta separazione concettuale e algoritmica tra due paradigmi:

```
+---------------------------------------------------------------------------------------+
| TOPIC DISCOVERY                                                                       |
| - Paradigma: Open-ended / Open Exploration                                            |
| - Obiettivo: Identificare i temi emergenti trattati spontaneamente nella chat         |
| - Motore: TopicDiscoveryAnalyzer.discover_topics()                                    |
| - Output: DiscoveredTopic (label, short_description, evidence_ids correlati)          |
| - Vincolo: Nessuna ipotesi a priori richiesta dall'operatore                          |
+---------------------------------------------------------------------------------------+

+---------------------------------------------------------------------------------------+
| TOPIC DETECTION                                                                       |
| - Paradigma: Targeted Verification (ipotesi investigativa specifica)                  |
| - Obiettivo: Verificare in modo rigoroso e vincolante la presenza di un singolo topic |
| - Motore: TopicDetectionAnalyzer.detect_topic()                                       |
| - Output: TopicDetectionResult (PRESENT, ABSENT, UNCERTAIN)                           |
| - Vincolo: Citazioni vincolanti (almeno 1 evidenza per PRESENT; lista vuota per ABSENT)|
| - Spiegazione: Rationale oggettivo in lingua italiana                                 |
+---------------------------------------------------------------------------------------+
```

### 4.1 Il Nuovo Workflow Operativo: Discovery → Detection

L'interfaccia consente all'investigatore di condurre un flusso investigativo continuo e formalmente rigoroso:

1. **Scansione Iniziale**: L'utente consulta i temi emersi nella scheda **Topic Discovery**.
2. **Selezione Argomento**: Per ogni card tematica (es. *"Accordi finanziari riservati"*), l'interfaccia presenta il pulsante:
   ```
   [ 🔬 Verifica con Topic Detection ]
   ```
3. **Costruzione Deterministica della Query**: Il sistema estrae `label` e `short_description` del `DiscoveredTopic` e invoca `ui.application.build_topic_query()` per creare un nuovo `TopicQuery` validato con ID univoco deterministico.
4. **Nuova Inferenza Reale**: Viene avviata una vera esecuzione di `TopicDetectionAnalyzer.detect_topic()` sul `ConversationEvidenceDocument` attivo.
   - **NON** viene effettuata una ricerca per stringa nei risultati preesistenti.
   - **NON** avviene alcuna conversione di tipo artificiale da `DiscoveredTopic` a `TopicDetectionResult`.
   - Viene interrogato il modello LLM locale configurato su LM Studio.
5. **Verdetto Tri-Stato e Citazioni**:
   - `PRESENT`: se il modello riscontra prove univoche nel testo, accompagnato da almeno un `evidence_id` valido appartenente al documento.
   - `ABSENT`: se il tema non trova riscontro (`evidence_ids` rigorosamente vuoto).
   - `UNCERTAIN`: in caso di prove frammentarie, ambigue o parziali.
6. **Persistenza e Aggiornamento Sessione**:
   - Il nuovo `TopicDetectionResult` viene aggiunto alla sessione (`add_detection_result`) senza alterare il documento forense originale.
   - Il `SearchService` viene contestualmente sincronizzato per rendere immediatamente indicizzato e ricercabile il nuovo verdetto nell'Area 2.
   - L'esito viene mostrato all'operatore con rationale, latenza, modello ed evidenze originali espandibili.

### 4.2 Inserimento Libero di un Argomento (Area 1)

L'operatore non è vincolato ai soli argomenti proposti dal discovery: può inserire qualsiasi ipotesi investigativa formulata liberamente:
- **Argomento** (input testuale obbligatorio, es. *"Fuga di notizie riservate"*);
- **Descrizione opzionale** (textarea contestuale per guidare la verifica);
- **Pulsante "🚀 Esegui Topic Detection"**: attiva la pipeline di inferenza locale descritta al punto precedente.

---

## 5. LM Studio: Architettura Locale e Protocolli di Sicurezza

L'inferenza locale si appoggia sull'adapter `ai/lmstudio.py` (`LmStudioClient`), progettato secondo stringenti criteri di sicurezza forense:

1. **Loopback-Only Security (Gate G1)**:
   - Il client accetta rigorosamente indirizzi di loopback locale (`127.0.0.1`, `localhost`, `::1`).
   - Qualsiasi tentativo di puntamento a host remoti, LAN o cloud (es. `api.openai.com`, IP di rete locale) solleva un'eccezione `ValueError` bloccante.
2. **Zero Dipendenze Esterne**:
   - Implementato tramite libreria standard Python (`urllib.request`, `json`).
3. **Nessun Download Automatico**:
   - Il sistema ispeziona esclusivamente i modelli già scaricati e registrati nel server LM Studio locale (`/v1/models`).
4. **Nessuna Inferenza Automatica durante l'Importazione**:
   - Il caricamento di file reali WhatsApp o Cellebrite è puramente computazionale, deterministico e offline.
   - La chiamata ad LM Studio si verifica **esclusivamente su azione manuale esplicita** dell'operatore.
5. **Tassonomia e Gestione Errori Controllata**:
   - Server offline o non avviato: intercettato come `LmStudioUnavailableError`, visualizzato all'utente con avviso chiaro ("LM Studio non raggiungibile su http://127.0.0.1:1234").
   - Nessun modello caricato: intercettato come `AiModelNotSpecifiedError`, visualizzato con messaggio di istruzione all'utente senza crash dell'applicazione.
   - Timeout di elaborazione: gestito tramite `AiBackendTimeoutError`.

---

## 6. Interfaccia Grafica Streamlit

L'interfaccia grafica locale è strutturata in 6 sezioni coerenti:

### 6.1 Schermata "2. Importazione"
- Accetta file SQLite WhatsApp (`msgstore.db` con companion `wa.db` opzionale o `wa.db` standalone), Cellebrite (CSV, JSON, XML) e il nuovo formato **WhatsApp export chat (TXT / ZIP)**.
- Valida il formato con preflight check in sandbox e produce un riepilogo metrico non sensibile (record raw, messaggi unificati, conversazioni estratte, validation issues, hash SHA-256 del file sorgente di importazione).

### 6.2 Schermata "5. Analisi topic"
La scheda è divisa in due tab operative:

#### Tab "Topic Detection (Verifica Mirata)"
Organizzata in due aree rigorosamente distinte:
- **AREA 1: "Verifica un argomento (Nuova Inferenza)"**:
  - Campo testuale "Argomento" e textarea "Descrizione opzionale";
  - Campo per modello LM Studio opzionale (con fallback automatico sul modello caricato);
  - Pulsante primario **"🚀 Esegui Topic Detection"**;
  - Riquadro risultato in tempo reale: badge decisione (`🟢 PRESENTE`, `⚪ ASSENTE`, `🟡 INCERTO`), motivazione AI (*Rationale*), modello utilizzato, latenza in secondi, elenco degli evidence ID citati ed expander contenente il **testo originale forense delle evidenze associate**.
- **AREA 2: "Filtra risultati Detection già eseguiti"**:
  - Distinta esplicitamente come visualizzatore e filtro della cronologia di sessione;
  - Selettore filtro decisione (*TUTTI, PRESENT, ABSENT, UNCERTAIN*);
  - Campo di ricerca per label o descrizione;
  - Interrogazione tramite `search_topic_detections()`.

#### Tab "Topic Discovery (Argomenti Emersi)"
- Visualizza gli argomenti estratti autonomamente dal motore AI o precalcolati nella demo;
- Per ogni argomento: label, sintesi, evidenze di supporto espandibili;
- Pulsante dedicato **"🔬 Verifica con Topic Detection"** per ogni card tematica, che lancia una nuova inferenza mirata sull'argomento selezionato e ne memorizza il risultato.

---

## 7. Verifica e Suite di Test: Dati Reali

Tutte le funzionalità descritte sono coperte da test automatizzati riproducibili eseguiti con `pytest`.

### 7.1 Bilancio Reale della Suite Completa

```
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\lucas\Desktop\Tesi
configfile: pyproject.toml

========== 1226 passed, 1 skipped, 5 deselected in 91.41s (0:01:31) ===========
```

- **Totale Test Superati:** **1226 passed**
- **Test Falliti / Errori:** **0 failed, 0 errors**
- **Test Skippati:** 1 (smoke test live condizionale per LM Studio `RUN_LM_STUDIO_SMOKE=1`, attivabile solo in presenza di demone LM Studio attivo con modello caricato)
- **Test Deselezionati:** 5 (benchmark opt-in guard)

### 7.2 Ripartizione per Aree Chiave

| Area di Test | Modulo Principale | Contenuto e Obiettivo |
| :--- | :--- | :--- |
| **Topic Detection Manuale & Workflow** | `tests/unit/test_ui_manual_topic_detection.py` | 12 test: topic libero → TopicQuery, inferenza con TopicDetectionAnalyzer, verdetto PRESENT/ABSENT/UNCERTAIN, Discovery → Detection, immutabilità forense, isolamento offline, AppTest interattivo. |
| **WhatsApp Export TXT / ZIP** | `tests/unit/test_whatsapp_export_importer.py` | 22 test: parsing formati timestamp, messaggi multilinea, eventi di sistema, estrazione ZIP, media linking, sicurezza anti zip-slip, anti zip-bomb, streaming SHA-256. |
| **WhatsApp Database Ingestion** | `tests/unit/test_whatsapp_msgstore_importer.py`, `test_whatsapp_wa_importer.py` | Schema SQLite, preservazione JID e timestamp grezzi, vincolo sola lettura, streaming record. |
| **Cellebrite Importer** | `tests/unit/test_cellebrite_csv/json/xml_importer.py` | Ingestion e mapping per export Cellebrite UFED. |
| **Validazione & Normalizzazione** | `tests/unit/test_validator.py`, `test_normalizer.py` | Validazione strutturale, mapping fusi orari, pulizia testo senza alterazione originale. |
| **Search Engine & Servizi** | `tests/unit/test_search_service.py`, `test_search_engine.py` | Indicizzazione deterministica, match mode (PHRASE, ALL_TERMS, ANY_TERM, EXACT), sincronizzazione real-time dei topic. |
| **Streamlit UI & Smoke Test** | `tests/unit/test_ui_smoke.py`, `test_ui_application.py`, `test_ui_state.py` | Navigazione headless AppTest, rendering pagine, gestione errori sicura, pseudonimizzazione identificatori. |
| **Sicurezza & Privacy** | `tests/unit/test_ui_privacy_hardening.py`, `test_deep_immutability.py` | Rifiuto binding su LAN/interfacce pubbliche, congelamento profondo strutture dati, assenza logging sensibile. |
| **Integrazione E2E Completa** | `tests/integration/test_full_pipeline_e2e.py`, `test_real_ingestion.py` | Catena completa da file grezzo a documento forense e ricerca. |

---

## 8. Protocollo Sperimentale di Benchmark e Chiusura Fase Cloud

### 8.1 Separazione tra UI Operativa e Suite di Benchmark

È fondamentale ribadire la separazione metodologica tra:
1. **Interfaccia Utente Operativa**: frontend interattivo orientato all'investigatore, abilitato all'analisi on-demand via LM Studio sia su dataset sintetico che su acquisizioni reali.
2. **Suite Sperimentale di Benchmark (`ai/experiment.py`)**: modulo batch rigorosamente congelato, destinato alla raccolta delle metriche formali per la tesi di laurea.

### 8.2 Conservazione e Freeze del Benchmark
- **Nessuna modifica a `ai/experiment.py`**: il modulo di benchmark non è stato alterato.
- **Dataset e Ground Truth Immutabili**: il dataset sintetico (`test_data/ai_benchmark/`) e la ground truth associata conservano i rispettivi hash crittografici originari.
- **Modelli Inclusi nel Benchmark Ufficiale**:
  - `Qwen2.5-7B-Instruct`
  - `Llama 3.1-8B-Instruct`
  - `DeepSeek-R1-Distill-Qwen-7B`

### 8.3 Distinzione Ambienti Hardware e Riproducibilità Benchmark

Il progetto distingue formalmente l'ambiente utilizzato per lo sviluppo e l'ingegnerizzazione del software dalla workstation di calcolo dedicata alla sperimentazione empirica dei modelli:

#### AMBIENTE DI SVILUPPO
- **Processore**: AMD A8-7410, privo di AVX2
- **Utilizzo**: Utilizzato per sviluppo e test software.

#### AMBIENTE SPERIMENTALE BENCHMARK
- **Sistema Operativo**: Ubuntu 22.04.5 LTS
- **Processore**: Intel Core i5-9600K, 6 core / 6 thread, AVX2
- **Memoria RAM**: circa 32 GB RAM
- **Scheda Video**: NVIDIA GeForce GTX TITAN X, 12 GB VRAM
- **Driver NVIDIA**: 550.107.02
- **CUDA**: 12.4
- **Python**: 3.12.4
- **Protocollo di Esecuzione**: I benchmark finali dei tre modelli (`Qwen2.5-7B-Instruct`, `Llama 3.1-8B-Instruct`, `DeepSeek-R1-Distill-Qwen-7B`) verranno rieseguiti su questa stessa workstation utilizzando un unico commit finale.

### 8.4 Chiusura della Validazione Cloud E2E Preliminare
La validazione preliminare condotta in precedenza su endpoint cloud (Protocolli V1, V2, V3 archiviati in `output/cloud_e2e/`) rimane formalmente **chiusa e congelata**. Essa ha assolto il compito di validare i contratti JSON Schema e la pipeline prima del definitivo passaggio all'ambiente offline. Nessun dato reale ha transitato o transiterà su canali esterni.

---

## 9. Riepilogo di Conformità ai Vincoli di Progetto

| Vincolo di Progetto | Stato | Riscontro Tecnico |
| :--- | :--- | :--- |
| **Privacy Forense & Offline** | CONFORME | Nessuna connessione remota. LmStudioClient vincolato a loopback (`127.0.0.1`). File reali elaborati in sandbox temporanea locale. |
| **Tracciabilità delle Evidenze** | CONFORME | Ogni TopicDetectionResult cita rigorosamente solo `evidence_id` validi appartenenti al documento. Rationale in italiano con spiegazione oggettiva. |
| **Determinismo Ingestion & Search** | CONFORME | Ingestion e SearchService sono deterministici e offline (zero inferenze automatiche). L'inferenza LLM è locale, controllata e on-demand (non qualificata come deterministica in senso rigoroso). |
| **Workflow Discovery → Detection** | CONFORME | Avvio di nuova inferenza reale con TopicDetectionAnalyzer su click esplicito. Area 1 e Area 2 distinte nella UI. |
| **Stato Test Suite** | CONFORME | **1226 passed, 1 skipped, 5 deselected** (0 failed, 0 errors) su `pytest`. Nessuna regressione introdotta. |
| **Integrità Benchmark** | CONFORME | Modelli fissati (`Qwen2.5-7B-Instruct`, `Llama 3.1-8B-Instruct`, `DeepSeek-R1-Distill-Qwen-7B`), ground truth, prompt e metriche congelati. |
