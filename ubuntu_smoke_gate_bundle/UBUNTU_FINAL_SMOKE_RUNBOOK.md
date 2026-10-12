# RUNBOOK OPERATIVO SMOKE TEST — AMBIENTE UBUNTU LAB

**Destinazione e Specifiche Hardware Macchina di Test:**
- **Host / Sistema Operativo:** Ubuntu 22.04.5 LTS
- **Processore:** Intel Core i5-9600K (6 core @ 3.70 GHz base, 4.60 GHz turbo)
- **RAM di Sistema:** 31 GB DDR4
- **Scheda Grafica:** NVIDIA GeForce GTX TITAN X 12 GB VRAM (Driver NVIDIA 535+ / CUDA compatibile)
- **Interprete Python:** Python 3.12.4 (Virtual Environment `.venv`)
- **Server Inferenza Locale:** LM Studio v0.3.x / v0.4.x
- **Percorso Repository sul Target:** `/home/nottedeiricercatori/Desktop/LUCA/Tesi`
- **Endpoint Loopback:** `http://127.0.0.1:1234`

---

## PROCEDURA OPERATIVA CRONOLOGICA (PASSI 1 - 14)

### 1. Verifica dello stato Git e delle modifiche locali
Posizionarsi nella cartella del repository e verificare lo stato dei sorgenti:
```bash
cd /home/nottedeiricercatori/Desktop/LUCA/Tesi
git status
```
- Verificare che il branch attivo sia allineato con la consegna `main`.
- **Regola:** Non apportare modifiche manuali al codice sorgente sul PC Ubuntu, salvo diagnostica esplicita.

---

### 2. Verifica della versione Python e dell'ambiente virtuale
Attivare l'ambiente virtuale Python 3.12 e controllare l'interprete:
```bash
source .venv/bin/activate
python3 --version
which python3
```
- L'output di `python3 --version` deve indicare `Python 3.12.4` (o `3.12.x`).
- L'interprete deve puntare a `/home/nottedeiricercatori/Desktop/LUCA/Tesi/.venv/bin/python3`.

---

### 3. Installazione / Verifica delle dipendenze necessarie
Verificare che i pacchetti operativi siano installati e coerenti:
```bash
pip list | grep -E "streamlit|pytest|requests|pydantic|httpx"
```
Se necessario, reinstallare in modalità editabile senza toccare dipendenze esterne:
```bash
pip install -e . --no-deps
```

---

### 4. Esecuzione preliminare di Pytest
Prima di avviare LM Studio, verificare che la suite di test offline sia verde:
```bash
pytest tests/unit/test_smoke_operational_ai.py tests/unit/test_discovery_provenance_hardening.py -v
```
Tutti i test devono passare (`PASSED`) con Exit Code 0.

---

### 5. Avvio del server locale LM Studio
1. Avviare l'interfaccia grafica di LM Studio su Ubuntu.
2. Accedere alla scheda **Local Server** (icona `<->` o Developer Server nel menu laterale).
3. Configurare i parametri di binding:
   - **Host:** `127.0.0.1` (loopback locale, zero connessioni esterne)
   - **Port:** `1234`
   - **CORS:** Abilitato per richieste locali.
4. Premere **Start Server** e verificare che nei log di LM Studio compaia:
   `Server listening on 127.0.0.1:1234`.
5. Da un terminale Ubuntu parallelo, verificare la rispondenza dell'endpoint:
   ```bash
   curl -s http://127.0.0.1:1234/v1/models
   ```

---

### 6. Verifica del modello Qwen2.5-7B-Instruct Q4_K_M
1. In LM Studio, selezionare il modello:
   `qwen2.5-7b-instruct` (o file GGUF `qwen2.5-7b-instruct-q4_k_m.gguf`).
2. Configurare l'accelerazione hardware GPU:
   - **GPU Offload:** Maximize / Full offload (la GPU TITAN X dispone di 12 GB VRAM dedicati, ampiamente sufficienti per ospitare tutti i layer del modello 7B Q4).
3. Verificare che il modello compaia come *Loaded* e risponda alle chiamate API.

---

### 7. Verifica Context Length esplicita: 8192
Nella schermata di configurazione avanzata del modello in LM Studio:
- **Context Length:** Impostare rigorosamente a **`8192`** token (non lasciare su Auto o 2048).
- **Temperature:** Impostare a `0.0` (determinismo scientifico).
- Verificare che il campo `context_length` restituito dall'endpoint nativo sia conforme a 8192.

---

### 8. Esecuzione dello smoke test operativo live
Eseguire lo script autoritativo connettendosi direttamente a LM Studio locale:
```bash
python3 scripts/smoke_operational_ai.py --model qwen2.5-7b-instruct
```
Lo script non utilizza mock: effettua chiamate reali di inferenza forense locale attraverso `LocalTopicAnalysisService`.

---

### 9. Verifica Topic Discovery → Detection (Prova del Nove)
Durante la Fase 1 e la Fase 2 dell'esecuzione:
- Verificare che Discovery produca almeno 1 topic con etichetta coerente (in italiano).
- Verificare che la seconda Detection indipendente riceva solo `label` e `short_description` (nessun suggerimento di evidence IDs nel prompt).
- Verificare che venga calcolato e stampato lo stato di Consistency (`CONSISTENT`, `PARTIAL` o `CONFLICT`).

---

### 10. Verifica separata del caso di controllo semantico Logistica
Durante la Fase 3 dell'esecuzione:
- **Topic Controllato:** `Logistica e Ispezione Depositi`
- **Target Evidence:** `msg::5::VISION_DESCRIPTION`
- **Regola Scientifica:**
  - Se il modello risponde `PRESENT`, verificare che citi `msg::5::VISION_DESCRIPTION` con un `rationale` non vuoto.
  - Se il modello risponde `ABSENT`, **non forzare né considerare il test fallito**: l'esito viene registrato fedelmente come discrepanza empirica del modello nel report della tesi.
  - Timeout, crash, JSON malformato o assenza di rationale costituiscono invece anomalie operative.

---

### 11. Verifica FILE importato realmente (WhatsApp TXT e ZIP)
Durante la Fase 5 e la Fase 6 dell'esecuzione:
- Lo script non usa documenti mock: importa la fixture `synthetic_whatsapp_chat.txt` attraverso `ingest_file_payload()` (importer reale `WhatsAppExportImporter`, validation, normalization, entity resolution, UnifiedBuilder).
- Verificare nei log:
  - `status=SUCCESS`
  - `raw_records=4`
  - `unified_msgs=4`
  - `source_name=whatsapp_export`
  - Topic Discovery e Detection eseguite sul documento reale.
  - **Zero leak** tra `doc::demo_forensic_chat` e `doc::whatsapp_export::...`.

---

### 12. Salvataggio di log e metadata
Catturare l'intero output e salvarlo in una directory di audit:
```bash
mkdir -p /home/nottedeiricercatori/Desktop/LUCA/Tesi/audit_logs_ubuntu
python3 scripts/smoke_operational_ai.py --model qwen2.5-7b-instruct | tee /home/nottedeiricercatori/Desktop/LUCA/Tesi/audit_logs_ubuntu/smoke_live_qwen_$(date +%Y%m%d_%H%M%S).log
```

---

### 13. Criteri espliciti di successo e fallimento

| Esito | Exit Code | Criteri e Condizioni |
|---|:---:|---|
| **SUCCESSO (PASS)** | **0** | Tutti i 13 controlli obbligatori hanno stato `[PASSED]`. Isolamento cross-document verificato al 100%. Rationale presenti. File TXT e ZIP importati correttamente. Stampa finale: `ESITO SMOKE TEST OPERATIVO: SUCCESSO`. |
| **FALLITO (FAIL)** | **1** | Uno o più controlli obbligatori hanno stato `[FAILED]`, rilevata contaminazione tra documenti, crash, eccezione non gestita, timeout, o JSON non conforme allo schema. Stampa finale: `ESITO SMOKE TEST OPERATIVO: FALLITO`. |
| **NOT_VERIFIED** | **2** | Server LM Studio locale non raggiungibile su `127.0.0.1:1234`. Non costituisce un fallimento del codice, ma indica che l'ambiente live non era attivo per il test. |

---

### 14. Istruzioni per riportare i risultati sul PC di sviluppo
1. Copiare i file di log generati da `/home/nottedeiricercatori/Desktop/LUCA/Tesi/audit_logs_ubuntu/` su chiavetta USB o tramite SCP.
2. Riportare il file di log nel workspace del repository sotto `audit_logs_ubuntu/`.
3. Non cancellare i log originali né alterare i valori di latenza e token contati.
