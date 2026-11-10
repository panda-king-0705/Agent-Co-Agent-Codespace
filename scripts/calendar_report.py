"""Summarise the economic calendar: release timing quality and event-to-category coverage.

Usage:
    python -m scripts.calendar_report data/calendar/forex_factory_cache.csv [--impact high,medium] [--top 15]

Prints, per currency, how many releases fall in each of the six score categories, which
categorised events have a forecast and a usual-effect direction (both are needed to score a
beat or miss), and the most frequent events that match no category. Event names and counts
only; no values are printed.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict

from carry.calendar import CATEGORY_PATTERNS, CURRENCIES, load_calendar

CATEGORIES = [name for name, _ in CATEGORY_PATTERNS]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path")
    parser.add_argument("--impact", default="high,medium", help="comma list of impacts to include")
    parser.add_argument("--top", type=int, default=15, help="unmapped events to list per currency")
    args = parser.parse_args(argv)
    impacts = set(args.impact.split(","))

    releases = load_calendar(args.path)
    print(f"{len(releases)} releases {releases[0].time:%Y-%m-%d} to {releases[-1].time:%Y-%m-%d}")
    for impact in ("high", "medium", "low"):
        sources = Counter(r.time_source for r in releases if r.impact == impact)
        total = sum(sources.values())
        print(f"  {impact:<6} {total:>6}  " + "  ".join(f"{s} {sources[s] / total:.0%}" for s in ("exact", "imputed", "unknown")))

    chosen = [r for r in releases if r.impact in impacts]
    print(f"\nReleases with impact in {sorted(impacts)}, by category (scoreable = has forecast and direction):")
    print(f"{'':5}" + "".join(f"{c:>17}" for c in CATEGORIES) + f"{'unmapped':>10}")
    for cur in CURRENCIES:
        mine = [r for r in chosen if r.currency == cur]
        cells = []
        for c in CATEGORIES:
            in_cat = [r for r in mine if r.category == c]
            scoreable = sum(r.surprise() is not None for r in in_cat)
            cells.append(f"{len(in_cat):>8} ({scoreable:>5})")
        print(f"{cur:5}" + "".join(f"{cell:>17}" for cell in cells) + f"{sum(r.category is None for r in mine):>10}")

    print("\nEvents per category (all years, chosen impacts):")
    events = defaultdict(Counter)
    for r in chosen:
        events[(r.currency, r.category)][r.event] += 1
    for cur in CURRENCIES:
        print(f"\n{cur}")
        for c in CATEGORIES:
            names = ", ".join(f"{e} ({n})" for e, n in events[(cur, c)].most_common())
            print(f"  {c:<11} {names or '-'}")
        unmapped = events[(cur, None)].most_common(args.top)
        print(f"  {'UNMAPPED':<11} " + ", ".join(f"{e} ({n})" for e, n in unmapped))


if __name__ == "__main__":
    main()
