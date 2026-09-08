uint8_t A;
uint8_t OUT;
void pointer_arith_scaling_byte_global(void) {
    const uint8_t *p = &A + 3;
    uint16_t base = (uint16_t)&A;
    uint16_t off = (uint16_t)p - base;
    OUT = (uint8_t)off;
}
