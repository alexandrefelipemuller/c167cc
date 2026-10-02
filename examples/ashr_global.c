volatile int16_t S;
volatile int8_t B;
volatile uint16_t U;
volatile uint8_t UB;
volatile uint16_t N;
volatile int16_t OUT_C;
volatile int16_t OUT_V;
volatile int16_t OUT_LOC;
volatile int16_t OUT_ASSIGN;
volatile int16_t OUT_B;
volatile int16_t OUT_B2;
volatile int16_t OUT_FOLD;
volatile uint16_t OUT_U;
volatile uint16_t OUT_UV;
volatile uint16_t OUT_UB;

/* BUG-16 da Sirius32 (01/10/2026, ver docs/BUGS_C167CC.md lá): `>>` em
   valor com sinal gerava SHR (lógico) em vez de ASHR - o sinal não era
   propagado. Cobre int16_t global e local, contagem constante e variável,
   int8_t (inclusive dois shifts em cadeia), constante dobrada em
   tempo de compilação, e garante que uint16_t/uint8_t continuam SHR. */
void ashr_global(void)
{
    int16_t loc;
    int16_t acc;

    OUT_C = S >> 6;
    OUT_V = S >> N;
    loc = S;
    OUT_LOC = loc >> 3;
    acc = S;
    acc = acc >> N;
    OUT_ASSIGN = acc;
    OUT_B = B >> 1;
    OUT_B2 = (B >> 1) >> N;
    OUT_FOLD = (int16_t)0x8000 >> 4;
    OUT_U = U >> 6;
    OUT_UV = U >> N;
    OUT_UB = UB >> 1;
}
