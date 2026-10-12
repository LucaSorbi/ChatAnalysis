# Verifica della Risoluzione dei Modelli Operativi LM Studio (Fase 16, 17, 18, 19, 20)

## 1. Specifiche Canoniche e Isolamento delle Famiglie
In `ai/lmstudio.py` è stata introdotta la struttura immutabile `OperationalModelSpec`:
- `qwen2.5-7b-instruct`: richiede `("qwen", "2.5", "7b", "instruct")`, proibisce `("deepseek", "qwen3")`;
- `meta-llama-3.1-8b-instruct`: richiede `("llama", "3.1", "8b", "instruct")`, proibisce `("qwen", "deepseek")`;
- `deepseek-r1-distill-qwen-7b`: richiede `("deepseek", "7b")`, proibisce `("llama",)`.

## 2. Esito Test Risoluzione Modelli
```
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0 -- C:\Users\lucas\Desktop\Tesi\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\lucas\Desktop\Tesi
configfile: pyproject.toml
plugins: anyio-4.15.1, cov-7.1.0
collecting ... collected 8 items

tests/unit/test_model_resolution_audit.py::test_model_resolution_strict_family_isolation PASSED [ 12%]
tests/unit/test_model_resolution_audit.py::test_qwen3_never_resolved_as_qwen25 PASSED [ 25%]
tests/unit/test_model_resolution_audit.py::test_deepseek_never_resolved_as_qwen25 PASSED [ 37%]
tests/unit/test_model_resolution_audit.py::test_preferred_quantization_q4_k_m PASSED [ 50%]
tests/unit/test_model_resolution_audit.py::test_ambiguity_detection_without_preferred_quant PASSED [ 62%]
tests/unit/test_model_resolution_audit.py::test_model_equivalence_and_mismatch PASSED [ 75%]
tests/unit/test_model_resolution_audit.py::test_jit_fallback_404_returns_jit_unverified_and_not_loaded PASSED [ 87%]
tests/unit/test_model_resolution_audit.py::test_is_model_loaded_propagates_real_errors PASSED [100%]

============================== 8 passed in 1.01s ==============================
```

## 3. Gestione Equivalenza Model Key vs Instance ID vs Response Model
- L'oggetto `ResolvedOperationalModel` incapsula:
  - `requested_canonical_id`
  - `model_key`
  - `instance_id`
  - `api_identifier`
  - `quantization`
- `is_equivalent_operational_model()` verifica l'identità operativa accettando instance ID derivati ma rifiutando tassativamente modelli di famiglie diverse.
- Per il JIT fallback: in caso di endpoint 404/405/501 il client restituisce `status="jit_unverified"`, `load_mode="jit_unverified"` e non imposta il modello come verificato finché una completion non ha avuto successo.
