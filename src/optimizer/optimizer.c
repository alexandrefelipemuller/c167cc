#include "c167cc/ir.h"
#include <stdlib.h>
#include <string.h>

/* Achado 05/09/2026 (mesmo bug documentado em ir_build.c/EXPR_CAST e
   codegen.c/IR_UNOP-OP_ASSIGN): um cast de byte->word ASSINADO
   (`(int16_t)(int8_t)x` e afins) muda o valor de verdade quando o byte de
   origem tem o bit 7 setado - não é uma cópia pura. `imm`/`b` num
   IR_UNOP/OP_ASSIGN carregam o tamanho e o sinal do tipo de ORIGEM (ver
   ir_build.c) - usado tanto pra decidir alias-propagation abaixo quanto
   pra fazer o constant-fold desse cast corretamente, em vez de só copiar
   o valor cru como se byte->word nunca precisasse de sign-extension. */
/* Achado 07/09/2026 (4º bug de cast desta sessão, ver
   docs/limitations.md/"Fixed bugs"): faltava o passo de ESTREITAMENTO
   (`uint16_t`->`uint8_t` e afins) aqui - a função só reinterpretava o
   tamanho/sinal de ORIGEM (pro caso byte->word assinado), nunca truncava
   pro tamanho de DESTINO. `dst_size`/`dst_signed` fazem esse truncamento:
   quando o destino é de 1 byte, mascara pra 0-255 e, se o tipo de destino
   for assinado, sign-extende o resultado (replicando o bit 7) - do
   contrário fica um zero-extend normal. Sem isso, dobrar essa função (só
   usada no caminho de constant-fold, quando `i->a` já é uma constante
   conhecida em tempo de compilação) produzia o valor de 16 bits inteiro
   em vez do valor truncado - exatamente o bug reproduzido em
   examples/narrowing_cast_in_expr.c. */
static long apply_cast_value(long v, int src_size, int src_signed, int dst_size, int dst_signed) {
    if (src_size == 1) {
        v &= 0xFF;
        if (src_signed && (v & 0x80)) v |= 0xFF00;
    }
    if (dst_size == 1) {
        v &= 0xFF;
        if (dst_signed && (v & 0x80)) v |= 0xFF00;
    }
    return v & 0xFFFF;
}

/* i->imm em IR_UNOP/OP_ASSIGN carrega tamanho (bits baixos) + sinal (bit
   0x100) do tipo de ORIGEM - ver o comentário grande em ir_build.c. */
static int cast_src_size(IrInst *i) { return (int)(i->imm & 0xFF); }
static int cast_src_signed(IrInst *i) { return (i->imm & 0x100) != 0; }

/* Um IR_UNOP/OP_ASSIGN só é uma cópia pura (segura pra alias-propagation
   E pra constant-fold via cast_needs_no_op abaixo) quando byte->word não
   está em jogo - só o único caso de verdade problemático hoje (os outros
   casts - word->word, ou estreitamento byte<-word - já preservam o valor
   como cópia crua no codegen atual, ver o comentário grande em
   codegen.c). */
static int cast_is_pure_copy(IrInst *i) {
    /* Achado 07/09/2026 (4º bug de cast desta sessão - ver
       docs/limitations.md/"Fixed bugs"): este predicado dizia "cópia
       pura" (segura pra alias-propagation: instruções que consomem
       `i->dst` passam a consumir `i->a` direto, e a instrução IR_UNOP em
       si pode virar código morto) pra QUALQUER estreitamento
       (`uint16_t`->`uint8_t` e afins, `i->size` aqui é o tamanho de
       DESTINO em bytes) - só o widening byte->word assinado (comentário
       original abaixo) estava coberto como caso impuro. Estreitamento
       É lossy sempre que o valor de origem não cabe no tamanho de
       destino (`b > 0xFF` truncado pra `uint8_t` vira outro número) -
       tratá-lo como cópia pura faz o otimizador apagar o truncamento
       inteiro: uma expressão como `a + acc - (uint8_t)b` virava
       `a + acc - b` de verdade (o vreg do cast era só um alias do vreg de
       `b` de 16 bits, nunca mascarado). Reproduzido/corrigido junto do
       fix em `codegen.c`/`IR_UNOP`-`OP_ASSIGN` (que tinha o mesmo buraco
       do lado do gerador de código: nenhum `AND #0x00FF`/sign-extend era
       emitido pra estreitamento). */
    if (cast_src_size(i) > i->size) return 0; /* estreitamento: pode truncar o valor */
    return !(i->size == 2 && cast_src_size(i) == 1 && cast_src_signed(i));
}

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
            case IR_UNOP: {
                int pure_copy = (i->op != OP_ASSIGN) || cast_is_pure_copy(i);
                if (i->op == OP_ASSIGN && pure_copy && !multi_def[i->dst] && i->a >= 0) {
                    alias[i->dst] = i->a; /* cast pass-through as copy for propagation purposes */
                }
                if (i->a >= 0 && is_const[i->a] && !multi_def[i->dst]) {
                    long v = cval[i->a], r = v;
                    if (i->op == OP_NEG) r = -v;
                    else if (i->op == OP_BNOT) r = ~v;
                    else if (i->op == OP_NOT) r = !v;
                    else if (i->op == OP_ASSIGN) r = pure_copy ? v : apply_cast_value(v, cast_src_size(i), cast_src_signed(i), i->size, i->is_signed);
                    /* note: i->imm is overwritten below (becomes the folded
                       constant) - already consumed via apply_cast_value above,
                       nothing else in this function reads it as "source size"
                       again after this point for this instruction. */
                    i->kind = IR_CONST; i->imm = r; i->a = -1;
                    is_const[i->dst] = 1; cval[i->dst] = r;
                }
                break;
            }
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
