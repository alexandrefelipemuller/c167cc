#include "c167cc/regalloc.h"
#include <stdlib.h>
#include <string.h>

typedef struct { int start, end; int vreg; int active_slot; } Interval;

static void touch(int *first, int *last, int vreg, int idx) {
    if (vreg < 0) return;
    if (first[vreg] < 0) first[vreg] = idx;
    if (idx > last[vreg]) last[vreg] = idx;
}

RegAllocResult *regalloc_run(IrFunc *fn, int base_spill_offset) {
    int n = fn->nvregs + 1;
    RegAllocResult *res = calloc(1, sizeof(RegAllocResult));
    res->nvregs = n;
    res->reg = malloc(sizeof(int) * n);
    res->spilled = calloc(n, sizeof(int));
    res->spill_offset = calloc(n, sizeof(int));
    for (int i = 0; i < n; i++) res->reg[i] = -1;

    int *first = malloc(sizeof(int) * n);
    int *last = malloc(sizeof(int) * n);
    for (int i = 0; i < n; i++) { first[i] = -1; last[i] = -1; }

    int idx = 0;
    for (IrInst *i = fn->head; i; i = i->next, idx++) {
        if (i->dst >= 0) touch(first, last, i->dst, idx);
        if (i->a >= 0) touch(first, last, i->a, idx);
        if (i->b >= 0) touch(first, last, i->b, idx);
        for (int k = 0; k < i->nargs; k++) touch(first, last, i->args[k], idx);
    }

    /* order vregs by increasing start point (already increasing by id in practice) */
    int *order = malloc(sizeof(int) * n);
    int nlive = 0;
    for (int v = 0; v < n; v++) if (first[v] >= 0) order[nlive++] = v;
    for (int i = 1; i < nlive; i++) {
        int key = order[i], j = i - 1;
        while (j >= 0 && first[order[j]] > first[key]) { order[j+1] = order[j]; j--; }
        order[j+1] = key;
    }

    Interval *active = malloc(sizeof(Interval) * c167_temp_pool_count);
    int nactive = 0;
    int spill_cursor = base_spill_offset;

    for (int oi = 0; oi < nlive; oi++) {
        int v = order[oi];
        /* expire old intervals */
        int w = 0;
        for (int k = 0; k < nactive; k++) {
            if (active[k].end < first[v]) continue;
            active[w++] = active[k];
        }
        nactive = w;

        if (nactive < c167_temp_pool_count) {
            /* find a free register in the pool */
            int used_mask[64] = {0};
            for (int k = 0; k < nactive; k++) used_mask[res->reg[active[k].vreg]] = 1;
            int chosen = -1;
            for (int p = 0; p < c167_temp_pool_count; p++) {
                if (!used_mask[c167_temp_pool[p]]) { chosen = c167_temp_pool[p]; break; }
            }
            res->reg[v] = chosen;
            Interval iv = { first[v], last[v], v, 0 };
            active[nactive++] = iv;
        } else {
            /* spill the active interval with the furthest end, or the new one */
            int spill_k = -1, furthest = last[v];
            for (int k = 0; k < nactive; k++) {
                if (active[k].end > furthest) { furthest = active[k].end; spill_k = k; }
            }
            if (spill_k >= 0) {
                int victim = active[spill_k].vreg;
                res->spilled[victim] = 1;
                res->spill_offset[victim] = spill_cursor; spill_cursor += 2;
                res->reg[v] = res->reg[victim];
                active[spill_k].vreg = v;
                active[spill_k].end = last[v];
            } else {
                res->spilled[v] = 1;
                res->spill_offset[v] = spill_cursor; spill_cursor += 2;
            }
        }
    }

    res->spill_area_size = spill_cursor - base_spill_offset;
    free(first); free(last); free(order); free(active);
    return res;
}

void regalloc_free(RegAllocResult *r) {
    if (!r) return;
    free(r->reg); free(r->spilled); free(r->spill_offset); free(r);
}
