//! Relay Gate atomic CLI core.
//!
//! Agent-native Rust surface for the NewAPI gateway at
//! `https://newapi.l1uyun.top:8080`. Read primitives use GET; write primitives
//! are atomic POST/PUT operations that default to dry-run unless `--apply` is
//! set. Admin authentication resolves from the `SIGIL_ADMIN_TOKEN` environment
//! variable (falling back to `RELAY_GATE_ADMIN_TOKEN`) and is sent as a raw
//! `Authorization` header plus a `New-Api-User` header. The token is never
//! echoed in stdout, arguments, or diagnostics; a final redaction pass scrubs
//! any occurrence of the raw credential from the serialized JSON envelope.
//!
//! All commands emit a versioned JSON envelope by default:
//!
//! ```json
//! {
//!   "schema_version": "relay-gate.v1",
//!   "ok": true,
//!   "operation": "doctor",
//!   "data": { ... }
//! }
//! ```
//!
//! Errors are emitted as the same envelope with `ok: false` and an `error`
//! object whose `message` has had the raw credential redacted.

#![warn(clippy::all)]

use std::{fmt, time::Duration};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

pub mod catalog;
pub mod redact;
pub mod schema;

pub use catalog::context_window;
pub use redact::{redact_secret_field, redact_secret_in_text, secret_fingerprint};

/// Default NewAPI base URL.
pub const DEFAULT_BASE_URL: &str = "https://newapi.l1uyun.top:8080";

/// Default NewAPI admin user id sent as the `New-Api-User` header.
pub const DEFAULT_USER_ID: &str = "1";

/// Bound a local CLI invocation when the gateway stops responding.
pub const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

/// JSON envelope schema version emitted by every command.
pub const SCHEMA_VERSION: &str = "relay-gate.v1";

/// Write intent for mutation primitives.
///
/// Default is [`WriteMode::DryRun`]. Pass `--apply` at the CLI to land a write.
/// If both `--dry-run` and `--apply` are present, dry-run wins.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum WriteMode {
    /// Preview only; no POST/PUT is issued.
    DryRun,
    /// Land the mutation.
    Apply,
}

impl WriteMode {
    /// Resolve CLI flags. `--dry-run` wins over `--apply`; bare invocations dry-run.
    pub fn resolve(apply: bool, dry_run: bool) -> Self {
        if dry_run || !apply {
            Self::DryRun
        } else {
            Self::Apply
        }
    }

    pub fn is_apply(self) -> bool {
        matches!(self, Self::Apply)
    }
}

fn dry_run_preview(method: &str, path: &str, body: Value) -> Value {
    json!({
        "dry_run": true,
        "applied": false,
        "method": method,
        "path": path,
        "body": body,
        "note": "pass --apply to land; --dry-run wins over --apply",
    })
}

fn redact_body_secrets(mut body: Value) -> Value {
    if let Some(obj) = body.as_object_mut() {
        if let Some(key) = obj.get("key").cloned() {
            if let Some(s) = key.as_str() {
                if !s.is_empty() {
                    obj.insert(
                        "key".into(),
                        json!({
                            "present": true,
                            "sha256": secret_fingerprint(s),
                            "redacted": true,
                        }),
                    );
                }
            }
        }
    }
    body
}


/// Error type for the atomic CLI core.
///
/// Messages are constructed with the raw credential already redacted so that
/// [`Error`]`::Display` output is safe to surface in diagnostics.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// Credential could not be resolved from the environment.
    #[error("credential: {0}")]
    Credential(String),
    /// HTTP transport failure or non-2xx response.
    #[error("http {path}: {status} {message}")]
    Http {
        status: u16,
        path: String,
        message: String,
    },
    /// Response body was not valid JSON.
    #[error("parse {path}: {message}")]
    Parse { path: String, message: String },
    /// NewAPI returned `success: false` in a 2xx body.
    #[error("newapi {path}: {message}")]
    NewApi { path: String, message: String },
    /// CLI selector input was malformed.
    #[error("selector: {0}")]
    Selector(String),
}

impl Error {
    /// Stable machine-readable code for this error variant.
    pub fn code(&self) -> &'static str {
        match self {
            Self::Credential(_) => "credential",
            Self::Http { .. } => "http_error",
            Self::Parse { .. } => "parse_error",
            Self::NewApi { .. } => "newapi_error",
            Self::Selector(_) => "selector_error",
        }
    }

    /// HTTP status code if this is an HTTP error.
    pub fn http_status(&self) -> Option<u16> {
        match self {
            Self::Http { status, .. } => Some(*status),
            _ => None,
        }
    }

    /// Request path if this error carries one.
    pub fn path(&self) -> Option<&str> {
        match self {
            Self::Http { path, .. } | Self::Parse { path, .. } | Self::NewApi { path, .. } => {
                Some(path)
            }
            _ => None,
        }
    }
}

/// Redacted error envelope carried inside [`Envelope::error`].
#[derive(Debug, Clone, Serialize)]
pub struct ErrorEnvelope {
    pub code: &'static str,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub http_status: Option<u16>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
}

impl From<&Error> for ErrorEnvelope {
    fn from(err: &Error) -> Self {
        Self {
            code: err.code(),
            message: err.to_string(),
            http_status: err.http_status(),
            path: err.path().map(str::to_string),
        }
    }
}

/// Versioned JSON envelope emitted by every command.
#[derive(Debug, Clone, Serialize)]
pub struct Envelope {
    pub schema_version: &'static str,
    pub ok: bool,
    pub operation: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ErrorEnvelope>,
}

impl Envelope {
    /// Build a success envelope for `operation` carrying `data`.
    pub fn ok(operation: &'static str, data: Value) -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            ok: true,
            operation,
            data: Some(data),
            error: None,
        }
    }

    /// Build an error envelope for `operation` carrying `err`.
    pub fn err(operation: &'static str, err: Error) -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            ok: false,
            operation,
            data: None,
            error: Some(ErrorEnvelope::from(&err)),
        }
    }
}

/// HTTP client for the NewAPI admin API.
#[derive(Clone)]
pub struct Client {
    base_url: String,
    admin_token: String,
    user_id: String,
    http: reqwest::blocking::Client,
}

impl fmt::Debug for Client {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Client")
            .field("base_url", &self.base_url)
            .field("admin_token", &redact::redacted_preview(&self.admin_token))
            .field("user_id", &self.user_id)
            .finish_non_exhaustive()
    }
}

impl Client {
    /// Build a client from explicit credential material.
    ///
    /// In typical use prefer [`Client::from_env`]. This constructor exists for
    /// tests and hosts that already hold the resolved token.
    pub fn new(
        base_url: impl Into<String>,
        admin_token: impl Into<String>,
        user_id: impl Into<String>,
    ) -> Result<Self, Error> {
        let base_url = base_url.into();
        let admin_token = admin_token.into();
        let user_id = user_id.into();
        if admin_token.is_empty() {
            return Err(Error::Credential(
                "admin token is empty; set SIGIL_ADMIN_TOKEN or RELAY_GATE_ADMIN_TOKEN".into(),
            ));
        }
        if base_url.is_empty() {
            return Err(Error::Credential("base_url is empty".into()));
        }
        let http = reqwest::blocking::Client::builder()
            .connect_timeout(DEFAULT_REQUEST_TIMEOUT)
            .timeout(DEFAULT_REQUEST_TIMEOUT)
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|e| Error::Credential(format!("http client build failed: {e}")))?;
        Ok(Self {
            base_url,
            admin_token,
            user_id,
            http,
        })
    }

    /// Resolve a client from the process environment.
    ///
    /// Reads `SIGIL_ADMIN_TOKEN` first, then `RELAY_GATE_ADMIN_TOKEN`.
    /// `base_url` overrides `RELAY_GATE_BASE_URL`; `user_id` overrides
    /// `RELAY_GATE_USER_ID` (default `"1"`).
    pub fn from_env(base_url: Option<&str>) -> Result<Self, Error> {
        let token = std::env::var("SIGIL_ADMIN_TOKEN")
            .or_else(|_| std::env::var("RELAY_GATE_ADMIN_TOKEN"))
            .map_err(|_| {
                Error::Credential(
                    "admin token not set; export SIGIL_ADMIN_TOKEN or RELAY_GATE_ADMIN_TOKEN"
                        .into(),
                )
            })?;
        let base = base_url
            .map(str::to_string)
            .or_else(|| std::env::var("RELAY_GATE_BASE_URL").ok())
            .unwrap_or(DEFAULT_BASE_URL.to_string());
        let user_id = std::env::var("RELAY_GATE_USER_ID").unwrap_or(DEFAULT_USER_ID.to_string());
        Self::new(base, token, user_id)
    }

    /// Base URL the client targets.
    /// Build a client without admin token validation.
    ///
    /// Used for caller-only commands (models list) that do not call admin endpoints.
    pub fn new_caller(base_url: impl Into<String>) -> Result<Self, Error> {
        let base_url = base_url.into();
        if base_url.is_empty() {
            return Err(Error::Credential("base_url is empty".into()));
        }
        let http = reqwest::blocking::Client::builder()
            .connect_timeout(DEFAULT_REQUEST_TIMEOUT)
            .timeout(DEFAULT_REQUEST_TIMEOUT)
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|e| Error::Credential(format!("http client build failed: {e}")))?;
        Ok(Self {
            base_url,
            admin_token: String::new(),
            user_id: String::new(),
            http,
        })
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    /// Redact any occurrence of the raw credential in `text`.
    ///
    /// Used as a final scrubbing pass over serialized stdout/stderr so a
    /// server-echoed token cannot leak through an error envelope.
    pub fn redact(&self, text: &str) -> String {
        redact_secret_in_text(text, &self.admin_token)
    }

    /// Issue a GET request and return the parsed JSON body.
    ///
    /// This is the only HTTP primitive in the slice; no code path issues a
    /// mutation request. Non-2xx responses and non-JSON bodies produce an
    /// [`Error::Http`] / [`Error::Parse`] whose message has the raw credential
    /// redacted. NewAPI `success: false` bodies produce [`Error::NewApi`].
    pub fn get(&self, path: &str, query: &[(&str, String)]) -> Result<Value, Error> {
        let url = format!("{}{}", self.base_url.trim_end_matches('/'), path);
        let mut req = self
            .http
            .get(&url)
            .header("Authorization", &self.admin_token)
            .header("New-Api-User", &self.user_id);
        if !query.is_empty() {
            let pairs: Vec<(&str, &str)> = query.iter().map(|(k, v)| (*k, v.as_str())).collect();
            req = req.query(&pairs);
        }
        let resp = req.send().map_err(|e| Error::Http {
            status: 0,
            path: path.to_string(),
            message: self.redact(&format!("transport: {e}")),
        })?;
        let status = resp.status().as_u16();
        let text = resp.text().map_err(|e| Error::Http {
            status,
            path: path.to_string(),
            message: self.redact(&format!("response body unreadable: {e}")),
        })?;
        if status >= 400 {
            return Err(Error::Http {
                status,
                path: path.to_string(),
                message: self.redact(&text),
            });
        }
        let parsed: Value = serde_json::from_str(&text).map_err(|_| {
            let preview: String = text.chars().take(120).collect();
            Error::Parse {
                path: path.to_string(),
                message: self.redact(&format!(
                    "non-JSON response body (status {status}): {preview}"
                )),
            }
        })?;
        if let Some(false) = parsed.get("success").and_then(Value::as_bool) {
            let message = parsed
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("(no message)")
                .to_string();
            return Err(Error::NewApi {
                path: path.to_string(),
                message: self.redact(&message),
            });
        }
        Ok(parsed)
    }

    /// Issue a POST request with an optional JSON body and return the parsed JSON body.
    ///
    /// Mirrors [`get`](Self::get) for auth, timeout, redaction, and NewAPI
    /// `success: false` handling. Used by create/status/key operations.
    pub fn post(&self, path: &str, body: Option<&Value>) -> Result<Value, Error> {
        let url = format!("{}{}", self.base_url.trim_end_matches('/'), path);
        let mut req = self
            .http
            .post(&url)
            .header("Authorization", &self.admin_token)
            .header("New-Api-User", &self.user_id);
        if let Some(b) = body {
            req = req.json(b);
        }
        self.send_and_parse(req, path)
    }

    /// Issue a PUT request with a JSON body and return the parsed JSON body.
    ///
    /// Mirrors [`get`](Self::get) for auth, timeout, redaction, and NewAPI
    /// `success: false` handling. Used by channel/token update and option set.
    pub fn put(&self, path: &str, body: &Value) -> Result<Value, Error> {
        let url = format!("{}{}", self.base_url.trim_end_matches('/'), path);
        let req = self
            .http
            .put(&url)
            .header("Authorization", &self.admin_token)
            .header("New-Api-User", &self.user_id)
            .json(body);
        self.send_and_parse(req, path)
    }

    /// Shared send + parse + redact logic for post/put.
    fn send_and_parse(
        &self,
        req: reqwest::blocking::RequestBuilder,
        path: &str,
    ) -> Result<Value, Error> {
        let resp = req.send().map_err(|e| Error::Http {
            status: 0,
            path: path.to_string(),
            message: self.redact(&format!("transport: {e}")),
        })?;
        let status = resp.status().as_u16();
        let text = resp.text().map_err(|e| Error::Http {
            status,
            path: path.to_string(),
            message: self.redact(&format!("response body unreadable: {e}")),
        })?;
        if status >= 400 {
            return Err(Error::Http {
                status,
                path: path.to_string(),
                message: self.redact(&text),
            });
        }
        let parsed: Value = serde_json::from_str(&text).map_err(|_| {
            let preview: String = text.chars().take(120).collect();
            Error::Parse {
                path: path.to_string(),
                message: self.redact(&format!("non-JSON response body (status {status}): {preview}")),
            }
        })?;
        if let Some(false) = parsed.get("success").and_then(Value::as_bool) {
            let message = parsed
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("(no message)")
                .to_string();
            return Err(Error::NewApi {
                path: path.to_string(),
                message: self.redact(&message),
            });
        }
        Ok(parsed)
    }
}

/// Selector for `channels list`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct ChannelsListSelector {
    pub page: Option<u32>,
    pub page_size: Option<u32>,
    pub status: Option<i32>,
    #[serde(rename = "type")]
    pub type_filter: Option<i32>,
    pub group: Option<String>,
    pub id_sort: Option<bool>,
}

/// Selector for `channels get`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct ChannelsGetSelector {
    pub id: Option<u64>,
}

/// Selector for `tokens list`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct TokensListSelector {
    pub keyword: Option<String>,
    pub page: Option<u32>,
    pub page_size: Option<u32>,
}

/// Selector for `tokens get`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct TokensGetSelector {
    pub id: Option<u64>,
}

/// Selector for `logs recent`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct LogsRecentSelector {
    pub page: Option<u32>,
    pub page_size: Option<u32>,
    /// Use `/api/log/self` (caller-scoped) instead of admin-wide `/api/log/`.
    #[serde(rename = "self")]
    pub self_scope: Option<bool>,
}

fn u32_str(v: Option<u32>) -> Option<String> {
    v.map(|n| n.to_string())
}

fn i32_str(v: Option<i32>) -> Option<String> {
    v.map(|n| n.to_string())
}

fn bool_lower(v: Option<bool>) -> Option<String> {
    v.map(|b| b.to_string().to_lowercase())
}

fn data_object(value: &Value) -> &Value {
    value.get("data").unwrap_or(&Value::Null)
}

/// `doctor`: structural preflight. Calls `/api/status` then `/api/channel/`
/// (page_size=1). Returns a compact summary object.
pub fn doctor(client: &Client) -> Result<Value, Error> {
    let status = client.get("/api/status", &[])?;
    let channels = client.get(
        "/api/channel/",
        &[("p", "1".into()), ("page_size", "1".into())],
    )?;
    let data = data_object(&channels);
    Ok(json!({
        "base_url": client.base_url(),
        "service_success": status.get("success"),
        "version": status.get("data").and_then(|d| d.get("version")),
        "setup": status.get("data").and_then(|d| d.get("setup")),
        "admin_api": "raw Authorization access_token + New-Api-User",
        "user_id": client.user_id.clone(),
        "channels_total": data.get("total"),
    }))
}

/// `channels list`.
pub fn channels_list(client: &Client, sel: &ChannelsListSelector) -> Result<Value, Error> {
    let path = "/api/channel/";
    let mut query: Vec<(&str, String)> = Vec::with_capacity(6);
    query.push(("p", u32_str(sel.page).unwrap_or("1".into())));
    query.push(("page_size", u32_str(sel.page_size).unwrap_or("100".into())));
    if let Some(s) = i32_str(sel.status) {
        query.push(("status", s));
    }
    if let Some(t) = i32_str(sel.type_filter) {
        query.push(("type", t));
    }
    if let Some(g) = &sel.group {
        query.push(("group", g.clone()));
    }
    if let Some(b) = bool_lower(sel.id_sort) {
        query.push(("id_sort", b));
    }
    let data = client.get(path, &query)?;
    let data_obj = data_object(&data);
    let items = data_obj
        .get("items")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let summaries: Vec<Value> = items.iter().map(channel_summary).collect();
    Ok(json!({
        "total": data_obj.get("total"),
        "items": summaries,
    }))
}

/// `channels get`.
pub fn channels_get(client: &Client, sel: &ChannelsGetSelector) -> Result<Value, Error> {
    let id = sel
        .id
        .ok_or_else(|| Error::Selector("channels.get requires `id`".into()))?;
    let path = format!("/api/channel/{id}");
    let data = client.get(&path, &[])?;
    let channel = data_object(&data);
    Ok(redact_channel(channel))
}

/// `tokens list`.
pub fn tokens_list(client: &Client, sel: &TokensListSelector) -> Result<Value, Error> {
    let path = "/api/token/search";
    let query = vec![
        ("keyword", sel.keyword.clone().unwrap_or_default()),
        ("p", u32_str(sel.page).unwrap_or("1".into())),
        ("size", u32_str(sel.page_size).unwrap_or("100".into())),
    ];
    let data = client.get(path, &query)?;
    let data_obj = data_object(&data);
    let items = data_obj
        .get("items")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let summaries: Vec<Value> = items.iter().map(token_summary).collect();
    Ok(json!({
        "total": data_obj.get("total"),
        "page": data_obj.get("page"),
        "page_size": data_obj.get("page_size"),
        "items": summaries,
    }))
}

/// `tokens get`.
pub fn tokens_get(client: &Client, sel: &TokensGetSelector) -> Result<Value, Error> {
    let id = sel
        .id
        .ok_or_else(|| Error::Selector("tokens.get requires `id`".into()))?;
    let path = format!("/api/token/{id}");
    let data = client.get(&path, &[])?;
    let token = data_object(&data);
    Ok(redact_token(token))
}

/// `logs recent`.
pub fn logs_recent(client: &Client, sel: &LogsRecentSelector) -> Result<Value, Error> {
    let path = if sel.self_scope.unwrap_or(false) {
        "/api/log/self"
    } else {
        "/api/log/"
    };
    let query: Vec<(&str, String)> = vec![
        ("p", u32_str(sel.page).unwrap_or("1".into())),
        ("page_size", u32_str(sel.page_size).unwrap_or("100".into())),
    ];
    let data = client.get(path, &query)?;
    let data_obj = data_object(&data);
    let items = data_obj
        .get("items")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let summaries: Vec<Value> = items.iter().map(log_summary).collect();
    Ok(json!({
        "total": data_obj.get("total"),
        "page": data_obj.get("page"),
        "page_size": data_obj.get("page_size"),
        "items": summaries,
    }))
}


// ---------------------------------------------------------------------------
// Write primitives (Phase 1-3)
// ---------------------------------------------------------------------------

/// Selector for `channels create`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct ChannelsCreateSelector {
    pub name: Option<String>,
    pub base_url: Option<String>,
    /// API key (will be redacted in output).
    pub key: Option<String>,
    pub models: Option<String>,
    pub group: Option<String>,
    pub r#type: Option<i32>,
    pub priority: Option<i32>,
    pub weight: Option<i32>,
}

/// Selector for `channels update`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct ChannelsUpdateSelector {
    pub id: Option<u64>,
    /// Fields to update, as a JSON object.
    pub fields: Option<Value>,
}

/// Selector for `channels status`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct ChannelsStatusSelector {
    pub id: Option<u64>,
    /// New status: 1=enabled, 2=disabled, 3=auto-disabled.
    pub status: Option<i32>,
}

/// Selector for `channels test`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct ChannelsTestSelector {
    pub id: Option<u64>,
    pub model: Option<String>,
}

/// Selector for `options list`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct OptionsListSelector {
    pub key: Option<String>,
}

/// Selector for `options set`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct OptionsSetSelector {
    pub key: Option<String>,
    pub value: Option<String>,
}

/// Selector for `tokens create`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct TokensCreateSelector {
    pub name: Option<String>,
    pub remain_quota: Option<i64>,
    pub unlimited_quota: Option<bool>,
    pub expired_time: Option<i64>,
    pub group: Option<String>,
}

/// Selector for `tokens update`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct TokensUpdateSelector {
    pub id: Option<u64>,
    pub fields: Option<Value>,
}

/// Selector for `tokens key`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct TokensKeySelector {
    pub id: Option<u64>,
}

/// Selector for `logs stats`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct LogsStatsSelector {}

/// Selector for `models list`.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct ModelsListSelector {}

/// `channels create`: POST /api/channel/ with channel definition.
pub fn channels_create(
    client: &Client,
    sel: &ChannelsCreateSelector,
    mode: WriteMode,
) -> Result<Value, Error> {
    let name = sel
        .name
        .as_ref()
        .ok_or_else(|| Error::Selector("channels.create requires `name`".into()))?;
    let mut payload = json!({
        "name": name,
        "base_url": sel.base_url,
        "key": sel.key,
        "models": sel.models,
        "group": sel.group,
        "type": sel.r#type,
        "priority": sel.priority,
        "weight": sel.weight,
    });
    // Remove null fields — NewAPI rejects some nulls.
    if let Some(obj) = payload.as_object_mut() {
        obj.retain(|_, v| !v.is_null());
    }
    if !mode.is_apply() {
        return Ok(dry_run_preview(
            "POST",
            "/api/channel/",
            redact_body_secrets(payload),
        ));
    }
    let result = client.post("/api/channel/", Some(&payload))?;
    let data = data_object(&result);
    Ok(json!({
        "created": true,
        "applied": true,
        "dry_run": false,
        "channel": redact_channel(data),
    }))
}

/// `channels update`: PUT /api/channel/ with id + fields (PATCH semantics).
pub fn channels_update(
    client: &Client,
    sel: &ChannelsUpdateSelector,
    mode: WriteMode,
) -> Result<Value, Error> {
    let id = sel
        .id
        .ok_or_else(|| Error::Selector("channels.update requires `id`".into()))?;
    let fields = sel.fields.as_ref().ok_or_else(|| {
        Error::Selector("channels.update requires `fields` (JSON object)".into())
    })?;
    let mut payload = fields.clone();
    if let Some(obj) = payload.as_object_mut() {
        obj.insert("id".to_string(), json!(id));
        // NewAPI rejects status in PUT body; use channels status instead.
        obj.remove("status");
    }
    if !mode.is_apply() {
        let before = client
            .get(&format!("/api/channel/{id}"), &[])
            .ok()
            .map(|v| redact_channel(data_object(&v)));
        let mut preview = dry_run_preview("PUT", "/api/channel/", redact_body_secrets(payload));
        if let Some(obj) = preview.as_object_mut() {
            if let Some(before) = before {
                obj.insert("before".into(), before);
            }
        }
        return Ok(preview);
    }
    client.put("/api/channel/", &payload)?;
    // Re-read the channel to show the result.
    let after = client.get(&format!("/api/channel/{id}"), &[])?;
    let channel = data_object(&after);
    Ok(json!({
        "updated": true,
        "applied": true,
        "dry_run": false,
        "after": redact_channel(channel),
    }))
}

/// `channels status`: POST /api/channel/{id}/status.
pub fn channels_status(
    client: &Client,
    sel: &ChannelsStatusSelector,
    mode: WriteMode,
) -> Result<Value, Error> {
    let id = sel
        .id
        .ok_or_else(|| Error::Selector("channels.status requires `id`".into()))?;
    let status = sel.status.ok_or_else(|| {
        Error::Selector(
            "channels.status requires `status` (1=enabled, 2=disabled, 3=auto)".into(),
        )
    })?;
    let path = format!("/api/channel/{id}/status");
    let body = json!({"status": status});
    if !mode.is_apply() {
        return Ok(dry_run_preview("POST", &path, body));
    }
    client.post(&path, Some(&body))?;
    Ok(json!({
        "id": id,
        "status": status,
        "applied": true,
        "dry_run": false,
    }))
}

/// `channels test`: GET /api/channel/test/{id}?model=...
pub fn channels_test(client: &Client, sel: &ChannelsTestSelector) -> Result<Value, Error> {
    let id = sel
        .id
        .ok_or_else(|| Error::Selector("channels.test requires `id`".into()))?;
    let path = format!("/api/channel/test/{id}");
    let mut query: Vec<(&str, String)> = Vec::new();
    if let Some(m) = &sel.model {
        query.push(("model", m.clone()));
    }
    let data = client.get(&path, &query)?;
    Ok(json!({
        "id": id,
        "success": data.get("success"),
        "time": data.get("time"),
        "message": data.get("message"),
    }))
}

/// `options list`: GET /api/option/.
pub fn options_list(client: &Client, sel: &OptionsListSelector) -> Result<Value, Error> {
    let data = client.get("/api/option/", &[])?;
    let items = data
        .get("data")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let filtered: Vec<Value> = if let Some(key) = &sel.key {
        items
            .iter()
            .filter(|item| item.get("key").and_then(Value::as_str) == Some(key.as_str()))
            .cloned()
            .collect()
    } else {
        items
    };
    Ok(json!({
        "total": filtered.len(),
        "items": filtered,
    }))
}

/// `options set`: PUT /api/option/ with {key, value}.
pub fn options_set(
    client: &Client,
    sel: &OptionsSetSelector,
    mode: WriteMode,
) -> Result<Value, Error> {
    let key = sel
        .key
        .as_ref()
        .ok_or_else(|| Error::Selector("options.set requires `key`".into()))?;
    let value = sel
        .value
        .as_ref()
        .ok_or_else(|| Error::Selector("options.set requires `value`".into()))?;
    let body = json!({"key": key, "value": value});
    if !mode.is_apply() {
        return Ok(dry_run_preview("PUT", "/api/option/", body));
    }
    client.put("/api/option/", &body)?;
    Ok(json!({
        "key": key,
        "value": value,
        "applied": true,
        "dry_run": false,
    }))
}

/// `tokens create`: POST /api/token/.
pub fn tokens_create(
    client: &Client,
    sel: &TokensCreateSelector,
    mode: WriteMode,
) -> Result<Value, Error> {
    let name = sel
        .name
        .as_ref()
        .ok_or_else(|| Error::Selector("tokens.create requires `name`".into()))?;
    let mut payload = json!({
        "name": name,
        "remain_quota": sel.remain_quota,
        "unlimited_quota": sel.unlimited_quota,
        "expired_time": sel.expired_time,
        "group": sel.group,
    });
    if let Some(obj) = payload.as_object_mut() {
        obj.retain(|_, v| !v.is_null());
    }
    if !mode.is_apply() {
        return Ok(dry_run_preview("POST", "/api/token/", payload));
    }
    let result = client.post("/api/token/", Some(&payload))?;
    let data = data_object(&result);
    Ok(json!({
        "created": true,
        "applied": true,
        "dry_run": false,
        "token": redact_token(data),
    }))
}

/// `tokens update`: PUT /api/token/ with id + fields.
pub fn tokens_update(
    client: &Client,
    sel: &TokensUpdateSelector,
    mode: WriteMode,
) -> Result<Value, Error> {
    let id = sel
        .id
        .ok_or_else(|| Error::Selector("tokens.update requires `id`".into()))?;
    let fields = sel.fields.as_ref().ok_or_else(|| {
        Error::Selector("tokens.update requires `fields` (JSON object)".into())
    })?;
    let mut payload = fields.clone();
    if let Some(obj) = payload.as_object_mut() {
        obj.insert("id".to_string(), json!(id));
    }
    if !mode.is_apply() {
        // PATCH preview: show current state so the change is auditable before landing.
        let mut preview = dry_run_preview("PUT", "/api/token/", payload);
        let before = client
            .get(&format!("/api/token/{id}"), &[])
            .ok()
            .map(|d| redact_token(data_object(&d)));
        if let (Some(b), Some(o)) = (before, preview.as_object_mut()) {
            o.insert("before".to_string(), b);
        }
        return Ok(preview);
    }
    client.put("/api/token/", &payload)?;
    let after = client.get(&format!("/api/token/{id}"), &[])?;
    let token = data_object(&after);
    Ok(json!({
        "updated": true,
        "applied": true,
        "dry_run": false,
        "after": redact_token(token),
    }))
}

/// `tokens key`: POST /api/token/{id}/key — regenerate and return raw key.
pub fn tokens_key(
    client: &Client,
    sel: &TokensKeySelector,
    mode: WriteMode,
) -> Result<Value, Error> {
    let id = sel
        .id
        .ok_or_else(|| Error::Selector("tokens.key requires `id`".into()))?;
    let path = format!("/api/token/{id}/key");
    if !mode.is_apply() {
        return Ok(dry_run_preview("POST", &path, json!({})));
    }
    let result = client.post(&path, None)?;
    let key = result
        .get("data")
        .and_then(|d| d.get("key"))
        .and_then(Value::as_str)
        .unwrap_or("");
    Ok(json!({
        "id": id,
        "key": key,
        "applied": true,
        "dry_run": false,
        "note": "raw key returned; store in Sigil immediately",
    }))
}

/// `logs stats`: GET /api/log/stat.
pub fn logs_stats(client: &Client, _sel: &LogsStatsSelector) -> Result<Value, Error> {
    let data = client.get("/api/log/stat", &[])?;
    Ok(data_object(&data).clone())
}

/// `models list`: GET /v1/models using caller token (not admin).
/// `models list`: GET /v1/models using caller token (not admin).
///
/// The caller token resolves from `RELAY_GATE_CALLER_TOKEN` env var.
/// This is a separate credential from the admin token because /v1/models
/// is the caller API, not the admin API.
pub fn models_list(_client: &Client, _sel: &ModelsListSelector) -> Result<Value, Error> {
    let caller_token = std::env::var("RELAY_GATE_CALLER_TOKEN")
        .map_err(|_| Error::Credential(
            "caller token not set; export RELAY_GATE_CALLER_TOKEN (use sigil binding L1UYUN_NEWAPI_API_KEY)"
                .into(),
        ))?;
    let base_url = _client.base_url();
    let url = format!("{}/v1/models", base_url.trim_end_matches('/'));
    let http = reqwest::blocking::Client::builder()
        .connect_timeout(DEFAULT_REQUEST_TIMEOUT)
        .timeout(DEFAULT_REQUEST_TIMEOUT)
        .build()
        .map_err(|e| Error::Credential(format!("http client build failed: {e}")))?;
    let resp = http
        .get(&url)
        .header("Authorization", &caller_token)
        .send()
        .map_err(|e| Error::Http {
            status: 0,
            path: "/v1/models".into(),
            message: format!("transport: {e}"),
        })?;
    let status = resp.status().as_u16();
    let text = resp.text().map_err(|e| Error::Http {
        status,
        path: "/v1/models".into(),
        message: format!("response body unreadable: {e}"),
    })?;
    if status >= 400 {
        return Err(Error::Http {
            status,
            path: "/v1/models".into(),
            message: text,
        });
    }
    let parsed: Value = serde_json::from_str(&text).map_err(|_| {
        let preview: String = text.chars().take(120).collect();
        Error::Parse {
            path: "/v1/models".into(),
            message: format!("non-JSON response body (status {status}): {preview}"),
        }
    })?;
    let models = parsed.get("data").and_then(Value::as_array).cloned().unwrap_or_default();
    let ids: Vec<String> = models.iter()
        .filter_map(|m| m.get("id").and_then(Value::as_str).map(String::from))
        .collect();
    Ok(json!({
        "total": ids.len(),
        "models": ids,
    }))
}
fn pick_str(value: &Value, key: &str) -> Value {
    value.get(key).cloned().unwrap_or(Value::Null)
}

fn channel_summary(channel: &Value) -> Value {
    json!({
        "id": channel.get("id"),
        "name": channel.get("name"),
        "type": channel.get("type"),
        "base_url": channel.get("base_url"),
        "models": channel.get("models"),
        "group": channel.get("group"),
        "status": channel.get("status"),
        "priority": channel.get("priority"),
        "weight": channel.get("weight"),
        "tag": channel.get("tag"),
        "response_time": channel.get("response_time"),
        "test_time": channel.get("test_time"),
    })
}

fn token_summary(token: &Value) -> Value {
    let mut obj = serde_json::Map::new();
    for key in [
        "id",
        "name",
        "status",
        "expired_time",
        "remain_quota",
        "unlimited_quota",
        "model_limits_enabled",
        "model_limits",
        "allow_ips",
        "group",
        "cross_group_retry",
        "used_quota",
        "accessed_time",
    ] {
        obj.insert(key.to_string(), pick_str(token, key));
    }
    obj.insert("key".to_string(), redact_secret_field(token.get("key")));
    Value::Object(obj)
}

fn log_summary(log: &Value) -> Value {
    json!({
        "id": log.get("id"),
        "type": log.get("type"),
        "model_name": log.get("model_name"),
        "channel_name": log.get("channel_name"),
        "token_name": log.get("token_name"),
        "created_at": log.get("created_at"),
        "content": log.get("content"),
        "other": log.get("other"),
    })
}

fn redact_channel(channel: &Value) -> Value {
    let mut obj = channel.clone();
    if let Some(obj_map) = obj.as_object_mut() {
        if let Some(key) = obj_map.remove("key") {
            obj_map.insert("key".to_string(), redact_secret_field(Some(&key)));
        }
    }
    obj
}

fn redact_token(token: &Value) -> Value {
    let mut obj = token.clone();
    if let Some(obj_map) = obj.as_object_mut() {
        if let Some(key) = obj_map.remove("key") {
            obj_map.insert("key".to_string(), redact_secret_field(Some(&key)));
        }
    }
    obj
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_grok_4_5_resolves_to_200000() {
        assert_eq!(context_window("grok-4.5"), Some(200_000));
    }

    #[test]
    fn catalog_unknown_model_is_none() {
        assert_eq!(context_window("does-not-exist"), None);
    }

    #[test]
    fn redact_secret_in_text_replaces_raw_credential() {
        let canary = "SECRET-CANARY-12345";
        let text = format!("bad {canary} token");
        assert_eq!(redact_secret_in_text(&text, canary), "bad [REDACTED] token");
    }

    #[test]
    fn redact_secret_field_masks_a_present_key() {
        let v = json!("sk-live-abcdef");
        let out = redact_secret_field(Some(&v));
        assert_eq!(out["present"], true);
        assert_eq!(out["sha256"].as_str().unwrap().len(), 12);
        assert!(out["redacted"].as_str().unwrap().contains('*'));
    }

    #[test]
    fn redact_secret_field_handles_absent_key() {
        let out = redact_secret_field(None);
        assert_eq!(out["present"], false);
        assert!(out["sha256"].is_null());
    }

    #[test]
    fn envelope_ok_shape() {
        let env = Envelope::ok("doctor", json!({"a": 1}));
        let s = serde_json::to_string(&env).unwrap();
        assert!(s.contains("\"schema_version\":\"relay-gate.v1\""));
        assert!(s.contains("\"ok\":true"));
        assert!(s.contains("\"operation\":\"doctor\""));
    }

    #[test]
    fn envelope_err_shape_and_redaction() {
        let err = Error::Http {
            status: 500,
            path: "/api/channel/".into(),
            message: "bad SECRET-CANARY body".into(),
        };
        let env = Envelope::err("channels.list", err);
        let client = Client::new("http://127.0.0.1:9", "SECRET-CANARY", "1").unwrap();
        let s = client.redact(&serde_json::to_string(&env).unwrap());
        assert!(s.contains("\"ok\":false"));
        assert!(s.contains("\"code\":\"http_error\""));
        assert!(s.contains("\"http_status\":500"));
        assert!(!s.contains("SECRET-CANARY"));
    }

    #[test]
    fn schema_lists_all_operations() {
        let s = schema::build();
        let ops: Vec<String> = s["operations"]
            .as_array()
            .unwrap()
            .iter()
            .map(|o| o["name"].as_str().unwrap().to_string())
            .collect();
        assert!(ops.contains(&"schema".to_string()));
        assert!(ops.contains(&"doctor".to_string()));
        assert!(ops.contains(&"channels.list".to_string()));
        assert!(ops.contains(&"channels.get".to_string()));
        assert!(ops.contains(&"tokens.list".to_string()));
        assert!(ops.contains(&"tokens.get".to_string()));
        assert!(ops.contains(&"logs.recent".to_string()));
    }

    #[test]
    fn channels_get_requires_id() {
        // No client needed: selector validation happens before any HTTP call.
        let sel = ChannelsGetSelector::default();
        let err = channels_get(&untargetable_client(), &sel).unwrap_err();
        assert!(matches!(err, Error::Selector(_)));
    }

    #[test]
    fn tokens_get_requires_id() {
        let sel = TokensGetSelector::default();
        let err = tokens_get(&untargetable_client(), &sel).unwrap_err();
        assert!(matches!(err, Error::Selector(_)));
    }


    #[test]
    fn write_mode_defaults_to_dry_run() {
        assert_eq!(WriteMode::resolve(false, false), WriteMode::DryRun);
        assert_eq!(WriteMode::resolve(true, false), WriteMode::Apply);
        assert_eq!(WriteMode::resolve(true, true), WriteMode::DryRun);
        assert_eq!(WriteMode::resolve(false, true), WriteMode::DryRun);
    }

    #[test]
    fn channels_status_dry_run_issues_no_http() {
        let sel = ChannelsStatusSelector {
            id: Some(9),
            status: Some(2),
        };
        let out = channels_status(&untargetable_client(), &sel, WriteMode::DryRun).unwrap();
        assert_eq!(out["dry_run"], true);
        assert_eq!(out["applied"], false);
        assert_eq!(out["method"], "POST");
        assert_eq!(out["path"], "/api/channel/9/status");
    }

    #[test]
    fn channels_create_dry_run_redacts_key() {
        let sel = ChannelsCreateSelector {
            name: Some("demo".into()),
            key: Some("sk-secret-key-material".into()),
            ..Default::default()
        };
        let out = channels_create(&untargetable_client(), &sel, WriteMode::DryRun).unwrap();
        assert_eq!(out["dry_run"], true);
        assert_eq!(out["body"]["key"]["redacted"], true);
        let s = out.to_string();
        assert!(!s.contains("sk-secret-key-material"));
    }



    #[test]
    fn non_json_2xx_message_includes_status_and_preview() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        use std::thread;

        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().unwrap();
        thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut buf = [0u8; 1024];
                let _ = stream.read(&mut buf);
                let body = "<html>not-json SECRET-CANARY-XYZ</html>";
                let resp = format!(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(),
                    body
                );
                let _ = stream.write_all(resp.as_bytes());
            }
        });

        let client = Client::new(
            format!("http://{addr}"),
            "SECRET-CANARY-XYZ",
            "1",
        )
        .unwrap();
        let err = client.get("/api/status", &[]).unwrap_err();
        match err {
            Error::Parse { message, .. } => {
                assert!(message.contains("non-JSON response body (status 200)"), "{message}");
                assert!(message.contains("<html>"), "{message}");
                assert!(!message.contains("SECRET-CANARY-XYZ"), "credential leaked: {message}");
            }
            other => panic!("expected Parse, got {other:?}"),
        }
    }

    /// A client pointed at a loopback that never answers; selector validation
    /// must fail before any request is attempted.
    fn untargetable_client() -> Client {
        Client::new("http://127.0.0.1:1", "nonempty-token", "1")
            .expect("client builds with a non-empty token")
    }
}
