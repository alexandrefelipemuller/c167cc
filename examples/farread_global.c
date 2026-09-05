/* Exercises IR_FARREAD16_SYM (see include/c167cc/ir.h) - the narrow atomic
   EXTP+MOV pair added 04/09/2026 to unblock file 0x3B488 in the sibling
   Sirius32 project. page=0 maps offset<0x4000 straight to that physical
   address (no DPP override needed to interpret the result), so SRC's
   known @ram address doubles as the "far" target for this test without
   needing a second data page. */
uint16_t c167cc_far_read16(uint16_t page, uint16_t off);

@ram(0x2000)
volatile uint16_t SRC;

volatile uint16_t OUT;

void farread_global(void)
{
    OUT = c167cc_far_read16(0, 0x2000);
}
