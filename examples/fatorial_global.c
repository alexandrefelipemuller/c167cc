volatile uint16_t NUMERO;
volatile uint16_t RESULTADO;

void fatorial_global(void)
{
    uint16_t n = NUMERO;
    uint16_t result = 1;
    while (n > 1) {
        result = result * n;
        n = n - 1;
    }
    RESULTADO = result;
}
