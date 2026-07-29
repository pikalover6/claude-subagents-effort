"""
Interactive installer.

Run with no arguments and it shows you what it is about to do, lets you change
any of it, and does nothing until you say so. Every option also has a flag, so
the same code path works unattended.
"""

import argparse
import shutil
import sys
from pathlib import Path

from . import build, install, locate, verify
from .bunfmt import NotABunExecutable
from .patch import PatchError
from .sign import SigningError

REPO = "https://github.com/pikalover6/claude-subagents-effort"
ISSUE = "https://github.com/anthropics/claude-code/issues/43083"

VERIFY_MODES = ["live", "offline", "none"]
VERIFY_HELP = {
    "live": "one real API call -- costs a small amount of usage credits",
    "offline": "no API calls, no cost; checks the wire format only",
    "none": "skip verification",
}


# ---------------------------------------------------------------------------
# terminal helpers
# ---------------------------------------------------------------------------

_TTY = sys.stdout.isatty()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def bold(t):
    return _c("1", t)


def dim(t):
    return _c("2", t)


def red(t):
    return _c("31", t)


def green(t):
    return _c("32", t)


def yellow(t):
    return _c("33", t)


def ask(prompt, default=""):
    try:
        answer = input(prompt).strip()
    except EOFError:
        return default
    return answer or default


def confirm(prompt, default=True):
    suffix = "[Y/n]" if default else "[y/N]"
    answer = ask(f"{prompt} {suffix} ").lower()
    if not answer:
        return default
    return answer.startswith("y")


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


class Plan:
    def __init__(self, args):
        self.source = Path(args.source).expanduser() if args.source else locate.find()
        self.version = locate.version_of(self.source) if self.source else None
        self.alias = args.alias
        self.bin_dir = Path(args.bin_dir).expanduser() if args.bin_dir else install.default_bin_dir()
        self.data_dir = Path(args.data_dir).expanduser() if args.data_dir else install.default_data_dir()
        self.verify = args.verify

    @property
    def binary(self):
        return install.binary_path(self.data_dir, self.alias, self.version or "unknown")

    @property
    def launcher(self):
        return install.launcher_path(self.bin_dir, self.alias)

    def rows(self):
        path_note = "" if install.on_path(self.bin_dir) else yellow("  not on PATH")
        return [
            ("Patch", f"Claude Code {self.version or '?'}", dim(f"  {self.source}")),
            ("Command name", self.alias, dim(f"  you will type `{self.alias}` to run it")),
            ("Install to", str(self.bin_dir), path_note),
            ("Keep binary in", str(self.data_dir), dim("  ~250 MB")),
            ("Verify", self.verify, dim(f"  {VERIFY_HELP[self.verify]}")),
        ]

    def show(self):
        print()
        for i, (label, value, note) in enumerate(self.rows(), 1):
            print(f"  {dim(str(i))}  {label:<16} {bold(str(value))}{note}")
        print()

    def edit(self, index):
        if index == 1:
            options = locate.find_all()
            if len(options) > 1:
                print("\n  Patchable Claude Code builds found:")
                for i, p in enumerate(options, 1):
                    print(f"    {i}  {p}  {dim(locate.version_of(p) or '')}")
                choice = ask("  Number, or a path: ")
                if choice.isdigit() and 1 <= int(choice) <= len(options):
                    self.source = options[int(choice) - 1]
                elif choice:
                    self.source = Path(choice).expanduser()
            else:
                answer = ask(f"  Path to the Claude Code binary [{self.source}]: ")
                if answer:
                    self.source = Path(answer).expanduser()
            self.version = locate.version_of(self.source)
        elif index == 2:
            answer = ask(f"  Command name [{self.alias}]: ", self.alias)
            if answer == "claude":
                print(red(
                    "\n  `claude` is the stock command. Using it would shadow your real\n"
                    "  install on PATH, and this tool will not do that for you."
                ))
                return
            self.alias = answer
        elif index == 3:
            answer = ask(f"  Install the launcher into [{self.bin_dir}]: ")
            if answer:
                self.bin_dir = Path(answer).expanduser()
        elif index == 4:
            answer = ask(f"  Keep the patched binary in [{self.data_dir}]: ")
            if answer:
                self.data_dir = Path(answer).expanduser()
        elif index == 5:
            print("\n  " + "\n  ".join(
                f"{i}  {m:<8} {VERIFY_HELP[m]}" for i, m in enumerate(VERIFY_MODES, 1)))
            answer = ask(f"  Choice [{self.verify}]: ")
            if answer.isdigit() and 1 <= int(answer) <= len(VERIFY_MODES):
                self.verify = VERIFY_MODES[int(answer) - 1]
            elif answer in VERIFY_MODES:
                self.verify = answer

    def problems(self):
        out = []
        if not self.source:
            out.append(
                "No Claude Code install found. Point at it with --source, or set "
                "CLAUDE_CODE_BINARY."
            )
            return out
        if not Path(self.source).is_file():
            out.append(f"{self.source} does not exist.")
        if not self.version:
            out.append(f"{self.source} did not report a version; is it Claude Code?")
        if not build.free_space_for(self.data_dir, 300 * 1024 * 1024):
            out.append(f"Less than 300 MB free where {self.data_dir} would go.")
        return out


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def header():
    print()
    print("  " + bold("claude-subagents-effort"))
    print("  " + dim("per-subagent reasoning effort for Claude Code"))
    print("  " + dim(ISSUE))


def do_install(plan, keep_sources=None):
    print()
    print(bold("  Building"))
    try:
        build.build(plan.source, plan.binary, log=lambda m: print("  " + m.strip()),
                    keep_sources=keep_sources)
        got = build.smoke_test(plan.binary, expect_version=plan.version)
        print(f"  starts and reports {got}")
    except (PatchError, NotABunExecutable, SigningError, build.BuildError) as exc:
        print()
        print(red("  Build failed."))
        print("  " + str(exc).replace("\n", "\n  "))
        return 1

    launcher = install.write_launcher(plan.bin_dir, plan.alias, plan.binary, plan.version)
    install.write_manifest(plan.data_dir, {
        "alias": plan.alias,
        "version": plan.version,
        "source": str(plan.source),
        "binary": str(plan.binary),
        "launcher": str(launcher),
        "repo": REPO,
    })
    print(f"  installed {launcher}")

    if plan.verify != "none":
        print()
        print(bold("  Verifying"))
        try:
            verify.run(plan.binary, live=(plan.verify == "live"),
                       log=lambda m: print("  " + m.strip()))
        except verify.VerificationError as exc:
            print()
            print(red("  Verification failed."))
            print("  " + str(exc).replace("\n", "\n  "))
            print()
            print("  The binary is installed but is not doing what it should. Please")
            print(f"  report this, with the output above, at {REPO}/issues")
            return 1

    print()
    print(green("  Done."))
    print()
    print(f"  Run {bold(plan.alias)} exactly as you would run `claude`. It shares your")
    print("  settings, credentials and session history, so you can switch between")
    print("  them freely, including resuming the same session.")
    print()
    print("  Ask for a subagent at a given effort in plain English -- \"review this")
    print("  at max effort\" -- or pass it explicitly:")
    print(dim('    Agent(subagent_type="general-purpose", effort="low", ...)'))
    print()
    if not install.on_path(plan.bin_dir):
        print(yellow(f"  {plan.bin_dir} is not on your PATH. Add it with:"))
        print(f"    {install.path_hint(plan.bin_dir)}")
        print()
    print(dim(f"  Uninstall:  python3 -m ccpatch --uninstall"))
    print()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ccpatch", description="Install a Claude Code build with per-subagent effort.")
    parser.add_argument("--source", help="Claude Code binary to patch (default: autodetect)")
    parser.add_argument("--alias", default="claude2", help="command name (default: claude2)")
    parser.add_argument("--bin-dir", help="where the launcher goes")
    parser.add_argument("--data-dir", help="where the patched binary is kept")
    parser.add_argument("--verify", choices=VERIFY_MODES, default="live",
                        help="how to check the result (default: live)")
    parser.add_argument("--keep-sources", metavar="DIR",
                        help="also write cli.orig.js and cli.patched.js here")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask, just do it")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)

    if args.uninstall:
        data_dir = Path(args.data_dir).expanduser() if args.data_dir else install.default_data_dir()
        return 0 if install.uninstall(data_dir) else 1

    header()
    plan = Plan(args)

    if plan.source:
        print()
        print(f"  Found Claude Code {bold(plan.version or '?')}")
        print(dim(f"    {plan.source}"))
        print(dim("    opened read-only; your install is never modified"))

    existing = install.read_manifest(plan.data_dir)
    if existing and not args.yes:
        print()
        print(yellow(f"  {existing.get('alias')} {existing.get('version')} is already "
                     "installed here; continuing replaces it."))

    while not args.yes:
        plan.show()
        for problem in plan.problems():
            print(red("  ! ") + problem)
        answer = ask("  " + dim("Enter to install, a number to change, q to quit") + " > ")
        if answer.lower() in ("q", "quit", "n", "no"):
            print("  Nothing was changed.\n")
            return 130
        if answer.isdigit() and 1 <= int(answer) <= len(plan.rows()):
            plan.edit(int(answer))
            continue
        if answer:
            continue
        break

    problems = plan.problems()
    if problems:
        print()
        for problem in problems:
            print(red("  ! ") + problem)
        print()
        return 2

    if plan.verify == "live" and not args.yes:
        print()
        print("  " + yellow("Verification makes one real API call") + " (a two-turn "
              "sonnet exchange,")
        print("  a few cents of usage at most) to prove the effort actually reaches")
        print("  the wire. Choose `offline` at option 5 to skip the cost.")
        if not confirm("  Continue?"):
            print("  Nothing was changed.\n")
            return 130

    return do_install(plan, keep_sources=args.keep_sources)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  Interrupted. Nothing was changed.\n")
        sys.exit(130)
