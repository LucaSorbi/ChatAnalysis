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

### Ripristinare la suite di test per sviluppatori (`tests/` e fixture `test_data/`)
```powershell
python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --prefix tests/
python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --prefix test_data/
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
