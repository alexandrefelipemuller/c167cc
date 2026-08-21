@ram(0x1234)
volatile uint16_t rpm;

uint16_t read_rpm(void)
{
    return rpm;
}
