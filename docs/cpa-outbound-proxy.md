# CPA 出站代理与 antigravity 出口选型

> **专题背景（2026-09-03）**：`gemini-3.8-flash-high` 经 NewAPI→CPA(ch30)→antigravity→Google `cloudcode-pa.googleapis.com` 一直返 `400 User location is not supported`。排查到根因是 **CPA 出口 IP 被 Google antigravity 端点的灰名单拒绝**，最后通过 mihomo 出站分流到特定机场出口家族（日本 HY2 移动联通）打通。本文沉淀该拓扑与排障方法论。

Owner: `D:\AgentWork\tools\relay-gate`（独立个人 infra，非 xiaolab）
Live host: `xiaolab-japan`（SSH alias `xiaolab-japan`，见 `~/.ssh/config`）
关键容器（docker，同 `newapi_default` 网络）：
- `cli-proxy-api` = CPA 主服务，IP `172.20.0.3`，host bind `127.0.0.1:8317`
- `cpa-xai-proxy` = 出口 mihomo，IP `172.20.0.2`，mixed-port `11090`，controller `19090`（**未映射 host**）
- `new-api` = NewAPI，host bind `127.0.0.1:3000`

---

## 链路拓扑

```
本地 Codex/WorkBuddy/Pi
   │  OpenAI-compatible
   ▼
NewAPI (new-api:3000) ── channel ch30/29 → cli-proxy-api:8317 (CPA)
                                              │ per-credential proxy_url
                                              ▼
                                    socks5://cpa-xai-proxy:11090 (mihomo)
                                              │ rule 分流
                        ┌─────────────────────┴─────────────────────┐
                        ▼                                          ▼
               group「CPA 指定节点」                       group「🇯🇵 Antigravity」
              (gpt/claude/xai 等)                      (antigravity gemini 专属)
                        │                                          │
                        ▼                                          ▼
              us-sui-panel-vless-reality                🇯🇵 日本HY2移动联通 (hysteria2)
              = 45.205.25.129 机房 IP                   = p01.lpylink.xyz 家宽出口
```

CPA 的 antigravity 凭证（auth JSON）各自带 `proxy_url`，全部指向 mihomo 的 mixed-port；真正的出口 IP 选择发生在 **mihomo 的 rule + proxy-group 层**，不是 CPA 层。

## 关键文件与配置

| 项 | 路径 / 值 |
|---|---|
| CPA auth 目录 | host `/root/cli-proxy-api/auths` → container `/root/.cli-proxy-api` |
| antigravity 凭证 | `/root/cli-proxy-api/auths/antigravity-*.json`（3 份：ardidang08 / doanhaiictn / liuyun91374286） |
| antigravity 凭证字段 | `proxy_url`（当前 `socks5://cpa-xai-proxy:11090`）、`type: antigravity`、`disabled: false` |
| mihomo 配置（bind mount） | host `/opt/newapi/cpa-xai-proxy/config.yaml` → container `/root/.config/mihomo/config.yaml` |
| mihomo mixed-port | `11090`（CPA 的 `proxy_url` 指向这里） |
| mihomo controller | `19090`，**未映射 host**，只能同网容器内调 |
| mihomo 持久化 cache 卷 | `cpa-xai-proxy-data:/root/.config/mihomo/cache` |

## 排障方法论：Google antigravity 端点对出口 IP 是灰名单

`cloudcode-pa.googleapis.com`（antigravity 执行端点）**不**因国家/区域一视同仁拒绝，而是**按出口 IP 的属性（ASN/机房/家宽）分层**。同一机场的不同出口家族，Google 判定结果可能相反。

2026-09-03 实测（对 `daily-cloudcode-pa.googleapis.com` / `cloudcode-pa.googleapis.com`）：

| 出口节点 | 协议/入口家族 | TCP 连通 | Google 判定 |
|---|---|---|---|
| `us-sui-panel-vless-reality` (45.205.25.129) | vless reality / Zenixcloud | ✅ | ❌ 400 |
| `🇯🇵 日本03-Gemini` (`ff70ea41bc.d24-09-99.am15boy.com`) | ss chacha20 / am15boy | ✅（实连 34.96.209.84） | ❌ 400 |
| `🇯🇵 日本Y01 \| IEPL` (`r.i.8.y...qpon`) | ss aes-256 / qpon | ❌ connection refused | — |
| `🇯🇵 日本Y01 \| IEPL-2` (`r.i.e.5...qpon`) | ss aes-256 / qpon | ❌ connection refused | — |
| ✅ `🇯🇵 日本HY2移动联通` (`p01.lpylink.xyz`) | hysteria2 / lpylink | ✅ | ✅ 200 |

**要点**：
- ❌ 不要断言"整个机场都行/都不行"。同机场多出口家族（qpon / am15boy / lpylink 三族），须**逐个实测**。
- ❌ 机场节点的"家宽 / Gemini 优化 / IEPL"标签**不可信**。am15boy 标"03-Gemini"仍被 400；真正通过的是 lpylink hysteria2。
- ❌ qpon 系列（Y01 IEPL/IEPL-2）在 xiaolab-japan **TCP 都连不上**（connection refused / DNS fallback IP 36.141.40.13 超时），但用户本机走同一节点 OK——**机场按客户端出口 IP 有白名单/区分策略**，xiaolab-japan 的 IP 被该入口排除。
- ⚠️ 用户说"我本机用这节点 OK"通常指普通 Google 服务（搜索/gstatic），**未必测过 cloudcode-pa 这条 antigravity 专属端点**——后者比 `generativelanguage.googleapis.com` 严格得多。排障前先问清测的是哪个端点。

## 生效的 mihomo 配置（2026-09-03，保留在生产）

`/opt/newapi/cpa-xai-proxy/config.yaml` 尾部（proxy-groups + rules）：

```yaml
proxy-groups:
- name: CPA 指定节点
  type: select
  proxies:
  - us-sui-panel-vless-reality
  - 🇺🇲 美国Y01 | IEPL | x1.5
- name: 🇯🇵 Antigravity
  type: fallback
  url: https://daily-cloudcode-pa.googleapis.com/
  interval: 15
  timeout: 6000
  proxies:
  - 🇯🇵 日本HY2移动联通        # 首选，Google 接受
  - 🇯🇵 日本03-Gemini
  - 🇯🇵 日本东京家宽
  - 🇯🇵 日本Y01 | IEPL
  - 🇯🇵 日本Y01 | IEPL-2
rules:
- DOMAIN,daily-cloudcode-pa.googleapis.com,🇯🇵 Antigravity
- DOMAIN-SUFFIX,cloudcode-pa.googleapis.com,🇯🇵 Antigravity
- MATCH,CPA 指定节点
```

要点：
- **两条 rule 缺一不可**：antigravity 实际用 `daily-cloudcode-pa.googleapis.com`（子域）。`DOMAIN-SUFFIX,cloudcode-pa.googleapis.com` 只匹配 `cloudcode-pa.googleapis.com` 自身，**不**匹配其子域 `daily-...`，所以必须显式加 `DOMAIN,daily-cloudcode-pa.googleapis.com` 那条。
- `proxies:` 段需已有 `🇯🇵 日本HY2移动联通` 与 `🇯🇵 日本Y01 | IEPL-2` 节点定义（后者原配置没有，是后补的）。
- `🇯🇵 Antigravity` 用 `fallback` 类型，`now` 指向首选（HY2）。其他模型走 `MATCH,CPA 指定节点`，不受影响。

## 关键坑

### 坑 1：mihomo 把 group 选择持久化到 cache——改 fallback 顺序必须清 cache 再重启

mihomo 把「哪个 proxy 被选中」存进 cache 卷。改了 fallback 候选顺序后**直接重启不会生效**——`now` 仍指旧 proxy（曾卡在 03-Gemini）。必须清 cache 再重启：

```bash
docker stop cpa-xai-proxy
docker exec cpa-xai-proxy rm -rf /root/.config/mihomo/cache
docker start cpa-xai-proxy
```

清完重启，fallback 才从 proxies 列表第一个（HY2）开始。验证 `now`：

```bash
docker exec cli-proxy-api bash /tmp/fetch.sh \
  "http://172.20.0.2:19090/proxies/%F0%9F%87%AF%F0%9F%87%B5%20Antigravity"
# 响应里 "now":"🇯🇵 日本HY2移动联通"
```

### 坑 2：mihomo controller 19090 未映射 host，只能同网容器内调

mihomo controller 在容器内监听 `19090`，但 **docker 未做端口映射**，host 上 curl `127.0.0.1:19090` 拿不到。必须从同 `newapi_default` 网络的容器（如 `cli-proxy-api`，172.20.0.3）访问 `172.20.0.2:19090`。而 `cli-proxy-api` 容器**没有 curl/wget/nc**，用 bash `/dev/tcp` 手拼 HTTP，或 docker cp 一个脚本进去：

```bash
# /tmp/fetch.sh 留在 cli-proxy-api 容器内（一次放好可复用）
# 简易 HTTP GET：URL 须是 http://host/path 形式
docker exec cli-proxy-api bash /tmp/fetch.sh "http://172.20.0.2:19090/proxies/%F0%9F%87%AF%F0%9F%87%B5%20Antigravity"
```

用到的 controller 端点：`GET /proxies/<urlencoded-name>`（查 group state）、`GET /connections`（看实际出口）、`GET /version`、`GET /configs`。

### 坑 3：改配置后 sed 直改含 `|` 的 proxy 名会炸

proxy 名含 `|`（如 `🇯🇵 日本Y01 | IEPL`），用 `sed -i 's|...|...|'` 时 `|` 既是定界符又出现在模式里 → `unknown option to s`。**改这种配置用本机 Edit + scp 上传**，别在远端 sed。

## 验证命令

本地（Windows）单次探活：

```python
# probe3.py 思路：对 NewAPI 发 gemini-3.8-flash-high 极简请求，看 HTTP 状态
# STATUS=200 通；STATUS=400 且 msg="User location is not supported" = 出口仍被拒
```

远端看 mihomo 实际出口：

```bash
# 实时连接（含 remoteDestination、chains、rule）
docker exec cli-proxy-api bash /tmp/fetch.sh "http://172.20.0.2:19090/connections"
# mihomo 日志确认 rule 命中
docker logs cpa-xai-proxy --since 60s 2>&1 | grep -E 'cloudcode-pa|Antigravity|HY2'
```

多模型回归（确认只改 gemini 没伤其他）：

```text
gemini-3.8-flash-high → OK
gpt-5.6-sol          → OK
qwen3.8-max          → OK
glm-5.3              → 内容空（与本任务无关的已知问题）
```

## 备份

改动前完整备份在 host：`/root/cli-proxy-api/backup-gemini20260903-111024/`（含原 config.yaml + 3 份 antigravity JSON）。本地工作副本：`D:\AgentWork\_tmp\gemini38-catalog\cpa-xai-config.bak.yaml`。

## Related

- CPA 管理面：`docs/cliproxy-management.md`
- NewAPI 渠道/模型清单：relay-gate skill `references/channels-and-models.md`
- catalog 投影真源：`D:\AgentWork\scripts\refresh-codex-model-menu-cache.ps1`（三张表），详见 relay-gate skill `references/topology-and-local-state.md`
