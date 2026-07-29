---
name: patch-effort
description: Build and install a Claude Code binary that supports per-subagent reasoning effort (an `effort` parameter on the Agent tool). Use when the user asks to install, reinstall, update, repair or uninstall claude2 / claude-subagents-effort, or after a Claude Code upgrade has left the patched build on an old version. Also handles re-deriving the patch anchors when a new Claude Code release breaks them.
---

# patch-effort

Installs [claude-subagents-effort](https://github.com/pikalover6/claude-subagents-effort):
a patched copy of the user's own Claude Code that accepts an `effort` parameter
on the Agent tool ([anthropics/claude-code#43083](https://github.com/anthropics/claude-code/issues/43083)).

The installer does the work. Your job is to run it, ask the user the few things
it needs, and — the part a script cannot do — repair the patch when a new
Claude Code release has moved the code it targets.

## Absolute rules

1. **Never modify the user's installed Claude Code.** Open it read-only. The
   tooling already enforces this; do not work around it, and do not "fix" a
   problem by writing into `~/.local/share/claude/versions/` or wherever the
   install lives.
2. **Never claim it works because it built.** A patched bundle that parses is
   not a patched bundle that does anything — that is the whole reason
   `--verify` exists. Only `verify` passing is evidence.
3. **Report failures as failures.** If verification does not pass, say so
   plainly and show what the wire actually carried. Do not soften it.

## 1. Get the repo

Use a checkout if one is already around, otherwise clone it:

```sh
test -d ~/.cache/claude-subagents-effort/.git \
  && git -C ~/.cache/claude-subagents-effort pull --ff-only \
  || git clone https://github.com/pikalover6/claude-subagents-effort ~/.cache/claude-subagents-effort
```

Everything below runs from that directory.

## 2. Ask what they want

The installer's defaults are good; do not interrogate the user. Ask once,
compactly, and offer to just use the defaults:

| Option | Flag | Default |
|--------|------|---------|
| Command name | `--alias` | `claude2` |
| Where the launcher goes | `--bin-dir` | `~/.local/bin` (Windows: `%LOCALAPPDATA%\Programs\claude-subagents-effort`) |
| Where the ~250 MB binary is kept | `--data-dir` | `~/.local/share/claude-subagents-effort` |
| Which Claude Code to patch | `--source` | autodetected, newest |
| Verification | `--verify` | `live` |

Two things they must actually be told before you run it:

- **`live` verification costs a small amount of usage credits** — one real
  two-turn Sonnet exchange. `offline` is free and still checks the wire format;
  `none` skips it.
- The command name must not be `claude`; that would shadow their real install.

Then run it non-interactively with their answers:

```sh
./install.sh --yes --alias claude2 --verify live
```

Use `--yes` since you are collecting the options yourself — without it the
installer will sit at a menu waiting for a keypress that never comes.

## 3. If it fails

**Verification failed, build succeeded.** The patch did not take effect.
Report the observed request efforts verbatim. Do not retry blindly.

**An anchor did not match.** The message names each anchor and how many times
it matched. `0` means the code moved; `2` or more means the pattern is no
longer unique inside its region. This is expected occasionally after a Claude
Code upgrade, and it is repairable:

1. Read `FINDINGS.md` in the repo — all of it, not just the section you think
   you need. §5 describes every edit by *structure*, which is what lets you
   find the new shape; §3 explains the bytecode cache, which is the one thing
   that will silently waste your time if you do not know about it.
2. Get the new bundle to search:
   ```sh
   ./install.sh --yes --verify none --keep-sources ./out
   ```
   It writes `out/cli.orig.js` even when the patch aborts.
3. Find the new shape. Search for **property names and call shapes**
   (`agentDefinition`, `selectedAgent`, `permissionLayers`, `spawnDepth`,
   `kind:"effort"`), never single-letter locals — those are reassigned every
   release, which is exactly why the anchor broke.
4. Update the regex in `ccpatch/patch.py`. Keep it structural: capture the
   identifiers the replacement needs rather than hard-coding them, and keep it
   scoped to its region. Confirm the new pattern matches the expected number of
   times before rebuilding.
5. Rebuild and **verify live**. If you changed an anchor, `--verify offline` is
   not enough — it drives the tool call itself rather than letting a model
   choose it, so it cannot catch a schema that no longer reaches the model.
6. Tell the user which anchor you changed and what it now matches, and suggest
   they open a PR against the repo so the next person does not repeat it.

**Nothing found to patch.** `locate.py` confirms candidates by parsing them,
so "not found" means no file it could see is a Bun-packaged Claude Code. Ask
where their install is and pass `--source`.

**macOS, `codesign` missing.** The build cannot proceed; on Apple silicon an
improperly signed binary will not launch. Tell them to run
`xcode-select --install`.

## 4. When it works

Tell them, briefly:

- the command name, and that it is a drop-in for `claude` sharing the same
  config, credentials and session history in both directions
- that they can just ask for effort in plain English ("review this at max
  effort"), no special syntax needed
- that auto-update is disabled for this build only, so they should re-run
  `/patch-effort` after a Claude Code upgrade
- that bugs go to the repo, never to Anthropic

## Uninstall

```sh
./install.sh --uninstall
```

Removes the launcher and the patched binary. Nothing under `~/.claude` is
touched — confirm that to the user, since it is the thing they will worry
about.
