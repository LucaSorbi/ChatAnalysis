# Forensic Chat Analysis & Search (`whatsapp-forensic-importer`)

Piattaforma locale di acquisizione, normalizzazione, consultazione, ricerca deterministica e analisi tematica assistita da Intelligenza Artificiale locale per conversazioni estratte da **WhatsApp** e da report forensi **Cellebrite**.

> **Avvertenza metodologica fondamentale**: questo software è uno **strumento di supporto all'analista**, progettato per agevolare l'esplorazione e la triage di conversazioni voluminose mantenendo ogni risultato ancorato agli identificativi delle evidenze originali (`evidence_id`). **Non costituisce mai una prova autonoma della veridicità fattuale dei contenuti né una certificazione esaustiva dei temi discussi**: le etichette, le sintesi e le decisioni prodotte da un modello linguistico (LLM) devono sempre essere verificate dall'operatore umano sui messaggi originali acquisiti.

---

## Indice

1. [Descrizione, obiettivi, casi d'uso, prerequisiti e limiti](#1-descrizione-obiettivi-casi-duso-prerequisiti-e-limiti)
2. [Funzionalità reali e formati di input implementati](#2-funzionalità-reali-e-formati-di-input-implementati)
3. [Architettura sintetica e struttura delle cartelle (dopo la pulizia)](#3-architettura-sintetica-e-struttura-delle-cartelle-dopo-la-pulizia)
4. [Tabella dei requisiti di sistema e hardware](#4-tabella-dei-requisiti-di-sistema-e-hardware)
5. [Installazione su Windows da zero (PowerShell)](#5-installazione-su-windows-da-zero-powershell)
6. [Installazione su Ubuntu da zero (Terminale bash)](#6-installazione-su-ubuntu-da-zero-terminale-bash)
7. [LM Studio su Windows e Ubuntu (modello LLM locale)](#7-lm-studio-su-windows-e-ubuntu-modello-llm-locale)
8. [Componenti multimediali (Tesseract OCR e Faster-Whisper STT)](#8-componenti-multimediali-tesseract-ocr-e-faster-whisper-stt)
9. [Configurazione (variabili d'ambiente, parametri UI e costanti interne)](#9-configurazione-variabili-dambiente-parametri-ui-e-costanti-interne)
10. [Avvio ogni volta (sequenza operativa quotidiana)](#10-avvio-ogni-volta-sequenza-operativa-quotidiana)
11. [Guida all'uso dettagliata passo per passo](#11-guida-alluso-dettagliata-passo-per-passo)
12. [Lingue e strategie di analisi (`DIRECT_MULTILINGUAL` vs `TRANSLATE_FIRST`)](#12-lingue-e-strategie-di-analisi-direct_multilingual-vs-translate_first)
13. [Chunk, microchunk e checkpoint SQLite](#13-chunk-microchunk-e-checkpoint-sqlite)
14. [Esempio pratico completo (tutorial end-to-end non sensibile)](#14-esempio-pratico-completo-tutorial-end-to-end-non-sensibile)
15. [Verifiche post-installazione](#15-verifiche-post-installazione)
16. [Risoluzione problemi (Windows e Ubuntu)](#16-risoluzione-problemi-windows-e-ubuntu)
17. [Privacy e sicurezza operativa](#17-privacy-e-sicurezza-operativa)
18. [Manutenzione, backup e ripristino dalla cartella `Trash`](#18-manutenzione-backup-e-ripristino-dalla-cartella-trash)
19. [Limiti noti e test effettivamente eseguiti](#19-limiti-noti-e-test-effettivamente-eseguiti)

---

## 1. Descrizione, obiettivi, casi d'uso, prerequisiti e limiti

### Obiettivi del progetto
L'applicazione consente a un analista, consulente tecnico o ricercatore di:
- **Importare localmente** esportazioni testuali/archivi WhatsApp e acquisizioni strutturate (SQLite, CSV, JSON, XML) calcolando in streaming l'impronta crittografica **SHA-256** del file sorgente senza alterarlo.
- **Unificare e consultare** i messaggi nell'ordine naturale di acquisizione, preservando la catena di provenienza (`document_id`, `message_id`, `source_record_id`, `evidence_id`).
- **Arricchire su richiesta (opt-in)** la conversazione estraendo localmente il testo contenuto nelle immagini (**OCR** tramite Tesseract) e trascrivendo le note vocali (**STT** tramite Faster-Whisper), mantenendo rigorosamente separati i testi originali dalle evidenze derivate.
- **Eseguire ricerche lessicali deterministiche** (`PHRASE`, `ALL_TERMS`, `ANY_TERM`, `EXACT`) senza dipendere da modelli AI o database vettoriali.
- **Individuare e verificare temi** tramite un modello linguistico eseguito **in locale** attraverso **LM Studio** (`http://127.0.0.1:1234`):
  - **Topic Discovery**: scansione aperta per far emergere gli argomenti discussi nella chat;
  - **Topic Detection**: verifica mirata con esito tri-stato (`PRESENT`, `ABSENT`, `UNCERTAIN`) su un tema scelto dall'analista;
  - **Verifica di consistenza («Prova del Nove»)**: seconda inferenza indipendente di Topic Detection avviata su un argomento emerso dalla Discovery (passando al modello solo titolo e descrizione del topic, mai le evidenze citate dalla Discovery) per misurarne la coerenza (`CONSISTENT`, `PARTIALLY_CONSISTENT`, `DISCREPANCY_ABSENT`, `DISCREPANCY_UNCERTAIN`).
- **Gestire conversazioni lunghe** tramite suddivisione automatica in blocchi (*chunk* e *microchunk*) e salvataggio incrementale su database **SQLite di checkpoint** (`.checkpoints/checkpoints.db`) per riprendere analisi interrotte senza ricalcolare i blocchi già validati.

### Casi d'uso tipici
- Analisi tecnica e indicizzazione di chat esportate da smartphone (`.txt` o `.zip` con allegati vocali e fotografici).
- Ispezione in sola lettura di database estratti da backup Android non cifrati (`msgstore.db` ed eventuale `wa.db`) o da esportazioni tabellari/strutturate Cellebrite (`.csv`, `.json`, `.xml`).
- Triage preliminare multilingua su conversazioni estese prima della lettura integrale puntuale da parte del consulente.

### Prerequisiti essenziali
- **Python 3.12** (richiesto `>=3.12` da `pyproject.toml`; Python 3.12 è la versione raccomandata per la piena disponibilità dei pacchetti precompilati audio/AI).
- **Connessione a Internet richiesta esclusivamente in fase di installazione iniziale** per scaricare Python, i pacchetti `pip`, Tesseract OCR, LM Studio, il file del modello LLM (`.gguf`) e, al primo utilizzo della trascrizione vocale, i pesi del modello Faster-Whisper. **Durante l'uso operativo l'applicazione funziona al 100% offline su interfaccia di loopback (`127.0.0.1`)**.
- **LM Studio** attivo in locale soltanto se si desidera utilizzare Topic Discovery, Topic Detection o la verifica di consistenza; importazione, consultazione, ricerca deterministica, OCR e STT funzionano anche con LM Studio spento.

### Limiti strutturali e metodologici
- **Supporto decisionale, non prova legale autonoma**: un verdetto `PRESENT` o `CONSISTENT` attesta che il modello ha associato determinati messaggi al tema richiesto, non che i fatti narrati nei messaggi siano veri; analogamente, un esito `ABSENT` o una Discovery senza argomenti non dimostra in assoluto l'assenza di riferimenti impliciti, gergali o allusivi.
- **Database WhatsApp cifrati (`.crypt12`, `.crypt14`, `.crypt15`) non supportati**: il software non esegue decifrazione crittografica; accetta esclusivamente database SQLite già in chiaro (`SQLite format 3`).
- **Vision su immagini reali non implementata**: per le immagini reali l'unico motore attivo è l'estrazione del testo stampato/scritto (**Tesseract OCR**). Il sistema **non** descrive scene, volti o oggetti privi di testo (le sezioni `VISION_DESCRIPTION` compaiono unicamente nel dataset sintetico dimostrativo `DEMO`).
- **Allegati multimediali per `msgstore.db` e Cellebrite**: durante il caricamento via browser (file uploader di Streamlit) viene trasferito il singolo file selezionato; pertanto l'estrazione automatica dei file binari audio/immagini allegati dalla UI è pienamente operativa per gli archivi **`WhatsApp export chat (TXT / ZIP)`** in formato `.zip` che contengono al proprio interno sia il transcript `.txt` sia i file media.

---

## 2. Funzionalità reali e formati di input implementati

### Formati di input supportati nella schermata `2. Importazione`
L'applicazione **non indovina automaticamente** il formato dal nome del file: l'utente seleziona esplicitamente il formato nel menu **`Tipo di sorgente forense da importare`**, e l'importer corrispondente verifica in sola lettura la firma binaria e lo schema strutturale del file (fino a **2 GB** / `2048 MB`).

| Voce esatta nel menu UI | Importer Python (`importer/`) | Estensioni accettate dal selettore UI | Requisiti strutturali verificati dal codice |
| :--- | :--- | :--- | :--- |
| **`WhatsApp export chat (TXT / ZIP)`** | `WhatsAppExportImporter` | `.txt`, `.zip` | File di testo UTF-8 (Android `GG/MM/AA, HH:MM - Autore: Testo` o iOS `[GG/MM/AA, HH:MM:SS] Autore: Testo`, formati 12h/24h e multiriga) oppure archivio `.zip` contenente almeno un transcript `.txt` ed eventuali allegati media. |
| **`WhatsApp msgstore (SQLite)`** | `WhatsAppMsgstoreImporter` | `.db`, `.sqlite`, `.csv`, `.json`, `.xml` | Header binario `SQLite format 3\x00` + presenza delle tabelle classiche `messages` e `chat_list` con colonne `key_remote_jid`, `key_from_me`, `key_id` (e lettura opzionale di `media_refs`). Consente di allegare nel secondo campo **`wa.db contatti — opzionale`** (`.db`, `.sqlite`). |
| **`WhatsApp wa.db (SQLite)`** | `WhatsAppWaDbImporter` | `.db`, `.sqlite`, `.csv`, `.json`, `.xml` | Header `SQLite format 3\x00` + tabella `contacts` con colonne `jid`, `display_name`, `phone_number`. Importato da solo produce stato `AUXILIARY_ONLY` (anagrafica contatti, nessuna conversazione autonoma). |
| **`Cellebrite (CSV)`** | `CellebriteCsvImporter` | `.db`, `.sqlite`, `.csv`, `.json`, `.xml` | File CSV UTF-8 (con o senza BOM) la cui prima riga di intestazione contiene almeno le colonne `Source`, `MessageType`, `TimeStamp`, `Direction`, `ChatId` (oltre a `From`, `To`, `Body`, `Attachments`). |
| **`Cellebrite (JSON)`** | `CellebriteJsonImporter` | `.db`, `.sqlite`, `.csv`, `.json`, `.xml` | Array JSON top-level (`[ {...}, ... ]`) letto in streaming tramite `ijson`, in cui il primo oggetto contiene almeno le chiavi `chat_id`, `sender`, `timestamp`, `type` (con testo in `content.text` ed eventuale `content.media_path`). |
| **`Cellebrite (XML)`** | `CellebriteXmlImporter` | `.db`, `.sqlite`, `.csv`, `.json`, `.xml` | Report XML UFDR analizzato in streaming con `defusedxml` (blocco rigoroso di DTD, XXE ed entità esterne) contenente root `<DumpFile type="UFDR">` e nodi `<InstantMessages>` / `<Message>`. |

### Differenza tra testi originali ed evidenze derivate
Ogni messaggio importato è incapsulato in un `MessageEvidenceBundle` immutabile che distingue in modo inequivocabile l'origine di ciascuna sezione di testo (`EvidenceSourceType`):
1. **`ORIGINAL_TEXT` (Evidenza originale acquisita)**: testo nativo del messaggio così come presente nella fonte forense. Non viene mai sovrascritto o alterato dall'elaborazione multimediale o dall'AI.
2. **`OCR_TEXT` (Evidenza derivata da immagine)**: testo estratto localmente da un allegato fotografico tramite **Tesseract OCR** quando l'utente preme `🎙️ Elabora media localmente`.
3. **`STT_TRANSCRIPTION` (Evidenza derivata da audio)**: trascrizione testuale prodotta localmente da una nota vocale o file audio tramite **Faster-Whisper** su richiesta esplicita dell'utente.
4. **`VISION_DESCRIPTION` / `VISION_OBSERVATION` (Solo modalità DEMO)**: presenti esclusivamente nel dataset sintetico dimostrativo per illustrare il modello dati; disabilitate sui file reali.

### Tipi di chat e allegati multimediali supportati
- **Chat individuali (direct) e chat di gruppo (group)**: il parser distingue conversazioni tra due interlocutori, conversazioni multi-partecipante e messaggi di sistema WhatsApp multilingua (creazione gruppo, cambi oggetto/icona, aggiunta/rimozione membri, avvisi di crittografia end-to-end). Quando un archivio contiene più chat (es. `msgstore.db` o report Cellebrite), l'utente sceglie quale conversazione aprire tramite etichette pseudonimizzate (`Conversazione 1 — N messaggi — [id]`, ed eventuale bucket `Messaggi non associati` per record privi di `chat_id`).
- **Media supportati per estrazione testo on-demand (da archivi ZIP WhatsApp)**:
  - **Immagini per OCR**: `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`, `.bmp`, `.tiff`, `.tif`.
  - **Audio/note vocali per STT**: `.opus` (formato nativo delle note vocali WhatsApp `PTT-*.opus` / `AUD-*.opus`), `.ogg`, `.mp3`, `.wav`, `.m4a`, `.aac`, `.flac`, `.wma`, `.amr`.
  - Video (`.mp4`, `.mov`, `.3gp`, ecc.) e documenti generici (`.pdf`, `.docx`, ecc.) vengono censiti strutturalmente nei metadati senza estrazione automatica di testo.

---

## 3. Architettura sintetica e struttura delle cartelle (dopo la pulizia)

La pipeline applicativa è rigidamente stratificata in sola lettura sulle evidenze:

`File Sorgente` → `Importer` (`RawRecord`) → `Validation` (`RecordValidator`) → `Normalization` (`RecordNormalizer` + SQLite staging a memoria limitata) → `Entity Resolution` (`DeterministicEntityResolver`) → `Unified Messages` (`UnifiedModelBuilder`) → `Evidence Bundles & Document` (`ConversationEvidenceDocument`) → `SearchService` (deterministico) / `LocalTopicAnalysisService` (LM Studio locale + Checkpoint SQLite).

### Struttura operativa del repository ripulito

```text
ChatAnalysis/
├── app.py                        # Entry point principale dell'interfaccia web locale Streamlit
├── pyproject.toml                # Definizione pacchetto Python (>=3.12) e dipendenze runtime/opzionali
├── MANIFEST.in                   # Regole di pulizia per la generazione di distribuzioni wheel/sdist
├── README.md                     # Manuale completo di installazione, configurazione e utilizzo
├── .gitignore                    # Regole Git e protezione di dati reali, modelli e checkpoint locali
├── .gitattributes                # Normalizzazione fine riga Git
├── .streamlit/
│   └── config.toml               # Hardening Streamlit: 127.0.0.1, zero telemetria, upload max 2048 MB
├── scripts/
│   └── run_streamlit_windows.ps1 # Script PowerShell di avvio rapido per Windows
├── ai/                           # Client LM Studio, prompt, JSON schema, token budget, chunking, topic engine
├── analysis/                     # Orchestrazione multi-chunk/microchunk, checkpoint SQLite, consistenza, export
├── core/                         # Limiti di sicurezza (upload/anti-zip-bomb) e immutabilità strutturale
├── entity_resolution/            # Risoluzione deterministica delle identità intra-sorgente
├── importer/                     # Importer reali per WhatsApp (TXT/ZIP, msgstore, wa.db) e Cellebrite (CSV/JSON/XML)
├── multimodal/                   # Accesso sicuro agli allegati ZIP, Tesseract OCR e Faster-Whisper STT
├── normalization/                # Normalizzazione di timestamp, mittenti e tipi messaggio
├── profiler/                     # Modulo CLI di profilazione statistica read-only (python -m profiler)
├── search/                       # Motore di ricerca lessicale deterministica e filtri sui topic
├── ui/                           # Logica applicativa, stato sessione, dataset DEMO e pagine Streamlit
├── unified/                      # Costruzione dei messaggi unificati con catena di provenienza
├── validation/                   # Validazione strutturale non distruttiva dei record grezzi
├── test_data/
│   ├── cloud_e2e/                # Asset sintetici congelati referenziati da ai/cloud_experiment.py
│   └── synthetic_smoke/          # Chat sintetiche non sensibili (.txt e .zip) per test post-installazione
├── output/
│   └── .gitkeep                  # Cartella predefinita per report del modulo CLI profiler (ignorata da Git)
├── .checkpoints/                 # Creata/usata a runtime per il database SQLite checkpoints.db (ignorata da Git)
└── Trash/                        # Archivio NON operativo (storico tesi + repository_cleanup_2026-11-07/)
```

> **Natura della cartella `Trash/`**: `Trash/` **non** è mai letta né utilizzata dall'applicazione durante l'installazione o l'esecuzione. Conserva in modo reversibile la documentazione storica di sviluppo e, nella sottocartella `Trash/repository_cleanup_2026-11-07/`, tutti i file non essenziali all'operatività (vecchi runbook, script di debug, artefatti temporanei e suite di test per sviluppatori) corredati da `manifest_cleanup.tsv`, `manifest_cleanup.json` e istruzioni di ripristino non distruttivo.

---

## 4. Tabella dei requisiti di sistema e hardware

La tabella seguente distingue i **requisiti minimi verificati nel codice/ambiente** dalle **indicazioni orientative consigliate** per lavorare comodamente con modelli LLM da 7–8 miliardi di parametri.

| Componente | Minimo verificato / richiesto dal codice | Configurazione consigliata (orientativa) | Note tecniche |
| :--- | :--- | :--- | :--- |
| **Sistema Operativo** | **Windows 10 / 11 (64-bit)** *(verificato)*; **Ubuntu 24.04 LTS / 22.04 LTS (x86_64)** *(24.04: procedura prevista; 22.04.5: usata per i test iniziali e i benchmark, vedi Sezione 19.3)* | Windows 11 64-bit o Ubuntu 24.04 LTS Desktop 64-bit | Ubuntu 24.04 LTS include nativamente Python 3.12; su Ubuntu 22.04 LTS Python 3.12 va installato a parte. |
| **Python** | **`>= 3.12`** (vincolo in `pyproject.toml`) | **Python 3.12.x (64-bit)** *(verificato con 3.12.10)* | Preferire **Python 3.12** rispetto a versioni sperimentali più recenti (es. 3.14/3.15) per avere wheel precompilate di `av`, `ctranslate2` e `faster-whisper`. |
| **CPU** | x86_64 multi-core con supporto **AVX2** | 8+ core moderni (Intel Core i7 / AMD Ryzen 7 o superiore) | Le istruzioni AVX2 sono richieste da LM Studio (`llama.cpp`) e CTranslate2 su PC x86_64. |
| **RAM di sistema** | **4 GB** (solo consultazione/ricerca senza LLM); **8–12 GB** con LLM 7B su sola CPU | **16 GB – 32 GB RAM** | Il modello `Qwen2.5-7B-Instruct` `Q4_K_M` con contesto `8192` occupa da solo circa **5,5 – 6,5 GB** tra pesi e KV cache, oltre a sistema operativo, browser e Python. |
| **GPU / VRAM** | **Non obbligatoria** (funziona anche in sola CPU) | GPU dedicata NVIDIA (CUDA) o AMD/Vulkan con **8+ GB VRAM** | Su sola CPU un singolo microchunk richiede tipicamente da 10 a 90 secondi; con GPU da 8+ GB VRAM scende a pochi secondi. |
| **Spazio su disco** | **~3 GB** (senza modelli AI); **~10 GB** (con Python venv, LM Studio, 1 modello GGUF e Faster-Whisper `base`) | **20+ GB liberi su SSD** | Lo staging di file grandi (fino a 2 GB) usa spazio temporaneo locale durante l'importazione. |
| **Modello LLM GGUF** | **`Qwen2.5-7B-Instruct`** quantizzato **`Q4_K_M`** (~4,7 GB, id canonico `qwen2.5-7b-instruct`) | `Qwen2.5-7B-Instruct` (`Q4_K_M`) con Context Length **`8192`** | Supportati dal codice anche `meta-llama-3.1-8b-instruct` (`Q4_K_M`, ~4,9 GB) e `deepseek-r1-distill-qwen-7b` (`Q4_K_M`, ~4,7 GB). |
| **Browser web** | Qualsiasi browser moderno locale (Edge, Chrome, Firefox) | Chromium / Chrome / Edge / Firefox aggiornato | L'interfaccia si apre localmente su `http://127.0.0.1:8501`. |
| **Rete Internet** | **Necessaria solo durante il setup iniziale** | Nessuna rete durante l'analisi forense | Nessun dato delle chat viene inviato in rete durante l'uso dell'applicazione. |

---

## 5. Installazione su Windows da zero (PowerShell)

Questa procedura guida passo dopo passo un utente che parte da un PC Windows 10 o Windows 11 appena installato.

### Passo 1 — Installare Python 3.12 (64-bit) e configurare il `PATH`
1. Apri il browser e vai alla pagina ufficiale di Python 3.12: https://www.python.org/downloads/windows/
2. Scarica **Windows installer (64-bit)** dell'ultima release stabile della serie **Python 3.12.x** (es. `python-3.12.10-amd64.exe`).
3. Avvia il file `.exe` scaricato:
   - **IMPORTANTE**: nella prima schermata spunta **obbligatoriamente** la casella in basso **`Add python.exe to PATH`** (e lascia attiva l'opzione per `py launcher`).
   - Clicca su **`Install Now`** e attendi la fine dell'installazione.
   - Se al termine compare il pulsante **`Disable path length limit`**, cliccalo e conferma: evita errori su percorsi lunghi di Windows.
4. **Chiudi eventuali finestre del terminale già aperte** e apri una **nuova** finestra di **Windows PowerShell** (premi il tasto `Start`, digita `PowerShell` e premi `Invio`). Aprire un nuovo terminale è indispensabile affinché Windows ricarichi la variabile `PATH`.

### Passo 2 — Verificare che Python 3.12 risponda correttamente
Copia e incolla in PowerShell:

```powershell
py -3.12 --version
python --version
```

Risultato atteso: `Python 3.12.x` (ad esempio `Python 3.12.10`). Se `py -3.12 --version` mostra `3.12.x`, usa sempre `py -3.12` nel passo 4 così da essere certo di non usare per errore un'altra versione di Python eventualmente presente sul PC.

### Passo 3 — Posizionarsi nella cartella del progetto
Assicurati di aver copiato o estratto la cartella del progetto `ChatAnalysis` sul tuo computer (ad esempio sul Desktop). In PowerShell sostituisci `<PERCORSO_CARTELLA_PROGETTO>` con il percorso reale della cartella che contiene il file `app.py` (esempio concreto: `C:\Users\TuoNome\Desktop\ChatAnalysis`):

```powershell
cd "<PERCORSO_CARTELLA_PROGETTO>"
```

Verifica di trovarti nella cartella giusta controllando che compaiano `app.py` e `pyproject.toml`:

```powershell
Test-Path .\app.py, .\pyproject.toml
```

Entrambe le righe devono rispondere `True`.

### Passo 4 — Creare l'ambiente virtuale isolato (`.venv`)
Un *ambiente virtuale* (`.venv`) è una sottocartella privata del progetto in cui vengono installate le librerie Python senza sporcare il resto di Windows. Crealo con:

```powershell
py -3.12 -m venv .venv
```

### Passo 5 — Attivare `.venv` (senza modifiche permanenti alla sicurezza di Windows)
Su molti PC Windows nuovi il comando di attivazione `Activate.ps1` viene bloccato dall'*Execution Policy* predefinita di PowerShell. Hai **due alternative sicure** (non serve modificare in modo permanente le impostazioni di sicurezza del sistema):

- **Opzione A (Consigliata — sblocco temporaneo valido solo per la finestra PowerShell aperta)**:
  ```powershell
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  .\.venv\Scripts\Activate.ps1
  ```
  Quando l'ambiente è attivo vedrai comparire il prefisso `(.venv)` all'inizio della riga di comando.

- **Opzione B (Senza attivazione — invocando direttamente l'eseguibile dentro `.venv`)**:
  Se non vuoi eseguire `Activate.ps1`, puoi eseguire ogni comando successivo chiamando direttamente `.\.venv\Scripts\python.exe` (i comandi qui sotto usano già questo percorso esplicito così funzionano in entrambi i casi).

### Passo 6 — Installare tutte le dipendenze applicative dichiarate
Con il PC connesso a Internet, aggiorna `pip` e installa il progetto insieme a tutti i gruppi opzionali (`ui`, `ocr`, `stt`, `image`) definiti in `pyproject.toml`:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[ui,ocr,stt,image]"
```

Questo comando scarica e installa automaticamente `streamlit` (incluso `pandas`), `charset-normalizer`, `python-dateutil`, `ijson`, `defusedxml`, `Pillow`, `pytesseract` e `faster-whisper` (inclusi `ctranslate2` e `av` / PyAV).

### Passo 7 — Verificare la configurazione Streamlit e l'installazione base
Il file di configurazione `.streamlit\config.toml` è già incluso nel repository e imposta automaticamente l'ascolto locale su `127.0.0.1`, la disattivazione della telemetria (`gatherUsageStats = false`) e il limite di upload a `2048` MB. Verifica che i moduli principali si importino senza errori:

```powershell
.\.venv\Scripts\python.exe -c "import streamlit, faster_whisper, pytesseract, PIL, ijson, defusedxml, ui, analysis, importer; print('Installazione Python Windows OK')"
```

Risultato atteso: `Installazione Python Windows OK`. Prosegui ora con le sezioni **7** (LM Studio) e **8** (Tesseract OCR) per completare i componenti esterni.

---

## 6. Installazione su Ubuntu da zero (Terminale bash)

> **Stato di verifica**: la procedura seguente è conforme alle dipendenze reali dichiarate in `pyproject.toml` e nel codice sorgente, ma **non è stata eseguita direttamente in questa sessione di lavoro** (svolta su host Windows 11). I test iniziali sui modelli e i benchmark di ottobre 2026 sono invece stati eseguiti su un host Ubuntu 22.04.5 LTS (dettagli nella Sezione 19.3).

### Release Ubuntu e versione Python richiesta
Il file `pyproject.toml` richiede tassativamente **`Python >= 3.12`**:
- **Ubuntu 24.04 LTS (Noble Numbat, x86_64)** — **Consigliata**: include nativamente **Python 3.12** nei repository ufficiali `apt`.
- **Ubuntu 22.04 LTS (Jammy Jellyfish, x86_64)**: include di serie Python 3.10, che **non soddisfa** `requires-python = ">=3.12"`. Su Ubuntu 22.04 occorre quindi disporre di un interprete `python3.12` (incluso `python3.12-venv`) prima di creare l'ambiente virtuale, oppure aggiornare a Ubuntu 24.04 LTS.

### Passo 1 — Installare i pacchetti di sistema su Ubuntu 24.04 LTS
Apri l'applicazione **Terminale** e aggiorna gli indici installando Python 3.12, il modulo `venv` e `curl` (utile per verificare LM Studio):

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip curl
python3 --version
```

Verifica che l'ultima riga stampi `Python 3.12.x`.
*(Nota per **Ubuntu 22.04 LTS**: se `python3 --version` mostra `3.10.x`, non usare `python3 -m venv` con la 3.10 perché `pip install` rifiuterà il pacchetto; installa prima Python 3.12 con il relativo pacchetto `python3.12-venv` e usa esplicitamente `python3.12 -m venv .venv` nel Passo 3).*

### Passo 2 — Posizionarsi nella cartella del progetto
Sostituisci `<PERCORSO_CARTELLA_PROGETTO>` con il percorso reale in cui hai copiato la cartella `ChatAnalysis` (ad esempio `~/Scrivania/ChatAnalysis` oppure `~/ChatAnalysis`):

```bash
cd "<PERCORSO_CARTELLA_PROGETTO>"
ls -la app.py pyproject.toml .streamlit/config.toml
```

### Passo 3 — Creare e attivare l'ambiente virtuale `.venv`

```bash
python3 -m venv .venv
source .venv/bin/activate
python --version
```

Dopo `source .venv/bin/activate` comparirà `(.venv)` all'inizio della riga del terminale e `python --version` mostrerà `Python 3.12.x`.

### Passo 4 — Installare le dipendenze Python del progetto

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[ui,ocr,stt,image]"
```

**Accorgimenti CPU / GPU su Ubuntu**:
- **Esecuzione su CPU (predefinita)**: il motore vocale `FasterWhisperTranscriber` è configurato di default su `device="cpu"` e `compute_type="int8"`, quindi funziona immediatamente su qualsiasi CPU x86_64 con AVX2 senza dover installare driver CUDA.
- **Accelerazione GPU per l'LLM**: l'accelerazione GPU (NVIDIA CUDA o Vulkan) per Topic Discovery e Topic Detection è gestita direttamente all'interno di **LM Studio**, senza necessità di installare PyTorch nell'ambiente Python dell'app.

### Passo 5 — Verifica rapida dell'ambiente Python su Ubuntu

```bash
python -c "import streamlit, faster_whisper, pytesseract, PIL, ijson, defusedxml, ui, analysis, importer; print('Installazione Python Ubuntu OK')"
```

Per avviare subito l'interfaccia (anche prima di installare LM Studio, ad esempio per provare consultazione e ricerca):

```bash
PYTHONUTF8=1 python -m streamlit run app.py
```

Premi `Ctrl+C` nel terminale quando vuoi fermare il server e prosegui con le sezioni **7** e **8**.

---

## 7. LM Studio su Windows e Ubuntu (modello LLM locale)

**LM Studio** è l'applicazione desktop gratuita che esegue sul tuo computer il modello linguistico in formato **GGUF** ed espone un server HTTP locale (`http://127.0.0.1:1234`) con cui ChatAnalysis comunica per **Topic Discovery**, **Topic Detection** e **Verifica di consistenza**.

### 7.1 Download e installazione di LM Studio
Sito ufficiale (unico punto di download raccomandato): **https://lmstudio.ai/**

- **Su Windows (10/11 64-bit)**:
  1. Scarica da https://lmstudio.ai/ l'installer **LM Studio for Windows (x64)** (`.exe`).
  2. Esegui l'installer e avvia **LM Studio** dal menu Start.
- **Su Ubuntu Desktop (22.04 / 24.04 x86_64)**:
  > **Nota di trasparenza**: LM Studio non è distribuito nei repository ufficiali `apt` di Ubuntu; viene rilasciato sul sito ufficiale come pacchetto **`.AppImage`** per Linux x86_64 con interfaccia grafica desktop.
  1. Dal browser su Ubuntu vai su https://lmstudio.ai/ e scarica **LM Studio for Linux (x86_64 AppImage)** nella cartella `~/Scaricati` (o `~/Downloads`).
  2. Su Ubuntu 24.04 LTS le AppImage richiedono la libreria FUSE 2 (`libfuse2t64`), mentre su Ubuntu 22.04 LTS il pacchetto si chiama `libfuse2`:
     ```bash
     # Solo su Ubuntu 24.04 LTS:
     sudo apt update && sudo apt install -y libfuse2t64

     # Solo su Ubuntu 22.04 LTS:
     sudo apt update && sudo apt install -y libfuse2
     ```
  3. Rendi eseguibile il file `.AppImage` scaricato e avvialo (sostituisci `<NOME_FILE_LMSTUDIO.AppImage>` con il nome esatto del file presente nella cartella):
     ```bash
     chmod +x ~/Scaricati/<NOME_FILE_LMSTUDIO.AppImage>
     ~/Scaricati/<NOME_FILE_LMSTUDIO.AppImage>
     ```

### 7.2 Download e selezione del modello ESATTAMENTE compatibile
Il modulo `ai/lmstudio.py` valida in modo rigoroso il nome del modello installato (`OPERATIONAL_SPECS`) per impedire esecuzioni accidentali su modelli non conformi:

| Ruolo | ID canonico nell'app | Modello da cercare in LM Studio | Quantizzazione GGUF consigliata | Regole di riconoscimento nel codice (`ai/lmstudio.py`) |
| :--- | :--- | :--- | :--- | :--- |
| **Predefinito e raccomandato** | **`qwen2.5-7b-instruct`** | **Qwen2.5-7B-Instruct GGUF** | **`Q4_K_M`** (~4,7 GB) | L'ID in LM Studio deve contenere `qwen`, `2.5`, `7b`, `instruct` (e **non** `deepseek` né `qwen3`). |
| Alternativa supportata 1 | `meta-llama-3.1-8b-instruct` | **Meta-Llama-3.1-8B-Instruct GGUF** | `Q4_K_M` (~4,9 GB) | Deve contenere `llama`, `3.1`, `8b`, `instruct`. |
| Alternativa supportata 2 | `deepseek-r1-distill-qwen-7b` | **DeepSeek-R1-Distill-Qwen-7B GGUF** | `Q4_K_M` (~4,7 GB) | Deve contenere `deepseek` e `7b`, e **non** `llama` (attenzione: la variante `deepseek-r1-distill-llama-8b` viene rifiutata per questa specifica). |

**Procedura passo-passo dentro LM Studio**:
1. Apri la scheda **Discover** (icona della lente d'ingrandimento nella barra laterale sinistra di LM Studio).
2. Cerca **`Qwen2.5-7B-Instruct GGUF`** (ad esempio la build GGUF ufficiale Qwen o Bartowski/LM Studio Community).
3. Nell'elenco dei file disponibili sulla destra scegli la variante **`Q4_K_M`** (circa **4,68 GB**) e clicca su **Download**. Attendi il completamento del download.

### 7.3 Configurare Context Length (`8192`) e avviare il Local Server (`127.0.0.1:1234`)
1. In LM Studio apri la scheda **Developer** (icona `>_` o `<->` del Local Server nella barra sinistra).
2. Seleziona in alto il modello scaricato **`qwen2.5-7b-instruct`**; prima di caricarlo (oppure nelle impostazioni di caricamento *Load Model Config*):
   - Imposta **Context Length** ad almeno **`8192`** token (valore richiesto da `DEFAULT_OPERATIONAL_CONTEXT_LENGTH = 8192`; se lasci un contesto inferiore come `2048` o `4096`, i profili operativi ripiegheranno o segnaleranno spazio di contesto insufficiente).
   - Se hai una GPU dedicata compatibile, regola **GPU Offload** al massimo supportato dalla tua VRAM; se usi solo la CPU, lascia l'offload GPU disattivato o automatico.
3. Clicca su **Load Model** e attendi che lo stato diventi **Ready / Loaded**.
4. Attiva l'interruttore **Status: Stopped → Running** per avviare il server locale sulla porta predefinita **`1234`** (`http://127.0.0.1:1234`). Non attivare l'opzione *Serve on Local Network* se non necessario: mantieni l'ascolto solo in locale (`127.0.0.1`).

### 7.4 Verificare da terminale che il server LM Studio risponda
Senza avviare l'app, puoi controllare in 2 secondi che LM Studio esponga correttamente il modello:

- **Su Windows (PowerShell)**:
  ```powershell
  Invoke-RestMethod -Uri "http://127.0.0.1:1234/api/v0/models" | Select-Object -ExpandProperty data | Select-Object id, state, quantization, max_context_length
  ```
- **Su Ubuntu (bash)**:
  ```bash
  curl -s http://127.0.0.1:1234/api/v0/models | python3 -m json.tool
  ```

Risultato atteso: nell'elenco deve comparire un modello il cui `id` contiene `qwen2.5-7b-instruct` (es. `qwen2.5-7b-instruct` o `qwen/qwen2.5-7b-instruct`) con `"state": "loaded"`. Nell'interfaccia di ChatAnalysis i campi **`Modello LM Studio locale:`** e **`Modello LM Studio per Discovery:`** sono già precompilati con `qwen2.5-7b-instruct`, e `ai/lmstudio.py` risolve automaticamente l'identificativo esatto installato in LM Studio.

### 7.5 Rapporto tra modello, quantizzazione GGUF, memoria e tempi
- **Perché `Q4_K_M`**: riduce a ~4 bit per peso la memoria richiesta da un modello da 7 miliardi di parametri (~4,7 GB su disco invece di ~15 GB in `FP16`), mantenendo una buona aderenza agli schemi JSON strutturati richiesti dall'app.
- **Memoria (RAM / VRAM)**: oltre ai ~4,7 GB dei pesi, la finestra di contesto a `8192` token richiede memoria aggiuntiva per la *KV cache* (~1–1,5 GB). Con meno di 8 GB di RAM libera complessiva il sistema operativo ricorre al file di swap su disco, rallentando drasticamente ogni risposta fino al timeout (`240` secondi).
- **Tempi di risposta**: su GPU dedicata da 8+ GB VRAM l'analisi di un microchunk impiega pochi secondi; su sola CPU moderna può richiedere da 15 a 90 secondi per microchunk. Per conversazioni lunghe (es. oltre 1.000 messaggi suddivise in decine di blocchi) una Topic Discovery completa su sola CPU può richiedere da alcune decine di minuti a oltre un'ora: grazie ai **checkpoint SQLite**, ogni sotto-blocco completato viene salvato subito su disco.

---

## 8. Componenti multimediali (Tesseract OCR e Faster-Whisper STT)

L'estrazione di testo da foto (**OCR**) e la trascrizione di messaggi vocali (**STT**) sono **componenti opzionali e on-demand**: non partono mai automaticamente durante l'importazione della chat, ma solo quando l'utente clicca su **`🎙️ Elabora media localmente`**.

### 8.1 Installazione e configurazione di Tesseract OCR (Immagini)
La libreria Python `pytesseract` (già installata al Passo 6 con `pip`) è soltanto un involucro: richiede che sul sistema operativo sia installato il programma nativo **Tesseract OCR** con i pacchetti lingua **italiano (`ita`)** e **inglese (`eng`)** (configurazione predefinita `lang="ita+eng"` in `multimodal/ocr.py`).

#### Su Windows (PowerShell)
1. Scarica l'installer ufficiale Windows mantenuto da **UB Mannheim**:
   https://github.com/UB-Mannheim/tesseract/wiki
2. Avvia il file `tesseract-ocr-w64-setup-*.exe`:
   - Durante la procedura guidata espandi la voce **`Additional language data (download)`** e **spunta `Italian`** (`ita`), lasciando attivo `English` (`eng`).
   - Completa l'installazione nel percorso predefinito `C:\Program Files\Tesseract-OCR`.
3. **Configurare il `PATH` di Windows (passaggio fondamentale)**:
   L'installer UB Mannheim spesso **non aggiunge automaticamente** `C:\Program Files\Tesseract-OCR` alla variabile `PATH`, e `TesseractImageTextExtractor.is_available()` cerca `tesseract` nel `PATH` di sistema.
   - **Per aggiungerlo stabilmente al `PATH` del tuo utente Windows** (senza richiedere privilegi di amministratore di sistema), esegui una sola volta in PowerShell:
     ```powershell
     $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
     if ($UserPath -notlike "*C:\Program Files\Tesseract-OCR*") {
         [Environment]::SetEnvironmentVariable("Path", "$UserPath;C:\Program Files\Tesseract-OCR", "User")
         Write-Host "Percorso Tesseract aggiunto al PATH Utente. Chiudi e riapri PowerShell."
     }
     ```
   - **Chiudi e riapri PowerShell** (oppure, per renderlo attivo subito nella finestra corrente senza riaprirla, esegui `$env:PATH = "C:\Program Files\Tesseract-OCR;" + $env:PATH`).
4. **Verifica del binario e delle lingue `ita` + `eng`**:
   ```powershell
   tesseract --version
   tesseract --list-langs
   ```
   Risultato atteso: la versione di Tesseract (es. `tesseract v5.x`) e un elenco lingue che include sia **`eng`** sia **`ita`**.

#### Su Ubuntu (bash)
Su Ubuntu sia il binario sia i file `tessdata` per italiano e inglese si installano direttamente con `apt`:

```bash
sudo apt update
sudo apt install -y tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng
tesseract --version
tesseract --list-langs
```

Risultato atteso: elenco lingue contenente almeno `eng`, `ita` e `osd`.

### 8.2 Trascrizione vocale audio: Faster-Whisper, CTranslate2 e PyAV
- **Nessun binario `ffmpeg` esterno necessario**: il pacchetto `faster-whisper` installato con `pip install -e ".[ui,ocr,stt,image]"` installa automaticamente **`ctranslate2`** (motore di inferenza C++ ottimizzato) e **`av` (PyAV)**, le cui wheel ufficiali su Windows e Linux x86_64 incorporano già le librerie di decodifica audio/video FFmpeg necessarie per leggere i file vocali WhatsApp `.opus`, `.ogg`, `.mp3`, `.m4a` e `.wav`. Non è quindi necessario installare `ffmpeg` separatamente nel `PATH`.
- **Download automatico del modello vocale al primo utilizzo**: `FasterWhisperTranscriber` utilizza di default il modello multilingua **`base`** (`model_name="base"`, `device="cpu"`, `compute_type="int8"`). La **prima volta** in assoluto in cui premi `🎙️ Elabora media localmente` su una chat con note vocali, `faster-whisper` scarica automaticamente i pesi CTranslate2 (`Systran/faster-whisper-base`, ~145 MB) nella cache locale del tuo utente (`%USERPROFILE%\.cache\huggingface\hub` su Windows, `~/.cache/huggingface/hub` su Ubuntu). Se prevedi di lavorare in un ambiente poi disconnesso da Internet, puoi pre-scaricare il modello `base` una tantum mentre sei ancora online:

  - **Windows (PowerShell)**:
    ```powershell
    .\.venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8'); print('Modello STT base pronto in cache locale')"
    ```
  - **Ubuntu (bash)**:
    ```bash
    source .venv/bin/activate
    python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8'); print('Modello STT base pronto in cache locale')"
    ```

### 8.3 Cosa succede senza OCR o STT e come fare una prova innocua
- **Comportamento senza OCR o senza STT**: l'applicazione **non va mai in crash**. Importazione chat, lettura messaggi testuali, ricerca deterministica e analisi AI dei messaggi testuali funzionano normalmente. Nella scheda `1. Panoramica` l'indicatore corrispondente mostrerà semplicemente `Non installato` (es. `tesseract non rilevato nel PATH di sistema`), oppure se solo uno dei due motori è presente produrrà uno stato `PARTIAL` elaborando i media supportati dal motore disponibile.
- **Prova rapida e innocua dell'OCR (senza dati reali)**:
  - **Windows (PowerShell)**:
    ```powershell
    .\.venv\Scripts\python.exe -c "from multimodal.ocr import TesseractImageTextExtractor; from multimodal.transcriber import FasterWhisperTranscriber; print('OCR disponibile:', TesseractImageTextExtractor.is_available()); print('STT disponibile:', FasterWhisperTranscriber.is_available())"
    ```
  - **Ubuntu (bash)**:
    ```bash
    source .venv/bin/activate
    python -c "from multimodal.ocr import TesseractImageTextExtractor; from multimodal.transcriber import FasterWhisperTranscriber; print('OCR disponibile:', TesseractImageTextExtractor.is_available()); print('STT disponibile:', FasterWhisperTranscriber.is_available())"
    ```
  Entrambe le righe devono stampare `True`.

---

## 9. Configurazione (variabili d'ambiente, parametri UI e costanti interne)

Per evitare qualsiasi ambiguità, questa sezione separa ciò che è configurabile tramite **variabili d'ambiente**, tramite **file `.streamlit/config.toml`**, tramite **controlli nella UI**, e ciò che è **fissato internamente nel codice** (privo di selettore grafico).

### 9.1 Variabili d'ambiente realmente lette dal codice

| Variabile d'ambiente | Modulo che la legge | Default se non impostata | Effetto e quando usarla |
| :--- | :--- | :--- | :--- |
| **`CHATANALYSIS_CHECKPOINT_DIR`** | `ui/application.py`, `ui/presentation.py` | `.checkpoints` (nella root del progetto) | Cambia la cartella locale in cui viene creato/letto il database SQLite `checkpoints.db`. Utile per isolare sessioni peritali distinte in cartelle dedicate. |
| **`LM_STUDIO_API_TOKEN`** | `ai/lmstudio.py` | *(vuota — nessun header Auth)* | Token Bearer opzionale inviato a LM Studio solo se hai abilitato l'autenticazione sul server locale LM Studio. |
| **`PYTHONUTF8`** | Interprete Python (`scripts/run_streamlit_windows.ps1`) | Dipende dal sistema | Impostata a `1` garantisce codifica UTF-8 uniforme su console Windows per caratteri accentati ed emoji. |
| **`RUN_LM_STUDIO_BENCHMARK`** | `ai/experiment.py` | *(disabilitata)* | Solo per sviluppatori (`=1`): abilita il runner CLI di benchmark locale `python -m ai.experiment`. |
| **`RUN_CLOUD_E2E`** / **`CLOUD_LLM_API_KEY`** | `ai/cloud_experiment.py` | *(disabilitate)* | Solo per test sperimentali sul dataset sintetico congelato (`test_data/cloud_e2e/`), protette da doppio opt-in obbligatorio (`RUN_CLOUD_E2E=1` + `--force-run`). **Non vengono mai usate dall'app Streamlit né sui file caricati dall'utente.** |

**Come impostare una variabile d'ambiente prima dell'avvio (esempio: cartella checkpoint dedicata)**:
- **Windows (PowerShell)**:
  ```powershell
  $env:CHATANALYSIS_CHECKPOINT_DIR = "C:\Percorso\MieiCheckpointCasoA"
  ```
- **Ubuntu (bash)**:
  ```bash
  export CHATANALYSIS_CHECKPOINT_DIR="$HOME/checkpoint_caso_a"
  ```

### 9.2 Parametri nel file versionato `.streamlit/config.toml`
- `server.address = "127.0.0.1"` e `browser.serverAddress = "127.0.0.1"`: espone l'interfaccia web **solo** al computer locale (mai sulla rete LAN).
- `browser.gatherUsageStats = false`: disattiva la telemetria Streamlit.
- `server.maxUploadSize = 2048` e `server.maxMessageSize = 2048`: allinea il limite di caricamento browser al limite applicativo di **2048 MB (2 GB)**.
- `client.showErrorDetails = "none"`: impedisce che eventuali traceback grezzi vengano stampati nella pagina browser.

### 9.3 Parametri selezionabili direttamente nell'interfaccia utente (UI)
- **Profilo Operativo di Analisi** (pannello espandibile `⚙️ Profilo Operativo & Gestione Checkpoint` in `5. Analisi topic`):
  - **`Bilanciato (4200 token - Consigliato)`** *(default UI)*: budget blocco `4.200` token / `20.000` caratteri.
  - **`Prudente (2500 token - Massima sicurezza)`**: budget blocco `2.500` token / `12.000` caratteri.
  - **`Prestazioni elevate (5200 token - Sperimentale)`**: budget blocco `5.200` token / `25.000` caratteri.
- **Modello LM Studio locale** (`Topic Detection` e `Topic Discovery`): campo di testo precompilato con **`qwen2.5-7b-instruct`**.
- **Numero max topic** (`Topic Discovery`): selettore numerico da `1` a `25` (default **`10`**).
- **Modalità di Matching e Limite Risultati** (`4. Ricerca`): `PHRASE`, `ALL_TERMS`, `ANY_TERM`, `EXACT`; limite da `0` (illimitato) a `500` (default **`50`**).
- **Modalità privacy avanzata** (esportazione JSON/CSV): casella **`🔒 Modalità privacy avanzata`**, **attiva (`True`) di default** (`SAFE_REDACTED`).

### 9.4 Parametri fissati solo internamente nel codice (nessuna opzione nella UI)
Questi valori sono costanti architetturali definite nei moduli Python e **non possiedono un controllo modificabile nell'interfaccia grafica**:
- **Endpoint LM Studio**: `http://127.0.0.1:1234` (`ai/lmstudio.py`, `ui/application.py`).
- **Context length operativo**: `8192` token (`DEFAULT_OPERATIONAL_CONTEXT_LENGTH`), con margine di sicurezza `1000` token (`DEFAULT_SAFETY_MARGIN_TOKENS`).
- **Temperatura e Seed LLM**: `temperature = 0.0` (`DEFAULT_OPERATIONAL_TEMPERATURE`), `seed = 42` per massimizzare la riproducibilità deterministica.
- **Timeout per singola richiesta HTTP a LM Studio**: `240.0` secondi (`DEFAULT_OPERATIONAL_TIMEOUT_SECONDS`).
- **Token massimi di risposta (output)**: `512` token per Topic Detection (`DEFAULT_OPERATIONAL_MAX_TOKENS`); calcolato dinamicamente in base a `max_topics` per Topic Discovery (`calculate_dynamic_output_budget`).
- **Limiti chunk e microchunk** (`analysis/models.py`, `ai/chunking.py`, `analysis/service.py`): massimo `60` bundle per chunk principale; pre-splitting automatico in **microchunk da massimo `8` messaggi sostanziali** (e `1500` token stimati); profondità massima di bisezione adattiva `3`; circuit breaker dopo `3` timeout consecutivi o `10` timeout totali nella run; budget temporale massimo di `600` secondi per singolo albero di microchunk in Detection.
- **Limiti anti-Zip-Bomb e upload** (`core/config.py`): upload max `2048 MB` (2 GB); dimensione totale decompressa ZIP max `4096 MB` (4 GB); numero massimo di file nello ZIP `50.000`; dimensione singolo file estratto max `2048 MB`; rapporto di compressione max `100.0` per file > 1 MB.
- **Motori OCR e STT**: Tesseract con `lang="ita+eng"` e timeout `30.0s` per immagine (`multimodal/ocr.py`); Faster-Whisper con `model_name="base"`, `device="cpu"`, `compute_type="int8"` (`multimodal/transcriber.py`).

---

## 10. Avvio ogni volta (sequenza operativa quotidiana)

### Differenza tra primo avvio e avvii successivi
- **Al primo avvio assoluto**: potresti ricevere da Windows Firewall la richiesta di consentire l'accesso locale a Python/LM Studio (non serve esporli su reti pubbliche: l'app usa solo `127.0.0.1`), e la prima volta che avvii l'elaborazione audio verrà scaricato in cache il modello vocale `base` (~145 MB) se non lo hai già pre-scaricato al punto 8.2.
- **In tutti gli avvii successivi**: non viene scaricato nulla da Internet; basta avviare LM Studio (se servono le funzioni AI) e lanciare Streamlit dall'ambiente `.venv`.

### 10.1 Sequenza completa di avvio su Windows (PowerShell)
1. *(Solo se devi usare Topic Discovery, Topic Detection o Verifica di consistenza)*: apri **LM Studio**, vai nella scheda **Developer**, verifica che il modello **`qwen2.5-7b-instruct`** sia caricato (**Context Length ≥ `8192`**) e che il **Local Server** sia impostato su **Running** (`127.0.0.1:1234`).
2. Apri **Windows PowerShell**, assicurati che Tesseract sia nel `PATH` (automatico se hai eseguito il Passo 3 della Sezione 8.1) ed entra nella cartella del progetto:
   ```powershell
   cd "<PERCORSO_CARTELLA_PROGETTO>"
   ```
3. Avvia l'applicazione scegliendo uno dei due modi equivalenti:
   - **Modo diretto (nessuna Execution Policy richiesta)**:
     ```powershell
     $env:PYTHONUTF8 = "1"
     .\.venv\Scripts\python.exe -m streamlit run app.py
     ```
   - **Tramite lo script di bootstrap PowerShell incluso (`scripts\run_streamlit_windows.ps1`)** (rileva automaticamente `.venv`, imposta `PYTHONUTF8=1` e con `-Browser` apre la scheda del browser):
     ```powershell
     powershell -ExecutionPolicy Bypass -File .\scripts\run_streamlit_windows.ps1 -Browser
     ```
4. Apri il browser web all'indirizzo locale:
   **http://127.0.0.1:8501**

### 10.2 Sequenza completa di avvio su Ubuntu (Terminale bash)
1. *(Solo se devi usare le funzioni AI)*: avvia **LM Studio** (`.AppImage`), carica **`qwen2.5-7b-instruct`** con Context Length `8192` e porta il **Local Server** su **Running** (`127.0.0.1:1234`).
2. Apri un **Terminale**, entra nella cartella del progetto, attiva `.venv` e avvia Streamlit:
   ```bash
   cd "<PERCORSO_CARTELLA_PROGETTO>"
   source .venv/bin/activate
   export PYTHONUTF8=1
   python -m streamlit run app.py
   ```
3. Apri il browser all'indirizzo:
   **http://127.0.0.1:8501**

### 10.3 Procedura corretta per interrompere l'applicazione
1. Se un'analisi AI è in corso, puoi attendere la fine del blocco corrente oppure interrompere: tutti i chunk/microchunk già validati fino a quel momento restano salvati in modo atomico in `.checkpoints/checkpoints.db`.
2. Torna nella finestra del terminale (PowerShell o bash) in cui gira Streamlit e premi **`Ctrl+C`** (una o due volte finché non ricompare il prompt dei comandi).
3. Chiudi la scheda del browser `http://127.0.0.1:8501`.
4. In **LM Studio**, se hai terminato il lavoro e vuoi liberare i ~6 GB di RAM/VRAM, clicca su **Unload** accanto al modello e porta il Local Server su **Stopped**.


---

## 11. Guida all'uso dettagliata passo per passo

Una volta aperta l'interfaccia nel browser su **`http://127.0.0.1:8501`**, sulla sinistra trovi il menu laterale **`Navigazione`** articolato in cinque sezioni numerate:
1. **`1. Panoramica`**
2. **`2. Importazione`**
3. **`3. Esplora conversazione`**
4. **`4. Ricerca`**
5. **`5. Analisi topic`**

Se nessun dataset è caricato, `1. Panoramica` mostra la **pagina iniziale**: una breve presentazione del sistema, lo stato rapido (dataset, LM Studio, rete), i primi passi e il pulsante **`🚀 Carica Dataset Dimostrativo`**. LM Studio viene interrogato solo quando premi **`Verifica connessione`**: l'applicazione non si collega in automatico.

Di seguito trovi il flusso operativo completo nell'ordine raccomandato.

### Passo 1 — Importare una chat (o caricare il dataset dimostrativo)
1. Nel menu a sinistra seleziona **`2. Importazione`**.
2. Hai due modalità distinte:
   - **Per una prova immediata senza file propri** (*Sezione `1. Modalità Dimostrativa (Dati Sintetici)`*): clicca su **`🚀 Carica Dataset Dimostrativo`**. Verrà generata in memoria una conversazione fittizia multilingua (italiano, inglese, spagnolo) con 12 sezioni di evidenza già pronte.
   - **Per analizzare un tuo file reale** (*Sezione `2. Modalità File Reale (Ingestion Forense Locale)`*):
     1. Nel menu a tendina **`Tipo di sorgente forense da importare`** scegli **esattamente** il formato corrispondente al tuo file:
        - `WhatsApp export chat (TXT / ZIP)` per esportazioni `.txt` o archivi `.zip` prodotti dalla funzione *Esporta chat* di WhatsApp;
        - `WhatsApp msgstore (SQLite)` per un database `msgstore.db` non cifrato; in questo caso compare anche il riquadro facoltativo **`wa.db contatti — opzionale`** in cui puoi caricare il database della rubrica per associare i nomi visualizzati ai numeri/JID;
        - `WhatsApp wa.db (SQLite)` solo se vuoi ispezionare le anagrafiche di `wa.db` da solo (nota: `wa.db` non contiene messaggi, quindi produrrà stato `AUXILIARY_ONLY`);
        - `Cellebrite (CSV)`, `Cellebrite (JSON)` o `Cellebrite (XML)` per i rispettivi report esportati da Cellebrite Reader / Physical Analyzer.
     2. Trascina o seleziona il file nel riquadro **`Seleziona file primario (...)`** (dimensione massima **2 GB**).
     3. Clicca sul pulsante **`📥 Importa e analizza struttura`**.
3. Al termine vedrai il pannello **`📋 Riepilogo Ultima Ingestion Reale`** con l'impronta crittografica **`SHA-256`** calcolata sul file sorgente, il numero di `Record Raw`, `Messaggi Unificati`, `Conversazioni` ed eventuali `Avvisi Non Bloccanti`.

### Passo 2 — Scegliere la conversazione e distinguere chat individuali (`direct`) e di gruppo (`group`)
1. Se il file importato contiene una o più chat (ad esempio un database `msgstore.db` o un export Cellebrite multi-conversazione), subito sotto il riepilogo compare la sezione **`💬 Selezione Conversazione`**.
2. Apri il menu **`Seleziona la conversazione attiva per l'analisi e la ricerca`**:
   - Per proteggere la riservatezza a schermo, le voci del menu usano etichette pseudonimizzate nella forma `Conversazione 1 — <N> messaggi — [<id_breve>]` (oppure `Messaggi non associati — <N> messaggi — [<id_breve>]` per eventuali record privi di chiave chat).
   - Seleziona la conversazione desiderata e clicca su **`📂 Apri conversazione`**.
3. **Come distinguere una conversazione individuale (`direct`) da una di gruppo (`group`)**:
   - Passa alla scheda **`1. Panoramica`** e apri il pannello a comparsa **`Provenance Tecnica (Metadati Interni)`**: alla voce **`Chat ID Tecnico`** vedrai l'identificativo canonico della chat:
     - Nei database WhatsApp/Cellebrite un identificativo che termina con **`@s.whatsapp.net`** identifica una chat individuale 1-a-1 (`direct`), mentre un identificativo che termina con **`@g.us`** identifica una chat di gruppo (`group`).
     - Negli export testuali/ZIP WhatsApp (`export:<nome_chat>`), il modello unificato (`unified/models.py`) distingue automaticamente le chat a 2 interlocutori (`direct`) dalle chat con 3 o più partecipanti o avvisi di sistema di creazione gruppo (`group`), preservando in ogni messaggio il mittente specifico (`sender`) anche durante l'analisi LLM.

### Passo 3 — Elaborare foto (OCR) ed eventuali note vocali (STT) PRIMA dell'analisi AI
1. Apri **`1. Panoramica`** (oppure **`3. Esplora conversazione`**).
2. Se la conversazione contiene allegati fotografici o vocali (ad esempio hai importato un archivio `.zip` di WhatsApp esportato *con media*), compariranno gli indicatori di conteggio media e la sezione **`🎬 Elaborazione Multimediale Locale (On-Demand)`** con tre metriche di stato:
   - **`Speech-to-Text (Audio)`**: mostra `Faster-Whisper` se il modulo è installato, altrimenti `Non installato`.
   - **`OCR (Immagini)`**: mostra `Tesseract OCR` se il binario `tesseract` è nel `PATH`, altrimenti `Non installato`.
   - **`Vision (Scena)`**: mostra sempre `Non configurato` sui file reali (la descrizione automatica di scene prive di testo non è implementata).
3. Clicca su **`🎙️ Elabora media localmente`** (se un tentativo precedente era riuscito solo in parte o era fallito, il pulsante si chiamerà **`🔁 Riprova elaborazione media`** o **`🔁 Riprova elaborazione media parziale`**):
   - Le immagini supportate (`.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`, `.tif`, `.tiff`) vengono lette dall'archivio locale e passate a Tesseract (`ita+eng`), generando nuove sezioni di evidenza di tipo **`IMAGE_OCR_TEXT`** se viene rilevato testo leggibile.
   - I messaggi vocali supportati (`.opus`, `.ogg`, `.mp3`, `.m4a`, `.wav`, `.aac`, `.flac`) vengono trascritti localmente da Faster-Whisper, generando nuove sezioni di evidenza di tipo **`AUDIO_TRANSCRIPTION`**.
   - I testi originali dei messaggi (`MESSAGE_TEXT`) restano intatti e separati dalle sezioni derivate.
   > **Regola operativa importante**: se intendi estrarre OCR/STT, fallo **prima** di avviare `Topic Discovery`. Poiché l'aggiunta di trascrizioni vocali o testi OCR modifica il contenuto e l'impronta crittografica (`document_fingerprint`) del documento in sessione, se esegui `🎙️ Elabora media localmente` *dopo* una `Topic Discovery` il sistema rileverà correttamente uno `SNAPSHOT_MISMATCH` e ti chiederà di rieseguire la Discovery sul documento arricchito prima di lanciare la verifica di consistenza.

### Passo 4 — Esaminare i messaggi e la provenance forense
1. Nel menu laterale clicca su **`3. Esplora conversazione`**.
2. I messaggi e le relative evidenze vengono elencati nell'**ordine naturale di acquisizione**.
3. Puoi restringere la vista usando i tre menu in alto:
   - **`Filtra per Tipo Evidenza`** (`ALL`, `MESSAGE_TEXT`, `AUDIO_TRANSCRIPTION`, `IMAGE_OCR_TEXT`, o `VISION_DESCRIPTION` nel dataset `DEMO`);
   - **`Filtra per Lingua`** (es. `ALL`, `it`, `en`, `es`, `und`);
   - **`Filtra per Sorgente`** (es. `whatsapp_export`, `msgstore_db`, `cellebrite_xml`, ecc.).
4. Sotto ogni riquadro clicca su **`Dettagli Provenance Forense`** per leggere gli identificativi immutabili associati a quel singolo contenuto:
   - **`Evidence ID`**: l'identificativo univoco dell'evidenza (es. `ev:unified:whatsapp_export:line_2:msg_text` oppure `ev:...:ocr:0`), che è **lo stesso codice citato dall'AI** nei risultati di Topic Discovery e Topic Detection;
   - **`Message ID`**, **`Source Record ID`**, **`Source Name`** e **`Ordinal`**.

### Passo 5 — Eseguire una ricerca deterministica
1. Seleziona **`4. Ricerca`** nel menu laterale.
2. Nel campo **`Termine o frase da ricercare`** inserisci una parola o un'espressione esatta (questa ricerca è puramente lessicale e deterministica: non usa né richiede LM Studio).
3. Scegli la **`Modalità di Matching`**:
   - **`PHRASE`**: cerca l'intera sequenza di caratteri come sottostringa contigua (case-insensitive);
   - **`ALL_TERMS`**: restituisce solo le evidenze che contengono **tutti** i vocaboli inseriti come parole intere, anche distanti fra loro;
   - **`ANY_TERM`**: restituisce le evidenze che contengono **almeno uno** dei vocaboli inseriti come parola intera;
   - **`EXACT`**: richiede che l'intero testo dell'evidenza coincida esattamente (a meno di maiuscole/spazi esterni) con la stringa cercata.
4. Se desideri, filtra anche per **`Tipo Evidenza`**, **`Lingua`**, **`Sorgente`** e imposta **`Limite Risultati (0 = illimitato)`** (predefinito `50`, massimo `500`), quindi clicca su **`Cerca Evidenze`**.
5. Ogni risultato mostra il testo integrale dell'evidenza e, aprendo **`Provenance & Termini`**, l'`Evidence ID` e i termini riconosciuti.

### Passo 6 — Avviare `Topic Discovery (Argomenti Emersi)` e leggere le evidenze
1. Assicurati che **LM Studio** sia avviato con **`qwen2.5-7b-instruct`** caricato (`Context Length >= 8192`) e il server locale attivo su `127.0.0.1:1234`.
2. Nel menu laterale apri **`5. Analisi topic`**.
3. *(Opzionale)* Apri il pannello **`⚙️ Profilo Operativo & Gestione Checkpoint`** per verificare il profilo attivo (**`Bilanciato (4200 token - Consigliato)`** è il default raccomandato) e apri **`📊 Profilo Preventivo Dataset & Stima Inferenze`** per controllare quanti messaggi, token stimati e blocchi previsti compongono la conversazione.
4. Clicca sulla seconda scheda in alto: **`Topic Discovery (Argomenti Emersi)`**.
5. Verifica i due campi:
   - **`Modello LM Studio per Discovery:`** lascia `qwen2.5-7b-instruct` (o un altro modello compatibile caricato in LM Studio);
   - **`Numero max topic:`** scegli il numero massimo di temi sintetici da estrarre (da `1` a `25`, default **`10`**).
6. Clicca su **`🚀 Esegui Topic Discovery (LIVE)`** e attendi il completamento della barra di avanzamento.
7. Al termine esamina:
   - Il **banner di stato della run**:
     - `COMPLETE`: tutti i blocchi sono stati analizzati e validati;
     - `PARTIAL`: alcuni sotto-blocchi sono riusciti e altri sono falliti (i topic emersi riguardano solo la porzione analizzata);
     - `COMPLETE_EMPTY_UNVERIFIED`: tutti i blocchi sono stati elaborati ma il modello non ha restituito argomenti da testo sostanziale (mostrato sempre come avviso giallo perché **non equivale** a certificare l'assenza di temi);
   - Il pannello **`📊 Diagnostica analisi`**, che riporta `Blocchi completati`, `Blocchi falliti`, `Recuperati da checkpoint`, `Evidenze analizzate` ed `Evidenze non analizzate`. Se uno o più sotto-blocchi sono andati in timeout o errore temporaneo, puoi premere **`🔄 Riprova solo i blocchi falliti`** senza ricalcolare i blocchi già completati;
   - Le **schede dei singoli argomenti emersi**: ciascun topic mostra titolo (`label`), descrizione sintetica in italiano (`short_description`) e l'elenco puntuale degli **`Evidence ID`** a supporto, che puoi riscontrare direttamente in `3. Esplora conversazione` o `4. Ricerca`.

### Passo 7 — Avviare `Topic Detection (Verifica Mirata)` e interpretare `PRESENT`, `ABSENT`, `UNCERTAIN`
1. Sempre in **`5. Analisi topic`**, apri la prima scheda **`Topic Detection (Verifica Mirata)`**.
2. Nella sezione **`🎯 Area 1: Verifica un argomento`** compila:
   - **`Argomento:`** il tema specifico che vuoi verificare (es. `Pagamenti in contanti`, `Consegna merce`, `Prenotazione viaggio`);
   - **`Modello LM Studio locale:`** `qwen2.5-7b-instruct`;
   - **`Descrizione opzionale:`** puoi lasciarla vuota (*modalità a sola label*) oppure specificare in 1–2 frasi cosa intendi esattamente includere o escludere.
3. Clicca su **`🚀 Esegui Topic Detection`**.
4. Come interpretare i **tre esiti possibili (`TopicDecision`)**:
   - **`PRESENT`** (`🟢 PRESENTE`): il modello ha individuato nella conversazione almeno un'evidenza pertinente al tema richiesto e ne elenca gli `Evidence ID` verificati insieme alla motivazione (`rationale`). Vai sempre a rileggere quei messaggi originali per confermare il contesto.
   - **`ABSENT`** (`⚪ ASSENTE`): nei blocchi analizzati il modello non ha riscontrato evidenze riconducibili al tema cercato. Ricorda che un esito `ABSENT` non esclude l'uso di parole in codice, allusioni indirette o allegati non leggibili.
   - **`UNCERTAIN`** (`🟡 INCERTO`): sono presenti indizi ambigui, frammentari o incompleti (oppure alcuni sotto-blocchi non hanno consentito una decisione netta), insufficienti per affermare `PRESENT` ma tali da non poter dichiarare `ABSENT`. È un invito diretto all'ispezione manuale dei passaggi segnalati.
5. Nella sottostante **`📂 Area 2: Filtra risultati Detection già eseguiti`** puoi rivedere tutte le verifiche lanciate nella sessione filtrandole tramite **`Filtro Decisione`** (`ALL`, `PRESENT`, `ABSENT`, `UNCERTAIN`).

### Passo 8 — Eseguire la verifica di consistenza («Prova del Nove») e interpretarne gli stati
Quando la **`Topic Discovery`** propone un argomento, puoi sottoporlo a una **verifica indipendente** senza uscire dalla scheda `Topic Discovery (Argomenti Emersi)`:
1. Individua la scheda del topic emerso dalla Discovery e clicca sul pulsante **`🔬 Verifica con Topic Detection`**.
2. Dietro le quinte (`analysis/service.py`, `analysis/verification.py`) l'applicazione lancia una **nuova inferenza indipendente di Topic Detection** sull'intero documento passando al modello **esclusivamente il titolo e la descrizione del topic**, **senza mai rivelargli gli `evidence_id` citati dalla Discovery**, e confronta poi i due risultati.
3. Sotto la scheda del topic comparirà il riquadro **`🔬 Risultato Verifica Indipendente`** con uno dei **quattro stati di `ConsistencyStatus`** effettivamente implementati:

   | Stato `ConsistencyStatus` | Badge nella UI | Significato esatto e come interpretarlo |
   | :--- | :--- | :--- |
   | **`CONSISTENT`** | `✅ COERENTE` | Sia la Discovery sia la Detection indipendente rilevano l'argomento (`PRESENT`) e **condividono almeno un `evidence_id` identico** (`overlap_evidence_ids` non vuoto). Indica elevata convergenza tra scansione aperta e verifica mirata. |
   | **`PARTIALLY_CONSISTENT`** | `🔵 PARZIALMENTE COERENTE` | Entrambe le analisi rilevano il tema (`PRESENT`), ma **citano insiemi disgiunti di `evidence_id`** (`overlap_evidence_ids` vuoto, ad esempio passaggi diversi della stessa chat sullo stesso argomento). Controlla entrambi i gruppi di evidenze. |
   | **`DISCREPANCY_ABSENT`** | `⚠️ DISCREPANZA (ABSENT)` | La Discovery aveva estratto l'argomento, ma la Detection indipendente (interrogata su titolo + descrizione) ha risposto **`ABSENT`**. **Non è un errore tecnico del programma** né la prova che il tema non esista: segnala che la formulazione astratta generata dalla Discovery potrebbe essere troppo generica, sovra-interpretata o lessicalmente distante dal testo letterale dei messaggi. Controlla direttamente gli `evidence_id` della Discovery. |
   | **`DISCREPANCY_UNCERTAIN`** | `🟡 INCERTO (UNCERTAIN)` | La Discovery aveva estratto l'argomento, mentre la Detection indipendente ha risposto **`UNCERTAIN`** perché ha ritenuto i riferimenti testuali ambigui o parziali. |

### Passo 9 — Esportare i risultati in formato JSON e CSV
- **In `Topic Detection` e `Topic Discovery`**:
  - La casella **`🔒 Modalità privacy avanzata (oscura label, descrizioni e motivazioni)`** è **selezionata (`True`) per impostazione predefinita**: in questa modalità (`SAFE_REDACTED`) il file JSON esportato tramite **`📥 Esporta rapporto completo (JSON)`** contiene metriche, tempi, hash di configurazione, stati dei chunk e conteggi, ma **omette i testi dei messaggi, i titoli/descrizioni libere e le motivazioni testuali**, risultando adatto ad audit tecnici o condivisione diagnostica.
  - Se deselezioni consapevolmente la casella `🔒 Modalità privacy avanzata`, il file JSON viene generato in modalità **`LOCAL_UNREDACTED_CAUTION`** includendo etichette, descrizioni ed `evidence_id` (da conservare con le stesse cautele riservate agli atti della conversazione).
  - In `Topic Discovery` è disponibile anche **`📥 Esporta diagnostica (CSV sicuro)`** per scaricare la tabella tecnica chunk-per-chunk priva di testi conversazionali.
- **Nella scheda della verifica di consistenza («Prova del Nove»)**:
  - Clicca su **`⬇️ Esporta verifica JSON (SAFE_REDACTED)`** per salvare su disco il rapporto crittograficamente collegato (`discovery_run_id` + `detection_run_id` + stato di consistenza). Nota: i risultati di verifica restano in RAM solo finché la sessione browser resta aperta, quindi esporta il JSON se desideri archiviarlo.
- **Utilità opzionale da riga di comando (`profiler`)**:
  - Se desideri generare un inventario tecnico preliminare in sola lettura (conteggio file, tabelle SQLite, colonne CSV/JSON/XML, conteggio media e anomalie strutturali, anonimizzato di default) su una cartella o file senza aprire il browser, puoi eseguire:
    - **Windows (PowerShell)**: `.\.venv\Scripts\python.exe -m profiler "<PERCORSO_SORGENTE>" --output output`
    - **Ubuntu (bash)**: `python -m profiler "<PERCORSO_SORGENTE>" --output output`

---

## 12. Lingue e strategie di analisi (`DIRECT_MULTILINGUAL` vs `TRANSLATE_FIRST`)

Il codice (`ai/models.py`, `ai/topics.py`, `ai/translation.py`) definisce due strategie distinte nell'enumerazione `AnalysisLanguageStrategy`, che **non vanno mai confuse**:

1. **`DIRECT_MULTILINGUAL` (Analisi multilingua diretta — unica strategia usata nella UI)**:
   - Il modello LLM riceve i messaggi **nella loro lingua originale** (italiano, inglese, spagnolo, chat miste o dialettali, senza alcuna traduzione preventiva del testo delle evidenze).
   - Le istruzioni di sistema del prompt richiedono al modello di redigere **in lingua italiana** i soli metadati analitici prodotti in uscita (`label` del topic, `short_description` e `rationale`), continuando a citare gli `evidence_id` originali.
   - **Vantaggio forense**: evita che una traduzione automatica intermedia distorca sfumature, gergo, nomi propri o termini tecnici prima del ragionamento del modello, dimezzando inoltre il numero di chiamate LLM necessarie.

2. **`TRANSLATE_FIRST` (Traduzione preventiva prima dell'analisi — solo API Python / benchmark)**:
   - Prima di eseguire Topic Detection o Topic Discovery, un componente dedicato (`EvidenceTranslator` in `ai/translation.py`) traduce ciascuna sezione di evidenza non italiana in italiano mantenendo l'ancoraggio all'`evidence_id` originale e verificando che l'espansione del testo tradotto non superi i limiti di sicurezza (`verify_translated_document_size`, max `8.000` caratteri). Solo dopo la traduzione il testo tradotto viene passato all'analizzatore dei topic.

### Dove si selezionano e quali funzioni le supportano
- **Nell'interfaccia grafica Streamlit (`app.py`, `ui/`)**: **NON esiste un selettore grafico per cambiare strategia linguistica**. Il servizio operativo dell'applicazione (`ui/application.py` → `OperationalAnalysisService`) opera **sempre ed esclusivamente in modalità `DIRECT_MULTILINGUAL`** sia per `Topic Detection`, sia per `Topic Discovery`, sia per la `Verifica di consistenza`.
- **A livello programmatico (API Python interna)**: il parametro `strategy=AnalysisLanguageStrategy.TRANSLATE_FIRST` (insieme a un'istanza di `EvidenceTranslator`) è supportato dai metodi Python `TopicDetectionAnalyzer.detect_topic(...)` e `TopicDiscoveryAnalyzer.discover_topics(...)` in `ai/topics.py`, pensati per confronti sperimentali di laboratorio (`ai/benchmark.py` e `ai/cloud_experiment.py`).

---

## 13. Chunk, microchunk e checkpoint SQLite

### 13.1 Cosa sono chunk e microchunk e perché servono
Un modello LLM locale ha una finestra di contesto finita (`8192` token) e tende a perdere precisione sugli `evidence_id` o a troncare il JSON quando riceve centinaia di messaggi in un solo blocco. Per analizzare in sicurezza anche conversazioni di migliaia di messaggi, `OperationalAnalysisService` (`analysis/service.py`) applica una scomposizione gerarchica deterministica:
1. **Chunk principali**: il documento viene dapprima suddiviso in blocchi ordinati (`split_document_into_chunks` in `ai/chunking.py`) che rispettano sia il limite massimo di `60` messaggi per chunk sia il budget di token/caratteri del profilo scelto (es. `4.200` token / `20.000` caratteri nel profilo `Bilanciato`, oppure budget adattivo ridotto in Discovery quando `max_topics` è alto).
2. **Pre-splitting in microchunk**: quando un chunk contiene più di **`8` messaggi sostanziali** (o supera la soglia prudenziale di **`1.500` token stimati**), il motore lo suddivide preventivamente in **microchunk** più piccoli prima di interrogare LM Studio, così da mantenere i tempi di risposta entro il timeout (`240s`) anche su CPU o GPU consumer e ridurre al minimo gli errori di formato JSON.
3. **Bisezione adattiva su errore**: se nonostante ciò un sotto-blocco fallisce per contesto eccessivo o risposta JSON troncata/invalida e contiene più di un messaggio, il sistema lo divide automaticamente a metà (fino a una profondità massima di `3` livelli) per isolare l'eventuale messaggio problematico senza perdere il resto del blocco.

### 13.2 Differenza tra nuova inferenza e recupero da checkpoint
Ogni volta che un chunk o microchunk supera la validazione formale del JSON e la verifica di esistenza degli `evidence_id` citati, il suo risultato viene salvato in modo **atomico** su disco in un database SQLite locale.
- **Nuova inferenza (`COMPUTED_THIS_RUN` / `SUCCESS`)**: LM Studio viene effettivamente interrogato via HTTP per calcolare i blocchi non ancora presenti nel checkpoint.
- **Recupero da checkpoint (`RECOVERED_FROM_CHECKPOINT` / `RESTORED_FROM_CHECKPOINT`)**: se un'analisi viene interrotta (chiusura browser, `Ctrl+C`, spegnimento del PC o timeout su alcuni blocchi finali) e poi rilanciata sullo stesso documento con gli stessi parametri, i blocchi già validati vengono riletti da SQLite in pochi millisecondi **senza richiamare LM Studio**, e il calcolo riprende solo dai blocchi mancanti o falliti.

### 13.3 Quando un checkpoint è riutilizzabile e cosa lo invalida
La compatibilità è governata da due hash SHA-256 calcolati in `analysis/checkpoint.py`:
- **`document_fingerprint`** (`compute_document_fingerprint`): dipende da `document_id`, ordine dei messaggi, timestamp, mittenti, `evidence_id` e testo esatto di tutte le evidenze. **Qualsiasi modifica al documento** (ad esempio l'esecuzione di `🎙️ Elabora media localmente` che aggiunge trascrizioni vocali o OCR) genera un nuovo `document_fingerprint` e invalida automaticamente l'uso dei vecchi checkpoint riferiti al documento non arricchito.
- **`run_config_hash`** (`compute_run_config_hash`): dipende da `model_id` (es. `qwen2.5-7b-instruct`), versione dei prompt, profilo operativo (`PRUDENT` / `BALANCED` / `PERFORMANCE`), limiti di chunking, `temperature`, `seed`, `max_topics` (in Discovery) oppure `label` + `description` del topic cercato (in Detection).
  - **Cosa NON invalida il checkpoint**: riavviare LM Studio o il computer ricaricando **lo stesso modello** (l'identificativo volatile di sessione `loaded_instance_id` di LM Studio è intenzionalmente escluso dall'hash di configurazione per consentire la ripresa dopo un riavvio).
  - **Cosa invalida il checkpoint**: cambiare modello LLM, cambiare profilo operativo (es. da `Bilanciato` a `Prudente`), cambiare `Numero max topic` in Discovery o modificare il testo/descrizione della query in Detection. In questi casi il sistema non mescola mai risultati eterogenei e avvia una nuova serie coerente di inferenze.

### 13.4 Come leggere avanzamento, recuperi, copertura e non confondere cache con nuova analisi
Nel pannello **`📊 Diagnostica analisi`** (e nei metadati del report JSON):
- **`Blocchi completati` / `Blocchi falliti`**: indicano quanti blocchi totali hanno prodotto un risultato valido e quanti hanno avuto un errore tecnico.
- **`Recuperati da checkpoint`**: indica quanti blocchi provengono dal database SQLite di una run precedente anziché da una chiamata LLM appena eseguita.
- **`Evidenze analizzate` / `Evidenze non analizzate`**: misurano la **copertura effettiva** della conversazione (`coverage_ratio`). Se `Evidenze non analizzate > 0`, una parte della chat non è stata coperta e l'esito è marcato come `PARTIAL`.
- **Come evitare di scambiare un recupero da checkpoint per una nuova inferenza**: se clicchi su `🚀 Esegui Topic Discovery (LIVE)` o `🚀 Esegui Topic Detection` e l'operazione termina in **meno di un secondo** mostrando tutti i blocchi sotto **`Recuperati da checkpoint`** (o nella card di verifica la dicitura di origine da checkpoint), significa che stai visualizzando il risultato già validato e salvato in precedenza per quella identica configurazione. Se desideri **forzare un ricalcolo integrale da zero** da parte del modello LLM:
  1. Apri l'expander **`⚙️ Profilo Operativo & Gestione Checkpoint`** in alto nella pagina `5. Analisi topic`;
  2. Clicca su **`🗑️ Cancella Checkpoint Documento`**;
  3. Rilancia l'analisi.

### 13.5 Percorsi reali dei checkpoint su disco
- **Percorso predefinito**: `<CARTELLA_PROGETTO>/.checkpoints/checkpoints.db` (su Windows `.\.checkpoints\checkpoints.db`, su Ubuntu `./.checkpoints/checkpoints.db`).
- **Percorso personalizzato**: se prima di avviare Streamlit imposti la variabile d'ambiente `CHATANALYSIS_CHECKPOINT_DIR`, il file `checkpoints.db` verrà creato e letto dentro `<CHATANALYSIS_CHECKPOINT_DIR>/checkpoints.db`.
- La cartella `.checkpoints/` è esclusa dal controllo versione in `.gitignore` perché contiene stato analitico locale riferito ai documenti esaminati.

---

## 14. Esempio pratico completo (tutorial end-to-end non sensibile)

Questo tutorial ti permette di collaudare dall'inizio alla fine tutte le funzioni principali usando **esclusivamente file sintetici non sensibili già inclusi nel repository** (`test_data/synthetic_smoke/synthetic_whatsapp_chat.txt` e `test_data/synthetic_smoke/synthetic_whatsapp_chat.zip`), senza toccare alcuna conversazione personale.

*(In alternativa, puoi creare in qualsiasi momento un tuo file di testo UTF-8 chiamato `prova_chat.txt` con righe nel formato standard di esportazione WhatsApp, ad esempio:*
```text
12/10/2026, 09:15 - Mario Rossi: Consegna dei bancali confermata per domani alle 08:30 al magazzino B.
12/10/2026, 09:17 - Giulia Bianchi: Perfetto, preparo le bolle di accompagnamento e avviso il trasportatore.
12/10/2026, 09:22 - Mario Rossi: Ricordati anche di verificare la fattura di acconto ricevuta ieri.
```
*e seguire gli stessi identici passaggi qui sotto).*

### Passaggi operativi dall'importazione all'export
1. **Avvia LM Studio e l'app**:
   - In **LM Studio** carica **`qwen2.5-7b-instruct`** (`Context Length = 8192`) e porta il Local Server su **Running** (`127.0.0.1:1234`).
   - Avvia Streamlit come visto nella Sezione 10 e apri **`http://127.0.0.1:8501`**.
2. **Importa la chat sintetica**:
   - Vai su **`2. Importazione`**.
   - In `Tipo di sorgente forense da importare` lascia selezionato **`WhatsApp export chat (TXT / ZIP)`**.
   - Clicca su *Browse files* e seleziona il file incluso nel progetto:
     - Su Windows: `test_data\synthetic_smoke\synthetic_whatsapp_chat.txt` (oppure `.zip`)
     - Su Ubuntu: `test_data/synthetic_smoke/synthetic_whatsapp_chat.txt` (oppure `.zip`)
   - Clicca su **`📥 Importa e analizza struttura`**: vedrai `4 record raw, 4 messaggi unificati` e l'impronta `SHA-256` del file.
3. **Attiva e ispeziona la conversazione**:
   - Nella sezione `💬 Selezione Conversazione` verifica che sia selezionata `Conversazione 1 — 4 messaggi — [...]` e clicca su **`📂 Apri conversazione`**.
   - Vai su **`3. Esplora conversazione`**: vedrai i 4 messaggi tra *Operatore Logistica* e *Ispettore Dogana* relativi a consegna container, sigilli doganali, inventario colli e autorizzazione allo sdoganamento. Apri `Dettagli Provenance Forense` sul secondo messaggio e annota il formato dell'`Evidence ID` (es. `ev:unified:whatsapp_export:...:msg_text`).
4. **Prova la ricerca deterministica**:
   - Vai su **`4. Ricerca`**, scrivi `sigilli doganali` con modalità **`PHRASE`** e clicca su **`Cerca Evidenze`**: otterrai esattamente `1` occorrenza con il relativo `Evidence ID`.
   - Prova ora `container sdoganamento` con modalità **`ANY_TERM`**: otterrai i messaggi che contengono almeno uno dei due vocaboli.
5. **Esegui la `Topic Discovery (LIVE)`**:
   - Vai su **`5. Analisi topic`** → scheda **`Topic Discovery (Argomenti Emersi)`**.
   - Imposta `Numero max topic:` a **`5`** e clicca su **`🚀 Esegui Topic Discovery (LIVE)`**.
   - Al termine vedrai il banner `COMPLETE` (`1/1` blocchi completati, `4` evidenze analizzate, `0` non analizzate) e i temi estratti dal modello in italiano (tipicamente legati a *consegna container / logistica portuale*, *verifica sigilli e ispezione doganale*, *inventario e sdoganamento merci*), ciascuno corredato dai propri `Evidence ID`.
6. **Esegui la «Prova del Nove» (`🔬 Verifica con Topic Detection`)**:
   - Su uno dei topic appena emersi dalla Discovery clicca sul pulsante **`🔬 Verifica con Topic Detection`**.
   - Attendi pochi secondi la seconda inferenza indipendente: sotto la scheda comparirà **`🔬 Risultato Verifica Indipendente`** (ad esempio `✅ COERENTE` / `CONSISTENT` con l'elenco degli `Evidence ID` condivisi in `overlap_evidence_ids`).
   - Clicca su **`⬇️ Esporta verifica JSON (SAFE_REDACTED)`** per scaricare il certificato JSON anonimizzato della verifica.
7. **Esegui due `Topic Detection` manuali (positiva e negativa)**:
   - Passa alla scheda **`Topic Detection (Verifica Mirata)`**.
   - Nel campo `Argomento:` scrivi **`Controlli doganali e sdoganamento merci`** e clicca su **`🚀 Esegui Topic Detection`** → l'esito atteso è **`PRESENT`** (`🟢 PRESENTE`) con citazione puntuale degli `Evidence ID` pertinenti.
   - Esegui una seconda prova scrivendo in `Argomento:` **`Prenotazione biglietti aerei per vacanza`** e clicca su **`🚀 Esegui Topic Detection`** → l'esito atteso è **`ABSENT`** (`⚪ ASSENTE`).
8. **Esporta il rapporto**:
   - Lascia attiva la spunta **`🔒 Modalità privacy avanzata`** e clicca su **`📥 Esporta rapporto completo (JSON)`** per salvare il report tecnico privo di testi in chiaro.
9. *(Facoltativo)* Torna su **`2. Importazione`** e clicca anche su **`🚀 Carica Dataset Dimostrativo`** per esplorare come appaiono nel programma anche le evidenze di tipo `AUDIO_TRANSCRIPTION` e `IMAGE_OCR_TEXT` già pre-costruite nel dataset `DEMO`.

---

## 15. Verifiche post-installazione

Usa questa checklist dopo aver completato l'installazione per accertarti che ogni sottosistema sia pronto prima di lavorare su dati reali.

### Parte A — Controlli completamente OFFLINE (non richiedono modelli LLM o vocali scaricati)

#### 1. Verifica versione Python e ambiente virtuale `.venv`
- **Windows (PowerShell)**:
  ```powershell
  .\.venv\Scripts\python.exe -c "import sys; print(sys.executable); print(sys.version)"
  ```
- **Ubuntu (bash)**:
  ```bash
  ./.venv/bin/python -c "import sys; print(sys.executable); print(sys.version)"
  ```
- **Risultato atteso**: il percorso stampato punta dentro `.venv` e la versione è `3.12.x`.

#### 2. Verifica importazione di tutti i pacchetti applicativi e librerie esterne
- **Windows (PowerShell)**:
  ```powershell
  .\.venv\Scripts\python.exe -c "import ai, analysis, core, entity_resolution, importer, multimodal, normalization, profiler, search, ui, unified, validation, streamlit, PIL, pytesseract, faster_whisper, ctranslate2, av, ijson, defusedxml; print('TUTTI I MODULI OK')"
  ```
- **Ubuntu (bash)**:
  ```bash
  ./.venv/bin/python -c "import ai, analysis, core, entity_resolution, importer, multimodal, normalization, profiler, search, ui, unified, validation, streamlit, PIL, pytesseract, faster_whisper, ctranslate2, av, ijson, defusedxml; print('TUTTI I MODULI OK')"
  ```
- **Risultato atteso**: `TUTTI I MODULI OK` senza eccezioni `ModuleNotFoundError` o `ImportError`.

#### 3. Verifica disponibilità di Tesseract OCR (binario + lingue `ita` ed `eng`) e modulo STT
- **Windows (PowerShell)**:
  ```powershell
  tesseract --version
  tesseract --list-langs
  .\.venv\Scripts\python.exe -c "from multimodal.ocr import TesseractImageTextExtractor; from multimodal.transcriber import FasterWhisperTranscriber; print('OCR:', TesseractImageTextExtractor.is_available(), '| STT:', FasterWhisperTranscriber.is_available())"
  ```
- **Ubuntu (bash)**:
  ```bash
  tesseract --version
  tesseract --list-langs
  ./.venv/bin/python -c "from multimodal.ocr import TesseractImageTextExtractor; from multimodal.transcriber import FasterWhisperTranscriber; print('OCR:', TesseractImageTextExtractor.is_available(), '| STT:', FasterWhisperTranscriber.is_available())"
  ```
- **Risultato atteso**: `tesseract --list-langs` elenca sia `eng` sia `ita`, e l'ultima riga stampa `OCR: True | STT: True`.

#### 4. Verifica permessi di scrittura sulle cartelle locali `.checkpoints/` e `output/`
- **Windows (PowerShell)**:
  ```powershell
  .\.venv\Scripts\python.exe -c "from pathlib import Path; [Path(d).mkdir(exist_ok=True) for d in ('.checkpoints','output')]; p=Path('.checkpoints/_perm_check.tmp'); p.write_text('ok', encoding='utf-8'); p.unlink(); print('PERMESSI CARTELLE OK')"
  ```
- **Ubuntu (bash)**:
  ```bash
  ./.venv/bin/python -c "from pathlib import Path; [Path(d).mkdir(exist_ok=True) for d in ('.checkpoints','output')]; p=Path('.checkpoints/_perm_check.tmp'); p.write_text('ok', encoding='utf-8'); p.unlink(); print('PERMESSI CARTELLE OK')"
  ```
- **Risultato atteso**: `PERMESSI CARTELLE OK`.

#### 5. Verifica mini-analisi offline (importazione chat sintetica + ricerca deterministica, senza LLM)
- **Windows (PowerShell)**:
  ```powershell
  .\.venv\Scripts\python.exe -c "from pathlib import Path; from ui.ingestion import ingest_file_payload; from ui.models import SourceFormat; from search.service import SearchService; from search.models import EvidenceSearchQuery, MatchMode; r=ingest_file_payload(Path('test_data/synthetic_smoke/synthetic_whatsapp_chat.txt').read_bytes(), 'synthetic_whatsapp_chat.txt', SourceFormat.WHATSAPP_EXPORT); doc=r.document_list[0]; hits=SearchService(doc).search_evidence(EvidenceSearchQuery(query_text='doganali', match_mode=MatchMode.ANY_TERM)); print('INGESTION+SEARCH OK | messaggi:', len(doc.bundles), '| hit:', hits.total_hits)"
  ```
- **Ubuntu (bash)**:
  ```bash
  ./.venv/bin/python -c "from pathlib import Path; from ui.ingestion import ingest_file_payload; from ui.models import SourceFormat; from search.service import SearchService; from search.models import EvidenceSearchQuery, MatchMode; r=ingest_file_payload(Path('test_data/synthetic_smoke/synthetic_whatsapp_chat.txt').read_bytes(), 'synthetic_whatsapp_chat.txt', SourceFormat.WHATSAPP_EXPORT); doc=r.document_list[0]; hits=SearchService(doc).search_evidence(EvidenceSearchQuery(query_text='doganali', match_mode=MatchMode.ANY_TERM)); print('INGESTION+SEARCH OK | messaggi:', len(doc.bundles), '| hit:', hits.total_hits)"
  ```
- **Risultato atteso**: `INGESTION+SEARCH OK | messaggi: 4 | hit: 1`.

#### 6. Verifica avvio interfaccia Streamlit
- Avvia `streamlit run app.py` (Sezione 10) e apri **`http://127.0.0.1:8501/healthz`** o **`http://127.0.0.1:8501/_stcore/health`** nel browser: deve rispondere **`ok`**, e su **`http://127.0.0.1:8501`** deve apparire la pagina `📊 Panoramica Documento`.

---

### Parte B — Controlli che RICHIEDONO i modelli locali scaricati (LM Studio + Faster-Whisper)

#### 7. Verifica connessione a LM Studio e compatibilità del modello GGUF caricato
*(Richiede LM Studio aperto con `qwen2.5-7b-instruct` caricato e Local Server in stato `Running` su `127.0.0.1:1234`)*:
- **Windows (PowerShell)**:
  ```powershell
  .\.venv\Scripts\python.exe -c "from ai.lmstudio import LmStudioClient; c=LmStudioClient(); print('Server attivo:', c.is_available(), '| Modelli:', c.list_models())"
  ```
- **Ubuntu (bash)**:
  ```bash
  ./.venv/bin/python -c "from ai.lmstudio import LmStudioClient; c=LmStudioClient(); print('Server attivo:', c.is_available(), '| Modelli:', c.list_models())"
  ```
- **Risultato atteso**: `Server attivo: True` seguito da una tupla non vuota contenente `'qwen2.5-7b-instruct'` (o l'equivalente ID GGUF esposto da LM Studio).

#### 8. Verifica presenza in cache del modello vocale Faster-Whisper `base`
*(Richiede di aver eseguito il download una tantum mostrato nella Sezione 8.2)*:
- **Windows (PowerShell)**:
  ```powershell
  .\.venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8', local_files_only=True); print('CACHE WHISPER BASE OFFLINE OK')"
  ```
- **Ubuntu (bash)**:
  ```bash
  ./.venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8', local_files_only=True); print('CACHE WHISPER BASE OFFLINE OK')"
  ```
- **Risultato atteso**: `CACHE WHISPER BASE OFFLINE OK` (conferma che la trascrizione audio potrà funzionare anche staccando completamente il cavo di rete / Wi-Fi).


---

## 16. Risoluzione problemi (Windows e Ubuntu)

Ogni casistica riporta **Sintomo**, **Cause plausibili**, **Verifiche** e **Rimedio sicuro** distinguendo Windows e Ubuntu.

### 16.1 Python non trovato o versione sbagliata (`< 3.12`)
- **Sintomo**: `python: termine non riconosciuto` / `command not found`, oppure `pip install` fallisce con `Package 'whatsapp-forensic-importer' requires a different Python: 3.10.x not in '>=3.12'`.
- **Cause plausibili**: Python non installato; casella `Add python.exe to PATH` non spuntata durante l'installazione Windows; terminale aperto prima dell'installazione; Ubuntu 22.04 LTS con Python 3.10 di sistema.
- **Verifiche**:
  - Windows (PowerShell): `py -0p` (elenca tutte le versioni di Python registrate sul PC) e `python --version`.
  - Ubuntu (bash): `python3 --version` e `which python3.12`.
- **Rimedio sicuro**:
  - **Windows**: installa **Python 3.12 (64-bit)** da https://www.python.org/downloads/windows/ spuntando **`Add python.exe to PATH`**, chiudi e riapri PowerShell e crea `.venv` con `py -3.12 -m venv .venv`.
  - **Ubuntu**: usa **Ubuntu 24.04 LTS** (`sudo apt install -y python3 python3-venv`, che fornisce Python 3.12); su Ubuntu 22.04 LTS installa un interprete `python3.12` + `python3.12-venv` e crea l'ambiente esplicitamente con `python3.12 -m venv .venv`.

### 16.2 L'ambiente virtuale `.venv` non si attiva o non si crea
- **Sintomo**: su Windows errore rosso `Impossibile caricare il file ... Activate.ps1 perché l'esecuzione di script è disabilitata nel sistema`; su Ubuntu `The virtual environment was not created successfully because ensurepip is not available`.
- **Cause plausibili**: Execution Policy predefinita `Restricted` di PowerShell; pacchetto `python3-venv` assente su Ubuntu.
- **Verifiche**:
  - Windows: `Test-Path .\.venv\Scripts\python.exe`
  - Ubuntu: `dpkg -l | grep python3.*-venv`
- **Rimedio sicuro**:
  - **Windows**: sblocca solo la finestra corrente con `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` seguito da `.\.venv\Scripts\Activate.ps1`, oppure usa direttamente `.\.venv\Scripts\python.exe` senza attivazione.
  - **Ubuntu**: esegui `sudo apt update && sudo apt install -y python3-venv`, ricrea l'ambiente con `python3 -m venv .venv` e attivalo con `source .venv/bin/activate`.

### 16.3 L'installazione dei pacchetti `pip` fallisce
- **Sintomo**: errori di rete (`ConnectionError` / SSL), `pip` obsoleto che tenta di compilare da sorgente, o errore di sintassi sulle parentesi quadre `[ui,ocr,stt,image]`.
- **Cause plausibili**: connessione Internet assente durante il setup; `pip` troppo vecchio per scaricare wheel moderne `manylinux`/`win_amd64`; dimenticanza delle virgolette attorno a `".[ui,ocr,stt,image]"`.
- **Verifiche**:
  - Windows: `.\.venv\Scripts\python.exe -m pip --version`
  - Ubuntu: `./.venv/bin/python -m pip --version`
- **Rimedio sicuro**: aggiorna sempre prima `pip` (`python -m pip install --upgrade pip`) e racchiudi sempre la specifica tra doppi apici: `python -m pip install -e ".[ui,ocr,stt,image]"`. Verifica di trovarti nella cartella che contiene `pyproject.toml`.

### 16.4 Incompatibilità di `PyAV` (`av`) o `CTranslate2`
- **Sintomo**: durante `pip install` viene tentata una compilazione C/C++ fallita per `av` o `ctranslate2`, oppure all'avvio della trascrizione audio il processo termina con errore di istruzione CPU illegale (`Illegal instruction`) o `ImportError`.
- **Cause plausibili**: uso di una versione di Python troppo recente e non ancora coperta da wheel binarie precompilate (es. Python 3.14/3.15 alpha/release appena uscite) o sistema a 32-bit; oppure CPU molto datata priva delle istruzioni vettoriali **AVX2** richieste da `ctranslate2`.
- **Verifiche**:
  - Windows: `.\.venv\Scripts\python.exe -c "import platform; print(platform.python_version(), platform.architecture())"`
  - Ubuntu: `lscpu | grep -i avx2` e `./.venv/bin/python -c "import platform; print(platform.python_version(), platform.machine())"`
- **Rimedio sicuro**: ricrea `.venv` usando **Python 3.12 a 64-bit (`x86_64` / `AMD64`)**, per il quale esistono wheel binarie ufficiali pronte sia su Windows sia su Linux. Se la CPU non supporta AVX2, puoi comunque installare e usare tutto il resto dell'applicazione senza STT con `pip install -e ".[ui,ocr,image]"`.

### 16.5 `Tesseract` non trovato (`Non installato` nella UI)
- **Sintomo**: nella scheda `1. Panoramica` la metrica `OCR (Immagini)` indica **`Non installato`** (`tesseract non rilevato nel PATH di sistema`), anche dopo aver installato `pytesseract` con `pip`.
- **Cause plausibili**: `pytesseract` è solo il ponte Python; manca il programma nativo Tesseract OCR, oppure su Windows l'installer UB Mannheim lo ha installato in `C:\Program Files\Tesseract-OCR` senza aggiungerlo al `PATH`, oppure il terminale da cui hai lanciato Streamlit era già aperto prima della modifica del `PATH`.
- **Verifiche**:
  - Windows (PowerShell): `Get-Command tesseract -ErrorAction SilentlyContinue` e `Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe"`
  - Ubuntu (bash): `which tesseract`
- **Rimedio sicuro**:
  - **Windows**: se `C:\Program Files\Tesseract-OCR\tesseract.exe` esiste, esegui i comandi del Passo 3 della Sezione 8.1 per aggiungerlo al `PATH` utente, **ferma Streamlit (`Ctrl+C`), chiudi PowerShell, apri una nuova finestra PowerShell** e riavvia Streamlit.
  - **Ubuntu**: installa il binario con `sudo apt update && sudo apt install -y tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng`.

### 16.6 Lingue OCR mancanti (`ita` assente in `tessdata`)
- **Sintomo**: `tesseract --version` funziona, ma l'elaborazione OCR delle foto fallisce o non riconosce correttamente l'italiano perché manca `ita.traineddata`.
- **Cause plausibili**: durante l'installazione Windows non è stata selezionata la lingua `Italian` sotto *Additional language data*, oppure su Ubuntu non è stato installato il pacchetto `tesseract-ocr-ita`.
- **Verifiche**:
  - Windows / Ubuntu: `tesseract --list-langs` (controlla che compaiano sia `eng` sia `ita`).
- **Rimedio sicuro**:
  - **Windows**: rilancia l'installer UB Mannheim di Tesseract e spunta **`Italian`** in *Additional language data (download)*.
  - **Ubuntu**: esegui `sudo apt install -y tesseract-ocr-ita tesseract-ocr-eng`.

### 16.7 File troppo grande o rifiutato dai controlli di sicurezza ZIP
- **Sintomo**: il caricamento si blocca oltre i 2 GB, oppure compare `InvalidUploadedFileError: Archivio ZIP non conforme ai requisiti di sicurezza`.
- **Cause plausibili**: file superiore al tetto massimo di **2048 MB (2 GB)** (`MAX_UPLOAD_SIZE_MB` in `core/config.py` e `.streamlit/config.toml`), oppure archivio `.zip` che supera i limiti anti-Zip-Bomb (`4096 MB` decompressi totali, `50.000` file interni, singolo membro `> 2048 MB` o rapporto di compressione sospetto `> 100:1`).
- **Verifiche**: controlla la dimensione reale del file su disco e il numero/dimensione dei file contenuti nello `.zip`.
- **Rimedio sicuro**: non modificare mai il file sorgente originale; se devi esaminare un'esportazione WhatsApp il cui `.zip` supera i 2 GB solo a causa di lunghi file video (i video non vengono analizzati da OCR/STT), puoi esportare da WhatsApp la variante *Senza media* (`.txt`) per l'analisi testuale dei messaggi, mantenendo separata la copia forense integrale.

### 16.8 LM Studio non raggiungibile, modello errato/non caricato, HTTP 500 o JSON invalido
- **Sintomo**: all'avvio di Topic Discovery o Topic Detection compare un errore di connessione a `127.0.0.1:1234`, oppure `AiModelNotInstalledError` / `AiModelMismatchError`, un errore `HTTP 500` dal backend o `AiStructuredOutputError` (risposta JSON invalida/troncata).
- **Cause plausibili**:
  1. Il **Local Server** di LM Studio è in stato *Stopped* o su una porta diversa da `1234`;
  2. Il modello selezionato non appartiene alle specifiche supportate (es. hai scaricato `deepseek-r1-distill-llama-8b` invece di `deepseek-r1-distill-qwen-7b`, oppure un modello non `instruct`);
  3. Il modello è stato caricato in LM Studio con **Context Length troppo basso** (es. `2048` o `4096` invece di `8192`), causando `HTTP 400/500` quando arriva un blocco lungo;
  4. Il modello ha generato testo fuori schema JSON su un passaggio complesso.
- **Verifiche**:
  - Windows: `Invoke-RestMethod -Uri "http://127.0.0.1:1234/api/v0/models" | Select-Object -ExpandProperty data`
  - Ubuntu: `curl -s http://127.0.0.1:1234/api/v0/models | python3 -m json.tool`
  - Apri nella UI l'expander `🔍 Diagnostica Tecnica Sicura (Senza Dati Sensibili)` per leggere `Categoria Errore` e `Codice HTTP`.
- **Rimedio sicuro**:
  1. In LM Studio → scheda **Developer**, verifica che il Local Server sia **Running** sulla porta **`1234`**;
  2. Scarica/ricarica **`qwen2.5-7b-instruct`** (`Q4_K_M`) impostando esplicitamente **Context Length = `8192`**;
  3. Se singoli sotto-blocchi hanno restituito JSON invalido, nella scheda Discovery usa il pulsante **`🔄 Riprova solo i blocchi falliti`** oppure passa temporaneamente al profilo **`Prudente (2500 token - Massima sicurezza)`**.

### 16.9 Memoria RAM o VRAM insufficiente
- **Sintomo**: LM Studio rifiuta di caricare il modello (`AiModelLoadError`), il sistema diventa estremamente lento (disco fisso al 100% di utilizzo) o il driver grafico va in errore.
- **Cause plausibili**: VRAM della scheda video inferiore ai ~5,5 GB richiesti per l'offload totale, oppure RAM di sistema libera inferiore a ~7–8 GB con molte altre applicazioni/schede browser aperte.
- **Verifiche**:
  - Windows: apri *Gestione attività* (`Ctrl+Maiusc+Esc`) → scheda *Prestazioni* → *Memoria* e *GPU*.
  - Ubuntu: esegui `free -h` (e `nvidia-smi` se hai GPU NVIDIA).
- **Rimedio sicuro**: chiudi i programmi pesanti non necessari; in LM Studio riduci parzialmente lo slider **GPU Offload** (spostando una parte dei layer sulla RAM di sistema) mantenendo la quantizzazione `Q4_K_M` e Context Length `8192`, e seleziona nella UI di ChatAnalysis il profilo **`Prudente (2500 token - Massima sicurezza)`**.

### 16.10 Timeout durante l'analisi (`240s`) o attivazione del Circuit Breaker
- **Sintomo**: un sotto-blocco fallisce per `AiBackendTimeoutError` (oltre `240` secondi per singola chiamata) oppure la run si interrompe dopo `3` timeout consecutivi / `10` totali.
- **Cause plausibili**: inferenza eseguita interamente su CPU lenta o in condizioni di swapping RAM; utilizzo di un modello *reasoning* verboso (come `deepseek-r1-distill-qwen-7b`) su macchina priva di GPU dedicata.
- **Verifiche**: controlla in LM Studio i token/secondo generati nel log della scheda *Developer*.
- **Rimedio sicuro**: usa **`qwen2.5-7b-instruct`** (`Q4_K_M`), assicurati che non vi sia swap su disco e rilancia la stessa analisi: grazie al salvataggio incrementale in `.checkpoints/checkpoints.db`, tutti i blocchi già completati prima del timeout verranno recuperati istantaneamente e verranno ricalcolati solo i blocchi mancanti (oppure premi `🔄 Riprova solo i blocchi falliti`).

### 16.11 Checkpoint precedenti non riutilizzati o blocco `SNAPSHOT_MISMATCH`
- **Sintomo**: rilanciando un'analisi il contatore `Recuperati da checkpoint` resta a `0`, oppure cliccando `🔬 Verifica con Topic Detection` il pulsante risulta disabilitato con l'avviso che il contenuto del documento è cambiato rispetto alla Discovery (`SNAPSHOT_MISMATCH`).
- **Cause plausibili**:
  1. Hai cambiato modello, profilo operativo (`Bilanciato` ↔ `Prudente`), `Numero max topic` o testo della query (questo modifica legittimamente `run_config_hash` per impedire risultati incoerenti);
  2. Hai eseguito **`🎙️ Elabora media localmente`** (OCR/STT) *dopo* aver fatto la Discovery: il documento ora possiede nuove evidenze derivate e quindi un nuovo `document_fingerprint`.
- **Verifiche**: controlla se hai cambiato parametri in `5. Analisi topic` o se hai arricchito i media dopo la prima run.
- **Rimedio sicuro**: se hai arricchito il documento con OCR/STT, riesegui `🚀 Esegui Topic Discovery (LIVE)` sul documento completo prima di avviare `🔬 Verifica con Topic Detection`. Se invece vuoi ripulire i vecchi checkpoint di quel documento, apri `⚙️ Profilo Operativo & Gestione Checkpoint` e clicca su **`🗑️ Cancella Checkpoint Documento`**.

### 16.12 Importazione WhatsApp o Cellebrite problematica
- **Sintomo**: errore in fase di importazione (`InvalidUploadedFileError`, `UnsupportedSourceFormatError`, `PipelineStageError`) oppure messaggio `ℹ️ wa.db contiene dati contatto/identità e non costituisce da solo una conversazione analizzabile`.
- **Cause plausibili**:
  1. Hai selezionato nel menu a tendina un `Tipo di sorgente forense` diverso dal formato reale del file (es. hai lasciato `WhatsApp export chat (TXT / ZIP)` caricando un `.db` SQLite o un `.xml` Cellebrite);
  2. Hai caricato un backup WhatsApp **cifrato** (`msgstore.db.crypt12`, `.crypt14`, `.crypt15`): la decifrazione non è supportata;
  3. Hai caricato un archivio `.zip` che non contiene alcun file di trascrizione `.txt` di WhatsApp;
  4. Hai caricato `wa.db` come sorgente primaria aspettandoti messaggi (`wa.db` contiene solo contatti: va caricato come **companion opzionale** insieme a `WhatsApp msgstore (SQLite)`).
- **Verifiche**: controlla l'estensione e la natura del file; per un database SQLite verifica che non abbia estensione `.crypt*`.
- **Rimedio sicuro**: seleziona nel menu `Tipo di sorgente forense da importare` la voce esatta corrispondente al file; per unire messaggi e contatti Android usa `WhatsApp msgstore (SQLite)` caricando `msgstore.db` nel primo riquadro e `wa.db` nel secondo riquadro (`wa.db contatti — opzionale`).

### 16.13 Differenza fondamentale tra errore tecnico e risultato `UNCERTAIN` (o `DISCREPANCY_ABSENT`)
Non confondere mai un malfunzionamento informatico con un esito epistemico prudenziale:
- **Errore tecnico di esecuzione** (`FAILED`, `LmStudioUnavailableError`, `AiBackendTimeoutError`, `HTTP 500`): significa che il blocco **non è stato analizzato validamente** (server spento, timeout, JSON corrotto). In questo caso `Blocchi falliti > 0` ed `Evidenze non analizzate > 0`: va risolto il problema tecnico e vanno riprovati i blocchi falliti.
- **Risultato analitico `UNCERTAIN` (`🟡 INCERTO`) o `DISCREPANCY_ABSENT` (`⚠️ DISCREPANZA (ABSENT)`)**: il programma, il server LM Studio e la validazione JSON **hanno funzionato regolarmente** (`0` errori tecnici), ma il contenuto testuale della conversazione è ambiguo/parziale (`UNCERTAIN`) oppure le due inferenze indipendenti di Discovery e Detection hanno valutato diversamente la pertinenza del tema astratto (`DISCREPANCY_ABSENT`). Non c'è nulla di «rotto» da riparare nel software: è il segnale progettato per guidare l'analista umano a leggere direttamente gli `Evidence ID` coinvolti.

---

### 16.14 «Elaborazione multimediale precedente non riuscita (motori non disponibili o errore)»
- **Sintomo**: in `1. Panoramica` o `3. Esplora conversazione`, nella sezione `🎬 Elaborazione Multimediale Locale (On-Demand)`, compare uno di questi messaggi:
  - `❌ Elaborazione multimediale precedente non riuscita (motori non disponibili o errore). È possibile riprovare.`
  - dopo il clic: `Elaborazione non riuscita: nessun media estratto con successo (motori non disponibili).`
  - dopo il clic: `Errore elaborazione media: <dettaglio>` (in questo caso il dettaglio indica la causa).
  - `⚠️ Elaborazione multimediale parziale…`: alcuni allegati sono riusciti e altri no (non è un errore dei motori: vedi il punto 4 delle cause).
- **Che cosa significa**: lo stato è `FAILED`, cioè nessun allegato è stato elaborato con successo. Il pulsante `🔁 Riprova elaborazione media` ripete l'operazione dall'inizio, ma se il motivo non è stato rimosso fallirà di nuovo.
- **Cause plausibili, dalla più frequente**:
  1. **Tesseract non trovato** nel `PATH` (OCR) o senza le lingue `ita` ed `eng`. Nella metrica `OCR (Immagini)` compare `Non installato`.
  2. **`pytesseract` non installato**, oppure **Faster-Whisper / CTranslate2 non importabili** (STT). Nella metrica `Speech-to-Text (Audio)` compare `Non installato`.
  3. **Streamlit avviato prima di una modifica al `PATH`**: il terminale già aperto non vede il nuovo percorso di Tesseract.
  4. **La chat è stata importata senza i file multimediali** (ad esempio solo il `.txt`): i messaggi citano allegati, ma i file non sono disponibili. Reimporta l'archivio `.zip` esportato da WhatsApp **con media**. Questo vale anche per il **dataset dimostrativo**, i cui allegati sono solo simulati: l'elaborazione multimediale non è significativa su quel dataset.
  5. **Primo uso di Faster-Whisper senza connessione**: il modello `base` viene scaricato al primo utilizzo (Sezione 8.2).
- **Diagnosi rapida (devono stampare `True` entrambe le righe)**:
  - Windows (PowerShell):
    ```powershell
    .\.venv\Scripts\python.exe -c "from multimodal.ocr import TesseractImageTextExtractor; from multimodal.transcriber import FasterWhisperTranscriber; print('OCR disponibile:', TesseractImageTextExtractor.is_available()); print('STT disponibile:', FasterWhisperTranscriber.is_available())"
    ```
  - Ubuntu (bash):
    ```bash
    source .venv/bin/activate
    python -c "from multimodal.ocr import TesseractImageTextExtractor; from multimodal.transcriber import FasterWhisperTranscriber; print('OCR disponibile:', TesseractImageTextExtractor.is_available()); print('STT disponibile:', FasterWhisperTranscriber.is_available())"
    ```
- **Rimedio per l'OCR (Tesseract)**:
  - Windows (PowerShell):
    ```powershell
    Get-Command tesseract -ErrorAction SilentlyContinue
    Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe"
    # Se il file esiste ma il comando non viene trovato, aggiungi il percorso SOLO a questa finestra:
    $env:PATH = "C:\Program Files\Tesseract-OCR;" + $env:PATH
    tesseract --list-langs
    .\.venv\Scripts\python.exe -c "import pytesseract; print(pytesseract.get_tesseract_version())"
    ```
    Poi avvia Streamlit **da questa stessa finestra** (Sezione 10.1). Per rendere la modifica permanente usa i comandi del Passo 3 della Sezione 8.1 e riapri PowerShell. Se `tesseract --list-langs` non mostra `ita`, rilancia l'installer UB Mannheim e spunta `Italian`.
  - Ubuntu (bash):
    ```bash
    sudo apt update && sudo apt install -y tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng
    tesseract --list-langs
    source .venv/bin/activate
    python -c "import pytesseract; print(pytesseract.get_tesseract_version())"
    ```
- **Rimedio per l'audio (Faster-Whisper)**:
  - Windows (PowerShell):
    ```powershell
    .\.venv\Scripts\python.exe -m pip install -e ".[ui,ocr,stt,image]"
    .\.venv\Scripts\python.exe -c "import ctranslate2, faster_whisper; print('faster-whisper pronto')"
    .\.venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8'); print('Modello STT base pronto in cache locale')"
    ```
  - Ubuntu (bash):
    ```bash
    source .venv/bin/activate
    pip install -e ".[ui,ocr,stt,image]"
    python -c "import ctranslate2, faster_whisper; print('faster-whisper pronto')"
    python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8'); print('Modello STT base pronto in cache locale')"
    lscpu | grep -i avx2
    ```
    L'ultimo comando deve mostrare `avx2`: senza di esso CTranslate2 non funziona (vedi Sezione 16.4).
- **Dopo aver sistemato i motori**:
  1. Se hai fermato Streamlit, riavvialo e **reimporta la chat**: il documento in sessione non viene conservato, mentre i checkpoint SQLite già salvati restano su disco.
  2. Apri `3. Esplora conversazione` e premi **`🔁 Riprova elaborazione media`** (o `🔁 Riprova elaborazione media parziale`).
  3. Esegui sempre l'OCR e la trascrizione **prima** di `Topic Discovery` (Sezione 11, Passo 3): modificano l'impronta del documento e invalidano i checkpoint creati prima.
- **Se dopo la diagnosi entrambi i motori risultano `True` ma l'errore persiste**: verifica di aver importato l'archivio `.zip` con i media (causa 4) e leggi il messaggio `Errore elaborazione media: …` mostrato dopo il clic, che contiene la causa tecnica.

## 17. Privacy e sicurezza operativa

Quando utilizzi l'applicazione su conversazioni reali (che possono contenere dati personali, sensibili, sanitari o giudiziari di terzi), tieni presenti sia le protezioni implementate nel codice sia le responsabilità dell'ambiente operativo:

1. **Esecuzione locale (`127.0.0.1`) vs Cloud**:
   - L'applicazione web Streamlit (`.streamlit/config.toml`) e il client LM Studio (`ai/lmstudio.py`) comunicano **esclusivamente su indirizzi di loopback locale** (`127.0.0.1`, `localhost`, `[::1]`): `LmStudioClient` rifiuta via codice qualsiasi URL non locale.
   - Il modulo `ai/cloud_experiment.py` presente nel repository è un runner sperimentale da riga di comando disabilitato di default, vincolato al solo dataset sintetico congelato `test_data/cloud_e2e/` e **mai importato né invocato dall'applicazione Streamlit**.
2. **Perché «locale» NON significa «sicurezza assoluta»**:
   - Il fatto che i dati non escano in rete tramite ChatAnalysis non protegge il computer se il sistema operativo è condiviso con utenti non autorizzati, infetto da malware, sincronizzato automaticamente con servizi cloud consumer (es. cartelle Desktop/Documenti sincronizzate automaticamente su OneDrive, Google Drive, Dropbox o iCloud) o privo di cifratura del disco (BitLocker su Windows / LUKS su Ubuntu). Evita di collocare la cartella di lavoro o i file delle chat dentro directory sincronizzate automaticamente in cloud.
3. **Staging temporaneo, checkpoint ed export su disco**:
   - Durante l'importazione (`ui/ingestion.py`), il file caricato viene scritto in una directory temporanea isolata (`tempfile.TemporaryDirectory`) distrutta automaticamente al termine dell'ingestion; per gli archivi con allegati multimediali l'accesso avviene in sola lettura tramite `MediaAssetRegistry`.
   - **Attenzione ai checkpoint (`.checkpoints/checkpoints.db`)**: per permettere la ripresa dopo un'interruzione, il database SQLite `.checkpoints/checkpoints.db` memorizza in locale sull'hard disk i risultati intermedi validati (inclusi titoli, descrizioni, motivazioni ed `evidence_id` estratti). Il file SQLite **non è cifrato a riposo** dall'applicazione: proteggi la cartella con i permessi del tuo utente di sistema operativo e su disco cifrato.
   - **Esportazioni (`SAFE_REDACTED` vs `LOCAL_UNREDACTED_CAUTION`)**: mantieni sempre attiva la casella **`🔒 Modalità privacy avanzata`** quando devi condividere un report per assistenza tecnica o verifiche prestazionali; disattivala solo quando devi archiviare un elaborato analitico in un fascicolo locale autorizzato.
4. **Integrità e tracciabilità (SHA-256) e limiti dell'automazione**:
   - Ogni file importato viene sottoposto a calcolo **SHA-256** in streaming (visibile in `1. Panoramica` e `2. Importazione`) senza modificare neppure un byte del file originale.
   - L'analisi AI è soggetta ai limiti intrinseci dei modelli linguistici (possibili omissioni di linguaggio allusivo/ironico o interpretazioni imprecise): verifica sempre ogni affermazione risalendo tramite `Evidence ID` al messaggio originale.

---

## 18. Manutenzione, backup e ripristino dalla cartella `Trash`

### 18.1 Aggiornamento controllato delle dipendenze
In ambito forense o di tesi è consigliabile **non aggiornare alla cieca** i pacchetti durante un'attività in corso per non alterare il comportamento del runtime. Se in futuro desideri salvare una fotografia esatta delle versioni installate prima di un aggiornamento:
- **Windows (PowerShell)**:
  ```powershell
  .\.venv\Scripts\python.exe -m pip freeze > pip_freeze_backup.txt
  ```
- **Ubuntu (bash)**:
  ```bash
  ./.venv/bin/python -m pip freeze > pip_freeze_backup.txt
  ```

### 18.2 Backup dei dati e dei checkpoint SQLite
Prima di spostare il progetto su un altro PC o fare manutenzione:
1. Arresta Streamlit (`Ctrl+C`) in modo che nessuna transazione SQLite sia aperta.
2. Copia in un archivio protetto:
   - i tuoi file sorgente originali (conservati separatamente);
   - la cartella **`.checkpoints/`** (contiene `checkpoints.db` con tutte le analisi lunghe già calcolate);
   - eventuali report JSON/CSV che hai scaricato dal browser o generato in **`output/`**.

### 18.3 Cosa NON cancellare mai dalla cartella operativa
Non eliminare né spostare:
- `.git/` (cronologia e integrità del repository Git);
- `app.py`, `pyproject.toml`, `MANIFEST.in`, `.streamlit/config.toml`, `scripts/run_streamlit_windows.ps1`;
- le 12 cartelle dei pacchetti Python (`ai/`, `analysis/`, `core/`, `entity_resolution/`, `importer/`, `multimodal/`, `normalization/`, `profiler/`, `search/`, `ui/`, `unified/`, `validation/`);
- `test_data/cloud_e2e/` e `test_data/synthetic_smoke/`;
- `.checkpoints/` mentre desideri conservare la possibilità di riprendere analisi già svolte.

### 18.4 Ripristino non distruttivo dalla cartella `Trash/repository_cleanup_2026-11-07/` e suite di test per sviluppatori
Durante la pulizia del repository nessun file è stato cancellato: tutti i file non richiesti dall'installazione e dall'uso operativo (inclusi i vecchi runbook di sviluppo, gli script ausiliari e **la suite di test automatici `tests/` con le relative fixture di collaudo `test_data/`**, che **non fanno parte della distribuzione operativa minima**) sono conservati intatti in:
- `Trash/repository_cleanup_2026-11-07/`

All'interno di quella sottocartella trovi:
- `manifest_cleanup.tsv` e `manifest_cleanup.json`: elenco completo di ogni file spostato con percorso originario, percorso in `Trash`, motivazione, dimensione e **hash SHA-256 verificato prima e dopo lo spostamento**;
- `RESTORE_INSTRUCTIONS.md` e lo script `restore_from_trash.py`, che ripristina file singoli o intere cartelle verificando l'hash SHA-256 e **rifiutando qualsiasi sovrascrittura accidentale**.

**Esempi pratici di ripristino (dalla root del progetto)**:
- **Vedere in anteprima (`--dry-run`) cosa verrebbe ripristinato senza toccare alcun file**:
  - Windows (PowerShell):
    ```powershell
    .\.venv\Scripts\python.exe Trash\repository_cleanup_2026-11-07\restore_from_trash.py --prefix tests/ --dry-run
    ```
  - Ubuntu (bash):
    ```bash
    ./.venv/bin/python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --prefix tests/ --dry-run
    ```
- **Ripristinare un singolo file specifico** (sostituendo `<PERCORSO_RELATIVO>` con un valore della colonna `source_rel_path` di `manifest_cleanup.tsv`):
  - Windows (PowerShell):
    ```powershell
    .\.venv\Scripts\python.exe Trash\repository_cleanup_2026-11-07\restore_from_trash.py --path "<PERCORSO_RELATIVO>"
    ```
  - Ubuntu (bash):
    ```bash
    ./.venv/bin/python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --path "<PERCORSO_RELATIVO>"
    ```
- **Per sviluppatori — ripristinare la suite di test Pytest (`tests/` e fixture `test_data/`) ed eseguirla**:
  - Windows (PowerShell):
    ```powershell
    .\.venv\Scripts\python.exe Trash\repository_cleanup_2026-11-07\restore_from_trash.py --prefix tests/
    .\.venv\Scripts\python.exe Trash\repository_cleanup_2026-11-07\restore_from_trash.py --prefix test_data/
    .\.venv\Scripts\python.exe Trash\repository_cleanup_2026-11-07\restore_from_trash.py --prefix scripts/
    .\.venv\Scripts\python.exe Trash\repository_cleanup_2026-11-07\restore_from_trash.py --prefix docs/
    .\.venv\Scripts\python.exe -m pip install -e ".[dev,ui,ocr,stt,image]"
    .\.venv\Scripts\python.exe -m pytest -q
    ```
  - Ubuntu (bash):
    ```bash
    ./.venv/bin/python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --prefix tests/
    ./.venv/bin/python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --prefix test_data/
    ./.venv/bin/python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --prefix scripts/
    ./.venv/bin/python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --prefix docs/
    ./.venv/bin/python -m pip install -e ".[dev,ui,ocr,stt,image]"
    ./.venv/bin/python -m pytest -q
    ```

> **Perché servono anche `scripts/` e `docs/`**: molti test importano moduli da `scripts/` e leggono file di documentazione o di benchmark che dopo la pulizia si trovano solo in `Trash/`. Ripristinando soltanto `tests/` e `test_data/`, questi test falliscono con `ModuleNotFoundError` o `FileNotFoundError`. Servono inoltre alcuni file nella radice di `Trash/repository_cleanup_2026-11-07/` (i runbook `*.md` e `RUNBOOK_UBUNTU.txt`, i `benchmark_*.json` e le misure di scalabilità `*_scalability_measurements.json`, `large_file_benchmark_results.json`, `benchmark_frozen_sha256.txt`, `run_benchmark.sh`, `requirements_audit.txt`, `compute_manifest.py`): ripristinali uno per uno con `--path <nome file>` oppure ripristina tutto con `restore_from_trash.py --all` (sempre in modo non distruttivo e senza sovrascrivere i file esistenti).
>
> **Test che dipendono dall'ambiente** (non indicano un difetto dell'applicazione): quelli basati su `streamlit.testing` richiedono l'extra `ui`; quelli su OCR e trascrizione richiedono Tesseract e Faster-Whisper (extra `ocr` e `stt`, più il binario nativo, vedi Sezione 8); il test sull'impronta SHA-256 congelata di `run_benchmark.sh` può fallire per le differenze di fine riga (CRLF su Windows, LF su Linux); quelli sui database WhatsApp (`msgstore.db`, `wa.db`) richiedono fixture che vanno rigenerate con `scripts/sanitize_synthetic_dataset.py` se mancano. Se `pytest` segnala errori di raccolta su un file, controlla prima che le dipendenze della riga di installazione siano presenti.

### 18.5 Come segnalare un problema in sicurezza
Se devi segnalare un errore tecnico a uno sviluppatore o relatore:
1. Indica sistema operativo, versione di Python (`python --version`), modello GGUF e Context Length usati in LM Studio;
2. Copia solo i dati dell'expander **`🔍 Diagnostica Tecnica Sicura (Senza Dati Sensibili)`** oppure allega esclusivamente file JSON esportati con **`🔒 Modalità privacy avanzata` attiva (`SAFE_REDACTED`)**;
3. **Non allegare mai** gli archivi delle chat reali, il file `.checkpoints/checkpoints.db` né esportazioni `LOCAL_UNREDACTED_CAUTION`.

---

## 19. Limiti noti e test effettivamente eseguiti

Per il massimo rigore metodologico, la tabella seguente distingue ciò che è stato **realmente eseguito e verificato sul repository ripulito** durante la sessione del `2026-11-07` (**Windows 11 64-bit, Python `3.12.10`**) da ciò che costituisce **procedura prevista ma non eseguita direttamente in questo ambiente**.

### 19.1 Verifiche realmente eseguite nello stato finale post-pulizia (Windows + Python 3.12.10)

| Ambito verificato | Modalità di verifica eseguita | Esito effettivo |
| :--- | :--- | :--- |
| **Baseline pre-pulizia (suite Pytest completa)** | `2.614` test eseguiti prima dello spostamento | **`2.609` passati**, `3` saltati, `2` falliti solo per fine-riga CRLF Windows su `run_benchmark.sh` (zero difetti funzionali dell'app). |
| **Installabilità / Build pacchetto da struttura pulita** | Build wheel isolata (`pip wheel --no-deps --no-build-isolation`) sui soli file operativi rimasti fuori da `Trash` + import di tutti i 12 pacchetti | **Verificato e superato** (`whatsapp_forensic_importer-0.1.0-py3-none-any.whl`). |
| **Tutti i 6 formati di importazione (`importer/`)** | Ingestion reale end-to-end su `WhatsApp export (TXT / ZIP)` individuale e di gruppo, `msgstore.db` + companion `wa.db`, `wa.db` standalone (`AUXILIARY_ONLY`), `Cellebrite CSV`, `Cellebrite JSON`, `Cellebrite XML` | **Verificato e superato** (6/6 importer operativi). |
| **Modalità `DEMO`, consultazione e ricerca deterministica** | Generazione dataset sintetico `DEMO` (`12` sezioni) + ricerca in tutte le 4 modalità (`PHRASE`, `ALL_TERMS`, `ANY_TERM`, `EXACT`) | **Verificato e superato**. |
| **Estrazione OCR reale da immagine in archivio ZIP** | Esecuzione reale del binario nativo **Tesseract OCR v5.5.3** (`ita+eng`) su allegato `.png` dentro archivio `.zip` WhatsApp (`IMAGE_OCR_TEXT`) | **Verificato LIVE su Windows**. |
| **Trascrizione audio STT (Faster-Whisper + PyAV + CTranslate2)** | Verifica `FasterWhisperTranscriber.is_available() == True` ed esecuzione reale end-to-end del motore Faster-Whisper + PyAV su allegato `.wav` dentro `.zip` WhatsApp usando il modello locale già in cache (`Systran/faster-whisper-tiny`) | **Verificato su Windows** *(vedi nota sotto sul modello `base`)*. |
| **Topic Discovery, Topic Detection e Verifica di consistenza («Prova del Nove») — LIVE con LM Studio** | Esecuzione **LIVE reale** contro **LM Studio (`http://127.0.0.1:1234`)** con modello **`qwen2.5-7b-instruct`** caricato su `test_data/synthetic_smoke/synthetic_whatsapp_chat.txt`: Discovery (`COMPLETE`), Verifica di consistenza indipendente (`CONSISTENT`), Detection positiva (`PRESENT`) e negativa (`ABSENT`) | **Verificato LIVE su Windows con LM Studio reale**. |
| **Suddivisione microchunk, checkpoint SQLite e export `SAFE_REDACTED`** | Split multi-microchunk (`20` messaggi → `3` microchunk), salvataggio e ripresa da SQLite (`0` chiamate LLM ripetute al secondo passaggio) ed esportazione JSON `SAFE_REDACTED` | **Verificato e superato**. |
| **Interfaccia utente Streamlit (6 pagine al momento della verifica) e server HTTP** | Rendering programmatico `AppTest` di tutte le sezioni della sidebar allora presenti (sei; la voce `6. Sistema / Stato` è stata poi rimossa dal menu e le sezioni attuali sono cinque) + avvio reale `streamlit run app.py` con verifica HTTP `200 ok` su `/_stcore/health` + CLI `python -m profiler` | **Verificato e superato su Windows**. |

### 19.2 Prove NON eseguite in questa sessione e limiti noti

1. **Installazione ed esecuzione nativa su Ubuntu**: tutta la procedura Ubuntu (Sezioni 6, 7.1, 8.1, 10.2) è stata redatta verificando puntualmente i vincoli reali di `pyproject.toml` (`Python >= 3.12`), dei pacchetti `apt` (`tesseract-ocr`, `tesseract-ocr-ita`, `libfuse2t64`/`libfuse2`) e dei moduli Python multipiattaforma, ma **non è stata eseguita su una macchina o VM Ubuntu durante questa sessione** (svolta interamente su host Windows).
2. **Download dei pesi Faster-Whisper `base` (~145 MB)**: per rispettare il vincolo di non scaricare inutilmente modelli pesanti da Internet durante la pulizia, la catena completa `FasterWhisperTranscriber` + `PyAV` + `CTranslate2` è stata collaudata dal vivo con i pesi `faster-whisper-tiny` già presenti nella cache locale dell'host; al primo utilizzo reale dalla UI senza cache verrà scaricato il modello predefinito `base` (`model_name="base"`).
3. **Configurazione `PATH` di Tesseract su Windows**: sull'host Windows esaminato `C:\Program Files\Tesseract-OCR\tesseract.exe` è installato e funzionante con `ita+eng`, ma se `C:\Program Files\Tesseract-OCR` non è aggiunto al `PATH` utente (Sezione 8.1, Passo 3) la UI mostra `OCR (Immagini): Non installato`.
4. **Limiti architetturali noti del software**:
   - Nessuna decifrazione di backup WhatsApp cifrati (`.crypt12`, `.crypt14`, `.crypt15`);
   - Nessun modello Vision attivo per descrivere scene fotografiche prive di testo (solo OCR tramite Tesseract);
   - Nessuna indicizzazione semantica tramite embedding / database vettoriali (la sezione `4. Ricerca` è intenzionalmente deterministica e lessicale per garantire riproducibilità forense).

### 19.3 Ambienti hardware e software registrati
- **Host Ubuntu (test iniziali e benchmark di ottobre 2026)**: Ubuntu 22.04.5 LTS, Intel Core i5-9600K (6 core), 31 GB di RAM, NVIDIA GeForce GTX TITAN X (12 GB di VRAM, driver 580), Python 3.12.4. I rapporti `hardware_and_environment.txt` di ogni esecuzione sono conservati nell'archivio `chat-20261021T182727Z-1-001.zip` nella radice del repository.
- **Host Windows 11 (sessione di pulizia e verifiche del §19.1)**: l'hardware di questo computer **non è registrato** nei file del repository. I benchmark di sola persistenza dei checkpoint (`benchmark_checkpoint_persistence_results.json`, archiviato in `Trash/repository_cleanup_2026-11-07/`) riportano Windows 11 e Python 3.14.7: non coinvolgono STT né CTranslate2, quindi non contraddicono la raccomandazione di usare Python 3.12 per l'uso operativo.
- **Conseguenza metodologica**: le misure di tempo di due esecuzioni sono confrontabili solo se eseguite sulla stessa macchina e con la stessa configurazione di LM Studio.
