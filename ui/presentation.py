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

import streamlit as st

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
from ai.models import TopicDecision
from search.models import MatchMode
from ui import state
from ui.application import (
    build_topic_query,
    check_lm_studio_status,
    execute_manual_topic_detection,
    execute_evidence_search,
    execute_real_ingestion,
    filter_evidence_sections,
    get_evidence_filter_options,
    get_system_status_info,
    prepare_operational_model,
    search_topic_detections,
    search_topic_discoveries,
    summarize_document,
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
    else:
        primary_types = ["db", "sqlite", "csv", "json", "xml"]

    uploaded_file = st.file_uploader(
        f"Seleziona file primario ({source_format.value})",
        type=primary_types,
        help="Il file viene elaborato esclusivamente in spazio temporaneo locale isolato con cleanup deterministico.",
        key="primary_file_uploader",
    )

    companion_file = None
    if source_format == SourceFormat.WHATSAPP_MSGSTORE:
        companion_file = st.file_uploader(
            "wa.db contatti — opzionale",
            type=["db", "sqlite"],
            help="Opzionale database contatti/identità WhatsApp per l'arricchimento dei sender.",
            key="companion_wa_uploader",
        )

    import_btn = st.button("📥 Importa e analizza struttura", type="primary")

    if import_btn:
        if uploaded_file is None:
            st.warning("Selezionare un file primario prima di procedere con l'importazione.")
        else:
            primary_bytes = uploaded_file.getvalue()
            companion_bytes = companion_file.getvalue() if companion_file is not None else None
            companion_name = companion_file.name if companion_file is not None else None

            req = IngestionRequest(
                source_format=source_format,
                filename=uploaded_file.name,
                file_bytes=primary_bytes,
                companion_filename=companion_name,
                companion_bytes=companion_bytes,
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

    # Gestione specifica per Real File Mode senza risultati AI
    if (
        state.get_dataset_mode() == DatasetMode.FILE
        and not state.get_detection_results()
        and not state.get_discovery_results()
    ):
        st.info(
            "Il dataset reale è stato importato correttamente.\n\n"
            "L'analisi AI non è stata eseguita in questa fase.\n\n"
            "Il benchmark/inference con modelli locali è differito alla fase sperimentale finale."
        )
        if state.get_conversation_document() is None:
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
                    status_placeholder = st.empty()
                    try:
                        status_placeholder.info("Verifica LM Studio locale...")
                        is_loaded = hasattr(client, "is_model_loaded") and client.is_model_loaded(raw_model_choice)
                        if not is_loaded:
                            status_placeholder.info("Preparazione del modello AI locale...")

                        resolved_model = prepare_operational_model(
                            client=client,
                            requested_model=raw_model_choice,
                            timeout_seconds=DEFAULT_OPERATIONAL_TIMEOUT_SECONDS,
                        )
                        display_name = get_model_display_name(resolved_model)
                        status_placeholder.info(f"{display_name} pronto.")

                        with st.spinner("Analisi locale in corso..."):
                            det_res = execute_manual_topic_detection(
                                document=doc,
                                topic=q,
                                client=client,
                                model_id=resolved_model,
                                timeout_seconds=DEFAULT_OPERATIONAL_TIMEOUT_SECONDS,
                                max_tokens=DEFAULT_OPERATIONAL_MAX_TOKENS,
                            )
                        status_placeholder.empty()
                        state.add_detection_result(det_res)
                        state.set_last_manual_detection(det_res)
                        state.set_custom_topic_label(topic_input)
                        state.set_custom_topic_description(desc_input)
                        state.set_lm_studio_model(resolved_model)
                        st.success(f"Topic Detection completata! Esito: {det_res.decision.value}")
                    except LmStudioUnavailableError:
                        status_placeholder.empty()
                        st.error(
                            "LM Studio non raggiungibile su http://127.0.0.1:1234. "
                            "Verificare che il server locale di LM Studio sia attivo."
                        )
                    except AiModelNotInstalledError as ni_err:
                        status_placeholder.empty()
                        st.error(str(ni_err))
                    except AiModelAmbiguousError as amb_err:
                        status_placeholder.empty()
                        st.error(str(amb_err))
                    except AiModelLoadError as l_err:
                        status_placeholder.empty()
                        st.error(f"Errore durante il caricamento del modello in LM Studio: {l_err}")
                    except AiBackendTimeoutError as t_err:
                        status_placeholder.empty()
                        st.error(f"Timeout durante l'operazione con LM Studio: {t_err}")
                    except AiModelMismatchError as m_err:
                        status_placeholder.empty()
                        st.error(f"Disallineamento modello LM Studio: {m_err}")
                    except AiInvalidEvidenceCitationError as c_err:
                        status_placeholder.empty()
                        st.error(f"Errore citazione evidenze: {c_err}")
                    except AiStructuredOutputError as s_err:
                        status_placeholder.empty()
                        st.error(f"Errore schema strutturato: {s_err}")
                    except (AiBackendRequestError, AiBackendProtocolError) as b_err:
                        status_placeholder.empty()
                        st.error(f"Errore di comunicazione con LM Studio: {b_err}")
                    except Exception as exc:
                        status_placeholder.empty()
                        st.error(f"Errore durante l'esecuzione della Topic Detection: {type(exc).__name__}")

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

                if last_det.topic.description and last_det.topic.description != last_det.topic.label:
                    st.markdown(f"**Descrizione Topic:** {last_det.topic.description}")

                st.info(f"**Motivazione AI (Rationale):** {last_det.rationale}")

                meta = last_det.metadata
                mod_name = meta.get("model", "n/a")
                lat = meta.get("latency_seconds")
                lat_str = f"{lat:.2f}s" if isinstance(lat, (int, float)) else "n/a"
                st.caption(f"Modello: `{mod_name}` | Latenza: `{lat_str}` | Provenance: `{last_det.provenance_document_id}`")

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
                                key=f"sec_top_{hit.topic_id}_{sec.evidence_id}_{s_idx}",
                                disabled=True,
                            )

    with tab_disc:
        st.subheader("Argomenti Emersi (Open Topic Discovery)")
        st.caption("Argomenti e cluster identificati autonomamente dal layer AI durante la scansione aperta.")

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
            st.info("Nessun argomento emerso corrisponde alla ricerca.")

        for idx, hit in enumerate(disc_results.hits, start=1):
            with st.container(border=True):
                st.markdown(f"### 💡 {hit.label}")
                st.markdown(f"**Sintesi AI:** {hit.description}")
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
                                key=f"disc_sec_{hit.topic_id}_{sec.evidence_id}_{s_idx}",
                                disabled=True,
                            )

                # Pulsante: Verifica con Topic Detection
                verify_col, _ = st.columns([2, 3])
                with verify_col:
                    verify_btn = st.button(
                        "🔬 Verifica con Topic Detection",
                        key=f"verify_disc_topic_{idx}_{hit.topic_id}",
                    )

                if verify_btn:
                    if doc is None:
                        st.error("Nessun documento di conversazione disponibile.")
                    else:
                        try:
                            disc_query = build_topic_query(
                                label=hit.label,
                                description=hit.description,
                            )
                        except ValueError as v_err:
                            st.error(f"Errore nella creazione della TopicQuery: {v_err}")
                            disc_query = None

                        if disc_query is not None:
                            client = state.get_lm_studio_client()
                            raw_model = state.get_lm_studio_model() or DEFAULT_OPERATIONAL_MODEL
                            disc_status = st.empty()
                            try:
                                disc_status.info("Verifica LM Studio locale...")
                                is_loaded = hasattr(client, "is_model_loaded") and client.is_model_loaded(raw_model)
                                if not is_loaded:
                                    disc_status.info("Preparazione del modello AI locale...")

                                resolved_model = prepare_operational_model(
                                    client=client,
                                    requested_model=raw_model,
                                    timeout_seconds=DEFAULT_OPERATIONAL_TIMEOUT_SECONDS,
                                )
                                display_name = get_model_display_name(resolved_model)
                                disc_status.info(f"{display_name} pronto.")

                                with st.spinner("Analisi locale in corso..."):
                                    det_res = execute_manual_topic_detection(
                                        document=doc,
                                        topic=disc_query,
                                        client=client,
                                        model_id=resolved_model,
                                        timeout_seconds=DEFAULT_OPERATIONAL_TIMEOUT_SECONDS,
                                        max_tokens=DEFAULT_OPERATIONAL_MAX_TOKENS,
                                    )
                                disc_status.empty()
                                state.add_detection_result(det_res)
                                state.set_last_manual_detection(det_res)
                                state.set_custom_topic_label(hit.label)
                                state.set_custom_topic_description(hit.description)
                                state.set_lm_studio_model(resolved_model)
                                st.success(f"Topic '{hit.label}' verificato! Esito: {det_res.decision.value}")
                            except LmStudioUnavailableError:
                                disc_status.empty()
                                st.error("LM Studio non raggiungibile su http://127.0.0.1:1234. Verificare che il server locale sia attivo.")
                            except AiModelNotInstalledError as ni_err:
                                disc_status.empty()
                                st.error(str(ni_err))
                            except AiModelAmbiguousError as amb_err:
                                disc_status.empty()
                                st.error(str(amb_err))
                            except AiModelLoadError as l_err:
                                disc_status.empty()
                                st.error(f"Errore durante il caricamento del modello in LM Studio: {l_err}")
                            except AiBackendTimeoutError as t_err:
                                disc_status.empty()
                                st.error(f"Timeout durante l'operazione con LM Studio: {t_err}")
                            except AiModelMismatchError as m_err:
                                disc_status.empty()
                                st.error(f"Disallineamento modello LM Studio: {m_err}")
                            except AiInvalidEvidenceCitationError as c_err:
                                disc_status.empty()
                                st.error(f"Errore citazione evidenze: {c_err}")
                            except AiStructuredOutputError as s_err:
                                disc_status.empty()
                                st.error(f"Errore schema strutturato: {s_err}")
                            except (AiBackendRequestError, AiBackendProtocolError) as b_err:
                                disc_status.empty()
                                st.error(f"Errore di comunicazione con LM Studio: {b_err}")
                            except Exception as exc:
                                disc_status.empty()
                                st.error(f"Errore durante la verifica con Topic Detection: {type(exc).__name__}")

                last_det = state.get_last_manual_detection()
                if last_det is not None and last_det.topic.label == hit.label:
                    with st.container(border=True):
                        dec_val = last_det.decision.value
                        badge_str = "🟢 PRESENTE" if dec_val == "PRESENT" else "⚪ ASSENTE" if dec_val == "ABSENT" else "🟡 INCERTO"
                        st.markdown(f"**Esito Verifica Recente:** {badge_str}")
                        st.info(f"**Rationale:** {last_det.rationale}")
                        st.caption(f"Evidenze valide collegate: {len(last_det.evidence_ids)} | Modello: {last_det.metadata.get('model', 'n/a')}")


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
