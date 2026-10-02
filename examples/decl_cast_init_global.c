volatile int16_t LO;
volatile int16_t HI;
volatile uint16_t W;
volatile uint16_t OUT_HI;
volatile uint16_t OUT_LO;
volatile int16_t OUT_ASHR_HI;
volatile uint16_t OUT_ASHR_LO;
volatile uint16_t OUT_WORD;
volatile uint16_t OUT_BYTE;
volatile uint16_t OUT_SECOND;

/* BUG-17 da Sirius32 (02/10/2026): um cast dentro do inicializador trocava o
   tipo da variável declarada pelo tipo do último cast (`int32_t n = ... |
   (uint16_t)lo;` declarava `n` como uint16_t; `uint16_t w = (uint8_t)x + y;`
   declarava `w` como uint8_t), inclusive o dos declaradores seguintes da
   mesma lista. Também cobre `int32_t >> n` com sinal (ASHR na palavra alta,
   complemento do BUG-16). */
void decl_cast_init_global(void)
{
    int32_t n = ((int32_t)HI << 16) | (uint16_t)LO;
    int32_t m = n >> 4;
    uint16_t w = (uint8_t)W + 0x0300;
    uint8_t b = (uint16_t)W;
    uint16_t first = (uint8_t)W, second = W;

    OUT_HI = (uint16_t)(n >> 16);
    OUT_LO = (uint16_t)n;
    OUT_ASHR_HI = (int16_t)(m >> 16);
    OUT_ASHR_LO = (uint16_t)m;
    OUT_WORD = w;
    OUT_BYTE = b;
    OUT_SECOND = second + first;
}
