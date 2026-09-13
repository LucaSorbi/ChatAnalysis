"""
app.py
------
Punto di ingresso principale per l'interfaccia locale Streamlit.

Esecuzione:
    .venv\\Scripts\\streamlit.exe run app.py
    oppure
    .venv\\Scripts\\python.exe -m streamlit run app.py

Caratteristiche:
- Offline al 100%: nessuna connessione di rete, nessuna telemetria.
- Layout wide, interfaccia professionale e responsiva.
- Separazione netta: app.py gestisce solo navigazione e configurazione,
  delegando rendering a ui.presentation e logica a ui.application.
"""
from __future__ import annotations

import streamlit as st

from ui import presentation, state
from ui.models import DatasetMode

# Configurazione pagina Streamlit (nessun unsafe_allow_html, layout wide)
st.set_page_config(
    page_title="Forensic Chat Analysis & Search",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Inizializzazione centralizzata dello stato di sessione
state.init_session_state()

# Sidebar: branding e navigazione
st.sidebar.title("🔍 Forensic Analyzer")
st.sidebar.caption("Piattaforma Forense di Analisi & Ricerca Chat")

# Stato sintetico del dataset in sidebar
if state.is_dataset_loaded():
    mode = state.get_dataset_mode()
    doc = state.get_conversation_document()
    bundles_cnt = len(doc.bundles) if doc else 0
    st.sidebar.success(f"Dataset: **{mode.value}** ({bundles_cnt} msg)")
else:
    st.sidebar.warning("Nessun dataset caricato")

st.sidebar.divider()

# Menu di navigazione
NAV_SECTIONS = [
    "1. Panoramica",
    "2. Importazione",
    "3. Esplora conversazione",
    "4. Ricerca",
    "5. Analisi topic",
    "6. Sistema / Stato",
]

selected_page = st.sidebar.radio(
    "Navigazione",
    options=NAV_SECTIONS,
    index=0,
)

st.sidebar.divider()
st.sidebar.markdown(
    "**Specifiche di Sicurezza**\n"
    "- 🔒 Analisi 100% Offline\n"
    "- 🛡️ Deep Immutability\n"
    "- 📜 Provenance Integrale\n"
    "- 🚫 Nessuna Telemetria"
)

# Routing verso i componenti di presentazione
if selected_page == "1. Panoramica":
    presentation.render_overview()
elif selected_page == "2. Importazione":
    presentation.render_import()
elif selected_page == "3. Esplora conversazione":
    presentation.render_explore()
elif selected_page == "4. Ricerca":
    presentation.render_search()
elif selected_page == "5. Analisi topic":
    presentation.render_topics()
elif selected_page == "6. Sistema / Stato":
    presentation.render_system_status()
