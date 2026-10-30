"""
AppTest REALE del pannello di avanzamento (Streamlit autentico + ijson autentico).
Viene SALTATO (quindi NOT_VERIFIED, non "passato") se manca Streamlit reale/`streamlit.testing.v1`.
Su Windows: python -m pytest tests\\unit\\test_progress_panel_apptest.py -q  (exit code 0 = ok; "skipped" = non verificato)
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1", reason="Streamlit reale non disponibile: AppTest NON VERIFICATO")
pytest.importorskip("ijson", reason="ijson reale non disponibile")
from streamlit.testing.v1 import AppTest  # noqa: E402

SCRIPT = str(Path(__file__).resolve().parent.parent.parent / "scripts" / "preview_discovery_progress_panel.py")


def _run(scenario: str) -> AppTest:
    os.environ["CHATANALYSIS_PREVIEW_DELAY"] = "0"
    at = AppTest.from_file(SCRIPT, default_timeout=60).run()
    assert not at.exception
    at.radio(key="scenario").set_value(scenario).run()
    at.button(key="run").click().run()
    return at


def _texts(at: AppTest) -> str:
    return "\n".join(str(e.value) for e in list(at.markdown) + list(at.caption) + list(at.warning) + list(at.info) + list(at.success))


@pytest.mark.parametrize("scenario,needles,absent", [
    ("Successo (3 microchunk)", ["Blocchi principali: 1 / 1", "Microchunk completati: 3 / 3", "COMPLETATA"], ["PARZIALE"]),
    ("Parziale (un microchunk non validato)", ["PARZIALE", "validate 2, fallite 1"], ["🟩"]),
    ("Zero topic con 12 evidenze (non verificato)", ["NON VERIFICATA"], ["🟩 COMPLETATA"]),
    ("Zero topic con 6 evidenze (conversazione breve)", ["NESSUN ARGOMENTO EMERSO"], ["🟩"]),
])
def test_real_streamlit_panel_renders_truthful_state(scenario, needles, absent):
    at = _run(scenario)
    assert not at.exception
    text = _texts(at)
    for n in needles:
        assert n in text, n
    for a in absent:
        assert a not in text, a
