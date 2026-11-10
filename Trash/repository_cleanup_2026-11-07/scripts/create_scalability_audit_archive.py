"""
scripts/create_scalability_audit_archive.py
-------------------------------------------
Genera l'archivio ZIP di consegna prescritto dal protocollo:
`chatAnalysis_packaging_trusted_inputs_audit_20261017.zip`

Integrità e Guardrail di Privacy e Trusted Packaging:
1. INVENTARIO ESPLICITO DEI PERCORSI AMMESSI (TRUSTED SOURCE INVENTORY):
   Nessun file sconosciuto viene incluso automaticamente. Qualsiasi file con nome
   ordinario o estensione comune fuori da test_data/ deve appartenere tassativamente
   all'inventario verificabile `TRUSTED_SOURCE_INVENTORY`, altrimenti viene escluso e segnalato.
2. VERIFICA DELL'IDENTITÀ DELLE FIXTURE SINTETICHE (SHA256 TRUSTED BASELINE):
   Ogni fixture sotto test_data/ non solo deve appartenere a un percorso approvato,
   ma il suo hash SHA-256 deve corrispondere esattamente alla baseline attendibile `APPROVED_TEST_DATA_HASHES`.
   Se il contenuto di una fixture autorizzata viene sostituito o alterato, viene rigettato e segnalato.
3. ESCLUSIONE TASSATIVA DI COLLEGAMENTI SIMBOLICI (SYMLINK):
   Tutti i collegamenti simbolici (a file o directory) vengono scartati e i percorsi devono
   risolversi all'interno del repository.
4. ESCLUSIONE FILE SENSIBILI (.env, CHIAVI, CREDENZIALI, CERTIFICATI):
   Esclude automaticamente .env, .env.*, chiavi private, token, credenziali,
   salvo espliciti template pubblici verificati (.env.example, .env.template, .env.sample).
5. ALLOWLIST RIGIDA DEI DATABASE SQLITE E FILE LATERALI:
   I database SQLite, i file laterali (WAL, SHM, journal) e gli archivi .zip sono ammessi SOLO
   se presenti nell'allowlist approvata con hash verificato.
6. RILEVAMENTO DELLA FIRMA MAGICA SQLITE (ANTI-LEAK):
   Ispezione dei primi 16 byte per rilevare ed escludere file con header 'SQLite format 3\x00'.
7. SEGNALAZIONE SENZA LEAK:
   I file esclusi o non riconosciuti vengono tracciati senza mai divulgarne il contenuto nel report.
8. MANIFEST DETERMINISTICO UNICO:
   Generato una sola volta alla fine, privo di auto-referenzialità.
"""
from __future__ import annotations

import os
import zipfile
import hashlib
from pathlib import Path

# Allowlist delle fixture SQLite sintetiche e archivi autorizzati
APPROVED_SYNTHETIC_FIXTURES: frozenset[str] = frozenset({
    "test_data/whatsapp_export/msgstore.db",
    "test_data/whatsapp_export/wa.db",
    "test_data/synthetic_smoke/synthetic_whatsapp_chat.zip",
})

# Baseline attendibile degli hash SHA-256 di tutte le 50 fixture sintetiche ufficiali sotto test_data/
APPROVED_TEST_DATA_HASHES: dict[str, str] = {
    "test_data/README.md": "5e437594dfeed668dee5745ab9dda94bf3933ce03004f0017ed5f2066c286a46",
    "test_data/cellebrite_export/messages.csv": "3e7a9421f34787d52b23884cab534bf2131793bb7ae61439bfe4fc6c2c5b3f00",
    "test_data/cellebrite_export/messages.json": "251003fef2f7439fb58d98116b0f5a8116ab6003a6a8b2ebe9a729ac8920f59e",
    "test_data/cellebrite_export/report.xml": "8c527beee1149f4d22ad1c91dabe6516366e408c7f0c7f38b5eb0a314f21e0a1",
    "test_data/cloud_e2e/ground_truth.json": "377fb95b7690ba507481f357449b1a56d1f89cb60636da9debd66fe603e5ad6d",
    "test_data/cloud_e2e/messages.json": "2e49a9c62d89125d225726953ff0e879fb49e07d7db803db5503c345b051b781",
    "test_data/fixtures/lmstudio/get_models_multi_instance.json": "4ba0a7942c3b7ce0024e22b125756d9b0ab8e4158219e25ce2b03a39dfe608d5",
    "test_data/fixtures/lmstudio/get_models_official_qwen.json": "424b0be0460764bc785a3ab68fa2f51596582480620e90873aa8841d89fdfa60",
    "test_data/fixtures/lmstudio/get_models_unloaded.json": "600ca31acb1b42eab66124aa11612f9f035cfaf354d6adafe0f9da693b0ceffe",
    "test_data/fixtures/lmstudio/post_load_official_response.json": "65dc4e305c445fce9902706301c6af6552bbbd95a5222d74732669aac9f706c5",
    "test_data/synthetic_smoke/synthetic_whatsapp_chat.txt": "db5042181a8e8e983bc5639a5aa787880ee6d6c9b8b302347f16f4e163d04ea9",
    "test_data/synthetic_smoke/synthetic_whatsapp_chat.zip": "74c5b2106bab2b8d5bb2d65e47317a27edd5f4e6e30095e13a192e9c02f3e974",
    "test_data/synthetic_whatsapp_large_75kb.txt": "004d48e31c8122129f09e885b34b8e988b2de83f1d9db7d3c0d7bf20ffb17514",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00000.opus": "692c6543639664bbd8a97724fe8642d1a12cfeba96a5c584591895334b849c06",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00001.opus": "bcbee1d497087f764ab58b154da3c33dec8ed7bc4830f85fe9f67b9e02005a1b",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00002.opus": "d8df2456bff3034ddfcd151da84d6734e1453bdccd2c317a5eaf507b08a94e1c",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00003.opus": "066d66e67b864dff86d9828a7050c6629bc36ba6ff03b205ff3828d7f9d0af22",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00004.opus": "6b3ee09b96d154a6cf59d1557cb8d8d32c106cc9c7ec527260fe5042a0b411d4",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00005.opus": "90655a6106324223b6258bf4a2335902fb62e21c52bd834ecb999f7ca749981e",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00006.opus": "0decb9993382de3a96848289bac8408acb152504bae623f47fc34f8c50348b26",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00007.opus": "89f27071d77ff7bc13e3741058ff5d1b679ba3efe669302f013f2f4aae630d3d",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00008.opus": "d8ed920cda40dbb0f906d76fd6195ebb92fc756a42aabcc37b6a3d3ee982eb5a",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00009.opus": "415f6a3c9ee7f326c1b94d42cde17ed2e70be2a551285ade64d79a45f21ba002",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00000.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00001.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00002.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00003.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00004.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00005.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00006.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00007.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00008.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00009.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00010.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00011.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00012.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00013.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00014.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00015.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00016.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00017.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00018.jpg": "6bcda3bb15903e54d8b5b81a55c80fbb6b9d3ea832f6d71427928616c987845f",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00019.jpg": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "test_data/whatsapp_export/WhatsApp Video/VID_00000.mp4": "9dd351f4cceaea310f2f96ee66831a9ea6c025280a65cee993d0414ac33cfbe8",
    "test_data/whatsapp_export/WhatsApp Video/VID_00001.mp4": "1c468793bca1f0a4d496772eea56509841528aeff0263d58b20fc42e9ac53681",
    "test_data/whatsapp_export/WhatsApp Video/VID_00002.mp4": "5fe7e8c20508bb4889ba5431bcb2433bd984f2a0e73108013f024d3dd085787a",
    "test_data/whatsapp_export/WhatsApp Video/VID_00003.mp4": "61b815ce61b901136525e3aa9619b3f21540dc210533f78492f8156cc75b59b3",
    "test_data/whatsapp_export/WhatsApp Video/VID_00004.mp4": "88c2b2d1c0c18b4138c79fbbe956caac0a9e86b8af397baf7889502e6cc64cf4",
    "test_data/whatsapp_export/msgstore.db": "cd05947aa433021c0428a0a9e51686f1aeb2c316c18aacab75c2e3f98b2d2ae0",
    "test_data/whatsapp_export/wa.db": "408ee45ef7eb347f84a507f8d505e0df18650dc264000497120f96dec0dee13e",
}

APPROVED_TEST_DATA_FIXTURES: frozenset[str] = frozenset(APPROVED_TEST_DATA_HASHES.keys())

TRUSTED_SOURCE_INVENTORY: frozenset[str] = frozenset({
    ".gitattributes",
    ".gitignore",
    ".streamlit/config.toml",
    "CHATANALYSIS_EVIDENCE_ID_LIVE_FIX_REPORT.md",
    "CHATANALYSIS_FINAL_GIT_INDEX_VERIFICATION_REPORT.md",
    "CHATANALYSIS_FINAL_GIT_READINESS_REPORT.md",
    "CHATANALYSIS_GIT_DELETION_RECONCILIATION_REPORT.md",
    "CHATANALYSIS_LOCAL_PRE_PUSH_GATE_REPORT.md",
    "CHATANALYSIS_PACKAGING_PRIVACY_FINAL_REPORT.md",
    "CHATANALYSIS_PACKAGING_PRIVATE_FILES_FINAL_REPORT.md",
    "CHATANALYSIS_PACKAGING_TRUSTED_INPUTS_REPORT.md",
    "CHATANALYSIS_REPOSITORY_CLEANUP_REPORT.md",
    "CHATANALYSIS_SCALABILITY_FINAL_CORRECTION_REPORT.md",
    "CHATANALYSIS_SCALABILITY_PACKAGING_FINAL_REPORT.md",
    "CHATANALYSIS_SCALABILITY_SAFE_OPTIMIZATION_REPORT.md",
    "CHATANALYSIS_UBUNTU_LIVE_CHUNK_FAILURE_AND_CHECKPOINT_SAFETY_REPORT.md",
    "MANIFEST.in",
    "README.md",
    "UBUNTU_FINAL_SMOKE_RUNBOOK.md",
    "UBUNTU_SMOKE_INSTRUCTIONS.md",
    "ai/__init__.py",
    "ai/backend.py",
    "ai/benchmark.py",
    "ai/chunking.py",
    "ai/cloud_experiment.py",
    "ai/cloud_test.py",
    "ai/discovery.py",
    "ai/experiment.py",
    "ai/hardware.py",
    "ai/lmstudio.py",
    "ai/models.py",
    "ai/serializer.py",
    "ai/structured.py",
    "ai/token_budget.py",
    "ai/topics.py",
    "ai/translation.py",
    "analisi_tecnica.md",
    "analysis/__init__.py",
    "analysis/checkpoint.py",
    "analysis/consistency.py",
    "analysis/models.py",
    "analysis/progress.py",
    "analysis/service.py",
    "app.py",
    "baseline_scalability_measurements.json",
    "benchmark_checkpoint_persistence_results.json",
    "benchmark_corrected_scalability_measurements.json",
    "benchmark_frozen_sha256.txt",
    "compute_manifest.py",
    "core/__init__.py",
    "core/config.py",
    "core/immutability.py",
    "dist/whatsapp_forensic_importer-0.1.0-py3-none-any.whl",
    "docs/SCALABILITY_MEASUREMENTS_REPORT.md",
    "docs/UBUNTU_LIVE_CALIBRATION_GUIDE.md",
    "docs/agent-report.md",
    "docs/ai-design.md",
    "docs/cloud-e2e-design.md",
    "docs/data-analysis.md",
    "docs/data-mapping.json",
    "docs/e2e-acceptance.md",
    "docs/entity-resolution-design.md",
    "docs/importer-design.md",
    "docs/lmstudio-benchmark-runbook.md",
    "docs/multimodal-design.md",
    "docs/normalization-design.md",
    "docs/real-ingestion-design.md",
    "docs/search-design.md",
    "docs/streamlit-design.md",
    "docs/unified-model-design.md",
    "docs/validation-design.md",
    "entity_resolution/__init__.py",
    "entity_resolution/base.py",
    "entity_resolution/models.py",
    "entity_resolution/resolver.py",
    "importer/__init__.py",
    "importer/base.py",
    "importer/cellebrite_csv.py",
    "importer/cellebrite_json.py",
    "importer/cellebrite_xml.py",
    "importer/models.py",
    "importer/whatsapp_export.py",
    "importer/whatsapp_msgstore.py",
    "importer/whatsapp_wa.py",
    "large_file_benchmark_results.json",
    "multimodal/__init__.py",
    "multimodal/asset_access.py",
    "multimodal/evidence.py",
    "multimodal/models.py",
    "multimodal/ocr.py",
    "multimodal/pipeline.py",
    "multimodal/resolver.py",
    "multimodal/transcriber.py",
    "multimodal/vision.py",
    "normalization/__init__.py",
    "normalization/base.py",
    "normalization/models.py",
    "normalization/normalizer.py",
    "normalization/phone.py",
    "optimized_scalability_measurements.json",
    "output/.gitkeep",
    "output/ai_benchmark.json",
    "output/ai_benchmark.md",
    "output/cloud_e2e/cloud-e2e-report.md",
    "output/cloud_e2e/cloud-e2e-result.json",
    "output/cloud_e2e_v2/cloud-e2e-report.md",
    "output/cloud_e2e_v2/cloud-e2e-result.json",
    "output/cloud_e2e_v3/cloud-e2e-report.md",
    "output/cloud_e2e_v3/cloud-e2e-result.json",
    "output/data_profile.json",
    "output/data_profile.md",
    "profiler/__init__.py",
    "profiler/__main__.py",
    "profiler/analyzers/__init__.py",
    "profiler/analyzers/csv_analyzer.py",
    "profiler/analyzers/file_scanner.py",
    "profiler/analyzers/json_analyzer.py",
    "profiler/analyzers/media_analyzer.py",
    "profiler/analyzers/sqlite_analyzer.py",
    "profiler/analyzers/text_analyzer.py",
    "profiler/analyzers/xml_analyzer.py",
    "profiler/config.py",
    "profiler/profiler.py",
    "profiler/report_writer.py",
    "pyproject.toml",
    "run_benchmark.sh",
    "scripts/__init__.py",
    "scripts/benchmark_checkpoint_persistence.py",
    "scripts/benchmark_large_ingestion.py",
    "scripts/create_scalability_audit_archive.py",
    "scripts/measure_scalability_and_profiling.py",
    "scripts/offline_scalability_benchmark.py",
    "scripts/run_and_log_tests_utf8.py",
    "scripts/sanitize_synthetic_dataset.py",
    "scripts/smoke_operational_ai.py",
    "search/__init__.py",
    "search/engine.py",
    "search/index.py",
    "search/models.py",
    "search/normalization.py",
    "search/service.py",
    "search/topics.py",
    "search/views.py",
    "test_data/README.md",
    "test_data/cellebrite_export/messages.csv",
    "test_data/cellebrite_export/messages.json",
    "test_data/cellebrite_export/report.xml",
    "test_data/cloud_e2e/ground_truth.json",
    "test_data/cloud_e2e/messages.json",
    "test_data/fixtures/lmstudio/get_models_multi_instance.json",
    "test_data/fixtures/lmstudio/get_models_official_qwen.json",
    "test_data/fixtures/lmstudio/get_models_unloaded.json",
    "test_data/fixtures/lmstudio/post_load_official_response.json",
    "test_data/synthetic_smoke/synthetic_whatsapp_chat.txt",
    "test_data/synthetic_smoke/synthetic_whatsapp_chat.zip",
    "test_data/synthetic_whatsapp_large_75kb.txt",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00000.opus",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00001.opus",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00002.opus",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00003.opus",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00004.opus",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00005.opus",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00006.opus",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00007.opus",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00008.opus",
    "test_data/whatsapp_export/WhatsApp Audio/AUD_00009.opus",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00000.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00001.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00002.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00003.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00004.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00005.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00006.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00007.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00008.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00009.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00010.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00011.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00012.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00013.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00014.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00015.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00016.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00017.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00018.jpg",
    "test_data/whatsapp_export/WhatsApp Images/IMG_00019.jpg",
    "test_data/whatsapp_export/WhatsApp Video/VID_00000.mp4",
    "test_data/whatsapp_export/WhatsApp Video/VID_00001.mp4",
    "test_data/whatsapp_export/WhatsApp Video/VID_00002.mp4",
    "test_data/whatsapp_export/WhatsApp Video/VID_00003.mp4",
    "test_data/whatsapp_export/WhatsApp Video/VID_00004.mp4",
    "test_data/whatsapp_export/msgstore.db",
    "test_data/whatsapp_export/wa.db",
    "tests/__init__.py",
    "tests/fixtures/__init__.py",
    "tests/fixtures/sample_records.py",
    "tests/integration/__init__.py",
    "tests/integration/test_ai_lmstudio_smoke.py",
    "tests/integration/test_cellebrite_csv_integration.py",
    "tests/integration/test_cellebrite_json_integration.py",
    "tests/integration/test_cellebrite_xml_integration.py",
    "tests/integration/test_cloud_e2e_dataset.py",
    "tests/integration/test_entity_resolution_integration.py",
    "tests/integration/test_full_pipeline_e2e.py",
    "tests/integration/test_multimodal_pipeline_integration.py",
    "tests/integration/test_normalization_integration.py",
    "tests/integration/test_ocr_pipeline_integration.py",
    "tests/integration/test_real_ingestion.py",
    "tests/integration/test_search_integration.py",
    "tests/integration/test_unified_pipeline_integration.py",
    "tests/integration/test_validation_integration.py",
    "tests/integration/test_whatsapp_msgstore_integration.py",
    "tests/integration/test_whatsapp_wa_integration.py",
    "tests/smoke/__init__.py",
    "tests/smoke/test_whisper_smoke.py",
    "tests/unit/__init__.py",
    "tests/unit/test_ai_benchmark.py",
    "tests/unit/test_ai_chunking.py",
    "tests/unit/test_ai_cloud_experiment.py",
    "tests/unit/test_ai_cloud_test.py",
    "tests/unit/test_ai_detection_multimodal_diagnostics.py",
    "tests/unit/test_ai_experiment.py",
    "tests/unit/test_ai_hardware.py",
    "tests/unit/test_ai_lmstudio.py",
    "tests/unit/test_ai_models.py",
    "tests/unit/test_ai_prompt_injection.py",
    "tests/unit/test_ai_serializer.py",
    "tests/unit/test_ai_structured.py",
    "tests/unit/test_ai_topic_detection.py",
    "tests/unit/test_ai_topic_discovery.py",
    "tests/unit/test_ai_translation.py",
    "tests/unit/test_analysis_service.py",
    "tests/unit/test_apptest_flows.py",
    "tests/unit/test_architecture_guard.py",
    "tests/unit/test_audio_transcriber.py",
    "tests/unit/test_base_importer.py",
    "tests/unit/test_cellebrite_csv_importer.py",
    "tests/unit/test_cellebrite_json_importer.py",
    "tests/unit/test_cellebrite_xml_importer.py",
    "tests/unit/test_checkpoint_integrity_scalability.py",
    "tests/unit/test_deep_immutability.py",
    "tests/unit/test_discovery_detection_invariants.py",
    "tests/unit/test_discovery_provenance_hardening.py",
    "tests/unit/test_discovery_provenance_service.py",
    "tests/unit/test_entity_resolution_models.py",
    "tests/unit/test_entity_resolver.py",
    "tests/unit/test_image_ocr_models.py",
    "tests/unit/test_image_text_extractor.py",
    "tests/unit/test_image_vision.py",
    "tests/unit/test_integrated_audit_fixes.py",
    "tests/unit/test_large_file_upload.py",
    "tests/unit/test_large_pipeline_bounded.py",
    "tests/unit/test_lmstudio_binding_lifecycle.py",
    "tests/unit/test_lmstudio_model_loading.py",
    "tests/unit/test_lmstudio_native_integration.py",
    "tests/unit/test_media_asset_access.py",
    "tests/unit/test_media_processing_lifecycle.py",
    "tests/unit/test_media_registry_session_isolation.py",
    "tests/unit/test_media_resolver.py",
    "tests/unit/test_model_resolution_audit.py",
    "tests/unit/test_multimodal_evidence.py",
    "tests/unit/test_normalization_models.py",
    "tests/unit/test_normalizer.py",
    "tests/unit/test_operational_evidence_alias_and_live_recovery.py",
    "tests/unit/test_operational_integration_audit.py",
    "tests/unit/test_operational_model_management.py",
    "tests/unit/test_packaging_release.py",
    "tests/unit/test_performance_accounting_audit.py",
    "tests/unit/test_phone_canonicalization.py",
    "tests/unit/test_raw_record.py",
    "tests/unit/test_realworld_topic_improvements.py",
    "tests/unit/test_sanitize_dataset.py",
    "tests/unit/test_scalability_safe_optimizations.py",
    "tests/unit/test_search_engine.py",
    "tests/unit/test_search_index.py",
    "tests/unit/test_search_models.py",
    "tests/unit/test_search_normalization.py",
    "tests/unit/test_search_service.py",
    "tests/unit/test_search_topics.py",
    "tests/unit/test_search_views.py",
    "tests/unit/test_smoke.py",
    "tests/unit/test_smoke_operational_ai.py",
    "tests/unit/test_staging_and_large_file.py",
    "tests/unit/test_subchunk_split_recovery_and_coverage.py",
    "tests/unit/test_ubuntu_live_chunk_failure_and_recovery.py",
    "tests/unit/test_ui_application.py",
    "tests/unit/test_ui_demo.py",
    "tests/unit/test_ui_ingestion.py",
    "tests/unit/test_ui_manual_topic_detection.py",
    "tests/unit/test_ui_models.py",
    "tests/unit/test_ui_privacy_hardening.py",
    "tests/unit/test_ui_smoke.py",
    "tests/unit/test_ui_state.py",
    "tests/unit/test_unified_builder.py",
    "tests/unit/test_unified_models.py",
    "tests/unit/test_validation_models.py",
    "tests/unit/test_validator.py",
    "tests/unit/test_whatsapp_export_importer.py",
    "tests/unit/test_whatsapp_msgstore_importer.py",
    "tests/unit/test_whatsapp_strict_encoding.py",
    "tests/unit/test_whatsapp_wa_importer.py",
    "tests/unit/test_zip_security_hardening.py",
    "ui/__init__.py",
    "ui/application.py",
    "ui/demo.py",
    "ui/ingestion.py",
    "ui/models.py",
    "ui/presentation.py",
    "ui/staging.py",
    "ui/state.py",
    "unified/__init__.py",
    "unified/builder.py",
    "unified/context.py",
    "unified/models.py",
    "validation/__init__.py",
    "validation/base.py",
    "validation/models.py",
    "validation/validator.py",
})


DATABASE_EXTENSIONS: frozenset[str] = frozenset({
    ".db", ".sqlite", ".sqlite3", ".db3", ".s3db", ".sl3",
    ".wal", ".shm", ".journal",
})

DATABASE_SIDECAR_SUFFIXES: tuple[str, ...] = (
    "-wal", "-shm", "-journal",
)

SQLITE3_MAGIC_HEADER: bytes = b"SQLite format 3\x00"

EXCLUDE_DIRS: frozenset[str] = frozenset({
    ".git", ".pytest_cache", "__pycache__", ".venv", "venv", "env",
    ".idea", ".vscode", ".checkpoints", "build", "ubuntu_smoke_gate_bundle"
})

EXCLUDE_EXTS: frozenset[str] = frozenset({".pyc", ".pyo", ".pyd", ".log"})

EXCLUDE_FILENAMES: frozenset[str] = frozenset({
    "MANIFEST_SHA256.txt",
    "MANIFEST.txt",
})

# File sensibili, credenziali e configurazioni da escludere ovunque
SENSITIVE_EXACT_FILENAMES: frozenset[str] = frozenset({
    ".env",
    "credentials.json", "credentials.yaml", "credentials.yml", "credentials.toml",
    "secrets.json", "secrets.yaml", "secrets.yml", "secrets.toml",
    "token.json", "auth.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    ".ds_store", "thumbs.db",
})

SENSITIVE_PREFIXES: tuple[str, ...] = (
    ".env.",
    "private_key",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials.",
    "secrets.",
)

ALLOWED_PUBLIC_TEMPLATES: frozenset[str] = frozenset({
    ".env.example",
    ".env.template",
    ".env.sample",
})

SENSITIVE_KEY_CERT_EXTENSIONS: frozenset[str] = frozenset({
    ".pem", ".key", ".pkcs12", ".pfx", ".p12", ".crt", ".cer",
})

TEMPORARY_BACKUP_EXTENSIONS: frozenset[str] = frozenset({
    ".bak", ".orig", ".save", ".swp", ".swo",
})


def has_sqlite_signature(fpath: Path) -> bool:
    """Rileva se un file possiede la firma magica standard di un database SQLite 3."""
    try:
        if fpath.is_symlink() or not fpath.is_file():
            return False
        if fpath.stat().st_size >= 16:
            with open(fpath, "rb") as f:
                header = f.read(16)
                return header == SQLITE3_MAGIC_HEADER
    except (OSError, PermissionError):
        pass
    return False


def is_database_or_sidecar_or_zip(fpath: Path, file_name: str, file_suffix: str) -> bool:
    """Verifica se il file è un database, un file laterale SQLite o un archivio zip."""
    lower_suffix = file_suffix.lower()
    lower_name = file_name.lower()

    if lower_suffix in DATABASE_EXTENSIONS or lower_suffix == ".zip":
        return True
    if any(lower_name.endswith(sidecar) for sidecar in DATABASE_SIDECAR_SUFFIXES):
        return True
    if has_sqlite_signature(fpath):
        return True
    return False


def is_sensitive_config_or_key(file_name: str, file_suffix: str) -> bool:
    """Identifica file sensibili (.env, chiavi, token, credenziali, certificati)."""
    lower_name = file_name.lower()
    lower_suffix = file_suffix.lower()

    # Template pubblici verificati sono sempre ammessi
    if lower_name in ALLOWED_PUBLIC_TEMPLATES:
        return False

    # .env o file sensibili esatti
    if lower_name in SENSITIVE_EXACT_FILENAMES:
        return True

    # Prefissi sensibili (.env.*, private_key*, credentials.*, etc.)
    if any(lower_name.startswith(p) for p in SENSITIVE_PREFIXES):
        return True

    # Chiavi private e certificati
    if lower_suffix in SENSITIVE_KEY_CERT_EXTENSIONS:
        return True

    # File di backup o temporanei
    if lower_suffix in TEMPORARY_BACKUP_EXTENSIONS or lower_name.endswith("~"):
        return True

    return False


def verify_test_data_fixture(fpath: Path, rel_str: str, baseline_hashes: dict[str, str]) -> bool:
    """Verifica che la fixture sotto test_data/ sia registrata e il suo hash corrisponda alla baseline."""
    if rel_str not in baseline_hashes:
        return False
    expected_hash = baseline_hashes[rel_str]
    try:
        actual_hash = hashlib.sha256(fpath.read_bytes()).hexdigest()
        return actual_hash == expected_hash
    except (OSError, PermissionError):
        return False


def should_pack_file(
    fpath: Path,
    rel_str: str,
    file_name: str,
    file_suffix: str,
    repo_root_resolved: Path,
    flagged_excluded: list[str] | None = None,
    trusted_inventory: frozenset[str] | set[str] | None = None,
    fixture_baseline_hashes: dict[str, str] | None = None,
) -> bool:
    """Verifica con guardrail rigidi di identità e privacy se un file debba essere incluso."""
    # 1. Rifiuto tassativo di qualsiasi collegamento simbolico
    if fpath.is_symlink():
        if flagged_excluded is not None:
            flagged_excluded.append(rel_str)
        return False

    # 2. Rifiuto di file che puntano o si risolvono all'esterno della radice del repository
    try:
        resolved = fpath.resolve()
        if not resolved.is_relative_to(repo_root_resolved):
            if flagged_excluded is not None:
                flagged_excluded.append(rel_str)
            return False
    except (OSError, ValueError):
        if flagged_excluded is not None:
            flagged_excluded.append(rel_str)
        return False

    # 3. Esclusione manifest e checksum dalla scansione
    if file_name in EXCLUDE_FILENAMES or file_name.endswith(".sha256"):
        return False

    # 4. Esclusione estensioni compilate / log
    if file_suffix in EXCLUDE_EXTS:
        return False

    # 5. Esclusione tassativa di file di configurazione sensibili, .env, chiavi e certificati
    if is_sensitive_config_or_key(file_name, file_suffix):
        if flagged_excluded is not None:
            flagged_excluded.append(rel_str)
        return False

    # 6. Verifica inventario esplicito dei percorsi ammessi nel pacchetto sorgente
    # Nessun file sconosciuto con nome ordinario viene incluso automaticamente
    inventory = trusted_inventory if trusted_inventory is not None else TRUSTED_SOURCE_INVENTORY
    if rel_str not in inventory:
        if flagged_excluded is not None:
            flagged_excluded.append(rel_str)
        return False

    # 7. Verifica identità e integrità SHA-256 delle fixture sintetiche rispetto alla baseline attendibile
    # Se il contenuto viene sostituito o alterato a parità di percorso, viene respinto
    if rel_str.startswith("test_data/") or rel_str == "test_data":
        baseline_hashes = fixture_baseline_hashes if fixture_baseline_hashes is not None else APPROVED_TEST_DATA_HASHES
        if not verify_test_data_fixture(fpath, rel_str, baseline_hashes):
            if flagged_excluded is not None:
                flagged_excluded.append(rel_str)
            return False

    # 8. Guardrail per DB, sidecar SQLite, ZIP e file con firma SQLite:
    # Ammessi SOLO ED ESCLUSIVAMENTE se presenti nell'allowlist approvata
    if is_database_or_sidecar_or_zip(fpath, file_name, file_suffix):
        if rel_str not in APPROVED_SYNTHETIC_FIXTURES:
            if flagged_excluded is not None:
                flagged_excluded.append(rel_str)
            return False

    return True


def create_archive(
    repo_root: Path,
    dest_zip: Path,
    dest_zip_sha: Path | None = None,
    target_zip_name: str | None = None,
    flagged_excluded: list[str] | None = None,
    trusted_inventory: frozenset[str] | set[str] | None = None,
    fixture_baseline_hashes: dict[str, str] | None = None,
) -> tuple[Path, str, int, list[str]]:
    """
    Costruisce l'archivio ZIP verificando l'inventario attendibile dei sorgenti
    e l'identità SHA256 delle fixture rispetto alla baseline.
    Restituisce (dest_zip, zip_sha256, numero_file_totali, lista_rel_path).
    """
    if target_zip_name is None:
        target_zip_name = dest_zip.name

    repo_root_resolved = repo_root.resolve()
    files_to_pack: list[tuple[Path, Path, str]] = []

    for root, dirs, files in os.walk(repo_root, followlinks=False):
        # Filtra directory escluse, egg-info e collegamenti simbolici di directory
        dirs[:] = [
            d for d in dirs
            if d not in EXCLUDE_DIRS
            and not d.endswith(".egg-info")
            and not (Path(root) / d).is_symlink()
        ]
        for file in files:
            fpath = Path(root) / file
            relpath = fpath.relative_to(repo_root)
            rel_str = str(relpath).replace("\\", "/")

            if any(part in EXCLUDE_DIRS for part in relpath.parts):
                continue
            if any(part.endswith(".egg-info") for part in relpath.parts):
                continue

            if not should_pack_file(
                fpath=fpath,
                rel_str=rel_str,
                file_name=file,
                file_suffix=fpath.suffix,
                repo_root_resolved=repo_root_resolved,
                flagged_excluded=flagged_excluded,
                trusted_inventory=trusted_inventory,
                fixture_baseline_hashes=fixture_baseline_hashes,
            ):
                continue

            files_to_pack.append((fpath, relpath, rel_str))

    # 1. Calcola hash SHA-256 dei singoli file ordinati alfabeticamente
    manifest_entries: list[tuple[str, str]] = []
    for fpath, relpath, rel_str in sorted(files_to_pack, key=lambda x: x[2]):
        h = hashlib.sha256()
        with open(fpath, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        manifest_entries.append((h.hexdigest(), rel_str))

    # 2. Genera il manifest deterministico (che NON elenca MANIFEST_SHA256.txt)
    manifest_lines = [
        f"# SHA256 MANIFEST - CHATANALYSIS PACKAGING TRUSTED INPUTS AUDIT",
        f"# ARCHIVE: {target_zip_name}",
        f"# TOTAL FILES: {len(manifest_entries)}",
        "",
    ]
    for sha, rel in manifest_entries:
        manifest_lines.append(f"{sha}  {rel}")
    manifest_content = "\n".join(manifest_lines) + "\n"

    # 3. Scrivi lo ZIP assicurando che MANIFEST_SHA256.txt sia aggiunto UNA SOLA VOLTA
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for fpath, relpath, rel_str in files_to_pack:
            z.write(fpath, arcname=rel_str)
        z.writestr("MANIFEST_SHA256.txt", manifest_content)

    # 4. Calcola hash dello ZIP generato
    zip_h = hashlib.sha256()
    with open(dest_zip, "rb") as f:
        while chunk := f.read(65536):
            zip_h.update(chunk)
    zip_sha256 = zip_h.hexdigest()

    # 5. Salva l'hash dello ZIP su file separato se richiesto
    if dest_zip_sha is not None:
        dest_zip_sha.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_zip_sha, "w", encoding="utf-8") as f_sha:
            f_sha.write(f"{zip_sha256}  {target_zip_name}\n")

    return dest_zip, zip_sha256, len(files_to_pack) + 1, [x[2] for x in files_to_pack]


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    target_zip_name = "chatAnalysis_packaging_trusted_inputs_audit_20261017.zip"
    dest_zip = repo_root.parent / target_zip_name
    dest_zip_sha = repo_root.parent / f"{target_zip_name}.sha256"

    flagged_excluded: list[str] = []
    dest_zip, zip_sha256, total_count, _ = create_archive(
        repo_root=repo_root,
        dest_zip=dest_zip,
        dest_zip_sha=dest_zip_sha,
        target_zip_name=target_zip_name,
        flagged_excluded=flagged_excluded,
    )

    print(f"Archive created: {dest_zip} ({dest_zip.stat().st_size:,} bytes, {total_count} files)")
    print(f"ZIP SHA256: {zip_sha256}")
    print(f"ZIP SHA256 file: {dest_zip_sha}")
    if flagged_excluded:
        print(f"[GUARDRAIL PRIVACY] {len(flagged_excluded)} file esclusi da verifiche manuali/automatiche:")
        for p in flagged_excluded:
            print(f"  - {p}")


if __name__ == "__main__":
    main()
