/* BUG-4 da Sirius32 (24/09/2026, ver docs/limitations.md): DIV/DIVU/DIVL/
   DIVLU agora montados e executados na codificação real "xB nn" (registrador
   repetido nos dois nibbles). Caminho c167cc -> c166asm -> c166sim de `/` e
   `%` em uint16_t (DIVU), int16_t (DIV, quociente truncado pra zero, resto
   com o sinal do dividendo) e uint32_t/uint16_t (DIVLU, dividendo MDH:MDL). */
uint16_t UA;
uint16_t UB;
int16_t SA;
int16_t SB;
uint16_t LHI;
uint16_t LLO;
uint32_t LA;
uint16_t LB;
uint16_t OUT_UQ;
uint16_t OUT_UR;
int16_t OUT_SQ;
int16_t OUT_SR;
uint16_t OUT_LQ;
uint16_t OUT_LR;
void div_mod_global(void) {
    OUT_UQ = UA / UB;
    OUT_UR = UA % UB;
    OUT_SQ = SA / SB;
    OUT_SR = SA % SB;
    /* dividendo de 32 bits de verdade (MDH != 0): monta LA a partir das
       duas metades, já que o harness só semeia palavras de 16 bits */
    LA = ((uint32_t)LHI << 16) | LLO;
    OUT_LQ = LA / LB;
    OUT_LR = LA % LB;
}
