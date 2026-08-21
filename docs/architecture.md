# Architecture

## Pipeline

```
 test.c
   │  flex (src/parser/lexer.l)
   ▼
 tokens
   │  bison (src/parser/parser.y)
   ▼
 AST (include/c167cc/ast.h, src/ast/ast.c)
   │  src/ir/ir_build.c  (also does the semantic resolution: symbol table
   │                       lookups, type propagation - src/semantic/semantic.c)
   ▼
 Typed IR (include/c167cc/ir.h)
   │  src/optimizer/optimizer.c
   ▼
 Optimized IR
   │  src/target/c167/codegen/codegen.c
   │    (uses src/target/c167/registers/regalloc.c for register allocation
   │     and src/target/c167/instructions/isa.c as the instruction database)
   ▼
 AsmProgram (include/c167cc/c167_target.h)
   │  src/target/c167/printer/printer.c
   ▼
 test.asm
```

`src/driver/main.c` wires the stages together and implements the CLI.

## Directory layout

```
include/c167cc/     public headers shared across modules
src/ast/            AST node constructors and the --dump-ast printer
src/parser/         flex lexer + bison grammar, builds the AST directly
src/semantic/       symbol table / scope chain (shared by ir_build)
src/ir/             typed IR data structures, AST → IR lowering, --dump-ir
src/optimizer/      constant folding, copy propagation, DCE, dead-code strip
src/target/c167/
  instructions/      the instruction database (single source of truth)
  registers/         linear-scan register allocator
  abi/               (reserved; the ABI is documented in docs/abi.md and
                       implemented directly in codegen.c/isa.c for now)
  memory/            (reserved for future near/far pointer lowering)
  codegen/           IR → C167 instruction selection
  printer/           AsmProgram → text
src/driver/          CLI entry point
tests/               golden and determinism tests (meson test)
examples/            sample .c programs
docs/                this documentation
```

The AST is built directly by the bison actions rather than via a separate
tree-building pass, to keep the parser small; every node still carries a
`SrcLoc` (file/line/column) for diagnostics.

## Why no separate "semantic" pass?

The grammar for this C subset is small enough that symbol resolution and
basic type propagation can happen safely while lowering to IR (in
`src/ir/ir_build.c`), reusing the scope-chain implementation in
`src/semantic/semantic.c`. If the language grows, that resolution logic
should be split into its own AST-walking pass before IR construction.
