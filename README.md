# relay-gate (Rust read-only core)

Agent-native Rust CLI for the NewAPI gateway at `https://newapi.l1uyun.top:8080`.
This slice is strictly **read-only**: every HTTP operation is `GET`. Mutating
NewAPI commands are out of scope and will require a separately reviewed write
boundary.

## Why

The Python `relay-gate` CLI is being replaced incrementally with Rust. This
crate establishes the read-only core: a stable versioned JSON envelope,
structured selector input, a discoverable schema, classified errors, and
redacted diagnostics. The existing Python files remain as compatibility
reference during this slice.

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
| `channels get` | `channels.get` | `{"id":u64}` |
| `tokens list` | `tokens.list` | `{"keyword":str?,"page":u32?,"page_size":u32?}` |
| `tokens get` | `tokens.get` | `{"id":u64}` |
| `logs recent` | `logs.recent` | `{"page":u32?,"page_size":u32?,"self":bool?}` |

```sh
relay-gate schema
relay-gate doctor
relay-gate channels list --input '{"page":1,"page_size":10}'
relay-gate channels get --input '{"id":7}'
relay-gate tokens list --input '{"keyword":"codex"}'
relay-gate logs recent --input '{"page_size":20}'
```

## Credentials

The admin token resolves from the environment, in order:

1. `SIGIL_ADMIN_TOKEN` (existing Sigil environment path)
2. `RELAY_GATE_ADMIN_TOKEN` (process env fallback)

It is sent as a raw `Authorization` header plus `New-Api-User: 1`. Override the
base URL with `--base-url` or `RELAY_GATE_BASE_URL`; override the user id with
`RELAY_GATE_USER_ID`.

## Redaction

The raw credential is never echoed in stdout, arguments, or diagnostics:

- Channel/token `key` fields are masked to `{present, sha256, redacted}`.
- Non-2xx and `success:false` bodies are redacted before being placed in error
  messages.
- A final scrub replaces any remaining occurrence of the raw token in the
  serialized envelope with `[REDACTED]`.

## Output modes

- `--output json` (default): versioned envelope.
- `--output human`: one-line summary.
- `--output quiet`: no stdout; exit code `0` on success, `1` on error.
- `--pretty`: pretty-print JSON.

## Catalog

`grok-4.5` is pinned to a `200000` Codex product context window. See
`src/catalog.rs`.

## Testing

```sh
CARGO_HOME=D:/AgentWork/state/cargo/home \
CARGO_TARGET_DIR=D:/AgentWork/state/cargo/targets/relay-gate-rust-305 \
cargo test
```

The integration suite (`tests/cli.rs`) runs an in-process fixture HTTP server
that proves auth headers, GET-only semantics, non-2xx JSON errors, `success:
false` handling, redaction, and the `grok-4.5` -> `200000` anchor.

## License

MIT
