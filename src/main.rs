//! relay-gate CLI entry point.

use std::process::ExitCode;

use clap::{Parser, Subcommand};
use relay_gate::{
    ChannelsCreateSelector, ChannelsGetSelector, ChannelsListSelector,
    ChannelsStatusSelector, ChannelsTestSelector, ChannelsUpdateSelector,
    Client, Envelope, Error, LogsRecentSelector, LogsStatsSelector,
    ModelsListSelector, OptionsListSelector, OptionsSetSelector,
    TokensCreateSelector, TokensGetSelector, TokensKeySelector,
    TokensListSelector, TokensUpdateSelector, WriteMode,
    schema,
};
use serde::de::DeserializeOwned;
use serde_json::Value;

#[derive(Parser, Debug)]
#[command(
    name = "relay-gate",
    version,
    about = "NewAPI Relay Gate CLI"
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
    /// Land mutation commands. Write ops default to dry-run without this flag.
    #[arg(long, global = true, default_value_t = false)]
    apply: bool,
    /// Force dry-run preview. Wins over `--apply` when both are set.
    #[arg(long, global = true, default_value_t = false)]
    dry_run: bool,
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
    /// Channel inventory and management.
    Channels {
        #[command(subcommand)]
        action: ChannelsAction,
    },
    /// Caller token inventory and management.
    Tokens {
        #[command(subcommand)]
        action: TokensAction,
    },
    /// Gateway logs.
    Logs {
        #[command(subcommand)]
        action: LogsAction,
    },
    /// NewAPI global options.
    Options {
        #[command(subcommand)]
        action: OptionsAction,
    },
    /// Model discovery via caller API.
    Models {
        #[command(subcommand)]
        action: ModelsAction,
    },
}

#[derive(Subcommand, Debug)]
enum ChannelsAction {
    /// List channels (paginated).
    List {
        #[arg(long)]
        input: Option<String>,
    },
    /// Get a single channel by id.
    Get {
        #[arg(long)]
        input: String,
    },
    /// Create a new channel.
    Create {
        #[arg(long)]
        input: String,
    },
    /// Update channel fields (PATCH semantics; body = id + fields).
    Update {
        #[arg(long)]
        input: String,
    },
    /// Set channel status (1=enabled, 2=disabled, 3=auto-disabled).
    Status {
        #[arg(long)]
        input: String,
    },
    /// Test a channel by id.
    Test {
        #[arg(long)]
        input: String,
    },
}

#[derive(Subcommand, Debug)]
enum TokensAction {
    /// List caller tokens.
    List {
        #[arg(long)]
        input: Option<String>,
    },
    /// Get a single token by id.
    Get {
        #[arg(long)]
        input: String,
    },
    /// Create a caller token.
    Create {
        #[arg(long)]
        input: String,
    },
    /// Update token fields.
    Update {
        #[arg(long)]
        input: String,
    },
    /// Regenerate token key.
    Key {
        #[arg(long)]
        input: String,
    },
}

#[derive(Subcommand, Debug)]
enum LogsAction {
    /// Recent gateway logs.
    Recent {
        #[arg(long)]
        input: Option<String>,
    },
    /// Aggregate log stats.
    Stats,
}

#[derive(Subcommand, Debug)]
enum OptionsAction {
    /// List NewAPI options.
    List {
        #[arg(long)]
        input: Option<String>,
    },
    /// Set a NewAPI option.
    Set {
        #[arg(long)]
        input: String,
    },
}

#[derive(Subcommand, Debug)]
enum ModelsAction {
    /// List models exposed via caller API.
    List,
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

    // Models list uses a caller token, not admin; allow empty admin for that command.
    let needs_admin = !matches!(&cli.command, Command::Models { .. });
    let client = if needs_admin {
        match Client::from_env(base_url) {
            Ok(c) => c,
            Err(err) => {
                let env = Envelope::err(operation, err);
                return emit(&env, mode, pretty, None);
            }
        }
    } else {
        // Models list: create a client with empty admin token (not used).
        Client::new_caller(base_url.unwrap_or(relay_gate::DEFAULT_BASE_URL)).unwrap()
    };

    let write_mode = WriteMode::resolve(cli.apply, cli.dry_run);
    let result = execute(&cli.command, &client, write_mode);
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
            ChannelsAction::Create { .. } => "channels.create",
            ChannelsAction::Update { .. } => "channels.update",
            ChannelsAction::Status { .. } => "channels.status",
            ChannelsAction::Test { .. } => "channels.test",
        },
        Command::Tokens { action } => match action {
            TokensAction::List { .. } => "tokens.list",
            TokensAction::Get { .. } => "tokens.get",
            TokensAction::Create { .. } => "tokens.create",
            TokensAction::Update { .. } => "tokens.update",
            TokensAction::Key { .. } => "tokens.key",
        },
        Command::Logs { action } => match action {
            LogsAction::Recent { .. } => "logs.recent",
            LogsAction::Stats => "logs.stats",
        },
        Command::Options { action } => match action {
            OptionsAction::List { .. } => "options.list",
            OptionsAction::Set { .. } => "options.set",
        },
        Command::Models { action } => match action {
            ModelsAction::List => "models.list",
        },
    }
}

fn execute(cmd: &Command, client: &Client, write_mode: WriteMode) -> Result<Value, Error> {
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
            ChannelsAction::Create { input } => {
                let sel: ChannelsCreateSelector = parse_selector(Some(input.as_str()))?;
                relay_gate::channels_create(client, &sel, write_mode)
            }
            ChannelsAction::Update { input } => {
                let sel: ChannelsUpdateSelector = parse_selector(Some(input.as_str()))?;
                relay_gate::channels_update(client, &sel, write_mode)
            }
            ChannelsAction::Status { input } => {
                let sel: ChannelsStatusSelector = parse_selector(Some(input.as_str()))?;
                relay_gate::channels_status(client, &sel, write_mode)
            }
            ChannelsAction::Test { input } => {
                let sel: ChannelsTestSelector = parse_selector(Some(input.as_str()))?;
                relay_gate::channels_test(client, &sel)
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
            TokensAction::Create { input } => {
                let sel: TokensCreateSelector = parse_selector(Some(input.as_str()))?;
                relay_gate::tokens_create(client, &sel, write_mode)
            }
            TokensAction::Update { input } => {
                let sel: TokensUpdateSelector = parse_selector(Some(input.as_str()))?;
                relay_gate::tokens_update(client, &sel, write_mode)
            }
            TokensAction::Key { input } => {
                let sel: TokensKeySelector = parse_selector(Some(input.as_str()))?;
                relay_gate::tokens_key(client, &sel, write_mode)
            }
        },
        Command::Logs { action } => match action {
            LogsAction::Recent { input } => {
                let sel: LogsRecentSelector = parse_selector(input.as_deref())?;
                relay_gate::logs_recent(client, &sel)
            }
            LogsAction::Stats => {
                let sel: LogsStatsSelector = parse_selector(None)?;
                relay_gate::logs_stats(client, &sel)
            }
        },
        Command::Options { action } => match action {
            OptionsAction::List { input } => {
                let sel: OptionsListSelector = parse_selector(input.as_deref())?;
                relay_gate::options_list(client, &sel)
            }
            OptionsAction::Set { input } => {
                let sel: OptionsSetSelector = parse_selector(Some(input.as_str()))?;
                relay_gate::options_set(client, &sel, write_mode)
            }
        },
        Command::Models { action } => match action {
            ModelsAction::List => {
                let sel: ModelsListSelector = parse_selector(None)?;
                relay_gate::models_list(client, &sel)
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
