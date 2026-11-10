# Archiviare in locale analisi private (Windows) — `Trash/Privato_Locale/`

Stato: STATIC + TEST sintetici (`tests/unit/test_trash_archive_integrity.py`). **Mai eseguito su dati reali da Claude.**

Il repository GitHub è **pubblico**. `Trash/Privato_Locale/` è ignorata da git (tranne il suo README) ed esiste solo sul tuo
PC. Non è un backup: tieni anche una copia indipendente (disco esterno) degli originali.

## Regole dello script `scripts\archive_private_local.py`
- **Dry-run di default**: senza `--execute` non copia nulla e stampa il piano.
- **Copia, non sposta**: gli originali restano dove sono, mai modificati né cancellati.
- Non sovrascrive mai (destinazione esistente → saltata e segnalata).
- File normali: verifica SHA-256 sorgente/destinazione. Database SQLite: Online Backup API (mai copia a mano di `.db`,
  `-wal`, `-shm`), poi `PRAGMA integrity_check` e confronto righe per tabella. Nota: aprire in sola lettura un DB in
  modalità WAL può creare file `-wal`/`-shm` vuoti accanto alla sorgente (innocui; rimuovili tu se vuoi).
- I checkpoint attivi (`CHATANALYSIS_CHECKPOINT_DIR` o `.checkpoints\`) sono **esclusi**. Per includerli: chiudi prima
  Streamlit/LM Studio e aggiungi `--include-active-checkpoints`.
- Destinazione sempre dentro `Trash\Privato_Locale\`. Il file originale della chat WhatsApp **non va passato** allo script.

## Procedura
```
cd C:\percorso\ChatAnalysis
python scripts\archive_private_local.py --source output --label analisi_2026_10
```
Controlla l'elenco (dry-run), poi:
```
python scripts\archive_private_local.py --source output --label analisi_2026_10 --execute
echo exit=%ERRORLEVEL%
git status
```
Atteso: `exit=0`, righe `OK` per ogni file, `ARCHIVE_MANIFEST.csv` nella destinazione, e `git status` che **non** mostra
nulla sotto `Trash\Privato_Locale\`. Se compare, NON committare: `git check-ignore -v Trash\Privato_Locale\<file>`.

## Cosa NON fare
- `git add -f Trash\Privato_Locale\...` (renderebbe pubblico il contenuto).
- Copiare a mano `checkpoints.db` mentre l'app gira.
- Aggiungere un ignore globale su `Trash/`: l'archivio storico deve restare versionato.
