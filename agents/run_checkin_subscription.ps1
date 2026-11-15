# Midweek check-in on the Claude SUBSCRIPTION (no API cost).
#
# Runs Claude Code headless (`claude -p`) with the run-analyst subagent from
# linkedin-ai\.claude\agents\run-analyst.md, gives it ONLY the read-only
# garmin-coach MCP tools, and saves the answer next to the other check-ins.
# Replaces the paid-API run of agents\checkin_agent.py in the Wednesday task
# (that script still works for manual runs).
#
# Billing: Claude Code uses the logged-in subscription unless an API key is in
# the environment, so this script clears ANTHROPIC_API_KEY and friends for its
# own process. Counts toward the subscription's usage limits, not the API bill.
# Needs: you logged in to Claude Code on this PC (`claude` once, then /login).

$ErrorActionPreference = "Stop"

$Root      = "C:\Users\papad\Desktop\garmin-coach"
$AgentHome = "C:\Users\papad\Desktop\linkedin-ai"          # where .claude\agents\run-analyst.md lives
$McpConfig = Join-Path $Root "agents\mcp_garmin.json"
$OutDir    = "C:\Users\papad\Desktop\Fitness Report\check-ins"
$Log       = Join-Path $Root "data\checkin_agent.log"
$Claude    = "C:\Users\papad\.local\bin\claude.exe"

$Tools = @(
    "mcp__garmin-coach__goals",
    "mcp__garmin-coach__training_load",
    "mcp__garmin-coach__recent_runs",
    "mcp__garmin-coach__recent_sleep",
    "mcp__garmin-coach__speed_workouts"
) -join ","

function Write-Log($level, $msg) {
    Add-Content -Path $Log -Encoding utf8 -Value ("{0} {1} [subscription] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $level, $msg)
}

# Never bill the API from here, whatever the environment holds.
foreach ($v in "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL") {
    Remove-Item "Env:$v" -ErrorAction SilentlyContinue
}

$today    = Get-Date
$Question = "Today is $($today.ToString('dddd dd MMMM yyyy', [Globalization.CultureInfo]::InvariantCulture)). " +
            "Midweek check-in: how did Monday and Tuesday go, how is my recovery, and should I run " +
            "Thursday's threshold session as planned, adjust it, or swap it for an easy run?"

try {
    Set-Location $AgentHome
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $answer = & $Claude -p $Question `
        --agent run-analyst `
        --mcp-config $McpConfig --strict-mcp-config `
        --allowedTools $Tools `
        --permission-mode dontAsk `
        --no-session-persistence 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0 -or -not $answer.Trim()) {
        throw "claude exited with code $LASTEXITCODE. Output: $answer"
    }

    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $date = $today.ToString("yyyy-MM-dd")
    $path = Join-Path $OutDir "check-in $date.md"
    $body = "# Check-in $date`n`n**Question:** $Question`n`n_Run on the Claude subscription (run-analyst subagent)._`n`n---`n`n$($answer.Trim())`n"
    [IO.File]::WriteAllText($path, $body, (New-Object Text.UTF8Encoding($false)))
    Write-Log "INFO" "saved check-in to $path"
}
catch {
    Write-Log "ERROR" $_.Exception.Message
    exit 1
}
