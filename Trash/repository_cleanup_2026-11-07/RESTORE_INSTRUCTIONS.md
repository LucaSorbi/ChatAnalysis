# Istruzioni di Ripristino Non Distruttivo (`repository_cleanup_2026-11-07`)

Questa cartella contiene tutti i file non essenziali all'installazione e al runtime dell'applicazione spostati in modo **totalmente reversibile** durante la pulizia del repository del `2026-11-07`.

- **Nessun file è stato cancellato.**
- Ogni file conserva il proprio **percorso relativo originario** all'interno di `Trash/repository_cleanup_2026-11-07/`.
- Gli hash crittografici **SHA-256 prima e dopo lo spostamento** sono certificati in `manifest_cleanup.tsv` e `manifest_cleanup.json`.

## 1. Ripristino guidato tramite script Python (consigliato, zero sovrascritture)

Dalla directory principale del repository (`ChatAnalysis`):

### Anteprima (Dry-Run) senza modificare il filesystem
```powershell
python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --prefix tests/ --dry-run
```

### Ripristinare un singolo file
```powershell
python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --path RUNBOOK_UBUNTU.txt
```

### Suite di test (`tests/`) e fixture (`test_data/`)
> **Aggiornamento 2026-11-16**: la suite di test e i dati sintetici sono stati riportati nella radice con `git mv` (`tests/`, `test_data/`; `python -m pytest`).
> In questa cartella restano solo 12 test che verificano file archiviati (richiedono `scripts/`, `docs/` e file della radice dell'archivio).
> `--prefix tests/` ripristina esattamente quei 12 file in `tests/unit/`; `--prefix test_data/` non ha più nulla da ripristinare.
```powershell
python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --prefix tests/ --dry-run
```

### Ripristinare integralmente tutti i file spostati
```powershell
python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --all
```

Lo script verifica l'integrità SHA-256 prima e dopo il ripristino e **non sovrascrive mai** eventuali file già presenti nella destinazione originale.

## 2. Ripristino manuale singolo (senza script)

Per ripristinare manualmente un file (verificando prima che la destinazione non esista già):

- **Windows (PowerShell)**:
  ```powershell
  if (-not (Test-Path ".\RUNBOOK_UBUNTU.txt")) {
      Move-Item -Path ".\Trash\repository_cleanup_2026-11-07\RUNBOOK_UBUNTU.txt" -Destination ".\RUNBOOK_UBUNTU.txt"
  }
  ```
- **Ubuntu / Linux (bash)**:
  ```bash
  mv -n "Trash/repository_cleanup_2026-11-07/RUNBOOK_UBUNTU.txt" "./RUNBOOK_UBUNTU.txt"
  ```
