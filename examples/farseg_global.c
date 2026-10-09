/* Exercises IR_FARREAD16_SEG / IR_FARWRITE8_SEG (see include/c167cc/ir.h):
   EXTS seg,#1 fused with MOV d,[off] / MOVB [off],val. Physical address =
   seg*0x10000 + off. */
uint16_t c167cc_far_read16_seg(uint16_t seg, uint16_t off);
void c167cc_far_write8_seg(uint16_t seg, uint16_t off, uint8_t val);

volatile uint16_t OUT;

void farseg_global(void)
{
    c167cc_far_write8_seg(2, 0x1234, 0x5A);
    OUT = c167cc_far_read16_seg(2, 0x1234);
}
