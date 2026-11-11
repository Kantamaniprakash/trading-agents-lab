"""Stamp, lint and package version 2 of the agent skill.

Usage:
  python skill/build_skill.py           stamp the scripts, check everything, write the ZIP
  python skill/build_skill.py --check   write nothing; exit 1 when a stamp or a known answer
                                        is stale, or a check fails

Writes: dist/trading-agents-desk.zip, one top-level folder named after the skill.

The skill ships four standard-library scripts and installs nothing when it runs, so there is
no release tag to pin and the build makes no network request. What it guards instead:

- the integrity stamps of docs/skill-v2/CONTRACTS.md section 1 (SELF in each script, SIBLINGS
  in brief.py) are the ones the shipped text gives;
- the known answers (KAT in brief.py and render.py) are what each script's own known-answer
  function returns on the Python that runs the build;
- every packaged file passes the lint: Python 3.9 grammar, the import allowlist, the open()
  rules, readable source (lines of at most 100 characters, one statement per line), no
  backslash-u escape that a file-writing tool could decode, size caps, ASCII with LF endings,
  and none of the patterns a marketplace scan fails a skill for;
- the stamped copy passes its own preflight on the Python that runs the build.

A known answer is there to catch a different result on a buyer's Python or a damaged copy.
It is not a regression test: the build stamps whatever the script computes, and the tests in
tests/skill_v2 are what hold the scripts to their contract.

The build is deterministic: the same sources give the same bytes on any operating system.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import subprocess
import sys
import tokenize
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = Path(__file__).resolve().parent / "trading-agents-desk"
DIST_DIR = REPO_ROOT / "dist"

SKILL_VERSION = "2.0.0"
# The stamp order matters: brief.py holds the hashes of the other three, in this order.
SIBLING_NAMES = ("get_data.py", "render.py", "page.py")
SCRIPT_NAMES = SIBLING_NAMES + ("brief.py",)
# The scripts that hold a known answer: a line KAT = "<12 hex>" and a function kat().
KAT_NAMES = ("render.py", "brief.py")
PACKAGE_FILES = (
    "LICENSE.txt", "SKILL.md", "references/lab.md", "references/roles.md",
    "scripts/brief.py", "scripts/get_data.py", "scripts/page.py", "scripts/render.py",
)
PLACEHOLDER = "0" * 12

MAX_SCRIPT_BYTES = 20000
MAX_LINE_CHARS = 100
MAX_SKILL_MD_BYTES = 12000
MAX_REFERENCE_BYTES = 20000

_BASE_IMPORTS = frozenset({"sys", "json", "math", "re", "hashlib", "datetime", "html"})
# `time` is for the one pause before the one new attempt after a rate limit.
_NETWORK_IMPORTS = frozenset({"urllib.request", "urllib.parse", "urllib.error", "time"})
ALLOWED_IMPORTS = {
    "get_data.py": _BASE_IMPORTS | _NETWORK_IMPORTS,
    "brief.py": _BASE_IMPORTS,
    "render.py": _BASE_IMPORTS,
    "page.py": _BASE_IMPORTS,
}
# How many open() calls each script may hold, by mode. Every path comes from the command
# line, or is the script's own file.
#   get_data.py  reads no files; writes the bundle
#   brief.py     reads a script to hash it (itself, and the others in the preflight) and its
#                one input; writes the brief
#   render.py    one reading function for itself, the brief, the draft and a saved report; one
#                writing statement, in a loop over its two output paths
#   page.py      reads itself and, with one function, its two inputs; writes the one page
ALLOWED_OPENS = {
    "get_data.py": {"w": 1},
    "brief.py": {"rb": 2, "w": 1},
    "render.py": {"rb": 1, "w": 1},
    "page.py": {"rb": 2, "w": 1},
}
_FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "compile", "__import__", "breakpoint", "os", "subprocess", "socket",
    "importlib", "environ", "getenv", "putenv",
})
_FORBIDDEN_ATTRIBUTES = frozenset({
    "eval", "exec", "__import__", "system", "popen", "environ", "getenv", "putenv",
})

REPO_URL = "https://github.com/Kantamaniprakash/trading-agents-lab"
DATA_HOST_URL = "https://query1.finance.yahoo.com"

_NAME_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_MAX_NAME_CHARS = 64
_MAX_DESCRIPTION_CHARS = 1024
_FRONTMATTER_KEYS = frozenset({"name", "description", "license"})
_FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
# A plain YAML scalar must not start with an indicator character, and ": " or
# " #" inside it would be read as a mapping or a comment.
_YAML_UNSAFE_START = tuple("!&*[]{}>|@`'\"%#,?-")
# Plain scalars that YAML resolves to something other than a string.
_YAML_NON_STRING = re.compile(
    r"(?i)(true|false|yes|no|on|off|null|~|y|n|=|<<"
    r"|[-+]?(\d[\d_]*)?\.?\d+([eE][-+]?\d+)?|[-+]?\d[\d_]*|\.\d[\d_]*([eE][-+]?\d+)?"
    r"|[-+]?\d[\d_]*\.[\d_]*([eE][-+]?\d+)?"
    r"|[-+]?0b[01_]+|[-+]?0x[0-9a-f_]+|[-+]?0o?[0-7_]+"
    r"|[-+]?\d[\d_]*(:[0-5]?\d)+(\.[\d_]*)?"
    r"|[-+]?\.(inf|nan)|\d{4}-\d{1,2}-\d{1,2}([Tt ].*)?)"
)

_SELF_LINE = re.compile(rb'^SELF = "[0-9a-f]{12}"(?=\r?\n)', re.MULTILINE)
_KAT_LINE = re.compile(rb'^KAT = "[0-9a-f]{12}"(?=\r?\n)', re.MULTILINE)
_SIBLINGS_LINE = re.compile(rb"^SIBLINGS = \{[^\r\n]*\}(?=\r?\n)", re.MULTILINE)
# The SIBLINGS line in the contract's form: the three names in stamp order.
_SIBLINGS_FORM = re.compile(
    (r"(?m)^SIBLINGS = \{%s\}\r?$" % ", ".join(
        '"%s": "[0-9a-f]{12}"' % re.escape(name) for name in SIBLING_NAMES)).encode("ascii"))

# What a marketplace scan fails a skill for, searched in every packaged file.
_FAIL_PATTERNS = tuple((label, re.compile(pattern)) for label, pattern in (
    ("os.system", r"\bos\s*\.\s*system\b"),
    ("shell=True", r"\bshell\s*=\s*True\b"),
    ("subprocess", r"\bsubprocess\b"),
    ("chmod 777", r"(?i)\bchmod\s+(?:-\S+\s+)*[0-7]?777\b|\b0o?777\b"),
    ("deletion from a root path",
     r"\brm\s+(?:-[A-Za-z-]+\s+)+[\"']?(?:/|~|\$HOME\b|\$\{HOME\}|[A-Za-z]:[\\/])"
     r"(?![A-Za-z0-9_.\\/-])"
     r"|\brmtree\(\s*r?[\"'](?:/|~|[A-Za-z]:[\\/]{0,2})[\"']"
     r"|(?i:\b(?:rd|rmdir|del|erase)\s+/s\b)|(?i:\bformat\s+[a-z]:)"),
    ("a download piped into a shell",
     r"(?i)\b(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b[^\n|]*\|\s*"
     r"(?:sudo\s+)?(?:(?:ba|z|k|da|fi|c)?sh|python[0-9.]*|iex|invoke-expression)\b"),
    ("eval", r"\beval\b"),
    ("exec", r"\bexec\b"),
    ("a secret-looking string",
     r"(?i:\b(?:api[_-]?key|secret|passwd|password|token|bearer|authorization|private[_-]?key)"
     r"\b\s*[:=]\s*[\"'][^\"'\s]{8,}[\"'])"
     r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
     r"|\b(?:sk|pk|rk)[-_][A-Za-z0-9_-]{20,}"
     r"|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{30,}|\bxox[abprs]-[A-Za-z0-9-]{10,}"
     r"|\bAIza[0-9A-Za-z_-]{30,}|\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}"
     r"|\b[0-9a-fA-F]{32,}\b"
     r"|(?<![A-Za-z0-9+=_-])(?=[A-Za-z0-9+=_-]*[0-9])(?=[A-Za-z0-9+=_-]*[a-z])"
     r"(?=[A-Za-z0-9+=_-]*[A-Z])[A-Za-z0-9+=_-]{40,}"),
    ("an install command",
     r"(?i)\b(?:pip3?|pipx|uv|conda|mamba|npm|pnpm|yarn|brew|apt|apt-get|dnf|yum|apk|choco"
     r"|winget|gem|cargo|poetry|easy_install)\s+(?:-\S+\s+)*(?:install|add)\b"
     r"|\bpython[0-9.]*\s+-m\s+(?:pip|venv|ensurepip)\b|\bgit\s+clone\b|\bnpx\s"),
    ("an archive address",
     r"(?i)archive/refs/|codeload\.github\.com|/releases/download/"
     r"|https?://[^\s\"'<>)\]]*\.(?:zip|whl|tar|tgz|gz|bz2|xz|7z|exe|msi|dmg|pkg|deb|rpm|sh)"
     r"(?![A-Za-z0-9])"),
))
# Wording a scan reads as hiding something or as a risky command, as lower-case substrings.
_FAIL_PHRASES = (
    "ignore previous", "do not tell the user", "don't tell the user", "secretly",
    "pip install", "curl", "sudo", "base64",
)
_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s\"'<>)\]]+", re.IGNORECASE)
# A script's stamp line. Its hash reads a line of exactly this form as the one word SELF,
# whatever stamp the line holds. The line is not left out: it keeps its place in the hash, so
# a second, a moved or a missing stamp line changes it. Two things follow from the rule and
# stand as they are (tests/skill_v2/test_portable.py holds both). A copy with the bare word
# SELF in place of the line hashes as the stamped copy: it passes the preflight and then
# stops with a NameError. And a line SELF = "000000000000" under the stamp line makes the
# copy a development copy when it runs (it warns and works); the preflight refuses such a
# copy of get_data.py, render.py or page.py.
_STAMP_LINE = re.compile(rb'SELF = "[0-9a-f]{12}"')
# An escape that names a character by its code or its name. Some agent hosts decode these
# while they write a script out, which changes the file and fails its integrity check.
_CODED_ESCAPE = re.compile(r"\\(?:u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|N\{)")
# Words a marketplace scan only warns about. The build reports the count and does not fail.
WARN_WORDS = ("fetch",)


def _skill_dir(skill_dir: Path | None) -> Path:
    # Read at call time, so a test can point the module at a copy.
    return SKILL_DIR if skill_dir is None else skill_dir


# --- frontmatter -----------------------------------------------------------------------

def read_frontmatter(skill_md: Path) -> dict[str, str]:
    """Parse the flat ``key: value`` YAML frontmatter at the top of a SKILL.md.

    Only single-line plain scalars are accepted. The common forms a YAML
    parser would read as something other than that text are rejected:
    indicator characters, ``: `` and `` #`` inside a value, tabs, a key with
    no space after its colon, and values YAML resolves to a boolean, null,
    number or date. It is a guard against surprises, not a YAML validator.
    """
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{skill_md}: missing '---' frontmatter opener on line 1")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        key, sep, value = line.partition(":")
        if not sep or not key.strip() or key[0].isspace() or "\t" in line \
                or (value and not value.startswith(" ")):
            raise ValueError(f"{skill_md}: unsupported frontmatter line: {line!r}")
        value = value.strip()
        if not value:
            raise ValueError(f"{skill_md}: frontmatter key {key.strip()!r} has no value")
        if value.startswith(_YAML_UNSAFE_START) or ": " in value or " #" in value \
                or value.endswith(":"):
            raise ValueError(
                f"{skill_md}: the value of {key.strip()!r} is not a plain YAML scalar "
                "(it starts with an indicator character, or contains ': ' or ' #')"
            )
        if _YAML_NON_STRING.fullmatch(value):
            raise ValueError(
                f"{skill_md}: YAML would not read the value of {key.strip()!r} as text "
                f"({value!r} is a boolean, null, number or date to a YAML parser)"
            )
        if key.strip() in fields:
            raise ValueError(f"{skill_md}: duplicate frontmatter key {key.strip()!r}")
        fields[key.strip()] = value
    raise ValueError(f"{skill_md}: frontmatter is never closed with '---'")


def _check_frontmatter(skill_dir: Path) -> None:
    fields = read_frontmatter(skill_dir / "SKILL.md")
    name = fields.get("name", "")
    if not _NAME_RE.fullmatch(name) or len(name) > _MAX_NAME_CHARS:
        raise ValueError(
            f"name {name!r} must be lowercase letters, digits and hyphens, "
            f"at most {_MAX_NAME_CHARS} characters"
        )
    if name != skill_dir.name:
        raise ValueError(f"name {name!r} must match the folder name {skill_dir.name!r}")
    description = fields.get("description", "")
    if not description:
        raise ValueError("description is required")
    if len(description) > _MAX_DESCRIPTION_CHARS:
        raise ValueError(
            f"description is {len(description)} characters; "
            f"the limit is {_MAX_DESCRIPTION_CHARS}"
        )
    extra = sorted(set(fields) - _FRONTMATTER_KEYS)
    if extra:
        raise ValueError(
            f"frontmatter key {extra[0]!r} is not portable across agent hosts; "
            f"use only {', '.join(sorted(_FRONTMATTER_KEYS))}"
        )


# --- integrity stamps ------------------------------------------------------------------

def script_hash(data: bytes) -> str:
    """The contract's hash of a script: SHA-256 over its bytes with LF line endings and no
    trailing whitespace, each line that is exactly ``SELF = "<12 hex>"`` read as the one
    word ``SELF``; the first 12 hex digits.

    The stamp line keeps its place in the hash, so a second, a moved or a missing stamp
    line changes it. Any other line counts as it is, whatever it starts with, and so does
    a byte-order mark: a file that holds either is an altered file.

    This is the rule brief.py applies to itself and its siblings, and render.py and page.py
    apply to themselves. The four must agree, and the tests hold them to it.
    """
    text = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    lines = [line.rstrip() for line in text.split(b"\n")]
    kept = [b"SELF" if _STAMP_LINE.fullmatch(line) else line for line in lines]
    return hashlib.sha256(b"\n".join(kept).rstrip()).hexdigest()[:12]


def known_answer(name: str, data: bytes) -> str:
    """What the script's own known-answer function returns on the Python that runs the build.

    The script's text is run as a module, not as a command: it ends in an
    ``if __name__ == "__main__"`` guard, so only its definitions are made. The answer then
    comes from the code that ships and not from a second copy of that code kept here.
    """
    namespace: dict = {"__name__": "known_answer_of_" + name[:-3]}
    try:
        exec(compile(data.decode("ascii"), name, "exec"), namespace)
        answer = namespace["kat"]()
    except (Exception, SystemExit) as exc:
        raise ValueError(f"scripts/{name}: its known-answer function could not be run on this "
                         f"Python ({type(exc).__name__}: {exc})") from None
    if not isinstance(answer, str) or not re.fullmatch("[0-9a-f]{12}", answer):
        raise ValueError(f"scripts/{name}: its known-answer function returned {answer!r}, not "
                         "12 hex digits: the script's built-in case fails on this Python")
    return answer


def _set_line(data: bytes, pattern: re.Pattern, line: str, name: str) -> bytes:
    if len(pattern.findall(data)) != 1:
        what = pattern.pattern.decode("ascii").split(" = ")[0].lstrip("^")
        raise ValueError(f"scripts/{name}: expected exactly one {what} line in the contract's form")
    return pattern.sub(lambda _: line.encode("ascii"), data)


def stamped_scripts(skill_dir: Path | None = None) -> dict[str, bytes]:
    """The four scripts as they read once stamped, in stamp order.

    Each line is written before the hash that covers it. A known answer (KAT) is part of the
    text its script hashes, so it goes in first. get_data.py, render.py and page.py then get
    their own hash. brief.py takes the three hashes in SIBLINGS, and its own hash last,
    because SIBLINGS is part of the text it hashes. Stamping twice changes nothing.
    """
    skill_dir = _skill_dir(skill_dir)
    scripts = skill_dir / "scripts"
    out: dict[str, bytes] = {}
    for name in SCRIPT_NAMES:
        data = (scripts / name).read_bytes()
        if name in KAT_NAMES:
            data = _set_line(data, _KAT_LINE, f'KAT = "{known_answer(name, data)}"', name)
        if name == "brief.py":
            siblings = json.dumps({sibling: script_hash(out[sibling]) for sibling in SIBLING_NAMES})
            data = _set_line(data, _SIBLINGS_LINE, f"SIBLINGS = {siblings}", name)
        out[name] = _set_line(data, _SELF_LINE, f'SELF = "{script_hash(data)}"', name)
    return out


def stale_stamps(skill_dir: Path | None = None) -> list[str]:
    """Names of the scripts whose stamp lines are not the ones their text gives: a stale hash
    (SELF, SIBLINGS) or a stale known answer (KAT)."""
    skill_dir = _skill_dir(skill_dir)
    scripts = skill_dir / "scripts"
    return [name for name, data in stamped_scripts(skill_dir).items()
            if (scripts / name).read_bytes() != data]


def stale_known_answers(skill_dir: Path | None = None) -> list[str]:
    """Names of the scripts whose KAT line is not what their kat() returns on this Python."""
    scripts = _skill_dir(skill_dir) / "scripts"
    stale = []
    for name in KAT_NAMES:
        data = (scripts / name).read_bytes()
        held = _KAT_LINE.findall(data)
        if held != [f'KAT = "{known_answer(name, data)}"'.encode("ascii")]:
            stale.append(name)
    return stale


def stamp(skill_dir: Path | None = None) -> list[str]:
    """Write fresh stamps into the scripts; returns the names of the files that changed."""
    skill_dir = _skill_dir(skill_dir)
    scripts = skill_dir / "scripts"
    changed = []
    for name, data in stamped_scripts(skill_dir).items():
        if (scripts / name).read_bytes() != data:
            (scripts / name).write_bytes(data)
            changed.append(name)
    return changed


# --- lint ------------------------------------------------------------------------------

def _packaged_files(skill_dir: Path) -> list[Path]:
    """Regular files to ship, sorted; hidden files, hidden folders, caches and
    symlinks are left out."""
    files = []
    for path in skill_dir.rglob("*"):
        relative = path.relative_to(skill_dir)
        if path.is_symlink() or not path.is_file():
            continue
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        files.append(path)
    # Sort on the archive name, not the native path: Windows and POSIX order
    # separators and letter case differently, and member order is part of the
    # bytes.
    return sorted(files, key=lambda path: path.relative_to(skill_dir).as_posix())


def shipped_bytes(path: Path) -> bytes:
    """The file as the archive holds it. A Windows checkout may hold CRLF line endings."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def _lint_text(name: str, data: bytes) -> list[str]:
    """Problems with one packaged file's bytes: encoding, layout, size and fail patterns."""
    problems = []
    if not data.endswith(b"\n") or data.endswith(b"\n\n"):
        problems.append(f"{name}: must end with exactly one newline")
    for number, line in enumerate(data.split(b"\n"), 1):
        if b"\t" in line:
            problems.append(f"{name}:{number}: holds a tab")
        elif any(byte < 32 or byte > 126 for byte in line):
            problems.append(f"{name}:{number}: holds a character that is not printable ASCII "
                            "(or a carriage return without a line feed)")
        elif line != line.rstrip():
            problems.append(f"{name}:{number}: has trailing whitespace")
    if name.startswith("scripts/"):
        limit = MAX_SCRIPT_BYTES
    elif name == "SKILL.md":
        limit = MAX_SKILL_MD_BYTES
    else:
        limit = MAX_REFERENCE_BYTES
    if len(data) > limit:
        problems.append(f"{name}: {len(data)} bytes; the limit is {limit}")

    text = data.decode("ascii", "replace")
    for number, line in enumerate(text.split("\n"), 1):
        for label, pattern in _FAIL_PATTERNS:
            if pattern.search(line):
                problems.append(f"{name}:{number}: matches a pattern a marketplace scan fails: "
                                f"{label}")
        lowered = line.lower()
        for phrase in _FAIL_PHRASES:
            if phrase in lowered:
                problems.append(f"{name}:{number}: holds wording a marketplace scan fails: "
                                f"{phrase!r}")
        for match in _URL.finditer(line):
            url = match.group().rstrip(".,;:")
            if url != REPO_URL and url != DATA_HOST_URL \
                    and not url.startswith(DATA_HOST_URL + "/"):
                problems.append(f"{name}:{number}: names an address other than the data host "
                                f"and the repository: {url}")
    return problems


def _lint_layout(label: str, text: str, tree: ast.AST) -> list[str]:
    """Problems with how a script is laid out. The contract wants source a person can read:
    lines of at most 100 characters and one statement per line, with no exception for the
    style sheet inside page.py (it may be compact, but its lines are no longer)."""
    problems = []
    for number, line in enumerate(text.split("\n"), 1):
        if len(line) > MAX_LINE_CHARS:
            problems.append(f"{label}:{number}: is {len(line)} characters wide; a line holds at "
                            f"most {MAX_LINE_CHARS}")
    # Tokens, not text: a semicolon inside a string literal or a comment is not a statement.
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.OP and token.string == ";":
                problems.append(f"{label}:{token.start[0]}: packs statements with a semicolon; "
                                "one statement per line")
    except (tokenize.TokenError, SyntaxError) as exc:
        problems.append(f"{label}: cannot be read token by token ({exc})")
    # A body on its header's line ("if x: return") is two statements on one line as well.
    starts: dict[int, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            starts[node.lineno] = starts.get(node.lineno, 0) + 1
    for number in sorted(starts):
        if starts[number] > 1 and not any(f"{label}:{number}: packs" in x for x in problems):
            problems.append(f"{label}:{number}: holds more than one statement; one statement "
                            "per line")
    return problems


def _lint_script(name: str, data: bytes) -> list[str]:
    """Problems with one script's code: grammar, layout, imports, file access and its stamp
    lines."""
    label = f"scripts/{name}"
    text = data.decode("ascii", "replace")
    try:
        tree = ast.parse(text, filename=name, feature_version=(3, 9))
    except SyntaxError as exc:
        return [f"{label}:{exc.lineno}: is not Python 3.9 grammar ({exc.msg})"]
    problems = _lint_layout(label, text, tree)
    for number, line in enumerate(text.split("\n"), 1):
        if _CODED_ESCAPE.search(line):
            problems.append(f"{label}:{number}: holds a backslash-u or backslash-N escape; some "
                            "hosts decode it while writing the script out, so spell the "
                            "character chr(n)")
    opens: dict[str, int] = {}
    open_calls = set()
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_IMPORTS[name]:
                    problems.append(f"{label}:{line}: imports {alias.name}, which is not on "
                                    "this script's allowlist")
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ALLOWED_IMPORTS[name]:
                problems.append(f"{label}:{line}: imports from {node.module or '.'}, which is "
                                "not on this script's allowlist")
        elif isinstance(node, ast.JoinedStr):
            # The 3.9 grammar check cannot see the f-string forms that only 3.12 accepts.
            problems.append(f"{label}:{line}: uses an f-string; the shipped scripts format "
                            "with % so the 3.9 grammar check stays sound")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "open":
            open_calls.add(id(node.func))
            mode = node.args[1].value if len(node.args) > 1 \
                and isinstance(node.args[1], ast.Constant) else None
            if mode not in ("rb", "w") or isinstance(node.args[0], ast.Constant):
                problems.append(f"{label}:{line}: open() must take a path from the command "
                                "line and the literal mode 'rb' or 'w'")
            else:
                opens[mode] = opens.get(mode, 0) + 1
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            problems.append(f"{label}:{line}: uses {node.id}, which a shipped script may not")
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRIBUTES:
            problems.append(f"{label}:{line}: uses .{node.attr}, which a shipped script may not")
        elif isinstance(node, ast.Name) and node.id == "open" and id(node) not in open_calls:
            problems.append(f"{label}:{line}: refers to open without calling it")
    if not problems and opens != ALLOWED_OPENS[name]:
        problems.append(f"{label}: opens files as {opens}; the contract allows "
                        f"{ALLOWED_OPENS[name]}")

    version = re.findall(rb'(?m)^VERSION = "([^"\r\n]*)"\r?$', data)
    if version != [SKILL_VERSION.encode("ascii")]:
        problems.append(f"{label}: must hold one line VERSION = \"{SKILL_VERSION}\"")
    if len(_SELF_LINE.findall(data)) != 1 or data.count(b"\nSELF = ") != 1:
        problems.append(f"{label}: must hold exactly one line SELF = \"<12 hex>\"")
    if name in KAT_NAMES and (len(_KAT_LINE.findall(data)) != 1
                              or data.count(b"\nKAT = ") != 1):
        problems.append(f"{label}: must hold exactly one line KAT = \"<12 hex>\"")
    if name == "brief.py" and not _SIBLINGS_FORM.search(data):
        problems.append(f"{label}: must hold one SIBLINGS line in the contract's form")
    return problems


def lint(skill_dir: Path | None = None, stamps: bool = True) -> list[str]:
    """Every problem with the packaged files, or an empty list.

    The stamps and the known answers are checked last, and only when nothing else is wrong
    (and not at all with ``stamps=False``, which is how the build lints before it stamps).
    """
    skill_dir = _skill_dir(skill_dir)
    files = _packaged_files(skill_dir)
    names = [path.relative_to(skill_dir).as_posix() for path in files]
    problems = [f"{name}: is missing; version 2 ships exactly {', '.join(PACKAGE_FILES)}"
                for name in PACKAGE_FILES if name not in names]
    problems += [f"{name}: is not part of the package; version 2 ships exactly "
                 f"{', '.join(PACKAGE_FILES)}" for name in names if name not in PACKAGE_FILES]
    for path, name in zip(files, names):
        if name not in PACKAGE_FILES:
            continue
        data = shipped_bytes(path)
        problems += _lint_text(name, data)
        if name.startswith("scripts/"):
            problems += _lint_script(path.name, data)
    if stamps and not problems:
        try:
            for name in stale_known_answers(skill_dir):
                problems.append(f"scripts/{name}: its known answer (KAT) is stale; run "
                                "python skill/build_skill.py to stamp it")
            for name in stale_stamps(skill_dir):
                problems.append(f"scripts/{name}: its integrity stamp is stale or missing; run "
                                "python skill/build_skill.py to stamp it")
        except ValueError as exc:
            problems.append(str(exc))
    return problems


def warn_words(skill_dir: Path | None = None) -> dict[str, int]:
    """How often each word a marketplace scan only warns about stands in the four scripts."""
    scripts = _skill_dir(skill_dir) / "scripts"
    text = b"".join((scripts / name).read_bytes() for name in SCRIPT_NAMES).decode(
        "ascii", "replace").lower()
    return {word: text.count(word) for word in WARN_WORDS if word in text}


def one_letter_names(text: str) -> list[str]:
    """The one-letter names a script binds outside the targets of loops and comprehensions.

    The contract wants names that say what they hold. A lint cannot judge a name, but it can
    count the ones that say nothing. The build reports the count for each script and the
    tests keep it from growing; brief.py and render.py were written before that rule and
    still hold many."""
    tree = ast.parse(text)
    in_loop: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.comprehension)):
            in_loop |= {id(name) for name in ast.walk(node.target)}
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) \
                and id(node) not in in_loop:
            found.add(node.id)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.FunctionDef):
            found.add(node.name)
    return sorted(name for name in found if len(name) == 1 and name != "_")


def validate(skill_dir: Path | None = None, stamps: bool = True) -> list[Path]:
    """Check the skill folder and return the files to package, sorted.

    Raises ``ValueError`` listing every problem found.
    """
    skill_dir = _skill_dir(skill_dir)
    if not (skill_dir / "SKILL.md").is_file():
        raise ValueError(f"{skill_dir}: SKILL.md not found")
    _check_frontmatter(skill_dir)
    problems = lint(skill_dir, stamps)
    if problems:
        raise ValueError("%d problem(s) in the package:\n  %s"
                         % (len(problems), "\n  ".join(problems)))
    return _packaged_files(skill_dir)


def self_test(skill_dir: Path | None = None) -> str:
    """Run the shipped preflight and each script's --version on this Python.

    Returns the preflight line. Raises ``ValueError`` when a script does not accept its own
    stamp, a known-answer test fails, the preflight does not name all four files as checked,
    or a script answers with anything but its version.
    """
    skill_dir = _skill_dir(skill_dir)
    scripts = skill_dir / "scripts"

    def run(name: str, *args: str) -> tuple[int, str, str]:
        done = subprocess.run([sys.executable, "-I", "-B", str(scripts / name), *args],
                              capture_output=True, timeout=120)
        return (done.returncode, done.stdout.decode("ascii", "replace").strip(),
                done.stderr.decode("ascii", "replace").strip())

    code, out, err = run("brief.py", "--preflight", str(scripts))
    wanted = "PREFLIGHT ok %s python %d.%d.%d checked %s" % (
        (SKILL_VERSION,) + sys.version_info[:3] + (" ".join(sorted(SCRIPT_NAMES)),))
    if code != 0 or err or out != wanted:
        raise ValueError(f"the shipped preflight failed (exit {code}): {err or out}")
    preflight = out
    for name in SCRIPT_NAMES:
        code, out, err = run(name, "--version")
        if code != 0 or err or out != f"trading-agents-desk {name} {SKILL_VERSION}":
            raise ValueError(f"scripts/{name} --version failed (exit {code}): {err or out}")
    return preflight


# --- the archive -----------------------------------------------------------------------

def build(skill_dir: Path | None = None, dist_dir: Path | None = None) -> Path:
    """Validate ``skill_dir``, run its preflight and write ``{dist_dir}/{skill name}.zip``."""
    skill_dir = _skill_dir(skill_dir)
    dist_dir = DIST_DIR if dist_dir is None else dist_dir
    files = validate(skill_dir)
    self_test(skill_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)
    out_path = dist_dir / f"{skill_dir.name}.zip"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            arcname = f"{skill_dir.name}/{path.relative_to(skill_dir).as_posix()}"
            info = zipfile.ZipInfo(arcname, date_time=_FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # same header whichever OS runs the build
            info.external_attr = 0o644 << 16
            archive.writestr(info, shipped_bytes(path))
    return out_path


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else list(argv)
    if args not in ([], ["--check"]):
        print("usage: python skill/build_skill.py [--check]", file=sys.stderr)
        sys.exit(2)
    try:
        if args:
            validate()
            print(f"check ok: stamps and known answers fresh, lint clean. {self_test()}")
            return
        # Lint before stamping: a script that is not sound should be reported as that, not
        # as a known-answer function that would not run.
        validate(stamps=False)
        for name in stamp():
            print(f"stamped scripts/{name}")
        out_path = build()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    with zipfile.ZipFile(out_path) as archive:
        for info in archive.infolist():
            print(f"  {info.file_size:>6}  {info.filename}")
    for word, count in warn_words().items():
        print(f"note: the scripts hold the word {word!r} {count} times; a marketplace scan "
              "may warn about it")
    for name in sorted(SCRIPT_NAMES):
        short = one_letter_names((SKILL_DIR / "scripts" / name).read_text(encoding="ascii"))
        print(f"note: scripts/{name} binds {len(short)} one-letter names outside loops")
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
