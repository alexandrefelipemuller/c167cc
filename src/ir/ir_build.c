#include "c167cc/ir.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

typedef struct {
    IrModule *mod;
    IrFunc *fn;
    Scope *scope;
    int next_vreg;
    int label_ctr;
    char *break_label;
    char *continue_label;
    /* Convenção "sret" (achado 21/08/2026 compilando reimplementacao_c pela
       1ª vez - retorno de struct por valor é usado em quase toda função de
       resposta K-line): quando a função atual devolve struct/union, ganha
       um parâmetro OCULTO extra (ponteiro pro espaço de retorno do
       chamador, sempre o 1º argumento de verdade) - `sret_sym` é o símbolo
       desse parâmetro, usado por STMT_RETURN pra copiar campo a campo em
       vez de tentar mover a struct inteira num vreg (que não existe pra
       isso neste backend). NULL = função atual não devolve struct/union. */
    Symbol *sret_sym;
} Builder;

static void *xalloc(size_t n) { void *p = calloc(1, n); return p; }

static int new_vreg(Builder *b) { return b->fn->nvregs = b->next_vreg++; }

static char *fmt_label(Builder *b, const char *tag) {
    char buf[128];
    snprintf(buf, sizeof(buf), ".L%s_%s_%d", b->fn->name, tag, b->label_ctr++);
    return strdup(buf);
}

static IrInst *emit(Builder *b, IrOpKind kind) {
    IrInst *i = xalloc(sizeof(IrInst));
    i->kind = kind;
    i->dst = -1; i->a = -1; i->b = -1;
    if (!b->fn->head) { b->fn->head = b->fn->tail = i; }
    else { b->fn->tail->next = i; b->fn->tail = i; }
    return i;
}

static int type_bytes(Type *t) { return type_size(t); }

/* struct and union both use TY_STRUCT/StructDef; only the error wording differs. */
static const char *agg_kind_name(const Type *t) { return t->struct_def->is_union ? "union" : "struct"; }

static int gen_expr(Builder *b, Expr *e, Type **out_type);
static void gen_stmt(Builder *b, Stmt *s);

static Type *u16_type(void) { return type_new(TY_U16); }

/* Achado 02/09/2026 (projeto irmão Sirius32, comparando código compilado
   contra o binário original de verdade num simulador): este backend NÃO
   tem suporte real a valor de 32 bits em lugar nenhum do pipeline -
   `uint32_t`/`int32_t` só reservam ARMAZENAMENTO de 4 bytes corretamente
   (`type_size`), mas todo IR_BINOP/IR_STORE_SYM/etc trabalha com um único
   vreg de 16 bits, então qualquer valor que dependa de verdade dos 32
   bits (o caso mais comum: `MULU`/`MUL` real do C167 sempre produz um
   produto de 32 bits em MDL:MDH) perde a metade alta silenciosamente -
   sem erro, sem aviso, só um `0` incorreto na prática. Somas/subtrações
   "de 32 bits" já usadas no projeto (aritmética saturada) funcionavam por
   coincidência (o resultado truncado de 16 bits já é matematicamente
   equivalente ao que a checagem de overflow por comparação de faixa
   precisa) - multiplicação não tem essa sorte, a metade alta contém
   informação que não existe na visão de 16 bits de jeito nenhum.
   Consertar isso de verdade (registrador-par em todo o IR/codegen) é
   fora de escopo aqui (feature grande, risco de regressão no resto do
   pipeline de 16 bits que já funciona) - em vez disso, reconhece o
   padrão EXATO `dst_de_32_bits = a * b` (atribuição direta de uma
   multiplicação a uma variável/global já declarada de 32 bits, com ou
   sem cast explícito nos operandos) e emite os dois `MOV` (palavra baixa
   de MDL, palavra alta de MDH) direto pro endereço da variável via
   IR_MUL32_STORE_SYM - sem inventar registrador-par de propósito geral.
   Só dispara quando o DESTINO já é uma variável simples de 32 bits (não
   dentro de expressão maior, não struct/array/membro) - fora desse
   padrão exato, cai no caminho antigo (mesmo bug de sempre, documentado
   acima, não silenciosamente "quase certo" - mantém o comportamento
   antigo em vez de arriscar miscompilar um caso não previsto). */
/* Retorna -1 se o padrão não bate (chamador deve seguir pro caminho
   normal, SEM ter emitido nada ainda - por isso os operandos só são
   avaliados depois de confirmar o tipo do destino). Retorna o vreg do
   operando esquerdo do produto em caso de sucesso (valor arbitrário só
   pra dar um retorno válido pra gen_expr chamador - o valor de verdade
   da expressão já foi gravado direto no símbolo via IR_MUL32_STORE_SYM,
   nenhum uso real depende do "valor" desta expressão de atribuição). */
static int try_gen_widening_mul_store_sym(Builder *b, Symbol *dst_sym, Expr *rhs, SrcLoc loc) {
    if (!dst_sym) return -1;
    if (dst_sym->type->kind != TY_U32 && dst_sym->type->kind != TY_I32) return -1;
    if (rhs->kind != EXPR_BINARY || rhs->op != OP_MUL) return -1;

    Type *lt, *rt;
    int lv = gen_expr(b, rhs->lhs, &lt);
    int rv = gen_expr(b, rhs->rhs, &rt);

    IrInst *m = emit(b, IR_MUL32_STORE_SYM);
    m->a = lv; m->b = rv;
    m->is_signed = type_is_signed(dst_sym->type);
    m->sym = dst_sym;
    m->loc = loc;
    return lv;
}

/* Narrow counterpart to try_gen_widening_mul_store_sym() above: the 4
   research/biblioteca_aritmetica saturating-multiply routines all follow
   `produto = a * b; ... produto >> N` to pull a scaled slice out of the
   32-bit product (the real C167 does this with plain SHL/SHR on MDH/MDL,
   see the disassembly comments in those files) - without this, reading
   `sym32 >> N` falls into the generic 16-bit-only IR_BINOP path and
   silently operates on just the low word, discarding MDH the same way
   plain multiplication used to. Recognizes `ident_of_32bit_type >> CONST`
   (CONST in [0,31]) and lowers it directly to IR_SHR32_SYM instead of
   inventing general register-pair shift support. Returns -1 if the exact
   pattern doesn't match (caller falls through to the normal path, nothing
   emitted yet). */
static int try_gen_shr32_sym(Builder *b, Expr *e, Type **out_type) {
    if (e->kind != EXPR_BINARY || e->op != OP_SHR) return -1;
    if (e->lhs->kind != EXPR_IDENT) return -1;
    Symbol *sym = scope_lookup(b->scope, e->lhs->name);
    if (!sym) return -1;
    if (sym->type->kind != TY_U32 && sym->type->kind != TY_I32) return -1;
    if (e->rhs->kind != EXPR_INT_LIT) return -1;
    long n = e->rhs->ival;
    if (n < 0 || n > 31) return -1;

    e->lhs->sym = sym;
    IrInst *i = emit(b, IR_SHR32_SYM);
    i->dst = new_vreg(b);
    i->sym = sym;
    i->imm = n;
    i->is_signed = type_is_signed(sym->type);
    i->loc = e->loc;
    *out_type = u16_type();
    return i->dst;
}

/* Narrow counterpart to try_gen_shr32_sym() above, same rationale (see the
   long comment on IR_DIV32_SYM in ir.h, found 03/09/2026 with the
   bilinear-interpolation cluster in the sibling Sirius32 project): plain
   IR_BINOP(OP_DIV/OP_MOD) only ever sees a single 16-bit vreg per operand,
   so `sym32 / x` silently truncated the dividend to its low word before
   dividing. Recognizes `ident_of_32bit_type / expr` or `ident_of_32bit_type
   % expr` (the divisor can be any ordinary 16-bit-producing expression,
   unlike the shift/mul patterns which only look at the immediate operand)
   and lowers it directly to IR_DIV32_SYM (DIVLU/DIVL: dividend pre-loaded
   into MDL:MDH) instead of inventing general register-pair division.
   Returns -1 if the pattern doesn't match (caller falls through to the
   normal, still-buggy-for-this-case path - not changing behavior outside
   this exact shape). */
static int try_gen_div32_sym(Builder *b, Expr *e, Type **out_type) {
    if (e->kind != EXPR_BINARY || (e->op != OP_DIV && e->op != OP_MOD)) return -1;
    if (e->lhs->kind != EXPR_IDENT) return -1;
    Symbol *sym = scope_lookup(b->scope, e->lhs->name);
    if (!sym) return -1;
    if (sym->type->kind != TY_U32 && sym->type->kind != TY_I32) return -1;

    e->lhs->sym = sym;
    Type *rt;
    int rv = gen_expr(b, e->rhs, &rt);

    IrInst *i = emit(b, IR_DIV32_SYM);
    i->dst = new_vreg(b);
    i->sym = sym;
    i->b = rv;
    i->op = e->op;
    i->is_signed = type_is_signed(sym->type);
    i->loc = e->loc;
    *out_type = u16_type();
    return i->dst;
}

/* Compute address of an lvalue into a vreg (address-of semantics). Returns element type. */
static int gen_lvalue_addr(Builder *b, Expr *e, Type **elem_type) {
    if (e->kind == EXPR_IDENT) {
        Symbol *sym = scope_lookup(b->scope, e->name);
        if (!sym) { fprintf(stderr, "%s:%d: error: undeclared identifier '%s'\n", e->loc.file, e->loc.line, e->name); exit(1); }
        e->sym = sym;
        IrInst *i = emit(b, IR_LOAD_ADDR);
        i->dst = new_vreg(b);
        i->sym = sym;
        i->loc = e->loc;
        *elem_type = sym->type;
        return i->dst;
    } else if (e->kind == EXPR_DEREF) {
        Type *ptrty;
        int p = gen_expr(b, e->rhs, &ptrty);
        *elem_type = ptrty->pointee ? ptrty->pointee : u16_type();
        return p;
    } else if (e->kind == EXPR_INDEX) {
        Type *basety;
        int base = gen_expr(b, e->base, &basety);
        Type *elemty = basety->pointee ? basety->pointee : u16_type();
        Type *ixty;
        int idx = gen_expr(b, e->index, &ixty);
        int esz = type_bytes(elemty);
        int off;
        if (esz == 1) { off = idx; }
        else {
            IrInst *c = emit(b, IR_CONST);
            c->dst = new_vreg(b); c->imm = esz; c->size = 2;
            IrInst *m = emit(b, IR_BINOP);
            m->dst = new_vreg(b); m->op = OP_MUL; m->a = idx; m->b = c->dst;
            m->size = 2; m->loc = e->loc;
            off = m->dst;
        }
        IrInst *add = emit(b, IR_BINOP);
        add->dst = new_vreg(b); add->op = OP_ADD; add->a = base; add->b = off; add->size = 2;
        add->loc = e->loc;
        *elem_type = elemty;
        return add->dst;
    } else if (e->kind == EXPR_MEMBER) {
        Type *basety;
        int base = gen_lvalue_addr(b, e->base, &basety);
        if (basety->kind != TY_STRUCT) {
            fprintf(stderr, "%s:%d: error: member access on non-struct type\n", e->loc.file, e->loc.line);
            exit(1);
        }
        const StructField *f = struct_def_find_field(basety->struct_def, e->name);
        if (!f) {
            fprintf(stderr, "%s:%d: error: %s '%s' has no member '%s'\n", e->loc.file, e->loc.line, agg_kind_name(basety), basety->struct_def->name, e->name);
            exit(1);
        }
        if (f->offset != 0) {
            IrInst *c = emit(b, IR_CONST);
            c->dst = new_vreg(b); c->imm = f->offset; c->size = 2;
            IrInst *add = emit(b, IR_BINOP);
            add->dst = new_vreg(b); add->op = OP_ADD; add->a = base; add->b = c->dst;
            add->size = 2; add->loc = e->loc;
            base = add->dst;
        }
        *elem_type = f->type;
        return base;
    }
    fprintf(stderr, "%s:%d: error: expression is not an lvalue\n", e->loc.file, e->loc.line);
    exit(1);
}

/* Soma um deslocamento constante (conhecido em tempo de compilação - índice
   de array/campo de struct) a um endereço já calculado em runtime, gerando
   IR_CONST+IR_BINOP só quando o deslocamento não é zero (evita lixo de
   "+0" - ver EXPR_MEMBER em gen_lvalue_addr, mesmo padrão). */
static int add_const_offset(Builder *b, int addr_vreg, int off, SrcLoc loc) {
    if (off == 0) return addr_vreg;
    IrInst *c = emit(b, IR_CONST);
    c->dst = new_vreg(b); c->imm = off; c->size = 2; c->loc = loc;
    IrInst *add = emit(b, IR_BINOP);
    add->dst = new_vreg(b); add->op = OP_ADD; add->a = addr_vreg; add->b = c->dst; add->size = 2; add->loc = loc;
    return add->dst;
}

/* Copia uma struct/union/array campo a campo (ou elemento a elemento) de
   `src_base+off` pra `dst_base+off`, achatando aninhamento (array de
   struct, struct com campo array etc) - mesma varredura recursiva de
   `flatten_init_list()` no backend C167 (codegen.c), só que gera CÓPIA EM
   RUNTIME (load+store) em vez de dado constante, porque um dos dois lados
   (ou os dois) pode não ser conhecido em tempo de compilação. Implementa
   tanto atribuição de struct (`x = y;`) quanto o `return x;` de uma função
   que devolve struct (ver `sret_sym` no Builder) - nenhum dos dois cabe
   num único vreg neste backend (ver docs/limitations.md), então "mover a
   struct" vira "copiar os campos de verdade". */
static void gen_struct_copy(Builder *b, int dst_base, int src_base, Type *type, int off, SrcLoc loc) {
    if (type->is_array) {
        Type *elem = type->pointee;
        int esz = type_bytes(elem);
        for (long i = 0; i < (long)type->array_len; i++)
            gen_struct_copy(b, dst_base, src_base, elem, off + (int)(i * esz), loc);
        return;
    }
    if (type->kind == TY_STRUCT) {
        StructDef *sd = type->struct_def;
        for (int i = 0; i < sd->nfields; i++)
            gen_struct_copy(b, dst_base, src_base, sd->fields[i].type, off + sd->fields[i].offset, loc);
        return;
    }
    int d = add_const_offset(b, dst_base, off, loc);
    int s = add_const_offset(b, src_base, off, loc);
    IrInst *ld = emit(b, IR_LOAD_MEM);
    ld->dst = new_vreg(b); ld->a = s; ld->size = type_bytes(type); ld->is_signed = type_is_signed(type); ld->loc = loc;
    IrInst *st = emit(b, IR_STORE_MEM);
    st->a = d; st->b = ld->dst; st->size = type_bytes(type); st->loc = loc;
}

static int gen_load_lvalue(Builder *b, Expr *e, Type **out_type) {
    if (e->kind == EXPR_IDENT) {
        Symbol *sym = scope_lookup(b->scope, e->name);
        if (!sym) { fprintf(stderr, "%s:%d: error: undeclared identifier '%s'\n", e->loc.file, e->loc.line, e->name); exit(1); }
        e->sym = sym;
        if (sym->type->is_array) {
            /* array decays to address */
            IrInst *i = emit(b, IR_LOAD_ADDR);
            i->dst = new_vreg(b); i->sym = sym; i->loc = e->loc;
            *out_type = type_new_ptr(sym->type->pointee);
            return i->dst;
        }
        if (sym->type->kind == TY_FUNC) {
            /* a bare function name decays to its address, same as an array does */
            IrInst *i = emit(b, IR_LOAD_ADDR);
            i->dst = new_vreg(b); i->sym = sym; i->loc = e->loc;
            *out_type = type_new_ptr(sym->type);
            return i->dst;
        }
        if (sym->type->kind == TY_STRUCT) {
            fprintf(stderr, "%s:%d: error: cannot use %s '%s' as a value (copy fields individually, or use '&%s')\n", e->loc.file, e->loc.line, agg_kind_name(sym->type), sym->type->struct_def->name, e->name);
            exit(1);
        }
        IrInst *i = emit(b, IR_LOAD_SYM);
        i->dst = new_vreg(b);
        i->sym = sym;
        i->size = type_bytes(sym->type);
        i->is_signed = type_is_signed(sym->type);
        i->loc = e->loc;
        i->comment = NULL;
        *out_type = sym->type;
        return i->dst;
    }
    Type *elemty;
    int addr = gen_lvalue_addr(b, e, &elemty);
    if (elemty->kind == TY_STRUCT) {
        fprintf(stderr, "%s:%d: error: cannot use %s '%s' as a value (copy fields individually, or take its address)\n", e->loc.file, e->loc.line, agg_kind_name(elemty), elemty->struct_def->name);
        exit(1);
    }
    if (elemty->is_array) {
        /* achado 21/08/2026: um campo de array dentro de struct/union
           (`p->arr`, não uma variável array solta - essa já decaía certo,
           ver o ramo EXPR_IDENT acima) precisa da MESMA regra de "decay
           pra endereço" - sem isso, `gen_expr` cairia direto no
           IR_LOAD_MEM abaixo e tentaria "carregar o valor" de um array
           inteiro (lendo só os primeiros 2 bytes como se fossem um
           escalar), miscompilando qualquer `p->arr[i]`/`p->arr` usado como
           ponteiro. */
        *out_type = type_new_ptr(elemty->pointee);
        return addr;
    }
    IrInst *ld = emit(b, IR_LOAD_MEM);
    ld->dst = new_vreg(b); ld->a = addr; ld->size = type_bytes(elemty);
    ld->is_signed = type_is_signed(elemty);
    ld->loc = e->loc;
    *out_type = elemty;
    return ld->dst;
}

static void gen_store_lvalue(Builder *b, Expr *lhs, int src_vreg, Type *src_type) {
    (void)src_type;
    if (lhs->kind == EXPR_IDENT) {
        Symbol *sym = scope_lookup(b->scope, lhs->name);
        if (!sym) { fprintf(stderr, "%s:%d: error: undeclared identifier '%s'\n", lhs->loc.file, lhs->loc.line, lhs->name); exit(1); }
        lhs->sym = sym;
        if (sym->type->kind == TY_STRUCT) {
            fprintf(stderr, "%s:%d: error: %s assignment is not supported (copy fields individually)\n", lhs->loc.file, lhs->loc.line, agg_kind_name(sym->type));
            exit(1);
        }
        IrInst *i = emit(b, IR_STORE_SYM);
        i->sym = sym; i->a = src_vreg; i->size = type_bytes(sym->type); i->loc = lhs->loc;
        return;
    }
    Type *elemty;
    int addr = gen_lvalue_addr(b, lhs, &elemty);
    if (elemty->kind == TY_STRUCT) {
        fprintf(stderr, "%s:%d: error: %s assignment is not supported (copy fields individually)\n", lhs->loc.file, lhs->loc.line, agg_kind_name(elemty));
        exit(1);
    }
    if (elemty->kind == TY_FUNC) {
        fprintf(stderr, "%s:%d: error: expression is not an lvalue\n", lhs->loc.file, lhs->loc.line);
        exit(1);
    }
    IrInst *st = emit(b, IR_STORE_MEM);
    st->a = addr; st->b = src_vreg; st->size = type_bytes(elemty); st->loc = lhs->loc;
}

/* Chama uma função que devolve struct/union por valor, empurrando
   `dest_addr_vreg` como argumento OCULTO na frente dos argumentos reais
   (convenção "sret", ver comentário do campo `sret_sym` no Builder) -
   usado só nos 2 contextos onde já se sabe o destino final ANTES de
   chamar (`x = f(...);` e `T x = f(...);`, ver EXPR_ASSIGN/STMT_DECL
   abaixo), o que evita uma cópia extra. Chamar uma função-que-devolve-
   struct em qualquer OUTRO contexto (ex. aninhada dentro de outra
   expressão) ainda não é suportado - não usado por nenhum código real
   compilado até agora. */
static int gen_call_into(Builder *b, Expr *call, int dest_addr_vreg) {
    Expr *callee = call->callee;
    while (callee->kind == EXPR_DEREF) callee = callee->rhs;
    Symbol *fsym = (callee->kind == EXPR_IDENT) ? scope_lookup(b->scope, callee->name) : NULL;
    int is_direct = fsym && fsym->kind == SYM_FUNC;
    int fn_addr = -1;
    if (is_direct) {
        callee->sym = fsym;
    } else {
        Type *ct;
        fn_addr = gen_expr(b, callee, &ct);
    }
    int nargs = call->nargs + 1;
    int *argv = xalloc(sizeof(int) * nargs);
    argv[0] = dest_addr_vreg;
    for (int i = 0; i < call->nargs; i++) {
        Type *at; argv[i + 1] = gen_expr(b, call->args[i], &at);
    }
    IrInst *c = emit(b, IR_CALL);
    c->dst = new_vreg(b);
    if (is_direct) c->call_name = strdup(callee->name); else c->a = fn_addr;
    c->args = argv; c->nargs = nargs;
    c->loc = call->loc;
    c->size = 0;
    return c->dst;
}

/* Verdadeiro só se `e` for uma struct/union pura (não ponteiro pra uma) -
   usado por EXPR_ASSIGN/STMT_DECL pra decidir entre o caminho normal
   (1 vreg) e o caminho de cópia campo a campo/sret. */
static int expr_is_struct_lvalue(Builder *b, Expr *e, Type **out_type) {
    if (e->kind == EXPR_IDENT) {
        Symbol *sym = scope_lookup(b->scope, e->name);
        if (sym && sym->type->kind == TY_STRUCT) { *out_type = sym->type; return 1; }
        return 0;
    }
    if (e->kind == EXPR_MEMBER || e->kind == EXPR_INDEX || e->kind == EXPR_DEREF) {
        /* resolve o tipo sem duplicar toda a lógica de gen_lvalue_addr:
           gera o endereço numa cópia descartável do builder não é viável
           aqui (efeitos colaterais de emissão de IR), então só cobre o
           caso realmente usado em reimplementacao_c hoje (EXPR_IDENT) -
           membro/índice de struct como alvo de atribuição de struct
           inteira não ocorre no código já portado; se aparecer, cai no
           caminho normal e dá o erro antigo, claro, não miscompila. */
        return 0;
    }
    return 0;
}

static int gen_expr(Builder *b, Expr *e, Type **out_type) {
    switch (e->kind) {
        case EXPR_INT_LIT: {
            IrInst *i = emit(b, IR_CONST);
            i->dst = new_vreg(b); i->imm = e->ival; i->size = 2; i->loc = e->loc;
            *out_type = u16_type();
            return i->dst;
        }
        case EXPR_IDENT:
        case EXPR_MEMBER:
            return gen_load_lvalue(b, e, out_type);
        case EXPR_CAST: {
            Type *srcty;
            int v = gen_expr(b, e->rhs, &srcty);
            IrInst *i = emit(b, IR_UNOP);
            i->dst = new_vreg(b); i->op = OP_ASSIGN; i->a = v;
            i->size = type_bytes(e->cast_type); i->is_signed = type_is_signed(e->cast_type);
            i->loc = e->loc;
            *out_type = e->cast_type;
            return i->dst;
        }
        case EXPR_ADDR: {
            Type *elemty;
            int addr = gen_lvalue_addr(b, e->rhs, &elemty);
            *out_type = type_new_ptr(elemty);
            return addr;
        }
        case EXPR_DEREF: {
            Type *ptrty;
            int addr = gen_expr(b, e->rhs, &ptrty);
            Type *elemty = ptrty->pointee ? ptrty->pointee : u16_type();
            if (elemty->kind == TY_FUNC) {
                /* dereferencing a function pointer yields a function
                   designator, not a memory load - *fp is just fp */
                *out_type = ptrty;
                return addr;
            }
            IrInst *ld = emit(b, IR_LOAD_MEM);
            ld->dst = new_vreg(b); ld->a = addr; ld->size = type_bytes(elemty);
            ld->is_signed = type_is_signed(elemty); ld->loc = e->loc;
            *out_type = elemty;
            return ld->dst;
        }
        case EXPR_INDEX: {
            Type *basety;
            int base = gen_expr(b, e->base, &basety);
            Type *elemty = basety->pointee ? basety->pointee : u16_type();
            Type *ixty;
            int idx = gen_expr(b, e->index, &ixty);
            int esz = type_bytes(elemty);
            int off = idx;
            if (esz != 1) {
                IrInst *c = emit(b, IR_CONST);
                c->dst = new_vreg(b); c->imm = esz; c->size = 2;
                IrInst *m = emit(b, IR_BINOP);
                m->dst = new_vreg(b); m->op = OP_MUL; m->a = idx; m->b = c->dst; m->size = 2;
                off = m->dst;
            }
            IrInst *add = emit(b, IR_BINOP);
            add->dst = new_vreg(b); add->op = OP_ADD; add->a = base; add->b = off; add->size = 2;
            IrInst *ld = emit(b, IR_LOAD_MEM);
            ld->dst = new_vreg(b); ld->a = add->dst; ld->size = type_bytes(elemty);
            ld->is_signed = type_is_signed(elemty); ld->loc = e->loc;
            *out_type = elemty;
            return ld->dst;
        }
        case EXPR_UNARY: {
            Type *t;
            int v = gen_expr(b, e->rhs, &t);
            IrInst *i = emit(b, IR_UNOP);
            i->dst = new_vreg(b); i->op = e->op; i->a = v;
            i->size = type_bytes(t); i->is_signed = type_is_signed(t); i->loc = e->loc;
            *out_type = (e->op == OP_NOT) ? u16_type() : t;
            return i->dst;
        }
        case EXPR_BINARY: {
            int shr32 = try_gen_shr32_sym(b, e, out_type);
            if (shr32 >= 0) return shr32;
            int div32 = try_gen_div32_sym(b, e, out_type);
            if (div32 >= 0) return div32;
            if (e->op == OP_LAND || e->op == OP_LOR) {
                /* short-circuit evaluation */
                char *l_rhs = fmt_label(b, e->op == OP_LAND ? "and_rhs" : "or_rhs");
                char *l_true = fmt_label(b, "logic_true");
                char *l_false = fmt_label(b, "logic_false");
                char *l_end = fmt_label(b, "logic_end");
                Type *lt;
                int lv = gen_expr(b, e->lhs, &lt);
                IrInst *cj = emit(b, IR_CJMP);
                cj->a = lv;
                if (e->op == OP_LAND) { cj->true_label = strdup(l_rhs); cj->false_label = strdup(l_false); }
                else { cj->true_label = strdup(l_true); cj->false_label = strdup(l_rhs); }
                emit(b, IR_LABEL)->label = strdup(l_rhs);
                Type *rt;
                int rv = gen_expr(b, e->rhs, &rt);
                IrInst *cjr = emit(b, IR_CJMP);
                cjr->a = rv; cjr->true_label = strdup(l_true); cjr->false_label = strdup(l_false);
                int dst = new_vreg(b);
                emit(b, IR_LABEL)->label = strdup(l_true);
                IrInst *c1 = emit(b, IR_CONST); c1->dst = dst; c1->imm = 1; c1->size = 2;
                emit(b, IR_JMP)->label = strdup(l_end);
                emit(b, IR_LABEL)->label = strdup(l_false);
                IrInst *c0 = emit(b, IR_CONST); c0->dst = dst; c0->imm = 0; c0->size = 2;
                emit(b, IR_LABEL)->label = strdup(l_end);
                *out_type = u16_type();
                return dst;
            }
            Type *lt, *rt;
            int lv = gen_expr(b, e->lhs, &lt);
            int rv = gen_expr(b, e->rhs, &rt);
            int sz = type_bytes(lt) >= type_bytes(rt) ? type_bytes(lt) : type_bytes(rt);
            if (sz < 2) sz = 2;
            IrInst *i = emit(b, IR_BINOP);
            i->dst = new_vreg(b); i->op = e->op; i->a = lv; i->b = rv;
            i->size = sz; i->is_signed = type_is_signed(lt) || type_is_signed(rt);
            i->loc = e->loc;
            switch (e->op) {
                case OP_EQ: case OP_NE: case OP_LT: case OP_GT: case OP_LE: case OP_GE:
                    *out_type = u16_type(); break;
                default:
                    *out_type = type_bytes(lt) >= type_bytes(rt) ? lt : rt;
            }
            return i->dst;
        }
        case EXPR_ASSIGN: {
            if (e->op == OP_ASSIGN) {
                Type *lhs_ty;
                if (expr_is_struct_lvalue(b, e->lhs, &lhs_ty)) {
                    Type *elemty;
                    int dst_addr = gen_lvalue_addr(b, e->lhs, &elemty);
                    if (e->rhs->kind == EXPR_CALL) {
                        gen_call_into(b, e->rhs, dst_addr);
                    } else {
                        Type *srcty;
                        if (!expr_is_struct_lvalue(b, e->rhs, &srcty)) {
                            fprintf(stderr, "%s:%d: error: struct assignment source is not a plain "
                                             "struct variable or function call (not supported)\n",
                                    e->loc.file, e->loc.line);
                            exit(1);
                        }
                        int src_addr = gen_lvalue_addr(b, e->rhs, &srcty);
                        gen_struct_copy(b, dst_addr, src_addr, lhs_ty, 0, e->loc);
                    }
                    *out_type = lhs_ty;
                    return dst_addr;
                }
                if (e->lhs->kind == EXPR_IDENT) {
                    Symbol *dst_sym = scope_lookup(b->scope, e->lhs->name);
                    int wv = try_gen_widening_mul_store_sym(b, dst_sym, e->rhs, e->loc);
                    if (wv >= 0) {
                        /* `x = a * b;` onde x já é uint32_t/int32_t - ver
                           comentário de try_gen_widening_mul_store_sym. */
                        e->lhs->sym = dst_sym;
                        *out_type = dst_sym->type;
                        return wv;
                    }
                }
            }
            Type *rt;
            int rv = gen_expr(b, e->rhs, &rt);
            if (e->op != OP_ASSIGN) {
                Type *lt;
                int lv = gen_load_lvalue(b, e->lhs, &lt);
                IrInst *bi = emit(b, IR_BINOP);
                bi->dst = new_vreg(b); bi->op = e->op; bi->a = lv; bi->b = rv;
                bi->size = type_bytes(lt); bi->is_signed = type_is_signed(lt); bi->loc = e->loc;
                rv = bi->dst; rt = lt;
            }
            gen_store_lvalue(b, e->lhs, rv, rt);
            *out_type = rt;
            return rv;
        }
        case EXPR_TERNARY: {
            char *l_true = fmt_label(b, "tern_true");
            char *l_false = fmt_label(b, "tern_false");
            char *l_end = fmt_label(b, "tern_end");
            Type *ct;
            int cv = gen_expr(b, e->cond, &ct);
            IrInst *cj = emit(b, IR_CJMP); cj->a = cv; cj->true_label = strdup(l_true); cj->false_label = strdup(l_false);
            int dst = new_vreg(b);
            emit(b, IR_LABEL)->label = strdup(l_true);
            Type *tt;
            int tv = gen_expr(b, e->then_e, &tt);
            IrInst *mv1 = emit(b, IR_MOV); mv1->dst = dst; mv1->a = tv; mv1->size = type_bytes(tt);
            emit(b, IR_JMP)->label = strdup(l_end);
            emit(b, IR_LABEL)->label = strdup(l_false);
            Type *et;
            int ev = gen_expr(b, e->else_e, &et);
            IrInst *mv2 = emit(b, IR_MOV); mv2->dst = dst; mv2->a = ev; mv2->size = type_bytes(et);
            emit(b, IR_LABEL)->label = strdup(l_end);
            *out_type = tt;
            return dst;
        }
        case EXPR_CALL: {
            /* (*fp)(...) is the same call as fp(...) - a function pointer
               dereference yields a function designator, not a memory load
               (see the EXPR_DEREF comment below), so strip any number of
               leading derefs before deciding how to call. */
            Expr *callee = e->callee;
            while (callee->kind == EXPR_DEREF) callee = callee->rhs;

            Symbol *fsym = (callee->kind == EXPR_IDENT) ? scope_lookup(b->scope, callee->name) : NULL;
            int is_direct = fsym && fsym->kind == SYM_FUNC;

            int fn_addr = -1;
            Type *ret_type;
            if (is_direct) {
                callee->sym = fsym;
                ret_type = fsym->func ? fsym->func->ret_type : u16_type();
            } else {
                Type *ct;
                fn_addr = gen_expr(b, callee, &ct);
                if (!(ct->kind == TY_PTR && ct->pointee && ct->pointee->kind == TY_FUNC)) {
                    fprintf(stderr, "%s:%d: error: called object is not a function or function pointer\n", e->loc.file, e->loc.line);
                    exit(1);
                }
                ret_type = ct->pointee->func_ret;
            }

            int *argv = xalloc(sizeof(int) * (e->nargs ? e->nargs : 1));
            for (int i = 0; i < e->nargs; i++) {
                Type *at; argv[i] = gen_expr(b, e->args[i], &at);
            }
            IrInst *c = emit(b, IR_CALL);
            c->dst = new_vreg(b);
            if (is_direct) c->call_name = strdup(callee->name);
            else c->a = fn_addr;
            c->args = argv; c->nargs = e->nargs;
            c->loc = e->loc;
            *out_type = ret_type;
            c->size = type_bytes(*out_type);
            return c->dst;
        }
        case EXPR_POSTINC: case EXPR_POSTDEC:
        case EXPR_PREINC: case EXPR_PREDEC: {
            Type *lt;
            int lv = gen_load_lvalue(b, e->lhs, &lt);
            IrInst *c = emit(b, IR_CONST); c->dst = new_vreg(b); c->imm = 1; c->size = 2;
            IrInst *bi = emit(b, IR_BINOP);
            bi->dst = new_vreg(b);
            bi->op = (e->kind == EXPR_POSTINC || e->kind == EXPR_PREINC) ? OP_ADD : OP_SUB;
            bi->a = lv; bi->b = c->dst; bi->size = type_bytes(lt); bi->is_signed = type_is_signed(lt);
            gen_store_lvalue(b, e->lhs, bi->dst, lt);
            *out_type = lt;
            return (e->kind == EXPR_POSTINC || e->kind == EXPR_POSTDEC) ? lv : bi->dst;
        }
    }
    fprintf(stderr, "internal error: unhandled expr kind\n");
    exit(1);
}

static void gen_block(Builder *b, Stmt *blk) {
    Scope *outer = b->scope;
    if (!blk->transparent) b->scope = scope_new(outer);
    for (int i = 0; i < blk->nstmts; i++) gen_stmt(b, blk->stmts[i]);
    b->scope = outer;
}

static void gen_stmt(Builder *b, Stmt *s) {
    switch (s->kind) {
        case STMT_EXPR:
            if (s->expr) { Type *t; gen_expr(b, s->expr, &t); }
            break;
        case STMT_DECL: {
            Symbol *sym = scope_declare(b->scope, s->decl->name, SYM_LOCAL, s->decl->type);
            s->decl->sym = sym;
            b->fn->locals = realloc(b->fn->locals, sizeof(Symbol*) * (b->fn->nlocals + 1));
            b->fn->locals[b->fn->nlocals++] = sym;
            if (s->decl->init) {
                if (sym->type->kind == TY_STRUCT && s->decl->init->kind == EXPR_CALL) {
                    /* `struct X x = f(...);` onde f devolve struct por
                       valor (convenção sret, ver gen_call_into) - chama já
                       apontando pro slot recém-declarado, sem cópia extra. */
                    IrInst *addr = emit(b, IR_LOAD_ADDR);
                    addr->dst = new_vreg(b); addr->sym = sym; addr->loc = s->loc;
                    gen_call_into(b, s->decl->init, addr->dst);
                } else if (try_gen_widening_mul_store_sym(b, sym, s->decl->init, s->loc) >= 0) {
                    /* `uint32_t x = a * b;` - ver comentário de
                       try_gen_widening_mul_store_sym acima. */
                } else {
                    Type *t;
                    int v = gen_expr(b, s->decl->init, &t);
                    IrInst *st = emit(b, IR_STORE_SYM);
                    st->sym = sym; st->a = v; st->size = type_bytes(sym->type); st->loc = s->loc;
                }
            }
            break;
        }
        case STMT_RETURN: {
            if (b->sret_sym) {
                /* `return expr;` numa função que devolve struct por valor
                   (convenção sret) - copia campo a campo pro ponteiro
                   oculto em vez de tentar devolver um vreg (ver
                   gen_struct_copy/sret_sym no Builder). */
                if (!s->expr) {
                    fprintf(stderr, "%s:%d: error: missing return value in a function "
                                     "returning struct/union\n", s->loc.file, s->loc.line);
                    exit(1);
                }
                Type *srcty;
                if (!expr_is_struct_lvalue(b, s->expr, &srcty)) {
                    fprintf(stderr, "%s:%d: error: 'return' value must be a plain struct/union "
                                     "variable (not supported otherwise)\n", s->loc.file, s->loc.line);
                    exit(1);
                }
                int src_addr = gen_lvalue_addr(b, s->expr, &srcty);
                IrInst *ldret = emit(b, IR_LOAD_SYM);
                ldret->dst = new_vreg(b); ldret->sym = b->sret_sym; ldret->size = 2; ldret->loc = s->loc;
                gen_struct_copy(b, ldret->dst, src_addr, srcty, 0, s->loc);
                IrInst *r = emit(b, IR_RET);
                r->loc = s->loc; r->a = -1; r->size = 0;
                break;
            }
            int val = -1; int size = 0;
            if (s->expr) { Type *t; val = gen_expr(b, s->expr, &t); size = type_bytes(t); }
            IrInst *r = emit(b, IR_RET);
            r->loc = s->loc;
            r->a = val; r->size = size;
            break;
        }
        case STMT_IF: {
            char *l_then = fmt_label(b, "if_then");
            char *l_else = fmt_label(b, "if_else");
            char *l_end = fmt_label(b, "if_end");
            Type *ct; int cv = gen_expr(b, s->cond, &ct);
            IrInst *cj = emit(b, IR_CJMP);
            cj->a = cv; cj->loc = s->loc;
            cj->true_label = strdup(l_then);
            cj->false_label = strdup(s->else_s ? l_else : l_end);
            emit(b, IR_LABEL)->label = strdup(l_then);
            gen_stmt(b, s->then_s);
            if (s->else_s) {
                emit(b, IR_JMP)->label = strdup(l_end);
                emit(b, IR_LABEL)->label = strdup(l_else);
                gen_stmt(b, s->else_s);
            }
            emit(b, IR_LABEL)->label = strdup(l_end);
            break;
        }
        case STMT_WHILE: {
            char *l_cond = fmt_label(b, "while_cond");
            char *l_body = fmt_label(b, "while_body");
            char *l_end = fmt_label(b, "while_end");
            emit(b, IR_LABEL)->label = strdup(l_cond);
            Type *ct; int cv = gen_expr(b, s->cond, &ct);
            IrInst *cj = emit(b, IR_CJMP); cj->a = cv; cj->true_label = strdup(l_body); cj->false_label = strdup(l_end);
            emit(b, IR_LABEL)->label = strdup(l_body);
            char *ob = b->break_label, *oc = b->continue_label;
            b->break_label = l_end; b->continue_label = l_cond;
            gen_stmt(b, s->body);
            b->break_label = ob; b->continue_label = oc;
            emit(b, IR_JMP)->label = strdup(l_cond);
            emit(b, IR_LABEL)->label = strdup(l_end);
            break;
        }
        case STMT_FOR: {
            Scope *outer = b->scope;
            b->scope = scope_new(outer);
            if (s->for_init_decl) gen_stmt(b, s->for_init_decl);
            else if (s->for_init_expr) { Type *t; gen_expr(b, s->for_init_expr, &t); }
            char *l_cond = fmt_label(b, "for_cond");
            char *l_body = fmt_label(b, "for_body");
            char *l_post = fmt_label(b, "for_post");
            char *l_end = fmt_label(b, "for_end");
            emit(b, IR_LABEL)->label = strdup(l_cond);
            if (s->for_cond) {
                Type *ct; int cv = gen_expr(b, s->for_cond, &ct);
                IrInst *cj = emit(b, IR_CJMP); cj->a = cv; cj->true_label = strdup(l_body); cj->false_label = strdup(l_end);
            } else {
                emit(b, IR_JMP)->label = strdup(l_body);
            }
            emit(b, IR_LABEL)->label = strdup(l_body);
            char *ob = b->break_label, *oc = b->continue_label;
            b->break_label = l_end; b->continue_label = l_post;
            gen_stmt(b, s->body);
            b->break_label = ob; b->continue_label = oc;
            emit(b, IR_LABEL)->label = strdup(l_post);
            if (s->for_post) { Type *t; gen_expr(b, s->for_post, &t); }
            emit(b, IR_JMP)->label = strdup(l_cond);
            emit(b, IR_LABEL)->label = strdup(l_end);
            b->scope = outer;
            break;
        }
        case STMT_BREAK:
            if (!b->break_label) { fprintf(stderr, "%s:%d: error: break outside loop\n", s->loc.file, s->loc.line); exit(1); }
            emit(b, IR_JMP)->label = strdup(b->break_label);
            break;
        case STMT_CONTINUE:
            if (!b->continue_label) { fprintf(stderr, "%s:%d: error: continue outside loop\n", s->loc.file, s->loc.line); exit(1); }
            emit(b, IR_JMP)->label = strdup(b->continue_label);
            break;
        case STMT_BLOCK:
            gen_block(b, s);
            break;
        case STMT_SWITCH: {
            char *l_end = fmt_label(b, "switch_end");
            Type *st; int sv = gen_expr(b, s->switch_expr, &st);
            char *ob = b->break_label; b->break_label = l_end;
            char **case_labels = xalloc(sizeof(char*) * s->ncases);
            int default_idx = -1;
            for (int i = 0; i < s->ncases; i++) {
                case_labels[i] = fmt_label(b, "case");
                if (s->cases[i]->kind == STMT_DEFAULT) default_idx = i;
            }
            for (int i = 0; i < s->ncases; i++) {
                if (s->cases[i]->kind == STMT_DEFAULT) continue;
                IrInst *c = emit(b, IR_CONST); c->dst = new_vreg(b); c->imm = s->cases[i]->case_value; c->size = 2;
                IrInst *cmp = emit(b, IR_BINOP); cmp->dst = new_vreg(b); cmp->op = OP_EQ; cmp->a = sv; cmp->b = c->dst; cmp->size = 2;
                char *l_next = fmt_label(b, "case_next");
                IrInst *cj = emit(b, IR_CJMP); cj->a = cmp->dst; cj->true_label = strdup(case_labels[i]); cj->false_label = strdup(l_next);
                emit(b, IR_LABEL)->label = strdup(l_next);
            }
            emit(b, IR_JMP)->label = strdup(default_idx >= 0 ? case_labels[default_idx] : l_end);
            for (int i = 0; i < s->ncases; i++) {
                emit(b, IR_LABEL)->label = strdup(case_labels[i]);
                for (int j = 0; j < s->cases[i]->nstmts; j++) gen_stmt(b, s->cases[i]->stmts[j]);
            }
            emit(b, IR_LABEL)->label = strdup(l_end);
            b->break_label = ob;
            break;
        }
        case STMT_CASE: case STMT_DEFAULT:
            break;
    }
}

IrModule *ir_build(TranslationUnit *tu) {
    IrModule *mod = xalloc(sizeof(IrModule));
    Scope *global = scope_new(NULL);

    /* first pass: declare all globals and function symbols so forward refs work */
    for (int i = 0; i < tu->nitems; i++) {
        TopLevel *it = tu->items[i];
        if (it->kind == TOP_DECL) {
            Symbol *sym = scope_declare(global, it->decl->name, SYM_GLOBAL, it->decl->type);
            sym->attrs = it->decl->attrs;
            if (it->decl->attrs & (ATTR_RAM | ATTR_ROM)) { sym->has_abs_addr = 1; sym->abs_addr = it->decl->attr_addr; }
            it->decl->sym = sym;
            IrGlobal *g = xalloc(sizeof(IrGlobal));
            g->sym = sym; g->init = it->decl->init;
            if (!mod->globals) mod->globals = mod->globals_tail = g;
            else { mod->globals_tail->next = g; mod->globals_tail = g; }
        } else {
            Type *fty = type_new(TY_FUNC);
            fty->func_ret = it->func->ret_type;
            Symbol *sym = scope_declare(global, it->func->name, SYM_FUNC, fty);
            sym->func = it->func;
            /* Structs never fit in a vreg/register, and this backend has no
               by-value aggregate calling convention (see docs/limitations.md),
               so reject struct params/return up front instead of failing deep
               inside codegen the first time the value is actually used. */
            for (int j = 0; j < it->func->nparams; j++) {
                if (it->func->params[j]->type->kind == TY_STRUCT) {
                    fprintf(stderr, "%s:%d: error: %s parameters are not supported (pass a pointer instead)\n", it->func->loc.file, it->func->loc.line, agg_kind_name(it->func->params[j]->type));
                    exit(1);
                }
            }
            /* retorno de struct/union por valor: suportado via convenção
               "sret" (parâmetro oculto, ver Builder.sret_sym/gen_call_into
               abaixo) - achado 21/08/2026 compilando reimplementacao_c
               pela 1ª vez, onde isso é o padrão de praticamente toda
               função que monta uma resposta K-line (`kwp_response_t`). */
        }
    }

    for (int i = 0; i < tu->nitems; i++) {
        TopLevel *it = tu->items[i];
        if (it->kind != TOP_FUNC || !it->func->body) continue;
        Func *f = it->func;
        IrFunc *fn = xalloc(sizeof(IrFunc));
        fn->name = strdup(f->name);
        fn->ret_type = f->ret_type;
        fn->attrs = f->attrs;
        fn->interrupt_vector = f->interrupt_vector;

        Builder b = {0};
        b.mod = mod; b.fn = fn; b.scope = scope_new(global);

        fn->params = xalloc(sizeof(Symbol*) * (f->nparams + 1));
        if (f->ret_type->kind == TY_STRUCT) {
            /* parâmetro oculto de retorno (sret) - sempre o argumento 0 de
               verdade, na frente de qualquer parâmetro real declarado.
               Nome com prefixo improvável de colidir com código real. */
            Symbol *sret = scope_declare(b.scope, "__sret_ptr", SYM_PARAM, type_new_ptr(f->ret_type));
            fn->params[fn->nparams++] = sret;
            b.sret_sym = sret;
        }
        for (int j = 0; j < f->nparams; j++) {
            Symbol *psym = scope_declare(b.scope, f->params[j]->name, SYM_PARAM, f->params[j]->type);
            f->params[j]->sym = psym;
            fn->params[fn->nparams++] = psym;
        }

        gen_block(&b, f->body);
        /* implicit return for void/fallthrough */
        emit(&b, IR_RET)->a = -1;

        if (!mod->funcs) mod->funcs = mod->funcs_tail = fn;
        else { mod->funcs_tail->next = fn; mod->funcs_tail = fn; }
    }

    return mod;
}
