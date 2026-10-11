"""Ответы API не оседают в кэше.

Почти всё, что отдаёт `/api/`, — персональные данные: карточки, отметки,
заявки, выгрузки CSV/XLSX, печатные заявления. Часть путей собирает ответ
сама (`FileResponse`, `HttpResponse` для выгрузок и PDF) и мимо общей
выдачи файлов (`files.serving.file_response`) оставалась без запрета
кэширования: файл с ПДн мог осесть в кэше браузера, прокси или WebView
Telegram на общем устройстве (аудит 11.10, M-10).

Поэтому правило стоит здесь, одно на весь API, а не в каждом view: ответ,
которому view не задал `Cache-Control` сам, получает `private, no-store`
и `nosniff`. Свой заголовок view может поставить и дальше (SSE, файлы) —
его не перетираем.
"""

from __future__ import annotations

from django.utils.deprecation import MiddlewareMixin

API_PREFIX = "/api/"


class PrivateApiResponseMiddleware(MiddlewareMixin):
    def process_response(self, request, response):
        if request.path.startswith(API_PREFIX):
            if not response.has_header("Cache-Control"):
                response["Cache-Control"] = "private, no-store"
            if not response.has_header("X-Content-Type-Options"):
                response["X-Content-Type-Options"] = "nosniff"
        return response
