#ifndef C167CC_REGALLOC_H
#define C167CC_REGALLOC_H

#include "c167cc/ir.h"
#include "c167cc/c167_target.h"

typedef struct {
    int nvregs;
    int *reg;          /* physical C167Reg per vreg, or -1 if spilled */
    int *spilled;       /* 1 if spilled */
    int *spill_offset;  /* frame offset for spilled vregs (bytes) */
    int spill_area_size;
} RegAllocResult;

/* Simple linear-scan register allocator over the IR instruction stream of a
 * single function. Live ranges are computed from first-def to last-use
 * instruction index; spills go to dedicated stack slots below the frame's
 * local-variable area. See docs/c167-backend.md for the algorithm summary.
 */
RegAllocResult *regalloc_run(IrFunc *fn, int base_spill_offset);
void regalloc_free(RegAllocResult *r);

#endif
