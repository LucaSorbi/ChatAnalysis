# RUNBOOK DEFINITIVO OPERATIVO — PASSAGGIO AD AMBIENTE LAB UBUNTU
# ChatAnalysis Scientific Evidence Guard & Final Ubuntu Handoff

**Specifiche Hardware & Ambiente di Laboratorio:**
- **Host / Sistema Operativo:** Ubuntu 22.04.5 LTS
- **Processore:** Intel Core i5-9600K (6 core @ 3.70 GHz base, 4.60 GHz turbo)
- **RAM di Sistema:** 31 GB DDR4
- **Scheda Grafica:** NVIDIA GeForce GTX TITAN X 12 GB VRAM (Driver NVIDIA 535+ / CUDA compatibile)
- **Interprete Python:** Python 3.12.4 (Virtual Environment `.venv`, **NON Anaconda base**)
- **Server Inferenza Locale:** LM Studio v0.3.x / v0.4.x
- **Percorso Repository sul Target:** `/home/nottedeiricercatori/Desktop/LUCA/Tesi`
- **Endpoint Loopback:** `http://127.0.0.1:1234`

---

## 1. PREPARAZIONE DEL COMMIT CONTROLLATO SU WINDOWS (DA ESEGUIRE MANUALMENTE)

I seguenti comandi Git sono predisposti per consentire all'operatore di eseguire un commit e push controllato e completo.
**Non eseguire autonomamente comandi che alterano il repository remoto senza autorizzazione.**
**Non utilizzare `git add .` indiscriminato: escludere zip di audit, cartelle bundle e file temporanei.**

### 1.1 Ispezione del Working Tree e verifica immutabilità benchmark
```powershell
# 1. Verifica modifiche
git status --short
git diff --name-status

# 2. Verifica che ai/experiment.py e ai/benchmark.py siano 100% identici a HEAD
git diff HEAD -- ai/experiment.py ai/benchmark.py

# 3. Verifica permessi eseguibili su run_benchmark.sh (deve comparire 100755)
git ls-files --stage run_benchmark.sh
```

### 1.2 Staging selettivo e completo di tutti i componenti legittimi del progetto
```powershell
# File di configurazione, build e documentazione
git add .gitattributes
git add MANIFEST.in
git add pyproject.toml
git add README.md
git add UBUNTU_FINAL_SMOKE_RUNBOOK.md
git add docs/agent-report.md

# Package applicativi modificati e nuovi
git add ai/chunking.py ai/lmstudio.py ai/topics.py
git add analysis/__init__.py analysis/consistency.py analysis/models.py analysis/service.py
git add entity_resolution/resolver.py
git add importer/whatsapp_export.py
git add multimodal/__init__.py multimodal/asset_access.py
git add search/topics.py
git add ui/application.py ui/demo.py ui/ingestion.py ui/models.py ui/presentation.py ui/staging.py ui/state.py

# Script operativi e benchmark runner
git add run_benchmark.sh
git add scripts/smoke_operational_ai.py
git add scripts/benchmark_large_ingestion.py

# Fixture e dati sintetici necessari
git add test_data/fixtures/lmstudio/get_models_multi_instance.json
git add test_data/fixtures/lmstudio/get_models_official_qwen.json
git add test_data/fixtures/lmstudio/get_models_unloaded.json
git add test_data/fixtures/lmstudio/post_load_official_response.json
git add test_data/synthetic_smoke/synthetic_whatsapp_chat.txt
git add test_data/synthetic_smoke/synthetic_whatsapp_chat.zip

# Test di integrazione e unitari (esistenti aggiornati e nuovi)
git add tests/integration/test_full_pipeline_e2e.py
git add tests/unit/test_ai_detection_multimodal_diagnostics.py
git add tests/unit/test_ai_lmstudio.py
git add tests/unit/test_analysis_service.py
git add tests/unit/test_apptest_flows.py
git add tests/unit/test_architecture_guard.py
git add tests/unit/test_discovery_detection_invariants.py
git add tests/unit/test_discovery_provenance_hardening.py
git add tests/unit/test_discovery_provenance_service.py
git add tests/unit/test_large_file_upload.py
git add tests/unit/test_large_pipeline_bounded.py
git add tests/unit/test_lmstudio_binding_lifecycle.py
git add tests/unit/test_lmstudio_model_loading.py
git add tests/unit/test_lmstudio_native_integration.py
git add tests/unit/test_media_asset_access.py
git add tests/unit/test_media_processing_lifecycle.py
git add tests/unit/test_media_registry_session_isolation.py
git add tests/unit/test_model_resolution_audit.py
git add tests/unit/test_operational_model_management.py
git add tests/unit/test_packaging_release.py
git add tests/unit/test_search_topics.py
git add tests/unit/test_smoke_operational_ai.py
git add tests/unit/test_staging_and_large_file.py
git add tests/unit/test_ui_manual_topic_detection.py
git add tests/unit/test_ui_smoke.py
git add tests/unit/test_ui_state.py
git add tests/unit/test_whatsapp_strict_encoding.py
git add tests/unit/test_zip_security_hardening.py

# NOTA FONDAMENTALE DI ESCLUSIONE:
# NON includere archivi ZIP di audit (*.zip)
# NON includere cartelle temporanee di bundle (*_bundle/, *_audit/)
# NON includere file di log temporanei (*.log, *.tmp)
```

### 1.3 Commit controllato e push
```powershell
git commit -m "feat(chatanalysis): complete self-sufficient application release and Ubuntu handoff"
git push origin main
```

---

## 2. PROCEDURA OPERATIVA CRONOLOGICA SU UBUNTU (PASSI 1 - 16)

Tutti i passaggi seguenti vanno eseguiti sulla macchina Ubuntu del laboratorio.

### Passo 1: Verifica dello stato Git locale prima del pull (Sicurezza Non Distruttiva)
```bash
cd /home/nottedeiricercatori/Desktop/LUCA/Tesi

# 1. Verifica preventiva dello stato locale
git status --short

# Se esistono modifiche locali non committate:
# NON USARE git reset --hard!
# NON ELIMINARE file senza autorizzazione!
# Salvare le eventuali modifiche su un branch di backup o stash se necessario:
# git stash save "backup_prima_di_pull_$(date +%Y%m%d_%H%M%S)"
```

### Passo 2: Aggiornamento del repository locale allo stesso commit di Windows
```bash
# Fetch e fast-forward pull
git fetch origin
git pull origin main

# Verifica commit hash: DEVE corrispondere esattamente al commit generato su Windows
git rev-parse HEAD
git log -n 1 --oneline
```

### Passo 3: Verifica del working tree pulito su Ubuntu
```bash
test -z "$(git status --porcelain)" && echo "WORKING TREE PULITO: OK" || (echo "ATTENZIONE: WORKING TREE NON PULITO!" && exit 1)
```

### Passo 4: Verifica permessi eseguibili e fine riga di run_benchmark.sh
```bash
# Verifica permessi 100755
ls -la run_benchmark.sh

# Verifica fine riga LF (nessun byte CRLF)
file run_benchmark.sh | grep -v "CRLF" && echo "LINE ENDINGS LF: OK"

# Verifica sintassi Bash nativa
bash -n run_benchmark.sh && echo "BASH SYNTAX: OK"
```

### Passo 5: Attivazione dell'ambiente virtuale `.venv` (Zero Anaconda)
```bash
# Disattivare eventuale ambiente conda base
conda deactivate 2>/dev/null || true

# Attivare l'ambiente virtuale dedicato
source .venv/bin/activate

# Verificare che l'interprete sia isolato e punti a Python 3.12.4
which python3
python3 --version
```
L'output deve confermare `/home/nottedeiricercatori/Desktop/LUCA/Tesi/.venv/bin/python3`.

### Passo 6: Esecuzione preliminare della suite di test su Ubuntu
Prima di avviare LM Studio, verificare che tutti i test unitari e di integrazione passino:
```bash
pytest -v
```
Tutti i test devono risultare `PASSED` con Exit Code 0.

### Passo 7: Avvio di LM Studio locale e configurazione Hardware
1. Avviare LM Studio su Ubuntu.
2. Caricare il modello: `qwen2.5-7b-instruct` (o file `Qwen2.5-7B-Instruct-GGUF` con quantizzazione `Q4_K_M`).
3. **GPU Offload:** Massimizzare l'offload sui 12 GB VRAM della NVIDIA GTX TITAN X.
4. **Context Length:** Impostare esplicitamente **`8192`** token nel pannello di caricamento o nella configurazione del modello.
5. Avviare il **Local Server** sulla porta standard `1234` (binding su `127.0.0.1`).

### Passo 8: Ispezione preliminare delle API LM Studio
Verificare l'esposizione del modello, della quantizzazione e del context tramite curl:
```bash
# Verifica endpoint standard OpenAI
curl -s http://127.0.0.1:1234/v1/models | jq .

# Verifica endpoint nativo LM Studio (quantizzazione e istanze caricate)
curl -s http://127.0.0.1:1234/api/v1/models | jq '.models[] | {key: .key, quantization: .quantization, instances: .loaded_instances}'
```
Controllare che:
- `key` o `id` contenga `qwen2.5-7b-instruct`.
- `quantization` sia un oggetto con `"name": "Q4_K_M"` (o stringa `"Q4_K_M"`).
- `loaded_instances` contenga l'istanza con `config.context_length: 8192`.

### Passo 9: Esecuzione dello Smoke Test Operativo Rigoroso (LIVE)
Eseguire lo smoke test live catturando l'exit code in modo deterministico con `pipefail`:
```bash
mkdir -p audit_logs_ubuntu
set -o pipefail

LOGFILE="audit_logs_ubuntu/smoke_live_qwen_$(date +%Y%m%d_%H%M%S).log"

python3 scripts/smoke_operational_ai.py \
  --model qwen2.5-7b-instruct \
  --required-quantization Q4_K_M \
  --expected-context-length 8192 \
  2>&1 | tee "$LOGFILE"

rc=${PIPESTATUS[0]}
echo "SMOKE EXIT CODE: $rc"
test "$rc" -eq 0
```
*Se è necessario specificare l'instance ID esatto:*
```bash
python3 scripts/smoke_operational_ai.py \
  --model qwen2.5-7b-instruct \
  --target-instance-id <INSTANCE_ID> \
  --required-quantization Q4_K_M \
  --expected-context-length 8192 \
  2>&1 | tee "$LOGFILE"
```

### Passo 10: Interpretazione dei Criteri di Certificazione (Scientific Evidence Guard)
- **Exit Code 0 (`LIVE_VERIFIED`):**
  - LM Studio ha risposto in loopback.
  - Quantizzazione `Q4_K_M` confermata dal server (`CHECK_MODEL_QUANTIZATION: PASSED`).
  - Context length `8192` confermato dal server (`CHECK_CONFIRMED_CONTEXT_LENGTH: PASSED`).
  - Discovery e Detection eseguite con successo con client unificato e binding verificato.
  - Tutti i controlli `[PASSED]`.
- **Exit Code 2 (`NOT_VERIFIED`):**
  - Server non raggiungibile OPPURE metadati di quantizzazione/contesto non esposti dall'API del server.
  - *Regola Scientific Evidence Guard:* Un semplice flag CLI manuale (`--confirmed-quantization` o `--confirmed-context`) **NON promuove mai automaticamente il controllo a PASSED**. Il gate scientifico automatico rimane `NOT_VERIFIED`.
- **Exit Code 1 (`FAILED`):**
  - Il modello caricato ha quantizzazione errata (es. `Q8_0`), o context errato (es. `4096`), o target instance inesistente, o errore operativo.
  - *Nessuna dichiarazione operatore può sovrascrivere o sanare un mismatch dichiarato dal server.*

### Passo 11: Verifica Topic Discovery reale (Fase 1)
Verificare nei log:
- `discovery_response_model` corrisponde al modello caricato.
- Generato almeno 1 topic con etichetta coerente in lingua italiana.
- Run registrata con `run_id` immutabile nello store documentale.

### Passo 12: Verifica Topic Detection indipendente (Fase 2)
Verificare nei log:
- Seconda inferenza eseguita in modo completamente indipendente.
- La prompt include solo `label` e `short_description` (nessun suggerimento di evidence IDs).
- Preservato `rationale` non vuoto e latenza reale.
- **Valutazione della Consistenza:** Registrare la classificazione di consistenza calcolata dal `ConsistencyService`. Un esito di discrepanza scientificamente motivato non costituisce automaticamente un guasto dell'infrastruttura software, ma un fenomeno empirico di allineamento del modello da documentare fedelmente nei risultati della tesi.

### Passo 13: Verifica Caso di Controllo "Logistica e Ispezione Depositi" (Fase 3)
- **Topic:** `Logistica e Ispezione Depositi`
- **Descrizione:** `Ispezione visiva del magazzino merci con casse sigillate e sicurezza`
- **Evidenza di riferimento:** `msg::5::VISION_DESCRIPTION`
- **Regola empirica:** La Topic Detection interroga realmente Qwen2.5.
  - Se risponde `PRESENT`: verificare che citi `msg::5::VISION_DESCRIPTION` e rationale.
  - Se risponde `ABSENT`: **NON forzare a PRESENT e NON considerare il test fallito**. È un risultato empirico valido del modello da documentare fedelmente nella tesi.

### Passo 14: Verifica Ingestion Reale WhatsApp TXT e ZIP (Fasi 5 e 7)
Verificare nei log:
- Ingestion reale di `synthetic_whatsapp_chat.txt` (`status=SUCCESS`, 4 record, 4 messaggi unificati).
- Ingestion reale di `synthetic_whatsapp_chat.zip` (`status=SUCCESS`).
- Discovery ed evidenze generate sul documento reale.
- **Zero leak** tra documento demo e documento reale importato (`CHECK_CROSS_DOCUMENT_ISOLATION: PASSED`).

### Passo 15: Calcolo SHA256 dei Log e Archiviazione
```bash
sha256sum "$LOGFILE" > "${LOGFILE}.sha256"
cat "${LOGFILE}.sha256"
```

---

## 3. BENCHMARK SCIENTIFICO CONGELATO (GATE FINALE CONTROLLATO)

**NON AVVIARE I 9 BENCHMARK AUTOMATICAMENTE SOLO PERCHÉ LO SMOKE HA DATO ESITO 0.**

Prima di avviare il benchmark scientifico:
1. Verificare che il commit hash corrente sia quello congelato e approvato.
2. Verificare che i file congelati (`ai/experiment.py`, `ai/benchmark.py`, `test_data/ai_benchmark/`) abbiano zero diff e hash conformi.
3. Verificare la disponibilità dei tre modelli finali su LM Studio:
   - `qwen2.5-7b-instruct` (Q4_K_M)
   - `meta-llama-3.1-8b-instruct` (Q4_K_M)
   - `deepseek-r1-distill-qwen-7b` (Q4_K_M)
4. Ottenere l'esplicita approvazione dell'operatore/supervisore.

Solo dopo il soddisfacimento dei 4 punti sopra, avviare il benchmark:
```bash
bash run_benchmark.sh
```

