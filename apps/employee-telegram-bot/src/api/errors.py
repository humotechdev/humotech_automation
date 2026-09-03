"""Ошибки backend-api в терминах контракта packages/api-contracts/telegram-bot.v1.md."""


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        super().__init__(f"{status} {code}: {message}")


class BadRequest(ApiError):
    """400 validation_error"""


class Unauthorized(ApiError):
    """401 — токен протух или отозван. Бот стирает токен и просит /start."""


class Forbidden(ApiError):
    """403 — роль не позволяет. Бот обновляет меню: роль могла измениться."""


class NotFound(ApiError):
    """404"""


class Conflict(ApiError):
    """409 — например, заявку уже обработал другой HR."""


class RateLimited(ApiError):
    """429"""


class ServerError(ApiError):
    """5xx"""


STATUS_MAP = {
    400: BadRequest,
    401: Unauthorized,
    403: Forbidden,
    404: NotFound,
    409: Conflict,
    429: RateLimited,
}


def error_for(status: int, code: str, message: str, details=None) -> ApiError:
    if status >= 500:
        return ServerError(status, code, message, details)
    return STATUS_MAP.get(status, ApiError)(status, code, message, details)
