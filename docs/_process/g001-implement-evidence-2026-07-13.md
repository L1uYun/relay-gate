# G001 implement evidence — relay-gate output contract

Date: 2026-07-13
Status: complete

## Changes
- `scripts/relay_gate.py`
  - `--output human|json|quiet` replaces `--json`
  - `emit` / `emit_and_optionally_log` take mode string
  - `--verbose` remains human-only detail
  - scheduled task command uses `--output json`
- `tests/test_routing.py` migrated
- `README.md` documents output modes

## Verification (this turn)
```text
python -m pytest -q
70 passed
```
`--json` rejected; `--output json` accepted; quiet emits empty stdout.

## Next
G002 review must PASS before sigil.
