union Value {
    int16_t as_signed;
    uint16_t as_unsigned;
};

uint16_t reinterpret(union Value *v)
{
    return v->as_unsigned;
}
