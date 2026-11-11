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
            "<small>as of %s</small>" % facts["mcap"]["as_of"], "<small>as of 2020-01-01</small>"),
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
        "to 2026-11-06. 50d and 200d are the 50-day and 200-day averages. Dashed lines are "
        "the report's levels.")


def test_shape_c_a_session_in_progress_with_no_bar_yet_is_not_called_behind(desk):
    """Only the row of the session that has just begun is empty: nothing is behind."""
    bundle = bundles.behind_case(holes=(-1,))
    brief, report, _, _ = whole_run(desk, bundle)
    assert (brief["as_of"], brief["session"], brief["flags"]) == ("2026-11-09", "finished", [])
    note = ("The source has no complete prices for 2026-11-10 yet, so this view ends at "
            "2026-11-09. That session was not over at the fetch.")
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
    assert "These prices came from a file, so the time they were downloaded is not known." in [
        note["text"] for note in brief["notes"]]
    # The session is not known to be over, so the draft may not call the price a close.
    closed = draft.replace("The price stood at {{last}}", "The stock closed at {{last}}")
    (folder / "closed.txt").write_bytes(closed.encode("ascii"))
    refused = flow.run(folder / "render.py", folder / "brief.json", folder / "closed.txt",
                       folder / "closed.md", folder / "closed.json")
    assert refused.rc == 1 and len(refused.lines) == 1, refused.out
    assert refused.lines[0].startswith("FIX why: D_NOTE "), refused.lines
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
