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
| `MANIFEST.csv` | per ogni file: `MOVED` (hash verificato), `KEPT`, `PRIVATE_NOT_MOVED`, `UNKNOWN_NOT_MOVED` |

## Come consultare

- Cercare un vecchio percorso: aprire `MANIFEST.csv` e filtrare `old_path`.
- I tre report di tesi presenti anche in `docs/` (audit di provenienza, sprint tesi, risultati e discussione) hanno
  un duplicato byte-identico in `docs/` (stesso SHA-256): la copia in `docs/` resta quella di riferimento.
- Gli script storici `scripts/create_*_archive.py` citano ancora i vecchi percorsi in radice: sono builder di release
  del passato e **non** sono stati aggiornati; rieseguirli richiederebbe di riportare i file in radice.
- I documenti già esistenti che nominano un report spostato (es. `docs/*.md`, runbook in radice) conservano il testo
  originale; per il nuovo percorso usare `MANIFEST.csv`.

## Verifica

`tests/unit/test_trash_archive_integrity.py` controlla che ogni file `MOVED` esista al nuovo percorso con lo stesso
SHA-256, che il vecchio percorso sia assente, che nessun codice runtime/test/packaging citi i vecchi percorsi, che
`.gitignore` ignori solo `Trash/Privato_Locale/` e che in `Trash/` non siano tracciati database, zip o file WAL.

## Archiviare analisi private in locale

`docs/TRASH_ARCHIVIAZIONE_LOCALE_RUNBOOK_WINDOWS.md` e `scripts/archive_private_local.py` (dry-run di default,
copia con verifica SHA-256, SQLite tramite Online Backup API, originali sempre intatti). Si raccomanda inoltre un
**backup locale indipendente** (disco esterno) dei dati originali: `Trash/Privato_Locale/` non è un backup.
