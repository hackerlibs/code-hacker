#
# Code Hacker — Server Management (Windows PowerShell)
#
# Usage:
#   .\stop_servers.ps1            # Stop all MCP servers
#   .\stop_servers.ps1 -Status    # Check server status
#

param(
    [switch]$Status
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidDir = Join-Path $ScriptDir ".mcp_pids"

$Servers = @(
    @{ Script = "filesystem.py";    Port = 8001; Name = "filesystem-command" },
    @{ Script = "git_tools.py";     Port = 8002; Name = "git-tools" },
    @{ Script = "code_intel.py";    Port = 8003; Name = "code-intel" },
    @{ Script = "memory_store.py";  Port = 8004; Name = "memory-store" },
    @{ Script = "code_review.py";   Port = 8005; Name = "code-review" },
    @{ Script = "multi_project.py"; Port = 8007; Name = "multi-project" }
)

if ($Status) {
    Write-Host "=== MCP Server Status ===" -ForegroundColor Cyan
    Write-Host ""

    foreach ($srv in $Servers) {
        $sname = $srv.Script -replace '\.py$', ''
        $pidFile = Join-Path $PidDir "$sname.pid"
        $running = $false

        if (Test-Path $pidFile) {
            $pid = Get-Content $pidFile -ErrorAction SilentlyContinue
            try {
                $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
                if ($proc -and -not $proc.HasExited) { $running = $true }
            } catch {}
        }

        if ($running) {
            Write-Host "  [UP]   $($srv.Name) - port $($srv.Port) - PID $pid" -ForegroundColor Green
        } else {
            Write-Host "  [DOWN] $($srv.Name) - port $($srv.Port)" -ForegroundColor Red
        }
    }
} else {
    Write-Host "=== Stopping MCP Servers ===" -ForegroundColor Cyan
    Write-Host ""

    foreach ($srv in $Servers) {
        $sname = $srv.Script -replace '\.py$', ''
        $pidFile = Join-Path $PidDir "$sname.pid"

        if (Test-Path $pidFile) {
            $pid = Get-Content $pidFile -ErrorAction SilentlyContinue
            try {
                $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
                if ($proc -and -not $proc.HasExited) {
                    Stop-Process -Id $pid -Force
                    Write-Host "  [STOP] $($srv.Name) (PID $pid)" -ForegroundColor Yellow
                } else {
                    Write-Host "  [SKIP] $($srv.Name) - not running" -ForegroundColor Gray
                }
            } catch {
                Write-Host "  [SKIP] $($srv.Name) - not running" -ForegroundColor Gray
            }
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        } else {
            Write-Host "  [SKIP] $($srv.Name) - no PID file" -ForegroundColor Gray
        }
    }

    Write-Host ""
    Write-Host "All servers stopped." -ForegroundColor Green
}
