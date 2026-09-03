# NewAPI 通道 param_override：gemini-3.8 if/then 剥离修复

> **事件（2026-09-03）**：`gemini-3.8-flash-high` 在 Pi（`api: openai-completions`）里报 `400 Unknown name "if"/"then" at 'tools[N].parameters'`。根因是 ch30 `cpa-gemini-responses` 的 `param_override` 里剥离 if/then/else 的操作被 `request_path == /v1/messages` 条件锁死，而 Pi 走 `/v1/chat/completions`，条件不命中 → 未剥离 → Google 拒。修复：去掉该条件，改为无条件剥离。本文记录因果、改法与验证，交叉引用可复用 pattern 在 relay-gate skill `references/param-override-cookbook.md`（#13）。

Owner: `D:\AgentWork\tools\relay-gate`（独立个人 infra，非 xiaolab）
Live host: `xiaolab-japan`（SSH alias 见 `~/.ssh/config`）
DB: `/opt/newapi/data/new-api.db`（SQLite，`journal_mode=delete`）

---

## 背景：为什么 if/then 会在 gemini 上炸

链路 `NewAPI ch30 → cli-proxy-api(CPA) → antigravity → Google cloudcode-pa.googleapis.com`。

NewAPI 对 type-58 AdvancedCustom 通道，converter 之后、upstream 之前执行 `param_override` 的逐条 op。ch30 的 models 是 `gemini-3.8-flash-high`，其 `settings.advanced_custom.advanced_routes` 只列了 3.6/3.7，故 3.8 落入 `converter: none` 直通 → body 按原样发给 CPA → CPA 转 Google `function_declarations`。

**Google 的 function_declarations 只收 OpenAPI 3.0 子集**，不收 OpenAI JSON-Schema 扩展——即 function `parameters` 对象里的条件必填字段 `if` / `then` / `else`。客户端（如 Pi 的 openai-completions 客户端，或任何生成 conditional-required schema 的工具）一旦发出带 `if/then/else` 的 tool，Google 直接 400：

```
400 {"message":"Invalid JSON payload received. Unknown name \"if\" at 'tools[17].function_declarations[17].parameters': Cannot find field.\nUnknown name \"then\" ..."}
```

## 根因：request_path 条件锁死了剥离

ch30 的 `param_override` 原本就带剥离 op，但**四个 op 全被 `conditions:[{"path":"request_path","mode":"full","value":"/v1/messages"}]` 限定**：

```json
{"path":"tools","mode":"prune_objects","value":{...非 function 删...},"conditions":[{"path":"request_path","mode":"full","value":"/v1/messages"}]},
{"path":"tools.*.function.parameters.if","mode":"delete","conditions":[{"path":"request_path","mode":"full","value":"/v1/messages"}]},
{"path":"tools.*.function.parameters.then","mode":"delete","conditions":[.../v1/messages]},
{"path":"tools.*.function.parameters.else","mode":"delete","conditions":[.../v1/messages]}
```

这四条的意图是防御性的（Google 永不收 if/then，非 function tool 也不收），**本应对所有路径生效**。但条件把作用域锁死在 `/v1/messages`（Anthropic Messages 路径）。结果：
- Claude Code 走 `/v1/messages` → 命中条件 → 剥离 → 正常
- **Pi 走 `/v1/chat/completions` → 条件不命中 → if/then 未剥 → Google 400**
- Codex 走 `/v1/responses` 同理会漏

所以"只在 Pi / 只对 3.8-high 报错"——gpt/deepseek 走的是接受 if/then 的其他上游，唯独 3.8 走 Google。

## 修复：去掉 request_path 条件，无条件剥离

新 param_override（8 个 op，全部无条件）：

```json
{"operations":[
 {"path":"messages.*.role","mode":"replace","from":"developer","to":"system"},
 {"path":"tools","mode":"prune_objects","value":{"recursive":false,"conditions":[{"path":"type","mode":"full","value":"function","invert":true}]}},
 {"path":"tools.*.function.parameters.if","mode":"delete"},
 {"path":"tools.*.function.parameters.then","mode":"delete"},
 {"path":"tools.*.function.parameters.else","mode":"delete"},
 {"path":"previous_response_id","mode":"delete"},
 {"path":"store","mode":"delete"},
 {"path":"prompt_cache_key","mode":"delete"}
]}
```

原则：**只在该 rewrite 对恰好一条路由成立时才用 `request_path` 门控；对防御性正确、所有路径都该执行的 op，不加条件。** ch30 只服务 Google，if/then/else 与 non-function tool 都是 Google 不收的，无条件剥离安全。

## 改法（直写运行中 DB，无需重启）

远端 `/opt/newapi/data/new-api.db` 是 SQLite `journal_mode=delete`，运行中 NewAPI 在写时会短暂持锁。sqlite3 直 UPDATE 加 `busy_timeout` 自旋即可，`rowcount=1` 即时生效、**无需重启容器**（NewAPI 每请求读 channel 配置）。不要用 PowerShell here-string 生成嵌套 JSON（会串字符，karma #523）；用 python `json.dumps(separators=(',',':'))` 生成后参数化 UPDATE。

```bash
# 远端，python3 已就绪
python3 - <<'PY'
import sqlite3
con = sqlite3.connect('/opt/newapi/data/new-api.db', timeout=8)
con.execute('PRAGMA busy_timeout=8000')
con.execute("UPDATE channels SET param_override=? WHERE id=30", (open('/tmp/new.json').read(),))
con.commit()
print('rows:', con.total_changes)
PY
```

## 验证（端到端，token id3，127.0.0.1:3000）

修复前后对照（复刻 Pi 报错结构，带 if/then 的 function）：

```text
修复前: 1 个 if/then function   → 400 Unknown name "if"
修复后: 1 个 if/then function   → 200，正确返回 tool_call
修复后: 3 个 if/then function (idx 3/17/29 复刻 Pi) → 200
修复后: 纯文本无 tools          → 200（回归安全）
```

## 备份与回滚

- 原始带条件版 param_override（847B）：`C:\tmp\relay-gate-backups\ch30_param_override_backup.json`
- 回滚：把该 JSON 原样写回 `channels.param_override WHERE id=30`

## Related

- 可复用 pattern 目录：relay-gate skill `references/param-override-cookbook.md`（#13 if/then strip + request_path-condition trap 通则）
- 出站代理/antigravity 出口选型（同链路姊妹主题）：`docs/cpa-outbound-proxy.md`
- CPA 管理面：`docs/cliproxy-management.md`
- 拓扑真源：relay-gate skill `references/topology-and-local-state.md`
