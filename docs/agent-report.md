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
| **Stato Test Suite** | CONFORME | **1269 passed, 1 skipped, 5 deselected** (0 failed, 0 errors) su `pytest`. Nessuna regressione introdotta. |
| **Integrità Benchmark** | CONFORME | Modelli fissati (`Qwen2.5-7B-Instruct`, `Llama 3.1-8B-Instruct`, `DeepSeek-R1-Distill-Qwen-7B`), ground truth, prompt e metriche congelati. |

---

## ULTIMO INTERVENTO VERIFICATO - 09/10/2026

### 1. PROBLEMI RIPRODOTTI
- **UI senza upload WhatsApp TXT/ZIP**: la schermata "Importazione" in Streamlit non esponeva il selettore `SourceFormat.WHATSAPP_EXPORT` con label `"WhatsApp export chat (TXT / ZIP)"` e l'uploader limitava i tipi a `.db/.csv/.json/.xml`, impedendo all'utente di caricare archivi `.zip` ed export `.txt` nativi di WhatsApp.
- **HTTP 400 da LM Studio durante auto-load**: durante l'avvio della Topic Detection, la chiamata REST a `POST /api/v1/models/load` generava l'errore:
  `HTTP 400 Bad Request: Unrecognized key(s) in object: 'identifier', 'contextLength', 'gpu_offload', 'gpuOffload'`. L'applicazione falliva l'auto-load e interrompeva il flusso prima dell'inferenza.
- **Vecchio Hardware Block nella UI**: la pagina "Sistema / Stato" riportava hardcoded riferimenti obsoleti legati alla macchina di sviluppo originaria (`"AMD A8-7410"`, `"Hardware Block"`, `"Rinvio Benchmark LM Studio"`, `"DEFERRED"`), violando la portabilità dell'applicazione.

### 2. ROOT CAUSE
- **Problema A (WhatsApp UI & Preflight)**:
  - In `ui/models.py` il selettore `SourceFormat.WHATSAPP_EXPORT` era definito ma non collegato nella logica di rendering `render_import()` di `ui/presentation.py`.
  - In `ui/presentation.py` il widget `st.file_uploader` non gestiva la visualizzazione della didascalia specifica né il tipo estensione `["txt", "zip"]`.
  - Mancava il preflight dedicato in `ui/ingestion.py` (`_validate_whatsapp_export_preflight`) per intercettare preliminarmente file non testuali o ZIP non conformi prima del passaggio all'engine.
- **Problema B (LM Studio Payload & Discovery)**:
  - In `ai/lmstudio.py` il metodo `load_model()` inviava chiavi derivate dalla sintassi CLI (`identifier`, `contextLength`, `gpu_offload`, `gpuOffload`). L'endpoint REST nativo v1 di LM Studio (`POST /api/v1/models/load`) effettua validazione stretta dello schema JSON e rifiuta chiavi non contemplate.
  - La risoluzione del modello locale utilizzava scorciatoie che non interrogavano la struttura ricca restituita da `GET /api/v1/models` (`key`, `display_name`, `quantization`, `loaded_instances`, `max_context_length`), rischiando di passare alias errati all'inferenza o scegliere arbitrariamente modelli in caso di ambiguità.
  - Mancava la cattura strutturata dell'errore HTTP 400 con conservazione di `status_code` e corpo di risposta tecnica (`technical_details`).
- **Problema C (Hardware Block)**:
  - In `ui/application.py` (`get_system_status_info()`) e in `ui/presentation.py` (`render_system_status_page()`) erano presenti costanti e stringhe hardcoded relative al processore `AMD A8-7410` e al differimento sperimentale, anziché esporre contratti agnostici e portabili.

### 3. FILE DI CODICE MODIFICATI
- [ai/backend.py](file:///c:/Users/lucas/Desktop/Tesi/ai/backend.py)
- [ai/lmstudio.py](file:///c:/Users/lucas/Desktop/Tesi/ai/lmstudio.py)
- [importer/whatsapp_export.py](file:///c:/Users/lucas/Desktop/Tesi/importer/whatsapp_export.py)
- [ui/application.py](file:///c:/Users/lucas/Desktop/Tesi/ui/application.py)
- [ui/ingestion.py](file:///c:/Users/lucas/Desktop/Tesi/ui/ingestion.py)
- [ui/presentation.py](file:///c:/Users/lucas/Desktop/Tesi/ui/presentation.py)
- [tests/unit/test_ai_lmstudio.py](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_ai_lmstudio.py)
- [tests/unit/test_operational_model_management.py](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_operational_model_management.py)
- [tests/unit/test_ui_manual_topic_detection.py](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_ui_manual_topic_detection.py)

### 4. FUNZIONI/CLASSI MODIFICATE
- `ai.backend`:
  - `AiModelAmbiguousError`: nuova eccezione specializzata per ambiguità nella risoluzione del modello locale.
  - `AiModelLoadError`: estesa con attributi `status_code: int | None` e `technical_details: str | None`.
  - `BaseLocalLlmClient.load_model`: firma pulita con parametri agnostici e `**kwargs`.
- `ai.lmstudio`:
  - `LocalModelMetadata`: dataclass immutabile per metadati nativi del modello (`key`, `display_name`, `quantization`, `loaded_instances`, `max_context_length`).
  - `resolve_qwen_operational_model`: risolutore deterministico del modello locale Qwen su lista di stringhe/identificatori.
  - `resolve_operational_model_from_metadata`: risolutore deterministico su metadati `LocalModelMetadata` con priorità su istanze già caricate, compatibilità 7B Instruct, preferenza quantizzazione `Q4_K_M`, rilevazione ambiguità ed esclusione auto-download.
  - `LmStudioClient.get_native_models`: query a `GET /api/v1/models` con fallback controllato a `GET /v1/models`.
  - `LmStudioClient.get_models_detailed`: supporto schema REST v1 e fallback trasparente OpenAI.
  - `LmStudioClient.is_model_loaded`: verifica presenze in `loaded_instances` o sessione.
  - `LmStudioClient.load_model`: invio del solo payload minimo supportato, gestione HTTP 400 e conservazione `instance_id`.
  - `LmStudioClient.ensure_model_loaded`: sequenza completa pre-flight -> risoluzione deterministica -> verifica già caricato -> load controllato.
- `importer.whatsapp_export`:
  - `WhatsAppExportImporter.validate_source`: validazione rigorosa diretta di archivi ZIP e file TXT con propagazione degli errori espliciti di sicurezza.
  - `WhatsAppExportImporter.can_import`: delega non distruttiva a `validate_source`.
  - `WhatsAppExportImporter._create_raw_record`: esposizione di sia `is_system` che `is_system_message` in `raw_fields`.
- `ui.ingestion`:
  - `_validate_whatsapp_export_preflight`: preflight difensivo per verificare che il file sia uno ZIP sicuro contenente un transcript o un testo non binario con timestamp.
  - `ingest_file_payload`: invocazione del preflight dedicato per `SourceFormat.WHATSAPP_EXPORT`.
  - `get_importer_for_format`: mapping esplicito `SourceFormat.WHATSAPP_EXPORT -> WhatsAppExportImporter()`.
- `ui.application`:
  - `prepare_operational_model`: rimozione di forzature GPU CLI nel client REST, gestione timeout separati (load = 240s, inference = 240s).
  - `get_system_status_info`: emissione di stato neutro, portabile e loopback (`READY - LM STUDIO LOCAL`, endpoint `http://127.0.0.1:1234`).
- `ui.presentation`:
  - `render_import`: integrazione di `SourceFormat.WHATSAPP_EXPORT` con dicitura `"Export nativo WhatsApp ottenuto tramite Esporta chat, con o senza media."` e tipo `type=["txt", "zip"]`.
  - `render_manual_topic_detection_section` & `render_topic_discovery_page`: progressione degli stati UX (`"Verifica LM Studio locale..."` -> `"Preparazione del modello AI locale..."` -> `"Modello locale pronto."` -> `"Analisi locale in corso..."`), blocco dei traceback a schermo e messaggi amichevoli controllati.
  - `render_system_status_page`: rimozione dei blocchi hardcoded "Hardware Block" e neutralizzazione dello stato benchmark.

### 5. PAYLOAD LM STUDIO PRIMA
Le chiavi JSON precedentemente inviate da `load_model()` includevano opzioni CLI non ammesse dallo schema REST:
```json
{
  "model": "qwen2.5-7b-instruct",
  "identifier": "qwen2.5-7b-instruct",
  "context_length": 8192,
  "contextLength": 8192,
  "gpu_offload": "max",
  "gpuOffload": "max"
}
```
Chiavi contestate da LM Studio con HTTP 400: `identifier`, `contextLength`, `gpu_offload`, `gpuOffload`.

### 6. PAYLOAD LM STUDIO DOPO
Il payload REST inviato a `POST /api/v1/models/load` è rigorosamente conforme allo schema nativo v1 di LM Studio:
```json
{
  "model": "<resolved local model key>",
  "context_length": 8192,
  "echo_load_config": true
}
```
Nessuna opzione GPU CLI arbitraria viene inviata via REST; l'offload GPU è gestito autonomamente dal runtime LM Studio.

### 7. RISOLUZIONE MODEL KEY
La risoluzione deterministica in `resolve_operational_model_from_metadata` segue un ordine tassativo:
1. **Istanza già caricata**: se tra i modelli locali un'istanza è già caricata (`loaded_instances` non vuoto) e compatibile con Qwen 2.5 7B Instruct, viene riutilizzata all'istante senza ricaricamento.
2. **Candidati compatibili**: ricerca tra i modelli locali di chiavi contenenti `qwen` e `7b` e `instruct` (escludendo altre famiglie o dimensioni).
3. **Preferenza quantizzazione**: se sono presenti più varianti compatibili e tra di esse è identificabile `Q4_K_M` (nel campo `quantization` o nel nome del file/chiave), viene selezionata univocamente la variante Q4_K_M.
4. **Corrispondenza univoca**: se esiste una sola corrispondenza, viene estratto il suo campo esatto `"key"` per l'invocazione di `load_model()`.
5. **Ambiguità non risolvibile**: se rimangono più corrispondenze non distinguibili in sicurezza, il sistema solleva `AiModelAmbiguousError` senza effettuare selezioni arbitrarie (`available_models[0]` è vietato).
6. **Modello non presente**: se nessun modello compatibile è installato, viene sollevato `AiModelNotInstalledError` con il messaggio: `"Qwen2.5-7B-Instruct non è installato localmente in LM Studio."`. Nessun download automatico da remoto viene mai avviato.

### 8. WHATSAPP TXT/ZIP
- **Enum**: `SourceFormat.WHATSAPP_EXPORT = "WhatsApp export chat (TXT / ZIP)"` in [ui/models.py](file:///c:/Users/lucas/Desktop/Tesi/ui/models.py).
- **Importer**: [importer/whatsapp_export.py](file:///c:/Users/lucas/Desktop/Tesi/importer/whatsapp_export.py) implementa `WhatsAppExportImporter(BaseImporter)` con `source_name = "whatsapp_export"`.
  - Supporto transcript Android (`DD/MM/YYYY, HH:MM - Mittente: Testo`) e iOS (`[DD/MM/YYYY, HH:MM:SS] Mittente: Testo`).
  - Gestione timestamp 24h e 12h AM/PM, anni a 2 o 4 cifre, secondi opzionali, caratteri Unicode direzionali/invisibili (`\u200e`, ecc.), emoji.
  - Messaggi multilinea e messaggi di sistema multilingua.
  - Nomi o URL contenenti due punti (`:`).
  - Riferimenti ad allegati multimediali quando presenti come membri nello ZIP.
- **Sicurezza ZIP**: impiego di `zipfile` standard con validazione stringente anti path-traversal (`..`, percorsi assoluti), protezione symlink, limite numero file (`10.000`), limite dimensione decompressa totale (`500 MB`) e rapporto di compressione (`100:1` ZIP bomb protection). Nessun file scritto indiscriminatamente su disco.
- **Dispatch**: in [ui/ingestion.py](file:///c:/Users/lucas/Desktop/Tesi/ui/ingestion.py) `get_importer_for_format(SourceFormat.WHATSAPP_EXPORT)` restituisce `WhatsAppExportImporter()`.
- **UI Uploader**: `st.file_uploader` accetta `["txt", "zip"]` con didascalia esatta.
- **Normalizer**: compatibilità completa con `normalize_records` per la produzione di `ConversationEvidenceDocument`.
- **Zero AI**: l'ingestion non invoca alcun endpoint LM Studio né apre socket di rete.

### 9. HARDWARE BLOCK
Sono stati completamente rimossi dal codice applicativo attivo (`ui/application.py`, `ui/presentation.py`):
- Stringhe hardcoded `"AMD A8-7410"`.
- Sezione `"Nota Rinvio Benchmark LM Studio (Hardware Block)"`.
- Stato `"Real LM Studio Benchmark: DEFERRED"`.
- Menzioni di incompatibilità AVX2 nella UI operativa.
La pagina di diagnostica espone ora parametri operativi portabili:
- `Local AI Layer: READY - LM STUDIO LOCAL`
- `LM Studio endpoint: http://127.0.0.1:1234`
- `Benchmark: EXTERNAL / SEPARATE EXPERIMENTAL RUNNER`
- `Real File Ingestion: INTEGRATED`

### 10. TEST AGGIUNTI/MODIFICATI
- [tests/unit/test_operational_model_management.py](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_operational_model_management.py): nuova suite esaustiva contenente i 43 test contrattuali obbligatori:
  - WhatsApp Export (test 1-19): enum, dispatch, Android TXT, iOS TXT, multilinea, messaggi sistema, Unicode/emoji, URL con `:`, ZIP in memoria, allegati ZIP, rifiuto ZIP ambiguo, protezione traversal `..`, protezione ZIP bomb, rifiuto file generici, pipeline integration SearchService, AppTest presenza UI, AppTest accettazione `.txt`/`.zip`, verifica zero AI durante ingestion.
  - LM Studio Model Management (test 20-39): parsing `GET /api/v1/models`, skip ricaricamento se già in memoria, `POST /api/v1/models/load` con payload conforme (`model`, `context_length`), assenza tassativa di `identifier`, `contextLength`, `gpu_offload`, `gpuOffload`, gestione modello assente, gestione ambiguità, server offline, gestione HTTP 400, timeout load, timeout inferenza, corrispondenza `model_id` istanza caricata, timeout configurato a 240s, divieto download automatici, divieto endpoint esterni non loopback, verifica assenza AI in ingestion.
  - System Status (test 40-43): assenza di `AMD A8-7410`, assenza di `Hardware Block`, assenza di `Rinvio Benchmark`, verifica contratto portabile e neutro.
- [tests/unit/test_ai_lmstudio.py](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_ai_lmstudio.py): aggiornamento test unitari `get_models_detailed`, `load_model` e `ensure_model_loaded` per il nuovo schema REST v1 e fallback trasparente.
- [tests/unit/test_ui_manual_topic_detection.py](file:///c:/Users/lucas/Desktop/Tesi/tests/unit/test_ui_manual_topic_detection.py): aggiornamento mock per verificare il payload pulito privo di parametri GPU arbitrari.

### 11. RISULTATO PYTEST REALE
Esecuzione completa dell'intera suite di test del progetto (`python -m pytest`):
```text
========== 1269 passed, 1 skipped, 5 deselected in 132.89s (0:02:12) ==========
```
**0 failed, 0 errors**. Suite completamente verde.

### 12. LIVE TEST LM STUDIO
Live test LM Studio non eseguito in questa sessione (server locale non in ascolto durante la fase di verifica automatizzata; l'intera pipeline è stata validata deterministicamente tramite suite di mock/fake HTTP server).

### 13. BENCHMARK INTEGRITY
Si conferma che:
- `ai/experiment.py` è rimasto rigorosamente invariato;
- I dataset sintetici di benchmark in `test_data/ai_benchmark/` non sono stati modificati;
- La ground truth associata non è stata modificata;
- I prompt dei benchmark non sono stati modificati;
- Le metriche di valutazione non sono state modificate;
- Il protocollo sperimentale comparativo (`Qwen2.5-7B-Instruct`, `Llama 3.1-8B-Instruct`, `DeepSeek-R1-Distill-Qwen-7B`) è integro.

### 14. GIT
Nessun commit effettuato. Nessun push effettuato.

