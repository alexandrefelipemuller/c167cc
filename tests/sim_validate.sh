#!/usr/bin/env bash
# Cross-validates c167cc's generated assembly against the independent
# assembler+simulator in ../simulador (c166asm.py/c166sim.py), which
# implements real C166/C167 opcode encoding validated against actual
# ECU firmware disassembly. See docs/limitations.md: that tool has no
# [Rw+#offset] indirect addressing, so tests/port_to_toy_asm.py adapts
# c167cc's frame-relative locals to flat variables first - see its
# docstring for exactly what is and isn't changed.
#
# Usage: sim_validate.sh <c167cc-binary> <source.c> <function> \
#           <sim-args...> -- <VAR>=<expected> [<VAR>=<expected> ...]
set -euo pipefail
BIN="$1"; SRC="$2"; FUNC="$3"; shift 3

SIM_ARGS=()
while [ "$1" != "--" ]; do SIM_ARGS+=("$1"); shift; done
shift

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIMDIR="$HERE/../simulador"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

"$BIN" --dump-asm "$SRC" > "$WORK/full.asm"
python3 "$HERE/port_to_toy_asm.py" "$WORK/full.asm" "$FUNC" > "$WORK/toy.asm"

( cd "$SIMDIR" && python3 c166asm.py "$WORK/toy.asm" "$WORK/toy.bin" ) > "$WORK/asm.log"
( cd "$SIMDIR" && python3 c166sim.py "$WORK/toy.bin" --syms="$WORK/toy.asm" "${SIM_ARGS[@]}" ) > "$WORK/sim.log"

fail=0
for pair in "$@"; do
  var="${pair%%=*}"
  expected="${pair#*=}"
  actual="$(grep -E "^\s*${var}\s" "$WORK/sim.log" | awk -F'= ' '{print $2}' | tr -d ' ')"
  if [ "$actual" != "$expected" ]; then
    echo "MISMATCH: $var expected=$expected actual=$actual"
    fail=1
  fi
done

if [ "$fail" != 0 ]; then
  cat "$WORK/sim.log"
  exit 1
fi
