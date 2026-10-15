"""
ui/presentation.py
------------------
Componenti di visualizzazione e rendering per l'interfaccia Streamlit.

Principi:
- PRIVACY FORENSE ASSOLUTA:
  - Nessun logging di testi chat o query in console o su disco.
  - Nessun invio dati all'esterno, nessuna telemetria.
  - Nessun uso di `unsafe_allow_html=True`.
  - Nessun codice JavaScript custom.
- RIGOROSA DISTINZIONE VISIVA:
  - Evidenza originale forense separata in modo inequivocabile da descrizioni e decisioni AI.
- GESTIONE ECCEZIONI SICURA:
  - Gli errori non provocano crash dell'app e non espongono dati sensibili.
"""
from __future__ import annotations

import inspect
import streamlit as st

from core.config import MAX_UPLOAD_SIZE_MB
from ai.backend import (
    AiBackendProtocolError,
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiBackendUnavailableError,
    AiInvalidEvidenceCitationError,
    AiModelAmbiguousError,
    AiModelLoadError,
    AiModelMismatchError,
    AiModelNotInstalledError,
    AiModelNotSpecifiedError,
    AiStructuredOutputError,
    LmStudioUnavailableError,
)
from ai.lmstudio import (
    DEFAULT_OPERATIONAL_MAX_TOKENS,
    DEFAULT_OPERATIONAL_MODEL,
    DEFAULT_OPERATIONAL_TIMEOUT_SECONDS,
    SUPPORTED_OPERATIONAL_MODELS,
    get_model_display_name,
)
from pathlib import Path

from ai.models import TopicDecision
from analysis.models import ConsistencyStatus, OperationalProfile, resolve_operational_profile
from analysis.checkpoint import AnalysisCheckpointStore
from search.models import MatchMode
from search.service import SearchService
from ui import state
from ui.application import (
    TopicProvenanceMismatchError,
    build_topic_query,
    check_lm_studio_status,
    execute_discovery_verification,
    execute_local_media_processing,
    execute_manual_topic_detection,
    execute_topic_discovery,
    execute_evidence_search,
    execute_real_ingestion,
    filter_evidence_sections,
    get_evidence_filter_options,
    get_system_status_info,
    search_topic_detections,
    search_topic_discoveries,
    summarize_document,
    validate_discovery_provenance,
)
from ui.ingestion import (
    IngestionError,
    InvalidUploadedFileError,
    PipelineStageError,
    UnsupportedSourceFormatError,
)
from ui.models import (
    DatasetMode,
    EvidenceFilterCriteria,
    IngestionRequest,
    IngestionStatus,
    SourceFormat,
    TopicFilterDecision,
)
from typing import Any
from ai.chunking import profile_document_for_analysis
from analysis.progress import AnalysisProgressEvent


def create_streamlit_progress_callback(container: Any, title: str = "ANALISI IN CORSO") -> Any:
    """
    Crea un callback thread-safe compatibile con Streamlit per aggiornare
    in tempo reale il pannello di avanzamento e i contatori di inferenza.
    Garantisce piena visibilità della separazione tra ciclo di vita chunk e chiamate HTTP.
    """
    def _callback(event: AnalysisProgressEvent) -> None:
        try:
            with container.container():
                st.markdown(f"#### ⏳ {title}")
                st.markdown(f"**Fase:** {event.phase}")
                progress_val = max(0.0, min(1.0, event.percentage / 100.0))
                st.progress(progress_val, text=f"Progresso fase corrente: {event.percentage:.1f}%")

                c1, c2, c3 = st.columns(3)
                with c1:
                    st.metric("Blocchi completati", f"{event.completed_chunks} / {event.total_chunks}")
                    st.metric("Blocco attuale", f"{event.current_chunk_index} di {event.total_chunks}")
                with c2:
                    st.metric("Blocchi rimanenti", f"{event.remaining_chunks}")
                    sent_txt = f"{event.requests_sent}"
                    if event.requests_in_flight > 0:
                        sent_txt += f" ({event.requests_in_flight} in volo)"
                    st.metric("Richieste inviate", sent_txt)
                with c3:
                    st.metric("Richieste riuscite", f"{event.requests_successful}")
                    st.metric("Retry / Fallite", f"{event.retries_count} / {event.requests_failed}")

                diag_parts = []
                if event.http_errors_count > 0:
                    diag_parts.append(f"HTTP Error: {event.http_errors_count}")
                if event.timeouts_count > 0:
                    diag_parts.append(f"Timeout: {event.timeouts_count}")
                if event.protocol_errors_count > 0:
                    diag_parts.append(f"Protocollo/JSON: {event.protocol_errors_count}")
                if event.split_count > 0:
                    diag_parts.append(f"Suddivisioni dinamiche: {event.split_count}")

                diag_str = f" | Contabilità: {', '.join(diag_parts)}" if diag_parts else ""
                st.caption(
                    f"⏱️ **Tempo trascorso:** `{event.elapsed_formatted}` | "
                    f"**Stato:** {event.status_message}{diag_str}"
                )
                if "recuperati" in event.phase.casefold():
                    st.info(f"💾 **Stato Checkpoint:** {event.phase}")
                elif "consolidamento" in event.phase.casefold():
                    st.info(f"🔄 **Fase:** {event.phase}")
        except Exception:
            pass

    return _callback


def render_operational_profile_selector(doc: Any) -> OperationalProfile:
    """
    Rende disponibili nell'app profili operativi espliciti:
    - Prudente (2500 token).
    - Bilanciato (4200 token).
    - Prestazioni elevate (5200 token).
    Verifica la sicurezza rispetto al context budget (context length 8192) e consente
    il ritorno immediato alla configurazione prudente. Mostra i limiti effettivi utilizzati.
    Offre controlli di gestione del checkpointing persistente.
    """
    current_prof = state.get_operational_profile()
    profile_options = [
        ("BALANCED", "Bilanciato (4200 token - Consigliato)"),
        ("PRUDENT", "Prudente (2500 token - Massima sicurezza)"),
        ("PERFORMANCE", "Prestazioni elevate (5200 token - Sperimentale)"),
    ]
    profile_keys = [p[0] for p in profile_options]
    current_key = current_prof.value if isinstance(current_prof, OperationalProfile) else str(current_prof)
    if current_key not in profile_keys:
        current_key = "BALANCED"
    default_idx = profile_keys.index(current_key)

    with st.expander("⚙️ Profilo Operativo & Gestione Checkpoint", expanded=False):
        c_prof, c_chk = st.columns([2, 1])
        with c_prof:
            choice = st.radio(
                "Profilo Operativo di Analisi:",
                options=profile_keys,
                index=default_idx,
                format_func=lambda k: dict(profile_options)[k],
                key="operational_profile_radio",
                help="Bilanciato riduce il numero di chunk del 40% rispetto al profilo prudente, preservando la sicurezza.",
            )
            requested_enum = OperationalProfile(choice)
            try:
                effective_enum, t_lim, c_lim = resolve_operational_profile(requested_enum, context_length=8192)
                if effective_enum != requested_enum:
                    st.warning(
                        f"⚠️ Il profilo '{requested_enum.value}' non è sicuro per il context headroom attuale. "
                        f"Ripiegato automaticamente sul profilo sicuro '{effective_enum.value}'."
                    )
            except InsufficientContextHeadroomError as err:
                st.error(f"⚠️ Spazio di contesto insufficiente per il profilo: {err}")
                effective_enum, t_lim, c_lim = OperationalProfile.PRUDENT, 2500, 12000

            state.set_operational_profile(effective_enum)

            st.caption(
                f"📊 **Limiti effettivi:** Budget token chunk: `{t_lim}` | "
                f"Budget caratteri: `{c_lim:,}` | Margine di sicurezza: `1000` token"
            )

        with c_chk:
            st.markdown("**💾 Checkpoint Permanente**")
            st.caption("Salvataggio progressivo e ripresa senza ripetizione dei chunk già validati.")
            chk_store = state.get_checkpoint_store()
            if chk_store is None:
                chk_store = AnalysisCheckpointStore(checkpoint_dir=Path(".checkpoints"))
                state.set_checkpoint_store(chk_store)

            if doc is not None and getattr(doc, "document_id", None):
                doc_id = doc.document_id
                if st.button("🗑️ Cancella Checkpoint Documento", key=f"btn_clear_chk_{doc_id}"):
                    try:
                        cleared = chk_store.clear_for_document(doc)
                        if cleared > 0:
                            st.success(f"Cancellati con successo {cleared} checkpoint per '{doc_id}'.")
                        else:
                            st.info(f"Nessun checkpoint presente per '{doc_id}'.")
                    except Exception as exc:
                        st.error(f"Errore durante la cancellazione dei checkpoint: {exc}")

    return state.get_operational_profile()


def render_pre_analysis_profile(document: Any, service: Any = None, max_topics: int | None = None) -> None:
    """
    Visualizza il profilo quantitativo preventivo del documento e la stima dei chunk prima dell'analisi.
    Utilizza la configurazione effettiva del servizio locale per garantire perfetta coerenza operativa.
    Riflette dinamicamente il numero massimo di topic impostato (es. 3, 10, 25 topic).
    """
    if document is None:
        return
    if service is None:
        try:
            from ui.application import get_operational_analysis_service
            service = get_operational_analysis_service()
        except Exception:
            service = None

    eff_max_topics = max_topics or (st.session_state.get("disc_max_topics", 10) if hasattr(st, "session_state") else 10)

    prof = profile_document_for_analysis(document, service=service, analysis_type="detection")
    prof_disc = profile_document_for_analysis(document, service=service, analysis_type="discovery", max_topics=int(eff_max_topics))

    with st.expander("📊 Profilo Preventivo Dataset & Stima Inferenze", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Messaggi Totali", f"{prof.total_messages:,}")
        c2.metric("Evidenze Testuali", f"{prof.total_evidence_sections:,}")
        c3.metric("Caratteri / Token Stimati", f"{prof.total_text_characters:,} / ~{prof.estimated_text_tokens:,}")
        if prof.estimated_chunks == prof_disc.estimated_chunks:
            c4.metric("Blocchi Previsti", f"{prof.estimated_chunks:,}")
        else:
            c4.metric("Blocchi Previsti", f"{prof.estimated_chunks} (Det) / {prof_disc.estimated_chunks} (Disc {eff_max_topics}t)")
        if prof.has_multimodal_evidence:
            st.caption("ℹ️ Il dataset include evidenze multimodali (trascrizioni audio o analisi immagini).")


def render_backend_error_details(exc: Exception) -> None:
    """
    Mostra all'utente una spiegazione chiara e priva di dati sensibili per errori AI backend.
    """
    if isinstance(exc, AiBackendRequestError):
        st.error(f"⚠️ {exc.safe_reason}")
        with st.expander("🔍 Diagnostica Tecnica Sicura (Senza Dati Sensibili)", expanded=False):
            st.write(f"- **Categoria Errore:** `{exc.error_category or 'NON_CLASSIFICATO'}`")
            if exc.status_code:
                st.write(f"- **Codice HTTP:** `{exc.status_code}`")
            if exc.model_id:
                st.write(f"- **Modello:** `{exc.model_id}`")
            if exc.request_id:
                st.write(f"- **Request ID:** `{exc.request_id}`")
            if exc.prompt_tokens_estimate is not None:
                st.write(f"- **Token Input Stimati:** `{exc.prompt_tokens_estimate}`")
            if exc.max_tokens_requested is not None:
                st.write(f"- **Token Output Richiesti:** `{exc.max_tokens_requested}`")
            if exc.technical_details:
                st.caption(f"Dettaglio backend: `{exc.technical_details}`")
    else:
        st.error(f"Errore di comunicazione con LM Studio: {type(exc).__name__}: {exc}")



def render_overview() -> None:
    """Schermata Panoramica: riepilogo aggregato e metriche forensi."""
    st.header("📊 Panoramica Documento")

    if not state.is_dataset_loaded():
        st.info("Nessun dataset attualmente caricato in memoria.")
        st.markdown(
            "Per esplorare le funzionalità del sistema locale, è possibile caricare il "
            "**dataset dimostrativo sintetico** oppure importare un file reale nella sezione Importazione."
        )
        col1, _ = st.columns([1, 2])
        with col1:
            if st.button("🚀 Carica Dataset Dimostrativo", use_container_width=True):
                state.load_demo_dataset()
                st.rerun()
        return

    is_real_file = state.get_dataset_mode() == DatasetMode.FILE
    ing_summary = state.get_ingestion_summary()
    doc = state.get_conversation_document()
    detections = state.get_detection_results()
    discoveries = state.get_discovery_results()

    # Caso wa.db standalone (auxiliary-only, nessun documento conversazione)
    if is_real_file and doc is None and ing_summary is not None:
        st.info("ℹ️ wa.db contiene dati contatto/identità e non costituisce da solo una conversazione analizzabile.")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Modalità Dataset", "REAL FILE")
        with c2:
            st.metric("Formato Sorgente", ing_summary.source_format.value)
        with c3:
            st.metric("Record Ausiliari", ing_summary.auxiliary_record_count)
        with c4:
            st.metric("Validation Issues", ing_summary.validation_issue_count)

        st.markdown(f"- **Filename**: `{ing_summary.original_filename}`")
        st.markdown(f"- **Dimensione file**: `{ing_summary.file_size_bytes} byte`")
        st.markdown(f"- **SHA-256**: `{ing_summary.sha256}`")
        st.divider()
        if st.button("🔄 Reimposta Sessione / Rimuovi Dataset"):
            state.reset_dataset()
            st.rerun()
        return

    if doc is None:
        st.warning("Stato inconsistente: dataset marcato come caricato ma documento non presente.")
        return

    summary = summarize_document(doc, detections, discoveries)
    unresolved_media = sum(1 for b in doc.bundles if b.message.media_reference)

    # Metriche principali
    c0, c1, c2, c3, c4 = st.columns(5)
    with c0:
        st.metric("Modalità", "REAL FILE" if is_real_file else "DEMO")
    with c1:
        st.metric("Messaggi (Bundle)", summary.bundle_count)
    with c2:
        st.metric("Sezioni Evidenza", summary.section_count)
    with c3:
        st.metric("Topic Detections", summary.topic_detection_count)
    with c4:
        st.metric("Topic Discovery", summary.topic_discovery_count)

    st.divider()

    # Dettagli identificativi
    st.subheader("Identificativi e Provenance")
    col_id1, col_id2, col_id3, col_id4 = st.columns(4)
    with col_id1:
        st.text_input("Document ID", value=summary.document_id, disabled=True)
    with col_id2:
        # Label pseudonimizzata della conversazione (nessun JID, numero telefonico o nome in chiaro)
        active_label = "Conversazione Attiva"
        if ing_summary:
            matching_c = next((c for c in ing_summary.conversations if c.document_id == doc.document_id), None)
            if matching_c:
                active_label = matching_c.display_label
        elif doc.chat_id:
            active_label = "Conversazione (Demo)"
        st.text_input("Conversazione", value=active_label, disabled=True)
    with col_id3:
        st.text_input("Sorgente Forense", value=doc.source_name or "N/D", disabled=True)
    with col_id4:
        if ing_summary:
            st.text_input("SHA-256", value=f"{ing_summary.sha256[:16]}...", help=ing_summary.sha256, disabled=True)
        else:
            st.text_input("SHA-256", value="N/D (Sintetico)", disabled=True)

    with st.expander("Provenance Tecnica (Metadati Interni)"):
        st.write(f"- **Document ID**: `{doc.document_id}`")
        st.write(f"- **Sorgente**: `{doc.source_name or 'N/D'}`")
        st.write(f"- **Chat ID Tecnico**: `{doc.chat_id or 'UNRESOLVED'}`")
        if doc.metadata:
            for k, v in doc.metadata.items():
                st.write(f"- **{k}**: `{v}`")

    if ing_summary:
        st.markdown(
            f"**File Sorgente**: `{ing_summary.original_filename}` ({ing_summary.file_size_bytes} byte) | "
            f"**Conversazioni nel dataset**: `{ing_summary.conversation_count}` | "
            f"**Media reference non risolti**: `{unresolved_media}`"
        )

    # Distribuzione sorgenti e lingue
    col_src, col_lang = st.columns(2)
    with col_src:
        st.subheader("Distribuzione Sezioni per Tipologia")
        for st_type, count in summary.counts_by_source_type.items():
            st.write(f"- **{st_type}**: {count} sezioni")

    with col_lang:
        st.subheader("Lingue Rilevate nel Documento")
        if summary.languages:
            st.write(", ".join(f"`{lang}`" for lang in summary.languages))
        else:
            st.write("Nessuna informazione linguistica esplicita.")

    # Sezione Elaborazione Media Locale (On-Demand)
    has_media_references = any(bool(b.message.media_reference) for b in doc.bundles)
    if has_media_references:
        st.divider()
        st.subheader("🎬 Elaborazione Multimediale Locale (On-Demand)")
        st.markdown(
            "Questo documento contiene riferimenti ad allegati multimediali (audio/immagini). "
            "L'elaborazione è rigorosamente locale e opzionale: nessun motore AI/STT/OCR viene eseguito automaticamente all'ingestion."
        )

        col_stt, col_ocr, col_vis = st.columns(3)
        from multimodal.transcriber import FasterWhisperTranscriber
        from multimodal.ocr import TesseractImageTextExtractor
        stt_avail = FasterWhisperTranscriber.is_available()
        ocr_avail = TesseractImageTextExtractor.is_available()

        with col_stt:
            st.metric("Speech-to-Text (Audio)", "Faster-Whisper" if stt_avail else "Non installato")
            if not stt_avail:
                st.caption("faster-whisper non installato o non rilevato nel runtime.")
        with col_ocr:
            st.metric("OCR (Immagini)", "Tesseract OCR" if ocr_avail else "Non installato")
            if not ocr_avail:
                st.caption("tesseract non rilevato nel PATH di sistema.")
        with col_vis:
            st.metric("Vision (Scena)", "Non configurato")
            st.caption("Vision locale reale non configurata (disabilitata per dati reali).")

        col_btn, _ = st.columns([1, 2])
        with col_btn:
            btn_label = "🎙️ Elabora media localmente"
            curr_status = doc.metadata.get("multimodal_status")
            if curr_status in ("PARTIAL", "FAILED"):
                btn_label = "🔁 Riprova elaborazione media"
            if st.button(btn_label, use_container_width=True):
                with st.spinner("Elaborazione multimediale locale in corso..."):
                    try:
                        registry = state.get_media_asset_registry(doc.document_id)
                        enriched_doc, report = execute_local_media_processing(
                            document=doc,
                            registry=registry,
                        )
                        state.update_conversation_document(enriched_doc)
                        res_status = enriched_doc.metadata.get("multimodal_status")
                        if res_status == "COMPLETE":
                            st.success(
                                f"Elaborazione completata con successo! "
                                f"Sezioni di evidenza derivate: {report['derived_sections']} "
                                f"(Totale sezioni: {len(enriched_doc.all_evidence_sections)})."
                            )
                        elif res_status == "PARTIAL":
                            st.warning(
                                f"Elaborazione parziale: {report['derived_sections']} sezioni derivate. "
                                f"Alcuni media non sono stati elaborati."
                            )
                        else:
                            st.error(
                                "Elaborazione non riuscita: nessun media elaborato con successo (backend assenti o falliti)."
                            )
                        st.rerun()
                    except Exception as err:
                        st.error(f"Errore durante l'elaborazione dei media: {err}")

    st.divider()
    if st.button("🔄 Reimposta Sessione / Scarica Dataset"):
        state.reset_dataset()
        st.rerun()


def render_import() -> None:
    """Schermata Importazione: gestione demo e ingestion reale di file forensi."""
    st.header("📥 Importazione Dati Forensi")

    st.subheader("1. Modalità Dimostrativa (Dati Sintetici)")
    st.markdown(
        "Genera un set completo di evidenze sintetiche multilingua (IT, EN, ES) "
        "con sezioni testuali, trascrizioni vocali STT, testo OCR e descrizioni visive "
        "completamente conformi ai modelli di dominio e privi di dati reali."
    )

    if state.is_dataset_loaded() and state.get_dataset_mode() == DatasetMode.DEMO:
        st.success("✅ Dataset dimostrativo attualmente caricato in memoria.")
        if st.button("Ricarica Dataset Dimostrativo"):
            state.load_demo_dataset()
            st.rerun()
    else:
        if st.button("🚀 Carica Dataset Dimostrativo", type="primary"):
            state.load_demo_dataset()
            st.rerun()

    st.divider()

    st.subheader("2. Modalità File Reale (Ingestion Forense Locale)")
    st.markdown(
        "Ingestion locale offline di archivi e database WhatsApp e Cellebrite. "
        "I file vengono elaborati in una sandbox temporanea volatile con calcolo SHA-256 "
        "e distruzione immediata della cartella al termine dell'operazione."
    )

    source_format_str = st.selectbox(
        "Tipo di sorgente forense da importare",
        options=[f.value for f in SourceFormat],
    )
    source_format = SourceFormat(source_format_str)

    if source_format == SourceFormat.WHATSAPP_EXPORT:
        primary_types = ["txt", "zip"]
        st.caption("Export nativo WhatsApp ottenuto tramite Esporta chat, con o senza media.")
        st.caption("Dimensione massima file: 2 GB")
        st.caption("Gli archivi molto grandi possono richiedere più tempo per la verifica di integrità e l'importazione.")
    else:
        primary_types = ["db", "sqlite", "csv", "json", "xml"]
        st.caption("Dimensione massima file: 2 GB")

    uploader_kwargs = {}
    if "max_upload_size" in inspect.signature(st.file_uploader).parameters:
        uploader_kwargs["max_upload_size"] = MAX_UPLOAD_SIZE_MB

    uploaded_file = st.file_uploader(
        f"Seleziona file primario ({source_format.value})",
        type=primary_types,
        help="Il file viene elaborato esclusivamente in spazio temporaneo locale isolato con cleanup deterministico.",
        key="primary_file_uploader",
        **uploader_kwargs,
    )

    companion_file = None
    if source_format == SourceFormat.WHATSAPP_MSGSTORE:
        companion_file = st.file_uploader(
            "wa.db contatti — opzionale",
            type=["db", "sqlite"],
            help="Opzionale database contatti/identità WhatsApp per l'arricchimento dei sender.",
            key="companion_wa_uploader",
            **uploader_kwargs,
        )

    import_btn = st.button("📥 Importa e analizza struttura", type="primary")

    if import_btn:
        if uploaded_file is None:
            st.warning("Selezionare un file primario prima di procedere con l'importazione.")
        else:
            companion_name = companion_file.name if companion_file is not None else None

            # Streaming a blocchi: si passa direttamente l'UploadedFile evitando duplicazioni in RAM
            req = IngestionRequest(
                source_format=source_format,
                filename=uploaded_file.name,
                file_bytes=uploaded_file,
                companion_filename=companion_name,
                companion_bytes=companion_file,
            )

            try:
                with st.spinner("Importazione, validazione e normalizzazione in corso..."):
                    result = execute_real_ingestion(req)
                    state.set_real_ingestion_result(result)
                st.success(f"✅ Ingestion completata: {result.summary.raw_record_count} record raw, {result.summary.unified_message_count} messaggi unificati.")
                st.rerun()
            except PipelineStageError as err:
                st.error(f"[{err.stage}] {type(err).__name__}: {err.safe_message}")
            except InvalidUploadedFileError as err:
                st.error(f"{type(err).__name__}: {err.safe_message}")
            except UnsupportedSourceFormatError as err:
                st.error(f"{type(err).__name__}: {err.safe_message}")
            except IngestionError as err:
                st.error(f"IngestionError: {err.safe_message}")
            except Exception as exc:
                st.error(f"Errore imprevisto durante l'elaborazione del file: {type(exc).__name__}")

    # Visualizzazione Summary Ingestion e Selezione Conversazione se presente
    real_res = state.get_real_ingestion_result()
    if real_res is not None and state.get_dataset_mode() == DatasetMode.FILE:
        st.divider()
        st.subheader("📋 Riepilogo Ultima Ingestion Reale")
        sm = real_res.summary

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("Formato", sm.source_format.value)
        with m2:
            st.metric("Dimensione File", f"{sm.file_size_bytes} B")
        with m3:
            st.metric("Record Raw", sm.raw_record_count)
        with m4:
            st.metric("Record Normalizzati", sm.normalized_record_count)

        m5, m6, m7, m8 = st.columns(4)
        with m5:
            st.metric("Messaggi Unificati", sm.unified_message_count)
        with m6:
            st.metric("Conversazioni", sm.conversation_count)
        with m7:
            st.metric("Record Ausiliari", sm.auxiliary_record_count)
        with m8:
            st.metric("Validation Issues", sm.validation_issue_count)

        st.markdown(f"- **Filename**: `{sm.original_filename}`")
        st.markdown(f"- **SHA-256**: `{sm.sha256}`")

        if sm.warnings:
            with st.expander(f"⚠️ Avvisi Non Bloccanti ({len(sm.warnings)})"):
                for w in sm.warnings:
                    st.write(f"- {w}")

        if real_res.status == IngestionStatus.AUXILIARY_ONLY:
            st.info("ℹ️ wa.db contiene dati contatto/identità e non costituisce da solo una conversazione analizzabile.")
        elif real_res.documents:
            st.subheader("💬 Selezione Conversazione")
            conv_options = [c.document_id for c in sm.conversations]
            conv_labels = {
                c.document_id: c.display_label
                for c in sm.conversations
            }

            curr_sel_id = state.get_selected_document_id()
            default_idx = 0
            if curr_sel_id and curr_sel_id in conv_options:
                default_idx = conv_options.index(curr_sel_id)

            chosen_id = st.selectbox(
                "Seleziona la conversazione attiva per l'analisi e la ricerca",
                options=conv_options,
                index=default_idx,
                format_func=lambda doc_id: conv_labels.get(doc_id, doc_id),
                key="conv_selectbox",
            )

            if st.button("📂 Apri conversazione", key="open_conv_btn"):
                if chosen_id:
                    state.select_conversation(chosen_id)
                    st.success(f"Conversazione attivata: {conv_labels.get(chosen_id, chosen_id)}")
                    st.rerun()


def render_explore() -> None:
    """Schermata Esplora Conversazione: scorrimento nell'ordine naturale con filtri e provenance."""
    st.header("🔍 Esplora Conversazione")

    if not state.is_dataset_loaded():
        st.info("Nessun dataset caricato. Caricare il dataset dimostrativo nella sezione Importazione.")
        return

    doc = state.get_conversation_document()
    if doc is None:
        if state.get_dataset_mode() == DatasetMode.FILE:
            st.info("Nessuna conversazione selezionabile per il dataset corrente (es. archivio solo ausiliario o contatti).")
        return

    has_media_references = any(bool(b.message.media_reference) for b in doc.bundles)
    if has_media_references:
        m_status = doc.metadata.get("multimodal_status", "NOT_PROCESSED")
        if not doc.metadata.get("multimodal_processed", False):
            if m_status == "PARTIAL":
                st.warning("⚠️ Elaborazione multimediale parziale: alcuni allegati non sono stati elaborati. È possibile riprovare.")
                btn_explore_label = "🔁 Riprova elaborazione media parziale"
            elif m_status == "FAILED":
                st.error("❌ Elaborazione multimediale precedente non riuscita (motori non disponibili o errore). È possibile riprovare.")
                btn_explore_label = "🔁 Riprova elaborazione media"
            else:
                st.info("ℹ️ Questa conversazione contiene allegati multimediali. L'estrazione STT/OCR è disponibile su richiesta.")
                btn_explore_label = "🎙️ Elabora media localmente"

            if st.button(btn_explore_label, key="explore_process_media_btn"):
                with st.spinner("Elaborazione multimediale locale in corso..."):
                    try:
                        registry = state.get_media_asset_registry(doc.document_id)
                        enriched_doc, report = execute_local_media_processing(
                            document=doc,
                            registry=registry,
                        )
                        state.update_conversation_document(enriched_doc)
                        res_status = enriched_doc.metadata.get("multimodal_status")
                        if res_status == "COMPLETE":
                            st.success(
                                f"Elaborazione completata con successo! Nuove sezioni derivate: {report['derived_sections']}."
                            )
                        elif res_status == "PARTIAL":
                            st.warning(
                                f"Elaborazione parziale ({report['derived_sections']} sezioni derivate). Alcuni media non hanno avuto successo."
                            )
                        else:
                            st.error(
                                "Elaborazione non riuscita: nessun media estratto con successo (motori non disponibili)."
                            )
                        st.rerun()
                    except Exception as err:
                        st.error(f"Errore elaborazione media: {err}")
        else:
            st.success("✅ Tutti gli allegati multimediali supportati sono stati elaborati con successo.")

    options = get_evidence_filter_options(doc)

    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        sel_source = st.selectbox("Filtra per Tipo Evidenza", options=options["source_types"])
    with col_f2:
        sel_lang = st.selectbox("Filtra per Lingua", options=options["languages"])
    with col_f3:
        sel_source_name = st.selectbox("Filtra per Sorgente", options=options["source_names"])

    criteria = EvidenceFilterCriteria(
        source_type=sel_source,
        language=sel_lang,
        source_name=sel_source_name,
    )

    filtered_sections = filter_evidence_sections(doc, criteria)
    st.write(f"Visualizzate **{len(filtered_sections)}** sezioni su un totale di **{len(doc.all_evidence_sections)}** (ordine naturale di acquisizione).")

    for idx, sec in enumerate(filtered_sections, start=1):
        st_label = sec.source_type.value if hasattr(sec.source_type, "value") else str(sec.source_type)
        with st.container(border=True):
            header_col, meta_col = st.columns([3, 1])
            with header_col:
                st.markdown(f"**#{idx} | Messaggio:** `{sec.message_id}` | **Tipo:** `{st_label}`")
            with meta_col:
                st.caption(f"Lingua: `{sec.language or 'n/a'}` | Sorgente: `{sec.source_name}`")

            st.text_area(
                "Contenuto Evidenza",
                value=sec.text,
                height=70,
                key=f"sec_text_{sec.evidence_id}_{idx}",
                disabled=True,
            )

            with st.expander("Dettagli Provenance Forense"):
                st.write(f"- **Evidence ID**: `{sec.evidence_id}`")
                st.write(f"- **Message ID**: `{sec.message_id}`")
                st.write(f"- **Source Record ID**: `{sec.source_record_id}`")
                st.write(f"- **Source Name**: `{sec.source_name}`")
                if sec.ordinal is not None:
                    st.write(f"- **Ordinal**: `{sec.ordinal}`")


def render_search() -> None:
    """Schermata Ricerca: esecuzione deterministica tramite SearchService."""
    st.header("🔎 Ricerca Forense Deterministica")

    if not state.is_dataset_loaded():
        st.info("Nessun dataset caricato. Caricare un dataset per effettuare ricerche.")
        return

    service = state.get_search_service()
    doc = state.get_conversation_document()
    if service is None or doc is None:
        if state.get_dataset_mode() == DatasetMode.FILE:
            st.info("Nessuna conversazione attiva per la ricerca (es. archivio solo ausiliario o contatti).")
        else:
            st.warning("Servizio di ricerca non configurato.")
        return

    options = get_evidence_filter_options(doc)

    col_q, col_m = st.columns([3, 1])
    with col_q:
        query_text = st.text_input("Termine o frase da ricercare", placeholder="Es. accordo, Milano, fattura, contract")
    with col_m:
        match_mode_str = st.selectbox(
            "Modalità di Matching",
            options=[m.value for m in MatchMode],
            index=0,
            help="PHRASE: sottostringa esatta. ALL_TERMS: tutti i termini interi. ANY_TERM: almeno un termine intero. EXACT: uguaglianza integrale.",
        )

    col_s1, col_s2, col_s3, col_s4 = st.columns(4)
    with col_s1:
        sel_source = st.selectbox("Tipo Evidenza", options=options["source_types"], key="search_st")
    with col_s2:
        sel_lang = st.selectbox("Lingua", options=options["languages"], key="search_lang")
    with col_s3:
        sel_source_name = st.selectbox("Sorgente", options=options["source_names"], key="search_src")
    with col_s4:
        limit_val = st.number_input("Limite Risultati (0 = illimitato)", min_value=0, max_value=500, value=50, step=10)

    execute_btn = st.button("Cerca Evidenze", type="primary")

    if execute_btn or query_text:
        if not query_text or not query_text.strip():
            st.info("Inserire un testo di ricerca per avviare la scansione.")
            return

        limit_arg = int(limit_val) if limit_val > 0 else None
        try:
            result = execute_evidence_search(
                search_service=service,
                query_text=query_text,
                match_mode=MatchMode(match_mode_str),
                source_type=sel_source,
                language=sel_lang,
                source_name=sel_source_name,
                limit=limit_arg,
            )
        except Exception as exc:
            st.error(f"Errore durante l'esecuzione della ricerca: {type(exc).__name__}")
            return

        st.subheader("Risultati Ricerca")
        st.write(
            f"Trovate **{result.total_hits}** occorrenze totali "
            f"(restituiti: **{len(result.hits)}**"
            f"{', troncati dal limite' if result.metadata.get('truncated') else ''})."
        )

        if not result.hits:
            st.warning("Nessuna evidenza soddisfa i criteri di ricerca specificati.")
            return

        for idx, hit in enumerate(result.hits, start=1):
            st_label = hit.source_type.value if hasattr(hit.source_type, "value") else str(hit.source_type)
            with st.container(border=True):
                h1, h2 = st.columns([3, 1])
                with h1:
                    st.markdown(f"**Hit #{idx} | Messaggio:** `{hit.message_id}` | **Tipo:** `{st_label}`")
                with h2:
                    st.caption(f"Lingua: `{hit.language or 'n/a'}`")

                st.text_area(
                    "Testo Evidenza",
                    value=hit.original_text,
                    height=70,
                    key=f"hit_text_{hit.evidence_id}_{idx}",
                    disabled=True,
                )

                with st.expander("Provenance & Termini"):
                    st.write(f"- **Evidence ID**: `{hit.evidence_id}`")
                    st.write(f"- **Source Name**: `{hit.source_name}`")
                    st.write(f"- **Source Record ID**: `{hit.source_record_id}`")
                    if hit.matched_terms:
                        st.write(f"- **Termini Riconosciuti**: `{', '.join(hit.matched_terms)}`")


def render_topics() -> None:
    """Schermata Analisi Topic: visualizzazione deterministica di Detection e Discovery."""
    st.header("🏷️ Analisi Tematica (Local AI Results)")

    if not state.is_dataset_loaded():
        st.info("Nessun dataset caricato. Caricare un dataset per visualizzare l'analisi dei topic.")
        return

    doc = state.get_conversation_document()
    if doc is None:
        st.info("Nessuna conversazione attiva per l'analisi.")
        return

    service = state.get_search_service()
    if service is None:
        service = SearchService(
            document=doc,
            detection_results=state.get_detection_results(),
            discovery_results=state.get_discovery_results(),
        )

    selected_profile = render_operational_profile_selector(doc)
    render_pre_analysis_profile(doc)

    tab_det, tab_disc = st.tabs(["Topic Detection (Verifica Mirata)", "Topic Discovery (Argomenti Emersi)"])

    with tab_det:
        # ==================================================
        # AREA 1: Verifica un argomento
        # ==================================================
        st.subheader("🎯 Area 1: Verifica un argomento")
        st.caption(
            "Verifica la presenza di un argomento a scelta nella conversazione corrente "
            "eseguendo un'analisi tramite il motore locale TopicDetectionAnalyzer (LM Studio)."
        )

        col_in1, col_in2 = st.columns([2, 1])
        with col_in1:
            topic_input = st.text_input(
                "Argomento:",
                value=state.get_custom_topic_label(),
                placeholder="Es. Organizzazione di incontri riservati",
                key="manual_topic_input",
            )
        with col_in2:
            current_model_val = state.get_lm_studio_model() or DEFAULT_OPERATIONAL_MODEL
            model_input = st.text_input(
                "Modello LM Studio locale:",
                value=current_model_val,
                placeholder="qwen2.5-7b-instruct",
                help="Modello predefinito: qwen2.5-7b-instruct. Supportati se installati: meta-llama-3.1-8b-instruct, deepseek-r1-distill-qwen-7b.",
                key="manual_model_input",
            )

        desc_input = st.text_area(
            "Descrizione opzionale:",
            value=state.get_custom_topic_description(),
            placeholder="Descrizione o dettagli specifici del topic da ricercare...",
            height=70,
            key="manual_desc_input",
        )

        run_detection_btn = st.button("🚀 Esegui Topic Detection", type="primary", key="btn_run_manual_detection")

        if run_detection_btn:
            if not topic_input.strip():
                st.warning("Inserire un argomento prima di avviare la Topic Detection.")
            else:
                try:
                    q = build_topic_query(
                        label=topic_input,
                        description=desc_input if desc_input.strip() else None,
                    )
                except ValueError as v_err:
                    st.error(f"Parametri argomento non validi: {v_err}")
                    q = None

                if q is not None:
                    client = state.get_lm_studio_client()
                    raw_model_choice = model_input.strip() if model_input.strip() else (state.get_lm_studio_model() or DEFAULT_OPERATIONAL_MODEL)
                    progress_placeholder = st.empty()
                    prog_cb = create_streamlit_progress_callback(progress_placeholder, title="TOPIC DETECTION IN CORSO")
                    try:
                        progress_placeholder.info("Preparazione ed esecuzione analisi locale...")
                        with st.spinner("Analisi locale in corso..."):
                            det_res = execute_manual_topic_detection(
                                document=doc,
                                topic=q,
                                client=client,
                                model_id=raw_model_choice,
                                timeout_seconds=DEFAULT_OPERATIONAL_TIMEOUT_SECONDS,
                                max_tokens=DEFAULT_OPERATIONAL_MAX_TOKENS,
                                progress_callback=prog_cb,
                                profile=selected_profile,
                            )
                        progress_placeholder.empty()
                        state.add_detection_result(det_res)
                        state.set_last_manual_detection(det_res)
                        state.set_custom_topic_label(topic_input)
                        state.set_custom_topic_description(desc_input)
                        state.set_lm_studio_model(det_res.metadata.get("model", raw_model_choice))
                        d_meta_res = getattr(det_res, "metadata", {}) or {}
                        chunk_c_res = d_meta_res.get("chunk_count", 1)
                        comp_c_res = d_meta_res.get("completed_chunk_count", 1)
                        fail_c_res = d_meta_res.get("failed_chunk_count", 0)
                        det_st = d_meta_res.get("status", "SUCCESS")
                        if det_st == "PARTIAL" or fail_c_res > 0:
                            st.warning(f"⚠️ Topic Detection parziale (PARTIAL): {comp_c_res}/{chunk_c_res} blocchi completati (falliti: {fail_c_res}). Esito parziale: {det_res.decision.value}")
                        else:
                            st.success(f"Topic Detection completata! Esito: {det_res.decision.value}")
                        if chunk_c_res > 1 and det_st != "PARTIAL":
                            st.info(f"Diagnostica blocchi: {comp_c_res}/{chunk_c_res} completati con successo (falliti: {fail_c_res}).")
                    except LmStudioUnavailableError:
                        progress_placeholder.empty()
                        st.error(
                            "LM Studio non raggiungibile su http://127.0.0.1:1234. "
                            "Verificare che il server locale di LM Studio sia attivo."
                        )
                    except (
                        AiModelNotInstalledError,
                        AiModelAmbiguousError,
                        AiModelLoadError,
                        AiBackendTimeoutError,
                        AiModelMismatchError,
                        AiInvalidEvidenceCitationError,
                        AiStructuredOutputError,
                    ) as ni_err:
                        progress_placeholder.empty()
                        st.error(str(ni_err))
                    except (AiBackendRequestError, AiBackendProtocolError) as b_err:
                        progress_placeholder.empty()
                        render_backend_error_details(b_err)
                    except Exception as exc:
                        progress_placeholder.empty()
                        st.error(f"Errore durante l'esecuzione della Topic Detection: {type(exc).__name__}: {exc}")

        # Visualizzazione del risultato per la verifica
        last_det = state.get_last_manual_detection()
        if last_det is not None:
            with st.container(border=True):
                dec_val = last_det.decision.value
                if dec_val == "PRESENT":
                    badge = "🟢 PRESENTE"
                elif dec_val == "ABSENT":
                    badge = "⚪ ASSENTE"
                else:
                    badge = "🟡 INCERTO"

                h_col, b_col = st.columns([3, 1])
                with h_col:
                    st.markdown(f"### Risultato Verifica: {last_det.topic.label}")
                with b_col:
                    st.markdown(f"**Esito:** {badge}")

                meta = last_det.metadata or {}
                is_label_only = (
                    meta.get("query_mode") == "LABEL_ONLY"
                    or getattr(last_det.topic, "is_label_only", False)
                    or (last_det.topic.description.strip().casefold() == last_det.topic.label.strip().casefold())
                )
                input_mode_str = "🏷️ Sola Label (titolo argomento)" if is_label_only else "📝 Label + Descrizione specifica"
                origin_flow = meta.get("origin_flow", "MANUAL_DETECTION")
                origin_flow_str = "🔬 Verifica automatica (da Topic Discovery)" if origin_flow == "DISCOVERY_VERIFICATION" else "🎯 Topic Detection manuale"

                if not is_label_only and last_det.topic.description:
                    st.markdown(f"**Descrizione Topic:** {last_det.topic.description}")

                st.info(f"**Motivazione AI (Rationale):** {last_det.rationale}")

                mod_name = meta.get("model", "n/a")
                lat = meta.get("latency_seconds")
                lat_str = f"{lat:.2f}s" if isinstance(lat, (int, float)) else "n/a"
                run_id_val = meta.get("run_id", "n/a")
                chunk_c = meta.get("chunk_count", 1)
                comp_c = meta.get("completed_chunk_count", 1)
                fail_c = meta.get("failed_chunk_count", 0)

                st.caption(f"Run ID: `{run_id_val}` | Flusso: `{origin_flow_str}` | Input: `{input_mode_str}` | Provenance: `{last_det.provenance_document_id}`")
                st.caption(f"Modello: `{mod_name}` | Latenza: `{lat_str}` | Chunk analizzati: `{comp_c}/{chunk_c}` (falliti: {fail_c})")

                st.write(
                    f"**Evidenze collegate:** {len(last_det.evidence_ids)} sezioni "
                    f"(IDs: {', '.join(f'`{e}`' for e in last_det.evidence_ids) if last_det.evidence_ids else 'nessuna'})"
                )

                matching_sections = [
                    s for s in doc.all_evidence_sections
                    if s.evidence_id in last_det.evidence_ids
                ]
                if matching_sections:
                    with st.expander(f"Visualizza le {len(matching_sections)} Evidenze Originali Associate", expanded=True):
                        for s_idx, sec in enumerate(matching_sections, start=1):
                            st.markdown(
                                f"**[EVIDENZA ORIGINALE #{s_idx}]** `{sec.source_type.value}` | ID: `{sec.evidence_id}`"
                            )
                            st.text_area(
                                "Testo Originale Forense",
                                value=sec.text,
                                height=60,
                                key=f"area1_ev_{sec.evidence_id}_{s_idx}",
                                disabled=True,
                            )

                # Controllo storico e correlazione inferenze precedenti per questo topic
                doc_analysis = state.get_document_analysis_state(doc.document_id)
                prior_runs = [
                    r for r in doc_analysis.detection_runs
                    if r.topic.label.strip().casefold() == last_det.topic.label.strip().casefold()
                    and r.metadata.get("run_id") != run_id_val
                ]
                if prior_runs:
                    with st.expander(f"📜 Storico inferenze per '{last_det.topic.label}' ({len(prior_runs)} precedenti in sessione)"):
                        for p_idx, prev_r in enumerate(reversed(prior_runs), start=1):
                            p_meta = prev_r.metadata or {}
                            p_flow = "Discovery Verification" if p_meta.get("origin_flow") == "DISCOVERY_VERIFICATION" else "Detection Manuale"
                            p_mode = "Sola label" if p_meta.get("query_mode") == "LABEL_ONLY" else "Label + Descrizione"
                            st.markdown(
                                f"- **Run #{p_idx}:** `{p_meta.get('run_id', 'n/a')}` | **Flusso:** {p_flow} | **Input:** {p_mode} | **Esito:** `{prev_r.decision.value}`"
                            )

                # Se presente una verifica da discovery con esito diverso, mostra chiarimento esplicito
                disc_verifs = [r for r in prior_runs if r.metadata.get("origin_flow") == "DISCOVERY_VERIFICATION"]
                if disc_verifs and origin_flow == "MANUAL_DETECTION":
                    last_v = disc_verifs[-1]
                    if last_v.decision != last_det.decision:
                        st.info(
                            f"ℹ️ **Discrepanza tra inferenze indipendenti**: Una precedente verifica da Topic Discovery (Run `{last_v.metadata.get('run_id')}`) "
                            f"ha prodotto **{last_v.decision.value}** (utilizzando come input la descrizione estratta da Discovery), "
                            f"mentre questa Detection manuale (Run `{run_id_val}`, input: {input_mode_str}) "
                            f"ha prodotto **{last_det.decision.value}**. Ciascuna inferenza riflette fedelmente il proprio input e rimane immutabile."
                        )

        # ==================================================
        # AREA 2: Filtra risultati Detection già eseguiti
        # ==================================================
        st.divider()
        st.subheader("📂 Area 2: Filtra risultati Detection già eseguiti")
        st.caption("Filtro e consultazione delle detection già presenti in sessione tramite search_topic_detections().")

        c_filt, c_srch = st.columns([1, 2])
        with c_filt:
            sel_dec = st.selectbox(
                "Filtro Decisione",
                options=[d.value for d in TopicFilterDecision],
                index=0,
                key="filter_existing_decision",
            )
        with c_srch:
            query_det = st.text_input(
                "Cerca per label/descrizione topic",
                placeholder="Es. accordo, contrabbando",
                key="filter_existing_query",
            )

        active_service = state.get_search_service() or service
        try:
            det_results = search_topic_detections(
                search_service=active_service,
                query_text=query_det,
                decision_filter=TopicFilterDecision(sel_dec),
            )
        except Exception as exc:
            st.error(f"Errore durante l'interrogazione dei topic: {type(exc).__name__}")
            return

        st.write(f"Visualizzati **{len(det_results.hits)}** risultati su **{det_results.total_hits}** totali.")

        if not det_results.hits:
            st.info("Nessun topic soddisfa i filtri selezionati.")

        for idx, hit in enumerate(det_results.hits, start=1):
            with st.container(border=True):
                dec_val = hit.decision.value if hasattr(hit.decision, "value") else str(hit.decision)
                if dec_val == "PRESENT":
                    badge = "🟢 PRESENTE"
                elif dec_val == "ABSENT":
                    badge = "⚪ ASSENTE"
                else:
                    badge = "🟡 INCERTO"

                h_col, b_col = st.columns([3, 1])
                with h_col:
                    st.markdown(f"### {hit.label}")
                with b_col:
                    st.markdown(f"**Esito:** {badge}")

                st.markdown(f"**Descrizione AI del Topic:** {hit.description}")

                rationale = hit.metadata.get("rationale")
                if rationale:
                    st.info(f"**Motivazione AI (Rationale):** {rationale}")

                meta = hit.metadata
                mod_name = meta.get("model")
                lat = meta.get("latency_seconds")
                if mod_name or lat is not None:
                    lat_str = f"{lat:.2f}s" if isinstance(lat, (int, float)) else "n/a"
                    st.caption(f"Modello: `{mod_name or 'n/a'}` | Latenza: `{lat_str}`")

                st.write(f"**Evidenze collegate:** {len(hit.evidence_ids)} sezioni")

                if hit.matched_sections:
                    with st.expander(f"Visualizza le {len(hit.matched_sections)} Evidenze Originali Collegate"):
                        for s_idx, sec in enumerate(hit.matched_sections, start=1):
                            st.markdown(
                                f"**[EVIDENZA ORIGINALE #{s_idx}]** `{sec.source_type.value}` | ID: `{sec.evidence_id}`"
                            )
                            st.text_area(
                                "Testo Originale Acquisito",
                                value=sec.text,
                                height=60,
                                key=f"sec_top_{idx}_{hit.topic_id}_{sec.evidence_id}_{s_idx}",
                                disabled=True,
                            )

    with tab_disc:
        st.subheader("💡 Topic Discovery (Scansione Argomenti Emersi)")
        st.caption(
            "Identifica autonomamente i temi principali e i cluster della conversazione corrente "
            "eseguendo una scansione aperta tramite LM Studio locale (Qwen2.5-7B-Instruct)."
        )

        col_d1, col_d2 = st.columns([2, 1])
        with col_d1:
            disc_model_val = state.get_lm_studio_model() or DEFAULT_OPERATIONAL_MODEL
            disc_model_input = st.text_input(
                "Modello LM Studio per Discovery:",
                value=disc_model_val,
                placeholder="qwen2.5-7b-instruct",
                key="disc_model_input",
            )
        with col_d2:
            max_topics_val = st.number_input(
                "Numero max topic:",
                min_value=1,
                max_value=25,
                value=10,
                key="disc_max_topics",
            )

        # Stima preventiva Discovery allineata dinamicamente al numero di topic
        try:
            from ui.application import get_operational_analysis_service
            disc_svc = get_operational_analysis_service()
            plan_disc = disc_svc.create_analysis_plan(
                document=doc,
                analysis_type="discovery",
                max_topics=int(max_topics_val),
                profile=selected_profile,
            )
            st.caption(
                f"ℹ️ **Piano Discovery ({int(max_topics_val)} topic):** `{plan_disc.planned_chunks_count}` blocchi previsti | "
                f"Budget chunk effettivo: `{plan_disc.chunk_token_budget}` token | "
                f"Riserva output: `{plan_disc.output_token_budget}` token"
            )
        except Exception:
            pass

        run_discovery_btn = st.button("🚀 Esegui Topic Discovery (LIVE)", type="primary", key="btn_run_live_discovery")

        if run_discovery_btn:
            client = state.get_lm_studio_client()
            raw_model = disc_model_input.strip() if disc_model_input.strip() else DEFAULT_OPERATIONAL_MODEL
            progress_placeholder = st.empty()
            prog_cb = create_streamlit_progress_callback(progress_placeholder, title="TOPIC DISCOVERY IN CORSO")
            try:
                progress_placeholder.info("Preparazione LM Studio per Topic Discovery...")
                with st.spinner("Scansione aperta e scoperta argomenti in corso..."):
                    disc_res = execute_topic_discovery(
                        document=doc,
                        client=client,
                        model_id=raw_model,
                        max_topics=int(max_topics_val),
                        progress_callback=prog_cb,
                        profile=selected_profile,
                    )
                progress_placeholder.empty()
                state.add_discovery_result(disc_res)
                state.set_lm_studio_model(raw_model)
                d_meta_res = getattr(disc_res, "metadata", {}) or {}
                chunk_c_res = d_meta_res.get("chunk_count", 1)
                comp_c_res = d_meta_res.get("completed_chunk_count", 1)
                fail_c_res = d_meta_res.get("failed_chunk_count", 0)
                disc_st = d_meta_res.get("status", "COMPLETE")
                if disc_st == "PARTIAL" or fail_c_res > 0:
                    st.warning(f"⚠️ Topic Discovery parziale (PARTIAL): {comp_c_res}/{chunk_c_res} blocchi completati (falliti: {fail_c_res}). Emersi {len(disc_res.topics)} argomenti.")
                else:
                    st.success(f"Topic Discovery completata! Emersi {len(disc_res.topics)} argomenti.")
                if chunk_c_res > 1 and disc_st != "PARTIAL":
                    st.info(f"Diagnostica blocchi: {comp_c_res}/{chunk_c_res} completati con successo (falliti: {fail_c_res}).")
            except LmStudioUnavailableError:
                progress_placeholder.empty()
                st.error("LM Studio non raggiungibile su http://127.0.0.1:1234. Verificare che il server locale sia attivo.")
            except (AiBackendRequestError, AiBackendProtocolError) as b_err:
                progress_placeholder.empty()
                render_backend_error_details(b_err)
            except Exception as exc:
                progress_placeholder.empty()
                st.error(f"Errore durante l'esecuzione di Topic Discovery: {type(exc).__name__}: {exc}")

        st.divider()

        query_disc = st.text_input("Cerca negli argomenti scoperti", placeholder="Es. logistica, contratti", key="disc_query_input")

        active_service = state.get_search_service() or service
        try:
            disc_results = search_topic_discoveries(
                search_service=active_service,
                query_text=query_disc,
            )
        except Exception as exc:
            st.error(f"Errore durante l'interrogazione degli argomenti scoperti: {type(exc).__name__}")
            return

        st.write(f"Visualizzati **{len(disc_results.hits)}** argomenti emersi.")

        if not disc_results.hits:
            st.info("Nessun argomento emerso corrisponde alla ricerca. Avviare la Topic Discovery per analizzare la chat.")

        for idx, hit in enumerate(disc_results.hits, start=1):
            with st.container(border=True):
                st.markdown(f"### 💡 {hit.label}")
                st.markdown(f"**Sintesi AI:** {hit.description}")
                d_meta = hit.metadata or {}
                d_run_id = d_meta.get("run_id", "n/a")
                d_chunk_c = d_meta.get("chunk_count", 1)
                d_comp_c = d_meta.get("completed_chunk_count", 1)
                d_fail_c = d_meta.get("failed_chunk_count", 0)
                d_prov = d_meta.get("provenance_document_id", "n/a")
                st.caption(f"Discovery Run ID: `{d_run_id}` | Chunk analizzati: `{d_comp_c}/{d_chunk_c}` (falliti: {d_fail_c}) | Provenance: `{d_prov}`")
                st.write(f"**Evidenze di supporto:** {len(hit.evidence_ids)} sezioni")

                if hit.matched_sections:
                    with st.expander(f"Visualizza le {len(hit.matched_sections)} Evidenze di Supporto"):
                        for s_idx, sec in enumerate(hit.matched_sections, start=1):
                            st.markdown(
                                f"**[EVIDENZA ORIGINALE #{s_idx}]** `{sec.source_type.value}` | ID: `{sec.evidence_id}`"
                            )
                            st.text_area(
                                "Testo Originale di Supporto",
                                value=sec.text,
                                height=60,
                                key=f"disc_sec_{idx}_{hit.topic_id}_{sec.evidence_id}_{s_idx}",
                                disabled=True,
                            )

                # Pulsante: Verifica con Topic Detection (Prova del Nove)
                verify_col, _ = st.columns([2, 3])
                with verify_col:
                    verify_btn = st.button(
                        "🔬 Verifica con Topic Detection",
                        key=f"verify_disc_topic_{idx}_{hit.topic_id}",
                    )

                if verify_btn:
                    if doc is None:
                        st.error("Nessun documento di conversazione disponibile.")
                    elif not doc.all_evidence_sections:
                        st.error("Documento privo di evidenze forensi: impossibile avviare la Topic Detection.")
                    else:
                        disc_prov_id = hit.metadata.get("provenance_document_id")
                        try:
                            validate_discovery_provenance(
                                discovery_provenance_id=disc_prov_id,
                                active_document_id=doc.document_id,
                            )
                        except TopicProvenanceMismatchError as p_err:
                            st.error(f"Operazione bloccata per discrepanza di provenienza: {p_err}")
                        else:
                            client = state.get_lm_studio_client()
                            raw_model = state.get_lm_studio_model() or DEFAULT_OPERATIONAL_MODEL
                            progress_placeholder = st.empty()
                            prog_cb = create_streamlit_progress_callback(progress_placeholder, title="VERIFICA PROVA DEL NOVE IN CORSO")
                            try:
                                progress_placeholder.info("Preparazione seconda inferenza indipendente...")
                                with st.spinner("Seconda inferenza indipendente (Prova del Nove) in corso..."):
                                    det_res, consistency_res = execute_discovery_verification(
                                        document=doc,
                                        discovered_topic=hit.raw_result,
                                        discovery_run_id=hit.metadata.get("run_id"),
                                        client=client,
                                        model_id=raw_model,
                                        progress_callback=prog_cb,
                                        profile=selected_profile,
                                    )
                                progress_placeholder.empty()
                                state.add_detection_result(det_res)
                                state.add_consistency_result(consistency_res)
                                state.set_last_manual_detection(det_res)
                                state.set_custom_topic_label(hit.label)
                                state.set_custom_topic_description(hit.description)
                                state.set_lm_studio_model(raw_model)

                                if consistency_res.status == ConsistencyStatus.CONSISTENT:
                                    st.success(f"Topic '{hit.label}' verificato! Esito: PRESENT (COERENTE con evidenze in comune)")
                                elif consistency_res.status == ConsistencyStatus.PARTIALLY_CONSISTENT:
                                    st.info(f"Topic '{hit.label}' verificato! Esito: PRESENT (PARZIALMENTE COERENTE su evidenze distinte)")
                                elif consistency_res.status == ConsistencyStatus.DISCREPANCY_ABSENT:
                                    st.warning(f"⚠️ DISCREPANZA: Topic '{hit.label}' valutato ABSENT in Topic Detection")
                                else:
                                    st.warning(f"⚠️ INCERTO: Topic '{hit.label}' valutato UNCERTAIN in Topic Detection")
                            except LmStudioUnavailableError:
                                progress_placeholder.empty()
                                st.error("LM Studio non raggiungibile su http://127.0.0.1:1234. Verificare che il server locale sia attivo.")
                            except (AiBackendRequestError, AiBackendProtocolError) as b_err:
                                progress_placeholder.empty()
                                render_backend_error_details(b_err)
                            except Exception as exc:
                                progress_placeholder.empty()
                                st.error(f"Errore durante la verifica con Topic Detection: {type(exc).__name__}: {exc}")

                # Visualizzazione dell'esito di verifica e consistency
                target_run_id = hit.metadata.get("run_id")
                cons_list = [
                    c for c in state.get_consistency_results(doc.document_id)
                    if c.topic_label.strip().casefold() == hit.label.strip().casefold()
                    and (target_run_id is None or c.metadata.get("discovery_run_id") == target_run_id or c.metadata.get("discovery_run_id") is None)
                ]
                if cons_list:
                    latest_cons = cons_list[-1]
                    with st.container(border=True):
                        if latest_cons.status == ConsistencyStatus.CONSISTENT:
                            badge_str = "✅ COERENTE"
                        elif latest_cons.status == ConsistencyStatus.PARTIALLY_CONSISTENT:
                            badge_str = "🔵 PARZIALMENTE COERENTE"
                        elif latest_cons.status == ConsistencyStatus.DISCREPANCY_ABSENT:
                            badge_str = "⚠️ DISCREPANZA (ABSENT)"
                        else:
                            badge_str = "🟡 INCERTO (UNCERTAIN)"

                        st.markdown(f"#### 🔬 Risultato Verifica Indipendente: {badge_str}")
                        st.markdown(f"**Consistency status:** `{latest_cons.status.value}`")
                        st.info(f"**Valutazione Consistenza:** {latest_cons.rationale}")

                        c_d1, c_d2 = st.columns(2)
                        with c_d1:
                            st.markdown(f"**Discovery topic:** {hit.label}")
                            disc_eids_str = ", ".join(latest_cons.discovery_evidence_ids) if latest_cons.discovery_evidence_ids else "Nessuna"
                            st.markdown(f"**Discovery evidence IDs:** `{disc_eids_str}`")
                        with c_d2:
                            st.markdown(f"**Detection decision:** `{latest_cons.metadata.get('detection_decision', 'n/a')}`")
                            det_eids_str = ", ".join(latest_cons.detection_evidence_ids) if latest_cons.detection_evidence_ids else "Nessuna"
                            st.markdown(f"**Detection evidence IDs:** `{det_eids_str}`")

                        overlap_str = ", ".join(latest_cons.overlap_evidence_ids) if latest_cons.overlap_evidence_ids else "Nessuna evidenza in comune"
                        st.caption(f"Evidenze condivise in overlap: `{overlap_str}`")

                # Controllo correlazione se esiste una detection manuale separata per questo topic
                doc_analysis = state.get_document_analysis_state(doc.document_id)
                manual_dets = [
                    r for r in doc_analysis.detection_runs
                    if r.topic.label.strip().casefold() == hit.label.strip().casefold()
                    and r.metadata.get("origin_flow") == "MANUAL_DETECTION"
                ]
                if manual_dets:
                    latest_m = manual_dets[-1]
                    m_meta = latest_m.metadata or {}
                    m_mode = "Sola label" if m_meta.get("query_mode") == "LABEL_ONLY" else "Label + Descrizione"
                    st.info(
                        f"📌 **Nota di correlazione indipendente**: Nella sessione esiste anche una Detection manuale per '{hit.label}' "
                        f"(Run `{m_meta.get('run_id')}`, input: {m_mode}) con esito **{latest_m.decision.value}**. "
                        f"La verifica sopra visualizzata è ancorata unicamente alla specifica Discovery (Run `{target_run_id}`) "
                        f"e non viene alterata da nuove inferenze indipendenti."
                    )

        # Sezione opzionale di riferimento per la modalità DEMO
        if state.get_dataset_mode() == DatasetMode.DEMO:
            ref_discoveries = state.get_demo_reference_discovery_results()
            if ref_discoveries:
                with st.expander("📋 Risultati di Riferimento Sintetici (DEMO Reference)", expanded=False):
                    st.caption("Argomenti attesi dal dataset sintetico per verifica e test offline.")
                    for ref_d in ref_discoveries:
                        for top in ref_d.topics:
                            st.markdown(f"- **{top.label}**: {top.short_description} (Evidenze attese: `{', '.join(top.evidence_ids)}`)")


def render_system_status() -> None:
    """Schermata Sistema / Stato: diagnostica e aderenza architetturale."""
    st.header("⚙️ Stato del Sistema & Diagnostica Architetturale")

    status_info = get_system_status_info(streamlit_version=st.__version__)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Componenti Pipeline")
        st.write(f"- **Search Layer**: `{status_info['search_layer']}`")
        st.write(f"- **Local AI Layer**: `{status_info['local_ai_architecture']}`")
        st.write(f"- **Real LM Studio Benchmark**: `{status_info['real_lm_studio_benchmark']}`")
        st.write(f"- **Real File Ingestion**: `{status_info['real_file_ingestion']}`")

    with c2:
        st.subheader("Vincoli e Sicurezza")
        st.write(f"- **Streamlit Version**: `{status_info['streamlit_version']}`")
        st.write(f"- **Network**: `{status_info['network_status']}`")
        st.write(f"- **Semantic Search**: `{status_info['semantic_search']}`")
        st.write(f"- **Embeddings / Vector DB**: `{status_info['embeddings']}`")

    st.divider()

    st.subheader("LM Studio / Benchmark")
    st.info(
        "- **Local AI**: disponibile tramite server locale LM Studio su `http://127.0.0.1:1234`.\n"
        "- **Inferenza operativa**: eseguita on-demand per Topic Detection e Topic Discovery con gestione autonoma del modello.\n"
        "- **Benchmark formale**: eseguito separatamente tramite la suite sperimentale (`ai/experiment.py` / `run_benchmark.sh`).\n"
        "- **Ambiente e hardware**: dettagli hardware e sistema operativo vengono registrati automaticamente nei report del benchmark.\n"
        "- **Portabilità**: nessun vincolo hardware specifico (es. CPU o architettura host) è hardcoded nella UI."
    )
