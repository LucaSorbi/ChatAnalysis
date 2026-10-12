# BLOCKER 1 — LM Studio Model Loading Lifecycle

## 1. Problema Originale
Nell'audit precedente `load_model()` in `ai/lmstudio.py` considerava riuscito un caricamento anche quando:
- La risposta HTTP 200 conteneva payload di errore (es. `{"status": "failed", "error": "Not enough VRAM"}`), restituendo falsamente lo stato `native_loaded` o `loaded`.
- La risposta HTTP 200 presentava body non-JSON (HTML/error text), attribuendo lo stato `loaded`.
- La risoluzione dei modelli rischiava di confondere famiglie distinte tramite substring matching (`deepseek-r1-distill-qwen-7b` confuso con Qwen, oppure `qwen3` accettato come `qwen2.5-7b-instruct`).

## 2. Causa Radice
Assenza di un modello di ciclo di vita tipizzato del modello: il codice si fidava del mero HTTP status code 200 senza validare la struttura semantica del payload JSON restituito da LM Studio, memorizzando prematuramente `_last_loaded_model`.

## 3. Decisione Architetturale
Progettazione di un lifecycle formale esplicito:
```text
NOT_LOADED → LOADING → LOADED / ALREADY_LOADED / JIT_UNVERIFIED / FAILED
```
- Introduzione dell'enum `ModelLifecycleStatus` e del dataclass immutabile tipizzato `ModelLoadResult` con campi: `status`, `requested_canonical_id`, `resolved_model_key`, `loaded_instance_id`, `api_identifier`, `context_length`, `load_mode`, `error_message`.
- HTTP 200 non equivale a caricamento: parsing rigoroso del body JSON; se `status == "failed"`, solleva `AiModelLoadError(status_code=200)`.
- Body vuoto o JSON malformato solleva `AiBackendProtocolError`.
- HTTP 404 / 405 / 501 entra nello stato controllato `JIT_UNVERIFIED` senza aggiornare `_last_loaded_model`.
- Verifica del binding del modello alla prima completion in modalità `JIT_UNVERIFIED`: se `response.model` non è conforme a `is_equivalent_operational_model`, solleva `AiModelMismatchError`.
- Separazione rigorosa delle famiglie: `is_equivalent_operational_model` proibisce esplicitamente equivalenze tra DeepSeek e Qwen2.5, e tra Qwen3 e Qwen2.5.

## 4. File Modificati
- `ai/lmstudio.py`: Riprogettazione di `load_model`, lifecycle, `ModelLifecycleStatus`, `ModelLoadResult`, gestione JIT e controllo binding.
- `tests/unit/test_lmstudio_model_loading.py`: Nuova suite esaustiva di 14 unit test dedicati.

## 5. Test Aggiunti ed Esiti
- `test_load_model_http_200_success`: PASSED
- `test_load_model_http_200_failure_payload_raises_error`: PASSED
- `test_load_model_http_200_malformed_json_raises_protocol_error`: PASSED
- `test_load_model_http_200_empty_body_raises_protocol_error`: PASSED
- `test_load_model_http_400_raises_model_load_error`: PASSED
- `test_load_model_http_404_returns_jit_unverified_without_loaded_state`: PASSED
- `test_load_model_http_405_returns_jit_unverified`: PASSED
- `test_load_model_http_500_raises_model_load_error`: PASSED
- `test_load_model_timeout_raises_timeout_error`: PASSED
- `test_load_model_connection_refused_raises_unavailable_error`: PASSED
- `test_jit_completion_verifies_model_binding`: PASSED
- `test_jit_completion_wrong_model_raises_model_mismatch`: PASSED
- `test_deepseek_and_qwen_strict_separation`: PASSED
- `test_qwen3_and_qwen25_strict_separation`: PASSED

## 6. Limitazioni Residue e Stato di Verifica
- **MOCK_VERIFIED**: Tutta la logica di lifecycle, errori tipizzati e binding è verificata deterministicamente via mock.
- **NOT_VERIFIED (Live LM Studio)**: Sulla workstation corrente LM Studio non è attivo; lo script `scripts/smoke_operational_ai.py` è predisposto per l'esecuzione live sul server Ubuntu target.
