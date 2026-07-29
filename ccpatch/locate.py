"""
Find the installed Claude Code binary, on any platform.

Rather than trusting install paths -- which differ by platform, installer and
release -- every candidate is confirmed by actually parsing it: if a file ends
with a Bun trailer whose module table contains a `cli.js`, it is a Claude Code
build we can patch, and if it does not, no amount of being in the right
directory helps.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .bunfmt import looks_like_claude_binary

VERSION_RE = re.compile(r"\b(\d+)\.(\d+)\.(\d+)\b")


def _home_roots():
    home = Path.home()
    roots = [
        home / ".local/share/claude/versions",
        home / ".claude/local",
        home / ".claude/versions",
    ]
    if sys.platform == "win32":
        for var in ("LOCALAPPDATA", "APPDATA", "ProgramFiles"):
            val = os.environ.get(var)
            if val:
                roots += [
                    Path(val) / "claude/versions",
                    Path(val) / "Programs/claude",
                    Path(val) / "claude",
                ]
    else:
        roots += [
            Path("/usr/local/lib/claude/versions"),
            Path("/opt/claude/versions"),
        ]
    return roots


def _from_path_entry():
    """Resolve `claude` on PATH. It is usually a symlink or a small wrapper."""
    found = shutil.which("claude")
    if not found:
        return []
    real = Path(found).resolve()
    out = [real]
    # A launcher script rather than the binary: mine it for absolute paths.
    try:
        if real.stat().st_size < 1_000_000:
            text = real.read_text("utf-8", "replace")
            for hit in re.findall(r'["\']?(/[^\s"\':]+|[A-Za-z]:\\[^\s"\']+)', text):
                out.append(Path(hit))
    except OSError:
        pass
    return out


def _version_key(path):
    m = VERSION_RE.search(path.name)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def candidates():
    seen, out = set(), []

    def add(p):
        try:
            p = Path(p)
            if p.is_file() and p not in seen:
                seen.add(p)
                out.append(p)
        except OSError:
            pass

    env = os.environ.get("CLAUDE_CODE_BINARY")
    if env:
        add(env)
    for p in _from_path_entry():
        add(p)
    for root in _home_roots():
        try:
            entries = sorted(root.iterdir(), key=_version_key, reverse=True)
        except OSError:
            continue
        for p in entries:
            add(p)
    return out


def find_all():
    """Every candidate that really is a patchable Claude Code build."""
    return [p for p in candidates() if looks_like_claude_binary(p)]


def find():
    """Best candidate, or None. Prefers the highest version number."""
    hits = find_all()
    if not hits:
        return None
    # PATH and $CLAUDE_CODE_BINARY come first and win ties; otherwise newest.
    return max(hits, key=lambda p: (_version_key(p), -hits.index(p)))


def version_of(binary):
    """`claude --version`, or None if it will not run here."""
    try:
        res = subprocess.run(
            [str(binary), "--version"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    m = VERSION_RE.search(res.stdout or res.stderr or "")
    return m.group(0) if m else None
