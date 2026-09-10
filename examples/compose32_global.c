volatile uint16_t HI;
volatile uint16_t LO;
volatile uint32_t OUT;

/* Exercises IR_COMPOSE32_STORE_SYM: the inverse of IR_SHR32_SYM's N==16
   case - composing a 32-bit value from two independent 16-bit halves,
   `dst32 = ((uint32_t)hi << 16) | lo;`, the exact shape found in
   `research/interpolacao_motor/3b51c_motor_divisao_peso_interpolacao.c`
   in the sibling Sirius32 project (file 0x3B51C). Before the fix, this
   fell into the generic 16-bit-only IR_BINOP path: `(uint32_t)HI << 16`
   was computed as a plain 16-bit SHL (SHL by 16 on a 16-bit register
   yields 0, not a genuine widening shift), the OR with LO then wrote
   only OUT's low word - OUT's high word was never written at all,
   silently keeping whatever garbage/zero was already there and losing
   HI's contribution entirely.
   HI=0x1234, LO=0x2222 gives OUT=0x12342222 - only observable in a
   16-bit-truncated read of OUT if the high word (0x1234) is genuinely
   there, so this only passes if MDH-equivalent storage is real. LO is
   kept below 0x8000 so the simulator's signed 16-bit register dump
   doesn't turn it negative in the test's expected-value comparison.
   Read
   back split as two 16-bit halves (OUT_HI/OUT_LO) since the toy
   simulator harness (tests/port_to_toy_asm.py) only ever compares
   16-bit register/memory values, not a full 32-bit read. */
volatile uint16_t OUT_HI;
volatile uint16_t OUT_LO;

void compose32_global(void)
{
    OUT = ((uint32_t)HI << 16) | LO;
    OUT_HI = (uint16_t)(OUT >> 16);
    OUT_LO = (uint16_t)OUT;
}
