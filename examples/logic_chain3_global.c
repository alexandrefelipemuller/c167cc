/* BUG-2 (Sirius32, investigado 24/09/2026 - ver docs/limitations.md):
   prova de EXECUÇÃO de ||/&& com 3 termos. Registrado em
   tests/meson.build com as 8 combinações verdadeiro/falso de A/B/C. */
volatile uint16_t A;
volatile uint16_t B;
volatile uint16_t C;
volatile uint16_t OUT_OR;
volatile uint16_t OUT_AND;
volatile uint16_t OUT_MIX;
volatile uint16_t OUT_ORL;
volatile uint16_t OUT_ORR;

void logic_chain3_global(void)
{
    uint16_t r = 0;
    if (A >= 0x10 || B >= 0x20 || C >= 0x30) { r = 1; }
    OUT_OR = r;
    r = 0;
    if (A >= 0x10 && B >= 0x20 && C >= 0x30) { r = 1; }
    OUT_AND = r;
    OUT_MIX = A >= 0x10 || B >= 0x20 && C >= 0x30;
    OUT_ORL = (A >= 0x10 || B >= 0x20) || C >= 0x30;
    OUT_ORR = A >= 0x10 || (B >= 0x20 || C >= 0x30);
}
