"""Trading Agents Desk: check the data, compute the facts, print the sheet. No network."""
import datetime
import hashlib
import json
import math
import re
import sys

VERSION = "2.0.0"
SELF = "0abec2f52602"
SIBLINGS = {"get_data.py": "f30054fa5d52", "render.py": "490116451cec", "page.py": "1c1a2d3218a1"}
KAT = "5260f3b7692a"
TYPES = ("EQUITY", "ETF", "CRYPTOCURRENCY", "INDEX", "MUTUALFUND")
MINOR = {"GBp": "pence", "GBX": "pence", "ZAc": "cents", "ILA": "agorot"}
COLS = ("open", "high", "low", "close", "volume")
USAGE = "usage: brief.py INPUT BRIEF_OUT [--symbol S --currency C] | --preflight DIR"
E_INPUT = ("the input is not a data bundle, a saved price history or a price file headed "
  "date,open,high,low,close,volume")


class Stop(Exception):
  pass


def stop(code, text, rc=2):
  raise Stop(rc, ("STOP " if rc == 2 else "ERROR ") + code + " " + text)


def say(line, err=False):
  # Flushed, so a closed stream fails here and not at exit.
  print(line.encode("ascii", "backslashreplace").decode(), file=sys.stderr if err else sys.stdout,
    flush=True)


def iso(day):
  return datetime.date.fromordinal(719163 + day).isoformat()


def num(x):
  if x in (None, "", "null"):
    return None
  x = float(x)
  return x if math.isfinite(x) else None


def when(t):
  if isinstance(t, str):
    t = (datetime.date.fromisoformat(t[:10]).toordinal() - 719163) * 86400
  return int(t)


def clean(s):
  return " ".join(re.sub(r"[\0-\x1f\x7f]", " ", str(s or "")).replace("|", "/").split())


def mean(x):
  # Exact for equal values, so a flat window has no spread.
  return x[0] if min(x) == max(x) else math.fsum(x) / len(x)


def div(a, b):
  return a / b if a is not None and b else None


def ewm(xs, a):
  # The recursion of pandas ewm(alpha=a, adjust=False).
  out, w, old = [], None, 1.0
  for x in xs:
    if w is None:
      w = x
    else:
      old *= 1 - a
      if x is not None:
        if w != x:
          w = (old * w + a * x) / (old + a)
        old = 1.0
    out.append(w)
  return out


def f_price(x):
  # Two decimals, or four significant figures below 10.
  a = abs(x)
  return "%.*f" % (2 if a >= 10 or a < 1e-9 else 3 - math.floor(math.log10(a)), x)


def f_big(x):
  a = abs(x)
  for s, u in (("", 1), ("K", 1e3), ("M", 1e6), ("B", 1e9), ("T", 1e12)):
    if a / u < 999.95 or s == "T":
      break
  a /= u
  return "-" * (x < 0) + ("%.0f" if not s else "%.1f" if a >= 99.995 else "%.2f") % a + s


def find(o):
  if isinstance(o, dict):
    if isinstance(o.get("history"), list):
      return o
    o = list(o.values())
  if isinstance(o, list):
    for x in o:
      r = find(x)
      if r:
        return r


def parse(text, opt):
  """Return (bundle or {}, meta, rows, symbol, currency) for any of the three shapes."""
  try:
    doc = json.loads(text, parse_float=num, parse_constant=lambda s: None)
  except ValueError:
    doc = None
  key = ("timestamp",) + COLS
  if isinstance(doc, dict) and doc.get("schema") == "bundle.v1":
    ch = doc["chart"]
    meta = ch.get("meta") or {}
    if len({len(ch[k]) for k in key}) != 1:
      raise ValueError
    return doc, meta, zip(*[ch[k] for k in key]), doc["symbol"], meta.get("currency")
  sym, cur = opt.get("symbol"), opt.get("currency")
  hist = find(doc)
  if hist:
    # The file's own symbol and currency win over the options.
    rows = [[r[k] for k in key] for r in hist["history"]]
    return {}, {}, rows, hist["symbol"], hist.get("currency")
  lines = [x.split(",") for x in text.splitlines() if x.strip()]
  head = [x.strip().lower() for x in lines[0]]
  ix = [head.index(k) for k in ("date",) + COLS]
  if sym is None:
    stop("E_USAGE", "a price file needs --symbol", 4)
  return {}, {}, [[p[i].strip() for i in ix] for p in lines[1:]], sym, cur


def build(doc, meta, rows, sym, cur):
  """Validate the rows in the contract's order, then return (brief, fact sheet lines)."""
  sym = str(sym).upper()
  if not re.fullmatch(r"[A-Z0-9.^=-]{1,15}", sym):
    stop("E_SYMBOL", "the symbol is not a plain ticker")
  raw = [[when(r[0])] + [num(x) for x in r[1:]] for r in rows]
  typ, off = meta.get("instrumentType"), int(meta.get("gmtoffset") or 0)
  if typ and typ not in TYPES:
    stop("E_TYPE", "the desk covers shares, funds, indexes and crypto, not the type "
      + clean(typ)[:20])
  crypto = typ == "CRYPTOCURRENCY"
  fetched = int(doc["fetched_at"]) if doc else 0
  if not doc and sum(r[0] % 86400 >= 72000 for r in raw) > 1:
    # Late in the UTC day and no exchange offset: the date may be the next.
    stop("E_DATES", "this price history does not say which day each row belongs to")
  flags, notes = [], []

  def note(text, flag=None):
    notes.append({"id": "n%d" % (len(notes) + 1), "text": text})
    if flag and flag not in flags:
      flags.append(flag)

  def day(t):
    return (t + off) // 86400

  def date(row):
    return iso(day(row[0]))

  def unfinished(t):
    # None when the session facts are missing.
    p = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    if crypto:
      return t // 86400 >= fetched // 86400
    if "start" in p and "end" in p:
      return t >= p["start"] and fetched < p["end"]

  # Same day: sorted so that a date's newest complete row comes last, and the last one stays.
  raw.sort(key=lambda r: (day(r[0]), None not in r, r[0]))
  daily = list({day(r[0]): r for r in raw}.values())
  price, stamp = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
  for r in daily[-1:]:
    # Fill: a missing newest close is that day's last regular price, if in the row's range.
    if (r[4] is None and r.count(None) == 1 and unfinished(r[0]) is False and not crypto
        and price and stamp and day(stamp) == day(r[0])
        and r[3] * 0.9999 <= price <= r[2] * 1.0001):
      r[4] = float(price)
      note("The source had no close for %s yet; it is filled from the exchange's last regular "
        "price." % date(r), "close_filled")
  # Rows: an older row with a missing value is left out. Behind: one among the newest 3 ends
  # the view before it, flagged unless its session is the one in progress.
  keep = []
  for i, r in enumerate(daily):
    if None not in r:
      keep.append(r)
    elif i < len(daily) - 3 or not keep:
      note("The row for %s had a missing value and was left out. Changes beside that date "
        "span two sessions." % date(r), "rows_dropped")
    else:
      text = "The source has no complete prices for %s yet, so this view ends at %s." % (
        date(r), date(keep[-1]))
      if unfinished(r[0]):
        note(text + " That session was not over at the fetch.")
      else:
        if price and stamp:
          text += " The source's last regular price is %s, dated %s." % (
            f_price(price), iso(day(stamp)))
        note(text, "prices_behind")
      break
  for t, o, h, lo, c, v in keep:
    # 0.01% of slack, as for the fill.
    if min(o, h, lo, c) <= 0 or v < 0 or lo * 0.9999 > min(o, c) or h * 1.0001 < max(o, c):
      stop("E_OHLC", "the prices for %s are impossible (zero, negative or outside the day's range)"
        % iso(day(t)))
  n = len(keep)
  if n < 60:
    stop("E_FEW", "only %d usable daily rows; the desk needs at least 60" % n)
  D = [date(r) for r in keep]
  # Gaps count between the newest 60 rows.
  for i in range(n - 59, n):
    gap, ends = day(keep[i][0]) - day(keep[i - 1][0]), (D[i - 1], D[i])
    if gap > (1 if crypto else 12):
      stop("E_GAP", "the daily history is broken between %s and %s" % ends)
    if gap > 5:
      note("No prices between %s and %s: a long holiday or missing days." % ends, "history_gap")
  last = keep[-1][0]
  fetched = fetched or last
  if not -86400 <= fetched - last <= 12 * 86400:
    stop("E_STALE", "the newest row (%s) is more than 12 days older than the fetch, or dated "
      "after it" % D[-1])
  live = unfinished(last)
  session = "unknown" if live is None else "unfinished" if live else "finished"
  did = hashlib.sha256("".join(
    "%s,%r,%r,%r,%r,%r;" % tuple([d] + r[1:]) for d, r in zip(D, keep)).encode())
  _, o, h, lo, c, v = zip(*keep)
  v = [int(x) if x == int(x) else x for x in v]
  cur = cur if re.fullmatch("[A-Za-z]{3}", str(cur)) else ""
  unit = MINOR.get(cur, cur)
  if not cur:
    note("The data names no currency.")
  if unit != cur:
    note("Prices are in %s (%s), a hundredth of the main currency unit." % (unit, cur))
  if live is None:
    note("The source gave no session times, so the newest row may not be final.")
  if not doc:
    note("These prices came from a file, so the time they were downloaded is not known.")
  if live:
    note("The session of %s was unfinished at the fetch: its price, range and volume are not "
      "final." % D[-1], "unfinished_session")
  if n < 200:
    flags.append("short_history")

  facts, sheet, miss = {}, [], []

  def pc(a, b):
    return 100 * (a / b - 1)

  def sma(k, i=n):
    return mean(c[i - k:i]) if i >= k else None

  def wil(x):
    return ewm(x, 1 / 14)

  def F(k, x, label, kind="level", sign=False, as_of=None, unit=""):
    if x is None or kind != "date" and not math.isfinite(x):
      return miss.append(k)
    if kind == "pct":
      t = ("%+.2f%%" if sign else "%.2f%%") % (round(x, 2) + 0.0)  # never "-0.00%"
    elif kind in ("count", "money"):
      t = f_big(x) + unit
    elif kind == "date":
      t = x
    elif kind == "price" or k[:3] in ("mac", "atr"):
      t = f_price(x)
    else:
      t = "%.2f" % x
    facts[k] = f = {"v": x, "t": t, "label": label, "kind": kind}
    if sign:
      f["sign"] = t[0]
    if as_of:
      f["as_of"] = as_of
    sheet.append("%s | %s | %s" % (k, label, t) + (" | as of " + as_of if as_of else ""))

  px = "close" if session == "finished" else "last price"
  F("last", c[-1], "last " + px[-5:], "price")
  F("as_of", D[-1], "data date", "date")
  F("prev_close", c[-2], "previous close", "price")
  F("chg1", pc(c[-1], c[-2]), "one-day change", "pct", 1)
  for N in range(10):
    i, d = n - 1 - N, D[n - 1 - N]
    F("date.%d" % N, d, "date of table row %d" % N, "date")
    for k, s in zip(COLS, (o, h, lo, c)):
      F("%s.%d" % (k, N), s[i], "%s on %s" % (k if N or k != "close" else px, d), "price")
    F("volume.%d" % N, v[i], "volume on " + d, "count")
    F("chg.%d" % N, pc(c[i], c[i - 1]), "change on " + d, "pct", 1)
    F("gap.%d" % N, pc(o[i], c[i - 1]), "opening gap on " + d, "pct", 1)

  dl = [c[i] - c[i - 1] for i in range(1, n)]
  up, dn = wil([max(x, 0.0) for x in dl])[-1], wil([max(-x, 0.0) for x in dl])[-1]
  F("rsi14", div(100 * up, up + dn), "RSI(14)")
  line = [a - b for a, b in zip(ewm(c, 2 / 13), ewm(c, 2 / 27))]
  sig = ewm(line, 0.2)[-1]
  F("macd", line[-1], "MACD(12,26)")
  F("macd_signal", sig, "MACD signal(9)")
  F("macd_hist", line[-1] - sig, "MACD histogram")
  mid = sma(20)
  sd = math.sqrt(mean([(x - mid) ** 2 for x in c[-20:]]))
  F("bb_pctb", div(c[-1] - (mid - 2 * sd), 4 * sd), "Bollinger %B(20,2)")
  F("bb_upper", mid + 2 * sd, "upper Bollinger band", "price")
  F("bb_lower", mid - 2 * sd, "lower Bollinger band", "price")
  atr = wil([h[0] - lo[0]] + [
    max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, n)])
  F("atr14", atr[-1], "ATR(14)")
  F("atr14_pct", atr[-1] / c[-1] * 100, "ATR(14) as a share of the " + px, "pct")
  pdm, mdm = [None], [None]
  for i in range(1, n):
    a, b = h[i] - h[i - 1], lo[i - 1] - lo[i]
    pdm.append(a if a > b and a > 0 else 0.0)
    mdm.append(b if b > a and b > 0 else 0.0)
  # DX: the average range cancels out of the ratio.
  dx = [None if a is None else div(100 * abs(a - b), a + b)
    for a, b in zip(wil(pdm), wil(mdm))]
  F("adx14", wil(dx)[-1], "ADX(14)")
  tp = [(h[i] + lo[i] + c[i]) / 3 for i in range(n)]
  m = mean(tp[-20:])
  F("cci20", div(tp[-1] - m, 0.015 * mean([abs(x - m) for x in tp[-20:]])), "CCI(20)")
  ks = []
  for i in (n - 3, n - 2, n - 1):
    a, b = min(lo[i - 13:i + 1]), max(h[i - 13:i + 1])
    ks.append(div(100 * (c[i] - a), b - a))
  kd = None if None in ks else mean(ks)
  F("stoch_k", ks[2], "Stochastic %K(14)")
  F("stoch_d", kd, "Stochastic %D(3)")
  F("stoch_j", kd and 3 * ks[2] - 2 * kd, "Stochastic J")
  pos = math.fsum(tp[i] * v[i] for i in range(n - 14, n) if tp[i] > tp[i - 1])
  neg = math.fsum(tp[i] * v[i] for i in range(n - 14, n) if tp[i] < tp[i - 1])
  F("mfi14", div(100 * pos, pos + neg), "MFI(14)")

  for k in (10, 50, 200):
    a, t = sma(k), "%d-day average" % k
    F("sma%d" % k, a, t, "price")
    F("sma%d_gap" % k, a and pc(c[-1], a), "%s against the %s" % (px, t), "pct", 1)

  for k in (1, 5, 21, 63):
    F("ret%d" % k, pc(c[-1], c[-1 - k]) if n > k else None, "%d-day return" % k, "pct", 1)
  r = [c[i] / c[i - 1] - 1 for i in range(n - 21, n)]
  m = mean(r)
  F("vol21", 100 * math.sqrt(math.fsum((x - m) ** 2 for x in r) / 20) * math.sqrt(252),
    "21-day annualised volatility", "pct")

  for k, fn in (("high", max), ("low", min)):
    x = fn(c[-252:]) if n >= 200 else None
    i = n - 1 - c[::-1].index(x) if x else 0
    F(k + "52", x, "52-week %s close" % k, "price")
    F(k + "52_date", x and D[i], "date of the 52-week %s close" % k, "date")
    F(k + "52_gap", x and pc(c[-1], x), "%s against the 52-week %s" % (px, k), "pct", 1)

  a = mean(v[-20:])
  F("vol_avg20", a, "20-day average volume", "count")
  F("vol_ratio", div(v[-1], a), "newest volume against the 20-day average", "ratio")

  i = max(range(n - 10, n), key=lambda j: abs(c[j] / c[j - 1] - 1))
  t = "largest one-day change in the table"
  F("move_max", pc(c[i], c[i - 1]), t, "pct", 1)
  F("move_max_date", D[i], "date of the " + t, "date")

  price_facts, st = len(miss), doc.get("statements") or {}

  def one(k, key, label, kind="money"):
    s = sorted((when(e["asOf"]) // 86400, e["value"], clean(e.get("currency"))[:3])
      for e in st.get(key) or [] if e.get("value") is not None)
    # A money figure names its currency, often not the price's.
    F(k, s[-1][1] if s else None, label, kind, as_of=s and iso(s[-1][0]),
      unit=" " + s[-1][2] if s and s[-1][2] and kind == "money" else "")
    return s

  def two(k, a, b, label):
    # Only when both figures carry the same date.
    ok = a and b and a[-1][0] == b[-1][0] and b[-1][1] > 0
    F(k, 100 * a[-1][1] / b[-1][1] if ok else None, label, "pct", as_of=ok and iso(a[-1][0]))

  def yoy(k, s, label):
    old = [x for d, x, _ in s if abs(s[-1][0] - 365 - d) <= 20]
    ok = old and old[0] > 0
    F(k, pc(s[-1][1], old[0]) if ok else None, label + " against a year earlier", "pct", 1,
      ok and iso(s[-1][0]))

  one("pe_trailing", "trailingPeRatio", "trailing P/E", "ratio")
  one("pe_forward", "trailingForwardPeRatio", "forward P/E", "ratio")
  one("peg", "trailingPegRatio", "PEG ratio", "ratio")
  one("mcap", "trailingMarketCap", "market value")
  t = ", trailing 12 months"
  rev = one("rev_ttm", "trailingTotalRevenue", "revenue" + t)
  two("margin_ttm", one("ni_ttm", "trailingNetIncome", "net income" + t), rev, "net margin" + t)
  t = "revenue, newest quarter"
  yoy("rev_q_yoy", one("rev_q", "quarterlyTotalRevenue", t), t)
  t = "net income, newest quarter"
  yoy("ni_q_yoy", one("ni_q", "quarterlyNetIncome", t), t)
  debt = one("debt", "quarterlyTotalDebt", "total debt")
  two("debt_equity", debt, one("equity", "quarterlyStockholdersEquity", "shareholders' equity"),
    "debt to equity")

  stm = miss[price_facts:]
  for t, ks, flag in (
      ("Too little history (%d daily rows) or a division by zero" % n, miss[:price_facts], 0),
      ("The source gave no statement figure", stm, len(stm) > 13 and "statements_absent")):
    if ks:
      note("%s, so these facts are absent: %s." % (t, ", ".join(ks)), flag)
  heads = []
  for x in (doc.get("news") or [])[:10]:
    if clean(x.get("title")):
      heads.append({
        "id": "h%d" % (len(heads) + 1), "date": iso(day(int(x["time"]))) if x.get("time") else "",
        "publisher": clean(x.get("publisher"))[:60], "title": clean(x["title"])[:160]})
  if not heads:
    note("No headlines came with this data.", "no_headlines")
  k = max(0, n - 120)
  b = {
    "schema": "brief.v1", "version": VERSION, "symbol": sym,
    "name": clean(meta.get("longName") or meta.get("shortName") or sym)[:80],
    "type": typ or "UNKNOWN", "currency": cur, "unit": unit,
    "source": "yahoo-chart" if doc else "price-file", "as_of": D[-1],
    "fetched_at": "%s %02d:%02d UTC" % (
      iso(fetched // 86400), fetched % 86400 // 3600, fetched % 3600 // 60),
    "session": session, "data_id": did.hexdigest()[:8], "flags": flags, "notes": notes,
    "facts": facts,
    "series": {
      "date": D[k:], "close": c[k:], "sma50": [sma(50, i + 1) for i in range(k, n)],
      "sma200": [sma(200, i + 1) for i in range(k, n)]},
    "headlines": heads}
  sheet = ["# %s | %s | %s | %s | session %s" % (sym, b["name"], b["type"], unit, session)] + sheet
  sheet += ["# Notes"] + [" | ".join(x.values()) for x in notes]
  sheet += ["# Flags", ", ".join(flags) or "none", "# Headlines"]
  sheet += [" | ".join(x.values()) for x in heads]
  return b, sheet + ["BRIEF ok %s %s facts=%d data_id=%s" % (sym, D[-1], len(facts), b["data_id"])]


def digest(path):
  with open(path, "rb") as f:
    lines = f.read().splitlines()
  t = b"\n".join(x.rstrip() for x in lines
    if not re.fullmatch(b'SELF = "[0-9a-f]{12}"', x.rstrip()))
  return hashlib.sha256(t.rstrip()).hexdigest()[:12]


def kat():
  # Known-answer test on a fixed series.
  rows = [(86400 * (20000 + i), 50 + i % 7, 60 + i % 4, 45 + i % 3, 50 + i % 9, 1000 + i % 11)
    for i in range(260)]
  facts = build({"fetched_at": 86400 * 20260}, {}, rows, "KAT", "USD")[0]["facts"]
  return hashlib.sha256("|".join(f["t"] for f in facts.values()).encode()).hexdigest()[:12]


def run(a, safe):
  pre = a[1] if len(a) == 2 and a[0] == "--preflight" else None
  stamps = {"brief.py": SELF}
  if pre is not None:
    stamps.update(SIBLINGS)
  found = []
  for name in sorted(stamps):
    try:
      path = __file__ if name == "brief.py" else pre.rstrip("/\\") + "/" + name
      ok = stamps[name] in ("0" * 12, digest(path))
      found.append(name)
    except OSError:
      # Only render.py must be there: the download and the page are optional steps.
      ok = name in ("get_data.py", "page.py")
    if not ok:
      stop("E_SELF" if name == "brief.py" else "E_SIBLING",
        name + " is missing or was altered; load the skill files again", 3)
  safe.append(1)
  if "0" * 12 in stamps.values():
    say("WARN unstamped development copy", 1)
  if pre is not None:
    try:
      ok = kat() == KAT
    except Exception:
      ok = False
    if not ok:
      stop("E_KAT", "the indicator known-answer test failed on this Python", 3)
    say("PREFLIGHT ok %s python %d.%d.%d checked " % ((VERSION,) + sys.version_info[:3])
      + " ".join(found))
    return 0
  if a == ["--version"]:
    say("trading-agents-desk brief.py " + VERSION)
    return 0
  pos, opt, it = [], {}, iter(a)
  for x in it:
    if x in ("--symbol", "--currency"):
      opt[x[2:]] = next(it, "")
    else:
      pos.append(x)
  if len(pos) != 2:
    stop("E_USAGE", USAGE, 4)
  with open(pos[0], "rb") as f:
    b = f.read()
  text = b.decode("utf-16" if b[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig")
  brief, sheet = build(*parse(text.replace(chr(8722), "-").replace(chr(160), " "), opt))
  with open(pos[1], "w", encoding="ascii", newline="\n") as f:
    f.write(json.dumps(brief) + "\n")
  for x in sheet:
    say(x)
  return 0


def main(argv):
  safe = []
  try:
    return run(list(argv[1:]), safe)
  except Stop as e:
    say(e.args[1], 1)
    return e.args[0]
  except OSError:
    sys.stdout = None  # no retry at exit
    say("ERROR E_USAGE cannot read the input file or write the output file", 1)
    return 4
  except Exception:
    # Before the integrity check passes, any failure means a damaged copy.
    say("STOP E_INPUT " + E_INPUT if safe else "ERROR E_SELF this copy of brief.py is damaged", 1)
    return 2 if safe else 3


if __name__ == "__main__":
  sys.exit(main(sys.argv))
