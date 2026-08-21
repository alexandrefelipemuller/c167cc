#ifndef C167CC_PARSE_LISTS_H
#define C167CC_PARSE_LISTS_H

#include "c167cc/ast.h"

typedef struct { Expr **data; int n; int cap; } ExprList;
typedef struct { Stmt **data; int n; int cap; } StmtList;
typedef struct { Decl **data; int n; int cap; } DeclList;

#endif
