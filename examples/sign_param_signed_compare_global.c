volatile int8_t IN_S8;
volatile uint16_t OUT;

/* Achado 07/09/2026, auditoria pos regalloc-fix (Sirius32 project,
   docs/AUDITORIA_REGALLOC_FIX.md secao 2): uma variavel (parametro OU
   local OU, como aqui, global) declarada `int8_t` e comparada com sinal
   (`< 0`) dava sempre falso, mesmo com valor negativo de verdade -
   IR_LOAD_SYM sempre zero-estendia o byte lido (AND #0x00FF) antes da
   comparacao rodar, sem olhar se o tipo era assinado. Caso real
   reproduzido: biblioteca_aritmetica_soma_saturada_byte_delta_signed_3b7fe
   (Sirius32 core/aritmetica/), com um parametro `int8_t delta` e
   `if (delta < 0)`; o workaround em producao evitava o parametro int8_t
   inteiramente usando um uint16_t sign-extended manualmente pelo
   chamador + teste de bit (`& 0x0080`). Este teste usa uma global (o
   harness toy do simulador nao suporta CALLR/parametros de verdade -
   ver docs/limitations.md - mas o bug esta no LOAD do simbolo, que e o
   mesmo codigo pra SYM_LOCAL/SYM_PARAM/global nomeada) pra travar a
   regressao sem depender de chamada de funcao.
   IN_S8 = 0x8C (-116) deve dar OUT=1 (ramo negativo). */
void sign_param_signed_compare_global(void)
{
    if (IN_S8 < 0) {
        OUT = 1;
    } else {
        OUT = 0;
    }
}
