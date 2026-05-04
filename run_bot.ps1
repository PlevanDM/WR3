#!/usr/bin/env powershell
<#
.SYNOPSIS
    Launch Warranty Bot in a terminal for local Telegram testing.

.DESCRIPTION
    Starts the bot process and displays output. Press Ctrl+C to stop.
    The bot will connect to RemOnline and listen to Telegram.

.EXAMPLE
    .\run_bot.ps1
#>

param([switch]$NoCheck)

Write-Host "Warranty 3.0 Bot Launcher" -ForegroundColor Cyan
Write-Host ('=' * 70)

# Check if .env exists
if (-not (Test-Path ".env")) {
    Write-Host "❌ .env not found" -ForegroundColor Red
    Write-Host "   Copy .env.example to .env and fill in:"
    Write-Host "   - BOT_TOKEN"
    Write-Host "   - ADMIN_TG_IDS"
    Write-Host "   - REMONLINE_API_KEY"
    exit 1
}

# Use system python by default if .venv not explicitly available
$python_exe = "python"
if (Test-Path ".\.venv\Scripts\python.exe") {
    $python_exe = ".\.venv\Scripts\python.exe"
}

# Optional: quick health check (ui_walk only; skips detective)
if (-not $NoCheck) {
    Write-Host "`n[ Health check... ]" -ForegroundColor Yellow
    $output = & $python_exe scripts\research.py --quick 2>&1
    $exitCode = $LASTEXITCODE
    $output | Select-Object -Last 20
    if ($exitCode -ne 0) {
        Write-Host "Health check failed (exit $exitCode)." -ForegroundColor Red
        exit $exitCode
    }
}

# Start bot
Write-Host "`n[ Starting bot process... ]" -ForegroundColor Green
Write-Host "   Listening to @warranty3_bot on Telegram..."
Write-Host "   Press Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host ""

try {
    & $python_exe -m app.main 2>&1
}
catch {
    Write-Host "Error starting bot: $_" -ForegroundColor Red
    exit 1
}
