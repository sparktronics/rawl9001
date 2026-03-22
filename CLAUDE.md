# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

RAWL 9001 POC — An automated PR regression review system deployed as Google Cloud Functions. It fetches PRs from Azure DevOps, sends file diffs to Gemini (Vertex AI) for analysis, and auto-comments or auto-rejects PRs based on severity. Focused on AEM frontend component regression detection (HTL/JS/CSS).

## Commands

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run HTTP function locally (port 8080)
functions-framework --target=review_pr --debug

# Run with specific env file
source .env && functions-framework --target=review_pr --debug

# Test a PR via curl
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"pr_id": 12345, "debug": true}'
```

### Tests

```bash
# Run all tests
pytest

# Run a single test file
pytest tests/test_main.py

# Run a single test
pytest tests/test_main.py::test_function_name -v

# Run with coverage
pytest --cov=pr_review
```

### Deployment

```bash
# Deploy via Terraform
cd terraform
terraform init
terraform plan
terraform apply

# Deploy a single function manually (HTTP)
gcloud functions deploy review_pr \
  --gen2 --runtime python312 --trigger-http \
  --entry-point review_pr --region us-central1
```

## Architecture

**Entry points** (`main.py` → `pr_review/entry_points.py`):
- `review_pr` — HTTP-triggered, synchronous, IAM-authenticated
- `review_pr_pubsub` — Pub/Sub-triggered, async, idempotent (production recommended)
- `receive_webhook` — Accepts Azure DevOps webhook, publishes to Pub/Sub for async processing
- `process_dead_letter_queue` — HTTP endpoint to manually reprocess failed messages

**Core review pipeline** (`pr_review/review.py`):
1. Fetch PR metadata + file diffs from Azure DevOps (`azure_client.py`)
2. Filter files — exclude non-code files, cap file count for extensive PRs (`filtering.py`)
3. Build prompt with before/after diffs (`prompt.py`)
4. Call Gemini via Vertex AI (`gemini.py`) — loads system prompt from GCS with fallback to `prompts/system-prompt.txt`
5. Parse severity from review markdown (`severity.py`)
6. Save review to GCS with date partitioning: `reviews/YYYY/MM/DD/pr-{id}-{ts}-review.md`
7. Post comment and/or reject PR in Azure DevOps based on severity

**Severity → actions:**
| Severity | Comment PR | Reject PR | Save to GCS |
|---|---|---|---|
| `action-required` (blocking) | ✅ | ✅ (unless `JUST_COMMENT_TICKET=true`) | ✅ |
| `review-recommended` (warning) | ✅ | ❌ | ✅ |
| `note` (info) | ❌ | ❌ | ✅ |

**Idempotency** (`idempotency.py`): GCS-based atomic claims using conditional writes (`if_generation_match=0`). Markers stored at `idempotency/pr-{id}-{sha}.json`. Tracks retry count (max 3), prevents duplicate Pub/Sub processing.

**Configuration** (`pr_review/config.py`): All config loaded from environment variables. See `env.example` for the full list. Key vars: `GCS_BUCKET`, `AZURE_DEVOPS_PAT`, `AZURE_DEVOPS_ORG`, `AZURE_DEVOPS_PROJECT`, `AZURE_DEVOPS_REPO`, `VERTEX_PROJECT`.

## File Operation Policy

Per `.cursorrules`: **ASK before creating new files.** Read files freely, but creating new files requires explicit confirmation. Use named constants — no magic numbers in code.

## Review Guidelines

When reviewing code in this repo (see `.claude/rules.md`):
- **Severity levels:** `blocking` (must fix before merge), `warning` (should fix), `info` (note only)
- **Out of scope:** personal style preferences, minor formatting, hypothetical improvements
- Focus on: actual bugs, regression risk, security issues, performance problems

## CI Workflows

- **`claude.yml`** — Responds to `@claude` mentions in PR/issue comments
- **`claude-code-review.yml`** — Auto-reviews Python file changes on PR open/sync, posts comment via `gh pr comment`
