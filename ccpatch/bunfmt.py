"""
Reader/writer for the Bun standalone-executable container.

A Bun single-file executable is a normal platform executable (Mach-O, ELF or
PE) with a blob appended to it:

    [ executable ][ blob ][ 32-byte Offsets ][ "\\n---- Bun! ----\\n" ][ signature? ]

The blob holds module contents (source, and optionally a JSC bytecode cache)
followed by a module table. Every pointer inside it is a (offset, length) pair
relative to the *start of the blob*, so the blob can be located from the end of
the file and read without understanding the executable format at all. That is
why one implementation covers macOS, Linux and Windows.

Module 0 of a Claude Code build is the bundled `cli.js`.
"""

import struct

MAGIC = b"\n---- Bun! ----\n"
ENTRY_SIZE = 52  # 13 x uint32
OFFSETS_FMT = "<QIIIIII"  # byte_count, modules.offset, modules.length, ...
OFFSETS_SIZE = struct.calcsize(OFFSETS_FMT)

# Field indices within a 13-uint32 module-table entry.
NAME_OFF, NAME_LEN = 0, 1
CONTENTS_OFF, CONTENTS_LEN = 2, 3
BYTECODE_OFF, BYTECODE_LEN = 6, 7


class NotABunExecutable(Exception):
    pass


class BunExecutable:
    def __init__(self, data: bytearray):
        self.data = data

        magic = data.rfind(MAGIC)
        if magic < 0:
            raise NotABunExecutable("no Bun trailer found")
        off = magic - OFFSETS_SIZE
        if off < 0:
            raise NotABunExecutable("truncated Bun trailer")

        byte_count, mod_off, mod_len = struct.unpack(OFFSETS_FMT, data[off:magic])[:3]
        base = off - byte_count
        if not 0 <= base < off:
            raise NotABunExecutable(f"implausible blob base {base}")
        if mod_len == 0 or mod_len % ENTRY_SIZE:
            raise NotABunExecutable(
                f"module table length {mod_len} is not a multiple of {ENTRY_SIZE}"
            )

        self.magic_offset = magic
        self.base = base
        self.table_offset = base + mod_off
        self.count = mod_len // ENTRY_SIZE

    # -- entry access ------------------------------------------------------

    def _entry_at(self, i):
        p = self.table_offset + i * ENTRY_SIZE
        return p, list(struct.unpack("<13I", self.data[p : p + ENTRY_SIZE]))

    def _write_entry(self, i, fields):
        p = self.table_offset + i * ENTRY_SIZE
        self.data[p : p + ENTRY_SIZE] = struct.pack("<13I", *fields)

    def _slice(self, offset, length):
        start = self.base + offset
        return self.data[start : start + length]

    def name(self, i):
        _, f = self._entry_at(i)
        return bytes(self._slice(f[NAME_OFF], f[NAME_LEN])).decode("utf-8", "replace")

    def contents(self, i):
        _, f = self._entry_at(i)
        return bytes(self._slice(f[CONTENTS_OFF], f[CONTENTS_LEN]))

    def describe(self, i):
        _, f = self._entry_at(i)
        return {
            "name": self.name(i),
            "contents_offset": f[CONTENTS_OFF],
            "contents_length": f[CONTENTS_LEN],
            "bytecode_offset": f[BYTECODE_OFF],
            "bytecode_length": f[BYTECODE_LEN],
        }

    def find_entrypoint(self):
        """Index of the bundled cli.js. Module 0 in every build seen so far."""
        for i in range(self.count):
            if self.name(i).endswith("cli.js"):
                return i
        raise NotABunExecutable("no cli.js module in the module table")

    # -- rewriting ---------------------------------------------------------

    def replace_contents_in_place(self, i, new_contents: bytes):
        """
        Swap a module's source without changing the file length.

        Claude Code ships module 0 with both a JS source and a much larger JSC
        bytecode cache, and Bun runs the bytecode in preference to the source --
        so editing the source alone is a no-op. The patched source is written
        over the bytecode region instead, the module's `contents` pointer is
        repointed at it, and its `bytecode` pointer is zeroed so Bun parses the
        source.

        Nothing after the region moves, so every other module's offsets stay
        valid; the only structural edit is 16 bytes in one table entry.

        Costs ~0.2s of extra startup (parsing 20+ MB of JS instead of loading
        bytecode). Returns the number of spare bytes left in the region.
        """
        p, f = self._entry_at(i)
        bc_off, bc_len = f[BYTECODE_OFF], f[BYTECODE_LEN]

        if bc_len == 0:
            raise NotABunExecutable(
                "module has no bytecode region to reuse; this build layout is "
                "not supported by the in-place rewriter"
            )
        # A JSC code cache is never zero-filled. If it is, we have mis-parsed
        # the table and are about to corrupt something else.
        if bytes(self._slice(bc_off, 4)) == b"\0\0\0\0":
            raise NotABunExecutable(
                "bytecode region does not look like a cache blob; aborting "
                "rather than overwriting an unknown part of the file"
            )
        if len(new_contents) > bc_len:
            raise NotABunExecutable(
                f"patched source ({len(new_contents)} bytes) exceeds the "
                f"reusable region ({bc_len} bytes)"
            )

        start = self.base + bc_off
        self.data[start : start + len(new_contents)] = new_contents
        # Blank the tail so a stray read lands on valid (empty) JS rather than
        # bytecode garbage.
        slack = bc_len - len(new_contents)
        self.data[start + len(new_contents) : start + bc_len] = b"\n" * slack

        f[CONTENTS_OFF], f[CONTENTS_LEN] = bc_off, len(new_contents)
        f[BYTECODE_OFF], f[BYTECODE_LEN] = 0, 0
        self._write_entry(i, f)
        return slack


def load(path) -> BunExecutable:
    with open(path, "rb") as fh:
        return BunExecutable(bytearray(fh.read()))


def looks_like_claude_binary(path) -> bool:
    """Cheap check: does this file end with a Bun trailer and contain a cli.js?"""
    try:
        exe = load(path)
        exe.find_entrypoint()
        return True
    except Exception:
        return False
