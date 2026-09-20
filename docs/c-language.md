# Supported C subset

## Types

`void`, `char`, `signed char`, `unsigned char`, `int8_t`, `uint8_t`,
`int16_t`, `uint16_t`, `int32_t`, `uint32_t`, `signed` (alias for
`int16_t`), `unsigned` (alias for `uint16_t`).

Pointers (`T *`) and fixed-size arrays (`T name[N]`) of any of the above.

Not supported: `float`, `double`, `long double`, `long long`, typedefs,
C++, dynamic allocation, threads, variadic functions, a standard
library.

## Enums

`enum Name { A, B, C = 5, D };` is supported, at top level only (not
inside a function body). Enumerator values follow the usual C rule
(start at 0, auto-increment, jump on an explicit `= <int literal>`), but
the initializer must be a plain integer literal, not a general constant
expression. `enum Name` can then be used as a type (equivalent to
`int16_t`) anywhere a type is expected, including for local variables.

Enum constants are resolved entirely at parse time: an identifier that
matches a declared enumerator is folded directly into an integer literal
before the AST is even built. This means enum constants share one flat,
unscoped namespace (like `#define`s, not like C's tag/member scoping) -
two enums cannot declare the same enumerator name. Anonymous enums
(`enum { A, B };`, only useful for their constants) are supported too.

## Structs

```c
struct Point {
    int16_t x;
    int16_t y;
};

int16_t sum_point(struct Point *p) { return p->x + p->y; }
```

`struct Name { field; ... };` is supported, at top level only (like
`enum`). Once defined, `struct Name` can be used as a type anywhere a
type is expected - including a pointer to it, a field of another struct
(nesting by value is fine; self-reference must go through a pointer,
same as C), and a fixed-size array of it. Both `.` and `->` member
access are supported (`p->b` is just parsed as `(*p).b`).

Field layout has no padding/packing control: each field starts 2-byte
aligned and the struct's total size is rounded up to an even number,
matching this compiler's frame/global layout everywhere else (see
`align2()` in the C167 backend) - there is no 4-byte alignment even for
`int32_t`/`uint32_t` fields.

**Struct return by value and struct assignment ARE supported** (achado
21/08/2026, compilando `reimplementacao_c` pela 1ª vez - o código real
precisava disso pra praticamente toda função que monta uma resposta
K-line, e de novo em 04/09/2026 ao decifrar `file 0x3B488`/`0x3B860` do
Sirius32, ambas devolvendo par de registrador no binário original).
Nenhum dos dois cabe num único vreg deste backend (cada valor de IR é 1
registrador virtual - ver `docs/limitations.md`), então os dois viram
CÓPIA CAMPO A CAMPO em vez de "mover a struct":

- **Retorno por valor** (`struct Point f(...) { ...; return p; }`) usa a
  convenção "sret": a função ganha um parâmetro OCULTO adicional (sempre
  o argumento 0 de verdade, na frente de qualquer parâmetro real
  declarado) que é o ENDEREÇO de onde o chamador quer o resultado; o
  `return p;` (só uma variável struct simples é aceita, não uma
  subexpressão) vira uma cópia campo a campo pra esse endereço, e a
  função sempre termina com `RET`/`RETI` normal (sem valor em R0 - o
  resultado já foi escrito por cópia). Chamar essa função só é suportado
  em 2 contextos, onde o destino final já é conhecido ANTES da chamada
  (o que evita uma cópia extra): `struct Point x = f(...);` e
  `x = f(...);` (`x` já declarado). Chamar em qualquer OUTRO contexto
  (aninhado dentro de outra expressão, ex. `g(f(...))`) ainda não é
  suportado. Como o parâmetro oculto ocupa o 1º slot de registrador de
  argumento, uma função que devolve struct por valor tem, na prática,
  1 argumento REAL a menos disponível (3 em vez de 4) antes de esbarrar
  no limite de `docs/abi.md`.
- **Atribuição/cópia** (`a = b;` onde os dois são structs, incluindo
  `b` sendo o retorno de uma chamada de função que devolve struct - o
  caso `b = f(...)` reusa a mesma convenção sret acima, escrevendo
  direto no endereço de `a` sem cópia extra) - suportado quando o lado
  esquerdo é uma variável simples (`EXPR_IDENT`); atribuir a um
  campo/elemento de struct/array (`p->campo = outra_struct;`) ainda cai
  no erro antigo (não miscompila, só não é suportado ainda - não usado
  por nenhum código real compilado até agora).

**O que continua deliberadamente NÃO suportado** (seria preciso mover a
struct inteira por 1 vreg, ou uma extensão que ainda não foi escrita):

- Struct **parâmetros passados por valor** - passe um ponteiro em vez
  disso (`struct Point *`). Continua sendo um erro de compilação
  explícito (`ir_build.c`, checado na declaração da função, antes de
  falhar mais fundo no codegen).
- Struct **inicializadores** (`struct Point p = {1, 2};`) - declare sem
  inicializador e atribua os campos individualmente.

Unlike `enum Name`, a `struct Name` reference *is* checked: using an
undefined tag is a compile error. Struct tags share one flat, top-level
namespace (parallel to, but separate from, the enum constant table
above) rather than C's proper tag scoping.

## Unions

```c
union Value {
    int16_t as_signed;
    uint16_t as_unsigned;
};
```

`union Name { field; ... };` works exactly like `struct` above (same
top-level-only rule, same "no by-value copy/param/return" restrictions,
same `.`/`->` member access) - every field simply starts at offset 0
instead of being laid out sequentially, and the union's size is its
largest field, rounded up to even per the same 2-byte-alignment rule.

`struct` and `union` tags share one namespace (as in real C: you can't
declare both `struct Foo` and `union Foo`), so using the wrong keyword
for an already-declared tag (`union Point` when `Point` was declared
`struct`) is a compile error, not silently accepted.

## Function pointers

```c
int16_t add(int16_t a, int16_t b) { return a + b; }
int16_t sub(int16_t a, int16_t b) { return a - b; }

int16_t apply(int16_t (*op)(int16_t, int16_t), int16_t x, int16_t y)
{
    return op(x, y);
}

int16_t run(void)
{
    int16_t (*fp)(int16_t, int16_t) = add;   /* a bare function name decays
                                                 to its address, like an array */
    return apply(fp, 3, 4) + apply(&sub, 3, 4) + (*fp)(1, 2);
}
```

`RetType (*name)(ParamTypes);` is supported as a variable/global/local
declarator, a struct or union field, and a function parameter. A
function name used as a value (with or without `&`) decays to its
address, the same way an array decays to a pointer to its first element.
`(*fp)(...)` and `fp(...)` are equivalent, per the usual C rule that
dereferencing a function pointer just gives back the function.

Calling through a function pointer is **not** arity- or type-checked
against the pointer's declared signature - the parameter-type list is
parsed (so `void (*cb)(int16_t, uint8_t);` is valid syntax) but not
recorded or verified at the call site, exactly like this compiler
already doesn't check argument types/count for direct calls either.
Passing the wrong argument list through a function pointer is undefined
behavior, same as it would be through a mismatched direct-call
declaration.

Not supported: arrays of function pointers, functions returning a
function pointer, and function-pointer arithmetic (comparison for
equality, e.g. `fp == 0`, works fine - it's an ordinary 2-byte value
like any other pointer).

`int32_t`/`uint32_t` can be declared, loaded and stored, but arithmetic on
32-bit values is not implemented by the backend yet (see
[limitations.md](limitations.md)).

## Compiler-recognized intrinsic: `c167cc_far_read16`

```c
uint16_t c167cc_far_read16(uint16_t page, uint16_t off);  /* just a prototype - no body, ever */

uint16_t read_far_word(uint16_t page, uint16_t off)
{
    return c167cc_far_read16(page, off);
}
```

A direct call to this exact name, with exactly 2 arguments, is recognized
by `ir_build.c` and never actually compiled as a function call (no `CALLS`
is emitted, and no such function needs to exist anywhere - the plain
prototype above only exists so the call type-checks normally, like any
other declared-but-undefined extern). It lowers to one atomic
`EXTP page,#1` / `MOV dst,[off]` pair with nothing emitted between them -
see `IR_FARREAD16_SYM` in `include/c167cc/ir.h` and the "Memory
segmentation" entry in [limitations.md](limitations.md) for why this
exists as a single fixed intrinsic rather than a general far-pointer type.
To read multiple consecutive far words (as real firmware routines that use
`EXTP_S` this way typically do), call it once per word with the offset
advanced at the C level (`c167cc_far_read16(page, off)`, then
`c167cc_far_read16(page, off + 2)`, ...) - each call is independently
atomic, so nothing needs to track "the pointer" across the two.

## Declarations

- Global and local variables, with optional initializers.
- Functions with up to 4 parameters (see [abi.md](abi.md)) and a
  prototype-only form (`f(...);` with no body).
- `@ram(addr)` / `@rom(addr)` on a global declaration to place it at a
  fixed absolute address (see [memory-model.md](memory-model.md)).
- `@interrupt(n)` on a function to mark it as an interrupt service routine.
- `volatile` and `const` are parsed and accepted but do not currently
  change code generation beyond the `@ram`/`@rom` interaction.

## Statements

`if`/`else`, `while`, `for` (including a `for (T i = ...; ...; ...)`
init-declaration), `break`, `continue`, `return`, blocks, `switch`/`case`/
`default`.

## Expressions

Arithmetic `+ - * / %`, bitwise `& | ^ ~ << >>`, logical `! && ||`
(short-circuit), comparisons `== != < > <= >=`, assignment `=` and
compound assignment `+= -= *= /=`, `++`/`--` (pre and post), the ternary
operator `?:`, function calls, array indexing `a[i]`, pointer
dereference `*p`, address-of `&x`, and C-style casts `(T)e`. A cast's type
name may include `const`/`volatile` (in any order/combination), e.g.
`(volatile uint16_t *)0xFD90` - needed to express a `volatile`
pointer-to-fixed-address read/write inline in an expression, without first
declaring a named pointer variable.

## Grammar

The grammar lives in `src/parser/parser.y` (bison) with tokens from
`src/parser/lexer.l` (flex). It builds the AST (`include/c167cc/ast.h`)
directly in the grammar actions.
