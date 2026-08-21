#!/usr/bin/env bash
# Compares c167cc's generated assembly for a source file against a golden file.
# Usage: golden_test.sh <c167cc-binary> <input.c> <golden.asm>
set -euo pipefail
BIN="$1"
SRC="$2"
GOLDEN="$3"

ACTUAL="$("$BIN" --dump-asm "$SRC")"

if [ ! -f "$GOLDEN" ]; then
  echo "$ACTUAL" > "$GOLDEN"
  echo "golden file did not exist, created: $GOLDEN"
  exit 0
fi

EXPECTED="$(cat "$GOLDEN")"

if [ "$ACTUAL" != "$EXPECTED" ]; then
  echo "MISMATCH for $SRC"
  diff <(echo "$EXPECTED") <(echo "$ACTUAL") || true
  exit 1
fi
