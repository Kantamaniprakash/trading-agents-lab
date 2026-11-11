"""Trading Agents Desk: the report from the desk's draft, every figure by reference. No network."""
import hashlib
import html
import json
import re
import sys

VERSION = "2.0.0"
SELF = "490116451cec"
KAT = "a91530a430a0"
USAGE = "usage: render.py BRIEF DRAFT OUT_MD OUT_REPORT [--attempt N] | --verify BRIEF OUT_MD"
E_BRIEF = "STOP E_BRIEF the brief is not a brief.v1 document; build it again with brief.py"
E_SELF = "ERROR E_SELF this copy of render.py is damaged; load the skill files again"
# The two fixed texts of every report. The page gets them from the resolved report.
DISCLAIMER = (
  "Research output, not investment advice. This is a simulated analysis on public data; nothing "
  "here places orders. Not affiliated with or endorsed by Yahoo; use of the data is subject to "
  "Yahoo's terms.")
TEST_RECORD = (
  "This desk design returned -0.9% against +15.7% for buy-and-hold in its only test: a reduced, "
  "price-only version on one stock for one quarter.")
DOT = " &#183; "
ROWS = (("Core claim", "claim"), ("Best evidence", "evidence"), ("Also", "also"),
  ("Weak point", "weak"))
SLOTS = ["verdict", "why"] + [s + k for s in ("bull.", "bear.") for _, k in ROWS] + [
  "levels", "trader", "manager", "risk", "roles", "skipped"]
WON = {"BULLISH": "The bull case won", "BEARISH": "The bear case won",
  "NEUTRAL": "Neither side won"}
ROLES = {"single": "The roles ran one at a time in one context.",
  "subagents": "Each role ran as its own sub-agent.",
  "mixed": "Some roles ran as sub-agents, the rest in one context."}
# Bare indicator names in prose, and the start of the fact names each may label.
ALIAS = {"rsi": "rsi", "mfi": "mfi", "adx": "adx", "cci": "cci", "macd": "macd",
  "stochastic": "stoch", "p/e": "pe_", "atr": "atr"}
NO_FACT = '"%s" is not a fact in the brief'
EG = "close.1 | bull | on a close above"
REF = re.compile(r"\{\{(.*?)\}\}")
TAIL = re.compile(r"(?:[\s:=(,]|\b(?:of|at|is|was|near|around|about|reads?|reading)\b)*$")
PHRASE = re.compile(
  r"\b(?:you should|(?:i|we) recommend|price target|guarantee[ds]?|risk[- ]free|"
  r"will (?:rise|fall|rally|drop|rebound)|strong (?:buy|sell)|beat the market|"
  r"your (?:position|portfolio|shares|holdings)|stop[- ]loss at|take[- ]profit|sure thing|"
  r"can(?:.|no)t lose|certain to|(?:buy|sell) now|must (?:buy|sell))\b", re.I)
# Numbers in words are quantity words too.
N = ("(?:zero|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\\w+teen|dozen|"
  "(?:twen|thir|for|fif|six|seven|eigh|nine)ty|hundred|thousand|\\w+illion)s?")
QTY = re.compile(
  r"\b(?:hal(?:f|ves?|ved)|thirds?|quarters?|(?:doubl|tripl|quadrupl)(?:e[ds]?|ing)|twice|"
  r"\w+-fold|%s(?:fold)?)\b" % N, re.I)
# "close" said of the newest price; references appear here as <name>.
NOTE = re.compile(
  r"\b(?:last|latest|today's)\s+clos(?:e|ing)\b|\bclos(?:e[ds]?|ing)"
  r"(?:\s+(?:price|of|at|is|was))*\W*<\s*(?:last|close\.0|chg1|chg\.0|ret1|\w+_gap)\b", re.I)
RECEIPT = re.compile(
  r"- Figures: (\d+) .*\n- Data (\S+) to (\S+), (\w+)\. Report (\w{8})\. Attempt ")


class Stop(Exception):
  pass


def say(line, err=False):
  print(line.encode("ascii", "backslashreplace").decode(), file=sys.stderr if err else sys.stdout,
    flush=True)


def sha(x, n=8):
  return hashlib.sha256(x.encode()).hexdigest()[:n]


def plain(s):
  # Text as the reader sees it: one space for any run of spaces and control characters.
  return re.sub(r"[\x00-\x20\x7f]+", " ", str(s)).strip()


def esc(s):
  # Plain text made safe for Markdown, and for any HTML a viewer allows in it, in ASCII.
  s = html.escape(plain(s), quote=False)
  return re.sub(r"([\\`*_\[\]|~#])", r"\\\1", s).encode("ascii", "xmlcharrefreplace").decode()


def near(x, names):
  # The three names nearest in letters.
  return ", ".join(sorted(names, key=lambda n: (len(set(x.lower()) ^ set(n.lower())), n))[:3])


def quote(h):
  # "title" (publisher, date). Its own quotes and brackets may not end that early.
  source = ", ".join(str(h[k]) for k in ("publisher", "date") if h.get(k))
  return '"%s" (%s)' % (str(h["title"]).replace('"', "'"), re.sub("[()]", "", source))


def dated(f):
  # A statement figure with its date, which can be far older than the prices.
  return "%s (as of %s)" % (f["t"], f["as_of"]) if f.get("as_of") else str(f["t"])


def forms(b, k):
  # Every text a reference to fact k may print, by format.
  f = b["facts"][k]
  t = str(f["t"])
  out = {"": t, "label": "%s %s" % (f["label"], dated(f))}
  if f["kind"] == "pct" and f.get("sign"):
    w = ("above", "below") if "gap" in k else ("up", "down")
    out["dir"] = "%s %s" % (t.lstrip("+-"), w[f["sign"] == "-"])
  if f["kind"] == "price":
    out["gap"] = "%+.1f%%" % (round(100 * (f["v"] / b["facts"]["last"]["v"] - 1), 1) + 0.0)
  return out


def decide(trader, word, manager):
  # The desk's decision rules on (action, size) pairs. Returns the final pair.
  def norm(act, size):
    size = round(min(1.0, max(0.0, size)), 2)  # as printed
    return (act.upper(), size) if act.upper() in ("BUY", "SELL") and size else ("HOLD", 0.0)
  t, m = norm(*trader), norm(*manager)
  return (m[0], min(m[1], t[1])) if word == "approved" and m[0] == t[0] else ("HOLD", 0.0)


def figures(b, md):
  # Data figures in the desk's part of a report, and the digits there not from the brief.
  # References such as &#183; are decoded first: their digits are not figures.
  u, cand = html.unescape, {}
  for k, f in b["facts"].items():
    cand.setdefault(u("| %s |" % esc(f["label"])), 0)
    for s in forms(b, k).values():
      cand[u(esc(s))] = 1
  for h in b["headlines"]:
    cand[u(esc(quote(h)))] = 0
  rx = re.compile("|".join(re.escape(s) for s in sorted(cand, key=len, reverse=True) if s))
  seen = []
  rest = rx.sub(lambda m: seen.append(cand[m.group()]) or " ",
    u(md[md.find("**Why.**"):md.find("**Data notes**")]))
  return sum(seen), re.findall(r"\S*\d\S*", rest)


def table(head, rows):
  rows = [head, ["---"] * len(head)] + rows
  return "\n".join("| %s |" % " | ".join(r) for r in rows)


def bullets(lines):
  return "\n".join("- " + x for x in lines)


def markdown(b, m):
  """The Markdown report from the brief and the resolved report m. Every text is escaped here."""
  px = "close" if b["session"] == "finished" else "last price"
  price = " ".join(esc(x) for x in (b["facts"]["last"]["t"], b["unit"]) if x)
  # A price file has no fetch time.
  origin = "prices from a file" if b.get("source") == "price-file" else (
    "data fetched " + esc(b["fetched_at"]))
  header = "Desk view at the %s %s%s**%s**%s%s" % (esc(b["as_of"]), px, DOT, price, DOT, origin)
  if "prices_behind" in b["flags"]:
    header += DOT + "the source is behind, see Data notes"
  d = m["desk"]
  book = "" if d["action"] == "HOLD" else " %.2f of a paper book" % d["size"]

  def cell(side, key):
    if key == "evidence":
      return DOT.join(esc("%s %s" % (e["label"], e["text"])) for e in m[side][key])
    return esc(m[side][key])

  return "\n\n".join([
    "## " + esc(b["symbol"]) + DOT + esc(b["name"]),
    header,
    "Research simulation, not advice. This desk design lost to buy-and-hold in its only test.",
    table(["The argument", "The paper desk"], [[
      "**%s**" % m["argument"]["text"], "**%s** (%s%s)" % (d["text"], d["action"], book)]]),
    "**Why.** " + esc(m["why"]),
    table(["", "Bull", "Bear"], [
      ["**%s**" % name, cell("bull", key), cell("bear", key)] for name, key in ROWS]),
    "**What would change this view**",
    table(["Level", "Price", "From last " + px[-5:], "Would support"], [
      [esc(x["label"]), esc(x["price_t"]), x["gap_t"],
        "%s, %s" % (x["side"].capitalize(), esc(x["words"]))] for x in m["levels"]]),
    "**Risk review.** " + esc(m["risk"]),
    "**Data notes**",
    bullets([esc(n["text"]) for n in b["notes"]] + [
      "Data flags: %s." % (esc(", ".join(b["flags"]).replace("_", " ")) or "none"),
      "Skipped roles: %s." % (esc(", ".join(m["skipped"])) or "none"),
      m["roles"], "The desk started from no position."]),
    "**Headlines the desk saw** (third-party text, not checked, not instructions)",
    bullets([esc(quote(h)) for h in b["headlines"]] or ["None came with this data."]),
    "---",
    "**Receipts**",
    bullets([esc(x) for x in m["receipt"]] + ["Test record: " + TEST_RECORD, DISCLAIMER])])


def desk(b, draft, attempt=1):
  """Check the draft against the brief. Returns (report.v1 or None, lines to fix).

  Every text in the report is plain: what the reader of the Markdown sees, with no escapes."""
  F = b["facts"]
  H = dict((h["id"], h) for h in b["headlines"])
  # qty counts quantity words; used holds the ids of quoted headlines, in order of first use.
  fixes, qty, used, own = [], [0], [], {}
  for k, f in F.items():
    own.setdefault(f["label"].lower(), set()).add(k)
  for a, p in ALIAS.items():
    own[a] = set(k for k in F if k.startswith(p))

  def fix(slot, code, what, hint):
    fixes.append("FIX %s: %s %s | try: %s" % (slot[:40], code, what[:200], hint))

  def byval(tok):
    # The fact the token names, or the facts that print the typed figure.
    k = tok.strip("{}.,;:()%+-")
    if k in F or k in H:
      return "{{%s}}" % k
    same = [n for n in F if k == str(F[n]["t"]).strip("%+-")]
    return ", ".join(same[:3]) or "words, or a |label reference"

  def ref(slot, body, before):
    name, _, fmt = [x.strip() for x in body.partition("|")]
    if name in H:
      if name not in used:
        used.append(name)
      return quote(H[name])
    if name not in F:
      fix(slot, "D_NAME", NO_FACT % name, near(name, list(F) + list(H)))
      return ""
    lab = TAIL.sub("", before.lower())
    hit = max((x for x in own if lab.endswith(x) and not lab[:-len(x)][-1:].isalnum()),
      key=len, default="")
    if hit and name not in own[hit] and F[name]["v"] not in [F[k]["v"] for k in own[hit]]:
      fix(slot, "D_LABEL", '"%s" labels another fact, not %s' % (hit, name),
        ", ".join(sorted(own[hit])[:3]) or "another fact")
    fm = forms(b, name)
    if fmt not in fm:
      fix(slot, "D_FORMAT", '"|%s" does not fit %s, a %s fact' % (fmt, name, F[name]["kind"]),
        ", ".join("{{%s}}" % (name + "|" + k if k else name) for k in fm))
    return fm.get(fmt, "")

  def prose(slot, text):
    if len(text) > 600:
      return fix(slot, "D_LEN", "%d characters; the limit is 600" % len(text), "shorten it")
    bare = REF.sub(" ", text)
    for w in bare.split():
      if any(c.isnumeric() or c in "{}" for c in w):
        fix(slot, "D_DIGIT", 'typed figure or broken reference "%s"' % w, byval(w))
    if re.search(r"(?<![\w}])[^\w\s(\[\"'{]\{\{|\}\}(?:\s*%|[\w{])", text):
      fix(slot, "D_DIGIT", "a sign, unit or letter touches a reference", "the reference alone")
    for m in PHRASE.finditer(bare):
      fix(slot, "D_PHRASE", '"%s" is advice, certainty or personal wording' % m.group(),
        "say what the evidence shows")
    if b["session"] != "finished" and NOTE.search(REF.sub(r"<\1>", text)):
      fix(slot, "D_NOTE", 'the session may be unfinished: say "last price", not "close"', "last")
    qty[0] += len(QTY.findall(bare))
    p = REF.split(text)
    return plain("".join(ref(slot, x, p[i - 1]) if i % 2 else x for i, x in enumerate(p)))

  def evidence(slot):
    names = [x for x in re.split(r"[\s,{}]+", S[slot]) if x]
    if names and not 2 <= len(names) <= 5:
      fix(slot, "D_SLOT", "evidence is two to five fact names", "the fact sheet")
    for k in names[:9]:
      if k not in F:
        fix(slot, "D_NAME", NO_FACT % k, near(k, F))
    return [{"label": plain(F[k]["label"]), "text": plain(dated(F[k]))} for k in names if k in F]

  def order(slot, lead, eg):
    g = re.fullmatch(lead + r"([a-z]+)\s+(-?\d*\.?\d+)", S[slot], re.I)
    if S[slot] and not g:
      fix(slot, "D_SLOT", "an action and a size are needed", eg)
    return g.groups() if g else ("", "HOLD", "0")

  raw, cur, S = {}, None, {}
  for line in draft.splitlines():
    g = re.match(r"\s*\[([A-Za-z_.]+)\]\s*(.*)", line)
    if g and g.group(1).lower() in SLOTS:
      cur = g.group(1).lower()
      raw[cur] = [g.group(2)]
    elif g:
      cur = fix(g.group(1), "D_SLOT", "unknown slot name", near(g.group(1), SLOTS))
    elif cur:
      raw[cur].append(line)
  for k in SLOTS:
    S[k] = " ".join(plain(" ".join(raw.get(k, []))).split())
    if not S[k]:
      fix(k, "D_SLOT", "the slot is missing or empty", "[%s] ..." % k)

  v = S["verdict"].upper()
  if v and v not in WON:
    fix("verdict", "D_SLOT", "not BULLISH, BEARISH or NEUTRAL", "NEUTRAL")
  why = prose("why", S["why"])
  case, levels = {}, []
  for s in ("bull", "bear"):
    case[s] = dict((k, evidence(s + "." + k) if k == "evidence" else prose(s + "." + k,
      S[s + "." + k])) for _, k in ROWS)
  rows = [x for x in raw.get("levels", []) if x.strip()]
  if len(rows) > 3:
    fix("levels", "D_SLOT", "one to three rows are allowed", EG)
  for r in rows[:3]:
    p = [plain(x) for x in r.split("|", 2)] + ["", ""]
    k = p[0].strip("{} ")
    f = F.get(k)
    w = prose("levels", p[2])
    if p[1].lower() not in ("bull", "bear") or not p[2]:
      fix("levels", "D_SLOT", "a row is: fact | bull or bear | words", EG)
    elif not f or f["kind"] != "price":
      fix("levels", "D_FORMAT" if f else "D_NAME", '"%s" is not a price fact in the brief' % k,
        near(k, [x for x in F if F[x]["kind"] == "price"]))
    else:
      levels.append({"label": plain(f["label"]), "price": f["v"], "price_t": plain(f["t"]),
        "gap_t": forms(b, k)["gap"], "side": p[1].lower(), "words": w})
  _, ta, ts = order("trader", "()", "HOLD 0.0")
  word, ma, ms = order("manager", r"(approved|rejected)\s+", "approved HOLD 0.0")
  act, size = decide((ta, float(ts)), word.lower(), (ma, float(ms)))
  risk = prose("risk", S["risk"])
  if S["roles"] and S["roles"].lower() not in ROLES:
    fix("roles", "D_SLOT", "not single, subagents or mixed", "single")
  skipped = prose("skipped", S["skipped"])
  if fixes:
    return None, fixes
  # The paper desk in words: "No trade", or a size word and a direction.
  trade = "No trade"
  if act != "HOLD":
    size_word = "Small" if size < 0.34 else "Medium" if size < 0.67 else "Large"
    trade = "%s %s" % (size_word, "long" if act == "BUY" else "short")
  # The skipped roles as a list: commas part the names, and "none" is an empty list.
  names = [x.strip(" .") for x in skipped.split(",")]
  m = {
    "schema": "report.v1", "symbol": b["symbol"], "data_id": b["data_id"], "report_id": "0" * 8,
    "attempt": attempt, "figures": 0, "quantity_words": qty[0], "script": VERSION,
    "argument": {"code": v, "text": WON[v]},
    "desk": {"action": act, "size": size, "text": trade},
    "why": why, "bull": case["bull"], "bear": case["bear"], "levels": levels, "risk": risk,
    "roles": ROLES[S["roles"].lower()],
    "skipped": [x for x in names if x and x.lower() != "none"], "headlines_used": used,
    "receipt": [], "test_record": TEST_RECORD, "disclaimer": DISCLAIMER}
  # Three passes: count the figures, take the report id of the text that holds the count,
  # then print the id into the receipt.
  for step in range(3):
    m["receipt"] = [
      "Figures: %d inserted by code from the data. Quantity words: %d, not checked." % (
        m["figures"], qty[0]),
      "Data %s to %s, %s. Report %s. Attempt %d. Script %s." % (plain(b["data_id"]),
        plain(b["as_of"]), plain(b["session"]), m["report_id"], attempt, VERSION),
      "This covers sourcing and arithmetic, not judgment."]
    md = markdown(b, m)
    m["figures"] = figures(b, md)[0]
    if step == 1:
      m["report_id"] = sha(md)
  return m, []


def verify(b, md):
  """Differences between a saved report and what the brief supports; empty when it holds."""
  g = RECEIPT.search(md)
  if not g:
    return ["the receipt lines are missing or altered"]
  n, odd = figures(b, md)
  out = ["a figure that is not in the brief: " + x for x in odd]
  if n != int(g.group(1)):
    out.append("figures: the receipt says %s, the text has %d" % (g.group(1), n))
  if g.group(2, 3, 4) != (b["data_id"], b["as_of"], b["session"]):
    out.append("data: the report is from %s, the brief is %s" % (g.group(2), b["data_id"]))
  rid = sha(md[:g.start(5)] + "0" * 8 + md[g.end(5):])
  if rid != g.group(5):
    out.append("report id: the receipt says %s, the text gives %s" % (g.group(5), rid))
  return out


def kat():
  # One draft that must render to a known text and one that must be refused. Returns the
  # known answer of the first, or "" when the second is not refused.
  def f(v, t, label, kind, **more):
    return dict(more, v=v, t=t, label=label, kind=kind)
  b = {"symbol": "KAT", "name": "<Known>", "unit": "USD", "as_of": "2000-01-04",
    "fetched_at": "2000-01-05", "session": "finished", "data_id": "0a1b2c3d",
    "flags": ["prices_behind"], "notes": [],
    "headlines": [{"id": "h1", "date": "2000-01-04", "publisher": "P", "title": "T | <b>"}],
    "facts": {"last": f(10.0, "10.00", "last close", "price"),
      "sma10": f(12.5, "12.50", "average", "price"),
      "gap": f(-20.0, "-20.00%", "gap", "pct", sign="-")}}
  d = "[verdict] BULLISH\n[why] Price is {{gap|dir}} it; see {{h1}}.\n" + "".join(
    "[%s.claim] Claim.\n[%s.evidence] last, gap\n[%s.also] Half of {{sma10|label}}.\n"
    "[%s.weak] Weak.\n" % ((s,) * 4) for s in ("bull", "bear")) + (
    "[levels]\nsma10 | bull | on a close above\n[trader] BUY 0.4\n[manager] approved BUY 0.9\n"
    "[risk] Fine.\n[roles] mixed\n[skipped] none\n")
  m, bad = desk(b, d)[0], desk(b, d.replace("{{gap|dir}}", "20% below"))[1]
  md = markdown(b, m)
  ok = len(bad) == 1 and "D_DIGIT" in bad[0] and not verify(b, md)
  return sha(md + json.dumps(m), 12) if ok else ""


def read(path, raw=False):
  with open(path, "rb") as f:
    x = f.read()
  if raw:
    return x
  x = x.decode("utf-16" if x[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig", "replace")
  return x.replace(chr(8722), "-").replace(chr(160), " ").replace("\r\n", "\n")


def run(a, safe):
  # Hashed as bytes, without the one stamp line.
  t = [x.rstrip() for x in read(__file__, 1).splitlines()]
  t = b"\n".join(x for x in t if not re.fullmatch(b'SELF = "[0-9a-f]{12}"', x)).rstrip()
  if SELF not in ("0" * 12, hashlib.sha256(t).hexdigest()[:12]):
    raise Stop(3, E_SELF)
  if SELF == "0" * 12:
    say("WARN unstamped development copy", 1)
  if kat() != KAT:
    raise Stop(3, "ERROR E_KAT the renderer known-answer test failed on this Python")
  safe.append(1)
  if a == ["--version"]:
    say("trading-agents-desk render.py " + VERSION)
    return 0
  att = "1"
  if "--attempt" in a:
    i = a.index("--attempt")
    att, a = "".join(a[i + 1:i + 2]), a[:i] + a[i + 2:]
  ver = a[:1] == ["--verify"]
  if len(a) != 4 - ver or not re.fullmatch("[123]", att):
    raise Stop(4, "ERROR E_USAGE " + USAGE)
  b, text = json.loads(read(a[ver])), read(a[ver + 1])
  if not isinstance(b, dict) or b.get("schema") != "brief.v1":
    raise Stop(2, E_BRIEF)
  if ver:
    diff = verify(b, text.rstrip())
    for x in diff or ["ok"]:
      say(("DIFF " if diff else "VERIFY ") + x)
    return 1 if diff else 0
  m, fixes = desk(b, text, int(att))
  if m:
    # The resolved report is ASCII JSON on one line; page.py draws the page from it.
    md = markdown(b, m)
    for path, x in ((a[2], md), (a[3], json.dumps(m, allow_nan=False))):
      with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(x + "\n")
    fixes = [md]
  for x in fixes:
    say(x)
  return 0 if m else 1


def main(argv):
  safe = []
  try:
    return run(list(argv[1:]), safe)
  except Stop as e:
    say(e.args[1], 1)
    return e.args[0]
  except Exception as e:
    # Before the self-checks have passed, any failure means a bad copy.
    sys.stdout = None  # no retry at exit
    k = 2 if not safe else isinstance(e, OSError)
    say((E_BRIEF, "ERROR E_FILE cannot read or write a file named on the command line",
      E_SELF)[k], 1)
    return (2, 4, 3)[k]


if __name__ == "__main__":
  sys.exit(main(sys.argv))
