# Codex 桌面端第三方模型显示机制

## 结论

Codex 桌面端菜单显示第三方模型（glm-5.2、claude-opus-4-8、deepseek-v4-pro、grok-4.3、kimi-for-coding 等）由 **Codex++（codex-plus-plus）的运行时 CDP 注入** 控制，不是 cc-switch，不是 `config.toml` 的 `model_catalog_json` 指令，也不是本地 watcher 脚本直接改前端。

2026-07-10 起，本机目录改为**集合 + 简化规格 + 完整模板**三层合成：NewAPI `/v1/models` 决定当前模型集合；cc-switch 当前 provider 的 `settings_config.modelCatalog.models` 若存在，只提供 `model`、`displayName`、`contextWindow` 等简化规格；现有本地完整 catalog 提供 Codex schema 模板。本地目录投影由组合层脚本完成：先用 `relay-gate models list` 读 NewAPI `/v1/models` 集合，再由 `D:\AgentWork\scripts\refresh-codex-model-menu-cache.ps1` 克隆完整 Codex schema 模板、覆盖规格并为上游新增模型补齐条目，投影到 `~/.codex/cc-switch-model-catalog.json`、`~/.codex/models_cache.json` 和 Codex++ `settings.json relayProfiles[].modelList`。旧 `relay-gate codex-catalog sync` 子命令已不在 Rust CLI。

cc-switch、config.toml、catalog 文件本身仍然不碰前端 UI 的 Statsig 配置；前端显示仍然靠 Codex++ 的 CDP 注入。区别只是：Codex++ 注入前消费的本地模型清单和目录元数据现在由 relay-gate CLI 统一生成，Windows 计划任务也直接执行 CLI，不再常驻 PowerShell watcher。

## 证据链

### 1. Codex 前端模型菜单的运行时数据源

Codex 桌面端是 Electron 应用，前端模型菜单由 **Statsig 动态配置 `107580212`** 的 `available_models` 字段控制。该配置缓存在 localStorage 的 `statsig.cached.evaluations.<sdkKey>` 里。

通过 CDP（`--remote-debugging-port`）读取运行时状态：

- localStorage 里的原始缓存（key `1692365553`，`source: "Network"`）只含 5 个官方模型：
  `["gpt-5.5","gpt-5.4","gpt-5.4-mini","gpt-5.3-codex","gpt-5.2"]`
- 但 Statsig 客户端实例 `inst_client-xxx` 的 `getDynamicConfig('107580212')` 返回值已被 Codex++ 替换为第三方模型集合，`default_model` 和 `use_hidden_models` 也由注入层控制。2026-07-10 验证时，本地目录与 Codex++ 运行时均已包含 NewAPI 暴露的 19 个模型。

这说明在 Statsig 客户端层面，`getDynamicConfig` 方法已被 monkey-patch。

### 2. Codex++ 的注入机制

Codex.exe 的主进程命令行带 `--remote-debugging-port=53704 --remote-allow-origins=http://127.0.0.1:53704 --inspect=127.0.0.1:53804`。这是 Codex++ 启动 Codex 时加的参数（`C:\Users\84618\.codex-session-delete\latest-status.json` 记录 `debug_port: 53704`）。

Codex++ 通过 CDP 连进 Codex 渲染进程，注入了以下 window 标记（实测全部存在）：

- `__codexPlusModelJsonResponsePatchInstalled = "1"` — 拦截 `/model/json` 响应
- `__codexPlusModelMessagePatchInstalled = true` — 拦截模型消息
- `__codexPlusForceChineseLocaleInstalled`
- `__codexPlusBackendHeartbeat`
- `__codexPlusResizeHandler`
- `__codexPlusConversationViewCleanup`
- `__codexPlusModelJsonResponseOriginals`
- `__codexPlusUserScripts`（含 `market-codex-list-pagebuster.js`）

Codex++ settings.json（`C:\Users\84618\.codex-session-delete\settings.json`）的关键开关：

- `codexAppModelWhitelistUnlock: true` — 模型白名单解锁总开关
- `launchMode: "patch"` — 以 patch 模式启动（带 CDP 注入）
- `enhancementsEnabled: true`
- `relayProfiles` 里的 `modelInsertMode: "patch"` — 模型列表注入模式
- `relayProfiles.modelList` — Codex++ 注入用的模型清单，由 relay-gate CLI 同步为完成本地模板补齐后的 NewAPI 模型集合
- `relayProfiles.configContents` — 写入 config.toml 的完整正文
- `relayProfiles.authContents` — 写入 auth.json 的 API key

### 3. cc-switch 的角色

cc-switch（`D:\software\cc-switch\cc-switch.exe`）是配置切换器，数据库在 `C:\Users\84618\.cc-switch\cc-switch.db`。

`providers` 表里 `app_type='codex'` 且 `is_current=1` 的 provider 的 `settings_config` 字段包含三块：

- `auth` — API key（写入 `~/.codex/auth.json`）
- `config` — 完整 config.toml 正文（写入 `~/.codex/config.toml`，含 `base_url`、`wire_api` 等；当存在自定义目录时，cc-switch 会补 `model_catalog_json = "cc-switch-model-catalog.json"`）
- `modelCatalog` — cc-switch 私有的简化模型规格，通常只有 `model`、`displayName`、`contextWindow` 和少量能力覆盖；cc-switch 与 relay-gate CLI 都会用完整模板把它展开成 Codex schema

cc-switch 切换 provider 时把这三份写进 `~/.codex/` 的三个文件。**不碰 Codex 前端的 Statsig 配置**，所以菜单不显示第三方模型。

### 4. 本地目录 owner：Rust `models list` + 组合层 PS1

2026-07-26 事实：Rust CLI 只提供 `models list` 原子原语；本地模型目录合成由 `D:\AgentWork\scripts\refresh-codex-model-menu-cache.ps1` 负责（内部调用 `models list`）：

1. 从 NewAPI `/v1/models` 读取当前暴露模型 id。
2. 可选读取 cc-switch 当前 provider 的简化 `modelCatalog` 规格；没有规格时复用现有完整 catalog 模板。
3. 对已知缺失模型应用 provider-surface 元数据覆盖，包括上下文窗口、推理等级和 GPT-5.6 的 multi-agent/tool 字段。
4. 原子投影到 catalog、models_cache 和 Codex++ `settings.json relayProfiles[].modelList`，并确保 profile 的 `model_catalog_json` 入口存在。
5. 以同一份规范化 catalog 的可见模型集合同步 Pi `providers.newapi.models` 和 CodeBuddy `models/availableModels`，同时补齐上下文与输出容量。
6. servitor 的 Claude/Codex provider 动态读取 NewAPI 模型，并使用 catalog `visibility=hide` 排除本地隔离项。
7. servitor 的 `agy-tui` 不属于 Relay Gate 投影目标：当前 AGY 原生配置没有第三方 provider/model 注入规范，且 servitor 通过 TUI 模式运行、未传递 `--model`。其静态 Gemini 列表是 AGY 自身能力，不伪装成 NewAPI 同步结果。

推荐的计划任务 / 手动修复命令：

```powershell
pwsh -File D:\AgentWork\scripts\refresh-codex-model-menu-cache.ps1 -Once
```

Python `relay_gate.py` 本地 CLI 已归档到 `D:\AgentWork\_archive\tools-relay-gate-python\`。自动同步若仍依赖计划任务，应改为调用 `refresh-codex-model-menu-cache.ps1 -Once`（上游集合源 = `relay-gate models list`）。旧 `codex-catalog task install` 不在 Rust CLI。

计划任务损坏或被删除时，重建 Windows 计划任务直接指向 `pwsh -File D:\AgentWork\scripts\refresh-codex-model-menu-cache.ps1 -Once`，不要调用已退役的 `codex-catalog task`。

### 5. 本次补齐的模型元数据

| 模型 | 本地 context_window | 推理等级 | 依据 |
|---|---:|---|---|
| `gpt-5.6-sol` | 216000 (Codex soft) / 272000 (product rated) | low, medium, high, xhigh, max, ultra | OpenAI product context reverted 372k→272k (2026-07); Codex soft compaction 256k; Sol 默认 low，multi-agent v2 |
| `gpt-5.6-terra` | 216000 (Codex soft) / 272000 (product rated) | low, medium, high, xhigh, max, ultra | Align with Sol product limit; Terra 默认 medium，multi-agent v2 |
| `gpt-5.6-luna` | 216000 (Codex soft) / 272000 (product rated) | low, medium, high, xhigh, max | Align with Sol product limit；当前 CPA live probe 返回 `auth_unavailable`，目录保留元数据但 `visibility=hide` |
| `grok-4.5` | 500000 | low, medium, high, xhigh | 当前 CLIProxyAPI xAI provider surface registry + 通用四档下限 |
| `grok-4.3` | 1000000 | none, low, medium, high, xhigh | 当前 CLIProxyAPI/xAI provider surface + 通用四档下限 |
| `grok-3-mini` | 1000000 | none, low, medium, high, xhigh | 实际 `/v1/responses` 返回模型为 `grok-4.3`，按当前别名目标收口 |
| `grok-3-mini-fast` | 1000000 | none, low, medium, high, xhigh | 实际 `/v1/responses` 返回模型为 `grok-4.3`，按当前别名目标收口 |
| `workbuddy-glm-5.2` | 400000 | none, low, medium, high, xhigh | 上游 GLM-5.2 额定 500K；Codex 使用 400K 软压缩触发值留出异步压缩余量 |

### 6. Codex++ 消费本地目录


Codex++ 日志（`C:\Users\84618\.codex-session-delete\codex-plus.log`）持续调用 `/codex-model-catalog` bridge endpoint。settings.json 的 `relayProfiles.configContents` 包含完整 config.toml 正文，`providerSyncEnabled` 当前为 `false`（不自动写 config.toml，靠手动切换 provider 时写）。

## 各组件职责

| 组件 | 职责 | 是否控制 UI 菜单 |
|---|---|---|
| Codex++（codex-plus-plus） | CDP 注入 Statsig，monkey-patch `getDynamicConfig`，消费 `settings.json relayProfiles[].modelList` | **是** |
| cc-switch | 持有当前 Codex provider 的配置和可选简化 `modelCatalog` 规格；切换 provider 时仍可按模板生成 catalog | 否 |
| config.toml + model_catalog_json | `base_url`、`wire_api`、`model` 负责请求路由；`model_catalog_json` 只负责让 Codex 加载自定义模型元数据 | 否 |
| cc-switch-model-catalog.json | 本地完整 Codex schema；由现有完整模板克隆，并用 cc-switch 简化规格和 NewAPI 模型集合覆盖 | 否 |
| models_cache.json | 本地缓存投影，现与 `cc-switch-model-catalog.json` 保持同源 | 否 |
| relay-gate Rust CLI | 原子面：`models list` 等 NewAPI 操作 | 否 |
| refresh-codex-model-menu-cache.ps1 | 组合目录生成器：NewAPI model ids + cc-switch optional specs + local full template -> catalog/models_cache/settings | 否 |

## 给别人电脑配置的步骤

只有一条路：装 Codex++。

1. 安装 `codex-plus-plus.exe` + `codex-plus-plus-manager.exe`
2. 在 Codex++ 管理器里加供应商：base_url、API key、模型列表（对应 settings.json 的 `relayProfiles` 字段）
3. 用 Codex++ 的启动按钮启动 Codex（它会加 `--remote-debugging-port` 参数并做 CDP 注入）

config.toml/catalog/auth 三个文件 Codex++ 会自动写进 `~/.codex/`（和 cc-switch 写的一样），但关键是 CDP 注入——这一步 cc-switch 不做，所以 cc-switch 单独不够。

如果对方不想装 Codex++，理论上可以手写一个等价脚本：带 `--remote-debugging-port=端口` 启动 Codex.exe，然后用 CDP 连进去执行那段 monkey-patch `getDynamicConfig` 的 JS。但这本质上就是把 Codex++ 的核心逻辑重写一遍。

## 关键路径

- Codex++ 程序：`D:\software\Codex++\codex-plus-plus.exe`、`codex-plus-plus-manager.exe`
- Codex++ settings：`C:\Users\84618\.codex-session-delete\settings.json`
- Codex++ 状态：`C:\Users\84618\.codex-session-delete\latest-status.json`（含 debug_port）
- Codex++ 日志：`C:\Users\84618\.codex-session-delete\codex-plus.log`
- cc-switch 程序：`D:\software\cc-switch\cc-switch.exe`
- cc-switch 数据库：`C:\Users\84618\.cc-switch\cc-switch.db`（providers 表，app_type='codex'）
- Codex config：`C:\Users\84618\.codex\config.toml`
- Codex catalog：`C:\Users\84618\.codex\cc-switch-model-catalog.json`
- Codex auth：`C:\Users\84618\.codex\auth.json`
- Codex models_cache：`C:\Users\84618\.codex\models_cache.json`
- relay-gate CLI：`relay-gate`（源码 `D:\AgentWork\tools\relay-gate\`，Rust；唯一安装点 `D:\AgentWork\state\cargo\home\bin`）
- 上游模型集合：`sigil exec RELAY_GATE_CALLER_TOKEN --apply -- relay-gate --output json models list`
- 本地目录投影：`pwsh -File D:\AgentWork\scripts\refresh-codex-model-menu-cache.ps1 -Once`
- CodeBuddy 单独投影：`D:\AgentWork\tools\relay-gate\scripts\sync-codebuddy-models.ps1`
- 计划任务建议 action：上述 PS1 `-Once`（旧 pythonw/relay_gate.py / codex-catalog task 已退役）
- 同步状态目录：`%LOCALAPPDATA%\CodexModelMenuCacheWatcher\` / `%LOCALAPPDATA%\RelayGate\`
- Pi NewAPI 模型：`C:\Users\84618\.pi\agent\models.json`
- CodeBuddy 第三方模型：`C:\Users\84618\.codebuddy\models.json`
- WorkBuddy 第三方模型：`C:\Users\84618\.workbuddy\models.json`
- 归档 Python CLI：`D:\AgentWork\_archive\tools-relay-gate-python\`

## 验证方法

先跑 relay-gate 单元测试和 dry-run：

```powershell
cargo test --manifest-path D:\AgentWork\tools\relay-gate\Cargo.toml
sigil exec RELAY_GATE_CALLER_TOKEN --apply -- relay-gate --output json models list
pwsh -File D:\AgentWork\scripts\refresh-codex-model-menu-cache.ps1 -Once -DryRun
```

验证覆盖：Rust `cargo test` 覆盖 envelope/redact/catalog anchors；组合层 PS1 负责模板合成与多消费者投影。实际应用后应确认 catalog/models_cache 与 `models list` 集合一致，且内容无变时不重复写盘。

改动 `config.toml`、`model_catalog_json`、cc-switch 当前 provider 或 relay-gate catalog 逻辑后，再跑：


```powershell
python -c "import pathlib,tomllib; tomllib.loads(pathlib.Path(r'C:\Users\84618\.codex\config.toml').read_text(encoding='utf-8')); print('toml_ok')"
codex exec --skip-git-repo-check "ping"
```

`model_catalog_json` 不负责 NewAPI 请求路由，也不控制前端白名单；但只要要让 Codex 加载 relay-gate 生成的上下文窗口、推理级别和工具能力元数据，它就是该 catalog 的入口。`toml_ok` 只证明 TOML 可解析，`codex exec` 才证明该入口和模型调用都能工作。

通过 CDP 读取 Codex 运行时状态（无损，不改任何东西）：

```python
import json, urllib.request, websocket

port = 53704  # 从 latest-status.json 读 debug_port
targets = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=5))
page = next((t for t in targets if t.get('url') == 'app://-/index.html'), targets[0])
ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=5)

# 读 Statsig 客户端的 getDynamicConfig 返回值
expr = r"""(() => {
  const s = window.__STATSIG__;
  const client = s.instances[Object.keys(s.instances)[0]];
  const cfg = client.getDynamicConfig('107580212', {disableExposureLog: true});
  return {
    patched: !!client.__codexLocalModelCatalogPatched,
    codexPlusMarks: Object.keys(window).filter(k => k.includes('codexPlus')),
    available_models: cfg.__value && cfg.__value.available_models,
    default_model: cfg.__value && cfg.__value.default_model
  };
})()"""

ws.send(json.dumps({'id':1, 'method':'Runtime.evaluate',
                    'params': {'expression': expr, 'returnByValue': True}}))
print(json.dumps(json.loads(ws.recv())['result']['result']['value'], indent=2))
ws.close()
```

如果 `available_models` 含第三方模型且 `codexPlusMarks` 非空，说明 Codex++ 注入生效。
如果 `available_models` 只有官方 5 个且 `codexPlusMarks` 为空，说明只有 cc-switch/手动配置，菜单不会显示第三方模型。
