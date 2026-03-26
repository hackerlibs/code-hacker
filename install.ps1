#
# Code Hacker — One-Click Installer (Windows PowerShell)
#
# Usage:
#   .\install.ps1                 # Full install: deps + VS Code config + start servers
#   .\install.ps1 -NoVSCode       # Skip VS Code configuration
#   .\install.ps1 -ServersOnly    # Only start MCP servers (skip install steps)
#
# If execution policy blocks the script, run:
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
#   .\install.ps1
#

param(
    [switch]$NoVSCode,
    [switch]$ServersOnly,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ─── Colors ───────────────────────────────────────────────────────────
function Write-Info   { param($msg) Write-Host "[INFO]  $msg" -ForegroundColor Cyan }
function Write-Ok     { param($msg) Write-Host "[OK]    $msg" -ForegroundColor Green }
function Write-Warn   { param($msg) Write-Host "[WARN]  $msg" -ForegroundColor Yellow }
function Write-Fail   { param($msg) Write-Host "[FAIL]  $msg" -ForegroundColor Red; exit 1 }

# ─── Banner ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  +==========================================+" -ForegroundColor Cyan
Write-Host "  |        Code Hacker - Installer           |" -ForegroundColor Cyan
Write-Host "  |   VS Code Custom Agent + MCP Servers     |" -ForegroundColor Cyan
Write-Host "  +==========================================+" -ForegroundColor Cyan
Write-Host ""

if ($Help) {
    Write-Host "Usage: .\install.ps1 [OPTIONS]"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -NoVSCode      Skip VS Code configuration"
    Write-Host "  -ServersOnly   Only start MCP servers (skip install steps)"
    Write-Host "  -Help          Show this help"
    exit 0
}

# ─── Server Definitions ──────────────────────────────────────────────
$Servers = @(
    @{ Script = "filesystem.py";    Port = 8001; Name = "filesystem-command" },
    @{ Script = "git_tools.py";     Port = 8002; Name = "git-tools" },
    @{ Script = "code_intel.py";    Port = 8003; Name = "code-intel" },
    @{ Script = "memory_store.py";  Port = 8004; Name = "memory-store" },
    @{ Script = "code_review.py";   Port = 8005; Name = "code-review" },
    @{ Script = "multi_project.py"; Port = 8007; Name = "multi-project" }
)

$PidDir = Join-Path $ScriptDir ".mcp_pids"

# ═══════════════════════════════════════════════════════════════════════
if (-not $ServersOnly) {

    # ─── Step 1: Check Prerequisites ─────────────────────────────────
    Write-Host "Step 1/5: Checking prerequisites" -ForegroundColor White
    Write-Host ""

    # Python 3.11+
    $PythonCmd = $null
    foreach ($cmd in @("python", "python3", "py")) {
        try {
            $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($ver) {
                $parts = $ver.Split(".")
                $major = [int]$parts[0]
                $minor = [int]$parts[1]
                if ($major -ge 3 -and $minor -ge 11) {
                    $PythonCmd = $cmd
                    Write-Ok "Python $ver found ($cmd)"
                    break
                }
            }
        } catch {}
    }
    if (-not $PythonCmd) {
        Write-Fail "Python 3.11+ is required. Download from https://python.org"
    }

    # uv or pip
    $UseUv = $false
    try {
        $uvVer = & uv --version 2>$null
        if ($uvVer) {
            Write-Ok "uv found ($uvVer)"
            $UseUv = $true
        }
    } catch {}

    if (-not $UseUv) {
        try {
            & $PythonCmd -m pip --version 2>$null | Out-Null
            Write-Ok "pip found"
        } catch {
            Write-Info "Installing uv (recommended Python package manager)..."
            try {
                Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
                $UseUv = $true
                Write-Ok "uv installed successfully"
            } catch {
                Write-Fail "Failed to install uv. Install manually: https://docs.astral.sh/uv/"
            }
        }
    }

    # VS Code CLI
    $VSCodeAvailable = $false
    try {
        & code --version 2>$null | Out-Null
        Write-Ok "VS Code CLI found"
        $VSCodeAvailable = $true
    } catch {
        Write-Warn "VS Code CLI (code) not found - skipping VS Code configuration"
        Write-Warn "Install from: https://code.visualstudio.com"
    }

    # Git
    try {
        & git --version 2>$null | Out-Null
        Write-Ok "Git found"
    } catch {
        Write-Fail "Git is required. Install from https://git-scm.com"
    }

    Write-Host ""

    # ─── Step 2: Install Python Dependencies ─────────────────────────
    Write-Host "Step 2/5: Installing Python dependencies" -ForegroundColor White
    Write-Host ""

    Push-Location $ScriptDir

    if ($UseUv) {
        Write-Info "Running uv sync..."
        & uv sync
        if ($LASTEXITCODE -ne 0) { Write-Fail "uv sync failed" }
        $PythonCmd = "uv"
        $PythonArgs = @("run", "python")
        Write-Ok "Dependencies installed via uv"
    } else {
        Write-Info "Running pip install..."
        & $PythonCmd -m pip install -e ".[dev]" --quiet
        if ($LASTEXITCODE -ne 0) { Write-Fail "pip install failed" }
        $PythonArgs = @()
        Write-Ok "Dependencies installed via pip"
    }

    Pop-Location
    Write-Host ""

    # ─── Step 3: Configure VS Code ───────────────────────────────────
    Write-Host "Step 3/5: Configuring VS Code" -ForegroundColor White
    Write-Host ""

    if ($NoVSCode) {
        Write-Info "Skipping VS Code configuration (-NoVSCode)"
    } elseif ($VSCodeAvailable) {

        # Install Copilot extensions
        $extensions = & code --list-extensions 2>$null
        if ($extensions -match "github\.copilot-chat") {
            Write-Ok "GitHub Copilot Chat extension installed"
        } else {
            Write-Info "Installing GitHub Copilot Chat extension..."
            & code --install-extension GitHub.copilot-chat --force 2>$null
        }
        if ($extensions -match "github\.copilot(?!-)") {
            Write-Ok "GitHub Copilot extension installed"
        } else {
            Write-Info "Installing GitHub Copilot extension..."
            & code --install-extension GitHub.copilot --force 2>$null
        }

        # VS Code user settings path on Windows
        $VSCodeSettingsDir = Join-Path $env:APPDATA "Code\User"
        $VSCodeSettings = Join-Path $VSCodeSettingsDir "settings.json"

        if (-not (Test-Path $VSCodeSettingsDir)) {
            New-Item -ItemType Directory -Path $VSCodeSettingsDir -Force | Out-Null
        }

        if (-not (Test-Path $VSCodeSettings)) {
            Set-Content -Path $VSCodeSettings -Value "{}"
        }

        Write-Info "Updating VS Code user settings with MCP servers..."

        # Use Python to safely merge JSON
        $pyScript = @"
import json, sys

settings_path = r'$VSCodeSettings'
try:
    with open(settings_path, 'r', encoding='utf-8') as f:
        content = f.read().strip()
        settings = json.loads(content) if content else {}
except (json.JSONDecodeError, FileNotFoundError):
    settings = {}

mcp_servers = {
    'filesystem-command': {'type': 'sse', 'url': 'http://localhost:8001/mcp'},
    'git-tools':          {'type': 'sse', 'url': 'http://localhost:8002/mcp'},
    'code-intel':         {'type': 'sse', 'url': 'http://localhost:8003/mcp'},
    'memory-store':       {'type': 'sse', 'url': 'http://localhost:8004/mcp'},
    'code-review':        {'type': 'sse', 'url': 'http://localhost:8005/mcp'},
    'multi-project':      {'type': 'sse', 'url': 'http://localhost:8007/mcp'},
}

if 'mcp' not in settings:
    settings['mcp'] = {}
if 'servers' not in settings['mcp']:
    settings['mcp']['servers'] = {}

settings['mcp']['servers'].update(mcp_servers)
settings.setdefault('chat.agent.enabled', True)

with open(settings_path, 'w', encoding='utf-8') as f:
    json.dump(settings, f, indent=4)

print('OK')
"@

        if ($UseUv) {
            $pyScript | & uv run python - 2>$null
        } else {
            $pyScript | & $PythonCmd - 2>$null
        }
        Write-Ok "MCP servers registered in VS Code user settings"

        if (Test-Path (Join-Path $ScriptDir "code-hacker.agent.md")) {
            Write-Ok "Agent definition file: code-hacker.agent.md"
        } else {
            Write-Warn "code-hacker.agent.md not found in project root"
        }
    } else {
        Write-Info "VS Code not available - skipping"
    }

    Write-Host ""
}

# ─── Step 4: Start MCP Servers ────────────────────────────────────────
$stepNum = if ($ServersOnly) { "1" } else { "4" }
Write-Host "Step ${stepNum}/5: Starting MCP servers" -ForegroundColor White
Write-Host ""

if (-not (Test-Path $PidDir)) {
    New-Item -ItemType Directory -Path $PidDir -Force | Out-Null
}

# Determine python command for servers (uv > .venv > system python)
if (-not $PythonCmd) {
    try {
        & uv --version 2>$null | Out-Null
        $PythonCmd = "uv"
        $PythonArgs = @("run", "python")
        $UseUv = $true
    } catch {
        $venvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
        if (Test-Path $venvPython) {
            $PythonCmd = $venvPython
            $PythonArgs = @()
        } else {
            $PythonCmd = "python"
            $PythonArgs = @()
        }
    }
}

# Pre-flight: check that MCP dependencies are importable
$depCheckCmd = if ($UseUv) { "uv" } else { $PythonCmd }
$depCheckArgs = if ($UseUv) { @("run", "python", "-c", "import mcp, fastapi, uvicorn") } else { @("-c", "import mcp, fastapi, uvicorn") }

try {
    & $depCheckCmd $depCheckArgs 2>$null
    $depsOk = ($LASTEXITCODE -eq 0)
} catch {
    $depsOk = $false
}

if (-not $depsOk) {
    Write-Warn "MCP dependencies not found (mcp, fastapi, uvicorn)"
    if ($ServersOnly) {
        Write-Fail "Dependencies missing. Run '.\install.ps1' for full install, or 'uv sync' / 'pip install -e .' first."
    }
    Write-Info "Auto-installing dependencies..."
    try {
        & uv sync 2>$null
        $PythonCmd = "uv"
        $PythonArgs = @("run", "python")
        $UseUv = $true
        Write-Ok "Dependencies installed via uv"
    } catch {
        try {
            & $PythonCmd -m pip install -e . --quiet
            Write-Ok "Dependencies installed via pip"
        } catch {
            Write-Fail "No package manager available. Install uv: https://docs.astral.sh/uv/"
        }
    }
}

Write-Info "Using: $PythonCmd $(if ($PythonArgs) { $PythonArgs -join ' ' })"

$env:NO_PROXY = "localhost,127.0.0.1"

foreach ($srv in $Servers) {
    $script = $srv.Script
    $port = $srv.Port
    $name = $srv.Name
    $sname = $script -replace '\.py$', ''
    $logFile = Join-Path $PidDir "$sname.log"
    $pidFile = Join-Path $PidDir "$sname.pid"

    # Check if already running
    if (Test-Path $pidFile) {
        $existingPid = Get-Content $pidFile -ErrorAction SilentlyContinue
        try {
            $proc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
            if ($proc -and -not $proc.HasExited) {
                Write-Ok "$name (port $port) - already running (PID $existingPid)"
                continue
            }
        } catch {}
    }

    Write-Host "  Starting $name on port $port ... " -NoNewline

    $scriptPath = Join-Path $ScriptDir $script

    if ($UseUv) {
        $process = Start-Process -FilePath "uv" `
            -ArgumentList "run", "python", $scriptPath `
            -WorkingDirectory $ScriptDir `
            -RedirectStandardOutput $logFile `
            -RedirectStandardError (Join-Path $PidDir "${sname}_err.log") `
            -PassThru -WindowStyle Hidden
    } else {
        $process = Start-Process -FilePath $PythonCmd `
            -ArgumentList $scriptPath `
            -WorkingDirectory $ScriptDir `
            -RedirectStandardOutput $logFile `
            -RedirectStandardError (Join-Path $PidDir "${sname}_err.log") `
            -PassThru -WindowStyle Hidden
    }

    $process.Id | Set-Content $pidFile
    Start-Sleep -Milliseconds 500

    try {
        $check = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
        if ($check -and -not $check.HasExited) {
            Write-Host "OK (PID $($process.Id))" -ForegroundColor Green
        } else {
            Write-Host "FAILED" -ForegroundColor Red
            Write-Host "    Log: $logFile"
        }
    } catch {
        Write-Host "FAILED" -ForegroundColor Red
    }
}

Write-Host ""

# ─── Step 5: Health Check ────────────────────────────────────────────
$stepNum = if ($ServersOnly) { "2" } else { "5" }
Write-Host "Step ${stepNum}/5: Verifying servers" -ForegroundColor White
Write-Host ""

Start-Sleep -Seconds 2

$pass = 0
$total = 0

foreach ($srv in $Servers) {
    $port = $srv.Port
    $name = $srv.Name
    $total++

    try {
        $response = Invoke-WebRequest -Uri "http://localhost:$port/mcp" `
            -Method GET -TimeoutSec 3 -UseBasicParsing -ErrorAction SilentlyContinue
        $code = $response.StatusCode
    } catch {
        if ($_.Exception.Response) {
            $code = [int]$_.Exception.Response.StatusCode
        } else {
            $code = 0
        }
    }

    if ($code -eq 200 -or $code -eq 405) {
        Write-Ok "$name  http://localhost:${port}/mcp"
        $pass++
    } else {
        Write-Warn "$name  http://localhost:${port}/mcp  (HTTP $code)"
    }
}

Write-Host ""

# ─── Summary ─────────────────────────────────────────────────────────
Write-Host "==========================================" -ForegroundColor Cyan
if ($pass -eq $total) {
    Write-Host "  Installation complete! ($pass/$total servers running)" -ForegroundColor Green
} else {
    Write-Host "  Installation complete. ($pass/$total servers running)" -ForegroundColor Yellow
    Write-Host "  Check logs: Get-ChildItem $PidDir\*.log"
}
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor White
Write-Host "  1. Open VS Code in this project directory:"
Write-Host "       code $ScriptDir"
Write-Host ""
Write-Host "  2. Open Copilot Chat (Ctrl+Shift+I)"
Write-Host "     Select 'Code Hacker' from the agent dropdown"
Write-Host ""
Write-Host "  3. MCP Server Endpoints:"
foreach ($srv in $Servers) {
    Write-Host "       $($srv.Name): http://localhost:$($srv.Port)/mcp"
}
Write-Host ""
Write-Host "Management:" -ForegroundColor White
Write-Host "  Stop servers:    bash $ScriptDir\start_servers.sh stop"
Write-Host "                   (or close PowerShell / kill python processes)"
Write-Host "  Restart servers: .\install.ps1 -ServersOnly"
Write-Host "  Server status:   bash $ScriptDir\start_servers.sh status"
Write-Host ""
