volatile uint16_t A;
volatile uint16_t B;
volatile uint16_t QUOC;
volatile uint16_t REM;

/* Exercises IR_DIV32_MUL: an INLINE widening product used directly as the
   dividend of a division (`((uint32_t)a * (uint32_t)b) / expr`), never
   assigned to a named 32-bit temp first - unlike div32_global.c above,
   which already goes through a 32-bit symbol and hits IR_DIV32_SYM. This
   is the exact shape of the widening-division family stuck in
   research/interpolacao_motor in the sibling Sirius32 project (files
   0x3BA56/0x3BAF2/0x3BB4C), found 19/09/2026: before IR_DIV32_MUL existed,
   this fell through to the generic 16-bit path and divided only the low
   word of the product with DIVU instead of DIVLU. Same numbers as
   div32_global.c: A=1234, B=777 gives produto=958818 (> 65535, so this
   only passes if MDH is really consulted), quociente=9588, resto=18. */
void div32_mul_global(void)
{
    QUOC = (uint16_t)(((uint32_t)A * (uint32_t)B) / 100);
    REM = (uint16_t)(((uint32_t)A * (uint32_t)B) % 100);
}
