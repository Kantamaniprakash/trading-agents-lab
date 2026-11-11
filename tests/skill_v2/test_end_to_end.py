"""The four shipped scripts together, on generated data: get_data.py with a stand-in for the
network, brief.py, a draft that names facts through render.py, the saved report through
--verify, and the page from page.py. Also the row shapes seen live from the data source, the
preflight against changed copies, and the same run from the files inside the built archive.

No test uses the network or the clock. The bundles come from bundles.py (generated numbers in
the real payload shape). The flow helpers are shared with test_portable.py, which runs the same
steps with the standard library only. flow.check_report is the heart of it: it works out from
the brief and the draft what the resolved report, the Markdown report and the page must say,
and accepts nothing else.
"""
from __future__ import annotations

import importlib.util
import json
import re
import socket
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load(name, path):
    """Load a file as a module without leaving a __pycache__ folder beside it."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = before
    return mod


bundles = load("skill_v2_bundles_e2e", HERE / "bundles.py")
flow = load("skill_v2_portable_flow", HERE / "test_portable.py")
build_skill = load("build_skill_e2e", ROOT / "skill" / "build_skill.py")

TABLE_LEVELS = ("high.1", "low.1")  # price facts every brief holds
DOT = flow.DOT
OUTPUTS = ("brief.json", "report.md", "report.json", "report.html")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """get_data.py runs inside this process with its HTTP function replaced. A connection
    attempt would mean the replacement was missed, so it fails the test."""
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise OSError("tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    yield
    assert not attempts, "a test tried to open a network connection"


@pytest.fixture
def desk(tmp_path):
    """A folder holding the four scripts, as the skill's setup step leaves it."""
    return flow.copy_scripts(tmp_path / "desk run")


def draft_for(bundle, levels=TABLE_LEVELS):
    return flow.make_draft(levels=levels, headline=bool(bundle.get("news")))


def whole_run(folder, bundle, draft=None):
    """Download (stand-in network), brief, render, page and --verify. Returns what
    flow.check_report returns, plus the fact sheet lines."""
    made, shown, drawn = flow.desk_run(
        folder, bundle, draft_for(bundle) if draft is None else draft, fetch=True)
    brief, report, doc = flow.check_report(folder, made, shown, drawn)
    done = verify(folder)
    assert (done.rc, done.lines, done.err) == (0, ["VERIFY ok"], "")
    return brief, report, doc, made.lines


def verify(folder, report="report.md", brief="brief.json"):
    return flow.run(folder / "render.py", "--verify", folder / brief, folder / report)


def page_of(folder):
    return flow.Tree(flow.read(folder / "report.html")).root


def notice(folder):
    """The lines of the page's notice strip; empty when the page has none."""
    return [item.text() for strip in page_of(folder).find("aside") for item in strip.find("li")]


# --- the ordinary run ------------------------------------------------------------------

def test_a_download_becomes_a_brief_a_report_and_a_page_with_the_right_figures(desk):
    bundle = bundles.bundle()
    want = flow.expected_texts(bundle)
    brief, report, doc, sheet = whole_run(desk, bundle, flow.make_draft())
    facts = brief["facts"]

    # brief.py's figures are the ones plain arithmetic gives from the bundle's rows.
    for name, text in want.items():
        assert facts[name]["t"] == text, (name, facts[name]["t"], text)
    assert brief["as_of"] == "2026-11-10" and brief["session"] == "finished"
    assert brief["flags"] == [] and brief["notes"] == []

    # render.py printed each of them where the draft named it. check_report compared the
    # whole report with what the brief and the draft give; these are the same facts, with
    # the expected texts written out.
    direction = "below" if want["sma10_gap"].startswith("-") else "above"
    why = "The price is %s %s its short average, with RSI(14) %s. The price stood at %s on %s." % (
        want["sma10_gap"].lstrip("+-"), direction, want["rsi14"], want["last"], want["as_of"])
    assert doc["why"] == why and flow.paragraph(report, "**Why.**") == "**Why.** " + why
    assert "**%s USD**" % want["last"] in report
    assert "Volume on the newest session was %s." % want["volume.0"] in report
    last = bundle["chart"]["close"][-1]
    for name, side, words in (("high52", "Bull", "on a close above"),
                              ("low52", "Bear", "on a close below")):
        gap = flow.pct(float(want[name]), last)
        assert abs(gap) > 0.1
        assert "| %s | %s | %+.1f%% | %s, %s |" % (
            facts[name]["label"], want[name], gap, side, words) in report
    evidence = [x for x in report.splitlines() if x.startswith("| **Best evidence** |")][0]
    for name in ("rsi14", "sma10_gap", "ret5", "sma50_gap", "atr14_pct", "macd_hist"):
        assert "%s %s" % (facts[name]["label"], facts[name]["t"]) in evidence, name
    assert '"Generated headline 1 about TEST" (Example Wire, ' in report

    # The page shows the same figures in its own places.
    root = page_of(desk)
    tiles = [tile.text() for tile in root.one("header").one("div", "t").kids]
    assert tiles[2] == "Last close%s USD%s on the day" % (want["last"], want["chg1"])
    assert tiles[3] == "The check%d figuresinserted by code" % flow.DRAFT_FIGURES
    assert root.one("p", "why").text() == "Why" + why
    shown = [(row.one("span").text(), row.one("b").text())
             for row in root.one("section", "bull").one("ul", "ev").kids]
    assert shown == [(facts[k]["label"], facts[k]["t"]) for k in ("rsi14", "sma10_gap", "ret5")]
    assert notice(desk) == [] and "No data notes." in root.one("section", "rc").text()

    # The receipt counts exactly the figures the draft asked for.
    assert "- Figures: %d inserted by code from the data." % flow.DRAFT_FIGURES in report

    # A download of one year holds the 200-day average for its newest 61 rows only, so the
    # page's 200-day line starts half way across the chart. That is how it is meant to be:
    # nothing is drawn for the rows before.
    assert [value is None for value in brief["series"]["sma200"]] == [True] * 59 + [False] * 61
    line = re.search(r'<path class=ln stroke=#1baf7a d="M([^"]*)"',
                     flow.read(desk / "report.html")).group(1).split("L")
    assert len(line) == 61 and line[0].startswith("59,") and line[-1].startswith("119,")


def test_verify_refuses_a_report_that_was_edited_after_rendering(desk):
    bundle = bundles.bundle()
    brief, report, _, _ = whole_run(desk, bundle, flow.make_draft())
    rsi, last = brief["facts"]["rsi14"]["t"], brief["facts"]["last"]["t"]
    edits = {
        "a figure that is not in the brief": ("RSI(14) %s." % rsi, "RSI(14) 12.34."),
        "a real figure moved to another place": ("stood at %s on" % last, "stood at %s on" % rsi),
        "an added sentence": ("its short average,", "its short average and will recover,"),
        "a changed disclaimer": ("not investment advice", "sound investment advice"),
    }
    for what, (old, new) in edits.items():
        assert report.count(old) == 1, what
        (desk / "edited.md").write_bytes(report.replace(old, new).encode("ascii"))
        done = verify(desk, "edited.md")
        assert done.rc == 1 and done.lines, what
        assert all(x.startswith("DIFF ") for x in done.lines), (what, done.lines)
    # The receipt lines removed altogether.
    cut = report[:report.index("**Receipts**")]
    (desk / "edited.md").write_bytes(cut.encode("ascii"))
    done = verify(desk, "edited.md")
    assert done.rc == 1 and done.lines[0].startswith("DIFF "), done.lines


def edit_once(text, old, new):
    assert text.count(old) == 1, (old, text.count(old))
    return text.replace(old, new)


def test_the_checks_used_here_refuse_a_report_or_a_page_with_one_changed_figure(desk):
    """The comparison in test_portable.py is what every test in this file leans on, so it is
    tested too: one changed, moved or added figure must fail it."""
    bundle = bundles.bundle()
    made, shown, drawn = flow.desk_run(desk, bundle, flow.make_draft(), fetch=True)
    brief, report, doc = flow.check_report(desk, made, shown, drawn)
    facts, page = brief["facts"], flow.read(desk / "report.html")
    flow.check_page(brief, doc, page)

    rsi, last, chg = facts["rsi14"]["t"], facts["last"]["t"], facts["chg1"]["t"]
    tick = re.search(r"text-anchor=end>([\d.]+)</text>", page).group(1)
    # The two level labels in the chart, from the higher price down: (y, dy) of each.
    (top, above), (bottom, below) = re.findall(r"<text class=lt x=0 y=([\d.]+)% dy=(-?\d+)>", page)
    assert (above, below) == ("-8", "16") and float(top) < float(bottom)
    seen = flow.on_page(brief["as_of"])
    assert seen == "2026&#8209;11&#8209;10" and page.count(seen) >= 4
    pages = {
        "a changed figure in a header tile": (
            "<b>%s</b> on the day" % chg, "<b>+9.99%</b> on the day"),
        "a changed value on a meter": (
            "<b>%s</b><svg height=30>" % rsi, "<b>55.55</b><svg height=30>"),
        "a changed number on the price axis": (
            "text-anchor=end>%s</text><line" % tick, "text-anchor=end>%s1</text><line" % tick),
        "a changed figure at a line's end": (">Close %s</text>" % last, ">Close 1.00</text>"),
        "a changed count of figures": (
            "<b>%d figures</b>" % doc["figures"], "<b>%d figures</b>" % (doc["figures"] + 1)),
        "a figure added where none stood": ("Bull against Bear", "Bull 7 against Bear"),
        # These two stand in places the test compares with nothing: only the last sweep,
        # which allows no digit that was not compared, can refuse them.
        "a figure in a heading": ("<h2>Indicators</h2>", "<h2>Indicators 14</h2>"),
        "a figure in a fixed sentence": ("Dot: the value.", "Dot: the value 42."),
        "a changed test record": ("<b>-0.9%</b>", "<b>+0.9%</b>"),
        "a changed statement date": (
            "<small>as of %s</small>" % flow.on_page(facts["mcap"]["as_of"]),
            "<small>as of 2020&#8209;01&#8209;01</small>"),
        # What this round changed on the page: each of these is the page as it was before.
        "a date that can break across two lines": (
            "Desk view at the %s close" % seen, "Desk view at the %s close" % brief["as_of"]),
        "a statement date that can break": (
            "<small>as of %s</small>" % flow.on_page(facts["mcap"]["as_of"]),
            "<small>as of %s</small>" % facts["mcap"]["as_of"]),
        "a header without the sentence about the test": (
            "</p><small>%s</small><div class=t>" % flow.LOST, "</p><div class=t>"),
        "another sentence in the header": ("lost to buy-and-hold", "kept up with buy-and-hold"),
        "a level label on the other level's line": (
            "<text class=lt x=0 y=%s%% dy=-8>" % top, "<text class=lt x=0 y=%s%% dy=-8>" % bottom),
        "a level label pushed down from its line": (
            "<text class=lt x=0 y=%s%% dy=16>" % bottom,
            "<text class=lt x=0 y=%.2f%% dy=4>" % (float(bottom) + 6)),
        "the top level label on its line, not above it": (
            "<text class=lt x=0 y=%s%% dy=-8>" % top, "<text class=lt x=0 y=%s%% dy=4>" % top),
        "the levels headed as they were": ("<th>To reach it</th>", "<th>From last</th>"),
        "the chart caption as it was": ("Dashed lines: the report", "Dashed lines are the report"),
    }
    for what, (old, new) in pages.items():
        changed = page.replace(old, new, 1)
        assert changed != page, what
        with pytest.raises(AssertionError):
            flow.check_page(brief, doc, changed)

    reports = {
        "a changed figure": ("RSI(14) %s." % rsi, "RSI(14) 12.34."),
        "a figure under another label": (
            "| **Best evidence** | RSI(14) %s" % rsi, "| **Best evidence** | RSI(14) %s" % last),
        "a changed level distance": (" | Bull, on a close above |", "1 | Bull, on a close above |"),
        "a changed disclaimer": ("not investment advice", "sound investment advice"),
        "the levels headed as they were": ("| To reach it |", "| From last close |"),
        "another sentence under the first line": (
            "\n\n%s\n\n" % flow.LOST, "\n\nResearch simulation, not advice.\n\n"),
    }
    for what, (old, new) in reports.items():
        (desk / "report.md").write_bytes(edit_once(report, old, new).encode("ascii"))
        with pytest.raises(AssertionError):
            flow.check_report(desk, made, shown, drawn)
    (desk / "report.md").write_bytes(report.encode("ascii"))

    # The resolved report, which the page is drawn from, is held to the brief as well.
    saved = flow.read(desk / "report.json")
    changed = json.loads(saved)
    changed["bull"]["evidence"][0]["text"] = last
    (desk / "report.json").write_bytes((json.dumps(changed) + "\n").encode("ascii"))
    with pytest.raises(AssertionError):
        flow.check_report(desk, made, shown, drawn)
    (desk / "report.json").write_bytes(saved.encode("ascii"))
    flow.check_report(desk, made, shown, drawn)


# --- the flags and notes travel into the report and onto the page ----------------------

VARIANTS = {
    "unfinished session": (bundles.unfinished, ["unfinished_session"], "unfinished"),
    "crypto, unfinished day": (bundles.crypto, ["unfinished_session", "statements_absent"],
                               "unfinished"),
    "prices in pence": (bundles.minor_unit, [], "finished"),
    "short history": (bundles.short_history, ["short_history"], "finished"),
    "index with no volume": (bundles.zero_volume, ["statements_absent"], "finished"),
    "dropped rows": (bundles.null_rows, ["rows_dropped"], "finished"),
    "a week without prices": (lambda: bundles.calendar_gap(7), ["history_gap"], "finished"),
    "no headlines": (lambda: bundles.bundle(seed=26, news=0), ["no_headlines"], "finished"),
    "both follow-up requests failed": (
        bundles.rate_limited, ["statements_absent", "no_headlines"], "finished"),
    "the headline request failed": (
        lambda: bundles.rate_limited(statements=False), ["no_headlines"], "finished"),
    "the statement request failed": (
        lambda: bundles.rate_limited(news=False), ["statements_absent"], "finished"),
    "price below ten": (bundles.low_priced, [], "finished"),
}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_every_data_condition_reaches_the_report_and_the_page(desk, name):
    make, flags, session = VARIANTS[name]
    brief, report, doc, sheet = whole_run(desk, make())
    assert brief["flags"] == flags and brief["session"] == session

    notes = flow.data_notes(report)
    for note in brief["notes"]:
        assert flow.md(note["text"]) in notes, note
    # The report names the flags in words; the fact sheet keeps the names the skill uses.
    assert "Data flags: %s." % (", ".join(flags).replace("_", " ") or "none") in notes
    # The fact sheet the desk reads shows the same notes and flags.
    for note in brief["notes"]:
        assert "%s | %s" % (note["id"], note["text"]) in sheet
    assert sheet[sheet.index("# Flags") + 1] == (", ".join(flags) or "none")
    # A flag brings the notice strip onto the page, with every note; no flag, no strip.
    assert notice(desk) == ([note["text"] for note in brief["notes"]] if flags else [])
    if flags:
        assert brief["notes"], "a flag without a note says nothing to the reader"

    # The newest price is called a close only when the session is over.
    word = "close" if session == "finished" else "last price"
    header = [x for x in report.splitlines() if x.startswith("Desk view at the ")][0]
    assert header.startswith("Desk view at the %s %s " % (brief["as_of"], word))
    assert "**%s %s**" % (brief["facts"]["last"]["t"], brief["unit"]) in header
    tile = page_of(desk).one("header").one("div", "t").kids[2]
    assert tile.one("h2").text() == ("Last close" if session == "finished" else "Last price")


def test_pence_are_named_as_the_unit(desk):
    brief, report, _, _ = whole_run(desk, bundles.minor_unit())
    assert brief["currency"] == "GBp" and brief["unit"] == "pence"
    assert any("pence" in x for x in flow.data_notes(report))
    assert "**%s pence**" % brief["facts"]["last"]["t"] in report
    tile = page_of(desk).one("header").one("div", "t").kids[2]
    assert tile.kids[1].text() == brief["facts"]["last"]["t"] + " pence"


@pytest.mark.parametrize("kind", ["crypto", "another type", "a price file"])
def test_a_market_with_a_bar_on_every_day_has_365_bars_in_its_year(tmp_path, kind):
    """The 52-week high and low reach back 365 bars, and the volatility is annualised over
    365, when the market has a bar on every calendar day: whatever the bundle calls the
    instrument, and when a price file calls it nothing. 252 is for a market that is closed
    at the weekend (the first test of this file)."""
    bundle = bundles.bundle(seed=73, n=400, every_day=True, gmtoffset=0, news=0,
                            with_statements=False,
                            typ="CRYPTOCURRENCY" if kind == "crypto" else "EQUITY")
    close = bundle["chart"]["close"]
    high, low = max(close[-365:]), min(close[-365:])
    # Both lie more than a trading year back, and an older bar is higher than the high.
    assert high > max(close[-252:]) and low < min(close[-252:]) and high < max(close)
    folder = flow.copy_scripts(tmp_path / "every day")
    draft = flow.make_draft(headline=False)
    if kind == "a price file":
        (folder / "prices.csv").write_bytes(bundles.csv_text(bundle).encode("ascii"))
        (folder / "draft.txt").write_bytes(draft.encode("ascii"))
        made = flow.run(folder / "brief.py", folder / "prices.csv", folder / "brief.json",
                        "--symbol", "TEST", "--currency", "USD")
        shown = flow.run(folder / "render.py", folder / "brief.json", folder / "draft.txt",
                         folder / "report.md", folder / "report.json")
        drawn = flow.run(folder / "page.py", folder / "brief.json", folder / "report.json",
                         folder / "report.html")
        brief, report, _ = flow.check_report(folder, made, shown, drawn)
    else:
        brief, report, _, _ = whole_run(folder, bundle, draft)
    facts = brief["facts"]
    assert (facts["high52"]["t"], facts["low52"]["t"]) == ("%.2f" % high, "%.2f" % low)
    moves = [after / before - 1 for before, after in zip(close[-22:], close[-21:])]
    mean = sum(moves) / 21
    spread = (sum((x - mean) ** 2 for x in moves) / 20) ** 0.5
    assert facts["vol21"]["t"] == "%.2f%%" % (100 * spread * 365 ** 0.5)
    assert facts["vol21"]["t"] != "%.2f%%" % (100 * spread * 252 ** 0.5)
    # The levels of the report are those of the calendar year.
    for name, value, words in (("high52", high, "Bull, on a close above"),
                               ("low52", low, "Bear, on a close below")):
        assert "| %s | %.2f | %+.1f%% | %s |" % (
            facts[name]["label"], value, flow.pct(value, close[-1]), words) in report


def test_a_fact_the_data_cannot_give_cannot_be_cited(desk):
    """Short history has no 200-day average: the draft cannot name it, so no report."""
    draft = flow.make_draft(levels=TABLE_LEVELS).replace("{{rsi14|label}}", "{{sma200|label}}")
    made, shown, drawn = flow.desk_run(desk, bundles.short_history(), draft)
    assert made.rc == 0 and shown.rc == 1 and drawn is None
    assert len(shown.lines) == 1 and shown.lines[0].startswith("FIX why: D_NAME "), shown.out
    for name in OUTPUTS[1:]:
        assert not (desk / name).exists(), name


def test_an_unfinished_session_cannot_be_called_a_close(desk):
    draft = flow.make_draft(levels=TABLE_LEVELS).replace(
        "The price stood at {{last}}", "The last close was {{last}}")
    made, shown, drawn = flow.desk_run(desk, bundles.unfinished(), draft)
    assert made.rc == 0 and shown.rc == 1 and drawn is None
    assert [x.split()[2] for x in shown.lines] == ["D_NOTE"], shown.out
    # The same sentence is fine once the session is over.
    made, shown, drawn = flow.desk_run(desk, bundles.bundle(), draft)
    flow.check_report(desk, made, shown, drawn)
    # "closed down" with the day's own move is the same claim about an unfinished session.
    clean = flow.make_draft(levels=TABLE_LEVELS)
    down = clean.replace("The price stood at {{last}}", "The stock closed down {{chg1}}")
    made, shown, drawn = flow.desk_run(desk, bundles.unfinished(), down)
    assert shown.rc == 1 and drawn is None and shown.lines == [
        'FIX why: D_NOTE the session may be unfinished: say "the price", not "close" | try: '
        'the price'], shown.out
    # The other order, the day's move first and "at the close" after it, says the same and
    # used to pass.
    after = clean.replace("The price stood at {{last}} on {{as_of}}.",
                          "It was {{chg1|dir}} at the close on {{as_of}}.")
    assert after != clean
    made, shown, drawn = flow.desk_run(desk, bundles.unfinished(), after)
    assert shown.rc == 1 and drawn is None and shown.lines == [
        'FIX why: D_NOTE the session may be unfinished: say "the price", not "close" | try: '
        'the price'], shown.out
    made, shown, drawn = flow.desk_run(desk, bundles.bundle(), after)
    _, _, doc = flow.check_report(desk, made, shown, drawn)
    assert re.search(r" It was \d+\.\d\d% (up|down) at the close on 2026-11-10\.$", doc["why"])
    # The previous close is a finished session's: a move measured from it is printed, with
    # the label of the fact that is named.
    since = clean.replace("The price stood at {{last}} on {{as_of}}.",
                          "It is {{chg1|dir}} from the previous close ({{chg1|label}}).")
    assert since != clean
    made, shown, drawn = flow.desk_run(desk, bundles.unfinished(), since)
    brief, _, doc = flow.check_report(desk, made, shown, drawn)
    move = brief["facts"]["chg1"]["t"]
    assert doc["why"].endswith(" It is %s %s from the previous close (one-day change %s)." % (
        move.lstrip("+-"), "down" if move.startswith("-") else "up", move))


# --- a follow-up request that failed is not data the source lacks ----------------------

FAILED_REQUESTS = {
    # what happened: (the bundle, the end of the DATA line, the statement note, the
    # headline note)
    "both failed": (bundles.rate_limited, "headlines=failed statements=failed", "failed",
                    "failed"),
    "the headlines failed": (lambda: bundles.rate_limited(statements=False),
                             "headlines=failed statements=ok", None, "failed"),
    "the statements failed": (lambda: bundles.rate_limited(news=False),
                              "headlines=3 statements=failed", "failed", None),
    "another status": (lambda: bundles.rate_limited(label="HTTP 503"),
                       "headlines=failed statements=failed", "failed", "failed"),
    "an answer that is not the data": (lambda: bundles.rate_limited(label="unexpected answer"),
                                       "headlines=failed statements=failed", "failed", "failed"),
    "the source has neither": (lambda: bundles.bundle(seed=27, news=0, with_statements=False),
                               "headlines=0 statements=absent", "absent", "absent"),
}
STATEMENT_NOTES = {
    "failed": "The statement request failed, so these facts are absent: %s.",
    "absent": "The source gave no statement figure, so these facts are absent: %s."}
HEADLINE_NOTES = {"failed": "The headline request failed.",
                  "absent": "No headlines came with this data."}


@pytest.mark.parametrize("name", sorted(FAILED_REQUESTS))
def test_a_request_that_failed_is_named_as_failed_from_the_data_line_to_the_page(desk, name):
    """The bars arrived and a request after them did not. The notes used to read as if the
    source had no statement figure and no headline for the symbol."""
    make, counts, statements, headlines = FAILED_REQUESTS[name]
    bundle = make()
    line = flow.download(desk, bundle)
    assert line == "DATA ok TEST bars=260 " + counts
    made, shown, drawn = flow.desk_run(desk, bundle, draft_for(bundle))
    brief, report, _ = flow.check_report(desk, made, shown, drawn)
    wanted = []
    if statements:
        wanted.append(STATEMENT_NOTES[statements] % flow.STATEMENT_FACTS)
    if headlines:
        wanted.append(HEADLINE_NOTES[headlines])
    assert [note["text"] for note in brief["notes"]] == wanted
    # The flags, which decide the roles that run, are the same for failed and for absent.
    assert brief["flags"] == ["statements_absent"] * bool(statements) + [
        "no_headlines"] * bool(headlines)
    # The report, the page and the fact sheet carry the sentence as the brief has it.
    notes = flow.data_notes(report)
    assert [flow.md(text) for text in wanted] == notes[:len(wanted)]
    assert notice(desk) == wanted
    for number, text in enumerate(wanted, 1):
        assert "n%d | %s" % (number, text) in made.lines
    assert verify(desk).lines == ["VERIFY ok"]


# --- only a company has statements -----------------------------------------------------

NO_STATEMENTS = {
    "a fund": lambda: bundles.bundle(seed=41, typ="ETF", symbol="FUND", with_statements=False),
    "a mutual fund": lambda: bundles.bundle(seed=42, typ="MUTUALFUND", symbol="MFUNDX",
                                            with_statements=False),
    "an index": bundles.zero_volume,
    "a coin": bundles.crypto,
}


@pytest.mark.parametrize("name", sorted(NO_STATEMENTS))
def test_an_instrument_that_is_not_a_share_is_asked_for_no_statements(desk, name):
    """A fund, an index and a coin have no statements, and the source answered that request
    with ten empty lists. It is not sent any more: two requests, and a DATA line, a note and
    a page that say the figures are absent, not that a request failed."""
    bundle = NO_STATEMENTS[name]()
    symbol, bars = bundle["symbol"], len(bundle["chart"]["timestamp"])
    assert not flow.statements_asked(bundle) and not any(bundle["statements"].values())
    # The stand-in would answer a third request with a share's figures, so a bundle without
    # them means that the request was not sent.
    chart, search, _ = flow.source_answers(bundle)
    figures = flow.source_answers(bundles.bundle(symbol=symbol))[2]
    assert b"reportedValue" in figures[2]
    done = flow.run_get_data(desk, symbol, (chart, search, figures))
    assert (done.rc, done.err, done.pauses) == (0, "", []), done.err
    assert len(done.asked) == 2 and not any("/timeseries/" in url for url in done.asked)
    assert "/v8/finance/chart/" in done.asked[0] and "/v1/finance/search?" in done.asked[1]
    assert done.out == "DATA ok %s bars=%d headlines=3 statements=absent\n" % (symbol, bars)
    assert json.loads(flow.read(desk / "bundle.json")) == bundle
    assert bundle["errors"] == {"news": None, "statements": None}

    # whole_run downloads once more, with the same count of requests, and checks the rest.
    brief, report, _, sheet = whole_run(desk, bundle)
    note = STATEMENT_NOTES["absent"] % flow.STATEMENT_FACTS
    assert "statements_absent" in brief["flags"]
    said = [x for x in brief["notes"] if x["text"] == note]
    assert len(said) == 1 and "%s | %s" % (said[0]["id"], note) in sheet
    assert flow.md(note) in flow.data_notes(report)
    assert "request failed" not in report and "request failed" not in "\n".join(sheet)
    root = page_of(desk)
    assert root.find("section", "fd") == [] and note in notice(desk)
    assert "%s %s yahoo-chart" % (brief["type"], flow.MID) in root.one("section", "rc").text()


@pytest.mark.parametrize("named", [True, False], ids=["a share", "no type in the answer"])
def test_a_share_or_an_instrument_of_no_named_type_is_asked_for_statements(desk, named):
    bundle = bundles.bundle(seed=43)
    if not named:
        del bundle["chart"]["meta"]["instrumentType"]
    assert flow.statements_asked(bundle)
    done = flow.run_get_data(desk, "TEST", flow.source_answers(bundle))
    assert (done.rc, done.err) == (0, "") and len(done.asked) == 3
    assert "/ws/fundamentals-timeseries/v1/finance/timeseries/TEST?" in done.asked[2]
    assert done.out == "DATA ok TEST bars=260 headlines=3 statements=ok\n"
    brief, _, _, _ = whole_run(desk, bundle)
    assert brief["type"] == ("EQUITY" if named else "UNKNOWN") and brief["flags"] == []
    assert "pe_trailing" in brief["facts"]
    assert len(page_of(desk).one("section", "fd").one("div", "fu").kids) == 8


# --- the row shapes seen live from the data source (OBSERVED-SHAPES.md) ----------------

def table_dates(brief):
    return [brief["facts"]["date.%d" % n]["t"] for n in range(10)]


def test_shape_a_every_row_complete(desk):
    """New York or Tokyo after the close: the calendar already describes the next session."""
    bundle = bundles.bundle(seed=30)
    assert bundle["chart"]["meta"]["currentTradingPeriod"]["regular"]["start"] \
        > bundle["fetched_at"] > bundle["chart"]["timestamp"][-1]
    brief, report, _, _ = whole_run(desk, bundle)
    assert (brief["as_of"], brief["session"], brief["flags"]) == ("2026-11-10", "finished", [])
    assert brief["facts"]["last"]["v"] == bundle["chart"]["close"][-1]
    assert "Data flags: none." in flow.data_notes(report) and notice(desk) == []


@pytest.mark.parametrize("kw", [{}, {"gmtoffset": 0, "currency": "GBp", "symbol": "TEST.L"},
                                {"gmtoffset": 3600, "currency": "EUR", "symbol": "TEST.DE"}],
                         ids=["plain", "london", "frankfurt"])
def test_shape_b_the_newest_close_is_filled_and_the_report_and_the_page_say_so(desk, kw):
    """From the close until the next open the newest row has no close; the source's last
    regular price, dated on that row's day, fills it."""
    bundle = bundles.fill_case(**kw)
    assert bundle["chart"]["close"][-1] is None
    price = bundle["chart"]["meta"]["regularMarketPrice"]
    brief, report, _, sheet = whole_run(desk, bundle)
    note = ("The source had no close for 2026-11-10 yet; it is filled from the exchange's last "
            "regular price.")
    assert (brief["as_of"], brief["session"], brief["flags"]) == (
        "2026-11-10", "finished", ["close_filled"])
    assert brief["facts"]["last"]["v"] == price == brief["facts"]["close.0"]["v"]
    assert brief["notes"][0]["text"] == note and "n1 | " + note in sheet
    # In the report: the note, the flag, and the filled price as the close.
    assert note in flow.data_notes(report)
    assert "Data flags: close filled." in flow.data_notes(report)
    assert "Desk view at the 2026-11-10 close%s**%s " % (
        DOT, brief["facts"]["last"]["t"]) in report
    # On the page: the notice strip with the note, and the filled price in the header.
    assert notice(desk)[0] == note
    assert brief["facts"]["last"]["t"] in page_of(desk).one("header").one("p").text()


@pytest.mark.parametrize("holes,quote,dated", [
    ((-2, -1), "today", "2026-11-10"), ((-2, -1), "yesterday", "2026-11-09"),
    ((-2,), "today", "2026-11-10"), ((-2, -1), "none", None)],
    ids=["london", "frankfurt", "the later look", "no last price"])
def test_shape_c_an_empty_finished_session_cuts_the_view_and_both_outputs_say_so(
        desk, holes, quote, dated):
    """Minutes after the next open the finished session's row is empty. The view ends at
    the last complete row, and the first lines of the report and of the page say so."""
    bundle = bundles.behind_case(holes=holes, quote=quote)
    brief, report, _, sheet = whole_run(desk, bundle)
    assert (brief["as_of"], brief["session"], brief["flags"]) == (
        "2026-11-06", "finished", ["prices_behind"])
    note = "The source has no complete prices for 2026-11-09 yet, so this view ends at 2026-11-06."
    if dated:
        note += " The source's last regular price is %.2f, dated %s." % (
            bundle["chart"]["meta"]["regularMarketPrice"], dated)
    assert [n["text"] for n in brief["notes"]] == [note] and "n1 | " + note in sheet
    # Nothing of the cut rows is in the view: not in the table, not in the chart.
    assert table_dates(brief)[0] == "2026-11-06" == brief["series"]["date"][-1]
    assert brief["facts"]["last"]["v"] == bundle["chart"]["close"][-3]
    # The report: its header line says it, and the note says which dates.
    header = report.split("\n\n")[1]
    assert header.startswith("Desk view at the 2026-11-06 close" + DOT)
    assert header.endswith(DOT + "the source is behind, see Data notes")
    assert note in flow.data_notes(report)
    assert "Data flags: prices behind." in flow.data_notes(report)
    # The page: the same in its header line and in the notice strip.
    root = page_of(desk)
    assert root.one("header").one("p").text().startswith(
        "Desk view at the 2026-11-06 close (the data source is behind) ")
    assert notice(desk) == [note]
    assert root.one("section", "s4").one("small").text().endswith(
        "to 2026-11-06. 50d and 200d: the 50-day and 200-day averages. Dashed lines: the "
        "report's levels.")
    # Read back, the dates are the brief's; in the file their hyphens are the ones that do
    # not break, so "2026-11-" and "06" cannot end up on two lines.
    page = flow.read(desk / "report.html")
    assert "Desk view at the 2026&#8209;11&#8209;06 close (the data source is behind)" in page
    assert "to 2026&#8209;11&#8209;06. 50d and 200d" in page and "2026-11-0" not in page


def test_shape_c_a_session_in_progress_with_no_bar_yet_is_not_called_behind(desk):
    """Only the row of the session that has just begun is empty: nothing is behind."""
    bundle = bundles.behind_case(holes=(-1,))
    brief, report, _, _ = whole_run(desk, bundle)
    assert (brief["as_of"], brief["session"], brief["flags"]) == ("2026-11-09", "finished", [])
    note = ("The source has no complete prices for 2026-11-10 yet, so this view ends at "
            "2026-11-09. Its prices were not final at the fetch.")
    assert [n["text"] for n in brief["notes"]] == [note] and note in flow.data_notes(report)
    assert "the source is behind" not in report
    # No flag, so no notice strip; the note is in the page's receipts.
    root = page_of(desk)
    assert notice(desk) == [] and "is behind" not in root.one("header").text()
    assert note in root.one("section", "rc").text()


@pytest.mark.parametrize("trade_first", [False, True], ids=["start row first", "trade row first"])
def test_shape_d_two_rows_for_one_date_become_one_and_nothing_is_said(tmp_path, trade_first):
    """Sydney after the close: a row stamped at the session start with no close, and a row
    stamped at the last trade with it. One row is kept, with the close."""
    bundle = bundles.same_day_case(trade_first=trade_first)
    stamps = bundle["chart"]["timestamp"]
    assert len(stamps) == 261 and None in bundle["chart"]["close"][-2:]
    brief, report, _, _ = whole_run(flow.copy_scripts(tmp_path / "two rows"), bundle)
    close = [c for c in bundle["chart"]["close"][-2:] if c is not None][0]
    assert (brief["as_of"], brief["session"], brief["flags"]) == ("2026-11-10", "finished", [])
    assert brief["facts"]["last"]["v"] == close and brief["notes"] == []
    dates = table_dates(brief)
    assert dates[:2] == ["2026-11-10", "2026-11-09"] and len(set(dates)) == 10
    assert len(set(brief["series"]["date"])) == len(brief["series"]["date"]) == 120
    assert "Data flags: none." in flow.data_notes(report)
    assert notice(tmp_path / "two rows") == []

    # Every output is the one the same history gives with a single row for that date.
    single = bundles.same_day_case()
    for key in ("timestamp",) + bundles.COLS:
        del single["chart"][key][-2]
    whole_run(flow.copy_scripts(tmp_path / "one row"), single)
    for name in OUTPUTS:
        assert (tmp_path / "two rows" / name).read_bytes() == \
            (tmp_path / "one row" / name).read_bytes(), name


def test_shape_e_an_empty_finished_crypto_day_cuts_the_view(desk):
    bundle = bundles.behind_crypto()
    brief, report, _, _ = whole_run(desk, bundle)
    assert (brief["as_of"], brief["session"]) == ("2026-11-08", "finished")
    assert brief["flags"] == ["prices_behind", "statements_absent"]
    note = brief["notes"][0]["text"]
    assert note.startswith("The source has no complete prices for 2026-11-09 yet, so this view "
                           "ends at 2026-11-08.")
    assert report.split("\n\n")[1].endswith(DOT + "the source is behind, see Data notes")
    assert notice(desk)[0] == note
    assert "(the data source is behind)" in page_of(desk).one("header").one("p").text()


def test_shape_f_a_fetch_after_the_session_end_is_a_finished_session(desk):
    """Hong Kong in its closing auction: the calendar still describes today's session, the
    fetch is after its end, so the newest row is a close."""
    bundle = bundles.bundle(seed=33, session="after_close", gmtoffset=28800, currency="HKD",
                            symbol="0700.HK")
    period = bundle["chart"]["meta"]["currentTradingPeriod"]["regular"]
    assert period["start"] == bundle["chart"]["timestamp"][-1]
    assert period["end"] < bundle["fetched_at"]
    brief, report, _, _ = whole_run(desk, bundle)
    assert (brief["as_of"], brief["session"], brief["flags"]) == ("2026-11-10", "finished", [])
    assert "Desk view at the 2026-11-10 close" in report


@pytest.mark.parametrize("quote, fetch, session", [
    (-900, 240, "unfinished"), (-900, 1799, "unfinished"), (-900, 1800, "finished"),
    (1, 240, "finished"), (None, 240, "finished"), (None, -1, "unfinished")],
    ids=["an old quote, minutes after the bell", "an old quote, just under half an hour",
         "an old quote, half an hour on", "a quote from after the bell", "no quote time",
         "no quote time, before the bell"])
def test_shape_f_minutes_after_the_bell_the_source_may_still_show_an_older_price(
        desk, quote, fetch, session):
    """The source's quote can be a quarter of an hour old. A fetch four minutes after the
    bell with a quote from before it used to be called a finished session, and the report
    named a price from before the bell as the close. The session is over once the quote is
    from the end or later, or half an hour has passed; with no quote time the fetch time
    decides, as before."""
    bundle = bundles.bundle(seed=34, session="unfinished")
    meta = bundle["chart"]["meta"]
    end = meta["currentTradingPeriod"]["regular"]["end"]
    bundle["fetched_at"] = end + fetch
    if quote is None:
        del meta["regularMarketTime"]
    else:
        meta["regularMarketTime"] = end + quote
    brief, report, _, _ = whole_run(desk, bundle)
    live = session == "unfinished"
    assert (brief["as_of"], brief["session"]) == ("2026-11-10", session)
    assert brief["flags"] == ["unfinished_session"] * live
    header = report.split("\n\n")[1]
    assert header.startswith("Desk view at the 2026-11-10 %s%s" % (
        "last price" if live else "close", DOT))
    line = page_of(desk).one("header").one("p").text()
    assert line.startswith("Desk view on 2026-11-10, session unfinished " if live
                           else "Desk view at the 2026-11-10 close ")
    assert bool(notice(desk)) == live


@pytest.mark.parametrize("make", [bundles.crypto, bundles.unfinished], ids=["a coin", "a share"])
def test_a_live_price_outside_the_days_published_range_reaches_the_report(desk, make):
    """Seen live for a coin: the source's last price is newer than its day-high field, so the
    close of the row in progress stood above that row's own high, and the run stopped with
    E_OHLC. The range of a row in progress is widened to hold its open and close; a row of a
    finished session with the same fault still stops the run."""
    bundle = make()
    chart = bundle["chart"]
    price = round(chart["high"][-1] * 1.0007, 2)
    assert price > chart["high"][-1]
    chart["close"][-1] = chart["meta"]["regularMarketPrice"] = price
    brief, report, _, sheet = whole_run(desk, bundle)
    facts = brief["facts"]
    assert brief["session"] == "unfinished" and "unfinished_session" in brief["flags"]
    assert facts["last"]["v"] == facts["high.0"]["v"] == price
    assert facts["low.0"]["v"] == chart["low"][-1]
    assert "high.0 | %s | %s" % (facts["high.0"]["label"], facts["last"]["t"]) in sheet
    assert "**%s %s**" % (facts["last"]["t"], brief["unit"]) in report.split("\n\n")[1]

    # The same row once its session is over: the data is wrong, and the run stops.
    done = bundles.bundle(seed=20)
    done["chart"]["close"][-1] = round(done["chart"]["high"][-1] * 1.0007, 2)
    made, shown, drawn = flow.desk_run(desk, done, draft_for(done))
    assert shown is None and drawn is None and made.rc == 2 and made.out == ""
    assert len(made.err_lines) == 1 and made.err_lines[0].startswith("STOP E_OHLC "), made.err


# --- stops -----------------------------------------------------------------------------

@pytest.mark.parametrize("make,code", [
    (bundles.too_few, "E_FEW"), (bundles.stale, "E_STALE"), (bundles.date_gap, "E_GAP"),
    (lambda: bundles.behind_crypto(absent=True), "E_GAP"), (bundles.bad_prices, "E_OHLC"),
    (bundles.future_bar, "E_STALE"),
], ids=["too few rows", "stale", "a gap of two weeks", "crypto with a day absent",
        "impossible prices", "a bar after the fetch"])
def test_a_stop_ends_the_run_before_any_report(desk, make, code):
    bundle = make()
    made, shown, drawn = flow.desk_run(desk, bundle, draft_for(bundle), fetch=True)
    assert shown is None and drawn is None and made.rc == 2 and made.out == ""
    assert len(made.err_lines) == 1 and made.err_lines[0].startswith("STOP %s " % code), made.err
    assert not (desk / "brief.json").exists()
    # With no brief there is nothing to render or to draw: one line each, no traceback.
    done = flow.run(desk / "render.py", desk / "brief.json", desk / "draft.txt",
                    desk / "report.md", desk / "report.json")
    assert done.rc != 0 and done.out == "" and len(done.err_lines) == 1, done.err
    done = flow.run(desk / "page.py", desk / "brief.json", desk / "report.json",
                    desk / "report.html")
    assert done.rc != 0 and done.out == "" and len(done.err_lines) == 1, done.err
    for name in OUTPUTS[1:]:
        assert not (desk / name).exists(), name


def test_the_crypto_stop_names_both_dates(desk):
    bundle = bundles.behind_crypto(absent=True)
    made, _, _ = flow.desk_run(desk, bundle, draft_for(bundle), fetch=True)
    assert made.err_lines == [
        "STOP E_GAP the daily history is broken between 2026-11-08 and 2026-11-10"]


# --- without the network, and without the optional scripts -----------------------------

@pytest.mark.parametrize("kind", ["price file", "marketplace result"])
def test_the_no_network_route_gives_the_same_figures_without_get_data(tmp_path, kind):
    """A host with no network never writes get_data.py. The price route then gives the same
    price facts, and the report and the page say what is missing."""
    bundle = bundles.bundle(seed=27)
    with_network = flow.copy_scripts(tmp_path / "network")
    online, _, _, _ = whole_run(with_network, bundle)

    folder = flow.copy_scripts(tmp_path / "no network", names=("brief.py", "render.py", "page.py"))
    pre = flow.run(folder / "brief.py", "--preflight", folder)
    assert (pre.rc, pre.err) == (0, "") and pre.lines == [
        flow.preflight_line(("brief.py", "page.py", "render.py"))]
    if kind == "price file":
        (folder / "prices.csv").write_bytes(bundles.csv_text(bundle).encode("ascii"))
        args = [folder / "prices.csv", folder / "brief.json", "--symbol", "TEST",
                "--currency", "USD"]
    else:
        (folder / "saved.json").write_bytes(bundles.dumps(bundles.history_result(bundle)))
        args = [folder / "saved.json", folder / "brief.json"]
    draft = flow.make_draft(levels=TABLE_LEVELS, headline=False)
    (folder / "draft.txt").write_bytes(draft.encode("ascii"))
    made = flow.run(folder / "brief.py", *args)
    assert made.rc == 0 and made.err == "", made.err
    shown = flow.run(folder / "render.py", folder / "brief.json", folder / "draft.txt",
                     folder / "report.md", folder / "report.json")
    drawn = flow.run(folder / "page.py", folder / "brief.json", folder / "report.json",
                     folder / "report.html")
    brief, report, doc = flow.check_report(folder, made, shown, drawn)
    assert (brief["source"], brief["session"]) == ("price-file", "unknown")
    assert brief["flags"] == ["statements_absent", "no_headlines"]
    # The same rows, so the same price facts and the same data id.
    assert brief["data_id"] == online["data_id"]
    for name, fact in online["facts"].items():
        if "as_of" not in fact and not name.startswith(("last", "close.0")) and name != "as_of":
            assert brief["facts"].get(name, {}).get("t") == fact["t"], name
    # A session that is not known to be over has a last price, not a close.
    assert "Desk view at the 2026-11-10 last price" + DOT in report
    root = page_of(folder)
    assert root.one("header").one("p").text().startswith(
        "Desk view on 2026-11-10, session unknown ")
    # Nothing was downloaded, so neither output claims a fetch time: the newest row's time
    # stamp used to stand there as "data fetched".
    assert report.split("\n\n")[1] == (
        "Desk view at the 2026-11-10 last price%s**%s USD**%sprices from a file" % (
            DOT, brief["facts"]["last"]["t"], DOT))
    assert root.one("header").one("p").text().endswith(" Prices from a file")
    assert "fetched" not in report.split("**Data notes**")[0]
    assert "fetched" not in flow.read(folder / "report.html").lower()
    assert "These prices came from a file; when they were downloaded is not known." in [
        note["text"] for note in brief["notes"]]
    # The session is not known to be over, so the draft may not call the price a close.
    closed = draft.replace("The price stood at {{last}}", "The stock closed at {{last}}")
    (folder / "closed.txt").write_bytes(closed.encode("ascii"))
    refused = flow.run(folder / "render.py", folder / "brief.json", folder / "closed.txt",
                       folder / "closed.md", folder / "closed.json")
    assert refused.rc == 1 and refused.lines == [
        'FIX why: D_NOTE the session may be unfinished: say "the price", not "close" | try: '
        'the price'], refused.out
    assert not (folder / "closed.md").exists()
    assert notice(folder) == [note["text"] for note in brief["notes"]]
    assert root.find("section", "fd") == [] and root.find("section", "hd") == []
    assert verify(folder).lines == ["VERIFY ok"]


@pytest.mark.parametrize("offset", [39600, 46800], ids=["Sydney", "New Zealand"])
def test_a_saved_history_from_an_exchange_far_east_of_utc_stops_the_run(tmp_path, offset):
    """Sydney in its summer and New Zealand open late in the UTC day before. A saved price
    history carries no exchange offset, so its dates came out one day early, and the day's
    finished close was cut with a false "the source is behind". The download knows the
    offset and is right; the saved history now stops with one sentence."""
    bundle = bundles.same_day_case(gmtoffset=offset)
    online = flow.copy_scripts(tmp_path / "download")
    brief, report, _, _ = whole_run(online, bundle)
    assert brief["as_of"] == "2026-11-10" and brief["flags"] == []

    folder = flow.copy_scripts(tmp_path / "saved", names=("brief.py", "render.py", "page.py"))
    (folder / "saved.json").write_bytes(bundles.dumps(bundles.history_result(bundle)))
    made = flow.run(folder / "brief.py", folder / "saved.json", folder / "brief.json")
    assert made.rc == 2 and made.out == ""
    assert made.err_lines == [
        "STOP E_DATES this price history does not say which day each row belongs to"]
    assert not (folder / "brief.json").exists()
    # The same rows as a price file with dates give the download's own dates and close.
    (folder / "prices.csv").write_bytes(bundles.csv_text(bundle).encode("ascii"))
    made = flow.run(folder / "brief.py", folder / "prices.csv", folder / "brief.json",
                    "--symbol", "TEST.AX", "--currency", "AUD")
    assert made.rc == 0, made.err
    dated = json.loads(flow.read(folder / "brief.json"))
    assert dated["as_of"] == "2026-11-10" and "prices_behind" not in dated["flags"]
    assert dated["facts"]["last"]["t"] == brief["facts"]["last"]["t"]
    assert dated["data_id"] == brief["data_id"]


def test_a_headline_with_a_quote_in_it_stays_one_quotation(desk):
    """A title that held a double quote closed its own quotation: the rest of it, a made-up
    figure and wording the renderer refuses from the desk, read as the desk's sentence."""
    bundle = bundles.bundle(seed=28)
    bundle["news"] = [{
        "title": 'Test Instruments falls 9%" and the desk sees RSI(14) at 12.00, a "strong buy',
        "publisher": "Example) desk note: RSI is 99 (x", "time": bundle["fetched_at"] - 3600,
        "tickers": ["TEST"]}]
    brief, report, doc, _ = whole_run(desk, bundle)
    said = ("\"Test Instruments falls 9%' and the desk sees RSI(14) at 12.00, a 'strong buy\" "
            "(Example desk note: RSI is 99 x, 2026-11-10)")
    assert doc["bear"]["also"] == "The newest headline, %s, does not explain the move." % said
    assert said.count('"') == 2 and said.index('" (') == said.rindex('"')
    assert report.count(flow.md(said)) == 2      # where the draft cites it, and in its block
    # The page shows the desk's sentence as the report has it, and the headline card lists
    # the title and the publisher apart, as the source gave them.
    root = page_of(desk)
    assert said in root.one("section", "bear").text()
    card = root.one("section", "hd")
    assert brief["headlines"][0]["title"] in card.text()
    assert verify(desk).lines == ["VERIFY ok"]


def test_a_headline_with_typographic_quotes_or_unseen_characters_is_the_plain_one(tmp_path):
    """render.py makes a plain double quote in a title a single one, so that the quotation
    ends where it should. A typographic or fullwidth quote reached it untouched, and to the
    eye it closed the quotation; characters that print nothing and marks that reorder what
    follows came through as well. get_data.py writes the plain quote and drops the others,
    so the bundle is the one a source with plain text gives."""
    bundle = bundles.bundle(seed=28)
    plain = 'Test Instruments falls 9%" and the desk sees RSI(14) at 12.00, a "strong buy'
    sent = ("Test Instruments falls 9%\u201d and the desk sees RSI\u200b(14) at 12.00, a "
            "\uff02str\u200dong\u202e buy\u2066\ufeff")
    bundle["news"] = [{"title": plain, "publisher": "Example Wire",
                       "time": bundle["fetched_at"] - 3600, "tickers": ["TEST"]}]
    chart, search, series = flow.source_answers(bundle)
    status, headers, body = search
    doc = json.loads(body)
    assert doc["news"][0]["title"] == plain
    doc["news"][0].update(title=sent, publisher="Example\u200f Wire\u202c")
    folder = flow.copy_scripts(tmp_path / "typographic")
    done = flow.run_get_data(folder, "TEST", (
        chart, (status, headers, json.dumps(doc).encode("ascii")), series))
    assert (done.rc, done.err) == (0, ""), done.err
    assert done.out == "DATA ok TEST bars=260 headlines=1 statements=ok\n"
    saved = json.loads(flow.read(folder / "bundle.json"))
    assert saved["news"][0]["title"] == plain and saved == bundle
    # So the report quotes it as one quotation, as it does the plain title.
    made, shown, drawn = flow.desk_run(folder, saved, draft_for(saved))
    brief, report, doc = flow.check_report(folder, made, shown, drawn)
    said = ("\"Test Instruments falls 9%' and the desk sees RSI(14) at 12.00, a 'strong buy\" "
            "(Example Wire, 2026-11-10)")
    assert doc["bear"]["also"] == "The newest headline, %s, does not explain the move." % said
    assert said.count('"') == 2 and report.count(flow.md(said)) == 2
    assert verify(folder).lines == ["VERIFY ok"]


def test_statement_money_says_its_currency_and_its_date_in_both_outputs(desk):
    """Prices in pence, market value in pounds, revenue in dollars, and a P/E from last year:
    the report used to print the bare numbers under a header that named only pence."""
    bundle = bundles.bundle(seed=29, currency="GBp", symbol="TEST.L", gmtoffset=0)
    old = bundles.iso(bundles.END - 400)
    for entry in bundle["statements"]["trailingMarketCap"]:
        entry["currency"] = "GBP"
    bundle["statements"]["trailingPeRatio"] = [
        {"asOf": old, "value": bundle["statements"]["trailingPeRatio"][-1]["value"]}]
    draft = flow.make_draft(levels=TABLE_LEVELS).replace(
        "[risk] ", "[risk] The shares trade on {{pe_trailing|label}} with {{mcap|label}} and "
        "{{rev_ttm|label}}. ").replace(
        "[bull.evidence] rsi14, sma10_gap, ret5", "[bull.evidence] rsi14, pe_trailing, mcap")
    brief, report, doc, sheet = whole_run(desk, bundle, draft)
    facts = brief["facts"]
    assert brief["unit"] == "pence"
    assert facts["mcap"]["t"].endswith(" GBP") and facts["rev_ttm"]["t"].endswith(" USD")
    assert " " not in facts["pe_trailing"]["t"] and facts["pe_trailing"]["as_of"] == old
    assert doc["risk"].startswith(
        "The shares trade on trailing P/E %s (as of %s) with market value %s (as of %s) and "
        "revenue, trailing 12 months %s (as of %s). " % (
            facts["pe_trailing"]["t"], old, facts["mcap"]["t"], facts["mcap"]["as_of"],
            facts["rev_ttm"]["t"], facts["rev_ttm"]["as_of"]))
    assert doc["bull"]["evidence"][1:] == [
        {"label": "trailing P/E", "text": "%s (as of %s)" % (facts["pe_trailing"]["t"], old)},
        {"label": "market value", "text": "%s (as of %s)" % (
            facts["mcap"]["t"], facts["mcap"]["as_of"])}]
    assert "market value %s (as of %s)" % (facts["mcap"]["t"], facts["mcap"]["as_of"]) in report
    assert [line for line in sheet if line.startswith("mcap | ")][0].split(" | ")[2:] == [
        facts["mcap"]["t"], "as of " + facts["mcap"]["as_of"]]
    tiles = {tile.kids[0].text(): tile for tile in
             page_of(desk).one("section", "fd").one("div", "fu").kids}
    assert tiles["market value"].kids[1].text() == facts["mcap"]["t"]
    assert tiles["trailing P/E"].kids[2].text() == "as of " + old


def test_figures_in_two_currencies_make_no_ratio_and_both_outputs_say_which_is_absent(tmp_path):
    """Net income in euros over revenue in dollars is not a margin, and a quarter in dollars
    against the same quarter a year earlier in euros is not growth. Both used to be computed
    from the bare numbers. The facts are absent now, and the note names them."""
    whole = bundles.bundle(seed=29)
    mixed = bundles.bundle(seed=29)
    for entry in mixed["statements"]["trailingNetIncome"]:
        entry["currency"] = "EUR"
    mixed["statements"]["quarterlyTotalRevenue"][0]["currency"] = "EUR"
    seen = {}
    for name, bundle in (("one currency", whole), ("two currencies", mixed)):
        folder = flow.copy_scripts(tmp_path / name)
        brief, report, _, sheet = whole_run(folder, bundle)
        tiles = [tile.kids[0].text() for tile in
                 page_of(folder).one("section", "fd").one("div", "fu").kids]
        seen[name] = (brief, flow.data_notes(report), sheet, tiles)
    brief, notes, sheet, tiles = seen["one currency"]
    assert {"margin_ttm", "rev_q_yoy", "ni_q_yoy", "debt_equity"} <= set(brief["facts"])
    assert brief["notes"] == [] and "net margin, trailing 12 months" in tiles
    brief, notes, sheet, tiles = seen["two currencies"]
    facts = brief["facts"]
    assert facts["ni_ttm"]["t"].endswith(" EUR") and facts["rev_ttm"]["t"].endswith(" USD")
    assert "margin_ttm" not in facts and "rev_q_yoy" not in facts
    # The same two figures in one currency still give their fact.
    assert "ni_q_yoy" in facts and "debt_equity" in facts
    note = ("The source gave no statement figure, so these facts are absent: margin_ttm, "
            "rev_q_yoy.")
    assert [n["text"] for n in brief["notes"]] == [note] and brief["flags"] == []
    assert flow.md(note) in notes and "n1 | " + note in sheet
    assert not any(line.startswith(("margin_ttm | ", "rev_q_yoy | ")) for line in sheet)
    assert "net margin, trailing 12 months" not in tiles
    assert "net income, trailing 12 months" in tiles


def test_the_checked_report_does_not_need_the_page_script(tmp_path):
    """A run that draws no page never writes page.py: the report and its check are whole."""
    folder = flow.copy_scripts(tmp_path / "no page", names=("get_data.py", "brief.py", "render.py"))
    pre = flow.run(folder / "brief.py", "--preflight", folder)
    assert (pre.rc, pre.err) == (0, "") and pre.lines == [
        flow.preflight_line(("brief.py", "get_data.py", "render.py"))]
    bundle = bundles.bundle()
    made, shown, drawn = flow.desk_run(folder, bundle, draft_for(bundle), fetch=True)
    assert made.rc == 0 and shown.rc == 0 and shown.err == ""
    # Python itself reports the missing script; nothing was drawn and the report stands.
    assert drawn.rc != 0 and not (folder / "report.html").exists()
    assert verify(folder).lines == ["VERIFY ok"]
    # The page script added later, checked by the preflight, draws from the saved files.
    flow.copy_scripts(folder, names=("page.py",))
    pre = flow.run(folder / "brief.py", "--preflight", folder)
    assert pre.lines == [flow.preflight_line(flow.NAMES)]
    drawn = flow.run(folder / "page.py", folder / "brief.json", folder / "report.json",
                     folder / "report.html")
    flow.check_report(folder, made, shown, drawn)


# --- integrity -------------------------------------------------------------------------

def test_single_character_edits_of_each_script_are_refused_by_the_preflight():
    # 40 edits of each of the four scripts, with another seed than the portable test uses.
    assert flow.NAMES == ("get_data.py", "brief.py", "render.py", "page.py")
    assert flow.check_mutations_are_refused(count=40, seed=7) >= 5


@pytest.mark.parametrize("name,old,new", [
    ("render.py", "returned -0.9% against +15.7%", "returned +0.9% against +15.7%"),
    ("render.py", "not investment advice", "sound investment advice"),
    ("brief.py", "at least 60", "at least 6"),
    ("brief.py", "if gap > (1 if crypto else 12):", "if gap > (1 if crypto else 120):"),
    ("render.py", "lost to buy-and-hold", "kept up with buy-and-hold"),
    ("render.py", "This covers sourcing and arithmetic, not judgment.",
     "This covers sourcing, arithmetic and judgment."),
    ("get_data.py", "query1.finance.yahoo.com", "query2.finance.yahoo.com"),
    ("page.py", '"rsi14": (0, 100, 30, 70, SWING)', '"rsi14": (0, 100, 40, 60, SWING)'),
    ("page.py", "Third-party text, shown as", "Checked text, shown as"),
])
def test_a_reworded_script_is_refused_before_it_can_print_a_report(desk, name, old, new):
    text = flow.read(desk / name)
    assert text.count(old) >= 1, old
    (desk / name).write_bytes(text.replace(old, new).encode("ascii"))
    pre = flow.run(desk / "brief.py", "--preflight", desk)
    assert pre.rc == 3 and pre.out == "", pre.out
    code = "E_SELF" if name == "brief.py" else "E_SIBLING " + name
    assert pre.err_lines[-1].startswith("ERROR " + code), pre.err
    if name != "get_data.py":
        # The changed script also refuses to do its own job.
        made, shown, drawn = flow.desk_run(desk, bundles.bundle(), flow.make_draft())
        failed = {"brief.py": made, "render.py": shown, "page.py": drawn}[name]
        assert failed.rc == 3 and failed.out == "", failed.out
        assert failed.err_lines[-1].startswith("ERROR E_SELF "), failed.err
        written = {"brief.py": "brief.json", "render.py": "report.md", "page.py": "report.html"}
        assert not (desk / written[name]).exists()


def test_the_stamps_in_the_tree_are_the_ones_the_build_computes():
    assert build_skill.stale_stamps() == [], "run: python skill/build_skill.py"
    assert build_skill.stale_known_answers() == [], "run: python skill/build_skill.py"


# --- the archive -----------------------------------------------------------------------

def test_the_scripts_inside_the_built_archive_run_the_same_desk(tmp_path, desk):
    archive = build_skill.build(dist_dir=tmp_path / "dist")
    with zipfile.ZipFile(archive) as z:
        tops = {n.split("/")[0] for n in z.namelist()}
        z.extractall(tmp_path / "unzipped")
    assert tops == {"trading-agents-desk"}
    shipped = tmp_path / "unzipped" / "trading-agents-desk" / "scripts"
    assert sorted(p.name for p in shipped.iterdir()) == sorted(flow.NAMES)
    folder = tmp_path / "from the archive"
    folder.mkdir()
    for name in flow.NAMES:
        (folder / name).write_bytes((shipped / name).read_bytes())
    pre = flow.run(folder / "brief.py", "--preflight", folder)
    assert pre.rc == 0 and pre.err == "", pre.err
    assert pre.lines == [flow.preflight_line(flow.NAMES)]

    bundle, draft = bundles.bundle(), flow.make_draft()
    for where in (folder, desk):
        made, shown, drawn = flow.desk_run(where, bundle, draft, fetch=True)
        flow.check_report(where, made, shown, drawn)
    for name in OUTPUTS:
        assert (folder / name).read_bytes() == (desk / name).read_bytes(), name
    assert json.loads(flow.read(folder / "brief.json"))["version"] == build_skill.SKILL_VERSION
    assert re.search(r"Script %s\." % re.escape(build_skill.SKILL_VERSION),
                     flow.read(folder / "report.md"))
