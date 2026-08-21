uint16_t fatorial_rec(uint16_t n)
{
    if (n <= 1)
        return 1;
    return n * fatorial_rec(n - 1);
}
