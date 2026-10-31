# CHATANALYSIS — RAPPORTO INTEGRATO DI STABILIZZAZIONE WINDOWS, AUDIT CAPITOLO 3 E PULIZIA REPOSITORY

**Data**: 21 Ottobre 2026  
**Autore / Candidato**: Luca Sorbi — Tesi Triennale in Informatica  
**Progetto**: ChatAnalysis — Piattaforma Forense di Analisi & Ricerca Chat  
**Ambiente di Riferimento**: Windows 11 64-bit nativo, Python 3.14.7 (.venv locale protetto), LM Studio 0.3.x (Qwen2.5-7B-Instruct Q4_K_M)

---

## 1. SINTESI ESECUTIVA DELL'INTERVENTO

Il presente rapporto documenta il completamento rigoroso e sequenziale delle quattro fasi di intervento stabilite:

1. **FASE A — Audit e Stabilizzazione Tecnica**:
   - **Criticità F1 (Ownership dei file temporanei)**: eliminata radicalmente la fiducia nel solo prefisso del percorso (`chatanalysis_snap_`) in `scripts/preflight_checkpoint_resume.py`. Introdotto il registro deterministico del ciclo di vita `_SnapshotOwnershipTracker` con istanza globale `_OWNERSHIP_TRACKER`. Implementato il rifiuto fail-closed di directory falsificate e la protezione atomica contro la sostituzione concorrente (TOCTOU). Sviluppate 5 nuove regressioni dedicate (`test_146`..`test_150`), tutte superate al 100% (164 passati su 164 eseguiti).
   - **Criticità Provenance (Topic aggregati da più chunk)**: corretto il disallineamento in `_authenticate_recovered_discovery_run_for_verification()` in `analysis/service.py`. L'autenticazione delle run di Discovery recuperate da SQLite non confronta più impropriamente il topic aggregato con un singolo chunk parziale, ma ricostruisce deterministicamente il risultato aggregato tramite `aggregate_topic_discovery()` sui checkpoint validati. Sviluppati e superati i test `test_11` e `test_12` in `tests/unit/test_priority1_recovered_discovery_prova_del_nove.py`.
   - **Invarianza del Benchmark Congelato**: `ai/experiment.py` e `ai/benchmark.py` risultano assolutamente invariati (0 diff lines).

2. **FASE B — Raccolta Sorgenti e Documentazione per il Capitolo 3**:
   - Redatto `CHATANALYSIS_CAPITOLO3_ANALISI_TECNICA.md`: mappa tecnica esaustiva di tutti i modelli dati (`RawRecord`, `NormalizedRecord`, `UnifiedMessage`, `MessageEvidenceBundle`, `ConversationEvidenceDocument`, `TopicQuery`, `DiscoveredTopic`, `ConsistencyResult`), algoritmi di chunking, token budget, lifecycle dei checkpoint e matrice di tracciabilità con classificazione formale dello stato di verifica (`TEST_VERIFIED`, `MOCK_VERIFIED`, `STATICALLY_VERIFIED`).
   - Redatto `CHATANALYSIS_CAPITOLO3_STRUTTURA_MANOSCRITTO.md`: schema analitico per la stesura accademica delle sezioni da 3.1 a 3.8 con linguaggio universitario rigoroso e 5 diagrammi Mermaid completamente modificabili.

3. **FASE C — Pulizia e Alleggerimento del Repository**:
   - Generato `CHATANALYSIS_FILE_INVENTORY_BEFORE.csv` (16.534 file classificati).
   - Eseguito backup preventivo verificabile fuori dal repository: `chatanalysis_cleanup_backup_20261021.zip` con relativo manifest SHA-256 (`chatanalysis_cleanup_backup_20261021_manifest.sha256`, 850 elementi).
   - Redatto `CHATANALYSIS_CLEANUP_PLAN.md` con analisi del rischio.
   - Rimossi 841 file di cache e artefatti di build rigenerabili (`build/`, `dist/`, `.pytest_cache/`, `egg-info`, logs e `*.pyc`).
   - Archiviati esternamente 7 script di audit storici (`scripts/create_*_archive.py`) in `chatanalysis_historical_audit_scripts/`.
   - **Conservato per prudenza** `scripts/create_scalability_audit_archive.py` perché attivamente verificato da `tests/unit/test_packaging_release.py`.
   - Redatto `CHATANALYSIS_CLEANUP_REPORT.md` e generato `CHATANALYSIS_FILE_INVENTORY_AFTER.csv` (15.686 file finali; riduzione dell'80,05% dello spazio workspace fuori da `.venv`/`.git`).

4. **FASE D — Suite di Test e Verifiche Post-Cleanup**:
   - Rieseguite tutte le suite di test: 2.018 test passati (1.799 unitari + 219 integrazione), 0 fallimenti, 5 saltati (POSIX only skips). Compilazione Python 100% PASS. Smoke test operativo Windows 100% SUCCESSO.

---

## 2. RISOLUZIONE CRITICITÀ F1 — OWNERSHIP DEI FILE TEMPORANEI

### 2.1 Analisi della Vulnerabilità Originaria
La precedente implementazione di `_perform_atomic_safe_unlink()` in `scripts/preflight_checkpoint_resume.py` validava la rimovibilità di una directory temporanea basandosi unicamente sul prefisso del percorso:
```python
# CODICE VULNERABILE ORIGINARIO:
dest_file.parent.name.startswith("chatanalysis_snap_")
```
Questo controllo era ingannevole: un processo concorrente o una prova di audit poteva creare manualmente una directory non posseduta con nome `chatanalysis_snap_not_actually_owned`, consentendo a `_perform_atomic_safe_unlink()` di cancellare file arbitrari al suo interno o di soccombere a sostituzioni concorrenti (TOCTOU) prima della `unlink()`.

### 2.2 Architettura della Correzione
È stato introdotto il tracciamento formale dell'ownership tramite `_SnapshotOwnershipTracker`:
1. **Registrazione dinamica al momento della creazione**: le sole directory create tramite `tempfile.mkdtemp()` e i singoli file creati tramite `open(..., O_CREAT | O_EXCL)` dall'invocazione corrente vengono registrati nel tracker globale `_OWNERSHIP_TRACKER`.
2. **Doppia verifica atomica di rimozione**:
   - Verifica di appartenenza rigorosa al set delle directory e dei file registrati.
   - Apertura del file tramite file descriptor (`os.open`) con verifica che il file descriptor appartenga al file registrato (stat matching) prima di invocare `os.unlink()`.
3. **Fail-Closed su ownership indeterminata**: se il percorso non è esplicitamente registrato come risorsa posseduta, la rimozione viene abortita restituendo esito fail-closed senza toccare il file.
4. **Preservazione dell'integrità dei database sorgenti**: nessun tentativo di cleanup può mai alterare i percorsi sorgenti del database SQLite principale, del file `-wal` o del file `-shm`.

### 2.3 Evidenze Sperimentali delle Regressioni
Nel modulo `tests/unit/test_checkpoint_preflight_resume.py` sono state aggiunte le seguenti 5 regressioni:
- `test_146_spoofed_directory_with_valid_prefix_rejected_fail_closed`: verifica che una directory falsificata con prefisso valido venga categoricamente rifiutata (**PASS**).
- `test_147_competing_file_replaced_after_final_check_survives`: verifica che un file concorrente sostituito dopo la verifica sopravviva intatto (**PASS**).
- `test_148_genuinely_owned_resources_cleaned_up`: verifica che le risorse legittimamente possedute vengano rimosse correttamente al termine del ciclo di vita (**PASS**).
- `test_149_indeterminate_ownership_fail_closed`: verifica il comportamento fail-closed in presenza di ownership indeterminata (**PASS**).
- `test_150_source_database_immutability_preserved_during_failed_cleanup`: verifica l'assoluta immutabilità dei database sorgenti durante fallimenti nel cleanup (**PASS**).

---

## 3. RISOLUZIONE CRITICITÀ PROVENANCE — TOPIC AGGREGATI DA PIÙ CHUNK

### 3.1 Analisi della Divergenza
Nel metodo `aggregate_topic_discovery()`, evidenze provenienti da chunk differenti (es. Chunk 1 con topic "Deposito" ed evidenza `E1`, Chunk 2 con topic "Deposito" ed evidenza `E2`) vengono legittimamente unite nel topic aggregato ("Deposito", evidenze `['E1', 'E2']`).
Tuttavia, la funzione di autenticazione `_authenticate_recovered_discovery_run_for_verification()` in `analysis/service.py` tentava di confrontare il topic aggregato direttamente contro un singolo chunk salvato nel checkpoint, causando un falso rigetto (`TopicProvenanceError`) pur a fronte di dati completamente autentici.

### 3.2 Correzione Implementata
La funzione è stata riscritta per operare una ricostruzione deterministica a due stadi:
1. **Validazione dei singoli chunk salvati**: ogni chunk registrato nel checkpoint SQLite viene validato individualmente verificando firma, documento e fingerprint.
2. **Riesecuzione deterministica dell'aggregazione**: la funzione invoca `aggregate_topic_discovery()` sull'insieme completo dei chunk validati, producendo il `TopicDiscoveryResult` aggregato di riferimento.
3. **Confronto deterministico dell'aggregato**: il topic selezionato per la Prova del Nove viene confrontato con il topic aggregato ricostruito (unione evidence IDs, ranking, deduplicazione).
4. **Conservazione dello status PARTIAL vs COMPLETE**: se la run salvata presenta chunk mancanti o falliti (es. 25/26), la run viene autenticata come `PARTIAL` e non viene promossa indebitamente a `COMPLETE`.

### 3.3 Verifiche Effettuate
In `tests/unit/test_priority1_recovered_discovery_prova_del_nove.py`:
- `test_11_multi_chunk_aggregated_topic_authenticated_for_verification`: valida l'aggregazione dello stesso topic da 2 chunk distinti (`E001` da chunk 0, `E002` da chunk 1) con autenticazione della run recuperata e successiva Prova del Nove (**PASS**).
- `test_12_multi_chunk_partial_discovery_preserves_partial_and_authenticates`: valida il recupero di una Discovery multi-chunk parziale (chunk fallito), dimostrando che lo status `PARTIAL` viene preservato e la verifica semantica procede lecitamente sulle sole evidenze autenticate (**PASS**).

---

## 4. INVARIANTI OPERATIVI DI TOPIC DISCOVERY E TOPIC DETECTION

### 4.1 Topic Discovery
- **Chunking Deterministico**: suddivisione dei documenti basata su budget di caratteri/token con overlap calibrato per preservare il contesto conversazionale.
- **Alias delle Evidenze**: mappatura sicura degli identificativi interni in alias compatti (es. `E001`, `E002`) inviati al prompt LM Studio per minimizzare il consumo di token e schermare percorsi interni.
- **Risoluzione e Validazione**: risposte JSON validate contro schema rigido. Qualsiasi alias non presente nella tabella di binding genera `INVALID_EVIDENCE_ID`.
- **Analisi del Caso LIVE Ubuntu (1.049 messaggi, 25/26 chunk completati)**:
  Il fallimento del chunk 11 su 26 con errore `INVALID_EVIDENCE_ID` su 2 tentativi è imputabile alla generazione da parte del modello di un alias allucinato non compreso nella finestra del chunk specifico. L'implementazione gestisce correttamente questo scenario: il chunk viene registrato come fallito, l'errore viene esposto nella UI diagnostica, la Discovery viene classificata `PARTIAL` e i restanti 25 chunk validati rimangono disponibili per l'analisi senza compromettere l'intera sessione.

### 4.2 Topic Detection
- **Regole di Decisione Tematica**:
  - `PRESENT`: solo se corroborato da evidenze valide e citazioni coerenti nel testo.
  - `ABSENT`: solo a fronte di copertura completa validata e assenza di riscontri.
  - `UNCERTAIN`: quando la copertura è parziale (es. chunk falliti) e non è stato dimostrato un `PRESENT`.
- **Analisi del Caso LIVE Ubuntu (17/26 chunk completati)**:
  L'esito `UNCERTAIN` assegnato alla run parziale (9 chunk falliti per timeout/contesto) rispetta rigorosamente i canoni forensi: non è possibile dichiarare `ABSENT` senza certezza di copertura integrale.

---

## 5. VERIFICA DELLA CONSISTENZA SEMANTICA MEDIANTE INFERENZE DISTINTE

Nella tesi accademica si adotta tassativamente la denominazione scientifica:
**«Verifica della consistenza semantica mediante inferenze distinte»** (abbandonando qualsiasi dicitura informale).

### Principi Architetturali Verificati:
1. **Due Inferenze Completamente Distinte**: l'inferenza di Discovery (esplorativa, senza etichette predeterminate) e l'inferenza di Detection (di controllo mirato su un topic specifico) operano con run_id e prompt differenti.
2. **Nessun Leak di Evidenze**: gli alias o gli evidence ID scoperti dalla Discovery non vengono passati come suggerimento al prompt di Detection.
3. **Consistenza Semantica != Correttezza Fattuale**: il `ConsistencyService` valuta la coerenza logica e l'accordo tra due interrogazioni del modello sullo stesso substrato documentale forense; la consistenza semantica non garantisce di per sé la verità materiale dei fatti storici citati nelle chat.

---

## 6. TABELLA RIASSUNTIVA DELLE ESECUZIONI DI TEST (FASE D POST-CLEANUP)

Tutti i test sono stati eseguiti con successo in data 21 Ottobre 2026 su Windows 11 nativo con Python 3.14.7:

| Suite di Test | Comando Effettivo | Exit Code | Superati | Falliti | Saltati | Stato |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Compilazione Sintattica** | `python -m compileall -q -x "\.venv\|\.git" .` | 0 | Tutti i file | 0 | 0 | **PASS** |
| **F1 Temp Ownership & Preflight** | `pytest tests/unit/test_checkpoint_preflight_resume.py -q` | 0 | 164 | 0 | 4 (POSIX) | **PASS** |
| **Discovery Provenance & Prova Nove** | `pytest tests/unit/test_priority1_recovered_discovery_prova_del_nove.py -q` | 0 | 12 | 0 | 0 | **PASS** |
| **Discovery Provenance Hardening** | `pytest tests/unit/test_discovery_provenance_hardening.py tests/unit/test_discovery_provenance_service.py -q` | 0 | 65 | 0 | 0 | **PASS** |
| **Invarianti Discovery/Detection** | `pytest tests/unit/test_discovery_detection_invariants.py tests/unit/test_topic_discovery_detection_stabilization.py -q` | 0 | 21 | 0 | 0 | **PASS** |
| **Importers (WhatsApp & Cellebrite)** | `pytest tests/unit/test_base_importer.py tests/unit/test_cellebrite_*.py tests/unit/test_whatsapp_*.py -q` | 0 | 320 | 0 | 0 | **PASS** |
| **Packaging & Release Gate** | `pytest tests/unit/test_packaging_release.py -q` | 0 | 7 | 0 | 0 | **PASS** |
| **Streamlit Windows Startup & Navigation** | `pytest tests/unit/test_priority3_streamlit_windows_startup.py -q` | 0 | 4 | 0 | 0 | **PASS** |
| **Resto della Suite Unitaria** | `pytest tests/unit -q --ignore=...preflight...` | 0 | 1.635 | 0 | 1 (1 desel) | **PASS** |
| **Suite di Integrazione Completa** | `pytest tests/integration -q` | 0 | 219 | 0 | 0 (1 desel) | **PASS** |
| **Smoke Test Operativo Windows** | `powershell -ExecutionPolicy Bypass -File scripts\windows_setup_and_verify.ps1 -SkipInstall -SkipTests -MockOnly` | 0 | 18 controlli | 0 | 0 | **PASS** |
| **Benchmark Congelato (Diff)** | `git diff ai/experiment.py ai/benchmark.py` | 0 | 0 righe | 0 | 0 | **FROZEN (100%)** |

**Totale Test di Regressione Superati**: **2.018 test passati con successo (0 fallimenti)**.

---

## 7. STATO DEI PROBLEMI APERTI E RACCOMANDAZIONI OPERATIVE

1. **Esecuzione LIVE su Windows Definitivo**:
   - I test sopra documentati certificano la correttezza algoritmica, l'integrità strutturale e il funzionamento del runtime (MOCK_VERIFIED e TEST_VERIFIED).
   - L'esecuzione LIVE richiederà l'avvio locale di LM Studio su `http://127.0.0.1:1234` con caricamento del modello `qwen2.5-7b-instruct` quantizzato `Q4_K_M` e context length impostata a 8.192 token.
2. **PowerShell Execution Policy su Windows 11**:
   - Come riscontrato durante i test, la policy di default di Windows 11 può bloccare l'esecuzione diretta dei file `.ps1`. Si raccomanda di utilizzare il flag `-ExecutionPolicy Bypass` come descritto nel runbook.
3. **Disponibilità Documentazione Capitolo 3**:
   - Sono pronti per l'audit indipendente e per la consultazione:
     - `CHATANALYSIS_CAPITOLO3_ANALISI_TECNICA.md`
     - `CHATANALYSIS_CAPITOLO3_STRUTTURA_MANOSCRITTO.md`
     - `CHATANALYSIS_FILE_INVENTORY_BEFORE.csv`
     - `CHATANALYSIS_CLEANUP_PLAN.md`
     - `CHATANALYSIS_CLEANUP_REPORT.md`
     - `CHATANALYSIS_FILE_INVENTORY_AFTER.csv`
