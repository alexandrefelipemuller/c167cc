# Assembly syntax and provenance

## Source

The only primary source available in this repository for the C167
instruction set is `../manual_3286A/c167cr_userguide.pdf` ("C167CR
Derivatives User's Manual", V3.1, 2000-03). No C166/C167 assembler,
disassembler, or binutils/LLVM backend was found installed on this
machine (checked: `gcc`, `flex`, `bison`, `cmake`/`meson`/`ninja`,
`pkg-config`; no `as166`, no LLVM C166 target, no local Keil/Tasking
toolchain). Because of that, the syntax below is validated against the
user's manual text only - **not** against a real assembler. Golden tests
(`tests/golden/*.asm`) pin the current output; if a real C166 assembler
becomes available, running it over the generated `.asm` files is the next
validation step (see [limitations.md](limitations.md)).

## Mnemonics: directly attested in the manual

Chapter 23, "Instruction Set Summary", of the user's manual explicitly
lists these mnemonics (grouped by instruction class) - this is where
`c167_instr_table` in `src/target/c167/instructions/isa.c` is drawn from:

`ADD ADDB ADDC ADDCB SUB SUBB SUBC SUBCB MUL MULU DIV DIVU DIVL DIVLU CPL
CPLB NEG NEGB AND ANDB OR ORB XOR XORB CMP CMPB CMPI1 CMPI2 CMPD1 CMPD2
BFLDH BFLDL BSET BCLR BMOV BMOVN BAND BOR BXOR BCMP SHR SHL ROR ROL ASHR
PRIOR MOV MOVB MOVBS MOVBZ PUSH POP SCXT JMPA JMPI JMPR JMPS JB JNB JBC
JNBS CALLA CALLI CALLR CALLS PCALL TRAP RET RETS RETP RETI SRST IDLE
PWRDN SRVWDT`.

This compiler currently only ever *emits* a subset of that list: `MOV
MOVB MOVBS MOVBZ ADD SUB MUL MULU DIV DIVU CMP AND OR XOR CPL NEG SHL SHR
JMPR CALLR CALLI RET RETI PUSH POP`. All of them appear above.
Instructions the compiler does not yet need (bit instructions, `BFLDx`,
`CALLA`, `PCALL`, `TRAP`, system-control instructions, etc.) are
intentionally left unimplemented rather than guessed at.

`CALLI` (emitted for calls through a function pointer) is the one
exception to "operand syntax only from the manual's own examples" below:
the manual excerpt names the mnemonic but its operand table isn't in the
extracted text, so this compiler always emits `CALLI cc_UC, Rw` on the
standard C166/ST10/C167-family convention (condition code, then the word
register holding the target address) - see the comment next to its
`c167_instr_table` entry in `isa.c`. Treat this one as
standard-C166-convention, not manual-verbatim, same caveat as the
`[Rd+#offset]` addressing mode below.

Operand syntax examples directly present in the manual's own code
listings (not invented) include: `MOV R0, #1234H` (register, immediate),
`CMP R1, [R0+]` (register, indirect-with-post-increment), `MOV MDH, R1` /
`MOV R3, MDL` (the `MDH`/`MDL` multiply/divide register pair), `MOV SP,
#0F802H` (direct `SP` manipulation), `SUB SP, #10D` / `ADD SP, #10D`
(stack reservation/release idiom used for this compiler's frame
prologue/epilogue), and `JMPR cc_xx, label` (conditional relative jump
with a `cc_` condition-code prefix).

## Addressing modes used by this backend

- **Register direct**: `Rd`, e.g. `MOV R0, R1`.
- **Immediate**: `#data`, e.g. `MOV R0, #10`.
- **GPR-indirect**: `[Rd]`, e.g. `MOV R0, [R1]` (used for pointer
  dereference and array element access).
- **GPR-indirect with 16-bit offset**: `[Rd+#offset]`, used for
  frame-relative local-variable/parameter access
  (`MOV R0, [R15+#4]`). This is the standard C166-family "indirect with
  16-bit offset" addressing mode; the exact worked examples in the
  extracted manual text use the related post-increment form (`[R0+]`)
  rather than this literal `[Rd+#offset]` spelling, so treat this
  specific operand spelling as *standard-C166-convention*, not
  manual-verbatim, until cross-checked against a real assembler or the
  C166 Family Instruction Set Manual.
- **Direct (symbolic)**: a bare label/symbol name, e.g. `MOV R0, rpm`,
  used for `@ram`/`@rom` globals resolved via `EQU` (see
  [memory-model.md](memory-model.md)). This assumes the symbol's address
  is reachable through the currently active data page - the compiler does
  not emit `EXTP`/DPP setup.

## Condition codes (`cc_xx`)

`cc_UC` (unconditional), `cc_SGT`, `cc_V`, and `cc_NV` appear verbatim in
the extracted user's manual text (system-programming and code-example
sections, e.g. `JMPR cc_SGT, LOOP`, `JMPR cc_V, ERROR`, `JMPR cc_NV,
COPYL`).

The complete 16-entry condition-code table has since been cross-checked
against `../simulador/c166asm.py`'s `CC_MAP` - an independent real
assembler for this CPU, built and validated in a separate session against
actual Copa Clio firmware disassembly (see `../simulador/README.md`) and
against `../ferramentas_disassembly/c166dis.py`'s opcode table. That is a
stronger source than the "standard naming convention" this doc originally
relied on, and it corrected a real bug here: **there is no `cc_EQ`,
`cc_NE`, `cc_ULT`, or `cc_UGE`** in the real encoding. The actual 16 codes
(hardware nibble value in parentheses) are:

`UC`(0x0) `NET`(0x1) `Z`(0x2) `NZ`(0x3) `V`(0x4) `NV`(0x5) `N`(0x6)
`NN`(0x7) `C`(0x8) `NC`(0x9) `SGT`(0xA) `SLE`(0xB) `SLT`(0xC) `SGE`(0xD)
`UGT`(0xE) `ULE`(0xF)

Equality is tested via the `Z` flag (`cc_Z`/`cc_NZ`), and unsigned
less-than/greater-or-equal via the `C` (carry/borrow) flag (`cc_C`/
`cc_NC`) - there is no dedicated "unsigned less-than" mnemonic distinct
from carry. `src/target/c167/codegen/codegen.c`'s `cc_for()` was updated
to emit `cc_Z`/`cc_NZ`/`cc_C`/`cc_NC`/`cc_SLT`/`cc_SGT`/`cc_SLE`/`cc_SGE`/
`cc_UGT`/`cc_ULE` accordingly - it no longer needs unsigned-less-than or
unsigned-greater-or-equal mnemonics because `C`/`NC` cover exactly those
cases. `cc_NET` is not currently used by the backend.

## Directives

`.section .text`, `.section .bss`, `.global <name>`, `EQU`, and `DS` are
generic assembler directives chosen for readability of this MVP's output;
they are **not** taken from the user's manual (which documents the CPU,
not a specific assembler's directive syntax) and should be considered
placeholders to be reconciled with whatever real C166 assembler is
adopted in a later phase.

## Labels

Local labels use `.L<function>_<tag>_<counter>` (e.g.
`.Lcalculate_if_then_0`), built deterministically from the source
function name and a per-function monotonically increasing counter
(`src/ir/ir_build.c`). The same input always produces the same labels
(see the `determinism` test in `tests/`).
