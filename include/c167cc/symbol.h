#ifndef C167CC_SYMBOL_H
#define C167CC_SYMBOL_H

#include "c167cc/ast.h"

typedef enum {
    SYM_GLOBAL,
    SYM_LOCAL,
    SYM_PARAM,
    SYM_FUNC,
} SymKind;

typedef struct Symbol {
    SymKind kind;
    char *name;
    Type *type;

    /* globals */
    int has_abs_addr;
    unsigned long abs_addr; /* @ram / @rom */
    AttrKind attrs;

    /* locals/params: stack offset from frame base, filled by codegen */
    int stack_offset;

    /* function */
    Func *func;

    struct Symbol *next; /* scope chain */
} Symbol;

typedef struct Scope {
    Symbol *symbols;
    struct Scope *parent;
} Scope;

Scope *scope_new(Scope *parent);
Symbol *scope_declare(Scope *sc, const char *name, SymKind kind, Type *type);
Symbol *scope_lookup(Scope *sc, const char *name);

#endif
