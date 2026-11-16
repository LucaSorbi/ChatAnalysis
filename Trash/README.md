# Trash/ — archivio storico consultabile (NON è una cancellazione)

Qui sono stati **spostati con `git mv`, senza alterarne il contenuto**, i report, gli audit e i materiali di lavoro storici
che prima affollavano la radice del repository. Nulla è stato cancellato; la storia git di ogni file è conservata
(`git log --follow Trash/<percorso>`).

> **Attenzione — il repository GitHub è PUBBLICO.** Spostare un file in `Trash/` non lo rende privato se viene committato.
> Per questo `Trash/` è **versionata** e ignora soltanto `Trash/Privato_Locale/` (area solo-locale). I file sospetti già
> versionati prima di questo intervento **non sono stati toccati né copiati**: sono elencati in `MANIFEST.csv` con stato
> `PRIVATE_NOT_MOVED` / `UNKNOWN_NOT_MOVED`. Questo lavoro **non riscrive la storia git**: i contenuti già pubblicati
> restano nella cronologia finché il proprietario non decide un'operazione dedicata (vedi `docs/PROGRESS_UNIFICATION_AND_TRASH_REPORT_20261030.md`).

## Struttura

| Cartella | Contenuto |
|---|---|
| `Analisi_Tesi/` | materiali per la tesi (capitolo 3, risultati e discussione, audit di provenienza, report LIVE, specifica export JSON) — vedi `INDICE_TESI.md` |
| `Audit_Storici/` | report di hardening, packaging, WAL/snapshot, sicurezza dei checkpoint, scalabilità |
| `Documentazione_Storica/` | piani/report di pulizia, inventari precedenti, `DIFF_SUMMARY.txt`, `REPORT_RISULTATI_VERIFICA.txt` |
| `Materiale_Obsoleto/` | script di prova (`scratch/`) non più usati |
| `Privato_Locale/` | **solo sul tuo PC**, ignorata da git (tranne il suo README) |
| `inventory/` | inventario SHA-256 di tutti i file tracciati PRIMA e DOPO l'intervento |
| `repository_cleanup_2026-11-07/` | file non operativi della pulizia del 7/11 (runbook, `scripts/`, benchmark, `docs/`) e i 12 test che li verificano; manifest e `restore_from_trash.py` |
| `quarantena_dati_sensibili_2026-11-16/` | artefatti di esecuzione potenzialmente derivati da chat reali, spostati dalla radice il 16/11 (elenco e SHA-256 in `LEGGIMI_QUARANTENA.md`) |
| `MANIFEST.csv` | per ogni file: `MOVED` (hash verificato), `KEPT`, `PRIVATE_NOT_MOVED`, `UNKNOWN_NOT_MOVED` |

## Come consultare

- Cercare un vecchio percorso: aprire `MANIFEST.csv` e filtrare `old_path`.
- I tre report di tesi presenti anche in `docs/` (audit di provenienza, sprint tesi, risultati e discussione) hanno
  un duplicato byte-identico in `docs/` (stesso SHA-256): la copia in `docs/` resta quella di riferimento.
- Gli script storici `scripts/create_*_archive.py` citano ancora i vecchi percorsi in radice: sono builder di release
  del passato e **non** sono stati aggiornati; rieseguirli richiederebbe di riportare i file in radice.
- I documenti già esistenti che nominano un report spostato (es. `docs/*.md`, runbook in radice) conservano il testo
  originale; per il nuovo percorso usare `MANIFEST.csv`.

## Limiti dell'autoverifica
`inventory/INVENTORY_AFTER.csv` è un'istantanea al commit `4e30039`: non include sé stesso e non è aggiornato dai commit
successivi. Un file di hash non prova la propria integrità: la prova indipendente è `git diff -M --name-status 93c106d 4e30039`
(57 voci `R100`). Dettagli in `inventory/README.md`. Gli script storici `scripts/create_*_archive.py` sono documentati come
non eseguibili in `scripts/HISTORICAL_ARCHIVE_BUILDERS.md`.

## Aggiornamento del 2026-11-16

- I file elencati in `MANIFEST.csv` come `PRIVATE_NOT_MOVED` / `UNKNOWN_NOT_MOVED` per i checkpoint di backup, `scratch/`,
  `scratch_call_385_request_body.json` e `chat-20261021T182727Z-1-001.zip` sono stati **spostati** in
  `quarantena_dati_sensibili_2026-11-16/` con `git mv`. `MANIFEST.csv` è un registro storico e non è stato modificato:
  per quei file riporta ancora il vecchio percorso in radice.
- La suite di test è stata riportata in `tests/` (nella radice); in `repository_cleanup_2026-11-07/tests/` restano solo i 12 test
  che verificano file archiviati. Il test di integrità descritto sotto è tra questi e **non fa più parte della suite**:
  se lo si ripristina, `test_trash_contains_no_databases_archives_or_wal_files_outside_private_area` e
  `test_suspicious_versioned_files_are_listed_as_exceptions_not_moved` falliscono perché la quarantena contiene `.db` e `.zip`
  tracciati e perché quei file non sono più in radice. Fallisce inoltre il controllo dell'impronta di `run_benchmark.sh`: il file archiviato non ha più l'impronta congelata (il contenuto è cambiato dopo il commit `6461512`, non solo le fine riga). Sono incoerenze note e documentate, non difetti del codice.

## Verifica

`repository_cleanup_2026-11-07/tests/unit/test_trash_archive_integrity.py` (archiviato, vedi sopra) controlla che ogni file `MOVED` esista al nuovo percorso con lo stesso
SHA-256, che il vecchio percorso sia assente, che nessun codice runtime/test/packaging citi i vecchi percorsi, che
`.gitignore` ignori solo `Trash/Privato_Locale/` e che in `Trash/` non siano tracciati database, zip o file WAL.

## Archiviare analisi private in locale

`docs/TRASH_ARCHIVIAZIONE_LOCALE_RUNBOOK_WINDOWS.md` e `scripts/archive_private_local.py` (dry-run di default,
copia con verifica SHA-256, SQLite tramite Online Backup API, originali sempre intatti). Si raccomanda inoltre un
**backup locale indipendente** (disco esterno) dei dati originali: `Trash/Privato_Locale/` non è un backup.
