# Known limitations

This is a deliberately scoped MVP. Priority order for this phase was
**correctness > simplicity > testability > readability of the generated
assembly > optimization**.

## Fixed bugs (kept here for history)

- **A narrowing cast (`uint16_t`->`uint8_t` and similar) used INSIDE a
  larger expression (not as a direct assignment `uint8_t x = expr;`,
  which already worked) silently discarded the truncation and used the
  full 16-bit value instead** (found 07/09/2026, promoting
  `atualiza_acumulador_carga_byte` in the sibling Sirius32 project -
  `core/motor_geral/atualiza_acumulador_carga_amostras.c` - where
  `acumulador_carga + valor_anterior - (uint8_t)novo_valor` gave a
  different result than materializing the cast into its own variable
  first, `uint8_t novo_valor_byte = (uint8_t)novo_valor;` then using
  `novo_valor_byte` in the same expression - those should be equivalent
  and were not). This is the 4th cast-related bug found in this session,
  distinct from the other three: bug #1 (`a3f83d9`) was about WIDENING
  sign-extension (`(int16_t)(int8_t)x`); bug #2 (`9d8df6f`) was the
  register allocator/liveness across `CALL`; bug #3 (`1bddcb1`) was about
  zero-extension on PARAMETER/signed-byte reload (`IR_LOAD_SYM`/
  `IR_LOAD_MEM`). This one is about NARROWING (`uint16_t`->`uint8_t`)
  used as an intermediate operand, not a final store.
  **Root cause (two independent bugs, one in the optimizer, one in
  codegen - each alone would have been enough to cause the observed
  miscompilation)**:
  1. `src/optimizer/optimizer.c`'s `cast_is_pure_copy()` treated ANY
     narrowing cast (destination size 1, regardless of source size or
     signedness) as a "pure copy" safe for alias-propagation - the same
     mechanism that let a widening sign-extend cast get incorrectly
     aliased away before bug #1 was fixed, except this predicate was
     never updated for the opposite (narrowing) direction. Once a
     `IR_UNOP`/`OP_ASSIGN` narrowing instruction is marked a pure copy,
     the optimizer sets `alias[dst] = src` and every later use of the
     cast's destination vreg is rewritten to use the ORIGINAL, untruncated
     16-bit source vreg directly - the narrowing `IR_UNOP` instruction
     itself typically becomes dead code and is deleted entirely by the
     same pass's DCE step. The cast vanishes from the IR before codegen
     ever sees it.
  2. Even had (1) not existed, `src/target/c167/codegen/codegen.c`'s
     `IR_UNOP`/`OP_ASSIGN` case (the same `case` block fixed for widening
     in bug #1) had no code path at all for narrowing to a 1-byte
     destination (`i->size == 1`): it just emitted a plain `MOV d, a` and
     nothing else, copying the full 16-bit source register verbatim. This
     backend represents every 1-byte value as a 16-bit register (byte in
     the low half, high half either zero or sign-replicated depending on
     signedness - see the `IR_LOAD_MEM`/`IR_LOAD_SYM`/`IR_FARREAD8_SYM`
     comments in the same file), so without an explicit mask/sign-extend
     after the `MOV`, the "narrowed" register still held the full,
     untruncated 16-bit value.
     `uint8_t x = expr;` (direct assignment) was unaffected by either bug
     in practice: (1) still aliased the vreg away, but the final consumer
     was a `STORE_SYM`/`STORE_MEM` of size 1, which uses `MOVB` and only
     ever writes the low byte to memory regardless of what garbage sits
     in the high byte of the source register - so the truncation happened
     "for free" at the store, independent of the (missing) cast codegen.
     The bug only became observable once the cast's result was consumed
     as an OPERAND of another arithmetic instruction instead of being
     stored directly.
  **Fix**: `cast_is_pure_copy()` now returns false whenever
  `cast_src_size(i) > i->size` (source wider than destination - any
  narrowing), in addition to the existing widening-sign-extend case;
  `apply_cast_value()` (used for the optimizer's own constant-fold of a
  compile-time-constant cast) now takes the destination size/signedness
  too and truncates+re-extends accordingly, instead of only handling the
  source side. In codegen, the `IR_UNOP`/`OP_ASSIGN` case now has a
  `narrow_to_byte` branch (`i->size == 1`) mirroring the existing
  widening branch: `AND d, #0x00FF` when the destination type is
  unsigned, or the same `SHL #8`/`ASHR #8` sign-extend trick when it is
  signed (`(int8_t)` truncation, not just `(uint8_t)`).
  **Validation**: reproduced with a minimal example
  (`examples/narrowing_cast_in_expr_global.c`,
  `OUT = A + B - (uint8_t)B`) - confirmed via `--dump-asm` that the
  pre-fix output had no `AND`/mask between loading `B` and using it in
  the `SUB`, and that running it on `../simulador/c166sim.py` with
  `B=511` (`0x1FF`, so `(uint8_t)B` truncating to `255` actually matters)
  gave `OUT=10` before the fix (i.e. `A + B - B`, the cast fully
  discarded) and `OUT=266` after (`10 + 511 - 255`, correct). Also
  manually checked, both before and after the fix, that: direct
  assignment (`uint8_t x = (uint8_t)expr;`) was already correct and
  remains correct; narrowing to a SIGNED byte inside an expression
  (`A + (int8_t)A`) now sign-extends correctly (confirmed numerically,
  `A=200` -> `(int8_t)200 == -56` -> `OUT=144`); and a fully
  compile-time-constant narrowing cast (`10 + 500 - (uint8_t)500`) still
  constant-folds to the right value (`266`) instead of just skipping the
  fold. New regression test `sim-narrowing_cast_in_expr_global` added to
  `tests/meson.build`. `meson test`: 22/22 -> 23/23, no regressions.
  Production workaround in the Sirius32 project (materializing the cast
  into a separate `uint8_t` variable before use) was then simplified back
  to the direct form and revalidated - see that project's own commit
  history for the outcome.

- **Loading a `int8_t` (or other signed 1-byte) symbol - parameter, local,
  or global - always zero-extended instead of sign-extending, so
  `if (x < 0)` was never true even for a genuinely negative value**
  (found 07/09/2026, in a post-mortem audit of the "register allocator/
  liveness across a call" fix below - see the sibling Sirius32 project's
  `docs/AUDITORIA_REGALLOC_FIX.md`, section "2.". That audit re-derived
  the original `rotina_validador_sensor_32d1e` divergence from first
  principles and found this THIRD, independent bug while confirming the
  other two fixes were sufficient - it wasn't; production code
  (`core/aritmetica/biblioteca_aritmetica_enderecos.c`,
  `biblioteca_aritmetica_soma_saturada_byte_delta_signed_3b7fe`) had been
  working around this exact bug for a while by avoiding an `int8_t`
  parameter entirely: the caller manually sign-extends into a
  `uint16_t` and the callee tests a bit (`& 0x0080`) instead of doing a
  signed comparison - which is what proved the bug was in the parameter
  reload path specifically, not in comparison codegen itself
  (`&`/bitwise tests always worked).
  **Root cause**: `IR_LOAD_SYM` in `src/target/c167/codegen/codegen.c`
  loads any 1-byte symbol (`SYM_LOCAL`/`SYM_PARAM` via `[R15+#N]`, or a
  named global) with `MOVB`, which - like every other byte-load form in
  this backend (see the `IR_LOAD_MEM`/`IR_FARREAD8_SYM` comments just
  above/below it) - only ever touches the destination's low byte; the
  high byte is left with whatever garbage was there before. The existing
  fix for that (found 21/08/2026, same file) was an unconditional
  `AND dst, #0x00FF` right after the `MOVB`, which zero-extends
  correctly for an unsigned byte but was applied unconditionally,
  clobbering the sign of a genuinely negative `int8_t`/other signed byte
  before any later comparison or arithmetic ever saw it. This is the
  same species of bug as the signed cast bug below (byte value gets
  stuck zero-extended because the code that's supposed to widen it
  doesn't look at the source type's signedness) but a different code
  path: a cast in an expression (`(int16_t)(int8_t)x`) versus loading a
  symbol straight out of the stack frame or a named global - the cast
  fix (`a3f83d9`) did not, and could not, cover this, since a plain
  `if (delta < 0)` on an `int8_t` parameter/local/global involves no
  cast expression at all in the IR.
  **Fix**: `IR_LOAD_SYM`'s 1-byte case now checks `i->is_signed`
  (already carried on the instruction, set from `type_is_signed(sym->
  type)` in `src/ir/ir_build.c`, previously computed but unused here):
  when true, it replaces `AND dst, #0x00FF` with the same `SHL dst, #8`
  / `ASHR dst, #8` sign-extend trick used by the cast fix (shifts the
  loaded byte into the high byte, then an arithmetic shift right
  replicates the sign bit back down) instead of the plain zero-extending
  `AND`. Unsigned 1-byte symbols are untouched - they keep the original
  `AND dst, #0x00FF`. The identical bug was also present, and fixed the
  same way, in `IR_LOAD_MEM` (pointer dereference of a signed byte type,
  e.g. `int8_t *p; ... *p < 0`) right next to it, since it shares the
  exact same `MOVB`-only-touches-low-byte problem and already carries
  `i->is_signed` too. `IR_FARREAD8_SYM` (`@ram`/`@far` byte read) was
  left as-is: it does not currently carry a signedness flag on the
  instruction at all, so fixing it would need IR plumbing beyond the
  scope of this fix - it remains a known gap, not yet observed in a real
  routine.
  **Validation**: reproduced first with a minimal example
  (`examples/sign_param_signed_compare_global.c` - a global `int8_t`
  compared with `< 0`, since the toy simulator harness
  (`tests/sim_validate.sh`/`port_to_toy_asm.py`) cannot exercise a real
  function call/parameter - see `docs/limitations.md`'s Validation
  section - but the buggy code path is identical for `SYM_PARAM`,
  `SYM_LOCAL`, and a named global, all going through the same
  `IR_LOAD_SYM` case): confirmed via `--dump-asm` that the pre-fix
  output emitted `AND R0, #0x00FF` right after the `MOVB` reload, and
  that `sim-sign_param_signed_compare_global` (new `meson test` case,
  `IN_S8=140` i.e. `0x8C`/-116, expects `OUT=1`) failed before the fix
  and passes after it; a companion manual check
  (`uint8_t` loaded and cast to `uint16_t`) confirms the unsigned path
  still emits the original `AND #0x00FF` unchanged. `meson test`:
  21/21 -> 22/22, no regressions. The real-world case that motivated
  this (`biblioteca_aritmetica_soma_saturada_byte_delta_signed_3b7fe`,
  file `0x3B7FE`) was then simplified in the Sirius32 project to use a
  natural `int8_t delta` parameter with a direct `if (delta < 0)`,
  recompiled with the fixed `c167cc`, and revalidated against the
  original firmware's numeric output - see that project's own
  `docs/AUDITORIA_REGALLOC_FIX.md` for the outcome.

- **Register allocator never spilled a value that lives across a function
  call, so it could land in a register the call itself destroys** (found
  06/09/2026, in the sibling Sirius32 project's continued investigation
  of `rotina_validador_sensor_32d1e` - `research/sensores_atuadores/
  DUVIDAS.md`, "Rodada seguinte (06/09/2026)". That investigation had
  isolated a 1-line repro - inserting one totally unrelated dead store
  changed a large function's runtime result without changing a single
  byte of `--dump-asm` - and concluded, reasonably from that evidence
  alone, that the bug had to be in the assembler's (`simulador/
  c166asm.py`) label/address resolution, since identical assembly can
  only produce different runtime behavior if something below codegen is
  wrong. That specific theory turned out to be a dead end on inspection
  of `c166asm.py` this session: `sizeof()` for every instruction form is
  fixed regardless of jump distance (`JMPR`/`CALLR` are always 2 bytes,
  `JMPA`/`CALLA` always 4), addresses are computed in one deterministic
  forward pass before any encoding happens, and `port_real_abi.py`
  already converts every `JMPR`/`CALLR` to the absolute `JMPA`/`CALLA`
  forms specifically because the 8-bit-relative forms are too short-range
  for real generated functions (see its own docstring) - so the
  suspected "stale relative-jump target after a 2-pass size change"
  mechanism does not exist in this codebase.
  The real bug was found instead by building a synthetic function from
  scratch (no relation to the ECU project, `~15` locals across `5`
  nested `{}` blocks, several 32-bit multiplies, a handful of `CALLA`s to
  small helper functions - see the investigation notes for the exact
  progression of sizes tried) and bisecting which added statement first
  broke a Python reference model. The minimal trigger is a single
  statement of the shape `v = v + f(x, v)`: the codegen for `IR_CALL`
  itself is fine (`src/target/c167/codegen/codegen.c`), and correctly
  marshals args and moves `R0` into the destination vreg after the
  call - the bug is entirely in `regalloc_run()`
  (`src/target/c167/registers/regalloc.c`), a linear-scan allocator over
  the fixed pool `c167_temp_pool` (`R0-R3, R8-R10`, see `isa.c`) that
  computed each vreg's `[first, last]` liveness interval purely from
  instruction index, with **no notion that an `IR_CALL` instruction
  clobbers the entire pool** (the callee reuses the exact same pool for
  its own temporaries, and the return value convention is `R0` - nothing
  in the pool survives a call). So a vreg like the old value of `v`
  above, live before and after the call, could be assigned a pool
  register that the call overwrote with its own return value before the
  vreg's second use - in the repro, `v`'s old value and the call's return
  value collided in the same physical register, so the subsequent
  `ADD` computed `result + result` instead of `v + result`. This
  produces perfectly well-formed, perfectly plausible-looking assembly
  (no missing spill/reload around the call at all, since the allocator
  never even considered one necessary) - exactly matching the original
  report's observation that `--dump-asm` looked completely correct.
  (Whether this is the exact same root cause as the original
  `32d1e`-triggering divergence was not re-confirmed by re-running
  `32d1e` itself in this session - see the note on that at the bottom of
  this entry's originating investigation - but it is undeniably a real,
  independently-reproduced miscompilation of exactly the reported shape:
  same symptom, "correct-looking assembly, wrong runtime result",
  entirely explained without needing any assembler-level mechanism.)
  Confirmed this was already silently present in the project's own
  pinned golden test: `examples/fatorial_rec.c`'s recursive case
  (`n * fatorial_rec(n - 1)`, `n` live across the recursive call) was
  already hitting this exact bug - `tests/golden/fatorial_rec.asm`
  (before this fix) shows `n`'s value loaded into `R1` before `CALLR`,
  then unconditionally overwritten by `MOV R1, R0 ; function result`
  right after the call, so the following `MULU R0, R1` multiplied the
  recursive call's result by itself instead of by `n` - meaning
  `fatorial_rec(n)` for any `n >= 2` was already wrong before this fix
  (the golden test only pins exact text, it never executed the code, so
  this had never been caught).
  **Fix**: `regalloc_run()` now records every `IR_CALL` instruction's
  index up front and, for each vreg, checks whether any call index falls
  strictly inside its `[first, last]` interval (i.e. the vreg is defined
  before the call and used again after it). Such a vreg is now forced
  into the spill path unconditionally - reusing the exact same
  stack-spill mechanism the allocator already had for ordinary register
  pressure (`res->spilled`/`res->spill_offset`, read back by
  `load_operand`/`dst_target` in codegen) - instead of ever being
  considered for a pool register. No codegen change was needed; the fix
  is entirely inside the liveness/allocation decision.
  **Validation**: `meson test` went from 18/19 to 19/19 with NO test
  changes needed beyond regenerating `tests/golden/fatorial_rec.asm`
  (the pinned text changed because the recursive case now spills `n`
  around the call, as it always should have) - the previously-passing
  17 golden tests and the previously-*failing*
  `sim-calculate_global` case (unrelated failure, pre-existing, see
  below) are untouched; `sim-calculate_global` in fact now passes too
  (19/19 clean), though it's unclear whether that was ever actually
  caused by this bug or is coincidental, since that example has no
  function calls at all - most likely just a stale/flaky golden
  expectation that this session didn't otherwise touch. A new dedicated
  regression pair was added: `examples/call_liveness.c` +
  `tests/golden/call_liveness.asm` (golden, pins the spill-around-call
  assembly shape) and `tests/call_liveness_runtime_test.sh` (executes
  the real ABI - `simulador/firmware_min/port_real_abi.py` +
  `simulador/c166asm.py`/`c166sim.py` - and asserts the actual numeric
  result, `OUT=4670` for `IN_X=10, IN_V=20`, independently computed in
  Python), registered in `tests/meson.build` as
  `golden-call_liveness`/`golden-call_liveness-runtime`.
  A minimal repro was built and iterated on before finding this (see
  investigation notes): a first synthetic function with 5 blocks/~25
  locals/3 straight-line `CALLA`s (no `if`s) passed every test vector
  cleanly - it took adding a conditional (`if (v03 < 5000) { v04 = v04 +
  helper1(v03, v04); }`, i.e. exactly the `v = v + f(x, v)` shape) inside
  a second `{}` block for the divergence to appear, at which point 6 of
  6 non-trivial test vectors mismatched the Python reference in the
  cascading accumulator, matching the qualitative severity ("wrong from
  the first stage on, not an off-by-one") reported for `32d1e`.

- **Signed byte->word cast (`(int16_t)(int8_t)x`) silently zero-extended
  instead of sign-extending** (found 05/09/2026, cross-checking a
  multi-function real firmware routine — `rotina_validador_sensor_32d1e`
  — in the sibling Sirius32 project; see that project's
  `research/sensores_atuadores/DUVIDAS.md`, "Rodada seguinte
  (05/09/2026)", for the original investigation that first hit this as an
  apparently context-dependent "isolated call works, full program
  doesn't" divergence before it was tracked down to this, a plain
  compile-time miscompilation with no dependency on program size at all).
  Two independent bugs stacked to produce this:
  1. `IR_UNOP`/`OP_ASSIGN` (`EXPR_CAST` in `src/ir/ir_build.c`) codegen
     (`src/target/c167/codegen/codegen.c`) computed the right mnemonic
     (`MOVBS` for a signed narrow-to-wide cast, `MOVBZ` otherwise) into a
     local `mn` variable, then **never used it** (`(void)mn;`) - it always
     emitted a plain `MOV`, which just copies the 16-bit vreg unchanged.
     Moot anyway: neither `MOVBS` nor `MOVBZ` were actually usable as
     written - `c166asm.py`'s encoder has no case for `MOVBS` at all, and
     its `MOVBZ` case only accepts a byte-register (`breg`) or memory
     source, never the plain word-register name this codegen always
     passes as `a`.
  2. Independently, `ir_optimize()`'s copy-propagation pass
     (`src/optimizer/optimizer.c`) treated **every** `IR_UNOP`/`OP_ASSIGN`
     (i.e. every cast, of any combination of sizes/signs) as a value-
     preserving alias for propagation and constant-folding purposes. A
     widening signed-byte cast is not a bit-preserving copy (that's
     exactly the point of sign extension), so even after fixing (1), the
     cast instruction itself could be aliased away entirely before ever
     reaching codegen, silently reintroducing the same bug from a
     different layer.
  Root cause of why this was so hard to pin down originally: an
  `IR_UNOP`/`OP_ASSIGN` had no record of the cast's *source* size/sign,
  only the destination's (`i->size`/`i->is_signed`) — nothing at codegen
  or optimizer time could tell "widen from an already-8-bit value" apart
  from "reinterpret a same-width value", so neither layer could special-
  case the one combination (signed byte -> word) that actually needs real
  extension work. Fixed by recording the cast's source size and
  signedness on the instruction itself (`i->imm`, low byte = source size
  in bytes, bit `0x100` = source signedness — deliberately NOT `i->b`,
  which the optimizer's generic alias-resolution pass treats as a vreg id
  for every instruction kind and would silently corrupt if repurposed as
  a plain flag), then: codegen emits `MOV` + `SHL #8` + `ASHR #8` (shift
  the byte into the high half and arithmetic-shift back, replicating the
  sign bit — instructions already fully supported by the real
  assembler/simulator, unlike the dead `MOVBS`/`MOVBZ` path) only for
  that one shape, and the optimizer skips both alias-propagation and
  applies the correct truncate+extend when constant-folding a cast in
  that same shape, leaving every other cast (same-size, unsigned-source,
  narrowing) as the cheap pure-copy it already correctly was. Verified:
  new `examples/signext_byte_global.c` / `sim-signext_byte_global` test
  (`IN=200` i.e. byte `0xC8`, bit 7 set — `(int16_t)(int8_t)200` must be
  `-56`, not `200`); a from-scratch minimal reproduction of the original
  multi-function routine (5 small `combinar()`-shaped helper functions
  plus a driver, assembled and simulated through the REAL calling
  convention — `scripts/compilar_e_montar.py` in the sibling Sirius32
  project, not the flattened single-function `tests/port_to_toy_asm.py`
  harness this compiler's own `sim-*` tests use, which never exercises
  real `[R15+#N]`-framed multi-function `CALLA` programs) went from 8/11
  to 11/11 matching test cases against a Python reference once this fix
  landed, using the *unmodified* `(int8_t)`/`(int16_t)` cast directly
  instead of the bit-test workaround the original session had used;
  `meson test` unchanged at 17/18 (same pre-existing, unrelated
  `sim-calculate_global` failure) plus the 1 new test, so 18/19 overall.

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
  case.

  **Update 03/09/2026**: added a third narrow case, `IR_DIV32_SYM`
  (`try_gen_div32_sym()` in `src/ir/ir_build.c`), for exactly the same
  reason and found investigating the same kind of code (the
  bilinear-interpolation cluster in the sibling Sirius32 project, file
  `0x3AE96-0x3B7FE`, which computes a widening product and immediately
  divides it: `produto = (uint32_t)a * b; resultado = produto / escala;`).
  Matches `ident_of_32bit_type / expr16` or `% expr16` (dividend must be
  an existing 32-bit variable; divisor can be any 16-bit expression,
  unlike the shift case which only accepts a constant; result truncated
  to 16 bits, same restriction as `IR_SHR32_SYM`) and emits `MOV
  MDL,lo(var); MOV MDH,hi(var); DIVLU/DIVL divisor; MOV dst,MDL|MDH`. NOT
  supported: a divisor wider than 16 bits, or a 32-bit quotient assigned
  back to a 32-bit variable. Also fixed two bugs uncovered in the
  reference simulator (`simulador/c166asm.py`) while validating this —
  `DIVU`/`DIVL`/`DIVLU` had no instruction-length table entry (only
  plain `DIV` did) and, worse, the byte-encoder emitted opcode `0x4B`
  (signed 16/16 `DIV`) for all four mnemonics regardless of which one was
  written, silently running the wrong division in the simulator; fixed by
  giving each its real opcode (`DIV=0x4B`, `DIVU=0x5B`, `DIVL=0x6B`,
  `DIVLU=0x7B`, matching what `c166sim.py` already executed). Verified
  with a new `sim-div32_global` test (`examples/div32_global.c`,
  `A=1234,B=777` → `produto=958818` (>65535, so this only passes if MDH
  is genuinely read) → `QUOC=9588,REM=18`, checked against the Python
  reference); `meson test` unchanged (16/17, same pre-existing unrelated
  failure); Sirius32's `regressao_core.py` unchanged at 79/79.

  Also uncovered a latent bug in the sibling `../simulador/`
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
  16-bit. Three narrow exceptions were special-cased (see "Fixed bugs"
  above, `IR_MUL32_STORE_SYM`/`IR_SHR32_SYM`/`IR_DIV32_SYM`): `dst32 = a *
  b` (direct assignment of a widening multiply to a 32-bit variable),
  `some32bitvar >> N` for constant `N`, and `some32bitvar / expr` /
  `some32bitvar % expr` (16-bit divisor, result truncated to 16 bits).
  Everything else - `+`, `-`, 32-bit values threaded through anything but
  those three exact shapes, function arguments/returns - is still
  silently wrong. Avoid general 32-bit arithmetic until this is
  addressed.
- **Function arguments**: only up to 4 word-sized arguments are
  supported (passed in `R4-R7`, see `docs/abi.md`). Calling or defining a
  function with more raises a compile error rather than silently spilling
  arguments to the stack.
- **Memory segmentation**: only near (16-bit offset) pointers are
  generated; there is no general DPP/page switching code, so `@ram`/`@rom`
  symbols must be reachable through whatever data page is active at the
  point of use. General far/huge pointers are still not supported.

  **Investigated 03/09/2026** (bilinear-interpolation cluster in the
  sibling Sirius32 project, which uses `EXTP_S`/`EXTS` before indexed
  loads to reach data outside the current 16-bit window): considered
  adding a general narrow `@far(page)` attribute (same style as
  `@ram(addr)`/`@rom(addr)`, page number a compile-time constant) that
  would emit `EXTP #page, #1` immediately before every load/store through
  the attributed pointer/variable, mirroring `IR_MUL32_STORE_SYM`'s
  "recognize one exact shape" approach. Decided **not** to implement a
  general attribute that session: `EXTP`'s hardware effect ("override the
  data page for exactly the next instruction") only holds if that next
  instruction is genuinely the paired `[Rw]`/`[Rw+#off]` access with
  nothing emitted in between - but this backend's IR is a flat,
  unordered-by-design instruction list (`IR_LOAD_MEM`/`IR_STORE_MEM` in
  `src/target/c167/codegen/codegen.c` take an address already computed
  into a vreg by an arbitrary earlier sequence, and the register
  allocator/spill logic can and does insert extra `MOV`s around any
  instruction to load spilled operands). Making "EXTP right before this
  specific load" a hard invariant for an ARBITRARY `@far`-tagged pointer
  would require either a new IR instruction fused with its own load/store
  for every such pointer, or a post-codegen peephole pass guaranteeing
  adjacency after spilling - both judged too big/risky for a general
  attribute at the time.

  **Implemented 04/09/2026, narrower than the rejected `@far` attribute**:
  `IR_FARREAD16_SYM` (see `include/c167cc/ir.h`) takes exactly the "fused
  IR instruction" route sketched above, but only for ONE fixed shape - a
  call to the compiler-recognized name `c167cc_far_read16(page, off)`
  (16-bit page, 16-bit offset, both runtime values) - lowered directly to
  `EXTP page,#1` immediately followed by `MOV dst,[off]` inside a single
  codegen case (`src/target/c167/codegen/codegen.c`), so nothing the
  register allocator does can ever separate them. This sidesteps the
  general risk above precisely because it never exposes a general `@far`
  pointer type to the rest of the IR - there is no vreg "carrying" a far
  address across instruction boundaries for the allocator to spill around;
  the page and offset are ordinary 16-bit values consumed atomically at
  the one call site. Reading N far words (as file 0x3B488 in the sibling
  Sirius32 project does: 2 consecutive words, `EXTP_S;MOV;ADD;ADDC;
  EXTP_S;MOV;RETS`) is composed at the C source level from N independent
  calls (e.g. `off` and `off+2`) rather than one instruction handling both
  - each call is independently atomic, so no "advance the pointer without
  breaking adjacency" state needs to survive across the boundary. Cross-
  validated against the real firmware routine at file 0x3B488 (8 test
  cases including page/offset boundary values, via a one-off harness
  patching the compiled function into a copy of the real binary and
  running both from the same memory - see
  `core/aritmetica/biblioteca_aritmetica_enderecos.c` in Sirius32).
  Still NOT supported: a general far pointer type usable anywhere an
  ordinary pointer is (assigned to a variable, passed as an opaque
  argument, dereferenced through arbitrary pointer arithmetic) - that
  remains exactly the rejected-attribute scope above, and the pointer/type
  model (`include/c167cc/ast.h`) still has no notion of a memory page.
- **Combined indexed + far addressing** (`[RwindRw]`/`[RwindRwPlus]`
  forms seen in the same firmware cluster): investigated 03/09/2026
  whether array/pointer indexing with a runtime (non-constant) index
  already works in isolation - it does. `arr[i]` for a runtime `i`
  already lowers correctly today via ordinary pointer arithmetic
  (`gen_lvalue_addr`'s `EXPR_INDEX` case in `src/ir/ir_build.c`: `MULU`
  by element size, `ADD` to the base address, then a plain `IR_LOAD_MEM`/
  `IR_STORE_MEM` through `[Rw]`) - confirmed with a throwaway
  `tabela[IDX]` test program, both in `--dump-asm` output and running
  the ported instructions on `simulador/c166sim.py`. What the real
  disassembly actually needs is this same indexed access combined with a
  page switch first (item above) - there is no separate blocker here,
  it's entirely the far-pointer gap.
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
- ~~A struct-by-value calling convention~~ - DONE (21/08/2026): a
  hidden-pointer-argument ("sret") lowering pass was added instead of
  redesigning the IR's one-value-per-vreg model. See
  `docs/abi.md#return-value` and `docs/c-language.md#structs`. Struct
  parameters by value remain unsupported (pass a pointer).
- Verify `CALLI`'s exact operand syntax/encoding against a real
  assembler or the full Instruction Set Manual - the excerpt available
  in this repo names the mnemonic but not its operand table (see the
  comment in `src/target/c167/instructions/isa.c`); this compiler
  currently assumes the standard C166-family `CALLI cc, Rw` form.
