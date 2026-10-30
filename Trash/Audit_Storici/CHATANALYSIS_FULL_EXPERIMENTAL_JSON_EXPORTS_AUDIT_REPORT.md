# CHATANALYSIS — RAPPORTO TECNICO DI AUDIT E CONFORMITÀ SCIENTIFICA
## Correzione Mirata Export Scientifico JSON, Bug DeepSeek NameError e Stato F1

**Data Audit:** 21 Ottobre 2026  
**Autore:** Luca Sorbi  
**Ambiente di Verifica:** Windows 11 Pro (OS Build 26100), Python 3.14.7 (64-bit), pytest 9.1.1  
**Oggetto:** Correzione difetti comprovati dell'export scientifico JSON, risoluzione NameError benchmark DeepSeek, tracciabilità F1 Linux  
**Versione Schema JSON:** `1.1.0`  
**Archivio Finale di Consegna:** `chatAnalysis_json_export_truthfulness_deepseek_f1_fix_audit_20261021.zip`  
**Collocazione Consegna:** Direttamente all'ESTERNO della cartella `Tesi` e FUORI dal repository Git (`../`)  
**Esito Complessivo Suite Export & Regressioni:** **32/32 TEST PASSATI (100% SUCCESSO, EXIT CODE 0)**  

---

## 1. RIEPILOGO ESECUTIVO DEI DIFETTI RISCONTRATI E RISOLTI

A seguito dell'audit indipendente del 21 ottobre 2026, sono stati analizzati e corretti chirurgicamente i difetti comprovati dell'esportazione sperimentale e il blocco programmatico del benchmark DeepSeek:

| Priorità | Area / Componente | Difetto Rilevato dall'Audit | Correzione Applicata | Stato di Verifica |
|---|---|---|---|---|
| **P0** | `ai/lmstudio.py` | `NameError: name 'AiStructuredOutputError' is not defined` con `finish_reason='length'` durante la traduzione `TRANSLATE_FIRST`. | Aggiunta di `AiStructuredOutputError` all'import da `ai.backend`. Nessuna alterazione ai prompt, budget token, quantizzazioni o algoritmi. | `TEST_VERIFIED` (Mock & Regressione) |
| **P0** | `analysis/export.py` | Run prive di origine autenticata promosse indebitamente a `LIVE_LM_STUDIO` / `LIVE_AUTHORITATIVE`. | Modello Fail-Closed: assenza di origin -> `origin = "NOT_RECORDED"`, `attestation_level = "UNATTESTED"`. Attestazione LIVE subordinata ad autenticazione verificata. | `TEST_VERIFIED` (Sintetico) |
| **P0** | `analysis/export.py` | Fabbricazione di parametri non osservati (Qwen, 8192, 512, 240, 0.0, BALANCED, durate 0.0 invece di null, piani 1/1). | Parametri e durate non misurate serializzati rigorosamente come `null`. Durata `0.0` denota esclusivamente misura reale zero. | `TEST_VERIFIED` (Sintetico) |
| **P0** | `analysis/export.py` | Contatori incoerenti (es. 1 planned, 1 completed, 2 failed) esportati con 100% completamento e COMPLETE. | Validazione invarianti di copertura: se la somma dei blocchi contraddice il piano, segnala `counter_invariants_valid = False` e `coverage_status = "INCONSISTENT_COUNTERS"`. | `TEST_VERIFIED` (Sintetico) |
| **P0** | `analysis/export.py`, `ui/` | Privacy non Safe by Default: leak di identificatori personali (`Alice Rossi`, email, telefoni, percorsi) in assenza di toggle esplicito. | Modalità pubblicabile (`SAFE_REDACTED`) impostata come **default vincolante** su UI, export storici, search e modulo base. Pseudonimizzazione di tutti gli evidence ID ed eliminazione testi liberi. | `TEST_VERIFIED` (Adversarial) |
| **P0** | `analysis/export.py` | Dichiarazione errata `SHA256_SALTED_TRUNCATED` pur usando hash non salato. | Dichiarazione trasparente e veritiera: `"SHA256_UNSALTED_TRUNCATED"` con documentazione dei limiti di pre-immagine. | `STATICALLY_VERIFIED` |
| **P0** | `ui/application.py` | `export_topic_detection_json` associava `ConsistencyResult` anche solo per coincidenza di topic label, causando cross-contamination. | Rimosso il fallback sulla sola label. L'associazione avviene **esclusivamente per detection_run_id esatto**. | `TEST_VERIFIED` (Sintetico) |
| **P0** | `analysis/export.py` | Confusione tra Git HEAD dell'export e commit originario di esecuzione storica. | Separazione esplicita tra `execution_software_commit` ed `export_software_commit`. | `TEST_VERIFIED` (Sintetico) |
| **P1** | `analysis/checkpoint.py` | Regressioni Linux F1 (test_122, 136, 137, 138, 144, 151) e residui staging `/tmp/chatanalysis_forensic_...`. | Ambiente locale Windows (nessuna simulazione POSIX fittizia). F1 dichiarato trasparentemente come debito tecnico aperto (`NOT_VERIFIED`). | `NOT_VERIFIED` (Ambiente Linux richiesto) |

---

## 2. RISOLUZIONE DETTAGLIATA PRIORITÀ P0

### 2.1 Bug DeepSeek NameError in `ai/lmstudio.py`
- **Sintomo:** Il 21 ottobre 2026, eseguendo `./run_benchmark.sh run1 deepseek` su Ubuntu, LM Studio ha restituito un JSON troncato per raggiungimento del budget di token (`finish_reason='length'`). L'eccezione interna `json.decoder.JSONDecodeError` doveva essere incapsulata in `AiStructuredOutputError`, ma ha sollevato `NameError: name 'AiStructuredOutputError' is not defined` interrompendo l'intero benchmark.
- **Causa Accertata:** In [ai/lmstudio.py](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/ai/lmstudio.py), la riga 2111 utilizzava `AiStructuredOutputError`, ma la classe non era inclusa nell'elenco di import `from ai.backend import (...)` (righe 34-46).
- **Correzione Chirurgica Minima:** Aggiunta `AiStructuredOutputError` all'import da `ai.backend` in [ai/lmstudio.py](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/ai/lmstudio.py).
- **Nessuna Alterazione Scientifica:** Non sono stati modificati i budget `max_tokens=512`, `context=8192`, `timeout=240`, `temperature=0.0`, le quantizzazioni `Q4_K_M`, i prompt, i ground truth o le strategie `TRANSLATE_FIRST` / `DIRECT_MULTILINGUAL`.
- **Verifica Automatizzata:** Test di regressione [tests/unit/test_analysis_export.py:TestP0ScientificTruthfulnessAndAuditFixes::test_acceptance_A_deepseek_named_error_regression_finish_reason_length](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/tests/unit/test_analysis_export.py). Con risposta HTTP simulata avente `finish_reason='length'` e JSON tronco, il client solleva `AiStructuredOutputError`, non `NameError`. La funzione `_classify_benchmark_error` registra l'esito come `STRUCTURED_OUTPUT_INVALID` senza causare il crash della suite, mentre bug imprevisti (es. `AttributeError`) continuano a propagare.

### 2.2 Provenance Fail-Closed e Autenticazione delle Run
- In [analysis/export.py](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/analysis/export.py), se il metadato `origin` è assente o vuoto, il report imposta:
  ```python
  origin = "NOT_RECORDED"
  attestation_level = "UNATTESTED"
  ```
- È fatto divieto di dedurre `LIVE_AUTHORITATIVE` dall'assenza del flag mock o dalla presenza del nome di un modello.
- Per run recuperate da checkpoint locale, la modalità di caricamento (`loading_mode = CHECKPOINT_RESTORE`) è disaccoppiata dall'origine originaria dell'inferenza.

### 2.3 Rifiuto di Valori Fittizi e Parametri Inventati
- I campi `temperature`, `max_tokens`, `timeout_seconds`, `declared_context_length`, `effective_context_length`, `operational_profile_requested` e `operational_profile_applied` acquisiscono esclusivamente i valori registrati nell'oggetto metadata reale. Se non disponibili, sono serializzati come `null` (`None`).
- La durata complessiva e le durate dei chunk non misurate sono serializzate come `null`, non `0.0`.
- I blocchi pianificati non avviati vengono registrati come `status = "UNATTEMPTED"`, `attempts = 0`, `duration_seconds = null`, senza simulare esecuzioni riuscite.
- Validazione delle invarianti: se `completed + failed + skipped != planned`, il report dichiara `counter_invariants_valid = False` e `coverage_status = "INCONSISTENT_COUNTERS"`, senza calcolare percentuali di completamento artefatte.

### 2.4 Privacy Safe by Default e Protezione Identificatori
- **Default Applicativo:** Il parametro `redact_sensitive_topics` è impostato a `True` come valore predefinito in tutte le funzioni applicative (`export_topic_detection_json`, `export_topic_discovery_json`, checkbox UI, export da storico ed export da search).
- **Oscuramento Rigoroso:** In modalità Safe, vengono oscurati:
  - `topic.label`, `topic.description`, `topic.topic_id`
  - `result.rationale`, `result.consistency_rationale`
  - `document.source_name` (restituisce pseudonimo `src_...`)
  - `chunk_diagnostics.error_detail` (blocca qualsiasi testo contenente tracce conversazionali o percorsi)
- **Pseudonimizzazione Evidenze:** Tutti gli `evidence_id` non corrispondenti ad alias numerici generici standard (es. `E001`) vengono convertiti in pseudonimi stabili `ev_<hash>`.
- **Nomi File Sicuri:** La funzione `generate_export_filename` utilizza esclusivamente lo pseudonimo del dataset (`chat_...`), evitando la dispersione di percorsi o chat ID grezzi nel filesystem.
- **Trasparenza Crittografica:** Lo schema e il report dichiarano onestamente `"pseudonymization_algorithm": "SHA256_UNSALTED_TRUNCATED"`, documentando che in domini a bassa entropia sussiste vulnerabilità a dizionario se l'input space è ridotto.

### 2.5 Correlazione Univoca tra Discovery e Detection
- In [ui/application.py](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/ui/application.py), è stato eliminato il criterio permissivo `or c.topic_label == ...`.
- L'associazione a `ConsistencyResult` richiede ora la **coincidenza esatta di `detection_run_id`**. Se non vi è corrispondenza univoca, la sezione semantica rimane `null`.
- Il flag `inferential_independence_verified` non viene più impostato arbitrariamente a `True`: assume valore `True` solo se vi è evidenza documentata di doppia inferenza disgiunta, altrimenti `null`.

---

## 3. STATO PRIORITÀ P1 — REGRESSIONI F1 LINUX

In ossequio al principio di verità scientifica:
- Il presente ambiente di esecuzione è **Windows 11** (`os.name == "nt"`).
- Le sei regressioni documentate su Linux (`test_122`, `test_136`, `test_137`, `test_138`, `test_144`, `test_151`) riguardano la concorrenza a livello di inode POSIX, le race conditions su `unlink/lstat` e i file temporanei in `/tmp/chatanalysis_forensic_...`.
- Tali condizioni **non possono essere verificate o simulate lealmente su filesystem NTFS/Windows**.
- **Dichiarazione formale:** Lo stato di F1 rimane **APERTO come debito tecnico e classificato come `NOT_VERIFIED (Linux environment required)`**. Non viene rilasciata alcuna falsa attestazione di superamento di tali test fino all'esecuzione nell'ambiente Linux di destinazione.

---

## 4. MATRICE COMPLETA DEI TEST E STATO DI VERIFICA

La suite include 32 test unitari e di integrazione (23 test obbligatori della specifica iniziale + 9 test vincolanti P0 dell'audit del 21 ottobre):

```
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\lucas\Desktop\CartellaTesi\Tesi, configfile: pyproject.toml
collected 32 items

tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_01_detection_complete_1of1_present PASSED [  3%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_02_detection_complete_absent PASSED [  6%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_03_detection_complete_uncertain PASSED [  9%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_04_detection_partial_17of26_uncertain PASSED [ 12%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_05_detection_partial_with_present_validated PASSED [ 15%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_06_discovery_complete_1of1 PASSED [ 18%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_07_discovery_complete_26of26 PASSED [ 21%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_08_discovery_partial_25of26_invalid_evidence_id PASSED [ 25%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_09_two_runs_same_model_and_dataset_distinct_names PASSED [ 28%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_10_different_models_same_conversation PASSED [ 31%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_11_results_recovered_from_checkpoint PASSED [ 34%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_12_missing_or_unattempted_chunks PASSED [ 37%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_13_durations_and_tokens_not_available PASSED [ 40%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_14_serialization_of_enum_dataclass_metadata PASSED [ 43%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_16_no_new_inference_during_download PASSED [ 46%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_17_no_data_loss_from_original_result PASSED [ 50%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_18_no_private_content_in_safe_report PASSED [ 53%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_19_absence_of_full_prompts_and_responses PASSED [ 56%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_20_no_regression_topic_discovery_detection_consistency PASSED [ 59%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_21_integrity_of_frozen_scientific_files PASSED [ 62%]
tests/unit/test_analysis_export.py::TestObligatorySyntheticExportScenarios::test_22_benchmark_autosave_continuity PASSED [ 65%]
tests/unit/test_analysis_export.py::TestTaxonomyAndVerificationLevels::test_mock_client_never_declared_live_authoritative PASSED [ 68%]
tests/unit/test_analysis_export.py::TestStreamlitAppTestExportButtons::test_15_and_23_streamlit_apptest_export_buttons_and_privacy_toggles PASSED [ 71%]
tests/unit/test_analysis_export.py::TestP0ScientificTruthfulnessAndAuditFixes::test_acceptance_A_deepseek_named_error_regression_finish_reason_length PASSED [ 75%]
tests/unit/test_analysis_export.py::TestP0ScientificTruthfulnessAndAuditFixes::test_acceptance_B_unknown_provenance_defaults_to_unattested PASSED [ 78%]
tests/unit/test_analysis_export.py::TestP0ScientificTruthfulnessAndAuditFixes::test_acceptance_C_adversarial_privacy_scrubbing_safe_default PASSED [ 81%]
tests/unit/test_analysis_export.py::TestP0ScientificTruthfulnessAndAuditFixes::test_acceptance_D_no_invented_metrics_null_distinct_from_zero PASSED [ 84%]
tests/unit/test_analysis_export.py::TestP0ScientificTruthfulnessAndAuditFixes::test_acceptance_E_coverage_counter_invariants_and_unattempted_chunks PASSED [ 87%]
tests/unit/test_analysis_export.py::TestP0ScientificTruthfulnessAndAuditFixes::test_acceptance_F_strict_consistency_matching_and_no_blind_independence PASSED [ 90%]
tests/unit/test_analysis_export.py::TestP0ScientificTruthfulnessAndAuditFixes::test_acceptance_G_historical_software_commit_separated_from_export_commit PASSED [ 93%]
tests/unit/test_analysis_export.py::TestP0ScientificTruthfulnessAndAuditFixes::test_acceptance_H_mock_runs_never_live_authoritative PASSED [ 96%]
tests/unit/test_analysis_export.py::TestP0ScientificTruthfulnessAndAuditFixes::test_acceptance_I_ui_export_defaults_to_safe_privacy PASSED [100%]

============================= 32 passed in 22.19s =============================
```

### 4.1 Tassonomia Rigorosa dello Stato di Verifica
- **`STATICALLY_VERIFIED`**:
  - Correttezza sintattica e tipi statici di [ai/lmstudio.py](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/ai/lmstudio.py), [analysis/export.py](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/analysis/export.py), [ui/application.py](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/ui/application.py), [ui/presentation.py](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/ui/presentation.py).
  - Schema JSON v1.1.0 e dichiarazione `SHA256_UNSALTED_TRUNCATED`.
- **`TEST_VERIFIED`**:
  - Tutti i 32 test sintetici ed avversari eseguiti con successo in ambiente isolato su Windows.
- **`MOCK_VERIFIED`**:
  - Risoluzione del NameError di DeepSeek mediante mock HTTP strutturato.
  - Test con `FakeLocalLlmClient` classificati esplicitamente come `MOCK_VERIFIED` (mai `LIVE_AUTHORITATIVE`).
- **`NOT_VERIFIED`**:
  - Esecuzione live con LM Studio su GPU Ubuntu (in carico all'utente per la prova empirica).
  - Suite di concorrenza F1 Linux (test_122, test_136-138, test_144, test_151).

---

## 5. INTEGRITÀ CRITTOGRAFICA DEI BENCHMARK CONGELATI

In conformità al vincolo di inviolabilità scientifica:
- `data/synthetic_multilingual_chat.json`: SHA-256 intatto e verificato byte-per-byte (`FROZEN_DATASET_SHA256`).
- `data/ground_truth_topics.json`: SHA-256 intatto e verificato byte-per-byte (`FROZEN_GROUND_TRUTH_SHA256`).
- Le sei run storiche già concluse (Qwen e Llama) sono state preservate senza alcuna sovrascrittura o alterazione.

---

## 6. LINEE GUIDA PER IL COMMIT LOCALE LEGITTIMO (SENZA AZIONI AUTOMATICHE)

Il repository Git locale è stato preservato senza comandi automatici (`commit`, `push`, `pull`, `fetch` o `reset`).
Qualora lo script di benchmark su Ubuntu richieda un working tree pulito (*clean working tree*), l'utente potrà procedere alla registrazione manuale del commit locale tramite i seguenti comandi verificati:

```bash
# 1. Verifica preliminare dello stato (working tree non deve contenere file privati)
git status

# 2. Aggiunta mirata dei moduli corretti e dei test
git add ai/lmstudio.py analysis/export.py analysis/__init__.py ui/application.py ui/presentation.py
git add tests/unit/test_analysis_export.py CHATANALYSIS_JSON_EXPORT_SCHEMA_SPECIFICATION.md docs/examples/

# 3. Registrazione del commit locale esplicito (approvato dall'utente)
git commit -m "fix(export,deepseek): P0 truthfulness, fail-closed provenance, safe privacy and DeepSeek NameError fix"
```

---

## 7. SPECIFICA DEL PACCHETTO DI CONSEGNA

- **Nome Archivio:** `chatAnalysis_json_export_truthfulness_deepseek_f1_fix_audit_20261021.zip`
- **Percorso di Creazione:** `c:\Users\lucas\Desktop\CartellaTesi\chatAnalysis_json_export_truthfulness_deepseek_f1_fix_audit_20261021.zip` (rigorosamente all'esterno della cartella `Tesi`).
- **Contenuto Verificato:** Codice sorgente completo, suite di test aggiornata (32 test), documentazione tecnica, specifica schema JSON 1.1.0, 4 esempi JSON sintetici e `manifest_sha256.txt` autenticato senza duplicati né percorsi traversal.
