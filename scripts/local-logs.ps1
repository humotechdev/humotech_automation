<#
.SYNOPSIS
    Логи стенда.

.DESCRIPTION
    Без аргументов — последние строки всех сервисов сразу: так видно,
    что с чем совпало по времени. С именем сервиса — только он.

.PARAMETER Service
    postgres, migrate, backend, bot, gateway или cloudflared.

.PARAMETER Tail
    Сколько последних строк показать. По умолчанию 100.

.PARAMETER Follow
    Не выходить, показывать новые строки по мере появления (Ctrl+C).

.EXAMPLE
    scripts\local-logs.ps1 -Service backend -Follow

.EXAMPLE
    scripts\local-logs.ps1 -Tail 300
#>

[CmdletBinding()]
param(
    [ValidateSet('postgres', 'migrate', 'backend', 'bot', 'gateway', 'cloudflared')]
    [string]$Service,

    [int]$Tail = 100,

    [switch]$Follow
)

. "$PSScriptRoot\_local-common.ps1"

if (-not (Test-DockerEngine)) {
    throw 'Docker не запущен.'
}

# Файлы compose требуют CLOUDFLARE_TUNNEL_TOKEN уже при чтении:
# без него docker compose не соберёт конфигурацию и ответит своей
# ошибкой вместо понятной. Проверяем раньше и говорим по делу.
Assert-TunnelToken

$arguments = @('logs', '--tail', "$Tail")
if ($Follow) { $arguments += '--follow' }
if ($Service) { $arguments += $Service }

# Токены в логи не попадают: бот их не печатает, cloudflared показывает
# только идентификатор соединения. Но если однажды что-то утечёт, эти
# строки уйдут на экран — читайте, прежде чем пересылать.
Invoke-Compose -ComposeArgs $arguments
