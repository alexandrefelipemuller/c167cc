/* BUG-14 da Sirius32 (01/10/2026 - ver docs/limitations.md):
   `do { } while ();` - corpo antes do teste, `continue` salta pro teste
   da condição (rótulo do_cond), `break` pro fim (do_end). */

/* caso mínimo do BUG-14 */
uint16_t bug14(uint16_t n)
{
    uint16_t i = 0;
    do {
        i = i + 1;
        n = n >> 1;
    } while (n != 0);
    return i;
}

/* corpo sem chaves */
uint16_t sem_chaves(uint16_t n)
{
    do n = n - 1; while (n > 10);
    return n;
}

/* break e continue dentro do corpo */
uint16_t break_continue(uint16_t lim)
{
    uint16_t k = 0;
    uint16_t sum = 0;
    do {
        k = k + 1;
        if (k > 9) break;
        if ((k & 1) == 0) continue;
        sum = sum + k;
    } while (k < lim);
    return sum;
}

/* aninhado: do dentro de while, for e do; break/continue do laço de
   dentro não afetam o de fora */
uint16_t aninhado(uint16_t n)
{
    uint16_t total = 0;
    while (n > 0) {
        uint16_t j = 0;
        do {
            j = j + 1;
            if (j == 2) continue;
            total = total + 1;
        } while (j < 3);
        n = n - 1;
    }
    for (uint16_t f = 0; f < 4; f++) {
        do {
            total = total + f;
            do {
                total = total + 1;
                if (total > 100) break;
            } while (total & 1);
        } while (0);
        if (f == 2) continue;
    }
    return total;
}
