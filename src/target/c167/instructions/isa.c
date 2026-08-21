#include "c167cc/c167_target.h"
#include <string.h>

/* Every entry below corresponds to a mnemonic documented in the
 * "C167CR Derivatives User's Manual", chapter 23 "Instruction Set Summary"
 * (see ../../../../../manual_3286A/c167cr_userguide.pdf). Condition-code
 * suffixes (cc_xx) follow the standard C166 family Instruction Set Manual
 * naming convention; cc_UC, cc_SGT, cc_V and cc_NV are directly attested in
 * the user's manual text (chapter 21/16 code examples). This table is the
 * single source of truth the backend uses when emitting instructions - see
 * docs/assembly-syntax.md.
 */
const C167Instr c167_instr_table[] = {
    { "MOV",   "Rd, Rs | Rd, #data16 | Rd, mem | Rd, [Rs] | [Rd], Rs", "Data movement of a word" },
    { "MOVB",  "Rd, Rs | Rd, #data8 | Rd, [Rs] | [Rd], Rs",            "Data movement of a byte" },
    { "MOVBZ", "Rd, Rs",  "Byte to word movement with zero extension" },
    { "MOVBS", "Rd, Rs",  "Byte to word movement with sign extension" },
    { "ADD",   "Rd, Rs | Rd, #data16", "Addition of two words" },
    { "ADDB",  "Rd, Rs | Rd, #data8",  "Addition of two bytes" },
    { "SUB",   "Rd, Rs | Rd, #data16", "Subtraction of two words" },
    { "SUBB",  "Rd, Rs | Rd, #data8",  "Subtraction of two bytes" },
    { "MUL",   "Rs1, Rs2", "16x16 signed multiplication, result in MDL/MDH" },
    { "MULU",  "Rs1, Rs2", "16x16 unsigned multiplication, result in MDL/MDH" },
    { "DIV",   "Rs",  "16/16 signed division of MDL by Rs, quotient in MDL" },
    { "DIVU",  "Rs",  "16/16 unsigned division of MDL by Rs, quotient in MDL" },
    { "CMP",   "Rd, Rs | Rd, #data16", "Comparison of two words, sets flags" },
    { "CMPB",  "Rd, Rs | Rd, #data8",  "Comparison of two bytes, sets flags" },
    { "AND",   "Rd, Rs | Rd, #data16", "Bitwise AND" },
    { "OR",    "Rd, Rs | Rd, #data16", "Bitwise OR" },
    { "XOR",   "Rd, Rs | Rd, #data16", "Bitwise XOR" },
    { "CPL",   "Rd",  "1's complement of a word" },
    { "NEG",   "Rd",  "2's complement (negation) of a word" },
    { "SHL",   "Rd, Rs | Rd, #data4", "Shift left" },
    { "SHR",   "Rd, Rs | Rd, #data4", "Shift right" },
    { "ROL",   "Rd, Rs | Rd, #data4", "Rotate left" },
    { "ROR",   "Rd, Rs | Rd, #data4", "Rotate right" },
    { "ASHR",  "Rd, Rs | Rd, #data4", "Arithmetic shift right" },
    { "JMPR",  "cc, rel", "Conditional relative jump" },
    { "JMPA",  "cc, caddr", "Conditional absolute jump within segment" },
    { "JMPS",  "seg, caddr", "Unconditional intersegment jump" },
    { "CALLR", "rel", "Unconditional relative call" },
    { "CALLA", "cc, caddr", "Conditional absolute call within segment" },
    /* CALLI's mnemonic is manual-verbatim (see chapter 23's call-instruction
       list), but its operand syntax is not shown in the excerpt available in
       this repo. "cc, Rw" (indirect call, condition cc, target address in
       word register Rw) is the standard C166/ST10/C167-family form used by
       every reference for this instruction; this codegen always passes
       cc_UC (unconditional), matching how JMPR is already used elsewhere in
       this backend for unconditional jumps. Verify against a real
       assembler/ISM before trusting the exact operand order. */
    { "CALLI", "cc, Rw", "Unconditional/conditional indirect call within segment" },
    { "CALLS", "seg, caddr", "Unconditional intersegment call" },
    { "RET",   "", "Return from subroutine (same segment)" },
    { "RETS",  "", "Return from subroutine (intersegment)" },
    { "RETI",  "", "Return from interrupt service routine" },
    { "PUSH",  "Rs", "Push a word onto the system stack" },
    { "POP",   "Rd", "Pop a word from the system stack" },
    { "NOP",   "", "No operation" },
};

const int c167_instr_table_count = sizeof(c167_instr_table) / sizeof(c167_instr_table[0]);

const C167Instr *c167_isa_lookup(const char *mnemonic) {
    for (int i = 0; i < c167_instr_table_count; i++) {
        if (strcmp(c167_instr_table[i].mnemonic, mnemonic) == 0) return &c167_instr_table[i];
    }
    return NULL;
}

static const char *reg_names[C167_REG_COUNT] = {
    "R0","R1","R2","R3","R4","R5","R6","R7",
    "R8","R9","R10","R11","R12","R13","R14","R15",
    "SP",
};

const char *c167_reg_name(C167Reg r) { return reg_names[r]; }

const C167Reg c167_arg_regs[C167_ARG_REGS_COUNT] = { C167_R4, C167_R5, C167_R6, C167_R7 };

/* R0-R3 and R8-R10 are available to the register allocator for IR
 * temporaries; R4-R7 are reserved for argument marshalling, R11/R12 are
 * spill-reload scratch registers, R13/R14 are reserved callee-saved
 * registers for future use, R15 is the compiler-managed frame pointer.
 * See docs/abi.md.
 */
const C167Reg c167_temp_pool[] = {
    C167_R0, C167_R1, C167_R2, C167_R3, C167_R8, C167_R9, C167_R10
};
const int c167_temp_pool_count = sizeof(c167_temp_pool) / sizeof(c167_temp_pool[0]);
