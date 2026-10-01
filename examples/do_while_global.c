/* BUG-14 da Sirius32 (01/10/2026 - ver docs/limitations.md): prova de
   EXECUÇÃO de `do { } while ();`.
   OUT_BITS: nº de bits significativos de N (corpo roda ao menos 1 vez:
             N=0 dá 1).
   OUT_ONCE: corpo executado exatamente 1 vez com condição falsa de cara.
   OUT_SUM:  soma dos ímpares de 1..LIM usando `continue` (que vai pro
             TESTE da condição - se voltasse pro início do corpo sem
             testar, o laço não terminaria) e `break` ao passar de 9.
   OUT_NEST: do/while aninhado com while e for - 3 * (2 + 4) = 18. */
volatile uint16_t N;
volatile uint16_t LIM;
volatile uint16_t OUT_BITS;
volatile uint16_t OUT_ONCE;
volatile uint16_t OUT_SUM;
volatile uint16_t OUT_NEST;

void do_while_global(void)
{
    uint16_t n = N;
    uint16_t i = 0;
    do {
        i = i + 1;
        n = n >> 1;
    } while (n != 0);
    OUT_BITS = i;

    uint16_t once = 0;
    do once = once + 1; while (once > 100);
    OUT_ONCE = once;

    uint16_t k = 0;
    uint16_t sum = 0;
    do {
        k = k + 1;
        if (k > 9) break;
        if ((k & 1) == 0) continue;
        sum = sum + k;
    } while (k < LIM);
    OUT_SUM = sum;

    uint16_t outer = 0;
    uint16_t nest = 0;
    do {
        uint16_t w = 0;
        while (w < 2) {
            w = w + 1;
            nest = nest + 1;
        }
        for (uint16_t f = 0; f < 10; f++) {
            if (f == 4) break;
            nest = nest + 1;
        }
        outer = outer + 1;
    } while (outer < 3);
    OUT_NEST = nest;
}
