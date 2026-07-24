//! Static model catalog for the relay gate read-only core.
//!
//! Holds fixed product facts that downstream consumers (Codex / Claude Code /
//! CodeBuddy / servitor) depend on. The frozen regression anchor is
//! `grok-4.5` -> `200000` (Codex product context window). The catalog is
//! intentionally minimal: each entry must be a frozen product decision; do not
//! add speculative context windows.

use std::collections::HashMap;
use std::sync::OnceLock;

/// Frozen context-window table (model name -> context window in tokens).
fn table() -> &'static HashMap<&'static str, u64> {
    static TABLE: OnceLock<HashMap<&'static str, u64>> = OnceLock::new();
    TABLE.get_or_init(|| {
        let mut m = HashMap::new();
        // Frozen anchor: grok-4.5 Codex product context window (Liber Null #305).
        m.insert("grok-4.5", 200_000);
        m
    })
}

/// Resolve the context window for `model`.
///
/// Returns `None` for unknown models. Matching is case-sensitive on the
/// canonical model name.
///
/// # Examples
///
/// ```
/// # use relay_gate::context_window;
/// assert_eq!(context_window("grok-4.5"), Some(200_000));
/// assert_eq!(context_window("unknown"), None);
/// ```
pub fn context_window(model: &str) -> Option<u64> {
    table().get(model).copied()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn grok_4_5_is_200000() {
        assert_eq!(context_window("grok-4.5"), Some(200_000));
    }

    #[test]
    fn unknown_model_is_none() {
        assert_eq!(context_window("nope"), None);
    }
}
