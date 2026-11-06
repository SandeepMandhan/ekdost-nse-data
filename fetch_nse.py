"""EkDost NSE mirror: downloads NSE's official daily files every evening and keeps them in this repository,
so the EkDost app (on Google's servers, which NSE blocks) can read them from GitHub.

  bhav/sec_bhavdata_full_DDMMYYYY.csv  NSE "full bhavcopy and security deliverable data" (only share series EQ, BE, BZ, SM, ST;
                                       columns unchanged: OPEN/HIGH/LOW/CLOSE, AVG_PRICE = VWAP, TTL_TRD_QNTY, DELIV_QTY, DELIV_PER)
  bhav/index.json                      which dates are here, and which weekdays were market holidays
  deals/deals.csv                      bulk and block deals, one year (history from NSE's report, then day by day)
  insider/insider.csv                  promoter & insider trades (SEBI PIT disclosures), one year
  announcements/ann.csv                corporate announcements, last 45 days
  fiidii/fiidii.csv                    FII / DII cash-market buying and selling, built up day by day
  meta/corpactions.csv                 NSE splits, bonus issues and consolidations since 2012 (symbol, ex-date, subject)
  meta/changes.json                    NSE symbol changes and company name changes (from NSE's symbolchange.csv / namechange.csv)
  fiidii/participant_oi.csv            F&O open positions of FII, DII, Pro and Client (index / stock futures and options), two years
  status.json                          which parts NSE allowed on the last run
Nothing is changed or estimated: rows are copied from NSE's files and reports as published.
"""
import csv, datetime as dt, io, json, os, sys, time
STATUS = {}
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
KEEP = {"EQ", "BE", "BZ", "SM", "ST"}
DAYS = int(os.environ.get("DAYS", "740"))   # two years: charts and technicals need 200+ days of history
NOW = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=5, minutes=30)   # India time
TODAY = NOW.date()

s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"})
try:
    s.get("https://www.nseindia.com/", timeout=20)   # cookies, if NSE wants them
except Exception as e:
    print("homepage:", e)

def get(url):
    for attempt in range(4):
        try:
            r = s.get(url, timeout=40)
            if r.status_code in (200, 404):
                return r
            print(url, "->", r.status_code)
            if r.status_code in (401, 403):          # session expired: get fresh cookies from the home page
                try:
                    s.get("https://www.nseindia.com/", timeout=20)
                except Exception:
                    pass
        except Exception as e:
            print(url, "error", e)
        time.sleep(3 + attempt * 5)
    return None
def months_back(n):
    """Calendar months (first day, last day) from n months ago to this month, as (key, start, end)."""
    out, y, m = [], TODAY.year, TODAY.month
    for _ in range(n + 1):
        first = dt.date(y, m, 1)
        nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
        out.append(("%04d-%02d" % (y, m), first, min(nxt - dt.timedelta(days=1), TODAY)))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out
def load_cov(path):
    try:
        return json.load(open(path))
    except Exception:
        return {}

# ---------- daily price, volume & delivery ----------
os.makedirs("bhav", exist_ok=True)
idx_path = "bhav/index.json"
try:
    idx = json.load(open(idx_path))
except Exception:
    idx = {"dates": [], "holidays": []}
have, hol = set(idx.get("dates", [])), set(idx.get("holidays", []))
saved = failed = 0
for i in range(DAYS):
    d = TODAY - dt.timedelta(days=i)
    iso = d.isoformat()
    if d.weekday() > 4 or iso in have or iso in hol:
        continue
    if d == TODAY and NOW.hour < 18:
        continue                                  # today's file comes out in the evening
    name = "sec_bhavdata_full_%s.csv" % d.strftime("%d%m%Y")
    r = get("https://nsearchives.nseindia.com/products/content/" + name)
    if r is None:
        failed += 1
        continue
    if r.status_code == 404:
        if (TODAY - d).days > 3:
            hol.add(iso)                          # NSE has no file: market holiday
        continue
    text = r.text.lstrip("﻿")
    if not text.startswith("SYMBOL"):
        print(iso, "unexpected content"); failed += 1
        continue
    lines = text.splitlines()
    try:
        fdate = dt.datetime.strptime(lines[1].split(",")[2].strip().title(), "%d-%b-%Y").date()
    except Exception:
        fdate = d
    if fdate != d:                                # a market holiday: NSE serves the previous day's file under this name
        hol.add(iso); continue
    rows = [lines[0]] + [l for l in lines[1:] if len(l.split(",")) > 2 and l.split(",")[1].strip() in KEEP]
    with open("bhav/" + name, "w", newline="\n") as f:
        f.write("\n".join(rows) + "\n")
    have.add(iso); saved += 1
    time.sleep(0.8)
for iso in sorted(have):                          # tidy up: holiday copies saved by earlier versions
    p_ = "bhav/sec_bhavdata_full_%s.csv" % dt.date.fromisoformat(iso).strftime("%d%m%Y")
    try:
        with open(p_) as fh:
            fh.readline(); first = fh.readline()
        if dt.datetime.strptime(first.split(",")[2].strip().title(), "%d-%b-%Y").date().isoformat() != iso:
            os.remove(p_); have.discard(iso); hol.add(iso)
    except Exception:
        pass
cut = (TODAY - dt.timedelta(days=DAYS + 10)).isoformat()
for fn in os.listdir("bhav"):                     # keep about a year
    if fn.startswith("sec_bhavdata_full_"):
        p = fn[18:26]; iso = "%s-%s-%s" % (p[4:8], p[2:4], p[0:2])
        if iso < cut:
            os.remove("bhav/" + fn); have.discard(iso)
json.dump({"dates": sorted(have), "holidays": sorted(h for h in hol if h >= cut), "updated": NOW.strftime("%Y-%m-%d %H:%M IST")},
          open(idx_path, "w"), indent=0)
print("daily files saved:", saved, "failed:", failed, "total:", len(have))
STATUS["daily"] = "ok" if have else "blocked"

# ---------- bulk & block deals ----------
os.makedirs("deals", exist_ok=True)
dpath = "deals/deals.csv"
deals = {}
if os.path.exists(dpath):
    for row in csv.DictReader(open(dpath)):
        deals[(row["date"], row["kind"], row["symbol"], row["client"], row["side"], row["qty"], row["price"])] = row

def iso_of(x):
    x = str(x or "").strip().replace("T", " ").split(" ")[0].strip()
    for f in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(x.title(), f).date().isoformat()
        except Exception:
            pass
    return ""

for kind, url in (("Bulk", "https://nsearchives.nseindia.com/content/equities/bulk.csv"), ("Block", "https://nsearchives.nseindia.com/content/equities/block.csv")):
    r = get(url)
    if r is None or r.status_code != 200:
        print(kind, "deals: not available"); continue
    rd = csv.reader(io.StringIO(r.text.lstrip("﻿")))
    head = [h.strip().lower() for h in next(rd, [])]
    def col(*names):
        for n in names:
            for i, h in enumerate(head):
                if h.startswith(n):
                    return i
        return -1
    iD, iS, iC, iB, iQ, iP = col("date"), col("symbol"), col("client"), col("buy"), col("quantity"), col("trade price", "price")
    n = 0
    for f in rd:
        if len(f) <= max(iD, iS, iC, iB, iQ, iP) or min(iD, iS, iC, iB, iQ, iP) < 0:
            continue
        d = iso_of(f[iD])
        if not d:
            continue
        row = {"date": d, "kind": kind, "symbol": f[iS].strip().upper(), "client": f[iC].strip().replace(",", ";"),
               "side": "SELL" if f[iB].strip().upper().startswith("S") else "BUY",
               "qty": f[iQ].replace(",", "").strip(), "price": f[iP].replace(",", "").strip()}
        deals[tuple(row[k] for k in ("date", "kind", "symbol", "client", "side", "qty", "price"))] = row; n += 1
    print(kind, "deals read:", n)
    STATUS["deals_today"] = "ok"

# ---------- NSE website reports (need a browser-like session) ----------
def api(path):
    r = get("https://www.nseindia.com/api/" + path)
    if r is None or r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None
def rows_of(j):
    return j if isinstance(j, list) else (j or {}).get("data", []) if isinstance(j, dict) else []
def pick(o, *names):
    for n in names:
        v = o.get(n)
        if v not in (None, ""):
            return v
    return ""
def num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return ""
def ddmmyyyy(d):
    return d.strftime("%d-%m-%Y")

# deals history: a year from NSE's bulk / block deals report, month by month. A month is remembered once NSE answered for it,
# so a month that failed is simply tried again next run; the current and previous month are always refreshed.
cov_path = "deals/coverage.json"; cov = load_cov(cov_path); recent = {k for k, a, b in months_back(1)}
for kind, opt in (("Bulk", "bulk_deals"), ("Block", "block_deals")):
    for key, a, b in months_back(12):
        ck = kind + ":" + key
        if cov.get(ck) and key not in recent:
            continue
        j = api("historicalOR/bulk-block-short-deals?optionType=%s&from=%s&to=%s" % (opt, ddmmyyyy(a), ddmmyyyy(b)))
        if j is None:
            continue
        cov[ck] = NOW.strftime("%Y-%m-%d")
        for o in rows_of(j):
            d = iso_of(str(pick(o, "BD_DT_DATE", "date", "mTIMESTAMP")))
            if not d:
                continue
            row = {"date": d, "kind": kind, "symbol": str(pick(o, "BD_SYMBOL", "symbol")).strip().upper(), "client": str(pick(o, "BD_CLIENT_NAME", "clientName")).strip().replace(",", ";"),
                   "side": "SELL" if str(pick(o, "BD_BUY_SELL", "buySell")).strip().upper().startswith("S") else "BUY",
                   "qty": str(num(pick(o, "BD_QTY_TRD", "qty"))).replace(".0", ""), "price": str(num(pick(o, "BD_TP_WATP", "watp", "price")))}
            deals[tuple(row[k] for k in ("date", "kind", "symbol", "client", "side", "qty", "price"))] = row
        time.sleep(1.2)
json.dump(cov, open(cov_path, "w"), indent=0)
missing = [k for kind in ("Bulk", "Block") for k, a, b in months_back(12) if not cov.get(kind + ":" + k)]
STATUS["deals_history"] = "ok" if not missing else ("partial" if len(missing) < 26 else "blocked")
print("deals history months missing:", missing)

# insider trades (SEBI PIT): a year, fetched in half-month windows. A window is remembered once NSE answered for it, so any
# window that failed is retried on the next run; the current and previous month are always refreshed.
os.makedirs("insider", exist_ok=True)
ipath = "insider/insider.csv"; ins = {}
if os.path.exists(ipath):
    for row in csv.DictReader(open(ipath)):
        ins[tuple(row.values())] = row
icov_path = "insider/coverage2.json"; icov = load_cov(icov_path)   # (v2: re-checks every window once); recent = {k for k, a, b in months_back(1)}
for key, a, b in months_back(12):
    halves = [(a, min(a + dt.timedelta(days=14), b))] + ([(a + dt.timedelta(days=15), b)] if a + dt.timedelta(days=15) <= b else [])
    for n_, (x, y) in enumerate(halves):
        ck = "%s-%d" % (key, n_)
        if icov.get(ck) and key not in recent:
            continue
        j = api("corporates-pit?index=equities&from_date=%s&to_date=%s" % (ddmmyyyy(x), ddmmyyyy(y)))
        if j is None:
            continue
        raw, parsed, dates_seen = rows_of(j), 0, set()
        for o in raw:
            d = iso_of(pick(o, "date", "intimDt", "acqtoDt", "acqfromDt"))
            sym = str(pick(o, "symbol")).strip().upper()
            if not d or not sym:
                continue
            typ = str(pick(o, "tdpTransactionType", "acqMode", "secAcqType"))
            row = {"date": d, "symbol": sym, "who": str(pick(o, "acqName", "name")).replace(",", ";").strip(), "role": str(pick(o, "personCategory", "category")).replace(",", ";"),
                   "side": "SELL" if any(w in typ.lower() for w in ("sell", "dispos", "revok", "pledge")) else "BUY", "type": typ.replace(",", ";"),
                   "qty": num(pick(o, "secAcq", "secAcquired")), "value": num(pick(o, "secVal", "value")), "after": num(pick(o, "afterAcqSharesPer", "secAcqPer"))}
            ins[tuple(str(row[k]) for k in row)] = row
            parsed += 1; dates_seen.add(d)
        print("insider %s..%s: NSE sent %d rows, read %d (%s .. %s)" % (x, y, len(raw), parsed, min(dates_seen) if dates_seen else "-", max(dates_seen) if dates_seen else "-"))
        if raw and not parsed:
            print("  sample row:", json.dumps(raw[0])[:600])
        elif raw or (TODAY - y).days > 3:
            icov[ck] = NOW.strftime("%Y-%m-%d")
        time.sleep(1.2)
json.dump(icov, open(icov_path, "w"), indent=0)
imiss = [k for k, a, b in months_back(12) if not icov.get(k + "-0")]
if os.path.exists("insider/coverage.json"):
    os.remove("insider/coverage.json")
got_i = not imiss
print("insider windows missing:", imiss)
STATUS["insider"] = "ok" if got_i else ("partial" if len(imiss) < 12 else "blocked")
cut_i = (TODAY - dt.timedelta(days=366)).isoformat()
with open(ipath, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date", "symbol", "who", "role", "side", "type", "qty", "value", "after"]); w.writeheader()
    for row in sorted(ins.values(), key=lambda r: (r["date"], r["symbol"]), reverse=True):
        if row["date"] >= cut_i:
            w.writerow(row)
print("insider trades kept:", len(ins), STATUS["insider"])

# corporate announcements: last 45 days
os.makedirs("announcements", exist_ok=True)
apath = "announcements/ann.csv"; ann = {}
if os.path.exists(apath):
    for row in csv.DictReader(open(apath)):
        ann[(row["date"], row["symbol"], row["subject"], row["link"])] = row
try:
    last_ok = json.load(open("status.json")).get("parts", {}).get("announcements") == "ok"
except Exception:
    last_ok = False
start, got_a = TODAY - dt.timedelta(days=(10 if ann and last_ok else 45)), False
while start <= TODAY:
    end = min(start + dt.timedelta(days=7), TODAY)
    j = api("corporate-announcements?index=equities&from_date=%s&to_date=%s" % (ddmmyyyy(start), ddmmyyyy(end)))
    for o in rows_of(j):
        d = iso_of(str(pick(o, "an_dt", "sort_date", "dt"))[:11])
        sym = str(pick(o, "symbol")).strip().upper()
        if not d or not sym:
            continue
        got_a = True
        row = {"date": d, "symbol": sym, "subject": " ".join(str(pick(o, "desc", "subject")).split())[:160],
               "text": " ".join(str(pick(o, "attchmntText")).split())[:300], "link": str(pick(o, "attchmntFile"))}
        ann[(row["date"], row["symbol"], row["subject"], row["link"])] = row
    start = end + dt.timedelta(days=1); time.sleep(1)
STATUS["announcements"] = "ok" if got_a else "blocked"
cut_a = (TODAY - dt.timedelta(days=45)).isoformat()
with open(apath, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date", "symbol", "subject", "text", "link"]); w.writeheader()
    for row in sorted(ann.values(), key=lambda r: (r["date"], r["symbol"]), reverse=True):
        if row["date"] >= cut_a:
            w.writerow(row)
print("announcements kept:", len(ann), STATUS["announcements"])

# FII / DII (whole market, ₹ crore): NSE publishes only the latest day, so it builds up day by day
os.makedirs("fiidii", exist_ok=True)
fpath = "fiidii/fiidii.csv"; fd = {}
if os.path.exists(fpath):
    for row in csv.DictReader(open(fpath)):
        fd[(row["date"], row["who"])] = row
got_f = False
for o in rows_of(api("fiidiiTradeReact")):
    d = iso_of(str(pick(o, "date")))
    if not d:
        continue
    got_f = True
    who = "FII / FPI" if any(w in str(o.get("category", "")).lower() for w in ("fii", "fpi")) else "DII"
    fd[(d, who)] = {"date": d, "who": who, "buy": num(o.get("buyValue")), "sell": num(o.get("sellValue")), "net": num(o.get("netValue"))}
STATUS["fiidii"] = "ok" if got_f else "blocked"
with open(fpath, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date", "who", "buy", "sell", "net"]); w.writeheader()
    for row in sorted(fd.values(), key=lambda r: (r["date"], r["who"]), reverse=True)[:800]:
        w.writerow(row)
print("FII/DII days:", len(fd) // 2, STATUS["fiidii"])

# ---------- F&O positions by participant: FII, DII, Pro, Client (NSE clearing archive, one small file per day) ----------
PFIELDS = ["fut_idx_long", "fut_idx_short", "fut_stk_long", "fut_stk_short", "opt_idx_call_long", "opt_idx_put_long", "opt_idx_call_short", "opt_idx_put_short",
           "opt_stk_call_long", "opt_stk_put_long", "opt_stk_call_short", "opt_stk_put_short", "total_long", "total_short"]
PCOLS = ["future index long", "future index short", "future stock long", "future stock short", "option index call long", "option index put long", "option index call short",
         "option index put short", "option stock call long", "option stock put long", "option stock call short", "option stock put short", "total long contracts", "total short contracts"]
ppath = "fiidii/participant_oi.csv"; P = {}
if os.path.exists(ppath):
    for row in csv.DictReader(open(ppath)):
        P[(row["date"], row["type"])] = row
done_p = {d for (d, t) in P}
got_p = 0
for iso in sorted(have, reverse=True):
    if iso in done_p or iso < (TODAY - dt.timedelta(days=DAYS)).isoformat():
        continue
    dd = dt.date.fromisoformat(iso).strftime("%d%m%Y"); text = None
    for u in ("https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_%s.csv" % dd, "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_%s_b.csv" % dd):
        r = get(u)
        if r is not None and r.status_code == 200 and "client type" in r.text.lower():
            text = r.text.lstrip("\ufeff"); break
    if not text:
        continue
    lines = [l for l in text.splitlines() if l.strip()]
    import re as _re
    m_ = _re.search(r"as on ([A-Za-z]{3}) (\d{1,2}),? (\d{4})", text)
    if m_:
        try:
            if dt.datetime.strptime("%s %s %s" % (m_.group(2), m_.group(1).title(), m_.group(3)), "%d %b %Y").date().isoformat() != iso:
                continue                          # holiday copy of the previous day
        except Exception:
            pass
    hi = next((i for i, l in enumerate(lines) if "client type" in l.lower()), None)
    if hi is None:
        continue
    head = [h.strip().lower() for h in next(csv.reader([lines[hi]]))]
    for f in csv.reader(lines[hi + 1:]):
        if not f or not f[0].strip():
            continue
        typ = f[0].strip().upper()
        if typ not in ("CLIENT", "DII", "FII", "PRO", "TOTAL"):
            continue
        row = {"date": iso, "type": typ.title() if typ in ("CLIENT", "PRO", "TOTAL") else typ}
        for k, c in zip(PFIELDS, PCOLS):
            j = head.index(c) if c in head else -1
            row[k] = num(f[j]) if 0 <= j < len(f) else ""
        P[(iso, row["type"])] = row
    got_p += 1
    time.sleep(0.5)
os.makedirs("fiidii", exist_ok=True)
with open(ppath, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date", "type"] + PFIELDS); w.writeheader()
    for row in sorted(P.values(), key=lambda r: (r["date"], r["type"]), reverse=True):
        w.writerow(row)
STATUS["participant_oi"] = "ok" if P else "blocked"
print("participant OI days added:", got_p, "total days:", len({d for (d, t) in P}))

keep_from = (TODAY - dt.timedelta(days=400)).isoformat()
with open(dpath, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date", "kind", "symbol", "client", "side", "qty", "price"]); w.writeheader()
    for row in sorted(deals.values(), key=lambda r: (r["date"], r["symbol"]), reverse=True):
        if row["date"] >= keep_from:
            w.writerow(row)
print("deals kept:", len(deals))

# ---- symbol and company-name changes (NSE's own lists), so the app can bring old trades up to the current symbol ----
def nse_date(x):
    x = (x or "").strip()
    for f in ("%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return dt.datetime.strptime(x.title() if "%b" in f else x, f).date().isoformat()
        except Exception:
            pass
    return ""
def nse_rows(url, want):
    r = get(url)
    if r is None or r.status_code != 200 or len(r.content) < 50:
        return None
    rows = [[c.strip() for c in f] for f in csv.reader(io.StringIO(r.content.decode("utf-8", "ignore"))) if f and any(c.strip() for c in f)]
    if not rows:
        return None
    head = [h.upper() for h in rows[0]]
    if any("SYMBOL" in h or h.startswith(("SM_", "NCH_")) for h in head):
        idx = []
        for keys in want:
            j = next((i for i, h in enumerate(head) if any(k in h for k in keys)), -1)
            idx.append(j)
        rows = rows[1:]
    else:
        idx = list(range(len(want)))
    return [[(f[j] if 0 <= j < len(f) else "") for j in idx] for f in rows]
os.makedirs("meta", exist_ok=True)
mpath = "meta/changes.json"
old = {}
try:
    old = json.load(open(mpath))
except Exception:
    pass
sc = nse_rows("https://nsearchives.nseindia.com/content/equities/symbolchange.csv", [("NAME",), ("KEY_SYMBOL", "OLD"), ("NEW_SYMBOL", "NEW"), ("APPLICABLE", "DATE", "FROM")])
nc = nse_rows("https://nsearchives.nseindia.com/content/equities/namechange.csv", [("SYMBOL",), ("PREV",), ("NEW",), ("DT", "DATE")])
meta = {"updated": NOW.strftime("%Y-%m-%d %H:%M IST"),
        "symbols": [[o.upper(), n.upper(), nse_date(d), nm] for nm, o, n, d in sc if o and n and o.upper() != n.upper()] if sc else old.get("symbols", []),
        "names": [[sy.upper(), p, n, nse_date(d)] for sy, p, n, d in nc if sy and n] if nc else old.get("names", [])}
json.dump(meta, open(mpath, "w"), separators=(",", ":"))
STATUS["symbol_changes"] = "ok" if sc else ("kept old list" if old else "blocked")
print("symbol changes:", len(meta["symbols"]), "name changes:", len(meta["names"]))

# ---- corporate actions: splits, bonus issues and consolidations (NSE), from 2012, so old trades can be adjusted ----
import re
capath, cacov = "meta/corpactions.csv", "meta/corpactions_cov.json"
CA = {}
try:
    for row in csv.DictReader(open(capath)):
        CA[(row["symbol"], row["exdate"], row["subject"])] = row
except Exception:
    pass
cov2 = load_cov(cacov)
got_ca = 0
for y in range(2012, TODAY.year + 1):
    key = str(y)
    if cov2.get(key) and y < TODAY.year - 1:
        continue
    j = api("corporates-corporateActions?index=equities&from_date=01-01-%d&to_date=31-12-%d" % (y, y))
    if j is None:
        continue
    for o in rows_of(j):
        subj = str(pick(o, "subject", "purpose")).strip()
        if not re.search(r"bonus|split|sub-?division|consolidat", subj, re.I):
            continue
        sym = str(pick(o, "symbol")).strip().upper(); ex = nse_date(str(pick(o, "exDate", "exdate", "ex_date")))
        if sym and ex:
            CA[(sym, ex, subj)] = {"symbol": sym, "exdate": ex, "subject": subj, "series": str(pick(o, "series")).strip()}
    cov2[key] = 1; got_ca += 1
    time.sleep(0.6)
with open(capath, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["symbol", "exdate", "subject", "series"]); w.writeheader()
    for row in sorted(CA.values(), key=lambda r: (r["symbol"], r["exdate"])):
        w.writerow(row)
json.dump(cov2, open(cacov, "w"))
STATUS["corp_actions"] = "ok" if got_ca else ("kept old list" if CA else "blocked")
print("corporate actions (splits / bonus):", len(CA), "years read this run:", got_ca)
json.dump({"updated": NOW.strftime("%Y-%m-%d %H:%M IST"), "parts": STATUS}, open("status.json", "w"), indent=1)
print("status:", STATUS)
