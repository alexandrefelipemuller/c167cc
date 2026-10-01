volatile uint8_t D;
volatile uint16_t W;
volatile int16_t S16;
volatile int8_t S8;
volatile uint16_t OUT_GT;
volatile uint16_t OUT_LT;
volatile int16_t OUT_WIDE;
volatile int16_t OUT_FROM_W;
volatile uint16_t OUT_U8;
volatile uint16_t OUT_U8S8;
volatile int16_t OUT_LOC;
volatile uint16_t OUT_LOC_NEG;

/* BUG-9 da Sirius32 (01/10/2026, ver docs/BUGS_C167CC.md lá): o cast
   explícito `(int8_t)` de um `uint8_t` não estendia o sinal - o otimizador
   tratava o cast byte->byte como cópia pura e o apagava, então
   `(int8_t)d > 0` comparava o byte zero-estendido e dava verdadeiro pra
   d = 0x80..0xFF. Cobre também os casts vizinhos: `(int16_t)(int8_t)u8`,
   `(int8_t)` de `uint16_t`, `(uint8_t)` de valor negativo (int16_t e
   int8_t) e atribuição a uma local `int8_t` lida depois. */
void sign_cast_global(void)
{
    int8_t loc;

    if ((int8_t)D > 0) {
        OUT_GT = 1;
    } else {
        OUT_GT = 0;
    }
    if ((int8_t)D < 0) {
        OUT_LT = 1;
    } else {
        OUT_LT = 0;
    }
    OUT_WIDE = (int16_t)(int8_t)D;
    OUT_FROM_W = (int8_t)W;
    OUT_U8 = (uint8_t)S16;
    OUT_U8S8 = (uint8_t)S8;
    loc = D;
    OUT_LOC = loc;
    if (loc < 0) {
        OUT_LOC_NEG = 1;
    } else {
        OUT_LOC_NEG = 0;
    }
}
