param([ValidateSet('up','status','logs','test','stop')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    switch ($Action) {
        'up' { docker compose up -d --build }
        'status' { docker compose ps -a }
        'logs' { docker compose logs --tail 100 }
        'test' { docker compose exec -T api python -m pytest tests/integration -v }
        'stop' { docker compose stop }
    }
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
