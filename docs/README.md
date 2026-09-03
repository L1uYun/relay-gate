# relay-gate docs

Tool documents for Relay Gate live in this directory.

- `rust-cli-atomic-primitives-plan.md` — atomic CLI design; Phase 1–3 done (18 ops)
- `codex-third-party-model-display.md` — Codex desktop third-party model display / catalog projection mechanism
- `cliproxy-management.md` — CPA management via companion module `scripts/cliproxy_mgmt.py` (not exposed by the current Rust binary)
- `cpa-outbound-proxy.md` — CPA 出站代理与 antigravity 出口选型（mihomo 分流、Google antigravity 端点 IP 灰名单、HY2 出口打通 2026-09-03）
- `newapi-param-override-gemini-if-then.md` — ch30 gemini-3.8 `if/then` 400 修复：param_override 的 request_path 门控盲区（Pi `/v1/chat/completions` 漏剥）与无条件剥离改法 2026-09-03
