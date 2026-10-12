# RUNBOOK OPERATIVO SMOKE TEST — AMBIENTE UBUNTU LAB (LIVE GATE READINESS)

**Destinazione e Specifiche Hardware Macchina di Test:**
- **Host / Sistema Operativo:** Ubuntu 22.04.5 LTS
- **Processore:** Intel Core i5-9600K (6 core @ 3.70 GHz base, 4.60 GHz turbo)
- **RAM di Sistema:** 31 GB DDR4
- **Scheda Grafica:** NVIDIA GeForce GTX TITAN X 12 GB VRAM (Driver NVIDIA 535+ / CUDA compatibile)
- **Interprete Python:** Python 3.12.4 (Virtual Environment `.venv`, NON Anaconda base)
- **Server Inferenza Locale:** LM Studio v0.3.x / v0.4.x
- **Percorso Repository sul Target:** `/home/nottedeiricercatori/Desktop/LUCA/Tesi`
- **Endpoint Loopback:** `http://127.0.0.1:1234`

---

## PROCEDURA OPERATIVA CRONOLOGICA (PASSI 1 - 15)

### 1. Controllo preventivo dei file effettivamente versionati
Prima di procedere al trasferimento o esecuzione su Ubuntu, verificare esattamente quali file sono tracciati e se vi sono file estranei non tracciati:
```bash
cd /home/nottedeiricercatori/Desktop/LUCA/Tesi
git status
git diff --stat
```
- Verificare che non vi siano file temporanei o contaminazioni esterne nel tree.
- I sorgenti applicativi e gli script devono coincidere rigorosamente con il bundle di certificazione Windows.

---

### 2. Confronto dell'hash del commit tra Windows e Ubuntu
Verificare che l'albero git corrisponda all'identico commit di riferimento:
```bash
git log -n 1 --oneline
git rev-parse HEAD
```
- Il commit hash su Ubuntu deve combaciare con quello di rilascio Windows.

---

### 3. Verifica del working tree pulito sul PC Ubuntu
Assicurarsi che l'albero di lavoro non presenti modifiche non committate:
```bash
test -z "$(git status --porcelain)" && echo "WORKING TREE PULITO: OK" || echo "ATTENZIONE: WORKING TREE SPORCO"
```
- **Regola tassativa:** Non modificare il codice direttamente su Ubuntu durante i test. Ogni modifica deve avvenire sul repository sorgente autoritativo.

---

### 4. Installazione delle dipendenze dentro `.venv` (Zero Anaconda)
Non utilizzare l'ambiente `base` di Anaconda. Assicurarsi di operare esclusivamente nel virtual environment isolato `.venv`:
```bash
# Se un ambiente conda è attivo, disattivarlo
conda deactivate 2>/dev/null || true

# Attivazione dell'ambiente virtuale dedicato
source .venv/bin/activate

# Verifica interprete Python
which python3
python3 --version
```
L'interprete deve puntare rigorosamente a `/home/nottedeiricercatori/Desktop/LUCA/Tesi/.venv/bin/python3` (Python 3.12.x).
Verificare le dipendenze installate:
```bash
pip list | grep -E "streamlit|pytest|requests|pydantic|httpx"
pip install -e . --no-deps
```

---

### 5. Controllo dell'endpoint di loopback, versione LM Studio e disponibilità REST API nativa
Prima di avviare o interrogare LM Studio, verificare che la porta loopback locale risponda esclusivamente su `127.0.0.1:1234`:
```bash
# 5.1 Verifica versione CLI/daemon (se lms è presente nel PATH)
lms --version 2>/dev/null || echo "lms CLI non nel PATH (verificare interfaccia GUI LM Studio)"
lms status 2>/dev/null || true

# 5.2 Controllo disponibilità endpoint OpenAI-compatibile (/v1/models)
curl -s -o /dev/null -w "OpenAI /v1/models HTTP: %{http_code}\n" http://127.0.0.1:1234/v1/models

# 5.3 Controllo disponibilità REST API nativa LM Studio (/api/v1/models)
curl -s -o /dev/null -w "Native /api/v1/models HTTP: %{http_code}\n" http://127.0.0.1:1234/api/v1/models
```
Se il codice HTTP restituito non è 200, avviare LM Studio Local Server (vedi Passo 6).

---

### 6. Verifica del modello Qwen2.5-7B-Instruct Q4_K_M e ispezione istanze caricate
1. In LM Studio su Ubuntu, selezionare il modello:
   `qwen2.5-7b-instruct` (o file GGUF `qwen2.5-7b-instruct-q4_k_m.gguf`).
2. **Quantizzazione Rigorosa Q4_K_M (Certificazione Scientifica):**
   - La quantizzazione deve essere rigorosamente `Q4_K_M` (`{"quantization": {"name": "Q4_K_M", "bits_per_weight": 4}}` o stringa canonica `Q4_K_M`).
   - **Regola Bloccante:** Se è caricato un modello con quantizzazione diversa (es. `Q8_0`), lo smoke test **FALLISCE (Exit Code 1)** (`CHECK_MODEL_QUANTIZATION: FAILED`). Non è consentito dichiarare `LIVE_VERIFIED` per `Q4_K_M` se è in esecuzione `Q8_0`.
   - Se `Q4_K_M` è installata ma non caricata mentre `Q8_0` è già in memoria, il resolver **non seleziona silenziosamente Q8_0**, ma prepara il caricamento della variante `Q4_K_M`.
3. Configurare l'accelerazione hardware GPU:
   - **GPU Offload:** Maximize / Full offload (la GPU TITAN X dispone di 12 GB VRAM dedicati).
4. Verificare che il modello compaia come *Loaded* nel tab **Local Server**.
5. Verificare dall'API che il modello, la quantizzazione e le sue istanze siano esposti:
   ```bash
   # Ispezione CLI via lms ps --json (quando supportato dalla versione installata)
   lms ps --json 2>/dev/null || true

   # Ispezione payload nativo LM Studio /api/v1/models (verifica quantization.name e loaded_instances)
   curl -s http://127.0.0.1:1234/api/v1/models | jq '.models[] | {key: .key, quantization: .quantization, instances: .loaded_instances}' 2>/dev/null || curl -s http://127.0.0.1:1234/api/v1/models

   # Ispezione endpoint standard /v1/models
   curl -s http://127.0.0.1:1234/v1/models | jq . 2>/dev/null || curl -s http://127.0.0.1:1234/v1/models
   ```
6. **Verifica parametri di caricamento (Instance ID & API Identifier):**
   - Rilevare il `model_key` o `id` (es. `lmstudio-community/Qwen2.5-7B-Instruct-GGUF`).
   - Rilevare l'`instance_id` (es. `qwen-instance-8192` o stringa opaca generata dal server).
   - Verificare che lo `status` sia `"loaded"`.
   - **Target Instance ID esplicito:** Se sono caricate più istanze dello stesso modello o si desidera vincolare l'esecuzione a una specifica istanza, specificare `--target-instance-id <instance_id>` allo smoke script. Se il target richiesto non esiste, l'esecuzione fallisce con errore tipizzato senza alcun fallback silenzioso ad altre istanze.

---

### 7. Verifica Context Length Rigorosa (Nessun Default Arbitrario 8192)
Nella schermata di configurazione avanzata del modello in LM Studio:
- **Context Length:** Impostare rigorosamente a **`8192`** token nell'interfaccia o nel payload di caricamento (`config.context_length: 8192` o `load_config.context_length: 8192`).
- **Separazione dei Contesti:** Il sistema distingue rigorosamente:
  - `requested_context_length` (8192)
  - `configured_context_length` (8192)
  - `loaded_instance_context_length` (effettivamente caricato in memoria dal backend: letto da `loaded_instances[].config.context_length` o `load_config.context_length`)
  - `model_max_context_length` (massimo architetturale, es. 32768)
  - `context_evidence_source` (fonte dell'evidenza: `SERVER_API_LOADED_INSTANCE` o `DOCUMENTED_MANUAL_LOADER_EVIDENCE`)
- **Regole di Esito e Salvaguardie Scientifiche:**
  - `loaded_instance_context_length == 8192`: **PASS consentito**.
  - `loaded_instance_context_length == 4096`: **FAILED (Exit Code 1)** (anche se `model_max_context_length == 32768`).
  - Context non confermato dal backend: **NOT_VERIFIED (Exit Code 2)**. Il sistema non attribuisce mai 8192 come valore predefinito a un contesto non confermato.
  - **Uso di `--confirmed-context 8192`:** Consentito SOLO se la versione di LM Studio non espone il context via API, a condizione di conservare i log di avvio del loader come evidenza manuale verificabile (`DOCUMENTED_MANUAL_LOADER_EVIDENCE`). Non deve essere utilizzato come scorciatoia automatica in assenza di evidenza esterna verificabile.
  - **Incompatibilità versione:** Se la versione installata di LM Studio presenta incompatibilità con il contratto atteso (es. payload REST non conformi o assenti), **registrare l'anomalia nel log senza modificare per nessun motivo il benchmark scientifico congelato**.
  - **Regola LIVE_VERIFIED:** Non dichiarare MAI `LIVE_VERIFIED` finché non avviene una vera inferenza (Discovery e Detection completate con token, latenza e response model verificati).

---

### 8. Smoke operativo live con Client Unificato Verificato e Pipefail
Eseguire lo smoke test live. Lo script inietta il client verificato (`LmStudioClient`) nel servizio per tutte le fasi di inferenza (Discovery, Detection, Control, File reale) garantendo che `response.model` appartenga al binding verificato:
```bash
mkdir -p audit_logs_ubuntu

set -o pipefail

# Esecuzione standard con quantizzazione obbligatoria Q4_K_M
python3 scripts/smoke_operational_ai.py \
  --model qwen2.5-7b-instruct \
  --required-quantization Q4_K_M \
  2>&1 | tee audit_logs_ubuntu/smoke_live_qwen_$(date +%Y%m%d_%H%M%S).log

rc=${PIPESTATUS[0]}
echo "Smoke exit code: $rc"
test "$rc" -eq 0
```
- Per specificare un instance ID target esclusivo (nessun fallback silenzioso):
```bash
python3 scripts/smoke_operational_ai.py \
  --model qwen2.5-7b-instruct \
  --target-instance-id <instance_id> \
  --required-quantization Q4_K_M \
  2>&1 | tee audit_logs_ubuntu/smoke_live_qwen_target_$(date +%Y%m%d_%H%M%S).log
```
- Se `rc` != 0, la pipeline fallisce immediatamente e non maschera l'errore dietro al successo di `tee`.

---

### 9. Verifica Topic Discovery reale (Fase 1)
Durante l'esecuzione:
- Verificare che Topic Discovery interroghi il documento forense tramite `LocalTopicAnalysisService`.
- Verificare che generi almeno 1 topic valido con etichetta descrittiva in italiano.
- Verificare che il risultato sia registrato nel registro document-scoped delle run immutabili.

---

### 10. Nuova Topic Detection indipendente (Fase 2 — Prova del Nove)
Durante la Fase 2:
- La Topic Detection indipendente riceve unicamente `label` e `short_description` (nessun suggerimento di evidence IDs nel prompt).
- Verificare che il motore valuti il documento da zero e calcoli lo stato di Consistency (`CONSISTENT`, `PARTIAL` o `CONFLICT`).

---

### 11. Controllo semantico Logistica e Ispezione Depositi (Fase 3)
- **Topic Controllato:** `Logistica e Ispezione Depositi`
- **Target Evidence:** `msg::5::VISION_DESCRIPTION`
- **Regola Scientifica:**
  - Se il modello risponde `PRESENT`, verificare che citi `msg::5::VISION_DESCRIPTION` con un `rationale` non vuoto.
  - Se il modello risponde `ABSENT`, **non forzare né considerare il test fallito**: l'esito viene registrato fedelmente come discrepanza empirica del modello nel report della tesi.
  - Crash, eccezioni, timeout o assenza del campo `rationale` causano invece fallimento operativo immediato.

---

### 12. Test FILE reale sintetico (WhatsApp TXT e ZIP — Fasi 5 e 6)
- Lo script non usa mock documentali: importa le fixture sintetiche `synthetic_whatsapp_chat.txt` e `synthetic_whatsapp_chat.zip` tramite `ingest_file_payload()` (`WhatsAppExportImporter`, validation, normalization, entity resolution, UnifiedBuilder).
- Verificare nei log:
  - `status=SUCCESS`
  - `raw_records=4`
  - `unified_msgs=4`
  - `source_name=whatsapp_export`
  - Topic Discovery e Detection eseguite sul documento reale importato.
  - **Zero leak** tra `doc::demo_forensic_chat` e `doc::whatsapp_export::...`.

---

### 13. Registrazione e Verifica di response.model, Binding, consistency e latenza
Verificare che i metadata di entrambe le inferenze siano stampati e archiviati:
- `discovery_response_model` e `detection_response_model` verificati entrambi rispetto al binding del client attivo.
- Assenza di sostituzione dell'istanza o del modello tra Discovery e Detection.
- Evidenze forensi associate a ciascun topic (nessun evidence ID passato come suggerimento alla seconda inferenza).
- Tempo di risposta (latenza in secondi) e token prompt/completion.
- Stato di concordanza tra Discovery e Detection.

---

### 14. Salvataggio affidabile dei log con exit code
Per esecuzioni ripetute o scriptate di collaudo, utilizzare il pattern standard:
```bash
set -o pipefail
LOGFILE="audit_logs_ubuntu/smoke_live_qwen_final.log"
python3 scripts/smoke_operational_ai.py --model qwen2.5-7b-instruct 2>&1 | tee "$LOGFILE"
rc=${PIPESTATUS[0]}
echo "SMOKE_EXIT_CODE=$rc"
if [ "$rc" -ne 0 ]; then
    echo "ERRORE: Lo smoke test operativo è fallito con codice $rc!"
    exit "$rc"
fi
```

---

### 15. Criteri espliciti di certificazione (PASS, FAILED, NOT_VERIFIED)

| Esito Gate | Exit Code | Condizioni e Comportamento Obbligatorio |
|---|:---:|---|
| **LIVE_VERIFIED (PASS)** | **0** | Connessione reale a LM Studio stabilita. Modello confermato equivalente a `qwen2.5-7b-instruct` (o mapping verificato su instance ID opaco). Quantizzazione `Q4_K_M` confermata dal server (`CHECK_MODEL_QUANTIZATION: PASSED`). Context length 8192 confermato dal server (o evidenza manuale documentata). Client iniettato coerente su Discovery e Detection. Tutti i controlli `[PASSED]`. Isolamento documentale garantito. Stampa finale: `ESITO SMOKE TEST OPERATIVO: SUCCESSO`. |
| **MOCK_VERIFIED (PASS MOCK)** | **0** | Esecuzione con `--fake` per collaudo logico offline. Solo simulazioni e mock. Non autorizza in nessun caso la certificazione live. |
| **FAILED (FAIL)** | **1** | Uno o più controlli obbligatori hanno stato `[FAILED]`: modello confermato errato, quantizzazione non conforme a Q4_K_M (es. Q8_0), context confermato diverso da 8192 (es. 4096), sostituzione imprevista del modello durante lo smoke, target instance ID inesistente, contaminazione tra documenti, crash, timeout, o JSON malformato. Stampa finale: `ESITO SMOKE TEST OPERATIVO: FALLITO`. |
| **NOT_VERIFIED** | **2** | Server LM Studio non raggiungibile su `127.0.0.1:1234`, identità del modello non verificabile, oppure quantizzazione/context length non confermati dal server (UNKNOWN) e privi di evidenza manuale documentata. Segnala ambiente live non pronto o non qualificato. |

---

### 16. Trasferimento dei log al PC di sviluppo
1. Copiare i log da `audit_logs_ubuntu/` su supporto esterno o via SCP.
2. Posizionare i file in `audit_logs_ubuntu/` nel workspace principale di ChatAnalysis.
3. Non alterare né ripulire i log originali.
