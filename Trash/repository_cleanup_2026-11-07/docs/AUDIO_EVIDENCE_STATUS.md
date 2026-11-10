# Messaggi vocali (.opus): stato veritiero in UI ed export

Questa nota descrive la modifica **additiva** che rende esplicito se un messaggio vocale ha prodotto testo analizzabile.
Non cambia prompt, Discovery/Detection, validazione degli alias, seed, fingerprint né lo schema dei checkpoint, e **non
avvia mai la STT automaticamente**: la trascrizione resta opt-in (pulsante «Elabora media localmente»).

## Stati (derivati, nessuna esecuzione)

| Stato | Significato |
|---|---|
| `AUDIO_NOT_FOUND` | messaggio vocale ma l'export non contiene il file audio (export solo `.txt`, oppure ZIP senza l'allegato): nessun pulsante, nessuna trascrizione possibile |
| `STT_NOT_RUN` | file audio presente, nessun risultato STT associato (elaborazione on-demand non ancora eseguita) |
| `STT_UNAVAILABLE` | elaborazione tentata ma motore locale non installato / modello non caricabile |
| `TRANSCRIPTION_FAILED` | motore disponibile ma la trascrizione di quel file è fallita |
| `TRANSCRIBED_EMPTY` | trascrizione riuscita senza testo (nessuna evidenza prodotta) |
| `TRANSCRIBED` | trascrizione riuscita con testo: esiste una sezione `STT_TRANSCRIPTION` (dato **derivato**) |

Il testo STT è un dato derivato e **non** ha attendibilità forense automatica. Per un messaggio non `TRANSCRIBED`
l'analisi vede solo la riga di testo dell'export (di norma il nome del file), non ciò che è stato detto.

## Dove compare

- **UI** (Riepilogo documento ed Esplora conversazione): un riquadro con soli conteggi; visibile anche quando non c'è
  alcun allegato risolvibile (caso `AUDIO_NOT_FOUND`, prima invisibile).
- **Export** (`document.audio_evidence`, additivo): solo interi — `voice_messages_total` e un conteggio per stato
  (la somma degli stati è `voice_messages_total`). Il blocco è **assente** se il documento non ha vocali, quindi gli
  export senza vocali restano invariati. Nessun nome file, testo, trascrizione o identificativo.
- **Provenienza del dato derivato**: `AudioTranscriptionResult` conserva id messaggio, sorgente, stato, motore,
  modello, dispositivo, lingua/probabilità e `provenance_asset` (sha256 del file). Il motivo strutturato di un fallimento è
  in `metadata["stt_reason"]` (`STT_UNAVAILABLE` / `TRANSCRIPTION_FAILED`); il testo di `error_message` non è cambiato
  e non viene mai mostrato né esportato.

## Impronta del documento e checkpoint

Un tentativo STT fallito o non disponibile **non** cambia l'impronta del documento (nessuna sezione prodotta). Una
trascrizione riuscita aggiunge evidenza testuale: è un documento nuovo e i checkpoint esistenti restano intatti e non
vengono riutilizzati per esso.

## Limiti noti

- Se l'archivio sorgente non è più disponibile, o un allegato non si risolve, il messaggio resta `STT_NOT_RUN` (nessun
  risultato per-messaggio viene creato); il conteggio aggregato di fallimento in `multimodal_*` è invariato.
- Il risultato `FAILED` per «STT non configurato» porta i valori di default di `engine`/`model_name` della dataclass
  (`faster-whisper`/`tiny`) anche se nessun motore è stato eseguito: non è una prova di esecuzione.
- Il trascrittore usa il modello Whisper `tiny` senza percorso locale configurato: con il comportamento standard di
  faster-whisper il modello viene scaricato al primo uso se non è già in cache (la trascrizione è locale, il download del
  modello no). Comportamento non verificato in questo ambiente.
