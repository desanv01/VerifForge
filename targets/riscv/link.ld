OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 0xff00 }
SECTIONS {
    . = ORIGIN(RAM);
    .text : { *(.text.init) *(.text .text.*) } > RAM
    .rodata : { *(.rodata .rodata.*) } > RAM
    .data : { *(.data .data.*) *(.sdata .sdata.*) } > RAM
    .bss (NOLOAD) : { *(.bss .bss.*) *(.sbss .sbss.*) *(COMMON) } > RAM
    _stack_top = ORIGIN(RAM) + LENGTH(RAM) - 16;
    ASSERT(. <= 0x80000100, "control code/data overlaps fixed scratch word")
}
