# CHATANALYSIS — RUNBOOK OPERATIVO WINDOWS 11 (64-BIT)
## Stabilizzazione Pipeline Topic Discovery, Topic Detection e Prova del Nove Locale

---

## 1. INTRODUZIONE E REQUISITI ARCHITETTURALI

**ChatAnalysis** è il sistema locale di analisi forense e semantica per conversazioni WhatsApp e Cellebrite sviluppato per tesi triennale in Informatica.

### Pipeline Forense e Semantica
```
RawRecord -> Validation -> Normalization -> Entity Resolution -> UnifiedMessage -> MessageEvidenceBundle -> ConversationEvidenceDocument -> Local AI (LM Studio) / Search / Streamlit UI
```

### Vincoli Operativi e Scientifici
- **Ambiente di Esecuzione**: Windows 11 nativo a 64-bit (PowerShell 5.1 / PowerShell 7+). Non è richiesto WSL2 né Docker.
- **AI Locale via LM Studio**: Endpoint vincolato `http://127.0.0.1:1234`. Nessun cloud, nessuna telemetria remota, nessun leakage di dati privati o giudiziari.
- **Nessun Framework Esterno Non Autorizzato**: Nessun RAG, nessun database vettoriale, nessun embedding cloud.
- **Architettura Deterministica e Forense**: Ogni evidenza conserva provenance, tracciamento crittografico SHA-256 e citazione verificabile.

---

## 2. PREPARAZIONE DELL'AMBIENTE WINDOWS 11

### 2.1 Requisiti di Sistema
- **Sistema Operativo**: Windows 11 64-bit (Build 22H2 o superiore raccomandata).
- **Processore**: CPU x86_64 moderna (Intel Core i5/i7/i9 10th+ gen o AMD Ryzen 5/7/9).
- **RAM**: Minimo 16 GB (raccomandati 32 GB per modelli 7B/8B).
- **GPU (Opzionale ma Raccomandata)**: NVIDIA GPU con architettura Turing/Ampere/Ada/Blackwell e driver CUDA aggiornati (VRAM >= 8 GB per offload completo di Q4_K_M). Esecuzione CPU supportata nativamente.
- **Python**: Versione **3.12** o superiore a 64-bit installata con l'opzione "Add Python to PATH" attiva.

### 2.2 Procedura Automatica con PowerShell
È disponibile lo script completo [scripts/windows_setup_and_verify.ps1](file:///scripts/windows_setup_and_verify.ps1):

```powershell
# Esecuzione dalla directory radice di ChatAnalysis (Tesi)
powershell -ExecutionPolicy Bypass -File .\scripts\windows_setup_and_verify.ps1
```

Lo script esegue automaticamente:
1. Verifica interprete Python (>= 3.12, 64-bit);
2. Creazione del virtual environment locale `.venv`;
3. Installazione in modalità editabile (`pip install -e ".[ui,stt,ocr,image,dev]"`);
4. Verifica degli import effettivi dei package;
5. Controllo dell'endpoint LM Studio locale (`http://127.0.0.1:1234`);
6. Esecuzione dei test di regressione e stabilizzazione (Topic Discovery, Detection, Prova del Nove, Snapshot F1);
7. Esecuzione dello smoke test sintetico end-to-end (`scripts/smoke_operational_ai.py`).

### 2.3 Procedura Manuale Passo-Passo
Se si preferisce configurare manualmente l'ambiente:

```powershell
# 1. Verifica Python
python --version
# Output atteso: Python 3.12.x o 3.13.x/3.14.x (64-bit)

# 2. Creazione dell'ambiente virtuale
python -m venv .venv

# 3. Attivazione dell'ambiente virtuale
.\.venv\Scripts\Activate.ps1

# 4. Aggiornamento strumenti di base
python -m pip install --upgrade pip setuptools wheel

# 5. Installazione ChatAnalysis con tutti i moduli
pip install -e ".[ui,stt,ocr,image,dev]"

# 6. Verifica degli import
python -c "import core, importer, validation, normalization, entity_resolution, unified, multimodal, ai, analysis, search, ui, streamlit; print('ALL_MODULES_OK')"
```

---

## 3. CONFIGURAZIONE DI LM STUDIO SU WINDOWS

### 3.1 Installazione di LM Studio
1. Scaricare **LM Studio for Windows** dal sito ufficiale: `https://lmstudio.ai/`.
2. Installare l'applicazione ed eseguirla.

### 3.2 Modelli Congelati per la Sperimentazione
I modelli scientifici del benchmark congelato sono:

| Modello Canonico | Quantizzazione | File GGUF Raccomandato | VRAM Stimata |
| :--- | :--- | :--- | :--- |
| **Qwen2.5-7B-Instruct** | Q4_K_M | `qwen2.5-7b-instruct-q4_k_m.gguf` | ~5.2 GB |
| **Meta-Llama-3.1-8B-Instruct** | Q4_K_M | `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` | ~5.8 GB |
| **DeepSeek-R1-Distill-Qwen-7B** | Q4_K_M | `DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf` | ~5.2 GB |

### 3.3 Configurazione Parametri del Server Locale
In LM Studio:
1. Aprire la scheda **Local Server** (icona a forma di due frecce `<->` o icona Server nella barra laterale).
2. Selezionare il modello da caricare (es. `Qwen2.5-7B-Instruct`).
3. Nella colonna di configurazione destra (**Configuration / Model Presets**):
   - **Context Length**: Impostare obbligatoriamente a **`8192`** token.
   - **GPU Offload**:
     - Se presente scheda NVIDIA: selezionare **GPU Offload (CUDA)**, slider al massimo (tutti i layer).
     - Se assente GPU o memoria insufficiente: impostare **CPU Only** (o offload parziale).
   - **Temperature**: `0.0` (determinismo scientifico).
   - **Top P**: `1.0`.
   - **Max Output Tokens**: `512` (per Topic Discovery chunking e Detection).
4. **Porta del Server**: Verificare che sia impostata su `1234` e binding su `127.0.0.1` (loopback).
5. Fare clic su **Start Server**.
6. Verificare da PowerShell:
   ```powershell
   curl http://127.0.0.1:1234/v1/models
   ```
   Deve rispondere con il JSON contenente l'ID del modello caricato.

---

## 4. ESECUZIONE DEI TEST E VALIDAZIONE

### 4.1 Test di Stabilizzazione Topic Discovery, Detection e Prova del Nove
Verifica il comportamento con chunk multipli (24/26 e 17/26), diagnostiche, checkpointing SQLite e isolamento inferenziale:

```powershell
pytest tests/unit/test_topic_discovery_detection_stabilization.py -v
```
*Esito atteso: 9 passed su 9 in ~10 secondi.*

### 4.2 Test di Ownership Snapshot e Assenza TOCTOU (F1/F2/F3)
Verifica l'atomicità e la protezione da sovrascritture o sostituzioni concorrenti:

```powershell
pytest tests/unit/test_checkpoint_preflight_resume.py -k "test_122 or test_136 or test_137 or test_138 or test_139 or test_140 or test_141 or test_144" -v
```
*Esito atteso: 8 passed su 8.*

### 4.3 Smoke Test Operativo Sintetico

#### Modalità Mock (Offline / Senza LM Studio attivo)
```powershell
python scripts/smoke_operational_ai.py --mock
```
*Classificazione esito: `MOCK_VERIFIED` (Exit Code 0). Valida tutti i 18 controlli obbligatori della pipeline.*

#### Modalità Live (Con LM Studio e modello caricato a 8192 token)
```powershell
python scripts/smoke_operational_ai.py
```
*Classificazione esito: `LIVE_VERIFIED` (Exit Code 0). Interroga realmente LM Studio su loopback.*

---

## 5. AVVIO DELL'INTERFACCIA STREAMLIT SU WINDOWS

### 5.1 Avvio Tramite Script Assistito
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_streamlit_windows.ps1
```

### 5.2 Avvio Manuale Diretto
```powershell
.\.venv\Scripts\Activate.ps1
streamlit run ui/application.py --server.port 8501
```

Accedere tramite browser all'indirizzo: `http://localhost:8501`.

---

## 6. WORKFLOW FORENSE NELL'INTERFACCIA STREAMLIT

### Fase 1: Importazione Conversazione
1. Nella barra laterale o nel modulo di ingestion, selezionare **WhatsApp Export (TXT o ZIP)** oppure caricare il dataset dimostrativo sintetico.
2. Cliccare su **Elabora Conversazione**.
3. Il sistema esegue:
   - Validazione strutturale formale
   - Normalizzazione dei timestamp (gestione timezone UTC/naive)
   - Entity Resolution deterministica
   - Costruzione del `ConversationEvidenceDocument` con fingerprint crittografico SHA-256 univoco.

### Fase 2: Topic Discovery Multi-Chunk
1. Spostarsi nella sezione **Analisi Tematica / Topic Discovery**.
2. Parametri operativi predefiniti:
   - Profilo: `BALANCED` (chunking dinamico rispettoso dei limiti del modello)
   - Contesto: `8192`
   - Temperatura: `0.0`
   - Checkpointing SQLite: **Abilitato**.
3. Cliccare su **Avvia Topic Discovery**.
4. Durante l'elaborazione, la progress bar visualizza il completamento di ciascun blocco.
5. In caso di fallimento isolato (es. timeout o JSON non valido sul chunk 10 o 19):
   - Lo stato globale dell'analisi diventa **`PARTIAL`** (mai falso `COMPLETE`).
   - I blocchi validati (es. 24/26) sono conservati e memorizzati in modo incrementale su SQLite.
   - I blocchi falliti vengono evidenziati con la rispettiva categoria diagnostica (`TIMEOUT`, `SCHEMA_VALIDATION_ERROR`, ecc.).
   - Il pulsante **Riprova Blocchi Mancanti** riesegue *esclusivamente* i chunk falliti, riutilizzando i 24 validati senza ricalcolo.

### Fase 3: Topic Detection Manuale
1. Nella sezione **Topic Detection**, inserire un argomento investigativo (Label e Descrizione).
2. Regole di aggregazione operative:
   - **PRESENT**: se almeno un chunk contiene evidenze a supporto (anche se 17/26 chunk completati -> stato `PARTIAL`, verdetto `PRESENT`).
   - **ABSENT**: *SOLTANTO* se tutti i 26 chunk attesi sono stati analizzati con successo e nessuno contiene il topic.
   - **UNCERTAIN**: se non vi sono evidenze PRESENT e l'esecuzione è incompleta (evita falsi negativi dovuti a errori operativi).
   - In presenza di chunk falliti, la card mostra un avviso ambrato prominente con il conteggio `Completati: X/Y | Falliti: Z`.

### Fase 4: Prova del Nove e Consistency Verification
1. Dalla lista dei topic scoperti nella Topic Discovery, cliccare su **Verifica Consistenza (Prova del Nove)**.
2. Il sistema avvia un'inferenza di Topic Detection completamente **indipendente**:
   - Invia al modello esclusivamente `label` e `short_description`.
   - **NON invia** gli `evidence_ids` scoperti in precedenza (isolamento inferenziale assoluto).
3. Il `ConsistencyService` confronta post-hoc le evidenze trovate dalla Discovery e dalla Detection:
   - `CONSISTENT`: se le evidenze coincidono o si sovrappongono in modo congruente.
   - `PARTIALLY_CONSISTENT` / `INCONSISTENT`: se vi sono divergenze, documentandone i motivi nei metadati.

---

## 7. CASO SEMANTICO DI CONTROLLO SCIENTIFICO

Per convalidare la corretta gestione delle evidenze multimodali e testuali:
- **Label**: `Logistica e Ispezione Depositi`
- **Description**: `Ispezione visiva del magazzino merci con casse sigillate e sicurezza`
- **Evidence ID di controllo**: `msg::5::VISION_DESCRIPTION`

Verificare che la descrizione visiva dell'allegato sia inclusa tra le sezioni analizzabili (`EvidenceSourceType.VISION_DESCRIPTION`) e che il modello restituisca `PRESENT` citando esplicitamente tale ID.

---

## 8. RISOLUZIONE PROBLEMI (TROUBLESHOOTING)

| Sintomo | Causa Probabile | Azione Correttiva |
| :--- | :--- | :--- |
| `LmStudioUnavailableError: Connessione rifiutata` | LM Studio non avviato o porta non corretta | Avviare LM Studio, verificare Local Server su porta 1234 e cliccare "Start Server". |
| `AiModelMismatchError: Modello in memoria discordante` | È caricato un modello diverso da quello richiesto | In LM Studio caricare `qwen2.5-7b-instruct` o selezionare dall'interfaccia il modello corretto. |
| `Context Length Mismatch: richiesto 8192, rilevato 2048/4096` | Preset di default in LM Studio con contesto ridotto | Nella colonna di configurazione del server in LM Studio, impostare "Context Length" = `8192` e ricaricare il modello. |
| `OutOfMemoryError / CUDA Out of Memory` | VRAM della GPU insufficiente per offload totale | Ridurre il numero di layer GPU offloaded in LM Studio (es. 24 layer anziché 32) o selezionare CPU execution. |
| `ImportError / ModuleNotFoundError` | Ambiente virtuale non attivato | Eseguire `.\.venv\Scripts\Activate.ps1` prima di lanciare i comandi Python. |
| `ExecutionPolicy Error` in PowerShell | Politica di esecuzione restrittiva di Windows | Eseguire lo script con `powershell -ExecutionPolicy Bypass -File .\scripts\windows_setup_and_verify.ps1`. |
| Topic Discovery si ferma con stato `PARTIAL` | Timeout o errore su singoli blocchi LLM | Utilizzare il pulsante di ripresa selettiva; verificare temperatura a 0.0 e carico della macchina. |
