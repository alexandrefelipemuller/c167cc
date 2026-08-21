#ifndef C167CC_C167_TARGET_H
#define C167CC_C167_TARGET_H

#include "c167cc/ir.h"
#include <stdio.h>

/* ---- Instruction database ----
 * Mnemonics and their meaning are taken from the Siemens/Infineon
 * "C167CR Derivatives User's Manual" (docs reference: manual_3286A/c167cr_userguide.pdf,
 * chapter 23 "Instruction Set Summary") and from well-known C166 family
 * instruction-set documentation. See docs/assembly-syntax.md for the
 * provenance of every mnemonic used by the backend.
 */
typedef struct {
    const char *mnemonic;
    const char *operand_syntax; /* human-readable description of expected operands */
    const char *description;
} C167Instr;

extern const C167Instr c167_instr_table[];
extern const int c167_instr_table_count;
const C167Instr *c167_isa_lookup(const char *mnemonic);

/* ---- Registers ---- */
typedef enum {
    C167_R0, C167_R1, C167_R2, C167_R3, C167_R4, C167_R5, C167_R6, C167_R7,
    C167_R8, C167_R9, C167_R10, C167_R11, C167_R12, C167_R13, C167_R14, C167_R15,
    C167_SP,
    C167_REG_COUNT
} C167Reg;

const char *c167_reg_name(C167Reg r);

/* ---- ABI (compiler-defined, see docs/abi.md) ---- */
#define C167_ARG_REGS_COUNT 4
extern const C167Reg c167_arg_regs[C167_ARG_REGS_COUNT];
#define C167_RETURN_REG C167_R0
#define C167_FRAME_REG  C167_R15
#define C167_SPILL_SCRATCH_1 C167_R11
#define C167_SPILL_SCRATCH_2 C167_R12

/* pool of registers available to the linear-scan allocator for IR temporaries */
extern const C167Reg c167_temp_pool[];
extern const int c167_temp_pool_count;

/* ---- Assembly line representation (backend output, before printing) ---- */
typedef struct AsmLine {
    char *label;      /* optional label preceding the instruction, or line is label-only if mnemonic is NULL */
    char *mnemonic;
    char *operands;    /* preformatted operand string, may be NULL */
    char *comment;
    struct AsmLine *next;
} AsmLine;

typedef struct AsmFunc {
    char *name;
    int is_global;
    AsmLine *head, *tail;
    struct AsmFunc *next;
} AsmFunc;

typedef struct AsmGlobal {
    char *name;
    Symbol *sym;
    struct AsmGlobal *next;
} AsmGlobal;

typedef struct AsmProgram {
    AsmGlobal *globals;
    AsmFunc *funcs;
} AsmProgram;

AsmProgram *c167_codegen(IrModule *mod);
void c167_print(AsmProgram *prog, FILE *out);

#endif
