<#
.SYNOPSIS
    Поднять стенд HUMOTECH целиком.

.DESCRIPTION
    Обычно запускать не нужно: контейнеры помечены `restart: unless-stopped`
    и поднимаются сами, как только запустится Docker Engine. Этот скрипт
    нужен после `local-stop.ps1`, после первой настройки и когда что-то
    пошло не так и хочется убедиться, что всё на месте.

    Публичный адрес не меняется никогда: он живёт в Cloudflare, а не
    в этих скриптах. Ни кнопку бота, ни адрес Mini App после запуска
    трогать не требуется.

.PARAMETER Build
    Пересобрать образы перед запуском. Нужно только после правки кода;
    для этого есть отдельный local-update.ps1, который ещё и миграции
    прогонит.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\local-start.ps1
#>

[CmdletBinding()]
param(
    [switch]$Build
)

. "$PSScriptRoot\_local-common.ps1"

Write-Step 'Docker Engine'
Start-DockerEngine
Write-Ok 'демон отвечает'

Write-Step 'Настройки стенда'
Assert-TunnelToken
$publicHost = Get-PublicHostname
Write-Ok "публичный адрес: https://$publicHost"

Write-Step 'Запуск контейнеров'
if ($Build) {
    Invoke-Compose -ComposeArgs @('up', '-d', '--build')
}
else {
    Invoke-Compose -ComposeArgs @('up', '-d')
}
if ($LASTEXITCODE -ne 0) { throw "docker compose up завершился с кодом $LASTEXITCODE" }

Write-Step 'Ожидание готовности'

# Порядок тот же, в котором сервисы зависят друг от друга. Ждать backend
# раньше миграций бессмысленно: пока схема не применена, /readyz честно
# отвечает 503, и контейнер не станет здоровым.
if (-not (Wait-ForHealthy -Name 'humotech_postgres' -TimeoutSeconds 120)) {
    throw 'PostgreSQL не стал здоровым'
}
Write-Ok 'PostgreSQL'

if (-not (Wait-ForHealthy -Name 'humotech_migrate' -TimeoutSeconds 300 -OneShot)) {
    throw 'Миграции не завершились'
}
Write-Ok 'миграции применены'

if (-not (Wait-ForHealthy -Name 'humotech_backend' -TimeoutSeconds 300)) {
    throw 'backend не стал здоровым. Логи: scripts\local-logs.ps1 -Service backend'
}
Write-Ok 'backend'

foreach ($name in @('humotech_gateway', 'humotech_bot', 'humotech_cloudflared')) {
    if (Wait-ForHealthy -Name $name -TimeoutSeconds 120) {
        Write-Ok $name.Replace('humotech_', '')
    }
    else {
        Write-Meh "$($name.Replace('humotech_', '')) не подтвердил готовность"
    }
}

Write-Host ''
Write-Host 'Стенд поднят.' -ForegroundColor Green
Write-Host "  Mini App   https://$publicHost/"
Write-Host "  API        https://$publicHost/api/v1/"
Write-Host "  готовность https://$publicHost/health/ready"
Write-Host ''
Write-Host 'Подробная проверка: scripts\local-status.ps1'
