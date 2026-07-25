//! Secret redaction helpers.
//!
//! Every public command surface scrubs raw credential material before it leaves
//! the process. This module provides the primitives used by the client and the
//! CLI emit layer:
//!
//! - [`redact_secret_in_text`]: replace any occurrence of a raw secret in a
//!   free-form string with `[REDACTED]`. Used as the final scrub over
//!   serialized stdout/stderr.
//! - [`redact_secret_field`]: mask a JSON secret field, replacing it with a
//!   `{present, sha256, redacted}` object that preserves identifiability
//!   without exposing material.
//! - [`secret_fingerprint`] / [`redacted_preview`]: short opaque fingerprints
//!   for diagnostics and `Debug` impls.

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

/// Number of hex characters retained from the sha256 fingerprint.
const FINGERPRINT_LEN: usize = 12;

/// Replace every occurrence of `secret` in `text` with `[REDACTED]`.
///
/// Also scrubs common JSON-escaped encodings of the same secret (for example
/// `\"` / `\n` / `\u00xx` forms produced by serializers), so a server echo
/// that only contains the escaped form cannot leak material through free-form
/// text scrubbing.
///
/// If `secret` is empty, the text is returned unchanged (nothing to redact).
pub fn redact_secret_in_text(text: &str, secret: &str) -> String {
    if secret.is_empty() {
        return text.to_string();
    }
    let mut out = text.replace(secret, "[REDACTED]");
    for variant in secret_text_variants(secret) {
        if variant != secret {
            out = out.replace(&variant, "[REDACTED]");
        }
    }
    out
}

/// JSON / escape variants that may appear when a secret is embedded in encoded text.
fn secret_text_variants(secret: &str) -> Vec<String> {
    let mut variants = Vec::new();

    // serde_json string body without surrounding quotes: handles \", \\, \n, etc.
    if let Ok(encoded) = serde_json::to_string(secret) {
        if encoded.len() >= 2 {
            let inner = &encoded[1..encoded.len() - 1];
            if inner != secret {
                variants.push(inner.to_string());
            }
        }
    }

    // Explicit \uXXXX form for non-ascii codepoints (UTF-16 code units).
    if secret.chars().any(|c| !c.is_ascii()) {
        let mut unicode = String::new();
        for ch in secret.chars() {
            if ch.is_ascii() {
                match ch {
                    '\\' => unicode.push_str("\\\\"),
                    '"' => unicode.push_str("\\\""),
                    '\n' => unicode.push_str("\\n"),
                    '\r' => unicode.push_str("\\r"),
                    '\t' => unicode.push_str("\\t"),
                    c => unicode.push(c),
                }
            } else {
                let n = u32::from(ch);
                if n <= 0xFFFF {
                    unicode.push_str(&format!("\\u{n:04x}"));
                } else {
                    let n2 = n - 0x10000;
                    let high = 0xD800 + (n2 >> 10);
                    let low = 0xDC00 + (n2 & 0x3FF);
                    unicode.push_str(&format!("\\u{high:04x}\\u{low:04x}"));
                }
            }
        }
        if unicode != secret && !variants.iter().any(|v| v == &unicode) {
            variants.push(unicode);
        }
    }

    variants
}

/// Short sha256 fingerprint of `secret` (first [`FINGERPRINT_LEN`] hex chars).
pub fn secret_fingerprint(secret: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(secret.as_bytes());
    let digest = hasher.finalize();
    let hex: String = digest.iter().map(|b| format!("{:02x}", b)).collect();
    hex.chars().take(FINGERPRINT_LEN).collect()
}

/// A short `***`-style preview for `Debug` impls; never reveals material.
pub fn redacted_preview(secret: &str) -> String {
    if secret.is_empty() {
        return "<empty>".to_string();
    }
    format!("***{}***", secret_fingerprint(secret))
}

/// Mask a JSON secret field.
///
/// Returns a JSON object:
///
/// ```json
/// { "present": true,  "sha256": "<12 hex>", "redacted": "sk-****" }
/// { "present": false, "sha256": null,       "redacted": null }
/// ```
///
/// The `redacted` form preserves a short prefix and masks the remainder with
/// `*`, so a caller can confirm a key changed without seeing it.
pub fn redact_secret_field(value: Option<&Value>) -> Value {
    match value {
        Some(v) if !v.is_null() => {
            let s = match v.as_str() {
                Some(s) => s.to_string(),
                None => v.to_string(),
            };
            json!({
                "present": true,
                "sha256": secret_fingerprint(&s),
                "redacted": mask_string(&s),
            })
        }
        _ => json!({
            "present": false,
            "sha256": Value::Null,
            "redacted": Value::Null,
        }),
    }
}

fn mask_string(s: &str) -> String {
    if s.is_empty() {
        return String::new();
    }
    let chars: Vec<char> = s.chars().collect();
    let prefix = chars.len().min(3);
    let masked = chars.len().saturating_sub(prefix).max(4);
    let mut out: String = chars[..prefix].iter().collect();
    out.push_str(&"*".repeat(masked));
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redact_in_text_replaces_raw_secret() {
        assert_eq!(
            redact_secret_in_text("a SECRET b", "SECRET"),
            "a [REDACTED] b"
        );
    }

    #[test]
    fn redact_in_text_empty_secret_is_noop() {
        assert_eq!(redact_secret_in_text("abc", ""), "abc");
    }

    #[test]
    fn fingerprint_is_12_hex_chars() {
        let fp = secret_fingerprint("sk-live-abcdef");
        assert_eq!(fp.len(), 12);
        assert!(fp.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn redacted_preview_never_exposes_material() {
        let preview = redacted_preview("sk-live-abcdef");
        assert!(preview.contains("***"));
        assert!(!preview.contains("sk-live"));
    }

    #[test]
    fn redact_secret_field_present() {
        let v = json!("sk-live-abcdef");
        let out = redact_secret_field(Some(&v));
        assert_eq!(out["present"], true);
        assert_eq!(out["sha256"].as_str().unwrap().len(), 12);
        assert!(out["redacted"].as_str().unwrap().contains('*'));
        assert!(!out.to_string().contains("sk-live-abcdef"));
    }

    #[test]
    fn redact_secret_field_absent() {
        let out = redact_secret_field(None);
        assert_eq!(out["present"], false);
        assert!(out["sha256"].is_null());
        assert!(out["redacted"].is_null());
    }

    #[test]
    fn redact_in_text_scrubs_json_escaped_quote_variant() {
        let secret = "tok\"en-secret";
        // Body embeds only the JSON-escaped form: tok\"en-secret
        let body = "leaked: tok\\\"en-secret tail".to_string();
        assert!(
            !body.contains(secret),
            "fixture must not contain the raw secret contiguous form"
        );
        let redacted = redact_secret_in_text(&body, secret);
        assert!(
            !redacted.contains("en-secret"),
            "escaped material remained: {redacted}"
        );
        assert!(redacted.contains("[REDACTED]"), "{redacted}");
    }

    #[test]
    fn redact_in_text_scrubs_json_escaped_newline_variant() {
        let secret = "line1\nline2-token";
        let escaped = serde_json::to_string(secret).unwrap();
        let inner = &escaped[1..escaped.len() - 1];
        assert_eq!(inner, "line1\\nline2-token");
        let body = format!("echo={inner}");
        assert!(!body.contains(secret));
        let redacted = redact_secret_in_text(&body, secret);
        assert!(!redacted.contains("line2-token"), "{redacted}");
        assert!(redacted.contains("[REDACTED]"), "{redacted}");
    }
}
