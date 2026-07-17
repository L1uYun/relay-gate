# CLIProxyAPI (CPA) Management

Owner: `D:\AgentWork\tools\relay-gate`
Live instance: `xiaolab-japan` docker `cli-proxy-api` (`eceasy/cli-proxy-api:v7.2.73`)
Bind: host `127.0.0.1:8317` only (not public)
Management secret: Sigil `cliproxy-mgmt-key` (config stores bcrypt under `remote-management.secret-key`)
Caller API key for NewAPI upstream: Sigil `cliproxy-api-key` / `cpa-api-key`

## Access model

CPA management is intentionally local-only. From this Windows workspace:

```powershell
# Preferred: auto transport (direct if reachable, else ssh xiaolab-japan)
relay-gate --output json cliproxy doctor
relay-gate cliproxy auth-files list
relay-gate cliproxy api-keys list
relay-gate cliproxy config get
relay-gate cliproxy logs recent
relay-gate cliproxy debug

# Force SSH to the CPA host
relay-gate --output json cliproxy --transport ssh doctor

# Optional local tunnel then direct
ssh -N -L 18317:127.0.0.1:8317 xiaolab-japan
relay-gate cliproxy --transport direct --cliproxy-base-url http://127.0.0.1:18317 doctor
```

Auth header: `Authorization: Bearer <mgmt-key>` (raw key also accepted by v7.2.73).

Do not put the management key in shell history, docs, or git. Use Sigil only.

## Command map (relay-gate cliproxy)

| Command | CPA path | Notes |
|---------|----------|-------|
| `doctor` | GET `/v0/management/auth-files` + surface probes | Summary counts only |
| `auth-files list` | GET `/v0/management/auth-files` | Provider/status summary |
| `auth-files set-status` | PATCH `/v0/management/auth-files/status` | dry-run default; `--apply` to write |
| `api-keys list` | GET `/v0/management/api-keys` | fingerprints/lengths only |
| `config get` | GET `/v0/management/config` | redacted tree |
| `logs recent` | GET `/v0/management/logs` or `/request-error-logs` | bounded |
| `debug` | GET `/v0/management/debug` | redacted |

## OAuth / login notes

CPA management UI (`/static/management.html`) drives provider OAuth:

- GET `/{provider}-auth-url` under `/v0/management`
- POST `/oauth-callback`
- Auth files land in host bind ` /root/cli-proxy-api/auths` → container `/root/.cli-proxy-api`

Operators should complete browser OAuth via the management UI or vendor CLI, then verify with `relay-gate cliproxy auth-files list`. This CLI does not automate browser login.

## Management UI route inventory (v7.2.73 SPA)

Verified from container `/CLIProxyAPI/static/management.html`:

- GET/DELETE `/auth-files`, PATCH `/auth-files/status`, PATCH `/auth-files/fields`, POST form `/auth-files`
- GET `/api-keys`, PUT/PATCH `/api-keys`, DELETE `/api-keys?index=`
- GET `/config`, PUT `/config.yaml`
- GET `/logs`, DELETE `/logs`, PUT `/request-log`, GET `/request-error-logs`
- GET `/get-auth-status`, GET `/latest-version`, GET `/nodes`, GET `/plugins`, ...
- Provider key helpers: `/codex-api-key`, `/claude-api-key`, `/gemini-api-key`, `/vertex-api-key`, `/xai-api-key`

## Cloudflare scoped-token minting pattern

CPA itself is not published on Cloudflare. When a public edge is required for other l1uyun services, mint a **scoped** Cloudflare API token with least privilege and store it in Sigil — never in git or shell history.

Local owner pattern:

1. Prefer an existing factory/meta token only as a mint parent: Sigil env/secret names such as `CLOUDFLARE_TOKEN_FACTORY_L1UYUN` / `CLOUDFLARE_META_TOKEN_L1UYUN`.
2. Create a child token scoped to the minimum Zone/Account resources and permissions needed (for example DNS Edit on one zone, or Tunnel Edit for one account).
3. Store the child token as its own Sigil secret (`cloudflare-api-token-...`) and bind an env name for runtime.
4. Verify with `sigil cloudflare token verify --token-cred <NAME>` before any write.
5. DNS writes always go through Sigil with `--dry-run` first:

```powershell
sigil cloudflare token verify --token-cred CLOUDFLARE_API_TOKEN_XIAOLAB_SSL
sigil cloudflare dns set-a <name> <ip> --zone <zone> --token-cred CLOUDFLARE_API_TOKEN_XIAOLAB_SSL --dry-run
```

Do not reuse broad account tokens for routine DNS/tunnel edits when a narrower child token exists.

## Safety

- Write commands default to dry-run (`auth-files set-status`).
- List/get paths redact secrets.
- Failed management auth can IP-ban the caller for ~30 minutes; avoid brute-forcing headers from the host.
- NewAPI channels 29/30/31/33 point at `http://cli-proxy-api:8317` on the docker network; management stays on the host loopback.

## Related

- NewAPI channel inventory: relay-gate skill `references/channels-and-models.md`
- NewAPI admin CLI remains the default `relay-gate channels|tokens|logs|...` surface