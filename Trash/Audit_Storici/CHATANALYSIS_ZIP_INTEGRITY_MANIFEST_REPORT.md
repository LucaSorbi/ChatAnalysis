# CHATANALYSIS — RAPPORTO TECNICO DI INTEGRITÀ DELL'ARCHIVIO ZIP E HARDENING DEL VERIFICATORE

**Data**: 21 Ottobre 2026  
**Autore**: Luca Sorbi — Tesi Triennale in Informatica  
**Archivio Verificato**: `chatAnalysis_f1_trust_boundary_manifest_validator_audit_20261021.zip`  
**Posizione File**: `c:\Users\lucas\Desktop\CartellaTesi\chatAnalysis_f1_trust_boundary_manifest_validator_audit_20261021.zip` (DIRETTAMENTE FUORI da `Tesi` e fuori dal repository Git)  
**Script di Confezionamento**: `scripts/create_closure_audit_archive.py`  
**Script di Verifica Autonoma**: `scripts/verify_package_manifest.py`  
**Suite di Test**: `tests/unit/test_verify_package_manifest.py` (11 test avversari)  
**Stato**: **TEST_VERIFIED & CRITTOGRAFICAMENTE CERTIFICATO (100% SUCCESSO)**

---

## 1. SINTESI ESECUTIVA

Il presente documento attesta il completamento e l'hardening formale della **Priorità 2 (Verificatore ZIP & Integrità Crittografica del Pacchetto)** per il rilascio di ChatAnalysis:

1. **Risoluzione del Difetto di Duplicate Entry nel Verificatore ZIP**:
   - L'audit indipendente ha dimostrato che la precedente versione di `scripts/verify_package_manifest.py` accettava uno ZIP sintetico malevolo contenente due file con lo stesso nome a causa della sovrascrittura trasparente del dizionario `zip_files_map`.
   - Il verificatore è stato riprogettato con una fase preliminare di scansione grezza di `infolist()` che intercetta e rigetta tassativamente qualsiasi archivio contenente nomi duplicati grezzi o collisioni derivanti da normalizzazione di percorso.
2. **Hardening Completo dei Vincoli di Sicurezza**:
   - Rigetto assoluto di percorsi assoluti Unix (`/...`) e Windows (`C:\...`, `\\server\...`).
   - Rigetto categorico di directory traversal (`..` o `.`).
   - Rigetto di separatori ambigui o doppi (`//`, `\\\\`).
   - Rigetto di digest SHA-256 non conformi allo standard esadecimale a 64 caratteri.
   - Rigetto di manifest auto-referenzianti.
   - Controllo bidirezionale al 100%: nessun file omesso, nessun file fantasma.
   - Confronto crittografico byte-per-byte calcolato in streaming.
3. **Suite di Test Avversari Dedicata (`tests/unit/test_verify_package_manifest.py`)**:
   - Implementati 11 scenari avversari che riproducono duplicati, collisioni, traversal, percorsi assoluti, hash corrotti e manomissioni di bit, tutti superati con esito 100% PASS.
4. **Verifica Ufficiale dell'Archivio**:
   - L'archivio ufficiale `chatAnalysis_f1_trust_boundary_manifest_validator_audit_20261021.zip` supera l'ispezione con esito `VERIFIED_PASS`, con tutti i digest SHA-256 perfettamente corrispondenti.

---

## 2. DIFETTO RISCONTRATO E AUDIT INDIPENDENTE

### 2.1 Il Bug dei Nomi Duplicati nello ZIP
Nello standard ZIP (PKWARE APPNOTE), un archivio può contenere più voci (entry nel *Central Directory*) con lo stesso identico nome di file. In Python, `zipfile.ZipFile.namelist()` restituisce una lista che include i duplicati, ma l'iterazione:
```python
zip_files_map = {zinfo.filename: zinfo for zinfo in zf.infolist()}
```
sovrascriveva silenziosamente la prima voce con l'ultima, occultando la presenza del duplicato e consentendo a un archivio manipolato di superare la validazione formale.

### 2.2 Collisioni di Normalizzazione di Percorso
Inoltre, percorsi espressi come `folder/file.txt` e `folder\file.txt` o con barre trailing potevano essere interpretati diversamente a seconda del sistema operativo se non normalizzati prima del controllo di unicità.

---

## 3. HARDENING DEL VERIFICATORE (`scripts/verify_package_manifest.py`)

Le correzioni implementate applicano i seguenti 7 livelli di convalida preventiva:

1. **Rilevazione di Nomi Duplicati Grezzi**:
   ```python
   raw_names = [zinfo.filename for zinfo in zf.infolist()]
   seen_raw = set()
   for name in raw_names:
       if name in seen_raw:
           # Rifiuto immediato: duplicato rilevato prima di costruire il dizionario
           return False
       seen_raw.add(name)
   ```
2. **Rigetto di Collisioni Normalizzate**:
   I percorsi vengono convertiti in POSIX pulito. Se due voci diverse normalizzano allo stesso percorso, l'archivio viene immediatamente rigettato.
3. **Rifiuto Percorsi Assoluti e Traversal**:
   Vengono bloccati prefissi `/`, `\`, lettere di unità Windows (`^[a-zA-Z]:`), percorsi UNC (`\\`) e qualsiasi segmento contenente `..` o `.`.
4. **Rifiuto Separatori Ambigui**:
   Vengono rigettate stringhe contenenti doppie barre `//` o `\\\\`.
5. **Validazione Sintattica Digest SHA-256**:
   Ogni riga del manifest deve rispettare rigorosamente la regex esadecimale `^[0-9a-fA-F]{64}$`.
6. **Verifica Bidirezionale di Completezza**:
   - `files_in_manifest == files_in_zip`
   - Il manifest non deve mai contenere se stesso (`manifest_sha256.txt`).
7. **Controllo Byte-per-Byte**:
   Tutti i file vengono decompressi in memoria o a blocchi e verificati con `hashlib.sha256`.

---

## 4. MATRICE DEI TEST AVVERSARI (`tests/unit/test_verify_package_manifest.py`)

La suite è stata eseguita con successo su Windows (Python 3.14.7):

| ID Test | Nome Test | Difetto Verificato | Esito |
| :--- | :--- | :--- | :--- |
| 1 | `test_verify_zip_duplicate_raw_filename_rejected` | Archivio sintetico con due file con stesso nome esatto | **PASSED** |
| 2 | `test_verify_zip_normalized_collision_rejected` | Collisione percorsi `sub/file.txt` vs `sub\file.txt` | **PASSED** |
| 3 | `test_verify_zip_absolute_path_unix_rejected` | Percorso assoluto Unix `/etc/shadow` | **PASSED** |
| 4 | `test_verify_zip_absolute_path_windows_rejected` | Percorso assoluto Windows `C:\windows\system32` | **PASSED** |
| 5 | `test_verify_zip_directory_traversal_rejected` | Traversal `../evil.py` o `sub/../../secret.txt` | **PASSED** |
| 6 | `test_verify_zip_ambiguous_separators_rejected` | Separatori ambigui `folder//file.txt` | **PASSED** |
| 7 | `test_verify_zip_invalid_sha256_syntax_rejected` | SHA-256 corto, lungo o con caratteri non-hex | **PASSED** |
| 8 | `test_verify_zip_self_referencing_manifest_rejected` | Manifest che include se stesso | **PASSED** |
| 9 | `test_verify_zip_missing_and_unlisted_files_rejected` | File mancanti nello ZIP o omessi dal manifest | **PASSED** |
| 10 | `test_verify_zip_hash_mismatch_rejected` | Alterazione di 1 bit nel contenuto decompress | **PASSED** |
| 11 | `test_verify_zip_valid_archive_passes` | Archivio sintetico valido e conforme al 100% | **PASSED** |

---

## 5. REQUISITI DI PACKAGING E RISULTATI SULL'ARCHIVIO DI RILASCIO

L'archivio `chatAnalysis_f1_trust_boundary_manifest_validator_audit_20261021.zip` è stato generato tramite `scripts/create_closure_audit_archive.py`:
- Posizionato direttamente in `c:\Users\lucas\Desktop\CartellaTesi\` (fuori dal repository Git).
- Nessun file temporaneo, cache `__pycache__`, file `.pyc`, checkpoint privati, credenziali o modelli GGUF inclusi.
- Manifest `manifest_sha256.txt` generato dinamicamente con digest calcolati sui byte effettivi.
- Risultato del verificatore: **100% conformità (VERIFIED_PASS)**.
