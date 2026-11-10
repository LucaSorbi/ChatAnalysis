# Runbook Operativo: Esecuzione Benchmark Empirico su LM Studio Locale

Questo documento fornisce la guida procedurale rigorosa per l'esecuzione dei benchmark sperimentali su modelli LLM locali (famiglie **Llama**, **Qwen**, **DeepSeek**) tramite **LM Studio**, destinati all'inclusione nella tesi di laurea.

---

## 1. Vincoli Operativi Fondamentali

1. **Esecuzione Esclusivamente Locale e Loopback**:
   Tutte le chiamate avvengono unicamente su `http://127.0.0.1:1234`. Nessun dato probatorio o sintetico viene trasmesso all'esterno.
2. **One Model at a Time (Gestione Memoria Host)**:
   L'ambiente host di sviluppo dispone di circa 11 GB di RAM di sistema e 1 GB di VRAM GPU. Non caricare mai più di un modello contemporaneamente.
   Scaricare ed eseguire **esclusivamente modelli compatti quantizzati in formato 4-bit (`Q4_K_M`)** tra 1 miliardo e 3 miliardi di parametri (ideale: 1.5B).
3. **Nessun Download Automatico**:
   Il framework non scarica autonomamente file di pesi (GGUF). Il download e caricamento dei pesi deve essere effettuato manualmente dall'utente nell'interfaccia di LM Studio.
4. **Distinzione Chiara tra Fake ed Empirico**:
   - `FAKE_HARNESS_VALIDATION`: Eseguito offline con `FakeLocalLlmClient` per la CI e la validazione dei test unitari (`pytest`). **Non costituisce risultato empirico di tesi**.
   - `REAL_MODEL_BENCHMARK`: Eseguito tramite `ai/experiment.py` con inferenza neurale effettiva sui pesi caricati in LM Studio.

---

## 2. Procedura Passo-Passo

### Passo 1: Avvio Manuale di LM Studio e del Server Locale
1. Aprire l'applicazione **LM Studio** sul computer host.
2. Nella barra laterale sinistra, accedere alla sezione **Local Server** (icona `<->` o "Developer").
3. Assicurarsi che:
   - Porta: `1234`
   - Host: `127.0.0.1` (o `localhost`)
   - Cross-Origin-Resource-Sharing (CORS): abilitato se richiesto.
4. Fare clic su **Start Server**.

### Passo 2: Verifica della Connettività Loopback
Verificare che il server risponda aprendo un terminale PowerShell:
```powershell
curl http://127.0.0.1:1234/v1/models
```
Se il server è attivo, restituirà un JSON con la lista dei modelli correntemente disponibili o caricati:
```json
{
  "data": [
    {
      "id": "qwen2.5-1.5b-instruct",
      "object": "model",
      "owned_by": "organization-owner"
    }
  ],
  "object": "list"
}
```

### Passo 3: Caricamento di UN Modello Quantizzato
Nella scheda **Chat** o **Local Server** di LM Studio, selezionare dal menu a tendina superiore il modello desiderato:
- Per **Qwen**: selezionare un modello quantizzato, es. `qwen2.5-1.5b-instruct` (`Q4_K_M`).
- Attendere che la memoria RAM si stabilizzi (consumo tipico ~1.5 - 2.5 GB).
- Annotare l'identificativo esatto (`id`) mostrato in LM Studio (es. `qwen2.5-1.5b-instruct`).

### Passo 4: Esecuzione del Benchmark con il Runner CLI
Dalla radice del repository `c:\Users\lucas\Desktop\Tesi`, eseguire il runner dedicato:

```powershell
.venv\Scripts\python.exe ai/experiment.py --model-id "qwen2.5-1.5b-instruct" --family QWEN --quantization "Q4_K_M" --parameter-size "1.5B" --timeout 120 --max-tokens 512 --force-run
```

**Note sui parametri di esecuzione**:
- `--timeout 120`: tempo limite in secondi per ciascuna richiesta HTTP verso LM Studio (default: 120.0s). Su CPU lente o dual-core, questo valore può essere incrementato (es. a 180s o 240s) per prevenire `BACKEND_TIMEOUT` durante inferenze prolungate.
- `--max-tokens 512`: limite massimo di token generabili dal modello (default: 512). Fissa un tetto uniforme che rende comparabile la lunghezza e il tempo di generazione fra modelli e famiglie diverse.

Il runner eseguirà in sequenza:
1. Verifica di sicurezza loopback e presenza del modello in `/v1/models`;
2. Validazione della famiglia (`--family`) rispetto al nome del modello (`infer_model_family`);
3. Esecuzione Topic Detection con confronto parallelo su `DIRECT_MULTILINGUAL` e `TRANSLATE_FIRST`;
4. Esecuzione Open Topic Discovery;
5. Calcolo delle metriche di Accuracy, Precision, Recall, F1, Completion Rate, validity rate dei riferimenti probatori e latenze;
6. Salvataggio automatico dei report in `output/`.

### Passo 5: Ripetizione per la Famiglia Llama
1. In LM Studio, espellere (`Eject`) il modello Qwen precedentemente caricato per liberare la RAM.
2. Caricare il modello Llama quantizzato, es. `llama-3.2-1b-instruct` o `llama-3.2-3b-instruct` (`Q4_K_M`).
3. Eseguire il benchmark con la specifica corretta:
```powershell
.venv\Scripts\python.exe ai/experiment.py --model-id "llama-3.2-1b-instruct" --family LLAMA --quantization "Q4_K_M" --parameter-size "1B" --timeout 120 --max-tokens 512 --force-run
```

### Passo 6: Ripetizione per la Famiglia DeepSeek
1. In LM Studio, espellere il modello Llama.
2. Caricare il modello distillato DeepSeek, es. `deepseek-r1-distill-qwen-1.5b` (`Q4_K_M`).
3. Eseguire il benchmark:
```powershell
.venv\Scripts\python.exe ai/experiment.py --model-id "deepseek-r1-distill-qwen-1.5b" --family DEEPSEEK --quantization "Q4_K_M" --parameter-size "1.5B" --timeout 120 --max-tokens 512 --force-run
```


---

## 3. Reperimento e Interpretazione dei Risultati

Tutti i risultati empirici generati vengono salvati nella directory `output/`:
- `output/real_benchmark_<model_id>.json`: Dati analitici completi per ogni query, latenze disaggregate (traduzione vs analisi), e ripartizione per lingua (`it`, `en`, `es`, `mixed`).
- `output/real_benchmark_<model_id>.md`: Tabella riepilogativa formattata in Markdown pronta per l'inclusione nei capitoli sperimentali della tesi.
- `output/real_discovery_<model_id>.json`: Esiti dell'Open Topic Discovery e validità delle evidenze citate.

---

## 4. Troubleshooting e Diagnostica

| Sintomo | Causa Probabile | Azione Correttiva |
| :--- | :--- | :--- |
| `ERRORE: Server LM Studio non disponibile su http://127.0.0.1:1234` | LM Studio non avviato o Local Server spento | Aprire LM Studio, andare su Local Server e premere **Start Server**. |
| `ERRORE: Discrepanza tra la famiglia indicata ... e quella inferita` | Parametro `--family` non coerente con l'identificativo del modello | Correggere `--family` o verificare il nome del modello fornito. |
| `ERRORE: Il modello specificato '...' non risulta tra i modelli caricati` | Errore di battitura nel `--model-id` o modello non caricato | Verificare `http://127.0.0.1:1234/v1/models` e usare l'esatto ID restituito. |
| `AiBackendTimeoutError` (`BACKEND_TIMEOUT`) | Timeout scaduto durante la risposta del modello | Incrementare `--timeout` oppure verificare il carico della GPU/CPU in LM Studio. |
| `AiModelMismatchError` | LM Studio ha risposto usando un alias o un modello diverso | Assicurarsi che in LM Studio sia caricato solo il modello specificato. |
| `AnalysisInputTooLargeError` | La finestra di contesto del modello o il budget di caratteri è stato superato | Ridurre la dimensione massima dei chunk o verificare le impostazioni di contesto in LM Studio. |
| `AiStructuredOutputError` | Il modello ha generato un JSON malformato o citato evidenze allucinate | Normale comportamento sotto test: l'errore viene registrato nell'Error Taxonomy del benchmark per confrontare l'aderenza sintattica tra famiglie di modelli. |

