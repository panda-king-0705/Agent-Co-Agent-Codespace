"""Check H1 candle files in a folder: coverage, order, weekend bars, in-week gaps, spreads, OHLC sanity.

Usage:
    python -m scripts.check_candles data
"""
import csv, sys, statistics
from datetime import datetime, timedelta
from pathlib import Path

H = timedelta(hours=1)


def weekend(t):
    # FX is shut from Friday ~21:00/22:00 UTC to Sunday ~21:00/22:00 UTC. Flag only clear weekend bars.
    wd = t.weekday()
    return wd == 5 or (wd == 4 and t.hour >= 22) or (wd == 6 and t.hour < 20)


def spans_weekend(a, b):
    return (b - a) >= timedelta(hours=40) and a.weekday() in (4,) and b.weekday() in (6, 0)


rows_out = []
for path in sorted(Path(sys.argv[1]).glob("*_H1.csv")):
    with open(path, newline="") as f:
        r = list(csv.DictReader(f))
    ts = [datetime.fromisoformat(x["time"].replace("Z", "+00:00")) for x in r]
    sp = [float(x["spread_pips"]) for x in r]
    bad_order = sum(1 for a, b in zip(ts, ts[1:]) if b <= a)
    wk = sum(map(weekend, ts))
    gaps = [(a, b) for a, b in zip(ts, ts[1:]) if b - a > H and not spans_weekend(a, b)]
    long_gaps = [(a, b) for a, b in gaps if b - a > timedelta(hours=6)]
    ohlc_bad = sum(1 for x in r if not (float(x["low"]) <= min(float(x["open"]), float(x["close"])) <= max(float(x["open"]), float(x["close"])) <= float(x["high"])))
    nonpos = sum(s <= 0 for s in sp)
    q = statistics.quantiles(sp, n=20)
    rows_out.append((path.stem.replace("_H1", ""), len(r), ts[0].date(), ts[-1].strftime("%Y-%m-%d %H:%M"), bad_order, wk, len(gaps), len(long_gaps),
                     max(((b - a) for a, b in gaps), default=H), ohlc_bad, nonpos, statistics.median(sp), q[18], max(sp),
                     [(a.strftime("%Y-%m-%d %H:%M"), int((b - a) / H)) for a, b in sorted(long_gaps, key=lambda g: g[0] - g[1])[:3]]))

print(f"{'pair':8}{'rows':>7} {'first':10} {'last':16}{'ord':>4}{'wkend':>6}{'gaps':>6}{'>6h':>5}{'maxgap':>8}{'ohlc':>5}{'sp<=0':>6}{'sp_med':>7}{'sp_p95':>7}{'sp_max':>8}")
for p, n, a, b, o, w, g, lg, mg, oh, np_, med, p95, mx, worst in rows_out:
    print(f"{p:8}{n:>7} {str(a):10} {b:16}{o:>4}{w:>6}{g:>6}{lg:>5}{int(mg / H):>7}h{oh:>5}{np_:>6}{med:>7.1f}{p95:>7.1f}{mx:>8.1f}")
print("\nLongest in-week gaps per pair (start UTC, hours):")
for p, *_, worst in rows_out:
    if worst:
        print(f"  {p}: {worst}")
