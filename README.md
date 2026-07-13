# relay-gate

NewAPI / Relay Gate management CLI for channel inventory, model projection, caller tokens, groups, and maintenance workflows.

## Why

A relay gateway drifts when channel models, Codex/Pi/CodeBuddy catalogs, and caller tokens are edited by hand. relay-gate keeps the admin operations in one CLI with non-revealing list/get defaults.

## Install

```powershell
pip install -e .
```

Python 3.11+. Dependency: `requests`.

## 30-second start

```powershell
relay-gate doctor
relay-gate channels list
relay-gate codex-catalog models
relay-gate agent-models sync --dry-run
```

## Command groups

- `doctor` — service and admin API access
- `codex-catalog models|sync|task install|status|remove` — project NewAPI models into Codex-family catalogs / scheduled task
- `agent-models sync` — reconcile Pi / CodeBuddy / WorkBuddy model lists
- `channels list|get|create|update|test|hold-quota|recover|models|optimize|maintain`
- `responses-bridge get|ensure`
- `groups list|ensure`
- `tokens list|get|create|update`

List/get paths are designed to avoid printing upstream keys or caller secrets.

## Safety

- Credentials come from environment / secret store, not committed files.
- Prefer dry-run / list before create/update/maintain.
- Do not commit gateway admin tokens, channel keys, or live route dumps.

## Testing

```powershell
python -m pytest -q
```

## License

MIT
