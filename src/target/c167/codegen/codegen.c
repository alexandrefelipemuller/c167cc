#include "c167cc/c167_target.h"
#include "c167cc/regalloc.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

typedef struct {
    AsmFunc *af;
    IrFunc *fn;
    RegAllocResult *ra;
    int frame_size;
    int last_line;
    const char *last_file;
} CG;

static void *xalloc(size_t n) { return calloc(1, n); }

static AsmLine *emit_raw(CG *cg, const char *label, const char *mnemonic, const char *operands, const char *comment) {
    AsmLine *l = xalloc(sizeof(AsmLine));
    if (label) l->label = strdup(label);
    if (mnemonic) l->mnemonic = strdup(mnemonic);
    if (operands) l->operands = strdup(operands);
    if (comment) l->comment = strdup(comment);
    if (!cg->af->head) cg->af->head = cg->af->tail = l;
    else { cg->af->tail->next = l; cg->af->tail = l; }
    return l;
}

static void emit_loc_comment(CG *cg, SrcLoc loc) {
    if (!loc.file) return;
    if (loc.file == cg->last_file && loc.line == cg->last_line) return;
    cg->last_file = loc.file; cg->last_line = loc.line;
    char buf[256];
    snprintf(buf, sizeof(buf), "source: %s:%d", loc.file, loc.line);
    AsmLine *l = xalloc(sizeof(AsmLine));
    l->comment = strdup(buf);
    if (!cg->af->head) cg->af->head = cg->af->tail = l;
    else { cg->af->tail->next = l; cg->af->tail = l; }
}

static const char *reg_of(CG *cg, int vreg) { return c167_reg_name(cg->ra->reg[vreg]); }

static const char *load_operand(CG *cg, int vreg, C167Reg scratch) {
    if (!cg->ra->spilled[vreg]) return reg_of(cg, vreg);
    char ops[80];
    snprintf(ops, sizeof(ops), "%s, [R15+#%d]", c167_reg_name(scratch), cg->ra->spill_offset[vreg]);
    emit_raw(cg, NULL, "MOV", ops, "reload spilled value");
    return c167_reg_name(scratch);
}

static void store_if_spilled(CG *cg, int vreg, C167Reg scratch) {
    if (!cg->ra->spilled[vreg]) return;
    char ops[80];
    snprintf(ops, sizeof(ops), "[R15+#%d], %s", cg->ra->spill_offset[vreg], c167_reg_name(scratch));
    emit_raw(cg, NULL, "MOV", ops, NULL);
}

static const char *dst_target(CG *cg, int vreg) {
    return cg->ra->spilled[vreg] ? c167_reg_name(C167_SPILL_SCRATCH_1) : reg_of(cg, vreg);
}

static void finish_dst(CG *cg, int vreg) {
    if (cg->ra->spilled[vreg]) store_if_spilled(cg, vreg, C167_SPILL_SCRATCH_1);
}

/* Real C166/C167 condition-code mnemonics (confirmed against actual hardware
 * encoding - see docs/assembly-syntax.md): there is no cc_EQ/cc_NE/cc_ULT/
 * cc_UGE. Equality uses the Z flag (cc_Z/cc_NZ) and unsigned less-than/
 * greater-or-equal use the C (carry/borrow) flag (cc_C/cc_NC). */
static const char *cc_for(OpKind op, int is_signed) {
    switch (op) {
        case OP_EQ: return "cc_Z";
        case OP_NE: return "cc_NZ";
        case OP_LT: return is_signed ? "cc_SLT" : "cc_C";
        case OP_GT: return is_signed ? "cc_SGT" : "cc_UGT";
        case OP_LE: return is_signed ? "cc_SLE" : "cc_ULE";
        case OP_GE: return is_signed ? "cc_SGE" : "cc_NC";
        default: return "cc_UC";
    }
}

static void emit_prologue(CG *cg) {
    emit_raw(cg, NULL, "PUSH", "R15", "save caller's frame pointer");
    if (cg->frame_size > 0) {
        char ops[32]; snprintf(ops, sizeof(ops), "SP, #%d", cg->frame_size);
        emit_raw(cg, NULL, "SUB", ops, "allocate locals + spills");
    }
    emit_raw(cg, NULL, "MOV", "R15, SP", "establish frame pointer");
    for (int i = 0; i < cg->fn->nparams; i++) {
        Symbol *p = cg->fn->params[i];
        char ops[64];
        const char *mn = (type_size(p->type) == 1) ? "MOVB" : "MOV";
        snprintf(ops, sizeof(ops), "[R15+#%d], %s", p->stack_offset, c167_reg_name(c167_arg_regs[i]));
        char cmt[64]; snprintf(cmt, sizeof(cmt), "spill incoming parameter '%s'", p->name);
        emit_raw(cg, NULL, mn, ops, cmt);
    }
}

static void emit_epilogue(CG *cg) {
    if (cg->frame_size > 0) {
        char ops[32]; snprintf(ops, sizeof(ops), "SP, #%d", cg->frame_size);
        emit_raw(cg, NULL, "ADD", ops, "release locals + spills");
    }
    emit_raw(cg, NULL, "POP", "R15", "restore caller's frame pointer");
}

static void gen_sym_addr_to(CG *cg, Symbol *sym, C167Reg dst) {
    if (sym->kind == SYM_LOCAL || sym->kind == SYM_PARAM) {
        char ops[48];
        if (sym->stack_offset == 0) {
            snprintf(ops, sizeof(ops), "%s, R15", c167_reg_name(dst));
            emit_raw(cg, NULL, "MOV", ops, NULL);
        } else {
            snprintf(ops, sizeof(ops), "%s, R15", c167_reg_name(dst));
            emit_raw(cg, NULL, "MOV", ops, NULL);
            char ops2[48]; snprintf(ops2, sizeof(ops2), "%s, #%d", c167_reg_name(dst), sym->stack_offset);
            emit_raw(cg, NULL, "ADD", ops2, NULL);
        }
    } else {
        char ops[80]; snprintf(ops, sizeof(ops), "%s, #%s", c167_reg_name(dst), sym->name);
        emit_raw(cg, NULL, "MOV", ops, "near address of global");
    }
}

static void gen_inst(CG *cg, IrInst *i, IrInst *next) {
    emit_loc_comment(cg, i->loc);
    switch (i->kind) {
        case IR_CONST: {
            const char *d = dst_target(cg, i->dst);
            char ops[48]; snprintf(ops, sizeof(ops), "%s, #%ld", d, i->imm);
            emit_raw(cg, NULL, "MOV", ops, NULL);
            finish_dst(cg, i->dst);
            break;
        }
        case IR_LOAD_SYM: {
            const char *d = dst_target(cg, i->dst);
            const char *mn = i->size == 1 ? "MOVB" : "MOV";
            char ops[80];
            if (i->sym->kind == SYM_LOCAL || i->sym->kind == SYM_PARAM)
                snprintf(ops, sizeof(ops), "%s, [R15+#%d]", d, i->sym->stack_offset);
            else
                snprintf(ops, sizeof(ops), "%s, %s", d, i->sym->name);
            char cmt[80]; snprintf(cmt, sizeof(cmt), "%s = %s", d, i->sym->name);
            emit_raw(cg, NULL, mn, ops, cmt);
            finish_dst(cg, i->dst);
            break;
        }
        case IR_STORE_SYM: {
            const char *s = load_operand(cg, i->a, C167_SPILL_SCRATCH_1);
            const char *mn = i->size == 1 ? "MOVB" : "MOV";
            char ops[80];
            if (i->sym->kind == SYM_LOCAL || i->sym->kind == SYM_PARAM)
                snprintf(ops, sizeof(ops), "[R15+#%d], %s", i->sym->stack_offset, s);
            else
                snprintf(ops, sizeof(ops), "%s, %s", i->sym->name, s);
            char cmt[80]; snprintf(cmt, sizeof(cmt), "%s = %s", i->sym->name, s);
            emit_raw(cg, NULL, mn, ops, cmt);
            break;
        }
        case IR_LOAD_ADDR: {
            C167Reg dr = cg->ra->spilled[i->dst] ? C167_SPILL_SCRATCH_1 : cg->ra->reg[i->dst];
            gen_sym_addr_to(cg, i->sym, dr);
            finish_dst(cg, i->dst);
            break;
        }
        case IR_LOAD_MEM: {
            const char *a = load_operand(cg, i->a, C167_SPILL_SCRATCH_2);
            const char *d = dst_target(cg, i->dst);
            const char *mn = i->size == 1 ? "MOVB" : "MOV";
            char ops[48]; snprintf(ops, sizeof(ops), "%s, [%s]", d, a);
            emit_raw(cg, NULL, mn, ops, NULL);
            finish_dst(cg, i->dst);
            break;
        }
        case IR_STORE_MEM: {
            const char *a = load_operand(cg, i->a, C167_SPILL_SCRATCH_1);
            const char *bsrc = load_operand(cg, i->b, C167_SPILL_SCRATCH_2);
            const char *mn = i->size == 1 ? "MOVB" : "MOV";
            char ops[48]; snprintf(ops, sizeof(ops), "[%s], %s", a, bsrc);
            emit_raw(cg, NULL, mn, ops, NULL);
            break;
        }
        case IR_MOV: {
            const char *a = load_operand(cg, i->a, C167_SPILL_SCRATCH_1);
            const char *d = dst_target(cg, i->dst);
            if (strcmp(a, d) != 0) {
                char ops[48]; snprintf(ops, sizeof(ops), "%s, %s", d, a);
                emit_raw(cg, NULL, "MOV", ops, NULL);
            }
            finish_dst(cg, i->dst);
            break;
        }
        case IR_UNOP: {
            const char *a = load_operand(cg, i->a, C167_SPILL_SCRATCH_1);
            const char *d = dst_target(cg, i->dst);
            if (i->op == OP_NEG) {
                if (strcmp(a, d) != 0) { char ops[48]; snprintf(ops, sizeof(ops), "%s, %s", d, a); emit_raw(cg, NULL, "MOV", ops, NULL); }
                char ops2[16]; snprintf(ops2, sizeof(ops2), "%s", d); emit_raw(cg, NULL, "NEG", ops2, NULL);
            } else if (i->op == OP_BNOT) {
                if (strcmp(a, d) != 0) { char ops[48]; snprintf(ops, sizeof(ops), "%s, %s", d, a); emit_raw(cg, NULL, "MOV", ops, NULL); }
                char ops2[16]; snprintf(ops2, sizeof(ops2), "%s", d); emit_raw(cg, NULL, "CPL", ops2, NULL);
            } else if (i->op == OP_NOT) {
                char ops[32]; snprintf(ops, sizeof(ops), "%s, #0", a); emit_raw(cg, NULL, "CMP", ops, NULL);
                char lt[96], le[96];
                static int uid = 0; uid++;
                snprintf(lt, sizeof(lt), ".Lnot_true_%d", uid);
                snprintf(le, sizeof(le), ".Lnot_end_%d", uid);
                char j1[96]; snprintf(j1, sizeof(j1), "cc_Z, %s", lt); emit_raw(cg, NULL, "JMPR", j1, NULL);
                char m0[32]; snprintf(m0, sizeof(m0), "%s, #0", d); emit_raw(cg, NULL, "MOV", m0, NULL);
                char j2[96]; snprintf(j2, sizeof(j2), "cc_UC, %s", le); emit_raw(cg, NULL, "JMPR", j2, NULL);
                emit_raw(cg, lt, NULL, NULL, NULL);
                char m1[32]; snprintf(m1, sizeof(m1), "%s, #1", d); emit_raw(cg, NULL, "MOV", m1, NULL);
                emit_raw(cg, le, NULL, NULL, NULL);
            } else { /* OP_ASSIGN: cast */
                if (i->size == 2) {
                    const char *mn = i->is_signed ? "MOVBS" : "MOVBZ";
                    /* widening from byte source is only meaningful when the source was byte-sized;
                       for same-size casts, a plain MOV suffices. */
                    char ops[48]; snprintf(ops, sizeof(ops), "%s, %s", d, a);
                    emit_raw(cg, NULL, strcmp(a, d) ? "MOV" : "MOV", ops, NULL);
                    (void)mn;
                } else {
                    char ops[48]; snprintf(ops, sizeof(ops), "%s, %s", d, a);
                    if (strcmp(a, d) != 0) emit_raw(cg, NULL, "MOV", ops, NULL);
                }
            }
            finish_dst(cg, i->dst);
            break;
        }
        case IR_BINOP: {
            if (i->op == OP_EQ || i->op == OP_NE || i->op == OP_LT || i->op == OP_GT || i->op == OP_LE || i->op == OP_GE) {
                const char *a = load_operand(cg, i->a, C167_SPILL_SCRATCH_1);
                const char *bsrc = load_operand(cg, i->b, C167_SPILL_SCRATCH_2);
                const char *d = dst_target(cg, i->dst);
                char ops[48]; snprintf(ops, sizeof(ops), "%s, %s", a, bsrc); emit_raw(cg, NULL, "CMP", ops, NULL);
                static int uid = 0; uid++;
                char lt[96], le[96]; snprintf(lt, sizeof(lt), ".Lcmp_true_%d", uid); snprintf(le, sizeof(le), ".Lcmp_end_%d", uid);
                char j1[96]; snprintf(j1, sizeof(j1), "%s, %s", cc_for(i->op, i->is_signed), lt); emit_raw(cg, NULL, "JMPR", j1, NULL);
                char m0[32]; snprintf(m0, sizeof(m0), "%s, #0", d); emit_raw(cg, NULL, "MOV", m0, NULL);
                char j2[96]; snprintf(j2, sizeof(j2), "cc_UC, %s", le); emit_raw(cg, NULL, "JMPR", j2, NULL);
                emit_raw(cg, lt, NULL, NULL, NULL);
                char m1[32]; snprintf(m1, sizeof(m1), "%s, #1", d); emit_raw(cg, NULL, "MOV", m1, NULL);
                emit_raw(cg, le, NULL, NULL, NULL);
                finish_dst(cg, i->dst);
                break;
            }
            if (i->op == OP_MUL) {
                const char *a = load_operand(cg, i->a, C167_SPILL_SCRATCH_1);
                const char *bsrc = load_operand(cg, i->b, C167_SPILL_SCRATCH_2);
                char ops[48]; snprintf(ops, sizeof(ops), "%s, %s", a, bsrc);
                emit_raw(cg, NULL, i->is_signed ? "MUL" : "MULU", ops, NULL);
                const char *d = dst_target(cg, i->dst);
                char ops2[32]; snprintf(ops2, sizeof(ops2), "%s, MDL", d); emit_raw(cg, NULL, "MOV", ops2, "low word of MDL:MDH product");
                finish_dst(cg, i->dst);
                break;
            }
            if (i->op == OP_DIV || i->op == OP_MOD) {
                const char *a = load_operand(cg, i->a, C167_SPILL_SCRATCH_1);
                const char *bsrc = load_operand(cg, i->b, C167_SPILL_SCRATCH_2);
                char ops1[32]; snprintf(ops1, sizeof(ops1), "MDL, %s", a); emit_raw(cg, NULL, "MOV", ops1, "dividend into MDL");
                char ops2[32]; snprintf(ops2, sizeof(ops2), "%s", bsrc); emit_raw(cg, NULL, i->is_signed ? "DIV" : "DIVU", ops2, NULL);
                const char *d = dst_target(cg, i->dst);
                char ops3[32]; snprintf(ops3, sizeof(ops3), "%s, %s", d, i->op == OP_DIV ? "MDL" : "MDH");
                emit_raw(cg, NULL, "MOV", ops3, i->op == OP_DIV ? "quotient" : "remainder");
                finish_dst(cg, i->dst);
                break;
            }
            const char *mn = NULL;
            switch (i->op) {
                case OP_ADD: mn = "ADD"; break;
                case OP_SUB: mn = "SUB"; break;
                case OP_AND: mn = "AND"; break;
                case OP_OR: mn = "OR"; break;
                case OP_XOR: mn = "XOR"; break;
                case OP_SHL: mn = "SHL"; break;
                case OP_SHR: mn = "SHR"; break;
                default: mn = "ADD"; break;
            }
            const char *a = load_operand(cg, i->a, C167_SPILL_SCRATCH_1);
            const char *d = dst_target(cg, i->dst);
            if (strcmp(a, d) != 0) { char ops[48]; snprintf(ops, sizeof(ops), "%s, %s", d, a); emit_raw(cg, NULL, "MOV", ops, NULL); }
            const char *bsrc = load_operand(cg, i->b, C167_SPILL_SCRATCH_2);
            char ops[48]; snprintf(ops, sizeof(ops), "%s, %s", d, bsrc); emit_raw(cg, NULL, mn, ops, NULL);
            finish_dst(cg, i->dst);
            break;
        }
        case IR_CALL: {
            if (i->nargs > C167_ARG_REGS_COUNT) {
                fprintf(stderr, "error: call to '%s' has %d arguments, only %d supported (see docs/limitations.md)\n",
                        i->call_name ? i->call_name : "<function pointer>", i->nargs, C167_ARG_REGS_COUNT);
                exit(1);
            }
            for (int k = 0; k < i->nargs; k++) {
                const char *s = load_operand(cg, i->args[k], C167_SPILL_SCRATCH_1);
                const char *argr = c167_reg_name(c167_arg_regs[k]);
                if (strcmp(s, argr) != 0) { char ops[48]; snprintf(ops, sizeof(ops), "%s, %s", argr, s); emit_raw(cg, NULL, "MOV", ops, NULL); }
            }
            if (i->call_name) {
                emit_raw(cg, NULL, "CALLR", i->call_name, NULL);
            } else {
                /* indirect call through a function-pointer value; loaded
                   after the args are marshalled, since it doesn't need to
                   live in R4-R7 and any spill scratch use above is done by
                   now - see load_operand()'s reload comment. */
                const char *fn = load_operand(cg, i->a, C167_SPILL_SCRATCH_1);
                char ops[48]; snprintf(ops, sizeof(ops), "cc_UC, %s", fn);
                emit_raw(cg, NULL, "CALLI", ops, NULL);
            }
            if (i->dst >= 0) {
                const char *d = dst_target(cg, i->dst);
                if (strcmp(d, "R0") != 0) { char ops[32]; snprintf(ops, sizeof(ops), "%s, R0", d); emit_raw(cg, NULL, "MOV", ops, "function result"); }
                finish_dst(cg, i->dst);
            }
            break;
        }
        case IR_LABEL:
            emit_raw(cg, i->label, NULL, NULL, NULL);
            break;
        case IR_JMP: {
            char ops[64]; snprintf(ops, sizeof(ops), "cc_UC, %s", i->label);
            emit_raw(cg, NULL, "JMPR", ops, NULL);
            break;
        }
        case IR_CJMP: {
            const char *a = load_operand(cg, i->a, C167_SPILL_SCRATCH_1);
            char ops[48]; snprintf(ops, sizeof(ops), "%s, #0", a); emit_raw(cg, NULL, "CMP", ops, NULL);
            char j1[96]; snprintf(j1, sizeof(j1), "cc_NZ, %s", i->true_label); emit_raw(cg, NULL, "JMPR", j1, NULL);
            int skip_false = (next && next->kind == IR_LABEL && strcmp(next->label, i->false_label) == 0);
            if (!skip_false) {
                char j2[96]; snprintf(j2, sizeof(j2), "cc_UC, %s", i->false_label); emit_raw(cg, NULL, "JMPR", j2, NULL);
            }
            break;
        }
        case IR_RET: {
            if (i->a >= 0) {
                const char *a = load_operand(cg, i->a, C167_SPILL_SCRATCH_1);
                if (strcmp(a, "R0") != 0) {
                    char ops[32]; snprintf(ops, sizeof(ops), "R0, %s", a);
                    emit_raw(cg, NULL, "MOV", ops, "return value");
                }
            }
            emit_epilogue(cg);
            emit_raw(cg, NULL, (cg->fn->attrs & ATTR_INTERRUPT) ? "RETI" : "RET", NULL, NULL);
            break;
        }
    }
}

static int align2(int x) { return (x + 1) & ~1; }

AsmProgram *c167_codegen(IrModule *mod) {
    AsmProgram *prog = xalloc(sizeof(AsmProgram));

    for (IrGlobal *g = mod->globals; g; g = g->next) {
        AsmGlobal *ag = xalloc(sizeof(AsmGlobal));
        ag->name = strdup(g->sym->name);
        ag->sym = g->sym;
        ag->next = prog->globals;
        prog->globals = ag;
    }

    for (IrFunc *fn = mod->funcs; fn; fn = fn->next) {
        int offset = 0;
        for (int i = 0; i < fn->nparams; i++) {
            fn->params[i]->stack_offset = offset;
            offset += align2(type_size(fn->params[i]->type));
        }
        for (int i = 0; i < fn->nlocals; i++) {
            fn->locals[i]->stack_offset = offset;
            offset += align2(type_size(fn->locals[i]->type));
        }

        RegAllocResult *ra = regalloc_run(fn, offset);

        AsmFunc *af = xalloc(sizeof(AsmFunc));
        af->name = strdup(fn->name);
        af->is_global = 1;
        af->next = prog->funcs;
        prog->funcs = af;

        CG cg = {0};
        cg.af = af; cg.fn = fn; cg.ra = ra;
        cg.frame_size = align2(offset + ra->spill_area_size);

        emit_raw(&cg, fn->name, NULL, NULL, NULL);
        emit_prologue(&cg);

        for (IrInst *i = fn->head; i; i = i->next) gen_inst(&cg, i, i->next);

        regalloc_free(ra);
    }

    /* funcs were prepended; reverse to preserve source order */
    AsmFunc *rev = NULL, *cur = prog->funcs;
    while (cur) { AsmFunc *nx = cur->next; cur->next = rev; rev = cur; cur = nx; }
    prog->funcs = rev;

    AsmGlobal *grev = NULL, *gcur = prog->globals;
    while (gcur) { AsmGlobal *nx = gcur->next; gcur->next = grev; grev = gcur; gcur = nx; }
    prog->globals = grev;

    return prog;
}
