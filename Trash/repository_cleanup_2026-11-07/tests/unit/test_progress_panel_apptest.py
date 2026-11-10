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


def _progress_text(p) -> str:
    """Didascalia di una barra st.progress (NON .value, che e' la percentuale 0-100)."""
    t = getattr(p, "text", None)
    return str(t if t is not None else p.proto.text)


def _texts(at: AppTest) -> str:
    # Le righe "Blocchi principali" e "Microchunk completati" sono la didascalia delle barre st.progress.
    parts = [str(e.value) for e in list(at.markdown) + list(at.caption) + list(at.warning) + list(at.info) + list(at.success)]
    parts += [_progress_text(p) for p in at.progress]
    return "\n".join(parts)


def _bars(at: AppTest):
    """(barra blocchi principali, barra microchunk) dell'ULTIMO rendering: le ultime due barre dell'albero."""
    bars = list(at.progress)
    assert len(bars) >= 2, f"attese due barre (parent + microchunk), trovate {len(bars)}"
    return bars[-2], bars[-1]


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


def test_success_scenario_both_bars_are_full_and_labelled():
    at = _run("Successo (3 microchunk)")
    parent, micro = _bars(at)
    assert _progress_text(parent).startswith("Blocchi principali: 1 / 1")
    assert _progress_text(micro).startswith("Microchunk completati: 3 / 3")
    assert parent.value == 100 and micro.value == 100


def test_partial_scenario_validated_micro_bar_and_parent_bar_stay_below_100():
    at = _run("Parziale (un microchunk non validato)")
    parent, micro = _bars(at)
    assert _progress_text(micro).startswith("Microchunk completati: 2 / 3")
    # le unita' processate sono 3/3 (2 validate + 1 fallita), ma la barra mostra solo le VALIDATE
    assert micro.value < 100, "un microchunk fallito non deve portare la barra dei validati al 100%"
    assert parent.value < 100, "il parent non e' validato: la copertura dei blocchi principali non e' completa"
    assert "Unità processate: 3 / 3 (validate 2, fallite 1)" in _texts(at)
