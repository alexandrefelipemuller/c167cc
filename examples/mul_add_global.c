volatile uint16_t X;
volatile uint16_t OUT;

void mul_add_global(void)
{
    OUT = X * 10 + 5;
}
