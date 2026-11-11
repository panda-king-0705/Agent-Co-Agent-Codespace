# Agent-Co-Agent-Codespace

## Tests

Every push and pull request runs the test suite on GitHub Actions (`.github/workflows/tests.yml`):
Python 3.12, `python -m pytest -q`. Read the result on the commit or pull request — the last lines of the
"Run the tests" step say how many passed and list any that failed.

To run them locally: `python -m pip install pytest && python -m pytest -q`.

## Economic calendar (carry strategy)

The carry strategy scores currencies from the Forex Factory calendar dataset on Hugging Face
(`Tropstan/Forex_Factory_Calendar`, research use only; keep it in `data/`, never commit it):

```
curl -L -o data/calendar/forex_factory_cache.csv https://huggingface.co/datasets/Tropstan/Forex_Factory_Calendar/resolve/main/forex_factory_cache.csv
python -m scripts.calendar_report data/calendar/forex_factory_cache.csv --impact high,medium,low
```

`carry.calendar.load_calendar` returns UTC-timed releases with parsed values, a score category and
the event's direction. About a third of rows in the file have no release time; the loader restores
them from each event's usual local release time and marks them `imputed` (see the module docstring).

## OANDA credentials

Set `OANDA_API_TOKEN` and `OANDA_ACCOUNT_ID` in the shell, or copy `.env.example` to `.env` and fill
them in. `.env` is ignored by git (this repository is public; never commit it). Shell variables win
over `.env`, and `.env` can't switch on live mode.

## Macro data (carry strategy)

```
python -m scripts.fetch_macro
python -m scripts.fetch_candles --instruments SPX500_USD,WTICO_USD,XCU_USD --granularity D --start 2009-01-01 --end 2026-11-01 --out-dir data/macro
```

FRED (VIX, US 10y-2y, US 2y), BIS central bank policy rates and CFTC Commitments of Traders, plus
S&P 500, WTI and copper daily CFD candles from OANDA, all into `data/macro/`. `carry.macro` loads
them; every value carries `known_from`, the first time a backtest may use it.
`python -m scripts.check_candles data` checks the H1 price files.

## Official release history (carry strategy)

```
python -m scripts.fetch_alfred [SERIES_ID ...]
```

Downloads every revision of FRED series from ALFRED into `data/macro/alfred/` and reports how much
release history each has. Needs a free FRED API key in `FRED_API_KEY` (environment or `.env`).
`carry.alfred` rebuilds each release as first published, computed from that day's vintage, for
the "beat the previous release" fundamental score.

## Fundamental-score indicators (carry strategy)

```
python -m scripts.fetch_indicators
```

One official series per category (inflation, employment, GDP, trade, business, sentiment) for
each of the 8 economies, listed in `carry.indicators.CATALOG`. US series are exact first releases
from ALFRED. The others are current revised values from FRED, BIS, Eurostat and the Bank of Canada,
each treated as released a fixed, conservative number of days after its period ends. Revised
figures are a little cleaner than what traders saw on the day, so results for those economies can
look somewhat better than reality. Needs `FRED_API_KEY`.

## 2-year government bond yields (carry strategy)

```
python -m scripts.fetch_yields
```

Daily 2-year yields for the yield-spread gate, from FRED, the Bundesbank, the Bank of England,
Japan's Ministry of Finance, the SNB, the Bank of Canada and (for Australia, from 2013) DBnomics,
into `data/macro/yields/`. The RBNZ only serves its file to browsers: download the B2 daily-close
workbook by hand into `data/macro/yields/manual/` (see `carry.yields.MANUAL`).

## Backtest (carry strategy)

```
python -m scripts.run_backtest [--start 2010-01-01] [--end 2026-11-01] [--score-threshold 3] [--stop-atr 1.5] [--trail atr|swing] [--entry-hours 1|4]
    [--blackout-hours 6] [--rank-by policy|yield2y] [--min-rate-gap 0] [--split 2018-07-01] [--skip-gates score,vix]
    [--take-profit-r 3] [--close-fraction 0.75] [--trail-atr 1] [--trail-always] [--trail-from-entry]
```

`--entry-hours 1` (default) takes entries and exits on 1H bars with the trend, stop and trail on 4H bars.
`--entry-hours 4` moves everything up one timeframe: entries and exits on 4H bars, trend, stop and
trail on daily bars (UTC days, Sunday evening joined to Monday).
`--blackout-hours 0` turns the news blackout off. `--rank-by yield2y` ranks currencies by 2-year
yields (policy rate where a yield is missing) and `--min-rate-gap` only enters pairs whose levels
differ by at least that many percentage points.

To tune without fitting the whole history, tune on an early period (`--end 2018-07-01`) and check
the chosen settings once on the rest; `--split 2018-07-01` reports both periods of a full run.

Runs the founder's rules on the local H1 files and `data/macro/` (fetch them with the scripts above)
and writes `trades.csv`, `gates.csv` (every candidate pair per day and which gates passed),
`equity.csv` and `summary.txt` to `logs/backtest/<run>/`. The interpretations of rules that left
room are listed at the top of `carry/strategy.py` and `carry/backtest.py`. Not trading advice.
