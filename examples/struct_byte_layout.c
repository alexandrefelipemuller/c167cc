/* BUG-13 da Sirius32 (01/10/2026 - ver docs/limitations.md): layout de
   struct/union com membros de 1 byte, como no C166 real - bytes
   consecutivos empacotam, membro de 16/32 bits (e ponteiro) alinha em
   endereço par, e o tamanho total só é arredondado pra par quando existe
   algum membro word. Offsets esperados anotados em cada campo. */
struct par_bytes_t {        /* tamanho 4 */
    uint8_t a;              /* +0 */
    uint8_t b;              /* +1 */
    uint16_t c;             /* +2 */
};

struct misto_t {            /* tamanho 12 */
    uint8_t flag;           /* +0 (+1 = padding) */
    uint16_t valor;         /* +2 */
    uint8_t x;              /* +4 */
    uint8_t y;              /* +5 */
    uint8_t z;              /* +6 (+7 = padding) */
    uint32_t longo;         /* +8 */
};

struct so_bytes_t {         /* tamanho 3 (ímpar: sem membro word) */
    uint8_t r;              /* +0 */
    uint8_t g;              /* +1 */
    uint8_t b;              /* +2 */
};

struct aninhada_t {         /* tamanho 10 */
    uint8_t tag;            /* +0 */
    struct so_bytes_t cor;  /* +1..+3 (alinhamento 1) */
    struct par_bytes_t par; /* +4..+7 (alinhamento 2) */
    uint8_t tab[2];         /* +8, +9 */
};

union reg_t {               /* tamanho 4 */
    uint16_t w;             /* +0 */
    uint8_t lo;             /* +0 */
    struct so_bytes_t rgb;  /* +0 (3 bytes -> união arredonda pra 4) */
};

struct par_bytes_t g_par;                       /* DS 4 */
struct misto_t g_misto;                         /* DS 12 */
struct so_bytes_t g_cores[3];                   /* DS 9 */
struct aninhada_t g_aninhada;                   /* DS 10 */
union reg_t g_reg;                              /* DS 4 */
@ram(0xE100) volatile struct par_bytes_t ram_par;
@ram(0xE200) volatile struct misto_t ram_tab[4];
struct par_bytes_t g_init = { 0x11, 0x22, 0x4433 };            /* DW 0x2211,0x4433 */
struct misto_t g_init_misto = { 1, 0x0302, 4, 5, 6, 0x0A090807 };
struct so_bytes_t g_init_cores[2] = { { 1, 2, 3 }, { 4, 5, 6 } }; /* 6 bytes */

/* caso mínimo do BUG-13: b em p+1, c em p+2 */
uint16_t bug13(const struct par_bytes_t *p)
{
    return (uint16_t)(p->b + p->c);
}

/* acesso por `.` em global e em @ram */
uint16_t ponto_global(void)
{
    g_par.b = 0x12;
    g_misto.z = g_par.a;
    g_misto.valor = 0x0102;
    ram_par.b = g_misto.y;
    return ram_par.c;
}

/* struct local: a em [frame+0], b em +1, c em +2 */
uint16_t local(uint8_t v)
{
    struct par_bytes_t s;
    s.a = v;
    s.b = v;
    s.c = 0x1234;
    return (uint16_t)(s.a + s.b + s.c);
}

/* array de struct: passo 3 (só bytes) e passo 12 */
uint8_t array_de_struct(uint16_t i)
{
    g_cores[i].b = ram_tab[i].y;
    return g_cores[2].g;
}

/* struct aninhada e campo array */
uint16_t aninhada(struct aninhada_t *p, uint16_t i)
{
    p->cor.b = p->tag;
    p->tab[i] = p->par.b;
    return p->par.c;
}

/* union: todos os membros em +0 */
uint8_t uniao(union reg_t *u)
{
    u->w = 0x1234;
    u->rgb.g = u->lo;
    return u->rgb.b;
}

/* cópia de struct campo a campo respeita os offsets novos */
void copia(struct par_bytes_t *dst)
{
    struct par_bytes_t tmp;
    tmp = g_par;
    dst->c = tmp.c;
    dst->b = tmp.b;
}
