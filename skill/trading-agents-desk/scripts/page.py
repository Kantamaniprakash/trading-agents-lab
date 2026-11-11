"""Trading Agents Desk: draw the finished report as one self-contained HTML page. No network."""
import hashlib
import html
import json
import math
import re
import sys

VERSION = "2.0.0"
SELF = "e9303bbf9c12"
SWING = ("oversold", "overbought")
# Meter range, both thresholds, and the words for a value outside them.
SCALES = {
  "rsi14": (0, 100, 30, 70, SWING), "stoch_k": (0, 100, 20, 80, SWING),
  "mfi14": (0, 100, 20, 80, SWING),
  "bb_pctb": (-0.5, 1.5, 0, 1, ("under lower band", "over upper band")),
  "adx14": (0, 100, 20, 25, ("weak trend", "strong trend"))}
# Statement figures by preference: the first eight that exist are shown.
TILES = (
  "pe_trailing pe_forward peg mcap margin_ttm rev_q_yoy ni_q_yoy debt_equity rev_ttm ni_ttm "
  "rev_q ni_q debt equity").split()
# Chart lines, the close last: series, fact, colour, name at the line's end.
LINES = (
  ("sma200", "sma200", "#1baf7a", "200d"), ("sma50", "sma50", "#eb6834", "50d"),
  ("close", "last", "#2a78d6", ""))
MONTHS = " Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ")
# Classes: s2 s3 s4 = columns of six that a card spans, col = a stack of cards, wide = not
# on a phone.
CSS = """*{box-sizing:border-box;margin:0}
body{font:14px/1.5 system-ui,sans-serif;background:#f2f3ee;
color:#0f1f38;padding:12px;overflow-wrap:anywhere}
main{max-width:1240px;margin:auto;display:grid;grid-template-columns:repeat(6,1fr);gap:12px}
main>*,section{grid-column:span 6;min-width:0;border-radius:14px;padding:22px 24px}
section,.col{display:flex;flex-direction:column}
section{background:#fff;border:1px solid #e1e4e8;overflow:clip}
.col{gap:12px;padding:0}.col>*{flex:auto}.hd{order:1}.rc{order:2}
.s4{grid-column:span 4}.s3{grid-column:span 3}.s2{grid-column:span 2}
header{background:linear-gradient(110deg,#0b1d36,#17365f);color:#fff;padding:30px 32px}
h1{font-size:30px;line-height:1.2;margin-bottom:4px}
h1 i,.t i{display:inline-block;font-size:15px;font-weight:500;
font-style:normal;opacity:.75}
h2,h4,dt,th,.why b,aside b{font-size:11px;font-weight:600;letter-spacing:.13em;
text-transform:uppercase;color:#5a6679;text-align:left}
h2{margin-bottom:12px}h4,dt{margin-bottom:4px}
header h2,header p,header small{color:#b9c6da}
h3{font-size:20px;line-height:1.3;margin-bottom:16px}
small{display:block;font-size:12px;font-weight:400;color:#5a6679}
.dn{color:#b3261e}.up{color:#0a7a4b}
.t{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-top:22px}
.t div{background:#ffffff12;border:1px solid #ffffff26;border-radius:10px;padding:14px 18px}
.t h2{margin-bottom:6px}.t div>b{display:block;font-size:22px}.t b{color:#fff}
.why{font-size:26px;line-height:1.35;
border-left:3px solid #2a78d6;border-radius:0;margin:14px 8px;padding:0 0 0 22px}
.why b,aside b{display:block;margin-bottom:6px}
aside{background:#e9eef5;border:1px solid #cfd8e4;padding:14px 24px}
svg{display:block;width:100%;overflow:visible}
text{font-size:11px;fill:#5a6679}.el,.lt,.lp{fill:#0f1f38;font-weight:600}
.lt,.ld text{paint-order:stroke;stroke:#fff;stroke-width:4px}
.g{stroke:#e6e9ed}.fl{fill:#2a78d6;opacity:.1}
.ln{fill:none;stroke-width:2;vector-effect:non-scaling-stroke}
.lv{stroke:#0f1f38;stroke-dasharray:5 4;opacity:.65}circle{stroke:#fff;stroke-width:2}
.ch{flex:1;display:flex;min-height:340px}.ch svg{flex:1;min-width:0}
.m{display:flex;flex-wrap:wrap;align-items:baseline;gap:0 10px;margin-bottom:10px}
.m span{flex:auto;font-weight:600}
.m b{font-size:16px;margin-left:auto;text-align:right}.m svg{margin-top:6px}
.m rect{fill:#e3e8ee}.m line{stroke:#fff;stroke-width:2}.m circle,.ld circle{fill:#0f1f38}
dl{display:grid;gap:16px 24px;
grid-template-columns:repeat(auto-fit,minmax(min(var(--m,180px),100%),1fr))}
.in dl{--m:72px;border-top:1px solid #e6e9ed;padding-top:14px;gap:8px}
.in dd{font-size:15px;white-space:nowrap}.in dl,.s3 dl{margin-top:auto}
ul{list-style:none;padding:0}li{padding:3px 0}.hl li{padding:10px 0}
li:first-child{padding-top:0}li+li,td,.fu div{border-top:1px solid #e6e9ed}
.ev{margin-bottom:16px}.ev li{display:flex;justify-content:space-between;gap:16px;padding:7px 0}
.ev b{flex:none;max-width:60%;text-align:right}.ev span:first-letter{text-transform:uppercase}
h2 b{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--a);
margin-right:8px}.bull{--a:#0f7b6c}.bear{--a:#b4432e}
.ax{stroke:#5a6679}.lp{font-size:12.5px}
table{width:100%;border-spacing:0;margin-top:6px}td,th{padding:9px 0;vertical-align:top}
td+td,th+th{text-align:right;white-space:nowrap;padding-left:18px;max-width:10em;overflow:hidden;
text-overflow:ellipsis}td+td{font-weight:600}
h2+small{margin:-6px 0 14px}
.fu{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
grid-auto-rows:auto auto 1fr;gap:0 18px;flex:1}
.fu div{display:grid;grid-row:span 3;grid-template-rows:subgrid;padding:9px 0}
.fu dt{align-self:end}.fu dd{font-size:17px;font-weight:600}
.rc dl{--m:300px}.tr{font-size:17px;margin-bottom:18px}
.tr b{background:#fdefc3;padding:1px 5px;border-radius:4px}
.x{border-top:1px solid #e6e9ed;margin-top:18px;padding-top:14px;color:#3d4a5e}
@media(max-width:860px){body{padding:8px}main{gap:10px}main>*,section{padding:18px}
.s2,.s3,.s4{grid-column:span 6}.col{display:contents}h1{font-size:26px}
.t{grid-template-columns:1fr 1fr;gap:8px}.t div{padding:12px}.t div>b{font-size:16px}
.why{font-size:19px;margin:18px 4px;padding:0 0 0 14px}.ch{min-height:260px}
.wide{display:none}}"""


def esc(s, cut=None):
  """A string as safe HTML text, cut to `cut` characters. A date stays on one line."""
  text = html.escape(re.sub(r"[\0-\x1f\x7f]", " ", str(s)[:cut]))
  return re.sub(r"(?<=\d)-(?=\d)", "&#8209;", text)


def card(css, title, body):
  return '<section class="%s"><h2>%s</h2>%s</section>' % (css, title, body)


def pairs(items):
  return "<dl>%s</dl>" % "".join("<div><dt>%s</dt><dd>%s</dd></div>" % pair for pair in items)


def bullets(items, css="list"):
  return "<ul class=%s>%s</ul>" % (css, "".join("<li>%s</li>" % item for item in items))


def chart(brief, levels, word, unit):
  """The price card: the daily series, both averages and the report's levels, as SVG."""
  series = brief["series"]
  dates = series["date"]
  count = len(dates)

  # The price axis runs in round steps around all that is drawn.
  prices = [level["price"] for level in levels]
  for key, name, colour, short in LINES:
    prices += [v for v in series[key] if v is not None]
  lo, hi = min(prices), max(prices)
  size = hi - lo or abs(hi) or 1
  step = 10 ** math.floor(math.log10(size / 4))
  step *= next(m for m in (1, 2, 2.5, 5, 10) if size / step / m <= 5)
  lo = math.floor(lo / step) * step
  hi = max(math.ceil(hi / step) * step, lo + step)

  def y_of(price):
    # Percent of the plot's height. The strip under the prices is room for labels.
    return 5 + (hi - price) * 83 / (hi - lo)

  out = '<svg role=img aria-label="Daily prices, averages and levels">'
  tick_chars = 1
  for i in range(round((hi - lo) / step) + 1):
    # Plain decimals, no zeros at the end, no exponent.
    label = ("%.10f" % (lo + i * step)).rstrip("0").rstrip(".")
    tick_chars = max(tick_chars, len(label))
    y = y_of(lo + i * step)
    out += ("<line class=g x2=100%% y1=%.2f%% y2=%.2f%% /><text x=-7 y=%.2f%% dy=4 "
      "text-anchor=end>%s</text>" % (y, y, y, label))
  # A month name where the month changes, unless the last name is too close.
  named = -count
  for i in range(1, count):
    month = dates[i][5:7]
    if month != dates[i - 1][5:7] and i - named > count / 9:
      named = i
      out += "<text x=%.1f%% y=100%% dy=16>%s</text>" % (
        i * 100 / (count - 1), MONTHS[int(month)])

  # The lines sit in their own box, which stretches with the card. The close comes last,
  # and the area under it is tinted.
  out += '<svg height=100%% viewBox="0 0 %d 100" preserveAspectRatio=none>' % (count - 1)
  ends = []
  for key, name, colour, short in LINES:
    if series[key][-1] is not None:
      points = ["%d,%.2f" % (i, y_of(v)) for i, v in enumerate(series[key]) if v is not None]
      out += '<path class=ln stroke=%s d="M%s"/>' % (colour, "L".join(points))
      label = "%s %s" % (short or word.capitalize(), str(brief["facts"][name]["t"])[:13])
      ends.append((y_of(series[key][-1]), colour, label))
  out += '<path class=fl d="M%sV88H0Z"/></svg>' % "L".join(points)

  # A level is a dashed line with its price and, on a wide screen, its name. The top label
  # sits above its line and the bottom one below it, so close levels keep their own.
  tags = ""
  rows = sorted(levels, key=lambda level: -level["price"])
  for i, level in enumerate(rows):
    y = y_of(level["price"])
    dy = 4 if 0 < i < len(rows) - 1 or len(rows) == 1 else 16 if i else -8
    out += "<line class=lv x2=100%% y1=%.2f%% y2=%.2f%% />" % (y, y)
    tags += "<text class=lt x=0 y=%.2f%% dy=%d>%s<tspan class=wide> &#183; %s</tspan></text>" % (
      y, dy, esc(level["price_t"], 13), esc(level["label"], 44))
  out += tags
  # A dot where each line ends. A label that would sit on the one above moves down.
  spot = -9
  for y, colour, label in sorted(ends):
    spot = max(y, spot + 6)
    out += ("<circle cx=100%% cy=%.2f%% r=4 fill=%s /><text class=el x=100%% dx=9 y=%.2f%% "
      "dy=4>%s</text>" % (y, colour, spot, esc(label)))
  # Room at the left for the axis numbers, at the right for the end labels.
  return ("<small>%d daily %ss in %s, %s to %s. 50d and 200d: the 50-day and 200-day averages. "
    "Dashed lines: the report's levels.</small>"
    '<div class=ch style="padding:0 %dpx 22px %dpx">%s</svg></div>' % (
      count, word, unit, esc(dates[0]), esc(dates[-1]),
      16 + 6.4 * max(len(end[2]) for end in ends), 12 + 6.4 * tick_chars, out))


def meter(fact, lo, hi, under, over, words):
  """One indicator on its real scale: the thresholds as gaps, the value as a dot."""
  value = fact["v"]
  # A value outside the usual range widens the scale: the dot stays on the track.
  lo, hi = min(lo, value), max(hi, value)

  def at(x):
    return (x - lo) * 100 / (hi - lo)

  # The words go by the two decimals shown.
  state = "between %g and %g" % (under, over)
  if round(value, 2) < under:
    state = "below %g &#183; %s" % (under, words[0])
  if round(value, 2) > over:
    state = "above %g &#183; %s" % (over, words[1])
  marks = ""
  for x, shift, anchor in (
      (lo, 0, "start"), (under, -4, "end"), (over, 4, "start"), (hi, 0, "end")):
    marks += ("<line x1=%.1f%% x2=%.1f%% y2=14 /><text x=%.1f%% y=27 dx=%d text-anchor=%s>"
      "%g</text>" % (at(x), at(x), at(x), shift, anchor, round(x, 2)))
  return ("<div class=m><span>%s</span><small>%s</small><b>%s</b><svg height=30>"
    "<rect width=100%% y=4 height=6 rx=3 />%s<circle cx=%.1f%% cy=7 r=6 /></svg></div>" % (
      esc(fact["label"]), state, esc(fact["t"]), marks, at(value)))


def indicators(facts):
  """The meters, then the returns over trading days."""
  returns = []
  for days in (1, 5, 21, 63):
    fact = facts.get("ret%d" % days)
    if fact:
      # Red and green only repeat the sign the text shows.
      css = "dn" if "-" in fact["t"] else "up"
      returns.append(("%d day%s" % (days, "s" * (days > 1)),
        "<b class=%s>%s</b>" % (css, esc(fact["t"]))))
  return card("s2 in", "Indicators",
    "<small>Dot: the value. Gaps: the convention thresholds.</small>%s%s" % (
      "".join(meter(facts[key], *SCALES[key]) for key in SCALES if key in facts),
      pairs(returns)))


def case(side, part):
  """The bull or the bear card: both built the same way, with the same room."""
  evidence = ["<span>%s</span><b>%s</b>" % (esc(x["label"]), esc(x["text"]))
    for x in part["evidence"]]
  return card("s3 " + side, "<b></b>The %s case" % side,
    "<h3>%s</h3><h4>Evidence</h4>%s%s" % (
      esc(part["claim"]), bullets(evidence, "ev"),
      pairs([("Also", esc(part["also"])), ("Weak point", esc(part["weak"]))])))


def change(levels, last, last_words):
  """What would change this view: the last price on a line between the levels, and a table."""
  prices = [level["price"] for level in levels] + [last]
  lo = min(prices)
  size = max(prices) - lo or 1
  marks = ""
  table = "<table><tr><th>Level</th><th>Price</th><th>To reach it</th></tr>"
  for row, level in enumerate(levels):
    x = 5 + (level["price"] - lo) * 90 / size
    # Each label has its own row. In the left half it starts at its mark, in the right half
    # it ends there.
    marks += ("<line class=ax x1=%.1f%% x2=%.1f%% y1=29 y2=43 /><text class=lp x=%.1f%% y=%d "
      "text-anchor=%s>%s &#183; %s</text>" % (
        x, x, x, 60 + 20 * row, "start" if x < 50 else "end", esc(level["price_t"], 12),
        esc(level["gap_t"], 8)))
    table += ("<tr><td><b>%s</b><small>Would support the %s case, %s</small></td><td>%s</td>"
      "<td>%s</td></tr>" % tuple(
        esc(level[k]) for k in ("label", "side", "words", "price_t", "gap_t")))
  x = 5 + (last - lo) * 90 / size
  # The last price sits above the line, centred on its dot if there is room.
  half = 1.1 * len(last_words)
  anchor = "start" if x < half else "end" if x + half > 100 else "middle"
  return card("cv", "What would change this view",
    "<svg class=ld height=%d>"
    "<line class=ax x1=5%% x2=95%% y1=36 y2=36 />%s<circle cx=%.1f%% cy=36 r=6 />"
    "<text class=lp x=%.1f%% y=20 text-anchor=%s>%s</text></svg>%s</table>" % (
      52 + 20 * len(levels), marks, x, x, anchor, esc(last_words), table))


def receipts(brief, report, notes, unit):
  record = re.sub(r"([+-]?\d[\d.]*%)", r"<b>\1</b>", esc(report["test_record"]))
  data = "%s &#183; %s &#183; as of %s, session %s &#183; prices in " % tuple(
    esc(brief[k]) for k in ("type", "source", "as_of", "session")) + unit
  ran = esc(report["roles"])
  if report["skipped"]:
    ran += " Skipped: %s." % esc(", ".join(report["skipped"]))
  return card("rc", "Receipts", "<h4>Test record</h4><p class=tr>%s</p>%s<p class=x>%s<small>"
    "Report %s &#183; Data %s</small></p>" % (
      record,
      pairs([
        ("The check", bullets(map(esc, report["receipt"]))),
        ("The data", bullets([data] + (notes or ["No data notes."]))),
        ("How it ran", ran + " The desk started from no position.")]),
      esc(report["disclaimer"]), esc(report["report_id"]), esc(brief["data_id"])))


def build(brief, report):
  """The whole page as one string. Every input string passes through esc."""
  facts = brief["facts"]
  notes = [esc(note["text"]) for note in brief["notes"]]
  # An unfinished session has a last price, not a close.
  finished = brief["session"] == "finished"
  word = "close" if finished else "price"
  unit = esc(brief["unit"] or "units")
  last = facts["last"]
  day = esc(brief["as_of"])
  view = "at the %s close" % day if finished else "on %s, session %s" % (
    day, esc(brief["session"]))
  if "prices_behind" in brief["flags"]:
    view += " (the data source is behind)"
  # A price file has no fetch time.
  origin = "Data fetched " + esc(brief["fetched_at"])
  if brief["source"] == "price-file":
    origin = "Prices from a file"
  tiles = ""
  for tile in (
      ("The argument", esc(report["argument"]["text"]), "Bull against Bear"),
      ("The paper desk", esc(report["desk"]["text"]),
        "%g%% of a paper book" % (100 * report["desk"]["size"])),
      ("Last " + word, "%s <i>%s</i>" % (esc(last["t"]), unit),
        "<b>%s</b> on the day" % esc(facts["chg1"]["t"])),
      ("The check", esc(report["figures"]) + " figures", "inserted by code")):
    tiles += "<div><h2>%s</h2><b>%s</b><small>%s</small></div>" % tile
  out = ("<!doctype html><html lang=en><meta charset=utf-8>"
    '<meta name=viewport content="width=device-width,initial-scale=1">'
    "<title>%s desk view</title><style>%s</style><main><header><h2>Trading Agents Desk</h2>"
    "<h1>%s <i>%s</i></h1><p>Desk view %s &#183; Last %s %s %s &#183; %s</p><small>Research "
    "simulation, not advice. This desk design lost to buy-and-hold in its only test.</small>"
    "<div class=t>%s</div></header><p class=why><b>Why</b>%s</p>" % (
      esc(brief["symbol"]), CSS, esc(brief["symbol"]), esc(brief["name"]), view, word,
      esc(last["t"]), unit, origin, tiles, esc(report["why"])))
  if brief["flags"]:
    # A notice in plain words, not an error.
    strip = notes or [esc(flag).replace("_", " ") for flag in brief["flags"]]
    out += "<aside><b>Data notes</b>%s</aside>" % bullets(strip)
  out += card("s4", "Price", chart(brief, report["levels"], word, unit)) + indicators(facts)
  out += case("bull", report["bull"]) + case("bear", report["bear"])

  sentences = re.split(r"(?<=[a-z\d%)][.!?]) (?=[A-Z])", str(report["risk"]))
  risk = [re.sub(r"^(\w+:)", r"<b>\1</b>", esc(sentence)) for sentence in sentences]
  risk = card("rk", "Risk review", bullets(risk, "hl"))
  stmt = ""
  for key in [key for key in TILES if key in facts][:8]:
    stmt += "<div><dt>%s</dt><dd>%s</dd><small>as of %s</small></div>" % tuple(
      esc(facts[key][k]) for k in ("label", "t", "as_of"))
  if stmt:
    stmt = card("fd", "Fundamentals", "<div class=fu>%s</div>" % stmt)
  news = ["%s<small>%s &#183; %s</small>" % tuple(
    esc(item[k]) for k in ("title", "publisher", "date")) for item in brief["headlines"]]
  if news:
    news = card("hd", "Headlines the desk saw", "<small>Third-party text, shown as "
      "published. Not the desk&#39;s words.</small>" + bullets(news, "hl"))
  # Two stacks of cards. When only one of the last two cards exists it takes a full row.
  left = change(report["levels"], last["v"], "Last %s %s" % (word, str(last["t"])[:13]))
  below = stmt or news or ""
  if stmt and news:
    left, risk, below = left + news, risk + stmt, ""
  out += '<div class="col s4">%s</div><div class="col s2">%s</div>%s' % (left, risk, below)
  return out + receipts(brief, report, notes, unit) + "</main></html>"


def digest(path):
  """The contract's script hash: its bytes, the stamp line as SELF, 12 hex digits."""
  with open(path, "rb") as f:
    lines = [line.rstrip() for line in f.read().splitlines()]
  kept = [re.sub(b'^SELF = "[0-9a-f]{12}"$', b"SELF", line) for line in lines]
  return hashlib.sha256(b"\n".join(kept).rstrip()).hexdigest()[:12]


def load(path, schema):
  """Read one input as bytes. A UTF-8 or UTF-16 byte-order mark is accepted."""
  with open(path, "rb") as f:
    # json.loads finds the encoding of bytes itself. NaN and Infinity become null.
    doc = json.loads(f.read(), parse_constant=lambda name: None)
  if doc["schema"] != schema:
    raise ValueError
  return doc


def run(args):
  """Return the exit code and the one line to print. Nothing may end in a traceback."""
  try:
    if SELF == "0" * 12:
      print("WARN unstamped development copy", file=sys.stderr)
    elif SELF != digest(__file__):
      return 3, "ERROR E_SELF this copy of page.py was altered; load the skill files again"
    if args == ["--version"]:
      return 0, "trading-agents-desk page.py " + VERSION
    if len(args) != 3:
      return 4, "ERROR E_USAGE usage: page.py BRIEF REPORT OUT_HTML"
    brief, report = load(args[0], "brief.v1"), load(args[1], "report.v1")
    if [brief["symbol"], brief["data_id"]] != [report["symbol"], report["data_id"]]:
      return 2, ("STOP E_PAIR the report was not made from this brief; make the report "
        "again, then draw the page")
    page = build(brief, report).encode("ascii", "xmlcharrefreplace").decode() + "\n"
    with open(args[2], "w", encoding="ascii", newline="\n") as f:
      f.write(page)
    line = "PAGE ok %s %s bytes=%d" % (brief["symbol"], report["report_id"], len(page))
    return 0, " ".join(line.split())
  except OSError:
    return 4, "ERROR E_FILE cannot read or write a file named on the command line"
  except Exception:
    return 2, "STOP E_INPUT the page needs the brief file and the report file made from it"


if __name__ == "__main__":
  code, line = run(sys.argv[1:])
  line = line.encode("ascii", "backslashreplace").decode()
  print(line, file=sys.stderr if code else sys.stdout)
  sys.exit(code)
