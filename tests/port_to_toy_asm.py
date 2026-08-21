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
  - renames MULU/DIVU to MUL/DIV (the toy assembler only implements the
    signed mnemonics; for the small positive values used in these checks
    the result is identical);
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
        code = code.replace("MULU", "MUL").replace("DIVU", "DIV")
        code = re.sub(r"\.L(\w+)", r"L\1", code)
        out.append(code)

    out.append("NOP")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} <c167cc-asm-file> <function-label>")
    with open(sys.argv[1]) as f:
        text = f.read()
    sys.stdout.write(port(text, sys.argv[2]))
