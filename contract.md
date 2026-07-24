# Relay Gate Rust Read-Only Core

Liber Null task: `305`

## Frozen decision

Replace the active local Relay Gate command surface incrementally with Rust. This
slice establishes the read-only NewAPI core. The CLI is agent-native rather than
an argparse compatibility layer: it emits a stable JSON envelope by default,
accepts structured selector input, exposes a discoverable schema, classifies
errors, and never writes remote state.

## Scope

Create a Rust 2024 crate named `relay-gate`. Its library may use the necessary
`catalog`, `redact`, and `schema` modules, with these read-only commands:

- `schema`
- `doctor`
- `channels list` and `channels get`
- `tokens list` and `tokens get`
- `logs recent`

Authentication must resolve the NewAPI administration credential through the
existing Sigil environment path or a process environment variable. The command
surface must never echo token material in arguments, normal output, or error
output. Use HTTP fixtures to test request headers, response envelopes, non-2xx
JSON diagnostics, and redaction. Mutating NewAPI commands are explicitly out of
scope for this slice; their eventual contract will require an explicit `apply`
intent and a separately reviewed write boundary.

The existing user change is authoritative: `grok-4.5` has a Codex product
context window of 200000. Preserve that value in the Rust catalog regression
test; do not overwrite any uncommitted Python changes on the primary worktree.

## Mechanical tokens

- `agent_native_cli`
- `read_only_surface`
- `secret_redaction`
- `grok_4_5_context_window`

## Acceptance

- The listed commands return a versioned JSON envelope by default and expose the
  same operation names from `schema`.
- Any selected HTTP operation is GET-only. No code path in this slice issues a
  mutation request.
- The test suite demonstrates that a raw authorization value is redacted from
  stdout and diagnostics.
- The Rust test suite asserts `grok-4.5` resolves to `200000`.
- `cargo fmt --all -- --check`, `cargo clippy --all-targets -- -D warnings`,
  `cargo test`, and `cargo build --release` pass using D: build state.
