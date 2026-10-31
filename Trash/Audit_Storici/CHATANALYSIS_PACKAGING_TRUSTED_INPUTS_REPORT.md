# CHATANALYSIS — RAPPORTO SUI GUARDRAIL DI IDENTITÀ DEI FILE E TRUSTED PACKAGING
**Data**: 17 Ottobre 2026  
**Ambiente di convalida**: Windows (sviluppo) per rilascio e validazione Ubuntu (test scalabilità)  
**Deliverable primario**: `chatAnalysis_packaging_trusted_inputs_audit_20261017.zip`  
**File di verifica hash**: `chatAnalysis_packaging_trusted_inputs_audit_20261017.zip.sha256`  

---

## 1. OBIETTIVO E LIMITI RISOLTI

L'audit indipendente sulla release precedente ha confermato l'eccellente stato della pipeline (318 file, nessun duplicato, 317/317 hash manifest verificati, 89/89 moduli wheel, conformità scientifica).

Sono stati tuttavia individuati e risolti **due specifici casi limite**:
1. **File privati con nomi ordinari fuori da `test_data/`**: file con nomi innocui o generici (es. `notes.txt`, `personal.json`, `chat.csv`, `unauthorized.py`) creati nella root o nei moduli applicativi potevano sfuggire ai filtri per nome (`.env`, `credentials`) ed essere inclusi automaticamente nell'archivio.
2. **Sostituzione del contenuto delle fixture autorizzate sotto `test_data/`**: se un file legittimo sotto `test_data/` (es. `test_data/whatsapp_export/msgstore.db`, `test_data/synthetic_smoke/synthetic_whatsapp_chat.txt` o `test_data/cellebrite_export/messages.csv`) veniva sostituito o sovrascritto con dati privati pur mantenendo lo stesso percorso, la sola allowlist di percorso non rilevava l'alterazione del payload.

Entrambi i difetti sono stati neutralizzati trasformando il packaging da un modello puramente sottrattivo a un modello basato su **inventario attendibile dei sorgenti** e **verifica crittografica dell'identità delle fixture sintetiche**.

---

## 2. ARCHITETTURA DEI GUARDRAIL DI IDENTITÀ

Tutte le modifiche sono state implementate esclusivamente in:
- `scripts/create_scalability_audit_archive.py`
- `tests/unit/test_packaging_release.py`

### 2.1 Inventario Esplicito e Verificabile dei Percorsi Sorgente (`TRUSTED_SOURCE_INVENTORY`)
- È stato introdotto un inventario completo ed esplicito di tutti i percorsi approvati nel pacchetto sorgente (`TRUSTED_SOURCE_INVENTORY`), comprendente tutti i moduli applicativi (`ai/`, `analysis/`, `core/`, `entity_resolution/`, `importer/`, `multimodal/`, `normalization/`, `search/`, `ui/`, `unified/`, `validation/`), gli script, i test e la documentazione ufficiale.
- **Regola di chiusura ermetica**: nessun file sconosciuto o non censito nell'inventario viene incluso automaticamente. Qualsiasi file con nome ordinario o anomalo aggiunto all'esterno della baseline viene respinto (`should_pack_file() -> False`) e registrato nella lista dei file esclusi per approvazione manuale.

### 2.2 Baseline Attendibile e Verifica SHA-256 delle Fixture Sintetiche (`APPROVED_TEST_DATA_HASHES`)
- Tutte le 50 fixture sintetiche e dataset scientifici sotto `test_data/` sono mappati nella tabella `APPROVED_TEST_DATA_HASHES` con i rispettivi hash SHA-256 ufficiali calcolati byte-for-byte.
- Durante la scansione, ogni file sotto `test_data/` viene ispezionato crittograficamente con `verify_test_data_fixture()`:
  - Se il percorso non è registrato -> **scartato**.
  - Se l'hash SHA-256 non corrisponde esattamente alla baseline attendibile -> **scartato come fixture manomessa/sostituita**.
  - Nessuna fixture scientifica ufficiale è stata modificata o cancellata per superare i controlli.

### 2.3 Segnalazione Sicura Senza Data Leak
- Quando un file viene escluso perché non presente nell'inventario o perché la fixture è alterata, il suo percorso relativo viene registrato in `flagged_excluded` e stampato a console:
  `[GUARDRAIL PRIVACY] File escluso: {rel_str}`
- **Nessun contenuto sensibile o dato privato viene mai letto o trascritto nei report o nei log.**

### 2.4 Conservazione Integrale dei Guardrail Precedenti
- **File sensibili**: rifiuto incondizionato di `.env`, `.env.*` (salvo template pubblici `.env.example`, `.env.template`), chiavi private (`.pem`, `.key`, `id_rsa`), credenziali (`credentials.*`, `secrets.*`, `token.json`, `auth.json`) e file di backup/temporanei.
- **Symlink e Path Traversal**: scarto tassativo di qualsiasi collegamento simbolico e confinamento rigoroso entro la radice del repository (`fpath.resolve().is_relative_to(...)`).
- **Firma SQLite binaria**: rilevamento dell'header `b"SQLite format 3\x00"` sui primi 16 byte per impedire database mascherati con estensioni innocue.
- **Manifest Deterministico**: generazione finale di `MANIFEST_SHA256.txt` privo di auto-referenzialità.

---

## 3. SUITE DI TEST DI PACKAGING (7/7 PASSED)

In `tests/unit/test_packaging_release.py` sono stati eseguiti con successo 7 test completi:

1. **`test_packaging_wheel_contains_analysis_and_core_packages`**: PASSED
   - Build wheel isolata, verifica di tutti i 6 file di `analysis/` e di tutti i 10 package fondamentali, import isolato in sottoprocesso.
2. **`test_packaging_archive_double_run_no_duplicate_manifest`**: PASSED
   - Esecuzione ripetuta, unicità del manifest SHA256, assenza assoluta di duplicati.
3. **`test_packaging_archive_rejects_unapproved_db_under_test_data`**: PASSED
   - Rigetto di `.sqlite3`, `.db3`, WAL, SHM, `.journal` e file con firma magica SQLite.
4. **`test_packaging_archive_rejects_symlinks_and_external_traversal`**: PASSED
   - Rigetto di collegamenti simbolici a file e directory e tentativi di path traversal.
5. **`test_packaging_archive_rejects_env_and_private_files_under_test_data`**: PASSED
   - Rigetto di `.env*`, credenziali, chiavi e file privati non approvati sotto `test_data/`.
6. **`test_packaging_archive_rejects_unknown_ordinary_files_outside_test_data` (NUOVO - DIFETTO 1)**: PASSED
   - Dimostra che file privati con nomi ordinari (`notes.txt`, `ai/private_chat.csv`, `core/personal_info.json`, `scripts/unauthorized_tool.py`, `tests/secret_leak.txt`) vengono rigorosamente esclusi dallo ZIP e segnalati per approvazione.
7. **`test_packaging_archive_rejects_tampered_or_substituted_synthetic_fixtures` (NUOVO - DIFETTO 2)**: PASSED
   - Dimostra che se il payload di una fixture autorizzata (es. `messages.csv` o `ground_truth.json`) viene sostituito mantenendo lo stesso nome file, la verifica SHA256 rileva l'incongruenza rispetto alla baseline attendibile ed esclude il file dallo ZIP.

### Riepilogo Pytest
```text
tests/unit/test_packaging_release.py::test_packaging_wheel_contains_analysis_and_core_packages PASSED [ 14%]
tests/unit/test_packaging_release.py::test_packaging_archive_double_run_no_duplicate_manifest PASSED [ 28%]
tests/unit/test_packaging_release.py::test_packaging_archive_rejects_unapproved_db_under_test_data PASSED [ 42%]
tests/unit/test_packaging_release.py::test_packaging_archive_rejects_symlinks_and_external_traversal PASSED [ 57%]
tests/unit/test_packaging_release.py::test_packaging_archive_rejects_env_and_private_files_under_test_data PASSED [ 71%]
tests/unit/test_packaging_release.py::test_packaging_archive_rejects_unknown_ordinary_files_outside_test_data PASSED [ 85%]
tests/unit/test_packaging_release.py::test_packaging_archive_rejects_tampered_or_substituted_synthetic_fixtures PASSED [100%]

============================= 7 passed in 32.74s ==============================
```

---

## 4. INTEGRITÀ BENCHMARK SCIENTIFICI E CODICE APPLICATIVO

- Nessun modulo applicativo, chunking, topic discovery, alias o checkpoint modificato.
- Gli hash dei tre file scientifici congelati sono identici e intatti:
  - `ai/experiment.py`: `91100c0a84cb4a8b7c2eef34a06cff9ec21aa6e6eec14ffd09ce6fea8bcee845`
  - `ai/benchmark.py`: `dc57081dbce3463a8017aa26bb4e4a18889af0a3296853fe40968561d6d44d36`
  - `run_benchmark.sh`: `7f9cae97df2fae1bc3b5964e639b9f80e3ac5a388ec9c3e20305ff64d48044a7`
- Nessun comando Git mutativo (`commit`, `push`, `pull`, `fetch`, `reset`) eseguito.

---

## 5. CONSEGNA ARCHIVIO FINALE

L'archivio è stato generato esternamente alla directory Git:
- **Archivio**: `C:\Users\lucas\Desktop\CartellaTesi\chatAnalysis_packaging_trusted_inputs_audit_20261017.zip`
- **File Checksum**: `C:\Users\lucas\Desktop\CartellaTesi\chatAnalysis_packaging_trusted_inputs_audit_20261017.zip.sha256`
- **Manifest interno**: `MANIFEST_SHA256.txt` (unico, certificato, senza auto-referenzialità).
- **Esito Audit di Privacy e Trusted Packaging**: 100% CONFORME.
