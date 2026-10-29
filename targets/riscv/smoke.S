/* Curated VerifForge RV32I control. No C runtime, CSR, ecall or external MMIO. */
.option norvc
.option norelax
.section .text.init
.global _start
_start:
    lui sp, 0x80010
    addi sp, sp, -272       /* 0x8000fef0, below reserved completion region */
    lui t0, 0x80000
    addi t0, t0, 256        /* scratch RAM at 0x80000100 */
    addi t1, zero, 7
    addi t2, zero, 5
    sub t3, t1, t2
    sw t3, 0(t0)
    lw t4, 0(t0)
    bne t4, t3, fail
    addi t5, zero, -1
    slti t6, t5, 0
    addi a1, zero, 1
    beq t6, a1, finish
fail:
    addi a1, zero, 2
finish:
    lui a0, 0x80010
    addi a0, a0, -256       /* RAM completion mailbox 0x8000ff00 */
    sw a1, 0(a0)            /* producer stops after this instruction retires */
stop:
    jal zero, stop
