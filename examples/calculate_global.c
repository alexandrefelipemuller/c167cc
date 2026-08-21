volatile uint16_t RPM;
volatile uint16_t LOAD;
volatile uint16_t OUT;

void calculate_global(void)
{
    if (RPM > 3000)
        OUT = LOAD + 10;
    else
        OUT = LOAD;
}
