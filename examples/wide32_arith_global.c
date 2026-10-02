/* BUG-11/BUG-10/BUG-8 da Sirius32: soma, subtração, negação, ternário e
   constante de 32 bits de verdade (ADD/ADDC, SUB/SUBC, as duas palavras
   gravadas). Entradas e saídas em metades de 16 bits porque o harness só
   semeia/compara palavras. */
uint16_t AHI;
uint16_t ALO;
uint16_t BHI;
uint16_t BLO;
uint16_t X;
uint16_t NEG;
uint32_t A;
uint32_t B;
uint32_t R;
uint16_t SUM_HI;
uint16_t SUM_LO;
uint16_t DIF_HI;
uint16_t DIF_LO;
uint16_t ADX_HI;
uint16_t ADX_LO;
uint16_t TER_HI;
uint16_t TER_LO;
uint16_t K_HI;
uint16_t K_LO;
uint16_t INC_HI;
uint16_t INC_LO;
uint16_t BIT_HI;
uint16_t BIT_LO;
uint16_t SHL_HI;
uint16_t SHL_LO;
uint16_t SHR_HI;
uint16_t SHR_LO;
void wide32_arith_global(void) {
    A = ((uint32_t)AHI << 16) | ALO;
    B = ((uint32_t)BHI << 16) | BLO;

    /* a + b: vai-um da palavra baixa pra alta */
    R = A + B;
    SUM_HI = (uint16_t)(R >> 16);
    SUM_LO = (uint16_t)R;

    /* a - b: empresta da palavra alta */
    R = A - B;
    DIF_HI = (uint16_t)(R >> 16);
    DIF_LO = (uint16_t)R;

    /* BUG-11 (bug11_add): a + (uint32_t)x */
    uint32_t s = A + (uint32_t)X;
    ADX_HI = (uint16_t)(s >> 16);
    ADX_LO = (uint16_t)s;

    /* BUG-10: ternário de 32 bits com negação */
    uint32_t m = NEG ? (uint32_t)(0 - A) : A;
    TER_HI = (uint16_t)(m >> 16);
    TER_LO = (uint16_t)m;

    /* BUG-8: constante de 32 bits (250000 = 0x0003D090) somada a x */
    uint32_t k = 0x0003D090;
    k = k + X;
    K_HI = (uint16_t)(k >> 16);
    K_LO = (uint16_t)k;

    /* ++ e += em 32 bits */
    R = A;
    R++;
    R += 0x10000;
    INC_HI = (uint16_t)(R >> 16);
    INC_LO = (uint16_t)R;

    /* &, |, ^ palavra por palavra */
    R = (A & B) | (A ^ 0x00FF00FF);
    BIT_HI = (uint16_t)(R >> 16);
    BIT_LO = (uint16_t)R;

    /* deslocamentos de 32 bits por constante */
    R = A << 4;
    SHL_HI = (uint16_t)(R >> 16);
    SHL_LO = (uint16_t)R;
    R = (A + B) >> 4;
    SHR_HI = (uint16_t)(R >> 16);
    SHR_LO = (uint16_t)R;
}
