# REPORT TECNICO SCIENTIFICO — PACKAGING GUARDRAILS & FIX FINALE DELLA RELEASE
**Progetto:** ChatAnalysis (WhatsApp Forensic Importer & Topic Analysis)  
**Data:** 17 Ottobre 2026  
**Ambiente di Riferimento:** Windows local workspace / Python 3.14.7 / Pytest 9.1.1  
**Destinazione Laboratorio:** Ubuntu Linux (GTX TITAN X 12GB Maxwell, Qwen 2.5 7B Instruct Q4_K_M, Context 8192)  
**Riferimento Archivio:** `chatAnalysis_scalability_packaging_guardrails_audit_20261017.zip`

---

## 1. OBIETTIVO E RISULTATI DELL'AUDIT

A seguito dell'audit indipendente su Linux che ha confermato l'efficacia delle ottimizzazioni CPU, la corrispondenza dei moduli della wheel e la stabilità dei benchmark scientifici congelati, è stato eseguito un intervento mirato ed esclusivo sui **guardrail di confezionamento (packaging)**, senza alterare il codice applicativo, i contratti forensi, il chunking, gli alias o la logica di ripresa dei checkpoint.

### Risultati Principali:
- **Duplicazione del manifest azzerata:** `MANIFEST_SHA256.txt` e `*.sha256` sono categoricamente esclusi dalla scansione ricorsiva dei file da archiviare; il manifest interno viene generato atomicamente una sola volta e non elenca mai se stesso.
- **Allowlist rigida per le fixture sintetiche:** La regola permissiva basata sull'estensione generica sotto `test_data/` è stata sostituita da un'allowlist stretta dei percorsi sintetici approvati. Qualsiasi database `.db`, `.sqlite` o archivio `.zip` non approvato viene tassativamente escluso dall'archivio di consegna.
- **Test di packaging e guardrail:** Aggiunti ed eseguiti con successo test automatici dedicati (`test_packaging_archive_double_run_no_duplicate_manifest` e `test_packaging_archive_rejects_unapproved_db_under_test_data`).
- **Verifica delle fixture e non-regressione:** 141/141 test pertinenti superati (inclusi tutti i test su `msgstore.db`, `wa.db` ed Entity Resolution).
- **Integrità scientifica e Git:** Benchmark scientifici congelati invariati al bit; nessun comando git di modifica eseguito.

---

## 2. MODIFICHE IMPLEMENTATE SUI GUARDRAIL DI PACKAGING

Nel file [scripts/create_scalability_audit_archive.py](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/scripts/create_scalability_audit_archive.py):

### 2.1. Esclusione Preventiva dei Manifest
```python
EXCLUDE_FILENAMES: frozenset[str] = frozenset({
    "MANIFEST_SHA256.txt",
    "MANIFEST.txt",
})

def should_pack_file(rel_str: str, file_name: str, file_suffix: str) -> bool:
    # 1. Esclusione manifest e file di checksum dalla scansione
    if file_name in EXCLUDE_FILENAMES or file_name.endswith(".sha256"):
        return False
    ...
```
Se un'esecuzione precedente o un'estrazione ha lasciato un `MANIFEST_SHA256.txt` nel repository root, questo non viene più aggiunto a `files_to_pack`. L'archivio conterrà una singola istanza deterministica inserita tramite `z.writestr("MANIFEST_SHA256.txt", manifest_content)` alla fine del processo.

### 2.2. Allowlist Rigida per le Fixture Sintetiche
```python
APPROVED_SYNTHETIC_FIXTURES: frozenset[str] = frozenset({
    "test_data/whatsapp_export/msgstore.db",
    "test_data/whatsapp_export/wa.db",
    "test_data/synthetic_smoke/synthetic_whatsapp_chat.zip",
})

def should_pack_file(rel_str: str, file_name: str, file_suffix: str) -> bool:
    ...
    # Guardrail per DB e ZIP: ammessi SOLO se presenti nell'allowlist approvata
    if file_suffix in {".db", ".sqlite", ".zip"}:
        return rel_str in APPROVED_SYNTHETIC_FIXTURES
    ...
```
Qualsiasi database reale, file SQLite di checkpoint o archivio zip estraneo eventualmente posizionato in `test_data/` o nella root viene bloccato e non entra nell'archivio.

---

## 3. TEST AUTOMATICI DEI GUARDRAIL DI PACKAGING

Nel modulo [tests/unit/test_packaging_release.py](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/tests/unit/test_packaging_release.py) sono stati aggiunti due test specialistici:

1. **`test_packaging_archive_double_run_no_duplicate_manifest`**:
   - Esegue la procedura di packaging due volte consecutive nella medesima directory temporanea.
   - Posiziona deliberatamente un vecchio `MANIFEST_SHA256.txt` nel repository fittizio prima del secondo run.
   - Verifica:
     - Assenza totale di entry duplicate nel file ZIP (`len(namelist) == len(set(namelist))`).
     - Presenza esattamente singola di `MANIFEST_SHA256.txt`.
     - Mancata inclusione o elencazione del vecchio manifest nel nuovo manifest.
     - Correttezza bit-for-bit di tutti gli hash registrati confrontati con i dati estratti dall'archivio.
   - **Esito:** `PASSED`.

2. **`test_packaging_archive_rejects_unapproved_db_under_test_data`**:
   - Crea un ambiente mock con fixture approvate (`msgstore.db`, `wa.db`, `synthetic_whatsapp_chat.zip`) e contemporaneamente inietta database fittizi e file zip non approvati (`unapproved_personal.db`, `unapproved_chat.sqlite`, `unapproved_archive.zip`, `root_leak.sqlite`).
   - Verifica che le sole fixture approvate compaiano nell'archivio e che tutti i file non approvati vengano rigorosamente esclusi.
   - **Esito:** `PASSED`.

---

## 4. RISULTATI DEI TEST PERTINENTI

Sono stati eseguiti esclusivamente i test pertinenti a packaging e fixture:
- `tests/unit/test_packaging_release.py` -> **3/3 PASSED** (inclusi test della wheel reale e isolamento import).
- `tests/unit/test_whatsapp_msgstore_importer.py` -> **56/56 PASSED**.
- `tests/unit/test_whatsapp_wa_importer.py` -> **51/51 PASSED**.
- `tests/unit/test_smoke_operational_ai.py` -> **31/31 PASSED**.
- **Totale parziale eseguito:** **141/141 PASSED (100% successo)** in 51 secondi.

---

## 5. INTEGRITÀ DEI BENCHMARK SCIENTIFICI E SICUREZZA GIT

I digest crittografici SHA-256 dei benchmark scientifici congelati sono stati ricalcolati e confermati invariati al 100%:
- `ai/experiment.py`: `91100c0a84cb4a8b7c2eef34a06cff9ec21aa6e6eec14ffd09ce6fea8bcee845`
- `ai/benchmark.py`: `dc57081dbce3463a8017aa26bb4e4a18889af0a3296853fe40968561d6d44d36`
- `run_benchmark.sh`: `7f9cae97df2fae1bc3b5964e639b9f80e3ac5a388ec9c3e20305ff64d48044a7`

Nessuna operazione Git (`git add`, `commit`, `push`, `pull`, `fetch`, `reset`) è stata eseguita.

---

## 6. VERIFICA DELLA WHEEL PYTHON IN `dist/`

- **File:** `dist/whatsapp_forensic_importer-0.1.0-py3-none-any.whl`
- **Dimensione:** 334.469 byte
- **SHA-256:** `f1bb9c0bb003e2a63ab24e294c4d00aba8809b95bdd901716afd2f6399a88fb0`
- **Moduli inclusi:** 93 file complessivi, allineati a tutti i 12 moduli sorgenti (`ai`, `analysis`, `core`, `entity_resolution`, `importer`, `multimodal`, `normalization`, `profiler`, `search`, `ui`, `unified`, `validation`).

---

## 7. ARTEFATTI DI CONSEGNA

1. **Archivio ZIP di Consegna (creato FUORI dalla directory Git):**  
   `c:\Users\lucas\Desktop\CartellaTesi\chatAnalysis_scalability_packaging_guardrails_audit_20261017.zip`  
   - **Dimensione:** ~1.43 MB (316 file compressi, incluso `MANIFEST_SHA256.txt` interno).
   - **Assenza duplicati:** Verificata formalmente con `assert len(names) == len(set(names))`.
   - **Manifest interno unico:** `MANIFEST_SHA256.txt` è presente una sola volta e non include se stesso nel conteggio o nell'elenco degli hash.
   - **Fixture autorizzate incluse:**
     - `test_data/whatsapp_export/msgstore.db`
     - `test_data/whatsapp_export/wa.db`
     - `test_data/synthetic_smoke/synthetic_whatsapp_chat.zip`
   - **Esclusioni rigorose:** Privo di `.git/`, virtual environment, file di cache (`__pycache__`, `.pytest_cache`), directory di build temporanee (`build/`, `*.egg-info`), checkpoint personali (`.checkpoints/`) e database non approvati.

2. **File Checksum Separato dello ZIP:**  
   `c:\Users\lucas\Desktop\CartellaTesi\chatAnalysis_scalability_packaging_guardrails_audit_20261017.zip.sha256`  
   - Generato atomicamente all'esterno della directory Git contestualmente all'archivio.
