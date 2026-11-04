"""EkDost NSE mirror: downloads NSE's official daily files every evening and keeps them in this repository,
so the EkDost app (on Google's servers, which NSE blocks) can read them from GitHub.

  bhav/sec_bhavdata_full_DDMMYYYY.csv  NSE "full bhavcopy and security deliverable data" (only share series EQ, BE, BZ, SM, ST;
                                       columns unchanged: OPEN/HIGH/LOW/CLOSE, AVG_PRICE = VWAP, TTL_TRD_QNTY, DELIV_QTY, DELIV_PER)
  bhav/index.json                      which dates are here, and which weekdays were market holidays
  deals/deals.csv                      bulk and block deals, one year (history from NSE's report, then day by day)
  insider/insider.csv                  promoter & insider trades (SEBI PIT disclosures), one year
  announcements/ann.csv                corporate announcements, last 45 days
  fiidii/fiidii.csv                    FII / DII cash-market buying and selling, built up day by day
  status.json                          which parts NSE allowed on the last run
Nothing is changed or estimated: rows are copied from NSE's files and reports as published.
"""
import csv, datetime as dt, io, json, os, sys, time
STATUS = {}
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
KEEP = {"EQ", "BE", "BZ", "SM", "ST"}
DAYS = int(os.environ.get("DAYS", "370"))
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
    rows = [lines[0]] + [l for l in lines[1:] if len(l.split(",")) > 2 and l.split(",")[1].strip() in KEEP]
    with open("bhav/" + name, "w", newline="\n") as f:
        f.write("\n".join(rows) + "\n")
    have.add(iso); saved += 1
    time.sleep(0.8)
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
    for f in ("%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(x.strip().title(), f).date().isoformat()
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
icov_path = "insider/coverage.json"; icov = load_cov(icov_path); recent = {k for k, a, b in months_back(1)}
for key, a, b in months_back(12):
    halves = [(a, min(a + dt.timedelta(days=14), b))] + ([(a + dt.timedelta(days=15), b)] if a + dt.timedelta(days=15) <= b else [])
    for n_, (x, y) in enumerate(halves):
        ck = "%s-%d" % (key, n_)
        if icov.get(ck) and key not in recent:
            continue
        j = api("corporates-pit?index=equities&from_date=%s&to_date=%s" % (ddmmyyyy(x), ddmmyyyy(y)))
        if j is None:
            continue
        icov[ck] = NOW.strftime("%Y-%m-%d")
        for o in rows_of(j):
            d = iso_of(str(pick(o, "date", "intimDt", "acqtoDt", "acqfromDt"))[:11])
            sym = str(pick(o, "symbol")).strip().upper()
            if not d or not sym:
                continue
            typ = str(pick(o, "tdpTransactionType", "acqMode", "secAcqType"))
            row = {"date": d, "symbol": sym, "who": str(pick(o, "acqName", "name")).replace(",", ";").strip(), "role": str(pick(o, "personCategory", "category")).replace(",", ";"),
                   "side": "SELL" if any(w in typ.lower() for w in ("sell", "dispos", "revok", "pledge")) else "BUY", "type": typ.replace(",", ";"),
                   "qty": num(pick(o, "secAcq", "secAcquired")), "value": num(pick(o, "secVal", "value")), "after": num(pick(o, "afterAcqSharesPer", "secAcqPer"))}
            ins[tuple(str(row[k]) for k in row)] = row
        time.sleep(1.2)
json.dump(icov, open(icov_path, "w"), indent=0)
imiss = [k for k, a, b in months_back(12) if not icov.get(k + "-0")]
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

keep_from = (TODAY - dt.timedelta(days=400)).isoformat()
with open(dpath, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date", "kind", "symbol", "client", "side", "qty", "price"]); w.writeheader()
    for row in sorted(deals.values(), key=lambda r: (r["date"], r["symbol"]), reverse=True):
        if row["date"] >= keep_from:
            w.writerow(row)
print("deals kept:", len(deals))
json.dump({"updated": NOW.strftime("%Y-%m-%d %H:%M IST"), "parts": STATUS}, open("status.json", "w"), indent=1)
print("status:", STATUS)
