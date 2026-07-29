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
        // Kimi K2 family per platform.kimi.ai/docs/api/models-overview (256K)
        m.insert("kimi-k2.6", 262_144);
        m.insert("Kimi-K2.7-Code", 262_144);
        // Qwen3.x family per help.aliyun.com/zh/model-studio/text-generation-model (1M)
        m.insert("qwen3.7-max", 1_048_576);
        m.insert("qwen3.7-plus", 1_048_576);
        m.insert("qwen3.8-max-preview", 1_048_576);
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
/// assert_eq!(context_window("kimi-k2.6"), Some(262_144));
/// assert_eq!(context_window("Kimi-K2.7-Code"), Some(262_144));
/// assert_eq!(context_window("qwen3.7-max"), Some(1_048_576));
/// assert_eq!(context_window("qwen3.7-plus"), Some(1_048_576));
/// assert_eq!(context_window("qwen3.8-max-preview"), Some(1_048_576));
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
    fn kimi_k2_6_is_262144() {
        assert_eq!(context_window("kimi-k2.6"), Some(262_144));
    }

    #[test]
    fn kimi_k2_7_code_is_262144() {
        assert_eq!(context_window("Kimi-K2.7-Code"), Some(262_144));
    }

    #[test]
    fn qwen3_7_max_is_1m() {
        assert_eq!(context_window("qwen3.7-max"), Some(1_048_576));
    }

    #[test]
    fn qwen3_7_plus_is_1m() {
        assert_eq!(context_window("qwen3.7-plus"), Some(1_048_576));
    }

    #[test]
    fn qwen3_8_max_preview_is_1m() {
        assert_eq!(context_window("qwen3.8-max-preview"), Some(1_048_576));
    }

    #[test]
    fn unknown_model_is_none() {
        assert_eq!(context_window("nope"), None);
    }
}
