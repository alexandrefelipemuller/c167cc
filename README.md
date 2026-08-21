# c167cc

A small, modular C-to-assembly compiler that targets the Siemens/Infineon
**C167CR** microcontroller. This first version compiles a subset of C down
to **C167 assembly text (`.asm`)**. It does **not** assemble, link, or
produce machine code / binaries — that is deliberately left for a later
phase (see [docs/limitations.md](docs/limitations.md)).

```
C source → Lexer → Parser → AST → Semantic analysis → IR → Optimizer → C167 backend → .asm
```

## Building

Requires `gcc`, `flex`, `bison`, `meson`, `ninja`.

```sh
meson setup build
ninja -C build
meson test -C build
```

If `../simulador/` (a separately-built C166/C167 assembler + simulator,
validated against real ECU firmware) is present as a sibling directory,
`meson test` also runs `sim-*` tests that assemble and simulate a few
generated programs and check the computed result - see
`docs/limitations.md#validation`.

## Usage

```sh
cat > test.c <<'EOF'
uint16_t add(uint16_t a, uint16_t b)
{
    return a + b;
}
EOF

./build/c167cc test.c -o test.asm
cat test.asm
```

Other options:

```sh
c167cc --dump-ast test.c     # print the AST
c167cc --dump-ir  test.c     # print the typed IR
c167cc --dump-asm test.c     # print the generated assembly to stdout
c167cc --verbose  test.c -o test.asm
```

## Documentation

- [docs/architecture.md](docs/architecture.md) — pipeline and module layout
- [docs/c-language.md](docs/c-language.md) — supported C subset
- [docs/c167-backend.md](docs/c167-backend.md) — code generation and register allocation
- [docs/abi.md](docs/abi.md) — the compiler's calling convention
- [docs/memory-model.md](docs/memory-model.md) — near/far pointers, `@ram`/`@rom`/`@interrupt`
- [docs/assembly-syntax.md](docs/assembly-syntax.md) — instruction syntax and provenance
- [docs/limitations.md](docs/limitations.md) — known limitations and what's next

## Status

This is an MVP. Correctness and simplicity were prioritized over
optimization and assembly quality. See `docs/limitations.md` for what is
intentionally out of scope.
