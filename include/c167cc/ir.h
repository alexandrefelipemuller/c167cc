#ifndef C167CC_IR_H
#define C167CC_IR_H

#include "c167cc/ast.h"
#include "c167cc/symbol.h"

typedef enum {
    IR_CONST,      /* dst = imm */
    IR_LOAD_SYM,   /* dst = load(sym) */
    IR_STORE_SYM,  /* store(sym, src) */
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
