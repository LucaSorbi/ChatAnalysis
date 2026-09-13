"""
ui
--
Package per l'interfaccia utente locale Streamlit e il relativo application layer.

Separazione rigorosa:
- ui.models: DTO e definizioni di presentazione pure Python.
- ui.state: Gestione tipizzata e centralizzata di st.session_state.
- ui.demo: Costruttore del dataset sintetico conforme a tutti i modelli di dominio.
- ui.application: Application layer testabile e privo di dipendenza Streamlit.
- ui.presentation: Componenti grafici e renderer Streamlit.
"""
from ui.models import (
    DatasetMode,
    DocumentSummary,
    EvidenceFilterCriteria,
    SourceFormat,
    TopicFilterDecision,
)

__all__ = [
    "DatasetMode",
    "SourceFormat",
    "TopicFilterDecision",
    "DocumentSummary",
    "EvidenceFilterCriteria",
]
