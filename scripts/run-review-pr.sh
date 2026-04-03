#!/bin/bash

set -e

# Resolve repo root so this works no matter where it's called from.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

python3 -m functions_framework --target=review_pr --debug --port=8080
