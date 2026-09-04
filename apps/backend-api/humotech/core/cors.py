"""CORS — по зонам: свой префикс пути, свой список origin'ов.

Браузерных клиентов у этого API три, и у каждого свои границы:

  * **Mini App** открывается Telegram по своему адресу, а к API обращается
    по нашему. Ему нужны endpoint'ы входа и весь личный кабинет;
  * **экран показа QR** висит в офисе и живёт на своём адресе. Ему нужен
    ровно один префикс — выдача кодов, — и origin'ы у него ДРУГИЕ: список
    Mini App не должен открывать доступ к выдаче кодов, а список экранов —
    к личным данным;
  * **CRM** ходит с того же origin'а и в CORS не участвует вовсе.

Отсюда зоны, а не один общий список. Общий список означал бы, что добавление
адреса Mini App попутно открывает выдачу QR — то есть решение, принятое по
одному поводу, тихо действует в другом месте.

Отдельная библиотека не ставится намеренно. `django-cors-headers` включает
общие правила на весь проект, а согласовывать их пришлось бы с CRM, ботом
и админкой; здесь же нужно разрешить перечисленные префиксы перечисленным
origin'ам — это тридцать строк с точными границами.

Границы, общие для всех зон:

  * префикс пути фиксирован. Другие endpoint'ы браузеру из чужого origin'а
    недоступны, даже если origin в списке;
  * список origin'ов задаётся окружением. Пустой список = зона закрыта,
    а не «разрешено всем» — умолчание обязано быть закрытым;
  * `Access-Control-Allow-Credentials` не выставляется. Клиенты ходят
    с токеном в заголовке, cookie им не нужны, а разрешение на cookie
    сделало бы браузер участником чужой сессии;
  * `*` в origin не поддерживается вообще.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse

# (префикс пути, ключ настройки со списком origin'ов).
# Порядок важен: берётся первое совпадение.
CORS_ZONES: tuple[tuple[str, str], ...] = (
    # Вход Mini App: обмен initData на внутренний токен.
    ("/api/v1/telegram/mini-app/", "MINI_APP_ALLOWED_ORIGINS"),
    # Личный кабинет: то же приложение, те же origin'ы.
    ("/api/v1/me/", "MINI_APP_ALLOWED_ORIGINS"),
    # Экраны показа QR: другой список, потому что это другие устройства.
    ("/api/v1/qr-display/", "QR_DISPLAY_ALLOWED_ORIGINS"),
)

ALLOWED_METHODS = "GET, POST, PATCH, DELETE, OPTIONS"
ALLOWED_HEADERS = "authorization, content-type"
PREFLIGHT_MAX_AGE = "600"


def _origins(setting_key: str) -> list[str]:
    return getattr(settings, "CORS_ORIGINS", {}).get(setting_key, [])


class ScopedCorsMiddleware:
    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request):
        origin = request.headers.get("Origin")
        applicable = origin is not None and self._zone_allows(request.path, origin)

        # Предварительный запрос браузера до самого обращения. Обрабатывается
        # здесь и дальше не идёт: у него нет ни тела, ни авторизации, и
        # доводить его до view незачем.
        if applicable and request.method == "OPTIONS":
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)

        if applicable:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Methods"] = ALLOWED_METHODS
            response["Access-Control-Allow-Headers"] = ALLOWED_HEADERS
            response["Access-Control-Max-Age"] = PREFLIGHT_MAX_AGE
            # Ответ зависит от Origin — без этого кэш отдал бы одному origin'у
            # заголовки, выписанные для другого.
            response["Vary"] = ", ".join(
                filter(None, [response.get("Vary"), "Origin"])
            )
        return response

    def _zone_allows(self, path: str, origin: str) -> bool:
        for prefix, setting_key in CORS_ZONES:
            if path.startswith(prefix):
                return origin.rstrip("/") in _origins(setting_key)
        return False


__all__ = ["CORS_ZONES", "ScopedCorsMiddleware"]
