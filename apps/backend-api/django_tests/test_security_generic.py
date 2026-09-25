"""Сквозная проверка ввода по всему API.

Фаззер обходит URLConf целиком и шлёт в каждый маршрут кривые данные:
строки вместо чисел, дат и UUID, огромные строки, NUL-байт, необычный
Unicode, повторяющиеся параметры, JSON глубиной в тысячу уровней, массив
вместо объекта. Ни один такой запрос не имеет права закончиться ответом 500:
500 — это либо трейсбек в логах на каждый чужой запрос, либо (при DEBUG)
трейсбек в ответе.

Два режима:

  * обычный (регрессионный) — ограниченный набор полезных нагрузок, все
    параметры получают одно и то же значение сразу. Быстро;
  * исследовательский (`SEC_FUZZ_FULL=1`) — каждый параметр по отдельности
    с полным набором нагрузок. Долго, запускается руками; итог печатается.

Рядом — точечные проверки общих частей: обработчики 404/500, курсоры
постраничного вывода, границы суток, лимит тела и глубины JSON.
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from collections import defaultdict
from datetime import date
from urllib.parse import quote

import pytest
from django.db import transaction
from django.test import Client
from django.urls import get_resolver
from rest_framework.test import APIClient

from humotech.core.permissions_catalog import ALL_PERMISSION_CODES

from .conftest import RealNetworkCallBlocked, bot_headers, create_actor, link_telegram

pytestmark = pytest.mark.django_db

FULL = os.environ.get("SEC_FUZZ_FULL") == "1"
SLOW_SECONDS = 3.0

# --- обход URLConf ----------------------------------------------------------

# Маршруты, которые обходятся стороной намеренно.
SKIP_PREFIXES = (
    "/admin/",          # Django admin — свой HTML-интерфейс, не API
    "/api/schema",      # генерация схемы: долго и без пользовательского ввода
    "/api/docs",
    "/api/redoc",
    "/api/v1/auth/logout",  # выкинет сессию HR посреди прохода
)

# Имена query-параметров, которые реально читаются кодом (собраны grep'ом
# по `query_params.get(...)`), плюс общие для списков.
QUERY_PARAMS = (
    "search", "limit", "status", "cursor", "kind", "offset", "language",
    "scope", "mine_only", "group", "date_to", "date_from", "year", "month",
    "weekday", "verification_status", "type", "state", "source", "series",
    "role_id", "request_kind", "region_id", "received", "quick",
    "qr_point_id", "q", "priority", "period", "people_limit", "office_id",
    "notification_type", "is_active", "include_inactive", "history", "fmt",
    "exception_type", "event_type", "entity_type", "employee_id",
    "department_id", "channel", "category", "at", "assignee", "all_versions",
    "action", "from", "to", "date", "day", "page", "page_size", "ordering",
    "sort", "order", "id", "week", "compare", "user_id", "position_id",
)

HUGE = "я" * 100_000

QUERY_PAYLOADS_FULL = (
    "abc", "-1", "99999999999999999999999999", "a\x00b",
    "‮\U0001F600́", "9999-12-31", "0001-01-01", "1e309", "я" * 5000,
)
# Регрессионный набор: по одному представителю каждого класса поломки.
QUERY_PAYLOADS_FAST = ("abc", "-1", "\x00", "9999-12-31", "1e309", "a" * 2_000)

DEEP_LIST = "[" * 1000 + "]" * 1000
DEEP_OBJECT = '{"a":' * 1000 + "1" + "}" * 1000

BODY_FIELDS = (
    "name", "title", "comment", "reason", "text", "message", "email",
    "password", "code", "description", "date_from", "date_to", "start_date",
    "end_date", "office_id", "employee_id", "status", "question", "answer",
    "body", "value", "timezone", "day", "date", "type", "kind",
)


def _body(value) -> str:
    return json.dumps({field: value for field in BODY_FIELDS})


BODY_PAYLOADS_FULL = (
    DEEP_LIST, DEEP_OBJECT, "[]", '[{"a":1}]', '"string"', "null", "1",
    "{not json", _body("\x00"), _body(HUGE), _body("9999-12-31"),
    _body(["x"]), _body({"a": {"b": 1}}), _body(-1),
)
BODY_PAYLOADS_FAST = (DEEP_LIST, DEEP_OBJECT, "[]", _body("\x00"), _body("9999-12-31"))

# Значения для параметров пути. `pk` у роутера DRF — `[^/.]+`, то есть
# туда пролезает что угодно, не только UUID.
PATH_VALUES_FULL = (None, "abc", "%00", quote("я" * 300))
PATH_VALUES_FAST = (None, "abc")


def _routes():
    """(шаблон пути, callback) для всех маршрутов проекта."""
    from django.contrib.admindocs.views import (
        extract_views_from_urlpatterns,
        simplify_regex,
    )

    seen = set()
    for callback, regex, _namespace, _name in extract_views_from_urlpatterns(
        get_resolver().url_patterns
    ):
        if "format" in regex:  # суффиксы `.json` у роутера — те же view
            continue
        template = simplify_regex(regex)
        if template in seen or template.startswith(SKIP_PREFIXES):
            continue
        seen.add(template)
        yield template, callback


def _methods(callback) -> list[str]:
    actions = getattr(callback, "actions", None)
    if actions:
        return sorted(m for m in actions if m in {"get", "post", "put", "patch"})
    cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    if cls is None:
        return ["get"]
    return [m for m in ("get", "post", "put", "patch") if hasattr(cls, m)]


def _fill(template: str, bad: str | None) -> str:
    """Подставляет значения в `<param>` шаблона."""
    import re

    def value(match):
        name = match.group(1)
        if bad is not None:
            return bad
        if name in {"kind"}:
            return "attendance"
        if name in {"decision"}:
            return "approve"
        if name in {"paper", "fmt"}:
            return "application"
        return str(uuid.uuid4())

    return re.sub(r"<(\w+)>", value, template)


# --- клиенты -----------------------------------------------------------------


@pytest.fixture()
def no_throttle(monkeypatch):
    from rest_framework.throttling import SimpleRateThrottle

    monkeypatch.setattr(SimpleRateThrottle, "allow_request", lambda *a, **k: True)


@pytest.fixture()
def clients(organization, employee, telegram_settings, no_throttle, settings):
    settings.AI_ASSISTANT_ENABLED = False
    user, _ = create_actor(organization, permissions=ALL_PERMISSION_CODES)
    hr = APIClient()
    # Только force_authenticate, без сессии: иначе после DataError внутри
    # точки сохранения (её ставит сам проход) middleware сессий полезет в
    # уже прерванную транзакцию. В бою такой транзакции нет —
    # ATOMIC_REQUESTS выключен, — это особенность только этого теста.
    hr.force_authenticate(user=user)

    link_telegram(employee)
    staff = APIClient()
    staff.credentials(**bot_headers())

    return {"hr": hr, "employee": staff, "anon": APIClient()}


class Findings:
    def __init__(self) -> None:
        self.errors: list[tuple] = []
        self.slow: list[tuple] = []
        self.network: list[tuple] = []
        self.count = 0

    def call(self, who, client, method, path, *, query=None, body=None):
        self.count += 1
        started = time.monotonic()
        status = None
        try:
            with transaction.atomic():
                kwargs = {}
                if body is not None:
                    kwargs = {"data": body, "content_type": "application/json"}
                if query is not None:
                    path_q = f"{path}?{query}"
                else:
                    path_q = path
                response = getattr(client, method)(path_q, **kwargs)
                status = response.status_code
                transaction.set_rollback(True)
        except RealNetworkCallBlocked as exc:
            self.network.append((who, method, path, query, str(exc)[:80]))
            return
        except Exception as exc:  # noqa: BLE001 — собираем всё
            self.errors.append(
                (who, method, path, (query or "")[:80], (body or "")[:60],
                 f"{type(exc).__module__}.{type(exc).__name__}: {str(exc)[:160]}")
            )
            return
        finally:
            elapsed = time.monotonic() - started
            if elapsed > SLOW_SECONDS:
                self.slow.append((who, method, path, (query or "")[:60], round(elapsed, 1)))
        # 503 — законный ответ «модуль выключен» (AI_ASSISTANT_ENABLED=false).
        if status is not None and status >= 500 and status != 503:
            self.errors.append((who, method, path, (query or "")[:80], (body or "")[:60],
                                f"HTTP {status}"))

    def report(self) -> str:
        grouped = defaultdict(list)
        for row in self.errors:
            grouped[(row[2], row[5].split(":")[0])].append(row)
        lines = [f"запросов: {self.count}, ошибок: {len(self.errors)}, "
                 f"медленных: {len(self.slow)}, сеть: {len(self.network)}"]
        for (path, kind), rows in sorted(grouped.items()):
            first = rows[0]
            lines.append(f"{path} [{kind}] x{len(rows)} e.g. who={first[0]} "
                         f"{first[1]} q={first[3]!r} body={first[4]!r} -> {first[5]}")
        for row in self.slow:
            lines.append(f"SLOW {row}")
        return "\n".join(lines)


def _client_fits(who: str, template: str) -> bool:
    """В подробном режиме каждый клиент ходит только туда, где он свой.

    Чужой клиент получит 401/403 до разбора параметров — такой проход
    ничего не проверяет, а время съедает.
    """
    own = template.startswith(("/api/v1/me/", "/api/v1/telegram/bot/"))
    if who == "employee":
        return own
    if who == "hr":
        return not own
    return template.startswith(("/api/v1/auth/", "/api/v1/telegram/", "/api/v1/qr"))


def _run_fuzz(clients, *, query_payloads, body_payloads, path_values, per_param):
    found = Findings()
    for template, callback in _routes():
        methods = _methods(callback)
        for bad_path in path_values:
            if bad_path is not None and "<" not in template:
                continue
            path = _fill(template, bad_path)
            for who, client in clients.items():
                if per_param and not _client_fits(who, template):
                    continue
                if "get" in methods:
                    found.call(who, client, "get", path)
                    for payload in query_payloads:
                        encoded = quote(payload, safe="")
                        if per_param and bad_path is None:
                            for name in QUERY_PARAMS:
                                found.call(who, client, "get", path,
                                           query=f"{name}={encoded}")
                        else:
                            query = "&".join(f"{n}={encoded}" for n in QUERY_PARAMS)
                            found.call(who, client, "get", path, query=query)
                    # повторяющиеся параметры
                    found.call(who, client, "get", path,
                               query="limit=1&limit=99999&cursor=a&cursor=b"
                                     "&date_from=2026-01-01&date_from=x")
                for method in methods:
                    if method == "get":
                        continue
                    for body in body_payloads:
                        found.call(who, client, method, path, body=body)
    return found


def test_fuzz_all_routes_never_answer_500(clients):
    if FULL:
        found = _run_fuzz(
            clients,
            query_payloads=QUERY_PAYLOADS_FULL,
            body_payloads=BODY_PAYLOADS_FULL,
            path_values=PATH_VALUES_FULL,
            per_param=True,
        )
        print("\n" + found.report())
    else:
        found = _run_fuzz(
            clients,
            query_payloads=QUERY_PAYLOADS_FAST,
            body_payloads=BODY_PAYLOADS_FAST,
            path_values=PATH_VALUES_FAST,
            per_param=False,
        )
    assert found.count > 500, "обход маршрутов ничего не нашёл"
    found.errors = [row for row in found.errors if not _known_open(row)]
    assert not found.errors, found.report()


# Известные и переданные владельцам места: (путь, тип исключения).
# Чинятся в своих модулях; после починки строку отсюда нужно убрать —
# тогда тест начнёт стеречь и их.
KNOWN_OPEN: set[tuple[str, str]] = set()


def _known_open(row) -> bool:
    return (row[2], row[5].split(":")[0]) in KNOWN_OPEN


# --- обработчики ошибок вне DRF -------------------------------------------


def _envelope(response) -> dict:
    assert response["Content-Type"].startswith("application/json"), response.content[:200]
    body = response.json()
    assert set(body) == {"error"}
    assert {"code", "message", "details"} <= set(body["error"])
    return body["error"]


def test_unknown_path_answers_json_404_not_html():
    response = Client().get("/api/v1/definitely-not-here")
    assert response.status_code == 404
    assert _envelope(response)["code"] == "not_found"


def test_unhandled_exception_answers_json_500_without_details(
    monkeypatch, make_user, organization
):
    """Необработанное исключение: 500 JSON без текста исключения и трейсбека."""
    from humotech.accounts.views import CurrentUserView

    marker = "postgresql://secret-user:secret-pass@db/internal"

    def explode(self, request, *args, **kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(CurrentUserView, "get", explode)
    client = Client(raise_request_exception=False)
    client.force_login(make_user(organization))
    response = client.get("/api/v1/auth/me")

    assert response.status_code == 500
    assert _envelope(response)["code"] == "server_error"
    text = response.content.decode()
    assert marker not in text
    assert "Traceback" not in text and "RuntimeError" not in text


def test_suspicious_request_answers_json_400(settings):
    """SuspiciousOperation (тут — чужой Host) уходит в handler400."""
    response = Client().get("/api/v1/auth/me", HTTP_HOST="evil.example")
    assert response.status_code == 400
    assert _envelope(response)["code"] == "bad_request"


# --- тело запроса: глубина и размер -----------------------------------------


def test_deeply_nested_json_is_400_not_recursion_500():
    for body in (DEEP_LIST, DEEP_OBJECT):
        response = APIClient().post(
            "/api/v1/auth/login", data=body, content_type="application/json"
        )
        assert response.status_code == 400, response.content[:200]
        assert _envelope(response)["code"] == "parse_error"


def test_request_body_size_is_limited():
    from django.conf import settings

    # Лимит Django действует и на JSON DRF: DRF ≥ 3.15 читает тело через
    # `request.body`. Выключать его (None) нельзя — шлюз пропускает 25 МБ.
    assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE is not None
    assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE <= 10 * 1024 * 1024

    big = json.dumps({"email": "a@b.tj", "password": "x" * (settings.DATA_UPLOAD_MAX_MEMORY_SIZE + 10)})
    response = APIClient().post(
        "/api/v1/auth/login", data=big, content_type="application/json"
    )
    assert response.status_code in (400, 413), response.status_code
    assert _envelope(response)["code"] in ("payload_too_large", "bad_request")


# --- перевод исключений ввода в 400 ------------------------------------------


@pytest.fixture()
def hr_client(make_user, organization, no_throttle):
    client = APIClient()
    client.force_authenticate(user=make_user(organization, permissions=ALL_PERMISSION_CODES))
    return client


@pytest.mark.parametrize("path", [
    "/api/v1/employees/abc/",
    "/api/v1/offices/abc/",
    "/api/v1/employees/?office_id=abc",
    "/api/v1/employees/?search=a%00b",
    "/api/v1/regions/?status=%00",
    "/api/v1/regions/abc/",
    "/api/v1/employees/?offset=99999999999999999999999999",
    "/api/v1/employees/?search=" + "a" * 5000,
    "/api/v1/employees/?limit=1&limit=99999",
    "/api/v1/employees/?limit=-1",
    "/api/v1/employees/?limit=1.5",
    "/api/v1/employees/?limit=" + "9" * 5000,
    "/api/v1/employees/?cursor=" + "A" * 5000,
    "/api/v1/employees/?cursor=" + base64.urlsafe_b64encode(DEEP_LIST.encode()).decode(),
])
def test_bad_input_is_client_error_not_500(hr_client, path):
    response = hr_client.get(path)
    assert 400 <= response.status_code < 500, (response.status_code, response.content[:300])
    _envelope(response)


@pytest.mark.parametrize("exc_factory, status, code", [
    (lambda: __import__("django.core.exceptions", fromlist=["x"]).ValidationError(
        "“abc” не UUID"), 400, "validation_error"),
    (lambda: __import__("django.db", fromlist=["x"]).DataError(
        "PostgreSQL text fields cannot contain NUL (0x00) bytes"), 400, "validation_error"),
    (lambda: RecursionError("maximum recursion depth exceeded"), 400, "parse_error"),
    (lambda: OverflowError("date value out of range"), 400, "validation_error"),
])
def test_exception_handler_maps_input_errors(exc_factory, status, code):
    from humotech.core.exceptions import domain_exception_handler

    response = domain_exception_handler(exc_factory(), {})
    assert response.status_code == status
    assert response.data["error"]["code"] == code
    # Текст драйвера наружу не уходит.
    assert "PostgreSQL" not in json.dumps(response.data, ensure_ascii=False)


def test_exception_handler_leaves_code_bugs_as_500():
    """ValueError/TypeError — не ввод, а ошибка в коде: их не маскируем."""
    from humotech.core.exceptions import domain_exception_handler

    assert domain_exception_handler(ValueError("bug"), {}) is None
    assert domain_exception_handler(KeyError("bug"), {}) is None


# --- курсоры постраничного вывода -----------------------------------------


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


@pytest.mark.parametrize("raw", [
    "not-base64!!",
    _b64("[]"),
    _b64('"string"'),
    _b64(DEEP_LIST),
    _b64('{"created_at": "2026-01-01T00:00:00", "id": "%s"}' % uuid.uuid4()),  # без пояса
    _b64('{"created_at": "2026-01-01T00:00:00+00:00", "id": 5}'),
    _b64('{"created_at": 5, "id": "%s"}' % uuid.uuid4()),
    "A" * 100_000,
])
def test_broken_cursor_is_validation_error(raw):
    from humotech.core.errors import ValidationFailed
    from humotech.core.pagination import Cursor, FieldCursor

    for cls in (Cursor, FieldCursor):
        with pytest.raises(ValidationFailed) as info:
            cls.decode(raw)
        assert len(json.dumps(info.value.details)) < 300  # без эха мегабайта


def test_field_cursor_with_foreign_value_is_validation_error(organization):
    from humotech.core.errors import ValidationFailed
    from humotech.core.pagination import FieldCursor, paginate_on
    from humotech.schedules.models import CalendarException

    for value in ("abc", "9999-99-99", "2026-01-01\x00"):
        cursor = _b64(json.dumps({"v": value, "id": str(uuid.uuid4())}))
        with pytest.raises(ValidationFailed):
            paginate_on(CalendarException.objects.all(), "date", cursor=cursor)
    good = FieldCursor(value="2026-01-01", id=uuid.uuid4()).encode()
    assert paginate_on(CalendarException.objects.all(), "date", cursor=good).items == []


def test_limit_is_clamped_not_unbounded():
    from humotech.core.errors import ValidationFailed
    from humotech.core.pagination import MAX_PAGE_SIZE, normalize_limit

    assert normalize_limit(10**12) == MAX_PAGE_SIZE
    with pytest.raises(ValidationFailed):
        normalize_limit(-1)
    with pytest.raises(ValidationFailed):
        normalize_limit(0)


# --- границы календаря -----------------------------------------------------


def test_calendar_edges_do_not_overflow():
    from humotech.core.errors import ValidationFailed
    from humotech.core.timeframes import (
        day_bounds, days_in, month_range, range_bounds, week_range, zone,
    )

    east = zone("Asia/Dushanbe")
    with pytest.raises(ValidationFailed):
        day_bounds(date.max, east)
    with pytest.raises(ValidationFailed):
        day_bounds(date.min, east)
    with pytest.raises(ValidationFailed):
        range_bounds(date(2026, 1, 1), date.max, east)
    assert list(days_in(date(9999, 12, 30), date.max)) == [date(9999, 12, 30), date.max]
    assert month_range(date(9999, 12, 5)) == (date(9999, 12, 1), date.max)
    assert week_range(date.max)[1] == date.max
    assert week_range(date(2026, 9, 25)) == (date(2026, 9, 21), date(2026, 9, 27))


def test_query_helpers_answer_400():
    from django.http import QueryDict

    from humotech.core.api import query_date, query_int, query_text, query_uuid
    from humotech.core.errors import ValidationFailed

    query = QueryDict(
        "n=abc&big=" + "9" * 50 + "&d=9999-12-31&u=x&s=a%00b&ok=5&n2=1&n2=500"
    )
    for call in (
        lambda: query_int(query, "n"),
        lambda: query_int(query, "big"),
        lambda: query_int(query, "n2", maximum=200),  # берётся последнее
        lambda: query_date(query, "d"),
        lambda: query_uuid(query, "u"),
        lambda: query_text(query, "s"),
    ):
        with pytest.raises(ValidationFailed):
            call()
    assert query_int(query, "ok", minimum=1) == 5
    assert query_int(query, "missing", default=7) == 7
