#ifndef C167CC_AST_H
#define C167CC_AST_H

#include <stddef.h>

typedef struct {
    const char *file;
    int line;
    int col;
} SrcLoc;

/* ---- Types ---- */

typedef enum {
    TY_VOID,
    TY_I8, TY_U8,
    TY_I16, TY_U16,
    TY_I32, TY_U32,
    TY_PTR,
    TY_STRUCT,
    TY_FUNC,
} TypeKind;

typedef struct Type {
    TypeKind kind;
    struct Type *pointee; /* for TY_PTR */
    size_t array_len;     /* 0 if not array */
    int is_array;
    struct StructDef *struct_def; /* for TY_STRUCT */
    struct Type *func_ret; /* for TY_FUNC: return type. Parameter types are
                               parsed (for valid C syntax) but not recorded -
                               calls through a function pointer are not
                               arity/type-checked, same as direct calls
                               aren't. TY_FUNC only ever appears as a TY_PTR's
                               pointee ("pointer to function"); it is never a
                               standalone variable type. */
} Type;

/* A struct's fields, laid out once at definition time (see struct_def_new).
   Every field is 2-byte aligned, matching this compiler's frame/global
   layout elsewhere (see align2() in the C167 backend) - there is no
   4-byte alignment for int32_t/uint32_t fields, and no packing control. */
typedef struct StructField {
    char *name;
    Type *type;
    int offset;
} StructField;

typedef struct StructDef {
    char *name;
    StructField *fields;
    int nfields;
    int size;
    int is_union; /* if set, every field is at offset 0 (see struct_def_new) */
} StructDef;

Type *type_new(TypeKind kind);
Type *type_new_ptr(Type *pointee);
Type *type_new_array(Type *elem, size_t len);
int type_size(const Type *t);
int type_is_signed(const Type *t);
const char *type_name(const Type *t);

StructDef *struct_def_new(const char *name, char **field_names, Type **field_types, int nfields, int is_union);
const StructField *struct_def_find_field(const StructDef *sd, const char *name);

/* ---- Expressions ---- */

typedef enum {
    EXPR_INT_LIT,
    EXPR_IDENT,
    EXPR_BINARY,
    EXPR_UNARY,
    EXPR_ASSIGN,       /* compound too, via op */
    EXPR_CALL,
    EXPR_INDEX,        /* a[b] */
    EXPR_MEMBER,       /* a.b (a->b is desugared to (*a).b by the parser) */
    EXPR_DEREF,        /* *a */
    EXPR_ADDR,         /* &a */
    EXPR_TERNARY,
    EXPR_CAST,
    EXPR_POSTINC,
    EXPR_POSTDEC,
    EXPR_PREINC,
    EXPR_PREDEC,
} ExprKind;

typedef enum {
    OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_MOD,
    OP_AND, OP_OR, OP_XOR,
    OP_SHL, OP_SHR,
    OP_LAND, OP_LOR,
    OP_EQ, OP_NE, OP_LT, OP_GT, OP_LE, OP_GE,
    OP_NOT, OP_BNOT, OP_NEG,
    OP_ASSIGN,
} OpKind;

typedef struct Expr {
    ExprKind kind;
    SrcLoc loc;
    Type *type; /* filled by semantic analysis */

    /* EXPR_INT_LIT */
    long ival;

    /* EXPR_IDENT */
    char *name;
    struct Symbol *sym; /* resolved by semantic */

    /* EXPR_BINARY / EXPR_UNARY / EXPR_ASSIGN */
    OpKind op;
    struct Expr *lhs;
    struct Expr *rhs; /* also used for EXPR_UNARY operand, EXPR_ASSIGN rhs */

    /* EXPR_CALL */
    struct Expr *callee;
    struct Expr **args;
    int nargs;

    /* EXPR_INDEX */
    struct Expr *base;
    struct Expr *index;

    /* EXPR_MEMBER: base . name (reuses IDENT's `name` field above) */

    /* EXPR_TERNARY */
    struct Expr *cond;
    struct Expr *then_e;
    struct Expr *else_e;

    /* EXPR_CAST */
    Type *cast_type;
} Expr;

/* ---- Statements ---- */

typedef enum {
    STMT_EXPR,
    STMT_DECL,
    STMT_RETURN,
    STMT_IF,
    STMT_WHILE,
    STMT_FOR,
    STMT_BREAK,
    STMT_CONTINUE,
    STMT_BLOCK,
    STMT_SWITCH,
    STMT_CASE,
    STMT_DEFAULT,
} StmtKind;

typedef struct Stmt {
    StmtKind kind;
    SrcLoc loc;

    Expr *expr; /* STMT_EXPR, STMT_RETURN (may be NULL) */

    /* STMT_DECL */
    struct Decl *decl;

    /* STMT_IF */
    Expr *cond;
    struct Stmt *then_s;
    struct Stmt *else_s;

    /* STMT_WHILE / STMT_FOR */
    Expr *for_init_expr;
    struct Stmt *for_init_decl;
    Expr *for_cond;
    Expr *for_post;
    struct Stmt *body;

    /* STMT_BLOCK */
    struct Stmt **stmts;
    int nstmts;
    int transparent; /* if set, does not open a new scope (used for comma-separated declarations) */

    /* STMT_SWITCH */
    Expr *switch_expr;
    struct Stmt **cases; /* STMT_CASE / STMT_DEFAULT */
    int ncases;

    /* STMT_CASE */
    long case_value;
} Stmt;

/* ---- Declarations ---- */

typedef enum {
    ATTR_NONE = 0,
    ATTR_RAM = 1,
    ATTR_ROM = 2,
    ATTR_INTERRUPT = 4,
} AttrKind;

typedef struct Decl {
    char *name;
    Type *type;
    SrcLoc loc;

    int is_param;
    int is_local;
    int is_global;

    AttrKind attrs;
    unsigned long attr_addr;   /* @ram / @rom address */
    int interrupt_vector;      /* @interrupt(n) */

    int is_volatile;
    int is_const;

    Expr *init; /* optional initializer for globals/locals */

    struct Symbol *sym;
} Decl;

typedef struct Func {
    char *name;
    Type *ret_type;
    Decl **params;
    int nparams;
    Stmt *body; /* STMT_BLOCK, NULL if prototype only */
    SrcLoc loc;
    AttrKind attrs;
    int interrupt_vector;
} Func;

typedef enum {
    TOP_FUNC,
    TOP_DECL,
} TopKind;

typedef struct TopLevel {
    TopKind kind;
    Func *func;
    Decl *decl;
} TopLevel;

typedef struct TranslationUnit {
    TopLevel **items;
    int nitems;
    char *filename;
} TranslationUnit;

/* constructors */
Expr *expr_new(ExprKind kind, SrcLoc loc);
Stmt *stmt_new(StmtKind kind, SrcLoc loc);
Decl *decl_new(const char *name, Type *type, SrcLoc loc);
Func *func_new(const char *name, Type *ret_type, SrcLoc loc);
TranslationUnit *tu_new(const char *filename);
void tu_add(TranslationUnit *tu, TopLevel *item);

void ast_dump(TranslationUnit *tu);

#endif
