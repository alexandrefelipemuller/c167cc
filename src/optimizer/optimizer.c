#include "c167cc/ir.h"
#include <stdlib.h>
#include <string.h>

static long apply_binop(OpKind op, long a, long b) {
    switch (op) {
        case OP_ADD: return a + b;
        case OP_SUB: return a - b;
        case OP_MUL: return a * b;
        case OP_DIV: return b ? a / b : 0;
        case OP_MOD: return b ? a % b : 0;
        case OP_AND: return a & b;
        case OP_OR:  return a | b;
        case OP_XOR: return a ^ b;
        case OP_SHL: return a << b;
        case OP_SHR: return a >> b;
        case OP_EQ:  return a == b;
        case OP_NE:  return a != b;
        case OP_LT:  return a < b;
        case OP_GT:  return a > b;
        case OP_LE:  return a <= b;
        case OP_GE:  return a >= b;
        default: return 0;
    }
}

static void optimize_func(IrFunc *fn) {
    int n = fn->nvregs + 1;
    if (n <= 0) return;
    long *cval = calloc(n, sizeof(long));
    char *is_const = calloc(n, 1);
    char *multi_def = calloc(n, 1);
    int *alias = malloc(sizeof(int) * n);
    for (int i = 0; i < n; i++) alias[i] = -1;
    char *seen_def = calloc(n, 1);

    for (IrInst *i = fn->head; i; i = i->next) {
        if (i->dst >= 0) {
            if (seen_def[i->dst]) multi_def[i->dst] = 1;
            seen_def[i->dst] = 1;
        }
    }

    /* forward const-fold + copy-propagation pass */
    for (IrInst *i = fn->head; i; i = i->next) {
        /* resolve operands through alias chain first */
        if (i->a >= 0) { int v = i->a; int guard = 0; while (alias[v] >= 0 && guard++ < n) v = alias[v]; i->a = v; }
        if (i->b >= 0) { int v = i->b; int guard = 0; while (alias[v] >= 0 && guard++ < n) v = alias[v]; i->b = v; }
        for (int k = 0; k < i->nargs; k++) {
            int v = i->args[k]; int guard = 0; while (alias[v] >= 0 && guard++ < n) v = alias[v]; i->args[k] = v;
        }

        switch (i->kind) {
            case IR_CONST:
                if (!multi_def[i->dst]) { is_const[i->dst] = 1; cval[i->dst] = i->imm; }
                break;
            case IR_BINOP:
                if (i->a >= 0 && i->b >= 0 && is_const[i->a] && is_const[i->b]) {
                    long r = apply_binop(i->op, cval[i->a], cval[i->b]);
                    i->kind = IR_CONST; i->imm = r; i->op = 0; i->a = -1; i->b = -1;
                    if (!multi_def[i->dst]) { is_const[i->dst] = 1; cval[i->dst] = r; }
                } else if (!multi_def[i->dst]) {
                    is_const[i->dst] = 0;
                }
                break;
            case IR_UNOP:
                if (i->op == OP_ASSIGN && !multi_def[i->dst] && i->a >= 0) {
                    alias[i->dst] = i->a; /* cast pass-through as copy for propagation purposes */
                }
                if (i->a >= 0 && is_const[i->a] && !multi_def[i->dst]) {
                    long v = cval[i->a], r = v;
                    if (i->op == OP_NEG) r = -v;
                    else if (i->op == OP_BNOT) r = ~v;
                    else if (i->op == OP_NOT) r = !v;
                    else if (i->op == OP_ASSIGN) r = v;
                    i->kind = IR_CONST; i->imm = r; i->a = -1;
                    is_const[i->dst] = 1; cval[i->dst] = r;
                }
                break;
            case IR_MOV:
                if (!multi_def[i->dst] && i->a >= 0) {
                    alias[i->dst] = i->a;
                    if (is_const[i->a]) { is_const[i->dst] = 1; cval[i->dst] = cval[i->a]; }
                }
                break;
            default:
                break;
        }
    }

    /* rewrite remaining operand refs through final alias chain (labels/branches unaffected) */
    for (IrInst *i = fn->head; i; i = i->next) {
        if (i->a >= 0) { int v = i->a; int guard = 0; while (alias[v] >= 0 && guard++ < n) v = alias[v]; i->a = v; }
        if (i->b >= 0) { int v = i->b; int guard = 0; while (alias[v] >= 0 && guard++ < n) v = alias[v]; i->b = v; }
        for (int k = 0; k < i->nargs; k++) {
            int v = i->args[k]; int guard = 0; while (alias[v] >= 0 && guard++ < n) v = alias[v]; i->args[k] = v;
        }
    }

    /* dead code elimination: mark used vregs, drop pure defs that are unused */
    char *used = calloc(n, 1);
    for (IrInst *i = fn->head; i; i = i->next) {
        if (i->a >= 0) used[i->a] = 1;
        if (i->b >= 0) used[i->b] = 1;
        for (int k = 0; k < i->nargs; k++) used[i->args[k]] = 1;
    }

    IrInst dummy = {0}; dummy.next = fn->head;
    IrInst *prev = &dummy;
    for (IrInst *i = fn->head; i; ) {
        int removable = (i->kind == IR_CONST || i->kind == IR_MOV || i->kind == IR_UNOP ||
                          i->kind == IR_BINOP || i->kind == IR_LOAD_SYM || i->kind == IR_LOAD_ADDR ||
                          i->kind == IR_LOAD_MEM);
        if (removable && i->dst >= 0 && !used[i->dst]) {
            prev->next = i->next;
            i = i->next;
            continue;
        }
        prev = i; i = i->next;
    }
    fn->head = dummy.next;
    fn->tail = prev == &dummy ? NULL : prev;

    free(cval); free(is_const); free(multi_def); free(alias); free(seen_def); free(used);
}

/* Remove instructions that follow an unconditional transfer of control
 * (RET/JMP) up to the next label - they can never be reached. */
static void strip_unreachable(IrFunc *fn) {
    IrInst dummy = {0}; dummy.next = fn->head;
    IrInst *prev = &dummy;
    int unreachable = 0;
    for (IrInst *i = fn->head; i; ) {
        if (i->kind == IR_LABEL) unreachable = 0;
        if (unreachable) {
            prev->next = i->next;
            i = i->next;
            continue;
        }
        if (i->kind == IR_RET || i->kind == IR_JMP) unreachable = 1;
        prev = i; i = i->next;
    }
    fn->head = dummy.next;
    fn->tail = prev == &dummy ? NULL : prev;
}

void ir_optimize(IrModule *m) {
    for (IrFunc *fn = m->funcs; fn; fn = fn->next) {
        optimize_func(fn);
        strip_unreachable(fn);
    }
}
