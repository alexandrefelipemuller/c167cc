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

**What's deliberately not supported yet** (this compiler's IR represents
every value as one register-sized virtual register, so anything that
would require moving a whole struct through one is rejected with a clear
error instead of miscompiling):

- Struct **parameters and return values passed by value** - pass/return
  a pointer instead (`struct Point *`).
- Struct **assignment/copy** (`a = b;` where both are structs) - copy the
  fields you need individually, or use pointers.
- Struct **initializers** (`struct Point p = {1, 2};`).

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
dereference `*p`, address-of `&x`, and C-style casts `(T)e`.

## Grammar

The grammar lives in `src/parser/parser.y` (bison) with tokens from
`src/parser/lexer.l` (flex). It builds the AST (`include/c167cc/ast.h`)
directly in the grammar actions.
