<#
.SYNOPSIS
  Sync codebuddy models.json from Relay Gate /v1/models endpoint.

.DESCRIPTION
  Queries Relay Gate channel state, keeps only enabled channels (status=1),
  then updates ~/.codebuddy/models.json with the complete schema CodeBuddy
  expects. This prevents disabled NewAPI channels from leaking stale models
  into servitor/codebuddy dispatch.

  Run this before servitor batch dispatch or when new models are added
  to Relay Gate.

.PARAMETER ApiKey
  API key for Relay Gate caller requests. Default: read from existing models.json.
  The sync source is relay-gate channels list, so this key is only written
  into CodeBuddy model entries and is not used for discovery.

.PARAMETER ModelsJsonPath
  Path to codebuddy models.json. Default: ~/.codebuddy/models.json

.EXAMPLE
  .\sync-codebuddy-models.ps1
  .\sync-codebuddy-models.ps1 -DryRun
#>

[CmdletBinding()]
param(
    [string]$ApiKey,
    [string]$ModelsJsonPath = "$env:USERPROFILE\.codebuddy\models.json",
    [switch]$DryRun,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"

if ($SelfTest) {
    $source = Get-Content -LiteralPath $MyInvocation.MyCommand.Path -Raw -Encoding UTF8
    foreach ($required in @(
        "--status 1",
        '$channel.status -ne 1',
        'id = $id',
        'name = $name',
        'vendor = $vendor',
        'apiKey = $ApiKey',
        'url = $completionUrl',
        'maxInputTokens = 128000',
        'maxOutputTokens = 8192',
        'supportsToolCall = $true',
        'supportsImages = $supportsImg',
        '<redacted>'
    )) {
        if (-not $source.Contains($required)) {
            throw "sync-codebuddy-models self-test failed: missing $required"
        }
    }
    Write-Output "sync_codebuddy_models_selftest=ok"
    exit 0
}

# Read existing config to get apiKey if not provided
if (-not $ApiKey) {
    if (Test-Path $ModelsJsonPath) {
        $existing = Get-Content $ModelsJsonPath -Raw | ConvertFrom-Json
        $ApiKey = $existing.models[0].apiKey
    } else {
        Write-Error "No ApiKey provided and no existing models.json to read from."
        exit 1
    }
}

Write-Output "Querying relay-gate enabled channel models ..."
$rgJson = & relay-gate --output json channels list --status 1 --page-size 100
$response = $rgJson | ConvertFrom-Json

$modelIds = @()
foreach ($channel in $response.items) {
    if ($channel.status -ne 1) { continue }
    foreach ($model in ($channel.models -split ",")) {
        $trimmed = $model.Trim()
        if ($trimmed) { $modelIds += $trimmed }
    }
}
$upstreamModels = $modelIds | Sort-Object -Unique
Write-Output "Upstream models: $($upstreamModels.Count)"

# Vendor detection
function Get-Vendor($modelId) {
    if ($modelId -match "^claude") { return "Anthropic" }
    if ($modelId -match "^gpt") { return "OpenAI" }
    if ($modelId -match "^deepseek") { return "DeepSeek" }
    if ($modelId -match "^glm") { return "Zhipu" }
    if ($modelId -match "^grok") { return "xAI" }
    if ($modelId -match "^step") { return "StepFun" }
    if ($modelId -match "^kimi") { return "Moonshot" }
    if ($modelId -match "^minimax") { return "MiniMax" }
    return "Unknown"
}

function Get-DisplayName($modelId) {
    $parts = $modelId -split "-"
    $name = ($parts | ForEach-Object { $_.Substring(0,1).ToUpper() + $_.Substring(1) }) -join " "
    return $name
}

$completionUrl = "https://newapi.l1uyun.top:8080/v1/chat/completions"
$modelsArray = @()
$availArray = @()

foreach ($id in $upstreamModels) {
    $vendor = Get-Vendor $id
    $name = Get-DisplayName $id
    $supportsImg = ($vendor -in @("OpenAI","Anthropic","Zhipu"))

    $modelObj = @{
        id = $id
        name = $name
        vendor = $vendor
        apiKey = $ApiKey
        url = $completionUrl
        maxInputTokens = 128000
        maxOutputTokens = 8192
        supportsToolCall = $true
        supportsImages = $supportsImg
    }
    $modelsArray += $modelObj
    $availArray += $id
}

$config = @{
    models = $modelsArray
    availableModels = $availArray
}

if ($DryRun) {
    Write-Output "[DRY RUN] Would write $($upstreamModels.Count) models to $ModelsJsonPath"
    $safeConfig = $config | ConvertTo-Json -Depth 5 | ConvertFrom-Json
    foreach ($model in $safeConfig.models) {
        $model.apiKey = "<redacted>"
    }
    Write-Output ($safeConfig | ConvertTo-Json -Depth 5)
} else {
    $json = $config | ConvertTo-Json -Depth 5
    Set-Content -Path $ModelsJsonPath -Value $json -Encoding UTF8
    Write-Output "Updated $ModelsJsonPath with $($upstreamModels.Count) models"

    # Verify with servitor
    $servitorOutput = servitor models list --agent codebuddy --json 2>&1
    $s = $servitorOutput | ConvertFrom-Json
    $count = $s.models.Count
    Write-Output "servitor now sees $count models for codebuddy"
}
