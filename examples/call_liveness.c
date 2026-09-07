/* Regressão pro bug de 06/09/2026: um valor vivo ATRAVÉS de uma chamada
 * (`v` é lido de novo depois de `f(...)` retornar) não podia ser alocado
 * num registrador do pool de temporários (R0-R3/R8-R10 - ver isa.c), já
 * que a própria chamada os destrói (convenção de retorno usa R0, e a
 * função chamada usa o MESMO pool pros temporários dela). O padrão
 * `v = v + f(x, v)` é o caso mínimo: o codegen calculava `v` num
 * registrador ANTES da chamada, a chamada sobrescrevia esse registrador
 * com o retorno, e o ADD final somava o resultado consigo mesmo em vez de
 * com o `v` original - sem nenhum erro de montagem, `--dump-asm`
 * perfeitamente plausível. Ver docs/limitations.md (Fixed bugs) e
 * src/target/c167/registers/regalloc.c. */
volatile uint16_t IN_X;
volatile uint16_t IN_V;
volatile uint16_t OUT;

uint16_t f(uint16_t x, uint16_t v)
{
    return (x + v) ^ 0x1234;
}

void call_liveness(void)
{
    uint16_t v;
    v = IN_V;
    v = v + f(IN_X, v);
    OUT = v;
}
