# C167 backend

## Instruction database

`include/c167cc/c167_target.h` declares `C167Instr` and
`src/target/c167/instructions/isa.c` defines the single table of mnemonics
the backend is allowed to emit (`c167_instr_table`). Every mnemonic in
that table is taken from the "C167CR Derivatives User's Manual" chapter
23 "Instruction Set Summary" (`manual_3286A/c167cr_userguide.pdf`); see
[assembly-syntax.md](assembly-syntax.md) for exact provenance per
mnemonic. `c167_isa_lookup()` lets other code (and a future assembler)
query it instead of duplicating mnemonic strings.

## Register allocation

`src/target/c167/registers/regalloc.c` implements a small **linear-scan**
allocator over a single function's flattened IR instruction stream:

1. Compute each virtual register's live range as `[first definition,
   last use]` by scanning the instruction list once.
2. Sort virtual registers by their live-range start.
3. Walk them in order, keeping an `active` list of currently-live
   intervals. On each new interval: expire intervals that have ended,
   then either take a free register from the fixed 7-register pool
   (`c167_temp_pool` = `R0-R3, R8-R10`), or - if the pool is full - spill
   the active interval with the furthest end point (classic linear-scan
   spill heuristic), assigning it a stack slot instead.

Spilled virtual registers are reloaded into a scratch register
(`R11`/`R12`, see [abi.md](abi.md)) immediately before use and stored back
immediately after definition. This is simple and always correct, at the
cost of extra `MOV`s around a spilled temporary - acceptable given the
project's stated priority order (correctness > simplicity > asm quality >
performance).

Locals and parameters are **not** register-allocated: they always live in
the stack frame and are loaded/stored explicitly by the IR
(`IR_LOAD_SYM`/`IR_STORE_SYM`). Only the transient values produced while
evaluating expressions go through the allocator. This keeps the allocator
simple and keeps register pressure low in practice.

## Code generation

`src/target/c167/codegen/codegen.c` walks each function's IR instruction
list once and emits a matching sequence of `AsmLine`s. Notable
translations:

- `IR_BINOP` for `==/!=/</>/<=/ >=` becomes a `CMP` followed by a
  `JMPR cc_xx` / fallthrough sequence that materializes a 0/1 boolean,
  since the C167 exposes comparisons as condition flags rather than a
  set-on-condition instruction.
- `IR_BINOP` `*` becomes `MULU`/`MUL` (result low word read back from
  `MDL`); `/` and `%` become `MOV MDL, ...` + `DIVU`/`DIV` (quotient in
  `MDL`, remainder in `MDH`), following the dividend/divisor convention
  shown in the user's manual's own code examples.
- `IR_CJMP` becomes `CMP reg, #0` + `JMPR cc_NE, true` + (`JMPR cc_UC,
  false` unless the false-label immediately follows, in which case the
  fallthrough jump is elided).
- `IR_CALL` marshals arguments into `R4-R7`, emits `CALLR`, and moves the
  result out of `R0`.

Every emitted instruction is checked against the instruction database
implicitly by construction (codegen only ever calls `emit_raw` with
mnemonics present in `c167_instr_table`); nothing here invents an
undocumented mnemonic or operand form.

## Optimizer

`src/optimizer/optimizer.c` runs, per function, in this order:

1. **Constant folding** - `IR_BINOP`/`IR_UNOP` instructions whose operands
   are both known constants are rewritten to `IR_CONST`.
2. **Copy propagation** - `IR_MOV` and no-op `IR_UNOP` (cast) targets are
   recorded as aliases and operand references are rewritten through the
   alias chain.
3. **Dead code elimination** - any pure, side-effect-free instruction
   whose destination is never used after propagation is dropped.
4. **Unreachable-code stripping** - instructions between an unconditional
   `RET`/`JMP` and the next label can never execute and are removed.

This is intentionally a simple, single-pass-per-function optimizer, not a
general SSA-based pipeline - consistent with the project's "don't
over-engineer" priorities.
