#ifndef C167CC_IR_H
#define C167CC_IR_H

#include "c167cc/ast.h"
#include "c167cc/symbol.h"

typedef enum {
    IR_CONST,      /* dst = imm */
    IR_LOAD_SYM,   /* dst = load(sym) */
    IR_STORE_SYM,  /* store(sym, src) */
    IR_MUL32_STORE_SYM, /* store32(sym, a * b) - widening 16x16->32 multiply
                            stored directly into a 32-bit symbol (low word =
                            MDL, high word = MDH). Narrow special case, NOT
                            general 32-bit arithmetic support - see the long
                            comment on try_gen_widening_mul_store_sym() in
                            ir_build.c for why this exists instead of real
                            register-pair support (found 02/09/2026,
                            comparing compiled output against the real
                            firmware binary in the sibling Sirius32 project:
                            plain IR_BINOP(OP_MUL) always discarded MDH,
                            silently returning 0 for the high word of any
                            widening multiply). */
    IR_SHR32_SYM,  /* dst = (u16)(sym32 >> imm) - narrow counterpart to
                       IR_MUL32_STORE_SYM: reads a right-shifted 16-bit slice
                       out of a 32-bit symbol without real 32-bit register
                       support. imm in [0,31], is_signed selects arithmetic
                       vs logical shift of the high word. See
                       try_gen_shr32_sym() in ir_build.c. */
    IR_DIV32_SYM,  /* dst = (u16)(sym32 / b) or (u16)(sym32 % b) - narrow
                       counterpart to IR_MUL32_STORE_SYM/IR_SHR32_SYM: real
                       32/16 division needs DIVLU/DIVL (dividend pre-loaded
                       into MDL:MDH, not just MDL like plain DIV/DIVU), which
                       this backend has no general register-pair support for.
                       Recognizes `ident_of_32bit_type / expr16` (or `%`)
                       where the 16-bit divisor is any ordinary expression
                       and the result is consumed/assigned as 16-bit -
                       see try_gen_div32_sym() in ir_build.c. Found
                       03/09/2026 investigating the bilinear-interpolation
                       cluster in the sibling Sirius32 project (file
                       0x3AE96-0x3B7FE): those routines compute
                       `uint32_t produto = (uint32_t)a * b;` (already
                       handled by IR_MUL32_STORE_SYM) then immediately
                       `resultado = produto / escala;` - before this, that
                       division silently used only the low 16 bits of
                       `produto`, discarding MDH and giving a wrong quotient
                       whenever the product exceeded 65535, exactly the case
                       these routines exist for. NOT supported: divisor
                       wider than 16 bits, or a 32-bit quotient assigned back
                       to a 32-bit variable (same narrow-result restriction
                       as IR_SHR32_SYM). */
    IR_COMPOSE32_STORE_SYM, /* store32(sym, (hi_word=a) << 16 | (lo_word=b)) -
                         narrow counterpart to IR_MUL32_STORE_SYM/
                         IR_SHR32_SYM/IR_DIV32_SYM (see the long comment
                         there and the "no real 32-bit value support"
                         note at the top of ir_build.c): recognizes the
                         common "compose a 32-bit value from two 16-bit
                         halves" C idiom, `dst32 = ((uint32_t)hi << 16) |
                         lo;` (or `lo | ((uint32_t)hi << 16)`, either
                         operand order), which is the exact inverse of
                         what IR_SHR32_SYM's N==16/N<16 cases extract.
                         Without this, the generic IR_BINOP path evaluates
                         `(uint32_t)hi << 16` as a single 16-bit SHL (SHL
                         by 16 on a 16-bit register yields 0, not a true
                         widening shift) and IR_STORE_SYM only ever writes
                         one word to the destination - so both HI's
                         contribution and the destination's high word were
                         silently lost (found 09/09/2026, promoting
                         `research/interpolacao_motor/
                         3b51c_motor_divisao_peso_interpolacao.c` in the
                         sibling Sirius32 project, file 0x3B51C). Emits
                         two plain MOVs straight to the destination
                         symbol's low/high words (`a` -> high word, `b`
                         -> low word) - no MDL/MDH involved, this is pure
                         data movement, not an arithmetic instruction with
                         a fixed 32-bit result register like MULU/DIVLU.
                         See try_gen_compose32_store_sym() in
                         ir_build.c. */
    IR_DIV32_MUL,  /* dst = (u16)((a * b) / divisor) or (u16)((a * b) % divisor)
                       - sibling of IR_DIV32_SYM for the case where the 32-bit
                       dividend is an INLINE widening product, not already a
                       named 32-bit symbol (found 19/09/2026: the widening-
                       division family in the sibling Sirius32 project,
                       research/interpolacao_motor 0x3BA56/0x3BAF2/0x3BB4C
                       and 3b536_rotina_normalizacao_divisao_32bit.c, writes
                       `resultado = (uint16_t)(((uint32_t)a * (uint32_t)b) /
                       escala);` with the product used directly as the
                       dividend, never stored to a 32-bit temp first - that
                       shape doesn't match try_gen_div32_sym(), whose LHS
                       must already be an ident, so it fell through to the
                       generic 16-bit path and silently divided only the low
                       word of the product with DIVU instead of DIVLU).
                       `a`/`b` are the two 16-bit multiply operands,
                       `args[0]` is the divisor vreg (nargs==1); codegen does
                       MULU/MUL a,b then feeds MDL:MDH straight into
                       DIVLU/DIVL without ever spilling the product to
                       memory, unlike IR_DIV32_SYM which reloads a stored
                       symbol. See try_gen_div32_mul() in ir_build.c. NOT
                       supported: an inline product as dividend where the
                       32-bit QUOTIENT itself needs to survive further (this,
                       like IR_DIV32_SYM, only ever produces a 16-bit
                       result). */
    IR_FARREAD16_SYM, /* dst = far_read16(page=a, off=b) - one atomic
                         EXTP page,#1 immediately followed by MOV dst,[off],
                         with NOTHING emitted in between (same "recognize
                         one exact C shape, emit fused raw instructions from
                         a single codegen case" discipline as
                         IR_MUL32_STORE_SYM/IR_SHR32_SYM/IR_DIV32_SYM).
                         Exists because a general `@far(page)` attribute was
                         investigated and deliberately rejected (see
                         docs/limitations.md, "Memory segmentation"): the
                         flat/unordered IR plus register-allocator spill
                         insertion can't otherwise guarantee EXTP stays
                         adjacent to its paired load, risking a silent
                         wrong-page read. Recognized ONLY from a direct call
                         to the exact name `c167cc_far_read16(page, off)`
                         (see the EXPR_CALL case in ir_build.c's gen_expr) -
                         that name is a compiler-recognized intrinsic, never
                         actually called (no CALLS is ever emitted; the
                         plain prototype declaration in the source exists
                         only so semantic.c type-checks the call like any
                         other undefined extern). Reading 2 consecutive far
                         words (the shape needed for file 0x3B488 in the
                         sibling Sirius32 project: EXTP_S;MOV;ADD;ADDC;
                         EXTP_S;MOV;RETS) is composed at the C source level
                         from 2 independent calls (offset and offset+2) -
                         each call is independently atomic, no register-pair
                         "advance the pointer" state needs to cross the
                         atomic boundary. Found 04/09/2026; which register
                         supplies the page was confirmed empirically in
                         c166sim.py against the real firmware (varying it
                         changes the result; varying the other register a
                         naive reading of the call site suggested does not)
                         before trusting any convention here. */
    IR_FARREAD8_SYM, /* dst = far_read8(page=a, off=b) - byte sibling of
                         IR_FARREAD16_SYM: EXTP page,#1 immediately followed
                         by MOVB dst,[off], same atomicity guarantee, same
                         intrinsic-recognition mechanism (exact call name
                         `c167cc_far_read8(page, off)`). Needed for file
                         0x194F8 in the sibling Sirius32 project, which reads
                         far BYTES (MOVB ...,[RbindRw]) rather than words. */
    IR_LOAD_ADDR,  /* dst = addr(sym) */
    IR_LOAD_MEM,   /* dst = *[addr_reg] (size in bytes) */
    IR_STORE_MEM,  /* *[addr_reg] = src (size in bytes) */
    IR_BINOP,      /* dst = lhs op rhs */
    IR_UNOP,       /* dst = op src */
    IR_MOV,        /* dst = src */
    IR_CALL,       /* dst = call(func, args...): direct if call_name is set
                       (a plain CALLR by label), indirect through the
                       function-pointer value in vreg `a` if call_name is
                       NULL (a CALLI) */
    IR_LABEL,
    IR_JMP,
    IR_CJMP,       /* if (cond) jmp true_label else jmp false_label */
    IR_RET,
} IrOpKind;

typedef struct IrInst {
    IrOpKind kind;
    SrcLoc loc;
    char *comment; /* source-derived comment for the printer */

    int dst;  /* virtual register id, -1 if none */
    int size; /* operand size in bytes: 1, 2, 4 */
    int is_signed;

    long imm;
    Symbol *sym;
    OpKind op;

    int a, b; /* virtual register operands, -1 if unused */

    char *label;       /* IR_LABEL, IR_JMP */
    char *true_label;  /* IR_CJMP */
    char *false_label; /* IR_CJMP */

    char *call_name;
    int *args;   /* virtual register ids */
    int nargs;

    struct IrInst *next;
} IrInst;

typedef struct IrFunc {
    char *name;
    Type *ret_type;
    Symbol **params;
    int nparams;
    IrInst *head;
    IrInst *tail;
    int nvregs;
    Symbol **locals;
    int nlocals;
    AttrKind attrs;
    int interrupt_vector;
    struct IrFunc *next;
} IrFunc;

typedef struct IrGlobal {
    Symbol *sym;
    Expr *init;
    struct IrGlobal *next;
} IrGlobal;

typedef struct IrModule {
    IrFunc *funcs;
    IrFunc *funcs_tail;
    IrGlobal *globals;
    IrGlobal *globals_tail;
} IrModule;

IrModule *ir_build(TranslationUnit *tu);
void ir_dump(IrModule *m);
void ir_optimize(IrModule *m);

#endif
