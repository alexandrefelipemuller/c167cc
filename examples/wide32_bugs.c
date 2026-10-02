/* Casos mínimos dos BUG-6/7/8/10/11/15 da Sirius32 (docs/BUGS_C167CC.md de
   lá): expressão uint32_t rebaixada pra 16 bits. Golden de assembly - a
   execução é coberta pelos testes sim-wide32_*. */
uint32_t bug6(uint16_t x, uint16_t a, uint16_t b)
{
    uint32_t r = (uint32_t)x + (uint32_t)a * (uint32_t)b;
    return r;
}

uint16_t bug7(uint16_t lo, uint16_t hi, uint16_t d)
{
    uint32_t n = ((uint32_t)hi << 16) | lo;
    uint32_t q = n / d;
    return (uint16_t)(q >> 16);
}

uint16_t bug8(void)
{
    uint32_t x = 0x0003D090;
    return (uint16_t)(x >> 16);
}

uint16_t bug10(uint16_t lo, uint16_t hi, uint16_t neg)
{
    uint32_t n = ((uint32_t)hi << 16) | lo;
    uint32_t m = neg ? (uint32_t)(0 - n) : n;
    return (uint16_t)m;
}

uint16_t bug11_lt(uint16_t alo, uint16_t ahi, uint16_t blo, uint16_t bhi)
{
    uint32_t a = ((uint32_t)ahi << 16) | alo;
    uint32_t b = ((uint32_t)bhi << 16) | blo;
    if (a < b) {
        return 1;
    }
    return 0;
}

uint16_t bug11_eq(uint16_t alo, uint16_t ahi, uint16_t blo, uint16_t bhi)
{
    uint32_t a = ((uint32_t)ahi << 16) | alo;
    uint32_t b = ((uint32_t)bhi << 16) | blo;
    if (a == b) {
        return 1;
    }
    return 0;
}

uint16_t bug11_add(uint16_t alo, uint16_t ahi, uint16_t x)
{
    uint32_t a = ((uint32_t)ahi << 16) | alo;
    uint32_t s = a + (uint32_t)x;
    return (uint16_t)(s >> 16);
}

uint16_t bug15(uint16_t lo, uint16_t hi, uint16_t d)
{
    return (uint16_t)((((uint32_t)hi << 16) | lo) / d);
}
