"""Bounded deterministic artifact parsing. Never invokes a compiler or executes input."""
import ast
import hashlib
import json
import re
import struct
from .contracts import AddressFact, ElfFacts, InstructionFact, LoadSegment, MmioFacts, Provenance

MAX_BYTES = 1_048_576


def sha(data):
    return hashlib.sha256(data).hexdigest()


def bounded(data):
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError("artifact must be bytes within 1 MiB")
    return data


def provenance(data, source, location, origin):
    return Provenance(source=source, source_sha256=sha(bounded(data)), location=location, origin=origin)


def _without_comments(text):
    """Erase comments while preserving source line positions and quoted strings."""
    pattern = r'"(?:\\[^\r\n]|[^"\\\r\n])*"|/\*[\s\S]*?\*/|//[^\r\n]*'
    def replace(match):
        value = match.group()
        if value.startswith('"'): return value
        return "".join(char if char in "\r\n" else " " for char in value)
    cleaned = re.sub(pattern, replace, text)
    # This narrow numeric subset does not admit comment delimiters in strings,
    # unclosed comments, or a dangling closing delimiter. Never guess recovery.
    if "/*" in cleaned or "*/" in cleaned: raise ValueError("unsupported or unterminated comment syntax")
    return cleaned


def _integer(expression, known):
    # A tiny numeric grammar, not Python eval or a C preprocessor.
    expression = re.sub(r"\b(0[xX][0-9a-fA-F]+|[0-9]+)(?:[uU](?:[lL]{1,2})?|[lL]{1,2}[uU]?)\b", r"\1", expression)
    tree = ast.parse(expression.strip(), mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 64:
        raise ValueError("numeric expression too complex")
    def value(node):
        if isinstance(node, ast.Constant) and type(node.value) is int: return node.value
        if isinstance(node, ast.Name) and node.id in known: return known[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            a, b = value(node.left), value(node.right)
            return a + b if isinstance(node.op, ast.Add) else a - b
        raise ValueError("unsupported numeric expression")
    result = value(tree.body)
    if not 0 <= result < 2**32: raise ValueError("address outside uint32")
    return result


def extract_header(data: bytes, source: str, macros: tuple[str, ...], *, origin="imported_artifact"):
    text = _without_comments(bounded(data).decode("utf-8"))
    if len(macros) > 64 or len(set(macros)) != len(macros): raise ValueError("macro selection must be unique and bounded")
    known, locations = {}, {}
    # No #undef/include/conditional/continuation can silently change selected facts.
    if "\\" in text: raise ValueError("continued or escaped header syntax unsupported")
    for line, raw in enumerate(text.splitlines(), 1):
        if not raw.strip(): continue
        match = re.fullmatch(r"\s*#define\s+([A-Za-z_]\w*)\s+(.+?)\s*", raw)
        if not match: raise ValueError("unsupported header/preprocessor syntax at line " + str(line))
        name, expression = match.groups()
        if name in locations: raise ValueError("duplicate macro " + name)
        locations[name] = line
        try: known[name] = _integer(expression, known)
        except (ValueError, SyntaxError, RecursionError) as exc:
            raise ValueError("unsupported numeric macro " + name) from exc
    if any(name not in known for name in macros): raise ValueError("selected header macro missing or unresolved")
    return tuple(AddressFact(name=name, value=known[name], provenance=provenance(data, source, f"line:{locations[name]}", origin)) for name in macros)


def extract_rtl_decode(data: bytes, source: str, *, selector="addr", byte_base=0, origin="imported_artifact"):
    """Only literal-address, one-line register assignments within one case(selector)."""
    text = _without_comments(bounded(data).decode("utf-8"))
    if "`" in text: raise ValueError("RTL preprocessing/macros unsupported; supply an explicit reviewed subset")
    if re.fullmatch(r"[A-Za-z_]\w*", selector) is None: raise ValueError("invalid selector")
    if type(byte_base) is not int or not 0 <= byte_base < 2**32: raise ValueError("invalid explicit byte base")
    active, seen, names, rows = False, 0, set(), []
    for line, raw in enumerate(text.splitlines(), 1):
        raw = raw.strip()
        if re.fullmatch(r"case\s*\(\s*" + selector + r"\s*\)", raw):
            if seen: raise ValueError("multiple decode cases unsupported")
            active, seen = True, 1
            continue
        if not active: continue
        if raw == "endcase": active = False; continue
        if not raw or raw == "default: ;": continue
        match = re.fullmatch(r"(8|16|32)'h([0-9a-fA-F_]+)\s*:\s*([A-Za-z_]\w*)\s*<=\s*write_data\s*;", raw)
        if not match: raise ValueError("unsupported RTL decode branch at line " + str(line))
        width, literal, name = match.groups()
        if int(literal.replace("_", ""), 16) >= 2**int(width): raise ValueError("RTL literal would truncate")
        address = byte_base + int(literal.replace("_", ""), 16)
        if name in names or any(row.value == address for row in rows): raise ValueError("ambiguous RTL decode")
        names.add(name)
        proof = provenance(data, source, f"line:{line}", origin).model_copy(update={"conditions": (f"explicit byte base={byte_base}; selector={selector}; one-line literal decode subset",)})
        rows.append(AddressFact(name=name, value=address, provenance=proof))
        if len(rows) > 64: raise ValueError("too many decode branches")
    if active or not rows: raise ValueError("missing or unterminated supported decode case")
    return tuple(rows)


def extract_elf(data: bytes, source: str, *, origin="imported_artifact"):
    bounded(data)
    if len(data) < 52 or data[:7] != b"\x7fELF\x01\x01\x01": raise ValueError("only ELF32 little-endian version 1 supported")
    kind, machine, version, entry, phoff, shoff, flags, ehsize, phsize, phcount, shsize, shcount, shstr = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    if (kind, machine, version, ehsize) != (2, 243, 1, 52): raise ValueError("only RISC-V ELF32 ET_EXEC supported")
    if phcount > 128 or (phcount and (phsize != 32 or phoff < 52 or phoff + phcount * phsize > len(data))): raise ValueError("invalid or oversized program header table")
    segments = []
    for index in range(phcount):
        typ, offset, va, pa, filesz, memsz, permissions, align = struct.unpack_from("<IIIIIIII", data, phoff + index * 32)
        if typ != 1: continue
        if filesz > memsz or offset + filesz > len(data) or va + memsz > 2**32 or pa + memsz > 2**32: raise ValueError("invalid load segment extent")
        if align > 1 and (align & (align - 1) or (va - offset) % align): raise ValueError("invalid load alignment")
        segments.append(LoadSegment(offset=offset, virtual_address=va, physical_address=pa, file_size=filesz, memory_size=memsz,
                                    flags=permissions, file_sha256=sha(data[offset:offset+filesz])))
    return ElfFacts(entry=entry, flags=flags, load_segments=tuple(segments), provenance=provenance(data, source, "bytes:0:52", origin))


def extract_disassembly(data: bytes, source: str, *, origin="imported_artifact"):
    rows, addresses = [], set()
    base = provenance(data, source, "artifact", origin)
    for line, raw in enumerate(bounded(data).decode("utf-8").splitlines(), 1):
        match = re.fullmatch(r"\s*([0-9a-fA-F]{1,8}):\s+([0-9a-fA-F]{4}|[0-9a-fA-F]{8})\s+([A-Za-z.][A-Za-z0-9_.]*)\s*(.*?)\s*", raw)
        if not match:
            if re.match(r"\s*[0-9a-fA-F]+:", raw): raise ValueError("unsupported disassembly encoding layout")
            continue
        address, encoding, mnemonic, operands = match.groups()
        address = int(address, 16)
        if address in addresses: raise ValueError("duplicate instruction address")
        addresses.add(address)
        rows.append(InstructionFact(address=address, encoding=int(encoding, 16), width_bits=len(encoding)*4,
            mnemonic=mnemonic, operands=operands, provenance=base.model_copy(update={"location": f"line:{line}"})))
        if len(rows) > 4096: raise ValueError("disassembly exceeds 4096 rows")
    if not rows: raise ValueError("no supported disassembly instructions")
    return tuple(rows)


def corroborate_instruction_bytes(elf_data: bytes, elf: ElfFacts, instructions):
    """Cross-check printed encodings against ELF load bytes, not mnemonic semantics."""
    bounded(elf_data)
    if sha(elf_data) != elf.provenance.source_sha256: raise ValueError("ELF identity changed")
    if len(instructions) > 4096: raise ValueError("too many disassembled instructions")
    for row in instructions:
        size = row.width_bits // 8
        segments = [segment for segment in elf.load_segments if segment.virtual_address <= row.address
                    and row.address + size <= segment.virtual_address + segment.file_size]
        if len(segments) != 1: raise ValueError("instruction not uniquely backed by file bytes")
        segment = segments[0]
        offset = segment.offset + row.address - segment.virtual_address
        if int.from_bytes(elf_data[offset:offset+size], "little") != row.encoding:
            raise ValueError("disassembly encoding differs from ELF bytes")
    return True


def extract_mmio(data: bytes, source: str, *, origin="imported_artifact"):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    raw = json.loads(bounded(data), object_pairs_hook=unique)
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "transactions"} or raw["schema_version"] != "firmware.mmio.v1": raise ValueError("unsupported MMIO envelope")
    facts = MmioFacts(transactions=raw["transactions"], provenance=provenance(data, source, "json:transactions", origin))
    if any(row.sequence != i for i, row in enumerate(facts.transactions)): raise ValueError("MMIO sequence must be contiguous from zero")
    return facts
