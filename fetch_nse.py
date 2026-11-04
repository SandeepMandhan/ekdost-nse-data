"""EkDost NSE mirror: downloads NSE's official daily files every evening and keeps them in this repository,
so the EkDost app (on Google's servers, which NSE blocks) can read them from GitHub.

  bhav/sec_bhavdata_full_DDMMYYYY.csv  NSE "full bhavcopy and security deliverable data" (only share series EQ, BE, BZ, SM, ST;
                                       columns unchanged: OPEN/HIGH/LOW/CLOSE, AVG_PRICE = VWAP, TTL_TRD_QNTY, DELIV_QTY, DELIV_PER)
  bhav/index.json                      which dates are here, and which weekdays were market holidays
  deals/deals.csv                      bulk and block deals, one year, built up day by day
Nothing is changed or estimated: rows are copied from NSE's files as published.
"""
import csv, datetime as dt, io, json, os, sys, time
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
    for attempt in range(3):
        try:
            r = s.get(url, timeout=40)
            if r.status_code in (200, 404):
                return r
            print(url, "->", r.status_code)
        except Exception as e:
            print(url, "error", e)
        time.sleep(3 + attempt * 4)
    return None

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
keep_from = (TODAY - dt.timedelta(days=400)).isoformat()
with open(dpath, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date", "kind", "symbol", "client", "side", "qty", "price"]); w.writeheader()
    for row in sorted(deals.values(), key=lambda r: (r["date"], r["symbol"]), reverse=True):
        if row["date"] >= keep_from:
            w.writerow(row)
print("deals kept:", len(deals))
