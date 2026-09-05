# ABI (compiler-defined)

There is no single "official" C ABI for the C167 that this project targets
(the vendor manuals describe the instruction set and hardware stack, not a
C calling convention). This ABI is therefore **defined by this compiler**,
documented here, and easy to change in one place
(`src/target/c167/instructions/isa.c` + `src/target/c167/codegen/codegen.c`).
It must not be assumed to match any particular existing firmware's calling
convention.

## Register usage

| Registers        | Role                                                          |
|-------------------|---------------------------------------------------------------|
| `R0`              | Function return value                                         |
| `R0-R3, R8-R10`   | Pool available to the register allocator for IR temporaries   |
| `R4-R7`           | Argument-passing registers (word arguments, in order)         |
| `R11, R12`        | Reserved scratch registers used to reload/store spilled values|
| `R13, R14`        | Reserved (unused for now; available for future callee-saved use) |
| `R15`             | Compiler-managed frame pointer                                |
| `SP`              | Hardware system stack pointer                                 |

Argument registers (`R4-R7`) and the temporary pool (`R0-R3, R8-R10`) are
disjoint on purpose: argument marshalling for a `CALLR` is then a simple
sequence of `MOV Rarg, Rsrc` instructions immediately before the call,
with no risk of one argument's source being clobbered by writing another
argument register.

## Arguments

Up to **4 word-sized arguments** are supported, passed in `R4, R5, R6, R7`
in order. A byte-sized (`int8_t`/`uint8_t`) argument still occupies a full
argument register/slot. Calling or defining a function with more than 4
parameters is a compile error in this MVP - stack-passed arguments are not
implemented yet (see [limitations.md](limitations.md)).

## Return value

The function result is returned in `R0`. `void` functions leave `R0`
unspecified.

**Struct-by-value return** is the one exception, via a hidden pointer
argument ("sret" convention - see
[c-language.md#structs](c-language.md#structs) for the source-level
rules): a function declared to return `struct Name` gets an implicit
extra parameter, always argument slot 0 (`R4`), pointing at the caller's
destination; every real declared parameter shifts up by one slot
accordingly (so such a function has 3 real argument registers left, not
4, before hitting the `R4-R7` limit above). The function's `RET`/`RETI`
carries no value in `R0` in this case - the result was already written
through the hidden pointer by the time control returns. This is how
`file 0x3B488`/`file 0x3B860` (Sirius32, register-pair return in the
original firmware) get promoted despite this compiler having no
register-pair return of its own: the C167 target simply never sees a
register pair, only a struct written through a pointer, same as any
other struct-by-pointer code in this compiler.

## Stack frame

Each function establishes a frame:

```
PUSH  R15            ; save caller's frame pointer
SUB   SP, #framesize  ; reserve locals + register-allocator spill slots
MOV   R15, SP          ; R15 now points at the base of the frame
MOV   [R15+#0], R4     ; store incoming parameters (as many as declared)
...
```

and tears it down before every `RET`/`RETI`:

```
ADD   SP, #framesize
POP   R15
RET
```

Parameters and local variables live in the frame at `[R15+#offset]`,
assigned in declaration order and rounded up to 2-byte alignment. Spill
slots allocated by the register allocator are placed immediately after the
locals, inside the same frame.

## Interrupts

A function declared `@interrupt(n)` uses the same prologue/epilogue as a
normal function, but ends with `RETI` instead of `RET`. Full interrupt
context save/restore (all registers, PSW, etc.) is **not** implemented in
this MVP; see [limitations.md](limitations.md).
