/* BUG-6/BUG-7/BUG-15 da Sirius32: produto de 32 bits somado (ADDC com o
   MDH), quociente de uint32_t/uint16_t gravado numa variável uint32_t
   (palavra alta = 0, não o que já estava lá) e composição `(hi << 16) | lo`
   usada direto como dividendo (MDH:MDL + DIVLU). */
uint16_t X;
uint16_t MA;
uint16_t MB;
uint16_t NHI;
uint16_t NLO;
uint16_t D;
uint16_t B6_HI;
uint16_t B6_LO;
uint16_t B7_HI;
uint16_t B7_LO;
uint16_t B15_Q;
uint16_t B15_R;
uint16_t MW_HI;
uint16_t MW_LO;
uint16_t DM_Q;
void wide32_muldiv_global(void) {
    /* BUG-6 */
    uint32_t r = (uint32_t)X + (uint32_t)MA * (uint32_t)MB;
    B6_HI = (uint16_t)(r >> 16);
    B6_LO = (uint16_t)r;

    /* BUG-7: q começa com lixo nas duas palavras */
    uint32_t n = ((uint32_t)NHI << 16) | NLO;
    uint32_t q = 0xFFFFFFFF;
    q = n / D;
    B7_HI = (uint16_t)(q >> 16);
    B7_LO = (uint16_t)q;

    /* BUG-15 */
    B15_Q = (uint16_t)((((uint32_t)NHI << 16) | NLO) / D);
    B15_R = (uint16_t)((((uint32_t)NHI << 16) | NLO) % D);

    /* 32 x 16 -> 32 (operando com palavra alta de verdade: produto cruzado) */
    uint32_t w = n * MA;
    MW_HI = (uint16_t)(w >> 16);
    MW_LO = (uint16_t)w;

    /* (soma de 32 bits) / d: dividendo que não é variável nem produto */
    DM_Q = (uint16_t)((n + X) / D);
}
