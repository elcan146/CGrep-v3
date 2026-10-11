# Stop all cgrep-related services.
# Run as:  powershell -ExecutionPolicy Bypass -File c:\projects\CGrep-v3\stop_all.ps1

$Targets = @(
    @{ Name = "Ollama"; Pattern = "ollama" },
    @{ Name = "LibreTranslate"; Pattern = "libretranslate" },
    @{ Name = "OpenWA"; Pattern = "OpenWA" },
    @{ Name = "Cloudflare Tunnel"; Pattern = "cloudflared" },
    @{ Name = "cgrep Server"; Pattern = "server.py" }
)

# Also kill the legacy start.py wrapper if it's still running
$StartPy = Get-CimInstance Win32_Process |
    Where-Object { $_.ProcessName -match "python" -and $_.CommandLine -match "start\.py" }
if ($StartPy) {
    Stop-Process -Id $StartPy.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped legacy start.py wrapper (PID $($StartPy.ProcessId))" -ForegroundColor Green
}

foreach ($t in $Targets) {
    $procs = Get-CimInstance Win32_Process |
        Where-Object { $_.ProcessName -match "^(python|node|cloudflared|ollama|libretranslate)" -and $_.CommandLine -match $t.Pattern }
    if ($procs) {
        foreach ($p in $procs) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped $($t.Name) (PID $($p.ProcessId))" -ForegroundColor Green
        }
    } else {
        Write-Host "$($t.Name) not found" -ForegroundColor Yellow
    }
}

Write-Host "Done." -ForegroundColor Cyan
