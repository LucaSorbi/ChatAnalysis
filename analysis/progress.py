"""
analysis/progress.py
--------------------
Sistema di monitoraggio e tracciamento in tempo reale dell'avanzamento per Topic Detection
e Topic Discovery, disaccoppiato da qualsiasi framework UI.

Principi architetturali e forensi:
1. DISACCOPPIAMENTO TOTALE DALLA UI:
   Il service layer emette eventi immutabili AnalysisProgressEvent; la UI Streamlit
   o un client CLI/API implementano il callback senza alcuna dipendenza inversa.
2. DISTINZIONE RIGOROSA TRA CICLO DI VITA DELLA RICHIESTA HTTP E DEL CHUNK:
   - Ciclo di vita HTTP: request_sent, response_received, http_error, timeout, protocol_error, valid_response, retry;
   - Ciclo di vita Chunk: start_chunk, chunk_success, chunk_failed, split_chunk, complete, partial;
   - Un fallimento HTTP (es. context overflow o HTTP 400) viene sempre contabilizzato in requests_failed,
     anche se il chunk associato viene successivamente recuperato con successo tramite sub-chunking.
3. INVARIANTE MATEMATICA INDEROGABILE SULLE RICHIESTE:
   requests_sent == requests_successful + requests_failed + requests_in_flight
   quando ogni richiesta raggiunge uno stato terminale (in-flight == 0).
4. DISTINZIONE ERRORI HTTP VS ERRORI DI PROTOCOLLO:
   Distingue tassativamente i guasti di trasporto (codici HTTP != 200, timeout, mancata connessione)
   dalle risposte HTTP 200 con JSON malformato o violazione di schema (protocol_error).
5. COERENZA DEGLI STATI COMPLETE / PARTIAL / FAILED:
   - Nessun chunk fallito viene conteggiato come "in attesa" (remaining esclude completati e falliti).
   - Un'analisi parziale (con chunk falliti) non emette mai il 100%.
   - I totali si adattano deterministicamente alle suddivisioni dinamiche (split_chunk).
   - Nessuna anomalia visuale tipo 2/1 o numeri negativi.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import threading
import time
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class AnalysisProgressEvent:
    """
    Evento immutabile di avanzamento dell'analisi tematica e contabilizzazione chiamate HTTP.
    """
    phase: str
    total_chunks: int
    completed_chunks: int
    current_chunk_index: int
    remaining_chunks: int
    requests_sent: int
    requests_successful: int
    retries_count: int
    requests_failed: int
    elapsed_seconds: float
    status_message: str
    requests_in_flight: int = 0
    http_errors_count: int = 0
    timeouts_count: int = 0
    protocol_errors_count: int = 0
    valid_responses_count: int = 0
    split_count: int = 0
    percentage: float = 0.0
    is_complete: bool = False
    is_failed: bool = False
    is_partial: bool = False
    failed_chunks: int = 0
    planned_chunks: int = 0
    subchunks_added: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # Contatori Detection a microchunk (default 0: compatibilità con eventi esistenti)
    microchunks_total: int = 0
    microchunks_completed: int = 0
    microchunks_recovered: int = 0
    microchunks_failed: int = 0
    bisections_count: int = 0
    # Errori del callback UI: solo conteggio e nome della classe dell'eccezione (mai il messaggio)
    callback_errors_count: int = 0
    last_callback_error: str | None = None
    # Finestra di microchunk del blocco principale corrente (0/0 se non autorevolmente disponibile)
    current_micro_total: int = 0
    current_micro_done: int = 0

    @property
    def elapsed_formatted(self) -> str:
        """Restituisce il tempo trascorso nel formato HH:MM:SS."""
        total_sec = int(self.elapsed_seconds)
        hours = total_sec // 3600
        minutes = (total_sec % 3600) // 60
        seconds = total_sec % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @property
    def is_requests_accounting_consistent(self) -> bool:
        """Verifica l'invariante: requests_sent == requests_successful + requests_failed + requests_in_flight."""
        return self.requests_sent == (
            self.requests_successful + self.requests_failed + self.requests_in_flight
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "total_chunks": self.total_chunks,
            "completed_chunks": self.completed_chunks,
            "current_chunk_index": self.current_chunk_index,
            "remaining_chunks": self.remaining_chunks,
            "requests_sent": self.requests_sent,
            "requests_successful": self.requests_successful,
            "retries_count": self.retries_count,
            "requests_failed": self.requests_failed,
            "requests_in_flight": self.requests_in_flight,
            "http_errors_count": self.http_errors_count,
            "timeouts_count": self.timeouts_count,
            "protocol_errors_count": self.protocol_errors_count,
            "valid_responses_count": self.valid_responses_count,
            "split_count": self.split_count,
            "is_requests_accounting_consistent": self.is_requests_accounting_consistent,
            "elapsed_seconds": self.elapsed_seconds,
            "elapsed_formatted": self.elapsed_formatted,
            "status_message": self.status_message,
            "percentage": self.percentage,
            "is_complete": self.is_complete,
            "is_failed": self.is_failed,
            "is_partial": self.is_partial,
            "failed_chunks": self.failed_chunks,
            "planned_chunks": self.planned_chunks,
            "subchunks_added": self.subchunks_added,
            "microchunks_total": self.microchunks_total,
            "microchunks_completed": self.microchunks_completed,
            "microchunks_recovered": self.microchunks_recovered,
            "microchunks_failed": self.microchunks_failed,
            "bisections_count": self.bisections_count,
            "callback_errors_count": self.callback_errors_count,
            "last_callback_error": self.last_callback_error,
            "current_micro_total": self.current_micro_total,
            "current_micro_done": self.current_micro_done,
            "metadata": dict(self.metadata),
        }


AnalysisProgressCallback = Callable[[AnalysisProgressEvent], None]

logger = logging.getLogger(__name__)


class AnalysisProgressTracker:
    """
    Tracciatore di stato thread-safe per raccogliere e propagare eventi di avanzamento.
    Garantisce disaggregazione totale del ciclo di vita HTTP dal ciclo di vita dei chunk.
    """

    def __init__(
        self,
        total_chunks: int,
        callback: AnalysisProgressCallback | None = None,
        initial_phase: str = "Inizializzazione",
    ) -> None:
        self.planned_chunks = max(1, total_chunks)
        self.subchunks_added = 0
        self.total_chunks = self.planned_chunks
        self.callback = callback
        self.phase = initial_phase
        self.completed_chunks = 0
        self.failed_chunks = 0
        self.current_chunk_index = 0
        self.requests_sent = 0
        self.requests_successful = 0
        self.retries_count = 0
        self.requests_failed = 0
        self.requests_in_flight = 0
        self.http_errors_count = 0
        self.timeouts_count = 0
        self.protocol_errors_count = 0
        self.valid_responses_count = 0
        self.split_count = 0
        self.microchunks_total = 0
        self.microchunks_completed = 0
        self.microchunks_recovered = 0
        self.microchunks_failed = 0
        self.bisections_count = 0
        self._active_micro_total = 0
        self._active_micro_done = 0
        self.callback_errors_count = 0
        self.last_callback_error: str | None = None
        self.start_time = time.perf_counter()
        self.is_complete = False
        self.is_failed = False
        self.is_partial = False
        self._lock = threading.Lock()

    @property
    def remaining_chunks(self) -> int:
        """Restituisce il numero di blocchi non ancora processati (esclude completati e falliti)."""
        return max(0, self.total_chunks - self.completed_chunks - self.failed_chunks)

    def check_requests_invariant(self) -> bool:
        """Verifica thread-safe dell'invariante requests_sent == successful + failed + in_flight."""
        with self._lock:
            return self.requests_sent == (
                self.requests_successful + self.requests_failed + self.requests_in_flight
            )

    def _calc_percentage(self) -> float:
        if self.total_chunks <= 0:
            return 0.0
        # Cappa al 95% finché la fase di consolidamento non è terminata
        # Durante l'elaborazione a microchunk del parent corrente, la frazione di microchunk
        # già esaminati contribuisce al progresso (mai oltre 0.99 di un parent non concluso).
        frac = 0.0
        if self._active_micro_total > 0:
            frac = min(0.99, self._active_micro_done / self._active_micro_total)
        raw = ((self.completed_chunks + frac) / self.total_chunks) * 95.0
        return round(min(95.0, max(0.0, raw)), 1)

    def _emit(
        self,
        status_msg: str,
        is_complete: bool = False,
        is_failed: bool = False,
        is_partial: bool = False,
        meta: dict[str, Any] | None = None,
        override_pct: float | None = None,
    ) -> AnalysisProgressEvent:
        elapsed = round(time.perf_counter() - self.start_time, 2)
        remaining = max(0, self.total_chunks - self.completed_chunks - self.failed_chunks)
        if override_pct is not None:
            pct = override_pct
        elif is_complete:
            pct = 100.0
        else:
            pct = self._calc_percentage()

        event = AnalysisProgressEvent(
            phase=self.phase,
            total_chunks=self.total_chunks,
            completed_chunks=self.completed_chunks,
            current_chunk_index=self.current_chunk_index,
            remaining_chunks=remaining,
            requests_sent=self.requests_sent,
            requests_successful=self.requests_successful,
            retries_count=self.retries_count,
            requests_failed=self.requests_failed,
            requests_in_flight=self.requests_in_flight,
            http_errors_count=self.http_errors_count,
            timeouts_count=self.timeouts_count,
            protocol_errors_count=self.protocol_errors_count,
            valid_responses_count=self.valid_responses_count,
            split_count=self.split_count,
            elapsed_seconds=elapsed,
            status_message=status_msg,
            percentage=pct,
            is_complete=is_complete,
            is_failed=is_failed,
            is_partial=is_partial,
            failed_chunks=self.failed_chunks,
            planned_chunks=self.planned_chunks,
            subchunks_added=self.subchunks_added,
            metadata=meta or {},
            microchunks_total=self.microchunks_total,
            microchunks_completed=self.microchunks_completed,
            microchunks_recovered=self.microchunks_recovered,
            microchunks_failed=self.microchunks_failed,
            bisections_count=self.bisections_count,
            callback_errors_count=self.callback_errors_count,
            last_callback_error=self.last_callback_error,
            current_micro_total=self._active_micro_total,
            current_micro_done=self._active_micro_done,
        )
        if self.callback:
            try:
                self.callback(event)
            except Exception as cb_exc:
                # Il callback UI non deve mai far fallire il processing del service layer, ma il
                # fallimento NON viene più taciuto: stato del tracker intatto, errore contabilizzato
                # e registrato in forma sanificata (solo classe dell'eccezione, mai il messaggio,
                # che potrebbe contenere testo di conversazione).
                self.callback_errors_count += 1
                self.last_callback_error = type(cb_exc).__name__
                if self.callback_errors_count == 1 or self.callback_errors_count % 50 == 0:
                    logger.warning(
                        "Callback di avanzamento fallito (%s); errori callback totali: %d. "
                        "L'analisi prosegue, l'interfaccia potrebbe non essere aggiornata.",
                        self.last_callback_error,
                        self.callback_errors_count,
                    )
        return event

    # =========================================================================
    # CICLO DI VITA DELLE RICHIESTE HTTP
    # =========================================================================

    def record_request_sent(self, phase: str | None = None) -> AnalysisProgressEvent:
        """Registra l'avvio e l'invio di una richiesta HTTP verso il backend LLM."""
        with self._lock:
            if phase:
                self.phase = phase
            self.requests_sent += 1
            self.requests_in_flight += 1
            msg = f"Richiesta HTTP {self.requests_sent} inviata (in volo: {self.requests_in_flight})..."
            return self._emit(msg)

    def record_http_response_received(self) -> AnalysisProgressEvent:
        """Registra la ricezione a livello di trasporto di una risposta HTTP."""
        with self._lock:
            msg = f"Risposta HTTP ricevuta per richiesta {self.requests_sent}."
            return self._emit(msg)

    def record_request_success(self, status_msg: str | None = None) -> AnalysisProgressEvent:
        """Registra la conclusione con successo di una richiesta HTTP e validazione payload."""
        with self._lock:
            self.requests_in_flight = max(0, self.requests_in_flight - 1)
            self.requests_successful += 1
            self.valid_responses_count += 1
            msg = status_msg or f"Risposta valida ricevuta con successo (totale riuscite: {self.requests_successful})."
            return self._emit(msg)

    def record_http_error(self, status_code: int | None = None, message: str = "") -> AnalysisProgressEvent:
        """Registra un errore HTTP a livello di trasporto o status code (es. 400, 500, connessione)."""
        with self._lock:
            self.requests_in_flight = max(0, self.requests_in_flight - 1)
            self.requests_failed += 1
            self.http_errors_count += 1
            code_str = f" HTTP {status_code}" if status_code else ""
            detail = f": {message}" if message else ""
            msg = f"Errore{code_str} durante richiesta HTTP{detail}."
            return self._emit(msg)

    def record_timeout(self, message: str = "") -> AnalysisProgressEvent:
        """Registra un timeout durante una richiesta HTTP."""
        with self._lock:
            self.requests_in_flight = max(0, self.requests_in_flight - 1)
            self.requests_failed += 1
            self.timeouts_count += 1
            detail = f": {message}" if message else ""
            msg = f"Timeout durante richiesta HTTP{detail}."
            return self._emit(msg)

    def record_protocol_error(self, message: str = "") -> AnalysisProgressEvent:
        """
        Registra una violazione di protocollo applicativo (es. risposta HTTP 200 ricevuta,
        ma con payload JSON malformato o schema non rispettato).
        Distingue rigorosamente l'errore di protocollo/parsing da un guasto HTTP di trasporto.
        """
        with self._lock:
            self.requests_in_flight = max(0, self.requests_in_flight - 1)
            self.requests_failed += 1
            self.protocol_errors_count += 1
            detail = f": {message}" if message else ""
            msg = f"Errore di protocollo (HTTP 200 con JSON/schema non valido){detail}."
            return self._emit(msg)

    def record_retry(self, reason: str = "") -> AnalysisProgressEvent:
        """Registra un tentativo di retry pianificato."""
        with self._lock:
            self.retries_count += 1
            detail = f" ({reason})" if reason else ""
            msg = f"Tentativo di retry registrato{detail} (totale retry: {self.retries_count})."
            return self._emit(msg)

    # =========================================================================
    # CICLO DI VITA DEI CHUNK DOCUMENTALI
    # =========================================================================

    def split_chunk(
        self,
        chunk_idx_1based: int,
        subchunks_count: int = 2,
        failed_prior_request: bool = True,
        prior_error_msg: str = "",
        grow_total: bool = True,
    ) -> AnalysisProgressEvent:
        """
        Registra la suddivisione dinamica di un chunk oversize in sotto-chunk.
        Con grow_total=False (Discovery/Detection a microchunk) il totale dei blocchi principali NON cresce:
        i sotto-blocchi sono contati dai contatori `microchunks_*`; resta solo la contabilità della richiesta fallita.
        Se una richiesta per questo blocco era in volo e ha causato lo split (es. per context overflow),
        la contabilizza rigorosamente come richiesta fallita, garantendo l'invariante:
        requests_sent == requests_successful + requests_failed + requests_in_flight.
        """
        with self._lock:
            if failed_prior_request and self.requests_in_flight > 0:
                self.requests_in_flight = max(0, self.requests_in_flight - 1)
                self.requests_failed += 1
                self.http_errors_count += 1

            added = max(0, subchunks_count - 1) if grow_total else 0
            self.subchunks_added += added
            self.total_chunks += added
            self.split_count += 1
            detail = f" a causa di: {prior_error_msg}" if prior_error_msg else ""
            if grow_total:
                msg = (
                    f"Blocco {chunk_idx_1based} suddiviso in {subchunks_count} sotto-blocchi{detail} "
                    f"(totale blocchi aggiornato: {self.total_chunks})."
                )
            else:
                msg = f"Blocco {chunk_idx_1based} di {self.total_chunks} suddiviso in micro-chunk{detail}."
            return self._emit(msg)

    def start_chunk(
        self,
        chunk_idx_1based: int,
        phase: str = "Analisi dei messaggi",
        record_request: bool = True,
    ) -> AnalysisProgressEvent:
        """Inizia l'elaborazione di un chunk documentale."""
        with self._lock:
            self.phase = phase
            self.current_chunk_index = chunk_idx_1based
            if record_request:
                self.requests_sent += 1
                self.requests_in_flight += 1
            msg = f"Elaborazione blocco {chunk_idx_1based} di {self.total_chunks}..."
            return self._emit(msg)

    def chunk_success(self, chunk_idx_1based: int, record_request: bool = True) -> AnalysisProgressEvent:
        """Registra il completamento con successo di un chunk documentale."""
        with self._lock:
            self.completed_chunks = min(self.total_chunks, self.completed_chunks + 1)
            if record_request and self.requests_in_flight > 0:
                self.requests_in_flight = max(0, self.requests_in_flight - 1)
                self.requests_successful += 1
                self.valid_responses_count += 1
            msg = f"Blocco {chunk_idx_1based} di {self.total_chunks} completato con successo."
            return self._emit(msg)

    def chunk_retry(self, chunk_idx_1based: int, reason: str = "") -> AnalysisProgressEvent:
        """
        Registra il retry di un chunk.
        La richiesta precedente che ha innescato il retry viene contabilizzata come fallita,
        mentre la nuova richiesta viene registrata come inviata e in volo.
        """
        with self._lock:
            if self.requests_in_flight > 0:
                self.requests_in_flight = max(0, self.requests_in_flight - 1)
                self.requests_failed += 1
            self.retries_count += 1
            self.requests_sent += 1
            self.requests_in_flight += 1
            detail = f" ({reason})" if reason else ""
            msg = f"Retry blocco {chunk_idx_1based} di {self.total_chunks}{detail}..."
            return self._emit(msg)

    def chunk_failed(
        self,
        chunk_idx_1based: int,
        error_msg: str = "",
        record_request: bool = True,
        is_protocol_or_validation: bool = False,
    ) -> AnalysisProgressEvent:
        """Registra il fallimento definitivo di un chunk documentale."""
        with self._lock:
            if record_request and self.requests_in_flight > 0:
                self.requests_in_flight = max(0, self.requests_in_flight - 1)
                self.requests_failed += 1
                if is_protocol_or_validation:
                    self.protocol_errors_count += 1
                else:
                    self.http_errors_count += 1
            self.failed_chunks += 1
            detail = f": {error_msg}" if error_msg else ""
            msg = f"Blocco {chunk_idx_1based} di {self.total_chunks} fallito{detail}."
            return self._emit(msg)

    # =========================================================================
    # CICLO DI VITA DEI MICROCHUNK (Topic Detection e Topic Discovery)
    # I contatori dei parent (completed_chunks/total_chunks) NON vengono alterati dai microchunk.
    #
    # REGOLA DELLE UNITA' DI LAVORO: l'unità è il microchunk PIANIFICATO di primo livello. Il totale
    # (`microchunks_total`) cresce solo quando si pianifica un nuovo blocco principale (totale dinamico).
    # Ogni unità termina UNA sola volta, in modo disgiunto: completata (nuova inferenza o recuperata dal
    # checkpoint) oppure fallita. Bisezioni e retry NON creano unità né successi: sono contatori a parte
    # (`bisections_count`, `retries_count`). Quindi completed + failed <= total in ogni momento.
    # =========================================================================

    def begin_microchunks(self, parent_idx_1based: int, count: int) -> AnalysisProgressEvent:
        """Registra il piano di microchunk del parent corrente (totale dinamico)."""
        with self._lock:
            n = max(0, int(count))
            self.current_chunk_index = parent_idx_1based
            self.microchunks_total += n
            self._active_micro_total = n
            self._active_micro_done = 0
            msg = f"Blocco {parent_idx_1based} di {self.total_chunks} pianificato in {n} micro-chunk."
            return self._emit(msg)

    def microchunk_done(self, recovered: bool = False) -> AnalysisProgressEvent:
        """Un microchunk concluso con successo (nuova inferenza o recupero da checkpoint)."""
        with self._lock:
            self.microchunks_completed += 1
            if recovered:
                self.microchunks_recovered += 1
            self._active_micro_done += 1
            kind = "recuperato da checkpoint" if recovered else "completato"
            msg = f"Micro-chunk {kind} ({self.microchunks_completed}/{self.microchunks_total})."
            return self._emit(msg)

    def microchunk_failed(self) -> AnalysisProgressEvent:
        """Un microchunk fallito in modo definitivo (non certifica il parent)."""
        with self._lock:
            self.microchunks_failed += 1
            self._active_micro_done += 1
            msg = f"Micro-chunk fallito (falliti: {self.microchunks_failed}/{self.microchunks_total})."
            return self._emit(msg)

    def microchunk_bisected(self) -> AnalysisProgressEvent:
        """Bisezione adattiva: contata a parte. NON modifica totale, completati né falliti (stessa unità)."""
        with self._lock:
            self.bisections_count += 1
            msg = f"Micro-chunk suddiviso (bisezioni: {self.bisections_count})."
            return self._emit(msg)

    def end_microchunks(self) -> AnalysisProgressEvent:
        """Chiude la finestra di microchunk del parent corrente."""
        with self._lock:
            self._active_micro_total = 0
            self._active_micro_done = 0
            return self._emit("Micro-chunk del blocco corrente conclusi.")

    def start_consolidation(self) -> AnalysisProgressEvent:
        """Registra l'avvio della fase di consolidamento / aggregazione topic."""
        with self._lock:
            self.phase = "Consolidamento topic"
            msg = "Consolidamento dei risultati dei singoli blocchi in corso..."
            return self._emit(msg)

    def complete(
        self,
        summary_msg: str = "Analisi completata con successo.",
        is_partial: bool = False,
        thematic_unverified: bool = False,
        no_topics: bool = False,
    ) -> AnalysisProgressEvent:
        """
        Segnala il completamento dell'analisi.
        Se vi sono blocchi falliti o se is_partial è True, l'evento viene emesso come
        PARTIAL e la percentuale rispecchia la copertura reale (non 100%).
        """
        with self._lock:
            should_be_partial = is_partial or self.failed_chunks > 0 or self.completed_chunks < self.total_chunks
            micro_progress = self.microchunks_completed > 0 and self.completed_chunks == 0
            if should_be_partial and (self.completed_chunks > 0 or micro_progress):
                self.phase = "Completato parzialmente"
                self.is_complete = False
                self.is_partial = True
                self.is_failed = False
                if self.completed_chunks > 0:
                    pct = round((self.completed_chunks / self.total_chunks) * 100.0, 1) if self.total_chunks > 0 else 0.0
                else:
                    # Nessun parent concluso ma microchunk validati: copertura reale a livello di microchunk
                    pct = round((self.microchunks_completed / max(1, self.microchunks_total)) * 100.0, 1)
                    pct = min(pct, 99.0)
                return self._emit(summary_msg, is_complete=False, is_failed=False, is_partial=True, override_pct=pct)
            elif should_be_partial and self.completed_chunks == 0:
                self.phase = "Fallito"
                self.is_complete = False
                self.is_partial = False
                self.is_failed = True
                return self._emit(summary_msg, is_complete=False, is_failed=True, is_partial=False, override_pct=0.0)
            else:
                self.phase = "Completato"
                self.is_complete = True
                self.is_partial = False
                self.is_failed = False
                return self._emit(
                    summary_msg, is_complete=True, is_failed=False, is_partial=False, override_pct=100.0,
                    meta=(
                        {"thematic_unverified": True} if thematic_unverified
                        else ({"no_topics": True} if no_topics else None)
                    ),
                )

    def partial(self, summary_msg: str = "Analisi completata parzialmente.") -> AnalysisProgressEvent:
        """Segnala esplicitamente un completamento parziale (PARTIAL)."""
        return self.complete(summary_msg=summary_msg, is_partial=True)

    def fail(self, error_msg: str) -> AnalysisProgressEvent:
        """Segnala il fallimento globale dell'analisi."""
        with self._lock:
            self.phase = "Fallito"
            self.is_failed = True
            self.is_complete = False
            self.is_partial = False
            pct = round((self.completed_chunks / self.total_chunks) * 100.0, 1) if self.total_chunks > 0 else 0.0
            return self._emit(f"Analisi fallita: {error_msg}", is_complete=False, is_failed=True, is_partial=False, override_pct=pct)
