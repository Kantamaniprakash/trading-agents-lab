"""Tests for skill/trading-agents-desk/scripts/page.py (CONTRACTS.md sections 1 and 6).

The page is run the way the skill runs it: as a separate process on two JSON files. Both files
are built here, in the shapes brief.py and render.py write (brief.v1 and report.v1), from a
seeded generator. No other script is imported, no network is used and no number is real market
data. The HTML is read back with the standard library's parser and compared with the inputs.
One fixture runs render.py as a process too: the texts the page shares with the Markdown
report are read from that report, not written down a second time here.
"""
from __future__ import annotations

import ast
import copy
import datetime
import hashlib
import json
import math
import random
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PAGE = HERE.parents[1] / "skill" / "trading-agents-desk" / "scripts" / "page.py"
RENDER = PAGE.with_name("render.py")
SOURCE = PAGE.read_bytes()

ALLOWED_IMPORTS = {"sys", "json", "math", "re", "hashlib", "datetime", "html"}
WARN = "WARN unstamped development copy"
E_INPUT = "STOP E_INPUT the page needs the brief file and the report file made from it"
E_PAIR = ("STOP E_PAIR the report was not made from this brief; make the report again, then "
          "draw the page")
E_FILE = "ERROR E_FILE cannot read or write a file named on the command line"
E_USAGE = "ERROR E_USAGE usage: page.py BRIEF REPORT OUT_HTML"
E_SELF = "ERROR E_SELF this copy of page.py was altered; load the skill files again"

END = datetime.date(2026, 11, 10)
ROWS = 120
DOT = chr(183)  # the middle dot the page puts between parts
# The hyphen the page writes between two digits, so that a date never breaks across two
# lines: as it is read back, and as it stands in the file.
NB_HYPHEN, NB_REFERENCE = chr(8209), "&#8209;"
CAPTION = ("%d daily %ss in %s, %s to %s. 50d and 200d: the 50-day and 200-day averages. "
           "Dashed lines: the report's levels.")
# The eight flags brief.py can set.
FLAGS = ("close_filled", "prices_behind", "history_gap", "rows_dropped", "unfinished_session",
         "short_history", "statements_absent", "no_headlines")
# Meter range and the two convention thresholds, as the contract gives them.
SCALES = {"rsi14": (0, 100, 30, 70), "stoch_k": (0, 100, 20, 80), "mfi14": (0, 100, 20, 80),
          "bb_pctb": (-0.5, 1.5, 0, 1), "adx14": (0, 100, 20, 25)}
INDICATORS = {"rsi14": "RSI(14)", "stoch_k": "Stochastic %K(14)", "mfi14": "MFI(14)",
              "bb_pctb": "Bollinger %B(20,2)", "adx14": "ADX(14)", "cci20": "CCI(20)"}
# Statement facts with the labels brief.py writes, in the order the tiles prefer them.
STATEMENTS = (
    ("pe_trailing", "trailing P/E", "ratio"), ("pe_forward", "forward P/E", "ratio"),
    ("peg", "PEG ratio", "ratio"), ("mcap", "market value", "money"),
    ("margin_ttm", "net margin, trailing 12 months", "pct"),
    ("rev_q_yoy", "revenue, newest quarter against a year earlier", "pct"),
    ("ni_q_yoy", "net income, newest quarter against a year earlier", "pct"),
    ("debt_equity", "debt to equity", "pct"),
    ("rev_ttm", "revenue, trailing 12 months", "money"),
    ("ni_ttm", "net income, trailing 12 months", "money"),
    ("rev_q", "revenue, newest quarter", "money"), ("ni_q", "net income, newest quarter", "money"),
    ("debt", "total debt", "money"), ("equity", "shareholders' equity", "money"),
)
TEST_RECORD = ("This desk design returned -0.9% against +15.7% for buy-and-hold in its only "
               "test: a reduced, price-only version on one stock for one quarter.")
DISCLAIMER = ("Research output, not investment advice. This is a simulated analysis on public "
              "data; nothing here places orders.")
# One string with everything a careless page would trip over.
HOSTILE = ('<b>X</b> & "q" \'s\' <script>alert(1)</script> </style></title></svg></section> '
           '<img src=x onerror=alert(2)> <a href="http://evil.example/">x</a> <!-- '
           + "W" * 90 + " " + chr(233) + chr(26085) + chr(26412))


# --- generated inputs ------------------------------------------------------------------

def price_text(value):
    """Two decimals, or four significant figures below 10 (the brief's display rule)."""
    size = abs(value)
    if size >= 10 or size == 0:
        return "%.2f" % value
    return "%.*f" % (3 - math.floor(math.log10(size)), value)


def big_text(value):
    for unit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= unit:
            return "%.2f%s" % (value / unit, suffix)
    return "%.0f" % value


def fact(value, text, label, kind, **extra):
    out = {"v": value, "t": text, "label": label, "kind": kind}
    out.update(extra)
    return out


def price_fact(value, label):
    return fact(value, price_text(value), label, "price")


def pct_fact(value, label, signed=True, **extra):
    text = ("%+.2f%%" if signed else "%.2f%%") % value
    if signed:
        extra["sign"] = text[0]
    return fact(value, text, label, "pct", **extra)


def ident(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()[:8]


def make_brief(seed=1, start=150.0, session="finished", flags=(), notes=None, headlines=4,
               statements=True, history=200, symbol="TEST", unit="USD"):
    """A brief.v1 document with generated numbers, complete in the way brief.py writes it."""
    rng = random.Random(seed)
    closes, price = [], start
    for _ in range(history + ROWS):
        price *= math.exp(rng.gauss(0.0005, 0.02))
        closes.append(float("%.6g" % price))

    def average(i, n):
        return sum(closes[i - n + 1:i + 1]) / n if i >= n - 1 else None

    index = range(history, history + ROWS)
    dates = [(END - datetime.timedelta(days=ROWS - 1 - i)).isoformat() for i in range(ROWS)]
    series = {"date": dates, "close": [closes[i] for i in index],
              "sma50": [average(i, 50) for i in index],
              "sma200": [average(i, 200) for i in index],
              "volume": [rng.randrange(1_000_000, 9_000_000) for _ in index]}
    close = series["close"]
    word = "close" if session == "finished" else "last price"
    facts = {
        "last": price_fact(close[-1], "last " + word[-5:]),
        "as_of": fact(dates[-1], dates[-1], "data date", "date"),
        "prev_close": price_fact(close[-2], "previous close"),
        "chg1": pct_fact(100 * (close[-1] / close[-2] - 1), "one-day change"),
    }
    for n in range(1, 4):
        facts["close.%d" % n] = price_fact(close[-1 - n], "close on " + dates[-1 - n])
    for key, label in INDICATORS.items():
        low, high = (-0.4, 1.4) if key == "bb_pctb" else (-250, 250) if key == "cci20" else (2, 98)
        value = round(rng.uniform(low, high), 4)
        facts[key] = fact(value, "%.2f" % value, label, "level")
    for n in (10, 50, 200):
        value = average(history + ROWS - 1, n)
        if value is not None:
            label = "%d-day average" % n
            facts["sma%d" % n] = price_fact(value, label)
            facts["sma%d_gap" % n] = pct_fact(
                100 * (close[-1] / value - 1), "%s against the %s" % (word, label))
    for n in (1, 5, 21, 63):
        facts["ret%d" % n] = pct_fact(100 * (close[-1] / close[-1 - n] - 1), "%d-day return" % n)
    facts["vol21"] = pct_fact(rng.uniform(15, 70), "21-day annualised volatility", signed=False)
    if history >= 200:
        facts["high52"] = price_fact(max(closes), "52-week high close")
        facts["low52"] = price_fact(min(closes), "52-week low close")
    if statements:
        for i, (key, label, kind) in enumerate(STATEMENTS):
            as_of = "2026-11-10" if i < 4 else "2026-09-30"
            if kind == "money":
                value = rng.uniform(1e8, 9e11)
                facts[key] = fact(value, big_text(value), label, kind, as_of=as_of)
            elif kind == "pct":
                facts[key] = pct_fact(rng.uniform(-30, 60), label, signed="yoy" in key,
                                      as_of=as_of)
            else:
                value = rng.uniform(4, 60)
                facts[key] = fact(value, "%.2f" % value, label, kind, as_of=as_of)
    if notes is None:
        notes = ["A generated note about %s for 2026-11-07." % flag.replace("_", " ")
                 for flag in flags]
    brief = {
        "schema": "brief.v1", "version": "2.0.0", "symbol": symbol,
        "name": "Generated Company %d" % seed, "type": "EQUITY", "currency": unit[:3].upper(),
        "unit": unit, "source": "yahoo-chart", "as_of": dates[-1],
        "fetched_at": "2026-11-11 04:06 UTC", "session": session, "data_id": ident(series),
        "flags": list(flags),
        "notes": [{"id": "n%d" % (i + 1), "text": text} for i, text in enumerate(notes)],
        "facts": facts, "series": series,
        "headlines": [{"id": "h%d" % (i + 1), "date": dates[-1 - i // 2],
                       "publisher": "Generated Wire %d" % (i + 1),
                       "title": "Generated headline %d about a company that is made up" % (i + 1)}
                      for i in range(headlines)],
    }
    return brief


def level_of(brief, key, side="bull", words="on a close above"):
    """A level row the way render.py resolves it from a price fact."""
    item = brief["facts"][key]
    gap = 100 * (item["v"] / brief["facts"]["last"]["v"] - 1)
    return {"label": item["label"], "price": item["v"], "price_t": item["t"],
            "gap_t": "%+.1f%%" % gap, "side": side, "words": words}


def level_at(brief, price, label, side="bull", words="on a move above"):
    """A hand-made level row at any price, in the shape render.py writes."""
    gap = 100 * (price / brief["facts"]["last"]["v"] - 1)
    return {"label": label, "price": price, "price_t": price_text(price),
            "gap_t": "%+.1f%%" % gap, "side": side, "words": words}


def make_report(brief, action="HOLD", size=0.0, levels=("prev_close", "sma50")):
    """A report.v1 document for the brief, every reference already resolved to text."""
    facts = brief["facts"]

    def evidence(*keys):
        return [{"label": facts[k]["label"], "text": facts[k]["t"]} for k in keys if k in facts]

    code = {"BUY": "BULLISH", "SELL": "BEARISH", "HOLD": "NEUTRAL"}[action]
    won = {"BULLISH": "The bull case won", "BEARISH": "The bear case won",
           "NEUTRAL": "Neither side won"}[code]
    how = "Small" if size < 0.34 else "Medium" if size < 0.67 else "Large"
    desk = "%s %s" % (how, "long" if action == "BUY" else "short")
    if action == "HOLD":
        desk = "No trade"
    report = {
        "schema": "report.v1", "symbol": brief["symbol"], "data_id": brief["data_id"],
        "report_id": "", "attempt": 1, "figures": 27, "quantity_words": 0, "script": "2.0.0",
        "argument": {"code": code, "text": won},
        "desk": {"action": action, "size": size, "text": desk},
        "why": "The side with more figures won the argument, and the desk sized the paper "
               "trade to the weak point the other side named.",
        "bull": {"claim": "Holding above the average with momentum that is not yet stretched",
                 "evidence": evidence("sma50_gap", "rsi14", "ret21", "mfi14"),
                 "also": "Up %s over 63 bars." % facts["ret63"]["t"],
                 "weak": "The trend reading is only %s." % facts["adx14"]["t"]},
        "bear": {"claim": "Stretched against the short average, and the band is close",
                 "evidence": evidence("sma10_gap", "bb_pctb", "stoch_k", "cci20"),
                 "also": "Volatility over 21 bars is %s." % facts["vol21"]["t"],
                 "weak": "No lower close in the table yet."},
        "levels": [level_of(brief, key) if isinstance(key, str) else key for key in levels],
        "risk": "The trader proposed %s. Risky: the figures allow more size. Safe: one bad "
                "session at this volatility would undo a larger position. The fund manager "
                "approved it unchanged." % desk.lower(),
        "roles": "The roles ran one at a time in one context.", "skipped": [],
        "headlines_used": ["h1"] if brief["headlines"] else [],
        "receipt": [], "test_record": TEST_RECORD, "disclaimer": DISCLAIMER,
    }
    report["report_id"] = ident(report)
    report["receipt"] = [
        "Figures: 27 inserted by code from the data. Quantity words: 0, not checked.",
        "Data %s to %s, %s. Report %s. Attempt 1. Script 2.0.0."
        % (brief["data_id"], brief["as_of"], brief["session"], report["report_id"]),
        "This covers sourcing and arithmetic, not judgment."]
    return report


def hostile_pair():
    """Every string of both files carries markup, quotes, an ampersand, a script tag, an
    unclosed comment, a 90-character word and non-ASCII characters."""
    brief = make_brief(seed=5, session="unfinished", history=0,
                       flags=["unfinished_session", "close_filled", "short_history"])
    report = make_report(brief, "BUY", 0.25, levels=("prev_close", "sma50", "close.2"))
    report["skipped"] = ["social media analyst", "news analyst"]

    def spoil(value, key=""):
        if isinstance(value, dict):
            return {k: spoil(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [spoil(v, key) for v in value]
        # The schema names are how the page knows its inputs; the dates keep their month.
        if isinstance(value, str) and key != "schema":
            return value + " " + HOSTILE
        return value

    return spoil(brief), spoil(report)


# --- running the page -------------------------------------------------------------------

class Run:
    def __init__(self, completed, out_path):
        self.code = completed.returncode
        self.stdout = completed.stdout.decode("ascii")
        stderr = completed.stderr.decode("ascii").splitlines()
        self.warned = WARN in stderr
        self.stderr = [line for line in stderr if line != WARN]
        self.out_path = out_path
        self.raw = out_path.read_bytes() if out_path.exists() else None
        self.page = self.raw.decode("ascii") if self.raw is not None else None


def write(path, document, encoding="ascii"):
    data = document if isinstance(document, bytes) else json.dumps(document).encode(encoding)
    path.write_bytes(data)
    return path


def run_page(tmp_path, brief, report, name="case", script=PAGE, python=sys.executable):
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / "page.html"
    args = [python, "-I", "-B", str(script), str(write(folder / "brief.json", brief)),
            str(write(folder / "report.json", report)), str(out)]
    return Run(subprocess.run(args, capture_output=True, timeout=60), out)


def good(result):
    """Assert a clean run and a safe, well-formed page, and return the parsed page."""
    assert result.code == 0, (result.stdout, result.stderr)
    assert result.stderr == []
    assert result.stdout.count("\n") == 1 and result.stdout.startswith("PAGE ok ")
    assert result.stdout.rstrip("\n").endswith(" bytes=%d" % len(result.raw))
    return assert_safe(result.page)


def refused(result, line, code):
    assert (result.code, result.stdout, result.stderr) == (code, "", [line])
    assert result.raw is None, "a refused run must not write the page"


@pytest.fixture(scope="module")
def normal(tmp_path_factory):
    brief = make_brief()
    report = make_report(brief)
    result = run_page(tmp_path_factory.mktemp("normal"), brief, report)
    return brief, report, result, good(result)


# --- reading the page back ----------------------------------------------------------------

class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.kids = tag, dict(attrs), parent, []

    def walk(self):
        yield self
        for kid in self.kids:
            if isinstance(kid, Node):
                yield from kid.walk()

    def find(self, tag=None, cls=None):
        return [node for node in self.walk() if node is not self
                and tag in (None, node.tag)
                and (cls is None or cls in (node.attrs.get("class") or "").split())]

    def one(self, tag=None, cls=None):
        found = self.find(tag, cls)
        assert len(found) == 1, (tag, cls, len(found))
        return found[0]

    def raw(self):
        return "".join(kid if isinstance(kid, str) else kid.raw() for kid in self.kids)

    def text(self):
        """All the text below this node as the inputs hold it: runs of white space made one
        space, and the page's non-breaking hyphens made plain again. raw() keeps them, and
        the tests of the dates below read the file itself."""
        return " ".join(self.raw().split()).replace(NB_HYPHEN, "-")


class Tree(HTMLParser):
    """The page as a tree, with a list of everything that is not well formed."""
    VOID = {"meta"}

    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.root = self.at = Node("#root", [], None)
        self.problems = []
        self.feed(page)
        self.close()
        if self.at is not self.root:
            self.problems.append("<%s> is never closed" % self.at.tag)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.at)
        self.at.kids.append(node)
        if tag not in self.VOID:
            self.at = node

    def handle_startendtag(self, tag, attrs):
        self.at.kids.append(Node(tag, attrs, self.at))

    def handle_endtag(self, tag):
        if self.at.tag != tag:
            self.problems.append("</%s> closes <%s> at %s" % (tag, self.at.tag, self.getpos()))
        else:
            self.at = self.at.parent

    def handle_data(self, data):
        self.at.kids.append(data)

    def handle_comment(self, data):
        self.problems.append("a comment at %s" % (self.getpos(),))


def strings(value):
    """Every string in a JSON document."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


TAGS = {"html", "meta", "title", "style", "main", "header", "h1", "h2", "h3", "h4", "p", "div",
        "section", "aside", "b", "i", "small", "span", "svg", "line", "text", "path", "circle",
        "rect", "ul", "li", "dl", "dt", "dd", "table", "tr", "th", "td", "tspan"}
ATTRIBUTES = {"lang", "charset", "name", "content", "class", "style", "role", "aria-label", "x",
              "y", "x1", "x2", "y1", "y2", "dx", "dy", "cx", "cy", "r", "rx", "d", "width",
              "height", "viewbox", "preserveaspectratio", "text-anchor", "fill", "stroke"}


def assert_safe(page):
    """ASCII, well formed, no script, nothing that loads and nothing that runs."""
    assert page.isascii()
    assert not [c for c in set(page) if ord(c) < 32 and c != "\n"]
    tree = Tree(page)
    assert tree.problems == []
    low = page.lower()
    for needle in ("<script", "javascript:", "url(", "@import", "<link", "<img", "<iframe",
                   "<object", "<embed", "<form", "<a ", "<base", "srcset", "<!--"):
        assert needle not in low, needle
    assert "http" not in tree.root.one("style").text().lower()
    # In the title and below the style sheet a hyphen between two digits is the
    # non-breaking one, written as a character reference, and that reference stands nowhere
    # else: Node.text() makes it plain again, so without this a page could hold either hyphen
    # in the wrong place.
    head, body = page.split("</style>", 1)
    title = head.split("<title>", 1)[1].split("</title>", 1)[0]
    for part in (title, body):
        assert not re.search(r"\d-\d", part), re.search(r".{0,30}\d-\d.{0,30}", part)
        assert not re.search(r"(?<!\d)&#8209;|&#8209;(?!\d)", part)
    assert NB_REFERENCE not in head.split("<style>", 1)[1]
    for node in tree.root.walk():
        if node.tag == "#root":
            continue
        assert node.tag in TAGS, node.tag
        for name, value in node.attrs.items():
            assert name in ATTRIBUTES and not name.startswith("on"), (node.tag, name)
            assert value, "an empty attribute: %s on <%s>" % (name, node.tag)
            assert "http" not in value.lower() and "//" not in value, (name, value)
            if name == "style":
                assert re.fullmatch(r"padding:0 \d+px 22px \d+px", value), value
    return tree.root


# --- the normal page ------------------------------------------------------------------------

def test_ok_line_and_file(normal):
    brief, report, result, _ = normal
    assert result.stdout == "PAGE ok TEST %s bytes=%d\n" % (report["report_id"], len(result.raw))
    assert result.raw.endswith(b"</main></html>\n") and b"\r" not in result.raw
    assert result.page.startswith("<!doctype html><html lang=en><meta charset=utf-8>")
    assert result.warned == (b'\nSELF = "000000000000"\n' in SOURCE)


def test_sections_in_the_contract_order(normal):
    _, _, _, root = normal
    main = root.one("main")
    top = [(kid.tag, kid.attrs.get("class", "")) for kid in main.kids]
    assert top == [("header", ""), ("p", "why"), ("section", "s4"), ("section", "s2 in"),
                   ("section", "s3 bull"), ("section", "s3 bear"), ("div", "col s4"),
                   ("div", "col s2"), ("section", "rc")]
    titles = [node.text() for node in main.find("h2")]
    assert titles == [
        "Trading Agents Desk", "The argument", "The paper desk", "Last close", "The check",
        "Price", "Indicators", "The bull case", "The bear case", "What would change this view",
        "Headlines the desk saw", "Risk review", "Fundamentals", "Receipts"]
    # No flags, so no notice strip.
    assert main.find("aside") == []


def test_the_page_is_self_contained(normal):
    _, _, result, _ = normal
    assert_safe(result.page)


def test_header_and_tiles_equal_the_inputs(normal):
    brief, report, _, root = normal
    facts = brief["facts"]
    header = root.one("header")
    assert header.one("h1").text() == "TEST " + brief["name"]
    assert header.one("p").text() == "Desk view at the %s close %s Last close %s USD %s Data " \
        "fetched %s" % (brief["as_of"], DOT, facts["last"]["t"], DOT, brief["fetched_at"])
    tiles = [[part.text() for part in tile.kids] for tile in header.one("div", "t").kids]
    assert tiles == [
        ["The argument", "Neither side won", "Bull against Bear"],
        ["The paper desk", "No trade", "0% of a paper book"],
        ["Last close", facts["last"]["t"] + " USD", facts["chg1"]["t"] + " on the day"],
        ["The check", "27 figures", "inserted by code"]]
    assert root.one("title").text() == "TEST desk view"
    assert root.one("p", "why").text() == "Why" + report["why"]


def test_meters_show_the_fact_on_its_real_scale(normal):
    brief, _, _, root = normal
    meters = root.one("section", "in").find("div", "m")
    assert len(meters) == len(SCALES)
    for meter, (key, (low, high, under, over)) in zip(meters, SCALES.items()):
        item = brief["facts"][key]
        assert meter.one("span").text() == item["label"]
        assert meter.one("b").text() == item["t"]
        state = meter.one("small").text()
        if item["v"] < under:
            assert state.startswith("below %g %s " % (under, DOT))
        elif item["v"] > over:
            assert state.startswith("above %g %s " % (over, DOT))
        else:
            assert state == "between %g and %g" % (under, over)
        assert [node.text() for node in meter.find("text")] == [
            "%g" % x for x in (low, under, over, high)]
        dot = (item["v"] - low) * 100 / (high - low)
        assert meter.one("circle").attrs["cx"] == "%.1f%%" % dot
    # CCI has no scale in the contract, so it gets no meter.
    assert INDICATORS["cci20"] not in [meter.one("span").text() for meter in meters]


def test_returns_are_labelled_in_days_as_the_report_names_them(normal):
    brief, _, _, root = normal
    rows = root.one("section", "in").one("dl").kids
    # The report calls these facts "5-day return"; the page used to say "5 bars".
    assert [row.one("dt").text() for row in rows] == ["1 day", "5 days", "21 days", "63 days"]
    for row, n in zip(rows, (1, 5, 21, 63)):
        item = brief["facts"]["ret%d" % n]
        assert row.one("dd").text() == item["t"]
        # The colour only repeats the sign that the text shows.
        assert row.one("b").attrs["class"] == ("dn" if item["v"] < 0 else "up")
        assert item["t"][0] == ("-" if item["v"] < 0 else "+")


def test_the_colour_of_a_return_follows_the_sign_the_reader_sees(tmp_path):
    # A change that rounds to zero has no minus in its text, so it must not be drawn red.
    brief = make_brief(seed=2)
    brief["facts"]["ret1"].update(v=-0.002, t="+0.00%", sign="+")
    brief["facts"]["ret5"].update(v=-1.25, t="-1.25%", sign="-")
    root = good(run_page(tmp_path, brief, make_report(brief)))
    rows = root.one("section", "in").one("dl").kids
    assert [row.one("b").attrs["class"] for row in rows[:2]] == ["up", "dn"]


def test_evidence_rows_equal_the_inputs(normal):
    _, report, _, root = normal
    for side in ("bull", "bear"):
        card = root.one("section", side)
        assert card.one("h2").text() == "The %s case" % side
        assert card.one("h3").text() == report[side]["claim"]
        rows = [(li.one("span").text(), li.one("b").text()) for li in card.one("ul", "ev").kids]
        assert rows == [(e["label"], e["text"]) for e in report[side]["evidence"]]
        pairs = [(row.one("dt").text(), row.one("dd").text()) for row in card.one("dl").kids]
        assert pairs == [("Also", report[side]["also"]), ("Weak point", report[side]["weak"])]
    bull, bear = root.one("section", "bull"), root.one("section", "bear")
    assert bull.attrs["class"].replace("bull", "bear") == bear.attrs["class"]


def level_rows(root):
    rows = root.one("section", "cv").one("table").kids
    # The third column says how far the price must move. It was headed "From last".
    assert [cell.text() for cell in rows[0].kids] == ["Level", "Price", "To reach it"]
    assert [cell.tag for cell in rows[0].kids] == ["th"] * 3
    return [(row.kids[0].one("b").text(), row.kids[0].one("small").text(),
             row.kids[1].text(), row.kids[2].text()) for row in rows[1:]]


def expected_rows(report):
    return [(lv["label"], "Would support the %s case, %s" % (lv["side"], lv["words"]),
             lv["price_t"], lv["gap_t"]) for lv in report["levels"]]


def test_level_rows_ladder_and_chart_equal_the_inputs(normal):
    brief, report, _, root = normal
    assert level_rows(root) == expected_rows(report)
    ladder = [node.text() for node in root.one("svg", "ld").find("text")]
    assert ladder == ["%s %s %s" % (lv["price_t"], DOT, lv["gap_t"]) for lv in report["levels"]] \
        + ["Last close " + brief["facts"]["last"]["t"]]
    chart = root.one("section", "s4")
    tags = sorted(node.text() for node in chart.find("text", "lt"))
    assert tags == sorted("%s %s %s" % (lv["price_t"], DOT, lv["label"])
                          for lv in report["levels"])
    ends = sorted(node.text() for node in chart.find("text", "el"))
    assert ends == sorted(["Close " + brief["facts"]["last"]["t"],
                           "50d " + brief["facts"]["sma50"]["t"],
                           "200d " + brief["facts"]["sma200"]["t"]])
    assert chart.one("small").text() == CAPTION % (
        120, "close", "USD", brief["series"]["date"][0], brief["as_of"])
    # Three lines and the tint under the close, each with one point for each row.
    paths = chart.find("path")
    assert [p.attrs["class"] for p in paths] == ["ln", "ln", "ln", "fl"]
    assert all(p.attrs["d"].count(",") == ROWS for p in paths)


def test_statement_tiles_show_eight_in_the_preferred_order(normal):
    brief, _, _, root = normal
    tiles = [[part.text() for part in tile.kids]
             for tile in root.one("section", "fd").one("div", "fu").kids]
    wanted = [brief["facts"][key] for key, _, _ in STATEMENTS[:8]]
    assert tiles == [[f["label"], f["t"], "as of " + f["as_of"]] for f in wanted]


def test_headlines_are_marked_as_third_party_text(normal):
    brief, _, _, root = normal
    card = root.one("section", "hd")
    assert card.kids[1].tag == "small" and card.kids[1].text() == \
        "Third-party text, shown as published. Not the desk's words."
    items = card.one("ul").kids
    assert [(li.kids[0], li.one("small").text()) for li in items] == [
        (h["title"], "%s %s %s" % (h["publisher"], DOT, h["date"])) for h in brief["headlines"]]


def test_receipts_carry_the_record_the_check_and_the_disclaimer(normal):
    brief, report, _, root = normal
    card = root.one("section", "rc")
    assert card.one("p", "tr").text() == TEST_RECORD
    # The two figures of the test record are made prominent.
    assert [b.text() for b in card.one("p", "tr").find("b")] == ["-0.9%", "+15.7%"]
    columns = {row.one("dt").text(): row.one("dd") for row in card.one("dl").kids}
    assert list(columns) == ["The check", "The data", "How it ran"]
    assert [li.text() for li in columns["The check"].find("li")] == report["receipt"]
    assert [li.text() for li in columns["The data"].find("li")] == [
        "EQUITY %s yahoo-chart %s as of %s, session finished %s prices in USD"
        % (DOT, DOT, brief["as_of"], DOT), "No data notes."]
    assert columns["How it ran"].text() == report["roles"] + " The desk started from no position."
    foot = card.one("p", "x")
    assert foot.kids[0] == DISCLAIMER
    assert foot.one("small").text() == "Report %s %s Data %s" % (
        report["report_id"], DOT, brief["data_id"])


def test_risk_review_is_split_into_its_sentences(normal):
    _, report, _, root = normal
    items = [li.text() for li in root.one("section", "rk").find("li")]
    assert len(items) == 4 and " ".join(items) == report["risk"]
    assert [b.text() for b in root.one("section", "rk").find("b")] == ["Risky:", "Safe:"]


def test_same_input_gives_the_same_bytes(tmp_path, normal):
    brief, report, first, _ = normal
    again = run_page(tmp_path, brief, report, "again")
    assert again.raw == first.raw and again.stdout == first.stdout


@pytest.mark.parametrize("version", range(9, 15))
def test_other_pythons_write_the_same_bytes(tmp_path, normal, version):
    python = shutil.which("python3.%d" % version)
    if python is None or Path(python).resolve() == Path(sys.executable).resolve():
        pytest.skip("no separate python3.%d on this machine" % version)
    brief, report, first, _ = normal
    other = run_page(tmp_path, brief, report, python=python)
    assert (other.code, other.raw, other.stdout) == (0, first.raw, first.stdout)


# --- what the page shares with the Markdown report ------------------------------------------

# A draft render.py accepts on a brief from make_brief: it names facts and types no figure.
DRAFT = """[verdict] NEUTRAL
[why] Neither case carries the day: {{adx14|label}}.
[bull.claim] Momentum leaves room.
[bull.evidence] rsi14, mfi14, pe_trailing
[bull.also] Nothing else stands out.
[bull.weak] No reversal shows yet.
[bear.claim] The trend is weak.
[bear.evidence] adx14, sma50_gap
[bear.also] The medium average is {{sma50|gap}} away.
[bear.weak] No lower price shows yet.
[levels]
sma50 | bull | while the price holds above it
close.1 | bear | on a move below
[trader] HOLD 0.0
[manager] approved HOLD 0.0
[risk] The trader proposed no trade. The manager approved it unchanged.
[roles] single
[skipped] none
"""


@pytest.fixture(scope="module")
def through_render(tmp_path_factory):
    """One brief taken through render.py and then page.py, both as processes: the brief, the
    Markdown report, the resolved report and the parsed page."""
    folder = tmp_path_factory.mktemp("render")
    brief = make_brief(seed=41)
    write(folder / "brief.json", brief)
    (folder / "draft.txt").write_bytes(DRAFT.encode("ascii"))
    done = subprocess.run(
        [sys.executable, "-I", "-B", str(RENDER), str(folder / "brief.json"),
         str(folder / "draft.txt"), str(folder / "report.md"), str(folder / "report.json")],
        capture_output=True, timeout=60)
    assert done.returncode == 0, (done.stdout, done.stderr)
    markdown = (folder / "report.md").read_bytes().decode("ascii")
    report = json.loads((folder / "report.json").read_bytes())
    drawn = subprocess.run(
        [sys.executable, "-I", "-B", str(PAGE), str(folder / "brief.json"),
         str(folder / "report.json"), str(folder / "page.html")], capture_output=True, timeout=60)
    result = Run(drawn, folder / "page.html")
    return brief, markdown, report, result, good(result)


def said_under_the_first_line(markdown):
    """The sentence the Markdown report prints between its desk view line and its first
    table: what the desk is, and how its only test ended."""
    parts = markdown.split("\n\n")
    assert parts[1].startswith("Desk view at the ") and parts[3].startswith("| The argument |")
    assert re.fullmatch(r"[A-Z][A-Za-z ,.-]+\.", parts[2]), parts[2]
    return parts[2]


def test_the_header_carries_the_sentence_the_report_prints_under_its_first_line(through_render):
    _, markdown, _, result, root = through_render
    said = said_under_the_first_line(markdown)
    # In the header band, in small type under the desk view line and above the four tiles.
    header = root.one("header")
    assert [kid.tag for kid in header.kids] == ["h2", "h1", "p", "small", "div"]
    assert header.kids[3].raw() == said and header.kids[3].attrs == {}
    assert result.page.count("</p><small>%s</small><div class=t>" % said) == 1
    # Once on the page: the receipts hold the test record with its two figures, not this.
    assert result.page.count(said) == 1 and markdown.count(said) == 1


@pytest.mark.parametrize("session, source, flags", [
    ("finished", "yahoo-chart", []), ("unfinished", "yahoo-chart", ["unfinished_session"]),
    ("unknown", "price-file", ["statements_absent", "no_headlines"]),
    ("finished", "yahoo-chart", ["prices_behind", "close_filled"])])
def test_every_page_carries_that_sentence(tmp_path, through_render, session, source, flags):
    said = said_under_the_first_line(through_render[1])
    brief = make_brief(seed=42, session=session, flags=flags)
    brief["source"] = source
    for action, size in (("HOLD", 0.0), ("BUY", 0.5)):
        root = good(run_page(tmp_path, brief, make_report(brief, action, size), action))
        header = root.one("header")
        assert [kid.tag for kid in header.kids] == ["h2", "h1", "p", "small", "div"]
        assert header.kids[3].raw() == said


def test_the_levels_are_headed_on_the_page_as_they_are_in_the_report(through_render):
    brief, markdown, report, _, root = through_render
    head = [line for line in markdown.split("\n") if line.startswith("| Level |")]
    assert len(head) == 1
    cells = [cell.strip() for cell in head[0].strip("|").split("|")]
    # The page leaves the last column out: it says which case a level supports under the
    # level's name.
    shown = [cell.text() for cell in root.one("section", "cv").one("table").kids[0].kids]
    assert shown == cells[:3] and cells[3] == "Would support"
    assert level_rows(root) == expected_rows(report)
    # The distances under that heading are the report's, sign and all.
    rows = [[cell.strip() for cell in line.strip("|").split("|")]
            for line in markdown.split("\n\n")[7].split("\n")[2:]]
    assert [row[2] for row in rows] == [row[3] for row in level_rows(root)]
    last = brief["facts"]["last"]["v"]
    assert [row[2][0] for row in rows] == [
        "+" if level["price"] > last else "-" for level in report["levels"]]


# --- dates and hyphens ----------------------------------------------------------------------

def test_esc_writes_the_hyphen_between_two_digits_as_the_non_breaking_one():
    esc = load_script()["esc"]
    assert esc("2026-11-10") == "2026&#8209;11&#8209;10"
    assert esc("fetched 2026-11-11 04:06 UTC, as of 2026-11-10.") == (
        "fetched 2026&#8209;11&#8209;11 04:06 UTC, as of 2026&#8209;11&#8209;10.")
    # Every hyphen that stands between two digits, one after another.
    assert esc("1-2-3") == "1&#8209;2&#8209;3" and esc("5-10%") == "5&#8209;10%"
    assert esc(20261110) == "20261110" and esc(-5) == "-5"
    # Any other hyphen is left as it is: a sign, a word, a ticker, a spaced dash.
    for same in ("-0.82", "+1.25%", "-1.25%", "a -3.5% move", "50-day average", "BRK-B",
                 "buy-and-hold", "5 - 3", "5 -3", "5- 3", "x-1", "1-x", "COVID-19", "A-1 rated",
                 "-", "--", "3--4", "", "(-2)", "1,-2"):
        assert esc(same) == same, same
    # The text is made safe first. A character reference has digits of its own, and a hyphen
    # beside one is not between two digits of the text.
    assert esc('<a href="1-2">') == "&lt;a href=&quot;1&#8209;2&quot;&gt;"
    assert esc("5'-3") == "5&#x27;-3" and esc("'-3") == "&#x27;-3" and esc("5-'") == "5-&#x27;"
    assert esc("1&-2") == "1&amp;-2" and esc("1-&2") == "1-&amp;2"
    assert esc("&#8209;") == "&amp;#8209;"
    # The cut counts the characters of the input, so it never falls inside a reference, and a
    # hyphen the cut leaves at the end has no digit after it.
    assert esc("2026-11-10", 7) == "2026&#8209;11" and esc("2026-11-10", 8) == "2026&#8209;11-"
    assert esc("2026-11-10", 5) == "2026-" and esc("1-2-3-4", 4) == "1&#8209;2-"
    # A control character between the two becomes a space, and the hyphen stays plain.
    assert esc("1\n-2") == "1 -2" and esc("1-\x002") == "1- 2"
    for text in ("2026-11-10", "1-2", '"1-2"', "<1-2>"):
        assert esc(text).isascii()


def test_every_date_on_the_page_is_written_with_the_non_breaking_hyphen(tmp_path):
    brief = make_brief(seed=31, flags=["close_filled"])
    facts = brief["facts"]
    report = make_report(brief, levels=("close.1", "sma50"))
    # Texts as render.py resolves them: a statement figure with its date, a sentence with one.
    report["bull"]["evidence"].append({
        "label": facts["pe_trailing"]["label"],
        "text": "%s (as of %s)" % (facts["pe_trailing"]["t"], facts["pe_trailing"]["as_of"])})
    report["why"] = "The price stood at %s on %s." % (facts["last"]["t"], brief["as_of"])
    report["risk"] = "The gap on %s was the risk. The manager agreed." % brief["series"][
        "date"][-2]
    result = run_page(tmp_path, brief, report)
    root = good(result)
    assert result.page.isascii()
    chart, levels, receipts = (root.one("section", cls) for cls in ("s4", "cv", "rc"))
    columns = {row.one("dt").text(): row.one("dd") for row in receipts.one("dl").kids}
    dates = brief["series"]["date"]
    dated = [node for node in chart.find("text", "lt") if "close on" in node.text()]
    places = {
        "the header line": (root.one("header").one("p"), [brief["as_of"], "2026-11-11"]),
        "the why sentence": (root.one("p", "why"), [brief["as_of"]]),
        "the notice strip": (root.one("aside"), ["2026-11-07"]),
        "the chart caption": (chart.one("small"), [dates[0], dates[-1]]),
        "a level label in the chart": (dated[0], [dates[-2]]),
        "a level in the table": (levels.one("table").kids[1], [dates[-2]]),
        "an evidence row": (root.one("section", "bull").one("ul", "ev"), ["2026-11-10"]),
        "the risk review": (root.one("section", "rk"), [dates[-2]]),
        "the statement tiles": (root.one("section", "fd"), ["2026-11-10", "2026-09-30"]),
        "the headlines": (root.one("section", "hd"), [dates[-1], dates[-2]]),
        "the receipt lines": (columns["The check"], [brief["as_of"]]),
        "the data line and the notes": (columns["The data"], [brief["as_of"], "2026-11-07"]),
    }
    for where, (node, wanted) in places.items():
        raw = node.raw()
        for date in wanted:
            assert date.replace("-", NB_HYPHEN) in raw, (where, date)
        assert not re.search(r"\d-\d", raw), where
        # Read back with the hyphens made plain, it is the date of the inputs.
        assert all(date in node.text() for date in wanted), where
    # In the file: a character reference, so the page is still ASCII, and no date anywhere
    # with a plain hyphen. Nothing but the dates holds the reference on this page.
    body = result.page.split("</style>", 1)[1]
    assert not re.search(r"\d-\d", body) and NB_HYPHEN not in result.page
    written = re.findall(r"\d{4}&#8209;\d\d&#8209;\d\d", body)
    assert len(written) >= 20 and body.count(NB_REFERENCE) == 2 * len(written)
    assert NB_REFERENCE not in result.page.split("</style>", 1)[0]
    # The one line on stdout is not markup: it names nothing with a date here.
    assert NB_REFERENCE not in result.stdout


def test_a_hyphen_that_does_not_join_two_digits_stays_plain(tmp_path):
    brief = make_brief(seed=32, symbol="BRK-B", flags=["rows_dropped"],
                       notes=["A well-known gap - see the note - of 3 rows."])
    brief["facts"]["ret5"].update(v=-1.25, t="-1.25%", sign="-")
    brief["facts"]["bb_pctb"].update(v=-0.2, t="-0.20")
    brief["headlines"][0]["title"] = "Chip-maker's well-known rival cuts prices - again"
    report = make_report(brief, "SELL", 0.25, levels=("sma50", "prev_close"))
    report["levels"][1]["gap_t"] = "-0.4%"
    report["bear"]["also"] = "A -3.5% move on a 5-day view is not a trend."
    result = run_page(tmp_path, brief, report)
    root = good(result)
    for kept in ("<title>BRK-B desk view</title>", "<h1>BRK-B <i>", ">-1.25%</b>", ">-0.20</b>",
                 ">50-day average</b>", "Chip-maker&#x27;s well-known rival cuts prices - again",
                 "<td>-0.4%</td>", "&#183; -0.4%</text>", "A -3.5% move on a 5-day view",
                 "<b>-0.9%</b>", "buy-and-hold", "price-only", "Third-party text",
                 "A well-known gap - see the note - of 3 rows.", ">-0.5</text>"):
        assert kept in result.page, kept
    assert result.stdout.startswith("PAGE ok BRK-B ")
    assert root.one("title").raw() == "BRK-B desk view"
    # The only non-breaking hyphens on this page are those of its dates.
    body = result.page.split("</style>", 1)[1]
    written = re.findall(r"\d{4}&#8209;\d\d&#8209;\d\d", body)
    assert written and body.count(NB_REFERENCE) == 2 * len(written)


def test_a_hyphen_between_digits_in_the_title_or_beside_markup_keeps_the_page_whole(tmp_path):
    brief = make_brief(seed=33, symbol="T1-2")
    brief["name"] = 'Class 1-2 "A" <b>3-4</b> & 5-6'
    brief["headlines"][0]["title"] = "5'-6\" tall, 7-'8, &9-0; <1-2> </title> 3-4"
    report = make_report(brief)
    report["levels"][0].update(label='x" y=1-2 onload="3-4', price_t="1-2-3-4-5-6-7-8",
                               gap_t="-1-2%")
    result = run_page(tmp_path, brief, report)
    # Well formed, ASCII, and only the page's own tags and attributes.
    root = good(result)
    assert result.page.count("<title>") == result.page.count("</title>") == 1
    assert "<title>T1&#8209;2 desk view</title>" in result.page
    assert root.one("title").raw() == "T1%s2 desk view" % NB_HYPHEN
    assert root.one("title").text() == "T1-2 desk view"
    assert root.one("h1").text() == "T1-2 " + brief["name"]
    assert "Class 1&#8209;2 &quot;A&quot; &lt;b&gt;3&#8209;4&lt;/b&gt; &amp; 5&#8209;6" \
        in result.page
    assert root.one("section", "hd").one("ul").kids[0].kids[0].replace(NB_HYPHEN, "-") \
        == brief["headlines"][0]["title"]
    assert "5&#x27;-6&quot; tall, 7-&#x27;8, &amp;9&#8209;0; &lt;1&#8209;2&gt;" in result.page
    # The line on stdout is not markup: it names the symbol as it was given.
    assert result.stdout.startswith("PAGE ok T1-2 ")
    # No text of the inputs is written into an attribute, so no attribute holds the hyphen
    # in either form, and no quote of an input ends one.
    for node in root.walk():
        for name, value in node.attrs.items():
            assert NB_HYPHEN not in value and "8209" not in value, (node.tag, name)
            assert "onload" not in name and "1-2" not in value
    # Every reference in the file is whole: a cut falls on a character of the input.
    assert "&#8209" not in result.page.replace(NB_REFERENCE, "")
    row = level_rows(root)[0]
    assert (row[0], row[2], row[3]) == ('x" y=1-2 onload="3-4', "1-2-3-4-5-6-7-8", "-1-2%")
    assert "<td>1&#8209;2&#8209;3&#8209;4&#8209;5&#8209;6&#8209;7&#8209;8</td>" in result.page
    assert "<td>-1&#8209;2%</td>" in result.page
    # In the drawings the price is cut to 13 and to 12 characters: the hyphen the second cut
    # leaves at the end has no digit after it and stays plain.
    chart = [node.raw() for node in root.one("section", "s4").find("text", "lt")]
    assert "1-2-3-4-5-6-7".replace("-", NB_HYPHEN) + " " in [x[:14] for x in chart]
    ladder = root.one("svg", "ld").find("text")[0].raw()
    assert ladder.startswith("1-2-3-4-5-6".replace("-", NB_HYPHEN) + "- " + DOT)


def css_rules(sheet):
    """The declarations of each selector of a style sheet, outside its phone block."""
    rules = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", sheet.split("@media", 1)[0]):
        for selector in selectors.split(","):
            rules.setdefault(selector.strip(), []).extend(x for x in body.split(";") if x)
    return rules


def test_the_returns_under_the_meters_do_not_wrap(normal):
    brief, _, _, root = normal
    sheet = root.one("style").raw()
    # The page lets any long word break (overflow-wrap:anywhere on the body), and a return
    # stands in a narrow column: "-12.34%" used to break after its sign or inside its number.
    assert "overflow-wrap:anywhere" in css_rules(sheet)["body"]
    assert "white-space:nowrap" in css_rules(sheet)[".in dd"]
    phone = sheet.split("@media", 1)[1]
    assert "white-space" not in phone and ".in dd" not in phone
    # The rule reaches each return, and nothing else on the page: the returns are the dd
    # elements of the card with the class "in", and the only ones there.
    card = root.one("section", "in")
    cells = card.find("dd")
    assert [cell.text() for cell in cells] == [
        brief["facts"]["ret%d" % days]["t"] for days in (1, 5, 21, 63)]
    assert all(cell.parent.parent.parent is card for cell in cells)
    assert "in" in card.attrs["class"].split() and len(root.find("section", "in")) == 1
    assert all(not {"class", "style"} & set(cell.attrs) for cell in cells)


# --- outcomes, sessions, flags and missing parts --------------------------------------------

def skeleton(node):
    """The markup of a node without its text."""
    kids = [skeleton(kid) for kid in node.kids if isinstance(kid, Node)]
    return (node.tag, sorted(node.attrs.items()), kids)


def test_buy_sell_and_hold_get_the_same_plain_tiles(tmp_path):
    brief = make_brief(seed=2)
    seen = {}
    for action, size, desk, share in (("HOLD", 0.0, "No trade", "0%"),
                                      ("BUY", 0.25, "Small long", "25%"),
                                      ("SELL", 0.5, "Medium short", "50%"),
                                      ("BUY", 1.0, "Large long", "100%")):
        report = make_report(brief, action, size)
        root = good(run_page(tmp_path, brief, report, action + share[:-1]))
        header = root.one("header")
        tiles = header.one("div", "t").kids
        assert [part.text() for part in tiles[0].kids][1] == report["argument"]["text"]
        assert [part.text() for part in tiles[1].kids] == [
            "The paper desk", desk, share + " of a paper book"]
        seen[action + share] = skeleton(header)
    # Same tags, same classes, no extra colour or badge for a trade.
    assert len({json.dumps(value) for value in seen.values()}) == 1
    assert "style" not in json.dumps(seen["BUY25%"])


@pytest.mark.parametrize("session", ["unfinished", "unknown"])
def test_a_session_that_is_not_finished_has_a_last_price_not_a_close(tmp_path, session):
    brief = make_brief(seed=3, session=session, symbol="COIN-USD")
    report = make_report(brief)
    result = run_page(tmp_path, brief, report)
    root = good(result)
    last = brief["facts"]["last"]["t"]
    header = root.one("header")
    assert header.one("p").text() == "Desk view on %s, session %s %s Last price %s USD %s Data " \
        "fetched %s" % (brief["as_of"], session, DOT, last, DOT, brief["fetched_at"])
    assert header.one("div", "t").kids[2].one("h2").text() == "Last price"
    chart = root.one("section", "s4")
    assert chart.one("small").text() == CAPTION % (
        120, "price", "USD", brief["series"]["date"][0], brief["as_of"])
    assert "Price " + last in [node.text() for node in chart.find("text", "el")]
    assert root.one("svg", "ld").find("text")[-1].text() == "Last price " + last
    # The only "close" left on the page is in the report's own words and the labels of facts.
    assert "Last close" not in result.page and "Close " + last not in result.page
    assert "daily closes" not in result.page


@pytest.mark.parametrize("flag", FLAGS)
def test_each_flag_brings_the_notice_strip(tmp_path, flag):
    brief = make_brief(seed=4, flags=[flag])
    root = good(run_page(tmp_path, brief, make_report(brief)))
    strip = root.one("aside")
    assert strip.one("b").text() == "Data notes"
    assert [li.text() for li in strip.find("li")] == [brief["notes"][0]["text"]]
    # The strip comes straight after the "why" text and before the first card.
    kids = root.one("main").kids
    assert kids.index(strip) == 2 and kids[1].attrs.get("class") == "why"
    # The receipts repeat the notes.
    assert brief["notes"][0]["text"] in root.one("section", "rc").text()
    behind = "(the data source is behind)" in root.one("header").one("p").text()
    assert behind == (flag == "prices_behind")


def test_flags_without_notes_are_still_named(tmp_path):
    brief = make_brief(seed=4, flags=["rows_dropped", "history_gap"], notes=[])
    root = good(run_page(tmp_path, brief, make_report(brief)))
    assert [li.text() for li in root.one("aside").find("li")] == ["rows dropped", "history gap"]


def test_several_notes_are_all_listed(tmp_path):
    notes = ["First generated note.", "Second generated note.", "Third generated note."]
    brief = make_brief(seed=4, flags=["close_filled", "rows_dropped"], notes=notes)
    root = good(run_page(tmp_path, brief, make_report(brief)))
    assert [li.text() for li in root.one("aside").find("li")] == notes


def test_notes_without_flags_stay_in_the_receipts(tmp_path):
    brief = make_brief(seed=4, notes=["Prices are in pence (GBp)."], unit="GBp")
    root = good(run_page(tmp_path, brief, make_report(brief)))
    assert root.find("aside") == []
    assert "Prices are in pence (GBp)." in root.one("section", "rc").text()
    assert root.one("header").one("div", "t").kids[2].kids[1].text().endswith(" GBp")


def test_a_brief_without_a_currency_says_units(tmp_path):
    brief = make_brief(seed=4, unit="", notes=["The data names no currency."])
    brief.update(type="UNKNOWN", source="price-file", session="unknown")
    result = run_page(tmp_path, brief, make_report(brief))
    root = good(result)
    last = brief["facts"]["last"]["t"]
    assert root.one("header").one("div", "t").kids[2].kids[1].text() == last + " units"
    assert "Last price %s units %s" % (last, DOT) in root.one("header").one("p").text()
    assert "120 daily prices in units, " in root.one("section", "s4").one("small").text()
    assert "UNKNOWN %s price-file %s as of %s, session unknown %s prices in units" % (
        DOT, DOT, brief["as_of"], DOT) in root.one("section", "rc").text()


def test_a_price_file_has_no_fetch_time_on_the_page(tmp_path):
    # Nothing was downloaded, so the header must not say when the data was fetched. The
    # brief still carries the newest row's time in fetched_at, for the staleness rules.
    brief = make_brief(seed=4, session="unknown", notes=["A generated note."])
    brief.update(type="UNKNOWN", source="price-file", fetched_at="2026-11-10 14:30 UTC")
    result = run_page(tmp_path, brief, make_report(brief))
    line = good(result).one("header").one("p").text()
    assert line == "Desk view on %s, session unknown %s Last price %s USD %s Prices from a file" % (
        brief["as_of"], DOT, brief["facts"]["last"]["t"], DOT)
    assert "fetched" not in result.page.lower() and "14:30" not in result.page


def test_no_headlines_leaves_the_card_out(tmp_path):
    brief = make_brief(seed=6, headlines=0, flags=["no_headlines"])
    root = good(run_page(tmp_path, brief, make_report(brief)))
    assert root.find("section", "hd") == []
    # The statements card then takes a full row below the two stacks.
    assert root.one("section", "fd").parent.tag == "main"
    assert [len(col.kids) for col in root.find("div", "col")] == [1, 1]


def test_no_statements_leaves_the_card_out(tmp_path):
    brief = make_brief(seed=6, statements=False, flags=["statements_absent"])
    root = good(run_page(tmp_path, brief, make_report(brief)))
    assert root.find("section", "fd") == []
    assert root.one("section", "hd").parent.tag == "main"


def test_neither_headlines_nor_statements(tmp_path):
    brief = make_brief(seed=6, statements=False, headlines=0,
                       flags=["statements_absent", "no_headlines"])
    root = good(run_page(tmp_path, brief, make_report(brief)))
    assert root.find("section", "fd") == root.find("section", "hd") == []
    assert [kid.attrs.get("class") for kid in root.one("main").kids][-3:] == [
        "col s4", "col s2", "rc"]


def test_few_statement_facts_are_shown_as_they_are(tmp_path):
    brief = make_brief(seed=6)
    for key in ("pe_forward", "mcap", "margin_ttm", "rev_q_yoy", "ni_q_yoy", "debt_equity",
                "rev_ttm", "ni_ttm", "rev_q", "debt"):
        del brief["facts"][key]
    root = good(run_page(tmp_path, brief, make_report(brief)))
    labels = [tile.one("dt").text() for tile in root.one("div", "fu").kids]
    assert labels == ["trailing P/E", "PEG ratio", "net income, newest quarter",
                      "shareholders' equity"]


def test_a_missing_indicator_has_no_meter(tmp_path):
    brief = make_brief(seed=6)
    report = make_report(brief)
    del brief["facts"]["rsi14"], brief["facts"]["adx14"], brief["facts"]["ret63"]
    root = good(run_page(tmp_path, brief, report))
    card = root.one("section", "in")
    assert [m.one("span").text() for m in card.find("div", "m")] == [
        INDICATORS[key] for key in ("stoch_k", "mfi14", "bb_pctb")]
    assert [row.one("dt").text() for row in card.one("dl").kids] == ["1 day", "5 days", "21 days"]


def test_a_value_outside_the_usual_range_widens_the_scale(tmp_path):
    brief = make_brief(seed=6)
    brief["facts"]["bb_pctb"].update(v=-0.82, t="-0.82")
    brief["facts"]["rsi14"].update(v=12.5, t="12.50")
    brief["facts"]["stoch_k"].update(v=91.0, t="91.00")
    root = good(run_page(tmp_path, brief, make_report(brief)))
    meters = {m.one("span").text(): m for m in root.one("section", "in").find("div", "m")}
    band = meters[INDICATORS["bb_pctb"]]
    assert [node.text() for node in band.find("text")] == ["-0.82", "0", "1", "1.5"]
    assert band.one("circle").attrs["cx"] == "0.0%"
    assert band.one("small").text() == "below 0 %s under lower band" % DOT
    assert meters[INDICATORS["rsi14"]].one("small").text() == "below 30 %s oversold" % DOT
    assert meters[INDICATORS["stoch_k"]].one("small").text() == "above 80 %s overbought" % DOT


METER_WORDS = [
    # fact, value, text, the words beside it
    ("rsi14", 30.0, "30.00", "between 30 and 70"),
    ("rsi14", 70.0, "70.00", "between 30 and 70"),
    ("rsi14", 29.99, "29.99", "below 30 %s oversold" % DOT),
    ("rsi14", 70.01, "70.01", "above 70 %s overbought" % DOT),
    # A value that prints as the threshold reads as on it: the words follow the number shown.
    ("rsi14", 29.996, "30.00", "between 30 and 70"),
    ("rsi14", 70.004, "70.00", "between 30 and 70"),
    ("bb_pctb", 1.004, "1.00", "between 0 and 1"),
    ("bb_pctb", -0.004, "-0.00", "between 0 and 1"),
    ("bb_pctb", 1.006, "1.01", "above 1 %s over upper band" % DOT),
    ("adx14", 20.0, "20.00", "between 20 and 25"),
    ("adx14", 25.0, "25.00", "between 20 and 25"),
    ("adx14", 19.994, "19.99", "below 20 %s weak trend" % DOT),
    ("stoch_k", 80.0, "80.00", "between 20 and 80"),
    ("mfi14", 20.0, "20.00", "between 20 and 80"),
]


@pytest.mark.parametrize("key, value, text, words", METER_WORDS,
                         ids=["%s=%r" % row[:2] for row in METER_WORDS])
def test_meter_words_at_a_threshold_follow_the_number_shown(tmp_path, key, value, text, words):
    brief = make_brief(seed=6)
    brief["facts"][key].update(v=value, t=text)
    root = good(run_page(tmp_path, brief, make_report(brief)))
    meters = {m.one("span").text(): m for m in root.one("section", "in").find("div", "m")}
    assert meters[INDICATORS[key]].one("b").text() == text
    assert meters[INDICATORS[key]].one("small").text() == words


def test_short_history_has_no_200_day_line(tmp_path):
    brief = make_brief(seed=7, history=0, flags=["short_history"])
    assert "sma200" not in brief["facts"] and brief["series"]["sma200"][-1] is None
    root = good(run_page(tmp_path, brief, make_report(brief)))
    chart = root.one("section", "s4")
    assert [p.attrs["class"] for p in chart.find("path")] == ["ln", "ln", "fl"]
    assert len(chart.find("circle")) == 2
    assert not [n for n in chart.find("text", "el") if n.text().startswith("200d")]


# --- levels and price sizes -----------------------------------------------------------------

def percent(value):
    assert value.endswith("%"), value
    return float(value[:-1])


def level_offsets(count):
    """Where the chart's level labels stand against their lines, from the highest price down:
    the top one above its line, the bottom one below it, any other on its line. One level
    alone is on its line."""
    if count < 2:
        return [4] * count
    return [-8] + [4] * (count - 2) + [16]


def axis_step(size):
    """The step of the price axis for drawn prices that span ``size``: the smallest of 1, 2,
    2.5 and 5 times a power of ten that crosses them in five steps or fewer."""
    family = sorted(m * 10.0 ** k for k in range(-12, 13) for m in (1, 2, 2.5, 5))
    return min(step for step in family if size / step <= 5)


def check_drawings(root, report):
    """Every level is in the table, on the ladder and in the chart, at a place on the page."""
    assert level_rows(root) == expected_rows(report)
    ladder = root.one("svg", "ld")
    marks = [line for line in ladder.find("line") if line.attrs["x1"] != "5%"]
    assert len(marks) == len(report["levels"])
    for node in marks + ladder.find("circle"):
        assert 5 <= percent(node.attrs.get("x1") or node.attrs["cx"]) <= 95
    # One row of labels for each level, so no two can share a place.
    rows = [node.attrs["y"] for node in ladder.find("text")[:-1]]
    assert len(set(rows)) == len(rows) == len(report["levels"])
    assert int(ladder.attrs["height"]) >= int(max(rows, key=int, default="0"))
    chart = root.one("section", "s4")
    lines = chart.find("line", "lv")
    assert len(lines) == len(report["levels"])
    for line in lines:
        assert 4.9 <= percent(line.attrs["y1"]) <= 88.1
    # Each level label is drawn at its own line, from the highest price down. A label used
    # to be pushed down to keep its distance from the one above, and so came to stand on a
    # neighbour's line.
    labels = chart.find("text", "lt")
    assert [label.attrs["y"] for label in labels] == [line.attrs["y1"] for line in lines]
    assert [int(label.attrs["dy"]) for label in labels] == level_offsets(len(labels))
    # The labels at the lines' ends still keep a clear distance from each other.
    ends = sorted(percent(node.attrs["y"]) for node in chart.find("text", "el"))
    assert all(b - a >= 5.99 for a, b in zip(ends, ends[1:]))


MONTH_NAMES = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
LINE_COLOURS = {"#2a78d6": "close", "#eb6834": "sma50", "#1baf7a": "sma200"}


def check_geometry(brief, report, root):
    """What the page DRAWS, against the inputs: the axis, every point of every line, the month
    names, the level lines and their labels, the end dots and the ladder.

    The texts are compared elsewhere. Without this an upside-down chart, a series drawn
    backwards or month names one month off would pass every other test."""
    series = brief["series"]
    dates = series["date"]
    count = len(dates)
    chart = root.one("section", "s4")

    # The axis: each number sits on its own grid line, and a higher price is higher up.
    grid = chart.find("line", "g")
    numbers = [node for node in chart.find("text") if node.attrs.get("text-anchor") == "end"]
    assert len(grid) == len(numbers) >= 2
    ticks = []
    for line, number in zip(grid, numbers):
        assert line.attrs["y1"] == line.attrs["y2"] == number.attrs["y"]
        assert re.fullmatch(r"\d+(\.\d+)?", number.text()), number.text()
        ticks.append((float(number.text()), percent(number.attrs["y"])))
    (low, y_low), (high, y_high) = ticks[0], ticks[-1]
    assert high > low and y_high < y_low, "a higher price must be higher on the page"

    def y_of(price):
        return y_low + (price - low) * (y_high - y_low) / (high - low)

    for value, y in ticks:
        assert abs(y_of(value) - y) <= 0.02, ticks
    # Round steps around everything that is drawn: no more lines than the prices need, and
    # the step is 1, 2, 2.5 or 5 times a power of ten.
    drawn = [level["price"] for level in report["levels"]]
    for key in LINE_COLOURS.values():
        drawn += [v for v in series[key] if v is not None]
    assert low <= min(drawn) and max(drawn) <= high
    step = (high - low) / (len(ticks) - 1)
    assert all(abs(b[0] - a[0] - step) <= 1e-9 * high for a, b in zip(ticks, ticks[1:]))
    assert min(drawn) - low < step * (1 + 1e-9) and high - max(drawn) < step * (1 + 1e-9)
    if max(drawn) > min(drawn):
        assert abs(step / axis_step(max(drawn) - min(drawn)) - 1) <= 1e-9, (step, ticks)

    # The lines: one point for each row that has a value, at that row and at that value.
    box = [node for node in chart.find("svg") if "viewbox" in node.attrs]
    assert [node.attrs["viewbox"] for node in box] == ["0 0 %d 100" % (count - 1)]
    drawn = {}
    for path in chart.find("path", "ln"):
        key = LINE_COLOURS[path.attrs["stroke"]]
        assert path.attrs["d"].startswith("M")
        points = [tuple(float(x) for x in point.split(","))
                  for point in path.attrs["d"][1:].split("L")]
        want = [(i, v) for i, v in enumerate(series[key]) if v is not None]
        assert len(points) == len(want), key
        for (x, y), (i, v) in zip(points, want):
            assert x == i and abs(y - y_of(v)) <= 0.02, (key, i, y, y_of(v))
        drawn[key] = points
    assert set(drawn) == {key for key in LINE_COLOURS.values() if series[key][-1] is not None}
    # The tint lies under the close: the same points, then down to the foot of the plot.
    tint = chart.one("path", "fl").attrs["d"]
    assert tint.endswith("V88H0Z")
    assert tint[1:-6].split("L") == chart.find("path", "ln")[-1].attrs["d"][1:].split("L")
    assert chart.find("path", "ln")[-1].attrs["stroke"] == "#2a78d6"

    # A dot where each line ends, in the line's colour.
    dots = {LINE_COLOURS[c.attrs["fill"]]: c for c in chart.find("circle")}
    assert set(dots) == set(drawn)
    for key, dot in dots.items():
        assert dot.attrs["cx"] == "100%"
        assert abs(percent(dot.attrs["cy"]) - y_of(series[key][-1])) <= 0.02, key

    # A month name stands at the first row of that month, and names that month.
    names = [node for node in chart.find("text") if node.attrs.get("y") == "100%"]
    assert names, "no month names under the chart"
    for node in names:
        at = percent(node.attrs["x"]) * (count - 1) / 100
        row = round(at)
        assert abs(at - row) <= 0.06, node.attrs["x"]
        assert node.text() == MONTH_NAMES[int(dates[row][5:7]) - 1], (node.text(), dates[row])
        assert dates[row - 1][5:7] != dates[row][5:7], (dates[row - 1], dates[row])

    # A level: a dashed line across the plot at its price. Its label starts where the line
    # starts, so no dash shows before the price, where it would read as a minus sign. The
    # lines and the labels come in the same order, from the highest price down, and each
    # label names the level whose line it stands at.
    lines, labels = chart.find("line", "lv"), chart.find("text", "lt")
    levels = sorted(report["levels"], key=lambda level: -level["price"])
    assert len(lines) == len(labels) == len(levels)
    for line, label, level in zip(lines, labels, levels):
        assert "x1" not in line.attrs and line.attrs["x2"] == "100%"
        assert line.attrs["y1"] == line.attrs["y2"]
        assert abs(percent(line.attrs["y1"]) - y_of(level["price"])) <= 0.02
        assert label.attrs["x"] == "0", "a level label must start where its line starts"
        assert label.text() == "%s %s %s" % (
            " ".join(level["price_t"][:13].split()), DOT, " ".join(level["label"][:44].split()))
        assert label.attrs["y"] == line.attrs["y1"], "a level label must stand at its own line"
    # Above the line for the highest level, below it for the lowest, on it otherwise.
    assert [int(label.attrs["dy"]) for label in labels] == level_offsets(len(levels))

    # The ladder: every level and the last price at its place between the lowest and the
    # highest of them, left to right in price order.
    ladder = root.one("svg", "ld")
    last = brief["facts"]["last"]["v"]
    prices = [level["price"] for level in report["levels"]] + [last]
    foot = min(prices)
    size = max(prices) - foot or 1
    marks = [line for line in ladder.find("line") if line.attrs["x1"] != "5%"]
    places = [percent(mark.attrs["x1"]) for mark in marks] + [
        percent(ladder.one("circle").attrs["cx"])]
    for place, price in zip(places, prices):
        assert abs(place - (5 + (price - foot) * 90 / size)) <= 0.06, (places, prices)
    for mark, label, level in zip(marks, ladder.find("text"), report["levels"]):
        assert mark.attrs["x1"] == mark.attrs["x2"] == label.attrs["x"]
        assert label.text().startswith(level["price_t"])


def test_the_chart_and_the_ladder_are_drawn_from_the_inputs(normal):
    brief, report, _, root = normal
    check_geometry(brief, report, root)


@pytest.mark.parametrize("seed, history, levels", [
    (21, 200, ("high52", "low52", "sma200")), (22, 200, ("prev_close",)),
    (23, 0, ("close.1", "sma50")), (24, 60, ("last", "sma10", "close.3")),
    (25, 200, ("low52", "last"))])
def test_the_drawings_follow_the_inputs_for_other_series(tmp_path, seed, history, levels):
    flags = [] if history >= 200 else ["short_history"]
    brief = make_brief(seed=seed, history=history, flags=flags)
    report = make_report(brief, "SELL", 0.3, levels=levels)
    check_geometry(brief, report, good(run_page(tmp_path, brief, report)))


LEVEL_SETS = {
    "one": ("prev_close",),
    "two": ("prev_close", "low52"),
    "three": ("high52", "sma50", "close.2"),
    "one equal to the last price": ("last",),
    "two, one equal to the last price": ("last", "sma50"),
    "three equal to the last price": ("last", "last", "last"),
    "three close together": ("close.1", "close.2", "close.3"),
}


@pytest.mark.parametrize("name", list(LEVEL_SETS))
def test_one_to_three_levels(tmp_path, name):
    brief = make_brief(seed=8)
    report = make_report(brief, "BUY", 0.25, levels=LEVEL_SETS[name])
    root = good(run_page(tmp_path, brief, report))
    check_drawings(root, report)
    check_geometry(brief, report, root)
    if "equal" in name:
        assert level_rows(root)[0][3] == "+0.0%"


# Levels as fractions of the last price, in the order the report names them, and where each
# label must stand against its line: -8 is above the line, 16 below it, 4 on it.
LABEL_PLACES = {
    "one above the price": ((1.02,), {"A": 4}),
    "one below the price": ((0.97,), {"A": 4}),
    "two": ((1.02, 0.97), {"A": -8, "B": 16}),
    "two, the lower one named first": ((0.97, 1.02), {"B": -8, "A": 16}),
    "two a hair apart": ((1.02, 1.018), {"A": -8, "B": 16}),
    "three": ((1.0, 1.03, 0.96), {"B": -8, "A": 4, "C": 16}),
    "three a hair apart": ((1.02, 1.018, 1.016), {"A": -8, "B": 4, "C": 16}),
    # What went wrong before: B was pushed down to keep its distance from A, onto C's line.
    "two a hair apart and one a label's height below": (
        (1.02, 1.018, 0.985), {"A": -8, "B": 4, "C": 16}),
    "the upper two a hair apart, named last": ((0.9, 1.0502, 1.05), {"B": -8, "C": 4, "A": 16}),
}


@pytest.mark.parametrize("name", list(LABEL_PLACES))
def test_each_level_label_is_drawn_at_its_own_line(tmp_path, name):
    parts, places = LABEL_PLACES[name]
    brief = make_brief(seed=26)
    last = brief["facts"]["last"]["v"]
    levels = [level_at(brief, last * part, "Level " + "ABC"[i]) for i, part in enumerate(parts)]
    report = make_report(brief, levels=levels)
    root = good(run_page(tmp_path, brief, report))
    chart = root.one("section", "s4")
    # Each line by the price it is drawn at; the prices here are far enough apart for that.
    grid = [node for node in chart.find("text") if node.attrs.get("text-anchor") == "end"]
    (low, y_low), (high, y_high) = [(float(node.text()), percent(node.attrs["y"]))
                                    for node in (grid[0], grid[-1])]
    lines = {}
    for level in levels:
        y = y_low + (level["price"] - low) * (y_high - y_low) / (high - low)
        near = [line.attrs["y1"] for line in chart.find("line", "lv")
                if abs(percent(line.attrs["y1"]) - y) <= 0.011]
        assert len(near) == 1, (level["label"], near)
        lines[level["label"][-1]] = near[0]
    assert len(set(lines.values())) == len(levels), lines
    # A label, found by the name it shows, stands at the line of its own level and at no
    # other, however near the next one is.
    labels = {node.text()[-1]: node for node in chart.find("text", "lt")}
    assert sorted(labels) == sorted(lines)
    for letter, label in labels.items():
        assert label.attrs["y"] == lines[letter], (letter, label.attrs["y"], lines)
        assert label.text() == "%s %s Level %s" % (
            levels["ABC".index(letter)]["price_t"], DOT, letter)
    assert {letter: int(label.attrs["dy"]) for letter, label in labels.items()} == places
    # From the highest price down: above its line, on it, below it.
    order = [node.text()[-1] for node in chart.find("text", "lt")]
    assert order == sorted(order, key=lambda letter: -levels["ABC".index(letter)]["price"])
    assert [places[letter] for letter in order] == level_offsets(len(levels))
    check_drawings(root, report)
    check_geometry(brief, report, root)


@pytest.mark.parametrize("count", [0, 4, 5, 6, 9])
def test_a_hand_made_report_with_no_level_or_with_more_than_three_still_draws(tmp_path, count):
    # render.py writes one to three levels. A report made by hand may hold any number: the
    # page draws them all, the ones between the highest and the lowest on their lines.
    brief = make_brief(seed=27)
    last = brief["facts"]["last"]["v"]
    levels = [level_at(brief, last * (1.06 - 0.02 * i), "Level %d" % i, "bear" if i % 2 else "bull")
              for i in range(count)]
    report = make_report(brief, levels=levels)
    result = run_page(tmp_path, brief, report)
    root = good(result)
    check_drawings(root, report)
    check_geometry(brief, report, root)
    labels = root.one("section", "s4").find("text", "lt")
    assert [int(label.attrs["dy"]) for label in labels] == (
        [-8] + [4] * (count - 2) + [16] if count else [])
    assert len(level_rows(root)) == count


def test_the_200_day_line_starts_where_the_history_allows(tmp_path):
    # A one-year download has about 250 rows, and the 200-day average exists from row 200
    # on, so in a chart of the newest 120 rows its line starts part of the way across. That
    # is how the page is meant to draw it: nothing is made up for the rows before.
    brief = make_brief(seed=28, history=140)
    series = brief["series"]
    first = min(i for i, value in enumerate(series["sma200"]) if value is not None)
    assert first == 59 and series["sma200"][-1] is not None and None not in series["sma50"]
    report = make_report(brief)
    root = good(run_page(tmp_path, brief, report))
    check_geometry(brief, report, root)
    chart = root.one("section", "s4")
    line = [path for path in chart.find("path", "ln") if path.attrs["stroke"] == "#1baf7a"][0]
    points = line.attrs["d"][1:].split("L")
    assert len(points) == ROWS - first and points[0].startswith("%d," % first)
    assert points[-1].startswith("%d," % (ROWS - 1))
    assert "200d " + brief["facts"]["sma200"]["t"] in [n.text() for n in chart.find("text", "el")]


def spanning(low, high, seed=29):
    """A brief whose drawn prices span exactly ``low`` to ``high``: a close that climbs from
    the one to the other, no averages, and one level between them."""
    brief = make_brief(seed=seed)
    close = [low + (high - low) * i / (ROWS - 1) for i in range(ROWS)]
    close[0], close[-1] = low, high
    brief["series"].update(close=close, sma50=[None] * ROWS, sma200=[None] * ROWS)
    brief["facts"]["last"] = price_fact(high, "last close")
    return brief, make_report(brief, levels=[level_at(brief, (low + high) / 2, "Level A")])


AXES = [
    # lowest and highest drawn price, the step, the numbers on the axis
    (100.2, 104.6, 1, ["100", "101", "102", "103", "104", "105"]),
    (100.5, 109.5, 2, ["100", "102", "104", "106", "108", "110"]),
    # Steps of 2.5: with 1, 2, 5 and 10 alone this axis had the four lines of the next row.
    (100.5, 111.5, 2.5, ["100", "102.5", "105", "107.5", "110", "112.5"]),
    (100.5, 114.5, 5, ["100", "105", "110", "115"]),
    (100.5, 139.5, 10, ["100", "110", "120", "130", "140"]),
    (1000.0, 1120.0, 25, ["1000", "1025", "1050", "1075", "1100", "1125"]),
    (61000.0, 73000.0, 2500, ["60000", "62500", "65000", "67500", "70000", "72500", "75000"]),
    (0.0101, 0.0113, 0.00025,
     ["0.01", "0.01025", "0.0105", "0.01075", "0.011", "0.01125", "0.0115"]),
    (0.61, 0.73, 0.025, ["0.6", "0.625", "0.65", "0.675", "0.7", "0.725", "0.75"]),
]


@pytest.mark.parametrize("low, high, step, numbers", AXES, ids=[str(row[2]) for row in AXES])
def test_the_price_axis_steps_by_1_2_2_5_or_5_times_a_power_of_ten(tmp_path, low, high, step,
                                                                   numbers):
    brief, report = spanning(low, high)
    result = run_page(tmp_path, brief, report)
    root = good(result)
    chart = root.one("section", "s4")
    axis = [n.text() for n in chart.find("text") if n.attrs.get("text-anchor") == "end"]
    assert axis == numbers
    assert abs(axis_step(high - low) / step - 1) < 1e-9
    values = [float(label) for label in axis]
    assert all(abs(b - a - step) < 1e-9 * step for a, b in zip(values, values[1:]))
    # The axis starts and ends on a step, around the drawn prices and no further.
    assert values[0] <= low < values[1] and values[-2] < high <= values[-1]
    check_geometry(brief, report, root)
    # The room at the left grows with the longest number: a step of 2.5 adds a decimal.
    longest = max(len(label) for label in axis)
    assert re.fullmatch(r"padding:0 \d+px 22px %dpx" % int(12 + 6.4 * longest),
                        chart.one("div", "ch").attrs["style"])


def test_the_axis_step_is_of_that_family_for_any_series(tmp_path):
    seen = set()
    for seed, start in enumerate((0.0007, 0.03, 0.9, 4.0, 17.0, 150.0, 3600.0, 70000.0, 2.2e6),
                                 start=50):
        brief = make_brief(seed=seed, start=start)
        report = make_report(brief, levels=("high52", "low52"))
        root = good(run_page(tmp_path, brief, report, "seed%d" % seed))
        check_geometry(brief, report, root)
        axis = [float(n.text()) for n in root.one("section", "s4").find("text")
                if n.attrs.get("text-anchor") == "end"]
        assert 2 <= len(axis) <= 8
        step = axis[1] - axis[0]
        seen.add(round(step / 10 ** math.floor(math.log10(step) + 1e-9), 6))
    assert seen <= {1.0, 2.0, 2.5, 5.0} and len(seen) >= 2, seen


@pytest.mark.parametrize("start, sample", [(0.0000083, "0.00000"), (0.0042, "0.00"),
                                           (0.61, "0."), (245000.0, "")])
def test_very_small_and_very_large_prices(tmp_path, start, sample):
    brief = make_brief(seed=9, start=start)
    last = brief["facts"]["last"]
    assert last["t"].startswith(sample) and (start < 1) == (last["v"] < 1)
    assert start < 1 or last["v"] >= 100000
    report = make_report(brief, "SELL", 0.1, levels=("prev_close", "high52", "low52"))
    result = run_page(tmp_path, brief, report)
    root = good(result)
    check_drawings(root, report)
    check_geometry(brief, report, root)
    assert root.one("header").one("div", "t").kids[2].kids[1].text() == last["t"] + " USD"
    chart = root.one("section", "s4")
    # Axis numbers are plain decimals on round steps, never exponent notation.
    axis = [n.text() for n in chart.find("text") if n.attrs.get("text-anchor") == "end"]
    assert 2 <= len(axis) <= 8
    assert all(re.fullmatch(r"\d+(\.\d+)?", label) for label in axis), axis
    values = [float(label) for label in axis]
    drawn = [v for key in ("close", "sma50", "sma200") for v in brief["series"][key]]
    drawn += [lv["price"] for lv in report["levels"]]
    assert values[0] <= min(drawn) and values[-1] >= max(drawn)
    steps = {round(b - a, 12) for a, b in zip(values, values[1:])}
    assert len(steps) == 1 and steps.pop() > 0
    assert "e+" not in result.page and "e-0" not in result.page
    # No zeros hang at the end of an axis number.
    assert not [label for label in axis if "." in label and label.endswith("0")], axis


# --- hostile strings ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def hostile(tmp_path_factory):
    brief, report = hostile_pair()
    return brief, report, run_page(tmp_path_factory.mktemp("hostile"), brief, report)


def test_hostile_strings_never_reach_the_page_unescaped(hostile):
    brief, report, result = hostile
    assert result.code == 0 and result.stderr == []
    root = assert_safe(result.page)
    shown = set(strings(brief)) | set(strings(report))
    assert len(shown) > 150
    for value in shown - {"brief.v1", "report.v1"}:
        assert HOSTILE in value
        assert value not in result.page
    for needle in ("<b>X</b>", "onerror=alert(2)>", 'href="http', '"q"', "'s'", "</title>",
                   "<!--", "& ", chr(233)):
        assert needle not in result.page.split("</style>", 1)[1], needle
    # Only the page's own markup is there: one style sheet, and no element the inputs named.
    assert len(root.find("style")) == len(root.find("title")) == len(root.find("main")) == 1
    assert root.find("script") == root.find("img") == root.find("a") == []


def test_hostile_strings_are_shown_as_text(hostile):
    brief, report, result = hostile
    root = Tree(result.page).root
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in result.page
    assert "&#233;&#26085;&#26412;" in result.page
    assert root.one("title").text() == brief["symbol"] + " desk view"
    assert root.one("h1").text() == brief["symbol"] + " " + brief["name"]
    assert root.one("p", "why").text() == "Why" + report["why"]
    assert level_rows(root) == expected_rows(report)
    for side in ("bull", "bear"):
        rows = [(li.one("span").text(), li.one("b").text())
                for li in root.one("section", side).one("ul", "ev").kids]
        assert rows == [(e["label"], e["text"]) for e in report[side]["evidence"]]
    assert [li.text() for li in root.one("aside").find("li")] == [
        note["text"] for note in brief["notes"]]
    assert root.one("p", "tr").text() == report["test_record"]
    assert root.one("p", "x").kids[0] == report["disclaimer"]
    assert report["skipped"][1] in root.one("section", "rc").text()
    titles = [li.kids[0] for li in root.one("section", "hd").one("ul").kids]
    assert titles == [h["title"] for h in brief["headlines"]]


def test_hostile_text_in_the_drawings_is_cut_short(hostile):
    _, report, result = hostile
    root = Tree(result.page).root
    # Text inside a drawing cannot wrap, so the page cuts it before it escapes it.
    for node in root.find("text"):
        assert len(node.text()) <= 64, node.text()
    ladder = [node.text() for node in root.one("svg", "ld").find("text")[:-1]]
    assert ladder == ["%s %s %s" % (" ".join(lv["price_t"][:12].split()), DOT,
                                    " ".join(lv["gap_t"][:8].split()))
                      for lv in report["levels"]]


def test_ok_line_stays_one_ascii_line(hostile):
    brief, report, result = hostile
    assert result.stdout.count("\n") == 1 and result.stdout.startswith("PAGE ok TEST <b>X</b>")
    assert "\\xe9\\u65e5\\u672c" in result.stdout
    assert result.stdout.rstrip("\n").endswith(" bytes=%d" % len(result.raw))


def test_control_characters_and_newlines_are_made_plain(tmp_path):
    brief = make_brief(seed=10, symbol="A\nB\tC")
    brief["name"] = "Line one\nline two\x00\x07 end"
    report = make_report(brief)
    report["why"] = "First line.\r\nSecond line."
    result = run_page(tmp_path, brief, report)
    root = good(result)
    assert_safe(result.page)
    assert result.stdout.startswith("PAGE ok A B C ")
    assert root.one("h1").text() == "A B C Line one line two end"


# --- inputs the page refuses ----------------------------------------------------------------

def test_a_report_for_another_symbol_is_refused(tmp_path):
    brief = make_brief(seed=11)
    report = make_report(brief)
    report["symbol"] = "OTHER"
    refused(run_page(tmp_path, brief, report), E_PAIR, 2)


def test_a_report_for_other_data_is_refused(tmp_path):
    brief = make_brief(seed=11)
    report = make_report(make_brief(seed=12))
    assert report["symbol"] == brief["symbol"] and report["data_id"] != brief["data_id"]
    refused(run_page(tmp_path, brief, report), E_PAIR, 2)


def test_a_report_without_a_data_id_is_refused(tmp_path):
    brief = make_brief(seed=11)
    report = make_report(brief)
    del report["data_id"]
    refused(run_page(tmp_path, brief, report), E_INPUT, 2)


@pytest.mark.parametrize("which, schema", [
    ("brief", "brief.v2"), ("brief", "bundle.v1"), ("brief", None), ("report", "report.v2"),
    ("report", "brief.v1"), ("report", 1)])
def test_a_wrong_schema_is_refused(tmp_path, which, schema):
    brief = make_brief(seed=11)
    pair = {"brief": brief, "report": make_report(brief)}
    pair[which]["schema"] = schema
    refused(run_page(tmp_path, pair["brief"], pair["report"]), E_INPUT, 2)


def test_swapped_files_are_refused(tmp_path):
    brief = make_brief(seed=11)
    refused(run_page(tmp_path, make_report(brief), brief), E_INPUT, 2)


@pytest.mark.parametrize("name", ["empty", "not json", "cut short", "a list", "a number",
                                  "bad utf-8", "nested too deep"])
@pytest.mark.parametrize("which", ["brief", "report"])
def test_broken_json_is_refused(tmp_path, which, name):
    brief = make_brief(seed=11)
    pair = {"brief": brief, "report": make_report(brief)}
    text = json.dumps(pair[which]).encode()
    pair[which] = {"empty": b"", "not json": b"{not json", "cut short": text[:len(text) // 2],
                   "a list": b"[1, 2]", "a number": b"7",
                   "bad utf-8": text[:40] + b"\xff\xfe\xfd" + text[40:],
                   "nested too deep": b"[" * 100000 + b"]" * 100000}[name]
    refused(run_page(tmp_path, pair["brief"], pair["report"]), E_INPUT, 2)


def test_an_incomplete_brief_is_refused(tmp_path):
    for key in ("facts", "series", "session", "flags", "notes", "headlines", "unit", "as_of"):
        brief = make_brief(seed=11)
        report = make_report(brief)
        del brief[key]
        refused(run_page(tmp_path, brief, report, "no-" + key), E_INPUT, 2)


def test_a_missing_file_is_a_file_error(tmp_path):
    brief = make_brief(seed=11)
    report = make_report(brief)
    folder = tmp_path / "missing"
    folder.mkdir()
    good_brief = write(folder / "brief.json", brief)
    good_report = write(folder / "report.json", report)
    out = folder / "page.html"
    for args in ([folder / "nope.json", good_report, out], [good_brief, folder / "nope.json", out],
                 [folder, good_report, out], [good_brief, good_report, folder / "no" / "p.html"],
                 [good_brief, good_report, folder]):
        done = subprocess.run([sys.executable, "-I", "-B", str(PAGE)] + [str(a) for a in args],
                              capture_output=True, timeout=60)
        refused(Run(done, out), E_FILE, 4)


@pytest.mark.parametrize("count", [0, 1, 2, 4])
def test_a_wrong_argument_count_is_a_usage_error(tmp_path, count):
    args = [str(tmp_path / ("file%d" % i)) for i in range(count)]
    done = subprocess.run([sys.executable, "-I", "-B", str(PAGE)] + args, capture_output=True,
                          timeout=60)
    refused(Run(done, tmp_path / "file2"), E_USAGE, 4)


def test_version(tmp_path):
    done = subprocess.run([sys.executable, "-I", "-B", str(PAGE), "--version"],
                          capture_output=True, timeout=60)
    result = Run(done, tmp_path / "none")
    assert (result.code, result.stdout, result.stderr) == (
        0, "trading-agents-desk page.py 2.0.0\n", [])


@pytest.mark.parametrize("brief_as, report_as", [("utf-8-sig", "utf-16"), ("utf-16", "utf-8"),
                                                 ("utf-16-le", "utf-16-be")])
def test_byte_order_marks_are_accepted(tmp_path, normal, brief_as, report_as):
    brief, report, first, _ = normal
    folder = tmp_path / "bom"
    folder.mkdir()

    def encoded(document, encoding):
        text = json.dumps(document, ensure_ascii=False)
        mark = {"utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff"}.get(encoding, b"")
        return mark + text.encode(encoding)

    args = [sys.executable, "-I", "-B", str(PAGE),
            str(write(folder / "b.json", encoded(brief, brief_as))),
            str(write(folder / "r.json", encoded(report, report_as))), str(folder / "p.html")]
    result = Run(subprocess.run(args, capture_output=True, timeout=60), folder / "p.html")
    assert (result.code, result.raw) == (0, first.raw)


def test_text_outside_ascii_in_an_input_is_shown_as_it_is(tmp_path):
    # The page's two inputs are files brief.py and render.py wrote, and those are ASCII.
    # render.py has already made a Unicode minus and a no-break space plain in the draft, so
    # page.py changes no character: one that is typed into a file by hand is shown as it is.
    brief = make_brief(seed=13)
    report = make_report(brief)
    report["why"] = "Down %s5.5%% in a week,%sand still falling." % (chr(8722), chr(160))
    folder = tmp_path / "plain"
    folder.mkdir()
    raw = json.dumps(report, ensure_ascii=False).encode("utf-8")
    args = [sys.executable, "-I", "-B", str(PAGE), str(write(folder / "b.json", brief)),
            str(write(folder / "r.json", raw)), str(folder / "p.html")]
    result = Run(subprocess.run(args, capture_output=True, timeout=60), folder / "p.html")
    assert good(result).one("p", "why").raw() == "<b>Why</b>".replace("<b>", "").replace(
        "</b>", "") + report["why"]
    assert "&#8722;5.5%" in result.page and "&#160;and" in result.page


# --- awkward values: a page or one STOP line, never a traceback -----------------------------

def load_script():
    """The script's functions without running it as a program and without a bytecode file."""
    space = {"__name__": "page_under_test", "__file__": str(PAGE)}
    exec(compile(SOURCE.decode("ascii"), str(PAGE), "exec"), space)
    return space


def paths(value, prefix=()):
    """Paths to every value of a document, the first two items of each list only."""
    yield prefix
    if isinstance(value, dict):
        for key in value:
            yield from paths(value[key], prefix + (key,))
    elif isinstance(value, list):
        for i in range(min(len(value), 2)):
            yield from paths(value[i], prefix + (i,))


def test_awkward_values_give_a_page_or_one_stop_line(tmp_path, capsys):
    run = load_script()["run"]
    brief = make_brief(seed=14, flags=["rows_dropped"])
    report = make_report(brief, "BUY", 0.25, levels=("prev_close", "sma50", "low52"))
    keep = {"last", "chg1", "rsi14", "bb_pctb", "sma50", "sma200", "ret1", "pe_trailing"}
    brief["facts"] = {k: v for k, v in brief["facts"].items() if k in keep}
    # A short series keeps the many runs quick; its length does not matter to the page.
    brief["series"] = {k: v[-30:] for k, v in brief["series"].items()}
    junk = [None, -1, 1e308, True, "", "q", [], {"v": "q"}, float("nan")]
    files = {"brief": tmp_path / "b.json", "report": tmp_path / "r.json"}
    out = tmp_path / "p.html"
    pages = stops = 0
    for which, base in (("brief", brief), ("report", report)):
        other = "report" if which == "brief" else "brief"
        write(files[other], report if which == "brief" else brief)
        for path in paths(base):
            if not path or path[:2] in (("series", "close"), ("series", "sma50")) and len(path) > 2:
                continue
            for value in junk + ["<gone>"]:
                changed = copy.deepcopy(base)
                target = changed
                for key in path[:-1]:
                    target = target[key]
                if value == "<gone>":
                    del target[path[-1]]
                else:
                    target[path[-1]] = value
                write(files[which], changed)
                if out.exists():
                    out.unlink()
                code, line = run([str(files["brief"]), str(files["report"]), str(out)])
                where = "%s %s <- %r" % (which, ".".join(map(str, path)), value)
                assert code in (0, 2), where
                if code == 2:
                    assert line in (E_INPUT, E_PAIR) and not out.exists(), where
                    stops += 1
                else:
                    assert line.startswith("PAGE ok "), where
                    page = out.read_text(encoding="ascii")
                    assert Tree(page).problems == [], where
                    assert "<script" not in page.lower(), where
                    pages += 1
    capsys.readouterr()
    # Both outcomes happen, and there are enough cases for the test to mean something.
    assert pages > 200 and stops > 200, (pages, stops)


# --- integrity -------------------------------------------------------------------------------

def contract_hash(data):
    """CONTRACTS.md section 1: SHA-256 over the bytes with LF line endings and trailing
    whitespace stripped, each line that is exactly SELF = "<12 hex>" read as the one word
    SELF; the first 12 hex digits. Every other line counts as it is, whatever it starts
    with."""
    text = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    kept = [re.sub(rb'^SELF = "[0-9a-f]{12}"$', b"SELF", line.rstrip())
            for line in text.split(b"\n")]
    return hashlib.sha256(b"\n".join(kept).rstrip()).hexdigest()[:12]


def stamped_source():
    stamp = b'SELF = "%s"' % contract_hash(SOURCE).encode()
    out, count = re.subn(rb'(?m)^SELF = "[0-9a-f]{12}"$', stamp, SOURCE)
    assert count == 1
    return out


def run_copy(tmp_path, name, data, normal):
    folder = tmp_path / name
    folder.mkdir()
    script = folder / "page.py"
    script.write_bytes(data)
    brief, report, _, _ = normal
    done = subprocess.run(
        [sys.executable, "-I", "-B", str(script), str(write(folder / "b.json", brief)),
         str(write(folder / "r.json", report)), str(folder / "p.html")],
        capture_output=True, timeout=60)
    return done.returncode, done.stdout.decode("ascii"), done.stderr.decode("ascii"), folder


def test_a_stamped_copy_runs_silently(tmp_path, normal):
    stamped = stamped_source()
    for name, data in (("lf", stamped), ("crlf", stamped.replace(b"\n", b"\r\n")),
                       ("spaces", stamped.replace(b"\n\n\n", b"  \n\n \n", 5))):
        code, out, err, folder = run_copy(tmp_path, name, data, normal)
        assert (code, err) == (0, ""), name
        assert out == normal[2].stdout and (folder / "p.html").read_bytes() == normal[2].raw
    done = subprocess.run([sys.executable, "-I", "-B", str(tmp_path / "lf" / "page.py"),
                           "--version"], capture_output=True, timeout=60)
    assert (done.returncode, done.stdout, done.stderr) == (
        0, b"trading-agents-desk page.py 2.0.0\n", b"")


def test_an_unstamped_copy_says_so_and_runs(tmp_path, normal):
    blank = re.sub(rb'(?m)^SELF = "[0-9a-f]{12}"$', b'SELF = "000000000000"', SOURCE)
    code, out, err, _ = run_copy(tmp_path, "blank", blank, normal)
    assert (code, err, out) == (0, WARN + "\n", normal[2].stdout)


def test_an_altered_copy_is_refused(tmp_path, normal):
    stamped = stamped_source()
    stamp = re.search(rb'(?m)^SELF = "[0-9a-f]{12}"\n', stamped).group()
    version, after = b'VERSION = "2.0.0"\n', b'SWING = ("oversold", "overbought")\n'
    assert stamped.count(version + stamp + after) == 1
    rng = random.Random(15)
    letters = [m.start() for m in re.finditer(rb"[a-z](?=[a-z])", stamped)
               if b"\nCSS = " in stamped[:m.start()] and b'}"""' in stamped[m.start():]]
    changes = {"a word": stamped.replace(b"Receipts", b"Receipt5", 1),
               "a threshold": stamped.replace(b"(0, 100, 30, 70,", b"(0, 100, 30, 71,", 1),
               "a wrong stamp": re.sub(rb'(?m)^SELF = "[0-9a-f]{12}"$',
                                       b'SELF = "0123456789ab"', SOURCE),
               "an added line": stamped + b"# one more line\n",
               # Only the stamp line itself is read as the word SELF, not any line that
               # starts like it: code on such a line used to pass.
               "code on a line that starts like the stamp": stamped.replace(
                   b"\nSELF = ", b'\nSELF = print("added")\nSELF = ', 1),
               "a second stamp line with a comment": stamped.replace(
                   b"\nSELF = ", b'\nSELF = "000000000000"  # x\nSELF = ', 1),
               # The stamp line keeps its place in the hash. It used to be left out of it,
               # so a second stamp line above the real one, the same line twice and the
               # stamp line in another place all hashed as the untouched copy and drew.
               "a second stamp line above the stamp": stamped.replace(
                   stamp, b'SELF = "0123456789ab"\n' + stamp),
               "the stamp line twice": stamped.replace(stamp, stamp + stamp),
               "the stamp line again at the end": stamped + stamp,
               "the stamp line one line up": stamped.replace(version + stamp, stamp + version),
               "the stamp line one line down": stamped.replace(stamp + after, after + stamp),
               "a byte-order mark": b"\xef\xbb\xbf" + stamped,
               "a no-break space in a text": stamped.replace(
                   b"Trading Agents Desk", "Trading\u00a0Agents Desk".encode("utf-8"), 1)}
    for at in rng.sample(letters, 6):
        swap = b"z" if stamped[at:at + 1] != b"z" else b"y"
        changes["style sheet byte %d" % at] = stamped[:at] + swap + stamped[at + 1:]
    assert all(data != stamped for data in changes.values())
    for name, data in changes.items():
        code, out, err, folder = run_copy(tmp_path, name.replace(" ", "-"), data, normal)
        # A self-check cannot stop a line that Python runs before the check; it refuses
        # the copy all the same, where it used to print "PAGE ok".
        printed = "added\n" if name.startswith("code on a line") else ""
        assert (code, out, err) == (3, printed, E_SELF + "\n"), name
        assert not (folder / "p.html").exists(), name


def test_a_copy_without_its_stamp_line_draws_nothing(tmp_path, normal):
    # A missing stamp line changes the hash, and the preflight names such a copy (see
    # test_portable.py). Run on its own, the copy has no stamp to compare its hash with:
    # what is held here is that it draws no page and ends with one line, not a traceback.
    stamped = stamped_source()
    gone = re.sub(rb'(?m)^SELF = "[0-9a-f]{12}"\n', b"", stamped)
    assert len(gone) == len(stamped) - 22 and contract_hash(gone) != contract_hash(stamped)
    code, out, err, folder = run_copy(tmp_path, "no-stamp", gone, normal)
    assert code != 0 and out == "", (code, out)
    assert len(err.splitlines()) == 1 and "Traceback" not in err, err
    assert not (folder / "p.html").exists()


# --- the source ------------------------------------------------------------------------------

def test_source_is_small_plain_ascii():
    assert len(SOURCE) <= 20000, "page.py is %d bytes; the limit is 20,000" % len(SOURCE)
    assert all(byte == 10 or 32 <= byte <= 126 for byte in SOURCE), "ASCII with LF endings only"
    assert SOURCE.endswith(b"\n") and not SOURCE.endswith(b"\n\n")
    lines = SOURCE.decode("ascii").split("\n")
    assert not [n for n, line in enumerate(lines, 1) if line != line.rstrip()]
    assert len(re.findall(rb'(?m)^SELF = "[0-9a-f]{12}"$', SOURCE)) == 1
    assert len(re.findall(rb'(?m)^VERSION = "2\.0\.0"$', SOURCE)) == 1


def test_source_is_readable():
    text = SOURCE.decode("ascii")
    lines = text.split("\n")
    long = [n for n, line in enumerate(lines, 1) if len(line) > 100]
    assert not long, "lines over 100 characters: %s" % long
    tree = ast.parse(text, feature_version=(3, 9))
    statements = [node for node in ast.walk(tree) if isinstance(node, ast.stmt)]
    assert len(statements) > 150
    # One statement on a line: every statement starts its own line, so there is no
    # "a = 1; b = 2" and no "if x: return y".
    shared = [node.lineno for node in statements
              if lines[node.lineno - 1][:node.col_offset].strip()]
    assert not shared, "more than one statement on lines %s" % shared
    starts = [node.lineno for node in statements]
    assert len(starts) == len(set(starts))
    # Names say what they hold: no one-letter function, and few one-letter variables.
    functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    assert functions and all(len(name) > 1 for name in functions)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)}
    assert {name for name in names if len(name) == 1} <= set("fikmstvxy")
    # Comments and docstrings are there, and the code is not packed.
    code = [line for line in lines[:lines.index('CSS = """*{box-sizing:border-box;margin:0}')]
            + lines[lines.index("def esc(s, cut=None):"):] if line.strip()]
    assert sum(line.lstrip().startswith("#") for line in code) >= 20
    assert sum(len(line) for line in code) / len(code) < 70


def test_source_follows_the_contract_rules():
    text = SOURCE.decode("ascii")
    tree = ast.parse(text, feature_version=(3, 9))
    imports, opens = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module)
        elif isinstance(node, ast.JoinedStr):
            pytest.fail("an f-string on line %d" % node.lineno)
        elif isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            assert name not in {"eval", "exec", "compile", "__import__", "os", "subprocess",
                                "socket", "environ", "getenv", "system", "popen", "urllib"}, name
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "open":
            opens.append(node.args[1].value)
    assert imports <= ALLOWED_IMPORTS, imports - ALLOWED_IMPORTS
    # It reads itself and the two inputs, and writes the one output path it was given.
    assert sorted(opens) == ["rb", "rb", "w"]
    assert "://" not in text and "http" not in text.lower()
    assert "<script" not in text.lower() and "javascript" not in text.lower()


def test_style_sheet_stays_inside_the_page():
    text = SOURCE.decode("ascii")
    sheet = text.split('CSS = """', 1)[1].split('"""', 1)[0]
    assert 3000 < len(sheet) < 7000
    for needle in ("url(", "@import", "http", "//", "expression(", "@font-face", "<", "\\"):
        assert needle not in sheet, needle
    assert sheet.count("{") == sheet.count("}")
    # The phone layout is there: one column, and the level names leave the chart.
    assert "@media(max-width:" in sheet and ".wide{display:none}" in sheet
