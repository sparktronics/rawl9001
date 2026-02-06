# Feature Plan: Repository Reorganization

**Created:** 2026-02-06
**Status:** Complete
**Author:** AI Assistant

---

## 1. Objective

Split the monolithic `main.py` (1,839 LOC) into a `pr_review/` Python package and reorganize the flat repository into a well-structured project with dedicated directories for docs, scripts, and tests.

---

## 2. Background

The project grew organically from a single `main.py` containing all logic: Azure DevOps client, Gemini integration, file filtering, prompt building, idempotency, storage, and four Cloud Function entry points. Documentation, scripts, and tests were scattered at the repository root. This made navigation, testing, and maintenance difficult.

---

## 3. Scope

### In Scope
- Split `main.py` into 13 modules in `pr_review/` package
- Maintain `main.py` as thin facade for Cloud Functions compatibility
- Move documentation to `docs/`
- Move shell scripts to `scripts/`
- Move tests to `tests/`
- Update all infrastructure (Terraform, deploy script, .gcloudignore)
- Update all test mock paths

### Out of Scope
- Adding new features or functionality
- Changing business logic
- Upgrading dependencies
- Modifying CI/CD workflows

---

## 4. Technical Approach

### 4.1 Overview

Cloud Functions Gen2 requires entry points to be importable from `main.py`. The solution uses a **thin facade pattern**: `main.py` re-exports entry points from the `pr_review` package, which Cloud Functions discovers at import time via `@functions_framework` decorators.

Internal modules use **module-level imports** for mockability:
```python
# In review.py
from pr_review import filtering, gemini, storage
result = gemini.call_gemini(config, prompt)  # mockable at pr_review.gemini.call_gemini
```

### 4.2 Module Dependency Graph (acyclic)
```
utils         (no deps)
config        (no deps)
models        (no deps)
severity      (no deps)
prompt        (no deps)
filtering     <- config, models, utils
azure_client  <- utils
storage       <- utils
idempotency   (uses stdlib + google.cloud only)
gemini        <- utils, prompt
review        <- filtering, gemini, storage, severity, models, utils, config, azure_client
entry_points  <- config, azure_client, review, idempotency, storage, utils
```

### 4.3 Final Directory Structure
```
pr-poc-script/
├── main.py                       # Thin facade (~10 lines, re-exports entry points)
├── pr_review/                    # Python package (split from main.py)
│   ├── __init__.py               # Package exports
│   ├── utils.py                  # timed_operation(), make_response(), logging
│   ├── config.py                 # load_config(), load_webhook_config(), constants
│   ├── models.py                 # ReviewResult, FilterResult dataclasses
│   ├── azure_client.py           # AzureDevOpsClient class
│   ├── storage.py                # save_to_storage()
│   ├── idempotency.py            # check_and_claim_processing(), update_marker_*()
│   ├── severity.py               # get_max_severity()
│   ├── filtering.py              # filter_non_code_files(), is_extensive_pr()
│   ├── prompt.py                 # SYSTEM_PROMPT, build_review_prompt()
│   ├── gemini.py                 # call_gemini()
│   ├── review.py                 # process_pr_review() core orchestration
│   └── entry_points.py           # 4 Cloud Function entry points
├── tests/                        # Unit and integration tests
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_main.py
│   └── test_vertex.py
├── docs/                         # Documentation
│   ├── SETUP.md
│   ├── AUTHENTICATION.md
│   ├── CONSOLE_IAM_SETUP.md
│   ├── IAM_AUTHENTICATION_PLAN.md
│   ├── IMPLEMENTATION_SUMMARY.md
│   └── pr-357462-review.md
├── scripts/                      # Deployment and setup scripts
│   ├── deploy.sh
│   ├── setup-env.sh
│   └── env-template.sh
├── requirements.txt
├── README.md
├── env.example
├── service.yaml
├── .gcloudignore
├── terraform/
├── .ai/
├── .claude/
├── .github/
├── api/
├── azure/
└── prompts/
```

### 4.4 Files Modified
| File | Change |
|------|--------|
| `main.py` | Replaced 1,839 lines with ~10-line facade |
| `terraform/functions.tf` | Added 13 pr_review/ source blocks to archive_file |
| `scripts/deploy.sh` | Added repo root resolution, pr_review/ check |
| `.gcloudignore` | Added scripts/tests/docs exclusions |
| `.gitignore` | Removed stale entries |
| `.cursorrules` | Updated project context and key files table |
| `README.md` | Updated links to docs/ and scripts/ |

### 4.5 Mock Path Migration
All `mocker.patch("main.XXX")` calls updated to target the actual definition module:

| Old Mock Path | New Mock Path |
|---------------|---------------|
| `main.call_gemini` | `pr_review.gemini.call_gemini` |
| `main.save_to_storage` | `pr_review.storage.save_to_storage` |
| `main.requests.request` | `pr_review.azure_client.requests.request` |
| `main.requests.get` | `pr_review.azure_client.requests.get` |
| `main.storage.Client` | `pr_review.storage.storage.Client` or `pr_review.idempotency.storage.Client` |
| `main.pubsub_v1.PublisherClient` | `pr_review.entry_points.pubsub_v1.PublisherClient` |

---

## 5. Implementation Steps

- [x] Phase 1: Create `pr_review/` package with all 13 modules
- [x] Phase 2: Replace `main.py` with thin facade
- [x] Phase 3: Move tests to `tests/`, update mock paths (71 tests pass)
- [x] Phase 4: Move docs to `docs/`, scripts to `scripts/`
- [x] Phase 5: Update infrastructure (terraform, deploy.sh, .gcloudignore, .gitignore)
- [x] Phase 6: Update .cursorrules, create this plan guide

---

## 6. Testing Plan

- All 71 unit tests pass via `pytest tests/test_main.py -v`
- Entry points importable: `python -c "from main import review_pr; print('OK')"`
- Package importable: `python -c "from pr_review import review_pr; print('OK')"`
- Terraform archive includes all pr_review/ files (13 source blocks)
- deploy.sh validates pr_review/ directory exists before deploying

---

## 7. Notes

- `SYSTEM_PROMPT` lives in `pr_review/prompt.py` (embedded in the package, not in external prompt files)
- The external prompt loaded from GCS at runtime is separate from `SYSTEM_PROMPT`
- Cloud Functions discovers entry points via `@functions_framework` decorators at import time
- The `__init__.py` eagerly imports all submodules, so `from pr_review import review_pr` works
