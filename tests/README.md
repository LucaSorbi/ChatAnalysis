# Test suite

```bash
pip install -e ".[ui,dev]"      # ui = streamlit (test AppTest); dev = pytest, pytest-cov
python -m pytest                # unit + integration (esclude i test marcati `smoke`)
python -m pytest -m smoke       # smoke test (richiedono dipendenze/modelli reali)
```

- Senza Streamlit i moduli AppTest/UI vengono saltati con un avviso (vedi `conftest.py`); installare l'extra `ui` per eseguirli.
- Opzionali: `faster-whisper` (extra `stt`) e `Pillow`/`pytesseract` (extra `ocr`): i test relativi si saltano se mancano.
- Dati di test: solo sintetici, in `test_data/` (vedi `test_data/README.md`).
- Non inclusi in questa suite (restano in `Trash/repository_cleanup_2026-11-07/tests/unit/`, perché verificano
  script, runbook e benchmark archiviati in `Trash/`): test_packaging_release, test_detection_ui_and_discovery_minor_fixes,
  test_checkpoint_preflight_resume, test_runbook_smoke, test_smoke_operational_ai, test_model_resolution_audit,
  test_verify_package_manifest, test_analysis_export, test_audit_rebuild_blockers_20261024,
  test_checkpoint_safe_diagnostics, test_sanitize_dataset, test_trash_archive_integrity.
