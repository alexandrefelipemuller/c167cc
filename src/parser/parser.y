%{
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "c167cc/ast.h"
#include "c167cc/parse_lists.h"

extern int yylex(void);
extern int yylineno;
extern char *yytext;
extern const char *g_input_filename;
TranslationUnit *g_tu;

static int yyerror(const char *msg) {
    fprintf(stderr, "%s:%d: error: %s (near '%s')\n", g_input_filename, yylineno, msg, yytext);
    exit(1);
}

static SrcLoc loc(void) {
    SrcLoc l; l.file = g_input_filename; l.line = yylineno; l.col = 0; return l;
}

/* pending attribute state applied to next declaration/function */
static AttrKind pend_attrs = ATTR_NONE;
static unsigned long pend_addr = 0;
static int pend_vec = -1;

static void clear_attrs(void) { pend_attrs = ATTR_NONE; pend_addr = 0; pend_vec = -1; }

/* base type shared by all declarators in a `type_spec declarator, declarator, ...;`
   declaration; set by a mid-rule action right after type_spec is reduced */
static Type *g_decl_base_type = NULL;

/* enum constants: a flat, unscoped name->value table (parser-global, like a
   simple macro table). An enum constant reference is folded directly into an
   EXPR_INT_LIT at parse time, so nothing downstream (AST/IR/codegen) needs to
   know enums exist. */
typedef struct EnumConst { char *name; long value; struct EnumConst *next; } EnumConst;
static EnumConst *g_enum_consts = NULL;
static long g_enum_next_value = 0;

static void enum_reset(void) { g_enum_next_value = 0; }

static void enum_declare(const char *name, long value) {
    EnumConst *c = calloc(1, sizeof(EnumConst));
    c->name = strdup(name); c->value = value; c->next = g_enum_consts; g_enum_consts = c;
    g_enum_next_value = value + 1;
}

static int enum_lookup(const char *name, long *out) {
    for (EnumConst *c = g_enum_consts; c; c = c->next) {
        if (strcmp(c->name, name) == 0) { *out = c->value; return 1; }
    }
    return 0;
}

/* struct types: a parser-global name->Type registry (parallel to the enum
   constant table above), plus a scratch buffer collecting the field list of
   whichever struct is currently being parsed. A struct must be fully
   defined - textually, at top level - before any use of `struct Name` as a
   type, same one-pass restriction already implied by the enum table.
   `struct` and `union` tags share this one registry (like real C's shared
   struct/union/enum tag namespace); struct_type_lookup_checked below
   rejects using the wrong keyword for a given tag. */
typedef struct StructTypeEntry { char *name; Type *type; struct StructTypeEntry *next; } StructTypeEntry;
static StructTypeEntry *g_struct_types = NULL;

static Type *struct_type_lookup(const char *name) {
    for (StructTypeEntry *e = g_struct_types; e; e = e->next) {
        if (strcmp(e->name, name) == 0) return e->type;
    }
    return NULL;
}

static Type *struct_type_lookup_checked(const char *name, int want_union) {
    Type *t = struct_type_lookup(name);
    if (!t) {
        fprintf(stderr, "%s:%d: error: undefined %s '%s'\n", g_input_filename, yylineno, want_union ? "union" : "struct", name);
        exit(1);
    }
    if (t->struct_def->is_union != want_union) {
        fprintf(stderr, "%s:%d: error: '%s' is a %s, not a %s\n", g_input_filename, yylineno, name,
                t->struct_def->is_union ? "union" : "struct", want_union ? "union" : "struct");
        exit(1);
    }
    return t;
}

static void struct_type_register(const char *name, StructDef *sd) {
    if (struct_type_lookup(name)) {
        fprintf(stderr, "%s:%d: error: '%s' redefined\n", g_input_filename, yylineno, name);
        exit(1);
    }
    Type *t = type_new(TY_STRUCT);
    t->struct_def = sd;
    StructTypeEntry *e = calloc(1, sizeof(StructTypeEntry));
    e->name = strdup(name); e->type = t; e->next = g_struct_types; g_struct_types = e;
}

static char **g_field_names = NULL;
static Type **g_field_types = NULL;
static int g_field_n = 0, g_field_cap = 0;

static void struct_field_reset(void) { g_field_n = 0; }

static void struct_field_add(const char *name, Type *type) {
    if (g_field_n == g_field_cap) {
        g_field_cap = g_field_cap ? g_field_cap * 2 : 4;
        g_field_names = realloc(g_field_names, sizeof(char*) * g_field_cap);
        g_field_types = realloc(g_field_types, sizeof(Type*) * g_field_cap);
    }
    g_field_names[g_field_n] = strdup(name);
    g_field_types[g_field_n] = type;
    g_field_n++;
}

static void el_push(ExprList *l, Expr *e) {
    if (l->n == l->cap) { l->cap = l->cap ? l->cap * 2 : 4; l->data = realloc(l->data, sizeof(Expr*) * l->cap); }
    l->data[l->n++] = e;
}

static void sl_push(StmtList *l, Stmt *s) {
    if (l->n == l->cap) { l->cap = l->cap ? l->cap * 2 : 4; l->data = realloc(l->data, sizeof(Stmt*) * l->cap); }
    l->data[l->n++] = s;
}

/* void (*name)(params) declarator: builds "pointer to function returning ret".
   Parameter types are consumed by the fnptr_param_types grammar purely to
   accept valid C syntax; they are not recorded (see Type.func_ret's comment
   in ast.h - calls through a function pointer aren't arity/type-checked). */
static Type *build_funcptr_type(Type *ret) {
    Type *ft = type_new(TY_FUNC);
    ft->func_ret = ret;
    return type_new_ptr(ft);
}

static void dl_push(DeclList *l, Decl *d) {
    if (l->n == l->cap) { l->cap = l->cap ? l->cap * 2 : 4; l->data = realloc(l->data, sizeof(Decl*) * l->cap); }
    l->data[l->n++] = d;
}
%}

%union {
    long ival;
    char *str;
    Type *type;
    Expr *expr;
    Stmt *stmt;
    Decl *decl;
    Func *func;
    TopLevel *top;
    ExprList *exprlist;
    StmtList *stmtlist;
    DeclList *decllist;
}

%token <ival> INT_LIT
%token <str> IDENT
%token KW_VOID KW_CHAR KW_SIGNED KW_UNSIGNED
%token KW_I8 KW_U8 KW_I16 KW_U16 KW_I32 KW_U32
%token KW_IF KW_ELSE KW_WHILE KW_FOR KW_RETURN KW_BREAK KW_CONTINUE
%token KW_SWITCH KW_CASE KW_DEFAULT KW_VOLATILE KW_CONST KW_ENUM KW_STRUCT KW_UNION
%token AT_RAM AT_ROM AT_INTERRUPT
%token TOK_EQ TOK_NE TOK_LE TOK_GE TOK_ANDAND TOK_OROR TOK_SHL TOK_SHR TOK_ARROW
%token OP_INC OP_DEC
%token OP_ADD_ASSIGN OP_SUB_ASSIGN OP_MUL_ASSIGN OP_DIV_ASSIGN

%type <type> type_spec
%type <expr> expr assign_expr cond_expr logor_expr logand_expr bitor_expr bitxor_expr bitand_expr
%type <expr> eq_expr rel_expr shift_expr add_expr mul_expr unary_expr postfix_expr primary_expr
%type <expr> opt_expr init_opt
%type <exprlist> arg_list
%type <stmt> stmt block_stmt decl_stmt if_stmt while_stmt for_stmt simple_stmt case_stmt
%type <stmtlist> stmt_list case_list
%type <decl> param decl_declarator
%type <decllist> param_list param_list_opt decl_declarator_list
%type <ival> array_suffix ptr_opt
%type <top> top_item

%right '='
%right '?' ':'
%left TOK_OROR
%left TOK_ANDAND
%left '|'
%left '^'
%left '&'
%left TOK_EQ TOK_NE
%left '<' '>' TOK_LE TOK_GE
%left TOK_SHL TOK_SHR
%left '+' '-'
%left '*' '/' '%'
%right UNARY
%left OP_INC OP_DEC

%start translation_unit

%%

translation_unit:
      /* empty */
    | translation_unit top_item { if ($2) tu_add(g_tu, $2); }
    ;

attr_opt:
      /* empty */
    | attr_opt AT_RAM '(' INT_LIT ')' { pend_attrs |= ATTR_RAM; pend_addr = (unsigned long)$4; }
    | attr_opt AT_ROM '(' INT_LIT ')' { pend_attrs |= ATTR_ROM; pend_addr = (unsigned long)$4; }
    | attr_opt AT_INTERRUPT '(' INT_LIT ')' { pend_attrs |= ATTR_INTERRUPT; pend_vec = (int)$4; }
    ;

qual_opt:
      /* empty */
    | qual_opt KW_VOLATILE
    | qual_opt KW_CONST
    ;

top_item:
      attr_opt qual_opt type_spec ptr_opt IDENT '(' param_list_opt ')' ';'
      {
        Type *rt = $4 ? type_new_ptr($3) : $3;
        Func *f = func_new($5, rt, loc());
        f->attrs = pend_attrs; f->interrupt_vector = pend_vec;
        if ($7) { f->params = $7->data; f->nparams = $7->n; }
        f->body = NULL;
        clear_attrs();
        TopLevel *t = calloc(1, sizeof(TopLevel)); t->kind = TOP_FUNC; t->func = f;
        $$ = t;
      }
    | attr_opt qual_opt type_spec ptr_opt IDENT '(' param_list_opt ')' block_stmt
      {
        Type *rt = $4 ? type_new_ptr($3) : $3;
        Func *f = func_new($5, rt, loc());
        f->attrs = pend_attrs; f->interrupt_vector = pend_vec;
        if ($7) { f->params = $7->data; f->nparams = $7->n; }
        f->body = $9;
        clear_attrs();
        TopLevel *t = calloc(1, sizeof(TopLevel)); t->kind = TOP_FUNC; t->func = f;
        $$ = t;
      }
    | attr_opt qual_opt type_spec ';'
      {
        /* a bare type definition with no variable, e.g. `struct Point { ... };`
           or `enum Color { ... };` - type_spec itself already registered the
           struct/enum as a side effect when it matched a defining form. */
        clear_attrs();
        $$ = NULL;
      }
    | attr_opt qual_opt type_spec decl_declarator_list ';'
      {
        /* Only the first declared global carries a @ram/@rom/@interrupt attribute
           in this MVP; a comma list of absolute-address globals is unusual. */
        Decl *d = $4->data[0];
        d->is_global = 1;
        d->attrs = pend_attrs; d->attr_addr = pend_addr;
        clear_attrs();
        TopLevel *t = calloc(1, sizeof(TopLevel)); t->kind = TOP_DECL; t->decl = d;
        $$ = t;
        for (int k = 1; k < $4->n; k++) {
            Decl *dk = $4->data[k];
            dk->is_global = 1;
            TopLevel *tk = calloc(1, sizeof(TopLevel)); tk->kind = TOP_DECL; tk->decl = dk;
            tu_add(g_tu, tk);
        }
      }
    ;

enumerator_list:
      enumerator
    | enumerator_list ',' enumerator
    ;

enumerator:
      IDENT { enum_declare($1, g_enum_next_value); }
    | IDENT '=' INT_LIT { enum_declare($1, $3); }
    ;

struct_field_list:
      struct_field
    | struct_field_list struct_field
    ;

struct_field:
      qual_opt type_spec decl_declarator_list ';'
      {
        for (int k = 0; k < $3->n; k++) {
            Decl *d = $3->data[k];
            if (d->init) {
                fprintf(stderr, "%s:%d: error: field initializers are not supported\n", g_input_filename, yylineno);
                exit(1);
            }
            struct_field_add(d->name, d->type);
        }
      }
    ;

param_list_opt:
      /* empty */ { $$ = NULL; }
    | KW_VOID { $$ = NULL; }
    | param_list { $$ = $1; }
    ;

param_list:
      param { DeclList *l = calloc(1, sizeof(DeclList)); dl_push(l, $1); $$ = l; }
    | param_list ',' param { dl_push($1, $3); $$ = $1; }
    ;

param:
      qual_opt type_spec ptr_opt IDENT
      {
        Type *t = $3 ? type_new_ptr($2) : $2;
        Decl *d = decl_new($4, t, loc());
        d->is_param = 1;
        $$ = d;
      }
    | qual_opt type_spec '(' '*' IDENT ')' '(' fnptr_param_types_opt ')'
      {
        Decl *d = decl_new($5, build_funcptr_type($2), loc());
        d->is_param = 1;
        $$ = d;
      }
    ;

fnptr_param_types_opt:
      /* empty */
    | KW_VOID
    | fnptr_param_types
    ;

fnptr_param_types:
      qual_opt type_spec ptr_opt
    | fnptr_param_types ',' qual_opt type_spec ptr_opt
    ;

ptr_opt:
      /* empty */ { $$ = 0; }
    | ptr_opt '*' { $$ = $1 + 1; }
    ;

array_suffix:
      /* empty */ { $$ = 0; }
    | '[' INT_LIT ']' { $$ = $2; }
    ;

decl_declarator:
      ptr_opt IDENT array_suffix init_opt
      {
        Type *t = g_decl_base_type;
        if ($1 > 0) t = type_new_ptr(t);
        if ($3 > 0) t = type_new_array(t, (size_t)$3);
        Decl *d = decl_new($2, t, loc());
        d->init = $4;
        $$ = d;
      }
    | '(' '*' IDENT ')' '(' fnptr_param_types_opt ')' init_opt
      {
        Decl *d = decl_new($3, build_funcptr_type(g_decl_base_type), loc());
        d->init = $8;
        $$ = d;
      }
    ;

decl_declarator_list:
      decl_declarator { DeclList *l = calloc(1, sizeof(DeclList)); dl_push(l, $1); $$ = l; }
    | decl_declarator_list ',' decl_declarator { dl_push($1, $3); $$ = $1; }
    ;

init_opt:
      /* empty */ { $$ = NULL; }
    | '=' assign_expr { $$ = $2; }
    ;

type_spec:
      KW_VOID { $$ = type_new(TY_VOID); g_decl_base_type = $$; }
    | KW_CHAR { $$ = type_new(TY_I8); g_decl_base_type = $$; }
    | KW_SIGNED KW_CHAR { $$ = type_new(TY_I8); g_decl_base_type = $$; }
    | KW_UNSIGNED KW_CHAR { $$ = type_new(TY_U8); g_decl_base_type = $$; }
    | KW_SIGNED { $$ = type_new(TY_I16); g_decl_base_type = $$; }
    | KW_UNSIGNED { $$ = type_new(TY_U16); g_decl_base_type = $$; }
    | KW_I8 { $$ = type_new(TY_I8); g_decl_base_type = $$; }
    | KW_U8 { $$ = type_new(TY_U8); g_decl_base_type = $$; }
    | KW_I16 { $$ = type_new(TY_I16); g_decl_base_type = $$; }
    | KW_U16 { $$ = type_new(TY_U16); g_decl_base_type = $$; }
    | KW_I32 { $$ = type_new(TY_I32); g_decl_base_type = $$; }
    | KW_U32 { $$ = type_new(TY_U32); g_decl_base_type = $$; }
    | KW_ENUM IDENT '{' { enum_reset(); } enumerator_list '}'
      { $$ = type_new(TY_I16); g_decl_base_type = $$; }
    | KW_ENUM '{' { enum_reset(); } enumerator_list '}'
      { $$ = type_new(TY_I16); g_decl_base_type = $$; }
    | KW_ENUM IDENT { $$ = type_new(TY_I16); g_decl_base_type = $$; }
    | KW_STRUCT IDENT '{' { struct_field_reset(); } struct_field_list '}'
      {
        StructDef *sd = struct_def_new($2, g_field_names, g_field_types, g_field_n, 0);
        struct_type_register($2, sd);
        $$ = struct_type_lookup($2);
        g_decl_base_type = $$;
      }
    | KW_STRUCT IDENT
      { $$ = struct_type_lookup_checked($2, 0); g_decl_base_type = $$; }
    | KW_UNION IDENT '{' { struct_field_reset(); } struct_field_list '}'
      {
        StructDef *sd = struct_def_new($2, g_field_names, g_field_types, g_field_n, 1);
        struct_type_register($2, sd);
        $$ = struct_type_lookup($2);
        g_decl_base_type = $$;
      }
    | KW_UNION IDENT
      { $$ = struct_type_lookup_checked($2, 1); g_decl_base_type = $$; }
    ;

/* Every type_spec reduction also records the base type for any
   decl_declarator(_list) that may follow it (variable declarations).
   This is done here - rather than via a mid-rule action at the call
   sites - because a mid-rule action right after type_spec would create
   an LALR reduce/reduce conflict with the function-declaration
   alternatives of top_item, which share the same "type_spec ptr_opt
   IDENT" prefix. Function declarations simply never read
   g_decl_base_type, so setting it unconditionally is harmless. */

block_stmt:
      '{' stmt_list '}'
      {
        Stmt *s = stmt_new(STMT_BLOCK, loc());
        if ($2) { s->stmts = $2->data; s->nstmts = $2->n; }
        $$ = s;
      }
    ;

stmt_list:
      /* empty */ { $$ = calloc(1, sizeof(StmtList)); }
    | stmt_list stmt { sl_push($1, $2); $$ = $1; }
    ;

stmt:
      simple_stmt { $$ = $1; }
    | block_stmt { $$ = $1; }
    | decl_stmt { $$ = $1; }
    | if_stmt { $$ = $1; }
    | while_stmt { $$ = $1; }
    | for_stmt { $$ = $1; }
    | KW_SWITCH '(' expr ')' '{' case_list '}'
      {
        Stmt *s = stmt_new(STMT_SWITCH, loc());
        s->switch_expr = $3;
        if ($6) { s->cases = $6->data; s->ncases = $6->n; }
        $$ = s;
      }
    ;

case_list:
      /* empty */ { $$ = calloc(1, sizeof(StmtList)); }
    | case_list case_stmt { sl_push($1, $2); $$ = $1; }
    ;

case_stmt:
      KW_CASE INT_LIT ':' stmt_list
      {
        Stmt *s = stmt_new(STMT_CASE, loc());
        s->case_value = $2;
        if ($4) { s->stmts = $4->data; s->nstmts = $4->n; }
        $$ = s;
      }
    | KW_DEFAULT ':' stmt_list
      {
        Stmt *s = stmt_new(STMT_DEFAULT, loc());
        if ($3) { s->stmts = $3->data; s->nstmts = $3->n; }
        $$ = s;
      }
    ;

simple_stmt:
      opt_expr ';' { Stmt *s = stmt_new(STMT_EXPR, loc()); s->expr = $1; $$ = s; }
    | KW_RETURN opt_expr ';' { Stmt *s = stmt_new(STMT_RETURN, loc()); s->expr = $2; $$ = s; }
    | KW_BREAK ';' { $$ = stmt_new(STMT_BREAK, loc()); }
    | KW_CONTINUE ';' { $$ = stmt_new(STMT_CONTINUE, loc()); }
    ;

opt_expr:
      /* empty */ { $$ = NULL; }
    | expr { $$ = $1; }
    ;

decl_stmt:
      qual_opt type_spec decl_declarator_list ';'
      {
        DeclList *l = $3;
        if (l->n == 1) {
            l->data[0]->is_local = 1;
            Stmt *s = stmt_new(STMT_DECL, loc());
            s->decl = l->data[0];
            $$ = s;
        } else {
            Stmt *blk = stmt_new(STMT_BLOCK, loc());
            blk->transparent = 1;
            blk->stmts = calloc(l->n, sizeof(Stmt *));
            blk->nstmts = l->n;
            for (int k = 0; k < l->n; k++) {
                l->data[k]->is_local = 1;
                Stmt *s = stmt_new(STMT_DECL, loc());
                s->decl = l->data[k];
                blk->stmts[k] = s;
            }
            $$ = blk;
        }
      }
    ;

if_stmt:
      KW_IF '(' expr ')' stmt %prec UNARY
      {
        Stmt *s = stmt_new(STMT_IF, loc());
        s->cond = $3; s->then_s = $5; s->else_s = NULL;
        $$ = s;
      }
    | KW_IF '(' expr ')' stmt KW_ELSE stmt
      {
        Stmt *s = stmt_new(STMT_IF, loc());
        s->cond = $3; s->then_s = $5; s->else_s = $7;
        $$ = s;
      }
    ;

while_stmt:
      KW_WHILE '(' expr ')' stmt
      {
        Stmt *s = stmt_new(STMT_WHILE, loc());
        s->cond = $3; s->body = $5;
        $$ = s;
      }
    ;

for_stmt:
      KW_FOR '(' opt_expr ';' opt_expr ';' opt_expr ')' stmt
      {
        Stmt *s = stmt_new(STMT_FOR, loc());
        s->for_init_expr = $3; s->for_cond = $5; s->for_post = $7; s->body = $9;
        $$ = s;
      }
    | KW_FOR '(' qual_opt type_spec decl_declarator ';' opt_expr ';' opt_expr ')' stmt
      {
        Decl *d = $5;
        d->is_local = 1;
        Stmt *ds = stmt_new(STMT_DECL, loc()); ds->decl = d;
        Stmt *s = stmt_new(STMT_FOR, loc());
        s->for_init_decl = ds; s->for_cond = $7; s->for_post = $9; s->body = $11;
        $$ = s;
      }
    ;

arg_list:
      /* empty */ { $$ = calloc(1, sizeof(ExprList)); }
    | assign_expr { ExprList *l = calloc(1, sizeof(ExprList)); el_push(l, $1); $$ = l; }
    | arg_list ',' assign_expr { el_push($1, $3); $$ = $1; }
    ;

expr:
      assign_expr { $$ = $1; }
    | expr ',' assign_expr { $$ = $3; }
    ;

assign_expr:
      cond_expr { $$ = $1; }
    | unary_expr '=' assign_expr
      { Expr *e = expr_new(EXPR_ASSIGN, loc()); e->op = OP_ASSIGN; e->lhs = $1; e->rhs = $3; $$ = e; }
    | unary_expr OP_ADD_ASSIGN assign_expr
      { Expr *e = expr_new(EXPR_ASSIGN, loc()); e->op = OP_ADD; e->lhs = $1; e->rhs = $3; $$ = e; }
    | unary_expr OP_SUB_ASSIGN assign_expr
      { Expr *e = expr_new(EXPR_ASSIGN, loc()); e->op = OP_SUB; e->lhs = $1; e->rhs = $3; $$ = e; }
    | unary_expr OP_MUL_ASSIGN assign_expr
      { Expr *e = expr_new(EXPR_ASSIGN, loc()); e->op = OP_MUL; e->lhs = $1; e->rhs = $3; $$ = e; }
    | unary_expr OP_DIV_ASSIGN assign_expr
      { Expr *e = expr_new(EXPR_ASSIGN, loc()); e->op = OP_DIV; e->lhs = $1; e->rhs = $3; $$ = e; }
    ;

cond_expr:
      logor_expr { $$ = $1; }
    | logor_expr '?' expr ':' cond_expr
      { Expr *e = expr_new(EXPR_TERNARY, loc()); e->cond = $1; e->then_e = $3; e->else_e = $5; $$ = e; }
    ;

logor_expr:
      logand_expr { $$ = $1; }
    | logor_expr TOK_OROR logand_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_LOR; e->lhs = $1; e->rhs = $3; $$ = e; }
    ;

logand_expr:
      bitor_expr { $$ = $1; }
    | logand_expr TOK_ANDAND bitor_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_LAND; e->lhs = $1; e->rhs = $3; $$ = e; }
    ;

bitor_expr:
      bitxor_expr { $$ = $1; }
    | bitor_expr '|' bitxor_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_OR; e->lhs = $1; e->rhs = $3; $$ = e; }
    ;

bitxor_expr:
      bitand_expr { $$ = $1; }
    | bitxor_expr '^' bitand_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_XOR; e->lhs = $1; e->rhs = $3; $$ = e; }
    ;

bitand_expr:
      eq_expr { $$ = $1; }
    | bitand_expr '&' eq_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_AND; e->lhs = $1; e->rhs = $3; $$ = e; }
    ;

eq_expr:
      rel_expr { $$ = $1; }
    | eq_expr TOK_EQ rel_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_EQ; e->lhs = $1; e->rhs = $3; $$ = e; }
    | eq_expr TOK_NE rel_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_NE; e->lhs = $1; e->rhs = $3; $$ = e; }
    ;

rel_expr:
      shift_expr { $$ = $1; }
    | rel_expr '<' shift_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_LT; e->lhs = $1; e->rhs = $3; $$ = e; }
    | rel_expr '>' shift_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_GT; e->lhs = $1; e->rhs = $3; $$ = e; }
    | rel_expr TOK_LE shift_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_LE; e->lhs = $1; e->rhs = $3; $$ = e; }
    | rel_expr TOK_GE shift_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_GE; e->lhs = $1; e->rhs = $3; $$ = e; }
    ;

shift_expr:
      add_expr { $$ = $1; }
    | shift_expr TOK_SHL add_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_SHL; e->lhs = $1; e->rhs = $3; $$ = e; }
    | shift_expr TOK_SHR add_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_SHR; e->lhs = $1; e->rhs = $3; $$ = e; }
    ;

add_expr:
      mul_expr { $$ = $1; }
    | add_expr '+' mul_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_ADD; e->lhs = $1; e->rhs = $3; $$ = e; }
    | add_expr '-' mul_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_SUB; e->lhs = $1; e->rhs = $3; $$ = e; }
    ;

mul_expr:
      unary_expr { $$ = $1; }
    | mul_expr '*' unary_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_MUL; e->lhs = $1; e->rhs = $3; $$ = e; }
    | mul_expr '/' unary_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_DIV; e->lhs = $1; e->rhs = $3; $$ = e; }
    | mul_expr '%' unary_expr
      { Expr *e = expr_new(EXPR_BINARY, loc()); e->op = OP_MOD; e->lhs = $1; e->rhs = $3; $$ = e; }
    ;

unary_expr:
      postfix_expr { $$ = $1; }
    | '!' unary_expr { Expr *e = expr_new(EXPR_UNARY, loc()); e->op = OP_NOT; e->rhs = $2; $$ = e; }
    | '~' unary_expr { Expr *e = expr_new(EXPR_UNARY, loc()); e->op = OP_BNOT; e->rhs = $2; $$ = e; }
    | '-' unary_expr %prec UNARY { Expr *e = expr_new(EXPR_UNARY, loc()); e->op = OP_NEG; e->rhs = $2; $$ = e; }
    | '&' unary_expr %prec UNARY { Expr *e = expr_new(EXPR_ADDR, loc()); e->rhs = $2; $$ = e; }
    | '*' unary_expr %prec UNARY { Expr *e = expr_new(EXPR_DEREF, loc()); e->rhs = $2; $$ = e; }
    | OP_INC unary_expr { Expr *e = expr_new(EXPR_PREINC, loc()); e->lhs = $2; $$ = e; }
    | OP_DEC unary_expr { Expr *e = expr_new(EXPR_PREDEC, loc()); e->lhs = $2; $$ = e; }
    | '(' type_spec ptr_opt ')' unary_expr %prec UNARY
      {
        Type *t = $3 ? type_new_ptr($2) : $2;
        Expr *e = expr_new(EXPR_CAST, loc()); e->cast_type = t; e->rhs = $5; $$ = e;
      }
    ;

postfix_expr:
      primary_expr { $$ = $1; }
    | postfix_expr '[' expr ']'
      { Expr *e = expr_new(EXPR_INDEX, loc()); e->base = $1; e->index = $3; $$ = e; }
    | postfix_expr '(' arg_list ')'
      {
        Expr *e = expr_new(EXPR_CALL, loc());
        e->callee = $1;
        if ($3) { e->args = $3->data; e->nargs = $3->n; }
        $$ = e;
      }
    | postfix_expr OP_INC { Expr *e = expr_new(EXPR_POSTINC, loc()); e->lhs = $1; $$ = e; }
    | postfix_expr OP_DEC { Expr *e = expr_new(EXPR_POSTDEC, loc()); e->lhs = $1; $$ = e; }
    | postfix_expr '.' IDENT
      { Expr *e = expr_new(EXPR_MEMBER, loc()); e->base = $1; e->name = $3; $$ = e; }
    | postfix_expr TOK_ARROW IDENT
      {
        /* p->b desugars to (*p).b so the IR builder only needs to know EXPR_MEMBER. */
        Expr *d = expr_new(EXPR_DEREF, loc()); d->rhs = $1;
        Expr *e = expr_new(EXPR_MEMBER, loc()); e->base = d; e->name = $3;
        $$ = e;
      }
    ;

primary_expr:
      INT_LIT { Expr *e = expr_new(EXPR_INT_LIT, loc()); e->ival = $1; $$ = e; }
    | IDENT
      {
        long v;
        if (enum_lookup($1, &v)) {
            Expr *e = expr_new(EXPR_INT_LIT, loc()); e->ival = v; $$ = e;
        } else {
            Expr *e = expr_new(EXPR_IDENT, loc()); e->name = $1; $$ = e;
        }
      }
    | '(' expr ')' { $$ = $2; }
    ;

%%
