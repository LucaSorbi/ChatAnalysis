# Unificazione progress Discovery/Detection + riordino conservativo — report (30/10/2026, rev. dopo revisione)

PR #2 (draft). Branch `feat/discovery-progress-trash-20261030` da `main` @ `93c106d`. Commit: **A** `0d13bb9` (progress Discovery),
**B** `4e30039` (archivio `Trash/`), **C** (correzioni dopo la revisione del 30/10, vedi sotto). Nessun merge, nessuna riscrittura della storia,
nessun test LIVE. Etichette: STATICALLY_VERIFIED / TEST_VERIFIED / MOCK_VERIFIED / LIVE_VERIFIED / NOT_VERIFIED / FAILED.

**DECISIONE: MERGE BLOCKED.** (1) La UI Streamlit reale e i moduli AppTest non sono verificati con librerie autentiche (NOT_VERIFIED).
(2) Il repository pubblico contiene file versionati la cui sensibilità è da verificare; il riordino non la riduce e non riscrive la storia:
la decisione sul trattamento spetta al proprietario (vedi "Sicurezza"). Il triage dettagliato è solo locale, fuori dal repository.

## Sicurezza (sanificata)
- Misura immediata raccomandata al proprietario: rendere privato il repository (non rimuove copie, fork, cache o cronologia).
- È stata fatta una ricognizione ESCLUSIVAMENTE locale dei file versionati potenzialmente sensibili (categorie: residui di test,
  backup/copie di database, file di scratch, archivi). Esito pubblico, solo per categorie: nessuna credenziale confermata; sensibilità
  dei database e degli archivi **da verificare** dal proprietario; una voce (percorso locale personale in un file di scratch) è
  confermata. Elenchi, hash e dettagli restano fuori dal repository.
- Nessun file potenzialmente privato è stato spostato né copiato in `Trash/` (non diventerebbe privato) e non è stato usato `git rm --cached`
  come falsa garanzia. Eventuale rimozione dalla cronologia (git-filter-repo, secondo la documentazione GitHub, con backup fuori dal repo e
  coinvolgendo branch/PR/cloni) richiede approvazione esplicita e NON è stata eseguita.
- Limite: il testo del commit B e il corpo iniziale della PR descrivevano le categorie in modo più esplicito; restano nella cronologia del branch
  (non riscrivibile senza force-push, non autorizzato).

## Commit A — progress Discovery = Detection
Pannello condiviso (`ui/progress_view.py`, modello puro): blocchi principali X/26 con totale stabile, blocco corrente, microchunk con totale
dinamico, recuperati da checkpoint, falliti, bisezioni, richieste LLM, retry/timeout/HTTP/protocollo, tempo; barra dei blocchi mai al 100%
senza evento finale; nessuna ETA. Regola delle unità: il totale dei microchunk è il numero PIANIFICATO di primo livello; bisezione e retry non
creano unità; completed+failed ≤ total. Non toccati: prompt, parsing, chunking reale, schema evidenze, modello, seed, benchmark.

## Correzioni dopo la revisione (commit C)
1. **Callsite `split_chunk`** [STATICALLY_VERIFIED + TEST_VERIFIED]: l'ultimo callsite Discovery (percorso reattivo a chunk singolo) cresceva ancora
   `total_chunks` e riportava `str(errore)`. Ora `grow_total=False` e solo il nome della classe. I due callsite della Detection restano storici (contratto
   di `test_integrated_audit_fixes`). Un test AST impone `grow_total=False` per ogni callsite in `run_topic_discovery`.
2. **Regressione trovata e corretta**: nel percorso reattivo a chunk singolo il servizio riportava `COMPLETE` mentre il pannello chiudeva `PARTIAL`
   (parent non marcato SUCCESS). Ora parent SUCCESS/FAILED coerente con i microchunk validati. Test sul caso singolo e su 26 parent con un parent reattivo
   (nessuna rinumerazione, totale sempre 26).
3. **Barra microchunk**: ora `validati/totale` (4 validati + 1 fallito su 5 = 80%, stato PARTIAL, mai 100%); le "Unità processate" (validate+fallite) sono
   una riga separata. "Microchunk del blocco corrente" è etichettato come elaborati.
4. **Contabilità richieste**: invariante `inviate = riuscite + fallite + in corso` provata sul client LM Studio reale con `urlopen` sostituito
   (successo, HTTP 500, timeout, connessione rifiutata, JSON malformato: un solo esito per richiesta, nessun doppio conteggio). Se il client non riporta dati di trasporto
   pur avendo eseguito inferenze, il pannello mostra "non disponibile" invece di zero.
5. **Zero topic**: numeratore e denominatore coerenti (denominatore = sezioni di evidenza del documento, soglia 10). Casi 5, 8, 9 → `COMPLETE_NO_TOPICS`
   (banner blu "nessun argomento emerso", non verde); 10, 11 → `COMPLETE_EMPTY_UNVERIFIED` (banner giallo); 0 evidenze → errore esplicito;
   ≥10 evidenze con un microchunk non validato → `PARTIAL`, mai "non verificato".
6. **4 unità invece di 5**: dimostrato su 32 messaggi (`split_chunk_into_microchunks` = 4 unità pianificate); la bisezione sostituisce un'unità con due metà
   della stessa unità e non ne crea una quinta. Il vecchio scenario (begin(4), done, recuperato, bisected, failed) è conservato in un test esplicito:
   con il vecchio modello dava totale 5 con 3 soli esiti terminali (un'unità che non poteva concludersi).

## P1 — `Trash/` e riproducibilità
- 57 rinomine: `git diff -M --name-status 93c106d 4e30039` → 57 voci `R100` (prova indipendente di git) + SHA-256 nel manifest [TEST_VERIFIED].
- I builder storici `scripts/create_*` sono documentati come NON eseguibili in `scripts/HISTORICAL_ARCHIVE_BUILDERS.md` (nessuno modificato né eseguito).
- Indici per la tesi conservati; nessuna copia di dati reali in `Trash/`. L'inventario "dopo" è un'istantanea che non include sé stessa: limiti
  dell'autoverifica in `Trash/inventory/README.md`.

## Test e comandi (Python 3.13; `ijson`/`streamlit` autentici NON installabili qui: stub MOCK fuori dal repo)
| Verifica | Esito |
|---|---|
| `python -m compileall -q ai analysis ui scripts/archive_private_local.py scripts/preview_discovery_progress_panel.py` | exit 0 |
| `tests/unit/test_discovery_progress_unification.py` | 28 passati [MOCK_VERIFIED per la resa; TEST_VERIFIED per contatori/servizio] |
| `tests/unit/test_trash_archive_integrity.py` | 13 passati [TEST_VERIFIED] |
| `tests/unit/test_progress_panel_apptest.py` (AppTest reale) | 1 saltato → **NOT_VERIFIED** (nessuno Streamlit reale) |
| `tests/unit` completo (9 file UI ignorati come in baseline) | 51 falliti, 1859 passati, 2 saltati: gli stessi 51 della baseline (dipendenze `ijson`/`streamlit` mancanti), nessuna nuova rottura |
| `tests/integration` | 30 falliti / 137 passati / 33 errori: identico alla baseline |

## NOT_VERIFIED e comando Windows
Moduli AppTest prima non collezionati, i 51 test falliti per dipendenze e il pannello reale. Sul PC Windows (ambiente del progetto, Streamlit e ijson veri):
```
python -m pytest tests\unit\test_progress_panel_apptest.py tests\unit\test_apptest_flows.py tests\unit\test_ui_smoke.py tests\unit\test_discovery_progress_unification.py -q
echo exit=%ERRORLEVEL%
streamlit run scripts\preview_discovery_progress_panel.py
```
L'ultimo comando apre una Discovery SINTETICA breve (4 scenari: successo, parziale, zero topic con 12 evidenze, zero topic con 6 evidenze) con client finto, senza LM Studio
né dati reali: serve per uno screenshot locale. Nessuno screenshot è stato ottenuto qui. Nessuna Discovery LIVE completa.
