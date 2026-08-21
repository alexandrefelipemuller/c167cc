uint16_t fatorial(uint16_t n)
{
    uint16_t result = 1;
    uint16_t i;
    for (i = 2; i <= n; i++) {
        result = result * i;
    }
    return result;
}
