<#
.SYNOPSIS
    Обновить стенд после правки кода.

.DESCRIPTION
    Пересобирает образы, прогоняет миграции и перезапускает то, что
    изменилось. База и загруженные документы не трогаются: тома
    переживают пересборку, а `down -v` здесь нет.

    Порядок держит compose: миграции выполняет отдельный разовый сервис,
    и backend не поднимется, пока тот не завершится успешно. Поэтому
    ситуация «новый код на старой схеме» невозможна — не потому, что
    так сложилось, а потому, что зависимость объявлена.

.PARAMETER SkipTests
    Не запускать тесты перед сборкой. По умолчанию тесты выполняются:
    выкатывать на стенд то, что не проходит их у себя, — способ
    выяснить это позже и дороже.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\local-update.ps1
#>

[CmdletBinding()]
param(
    [switch]$SkipTests
)

. "$PSScriptRoot\_local-common.ps1"

Start-DockerEngine
Assert-TunnelToken

if (-not $SkipTests) {
    Write-Step 'Тесты backend'
    $python = Join-Path $Root 'apps\backend-api\.venv\Scripts\python.exe'
    if (Test-Path $python) {
        Push-Location (Join-Path $Root 'apps\backend-api')
        try {
            & $python -m pytest -q
            if ($LASTEXITCODE -ne 0) {
                throw 'Тесты backend не прошли. Сборка остановлена.'
            }
        }
        finally {
            Pop-Location
        }
        Write-Ok 'backend'
    }
    else {
        Write-Meh 'окружение backend не найдено, тесты пропущены'
    }

    Write-Step 'Тесты бота'
    $botPython = Join-Path $Root 'apps\employee-telegram-bot\.venv\Scripts\python.exe'
    if (Test-Path $botPython) {
        Push-Location (Join-Path $Root 'apps\employee-telegram-bot')
        try {
            & $botPython -m pytest -q
            if ($LASTEXITCODE -ne 0) {
                throw 'Тесты бота не прошли. Сборка остановлена.'
            }
        }
        finally {
            Pop-Location
        }
        Write-Ok 'бот'
    }
    else {
        Write-Meh 'окружение бота не найдено, тесты пропущены'
    }
}

Write-Step 'Сборка образов'
Invoke-Compose -ComposeArgs @('build')
if ($LASTEXITCODE -ne 0) { throw "Сборка завершилась с кодом $LASTEXITCODE" }

Write-Step 'Перезапуск изменившегося'
Invoke-Compose -ComposeArgs @('up', '-d')
if ($LASTEXITCODE -ne 0) { throw "docker compose up завершился с кодом $LASTEXITCODE" }

if (-not (Wait-ForHealthy -Name 'humotech_migrate' -TimeoutSeconds 300 -OneShot)) {
    throw 'Миграции не завершились'
}
Write-Ok 'миграции применены'

if (-not (Wait-ForHealthy -Name 'humotech_backend' -TimeoutSeconds 300)) {
    throw 'backend не стал здоровым. Логи: scripts\local-logs.ps1 -Service backend'
}
Write-Ok 'backend'

Write-Host ''
Write-Host 'Обновлено. Адрес не изменился.' -ForegroundColor Green
Write-Host 'Проверка: scripts\local-status.ps1'
