# Inline Comments Feature

## Overview

In addition to the existing top-level PR summary comment, the review system now posts **inline comments** directly on the relevant file lines in the Azure DevOps diff view. This gives developers immediate visual context for each finding without leaving the code review interface.

## Architecture Decision Record

| Decision | Choice | Rationale |
|---|---|---|
| Alongside or replace top-level comment? | Additive — both are posted | Preserves the full summary; inline comments provide additional signal |
| How to determine file/line targets? | Prompt-driven structured JSON | More accurate than heuristic parsing; Gemini has full diff context |
| Which findings get inline comments? | `action-required` and `review-recommended` only | Reduces noise; `note`-level findings don't warrant inline interruption |
| Failure mode | Non-fatal per-finding | Inline failure must not block the top-level comment or PR status |

## How It Works

### 1. Gemini Output Format

The system prompt instructs Gemini to append a structured JSON block after its markdown review, separated by a known delimiter:

```
<!--FINDINGS_JSON_START-->
{
  "findings": [
    {
      "title": "Missing default value for data attribute",
      "priority": "action-required",
      "file_path": "/components/button/button.htl",
      "line_number": 42,
      "side": "right",
      "inline_comment": "This attribute has no default value. If the Sling Model property is null, the rendered HTML will break downstream JS that reads this attribute."
    }
  ]
}
```

**Field rules:**
- `priority`: must be `"action-required"` or `"review-recommended"` (Gemini omits `"note"` findings)
- `file_path`: must exactly match the path shown in the diff header
- `line_number`: 1-based, refers to the line in `side` file
- `side`: `"right"` = new/source file; `"left"` = old/target file (deleted lines)
- If line cannot be determined confidently, Gemini omits that finding from the JSON

### 2. Parsing (`pr_review/severity.py`)

`parse_findings_json(review)` extracts and validates the JSON block:

1. Checks for `FINDINGS_JSON_DELIMITER` — returns `[]` immediately if absent (backwards compatible)
2. Partitions on delimiter, parses JSON
3. Validates each finding: required fields, valid `priority`, `line_number >= 1`, valid `side`
4. Invalid findings are skipped with a WARNING log; parse failures return `[]`

### 3. Posting Inline Comments (`pr_review/azure_client.py`)

`post_inline_comment(pr_id, content, file_path, line_number, side)` posts to:

```
POST /git/repositories/{repo}/pullrequests/{pr_id}/threads
```

With a `threadContext` to anchor the comment to a specific line:

```json
{
  "comments": [{"parentCommentId": 0, "content": "...", "commentType": 1}],
  "threadContext": {
    "filePath": "/components/button/button.htl",
    "rightFileStart": {"line": 42, "offset": 1},
    "rightFileEnd": {"line": 42, "offset": 1}
  },
  "status": 1
}
```

Uses `leftFileStart`/`leftFileEnd` when `side="left"`.

### 4. Orchestration (`pr_review/review.py`)

- `parse_findings_json` is called immediately after severity detection
- `_post_inline_comments` is called after `post_pr_comment` succeeds, inside the `has_blocking or has_warning` branch
- Each inline comment call is wrapped in a broad `except Exception` — a single failure skips that finding and continues

## Error Handling

| Failure | Behaviour |
|---|---|
| No delimiter in Gemini response | `parse_findings_json` returns `[]`; no inline comments posted |
| Malformed JSON | `JSONDecodeError` caught; WARNING logged; returns `[]` |
| Missing/invalid fields in a finding | That finding skipped; WARNING logged; others continue |
| Azure DevOps API error on inline comment | That finding skipped; WARNING logged; others continue |
| All inline comments fail | Only WARNING logs; top-level comment and PR status are unaffected |

## Deployment Notes

If the system prompt is served from GCS (`prompts/system-prompt.txt`), the blob must be updated to include the new `## Structured Findings Output` section with the `<!--FINDINGS_JSON_START-->` delimiter instruction. Until updated, the hardcoded `SYSTEM_PROMPT` constant fallback in `prompt.py` applies automatically.

## Key Constants

| Constant | Location | Value | Purpose |
|---|---|---|---|
| `FINDINGS_JSON_DELIMITER` | `prompt.py` | `<!--FINDINGS_JSON_START-->` | Splits markdown from JSON in Gemini response |
| `INLINE_COMMENT_SEVERITIES` | `severity.py` | `{"action-required", "review-recommended"}` | Findings eligible for inline comments |
| `COMMENT_TYPE_TEXT` | `azure_client.py` | `1` | Azure DevOps API: plain text comment |
| `THREAD_STATUS_ACTIVE` | `azure_client.py` | `1` | Azure DevOps API: active thread status |
| `LINE_OFFSET_DEFAULT` | `azure_client.py` | `1` | Azure DevOps API: line offset for single-line annotations |

## Files Modified

| File | Change |
|---|---|
| `pr_review/prompt.py` | Added `FINDINGS_JSON_DELIMITER`; extended `SYSTEM_PROMPT` with structured output instructions |
| `pr_review/azure_client.py` | Added `post_inline_comment()`; extracted magic numbers to named constants |
| `pr_review/severity.py` | Added `parse_findings_json()`, `INLINE_COMMENT_SEVERITIES`, `_FINDING_REQUIRED_FIELDS`, `_VALID_SIDES` |
| `pr_review/review.py` | Wired `parse_findings_json` call and `_post_inline_comments` private helper |
| `tests/test_main.py` | Added `TestParseFindings`, `TestPostInlineComment`, `TestPostInlineComments` |
