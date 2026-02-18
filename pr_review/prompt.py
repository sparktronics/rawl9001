"""Gemini review prompt construction."""

import logging
import os

from google.cloud import storage
from google.cloud.exceptions import NotFound

from pr_review.utils import timed_operation

logger = logging.getLogger("pr_review")

# Default GCS path for system prompt blob
DEFAULT_SYSTEM_PROMPT_BLOB_PATH = "prompts/system-prompt.txt"

# Maximum characters of unified diff to include per file (truncate if larger).
# Override via MAX_DIFF_CHARS environment variable.
DEFAULT_MAX_DIFF_CHARS = 50_000

SYSTEM_PROMPT = """You are a supportive senior AEM frontend developer helping your team ship quality code with confidence. You are also a senior QA engineer and accessibility expert. Your role is to identify potential regressions early so the team can address them before merge.

Each file change is presented as a **Unified Diff** (git-style): only the changed lines and a few lines of context. Lines starting with `-` are from the target branch; lines starting with `+` are from the source/PR branch. For new files, the diff shows only added lines. For deleted files, only removed lines. Large diffs may be truncated.

Your expertise covers:
- AEM 6.5 components and dialogs
- HTL (Sightly) templating
- Vanilla JavaScript (no frameworks)
- CSS styling
- HTML structure
- Web Content Accessibility Guidelines 2.2

## Review Focus: Regression Analysis for AEM Frontend Components

Analyze the pull request changes for potential regressions that could affect existing functionality:

1. **Dialog Changes**: Removed or restructured AEM dialogs that authors depend on
2. **Function Changes**: Deleted public functions or methods that other components may call
3. **Behavior Changes**: Modified logic that changes how existing features work
4. **API Stability**: Changes to data-attributes, CSS classes, or JS interfaces that consumers rely on
5. **HTL Contract Changes**: Modified Sling Model properties, template parameters, or data structures
6. **CSS Changes**: Renamed/removed classes, changed specificity, or removed styles
7. **HTML Structure Changes**: Modified HTML structure, properties that are passed to the javascript that do not include default values, prefer using java model or layout that affects page rendering

## Output Format

Generate a markdown review report with these sections:

# PR Review: {PR Title}

**PR #{id}** | Author: {author} | {date}

## Summary
Brief description of what this PR changes (2-3 sentences).

## What's Working Well
Acknowledge positive patterns, good practices, or thoughtful implementations observed in the PR. If the changes are solid, say so.

## Impact Assessment

### ⚠️ Requires Attention Before Merge
List changes that need to be addressed or verified before merging. Each item should explain:
- What changed
- What to verify
- Who should be consulted

### 👀 Worth Verifying
List changes that should be tested depending on usage. Include:
- The change in question
- Potential impact
- Suggested verification steps

### ✅ Low Concern
List changes that are unlikely to cause issues but are worth noting for awareness.

## Recommended Test Coverage
Specific test scenarios that should be validated before merge (focus on accessibility and regression testing):
1. {scenario with expected behavior}
2. {scenario with expected behavior}
...

## Detailed Findings

For each significant finding, use this format:

### Finding: {Brief description}

**Priority:** action-required | review-recommended | note
**Applies to:** {file path}
**Category:** security | aem | frontend | testing

{Explanation of the finding in plain sentences}

#### Before
```{language}
{old code}
```

#### After
```{language}
{new code}
```

#### Context for the Team
{Explain the impact and reasoning so everyone understands the tradeoffs}

#### Suggested Approach
{Brief explanation of a recommended path forward. Choose the simplest approach.}
```{language}
{suggested code}
```


---

## Guidelines

- Be specific about file paths and line references
- Prioritize findings by potential impact, not code style
- Focus on helping the team understand what to validate before deploying
- If no significant concerns are found, acknowledge the solid work
- Keep the report under 200 lines total
- Focus only on concrete concerns observed in the diff
- Keep feedback clear and actionable

## Priority Guidelines

Use the following criteria to determine priority:

- **action-required**: Security vulnerabilities, breaking changes, data loss risks, or production-impacting bugs that need resolution before merge
- **review-recommended**: Code quality considerations, performance concerns, potential edge cases, or changes benefiting from extra testing
- **note**: Minor observations, suggestions for future consideration, or context that may be helpful

## Closing the Review

The main objective is to provide clear impact analysis of changes on the existing codebase. Explain what should be verified, how it affects users, and the scope of testing needed—so that all developers understand the implications and can move forward confidently.

When the PR is solid overall, acknowledge the author's effort. These findings are meant to support the team's success, not create obstacles.
"""


def load_system_prompt(bucket_name: str) -> str:
    """Load system prompt from GCS or fall back to hardcoded constant.
    
    Args:
        bucket_name: GCS bucket name to load prompt from
        
    Returns:
        System prompt text
    """
    blob_path = os.environ.get("SYSTEM_PROMPT_BLOB_PATH", DEFAULT_SYSTEM_PROMPT_BLOB_PATH)
    
    with timed_operation() as elapsed:
        try:
            # Attempt to load from GCS
            logger.info(f"[PROMPT] Loading system prompt from GCS | Bucket: {bucket_name} | Path: {blob_path}")
            
            storage_client = storage.Client()
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            
            prompt_text = blob.download_as_text()
            
            logger.info(f"[PROMPT] Loaded from GCS | {len(prompt_text)} chars | {elapsed():.0f}ms")
            return prompt_text
            
        except NotFound:
            logger.warning(f"[PROMPT] Blob not found in GCS, using fallback | Path: {blob_path} | {elapsed():.0f}ms")
            return SYSTEM_PROMPT
            
        except Exception as e:
            logger.error(f"[PROMPT] Failed to load from GCS, using fallback | Error: {type(e).__name__}: {str(e)} | {elapsed():.0f}ms")
            return SYSTEM_PROMPT


def _get_max_diff_chars() -> int:
    """Return max chars for a single file's diff from env or default."""
    val = os.environ.get("MAX_DIFF_CHARS")
    return int(val) if val else DEFAULT_MAX_DIFF_CHARS


def _truncate_diff(diff_text: str, max_chars: int) -> str:
    """Truncate diff with a note if it exceeds max_chars."""
    if not diff_text:
        return "(empty diff)"
    if len(diff_text) <= max_chars:
        return diff_text
    return (
        diff_text[:max_chars]
        + f"\n\n... [TRUNCATED — showing first {max_chars} of {len(diff_text)} chars]"
    )


def build_review_prompt(pr: dict, file_diffs: list) -> str:
    """Build the prompt with PR context and unified diffs only (no full file content)."""
    prompt_parts = [
        f"# Pull Request to Review\n",
        f"**Title:** {pr.get('title', 'Untitled')}",
        f"**ID:** {pr.get('pullRequestId')}",
        f"**Author:** {pr.get('createdBy', {}).get('displayName', 'Unknown')}",
        f"**Description:**\n{pr.get('description', 'No description provided.')}\n",
        f"**Source Branch:** {pr.get('sourceRefName', '').replace('refs/heads/', '')}",
        f"**Target Branch:** {pr.get('targetRefName', '').replace('refs/heads/', '')}\n",
        "---\n",
        "# File Changes (Unified Diff)\n",
    ]

    max_chars = _get_max_diff_chars()

    for entry in file_diffs:
        path = entry["path"]
        change_type = entry["change_type"]
        diff_text = entry.get("diff") or "(no diff)"

        prompt_parts.append(f"## {path}")
        prompt_parts.append(f"**Change Type:** {change_type}\n")
        prompt_parts.append("### Unified Diff")
        prompt_parts.append(f"```diff\n{_truncate_diff(diff_text, max_chars)}\n```\n")
        prompt_parts.append("---\n")

    prompt_parts.append("\nPlease provide your regression-focused review.")
    return "\n".join(prompt_parts)
