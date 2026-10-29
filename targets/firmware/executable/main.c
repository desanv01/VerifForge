/* Curated firmware source, no compiler/core execution claimed yet. */
#include "registers.h"
typedef unsigned int u32;
typedef unsigned char u8;
_Static_assert(sizeof(u32) == 4, "requires ILP32 32-bit unsigned int");
extern u32 read_status_at_reviewed_address(void);

u32 fw_test(void) {
    volatile u32 *control = (volatile u32 *)CONTROL_ADDR;
    volatile u32 *status = (volatile u32 *)STATUS_ADDR;
    volatile u32 *data = (volatile u32 *)DATA_ADDR;
    volatile u32 *scratch = (volatile u32 *)SCRATCH_ADDR;
    *control = 0x11223344u;
    if (*control != 0x11223344u) return 2;
    *status = 0x55667788u;
    /* A bad header write and read must not agree with their own wrong address. */
    if (read_status_at_reviewed_address() != 0x55667788u) return 2;
    *data = 0xAABBCCDDu;
    *(volatile u8 *)(DATA_ADDR + 1u) = 0x11u;
    if (*data != 0xAABB11DDu) return 2;
    *scratch = 0x13579BDFu;
    if (*scratch != 0x13579BDFu) return 2;
    return 1;
}
