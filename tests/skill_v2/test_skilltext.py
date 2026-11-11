"""Tests for the skill's instruction text: SKILL.md, references/roles.md, references/lab.md.

The text is checked against docs/skill-v2/CONTRACTS.md and against what the four shipped
scripts really print. Some tests run the scripts with the commands exactly as SKILL.md prints
them, on generated numbers. No test uses the network: get_data.py is never run.
"""
from __future__ import annotations

import ast
import datetime
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


def test_what_this_is_takes_five_lines_and_names_the_lost_test():
    lines = [x for x in section(text_of(SKILL_MD), "What this is").splitlines() if x.strip()]
    assert len(lines) == 5 and all(x.startswith("- ") for x in lines)
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
    # Two scripts always, two only when the run will use them (contract section 1).
    (step,) = [x for x in setup.splitlines() if x.startswith("3. ")]
    for phrase in ("unchanged", "`brief.py` and `render.py` always",
                   "`get_data.py` unless you know that your command tool has no internet",
                   "`page.py` when you will draw the page", "character for character"):
        assert phrase in step, phrase
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
    for phrase in ("starts with `STOP`", "unchanged", "and stop", "No retry",
                   "no changed request", "no disclosure block"):
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
        "E_SYMBOL", "E_NOT_FOUND", "E_REFUSED", "E_NO_ROUTE", "E_SERVER", "E_CERT", "E_SHAPE"}
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
                  "Never retype price rows by hand into a file",
                  "say so and offer choices 1 and 2", "only when the user explicitly asks",
                  "above the report",
                  '"The price rows were typed by the assistant and not checked against the '
                  'source."'):
        assert value in rows, value


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
    for flag in ("unfinished_session",):
        assert '"last price", never "close"' in rows[flag]
    assert "`session unknown`" in step


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
    # roles.md says which of the two to trust.
    page = " ".join(section(text_of(ROLES_MD), "The page", "###").split())
    for phrase in ("The printed report is the answer", "trust the printed report",
                   "Never paste the page's HTML into the chat as text"):
        assert phrase in page, phrase


def test_closing_sections_keep_their_content():
    text, roles = text_of(SKILL_MD), text_of(ROLES_MD)
    after = section(text, "After the report")
    for phrase in ('`references/roles.md`, under "After the report"', "read a decision",
                   "what the receipt covers", "several tickers"):
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
    for phrase in ("advice", "Never invent a figure", "real order", "past date",
                   "references/lab.md"):
        assert phrase in guard, phrase
    assert "references/roles.md" in section(text, "Step 3")


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
            "Headline dates are calendar dates", "one day after `as_of`",
            "after the newest bar", "No arithmetic", "described in words",
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
    assert resolved["why"] in page.replace("&quot;", '"')

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
    live = synthetic_bundle(n=150)
    live["chart"]["close"][-30] = None
    live["news"] = []
    live["statements"] = {k: [] for k in live["statements"]}
    live["fetched_at"] = live["chart"]["meta"]["currentTradingPeriod"]["regular"]["end"] - 600
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
    live = synthetic_bundle()
    live["fetched_at"] = live["chart"]["meta"]["currentTradingPeriod"]["regular"]["end"] - 600
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
        # The sentence an agent writes first: the newest price by its name on the sheet.
        trap = render_with(desk_folder, dict(good, why=good["why"].replace(
            "The price", "The last " + word)))
        if trap.returncode == 1:
            assert "D_LABEL" in trap.stdout
            refused.append(session)
    if refused:
        assert '"last price"' in rules and '"last close"' in rules, refused


# What the reviewers proved about the text

def test_the_clean_up_command_removes_only_a_folder_the_setup_step_made(tmp_path):
    # The command used to remove whatever folder was put in its place, with no message.
    cmds = skill_commands()
    assert "startswith('desk-')" in " ".join(cmds["rmtree"])
    assert "Pass only the folder from setup step 2" in section(text_of(SKILL_MD), "Step 6")
    mine = tmp_path / "my project"
    (mine / "src").mkdir(parents=True)
    (mine / "src" / "keep.txt").write_text("keep", encoding="ascii")
    looks_alike = tmp_path / "desk-top" / "work"
    looks_alike.mkdir(parents=True)
    for folder in (mine, looks_alike, tmp_path):
        done = run(cmds["rmtree"], str(folder))
        assert done.returncode == 0 and done.stdout == done.stderr == ""
        assert folder.is_dir(), folder
    assert (mine / "src" / "keep.txt").read_text(encoding="ascii") == "keep"
    # The folder of setup step 2 goes, with or without a slash at its end.
    env = dict(os.environ, TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path))
    for tail in ("", "/"):
        made = run(cmds["tempfile"], "", env).stdout.strip()
        assert Path(made).name.startswith("desk-") and os.path.isdir(made)
        (Path(made) / "brief.json").write_text("{}", encoding="ascii")
        assert run(cmds["rmtree"], made + tail).returncode == 0 and not os.path.exists(made)


def test_the_text_fits_a_user_who_brings_a_price_file():
    text = text_of(SKILL_MD)
    setup = section(text, "Setup for one run")
    # The sentence to the user is true for a run that downloads nothing.
    assert "Unless you give me a price file, the market data comes from " + HOST in setup
    step1 = section(text, "Step 1")
    before = step1.split("```sh")[0]
    for phrase in ("a price file or a saved price history from the user",
                   "no `get_data.py` in `DIR`", "go to the choices below"):
        assert phrase in before, phrase
    choices = step1.split("### ")[1]
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
            "numbers spelled out in words, are counted on the receipt and not checked",
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

