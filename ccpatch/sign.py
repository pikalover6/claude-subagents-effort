"""
Re-sign the rebuilt binary, where the platform cares.

macOS  - required. Editing any byte invalidates Anthropic's Developer ID
         signature, and on Apple silicon an executable with a *broken*
         signature will not launch at all (an unsigned one is tolerated, a
         corrupt one is not). We re-sign ad-hoc, copying the entitlements off
         the original so the runtime keeps the same capabilities.
Linux  - nothing to do.
Windows- the Authenticode signature is invalidated, but PE execution does not
         require a valid one. SmartScreen may warn on first run.
"""

import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class SigningError(Exception):
    pass


def required():
    return sys.platform == "darwin"


def _codesign():
    return shutil.which("codesign")


def preflight():
    """Explain why signing cannot happen, or None if we are good to go."""
    if not required():
        return None
    if not _codesign():
        return (
            "`codesign` is not installed. On macOS the patched binary must be "
            "re-signed or it will not launch. Install the Xcode command line "
            "tools with:  xcode-select --install"
        )
    return None


def entitlements_of(binary, dest):
    """Copy the original's entitlements to `dest`. False if it has none."""
    if not _codesign():
        return False
    res = subprocess.run(
        [_codesign(), "-d", "--entitlements", "-", "--xml", str(binary)],
        capture_output=True,
    )
    xml = res.stdout
    if res.returncode != 0 or not xml.lstrip().startswith(b"<"):
        return False
    Path(dest).write_bytes(xml)
    return True


def sign(binary, reference=None, log=print):
    """Ad-hoc sign `binary`, taking entitlements from `reference` if given."""
    if not required():
        if sys.platform == "win32":
            log("  signing: skipped (Windows does not require a valid signature)")
        else:
            log("  signing: not required on this platform")
        return

    problem = preflight()
    if problem:
        raise SigningError(problem)

    cmd = [_codesign(), "--force", "--sign", "-", "--options", "runtime"]
    tmp = None
    if reference:
        tmp = Path(tempfile.mkdtemp()) / "entitlements.plist"
        if entitlements_of(reference, tmp):
            cmd += ["--entitlements", str(tmp)]
            log("  signing: reusing the original's entitlements")
    cmd.append(str(binary))

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise SigningError(f"codesign failed:\n{res.stderr.strip()}")

    check = subprocess.run(
        [_codesign(), "--verify", "--verbose", str(binary)],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        raise SigningError(f"signature did not verify:\n{check.stderr.strip()}")
    log(f"  signing: ad-hoc signed and verified ({platform.machine()})")
