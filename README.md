# claude-subagents-effort

Per-subagent reasoning effort for Claude Code.

Claude Code lets you pick the *model* for a subagent when you spawn it, but not
the *effort*. This adds an `effort` parameter to the Agent tool, so a strong
model can be spawned to do cheap work, or a small one to think hard about a
narrow problem:

```
Agent(subagent_type="code-reviewer", model="opus", effort="low", ...)
```

Fixes [anthropics/claude-code#43083](https://github.com/anthropics/claude-code/issues/43083).
The subagents stay ordinary in-session subagents — visible in the agents panel,
addressable with `SendMessage`, resumable, killable — unlike the
`claude --bg --effort` workaround going around on that issue, which spawns
detached out-of-session runs.

**No binaries are distributed here.** The installer patches a *copy* of the
Claude Code you already have. Your install is opened read-only and never
modified.

---

## Install

Clone, then run. Deliberately not `curl | sh` — this rebuilds your coding
agent, so the code should be on your disk where you can read it first.

**macOS / Linux**

```sh
git clone https://github.com/pikalover6/claude-subagents-effort && ./claude-subagents-effort/install.sh
```

**Windows** (PowerShell)

```powershell
git clone https://github.com/pikalover6/claude-subagents-effort; .\claude-subagents-effort\install.ps1
```

Needs Python 3.8+ and about 300 MB of disk. Takes a couple of seconds, plus the
verification run.

You get a plain menu, and pressing Enter accepts everything:

```
  1  Patch            Claude Code 2.1.220  /Users/you/.local/share/claude/versions/2.1.220
  2  Command name     claude2  you will type `claude2` to run it
  3  Install to       /Users/you/.local/bin
  4  Keep binary in   /Users/you/.local/share/claude-subagents-effort  ~250 MB
  5  Verify           live  one real API call -- costs a small amount of usage credits

  Enter to install, a number to change, q to quit >
```

Every option is also a flag, for unattended installs:

```sh
./install.sh --alias cc2 --bin-dir ~/bin --verify offline --yes
./install.sh --uninstall
./install.sh --help
```

> **Verification costs a little usage.** The `live` check runs a real two-turn
> Sonnet exchange (a `sonnet-4-6` session at `low` effort spawns a `sonnet-4-6`
> subagent at `medium` whose entire job is to answer `hi`) and reads the actual
> request bodies to confirm the effort reached the wire. A few cents at most.
> Pick `offline` at option 5 for a free check that uses canned responses, or
> `none` to skip it.

## Using it

Run `claude2` exactly as you would run `claude`. It shares `~/.claude`, so
credentials, settings, MCP servers, plugins, projects and session history are
the same, and `claude --resume` can pick up a session started under `claude2`
and vice versa.

No special prompting is needed — the parameter is documented in the tool
schema, so plain English works:

```
> review this migration at max effort, and in parallel have a low-effort agent
  list every call site
```

Accepted levels: `low`, `medium`, `high`, `xhigh`, `max`.

Precedence: **invocation parameter → agent frontmatter `effort:` → inherited
from the spawning conversation.** Effort is independent of `model`. Levels the
resolved model does not support are clamped down by Claude Code's existing
capability logic (so `max` becomes `high` on Opus 4.5, for instance — that is
stock behaviour, not this patch).

Effort now also inherits down the spawn chain the way `model` already did: a
subagent spawned by a low-effort subagent stays low unless told otherwise.
And the effective effort is written into `agent-*.meta.json` at every depth, so
a configuration can be checked rather than trusted.

## Please read this part

- **This is not supported by Anthropic, and it is not affiliated with them.**
  Do not file bugs against Claude Code from a patched build, and do not ask
  Anthropic support about it — if something is broken here, it is this repo's
  problem, so [open an issue](https://github.com/pikalover6/claude-subagents-effort/issues).
- **Auto-update is force-disabled for the patched build only.** Claude Code's
  updater installs into the shared versions directory and repoints the stock
  `claude` launcher, so an update triggered from `claude2` would overwrite your
  real install. The launcher sets `DISABLE_AUTOUPDATER=1` for itself; no shared
  config is touched, and stock `claude` keeps updating normally.
- **`claude2` stays on the version you built it from.** When Claude Code
  updates, re-run the installer to rebuild against the new one. Expect this to
  work most of the time and to occasionally need an anchor updated — see below.
- **Claude Code is proprietary software.** This repo distributes only its own
  tooling; you run it against your own licensed copy. Whether modifying that
  copy is something you want to do is your call and your responsibility — the
  terms you agreed to are the place to check.
- If you find this useful, the thing that would help everyone most is
  [upvoting the issue](https://github.com/anthropics/claude-code/issues/43083)
  so it ships properly.

## What it actually changes

Eight edits, about 1.5 KB, to the bundled `cli.js` — the mechanism was already
there and simply had nothing feeding it:

| Where | Change |
|-------|--------|
| Agent tool input schema | declare the `effort` parameter |
| Agent tool `call()` | read and validate it |
| Agent tool `call()` | apply it to the agent definition handed to the subagent — *this is the feature* |
| task registry (×2) | register the override so the agents panel and status line show it |
| worktree spawn metadata | record it |
| subagent query builder | inherit effort down the spawn chain, as `model` already was |
| subagent spawn metadata | persist the effective effort to `agent-*.meta.json` |
| tool description (×2) | document the parameter |

The interesting part is how little there is: Claude Code already converts
`agentDefinition.effort` into a `{kind:"effort"}` context layer and already
flows that to the child. The Agent tool simply never populated it.
[FINDINGS.md](FINDINGS.md) has the full reverse-engineering writeup — the Bun
container format, the bytecode cache that makes naive patching a silent no-op,
where each edit goes and why, and how to re-derive them all when a release
moves things.

## When a new Claude Code release breaks it

Anchors match *structure*, not exact minified text, so identifier churn between
releases mostly does not matter. When one does break, the installer says which
anchor missed and how many times it matched, and refuses to produce a
half-patched build.

Two ways to fix it:

- By hand, following [FINDINGS.md](FINDINGS.md) §5 and §8.
- Or let Claude Code do it: the repo ships a **`/patch-effort` skill**. Copy
  `skills/patch-effort` into `~/.claude/skills/`, then run `/patch-effort` and
  it will build, install, and — if an anchor has rotted — re-derive it from
  FINDINGS.md and verify the result on the wire before handing it back. It
  takes the same options as the installer.

## Uninstall

```sh
./install.sh --uninstall
```

Removes the launcher and the patched binary. Nothing under `~/.claude` is
touched.

## Layout

```
install.sh / install.ps1   entry points
ccpatch/
  __main__.py              the interactive installer
  locate.py                find the installed binary (by parsing it, not by path)
  bunfmt.py                read/write the Bun standalone container
  patch.py                 the edits, as structural regexes
  build.py                 extract -> patch -> re-embed -> sign
  sign.py                  per-platform signing
  install.py               launcher + manifest
  verify.py                wire-level verification, live or offline
skills/patch-effort/       the self-repairing Claude Code skill
FINDINGS.md                how all of it works
```

MIT for everything in this repo. That covers this tooling only, not Claude Code.
