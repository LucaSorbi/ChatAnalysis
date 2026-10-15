# REPORT: MIGLIORAMENTO TOPIC DETECTION E TOPIC DISCOVERY SU CONVERSAZIONI REALI (CHATANALYSIS)
**Data:** 13 Ottobre 2026  
**Ambiente di Riferimento:** Ubuntu / LM Studio / Qwen2.5-7B-Instruct Q4_K_M (Context 8192, Temp 0.0)  
**Pacchetto di Rilascio:** `chatAnalysis_realworld_topic_analysis_improvements_20261013.zip`

---

## 1. EXECUTIVE SUMMARY & ANALISI DELLE CAUSE RADICE

Nei test operativi eseguiti su Ubuntu con LM Studio e una conversazione WhatsApp reale di circa 68 KB sono stati riscontrati comportamenti anomali:
1. **Errore `Unterminated string` in Topic Discovery con 10 argomenti**: il budget di output predefinito era impostato rigidamente a `512` token (`DEFAULT_OPERATIONAL_MAX_TOKENS = 512`). La serializzazione di 10 topic con label, descrizioni ed evidence ID ha saturato il buffer prima della chiusura delle parentesi quadre/graffe (`finish_reason="length"`), producendo un JSON troncato a metà stringa.
2. **Discrepanza tra verifica da Discovery e Topic Detection manuale**:
   - Dalla pagina Discovery, la verifica invocava la detection passando sia la label sia la `short_description` generata dal modello durante la discovery.
   - Dalla pagina manuale, quando l'utente inseriva solo il titolo, `build_topic_query()` eseguiva un fallback rigido `description = label`, inviando al prompt `DESCRIPTION: Acquisto di un'automobile` senza istruzioni semantiche su sinonimi e formulazioni equivalenti. Il modello interpretava l'assenza di descrizioni come una richiesta di corrispondenza letterale, restituendo `ABSENT`.
3. **Mancanza di granularità multi-chunk e diagnostica UI**: le conversazioni reali di grandi dimensioni (>60 KB) generano decine di chunk di contesto. Senza metriche sui chunk analizzati, completati e falliti, l'utente non aveva visibilità sull'effettiva copertura dell'analisi.
4. **Tracciamento di consistenza ambiguo**: una nuova detection manuale poteva sovrascrivere o essere confusa con una precedente verifica eseguita dalla pagina Discovery, senza chiara attribuzione del `run_id`, dell'input mode (`Sola Label` vs `Label + Descrizione`) e del documento di riferimento.

---

## 2. ARCHITETTURA DELLE SOLUZIONI IMPLEMENTATE

### 2.1 Obiettivo 1: Topic Detection Semantica con Sola Label
- **Distinzione esplicita in `TopicQuery`**: Aggiunto il campo `is_label_only: bool = False` sia nel modello dati (`ai/models.py`) sia nella logica applicativa (`ui/application.py` in `build_topic_query`). Se la descrizione fornita è nulla o vuota, la query viene marcata formalmente come `is_label_only = True`.
- **Direttiva Semantica Operativa (`OPERATIONAL_SEMANTIC_DIRECTIVE`)**:
  - Introdotta nel prompt operativo `topic_detection_operational_v2` (`ai/topics.py`).
  - Istruisce il modello a interpretare il concetto semantico sotteso al titolo, riconoscendo sinonimi, parafrasi, gergo e azioni collegate (es. `"Acquisto di un'automobile"` riconosce `"macchina usata"`, `"passaggio di proprietà"`, `"costa ottomila euro"`, senza richiedere la parola letterale `"automobile"`).
  - Mantiene la rigida disciplina tri-stato forense: `PRESENT`, `ABSENT`, `UNCERTAIN`. Nessun falso positivo forzato; argomenti estranei (es. `"Cucina giapponese"`) restituiscono tassativamente `ABSENT`, mentre riferimenti parziali o dubbi restituiscono `UNCERTAIN`.
  - Vincolo forense sugli Evidence ID: ammessi solo ID effettivamente presenti nei messaggi del chunk; gli ID inesistenti o allucinati vengono intercettati e respinti dal validatore prima della visualizzazione.
  - Multi-chunk: la detection analizza sistematicamente tutti i chunk del documento; lo stato `PRESENT` viene aggregato con l'unione dedublicata di tutti gli Evidence ID validi.

### 2.2 Obiettivo 2: Topic Discovery con Scalabilità fino a 25 Argomenti
- **Budget di Generazione Dinamico (`calculate_discovery_max_tokens`)**:
  - Implementato in `analysis/service.py`: calcola il budget di token in base al numero di topic richiesti:
    - 3–5 topic: 1024 token
    - 10 topic: 1456 token
    - 15 topic: 2056 token
    - 20 topic: 2656 token
    - 25 topic: 3256 token
  - Rispetta sempre il limite di sicurezza `max_safe = context_length - 4096`, garantendo che su un context di 8192 token non si verifichino mai OOM o superamenti di finestra.
- **Decomposizione per Chunk e Consolidamento Multi-Chunk**:
  - Per evitare richieste ingestibili al modello all'interno di un singolo chunk, la scoperta per chunk è limitata a un target di sicurezza (minimo 5, massimo 8 argomenti per chunk).
  - L'aggregatore multi-chunk (`aggregate_topic_discovery`) unisce i topic scoperti nei diversi chunk, consolida le evidenze (unione degli evidence ID validi) e restituisce fino al massimo richiesto dall'utente (fino a 25 topic).
  - Se una conversazione reale produce solo 6 topic genuini con evidenze, ne vengono restituiti 6: **nessuna allucinazione per riempire artificialmente la lista**.
- **Gestione Esplicita di `finish_reason="length"` e Retry Controllato**:
  - In `TopicDiscoveryAnalyzer.discover_topics` (`ai/topics.py`), se la risposta LLM è troncata per lunghezza o presenta un errore di deserializzazione JSON (`Unterminated string`), viene attivato un **retry controllato** (massimo 1 tentativo) con budget di token aumentato (`min(effective_max_tokens + 512, 4096)`).
  - Se il JSON rimane invalido dopo il retry, il sistema solleva esplicitamente un errore controllato: **non vengono inventati dati per riparare un JSON incompleto**.
- **Diagnostica Completa dei Chunk**:
  - Tracciamento nei metadati e nell'interfaccia di:
    - Numero totale di chunk analizzati (`total_chunks`)
    - Chunk completati con successo (`completed_chunks`)
    - Chunk falliti (`failed_chunks`)

### 2.3 Obiettivo 3: Coerenza tra Topic Discovery e Topic Detection nella UI
- **Identità Univoca e Non-Mutabilità delle Run Storiche**:
  - Ogni operazione riceve un `run_id` univoco (`run-det-...` per detection, `run-disc-...` per discovery).
  - I risultati storici in session state non vengono mai alterati retroattivamente.
  - La verifica eseguita dalla schermata Discovery viene annotata con `origin_flow = "DISCOVERY_VERIFICATION"` e memorizza il riferimento alla `discovery_run_id` di origine.
  - La detection manuale dalla schermata Detection viene annotata con `origin_flow = "MANUAL_DETECTION"`.
- **Interfaccia Utente Trasparente**:
  - In **Topic Detection**: per ogni run viene visualizzato:
    - Identificativo Run (`run_id`) e data/ora.
    - Modalità Input: `Sola Label` o `Label + Descrizione`.
    - Origine del flusso: manuale o da verifica automatica.
    - Metriche di copertura chunk (completati, totali, falliti).
    - Eventuale **Avviso di Discrepanza**: se una successiva detection manuale differisce dalla verifica precedente, viene mostrato un banner informativo chiaro che documenta la divergenza senza sovrascrivere i dati storici.
  - In **Topic Discovery**:
    - Ogni card di argomento mostra il `discovery_run_id` di provenienza e le metriche sui chunk analizzati.
    - Lo stato di consistenza della verifica viene filtrato rigorosamente per la specifica coppia Discovery/Detection associata, evitando etichette fuori contesto.

---

## 3. CONGELAMENTO DEL BENCHMARK SCIENTIFICO

Come da vincolo perentorio, **nessun file relativo al benchmark scientifico è stato modificato**.

### Verifica SHA256 dei file congelati:
| File | SHA256 Hash Calcolato | Esito |
| :--- | :--- | :--- |
| `ai/experiment.py` | `91100C0A84CB4A8B7C2EEF34A06CFF9EC21AA6E6EEC14FFD09CE6FEA8BCEE845` | **CONFORME & INTATTO** |
| `ai/benchmark.py` | `DC57081DBCE3463A8017AA26BB4E4A18889AF0A3296853FE40968561D6D44D36` | **CONFORME & INTATTO** |
| `run_benchmark.sh` | `7F9CAE97DF2FAE1BC3B5964E639B9F80E3AC5A388EC9C3E20305FF64D48044A7` | **CONFORME & INTATTO** |

- Il prompt del benchmark scientifico (`topic_detection_v1`) e il token budget (512 token) rimangono inalterati.
- Le modifiche di prompt (`topic_detection_operational_v2`, `topic_discovery_operational_v2`) e il token budget dinamico (fino a 3256 token) operano **esclusivamente nella pipeline operativa (`analysis/service.py`, `ui/presentation.py`)**.

---

## 4. SUITE DI TEST E FIXTURE SINTETICHE

Per validare la robustezza senza violare la privacy, è stata creata una fixture sintetica di grandi dimensioni:
- **`test_data/synthetic_whatsapp_large_75kb.txt`**:
  - Dimensione: **92.400 byte (90.23 KB)**.
  - Contenuto: **750 messaggi** distribuiti su **25 argomenti tematici realistici** (compravendita auto, viaggi, consulenza legale, cene, progetti scolastici, riparazioni casa, ecc.).
  - Verifica: Ingestione forense completata in 0.08s con **zero chiamate AI durante l'ingestion**.

### Matrice dei Test Eseguiti:
| Suite di Test | Test Eseguiti | Esito |
| :--- | :---: | :---: |
| `tests/unit/test_realworld_topic_improvements.py` | **21 / 21** | **PASSED** |
| `tests/unit/test_ai_detection_multimodal_diagnostics.py` | **16 / 16** | **PASSED** |
| `tests/unit/test_ui_manual_topic_detection.py` | **13 / 13** | **PASSED** |
| `tests/unit/test_apptest_flows.py` | **7 / 7** | **PASSED** |
| **Suite Completa di Progetto (`pytest`)** | **1675 / 1675** | **PASSED** |

### Dettaglio dei Casi di Test Coperti:
1. `test_build_topic_query_sets_is_label_only`: verifica marcatura label-only quando descrizione è vuota.
2. `test_detection_label_only_car_purchase_present_with_synonyms`: detection del tema "Acquisto di un'automobile" su messaggi con "macchina usata", "ottomila euro", "passaggio di proprietà" (senza la parola letterale "automobile").
3. `test_detection_negative_topic_absent`: verifica che argomenti non presenti restituiscano `ABSENT` (nessun falso positivo).
4. `test_detection_ambiguous_topic_uncertain`: verifica che argomenti vaghi restituiscano `UNCERTAIN`.
5. `test_detection_rejects_hallucinated_evidence_id`: validazione dello scarto di evidence ID non esistenti nel chunk.
6. `test_detection_multi_chunk_completeness`: analisi completa su tutti i chunk e unione degli ID.
7. `test_calculate_discovery_max_tokens_scaling`: scaling del budget per 3, 5, 10, 15, 20, 25 topic.
8. `test_topic_discovery_returns_fewer_than_max_when_fewer_exist`: se esistono solo 3 topic supportati, restituisce solo 3 topic.
9. `test_topic_discovery_25_topics_consolidation`: scoperta e consolidamento fino a 25 topic con evidenze associate.
10. `test_topic_discovery_finish_reason_length_triggers_controlled_retry`: simulazione di troncamento con retry e successo.
11. `test_topic_discovery_unrepairable_json_raises_without_hallucinating`: rifiuto di completare con allucinazioni un JSON non riparabile.
12. `test_discovery_verification_maintains_independent_run_identity`: separazione e tracciamento tra Discovery e Verification.
13. `test_manual_detection_after_discovery_verification_does_not_mutate_prior_runs`: immutabilità delle run storiche.
14. `test_zero_ai_inference_during_real_file_ingestion`: isolamento totale dell'ingestione forense.
15. `test_no_cross_contamination_between_documents`: isolamento di sessione e documenti multipli.
16. `test_streamlit_apptest_topic_tabs_rendering`: rendering headless Streamlit con componenti e avvisi.

---

## 5. ISTRUZIONI PER L'INSTALLAZIONE E IL TEST SU UBUNTU (LM STUDIO + QWEN)

### Prerequisiti su Ubuntu:
- LM Studio in esecuzione con **Qwen2.5-7B-Instruct Q4_K_M**.
- Server locale avviato su porta `1234` (`http://localhost:1234/v1`).
- Modello caricato con Context Length impostato a `8192` (o superiore).

### Passaggi di Installazione:
1. **Scaricare ed estrarre il pacchetto ZIP**:
   ```bash
   unzip chatAnalysis_realworld_topic_analysis_improvements_20261013.zip -d chatanalysis_update
   cd chatanalysis_update
   ```

2. **Attivare l'ambiente virtuale di ChatAnalysis**:
   ```bash
   source .venv/bin/activate
   ```

3. **Installare il pacchetto wheel aggiornato**:
   ```bash
   pip install --force-reinstall dist/whatsapp_forensic_importer-0.1.0-py3-none-any.whl
   ```

4. **Verificare l'integrità dei test con pytest**:
   ```bash
   pytest tests/unit/test_realworld_topic_improvements.py -v
   ```

5. **Avviare l'applicazione Streamlit**:
   ```bash
   streamlit run ui/main.py
   ```

### Procedura di Validazione sulla Chat Reale WhatsApp (68 KB):
1. **Importazione della chat reale**:
   - Caricare il file di chat WhatsApp nella scheda di Ingestion.
   - Verificare che il parsing si completi istantaneamente senza invocazioni AI.
2. **Test di Topic Discovery con 10, 15 o 20 argomenti**:
   - Spostarsi nella scheda **Topic Discovery**.
   - Selezionare `10` argomenti (o fino a `25`) e cliccare su **Avvia Topic Discovery**.
   - **Risultato atteso**: Nessun errore `Unterminated string`; l'analisi completa tutti i chunk, mostra il conteggio dei chunk analizzati e restituisce gli argomenti supportati da evidenze.
3. **Verifica della Topic Detection da Discovery**:
   - Cliccare su **Verifica Consistenza** per uno degli argomenti scoperti.
   - Osservare il risultato di consistenza (`CONSISTENT` o `INCONSISTENT`) con il rispettivo `run_id`.
4. **Test di Topic Detection Manuale con Sola Label**:
   - Spostarsi nella scheda **Topic Detection**.
   - Inserire unicamente il titolo dell'argomento nel campo **Titolo Argomento** (es. `Acquisto di un'automobile` o altro tema presente nella chat).
   - Lasciare il campo **Descrizione** completamente vuoto.
   - Cliccare su **Esegui Topic Detection**.
   - **Risultato atteso**: Il sistema riconosce semanticamente il tema in base al contenuto dei messaggi, associando gli Evidence ID pertinenti, senza richiedere parole letterali o forzare risultati.
   - Se il risultato differisce da una verifica precedente, l'interfaccia segnala la discrepanza in modo chiaro senza sovrascrivere la storia.

---

## 6. CONTENUTO DEL PACCHETTO DI RILASCIO

Il file `chatAnalysis_realworld_topic_analysis_improvements_20261013.zip` contiene:
- File sorgenti modificati (`ai/__init__.py`, `ai/models.py`, `ai/topics.py`, `analysis/service.py`, `ui/application.py`, `ui/presentation.py`).
- Pacchetto binario di rilascio (`dist/whatsapp_forensic_importer-0.1.0-py3-none-any.whl`).
- Test suite completa aggiornata (`tests/unit/test_realworld_topic_improvements.py`).
- Fixture sintetica di 90 KB (`test_data/synthetic_whatsapp_large_75kb.txt`).
- File patch git (`git_diff_topic_improvements.patch`).
- File di hash del benchmark scientifico (`benchmark_frozen_sha256.txt`).
- Log completi dei test (`pytest_and_apptest.log`, `json_multichunk_verification.log`).
- Manifest completo SHA256 (`MANIFEST_SHA256.txt`).
- Questo documento di report (`REALWORLD_TOPIC_ANALYSIS_IMPROVEMENTS_REPORT.md`).
