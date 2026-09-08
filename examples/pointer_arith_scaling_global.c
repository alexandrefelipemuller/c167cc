uint16_t A;
uint16_t OUT;
void pointer_arith_scaling_global(void) {
    const uint16_t *p = &A + 3;
    uint16_t base = (uint16_t)&A;
    uint16_t off = (uint16_t)p - base;
    OUT = off;
}
