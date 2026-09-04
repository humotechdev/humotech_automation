"""CORS — только для Mini App и только для его путей.

Mini App открывается Telegram по своему адресу, а к API обращается по нашему:
для браузера это разные origin'ы, и без явного разрешения запрос не уйдёт
вовсе. Поэтому CORS здесь нужен, но нужен ровно в одном месте.

Отдельная библиотека не ставится намеренно. `django-cors-headers` включает
общие правила на весь проект, а согласовывать их придётся с CRM, ботом и
админкой; здесь же требуется разрешить один префикс пути для перечисленного
списка origin'ов — это тридцать строк с точными границами.

Границы:

  * префикс пути фиксирован. Другие endpoint'ы браузеру из чужого origin'а
    недоступны, даже если origin в списке;
  * список origin'ов задаётся окружением. Пустой список = CORS выключен,
    а не «разрешено всем» — умолчание обязано быть закрытым;
  * `Access-Control-Allow-Credentials` не выставляется. Mini App ходит с
    внутренним токеном в заголовке, cookie ему не нужны, а разрешение на
    cookie сделало бы браузер участником чужой сессии;
  * `*` в origin не поддерживается вообще.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse

# CORS действует только на этих путях.
MINI_APP_PATH_PREFIX = "/api/v1/telegram/mini-app/"

ALLOWED_METHODS = "GET, POST, OPTIONS"
ALLOWED_HEADERS = "authorization, content-type"
PREFLIGHT_MAX_AGE = "600"


class MiniAppCorsMiddleware:
    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def _allowed_origins(self) -> list[str]:
        return getattr(settings, "TELEGRAM", {}).get("MINI_APP_ALLOWED_ORIGINS", [])

    def __call__(self, request):
        origin = request.headers.get("Origin")
        applicable = (
            origin is not None
            and request.path.startswith(MINI_APP_PATH_PREFIX)
            and origin.rstrip("/") in self._allowed_origins()
        )

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
