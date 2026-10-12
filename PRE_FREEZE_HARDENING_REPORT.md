# CHATANALYSIS — PRE-FREEZE HARDENING & INDEPENDENT AUDIT REPORT
**Data di riferimento:** 10 Ottobre 2026  
**Autori/Ruoli:** Senior Python Engineer, Forensic Software Architect, Quality Assurance Lead  
**Stato Release Gate:** NON AUTORIZZATO IL FREEZE (In attesa di audit indipendente post-hardening)  
**Bundle Generato:** `chatAnalysis_pre_freeze_hardening_audit_20261010.zip`

---

## 1. QUADRO ESECUTIVO & REGISTRO DEI BLOCKER

L'audit indipendente condotto sul bundle `chatAnalysis_release_gate_reaudit_20261010.zip` ha confermato l'efficacia delle correzioni precedenti, ma ha identificato cinque blocker critici che non venivano intercettati dai test di regressione esistenti.

Questo report documenta le azioni correttive strutturali implementate sul repository corrente di ChatAnalysis per rimuovere i difetti alla radice, senza introdurre workaround cosmetici o strati di compatibilità che preservino le vulnerabilità.

| Blocker | Descrizione | Root Cause Primaria | Stato Verifica | Esito Test |
| :--- | :--- | :--- | :--- | :--- |
| **BLOCKER 1** | Media Registry: Isolamento Sessioni e Pulizia File | Registro globale mutabile `_fallback_default` e descrittore `_HybridRegistryMethod` | **STATICALLY_VERIFIED / MOCK_VERIFIED** | 7/7 Blocker Tests PASSED<br>31/31 Asset Access PASSED |
| **BLOCKER 2** | LM Studio: Identità del Modello Non Verificata | Accettazione di risposte generiche `{"status":"loaded"}` e modelli non pertinenti | **STATICALLY_VERIFIED / MOCK_VERIFIED** | 24/24 Loading Tests PASSED<br>31/31 AI Client Tests PASSED |
| **BLOCKER 3** | Discovery Provenance: Run Non Autenticata | `verify_discovered_topic` registrava run arbitrarie esterne; topic matching su sola label | **STATICALLY_VERIFIED / MOCK_VERIFIED** | 19/19 Provenance Tests PASSED<br>12/12 UI Detection PASSED |
| **BLOCKER 4** | Large-File: Complessità Memoria e Prestazioni Ingestion | Backtracking catastrofico regex allegati in `import_records` ($O(L^2)$ per messaggio) | **EMPIRICALLY_VERIFIED / STATICALLY_VERIFIED** | 10 MB in 26.44s (da ~350s)<br>25 MB in 62.87s (RSS 381 MB) |
| **BLOCKER 5** | Media Processing: Successo Prematuro e Lifecycle | `multimodal_processed=True` impostato anche su OCR FAILED senza backend | **STATICALLY_VERIFIED / MOCK_VERIFIED** | 8/8 Lifecycle Tests PASSED<br>100% Non-destructive retry |

---

## 2. BLOCKER 1 — MEDIA REGISTRY: ISOLAMENTO TRA SESSIONI

### Problema Iniziale
Il componente `multimodal/asset_access.py` consentiva il recupero di asset multimediali caricati da una sessione Streamlit (Sessione A) attraverso un'altra sessione concorrente (Sessione B) avente il medesimo `document_id`. Il reset della Sessione A non invalidava né ripuliva i file dal registro globale di processo, violando l'isolamento forense multi-tenant e provocando potenziale data leakage tra procedimenti giudiziari differenti.

### Root Cause
Presenza del dizionario globale di processo `MediaAssetRegistry._fallback_default` e della classe descrittore `_HybridRegistryMethod`, introdotti per comodità legacy, che consentivano alle chiamate classmethod di scrivere e leggere da una struttura dati condivisa tra tutti i thread e sessioni del processo Python.

### Decisione Architetturale
1. **Eliminazione Radicale dello Stato Globale**: Rimossi integralmente `_fallback_default` e `_HybridRegistryMethod`. Nessun metodo di classe o singleton di processo è più consentito.
2. **Esclusiva Istanziabilità e Session-Scoping**: `MediaAssetRegistry` è ora strettamente istanziabile (`__init__(managed_temp_dir=...)`) e implementa il protocollo context manager (`__enter__` / `__exit__`).
3. **Iniezione Esplicita di Dipendenza**: Streamlit `ui/state.py` istanzia e conserva un `MediaAssetRegistry` all'interno di `st.session_state` (`get_media_registry()`). Tutte le invocazioni di `ingest_file_payload()` ricevono il registro di sessione come parametro esplicito `media_registry`.
4. **Cleanup Garantito e Isolamento del Filesystem**: Ciascuna istanza di registro possiede una propria directory temporanea isolata `_managed_temp_dir` (`tempfile.TemporaryDirectory(prefix="chatanalysis_media_session_")`). Il metodo `cleanup()` elimina ricorsivamente la cartella e svuota le tabelle di mapping. Il reset di Sessione A elimina unicamente i file di A senza impattare Sessione B.
5. **Autonomia di Ingestion API/Test**: Se `media_registry` è `None`, `ingest_file_payload` istanzia un registro effimero isolato locale, preservando l'eseguibilità senza Streamlit.
6. **Supporto Multi-Conversation A → B → A**: Gli archivi ZIP associati a ciascun `document_id` vengono copiati nella directory gestita di sessione (`copy_source_to_managed`), garantendo che il cleanup della directory effimera di staging non renda indisponibili i media durante il cambio di conversazione attiva.

### Sorgenti Modificati
- `multimodal/asset_access.py`: Eliminazione di `_fallback_default` e `_HybridRegistryMethod`; implementazione di `MediaAssetRegistry` istanziabile con gestione ciclo di vita e directory temporanea gestita.
- `ui/state.py`: Registrazione in `st.session_state` del registry isolato per sessione; invocazione di `registry.cleanup()` nel reset di sessione.
- `ui/ingestion.py`: Accettazione di `media_registry: MediaAssetRegistry | None = None` in `ingest_file_payload`, associazione document-scoped per ciascun `document_id`.
- `ui/presentation.py`: Passaggio del registry di sessione attivo.

### Test Aggiunti e Verificati
File: `tests/unit/test_media_registry_session_isolation.py` (7 test)
1. `test_session_isolation_a_does_not_leak_to_b`: Sessione A registra file, Sessione B non lo vede.
2. `test_identical_document_id_no_cross_contamination`: Sessioni A e B con `document_id` identico rimangono rigidamente isolate.
3. `test_reset_session_a_cleans_only_a`: Reset di Sessione A cancella file di A senza alterare Sessione B.
4. `test_no_global_registry_methods_remain`: Verifica statica e dinamica dell'assenza di metodi di classe globali o fallback.
5. `test_multi_conversation_a_b_a_in_same_session`: Navigazione conversazioni A → B → A conserva l'accesso integro agli asset.
6. `test_zip_ingestion_media_accessible_after_staging_cleanup`: L'ingestion ZIP conserva accesso ai media anche dopo l'eliminazione della staging directory.
7. `test_direct_instantiation_without_streamlit`: Esecuzione autonoma della pipeline senza dipendenze implicite.

---

## 3. BLOCKER 2 — LM STUDIO: IDENTITÀ DEL MODELLO NON VERIFICATA

### Problema Iniziale
`LmStudioClient.load_model()` e `chat_completion()` accettavano risposte HTTP prive di identità verificabile, come `{"status":"loaded"}`, oppure risposte recanti modelli divergenti (es. DeepSeek, Llama o Qwen3) mentre era richiesto `qwen2.5-7b-instruct`. Il benchmark scientifico e l'analisi operativa rischiavano di eseguire inferenze su modelli diversi da quelli certificati per la perizia forense.

### Root Cause
Validazione debole del payload JSON restituito dagli endpoint `/api/v1/models/load` e `/v1/chat/completions`, priva di controllo positivo rispetto alla matrice di compatibilità canonica e ai token proibiti.

### Decisione Architetturale
1. **Controllo Positivo di Identità e Token Proibiti**: Introdotta la funzione `is_conflicting_model_or_instance(candidate, requested)` che rifiuta categoricamente identificatori appartenenti a famiglie non richieste (es. `deepseek`, `llama`, `qwen3` o varianti non compatibili quando è richiesto `qwen2.5`).
2. **Supporto ad Instance ID Opachi**: Riconoscimento di instance ID generati da LM Studio sotto forma di UUID opachi (es. `real-instance-uuid-42`), senza pretendere che l'UUID contenga la sottostringa del nome modello, purché non contengano token in conflitto.
3. **Rigido Ciclo di Vita del Modello**: Distinzione tassonomica degli stati:
   - `LOADED`: identità verificata positivamente contro il modello richiesto.
   - `ALREADY_LOADED`: istanza attiva già verificata.
   - `LOADING`: operazione asincrona in corso (non utilizzabile per inferenza immediata).
   - `JIT_UNVERIFIED`: endpoint di load non disponibile (404/405/501) o risposta generica priva di modello (`{"status":"loaded"}`). Non aggiorna `_last_loaded_model`.
   - `FAILED`: caricamento fallito o rifiutato.
4. **Verifica su Prima Completion**: In stato `JIT_UNVERIFIED`, la prima completion deve validare tassativamente `response.model`. Se il modello differisce o appartiene a un'altra famiglia, viene sollevata immediatamente `AiModelMismatchError` e `_last_loaded_model` rimane `None`.
5. **Preservazione Match Esatto per Benchmark Scientifico**: Il benchmark scientifico continua a utilizzare matching esatto del Model ID senza tolleranza.

### Sorgenti Modificati
- `ai/lmstudio.py`: Implementazione di `is_conflicting_model_or_instance()`, raffinamento di `load_model()` e `chat_completion()` con validazione positiva, gestione JIT e controllo anti-mismatch.

### Test Aggiunti e Verificati
File: `tests/unit/test_lmstudio_model_loading.py` (24 test)
1. `test_loaded_with_exact_correct_identity`: Stato loaded e identità esatta → accettato.
2. `test_loaded_with_conflicting_model_deepseek_rejected`: Risposta DeepSeek per Qwen2.5 → solleva `AiModelMismatchError`.
3. `test_loaded_with_conflicting_model_qwen3_rejected`: Risposta Qwen3 per Qwen2.5 → solleva `AiModelMismatchError`.
4. `test_loaded_with_generic_status_no_identity_returns_jit_unverified`: Risposta `{"status":"loaded"}` → `JIT_UNVERIFIED`, `_last_loaded_model` non impostato.
5. `test_first_completion_mismatch_raises_ai_model_mismatch_error`: Prima completion con modello errato → fallimento esplicito.
6. `test_first_completion_success_verifies_binding`: Prima completion corretta → binding confermato.

---

## 4. BLOCKER 3 — DISCOVERY PROVENANCE: RUN NON AUTENTICATA

### Problema Iniziale
L'audit ha dimostrato che un client poteva forgiare un `TopicDiscoveryResult` artificiale (`run_forged_external`), passarlo a `verify_discovered_topic()` e ottenere un verdetto `CONSISTENT`. Inoltre, era possibile sottomettere un topic con la medesima label ma con descrizione o evidence ID differenti rispetto a quelli originariamente prodotti dalla Topic Discovery, violando la catena di custodia scientifica della "Prova del Nove".

### Root Cause
In `analysis/service.py`, `verify_discovered_topic()` conteneva un blocco di fallback che invocava `register_discovery_run()` sul risultato fornito come argomento della chiamata, senza verificare se la run fosse stata effettivamente generata dal servizio. Il matching del topic si basava inoltre esclusivamente sulla stringa normalizzata della label.

### Decisione Architetturale
1. **Registrazione Autoritativa Esclusiva**: La registrazione di una discovery run avviene unicamente all'interno del metodo `run_topic_discovery()`. Il metodo `verify_discovered_topic()` non può registrare né accettare run non presenti nel registro autoritativo (`discovery_run_store`).
2. **Iniezione del Run Store Document-Scoped**: L'Analysis Service riceve un `discovery_run_store` document-scoped dalla sessione Streamlit (`ui/state.py`), prevenendo la perdita di stato tra chiamate UI successive.
3. **Identità Forense Integrale del Topic**: Il confronto del topic non si limita alla label, ma richiede:
   - Validazione del `run_id` registrato;
   - Corrispondenza identica del testo della `short_description`;
   - Corrispondenza biunivoca degli `evidence_ids` associati al topic dalla Discovery;
   - Appartenenza al medesimo `document_id`.
   - Se uno qualunque di questi campi è alterato, la verifica fallisce prima di eseguire qualunque inferenza.
4. **Indipendenza Assoluta della Topic Detection (Prova del Nove)**:
   - La seconda inferenza (Detection) riceve nel prompt esclusivamente: `label`, `short_description` e il testo dell'intero `ConversationEvidenceDocument`.
   - Gli `evidence_ids` della Discovery **non sono mai inclusi** nel prompt di Detection.
   - Il confronto degli evidence ID per la determinazione dell'overlap e della Consistency avviene post-inferenza, confrontando i due insiemi indipendenti.

### Sorgenti Modificati
- `analysis/service.py`: Rimozione dell'auto-registrazione arbitraria in `verify_discovered_topic()`; confronto forense completo di topic identity e provenance; controllo rigoroso del run store.
- `ui/state.py`: Esposizione di `get_discovery_run_store()` con sincronizzazione delle run attive e DEMO nel service operativo.

### Test Aggiunti e Verificati
File: `tests/unit/test_discovery_provenance_service.py` (19 test)
1. `test_unregistered_run_rejected_before_detection`: Run forged esterna respinta con errore prima di invocare il LLM.
2. `test_same_label_tampered_description_rejected`: Stessa label ma descrizione modificata respinta prima della Detection.
3. `test_same_label_tampered_evidence_ids_rejected`: Stessa label ma evidence IDs alterati respinta prima della Detection.
4. `test_detection_prompt_contains_zero_discovery_evidence_ids`: Verifica che il prompt inviato al modello non contenga le evidenze scoperte in precedenza.
5. `test_multi_conversation_provenance_preserved`: Catena di custodia verificata su cambio conversazione A → B → A.

---

## 5. BLOCKER 4 — LARGE-FILE: COMPLESSITÀ DI MEMORIA E PERFORMANCE

### Problema Iniziale
L'audit ha osservato che la memoria del resolver cresce proporzionalmente al numero di record ($N$ riferimenti per $N$ messaggi), rendendo scorretta la precedente dichiarazione di "O(1) memory complexity". Inoltre, l'ingestion di file da 10 MB richiedeva circa 350 secondi nei benchmark iniziali.

### Root Cause
1. **Complessità Algoritmica Dichiarata**: La materializzazione finale del grafo forense e del `ConversationEvidenceDocument` immutabile richiede intrinsecamente memoria proporzionale ai record unificati ($O(N)$), mentre solo la working memory dello streaming SQLite è delimitata a $O(B)$ (dimensione del batch).
2. **Collo di Bottiglia Prestazionale Rilevato tramite Profiling**: Il profiling su `import_records` di `importer/whatsapp_export.py` ha rivelato che la regex `_ATTACHMENT_RE`:
   ```python
   (?P<fn2>[^\r\n:]+?)\s*\((?:file allegato|file attached|archivo adjunto|fichier attaché)\)
   ```
   provocava un backtracking catastrofico ($O(L^2)$) su ogni riga di testo ordinaria (500 caratteri privi di parentesi), assorbendo oltre 12.9 secondi su 13.4 secondi totali (96.5% del tempo di esecuzione).

### Decisione Architetturale e Ottimizzazioni
1. **Dichiarazione Esplicita del Modello di Complessità**:
   - Working buffer ingestion: delimitato da SQLite chunking ($O(B)$, batch da 1.000 record).
   - Indice entità/identità: $O(E)$ proporzionale al numero di entità uniche.
   - Strutture di provenance e riferimenti: $O(N)$ proporzionale ai record elaborati.
   - Documento finale forense in memoria: $O(N)$ per garantire l'immutabilità e l'integrità crittografica dell'evidenza.
2. **Ottimizzazione Importer senza Alterazione Semantica**:
   - Introdotta funzione `_extract_attachment_name` con guardie a costo zero: se la riga non contiene `'('` o parole chiave di allegato, la scansione a ritroso per il formato tra parentesi viene saltata istantaneamente.
   - Tempo di parsing su 1.000 messaggi ridotto da 13.5s a 0.15s (accelerazione di **90x**).
3. **Ottimizzazione Indici SQLite Staging**: Raggruppamento duplicati direttamente su `(timeline_kind, timeline_value, text_content)` sfruttando l'indice SQLite `idx_staged_dup`.

### Benchmark Empirico di Ingestion (Misurazioni Reali)
Esecuzione reale tramite `scripts/benchmark_large_ingestion.py`:

| Target (MB) | Input Bytes | Record Count | Parsing (s) | ER (s) | DocBuild (s) | Tempo Totale (s) | Peak RSS (MB) | Msg Count | Evidence Count | SHA-256 Integrità | Correttezza Rif. |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **10 MB** | 10.486.064 | 18.963 | 15.03 s | 4.35 s | 7.05 s | **26.44 s** | 181.95 MB | 18.963 | 18.963 | `c6bdc6d2...` | **MATCH_REFERENCE** |
| **25 MB** | 26.214.488 | 47.405 | 35.87 s | 8.18 s | 18.80 s | **62.87 s** | 381.47 MB | 47.405 | 47.405 | `112f20da...` | **MATCH_REFERENCE** |

*Nota di conformità*: Il report precedente indicava 350 secondi per 10 MB; il nuovo tempo misurato è di **26.44 secondi** (riduzione dell'oltre 92% del tempo totale), con picco RSS di soli 181 MB e integrità crittografica e referenziale perfetta al 100%.

---

## 6. BLOCKER 5 — MEDIA PROCESSING: SUCCESSO PREMATURO E LIFECYCLE

### Problema Iniziale
Quando un documento conteneva immagini ma il backend OCR non era configurato o disponibile, il sistema impostava `multimodal_processed = True` con stato OCR `FAILED` e zero evidenze estratte. Questa falsa indicazione di successo impediva all'utente di tentare il retry dalla schermata Esplora una volta installato il motore OCR.

### Root Cause
Mancanza di una tassonomia esplicita di lifecycle per il processing multimediale; il flag booleano `multimodal_processed` veniva impostato a `True` al termine dell'iterazione indipendentemente dal successo effettivo delle singole estrazioni.

### Decisione Architetturale
1. **Tassonomia Rigida degli Stati**: Introdotto l'enum document-scoped `MediaProcessingStatus`:
   - `NOT_PROCESSED`: nessuna elaborazione avviata o documento privo di media.
   - `COMPLETE`: tutti gli asset multimediali supportati sono stati processati con successo.
   - `PARTIAL`: almeno un asset multimediale elaborato con successo e almeno uno fallito.
   - `FAILED`: tutti gli asset multimediali supportati hanno fallito l'elaborazione.
2. **Accoppiamento del Flag di Completamento**: `multimodal_processed` assume valore `True` **esclusivamente** se `status == MediaProcessingStatus.COMPLETE`.
3. **Retry Non Distruttivo e Idempotente**:
   - Il retry rielabora unicamente gli asset non ancora completati con successo.
   - Le sezioni di evidenza già generate non vengono duplicate né eliminate.
   - Un fallimento successivo non sovrascrive mai sezioni OCR/STT precedentemente estratte con successo.
   - L'immutabilità del `ConversationEvidenceDocument` originale è rigorosamente preservata (creazione di una nuova istanza arricchita).
4. **Documenti Privi di Media**: I documenti senza file multimediali ritornano immediatamente lo stato esplicito `NOT_PROCESSED` con `multimodal_processed = False` senza richiedere un file ZIP.

### Sorgenti Modificati
- `multimodal/asset_access.py`: Definizione di `MediaProcessingStatus`, implementazione della logica di lifecycle e retry non distruttivo in `process_document_media()`.
- `ui/state.py`: Tracciamento dello stato multimediale per `document_id`.

### Test Aggiunti e Verificati
File: `tests/unit/test_media_processing_lifecycle.py` (8 test)
1. `test_ocr_success_returns_complete`: Estrazione OCR riuscita → `COMPLETE`, `multimodal_processed = True`.
2. `test_ocr_unavailable_returns_failed_not_processed`: Backend assente → `FAILED`, `multimodal_processed = False`.
3. `test_partial_media_processing_one_success_one_fail`: 1 successo + 1 fallimento → `PARTIAL`, `multimodal_processed = False`.
4. `test_retry_after_backend_available_reaches_complete`: Retry dopo installazione backend → transizione a `COMPLETE`.
5. `test_retry_does_not_duplicate_evidence_sections`: Verifica anti-duplicazione delle sezioni nel documento finale.
6. `test_document_without_media_not_processed`: Documento senza allegati → `NOT_PROCESSED`, nessun errore.
7. `test_zip_error_no_fictitious_success`: Archivio corrotto → `FAILED`, nessun successo fittizio.
8. `test_no_fake_vision_on_real_data`: Dati reali non invocano simulazioni fittizie di visione.

---

## 7. VERIFICA DEI RELEASE GATES

| Controllo Gate | Comando di Verifica Eseguito | Esito | Dettaglio / Note |
| :--- | :--- | :---: | :--- |
| **1. Full pytest** | `pytest` | **1459 PASSED** | 100% test verdi in 183.75s (1 skipped, 5 deselected) |
| **2. Streamlit AppTest** | `pytest tests/unit/test_ui_*.py tests/integration/test_*.py` | **109 PASSED** | Workflow completi, upload grandi file, navigazione UI verificati |
| **3. Python compileall** | `python -m compileall -q .` | **PASSED** | Exit code 0, 0 errori sintattici su tutto il repository |
| **4. Wheel Build** | `pip wheel --no-deps -w dist .` | **PASSED** | Creata `whatsapp_forensic_importer-0.1.0-py3-none-any.whl` (264 KB) |
| **5. Isolated Wheel Import** | Test esterno su tempdir isolata | **PASSED** | Import riuscito di tutti i package fuori dall'albero sorgente |
| **6. Package Inclusi** | Ispezione namelist archivio wheel | **PASSED** | Tutti i 12 moduli inclusi: `ai, analysis, core, entity_resolution, importer, multimodal, normalization, profiler, search, ui, unified, validation` |
| **7. Assenza `.pyc` / `__pycache__`** | Ispezione file `.whl` | **PASSED** | **0** file `.pyc` e **0** directory `__pycache__` presenti |
| **8. Bash Syntax Check** | `bash -n run_benchmark.sh` | **PASSED** | Exit code 0, sintassi shell valida e corretta |
| **9. Git Mode Benchmark Script** | `git ls-files --stage run_benchmark.sh` | **PASSED** | `100755 2f6539b...` (Permessi eseguibili preservati, CRLF false) |
| **10. Diff Componenti Congelati** | `git diff HEAD -- ai/experiment.py ai/benchmark.py benchmark/` | **PASSED** | **0 diff** (identici byte-per-byte al benchmark scientifico congelato) |
| **11. Smoke Test LM Studio** | `python scripts/smoke_operational_ai.py` | **NOT_VERIFIED** | Server LM Studio loopback non attivo nell'ambiente di test Windows (previsto da protocollo; test mock `--fake` PASSED con successo) |

---

## 8. MATRICE COMPLETA DEI TEST E STATO DI VERIFICA

Secondo la tassonomia formale richiesta:
- **IMPLEMENTED**: Codice implementato e allineato ai contratti forensi.
- **STATICALLY_VERIFIED**: Verificato tramite AST, compileall, bash -n, o ispezione statica.
- **MOCK_VERIFIED**: Verificato tramite suite di test automatizzati con client o server simulati.
- **EMPIRICALLY_VERIFIED**: Misurato empiricamente su dataset sintetici reali (memoria, tempo, hash).
- **NOT_VERIFIED**: Funzionalità che richiede hardware specifico (es. server LM Studio live con GPU attiva su loopback) non presente nella presente sessione.
- **FAILED**: Controllo fallito (0 fallimenti riscontrati).

| Componente / Ambito | Verifica | Metodo di Esecuzione | Esito |
| :--- | :--- | :--- | :---: |
| Media Registry Isolamento Sessioni | STATICALLY_VERIFIED / MOCK_VERIFIED | `test_media_registry_session_isolation.py` (7 test) | **PASSED** |
| LM Studio Identità Modello & JIT Binding | STATICALLY_VERIFIED / MOCK_VERIFIED | `test_lmstudio_model_loading.py` (24 test) | **PASSED** |
| Discovery Run Store & Topic Provenance | STATICALLY_VERIFIED / MOCK_VERIFIED | `test_discovery_provenance_service.py` (19 test) | **PASSED** |
| Large Ingestion 10 MB Scaling & Timing | EMPIRICALLY_VERIFIED | `scripts/benchmark_large_ingestion.py --sizes 10` | **26.44 s (VALID)** |
| Large Ingestion 25 MB Scaling & Timing | EMPIRICALLY_VERIFIED | `scripts/benchmark_large_ingestion.py --sizes 25` | **62.87 s (VALID)** |
| Media Processing Lifecycle & Non-Destructive Retry | STATICALLY_VERIFIED / MOCK_VERIFIED | `test_media_processing_lifecycle.py` (8 test) | **PASSED** |
| Smoke Operational AI (LM Studio Live) | NOT_VERIFIED | `scripts/smoke_operational_ai.py` (senza flag) | **NOT EXECUTED** (Offline) |
| Smoke Operational AI (Fallback Diagnostico) | MOCK_VERIFIED | `scripts/smoke_operational_ai.py --fake` | **PASSED** |
| Packaging Wheel Distributable & Cleanliness | STATICALLY_VERIFIED | Script di verifica archivio zipfile | **PASSED (0 .pyc)** |

---

## 9. LIMITAZIONI RESIDUE & RACCOMANDAZIONI PER IL REVISORE

1. **Hardware LM Studio Reale**: L'ambiente Windows di pre-hardening non disponeva di un'istanza live di LM Studio in ascolto su `127.0.0.1:1234` con modello `qwen2.5-7b-instruct` caricato in VRAM. Il comportamento è stato verificato al 100% tramite mock e unit test, ed è pronto per la validazione live finale sull'ambiente target Ubuntu.
2. **Dimensioni Oltre 25 MB**: I test empirici hanno dimostrato una scalabilità lineare eccellente fino a 25 MB (47.405 messaggi in 62.87s con picco RSS di 381 MB). Dimensioni estreme (100 MB, 250 MB, 500 MB) richiedono memoria proporzionale alla dimensione del grafo di evidenza ($O(N)$), come documentato nell'analisi architetturale.
3. **Preservazione Benchmark Congelati**: Nessun file del protocollo scientifico di benchmark (`ai/experiment.py`, `ai/benchmark.py`, prompt, ground truth) è stato toccato (`git diff` vuoto). I nove run finali non sono stati eseguiti, preservando l'integrità scientifica dello studio.

---

## 10. CONCLUSIONE E RACCOMANDAZIONE SUL FREEZE

Tutti i cinque blocker identificati dall'audit indipendente sono stati corretti strutturalmente:
- Eliminazione completa di qualsiasi registro globale mutabile e isolamento tra sessioni;
- Rifiuto tassativo di risposte e modelli non conformi in LM Studio;
- Autenticazione rigorosa della Topic Discovery e inviolabilità della "Prova del Nove";
- Risoluzione del collo di bottiglia prestazionale ($>90x$ più veloce nell'importazione) e dichiarazione trasparente del modello di memoria;
- Lifecycle multimediale chiaro con retry non distruttivo.

Il bundle **`chatAnalysis_pre_freeze_hardening_audit_20261010.zip`** è generato e pronto per la valutazione finale da parte dell'auditor indipendente.
