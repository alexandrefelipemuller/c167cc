/* BUG-2 (Sirius32, investigado 24/09/2026 - ver docs/limitations.md):
   suspeita de que `a || b || c` (3+ termos) pulava direto pro resultado
   falso quando `a` e `b` eram falsos, sem testar `c`. NÃO se confirmou: o
   `||` interno materializa 0/1 e o `||` externo testa esse valor e cai no
   teste de `c` (`.L*_or_rhs_*`). Este golden fixa o assembly desse fluxo
   (curto-circuito, ordem de avaliação, cada operando volatile lido no
   máximo uma vez) para `||`/`&&` de 3 termos, mistura `||`/`&&`,
   associações explícitas e uso fora de `if` (atribuição e `while`).
   A prova de execução das 8 combinações V/F fica nos testes
   `sim-logic_chain3_global_*` (examples/logic_chain3_global.c). */
@ram(0xE7F8) volatile uint8_t a;
@ram(0xE09A) volatile uint8_t b;
@ram(0xE194) volatile uint8_t c;
@ram(0xE0D8) volatile uint8_t d;

uint8_t or3(void)
{
    uint8_t r = 0;
    if (a >= 0x10 || b >= 0x20 || c >= 0x30) { r = 1; }
    return r;
}

uint8_t and3(void)
{
    uint8_t r = 0;
    if (a >= 0x10 && b >= 0x20 && c >= 0x30) { r = 1; }
    return r;
}

uint16_t mistura(void)
{
    return a >= 0x10 || b >= 0x20 && c >= 0x30;
}

uint16_t associacoes(void)
{
    uint16_t r = (a >= 0x10 || b >= 0x20) || c >= 0x30;
    r = r + (a >= 0x10 || (b >= 0x20 || c >= 0x30));
    return r;
}

uint16_t or4_atribuicao(void)
{
    uint16_t r = a >= 0x10 || b >= 0x20 || c >= 0x30 || d >= 0x40;
    return r;
}

uint16_t or3_while(void)
{
    uint16_t n = 0;
    while (a >= 0x10 || b >= 0x20 || c >= 0x30) { n = n + 1; }
    return n;
}
