# INVENTARIO APPTEST STREAMLIT (`streamlit.testing.v1.AppTest`)

Questo documento censisce tutti i test headless di interfaccia utente basati sul framework `AppTest` di Streamlit, tracciando coperture effettive, mock utilizzati e lacune di test (*test coverage gaps*).

---

## 1. Censimento Dettagliato degli AppTest Esistenti

### A. `tests/unit/test_ui_smoke.py`
| Test | Pagina / Sezione | Dataset Mode | Pulsanti Cliccati | Mock/Fake AI? | LM Studio Reale? | Cosa Verifica |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `test_apptest_startup_no_errors` | 1. Panoramica | None (unloaded) | Nessuno | N/A | NO | Avvio pulito senza eccezioni, titolo visualizzato, radio navigazione presente |
| `test_apptest_page_1_panoramica_default` | 1. Panoramica | None (unloaded) | Nessuno | N/A | NO | Messaggio informativo "Nessun dataset caricato" |
| `test_apptest_load_demo_dataset_flow` | 2. Importazione | DEMO | "Carica Dataset Dimostrativo" | Fake data | NO | Transizione stato a dataset caricato in memoria DEMO |
| `test_apptest_page_3_browse_messages` | 3. Esplora Conversazione | DEMO | "Carica Dataset Dimostrativo" | Fake data | NO | Visualizzazione messaggi in ordine naturale con selettori |
| `test_apptest_page_4_search_deterministic` | 4. Ricerca | DEMO | "Carica Dataset Dimostrativo", input ricerca | Deterministic search | NO | Ricerca deterministica per termine con match mode |
| `test_apptest_page_5_topics_demo` | 5. Analisi topic | DEMO | "Carica Dataset Dimostrativo" | Precalcolati demo | NO | Rendering tab Topic Detection e Topic Discovery con card e badge |
| `test_apptest_page_6_system_status` | 6. Sistema / Stato | N/A | Radio selezione pagina | N/A | NO | Dati di sistema, assenza di leak token o chiavi |
| `test_apptest_all_pages_cycle` | Pagine 1-6 | DEMO | Navigazione sequenziale radio | Fake data | NO | Assenza di crash o eccezioni unhandled nel ciclo completo |
| `test_apptest_evidence_selection_roundtrip` | 3. Esplora Conversazione | DEMO | Selectbox evidenza | N/A | NO | Selezione evidenza e dettaglio espandibile |
| `test_apptest_search_mode_change` | 4. Ricerca | DEMO | Radio match mode | N/A | NO | Esecuzione ricerca con match mode alternativi (EXACT, ALL_TERMS) |
| `test_apptest_empty_dataset_navigation` | Pagine 1-5 | None (unloaded) | Navigazione a dataset vuoto | N/A | NO | Gestione difensiva con banner st.info anziché crash |

---

### B. `tests/unit/test_ui_manual_topic_detection.py`
| Test | Pagina / Sezione | Dataset Mode | Pulsanti Cliccati | Mock/Fake AI? | LM Studio Reale? | Cosa Verifica |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `test_12_interactive_streamlit_apptest_walkthrough` | 5. Analisi topic | DEMO | "Carica Dataset Dimostrativo", "🚀 Esegui Topic Detection", "🔬 Verifica con Topic Detection" | `FakeLocalLlmClient` | NO | Flusso completo: Area 1 (argomento libero), Area 2 (filtro cronologia), Tab Discovery con pulsante verifica topic scoperto |

---

### C. `tests/unit/test_operational_model_management.py`
| Test | Pagina / Sezione | Dataset Mode | Pulsanti Cliccati | Mock/Fake AI? | LM Studio Reale? | Cosa Verifica |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `test_17_streamlit_apptest_whatsapp_export_displayed` | 2. Importazione | N/A | Nessuno | N/A | NO | Opzione `"WhatsApp export chat (TXT / ZIP)"` presente nel selectbox |
| `test_18_streamlit_apptest_uploader_accepts_txt_and_zip` | 2. Importazione | N/A | Selectbox WhatsApp Export | N/A | NO | Widget file_uploader accetta tipi `['txt', 'zip']` |
| `test_41_apptest_system_status_neutral` | 6. Sistema / Stato | N/A | Radio pagina 6 | N/A | NO | Stato `READY - LM STUDIO LOCAL`, endpoint `http://127.0.0.1:1234` |
| `test_42_apptest_system_status_no_avx2_mention` | 6. Sistema / Stato | N/A | Radio pagina 6 | N/A | NO | Assenza di menzioni obsolete hardware AMD A8 / AVX2 |

---

### D. `tests/unit/test_large_file_upload.py`
| Test | Pagina / Sezione | Dataset Mode | Pulsanti Cliccati | Mock/Fake AI? | LM Studio Reale? | Cosa Verifica |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `test_apptest_uploader_has_max_size_configured` | 2. Importazione | N/A | Nessuno | N/A | NO | Parametro `max_upload_size_mb == 2048` nel protobuf dell'uploader |
| `test_apptest_ui_displays_discreet_2gb_note` | 2. Importazione | N/A | Nessuno | N/A | NO | Presenza didascalia discreta `"Dimensione massima file: 2 GB"` |

---

### E. `tests/integration/test_full_pipeline_e2e.py`
| Test | Pagina / Sezione | Dataset Mode | Pulsanti Cliccati | Mock/Fake AI? | LM Studio Reale? | Cosa Verifica |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `test_e2e_real_file_ai_isolation` | 2. Importazione -> 5. Analisi topic | FILE | Caricamento fixture sintetica reale | Nessuna AI chiamata | NO | Zero chiamate AI durante ingestion; presenza banner fase sperimentale su FILE |
| `test_e2e_streamlit_apptest_full_walkthrough` | Tutte le 6 pagine | DEMO | Walkthrough completo su tutte le pagine | Fake data | NO | Integrazione completa end-to-end senza errori Streamlit |

---

## 2. Matrice di Copertura per Requisiti Operativi

| Requisito Operativo | Copertura AppTest | Tipo Test | Note / Valutazione |
| :--- | :---: | :--- | :--- |
| **DatasetMode.DEMO completo** | **COPERTO** | AppTest | Navigazione, Topic Detection Area 1, Discovery card |
| **DatasetMode.FILE completo** | **PARZIALE** | E2E AppTest | Testato caricamento e isolamento; non testata interazione completa topic |
| **Topic Detection su FILE** | **NON COPERTO** | N/A | Manca un AppTest che carichi un file reale e prema "Esegui Topic Detection" |
| **Topic Discovery su FILE** | **NON COPERTO** | N/A | **ASSENTE DA CODICE**: Discovery su FILE non è implementato nella UI |
| **Discovery -> Detection su FILE** | **NON COPERTO** | N/A | Impossibile per assenza di card Discovery su FILE |
| **Discovery -> Detection su DEMO** | **COPERTO** | AppTest | `test_12_interactive_streamlit_apptest_walkthrough` con Fake Client |
| **Empty detection_results** | **COPERTO** | AppTest | Gestione banner informativo verificata |
| **Empty discovery_results** | **COPERTO** | AppTest | Gestione banner informativo verificata |
| **Real imported synthetic fixture** | **COPERTO** | AppTest | Testato in `test_e2e_real_file_ai_isolation` |
| **Upload TXT nativo** | **COPERTO** | AppTest | Verifica widget e opzione uploader |
| **Upload ZIP nativo** | **COPERTO** | AppTest | Verifica widget e opzione uploader |
| **Configurazione > 200 MB** | **COPERTO** | AppTest | Verifica `max_upload_size_mb == 2048` e config TOML |

---

## 3. Lacune di Copertura Rilevate (Gaps)

1. **Assenza di AppTest interattivo su `DatasetMode.FILE` con Topic Detection**:
   Tutti i test interattivi di Topic Detection che cliccano `st.button("🚀 Esegui Topic Detection")` operano su `DatasetMode.DEMO`. Non esiste un AppTest che:
   - Carichi un file reale o fixture sintetica tramite l'uploader di `DatasetMode.FILE`;
   - Navighi in "5. Analisi topic";
   - Esegua la Detection con mock client su un topic arbitrario su file reale.
2. **Assenza totale di Topic Discovery per `DatasetMode.FILE`**:
   Nessun AppTest può verificare Topic Discovery su file reali perché il pulsante e il wiring orchestrale non esistono nell'interfaccia né in `ui/application.py`.
3. **Nessun AppTest con LM Studio Locale Reale**:
   Tutti gli AppTest impiegano `FakeLocalLlmClient` o dati sintetici precalcolati. Il test live condizionale `RUN_LM_STUDIO_SMOKE` è skippato di default.
