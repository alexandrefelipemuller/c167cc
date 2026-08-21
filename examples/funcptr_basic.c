int16_t add(int16_t a, int16_t b)
{
    return a + b;
}

int16_t sub(int16_t a, int16_t b)
{
    return a - b;
}

int16_t apply(int16_t (*op)(int16_t, int16_t), int16_t x, int16_t y)
{
    return op(x, y);
}

int16_t run(void)
{
    int16_t (*fp)(int16_t, int16_t) = add;
    int16_t r1 = apply(fp, 3, 4);
    int16_t r2 = apply(&sub, 10, 4);
    int16_t r3 = (*fp)(1, 2);
    return r1 + r2 + r3;
}
