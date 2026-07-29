#!/bin/sh
# claude-subagents-effort -- installer entry point for macOS and Linux.
#
# This is deliberately not a `curl | sh` installer. It runs from a clone you
# already have on disk, so you can read what it does before you run it -- which
# matters more than usual for something that rebuilds your coding agent.
#
#   git clone https://github.com/pikalover6/claude-subagents-effort
#   ./claude-subagents-effort/install.sh
#
# Any argument is passed straight through to `python3 -m ccpatch`, so
# `./install.sh --help` works, as does `./install.sh --alias cc2 --yes`.

set -eu

cd "$(dirname "$0")"

PYTHON=""
for candidate in python3 python; do
	if command -v "$candidate" >/dev/null 2>&1; then
		if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
			PYTHON="$candidate"
			break
		fi
	fi
done

if [ -z "$PYTHON" ]; then
	echo "This installer needs Python 3.8 or newer, and could not find it." >&2
	echo >&2
	case "$(uname -s)" in
	Darwin) echo "  brew install python3      (or: xcode-select --install)" >&2 ;;
	*) echo "  sudo apt install python3   # or your distribution's equivalent" >&2 ;;
	esac
	exit 1
fi

exec "$PYTHON" -m ccpatch "$@"
