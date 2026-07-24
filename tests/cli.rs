//! Integration suite: in-process fixture HTTP server.
//!
//! Spins up a tiny TCP HTTP server that records request method, path, and
//! headers, and serves configured JSON responses. Proves:
//! - `Authorization` + `New-Api-User` headers are sent on every request.
//! - Every request is `GET` (no mutation path exists in this slice).
//! - Non-2xx responses surface as `http_error` envelopes.
//! - `success: false` 2xx responses surface as `newapi_error` envelopes.
//! - Raw credential material never appears in serialized stdout/diagnostics.
//! - `grok-4.5` resolves to `200000` (frozen catalog regression anchor).

use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use relay_gate::{
    ChannelsGetSelector, ChannelsListSelector, Client, Envelope, Error, LogsRecentSelector,
    SCHEMA_VERSION, TokensGetSelector, TokensListSelector, channels_get, channels_list,
    context_window, doctor, logs_recent, tokens_get, tokens_list,
};
use serde_json::json;

/// Raw admin token used as the redaction canary across every test.
const RAW_TOKEN: &str = "sk-admin-SECRET-canary-9876543210";

#[derive(Clone, Debug)]
struct RecordedRequest {
    method: String,
    path: String,
    headers: Vec<(String, String)>,
}

struct FixtureServer {
    base: String,
    requests: Arc<Mutex<Vec<RecordedRequest>>>,
    _thread: thread::JoinHandle<()>,
}

impl FixtureServer {
    fn new(responder: impl Fn(&RecordedRequest) -> (u16, String) + Send + Sync + 'static) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind fixture listener");
        let addr = listener.local_addr().expect("local addr");
        let base = format!("http://{addr}");
        let requests: Arc<Mutex<Vec<RecordedRequest>>> = Arc::new(Mutex::new(Vec::new()));
        let shared = Arc::clone(&requests);
        let _thread = thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { break };
                let req = match read_request(&mut stream) {
                    Ok(r) => r,
                    Err(_) => continue,
                };
                let (status, body) = responder(&req);
                let _ = write_response(&mut stream, status, &body);
                let _ = stream.flush();
                shared.lock().unwrap().push(req);
            }
        });
        Self {
            base,
            requests,
            _thread,
        }
    }

    fn requests(&self) -> Vec<RecordedRequest> {
        self.requests.lock().unwrap().clone()
    }

    fn wait_for_requests(&self, expected: usize) -> Vec<RecordedRequest> {
        for _ in 0..50 {
            let requests = self.requests();
            if requests.len() >= expected {
                return requests;
            }
            thread::sleep(Duration::from_millis(10));
        }
        self.requests()
    }
}

fn read_request(stream: &mut TcpStream) -> std::io::Result<RecordedRequest> {
    let mut reader = BufReader::new(stream.try_clone()?);
    let mut request_line = String::new();
    reader.read_line(&mut request_line)?;
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or("").to_string();
    let path = parts.next().unwrap_or("").to_string();
    let mut headers = Vec::new();
    loop {
        let mut line = String::new();
        let n = reader.read_line(&mut line)?;
        if n == 0 || line.trim().is_empty() {
            break;
        }
        if let Some((k, v)) = line.split_once(':') {
            headers.push((k.trim().to_string(), v.trim().to_string()));
        }
    }
    Ok(RecordedRequest {
        method,
        path,
        headers,
    })
}

fn write_response(stream: &mut TcpStream, status: u16, body: &str) -> std::io::Result<()> {
    let reason = match status {
        200 => "OK",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        500 => "Internal Server Error",
        _ => "OK",
    };
    let out = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len}\r\nConnection: close\r\n\r\n{body}",
        len = body.len()
    );
    stream.write_all(out.as_bytes())
}

fn header<'a>(req: &'a RecordedRequest, name: &str) -> Option<&'a str> {
    req.headers
        .iter()
        .find(|(k, _)| k.eq_ignore_ascii_case(name))
        .map(|(_, v)| v.as_str())
}

fn client(base: &str) -> Client {
    Client::new(base, RAW_TOKEN, "1").expect("client builds with non-empty token")
}

fn cli_command(server: &FixtureServer) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_relay-gate"));
    command
        .env("SIGIL_ADMIN_TOKEN", RAW_TOKEN)
        .env_remove("RELAY_GATE_ADMIN_TOKEN")
        .env("RELAY_GATE_BASE_URL", &server.base);
    command
}

#[test]
fn doctor_sends_auth_headers_and_get_only() {
    let server = FixtureServer::new(|req| {
        if req.path.starts_with("/api/status") {
            (
                200,
                json!({"success": true, "data": {"version": "v1", "setup": true}}).to_string(),
            )
        } else if req.path.starts_with("/api/channel/") {
            (
                200,
                json!({"success": true, "data": {"total": 3, "items": []}}).to_string(),
            )
        } else {
            (
                404,
                json!({"success": false, "message": "not found"}).to_string(),
            )
        }
    });
    let client = client(&server.base);
    let data = doctor(&client).expect("doctor ok");
    assert_eq!(data["channels_total"].as_u64(), Some(3));
    assert_eq!(data["user_id"], "1");

    let reqs = server.requests();
    assert!(reqs.len() >= 2, "doctor should issue at least 2 requests");
    for req in &reqs {
        assert_eq!(req.method, "GET", "every request must be GET");
        assert_eq!(
            header(req, "Authorization").unwrap_or_default(),
            RAW_TOKEN,
            "Authorization header must carry the raw admin token"
        );
        assert_eq!(
            header(req, "New-Api-User").unwrap_or_default(),
            "1",
            "New-Api-User header must be set"
        );
    }
}

#[test]
fn channels_list_redacts_keys_and_stays_get_only() {
    let body = json!({
        "success": true,
        "data": {
            "total": 1,
            "items": [
                {"id": 7, "name": "ch-seven", "key": "sk-live-abcdef", "type": 1, "models": "grok-4.5"}
            ]
        }
    });
    let server = FixtureServer::new(move |_| (200, body.to_string()));
    let client = client(&server.base);
    let data = channels_list(&client, &ChannelsListSelector::default()).expect("channels list ok");
    let serialized = serde_json::to_string(&data).unwrap();
    assert!(
        !serialized.contains("sk-live-abcdef"),
        "raw channel key must not appear in list output"
    );
    assert!(serialized.contains("grok-4.5"));

    for req in server.requests() {
        assert_eq!(req.method, "GET");
    }
}

#[test]
fn channels_get_redacts_key_field() {
    let body = json!({
        "success": true,
        "data": {"id": 7, "name": "ch-seven", "key": "sk-live-abcdef", "type": 1}
    });
    let server = FixtureServer::new(move |_| (200, body.to_string()));
    let client = client(&server.base);
    let data =
        channels_get(&client, &ChannelsGetSelector { id: Some(7) }).expect("channels get ok");
    assert_eq!(data["key"]["present"], true);
    assert_eq!(data["key"]["sha256"].as_str().unwrap().len(), 12);
    let serialized = serde_json::to_string(&data).unwrap();
    assert!(!serialized.contains("sk-live-abcdef"));
}

#[test]
fn tokens_list_and_get_redact_key_fields() {
    let server = FixtureServer::new(|req| {
        if req.path.starts_with("/api/token/search") {
            (
                200,
                json!({
                    "success": true,
                    "data": {
                        "total": 1,
                        "page": 1,
                        "page_size": 100,
                        "items": [{"id": 9, "name": "caller", "key": "sk-live-token-list"}]
                    }
                })
                .to_string(),
            )
        } else {
            (
                200,
                json!({
                    "success": true,
                    "data": {"id": 9, "name": "caller", "key": "sk-live-token-get"}
                })
                .to_string(),
            )
        }
    });
    let client = client(&server.base);
    let listed = tokens_list(&client, &TokensListSelector::default()).expect("tokens list ok");
    let fetched = tokens_get(&client, &TokensGetSelector { id: Some(9) }).expect("tokens get ok");

    for value in [&listed, &fetched] {
        let serialized = serde_json::to_string(value).unwrap();
        assert!(serialized.contains("\"present\":true"));
        assert!(serialized.contains("\"sha256\""));
        assert!(!serialized.contains("sk-live-token"));
    }
    let reqs = server.requests();
    assert!(reqs.iter().any(|r| r.path.starts_with("/api/token/search")));
    assert!(reqs.iter().any(|r| r.path.starts_with("/api/token/9")));
}

#[test]
fn non_2xx_surfaces_as_http_error_envelope() {
    let server = FixtureServer::new(|_| {
        (
            500,
            json!({"success": false, "message": format!("upstream blew up; token={RAW_TOKEN}")})
                .to_string(),
        )
    });
    let client = client(&server.base);
    let err = doctor(&client).unwrap_err();
    assert!(matches!(err, Error::Http { status: 500, .. }));
    let env = Envelope::err("doctor", err);
    let serialized = client.redact(&serde_json::to_string(&env).unwrap());
    assert!(serialized.contains("\"ok\":false"));
    assert!(serialized.contains("\"code\":\"http_error\""));
    assert!(serialized.contains("\"http_status\":500"));
    assert!(
        !serialized.contains(RAW_TOKEN),
        "raw credential must be redacted from the error envelope"
    );
    assert_eq!(env.schema_version, SCHEMA_VERSION);
}

#[test]
fn newapi_success_false_surfaces_as_newapi_error() {
    let server = FixtureServer::new(|_| {
        (
            200,
            json!({"success": false, "message": format!("denied: {RAW_TOKEN}")}).to_string(),
        )
    });
    let client = client(&server.base);
    let err = channels_list(&client, &ChannelsListSelector::default()).unwrap_err();
    assert!(matches!(err, Error::NewApi { .. }));
    let env = Envelope::err("channels.list", err);
    let serialized = client.redact(&serde_json::to_string(&env).unwrap());
    assert!(serialized.contains("\"code\":\"newapi_error\""));
    assert!(!serialized.contains(RAW_TOKEN));
}

#[test]
fn redaction_scrubs_token_from_arbitrary_diagnostics() {
    let client = client("http://127.0.0.1:9");
    let text = format!("transport error while contacting {RAW_TOKEN} upstream");
    let scrubbed = client.redact(&text);
    assert!(scrubbed.contains("[REDACTED]"));
    assert!(!scrubbed.contains(RAW_TOKEN));
}

#[test]
fn logs_recent_is_get_only() {
    let body = json!({"success": true, "data": {"total": 0, "items": []}});
    let server = FixtureServer::new(move |_| (200, body.to_string()));
    let client = client(&server.base);
    let data = logs_recent(&client, &LogsRecentSelector::default()).expect("logs recent ok");
    assert_eq!(data["total"].as_u64(), Some(0));
    for req in server.requests() {
        assert_eq!(req.method, "GET");
    }
}

#[test]
fn logs_recent_self_scope_uses_self_endpoint() {
    let body = json!({"success": true, "data": {"total": 0, "items": []}});
    let server = FixtureServer::new(move |_| (200, body.to_string()));
    let client = client(&server.base);
    logs_recent(
        &client,
        &LogsRecentSelector {
            self_scope: Some(true),
            ..Default::default()
        },
    )
    .expect("self logs ok");
    assert!(
        server
            .wait_for_requests(1)
            .iter()
            .any(|request| request.path.starts_with("/api/log/self"))
    );
}

#[test]
fn binary_emits_redacted_json_and_human_and_quiet_modes() {
    let server = FixtureServer::new(|req| {
        if req.path.starts_with("/api/channel/7") {
            (
                200,
                json!({
                    "success": true,
                    "data": {"id": 7, "name": "ch-seven", "key": "sk-live-cli-key"}
                })
                .to_string(),
            )
        } else if req.path.starts_with("/api/status") {
            (
                200,
                json!({"success": true, "data": {"version": "v1"}}).to_string(),
            )
        } else if req.path.starts_with("/api/channel/") {
            (
                200,
                json!({"success": true, "data": {"total": 3, "items": []}}).to_string(),
            )
        } else {
            (404, json!({"success": false}).to_string())
        }
    });

    let json = cli_command(&server)
        .args(["channels", "get", "--input", "{\"id\":7}"])
        .output()
        .expect("binary runs");
    assert!(json.status.success());
    let stdout = String::from_utf8(json.stdout).expect("utf8 stdout");
    assert!(stdout.contains("\"operation\":\"channels.get\""));
    assert!(!stdout.contains("sk-live-cli-key"));
    assert!(!stdout.contains(RAW_TOKEN));

    let human = cli_command(&server)
        .args(["--output", "human", "doctor"])
        .output()
        .expect("binary runs");
    assert!(human.status.success());
    assert!(
        String::from_utf8(human.stdout)
            .expect("utf8 stdout")
            .contains("relay-gate.v1 doctor ok")
    );

    let quiet = cli_command(&server)
        .args([
            "--output",
            "quiet",
            "tokens",
            "get",
            "--input",
            "{not-json}",
        ])
        .output()
        .expect("binary runs");
    assert!(!quiet.status.success());
    assert!(quiet.stdout.is_empty());
    let stderr = String::from_utf8(quiet.stderr).expect("utf8 stderr");
    assert!(stderr.contains("selector_error"));
    assert!(!stderr.contains(RAW_TOKEN));
}

#[test]
fn grok_4_5_context_window_is_200000() {
    assert_eq!(context_window("grok-4.5"), Some(200_000));
}

#[test]
fn envelope_data_carries_no_raw_token_after_scrub() {
    // A 2xx body that echoes the raw token in a non-key field. The final scrub
    // must still remove it from the serialized envelope.
    let body = json!({
        "success": true,
        "data": {"id": 1, "name": format!("leaked-{RAW_TOKEN}"), "type": 1}
    });
    let server = FixtureServer::new(move |_| (200, body.to_string()));
    let client = client(&server.base);
    let data =
        channels_get(&client, &ChannelsGetSelector { id: Some(1) }).expect("channels get ok");
    let env = Envelope::ok("channels.get", data);
    let serialized = client.redact(&serde_json::to_string(&env).unwrap());
    assert!(
        !serialized.contains(RAW_TOKEN),
        "raw token must be scrubbed even from 2xx data fields"
    );
}
