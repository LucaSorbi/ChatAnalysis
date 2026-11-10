# GUIDA DI CALIBRAZIONE E BENCHMARK LIVE SU SERVER UBUNTU (GTX TITAN X)

## 1. Obiettivo e Distinzione Metodologica

Questo documento definisce la procedura operativa rigorosa per l'esecuzione di benchmark **LIVE** con LM Studio su ambiente di calcolo reale (workstation/server Ubuntu equipaggiato con GPU NVIDIA GTX TITAN X Maxwell 12GB).

In conformità ai requisiti dell'audit scientifico, è fatto divieto assoluto di presentare stime analitiche, simulazioni offline o test basati su mock come misurazioni reali dell'hardware. La tassonomia obbligatoria delle misurazioni è:

1. **TEST CON MOCK**: Esecuzione deterministica con risposte pre-configurate (pytest standard su Windows/Linux per verifica logica e contratti).
2. **SIMULAZIONI OFFLINE**: Calcolo teorico o riproduzione locale senza runtime LLM in esecuzione.
3. **STIME ANALITICHE**: Proiezioni matematiche basate su token attesi e throughput ipotetico.
4. **MISURAZIONI EFFETTIVE (LIVE_VERIFIED)**: Misure strumentali ottenute con server LM Studio attivo, modello caricato su VRAM reale e richieste HTTP/JSON reali su loopback.

---

## 2. Configurazione Hardware e Software di Riferimento

- **OS**: Ubuntu 22.04 LTS / 24.04 LTS (x86_64)
- **GPU**: NVIDIA GeForce GTX TITAN X (12 GB GDDR5, Maxwell GM200)
- **Driver NVIDIA**: Version >= 535.183.01
- **CUDA Toolkit**: 12.2 o 12.4
- **Runtime LLM**: LM Studio Local Server (Linux headless o UI) v0.3.x in ascolto su `http://127.0.0.1:1234/v1`
- **Modello di Benchmark**: `qwen2.5-7b-instruct` (oppure `llama-3.1-8b-instruct`) quantizzato `Q4_K_M`
- **Contesto Operativo**: 8192 token (GPU Offload: 100% dei layer su VRAM da 12GB)
- **Temperatura**: `0.0` (deterministica)
- **Seed**: `42`

---

## 3. Metriche Obbligatorie da Acquisire

Durante ogni sessione LIVE devono essere registrate le seguenti 8 metriche:

| Metrica | Unità | Strumento di Misura | Descrizione |
| :--- | :---: | :--- | :--- |
| **Prefill Speed** | token/s | Log LM Studio / API metrics | Velocità di elaborazione del prompt iniziale |
| **Generation Speed** | token/s | Log LM Studio / API metrics | Throughput di generazione dei token di output |
| **Richieste al Minuto** | req/min | `AnalysisProgressTracker` | Throughput complessivo di transazioni HTTP completate |
| **Tempo Medio per Chunk**| s/chunk | Timer applicativo / Tracker | Latenza end-to-end per ciascun blocco analizzato |
| **Tempo Totale Run** | s o min | Timer end-to-end | Durata dell'intera analisi dall'avvio al consolidamento |
| **Memoria VRAM Allocata** | MB | `nvidia-smi --query-gpu=memory.used` | Picco e regime di occupazione della memoria grafica |
| **Errori e Retry** | conteggio | `AnalysisProgressTracker` | Errori HTTP 400, timeout, token overflow o parsing JSON |
| **Copertura Evidenze** | % | Validatore forense | Percentuale di evidenze citate rispetto all'oro documentale |

---

## 4. Procedura di Avvio e Verifica Pre-Flight

### Passo 1: Verifica GPU e Driver
```bash
nvidia-smi
```
Verificare che la GPU GTX TITAN X sia riconosciuta, che la memoria libera sia >= 11500 MB e che non vi siano processi zombi in esecuzione.

### Passo 2: Avvio LM Studio Server
Avviare il server locale LM Studio con binding su loopback:
```bash
lms server start --port 1234
```
oppure, se si usa l'interfaccia grafica:
1. Aprire LM Studio.
2. Selezionare la tab "Local Server" (`<->`).
3. Verificare che la porta sia `1234`.
4. Caricare il modello `qwen2.5-7b-instruct-q4_k_m`.
5. Impostare `Context Length` a `8192`.
6. Impostare `GPU Offload` al massimo (tutti i layer su GPU).

### Passo 3: Verifica Connessione e Introspezione Modello
```bash
curl http://127.0.0.1:1234/v1/models
```
Il JSON di risposta deve elencare il modello caricato.

---

## 5. Script di Calibrazione e Misurazione Live

È disponibile uno script di calibrazione dedicato: `scripts/measure_scalability_and_profiling.py` che supporta la modalità `--live-lmstudio`:

```bash
# Esecuzione calibrazione live
python3 scripts/measure_scalability_and_profiling.py --live-lmstudio --model qwen2.5-7b-instruct --samples 3
```

Lo script:
1. Rileva se LM Studio è attivo su `http://127.0.0.1:1234/v1`. Se non risponde, rifiuta categoricamente di emettere etichette `LIVE_VERIFIED` e opera esclusivamente in modalità simulazione offline chiaramente dichiarata.
2. Invia chunk controllati con i tre profili (`PRUDENT`: 2500, `BALANCED`: 4200, `PERFORMANCE`: 5200 token).
3. Monitora in tempo reale la VRAM tramite subprocess `nvidia-smi`.
4. Misura i tempi di prefill e generation tramite i metadati restituiti da LM Studio (`usage.prompt_tokens_details` e latency).
5. Verifica la validità degli output strutturati JSON e l'integrità delle evidenze forensi.
6. Salva i log completi e le tabelle comparative in `docs/SCALABILITY_MEASUREMENTS_REPORT.md`.

---

## 6. Monitoraggio VRAM in Background

Per raccogliere il profilo continuo di memoria grafica durante un run lungo:
```bash
nvidia-smi --query-gpu=timestamp,memory.used,memory.free,utilization.gpu,temperature.gpu \
  --format=csv -l 1 > vram_profile_live.csv &
VRAM_PID=$!

# Esegui la suite o l'analisi
python3 -m pytest tests/unit/test_operational_integration_audit.py

# Al termine, interrompi il monitoraggio
kill $VRAM_PID
```

---

## 7. Criteri di Accettazione per Dichiarazione "LIVE_VERIFIED"

Una misurazione può recare il tag `LIVE_VERIFIED` nella documentazione o nei report scientifici solo ed esclusivamente se:
1. Il runtime era LM Studio reale su GPU reale.
2. L'indirizzo del server era `127.0.0.1:1234`.
3. Tutti i chunk hanno transitato come richieste HTTP POST reali con risposta JSON validata.
4. L'invariante contabile `requests_sent = requests_successful + requests_failed + requests_in_flight` è stato verificato al 100%.
5. Nessuna eccezione non gestita è stata silenziata.
