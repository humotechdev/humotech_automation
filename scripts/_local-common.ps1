# Общая часть скриптов стенда. Подключается через точку:
#
#     . "$PSScriptRoot\_local-common.ps1"
#
# Здесь только то, что нужно всем пяти: где лежит проект, как позвать
# compose и как дождаться Docker. Держать это в каждом скрипте отдельно
# означало бы пять разных представлений о том, где корень репозитория.

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$DockerDir = Join-Path $Root 'infrastructure\docker'
$EnvFile = Join-Path $DockerDir '.env'
$EnvExample = Join-Path $DockerDir '.env.example'

# Оба файла всегда вместе и всегда в этом порядке: описание PostgreSQL
# берётся из базового, надстройка добавляет остальное.
$ComposeFiles = @(
    '-f', (Join-Path $DockerDir 'docker-compose.yml'),
    '-f', (Join-Path $DockerDir 'docker-compose.local.yml')
)

$Services = @('postgres', 'migrate', 'backend', 'bot', 'gateway', 'cloudflared')

function Write-Step { param([string]$Text) Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok { param([string]$Text) Write-Host "    OK   $Text" -ForegroundColor Green }
function Write-Bad { param([string]$Text) Write-Host "    ХУДО $Text" -ForegroundColor Red }
function Write-Meh { param([string]$Text) Write-Host "    ??   $Text" -ForegroundColor Yellow }

function Invoke-Compose {
    <#
        Вызов docker compose с обоими файлами и из нужного каталога.
        Возвращает вывод, код возврата остаётся в $LASTEXITCODE.
    #>
    param([Parameter(Mandatory = $true)][string[]]$ComposeArgs)

    # Аргументы приходят одним массивом, а не «остатком строки».
    # Иначе PowerShell пытается связать `-o` и `-f` из команды docker
    # со своими параметрами и отказывается: «parameter name 'o' is
    # ambiguous». Нативной команде массив раскрывается как есть.
    Push-Location $DockerDir
    try {
        & docker compose @ComposeFiles @ComposeArgs
    }
    finally {
        Pop-Location
    }
}

function Test-DockerEngine {
    docker info 2>&1 | Out-Null
    return $LASTEXITCODE -eq 0
}

function Start-DockerEngine {
    <#
        Поднять Docker Desktop, если он не запущен, и дождаться демона.

        Долго — до пяти минут: на холодном старте поднимается ещё и
        виртуальная машина Linux, и раньше неё демон не отвечает.
    #>
    param([int]$TimeoutSeconds = 300)

    if (Test-DockerEngine) { return }

    $exe = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'
    if (-not (Test-Path $exe)) {
        $exe = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
    }
    if (-not (Test-Path $exe)) {
        throw "Docker Desktop не найден. Запустите его вручную и повторите."
    }

    Write-Host '    Docker не отвечает, запускаю Docker Desktop...'
    Start-Process $exe | Out-Null

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerEngine) { return }
        Start-Sleep -Seconds 3
    }
    throw "Docker Desktop не поднялся за $TimeoutSeconds с."
}

function Read-LocalEnv {
    <#
        Настройки стенда из infrastructure/docker/.env.

        Значения возвращаются как есть; печатать их нельзя — там токен
        туннеля.
    #>
    $map = @{}
    if (-not (Test-Path $EnvFile)) { return $map }
    foreach ($line in Get-Content -LiteralPath $EnvFile -Encoding UTF8) {
        $trimmed = $line.Trim()
        if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
        $split = $trimmed.IndexOf('=')
        if ($split -lt 1) { continue }
        $map[$trimmed.Substring(0, $split).Trim()] = $trimmed.Substring($split + 1).Trim()
    }
    return $map
}

function Get-PublicHostname {
    $settings = Read-LocalEnv
    if ($settings.ContainsKey('PUBLIC_HOSTNAME') -and $settings['PUBLIC_HOSTNAME']) {
        return $settings['PUBLIC_HOSTNAME']
    }
    return 'hr-dev.humotech.com'
}

function Assert-TunnelToken {
    <#
        Без токена туннеля стек поднимется, но публичного адреса не будет.
        Сказать об этом здесь дешевле, чем разбирать потом, почему
        cloudflared перезапускается по кругу.
    #>
    if (-not (Test-Path $EnvFile)) {
        throw "Нет $EnvFile. Скопируйте $EnvExample и впишите CLOUDFLARE_TUNNEL_TOKEN."
    }
    $settings = Read-LocalEnv
    if (-not $settings['CLOUDFLARE_TUNNEL_TOKEN']) {
        throw "В $EnvFile пуст CLOUDFLARE_TUNNEL_TOKEN. Где его взять — написано в .env.example."
    }
}

function Get-ContainerState {
    <#
        Состояние одного контейнера: running/exited и healthy/unhealthy.
        Возвращает объект, а не строку: печатать его будет вызывающий.
    #>
    param([string]$Name)

    # Существование проверяется через `docker ps`, а не через `inspect`.
    # `inspect` несуществующего контейнера пишет в stderr, а Windows
    # PowerShell при $ErrorActionPreference = 'Stop' превращает вывод
    # нативной команды в stderr в терминирующую ошибку — скрипт падал бы
    # ровно там, где должен спокойно сказать «контейнера нет».
    $existing = docker ps -a --filter "name=^/$Name$" --format '{{.Names}}'
    if (-not $existing) {
        return [pscustomobject]@{ Name = $Name; Exists = $false; Status = 'нет'; Health = '-'; ExitCode = $null }
    }

    $raw = docker inspect $Name --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}|{{.State.ExitCode}}'
    if (-not $raw) {
        return [pscustomobject]@{ Name = $Name; Exists = $false; Status = 'нет'; Health = '-'; ExitCode = $null }
    }
    $parts = $raw.Trim().Split('|')
    return [pscustomobject]@{
        Name     = $Name
        Exists   = $true
        Status   = $parts[0]
        Health   = $parts[1]
        ExitCode = [int]$parts[2]
    }
}

function Wait-ForHealthy {
    <#
        Дождаться, пока контейнер станет healthy.

        Разовый сервис миграций здоровым не становится — он завершается,
        поэтому для него условие другое: вышел с нулевым кодом.
    #>
    param([string]$Name, [int]$TimeoutSeconds = 300, [switch]$OneShot)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $state = Get-ContainerState -Name $Name
        if ($OneShot) {
            if ($state.Status -eq 'exited' -and $state.ExitCode -eq 0) { return $true }
            if ($state.Status -eq 'exited' -and $state.ExitCode -ne 0) {
                throw "$Name завершился с кодом $($state.ExitCode). Логи: scripts\local-logs.ps1 -Service migrate"
            }
        }
        else {
            if ($state.Health -eq 'healthy') { return $true }
            if ($state.Health -eq '-' -and $state.Status -eq 'running') { return $true }
        }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Test-Url {
    <#
        Проверка адреса без исключений: возвращается код ответа или 0.
    #>
    param([string]$Url, [int]$TimeoutSeconds = 20)

    try {
        $response = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSeconds -UseBasicParsing -ErrorAction Stop
        return [int]$response.StatusCode
    }
    catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            return [int]$_.Exception.Response.StatusCode
        }
        return 0
    }
}
