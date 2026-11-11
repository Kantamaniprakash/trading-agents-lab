"""usage: python get_data.py SYMBOL OUT_PATH

Downloads one symbol's daily bars, headlines and statement figures from one host
and writes them as bundle.v1. Reads no files.
"""
import datetime
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "2.0.0"
SELF = "a37ad31f829f"
HOST = "https://query1.finance.yahoo.com"
UA = "trading-agents-desk/2.0 (+https://github.com/Kantamaniprakash/trading-agents-lab)"
# At least one letter or digit: dots alone are not a ticker.
SYM = re.compile(r"(?=.*[A-Za-z0-9])[A-Za-z0-9.^=-]{1,15}")
TYPES = ("trailingPeRatio trailingForwardPeRatio trailingMarketCap trailingPegRatio "
         "trailingTotalRevenue trailingNetIncome quarterlyTotalRevenue quarterlyNetIncome "
         "quarterlyTotalDebt quarterlyStockholdersEquity").split()
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
# Seconds to wait before the one new attempt after a rate limit (HTTP 429).
WAIT = 7
STOPS = {
    "E_SYMBOL": "a symbol is 1 to 15 letters, digits, dots, hyphens, carets or equals signs, "
                "with a letter or a digit among them",
    "E_NOT_FOUND": "the data source has no data for this symbol; check the spelling",
    "E_REFUSED": "the data source refused this network; this is not a problem with the ticker "
                 "and waiting rarely helps",
    "E_BUSY": "the data source is limiting requests from this network right now; this is not a "
              "problem with the ticker, and a new run in a few minutes may work",
    "E_NO_ROUTE": "could not reach the data source: no connection, or no answer in 10 seconds",
    "E_SERVER": "the data source answered with an error of its own; this is not a problem with "
                "the ticker, and it may work later",
    "E_CERT": "the secure connection to the data source was not trusted (certificate check)",
    "E_SHAPE": "the data source sent an answer this script does not understand; "
               "its format may have changed",
}
ERRORS = {
    "E_USAGE": "usage: python get_data.py SYMBOL OUT_PATH",
    "E_WRITE": "could not write the output file or print the result",
    "E_INTERNAL": "get_data.py failed unexpectedly; run the preflight to check this copy",
}
DOT = ("; a US share class is written with a hyphen there, not a dot (BRK-B, not BRK.B); for "
       "another exchange, check the letters after the dot")
ODD = "unexpected answer"


class Stop(Exception):
    """args: the code, then a short label for the bundle's errors block."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # one host only: a redirect becomes an HTTP error


OPENER = urllib.request.build_opener(NoRedirect)


def http_get(url):
    """One GET. Returns (status, headers, body bytes). Tests replace this function."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with OPENER.open(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read(4000000)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), b""


def ask(url):
    """One JSON answer as (headers, data), or Stop with the fixed code."""
    for attempt in (1, 2):
        try:
            status, headers, body = http_get(url)
        except Exception as e:
            why = getattr(e, "reason", e)
            if "CERT" in (type(why).__name__ + str(why)).upper():
                raise Stop("E_CERT", "certificate not trusted")
            raise Stop("E_NO_ROUTE", "no connection")
        if status != 429 or attempt == 2:
            break
        # A rate limit often clears within seconds: wait once, then ask once more.
        time.sleep(WAIT)
    if status != 200:
        code = ("E_NOT_FOUND" if status == 404 else "E_BUSY" if status == 429
                else "E_REFUSED" if status in (401, 403, 999)
                else "E_SERVER" if status >= 500 else "E_SHAPE")
        raise Stop(code, "HTTP %d" % status)
    try:
        data = json.loads(body)
        json.dumps(data, allow_nan=False)  # NaN and Infinity are not JSON
        return headers, data
    except Exception:
        raise Stop("E_SHAPE", ODD)


def http_date(headers):
    """Epoch seconds of the HTTP Date header, or None."""
    text = " ".join(str(headers[k]) for k in headers if k.lower() == "date")
    m = re.search(r"(\d\d?) (\w{3}) (\d{4}) (\d\d):(\d\d):(\d\d) GMT", text)
    try:
        d, y, hh, mm, ss = (int(m.group(i)) for i in (1, 3, 4, 5, 6))
        t = datetime.datetime(y, MONTHS.index(m.group(2)) + 1, d, hh, mm, ss)
        return int((t - datetime.datetime(1970, 1, 1)).total_seconds())
    except (AttributeError, ValueError):
        return None


def epoch(t):
    """True for an integer that can be a time in seconds: not milliseconds, not before 1970."""
    return type(t) is int and 0 < t < 1e11


def number(x):
    """True for an int or float, not a bool, that a float can hold."""
    return type(x) in (int, float) and abs(x) < 1e300


def real_day(text):
    """True for a calendar date written YYYY-MM-DD."""
    try:
        return bool(re.fullmatch("[0-9]{4}-[0-9]{2}-[0-9]{2}", text)
                    and datetime.date(*map(int, text.split("-"))))
    except (TypeError, ValueError):
        return False


def clean(text, n):
    """One line of at most n characters: no control characters, plain spaces, minus, quotes."""
    if not isinstance(text, str):
        return ""
    # chr(8722) is the Unicode minus sign.
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text).replace(chr(8722), "-")
    # Typographic and fullwidth double quotes become the plain one, which render.py handles.
    text = re.sub("[%s-%s%s]" % (chr(8220), chr(8223), chr(65282)), '"', text)
    # Zero-width characters and direction marks print nothing and can reorder what follows.
    marks = tuple(map(chr, (8203, 8207, 8234, 8238, 8294, 8297, 65279)))
    text = re.sub("[%s-%s%s-%s%s-%s%s]" % marks, "", text)
    return " ".join(text.split())[:n]


def parse_chart(d):
    r = d["chart"]["result"][0]
    q, ts = r["indicators"]["quote"][0], r.get("timestamp") or []
    out = {"meta": r["meta"], "timestamp": ts}
    if not isinstance(r["meta"], dict) or not all(epoch(t) for t in ts):
        raise ValueError
    for k in ("open", "high", "low", "close", "volume"):
        out[k] = q.get(k) or []
        if len(out[k]) != len(ts) or not all(x is None or number(x) for x in out[k]):
            raise ValueError
    return out


def parse_news(d, sym):
    out = []
    for it in d["news"]:
        tick = it.get("relatedTickers")
        tick = [t.upper() for t in (tick if isinstance(tick, list) else [])
                if isinstance(t, str) and SYM.fullmatch(t)]
        title, when = clean(it.get("title"), 160), it.get("providerPublishTime")
        if sym in tick and title:
            out.append({"title": title, "publisher": clean(it.get("publisher"), 60),
                        "time": when if epoch(when) else None, "tickers": tick})
    return out[:10]


def parse_statements(d):
    out = {t: [] for t in TYPES}
    for it in d["timeseries"]["result"]:
        for t in TYPES:
            for v in it.get(t) or []:
                day = (v or {}).get("asOfDate")
                raw = ((v or {}).get("reportedValue") or {}).get("raw")
                code = (v or {}).get("currencyCode")
                if number(raw) and real_day(day):
                    item = {"asOf": day, "value": raw}
                    # The currency of a statement figure is often not the currency of the price.
                    if isinstance(code, str) and re.fullmatch("[A-Za-z]{3}", code):
                        item["currency"] = code
                    out[t].append(item)
    for t in TYPES:
        out[t].sort(key=lambda x: x["asOf"])
    return out


def extra(url, parse, empty):
    """Requests 2 and 3: a failure is recorded and the run continues."""
    try:
        return parse(ask(url)[1]), None
    except Stop as e:
        return empty, e.args[1]
    except Exception:
        return empty, ODD


def build(sym):
    s = urllib.parse.quote(sym, safe="")
    headers, d = ask("%s/v8/finance/chart/%s?range=1y&interval=1d&events=div%%2Csplit" % (HOST, s))
    try:
        chart = parse_chart(d)
        # "Now" is the source's clock, never this machine's.
        now = http_date(headers)
        if not epoch(now):
            now = chart["meta"].get("regularMarketTime")
        if not epoch(now):
            raise ValueError
    except Exception:
        raise Stop("E_SHAPE")
    news, e2 = extra("%s/v1/finance/search?q=%s&newsCount=10&quotesCount=0" % (HOST, s),
                     lambda a: parse_news(a, sym), [])
    st, e3 = {t: [] for t in TYPES}, None
    # Only a company has statements: a fund, an index or a coin is not asked for any.
    if chart["meta"].get("instrumentType") in (None, "EQUITY"):
        st, e3 = extra("%s/ws/fundamentals-timeseries/v1/finance/timeseries/%s?symbol=%s"
                       "&type=%s&period1=%d&period2=%d" % (
                           HOST, s, s, "%2C".join(TYPES), now - 800 * 86400, now),
                       parse_statements, st)
    return {"schema": "bundle.v1", "symbol": sym, "source": "yahoo-chart", "fetched_at": now,
            "chart": chart, "news": news, "statements": st,
            "errors": {"news": e2, "statements": e3}}


def say(stream, line):
    """Write one line. False when the stream is closed or full: never a traceback."""
    try:
        stream.write(line.encode("ascii", "backslashreplace").decode("ascii") + "\n")
        stream.flush()
        return True
    except Exception:
        # Python would try a failed stream once more on its way out, and complain.
        if stream is sys.stdout:
            sys.stdout = None
        if stream is sys.stderr:
            sys.stderr = None
        return False


def main(argv):
    """Returns the exit code: 0 ok, 2 stop, 3 internal, 4 usage."""
    code, sym, done, status = "E_USAGE", "", None, ""
    try:
        if argv[:1] == ["--version"]:
            done = "trading-agents-desk get_data.py " + VERSION
        elif len(argv) == 2:
            sym = argv[0].replace(chr(8722), "-")
            if not SYM.fullmatch(sym):
                raise Stop("E_SYMBOL")
            sym = sym.upper()
            # Opened before any request: a failed run leaves an empty file, never an old bundle.
            with open(argv[1], "w", encoding="ascii", newline="\n") as out:
                b = build(sym)
                out.write(json.dumps(b) + "\n")
            # A request that failed is named as failed, not as data the source lacks.
            done = "DATA ok %s bars=%d headlines=%s statements=%s" % (
                sym, len(b["chart"]["timestamp"]),
                "failed" if b["errors"]["news"] else len(b["news"]),
                "failed" if b["errors"]["statements"] else
                "ok" if any(b["statements"].values()) else "absent")
    except Stop as e:
        code = e.args[0]
        if str(e.args[-1]).startswith("HTTP "):
            status = " (%s)" % e.args[-1]
    except OSError:
        code = "E_WRITE"
    except (Exception, KeyboardInterrupt):
        code = "E_INTERNAL"
    if done is not None:
        if say(sys.stdout, done):
            return 0
        code = "E_WRITE"
    if code in STOPS:
        hint = DOT if code == "E_NOT_FOUND" and "." in sym else ""
        say(sys.stderr, "STOP %s %s%s%s" % (code, STOPS[code], hint, status))
        return 2
    say(sys.stderr, "ERROR %s %s" % (code, ERRORS[code]))
    return 3 if code == "E_INTERNAL" else 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
