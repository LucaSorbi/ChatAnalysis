# Topic Detection a microchunk — checkpoint, resume e runbook Windows

Stato della verifica: STATIC / TEST / MOCK. **Nessun LIVE VERIFIED** finché non si esegue la prova del paragrafo 3 su
LM Studio reale. La causa esatta delle 5 ore senza chunk completati resta NON VERIFICATA senza i log privati.

## 1. Comportamento della Detection

- Un parent con più di 8 messaggi sostanziali è pre-diviso in microchunk deterministici (come la Discovery).
- Ogni microchunk fa una chiamata; su errore strutturale/di contesto è bisecato (profondità max 3, ≤15 nodi × 2 tentativi
  = ≤30 chiamate). I timeout NON sono bisecati.
- Limiti finiti: 3 timeout consecutivi **oppure** 10 timeout complessivi interrompono la run con l'eccezione del
  timeout (progresso validato conservato, ripresa possibile); budget di 600 s per microchunk, oltre il quale non si
  bisecano né si avviano nuovi nodi (il microchunk resta FAILED). Costanti in `analysis/service.py`.
- Un microchunk fallito non dà mai SUCCESS del parent né ABSENT globale. ABSENT solo se tutti i microchunk attesi sono
  SUCCESS e ABSENT; PRESENT con copertura parziale resta PARTIAL; se nessun microchunk riesce la run fallisce.
- Contabilità separata nei metadati: `chunk_count/completed_chunk_count/failed_chunk_count` = parent pianificati
  (es. 26), `microchunk_*` = unità di inferenza. Il percorso reattivo storico del singolo parent diviso dopo un
  overflow conserva la contabilità a sotto-blocchi (test storico `test_integrated_audit_fixes`).

## 2. Checkpoint e resume

- Ogni microchunk validato è salvato subito in `checkpoint_subchunks`; il parent in `checkpoint_chunks` solo quando tutti
  i suoi microchunk sono validati. Un microchunk fallito non è mai salvato.
- **Nessuna cancellazione automatica.** In precedenza il percorso multi-chunk chiamava `clear_checkpoint` dopo un SUCCESS,
  che (con `foreign_keys=ON`) eliminava a cascata `checkpoints`, `checkpoint_chunks` e `checkpoint_subchunks`: ora i
  checkpoint completati restano (come per la Discovery) e una nuova esecuzione con stessa chiave non rifà alcuna chiamata.
  La cancellazione resta solo esplicita (`clear_for_document`, `clear_all_for_document_id`).
- Chiave: fingerprint del documento + configurazione (modello, versione del prompt, topic, temperatura, seed, limiti di
  chunking, identità dei chunk; vedi `compute_run_config_hash`). Se cambia, il vecchio checkpoint non è riusato.
- Fail-closed: al resume ogni microchunk salvato è rivalidato (evidence ID esistenti, coerenza col piano); se invalido
  è scartato e rifatto. Nessuna migrazione e nessun cambio di schema; la compatibilità con checkpoint creati dalla
  versione precedente è provata solo con test sintetici.
- **Inferenza ≠ persistenza.** Se una scrittura fallisce il risultato semantico NON cambia, ma
  `detection_diagnostics` riporta `checkpoint_write_failures`, `checkpoint_persistence` (`OK` / `WRITE_FAILURES` /
  `NOT_ATTEMPTED`) e `resume_guaranteed=false`; i record di diagnostica del parent hanno `checkpoint_saved=false`.
  Con `resume_guaranteed=false` non si deve contare sulla ripresa: i microchunk non salvati verranno rifatti.

## 3. Prova LIVE limitata (UNA sola, dati sintetici, checkpoint isolato)

Prerequisiti: LM Studio avviato con Qwen2.5-7B-Instruct Q4_K_M e server locale su `127.0.0.1:1234`; ambiente Python del
progetto attivo; nessuna conversazione reale coinvolta (il documento è sintetico, 24 messaggi = 3 microchunk); la
cartella dei checkpoint dell'app NON viene usata (il script ne crea una temporanea nuova).

```
python scripts\run_limited_detection_live.py --messages 24 > prova_live.txt
echo exit=%ERRORLEVEL%
```

PASS (tutte le condizioni): `exit=0`, `origine=LIVE_VERIFIED`, `esito_tecnico=PASS`, `status=SUCCESS`, `parent=1/1`,
`microchunk=3/3 falliti=0`, `persistenza=OK`, `chiamate_chat_completion` tra 3 e 90 (3 = nessun retry; di più indica
retry/bisezioni da riportare). `exit=0` significa SOLO criteri tecnici soddisfatti: non prova la qualità del modello.
La riga `qualita_scientifica` (atteso PRESENT sul sintetico) è informativa e separata dall'esito tecnico.

FAIL: `exit=1` (status diverso da SUCCESS, copertura incompleta, errore di scrittura, evidenze non valide, eccezione) oppure
`exit=2` (LM Studio non raggiungibile → NOT_VERIFIED). Dopo qualunque FAIL non rilanciare: inviare il report.
Interrompere con Ctrl+C se la prova supera ~15 minuti (normalmente dura pochi minuti): il progresso validato resta
nella cartella temporanea indicata da `checkpoint_dir=`.

Report sanificato da inviare: il solo contenuto di `prova_live.txt` (righe origine/status/parent/chiamate, `contatori=`,
tabella dei microchunk). Non contiene testo di conversazione né output del modello. NON inviare il database dei checkpoint.
`--fake` esegue la stessa prova con un client simulato: esito `MOCK_VERIFIED`, mai promosso a LIVE.

Conteggio chiamate: `chiamate_chat_completion` conta le chiamate reali al client; `http_requests_sent` è il contatore del
tracker (include eventuali retry interni del trasporto); `detect_topic_calls + retry` deve eguagliare le chiamate reali
quando non ci sono retry di trasporto.

## 4. Osservare i contatori senza rilanciare la Detection 26/26 (sola lettura)

```
python scripts\detection_microchunk_status.py --db .checkpoints\checkpoints.db
```

Con l'app in esecuzione NON copiare a mano `checkpoints.db`, `-wal`, `-shm` (una copia ingenua di file in scrittura può
essere incoerente). Usare lo snapshot consistente (Online Backup API di SQLite, sorgente aperta in sola lettura,
`PRAGMA integrity_check` sulla copia, destinazione in una cartella NUOVA):

```
python scripts\detection_microchunk_status.py --db .checkpoints\checkpoints.db --snapshot-dir C:\temp\snap_chk
```

In alternativa chiudere l'app e copiare i file offline. I checkpoint originali non vengono mai modificati. Per prove
dell'app con checkpoint isolati: `set CHATANALYSIS_CHECKPOINT_DIR=C:\temp\chk_prova` prima di avviare Streamlit.

## 5. Note statiche sulle prestazioni (non misurate)

- Prompt di sistema fisso: ~5.100 caratteri contro ~3.200 di evidenze per un microchunk da 8 messaggi sintetici (≈61% fisso).
  Più microchunk = più chiamate = più overhead fisso: il pre-split limita la latenza per chiamata, non è dimostrato
  che riduca il tempo totale. Tempi totali NON misurati.
- Caso peggiore teorico: ogni nodo di bisezione ha timeout 240 s × 2 tentativi, ma i timeout non si bisecano e sono
  limitati dai budget del paragrafo 1; il valore di 600 s e il tetto di 10 timeout sono scelte prudenti, non calibrate
  su dati reali.

## 6. Nota sui 360 MB

La dimensione dell'export grezzo (360 MB) non equivale al testo normalizzato analizzato: media, metadati e duplicazioni
pesano sul raw ma non sul numero di messaggi/chunk. La scalabilità non si stima dai MB ma dal numero di messaggi
sostanziali e di microchunk (vedi `docs/SCALABILITY_MEASUREMENTS_REPORT.md`).

## 7. Limite qualitativo noto della Discovery

Non c'è deduplicazione semantica automatica: topic molto simili tra chunk possono restare come etichette distinte e
ravvicinate (es. 10 etichette quasi equivalenti). È un limite di qualità da dichiarare in tesi; la fusione è manuale.
