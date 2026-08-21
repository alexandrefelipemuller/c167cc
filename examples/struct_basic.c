struct Point {
    int16_t x;
    int16_t y;
};

int16_t sum_point(struct Point *p)
{
    return p->x + p->y;
}

int16_t manhattan(struct Point *a, struct Point *b)
{
    struct Point d;
    d.x = a->x - b->x;
    d.y = a->y - b->y;
    return sum_point(&d);
}
