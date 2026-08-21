#include "c167cc/symbol.h"
#include <stdlib.h>
#include <string.h>

Scope *scope_new(Scope *parent) {
    Scope *s = calloc(1, sizeof(Scope));
    s->parent = parent;
    return s;
}

Symbol *scope_declare(Scope *sc, const char *name, SymKind kind, Type *type) {
    Symbol *sym = calloc(1, sizeof(Symbol));
    sym->name = strdup(name);
    sym->kind = kind;
    sym->type = type;
    sym->next = sc->symbols;
    sc->symbols = sym;
    return sym;
}

Symbol *scope_lookup(Scope *sc, const char *name) {
    for (Scope *s = sc; s; s = s->parent) {
        for (Symbol *sym = s->symbols; sym; sym = sym->next) {
            if (strcmp(sym->name, name) == 0) return sym;
        }
    }
    return NULL;
}
