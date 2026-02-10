# Unified Diff Approach for PR Reviews

## What Changed

The AI review tool now sends **only the changed lines** of each file to the language model, rather than sending the entire file contents before and after the change.

## Why This Matters

### Before

When a developer changed 3 lines in a 500-line file, the tool sent all 500 lines twice (the old version and the new version). The AI had to read through 1,000 lines just to find the 3 that actually changed.

### After

The tool now sends a **unified diff** -- a compact format that shows only the changed lines along with 5 lines of surrounding context. For that same 3-line change in a 500-line file, the AI might receive ~13 lines instead of 1,000.

## Benefits

- **Lower cost**: Fewer tokens sent to the AI model means lower per-review costs.
- **Faster reviews**: Smaller inputs are processed more quickly.
- **More focused feedback**: The AI spends its attention on actual changes rather than scanning unchanged code, leading to more relevant review comments.

## What Stays the Same

- **New files** are still shown in full so the AI can review the complete implementation.
- **Deleted files** are still shown in full so the AI can assess what is being removed.
- The review output format, quality criteria, and regression analysis focus are all unchanged.

## How a Unified Diff Looks

A unified diff uses simple markers to indicate changes:

```
 unchanged line (context)
-this line was removed
+this line was added
 unchanged line (context)
```

Lines starting with `-` were in the old version. Lines starting with `+` are in the new version. Unmarked lines provide surrounding context so the AI understands where the change occurs.
