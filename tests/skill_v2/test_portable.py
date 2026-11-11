"""The whole desk flow on the four shipped scripts, with the standard library only.

Runs under pytest and as a plain script:

    python tests/skill_v2/test_portable.py

It needs no package, no pandas, no network and no pytest fixture, so it runs on any Python from
3.9 up on Linux, macOS and Windows. The scripts are run the way the skill runs them: as
separate processes, from a temporary folder they were copied into. The one exception is the
download: get_data.py is loaded from that folder and its single HTTP function is replaced by a
stand-in that answers in the data source's shapes, so nothing here opens a connection.

Every number is generated here. What a report and a page must say is worked out below from
the brief's facts and the draft, with plain arithmetic and plain string formatting, not with
the scripts' own code: the Markdown report is rebuilt whole, and every text on the page that
holds a digit is compared with the brief or with the resolved report.
"""
from __future__ import annotations

import datetime
import hashlib
import html
import importlib.util
import io
import json
import random
import re
import subprocess
import sys
import tempfile
import traceback
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / "skill" / "trading-agents-desk" / "scripts"
NAMES = ("get_data.py", "brief.py", "render.py", "page.py")
# The preflight wants brief.py and render.py; the download and the page are optional steps.
OPTIONAL = ("get_data.py", "page.py")
COLS = ("open", "high", "low", "close", "volume")
DAY = 86400
END = (datetime.date(2026, 11, 10) - datetime.date(1970, 1, 1)).days  # a Tuesday
HOST = "https://query1.finance.yahoo.com"

# The two fixed texts of every report, word for word.
TEST_RECORD = ("This desk design returned -0.9% against +15.7% for buy-and-hold in its only "
               "test: a reduced, price-only version on one stock for one quarter.")
DISCLAIMER = ("Research output, not investment advice. This is a simulated analysis on public "
              "data; nothing here places orders. Not affiliated with or endorsed by Yahoo; use "
              "of the data is subject to Yahoo's terms.")
LOST = "Research simulation, not advice. This desk design lost to buy-and-hold in its only test."
RECEIPT = (
    "Figures: %d inserted by code from the data. Quantity words: %d, not checked.",
    "Data %s to %s, %s. Report %s. Attempt %d. Script 2.0.0.",
    "This covers sourcing and arithmetic, not judgment.",
)
WON = {"BULLISH": "The bull case won", "BEARISH": "The bear case won",
       "NEUTRAL": "Neither side won"}
ROLES = {"single": "The roles ran one at a time in one context.",
         "subagents": "Each role ran as its own sub-agent.",
         "mixed": "Some roles ran as sub-agents, the rest in one context."}
ROLES_SINGLE = ROLES["single"]
REPORT_FIELDS = [
    "schema", "symbol", "data_id", "report_id", "attempt", "figures", "quantity_words", "script",
    "argument", "desk", "why", "bull", "bear", "levels", "risk", "roles", "skipped",
    "headlines_used", "receipt", "test_record", "disclaimer"]
# What the page holds for itself (contract section 6): the meter scales and the tile order.
SCALES = {"rsi14": (0, 100, 30, 70), "stoch_k": (0, 100, 20, 80), "mfi14": (0, 100, 20, 80),
          "bb_pctb": (-0.5, 1.5, 0, 1), "adx14": (0, 100, 20, 25)}
TILES = ("pe_trailing pe_forward peg mcap margin_ttm rev_q_yoy ni_q_yoy debt_equity rev_ttm "
         "ni_ttm rev_q ni_q debt equity").split()
DOT = " &#183; "  # between parts of a Markdown line
MID = chr(183)    # the same dot once a page is read back
REF = re.compile(r"\{\{(.*?)\}\}")
QUANTITY = re.compile(r"\b(half|halves|double|twice|thirds?|quarters?)\b", re.I)
# One printable character that Python source can hold almost anywhere.
ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789+-*/=<>()[]{}.,:\"'%"


# --- generated input -------------------------------------------------------------------

def iso(day):
    return (datetime.date(1970, 1, 1) + datetime.timedelta(days=day)).isoformat()


def make_bundle(n=260, symbol="TEST", seed=7):
    """A bundle.v1 document in the shape get_data.py writes, for a finished session.

    The numbers come from an integer generator, so they are the same on every platform.
    """
    x, prev, rows = seed, 150.0, []
    for _ in range(n):
        x = (x * 1103515245 + 12345) % 2147483648
        o = round(prev * (1 + (x // 7 % 801 - 400) / 80000.0), 2)
        c = round(o * (1 + (x % 2001 - 1000) / 40000.0), 2)
        h = round(max(o, c) * (1 + (x // 11 % 100) / 10000.0), 2)
        low = round(min(o, c) * (1 - (x // 13 % 100) / 10000.0), 2)
        rows.append((o, h, low, c, 1000000 + x % 9000000))
        prev = c
    days, d = [], END
    while len(days) < n:
        if (d + 3) % 7 < 5:  # day 0 was a Thursday
            days.append(d)
        d -= 1
    days.reverse()
    offset = -18000
    stamps = [d * DAY + 34200 - offset for d in days]
    last_close = days[-1] * DAY + 57600 - offset
    fetched = last_close + 7 * 3600
    nxt = days[-1] + 1
    period = {"timezone": "TST", "gmtoffset": offset}
    meta = {
        "currency": "USD", "symbol": symbol, "exchangeName": "TST", "instrumentType": "EQUITY",
        "gmtoffset": offset, "timezone": "TST", "exchangeTimezoneName": "Test/Zone",
        "regularMarketPrice": rows[-1][3], "regularMarketTime": last_close + 1,
        "longName": "Test Instruments Inc.", "shortName": "Test Instr", "priceHint": 2,
        "dataGranularity": "1d",
        "currentTradingPeriod": {
            "pre": dict(period, start=nxt * DAY + 30600 - offset, end=nxt * DAY + 34200 - offset),
            "regular": dict(period, start=nxt * DAY + 34200 - offset,
                            end=nxt * DAY + 57600 - offset),
            "post": dict(period, start=nxt * DAY + 57600 - offset,
                         end=nxt * DAY + 61200 - offset)},
    }
    chart = {"meta": meta, "timestamp": stamps}
    for i, key in enumerate(COLS):
        chart[key] = [r[i] for r in rows]
    news = [
        {"title": "Generated headline <b>bold</b> | piped *stars*", "publisher": "Example Wire",
         "time": fetched - 3600, "tickers": [symbol]},
        {"title": "Generated headline two about " + symbol, "publisher": "Sample Daily",
         "time": fetched - 7200, "tickers": [symbol]},
    ]
    names = ("trailingPeRatio trailingForwardPeRatio trailingMarketCap trailingPegRatio "
             "trailingTotalRevenue trailingNetIncome quarterlyTotalRevenue quarterlyNetIncome "
             "quarterlyTotalDebt quarterlyStockholdersEquity").split()
    statements = dict((k, []) for k in names)
    statements["trailingPeRatio"] = [{"asOf": iso(END - 1), "value": 21.5}]
    # A money figure comes with its currency, which need not be the currency of the price.
    statements["trailingMarketCap"] = [
        {"asOf": iso(END - 1), "value": 250000000000, "currency": "EUR"}]
    return {"schema": "bundle.v1", "symbol": symbol, "source": "yahoo-chart",
            "fetched_at": fetched, "chart": chart, "news": news, "statements": statements,
            "errors": {"news": None, "statements": None}}


# The row shapes seen live from the data source, rebuilt on the generated bundle.

def shape_no_close(bundle):
    """London from the close until the next open: the newest row has no close yet, and the
    source's last regular price is dated on that row's day and lies inside its range."""
    ch = bundle["chart"]
    price = round((ch["high"][-1] + ch["low"][-1]) / 2, 2)
    ch["meta"]["regularMarketPrice"] = price
    ch["close"][-1] = None
    return price


def shape_behind(bundle):
    """London minutes after the next open: the finished session's row is empty, and so is
    the row of the session that has just begun. Returns the source's last regular price."""
    ch = bundle["chart"]
    for key in COLS:
        ch[key][-2] = ch[key][-1] = None
    opened = ch["timestamp"][-1]
    ch["meta"]["currentTradingPeriod"]["regular"].update(start=opened, end=opened + 23400)
    bundle["fetched_at"] = opened + 720
    price = round(ch["close"][-3] * 0.98, 2)
    ch["meta"].update(regularMarketPrice=price, regularMarketTime=opened - 2400)
    return price


def shape_same_day(bundle):
    """Sydney after the close: two rows for the newest date. One is stamped at the session
    start and has no close; one is stamped at the last trade and has it."""
    ch = bundle["chart"]
    ch["timestamp"].append(ch["timestamp"][-1] + 20000)
    for key in COLS:
        ch[key].append(ch[key][-1])
    ch["close"][-2] = None


def make_draft(levels=("high52", "low52"), headline=True):
    """A draft that names facts and types no figure. It reads the same in any session."""
    lines = [
        "[verdict] BEARISH",
        "[why] The price is {{sma10_gap|dir}} its short average, with {{rsi14|label}}. "
        "The price stood at {{last}} on {{as_of}}.",
        "[bull.claim] Momentum is stretched, which often comes before a bounce.",
        "[bull.evidence] rsi14, sma10_gap, ret5",
        "[bull.also] Volume on the newest session was {{volume.0}}.",
        "[bull.weak] A stretched reading can stay stretched while a trend runs.",
        "[bear.claim] The trend is down and has strength.",
        "[bear.evidence] sma50_gap, atr14_pct, macd_hist",
        "[bear.also] " + ("The newest headline, {{h1}}, does not explain the move."
                          if headline else "No headline came with the data."),
        "[bear.weak] About half of the selling may be done.",
        "[levels]",
        "%s | bull | on a close above" % levels[0],
        "%s | bear | on a close below" % levels[1],
        "[trader] SELL 0.2",
        "[manager] approved SELL 0.6",
        "[risk] The safe analyst pointed to {{chg1|label}} and asked for less. "
        "The fund manager agreed.",
        "[roles] single",
        "[skipped] none",
    ]
    return "\n".join(lines) + "\n"


# Figures the draft above asks for: four in [why], three in each evidence list, one in
# [bull.also], a price and a distance in each level row, one in [risk]. A headline is not one.
DRAFT_FIGURES = 4 + 3 + 3 + 1 + 2 * 2 + 1
DRAFT_QUANTITY_WORDS = 1  # "half"


# --- the figures, worked out here ------------------------------------------------------

def pct(a, b):
    return 100 * (a / b - 1)


def expected_texts(bundle):
    """The display text of some facts, from the bundle's rows with plain arithmetic.

    Valid for the bundles used here: no empty rows, prices of 10 or more, 252 rows or more.
    """
    ch = bundle["chart"]
    close, volume = ch["close"], ch["volume"]
    assert len(close) >= 252 and min(close) >= 10 and None not in close
    up = down = None
    for before, after in zip(close, close[1:]):
        gain, loss = max(after - before, 0.0), max(before - after, 0.0)
        if up is None:
            up, down = gain, loss
        else:
            up, down = up + (gain - up) / 14, down + (loss - down) / 14
    sma10 = sum(close[-10:]) / 10
    assert 1e6 <= volume[-1] < 99.995e6
    offset = ch["meta"]["gmtoffset"]
    return {
        "last": "%.2f" % close[-1],
        "as_of": iso((ch["timestamp"][-1] + offset) // DAY),
        "prev_close": "%.2f" % close[-2],
        "chg1": "%+.2f%%" % pct(close[-1], close[-2]),
        "ret5": "%+.2f%%" % pct(close[-1], close[-6]),
        "sma10": "%.2f" % sma10,
        "sma10_gap": "%+.2f%%" % pct(close[-1], sma10),
        "rsi14": "%.2f" % (100 * up / (up + down)),
        "high52": "%.2f" % max(close[-252:]),
        "low52": "%.2f" % min(close[-252:]),
        "volume.0": "%.2fM" % (volume[-1] / 1e6),
    }


def md(text):
    """Text as the report shows it: HTML-escaped, Markdown characters backslash-escaped."""
    return re.sub(r"([\\`*_\[\]|~#])", r"\\\1", html.escape(str(text), quote=False))


def quoted(headline):
    """A headline as the report quotes it. A double quote in the title is shown as a single
    one and the publisher loses its round brackets, so the quotation ends where it should."""
    return '"%s" (%s, %s)' % (headline["title"].replace('"', "'"),
                              re.sub("[()]", "", headline["publisher"]), headline["date"])


def dated(fact):
    """A fact's text as it is shown beside its label: a statement figure with its date."""
    return fact["t"] + (" (as of %s)" % fact["as_of"] if "as_of" in fact else "")


def printed(brief, name, form=""):
    """What a reference must print: the brief's own text for that fact, in the form asked."""
    for headline in brief["headlines"]:
        if headline["id"] == name:
            return quoted(headline)
    fact = brief["facts"][name]
    text = fact["t"]
    if form == "label":
        return "%s %s" % (fact["label"], dated(fact))
    if form == "dir":
        words = ("above", "below") if name.endswith("_gap") else ("up", "down")
        return "%s %s" % (text.lstrip("+-"), words[text.startswith("-")])
    if form == "gap":
        # The distance from the last price, to one decimal, never "-0.0%".
        return "%+.1f%%" % (round(pct(fact["v"], brief["facts"]["last"]["v"]), 1) + 0.0)
    assert form == "", form
    return text


def resolve(text, brief):
    def one(match):
        name, _, form = [x.strip() for x in match.group(1).partition("|")]
        return printed(brief, name, form)
    return REF.sub(one, text)


def slots(draft):
    """The draft's slots as lists of lines; the level rows are the lines of ``levels``."""
    out, name = {}, None
    for line in draft.splitlines():
        m = re.match(r"\[([a-z.]+)\] ?(.*)$", line)
        if m:
            name = m.group(1)
            out[name] = [m.group(2)] if m.group(2) else []
        elif line.strip():
            out[name].append(line)
    return out


def expected_report(brief, draft):
    """The resolved report a clean draft must give on a brief: every text and figure of it
    that comes from the draft and the data. The ids and the counts are checked apart."""
    s, facts = slots(draft), brief["facts"]

    def prose(name):
        return resolve(" ".join(s[name]), brief)

    def case(side):
        names = [x.strip() for x in s[side + ".evidence"][0].split(",")]
        return {"claim": prose(side + ".claim"),
                "evidence": [{"label": facts[k]["label"], "text": dated(facts[k])}
                             for k in names],
                "also": prose(side + ".also"), "weak": prose(side + ".weak")}

    levels = []
    for row in s["levels"]:
        name, side, words = [x.strip() for x in row.split("|", 2)]
        levels.append({"label": facts[name]["label"], "price": facts[name]["v"],
                       "price_t": facts[name]["t"], "gap_t": printed(brief, name, "gap"),
                       "side": side, "words": resolve(words, brief)})

    # The decision rules: the manager approves, cuts or rejects, and never adds or enlarges.
    proposed, proposed_size = s["trader"][0].split()
    word, final, final_size = s["manager"][0].split()
    action, size = "HOLD", 0.0
    if word == "approved" and final == proposed and final in ("BUY", "SELL"):
        # Each size is the one that is printed, to two decimals.
        size = min(round(min(1.0, max(0.0, float(x))), 2) for x in (final_size, proposed_size))
        action = final if size else "HOLD"
    trade = "No trade"
    if action != "HOLD":
        trade = "%s %s" % ("Small" if size < 0.34 else "Medium" if size < 0.67 else "Large",
                           "long" if action == "BUY" else "short")
    verdict = s["verdict"][0]
    skipped = [x.strip() for x in s["skipped"][0].split(",") if x.strip().lower() != "none"]
    return {"argument": {"code": verdict, "text": WON[verdict]},
            "desk": {"action": action, "size": size, "text": trade},
            "why": prose("why"), "bull": case("bull"), "bear": case("bear"), "levels": levels,
            "risk": prose("risk"), "roles": ROLES[s["roles"][0]], "skipped": skipped}


def count_figures(draft):
    """Figures a draft asks for: each fact reference in prose, each evidence name, and a
    price and a distance for each level row. A headline reference is not a figure."""
    s, count = slots(draft), 0
    for name, lines in s.items():
        if name.endswith(".evidence"):
            count += len(lines[0].split(","))
        elif name == "levels":
            count += 2 * len(lines)
        count += len([x for x in REF.findall(" ".join(lines))
                      if not re.fullmatch(r"h\d+", x.partition("|")[0].strip())])
    return count


def table(head, rows):
    rows = [head, ["---"] * len(head)] + rows
    return "\n".join("| %s |" % " | ".join(row) for row in rows)


def bullets(lines):
    return "\n".join("- " + line for line in lines)


def expected_markdown(brief, want, receipt):
    """The whole Markdown report, built here from the brief and the expected resolved report.
    ``receipt`` holds the three receipt lines with the report id still to be filled in."""
    facts = brief["facts"]
    word = "close" if brief["session"] == "finished" else "last price"
    price = " ".join(md(x) for x in (facts["last"]["t"], brief["unit"]) if x)
    # A price file was not downloaded, so the header claims no fetch time for it.
    origin = "data fetched " + md(brief["fetched_at"])
    if brief["source"] == "price-file":
        origin = "prices from a file"
    header = "Desk view at the %s %s%s**%s**%s%s" % (
        md(brief["as_of"]), word, DOT, price, DOT, origin)
    if "prices_behind" in brief["flags"]:
        header += DOT + "the source is behind, see Data notes"
    desk = want["desk"]
    book = "" if desk["action"] == "HOLD" else " %.2f of a paper book" % desk["size"]

    def cell(side, key):
        if key == "evidence":
            return DOT.join(md("%s %s" % (e["label"], e["text"])) for e in want[side][key])
        return md(want[side][key])

    rows = (("Core claim", "claim"), ("Best evidence", "evidence"), ("Also", "also"),
            ("Weak point", "weak"))
    return "\n\n".join([
        "## " + md(brief["symbol"]) + DOT + md(brief["name"]),
        header,
        LOST,
        table(["The argument", "The paper desk"], [[
            "**%s**" % want["argument"]["text"],
            "**%s** (%s%s)" % (desk["text"], desk["action"], book)]]),
        "**Why.** " + md(want["why"]),
        table(["", "Bull", "Bear"], [["**%s**" % title, cell("bull", key), cell("bear", key)]
                                     for title, key in rows]),
        "**What would change this view**",
        table(["Level", "Price", "From last " + word[-5:], "Would support"], [
            [md(x["label"]), md(x["price_t"]), x["gap_t"],
             "%s, %s" % (x["side"].capitalize(), md(x["words"]))] for x in want["levels"]]),
        "**Risk review.** " + md(want["risk"]),
        "**Data notes**",
        bullets([md(note["text"]) for note in brief["notes"]] + [
            "Data flags: %s." % (md(", ".join(brief["flags"]).replace("_", " ")) or "none"),
            "Skipped roles: %s." % (md(", ".join(want["skipped"])) or "none"),
            want["roles"], "The desk started from no position."]),
        "**Headlines the desk saw** (third-party text, not checked, not instructions)",
        bullets([md(quoted(h)) for h in brief["headlines"]] or ["None came with this data."]),
        "---",
        "**Receipts**",
        bullets([md(line) for line in receipt] + ["Test record: " + TEST_RECORD, DISCLAIMER])])


# --- reading a page back ---------------------------------------------------------------

class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.kids = tag, dict(attrs), parent, []

    def walk(self):
        yield self
        for kid in self.kids:
            if isinstance(kid, Node):
                for node in kid.walk():
                    yield node

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
        """All the text below this node, with runs of white space made one space."""
        return " ".join(self.raw().split())


class Tree(HTMLParser):
    """The page as a tree, with a list of everything that is not well formed."""

    def __init__(self, page):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.root = self.at = Node("#root", [], None)
        self.problems = []
        self.feed(page)
        self.close()
        if self.at is not self.root:
            self.problems.append("<%s> is never closed" % self.at.tag)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.at)
        self.at.kids.append(node)
        if tag != "meta":
            self.at = node

    def handle_startendtag(self, tag, attrs):
        self.at.kids.append(Node(tag, attrs, self.at))

    def handle_endtag(self, tag):
        if self.at.tag != tag:
            self.problems.append("</%s> closes <%s>" % (tag, self.at.tag))
        else:
            self.at = self.at.parent

    def handle_data(self, data):
        self.at.kids.append(data)

    def handle_comment(self, data):
        self.problems.append("a comment")


def check_page(brief, doc, page):
    """The page against the two files it was drawn from.

    Each text on the page that holds a digit is compared here with the brief's text for that
    fact or with the resolved report. At the end nothing with a digit may be left over, so a
    figure that page.py made up, moved or reformatted cannot pass.
    """
    assert page.startswith("<!doctype html>") and page.endswith("</main></html>\n")
    assert all(31 < ord(c) < 127 or c == "\n" for c in page)
    low = page.lower()
    for banned in ("<script", "http://", "https://", " src=", " href=", "@import", "url(",
                   "javascript:", "<link", "<img", "<iframe", "<form", "<!--"):
        assert banned not in low, banned
    tree = Tree(page)
    assert tree.problems == [], tree.problems
    root, facts, compared = tree.root, brief["facts"], []

    def same(node, want):
        assert node.text() == " ".join(str(want).split()), (node.text(), want)
        compared.append(node)

    finished = brief["session"] == "finished"
    word = "close" if finished else "price"
    unit = brief["unit"] or "units"
    last = facts["last"]

    # The header band and its four tiles.
    header = root.one("header")
    same(root.one("title"), brief["symbol"] + " desk view")
    same(header.one("h1"), "%s %s" % (brief["symbol"], brief["name"]))
    view = "at the %s close" % brief["as_of"] if finished else "on %s, session %s" % (
        brief["as_of"], brief["session"])
    if "prices_behind" in brief["flags"]:
        view += " (the data source is behind)"
    origin = "Data fetched " + brief["fetched_at"]
    if brief["source"] == "price-file":
        origin = "Prices from a file"
    same(header.one("p"), "Desk view %s %s Last %s %s %s %s %s" % (
        view, MID, word, last["t"], unit, MID, origin))
    tiles = [
        ("The argument", doc["argument"]["text"], "Bull against Bear"),
        ("The paper desk", doc["desk"]["text"],
         "%g%% of a paper book" % (100 * doc["desk"]["size"])),
        ("Last " + word, "%s %s" % (last["t"], unit), facts["chg1"]["t"] + " on the day"),
        ("The check", "%d figures" % doc["figures"], "inserted by code")]
    shown = header.one("div", "t").kids
    assert len(shown) == len(tiles)
    for tile, texts in zip(shown, tiles):
        assert len(tile.kids) == 3
        for part, text in zip(tile.kids, texts):
            same(part, text)
    same(root.one("p", "why"), "Why" + doc["why"])

    # The notice strip: there when the brief has flags, with every note of the brief.
    notes = [note["text"] for note in brief["notes"]]
    strips = root.find("aside")
    assert len(strips) == (1 if brief["flags"] else 0)
    for strip in strips:
        items = strip.find("li")
        wanted = notes or [flag.replace("_", " ") for flag in brief["flags"]]
        assert len(items) == len(wanted)
        for item, text in zip(items, wanted):
            same(item, text)

    # The chart: its caption, the label at each line's end, the levels and the price axis.
    chart, series = root.one("section", "s4"), brief["series"]
    same(chart.one("small"),
         "%d daily %ss in %s, %s to %s. 50d and 200d are the 50-day and 200-day averages. "
         "Dashed lines are the report's levels." % (
             len(series["date"]), word, unit, series["date"][0], series["date"][-1]))
    assert series["date"][-1] == brief["as_of"]
    ends = ["%s %s" % (word.capitalize(), last["t"][:13])]
    ends += ["%s %s" % (short, facts[key]["t"][:13])
             for key, short in (("sma50", "50d"), ("sma200", "200d"))
             if series[key][-1] is not None]
    assert sorted(node.text() for node in chart.find("text", "el")) == sorted(ends)
    tags = ["%s %s %s" % (lv["price_t"][:13], MID, lv["label"][:44]) for lv in doc["levels"]]
    assert sorted(node.text() for node in chart.find("text", "lt")) == sorted(tags)
    compared.extend(chart.find("text", "el") + chart.find("text", "lt"))
    plotted = [lv["price"] for lv in doc["levels"]]
    for key in ("close", "sma50", "sma200"):
        plotted += [v for v in series[key] if v is not None]
    ticks = []
    for node in chart.find("text"):
        if "class" not in node.attrs and re.search(r"\d", node.text()):
            assert re.fullmatch(r"\d+(\.\d+)?", node.text()), node.text()
            ticks.append(float(node.text()))
            compared.append(node)
    # A level's label starts where its dashed line starts: a dash before the price would
    # read as a minus sign.
    assert set(node.attrs["x"] for node in chart.find("text", "lt")) <= {"0"}
    steps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert len(ticks) >= 2 and min(steps) > 0 and max(steps) - min(steps) < 1e-6 * max(steps)
    assert ticks[0] <= min(plotted) and max(plotted) <= ticks[-1]

    # The meters: each fact on its real scale, with the words for the number as it is
    # shown. Then the returns, named in days as the report names them.
    card = root.one("section", "in")
    keys = [key for key in SCALES if key in facts]
    meters = card.find("div", "m")
    assert len(meters) == len(keys)
    for meter, key in zip(meters, keys):
        low_end, high_end, under, over = SCALES[key]
        fact = facts[key]
        same(meter.one("span"), fact["label"])
        same(meter.one("b"), fact["t"])
        low_end, high_end = min(low_end, fact["v"]), max(high_end, fact["v"])
        state = meter.one("small").text()
        if round(fact["v"], 2) < under:
            assert state.startswith("below %g %s " % (under, MID)), state
        elif round(fact["v"], 2) > over:
            assert state.startswith("above %g %s " % (over, MID)), state
        else:
            assert state == "between %g and %g" % (under, over), state
        marks = meter.find("text")
        assert [node.text() for node in marks] == [
            "%g" % round(x, 2) for x in (low_end, under, over, high_end)]
        spot = (fact["v"] - low_end) * 100 / (high_end - low_end)
        assert meter.one("circle").attrs["cx"] == "%.1f%%" % spot
        compared.extend(marks + [meter.one("small")])
    bars = [n for n in (1, 5, 21, 63) if "ret%d" % n in facts]
    rows = card.one("dl").kids
    assert len(rows) == len(bars)
    for row, n in zip(rows, bars):
        same(row.one("dt"), "%d day%s" % (n, "s" * (n > 1)))
        same(row.one("dd"), facts["ret%d" % n]["t"])

    # Bull and bear, each with its claim, its evidence pairs and its two other points.
    for side in ("bull", "bear"):
        card, part = root.one("section", side), doc[side]
        same(card.one("h3"), part["claim"])
        rows = card.one("ul", "ev").kids
        assert len(rows) == len(part["evidence"])
        for row, pair in zip(rows, part["evidence"]):
            same(row.one("span"), pair["label"])
            same(row.one("b"), pair["text"])
        also, weak = card.one("dl").kids
        same(also.one("dd"), part["also"])
        same(weak.one("dd"), part["weak"])

    # The levels: the table, and the ladder with the last price on it.
    card = root.one("section", "cv")
    rows = card.one("table").kids[1:]
    assert len(rows) == len(doc["levels"])
    for row, lv in zip(rows, doc["levels"]):
        same(row.kids[0].one("b"), lv["label"])
        same(row.kids[0].one("small"), "Would support the %s case, %s" % (lv["side"], lv["words"]))
        same(row.kids[1], lv["price_t"])
        same(row.kids[2], lv["gap_t"])
    ladder = card.one("svg", "ld").find("text")
    assert [node.text() for node in ladder] == [
        "%s %s %s" % (lv["price_t"][:12], MID, lv["gap_t"][:8]) for lv in doc["levels"]] + [
        "Last %s %s" % (word, last["t"][:13])]
    compared.extend(ladder)

    # The risk review, sentence by sentence.
    items = root.one("section", "rk").find("li")
    assert " ".join(item.text() for item in items) == doc["risk"]
    compared.extend(items)

    # Statement figures, each with its own date, and the headlines the desk saw.
    keys = [key for key in TILES if key in facts][:8]
    cards = root.find("section", "fd")
    assert len(cards) == (1 if keys else 0)
    for card in cards:
        shown = card.one("div", "fu").kids
        assert len(shown) == len(keys)
        for tile, key in zip(shown, keys):
            same(tile.kids[0], facts[key]["label"])
            same(tile.kids[1], facts[key]["t"])
            same(tile.kids[2], "as of " + facts[key]["as_of"])
    cards = root.find("section", "hd")
    assert len(cards) == (1 if brief["headlines"] else 0)
    for card in cards:
        items = card.one("ul").kids
        assert len(items) == len(brief["headlines"])
        for item, headline in zip(items, brief["headlines"]):
            assert item.kids[0] == headline["title"], item.kids[0]
            same(item.one("small"), "%s %s %s" % (
                headline["publisher"], MID, headline["date"]))
            compared.append(item)

    # The receipts: the test record, the three receipt lines, the data notes, the ids.
    card = root.one("section", "rc")
    same(card.one("p", "tr"), doc["test_record"])
    columns = dict((row.one("dt").text(), row.one("dd")) for row in card.one("dl").kids)
    assert list(columns) == ["The check", "The data", "How it ran"]
    lines = columns["The check"].find("li")
    assert [item.text() for item in lines] == doc["receipt"]
    data = columns["The data"].find("li")
    assert [item.text() for item in data] == [
        "%s %s %s %s as of %s, session %s %s prices in %s" % (
            brief["type"], MID, brief["source"], MID, brief["as_of"], brief["session"], MID,
            unit)] + (notes or ["No data notes."])
    ran = doc["roles"]
    if doc["skipped"]:
        ran += " Skipped: %s." % ", ".join(doc["skipped"])
    same(columns["How it ran"], ran + " The desk started from no position.")
    foot = card.one("p", "x")
    assert foot.kids[0] == doc["disclaimer"]
    same(foot.one("small"), "Report %s %s Data %s" % (doc["report_id"], MID, brief["data_id"]))
    compared.extend(lines + data)

    # Nothing with a digit is left that was not compared above.
    done = set(id(node) for node in compared)
    for node in root.walk():
        chain, up = [], node
        while up is not None:
            chain.append(id(up))
            up = up.parent
        for kid in node.kids:
            if isinstance(kid, str) and re.search(r"\d", kid) and node.tag != "style":
                assert done.intersection(chain), "a figure the test did not compare: %r" % kid
    return root


# --- running the scripts ---------------------------------------------------------------

class Done:
    def __init__(self, p):
        self.rc = p.returncode
        # Windows writes CRLF to a text stream; the content is what is compared.
        self.out = p.stdout.decode("ascii").replace("\r\n", "\n")
        self.err = p.stderr.decode("ascii", "replace").replace("\r\n", "\n")
        self.lines, self.err_lines = self.out.splitlines(), self.err.splitlines()


def run(script, *args):
    return Done(subprocess.run([sys.executable, str(script)] + [str(a) for a in args],
                               capture_output=True, timeout=120))


def copy_scripts(folder, crlf=False, names=NAMES):
    """Put the scripts into ``folder`` as the skill's setup step does."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        data = (SCRIPTS / name).read_bytes().replace(b"\r\n", b"\n")
        (folder / name).write_bytes(data.replace(b"\n", b"\r\n") if crlf else data)
    return folder


def read(path):
    return path.read_bytes().decode("ascii")


def load(path, name):
    """Load a script as a module by its path, leaving no cache folder beside it."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = keep
    return module


def http_date(epoch):
    """An HTTP Date header value, with fixed English names."""
    t = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=epoch)
    days = "Mon Tue Wed Thu Fri Sat Sun".split()
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return "%s, %02d %s %04d %02d:%02d:%02d GMT" % (
        days[t.weekday()], t.day, months[t.month - 1], t.year, t.hour, t.minute, t.second)


def source_answers(bundle):
    """The three answers of the data source that would give this bundle, in its own shapes:
    (status, headers, body) for the chart, the search and the statements request."""
    ch = bundle["chart"]
    chart = {"chart": {"error": None, "result": [{
        "meta": ch["meta"], "timestamp": ch["timestamp"],
        "indicators": {"quote": [dict((k, ch[k]) for k in COLS)]}}]}}
    search = {"news": [{"title": n["title"], "publisher": n["publisher"],
                        "providerPublishTime": n["time"], "relatedTickers": n["tickers"]}
                       for n in bundle["news"]]}
    series = {"timeseries": {"error": None, "result": [
        {"meta": {"symbol": [bundle["symbol"]], "type": [key]},
         key: [dict({"asOfDate": e["asOf"], "reportedValue": {"raw": e["value"]}},
                    **({"currencyCode": e["currency"]} if "currency" in e else {}))
               for e in rows]}
        for key, rows in bundle["statements"].items()]}}
    # The source's clock is the HTTP Date header of the chart answer.
    headers = {"content-type": "application/json", "date": http_date(bundle["fetched_at"])}
    answers = [(200, headers, json.dumps(doc).encode("ascii")) for doc in (chart, search, series)]
    if bundle["errors"].get("statements") == "HTTP 404":
        answers[2] = (404, headers, b"")
    return answers


def download(folder, bundle):
    """Step 1 of the skill without a network: get_data.py from ``folder`` runs as shipped,
    except that its one HTTP function answers from ``bundle``. Returns the DATA line."""
    script = load(folder / "get_data.py", "get_data_in_" + hashlib.sha256(
        str(folder).encode("utf-8")).hexdigest()[:8])
    chart, search, series = source_answers(bundle)
    asked = []

    def http_get(url):
        asked.append(url)
        assert url.startswith(HOST + "/"), url
        if "/v8/finance/chart/" in url:
            return chart
        return search if "/v1/finance/search" in url else series

    script.http_get = http_get
    out, keep = io.StringIO(), sys.stdout
    sys.stdout = out
    try:
        code = script.main([bundle["symbol"], str(folder / "bundle.json")])
    finally:
        sys.stdout = keep
    assert code == 0 and len(asked) == 3, (code, asked)
    # The bundle on disk is the source's answer and nothing else.
    assert json.loads(read(folder / "bundle.json")) == bundle
    return out.getvalue().strip()


def desk_run(folder, bundle, draft, attempt=1, fetch=False):
    """Steps 1, 2, 5 and 6 of the skill in ``folder``. Returns the runs of brief.py, render.py
    and page.py; a step that was not reached is None.

    ``fetch`` runs get_data.py with a stand-in for the network; otherwise the bundle is
    written to the file directly, as when the user supplies the data.
    """
    if fetch:
        line = download(folder, bundle)
        assert line.startswith("DATA ok %s bars=%d " % (
            bundle["symbol"], len(bundle["chart"]["timestamp"]))), line
    else:
        (folder / "bundle.json").write_bytes(json.dumps(bundle).encode("ascii") + b"\n")
    (folder / "draft.txt").write_bytes(draft.encode("ascii"))
    made = run(folder / "brief.py", folder / "bundle.json", folder / "brief.json")
    if made.rc != 0:
        return made, None, None
    shown = run(folder / "render.py", folder / "brief.json", folder / "draft.txt",
                folder / "report.md", folder / "report.json", "--attempt", attempt)
    if shown.rc != 0:
        return made, shown, None
    drawn = run(folder / "page.py", folder / "brief.json", folder / "report.json",
                folder / "report.html")
    return made, shown, drawn


def check_report(folder, made, shown, drawn, attempt=1):
    """Everything a finished run must show, from the files it left in ``folder``.

    The resolved report must be the one the draft and the brief give, the Markdown report
    must be that report word for word, and the page must show the same. Returns the brief,
    the Markdown report and the resolved report.
    """
    assert made.rc == 0 and made.err == "", made.err
    assert shown is not None and shown.rc == 0 and shown.err == "", (shown.out, shown.err)
    assert drawn is not None and drawn.rc == 0 and drawn.err == "", (drawn.out, drawn.err)
    brief = json.loads(read(folder / "brief.json"))
    draft = read(folder / "draft.txt")
    report = read(folder / "report.md")
    doc = json.loads(read(folder / "report.json"))
    page = read(folder / "report.html")
    facts = brief["facts"]

    # The fact sheet ends with the BRIEF ok line, which names the data.
    assert made.lines[-1] == "BRIEF ok %s %s facts=%d data_id=%s" % (
        brief["symbol"], brief["as_of"], len(facts), brief["data_id"])
    assert re.fullmatch("[0-9a-f]{8}", brief["data_id"])
    # The fields that left the brief in contract rev 3 stay out of it.
    assert not set(brief) & {"bars", "scales", "disclaimer", "test_record"}

    # The resolved report: the contract's fields in the contract's order, every text and
    # figure the one this draft must give on this brief.
    assert list(doc) == REPORT_FIELDS
    want = expected_report(brief, draft)
    for key, value in want.items():
        assert doc[key] == value, (key, doc[key], value)
    figures, words = count_figures(draft), len(QUANTITY.findall(REF.sub(" ", draft)))
    assert (doc["schema"], doc["symbol"], doc["data_id"], doc["script"]) == (
        "report.v1", brief["symbol"], brief["data_id"], "2.0.0")
    assert (doc["attempt"], doc["figures"], doc["quantity_words"]) == (attempt, figures, words)
    assert (doc["test_record"], doc["disclaimer"]) == (TEST_RECORD, DISCLAIMER)
    assert all(re.fullmatch(r"h\d+", x) for x in doc["headlines_used"])
    assert read(folder / "report.json") == json.dumps(doc) + "\n"

    # The Markdown report, rebuilt here whole. The report id is the hash of the report with
    # the id itself blanked, so it is known once the rest is.
    receipt = [RECEIPT[0] % (figures, words),
               RECEIPT[1] % (brief["data_id"], brief["as_of"], brief["session"], "0" * 8,
                             attempt),
               RECEIPT[2]]
    blank = expected_markdown(brief, want, receipt)
    rid = hashlib.sha256(blank.encode("ascii")).hexdigest()[:8]
    receipt[1] = receipt[1].replace("Report 00000000.", "Report %s." % rid)
    assert report == expected_markdown(brief, want, receipt) + "\n"
    assert (doc["report_id"], doc["receipt"]) == (rid, receipt)
    # It was printed as saved and nothing follows it: the receipt stands once, in the report.
    assert shown.out == report and shown.out.count("Figures: ") == 1

    # The page: drawn from the brief and the resolved report, and nothing else.
    assert drawn.lines == ["PAGE ok %s %s bytes=%d" % (
        brief["symbol"], rid, len((folder / "report.html").read_bytes()))]
    check_page(brief, doc, page)
    return brief, report, doc


def paragraph(report, start):
    i = report.index(start)
    return report[i:report.index("\n\n", i)]


def data_notes(report):
    """The lines of the Markdown report's Data notes block."""
    start = report.index("**Data notes**")
    block = report[start:report.index("**Headlines the desk saw**", start)]
    return [x[2:] for x in block.splitlines() if x.startswith("- ")]


# --- tests -----------------------------------------------------------------------------

def test_each_script_answers_with_its_version():
    for name in NAMES:
        done = run(SCRIPTS / name, "--version")
        assert done.rc == 0, done.err
        assert done.lines == ["trading-agents-desk %s 2.0.0" % name]
        # A WARN line here would mean the committed copy is not stamped.
        assert done.err == "", done.err


def preflight_line(names):
    return "PREFLIGHT ok 2.0.0 python %d.%d.%d checked %s" % (
        sys.version_info[:3] + (" ".join(sorted(names)),))


def test_preflight_passes_on_the_copied_scripts():
    with tempfile.TemporaryDirectory(prefix="desk ") as tmp:
        folder = copy_scripts(Path(tmp) / "desk run")
        done = run(folder / "brief.py", "--preflight", folder)
        assert done.rc == 0 and done.err == "", done.err
        assert done.lines == [preflight_line(NAMES)]


def test_the_preflight_checks_the_optional_scripts_only_when_they_are_there():
    with tempfile.TemporaryDirectory(prefix="desk ") as tmp:
        # A host with no network has no use for get_data.py, a run with no page none for
        # page.py. The line says which files were checked.
        for i, left_out in enumerate((("get_data.py",), ("page.py",), OPTIONAL)):
            names = [n for n in NAMES if n not in left_out]
            folder = copy_scripts(Path(tmp) / ("some %d" % i), names=names)
            done = run(folder / "brief.py", "--preflight", folder)
            assert (done.rc, done.err) == (0, ""), (left_out, done.err)
            assert done.lines == [preflight_line(names)]
        # render.py must be there.
        folder = copy_scripts(Path(tmp) / "no render", names=("brief.py", "page.py"))
        done = run(folder / "brief.py", "--preflight", folder)
        assert done.rc == 3 and done.out == "", done.out
        assert done.err_lines == [
            "ERROR E_SIBLING render.py is missing or was altered; load the skill files again"]
        # A file that is there but altered is always an error, optional or not.
        for name in NAMES[::-1]:
            if name != "brief.py":
                folder = copy_scripts(Path(tmp) / ("altered " + name[:-3]))
                (folder / name).write_bytes((folder / name).read_bytes() + b"# one more line\n")
                done = run(folder / "brief.py", "--preflight", folder)
                assert done.rc == 3 and done.out == "", (name, done.out)
                assert done.err_lines == ["ERROR E_SIBLING %s is missing or was altered; load "
                                          "the skill files again" % name]
        # An empty file is an altered file, not an absent one.
        folder = copy_scripts(Path(tmp) / "empty page")
        (folder / "page.py").write_bytes(b"")
        done = run(folder / "brief.py", "--preflight", folder)
        assert done.rc == 3 and done.err_lines[0].startswith("ERROR E_SIBLING page.py ")


def test_data_to_brief_to_report_to_page_holds_the_right_figures():
    bundle = make_bundle()
    want = expected_texts(bundle)
    with tempfile.TemporaryDirectory(prefix="desk ") as tmp:
        folder = copy_scripts(Path(tmp) / "desk run")
        made, shown, drawn = desk_run(folder, bundle, make_draft(), fetch=True)
        brief, report, doc = check_report(folder, made, shown, drawn)
        facts = brief["facts"]

        # The brief's figures are the ones plain arithmetic gives.
        for name, text in want.items():
            assert facts[name]["t"] == text, (name, facts[name]["t"], text)
        assert brief["symbol"] == "TEST" and brief["session"] == "finished"
        assert brief["as_of"] == want["as_of"] == "2026-11-10"
        assert brief["flags"] == [] and brief["source"] == "yahoo-chart"
        for name, text in want.items():
            assert any(line.startswith("%s | " % name) and line.endswith(" | " + text)
                       for line in made.lines), name

        # The report prints each referenced figure where the draft put it. check_report has
        # compared the whole text; these lines say it once more in plain sight.
        below = want["sma10_gap"].startswith("-")
        assert doc["why"] == (
            "The price is %s %s its short average, with RSI(14) %s. "
            "The price stood at %s on %s." % (
                want["sma10_gap"].lstrip("+-"), "below" if below else "above", want["rsi14"],
                want["last"], want["as_of"]))
        assert paragraph(report, "**Why.**") == "**Why.** " + doc["why"]
        assert "Desk view at the %s close%s**%s USD**" % (
            want["as_of"], DOT, want["last"]) in report
        assert doc["bull"]["evidence"] == [
            {"label": facts[k]["label"], "text": facts[k]["t"]}
            for k in ("rsi14", "sma10_gap", "ret5")]
        assert doc["bull"]["also"] == "Volume on the newest session was %s." % want["volume.0"]
        last = bundle["chart"]["close"][-1]
        for level, name in zip(doc["levels"], ("high52", "low52")):
            gap = pct(float(want[name]), last)
            assert abs(gap) > 0.1  # far from a rounding boundary at zero
            assert (level["price_t"], level["gap_t"]) == (want[name], "%+.1f%%" % gap)
        assert doc["risk"].startswith("The safe analyst pointed to %s %s and asked for less." % (
            facts["chg1"]["label"], want["chg1"]))
        assert DRAFT_FIGURES == doc["figures"] and DRAFT_QUANTITY_WORDS == doc["quantity_words"]

        # The decision rules: the manager cannot exceed the trader's size.
        assert "| **The bear case won** | **Small short** (SELL 0.20 of a paper book) |" in report
        assert doc["desk"] == {"action": "SELL", "size": 0.2, "text": "Small short"}

        # Headline text is third-party text: escaped in the Markdown and on the page, plain
        # in the resolved report, never markup.
        title = "Generated headline <b>bold</b> / piped *stars*"
        assert brief["headlines"][0]["title"] == title and doc["headlines_used"] == ["h1"]
        assert doc["bear"]["also"] == (
            'The newest headline, "%s" (Example Wire, %s), does not explain the move.' % (
                title, want["as_of"]))
        escaped = ('"Generated headline &lt;b&gt;bold&lt;/b&gt; / piped \\*stars\\*" '
                   "(Example Wire, %s)" % want["as_of"])
        assert "The newest headline, %s, does not explain the move." % escaped in report
        assert "- " + escaped in report
        page = read(folder / "report.html")
        assert "<b>bold" not in report and "<b>bold" not in page
        assert "Generated headline &lt;b&gt;bold&lt;/b&gt; / piped *stars*" in page
        assert "Data flags: none." in data_notes(report) and ROLES_SINGLE in data_notes(report)


def test_verify_accepts_the_report_and_refuses_a_changed_one():
    bundle = make_bundle()
    want = expected_texts(bundle)
    with tempfile.TemporaryDirectory(prefix="desk ") as tmp:
        folder = copy_scripts(Path(tmp) / "desk run")
        made, shown, drawn = desk_run(folder, bundle, make_draft())
        check_report(folder, made, shown, drawn)
        ok = run(folder / "render.py", "--verify", folder / "brief.json", folder / "report.md")
        assert (ok.rc, ok.lines, ok.err) == (0, ["VERIFY ok"], "")

        report = read(folder / "report.md")
        old = "with RSI(14) %s." % want["rsi14"]
        assert report.count(old) == 1
        for new in ("with RSI(14) 99.99.", "with RSI(14) %s and rising." % want["rsi14"]):
            (folder / "changed.md").write_bytes(report.replace(old, new).encode("ascii"))
            bad = run(folder / "render.py", "--verify", folder / "brief.json",
                      folder / "changed.md")
            assert bad.rc == 1 and bad.lines and all(x.startswith("DIFF ") for x in bad.lines)
        # A report from other data does not verify against this brief.
        other = make_bundle(seed=8)
        (folder / "bundle.json").write_bytes(json.dumps(other).encode("ascii"))
        assert run(folder / "brief.py", folder / "bundle.json", folder / "other.json").rc == 0
        bad = run(folder / "render.py", "--verify", folder / "other.json", folder / "report.md")
        assert bad.rc == 1 and any(x.startswith("DIFF data: ") for x in bad.lines), bad.lines


def test_the_page_is_drawn_only_from_a_report_made_from_the_same_brief():
    with tempfile.TemporaryDirectory(prefix="desk ") as tmp:
        folder = copy_scripts(Path(tmp) / "desk run")
        made, shown, drawn = desk_run(folder, make_bundle(), make_draft())
        check_report(folder, made, shown, drawn)
        # A brief from other data, and the report that was made before it.
        other = make_bundle(seed=8)
        (folder / "bundle.json").write_bytes(json.dumps(other).encode("ascii"))
        assert run(folder / "brief.py", folder / "bundle.json", folder / "other.json").rc == 0
        bad = run(folder / "page.py", folder / "other.json", folder / "report.json",
                  folder / "other.html")
        assert bad.rc == 2 and bad.out == "", bad.out
        assert bad.err_lines == ["STOP E_PAIR the report was not made from this brief; make the "
                                 "report again, then draw the page"]
        assert not (folder / "other.html").exists()
        # The two files in the wrong order, and the Markdown in place of the resolved report.
        for args in ((folder / "report.json", folder / "brief.json"),
                     (folder / "brief.json", folder / "report.md")):
            bad = run(folder / "page.py", args[0], args[1], folder / "other.html")
            assert bad.rc == 2 and bad.out == "", bad.out
            assert len(bad.err_lines) == 1 and bad.err_lines[0].startswith("STOP E_INPUT ")
            assert not (folder / "other.html").exists()
        # The report itself is untouched by any of this.
        ok = run(folder / "render.py", "--verify", folder / "brief.json", folder / "report.md")
        assert (ok.rc, ok.lines) == (0, ["VERIFY ok"])


def test_a_typed_figure_is_sent_back_and_the_fixed_draft_is_attempt_two():
    bundle = make_bundle()
    want = expected_texts(bundle)
    with tempfile.TemporaryDirectory(prefix="desk ") as tmp:
        folder = copy_scripts(Path(tmp) / "desk run")
        typed = make_draft().replace("{{last}}", want["last"])
        made, shown, drawn = desk_run(folder, bundle, typed)
        assert made.rc == 0 and shown.rc == 1 and shown.err == "" and drawn is None
        assert len(shown.lines) == 1 and shown.lines[0].startswith("FIX why: D_DIGIT "), shown.out
        # The hint names the fact that holds the typed value.
        assert re.search(r"\| try: .*\blast\b", shown.lines[0]), shown.lines[0]
        # No report of either kind, so there is nothing a page could be drawn from.
        for name in ("report.md", "report.json", "report.html"):
            assert not (folder / name).exists(), name
        made, shown, drawn = desk_run(folder, bundle, make_draft(), attempt=2)
        check_report(folder, made, shown, drawn, attempt=2)


def test_a_data_problem_is_one_stop_line_and_no_brief():
    with tempfile.TemporaryDirectory(prefix="desk ") as tmp:
        folder = copy_scripts(Path(tmp) / "desk run")
        made, shown, drawn = desk_run(folder, make_bundle(n=59), make_draft())
        assert shown is None and drawn is None and made.rc == 2 and made.out == ""
        assert len(made.err_lines) == 1 and made.err_lines[0].startswith("STOP E_FEW "), made.err
        assert not (folder / "brief.json").exists()
        # With no brief there is nothing to render and nothing to draw: one line each.
        done = run(folder / "render.py", folder / "brief.json", folder / "draft.txt",
                   folder / "report.md", folder / "report.json")
        assert done.rc != 0 and done.out == "" and len(done.err_lines) == 1, done.err
        done = run(folder / "page.py", folder / "brief.json", folder / "report.json",
                   folder / "report.html")
        assert done.rc != 0 and done.out == "" and len(done.err_lines) == 1, done.err
        assert not (folder / "report.md").exists() and not (folder / "report.html").exists()
        # get_data.py refuses a symbol it would not send, before any request.
        asked = run(folder / "get_data.py", "NOT A TICKER", folder / "unused.json")
        assert asked.rc == 2 and asked.out == ""
        assert len(asked.err_lines) == 1 and asked.err_lines[0].startswith("STOP E_SYMBOL ")
        assert not (folder / "unused.json").exists()


def test_the_row_shapes_seen_live_reach_the_report_and_the_page():
    with tempfile.TemporaryDirectory(prefix="desk ") as tmp:
        plain = copy_scripts(Path(tmp) / "plain")
        check_report(plain, *desk_run(plain, make_bundle(), make_draft(), fetch=True))

        # The newest close not published yet: filled from the last regular price, and said.
        bundle = make_bundle()
        price = shape_no_close(bundle)
        folder = copy_scripts(Path(tmp) / "no close")
        brief, report, doc = check_report(
            folder, *desk_run(folder, bundle, make_draft(), fetch=True))
        note = ("The source had no close for 2026-11-10 yet; it is filled from the exchange's "
                "last regular price.")
        assert brief["flags"] == ["close_filled"] and brief["session"] == "finished"
        assert brief["as_of"] == "2026-11-10" and brief["facts"]["last"]["t"] == "%.2f" % price
        assert [n["text"] for n in brief["notes"]][0] == note
        assert note in data_notes(report) and "Data flags: close filled." in data_notes(report)
        assert "<aside><b>Data notes</b>" in read(folder / "report.html")

        # The finished session's row empty: the view ends a day earlier, and the first lines
        # of the report and of the page say that the source is behind.
        bundle = make_bundle()
        price = shape_behind(bundle)
        folder = copy_scripts(Path(tmp) / "behind")
        brief, report, doc = check_report(
            folder, *desk_run(folder, bundle, make_draft(), fetch=True))
        note = ("The source has no complete prices for 2026-11-09 yet, so this view ends at "
                "2026-11-06. The source's last regular price is %.2f, dated 2026-11-10." % price)
        assert brief["flags"] == ["prices_behind"] and brief["session"] == "finished"
        assert brief["as_of"] == "2026-11-06" == brief["series"]["date"][-1]
        assert [n["text"] for n in brief["notes"]][0] == note
        assert brief["facts"]["last"]["t"] == "%.2f" % bundle["chart"]["close"][-3]
        header = report.split("\n\n")[1]
        assert header.startswith("Desk view at the 2026-11-06 close" + DOT)
        assert header.endswith(DOT + "the source is behind, see Data notes")
        assert note in data_notes(report)
        page = read(folder / "report.html")
        assert "Desk view at the 2026-11-06 close (the data source is behind)" in page
        assert "<aside><b>Data notes</b><ul class=list><li>" + html.escape(note) in page

        # Two rows for the newest date: one is kept and nothing is said, so every file is
        # the one the plain bundle gives.
        bundle = make_bundle()
        shape_same_day(bundle)
        folder = copy_scripts(Path(tmp) / "same day")
        check_report(folder, *desk_run(folder, bundle, make_draft(), fetch=True))
        for name in ("brief.json", "report.md", "report.json", "report.html"):
            assert (folder / name).read_bytes() == (plain / name).read_bytes(), name


def test_a_copy_with_windows_line_endings_gives_the_same_report():
    bundle = make_bundle()
    reports = []
    with tempfile.TemporaryDirectory(prefix="desk ") as tmp:
        for crlf in (False, True):
            folder = copy_scripts(Path(tmp) / ("crlf" if crlf else "lf"), crlf=crlf)
            for name in NAMES:
                assert (b"\r\n" in (folder / name).read_bytes()) == crlf
            pre = run(folder / "brief.py", "--preflight", folder)
            assert pre.rc == 0 and pre.err == "", pre.err
            assert pre.lines == [preflight_line(NAMES)]
            made, shown, drawn = desk_run(folder, bundle, make_draft(), fetch=True)
            check_report(folder, made, shown, drawn)
            reports.append(b"".join((folder / name).read_bytes() for name in (
                "bundle.json", "brief.json", "report.md", "report.json", "report.html")))
    assert reports[0] == reports[1] and b"\r" not in reports[0]


def mutations(text, count, seed):
    """``count`` single-character edits of ``text``: a replacement, a deletion or an insertion
    at a random non-blank character. Returns (description, new text) pairs.

    The value after ``SELF = `` is left alone. The contract takes the SELF line out of the
    hash, so an edit there is seen by the script's own run, not by a sibling's check.
    """
    stamp = re.search(r'(?m)^SELF = ("[0-9a-f]{12}")$', text)
    spots = [i for i, ch in enumerate(text)
             if not ch.isspace() and not stamp.start(1) <= i < stamp.end(1)]
    rng = random.Random(seed)
    out = []
    while len(out) < count:
        i, kind = rng.choice(spots), rng.choice(("replace", "delete", "insert"))
        new = rng.choice([c for c in ALPHABET if c != text[i]])
        if kind == "replace":
            changed = text[:i] + new + text[i + 1:]
        elif kind == "delete":
            changed = text[:i] + text[i + 1:]
        else:
            changed = text[:i] + new + text[i:]
        out.append(("%s at %d (%r)" % (kind, i, text[i]), changed))
    return out


def check_mutations_are_refused(count=32, seed=2026):
    """Every changed copy of each of the four scripts must fail the preflight."""
    with tempfile.TemporaryDirectory(prefix="desk ") as tmp:
        folder = copy_scripts(Path(tmp) / "desk run")
        assert run(folder / "brief.py", "--preflight", folder).rc == 0
        refused_by_script = 0
        for n, name in enumerate(NAMES):
            good = read(folder / name)
            for what, changed in mutations(good, count, seed + n):
                (folder / name).write_bytes(changed.encode("ascii"))
                done = run(folder / "brief.py", "--preflight", folder)
                where = "%s: %s" % (name, what)
                assert "PREFLIGHT ok" not in done.out and done.rc != 0, where
                if name != "brief.py":
                    # brief.py itself is intact, so the answer is always its own.
                    assert done.rc == 3 and done.out == "", where
                    assert len(done.err_lines) == 1, (where, done.err)
                    assert done.err_lines[0].startswith("ERROR E_SIBLING %s " % name), where
                elif done.rc == 3:
                    assert done.err_lines[-1].startswith("ERROR E_SELF "), (where, done.err)
                    refused_by_script += 1
                else:
                    # A change that breaks the grammar is refused by Python itself.
                    assert done.rc == 1 and "Error" in done.err, (where, done.rc, done.err)
            (folder / name).write_bytes(good.encode("ascii"))
        assert run(folder / "brief.py", "--preflight", folder).rc == 0
        return refused_by_script


def test_a_changed_copy_of_any_script_is_refused_by_the_preflight():
    # 32 edits of each of the four scripts. Some edits of brief.py must reach its own check.
    assert check_mutations_are_refused(32) >= 5


def test_scripts_written_out_by_a_tool_that_decodes_escapes_are_still_accepted():
    # Some agent hosts turn a backslash-u, backslash-U or backslash-N sequence into the
    # character itself while they write a file. A script that held one arrived altered, the
    # preflight refused it, and the run stopped at the setup step.
    def decoded(text):
        text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
        return re.sub(r"\\U([0-9a-fA-F]{8})", lambda m: chr(int(m.group(1), 16)), text)

    assert decoded("a\\u2212b") == "a" + chr(8722) + "b"
    with tempfile.TemporaryDirectory(prefix="desk ") as tmp:
        folder = Path(tmp) / "desk run"
        folder.mkdir()
        for name in NAMES:
            text = read(SCRIPTS / name)
            assert decoded(text) == text, name + " holds an escape a host could decode"
            assert "\\N{" not in text, name
            (folder / name).write_bytes(decoded(text).encode("utf-8"))
        done = run(folder / "brief.py", "--preflight", folder)
        assert (done.rc, done.err) == (0, ""), done.err
        assert done.lines == [preflight_line(NAMES)]


def test_a_copy_with_a_mark_in_front_or_an_extra_stamp_line_is_refused():
    # One rule for all four scripts: the hash is over the bytes, and only the stamp line
    # itself is left out. A byte-order mark used to pass in one script and fail in two, and
    # code on a second line that began like the stamp passed the preflight in all four.
    changes = (
        ("a byte-order mark", lambda data: b"\xef\xbb\xbf" + data),
        ("code on a line that starts like the stamp", lambda data: data.replace(
            b"\nSELF = ", b'\nSELF = len("added code")\nSELF = ', 1)),
        ("a second stamp with a comment", lambda data: data.replace(
            b"\nSELF = ", b'\nSELF = "000000000000"  # x\nSELF = ', 1)),
        ("a no-break space for a space", lambda data: data.replace(
            b"Trading Agents Desk", b"Trading\xc2\xa0Agents Desk", 1) if b"Trading Agents Desk"
            in data else data.replace(b"one host", b"one\xc2\xa0host", 1)),
    )
    with tempfile.TemporaryDirectory(prefix="desk ") as tmp:
        folder = copy_scripts(Path(tmp) / "desk run")
        for name in NAMES:
            good = (folder / name).read_bytes()
            for what, change in changes:
                changed = change(good)
                assert changed != good, (name, what)
                (folder / name).write_bytes(changed)
                done = run(folder / "brief.py", "--preflight", folder)
                where = "%s: %s" % (name, what)
                assert done.rc == 3 and "PREFLIGHT ok" not in done.out, (where, done.out)
                code = "E_SELF" if name == "brief.py" else "E_SIBLING " + name
                assert done.err_lines[-1].startswith("ERROR " + code), (where, done.err)
                if name in ("render.py", "page.py"):
                    # The changed script refuses its own run in the same way.
                    own = run(folder / name, "--version")
                    assert own.rc == 3 and own.err_lines[-1].startswith("ERROR E_SELF "), where
            (folder / name).write_bytes(good)
        assert run(folder / "brief.py", "--preflight", folder).rc == 0


def main():
    failed = 0
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for name, test in tests:
        try:
            test()
            print("ok   " + name)
        except Exception:
            failed += 1
            traceback.print_exc()
            print("FAIL " + name)
    print("%d passed, %d failed on Python %s" % (
        len(tests) - failed, failed, sys.version.split()[0]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
