"""Fake answers with the real shapes of the three Yahoo endpoints get_data.py reads.

Every number and every headline in this file is generated from a seed. Nothing here is
market data: the shapes (key names, nesting, value types, where nulls appear) follow
answers recorded on 2026-11-11, the values do not. Load it by path, the same way the
tests load the shipped scripts.
"""
import datetime
import json
import random

DAY = 86400
TYPES = [
    "trailingPeRatio", "trailingForwardPeRatio", "trailingMarketCap", "trailingPegRatio",
    "trailingTotalRevenue", "trailingNetIncome", "quarterlyTotalRevenue", "quarterlyNetIncome",
    "quarterlyTotalDebt", "quarterlyStockholdersEquity",
]


def epoch(y, mo, d, hh=0, mm=0, ss=0):
    return int((datetime.datetime(y, mo, d, hh, mm, ss) - datetime.datetime(1970, 1, 1))
               .total_seconds())


def http_date(ts):
    """An HTTP Date header value for an epoch, with fixed English names."""
    t = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=ts)
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return "%s, %02d %s %04d %02d:%02d:%02d GMT" % (
        days[t.weekday()], t.day, months[t.month - 1], t.year, t.hour, t.minute, t.second)


# The newest generated session opens on a Friday; the fetch happens early on Saturday.
LAST_OPEN = epoch(2026, 1, 16, 14, 30)
FETCHED = epoch(2026, 1, 17, 4, 6)
DATE = http_date(FETCHED)


def sessions(n, last=LAST_OPEN, every_day=False):
    """n bar timestamps, oldest first. Weekdays only unless every_day (crypto)."""
    out, t = [], last
    while len(out) < n:
        weekday = ((t // DAY) + 3) % 7  # 0 is Monday
        if every_day or weekday < 5:
            out.append(t)
        t -= DAY
    return out[::-1]


def chart_answer(symbol="NVDA", n=251, seed=1, kind="EQUITY", currency="USD", gmtoffset=-18000,
                 null_last_close=False, null_rows=(), events=True, split=False, last=LAST_OPEN,
                 **meta):
    """The /v8/finance/chart answer.

    null_last_close: the newest row has open, high, low and volume but no close, as seen
    for some exchanges before the close is published.
    null_rows: indexes of rows where all five values are null, as seen for crypto.
    events: add a dividends block, as equities have. split: also add a splits block
    (written from memory of the endpoint; none was in the recorded answers).
    Extra keyword arguments replace meta fields; a value of None removes the field.
    """
    rng = random.Random(seed)
    crypto = kind == "CRYPTOCURRENCY"
    ts = sessions(n, last=last, every_day=crypto)
    price = 40.0 + 200.0 * rng.random()
    cols = {k: [] for k in ("open", "high", "low", "close", "volume")}
    adj = []
    for _ in ts:
        o = price * (1 + rng.uniform(-0.01, 0.01))
        c = o * (1 + rng.uniform(-0.03, 0.03))
        cols["open"].append(o)
        cols["close"].append(c)
        cols["high"].append(max(o, c) * (1 + rng.uniform(0, 0.01)))
        cols["low"].append(min(o, c) * (1 - rng.uniform(0, 0.01)))
        cols["volume"].append(rng.randrange(1000000, 90000000))
        adj.append(c * 0.99)  # the dividend-adjusted close differs from the close
        price = c
    for i in null_rows:
        for k in cols:
            cols[k][i] = None
        adj[i] = None
    if null_last_close:
        cols["close"][-1] = None
        adj[-1] = None
    closes = [c for c in cols["close"] if c is not None]
    period = {"timezone": "EST", "gmtoffset": gmtoffset}
    m = {
        "currency": currency, "symbol": symbol, "exchangeName": "NMS",
        "fullExchangeName": "NasdaqGS", "instrumentType": kind,
        "firstTradeDate": last - 9000 * DAY,  # made up, like every other value here
        "regularMarketTime": (ts[-1] + 6 * 3600 + 30 * 60 + 1) if ts else FETCHED,
        "hasPrePostMarketData": not crypto, "gmtoffset": gmtoffset, "timezone": "EST",
        "exchangeTimezoneName": "America/New_York",
        "regularMarketPrice": round(closes[-1], 2) if closes else 0.0,
        "fiftyTwoWeekHigh": round(max(closes), 2) if closes else 0.0,
        "fiftyTwoWeekLow": round(min(closes), 2) if closes else 0.0,
        "regularMarketDayHigh": 0.0, "regularMarketDayLow": 0.0, "regularMarketVolume": 0,
        "longName": "Generated Holdings Corporation", "shortName": "Generated Holdings",
        "chartPreviousClose": round(closes[0], 2) if closes else 0.0, "priceHint": 2,
        "fulldayPrice": 0.0, "fulldayChange": 0.0, "fulldayChangePercent": 0.0,
        "regularMarketChangePercent": 0.0,
        "currentTradingPeriod": {
            # The next session, as the source reports it after a session has ended.
            "pre": dict(period, start=last + 3 * DAY - 19800, end=last + 3 * DAY),
            "regular": dict(period, start=last + 3 * DAY, end=last + 3 * DAY + 23400),
            "post": dict(period, start=last + 3 * DAY + 23400, end=last + 3 * DAY + 37800),
        },
        "dataGranularity": "1d", "range": "1y",
        "validRanges": ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"],
    }
    for k, v in meta.items():
        if v is None:
            m.pop(k, None)
        else:
            m[k] = v
    result = {"meta": m, "timestamp": ts,
              "indicators": {"quote": [cols], "adjclose": [{"adjclose": adj}]}}
    if events and not crypto and len(ts) > 60:
        d, s = ts[-40], ts[-60]
        result["events"] = {"dividends": {str(d): {"amount": round(rng.uniform(0.2, 0.9), 2),
                                                   "date": d}}}
        if split:
            result["events"]["splits"] = {str(s): {
                "date": s, "numerator": 10.0, "denominator": 1.0, "splitRatio": "10:1"}}
    # A known symbol with no bars in the range: no timestamp and an empty quote. Written
    # from memory of the endpoint; it was not in the recorded answers.
    if not ts:
        result = {"meta": m, "indicators": {"quote": [{}], "adjclose": [{}]}}
    return {"chart": {"result": [result], "error": None}}


def news_item(i, tickers, title=None, when=FETCHED):
    return {
        "uuid": "00000000-0000-4000-8000-%012d" % i,
        "title": title if title is not None else "Generated headline %d about a made-up event" % i,
        "publisher": "Example Wire %d" % (i % 3),
        "link": "https://example.invalid/story-%d.html" % i,
        "providerPublishTime": when - 3600 * i,
        "type": "STORY",
        "thumbnail": {"resolutions": [{"url": "https://example.invalid/t%d.jpg" % i,
                                       "width": 140, "height": 140, "tag": "140x140"}]},
        "relatedTickers": list(tickers),
    }


LONG_TITLE = "Generated long headline " + "word " * 40 + "end"
ODD_TITLE = "Generated \u201cquoted\u201d  headline\u00a0with a caf\u00e9, a \u2212 sign,\n a break"


def search_answer(symbol="NVDA", own=3, other=2, long_title=False, odd_title=False):
    """The /v1/finance/search answer: `own` items tagged with the symbol, `other` without."""
    news = []
    for i in range(own):
        news.append(news_item(i, [symbol, "ZZZA"]))
    for i in range(own, own + other):
        news.append(news_item(i, ["ZZZB", "ZZZC"]))
    if long_title:
        news.append(news_item(len(news), [symbol], title=LONG_TITLE))
    if odd_title:
        news.append(news_item(len(news), [symbol], title=ODD_TITLE))
    return {
        "explains": [], "count": len(news), "quotes": [], "news": news, "nav": [], "lists": [],
        # the timing fields are placeholders, not recorded timings
        "researchReports": [], "screenerFieldResults": [], "totalTime": 1,
        "timeTakenForQuotes": 0, "timeTakenForNews": 1, "timeTakenForAlgowatchlist": 1,
        "timeTakenForQuestions": 0,
        "timeTakenForPredefinedScreener": 1, "timeTakenForCrunchbase": 0, "timeTakenForNav": 1,
        "timeTakenForResearchReports": 0, "timeTakenForScreenerField": 0,
        "timeTakenForCulturalAssets": 0, "timeTakenForSearchLists": 0,
    }


def timeseries_answer(symbol="NVDA", seed=1, empty=("trailingPegRatio",), now=FETCHED,
                      currency="USD", null_entry=False):
    """The fundamentals-timeseries answer.

    Types named in `empty` come back with meta only; for crypto that is all ten, which is
    why the script now sends this request for a share alone (a chart meta whose
    instrumentType is EQUITY or absent).
    null_entry puts a null inside the quarterly lists. None was in the recorded answers;
    the script skips one all the same.
    """
    rng = random.Random(seed)
    result = []
    for t in TYPES:
        item = {"meta": {"symbol": [symbol], "type": [t]}}
        if t not in empty:
            quarterly = t.startswith("quarterly")
            ratio = "Ratio" in t
            count = 5 if quarterly else 4 if ratio or "Cap" in t else 1
            stamps = [now - (count - 1 - i) * 91 * DAY - 2 * DAY for i in range(count)]
            rows = []
            for ts in stamps:
                raw = round(rng.uniform(5, 60), 6) if ratio else float(rng.randrange(10**9, 10**11))
                day = (datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=ts)).date()
                row = {"dataId": rng.randrange(10000, 99999), "asOfDate": day.isoformat(),
                       "periodType": "3M" if quarterly else "TTM",
                       "reportedValue": {"raw": raw, "fmt": "%.2f" % raw}}
                if not ratio:  # ratios carry no currency
                    row["currencyCode"] = currency
                rows.append(row)
            if quarterly and null_entry:
                rows.insert(1, None)
                stamps.insert(1, stamps[0] + DAY)
            item["timestamp"] = stamps
            item[t] = rows
        result.append(item)
    rng.shuffle(result)  # the source does not keep the order of the request
    return {"timeseries": {"result": result, "error": None}}


# Header names arrive in mixed case; the recorded answers had a lower-case "date".
JSON_HEADERS = {"content-type": "application/json;charset=utf-8", "cache-control": "public",
                "vary": "Origin,Accept-Encoding", "server": "ATS", "Age": "0",
                "Connection": "close", "X-Content-Type-Options": "nosniff"}


def ok(obj, date=DATE, date_key="date"):
    """A 200 answer as get_data.http_get returns it: (status, headers, body bytes)."""
    headers = dict(JSON_HEADERS)
    if date is not None:
        headers[date_key] = date
    return 200, headers, json.dumps(obj, ensure_ascii=False).encode("utf-8")


def not_found(date=DATE):
    """What the chart endpoint sends for an unknown symbol."""
    body = {"chart": {"result": None, "error": {
        "code": "Not Found", "description": "No data found, symbol may be delisted"}}}
    return 404, dict(JSON_HEADERS, date=date), json.dumps(body).encode("utf-8")


def refused(status=429, date=DATE):
    """A refusal: a short text body, no JSON. Only the status matters to the script. The
    recorded runs met status 429 (a rate limit) on the two follow-up requests; a bundle
    keeps the status and nothing else of such an answer, so the body is a stand-in."""
    headers = {"content-type": "text/html", "date": date, "server": "ATS"}
    return status, headers, b"Too Many Requests\r\n"


def in_turn(*answers):
    """A route for FakeYahoo that gives one answer per request, in the order written: a
    (status, headers, body) triple, or an exception to raise. ``left`` holds the answers
    not asked for yet; a request after the last one raises AssertionError."""
    left = list(answers)

    def route(url):
        if not left:
            raise AssertionError("more requests than answers: " + url)
        answer = left.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    route.left = left
    return route


class FakeYahoo:
    """Stands in for get_data.http_get and records every URL asked.

    Each of the three routes holds a (status, headers, body) triple, an exception to
    raise, or a function taking the URL. A route left unset answers with a healthy fake:
    the statement route holds figures whatever the chart's type, so a bundle without them
    means the request was not sent (see ``calls``) or failed.
    """

    MARKS = (("chart", "/v8/finance/chart/"), ("search", "/v1/finance/search"),
             ("timeseries", "/ws/fundamentals-timeseries/v1/finance/timeseries/"))

    def __init__(self, chart=None, search=None, timeseries=None, symbol="NVDA"):
        self.calls = []
        self.routes = {
            "chart": chart if chart is not None else ok(chart_answer(symbol)),
            "search": search if search is not None else ok(search_answer(symbol)),
            "timeseries": (timeseries if timeseries is not None
                           else ok(timeseries_answer(symbol))),
        }

    def __call__(self, url):
        self.calls.append(url)
        for key, mark in self.MARKS:
            if mark in url:
                answer = self.routes[key]
                break
        else:
            raise AssertionError("unexpected URL: " + url)
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return answer(url)
        return answer
