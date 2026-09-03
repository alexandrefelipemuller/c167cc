#include "c167cc/ir.h"
#include <stdio.h>

static const char *op_str(OpKind op) {
    switch (op) {
        case OP_ADD: return "add"; case OP_SUB: return "sub"; case OP_MUL: return "mul";
        case OP_DIV: return "div"; case OP_MOD: return "mod";
        case OP_AND: return "and"; case OP_OR: return "or"; case OP_XOR: return "xor";
        case OP_SHL: return "shl"; case OP_SHR: return "shr";
        case OP_LAND: return "land"; case OP_LOR: return "lor";
        case OP_EQ: return "eq"; case OP_NE: return "ne";
        case OP_LT: return "lt"; case OP_GT: return "gt"; case OP_LE: return "le"; case OP_GE: return "ge";
        case OP_NOT: return "not"; case OP_BNOT: return "bnot"; case OP_NEG: return "neg";
        case OP_ASSIGN: return "cast";
    }
    return "?";
}

static void dump_inst(IrInst *i) {
    switch (i->kind) {
        case IR_CONST: printf("  t%d = const %ld\n", i->dst, i->imm); break;
        case IR_LOAD_SYM: printf("  t%d = load %s\n", i->dst, i->sym->name); break;
        case IR_STORE_SYM: printf("  store %s, t%d\n", i->sym->name, i->a); break;
        case IR_MUL32_STORE_SYM: printf("  store32 %s, t%d %s t%d\n", i->sym->name, i->a, i->is_signed ? "smul" : "umul", i->b); break;
        case IR_SHR32_SYM: printf("  t%d = shr32 %s, %ld\n", i->dst, i->sym->name, i->imm); break;
        case IR_LOAD_ADDR: printf("  t%d = addr %s\n", i->dst, i->sym->name); break;
        case IR_LOAD_MEM: printf("  t%d = load%d [t%d]\n", i->dst, i->size, i->a); break;
        case IR_STORE_MEM: printf("  store%d [t%d], t%d\n", i->size, i->a, i->b); break;
        case IR_BINOP: printf("  t%d = %s t%d, t%d\n", i->dst, op_str(i->op), i->a, i->b); break;
        case IR_UNOP: printf("  t%d = %s t%d\n", i->dst, op_str(i->op), i->a); break;
        case IR_MOV: printf("  t%d = t%d\n", i->dst, i->a); break;
        case IR_CALL: {
            if (i->call_name) printf("  t%d = call %s(", i->dst, i->call_name);
            else printf("  t%d = call *t%d(", i->dst, i->a);
            for (int k = 0; k < i->nargs; k++) printf("%st%d", k ? ", " : "", i->args[k]);
            printf(")\n");
            break;
        }
        case IR_LABEL: printf("%s:\n", i->label); break;
        case IR_JMP: printf("  jmp %s\n", i->label); break;
        case IR_CJMP: printf("  branch t%d, %s, %s\n", i->a, i->true_label, i->false_label); break;
        case IR_RET:
            if (i->a >= 0) printf("  return t%d\n", i->a); else printf("  return\n");
            break;
    }
}

void ir_dump(IrModule *m) {
    for (IrGlobal *g = m->globals; g; g = g->next) {
        printf("global %s : %s", g->sym->name, type_name(g->sym->type));
        if (g->sym->has_abs_addr) printf(" @0x%lX", g->sym->abs_addr);
        printf("\n");
    }
    for (IrFunc *fn = m->funcs; fn; fn = fn->next) {
        printf("func %s(", fn->name);
        for (int i = 0; i < fn->nparams; i++) printf("%s%s", i ? ", " : "", fn->params[i]->name);
        printf("):\n");
        for (IrInst *i = fn->head; i; i = i->next) dump_inst(i);
        printf("\n");
    }
}
