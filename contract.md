# Relay Gate Rust CLI — current surface + historical contract

## Current surface (2026-07-26)

Canonical local CLI is the Rust binary. Live `schema` reports:

- `schema_version`: `relay-gate.v1`
- `mutation_allowed`: `true`
- 18 operations: `schema`, `doctor`, `channels.{list,get,create,update,status,test}`, `tokens.{list,get,create,update,key}`, `logs.{recent,stats}`, `options.{list,set}`, `models.list`

Selector style: `--input '<json>'`.
Write ops execute immediately (no Rust dry-run flag yet).
Composite catalog / maintain / CPA stay outside this binary.

See `README.md` and `docs/rust-cli-atomic-primitives-plan.md`.

---

## Historical frozen decision (Liber Null task 305, read-only slice)

The text below is the original slice-305 contract that established the read-only core. It is retained for provenance. **It is superseded by the Phase 1–3 atomic write primitives already shipped.**

### Frozen decision (historical)

Replace the active local Relay Gate command surface incrementally with Rust. This
slice establishes the read-only NewAPI core. The CLI is agent-native rather than
an argparse compatibility layer: it emits a stable JSON envelope by default,
accepts structured selector input, exposes a discoverable schema, classifies
errors, and never writes remote state.

### Scope (historical)

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
JSON diagnostics, and redaction. Mutating NewAPI commands were out of scope for
slice 305; they later landed as atomic primitives with a separate plan.

The existing user change is authoritative: `grok-4.5` has a Codex product
context window of 200000. Preserve that value in the Rust catalog regression
test.

### Mechanical tokens (historical)

- `agent_native_cli`
- `read_only_surface`
- `secret_redaction`
- `grok_4_5_context_window`

### Acceptance (historical slice 305)

- The listed commands return a versioned JSON envelope by default and expose the
  same operation names from `schema`.
- Any selected HTTP operation in slice 305 was GET-only.
- The test suite demonstrates that a raw authorization value is redacted from
  stdout and diagnostics.
- The Rust test suite asserts `grok-4.5` resolves to `200000`.
- `cargo fmt --all -- --check`, `cargo clippy --all-targets -- -D warnings`,
  `cargo test`, and `cargo build --release` pass using D: build state.
