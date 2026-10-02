/* BUG-11 da Sirius32: comparação de uint32_t/int32_t com as DUAS palavras.
   Cada saída é 1 bit por operador: LT=1, GT=2, LE=4, GE=8, EQ=16, NE=32.
   OUT_U compara como uint32_t, OUT_S como int32_t, OUT_K contra constantes
   de 32 bits, OUT_C testa `if (a - b)` e `!a` (valor de 32 bits como
   condição: as duas palavras). ==/!= saem como dois CMP de palavra, sem
   depender do flag Z encadeado do SUBC. */
uint16_t AHI;
uint16_t ALO;
uint16_t BHI;
uint16_t BLO;
uint32_t A;
uint32_t B;
int32_t SA;
int32_t SB;
uint16_t OUT_U;
uint16_t OUT_S;
uint16_t OUT_K;
uint16_t OUT_C;
void wide32_cmp_global(void) {
    uint16_t r;
    A = ((uint32_t)AHI << 16) | ALO;
    B = ((uint32_t)BHI << 16) | BLO;
    SA = ((uint32_t)AHI << 16) | ALO;
    SB = ((uint32_t)BHI << 16) | BLO;

    r = 0;
    if (A < B) { r = r | 1; }
    if (A > B) { r = r | 2; }
    if (A <= B) { r = r | 4; }
    if (A >= B) { r = r | 8; }
    if (A == B) { r = r | 16; }
    if (A != B) { r = r | 32; }
    OUT_U = r;

    r = 0;
    if (SA < SB) { r = r | 1; }
    if (SA > SB) { r = r | 2; }
    if (SA <= SB) { r = r | 4; }
    if (SA >= SB) { r = r | 8; }
    if (SA == SB) { r = r | 16; }
    if (SA != SB) { r = r | 32; }
    OUT_S = r;

    r = 0;
    if (A < 0x00010000) { r = r | 1; }
    if (A > 0xFFFF) { r = r | 2; }
    if (A == 0x00010000) { r = r | 16; }
    if (SA < -32768) { r = r | 4; }
    if (SA > 32767) { r = r | 8; }
    OUT_K = r;

    r = 0;
    if (A - B) { r = r | 1; }
    if (!A) { r = r | 2; }
    if (A && B) { r = r | 4; }
    OUT_C = r;
}
