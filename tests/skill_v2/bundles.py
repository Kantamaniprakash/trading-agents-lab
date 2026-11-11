"""Synthetic inputs for the brief tests: bundle.v1 documents, the marketplace price-history
shape and CSV text for the same series, and one builder per row of the validation table.

Every number here is generated from a seeded random walk. Nothing is market data.
"""
from __future__ import annotations

import copy
import datetime
import json
import random

DAY = 86400
EPOCH = datetime.date(1970, 1, 1)
# The newest bar of every default series. A fixed Tuesday, so no test reads the clock.
END = (datetime.date(2026, 11, 10) - EPOCH).days
COLS = ("open", "high", "low", "close", "volume")
STATEMENT_TYPES = (
    "trailingPeRatio", "trailingForwardPeRatio", "trailingMarketCap", "trailingPegRatio",
    "trailingTotalRevenue", "trailingNetIncome", "quarterlyTotalRevenue", "quarterlyNetIncome",
    "quarterlyTotalDebt", "quarterlyStockholdersEquity",
)


def iso(day: int) -> str:
    return (EPOCH + datetime.timedelta(days=day)).isoformat()


def walk(seed: int, n: int, start: float = 150.0, vol: float = 0.02, digits: int = 2,
         volume: int = 40_000_000) -> list[tuple[float, float, float, float, int]]:
    """``n`` rows of (open, high, low, close, volume) from a seeded random walk."""
    rng = random.Random(seed)
    rows, prev = [], start
    for _ in range(n):
        o = round(prev * (1 + rng.gauss(0, vol / 4)), digits)
        c = round(o * (1 + rng.gauss(0, vol)), digits)
        h = round(max(o, c) * (1 + abs(rng.gauss(0, vol / 3))), digits)
        low = round(min(o, c) * (1 - abs(rng.gauss(0, vol / 3))), digits)
        rows.append((o, h, low, c, int(volume * rng.uniform(0.4, 2.5))))
        prev = c
    return rows


def days_back(n: int, end: int = END, every_day: bool = False) -> list[int]:
    """The ``n`` newest day numbers up to ``end``: weekdays only unless ``every_day``."""
    out, d = [], end
    while len(out) < n:
        if every_day or (d + 3) % 7 < 5:  # day 0 was a Thursday
            out.append(d)
        d -= 1
    return out[::-1]


def statements(end: int = END, seed: int = 1, currency: str = "USD") -> dict:
    """Statement figures in the bundle's shape: trailing values and five quarters. Each
    money figure names its currency, as the source does; a ratio names none."""
    rng = random.Random(seed)
    asof = iso(end - 1)
    quarters = [end - 40 - 91 * k for k in range(4, -1, -1)]
    rev = [round(20e9 * (1.08 ** k) * rng.uniform(0.97, 1.03)) for k in range(5)]
    out = {t: [] for t in STATEMENT_TYPES}
    out["trailingPeRatio"] = [{"asOf": asof, "value": round(rng.uniform(12, 40), 4)}]
    out["trailingForwardPeRatio"] = [{"asOf": asof, "value": round(rng.uniform(10, 30), 4)}]
    out["trailingPegRatio"] = [{"asOf": asof, "value": round(rng.uniform(0.5, 3), 4)}]
    out["trailingMarketCap"] = [{"asOf": asof, "value": round(rng.uniform(1e11, 4e12))}]
    out["trailingTotalRevenue"] = [{"asOf": iso(quarters[-1]), "value": sum(rev[1:])}]
    out["trailingNetIncome"] = [{"asOf": iso(quarters[-1]), "value": round(sum(rev[1:]) * 0.31)}]
    for k, q in enumerate(quarters):
        out["quarterlyTotalRevenue"].append({"asOf": iso(q), "value": rev[k]})
        out["quarterlyNetIncome"].append({"asOf": iso(q), "value": round(rev[k] * 0.3)})
        out["quarterlyTotalDebt"].append({"asOf": iso(q), "value": round(9e9 + 1e8 * k)})
        out["quarterlyStockholdersEquity"].append({"asOf": iso(q), "value": round(60e9 + 2e9 * k)})
    for key, series in out.items():
        if "Ratio" not in key:
            for entry in series:
                entry["currency"] = currency
    return out


def bundle(seed: int = 1, n: int = 260, symbol: str = "TEST", typ: str = "EQUITY",
           currency: str = "USD", gmtoffset: int = -18000, start: float = 150.0,
           session: str = "finished", news: int = 3, with_statements: bool = True,
           rows: list | None = None, end: int = END, digits: int = 2,
           name: str = "Test Instruments Inc.", every_day: bool | None = None) -> dict:
    """A bundle.v1 document as get_data.py writes it, with a plausible chart meta.

    ``session``: ``finished`` (fetched the night after the newest bar, so the current trading
    period is the next one), ``unfinished`` (fetched two hours into the newest bar's session,
    the source's quote five seconds old) or ``after_close`` (fetched a quarter of an hour
    after that session's end, the quote stamped one second after the end: a quote from
    before the end would leave the session unfinished, see at_the_bell).
    ``every_day``: a bar on every calendar day, as crypto has (the default for that type and
    for no other); the session times stay the type's own.
    """
    crypto = typ == "CRYPTOCURRENCY"
    rows = walk(seed, n, start=start, digits=digits) if rows is None else rows
    days = days_back(len(rows), end, every_day=crypto if every_day is None else every_day)
    open_s, close_s = (0, DAY - 60) if crypto else (34200, 57600)
    stamps = [d * DAY + open_s - gmtoffset for d in days]
    last_open, last_close = stamps[-1], days[-1] * DAY + close_s - gmtoffset
    nxt = days[-1] + (1 if crypto or every_day or (days[-1] + 3) % 7 < 4 else 3)
    if session == "unfinished":
        period, fetched, market_time = (last_open, last_close), last_open + 7200, last_open + 7195
    elif session == "after_close":
        period, fetched, market_time = (last_open, last_close), last_close + 900, last_close + 1
    else:
        period = (nxt * DAY + open_s - gmtoffset, nxt * DAY + close_s - gmtoffset)
        fetched, market_time = last_close + 7 * 3600, last_close + 1
    meta = {
        "currency": currency, "symbol": symbol, "exchangeName": "TST", "instrumentType": typ,
        "gmtoffset": gmtoffset, "timezone": "TST", "exchangeTimezoneName": "Test/Zone",
        "regularMarketPrice": round(rows[-1][3], 2), "regularMarketTime": market_time,
        "longName": name, "shortName": name[:12], "priceHint": 2, "dataGranularity": "1d",
        "currentTradingPeriod": {
            "pre": {"timezone": "TST", "start": period[0] - 3600, "end": period[0],
                    "gmtoffset": gmtoffset},
            "regular": {"timezone": "TST", "start": period[0], "end": period[1],
                        "gmtoffset": gmtoffset},
            "post": {"timezone": "TST", "start": period[1], "end": period[1] + 3600,
                     "gmtoffset": gmtoffset}},
    }
    chart = {"meta": meta, "timestamp": stamps}
    for i, k in enumerate(COLS):
        chart[k] = [r[i] for r in rows]
    items = [{"title": "Generated headline %d about %s" % (i + 1, symbol),
              "publisher": "Example Wire", "time": fetched - 3600 * (i + 1), "tickers": [symbol]}
             for i in range(news)]
    return {
        "schema": "bundle.v1", "symbol": symbol, "source": "yahoo-chart", "fetched_at": fetched,
        "chart": chart, "news": items,
        "statements": statements(end) if with_statements else {t: [] for t in STATEMENT_TYPES},
        # No follow-up request failed. For a fund, an index or a coin get_data.py sends no
        # statement request and writes these empty lists, and so does a source that has no
        # figure for a share; rate_limited() is the bundle of a request that failed.
        "errors": {"news": None, "statements": None}}


def history_result(b: dict) -> dict:
    """The marketplace price-history tool's saved result for the same series."""
    ch = b["chart"]
    hist = [{"low": ch["low"][i], "high": ch["high"][i], "open": ch["open"][i],
             "close": ch["close"][i], "volume": ch["volume"][i], "timestamp": t}
            for i, t in enumerate(ch["timestamp"])]
    return {"tool_name": "Generated historical prices", "job_id": "00000000-0000-0000-0000-0000",
            "tool_id": "example:get_historical_data", "status": "succeeded", "charged_usd": 0.0,
            "files": [],
            "data": {"data": {"symbol": b["symbol"], "history": hist,
                              "currency": ch["meta"]["currency"]}, "status": "success"},
            "balance_usd": 0.0}


def csv_text(b: dict, header: str = "date,open,high,low,close,volume") -> str:
    """The same series as a price file. Dates are the exchange dates of the bars."""
    ch = b["chart"]
    off = ch["meta"].get("gmtoffset") or 0
    lines = [header]
    for i, t in enumerate(ch["timestamp"]):
        cells = ["" if ch[k][i] is None else repr(ch[k][i]) for k in COLS]
        lines.append(",".join([iso((t + off) // DAY)] + cells))
    return "\n".join(lines) + "\n"


def dumps(doc: dict) -> bytes:
    return json.dumps(doc).encode("ascii")


def _blank(b: dict, i: int, keys=COLS) -> None:
    for k in keys:
        b["chart"][k][i] = None


# One builder per row of the validation table in CONTRACTS.md section 3. The row shapes are
# the ones seen live from the data source (shapes A to F), rebuilt with generated numbers.

def null_rows(indexes=(-40, -7)) -> dict:
    """Rows with no values in the middle of the history: dropped, each with a note."""
    b = bundle(seed=11)
    for i in indexes:
        _blank(b, i)
    return b


def same_day_case(trade_first: bool = False, **kw) -> dict:
    """Shape D, Sydney after the close: the newest date has two rows. One is stamped at the
    session start and has no close; one is stamped at the last trade and has it.

    As seen, the last trade is stamped before the regular end and the fetch came three hours
    after it. A quote from before the end makes the session unfinished until half an hour
    after the end, so the fetch time is part of the shape.

    ``trade_first`` writes the last-trade row before the session-start row in the arrays.
    """
    kw.setdefault("gmtoffset", 39600)
    b = bundle(seed=32, session="after_close", currency="AUD", symbol="TEST.AX", **kw)
    ch = b["chart"]
    trade = ch["timestamp"][-1] + 22208
    both = [[ch["timestamp"][-1]] + [ch[k][-1] for k in COLS[:3]] + [None, ch["volume"][-1]],
            [trade] + [ch[k][-1] for k in COLS]]
    if trade_first:
        both.reverse()
    for i, k in enumerate(("timestamp",) + COLS):
        ch[k][-1:] = [both[0][i], both[1][i]]
    ch["meta"]["regularMarketTime"] = trade
    assert trade < ch["meta"]["currentTradingPeriod"]["regular"]["end"]
    b["fetched_at"] = ch["meta"]["currentTradingPeriod"]["regular"]["end"] + 10900
    return b


def fill_case(**kw) -> dict:
    """Shape B, London from the close until the next open: the newest row has open, high, low
    and volume but no close, the session is over, and the exchange's last regular price is
    stamped on that row's date and lies inside its range."""
    b = bundle(seed=12, **kw)
    ch = b["chart"]
    ch["meta"]["regularMarketPrice"] = round((ch["high"][-1] + ch["low"][-1]) / 2, 2)
    ch["close"][-1] = None
    return b


def fill_wrong_day() -> dict:
    """As fill_case, but the last regular price belongs to the day before: no fill."""
    b = fill_case()
    b["chart"]["meta"]["regularMarketTime"] -= DAY
    return b


def behind_case(holes=(-2, -1), quote: str = "today", **kw) -> dict:
    """Shape C, London or Frankfurt minutes after the next open: the finished session's row
    is empty, and so is the row of the session that has just begun.

    ``holes`` are the emptied rows; ``(-2,)`` is the later look, when the new session's row has
    values. ``quote`` dates the source's last regular price: ``today`` (before this open, as
    seen for London), ``yesterday`` (at the last close, as seen for Frankfurt) or ``none``.
    """
    kw.setdefault("gmtoffset", 0)
    b = bundle(seed=31, session="unfinished", **kw)
    ch = b["chart"]
    meta, opened = ch["meta"], ch["timestamp"][-1]
    b["fetched_at"] = opened + 748
    meta["regularMarketPrice"] = round(ch["close"][-3] * 0.98, 2)
    meta["regularMarketTime"] = opened - 2400 if quote == "today" else opened - 15 * 3600 - 2800
    if quote == "none":
        del meta["regularMarketPrice"], meta["regularMarketTime"]
    for i in holes:
        _blank(b, i)
    return b


def behind_crypto(absent: bool = False) -> dict:
    """Shape E: the row of the finished previous UTC day is empty (or ``absent``) while the
    row of the UTC day in progress has values."""
    b = bundle(seed=13, typ="CRYPTOCURRENCY", gmtoffset=0, start=70000.0, session="unfinished",
               symbol="COIN-USD", with_statements=False, n=366)
    if absent:
        for k in ("timestamp",) + COLS:
            del b["chart"][k][-2]
    else:
        _blank(b, -2)
    return b


def at_the_bell(quote: int | None, fetch: int, **kw) -> dict:
    """Shape F, Hong Kong during its closing auction, and any fetch around a close: the
    newest row is stamped at the regular session's start and the calendar still describes
    that session. The source's own quote is stamped ``quote`` seconds after the regular end
    (negative: before it; None: the meta holds no quote time) and the fetch comes ``fetch``
    seconds after it."""
    kw.setdefault("seed", 35)
    b = bundle(session="unfinished", **kw)
    meta = b["chart"]["meta"]
    end = meta["currentTradingPeriod"]["regular"]["end"]
    b["fetched_at"] = end + fetch
    if quote is None:
        del meta["regularMarketTime"]
    else:
        meta["regularMarketTime"] = end + quote
    return b


def outside_range(b: dict, side: str = "high", by: float = 0.0007, i: int = -1,
                  column: str = "close") -> dict:
    """Seen live for a coin during its day, after shapes A to F: the source's last price is
    newer than its day-high field, so the close of the row in progress lies above that row's
    own high (by 0.07 percent when seen). Puts ``column`` of row ``i`` the fraction ``by``
    above the row's high, or below its low for ``side`` "low"."""
    ch = b["chart"]
    edge = ch[side][i]
    ch[column][i] = round(edge * (1 + by if side == "high" else 1 - by), 6)
    if i == -1 and column == "close":
        ch["meta"]["regularMarketPrice"] = ch["close"][-1]
    return b


def bad_prices(kind: str = "negative") -> dict:
    b = bundle(seed=14)
    ch = b["chart"]
    if kind == "negative":
        ch["low"][-30] = -1.0
    elif kind == "zero":
        ch["close"][-30] = ch["low"][-30] = 0.0
    elif kind == "low_above_close":
        ch["low"][-30] = ch["close"][-30] * 1.01
    elif kind == "high_below_open":
        ch["high"][-30] = ch["open"][-30] * 0.99
    else:
        raise ValueError(kind)
    return b


def too_few(n: int = 59) -> dict:
    return bundle(seed=15, n=n)


def date_gap(position: int = -20, missing: int = 9, **kw) -> dict:
    """Remove ``missing`` consecutive bars ending just before ``position`` (from the end).
    Nine weekday bars leave 14 calendar days between two rows: more than the 12 allowed."""
    b = bundle(seed=16, **kw)
    ch = b["chart"]
    for k in ("timestamp",) + COLS:
        del ch[k][position - missing:position]
    return b


def widen(b: dict, days: int, position: int = -20) -> dict:
    """Put exactly ``days`` calendar days between the row at ``position`` and the row before
    it: every older row is moved by whole days, so no other distance changes."""
    ts = b["chart"]["timestamp"]
    at = len(ts) + position
    shift = days * DAY - (ts[at] - ts[at - 1])
    ts[:at] = [t - shift for t in ts[:at]]
    return b


def calendar_gap(days: int, position: int = -20, **kw) -> dict:
    """A history with exactly ``days`` calendar days between two neighbouring rows."""
    return widen(bundle(seed=16, **kw), days, position)


def stale(days: int = 13) -> dict:
    b = bundle(seed=17)
    b["fetched_at"] = b["chart"]["timestamp"][-1] + days * DAY + 60
    return b


def future_bar(days: int = 2) -> dict:
    b = bundle(seed=18)
    b["fetched_at"] = b["chart"]["timestamp"][-1] - days * DAY
    return b


def short_history(n: int = 150) -> dict:
    return bundle(seed=19, n=n)


def unfinished() -> dict:
    return bundle(seed=20, session="unfinished")


def crypto(session: str = "unfinished") -> dict:
    return bundle(seed=21, typ="CRYPTOCURRENCY", gmtoffset=0, start=70000.0, session=session,
                  symbol="COIN-USD", with_statements=False, n=366, name="Coin USD")


def every_day(n: int = 400, typ: str = "EQUITY", **kw) -> dict:
    """A market with a bar on every calendar day under a type that is not crypto, or (as a
    price file made from it) under no type at all. More than a year of rows by default."""
    kw.setdefault("gmtoffset", 0)
    return bundle(seed=28, n=n, typ=typ, every_day=True, **kw)


def rate_limited(news: bool = True, statements: bool = True, label: str = "HTTP 429",
                 **kw) -> dict:
    """Seen live on a network the source was limiting: the bars arrived and a follow-up
    request did not. The bundle then holds no headline or no statement figure, and its
    errors block says why."""
    b = bundle(seed=27, **kw)
    if news:
        b["news"], b["errors"]["news"] = [], label
    if statements:
        b["statements"] = {t: [] for t in STATEMENT_TYPES}
        b["errors"]["statements"] = label
    return b


def minor_unit(currency: str = "GBp") -> dict:
    return bundle(seed=22, currency=currency, gmtoffset=0, start=3600.0, symbol="TEST.L")


def low_priced(start: float = 0.37) -> dict:
    return bundle(seed=23, start=start, digits=4, symbol="PENNY")


def zero_volume() -> dict:
    rows = [r[:4] + (0,) for r in walk(24, 260, start=5200.0)]
    return bundle(seed=24, rows=rows, typ="INDEX", symbol="^TEST", with_statements=False)


def flat_prices() -> dict:
    """Every price the same: most oscillators divide by zero."""
    return bundle(seed=25, rows=[(10.0, 10.0, 10.0, 10.0, 1000)] * 260)


def clone(b: dict) -> dict:
    return copy.deepcopy(b)
