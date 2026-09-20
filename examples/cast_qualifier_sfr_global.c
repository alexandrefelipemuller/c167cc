/* Same shape as cast_qualifier_fixed_addr.c but actually simulated: writes
   through a `(volatile uint16_t *)ADDR` cast and reads it back, to prove
   the parser fix (qual_opt now accepted inside an expression cast, see
   docs/limitations.md) produces working codegen and not just a parse. */
uint16_t IN;
uint16_t OUT;

void cast_qualifier_sfr_global(void)
{
    *(volatile uint16_t *)0x2000 = IN;
    OUT = *(const volatile uint16_t *)0x2000;
}
