# Report Ufficiale di Sviluppo: Final Pre-Pilot AI Integrity Gate & Real-Pilot Readiness

**Data e Ora**: 2026-09-12  
**Repository**: `c:\Users\lucas\Desktop\Tesi`  
**Autore**: Agente di Sviluppo Software  
**Stato della Pipeline**:  
DATI ORIGINALI → IMPORTER → RawRecord → VALIDAZIONE → NORMALIZZAZIONE → ENTITY RESOLUTION → UnifiedMessage → MediaResolver → ResolvedMediaAsset → STT / OCR / Vision → MessageEvidenceBundle → ConversationEvidenceDocument → Local LLM (Topic Detection & Open Topic Discovery)  
**Gate**: FINAL PRE-PILOT AI INTEGRITY GATE — SUPERATO  

---

## 1. FATTO VERIFICATO

### 1.1 Risultati della Suite di Test Completa (Pytest Gate)
La suite completa di test è stata eseguita con l'interprete Python del virtual environment (`.venv\Scripts\python.exe -m pytest`):
```text
================ 880 passed, 1 skipped, 5 deselected in 37.74s ================
```
- **Totale test raccolti**: 886 (incremento rispetto alla baseline precedente di 877 / 850)
- **Passed**: 880 (100% dei test attivi)
- **Failed**: 0
- **Errors**: 0
- **Skipped**: 1 (`tests/unit/test_media_resolver.py::TestPathTraversalSecurity::test_immutable_symlink_attack_rejected`, per assenza del privilegio Windows `SeCreateSymbolicLinkPrivilege` per l'utente non elevato, comportamento standard e atteso su Windows)
- **Deselected**: 5 (smoke test con marker `@pytest.mark.smoke` su server reali o modelli reali, tra cui `test_ai_lmstudio_smoke.py`, esclusi da `addopts = ["-m", "not smoke"]`)
- **Warnings**: 0 warnings bloccanti
- **Durata di esecuzione**: ~37.74s
- **Nuovi test specifici**: Aggiunti 9 nuovi test mirati che coprono `--timeout`, `--max-tokens`, validazione positiva, propagazione end-to-end, rigore `OTHER` family, metriche run-level di discovery ed estrazione esaustiva dei token usage.

### 1.2 Stato Effettivo del Demone LM Studio e Benchmark Reali
- **Stato Server LM Studio**: **UNAVAILABLE** (nessun processo in ascolto su `http://127.0.0.1:1234`, `client.is_available() == False`).
- **REAL MODEL BENCHMARK EXECUTED**: **NO**.
- Nessun modello scaricato su disco, nessun modello caricato in memoria volatile, nessun benchmark empirico reale eseguito su LM Studio.
- Tutti i test eseguiti operano rigidamente su mock e `FakeLocalLlmClient` con `benchmark_mode = "FAKE_HARNESS_VALIDATION"`.

### 1.3 CLI Timeout e Max Tokens con Validazione e Catena di Propagazione Completa
1. **Parametri CLI in `ai/experiment.py`**:
   - `--timeout`: tipo `float`, default `120.0`. Validazione esplicita: `timeout > 0`. Valori `<= 0` vengono rifiutati immediatamente con errore CLI.
   - `--max-tokens`: tipo `int`, default `512`. Validazione esplicita: `max_tokens > 0`. Valori `<= 0` vengono rifiutati immediatamente con errore CLI.
2. **Catena di Propagazione Completa**:
   I parametri vengono propagati lungo l'intera catena reale:
   `CLI (ai/experiment.py)`  
   → `run_synthetic_benchmark(timeout_seconds=args.timeout, max_tokens=args.max_tokens)`  
   → `TopicDetectionAnalyzer.detect_topic(timeout_seconds, max_tokens)` & `EvidenceTranslator.translate_document(timeout_seconds, max_tokens)`  
   → `BaseLocalLlmClient.chat_completion(timeout_seconds, max_tokens)`  
   → `LmStudioClient.chat_completion(timeout_seconds, max_tokens)` (incluso nel payload HTTP `{"max_tokens": max_tokens}` e timeout socket).
   - In Open Topic Discovery la propagazione copre analogamente:
   `CLI` → `run_synthetic_discovery_benchmark` → `TopicDiscoveryAnalyzer.discover_topics` → `chat_completion`.
3. **Registrazione nei Detailed Runs**:
   Ciascun run dettagliato in Topic Detection e Topic Discovery registra il valore di `max_tokens` e `timeout_seconds` effettivamente impiegato.

### 1.4 Tracciamento Esaustivo dei Token di Completamento
In tutti i moduli di analisi locale (`ai/topics.py`, `ai/translation.py`, `ai/benchmark.py`), i metadati dei risultati ora estraggono e memorizzano disaggregati:
- `prompt_tokens`: conteggio dei token di input;
- `completion_tokens`: conteggio dei token di output generati;
- `total_tokens`: totale token consumati;
- `max_tokens`: limite massimo configurato per la specifica chiamata.
- Se il backend non restituisce metadati `usage`, i campi corrispondenti restano rigorosamente `None` senza inventare conteggi fittizi.

### 1.5 Rigore della Famiglia OTHER nel CLI Runner
In `ai/experiment.py`, la verifica della famiglia architetturale impone che:
- Se la famiglia inferita da `infer_model_family(model_id)` è `LLAMA`, `QWEN` o `DEEPSEEK` e differisce da `--family`, l'esecuzione viene bloccata con errore esplicito ed exit code 1.
- Se la famiglia inferita è `OTHER` e il parametro specificato è `--family LLAMA`, `--family QWEN` o `--family DEEPSEEK`, l'esecuzione viene bloccata con exit code 1 con messaggio esplicativo:
  *"Il model_id '...' non consente di verificare automaticamente la famiglia dichiarata '...'. Utilizzare un identificativo riconoscibile oppure dichiarare '--family OTHER'."*
- La combinazione `OTHER` inferito + `--family OTHER` dichiarato è invece consentita e procede regolarmente.

### 1.6 Metrica Run-Level per Open Topic Discovery
In `ai/benchmark.py`, per evitare l'illusione di contare singoli ID allucinati quando il validatore sintattico interrompe l'analisi all'insorgere della prima violazione, la metrica probatoria di Discovery è formalizzata a livello di RUN:
- `invalid_evidence_reference_failure_count`: conteggio dei run falliti con esito `INVALID_EVIDENCE_REFERENCE`;
- `evidence_reference_valid_run_rate`: rapporto tra run completati con riferimenti probatori validi e run tentati (`valid_runs / attempted_runs`);
- Ciascun log di scenario registra il flag booleano `evidence_reference_valid` e i metadati completi dei token (`prompt_tokens`, `completion_tokens`, `total_tokens`).

---

## 2. DECISIONE ARCHITETTURALE

1. **Configurabilità Deterministica del Budget di Generazione (`--max-tokens`)**:
   Fissare uniformemente `max_tokens` (default 512) lungo l'intera catena di invocazione garantisce che il confronto comparativo tra famiglie di modelli (Llama, Qwen, DeepSeek) avvenga con un tetto di risorse identico, evitando che modelli più verbosi falsino i tempi di latenza.
2. **Resilienza ai Timeout su Hardware Eterogeneo (`--timeout`)**:
   L'introduzione del flag configurabile con default a 120.0s permette di adattare il tempo di attesa alla specifica capacità di calcolo dell'host (es. CPU dual-core dell'ambiente di test), prevenendo interruzioni premature classificate come `BACKEND_TIMEOUT`.
3. **Rigore Trasparente nella Classificazione della Famiglia**:
   Impedire l'assegnazione arbitraria di un modello sconosciuto (`OTHER`) a famiglie nobili come `LLAMA` o `QWEN` salvaguarda l'onestà scientifica dei risultati empirici della tesi.
4. **Semantica Run-Level per la Validità dei Riferimenti Probatori**:
   Poiché la validazione forense è rigorosa e solleva un'eccezione `AiStructuredOutputError` al primo `evidence_id` inesistente, contare frazioni di ID all'interno di un payload rifiutato sarebbe metodologicamente scorretto. Il calcolo per run riflette fedelmente il comportamento del sistema.

---

## 3. VALUTAZIONE

- **Integrità del Codice e Robustezza**: 880 test passati su 880 attivi (0 fallimenti, 0 errori). Suite interamente verde.
- **Model Identity Strictness**: 100% implementata e convalidata. Nessun fallback a `target_model`, validazione forma OpenAI completa.
- **Parametri CLI e Propagazione**: `--timeout` e `--max-tokens` integrati con validazione positiva (> 0) e propagati al socket e al payload HTTP.
- **Completezza Token Usage**: Tracciamento completo di `prompt_tokens`, `completion_tokens` e `total_tokens` in Topic Detection, Topic Discovery e Translation.
- **REAL PILOT READY**: **YES**.

---

## 4. PROBLEMA APERTO

1. **Avvio Manuale di LM Studio per i Benchmark Reali**:
   Il server LM Studio non è attualmente in esecuzione. Quando l'operatore intenderà condurre la campagna empirica reale, dovrà:
   - Avviare l'applicazione LM Studio e premere "Start Server" su `http://127.0.0.1:1234`;
   - Caricare un modello quantizzato alla volta (es. Qwen 2.5 1.5B, Llama 3.2 1B, DeepSeek R1 Distill Qwen 1.5B);
   - Eseguire:
     `.venv\Scripts\python.exe ai/experiment.py --model-id "<ID>" --family <FAMIGLIA> --quantization "Q4_K_M" --parameter-size "1.5B" --timeout 120 --max-tokens 512 --force-run`
2. **Benchmark Reale Non Ancora Eseguito**:
   In conformità ai vincoli operativi di questa fase, nessun benchmark reale è stato eseguito (`REAL MODEL BENCHMARK EXECUTED: NO`).
3. **Permesso Windows per Test di Symlink**:
   1 test skipped (`test_immutable_symlink_attack_rejected`) in assenza del privilegio `SeCreateSymbolicLinkPrivilege` per utenti non elevati su Windows.
