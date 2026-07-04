<#
.SYNOPSIS
    One-command dev bring-up for Pilot on Windows: preflight the toolchain and the
    Ollama model, install frontend deps, then launch the backend and frontend
    together.

.DESCRIPTION
    Pilot is a local-desktop agent, so this is a plain process launcher rather than
    a container stack (see scripts/README.md for why there is no docker-compose).
    The script:
      1. checks uv / pnpm / Ollama are on PATH,
      2. verifies the default model (OLLAMA_MODEL, or gemma4:12b) is pulled and
         offers to pull it if not,
      3. runs `pnpm install` once if node_modules is missing,
      4. starts the backend (uv run python main.py) and the frontend (pnpm dev)
         in separate windows.

    Everything degrades gracefully: a missing Ollama only warns (the backend still
    boots), and no external service is required beyond the local defaults.

.PARAMETER SingleOrigin
    Build the frontend and serve UI + WebSocket from the backend on one port
    (http://localhost:8000) instead of running two dev servers. Mirrors the
    "single-origin" mode in GETTING_STARTED.md.

.PARAMETER SkipModelCheck
    Skip the Ollama model preflight (useful when pointing OLLAMA_BASE_URL at a
    remote host or running a non-default backend).

.EXAMPLE
    ./scripts/dev.ps1

.EXAMPLE
    ./scripts/dev.ps1 -SingleOrigin
#>
[CmdletBinding()]
param(
    [switch]$SingleOrigin,
    [switch]$SkipModelCheck
)

$ErrorActionPreference = 'Stop'

# Repo root is the parent of this script's directory, regardless of cwd.
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $RepoRoot 'backend'
$Frontend = Join-Path $RepoRoot 'frontend'

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Warn2($msg) { Write-Host "!!  $msg" -ForegroundColor Yellow }

# --- 1. Toolchain preflight -------------------------------------------------
Write-Step 'Checking toolchain (uv, pnpm)'
$missing = @()
if (-not (Test-Command 'uv')) { $missing += 'uv (https://docs.astral.sh/uv/)' }
if (-not (Test-Command 'pnpm')) { $missing += 'pnpm (https://pnpm.io)' }
if ($missing.Count -gt 0) {
    Write-Error "Missing required tools:`n  - $($missing -join "`n  - ")"
    exit 1
}

# --- 2. Ollama model preflight ---------------------------------------------
# The model is only needed on the default local backend; a warning here never
# blocks bring-up because the backend can also target a remote/OpenAI path.
$model = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { 'gemma4:12b' }
if (-not $SkipModelCheck) {
    Write-Step "Checking Ollama model '$model'"
    if (-not (Test-Command 'ollama')) {
        Write-Warn2 'ollama not found on PATH. Install from https://ollama.com and pull the model.'
        Write-Warn2 'Continuing anyway (the backend still boots; local answers will fail until Ollama is up).'
    }
    else {
        $tags = & ollama list 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Warn2 'Could not reach the Ollama daemon (`ollama list` failed). Is it running?'
        }
        elseif ($tags -match [regex]::Escape($model)) {
            Write-Host "    model '$model' is present." -ForegroundColor Green
        }
        else {
            Write-Warn2 "model '$model' is not pulled."
            $answer = Read-Host "    Pull it now with 'ollama pull $model'? [y/N]"
            if ($answer -match '^(y|yes)$') {
                & ollama pull $model
            }
            else {
                Write-Warn2 "Skipping. Pull it later with: ollama pull $model"
            }
        }
    }
}

# --- 3. Frontend deps -------------------------------------------------------
if (-not (Test-Path (Join-Path $Frontend 'node_modules'))) {
    Write-Step 'Installing frontend dependencies (pnpm install)'
    Push-Location $Frontend
    try { pnpm install } finally { Pop-Location }
}

# --- 4. Launch --------------------------------------------------------------
if ($SingleOrigin) {
    Write-Step 'Single-origin mode: building the frontend, then serving UI + backend on :8000'
    Push-Location $Frontend
    try { pnpm build } finally { Pop-Location }
    Write-Host 'Open http://localhost:8000' -ForegroundColor Green
    Push-Location $Backend
    try { uv run python main.py } finally { Pop-Location }
}
else {
    Write-Step 'Dev mode: launching backend (:8000) and frontend (:3000) in separate windows'
    # Each server gets its own window so Ctrl+C in one does not kill the other.
    Start-Process -FilePath 'pwsh' -ArgumentList @(
        '-NoExit', '-Command', "Set-Location '$Backend'; uv run python main.py"
    )
    Start-Process -FilePath 'pwsh' -ArgumentList @(
        '-NoExit', '-Command', "Set-Location '$Frontend'; pnpm dev"
    )
    Write-Host 'Backend  -> http://localhost:8000  (WebSocket ws://localhost:8000/ws, MCP :3001)' -ForegroundColor Green
    Write-Host 'Frontend -> http://localhost:3000' -ForegroundColor Green
    Write-Host 'Two windows opened. Close them (or Ctrl+C in each) to stop.' -ForegroundColor Green
}
