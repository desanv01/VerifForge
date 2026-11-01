/* Curated, not yet compiled. Frozen RV32I startup; ELF loader supplies .data. */
.option norvc
.option norelax
.section .text.start,"ax",@progbits
.globl _start
_start:
    lui sp, 0x80010
    addi sp, sp, -272             /* 0x8000fef0 */
    lui t0, %hi(__bss_start)
    addi t0, t0, %lo(__bss_start)
    lui t1, %hi(__bss_end)
    addi t1, t1, %lo(__bss_end)
1:
    bgeu t0, t1, 2f
    sw zero, 0(t0)
    addi t0, t0, 4
    jal zero, 1b
2:
    call fw_test
    lui t0, 0x80010
    addi t0, t0, -256             /* completion mailbox 0x8000ff00 */
    sw a0, 0(t0)                  /* 1 pass, 2 firmware self-check failed */
3:
    jal zero, 3b
