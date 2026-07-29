# How this works

Everything needed to re-derive the patch from scratch against a Claude Code
build this repo has never seen. Written down because the code in `ccpatch/`
will rot — it matches minified structure, and minified structure changes — but
none of what follows does.

Reverse-engineered from Claude Code 2.1.220 (macOS, arm64).

---

## 1. The bug is a missing parameter, not a missing feature

Reasoning effort reaches the API as a field on the request body:

```json
{ "model": "claude-sonnet-4-6", "output_config": { "effort": "medium" }, ... }
```

Claude Code resolves a conversation's effort through a stack of *context
layers*. Each layer is `{kind: "model" | "effort" | "permission_mode" | ...}`,
and the effective value is the last matching layer:

```js
function getEffortValue(ctx) {
  let value = ctx.getAppState().effortValue;
  let layers = ctx.permissionLayers;
  if (!layers) return value;
  for (const layer of layers) if (layer.kind === "effort") value = layer.effort;
  return value;
}
```

When a subagent is spawned, the spawn function builds a fresh layer list for
the child. In stock 2.1.220 that list already reads an `effort` off the agent
definition:

```js
layers = [
  { kind: "model", mainLoopModel: resolvedModel },
  ...definition.effort !== undefined
      ? [{ kind: "effort", effort: definition.effort }]
      : [],
]
```

So the whole mechanism is present and working. `effort:` in an agent's
`.claude/agents/*.md` frontmatter already worked before this patch — several
comments on
[#43083](https://github.com/anthropics/claude-code/issues/43083) say otherwise;
they are wrong, or were reading a stale config, since agent definitions are
read once at session start.

**The only thing missing is that the Agent tool never puts anything into
`definition.effort`.** It has a `model` parameter and no `effort` parameter, so
a caller can override the model per invocation but not the effort.

That is why the patch is ~1.5 KB and not a feature port. It feeds an existing
path rather than adding a parallel one. Anthropic's own Workflow runtime
already uses the exact shape this patch uses — `{...agentDefinition, effort}` —
for its `agent({effort})` option.

---

## 2. The container: a Bun standalone executable

The shipped binary is a normal platform executable with a blob appended:

```
[ Mach-O / ELF / PE ][ blob ][ 32-byte Offsets ][ "\n---- Bun! ----\n" ][ signature? ]
```

Find the trailer magic from the end of the file. The 32 bytes before it are:

| offset | type | meaning |
|--------|------|---------|
| 0  | u64 | `byte_count` — size of the blob |
| 8  | u32 | module table offset (relative to blob start) |
| 12 | u32 | module table length |
| 16 | u32 x4 | other pointers |

`blob_start = (magic_offset - 32) - byte_count`. Every offset inside the blob
is relative to `blob_start`, which is what makes this readable without parsing
Mach-O, ELF or PE at all — one implementation covers all three platforms.

The module table is a flat array of 52-byte entries (13 × u32). The fields that
matter:

| index | meaning |
|-------|---------|
| 0, 1 | name offset, length |
| 2, 3 | **contents** offset, length |
| 6, 7 | **bytecode** offset, length |

Module 0 is `/$bunfs/root/src/entrypoints/cli.js` — the entire ~21.6 MB
Claude Code bundle, minified onto very few lines.

Entry size was confirmed structurally rather than guessed: for the correct
size, every entry's fields are self-consistent (`contents_offset ==
name_offset + name_length + 1`, names decode as UTF-8 paths, offsets are
in range). 56 does not satisfy that; 52 does, across all 14 entries.

## 3. The trap: patching the source alone does nothing

Module 0 carries **both** a JS source and a ~153 MB JSC bytecode cache, and Bun
runs the bytecode whenever it is present. The bundle even says so, on line 1:

```
// @bun @bytecode
```

Edit the source, rebuild, and the binary behaves exactly as before. This is the
single most important thing to know before starting, and it costs hours to
discover by experiment.

The fix is also what makes the rebuild cheap: **write the patched source over
the bytecode region and zero the bytecode pointer.**

```
entry[2], entry[3] = bytecode_offset, len(patched)   # contents -> old bytecode region
entry[6], entry[7] = 0, 0                            # bytecode -> gone
```

The patched source (~21.6 MB) fits inside the old bytecode region (~153 MB)
many times over, so nothing after it moves, every other module's offsets stay
valid, and the file length is unchanged. The only structural edit in the whole
file is **16 bytes in one table entry**. Blank the region's tail with newlines
so a stray read lands on valid empty JS rather than bytecode garbage.

Cost: startup parses 21.6 MB of JS instead of loading bytecode. Measured at
~0.2 s (`--version`: 0.04 s stock, 0.24 s patched).

Sanity checks worth keeping, because a mis-parsed table would silently corrupt
the file: refuse if module 0 is not `cli.js`, if there is no bytecode region,
if the region starts with four zero bytes (a JSC cache never does), if the
source does not fit, or if the payload length changes.

## 4. Signing

Any byte edit invalidates the Developer ID signature.

- **macOS** — must re-sign. On Apple silicon a *broken* signature will not
  launch at all (an unsigned binary is tolerated; a corrupt one is not).
  `codesign --force --sign - --options runtime --entitlements <original's>`.
  Pull the entitlements off the original with
  `codesign -d --entitlements - --xml`.
- **Linux** — nothing to do.
- **Windows** — Authenticode is invalidated but PE execution does not require a
  valid signature. SmartScreen may warn on first run.

---

## 5. The five places to change

Described by structure, since the identifiers (`s6`, `Sb`, `W`, `$eff`) are
reassigned every release. `ccpatch/patch.py` encodes exactly this list as
regexes; if one stops matching, this section is how to find its replacement.

Two **regions** scope the search, so a pattern only has to be unique within one
function instead of within 21 MB:

- **`agent_call`** — the Agent tool's `call()`. Signature looks like
  `async call({prompt, subagent_type, description, model, run_in_background, name, isolation, cwd}, ...)`.
- **`subagent_query`** — the subagent query-stream builder, an async generator
  taking `{agentDefinition, promptMessages, toolUseContext, ...}`. Every
  subagent from every spawn path goes through it.

| # | Where | What | Why |
|---|-------|------|-----|
| 1 | Agent input schema, next to `run_in_background: z.boolean()` | add `effort: z.enum([...]).optional().describe(...)` | declares the parameter, and the `describe` text is what makes the model choose sensible values unprompted |
| 2 | `agent_call` signature + first statement | destructure `effort`, declare `$eff` (validated) and `$mkd` (definition → definition-with-effort) | see the TDZ note below |
| 3 | `agent_call`, `{agentDefinition: X, promptMessages: ...}` | `agentDefinition: $mkd(X)` | **this is the feature** — everything else is bookkeeping |
| 4 | `agent_call`, `selectedAgent: X, taskRegistry: ...` (2 sites: async and sync registration) | `selectedAgent: $mkd(X)` | the agents panel and tasks status line read effort off `selectedAgent` |
| 5a | `agent_call`, worktree spawn metadata `spawnDepth: n, ...!x && m && {model: m}` | append `...$eff !== undefined && {effort: $eff}` | records it |
| 5b | `subagent_query`, spawn metadata `...m && {model: m}, ...j}).catch(` | insert `...$efR !== undefined && {effort: $efR},` | records the *effective* effort in `agent-*.meta.json`, so inherited values are auditable too |
| 6 | `subagent_query`, the layer list in §1 | `$efR = definition.effort ?? <effective effort of the spawning context>` and build the layer from `$efR` | without it, a subagent spawned *by* a low-effort subagent silently reverts to the session-wide effort |

Two implementation notes that were learned the hard way:

**Declare the helpers at the top of `call()`, not next to their first use.**
The worktree-metadata closure and the task-registry calls sit *above* the point
where the agent definition is resolved. A `let` further down is a temporal dead
zone error at runtime, not a style preference. This is why `$mkd` is a function
applied at each use site rather than a precomputed value — it makes every edit
order-independent.

**Inline the effort getter rather than calling it by name.** Edit 6 needs the
spawning context's effective effort, i.e. `getEffortValue(toolUseContext)`. That
helper's minified name changes every release; `permissionLayers`,
`kind === "effort"` and `getAppState().effortValue` do not. Inlining three
lines removes a whole class of version fragility.

---

## 6. Ground truth is the wire, not the UI

A subagent can *report* an effort it was never launched at. It cannot send a
request body it did not send. Every claim in this repo's README was measured by
running the binary against a local HTTP endpoint and reading
`output_config.effort` off the actual request bodies (`ccpatch/verify.py`).

Two things that will mislead you when reading captures:

- **Not every request is a conversation turn.** Session titling fires its own
  call, at its own fixed effort, with `"tools": []`. It arrives *first*, so
  treating request #1 as the parent's first turn gives a wrong answer. Filter
  to requests that carry a tool set.
- **The parent's effort must be checked too.** A patch that accidentally set
  effort globally would still show the child at the requested level.

Measured on 2.1.220, format `[parent, subagent, ...]`, model `claude-opus-5`:

```
stock, effort:"low" requested    ['high', 'high', 'high']   <- parameter ignored
patched, effort:"low"            ['high', 'low',   'high']
patched, effort:"medium"         ['high', 'medium','high']
patched, effort:"xhigh"          ['high', 'xhigh', 'high']
patched, effort:"max"            ['high', 'max',   'high']
patched, omitted (inherit)       ['high', 'high',  'high']
patched, frontmatter effort: low ['high', 'low',   'high']
patched, param xhigh vs fm low   ['high', 'xhigh', 'high']
patched, nested low, then unset  ['high', 'low', 'low', 'low', 'high']
```

## 7. Three things that look like bugs and are not

- **`max` gets clamped to `high` on some models.** Correct existing behaviour.
  A model-capability check clamps any level the resolved model does not
  support. `claude-opus-4-5` does not support `max`; `claude-opus-5` does. Test
  clamping on a model that supports the level, or you will chase a ghost.
- **Integer effort values do nothing.** Agent frontmatter accepts them, but the
  function that emits `output_config` only forwards `typeof value === "string"`,
  so an integer is dropped and *no* `output_config` is sent at all. This patch
  deliberately offers only the five named levels rather than shipping a
  parameter that silently does nothing.
- **Frontmatter `effort:` works in stock.** See §1.

## 8. When a new release breaks the anchors

1. `python3 -m ccpatch --keep-sources ./out` writes `cli.orig.js` next to
   `cli.patched.js`, so you have the new bundle to search even when the patch
   aborts.
2. The failure message names which anchors missed and how many times each
   matched. `0` means the structure moved; `2+` means the pattern is no longer
   unique within its region and needs narrowing, not rewriting.
3. Find the new shape using §5's descriptions — search for the *property names*
   (`agentDefinition`, `selectedAgent`, `permissionLayers`, `spawnDepth`),
   never the single-letter locals.
4. Update the regex in `ccpatch/patch.py`, capturing whatever identifiers the
   replacement needs.
5. Re-run with `--verify live`. Do not trust a build that only compiles; §6
   exists because "it ran without erroring" is not evidence.

The `/patch-effort` skill in `skills/` hands this document to Claude Code and
asks it to do exactly those five steps.
