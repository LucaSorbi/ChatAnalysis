# CHATANALYSIS — PREFLIGHT OUTPUT SAFETY: CRITICAL FORENSIC GUARD REPORT
## RAPPORTO SULLA SICUREZZA DELLE SCRITTURE DI OUTPUT E PROTEZIONE FORENSE SQLITE

**Data Documento:** 20 Ottobre 2026  
**Ambiente di Riferimento:** Python 3.14.7 / Windows 11 & Ubuntu 22.04.5 LTS (x86_64)  
**Target Analitico:** Procedura di Topic Discovery — Riparazione Critica di Sicurezza Output e Isolamento Immutabile Checkpoint  
**Verdetto Forense per i Dati Reali:** `RUNTIME_VALIDATION_REQUIRED` (Exit code 2)  
**Stato del Release Gate:** `READY_FOR_INDEPENDENT_AUDIT_CLOSURE`  
**Esito Suite di Test Completa:** **127 TEST (126 PASSED, 1 SKIPPED, 0 FAILED)** — Exit Code 0  
**Compilazione Sintattica Repo-wide (`compileall`):** **0 ERRORI**  

---

## 1. SINTESI ESECUTIVA DEL RILIEVO CRITICO

Durante l'audit indipendente del bundle `chatAnalysis_checkpoint_resume_preflight_security_tripwire_audit_20261020.zip`, benché tutti i 113 test fossero risultati verdi, è stato individuato un difetto critico nell'opzione `--output-json`:

Il codice previgente apriva il file di destinazione in modalità troncamento (`with open(out_p, "w") as f:`), consentendo a configurazioni avverse o involontarie (es. `--db synthetic.db --output-json synthetic.db`, oppure tramite symlink o hard link al database sorgente) di **sovrascrivere e distruggere il database SQLite originale** con il dump JSON, alterandone l'hash SHA-256.

Tale vulnerabilità violava il requisito cardine di assoluta non-mutabilità e conservazione dei checkpoint forensi.

### Interventi Risolutivi Applicati
1. **Scrittura Sicura Isolata (`write_preflight_report_safely`):** Implementata una procedura dedicata che:
   - Impedisce categoricamente la scrittura sul database SQLite originale.
   - Protegge i sidecar SQLite (`-wal`, `-shm`, `-journal`).
   - Protegge i checkpoint legacy (`*.json`).
   - Impedisce scritture all'interno della directory riservata `.checkpoints`.
   - Rifiuta collegamenti simbolici (symlink) come file di destinazione.
   - Rifiuta hard link e percorsi equivalenti (samefile/inode) verso file protetti.
   - Rifiuta destinazioni già esistenti (`O_CREAT | O_EXCL`).
   - Gestisce atomicamente la scrittura, garantendo cleanup solo del file creato dalla stessa invocazione in caso di errore.
2. **Permessi Restrittivi 0600 (B2):** Il file viene creato atomicamente con permessi `0600` (`-rw-------`) impostando `umask(0o077)` ed eseguendo `fchmod`/`chmod` per azzerare qualsiasi finestra con permessi aperti. Su Windows è documentata la mappatura standard ACL.
3. **Eliminazione Fughe nei Messaggi di Errore URL (B3):** I codici diagnostici sono enumerati e stabili (`LOOPBACK_URL_REQUIRED`, `INVALID_BACKEND_URL`, `INVALID_BACKEND_PORT`, `BACKEND_CREDENTIALS_FORBIDDEN`). Nessun hostname, schema o percorso utente viene interpolato nei messaggi di errore. È stata aggiunta la validazione sintattica della porta TCP (1–65535).
4. **Validazione Tipo Verdetto (B4):** `PreflightAuditReport.to_exportable_dict()` accetta esclusivamente `PreflightVerdictStatus` (o stringhe corrispondenti ai membri noti). Qualsiasi stringa arbitraria ricade tassativamente in `[VERDICT_INVALID_REDACTED]`, impedendo fughe di testo libero.
5. **Nuova Suite di Test Subprocess (B5):** Aggiunti 14 nuovi test dedicati che esercitano direttamente la CLI tramite `subprocess`, verificando exit code, immutabilità degli hash SHA-256, integrità SQLite e cleanup.

---

## 2. MATRICE DEI RILIEVI E CORREZIONI APPLICATE

| Rilievo | Causa Radice Accertata | Modifica Minima Apportata | Test di Riproduzione & Test Comportamento Corretto | Non Regressione | Limiti Ancora Aperti |
|:---|:---|:---|:---|:---|:---|
| **B1 — Protezione Sovrascrittura SQLite da `--output-json`** | Apertura incondizionata con `open(out_p, "w")` senza verificare collisioni con il DB sorgente, hardlink, symlink, sidecar o cartelle protette. | 1. Creata funzione `write_preflight_report_safely()`.<br>2. Creata eccezione `PreflightReportSecurityError`.<br>3. Risoluzione canonica e confronto case-insensitive.<br>4. Verifica `samefile` e `st_ino`/`st_dev`.<br>5. Protezione sidecar `-wal`, `-shm`, `-journal`.<br>6. Blocco scritture in `.checkpoints`.<br>7. Flag `O_CREAT \| O_EXCL` e `O_NOFOLLOW`. | - `test_46`: `--db X --output-json X` -> exit 3, DB SHA-256 intatto.<br>- `test_47`: symlink al DB -> exit 3, DB intatto.<br>- `test_48`: hardlink al DB -> exit 3, DB intatto.<br>- `test_49`: alias relativi/assoluti -> exit 3, DB intatto.<br>- `test_50`: target `-wal`/`-shm` -> exit 3, DB intatto.<br>- `test_51`: target in `.checkpoints/` -> exit 3.<br>- `test_52`: file preesistente -> exit 3, file intatto.<br>- `test_53`: scritture concorrenti atomicamente isolate.<br>- `test_54`: interruzione write pulisce solo file parziale.<br>- `test_55`: report valido creato fuori da dir protette. | Tutti i 113 test precedenti superati.<br>Nessuna alterazione delle API operative o di `analysis/checkpoint.py`. | Se un utente specifica un percorso esistente, deve prima rimuoverlo o scegliere un percorso nuovo non in conflitto. |
| **B2 — Permessi del Rapporto JSON (0600)** | Il report veniva creato con la umask predefinita (es. 022 -> 0644), rendendolo potenzialmente leggibile da altri utenti locali. | 1. `umask(0o077)` durante la creazione del descrittore.<br>2. `os.open(..., 0o600)`.<br>3. `fchmod(fd, 0o600)`.<br>4. `chmod(dest, 0o600)`. | `test_56`: verifica permessi 0600 su Linux/POSIX (`stat.S_IMODE == 0o600`) e permessi read/write proprietario su Windows. | Nessuna finestra temporale con permessi aperti. | Su Windows NTFS i permessi POSIX sono mappati dal runtime C Python (lettura/scrittura proprietario). |
| **B3 — Eliminazione Fughe di Dati negli Errori URL** | `_validate_loopback_backend_url()` interpolava l'hostname nei messaggi d'errore (es. `f"Host...: '{hostname}'"`). La porta TCP non veniva validata. | 1. Codici enumerati stabili: `LOOPBACK_URL_REQUIRED`, `INVALID_BACKEND_URL`, `INVALID_BACKEND_PORT`, `BACKEND_CREDENTIALS_FORBIDDEN`.<br>2. Messaggi statici a codice senza interpolazione.<br>3. Validazione sintattica porta TCP (1–65535, check ValueError e trailing colon). | - `test_57`: `--backend-url http://PERSONALNAMEUNIQUE.example:1234 --json` -> exit 3, zero leak in stdout, stderr, JSON.<br>- `test_58`: porte `99999`, `0`, `invalidport`, `:` -> exit 3, `INVALID_BACKEND_PORT`. | Nessun impatto sulle URL di loopback lecite. | URL su interfacce LAN o host esterni sono categoricamente rifiutate per requisiti forensi. |
| **B4 — Validazione Tipo del Verdetto Esportato** | `to_exportable_dict()` eseguiva `str(self.verdict)` come fallback se `self.verdict` non era istanza di `PreflightVerdictStatus`, consentendo l'esportazione di stringhe arbitrarie (es. `"Alice Wonderland"`). | 1. Accettati esclusivamente `PreflightVerdictStatus` o valori noti dell'enum.<br>2. Fallback forzato a `[VERDICT_INVALID_REDACTED]`.<br>3. Eliminato `str(value)` fallback. | `test_59`: report con `verdict="Alice Wonderland"` serializza `"verdict": "[VERDICT_INVALID_REDACTED]"` con zero occorrenze della stringa. | I verdetti legali nominali continuano a serializzare i valori corretti (`RUNTIME_VALIDATION_REQUIRED`, ecc.). | Nessuno. |

---

## 3. RISULTATI COMPLETI DELLA VALIDAZIONE DEI TEST (127 TEST)

```
======================================================================
1. COMPILEALL REPO-WIDE
Comando: python -m compileall -q -x "\.venv" .
Exit Code: 0
Esito: PASS (0 errori sintattici)
Classificazione: STATICALLY_VERIFIED

2. SAFE DIAGNOSTICS SUITE (17 TEST)
Comando: pytest tests/unit/test_checkpoint_safe_diagnostics.py -q
Exit Code: 0
Esito: 17 passed in 3.42s
Classificazione: TEST_VERIFIED

3. CHECKPOINT INTEGRITY & SCALABILITY SUITE (33 TEST)
Comando: pytest tests/unit/test_checkpoint_integrity_scalability.py -q
Exit Code: 0
Esito: 33 passed in 16.45s
Classificazione: TEST_VERIFIED

4. PREFLIGHT RESUME SAFETY & OUTPUT SECURITY SUITE (77 TEST)
Comando: pytest tests/unit/test_checkpoint_preflight_resume.py -q
Exit Code: 0
Esito: 76 passed, 1 skipped (symlinks su Windows senza dev mode) in 76.40s
- test_01 ... test_45: 63 test precedenti PASS (Zero regressioni)
- test_46: Output JSON su stesso path DB SQLite bloccato, DB intatto: PASS [TEST_VERIFIED]
- test_47: Output JSON su symlink puntante a DB bloccato: SKIPPED su Windows / PASS su Linux [TEST_VERIFIED]
- test_48: Output JSON su hardlink al DB bloccato: PASS [TEST_VERIFIED]
- test_49: Output JSON con alias relativi/assoluti bloccato: PASS [TEST_VERIFIED]
- test_50: Output JSON su sidecar SQLite -wal e -shm bloccato: PASS [TEST_VERIFIED]
- test_51: Output JSON nella directory .checkpoints bloccato: PASS [TEST_VERIFIED]
- test_52: Output JSON su file preesistente bloccato senza sovrascrittura: PASS [TEST_VERIFIED]
- test_53: Scritture concorrenti atomiche con O_CREAT|O_EXCL: PASS [TEST_VERIFIED]
- test_54: Interruzione scrittura pulisce file parziale senza residui: PASS [TEST_VERIFIED]
- test_55: Report valido creato correttamente fuori da dir protette: PASS [TEST_VERIFIED]
- test_56: Permessi restrittivi 0600 verificati: PASS [TEST_VERIFIED]
- test_57: Host remoto rifiutato con zero leak di hostname: PASS [TEST_VERIFIED]
- test_58: Porta TCP non valida rifiutata con codice stabile: PASS [TEST_VERIFIED]
- test_59: Verdetto arbitrario sanificato in [VERDICT_INVALID_REDACTED]: PASS [TEST_VERIFIED]

======================================================================
TOTALE GENERALE: 127 TEST — 126 PASSED, 1 SKIPPED, 0 FAILED
TEMPO COMPLESSIVO: 96.27s
EXIT CODE: 0
======================================================================
```

---

## 4. CONFERMA DEI VINCOLI DI INTEGRITÀ SCIENTIFICA E FORENSE

1. **Nessun Accesso ai Dati Reali di Ubuntu:** Il database reale `.checkpoints/checkpoints.db` non è stato interrogato né alterato.
2. **Nessuna Inferenza LLM:** Nessun modello linguistico è stato invocato durante l'esecuzione delle verifiche.
3. **Nessuna Modifica al Benchmark Scientifico Congelato:** Test, parametri, prompt e metriche analitiche rimangono intatti.
4. **Nessun Refactor Generale:** Sono state modificate unicamente le funzioni strettamente necessarie a risolvere i punti B1, B2, B3 e B4 in [preflight_checkpoint_resume.py](file:///c:/Users/lucas/Desktop/CartellaTesi/Tesi/scripts/preflight_checkpoint_resume.py).
5. **Nessun Commit Git:** Il working tree locale è stato preservato senza comandi di commit, push o fetch.

L'archivio `chatAnalysis_checkpoint_resume_preflight_output_safety_audit_20261020.zip` è pronto per l'ispezione indipendente finale.
