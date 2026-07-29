# relay-gate (Rust atomic CLI)

Agent-native Rust CLI for the NewAPI gateway at `https://newapi.l1uyun.top:8080`.

Canonical binary: `D:\AgentWork\state\relay-gate\bin\relay-gate.exe` (also on `PATH` via cargo bin).

As of 2026-07-26 this CLI is **not read-only**. `schema` reports `mutation_allowed: true` and 18 atomic operations. Composite workflows (catalog projection, maintain/optimize, CPA management) stay outside this binary.

Legacy Python CLI is archived at `D:\AgentWork\_archive\tools-relay-gate-python\`.

## Why

Replace the Python `relay-gate` surface with a stable Rust core: versioned JSON envelope, structured `--input` selectors, discoverable schema, classified errors, and redacted diagnostics. Higher-level maintain/catalog/CPA logic is composed from these primitives in scripts/workflows.

## Build

Rust 2024 edition (rustc 1.85+). Keep build state on `D:`:

```sh
CARGO_HOME=D:/AgentWork/state/cargo/home \
CARGO_TARGET_DIR=D:/AgentWork/state/cargo/targets/relay-gate-rust-305 \
cargo build --release
```

## Commands

All commands emit a versioned JSON envelope by default:

```json
{
  "schema_version": "relay-gate.v1",
  "ok": true,
  "operation": "doctor",
  "data": { ... }
}
```

Errors use the same envelope with `ok: false` and a redacted `error` object.

| Command | Operation | Selector (`--input` JSON) |
|---|---|---|
| `schema` | `schema` | none |
| `doctor` | `doctor` | none |
| `channels list` | `channels.list` | `{"page":u32?,"page_size":u32?,"status":i32?,"type":i32?,"group":str?,"id_sort":bool?}` |
| `channels get` | `channels.get` | `--id 40` or `{"id":u64}` |
| `channels create` | `channels.create` | `{"name":str,"type":i32?,"base_url":str?,"key":str?,"models":str?,"group":str?,"priority":i32?,"weight":i32?}` |
| `channels update` | `channels.update` | `--id 40 --set-models "a,b,c"` or `{"id":u64,"fields":{...}}` PATCH; no `status` |
| `channels status` | `channels.status` | `--id 40 --status 1` or `{"id":u64,"status":i32}` 1=enabled, 2=disabled, 3=auto |
| `channels test` | `channels.test` | `{"id":u64,"model":str?}` |
| `tokens list` | `tokens.list` | `{"keyword":str?,"page":u32?,"page_size":u32?}` |
| `tokens get` | `tokens.get` | `{"id":u64}` |
| `tokens create` | `tokens.create` | `{"name":str,"group":str?,"remain_quota":i64?,"unlimited_quota":bool?,"expired_time":i64?}` |
| `tokens update` | `tokens.update` | `{"id":u64,"fields":{...}}` |
| `tokens key` | `tokens.key` | `{"id":u64}` regenerate key |
| `logs recent` | `logs.recent` | `{"page":u32?,"page_size":u32?,"self":bool?}` |
| `logs stats` | `logs.stats` | none |
| `options list` | `options.list` | `{"key":str?}` |
| `options set` | `options.set` | `{"key":str,"value":str}` |
| `models list` | `models.list` | none; uses caller token |

```
sh
relay-gate schema
relay-gate doctor
relay-gate channels list --input '{"page":1,"page_size":10}'
relay-gate channels get --id 7
relay-gate channels update --id 7 --set-models "gpt-5.5,gpt-5.4" --apply
relay-gate channels status --id 7 --status 1 --apply
relay-gate channels test --id 7 --model "gpt-5.5"
relay-gate tokens list --input '{"keyword":"codex"}'
relay-gate options list
relay-gate logs recent --input '{"page_size":20}'
relay-gate logs stats
# models list needs RELAY_GATE_CALLER_TOKEN
sigil exec RELAY_GATE_CALLER_TOKEN --apply -- relay-gate --output json models list
```

### Shorthand flags

`channels get/update/status/test` support shorthand flags that merge into `--input` JSON (`--input` wins on conflict):

| Command | Shorthand | Equivalent `--input` |
|---|---|---|
| `channels get` | `--id 40` | `{"id":40}` |
| `channels update` | `--id 40 --set-models "a,b,c"` | `{"id":40,"fields":{"models":"a,b,c"}}` |
| `channels update` | `--id 40 --set-status 1` | `{"id":40,"fields":{"status":1}}` (use `channels status` instead) |
| `channels status` | `--id 40 --status 1` | `{"id":40,"status":1}` |
| `channels test` | `--id 40 --model "gpt-5.5"` | `{"id":40,"model":"gpt-5.5"}` |

### `rgate` PowerShell wrapper

`scripts/rgate.ps1` provides a one-liner wrapper that auto-injects Sigil credentials:

```powershell
. D:\AgentWork\tools\relay-gate\scripts\rgate.ps1
rgate doctor
rgate channels update --id 40 --set-models "a,b,c" --apply
rgate channels status --id 41 --status 1 --apply
```

Dot-sourced from `~/Documents/PowerShell/profile.ps1`.

## Credentials

Admin token resolves from the environment, in order:

1. `SIGIL_ADMIN_TOKEN`
2. `RELAY_GATE_ADMIN_TOKEN`

Sent as raw `Authorization` plus `New-Api-User: 1` (override with `RELAY_GATE_USER_ID`). Base URL: `--base-url` or `RELAY_GATE_BASE_URL`.

Caller token for `models list`:

1. `RELAY_GATE_CALLER_TOKEN`

Prefer `sigil exec RELAY_GATE_CALLER_TOKEN --apply -- relay-gate ...` so secrets stay out of shell history.

## Write semantics

- Global flags: --dry-run (force preview), --apply (land mutation).
- Mutation ops: channels.create/update/status, 	okens.create/update/key, options.set.

- Write ops **default to dry-run**. Pass --apply to land. If both --dry-run and --apply are set, dry-run wins.
- `channels.update` is PATCH-style: send only fields to change; never include `status` (use `channels.status`).
- Always `channels get` first when patching a live channel so user edits are not overwritten.

## Not in this binary

| Capability | Owner |
|---|---|
| Codex/Codex++/Pi/CodeBuddy catalog projection | `D:\AgentWork\scripts\refresh-codex-model-menu-cache.ps1` |
| CodeBuddy sync helper | `scripts/sync-codebuddy-models.ps1` |
| CPA / cliproxy management | `scripts/cliproxy_mgmt.py` + `docs/cliproxy-management.md` |
| Remote maintain timer | `xiaolab-japan:/opt/newapi-maintainer` (Python, remote-only) |
| `channels maintain/optimize`, `codex-catalog *`, `agent-models *` | retired composite CLI surface |

## Redaction

The raw credential is never echoed in stdout, arguments, or diagnostics:

- Channel/token `key` fields are masked to `{present, sha256, redacted}`.
- Non-2xx and `success:false` bodies are redacted before being placed in error messages.
- A final scrub replaces any remaining occurrence of the raw token in the serialized envelope with `[REDACTED]`.

## Output modes

- `--output json` (default): versioned envelope.
- `--output human`: one-line summary.
- `--output quiet`: no stdout; exit code `0` on success, `1` on error.
- `--pretty`: pretty-print JSON.

## Catalog overrides

`grok-4.5` and other context-window pins live in `src/catalog.rs` for local projection consumers.

## Testing

```sh
CARGO_HOME=D:/AgentWork/state/cargo/home \
CARGO_TARGET_DIR=D:/AgentWork/state/cargo/targets/relay-gate-rust-305 \
cargo test
```

## License

MIT


