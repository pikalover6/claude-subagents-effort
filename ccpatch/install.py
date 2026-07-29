"""
Put the patched build on PATH under its own name, alongside stock Claude Code.

The launcher is a small wrapper rather than a symlink for one reason: it has to
set DISABLE_AUTOUPDATER. Claude Code's updater installs into the shared
versions directory and repoints the stock `claude` launcher, so an update
triggered from the patched build would quietly overwrite the user's real
install. Setting it in the wrapper pins the patched build only -- stock
`claude` keeps updating itself normally, and no shared config is touched.
"""

import json
import os
import stat
import sys
from pathlib import Path

MANIFEST = "manifest.json"

UNIX_LAUNCHER = """\
#!/bin/sh
# {alias} -- Claude Code {version} patched for per-subagent reasoning effort.
#   https://github.com/pikalover6/claude-subagents-effort
#
# Shares ~/.claude with stock `claude`: same credentials, settings, MCP
# servers, projects and session history, so a session started under one can be
# resumed under the other.
#
# Auto-update is force-disabled here and only here. Claude Code's updater
# installs into the shared versions directory and repoints the stock `claude`
# launcher, so without this an update triggered from {alias} would overwrite
# the stock install. Stock `claude` still updates normally; {alias} stays on
# this build until you re-run the installer.

BIN="{binary}"

if [ ! -x "$BIN" ]; then
	echo "{alias}: patched binary missing at $BIN" >&2
	echo "{alias}: reinstall from https://github.com/pikalover6/claude-subagents-effort" >&2
	exit 127
fi

DISABLE_AUTOUPDATER=1
export DISABLE_AUTOUPDATER

exec "$BIN" "$@"
"""

WINDOWS_LAUNCHER = """\
@echo off
rem {alias} -- Claude Code {version} patched for per-subagent reasoning effort.
rem   https://github.com/pikalover6/claude-subagents-effort
rem
rem Shares %USERPROFILE%\\.claude with stock `claude`. Auto-update is disabled
rem here and only here, so an update triggered from {alias} cannot overwrite
rem the stock install.
setlocal
set "DISABLE_AUTOUPDATER=1"
if not exist "{binary}" (
	echo {alias}: patched binary missing at {binary} 1>&2
	exit /b 127
)
"{binary}" %*
exit /b %ERRORLEVEL%
"""


def default_bin_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "Programs" / "claude-subagents-effort"
    return Path.home() / ".local" / "bin"


def default_data_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "claude-subagents-effort"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "claude-subagents-effort"


def binary_path(data_dir, alias, version):
    name = f"{alias}-{version}"
    if sys.platform == "win32":
        name += ".exe"
    return Path(data_dir) / name


def launcher_path(bin_dir, alias):
    name = f"{alias}.cmd" if sys.platform == "win32" else alias
    return Path(bin_dir) / name


def on_path(directory):
    directory = Path(directory).expanduser().resolve()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            if Path(entry).expanduser().resolve() == directory:
                return True
        except OSError:
            continue
    return False


def path_hint(directory):
    """How to add `directory` to PATH, for whatever shell this looks like."""
    if sys.platform == "win32":
        return (
            f'setx PATH "%PATH%;{directory}"\n'
            "    (then open a new terminal)"
        )
    shell = Path(os.environ.get("SHELL", "sh")).name
    rc = {"zsh": "~/.zshrc", "bash": "~/.bashrc", "fish": "~/.config/fish/config.fish"}
    if shell == "fish":
        return f'fish_add_path {directory}'
    return f'echo \'export PATH="{directory}:$PATH"\' >> {rc.get(shell, "~/.profile")}'


def write_launcher(bin_dir, alias, binary, version):
    launcher = launcher_path(bin_dir, alias)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    template = WINDOWS_LAUNCHER if sys.platform == "win32" else UNIX_LAUNCHER
    launcher.write_text(
        template.format(alias=alias, binary=binary, version=version),
        encoding="utf-8",
    )
    if sys.platform != "win32":
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return launcher


def write_manifest(data_dir, record):
    path = Path(data_dir) / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def read_manifest(data_dir):
    try:
        return json.loads((Path(data_dir) / MANIFEST).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def uninstall(data_dir, log=print):
    """Remove a previous install. Never touches anything under ~/.claude."""
    record = read_manifest(data_dir)
    if not record:
        log(f"nothing installed under {data_dir}")
        return False
    for key in ("launcher", "binary"):
        target = Path(record.get(key, ""))
        if target.exists():
            target.unlink()
            log(f"removed {target}")
    manifest = Path(data_dir) / MANIFEST
    if manifest.exists():
        manifest.unlink()
    try:
        Path(data_dir).rmdir()
    except OSError:
        pass
    log("Your Claude Code install, settings and session history are untouched.")
    return True
