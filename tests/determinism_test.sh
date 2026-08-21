#!/usr/bin/env bash
# Verifies that compiling the same source twice yields byte-identical assembly.
set -euo pipefail
BIN="$1"
SRC="$2"

A="$("$BIN" --dump-asm "$SRC")"
B="$("$BIN" --dump-asm "$SRC")"

if [ "$A" != "$B" ]; then
  echo "non-deterministic output for $SRC"
  exit 1
fi
