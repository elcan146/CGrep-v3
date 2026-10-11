# Start all cgrep dependencies and the main server.
# Run as:  powershell -ExecutionPolicy Bypass -File c:\projects\CGrep-v3\start_all.ps1

$BaseDir = "c:\projects\CGrep-v3"
$OpenWADir = "$BaseDir\OpenWA"
$ApiKeyFile = "$OpenWADir\data\.api-key"
$SessionId = "YOUR_OPENWA_SESSION_ID"

function Test-Port($HostName, $Port) {
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect($HostName, $Port)
        $tcp.Close()
        return $true
    } catch {
        return $false
    }
}

function Start-ServiceWindow($Title, $Directory, $Command) {
    $ArgumentList = "-NoExit", "-Command", "cd `"$Directory`"; $host.ui.RawUI.WindowTitle = '$Title'; $Command"
    Start-Process powershell -ArgumentList $ArgumentList
    Write-Host "Started $Title" -ForegroundColor Green
}

function Wait-ForPort($ServiceName, $Port, $TimeoutSeconds = 60) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Port localhost $Port)) {
        if ($sw.Elapsed.TotalSeconds -gt $TimeoutSeconds) {
            throw "$ServiceName did not become ready on port $Port within $TimeoutSeconds seconds"
        }
        Start-Sleep -Seconds 1
    }
    Write-Host "$ServiceName is ready on port $Port" -ForegroundColor Green
}

function Get-OpenWAApiKey() {
    if (Test-Path $ApiKeyFile) {
        return (Get-Content $ApiKeyFile -Raw).Trim()
    }
    return $null
}

function Start-OpenWASession() {
    $apiKey = Get-OpenWAApiKey
    if (-not $apiKey) {
        Write-Host "Could not read OpenWA API key from $ApiKeyFile. Session must be started manually." -ForegroundColor Yellow
        return
    }
    $headers = @{ "X-API-Key" = $apiKey }
    try {
        Invoke-WebRequest -Uri "http://localhost:2785/api/sessions/$SessionId/start" -Method POST -Headers $headers -UseBasicParsing | Out-Null
        Write-Host "Started OpenWA session $SessionId" -ForegroundColor Green
    } catch {
        Write-Host "OpenWA session start returned: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

Write-Host "Starting cgrep services..." -ForegroundColor Cyan

# Ollama (port 11434)
if (Test-Port localhost 11434) {
    Write-Host "Ollama is already running on port 11434" -ForegroundColor Yellow
} else {
    Start-ServiceWindow "Ollama" $BaseDir "ollama serve"
}

# LibreTranslate (port 5000)
if (Test-Port localhost 5000) {
    Write-Host "LibreTranslate is already running on port 5000" -ForegroundColor Yellow
} else {
    Start-ServiceWindow "LibreTranslate" $BaseDir "libretranslate"
}

# OpenWA (port 2785)
if (Test-Port localhost 2785) {
    Write-Host "OpenWA is already running on port 2785" -ForegroundColor Yellow
} else {
    Start-ServiceWindow "OpenWA" $OpenWADir "npm run dev"
    Wait-ForPort "OpenWA" 2785 -TimeoutSeconds 60
    Start-OpenWASession
}

# cgrep server
if (Test-Port localhost 8000) {
    Write-Host "cgrep server is already running on port 8000" -ForegroundColor Yellow
} else {
    Start-ServiceWindow "cgrep Server" $BaseDir "python server.py"
}

# Cloudflare tunnel
Start-ServiceWindow "Cloudflare Tunnel" $BaseDir "cloudflared tunnel run YOUR_CLOUDFLARE_TUNNEL_ID"

Write-Host "All services started. If OpenWA asks for a QR, scan it with WhatsApp." -ForegroundColor Cyan
