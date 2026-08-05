# One-shot setup: writes a working .env (random secret key + DB password,
# prompts for the hostname you'll browse Spool through) and brings the
# stack up. Safe to re-run - it never overwrites an existing .env.
Set-Location $PSScriptRoot

function New-RandomHex($bytes) {
    $buf = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($buf)
    -join ($buf | ForEach-Object { $_.ToString('x2') })
}

if (Test-Path .env) {
    Write-Host ".env already exists - leaving it alone. Delete it first if you want setup.ps1 to regenerate it."
} else {
    Copy-Item .env.example .env

    $secretKey = New-RandomHex 32
    $dbPassword = New-RandomHex 32
    $allowedHost = Read-Host "Hostname/IP you'll access Spool through [localhost]"
    if ([string]::IsNullOrWhiteSpace($allowedHost)) { $allowedHost = "localhost" }

    $content = Get-Content .env -Raw
    $content = $content -replace '(?m)^DJANGO_SECRET_KEY=.*$', "DJANGO_SECRET_KEY=$secretKey"
    $content = $content -replace '(?m)^DB_PASSWORD=.*$', "DB_PASSWORD=$dbPassword"
    $content = $content -replace '(?m)^DATABASE_URL=.*$', "DATABASE_URL=postgres://spool:$dbPassword@db:5432/spool"
    $content = $content -replace '(?m)^DJANGO_ALLOWED_HOSTS=.*$', "DJANGO_ALLOWED_HOSTS=$allowedHost"
    Set-Content .env -Value $content -Encoding utf8 -NoNewline

    Write-Host "Wrote .env with a random DJANGO_SECRET_KEY and DB_PASSWORD."
    Write-Host "Edit .env now if you want Trakt/Simkl/TMDB integration, HTTPS via a reverse proxy, or a non-default admin password - see docs/CONFIGURATION.md."
}

Write-Host "Starting Spool (this builds the image and runs migrations - first run can take a minute or two)..."
docker compose up -d --build

Write-Host ""
Write-Host "Done. Once 'docker compose ps' shows all five services healthy, open http://<the host you entered>:8000"
Write-Host "and sign in with ADMIN_USERNAME/ADMIN_PASSWORD from .env (still the 'changeme' default unless you edited it - see docs/QUICKSTART.md's First login section)."
