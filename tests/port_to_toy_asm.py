#!/usr/bin/env python3
"""Mechanically ports one c167cc-generated function into the small
`../simulador/c166asm.py` dialect, for cross-validation purposes only.

c167cc's own calling convention (frame pointer in R15, parameters/locals
addressed as `[R15+#offset]`, MULU/DIVU) is not something the hand-rolled
simulador/c166asm.py implements - it has no indirect-with-offset addressing
mode at all (see docs/limitations.md and the session notes). This script
does NOT change what c167cc emits; it rewrites a *copy* of one function's
body so the underlying arithmetic/control-flow instructions - the actual
question being validated - can be assembled and simulated by that tool:

  - drops the prologue/epilogue (PUSH R15 / SUB SP / MOV R15,SP / ADD SP /
    POP R15 / RET) since there is no caller here and no stack frame to
    manage in this flat single-function harness;
  - remaps every `[R15+#N]` operand to a synthetic flat variable name
    `L<N>`, which is semantically identical to a stack slot for a
    straight-line/single-invocation run (same distinct memory location per
    offset, just not on the call stack);
  - MULU is NOT renamed to MUL anymore (BUG-6/BUG-11 of Sirius32,
    01/10/2026): c166asm.py/c166sim.py have had a real MULU since
    02/09/2026, and the signed MUL gives a wrong MDH (high word of the
    32-bit product) as soon as an operand has bit 15 set - exactly what the
    sim-wide32_* tests need to check. DIVU is NOT renamed either (BUG-4 of
    Sirius32, 24/09/2026): c166asm.py has encoded DIVU/DIVL/DIVLU since
    03/09/2026, and renaming it to the signed DIV hid unsigned-division
    behaviour (e.g. 50000/7 came out as -15536/7);
  - strips the leading '.' from local labels (the toy assembler's label
    regex is `\\w+`, which does not include '.');
  - replaces the trailing RET with NOP, which is c166sim.py's convention
    for "natural end of program".
"""
import re
import sys


def port(asm_text: str, func_label: str) -> str:
    lines = asm_text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == f"{func_label}:":
            start = idx + 1
            break
    if start is None:
        raise SystemExit(f"function label '{func_label}:' not found")

    body = []
    for line in lines[start:]:
        stripped = line.split(";", 1)[0].strip()
        if stripped == "RET" or stripped == "RETI":
            break
        body.append(line)

    out = []
    for line in body:
        code = line.split(";", 1)[0].strip()
        if not code:
            continue
        mnemonic = code.split(None, 1)[0]
        if mnemonic in ("PUSH", "POP") and "R15" in code:
            continue
        if mnemonic in ("SUB", "ADD") and "SP," in code:
            continue
        if mnemonic == "MOV" and code.replace(" ", "") == "MOV R15,SP".replace(" ", ""):
            continue
        code = re.sub(r"\[R15\+#(\d+)\]", r"L\1", code)
        code = re.sub(r"\.L(\w+)", r"L\1", code)
        out.append(code)

    # c166sim.py no longer treats NOP as "end of program" (that heuristic was
    # removed 20/08/2026, see ../simulador/README.md "Conserto do heurística
    # NOP" - NOP is now a real 2-byte no-op instruction). A bare trailing NOP
    # here made execution fall off the end into the variable/data area and
    # execute garbage bytes as opcodes, eventually landing on an unsupported
    # or SFR-clobbering opcode. Use the same self-referential-loop halt
    # convention c166sim.py's `run()` actually detects (pc stuck for
    # _HALT_THRESHOLD steps).
    # BUG-13 da Sirius32 (01/10/2026): um global de mais de 2 bytes (array,
    # struct) referenciado pelo corpo precisa do seu tamanho REAL no
    # montador de brinquedo - sem isto ele vira uma variável de 1 word e o
    # acesso por ponteiro ([Rn] com offset de campo/índice) cai em cima da
    # variável vizinha. O tamanho vem do próprio `NOME: DS N` que o c167cc
    # emite na .bss; `RESERVE NOME, #N` é a diretiva de c166asm.py pra isso
    # (não gera código). Globais de até 2 bytes ficam como sempre.
    body_text = "\n".join(out)
    reserves = []
    for line in lines[:start]:
        m = re.match(r"^(\w+):\s+DS\s+(\d+)\b", line)
        if m and int(m.group(2)) > 2 and re.search(r"\b%s\b" % re.escape(m.group(1)), body_text):
            reserves.append(f"RESERVE {m.group(1)}, #{m.group(2)}")
    out = reserves + out

    out.append("PORT_HALT:")
    out.append("JMPR cc_UC, PORT_HALT")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} <c167cc-asm-file> <function-label>")
    with open(sys.argv[1]) as f:
        text = f.read()
    sys.stdout.write(port(text, sys.argv[2]))
