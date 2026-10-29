# Topic Detection a microchunk — checkpoint, resume e runbook Windows

Stato della verifica: STATIC / TEST / MOCK. **Nessun LIVE VERIFIED** finché non si esegue il passo 2 sotto
su LM Studio reale. Le cause esatte delle 5 ore senza chunk completati restano NON VERIFICATE senza i log privati.

## 1. Cosa fa ora la Detection

- Un chunk padre con più di 8 messaggi sostanziali viene pre-diviso in microchunk deterministici (come la Discovery).
- Ogni microchunk fa una chiamata; se fallisce in modo recuperabile viene bisecato (profondità max 3, al massimo
  15 nodi × 2 tentativi = 30 chiamate per microchunk). I timeout NON vengono bisecati.
- Circuit breaker: 3 timeout consecutivi tra microchunk interrompono il padre (PARTIAL), senza attendere il resto.
- Un microchunk fallito non produce mai SUCCESS del padre né ABSENT globale: l'aggregazione dà ABSENT solo se
  tutti i microchunk attesi sono SUCCESS e ABSENT. PRESENT vince sempre; altrimenti UNCERTAIN/PARTIAL.

## 2. Regole di checkpoint e resume

- Ogni microchunk validato (SUCCESS) è salvato subito in `checkpoint_subchunks`; il padre solo quando tutti i suoi
  microchunk sono validati. Un microchunk fallito non viene mai salvato.
- Chiave di resume: fingerprint del documento + fingerprint della configurazione (modello, versione del prompt, topic, temperatura, seed,
  limiti di chunking, identità dei chunk: vedi `compute_run_config_hash`). Cambiando uno di questi, il vecchio checkpoint non è riutilizzato (nuova chiave).
- Fail-closed: al resume ogni microchunk salvato è rivalidato (evidence ID esistenti, coerenza col piano); se non
  valido viene scartato e rifatto, mai accettato. Nessuna migrazione distruttiva, nessuna cancellazione automatica.
- I sub-checkpoint usano la tabella `checkpoint_subchunks` già esistente (nessun cambio di schema, nessuna migrazione).
  La compatibilità con checkpoint di Detection creati dalla versione precedente è verificata solo con test sintetici.

## 3. Runbook Windows (non distruttivo)

NON rilanciare la Detection 26/26 né toccare `.checkpoints` dell'app. Usare una cartella nuova.

1. Chiudere Streamlit. Avviare LM Studio con il modello Qwen2.5-7B-Instruct Q4_K_M sul server locale (porta 1234).
2. Prova LIVE limitata su dati sintetici (3 microchunk, cartella temporanea nuova):

   ```
   python scripts\run_limited_detection_live.py --messages 24
   ```

   Exit 0 = eseguita; 1 = fallita; 2 = LM Studio non raggiungibile (NOT_VERIFIED). L'output contiene solo contatori,
   durate, token. `--fake` esegue la stessa prova con un client simulato (MOCK_VERIFIED, mai LIVE).
3. Osservare i contatori di una corsa reale SENZA rilanciarla (sola lettura, nessun testo di conversazione):

   ```
   python scripts\detection_microchunk_status.py --db .checkpoints\checkpoints.db
   python scripts\detection_microchunk_status.py --db .checkpoints\checkpoints.db --json
   ```

   Se l'app sta scrivendo e la lettura fallisce, copiare `checkpoints.db`, `-wal` e `-shm` in una cartella temporanea
   e puntare `--db` alla copia.
4. Per una prova dell'app con checkpoint isolati: impostare `set CHATANALYSIS_CHECKPOINT_DIR=C:\temp\chk_prova`
   prima di avviare Streamlit.
5. Lettura dei contatori: `meta["detection_diagnostics"]` (microchunk totali/completati/falliti, chiamate HTTP,
   retry, bisezioni, timeout, errori) e `meta["chunk_diagnostics"]` (per microchunk: stato, tentativi, secondi, token).
   La UI mostra micro-chunk completati/totali; un errore di rendering della UI è contato (`callback_errors_count`)
   e non viene più silenziato né interrompe l'analisi.

## 4. Note statiche sulle prestazioni (P1, non misurate)

- Per ogni chiamata il prompt di sistema è fisso: ~5.100 caratteri contro ~3.200 di evidenze per un microchunk da
  8 messaggi sintetici (circa il 61% dei caratteri è fisso). Più microchunk = più chiamate = più overhead fisso:
  il pre-split riduce la latenza per chiamata, non necessariamente il tempo totale. Tempi totali NON misurati.
- Timeout 240 s × 2 tentativi per chiamata, più retry correttivi e fallback GBNF: il caso peggiore per microchunk
  è limitato (30 chiamate) ma non trascurabile. Nessun cambio di modello, decoding, seed o temperatura.

## 5. Nota sui 360 MB

La dimensione dell'export grezzo (360 MB) non equivale al testo normalizzato analizzato: media, metadati e
duplicazioni pesano sul raw ma non sul numero di messaggi/chunk. La scalabilità non si stima dai MB: va dedotta
dal numero di messaggi sostanziali e di microchunk (vedi `docs/SCALABILITY_MEASUREMENTS_REPORT.md`).

## 6. Limite qualitativo noto della Discovery

Non c'è deduplicazione semantica automatica: topic molto simili tra chunk possono restare come etichette distinte
ravvicinate (es. 10 etichette quasi equivalenti). È un limite di qualità da dichiarare in tesi, non un errore di
completezza; la fusione resta una revisione manuale.
