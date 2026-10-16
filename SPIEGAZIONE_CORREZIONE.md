# SPIEGAZIONE DELLA CORREZIONE: ISOLAMENTO DETERMINISTICO DEL TEST SMOKE TEST OPERATIVO AI

**File:** `tests/unit/test_smoke_operational_ai.py`  
**Test:** `TestSmokeOperationalAiHardening.test_smoke_test_unreachable_lmstudio_returns_exit_2_not_verified`  
**Data:** 16 Ottobre 2026  

---

## 1. Contesto e Causa Radice del Fallimento su Ubuntu

Nel test `test_smoke_test_unreachable_lmstudio_returns_exit_2_not_verified`, la chiamata applicativa eseguita era:
```python
exit_code = run_smoke_test(use_fake=False)
```
Il test intendeva verificare il comportamento dello Smoke Test quando LM Studio è assente/irraggiungibile (restituzione di exit code `2` e marcatura `NOT_VERIFIED`).

Tuttavia, il test invocava `run_smoke_test(use_fake=False)` senza alcun mock della connessione di rete:
1. Su ambienti di sviluppo dove LM Studio era spento, la chiamata socket falliva e il test passava per puro caso accidentale.
2. Sulla macchina Ubuntu con LM Studio in esecuzione (in ascolto su `http://127.0.0.1:1234`), la connessione HTTP `GET /v1/models` andava a buon fine (`is_available() == True`). Di conseguenza lo smoke test proseguiva con l'esecuzione reale e restituiva exit code `0`, causando:
   ```text
   assert exit_code == 2
   AssertionError: assert 0 == 2
   ```
3. Il test non era deterministico ed effettuava richieste HTTP reali verso la rete locale.

---

## 2. Soluzione Applicata

La correzione è stata applicata **esclusivamente** al metodo di test indicato, senza toccare il codice di produzione né altri test:

1. **Iniezione della fixture `monkeypatch`:**
   È stato aggiunto il parametro `monkeypatch: pytest.MonkeyPatch` alla firma del test.
2. **Mock del Reale Punto di Connessione HTTP:**
   In `ai/lmstudio.py`, tutte le connessioni REST verso LM Studio avvengono tramite `urllib.request.urlopen`. Mediante `monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)`, qualsiasi tentativo di connessione HTTP solleva immediatamente:
   ```python
   urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
   ```
3. **Esecuzione Reale del Codice Applicativo (Nessun Mock del Valore di Ritorno):**
   - Non viene simulato il ritorno di `run_smoke_test()` né quello di `is_available()`.
   - `run_smoke_test` istanzia realmente `LmStudioClient`.
   - `LmStudioClient.is_available()` interroga realmente il suo blocco di gestione `_NETWORK_ERRORS` e rileva la mancata connessione, restituendo `False`.
   - `run_smoke_test` gestisce la mancata disponibilità, stampa il messaggio diagnostico `LIVE LM STUDIO: NOT EXECUTED` e restituisce il codice `2`.
4. **Zero Richieste HTTP Reali e Determinismo 100%:**
   - La chiamata a `urlopen` viene intercettata in memoria senza toccare lo stack di rete del sistema operativo.
   - Il test è ora totalmente deterministico e ha esito positivo (`assert exit_code == 2`) sia su macchine con LM Studio acceso sia con LM Studio spento.

---

## 3. Verifica Rapida Eseguita

1. **Singolo Test Specifico:**
   ```bash
   python -m pytest tests/unit/test_smoke_operational_ai.py::TestSmokeOperationalAiHardening::test_smoke_test_unreachable_lmstudio_returns_exit_2_not_verified -q
   ```
   **Esito:** `1 passed in 1.17s` (Exit Code 0).

2. **Suite Completa del File `test_smoke_operational_ai.py`:**
   ```bash
   python -m pytest tests/unit/test_smoke_operational_ai.py -q
   ```
   **Esito:** `31 passed in 6.98s` (Exit Code 0).
