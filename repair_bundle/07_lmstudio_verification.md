# VERIFICA LM STUDIO E STATO DEL BACKEND

## Stato di Esecuzione
- **STATO CORRENTE (Macchina di Sviluppo Windows)**: **STATIC / MOCK VERIFIED**
  - Il demone locale LM Studio non è attivo sul loopback 127.0.0.1:1234 durante l'esecuzione del bundle.
  - L'applicazione rileva l'assenza e solleva in modo controllato `LmStudioUnavailableError` senza produrre falsi crash né fingere risposte AI.
  - **LIVE LM STUDIO: NOT EXECUTED** in questa sessione.
  - È stato predisposto e verificato lo script `scripts/smoke_operational_ai.py` per l'esecuzione live sulla workstation Ubuntu con GPU dedicata.

## Specifiche del Lifecycle Operativo
1. **Singolo Ciclo di Vita**: `LocalTopicAnalysisService` prepara il modello una sola volta all'avvio dell'operazione.
2. **Payload /api/v1/models/load**:
   ```json
   {
       "model": "<resolved model key>",
       "context_length": 8192,
       "echo_load_config": true
   }
   ```
3. **Loopback Only**: Connessioni rigidamente confinate a `http://127.0.0.1:1234`. Nessun auto-download di modelli da internet.
