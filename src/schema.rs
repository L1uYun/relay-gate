//! Discoverable command schema.
//!
//! [`build`] returns the static JSON description of the read-only command
//! surface. Operation names match the `operation` field emitted in the
//! versioned envelope, so a downstream agent can drive the CLI by first
//! reading `schema` and then calling the listed operations.

use serde_json::{Value, json};

/// Build the static schema JSON for the read-only command surface.
pub fn build() -> Value {
    json!({
        "schema_version": crate::SCHEMA_VERSION,
        "http_method": "GET",
        "mutation_allowed": false,
        "operations": [
            op("schema", "discover this command schema", None),
            op("doctor", "structural preflight: GET /api/status + /api/channel/", None),
            op("channels.list", "list channels (paginated)", Some(channels_list_selector())),
            op("channels.get", "get one channel by id", Some(channels_get_selector())),
            op("tokens.list", "list caller tokens", Some(tokens_list_selector())),
            op("tokens.get", "get one token by id", Some(tokens_get_selector())),
            op("logs.recent", "recent gateway logs", Some(logs_recent_selector())),
        ],
    })
}

fn op(name: &str, doc: &str, selector: Option<Value>) -> Value {
    json!({
        "name": name,
        "method": "GET",
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
    json!({
        "type": "object",
        "fields": { "id": "u64" },
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
    json!({
        "type": "object",
        "fields": { "id": "u64" },
    })
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
                "tokens.list",
                "tokens.get",
                "logs.recent",
            ]
        );
    }

    #[test]
    fn build_marks_get_only_and_no_mutation() {
        let s = build();
        assert_eq!(s["http_method"], "GET");
        assert_eq!(s["mutation_allowed"], false);
        for op in s["operations"].as_array().unwrap() {
            assert_eq!(op["method"], "GET");
        }
    }
}
