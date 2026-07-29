"""
The patch itself: give the Agent tool an `effort` parameter.

Anchors are regexes over structure, not exact minified text, because Claude
Code is re-minified every release and identifiers like `s6`, `Sb` or `W` are
reassigned each time. Property names (`agentDefinition`, `permissionLayers`,
`kind:"effort"`), call shapes and prose survive; single-letter locals do not.
So every anchor captures the identifiers it needs and the replacement builds
itself from those captures.

Anchors are also scoped to a *region* -- the body of the function being
patched, located by its own structural signature -- so a pattern only has to be
unique within one function rather than within 21 MB of bundle.

See FINDINGS.md for why these particular five places are the whole fix.
"""

import re

LEVELS = ["low", "medium", "high", "xhigh", "max"]
_LEVELS_JS = "[" + ",".join(f"'{x}'" for x in LEVELS) + "]"

EFFORT_DESC = (
    "Optional reasoning-effort override for this agent. Takes precedence over the agent "
    "definition's `effort` frontmatter; if omitted, the definition's effort is used, and "
    "failing that the effort is inherited from the spawning conversation. Independent of "
    "`model` \\u2014 pair a strong model with low effort for cheap read-and-verify work, or a "
    "small model with high effort for hard reasoning on a narrow task. Levels the resolved "
    "model does not support are clamped down."
)

# Appended to the Agent tool description. The surrounding text is a JS template
# literal, so backticks are backslash-escaped and newlines are real.
DOC_BULLET = (
    "\n- \\`effort\\` sets how hard the agent thinks (\\`low\\`/\\`medium\\`/\\`high\\`/"
    "\\`xhigh\\`/\\`max\\`), independently of \\`model\\`. Spend it where reasoning pays off: "
    "low for lookups, mechanical edits, and re-deriving a stated claim; high or above for "
    "design, debugging, and multi-step analysis. Omit it to inherit."
)

# Injected at the top of the Agent tool's call(). Deliberately references no
# bundle identifiers at all: the two helpers are read later by code that sits
# both above and below the point where the agent definition is resolved, so
# they must be declared before anything else in the body (a `let` further down
# is a temporal-dead-zone error, not a scoping nicety).
#
# $mkd is a function rather than a precomputed value for the same reason -- it
# can be applied at each use site regardless of where the definition is built.
PRELUDE = (
    "let $eff=typeof $eIn==='string'&&{levels}.includes($eIn)?$eIn:void 0,"
    "$mkd=($d)=>$eff!==void 0?{{...$d,effort:$eff}}:$d;"
).format(levels=_LEVELS_JS)

# Inlined equivalent of the bundle's getEffortValue(): read the effective
# effort of the spawning context. Inlined rather than called by name because
# the helper's minified name changes every release, whereas `permissionLayers`,
# `kind === "effort"` and `getAppState().effortValue` do not.
def _effective_effort_js(defn, ctx):
    return (
        f"{defn}.effort!==void 0?{defn}.effort:(()=>{{"
        f"let $t={ctx}.getAppState().effortValue;"
        f"for(let $l of {ctx}.permissionLayers??[])if($l.kind==='effort')$t=$l.effort;"
        f"return $t}})()"
    )


class PatchError(Exception):
    pass


# ---------------------------------------------------------------------------
# regions
# ---------------------------------------------------------------------------

REGIONS = {
    # The Agent tool's call(). Spans the signature too, since the `effort`
    # parameter has to be destructured there.
    "agent_call": r"async call\(\{prompt:\w+,subagent_type:\w+,",
    # The subagent query-stream builder (s6): every subagent, from any spawn
    # path, is started through this.
    "subagent_query": (
        r"async function\*\w+\(\{agentDefinition:(?P<defn>\w+),promptMessages:\w+,"
        r"toolUseContext:(?P<ctx>\w+),"
    ),
}


def _balanced_body(src, from_index):
    """End offset of the brace-balanced block opened at or after `from_index`."""
    i = src.index("{", from_index)
    depth = 0
    while i < len(src):
        c = src[i]
        if c in "\"'`":
            quote, i = c, i + 1
            while i < len(src):
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    break
                i += 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise PatchError("unbalanced braces while measuring a region")


def find_region(src, name):
    """(start, end, captured identifiers) for a named region."""
    pattern = REGIONS[name]
    matches = list(re.finditer(pattern, src))
    if len(matches) != 1:
        raise PatchError(
            f"region {name!r}: signature matched {len(matches)} times, expected 1"
        )
    m = matches[0]
    # Skip the parameter list: the body opens at the '{' after the ')' that
    # closes the parameters.
    close = src.index("){", m.start())
    end = _balanced_body(src, close + 1)
    return m.start(), end, {k: v for k, v in m.groupdict().items() if v}


# ---------------------------------------------------------------------------
# edits
# ---------------------------------------------------------------------------


class Edit:
    """One structural rewrite, applied within a region (or the whole file)."""

    def __init__(self, name, pattern, build, region=None, count=1, required=True, why=""):
        self.name = name
        self.pattern = pattern
        self.build = build  # (match, region_ids) -> replacement text
        self.region = region
        self.count = count
        self.required = required
        self.why = why


def _schema(m, ids):
    z = m.group("zod")
    return (
        f'effort:{z}.enum([{",".join(chr(34) + x + chr(34) for x in LEVELS)}])'
        f'.optional().describe("{EFFORT_DESC}"),' + m.group(0)
    )


def _signature(m, ids):
    return f"async call({{{m.group('params')},effort:$eIn}}{m.group('rest')}{PRELUDE}"


def _apply_to_definition(m, ids):
    return f"{{agentDefinition:$mkd({m.group('defn')}),promptMessages:"


def _task_registry(m, ids):
    return f"selectedAgent:$mkd({m.group('defn')}),taskRegistry:"


def _worktree_metadata(m, ids):
    return m.group(0) + ",...$eff!==void 0&&{effort:$eff}"


def _inherit_effort(m, ids):
    layers, defn, ctx = m.group("layers"), m.group("defn"), ids["ctx"]
    return (
        f"$efR={_effective_effort_js(defn, ctx)},"
        f'{layers}=[{{kind:"model",mainLoopModel:{m.group("mm")}}},'
        f'...$efR!==void 0?[{{kind:"effort",effort:$efR}}]:[]]'
    )


def _spawn_metadata(m, ids):
    return (
        f"...{m.group('model')}&&{{model:{m.group('model')}}},"
        f"...$efR!==void 0&&{{effort:$efR}},...{m.group('rest')}}}).catch("
    )


def _doc_bullet(m, ids):
    # The pattern is anchored to end on the line's newline; keep it last.
    return m.group(0)[:-1] + DOC_BULLET + "\n"


EDITS = [
    Edit(
        "schema:effort-param",
        r"run_in_background:(?P<zod>\w+)\.boolean\(\)",
        _schema,
        why="declare `effort` on the Agent tool's input schema",
    ),
    Edit(
        "call:destructure+prelude",
        r"async call\(\{(?P<params>[^{}]*)\}(?P<rest>,[^)]*\)\{)",
        _signature,
        region="agent_call",
        why="read the parameter and install the $eff/$mkd helpers",
    ),
    Edit(
        "call:apply-to-definition",
        r"\{agentDefinition:(?P<defn>\w+),promptMessages:",
        _apply_to_definition,
        region="agent_call",
        why="hand the overridden definition to the subagent query -- this is the feature",
    ),
    Edit(
        "call:task-registry",
        r"selectedAgent:(?P<defn>\w+),taskRegistry:",
        _task_registry,
        region="agent_call",
        count=2,
        why="show the override in the agents panel and tasks status line",
    ),
    Edit(
        "call:worktree-metadata",
        r"spawnDepth:\w+,\.\.\.!\w+&&(?P<model>\w+)&&\{model:(?P=model)\}",
        _worktree_metadata,
        region="agent_call",
        why="record the effort in worktree spawn metadata",
    ),
    Edit(
        "subagent:inherit-effort",
        r'(?P<layers>\w+)=\[\{kind:"model",mainLoopModel:(?P<mm>\w+)\},'
        r'\.\.\.(?P<defn>\w+)\.effort!==void 0\?\[\{kind:"effort",effort:(?P=defn)\.effort\}\]:\[\]\]',
        _inherit_effort,
        region="subagent_query",
        why="propagate effort down the spawn chain, as `model` already is",
    ),
    Edit(
        "subagent:spawn-metadata",
        r"\.\.\.(?P<model>\w+)&&\{model:(?P=model)\},\.\.\.(?P<rest>\w+)\}\)\.catch\(",
        _spawn_metadata,
        region="subagent_query",
        why="persist the effective effort to agent-*.meta.json so it can be verified",
    ),
    Edit(
        "docs:tool-description",
        "- Each agent type's model, reasoning effort, and tool[^\n]*\n",
        _doc_bullet,
        count=2,
        required=False,
        why="document the parameter in the tool description (cosmetic; the "
        "schema description carries the same guidance)",
    ),
]


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


def apply(src, on_event=lambda *a: None):
    """
    Patch a cli.js source string. Returns (patched_source, report).

    Every required edit must match its expected number of times; anything else
    aborts rather than producing a half-patched bundle.
    """
    regions = {}
    report = []

    for name in REGIONS:
        try:
            start, end, ids = find_region(src, name)
            regions[name] = (start, end, ids)
            on_event("region", name, f"{end - start} bytes")
        except PatchError as exc:
            raise PatchError(f"could not locate region {name!r}: {exc}") from exc

    failures = []
    # Apply back-to-front within each scope so earlier offsets stay valid.
    plan = []
    for edit in EDITS:
        if edit.region:
            start, end, ids = regions[edit.region]
            scope, offset = src[start:end], start
        else:
            scope, offset, ids = src, 0, {}
        matches = list(re.finditer(edit.pattern, scope))
        if len(matches) != edit.count:
            msg = f"{edit.name}: matched {len(matches)}x, expected {edit.count}"
            if edit.required:
                failures.append(msg)
            else:
                on_event("skip", edit.name, msg)
            continue
        for m in matches:
            plan.append((offset + m.start(), offset + m.end(), edit.build(m, ids), edit))

    if failures:
        raise PatchError(
            "the bundle does not match the expected structure:\n  "
            + "\n  ".join(failures)
            + "\n\nThis usually means Claude Code changed in a way the anchors in "
            "ccpatch/patch.py do not cover. See FINDINGS.md, or run the "
            "/patch-effort skill and let Claude re-derive them."
        )

    out = src
    for start, end, replacement, edit in sorted(plan, key=lambda p: p[0], reverse=True):
        out = out[:start] + replacement + out[end:]
    for edit in EDITS:
        n = sum(1 for _, _, _, e in plan if e is edit)
        if n:
            report.append((edit.name, n, edit.why))
            on_event("edit", edit.name, f"{n}x")

    return out, report
