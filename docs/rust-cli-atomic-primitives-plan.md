# relay-gate Rust CLI — 原子写原语扩展计划

> 2026-07-25. Python legacy CLI 已归档到 `D:\AgentWork\_archive\tools-relay-gate-python\`。
> Rust 二进制 v0.1.0 是 canonical，当前 read-only。本计划评估哪些写操作值得做成 Rust 原子原语，
> 哪些通过原语拼接或直接走原生 NewAPI API。

## 现状

Rust CLI 已有：`doctor`、`channels list/get`、`tokens list/get`、`logs recent`、`schema`。

`Client` 结构体只有 `get()` 方法（HTTP GET）。没有 `post()` / `put()`。

redact 模块（`redact.rs`）已成熟，支持 secret 文本脱敏和字段级 sha256 指纹。

## 设计原则

1. **只添加原子原语**：每个新增命令对应一个 NewAPI API endpoint 的一种 HTTP method，不做组合逻辑。
2. **高层操作走拼接**：`channels maintain`（一轮维护）、`channels optimize`（优先级优化）、`codex-catalog sync`（多消费者目录同步）等复合操作不进 Rust，由 workflow 或 shell 脚本调用原子原语拼接。
3. **dry-run 是一等公民**：每个写原语默认 dry-run，`--apply` 才落地。
4. **secret 不泄露**：复用现有 redact 模块，POST/PUT 的 response 同样过 redact。

## Phase 1：HTTP 写原语 + channel 状态管理（最小可用）

给 `Client` 加 `post()` 和 `put()` 方法（复用 `get()` 的 auth、timeout、redact 逻辑）。

| 新增命令 | NewAPI API | 原子性 | 理由 |
|---|---|---|---|
| `channels create` | `POST /api/channel/` | 原子 | 单次 POST，body 是完整 channel 定义 |
| `channels update` | `PUT /api/channel/` | 原子 | PATCH 语义，body 只含 id + 要改的字段 |
| `channels status` | `POST /api/channel/{id}/status` | 原子 | 单字段操作，NewAPI 要求 status 走单独 endpoint |
| `channels test` | `GET /api/channel/test/{id}` | 原子 | 只读 probe，但需要传 model 参数 |

这 4 个解锁了 channel CRUD + 健康测试。`channels hold-quota`、`channels recover` 是这些原语的组合（读 channel → 改 status → 测试 → 改回），不进 Rust。

## Phase 2：option 和 token 写原语

| 新增命令 | NewAPI API | 原子性 | 理由 |
|---|---|---|---|
| `options list` | `GET /api/option/` | 原子 | 读 NewAPI 全局选项（AutomaticDisableKeywords 等） |
| `options set` | `PUT /api/option/` | 原子 | 设单个 key=value，body 只含 key + value |
| `tokens create` | `POST /api/token/` | 原子 | 单次 POST |
| `tokens update` | `PUT /api/token/` | 原子 | PATCH 语义 |
| `tokens key` | `POST /api/token/{id}/key` | 原子 | 重新生成 key，返回明文 key（需 Sigil 存储） |
| `logs stats` | `GET /api/log/stat` | 原子 | 只读聚合统计 |

`tokens ensure-self` 是 `tokens create` + `tokens key` + Sigil 存储的组合，不进 Rust。

## Phase 3：models 探查原语

| 新增命令 | NewAPI API | 原子性 | 理由 |
|---|---|---|---|
| `models list` | `GET /v1/models`（caller API） | 原子 | 用 caller token 列出上游暴露的模型 |

`codex-catalog sync` 是 `models list` + 读 channel → 写本地 catalog 文件 → 写 Pi/CodeBuddy 配置的组合，不进 Rust。`agent-models sync` 同理。

## 不进 Rust 的（保持组合层）

| Python 命令 | 为什么不进 |
|---|---|
| `channels maintain` | 组合：读 options → 改 options → hold quota → test → recover → optimize → 写 Buddy 配置 |
| `channels optimize` | 组合：读 logs → 计算优先级 → channels update × N |
| `channels hold-quota` | 组合：读 channel → channels status → channels test |
| `channels recover` | 组合：channels test → channels status |
| `channels models set` | 等价于 `channels update` 传 models/test_model/model_mapping 字段 |
| `responses-bridge ensure` | 组合：options list → 解析 JSON → options set |
| `groups ensure` | 组合：groups list → 校验 → options set × 3 |
| `tokens ensure-self` | 组合：tokens create → tokens key → Sigil 存储 |
| `codex-catalog sync` | 组合：models list → 写多个本地文件 |
| `codex-catalog task install/status/remove` | Windows 计划任务管理，非 NewAPI API |
| `agent-models sync` | 组合：models list → 写 Pi/CodeBuddy 配置文件 |

## 实现顺序

1. `Client::post()` + `Client::put()`（lib.rs，复用 get 的 auth/timeout/redact）
2. `channels create` / `channels update` / `channels status`（Phase 1）
3. `channels test`（Phase 1，GET 但需要 caller token 认证路径）
4. `options list` / `options set`（Phase 2）
5. `tokens create` / `tokens update` / `tokens key`（Phase 2）
6. `logs stats`（Phase 2，纯只读）
7. `models list`（Phase 3，caller API 认证路径）

## 每个 Phase 的验收

- Phase 1：能用 Rust CLI 创建、更新、启停、测试一个 channel，不调用 Python 脚本或手写 curl。
- Phase 2：能用 Rust CLI 管理 NewAPI 全局选项和 caller token 全生命周期。
- Phase 3：能用 Rust CLI 列出上游模型集合，供 catalog sync 组合层消费。
