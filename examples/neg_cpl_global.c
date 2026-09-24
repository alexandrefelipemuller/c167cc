uint16_t A;
uint16_t OUT_NEG;
uint16_t OUT_CPL;
void neg_cpl_global(void) {
    OUT_NEG = -A;
    OUT_CPL = ~A;
}
