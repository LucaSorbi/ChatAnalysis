# CHATANALYSIS CURRENT STATE AUDIT
**Forensic & Architectural Audit Report**  
**Date:** 2026-10-09  
**Target Repository:** `c:\Users\lucas\Desktop\Tesi` (`ChatAnalysis`)  
**Scope:** Strictly read-only forensic inspection of current repository state, code architecture, test suite, and operational pathways. No code modifications, refactorings, or benchmark modifications applied.

---

## 1. Repository State

- **Commit HEAD:** `b3765a4d210293604d684299f472ecb08deae9e5`
- **Branch:** `main`
- **Working Tree:** `DIRTY` (Uncommitted modifications present on 3 files prior to audit start, fully preserved without modification):
  - `ai/topics.py` (working tree modification in prompt directives / typing)
  - `docs/agent-report.md` (uncommitted documentation update)
  - `tests/unit/test_ai_detection_multimodal_diagnostics.py` (uncommitted diagnostic test)
- **Untracked files:** `audit_bundle/` (audit diagnostic artifacts created strictly within the authorized sandbox).

Full git state capture: See [`00_git_state.txt`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/00_git_state.txt).

---

## 2. Pytest Execution

- **Full Suite Command:** `python -m pytest`
- **Execution Summary:**
  - **Passed:** `1304`
  - **Failed:** `0`
  - **Errors:** `0`
  - **Skipped:** `1` (`tests/unit/test_ai_benchmark_gpu.py:10` - Hardware AMD A8 / AVX2 / GPU conditional skip)
  - **Deselected:** `5` (Targeted hardware/benchmark filters)
  - **Total Duration:** `75.56s`
- **Targeted Test Suites:** 11/11 executed targeted suites passed with 0 failures (see [`13_targeted_tests.txt`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/13_targeted_tests.txt)).
- **Critical Finding on Test Health:** While the test suite passes with 1304 green tests, high test coverage is heavily sustained by **mocked LLM clients** (`FakeLmStudioClient`, unittest mocks). Real operational edge cases (live LM Studio timeouts, prompt drift, context window overflow, session state wipe upon switching chats) are largely unexercised by existing unit tests.

Full test log: See [`12_pytest_full.txt`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/12_pytest_full.txt).

---

## 3. Pipeline Current State

The data processing pipeline follows the theoretical architecture:
```
Raw File Upload -> IngestionRequest -> ingest_file_payload() -> Importer.parse()
  -> RawRecord -> validate_records() -> normalize_records() 
  -> resolve_entities() -> UnifiedMessage -> build_evidence_bundles() 
  -> ConversationEvidenceDocument -> SessionState / SearchService
```
**Current Operational Status:**
1. **Deterministic Ingestion:** Completely free of automatic AI invocation (`AI CALL: NO`). Safe, reproducible, and verifiable.
2. **Provenance & Immutability:** `ConversationEvidenceDocument`, `MessageEvidenceBundle`, and `TextEvidenceSection` maintain strict provenance IDs (`doc_id`, `bundle_id`, `section_id`).
3. **Multimodal Derivation Gap:** Real file importers currently only populate `ORIGINAL_TEXT`. Multimodal evidence sections (`VISION_DESCRIPTION`, `OCR_TEXT`, `STT_TRANSCRIPTION`) are defined in schema but only populated in synthetic demo fixtures (`ui/demo.py`) or dedicated pipeline tests.

Full pipeline trace: See [`03_file_mode_topic_flow.md`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/03_file_mode_topic_flow.md).

---

## 4. Real Ingestion by Format

| Format | SourceFormat Enum | UI Support | Importer Class | Validation / Norm / Entity Resolution | Pipeline Output |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **WhatsApp TXT Export** | `WHATSAPP_EXPORT` | Yes (`.txt`) | `WhatsAppExportImporter` | Full pipeline active | `ConversationEvidenceDocument` |
| **WhatsApp ZIP Export** | `WHATSAPP_EXPORT` | Yes (`.zip`) | `WhatsAppExportImporter` | Full pipeline + Zip Slip defense | `ConversationEvidenceDocument` |
| **WhatsApp msgstore.db**| `WHATSAPP_SQLITE` | Yes (`.db,.sqlite`) | `WhatsAppMsgstoreImporter` | Full pipeline active | `ConversationEvidenceDocument` |
| **WhatsApp wa.db**      | `WHATSAPP_WA`     | Yes (`.db,.sqlite`) | `WhatsAppWaImporter` | Full pipeline active | Contact / Profile enrichment |
| **Cellebrite CSV**      | `CELLEBRITE_CSV`  | Yes (`.csv`) | `CellebriteCsvImporter` | Full pipeline active | `ConversationEvidenceDocument` |
| **Cellebrite JSON**     | `CELLEBRITE_JSON` | Yes (`.json`) | `CellebriteJsonImporter`| Full pipeline active | `ConversationEvidenceDocument` |
| **Cellebrite XML**      | `CELLEBRITE_XML`  | Yes (`.xml`) | `CellebriteXmlImporter` | Full pipeline active | `ConversationEvidenceDocument` |

Full ingestion audit: See [`09_real_ingestion_audit.md`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/09_real_ingestion_audit.md).

---

## 5. Large Files & Upload Limits

- **Configured Limit:** **2048 MB (2.0 GB)**
  - `.streamlit/config.toml`: `server.maxUploadSize = 2048`, `server.maxMessageSize = 2048`
  - `core/config.py`: `DEFAULT_MAX_FILE_SIZE_MB = 2048`
  - `ui/ingestion.py`: `st.file_uploader(..., max_upload_size=2048)`
- **Streaming & Memory Efficiency:**
  - Ingestion payload uses 1 MB streaming chunks (`chunk_size=1024*1024`) for SHA-256 calculation and writing to temporary disk files.
  - Zero arbitrary `.read()` or `.getvalue()` of entire multi-gigabyte payloads in `ui/ingestion.py`.
- **ZIP Limits & Safety:**
  - `MAX_ARCHIVE_UNCOMPRESSED_SIZE_MB = 4096 MB` (4 GB)
  - `MAX_ARCHIVE_MEMBERS = 50,000`
  - `MAX_COMPRESSION_RATIO = 100.0`
  - Strict path traversal defenses (`os.path.isabs`, `..`, Windows reserved names).

Full large file audit: See [`08_large_file_audit.md`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/08_large_file_audit.md).

---

## 6. DatasetMode.DEMO Real Behavior

- **Document Creation:** Pre-populated synthetic forensic dataset (`doc::demo_forensic_chat`, 6 bundles, 9 evidence sections, 2 chat participants).
- **Topic Detection:** Pre-calculated in `ui/demo.py:270-329` (5 detection queries pre-computed with synthetic labels and rationales). Manual live re-run available via UI button.
- **Topic Discovery:** **100% HARDCODED / PRECALCULATED** in `ui/demo.py:330-345`. Live discovery analyzer is **never invoked** when loading DEMO.
- **Verification Workflow:** "Verifica con Topic Detection" triggers a live invocation of `TopicDetectionAnalyzer` (or mock client in tests) using the discovered topic's label and description.

Full demo audit: See [`05_demo_case_analysis.md`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/05_demo_case_analysis.md).

---

## 7. DatasetMode.FILE Real Behavior

- **Document Creation:** Generated dynamically from real user uploads via `ingest_file_payload()`.
- **Topic Detection:**
  - Ingestion initializes `state.set_detection_results(())` (empty tuple).
  - Manual Topic Detection UI exists in `ui/presentation.py:465-515` allowing custom query input.
  - **Obsolete Banner Issue:** If no detection results exist yet, `ui/presentation.py:536-547` displays the obsolete banner: *"Il benchmark/inference con modelli locali è differito alla fase sperimentale finale"*, giving the false impression that analysis is blocked.
- **Topic Discovery:** **COMPLETELY ABSENT**. No UI button, trigger, or background call exists to invoke `TopicDiscoveryAnalyzer` on real uploaded files.
- **Discovery -> Detection:** **UNAVAILABLE** because discovery results are always empty on FILE mode.

Full file flow audit: See [`03_file_mode_topic_flow.md`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/03_file_mode_topic_flow.md).

---

## 8. Topic Detection DEMO: Live / Fake / Precalculated

- **Initial State:** Precalculated via `ui/demo.py:demo_topic_detection_results()`.
- **Interactive State:** If the user triggers "Avvia Analisi Tematica (Topic Detection)" in DEMO, a live inference is performed via `OperationalModelManager` and `TopicDetectionAnalyzer` (if LM Studio is running) or falls back to error if offline.

---

## 9. Topic Detection FILE: Available / Blocked

- **Interactive Manual Detection:** Operationally available via the manual query form in `ui/presentation.py:465-515`.
- **Pre-computed Detection:** None (correct, as real files must not undergo automatic AI ingestion).
- **Visual Block:** Visually obstructed by the obsolete warning banner at `ui/presentation.py:544` when results tuple is empty.

---

## 10. Topic Discovery DEMO: Live / Hardcoded / Both

- **Current State:** **100% HARDCODED / PRECALCULATED**.
- `ui/demo.py:330-345` hardcodes exactly 2 topics:
  1. `"Logistica e Ispezione Depositi"` (references `msg::5::VISION_DESCRIPTION`)
  2. `"Gestione Contratti e Bozze"` (references `msg::2::ORIGINAL_TEXT`, `msg::4::ORIGINAL_TEXT`)
- There is no option in the UI to run *live* Topic Discovery on DEMO.

---

## 11. Topic Discovery FILE: Available / Blocked

- **Current State:** **COMPLETELY BLOCKED / UNIMPLEMENTED IN UI**.
- `TopicDiscoveryAnalyzer` exists in `ai/topics.py:347-493` with full prompt engineering and structured schema, but **no function in `ui/presentation.py`, `ui/application.py`, or `ui/ingestion.py` ever instantiates or calls it for real files**.
- As a consequence, `state.get_discovery_results()` is permanently empty for `DatasetMode.FILE`.

---

## 12. Discovery -> Detection Workflow

When the user clicks `"Verifica con Topic Detection"` in `ui/presentation.py:657-679`:
1. **Query Construction:** Builds `TopicQuery(topic_id=f"verify_{discovered.topic_id}", label=discovered.label, description=discovered.short_description)`.
2. **Document Association:** Retrieves active document via `state.get_active_document(session_state)`.
3. **Independent Inference:** Calls `OperationalModelManager.run_topic_detection(active_doc, [query])`. This performs a genuine, independent LLM chat completion call.
4. **No Cross-Contamination:** The discovery evidence IDs are **NOT** passed to the detection prompt. Detection must locate evidence independently.
5. **Consistency Evaluation:** Evaluates agreement between discovery and detection.

Full discovery-detection audit: See [`04_discovery_detection_flow.md`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/04_discovery_detection_flow.md).

---

## 13. "Prova del Nove" Authenticity

- **Is it a genuine independent inference?** **YES**. The code constructs a fresh `TopicQuery` containing only the label and description, sending the entire document text sections to the LLM without hinting which evidence IDs Discovery found.
- **Is it hardcoded or converted?** **NO**. It invokes `TopicDetectionAnalyzer.detect_topic()` directly against the backend.
- **Weakness in Consistency Logic:** In `ui/presentation.py:680-683`, consistency is checked strictly as:
  ```python
  status = "CONSISTENT" if detection_result.decision == TopicDecision.PRESENT else "DISCREPANCY"
  ```
  It does not verify whether the evidence IDs found by Detection overlap with the evidence IDs discovered by Discovery.

---

## 14. Unexpected ABSENT Root Cause Analysis

In the reported failure where `"Logistica e Ispezione Depositi"` returned `ABSENT`:
1. **Semantic Context:** Synthetic evidence `msg::5::VISION_DESCRIPTION` states:
   > *"Foto scattata all'interno di un magazzino industriale con scaffalature in metallo, diverse casse di legno sigillate con etichette visibili e una porta di sicurezza sullo sfondo."*
2. **Operational Prompt Directive:** `ai/topics.py:157-190` specifies:
   - Topic grounding requires unambiguous explicit affirmation.
   - If evidence is merely tangential or descriptive without conversational intent, the model may conservatively assign `ABSENT`.
3. **Schema / Validation Failure Mapping:** In `ai/topics.py:270-320`, if the model returns unparseable JSON or invalid evidence IDs, does it produce ABSENT?
   - In `TopicDetectionAnalyzer.detect_topic()`, schema errors raise `TopicDetectionError` / `ValueError` rather than silently converting to `ABSENT`.
   - Therefore, `ABSENT` was an actual semantic decision rendered by Qwen2.5-7B-Instruct under the prompt instructions, or caused by prompt phrasing that prioritized explicit message dialogue over passive scene descriptions.

---

## 15. Serializer Evidence Coverage

`serialize_document_for_llm` in `ai/serializer.py:27-77`:
- Iterates over `bundle.evidence_sections` and includes:
  - `ORIGINAL_TEXT`
  - `STT_TRANSCRIPTION`
  - `OCR_TEXT`
  - `VISION_DESCRIPTION`
  - `VISION_OBSERVATION`
- Formats each section with `[EVIDENCE_ID: {section.section_id}] [{section.source_type.value}] {section.text_content}`.
- **Verdict:** The serializer correctly includes all 5 text evidence source types without filtering.

Full serializer audit: See [`06_serializer_audit.md`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/06_serializer_audit.md).

---

## 16. Session State Persistence & Fragility

- 17 session state keys tracked in `ui/state.py`.
- **Critical Fragility:**
  - Topic detection results are stored in a single flat tuple `st.session_state["detection_results"]`.
  - When a user uploads a new file or switches conversations, this tuple is cleared.
  - Multi-page navigation in Streamlit retains state, but browser refreshes (F5) wipe all non-persisted in-memory analysis results.

Full session state audit: See [`10b_session_state_audit.md`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/10b_session_state_audit.md).

---

## 17. Multi-Conversation Isolation & Cross-Contamination

- In `ui/state.py:314-325` (`select_conversation()`):
  ```python
  target[KEY_ACTIVE_CONVERSATION_ID] = conversation_id
  target[KEY_ACTIVE_DOCUMENT] = doc
  target[KEY_DETECTION_RESULTS] = () # <--- BUG: Wipes detection results completely!
  target[KEY_SEARCH_SERVICE] = SearchService(doc)
  ```
- **Architectural Defect:** Detection results are **not scoped per `document_id`**. Switching between chats wipes the detection results of the previous chat. If not reset, it would cause cross-contamination. Scoping per document is urgently needed.

Full multi-conversation audit: See [`10_multiconversation_state.md`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/10_multiconversation_state.md).

---

## 18. Context Window & Large Chats

- **Current Implementation:**
  - `ai/chunking.py` exists with `split_document_into_chunks(doc, max_tokens, overlap)`.
  - **Orphaned Module:** Neither `TopicDetectionAnalyzer` nor `TopicDiscoveryAnalyzer` imports or calls `split_document_into_chunks`.
  - The entire `ConversationEvidenceDocument` is serialized into a single string and passed directly to the LLM.
- **Consequence for Real Chats (>500 messages / >8192 tokens):**
  - LM Studio returns `HTTP 400 Context window exceeded` or truncates tokens silently based on LM Studio server configuration.
  - **No intelligent multi-chunk map-reduce or hierarchical aggregation exists in the operational AI layer.**

Full chunking audit: See [`11_context_chunking_audit.md`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/11_context_chunking_audit.md).

---

## 19. LM Studio Client State

- **Transport:** HTTP REST API on `http://127.0.0.1:1234`.
- **Loopback Validation:** Strict enforcement of localhost IP / loopback in `ai/lmstudio.py:100-117`. Remote hosts and auto-downloading are rejected.
- **Model Load Payload (POST `/api/v1/models/load`):**
  - Cleaned in `ai/lmstudio.py:537-542`.
  - Contains strictly: `{"model": ..., "context_length": ..., "echo_load_config": ...}`.
  - Legacy forbidden keys (`identifier`, `contextLength`, `gpu_offload`, `gpuOffload`) have been completely removed from active code.
- **Live Server Availability:** Port 1234 is currently closed (`ConnectionRefusedError: WinError 10061`). See [`15_live_lmstudio_status.txt`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/15_live_lmstudio_status.txt).

Full LM Studio audit: See [`07_lmstudio_audit.md`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/07_lmstudio_audit.md).

---

## 20. Obsolete Guards Inventory

Full scan for obsolete patterns (`DEFERRED`, `differito`, `Hardware Block`, `fase sperimentale finale`, etc.) revealed 68 occurrences in the codebase:
1. `ui/presentation.py:544`: Obsolete warning banner displayed on real file mode.
2. `ai/experiment.py:76-80`: Frozen benchmark guards (`BENCHMARK_FROZEN_GUARD`, `DEFERRED_EXPERIMENT_TOKEN`).
3. `tests/unit/test_ai_benchmark_gpu.py:10`: Legacy hardware check skip.
4. Legacy comments and test assertions referencing historical phases.

Full inventory: See [`02_obsolete_guards_search.txt`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/02_obsolete_guards_search.txt).

---

## 21. Benchmark Integrity

The official AI benchmark suite remains completely untouched and frozen:
- `ai/experiment.py`: Untouched (`SHA256: e8d4a9463b384ca4a38f4be2b05615707bb88f62fae804f514d79df01aa2372c`)
- `test_data/ai_benchmark/`: Untouched
- `run_benchmark.sh`: Untouched (`SHA256: a4aa5ef062776c5b966f91811e74b5c777e38ba85b0d0c3fe5df31495c65f299`)
- Complete separation between operational UI inference and benchmark evaluation is preserved.

Full benchmark audit: See [`16_benchmark_integrity.md`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/16_benchmark_integrity.md).

---

## 22. Architectural Debt Evaluation

| Architectural Area | Classification | Root Justification |
| :--- | :--- | :--- |
| **`ui/presentation.py`** | **REWRITE** | Massive monolithic file (800+ lines) mixing layout rendering, session state mutations, AI orchestration, obsolete banner logic, and consistency checking. |
| **`ui/application.py`** | **REFACTOR** | Mixes UI controllers with business logic; lacks dedicated discovery orchestration. |
| **`ui/state.py`** | **REFACTOR** | Centralizes keys well, but lacks conversation-scoped result storage, wiping analysis results when switching chats. |
| **`ui/ingestion.py`** | **KEEP** | Clean streaming upload, size enforcement, and safe temporary file lifecycle. |
| **`ai/lmstudio.py`** | **KEEP** | Robust loopback enforcement, clean v1 REST load payload, clear error mappings. |
| **`ai/topics.py`** | **REFACTOR** | Strong schemas and prompts, but needs unified operational service layer and proper integration with chunking. |
| **`ai/serializer.py`** | **KEEP** | Deterministic, cleanly handles all 5 evidence sources. |
| **`ai/chunking.py`** | **REFACTOR** | Core chunking algorithm is sound, but completely orphaned; must be integrated into analyzers. |
| **SearchService Integration** | **KEEP** | Clean, fast, deterministic search over evidence sections. |
| **Discovery -> Detection Flow** | **REFACTOR** | Core LLM flow is authentic, but lacks multi-evidence consistency verification and is unavailable in FILE mode. |
| **Multi-Conversation Handling** | **REFACTOR** | Must scope AI analysis results by `document_id`. |
| **Large-File Ingestion Pipeline** | **KEEP** | Importers, normalizers, and validators are mature, robust, and clean. |

Full architectural debt analysis: See [`17_architectural_debt.md`](file:///c:/Users/lucas/Desktop/Tesi/audit_bundle/17_architectural_debt.md).

---

## 23. Catalog of Identified Defects & Vulnerabilities

### ISSUE-01: Obsolete "Differito alla fase sperimentale finale" Banner in FILE Mode
- **ID:** `ISSUE-01`
- **Severity:** `HIGH`
- **Component:** UI Presentation
- **File:** `ui/presentation.py`
- **Lines:** 536–547
- **Description:** An obsolete `if` block checks if `detection_results` is empty under `DatasetMode.FILE`, and displays an error banner stating that inference with local models is deferred to the experimental phase.
- **Observed Effect:** When a user imports a real file, entering "Analisi Tematica" displays an alarming message claiming AI analysis is blocked.
- **Root Cause Evidence:** Legacy leftover from an earlier phase before live local model inference was enabled.
- **Current Test Coverage:** Covered by tests verifying the banner appears when empty (`tests/unit/test_ui_smoke.py`).
- **Missing Test:** Test verifying that FILE mode provides a clear call-to-action to run topic analysis without legacy warnings.
- **Architectural Classification:** `LOCAL FIX` (remove obsolete block).

---

### ISSUE-02: Topic Discovery in FILE Mode is Completely Unimplemented in UI
- **ID:** `ISSUE-02`
- **Severity:** `CRITICAL`
- **Component:** UI Presentation / Application Orchestrator
- **File:** `ui/presentation.py`, `ui/application.py`
- **Lines:** `ui/presentation.py:550-685`
- **Description:** `TopicDiscoveryAnalyzer` exists in `ai/topics.py`, but neither `ui/application.py` nor `ui/presentation.py` provides a button or workflow to trigger live Topic Discovery on uploaded files.
- **Observed Effect:** Topic Discovery is completely impossible on real imported conversations.
- **Root Cause Evidence:** Discovery was only wired up to display precalculated synthetic data from `ui/demo.py`.
- **Current Test Coverage:** None for FILE discovery.
- **Missing Test:** `test_file_mode_topic_discovery_live()`.
- **Architectural Classification:** `REFACTOR` (implement `run_topic_discovery()` in application service and render Discovery UI in FILE mode).

---

### ISSUE-03: Topic Discovery in DEMO Mode is 100% Hardcoded
- **ID:** `ISSUE-03`
- **Severity:** `MEDIUM`
- **Component:** UI Demo Data
- **File:** `ui/demo.py`
- **Lines:** 330–345
- **Description:** DEMO mode populates discovery results directly from static tuples in `demo_topic_discovery_results()`. No live discovery analyzer is ever invoked.
- **Observed Effect:** Demo topic discovery is completely static.
- **Root Cause Evidence:** Precalculated mock fixture designed for rapid offline demo rendering.
- **Current Test Coverage:** Tested by `test_demo_dataset_loading()`.
- **Missing Test:** Option to run live discovery on DEMO data.
- **Architectural Classification:** `REFACTOR` (allow user to choose between precalculated demo discovery and live LLM discovery).

---

### ISSUE-04: Multi-Conversation AI Results Wipe Out on Chat Switch
- **ID:** `ISSUE-04`
- **Severity:** `HIGH`
- **Component:** UI State Management
- **File:** `ui/state.py`
- **Lines:** 317–318
- **Description:** In `select_conversation()`, `target[KEY_DETECTION_RESULTS] = ()` explicitly resets the detection results tuple to empty when switching chats.
- **Observed Effect:** Switching from Chat A to Chat B and back to Chat A wipes out all computed AI analysis for Chat A.
- **Root Cause Evidence:** Session state lacks document-scoped storage for AI results (`dict[str, tuple[TopicDetectionResult, ...]]`).
- **Current Test Coverage:** No test exercises preserving AI results across conversation switching.
- **Missing Test:** `test_multi_conversation_preserves_detection_results()`.
- **Architectural Classification:** `REFACTOR` (scope AI results by `document_id`).

---

### ISSUE-05: Context Window Chunking Disconnected from Operational AI Analyzers
- **ID:** `ISSUE-05`
- **Severity:** `CRITICAL`
- **Component:** AI Topics / Chunking
- **File:** `ai/topics.py`, `ai/chunking.py`
- **Lines:** `ai/topics.py:270-340`, `ai/chunking.py:45-120`
- **Description:** `ai/chunking.py` defines document chunking logic, but `TopicDetectionAnalyzer` and `TopicDiscoveryAnalyzer` serialize the entire document and send it directly to LM Studio in a single prompt.
- **Observed Effect:** Real forensic conversations exceeding 8192 tokens fail with HTTP 400 or suffer silent truncation.
- **Root Cause Evidence:** Chunking was developed as an isolated module and never integrated into the topic analyzer calling path.
- **Current Test Coverage:** Unit tests exist for chunking in isolation (`tests/unit/test_chunking.py`), but end-to-end analyzer tests only use tiny synthetic documents.
- **Missing Test:** `test_topic_detection_large_document_with_chunking()`.
- **Architectural Classification:** `REWRITE / REFACTOR` (integrate chunking and result aggregation directly into topic analyzers).

---

### ISSUE-06: Naive Consistency Evaluation in Discovery -> Detection
- **ID:** `ISSUE-06`
- **Severity:** `MEDIUM`
- **Component:** UI Presentation
- **File:** `ui/presentation.py`
- **Lines:** 680–683
- **Description:** Consistency between Discovery and Detection is computed solely as `decision == PRESENT ? CONSISTENT : DISCREPANCY`.
- **Observed Effect:** If Discovery finds Topic X based on Message 1, and Detection finds Topic X based on an unrelated Message 99, the system declares them `CONSISTENT` despite zero evidence overlap.
- **Root Cause Evidence:** Oversimplified UI evaluation logic without set-intersection on `evidence_ids`.
- **Current Test Coverage:** `test_ui_manual_topic_detection.py` checks basic status string.
- **Missing Test:** `test_consistency_evaluates_evidence_overlap()`.
- **Architectural Classification:** `REFACTOR` (create dedicated `ConsistencyService` computing Jaccard overlap on evidence IDs).

---

### ISSUE-07: Monolithic Coupling in `ui/presentation.py`
- **ID:** `ISSUE-07`
- **Severity:** `HIGH`
- **Component:** UI Presentation Layer
- **File:** `ui/presentation.py`
- **Lines:** 1–850
- **Description:** `presentation.py` acts as view, controller, error handler, and AI workflow coordinator all at once, violating single-responsibility principles.
- **Observed Effect:** Fragile UI where small layout changes risk breaking AI orchestration or session state.
- **Root Cause Evidence:** Incremental feature addition without separation of concerns.
- **Current Test Coverage:** Monolithic AppTest tests.
- **Missing Test:** Clean isolated unit tests for UI views decoupled from Streamlit runtime.
- **Architectural Classification:** `REWRITE` (split into view components and delegate workflow orchestration to `AnalysisSessionService`).

---

## Conclusion & Recommendations for External Reviewer

1. **Keep Core Ingestion & Normalization:** The importer pipeline (`importer/`, `validation/`, `normalization/`, `entity_resolution/`, `unified/`, `search/`) is robust, secure, and thoroughly tested. It should be kept as the foundation.
2. **Refactor Operational AI Layer:** Integrate `ai/chunking.py` into `TopicDetectionAnalyzer` and `TopicDiscoveryAnalyzer` to support arbitrarily large chats without context window exhaustion.
3. **Rewrite `ui/presentation.py` & Refactor `ui/application.py`:** Extract AI business logic into clean domain services (`TopicDiscoveryService`, `TopicDetectionService`, `ConsistencyService`). Ensure `DatasetMode.DEMO` and `DatasetMode.FILE` traverse the exact same service layer once `ConversationEvidenceDocument` is instantiated.
4. **Fix State Scoping:** Scope all detection, discovery, and consistency results by `document_id` in session state to enable seamless multi-conversation analysis.
