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
