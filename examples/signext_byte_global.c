volatile uint16_t IN;
volatile uint16_t OUT;

/* Achado 05/09/2026, investigando uma divergência de 4/11 casos de teste
   numa rotina real (Sirius32 project, `research/sensores_atuadores/
   DUVIDAS.md`, "Rodada seguinte (05/09/2026)"): um cast byte->word
   ASSINADO ((int16_t)(int8_t)x) silenciosamente zero-estendia em vez de
   sign-estender. IN=200 (bit 7 setado, -56 como int8_t) só passa neste
   teste se o sinal for de fato propagado: (int16_t)(int8_t)200 == -56,
   que como uint16_t é 0xFFC8. Root cause dupla: (1) o codegen do
   IR_UNOP/OP_ASSIGN calculava o mnemônico certo (MOVBS/MOVBZ) mas
   descartava a variável sem usar (`(void)mn`), sempre emitindo um MOV
   puro; (2) o otimizador (`ir_optimize`/copy-propagation) tratava
   QUALQUER cast como alias/cópia pura pra fins de constant-fold e
   propagação, então mesmo consertando (1) o cast inteiro podia ser
   eliminado do fluxo antes de chegar no codegen. */
void signext_byte_global(void)
{
    int8_t b = (int8_t)IN;
    int16_t w = (int16_t)b;
    OUT = (uint16_t)w;
}
