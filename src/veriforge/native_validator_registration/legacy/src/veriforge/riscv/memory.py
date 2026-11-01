"""Pure native-bus RAM semantics for the future curated execution harness."""
from .profile import PLATFORM


class Ram:
    def __init__(self, program: bytes):
        if not isinstance(program, bytes) or len(program) > PLATFORM.completion-PLATFORM.ram_base:
            raise ValueError("binary exceeds curated program RAM")
        self.data = bytearray(PLATFORM.ram_bytes)
        self.data[:len(program)] = program

    def _offset(self, address):
        if type(address) is not int or address % 4 or not PLATFORM.ram_base <= address <= PLATFORM.ram_base+PLATFORM.ram_bytes-4:
            raise ValueError("unmapped or unaligned native memory request; external MMIO unsupported")
        return address-PLATFORM.ram_base

    def read_word(self, address):
        offset = self._offset(address)
        return int.from_bytes(self.data[offset:offset+4], 'little')

    def write_word(self, address, value, strobes):
        offset = self._offset(address)
        if type(value) is not int or not 0 <= value < 2**32 or type(strobes) is not int or not 0 <= strobes <= 15:
            raise ValueError("invalid native bus data/strobes")
        for lane in range(4):
            if strobes & (1 << lane):
                self.data[offset+lane] = value >> (8*lane) & 255
