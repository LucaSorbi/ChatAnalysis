"""
ui/progress_view.py
-------------------
Modello di visualizzazione PURO (nessuna dipendenza da Streamlit) del pannello di avanzamento,
condiviso da Topic Discovery e Topic Detection.

Regole di veridicità:
- il denominatore dei blocchi principali è `total_chunks` (es. 26) e non viene mai sostituito dal totale dei microchunk;
- i microchunk hanno un totale DINAMICO (cresce quando si pianifica un nuovo blocco principale);
- la barra dei blocchi principali non raggiunge il 100% se non è arrivato l'evento finale di completamento;
- recuperati da checkpoint, falliti e bisezioni sono contatori separati; le richieste HTTP restano distinte;
- nessuna ETA inventata; nessun testo di conversazione nei messaggi.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from analysis.progress import AnalysisProgressEvent

STATUS_RUNNING = "IN_CORSO"
STATUS_SUCCESS = "SUCCESS"
STATUS_SUCCESS_UNVERIFIED = "COMPLETATA_NON_VERIFICATA"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"

_BADGES = {
    STATUS_RUNNING: "🟦 IN CORSO",
    STATUS_SUCCESS: "🟩 COMPLETATA",
    STATUS_SUCCESS_UNVERIFIED: "🟨 COMPLETATA TECNICAMENTE - TEMATICAMENTE NON VERIFICATA",
    STATUS_PARTIAL: "🟧 PARZIALE",
    STATUS_FAILED: "🟥 FALLITA",
}


@dataclass(frozen=True)
class ProgressView:
    title: str
    phase_label: str
    status: str
    badge: str
    parent_line: str
    parent_fraction: float
    current_line: str | None
    micro_line: str | None
    micro_fraction: float | None
    micro_current_line: str | None
    micro_detail_line: str | None
    requests_line: str
    requests_detail_line: str
    elapsed_line: str
    message: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


def classify_status(event: AnalysisProgressEvent) -> str:
    if event.is_failed:
        return STATUS_FAILED
    if event.is_partial:
        return STATUS_PARTIAL
    if event.is_complete:
        return STATUS_SUCCESS_UNVERIFIED if event.metadata.get("thematic_unverified") else STATUS_SUCCESS
    return STATUS_RUNNING


def classify_phase(event: AnalysisProgressEvent) -> str:
    st = classify_status(event)
    if st == STATUS_FAILED:
        return "Fallito"
    if st == STATUS_PARTIAL:
        return "Completato parzialmente"
    if st in (STATUS_SUCCESS, STATUS_SUCCESS_UNVERIFIED):
        return "Completato"
    ph = (event.phase or "").casefold()
    if "consolidamento" in ph:
        return "Consolidamento"
    if "recuperati" in ph or "checkpoint" in ph:
        return "Recupero da checkpoint"
    if event.microchunks_total > 0:
        return "Elaborazione microchunk"
    if "inizializzazione" in ph or event.requests_sent == 0 and event.completed_chunks == 0:
        return "Preparazione"
    return "Elaborazione"


def build_progress_view(event: AnalysisProgressEvent, title: str = "ANALISI IN CORSO") -> ProgressView:
    status = classify_status(event)
    final = status != STATUS_RUNNING
    total = max(1, int(event.total_chunks))
    done = max(0, int(event.completed_chunks))

    # Copertura blocchi principali: mai 100% senza l'evento finale di completamento riuscito.
    if status in (STATUS_SUCCESS, STATUS_SUCCESS_UNVERIFIED):
        parent_fraction = 1.0
    else:
        parent_fraction = min(0.99, done / total) if not final else min(1.0, done / total)
    parent_line = f"Blocchi principali: {done} / {event.total_chunks}"
    if event.failed_chunks > 0:
        parent_line += f" (non validati: {event.failed_chunks})"

    current_line = None
    if not final and event.current_chunk_index > 0:
        current_line = f"Blocco principale corrente: {min(event.current_chunk_index, event.total_chunks)} / {event.total_chunks}"

    micro_line = micro_current = micro_detail = None
    micro_fraction: float | None = None
    if event.microchunks_total > 0:
        terminal = event.microchunks_completed + event.microchunks_failed
        micro_line = f"Microchunk completati: {event.microchunks_completed} / {event.microchunks_total} (totale dinamico)"
        micro_fraction = max(0.0, min(1.0, terminal / event.microchunks_total))
        if not final and event.current_micro_total > 0:
            micro_current = f"Microchunk del blocco corrente: {min(event.current_micro_done, event.current_micro_total)} / {event.current_micro_total}"
        micro_detail = (
            f"Recuperati da checkpoint: {event.microchunks_recovered} | "
            f"Microchunk falliti: {event.microchunks_failed} | Bisezioni: {event.bisections_count}"
        )

    requests_line = (
        f"Richieste LLM: inviate {event.requests_sent}, riuscite {event.requests_successful}, "
        f"fallite {event.requests_failed}, in corso {event.requests_in_flight}"
    )
    requests_detail = (
        f"Retry {event.retries_count} | Timeout {event.timeouts_count} | "
        f"Errori HTTP {event.http_errors_count} | Protocollo/JSON {event.protocol_errors_count}"
    )

    warnings: list[str] = []
    if status == STATUS_SUCCESS_UNVERIFIED:
        warnings.append(
            "Elaborazione tecnicamente completata, ma nessun argomento attendibile emerso: "
            "il risultato NON è verificato dal punto di vista tematico."
        )
    if status == STATUS_PARTIAL:
        warnings.append("Copertura incompleta: alcuni blocchi o microchunk non sono stati validati.")
    if not event.is_requests_accounting_consistent:
        warnings.append("Contabilità richieste non coerente (inviate ≠ riuscite + fallite + in corso).")
    if event.callback_errors_count > 0:
        warnings.append(
            f"Errori di aggiornamento interfaccia: {event.callback_errors_count} "
            f"(ultimo: {event.last_callback_error}). L'analisi prosegue regolarmente."
        )

    return ProgressView(
        title=title,
        phase_label=classify_phase(event),
        status=status,
        badge=_BADGES[status],
        parent_line=parent_line,
        parent_fraction=parent_fraction,
        current_line=current_line,
        micro_line=micro_line,
        micro_fraction=micro_fraction,
        micro_current_line=micro_current,
        micro_detail_line=micro_detail,
        requests_line=requests_line,
        requests_detail_line=requests_detail,
        elapsed_line=f"Tempo trascorso: {event.elapsed_formatted}",
        message=event.status_message,
        warnings=tuple(warnings),
    )
