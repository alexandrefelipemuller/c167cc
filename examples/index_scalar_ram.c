/* BUG-1 (Sirius32, corrigido 24/09/2026 - ver docs/limitations.md):
   indexar um @ram declarado ESCALAR (`calib[i]` com `uint8_t calib;`)
   usava o VALOR do escalar como ponteiro e assumia elemento de 16 bits
   (índice *2, MOV word). O correto é tratar o escalar como base de uma
   tabela do próprio tipo, igual a declarar `x[N]`: uint8_t -> índice *1 e
   MOVB; uint16_t -> índice *2 e MOV. Cobre leitura e escrita dos dois. */
@ram(0x1356) volatile uint8_t calib_1356;
@ram(0x1358) volatile uint16_t tab_w_1358;
@ram(0xE0B3) volatile uint8_t tab[4];
@ram(0xE09F) volatile uint8_t idx;

uint8_t le_byte(void) { return calib_1356[tab[idx]]; }
void escreve_byte(uint8_t v) { calib_1356[idx] = v; }
uint16_t le_word(void) { return tab_w_1358[idx]; }
void escreve_word(uint16_t v) { tab_w_1358[idx] = v; }
