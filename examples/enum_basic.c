enum Color {
    RED,
    GREEN,
    BLUE = 5,
    YELLOW
};

uint16_t pick(uint16_t which)
{
    if (which == RED) {
        return RED;
    }
    return YELLOW;
}
