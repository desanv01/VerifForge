OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 0x10000 }
SECTIONS {
    . = ORIGIN(RAM);
    .text : { KEEP(*(.text.start)) *(.text .text.*) } > RAM
    .rodata : ALIGN(4) { *(.rodata .rodata.*) } > RAM
    .data : ALIGN(4) { *(.data .data.*) *(.sdata .sdata.*) } > RAM
    .bss (NOLOAD) : ALIGN(4) {
        __bss_start = .; *(.bss .bss.*) *(.sbss .sbss.*) *(COMMON)
        . = ALIGN(4); __bss_end = .;
    } > RAM
    __image_end = .;
    ASSERT(__image_end <= 0x8000E000, "firmware overlaps reserved stack space")
    ASSERT(_start == 0x80000000, "wrong firmware entry")
    /DISCARD/ : { *(.eh_frame*) *(.comment*) }
}
