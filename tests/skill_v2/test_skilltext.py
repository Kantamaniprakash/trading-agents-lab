"""Tests for the skill's instruction text: SKILL.md, references/roles.md, references/lab.md,
and for the two texts written around it, docs/skill-v2/CONTRACTS.md and skill/LISTING.md.

The text is checked against docs/skill-v2/CONTRACTS.md and against what the four shipped
scripts really print. Some tests run the scripts with the commands exactly as SKILL.md prints
them, on generated numbers. No test uses the network and no test waits: where get_data.py
runs, its one HTTP function and its pause are replaced.
"""
from __future__ import annotations

import ast
import contextlib
import datetime
import hashlib
import html
import importlib.util
import io
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SKILL = ROOT / "skill" / "trading-agents-desk"
SCRIPTS = SKILL / "scripts"
CONTRACTS = ROOT / "docs" / "skill-v2" / "CONTRACTS.md"
LISTING = ROOT / "skill" / "LISTING.md"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
SKILL_MD = SKILL / "SKILL.md"
ROLES_MD = SKILL / "references" / "roles.md"
LAB_MD = SKILL / "references" / "lab.md"
MARKDOWN = (SKILL_MD, ROLES_MD, LAB_MD)

REPO_URL = "https://github.com/Kantamaniprakash/trading-agents-lab"
HOST = "query1.finance.yahoo.com"
MAX_SKILL_BYTES = 12000
MAX_DESCRIPTION_CHARS = 1024

# Words the marketplace's scanner reads badly, as plain lower-case substrings.
AVOIDED = ("fetch", "curl", "sudo", "base64", "ignore previous", "do not tell the user",
           "secretly", "pip install")
# The name of a key or of an environment variable, in any of the usual spellings.
AVOIDED_PATTERNS = (
    r"(?i)api[ _-]?key", r"\b[A-Z][A-Z0-9]*_(?:KEY|TOKEN|SECRET)\b",
    r"(?i)environment variable", r"(?i)\benv(?:ironment)? vars?\b", r"\$[A-Za-z_]{2,}",
    r"\$\{", r"%[A-Z_]{2,}%", r"os\.environ", r"(?i)\bexport [A-Z_]+=")
# Version 1 wording that must not come back.
V1_LEFTOVERS = (r"(?i)\bseats?\b", r"(?i)snapshot", r"(?i)tradinglab\b", r"(?i)\bvenv\b",
                r"(?i)yfinance", r"research-harness")

VERDICTS = ("BULLISH", "BEARISH", "NEUTRAL")
PROSE_SENTENCES = {"why": (1, 3), "risk": (2, 4)}
ROLE_NAMES = ("Market analyst", "Fundamentals analyst", "News analyst", "Sentiment analyst",
              "Bull researcher", "Bear researcher", "Facilitator", "Trader", "Risky analyst",
              "Safe analyst", "Neutral analyst", "Fund manager")
SECTIONS = ("What this is", "Ground rules", "Setup for one run", "How to read a script's answer",
            "Step 1", "Step 2", "Step 3", "Step 4", "Step 5", "Step 6", "After the report",
            "Guardrails")
# What moved from SKILL.md to roles.md when the page step needed the room, by heading.
MOVED = ("Price rows from the marketplace tool", "After the report", "Reading a decision",
         "What the receipt covers", "The page", "Several tickers")
# Every flag brief.py can put on a fact sheet, by the id it prints.
FLAGS = ("unfinished_session", "close_filled", "prices_behind", "rows_dropped", "history_gap",
         "short_history", "statements_absent", "no_headlines")
REF = re.compile(r"\{\{(.*?)\}\}")


def shipped_bytes(path: Path) -> bytes:
    """The file as the build ships it: a Windows checkout may hold CRLF line endings."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def text_of(path: Path) -> str:
    return shipped_bytes(path).decode("utf-8")


def fenced(text: str) -> list[tuple[str, str]]:
    """Every fenced block as (info string, body), with the fence's own indentation removed."""
    out, info, body, indent = [], None, [], 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if info is None:
                info, body, indent = stripped[3:].strip(), [], len(line) - len(line.lstrip())
            else:
                out.append((info, "\n".join(body)))
                info = None
        elif info is not None:
            body.append(line[indent:] if line[:indent].strip() == "" else line)
    assert info is None, "a fenced block is never closed"
    return out


def without_fences(text: str) -> str:
    out, inside = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            inside = not inside
        elif not inside:
            out.append(line)
    return "\n".join(out)


def inline_code(text: str) -> list[str]:
    return re.findall(r"`([^`\n]+)`", without_fences(text))


def section(text: str, heading: str, level: str = "##") -> str:
    """The body of the first section whose heading starts with ``heading``."""
    m = re.search(r"(?m)^%s %s.*$" % (re.escape(level), re.escape(heading)), text)
    assert m, "no section %r" % heading
    rest = text[m.end():]
    end = re.search(r"(?m)^#{1,%d} " % len(level), rest)
    return rest[:end.start()] if end else rest


def commands(text: str) -> list[str]:
    """Every command the text asks the agent to run: sh blocks, and inline code that starts
    with the Python placeholder or a Python launcher."""
    out = [line for info, body in fenced(text) if info == "sh" for line in body.splitlines()
           if line.strip()]
    return out + [c for c in inline_code(text) if re.match(r"(PY|python3?|py) ", c)]


def contract() -> str:
    return text_of(CONTRACTS)


def contract_facts() -> set[str]:
    """The fact names of CONTRACTS.md section 3, with the table names spelled out for N 0 to 9."""
    body = section(contract(), "Fact names", "###")
    body = body.split("A fact that cannot be computed")[0]
    names: set[str] = set()
    for span in re.findall(r"`([^`]+)`", body):
        for name in (x.strip() for x in span.replace("\n", " ").split(",")):
            if name.endswith(".N"):
                names.update("%s.%d" % (name[:-2], n) for n in range(10))
            elif name:
                names.add(name)
    assert len(names) > 100 and {"last", "rsi14", "close.9", "debt_equity"} <= names
    return names


def contract_scripts() -> set[str]:
    names = set(re.findall(r"(?m)^scripts/(\w+\.py)\b", fenced(contract())[0][1]))
    assert names == {"get_data.py", "brief.py", "render.py", "page.py"}
    return names


def contract_usage() -> dict[str, list[str]]:
    """The command line of each script's main job, from the contract's usage blocks: the
    words after the script name, up to the first optional part."""
    out = {}
    for _, body in fenced(contract()):
        for line in body.splitlines():
            m = re.match(r"python (\w+)\.py ([A-Z][A-Z_ ]*[A-Z])", line)
            if m:
                out[m.group(1)] = m.group(2).split()
    assert out["render"] == ["BRIEF", "DRAFT", "OUT_MD", "OUT_REPORT"], out
    return out


def brief_flags() -> set[str]:
    """Every flag id in brief.py's code: the second argument of a note, a value appended to
    the flags, and a flag chosen by a condition (``... and "flag"``)."""
    tree, found = ast.parse(text_of(SCRIPTS / "brief.py")), set()
    for node in ast.walk(tree):
        values = []
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "note":
            values = node.args[1:2]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "append" and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "flags":
            values = node.args[:1]
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
            values = node.values[-1:]
        found |= {v.value for v in values if isinstance(v, ast.Constant)
                  and isinstance(v.value, str) and re.fullmatch(r"[a-z]+(_[a-z]+)+", v.value)}
    return found


def contract_stop_codes() -> set[str]:
    """Every code the contract names: the STOP codes and the ERROR codes."""
    codes = set(re.findall(r"\bE_[A-Z_]+\b", contract()))
    assert {"E_SYMBOL", "E_NO_ROUTE", "E_INPUT", "E_STALE"} <= codes
    return codes


def script_stop_codes(name: str) -> set[str]:
    """The codes a script prints on a STOP line (exit code 2), read from its source. An ERROR
    line (exit code 3 or 4) is handled by its exit code and needs no name in SKILL.md."""
    source = text_of(SCRIPTS / name)
    if name == "get_data.py":
        block = source[source.index("STOPS = {"):source.index("ERRORS = {")]
        return set(re.findall(r'"(E_[A-Z_]+)":', block))
    found = set(re.findall(r"STOP (E_[A-Z_]+)", source))
    if name == "brief.py":
        # stop(code, sentence) is a STOP; a third argument is the exit code of an ERROR.
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "stop" and len(node.args) == 2 \
                    and isinstance(node.args[0], ast.Constant):
                found.add(node.args[0].value)
    return found


def contract_slots() -> list[str]:
    block = next(body for _, body in fenced(section(contract(), "4. ")) if "[verdict]" in body)
    return re.findall(r"(?m)^\[([a-z.]+)\]", block)


FACTS = contract_facts()
PRICE = {n for n in FACTS if re.fullmatch(
    r"last|prev_close|(open|high|low|close)\.\d|sma\d+|bb_(upper|lower)|(high|low)52", n)}
SIGNED = {n for n in FACTS if re.fullmatch(
    r"chg1|(chg|gap)\.\d|\w+_gap|ret\d+|move_max|\w+_yoy", n)}


def example_draft() -> str:
    blocks = [body for info, body in fenced(text_of(SKILL_MD)) if body.startswith("[verdict]")]
    assert len(blocks) == 1, "SKILL.md must hold exactly one complete draft example"
    return blocks[0]


def prose_problems(slot: str, text: str, facts: set[str]) -> list[str]:
    out = []
    for body in REF.findall(text):
        name, _, fmt = (x.strip() for x in body.partition("|"))
        if re.fullmatch(r"h[1-9]\d?", name):
            if fmt:
                out.append("%s: a headline reference takes no format" % slot)
        elif name not in facts:
            out.append("%s: %r is not a fact name" % (slot, name))
        elif fmt not in ("", "label") and not (fmt == "dir" and name in SIGNED) \
                and not (fmt == "gap" and name in PRICE):
            out.append("%s: format %r does not fit %s" % (slot, fmt, name))
    bare = REF.sub(" ", text)
    if re.search(r"\d", bare):
        out.append("%s: a digit outside a reference" % slot)
    if "{" in bare or "}" in bare:
        out.append("%s: a broken reference" % slot)
    if len(text) > 600:
        out.append("%s: over 600 characters" % slot)
    low, high = PROSE_SENTENCES.get(slot.split(".")[-1], (1, 1))
    count = len(re.findall(r"[.!?](?=\s|$)", bare))
    if slot != "levels" and not low <= count <= high:
        out.append("%s: %d sentences, %d to %d wanted" % (slot, count, low, high))
    return out


def draft_problems(text: str, facts: set[str] = FACTS) -> list[str]:
    """Check a draft against CONTRACTS.md section 4 without the renderer. Empty when clean."""
    out, order, body, cur = [], [], {}, None
    for line in text.splitlines():
        m = re.match(r"\[([a-z.]+)\] ?(.*)$", line)
        if m:
            cur = m.group(1)
            order.append(cur)
            body[cur] = [m.group(2)]
        elif cur is None:
            out.append("text before the first slot")
        else:
            body[cur].append(line)
    if order != contract_slots():
        out.append("the slots are %s" % order)
    for slot in order:
        lines = [x.strip() for x in body[slot] if x.strip()]
        value = " ".join(lines)
        if slot == "verdict":
            ok = value in VERDICTS
        elif slot == "trader":
            ok = re.fullmatch(r"(BUY|SELL|HOLD) (0\.\d|1\.0)", value)
        elif slot == "manager":
            ok = re.fullmatch(r"(approved|rejected) (BUY|SELL|HOLD) (0\.\d|1\.0)", value)
        elif slot == "roles":
            ok = value in ("single", "subagents", "mixed")
        elif slot == "skipped":
            ok = value and not re.search(r"[\d{}]", value)
        elif slot.endswith(".evidence"):
            names = [x.strip() for x in value.split(",")]
            ok = 2 <= len(names) <= 5 and all(n in facts for n in names)
        elif slot == "levels":
            ok = 1 <= len(lines) <= 3
            for row in lines:
                parts = [x.strip() for x in row.split("|", 2)]
                if len(parts) != 3 or parts[0] not in facts & PRICE \
                        or parts[1] not in ("bull", "bear") or not parts[2]:
                    ok = False
                else:
                    out += prose_problems(slot, parts[2], facts)
        else:
            ok = bool(value)
            out += prose_problems(slot, value, facts)
        if not ok:
            out.append("%s: %r does not fit the slot" % (slot, value))
    return out


# Frontmatter and sizes

def frontmatter() -> dict[str, str]:
    lines = text_of(SKILL_MD).split("\n")
    assert lines[0] == "---", "SKILL.md must open with frontmatter"
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(": ")
        assert sep and re.fullmatch(r"[a-z]+", key), "unsupported frontmatter line %r" % line
        assert key not in fields
        fields[key] = value
    return fields


def test_frontmatter_is_valid_and_within_limits():
    fields = frontmatter()
    assert list(fields) == ["name", "description", "license"]
    assert fields["name"] == "trading-agents-desk" == SKILL.name
    assert fields["license"] == "MIT"
    description = fields["description"]
    assert 0 < len(description) <= MAX_DESCRIPTION_CHARS
    assert "Use when" in description, "the description must say when to use the skill"
    assert "lost to buy-and-hold" in description
    for value in fields.values():
        # A plain YAML scalar: one line, no indicator at the start, no mapping or comment inside.
        assert value == value.strip() and value
        assert value[0] not in "!&*[]{}>|@`'\"%#,?-"
        assert ": " not in value and " #" not in value and not value.endswith(":")


def test_skill_md_fits_the_size_cap():
    assert len(shipped_bytes(SKILL_MD)) <= MAX_SKILL_BYTES


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.name)
def test_markdown_is_ascii_with_lf_line_endings(path):
    data = shipped_bytes(path)
    data.decode("ascii")
    assert b"\r" not in data and b"\t" not in data
    assert data.endswith(b"\n") and not data.endswith(b"\n\n")
    assert not [line for line in data.split(b"\n") if line != line.rstrip()]


def test_the_version_one_harness_reference_is_gone():
    assert not (SKILL / "references" / "research-harness.md").exists()
    assert sorted(p.name for p in (SKILL / "references").iterdir()) == ["lab.md", "roles.md"]


# Words the scanner reads, and wording version 1 left behind

@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.name)
def test_avoided_words_do_not_appear(path):
    text = text_of(path)
    lowered = text.lower()
    assert not [w for w in AVOIDED if w in lowered]
    for pattern in AVOIDED_PATTERNS + V1_LEFTOVERS:
        assert not re.findall(pattern, text), pattern


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.name)
def test_the_only_link_is_the_repository_in_lab_md(path):
    text = text_of(path)
    urls = re.findall(r"[a-z][a-z0-9+.-]*://[^\s)>\"'`]+", text)
    assert not re.search(r"(?i)\bwww\.", text)
    if path == LAB_MD:
        assert urls == [REPO_URL]
    else:
        assert urls == []


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.name)
def test_commands_hold_no_redirection_characters(path):
    text = text_of(path)
    allowed = {SKILL_MD: {"sh", "text"}, ROLES_MD: {"json"}, LAB_MD: set()}[path]
    assert {info for info, _ in fenced(text)} <= allowed
    for command in commands(text):
        assert not re.search(r"[<>|]|&&", command), command


# Names that must exist in the contract

def test_every_script_name_exists_in_the_contract():
    shipped = contract_scripts()
    for path in MARKDOWN:
        assert set(re.findall(r"\b\w+\.py\b", text_of(path))) <= shipped, path.name
    assert set(re.findall(r"\b\w+\.py\b", text_of(SKILL_MD))) == shipped


def test_every_reference_in_the_text_names_a_fact_or_a_placeholder():
    for path in MARKDOWN:
        for body in REF.findall(text_of(path)):
            name, _, fmt = (x.strip() for x in body.partition("|"))
            assert name in FACTS | {"name", "hN"} or re.fullmatch(r"h[1-9]", name), body
            assert fmt in ("", "label", "dir", "gap"), body


def test_every_fact_like_name_in_backticks_is_a_fact():
    # Lower-case names with a digit, an underscore or a table index are fact names unless
    # they are one of the brief's flags or a key of a role's JSON output.
    flags = set(re.findall(r"(?m)^\| `([a-z_]+)` \|", section(text_of(SKILL_MD), "Step 2")))
    assert flags >= {"close_filled", "short_history"}
    other = flags | {"final_action", "final_size", "h1"}
    for path in MARKDOWN:
        for span in inline_code(text_of(path)):
            if span in commands(text_of(path)):
                continue
            for token in re.split(r"[\s,]+", span):
                if re.fullmatch(r"[a-z][a-z0-9_]*(\.[0-9N])?", token) \
                        and re.search(r"[0-9_]|\.N$", token) and token not in other:
                    names = {"%s.%d" % (token[:-2], n) for n in range(10)} \
                        if token.endswith(".N") else {token}
                    assert names <= FACTS, "%s: %r is not a fact name" % (path.name, token)


def test_the_flag_rows_of_step_2_are_exactly_the_flags_in_the_code_of_brief_py():
    # Every flag brief.py can print has a row, and every row names a real flag.
    rows = re.findall(r"(?m)^\| `([a-z_]+)` \|", section(text_of(SKILL_MD), "Step 2"))
    assert len(rows) == len(set(rows))
    assert set(rows) == brief_flags() == set(FLAGS)


# The draft example

def test_the_draft_example_fits_the_contract_grammar():
    assert draft_problems(example_draft()) == []


def test_the_draft_example_uses_only_contract_fact_names():
    draft = example_draft()
    used = {body.partition("|")[0].strip() for body in REF.findall(draft)}
    for slot in ("bull.evidence", "bear.evidence"):
        line = re.search(r"(?m)^\[%s\] (.*)$" % re.escape(slot), draft).group(1)
        used |= {x.strip() for x in line.split(",")}
    used |= set(re.findall(r"(?m)^([a-z0-9_.]+) \| (?:bull|bear) \|", draft))
    assert len(used) >= 12
    assert {n for n in used if not re.fullmatch(r"h[1-9]", n)} <= FACTS
    # Every reference format is shown once, by the example or by the list under it.
    step = section(text_of(SKILL_MD), "Step 4")
    for form in ("{{name}}", "{{name|label}}", "{{name|dir}}", "{{name|gap}}", "{{h1}}"):
        assert form in step, form


def test_the_draft_example_has_no_digit_outside_references_and_the_grammar():
    rest = REF.sub("", example_draft())
    rest = re.sub(r"(?m)^\[(?:bull|bear)\.evidence\] .*$", "", rest)
    rest = re.sub(r"(?m)^[a-z0-9_.]+ \| (bull|bear) \|", "", rest)
    rest = re.sub(r"(?m)^\[trader\] (BUY|SELL|HOLD) \d\.\d$", "", rest)
    rest = re.sub(r"(?m)^\[manager\] (approved|rejected) (BUY|SELL|HOLD) \d\.\d$", "", rest)
    assert not re.findall(r".*\d.*", rest)


@pytest.mark.parametrize("old, new, problem", [
    ("{{sma50_gap|dir}}", "13.76% below", "a digit outside a reference"),
    ("its medium-term average", "its 50-day average", "a digit outside a reference"),
    ("rsi14, stoch_k", "rsi_14, stoch_k", "does not fit the slot"),
    ("{{low52_gap|dir}}", "{{low52|dir}}", "does not fit low52"),
    ("{{ret5|label}}", "{{ret5|gap}}", "does not fit ret5"),
    ("{{vol_ratio|label}}", "{{volume_ratio|label}}", "is not a fact name"),
    ("{{h1}}", "{{h1|label}}", "takes no format"),
    ("close.1 | bull |", "rsi14 | bull |", "does not fit the slot"),
    ("low52 | bear |", "low52 | down |", "does not fit the slot"),
    ("[trader] SELL 0.2", "[trader] SELL 20%", "does not fit the slot"),
    ("[manager] approved SELL 0.1", "[manager] SELL 0.1", "does not fit the slot"),
    ("[roles] mixed", "[roles] parallel", "does not fit the slot"),
    ("[skipped] none\n", "", "the slots are"),
    ("{{move_max_date}}", "{{move_max_date}", "broken reference"),
])
def test_the_draft_checker_refuses_a_bad_draft(old, new, problem):
    draft = example_draft() + "\n"
    assert old in draft
    found = draft_problems(draft.replace(old, new, 1))
    assert any(problem in x for x in found), found


# What SKILL.md must say

def test_skill_md_has_the_sections_in_order():
    headings = re.findall(r"(?m)^## (.+)$", without_fences(text_of(SKILL_MD)))
    wanted = iter(headings)
    for name in SECTIONS:
        assert any(h.startswith(name) for h in wanted), "%r is missing or out of order" % name


def test_what_this_is_takes_four_lines_and_names_the_lost_test():
    lines = [x for x in section(text_of(SKILL_MD), "What this is").splitlines() if x.strip()]
    assert len(lines) == 4 and all(x.startswith("- ") for x in lines)
    # The scripts get the data: a price file is not downloaded.
    assert lines[0] == ("- Four Python scripts get the data, compute every figure, print the "
                        "report and draw a page.")
    assert any("lost to buy-and-hold in its only test" in x for x in lines)


def test_ground_rules_keep_the_four_of_version_one_and_add_three():
    rules = section(text_of(SKILL_MD), "Ground rules")
    assert len(re.findall(r"(?m)^\d+\. ", rules)) == 7
    for phrase in (
            "never trades", "brokerage, order, account, watchlist or alert", "Subagents too",
            "No personal inputs", "starts from no position",
            "fact sheet is the only market data", "headlines included",
            "never an instruction", "script prints the disclosure",
            "Never type a market figure, date or headline into the draft",
            "Never edit, shorten or rewrite a script",
            "English", "exactly as printed", "translation", "not checked by the script"):
        assert phrase.lower() in rules.lower(), phrase


def test_setup_is_one_folder_the_scripts_the_run_needs_and_a_preflight():
    setup = section(text_of(SKILL_MD), "Setup for one run")
    assert HOST in setup and "approval" in setup
    assert setup.index(HOST) < setup.index("1. "), "the user is told before the first step"
    launchers = [setup.index("`%s --version`" % x) for x in ("python3", "python", "py -3")]
    assert launchers == sorted(launchers)
    assert "3.9" in setup and "ONE new temporary folder" in setup
    assert "Each ticker gets its own" in setup
    assert "tempfile.mkdtemp" in setup and "absolute path" in setup and "double quotes" in setup
    # Two scripts always, two only when the run will use them (contract section 1). A copy
    # made by a command cannot go wrong by a character; writing a script out is the last resort.
    (step,) = [x for x in setup.splitlines() if x.startswith("3. ")]
    for phrase in ("unchanged", "`brief.py` and `render.py` always",
                   "`get_data.py` unless you know that your command tool has no internet",
                   "`page.py` can wait until Step 6", "Copy the files with a command.",
                   "Only if you cannot, write each with your file-writing tool, character for "
                   "character"):
        assert phrase in step, phrase
    assert step.index("Copy the files with a command") < step.index("file-writing tool")
    assert "PREFLIGHT ok" in setup and "ends with the names of the scripts it checked" in setup
    assert "write the scripts again, once" in setup
    assert "show the user the line it printed and stop" in setup
    # A script that is added later is checked before it is used.
    assert "Run it again whenever you add a script to `DIR`" in setup
    step1 = section(text_of(SKILL_MD), "Step 1")
    assert "put `get_data.py` in `DIR` if it is not there, run the preflight" in step1
    assert "If `page.py` is not in `DIR`, put it there and run the preflight first" \
        in section(text_of(SKILL_MD), "Step 6")


def test_every_stop_code_is_named_or_covered_by_the_general_rule():
    text = text_of(SKILL_MD)
    rule = [x for x in section(text, "How to read a script's answer").splitlines()
            if "`STOP`" in x]
    assert len(rule) == 1
    # What the agent may not do on its own; the step tables say what the user may choose.
    for phrase in ("starts with `STOP`", "(exit code 2, on stderr)", "that line, unchanged",
                   "and stop", "Do not retry, change the request or switch source on your own",
                   "no disclosure block"):
        assert phrase in rule[0], phrase
    named = set(re.findall(r"\bE_[A-Z_]+\b", text))
    codes = contract_stop_codes()
    assert not {"E_PENDING", "E_CLOCK"} & (codes | named), "stops that left the contract"
    assert named <= codes, "SKILL.md names a stop code the contract does not have"
    # The general rule covers any STOP line. The stops of the three steps that have a next
    # step of their own are named where that step is described: every one a script can
    # print, and none it cannot.
    step1 = section(text, "Step 1")
    step2, step6 = section(text, "Step 2"), section(text, "Step 6")
    for step, name in ((step1, "get_data.py"), (step2, "brief.py"), (step6, "page.py")):
        stops = script_stop_codes(name)
        assert stops and stops <= codes, (name, stops - codes)
        here = set(re.findall(r"\bE_[A-Z_]+\b", step))
        assert here == stops, (name, sorted(here ^ stops))
    assert script_stop_codes("get_data.py") == {
        "E_SYMBOL", "E_NOT_FOUND", "E_REFUSED", "E_BUSY", "E_NO_ROUTE", "E_SERVER", "E_CERT",
        "E_SHAPE"}
    assert script_stop_codes("brief.py") == {
        "E_INPUT", "E_SYMBOL", "E_TYPE", "E_DATES", "E_OHLC", "E_FEW", "E_GAP", "E_STALE"}
    assert script_stop_codes("page.py") == {"E_PAIR", "E_INPUT"}
    # render.py's one stop is covered by the general rule and named in the contract.
    assert script_stop_codes("render.py") == {"E_BRIEF"} <= codes
    # A history whose dates cannot be read leaves the two other routes open.
    assert "After `E_DATES`, offer choices 1 and 2 of Step 1" in step2


def test_the_no_network_route_offers_three_choices_and_no_retyping():
    step = section(text_of(SKILL_MD), "Step 1")
    assert HOST in step
    assert len(re.findall(r"(?m)^[123]\. ", step.split("### ")[1])) == 3
    assert "date,open,high,low,close,volume" in step and '--symbol "SYMBOL"' in step
    assert "dates as YYYY-MM-DD" in step
    assert "Never retype price rows by hand into a file" in step
    # The third choice is a rare case: SKILL.md names it and its charge, roles.md has the call.
    assert "marketplace's price-history tool" in step and "the user approves first" in step
    assert '"Price rows from the marketplace tool" in `references/roles.md`' in step
    rows = " ".join(section(text_of(ROLES_MD), "Price rows from the marketplace tool").split())
    assert "yahoo-finance:get_historical_data" in rows
    for value in ("choice 3", "`symbol`", "`range` `1y`", "`interval` `1d`", "$0.07", "approval",
                  "unchanged", "in place of `DIR/bundle.json`",
                  "If the tool's result is only text in the conversation, say so and offer the "
                  "other choices", "Type rows only when the user explicitly asks",
                  "above the report",
                  '"The price rows were typed by the assistant and not checked against the '
                  'source."'):
        assert value in rows, value
    # The rule on typed rows stands once, in SKILL.md; roles.md holds its one exception.
    assert "Never retype" not in text_of(ROLES_MD)


def test_every_section_of_roles_md_that_skill_md_points_to_exists():
    text, roles = text_of(SKILL_MD), text_of(ROLES_MD)
    headings = set(re.findall(r"(?m)^#{2,3} (.+)$", without_fences(roles)))
    named = re.findall(r'"([^"]+)" in `references/roles\.md`', text) \
        + re.findall(r'`references/roles\.md`, under "([^"]+)"', text)
    assert sorted(named) == ["After the report", "From the desk to the draft",
                             "Price rows from the marketplace tool"]
    assert set(named) | set(MOVED) <= headings
    # roles.md points back to a section of SKILL.md by its name, too.
    back = re.findall(r'"([^"]+)" in `SKILL\.md`', roles)
    assert back and set(back) <= set(re.findall(r"(?m)^#{2,3} (.+)$", without_fences(text)))


def test_values_the_agent_fills_in_are_double_quoted_in_every_command():
    # An index symbol starts with a caret. Outside double quotes cmd.exe drops it, so another
    # symbol is asked for, and zsh can expand it.
    text = text_of(SKILL_MD)
    spans = commands(text) + [c for c in inline_code(text) if "--symbol" in c]
    assert any("get_data.py" in x for x in spans) and any("--symbol" in x for x in spans)
    for span in spans:
        assert re.findall(r"(?<!\")\b(?:SYMBOL|DIR)\b", span) == [], span


def test_step_1_says_which_characters_a_symbol_may_hold():
    # The symbol goes into a shell command, so the text carries the contract's pattern.
    pattern = re.search(r"`\^\[(\S+)\]\{1,15\}\$`", contract()).group(1)
    marks = set(re.sub(r"\w-\w", "", pattern))
    assert marks == set(".^=-")
    step = section(text_of(SKILL_MD), "Step 1").split("\n### ")[0]
    for phrase in ["letters", "digits", "ask again", "run nothing"] + [
            "`%s`" % c for c in sorted(marks)]:
        assert phrase in step, phrase


def test_a_failure_with_no_rule_of_its_own_ends_the_run():
    # Python itself exits with code 2, and no STOP line, when it cannot open a script.
    out = subprocess.run([sys.executable, str(HERE / "no_such_script.py")],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 2
    assert not re.search(r"(?m)^(STOP|ERROR|FIX)\b", out.stdout + out.stderr)
    rules = [x for x in section(text_of(SKILL_MD), "How to read a script's answer").splitlines()
             if x.startswith("- ")]
    for phrase in ("A second `ERROR`", "any failure not listed", "show the user the output",
                   "and stop"):
        assert phrase in rules[-1], phrase
    # After the scripts are written again, the command that failed is run again.
    (three,) = [x for x in rules if "exit code 3" in x]
    assert "then the command" in three
    (four,) = [x for x in rules if "exit code 4" in x]
    assert "once more" in four


def test_step_2_explains_every_flag_brief_py_can_print():
    step = section(text_of(SKILL_MD), "Step 2")
    rows = dict(re.findall(r"(?m)^\| `([a-z_]+)` \| (.+) \|$", step))
    assert sorted(rows) == sorted(FLAGS)
    # A view that ends before the newest session must be said up front, in plain words.
    for phrase in ("the view ends at the date shown", "in the first lines of your answer",
                   "above the report", 'Never call the last bar "today"'):
        assert phrase in rows["prices_behind"], phrase
    assert "Notes name the dates" in rows["history_gap"]
    assert "Notes name the dates" in rows["rows_dropped"]
    # "Last price" is a label on the sheet, so the two rows name the wording that every
    # sentence can take: "the price".
    assert rows["unfinished_session"] == (
        "The newest row is not final, and every average, gap, return and indicator includes "
        'it. Say "the price", never "close".')
    assert ('| `session unknown` | The newest row may be unfinished. Say "the price". |'
            in step.splitlines())
    assert "last price" not in step


def test_render_step_allows_two_corrections_and_shows_the_report_unchanged():
    step = section(text_of(SKILL_MD), "Step 5")
    for phrase in ("--attempt 1", "--attempt 2", "--attempt 3", "Never more", "`FIX`",
                   "The report could not be completed.", "show the FIX lines",
                   "complete and unchanged", "It is the answer, with or without a page",
                   "`DIR/report.json` holds the same report for Step 6"):
        assert phrase in step, phrase
    assert "--attempt 4" not in step
    # The page has its own step; the render step no longer writes or names it.
    assert "report.html" not in step and "page.py" not in step


def test_the_page_step_is_optional_and_never_pastes_the_page_into_the_chat():
    step = section(text_of(SKILL_MD), "Step 6")
    for phrase in ("After the report is printed", "can show or save an HTML file",
                   "when the user asks for it", "starts with `PAGE ok`", "`DIR/report.html`",
                   "tell the user where it is", "Never paste the HTML into the chat as text",
                   "the page was not drawn", "The report above still stands",
                   "With or without a page, ask whether to keep the folder"):
        assert phrase in step, phrase
    # One command for the page, and the folder is removed only by the last command.
    assert [k for k in skill_commands() if k in ("page", "rmtree")] == ["page", "rmtree"]
    assert text_of(SKILL_MD).index("## Step 6") > text_of(SKILL_MD).index("## Step 5")
    # roles.md says which of the two to trust. The rule on pasting stands once, in Step 6.
    page = " ".join(section(text_of(ROLES_MD), "The page", "###").split())
    assert page == ("The page is the same report, drawn by `page.py`. The printed report is the "
                    "answer: if the two ever disagree, trust it and say so.")
    assert "paste" not in text_of(ROLES_MD)


def test_closing_sections_keep_their_content():
    text, roles = text_of(SKILL_MD), text_of(ROLES_MD)
    after = section(text, "After the report")
    for phrase in ('See "After the report" in `references/roles.md`', "reading a decision",
                   "the receipt", "several tickers"):
        assert phrase in after, phrase
    # The three closing sections moved to roles.md word for word.
    decision = " ".join(section(roles, "Reading a decision", "###").split())
    for word in ("BUY", "SELL", "HOLD", "fund manager", "next few trading days", "judgment",
                 "A second run on the same data"):
        assert word in decision, word
    several = " ".join(section(roles, "Several tickers", "###").split())
    for phrase in ("one ticker at a time", "own new folder", "no portfolio view"):
        assert phrase in several, phrase
    receipt = [x for x in section(roles, "What the receipt covers", "###").splitlines()
               if x.startswith("- ")]
    assert len(receipt) == 3 and receipt[0].startswith("- Guaranteed: every figure")
    assert receipt[1].startswith("- Not guaranteed:") and "not checked" in " ".join(
        section(roles, "What the receipt covers", "###").split())
    guard = section(text, "Guardrails")
    for phrase in ("advice", "a prediction with a known hit rate",
                   "Never turn the desk's decision into an order or an action on the user's "
                   "own holding or account", "past date", "references/lab.md"):
        assert phrase in guard, phrase
    assert len(re.findall(r"(?m)^- Never ", guard)) == 3
    # The rule on invented figures stands where the draft is written: ground rule 5.
    assert "Never type a market figure, date or headline into the draft" in text
    step3 = section(text, "Step 3")
    assert "references/roles.md" in step3
    # A sheet with no statement figures or no headlines runs fewer than twelve roles.
    assert "Run the roles that apply, in its order" in step3


# roles.md and lab.md

def test_roles_md_names_the_twelve_roles_in_order():
    text = text_of(ROLES_MD)
    assert "twelve roles" in text
    # Each role's brief: a table row for an analyst, a bold bullet for the others.
    found = re.findall(r"(?m)^(?:\| |- \*\*)([A-Z][a-z]+(?: [a-z]+)?)(?: \||\.\*\*)", text)
    assert tuple(x for x in found if x not in ("Facts", "Role")) == ROLE_NAMES
    for stage in ("Stage 1: analyst team", "Stage 2: research debate", "Stage 3: trader",
                  "Stage 4: risk review"):
        assert "## " + stage in text
    assert len(re.findall(r"(?m)^\s*```json$", text)) == 3
    assert "Bull,\nBear, Bull, Bear" in text or "Bull, Bear, Bull, Bear" in text
    assert "Risky, Safe, Neutral" in text


def test_roles_md_closes_the_wording_gaps_of_version_one():
    text = " ".join(text_of(ROLES_MD).split())
    own = " ".join(section(text_of(ROLES_MD), "A role's own context").split())
    for phrase in ("in-process subagent or task tool", "returns text to you",
                   "separate session", "cloud", "bills the user", "its own connectors"):
        assert phrase in own, phrase
    manager = text[text.index("**Fund manager.**"):]
    assert "the trader's proposed action, size and rationale" in manager[:200]
    rules = " ".join(section(text_of(ROLES_MD), "Rules for every role").split())
    for phrase in ("the flag `prices_behind`", "its newest row is not the newest session",
                   "No role may call the newest row today's price or the latest session",
                   "`history_gap`", "across a break in the dates"):
        assert phrase in rules, phrase
    for phrase in (
            "Headline dates are calendar dates. A headline dated after the newest bar was "
            "published after every move in the table, so it cannot be what moved the price: do "
            "not present it as a cause.", "No arithmetic", "described in words",
            "Skip this role when the statement facts are absent or the type is not EQUITY",
            "Quote the script's sentence", "no disclosure block", "The fact sheet"):
        assert phrase in text, phrase


def test_roles_md_makes_no_claim_about_what_the_lab_pipeline_enforces():
    # Version 1 marked two briefs as stricter than the lab's pipeline. Version 2 dropped the
    # marks, so the sentence that promised them must not stay behind.
    text = " ".join(text_of(ROLES_MD).split())
    assert "pipeline does not enforce" not in text
    assert text.count("pipeline") <= 1


def test_roles_md_warns_that_plain_words_can_be_a_label():
    # "last price", "last close" and "previous close" are labels on the sheet, so the
    # renderer refuses another fact's reference straight after them.
    rules = " ".join(section(text_of(ROLES_MD), "From the desk to the draft").split())
    for phrase in ('"last price"', '"last close"', '"previous close"',
                   '"The price is {{sma50_gap|dir}} its medium-term average"'):
        assert phrase in rules, phrase


def test_roles_md_keeps_the_indicator_conventions():
    table = section(text_of(ROLES_MD), "Indicator conventions")
    rows = [x for x in table.splitlines() if x.startswith("| `")]
    assert len(rows) >= 10
    for fact in ("rsi14", "stoch_k", "cci20", "mfi14", "bb_pctb", "adx14", "macd", "atr14",
                 "sma200_gap", "high52", "vol_ratio"):
        assert "`%s`" % fact in table, fact
    assert "Never type one into the draft" in table


def test_roles_md_maps_the_desk_onto_every_draft_slot():
    mapping = section(text_of(ROLES_MD), "From the desk to the draft")
    for slot in contract_slots():
        assert "`[%s]`" % slot in mapping, slot
    levels = mapping[mapping.index("- `[levels]`"):mapping.index("- `[trader]`")]
    listed = set()
    for token in re.findall(r"`([a-z0-9_.N]+)`", levels):
        listed |= {"%s.%d" % (token[:-2], n) for n in range(10)} if token.endswith(".N") \
            else {token}
    assert listed & FACTS == PRICE, "the price facts listed for [levels]"
    assert "single" in text_of(ROLES_MD) and "subagents" in text_of(ROLES_MD)
    assert "600 characters" in mapping


def test_lab_md_is_short_and_keeps_the_one_rule():
    text = text_of(LAB_MD)
    assert len(text.encode()) <= 2000
    flat = " ".join(text.split())
    for phrase in ("backtest harness", "recorded API run", "memorisation probe",
                   "separate install", "Never run the desk on a past date",
                   "remember what happened next"):
        assert phrase in flat, phrase
    assert text.count(REPO_URL) == 1


# The commands as printed, run on generated numbers

DAY = 86400
END_DAY = datetime.date(2026, 11, 10).toordinal() - datetime.date(1970, 1, 1).toordinal()


def synthetic_bundle(n: int = 260, seed: int = 7) -> dict:
    """A bundle.v1 document in the contract's shape. Every number is generated."""
    rng = random.Random(seed)
    days, d = [], END_DAY
    while len(days) < n:
        if (d + 3) % 7 < 5:  # day 0 was a Thursday; keep Monday to Friday
            days.append(d)
        d -= 1
    days.reverse()
    cols = {k: [] for k in ("open", "high", "low", "close", "volume")}
    prev = 150.0
    for _ in days:
        o = round(prev * (1 + rng.gauss(0, 0.005)), 2)
        c = round(o * (1 + rng.gauss(0, 0.02)), 2)
        cols["open"].append(o)
        cols["close"].append(c)
        cols["high"].append(round(max(o, c) * (1 + abs(rng.gauss(0, 0.006))) + 0.01, 2))
        cols["low"].append(round(min(o, c) * (1 - abs(rng.gauss(0, 0.006))) - 0.01, 2))
        cols["volume"].append(int(40e6 * rng.uniform(0.4, 2.5)))
        prev = c
    stamps = [x * DAY + 14 * 3600 + 1800 for x in days]  # 09:30 at the exchange
    close_time = stamps[-1] + 23400
    asof = (datetime.date(1970, 1, 1) + datetime.timedelta(days=END_DAY - 1)).isoformat()
    types = ("trailingPeRatio trailingForwardPeRatio trailingMarketCap trailingPegRatio "
             "trailingTotalRevenue trailingNetIncome quarterlyTotalRevenue quarterlyNetIncome "
             "quarterlyTotalDebt quarterlyStockholdersEquity").split()
    statements = {t: [] for t in types}
    statements["trailingPeRatio"] = [{"asOf": asof, "value": 21.5}]
    statements["trailingMarketCap"] = [{"asOf": asof, "value": 2.5e11}]
    meta = {"currency": "USD", "symbol": "TEST", "instrumentType": "EQUITY",
            "gmtoffset": -18000, "longName": "Test Instruments Inc.",
            "regularMarketPrice": cols["close"][-1], "regularMarketTime": close_time,
            "currentTradingPeriod": {"regular": {"start": stamps[-1], "end": close_time}}}
    news = [{"title": "Test Instruments opens a second plant", "publisher": "Example Wire",
             "time": stamps[-1] + 3600, "tickers": ["TEST"]},
            {"title": "Test Instruments names a finance chief", "publisher": "Example Wire",
             "time": stamps[-3], "tickers": ["TEST", "OTHER"]}]
    return {"schema": "bundle.v1", "symbol": "TEST", "source": "yahoo-chart",
            "fetched_at": close_time + 3600, "chart": dict(cols, meta=meta, timestamp=stamps),
            "news": news, "statements": statements, "errors": {"news": None, "statements": None}}


def at_the_clock(bundle: dict, fetch: int, quote: int | None) -> dict:
    """The bundle fetched ``fetch`` seconds after its regular session ends, holding a quote
    made ``quote`` seconds after that end (None: the source names no quote time)."""
    meta = bundle["chart"]["meta"]
    end = meta["currentTradingPeriod"]["regular"]["end"]
    bundle["fetched_at"] = end + fetch
    if quote is None:
        del meta["regularMarketTime"]
    else:
        meta["regularMarketTime"] = end + quote
    return bundle


def live_bundle(n: int = 260) -> dict:
    """The generated bundle fetched ten minutes before the bell, its quote a minute old."""
    return at_the_clock(synthetic_bundle(n), -600, -660)


def coin_bundle(n: int = 366, hours: int = 17) -> dict:
    """The generated rows as a crypto pair: a bar on every UTC day, fetched ``hours`` hours
    after the newest bar starts. The source is not asked for a coin's statements."""
    bundle = synthetic_bundle(n)
    chart = bundle["chart"]
    chart["timestamp"] = [(END_DAY - n + 1 + i) * DAY for i in range(n)]
    bundle["fetched_at"] = chart["timestamp"][-1] + hours * 3600
    chart["meta"].update(instrumentType="CRYPTOCURRENCY", gmtoffset=0,
                         regularMarketTime=bundle["fetched_at"] - 60)
    bundle["statements"] = {k: [] for k in bundle["statements"]}
    return bundle


def skill_commands() -> dict[str, list[str]]:
    """The sh commands of SKILL.md as argument lists, keyed by what they run."""
    out = {}
    for info, body in fenced(text_of(SKILL_MD)):
        if info == "sh":
            (line,) = [x for x in body.splitlines() if x.strip()]
            args = shlex.split(line)
            key = next((k for k in ("tempfile", "rmtree") if k in line), None) or (
                "preflight" if "--preflight" in args else Path(args[1]).name[:-3])
            assert key not in out, "two commands for %s" % key
            out[key] = args
    return out


def test_the_printed_commands_match_the_contract_usage():
    cmds = skill_commands()
    assert list(cmds) == ["tempfile", "preflight", "get_data", "brief", "render", "page",
                          "rmtree"], "one command for each step, in the order of the run"
    line = {k: " ".join(v) for k, v in cmds.items()}
    assert all(v[0] == "PY" for v in cmds.values())
    assert cmds["preflight"][1:] == ["DIR/brief.py", "--preflight", "DIR"]
    assert cmds["get_data"][1:] == ["DIR/get_data.py", "SYMBOL", "DIR/bundle.json"]
    assert cmds["brief"][1:] == ["DIR/brief.py", "DIR/bundle.json", "DIR/brief.json"]
    # The resolved report is render.py's fourth path and page.py's second.
    assert cmds["render"][1:] == ["DIR/render.py", "DIR/brief.json", "DIR/draft.txt",
                                  "DIR/report.md", "DIR/report.json", "--attempt", "1"]
    assert cmds["page"][1:] == ["DIR/page.py", "DIR/brief.json", "DIR/report.json",
                                "DIR/report.html"]
    # As many paths as the contract's usage line of each script names.
    usage = contract_usage()
    assert usage["page"] == ["BRIEF", "REPORT", "OUT_HTML"]
    for name in ("get_data", "render", "page"):
        given = [a for a in cmds[name][2:] if not a.startswith("--") and a != "1"]
        assert len(given) == len(usage[name]), (name, given, usage[name])
    assert cmds["rmtree"][-1] == "DIR" and cmds["tempfile"][1] == "-c"
    assert "DIR" not in line["tempfile"]
    # Every path is the folder's absolute path inside double quotes.
    raw = "\n".join(commands(text_of(SKILL_MD)))
    assert re.findall(r"(?<!\")\bDIR\b", raw) == []
    assert len(re.findall(r"\"DIR(?:/[\w.]+)?\"", raw)) == len(re.findall(r"\bDIR\b", raw))


def run(args: list[str], folder: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run one printed command with the placeholders filled in."""
    real = [sys.executable if a == "PY" else re.sub(r"^DIR(?=/|$)", lambda m: folder, a)
            for a in args]
    return subprocess.run(real, capture_output=True, text=True, timeout=120, env=env)


@pytest.fixture()
def desk_folder(tmp_path):
    """Setup steps 2 to 4 of SKILL.md, with the temporary folder made under tmp_path."""
    missing = [n for n in sorted(contract_scripts()) if not (SCRIPTS / n).is_file()]
    if missing:
        pytest.skip("not in this checkout yet: " + ", ".join(missing))
    cmds = skill_commands()
    env = dict(os.environ, TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path))
    made = run(cmds["tempfile"], "", env)
    folder = made.stdout.strip()
    assert made.returncode == 0 and os.path.isabs(folder) and os.path.isdir(folder)
    assert Path(folder).resolve().parent == tmp_path.resolve() and os.listdir(folder) == []
    for name in contract_scripts():
        shutil.copyfile(SCRIPTS / name, Path(folder) / name)
    pre = run(cmds["preflight"], folder)
    assert pre.returncode == 0 and pre.stdout.startswith("PREFLIGHT ok"), pre.stderr
    # It ends with the names of the scripts it checked, as the setup step says.
    assert pre.stdout.rstrip().endswith(" checked brief.py get_data.py page.py render.py")
    assert pre.stderr == "", "the committed scripts must be stamped"
    yield folder
    gone = run(cmds["rmtree"], folder)
    assert gone.returncode == 0 and not os.path.exists(folder)


def test_the_printed_commands_turn_the_example_draft_into_a_report(desk_folder):
    cmds, folder = skill_commands(), Path(desk_folder)
    (folder / "bundle.json").write_text(json.dumps(synthetic_bundle()) + "\n", encoding="ascii")
    brief = run(cmds["brief"], desk_folder)
    assert brief.returncode == 0, brief.stderr
    sheet = brief.stdout.splitlines()
    assert sheet[-1].startswith("BRIEF ok TEST ")
    assert "session finished" in sheet[0]
    assert any(x.startswith("h1 | ") for x in sheet)
    assert any(re.fullmatch(r"rsi14 \| .+ \| [\d.]+", x) for x in sheet), "name | label | value"

    # The kinds the checker above assumes are the kinds the brief really gives.
    facts = json.loads((folder / "brief.json").read_text(encoding="ascii"))["facts"]
    assert set(facts) <= FACTS
    assert {k for k, f in facts.items() if f["kind"] == "price"} == PRICE & set(facts)
    assert {k for k, f in facts.items() if f["kind"] == "pct" and "sign" in f} \
        == SIGNED & set(facts)

    (folder / "draft.txt").write_text(example_draft() + "\n", encoding="ascii")
    done = run(cmds["render"], desk_folder)
    assert done.returncode == 0, done.stdout + done.stderr
    # The report is what is printed, and it ends with its Receipts block: the three receipt
    # lines stand there once, then the test record and the disclaimer.
    printed = done.stdout.rstrip().splitlines()
    receipts = printed[printed.index("**Receipts**") + 2:]
    assert receipts[0].startswith("- Figures: ") and " Attempt 1. " in receipts[1]
    assert receipts[2] == "- This covers sourcing and arithmetic, not judgment."
    assert receipts[3].startswith("- Test record: ") and len(receipts) == 5
    assert done.stdout.count("Figures: ") == 1
    assert "lost to buy-and-hold" in done.stdout
    assert "Test Instruments opens a second plant" in done.stdout
    # The render step leaves the Markdown report and the resolved report, and no page.
    assert (folder / "report.md").read_text(encoding="ascii") in done.stdout
    resolved = json.loads((folder / "report.json").read_text(encoding="ascii"))
    assert resolved["schema"] == "report.v1" and resolved["symbol"] == "TEST"
    assert not (folder / "report.html").exists()

    # Step 6, as printed: one command draws the page from the two saved files.
    drawn = run(cmds["page"], desk_folder)
    assert drawn.returncode == 0 and drawn.stderr == "", drawn.stderr
    assert drawn.stdout.startswith("PAGE ok TEST %s bytes=" % resolved["report_id"])
    page = (folder / "report.html").read_text(encoding="ascii")
    assert page.startswith("<!doctype html>") and "Test Instruments opens a second plant" in page
    # The page writes a hyphen between two digits as the non-breaking hyphen.
    assert resolved["why"] in html.unescape(page).replace(chr(8209), "-")

    # A typed figure sends the draft back with a FIX line; the correction is attempt 2.
    bad = example_draft().replace("{{sma50_gap|dir}}", "13.76% below")
    (folder / "draft.txt").write_text(bad + "\n", encoding="ascii")
    again = run(cmds["render"][:-1] + ["2"], desk_folder)
    assert again.returncode == 1
    assert again.stdout.startswith("FIX why:")


def test_the_no_network_inputs_work_as_the_skill_describes(desk_folder):
    cmds, folder = skill_commands(), Path(desk_folder)
    chart = synthetic_bundle()["chart"]
    keys = ("open", "high", "low", "close", "volume")
    days = [datetime.date(1970, 1, 1) + datetime.timedelta(days=(t - 18000) // DAY)
            for t in chart["timestamp"]]

    rows = ["date,open,high,low,close,volume"] + [
        ",".join([d.isoformat()] + [str(chart[k][i]) for k in keys]) for i, d in enumerate(days)]
    (folder / "prices.csv").write_text("\n".join(rows) + "\n", encoding="ascii")
    args = cmds["brief"][:2] + ["DIR/prices.csv", "DIR/brief.json",
                                "--symbol", "TEST", "--currency", "USD"]
    out = run(args, desk_folder)
    assert out.returncode == 0, out.stderr
    sheet = out.stdout.splitlines()
    assert sheet[-1].startswith("BRIEF ok TEST ") and "session unknown" in sheet[0]
    flags = sheet[sheet.index("# Flags") + 1]
    assert "no_headlines" in flags and "statements_absent" in flags

    # The marketplace tool's saved result, passed in place of the bundle and unchanged.
    history = [dict({k: chart[k][i] for k in keys}, timestamp=t)
               for i, t in enumerate(chart["timestamp"])]
    saved = {"tool_id": "yahoo-finance:get_historical_data", "status": "succeeded",
             "data": {"status": "success",
                      "data": {"symbol": "TEST", "currency": "USD", "history": history}}}
    (folder / "bundle.json").write_text(json.dumps(saved), encoding="ascii")
    out = run(cmds["brief"], desk_folder)
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines()[-1].startswith("BRIEF ok TEST ")


def price_file(chart: dict, style: str) -> str:
    """The chart's rows as a price file, with the dates written in the given strftime style."""
    days = [datetime.date(1970, 1, 1) + datetime.timedelta(days=(t - 18000) // DAY)
            for t in chart["timestamp"]]
    keys = ("open", "high", "low", "close", "volume")
    rows = ["date,open,high,low,close,volume"] + [
        ",".join([d.strftime(style)] + [str(chart[k][i]) for k in keys])
        for i, d in enumerate(days)]
    return "\n".join(rows) + "\n"


def test_the_price_file_dates_must_be_written_as_the_skill_says(desk_folder):
    cmds, folder = skill_commands(), Path(desk_folder)
    chart = synthetic_bundle()["chart"]
    args = cmds["brief"][:2] + ["DIR/prices.csv", "DIR/brief.json",
                                "--symbol", "^TEST", "--currency", "USD"]
    (folder / "prices.csv").write_text(price_file(chart, "%Y-%m-%d"), encoding="ascii")
    out = run(args, desk_folder)
    assert out.returncode == 0 and out.stdout.splitlines()[-1].startswith("BRIEF ok ^TEST ")
    # Any other spelling is refused, and the stop sentence does not say why: the skill must.
    for style in ("%m/%d/%Y", "%d.%m.%Y", "%d %b %Y"):
        (folder / "prices.csv").write_text(price_file(chart, style), encoding="ascii")
        out = run(args, desk_folder)
        assert out.returncode == 2 and out.stderr.splitlines()[-1].startswith("STOP E_INPUT ")
        assert "YYYY" not in out.stderr and out.stdout == ""


def printed_flags(folder: str, bundle: dict) -> set[str]:
    (Path(folder) / "bundle.json").write_text(json.dumps(bundle), encoding="ascii")
    out = run(skill_commands()["brief"], folder)
    assert out.returncode == 0, out.stderr
    sheet = out.stdout.splitlines()
    line = sheet[sheet.index("# Flags") + 1]
    return set() if line == "none" else set(line.split(", "))


def test_every_flag_a_sheet_prints_has_a_row_in_step_2(desk_folder):
    table = set(re.findall(r"(?m)^\| `([a-z_]+)` \|", section(text_of(SKILL_MD), "Step 2")))
    # A short history with a missing row, no headlines, no statements, fetched in the session.
    live = live_bundle(n=150)
    live["chart"]["close"][-30] = None
    live["news"] = []
    live["statements"] = {k: [] for k in live["statements"]}
    seen = printed_flags(desk_folder, live)
    assert seen == {"unfinished_session", "rows_dropped", "short_history", "statements_absent",
                    "no_headlines"}
    # A finished session whose newest close the source has not published yet.
    late = synthetic_bundle()
    late["chart"]["close"][-1] = None
    assert printed_flags(desk_folder, late) == {"close_filled"}
    # The session before the newest one came back empty: the view ends before it.
    behind = synthetic_bundle()
    for key in ("open", "high", "low", "close", "volume"):
        behind["chart"][key][-2] = None
    assert printed_flags(desk_folder, behind) == {"prices_behind"}
    # A week of trading days is absent from the history.
    holiday = synthetic_bundle()
    for key in ("timestamp", "open", "high", "low", "close", "volume"):
        del holiday["chart"][key][-25:-20]
    assert printed_flags(desk_folder, holiday) == {"history_gap"}
    seen |= {"close_filled", "prices_behind", "history_gap"}
    assert seen == set(FLAGS)
    assert seen <= table, "a flag the sheet prints has no row in Step 2: %s" % (seen - table)


def render_with(folder: str, changes: dict[str, str]) -> subprocess.CompletedProcess:
    """Render the example draft of SKILL.md with some slots given new one-line content."""
    draft = example_draft() + "\n"
    for slot, line in changes.items():
        draft, n = re.subn(r"(?m)^\[%s\] .*$" % re.escape(slot),
                           lambda m: "[%s] %s" % (slot, line), draft)
        assert n == 1, slot
    (Path(folder) / "draft.txt").write_text(draft, encoding="ascii")
    return run(skill_commands()["render"], folder)


def test_the_wording_the_text_recommends_gets_through_the_renderer(desk_folder):
    folder = Path(desk_folder)
    rules = " ".join(section(text_of(ROLES_MD), "From the desk to the draft").split())
    # A statement figure is cited with |label, which prints its "as of" date.
    assert ('`{{name|label}}` and an evidence row print a statement figure with its "as of" '
            "date") in rules and "cite statement figures with `|label`" in rules
    period = "the newest reported period on the sheet"
    good = {"why": "The price is {{sma50_gap|dir}} its medium-term average.",
            "bull.also": "The statement figures are for %s." % period}
    live = live_bundle()
    refused = []
    for session, bundle, word in (("finished", synthetic_bundle(), "close"),
                                  ("unfinished", live, "price"), ("unknown", None, "price")):
        args = skill_commands()["brief"]
        if bundle is None:
            (folder / "prices.csv").write_text(
                price_file(live["chart"], "%Y-%m-%d"), encoding="ascii")
            args = args[:2] + ["DIR/prices.csv", "DIR/brief.json", "--symbol", "TEST"]
        else:
            (folder / "bundle.json").write_text(json.dumps(bundle), encoding="ascii")
        brief = run(args, desk_folder)
        assert brief.returncode == 0, brief.stderr
        assert "session " + session in brief.stdout.splitlines()[0]
        if session != "unknown":
            # The example as printed, headline reference included.
            done = render_with(desk_folder, {})
            assert done.returncode == 0, session + "\n" + done.stdout
        done = render_with(desk_folder, good)
        assert done.returncode == 0, session + "\n" + done.stdout
        assert done.stdout.count("Quantity words: 0, not checked.") == 1, period
        if session == "finished":
            dated = render_with(desk_folder, dict(good, risk="Valuation: {{pe_trailing|label}}. "
                                                              "The fund manager cut the size."))
            assert dated.returncode == 0, dated.stdout
            assert re.search(r"Valuation: trailing P/E [\d.]+ \(as of \d{4}-\d\d-\d\d\)\.",
                             dated.stdout), dated.stdout
        # The sentence an agent writes first: the newest price by its name on the sheet. That
        # name is the label of one fact, so another fact's figure is refused after it.
        trap = render_with(desk_folder, dict(good, why=good["why"].replace(
            "The price", "The last " + word)))
        assert trap.returncode == 1, session
        assert trap.stdout.startswith('FIX why: D_LABEL "last %s" labels another fact' % word)
        refused.append(session)
    assert refused == ["finished", "unfinished", "unknown"]
    assert '"last price"' in rules and '"last close"' in rules
    # The two flag rows of Step 2 name the wording that went through in every session.
    step = section(text_of(SKILL_MD), "Step 2")
    assert step.count('Say "the price"') == 2 and good["why"].startswith("The price is ")
    assert '"The price is {{sma50_gap|dir}} its medium-term average" is always accepted' in rules


# What the reviewers proved about the text

def test_the_clean_up_command_removes_only_a_folder_the_setup_step_made(tmp_path):
    # The command used to remove whatever folder was put in its place, with no message.
    cmds = skill_commands()
    line = " ".join(cmds["rmtree"])
    assert "startswith('desk-')" in line and "(p / 'brief.py').is_file()" in line
    assert "Pass only the folder from setup step 2" in section(text_of(SKILL_MD), "Step 6")
    mine = tmp_path / "my project"
    (mine / "src").mkdir(parents=True)
    (mine / "src" / "keep.txt").write_text("keep", encoding="ascii")
    looks_alike = tmp_path / "desk-top" / "work"
    looks_alike.mkdir(parents=True)
    # A folder of the user's own that happens to start with the same letters: no brief.py.
    own_desk = tmp_path / "desk-notes"
    own_desk.mkdir()
    (own_desk / "todo.txt").write_text("keep", encoding="ascii")
    (own_desk / "brief.py.txt").write_text("keep", encoding="ascii")
    (own_desk / "sub").mkdir()
    (own_desk / "sub" / "brief.py").write_text("keep", encoding="ascii")
    # The script alone is not enough either: the name must start with desk-.
    (mine / "brief.py").write_text("keep", encoding="ascii")
    for folder in (mine, looks_alike, own_desk, tmp_path):
        done = run(cmds["rmtree"], str(folder))
        assert done.returncode == 0 and done.stdout == done.stderr == ""
        assert folder.is_dir(), folder
    assert (mine / "src" / "keep.txt").read_text(encoding="ascii") == "keep"
    assert sorted(p.name for p in own_desk.iterdir()) == ["brief.py.txt", "sub", "todo.txt"]
    # The folder of setup step 2 goes once it holds brief.py, as it does from setup step 3
    # on, with or without a slash at its end. Before that it is empty and stays.
    env = dict(os.environ, TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path))
    for tail in ("", "/"):
        made = run(cmds["tempfile"], "", env).stdout.strip()
        assert Path(made).name.startswith("desk-") and os.path.isdir(made)
        assert run(cmds["rmtree"], made + tail).returncode == 0 and os.path.isdir(made)
        (Path(made) / "brief.json").write_text("{}", encoding="ascii")
        shutil.copyfile(SCRIPTS / "brief.py", Path(made) / "brief.py")
        assert run(cmds["rmtree"], made + tail).returncode == 0 and not os.path.exists(made)


def test_the_text_fits_a_user_who_brings_a_price_file():
    text = text_of(SKILL_MD)
    setup = section(text, "Setup for one run")
    # The sentence to the user is true for a run that downloads nothing.
    assert "Unless you give me a price file, the market data comes from " + HOST in setup
    step1 = section(text, "Step 1")
    before = step1.split("```sh")[0]
    # The user who already brought a file is not sent to a list of choices to wait at.
    for phrase in ("If the user gave a price file, use choice 2 below",
                   "a saved price history, choice 3",
                   "With no `get_data.py` in `DIR`, offer the choices below"):
        assert phrase in before, phrase
    choices = step1.split("### ")[1]
    assert "Offer the choices named for the stop, keep `DIR` and wait." in choices
    (two,) = [x for x in choices.splitlines() if x.startswith("2. ")]
    for phrase in ("text headed `date,open,high,low,close,volume`", "dates as YYYY-MM-DD",
                   "plain numbers", "60 daily rows or more",
                   "(`CODE`: its currency, `GBp` for pence; ask if unsure)",
                   "The sheet then has prices only."):
        assert phrase in two, phrase
    assert "If its newest date is more than a few days old, say so above the report" in choices
    assert "or its saved result if the user has that file" in choices
    # One rule about typed rows, with its one exception named where it stands.
    assert "Never retype price rows by hand into a file. That section holds the one exception." \
        in choices
    # Saved as the scripts are: an encoding mark in front of a file fails the preflight.
    assert "as plain ASCII with no byte-order mark" in setup


def test_the_message_is_the_printed_report_and_only_the_lines_the_text_asks_for():
    step = section(text_of(SKILL_MD), "Step 5")
    assert "it printed the report. Your message holds that printed text, complete and " \
        "unchanged. Only lines this file or `roles.md` asks for may stand around it." in step
    assert "then the receipt" not in step
    roles = " ".join(text_of(ROLES_MD).split())
    # The offer of the full debate is the agent's line now, not the script's.
    assert "Below the report you may add one line of your own" in roles
    assert "full debate (every role's notes, which the script does not check)" in roles
    assert "short version" in roles
    assert "full debate" not in text_of(SCRIPTS / "render.py")


def test_roles_md_says_what_the_renderer_and_the_brief_now_do():
    roles = " ".join(text_of(ROLES_MD).split())
    for phrase in (
            # a dropped row
            "If it carries `rows_dropped`, a change or an opening gap dated right after a "
            "dropped row spans two sessions",
            # third-party text on the sheet's first line
            "so is the company name on the sheet's first line",
            # money in its own currency
            "A money figure names its currency, which is often not the currency of the price",
            "never set figures in different currencies against each other",
            # numbers in words
            'A number spelled out in words ("thirty", "a dozen") is a typed figure in disguise',
            "Quantity words and numbers in words are counted, not checked.",
            # nothing touches a reference
            "Let nothing but a space or ordinary punctuation touch a reference",
            # what the receipt does not cover
            "that the sentence around a figure is right or names the right fact",
            "every figure on the report is a figure from the data file",
            # the marketplace route
            "Before any charge: call the tool only if your host saves a tool's result as a "
            "file that you can pass by path",
            "Do not use this route for a listing in Australia or New Zealand",
            "`brief.py` stops with `E_DATES`",
            "Add no `--symbol` and no `--currency`: the file names both",
            # a stopped run
            "remove the folder with the last command of Step 6"):
        assert phrase in roles, phrase
    # The meter words of the page are the conventions the roles reason with.
    table = section(text_of(ROLES_MD), "Indicator conventions")
    mfi = [row for row in table.splitlines() if row.startswith("| `mfi14`")][0]
    adx = [row for row in table.splitlines() if row.startswith("| `adx14`")][0]
    assert "overbought" in mfi and "oversold" in mfi
    assert "weak trend" in adx and "strong trend" in adx
    page = text_of(SCRIPTS / "page.py")
    assert '"weak trend", "strong trend"' in page and '"oversold", "overbought"' in page


def test_the_verify_command_in_roles_md_works_as_printed(desk_folder):
    roles = " ".join(text_of(ROLES_MD).split())
    command = re.search(r"`(PY \"DIR/render\.py\" --verify [^`]+)`", roles).group(1)
    args = shlex.split(command)
    assert args == ["PY", "DIR/render.py", "--verify", "DIR/brief.json", "DIR/report.md"]
    assert "`VERIFY ok` or the differences" in roles
    cmds, folder = skill_commands(), Path(desk_folder)
    (folder / "bundle.json").write_text(json.dumps(synthetic_bundle()) + "\n", encoding="ascii")
    assert run(cmds["brief"], desk_folder).returncode == 0
    (folder / "draft.txt").write_text(example_draft() + "\n", encoding="ascii")
    assert run(cmds["render"], desk_folder).returncode == 0
    done = run(args, desk_folder)
    assert (done.returncode, done.stdout, done.stderr) == (0, "VERIFY ok\n", "")
    report = (folder / "report.md").read_text(encoding="ascii")
    (folder / "report.md").write_text(report.replace("lost to", "beat"), encoding="ascii")
    done = run(args, desk_folder)
    assert done.returncode == 1 and done.stdout.startswith("DIFF ")



# The fix round after the live checks of 2026-11-11: the texts against the scripts

BUSY = ("the data source is limiting requests from this network right now; this is not a "
        "problem with the ticker, and a new run in a few minutes may work")
REFUSED = ("the data source refused this network; this is not a problem with the ticker and "
           "waiting rarely helps")
DOT_HINT = ("; a US share class is written with a hyphen there, not a dot (BRK-B, not BRK.B); "
            "for another exchange, check the letters after the dot")
NOTE_LINE = ('FIX why: D_NOTE the session may be unfinished: say "the price", not "close" | '
             "try: the price")
FILE_NOTE = "These prices came from a file; when they were downloaded is not known."
STATEMENTS_FAILED = "The statement request failed, so these facts are absent: "
STATEMENTS_NONE = "The source gave no statement figure, so these facts are absent: "
HEADLINES_FAILED = "The headline request failed."
HEADLINES_NONE = "No headlines came with this data."
PLAIN_WHY = "The price is {{sma50_gap|dir}} its medium-term average."
SOURCE_CLOCK = "Tue, 10 Nov 2026 22:00:00 GMT"  # an hour after the newest generated session


def flat(path: Path) -> str:
    """A text as one line, so that a sentence is found wherever its lines break."""
    return " ".join(text_of(path).split())


def load_script(name: str):
    """A shipped script as a module, with no bytecode cache left beside it."""
    spec = importlib.util.spec_from_file_location("skilltext_" + name[:-3], SCRIPTS / name)
    module = importlib.util.module_from_spec(spec)
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = keep
    return module


def chart_answer(bundle: dict) -> dict:
    """The data source's chart answer for a bundle's rows, in the shape get_data.py reads."""
    chart = bundle["chart"]
    quote = {k: chart[k] for k in ("open", "high", "low", "close", "volume")}
    return {"chart": {"result": [{"meta": chart["meta"], "timestamp": chart["timestamp"],
                                  "indicators": {"quote": [quote]}}], "error": None}}


class Download:
    """One run of get_data.py against a stand-in for the data source: no network, no waiting.

    ``statuses`` gives, for the request ``chart``, ``news`` or ``statements``, the HTTP
    statuses the source answers with, in order; the last one repeats. ``headlines`` are the
    items of the headline answer, and ``bundle`` holds the rows and the session facts of the
    chart answer (the generated share when none is given). The script's pause is noted in
    ``waits``, never taken."""

    def __init__(self, out_path, symbol="TEST", headlines=(), bundle=None, **statuses):
        script = load_script("get_data.py")
        bodies = {"chart": chart_answer(bundle or synthetic_bundle()),
                  "news": {"news": list(headlines)},
                  "statements": {"timeseries": {"result": []}}}
        plan = {kind: list(statuses.get(kind, (200,))) for kind in bodies}
        self.asked, self.waits, self.wait = [], [], script.WAIT

        def answer(url):
            assert url.startswith("https://%s/" % HOST), url
            kind = ("chart" if "/v8/finance/chart/" in url else
                    "news" if "/v1/finance/search?" in url else "statements")
            self.asked.append(kind)
            status = plan[kind].pop(0) if len(plan[kind]) > 1 else plan[kind][0]
            body = json.dumps(bodies[kind]) if status == 200 else ""
            # With a rate limit the source names a wait of its own, far longer than WAIT.
            wish = {"Retry-After": "120"} if status == 429 else {}
            return status, dict(wish, Date=SOURCE_CLOCK), body.encode("ascii")

        out, err = io.StringIO(), io.StringIO()
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(script, "http_get", answer)
            patch.setattr(script.time, "sleep", self.waits.append)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.code = script.main([symbol, str(out_path)])
        self.stdout, self.stderr = out.getvalue(), err.getvalue()


def step_1_rows() -> dict[str, str]:
    """What Step 1 of SKILL.md says to do after each stop code of the download."""
    rows = {}
    for cell, action in re.findall(r"(?m)^\| (`E_[^|]+) \| (.+) \|$",
                                   section(text_of(SKILL_MD), "Step 1")):
        for code in re.findall(r"E_[A-Z_]+", cell):
            assert code not in rows, code
            rows[code] = action
    return rows


def sheet_notes(sheet: str) -> list[str]:
    lines = sheet.splitlines()
    return [x.split(" | ", 1)[1] for x in lines[lines.index("# Notes") + 1:lines.index("# Flags")]]


def sheet_flags(sheet: str) -> str:
    lines = sheet.splitlines()
    return lines[lines.index("# Flags") + 1]


def test_step_1_says_what_comes_after_each_stop_of_the_download():
    rows = step_1_rows()
    assert set(rows) == script_stop_codes("get_data.py")
    later = "Stop. The user may ask again later. Offer choices 2 and 3 below."
    assert rows["E_BUSY"] == rows["E_SERVER"] == later
    assert rows["E_REFUSED"] == rows["E_CERT"] == "Stop. Offer choices 2 and 3 below."
    assert rows["E_NO_ROUTE"] == "Stop. Offer the three choices below."
    assert rows["E_SYMBOL"] == rows["E_NOT_FOUND"] == "Stop. The user may give another symbol."
    assert rows["E_SHAPE"] == "Stop."
    # The rows agree with the sentences the script prints: the user is told to ask again
    # later exactly where the sentence says that it may work then, and choice 1 (allow the
    # host) is offered only where the source was not reached at all.
    sentences = load_script("get_data.py").STOPS
    assert sentences["E_BUSY"] == BUSY and sentences["E_REFUSED"] == REFUSED
    assert {c for c, s in sentences.items() if "may work" in s} == {
        c for c, row in rows.items() if "again later" in row} == {"E_BUSY", "E_SERVER"}
    assert {c for c, s in sentences.items() if "could not reach" in s} == {
        c for c, row in rows.items() if "three choices" in row} == {"E_NO_ROUTE"}
    # The contract holds both sentences word for word, and the new code.
    rule = flat(CONTRACTS)
    assert '"%s"' % BUSY in rule and '"%s"' % REFUSED in rule
    assert "`E_BUSY` (429 again after the one new attempt" in rule
    assert "`E_REFUSED` (401, 403, 999, after one attempt" in rule
    # roles.md sends the agent back to these rows.
    assert ("Add the choices that `SKILL.md` lists for that stop code, if any"
            in flat(ROLES_MD))


@pytest.mark.parametrize("status, code, tries", [
    (401, "E_REFUSED", 1), (403, "E_REFUSED", 1), (999, "E_REFUSED", 1), (429, "E_BUSY", 2),
    (404, "E_NOT_FOUND", 1), (503, "E_SERVER", 1), (302, "E_SHAPE", 1)])
def test_a_stop_of_the_download_is_one_line_on_stderr_that_ends_with_the_status(
        tmp_path, status, code, tries):
    got = Download(tmp_path / "bundle.json", chart=(status,))
    assert (got.code, got.stdout) == (2, "")
    (line,) = got.stderr.splitlines()
    sentence = load_script("get_data.py").STOPS[code]
    assert line == "STOP %s %s (HTTP %d)" % (code, sentence, status)
    assert code in step_1_rows()
    # One attempt, and only after a rate limit one pause and one more.
    assert got.asked == ["chart"] * tries
    assert got.waits == [got.wait] * (tries - 1)
    rule = flat(CONTRACTS)
    assert "A stop that came from an HTTP status ends with that status in round brackets" in rule
    for shown in ("`... waiting rarely helps (HTTP 403)`", "`(HTTP 429)`", "`(HTTP 404)`",
                  "`(HTTP 503)`"):
        assert shown in rule, shown


def test_a_rate_limit_gets_one_pause_of_seven_seconds_and_one_new_attempt(tmp_path):
    # The limit clears: the run goes on as if nothing had happened.
    got = Download(tmp_path / "bundle.json", chart=(429, 200))
    assert (got.code, got.stderr) == (0, "") and got.stdout.startswith("DATA ok TEST bars=260 ")
    assert got.asked == ["chart", "chart", "news", "statements"] and got.waits == [got.wait]
    # The number the contract and the listing name is the script's.
    assert got.wait == 7 and type(got.wait) is int
    rule = flat(CONTRACTS)
    assert ("After HTTP 429 (a rate limit) on any of the three requests the script waits `WAIT` "
            "seconds (a module constant, 7) once and sends that request once more.") in rule
    assert "No retries, with one exception." in rule and "no retries," not in rule
    # The stand-in sends "Retry-After: 120" with every 429: the pause above was still WAIT.
    assert ("The source's `Retry-After` header is not read: the pause is always `WAIT` "
            "seconds.") in rule
    assert ("When the source answers a request with a rate limit, the script waits seven "
            "seconds and sends that request once more.") in flat(LISTING)
    # A stop with no HTTP status behind it ends with its sentence.
    got = Download(tmp_path / "bundle.json", symbol="..")
    assert got.code == 2 and got.asked == [] and got.stderr.startswith("STOP E_SYMBOL ")
    assert "HTTP" not in got.stderr


def test_the_not_found_hint_speaks_of_us_share_classes_and_of_other_exchanges(tmp_path):
    got = Download(tmp_path / "bundle.json", symbol="BRK.B", chart=(404,))
    assert got.code == 2 and got.stderr.rstrip("\n").endswith(DOT_HINT + " (HTTP 404)")
    assert '"%s"' % DOT_HINT in flat(CONTRACTS)
    plain = Download(tmp_path / "bundle.json", symbol="BRKB", chart=(404,))
    assert plain.stderr == ("STOP E_NOT_FOUND the data source has no data for this symbol; "
                            "check the spelling (HTTP 404)\n")


def test_a_failed_follow_up_request_is_named_as_failed_from_the_data_line_to_the_report(
        desk_folder):
    cmds, folder = skill_commands(), Path(desk_folder)
    # The source answers both follow-up requests and has nothing.
    got = Download(folder / "bundle.json")
    assert (got.code, got.stderr) == (0, "")
    assert got.stdout == "DATA ok TEST bars=260 headlines=0 statements=absent\n"
    nothing = run(cmds["brief"], desk_folder).stdout
    assert [x[:len(STATEMENTS_NONE)] for x in sheet_notes(nothing)] == [
        STATEMENTS_NONE, HEADLINES_NONE]
    # The source limits both follow-up requests, at the one new attempt too.
    got = Download(folder / "bundle.json", news=(429,), statements=(429,))
    assert (got.code, got.stderr) == (0, "")
    assert got.stdout == "DATA ok TEST bars=260 headlines=failed statements=failed\n"
    assert got.asked == ["chart", "news", "news", "statements", "statements"]
    assert got.waits == [got.wait] * 2
    saved = json.loads((folder / "bundle.json").read_text(encoding="ascii"))
    assert saved["errors"] == {"news": "HTTP 429", "statements": "HTTP 429"}
    failed = run(cmds["brief"], desk_folder).stdout
    assert [x[:len(STATEMENTS_FAILED)] for x in sheet_notes(failed)] == [
        STATEMENTS_FAILED, HEADLINES_FAILED]
    # The facts named and the flags are the same; only the reason differs.
    assert sheet_notes(failed)[0][len(STATEMENTS_FAILED):] \
        == sheet_notes(nothing)[0][len(STATEMENTS_NONE):]
    assert sheet_flags(failed) == sheet_flags(nothing) == "statements_absent, no_headlines"
    # The report carries the notes, so the reader is told too.
    done = render_with(desk_folder, {"why": PLAIN_WHY})
    assert done.returncode == 0, done.stdout
    notes = done.stdout[done.stdout.index("**Data notes**"):]
    assert "- " + STATEMENTS_FAILED in notes and "- " + HEADLINES_FAILED in notes
    # A limit that clears at the new attempt leaves no trace.
    got = Download(folder / "bundle.json", news=(429, 200), statements=(429, 200))
    assert got.stdout == "DATA ok TEST bars=260 headlines=0 statements=absent\n"
    assert got.waits == [got.wait] * 2
    # The three texts say so.
    rule = flat(CONTRACTS)
    assert "`DATA ok <SYMBOL> bars=<n> headlines=<n|failed> statements=<ok|absent|failed>`" in rule
    for sentence in (STATEMENTS_FAILED, STATEMENTS_NONE, HEADLINES_FAILED, HEADLINES_NONE):
        assert '"%s' % sentence in rule, sentence
    assert ("When only the headline request or the statement request fails, the report says "
            "that the request failed, and the desk runs without the roles that need them."
            ) in flat(LISTING)
    step2 = dict(re.findall(r"(?m)^\| `([a-z_]+)` \| (.+) \|$", section(text_of(SKILL_MD),
                                                                       "Step 2")))
    assert step2["statements_absent"] == "Skip the fundamentals analyst."
    assert step2["no_headlines"] == "Skip the news and sentiment analysts."


def test_a_headline_cannot_end_its_own_quotation_with_a_double_quote_of_any_kind(desk_folder):
    script = load_script("get_data.py")
    doubles = [chr(n) for n in (0x201C, 0x201D, 0x201E, 0x201F, 0xFF02)]
    marks = [chr(n) for n in (list(range(0x200B, 0x2010)) + list(range(0x202A, 0x202F))
                              + list(range(0x2066, 0x206A)) + [0xFEFF])]
    for char in doubles:
        assert script.clean("a%sb" % char, 160) == 'a"b', hex(ord(char))
    for char in marks:
        assert script.clean("a%sb" % char, 160) == "ab", hex(ord(char))
    rule = flat(CONTRACTS)
    for phrase in ("typographic and fullwidth double quotes (U+201C to U+201F and U+FF02) become "
                   "the plain double quote",
                   "zero-width characters and direction marks (U+200B to U+200F, U+202A to "
                   "U+202E, U+2066 to U+2069) and U+FEFF are dropped"):
        assert phrase in rule, phrase
    # From the source's answer to the report: the title's own double quotes are single ones
    # there, and nothing invisible is left. The limit both texts state stands beside it:
    # guillemets and two apostrophes in a row are left as they are.
    cmds, folder = skill_commands(), Path(desk_folder)
    title = ("Test falls%s and the desk sees %sa rally%s" % (doubles[1], doubles[0], doubles[4])
             + marks[0] + marks[9] + marks[-1] + " %sstrong%s and ''firm''" % (chr(171), chr(187)))
    stamp = synthetic_bundle()["chart"]["timestamp"][-1] + 3600
    item = {"title": title, "publisher": "Example Wire", "providerPublishTime": stamp,
            "relatedTickers": ["TEST"]}
    got = Download(folder / "bundle.json", headlines=[item])
    assert got.stdout == "DATA ok TEST bars=260 headlines=1 statements=absent\n"
    assert run(cmds["brief"], desk_folder).returncode == 0
    done = render_with(desk_folder, {"why": "The newest headline is {{h1}}."})
    assert done.returncode == 0, done.stdout
    (why,) = [x for x in done.stdout.splitlines() if x.startswith("**Why.** ")]
    assert why == ("**Why.** The newest headline is \"Test falls' and the desk sees 'a rally' "
                   "&#171;strong&#187; and ''firm''\" (Example Wire, 2026-11-10).")
    for limit in ("A headline can still end its own quotation with guillemets or with two "
                  "apostrophes",):
        assert limit in rule and limit in flat(LISTING)


def script_imports(name: str) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(text_of(SCRIPTS / name))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module)
    return found


def test_the_imports_are_the_ones_the_contract_allows_and_time_is_for_the_download_only():
    rule = section(contract(), "1. ").split("Allowed imports: `", 1)[1]
    allowed = {x.strip() for x in rule.split("`", 1)[0].replace("\n", " ").split(",")}
    network = {"urllib.request", "urllib.parse", "urllib.error", "time"}
    assert network < allowed and len(allowed) == 11
    for name in sorted(contract_scripts()):
        used = script_imports(name)
        assert used <= allowed, (name, used - allowed)
        assert used & network == (network if name == "get_data.py" else set()), name
    # The clock has one use: the pause before the one new attempt.
    source = text_of(SCRIPTS / "get_data.py")
    assert re.findall(r"\btime\.\w+\(.*?\)", source) == ["time.sleep(WAIT)"]
    assert "`time` only in `get_data.py`" in flat(CONTRACTS)


def stamp_of(name: str) -> str:
    return re.search(r'(?m)^SELF = "([0-9a-f]{12})"$', text_of(SCRIPTS / name)).group(1)


def contract_hash(data: bytes) -> str:
    """The hash rule of contract section 1, written out once more: SHA-256 over the bytes
    with LF line endings and no trailing whitespace, each stamp line read as the word SELF."""
    lines = [x.rstrip() for x in data.replace(b"\r\n", b"\n").split(b"\n")]
    lines = [b"SELF" if re.fullmatch(rb'SELF = "[0-9a-f]{12}"', x) else x for x in lines]
    return hashlib.sha256(b"\n".join(lines).rstrip()).hexdigest()[:12]


def test_every_stamp_is_the_hash_the_contract_describes():
    rule = flat(CONTRACTS)
    assert ('where each line that is exactly `SELF = "<12 hex>"` is read as the one word `SELF`'
            in rule)
    assert "a second stamp line, a stamp line that was moved and a missing one" in rule
    assert "with the stamp line removed" not in rule
    stamps = {name: stamp_of(name) for name in sorted(contract_scripts())}
    for name, stamp in stamps.items():
        assert stamp == contract_hash(shipped_bytes(SCRIPTS / name)), name
    siblings = re.search(r"(?m)^SIBLINGS = (\{.+\})$", text_of(SCRIPTS / "brief.py")).group(1)
    assert ast.literal_eval(siblings) == {k: v for k, v in stamps.items() if k != "brief.py"}


def second_stamp(text: str, line: str) -> str:
    return text.replace(line, line + line, 1)


def moved_stamp(text: str, line: str) -> str:
    # One line down: the program is the same, the place of the stamp is not.
    after = text.split(line, 1)[1].split("\n", 1)[0] + "\n"
    assert re.match(r"[A-Z]+ = ", after), after
    return text.replace(line + after, after + line, 1)


def missing_stamp(text: str, line: str) -> str:
    return text.replace(line, "", 1)


@pytest.mark.parametrize("name", ["brief.py", "get_data.py", "page.py", "render.py"])
@pytest.mark.parametrize("change", [second_stamp, moved_stamp, missing_stamp],
                         ids=lambda f: f.__name__)
def test_a_second_a_moved_or_a_missing_stamp_line_is_refused(tmp_path, name, change):
    for script in contract_scripts():
        shutil.copyfile(SCRIPTS / script, tmp_path / script)
    cmds = skill_commands()
    assert run(cmds["preflight"], str(tmp_path)).stdout.startswith("PREFLIGHT ok ")
    text = text_of(tmp_path / name)
    line = 'SELF = "%s"\n' % stamp_of(name)
    assert text.count(line) == 1
    (tmp_path / name).write_bytes(change(text, line).encode("ascii"))
    pre = run(cmds["preflight"], str(tmp_path))
    assert pre.returncode == 3 and "PREFLIGHT" not in pre.stdout, pre.stdout
    assert pre.stderr.startswith("ERROR E_SELF " if name == "brief.py" else "ERROR E_SIBLING ")


def test_limit_a_copy_with_the_bare_word_self_for_its_stamp_line_passes_the_preflight(tmp_path):
    # The other side of the hash rule, stated in contract section 8: the word that stands
    # in for the stamp line hashes like the stamp line. The copy then fails as Python does.
    for script in contract_scripts():
        shutil.copyfile(SCRIPTS / script, tmp_path / script)
    text = text_of(tmp_path / "render.py")
    line = 'SELF = "%s"\n' % stamp_of("render.py")
    (tmp_path / "render.py").write_bytes(text.replace(line, "SELF\n", 1).encode("ascii"))
    cmds = skill_commands()
    pre = run(cmds["preflight"], str(tmp_path))
    assert pre.returncode == 0 and pre.stdout.startswith("PREFLIGHT ok "), pre.stderr
    ran = run(cmds["render"][:2] + ["--version"], str(tmp_path))
    assert ran.returncode == 1 and ran.stdout == ""
    assert ran.stderr.rstrip().endswith("NameError: name 'SELF' is not defined")
    assert ("A copy whose stamp line is the bare word `SELF` has the hash of the stamped "
            "script. Such a copy of a sibling passes the preflight and then ends with Python's "
            "own traceback, a name error.") in flat(CONTRACTS)


@pytest.mark.skipif(os.name == "nt", reason="no folder name ends with a backslash there")
def test_the_preflight_takes_the_folder_as_it_was_given(tmp_path):
    # A separator at the end of the folder is not stripped, of either kind.
    folder = tmp_path / "desk-odd\\"
    folder.mkdir()
    for script in contract_scripts():
        shutil.copyfile(SCRIPTS / script, folder / script)
    cmds = skill_commands()
    for given in (str(folder), str(folder) + "/"):
        pre = run(cmds["preflight"][:3] + [given], str(folder))
        assert pre.returncode == 0 and pre.stdout.startswith("PREFLIGHT ok "), pre.stderr
        assert pre.stdout.rstrip().endswith(" checked brief.py get_data.py page.py render.py")
    assert "no separator is stripped from its end" in flat(CONTRACTS)


def test_page_py_can_wait_until_step_6_and_is_checked_before_it_runs(tmp_path):
    cmds = skill_commands()
    env = dict(os.environ, TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path))
    folder = run(cmds["tempfile"], "", env).stdout.strip()
    # Setup step 3 for a host with no internet: the two scripts every run needs.
    for name in ("brief.py", "render.py"):
        shutil.copyfile(SCRIPTS / name, Path(folder) / name)
    pre = run(cmds["preflight"], folder)
    assert pre.returncode == 0 and pre.stdout.rstrip().endswith(" checked brief.py render.py")
    (Path(folder) / "bundle.json").write_text(json.dumps(synthetic_bundle()), encoding="ascii")
    assert run(cmds["brief"], folder).returncode == 0
    (Path(folder) / "draft.txt").write_text(example_draft() + "\n", encoding="ascii")
    assert run(cmds["render"], folder).returncode == 0
    # Step 6: the page script arrives, the preflight names it, and only then it draws.
    step6 = section(text_of(SKILL_MD), "Step 6")
    assert "If `page.py` is not in `DIR`, put it there and run the preflight first" in step6
    missing = run(cmds["page"], folder)
    assert missing.returncode == 2 and "STOP" not in missing.stderr  # Python's own failure
    shutil.copyfile(SCRIPTS / "page.py", Path(folder) / "page.py")
    pre = run(cmds["preflight"], folder)
    assert pre.stdout.rstrip().endswith(" checked brief.py page.py render.py")
    drawn = run(cmds["page"], folder)
    assert drawn.returncode == 0 and drawn.stdout.startswith("PAGE ok TEST ")
    assert run(cmds["rmtree"], folder).returncode == 0 and not os.path.exists(folder)


def test_every_stop_line_of_the_file_scripts_is_on_stderr_with_exit_code_2(desk_folder):
    # "A line that starts with `STOP` (exit code 2, on stderr)": brief, render and page.
    cmds, folder = skill_commands(), Path(desk_folder)
    (folder / "bundle.json").write_text("not a bundle\n", encoding="ascii")
    stops = [run(cmds["brief"], desk_folder)]
    (folder / "brief.json").write_text("{}\n", encoding="ascii")
    (folder / "draft.txt").write_text(example_draft() + "\n", encoding="ascii")
    stops.append(run(cmds["render"], desk_folder))
    (folder / "report.json").write_text("{}\n", encoding="ascii")
    stops.append(run(cmds["page"], desk_folder))
    codes = []
    for stop in stops:
        assert (stop.returncode, stop.stdout) == (2, "")
        (line,) = stop.stderr.splitlines()
        codes.append(re.fullmatch(r"STOP (E_[A-Z]+) \S.*", line).group(1))
    assert codes == ["E_INPUT", "E_BRIEF", "E_INPUT"]


def calendar_file(days: list[datetime.date], peak_back: int) -> str:
    """A price file with one row for each date. The close ``peak_back`` rows before the
    newest stands far above every other; the closes depend on the row's place only."""
    rows = ["date,open,high,low,close,volume"]
    for i, day in enumerate(days):
        close = 150.0 if i == len(days) - 1 - peak_back else 100.0 + i % 5 / 10
        rows.append("%s,%s,%s,%s,%s,1000" % (day.isoformat(), close, close + 1, close - 1, close))
    return "\n".join(rows) + "\n"


def test_a_market_with_a_bar_on_every_day_has_365_bars_in_a_year(desk_folder):
    cmds, folder = skill_commands(), Path(desk_folder)
    newest = datetime.date(2026, 11, 10)
    back = [newest - datetime.timedelta(days=i) for i in range(600)]
    every = back[:400][::-1]
    weekdays = [d for d in back if d.weekday() < 5][:400][::-1]

    def holes(count: int) -> list[datetime.date]:
        """A bar on every day but ``count`` single days: the newest 60 rows then span
        59 + count calendar days, and no break in the dates is long enough for a flag."""
        gone = {2 * k for k in range(1, count + 1)}
        return [d for i, d in enumerate(back) if i not in gone][:400][::-1]

    args = cmds["brief"][:2] + ["DIR/prices.csv", "DIR/brief.json", "--symbol", "TEST"]
    seen = {}
    for days, bars in ((every, 365), (weekdays, 252), (holes(8), 365), (holes(9), 252)):
        assert ((days[-1] - days[-60]).days < 68) == (bars == 365)
        # The highest close is 300 rows back: inside a year of 365 bars, outside one of 252.
        (folder / "prices.csv").write_text(calendar_file(days, 300), encoding="ascii")
        out = run(args, desk_folder)
        assert out.returncode == 0, out.stderr
        assert "history_gap" not in sheet_flags(out.stdout)
        facts = json.loads((folder / "brief.json").read_text(encoding="ascii"))["facts"]
        assert (facts["high52"]["v"] == 150.0) == (bars == 365), bars
        assert (facts["high52_date"]["v"] == days[-301].isoformat()) == (bars == 365), bars
        assert facts["high52"]["label"] == "52-week high close"
        # The same closes in every file: only the bars in a year differ in the annualising.
        assert seen.setdefault(bars, facts["vol21"]["v"]) == facts["vol21"]["v"]
    assert seen[365] / seen[252] == pytest.approx((365 / 252) ** 0.5, rel=1e-12)
    assert [(d[-1] - d[-60]).days for d in (every, holes(8), holes(9))] == [59, 67, 68]
    rule = flat(CONTRACTS)
    assert ("Bars in a year: 365 when the newest 60 kept rows span fewer than 68 calendar days "
            "(a market with a bar on every day, such as crypto, whatever the input shape or "
            "the instrument type), else 252.") in rule
    assert "sqrt(252)" not in rule and "the newest 252 bars" not in rule
    # roles.md names the window by its length in time, and warns about crypto volume.
    table = section(text_of(ROLES_MD), "Indicator conventions")
    assert "highest and lowest daily close of the past year" in table
    (volume,) = [x for x in table.splitlines() if x.startswith("| `vol_ratio`")]
    assert "Not for crypto: there the volume is a rolling 24-hour turnover." in volume


def test_a_ratio_or_a_change_across_two_currencies_is_absent_and_the_note_names_it(desk_folder):
    cmds, folder = skill_commands(), Path(desk_folder)
    paired = ("margin_ttm", "debt_equity", "rev_q_yoy", "ni_q_yoy")

    def sheet(other: str) -> list[str]:
        """Net income, debt and the newest quarter in USD; their partners in ``other``."""
        bundle = synthetic_bundle()
        asof = bundle["statements"]["trailingPeRatio"][0]["asOf"]
        old = (datetime.date.fromisoformat(asof) - datetime.timedelta(days=365)).isoformat()

        def money(day, value, currency):
            return {"asOf": day, "value": value, "currency": currency}

        bundle["statements"].update(
            trailingTotalRevenue=[money(asof, 9.0e10, other)],
            trailingNetIncome=[money(asof, 3.0e10, "USD")],
            quarterlyTotalDebt=[money(asof, 1.0e10, "USD")],
            quarterlyStockholdersEquity=[money(asof, 7.0e10, other)],
            quarterlyTotalRevenue=[money(old, 2.0e10, other), money(asof, 2.7e10, "USD")],
            quarterlyNetIncome=[money(old, 2.0e9, other), money(asof, 2.7e9, "USD")])
        (folder / "bundle.json").write_text(json.dumps(bundle), encoding="ascii")
        out = run(cmds["brief"], desk_folder)
        assert out.returncode == 0, out.stderr
        return out.stdout.splitlines()

    one = sheet("USD")
    assert [x.split(" | ")[0] for x in one if x.split(" | ")[0] in paired] == [
        "margin_ttm", "rev_q_yoy", "ni_q_yoy", "debt_equity"]
    two = sheet("EUR")
    assert not [x for x in two if x.split(" | ")[0] in paired]
    # The figures themselves stay, each with its own currency.
    assert "rev_ttm | revenue, trailing 12 months | 90.00B EUR" in " ".join(two)
    assert "ni_ttm | net income, trailing 12 months | 30.00B USD" in " ".join(two)
    (note,) = [x for x in sheet_notes("\n".join(two)) if x.startswith(STATEMENTS_NONE)]
    assert set(paired) <= set(note[len(STATEMENTS_NONE):].rstrip(".").split(", "))
    rule = flat(CONTRACTS)
    for phrase in ("`margin_ttm` and `debt_equity` are computed only when both of their figures "
                   "carry the same date and the same currency",
                   "`rev_q_yoy` and `ni_q_yoy` are computed only against a figure dated a year "
                   "earlier (365 days, give or take 20) in the same currency",
                   "Otherwise the fact is absent and named in the note on absent statement "
                   "facts"):
        assert phrase in rule, phrase


@pytest.mark.parametrize("symbol", ["..", "-", "^^", "="])
def test_brief_py_takes_no_symbol_without_a_letter_or_a_digit(desk_folder, symbol):
    cmds, folder = skill_commands(), Path(desk_folder)
    chart = synthetic_bundle()["chart"]
    (folder / "prices.csv").write_text(price_file(chart, "%Y-%m-%d"), encoding="ascii")
    args = cmds["brief"][:2] + ["DIR/prices.csv", "DIR/brief.json", "--symbol", symbol]
    out = run(args, desk_folder)
    assert (out.returncode, out.stdout) == (2, "")
    assert out.stderr == "STOP E_SYMBOL the symbol is not a plain ticker\n"
    assert not (folder / "brief.json").exists()
    # One letter is a ticker.
    assert run(args[:-1] + ["F"], desk_folder).stdout.splitlines()[-1].startswith("BRIEF ok F ")
    assert ("The symbol MUST be a plain ticker as in section 2, with the same characters and at "
            "least one letter or digit, else `STOP E_SYMBOL`.") in flat(CONTRACTS)
    assert "`E_SYMBOL`" in section(text_of(SKILL_MD), "Step 2")


def test_a_price_file_is_taken_as_it_is_and_only_a_download_can_be_too_old(desk_folder):
    cmds, folder = skill_commands(), Path(desk_folder)
    chart = synthetic_bundle()["chart"]
    # The same rows dated six years back, a whole number of weeks: brief.py cannot know.
    aged = dict(chart, timestamp=[t - 312 * 7 * DAY for t in chart["timestamp"]])
    (folder / "prices.csv").write_text(price_file(aged, "%Y-%m-%d"), encoding="ascii")
    args = cmds["brief"][:2] + ["DIR/prices.csv", "DIR/brief.json",
                                "--symbol", "TEST", "--currency", "GBp"]
    out = run(args, desk_folder)
    assert out.returncode == 0, out.stderr
    sheet = out.stdout
    # The currency code of choice 2 for a price in pence gives the unit and a note.
    assert sheet.splitlines()[0] == "# TEST | TEST | UNKNOWN | pence | session unknown"
    assert sheet.splitlines()[-1].startswith("BRIEF ok TEST 2020-11-17 ")
    notes = sheet_notes(sheet)
    assert "Prices are in pence (GBp), a hundredth of the main currency unit." in notes
    assert FILE_NOTE in notes and sheet_flags(sheet) == "statements_absent, no_headlines"
    assert not [x for x in notes if re.search(r"\bold\b|stale|days", x)]
    # "The sheet then has prices only."
    assert not re.search(r"(?m)^(pe_trailing|mcap|h1) \| ", sheet)
    done = render_with(desk_folder, {"why": PLAIN_WHY})
    assert done.returncode == 0, done.stdout
    header = done.stdout.splitlines()[2]
    assert header.startswith("Desk view at the 2020-11-17 last price")
    assert header.endswith("prices from a file") and "fetched" not in done.stdout
    # A download is another matter: its fetch time is known.
    for days, code in ((12, 0), (13, 2)):
        bundle = synthetic_bundle()
        bundle["fetched_at"] = bundle["chart"]["timestamp"][-1] + days * DAY
        (folder / "bundle.json").write_text(json.dumps(bundle), encoding="ascii")
        out = run(cmds["brief"], desk_folder)
        assert out.returncode == code, days
    assert out.stderr == ("STOP E_STALE the newest row (2026-11-10) is more than 12 days older "
                          "than the fetch, or dated after it\n")
    # What the three texts say about it.
    rule, page = flat(CONTRACTS), flat(LISTING)
    assert '"%s"' % FILE_NOTE in rule and "So `E_STALE` cannot stop a price file" in rule
    for gone in ("It does not run on past dates", "data more than 12 days old",
                 "on current data only"):
        assert gone not in page, gone
    for phrase in ("a download more than 12 days old",
                   "A download whose newest price is more than 12 days old stops.",
                   "A price file is taken as it is: the script cannot know how old a file is.",
                   'The header then says "prices from a file", and the agent is told to say so '
                   "above the report when the newest date is more than a few days old."):
        assert phrase in page, phrase
    assert ("If its newest date is more than a few days old, say so above the report"
            in section(text_of(SKILL_MD), "Step 1"))


# (the [why] slot, the code of the FIX line or None when the draft goes through)
WORDING = [
    ("The close is {{sma50_gap|dir}} its medium-term average.", "D_NOTE"),
    (PLAIN_WHY, None),
    ("The desk reads {{last|label}} as weak.", None),
    ("The last price is {{sma50_gap|dir}} its medium-term average.", "D_LABEL"),
    ("The price is below the previous close ({{chg1|label}}).", None),
    ("The price is below the previous close ({{chg1}}).", "D_LABEL"),
    ("Against the last price, {{sma50|label}} is far away.", None),
    ("The shares closed down {{chg1|dir}} on the day.", "D_NOTE"),
    ("The shares closed higher, {{chg1|dir}} on the day.", "D_NOTE"),
    # The other order, reference then "close": for the newest price and its one-day change.
    ("It was {{chg1|dir}} at the close.", "D_NOTE"),
    ("The price stood at {{last}} at the close.", "D_NOTE"),
    ("The change was {{ret1|dir}} at the close.", None),
    # An indicator's name typed before another indicator's figure, whatever the format.
    ("RSI {{mfi14|label}} is low.", "D_LABEL"),
    ("Unlike the RSI, {{mfi14|label}} is not stretched.", "D_LABEL"),
    ("The RSI reading, {{rsi14|label}}, is low.", None),
    ("An MFI of {{rsi14}} is low.", "D_LABEL"),
    ("RSI stands at {{mfi14}} and MFI sits at {{rsi14}}.", None),
    ("The bear case rests on a strong sell-off continuing.", None),
    ("The bull case rests on a strong buy-in by funds.", None),
    ("The desk rates it a strong sell here.", "D_PHRASE"),
    ("The desk rates it a strong buy here.", "D_PHRASE"),
]
# (the [why] slot, the quantity words the receipt counts in it)
COUNTED = [
    ("The index sits in the low forties.", 1),
    ("The index was in the mid-twenties and is in the low forties.", 2),
    ("The price fell thirty percent.", 1),
    ("The price fell one percent, then a fifth, then a tenth.", 0),
]


def test_the_wording_rules_of_the_texts_are_the_rules_of_the_renderer(desk_folder):
    folder = Path(desk_folder)
    (folder / "bundle.json").write_text(json.dumps(live_bundle()), encoding="ascii")
    brief = run(skill_commands()["brief"], desk_folder)
    assert "session unfinished" in brief.stdout.splitlines()[0]
    for why, code in WORDING:
        done = render_with(desk_folder, {"why": why})
        fixes = [x for x in done.stdout.splitlines() if x.startswith("FIX ")]
        if code is None:
            assert (done.returncode, fixes) == (0, []), why
        else:
            assert done.returncode == 1 and len(fixes) == 1, (why, fixes)
            assert fixes[0].startswith("FIX why: %s " % code), (why, fixes)
    # The hint of D_NOTE is wording that goes through, and the contract holds the line.
    first = render_with(desk_folder, {"why": WORDING[0][0]})
    assert first.stdout == NOTE_LINE + "\n"
    assert WORDING[1] == (WORDING[0][0].replace("The close", "The price"), None)
    for why, count in COUNTED:
        done = render_with(desk_folder, {"why": why})
        assert done.returncode == 0, why
        assert done.stdout.count("Quantity words: %d, not checked." % count) == 1, why
    # In a finished session "close" is the right word, in either order.
    (folder / "bundle.json").write_text(json.dumps(synthetic_bundle()), encoding="ascii")
    brief = run(skill_commands()["brief"], desk_folder)
    assert "session finished" in brief.stdout.splitlines()[0]
    said = [why for why, code in WORDING if code == "D_NOTE"]
    assert len(said) == 5
    for why in said:
        assert render_with(desk_folder, {"why": why}).returncode == 0, why
    rule, roles, page = flat(CONTRACTS), flat(ROLES_MD), flat(LISTING)
    for phrase in ("`%s`" % NOTE_LINE,
                   'The indicator names are "RSI", "MFI", "ADX", "CCI", "MACD", "stochastic", '
                   '"P/E" and "ATR", in any letter case.',
                   'After a whole label from the sheet, such as "previous close", "last price" '
                   'or "last close", a reference with the `label` format is accepted: it prints '
                   "its own label.",
                   'After an indicator name it is refused like any other ("RSI '
                   '{{mfi14|label}}")',
                   'so "Unlike the RSI, {{mfi14|label}}" is refused too',
                   'With a hyphen right after, as in "strong sell-off" and "strong buy-in", they '
                   "are not",
                   'Not raised for "previous close" before a reference',
                   '("closed down {{chg1}}")',
                   'or a reference to `last` or `chg1` followed by "at the close" or "at the '
                   'closing" ("It was {{chg1|dir}} at the close.")',
                   "In that order no other fact is caught.",
                   'plural tens among them ("forties", "twenties")',
                   'Not counted: "one", and fractions in words other than halves, thirds and '
                   'quarters ("a fifth", "a tenth")'):
        assert phrase in rule, phrase
    for phrase in ('When the session is unfinished or unknown, call the newest price "the '
                   'price" or cite `{{last|label}}`, never the close.',
                   'Fractions in words ("a fifth") are not counted: never use one for a figure.',
                   '("MFI {{rsi14}}" is refused)'):
        assert phrase in roles, phrase
    for phrase in ("a reference that stands straight after another indicator's name (as in "
                   '"MFI {{rsi14}}")',
                   "A figure that stands further from the wrong name is not caught.",
                   'number words such as "thirty" and "forties". They are counted on the '
                   'receipt and not checked. "One" and fractions in words ("a fifth") are not '
                   "counted at all.",
                   "The agent never types a market number in digits."):
        assert phrase in page, phrase
    for gone in ("a figure placed under another indicator's label",
                 "The agent never writes a market number"):
        assert gone not in page, gone


def test_roles_md_says_what_the_fix_round_added():
    roles = flat(ROLES_MD)
    for phrase in (
            # no charge unless the host is known to save the tool's result
            "If it does not, or you do not know, start no charge: ask only whether the user "
            "has the saved result as a file, beside the other choices `SKILL.md` names for "
            "the stop.",
            # which marketplace, and an agent without the tool
            "The tool's id is `yahoo-finance:get_historical_data`, on the Agensi marketplace. "
            "If you have no such tool, say so.",
            "It costs about $0.07, and the price is shown to the user for approval first.",
            # the host, not a script, ends the reply
            "If your host, not a script, stops your reply, say that no report was made and "
            "that the user can ask again in a new chat.",
            # the role briefs stand on their own
            "Each role sees only the inputs listed for it. \"The fact sheet\" means"):
        assert phrase in roles, phrase
    stopped = " ".join(section(text_of(ROLES_MD), "When a run stops").split())
    assert stopped.index("no figure from the data") < stopped.index("If your host, not a script")
    # roles.md stays under the cap for a reference file.
    assert len(shipped_bytes(ROLES_MD)) <= 20000


# The second fix round of 2026-11-11: the live row, the session rule, statements for a
# company only, the wording rules and the page. Each text against what the scripts do.

NOT_ADVICE = ("Research simulation, not advice. This desk design lost to buy-and-hold in its "
              "only test.")
OHLC = "STOP E_OHLC the prices for %s are impossible (zero, negative or outside the day's range)\n"
RANGE_FACTS = ("high52", "high52_date", "high52_gap", "low52", "low52_date", "low52_gap")


def sheet_of(folder: str, bundle: dict) -> subprocess.CompletedProcess:
    """Step 2 as printed, on one bundle."""
    (Path(folder) / "bundle.json").write_text(json.dumps(bundle), encoding="ascii")
    return run(skill_commands()["brief"], folder)


def brief_of(folder: str) -> dict:
    return json.loads((Path(folder) / "brief.json").read_text(encoding="ascii"))


def row_date(chart: dict, back: int) -> str:
    """The exchange date of the row ``back`` places from the end, for the New York offset."""
    day = (chart["timestamp"][-back] + chart["meta"]["gmtoffset"]) // DAY
    return (datetime.date(1970, 1, 1) + datetime.timedelta(days=day)).isoformat()


def past_its_range(bundle: dict, back: int = 1) -> dict:
    """One row with its close 2% over the day's published high and its open 2% under the
    published low, as the source sends a live row: its last price is newer than its range."""
    chart = bundle["chart"]
    chart["close"][-back] = round(chart["high"][-back] * 1.02, 2)
    chart["open"][-back] = round(chart["low"][-back] * 0.98, 2)
    if back == 1:
        chart["meta"]["regularMarketPrice"] = chart["close"][-1]
    return bundle


def test_the_live_row_is_widened_and_any_other_row_outside_its_range_still_stops(desk_folder):
    folder = Path(desk_folder)
    for name, live in (("share", past_its_range(live_bundle())),
                       ("coin", past_its_range(coin_bundle()))):
        chart = live["chart"]
        out = sheet_of(desk_folder, live)
        assert out.returncode == 0, (name, out.stderr)
        assert out.stdout.splitlines()[0].endswith(" | session unfinished"), name
        assert sheet_flags(out.stdout).split(", ")[0] == "unfinished_session", name
        assert sheet_notes(out.stdout)[0] == (
            "The session of 2026-11-10 was unfinished at the fetch: its price, range and "
            "volume are not final."), name
        facts = brief_of(desk_folder)["facts"]
        # The sheet shows the widened range. Every other row is as the source sent it.
        assert facts["high.0"]["v"] == facts["close.0"]["v"] == chart["close"][-1], name
        assert facts["low.0"]["v"] == facts["open.0"]["v"] == chart["open"][-1], name
        assert chart["low"][-1] > chart["open"][-1] and chart["high"][-1] < chart["close"][-1]
        assert (facts["high.1"]["v"], facts["low.1"]["v"]) == (chart["high"][-2], chart["low"][-2])
        # "Every average, gap, return and indicator includes it."
        assert facts["last"]["v"] == chart["close"][-1] and facts["last"]["label"] == "last price"
        assert facts["sma10"]["v"] == pytest.approx(sum(chart["close"][-10:]) / 10, rel=1e-12)
        assert facts["ret1"]["v"] == pytest.approx(
            100 * (chart["close"][-1] / chart["close"][-2] - 1), rel=1e-12)

    # What still stops, each with the date of its row: the same row once its session is
    # finished, an older row, and a price that is zero or negative in the live row.
    older = past_its_range(live_bundle(), back=5)
    zero, negative = live_bundle(), live_bundle()
    zero["chart"]["low"][-1], negative["chart"]["close"][-1] = 0, -1.0
    stops = (("finished", past_its_range(synthetic_bundle()), "2026-11-10"),
             ("coin, a day on", past_its_range(coin_bundle(hours=25)), "2026-11-10"),
             ("older", older, row_date(older["chart"], 5)),
             ("zero", zero, "2026-11-10"), ("negative", negative, "2026-11-10"))
    for name, bundle, date in stops:
        out = sheet_of(desk_folder, bundle)
        assert (out.returncode, out.stdout, out.stderr) == (2, "", OHLC % date), name
    assert stops[2][2] == "2026-11-04"
    # A price file has no session facts: its newest row is checked as it is.
    (folder / "prices.csv").write_text(
        price_file(past_its_range(live_bundle())["chart"], "%Y-%m-%d"), encoding="ascii")
    out = run(skill_commands()["brief"][:2] + ["DIR/prices.csv", "DIR/brief.json",
                                               "--symbol", "TEST"], desk_folder)
    assert (out.returncode, out.stderr) == (2, OHLC % "2026-11-10")

    # The texts: the contract's rule, the two instruction files and the listing.
    rule, page = flat(CONTRACTS), flat(LISTING)
    for phrase in (
            "| live row | The newest row is complete and its session is unfinished by the "
            "session rule below.",
            "The row's high becomes the highest and its low the lowest of its open, high, low "
            "and close, before the `E_OHLC` rule.",
            "The facts `high.0` and `low.0` show the widened values.",
            "The live row is checked as widened, so only a price <= 0 or a volume below 0 "
            "stops it. A row of a finished session and every older row are checked as the "
            "source sent them",
            "A price file has no session facts, so its rows are never changed"):
        assert phrase in rule, phrase
    step2 = section(text_of(SKILL_MD), "Step 2")
    assert "The newest row is not final, and every average, gap, return and indicator " \
        "includes it." in step2
    assert ("Every average, gap, return and indicator includes that row, so never credit one "
            "to finished sessions.") in " ".join(
                section(text_of(ROLES_MD), "Rules for every role").split())
    for phrase in ("In an unfinished trading day, which for a crypto pair is every current "
                   "day, the newest row is not final. The script widens that row's range to "
                   "hold its last price, so a last price above the day's published high "
                   "does not stop a run.",
                   "Every average, gap, return and indicator on the sheet includes that row."):
        assert phrase in page, phrase


def test_a_quote_from_before_the_bell_keeps_the_session_unfinished_for_half_an_hour(
        desk_folder):
    def sheet(fetch: int, quote: int | None, change=None) -> str:
        bundle = at_the_clock(synthetic_bundle(), fetch, quote)
        if change:
            change(bundle)
        out = sheet_of(desk_folder, bundle)
        assert out.returncode == 0, out.stderr
        return out.stdout

    def session(text: str) -> str:
        return text.splitlines()[0].rsplit(" ", 1)[1]

    # (the fetch, the source's quote), in seconds after the regular end
    clock = {(-600, -660): "unfinished",   # in the session
             (240, -660): "unfinished",    # four minutes after the bell, a quote 15 minutes old
             (240, 0): "finished",         # the quote is the closing one
             (1799, -1): "unfinished",     # the half hour, to the second
             (1800, -1): "finished",
             (-1, None): "unfinished",     # no quote time: the fetch time stands in
             (0, None): "finished",
             (240, None): "finished"}
    for (fetch, quote), word in clock.items():
        text = sheet(fetch, quote)
        assert session(text) == word, (fetch, quote)
        assert ("unfinished_session" in sheet_flags(text)) == (word == "unfinished")

    # A row that starts before the regular session start belongs to an earlier session.
    def next_session(bundle: dict) -> None:
        period = bundle["chart"]["meta"]["currentTradingPeriod"]["regular"]
        period["start"], period["end"] = period["start"] + DAY, period["end"] + DAY

    assert session(sheet(16 * 3600, 0, next_session)) == "finished"

    # The close is filled only when the session is finished. Until then the view ends a day
    # earlier, and nothing is flagged.
    def no_close(bundle: dict) -> None:
        bundle["chart"]["close"][-1] = None

    early = sheet(240, -660, no_close)
    assert sheet_flags(early) == "none" and session(early) == "finished"
    assert sheet_notes(early)[0] == (
        "The source has no complete prices for 2026-11-10 yet, so this view ends at "
        "2026-11-09. Its prices were not final at the fetch.")
    assert early.splitlines()[-1].startswith("BRIEF ok TEST 2026-11-09 ")
    for fetch, quote in ((240, 0), (1800, -660)):
        filled = sheet(fetch, quote, no_close)
        assert sheet_flags(filled) == "close_filled" and session(filled) == "finished"
        assert filled.splitlines()[-1].startswith("BRIEF ok TEST 2026-11-10 ")

    # Crypto is as it was: the row of the fetch's UTC day is unfinished.
    for hours, word in ((1, "unfinished"), (23, "unfinished"), (25, "finished")):
        out = sheet_of(desk_folder, coin_bundle(hours=hours))
        assert out.returncode == 0 and session(out.stdout) == word, hours

    rule, page = flat(CONTRACTS), flat(LISTING)
    for phrase in (
            "| session | Newest bar time >= `currentTradingPeriod.regular.start`, the "
            "source's quote time (`meta.regularMarketTime`; the fetch time when the bundle "
            "has none) < its `end`, and fetch time < `end` + 1800 seconds | `session = "
            "unfinished`. Missing facts: `unknown`. Else `finished`.",
            "The source's quote can be 15 minutes old",
            "until rev 5 only the fetch time counted, and such a row was called finished",
            "CRYPTOCURRENCY is `unfinished` unless the bar is before the current UTC day",
            "but no close, the session is finished by the session rule below",
            # the limit that the half hour brings
            '"unfinished" also covers the half hour after the bell while the source\'s quote '
            "is older than the bell."):
        assert phrase in rule, phrase
    assert ("The source's quotes can be delayed. For half an hour after the closing bell the "
            'report says "unfinished" while the source\'s quote is older than the bell.') in page


@pytest.mark.parametrize("kind, sent", [
    ("EQUITY", True), (None, True), ("ETF", False), ("MUTUALFUND", False), ("INDEX", False),
    ("CRYPTOCURRENCY", False), ("FUTURE", False)])
def test_only_a_company_is_asked_for_statements(tmp_path, kind, sent):
    bundle = synthetic_bundle()
    bundle["chart"]["meta"]["instrumentType"] = kind
    if kind is None:
        del bundle["chart"]["meta"]["instrumentType"]
    # The source would turn the statement request away: only a company's run gets to know.
    got = Download(tmp_path / "bundle.json", bundle=bundle, statements=(429,))
    assert (got.code, got.stderr) == (0, "")
    saved = json.loads((tmp_path / "bundle.json").read_text(encoding="ascii"))
    assert len(saved["statements"]) == 10 and not any(saved["statements"].values())
    if sent:
        assert got.asked == ["chart", "news", "statements", "statements"]
        assert got.waits == [got.wait]
        assert saved["errors"] == {"news": None, "statements": "HTTP 429"}
        assert got.stdout == "DATA ok TEST bars=260 headlines=0 statements=failed\n"
    else:
        assert got.asked == ["chart", "news"] and got.waits == []
        assert saved["errors"] == {"news": None, "statements": None}
        assert got.stdout == "DATA ok TEST bars=260 headlines=0 statements=absent\n"


def test_a_fund_and_a_coin_run_without_statements_and_the_texts_say_so(desk_folder):
    cmds, folder = skill_commands(), Path(desk_folder)
    fund = synthetic_bundle()
    fund["chart"]["meta"]["instrumentType"] = "ETF"
    for bundle, kind, session in ((fund, "ETF", "finished"),
                                  (coin_bundle(), "CRYPTOCURRENCY", "unfinished")):
        got = Download(folder / "bundle.json", bundle=bundle)
        assert got.asked == ["chart", "news"], kind
        assert got.stdout == "DATA ok TEST bars=%d headlines=0 statements=absent\n" % len(
            bundle["chart"]["timestamp"])
        out = run(cmds["brief"], desk_folder)
        assert out.returncode == 0, out.stderr
        assert out.stdout.splitlines()[0] == (
            "# TEST | Test Instruments Inc. | %s | USD | session %s" % (kind, session))
        # The sheet reads as it does for a company the source had no figure for.
        (note,) = [x for x in sheet_notes(out.stdout) if "statement" in x]
        assert note.startswith(STATEMENTS_NONE) and "pe_trailing" in note
        assert "statements_absent" in sheet_flags(out.stdout)
    rule, page = flat(CONTRACTS), flat(LISTING)
    for phrase in (
            "Up to three GET requests",
            "Request 3 is sent only when the chart's `meta.instrumentType` is missing or is "
            "`EQUITY`: only a company has statements.",
            "For any other type (a fund, an index, a coin) there is no third request: "
            "`statements` holds its ten empty lists, `errors.statements` is `null` and the "
            "result line says `statements=absent`.",
            "`statements=absent` also stands where request 3 was not sent"):
        assert phrase in rule, phrase
    for phrase in ("and, for a company, a few statement figures",
                   "A fund, an index or a crypto pair is not asked for statement figures: its "
                   "report says that they are absent, and the desk runs without the "
                   "fundamentals analyst."):
        assert phrase in page, phrase
    # The agent's side of it, unchanged: the flag row and the role's own rule.
    step2 = dict(re.findall(r"(?m)^\| `([a-z_]+)` \| (.+) \|$", section(text_of(SKILL_MD),
                                                                       "Step 2")))
    assert step2["statements_absent"] == "Skip the fundamentals analyst."
    assert ("Skip this role when the statement facts are absent or the type is not EQUITY"
            in flat(ROLES_MD))


def test_the_52_week_facts_need_most_of_a_year_of_rows(desk_folder):
    cmds, folder = skill_commands(), Path(desk_folder)
    newest = datetime.date(2026, 11, 10)
    every = [newest - datetime.timedelta(days=i) for i in range(400)]
    weekdays = [d for d in every if d.weekday() < 5]
    args = cmds["brief"][:2] + ["DIR/prices.csv", "DIR/brief.json", "--symbol", "TEST"]
    # (a calendar, the rows of it): are the 52-week facts there, is the history flagged short
    for days, rows, there, short in ((weekdays, 199, False, True), (weekdays, 200, True, False),
                                     (every, 288, False, False), (every, 289, True, False)):
        (folder / "prices.csv").write_text(calendar_file(days[:rows][::-1], 10), encoding="ascii")
        out = run(args, desk_folder)
        assert out.returncode == 0, out.stderr
        facts = brief_of(desk_folder)["facts"]
        assert [k in facts for k in RANGE_FACTS] == [there] * 6, rows
        assert ("short_history" in sheet_flags(out.stdout)) == short, rows
        assert ("sma200" in facts) == (rows >= 200), rows
        if not there:
            # No flag of its own in a 365-bar year: the note on absent facts names them.
            (note,) = [x for x in sheet_notes(out.stdout) if x.startswith("Too little history")]
            assert note.startswith("Too little history (%d daily rows) " % rows)
            assert set(RANGE_FACTS) <= set(note.rstrip(".").split(": ")[1].split(", "))
    rule = flat(CONTRACTS)
    for phrase in ("The 52-week facts need more than 0.79 of a year of rows: 200 rows for a "
                   "252-bar year, 289 for a 365-bar year.",
                   "| short | Fewer than 200 rows | Flag `short_history`; the 200-day facts "
                   "and the 52-week facts are absent."):
        assert phrase in rule, phrase
    assert "| `short_history` | The 200-day and 52-week facts are absent. |" in text_of(SKILL_MD)


def test_the_known_answer_rows_stand_for_a_weekday_market():
    script = load_script("brief.py")
    seen, real = [], script.build

    def build(doc, meta, rows, sym, cur):
        seen.append((doc, list(rows)))
        return real(doc, meta, seen[-1][1], sym, cur)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(script, "build", build)
        assert script.kat() == script.KAT
    ((doc, rows),) = seen
    stamps = [row[0] for row in rows]
    # 260 rows, each 1.4 days after the one before.
    assert len(rows) == 260 and {b - a for a, b in zip(stamps, stamps[1:])} == {7 * DAY // 5}
    assert stamps[-1] // DAY - stamps[-60] // DAY == 82
    # The same prices with a bar on every day take the other path: 365 bars in a year.
    daily = [(stamps[0] + i * DAY,) + tuple(row[1:]) for i, row in enumerate(rows)]
    weekday_year = real(doc, {}, rows, "KAT", "USD")[0]["facts"]["vol21"]["v"]
    every_day_year = real({"fetched_at": daily[-1][0]}, {}, daily, "KAT", "USD")[0]["facts"][
        "vol21"]["v"]
    assert every_day_year / weekday_year == pytest.approx((365 / 252) ** 0.5, rel=1e-12)
    assert ("builds a brief from 260 generated rows that stand 1.4 days apart, so the newest 60 "
            "span 82 days and the test pins the 252-bar path") in flat(CONTRACTS)


def draft_with_levels(rows: list[str]) -> str:
    """The example draft of SKILL.md with other rows in its [levels] slot."""
    head, rest = example_draft().split("[levels]\n", 1)
    return "%s[levels]\n%s\n[trader]%s\n" % (head, "\n".join(rows), rest.split("\n[trader]", 1)[1])


def page_of(folder: str, draft: str) -> tuple[str, dict]:
    """Steps 5 and 6 as printed: the page, and the resolved report it was drawn from."""
    cmds = skill_commands()
    (Path(folder) / "draft.txt").write_text(draft, encoding="ascii")
    done = run(cmds["render"], folder)
    assert done.returncode == 0, done.stdout
    drawn = run(cmds["page"], folder)
    assert drawn.returncode == 0 and drawn.stdout.startswith("PAGE ok TEST "), drawn.stderr
    report = json.loads((Path(folder) / "report.json").read_text(encoding="ascii"))
    return (Path(folder) / "report.html").read_text(encoding="ascii"), report


def level_labels(page: str) -> list[tuple[float, int, str]]:
    """Each level label of the chart, from the top: the height of its anchor in percent of
    the plot, its shift from there in pixels, and the price it shows."""
    return [(float(y), int(dy), price) for y, dy, price in re.findall(
        r"<text class=lt x=0 y=([\d.]+)% dy=(-?\d+)>([^<]*)<tspan", page)]


def level_lines(page: str) -> list[float]:
    return [float(y) for y in re.findall(r"<line class=lv x2=100% y1=([\d.]+)% y2=\1% />", page)]


def test_the_report_and_the_page_carry_the_not_advice_line_and_the_column_to_reach_it(
        desk_folder):
    assert sheet_of(desk_folder, synthetic_bundle()).returncode == 0
    page, report = page_of(desk_folder, example_draft() + "\n")
    printed = (Path(desk_folder) / "report.md").read_text(encoding="ascii")
    last = brief_of(desk_folder)["facts"]["last"]["v"]

    # The levels table: one heading in both, over the move that would reach the level.
    assert "| Level | Price | To reach it | Would support |" in printed.splitlines()
    assert "<table><tr><th>Level</th><th>Price</th><th>To reach it</th></tr>" in page
    assert "From last" not in printed + page
    assert len(report["levels"]) == 2
    for level in report["levels"]:
        gap = "%+.1f%%" % (round(100 * (level["price"] / last - 1), 1) + 0.0)
        assert level["gap_t"] == gap
        assert " | %s | %s | " % (level["price_t"], gap) in printed
        assert "<td>%s</td><td>%s</td></tr>" % (level["price_t"], gap) in page

    # The line under the header: the same sentence in the report and on the page.
    assert printed.split("\n\n")[2] == NOT_ADVICE
    header = page.split("<header>", 1)[1].split("</header>", 1)[0]
    assert re.search(r"</p><small>%s</small><div class=t>" % re.escape(NOT_ADVICE), header)
    assert page.count(NOT_ADVICE) == 1 and NOT_ADVICE.split(". ")[1] in text_of(SKILL_MD)

    # A date never breaks across two lines, and the returns stay on one.
    body = page.split("</style>", 1)[1]
    assert "2026&#8209;11&#8209;10" in body and not re.search(r"\d-\d", body)
    assert "2026%s11%s10" % (chr(8209), chr(8209)) in html.unescape(body)
    assert ".in dd{font-size:15px;white-space:nowrap}" in page
    series = brief_of(desk_folder)["series"]["date"]
    assert ("<small>120 daily closes in USD, %s to %s. 50d and 200d: the 50-day and 200-day "
            "averages. Dashed lines: the report's levels.</small>" % tuple(
                x.replace("-", "&#8209;") for x in (series[0], series[-1]))) in page

    rule, listing = flat(CONTRACTS), flat(LISTING)
    for phrase in (
            "The levels table has the columns Level, Price, To reach it and Would support.",
            'until rev 5 the column was headed "From last close" or "From last price"',
            'Under its header the report prints the line "%s", and the page carries the same '
            "line in its header band." % NOT_ADVICE,
            "In each escaped string a hyphen between two digits is written as `&#8209;`, the "
            "non-breaking hyphen, so that a date never breaks across two lines.",
            "Text copied from the page holds that character (U+2011) in its dates, not the "
            "ASCII hyphen.",
            'in small type under its first paragraph, and four tiles',
            'a table whose third column is headed "To reach it"',
            "The returns are named in days, as the report names them, and do not wrap.",
            'The chart caption reads "<n> daily closes in <unit>, <first date> to <last '
            "date>. 50d and 200d: the 50-day and 200-day averages. Dashed lines: the report's "
            'levels."'):
        assert phrase in rule, phrase
    for phrase in ("each with the move from the newest price that would reach it",
                   'The line "%s" stands at the top of the page, under the first line of the '
                   "header." % NOT_ADVICE):
        assert phrase in listing, phrase


def test_each_level_label_is_drawn_at_its_own_line(desk_folder):
    assert sheet_of(desk_folder, synthetic_bundle()).returncode == 0
    words = "on a close beyond it"
    wanted = {("low52 | bear",): [4],
              ("close.1 | bull", "low52 | bear"): [-8, 16],
              ("low52 | bear", "close.1 | bull", "sma200 | bear"): [-8, 4, 16]}
    for rows, shifts in wanted.items():
        page, report = page_of(desk_folder, draft_with_levels(
            ["%s | %s" % (row, words) for row in rows]))
        labels, lines = level_labels(page), level_lines(page)
        # From the top of the plot down, each label is anchored at the height of its line.
        prices = sorted(report["levels"], key=lambda level: -level["price"])
        assert [price for _, _, price in labels] == [level["price_t"] for level in prices]
        assert [y for y, _, _ in labels] == lines == sorted(lines) and len(set(lines)) == len(rows)
        assert [dy for _, dy, _ in labels] == shifts, rows
    assert ("it is drawn at its own line: with two or three levels the top label sits above "
            "its line, the bottom one below it and a middle one on it; a single level sits on "
            "its line.") in flat(CONTRACTS)


def trend_bundle() -> dict:
    """The generated share with prices that climb by a fixed amount a day, so that the range
    of the chart is known: a little over a hundred."""
    bundle = synthetic_bundle()
    chart = bundle["chart"]
    closes = [100 + 0.45 * i + i % 5 for i in range(len(chart["close"]))]
    opens = [100.0] + closes[:-1]
    chart.update(open=opens, close=closes,
                 high=[max(pair) + 1 for pair in zip(opens, closes)],
                 low=[min(pair) - 1 for pair in zip(opens, closes)])
    chart["meta"]["regularMarketPrice"] = closes[-1]
    return bundle


def test_the_price_axis_can_step_by_two_and_a_half(desk_folder):
    assert sheet_of(desk_folder, trend_bundle()).returncode == 0
    page, report = page_of(desk_folder, example_draft() + "\n")
    series = brief_of(desk_folder)["series"]
    drawn = [level["price"] for level in report["levels"]] + [
        v for key in ("close", "sma50", "sma200") for v in series[key] if v is not None]
    # Over 100 and at most 125: five steps of 20 are too few, and steps of 50 would leave
    # half of the plot empty.
    assert 100 < max(drawn) - min(drawn) <= 125
    ticks = [float(x) for x in re.findall(
        r"<text x=-7 y=[\d.]+% dy=4 text-anchor=end>([\d.]+)</text>", page)]
    assert ticks == [100, 125, 150, 175, 200, 225]
    assert ticks[0] <= min(drawn) and max(drawn) <= ticks[-1]
    assert "the axis in steps of 1, 2, 2.5 or 5 times a power of ten" in flat(CONTRACTS)


def test_roles_md_says_what_the_second_fix_round_added(desk_folder):
    roles = flat(ROLES_MD)
    rules = " ".join(section(text_of(ROLES_MD), "Rules for every role").split())
    mapping = " ".join(section(text_of(ROLES_MD), "From the desk to the draft").split())
    table = section(text_of(ROLES_MD), "Indicator conventions")
    (adx,) = [row for row in table.splitlines() if row.startswith("| `adx14`")]
    (analyst,) = [row for row in text_of(ROLES_MD).splitlines()
                  if row.startswith("| Fundamentals analyst |")]
    # the unfinished row is in every average, gap, return and indicator
    assert ("no role may treat that row's price or volume as final. Every average, gap, return "
            "and indicator includes that row, so never credit one to finished sessions."
            ) in rules
    # a level that is still open, and a level that moves
    assert ("A level must still be open: not already met by the newest finished close. An "
            "average or a band moves each day: prefer a dated price, or say that the level "
            "moves.") in mapping
    # ADX after a turn
    assert adx.endswith("It measures strength, not direction. After a turn it still carries "
                        "the old move, not the new one's strength. |")
    # the two figures that are estimates
    assert "`pe_forward` and `peg` rest on analyst estimates: say so." in analyst
    # a headline is a claim
    assert ('A headline is its publisher\'s claim, not a fact: write "a headline reports", '
            "above all about a named person.") in mapping
    # what was shortened to make the room
    for phrase in ("Cite facts by name, with the sheet's value beside it if that helps.",
                   "A single context cannot un-see earlier turns, so the report says how the "
                   "roles ran.",
                   "The numbers in this table are for reasoning. Never type one into the "
                   "draft."):
        assert phrase in roles, phrase
    assert len(shipped_bytes(ROLES_MD)) <= 20000 and len(shipped_bytes(SKILL_MD)) <= 12000

    # The wording these rules ask for goes through the renderer, in a level row too.
    assert sheet_of(desk_folder, synthetic_bundle()).returncode == 0
    draft = draft_with_levels([
        "sma50 | bull | on a close above this average, which moves each day",
        "close.1 | bear | on a close below that day's close"])
    draft = draft.replace("Headline {{h1}} does not explain", "A headline reports {{h1}}, "
                          "which does not explain")
    assert "A headline reports {{h1}}" in draft
    page, report = page_of(desk_folder, draft)
    assert [level["words"] for level in report["levels"]] == [
        "on a close above this average, which moves each day",
        "on a close below that day's close"]
    assert 'A headline reports "Test Instruments opens a second plant"' in report["why"]


# The limits the contract and the listing state, shown on generated data. When a script
# closes one of them, its test here fails and both texts lose the sentence.

def saved_history(stamps: list) -> dict:
    """The marketplace tool's saved result for the generated rows, with the given stamps."""
    chart = synthetic_bundle()["chart"]
    keys = ("open", "high", "low", "close", "volume")
    history = [dict({k: chart[k][i] for k in keys}, timestamp=t) for i, t in enumerate(stamps)]
    return {"tool_id": "yahoo-finance:get_historical_data", "status": "succeeded",
            "data": {"status": "success",
                     "data": {"symbol": "TEST", "currency": "USD", "history": history}}}


def test_limit_a_saved_history_with_one_late_row_or_text_stamps_passes_e_dates(desk_folder):
    cmds, folder = skill_commands(), Path(desk_folder)
    opens = synthetic_bundle()["chart"]["timestamp"]
    # An exchange east of UTC opens late in the UTC day before: 23:00 here.
    late = [t - t % DAY - 3600 for t in opens]
    as_text = [datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ") for t in late]
    wanted = {"all late": (late, None), "one late": (opens[:-1] + late[-1:], "2026-11-09"),
              "text": (as_text, "2026-11-09")}
    for name, (stamps, newest) in wanted.items():
        (folder / "bundle.json").write_text(json.dumps(saved_history(stamps)), encoding="ascii")
        out = run(cmds["brief"], desk_folder)
        if newest is None:
            assert out.returncode == 2 and out.stderr.startswith("STOP E_DATES "), name
        else:
            # The newest session is 2026-11-10; the sheet dates it a day early, unflagged.
            assert out.returncode == 0, (name, out.stderr)
            assert out.stdout.splitlines()[-1].startswith("BRIEF ok TEST %s " % newest), name
            assert "history_gap" not in sheet_flags(out.stdout)
    assert ("a history with a single late-stamped row passes. A stamp written as text is read "
            "by its first ten characters, as a date, so the rule never sees its time."
            ) in flat(CONTRACTS)
    assert ("A saved price history can carry a wrong date that the script does not catch: one "
            "with a single row stamped late in the UTC day, or with time stamps written as "
            "text.") in flat(LISTING)


def test_limit_one_row_with_a_missing_value_gives_one_note_each(desk_folder):
    bundle = synthetic_bundle()
    for back in (40, 50, 60, 70):
        bundle["chart"]["close"][-back] = None
    (Path(desk_folder) / "bundle.json").write_text(json.dumps(bundle), encoding="ascii")
    out = run(skill_commands()["brief"], desk_folder)
    assert out.returncode == 0 and sheet_flags(out.stdout) == "rows_dropped"
    left_out = [x for x in sheet_notes(out.stdout) if "was left out" in x]
    assert len(left_out) == 4 and len(set(left_out)) == 4
    limit = "One row with a missing value gives one note each"
    assert limit in flat(CONTRACTS) and limit in flat(LISTING)


def test_limit_an_open_or_a_close_outside_the_days_range_stops_the_run(desk_folder):
    # Seen for a thinly traded share: a quoted open and close beside a traded high and low.
    bundle = synthetic_bundle()
    chart = bundle["chart"]
    chart["open"][-100] = chart["close"][-100] = round(chart["low"][-100] * 0.95, 2)
    chart["close"][-30] = round(chart["high"][-30] * 1.05, 2)
    (Path(desk_folder) / "bundle.json").write_text(json.dumps(bundle), encoding="ascii")
    out = run(skill_commands()["brief"], desk_folder)
    oldest = (datetime.date(1970, 1, 1) + datetime.timedelta(
        days=(chart["timestamp"][-100] - 18000) // DAY)).isoformat()
    assert (out.returncode, out.stdout) == (2, "")
    assert out.stderr == OHLC % oldest
    # In an unfinished session too: only the newest row is widened, never a finished one.
    live = live_bundle()
    live["chart"].update({k: chart[k] for k in ("open", "close")})
    out = sheet_of(desk_folder, live)
    assert (out.returncode, out.stdout, out.stderr) == (2, "", OHLC % oldest)
    rule, page = flat(CONTRACTS), flat(LISTING)
    assert ("A thinly traded share stops with `E_OHLC` when the source's rows put the open or "
            "the close of a finished session outside that day's low-to-high range.") in rule
    assert ("The sentence names the oldest such date only, and the skill text then offers a "
            "price file.") in rule
    assert ("A thinly traded share can still stop the run. When the source's rows put the open "
            "or the close of a finished day outside that day's range, the script stops with a "
            "sentence that names the date. There is no report for that ticker from the "
            "download, and the agent offers to run on a price file that you supply."
            ) in page
    # What the agent is told to offer then: choice 2 of Step 1, the price file.
    step2 = section(text_of(SKILL_MD), "Step 2")
    assert ("Show the sentence and stop. After `E_DATES`, offer choices 1 and 2 of Step 1; "
            "after `E_OHLC`, choice 2.") in step2
    choices = section(text_of(SKILL_MD), "Step 1").split("### ")[1]
    (two,) = [x for x in choices.splitlines() if x.startswith("2. ")]
    assert two.startswith("2. The user supplies a price file: ")


def test_limit_page_py_trusts_the_fixed_texts_inside_the_report_file(desk_folder):
    cmds, folder = skill_commands(), Path(desk_folder)
    (folder / "bundle.json").write_text(json.dumps(synthetic_bundle()), encoding="ascii")
    assert run(cmds["brief"], desk_folder).returncode == 0
    (folder / "draft.txt").write_text(example_draft() + "\n", encoding="ascii")
    assert run(cmds["render"], desk_folder).returncode == 0
    report = json.loads((folder / "report.json").read_text(encoding="ascii"))
    record, disclaimer = "for buy-and-hold in its only test", "not investment advice"

    def page(changed: dict) -> subprocess.CompletedProcess:
        (folder / "report.json").write_text(json.dumps(changed), encoding="ascii")
        return run(cmds["page"], desk_folder)

    # An honest run carries both texts: render.py wrote them.
    assert page(report).stdout.startswith("PAGE ok TEST ")
    html = (folder / "report.html").read_text(encoding="ascii")
    assert record in html and disclaimer in html
    # Emptied by hand, the page is drawn without them and says ok.
    emptied = page(dict(report, test_record="", disclaimer=""))
    assert emptied.returncode == 0 and emptied.stdout.startswith("PAGE ok TEST ")
    html = (folder / "report.html").read_text(encoding="ascii")
    assert record not in html and disclaimer not in html
    # The keys must be there, and the report must belong to the brief.
    gone = page({k: v for k, v in report.items() if k != "test_record"})
    assert gone.returncode == 2 and gone.stderr.startswith("STOP E_INPUT ")
    other = page(dict(report, data_id="00000000"))
    assert other.returncode == 2 and other.stderr.startswith("STOP E_PAIR ")
    # The printed report cannot lose them that way: it is not made from this file.
    assert "lost to buy-and-hold" in (folder / "report.md").read_text(encoding="ascii")
    assert ("`page.py` trusts the fixed texts inside `report.json`. It checks that the report "
            "belongs to the brief and that the test record and the disclaimer are there as "
            "keys, not what they say") in flat(CONTRACTS)
    assert ("The page script trusts the fixed texts inside the report file that the report "
            "script saved: it does not check that the test record and the disclaimer in that "
            "file are unchanged.") in flat(LISTING)


def test_limit_a_stamp_of_zeros_after_the_real_stamp_line_makes_a_development_copy(tmp_path):
    # A standing decision: Python keeps the last value of SELF, and a zero stamp marks a
    # development copy, which warns and skips its own comparison.
    cmds, zeros = skill_commands(), 'SELF = "%s"\n' % ("0" * 12)
    warning = "WARN unstamped development copy\n"

    def copies(changed: str) -> None:
        for script in contract_scripts():
            shutil.copyfile(SCRIPTS / script, tmp_path / script)
        text, line = text_of(tmp_path / changed), 'SELF = "%s"\n' % stamp_of(changed)
        assert text.count(line) == 1
        (tmp_path / changed).write_bytes(text.replace(line, line + zeros, 1).encode("ascii"))

    copies("brief.py")
    pre = run(cmds["preflight"], str(tmp_path))
    assert pre.returncode == 0 and pre.stdout.startswith("PREFLIGHT ok "), pre.stderr
    assert pre.stderr == warning
    # Such a copy of a sibling runs by itself with the warning. The preflight refuses it:
    # its hash is not the one brief.py holds.
    copies("render.py")
    ran = run(cmds["render"][:2] + ["--version"], str(tmp_path))
    assert (ran.returncode, ran.stdout, ran.stderr) == (
        0, "trading-agents-desk render.py 2.0.0\n", warning)
    pre = run(cmds["preflight"], str(tmp_path))
    assert pre.returncode == 3 and pre.stderr.startswith("ERROR E_SIBLING render.py ")
    rule = flat(CONTRACTS)
    for phrase in ("A stamp line of zeros added after the real stamp line makes the copy an "
                   "unstamped development copy",
                   "warns on stderr (`WARN unstamped development copy`), skips its own "
                   "comparison and runs as a stamped copy does, with exit code 0 when nothing "
                   "else fails. That is by design",
                   "The preflight still refuses such a copy of a sibling"):
        assert phrase in rule, phrase


def test_limit_the_200_day_line_starts_where_a_year_of_rows_allows(desk_folder):
    # A one-year download of a share holds about 252 rows, and the average needs 200.
    assert sheet_of(desk_folder, synthetic_bundle(n=252)).returncode == 0
    series = brief_of(desk_folder)["series"]
    assert len(series["date"]) == 120
    assert [v is None for v in series["sma200"]] == [True] * 67 + [False] * 53
    assert None not in series["sma50"] + series["close"]
    page, _ = page_of(desk_folder, example_draft() + "\n")
    starts = re.findall(r'<path class=ln stroke=#\w+ d="M(\d+),', page)
    assert starts == ["67", "0", "0"]  # the 200-day line, the 50-day line, the close
    assert ("The 200-day line on the chart starts where a one-year download allows. The "
            "average needs 200 rows, so with about 250 rows it is drawn over the newest 50 or "
            "so of the chart's 120 days") in flat(CONTRACTS)
    assert ("On the page the 200-day line starts where a year of data allows: for a share "
            "that is over the newest 50 or so of the chart's 120 days.") in flat(LISTING)


def test_limit_a_middle_level_label_can_overlap_a_neighbour_that_is_a_few_pixels_away(
        desk_folder):
    # The previous close and the close of table row 1 are one price: two levels on one line.
    assert sheet_of(desk_folder, synthetic_bundle()).returncode == 0
    words = "on a close beyond it"
    page, _ = page_of(desk_folder, draft_with_levels(
        ["%s | %s" % (row, words) for row in ("prev_close | bull", "close.1 | bull",
                                              "low52 | bear")]))
    (top, middle, bottom), lines = level_labels(page), level_lines(page)
    assert top[0] == middle[0] == lines[0] == lines[1] < lines[2] == bottom[0]
    # Twelve pixels between the two baselines, and the text is eleven pixels high.
    assert (top[1], middle[1], bottom[1]) == (-8, 4, 16)
    assert "text{font-size:11px;" in page and middle[1] - top[1] > 11
    # Two levels alone keep 24 pixels between them, even at one price.
    page, _ = page_of(desk_folder, draft_with_levels(
        ["%s | %s" % (row, words) for row in ("prev_close | bull", "close.1 | bear")]))
    (top, bottom), lines = level_labels(page), level_lines(page)
    assert top[0] == bottom[0] and lines[0] == lines[1] and bottom[1] - top[1] == 24
    assert ("A level label in the chart can still overlap a neighbour when two levels are "
            "within a few pixels of each other: with three levels the middle label sits on "
            "its line, and the label of a neighbour at the same price stands 12 pixels "
            "from it, while the text is 11 pixels high and carries a white edge. Two levels "
            "alone keep 24 pixels between their labels.") in flat(CONTRACTS)
    assert ("level labels in the chart can overlap when three levels are given and two of "
            "them are almost at one price") in flat(LISTING)


def test_the_contract_is_at_rev_6_and_lists_the_known_limits():
    text = contract()
    top = text.split("Shipped files", 1)[0]
    assert top.index("Rev 6 (") < top.index("Rev 5 (") < top.index("Rev 4 (")
    rev6 = " ".join(top[top.index("Rev 6 ("):top.index("Rev 5 (")].split())
    for word in ("statements only for a company", "the live-row rule",
                 "the session rule with the source's quote time", "the bars in a year",
                 "the rows the 52-week facts need", "the known-answer rows", "`D_LABEL`",
                 "`D_NOTE`", 'the "To reach it" column', "the header line",
                 "the non-breaking hyphens", "the label positions", "the axis steps",
                 "the chart caption", "the limits in section 8"):
        assert word in rev6, word
    rev5 = " ".join(top[top.index("Rev 5 ("):top.index("Rev 4 (")].split())
    for word in ("the hash rule", "the `time` import", "the one new attempt after HTTP 429",
                 "`E_BUSY`", "the HTTP status at the end of a stop line", "the result line",
                 "the not-found hint", "the cleaning of quotes and marks", "the bars in a year",
                 "the currency rule", "the symbol rule", 'the two "request failed" notes',
                 "the price-file note", "the preflight paths", "`D_NOTE`", "`D_LABEL`",
                 "the phrase rule", "the quantity words", "Section 8 is new"):
        assert word in rev5, word
    headings = re.findall(r"(?m)^## (\d)\. (.+)$", text)
    assert [n for n, _ in headings] == list("12345678") and headings[-1][1] == "Known limits"
    limits = [" ".join(x.split()) for x in section(text, "8. ").split("\n- ")[1:]]
    # The standing decisions and the limits a test shows, each by its first words.
    starts = ("A headline can still end its own quotation", "`page.py` trusts the fixed texts",
              "`E_DATES` does not catch every saved price history",
              "One row with a missing value gives one note each",
              "A thinly traded share stops with `E_OHLC`", "A price file is taken as it is",
              "A copy whose stamp line is the bare word `SELF`",
              "A stamp line of zeros added after the real stamp line",
              "`Retry-After` is not read", '"one" and most fractions in words',
              "The 200-day line on the chart starts where a one-year download allows",
              "A level label in the chart can still overlap a neighbour",
              '"unfinished" also covers the half hour after the bell')
    assert [x[:len(start)] for x, start in zip(limits, starts)] == list(starts)
    assert len(limits) == len(starts) == 13
    # What a buyer can meet of these stands under Limits in the listing too, in plain words.
    page = " ".join(section(text_of(LISTING), "Limits", "###").split())
    for said in ("guillemets or with two apostrophes", "trusts the fixed texts",
                 "single row stamped late in the UTC day", "time stamps written as text",
                 "One row with a missing value gives one note each",
                 "A thinly traded share can still stop the run", "A price file is taken as it is",
                 "the 200-day line starts where a year of data allows",
                 "level labels in the chart can overlap",
                 'the report says "unfinished" while the source\'s quote is older than the '
                 "bell"):
        assert said in page, said


# The listing

def listing_table(start: str, end: str) -> dict[str, str]:
    """The rows of the listing's two-column table between two pieces of its text."""
    return dict(re.findall(r"(?m)^\| (.+?) \| (.+?) \|$",
                           text_of(LISTING).split(start, 1)[1].split(end, 1)[0]))


def test_the_listing_table_says_what_was_tried_and_claims_no_agent_host():
    rows = listing_table("What has been tried so far", "Rows change to")
    assert rows.pop("What") == "Status" and len(rows) == 6
    suite, ci, live, tool, agent, page = rows.items()
    assert "automated tests on generated data, the full suite" in suite[0]
    assert "on Linux with Python 3.10, 3.11, 3.12 and 3.13" in suite[0]
    # Two rounds of fixes came after the runs on record, and each row says which build it saw.
    assert suite[0].endswith(", for the build before the last round of fixes")
    assert suite[1] == "tested on 2026-11-11. The suite is run again on the final build"
    # The CI row names the legs that the workflow really has.
    job = text_of(CI_YML).split("\n  skill-scripts:\n", 1)[1]
    systems = re.search(r"(?m)^\s+os: \[(.+)\]$", job).group(1).split(", ")
    versions = re.findall(r'"([\d.]+)"', re.search(r"(?m)^\s+python-version: \[(.+)\]$",
                                                   job).group(1))
    assert systems == ["ubuntu-latest", "macos-latest", "windows-latest"]
    assert versions == ["3.9", "3.13", "3.14", "3.15"] and "- os: ubuntu-24.04" in job
    for phrase in ("in CI on GitHub", "the pull request's commit before the two rounds of fixes",
                   "the preflight and the standard-library flow",
                   "on Linux, macOS and Windows with Python 3.9, 3.13, 3.14 and 3.15",
                   "(%d legs; the Linux 3.9 leg runs on Ubuntu 24.04)"
                   % (len(systems) * len(versions))):
        assert phrase in ci[0], phrase
    assert ci[1] == "tested: all 12 legs passed. It is run again on the final commit"
    assert live[0] == ("Live downloads from one Linux cloud machine on 2026-11-11, 18 symbols, "
                       "with the build before the two rounds of fixes")
    for phrase in ("12 of the 18 were answered", "11 with data",
                   "The source's rate limit turned the other 6 away within 20 minutes",
                   "waits once, asks once more and names the limit plainly"):
        assert phrase in live[1], phrase
    assert "a real saved result for one US share" in tool[0]
    assert tool[1] == "tested once on 2026-11-11"
    # No agent host is named as tested: the release notes say what the agent runs showed.
    assert agent == ("An agent following the skill from start to finish",
                     "see the release notes")
    assert page[1].startswith("not tested yet")
    for what, status in rows.items():
        assert not re.search(r"Claude|Codex|Cursor", what + status), what
        assert what == agent[0] or status.startswith(("tested", "not tested yet")), what
    flat_page = flat(LISTING)
    assert "The table below says which versions and systems have been run." in flat_page
    assert "3.14 and newer have not" not in flat_page


def test_the_listing_gives_the_right_way_forward_after_a_refusal_or_a_rate_limit():
    page = flat(LISTING)
    answer = page.split("**What if the data source refuses?**", 1)[1].split("**Does it", 1)[0]
    for phrase in ("which HTTP status the source gave",
                   "After a refusal or a rate limit your choices are a price file that you "
                   "supply, or the marketplace's price-history tool.",
                   "After a rate limit the script has already waited once and asked once more, "
                   "and a new run a few minutes later may work.",
                   "Allowing the host for your command tool helps only when that tool has no "
                   "internet at all."):
        assert phrase in answer, phrase
    # That is what SKILL.md tells the agent to offer: choice 1 only where nothing was reached.
    rows = step_1_rows()
    choices = section(text_of(SKILL_MD), "Step 1").split("### ")[1]
    assert choices.index("1. The user allows the host") < choices.index("2. The user supplies")
    assert "3. The marketplace's price-history tool" in choices
    for code in ("E_REFUSED", "E_BUSY"):
        assert "choices 2 and 3" in rows[code] and "three" not in rows[code]
    assert "three choices" in rows["E_NO_ROUTE"]
    limits = " ".join(section(text_of(LISTING), "Limits", "###").split())
    assert ("The data source limits requests from shared cloud networks. The script waits once "
            "and asks once more. If the limit on the price request still holds, the run stops "
            "with a sentence that says so, and a price file or a later run are the ways "
            "forward.") in limits


def test_the_listing_declares_the_marketplace_tool_and_the_two_one_line_commands():
    rows = listing_table("## Permissions to declare", "The ZIP ships four scripts")
    assert rows.pop("Permission") == "Why"
    assert list(rows) == ["Terminal", "Network", "File write", "Environment variables",
                          "Marketplace tool"]
    # The tool, its charge and when it is used are those of roles.md.
    tool, roles = rows["Marketplace tool"], flat(ROLES_MD)
    for phrase in ("`yahoo-finance:get_historical_data`",
                   "Only when the command tool has no internet or the source refuses, and only "
                   "when the user chooses it.",
                   "The marketplace shows the price, about $0.07, for approval first."):
        assert phrase in tool, phrase
    assert "`yahoo-finance:get_historical_data`" in roles and "about $0.07" in roles
    page = flat(LISTING)
    assert ("This route was tried once, on 2026-11-11, with a real saved result for one US "
            "share.") in page
    assert "on a host that saves a tool's result as a file" in page
    # The clean-up command as SKILL.md prints it.
    assert ("a one-line command that removes the folder only if its name starts with `desk-` "
            "and it holds `brief.py`") in rows["Terminal"]
    assert "Where the host can, a command also copies the scripts" in rows["Terminal"]
    line = " ".join(skill_commands()["rmtree"])
    assert "p.name.startswith('desk-') and (p / 'brief.py').is_file() and shutil.rmtree" in line
    # One host is contacted. The second address in the script is text in its User-Agent.
    assert ("Its User-Agent string holds the repository's address, which is never contacted."
            in rows["Network"])
    source = text_of(SCRIPTS / "get_data.py")
    assert re.findall(r"https?://[^\s\"')]+", source) == ["https://" + HOST, REPO_URL]
    script = load_script("get_data.py")
    assert script.UA == "trading-agents-desk/2.0 (+%s)" % REPO_URL
    assert "The address in it is text: it is never contacted." in flat(CONTRACTS)


def test_the_listing_states_the_costs_and_limits_a_buyer_meets():
    limits = " ".join(section(text_of(LISTING), "Limits", "###").split())
    # The scripts an agent may have to write out, by their real size.
    said = re.search(r"the agent may have to write the four scripts out, about (\d+) KB of "
                     r"text, before the first command\. That costs time and tokens, and a copy "
                     r"with a wrong character is refused by the preflight\.", limits)
    total = sum(len(shipped_bytes(SCRIPTS / name)) for name in contract_scripts())
    assert said and int(said.group(1)) == round(total / 1000), total
    assert ("An AI host may decline to run the roles for some requests. The skill then tells "
            "the agent to say that no report was made.") in limits
    assert "say that no report was made" in flat(ROLES_MD)
    assert ("The agent is also told never to run the desk on a past date to see how it would "
            "have done. No script can hold it to that.") in limits
    assert "Never run the desk on a past date" in section(text_of(SKILL_MD), "Guardrails")
    # Version 2 is not on the main branch yet.
    page = flat(LISTING)
    assert ("Version 2 of the skill, these scripts and instructions, is there on a pull request "
            "branch until that request is merged, and on the main branch from then on."
            ) in page
    assert "The scripts and the instructions are in the public repository" not in page
    # The price stands, and nothing on the page is a measured claim.
    assert "| Price | $5, one-time |" in text_of(LISTING)
    assert set(re.findall(r"[-+]?\d+(?:\.\d+)?\s?%", page)) == {"-0.9%", "+15.7%"}


def test_the_note_on_the_data_providers_terms_is_marked_as_a_note_for_the_seller():
    text = text_of(LISTING)
    headings = re.findall(r"(?m)^## (.+)$", text)
    title = "Note for the seller, not listing text: the data provider's terms"
    assert title in headings and "Data source note" not in headings
    assert headings.index(title) > headings.index("Page copy")
    note = " ".join(section(text, title).split())
    for phrase in ("This section is for the person who lists the skill. None of it goes on the "
                   "listing page.",
                   "These endpoints are unofficial.",
                   "The skill redistributes no data",
                   "a paid listing is a commercial product at any price, and Yahoo's terms do "
                   "not clearly allow one to be built on this data",
                   "This is the weakest point of a paid listing.",
                   "Read the terms, or move to a licensed source, before taking payment.",
                   "A free listing carries less of this risk."):
        assert phrase in note, phrase
