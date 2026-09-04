"""Кто обращается к endpoint'ам сотрудника: Mini App, бот или сам бот-сервис.

Три способа, и все намеренно узкие.

**Mini App.** Сотрудник — не пользователь CRM: у него нет ни почты, ни пароля,
ни роли. Поэтому `request.user` здесь — не модель `accounts.User`, а лёгкий
`EmployeePrincipal`, у которого нет ни `id`, ни `organization_id` в том виде,
в каком их ждёт `Actor.from_user`. Это сделано специально: класс подключается
ТОЛЬКО к своим view, и даже при ошибке в маршрутах токеном Mini App нельзя
пройти в кадровый API — попытка построить из него `Actor` упадёт, а не
выдаст тихо чужие данные.

**Бот, действующий за сотрудника.** Токена у бота нет, и это не упущение,
а решение. Разбиралось два варианта:

  1. бот получает короткоживущий session token на сотрудника и носит его
     в заголовке;
  2. бот на каждое действие предъявляет свой общий секрет и подтверждённый
     Telegram ID, а сотрудника по нему находит backend.

Выбран второй. Первый требует где-то держать выданные токены: в памяти
процесса они теряются при перезапуске и живут в `MemoryTokenStorage` дольше,
чем нужно, а в базе — это ещё одна таблица секретов, которую надо чистить
и отзывать. Главное же: пока такой токен не истёк, он продолжает работать
после того, как HR отключил привязку. Во втором варианте отзывать нечего —
доступ проверяется заново на каждом запросе, и «отключил» значит «сразу».

Плата за это — обращение к базе на каждое действие бота. Здесь это
приемлемо: то же самое обращение всё равно нужно, чтобы узнать сотрудника.

**Бот как таковой.** Общий секрет без Telegram ID — для служебных операций
вроде погашения ссылки привязки, когда сотрудника ещё нет. Это `IsTelegramBot`.

Ни в одном из трёх случаев `employee_id` и `organization_id` из запроса не
читаются. Их негде передать — значит, нечего подделывать.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from rest_framework import authentication, permissions
from rest_framework.exceptions import AuthenticationFailed

from humotech.telegram.compare import constant_time_equal
from humotech.telegram.identity import (
    AccessDenied,
    EmployeeContext,
    resolve_by_telegram_user_id,
)

BOT_SECRET_HEADER = "X-Bot-Token"
# Telegram ID, подтверждённый ботом. Заголовок имеет вес ТОЛЬКО вместе
# с верным общим секретом: сам по себе он всего лишь число из запроса.
BOT_EMPLOYEE_HEADER = "X-Telegram-User-Id"


@dataclass(frozen=True)
class EmployeePrincipal:
    """Сотрудник, вошедший через Mini App или через бота.

    Умышленно НЕ является `accounts.User` и не притворяется им: у него нет
    ни `pk`, ни `organization_id`, ни `has_perm`. Всё, что он умеет, —
    назвать себя и показать проверенный контекст: сотрудника, привязку,
    действующее назначение и организацию.
    """

    context: EmployeeContext

    @property
    def employee(self):
        return self.context.employee

    @property
    def account(self):
        return self.context.account

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def __str__(self) -> str:
        return f"employee:{self.employee.id}"


class MiniAppAuthentication(authentication.BaseAuthentication):
    """`Authorization: Bearer <токен Mini App>`.

    Подписи мало: состояние привязки перечитывается на каждом запросе,
    поэтому отзыв действует немедленно, а не с истечением срока токена.
    """

    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode("latin-1")
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0].lower() != self.keyword.lower():
            return None

        from humotech.telegram.services import TelegramMiniAppService

        context = TelegramMiniAppService().resolve(parts[1])
        if context is None:
            # Одно сообщение на все причины: истёк срок, отозвана привязка,
            # подделана подпись — клиенту в любом случае надо открыть
            # Mini App заново, а разница ответов помогала бы подбирать токен.
            raise AuthenticationFailed("Сессия недействительна")

        return EmployeePrincipal(context=context), None

    def authenticate_header(self, request) -> str:
        return self.keyword


class BotEmployeeAuthentication(authentication.BaseAuthentication):
    """Бот действует за сотрудника: общий секрет + подтверждённый Telegram ID.

    Порядок проверок здесь важен. Сначала секрет, и только потом всё
    остальное: без верного секрета заголовок с Telegram ID — просто число,
    которое написал отправитель запроса, и обрабатывать его как личность
    значило бы отдать данные любого сотрудника любому, кто знает адрес.
    """

    def authenticate(self, request):
        raw_id = request.headers.get(BOT_EMPLOYEE_HEADER)
        if raw_id is None:
            # Не наша схема — пусть попробуют остальные.
            return None

        expected = settings.TELEGRAM["BOT_API_SECRET"]
        if not expected or not constant_time_equal(
            request.headers.get(BOT_SECRET_HEADER, ""), expected
        ):
            # Не настроен — значит, закрыто. Открытый по умолчанию вход
            # означал бы, что представиться кем угодно может кто угодно.
            raise AuthenticationFailed("Запрос отклонён")

        try:
            telegram_user_id = int(raw_id)
        except (TypeError, ValueError):
            raise AuthenticationFailed("Запрос отклонён") from None

        resolved = resolve_by_telegram_user_id(telegram_user_id)
        if isinstance(resolved, AccessDenied):
            # Боту отдаётся ТОЧНАЯ причина, а не усечённая: он предъявил
            # общий секрет, то есть он наша же сторона. Формулировку для
            # человека выбирает он — «ждём HR» и «привязка отключена»
            # требуют разных действий.
            raise AuthenticationFailed(
                {"detail": "Доступ закрыт", "reason": resolved.reason}
            )

        return EmployeePrincipal(context=resolved), None

    def authenticate_header(self, request) -> str:
        # Без этого DRF отвечает 403 вместо 401 на неудачную аутентификацию.
        return BOT_SECRET_HEADER


class IsLinkedEmployee(permissions.BasePermission):
    """Пускает только владельца рабочей привязки Telegram."""

    def has_permission(self, request, view) -> bool:
        return isinstance(getattr(request, "user", None), EmployeePrincipal)


class IsTelegramBot(permissions.BasePermission):
    """Запрос пришёл от нашего бота, а не от кого-то ещё.

    Сравнение постоянного времени: обычное `==` выходит на первом
    несовпавшем байте, и по времени ответа секрет подбирается побайтово.
    """

    def has_permission(self, request, view) -> bool:
        expected = settings.TELEGRAM["BOT_API_SECRET"]
        if not expected:
            # Не настроен — значит, закрыто. Открытый по умолчанию endpoint
            # привязки означал бы, что привязать можно чей угодно Telegram.
            return False
        return constant_time_equal(
            request.headers.get(BOT_SECRET_HEADER, ""), expected
        )


__all__ = [
    "BOT_EMPLOYEE_HEADER",
    "BOT_SECRET_HEADER",
    "BotEmployeeAuthentication",
    "EmployeePrincipal",
    "IsLinkedEmployee",
    "IsTelegramBot",
    "MiniAppAuthentication",
]
