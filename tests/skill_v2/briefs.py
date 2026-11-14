"""Hand-built brief.v1 documents for the render tests (CONTRACTS.md section 3, rev 3).

They are written from the contract, not by running brief.py, so the render tests stand on
their own. Every number comes from a seeded random walk and every headline is invented.
Nothing here is market data. The indicator values are plausible stand-ins, not the real
formulas: render.py only prints facts, it never recomputes them.

The field names, fact names, kinds, labels, flags and note wordings are the ones brief.py
writes; test_render.py holds a test that runs brief.py on generated bundles and compares.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import random

END = datetime.date(2026, 11, 10)  # a Tuesday; no test reads the clock
# The eight flags brief.py can set, in the order it sets them.
FLAGS = ("close_filled", "rows_dropped", "prices_behind", "history_gap", "unfinished_session",
         "short_history", "statements_absent", "no_headlines")
STATEMENT_FACTS = [
    "pe_trailing", "pe_forward", "peg", "mcap", "rev_ttm", "ni_ttm", "margin_ttm", "rev_q",
    "rev_q_yoy", "ni_q", "ni_q_yoy", "debt", "equity", "debt_equity"]
MINOR = {"GBp": "pence", "GBX": "pence", "ZAc": "cents", "ILA": "agorot"}
HEADLINES = [
    ("Example Wire", "Test Instruments unveils a wider widget range"),
    ("Sample Daily", "Widget makers face 3 new rules in Q4, analysts say"),
    ("Mock Markets", "Test Instruments names a new finance chief"),
    ("Example Wire", "Why widget demand is hard to read this season"),
]


def f_price(x: float) -> str:
    """Two decimals, or four significant figures below 10."""
    a = abs(x)
    return "%.*f" % (2 if a >= 10 or a < 1e-9 else 3 - int(math.floor(math.log10(a))), x)


def f_big(x: float) -> str:
    a = abs(x)
    for s, u in (("", 1), ("K", 1e3), ("M", 1e6), ("B", 1e9), ("T", 1e12)):
        if a / u < 999.95 or s == "T":
            break
    a /= u
    return "-" * (x < 0) + ("%.0f" if not s else "%.1f" if a >= 99.995 else "%.2f") % a + s


def fact(v, label: str, kind: str = "level", sign: bool = False, as_of: str | None = None) -> dict:
    """One fact with its display text made the way the contract describes."""
    if kind == "pct":
        t = ("%+.2f%%" if sign else "%.2f%%") % v
    elif kind in ("count", "money"):
        t = f_big(v)
    elif kind in ("date", "text"):
        t = v
    elif kind == "price":
        t = f_price(v)
    else:
        t = "%.2f" % v
    out = {"v": v, "t": t, "label": label, "kind": kind}
    if sign:
        out["sign"] = t[0]
    if as_of:
        out["as_of"] = as_of
    return out


def weekdays(n: int, end: datetime.date = END) -> list[str]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= datetime.timedelta(days=1)
    return out[::-1]


def brief(seed: int = 1, symbol: str = "TEST", name: str = "Test Instruments Inc.",
          typ: str = "EQUITY", currency: str = "USD", session: str = "finished",
          statements: bool = True, headlines: int = 3, start: float = 150.0,
          rows: int = 260, drift: float = 0.0, source: str = "yahoo-chart",
          filled: bool = False, dropped: bool = False, behind: bool = False,
          gap: bool = False, failed: bool = False) -> dict:
    """A brief.v1 document. ``rows`` under 200 leaves out the 200-day and 52-week facts.

    ``filled``, ``dropped``, ``behind`` and ``gap`` add the flag and the note brief.py writes
    for a filled close, a dropped row, a source that is behind and a hole in the dates, and
    shape the dates to match: ``behind`` ends the view one session before END, ``dropped``
    takes one date out and ``gap`` takes four sessions in a row out (seven calendar days
    between two rows). ``failed`` words the notes for absent statements and absent headlines
    the way brief.py does when the request for them failed; the flags are the same."""
    rng = random.Random(seed)
    D = weekdays(rows + dropped + 4 * gap, END - datetime.timedelta(days=1) if behind else END)
    if gap:
        del D[-24:-20]
    lost = D.pop(-40) if dropped else None
    assert len(D) == rows
    o, h, lo, c, v, prev = [], [], [], [], [], start
    for _ in range(rows):
        a = round(prev * (1 + rng.gauss(0, 0.004)), 2)
        b = round(a * (1 + rng.gauss(drift, 0.02)), 2)
        o.append(a)
        c.append(b)
        h.append(round(max(a, b) * (1 + abs(rng.gauss(0, 0.006))), 2))
        lo.append(round(min(a, b) * (1 - abs(rng.gauss(0, 0.006))), 2))
        v.append(int(40_000_000 * rng.uniform(0.4, 2.5)))
        prev = b
    n = rows
    px = "close" if session == "finished" else "last price"
    unit = MINOR.get(currency, currency)
    facts, notes, flags = {}, [], []

    def note(text: str, flag: str | None = None) -> None:
        notes.append({"id": "n%d" % (len(notes) + 1), "text": text})
        if flag:
            flags.append(flag)

    def pc(a: float, b: float) -> float:
        return 100 * (a / b - 1)

    def sma(k: int, i: int = n) -> float | None:
        return sum(c[i - k:i]) / k if i >= k else None

    facts["last"] = fact(c[-1], "last " + px[-5:], "price")
    facts["as_of"] = fact(D[-1], "data date", "date")
    facts["prev_close"] = fact(c[-2], "previous close", "price")
    facts["chg1"] = fact(pc(c[-1], c[-2]), "one-day change", "pct", True)
    for N in range(10):
        i, d = n - 1 - N, D[n - 1 - N]
        facts["date.%d" % N] = fact(d, "date of table row %d" % N, "date")
        for k, s in (("open", o), ("high", h), ("low", lo), ("close", c)):
            label = "%s on %s" % (px if k == "close" and N == 0 else k, d)
            facts["%s.%d" % (k, N)] = fact(s[i], label, "price")
        facts["volume.%d" % N] = fact(v[i], "volume on " + d, "count")
        facts["chg.%d" % N] = fact(pc(c[i], c[i - 1]), "change on " + d, "pct", True)
        facts["gap.%d" % N] = fact(pc(o[i], c[i - 1]), "opening gap on " + d, "pct", True)

    mid = sma(20)
    sd = math.sqrt(sum((x - mid) ** 2 for x in c[-20:]) / 20)
    atr = sum(h[i] - lo[i] for i in range(n - 14, n)) / 14
    macd, signal = rng.uniform(-4, 4), rng.uniform(-4, 4)
    k_, d_ = rng.uniform(3, 97), rng.uniform(3, 97)
    for key, value, label, kind in (
            ("rsi14", rng.uniform(15, 85), "RSI(14)", "level"),
            ("macd", macd, "MACD(12,26)", "level"),
            ("macd_signal", signal, "MACD signal(9)", "level"),
            ("macd_hist", macd - signal, "MACD histogram", "level"),
            ("bb_pctb", (c[-1] - (mid - 2 * sd)) / (4 * sd), "Bollinger %B(20,2)", "level"),
            ("bb_upper", mid + 2 * sd, "upper Bollinger band", "price"),
            ("bb_lower", mid - 2 * sd, "lower Bollinger band", "price"),
            ("atr14", atr, "ATR(14)", "level"),
            ("atr14_pct", 100 * atr / c[-1], "ATR(14) as a share of the " + px, "pct"),
            ("adx14", rng.uniform(8, 55), "ADX(14)", "level"),
            ("cci20", rng.uniform(-220, 220), "CCI(20)", "level"),
            ("stoch_k", k_, "Stochastic %K(14)", "level"),
            ("stoch_d", d_, "Stochastic %D(3)", "level"),
            ("stoch_j", 3 * k_ - 2 * d_, "Stochastic J", "level"),
            ("mfi14", rng.uniform(10, 90), "MFI(14)", "level")):
        facts[key] = fact(value, label, kind)
        if key[:3] in ("mac", "atr") and kind == "level":
            # brief.py prints these two families like prices: four figures when below 10.
            facts[key]["t"] = f_price(value)

    missing = []
    for k in (10, 50, 200):
        a, t = sma(k), "%d-day average" % k
        if a is None:
            missing += ["sma%d" % k, "sma%d_gap" % k]
            continue
        facts["sma%d" % k] = fact(a, t, "price")
        facts["sma%d_gap" % k] = fact(pc(c[-1], a), "%s against the %s" % (px, t), "pct", True)
    for k in (1, 5, 21, 63):
        facts["ret%d" % k] = fact(pc(c[-1], c[-1 - k]), "%d-day return" % k, "pct", True)
    facts["vol21"] = fact(rng.uniform(12, 70), "21-day annualised volatility", "pct")
    for k, fn in (("high", max), ("low", min)):
        # brief.py: more than 0.79 of a year of rows, which is 200 on this five-day week.
        if n < 200:
            missing += [k + "52", k + "52_date", k + "52_gap"]
            continue
        x = fn(c[-252:])
        facts[k + "52"] = fact(x, "52-week %s close" % k, "price")
        facts[k + "52_date"] = fact(D[n - 1 - c[::-1].index(x)],
                                    "date of the 52-week %s close" % k, "date")
        facts[k + "52_gap"] = fact(pc(c[-1], x), "%s against the 52-week %s" % (px, k), "pct", True)
    avg = sum(v[-20:]) / 20
    facts["vol_avg20"] = fact(avg, "20-day average volume", "count")
    facts["vol_ratio"] = fact(v[-1] / avg, "newest volume against the 20-day average", "ratio")
    i = max(range(n - 10, n), key=lambda j: abs(c[j] / c[j - 1] - 1))
    facts["move_max"] = fact(pc(c[i], c[i - 1]), "largest one-day change in the table", "pct", True)
    facts["move_max_date"] = fact(D[i], "date of the largest one-day change in the table", "date")

    if statements:
        day, q = D[-2], D[-30]
        rev, ni = rng.uniform(40e9, 90e9), rng.uniform(5e9, 20e9)
        debt, equity = rng.uniform(5e9, 30e9), rng.uniform(40e9, 90e9)
        t = ", trailing 12 months"
        for key, value, label, kind, sign, as_of in (
                ("pe_trailing", rng.uniform(12, 40), "trailing P/E", "ratio", False, day),
                ("pe_forward", rng.uniform(10, 30), "forward P/E", "ratio", False, day),
                ("peg", rng.uniform(0.5, 3), "PEG ratio", "ratio", False, day),
                ("mcap", rng.uniform(1e11, 4e12), "market value", "money", False, day),
                ("rev_ttm", rev, "revenue" + t, "money", False, q),
                ("ni_ttm", ni, "net income" + t, "money", False, q),
                ("margin_ttm", 100 * ni / rev, "net margin" + t, "pct", False, q),
                ("rev_q", rev / 4, "revenue, newest quarter", "money", False, q),
                ("rev_q_yoy", rng.uniform(-20, 40),
                 "revenue, newest quarter against a year earlier", "pct", True, q),
                ("ni_q", ni / 4, "net income, newest quarter", "money", False, q),
                ("ni_q_yoy", rng.uniform(-30, 60),
                 "net income, newest quarter against a year earlier", "pct", True, q),
                ("debt", debt, "total debt", "money", False, q),
                ("equity", equity, "shareholders' equity", "money", False, q),
                ("debt_equity", 100 * debt / equity, "debt to equity", "pct", False, q)):
            facts[key] = fact(value, label, kind, sign, as_of)
            if kind == "money":
                # A money figure names its currency, which is the statement's and not
                # always the price's.
                facts[key]["t"] += " USD"

    # The notes and flags in the order brief.py writes them, with its wording.
    fetched_at = "2026-11-11 04:06 UTC"
    if filled:
        note("The source had no close for %s yet; it is filled from the exchange's last "
             "regular price." % D[-1], "close_filled")
    if dropped:
        note("The row for %s had a missing value and was left out. Changes beside that date "
             "span two sessions." % lost, "rows_dropped")
    if behind:
        # Seen for London minutes after the next open: the finished session's row is empty.
        fetched_at = "2026-11-11 08:12 UTC"
        note("The source has no complete prices for %s yet, so this view ends at %s. The "
             "source's last regular price is %s, dated %s."
             % (END.isoformat(), D[-1], f_price(c[-1] * 0.98), END.isoformat()), "prices_behind")
    if gap:
        note("No prices between %s and %s: a long holiday or missing days." % (D[-21], D[-20]),
             "history_gap")
    if unit != currency:
        note("Prices are in %s (%s), a hundredth of the main currency unit." % (unit, currency))
    if session == "unknown":
        note("The source gave no session times, so the newest row may not be final.")
    if source == "price-file":
        fetched_at = D[-1] + " 00:00 UTC"
        note("These prices came from a file; when they were downloaded is not known.")
    if session == "unfinished":
        note("The session of %s was unfinished at the fetch: its price, range and volume are "
             "not final." % D[-1], "unfinished_session")
    if n < 200:
        flags.append("short_history")
    if missing:
        note("Too little history (%d daily rows) or a division by zero, so these facts are "
             "absent: %s." % (n, ", ".join(missing)))
    if not statements:
        note("%s, so these facts are absent: %s."
             % ("The statement request failed" if failed else "The source gave no statement "
                "figure", ", ".join(STATEMENT_FACTS)), "statements_absent")
    heads = [{"id": "h%d" % (j + 1), "date": D[-1 - j % 3], "publisher": p, "title": t}
             for j, (p, t) in enumerate(HEADLINES[:headlines])]
    if not heads:
        note("The headline request failed." if failed else "No headlines came with this data.",
             "no_headlines")

    k = max(0, n - 120)
    data = "".join("%s,%r,%r,%r,%r,%r;" % (D[j], o[j], h[j], lo[j], c[j], v[j]) for j in range(n))
    return {
        "schema": "brief.v1", "version": "2.0.0", "symbol": symbol, "name": name, "type": typ,
        "currency": currency, "unit": unit, "source": source, "as_of": D[-1],
        "fetched_at": fetched_at, "session": session,
        "data_id": hashlib.sha256(data.encode()).hexdigest()[:8], "flags": flags,
        "notes": notes, "facts": facts,
        "series": {"date": D[k:], "close": c[k:],
                   "sma50": [sma(50, j + 1) for j in range(k, n)],
                   "sma200": [sma(200, j + 1) for j in range(k, n)]},
        "headlines": heads}


def ordinary() -> dict:
    """An ordinary equity: finished session, statements and headlines present."""
    return brief(seed=11)


def unfinished() -> dict:
    """The newest bar is from a session that was still open at the fetch."""
    return brief(seed=12, symbol="LIVE", name="Live Session Holdings", session="unfinished")


def bare() -> dict:
    """No statement facts and no headlines."""
    return brief(seed=13, symbol="BARE", name="Bare Data Corp", statements=False, headlines=0)


def minor() -> dict:
    """A London-style listing quoted in pence."""
    return brief(seed=14, symbol="TST.L", name="Test Instruments plc", currency="GBp",
                 start=1450.0, headlines=2)


def short() -> dict:
    """Under 200 rows: no 200-day average and no 52-week facts."""
    return brief(seed=15, symbol="NEWCO", name="Newly Listed Co", rows=150)


def behind() -> dict:
    """The source is behind: the view ends one session early and says so (prices_behind)."""
    return brief(seed=16, symbol="TST.L", name="Test Instruments plc", currency="GBp",
                 start=3600.0, behind=True)


def filled() -> dict:
    """The newest close was filled from the exchange's last regular price (close_filled)."""
    return brief(seed=17, filled=True)


def patchy() -> dict:
    """An older row was left out and the dates have a hole (rows_dropped, history_gap)."""
    return brief(seed=18, dropped=True, gap=True)


def price_file() -> dict:
    """Built from a price file: no session facts, no statements, no headlines, no name."""
    return brief(seed=19, symbol="FILE", name="FILE", typ="UNKNOWN", session="unknown",
                 statements=False, headlines=0, source="price-file")


def limited() -> dict:
    """The source was limiting requests: the bars arrived, the statement request and the
    headline request failed. The facts and flags are those of bare(); two notes differ.

    Not in FIXTURES: test_render.py pairs every name there with a bundle of its own.
    test_brief.py holds this one to what brief.py writes."""
    return brief(seed=20, symbol="BUSY", name="Busy Source Corp", statements=False, headlines=0,
                 failed=True)


FIXTURES = {"ordinary": ordinary, "unfinished": unfinished, "bare": bare, "minor": minor,
            "short": short, "behind": behind, "filled": filled, "patchy": patchy,
            "price_file": price_file}


def draft(b: dict, **slots: str) -> str:
    """A clean draft for brief ``b``. Keyword arguments replace slots, with ``_`` for the dot
    (``bull_claim="..."``); a value of None drops the slot."""
    px = "close" if b["session"] == "finished" else "last price"
    f = b["facts"]
    long = "sma200_gap" in f
    text = {
        "verdict": "NEUTRAL",
        "why": ("The trend is weak with {{adx14|label}}, and the %s sits {{sma50_gap|dir}} its "
                "medium average, so neither case carries the day." % px),
        "bull.claim": "Momentum readings leave room for a rebound.",
        "bull.evidence": "rsi14, stoch_k, bb_pctb, mfi14",
        "bull.also": ("Volume was {{vol_ratio}} times its recent average on the newest row."
                      if not b["headlines"] else "One story may help: {{h1}}."),
        "bull.weak": "No reversal shows in the table yet.",
        "bear.claim": "The price is {{sma10_gap|dir}} its short average.",
        "bear.evidence": "sma10_gap, sma50_gap, adx14" + (", sma200_gap" if long else ""),
        "bear.also": "The largest move in the table was {{move_max}} on {{move_max_date}}.",
        "bear.weak": "A short here sits near {{bb_lower|label}}.",
        "levels": "\nclose.1 | bull | on a close above with falling volume\n" + (
            "low52 | bear | on a close below" if long else "bb_lower | bear | on a close below"),
        "trader": "HOLD 0.0",
        "manager": "approved HOLD 0.0",
        "risk": ("The trader proposed no trade. Staying out avoids gap risk such as the "
                 "{{gap.1}} open on {{date.1}}. The manager approved it unchanged."),
        "roles": "single",
        "skipped": "none" if "pe_trailing" in f else "fundamentals analyst",
    }
    for k, v in slots.items():
        k = k.replace("_", ".")
        assert k in text, k
        if v is None:
            del text[k]
        else:
            text[k] = v
    return "".join("[%s] %s\n" % (k, v) for k, v in text.items())


def dumps(b: dict) -> bytes:
    return json.dumps(b).encode("ascii")
