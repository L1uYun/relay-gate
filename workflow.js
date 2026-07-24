// Goalchain Dispatcher workflow template.
// Instantiate per task in _tmp/<task-slug>/workflow.js by filling CONFIG and
// the phase bodies. Do NOT edit this template per task; sharpen the template
// only when a pattern repeats across tasks.
//
// State model: Liber Null item owns lifecycle; this run directory owns execution
// evidence; the contract owns requirement truth. Recovery anchor:
//   Liber Null item id -> contract path -> latest run manifest -> first unpassed gate.

export const meta = {
  name: "goalchain-dispatcher",
  description: "Recoverable work-delivery chain for one human-confirmed Liber Null item",
};

const CONFIG = {
  taskId: 305,
  contractPath: "D:/AgentWork/_tmp/relay-gate-rust-305/contract.md",
  projectRoot: "D:/AgentWork/_tmp/relay-gate-rust-305",
  scratch: "D:/AgentWork/_tmp/relay-gate-rust-305",
  deliverables: [
    "D:/AgentWork/_tmp/relay-gate-rust-305/Cargo.toml",
    "D:/AgentWork/_tmp/relay-gate-rust-305/src/lib.rs",
    "D:/AgentWork/_tmp/relay-gate-rust-305/src/catalog.rs",
    "D:/AgentWork/_tmp/relay-gate-rust-305/src/redact.rs",
    "D:/AgentWork/_tmp/relay-gate-rust-305/src/schema.rs",
    "D:/AgentWork/_tmp/relay-gate-rust-305/src/main.rs",
    "D:/AgentWork/_tmp/relay-gate-rust-305/tests/cli.rs",
    "D:/AgentWork/_tmp/relay-gate-rust-305/README.md",
  ],
  evidenceJson: "D:/AgentWork/_tmp/relay-gate-rust-305/relay-gate-rust-evidence.json",
  // mechanicalTokens: copy from contract mechanical_tokens (same spelling as deliverable keys).
  // Empty is invalid when claiming a mechanical gate — readiness must fail.
  mechanicalTokens: [
    "agent_native_cli",
    "read_only_surface",
    "secret_redaction",
    "grok_4_5_context_window",
  ],
};

// ---------------------------------------------------------------------------
phase("readiness");
// Controller-side checks that must hold before any worker dispatch:
// contract exists and is frozen, required sources readable, write boundary named.
// Throw to stop the chain here.
// Readiness: contract path exists AND mechanical token table is non-empty.
// Acceptance identifier binding: tokens must match contract text and later deliverable keys.
if (!Array.isArray(CONFIG.mechanicalTokens) || CONFIG.mechanicalTokens.length === 0) {
  throw new Error(
    "readiness gate failed: CONFIG.mechanicalTokens empty — copy contract mechanical_tokens; no evergreen mechanical pass"
  );
}
const readiness = await command("pwsh", [
  "-NoProfile", "-Command",
  (
    "$ErrorActionPreference='Stop';" +
    "$c=Get-Content -LiteralPath '" + (CONFIG.contractPath || "") + "' -Raw -Encoding utf8;" +
    "$tokens=@(" + CONFIG.mechanicalTokens.map((t) => "'" + String(t).replace(/'/g, "''") + "'").join(",") + ");" +
    "foreach($t in $tokens){ if($c -notmatch [regex]::Escape($t)){ Write-Error \"contract missing token: $t\"; exit 11 } };" +
    "'ready'"
  ),
], { label: "readiness", timeoutSeconds: 60 });
if (readiness.exitCode !== 0 || readiness.timedOut) {
  throw new Error("readiness gate failed: " + (readiness.stderr || readiness.stdout));
}

// P2 method-layer rule (karma #459): remote ssh/docker/psql boundaries go in the
// worker packet, not a new tool primitive:
//   - never `docker exec -i cmd <<SQL` (bash stdin is passed straight into the
//     container and eats the heredoc); use `cat <<SQL | docker exec -i`;
//   - normalize SQL heredoc lines to LF; CRLF breaks the remote heredoc opener;
//   - psql -At defaults to | separator; vector strings with [ ] make awk { }
//     actions get swallowed by the transport; use -F | explicitly.
// P3 method-layer rule: expert consultation goes through
//   `servitor submit --model newapi/gpt-5.6-sol --system-prompt <advisor contract>`
// where the advisor contract template lives at
// agent-dispatch/references/advisor-contract.md. There is no separate advisor
// subcommand; advice is evidence input to the controller, never an executor.
// Do not let advisor output drive commands, gates, or deploys.

// Mid-flight redirect (continue-as-new): when a worker or gate discovers the
// direction is wrong, call supersede() instead of forcing the old plan through.
// The engine marks this run terminal=superseded (NOT failed/cancelled) and
// records {reason, evidence, newContract}. The controller then:
//   1. reads `servitor-workflows get <run_id>` -> status=superseded + supersede info;
//   2. writes a Liber Null note linking old run -> new contract (supersede chain);
//   3. starts a NEW workflow from the new contract; its readiness gate skips
//      already-produced artifacts, so completed work is not re-done.
// This is Temporal continue-as-new / Plan-and-Act scoped revision: never edit a
// running script in place (journal replay keys would all miss).

// Error-handling spectrum available in this template:
//   retry(() => command/agent(...), {maxAttempts, delayMs, backoff, wallTimeSeconds, nonRetryable})
//     -> automatic recovery for transient failures; each attempt is a journaled call.
//   gate(question, {expect:"value", current, hint})
//     -> human-corrects-input: parks waiting_human; controller collects the value
//        (decision page) and injects via `approve RUN_ID --value '<json>'`.
//   supersede({reason, evidence, newContract})
//     -> human-redirects-direction: terminal=superseded, controller starts a new run.

// ---------------------------------------------------------------------------
phase("dispatch");
// Bounded worker. One objective, one evidence class, explicit write boundary.
// Replace the prompt; keep the structure (objective / read-first / write-only /
// evidence file / return JSON).
// HARD (karma #470): final channel is schema JSON only — never free-text
// "await notification" endings. Long ops (>~5min): prefer command() phase with
// timeoutSeconds, or block until process exit then write evidence BEFORE return.
// Transport succeeded != business complete.
// HARD (karma #473): dual-home fingerprints in evidence are post-mutation freezes —
// recompute after the last push/recreate/patch this turn; controller re-hashes this-turn.
//
// Envelope recovery (advisor E / 2026-07-22): NO hidden agent() retry.
// Transport schema-aware extract runs first. On remaining envelope/schema fail:
//   - durable landed evidence -> phase("format-recovery") format-only agent once
//   - else -> one explicit re-execution of implement (retry maxAttempts: 2)
// Never blindly loop side-effectful implement beyond that bound.

const workerSchema = {
  type: "object",
  required: ["summary"],
  properties: { summary: { type: "string" }, evidence: { type: "string" } },
};

const workerImplementPrompt = `
TASK_ID=${CONFIG.taskId}
Objective: implement the frozen Relay Gate Rust read-only core described in the contract.

Read first (owner skills + primary sources):
- C:/Users/84618/.codex/skills/rust-skills/SKILL.md
- C:/Users/84618/.codex/skills/cli-creator/SKILL.md
- C:/Users/84618/.codex/skills/relay-gate/SKILL.md
- ${CONFIG.contractPath}

Write only:
- ${CONFIG.deliverables.join("\n- ")}

Write boundary: this worktree only. Do not modify the primary relay-gate worktree,
do not modify PATH, do not deploy, and issue no NewAPI mutations. Preserve the
existing Python files as compatibility reference during this slice. Use Rust 2024,
default versioned JSON output, structured --input JSON where selector data is
needed, a schema command, error envelopes, GET-only HTTP operations, credentials
from SIGIL_ADMIN_TOKEN / RELAY_GATE_ADMIN_TOKEN environment variables, and redacted
diagnostics. Add an in-process fixture HTTP server integration suite that proves
headers, GET-only semantics, non-2xx JSON errors, redaction, and grok-4.5=200000.
Run the four Cargo quality commands with CARGO_HOME and CARGO_TARGET_DIR on D:.

Evidence: write machine-readable results to ${CONFIG.evidenceJson}.
If recording dual-home fingerprints (md5_table / path->hash map), write them only
after the last deploy mutation this turn (karma #473 final freeze).

Return JSON only: {"summary":"one-sentence status","evidence":"${CONFIG.evidenceJson}"}
`;

const workerFormatPrompt = `
FORMAT RESIDUAL ONLY for TASK_ID=${CONFIG.taskId}.
Do NOT edit files, deploy, push, recreate services, or re-implement work.
Read only existing artifacts:
- ${CONFIG.contractPath}
- ${CONFIG.evidenceJson}
- ${CONFIG.deliverables.join("\n- ")}

Emit schema JSON from on-disk evidence only (honest incomplete status if residual).
Return JSON: {"summary":"one-sentence status from evidence","evidence":"${CONFIG.evidenceJson}"}
`;

async function landedEvidenceOk() {
  const evidencePath = String(CONFIG.evidenceJson || "").replace(/'/g, "''");
  if (!evidencePath) return false;
  const r = await command("pwsh", [
    "-NoProfile", "-Command",
    (
      "$ErrorActionPreference='Stop';" +
      "$p='" + evidencePath + "';" +
      "if(!(Test-Path -LiteralPath $p)){ exit 1 };" +
      "$raw=Get-Content -LiteralPath $p -Raw -Encoding utf8;" +
      "if([string]::IsNullOrWhiteSpace($raw)){ exit 2 };" +
      "try { $null = $raw | ConvertFrom-Json } catch { exit 3 };" +
      "'landed'"
    ),
  ], { label: "landed-evidence", timeoutSeconds: 30 });
  return r.exitCode === 0 && !r.timedOut;
}

function isEnvelopeError(err) {
  const text = String(err && err.message ? err.message : err);
  return /summary is required|not JSON|no schema-valid|agent output is not JSON|is required|must be object|must be string/i.test(text);
}

const worker = await retry(async (attempt) => {
  try {
    if (attempt > 1 && (await landedEvidenceOk())) {
      phase("format-recovery");
      return await agent(workerFormatPrompt, {
        label: "format-recovery",
        agent: "codebuddy",
        cwd: CONFIG.projectRoot,
        timeoutSeconds: 300,
        schema: workerSchema,
      });
    }
    return await agent(workerImplementPrompt, {
        label: attempt === 1 ? "worker" : "worker-retry",
        agent: "codebuddy",
      cwd: CONFIG.projectRoot,
      timeoutSeconds: 900,
      schema: workerSchema,
    });
  } catch (err) {
    // Only envelope/schema failures are retryable here; other failures fail fast.
    if (!isEnvelopeError(err)) {
      throw new Error("non-envelope-worker-fail: " + String(err && err.message ? err.message : err));
    }
    throw err;
  }
}, {
  maxAttempts: 2,
  delayMs: 500,
  backoff: 1,
  nonRetryable: [
    "non-envelope-worker-fail",
    "workflow interrupted",
    "cancelled",
    "contract missing",
    "readiness gate failed",
    "authorization",
    "credential",
    "Access denied",
  ],
});

// ---------------------------------------------------------------------------
phase("verification");
// Controller reads evidence from disk, never trusts worker self-report.
const evidence = await command("pwsh", [
  "-NoProfile", "-Command",
  "Get-Content -LiteralPath '" + CONFIG.evidenceJson + "' -Raw -Encoding utf8",
], { label: "read-evidence", timeoutSeconds: 30 });
if (evidence.exitCode !== 0) {
  throw new Error("evidence missing after worker run: " + evidence.stderr);
}
const data = JSON.parse(evidence.stdout);

// Mechanical gate: every contract token must appear in joined deliverables + evidence.
// Do not use a constant success string. Append extra invariants only after this token check.
// If evidence has path->hash fingerprints, controller re-hashes those paths this-turn
// (karma #473); do not trust a mid-deploy freeze.
const mechanical = await command("pwsh", [
  "-NoProfile", "-Command",
  (
    "$ErrorActionPreference='Stop';" +
    "$paths=@(" +
      [...CONFIG.deliverables, CONFIG.evidenceJson].filter(Boolean)
        .map((p) => "'" + String(p).replace(/'/g, "''") + "'").join(",") +
    ");" +
    "$blob=''; foreach($p in $paths){ if(!(Test-Path -LiteralPath $p)){ Write-Error \"missing deliverable: $p\"; exit 12 }; $blob+=Get-Content -LiteralPath $p -Raw -Encoding utf8 };" +
    "$tokens=@(" + CONFIG.mechanicalTokens.map((t) => "'" + String(t).replace(/'/g, "''") + "'").join(",") + ");" +
    "foreach($t in $tokens){ if($blob -notmatch [regex]::Escape($t)){ Write-Error \"deliverables missing token: $t\"; exit 13 } };" +
    "'mechanical-tokens-ok'"
  ),
], { label: "mechanical-gate", timeoutSeconds: 60 });
if (mechanical.exitCode !== 0) {
  throw new Error("mechanical gate failed: " + (mechanical.stderr || mechanical.stdout));
}

// ---------------------------------------------------------------------------
phase("semantic-gate");
// Independent reviewer. Must end with exactly VERDICT=APPROVED or VERDICT=REJECTED.
// Read-only/idempotent: bounded explicit retry for missing VERDICT line only.
// HARD (karma #474 / P5d #301 residual): semantic reviewer is no-tools. Path-only
// "Read: /path" produces false VERDICT=REJECTED ("证据正文缺失"). Always
// command()-load contract + deliverables + evidence and INLINE bodies before agent().
const deliveryPaths = (Array.isArray(CONFIG.deliverables) ? CONFIG.deliverables : [])
  .filter(Boolean)
  .map((p) => String(p).replace(/'/g, "''"));
const semanticBodiesCmd = await command("pwsh", [
  "-NoProfile", "-Command",
  (
    "$ErrorActionPreference='Stop';" +
    "$o=[ordered]@{};" +
    "$o.contract=Get-Content -LiteralPath '" + String(CONFIG.contractPath).replace(/'/g, "''") + "' -Raw -Encoding utf8;" +
    "$dpaths=@(" + deliveryPaths.map((p) => "'" + p + "'").join(",") + ");" +
    "if($dpaths.Count -eq 0){ $o.delivery='' } else { $parts=@(); foreach($p in $dpaths){ $parts += (Get-Content -LiteralPath $p -Raw -Encoding utf8) }; $o.delivery=($parts -join \"`n---`n\") };" +
    "$o.evidence=Get-Content -LiteralPath '" + String(CONFIG.evidenceJson).replace(/'/g, "''") + "' -Raw -Encoding utf8;" +
    "($o | ConvertTo-Json -Compress -Depth 3)"
  ),
], { label: "semantic-load-bodies", timeoutSeconds: 60 });
if (semanticBodiesCmd.exitCode !== 0) {
  throw new Error("semantic body load failed: " + (semanticBodiesCmd.stderr || semanticBodiesCmd.stdout));
}
const semanticBodies = JSON.parse(semanticBodiesCmd.stdout);
function clipSemantic(s, n) {
  const t = String(s || "");
  return t.length <= n ? t : t.slice(0, n) + "\n...[truncated]...";
}
if (!String(semanticBodies.contract || "").trim()) {
  throw new Error("semantic body empty: contract (karma #474)");
}
if (!String(semanticBodies.evidence || "").trim()) {
  throw new Error("semantic body empty: evidence (karma #474)");
}
const review = await retry(async (attempt) => {
  const text = await agent(`
Review the delivery for Liber Null task ${CONFIG.taskId} against its contract.
Do NOT use tools. Bodies are inlined below (karma #474).

CONTRACT:
${clipSemantic(semanticBodies.contract, 8000)}

DELIVERY:
${clipSemantic(semanticBodies.delivery, 8000)}

EVIDENCE:
${clipSemantic(semanticBodies.evidence, 12000)}

Also listed paths (reference only; do not require file tools):
- ${CONFIG.contractPath}
- ${CONFIG.deliverables.join("\n- ")}
- ${CONFIG.evidenceJson}

Check: draft-vs-approved boundary (if any), numbers agree with evidence, scope
respected, prohibited actions absent, cold-start reader questions answered.
End with exactly one line: VERDICT=APPROVED or VERDICT=REJECTED.
`, {
    label: attempt === 1 ? "semantic-gate" : "semantic-gate-retry",
    agent: "codebuddy",
    cwd: CONFIG.projectRoot,
    timeoutSeconds: 900,
  });
  if (!/VERDICT=(APPROVED|REJECTED)/.test(text)) {
    throw new Error("semantic envelope missing VERDICT line");
  }
  return text;
}, {
  maxAttempts: 2,
  delayMs: 500,
  backoff: 1,
  nonRetryable: ["workflow interrupted", "cancelled"],
});
if (!review.includes("VERDICT=APPROVED")) {
  throw new Error("semantic gate rejected: " + review);
}

// ---------------------------------------------------------------------------
phase("writeback");
// Controller owns Liber Null writeback (not the worker).
// After this phase succeeds, the controller's chat report to the user is 说人话 HARD:
// lead with human outcome + next move; LN/run/path/tokens as optional footer only.
return {
  summary: worker.summary,
  taskId: CONFIG.taskId,
  deliverables: CONFIG.deliverables,
  evidence: CONFIG.evidenceJson,
  review,
};
