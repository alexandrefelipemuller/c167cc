/* BUG-13 da Sirius32 (01/10/2026 - ver docs/limitations.md): prova de
   EXECUÇÃO do layout de struct com membros de 1 byte. No C166 bytes
   consecutivos empacotam e só membro de 16/32 bits alinha em endereço par:
   `a` em +0, `b` em +1, `c` em +2, `d` em +4, `e` (word) em +6 (1 byte de
   padding em +5), tamanho 8. BUF é a "memória crua" - a struct é sobreposta
   nela por ponteiro, e os campos são lidos/escritos por `->`.
   Com o bug (todo membro alinhado em palavra) `b` era lido em +2, `c` em
   +4 etc. */
struct reg_bytes_t {
    uint8_t a;
    uint8_t b;
    uint16_t c;
    uint8_t d;
    uint16_t e;
};

volatile uint16_t IN;
volatile uint16_t BUF[4];
volatile uint16_t OUT_A;
volatile uint16_t OUT_B;
volatile uint16_t OUT_C;
volatile uint16_t OUT_D;
volatile uint16_t OUT_E;
volatile uint16_t OUT_W0;
volatile uint16_t OUT_W2;

void struct_byte_layout_global(void)
{
    struct reg_bytes_t *p = (struct reg_bytes_t *)BUF;
    BUF[0] = 0x2211;
    BUF[1] = IN;
    BUF[2] = 0x6655;
    BUF[3] = 0x0888;
    OUT_A = p->a;
    OUT_B = p->b;
    OUT_C = p->c;
    OUT_D = p->d;
    OUT_E = p->e;
    p->b = 0x7F;
    p->d = 0x01;
    OUT_W0 = BUF[0];
    OUT_W2 = BUF[2];
}
