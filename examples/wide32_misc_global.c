/* BUG-11 da Sirius32, casos fora do caminho "variável simples sem sinal":
   lvalue de 32 bits por ponteiro (as duas palavras lidas/gravadas via
   [reg] e [reg+2]), int16_t alargado pra int32_t com extensão de sinal
   (o padrão de soma/subtração saturada de core/aritmetica/
   lote_s_saturada_diversos.c da Sirius32), produto com sinal somado e
   valor de 32 bits atravessando várias expressões vivas ao mesmo tempo. */
int16_t S1;
int16_t S2;
uint16_t U1;
uint16_t U2;
uint32_t M;
uint16_t PT_HI;
uint16_t PT_LO;
int16_t SAT_ADD;
int16_t SAT_SUB;
uint16_t SP_HI;
uint16_t SP_LO;
uint16_t MIX_HI;
uint16_t MIX_LO;
void wide32_misc_global(void) {
    /* *p += x, com p apontando pra um uint32_t */
    uint32_t *p = &M;
    *p = ((uint32_t)U1 << 16) | U2;
    *p = *p + U2;
    *p += 0x00020003;
    PT_HI = (uint16_t)(M >> 16);
    PT_LO = (uint16_t)M;

    /* soma/subtração saturada com sinal em 32 bits */
    int32_t soma = (int32_t)S1 + (int32_t)S2;
    if (soma > 32767) {
        SAT_ADD = 32767;
    } else if (soma < -32768) {
        SAT_ADD = -32768;
    } else {
        SAT_ADD = (int16_t)soma;
    }
    int32_t dif = (int32_t)S1 - (int32_t)S2;
    if (dif > 32767 || dif < -32768) {
        if (S1 >= S2) {
            SAT_SUB = 32767;
        } else {
            SAT_SUB = -32768;
        }
    } else {
        SAT_SUB = (int16_t)dif;
    }

    /* produto com sinal (MUL) somado a um int16_t alargado */
    int32_t sp = (int32_t)S1 * (int32_t)S2 + (int32_t)S1;
    SP_HI = (uint16_t)(sp >> 16);
    SP_LO = (uint16_t)sp;

    /* várias palavras vivas ao mesmo tempo */
    uint32_t a = ((uint32_t)U1 << 16) | U2;
    uint32_t b = ((uint32_t)U2 << 16) | U1;
    uint32_t mix = (a + b) - ((a ^ b) + (uint32_t)U1 * (uint32_t)U2) + (a - 0x12345);
    MIX_HI = (uint16_t)(mix >> 16);
    MIX_LO = (uint16_t)mix;
}
