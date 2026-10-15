# CHATANALYSIS — GIT BASELINE, STATUS & BENCHMARK INTEGRITY

**Data**: 13 Ottobre 2026  
**Repository**: ChatAnalysis  
**Baseline Commit**: `6c1dc46b88158b394b8b4ea06d8fac91f38c08a2` (`UPDATE 18`)  
**Codifica Patch**: UTF-8 (Unix/Windows compliant)  

---

## 1. BASELINE COMMIT IDENTIFIER

```text
commit 6c1dc46b88158b394b8b4ea06d8fac91f38c08a2
Author: Luca Sorbi
Date:   Mon Oct 13 2026
Subject: UPDATE 18
```

---

## 2. STATO DEI FILE MODIFICATI NEL REPOSITORY

```text
MODIFICHE TRACCIATE (git status -s):
 M ai/__init__.py
 M ai/backend.py
 M ai/chunking.py
 M ai/lmstudio.py
 M ai/models.py
 M ai/topics.py
 M analysis/service.py
 M ui/application.py
 M ui/presentation.py

NUOVI FILE / SORGENTI DI SUPPORTO AUDIT:
?? ai/token_budget.py
?? analysis/progress.py
?? tests/unit/test_integrated_audit_fixes.py
?? scripts/measure_scalability_and_profiling.py
?? scripts/create_git_patch_utf8.py
?? scripts/run_and_log_tests_utf8.py
?? docs/SCALABILITY_MEASUREMENTS_REPORT.md
?? test_data/synthetic_whatsapp_large_75kb.txt
?? pytest_and_apptest.log
?? git_diff_integrated_audit.patch
?? CHATANALYSIS_INTEGRATED_CORRECTIONS_AUDIT_REPORT.md
?? dist/whatsapp_forensic_importer-0.1.0-py3-none-any.whl
```

---

## 3. STATISTICHE DELLA PATCH GIT (`git diff --stat HEAD`)

```text
 ai/__init__.py      |   2 +
 ai/backend.py       |  28 +++-
 ai/chunking.py      | 106 +++++++++++++-
 ai/lmstudio.py      |  97 ++++++++++++-
 ai/models.py        |   1 +
 ai/topics.py        | 274 +++++++++++++++++++++++++++---------
 analysis/service.py | 398 +++++++++++++++++++++++++++++++++++++++++++++++++---
 ui/application.py   |  50 ++++++-
 ui/presentation.py  | 253 +++++++++++++++++++++++++++------
 9 files changed, 1074 insertions(+), 135 deletions(-)
```

---

## 4. VERIFICA HASH SHA256 DEI FILE BENCHMARK CONGELATI

I tre file del benchmark scientifico rimangono assolutamente intatti e congelati:

### `ai/experiment.py`
- Baseline SHA256: `91100C0A84CB4A8B7C2EEF34A06CFF9EC21AA6E6EEC14FFD09CE6FEA8BCEE845`
- Attuale SHA256:  `91100C0A84CB4A8B7C2EEF34A06CFF9EC21AA6E6EEC14FFD09CE6FEA8BCEE845`
- Esito: **100% IDENTICO (INTEGRO)**

### `ai/benchmark.py`
- Baseline SHA256: `DC57081DBCE3463A8017AA26BB4E4A18889AF0A3296853FE40968561D6D44D36`
- Attuale SHA256:  `DC57081DBCE3463A8017AA26BB4E4A18889AF0A3296853FE40968561D6D44D36`
- Esito: **100% IDENTICO (INTEGRO)**

### `run_benchmark.sh`
- Baseline SHA256: `7F9CAE97DF2FAE1BC3B5964E639B9F80E3AC5A388EC9C3E20305FF64D48044A7`
- Attuale SHA256:  `7F9CAE97DF2FAE1BC3B5964E639B9F80E3AC5A388EC9C3E20305FF64D48044A7`
- Esito: **100% IDENTICO (INTEGRO)**
