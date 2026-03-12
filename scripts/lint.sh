#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Run all linters/formatters locally (mirrors what CI checks).
# Usage: bash scripts/lint.sh [--fix]
# ─────────────────────────────────────────────────────────────────────────────
set -e

FIX=false
[[ "$1" == "--fix" ]] && FIX=true

echo "==> isort (import sorting)"
if $FIX; then isort .; else isort --check-only .; fi

echo "==> black (formatting)"
if $FIX; then black .; else black --check .; fi

echo "==> ruff (linting)"
if $FIX; then ruff check --fix .; else ruff check .; fi

echo "All checks passed."
