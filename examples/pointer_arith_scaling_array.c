uint16_t tabela[8];
uint16_t le_segundo_elemento(void) {
    const uint16_t *p = tabela + 1;
    return *p;
}
