uint16_t calculate(uint16_t rpm, uint16_t load)
{
    if (rpm > 3000)
        return load + 10;

    return load;
}
