"""How a shell command reads against gantry's allow, ask and deny globs: the one matcher.

Used by guard.py (Codex and Pi) and by Claude Code's compound-command-allow.py. A glob is the
text inside Claude Code's `Bash(...)`: `ls *`, `git push *main*`. `ls *` is matched against
the start of a command part and also covers a bare `ls`, as Claude Code's own matching does.
"""

import json
import shlex
from fnmatch import fnmatchcase

PUNCT = "();<>|&\n"
SPLIT = {"&&", "||", ";", "|", "&", "|&", "\n"}
HARMLESS = {(">&", "1"), (">&", "2"), (">", "/dev/null"), (">>", "/dev/null")}


def without_comments(cmd):
    """cmd with its comments cut, or None when it runs a command substitution.

    shlex drops quotes, so the two things that depend on them are read here first. A `#`
    starts a comment only at the start of an unquoted word, so `a#b`, `"#"` and `${#x}` are
    text. `$(` or a backtick runs a command unless it is single-quoted or escaped.
    """
    out, single, double, word_start, i = [], False, False, True, 0
    while i < len(cmd):
        c = cmd[i]
        if single:
            single = c != "'"
        elif c == "\\":
            out.append(cmd[i:i + 2])
            i, word_start = i + 2, False
            continue
        elif c == "`" or cmd.startswith("$(", i):
            return None
        elif double:
            double = c != '"'
        elif c == "#" and word_start:
            while i < len(cmd) and cmd[i] != "\n":
                i += 1
            continue
        elif c in "'\"":
            single, double = c == "'", c == '"'
        out.append(c)
        word_start = not (single or double) and c in " \t\n;&|()<>"
        i += 1
    return "".join(out)


def parts(cmd):
    """The command split at its operators, or None when it is not sure what would run.

    Unsure means a command substitution, a subshell, a file redirect other than HARMLESS, a
    heredoc, or an unclosed quote.
    """
    cmd = without_comments(cmd)
    if cmd is None:
        return None
    lexer = shlex.shlex(cmd, posix=True, punctuation_chars=PUNCT)
    lexer.whitespace_split, lexer.whitespace, lexer.commenters = True, " \t\r", ""
    try:
        tokens = list(lexer)
    except ValueError:  # an unclosed quote
        return None
    found, words = [], []
    pairs = zip(tokens, tokens[1:] + [""])
    for token, following in pairs:
        if token in SPLIT:
            found.append(words)
            words = []
        elif (token, following) in HARMLESS:
            next(pairs)  # the redirect's target goes with it
        elif set(token) <= set(PUNCT):
            return None
        else:
            words.append(token)
    found.append(words)
    return [" ".join(w) for w in found if w]


def matches(part, patterns):
    # The appended space lets `ls *` cover a bare `ls` but not `lsof`.
    return any(fnmatchcase(part + " ", pattern + "*") for pattern in patterns)


def normalize(glob):
    """The glob as matches() reads it: a trailing `*` or `:*` is implied."""
    return glob.removesuffix("*").removesuffix(":")


def load(path):
    """`{"allow": [...], "ask": [...], "deny": [...]}` from a permissions.json, normalized."""
    data = json.loads(path.read_text())
    return {key: [normalize(g) for g in data.get(key, [])] for key in ("allow", "ask", "deny")}


def classify(command, allow, ask, deny):
    """("deny" | "ask" | "allow", reason) for `command`. Deny wins over ask, ask over allow.

    A command whose parts cannot all be seen (a `$(...)`, a subshell, a file redirect, a
    heredoc, an unclosed quote) is denied when a deny glob appears anywhere in its text, and
    asks otherwise. A command with no allow rule for every part asks.
    """
    split = parts(command)
    if not split:
        hit = next((g for g in deny if fnmatchcase(command + " ", "*" + g + "*")), None)
        if hit is not None:
            return "deny", f"matches deny `{hit}*`"
        return "ask", "not every command it runs can be read"
    for key in ("deny", "ask"):
        globs = deny if key == "deny" else ask
        for part in split:
            hit = next((g for g in globs if matches(part, [g])), None)
            if hit is not None:
                return key, f"`{part}` matches {key} `{hit}*`"
    if all(matches(part, allow) for part in split):
        return "allow", "every part matches an allow rule"
    return "ask", "no allow rule matches every part"
