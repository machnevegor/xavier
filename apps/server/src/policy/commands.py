"""Shell command lines split into operations for policy rules.

A line splits on ``&&``, ``||``, ``;``, ``|`` and newlines into simple commands, each a ``command``
resource in normalized form. Anything the split cannot see through (substitution, expansion,
redirection, heredoc, subshell, background job, unbalanced quotes) makes the line opaque: it is
one operation and never read-only. Quoted separators split too, which only adds operations.
"""

import posixpath
import shlex
from dataclasses import dataclass

from src.policy.urls import WORKSPACE_ROOT

_PUNCTUATION = "();<>|&\n"
_SEPARATORS = frozenset({"", ";", "&&", "||", "|"})
_EXPANSIONS = ("$", "`")
_READERS = frozenset({"cat", "grep", "head", "ls", "pwd", "tail", "wc"})
_FIND_ACTIONS = ("-delete", "-exec", "-fls", "-fprint", "-ok")
_GIT_READS = frozenset({"diff", "log", "show", "status"})
_GIT_RUNNERS = ("--ext-diff", "--output", "--textconv")
"""Options that make a reading git command write a file or run a configured program."""


@dataclass(frozen=True, slots=True)
class CommandLine:
    """The operations of a command line and whether all of them only read."""

    operations: tuple[str, ...]
    read_only: bool


def analyze(command: str, cwd: str) -> CommandLine:
    """Split ``command``, run in ``cwd``, into operations and rate them."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=_PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    operations: list[list[str]] = [[]]
    try:
        for token in lexer:
            if token and all(char in _PUNCTUATION for char in token):
                if token.replace("\n", "") not in _SEPARATORS:
                    return _opaque(command)
                operations.append([])
            elif any(expansion in token for expansion in _EXPANSIONS):
                return _opaque(command)
            else:
                operations[-1].append(token)
    except ValueError:
        return _opaque(command)
    words = [operation for operation in operations if operation]
    if not words:
        return _opaque(command)
    return CommandLine(
        operations=tuple(dict.fromkeys(shlex.join(operation) for operation in words)),
        read_only=all(_reads(operation, cwd) for operation in words),
    )


def _opaque(command: str) -> CommandLine:
    return CommandLine(operations=(command.strip(),), read_only=False)


def _reads(words: list[str], cwd: str) -> bool:
    name, args = words[0], words[1:]
    if name in _READERS:
        return True
    match name:
        case "rg":
            return not any(arg.startswith("--pre") for arg in args)
        case "find":
            return not any(arg.startswith(_FIND_ACTIONS) for arg in args)
        case "git":
            return (
                bool(args)
                and args[0] in _GIT_READS
                and not any(arg.startswith(_GIT_RUNNERS) for arg in args)
            )
        case "cd":
            return len(args) == 1 and _inside_workspace(cwd, args[0])
    return False


def _inside_workspace(cwd: str, target: str) -> bool:
    if target.startswith(("~", "-")):
        return False
    path = posixpath.normpath(posixpath.join(cwd, target))
    return path == WORKSPACE_ROOT or path.startswith(WORKSPACE_ROOT + "/")
