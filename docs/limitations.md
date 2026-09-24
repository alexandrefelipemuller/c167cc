# Known limitations

This is a deliberately scoped MVP. Priority order for this phase was
**correctness > simplicity > testability > readability of the generated
assembly > optimization**.

## Fixed bugs (kept here for history)

- **Indexar um identificador declarado ESCALAR inteiro (BUG-1 da
  Sirius32)**, ex. `@ram(0x1356) volatile uint8_t calib_1356;` seguido de
  `calib_1356[i]` (achado 21/09/2026 na Sirius32 - `docs/BUGS_C167CC.md`
  de lá, `core/motor_geral/detonacao_adaptativa_lote33.c`, file
  `0x2E2E6`; corrigido 24/09/2026). Gerava `MOVB R0, calib_1356` (o
  VALOR do escalar usado como ponteiro), multiplicava o índice por 2
  (`MULU`) e lia/escrevia com `MOV` (word) - byte errado, em silêncio,
  tanto em leitura quanto em escrita; o escalar `uint16_t` indexado
  também usava o valor como endereço. **Causa raiz**: os dois pontos que
  tratam `EXPR_INDEX` em `src/ir/ir_build.c` (`gen_lvalue_addr` pra
  escrita/endereço e `gen_expr` pra leitura) faziam `gen_expr(e->base)`
  - que só decai pra endereço quando o símbolo é array/função - e, sem
  `pointee` no tipo resultante, caíam no fallback `u16_type()` como tipo
  do elemento. **Correção**: novo helper `gen_index_base()` usado pelos
  dois pontos: se a base é um `EXPR_IDENT` de tipo inteiro escalar
  (`TY_I8`..`TY_U32`, não array), emite `IR_LOAD_ADDR` do símbolo e usa o
  PRÓPRIO tipo do escalar como elemento - semântica `(&x)[i]`, idêntica a
  declarar `x[N]` (uint8_t: índice *1 + `MOVB`; uint16_t: *2 + `MOV`;
  o `.asm` gerado é byte a byte igual ao da versão array, exceto o
  comentário do `EQU`). Não vira erro de compilação porque o código
  reimplementado da Sirius32 usa esse idioma. Ponteiro/array continuam
  pelo caminho antigo. **Fora do escopo** (comportamento inalterado):
  base escalar que não é identificador simples (campo de struct,
  `(*p)[i]`, expressão) ainda cai no fallback antigo.
  **Validação**: `meson test`: 30/30 -> 31/31 (novo
  `golden-index_scalar_ram`, `examples/index_scalar_ram.c`: leitura e
  escrita indexada de escalar `uint8_t` e `uint16_t`); Sirius32:
  `make core-all-check` 327/327 OK, `validar_aleatorio_dpp.py` todas OK,
  `regressao_core.py` 88 OK + 5 ERRO_COMPILACAO (mesmos preexistentes).

- **A type qualifier (`const`/`volatile`) inside an expression cast to
  pointer type was rejected by the parser**, e.g. `(volatile uint16_t
  *)0xFD90` or `(const uint16_t *)&x` failed with `error: syntax error
  (near 'volatile')` (found 19/09/2026, auditing 47 files in the sibling
  Sirius32 project stuck in the "pointer/stack/extern/static" triage
  category - most were actually blocked by this, not by a genuine
  pointer/codegen limitation: the project's `@ram(0xADDR) volatile <T>
  name;` convention already covers *declared* fixed-address symbols, but
  SFR/register access expressed inline as a cast of a raw address
  literal - which by the same project's convention must be `volatile` -
  had no way to spell that). **Root cause**: `qual_opt` (`const`/
  `volatile`, in any order/combination) was only wired into the grammar
  at declaration sites (`top_item`, `decl_declarator_list`, etc., all in
  `src/parser/parser.y`) - the single cast-expression production in
  `unary_expr`, `'(' type_spec ptr_opt ')' unary_expr`, went straight
  from `(` to `type_spec` with no qualifier in between, so a qualifier
  there was simply not a valid token at that position. This was a pure
  grammar gap: the AST's `Type` already carries a qualifier-independent
  representation (a cast's target type is used only for its size/
  signedness/pointer-ness, exactly like `(uint16_t *)0xFD90` - already
  supported - so nothing downstream of the parser needed to change).
  **Fix**: `'(' qual_opt type_spec ptr_opt ')' unary_expr` - `qual_opt`
  (already `%empty | qual_opt KW_VOLATILE | qual_opt KW_CONST`) added
  before `type_spec` in that one production; the qualifier tokens are
  consumed and intentionally discarded, matching how declaration sites
  already parse qualifiers without storing them separately (this
  compiler doesn't distinguish `const`/non-`const` for codegen purposes -
  see "Not implemented at all" below). No change to the AST, semantic
  analysis, IR, or codegen was needed.
  **Validation**: `meson test`: 27/27 -> 29/29 (2 new tests, no
  regressions) - `golden-cast_qualifier_fixed_addr` (`examples/
  cast_qualifier_fixed_addr.c`) pins the parse/codegen of `*(volatile
  uint16_t *)0xFD90 = 42;` and `*(const volatile uint16_t *)0xFD90`
  (confirmed identical to the already-working unqualified-cast codegen,
  as expected since the qualifier is discarded), and
  `sim-cast_qualifier_sfr_global` (`examples/
  cast_qualifier_sfr_global.c`) actually runs a write-then-read-back
  through `(volatile uint16_t *)0x2000` / `(const uint16_t *)0x2000` in
  the simulator and checks the round-tripped value.

- **Composing a `uint32_t`/`int32_t` from two 16-bit halves,
  `dst32 = ((uint32_t)hi << 16) | lo;` (the exact inverse of the
  already-known `sym32 >> 16` extraction pattern, `IR_SHR32_SYM` below),
  fell into the generic 16-bit-only path: the cast-and-shift never
  produced a real 32-bit value (`SHL` by 16 on a 16-bit register just
  zeroes it, discarding `hi` entirely) and the subsequent `OR`/store only
  ever wrote the destination's low word - the high word of the 32-bit
  destination was never touched at all** (found 09/09/2026, promoting a
  function in the sibling Sirius32 project - `research/
  interpolacao_motor/3b51c_motor_divisao_peso_interpolacao.c`, file
  `0x3B51C` - which reconstructs a wide dividend from two hardware
  registers this way before a 32-bit division). This is the 6th bug found
  in this session, and - like `IR_MUL32_STORE_SYM`/`IR_SHR32_SYM`/
  `IR_DIV32_SYM` below - not a new *kind* of defect so much as a new
  *shape* hitting the same, already-documented, already-understood root
  limitation: this backend represents every scalar value as ONE 16-bit
  vreg everywhere in the IR/codegen, so genuine 32-bit arithmetic outside
  the three previously special-cased shapes is "not implemented", not
  merely buggy (see "Implemented but scoped down" below, which already
  said "everything else... is still silently wrong" before this fix -
  this entry adds a 4th narrow exception to that list rather than
  claiming a previously-unknown miscompile of otherwise-supported code).
  **Root cause**: `src/ir/ir_build.c`'s generic `EXPR_BINARY` case has no
  concept of a 32-bit result at all - `(uint32_t)hi << 16` lowers through
  the ordinary `EXPR_CAST` (a same-vreg `IR_UNOP`/`OP_ASSIGN`, still just
  16 bits wide in this backend) followed by the ordinary `OP_SHL`
  `IR_BINOP`, which shifts a single 16-bit register by an immediate of
  16 - shifting a register by its own full width is well-defined on the
  C167 (`SHL Rn, #16`... in practice the codegen clamps/executes a
  16-bit-wide shift which simply empties the register) but is NEVER what
  the C source means when the destination is actually 32 bits; the `OR`
  with `lo` then combines two 16-bit vregs into one more 16-bit vreg, and
  the final `IR_STORE_SYM` writes exactly `size` bytes of straight memory
  starting at the symbol's address using the *type's* declared size for
  the instruction, but with only one 16-bit vreg to source from - so only
  the low word of a 4-byte destination was ever written; the high word
  kept whatever was already at that memory location (a `.bss` global's
  usual zero-init, in practice, which is why the report initially read as
  "the high word is zeroed" - it's really "the high word is simply never
  written", zero only because nothing else had written it either).
  **Fix**: mirrors `try_gen_widening_mul_store_sym()`/`try_gen_shr32_sym()`/
  `try_gen_div32_sym()` exactly (same file): a new
  `try_gen_compose32_store_sym()` recognizes the exact shape `dst32 =
  (hi_expr << 16) | lo_expr` (or `lo_expr | (hi_expr << 16)`, either
  operand order of the `OR`; the shift's left operand may or may not be
  wrapped in an explicit widening cast, matching how the multiply pattern
  also accepts "with or without an explicit cast on the operands") for a
  direct assignment (or declaration-with-initializer) to an
  already-declared 32-bit symbol, and lowers it to a new IR instruction,
  `IR_COMPOSE32_STORE_SYM` (`include/c167cc/ir.h`), instead of the
  generic `IR_BINOP`+`IR_STORE_SYM` pair. Unlike `IR_MUL32_STORE_SYM`
  (which reads its 32-bit result out of the fixed `MDL:MDH` register pair
  left behind by `MULU`/`MUL`), `IR_COMPOSE32_STORE_SYM`'s two halves are
  two independent, already-evaluated 16-bit vregs with no relationship to
  each other - codegen (`src/target/c167/codegen/codegen.c`) just emits
  two plain `MOV`s straight to the destination symbol's low/high words
  (`sym`/`sym+2` for a global, `[R15+#off]`/`[R15+#off+2]` for a
  local/parameter), no `MDL`/`MDH` involved. Hooked into both places
  `try_gen_widening_mul_store_sym()` already was: the `EXPR_ASSIGN`
  case in `gen_expr()` (`x = ...;`) and the `STMT_DECL` initializer case
  in `gen_stmt()` (`uint32_t x = ...;`), tried right after the
  multiply pattern in both spots so a mixed use of both idioms in the
  same function keeps working. Outside this exact shape (composing into
  something other than a plain already-declared 32-bit variable - e.g.
  as an intermediate value inside a larger expression, or into a struct
  field/array element), the code still falls into the old, still-buggy,
  documented-limitation path.
  **Validation**: reproduced with a minimal example
  (`examples/compose32_global.c`, `OUT = ((uint32_t)HI << 16) | LO;`) -
  confirmed via `--dump-asm` that the pre-fix output computed `SHL` on a
  16-bit register by an immediate of 16 (zeroing it) and emitted only one
  `MOV OUT, ...` (no `OUT+2` store anywhere), and that the post-fix
  output emits `MOV OUT, R1` (low word = `LO`) followed by
  `MOV OUT+2, R0` (high word = `HI`) with no `SHL`/`OR` at all. Since the
  toy simulator harness (`tests/port_to_toy_asm.py`) only ever compares
  16-bit values, the new `sim-compose32_global` regression test reads
  `OUT` back split into two 16-bit halves via `OUT >> 16` (already-fixed
  `IR_SHR32_SYM`) and a plain narrowing cast (`(uint16_t)OUT`, low word):
  `HI=0x1234 (4660)`, `LO=0x2222 (8738)` (kept under `0x8000` so the
  simulator's signed 16-bit register dump in the test log doesn't turn a
  legitimate positive value negative and confuse the comparison) expects
  `OUT_HI=4660, OUT_LO=8738` - this failed before the fix (`OUT_HI` came
  back `0`, `HI`'s value nowhere in the result) and passed after it.
  Manually re-ran `sim-div32_global` and `sim-pointer_arith_scaling_*`
  (the other tests exercising 32-bit symbols/pointer scaling) to confirm
  no interaction with the new pattern-match order. `meson test`: 26/26 ->
  27/27 (1 new test), no regressions.

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

- **Pointer arithmetic (`ptr + int`, `int + ptr`, `ptr - int`, `ptr -
  ptr`) assigned to a variable, or otherwise used outside of `array[i]`
  subscript syntax, did not scale the integer operand by `sizeof(*ptr)` -
  it added/subtracted the raw literal as a byte count regardless of the
  pointee's size** (found 07/09/2026, promoting `busca_indice_eixo_rpm`
  in the sibling Sirius32 project - `core/interpolacao/
  busca_eixo_interpolacao.c`). `const uint16_t *valores = tabela + 1;`
  generated code that added 1 BYTE to the pointer instead of 1 WORD (2
  bytes) - confirmed with `--dump-asm` (`MOV R1,#1 ; ADD R2,R1`, no
  scaling). This is the 5th bug found in this session. Array *subscript*
  access (`array[i]`) was NOT affected - `gen_lvalue_addr()`'s
  `EXPR_INDEX` case already scaled the index correctly via an explicit
  `IR_CONST` + `IR_BINOP(OP_MUL)` by `sizeof(element)` (confirmed in the
  same dump) before adding it to the base address.
  **Root cause**: `src/ir/ir_build.c`'s generic `EXPR_BINARY` case in
  `gen_expr()` - reached for any `+`/`-` expression that is not a direct
  `array[i]` subscript, including a decayed array or a pointer value
  combined with an integer anywhere else (`ptr + literal`, `&x + n`,
  `ptr - n`, pointer difference, or any of these nested inside a larger
  expression) - built the `IR_BINOP` straight from both operands' raw
  vregs with no awareness that one side was a pointer type at all: it
  just picked the wider of the two operand sizes for the result and
  emitted a plain `ADD`/`SUB`. The scaling logic that `EXPR_INDEX` already
  had lived only in `gen_lvalue_addr()`, a completely separate function
  used solely for lvalue address computation (assignment targets,
  `&expr`, dereference) - `gen_expr()`'s generic binary-operator path
  never called it and had no equivalent of its own.
  **Fix**: in `gen_expr()`'s `EXPR_BINARY` case, right after evaluating
  both operands' types, three new cases run before the existing
  `IR_BINOP` is built: (1) `ptr - ptr` (both operands `TY_PTR`) computes
  the raw byte difference and then divides it by `sizeof(*ptr)` to yield
  an element count, returning early with `u16_type()` as the result type;
  (2) `ptr +/- int` and `int + ptr` multiply the integer operand by
  `sizeof(*ptr)` (via the same `IR_CONST`+`IR_BINOP(OP_MUL)` pattern
  `EXPR_INDEX` already used) before falling through into the normal
  `IR_BINOP` construction, so the rest of the function - result type,
  signedness, codegen - is untouched. In every case, a pointee of size 1
  (`uint8_t*`/`char*`/`int8_t*`) skips the multiply/divide entirely
  (`esz > 1` guard), so that already-correct case stays on the exact same
  code path it used before the fix - scaling by 1 was never wrong, but
  the fix is careful not to introduce a `MUL #1`/`DIV #1` there that
  wasn't there before.
  **Validation**: reproduced with a minimal example
  (`examples/pointer_arith_scaling_array.c`, `tabela + 1` assigned to a
  `const uint16_t *`) - confirmed via `--dump-asm` that the pre-fix output
  added a raw, unscaled `#1` to the pointer and the post-fix output adds
  `#2` (now a `golden-pointer_arith_scaling_array` test, pinning the
  scaled output). Numerically cross-validated on `../simulador/
  c166sim.py` with two new `sim-*` regression tests, since the toy
  harness (`tests/port_to_toy_asm.py`) only declares symbols that
  actually appear in a function's ported body - not the original
  `.bss` globals - so a test can't rely on a second array element or
  global happening to land at a computable adjacent address:
  `examples/pointer_arith_scaling_global.c` computes
  `off = (uint16_t)(&A + 3) - (uint16_t)&A` on a `uint16_t*`, which must
  be `6` (`3 * sizeof(uint16_t)`) - it read back as `3` (raw, unscaled)
  before the fix and `6` after; `examples/
  pointer_arith_scaling_byte_global.c` runs the identical pattern on a
  `uint8_t*`, where the expected difference is `3` either way (scaling by
  1 is a no-op) - this passed both before and after the fix, guarding
  against the fix's `esz > 1` branch accidentally being taken (or not
  taken) for byte pointers. Also manually re-checked `array[i]` subscript
  access (`golden-max_vetor`, which indexes a `uint16_t*` parameter) still
  produces the identical, already-correct `MULU`-scaled golden output
  after the fix. `meson test`: 23/23 -> 26/26 (3 new tests), no
  regressions. Production workaround in the Sirius32 project (indexing
  the original array directly with the offset folded into the index,
  `tabela[i + 1]` instead of `(tabela + 1)[i]`, to avoid the buggy
  intermediate pointer variable) was deliberately left in place after the
  fix, not reverted: `tabela[i + 1]` is already idiomatic, at-least-as-
  readable C on its own merits, not something that was made awkward just
  to route around the bug - see that project's own commit history for
  the decision.

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

  **Update 19/09/2026**: added a fourth narrow case, `IR_DIV32_MUL`
  (`try_gen_div32_mul()` in `src/ir/ir_build.c`), for the case where the
  32-bit dividend is an INLINE widening product rather than an
  already-named 32-bit symbol - `IR_DIV32_SYM` above requires the LHS of
  `/`/`%` to already be `EXPR_IDENT`, so `((uint32_t)a * (uint32_t)b) /
  escala` (product used directly, never assigned to a 32-bit temp first)
  fell through to the generic 16-bit path and silently divided only the
  low word of the product with `DIV`/`DIVU`. This is exactly the shape of
  the widening-division family stuck in `research/interpolacao_motor` in
  the sibling Sirius32 project (`0x3BA56`/`0x3BAF2`/`0x3BB4C`, and the
  14-occurrence `3b536_rotina_normalizacao_divisao_32bit.c`). Matches
  `((T)a * (T)b) / expr` or `% expr` where at least one multiply operand
  carries an explicit cast to a 32-bit type (same "explicit cast signals
  widening intent" convention already used by the worked examples for the
  other three cases - there's no 32-bit assignment target here to infer
  widening from the way `IR_MUL32_STORE_SYM` does). Codegen is actually
  simpler than `IR_DIV32_SYM`: `MULU`/`MUL a,b` already leaves the product
  in `MDL:MDH`, so `DIVLU`/`DIVL` consumes it directly with no
  store/reload round-trip through memory. Verified with a new
  `sim-div32_mul_global` test (`examples/div32_mul_global.c`, same
  `A=1234,B=777` → `produto=958818` → `QUOC=9588,REM=18` as
  `div32_global.c`, but computed with the product inline instead of
  through a temp); `meson test` unchanged otherwise (27/27 after adding
  the new test, up from 26/26 baseline measured before this change - note
  this differs from older docs mentioning 15/16 or 29/29, which are
  stale/from a different environment; trust a freshly measured baseline).

  Still NOT covered by any of the four narrow cases (characterized
  19/09/2026, still falls into the old silent-truncation path): a 32-bit
  accumulator added to a product in the same expression (`acc = acc +
  (uint32_t)a * (uint32_t)b` - both the product's and `acc`'s high words
  are dropped), a product used directly in a 16-bit comparison or plain
  assignment without an intervening cast/division, `>>`/`/` applied to an
  expression that is itself a sum/difference (only a bare 32-bit
  identifier or, now, a bare inline product is recognized), `<<` used to
  compose a 32-bit value from two halves (`((uint32_t)hi << 16) | lo` -
  no `IR_COMPOSE32_STORE_SYM` or equivalent exists in this codebase
  despite being a plausible fifth case; not implemented), and any
  compound-assignment operator (`+=`, `*=`, etc.) on a 32-bit value.

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
  16-bit. Five narrow exceptions were special-cased (see "Fixed bugs"
  above, `IR_MUL32_STORE_SYM`/`IR_SHR32_SYM`/`IR_DIV32_SYM`/
  `IR_COMPOSE32_STORE_SYM`/`IR_DIV32_MUL`):
  `dst32 = a * b` (direct assignment of a widening multiply to a 32-bit
  variable), `some32bitvar >> N` for constant `N`, `some32bitvar / expr` /
  `some32bitvar % expr` (16-bit divisor, result truncated to 16 bits),
  `dst32 = ((uint32_t)hi << 16) | lo` (direct assignment composing a 32-bit
  variable from two 16-bit halves - the inverse of the `>> N` extraction),
  and `((T)a * (T)b) / expr` / `% expr` (an inline widening product used
  directly as dividend, not first assigned to a 32-bit temp). Everything
  else - `+`, `-`, `<<` used to compose a 32-bit value, compound assignment
  (`+=` etc.), a product added to a 32-bit accumulator in the same
  expression, 32-bit values threaded through anything but those five exact
  shapes, function arguments/returns - is still silently wrong. Avoid
  general 32-bit arithmetic until this is addressed.
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
