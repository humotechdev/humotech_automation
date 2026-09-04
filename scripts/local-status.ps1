<#
.SYNOPSIS
    Что сейчас работает и отвечает ли публичный адрес.

.DESCRIPTION
    Проверяется вся цепочка снизу вверх: Docker, контейнеры, локальная
    готовность, публичный HTTPS и страница Mini App. Порядок не
    случайный — первая красная строка сверху и есть причина, всё, что
    ниже, обычно следствие.

    Ни одного секрета в выводе: токен туннеля и токен бота не читаются
    и не печатаются.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\local-status.ps1
#>

[CmdletBinding()]
param()

. "$PSScriptRoot\_local-common.ps1"

$problems = 0

# Файлы compose требуют CLOUDFLARE_TUNNEL_TOKEN уже при чтении:
# без него docker compose не соберёт конфигурацию и ответит своей
# ошибкой вместо понятной. Проверяем раньше и говорим по делу.
Assert-TunnelToken

Write-Step 'Docker Engine'
if (Test-DockerEngine) {
    Write-Ok 'демон отвечает'
}
else {
    Write-Bad 'демон не отвечает — запустите Docker Desktop'
    Write-Host ''
    Write-Host 'Дальше проверять нечего.' -ForegroundColor Red
    exit 1
}

Write-Step 'Контейнеры'

# Разовый сервис миграций оценивается иначе: для него «здоров» — это
# «вышел с нулевым кодом», а не «работает».
$expected = [ordered]@{
    'humotech_postgres'    = 'PostgreSQL'
    'humotech_migrate'     = 'миграции (разовый)'
    'humotech_backend'     = 'backend (Django + gunicorn)'
    'humotech_bot'         = 'Telegram-бот'
    'humotech_gateway'     = 'обратный прокси + Mini App'
    'humotech_cloudflared' = 'Cloudflare Tunnel'
}

foreach ($name in $expected.Keys) {
    $label = $expected[$name]
    $state = Get-ContainerState -Name $name

    if (-not $state.Exists) {
        Write-Bad "$label — контейнера нет"
        $problems++
        continue
    }

    if ($name -eq 'humotech_migrate') {
        if ($state.Status -eq 'exited' -and $state.ExitCode -eq 0) {
            Write-Ok "$label — отработал"
        }
        elseif ($state.Status -eq 'running') {
            Write-Meh "$label — ещё выполняется"
        }
        else {
            Write-Bad "$label — код возврата $($state.ExitCode)"
            $problems++
        }
        continue
    }

    if ($state.Status -ne 'running') {
        Write-Bad "$label — $($state.Status)"
        $problems++
    }
    elseif ($state.Health -eq 'healthy' -or $state.Health -eq '-') {
        $suffix = ''
        if ($state.Health -eq '-') { $suffix = ' (проверки здоровья нет)' }
        Write-Ok "$label — работает$suffix"
    }
    elseif ($state.Health -eq 'starting') {
        # Не проблема: у контейнера есть льготный период на разогрев,
        # и красная строка здесь означала бы «сломано» там, где просто
        # «ещё не проверялось».
        Write-Meh "$label — работает, проверка ещё не отвечала"
    }
    else {
        Write-Bad "$label — работает, но $($state.Health)"
        $problems++
    }
}

# Рабочих процессов отдельными сервисами в проекте нет, и это не
# упущение: очередь уведомлений разбирает сам бот через HTTP, а очередь
# выгрузок пока без исполнителя — модель есть, worker'а нет.
Write-Meh 'отдельных worker-контейнеров нет — см. docs/local-always-on.md'

Write-Step 'Локальные проверки'
$local = Invoke-Compose -ComposeArgs @(
    'exec', '-T', 'backend',
    'curl', '-fsS', '-o', '/dev/null', '-w', '%{http_code}',
    # Оба заголовка обязательны: без Host придёт 400 от проверки хостов,
    # без X-Forwarded-Proto — 301 от принудительного HTTPS.
    '-H', "Host: $(Get-PublicHostname)",
    '-H', 'X-Forwarded-Proto: https',
    'http://127.0.0.1:8000/readyz'
)
if ($LASTEXITCODE -eq 0) {
    Write-Ok "готовность backend внутри контейнера: HTTP $local"
}
else {
    Write-Bad 'backend не подтверждает готовность внутри контейнера'
    $problems++
}

Write-Step 'Публичный адрес'
$publicHost = Get-PublicHostname
$checks = [ordered]@{
    "https://$publicHost/health/live"  = 'живость через туннель'
    "https://$publicHost/health/ready" = 'готовность через туннель'
    "https://$publicHost/"             = 'страница Mini App'
}
foreach ($url in $checks.Keys) {
    $code = Test-Url -Url $url
    if ($code -ge 200 -and $code -lt 400) {
        Write-Ok "$($checks[$url]): HTTP $code"
    }
    elseif ($code -eq 0) {
        Write-Bad "$($checks[$url]): нет ответа ($url)"
        $problems++
    }
    else {
        Write-Bad "$($checks[$url]): HTTP $code"
        $problems++
    }
}

Write-Host ''
if ($problems -eq 0) {
    Write-Host 'Всё на месте.' -ForegroundColor Green
    Write-Host "  Mini App  https://$publicHost/"
}
else {
    Write-Host "Проблем: $problems" -ForegroundColor Red
    Write-Host 'Логи: scripts\local-logs.ps1 -Service backend'
    exit 1
}
