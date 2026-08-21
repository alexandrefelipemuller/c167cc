uint16_t max_vetor(uint16_t *v, uint16_t n)
{
    uint16_t maior = v[0];
    uint16_t i;
    for (i = 1; i < n; i++) {
        if (v[i] > maior)
            maior = v[i];
    }
    return maior;
}
