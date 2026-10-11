$apiKey = (Get-Content "c:\projects\CGrep-v3\OpenWA\data\.api-key" -Raw).Trim()
$base   = "http://localhost:2785"
$sessionId = "YOUR_OPENWA_SESSION_ID"
$headers = @{ "X-API-Key" = $apiKey }

while ($true) {
    $ok = $false
    try {
        $r = Invoke-RestMethod -Uri "$base/api/sessions/$sessionId/status" -Headers $headers -Method GET
        # OpenWA sometimes uses 'state', sometimes 'status'
        $state = if ($r.PSObject.Properties['state']) { $r.state } else { $r.status }
        if ($state -in @("CONNECTED", "OPENING")) {
            $ok = $true
            Write-Host "$(Get-Date -Format 'o') session state: $state"
        } else {
            Write-Host "$(Get-Date -Format 'o') session state: $state — restarting"
        }
    } catch {
        Write-Host "$(Get-Date -Format 'o') status check failed: $_"
    }

    if (-not $ok) {
        try {
            Invoke-RestMethod -Uri "$base/api/sessions/$sessionId/start" -Method POST -Headers $headers | Out-Null
            Write-Host "$(Get-Date -Format 'o') start called"
        } catch {
            Write-Host "$(Get-Date -Format 'o') start failed: $_"
        }
    }

    Start-Sleep -Seconds 30
}