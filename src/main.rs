//! relay-gate CLI entry point.

use std::process::ExitCode;

use clap::{Parser, Subcommand};
use relay_gate::{
    ChannelsGetSelector, ChannelsListSelector, Client, Envelope, Error, LogsRecentSelector,
    TokensGetSelector, TokensListSelector, schema,
};
use serde::de::DeserializeOwned;
use serde_json::Value;

#[derive(Parser, Debug)]
#[command(
    name = "relay-gate",
    version,
    about = "Read-only NewAPI Relay Gate core"
)]
struct Cli {
    /// Override the NewAPI base URL (default: https://newapi.l1uyun.top:8080).
    #[arg(long, global = true)]
    base_url: Option<String>,
    /// Output mode: `json` (versioned envelope, default), `human`, `quiet`.
    #[arg(long, global = true, default_value = "json")]
    output: OutputMode,
    /// Pretty-print JSON output.
    #[arg(long, global = true)]
    pretty: bool,
    #[command(subcommand)]
    command: Command,
}

#[derive(Clone, Copy, Debug, clap::ValueEnum)]
enum OutputMode {
    Json,
    Human,
    Quiet,
}

#[derive(Subcommand, Debug)]
enum Command {
    /// Discover the command schema.
    Schema,
    /// Structural preflight (GET /api/status + /api/channel/).
    Doctor,
    /// Channel inventory.
    Channels {
        #[command(subcommand)]
        action: ChannelsAction,
    },
    /// Caller token inventory.
    Tokens {
        #[command(subcommand)]
        action: TokensAction,
    },
    /// Gateway logs.
    Logs {
        #[command(subcommand)]
        action: LogsAction,
    },
}

#[derive(Subcommand, Debug)]
enum ChannelsAction {
    /// List channels (paginated).
    List {
        /// Selector JSON: {"page":u32?,"page_size":u32?,"status":i32?,"type":i32?,"group":str?,"id_sort":bool?}
        #[arg(long)]
        input: Option<String>,
    },
    /// Get a single channel by id.
    Get {
        /// Selector JSON: {"id":u64}
        #[arg(long)]
        input: String,
    },
}

#[derive(Subcommand, Debug)]
enum TokensAction {
    /// List caller tokens.
    List {
        /// Selector JSON: {"keyword":str?,"page":u32?,"page_size":u32?}
        #[arg(long)]
        input: Option<String>,
    },
    /// Get a single token by id.
    Get {
        /// Selector JSON: {"id":u64}
        #[arg(long)]
        input: String,
    },
}

#[derive(Subcommand, Debug)]
enum LogsAction {
    /// Recent gateway logs.
    Recent {
        /// Selector JSON: {"page":u32?,"page_size":u32?,"self":bool?}
        #[arg(long)]
        input: Option<String>,
    },
}

fn main() -> ExitCode {
    if run(Cli::parse()) {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}

fn run(cli: Cli) -> bool {
    let mode = cli.output;
    let pretty = cli.pretty;
    let base_url = cli.base_url.as_deref();
    let operation = operation_name(&cli.command);

    if matches!(cli.command, Command::Schema) {
        let env = Envelope::ok("schema", schema::build());
        return emit(&env, mode, pretty, None);
    }

    let client = match Client::from_env(base_url) {
        Ok(c) => c,
        Err(err) => {
            let env = Envelope::err(operation, err);
            return emit(&env, mode, pretty, None);
        }
    };

    let result = execute(&cli.command, &client);
    let env = match result {
        Ok(data) => Envelope::ok(operation, data),
        Err(err) => Envelope::err(operation, err),
    };
    emit(&env, mode, pretty, Some(&client))
}

fn operation_name(cmd: &Command) -> &'static str {
    match cmd {
        Command::Schema => "schema",
        Command::Doctor => "doctor",
        Command::Channels { action } => match action {
            ChannelsAction::List { .. } => "channels.list",
            ChannelsAction::Get { .. } => "channels.get",
        },
        Command::Tokens { action } => match action {
            TokensAction::List { .. } => "tokens.list",
            TokensAction::Get { .. } => "tokens.get",
        },
        Command::Logs { action } => match action {
            LogsAction::Recent { .. } => "logs.recent",
        },
    }
}

fn execute(cmd: &Command, client: &Client) -> Result<Value, Error> {
    match cmd {
        Command::Schema => Ok(schema::build()),
        Command::Doctor => relay_gate::doctor(client),
        Command::Channels { action } => match action {
            ChannelsAction::List { input } => {
                let sel: ChannelsListSelector = parse_selector(input.as_deref())?;
                relay_gate::channels_list(client, &sel)
            }
            ChannelsAction::Get { input } => {
                let sel: ChannelsGetSelector = parse_selector(Some(input.as_str()))?;
                relay_gate::channels_get(client, &sel)
            }
        },
        Command::Tokens { action } => match action {
            TokensAction::List { input } => {
                let sel: TokensListSelector = parse_selector(input.as_deref())?;
                relay_gate::tokens_list(client, &sel)
            }
            TokensAction::Get { input } => {
                let sel: TokensGetSelector = parse_selector(Some(input.as_str()))?;
                relay_gate::tokens_get(client, &sel)
            }
        },
        Command::Logs { action } => match action {
            LogsAction::Recent { input } => {
                let sel: LogsRecentSelector = parse_selector(input.as_deref())?;
                relay_gate::logs_recent(client, &sel)
            }
        },
    }
}

fn parse_selector<T: DeserializeOwned + Default>(input: Option<&str>) -> Result<T, Error> {
    match input {
        None => Ok(T::default()),
        Some(s) => serde_json::from_str(s)
            .map_err(|e| Error::Selector(format!("invalid selector JSON: {e}"))),
    }
}

fn emit(env: &Envelope, mode: OutputMode, pretty: bool, redactor: Option<&Client>) -> bool {
    let ok = env.ok;
    match mode {
        OutputMode::Quiet => {
            if !ok {
                eprintln!("{}", redact_str(&serialize(env, pretty), redactor));
            }
            ok
        }
        OutputMode::Json => {
            println!("{}", redact_str(&serialize(env, pretty), redactor));
            ok
        }
        OutputMode::Human => {
            println!("{}", redact_str(&human_summary(env), redactor));
            ok
        }
    }
}

fn serialize(env: &Envelope, pretty: bool) -> String {
    if pretty {
        serde_json::to_string_pretty(env).unwrap_or_default()
    } else {
        serde_json::to_string(env).unwrap_or_default()
    }
}

fn redact_str(s: &str, redactor: Option<&Client>) -> String {
    match redactor {
        Some(c) => c.redact(s),
        None => s.to_string(),
    }
}

fn human_summary(env: &Envelope) -> String {
    let status = if env.ok { "ok" } else { "error" };
    match (env.ok, &env.error) {
        (true, _) => {
            let detail = env.data.as_ref().map(summary_data).unwrap_or_default();
            if detail.is_empty() {
                format!("{} {} {}", env.schema_version, env.operation, status)
            } else {
                format!(
                    "{} {} {} — {}",
                    env.schema_version, env.operation, status, detail
                )
            }
        }
        (false, Some(e)) => format!(
            "{} {} {} — {}: {}",
            env.schema_version, env.operation, status, e.code, e.message
        ),
        _ => format!("{} {} {}", env.schema_version, env.operation, status),
    }
}

fn summary_data(data: &Value) -> String {
    let Some(obj) = data.as_object() else {
        return String::new();
    };
    let mut parts = Vec::new();
    for key in ["channels_total", "total", "version", "user_id"] {
        if let Some(v) = obj.get(key) {
            if !v.is_null() {
                parts.push(format!("{key}={v}"));
            }
        }
    }
    parts.join(" ")
}
