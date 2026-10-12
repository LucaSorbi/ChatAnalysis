# AUDIT INTEGRATIVO LM STUDIO (`ai/lmstudio.py`)

Analisi tecnica dettagliata del client locale `LmStudioClient` e dei meccanismi di gestione dei modelli AI.

---

## 1. Architettura di Connessione e Sicurezza Forense

* **Base URL**: Default `http://127.0.0.1:1234`.
* **Loopback Validation (`_validate_loopback_url`)**:
  - Verifica vincolante: ammette unicamente host appartenenti a `{"127.0.0.1", "localhost", "::1"}`.
  - Tentativi di connessione a IP remoti, LAN o cloud (es. `api.openai.com`) sollevano immediatamente `ValueError` bloccante.
* **Isolamento da Rete Esterna**: Nessun socket aperto verso l'esterno; libreria standard `urllib.request`.
* **Nessun Auto-Download**: Il sistema interroga solo i modelli fisicamente presenti sul disco locale; se il modello manca, solleva `AiModelNotInstalledError` senza avviare download da HuggingFace o repository esterni.

---

## 2. Payload di Caricamento Modello (`POST /api/v1/models/load`)

### Codice Sorgente Reale (`ai/lmstudio.py:537-542`)
```python
payload = {
    "model": model_id,
    "context_length": context_length,
    "echo_load_config": True,
}
```

### JSON Inviato a LM Studio
```json
{
  "model": "qwen2.5-7b-instruct",
  "context_length": 8192,
  "echo_load_config": true
}
```

### Tabella di Verifica delle Chiavi (Conformità REST Schema v1)
| Chiave JSON | Presente nel Codice Attuale? | Stato / Valutazione |
| :--- | :---: | :--- |
| `model` | **SÌ** | Richiesta da LM Studio |
| `context_length` | **SÌ** | Intero (default: 8192) |
| `echo_load_config` | **SÌ** | Booleano `true` |
| `identifier` | **NO (ASSENTE)** | **Rimosso**: generava HTTP 400 Bad Request |
| `contextLength` | **NO (ASSENTE)** | **Rimosso**: sintassi camelCase non ammessa |
| `gpu_offload` | **NO (ASSENTE)** | **Rimosso**: parametro CLI non supportato da REST API |
| `gpuOffload` | **NO (ASSENTE)** | **Rimosso**: parametro CLI non supportato da REST API |

---

## 3. Risoluzione Deterministica del Modello Locale

La funzione `resolve_operational_model_from_metadata()` (`ai/lmstudio.py:165-245`) adotta la seguente priorità sequenziale:
1. **Verifica istanza già caricata**: se tra i modelli locali un'istanza è già presente in `loaded_instances` e compatibile con `qwen 7b instruct`, viene restituita immediatamente evitando chiamate a `load_model()`.
2. **Filtraggio candidati**: individua i modelli contenenti `'qwen'` E `'7b'` E `'instruct'`.
3. **Preferenza quantizzazione `Q4_K_M`**: se esistono più varianti compatibili e una possiede `Q4_K_M` (nei metadati o nel filename), viene selezionata univocamente.
4. **Univocità**: se esiste un solo candidato valido, restituisce il suo campo `key`.
5. **Divieto Scelta Arbitraria**: se rimangono più candidati indistinguibili, solleva `AiModelAmbiguousError` (vietato l'uso di `models[0]`).
6. **Modello Non Trovato**: se nessun modello compatibile è presente, solleva `AiModelNotInstalledError("Qwen2.5-7B-Instruct non è installato localmente in LM Studio.")`.

---

## 4. Gestione Errori e Chiamata di Inferenza

* **HTTP 400 Bad Request**: Catturato esplicitamente in `load_model()` ed emesso come `AiModelLoadError(status_code=400, technical_details=err_body)`.
* **Server Offline**: Timeout o rifiuto di connessione catturati ed emessi come `LmStudioUnavailableError("LM Studio non raggiungibile su http://127.0.0.1:1234")`.
* **Timeout Operativi**: Caricamento modello e inferenza operano con timeout estesi di 240 secondi (`DEFAULT_OPERATIONAL_TIMEOUT_SECONDS`).
* **Verifica Model Mismatch**: In `detect_topic()`, se il modello effettivo restituito da `response.model` differisce dal `target_model` richiesto, solleva `AiModelMismatchError`.
