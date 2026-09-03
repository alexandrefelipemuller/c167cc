# Known limitations

This is a deliberately scoped MVP. Priority order for this phase was
**correctness > simplicity > testability > readability of the generated
assembly > optimization**.

## Fixed bugs (kept here for history)

- **Silent label truncation in `IR_JMP`/`IR_CJMP` codegen** (found
  02/09/2026, integrating with the sibling `Sirius32/` project — a
  regression suite there compares every compiled leaf routine's simulated
  behavior against the real firmware binary, function by function).
  `fmt_label()` (`src/ir/ir_build.c`) embeds the FULL function name into
  every generated label (`.L<fn>_<tag>_<counter>`) for `if`/`while`/`for`/
  short-circuit `&&`/`||`/ternary. For a function with a long, descriptive
  name (common when the C is itself a translation of disassembled
  firmware routines, e.g. `rotina_validador_condicoes_ignicao_detonacao`,
  44 chars), the label easily exceeds 60 characters. `src/target/c167/
  codegen/codegen.c`'s `IR_JMP`/`IR_CJMP` cases formatted `"cc_UC, %s"` /
  `"cc_NZ, %s"` into fixed buffers of 64/96 bytes with `snprintf` — which
  truncates silently (no error, no warning at the call site) instead of
  failing loudly. The truncation cut off exactly the trailing `_<counter>`
  suffix, producing a `JMPR` to a label that is never defined anywhere
  (only the correctly-suffixed variants exist) — `c166asm.py` (sibling
  simulator/assembler) failed to resolve the symbol at assemble time.
  Fixed by widening the buffers to 256 bytes (comfortable margin over any
  realistic function name) in both `IR_JMP` and `IR_CJMP`, plus the two
  comparison-operator (`EQ`/`NE`/`LT`/...) and unary-`!` cases that
  `-Wformat-truncation` flagged with the same risk (128 bytes, since those
  labels use a short fixed tag + counter, not the full function name).
  Verified: the specific reproduction case (isolated from
  `Sirius32/core/flags/lote_r_final_flags_e_estado.c`) now emits correctly
  suffixed labels; `meson test` still passes the same 15/16 (the 1
  pre-existing failure, `sim-calculate_global`, reproduces identically
  with or without this fix — confirmed unrelated, an unsupported opcode
  in `c166sim.py`, not a codegen regression); Sirius32's regression suite
  went from 71/72 to 72/72 exact matches against the real firmware
  binary.

- **Widening 16x16->32 multiply always discarded the high word** (found
  02/09/2026, same cross-checking method as the bug above). `uint32_t
  produto = a * b;` compiled to a plain `MOV d, MDL`, silently returning
  `0` for the high 16 bits of any product needing more than 16 bits.
  Root cause: this compiler has no real 32-bit value support anywhere in
  the pipeline (every value is one 16-bit-sized vreg; `IR_STORE_SYM` and
  friends always move exactly one word regardless of the symbol's true
  byte size). Saturating add/sub already used in the sibling project
  happened to work anyway (the 16-bit-truncated result is already what a
  range-comparison overflow check needs), but multiplication has no such
  luck — the high word carries information the 16-bit view simply doesn't
  have. Real register-pair support across the whole IR/codegen was
  considered and explicitly rejected as out of scope (large change, real
  regression risk to the 16-bit pipeline that already works — see the
  user's own choice via AskUserQuestion in that session). Fixed with a
  **narrow** special case instead of general 32-bit arithmetic:
  - `IR_MUL32_STORE_SYM` (`try_gen_widening_mul_store_sym()` in
    `src/ir/ir_build.c`): matches `dst_of_32bit_type = a * b` exactly
    (direct assignment to an already-declared 32-bit variable/global) and
    emits `MULU`/`MUL` followed by two `MOV`s writing MDL/MDH straight to
    the destination's low/high words.
  - `IR_SHR32_SYM` (`try_gen_shr32_sym()`, same file): matches
    `some_32bit_var >> N` for a constant `N` in `[0,31]` and synthesizes
    the same SHL/SHR/ADD sequence the real disassembly uses to pull a
    scaled slice out of a 32-bit product (`N==16` is just the high word;
    `N<16` is `(hi << (16-N)) + (lo >> N)`; `N>16` is `hi >> (N-16)`).

  Outside those two exact shapes, 32-bit arithmetic still falls into the
  old (documented) bug rather than risking a miscompile of an unforeseen
  case. Also uncovered a latent bug in the sibling `../simulador/`
  assembler while validating this: `MULU` was unconditionally renamed to
  `MUL` by `../simulador/firmware_min/port_real_abi.py` (comment claimed
  only the signed mnemonic was assemblable), which silently changes the
  result whenever an operand has bit 15 set — invisible while only the
  low word (MDL) was ever read (`MUL` and `MULU` agree there), broken as
  soon as the high word (MDH) is read too. Fixed by adding real `MULU`
  support to `../simulador/c166asm.py` (opcode `0x1B` — `c166sim.py`
  already executed it correctly, only the assembler was missing it) and
  removing the rename in `port_real_abi.py` (the `DIVU`→`DIV` rename
  there is a separate, still-unfixed limitation, left alone). Verified:
  the 4 `research/biblioteca_aritmetica/*saturada*` routines that had been
  blocked since the pilot now produce correct results (checked via
  Sirius32's `scripts/rodar_funcao.py` against a Python reference
  computation, both the normal and saturating branch of each); `meson
  test` unchanged at 15/16 (same pre-existing, unrelated failure);
  Sirius32's `scripts/regressao_core.py` unchanged at 72/72.

## Not implemented at all (by design, this phase)

- Assembler, linker, object files, ELF, relocations, machine-code
  encoding, binary/HEX/S-record output, flashing, bootloader. The
  compiler's product is `.asm` text only.
- `float`/`double`/`long long`, typedefs, dynamic memory, threads,
  variadic functions, a standard library.

`enum`, `struct`, `union`, and function pointers - the whole original
"c167cc doesn't support yet" list from this compiler's first version -
are now all supported; see
[c-language.md#enums](c-language.md#enums),
[c-language.md#structs](c-language.md#structs),
[c-language.md#unions](c-language.md#unions), and
[c-language.md#function-pointers](c-language.md#function-pointers) for
exactly what's checked and what isn't (struct/enum/union definitions are
top-level only; calls through a function pointer aren't arity/type
checked against its declared signature). Notably, structs and unions
**cannot** be passed/returned by value or assigned/copied as a whole
(`a = b;`) - this compiler's IR represents every value as one
register-sized virtual register, so those operations are rejected at
compile time with a clear error rather than silently miscompiled;
pointers to them work fully.

## Implemented but scoped down

- **32-bit integers** (`int32_t`/`uint32_t`) can be declared and
  loaded/stored, but arithmetic (`+ - * / etc.`) on them is not lowered
  correctly by the backend in general - it treats every scalar operation as
  16-bit. Two narrow exceptions were special-cased (see "Fixed bugs"
  above, `IR_MUL32_STORE_SYM`/`IR_SHR32_SYM`): `dst32 = a * b` (direct
  assignment of a widening multiply to a 32-bit variable) and
  `some32bitvar >> N` for constant `N`. Everything else - `+`, `-`, `/`,
  32-bit values threaded through anything but a direct-assign or a
  constant-shift-right, function arguments/returns - is still silently
  wrong. Avoid general 32-bit arithmetic until this is addressed.
- **Function arguments**: only up to 4 word-sized arguments are
  supported (passed in `R4-R7`, see `docs/abi.md`). Calling or defining a
  function with more raises a compile error rather than silently spilling
  arguments to the stack.
- **Memory segmentation**: only near (16-bit offset) pointers are
  generated; there is no DPP/page switching code, so `@ram`/`@rom`
  symbols must be reachable through whatever data page is active at the
  point of use. Far/huge pointers are not supported.
- **Interrupts** (`@interrupt(n)`): the generated prologue/epilogue is
  the same as a normal function (save/restore `R15`, use `RETI`); it does
  **not** save/restore the full register set, `PSW`, or install the
  vector into an interrupt vector table. Vector-table wiring is left to
  the linker phase.
- **`const`/`volatile`**: parsed and accepted, but only `@ram`/`@rom`
  actually change codegen; `const` does not yet enforce read-only access
  and `volatile` does not yet suppress optimizer reordering (the
  optimizer does not currently reorder/merge memory accesses at all, so
  this is not observably wrong today, but it is not a designed
  guarantee).
- **`switch`**: lowered as a linear chain of compare-and-branch
  instructions, not a jump table - correct, not fast.
- **Register allocation**: a simple linear-scan allocator over a small
  fixed pool (`R0-R3, R8-R10`); spills always go through dedicated
  scratch registers rather than being coalesced or rematerialized. Fine
  for small functions; will produce more `MOV`s than necessary for large
  ones.
- **Assembly directive syntax** (`.section`, `.global`, `EQU`, `DS`):
  reasonable placeholders, not verified against a specific real C166
  assembler - see `docs/assembly-syntax.md`.
- **Condition-code mnemonics**: most `cc_xx` codes follow the standard
  C166 naming convention but were not individually found in the excerpt
  of the user's manual available in this repository - see
  `docs/assembly-syntax.md` for exactly which ones are manual-verbatim.

## Validation

No general-purpose C166/C167 assembler or disassembler was found on this
machine when this project started. A separate session has since built one
in `../simulador/` (`c166asm.py` + `c166sim.py`), a real opcode-encoding
assembler and instruction-level simulator validated against actual Copa
Clio ECU firmware disassembly (see `../simulador/README.md`). Output
correctness is checked via:

- Golden tests (`tests/golden/*.asm`, run via `meson test`) that pin the
  exact generated assembly for representative inputs.
- A determinism test ensuring identical input always produces identical
  output.
- Manual cross-referencing of every mnemonic/operand form against the
  user's manual (see `docs/assembly-syntax.md`).
- **Simulator cross-validation** (`tests/sim_validate.sh`, `meson test`
  targets `sim-*`): compiles a small `examples/*_global.c` program,
  ports it into `../simulador`'s dialect with `tests/port_to_toy_asm.py`,
  assembles it with the real `c166asm.py`, runs it on `c166sim.py`, and
  asserts the resulting memory values match the expected computation
  (e.g. `fatorial_global` with `NUMERO=5` must produce `RESULTADO=120`).
  This is the strongest validation signal in the project so far: it
  already caught and fixed a real bug (see below).

`../simulador`'s assembler implements a genuine but intentionally small
dialect (no `.section`/`.global`/`EQU`/`DS` directives, and critically
**no `[Rw+#offset]` indirect-with-16-bit-offset addressing** - see its own
`README.md`). Since that addressing mode is exactly what c167cc's
stack-frame ABI uses for every parameter and local variable
(`docs/abi.md`), c167cc's raw output cannot be fed to it unmodified.
`tests/port_to_toy_asm.py` performs a small, documented, purely mechanical
transformation (drop prologue/epilogue, remap `[R15+#N]` to a flat
variable name, rename `MULU`/`DIVU` to `MUL`/`DIV`) to test the underlying
arithmetic/control-flow instruction selection in isolation from the frame
mechanism. It cannot validate: the stack frame/ABI itself, function calls
(`CALLR`), or array/pointer dereference (`[Rw]` with no offset - also
unsupported by that assembler). Extending that assembler's addressing
modes was considered out of scope for this session; it is a natural next
step if broader validation is wanted later.

### Bug found and fixed via this validation

Cross-checking against `../simulador/c166asm.py`'s `CC_MAP` - itself
derived from real hardware opcode encoding and cross-validated against
firmware - revealed that this compiler's condition-code mnemonics were
wrong: it emitted `cc_EQ`/`cc_NE`/`cc_ULT`/`cc_UGE`, none of which exist
in the real instruction set. The real C166/C167 encodes equality via the
`Z` flag (`cc_Z`/`cc_NZ`) and unsigned less-than/greater-or-equal via the
`C` (carry/borrow) flag (`cc_C`/`cc_NC`), with no separate mnemonics for
those cases. Fixed in `src/target/c167/codegen/codegen.c`'s `cc_for()`;
see `docs/assembly-syntax.md` for the full corrected table.

## Suggested next steps (out of scope for this phase)

- Wire the frame-pointer-relative addressing mode against a real
  assembler/Instruction Set Manual to confirm the exact operand spelling.
- Implement 32-bit arithmetic (as pairs of 16-bit operations).
- Support stack-passed arguments beyond 4.
- Proper interrupt context save/restore and a vector table.
- An assembler + linker + object format, per the project's longer-term
  roadmap (`C → IR → C167 backend → .asm → C167 assembler → object →
  linker → binary`).
- A struct-by-value calling convention, if ever needed (would require
  redesigning the IR's one-value-per-vreg model, or at minimum a
  hidden-pointer-argument lowering pass - not a small change).
- Verify `CALLI`'s exact operand syntax/encoding against a real
  assembler or the full Instruction Set Manual - the excerpt available
  in this repo names the mnemonic but not its operand table (see the
  comment in `src/target/c167/instructions/isa.c`); this compiler
  currently assumes the standard C166-family `CALLI cc, Rw` form.
