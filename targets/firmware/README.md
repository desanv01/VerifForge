# Firmware artifact fixtures

All files here are **synthetic parser fixtures**, not compiled or executed firmware. `registers.sv` is deliberately a small literal-decode source subset, not an installed MMIO peripheral or native-to-AXI bridge. `example.disasm` and `mmio.json` are handwritten text/transactions; no compiler, disassembler, core or simulator produced them. The test-only ELF byte array is constructed directly in Python.

The four-register addresses follow main's separately approved 0x40000000..0x40000010 window, but approval of that intent does not establish an implementation. The reviewed fixture contract supplies explicit header-macro/RTL-signal relationships rather than assuming equal names imply equal hardware identity. Access/reset/strobe fields are requirements, not behavior extracted by this small address parser.

See `docs/project/FIRMWARE.md` for actual prerequisites, unsupported syntax and evidence labels. No executable source, linker script, binary, build recipe or arbitrary subprocess interface is supplied in this finite source task.
