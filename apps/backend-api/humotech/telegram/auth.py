"""Кто обращается к endpoint'ам Telegram: сотрудник из Mini App или сам бот.

Два способа, и оба намеренно узкие.

**Mini App.** Сотрудник — не пользователь CRM: у него нет ни почты, ни пароля,
ни роли. Поэтому `request.user` здесь — не модель `accounts.User`, а лёгкий
`EmployeePrincipal`, у которого нет ни `id`, ни `organization_id` в том виде,
в каком их ждёт `Actor.from_user`. Это сделано специально: класс подключается
ТОЛЬКО к своим view, и даже при ошибке в маршрутах токеном Mini App нельзя
пройти в кадровый API — попытка построить из него `Actor` упадёт, а не
выдаст тихо чужие данные.

Вторая система прав отсюда не растёт. `EmployeePrincipal` ничего не решает:
он отвечает на вопрос «кто это», а «что можно» на этом этапе не спрашивают
вовсе — Mini App отдаёт человеку только его собственные данные.

**Бот.** Общий секрет в заголовке. Он нужен не вместо токена приглашения,
а рядом с ним: токен доказывает, что человек получил ссылку, а секрет — что
`telegram_user_id` пришёл от Telegram через нашего бота, а не выдуман
отправителем запроса. Без секрета кто угодно привязал бы к найденной ссылке
чужой аккаунт.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from rest_framework import authentication, permissions
from rest_framework.exceptions import AuthenticationFailed

from humotech.telegram.compare import constant_time_equal

BOT_SECRET_HEADER = "X-Bot-Token"


@dataclass(frozen=True)
class EmployeePrincipal:
    """Сотрудник, вошедший через Mini App.

    Умышленно НЕ является `accounts.User` и не притворяется им: у него нет
    ни `pk`, ни `organization_id`, ни `has_perm`. Всё, что он умеет, —
    назвать себя.
    """

    employee: object
    account: object

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

        resolved = TelegramMiniAppService().resolve(parts[1])
        if resolved is None:
            # Одно сообщение на все причины: истёк срок, отозвана привязка,
            # подделана подпись — клиенту в любом случае надо открыть
            # Mini App заново, а разница ответов помогала бы подбирать токен.
            raise AuthenticationFailed("Сессия недействительна")

        account, employee = resolved
        return EmployeePrincipal(employee=employee, account=account), None

    def authenticate_header(self, request) -> str:
        return self.keyword


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
