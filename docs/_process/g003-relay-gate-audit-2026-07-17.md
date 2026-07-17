# relay_gate.py code-audit (2026-07-17, #219)

Scope: `D:\AgentWork\tools\relay-gate\scripts\relay_gate.py` (+ new `cliproxy_mgmt.py`).

## Metrics

| Metric | Value |
|--------|------:|
| Lines | 4496 |
| Functions | 159 |
| dry_run mentions | 17 |
| effective_apply uses | 27 |
| TODO/FIXME | 0 |

## Findings

### F1 — God module size (keep / defer split)
`relay_gate.py` is a single 4.5k-line operator surface. Longest bodies: `build_parser` (~380), `run_channel_optimization` (~181), `sse_probe_via_relay` (~162), `_promote_on_recovery` (~121).  
**Decision:** Do **not** split in this slice (如无必要勿增实体). CPA was added as a companion module `cliproxy_mgmt.py` instead of growing NewAPI helpers further.

### F2 — Write-path apply guards mostly consistent (no-issue for critical paths)
Create/update/optimize/maintain paths use `effective_apply` / dry-run defaults.  
`tokens_ensure_self` is the only write-ish command without an obvious dry-run gate in the first 4k chars of the function; it is an idempotent ensure path that already exists in production. **No change this turn** unless user wants dry-run parity.

### F3 — Secret handling (no-issue)
No `print` of tokens/passwords found. List/get helpers use `redacted_tree` / fingerprint patterns. Cliproxy list paths redact API keys to `sha256_prefix` + length only.

### F4 — Authorization header construction (no-issue after verify)
Two Authorization constructions exist for NewAPI admin + cliproxy management. Cliproxy tries Bearer then raw to match CPA v7.2.73.

### F5 — Network / ban footgun (documented)
CPA management IP-bans after repeated 401s (~30m). Documented in `docs/cliproxy-management.md`. Prefer correct Sigil key and avoid header fuzzing against 127.0.0.1:8317.

## Fixes applied this turn

1. Added `scripts/cliproxy_mgmt.py` + `relay-gate cliproxy ...` registration.
2. Added unit tests `tests/test_cliproxy_mgmt.py` (6 passed).
3. Added operator docs `docs/cliproxy-management.md` (OAuth notes + Cloudflare scoped-token pattern).

## Explicit no-issue for functional NewAPI core

No defect found that requires an emergency patch to existing NewAPI channel/token/optimizer behavior. Remaining size/complexity is structural debt, not a correctness regression.

## Residual risk

- SSH transport pays multi-second cost per call; doctor probes several surfaces sequentially.
- CPA remains loopback-only; availability depends on `ssh xiaolab-japan` or a local tunnel.