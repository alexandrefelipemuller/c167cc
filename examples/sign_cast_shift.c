/* BUG-9 e BUG-16 da Sirius32 (01/10/2026): casos mínimos com PARÂMETRO (o
   harness do simulador não passa parâmetros - a execução é coberta por
   sim-sign_cast_global/sim-ashr_global; aqui fica pinado o assembly):
   bug9  - `(int8_t)` de `uint8_t` tem que estender o sinal (SHL #8/ASHR #8);
   bug16/bug16v/s8 - `>>` com sinal é ASHR (contagem constante e variável,
   parâmetro e local, int16_t e int8_t); u16 - sem sinal continua SHR, mesmo
   com a contagem `int16_t`. */
uint16_t bug9(uint8_t d)
{
    if ((int8_t)d > 0) {
        return 1;
    }
    return 0;
}
int16_t bug16(int16_t x)
{
    return x >> 6;
}
int16_t bug16v(int16_t x, uint16_t n)
{
    int16_t s = x;
    return s >> n;
}
uint16_t u16(uint16_t x, int16_t n)
{
    return (x >> 3) + (x >> n);
}
int16_t s8(int8_t x)
{
    return (x >> 1) >> 1;
}
