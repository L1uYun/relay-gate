//! Discoverable command schema.
//!
//! [`build`] returns the static JSON description of the command surface.
//! Operation names match the `operation` field emitted in the versioned
//! envelope, so a downstream agent can drive the CLI by first reading `schema`
//! and then calling the listed operations.

use serde_json::{Value, json};

/// Build the static schema JSON for the command surface.
pub fn build() -> Value {
    json!({
        "schema_version": crate::SCHEMA_VERSION,
        "http_method": "ANY",
        "mutation_allowed": true,
        "write_policy": {
            "default": "dry_run",
            "apply_flag": "--apply",
            "dry_run_flag": "--dry-run",
            "precedence": "dry_run wins over apply",
            "mutation_ops": [
                "channels.create",
                "channels.update",
                "channels.status",
                "tokens.create",
                "tokens.update",
                "tokens.key",
                "options.set"
            ]
        },
        "operations": [
            op("schema", "GET", "discover this command schema", None),
            op("doctor", "GET", "structural preflight: GET /api/status + /api/channel/", None),
            op("channels.list", "GET", "list channels (paginated)", Some(channels_list_selector())),
            op("channels.get", "GET", "get one channel by id", Some(channels_get_selector())),
            op("channels.create", "POST", "create a new channel (dry-run default; --apply to land)", Some(channels_create_selector())),
            op("channels.update", "PUT", "update channel fields (PATCH semantics; dry-run default; --apply to land)", Some(channels_update_selector())),
            op("channels.status", "POST", "set channel status (1=enabled, 2=disabled, 3=auto; dry-run default; --apply to land)", Some(channels_status_selector())),
            op("channels.test", "GET", "test a channel by id", Some(channels_test_selector())),
            op("tokens.list", "GET", "list caller tokens", Some(tokens_list_selector())),
            op("tokens.get", "GET", "get one token by id", Some(tokens_get_selector())),
            op("tokens.create", "POST", "create a caller token (dry-run default; --apply to land)", Some(tokens_create_selector())),
            op("tokens.update", "PUT", "update token fields (dry-run default; --apply to land)", Some(tokens_update_selector())),
            op("tokens.key", "POST", "regenerate token key (dry-run default; --apply to land)", Some(tokens_key_selector())),
            op("logs.recent", "GET", "recent gateway logs", Some(logs_recent_selector())),
            op("logs.stats", "GET", "aggregate log stats", None),
            op("options.list", "GET", "list NewAPI options", Some(options_list_selector())),
            op("options.set", "PUT", "set a NewAPI option (dry-run default; --apply to land)", Some(options_set_selector())),
            op("models.list", "GET", "list models exposed via caller API", None),
        ],
    })
}

fn op(name: &str, method: &str, doc: &str, selector: Option<Value>) -> Value {
    json!({
        "name": name,
        "method": method,
        "selector": selector,
        "doc": doc,
    })
}

fn channels_list_selector() -> Value {
    json!({
        "type": "object",
        "fields": {
            "page": "u32?",
            "page_size": "u32?",
            "status": "i32?",
            "type": "i32?",
            "group": "string?",
            "id_sort": "bool?",
        },
    })
}

fn channels_get_selector() -> Value {
    json!({ "type": "object", "fields": { "id": "u64" } })
}

fn channels_create_selector() -> Value {
    json!({
        "type": "object",
        "fields": {
            "name": "string",
            "base_url": "string?",
            "key": "string?",
            "models": "string?",
            "group": "string?",
            "type": "i32?",
            "priority": "i32?",
            "weight": "i32?",
        },
    })
}

fn channels_update_selector() -> Value {
    json!({
        "type": "object",
        "fields": {
            "id": "u64",
            "fields": "object (channel fields to update, excluding status)",
        },
    })
}

fn channels_status_selector() -> Value {
    json!({
        "type": "object",
        "fields": { "id": "u64", "status": "i32 (1=enabled, 2=disabled, 3=auto)" },
    })
}

fn channels_test_selector() -> Value {
    json!({
        "type": "object",
        "fields": { "id": "u64", "model": "string?" },
    })
}

fn tokens_list_selector() -> Value {
    json!({
        "type": "object",
        "fields": {
            "keyword": "string?",
            "page": "u32?",
            "page_size": "u32?",
        },
    })
}

fn tokens_get_selector() -> Value {
    json!({ "type": "object", "fields": { "id": "u64" } })
}

fn tokens_create_selector() -> Value {
    json!({
        "type": "object",
        "fields": {
            "name": "string",
            "remain_quota": "i64?",
            "unlimited_quota": "bool?",
            "expired_time": "i64?",
            "group": "string?",
        },
    })
}

fn tokens_update_selector() -> Value {
    json!({
        "type": "object",
        "fields": {
            "id": "u64",
            "fields": "object (token fields to update)",
        },
    })
}

fn tokens_key_selector() -> Value {
    json!({ "type": "object", "fields": { "id": "u64" } })
}

fn logs_recent_selector() -> Value {
    json!({
        "type": "object",
        "fields": {
            "page": "u32?",
            "page_size": "u32?",
            "self": "bool?",
        },
    })
}

fn options_list_selector() -> Value {
    json!({
        "type": "object",
        "fields": { "key": "string?" },
    })
}

fn options_set_selector() -> Value {
    json!({
        "type": "object",
        "fields": { "key": "string", "value": "string" },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_lists_all_operations_in_order() {
        let s = build();
        let names: Vec<&str> = s["operations"]
            .as_array()
            .unwrap()
            .iter()
            .map(|o| o["name"].as_str().unwrap())
            .collect();
        assert_eq!(
            names,
            vec![
                "schema",
                "doctor",
                "channels.list",
                "channels.get",
                "channels.create",
                "channels.update",
                "channels.status",
                "channels.test",
                "tokens.list",
                "tokens.get",
                "tokens.create",
                "tokens.update",
                "tokens.key",
                "logs.recent",
                "logs.stats",
                "options.list",
                "options.set",
                "models.list",
            ]
        );
    }

    #[test]
    fn build_marks_mutation_allowed() {
        let s = build();
        assert_eq!(s["mutation_allowed"], true);
    }
}
