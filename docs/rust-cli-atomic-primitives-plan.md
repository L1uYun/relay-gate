# relay-gate Rust CLI — 原子写原语扩展计划

> 更新 2026-07-26。Python legacy CLI 已归档到 `D:\AgentWork\_archive\tools-relay-gate-python\`。
> Rust 二进制是 canonical。**Phase 1–3 已落地**：`mutation_allowed=true`，18 个原子操作。
> 本文件保留设计原则与“不进 Rust 的组合层”边界；不再描述“当前 read-only”。

## 现状（事实）

Rust CLI 已有：

- meta: `schema` `doctor`
- channels: `list` `get` `create` `update` `status` `test`
- tokens: `list` `get` `create` `update` `key`
- logs: `recent` `stats`
- options: `list` `set`
- models: `list`（caller token 路径，`Client::new_caller()`）

`Client` 已有 `get()` / `post()` / `put()` / `send_and_parse()` / `new_caller()`。

选择器统一 `--input JSON`。写操作默认 dry-run；`--apply` 落地；`--dry-run` 优先于 `--apply`（2026-07-26 已补）。

redact 模块成熟，POST/PUT response 同样过 redact。

## 设计原则

1. **只添加原子原语**：每个新增命令对应一个 NewAPI API endpoint 的一种 HTTP method，不做组合逻辑。
2. **高层操作走拼接**：`channels maintain`、`channels optimize`、catalog 投影等复合操作不进 Rust，由 workflow / shell 调用原子原语拼接。
3. **dry-run 是一等公民**：写原语默认 dry-run，`--apply` 才落地；`--dry-run` 优先于 `--apply`。
4. **secret 不泄露**：复用 redact 模块。

## Phase 1–3（已完成）

### Phase 1：channel 状态管理

| 命令 | NewAPI API | 状态 |
|---|---|---|
| `channels create` | `POST /api/channel/` | done |
| `channels update` | `PUT /api/channel/` | done |
| `channels status` | `POST /api/channel/{id}/status` | done |
| `channels test` | `GET /api/channel/test/{id}` | done |

### Phase 2：option 和 token

| 命令 | NewAPI API | 状态 |
|---|---|---|
| `options list` | `GET /api/option/` | done |
| `options set` | `PUT /api/option/` | done |
| `tokens create` | `POST /api/token/` | done |
| `tokens update` | `PUT /api/token/` | done |
| `tokens key` | `POST /api/token/{id}/key` | done |
| `logs stats` | `GET /api/log/stat` | done |

### Phase 3：models 探查

| 命令 | NewAPI API | 状态 |
|---|---|---|
| `models list` | `GET /v1/models`（caller API） | done |

## 不进 Rust 的（保持组合层）

| 旧 Python 命令 | 为什么不进 | 当前 owner / 替代 |
|---|---|---|
| `channels maintain` | 组合逻辑 | remote maintainer on xiaolab-japan |
| `channels optimize` | 组合：logs → priority → update × N | 手工 / 脚本拼接原子原语 |
| `channels hold-quota` / `recover` | status + test 组合 | `channels status` + `channels test` |
| `channels models set` | 等价 update 字段 | `channels update --input '{"id":N,"fields":{...}}'` |
| `responses-bridge ensure` / `groups ensure` | options 组合 | `options list/set` |
| `tokens ensure-self` | create + key + Sigil | 原子 tokens.* + sigil 手工 |
| `codex-catalog sync/task` | 多文件投影 + Windows 任务 | `D:\AgentWork\scripts\refresh-codex-model-menu-cache.ps1` + `models list` |
| `agent-models sync` | 多消费者投影 | 同上 / `scripts/sync-codebuddy-models.ps1` |
| `cliproxy *` | CPA 管理面 | `scripts/cliproxy_mgmt.py` |

## 后续（未开）

1. 组合层 catalog 投影继续以 `models list` 为上游集合源，不要把复合逻辑塞回 Rust。
3. 评估是否给 CPA 做独立原子 CLI（另开 contract），当前不进 relay-gate 主二进制。

## 验收记录

- Phase 1–3 代码：`c95c0dd` 一带入主线。
- Live `relay-gate --output json schema`：`mutation_allowed=true`，18 ops。
- 文档同步：2026-07-26（本回合）。

