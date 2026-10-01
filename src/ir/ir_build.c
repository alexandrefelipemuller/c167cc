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
    /* > 0 enquanto o rebaixamento de 32 bits (gen_expr32 e cia) está
       emitindo - marca IrInst.wide32 (ver optimizer.c). */
    int wide32;
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
    i->wide32 = b->wide32 > 0;
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
static Type *u8_type(void) { return type_new(TY_U8); }

/* ver o bloco "Valores de 32 bits como PAR de palavras" mais abaixo */
static Type *expr_type(Builder *b, Expr *e);
static int is_wide(Builder *b, Expr *e);

/* ATUALIZAÇÃO 01/10/2026 (BUG-6/7/8/10/11/15 da Sirius32): o texto abaixo
   descreve o estado ANTERIOR. Expressões de 32 bits agora são avaliadas
   como par de palavras (ver o bloco "Valores de 32 bits como PAR de
   palavras" mais abaixo); as formas estreitas daqui continuam sendo
   tentadas primeiro, só quando os operandos são valores de 16 bits
   alargados (is_wide() falso), porque nesse caso já davam o código certo.

   Achado 02/09/2026 (projeto irmão Sirius32, comparando código compilado
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
    /* operando com palavra alta de verdade: não é 16x16->32, vai pro
       caminho geral de 32 bits (BUG-11 da Sirius32). */
    if (is_wide(b, rhs->lhs) || is_wide(b, rhs->rhs)) return -1;

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

    /* divisor com palavra alta de verdade: caminho geral (que dá erro de
       compilação em vez de dividir só pela palavra baixa). */
    if (is_wide(b, e->rhs)) return -1;

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

/* Narrow counterpart to try_gen_shr32_sym()/try_gen_div32_sym() above,
   going the other direction: recognizes the "compose a 32-bit value from
   two 16-bit halves" idiom, `dst32 = ((uint32_t)hi << 16) | lo;` (or with
   the OR operands swapped), and lowers it to IR_COMPOSE32_STORE_SYM
   instead of letting it fall into the generic 16-bit-only IR_BINOP path
   (found 09/09/2026, promoting `research/interpolacao_motor/
   3b51c_motor_divisao_peso_interpolacao.c` in the sibling Sirius32
   project - see IR_COMPOSE32_STORE_SYM in ir.h for the full symptom).
   Only matches a direct assignment to an already-declared 32-bit
   variable/global, same restriction as try_gen_widening_mul_store_sym().
   Returns -1 if the pattern doesn't match (caller falls through to the
   normal, still-buggy-for-this-shape path). */
static int match_hi_shl16(Expr *e, Expr **hi_out) {
    if (e->kind != EXPR_BINARY || e->op != OP_SHL) return 0;
    if (e->rhs->kind != EXPR_INT_LIT || e->rhs->ival != 16) return 0;
    *hi_out = e->lhs;
    return 1;
}

static int try_gen_compose32_store_sym(Builder *b, Symbol *dst_sym, Expr *rhs, SrcLoc loc) {
    if (!dst_sym) return -1;
    if (dst_sym->type->kind != TY_U32 && dst_sym->type->kind != TY_I32) return -1;
    if (rhs->kind != EXPR_BINARY || rhs->op != OP_OR) return -1;

    Expr *hi_expr, *lo_expr;
    if (match_hi_shl16(rhs->lhs, &hi_expr)) lo_expr = rhs->rhs;
    else if (match_hi_shl16(rhs->rhs, &hi_expr)) lo_expr = rhs->lhs;
    else return -1;
    /* metade com palavra alta de verdade (ex. `(x32 << 16) | y32`): não é a
       composição de duas metades de 16 bits, vai pro caminho geral. */
    if (is_wide(b, hi_expr) || is_wide(b, lo_expr)) return -1;

    Type *ht, *lt;
    int hv = gen_expr(b, hi_expr, &ht);
    int lv = gen_expr(b, lo_expr, &lt);

    IrInst *m = emit(b, IR_COMPOSE32_STORE_SYM);
    m->a = hv; m->b = lv;
    m->sym = dst_sym;
    m->loc = loc;
    return lv;
}

static int is_cast_to_32(Expr *x) {
    return x->kind == EXPR_CAST && (x->cast_type->kind == TY_U32 || x->cast_type->kind == TY_I32);
}

/* Sibling of try_gen_div32_sym() above for the case where the 32-bit
   dividend is an INLINE widening product (`((uint32_t)a * (uint32_t)b) /
   expr`) instead of an already-named 32-bit symbol - found 19/09/2026
   auditing the "evidence of demand" cluster in the sibling Sirius32
   project: the widening-division family (research/interpolacao_motor,
   0x3BA56/0x3BAF2/0x3BB4C, and the 14-occurrence
   3b536_rotina_normalizacao_divisao_32bit.c) writes the product straight
   into the division instead of assigning it to a 32-bit temp first, so it
   never matched try_gen_div32_sym() (whose LHS must already be
   EXPR_IDENT) and silently divided only the low word with plain DIV/DIVU.
   Recognizes `((T)a * (T)b) / expr` or `% expr` where at least one
   multiply operand carries an explicit cast to a 32-bit type (same
   "explicit cast signals widening intent" convention the examples in the
   4 narrow fixes already use - there is no 32-bit assignment target here
   to infer it from the way try_gen_widening_mul_store_sym() does).
   Lowers to IR_DIV32_MUL: codegen does MULU/MUL a,b (product lands in
   MDL:MDH for free, no store/reload needed) then DIVLU/DIVL directly -
   see the case in codegen.c. Returns -1 if the pattern doesn't match
   (falls through to the old, still-buggy-for-this-case path). */
static int try_gen_div32_mul(Builder *b, Expr *e, Type **out_type) {
    if (e->kind != EXPR_BINARY || (e->op != OP_DIV && e->op != OP_MOD)) return -1;
    Expr *mul = e->lhs;
    if (mul->kind != EXPR_BINARY || mul->op != OP_MUL) return -1;
    if (!is_cast_to_32(mul->lhs) && !is_cast_to_32(mul->rhs)) return -1;
    if (is_wide(b, mul->lhs) || is_wide(b, mul->rhs) || is_wide(b, e->rhs)) return -1;

    Type *lt, *rt, *dt;
    int lv = gen_expr(b, mul->lhs, &lt);
    int rv = gen_expr(b, mul->rhs, &rt);
    int dv = gen_expr(b, e->rhs, &dt);

    int is_signed = is_cast_to_32(mul->lhs) ? type_is_signed(mul->lhs->cast_type)
                                             : type_is_signed(mul->rhs->cast_type);

    IrInst *i = emit(b, IR_DIV32_MUL);
    i->dst = new_vreg(b);
    i->a = lv; i->b = rv;
    i->args = xalloc(sizeof(int));
    i->args[0] = dv;
    i->nargs = 1;
    i->op = e->op;
    i->is_signed = is_signed;
    i->loc = e->loc;
    *out_type = u16_type();
    return i->dst;
}

/* Base de uma indexação `base[i]`: devolve o vreg com o ENDEREÇO base e,
   em `*elem_type`, o tipo do elemento (que define a escala do índice e o
   tamanho do acesso).

   BUG-1 (achado 21/09/2026 na Sirius32, corrigido 24/09/2026): indexar um
   identificador declarado ESCALAR inteiro (`@ram(0x1356) volatile uint8_t
   calib_1356;` seguido de `calib_1356[i]`) caía em `gen_expr(base)`, que
   CARREGAVA O VALOR do escalar e o usava como ponteiro, e - sem `pointee`
   - assumia elemento uint16_t (índice *2, MOV word). O firmware
   reimplementado usa esse idioma pra tabelas @ram de bytes declaradas como
   escalar, então não vira erro: o escalar é tratado como base de uma
   tabela do PRÓPRIO tipo, como se fosse `(&x)[i]` - endereço do símbolo,
   índice escalado por sizeof(tipo) (uint8_t: *1 + MOVB; uint16_t: *2 +
   MOV), exatamente igual a declarar `x[N]`. */
static int gen_index_base(Builder *b, Expr *base_e, Type **elem_type) {
    if (base_e->kind == EXPR_IDENT) {
        Symbol *sym = scope_lookup(b->scope, base_e->name);
        if (sym && !sym->type->is_array && sym->type->kind >= TY_I8 && sym->type->kind <= TY_U32) {
            base_e->sym = sym;
            IrInst *i = emit(b, IR_LOAD_ADDR);
            i->dst = new_vreg(b); i->sym = sym; i->loc = base_e->loc;
            *elem_type = sym->type;
            return i->dst;
        }
    }
    Type *basety;
    int base = gen_expr(b, base_e, &basety);
    *elem_type = basety->pointee ? basety->pointee : u16_type();
    return base;
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
        Type *elemty;
        int base = gen_index_base(b, e->base, &elemty);
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

/* ======================================================================
   Valores de 32 bits como PAR de palavras de 16 bits
   (BUG-6/7/8/10/11/15 da Sirius32, 01/10/2026)

   O IR só tem vregs de 16 bits. Até aqui, 32 bits só funcionavam em 5
   formas exatas (IR_MUL32_STORE_SYM, IR_SHR32_SYM, IR_DIV32_SYM,
   IR_COMPOSE32_STORE_SYM, IR_DIV32_MUL); qualquer outra expressão
   uint32_t/int32_t caía no caminho de 16 bits e perdia a palavra alta em
   silêncio. Em vez de mais uma forma estreita por bug, toda expressão cujo
   tipo ESTÁTICO (expr_type) é de 32 bits é agora avaliada por gen_expr32(),
   que devolve um `Val32`: duas palavras, cada uma um vreg ou uma constante
   conhecida. As operações de 32 bits viram operações de palavra:
     +, -        IR_CARRYOP (ADD/SUB na baixa, ADDC/SUBC na alta)
     *           IR_MULW + IR_MDH (e produtos cruzados se um operando tem
                 palavra alta de verdade)
     / %         IR_DIV32 (MDH:MDL + DIVLU/DIVL), divisor de 16 bits
     & | ^ ~     palavra por palavra
     << >>       contagem constante, com SHL/SHR/OR de palavra
     < > <= >= == !=   IR_CMP32
     constante   as duas palavras (BUG-8)
     ?:          dois vregs de destino (BUG-10)
     carga/gravação  as duas palavras do símbolo/da memória
   Palavra constante conhecida (em geral 0, da extensão de um uint16_t)
   some do código gerado: `((uint32_t)hi << 16) | lo` não emite nenhuma
   operação, só dá nome às duas palavras (BUG-15: usado direto como
   dividendo, vira MDH:MDL + DIVLU).

   As 5 formas antigas continuam sendo tentadas primeiro onde já davam o
   resultado certo, pra não mudar o assembly do que já funcionava.

   NÃO coberto (continua como era): parâmetro, argumento e retorno de função
   de 32 bits só carregam a palavra baixa (ABI de 1 registrador por valor);
   deslocamento de 32 bits por contagem não constante e divisor de mais de
   16 bits são ERRO de compilação (antes: resultado errado em silêncio).
   ====================================================================== */
typedef struct { int v; int is_const; long c; } Word;
typedef struct {
    Word lo, hi;
    int sext; /* a palavra alta é a extensão de sinal da baixa (int16_t alargado) */
} Val32;

static Word w_reg(int v) { Word w; w.v = v; w.is_const = 0; w.c = 0; return w; }
static Word w_k(long c) { Word w; w.v = -1; w.is_const = 1; w.c = c & 0xFFFF; return w; }
static int w_is(Word w, long c) { return w.is_const && w.c == c; }

static int w_vreg(Builder *b, Word w, SrcLoc loc) {
    if (!w.is_const) return w.v;
    IrInst *c = emit(b, IR_CONST);
    c->dst = new_vreg(b); c->imm = w.c; c->size = 2; c->loc = loc;
    return c->dst;
}

static Val32 v32_const(long c) {
    Val32 r;
    r.lo = w_k(c); r.hi = w_k(c >> 16);
    r.sext = (r.hi.c == ((r.lo.c & 0x8000) ? 0xFFFF : 0));
    return r;
}
static int v32_is_const(Val32 v) { return v.lo.is_const && v.hi.is_const; }
static long v32_cval(Val32 v) { return (v.hi.c << 16) | v.lo.c; }

static void err32(SrcLoc loc, const char *msg) {
    fprintf(stderr, "%s:%d: error: %s\n", loc.file ? loc.file : "?", loc.line, msg);
    exit(1);
}

static int ty_is32(const Type *t) {
    return t && !t->is_array && (t->kind == TY_U32 || t->kind == TY_I32);
}

static int op_is_cmp(OpKind op) {
    return op == OP_EQ || op == OP_NE || op == OP_LT || op == OP_GT || op == OP_LE || op == OP_GE;
}

/* Tipo estático de uma expressão, SEM emitir IR (o resto do builder só
   descobre o tipo ao gerar o código, via out_type de gen_expr). Só precisa
   ser exato no que decide "isto é de 32 bits?" e no sinal. */
static Type *lvalue_type(Builder *b, Expr *e) {
    switch (e->kind) {
        case EXPR_IDENT: {
            Symbol *sym = scope_lookup(b->scope, e->name);
            return sym ? sym->type : u16_type();
        }
        case EXPR_DEREF: {
            Type *pt = expr_type(b, e->rhs);
            return pt->pointee ? pt->pointee : u16_type();
        }
        case EXPR_INDEX: {
            if (e->base->kind == EXPR_IDENT) {
                Symbol *sym = scope_lookup(b->scope, e->base->name);
                if (sym && !sym->type->is_array && sym->type->kind >= TY_I8 && sym->type->kind <= TY_U32)
                    return sym->type;
            }
            Type *bt = expr_type(b, e->base);
            return bt->pointee ? bt->pointee : u16_type();
        }
        case EXPR_MEMBER: {
            Type *bt = lvalue_type(b, e->base);
            if (bt->kind != TY_STRUCT || bt->is_array) return u16_type();
            const StructField *f = struct_def_find_field(bt->struct_def, e->name);
            return f ? f->type : u16_type();
        }
        default:
            return expr_type(b, e);
    }
}

static Type *expr_type(Builder *b, Expr *e) {
    switch (e->kind) {
        case EXPR_INT_LIT:
            if (e->ival > 0x7FFFFFFFL) return type_new(TY_U32);
            if (e->ival > 0xFFFF || e->ival < -0x8000L) return type_new(TY_I32);
            return u16_type();
        case EXPR_IDENT: case EXPR_MEMBER: case EXPR_INDEX: case EXPR_DEREF: {
            Type *t = lvalue_type(b, e);
            if (t->is_array) return type_new_ptr(t->pointee);
            if (t->kind == TY_FUNC) return type_new_ptr(t);
            return t;
        }
        case EXPR_CAST: return e->cast_type;
        case EXPR_ADDR: return type_new_ptr(lvalue_type(b, e->rhs));
        case EXPR_UNARY: return e->op == OP_NOT ? u16_type() : expr_type(b, e->rhs);
        case EXPR_BINARY: {
            if (e->op == OP_LAND || e->op == OP_LOR || op_is_cmp(e->op)) return u16_type();
            Type *lt = expr_type(b, e->lhs);
            if (e->op == OP_SHL || e->op == OP_SHR) return lt;
            Type *rt = expr_type(b, e->rhs);
            if (lt->kind == TY_PTR && rt->kind == TY_PTR) return e->op == OP_SUB ? u16_type() : lt;
            if (lt->kind == TY_PTR) return lt;
            if (rt->kind == TY_PTR) return rt;
            if (ty_is32(lt) && ty_is32(rt)) return (lt->kind == TY_U32) ? lt : rt;
            return type_bytes(lt) >= type_bytes(rt) ? lt : rt;
        }
        case EXPR_ASSIGN:
        case EXPR_POSTINC: case EXPR_POSTDEC: case EXPR_PREINC: case EXPR_PREDEC:
            return lvalue_type(b, e->lhs);
        case EXPR_TERNARY: {
            Type *tt = expr_type(b, e->then_e), *et = expr_type(b, e->else_e);
            if (ty_is32(tt) && ty_is32(et)) return (tt->kind == TY_U32) ? tt : et;
            if (ty_is32(et) && !ty_is32(tt)) return et;
            return tt;
        }
        case EXPR_CALL: {
            Expr *callee = e->callee;
            if (callee->kind == EXPR_IDENT && e->nargs == 2) {
                if (strcmp(callee->name, "c167cc_far_read16") == 0) return u16_type();
                if (strcmp(callee->name, "c167cc_far_read8") == 0) return u8_type();
            }
            while (callee->kind == EXPR_DEREF) callee = callee->rhs;
            Symbol *fsym = (callee->kind == EXPR_IDENT) ? scope_lookup(b->scope, callee->name) : NULL;
            if (fsym && fsym->kind == SYM_FUNC) return fsym->func ? fsym->func->ret_type : u16_type();
            Type *ct = expr_type(b, callee);
            if (ct->kind == TY_PTR && ct->pointee && ct->pointee->kind == TY_FUNC && ct->pointee->func_ret)
                return ct->pointee->func_ret;
            return u16_type();
        }
        default:
            return u16_type();
    }
}

/* Constante inteira conhecida em tempo de compilação (literal, -literal,
   ~literal, cast de constante). */
static int const_eval(Expr *e, long *out) {
    long v;
    switch (e->kind) {
        case EXPR_INT_LIT: *out = e->ival; return 1;
        case EXPR_UNARY:
            if ((e->op != OP_NEG && e->op != OP_BNOT) || !const_eval(e->rhs, &v)) return 0;
            /* valor matemático exato (o literal é tratado como inteiro com
               sinal de 32 bits, como no GCC usado pra referência na
               Sirius32): -32768 e -0x8000 são 0xFFFF8000 em 32 bits. */
            *out = (e->op == OP_NEG) ? -v : ~v;
            return 1;
        case EXPR_CAST:
            if (e->cast_type->is_array || !const_eval(e->rhs, &v)) return 0;
            switch (e->cast_type->kind) {
                case TY_U32: v &= 0xFFFFFFFFL; break;
                case TY_I32: v &= 0xFFFFFFFFL; if (v & 0x80000000L) v -= 0x100000000L; break;
                case TY_U16: v &= 0xFFFFL; break;
                case TY_I16: v &= 0xFFFFL; if (v & 0x8000L) v -= 0x10000L; break;
                case TY_U8: v &= 0xFFL; break;
                case TY_I8: v &= 0xFFL; if (v & 0x80L) v -= 0x100L; break;
                default: return 0;
            }
            *out = v;
            return 1;
        default:
            return 0;
    }
}

/* "Largo de verdade": a expressão é de 32 bits e a palavra alta pode não
   ser só a extensão de um valor de 16 bits. `(uint32_t)x16` e constante
   pequena NÃO são largos - as formas estreitas antigas (que só olham a
   palavra baixa dos operandos) continuam certas pra eles. */
static int is_wide(Builder *b, Expr *e) {
    long c;
    if (const_eval(e, &c)) return c > 0xFFFFL || c < -0x8000L;
    if (!ty_is32(expr_type(b, e))) return 0;
    if (e->kind == EXPR_CAST) return is_wide(b, e->rhs);
    return 1;
}

static Word w_sext(Builder *b, Word w, SrcLoc loc) {
    if (w.is_const) return w_k((w.c & 0x8000) ? 0xFFFF : 0);
    IrInst *s = emit(b, IR_SEXT16);
    s->dst = new_vreg(b); s->a = w.v; s->size = 2; s->loc = loc;
    return w_reg(s->dst);
}

/* Alarga um valor de 16 bits (1 vreg) pra 32: extensão de sinal se o tipo
   de origem tem sinal, zero se não tem. */
static Val32 widen16(Builder *b, int v, Type *t, SrcLoc loc) {
    Val32 r;
    r.lo = w_reg(v);
    if (type_is_signed(t)) { r.hi = w_sext(b, r.lo, loc); r.sext = 1; }
    else { r.hi = w_k(0); r.sext = 0; }
    return r;
}

/* Operação de PALAVRA (16 bits), com dobra de constante (mascarada em 16
   bits) e identidades com 0/0xFFFF - é isso que faz a palavra alta zero de
   um uint16_t alargado não gerar código. Deslocamento: contagem constante
   em [0,15] (o SHL/SHR do C166 só usa 4 bits da contagem). */
static Word w_binop(Builder *b, OpKind op, Word x, Word y, int is_signed, SrcLoc loc) {
    if (x.is_const && y.is_const) {
        long a = x.c, c = y.c, r = 0;
        switch (op) {
            case OP_ADD: r = a + c; break;
            case OP_SUB: r = a - c; break;
            case OP_MUL: r = a * c; break;
            case OP_AND: r = a & c; break;
            case OP_OR:  r = a | c; break;
            case OP_XOR: r = a ^ c; break;
            case OP_SHL: r = a << c; break;
            case OP_SHR:
                if (is_signed && (a & 0x8000)) a -= 0x10000L;
                r = a >> c; break;
            case OP_DIV: r = c ? a / c : 0; break;
            case OP_MOD: r = c ? a % c : 0; break;
            default: break;
        }
        return w_k(r);
    }
    switch (op) {
        case OP_ADD: case OP_OR: case OP_XOR:
            if (w_is(x, 0)) return y;
            if (w_is(y, 0)) return x;
            break;
        case OP_SUB:
            if (w_is(y, 0)) return x;
            break;
        case OP_AND:
            if (w_is(x, 0) || w_is(y, 0)) return w_k(0);
            if (w_is(x, 0xFFFF)) return y;
            if (w_is(y, 0xFFFF)) return x;
            break;
        case OP_MUL:
            if (w_is(x, 0) || w_is(y, 0)) return w_k(0);
            if (w_is(x, 1)) return y;
            if (w_is(y, 1)) return x;
            break;
        case OP_SHL: case OP_SHR:
            if (w_is(y, 0)) return x;
            if (w_is(x, 0)) return x;
            break;
        default: break;
    }
    int xv = w_vreg(b, x, loc), yv = w_vreg(b, y, loc);
    IrInst *i = emit(b, IR_BINOP);
    i->dst = new_vreg(b); i->op = op; i->a = xv; i->b = yv;
    i->size = 2; i->is_signed = is_signed; i->loc = loc;
    return w_reg(i->dst);
}

static Val32 v32_addsub(Builder *b, OpKind op, Val32 x, Val32 y, SrcLoc loc) {
    Val32 r;
    if (v32_is_const(x) && v32_is_const(y))
        return v32_const(op == OP_ADD ? v32_cval(x) + v32_cval(y) : v32_cval(x) - v32_cval(y));
    r.sext = 0;
    if (w_is(y.lo, 0) || (op == OP_ADD && w_is(x.lo, 0))) {
        /* uma das palavras baixas é 0: não há vai-um, a alta é uma operação
           de 16 bits comum. */
        r.lo = w_binop(b, op, x.lo, y.lo, 0, loc);
        r.hi = w_binop(b, op, x.hi, y.hi, 0, loc);
        return r;
    }
    /* os 4 operandos são materializados ANTES do par ADD/ADDC: nada pode
       ficar entre as duas instruções além de MOV (ver IR_CARRYOP). */
    int xl = w_vreg(b, x.lo, loc), yl = w_vreg(b, y.lo, loc);
    int xh = w_vreg(b, x.hi, loc), yh = w_vreg(b, y.hi, loc);
    IrInst *lo = emit(b, IR_CARRYOP);
    lo->dst = new_vreg(b); lo->op = op; lo->a = xl; lo->b = yl; lo->imm = 0; lo->size = 2; lo->loc = loc;
    IrInst *hi = emit(b, IR_CARRYOP);
    hi->dst = new_vreg(b); hi->op = op; hi->a = xh; hi->b = yh; hi->imm = 1; hi->size = 2; hi->loc = loc;
    r.lo = w_reg(lo->dst); r.hi = w_reg(hi->dst);
    return r;
}

static Val32 v32_bitop(Builder *b, OpKind op, Val32 x, Val32 y, SrcLoc loc) {
    Val32 r;
    r.lo = w_binop(b, op, x.lo, y.lo, 0, loc);
    r.hi = w_binop(b, op, x.hi, y.hi, 0, loc);
    r.sext = 0;
    return r;
}

static Val32 v32_mul(Builder *b, Val32 x, Val32 y, SrcLoc loc) {
    Val32 r;
    if (v32_is_const(x) && v32_is_const(y))
        return v32_const((long)(((unsigned long)v32_cval(x) * (unsigned long)v32_cval(y)) & 0xFFFFFFFFUL));
    r.sext = 0;
    int zx = w_is(x.hi, 0), zy = w_is(y.hi, 0);
    int xl = w_vreg(b, x.lo, loc), yl = w_vreg(b, y.lo, loc);
    IrInst *m = emit(b, IR_MULW);
    m->dst = new_vreg(b); m->a = xl; m->b = yl; m->size = 2; m->loc = loc;
    /* 16x16->32 direto quando os dois operandos são valores de 16 bits
       alargados: MULU se os dois foram estendidos com zero, MUL se os dois
       foram estendidos com sinal. */
    m->is_signed = (!(zx && zy) && x.sext && y.sext);
    IrInst *h = emit(b, IR_MDH);
    h->dst = new_vreg(b); h->size = 2; h->loc = loc;
    r.lo = w_reg(m->dst); r.hi = w_reg(h->dst);
    if ((zx && zy) || m->is_signed) return r;
    /* caso geral (módulo 2^32): hi += lo(x.lo * y.hi) + lo(x.hi * y.lo) */
    Word c1 = w_binop(b, OP_MUL, w_reg(xl), y.hi, 0, loc);
    Word c2 = w_binop(b, OP_MUL, x.hi, w_reg(yl), 0, loc);
    r.hi = w_binop(b, OP_ADD, r.hi, c1, 0, loc);
    r.hi = w_binop(b, OP_ADD, r.hi, c2, 0, loc);
    return r;
}

/* Deslocamento de palavra por contagem constante k em [0,15]. */
static Word w_shift(Builder *b, OpKind op, Word w, long k, int is_signed, SrcLoc loc) {
    return w_binop(b, op, w, w_k(k), is_signed, loc);
}

static Val32 v32_shift(Builder *b, OpKind op, Val32 x, long n, int is_signed, SrcLoc loc) {
    Val32 r;
    r.sext = 0;
    if (n < 0 || n > 31) err32(loc, "32-bit shift count out of range (0..31)");
    if (n == 0) return x;
    if (op == OP_SHL) {
        if (n >= 16) {
            r.hi = w_shift(b, OP_SHL, x.lo, n - 16, 0, loc);
            r.lo = w_k(0);
        } else {
            Word carry = w_shift(b, OP_SHR, x.lo, 16 - n, 0, loc);
            r.hi = w_binop(b, OP_OR, w_shift(b, OP_SHL, x.hi, n, 0, loc), carry, 0, loc);
            r.lo = w_shift(b, OP_SHL, x.lo, n, 0, loc);
        }
    } else {
        if (n >= 16) {
            r.lo = w_shift(b, OP_SHR, x.hi, n - 16, is_signed, loc);
            r.hi = is_signed ? w_sext(b, x.hi, loc) : w_k(0);
        } else {
            Word carry = w_shift(b, OP_SHL, x.hi, 16 - n, 0, loc);
            r.lo = w_binop(b, OP_OR, w_shift(b, OP_SHR, x.lo, n, 0, loc), carry, 0, loc);
            r.hi = w_shift(b, OP_SHR, x.hi, n, is_signed, loc);
        }
    }
    return r;
}

/* n / d ou n % d com dividendo de 32 bits. O C167 só divide 32 por 16
   (DIVLU/DIVL): o divisor tem que caber em 16 bits e o quociente sai com 16
   bits (palavra alta do resultado = 0, ou extensão de sinal em int32_t) -
   BUG-7. Estouro do quociente não é detectado (como no hardware: flag V). */
static Val32 v32_div(Builder *b, OpKind op, Val32 n, Val32 d, int is_signed, SrcLoc loc) {
    Val32 r;
    if (!(w_is(d.hi, 0) || (is_signed && d.sext)))
        err32(loc, "division by a 32-bit divisor is not supported (the C167 only divides 32 by 16 bits; "
                   "cast the divisor to uint16_t/int16_t if it fits)");
    if (!is_signed && w_is(n.hi, 0)) {
        r.lo = w_binop(b, op, n.lo, d.lo, 0, loc);
        r.hi = w_k(0); r.sext = 0;
        return r;
    }
    int dv = w_vreg(b, d.lo, loc);
    int nl = w_vreg(b, n.lo, loc), nh = w_vreg(b, n.hi, loc);
    IrInst *i = emit(b, IR_DIV32);
    i->dst = new_vreg(b); i->a = nl; i->b = nh;
    i->args = xalloc(sizeof(int)); i->args[0] = dv; i->nargs = 1;
    i->op = op; i->is_signed = is_signed; i->size = 2; i->loc = loc;
    r.lo = w_reg(i->dst);
    if (is_signed) { r.hi = w_sext(b, r.lo, loc); r.sext = 1; }
    else { r.hi = w_k(0); r.sext = 0; }
    return r;
}

static Val32 gen_expr32(Builder *b, Expr *e);

/* x op <rhs_e>, com x já avaliado. Deslocamento exige contagem constante. */
static Val32 v32_apply(Builder *b, OpKind op, Val32 x, Expr *rhs_e, int is_signed, SrcLoc loc) {
    if (op == OP_SHL || op == OP_SHR) {
        long n;
        if (!const_eval(rhs_e, &n))
            err32(loc, "32-bit shift by a non-constant count is not supported");
        return v32_shift(b, op, x, n, is_signed, loc);
    }
    Val32 y = gen_expr32(b, rhs_e);
    switch (op) {
        case OP_ADD: case OP_SUB: return v32_addsub(b, op, x, y, loc);
        case OP_AND: case OP_OR: case OP_XOR: return v32_bitop(b, op, x, y, loc);
        case OP_MUL: return v32_mul(b, x, y, loc);
        case OP_DIV: case OP_MOD: return v32_div(b, op, x, y, is_signed, loc);
        default: break;
    }
    err32(loc, "internal error: unsupported 32-bit operator");
    return x;
}

/* Carga das duas palavras de um lvalue de 32 bits. */
static Val32 load32(Builder *b, Expr *e) {
    Val32 r;
    r.sext = 0;
    if (e->kind == EXPR_IDENT) {
        Symbol *sym = scope_lookup(b->scope, e->name);
        if (!sym) { fprintf(stderr, "%s:%d: error: undeclared identifier '%s'\n", e->loc.file, e->loc.line, e->name); exit(1); }
        e->sym = sym;
        for (int k = 0; k < 2; k++) {
            IrInst *i = emit(b, IR_LOAD_SYM);
            i->dst = new_vreg(b); i->sym = sym; i->imm = k * 2;
            i->size = k ? 2 : type_bytes(sym->type);
            i->is_signed = type_is_signed(sym->type); i->loc = e->loc;
            if (k) r.hi = w_reg(i->dst); else r.lo = w_reg(i->dst);
        }
        return r;
    }
    Type *elemty;
    int addr = gen_lvalue_addr(b, e, &elemty);
    IrInst *lo = emit(b, IR_LOAD_MEM);
    lo->dst = new_vreg(b); lo->a = addr; lo->size = 2; lo->loc = e->loc;
    int addr2 = add_const_offset(b, addr, 2, e->loc);
    IrInst *hi = emit(b, IR_LOAD_MEM);
    hi->dst = new_vreg(b); hi->a = addr2; hi->size = 2; hi->loc = e->loc;
    r.lo = w_reg(lo->dst); r.hi = w_reg(hi->dst);
    return r;
}

static void store32_sym(Builder *b, Symbol *sym, Val32 v, SrcLoc loc) {
    int lo = w_vreg(b, v.lo, loc), hi = w_vreg(b, v.hi, loc);
    IrInst *s0 = emit(b, IR_STORE_SYM);
    s0->sym = sym; s0->a = lo; s0->size = type_bytes(sym->type); s0->loc = loc;
    IrInst *s1 = emit(b, IR_STORE_SYM);
    s1->sym = sym; s1->a = hi; s1->imm = 2; s1->size = 2; s1->loc = loc;
}

/* Gravação das duas palavras num lvalue de 32 bits. */
static void store32(Builder *b, Expr *lhs, Val32 v) {
    if (lhs->kind == EXPR_IDENT) {
        Symbol *sym = scope_lookup(b->scope, lhs->name);
        if (!sym) { fprintf(stderr, "%s:%d: error: undeclared identifier '%s'\n", lhs->loc.file, lhs->loc.line, lhs->name); exit(1); }
        lhs->sym = sym;
        store32_sym(b, sym, v, lhs->loc);
        return;
    }
    int lo = w_vreg(b, v.lo, lhs->loc), hi = w_vreg(b, v.hi, lhs->loc);
    Type *elemty;
    int addr = gen_lvalue_addr(b, lhs, &elemty);
    IrInst *s0 = emit(b, IR_STORE_MEM);
    s0->a = addr; s0->b = lo; s0->size = 2; s0->loc = lhs->loc;
    int addr2 = add_const_offset(b, addr, 2, lhs->loc);
    IrInst *s1 = emit(b, IR_STORE_MEM);
    s1->a = addr2; s1->b = hi; s1->size = 2; s1->loc = lhs->loc;
}

static int gen_cond(Builder *b, Expr *e);

static Val32 gen_expr32_inner(Builder *b, Expr *e) {
    long cv;
    if (const_eval(e, &cv)) return v32_const(cv); /* BUG-8: as duas palavras */

    Type *t = expr_type(b, e);
    if (ty_is32(t)) {
        int is_signed = type_is_signed(t);
        switch (e->kind) {
            case EXPR_IDENT: case EXPR_MEMBER: case EXPR_INDEX: case EXPR_DEREF:
                return load32(b, e);
            case EXPR_CAST: {
                if (ty_is32(expr_type(b, e->rhs))) return gen_expr32(b, e->rhs);
                Type *st;
                int v = gen_expr(b, e->rhs, &st);
                return widen16(b, v, st, e->loc);
            }
            case EXPR_UNARY: {
                Val32 x = gen_expr32(b, e->rhs);
                if (e->op == OP_NEG) return v32_addsub(b, OP_SUB, v32_const(0), x, e->loc);
                if (e->op == OP_BNOT) {
                    Val32 r; r.sext = 0;
                    Word *src[2] = { &x.lo, &x.hi }, *dst[2] = { &r.lo, &r.hi };
                    for (int k = 0; k < 2; k++) {
                        if (src[k]->is_const) { *dst[k] = w_k(~src[k]->c); continue; }
                        IrInst *u = emit(b, IR_UNOP);
                        u->dst = new_vreg(b); u->op = OP_BNOT; u->a = src[k]->v; u->size = 2; u->loc = e->loc;
                        *dst[k] = w_reg(u->dst);
                    }
                    return r;
                }
                break;
            }
            case EXPR_BINARY: {
                if (e->op == OP_LAND || e->op == OP_LOR || op_is_cmp(e->op)) break;
                Type *ot;
                /* As formas antigas `sym32 >> N` e `sym32 / x`, `(a*b) / x`
                   já calculam a palavra baixa certa: reaproveita (mesmo
                   assembly de antes) e só acrescenta a palavra alta. */
                if (e->op == OP_SHR && e->rhs->kind == EXPR_INT_LIT && e->rhs->ival >= 1 && e->rhs->ival <= 31) {
                    int lo = try_gen_shr32_sym(b, e, &ot);
                    if (lo >= 0) {
                        Val32 r; r.sext = 0;
                        long n = e->rhs->ival;
                        r.lo = w_reg(lo);
                        if (n >= 16 && !is_signed) { r.hi = w_k(0); return r; }
                        IrInst *h = emit(b, IR_LOAD_SYM);
                        h->dst = new_vreg(b); h->sym = e->lhs->sym; h->imm = 2; h->size = 2; h->loc = e->loc;
                        if (n >= 16) r.hi = w_sext(b, w_reg(h->dst), e->loc);
                        else r.hi = w_shift(b, OP_SHR, w_reg(h->dst), n, is_signed, e->loc);
                        return r;
                    }
                }
                if (e->op == OP_DIV || e->op == OP_MOD) {
                    int q = try_gen_div32_sym(b, e, &ot);
                    if (q < 0) q = try_gen_div32_mul(b, e, &ot);
                    if (q >= 0) {
                        /* BUG-7: o quociente/resto do DIVLU tem 16 bits; a
                           palavra alta do resultado de 32 bits é 0. */
                        Val32 r;
                        r.lo = w_reg(q);
                        if (is_signed) { r.hi = w_sext(b, r.lo, e->loc); r.sext = 1; }
                        else { r.hi = w_k(0); r.sext = 0; }
                        return r;
                    }
                }
                Val32 x = gen_expr32(b, e->lhs);
                return v32_apply(b, e->op, x, e->rhs, is_signed, e->loc);
            }
            case EXPR_ASSIGN: {
                Val32 v;
                if (e->op == OP_ASSIGN) {
                    v = gen_expr32(b, e->rhs);
                } else {
                    Val32 x = load32(b, e->lhs);
                    v = v32_apply(b, e->op, x, e->rhs, is_signed, e->loc);
                }
                store32(b, e->lhs, v);
                return v;
            }
            case EXPR_POSTINC: case EXPR_POSTDEC: case EXPR_PREINC: case EXPR_PREDEC: {
                Val32 x = load32(b, e->lhs);
                int inc = (e->kind == EXPR_POSTINC || e->kind == EXPR_PREINC);
                Val32 v = v32_addsub(b, inc ? OP_ADD : OP_SUB, x, v32_const(1), e->loc);
                store32(b, e->lhs, v);
                return (e->kind == EXPR_POSTINC || e->kind == EXPR_POSTDEC) ? x : v;
            }
            case EXPR_TERNARY: {
                /* BUG-10: dois vregs de destino, um por palavra. */
                char *l_true = fmt_label(b, "tern_true");
                char *l_false = fmt_label(b, "tern_false");
                char *l_end = fmt_label(b, "tern_end");
                int cv2 = gen_cond(b, e->cond);
                IrInst *cj = emit(b, IR_CJMP); cj->a = cv2; cj->true_label = strdup(l_true); cj->false_label = strdup(l_false);
                int dlo = new_vreg(b), dhi = new_vreg(b);
                Expr *arms[2] = { e->then_e, e->else_e };
                for (int k = 0; k < 2; k++) {
                    emit(b, IR_LABEL)->label = strdup(k ? l_false : l_true);
                    Val32 v = gen_expr32(b, arms[k]);
                    int lo = w_vreg(b, v.lo, e->loc), hi = w_vreg(b, v.hi, e->loc);
                    IrInst *m1 = emit(b, IR_MOV); m1->dst = dlo; m1->a = lo; m1->size = 2;
                    IrInst *m2 = emit(b, IR_MOV); m2->dst = dhi; m2->a = hi; m2->size = 2;
                    if (k == 0) emit(b, IR_JMP)->label = strdup(l_end);
                }
                emit(b, IR_LABEL)->label = strdup(l_end);
                Val32 r; r.lo = w_reg(dlo); r.hi = w_reg(dhi); r.sext = 0;
                return r;
            }
            default:
                break;
        }
    }
    /* valor de 16 bits (ou chamada de função: a ABI só devolve a palavra
       baixa): 1 vreg, alargado conforme o sinal do tipo. */
    Type *ot;
    int v = gen_expr(b, e, &ot);
    return widen16(b, v, ot, e->loc);
}

static Val32 gen_expr32(Builder *b, Expr *e) {
    b->wide32++;
    Val32 r = gen_expr32_inner(b, e);
    b->wide32--;
    return r;
}

/* Comparação em que pelo menos um operando é de 32 bits (BUG-11). */
static int gen_cmp32(Builder *b, Expr *e) {
    b->wide32++;
    Type *lt = expr_type(b, e->lhs), *rt = expr_type(b, e->rhs);
    /* conversões aritméticas usuais do C: uint32_t ganha; senão int32_t
       (com sinal) representa qualquer operando menor. */
    int is_signed = !((ty_is32(lt) && lt->kind == TY_U32) || (ty_is32(rt) && rt->kind == TY_U32));
    Val32 x = gen_expr32(b, e->lhs);
    Val32 y = gen_expr32(b, e->rhs);
    OpKind op = e->op;
    if (op == OP_GT || op == OP_LE) { Val32 t = x; x = y; y = t; op = (op == OP_GT) ? OP_LT : OP_GE; }
    int dst;
    if (w_is(x.hi, 0) && w_is(y.hi, 0)) {
        /* as duas palavras altas são 0: comparação de 16 bits sem sinal */
        int xv = w_vreg(b, x.lo, e->loc), yv = w_vreg(b, y.lo, e->loc);
        IrInst *i = emit(b, IR_BINOP);
        i->dst = new_vreg(b); i->op = op; i->a = xv; i->b = yv; i->size = 2; i->is_signed = 0; i->loc = e->loc;
        dst = i->dst;
    } else {
        int xl = w_vreg(b, x.lo, e->loc), yl = w_vreg(b, y.lo, e->loc);
        int xh = w_vreg(b, x.hi, e->loc), yh = w_vreg(b, y.hi, e->loc);
        IrInst *i = emit(b, IR_CMP32);
        i->dst = new_vreg(b); i->op = op; i->a = xl; i->b = yl;
        i->args = xalloc(sizeof(int) * 2); i->args[0] = xh; i->args[1] = yh; i->nargs = 2;
        i->size = 2; i->is_signed = is_signed; i->loc = e->loc;
        dst = i->dst;
    }
    b->wide32--;
    return dst;
}

/* Valor de uma condição (if/while/for/?:/&&/||/!): 1 vreg que é != 0 se e
   só se a expressão é != 0. Em 32 bits é o OR das duas palavras - antes só
   a palavra baixa era testada. */
static int gen_cond(Builder *b, Expr *e) {
    Type *t;
    if (!ty_is32(expr_type(b, e))) return gen_expr(b, e, &t);
    b->wide32++;
    Val32 v = gen_expr32(b, e);
    int r = w_vreg(b, w_binop(b, OP_OR, v.lo, v.hi, 0, e->loc), e->loc);
    b->wide32--;
    return r;
}

static int gen_expr(Builder *b, Expr *e, Type **out_type) {
    switch (e->kind) {
        case EXPR_INT_LIT: {
            IrInst *i = emit(b, IR_CONST);
            i->dst = new_vreg(b); i->imm = e->ival; i->size = 2; i->loc = e->loc;
            *out_type = u16_type();
            if (e->ival > 0xFFFF || e->ival < -0x8000L) {
                /* BUG-8 da Sirius32: literal de 32 bits num contexto de 1
                   vreg - só a palavra baixa cabe aqui (o valor inteiro sai
                   por gen_expr32); antes ia o valor inteiro num imediato
                   de 16 bits. */
                i->imm = e->ival & 0xFFFF;
                *out_type = expr_type(b, e);
            }
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
            /* Achado 05/09/2026 (bug real de miscompilação, encontrado
               investigando uma divergência de `combinar()`/`termo_final`
               na Sirius32 - ver research/sensores_atuadores/DUVIDAS.md):
               `i->is_signed` acima é o sinal do tipo DE DESTINO, mas
               widening byte->word (`(int16_t)(int8_t)x`) precisa saber o
               sinal do tipo de ORIGEM pra decidir entre sign-extend e
               zero-extend - `(int16_t)(uint8_t)x` deve zero-estender
               mesmo com destino signed, e `(uint16_t)(int8_t)x` deve
               SIGN-estender apesar do destino ser unsigned (é isso que
               `research/sensores_atuadores/32d1e_...` faz: reinterpreta o
               byte assinado de volta pra uint16_t só pra somar depois).
               `imm` não é usado por IR_UNOP/OP_ASSIGN - reaproveita pra
               carregar o tamanho (bits baixos) e o sinal (bit 8) do tipo
               de ORIGEM até o codegen/otimizador (ver IR_UNOP/OP_ASSIGN
               em codegen.c e optimizer.c). NÃO usar `i->b` pra isso -
               achado nesta mesma sessão: `i->b`/`i->a` são resolvidos
               genericamente pela cadeia de alias do otimizador pra TODA
               instrução (são sempre "id de vreg, -1 se não usado") -
               guardar uma flag 0/1 ali faz o otimizador confundir 0/1 com
               vreg de verdade e reescrever silenciosamente. */
            i->imm = type_bytes(srcty) | (type_is_signed(srcty) ? 0x100 : 0);
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
            Type *elemty;
            int base = gen_index_base(b, e->base, &elemty);
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
            int v;
            if (e->op == OP_NOT && ty_is32(expr_type(b, e->rhs))) {
                /* !x32: testa as duas palavras */
                v = gen_cond(b, e->rhs); t = u16_type();
            } else {
                v = gen_expr(b, e->rhs, &t);
            }
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
            int div32m = try_gen_div32_mul(b, e, out_type);
            if (div32m >= 0) return div32m;
            if (e->op == OP_LAND || e->op == OP_LOR) {
                /* short-circuit evaluation */
                char *l_rhs = fmt_label(b, e->op == OP_LAND ? "and_rhs" : "or_rhs");
                char *l_true = fmt_label(b, "logic_true");
                char *l_false = fmt_label(b, "logic_false");
                char *l_end = fmt_label(b, "logic_end");
                int lv = gen_cond(b, e->lhs);
                IrInst *cj = emit(b, IR_CJMP);
                cj->a = lv;
                if (e->op == OP_LAND) { cj->true_label = strdup(l_rhs); cj->false_label = strdup(l_false); }
                else { cj->true_label = strdup(l_true); cj->false_label = strdup(l_rhs); }
                emit(b, IR_LABEL)->label = strdup(l_rhs);
                int rv = gen_cond(b, e->rhs);
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
            /* 32 bits (BUG-6/11/15 da Sirius32): comparação com operando de
               32 bits e aritmética de tipo 32 bits vão pelo par de
               palavras; aqui (contexto de 1 vreg) fica a palavra baixa. */
            if (op_is_cmp(e->op)) {
                if (ty_is32(expr_type(b, e->lhs)) || ty_is32(expr_type(b, e->rhs))) {
                    *out_type = u16_type();
                    return gen_cmp32(b, e);
                }
            } else {
                Type *st = expr_type(b, e);
                if (ty_is32(st)) {
                    Val32 v = gen_expr32(b, e);
                    *out_type = st;
                    return w_vreg(b, v.lo, e->loc);
                }
            }
            Type *lt, *rt;
            int lv = gen_expr(b, e->lhs, &lt);
            int rv = gen_expr(b, e->rhs, &rt);
            /* Pointer arithmetic scaling (found 07/09/2026, promoting
               `busca_indice_eixo_rpm` in the sibling Sirius32 project - see
               docs/limitations.md "Fixed bugs"): `ptr + int`/`int + ptr`/
               `ptr - int` must scale the integer operand by sizeof(*ptr)
               before the raw add/sub, and `ptr - ptr` must divide the raw
               byte difference by sizeof(*ptr) to yield an element count.
               `array[i]` (EXPR_INDEX, in gen_lvalue_addr above) already did
               this correctly via an explicit IR_CONST+OP_MUL; this generic
               EXPR_BINARY path - reached whenever a decayed array/pointer
               value is combined with an integer OUTSIDE of `[]` (e.g.
               `p = arr + 1;`) - treated both operands as plain integers and
               silently added/subtracted raw byte counts instead. A pointee
               of size 1 (`uint8_t*`/`char*`) is left untouched: scaling by 1
               is a no-op, so that case was never observably wrong and stays
               on the exact same code path it already used. */
            if (lt->kind == TY_PTR && rt->kind == TY_PTR && (e->op == OP_SUB)) {
                int esz = type_bytes(lt->pointee ? lt->pointee : u16_type());
                IrInst *i = emit(b, IR_BINOP);
                i->dst = new_vreg(b); i->op = OP_SUB; i->a = lv; i->b = rv;
                i->size = 2; i->loc = e->loc;
                int diff = i->dst;
                if (esz > 1) {
                    IrInst *c = emit(b, IR_CONST);
                    c->dst = new_vreg(b); c->imm = esz; c->size = 2; c->loc = e->loc;
                    IrInst *d = emit(b, IR_BINOP);
                    d->dst = new_vreg(b); d->op = OP_DIV; d->a = diff; d->b = c->dst;
                    d->size = 2; d->loc = e->loc;
                    diff = d->dst;
                }
                *out_type = u16_type();
                return diff;
            }
            if ((e->op == OP_ADD || e->op == OP_SUB) && lt->kind == TY_PTR && rt->kind != TY_PTR) {
                int esz = type_bytes(lt->pointee ? lt->pointee : u16_type());
                if (esz > 1) {
                    IrInst *c = emit(b, IR_CONST);
                    c->dst = new_vreg(b); c->imm = esz; c->size = 2; c->loc = e->loc;
                    IrInst *m = emit(b, IR_BINOP);
                    m->dst = new_vreg(b); m->op = OP_MUL; m->a = rv; m->b = c->dst;
                    m->size = 2; m->loc = e->loc;
                    rv = m->dst;
                }
            } else if (e->op == OP_ADD && rt->kind == TY_PTR && lt->kind != TY_PTR) {
                int esz = type_bytes(rt->pointee ? rt->pointee : u16_type());
                if (esz > 1) {
                    IrInst *c = emit(b, IR_CONST);
                    c->dst = new_vreg(b); c->imm = esz; c->size = 2; c->loc = e->loc;
                    IrInst *m = emit(b, IR_BINOP);
                    m->dst = new_vreg(b); m->op = OP_MUL; m->a = lv; m->b = c->dst;
                    m->size = 2; m->loc = e->loc;
                    lv = m->dst;
                }
            }
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
                    int cv = try_gen_compose32_store_sym(b, dst_sym, e->rhs, e->loc);
                    if (cv >= 0) {
                        /* `x = ((uint32_t)hi << 16) | lo;` onde x já é
                           uint32_t/int32_t - ver comentário de
                           try_gen_compose32_store_sym. */
                        e->lhs->sym = dst_sym;
                        *out_type = dst_sym->type;
                        return cv;
                    }
                }
            }
            {
                /* destino de 32 bits (BUG-11 da Sirius32): grava as duas
                   palavras; o lado direito é avaliado em 32 bits (ou
                   alargado). */
                Type *dt = lvalue_type(b, e->lhs);
                if (ty_is32(dt)) {
                    Val32 v = gen_expr32(b, e);
                    *out_type = dt;
                    return w_vreg(b, v.lo, e->loc);
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
            int cv = gen_cond(b, e->cond);
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
            /* `c167cc_far_read16(page, off)` - compiler-recognized narrow
               atomic far-indexed word read, NOT a real function call (no
               CALLS is ever emitted for this name - see IR_FARREAD16_SYM in
               ir.h for the full rationale). Checked before the general
               EXPR_CALL handling below so this exact name never reaches
               the normal direct/indirect call codegen. */
            if (e->callee->kind == EXPR_IDENT &&
                strcmp(e->callee->name, "c167cc_far_read16") == 0 &&
                e->nargs == 2) {
                Type *pt, *ot;
                int page_v = gen_expr(b, e->args[0], &pt);
                int off_v = gen_expr(b, e->args[1], &ot);
                IrInst *fr = emit(b, IR_FARREAD16_SYM);
                fr->dst = new_vreg(b);
                fr->a = page_v; fr->b = off_v; fr->loc = e->loc;
                *out_type = u16_type();
                return fr->dst;
            }
            /* `c167cc_far_read8(page, off)` - byte sibling, see
               IR_FARREAD8_SYM in ir.h. */
            if (e->callee->kind == EXPR_IDENT &&
                strcmp(e->callee->name, "c167cc_far_read8") == 0 &&
                e->nargs == 2) {
                Type *pt, *ot;
                int page_v = gen_expr(b, e->args[0], &pt);
                int off_v = gen_expr(b, e->args[1], &ot);
                IrInst *fr = emit(b, IR_FARREAD8_SYM);
                fr->dst = new_vreg(b);
                fr->a = page_v; fr->b = off_v; fr->loc = e->loc;
                *out_type = u8_type();
                return fr->dst;
            }
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
            if (ty_is32(lvalue_type(b, e->lhs))) {
                Val32 v = gen_expr32(b, e);
                *out_type = lvalue_type(b, e->lhs);
                return w_vreg(b, v.lo, e->loc);
            }
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
                } else if (try_gen_compose32_store_sym(b, sym, s->decl->init, s->loc) >= 0) {
                    /* `uint32_t x = ((uint32_t)hi << 16) | lo;` - ver
                       comentário de try_gen_compose32_store_sym acima. */
                } else if (ty_is32(sym->type)) {
                    /* `uint32_t x = <expr>;` (BUG-6/7/8/10/11 da Sirius32):
                       as duas palavras. */
                    b->wide32++;
                    Val32 v = gen_expr32(b, s->decl->init);
                    store32_sym(b, sym, v, s->loc);
                    b->wide32--;
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
            int cv = gen_cond(b, s->cond);
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
            int cv = gen_cond(b, s->cond);
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
                int cv = gen_cond(b, s->for_cond);
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
