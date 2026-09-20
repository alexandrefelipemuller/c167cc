/* Exercises a type qualifier (const/volatile) inside an expression cast to
   pointer type, e.g. `(volatile uint16_t *)0xFD90` - previously rejected by
   the parser with "syntax error (near 'volatile')" because qual_opt was
   only wired into declarations, not the cast-expression grammar rule (see
   docs/limitations.md). SFR/register access from research/ conventionally
   needs `volatile` on this exact shape. */
uint16_t OUT;

void cast_qualifier_fixed_addr(void)
{
    *(volatile uint16_t *)0xFD90 = 42;
    OUT = *(const volatile uint16_t *)0xFD90;
}
