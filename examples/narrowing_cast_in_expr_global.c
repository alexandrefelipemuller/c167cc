volatile uint16_t A;
volatile uint16_t B;
volatile uint16_t OUT;

/* Achado 07/09/2026, promovendo `atualiza_acumulador_carga_byte` na
   Sirius32 (`core/motor_geral/atualiza_acumulador_carga_amostras.c`):
   um cast de ESTREITAMENTO (`uint16_t`->`uint8_t`) usado dentro de uma
   expressão maior, não como store final direto (`uint8_t x = expr;`,
   que já funcionava), perdia o truncamento por completo - o valor de
   16 bits INTEIRO do operando era usado na conta, como se o `(uint8_t)`
   nunca tivesse existido. B=0x1FF (511) só passa neste teste se o
   truncamento de fato acontecer: `(uint8_t)B` == 0xFF (255), então
   `A + B - (uint8_t)B` == 10 + 511 - 255 == 266. Com o bug, o cast virava
   um no-op (tanto no otimizador - `cast_is_pure_copy` tratava qualquer
   estreitamento como cópia pura e aliasava o vreg do cast pro vreg de
   origem sem truncar - quanto no codegen - IR_UNOP/OP_ASSIGN não emitia
   nenhum AND #0x00FF/sign-extend pro caso de destino de 1 byte), dando
   `A + B - B == A == 10`. */
void narrowing_cast_in_expr_global(void)
{
    OUT = A + B - (uint8_t)B;
}
