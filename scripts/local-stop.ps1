<#
.SYNOPSIS
    Остановить стенд, ничего не удаляя.

.DESCRIPTION
    Используется `docker compose stop`, а не `down`. Разница
    существенная: `stop` гасит процессы и оставляет контейнеры на
    месте, `down` их удаляет, а `down -v` вместе с ними стирает тома —
    то есть базу и все загруженные документы.

    `down -v` в этих скриптах нет и не будет. Если понадобится стереть
    данные, это делается руками и осознанно.

    После остановки Docker больше не поднимет контейнеры автоматически:
    ручная остановка сильнее политики `unless-stopped`. Обратно —
    scripts\local-start.ps1.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\local-stop.ps1
#>

[CmdletBinding()]
param()

. "$PSScriptRoot\_local-common.ps1"

if (-not (Test-DockerEngine)) {
    Write-Host 'Docker не запущен — останавливать нечего.' -ForegroundColor Yellow
    return
}

Write-Step 'Остановка контейнеров'
Invoke-Compose -ComposeArgs @('stop')
if ($LASTEXITCODE -ne 0) { throw "docker compose stop завершился с кодом $LASTEXITCODE" }

Write-Host ''
Write-Host 'Остановлено. Данные на месте:' -ForegroundColor Green
Write-Host '  docker_postgres_data  база'
Write-Host '  docker_private_media  справки и больничные'
Write-Host '  docker_static_files   статика админки'
Write-Host ''
Write-Host 'Публичный адрес сохранён: домен закреплён за аккаунтом ngrok'
Write-Host 'и от контейнеров не зависит.'
Write-Host 'Пока стенд остановлен, Mini App отвечать не будет.'
Write-Host 'Обратно: scripts\local-start.ps1'
