volatile uint16_t A;
volatile uint16_t B;
volatile uint16_t QUOC;
volatile uint16_t REM;

/* Exercises IR_DIV32_SYM: a widening 16x16->32 multiply (already handled by
   IR_MUL32_STORE_SYM) followed by a 32/16 division that needs the full
   product, not just its low 16 bits - the exact shape found in the
   bilinear-interpolation cluster in the sibling Sirius32 project (file
   0x3AE96-0x3B7FE). A=1234, B=777 gives produto=958818 (> 65535, so this
   only passes if MDH is really consulted) with quociente=9588, resto=18. */
void div32_global(void)
{
    uint32_t produto = (uint32_t)A * B;
    QUOC = produto / 100;
    REM = produto % 100;
}
