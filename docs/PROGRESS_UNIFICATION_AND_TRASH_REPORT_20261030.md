# Unificazione progress Discovery/Detection + riordino conservativo — report finale (30/10/2026)

Branch `feat/discovery-progress-trash-20261030` (da `main` @ `93c106d`). Due commit separati: **A** (progress Discovery),
**B** (archivio `Trash/`, inventari, script locale, test, questo report). Nessuna modifica a main, nessun merge/force-push,
nessuna riscrittura della storia. Etichette: STATIC / TEST / MOCK / LIVE / NOT VERIFIED. **Nessun LIVE eseguito.**

## Verità scomode (da leggere prima)
1. **Il repository pubblico già contiene dati di campagna reale**: 8 database SQLite di checkpoint in
   `forensic_provenance_audit_backup_20261027/`, `scratch/fresh_live_test_store/checkpoints.db`, una request body LLM grezza
   (`scratch_call_385_request_body.json`), un percorso privato Windows in `scratch/debug_actual_output.py`, e 484 residui di
   pytest in `temp_pytest/` (76 `.db`, 81 `.zip`, non ispezionati). Non li ho spostati né copiati (spostarli in `Trash/` non li
   rende privati) e **non ho riscritto la storia**: restano pubblici nella cronologia finché non decidi tu un'operazione
   dedicata (rimozione dall'indice + riscrittura storia + rotazione di eventuali segreti). Contenuto dei DB non letto (solo conteggi tabelle). [STATIC]
2. **La UI Streamlit reale NON è verificata**: nell'ambiente cloud `ijson` e `streamlit` non sono installabili (PyPI bloccato).
   La resa è provata solo con un registratore al posto di `st` [MOCK]; nessun AppTest reale, nessuno screenshot.

## Parte A — Progress Discovery = Detection (commit A)
- Pannello unico (`ui/progress_view.py`, modello puro testabile): "Blocchi principali X/26" (totale stabile), "Blocco principale
  corrente k/26", "Microchunk completati X/Y (totale dinamico)", "Microchunk del blocco corrente", recuperati da checkpoint, falliti,
  bisezioni, richieste LLM inviate/riuscite/fallite/in corso, retry/timeout/HTTP/protocollo, tempo trascorso, fase leggibile.
  Barra dei blocchi mai al 100% senza evento finale; nessuna ETA.
- Tracker: regola delle unità (bisezione/retry non creano unità; `completed+failed ≤ total`), campi evento opzionali
  `current_micro_total/current_micro_done` (retro-compatibili), `split_chunk(grow_total=False)`.
- Discovery: microchunk, recuperi (senza nuova richiesta HTTP), retry e bisezioni riportati al tracker; parent SUCCESS solo con tutti
  i microchunk validati; nessun doppio conteggio HTTP (il tracker è già il `request_listener`).
- **Difetto trovato e corretto**: dopo suddivisione in sub-chunk, 0 topic chiudeva `COMPLETE` (banner verde); ora
  `COMPLETE_EMPTY_UNVERIFIED` (≥10 evidenze) o `COMPLETE_NO_TOPICS`.
- **Modifica di un'aspettativa di test di PR #1**: `test_tracker_counts_callback_errors_without_losing_state` passa da 5 a 4 unità,
  perché con la nuova regola la bisezione non crea unità. Cambio motivato dal modello dei conteggi, non per "far diventare verde" un test.
- Non toccati: prompt, parsing, chunking reale, schema evidenze, serializzatore, modello, temperature, seed, aggregazioni, benchmark.

## Parte B — `Trash/` (commit B)
- 57 file **MOVED** con `git mv` (33 `Audit_Storici`, 12 `Analisi_Tesi`, 7 `Documentazione_Storica`, 5 `Materiale_Obsoleto/scratch`), SHA-256 prima/dopo **identico per tutti** [TEST + verifica in esecuzione]. I tre report di tesi duplicati in `docs/` restano là.
- **KEPT** (26 voci): pacchetti, test, fixture, packaging, `RUNBOOK_UBUNTU.txt` (letto da un test), benchmark congelati e `benchmark_frozen_sha256.txt`,
  runbook di riproduzione (UNCERTAIN: tenuti per prudenza). **PRIVATE_NOT_MOVED** (11), **UNKNOWN_NOT_MOVED** (2: zip di chat da 97 membri di benchmark, `temp_pytest/`).
  Dettaglio per file in `Trash/MANIFEST.csv`; inventari SHA-256 completi in `Trash/inventory/` (BEFORE: 1001 file tracciati; AFTER: stato finale, non include sé stesso).
- `.gitignore`: ignorato **solo** `Trash/Privato_Locale/*` (README escluso dall'ignore). Nessun ignore globale su `Trash/`.
- Indice tesi (`Trash/INDICE_TESI.md`): solo rimandi ai documenti. I valori "contatore recuperati = 0" e "export 0,386 s" della Detection
  **non compaiono in alcun file del repository**: marcati NON IN REPO, da non citare senza log/run originale.
- Script locale `scripts/archive_private_local.py` + runbook Windows: dry-run di default, copia (mai sposta), SHA-256, SQLite via Online Backup API,
  checkpoint attivi esclusi. Provato solo su dati sintetici [TEST].
- Limiti noti: gli script storici `scripts/create_*_archive.py` citano ancora i vecchi percorsi in radice (non aggiornati; non eseguiti);
  vari documenti nominano report spostati per nome (testo storico invariato; usare `MANIFEST.csv`).
  Aprire in sola lettura un DB WAL crea `-wal/-shm` vuoti: è successo durante l'ispezione e li ho rimossi (hash dei 87 `.db` tracciati verificati invariati).

## Test eseguiti (ambiente: Python 3.13, stub MOCK di `ijson`/`streamlit` fuori dal repo)
| Verifica | Esito |
|---|---|
| `python -m compileall -q ai analysis ui` | exit 0 |
| `tests/unit/test_discovery_progress_unification.py` (10 test: 26 parent, 5 microchunk 1/3/1, bisezione+retry, recupero 100% con 0 HTTP, zero topic, resa callback, PARTIAL/FAILED, compatibilità) | 10 passati [MOCK] |
| `tests/unit/test_trash_archive_integrity.py` (11 test: hash, riferimenti, gitignore, niente DB/zip in Trash, script locale) | 11 passati [TEST] |
| `tests/unit` completo (con 9 file UI ignorati come in baseline) | 51 falliti, 1839 passati: **stessi 51 della baseline** (tutti per `ijson`/`streamlit` assenti) |
| `tests/integration` | 30 falliti / 137 passati / 33 errori: identico alla baseline su albero pulito |
| `git diff --check` | exit 0 |

**NOT VERIFIED**: AppTest/UI Streamlit reale, i 51+9 test dipendenti da `ijson`/`streamlit`, qualsiasi comportamento con LM Studio. Sul PC Windows, con l'ambiente del progetto:
```
python -m pytest tests\unit\test_apptest_flows.py tests\unit\test_ui_smoke.py tests\unit\test_discovery_progress_unification.py tests\unit\test_trash_archive_integrity.py -q
echo exit=%ERRORLEVEL%
```
Poi avvia l'app e osserva a occhio il pannello su una Discovery sintetica breve (screenshot non ottenuti qui).

## Raccomandazioni
1. Decidi tu come trattare i file sensibili già pubblici (punto 1): è l'unico rischio reale emerso; il riordino non lo riduce.
2. Backup locale indipendente (disco esterno) di checkpoint e dati originali prima di qualsiasi operazione sulla storia.
3. Passo dedicato e separato per `temp_pytest/` (`git rm --cached` + `.gitignore`), dopo ispezione dei file.
