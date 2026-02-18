# Git-Based Diff Approach for PR Reviews

## Overview

The AI review tool uses a **git-based diff** flow: the list of changed files is obtained from the Azure DevOps **diffs/commits** API, then a **unified diff** is generated per file. The prompt sent to the model contains **only the unified diff** for each file (no full old/new file content), so the model sees the same format as `git diff`.

## How It Works

### 1. Obtaining the change list

- The client calls the PR endpoint to get **source** and **target** commit IDs.
- It then calls **`GET /git/repositories/{repo}/diffs/commits`** with:
  - `baseVersion` = target branch commit
  - `targetVersion` = source branch commit  
  - `baseVersionType=commit`, `targetVersionType=commit` (as required by the API)

The response is a JSON list of **changes** (path, `changeType`, folder/blob). This replaces the previous use of the PR iteration **changes** endpoint for the file list.

### 2. Generating the unified diff per file

The Azure **diffs/commits** API returns only metadata (path, change type); it does **not** return line-by-line patch text. So for each **blob** (file) in the change list:

- The client fetches file content at the **target** commit and at the **source** commit via the existing Items API (`get_file_content`).
- A **unified diff** is generated with Python’s `difflib.unified_diff()` (same format as `git diff -u`), with a small number of context lines (e.g. 5).

Resulting shape per file: `{path, change_type, diff}` where `diff` is the unified diff string. For **add** and **delete**, the diff may be an add-only or delete-only hunk (or full content in diff form) so the model can still review new/removed files.

### 3. What the model receives

- **Per file:** one section with path, change type, and a single **Unified Diff** block (no “Old Content” or “New Content” sections).
- New files: diff shows added lines (or full new file as add).
- Deleted files: diff shows removed lines (or full old file as delete).
- Large diffs can be truncated per file using a configurable max character limit.

## Benefits

- **Fewer tokens:** Only changed lines (plus context) are sent, not full file contents twice.
- **Familiar format:** The model sees standard unified diff (`-` / `+` / context), matching git and code review UIs.
- **Single source of truth:** The file list comes from the official **diffs/commits** API between the two commits.
- **Extensive PR detection:** “Extensive” is based on total **diff** size (and file count), not full content size.

## What Stays the Same

- New and deleted files are still represented (as add-only or delete-only diffs, or equivalent).
- Review output format, severity levels, and regression focus are unchanged.
- Non-code file filtering and file-count limits for extensive PRs still apply.

## Unified diff format

Unified diff uses simple markers:

```
 unchanged line (context)
-this line was removed
+this line was added
 unchanged line (context)
```

- Lines starting with `-` are from the old (target) version.
- Lines starting with `+` are from the new (source) version.
- Unmarked lines are context so the model can see where the change sits in the file.

## References

- Azure DevOps: [Diffs - Get](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/diffs/get?view=azure-devops-rest-7.1) (diffs/commits).
- In-repo: `pr_review/azure_client.py` (`get_pr_diff`, `_get_diffs_commits`, `_generate_unified_diff`), `pr_review/prompt.py` (`build_review_prompt`).
