/* Reviewed hardware address encoded independently of every C register header. */
.option norvc
.option norelax
.section .text,"ax",@progbits
.globl read_status_at_reviewed_address
read_status_at_reviewed_address:
    lui t0, 0x40000
    lw a0, 4(t0)
    ret
