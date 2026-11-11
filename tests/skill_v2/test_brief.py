"""Tests for skill/trading-agents-desk/scripts/brief.py (CONTRACTS.md sections 1 and 3).

No test uses the network or the clock. Inputs come from bundles.py: generated numbers in the
real payload shapes.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPT = ROOT / "skill" / "trading-agents-desk" / "scripts" / "brief.py"
ORACLE = ROOT / "src" / "tradinglab" / "features" / "indicators.py"

ALLOWED_IMPORTS = {"sys", "json", "math", "re", "hashlib", "datetime", "html"}
KINDS = {"price", "pct", "level", "count", "ratio", "money", "date", "text"}
BRIEF_KEYS = ["schema", "version", "symbol", "name", "type", "currency", "unit", "source",
              "as_of", "fetched_at", "session", "data_id", "flags", "notes", "facts", "series",
              "headlines"]
INDICATORS = ["rsi14", "macd", "macd_signal", "macd_hist", "bb_pctb", "bb_upper", "bb_lower",
              "atr14", "atr14_pct", "adx14", "cci20", "stoch_k", "stoch_d", "stoch_j", "mfi14"]
STATEMENT_FACTS = ["pe_trailing", "pe_forward", "peg", "mcap", "rev_ttm", "ni_ttm", "margin_ttm",
                   "rev_q", "rev_q_yoy", "ni_q", "ni_q_yoy", "debt", "equity", "debt_equity"]
LONG_FACTS = ["sma200", "sma200_gap", "high52", "high52_date", "high52_gap", "low52",
              "low52_date", "low52_gap"]
ALL_FACTS = (
    ["last", "as_of", "prev_close", "chg1"]
    + ["%s.%d" % (k, n) for n in range(10)
       for k in ("date", "open", "high", "low", "close", "volume", "chg", "gap")]
    + INDICATORS
    + ["sma10", "sma10_gap", "sma50", "sma50_gap", "sma200", "sma200_gap"]
    + ["ret1", "ret5", "ret21", "ret63", "vol21"]
    + ["high52", "high52_date", "high52_gap", "low52", "low52_date", "low52_gap"]
    + ["vol_avg20", "vol_ratio", "move_max", "move_max_date"]
    + STATEMENT_FACTS)


def load(name, path):
    """Load a file as a module without leaving a __pycache__ folder beside a shipped script."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = before
    return mod


bundles = load("skill_v2_bundles", HERE / "bundles.py")
briefs = load("skill_v2_briefs_for_brief", HERE / "briefs.py")
brief = load("skill_v2_brief", SCRIPT)


class Run:
    def __init__(self, rc, out, err, doc):
        self.rc, self.out, self.doc = rc, out, doc
        # The development copy warns that it is not stamped yet; that line is not an error.
        self.err = [x for x in err.splitlines() if x != "WARN unstamped development copy"]

    @property
    def facts(self):
        return self.doc["facts"]

    def note_text(self):
        return " ".join(n["text"] for n in self.doc["notes"])


@pytest.fixture
def run(tmp_path, capsys):
    """Run brief.main in-process on a document, bytes or text; returns a Run."""
    def go(source, *args, name="in.json"):
        src = tmp_path / name
        if isinstance(source, bytes):
            src.write_bytes(source)
        elif isinstance(source, str):
            src.write_bytes(source.encode("utf-8"))
        else:
            src.write_bytes(bundles.dumps(source))
        out = tmp_path / "brief.json"
        if out.exists():
            out.unlink()
        capsys.readouterr()
        rc = brief.main(["brief.py", str(src), str(out)] + list(args))
        cap = capsys.readouterr()
        doc = json.loads(out.read_text(encoding="ascii")) if out.exists() else None
        return Run(rc, cap.out, cap.err, doc)
    return go


def assert_stop(r, code, *words):
    assert r.rc == 2, (r.rc, r.err)
    assert r.out == "" and r.doc is None
    assert len(r.err) == 1 and r.err[0].startswith("STOP %s " % code), r.err
    for w in words:
        assert w in r.err[0], (w, r.err[0])


def last_date(b, i=-1):
    off = b["chart"]["meta"]["gmtoffset"]
    return bundles.iso((b["chart"]["timestamp"][i] + off) // bundles.DAY)


# ---------------------------------------------------------------------------------------
# Rules for all scripts (section 1)
# ---------------------------------------------------------------------------------------

def test_script_follows_the_shared_rules():
    # A Windows checkout may hold CRLF; the shipped form is LF and that is what is measured.
    raw = SCRIPT.read_bytes().replace(b"\r\n", b"\n")
    assert len(raw) <= 20000, "brief.py is %d bytes; the hard limit is 20,000" % len(raw)
    assert b"\r" not in raw and b"\t" not in raw
    text = raw.decode("ascii")  # ASCII only
    assert not any(line != line.rstrip() for line in text.split("\n"))
    tree = ast.parse(text, feature_version=(3, 9))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported <= ALLOWED_IMPORTS, imported - ALLOWED_IMPORTS
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"eval", "exec", "compile", "__import__", "environ", "getenv", "system",
                        "popen", "socket", "urlopen", "now", "today", "time", "localtime"}
    # Exactly one open() for writing, and it writes the output path from the command line.
    writes = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
              and getattr(n.func, "id", "") == "open" and len(n.args) > 1
              and getattr(n.args[1], "value", "") != "rb"]
    assert len(writes) == 1 and ast.unparse(writes[0].args[0]) == "pos[1]"


def test_script_is_readable():
    """Section 1: one statement per line, lines of at most 100 characters, nothing packed."""
    text = SCRIPT.read_text(encoding="ascii")
    lines = text.split("\n")
    assert max(len(x) for x in lines) <= 100
    tree = ast.parse(text, feature_version=(3, 9))
    starts = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.stmt)]
    assert len(starts) == len(set(starts)), "two statements share a line"
    assert not any(isinstance(n, ast.JoinedStr) for n in ast.walk(tree)), "an f-string"
    # The file keeps its two-space indentation, and no line is a wall of code.
    assert all((len(x) - len(x.lstrip(" "))) % 2 == 0 for x in lines)
    code = [x for x in lines if x.strip() and not x.strip().startswith("#")]
    assert sum(len(x) for x in code) / len(code) < 60


def test_version(capsys):
    assert brief.main(["brief.py", "--version"]) == 0
    assert capsys.readouterr().out == "trading-agents-desk brief.py 2.0.0\n"


def test_fixed_texts_and_meter_scales_have_left_the_script(run):
    # Rev 3: render.py holds the test record and the disclaimer, page.py holds the scales.
    text = SCRIPT.read_text(encoding="ascii")
    for gone in ("investment advice", "buy-and-hold", "Yahoo", "SCALES", "DISCLAIMER",
                 "TEST_RECORD", "E_PENDING"):
        assert gone not in text, gone
    doc = run(bundles.bundle()).doc
    assert not {"bars", "scales", "disclaimer", "test_record"} & set(doc)


# ---------------------------------------------------------------------------------------
# Indicator parity with the package (the oracle)
# ---------------------------------------------------------------------------------------

def ref_fixed(x, places):
    """printf-style rounding done independently: half-even on the exact binary value."""
    q = Decimal(1).scaleb(-places)
    return format(Decimal(x).quantize(q, rounding=ROUND_HALF_EVEN), "f")


def ref_price(x):
    """Two decimals, or four significant figures below 10."""
    if abs(x) >= 10:
        return ref_fixed(x, 2)
    return ref_fixed(x, 3 - Decimal(abs(x)).adjusted())


def ref_pct(x, signed=True):
    return ("+" if signed and x >= 0 else "") + ref_fixed(x, 2) + "%"


def oracle_values(ind, pd, rows):
    """Newest-row values the way version 1 computed them (agents/graph.py), as percent
    where the brief shows percent."""
    df = pd.DataFrame([r[:5] for r in rows], columns=["open", "high", "low", "close", "volume"],
                      dtype="float64")
    last = ind.compute_indicator_frame(df).iloc[-1]
    closes = df["close"]
    close = float(closes.iloc[-1])
    want = {
        "rsi14": last["rsi_14"], "macd": last["macd"], "macd_signal": last["macd_signal"],
        "macd_hist": last["macd_hist"], "bb_pctb": last["bb_pctb"], "bb_upper": last["bb_upper"],
        "bb_lower": last["bb_lower"], "atr14": last["atr_14"],
        "atr14_pct": float(last["atr_14"]) / close * 100, "adx14": last["adx_14"],
        "cci20": last["cci_20"], "stoch_k": last["stoch_k"], "stoch_d": last["stoch_d"],
        "stoch_j": last["kdj_j"], "mfi14": last["mfi_14"],
    }
    for n in (10, 50, 200):
        sma = float(last["sma_%d" % n])
        want["sma%d" % n] = sma
        want["sma%d_gap" % n] = (close / sma - 1.0) * 100
    for n in (1, 5, 21, 63):
        ok = len(closes) > n
        want["ret%d" % n] = float(closes.iloc[-1] / closes.iloc[-1 - n] - 1.0) * 100 if ok \
            else float("nan")
    want["vol21"] = float(closes.pct_change().tail(21).std() * math.sqrt(252)) * 100
    year = closes.tail(252)
    hi, lo = float(year.max()), float(year.min())
    nan = float("nan")
    long_enough = len(closes) >= 200
    want["high52"], want["low52"] = (hi, lo) if long_enough else (nan, nan)
    want["high52_gap"] = (close / hi - 1.0) * 100 if long_enough else nan
    want["low52_gap"] = (close / lo - 1.0) * 100 if long_enough else nan
    return {k: float(v) for k, v in want.items()}, close


# How each fact is shown, and the size against which a difference is judged when the value
# itself is a small difference of large numbers.
PRICE_LIKE = {"macd", "macd_signal", "macd_hist", "bb_upper", "bb_lower", "atr14", "sma10",
              "sma50", "sma200", "high52", "low52"}
UNSIGNED_PCT = {"atr14_pct", "vol21"}
TWO_DECIMALS = {"rsi14", "bb_pctb", "adx14", "cci20", "stoch_k", "stoch_d", "stoch_j", "mfi14"}


def display(name, w):
    if name in TWO_DECIMALS:
        return ref_fixed(w, 2)
    if name in PRICE_LIKE:
        return ref_price(w)
    return ref_pct(w, signed=name not in UNSIGNED_PCT)


def test_indicators_match_the_package_on_200_series():
    pd = pytest.importorskip("pandas")
    ind = load("skill_v2_oracle", ORACLE)
    checked = ties = 0
    worst = 0.0
    for seed in range(200):
        rng = random.Random(9000 + seed)
        n = 60 if seed == 0 else 400 if seed == 1 else rng.randint(60, 400)
        start = rng.choice([0.4, 3.0, 8.0, 25.0, 180.0, 3600.0, 70000.0])
        rows = bundles.walk(seed, n, start=start, vol=rng.choice([0.008, 0.02, 0.045]),
                            digits=4 if start < 10 else 2)
        doc = bundles.bundle(rows=rows)
        out, _ = brief.build(*brief.parse(json.dumps(doc), {}))
        facts = out["facts"]
        want, close = oracle_values(ind, pd, rows)
        for name, w in want.items():
            if math.isnan(w):
                assert name not in facts, (seed, n, name)
                continue
            assert name in facts, (seed, n, name)
            got = facts[name]
            scale = close if name in PRICE_LIKE else 100.0 if name in TWO_DECIMALS else 1.0
            assert math.isclose(got["v"], w, rel_tol=1e-9, abs_tol=1e-12 * scale), \
                (seed, n, name, got["v"], w)
            worst = max(worst, abs(got["v"] - w) / abs(w)) if w else worst
            assert "e" not in got["t"].lower()
            checked += 1
            # An average of two-decimal prices can sit exactly on a rounding step. Two
            # values that agree to 1e-9 may then print differently, so the text is only
            # compared when the oracle's value settles it.
            if display(name, w * (1 - 2e-9)) != display(name, w * (1 + 2e-9)):
                ties += 1
                continue
            assert got["t"] == display(name, w), (seed, n, name, got["t"], w)
    assert checked > 200 * 25
    assert ties < checked / 100
    assert worst < 1e-9


def test_series_averages_match_the_package():
    pd = pytest.importorskip("pandas")
    ind = load("skill_v2_oracle", ORACLE)
    rows = bundles.walk(77, 260)
    out, _ = brief.build(*brief.parse(json.dumps(bundles.bundle(rows=rows)), {}))
    closes = pd.Series([r[3] for r in rows], dtype="float64")
    for n in (50, 200):
        want = ind.sma(closes, n).tail(120).tolist()
        got = out["series"]["sma%d" % n]
        assert len(got) == 120
        for g, w in zip(got, want):
            assert (g is None and math.isnan(w)) or math.isclose(g, w, rel_tol=1e-9)
    assert out["series"]["sma200"][0] is None and out["series"]["sma200"][-1] is not None


# ---------------------------------------------------------------------------------------
# Validation table, in the contract's order
# ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("source", [
    b"", b"   \n", b"not a file this script knows", b"{", b"[1, 2, 3]", b"12", b'"text"',
    b'{"schema": "bundle.v2"}', b'{"schema": "bundle.v1"}', b'{"history": "nope"}',
    b'{"schema": "bundle.v1", "symbol": "A", "chart": {"timestamp": [1], "open": []}}',
    b"day,open,high,low,close,volume\n2026-11-10,1,2,0.5,1.5,10\n",
    b"date,open,high,low,close,volume\nyesterday,1,2,0.5,1.5,10\n",
    b"date,open,high,low,close,volume\n2026-11-10,1,2,0.5,abc,10\n",
    b"date,open,high,low,close,volume\n2026-11-10,1,2\n",
    b"\xff\xfe\x00\xd8",  # a UTF-16 mark followed by half a character
    b"\x80\x81\x82",
])
def test_e_input_for_anything_that_is_not_one_of_the_three_shapes(run, source):
    assert_stop(run(source, "--symbol", "TEST"), "E_INPUT", "date,open,high,low,close,volume")


def test_e_input_for_a_bundle_without_its_fetch_time(run):
    b = bundles.bundle()
    del b["fetched_at"]
    assert_stop(run(b), "E_INPUT")


def test_e_input_when_columns_differ_in_length(run):
    b = bundles.bundle()
    b["chart"]["volume"].pop()
    assert_stop(run(b), "E_INPUT")


@pytest.mark.parametrize("typ", ["CURRENCY", "FUTURE", "OPTION", "equity"])
def test_e_type_for_unsupported_instruments(run, typ):
    assert_stop(run(bundles.bundle(typ=typ)), "E_TYPE", typ)


def test_e_type_names_the_type_in_a_few_plain_characters(run):
    # The type is the source's text and the user is shown this line unchanged.
    r = run(bundles.bundle(typ="FUTURE\x1b[2J\x00\nSTOP E_X " + "x" * 5000))
    assert_stop(r, "E_TYPE", "FUTURE")
    assert len(r.err[0]) < 120 and not re.search(r"[\x00-\x1f\x7f]", r.err[0])


@pytest.mark.parametrize("typ", ["EQUITY", "ETF", "INDEX", "MUTUALFUND", None])
def test_supported_and_unknown_types_pass(run, typ):
    b = bundles.bundle(typ=typ)
    if typ is None:
        del b["chart"]["meta"]["instrumentType"]
    r = run(b)
    assert r.rc == 0 and r.doc["type"] == (typ or "UNKNOWN")


def test_e_type_comes_before_the_row_checks(run):
    b = bundles.bad_prices("negative")
    b["chart"]["meta"]["instrumentType"] = "CURRENCY"
    assert_stop(run(b), "E_TYPE")


# Flags the row rules can set. The other flags describe statements, headlines and length.
ROW_FLAGS = {"close_filled", "prices_behind", "rows_dropped", "history_gap"}
BEHIND = "The source has no complete prices for %s yet, so this view ends at %s."
QUOTE = " The source's last regular price is %s, dated %s."
NOT_OVER = " Its prices were not final at the fetch."
UNFINISHED = ("The session of %s was unfinished at the fetch: its price, range and volume are "
              "not final.")
NO_FIGURE = "The source gave no statement figure, so these facts are absent: %s."
REQUEST_FAILED = "The statement request failed, so these facts are absent: %s."
NO_HEADLINES = "No headlines came with this data."
HEADLINES_FAILED = "The headline request failed."


def row_flags(r):
    return sorted(f for f in r.doc["flags"] if f in ROW_FLAGS)


def notes_with(r, words):
    return [n["text"] for n in r.doc["notes"] if words in n["text"]]


def quote_text(b):
    """The source's last regular price and its exchange date, as the behind note prints them."""
    meta = b["chart"]["meta"]
    day = (meta["regularMarketTime"] + meta["gmtoffset"]) // bundles.DAY
    return QUOTE % (brief.f_price(meta["regularMarketPrice"]), bundles.iso(day))


def assert_behind(r, b, first, ends, quote=True):
    """The view was cut before row ``first`` and now ends at row ``ends``, flagged behind."""
    assert r.rc == 0 and "prices_behind" in r.doc["flags"]
    assert r.doc["as_of"] == last_date(b, ends) and r.doc["session"] == "finished"
    assert r.facts["last"]["label"] == "last close"
    assert r.facts["last"]["v"] == b["chart"]["close"][ends]
    want = BEHIND % (last_date(b, first), last_date(b, ends)) + (quote_text(b) if quote else "")
    assert notes_with(r, "no complete prices") == [want]
    cut = {last_date(b, i) for i in range(first, 0)}
    assert not cut & set(r.doc["series"]["date"])


def assert_not_behind(r, b, first, ends):
    """The session in progress has no row yet: the view ends the row before, with no flag."""
    assert r.rc == 0 and "prices_behind" not in r.doc["flags"]
    assert r.doc["as_of"] == last_date(b, ends) and r.doc["session"] == "finished"
    want = BEHIND % (last_date(b, first), last_date(b, ends)) + NOT_OVER
    assert notes_with(r, "no complete prices") == [want]
    assert "unfinished_session" not in r.doc["flags"]


def test_null_rows_are_dropped_each_with_a_note(run):
    b = bundles.null_rows((-40, -7))
    r = run(b)
    assert r.rc == 0
    assert row_flags(r) == ["rows_dropped"]
    dropped = [last_date(b, -40), last_date(b, -7)]
    texts = [n["text"] for n in r.doc["notes"]]
    for d in dropped:
        assert sum(d in t and "left out" in t for t in texts) == 1
    assert not set(dropped) & set(r.doc["series"]["date"])
    assert r.doc["as_of"] == last_date(b)
    clean = run(bundles.bundle(seed=11))
    assert r.doc["data_id"] != clean.doc["data_id"]


def test_a_row_missing_only_its_volume_is_dropped_too(run):
    b = bundles.bundle(seed=11)
    b["chart"]["volume"][-5] = None
    r = run(b)
    assert r.rc == 0 and row_flags(r) == ["rows_dropped"]
    assert last_date(b, -5) in r.note_text()


# --- Shape A: a normal history ----------------------------------------------------------

@pytest.mark.parametrize("gmtoffset", [-18000, 32400])
def test_shape_a_every_row_complete_after_the_close(run, gmtoffset):
    # New York and Tokyo after the close. The source's calendar already describes the next
    # session, and that must not make the finished one look unfinished.
    b = bundles.bundle(seed=3, gmtoffset=gmtoffset)
    ch = b["chart"]
    period = ch["meta"]["currentTradingPeriod"]["regular"]
    assert ch["timestamp"][-1] < b["fetched_at"] < period["start"]
    r = run(b)
    assert r.rc == 0 and r.doc["flags"] == [] and r.doc["notes"] == []
    assert r.doc["session"] == "finished" and r.doc["as_of"] == last_date(b)
    assert r.facts["last"]["v"] == ch["close"][-1] == ch["meta"]["regularMarketPrice"]
    assert len(r.doc["series"]["date"]) == 120


# --- Shape D: same day ------------------------------------------------------------------

def twin(b, i=-1, later=3600, **changes):
    """Add a second row for the exchange date of row ``i``, stamped ``later`` seconds after
    it, with the same values unless ``changes`` names others."""
    ch = b["chart"]
    at = len(ch["timestamp"]) + i
    ch["timestamp"].insert(at + 1, ch["timestamp"][at] + later)
    for k in bundles.COLS:
        ch[k].insert(at + 1, changes.get(k, ch[k][at]))
    return at


@pytest.mark.parametrize("trade_first", [False, True])
def test_shape_d_two_rows_for_one_exchange_date_become_one(run, trade_first):
    b = bundles.same_day_case(trade_first=trade_first)
    ch, off = b["chart"], b["chart"]["meta"]["gmtoffset"]
    stamps = sorted(ch["timestamp"][-2:])
    assert (stamps[0] + off) // 86400 == (stamps[1] + off) // 86400
    # In universal time the session start falls on the day before: the exchange date counts.
    assert stamps[0] // 86400 != stamps[1] // 86400
    closes = [c for c in ch["close"][-2:] if c is not None]
    assert len(closes) == 1
    r = run(b)
    assert r.rc == 0 and r.err == []
    assert r.doc["flags"] == [] and r.doc["notes"] == []  # nothing is said
    assert r.doc["session"] == "finished" and r.doc["as_of"] == "2026-11-10"
    assert r.facts["last"]["v"] == r.facts["close.0"]["v"] == closes[0]
    assert r.facts["date.1"]["v"] == "2026-11-09"
    dates = r.doc["series"]["date"]
    assert len(dates) == len(set(dates)) == 120
    # The brief is the one the history gives with the complete row alone.
    alone = run(bundles.bundle(seed=32, session="after_close", currency="AUD",
                               symbol="TEST.AX", gmtoffset=39600))
    assert r.doc["facts"] == alone.doc["facts"] and r.doc["data_id"] == alone.doc["data_id"]
    assert r.doc["series"] == alone.doc["series"]


def test_same_day_while_the_session_runs(run):
    # The same two rows during the session: one row, and it is the unfinished one.
    b = bundles.same_day_case()
    regular = b["chart"]["meta"]["currentTradingPeriod"]["regular"]
    b["fetched_at"] = regular["end"] - 60
    r = run(b)
    assert r.rc == 0 and row_flags(r) == [] and r.doc["session"] == "unfinished"
    assert r.facts["last"]["v"] == [c for c in b["chart"]["close"][-2:] if c][0]


def test_same_day_keeps_a_complete_row_over_a_newer_incomplete_one(run):
    b = bundles.bundle(seed=33)
    want = run(b)
    twin(b, close=None)
    r = run(b)
    assert r.rc == 0 and r.doc["flags"] == [] and r.doc["notes"] == []
    assert r.doc["facts"] == want.doc["facts"] and r.doc["data_id"] == want.doc["data_id"]


def test_same_day_keeps_the_newest_of_two_complete_rows(run):
    b = bundles.bundle(seed=33)
    ch = b["chart"]
    newer = round((ch["high"][-1] + ch["low"][-1]) / 2, 2)
    assert newer != ch["close"][-1]
    twin(b, close=newer)
    r = run(b)
    assert r.rc == 0 and r.doc["flags"] == [] and r.doc["notes"] == []
    assert r.facts["last"]["v"] == newer and r.doc["as_of"] == "2026-11-10"
    # The order in the file does not decide: the time stamps do.
    for k in ("timestamp",) + bundles.COLS:
        ch[k][-2:] = ch[k][-2:][::-1]
    assert run(b).facts["last"]["v"] == newer


def test_same_day_keeps_the_newest_of_two_incomplete_rows(run):
    # Neither row is complete, so the newest stays and the later rules judge it: it has no
    # close, a volume and a last regular price of its day, so its close is filled.
    b = bundles.fill_case()
    ch = b["chart"]
    volume = ch["volume"][-1] + 1000
    twin(b, volume=volume)
    ch["volume"][-2] = None
    r = run(b)
    assert r.rc == 0 and row_flags(r) == ["close_filled"]
    assert r.facts["volume.0"]["v"] == volume and r.facts["date.1"]["v"] == "2026-11-09"
    # With the rows the other way round in time, the newest lacks its volume: no fill.
    b = bundles.fill_case()
    twin(b, volume=None)
    r = run(b)
    assert row_flags(r) == ["prices_behind"] and r.doc["as_of"] == "2026-11-09"
    assert len(notes_with(r, "2026-11-10")) == 1


def test_same_day_with_three_rows(run):
    b = bundles.bundle(seed=33)
    ch = b["chart"]
    second = round((ch["high"][-1] + ch["low"][-1]) / 2, 2)
    twin(b, later=3600, close=second)
    twin(b, later=7200, close=None)
    r = run(b)
    assert r.rc == 0 and r.doc["flags"] == [] and r.doc["notes"] == []
    assert r.facts["last"]["v"] == second
    assert r.facts["date.1"]["v"] == "2026-11-09"


def test_same_day_in_the_middle_of_the_history(run):
    b = bundles.bundle(seed=33)
    want = run(b)
    twin(b, i=-30, close=None)
    r = run(b)
    assert r.doc["flags"] == [] and r.doc["notes"] == []
    assert r.doc["data_id"] == want.doc["data_id"]
    # A row stamped on its neighbour's date takes that date's place and leaves its own
    # date without a row. Nothing stops and nothing is said.
    b = bundles.bundle()
    lost = last_date(b, -3)
    b["chart"]["timestamp"][-3] = b["chart"]["timestamp"][-4] + 60
    r = run(b)
    assert r.rc == 0 and r.doc["flags"] == [] and r.doc["notes"] == []
    assert lost not in r.doc["series"]["date"]
    assert r.facts["close.2"]["v"] == b["chart"]["close"][-3]
    assert r.facts["date.2"]["v"] == last_date(b, -4)


def test_rows_in_any_order_give_the_same_brief(run):
    # Rows are put in time order before any rule, so no order of the input can stop the run.
    b = bundles.bundle(seed=33)
    want = run(b)
    order = list(range(len(b["chart"]["timestamp"])))
    random.Random(8).shuffle(order)
    for k in ("timestamp",) + bundles.COLS:
        b["chart"][k] = [b["chart"][k][i] for i in order]
    r = run(b)
    assert r.rc == 0 and r.doc["facts"] == want.doc["facts"]
    assert r.doc["data_id"] == want.doc["data_id"] and r.doc["series"] == want.doc["series"]


# --- Shape B: fill ----------------------------------------------------------------------

@pytest.mark.parametrize("kw", [
    {}, {"gmtoffset": 0, "currency": "GBp", "start": 3600.0}, {"gmtoffset": 3600}])
def test_shape_b_fill_from_the_last_regular_price(run, kw):
    b = bundles.fill_case(**kw)
    ch = b["chart"]
    # Fetched between that session's close and the next open.
    regular = ch["meta"]["currentTradingPeriod"]["regular"]
    assert ch["timestamp"][-1] < b["fetched_at"] < regular["start"]
    r = run(b)
    assert r.rc == 0
    assert row_flags(r) == ["close_filled"]
    price = ch["meta"]["regularMarketPrice"]
    assert r.facts["last"]["v"] == price and r.facts["close.0"]["v"] == price
    assert r.doc["as_of"] == last_date(b) and r.doc["session"] == "finished"
    assert r.facts["last"]["label"] == "last close"
    notes = notes_with(r, "filled")
    assert len(notes) == 1 and last_date(b) in notes[0]
    # The rest of the row is the source's own, and the earlier rows are untouched.
    for k in ("open", "high", "low", "volume"):
        assert r.facts[k + ".0"]["v"] == ch[k][-1]
    assert r.facts["prev_close"]["v"] == ch["close"][-2]
    assert math.isclose(r.facts["chg1"]["v"], (price / ch["close"][-2] - 1) * 100)


def test_a_filled_close_counts_like_a_close_the_source_gave(run):
    # The last regular price may be a whole number in the source's answer. The brief and
    # its data id are then the ones the same history gives with that close in the row.
    b = bundles.fill_case()
    ch = b["chart"]
    whole = int((ch["high"][-1] + ch["low"][-1]) / 2)
    assert ch["low"][-1] <= whole <= ch["high"][-1]
    ch["meta"]["regularMarketPrice"] = whole
    filled = run(b)
    ch["close"][-1] = float(whole)
    plain = run(b)
    assert filled.doc["flags"] == ["close_filled"] and plain.doc["flags"] == []
    assert filled.doc["data_id"] == plain.doc["data_id"]
    assert filled.doc["facts"] == plain.doc["facts"]
    assert filled.doc["series"] == plain.doc["series"]


def assert_not_filled(r, b):
    assert r.rc == 0 and "close_filled" not in r.doc["flags"]
    assert r.doc["as_of"] == last_date(b, -2) and not notes_with(r, "filled")
    assert r.facts["last"]["v"] == b["chart"]["close"][-2]


@pytest.mark.parametrize("column", ["open", "high", "low", "volume"])
def test_no_fill_when_the_row_lacks_more_than_its_close(run, column):
    b = bundles.fill_case()
    b["chart"][column][-1] = None
    r = run(b)
    assert_not_filled(r, b)
    assert row_flags(r) == ["prices_behind"]


@pytest.mark.parametrize("column", ["open", "high", "low", "volume"])
def test_no_fill_for_a_row_that_has_its_close_and_lacks_something_else(run, column):
    # Everything else would allow a fill: only the close that is there forbids it.
    b = bundles.bundle(seed=12)
    b["chart"][column][-1] = None
    r = run(b)
    assert r.rc == 0 and row_flags(r) == ["prices_behind"]
    assert r.doc["as_of"] == last_date(b, -2) and not notes_with(r, "filled")


def test_fill_is_for_the_newest_row_only(run):
    # The row before the newest lacks its close and the last regular price is of its day.
    b = bundles.bundle(seed=12)
    ch = b["chart"]
    ch["close"][-2] = None
    ch["meta"]["regularMarketPrice"] = round((ch["high"][-2] + ch["low"][-2]) / 2, 2)
    ch["meta"]["regularMarketTime"] = ch["timestamp"][-2] + 3600
    r = run(b)
    assert_behind(r, b, -2, -3)
    assert row_flags(r) == ["prices_behind"]


def test_fill_compares_exchange_dates_not_universal_ones(run):
    # Sydney: the row is stamped at the session start, the evening before in universal
    # time, and the last regular price at the close, which is the next universal day.
    b = bundles.fill_case(gmtoffset=39600)
    ch = b["chart"]
    assert ch["timestamp"][-1] // 86400 != ch["meta"]["regularMarketTime"] // 86400
    assert row_flags(run(b)) == ["close_filled"]
    # A price from late on the exchange's previous day shares the row's universal day.
    ch["meta"]["regularMarketTime"] = ch["timestamp"][-1] - 37800
    assert ch["timestamp"][-1] // 86400 == ch["meta"]["regularMarketTime"] // 86400
    r = run(b)
    assert_not_filled(r, b)
    assert_behind(r, b, -1, -2)
    # New York: a price stamped at eight in the evening is the next universal day.
    b = bundles.fill_case()
    ch = b["chart"]
    ch["meta"]["regularMarketTime"] = ch["timestamp"][-1] + 37800
    assert ch["timestamp"][-1] // 86400 != ch["meta"]["regularMarketTime"] // 86400
    assert row_flags(run(b)) == ["close_filled"]


def test_fill_accepts_a_price_a_rounding_step_outside_the_range(run):
    # Bars arrive as 32-bit floats while the last regular price is a rounded decimal.
    b = bundles.fill_case()
    b["chart"]["meta"]["regularMarketPrice"] = round(b["chart"]["high"][-1] * (1 + 4e-7), 6)
    r = run(b)
    assert r.rc == 0 and row_flags(r) == ["close_filled"]


@pytest.mark.parametrize("column, factor, filled", [
    ("high", 1.0, True), ("high", 1.00009, True), ("high", 1.00011, False),
    ("high", 1.01, False), ("low", 1.0, True), ("low", 0.99991, True),
    ("low", 0.99989, False), ("low", 0.99, False)])
def test_fill_needs_the_price_within_a_hundredth_of_a_percent_of_the_range(
        run, column, factor, filled):
    b = bundles.fill_case()
    price = b["chart"][column][-1] * factor
    b["chart"]["meta"]["regularMarketPrice"] = price
    r = run(b)
    if filled:
        # The filled row is then a possible row: it does not stop as E_OHLC.
        assert r.rc == 0 and row_flags(r) == ["close_filled"] and r.facts["last"]["v"] == price
    else:
        assert_not_filled(r, b)
        assert_behind(r, b, -1, -2)


def test_no_fill_when_the_price_is_from_another_day(run):
    b = bundles.fill_wrong_day()
    r = run(b)
    assert_not_filled(r, b)
    assert_behind(r, b, -1, -2)
    assert row_flags(r) == ["prices_behind"]
    # Nor from the day after.
    b = bundles.fill_case()
    b["chart"]["meta"]["regularMarketTime"] += bundles.DAY
    assert_not_filled(run(b), b)


@pytest.mark.parametrize("key", ["regularMarketPrice", "regularMarketTime"])
def test_no_fill_without_the_price_or_its_time(run, key):
    for value in ("absent", None, 0):
        b = bundles.fill_case()
        if value == "absent":
            del b["chart"]["meta"][key]
        else:
            b["chart"]["meta"][key] = value
        r = run(b)
        assert_not_filled(r, b)
        assert_behind(r, b, -1, -2, quote=False)


def test_no_fill_while_the_session_is_unfinished(run):
    b = bundles.bundle(seed=12, session="unfinished")
    ch = b["chart"]
    ch["meta"]["regularMarketPrice"] = round((ch["high"][-1] + ch["low"][-1]) / 2, 2)
    ch["close"][-1] = None
    r = run(b)
    assert_not_filled(r, b)
    assert_not_behind(r, b, -1, -2)
    assert row_flags(r) == []
    # Fetched at the regular end with a quote from two hours into the session, it is not
    # over yet: the source's quote can be a quarter of an hour old.
    end = ch["meta"]["currentTradingPeriod"]["regular"]["end"]
    assert ch["meta"]["regularMarketTime"] < end
    b["fetched_at"] = end
    r = run(b)
    assert_not_filled(r, b)
    assert_not_behind(r, b, -1, -2)
    # The same row once the session is over is filled: the session alone decided. Over is
    # half an hour after the end, or sooner with a quote stamped at the end.
    b["fetched_at"] = end + 1800
    assert row_flags(run(b)) == ["close_filled"]
    b["fetched_at"], ch["meta"]["regularMarketTime"] = end + 60, end
    assert row_flags(run(b)) == ["close_filled"]


def test_no_fill_when_the_session_is_unknown(run):
    b = bundles.fill_case()
    del b["chart"]["meta"]["currentTradingPeriod"]
    r = run(b)
    assert_not_filled(r, b)
    assert row_flags(r) == ["prices_behind"]


def test_no_fill_for_crypto(run):
    b = bundles.crypto(session="finished")
    ch = b["chart"]
    ch["meta"]["regularMarketPrice"] = round((ch["high"][-1] + ch["low"][-1]) / 2, 2)
    ch["close"][-1] = None
    r = run(b)
    assert_not_filled(r, b)
    assert row_flags(r) == ["prices_behind"]
    # The same rows as a share are filled: the type alone decided.
    ch["meta"]["instrumentType"] = "EQUITY"
    assert row_flags(run(b)) == ["close_filled"]


# --- Shapes C and E: behind -------------------------------------------------------------

@pytest.mark.parametrize("quote, gmtoffset", [
    ("today", 0), ("yesterday", 3600), ("today", 39600), ("yesterday", -18000)])
def test_shape_c_the_finished_sessions_row_is_empty_after_the_next_open(run, quote, gmtoffset):
    # London and Frankfurt minutes after the open: the finished session's row is empty and
    # so is the row of the session that has just begun. (The other two offsets put the
    # price's time stamp on another universal day than its exchange date.)
    b = bundles.behind_case(quote=quote, gmtoffset=gmtoffset)
    ch = b["chart"]
    regular = ch["meta"]["currentTradingPeriod"]["regular"]
    assert regular["start"] == ch["timestamp"][-1] < b["fetched_at"] < regular["end"]
    assert all(ch[k][i] is None for k in bundles.COLS for i in (-2, -1))
    r = run(b)
    assert_behind(r, b, -2, -3)
    assert row_flags(r) == ["prices_behind"]
    assert "unfinished_session" not in r.doc["flags"]
    note = notes_with(r, "no complete prices")[0]
    assert "2026-11-09" in note and "2026-11-06" in note
    assert ("dated 2026-11-10." if quote == "today" else "dated 2026-11-09.") in note
    assert brief.f_price(ch["meta"]["regularMarketPrice"]) in note


def test_shape_c_later_the_new_sessions_row_has_values(run):
    # Expected later in the same session: the finished session's row still empty while the
    # running session's row has values. Both rows are cut all the same.
    b = bundles.behind_case(holes=(-2,))
    assert None not in [b["chart"][k][-1] for k in bundles.COLS]
    r = run(b)
    assert_behind(r, b, -2, -3)
    assert row_flags(r) == ["prices_behind"]


def test_behind_reaches_three_rows_back_and_no_further(run):
    b = bundles.behind_case(holes=(-3,))
    r = run(b)
    assert_behind(r, b, -3, -4)
    assert row_flags(r) == ["prices_behind"]
    # The fourth row from the end is an older row: it is left out and the view goes on.
    b = bundles.behind_case(holes=(-4,))
    r = run(b)
    assert r.rc == 0 and row_flags(r) == ["rows_dropped"]
    assert r.doc["as_of"] == last_date(b) and r.doc["session"] == "unfinished"
    # The one-day figures next to a dropped date run across it, and the note says so.
    assert notes_with(r, "left out") == [
        "The row for %s had a missing value and was left out. Changes beside that date span "
        "two sessions." % last_date(b, -4)]


@pytest.mark.parametrize("column", bundles.COLS)
def test_behind_for_any_missing_value(run, column):
    b = bundles.behind_case(holes=())
    b["chart"][column][-2] = None
    assert_behind(run(b), b, -2, -3)


def test_older_holes_are_left_out_and_the_view_ends_at_the_newest_kept_row(run):
    b = bundles.behind_case(holes=(-40, -4, -3))
    r = run(b)
    assert_behind(r, b, -3, -5)
    assert row_flags(r) == ["prices_behind", "rows_dropped"]
    left_out = notes_with(r, "left out")
    assert len(left_out) == 2 and last_date(b, -40) in left_out[0]
    assert last_date(b, -4) in left_out[1]


def test_a_session_in_progress_without_a_row_is_not_behind(run):
    b = bundles.behind_case(holes=(-1,))
    r = run(b)
    assert_not_behind(r, b, -1, -2)
    assert row_flags(r) == [] and "last regular price" not in r.note_text()
    assert r.facts["last"]["v"] == b["chart"]["close"][-2]


def test_in_progress_means_at_or_after_the_start_and_not_yet_over(run):
    b = bundles.behind_case(holes=(-1,))
    meta = b["chart"]["meta"]
    regular = meta["currentTradingPeriod"]["regular"]
    assert regular["start"] == b["chart"]["timestamp"][-1]
    assert meta["regularMarketTime"] < regular["start"]  # the quote is from before this open
    assert_not_behind(run(b), b, -1, -2)
    # One second before the session's start, the empty row is a finished session's.
    late = bundles.clone(b)
    late["chart"]["meta"]["currentTradingPeriod"]["regular"]["start"] += 1
    assert_behind(run(late), late, -1, -2)
    # With a quote from before the end, the session is over half an hour after its end and
    # not one second earlier: the fetch time alone no longer ends it.
    done = bundles.clone(b)
    for after, over in ((-1, False), (0, False), (240, False), (1799, False), (1800, True)):
        done["fetched_at"] = regular["end"] + after
        (assert_behind if over else assert_not_behind)(run(done), done, -1, -2)
    # A quote stamped at the end ends it at once; one stamped a second earlier does not.
    done["fetched_at"] = regular["end"] + 60
    done["chart"]["meta"]["regularMarketTime"] = regular["end"]
    assert_behind(run(done), done, -1, -2)
    done["chart"]["meta"]["regularMarketTime"] = regular["end"] - 1
    assert_not_behind(run(done), done, -1, -2)
    # Without a quote time the fetch time decides, as it did before: over at the end.
    del done["chart"]["meta"]["regularMarketTime"]
    done["fetched_at"] = regular["end"]
    assert_behind(run(done), done, -1, -2, quote=False)
    done["fetched_at"] = regular["end"] - 1
    assert_not_behind(run(done), done, -1, -2)


@pytest.mark.parametrize("gone", ["currentTradingPeriod", "regular", "start", "end"])
def test_missing_session_facts_count_as_a_finished_session(run, gone):
    b = bundles.behind_case(holes=(-1,))
    meta = b["chart"]["meta"]
    if gone == "currentTradingPeriod":
        del meta[gone]
    elif gone == "regular":
        del meta["currentTradingPeriod"][gone]
    else:
        del meta["currentTradingPeriod"]["regular"][gone]
    r = run(b)
    assert r.rc == 0 and row_flags(r) == ["prices_behind"]
    assert r.doc["as_of"] == last_date(b, -2) and r.doc["session"] == "unknown"
    assert notes_with(r, "no complete prices") == [
        BEHIND % (last_date(b), last_date(b, -2)) + quote_text(b)]


@pytest.mark.parametrize("gone", ["both", "regularMarketPrice", "regularMarketTime"])
def test_behind_note_leaves_the_price_out_when_the_source_gives_none(run, gone):
    b = bundles.behind_case(quote="none" if gone == "both" else "today")
    if gone != "both":
        del b["chart"]["meta"][gone]
    r = run(b)
    assert_behind(r, b, -2, -3, quote=False)
    assert "last regular price" not in r.note_text()


def test_shape_e_crypto_with_an_empty_row_for_the_finished_day(run):
    b = bundles.behind_crypto()
    ch = b["chart"]
    assert ch["close"][-2] is None and ch["close"][-1] is not None
    assert ch["timestamp"][-1] // 86400 == b["fetched_at"] // 86400
    r = run(b)
    assert_behind(r, b, -2, -3)
    assert row_flags(r) == ["prices_behind"] and r.doc["type"] == "CRYPTOCURRENCY"
    assert "unfinished_session" not in r.doc["flags"]


def test_shape_e_crypto_with_the_finished_day_absent_is_a_gap(run):
    b = bundles.behind_crypto(absent=True)
    assert_stop(run(b), "E_GAP", last_date(b, -2), last_date(b, -1))


def test_crypto_day_in_progress_without_a_row_is_not_behind(run):
    b = bundles.crypto(session="unfinished")
    for k in bundles.COLS:
        b["chart"][k][-1] = None
    r = run(b)
    assert_not_behind(r, b, -1, -2)
    assert row_flags(r) == []
    # Once that day is over (fetched on the next UTC day) the empty row is a finished day's.
    b["fetched_at"] = (b["chart"]["timestamp"][-1] // 86400 + 1) * 86400
    r = run(b)
    assert_behind(r, b, -1, -2)


def test_behind_for_a_price_file(run):
    # A price file has no session facts and no last regular price.
    b = bundles.bundle(seed=5)
    b["chart"]["close"][-1] = None
    for source, args in ((bundles.csv_text(b), ["--symbol", "TEST"]),
                         (bundles.history_result(b), [])):
        r = run(source, *args, name="in.csv" if args else "in.json")
        assert r.rc == 0 and row_flags(r) == ["prices_behind"]
        assert r.doc["as_of"] == last_date(b, -2) and r.doc["session"] == "unknown"
        assert notes_with(r, "no complete prices") == [
            BEHIND % (last_date(b), last_date(b, -2))]


def test_fact_sheet_shows_the_row_notes_and_flags(run):
    cases = ((bundles.behind_case(), "prices_behind"), (bundles.calendar_gap(8), "history_gap"),
             (bundles.fill_case(), "close_filled"), (bundles.null_rows(), "rows_dropped"),
             (bundles.behind_case(holes=(-1,)), None))
    for b, flag in cases:
        r = run(b)
        assert r.rc == 0 and r.err == []
        lines = r.out.splitlines()
        notes = lines[lines.index("# Notes") + 1:lines.index("# Flags")]
        assert notes == ["%s | %s" % (n["id"], n["text"]) for n in r.doc["notes"]]
        assert len(notes) >= 1
        shown = lines[lines.index("# Flags") + 1]
        assert shown == (", ".join(r.doc["flags"]) or "none")
        assert flag is None or flag in shown.split(", ")
        assert lines[lines.index("# Flags") + 2] == "# Headlines"
        assert lines[0].endswith("session finished")
        assert lines[-1].startswith("BRIEF ok TEST %s " % r.doc["as_of"])


@pytest.mark.parametrize("kind", ["negative", "zero", "low_above_close", "high_below_open"])
def test_e_ohlc_names_the_date(run, kind):
    b = bundles.bad_prices(kind)
    assert_stop(run(b), "E_OHLC", last_date(b, -30))


@pytest.mark.parametrize("column, factor, ok", [
    ("high", 1.00009, True), ("high", 1.00011, False),
    ("low", 0.99991, True), ("low", 0.99989, False)])
def test_e_ohlc_allows_the_rounding_step_the_fill_allows(run, column, factor, ok):
    for price in ("open", "close"):
        b = bundles.bundle(seed=14)
        b["chart"][price][-30] = b["chart"][column][-30] * factor
        r = run(b)
        if ok:
            assert r.rc == 0 and r.doc["flags"] == []
        else:
            assert_stop(r, "E_OHLC", last_date(b, -30))


def test_a_negative_volume_is_impossible_too(run):
    b = bundles.bundle(seed=14)
    b["chart"]["volume"][-30] = -1
    assert_stop(run(b), "E_OHLC", last_date(b, -30))
    b["chart"]["volume"][-30] = 0
    assert run(b).rc == 0


def test_e_ohlc_comes_before_e_few(run):
    b = bundles.too_few(30)
    b["chart"]["open"][3] = -2.0
    assert_stop(run(b), "E_OHLC")


def test_e_ohlc_does_not_judge_a_row_that_was_cut_or_left_out(run):
    # "In any kept row": a hole puts the row out before its prices are looked at.
    b = bundles.behind_case(holes=(-2,))
    b["chart"]["low"][-1] = -5.0
    assert_behind(run(b), b, -2, -3)
    b = bundles.null_rows((-40,))
    b["chart"]["high"][-40] = -5.0
    assert run(b).rc == 0


# --- The row in progress: its range is widened to hold its open and its newest price -------

# A newest row whose session was running at the fetch, and the same kind of row once it is
# over. The quarter-hour cases are the session rule's: a quote from before the regular end
# keeps the session unfinished until half an hour after it.
LIVE = {
    "crypto, the fetch's UTC day": bundles.crypto,
    "share, two hours into the session": bundles.unfinished,
    "share, fetched 4 minutes after the end with an older quote":
        lambda: bundles.at_the_bell(-660, 240),
}
OVER = {
    "crypto, the next UTC day": lambda: bundles.crypto(session="finished"),
    "share, the night after": lambda: bundles.bundle(seed=20),
    "share, a quote stamped after the end": lambda: bundles.bundle(seed=20, session="after_close"),
    "share, fetched half an hour after the end": lambda: bundles.at_the_bell(-660, 1800),
    "share, no session times": lambda: without_session_times(bundles.unfinished()),
}
# The excess seen live (0.07 percent), a large one, and the same below the low. The range
# check allows a hundredth of a percent, so each of these is outside it.
EXCESS = [("high", 0.0007), ("high", 0.05), ("low", 0.0007), ("low", 0.05)]


def without_session_times(b):
    del b["chart"]["meta"]["currentTradingPeriod"]
    return b


def outside(b, side, i=-1, column="close"):
    """True when the price lies outside the row's range by more than the rounding step."""
    ch = b["chart"]
    if side == "high":
        return ch[column][i] > ch["high"][i] * 1.0001
    return ch[column][i] < ch["low"][i] * 0.9999


@pytest.mark.parametrize("side, by", EXCESS)
@pytest.mark.parametrize("kind", sorted(LIVE))
def test_a_live_row_is_widened_to_hold_its_newest_price(run, kind, side, by):
    # Seen for a coin during its day: the source's last price was newer than its day-high
    # field, the close of the row in progress lay 0.07 percent above the row's high, and the
    # run stopped with E_OHLC.
    b = bundles.outside_range(LIVE[kind](), side, by)
    ch = b["chart"]
    close, other = ch["close"][-1], "low" if side == "high" else "high"
    assert outside(b, side)
    r = run(b)
    assert r.rc == 0 and r.err == [], r.err
    assert r.doc["session"] == "unfinished" and r.doc["as_of"] == last_date(b)
    assert r.facts[side + ".0"]["v"] == r.facts["close.0"]["v"] == r.facts["last"]["v"] == close
    # The sheet is built, and it shows the widened value.
    sheet = r.out.splitlines()
    assert sheet[-1].startswith("BRIEF ok ") and sheet[0].endswith(" | session unfinished")
    assert "%s.0 | %s on %s | %s" % (side, side, last_date(b), r.facts["last"]["t"]) in sheet
    # The other side of the range, the open and the volume are the source's own, and so
    # are the rows before.
    assert r.facts[other + ".0"]["v"] == ch[other][-1]
    assert r.facts["open.0"]["v"] == ch["open"][-1]
    assert r.facts["volume.0"]["v"] == ch["volume"][-1]
    assert (r.facts["high.1"]["v"], r.facts["low.1"]["v"]) == (ch["high"][-2], ch["low"][-2])
    # Nothing new is said: the one note and the one flag of an unfinished session.
    assert row_flags(r) == [] and "unfinished_session" in r.doc["flags"]
    assert notes_with(r, last_date(b)) == [UNFINISHED % last_date(b)]
    # The brief is the one the same rows give when the source's range holds the price:
    # every fact that reads the range (ATR, ADX, CCI, the stochastics, MFI) uses the wider one.
    held = bundles.clone(b)
    held["chart"][side][-1] = close
    want = run(held)
    assert r.doc["facts"] == want.doc["facts"] and r.doc["data_id"] == want.doc["data_id"]
    assert r.doc["notes"] == want.doc["notes"] and r.doc["flags"] == want.doc["flags"]
    assert r.doc["series"] == want.doc["series"]


@pytest.mark.parametrize("kind", sorted(LIVE))
def test_a_live_row_is_widened_to_hold_its_open_too(run, kind):
    # High is the largest and low the smallest of the row's four prices.
    b = bundles.outside_range(LIVE[kind](), "high", 0.02, column="open")
    ch = b["chart"]
    assert outside(b, "high", column="open")
    r = run(b)
    assert r.rc == 0 and r.facts["high.0"]["v"] == r.facts["open.0"]["v"] == ch["open"][-1]
    assert r.facts["low.0"]["v"] == ch["low"][-1] and r.facts["last"]["v"] == ch["close"][-1]
    # Both at once: an open above the high and a newest price below the low.
    bundles.outside_range(b, "low", 0.02)
    assert outside(b, "low")
    r = run(b)
    assert r.rc == 0
    assert (r.facts["high.0"]["v"], r.facts["low.0"]["v"]) == (ch["open"][-1], ch["close"][-1])
    # A source whose high and low came the wrong way round is put right the same way.
    b = LIVE[kind]()
    ch = b["chart"]
    ch["high"][-1], ch["low"][-1] = ch["low"][-1], ch["high"][-1]
    r = run(b)
    assert r.rc == 0
    assert (r.facts["high.0"]["v"], r.facts["low.0"]["v"]) == (ch["low"][-1], ch["high"][-1])


@pytest.mark.parametrize("kind", sorted(LIVE))
def test_a_live_row_inside_its_range_is_left_as_the_source_gave_it(run, kind):
    b = LIVE[kind]()
    ch = b["chart"]
    r = run(b)
    assert r.rc == 0 and r.doc["session"] == "unfinished"
    for k in bundles.COLS:
        assert r.facts[k + ".0"]["v"] == ch[k][-1], k
    # A price on the edge, or a rounding step past it, moves the edge and nothing else.
    b = bundles.outside_range(LIVE[kind](), "high", 0.00005)
    r = run(b)
    assert r.rc == 0 and r.facts["high.0"]["v"] == b["chart"]["close"][-1]


@pytest.mark.parametrize("side, by", EXCESS)
@pytest.mark.parametrize("kind", sorted(OVER))
def test_the_same_excess_in_the_row_of_a_finished_session_still_stops(run, kind, side, by):
    b = bundles.outside_range(OVER[kind](), side, by)
    assert outside(b, side)
    assert_stop(run(b), "E_OHLC", last_date(b))
    # Only the excess stopped it.
    assert run(OVER[kind]()).doc["session"] == (
        "unknown" if kind == "share, no session times" else "finished")


@pytest.mark.parametrize("i", [-2, -3, -30])
@pytest.mark.parametrize("kind", sorted(LIVE))
def test_an_older_row_outside_its_range_stops_though_the_newest_row_is_live(run, kind, i):
    for side, by in EXCESS:
        b = bundles.outside_range(LIVE[kind](), side, by, i=i)
        assert outside(b, side, i)
        assert_stop(run(b), "E_OHLC", last_date(b, i))
    # And with the newest row past its own range as well: the older row is the one named.
    b = bundles.outside_range(bundles.outside_range(LIVE[kind]()), "high", 0.05, i=i)
    assert_stop(run(b), "E_OHLC", last_date(b, i))


@pytest.mark.parametrize("shape", ["price file", "saved history"])
@pytest.mark.parametrize("kind", sorted(LIVE))
def test_no_row_of_a_price_file_is_live(run, kind, shape):
    # A file does not say when it was downloaded, so none of its rows is known to be in
    # progress: the same rows that pass as a bundle stop as before.
    for side, by in EXCESS:
        b = bundles.outside_range(LIVE[kind](), side, by)
        assert run(b).rc == 0
        source, args, name = as_source(b, shape)
        assert_stop(run(source, *args, name=name), "E_OHLC", last_date(b))


@pytest.mark.parametrize("value", [0.0, -1.0])
@pytest.mark.parametrize("column", ["open", "high", "low", "close"])
@pytest.mark.parametrize("kind", sorted(LIVE))
def test_a_zero_or_negative_price_in_a_live_row_still_stops(run, kind, column, value):
    # Widening never hides a price that cannot be: the smallest of the four becomes the low.
    b = LIVE[kind]()
    b["chart"][column][-1] = value
    assert_stop(run(b), "E_OHLC", last_date(b))


@pytest.mark.parametrize("kind", sorted(LIVE))
def test_a_negative_volume_in_a_live_row_still_stops(run, kind):
    b = LIVE[kind]()
    b["chart"]["volume"][-1] = -1
    assert_stop(run(b), "E_OHLC", last_date(b))


def test_a_live_row_with_a_missing_value_is_cut_not_widened(run):
    # Only a complete row is widened. A row in progress that lacks a value ends the view at
    # the row before, whatever its other prices are.
    b = bundles.outside_range(bundles.unfinished(), "high", 0.05, column="open")
    b["chart"]["close"][-1] = None
    r = run(b)
    assert_not_behind(r, b, -1, -2)
    assert r.facts["high.0"]["v"] == b["chart"]["high"][-2]


def test_e_few_below_60_rows(run):
    assert_stop(run(bundles.too_few(59)), "E_FEW", "59")
    r = run(bundles.too_few(60))
    assert r.rc == 0 and "short_history" in r.doc["flags"]


def test_e_few_counts_kept_rows(run):
    b = bundles.too_few(60)
    for k in bundles.COLS:
        b["chart"][k][10] = None
    assert_stop(run(b), "E_FEW", "59")
    # A row that was cut is not a kept row either.
    b = bundles.too_few(60)
    b["chart"]["close"][-1] = None
    b["chart"]["meta"]["regularMarketTime"] -= bundles.DAY
    assert_stop(run(b), "E_FEW", "59")


@pytest.mark.parametrize("rows, hole", [
    (0, None), (1, 0), (2, 0), (2, 1), (3, 0), (3, 1), (3, 2), (5, 0), (5, 2)])
def test_e_few_for_a_handful_of_rows_with_a_hole(run, rows, hole):
    # A hole with no complete row before it has no date for the view to end at: the row is
    # left out and the rest is counted. Any other hole among the newest 3 ends the view.
    b = bundles.too_few(max(rows, 1))
    ch = b["chart"]
    if rows == 0:
        for k in ("timestamp",) + bundles.COLS:
            ch[k] = []
        kept = 0
    else:
        for k in bundles.COLS:
            ch[k][hole] = None
        kept = hole if hole and hole >= rows - 3 else rows - 1
    assert_stop(run(b), "E_FEW", "only %d usable" % kept)


# --- Gaps -------------------------------------------------------------------------------

GAP_NOTE = "No prices between %s and %s: a long holiday or missing days."


@pytest.mark.parametrize("days, outcome", [
    (1, None), (5, None), (6, "flag"), (9, "flag"), (12, "flag"), (13, "stop"), (40, "stop")])
def test_gap_bands(run, days, outcome):
    b = bundles.calendar_gap(days)
    before, after = last_date(b, -21), last_date(b, -20)
    off = b["chart"]["meta"]["gmtoffset"]
    stamps = b["chart"]["timestamp"]
    assert (stamps[-20] + off) // 86400 - (stamps[-21] + off) // 86400 == days
    r = run(b)
    if outcome == "stop":
        assert_stop(r, "E_GAP", before, after)
        return
    assert r.rc == 0
    assert row_flags(r) == (["history_gap"] if outcome else [])
    assert notes_with(r, "No prices between") == ([GAP_NOTE % (before, after)] if outcome else [])


def test_e_gap_names_both_dates(run):
    b = bundles.date_gap(position=-20)
    assert_stop(run(b), "E_GAP", last_date(b, -21), last_date(b, -20))


def test_a_missing_week_is_flagged_and_the_run_goes_on(run):
    b = bundles.date_gap(position=-20, missing=4)
    r = run(b)
    assert r.rc == 0 and row_flags(r) == ["history_gap"]
    assert notes_with(r, "No prices between") == [
        GAP_NOTE % (last_date(b, -21), last_date(b, -20))]


def test_a_long_weekend_is_not_a_gap(run):
    r = run(bundles.date_gap(position=-20, missing=1))
    assert r.rc == 0 and r.doc["flags"] == []


@pytest.mark.parametrize("days, inside", [(13, "stop"), (8, "flag")])
def test_gaps_count_between_the_newest_60_rows_only(run, days, inside):
    # Row -60 is the oldest of the newest 60: the step into it is outside, the step out of
    # it is inside.
    r = run(bundles.calendar_gap(days, position=-60))
    assert r.rc == 0 and row_flags(r) == []
    assert run(bundles.calendar_gap(days, position=-100)).doc["flags"] == []
    b = bundles.calendar_gap(days, position=-59)
    r = run(b)
    if inside == "stop":
        assert_stop(r, "E_GAP", last_date(b, -60), last_date(b, -59))
    else:
        assert r.rc == 0 and row_flags(r) == ["history_gap"]
    # The newest step counts too.
    r = run(bundles.calendar_gap(days, position=-1))
    assert (r.rc == 2) if inside == "stop" else (row_flags(r) == ["history_gap"])


def test_gaps_are_counted_in_exchange_dates(run):
    # Sydney again: the newest row is stamped at the last trade, a universal day later than
    # a row stamped at the session start. Five exchange days apart is not a gap.
    b = bundles.bundle(seed=16, gmtoffset=39600)
    ts = b["chart"]["timestamp"]
    ts[-1] += 22208
    apart = (ts[-1] + 39600) // 86400 - (ts[-2] + 39600) // 86400
    ts[:-1] = [t - (5 - apart) * 86400 for t in ts[:-1]]
    assert ts[-1] // 86400 - ts[-2] // 86400 == 6
    r = run(b)
    assert r.rc == 0 and row_flags(r) == [] and r.doc["as_of"] == "2026-11-10"
    # Twelve exchange days apart is flagged and does not stop.
    ts[:-1] = [t - 7 * 86400 for t in ts[:-1]]
    b["fetched_at"] = ts[-1] + 3600
    r = run(b)
    assert r.rc == 0 and row_flags(r) == ["history_gap"]


def test_each_gap_has_its_own_note_and_the_flag_is_set_once(run):
    b = bundles.widen(bundles.calendar_gap(7, position=-20), 10, position=-40)
    r = run(b)
    assert r.rc == 0 and r.doc["flags"].count("history_gap") == 1
    assert notes_with(r, "No prices between") == [
        GAP_NOTE % (last_date(b, -41), last_date(b, -40)),
        GAP_NOTE % (last_date(b, -21), last_date(b, -20))]


def test_e_gap_is_one_day_for_crypto(run):
    b = bundles.crypto(session="finished")
    for k in ("timestamp",) + bundles.COLS:
        del b["chart"][k][-10]
    assert_stop(run(b), "E_GAP", last_date(b, -10), last_date(b, -9))


@pytest.mark.parametrize("days", [2, 6, 12, 13])
def test_crypto_gaps_always_stop_and_are_never_only_flagged(run, days):
    kw = dict(typ="CRYPTOCURRENCY", gmtoffset=0, start=70000.0, symbol="COIN-USD",
              with_statements=False, n=366)
    b = bundles.calendar_gap(days, **kw)
    assert_stop(run(b), "E_GAP", last_date(b, -21), last_date(b, -20))
    r = run(bundles.calendar_gap(1, **kw))
    assert r.rc == 0 and row_flags(r) == []
    # A share is judged by the share's limits.
    r = run(bundles.calendar_gap(days, **dict(kw, typ="EQUITY")))
    assert (r.rc, r.rc == 0 and row_flags(r)) == (
        (2, False) if days > 12 else (0, ["history_gap"] if days > 5 else []))


def test_a_cut_does_not_leave_a_gap_behind(run):
    # After the view is cut the newest kept rows are judged as they stand.
    b = bundles.behind_case()
    r = run(b)
    assert r.rc == 0 and "history_gap" not in r.doc["flags"]


def test_e_stale_after_12_days(run):
    b = bundles.stale(13)
    assert_stop(run(b), "E_STALE", last_date(b))
    assert run(bundles.stale(11)).rc == 0
    # A minute more than 12 days stops; 12 days to the second does not.
    b = bundles.stale(12)
    assert_stop(run(b), "E_STALE", last_date(b))
    b["fetched_at"] -= 60
    assert run(b).rc == 0


def test_a_bar_dated_after_the_fetch_stops_too(run):
    # One stop for a fetch time that does not fit the newest row, on either side of it.
    b = bundles.future_bar(2)
    assert_stop(run(b), "E_STALE", last_date(b), "or dated after it")
    # A day and a minute after the fetch stops; a day to the second does not.
    b = bundles.future_bar(1)
    assert run(b).rc == 0
    b["fetched_at"] -= 60
    assert_stop(run(b), "E_STALE", last_date(b))


def test_short_history_flag_and_absent_facts(run):
    r = run(bundles.short_history(150))
    assert r.rc == 0 and "short_history" in r.doc["flags"]
    for name in LONG_FACTS:
        assert name not in r.facts
    notes = [n["text"] for n in r.doc["notes"] if "Too little history" in n["text"]]
    assert len(notes) == 1 and "(150 daily rows)" in notes[0]
    for name in LONG_FACTS:
        assert re.search(r"\b%s\b" % name, notes[0])
    assert "sma50" in r.facts and "ret63" in r.facts
    assert len(r.doc["series"]["date"]) == 120 and set(r.doc["series"]["sma200"]) == {None}


def test_sixty_rows_lack_the_63_bar_return(run):
    r = run(bundles.too_few(60))
    assert "ret63" not in r.facts and re.search(r"\bret63\b", r.note_text())
    assert len(r.doc["series"]["date"]) == 60


def test_200_rows_is_not_short(run):
    r = run(bundles.bundle(n=200))
    assert "short_history" not in r.doc["flags"]
    assert not set(LONG_FACTS) - set(r.facts)


def test_session_finished(run):
    r = run(bundles.bundle())
    assert r.doc["session"] == "finished" and "unfinished_session" not in r.doc["flags"]
    assert r.facts["last"]["label"] == "last close"
    assert run(bundles.bundle(session="after_close")).doc["session"] == "finished"


def test_session_unfinished(run):
    b = bundles.unfinished()
    r = run(b)
    assert r.doc["session"] == "unfinished" and "unfinished_session" in r.doc["flags"]
    notes = [n["text"] for n in r.doc["notes"] if "unfinished" in n["text"]]
    assert len(notes) == 1 and last_date(b) in notes[0]
    # Nothing calls the newest price a close.
    for name in ("last", "close.0", "sma50_gap", "high52_gap", "atr14_pct"):
        assert "close" not in r.facts[name]["label"], name
    assert "close" in r.facts["close.1"]["label"]


def test_session_unknown_without_session_facts(run):
    b = bundles.bundle()
    del b["chart"]["meta"]["currentTradingPeriod"]
    r = run(b)
    assert r.doc["session"] == "unknown"
    assert "not be final" in r.note_text()


def test_crypto_is_unfinished_unless_the_bar_is_before_the_current_utc_day(run):
    r = run(bundles.crypto(session="unfinished"))
    assert r.rc == 0 and r.doc["session"] == "unfinished" and r.doc["type"] == "CRYPTOCURRENCY"
    assert run(bundles.crypto(session="finished")).doc["session"] == "finished"


# --- The session rule for everything but crypto -------------------------------------------

# A row inside the regular session is unfinished when the source's own quote is from before
# the regular end and the fetch came less than 1800 seconds after it. The quote can be a
# quarter of an hour old, so a fetch minutes after the bell does not hold the close yet.
# Each case: the quote time and the fetch time in seconds after the regular end (None: the
# meta holds no quote time, and the fetch time decides alone, as it did before), and whether
# the session is unfinished.
BELL = [
    ("quote and fetch both before the end", -3605, -3600, True),
    ("quote and fetch in the last seconds", -2, -1, True),
    ("fetched at the end, the quote 5 seconds old", -5, 0, True),
    ("fetched 4 minutes after the end, the quote 15 minutes old", -660, 240, True),
    ("fetched 29 minutes after, the quote from before the end", -1, 1740, True),
    ("fetched a second short of 30 minutes after", -1, 1799, True),
    ("fetched 30 minutes after, the quote from before the end", -1, 1800, False),
    ("fetched 31 minutes after, the quote from before the end", -1, 1860, False),
    ("fetched 31 minutes after, the quote hours old", -20000, 1860, False),
    ("the quote stamped at the end, fetched a minute later", 0, 60, False),
    ("the quote stamped a second after the end, fetched a minute later", 1, 60, False),
    ("the quote stamped at the end and fetched in that second", 0, 0, False),
    ("no quote time, fetched a second before the end", None, -1, True),
    ("no quote time, fetched at the end", None, 0, False),
    ("no quote time, fetched 4 minutes after the end", None, 240, False),
]
BELL_PARAMS = dict(argnames="quote, fetch, live", argvalues=[c[1:] for c in BELL],
                   ids=[c[0] for c in BELL])


@pytest.mark.parametrize(**BELL_PARAMS)
def test_the_session_is_judged_by_the_sources_quote_time_and_the_fetch_time(
        run, quote, fetch, live):
    b = bundles.at_the_bell(quote, fetch)
    meta, date = b["chart"]["meta"], last_date(b)
    regular = meta["currentTradingPeriod"]["regular"]
    assert regular["start"] == b["chart"]["timestamp"][-1] < regular["end"]
    assert b["fetched_at"] == regular["end"] + fetch
    assert meta.get("regularMarketTime") == (None if quote is None else regular["end"] + quote)
    r = run(b)
    word = "unfinished" if live else "finished"
    assert r.rc == 0 and r.err == [] and r.doc["as_of"] == date
    # The session word, in the brief and on the first line of the sheet.
    assert r.doc["session"] == word and r.out.splitlines()[0].endswith(" | session " + word)
    # The flag and the note.
    assert r.doc["flags"] == (["unfinished_session"] if live else [])
    assert [n["text"] for n in r.doc["notes"]] == ([UNFINISHED % date] if live else [])
    # The labels: nothing calls the price of an unfinished session a close.
    px = "last price" if live else "close"
    assert r.facts["last"]["label"] == ("last price" if live else "last close")
    assert r.facts["close.0"]["label"] == "%s on %s" % (px, date)
    assert r.facts["sma50_gap"]["label"] == px + " against the 50-day average"
    assert r.facts["atr14_pct"]["label"] == "ATR(14) as a share of the " + px
    # The prices are the same either way: the rule names them, it does not change them.
    assert r.facts["last"]["v"] == b["chart"]["close"][-1]


@pytest.mark.parametrize(**BELL_PARAMS)
def test_a_missing_newest_close_is_filled_only_when_the_session_is_finished(
        run, quote, fetch, live):
    b = bundles.at_the_bell(quote, fetch)
    ch = b["chart"]
    price = ch["meta"]["regularMarketPrice"] = round((ch["high"][-1] + ch["low"][-1]) / 2, 2)
    ch["close"][-1] = None
    r = run(b)
    if live:
        # The row in progress has no close yet: the view ends the row before and no flag
        # is set.
        assert_not_filled(r, b)
        assert_not_behind(r, b, -1, -2)
        assert row_flags(r) == []
    elif quote is None:
        # Finished, but a fill needs the time of the price it takes.
        assert_not_filled(r, b)
        assert_behind(r, b, -1, -2, quote=False)
    else:
        assert r.rc == 0 and row_flags(r) == ["close_filled"]
        assert r.doc["session"] == "finished" and r.doc["as_of"] == last_date(b)
        assert r.facts["last"]["v"] == r.facts["close.0"]["v"] == price
        assert r.facts["last"]["label"] == "last close"
        assert len(notes_with(r, "filled")) == 1 and "unfinished_session" not in r.doc["flags"]


@pytest.mark.parametrize("value", [None, 0])
def test_a_quote_time_that_is_null_or_zero_counts_as_none(run, value):
    for fetch, live in ((-1, True), (0, False), (240, False)):
        b = bundles.at_the_bell(-660, fetch)
        b["chart"]["meta"]["regularMarketTime"] = value
        assert run(b).doc["session"] == ("unfinished" if live else "finished"), fetch


def test_a_row_from_before_the_session_start_is_finished_whatever_the_quote_says(run):
    # After a close the source's calendar describes the next session. The newest row is then
    # a finished session's row, though the quote is from before that session's end and the
    # fetch is too.
    b = bundles.bundle(seed=35)
    meta = b["chart"]["meta"]
    regular = meta["currentTradingPeriod"]["regular"]
    assert b["chart"]["timestamp"][-1] < regular["start"]
    assert meta["regularMarketTime"] < b["fetched_at"] < regular["end"]
    assert run(b).doc["session"] == "finished"
    # One second decides: a row stamped at the start is inside the session.
    b = bundles.at_the_bell(-660, 240)
    assert run(b).doc["session"] == "unfinished"
    b["chart"]["meta"]["currentTradingPeriod"]["regular"]["start"] += 1
    assert run(b).doc["session"] == "finished"


def test_crypto_keeps_its_own_rule_the_utc_day_of_the_fetch(run):
    # For a coin neither the quote time nor the half hour counts: the row of the fetch's UTC
    # day is unfinished and the row of the day before is finished.
    b = bundles.crypto()
    ch = b["chart"]
    midnight = (ch["timestamp"][-1] // 86400 + 1) * 86400
    regular = ch["meta"]["currentTradingPeriod"]["regular"]
    assert regular["end"] < midnight < regular["end"] + 1800
    # The last second of the day, with a quote stamped after the calendar's end.
    b["fetched_at"], ch["meta"]["regularMarketTime"] = midnight - 1, regular["end"] + 5
    assert run(b).doc["session"] == "unfinished"
    # The first second of the next day, with a quote from before that end.
    b["fetched_at"], ch["meta"]["regularMarketTime"] = midnight, regular["end"] - 600
    r = run(b)
    assert r.doc["session"] == "finished" and r.facts["last"]["label"] == "last close"
    # The same rows and times as a share fall under the share's rule.
    ch["meta"]["instrumentType"] = "EQUITY"
    assert run(b).doc["session"] == "unfinished"
    b["fetched_at"], ch["meta"]["regularMarketTime"] = midnight - 1, regular["end"] + 5
    assert run(b).doc["session"] == "finished"


# ---------------------------------------------------------------------------------------
# Bars in a year: 365 for a market with a bar on every day, 252 for a five-day week
# ---------------------------------------------------------------------------------------

def plant(rows, at, close):
    """Give row ``at`` (counted from the end) this close, widening its range to hold it."""
    o, h, low, _, v = rows[at]
    rows[at] = (o, max(h, close), min(low, close), close, v)


def with_extremes(n, high_in, high_out, low_in, low_out, seed=41):
    """A walk of ``n`` rows with four planted closes: a high and a low meant to lie inside
    the 52-week window, and a higher high and a lower low meant to lie just outside it.
    Returns the rows and the two closes the window must give."""
    rows = bundles.walk(seed, n, start=500.0)
    top, bottom = max(r[3] for r in rows), min(r[3] for r in rows)
    high, low = round(top * 2, 2), round(bottom / 2, 2)
    plant(rows, high_in, high)
    plant(rows, high_out, round(top * 3, 2))
    plant(rows, low_in, low)
    plant(rows, low_out, round(bottom / 3, 2))
    return rows, high, low


def ref_vol21(closes, bars_in_a_year):
    """The 21-day volatility done independently: the sample deviation of the newest 21
    one-day returns, times the root of the bars in a year, in percent."""
    r = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 21, len(closes))]
    return statistics.stdev(r) * math.sqrt(bars_in_a_year) * 100


def as_source(b, kind):
    """The bundle itself, or its series as a price file or a saved history: neither of those
    names a type. Returns what the run fixture takes: (source, extra arguments, file name)."""
    if kind == "price file":
        return bundles.csv_text(b), ["--symbol", b["symbol"]], "in.csv"
    return (bundles.history_result(b) if kind == "saved history" else b), [], "in.json"


def assert_year(r, b, bars, high, low, high_at, low_at):
    """The 52-week facts are those of the newest ``bars`` rows, and vol21 is annualised with
    the same number."""
    closes = b["chart"]["close"]
    assert r.rc == 0, r.err
    assert r.facts["high52"]["v"] == high == max(closes[-bars:])
    assert r.facts["low52"]["v"] == low == min(closes[-bars:])
    assert r.facts["high52_date"]["v"] == last_date(b, high_at)
    assert r.facts["low52_date"]["v"] == last_date(b, low_at)
    assert math.isclose(r.facts["high52_gap"]["v"], (closes[-1] / high - 1) * 100)
    assert math.isclose(r.facts["low52_gap"]["v"], (closes[-1] / low - 1) * 100)
    assert math.isclose(r.facts["vol21"]["v"], ref_vol21(closes, bars), rel_tol=1e-9)
    other = 252 if bars == 365 else 365
    assert not math.isclose(r.facts["vol21"]["v"], ref_vol21(closes, other), rel_tol=1e-3)


@pytest.mark.parametrize("typ", ["CRYPTOCURRENCY", "EQUITY", "ETF", "INDEX"])
@pytest.mark.parametrize("kind", ["bundle", "price file", "saved history"])
def test_a_market_with_a_bar_every_day_has_365_bars_in_a_year(run, typ, kind):
    # 420 rows, one for each calendar day. The high sits on the oldest row of the newest 365
    # and a higher one on the row before it; the low sits 300 rows back, past the 252 rows of
    # a five-day year, and a lower one 367 rows back.
    rows, high, low = with_extremes(420, -365, -366, -300, -367)
    b = bundles.every_day(typ=typ, rows=rows, symbol="COIN-USD", with_statements=False)
    stamps = b["chart"]["timestamp"]
    assert len(stamps) == 420 > 365 and {y - x for x, y in zip(stamps, stamps[1:])} == {86400}
    source, args, name = as_source(b, kind)
    r = run(source, *args, name=name)
    assert_year(r, b, 365, high, low, -365, -300)
    assert r.doc["type"] == (typ if kind == "bundle" else "UNKNOWN")
    assert "history_gap" not in r.doc["flags"] and "short_history" not in r.doc["flags"]


@pytest.mark.parametrize("typ", ["EQUITY", "ETF", "INDEX", "MUTUALFUND"])
@pytest.mark.parametrize("kind", ["bundle", "price file", "saved history"])
def test_a_five_day_week_keeps_252_bars_in_a_year(run, typ, kind):
    # The same 420 rows on weekdays only. The high sits on the oldest row of the newest 252
    # and a higher one on the row before it; the lower low sits 300 rows back, which the
    # newest 365 rows would take in.
    rows, high, low = with_extremes(420, -252, -253, -200, -300)
    b = bundles.bundle(seed=28, typ=typ, rows=rows, with_statements=False)
    stamps = b["chart"]["timestamp"]
    assert {y - x for x, y in zip(stamps, stamps[1:])} == {86400, 3 * 86400}
    source, args, name = as_source(b, kind)
    r = run(source, *args, name=name)
    assert_year(r, b, 252, high, low, -252, -200)


def test_the_same_rows_on_two_calendars_differ_only_in_the_yearly_facts(run):
    # 300 rows whose highest close is 280 rows back: inside a year of 365 bars, outside a
    # year of 252.
    rows = bundles.walk(43, 300, start=80.0)
    top = round(max(r[3] for r in rows) * 2, 2)
    plant(rows, -280, top)
    week = run(bundles.bundle(seed=28, rows=rows, with_statements=False))
    daily = run(bundles.every_day(rows=rows, with_statements=False))
    assert week.rc == daily.rc == 0
    yearly = {"vol21", "high52", "high52_date", "high52_gap", "low52", "low52_date", "low52_gap"}
    for name, f in week.facts.items():
        if f["kind"] != "date" and name not in yearly:
            assert daily.facts[name]["v"] == f["v"], name
    assert math.isclose(daily.facts["vol21"]["v"] / week.facts["vol21"]["v"],
                        math.sqrt(365 / 252), rel_tol=1e-12)
    assert daily.facts["high52"]["v"] == top > week.facts["high52"]["v"]
    assert week.facts["high52"]["v"] == max(r[3] for r in rows[-252:])
    # The label does not change: "21-day annualised volatility" either way.
    assert daily.facts["vol21"]["label"] == week.facts["vol21"]["label"]


@pytest.mark.parametrize("gap, bars", [(1, 365), (6, 365), (9, 365), (10, 252), (12, 252)])
def test_the_calendar_is_read_from_the_span_of_the_newest_60_rows(run, gap, bars):
    # Sixty rows of a market open every day span 59 calendar days, and sixty rows of a
    # five-day week at least 81. Fewer than 68 days is a market open every day: 67 days is,
    # 68 days is a different calendar.
    b = bundles.widen(bundles.every_day(with_statements=False), gap, position=-20)
    stamps, closes = b["chart"]["timestamp"], b["chart"]["close"]
    assert (stamps[-1] - stamps[-60]) // 86400 == 58 + gap
    assert ((stamps[-1] - stamps[-60]) // 86400 < 68) == (bars == 365)
    r = run(b)
    assert r.rc == 0 and row_flags(r) == (["history_gap"] if gap > 5 else [])
    assert math.isclose(r.facts["vol21"]["v"], ref_vol21(closes, bars), rel_tol=1e-9)
    assert r.facts["high52"]["v"] == max(closes[-bars:])
    assert r.facts["low52"]["v"] == min(closes[-bars:])
    # A hole before the newest 60 rows says nothing about the calendar of today.
    old = bundles.widen(bundles.every_day(with_statements=False), 12, position=-60)
    assert math.isclose(run(old).facts["vol21"]["v"], ref_vol21(closes, 365), rel_tol=1e-9)
    # The step out of row -60 lies inside them: ten days there make 68, nine make 67.
    edge = bundles.widen(bundles.every_day(with_statements=False), 10, position=-59)
    assert math.isclose(run(edge).facts["vol21"]["v"], ref_vol21(closes, 252), rel_tol=1e-9)
    edge = bundles.widen(bundles.every_day(with_statements=False), 9, position=-59)
    assert math.isclose(run(edge).facts["vol21"]["v"], ref_vol21(closes, 365), rel_tol=1e-9)


def test_a_five_day_week_is_far_from_the_68_days(run):
    # The newest 60 rows of a five-day week span 81 or 83 calendar days, whichever weekday
    # the newest row is: the 68 days leave room for some missing days on the daily side, and
    # no five-day week is taken for a market open every day.
    spans = set()
    for back in range(7):
        b = bundles.bundle(seed=28, n=300, end=bundles.END - back, with_statements=False)
        stamps, closes = b["chart"]["timestamp"], b["chart"]["close"]
        spans.add((stamps[-1] - stamps[-60]) // 86400)
        r = run(b)
        assert r.rc == 0, r.err
        assert math.isclose(r.facts["vol21"]["v"], ref_vol21(closes, 252), rel_tol=1e-9)
        assert r.facts["high52"]["v"] == max(closes[-252:])
    assert spans == {81, 83}


@pytest.mark.parametrize("holes, bars", [(5, 365), (8, 365), (9, 252)])
def test_the_span_is_that_of_the_rows_that_were_kept(run, holes, bars):
    # Rows left out for a missing value widen the span of the 60 rows that stay: eight of
    # them make it 67 days, nine make it 68.
    b = bundles.every_day(with_statements=False)
    for i in range(-30, -30 + holes):
        b["chart"]["close"][i] = None
    kept = [c for c in b["chart"]["close"] if c is not None]
    stamps = [t for t, c in zip(b["chart"]["timestamp"], b["chart"]["close"]) if c is not None]
    assert (stamps[-1] - stamps[-60]) // 86400 == 59 + holes
    r = run(b)
    assert r.rc == 0 and row_flags(r) == ["history_gap", "rows_dropped"]
    assert len(notes_with(r, "left out")) == holes
    assert math.isclose(r.facts["vol21"]["v"], ref_vol21(kept, bars), rel_tol=1e-9)
    assert r.facts["high52"]["v"] == max(kept[-bars:])


@pytest.mark.parametrize("n", [60, 199, 200, 288, 289, 364, 365, 366])
def test_a_daily_history_of_any_length(run, n):
    b = bundles.every_day(n=n, typ="CRYPTOCURRENCY", symbol="COIN-USD", with_statements=False)
    closes = b["chart"]["close"]
    r = run(b)
    assert r.rc == 0
    assert math.isclose(r.facts["vol21"]["v"], ref_vol21(closes, 365), rel_tol=1e-9)
    # The 52-week facts need more than 0.79 of a year of rows: 289 of a year of 365.
    assert ("high52" in r.facts) == (n >= 289)
    assert ("short_history" in r.doc["flags"]) == (n < 200)
    if n >= 289:
        assert r.facts["high52"]["v"] == max(closes[-365:])
        assert r.facts["low52"]["v"] == min(closes[-365:])


# --- The 52-week facts need more than 0.79 of a year of rows --------------------------------

YEAR_FACTS = ["high52", "high52_date", "high52_gap", "low52", "low52_date", "low52_gap"]
TOO_LITTLE = ("Too little history (%d daily rows) or a division by zero, so these facts are "
              "absent: %s.")


@pytest.mark.parametrize("kind", ["bundle", "price file", "saved history"])
@pytest.mark.parametrize("n, there", [(199, False), (200, True), (201, True)])
def test_a_five_day_week_has_its_52_week_facts_from_200_rows(run, n, there, kind):
    # 0.79 of 252 bars is 199.08 rows, so the line is where it was: 200 rows and no fewer.
    b = bundles.bundle(seed=19, n=n, with_statements=False, news=0)
    closes = b["chart"]["close"]
    source, args, name = as_source(b, kind)
    r = run(source, *args, name=name)
    assert r.rc == 0, r.err
    assert math.isclose(r.facts["vol21"]["v"], ref_vol21(closes, 252), rel_tol=1e-9)
    assert [k for k in LONG_FACTS if k in r.facts] == (LONG_FACTS if there else [])
    assert ("short_history" in r.doc["flags"]) == (not there)
    # The absent facts are named in one note, with the number of rows.
    assert notes_with(r, "Too little history") == (
        [] if there else [TOO_LITTLE % (n, ", ".join(LONG_FACTS))])
    if there:
        assert r.facts["high52"]["v"] == max(closes) and r.facts["low52"]["v"] == min(closes)
        assert r.facts["high52_date"]["v"] == last_date(b, closes.index(max(closes)) - n)


@pytest.mark.parametrize("typ", ["CRYPTOCURRENCY", "EQUITY", "price file"])
@pytest.mark.parametrize("n, there", [(200, False), (288, False), (289, True), (290, True)])
def test_a_seven_day_series_has_its_52_week_facts_from_289_rows(run, n, there, typ):
    # 0.79 of 365 bars is 288.35 rows. Before, 200 rows of a coin gave a "52-week" high that
    # was the high of 29 weeks.
    kind = "bundle" if typ != "price file" else typ
    b = bundles.every_day(n=n, typ="EQUITY" if kind != "bundle" else typ, symbol="COIN-USD",
                          with_statements=False, news=0)
    closes = b["chart"]["close"]
    source, args, name = as_source(b, kind)
    r = run(source, *args, name=name)
    assert r.rc == 0, r.err
    assert math.isclose(r.facts["vol21"]["v"], ref_vol21(closes, 365), rel_tol=1e-9)
    assert [k for k in YEAR_FACTS if k in r.facts] == (YEAR_FACTS if there else [])
    # 200 rows or more are not short history, and the 200-day average is there either way.
    assert "short_history" not in r.doc["flags"]
    assert "sma200" in r.facts and "sma200_gap" in r.facts
    # The absent facts are named in one note, with the number of rows.
    assert notes_with(r, "Too little history") == (
        [] if there else [TOO_LITTLE % (n, ", ".join(YEAR_FACTS))])
    assert set(named_absent(r)) == set(ALL_FACTS) - set(r.facts)
    if there:
        # Fewer rows than a year: the window is all of them.
        assert r.facts["high52"]["v"] == max(closes) and r.facts["low52"]["v"] == min(closes)
        assert r.facts["low52_date"]["v"] == last_date(b, closes.index(min(closes)) - n)
        assert math.isclose(r.facts["high52_gap"]["v"], (closes[-1] / max(closes) - 1) * 100)


def test_the_52_week_line_follows_the_calendar_of_the_rows(run):
    # The same 250 rows: enough on a five-day week, too few on a market open every day.
    rows = bundles.walk(44, 250, start=90.0)
    week = run(bundles.bundle(seed=28, rows=rows, with_statements=False))
    daily = run(bundles.every_day(rows=rows, with_statements=False))
    assert week.rc == daily.rc == 0
    assert set(YEAR_FACTS) <= set(week.facts) and not set(YEAR_FACTS) & set(daily.facts)
    assert "short_history" not in week.doc["flags"] + daily.doc["flags"]
    assert notes_with(week, "Too little history") == []
    assert notes_with(daily, "Too little history") == [TOO_LITTLE % (250, ", ".join(YEAR_FACTS))]


# ---------------------------------------------------------------------------------------
# The three input shapes
# ---------------------------------------------------------------------------------------

def price_facts(r):
    return {k: (f["v"], f["t"], f["kind"]) for k, f in r.facts.items()}


def test_three_shapes_give_the_same_facts(run):
    b = bundles.bundle(seed=5, with_statements=False, news=0)
    from_bundle = run(b)
    from_history = run(bundles.history_result(b))
    from_csv = run(bundles.csv_text(b), "--symbol", "test", "--currency", "USD", name="in.csv")
    assert from_bundle.rc == from_history.rc == from_csv.rc == 0
    assert len(from_bundle.facts) == len(ALL_FACTS) - len(STATEMENT_FACTS)
    assert price_facts(from_bundle) == price_facts(from_history) == price_facts(from_csv)
    assert from_bundle.doc["data_id"] == from_history.doc["data_id"] == from_csv.doc["data_id"]
    for k in ("series", "as_of", "symbol", "currency", "unit"):
        assert from_bundle.doc[k] == from_history.doc[k] == from_csv.doc[k], k


def test_price_file_sources_are_marked(run):
    b = bundles.bundle(seed=5)
    for r in (run(bundles.history_result(b)),
              run(bundles.csv_text(b), "--symbol", "TEST", name="in.csv")):
        d = r.doc
        assert d["source"] == "price-file" and d["session"] == "unknown"
        assert d["type"] == "UNKNOWN" and d["headlines"] == []
        assert {"no_headlines", "statements_absent"} <= set(d["flags"])
        assert not set(STATEMENT_FACTS) & set(d["facts"])
        text = r.note_text()
        assert notes_with(r, "file") == [
            "These prices came from a file; when they were downloaded is not known."]
        for name in STATEMENT_FACTS:
            assert re.search(r"\b%s\b" % name, text)
        # A file holds no record of a request, so nothing is said to have failed.
        assert notes_with(r, "absent: ") == [NO_FIGURE % ", ".join(STATEMENT_FACTS)]
        assert d["notes"][-1]["text"] == NO_HEADLINES and "failed" not in text
        # The fetch time is the newest bar.
        assert d["fetched_at"].startswith(d["as_of"])
        assert d["facts"]["last"]["label"] == "last price"
    assert run(b).doc["source"] == "yahoo-chart"


def test_history_is_found_at_any_depth(run):
    b = bundles.bundle(seed=5)
    inner = bundles.history_result(b)["data"]["data"]
    for doc in (inner, {"data": inner}, [{"x": 1}, {"result": {"content": [inner]}}]):
        r = run(doc)
        assert r.rc == 0 and r.doc["symbol"] == "TEST" and r.doc["currency"] == "USD"


def test_a_saved_history_keeps_its_own_symbol_and_currency(run):
    # The options are for a price file that names neither. A saved history names both, and
    # an option that says otherwise used to put another name and unit on its prices.
    b = bundles.bundle(seed=5)
    r = run(bundles.history_result(b), "--currency", "eur", "--symbol", "other")
    assert r.rc == 0 and r.doc["symbol"] == "TEST" and r.doc["currency"] == "USD"
    assert r.out.splitlines()[0].startswith("# TEST | TEST | UNKNOWN | USD | ")
    same = run(bundles.history_result(b))
    assert same.doc == r.doc


def test_csv_needs_a_symbol(run):
    r = run(bundles.csv_text(bundles.bundle()), name="in.csv")
    assert r.rc == 4 and r.out == "" and r.doc is None
    assert len(r.err) == 1 and r.err[0].startswith("ERROR E_USAGE ") and "--symbol" in r.err[0]


def test_csv_without_a_currency_says_so(run):
    r = run(bundles.csv_text(bundles.bundle()), "--symbol", "TEST", name="in.csv")
    assert r.doc["currency"] == "" and r.doc["unit"] == ""
    assert "names no currency" in r.note_text()


def test_csv_header_case_column_order_and_extra_columns(run):
    b = bundles.bundle(seed=5)
    plain = run(bundles.csv_text(b), "--symbol", "TEST", name="a.csv")
    lines = bundles.csv_text(b).splitlines()
    shuffled = ["Volume,Date,Adj Close,Close,Low,High,Open"]
    for ln in lines[1:]:
        d, o, h, low, c, v = ln.split(",")
        shuffled.append(",".join([v, d, c, c, low, h, o]))
    shuffled.reverse()  # newest first, header last would be wrong: put the header back on top
    shuffled.insert(0, shuffled.pop())
    r = run("\r\n".join(shuffled) + "\r\n", "--symbol", "TEST", name="b.csv")
    assert r.rc == 0 and price_facts(r) == price_facts(plain)


def test_byte_order_marks_are_accepted(run):
    b = bundles.bundle(seed=5, with_statements=False, news=0)
    want = price_facts(run(b))
    text = json.dumps(b)
    csv = bundles.csv_text(b)
    for raw in (b"\xef\xbb\xbf" + text.encode("utf-8"), text.encode("utf-16"),
                b"\xff\xfe" + text.encode("utf-16-le"), b"\xfe\xff" + text.encode("utf-16-be")):
        r = run(raw)
        assert r.rc == 0 and price_facts(r) == want
    for raw in (b"\xef\xbb\xbf" + csv.encode("utf-8"), csv.encode("utf-16")):
        r = run(raw, "--symbol", "TEST", name="in.csv")
        assert r.rc == 0 and price_facts(r) == want


def test_unicode_minus_and_no_break_space_are_normalised(run):
    b = bundles.bundle(seed=5, with_statements=False, news=0)
    csv = bundles.csv_text(b)
    odd = csv.replace(",", ",\u00a0").replace("\n2026", "\n\u00a02026")
    r = run(odd.encode("utf-8"), "--symbol", "TEST", name="in.csv")
    assert r.rc == 0 and price_facts(r) == price_facts(run(b))
    # A Unicode minus reads as a minus: the negative price is then caught as impossible.
    lines = csv.splitlines()
    cells = lines[-30].split(",")
    cells[3] = "\u2212" + cells[3]
    lines[-30] = ",".join(cells)
    assert_stop(run("\n".join(lines).encode("utf-8"), "--symbol", "TEST", name="in.csv"),
                "E_OHLC", cells[0])


def test_non_finite_numbers_count_as_missing(run):
    b = bundles.bundle(seed=5)
    text = json.dumps(b).replace(json.dumps(b["chart"]["close"][-9]), "NaN", 1)
    r = run(text)
    assert r.rc == 0 and "rows_dropped" in r.doc["flags"]
    assert "nan" not in json.dumps(r.doc).lower().replace("finance", "")


@pytest.mark.parametrize("symbol", [
    "", "TOO-LONG-A-SYMBOL-NAME", "A B", "A|B", "<b>", "A\n",
    # allowed signs with no letter or digit among them are not a ticker
    ".", "..", "-", "^", "=", "^=.-", "-.-.-.-.-.-.-.-"])
def test_symbol_must_be_a_plain_ticker(run, symbol):
    r = run(bundles.csv_text(bundles.bundle()), "--symbol", symbol, name="in.csv")
    assert_stop(r, "E_SYMBOL")
    assert r.err == ["STOP E_SYMBOL the symbol is not a plain ticker"]


@pytest.mark.parametrize("symbol", ["..", "-", "^=.-"])
def test_a_symbol_of_signs_alone_stops_whatever_the_input(run, symbol):
    b = bundles.bundle(symbol=symbol)
    assert_stop(run(b), "E_SYMBOL")
    assert_stop(run(bundles.history_result(b)), "E_SYMBOL")


@pytest.mark.parametrize("symbol, shown", [
    (".7", ".7"), ("-x", "-X"), ("^gspc", "^GSPC"), ("a", "A"), ("eurusd=x", "EURUSD=X"),
    ("7203.t", "7203.T"), ("^=.-x", "^=.-X"), ("-.-.-.-.-.-.-.9", "-.-.-.-.-.-.-.9")])
def test_one_letter_or_digit_among_the_signs_is_enough(run, symbol, shown):
    r = run(bundles.csv_text(bundles.bundle()), "--symbol", symbol, name="in.csv")
    assert r.rc == 0 and r.doc["symbol"] == shown
    assert r.out.splitlines()[-1].startswith("BRIEF ok %s " % shown)


# ---------------------------------------------------------------------------------------
# brief.v1 and the fact sheet
# ---------------------------------------------------------------------------------------

def test_brief_v1_shape(run):
    b = bundles.bundle(seed=3)
    r = run(b)
    d = r.doc
    assert list(d) == BRIEF_KEYS
    assert d["schema"] == "brief.v1" and d["version"] == "2.0.0"
    assert d["symbol"] == "TEST" and d["name"] == "Test Instruments Inc."
    assert d["type"] == "EQUITY" and d["currency"] == d["unit"] == "USD"
    assert d["as_of"] == "2026-11-10" and d["fetched_at"] == "2026-11-11 04:00 UTC"
    assert d["flags"] == [] and re.fullmatch("[0-9a-f]{8}", d["data_id"])
    assert list(d["facts"]) == ALL_FACTS
    for name, f in d["facts"].items():
        assert set(f) <= {"v", "t", "label", "kind", "sign", "as_of"} and f["kind"] in KINDS
        assert isinstance(f["t"], str) and f["t"] and f["label"] and f["label"].isascii()
        assert not re.search(r"\d[eE][-+]?\d", f["t"]), (name, f["t"])
        if f["kind"] == "date":
            assert f["v"] == f["t"] and re.fullmatch(r"\d{4}-\d\d-\d\d", f["v"])
        else:
            assert isinstance(f["v"], (int, float)) and math.isfinite(f["v"])
    assert len(set(f["label"] for f in d["facts"].values())) == len(d["facts"])
    assert [n["id"] for n in d["notes"]] == ["n%d" % (i + 1) for i in range(len(d["notes"]))]
    ch = b["chart"]
    # The series is what the page draws: the volume row went when its bars did.
    assert set(d["series"]) == {"date", "close", "sma50", "sma200"}
    assert all(len(v) == 120 for v in d["series"].values())
    assert d["series"]["close"] == ch["close"][-120:]
    # Whole volumes are written as whole numbers.
    assert type(d["facts"]["volume.0"]["v"]) is int
    assert d["series"]["date"][-1] == d["as_of"] == d["facts"]["date.0"]["v"]
    assert d["series"]["date"] == sorted(d["series"]["date"])
    assert math.isclose(d["series"]["sma50"][-1], d["facts"]["sma50"]["v"])
    assert d["headlines"] == [
        {"id": "h%d" % (i + 1), "date": "2026-11-10", "publisher": "Example Wire",
         "title": "Generated headline %d about TEST" % (i + 1)}
        for i in range(3)]
    # Headline dates are exchange dates, like the bars.
    late = bundles.clone(b)
    late["news"][0]["time"] = b["chart"]["timestamp"][-1] + 86400
    assert run(late).doc["headlines"][0]["date"] == "2026-11-11"


def test_data_id_is_a_hash_of_the_kept_rows(run):
    b = bundles.null_rows((-40,))
    ch, off = b["chart"], b["chart"]["meta"]["gmtoffset"]
    h = hashlib.sha256()
    for i, t in enumerate(ch["timestamp"]):
        if ch["close"][i] is not None:
            vals = [float(ch[k][i]) for k in bundles.COLS]
            h.update(("%s,%r,%r,%r,%r,%r;" % tuple([bundles.iso((t + off) // 86400)] + vals))
                     .encode())
    assert run(b).doc["data_id"] == h.hexdigest()[:8]
    other = bundles.clone(b)
    other["chart"]["volume"][5] += 1
    assert run(other).doc["data_id"] != h.hexdigest()[:8]


def test_table_facts_are_newest_first(run):
    b = bundles.bundle(seed=3)
    r = run(b)
    ch = b["chart"]
    for n in range(10):
        i = -1 - n
        assert r.facts["date.%d" % n]["v"] == last_date(b, i)
        for k in ("open", "high", "low", "close", "volume"):
            assert r.facts["%s.%d" % (k, n)]["v"] == ch[k][i]
        prev = ch["close"][i - 1]
        assert math.isclose(r.facts["chg.%d" % n]["v"], (ch["close"][i] / prev - 1) * 100)
        assert math.isclose(r.facts["gap.%d" % n]["v"], (ch["open"][i] / prev - 1) * 100)
        assert last_date(b, i) in r.facts["close.%d" % n]["label"]
    assert r.facts["last"]["v"] == ch["close"][-1]
    assert r.facts["prev_close"]["v"] == ch["close"][-2]
    assert r.facts["as_of"]["v"] == "2026-11-10"
    assert r.facts["chg1"]["v"] == r.facts["chg.0"]["v"] == r.facts["ret1"]["v"]
    moves = [r.facts["chg.%d" % n] for n in range(10)]
    biggest = max(range(10), key=lambda n: abs(moves[n]["v"]))
    assert r.facts["move_max"]["v"] == moves[biggest]["v"]
    assert r.facts["move_max_date"]["v"] == r.facts["date.%d" % biggest]["v"]
    vol = ch["volume"]
    assert math.isclose(r.facts["vol_avg20"]["v"], sum(vol[-20:]) / 20)
    assert math.isclose(r.facts["vol_ratio"]["v"], vol[-1] / (sum(vol[-20:]) / 20))
    year = ch["close"][-252:]
    assert r.facts["high52"]["v"] == max(year) and r.facts["low52"]["v"] == min(year)
    dates = [last_date(b, i) for i in range(-252, 0)]
    assert r.facts["high52_date"]["v"] == dates[year.index(max(year))]
    assert r.facts["low52_date"]["v"] == dates[year.index(min(year))]


def test_labels_are_ones_a_person_would_recognise(run):
    f = run(bundles.bundle()).facts
    assert f["rsi14"]["label"] == "RSI(14)"
    assert f["sma50_gap"]["label"] == "close against the 50-day average"
    assert f["move_max"]["label"] == "largest one-day change in the table"
    assert f["high52"]["label"] == "52-week high close"
    assert f["rev_q_yoy"]["label"] == "revenue, newest quarter against a year earlier"


def test_fact_sheet_format(run):
    b = bundles.bundle(seed=3)
    b["news"][1]["title"] = "Caf\u00e9 chain | profit \u2212 up"
    r = run(b)
    assert r.rc == 0 and r.err == [] and r.out.isascii()
    lines = r.out.splitlines()
    d = r.doc
    assert lines[0].startswith("# TEST | Test Instruments Inc. | EQUITY | USD | session finished")
    assert lines[-1] == "BRIEF ok TEST 2026-11-10 facts=%d data_id=%s" % (
        len(d["facts"]), d["data_id"])
    # The facts follow the first line in the contract's order, with no heading between
    # them; the three parts after them each have one.
    heads = [x for x in lines if x.startswith("# ")]
    assert [h[2:] for h in heads[1:]] == ["Notes", "Flags", "Headlines"]
    fact_lines = lines[1:lines.index("# Notes")]
    assert len(fact_lines) == len(d["facts"])
    for line, (name, f) in zip(fact_lines, d["facts"].items()):
        parts = line.split(" | ")
        assert parts[:3] == [name, f["label"], f["t"]]
        assert parts[3:] == (["as of " + f["as_of"]] if "as_of" in f else [])
    notes = lines[lines.index("# Notes") + 1:lines.index("# Flags")]
    assert notes == ["%s | %s" % (n["id"], n["text"]) for n in d["notes"]]
    assert lines[lines.index("# Flags") + 1] == "none"
    news = lines[lines.index("# Headlines") + 1:-1]
    assert len(news) == 3 and news[0].split(" | ") == [
        "h1", "2026-11-10", "Example Wire", "Generated headline 1 about TEST"]
    # A pipe in a title cannot add a column, and other alphabets are escaped on stdout.
    assert news[1] == "h2 | 2026-11-10 | Example Wire | Caf\\xe9 chain / profit \\u2212 up"
    assert d["headlines"][1]["title"] == "Caf\u00e9 chain / profit \u2212 up"


def test_flags_line_lists_the_flags(run):
    r = run(bundles.short_history(150))
    lines = r.out.splitlines()
    assert lines[lines.index("# Flags") + 1] == ", ".join(r.doc["flags"])


def test_headlines_are_cleaned_and_capped(run):
    b = bundles.bundle(news=12)
    b["news"][0]["title"] = "  Two\nlines\tand   spaces " + "x" * 300
    b["news"][1]["time"] = None
    b["news"][2]["title"] = ""
    h = run(b).doc["headlines"]
    assert len(h) == 9 and [x["id"] for x in h] == ["h%d" % (i + 1) for i in range(9)]
    assert h[0]["title"].startswith("Two lines and spaces x") and len(h[0]["title"]) == 160
    assert h[1]["date"] == ""


def test_control_characters_never_reach_the_fact_sheet(run):
    # A terminal escape, a NUL or a backspace in third-party text could hide or rewrite
    # lines of the sheet. They are ASCII, so the ASCII rule alone does not stop them.
    b = bundles.bundle()
    b["news"][0]["title"] = "Head\x1b[2Jline\x00 with\x08 controls\x7f"
    b["news"][0]["publisher"] = "Wire\x1b]0;x\x07"
    b["chart"]["meta"]["longName"] = "Name\x1b[31m Inc\x00"
    r = run(b)
    assert r.rc == 0
    assert not re.search(r"[\x00-\x09\x0b-\x1f\x7f]", r.out)
    assert r.doc["headlines"][0] == {"id": "h1", "date": "2026-11-10", "publisher": "Wire ]0;x",
                                     "title": "Head [2Jline with controls"}
    assert r.doc["name"] == "Name [31m Inc"
    assert r.out.splitlines()[0].startswith("# TEST | Name [31m Inc | EQUITY")


def test_no_headlines_is_flagged(run):
    r = run(bundles.bundle(news=0))
    assert r.doc["flags"] == ["no_headlines"] and r.doc["headlines"] == []
    assert [n["text"] for n in r.doc["notes"]] == [NO_HEADLINES]
    assert r.out.splitlines()[-2] == "# Headlines"
    # A headline with no title is no headline.
    b = bundles.bundle(news=2)
    b["news"][0]["title"], b["news"][1]["title"] = "", " \n "
    r = run(b)
    assert r.doc["flags"] == ["no_headlines"] and r.doc["notes"][-1]["text"] == NO_HEADLINES


# --- A request that failed is not a source that had nothing ------------------------------

# What get_data.py writes into the bundle's errors block for a failed follow-up request.
FAILURE_LABELS = ["HTTP 429", "HTTP 404", "HTTP 403", "HTTP 500", "no connection",
                  "certificate not trusted", "unexpected answer"]


@pytest.mark.parametrize("label", FAILURE_LABELS)
def test_a_failed_statement_request_is_said_to_have_failed(run, label):
    names = ", ".join(STATEMENT_FACTS)
    failed = run(bundles.rate_limited(news=False, label=label))
    assert [n["text"] for n in failed.doc["notes"]] == [REQUEST_FAILED % names]
    # The same bundle from a source that answered and had no figure, as for a fund or crypto.
    none = run(bundles.bundle(seed=27, with_statements=False))
    assert [n["text"] for n in none.doc["notes"]] == [NO_FIGURE % names]
    # Only the wording differs: the flags and the facts are the same.
    assert failed.doc["flags"] == none.doc["flags"] == ["statements_absent"]
    assert failed.doc["facts"] == none.doc["facts"] and failed.doc["data_id"] == none.doc["data_id"]
    assert not set(STATEMENT_FACTS) & set(failed.facts)
    assert "n1 | " + REQUEST_FAILED % names in failed.out.splitlines()


@pytest.mark.parametrize("label", FAILURE_LABELS)
def test_a_failed_headline_request_is_said_to_have_failed(run, label):
    failed = run(bundles.rate_limited(statements=False, label=label))
    assert [n["text"] for n in failed.doc["notes"]] == [HEADLINES_FAILED]
    none = run(bundles.bundle(seed=27, news=0))
    assert [n["text"] for n in none.doc["notes"]] == [NO_HEADLINES]
    assert failed.doc["flags"] == none.doc["flags"] == ["no_headlines"]
    assert failed.doc["headlines"] == none.doc["headlines"] == []
    assert failed.doc["facts"] == none.doc["facts"]
    lines = failed.out.splitlines()
    assert "n1 | " + HEADLINES_FAILED in lines and lines[-2] == "# Headlines"


def test_both_follow_up_requests_failed_as_on_a_limited_network(run):
    # As recorded live: the bars arrived, the other two requests met HTTP 429.
    r = run(bundles.rate_limited())
    assert r.rc == 0 and r.doc["flags"] == ["statements_absent", "no_headlines"]
    assert [n["text"] for n in r.doc["notes"]] == [
        REQUEST_FAILED % ", ".join(STATEMENT_FACTS), HEADLINES_FAILED]
    # The price facts are all there: the failed requests cost nothing else.
    assert list(r.facts) == [k for k in ALL_FACTS if k not in STATEMENT_FACTS]
    assert "HTTP" not in json.dumps(r.doc) and "429" not in r.note_text()


def test_a_recorded_failure_with_the_data_there_says_nothing(run):
    # Only what is absent is explained. get_data.py never writes such a bundle; a bundle put
    # together by hand may.
    b = bundles.bundle(seed=27)
    b["errors"] = {"news": "HTTP 429", "statements": "HTTP 429"}
    r = run(b)
    assert r.rc == 0 and r.doc["notes"] == [] and r.doc["flags"] == []
    assert len(r.doc["headlines"]) == 3 and set(STATEMENT_FACTS) <= set(r.facts)
    # A statement fact that is absent all the same is named under the failed request.
    b["statements"]["trailingPegRatio"] = []
    assert [n["text"] for n in run(b).doc["notes"]] == [REQUEST_FAILED % "peg"]


@pytest.mark.parametrize("errors", ["absent", None, {}, {"news": None}, {"news": ""},
                                    {"news": None, "statements": None}], ids=repr)
def test_without_a_recorded_failure_nothing_is_said_to_have_failed(run, errors):
    b = bundles.bundle(seed=27, news=0, with_statements=False)
    if errors == "absent":
        del b["errors"]
    else:
        b["errors"] = errors
    r = run(b)
    assert r.rc == 0 and r.doc["flags"] == ["statements_absent", "no_headlines"]
    assert [n["text"] for n in r.doc["notes"]] == [
        NO_FIGURE % ", ".join(STATEMENT_FACTS), NO_HEADLINES]


# ---------------------------------------------------------------------------------------
# Display rules
# ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("value, text", [
    (168.89, "168.89"), (3624.5, "3624.50"), (73678.5234, "73678.52"), (10.0, "10.00"),
    (9.5, "9.500"), (1.1168, "1.117"), (0.5, "0.5000"), (0.37416, "0.3742"),
    (0.012346, "0.01235"), (0.00001234567, "0.00001235"), (-0.34561, "-0.3456"),
    (-12.5, "-12.50"), (0.0, "0.00"),
])
def test_price_display(value, text):
    assert brief.f_price(value) == text


@pytest.mark.parametrize("value, text", [
    (426599700, "426.6M"), (4.08e12, "4.08T"), (28968978432, "28.97B"), (13023543, "13.02M"),
    (950, "950"), (0, "0"), (12345, "12.35K"), (999.96e6, "1.00B"), (99.996e6, "100.0M"),
    (-1.234e9, "-1.23B"), (2.5e15, "2500.0T"), (123456.7, "123.5K"),
])
def test_count_and_money_display(value, text):
    assert brief.f_big(value) == text


def test_percent_facts_carry_a_sign_when_they_are_a_change_or_a_gap(run):
    f = run(bundles.bundle(seed=3)).facts
    signed = (["chg1", "sma10_gap", "sma50_gap", "sma200_gap", "ret1", "ret5", "ret21", "ret63",
               "high52_gap", "low52_gap", "move_max", "rev_q_yoy", "ni_q_yoy"]
              + ["%s.%d" % (k, n) for n in range(10) for k in ("chg", "gap")])
    unsigned = ["atr14_pct", "vol21", "margin_ttm", "debt_equity"]
    for name, fact in f.items():
        if fact["kind"] != "pct":
            assert "sign" not in fact, name
            continue
        assert name in signed + unsigned, name
        assert re.fullmatch(r"[-+]?\d+\.\d\d%", fact["t"]), (name, fact["t"])
        if name in signed:
            assert fact["sign"] == fact["t"][0] and fact["sign"] in "+-"
            assert (fact["sign"] == "-") == (fact["v"] < 0)
            assert math.isclose(float(fact["t"][:-1]), fact["v"], abs_tol=0.005000001)
        else:
            assert "sign" not in fact and fact["t"][0].isdigit()
    assert f["high52_gap"]["v"] <= 0 <= f["low52_gap"]["v"]


def test_kinds(run):
    f = run(bundles.bundle(seed=3)).facts
    kinds = {
        "last": "price", "open.3": "price", "sma50": "price", "bb_upper": "price",
        "high52": "price", "rsi14": "level", "macd": "level", "atr14": "level",
        "stoch_j": "level", "volume.0": "count", "vol_avg20": "count", "volume.9": "count",
        "vol_ratio": "ratio", "pe_trailing": "ratio", "peg": "ratio", "mcap": "money",
        "rev_ttm": "money", "debt": "money", "as_of": "date", "date.9": "date",
        "high52_date": "date", "move_max_date": "date", "ret21": "pct", "margin_ttm": "pct"}
    for name, kind in kinds.items():
        assert f[name]["kind"] == kind, name
    assert re.fullmatch(r"\d+\.\d\d", f["rsi14"]["t"]) and re.fullmatch(r"\d+\.\d\d", f["peg"]["t"])
    assert re.fullmatch(r"\d+\.\d+[KMBT] USD", f["mcap"]["t"])
    assert re.fullmatch(r"\d+\.\d+[KMBT]", f["volume.0"]["t"])


def test_low_priced_instruments_show_four_significant_figures(run):
    b = bundles.low_priced(0.37)
    r = run(b)
    close = b["chart"]["close"][-1]
    assert close < 1
    for name in ("last", "open.0", "low.4", "sma10", "bb_lower", "low52", "atr14", "macd"):
        fact = r.facts[name]
        assert fact["t"] == ref_price(fact["v"]), name
    assert re.fullmatch(r"0\.\d{4,6}", r.facts["last"]["t"])
    digits = r.facts["last"]["t"].split(".")[1].lstrip("0")
    assert len(digits) == 4
    assert float(r.facts["atr14"]["t"]) > 0


def test_minor_unit_currency(run):
    r = run(bundles.minor_unit("GBp"))
    assert r.doc["currency"] == "GBp" and r.doc["unit"] == "pence"
    notes = [n["text"] for n in r.doc["notes"] if "pence" in n["text"]]
    assert len(notes) == 1 and "GBp" in notes[0] and "hundredth" in notes[0]
    assert re.fullmatch(r"\d{4}\.\d\d", r.facts["last"]["t"])
    assert r.out.splitlines()[0].split(" | ")[3] == "pence"
    for code, unit in (("GBX", "pence"), ("ZAc", "cents"), ("ILA", "agorot")):
        assert run(bundles.minor_unit(code)).doc["unit"] == unit
    plain = run(bundles.bundle())
    assert plain.doc["unit"] == "USD" and "hundredth" not in plain.note_text()


# ---------------------------------------------------------------------------------------
# Statement facts
# ---------------------------------------------------------------------------------------

def test_statement_facts_and_their_dates(run):
    b = bundles.bundle(seed=3)
    st = b["statements"]
    f = run(b).facts
    newest = {k: v[-1] for k, v in st.items()}
    direct = {"pe_trailing": "trailingPeRatio", "pe_forward": "trailingForwardPeRatio",
              "peg": "trailingPegRatio", "mcap": "trailingMarketCap",
              "rev_ttm": "trailingTotalRevenue", "ni_ttm": "trailingNetIncome",
              "rev_q": "quarterlyTotalRevenue", "ni_q": "quarterlyNetIncome",
              "debt": "quarterlyTotalDebt", "equity": "quarterlyStockholdersEquity"}
    for name, key in direct.items():
        assert f[name]["v"] == newest[key]["value"], name
        assert f[name]["as_of"] == newest[key]["asOf"], name
    for name in STATEMENT_FACTS:
        assert re.fullmatch(r"\d{4}-\d\d-\d\d", f[name]["as_of"]), name
    assert "as_of" not in f["rsi14"] and "as_of" not in f["last"]
    rev, ni = newest["trailingTotalRevenue"]["value"], newest["trailingNetIncome"]["value"]
    assert math.isclose(f["margin_ttm"]["v"], ni / rev * 100)
    debt = newest["quarterlyTotalDebt"]["value"]
    equity = newest["quarterlyStockholdersEquity"]["value"]
    assert math.isclose(f["debt_equity"]["v"], debt / equity * 100)
    q = st["quarterlyTotalRevenue"]
    assert math.isclose(f["rev_q_yoy"]["v"], (q[-1]["value"] / q[0]["value"] - 1) * 100)
    assert f["rev_q_yoy"]["as_of"] == q[-1]["asOf"] and f["rev_q_yoy"]["sign"] == "+"
    assert f["ni_q_yoy"]["v"] > 0


def test_statement_entries_may_arrive_in_any_order(run):
    b = bundles.bundle(seed=3)
    want = run(b).facts
    for series in b["statements"].values():
        series.reverse()
    got = run(b).facts
    assert {k: got[k] for k in STATEMENT_FACTS} == {k: want[k] for k in STATEMENT_FACTS}


@pytest.mark.parametrize("shift, present", [(0, True), (19, True), (-19, True), (25, False)])
def test_year_earlier_quarter_must_be_within_20_days(run, shift, present):
    b = bundles.bundle(seed=3)
    q = b["statements"]["quarterlyTotalRevenue"]
    newest = bundles.END - 40
    q[0]["asOf"] = bundles.iso(newest - 365 + shift)
    r = run(b)
    assert ("rev_q_yoy" in r.facts) is present
    if not present:
        assert re.search(r"\brev_q_yoy\b", r.note_text())
        assert "rev_q" in r.facts and "ni_q_yoy" in r.facts


def test_derived_statement_facts_are_absent_rather_than_misleading(run):
    b = bundles.bundle(seed=3)
    st = b["statements"]
    st["quarterlyNetIncome"][0]["value"] = -5e8        # a loss a year ago: no growth figure
    st["quarterlyStockholdersEquity"][-1]["value"] = -1e9
    st["trailingNetIncome"][-1]["asOf"] = bundles.iso(bundles.END - 200)  # dates differ
    st["trailingPegRatio"] = []
    st["trailingForwardPeRatio"] = [{"asOf": bundles.iso(bundles.END - 1), "value": None}]
    r = run(b)
    gone = ["ni_q_yoy", "debt_equity", "margin_ttm", "peg", "pe_forward"]
    assert not set(gone) & set(r.facts)
    assert "statements_absent" not in r.doc["flags"]
    notes = [n["text"] for n in r.doc["notes"] if "statement" in n["text"]]
    assert len(notes) == 1
    for name in gone:
        assert re.search(r"\b%s\b" % name, notes[0])
    assert r.facts["equity"]["t"] == "-1.00B USD" and "rev_q_yoy" in r.facts


def test_a_ratio_of_two_figures_needs_them_in_one_currency(run):
    # Net income in euros over revenue in dollars is not a margin, and debt in dollars over
    # equity in pounds is not a ratio.
    b = bundles.bundle(seed=3)
    st = b["statements"]
    whole = run(b).facts
    assert "margin_ttm" in whole and "debt_equity" in whole
    for entry in st["trailingNetIncome"]:
        entry["currency"] = "EUR"
    for entry in st["quarterlyStockholdersEquity"]:
        entry["currency"] = "GBP"
    r = run(b)
    assert r.rc == 0 and "statements_absent" not in r.doc["flags"]
    assert named_absent(r) == ["margin_ttm", "debt_equity"]
    assert [n["text"] for n in r.doc["notes"]] == [NO_FIGURE % "margin_ttm, debt_equity"]
    # The dates still agree: the currency alone decided.
    assert st["trailingNetIncome"][-1]["asOf"] == st["trailingTotalRevenue"][-1]["asOf"]
    # Each figure stays, with its own currency, and nothing else changes.
    assert r.facts["ni_ttm"]["t"] == whole["ni_ttm"]["t"].replace(" USD", " EUR")
    assert r.facts["equity"]["t"] == whole["equity"]["t"].replace(" USD", " GBP")
    for name in set(whole) - {"margin_ttm", "debt_equity", "ni_ttm", "equity"}:
        assert r.facts[name] == whole[name], name
    # Back in one currency, whichever it is, the two are there again.
    for entry in st["trailingTotalRevenue"]:
        entry["currency"] = "EUR"
    for entry in st["quarterlyTotalDebt"]:
        entry["currency"] = "GBP"
    again = run(b).facts
    assert again["margin_ttm"] == whole["margin_ttm"]
    assert again["debt_equity"] == whole["debt_equity"]


@pytest.mark.parametrize("pair, fact", [
    (("trailingNetIncome", "trailingTotalRevenue"), "margin_ttm"),
    (("quarterlyTotalDebt", "quarterlyStockholdersEquity"), "debt_equity")])
def test_a_figure_that_names_no_currency_is_not_assumed_to_share_one(run, pair, fact):
    for unnamed in pair:
        b = bundles.bundle(seed=3)
        for entry in b["statements"][unnamed]:
            del entry["currency"]
        r = run(b)
        assert fact not in r.facts and named_absent(r) == [fact], unnamed
    # When neither names one, as in a bundle from before the currency was kept, there is
    # nothing to tell them apart.
    b = bundles.bundle(seed=3)
    for key in pair:
        for entry in b["statements"][key]:
            del entry["currency"]
    assert fact in run(b).facts


@pytest.mark.parametrize("key, fact", [("quarterlyTotalRevenue", "rev_q_yoy"),
                                       ("quarterlyNetIncome", "ni_q_yoy")])
def test_growth_is_measured_against_a_year_earlier_figure_in_the_same_currency(run, key, fact):
    b = bundles.bundle(seed=3)
    q = b["statements"][key]
    whole = run(b).facts
    newest, old = q[-1]["value"], q[0]["value"]
    assert math.isclose(whole[fact]["v"], (newest / old - 1) * 100)
    # The quarter a year earlier was reported in another currency: no growth figure.
    q[0]["currency"] = "EUR"
    r = run(b)
    assert fact not in r.facts and named_absent(r) == [fact]
    assert r.facts[fact[:-4]] == whole[fact[:-4]]
    # So it is when that quarter names no currency, or when only the newest names none.
    del q[0]["currency"]
    assert fact not in run(b).facts
    q[0]["currency"] = "USD"
    del q[-1]["currency"]
    assert fact not in run(b).facts
    # A second figure for that quarter, in the newest one's currency, is the one compared,
    # though the figure in the other currency is the older of the two.
    q[-1]["currency"], q[0]["currency"] = "USD", "EUR"
    q.append({"asOf": bundles.iso(bundles.END - 40 - 364 + 3), "value": old * 2,
              "currency": "USD"})
    r = run(b)
    assert math.isclose(r.facts[fact]["v"], (newest / (old * 2) - 1) * 100)
    assert r.facts[fact]["as_of"] == q[-2]["asOf"] == bundles.iso(bundles.END - 40)


def test_no_statements_is_flagged(run):
    r = run(bundles.bundle(with_statements=False))
    assert "statements_absent" in r.doc["flags"]
    assert not set(STATEMENT_FACTS) & set(r.facts)
    notes = [n["text"] for n in r.doc["notes"] if "statement" in n["text"]]
    assert len(notes) == 1
    for name in STATEMENT_FACTS:
        assert re.search(r"\b%s\b" % name, notes[0])


# ---------------------------------------------------------------------------------------
# Facts that cannot be computed
# ---------------------------------------------------------------------------------------

def test_zero_volume_throughout(run):
    r = run(bundles.zero_volume())
    assert r.rc == 0 and r.doc["type"] == "INDEX"
    assert "mfi14" not in r.facts and "vol_ratio" not in r.facts
    notes = [n["text"] for n in r.doc["notes"] if "division by zero" in n["text"]]
    assert len(notes) == 1 and re.search(r"\bmfi14\b", notes[0]) and "vol_ratio" in notes[0]
    assert r.facts["vol_avg20"]["t"] == "0" and r.facts["volume.0"]["t"] == "0"
    assert "rsi14" in r.facts and "adx14" in r.facts


def test_flat_prices_give_absent_facts_not_numbers(run):
    r = run(bundles.flat_prices())
    assert r.rc == 0
    gone = ["rsi14", "bb_pctb", "adx14", "cci20", "stoch_k", "stoch_d", "stoch_j", "mfi14"]
    assert not set(gone) & set(r.facts)
    text = r.note_text()
    for name in gone:
        assert re.search(r"\b%s\b" % name, text)
    assert r.facts["macd"]["v"] == 0 and r.facts["ret63"]["t"] == "+0.00%"
    assert r.facts["atr14"]["t"] == "0.00" and r.facts["vol21"]["t"] == "0.00%"
    assert not re.search(r"nan|inf", json.dumps(r.doc["facts"]), re.I)


@pytest.mark.parametrize("price", [7660.6941, 103.289, 10.37, 0.07])
def test_a_flat_window_gives_absent_facts_at_any_price(run, price):
    # Twenty equal prices that are not exact binary values: their mean must be that price
    # exactly, or rounding noise turns the division by zero into a made-up %B and CCI.
    rows = bundles.walk(5, 240, start=price * 1.3, digits=4)[:-25]
    rows += [(price, price, price, price, 1000)] * 25
    r = run(bundles.bundle(rows=rows))
    assert r.rc == 0
    gone = ["bb_pctb", "cci20", "stoch_k", "stoch_d", "stoch_j", "mfi14"]
    assert not set(gone) & set(r.facts)
    assert set(gone) <= set(named_absent(r))
    assert r.facts["sma10"]["v"] == r.facts["bb_upper"]["v"] == r.facts["bb_lower"]["v"] == price
    for name in ("sma10_gap", "chg1", "ret5", "move_max"):
        assert r.facts[name]["t"] == "+0.00%" and r.facts[name]["v"] == 0, name
    assert r.facts["vol_ratio"]["v"] == 1 and r.facts["vol21"]["v"] == 0


def named_absent(r):
    names = []
    for n in r.doc["notes"]:
        if "absent: " in n["text"]:
            assert n["text"].endswith(".")
            names += n["text"][:-1].split("absent: ")[1].split(", ")
    return names


def test_every_absent_fact_is_named_in_exactly_one_note(run):
    cases = [bundles.bundle(), bundles.short_history(70), bundles.zero_volume(),
             bundles.flat_prices(), bundles.bundle(with_statements=False), bundles.crypto(),
             bundles.too_few(60), bundles.history_result(bundles.short_history(90))]
    for b in cases:
        r = run(b)
        assert r.rc == 0
        names = named_absent(r)
        assert len(names) == len(set(names))
        assert set(names) == set(ALL_FACTS) - set(r.facts)
        assert set(r.facts) <= set(ALL_FACTS)


def test_an_overflow_gives_an_absent_fact_not_inf(run):
    b = bundles.bundle(seed=3)
    b["statements"]["trailingNetIncome"][-1]["value"] = 1e308
    r = run(b)
    assert r.rc == 0 and "margin_ttm" not in r.facts and "margin_ttm" in named_absent(r)
    assert not re.search(r"\binf|\bnan", json.dumps(r.doc["facts"]), re.I)


# ---------------------------------------------------------------------------------------
# Exit codes and the no-traceback rule
# ---------------------------------------------------------------------------------------

def script(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT)] + [str(a) for a in args],
                          capture_output=True, cwd=cwd, timeout=60)


def error_lines(proc):
    lines = proc.stderr.decode("ascii").splitlines()
    return [x for x in lines if x != "WARN unstamped development copy"]


def test_a_clean_run_as_a_program(tmp_path):
    src, out = tmp_path / "bundle.json", tmp_path / "brief.json"
    src.write_bytes(bundles.dumps(bundles.bundle()))
    p = script(src, out)
    assert p.returncode == 0 and error_lines(p) == []
    text = p.stdout.decode("ascii")
    assert text.splitlines()[-1].startswith("BRIEF ok TEST 2026-11-10 facts=")
    raw = out.read_bytes()
    assert raw.isascii() and b"\r" not in raw and raw.endswith(b"\n")
    assert json.loads(raw)["schema"] == "brief.v1"


@pytest.mark.parametrize("args", [
    [], ["only-one"], ["a", "b", "c"], ["--preflight"], ["--bogus", "a", "b"],
    ["a", "b", "--symbol"], ["--version", "extra"], ["a", "--preflight", "b"],
])
def test_usage_errors_exit_4(args):
    p = script(*args)
    assert p.returncode == 4 and p.stdout == b""
    lines = error_lines(p)
    assert len(lines) == 1 and lines[0].startswith("ERROR E_USAGE "), lines


def test_unreadable_input_and_unwritable_output_exit_4(tmp_path):
    p = script(tmp_path / "missing.json", tmp_path / "brief.json")
    assert p.returncode == 4 and error_lines(p)[0].startswith("ERROR E_USAGE ")
    src = tmp_path / "bundle.json"
    src.write_bytes(bundles.dumps(bundles.bundle()))
    p = script(src, tmp_path / "no-such-folder" / "brief.json")
    assert p.returncode == 4 and p.stdout == b""
    assert error_lines(p)[0].startswith("ERROR E_USAGE ")


def test_a_closed_output_stream_is_not_called_an_input_problem(tmp_path):
    # The sheet piped into a reader that stops early (head, a pager): the answer must not be
    # a STOP that blames the input, which the user would be shown as the reason.
    src, out = tmp_path / "bundle.json", tmp_path / "brief.json"
    src.write_bytes(bundles.dumps(bundles.bundle()))
    for source, extra in ((src, []), (None, ["--symbol", "TEST"])):
        if source is None:  # a price file prints a shorter sheet
            source = tmp_path / "prices.csv"
            source.write_text(bundles.csv_text(bundles.bundle()), encoding="ascii")
        p = subprocess.Popen([sys.executable, str(SCRIPT), str(source), str(out)] + extra,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p.stdout.close()  # no reader is left before the script starts to print
        err = p.stderr.read().decode("ascii")
        p.stderr.close()
        assert p.wait(timeout=60) == 4, err
        assert error_lines(subprocess.CompletedProcess([], 4, b"", err.encode())) == [
            "ERROR E_USAGE cannot read the input file or write the output file"]
        assert json.loads(out.read_text(encoding="ascii"))["schema"] == "brief.v1"
    # The same holds for the one-line answers.
    p = subprocess.Popen([sys.executable, str(SCRIPT), "--version"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p.stdout.close()
    err = p.stderr.read()
    p.stderr.close()
    assert p.wait(timeout=60) == 4 and b"STOP" not in err and b"Traceback" not in err, err


@pytest.mark.parametrize("make, code", [
    (lambda: b"\x00\x01garbage", "E_INPUT"),
    (lambda: bundles.dumps(bundles.bundle(typ="CURRENCY")), "E_TYPE"),
    (lambda: bundles.dumps(bundles.bad_prices()), "E_OHLC"),
    (lambda: bundles.dumps(bundles.too_few()), "E_FEW"),
    (lambda: bundles.dumps(bundles.date_gap()), "E_GAP"),
    (lambda: bundles.dumps(bundles.stale()), "E_STALE"),
    (lambda: bundles.dumps(bundles.future_bar()), "E_STALE"),
    (lambda: bundles.dumps(bundles.history_result(bundles.same_day_case())), "E_DATES"),
])
def test_stops_exit_2_with_one_line_and_no_traceback(tmp_path, make, code):
    src, out = tmp_path / "in.json", tmp_path / "brief.json"
    src.write_bytes(make())
    p = script(src, out)
    assert p.returncode == 2 and p.stdout == b"" and not out.exists()
    lines = error_lines(p)
    assert len(lines) == 1 and lines[0].startswith("STOP %s " % code), lines
    assert b"Traceback" not in p.stderr


def _damage(doc, rng):
    """Break one thing in a bundle the way a truncated or hand-edited file would."""
    doc = bundles.clone(doc)
    ch = doc["chart"]
    junk = rng.choice([None, "x", [], {}, -1, 1e308, True, "NaN", [None], {"a": 1}])
    where = rng.randrange(12)
    if where == 0:
        del doc[rng.choice(list(doc))]
    elif where == 1:
        del ch[rng.choice(list(ch))]
    elif where == 2:
        ch[rng.choice(list(ch))] = junk
    elif where == 3:
        ch[rng.choice(bundles.COLS)][rng.randrange(-70, 0)] = junk
    elif where == 4:
        ch["timestamp"][rng.randrange(-70, 0)] = junk
    elif where == 5:
        ch["meta"][rng.choice(list(ch["meta"]))] = junk
    elif where == 6:
        doc[rng.choice(list(doc))] = junk
    elif where == 7:
        doc["news"][0][rng.choice(list(doc["news"][0]))] = junk
    elif where == 8:
        key = rng.choice(list(doc["statements"]))
        doc["statements"][key] = rng.choice([junk, [junk], [{"asOf": junk, "value": 1}],
                                             [{"asOf": "2026-01-01", "value": junk}]])
    elif where == 9:
        ch["meta"]["currentTradingPeriod"]["regular"] = junk
    elif where == 10:
        for k in bundles.COLS:
            ch[k] = ch[k][:rng.randrange(0, 80)]
        ch["timestamp"] = ch["timestamp"][:len(ch["close"])]
    else:
        doc["errors"] = junk
    return doc


def test_damaged_bundles_never_raise_or_print_a_traceback(run):
    rng = random.Random(4242)
    base = bundles.bundle(seed=6, n=120)
    seen = set()
    for _ in range(400):
        text = json.dumps(_damage(base, rng))
        r = run(text)
        seen.add(r.rc)
        if r.rc == 0:
            assert r.err == [] and r.doc["schema"] == "brief.v1"
            assert r.out.isascii() and r.out.splitlines()[-1].startswith("BRIEF ok ")
            for f in r.doc["facts"].values():
                assert not re.search(r"nan|inf", f["t"], re.I), f
        else:
            assert r.rc == 2 and r.out == "" and r.doc is None
            assert len(r.err) == 1 and re.match(r"STOP E_[A-Z]+ \S", r.err[0]), r.err
    assert seen == {0, 2}


def test_damaged_bundles_with_holes_in_the_newest_rows_never_raise(run):
    # The row rules read the session facts and the last regular price. With a hole among
    # the newest rows, damage there is read too and must end the same two ways.
    rng = random.Random(777)
    bases = [bundles.fill_case(n=120), bundles.behind_case(n=120),
             bundles.behind_case(holes=(-1,), n=120), bundles.same_day_case(n=120),
             bundles.behind_crypto()]
    seen, flagged = set(), set()
    for k in range(500):
        r = run(json.dumps(_damage(bases[k % len(bases)], rng)))
        seen.add(r.rc)
        if r.rc == 0:
            assert r.err == [] and list(r.doc) == BRIEF_KEYS
            assert r.out.isascii() and r.out.splitlines()[-1].startswith("BRIEF ok ")
            flagged |= set(r.doc["flags"])
            assert not re.search(r"\bnan\b|\binf\b|None", r.doc["as_of"] + r.note_text(), re.I)
        else:
            assert r.rc == 2 and r.out == "" and r.doc is None
            assert len(r.err) == 1 and re.match(r"STOP E_[A-Z]+ \S", r.err[0]), r.err
    assert seen == {0, 2} and {"close_filled", "prices_behind"} <= flagged


# ---------------------------------------------------------------------------------------
# Integrity: SELF, SIBLINGS, the preflight and the known-answer test
# ---------------------------------------------------------------------------------------

BLANK = "000000000000"
# The order in which the contract writes the SIBLINGS line.
SIBLING_NAMES = ("get_data.py", "render.py", "page.py")
STAND_INS = {
    "get_data.py": 'VERSION = "2.0.0"\nSELF = "aaaaaaaaaaaa"\nprint("get")\n',
    "render.py": 'VERSION = "2.0.0"\nSELF = "bbbbbbbbbbbb"\nprint("render")\n',
    "page.py": 'VERSION = "2.0.0"\nSELF = "dddddddddddd"\nprint("page")\n'}


def contract_hash(text):
    """The integrity hash, written out apart from the scripts: SHA-256 of the bytes with LF
    endings and no trailing whitespace, each line that is exactly SELF = "<12 hex>" read as
    the one word SELF; first 12 hex digits. The stamp line keeps its place, so a second, a
    moved or a missing one changes the hash."""
    data = text.encode("utf-8") if isinstance(text, str) else text
    lines = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
    kept = [re.sub(rb'^SELF = "[0-9a-f]{12}"$', b"SELF", x.rstrip()) for x in lines]
    return hashlib.sha256(b"\n".join(kept).rstrip()).hexdigest()[:12]


def set_line(text, name, value):
    out, n = re.subn(r"(?m)^%s = .*$" % name, "%s = %s" % (name, value), text)
    assert n == 1
    return out


def stamped(tmp_path, stamp=True, siblings=None, absent=()):
    """A folder holding brief.py and its siblings (stand-ins unless given), stamped the way
    the build does. A file named in ``absent`` is stamped in brief.py but not written."""
    folder = tmp_path / "scripts"
    folder.mkdir(exist_ok=True)
    siblings = siblings or STAND_INS
    assert set(siblings) == set(SIBLING_NAMES)
    for name, text in siblings.items():
        if name not in absent:
            (folder / name).write_bytes(text.encode("ascii"))
    text = SCRIPT.read_text(encoding="ascii")
    hashes = {k: contract_hash(siblings[k]) if stamp else BLANK for k in SIBLING_NAMES}
    text = set_line(text, "SIBLINGS", json.dumps(hashes))
    text = set_line(text, "SELF", '"%s"' % (contract_hash(text) if stamp else BLANK))
    (folder / "brief.py").write_bytes(text.encode("ascii"))
    return folder


def preflight(folder, script_path=None):
    return subprocess.run(
        [sys.executable, str(script_path or folder / "brief.py"), "--preflight", str(folder)],
        capture_output=True, timeout=60)


def preflight_line(*names):
    return "PREFLIGHT ok 2.0.0 python %d.%d.%d checked %s" % (
        sys.version_info[:3] + (" ".join(names),))


def test_integrity_lines_have_the_contract_form():
    text = SCRIPT.read_text(encoding="ascii")
    assert len(re.findall(r'(?m)^SELF = "[0-9a-f]{12}"$', text)) == 1
    assert len(re.findall(r'(?m)^KAT = "[0-9a-f]{12}"$', text)) == 1
    assert len(re.findall(
        r'(?m)^SIBLINGS = \{"get_data.py": "[0-9a-f]{12}", "render.py": "[0-9a-f]{12}", '
        r'"page.py": "[0-9a-f]{12}"\}$', text)) == 1
    assert list(brief.SIBLINGS) == list(SIBLING_NAMES)
    assert brief.SELF in (BLANK, contract_hash(text))
    for name in SIBLING_NAMES:
        if (SCRIPT.parent / name).exists():
            sibling = (SCRIPT.parent / name).read_bytes()
            assert brief.SIBLINGS[name] in (BLANK, contract_hash(sibling)), name
    # A development copy is unstamped as a whole, a shipped copy is stamped as a whole.
    stamps = [brief.SELF] + list(brief.SIBLINGS.values())
    assert BLANK not in stamps or set(stamps) == {BLANK}


def test_preflight_passes_on_a_stamped_copy(tmp_path):
    p = preflight(stamped(tmp_path))
    assert p.returncode == 0 and p.stderr == b""
    assert p.stdout.decode("ascii").splitlines() == [
        preflight_line("brief.py", "get_data.py", "page.py", "render.py")]


@pytest.mark.parametrize("absent", [("get_data.py",), ("page.py",), ("get_data.py", "page.py")])
def test_preflight_passes_without_the_optional_files_and_says_what_it_checked(tmp_path, absent):
    # A host with no network has no use for get_data.py; a run with no page, none for page.py.
    p = preflight(stamped(tmp_path, absent=absent))
    assert p.returncode == 0 and p.stderr == b""
    checked = [n for n in ("brief.py", "get_data.py", "page.py", "render.py") if n not in absent]
    assert p.stdout.decode("ascii").splitlines() == [preflight_line(*checked)]


@pytest.mark.parametrize("stamp", [True, False])
def test_preflight_needs_render(tmp_path, stamp):
    # render.py must be there, in a development copy too.
    for absent in (("render.py",), ("render.py", "page.py", "get_data.py")):
        p = preflight(stamped(tmp_path, stamp=stamp, absent=absent))
        assert p.returncode == 3 and p.stdout == b""
        lines = p.stderr.decode("ascii").splitlines()
        assert lines[-1].startswith("ERROR E_SIBLING render.py is missing or was altered"), lines
        assert len(lines) == 1
        for name in SIBLING_NAMES:
            (tmp_path / "scripts" / name).unlink(missing_ok=True)


def test_a_stamped_copy_runs_without_a_warning(tmp_path):
    folder = stamped(tmp_path)
    src = tmp_path / "bundle.json"
    src.write_bytes(bundles.dumps(bundles.bundle()))
    p = subprocess.run([sys.executable, str(folder / "brief.py"), str(src),
                        str(tmp_path / "brief.json")], capture_output=True, timeout=60)
    assert p.returncode == 0 and p.stderr == b""


def test_an_unstamped_copy_warns_once_and_continues(tmp_path):
    folder = stamped(tmp_path, stamp=False)
    p = preflight(folder)
    assert p.returncode == 0
    assert p.stderr.decode("ascii").splitlines() == ["WARN unstamped development copy"]
    assert p.stdout.decode("ascii").splitlines() == [
        preflight_line("brief.py", "get_data.py", "page.py", "render.py")]
    v = subprocess.run([sys.executable, str(folder / "brief.py"), "--version"],
                       capture_output=True, timeout=60)
    assert v.returncode == 0 and v.stdout.strip() == b"trading-agents-desk brief.py 2.0.0"
    assert v.stderr.decode("ascii").splitlines() == ["WARN unstamped development copy"]
    # Its stamps are not compared, so a sibling that is still being changed passes.
    (folder / "page.py").write_bytes(b"print('work in progress')\n")
    p = preflight(folder)
    assert p.returncode == 0 and p.stdout.startswith(b"PREFLIGHT ok 2.0.0 python ")


def test_the_shipped_copy_as_it_stands_passes_its_own_preflight():
    # The development copy in the repository, with whichever siblings are beside it.
    p = preflight(SCRIPT.parent, script_path=SCRIPT)
    lines = [x for x in p.stderr.decode("ascii").splitlines()
             if x != "WARN unstamped development copy"]
    if brief.SELF != BLANK or (SCRIPT.parent / "render.py").exists():
        assert p.returncode == 0 and lines == [], p.stderr
        names = p.stdout.decode("ascii").split(" checked ")[1].split()
        assert names == sorted(names) and {"brief.py", "render.py"} <= set(names)
        assert set(names) == {"brief.py"} | {
            n for n in SIBLING_NAMES if (SCRIPT.parent / n).exists()}


def test_line_endings_and_trailing_whitespace_do_not_change_the_hash(tmp_path):
    folder = stamped(tmp_path)
    for name in ("brief.py",) + SIBLING_NAMES:
        text = (folder / name).read_text(encoding="ascii")
        lines = [x + "  " if i % 7 == 0 else x for i, x in enumerate(text.split("\n"))]
        (folder / name).write_bytes(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
    p = preflight(folder)
    assert p.returncode == 0, p.stderr
    # Old Mac line ends count as line ends too.
    for name in ("brief.py",) + SIBLING_NAMES:
        text = (folder / name).read_bytes().replace(b"\r\n", b"\n")
        if name != "brief.py":  # Python itself cannot run a file that ends its lines with CR
            text = text.replace(b"\n", b"\r")
        (folder / name).write_bytes(text)
    assert preflight(folder).returncode == 0


def test_an_altered_copy_of_itself_exits_3(tmp_path):
    folder = stamped(tmp_path)
    path = folder / "brief.py"
    text = path.read_text(encoding="ascii")
    assert "at least 60" in text
    path.write_bytes(text.replace("at least 60", "at least 61").encode("ascii"))
    for args in (["--preflight", str(folder)], ["--version"], ["in.json", "out.json"]):
        p = subprocess.run([sys.executable, str(path)] + args, capture_output=True, timeout=60)
        assert p.returncode == 3 and p.stdout == b""
        lines = p.stderr.decode("ascii").splitlines()
        assert len(lines) == 1 and lines[0].startswith("ERROR E_SELF "), lines


def test_a_copy_of_itself_with_a_mark_or_an_extra_stamp_line_exits_3(tmp_path):
    folder = stamped(tmp_path)
    path = folder / "brief.py"
    good = path.read_bytes()
    changes = {
        "a byte-order mark": b"\xef\xbb\xbf" + good,
        "a comment on a second stamp line": good.replace(
            b"\nSELF = ", b'\nSELF = "000000000000"  # x\nSELF = ', 1),
        "code on a line that starts like the stamp": good.replace(
            b"\nSELF = ", b'\nSELF = len("added code")\nSELF = ', 1),
        "a no-break space in a sentence": good.replace(
            b"at least 60", "at\u00a0least 60".encode("utf-8"), 1)}
    for what, data in changes.items():
        assert data != good, what
        path.write_bytes(data)
        for args in (["--preflight", str(folder)], ["--version"]):
            p = subprocess.run([sys.executable, str(path)] + args, capture_output=True,
                               timeout=60)
            assert p.returncode == 3 and p.stdout == b"", what
            assert p.stderr.startswith(b"ERROR E_SELF "), (what, p.stderr)


def test_a_wrong_self_stamp_exits_3(tmp_path):
    folder = stamped(tmp_path)
    path = folder / "brief.py"
    path.write_bytes(set_line(path.read_text(encoding="ascii"), "SELF", '"0123456789ab"')
                     .encode("ascii"))
    p = preflight(folder)
    assert p.returncode == 3 and p.stderr.startswith(b"ERROR E_SELF ")


@pytest.mark.parametrize("name", SIBLING_NAMES)
def test_an_altered_sibling_exits_3_whether_it_is_optional_or_not(tmp_path, name):
    folder = stamped(tmp_path)
    path = folder / name
    good = path.read_bytes()
    path.write_bytes(good.replace(b"print", b"pront"))
    p = preflight(folder)
    assert p.returncode == 3 and p.stdout == b""
    lines = p.stderr.decode("ascii").splitlines()
    assert len(lines) == 1 and lines[0].startswith("ERROR E_SIBLING " + name + " "), lines
    # An empty file is an altered file, not a missing one.
    path.write_bytes(b"")
    p = preflight(folder)
    assert p.returncode == 3 and p.stderr.startswith(b"ERROR E_SIBLING " + name.encode())
    # Another stamp on a sibling's own stamp line is not an alteration of its text.
    path.write_bytes(re.sub(rb'SELF = "[a-z]{12}"', b'SELF = "0123456789ab"', good))
    assert preflight(folder).returncode == 0
    # Anything else on a line that starts like the stamp is: only a line that is exactly
    # the stamp is read as the one word SELF. Code on such a line used to pass with
    # "PREFLIGHT ok".
    for line in (b'SELF = print("added")\n', b'SELF = "0123456789ab"  # note\n',
                 b'SELF = "0123456789AB"\n', b"SELF = VERSION\n"):
        path.write_bytes(good.replace(b"SELF = ", line + b"SELF = ", 1))
        p = preflight(folder)
        assert p.returncode == 3 and p.stdout == b"", line
        assert p.stderr.startswith(b"ERROR E_SIBLING " + name.encode()), line
    # So is a byte-order mark in front of the file, and a no-break space for a space.
    for data in (b"\xef\xbb\xbf" + good, good.replace(b" = ", "\u00a0= ".encode("utf-8"), 1)):
        path.write_bytes(data)
        p = preflight(folder)
        assert p.returncode == 3 and p.stderr.startswith(b"ERROR E_SIBLING " + name.encode())
    # Missing: an error for render.py, and nothing to check for the other two.
    path.unlink()
    p = preflight(folder)
    if name == "render.py":
        assert p.returncode == 3 and p.stderr.startswith(b"ERROR E_SIBLING render.py ")
    else:
        assert p.returncode == 0 and p.stderr == b""
        assert name not in p.stdout.decode("ascii").split()


ZEROS = b'SELF = "000000000000"\n'


def stamp_changes(good):
    """Copies of a stamped script in which the stamp line is no longer the one line the
    build wrote, each with a short name. While a stamp line was left out of the hash, every
    one of them had the hash of the good copy."""
    lines = re.findall(rb'(?m)^SELF = "[0-9a-f]{12}"\n', good)
    assert len(lines) == 1 and lines[0] != ZEROS
    line = lines[0]
    without = good.replace(line, b"", 1)
    # The line above the stamp is the one-line VERSION in every script.
    above, n = re.subn(rb'(?m)^(.*\n)(SELF = "[0-9a-f]{12}"\n)', rb"\2\1", good)
    assert n == 1 and above != good
    return {
        "a second stamp line": good.replace(line, line + line, 1),
        "a second stamp line at the end": good + line,
        "the stamp line moved up a line": above,
        "the stamp line moved to the end": without + line,
        "no stamp line": without,
        "zeros added after the stamp": good.replace(line, line + ZEROS, 1),
        "zeros added before the stamp": good.replace(line, ZEROS + line, 1),
        "zeros added at the end": good + ZEROS,
    }


STAMP_CHANGES = sorted(stamp_changes(b'A = 1\nSELF = "0123456789ab"\nB = 2\n'))


def real_scripts(tmp_path):
    """A stamped folder of the four real scripts, each sibling carrying its own stamp."""
    if not all((SCRIPT.parent / n).exists() for n in SIBLING_NAMES):
        pytest.skip("the sibling scripts are not here")
    real = {n: (SCRIPT.parent / n).read_text(encoding="ascii") for n in SIBLING_NAMES}
    real = {n: set_line(t, "SELF", '"%s"' % contract_hash(t)) for n, t in real.items()}
    folder = stamped(tmp_path, siblings=real)
    p = preflight(folder)
    assert p.returncode == 0 and p.stderr == b"", p.stderr
    return folder


def test_the_hash_keeps_the_place_of_the_stamp_line(tmp_path):
    def digest(data):
        path = tmp_path / "sample.py"
        path.write_bytes(data)
        got = brief.digest(str(path))
        assert got == contract_hash(data), data  # the script and the tests hash alike
        return got

    stamp, other = b'SELF = "0123456789ab"\n', b'SELF = "ba9876543210"\n'
    good = digest(b"A = 1\n" + stamp + b"B = 2\n")
    # The stamp's own digits are the one thing that does not count.
    assert digest(b"A = 1\n" + other + b"B = 2\n") == good
    assert digest(b"A = 1\n" + ZEROS + b"B = 2\n") == good
    # Line ends and trailing whitespace do not count either, on the stamp line or elsewhere.
    assert digest(b"A = 1\r\n" + stamp[:-1] + b"\r\nB = 2\r\n") == good
    assert digest(b"A = 1  \n" + stamp[:-1] + b" \t\nB = 2\t\n\n\n") == good
    assert digest(b"A = 1\r" + stamp[:-1] + b"\rB = 2") == good
    # A second, a moved or a missing stamp line does, and so does anything else.
    changed = [b"A = 1\nB = 2\n", b"A = 1\n" + stamp + stamp + b"B = 2\n",
               b"A = 1\n" + stamp + ZEROS + b"B = 2\n", b"A = 1\n" + stamp + b"B = 2\n" + ZEROS,
               b"A = 1\nB = 2\n" + stamp, stamp + b"A = 1\nB = 2\n",
               b"A = 1\n " + stamp + b"B = 2\n", b"A = 1\n" + stamp.upper() + b"B = 2\n",
               b"A = 1\n" + stamp[:-1] + b"  # x\nB = 2\n", b"A = 1\n\n" + stamp + b"B = 2\n",
               b"\xef\xbb\xbfA = 1\n" + stamp + b"B = 2\n", b"A = 2\n" + stamp + b"B = 2\n"]
    for data in changed:
        assert digest(data) != good, data
    # Two stamp lines hash alike whatever their digits: each is the same one word.
    assert digest(changed[1]) == digest(changed[2])
    for data in (b"", b"\n", stamp, b"\x0c\n", b"a\x0bb \x0c\n", b"a\r\r\nb", b"\r\n\r\n"):
        digest(data)


@pytest.mark.parametrize("name", SIBLING_NAMES)
@pytest.mark.parametrize("what", STAMP_CHANGES)
def test_a_sibling_whose_stamp_line_was_doubled_moved_or_removed_is_refused(tmp_path, name, what):
    folder = real_scripts(tmp_path)
    path = folder / name
    data = stamp_changes(path.read_bytes())[what]
    path.write_bytes(data)
    p = preflight(folder)
    assert p.returncode == 3 and p.stdout == b"", (what, p.stdout)
    assert p.stderr.decode("ascii").splitlines() == [
        "ERROR E_SIBLING %s is missing or was altered; load the skill files again" % name]


# The one change of the stamp line a copy of brief.py does not refuse in itself.
ZEROS_AFTER = "zeros added after the stamp"


@pytest.mark.parametrize("what", STAMP_CHANGES)
def test_a_copy_of_itself_whose_stamp_line_was_doubled_moved_or_removed_exits_3(tmp_path, what):
    # All but one. With "zeros added after the stamp" Python reads the second line, so the
    # copy takes itself for an unstamped development copy: it warns and runs. That is a
    # standing decision, and the test after this one holds it in full.
    folder = real_scripts(tmp_path)
    path = folder / "brief.py"
    path.write_bytes(stamp_changes(path.read_bytes())[what])
    src = tmp_path / "bundle.json"
    src.write_bytes(bundles.dumps(bundles.bundle()))
    for args in (["--preflight", str(folder)], ["--version"], [str(src), str(tmp_path / "b.json")]):
        p = subprocess.run([sys.executable, str(path)] + args, capture_output=True, timeout=60)
        if what == ZEROS_AFTER:
            assert p.returncode == 0 and p.stdout != b"", (args[0], p.stdout, p.stderr)
            assert p.stderr == b"WARN unstamped development copy\n", (args[0], p.stderr)
            continue
        assert p.returncode == 3 and p.stdout == b"", (what, args[0], p.stdout, p.stderr)
        lines = p.stderr.decode("ascii").splitlines()
        assert len(lines) == 1 and lines[0].startswith("ERROR E_SELF "), (what, lines)
    assert (tmp_path / "b.json").exists() == (what == ZEROS_AFTER)


def test_a_stamp_of_zeros_added_after_the_real_one_makes_an_unstamped_development_copy(tmp_path):
    # A standing decision, kept as it is: Python reads the second of the two lines, so the
    # copy holds a stamp of zeros and takes itself for a development copy, whose own stamp
    # is not compared. It says so on stderr at every run and does its work. The hash rule is
    # unchanged: the copy does not have the hash of the stamped script, a sibling changed
    # the same way is refused, and so is a copy of brief.py with the zeros anywhere else.
    assert ZEROS_AFTER in STAMP_CHANGES
    folder = real_scripts(tmp_path)
    path = folder / "brief.py"
    good = path.read_bytes()
    changed = stamp_changes(good)[ZEROS_AFTER]
    path.write_bytes(changed)
    assert changed.count(b"\nSELF = ") == 2 and contract_hash(changed) != contract_hash(good)
    src, out = tmp_path / "bundle.json", tmp_path / "b.json"
    src.write_bytes(bundles.dumps(bundles.bundle()))
    warn = b"WARN unstamped development copy\n"
    p = subprocess.run([sys.executable, str(path), "--preflight", str(folder)],
                       capture_output=True, timeout=60)
    assert (p.returncode, p.stderr) == (0, warn)
    assert p.stdout.decode("ascii").splitlines() == [
        preflight_line("brief.py", "get_data.py", "page.py", "render.py")]
    p = subprocess.run([sys.executable, str(path), "--version"], capture_output=True, timeout=60)
    assert (p.returncode, p.stderr) == (0, warn)
    assert p.stdout == b"trading-agents-desk brief.py 2.0.0\n"
    p = subprocess.run([sys.executable, str(path), str(src), str(out)], capture_output=True,
                       timeout=60)
    assert (p.returncode, p.stderr) == (0, warn)
    assert p.stdout.decode("ascii").splitlines()[-1].startswith("BRIEF ok TEST 2026-11-10 ")
    assert json.loads(out.read_text(encoding="ascii"))["schema"] == "brief.v1"
    # Its siblings are still held to the stamps it carries for them.
    (folder / "page.py").write_bytes(b"print('altered')\n")
    p = subprocess.run([sys.executable, str(path), "--preflight", str(folder)],
                       capture_output=True, timeout=60)
    assert (p.returncode, p.stdout) == (3, b"")
    assert p.stderr.decode("ascii").splitlines() == [
        "ERROR E_SIBLING page.py is missing or was altered; load the skill files again"]


@pytest.mark.parametrize("change", [
    "CRLF line ends", "spaces after every line", "spaces and a tab after the stamp line",
    "blank lines at the end", "no line end on the last line"])
def test_a_correct_copy_passes_whatever_its_line_ends_and_trailing_spaces(tmp_path, change):
    folder = real_scripts(tmp_path)
    for name in ("brief.py",) + SIBLING_NAMES:
        text = (folder / name).read_bytes()
        if change == "CRLF line ends":
            text = text.replace(b"\n", b"\r\n")
        elif change == "spaces after every line":
            text = text.replace(b"\n", b"   \n")
        elif change == "spaces and a tab after the stamp line":
            text, n = re.subn(rb'(?m)^(SELF = "[0-9a-f]{12}")$', rb"\1  \t ", text)
            assert n == 1
        elif change == "blank lines at the end":
            text += b"\n\n  \n"
        else:
            text = text.rstrip(b"\n")
        assert text != (folder / name).read_bytes()
        (folder / name).write_bytes(text)
    p = preflight(folder)
    assert p.returncode == 0 and p.stderr == b"", p.stderr
    assert p.stdout.decode("ascii").splitlines() == [
        preflight_line("brief.py", "get_data.py", "page.py", "render.py")]
    # The changed copy of brief.py still does its work.
    src, out = tmp_path / "bundle.json", tmp_path / "brief.json"
    src.write_bytes(bundles.dumps(bundles.bundle()))
    p = subprocess.run([sys.executable, str(folder / "brief.py"), str(src), str(out)],
                       capture_output=True, timeout=60)
    assert p.returncode == 0 and p.stderr == b""
    assert json.loads(out.read_text(encoding="ascii"))["schema"] == "brief.v1"


def test_preflight_reads_siblings_from_the_given_folder(tmp_path):
    folder = stamped(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    p = preflight(other, script_path=folder / "brief.py")
    assert p.returncode == 3 and p.stderr.startswith(b"ERROR E_SIBLING render.py")
    for tail in ("/", "//", "/."):
        p = preflight(str(folder) + tail, script_path=folder / "brief.py")
        assert p.returncode == 0, tail
    # The folder's files are the ones named in the answer, not the running script's.
    (other / "render.py").write_bytes((folder / "render.py").read_bytes())
    p = preflight(other, script_path=folder / "brief.py")
    assert p.returncode == 0
    assert p.stdout.decode("ascii").splitlines() == [preflight_line("brief.py", "render.py")]


@pytest.mark.skipif(os.sep != "/", reason="a backslash is a separator on this system")
def test_preflight_takes_the_folder_name_as_it_was_given(tmp_path):
    # Each path is the folder, a slash and the name. A folder whose own name ends in a
    # backslash used to lose it, and the files of the folder beside it were read instead.
    folder = stamped(tmp_path)
    odd = tmp_path / "copy\\"
    shutil.copytree(str(folder), str(odd))
    assert odd.name.endswith("\\") and sorted(x.name for x in odd.iterdir()) == [
        "brief.py", "get_data.py", "page.py", "render.py"]
    for beside in (None, "nothing", "altered"):
        if beside:
            (tmp_path / "copy").mkdir(exist_ok=True)
        if beside == "altered":
            for name in SIBLING_NAMES:
                (tmp_path / "copy" / name).write_bytes(b"print('another file')\n")
        p = preflight(odd, script_path=folder / "brief.py")
        assert p.returncode == 0 and p.stderr == b"", (beside, p.stderr)
        assert p.stdout.decode("ascii").splitlines() == [
            preflight_line("brief.py", "get_data.py", "page.py", "render.py")]
    # And a file altered in the folder that was named is found there.
    (odd / "page.py").write_bytes(b"print('altered')\n")
    p = preflight(odd, script_path=folder / "brief.py")
    assert p.returncode == 3 and p.stderr.startswith(b"ERROR E_SIBLING page.py ")


def test_the_real_siblings_pass_a_stamped_preflight_and_a_changed_one_is_refused(tmp_path):
    if not all((SCRIPT.parent / n).exists() for n in SIBLING_NAMES):
        pytest.skip("the sibling scripts are not here")
    real = {n: (SCRIPT.parent / n).read_text(encoding="ascii") for n in SIBLING_NAMES}
    real = {n: set_line(t, "SELF", '"%s"' % contract_hash(t)) if "\nSELF = " in t else t
            for n, t in real.items()}
    folder = stamped(tmp_path, siblings=real)
    p = preflight(folder)
    assert p.returncode == 0 and p.stderr == b"", p.stderr
    assert p.stdout.decode("ascii").splitlines() == [
        preflight_line("brief.py", "get_data.py", "page.py", "render.py")]
    for name in SIBLING_NAMES:
        path = folder / name
        good = path.read_bytes()
        i = good.index(b"\ndef ") + 1
        path.write_bytes(good[:i] + b"dof" + good[i + 3:])
        p = preflight(folder)
        assert p.returncode == 3 and p.stdout == b""
        assert p.stderr.startswith(b"ERROR E_SIBLING " + name.encode()), p.stderr
        path.write_bytes(good)
    assert preflight(folder).returncode == 0


def test_single_character_mutations_are_refused(tmp_path):
    folder = stamped(tmp_path)
    path = folder / "brief.py"
    good = path.read_text(encoding="ascii")
    rng = random.Random(2026)
    spots = [i for i, ch in enumerate(good) if not ch.isspace()]
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789+-*/=<>()[]{}.,:\"'%"
    exit_3 = 0
    for _ in range(60):
        i = rng.choice(spots)
        new = rng.choice([c for c in alphabet if c != good[i]])
        path.write_bytes((good[:i] + new + good[i + 1:]).encode("ascii"))
        p = preflight(folder)
        # A change that stops the file loading (bad grammar, or a broken name at the top
        # of the file) is refused by Python itself; every other change must be refused
        # by the script with exit 3.
        assert p.returncode != 0 and b"PREFLIGHT ok" not in p.stdout, (i, good[i], new)
        # Bad grammar is reported as a SyntaxError or as one of its two kinds, which name
        # themselves and print no "Traceback" line.
        if not re.search(rb"Traceback|SyntaxError|IndentationError|TabError", p.stderr):
            # The interpreter may print a SyntaxWarning about the changed text while it
            # compiles the file; the script's own answer is the last line. The changed
            # character may sit in the words of that answer, so one of them may differ.
            last = p.stderr.decode("ascii").splitlines()[-1]
            start = "ERROR E_SELF "
            differ = sum(a != b for a, b in zip(last, start))
            assert p.returncode == 3 and len(last) > len(start) and differ <= 1, (i, p.stderr)
            exit_3 += 1
    # About half of all single-character changes still load; the rest break the grammar.
    # The share depends on which characters the seed picks, so the floor is a loose one.
    assert exit_3 >= 20


def test_known_answer_matches_the_stamped_value():
    assert re.fullmatch("[0-9a-f]{12}", brief.KAT)
    assert brief.kat() == brief.KAT


def test_known_answer_series_takes_the_252_bar_path(monkeypatch):
    # Its rows are 1.4 days apart, so the newest 60 span more than the 68 days that mark a
    # market open every day: the known answer holds the five-day year most instruments take.
    seen = {}
    real = brief.build

    def build(doc, meta, rows, sym, cur):
        seen["rows"] = rows = list(rows)
        out = real(doc, meta, rows, sym, cur)
        seen["facts"] = out[0]["facts"]
        return out

    monkeypatch.setattr(brief, "build", build)
    assert brief.kat() == brief.KAT
    stamps, closes = [r[0] for r in seen["rows"]], [r[4] for r in seen["rows"]]
    assert len(stamps) == 260 and {b - a for a, b in zip(stamps, stamps[1:])} == {120960}
    assert stamps[-1] // 86400 - stamps[-60] // 86400 >= 68
    vol = seen["facts"]["vol21"]["v"]
    assert math.isclose(vol, ref_vol21(closes, 252), rel_tol=1e-9)
    assert not math.isclose(vol, ref_vol21(closes, 365), rel_tol=1e-3)
    assert set(LONG_FACTS) <= set(seen["facts"])


def test_known_answer_failure_exits_3(tmp_path):
    folder = stamped(tmp_path, stamp=False)
    path = folder / "brief.py"
    text = set_line(path.read_text(encoding="ascii"), "KAT", '"0123456789ab"')
    path.write_bytes(text.encode("ascii"))
    p = preflight(folder)
    assert p.returncode == 3 and p.stdout == b""
    assert p.stderr.decode("ascii").splitlines()[-1].startswith("ERROR E_KAT ")


def test_known_answer_series_is_generated_not_embedded():
    tree = ast.parse(SCRIPT.read_text(encoding="ascii"))
    longest = max((len(n.elts) for n in ast.walk(tree) if isinstance(n, (ast.List, ast.Tuple))),
                  default=0)
    assert longest < 30


@pytest.mark.parametrize("exe", ["python3.9", "python3.11", "python3.12", "python3.13"])
def test_other_interpreters_agree(tmp_path, exe):
    import shutil
    if not shutil.which(exe):
        pytest.skip(exe + " is not installed")
    folder = stamped(tmp_path)
    p = subprocess.run([exe, str(folder / "brief.py"), "--preflight", str(folder)],
                       capture_output=True, timeout=60)
    assert p.returncode == 0 and p.stderr == b"", p.stderr
    src, out = tmp_path / "bundle.json", tmp_path / "brief.json"
    src.write_bytes(bundles.dumps(bundles.bundle(seed=3)))
    p = subprocess.run([exe, str(folder / "brief.py"), str(src), str(out)],
                       capture_output=True, timeout=60)
    assert p.returncode == 0 and p.stderr == b""
    theirs = json.loads(out.read_text(encoding="ascii"))
    ours, _ = brief.build(*brief.parse(json.dumps(bundles.bundle(seed=3)), {}))
    assert {k: f["t"] for k, f in theirs["facts"].items()} == \
        {k: f["t"] for k, f in ours["facts"].items()}
    assert theirs["data_id"] == ours["data_id"]


# ---------------------------------------------------------------------------------------
# The shipped scripts in sequence
# ---------------------------------------------------------------------------------------

GET_DATA_RUNNER = """
import importlib.util, sys
sys.dont_write_bytecode = True
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
gd = load("get_data", sys.argv[1])
ys = load("yahoo_shapes", sys.argv[2])
mode = sys.argv[3]
kw = {"null_last_close": True} if mode == "fill" else {}
if mode.isupper():  # an instrument type: the chart answer says it is that
    kw = {"kind": mode}
answer = ys.chart_answer(**kw)
if mode == "live":
    # A coin fetched four hours into its UTC day, its last price above the day's high.
    answer = ys.chart_answer(kind="CRYPTOCURRENCY", gmtoffset=0, n=366,
                             last=ys.FETCHED // 86400 * 86400)
    quote = answer["chart"]["result"][0]["indicators"]["quote"][0]
    quote["close"][-1] = quote["high"][-1] * 1.0007
# The statement route holds figures in every mode: where none reach the bundle, none were
# asked for.
gd.http_get = ys.FakeYahoo(chart=ys.ok(answer), search=ys.ok(ys.search_answer(odd_title=True)))
if mode == "limited":
    gd.http_get = ys.FakeYahoo(search=ys.refused(429), timeseries=ys.refused(429))
class Clock:
    waits = []
    def sleep(self, seconds):
        self.waits.append(seconds)
gd.time = Clock()  # the pause after a rate limit is noted, not waited
code = gd.main(sys.argv[4:])
asked = sum("/timeseries/" in url for url in gd.http_get.calls)
if asked != (2 if mode == "limited" else 0 if mode.isupper() or mode == "live" else 1):
    code = 98
sys.exit(code if Clock.waits == ([7, 7] if mode == "limited" else []) else 99)
"""
NOT_COMPANIES = ["ETF", "INDEX", "MUTUALFUND", "CRYPTOCURRENCY"]
DATA_LINES = {"plain": b"DATA ok NVDA bars=251 headlines=4 statements=ok\n",
              "fill": b"DATA ok NVDA bars=251 headlines=4 statements=ok\n",
              "limited": b"DATA ok NVDA bars=251 headlines=failed statements=failed\n",
              "live": b"DATA ok NVDA bars=366 headlines=4 statements=absent\n"}
DATA_LINES.update({typ: b"DATA ok NVDA bars=251 headlines=4 statements=absent\n"
                   for typ in NOT_COMPANIES})
DRAFT = """[verdict] NEUTRAL
[why] The price is {{sma50_gap|dir}} its medium average and {{rsi14|label}} is mid-range.
[bull.claim] Momentum is not stretched.
[bull.evidence] rsi14, stoch_k, bb_pctb, mfi14
[bull.also] The newest volume was {{volume.0}}.
[bull.weak] Trend strength is unclear.
[bear.claim] The averages are overhead.
[bear.evidence] sma10_gap, sma50_gap, sma200_gap, adx14
[bear.also] The largest move in the table was {{move_max}} on {{move_max_date}}.
[bear.weak] The sample is short.
[levels]
close.1 | bull | on a close above with falling volume
low52 | bear | on a close below
[trader] HOLD 0.0
[manager] approved HOLD 0.0
[risk] The view rests on few facts. It could be wrong.
[roles] single
[skipped] none
"""


def bundle_then_brief(tmp_path, mode):
    """Run the real get_data.py (its network replaced by generated answers), then brief.py
    on the bundle it wrote. Returns the brief's path, the brief and the fact sheet lines."""
    folder, shapes = SCRIPT.parent, HERE / "yahoo_shapes.py"
    if not all(p.exists() for p in (folder / "get_data.py", shapes)):
        pytest.skip("get_data.py or its fixtures are not here")
    bundle, out = tmp_path / "bundle.json", tmp_path / "brief.json"
    p = subprocess.run([sys.executable, "-c", GET_DATA_RUNNER, str(folder / "get_data.py"),
                        str(shapes), mode, "NVDA", str(bundle)], capture_output=True, timeout=60)
    assert p.returncode == 0 and p.stdout.replace(b"\r\n", b"\n") == DATA_LINES[mode], (
        p.returncode, p.stdout, p.stderr)
    p = script(bundle, out)
    assert p.returncode == 0 and error_lines(p) == [], p.stderr
    return out, json.loads(out.read_text(encoding="ascii")), p.stdout.decode("ascii").splitlines()


@pytest.mark.parametrize("mode", ["plain", "fill"])
def test_get_data_then_brief(tmp_path, mode):
    """A bundle written by the real get_data.py is accepted by brief.py."""
    _, doc, sheet = bundle_then_brief(tmp_path, mode)
    assert sheet[-1] == "BRIEF ok NVDA %s facts=%d data_id=%s" % (
        doc["as_of"], len(doc["facts"]), doc["data_id"])
    assert list(doc) == BRIEF_KEYS
    assert doc["source"] == "yahoo-chart" and doc["session"] == "finished"
    assert doc["flags"] == (["close_filled"] if mode == "fill" else [])
    assert doc["headlines"] and "rev_ttm" in doc["facts"] and "sma200" in doc["facts"]
    # The unusual headline arrives as one clean line, its typographic quotes made plain.
    assert all("|" not in h["title"] and "\n" not in h["title"] for h in doc["headlines"])
    assert doc["headlines"][-1]["title"] == (
        'Generated "quoted" headline with a caf\u00e9, a - sign, a break')


def test_a_rate_limited_download_reaches_the_brief_as_failed_requests(tmp_path):
    """get_data.py met HTTP 429 twice on each follow-up request; brief.py says that the
    requests failed, not that the source had nothing."""
    _, doc, sheet = bundle_then_brief(tmp_path, "limited")
    assert doc["flags"] == ["statements_absent", "no_headlines"]
    assert [n["text"] for n in doc["notes"]] == [
        REQUEST_FAILED % ", ".join(STATEMENT_FACTS), HEADLINES_FAILED]
    assert doc["headlines"] == [] and "sma200" in doc["facts"]
    assert sheet[sheet.index("# Notes") + 1:sheet.index("# Flags")] == [
        "n1 | " + REQUEST_FAILED % ", ".join(STATEMENT_FACTS), "n2 | " + HEADLINES_FAILED]


@pytest.mark.parametrize("typ", NOT_COMPANIES)
def test_a_fund_an_index_or_a_coin_reaches_the_brief_with_no_statement_request(tmp_path, typ):
    """Only a company has statements. For any other type get_data.py sends no third request
    (the runner answers 98 if it did), and brief.py says that the source gave no figure,
    not that a request failed."""
    _, doc, sheet = bundle_then_brief(tmp_path, typ)
    bundle = json.loads((tmp_path / "bundle.json").read_text(encoding="ascii"))
    assert bundle["chart"]["meta"]["instrumentType"] == typ
    assert bundle["errors"] == {"news": None, "statements": None}
    assert list(bundle["statements"]) == list(bundles.STATEMENT_TYPES)
    assert not any(bundle["statements"].values())
    assert doc["type"] == typ and doc["session"] == "finished"
    assert "statements_absent" in doc["flags"] and len(doc["headlines"]) == 4
    assert not set(STATEMENT_FACTS) & set(doc["facts"])
    note = NO_FIGURE % ", ".join(STATEMENT_FACTS)
    assert [n["text"] for n in doc["notes"] if "statement" in n["text"]] == [note]
    assert "failed" not in " ".join(n["text"] for n in doc["notes"])
    assert [x.split(" | ", 1)[1] for x in sheet if note in x] == [note]
    # A year of a coin's rows is too few for the 52-week facts of a 365-bar year; a year of
    # a fund's or an index's is enough for those of a 252-bar year.
    assert ("high52" in doc["facts"]) == (typ != "CRYPTOCURRENCY")
    assert "short_history" not in doc["flags"]


def test_a_live_coin_whose_price_passed_its_days_high_goes_through_both_scripts(tmp_path):
    """As met live: get_data.py passes the row on as the source gave it, the close 0.07
    percent above the high, and brief.py widens the row of the day in progress instead of
    stopping with E_OHLC."""
    _, doc, sheet = bundle_then_brief(tmp_path, "live")
    chart = json.loads((tmp_path / "bundle.json").read_text(encoding="ascii"))["chart"]
    assert chart["close"][-1] > chart["high"][-1] * 1.0005
    assert doc["type"] == "CRYPTOCURRENCY" and doc["session"] == "unfinished"
    assert doc["flags"] == ["unfinished_session", "statements_absent"]
    facts = doc["facts"]
    assert facts["high.0"]["v"] == facts["close.0"]["v"] == facts["last"]["v"] == chart["close"][-1]
    assert facts["low.0"]["v"] == chart["low"][-1] and facts["last"]["label"] == "last price"
    assert sheet[0].endswith(" | session unfinished") and "high52" in facts


def note_forms(doc):
    """The notes of a brief with every date and number made the same."""
    return [re.sub(r"\d+(\.\d+)?", "9", re.sub(r"\d{4}-\d\d-\d\d", "<date>", n["text"]))
            for n in doc["notes"]]


@pytest.mark.parametrize("hand_built, source, args", [
    (briefs.limited, bundles.rate_limited, []),
    (briefs.bare, lambda: bundles.bundle(seed=13, news=0, with_statements=False), []),
    (briefs.price_file, lambda: bundles.csv_text(bundles.bundle(seed=16)),
     ["--symbol", "file", "--currency", "USD"])], ids=["limited", "bare", "price_file"])
def test_the_hand_built_briefs_word_their_notes_as_brief_py_does(run, hand_built, source, args):
    """briefs.py is written by hand for the render tests. Its wording of a failed request, of
    a source with nothing and of a price file is held here to what brief.py writes."""
    mine = hand_built()
    r = run(source(), *args, name="in.csv" if args else "in.json")
    assert r.rc == 0
    assert note_forms(mine) == note_forms(r.doc)
    assert [n["id"] for n in mine["notes"]] == [n["id"] for n in r.doc["notes"]]
    assert mine["flags"] == r.doc["flags"] and list(mine["facts"]) == list(r.doc["facts"])
    assert (mine["source"], mine["session"]) == (r.doc["source"], r.doc["session"])


@pytest.mark.parametrize("mode", ["plain", "fill"])
def test_the_brief_is_accepted_by_render(tmp_path, mode):
    """The brief brief.py writes is accepted by the real render.py, called the way contract
    section 5 says: BRIEF DRAFT OUT_MD OUT_REPORT."""
    if not (SCRIPT.parent / "render.py").exists():
        pytest.skip("render.py is not here")
    out, doc, _ = bundle_then_brief(tmp_path, mode)
    draft, md, report = tmp_path / "draft.txt", tmp_path / "report.md", tmp_path / "report.json"
    draft.write_text(DRAFT, encoding="ascii")
    render = [sys.executable, str(SCRIPT.parent / "render.py")]
    p = subprocess.run(render + [str(out), str(draft), str(md), str(report)],
                       capture_output=True, timeout=60)
    assert p.returncode == 0, (p.stdout[-600:], p.stderr)
    text = md.read_text(encoding="ascii")
    f = doc["facts"]
    for name in ("rsi14", "volume.0", "move_max", "move_max_date", "adx14", "low52"):
        assert f[name]["t"] in text, name
    assert f["sma50_gap"]["t"].lstrip("+-") in text and doc["data_id"] in text
    p = subprocess.run(render + ["--verify", str(out), str(md)], capture_output=True, timeout=60)
    assert p.returncode == 0 and p.stdout.strip() == b"VERIFY ok", (p.stdout, p.stderr)


# ---------------------------------------------------------------------------------------
# What the reviewers proved
# ---------------------------------------------------------------------------------------

MONEY_FACTS = {"mcap": "trailingMarketCap", "rev_ttm": "trailingTotalRevenue",
               "ni_ttm": "trailingNetIncome", "rev_q": "quarterlyTotalRevenue",
               "ni_q": "quarterlyNetIncome", "debt": "quarterlyTotalDebt",
               "equity": "quarterlyStockholdersEquity"}


def test_a_money_figure_says_its_currency(run):
    # Prices in pence, market value in pounds, revenue and debt in dollars: the report used
    # to print "revenue 321.3B and market value 209.5B" under a header that said pence.
    b = bundles.bundle(seed=6, currency="GBp", symbol="TEST.L", gmtoffset=0)
    for series in b["statements"].values():
        for entry in series:
            entry.pop("currency", None)
    plain = run(b).facts
    assert not [name for name in MONEY_FACTS if " " in plain[name]["t"]]
    for key in MONEY_FACTS.values():
        for entry in b["statements"][key]:
            entry["currency"] = "GBP" if key == "trailingMarketCap" else "USD"
    r = run(b)
    assert r.rc == 0 and r.doc["unit"] == "pence"
    for name, key in MONEY_FACTS.items():
        code = "GBP" if name == "mcap" else "USD"
        f = r.facts[name]
        assert f["kind"] == "money" and f["v"] == plain[name]["v"]
        assert f["t"] == plain[name]["t"] + " " + code, name
        assert re.fullmatch(r"-?\d+(\.\d+)?[KMBT]? [A-Z]{3}", f["t"]), f["t"]
        line = [x for x in r.out.splitlines() if x.startswith(name + " | ")][0]
        assert line.split(" | ")[2] == f["t"]
    # A ratio, a percentage and a count carry no currency.
    for name in set(STATEMENT_FACTS) - set(MONEY_FACTS):
        assert r.facts[name]["t"] == plain[name]["t"], name
    assert r.facts["volume.0"]["t"] == plain["volume.0"]["t"]
    # The derived figures are unchanged.
    assert r.facts["margin_ttm"]["v"] == plain["margin_ttm"]["v"]
    assert r.facts["rev_q_yoy"]["v"] == plain["rev_q_yoy"]["v"]


def test_the_currency_shown_is_the_one_of_the_figure_shown(run):
    b = bundles.bundle(seed=6)
    cap = b["statements"]["trailingMarketCap"]
    cap[:] = [{"asOf": "2026-08-01", "value": 1.0e11, "currency": "EUR"},
              {"asOf": "2026-11-09", "value": 2.0e11, "currency": "USD"},
              {"asOf": "2026-05-01", "value": 3.0e11, "currency": "JPY"}]
    assert run(b).facts["mcap"]["t"] == "200.0B USD"
    # A bundle from before the currency was kept, or a figure the source names none for.
    del cap[1]["currency"]
    assert run(b).facts["mcap"]["t"] == "200.0B"
    cap[1]["currency"] = None
    assert run(b).facts["mcap"]["t"] == "200.0B"
    # Whatever stands there is cut to three characters and cannot add a column to the sheet.
    cap[1]["currency"] = "USD|dollars | more"
    r = run(b)
    assert r.facts["mcap"]["t"] == "200.0B USD"
    assert [x for x in r.out.splitlines() if x.startswith("mcap | ")][0].count(" | ") == 3


def test_a_saved_history_stamped_late_in_the_utc_day_stops(run):
    # Sydney in its summer opens at 23:00 UTC of the day before, New Zealand at 21:00 or
    # 22:00. A saved price history carries no exchange offset, so every date came out one day
    # early, with sessions on a Sunday, and the finished close was cut as "source is behind".
    sydney = bundles.bundle(seed=32, gmtoffset=39600, currency="AUD", symbol="TEST.AX")
    assert sydney["chart"]["timestamp"][-1] % 86400 >= 20 * 3600
    assert run(sydney).doc["as_of"] == "2026-11-10"
    for b in (sydney, bundles.same_day_case(), bundles.same_day_case(trade_first=True),
              bundles.bundle(seed=33, gmtoffset=46800, symbol="TEST.NZ", currency="NZD")):
        r = run(bundles.history_result(b))
        assert_stop(r, "E_DATES")
        assert r.err[0] == ("STOP E_DATES this price history does not say which day each row "
                            "belongs to")
    # The same rows as a price file with dates are fine: the file says the day.
    r = run(bundles.csv_text(sydney), "--symbol", "TEST.AX", "--currency", "AUD", name="in.csv")
    assert r.rc == 0 and r.doc["as_of"] == "2026-11-10"
    assert [n for n in r.doc["flags"] if n == "prices_behind"] == []


def test_a_saved_history_stamped_earlier_in_the_utc_day_runs(run):
    # New York opens at 14:30 UTC, Tokyo and Hong Kong before 02:00, London at 08:00.
    for offset in (-18000, 32400, 28800, 0, 3600):
        b = bundles.bundle(seed=34, gmtoffset=offset)
        r = run(bundles.history_result(b))
        assert r.rc == 0, (offset, r.err)
        assert r.doc["as_of"] == run(b).doc["as_of"] == "2026-11-10"
    # One row stamped at the last trade, late in the UTC day, is not the exchange's habit.
    b = bundles.bundle(seed=34, session="after_close")
    b["chart"]["timestamp"][-1] = b["chart"]["meta"]["regularMarketTime"]
    assert b["chart"]["timestamp"][-1] % 86400 >= 20 * 3600
    r = run(bundles.history_result(b))
    assert r.rc == 0 and r.doc["as_of"] == "2026-11-10"
    # Two such rows are a habit: the rule is "two or more", and "late" starts at 20:00 UTC.
    stamps = b["chart"]["timestamp"]
    stamps[-2] = stamps[-2] // 86400 * 86400 + 20 * 3600
    assert_stop(run(bundles.history_result(b)), "E_DATES")
    stamps[-2] -= 1  # 19:59:59
    assert run(bundles.history_result(b)).rc == 0


def test_a_change_that_rounds_to_zero_is_never_shown_as_negative(run):
    b = bundles.bundle(seed=9)
    ch = b["chart"]
    ch["close"][-1] = round(ch["close"][-2] * (1 - 0.00002), 6)
    ch["low"][-1] = min(ch["low"][-1], ch["close"][-1])
    ch["high"][-1] = max(ch["high"][-1], ch["close"][-1])
    ch["meta"]["regularMarketPrice"] = ch["close"][-1]
    r = run(b)
    f = r.facts["chg1"]
    assert -0.005 < f["v"] < 0
    assert (f["t"], f["sign"]) == ("+0.00%", "+")
    assert r.facts["chg.0"]["t"] == r.facts["ret1"]["t"] == "+0.00%"
    assert not [k for k, x in r.facts.items() if str(x["t"]).startswith("-0.00%")]
    assert "-0.00%" not in r.out


def test_two_hundred_rows_is_where_the_long_facts_begin(run):
    # The contract: fewer than 200 rows is short history. 199 is short, 200 is not.
    short, full = run(bundles.bundle(seed=19, n=199)), run(bundles.bundle(seed=19, n=200))
    assert "short_history" in short.doc["flags"] and "short_history" not in full.doc["flags"]
    assert not set(LONG_FACTS) & set(short.facts)
    assert set(LONG_FACTS) <= set(full.facts)
    assert short.doc["series"]["sma200"] == [None] * 120
    assert full.doc["series"]["sma200"][-1] is not None
    assert full.doc["series"]["sma200"][:-1] == [None] * 119
    closes = bundles.bundle(seed=19, n=200)["chart"]["close"]
    assert full.facts["high52"]["v"] == max(closes) and full.facts["low52"]["v"] == min(closes)
    assert math.isclose(full.facts["sma200"]["v"], sum(closes) / 200)


def test_no_escape_a_file_writing_tool_could_decode_is_in_the_source():
    # Some hosts turn a backslash-u sequence into the character while writing the script
    # out; the preflight then refuses the copy and the run stops.
    text = SCRIPT.read_text(encoding="ascii")
    assert not re.search(r"\\[uUN]", text)
    assert text.count("chr(8722)") == 1 and text.count("chr(160)") == 1


@pytest.mark.parametrize("n", [251, 252, 366])
def test_the_200_day_series_starts_where_a_one_year_download_allows(run, n):
    # A standing decision: the download is one year of rows, about 251 on a five-day week.
    # The 200-day average exists from the 200th row on, so the series the chart draws holds
    # it for the newest n - 199 of its 120 dates and nothing before them.
    r = run(bundles.bundle(seed=3, n=n) if n < 366 else bundles.crypto())
    assert r.rc == 0 and len(r.doc["series"]["date"]) == 120
    there = [x is not None for x in r.doc["series"]["sma200"]]
    drawn = min(120, n - 199)
    assert there == [False] * (120 - drawn) + [True] * drawn
    assert all(x is not None for x in r.doc["series"]["sma50"])
    closes = r.doc["series"]["close"]
    assert math.isclose(r.doc["series"]["sma50"][-1], sum(closes[-50:]) / 50)
    assert math.isclose(r.doc["series"]["sma200"][-1], r.facts["sma200"]["v"])


@pytest.mark.skipif(not Path("/dev/full").exists(), reason="needs the full device of Linux")
@pytest.mark.parametrize("target", [">/dev/full", "| head -1 >/dev/null"])
def test_output_that_cannot_be_written_ends_with_one_line_and_exit_code_4(tmp_path, target):
    # The long fact sheet is what an agent is most likely to cut short with another command.
    # Python used to add "Exception ignored ... BrokenPipeError" and end with exit code 120.
    src = tmp_path / "bundle.json"
    src.write_bytes(bundles.dumps(bundles.bundle()))
    command = '"%s" -I -B "%s" "%s" "%s" %s; echo "exit ${PIPESTATUS[0]}"' % (
        sys.executable, SCRIPT, src, tmp_path / "brief.json", target)
    for _ in range(5):  # a pipe that closes early is a race, so more than once
        done = subprocess.run(["bash", "-c", command], capture_output=True, timeout=60)
        err = [x for x in done.stderr.decode("ascii").splitlines()
               if x != "WARN unstamped development copy"]
        code = int(done.stdout.decode("ascii").split("exit ")[-1])
        assert "Exception ignored" not in " ".join(err) and code in (0, 4), (code, err)
        if code == 4:
            assert err == ["ERROR E_USAGE cannot read the input file or write the output file"]
        else:
            assert err == [] and "head" in target
        # The brief itself was written before anything was printed.
        assert json.loads((tmp_path / "brief.json").read_text("ascii"))["schema"] == "brief.v1"

