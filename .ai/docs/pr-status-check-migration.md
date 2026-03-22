# PR Status Check Migration

## Problem

The current blocking mechanism casts a `vote=-10` (reject) on the PR using the user's personal PAT. This ties the block to a specific person's identity — to unblock the PR, that same person must change their vote. Tech leads cannot proceed without waiting for the individual.

## Solution

Replace the vote-based rejection with an **Azure DevOps PR Status Check** (Statuses API). Status checks behave like CI checks — they can be required via branch policy, and tech leads with branch policy bypass permissions can **override and complete** the PR without waiting for the individual.

---

## Code Changes

### Files modified

| File | Change |
|---|---|
| `pr_review/azure_client.py` | Add `post_pr_status()` method |
| `pr_review/review.py` | Replace `reject_pr()` with `post_pr_status()` for all severity outcomes |
| `pr_review/config.py` | Add `STATUS_CONTEXT_NAME` and `STATUS_GENRE` optional vars |

### Severity → Status mapping

| Severity | Status posted | Comment posted |
|---|---|---|
| `action-required` | `failed` | ✅ |
| `review-recommended` | `succeeded` (with note) | ✅ |
| `note` | `succeeded` | ❌ |
| `JUST_COMMENT_TICKET=true` | _(no status posted)_ | ✅ |

---

## One-time Azure DevOps Setup (admin)

### 1. Configure Status policy on the target branch

- **Where:** Project Settings → Repos → Branches → target branch → Branch Policies
- **Add policy:** Status Check
- **Status to check:** genre=`rawl-review`, name=`rawl-review/ai-review`
  _(must match `STATUS_GENRE` / `STATUS_CONTEXT_NAME` env vars)_
- **Set as:** Required
- **Authorised account:** The Azure DevOps identity of the PAT used by the Cloud Function

### 2. Grant tech lead group bypass permission

- **Where:** Branch security settings → tech lead group
- **Permission:** "Bypass policies when completing pull requests" → **Allow**
- **Effect:** Tech leads see an "Override branch policies and enable merge" checkbox on blocked PRs, with an auditable reason field

### 3. Redeploy the Cloud Function

After deploying updated code, optionally set these env vars if non-default values are needed:

```bash
--set-env-vars="STATUS_CONTEXT_NAME=rawl-review/ai-review,STATUS_GENRE=rawl-review"
```

---

## New Environment Variables

| Variable | Default | Description |
|---|---|---|
| `STATUS_CONTEXT_NAME` | `rawl-review/ai-review` | Status check name shown in Azure DevOps PR Checks tab and used in branch policy config |
| `STATUS_GENRE` | `rawl-review` | Grouping label for related status checks |

---

## Verification

1. Trigger a test PR with a known regression in the AEM repo
2. Confirm the bot posts a **failed** status visible in the PR Checks tab in Azure DevOps
3. Confirm the PR cannot be completed by a non-bypass user
4. Log in as a tech lead group member → confirm **"Override and complete"** checkbox is visible
5. Override and confirm the PR completes successfully
6. Trigger a clean PR → confirm a **succeeded** status is posted and the PR can be merged normally
