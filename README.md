# Agent-Co-Agent-Codespace

## Tests

Every push and pull request runs the test suite on GitHub Actions (`.github/workflows/tests.yml`):
Python 3.12, `python -m pytest -q`. Read the result on the commit or pull request — the last lines of the
"Run the tests" step say how many passed and list any that failed.

To run them locally: `python -m pip install pytest && python -m pytest -q`.
