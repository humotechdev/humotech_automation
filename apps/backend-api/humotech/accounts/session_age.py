"""Абсолютный срок сессии CRM.

Срок cookie скользящий (`SESSION_COOKIE_AGE` + `SESSION_SAVE_EVERY_REQUEST`):
каждый запрос продлевает сессию. Это удобно в рабочий день, но украденная
cookie жила бы, пока ею пользуются, — сколько угодно (аудит 11.10, L-11).
Поэтому у сессии есть и потолок от момента ВХОДА: после
`SESSION_ABSOLUTE_AGE` секунд нужен новый вход, как бы активно ни работали.

Момент входа кладётся в сессию при входе (`AUTH_AT_KEY`). Сессиям, открытым
до появления правила, он ставится при первом запросе — их не обрывает
внезапно, а отсчитывает с этого момента.
"""

from __future__ import annotations

import time

from django.conf import settings
from django.contrib.auth import logout
from django.utils.deprecation import MiddlewareMixin

AUTH_AT_KEY = "humotech_auth_at"


def mark_login(request) -> None:
    request.session[AUTH_AT_KEY] = int(time.time())


class AbsoluteSessionAgeMiddleware(MiddlewareMixin):
    def process_request(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        now = int(time.time())
        started = request.session.get(AUTH_AT_KEY)
        if started is None:
            request.session[AUTH_AT_KEY] = now
            return None
        if now - int(started) > settings.SESSION_ABSOLUTE_AGE:
            # Сессия удаляется на сервере; запрос идёт дальше анонимным и
            # получает обычный отказ «нужен вход».
            logout(request)
        return None
