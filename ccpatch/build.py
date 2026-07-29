"""
extract -> patch -> re-embed -> sign -> smoke test.

The installed Claude Code binary is opened read-only and never written to; the
result is a separate file that lives alongside it.
"""

import os
import shutil
from pathlib import Path

from . import bunfmt, patch, sign


class BuildError(Exception):
    pass


def build(source, output, log=print, keep_sources=None):
    """
    Produce a patched copy of `source` at `output`.

    `keep_sources` (a directory) writes out the original and patched cli.js for
    inspection or diffing -- useful when an anchor needs re-deriving.
    """
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise BuildError("refusing to patch the installed binary in place")

    problem = sign.preflight()
    if problem:
        raise BuildError(problem)

    log(f"  reading {source}")
    exe = bunfmt.load(source)
    index = exe.find_entrypoint()
    info = exe.describe(index)
    log(f"  module {index}: {info['name']}")
    log(f"    source   {info['contents_length']:>12,} bytes")
    log(f"    bytecode {info['bytecode_length']:>12,} bytes")

    src = exe.contents(index).decode("utf-8")
    if keep_sources:
        Path(keep_sources).mkdir(parents=True, exist_ok=True)
        (Path(keep_sources) / "cli.orig.js").write_text(src, encoding="utf-8")

    patched, report = patch.apply(
        src, lambda kind, name, detail: log(f"  {kind:7s} {name:28s} {detail}")
    )
    if keep_sources:
        (Path(keep_sources) / "cli.patched.js").write_text(patched, encoding="utf-8")
    log(f"  patched cli.js +{len(patched) - len(src)} bytes")

    before = len(exe.data)
    slack = exe.replace_contents_in_place(index, patched.encode("utf-8"))
    if len(exe.data) != before:
        raise BuildError("payload length changed; refusing to write")
    log(f"  re-embedded, {slack:,} bytes spare in the reused region")

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.name + ".partial")
    tmp.write_bytes(exe.data)
    os.chmod(tmp, 0o755)
    tmp.replace(output)
    log(f"  wrote {output} ({len(exe.data):,} bytes)")

    sign.sign(output, reference=source, log=log)
    return report


def smoke_test(binary, expect_version=None):
    """Confirm the rebuilt binary starts. Returns its reported version."""
    from .locate import version_of

    got = version_of(binary)
    if not got:
        raise BuildError(
            f"{binary} did not report a version -- the rebuild is not runnable"
        )
    if expect_version and got != expect_version:
        raise BuildError(
            f"rebuilt binary reports {got}, expected {expect_version}"
        )
    return got


def free_space_for(path, needed):
    """True if `needed` bytes will fit where `path` is going."""
    target = Path(path)
    while not target.exists():
        target = target.parent
    return shutil.disk_usage(target).free >= needed
