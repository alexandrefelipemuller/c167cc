#include "c167cc/ast.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

static void *xalloc(size_t n) {
    void *p = calloc(1, n);
    if (!p) { fprintf(stderr, "out of memory\n"); exit(1); }
    return p;
}

Type *type_new(TypeKind kind) {
    Type *t = xalloc(sizeof(Type));
    t->kind = kind;
    return t;
}

Type *type_new_ptr(Type *pointee) {
    Type *t = type_new(TY_PTR);
    t->pointee = pointee;
    return t;
}

Type *type_new_array(Type *elem, size_t len) {
    Type *t = type_new(TY_PTR); /* array decays; base kind acts like ptr to elem */
    t->pointee = elem;
    t->is_array = 1;
    t->array_len = len;
    return t;
}

int type_size(const Type *t) {
    switch (t->kind) {
        case TY_VOID: return 0;
        case TY_I8: case TY_U8: return 1;
        case TY_I16: case TY_U16: return 2;
        case TY_I32: case TY_U32: return 4;
        case TY_PTR:
            if (t->is_array) return type_size(t->pointee) * (int)t->array_len;
            return 2; /* near pointer */
        case TY_STRUCT: return t->struct_def->size;
        case TY_FUNC: return 0; /* never instantiated as a value; only appears as a TY_PTR's pointee */
    }
    return 0;
}

int type_is_signed(const Type *t) {
    switch (t->kind) {
        case TY_I8: case TY_I16: case TY_I32: return 1;
        default: return 0;
    }
}

const char *type_name(const Type *t) {
    switch (t->kind) {
        case TY_VOID: return "void";
        case TY_I8: return "int8_t";
        case TY_U8: return "uint8_t";
        case TY_I16: return "int16_t";
        case TY_U16: return "uint16_t";
        case TY_I32: return "int32_t";
        case TY_U32: return "uint32_t";
        case TY_PTR: return t->is_array ? "array" : "pointer";
        case TY_STRUCT: return t->struct_def->name;
        case TY_FUNC: return "function";
    }
    return "?";
}

StructDef *struct_def_new(const char *name, char **field_names, Type **field_types, int nfields, int is_union) {
    StructDef *sd = xalloc(sizeof(StructDef));
    sd->name = strdup(name);
    sd->fields = xalloc(sizeof(StructField) * (nfields ? nfields : 1));
    sd->nfields = nfields;
    sd->is_union = is_union;
    int offset = 0, max_size = 0;
    for (int i = 0; i < nfields; i++) {
        int fsize = type_size(field_types[i]);
        sd->fields[i].name = strdup(field_names[i]);
        sd->fields[i].type = field_types[i];
        if (is_union) {
            sd->fields[i].offset = 0;
            if (fsize > max_size) max_size = fsize;
        } else {
            offset = (offset + 1) & ~1; /* align2, matching the backend's frame/global layout */
            sd->fields[i].offset = offset;
            offset += fsize;
        }
    }
    sd->size = is_union ? ((max_size + 1) & ~1) : ((offset + 1) & ~1);
    return sd;
}

const StructField *struct_def_find_field(const StructDef *sd, const char *name) {
    for (int i = 0; i < sd->nfields; i++) {
        if (strcmp(sd->fields[i].name, name) == 0) return &sd->fields[i];
    }
    return NULL;
}

Expr *expr_new(ExprKind kind, SrcLoc loc) {
    Expr *e = xalloc(sizeof(Expr));
    e->kind = kind;
    e->loc = loc;
    return e;
}

Stmt *stmt_new(StmtKind kind, SrcLoc loc) {
    Stmt *s = xalloc(sizeof(Stmt));
    s->kind = kind;
    s->loc = loc;
    return s;
}

Decl *decl_new(const char *name, Type *type, SrcLoc loc) {
    Decl *d = xalloc(sizeof(Decl));
    d->name = strdup(name);
    d->type = type;
    d->loc = loc;
    return d;
}

Func *func_new(const char *name, Type *ret_type, SrcLoc loc) {
    Func *f = xalloc(sizeof(Func));
    f->name = strdup(name);
    f->ret_type = ret_type;
    f->loc = loc;
    return f;
}

TranslationUnit *tu_new(const char *filename) {
    TranslationUnit *tu = xalloc(sizeof(TranslationUnit));
    tu->filename = strdup(filename);
    return tu;
}

void tu_add(TranslationUnit *tu, TopLevel *item) {
    tu->items = realloc(tu->items, sizeof(TopLevel *) * (tu->nitems + 1));
    tu->items[tu->nitems++] = item;
}

/* ---------------- dump ---------------- */

static void indent(int n) { for (int i = 0; i < n; i++) fputs("  ", stdout); }

static const char *op_name(OpKind op) {
    switch (op) {
        case OP_ADD: return "+"; case OP_SUB: return "-"; case OP_MUL: return "*";
        case OP_DIV: return "/"; case OP_MOD: return "%";
        case OP_AND: return "&"; case OP_OR: return "|"; case OP_XOR: return "^";
        case OP_SHL: return "<<"; case OP_SHR: return ">>";
        case OP_LAND: return "&&"; case OP_LOR: return "||";
        case OP_EQ: return "=="; case OP_NE: return "!=";
        case OP_LT: return "<"; case OP_GT: return ">";
        case OP_LE: return "<="; case OP_GE: return ">=";
        case OP_NOT: return "!"; case OP_BNOT: return "~"; case OP_NEG: return "-";
        case OP_ASSIGN: return "=";
    }
    return "?";
}

static void dump_expr(Expr *e, int lvl) {
    if (!e) { indent(lvl); printf("<null>\n"); return; }
    indent(lvl);
    switch (e->kind) {
        case EXPR_INT_LIT: printf("Int %ld\n", e->ival); break;
        case EXPR_IDENT: printf("Ident %s\n", e->name); break;
        case EXPR_BINARY:
            printf("Binary %s\n", op_name(e->op));
            dump_expr(e->lhs, lvl + 1);
            dump_expr(e->rhs, lvl + 1);
            break;
        case EXPR_UNARY:
            printf("Unary %s\n", op_name(e->op));
            dump_expr(e->rhs, lvl + 1);
            break;
        case EXPR_ASSIGN:
            printf("Assign %s\n", op_name(e->op));
            dump_expr(e->lhs, lvl + 1);
            dump_expr(e->rhs, lvl + 1);
            break;
        case EXPR_CALL:
            printf("Call\n");
            dump_expr(e->callee, lvl + 1);
            for (int i = 0; i < e->nargs; i++) dump_expr(e->args[i], lvl + 1);
            break;
        case EXPR_INDEX:
            printf("Index\n");
            dump_expr(e->base, lvl + 1);
            dump_expr(e->index, lvl + 1);
            break;
        case EXPR_DEREF: printf("Deref\n"); dump_expr(e->rhs, lvl + 1); break;
        case EXPR_MEMBER: printf("Member .%s\n", e->name); dump_expr(e->base, lvl + 1); break;
        case EXPR_ADDR: printf("Addr\n"); dump_expr(e->rhs, lvl + 1); break;
        case EXPR_TERNARY:
            printf("Ternary\n");
            dump_expr(e->cond, lvl + 1);
            dump_expr(e->then_e, lvl + 1);
            dump_expr(e->else_e, lvl + 1);
            break;
        case EXPR_CAST:
            printf("Cast (%s)\n", type_name(e->cast_type));
            dump_expr(e->rhs, lvl + 1);
            break;
        case EXPR_POSTINC: printf("PostInc\n"); dump_expr(e->lhs, lvl + 1); break;
        case EXPR_POSTDEC: printf("PostDec\n"); dump_expr(e->lhs, lvl + 1); break;
        case EXPR_PREINC: printf("PreInc\n"); dump_expr(e->lhs, lvl + 1); break;
        case EXPR_PREDEC: printf("PreDec\n"); dump_expr(e->lhs, lvl + 1); break;
    }
}

static void dump_stmt(Stmt *s, int lvl) {
    if (!s) return;
    indent(lvl);
    switch (s->kind) {
        case STMT_EXPR: printf("ExprStmt\n"); dump_expr(s->expr, lvl + 1); break;
        case STMT_DECL:
            printf("Decl %s : %s\n", s->decl->name, type_name(s->decl->type));
            if (s->decl->init) dump_expr(s->decl->init, lvl + 1);
            break;
        case STMT_RETURN:
            printf("Return\n");
            if (s->expr) dump_expr(s->expr, lvl + 1);
            break;
        case STMT_IF:
            printf("If\n");
            dump_expr(s->cond, lvl + 1);
            dump_stmt(s->then_s, lvl + 1);
            if (s->else_s) dump_stmt(s->else_s, lvl + 1);
            break;
        case STMT_WHILE:
            printf("While\n");
            dump_expr(s->cond, lvl + 1);
            dump_stmt(s->body, lvl + 1);
            break;
        case STMT_FOR:
            printf("For\n");
            if (s->for_init_decl) dump_stmt(s->for_init_decl, lvl + 1);
            if (s->for_init_expr) dump_expr(s->for_init_expr, lvl + 1);
            if (s->for_cond) dump_expr(s->for_cond, lvl + 1);
            if (s->for_post) dump_expr(s->for_post, lvl + 1);
            dump_stmt(s->body, lvl + 1);
            break;
        case STMT_BREAK: printf("Break\n"); break;
        case STMT_CONTINUE: printf("Continue\n"); break;
        case STMT_BLOCK:
            printf("Block\n");
            for (int i = 0; i < s->nstmts; i++) dump_stmt(s->stmts[i], lvl + 1);
            break;
        case STMT_SWITCH:
            printf("Switch\n");
            dump_expr(s->switch_expr, lvl + 1);
            for (int i = 0; i < s->ncases; i++) dump_stmt(s->cases[i], lvl + 1);
            break;
        case STMT_CASE:
            printf("Case %ld\n", s->case_value);
            for (int i = 0; i < s->nstmts; i++) dump_stmt(s->stmts[i], lvl + 1);
            break;
        case STMT_DEFAULT:
            printf("Default\n");
            for (int i = 0; i < s->nstmts; i++) dump_stmt(s->stmts[i], lvl + 1);
            break;
    }
}

void ast_dump(TranslationUnit *tu) {
    printf("TranslationUnit %s\n", tu->filename);
    for (int i = 0; i < tu->nitems; i++) {
        TopLevel *it = tu->items[i];
        if (it->kind == TOP_FUNC) {
            Func *f = it->func;
            printf("Func %s : %s(", f->name, type_name(f->ret_type));
            for (int j = 0; j < f->nparams; j++) {
                printf("%s%s %s", j ? ", " : "", type_name(f->params[j]->type), f->params[j]->name);
            }
            printf(")\n");
            if (f->body) dump_stmt(f->body, 1);
        } else {
            Decl *d = it->decl;
            printf("Decl %s : %s", d->name, type_name(d->type));
            if (d->attrs & ATTR_RAM) printf(" @ram(0x%lX)", d->attr_addr);
            if (d->attrs & ATTR_ROM) printf(" @rom(0x%lX)", d->attr_addr);
            printf("\n");
        }
    }
}
