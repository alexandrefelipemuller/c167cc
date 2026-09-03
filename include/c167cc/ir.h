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
