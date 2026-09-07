#!/usr/bin/env bash
# Regressão em nível de EXECUÇÃO (não só texto pinado) pro bug de
# 06/09/2026: um valor vivo através de uma chamada (`v = v + f(x, v)`)
# podia ganhar um registrador do pool de temporários que a própria
# chamada destrói (retorno em R0, mesmo pool reusado pela função
# chamada) - runtime errado com `--dump-asm` perfeitamente plausível,
# sem erro de montagem. examples/call_liveness.c e o golden-call_liveness
# (tests/golden/call_liveness.asm) pinam o texto esperado; este script
# além disso MONTA de verdade com a ABI real (PUSH R15/[R15+#N]/CALLR -
# ver tests/../simulador/firmware_min/port_real_abi.py, que só converte
# JMPR/CALLR pra formas absolutas porque simulador/c166asm.py não é
# relativo-de-8-bits-safe pra funções grandes, sem tocar a lógica) e
# RODA no simulador, conferindo o valor numérico certo - a garantia mais
# forte de que o padrão "valor vivo através de chamada" continua correto
# mesmo que o texto do golden mude por outro motivo legítimo no futuro.
#
# Uso: call_liveness_runtime_test.sh <c167cc-binário>
set -euo pipefail
BIN="$1"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
SIMDIR="$ROOT/simulador"
PORT="$SIMDIR/firmware_min/port_real_abi.py"
ASM="$SIMDIR/c166asm.py"
SIM="$SIMDIR/c166sim.py"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

"$BIN" --dump-asm "$ROOT/examples/call_liveness.c" > "$WORK/raw.asm"
python3 "$PORT" "$WORK/raw.asm" > "$WORK/ported.asm"
{
  echo "    CALLA UC, call_liveness"
  echo "HALT_LOOP:"
  echo "    JMPA UC, HALT_LOOP"
  cat "$WORK/ported.asm"
} > "$WORK/driven.asm"

( cd "$SIMDIR" && python3 c166asm.py "$WORK/driven.asm" "$WORK/driven.bin" ) > "$WORK/asm.log"

# IN_X=10, IN_V=20: f(10,20) = (10+20) ^ 0x1234 = 30 ^ 4660 = 4650;
# v = 20 + 4650 = 4670 - conferido com `python3 -c "print((10+20)^0x1234,
# 20+((10+20)^0x1234))"`, mesma semântica u16 do C (sem overflow aqui).
EXPECTED=4670
OUT="$(cd "$SIMDIR" && python3 c166sim.py "$WORK/driven.bin" --syms="$WORK/driven.asm" IN_X=10 IN_V=20 \
  | grep -E '^\s*OUT\s' | awk -F'= ' '{print $2}' | tr -d ' ')"

if [ "$OUT" != "$EXPECTED" ]; then
  echo "MISMATCH: OUT esperado=$EXPECTED atual=$OUT (bug de valor vivo através de chamada voltou?)"
  exit 1
fi
echo "OK: OUT=$OUT"
