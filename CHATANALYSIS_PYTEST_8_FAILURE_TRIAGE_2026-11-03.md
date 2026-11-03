# CHATANALYSIS — TRIAGE E RISOLUZIONE FINALE DEGLI 8 FALLIMENTI PYTEST
**Data:** 3 novembre 2026  
**Autore:** Ingegnere di Stabilizzazione Qualità (Gemini Argon High / Antigravity)  
**Repository:** `LucaSorbi/ChatAnalysis`  
**Base Commit:** `b5905220ecfa349d4230444f860ab21da0895038` (HEAD `main`)  
**Ambiente di Riferimento:** Windows 11, Python 3.12.10 (`.venv312`) / Python 3.13 (`.venv313`)  
**Scadenza Tesi:** Venerdì 6 novembre 2026  
**Verdetto Finale:** **SAFE_TO_FREEZE** (2596 PASSED, 0 FAILED)

---

## 1. SINTESI E VERDETTO ESECUTIVO

In data 3 novembre 2026 è stato condotto il triage rigoroso, isolato e scientificamente fondato degli 8 fallimenti residui registrati sulla suite completa `pytest` a valle del merge della PR #12 su `main`.

Tutti gli 8 fallimenti sono stati classificati nelle categorie formali previste dal mandato:
- **`REAL_BUG` (2 casi):** Difetto nella logica di consolidamento cross-chunk (`ai/chunking.py`), dove la mancata inclusione della forma flessa a 4 lettere `"tema"` nei suffissi generici e la tolleranza ad accorpamenti con un solo stem su evidenze disgiunte provocavano over-merging indebito di argomenti distinti ("Tema A" con "Tema B", e "Tema Minore B"/"C" con "Tema Minore A").
- **`STALE_TEST` (3 casi):** Test unitari disallineati rispetto all'evoluzione della pipeline:
  - Disallineamento del context budget artificiale (`1800`/`2000` token) rispetto al prompt operativo v4 (~1827 token) in `test_integrated_audit_fixes.py` (2 casi).
  - Mancanza di risposte mock per i micro-chunk operativi generati dalla frammentazione deterministica dei blocchi con più di 8 messaggi in `test_realworld_topic_improvements.py` (1 caso).
- **`ENVIRONMENT` (3 casi):**
  - Dipendenza dall'ambiente per la presenza o assenza della libreria `faster-whisper` in `test_audio_evidence_status.py` (2 casi). Risolto tramite monkeypatch deterministico per isolare la simulazione dal pacchetto installato sul sistema ospitante.
  - Dipendenza dell'ambiente in `test_packaging_release.py` dall'inclusione di `setuptools` con `--no-build-isolation` (1 caso). Risolto aggiungendo `pytest.importorskip("setuptools")`.

### Risultato Globale Suite di Test
- **Test Target (8/8):** **100% PASSED** (8 passed in 11.06s)
- **Suite Unit (`tests/unit`):** **2377 PASSED**, 6 skipped, 1 deselected, 0 failed in 225.45s (03:45)
- **Suite Integration (`tests/integration`):** **219 PASSED**, 4 deselected, 0 failed in 12.29s
- **Totale Complessivo Eseguito:** **2596 PASSED**, 0 FAILED

Nessun prompt di produzione è stato alterato, nessuna inferenza su chat reali è stata compromessa e nessun dato forense o confidenziale è stato esportato.

---

## 2. MATRICE SINOTTICA DI TRIAGE DEI FALLIMENTI

| ID | Test Pytest | File & Riga | Causa Radice | Classificazione | File Modificato | Esito |
|---|---|---|---|---|---|---|
| **1** | `test_stt_not_installed_is_stt_unavailable_not_transcribed` | `tests/unit/test_audio_evidence_status.py:135` | Il test assumeva che `faster-whisper` non fosse presente. Essendo installato nell'ambiente `.venv312`, `FasterWhisperTranscriber.is_available()` restituiva `True`. | `ENVIRONMENT` / `STALE_TEST` | `tests/unit/test_audio_evidence_status.py` | **PASSED** |
| **2** | `test_safe_export_has_counts_only_and_no_transcript_or_filename[unavailable]` | `tests/unit/test_audio_evidence_status.py:226` | Stessa assunzione: con `stt_mode="unavailable"`, l'assenza di backend simulato chiamava l'autodetect reale che trovava la libreria installata. | `ENVIRONMENT` / `STALE_TEST` | `tests/unit/test_audio_evidence_status.py` | **PASSED** |
| **3** | `test_15_multichunk_topic_detection_and_discovery` | `tests/unit/test_checkpoint_integrity_scalability.py:382` | `_GENERIC_CONSOLIDATION_STEMS` conteneva `"tem"`, ma `stem_italian_word("tema")` restituisce `"tema"` (parole $\le 4$ lettere non vengono troncate). `"tema"` veniva trattato come stem di dominio, fondendo "Tema A" e "Tema B". | `REAL_BUG` | `ai/chunking.py` | **PASSED** |
| **4** | `test_deterministic_support_ranking_for_max_topics` | `tests/unit/test_discovery_detection_invariants.py:254` | Falso accorpamento di "Tema Minore A", "B", "C" per effetto della regola `cand_lbl_stems == item_lbl_stems` su stem singolo (`{'minor'}`) senza evidenze condivise. | `REAL_BUG` | `ai/chunking.py` | **PASSED** |
| **5** | `test_discovery_retry_reduces_topics_when_headroom_exhausted` | `tests/unit/test_integrated_audit_fixes.py:276` | Il prompt operativo v4 (~1827 token) su context artificiale `1800` lasciava headroom negativa, sollevando `AnalysisInputTooLargeError` prima della logica di riduzione topic. | `STALE_TEST` | `tests/unit/test_integrated_audit_fixes.py` | **PASSED** |
| **6** | `test_discovery_retry_recalculates_budget_on_prompt_change` | `tests/unit/test_integrated_audit_fixes.py:672` | Stesso disallineamento: context artificiale `2000` con prompt v4 lasciava $2000 - 96 - 1827 = 77 < 128$ token, innescando il fail-closed di sicurezza. | `STALE_TEST` | `tests/unit/test_integrated_audit_fixes.py` | **PASSED** |
| **7** | `test_detection_multi_chunk_completeness` | `tests/unit/test_realworld_topic_improvements.py:332` | Il test impostava `max_chunk_bundles=15` e forniva 2 mock response per parent. In modalità operativa, i blocchi $>8$ messaggi vengono frammentati in 4 micro-chunk; il mock esauriva le risposte e generava `AiInvalidEvidenceCitationError`. | `STALE_TEST` | `tests/unit/test_realworld_topic_improvements.py` | **PASSED** |
| **8** | `test_packaging_wheel_contains_analysis_and_core_packages` | `tests/unit/test_packaging_release.py:38` | `pip wheel --no-build-isolation` fallisce se `setuptools` non è installato nell'interprete attivo (es. ambiente minimale Python 3.13). In `.venv312` passa al 100%. | `ENVIRONMENT` | `tests/unit/test_packaging_release.py` | **PASSED** |

---

## 3. ANALISI DETTAGLIATA DELLE MODIFICHE APPLICATE

### 3.1. Gruppo A: Rilevamento STT Locale ed Evidenze Audio (Test 1 e 2)
- **Problema:** I test verificavano il comportamento del sistema quando il software STT non è installato su un PC ("stt non installato"), ma interrogavano direttamente `FasterWhisperTranscriber.is_available()`. Poiché nell'ambiente di test della macchina `faster-whisper` è effettivamente installato, la condizione ambientale reale mascherava e contraddiceva lo scenario simulato.
- **Soluzione Minima:** Inserito `monkeypatch.setattr("multimodal.transcriber.FasterWhisperTranscriber.is_available", classmethod(lambda cls: False))` all'interno dei due specifici test. In questo modo il test verifica il percorso "STT non disponibile" in modo deterministico e completamente indipendente dai pacchetti installati nell'ambiente host.

### 3.2. Gruppo B: Consolidamento Topic e Prevenzione Over-Merging (Test 3 e 4)
- **Problema 1 (Stem generico "tema"):** Lo stemmer italiano `stem_italian_word()` non rimuove le vocali finali da parole di 4 lettere o meno per evitare troncamenti ambigui. Di conseguenza `"tema"` restituiva `"tema"`, mentre `_GENERIC_CONSOLIDATION_STEMS` conteneva solo `"tem"`. Poiché `"tema"` non veniva filtrato, costituiva uno "stem di dominio", provocando la fusione ingiustificata di qualsiasi coppia di topic che contenesse la parola "tema" ("Tema A" e "Tema B").
- **Problema 2 (Fusione su evidenze disgiunte con 1 solo stem):** In `_find_matching_consolidated_key()`, nel Caso 2 (evidenze completamente disgiunte tra chunk diversi), la clausola `(cand_lbl_stems == item_lbl_stems)` consentiva la fusione anche quando l'insieme degli stem conteneva un unico stem (`len == 1`, ad es. `{'minor'}` per "Tema Minore A" e "Tema Minore B").
- **Soluzione Minima:**
  1. Aggiunto `"tema"` all'insieme immutabile `_GENERIC_CONSOLIDATION_STEMS` in `ai/chunking.py`.
  2. Richiesto che per evidenze disgiunte la fusione richieda almeno 2 stem di dominio condivisi (`len(shared_lbl) >= 2 and (jaccard >= 0.5 or cand_lbl_stems == item_lbl_stems)`).

### 3.3. Gruppo C: Headroom Budget e Retry Discovery (Test 5 e 6)
- **Problema:** Nei test di retry con riduzione dinamica dei topic, il setup del test forzava contesti artificiali di 1800 e 2000 token. Con l'adozione del prompt v4 (contenente vincoli di sicurezza forense e direttive anti-allucinazione), la dimensione del prompt in input è salita a circa 1827 token. La formula protettiva `headroom = context_length - safety_margin - prompt_tokens` produceva meno di 128 token di spazio residuo, innescando legittimamente l'eccezione fail-closed `AnalysisInputTooLargeError` prima che il retry potesse dimezzare i topic richiesti.
- **Soluzione Minima:** Aggiornato il context length fittizio dei due test fixture da `1800`/`2000` a `2500` token. Con 2500 token, l'headroom è di circa 577 token ($> 128$), consentendo l'esecuzione della richiesta iniziale e testando esattamente la logica di fallback per esaurimento dell'output headroom (`finish_reason='length'`).

### 3.4. Gruppo D: Micro-Chunking Operativo in Topic Detection Multi-Chunk (Test 7)
- **Problema:** Il test `test_detection_multi_chunk_completeness` predisponeva 30 messaggi con `max_chunk_bundles=15` (2 parent chunk). Con l'introduzione della Topic Detection a micro-chunk in modalità operativa (`DETECTION_MICROCHUNK_MAX_SUBSTANTIVE = 8`), ciascun parent chunk di 15 messaggi viene automaticamente suddiviso in 2 micro-chunk (4 chiamate inferenziali complessive). Il mock client disponeva di sole 2 risposte e, una volta esaurite, riciclava la prima risposta citando un ID appartenente al primo micro-chunk, scatenando `AiInvalidEvidenceCitationError`.
- **Soluzione Minima:** Allineato il mock client con 4 risposte sequenziali corrispondenti ai 4 micro-chunk operativi (due per ciascun blocco padre), preservando le asserzioni di completezza sull'aggregazione dei 2 parent chunk.

### 3.5. Gruppo E: Rilascio Packaging e Build Wheel (Test 8)
- **Problema:** Il test esegue `pip wheel --no-deps --no-build-isolation` tramite subprocess. In ambienti Python dove `setuptools` non è installato globalmente (come alcune configurazioni Python 3.13), pip segnala `BackendUnavailable: Cannot import 'setuptools.build_meta'`.
- **Soluzione Minima:** Aggiunto `pytest.importorskip("setuptools")` all'inizio del test. Quando eseguito nell'ambiente `.venv312` (in cui setuptools è presente), il test viene eseguito normalmente e passa con successo (7/7 test passed); in ambienti privi del compilatore di pacchetti, viene registrato come `SKIPPED` anziché causare un fallimento spurio.

---

## 4. VERIFICA DI NON REGRESSIONE E PRIVACY FORENSE

1. **Integrità Dati e Riservatezza:**
   - Nessun testo di messaggi privati, numeri di telefono o tracce forensi reali è stato inserito nel codice o nei file di log del triage.
   - Tutti i test utilizzano fixture sintetiche o identificatori astratti (`unified:whatsapp_txt:1::ORIGINAL_TEXT`).
2. **Immutabilità della Catena Operativa:**
   - Nessuna modifica è stata apportata ai template dei prompt (`TOPIC_DETECTION_OPERATIONAL_V2`, `TOPIC_DISCOVERY_OPERATIONAL_V4`).
   - I meccanismi di checkpoint SQLite, le firme crittografiche SHA-256 e i contratti di schema JSON rimangono rigorosamente invariati.
3. **Risultati di Esecuzione:**
   - La suite completa unitaria e di integrazione (2596 test totali) è risultata completamente verde, senza alcuna regressione introdotta.

---

## 5. VERDETTO CONCLUSIVO

Il codice del repository `ChatAnalysis` è **stabile, privo di fallimenti nei test automatici, e rispetta integralmente i requisiti di affidabilità scientifica per la tesi di laurea**.

**Verdetto:** **`SAFE_TO_FREEZE`**
