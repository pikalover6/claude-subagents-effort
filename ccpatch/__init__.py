"""
claude-subagents-effort -- per-subagent reasoning effort for Claude Code.

Patches a *copy* of the Claude Code binary you already have installed so the
Agent tool accepts an `effort` parameter. The original is opened read-only and
never modified.

    python3 -m ccpatch

See FINDINGS.md for how the patch works and why it is only a few edits.
"""

__version__ = "1.0.0"
