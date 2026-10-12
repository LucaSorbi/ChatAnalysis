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

### 5. Controllo dell'endpoint di loopback e porta 1234
Prima di avviare o interrogare LM Studio, verificare che la porta loopback locale risponda esclusivamente su `127.0.0.1:1234`:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:1234/v1/models
```
Se il codice HTTP restituito non è 200, avviare LM Studio Local Server (vedi Passo 6).

---

### 6. Verifica del modello Qwen2.5-7B-Instruct Q4_K_M
1. In LM Studio su Ubuntu, selezionare il modello:
   `qwen2.5-7b-instruct` (o file GGUF `qwen2.5-7b-instruct-q4_k_m.gguf`).
2. Configurare l'accelerazione hardware GPU:
   - **GPU Offload:** Maximize / Full offload (la GPU TITAN X dispone di 12 GB VRAM dedicati).
3. Verificare che il modello compaia come *Loaded* nel tab **Local Server**.
4. Verificare dall'API che il modello sia esposto:
   ```bash
   curl -s http://127.0.0.1:1234/v1/models | grep -i "qwen2.5-7b-instruct"
   ```

---

### 7. Verifica Context Length esplicita: 8192
Nella schermata di configurazione avanzata del modello in LM Studio:
- **Context Length:** Impostare rigorosamente a **`8192`** token (non lasciare su Auto o 2048 o 4096).
- **Temperature:** Impostare a `0.0` (determinismo scientifico).
- Lo script `smoke_operational_ai.py` interroga le API native LM Studio (`/v1/models`, `/api/v0/models`) o accetta `--confirmed-context 8192` se validato dalle evidenze manuali del loader.
- Un contesto confermato pari a 4096 produrrà immediatamente **FAILED (Exit Code 1)**.
- Un contesto non verificabile produrrà **NOT_VERIFIED (Exit Code 2)**.

---

### 8. Smoke operativo live con preservazione dell'Exit Code (Pipefail)
Eseguire lo smoke test live catturando simultaneamente stdout/stderr su file e salvaguardando l'exit code di Python mediante `set -o pipefail` e `PIPESTATUS[0]`:
```bash
mkdir -p audit_logs_ubuntu

set -o pipefail

python3 scripts/smoke_operational_ai.py \
  --model qwen2.5-7b-instruct \
  2>&1 | tee audit_logs_ubuntu/smoke_live_qwen_$(date +%Y%m%d_%H%M%S).log

rc=${PIPESTATUS[0]}
echo "Smoke exit code: $rc"
test "$rc" -eq 0
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

### 13. Registrazione di response.model, evidence IDs, consistency e latenza
Verificare che i metadata di inferenza siano stampati e archiviati:
- Modello richiesto vs `response.model` effettivo.
- Evidenze forensi associate a ciascun topic.
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
| **LIVE_VERIFIED (PASS)** | **0** | Connessione reale a LM Studio stabilita. Modello confermato equivalente a `qwen2.5-7b-instruct` (o mapping verificato su instance ID opaco). Context length 8192 verificato. Tutti i 15 controlli `[PASSED]`. Isolamento documentale garantito. Stampa finale: `ESITO SMOKE TEST OPERATIVO: SUCCESSO`. |
| **MOCK_VERIFIED (PASS MOCK)** | **0** | Esecuzione con `--fake` per collaudo logico offline. Solo simulazioni e mock. Non autorizza in nessun caso la certificazione live. |
| **FAILED (FAIL)** | **1** | Uno o più controlli obbligatori hanno stato `[FAILED]`: modello confermato errato, context confermato diverso da 8192 (es. 4096), contaminazione tra documenti, crash, timeout, o JSON malformato. Stampa finale: `ESITO SMOKE TEST OPERATIVO: FALLITO`. |
| **NOT_VERIFIED** | **2** | Server LM Studio non raggiungibile su `127.0.0.1:1234`, identità del modello non verificabile, oppure context length non verificabile. Segnala ambiente live non pronto o non qualificato. |

---

### 16. Trasferimento dei log al PC di sviluppo
1. Copiare i log da `audit_logs_ubuntu/` su supporto esterno o via SCP.
2. Posizionare i file in `audit_logs_ubuntu/` nel workspace principale di ChatAnalysis.
3. Non alterare né ripulire i log originali.
