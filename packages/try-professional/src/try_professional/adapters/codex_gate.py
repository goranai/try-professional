"""Stands in for gh and git inside an agent's step. Reading runs; anything else waits for a person.

An agent runs a role's commands with nobody to ask, so `gh pr create` or `git push` would just happen. The runner puts
this in front of gh and git on the step's PATH. It sees the exact words gh or git would get, after the shell is done
with them, so it reads them as they do. It runs what only reads, such as `gh pr list` or `git log`, and git's work on
local files, such as `git commit` or `git clone`. Anything else, such as a change to git's global settings, and
anything it doesn't know, waits for a person, as `trypro held` shows. It guards these two programs; it isn't a
sandbox for the rest of the machine.

    python -m try_professional.adapters.codex_gate <company> <run_id> <role> <workspace> <real program> [args...]
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

# gh commands that only read, by command group. None: every subcommand of the group reads.
_GH_READS: dict[str, set[str] | None] = {
    "pr": {"list", "view", "status", "checks", "diff"},
    "issue": {"list", "view", "status"},
    "repo": {"view", "list", "clone"},
    "release": {"list", "view", "download"},
    "run": {"list", "view", "watch", "download"},
    "workflow": {"list", "view"},
    "label": {"list"},
    "gist": {"list", "view"},
    "cache": {"list"},
    "ruleset": {"list", "view", "check"},
    "search": None,
    "status": None,
    "help": None,
    "version": None,
}
_GH_OPTIONS_WITH_A_VALUE = {"-R", "--repo", "--hostname"}
# gh api's short options that take a value, attached (-ftitle=x) or as the next word; others are switches (-i).
_GH_API_VALUED = set("XfFHpqt")
# git's own options that take the next word as their value.
_GIT_OPTIONS_WITH_A_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env",
                             "--super-prefix", "--list-cmds", "--attr-source"}  # fmt: skip
# git commands that change only this copy, or only read. Others, such as push, and any alias, wait for a person.
_GIT_LOCAL = frozenset(
    [
        "add",
        "am",
        "annotate",
        "apply",
        "archive",
        "bisect",
        "blame",
        "branch",
        "bundle",
        "cat-file",
        "check-attr",
        "check-ignore",
        "check-ref-format",
        "checkout",
        "cherry",
        "cherry-pick",
        "clean",
        "clone",
        "commit",
        "commit-graph",
        "config",
        "count-objects",
        "describe",
        "diff",
        "diff-files",
        "diff-index",
        "diff-tree",
        "difftool",
        "fetch",
        "for-each-ref",
        "format-patch",
        "fsck",
        "gc",
        "grep",
        "hash-object",
        "help",
        "init",
        "log",
        "ls-files",
        "ls-remote",
        "ls-tree",
        "maintenance",
        "merge",
        "merge-base",
        "merge-file",
        "mergetool",
        "mv",
        "name-rev",
        "notes",
        "pull",
        "range-diff",
        "read-tree",
        "rebase",
        "reflog",
        "remote",
        "repack",
        "replace",
        "rerere",
        "reset",
        "restore",
        "rev-list",
        "rev-parse",
        "revert",
        "rm",
        "shortlog",
        "show",
        "show-branch",
        "show-ref",
        "sparse-checkout",
        "stash",
        "status",
        "stripspace",
        "submodule",
        "switch",
        "symbolic-ref",
        "tag",
        "update-index",
        "update-ref",
        "var",
        "verify-commit",
        "verify-tag",
        "version",
        "whatchanged",
        "worktree",
        "write-tree",
    ]
)
_GIT_LFS_READS = {"ls-files", "status", "env", "version", "fetch", "pull", "checkout", "track", "untrack"}
# Options that make git config change settings beyond this copy, such as a global alias or credential helper.
_GIT_CONFIG_BEYOND = ("--global", "--system", "--file", "-f", "--blob", "--worktree")


def _words(args: list[str], with_a_value: set[str]) -> tuple[list[str], list[str]]:
    """(the command's own words before its arguments, every option seen), skipping options and their values."""
    words: list[str] = []
    options: list[str] = []
    at = 0
    while at < len(args):
        word = args[at]
        if word in with_a_value:
            options += args[at : at + 2]
            at += 2
            continue
        if word.startswith("-"):
            options.append(word)
            at += 1
            continue
        words.append(word)
        at += 1
    return words, options


def gh_changes_something(args: list[str]) -> bool:
    words, _ = _words(args, _GH_OPTIONS_WITH_A_VALUE)
    if not words:
        return False  # gh --version, gh --help
    group = words[0].lower()
    if group == "api":
        return _gh_api_writes(args, words)
    if group == "auth":
        # `gh auth token`, or status with -t, would print the token for the model to see.
        shows = any(a in ("-t", "--show-token") or (a.startswith("-") and not a.startswith("--") and "t" in a)
                    for a in args)  # fmt: skip
        return words[1:2] != ["status"] or shows
    if group not in _GH_READS:
        return True
    reads = _GH_READS[group]
    return reads is not None and len(words) > 1 and words[1].lower() not in reads


def _gh_api_options(args: list[str]) -> tuple[str, list[str], bool]:
    """(the method, each field's value, whether --input is given), read as gh reads its options."""
    method, fields, given_input = "", [], False
    at = 0
    while at < len(args):
        arg = args[at]
        if arg.startswith("--"):
            name, _, attached = arg.partition("=")
            if name in ("--method", "--field", "--raw-field", "--input") and not attached and at + 1 < len(args):
                attached, at = args[at + 1], at + 1
            if name == "--method":
                method = attached
            elif name in ("--field", "--raw-field"):
                fields.append(attached)
            elif name == "--input":
                given_input = True
        elif arg.startswith("-") and len(arg) > 1:
            letters = arg[1:]
            for place, letter in enumerate(letters):
                if letter in _GH_API_VALUED:
                    value = letters[place + 1 :]
                    if not value and at + 1 < len(args):
                        value, at = args[at + 1], at + 1
                    if letter == "X":
                        method = value
                    elif letter in "fF":
                        fields.append(value)
                    break
        at += 1
    return method, fields, given_input


def _gh_api_writes(args: list[str], words: list[str]) -> bool:
    method, fields, given_input = _gh_api_options(args)
    if len(words) > 1 and words[1].strip("/").lower() == "graphql":
        # A query read from a file or stdin can't be seen, so it may be a mutation.
        from_a_file = any(value.partition("=")[2].startswith("@") for value in fields)
        return given_input or from_a_file or any("mutation" in arg.lower() for arg in args)
    if method:
        return method.upper() != "GET"
    return bool(fields) or given_input


def git_changes_something(args: list[str]) -> bool:
    at = 0
    while at < len(args):
        word = args[at]
        lowered = word.lower()
        if "alias." in lowered:
            return True  # an alias set on the command line could be anything
        if word in _GIT_OPTIONS_WITH_A_VALUE:
            if at + 1 < len(args) and "alias." in args[at + 1].lower():
                return True
            at += 2
            continue
        if word.startswith("-"):
            at += 1
            continue
        rest = [w.lower() for w in args[at + 1 :] if not w.startswith("-")]
        if lowered == "lfs":
            return rest[:1] != [] and rest[0] not in _GIT_LFS_READS
        if lowered == "config":
            return any(a == o or a.startswith(o + "=") for a in args[at + 1 :] for o in _GIT_CONFIG_BEYOND)
        if lowered == "subtree":
            return "push" in rest
        return lowered not in _GIT_LOCAL
    return False  # git, git --version


def changes_something(name: str, args: list[str]) -> bool:
    if name == "gh":
        return gh_changes_something(args)
    if name == "git":
        return git_changes_something(args)
    return True


def held_command(name: str, args: list[str], cwd: Path, workspace: Path) -> str:
    """The command as a person would run it later, from the run's workspace."""
    command = shlex.join([name, *args])
    try:
        relative = cwd.resolve().relative_to(workspace.resolve())
    except ValueError:
        return command  # typed outside the workspace, such as in the step's own folder, which is gone by then
    return command if str(relative) == "." else f"cd {shlex.quote(relative.as_posix())} && {command}"


def main(argv: list[str]) -> int:
    company, run_id, role, workspace, real, *args = argv
    name = Path(real).stem.lower()
    if not changes_something(name, args):
        return subprocess.call([real, *args])
    from ..cli import _outside_orchestrator

    command = held_command(name, args, Path.cwd(), Path(workspace))
    orchestrator = _outside_orchestrator(Path(company), with_memory=False)
    try:
        number = orchestrator.hold_command(run_id, role, command)
    finally:
        orchestrator.close()
    print(
        f"Not run: this may change something outside, so it waits for a person as #{number} (trypro held). "
        "Carry on without it, and say in your submission that it's waiting.",
        file=sys.stderr,
    )
    return 3


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main(sys.argv[1:]))
