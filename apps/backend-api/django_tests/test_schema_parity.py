"""Схема, построенная Django-миграциями, обязана совпадать с эталоном Alembic.

Разовая сверка при переносе ничего не гарантирует дальше: следующий
`makemigrations` может тихо изменить тип колонки или потерять ограничение.
Этот тест превращает разовый результат в постоянное условие.

Эталон — снимок схемы на момент перехода на Django, и развитие проекта от него
отходит: это нормально. Ненормально — отойти незаметно. Поэтому сравнение
не ослабляется, а каждое расхождение перечисляется поимённо ниже. Список,
который можно прочитать, — документация; сравнение, которое всё прощает, —
мёртвый тест.

Эталонная база задаётся `ALEMBIC_BASELINE_URL`. Без неё сверка пропускается —
на машине, где старой базы нет, набор не должен падать по этой причине.
Опорные числа ниже работают и без эталона.
"""

from __future__ import annotations

import os

import pytest
from django.db import connection

from scripts.schema_snapshot import diff, dump

pytestmark = pytest.mark.django_db


def _baseline_url() -> str | None:
    return os.getenv("ALEMBIC_BASELINE_URL")


def _django_url() -> str:
    params = connection.get_connection_params()
    return (
        f"postgresql://{params['user']}:{params['password']}"
        f"@{params['host']}:{params['port']}/{params['dbname']}"
    )


# Всё, чем схема отошла от эталона Alembic ПОСЛЕ перехода, — поимённо.
#
# Этап «безопасная привязка Telegram»:
#   * новая таблица одноразовых ссылок привязки;
#   * у привязки появился статус PENDING — переход по ссылке больше не даёт
#     доступа сам по себе, доступ открывает подтверждение HR.
#
# Этап сотруднической части:
#   * экраны показа QR стали объектом со своим доступом: раньше код мог
#     запросить кто угодно, знающий адрес;
#   * у сессии показа появилась ссылка на экран — иначе на вопрос «какой
#     экран показал код, по которому прошла отметка» ответа нет;
#   * таблица уведомлений превратилась в очередь отправки: попытки, время
#     следующей попытки, захват строки отправщиком и ключ повтора.
#
# Ничего, кроме перечисленного, разойтись не имеет права: любая другая
# строка в выводе `diff` роняет тест.
#   * очередь фоновых выгрузок: большой XLSX собирается воркером, а не
#     в запросе, и заданию нужны состояние, попытки и ссылка на файл;
#   * у календарного исключения появилось основание переноса — название
#     видит сотрудник, основание читает кадровик;
#   * исключение стало сниматься с действия, а не только удаляться:
#     «перенос отменили приказом» через полгода объясняет расхождение
#     в табеле, а удалённая строка не объясняет ничего. Вместе с колонкой
#     переписаны оба уникальных ключа — снятая строка не должна занимать
#     дату.
#   * у уведомлений появилась история попыток отдельной таблицей: счётчик
#     и текст последней ошибки не отвечают на вопрос, сообщение не уходит
#     второй день или упало один раз ночью.
KNOWN_DIVERGENCES = {
    "ЛИШНЯЯ ТАБЛИЦА: telegram_link_invitations",
    "ЛИШНЯЯ ТАБЛИЦА: notification_attempts",
    "telegram_accounts: ОГРАНИЧЕНИЕ ОТСУТСТВУЕТ: "
    "check (((status) = any ((array['active', 'revoked', 'blocked'])[])))",
    "telegram_accounts: лишнее ограничение: "
    "check (((status) = any "
    "((array['pending', 'active', 'revoked', 'blocked'])[])))",
    # --- экраны показа QR ---
    "ЛИШНЯЯ ТАБЛИЦА: qr_display_devices",
    "qr_display_sessions.device_id: лишняя колонка",
    "qr_display_sessions: лишнее ограничение: foreign key (device_id) "
    "references qr_display_devices(id) on delete restrict",
    "qr_display_sessions: лишний индекс: "
    "public.qr_display_sessions using btree (device_id)",
    # --- очередь отправки уведомлений ---
    "notifications.attempts: лишняя колонка",
    # Полна ли история попыток. Постоянный признак, а не вывод по данным:
    # счётчик выше обнуляется при ручном повторе, и по нему утраченную
    # историю от только что заведённой строки не отличить.
    "notifications.attempt_history_complete: лишняя колонка",
    "notifications.idempotency_key: лишняя колонка",
    "notifications.locked_at: лишняя колонка",
    "notifications.next_attempt_at: лишняя колонка",
    "notifications: ОГРАНИЧЕНИЕ ОТСУТСТВУЕТ: check (((status) = any "
    "((array['pending', 'sent', 'failed', 'cancelled', 'read'])[])))",
    "notifications: лишнее ограничение: "
    "check ((((status) <> 'running') or (locked_at is not null)))",
    "notifications: лишнее ограничение: check (((status) = any "
    "((array['pending', 'running', 'sent', 'failed', 'cancelled', "
    "'read'])[])))",
    "notifications: лишнее ограничение: check ((attempts >= 0))",
    "notifications: лишнее ограничение: not null attempts",
    "notifications: лишнее ограничение: not null attempt_history_complete",
    "notifications: лишний индекс: public.notifications using btree "
    "(next_attempt_at) where ((status) = 'pending')",
    "notifications: лишний индекс: public.notifications using btree "
    "(organization_id, idempotency_key) where (idempotency_key is not null)",
    # --- фоновые выгрузки и основание переноса ---
    "ЛИШНЯЯ ТАБЛИЦА: export_jobs",
    "calendar_exceptions.reason: лишняя колонка",
    # --- снятие календарного исключения ---
    #
    # Оба уникальных ключа переписаны на условие «действующее». Иначе
    # снятая строка продолжала бы занимать дату, и завести на неё новое
    # исключение стало бы нельзя — то есть снятие было бы бессмысленным.
    "calendar_exceptions.is_active: лишняя колонка",
    "calendar_exceptions: лишнее ограничение: not null is_active",
    "calendar_exceptions: ИНДЕКС ОТСУТСТВУЕТ: public.calendar_exceptions "
    "using btree (office_id, date) where (office_id is not null)",
    "calendar_exceptions: ИНДЕКС ОТСУТСТВУЕТ: public.calendar_exceptions "
    "using btree (organization_id, date) where (office_id is null)",
    "calendar_exceptions: лишний индекс: public.calendar_exceptions "
    "using btree (office_id, date) where (is_active and "
    "(office_id is not null))",
    "calendar_exceptions: лишний индекс: public.calendar_exceptions "
    "using btree (organization_id, date) where (is_active and "
    "(office_id is null))",
}


def test_django_schema_matches_alembic_baseline():
    baseline = _baseline_url()
    if not baseline:
        pytest.skip(
            "Не задан ALEMBIC_BASELINE_URL — сверка схемы с эталоном пропущена"
        )

    problems = diff(dump(baseline), dump(_django_url()))
    unexpected = [item for item in problems if item not in KNOWN_DIVERGENCES]
    assert not unexpected, (
        "Схема разошлась с эталоном сверх объявленного:\n  "
        + "\n  ".join(unexpected)
    )

    # Список расхождений не должен пережить свою причину: если объявленное
    # изменение откатили, строку надо убрать отсюда, а не оставлять
    # молчаливое разрешение на будущее.
    stale = KNOWN_DIVERGENCES - set(problems)
    assert not stale, (
        "Эти расхождения объявлены, но их больше нет — удалите их из списка:"
        "\n  " + "\n  ".join(sorted(stale))
    )


def test_business_schema_has_expected_shape():
    """Опорные числа схемы. Работает и без эталонной базы.

    Не заменяет полную сверку, но ловит грубую потерю: исчезнувшую таблицу,
    снятое ограничение, пропавший индекс.
    """
    snapshot = dump(_django_url())
    # 44 таблицы перенесены с Alembic, + telegram_link_invitations,
    # + qr_display_devices, + export_jobs, + notification_attempts.
    assert len(snapshot["tables"]) == 48, (
        f"бизнес-таблиц {len(snapshot['tables'])}, ожидалось 48"
    )

    counts = {"c": 0, "f": 0, "u": 0, "x": 0}
    for table in snapshot["tables"]:
        for item in snapshot["constraints"].get(table, {}).values():
            if item["kind"] in counts:
                counts[item["kind"]] += 1

    # Прибавка этапа привязки Telegram к числам перехода:
    #   CHECK  92 + 1  — статусы приглашения;
    #   FK    127 + 4  — организация, сотрудник, автор, принявший решение;
    #   UNIQUE 20 + 1  — хеш токена. Второй уникальный ключ приглашений
    #                    частичный, а частичный Django строит ИНДЕКСОМ,
    #                    и в pg_constraint он не попадает.
    #
    # Прибавка сотруднической части:
    #   CHECK  93 + 6  — четыре у экранов показа (статус и три согласования
    #                    состояния с секретами) и два в очереди отправки;
    #   FK    131 + 4  — организация, точка и автор у экрана, плюс ссылка
    #                    сессии показа на экран;
    #   UNIQUE 21 + 0  — оба новых ключа частичные, то есть индексы.
    #
    # Прибавка очереди выгрузок:
    #   CHECK  99 + 1  — статус задания;
    #   FK    135 + 2  — организация и автор выгрузки;
    #   UNIQUE 21 + 0  — уникальных ключей у очереди нет: одну и ту же
    #                    выгрузку можно заказать дважды, это не ошибка.
    # Прибавка истории попыток:
    #   CHECK 100 + 3 — исход, положительный номер и «у успеха нет причины»;
    #   FK    137 + 1 — ссылка на уведомление;
    #   UNIQUE  21 + 0 — двух одинаковых попыток не бывает по построению:
    #                    номер выдаётся при записи.
    assert counts["c"] == 103, f"CHECK: {counts['c']}, ожидалось 103"
    assert counts["f"] == 138, f"FOREIGN KEY: {counts['f']}, ожидалось 138"
    assert counts["u"] == 21, f"UNIQUE: {counts['u']}, ожидалось 21"
    assert counts["x"] == 2, f"EXCLUDE: {counts['x']}, ожидалось 2"
    assert {"btree_gist", "vector"} <= set(snapshot["extensions"])


def test_every_foreign_key_keeps_its_on_delete_action():
    """Django не выводит `ON DELETE` сам — его восстанавливает `core/0002`.

    Без этого прямой `DELETE` мимо ORM снёс бы историю: запрет физического
    удаления объектов со связями держится на действии в самой базе,
    а не на коллекторе Django.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.relname, c.conname, c.confdeltype, c.condeferrable
              FROM pg_constraint c
              JOIN pg_class t ON t.oid = c.conrelid
              JOIN pg_namespace n ON n.oid = t.relnamespace
             WHERE n.nspname = 'public' AND c.contype = 'f'
               AND t.relname NOT LIKE 'django_%' AND t.relname NOT LIKE 'auth_%'
            """
        )
        rows = cursor.fetchall()

    assert len(rows) == 138, f"внешних ключей {len(rows)}, ожидалось 138"

    # 'a' = NO ACTION: значит, действие не задано
    without_action = [f"{t}.{n}" for t, n, kind, _ in rows if kind == "a"]
    assert not without_action, (
        f"внешние ключи без ON DELETE: {without_action[:5]}"
    )
    deferrable = [f"{t}.{n}" for t, n, _, deferred in rows if deferred]
    assert not deferrable, (
        f"отложенные внешние ключи (в эталоне их нет): {deferrable[:5]}"
    )

    actions = {"r": 0, "n": 0, "c": 0}
    for _, _, kind, _ in rows:
        actions[kind] = actions.get(kind, 0) + 1
    # +3 RESTRICT и +1 SET NULL — ключи telegram_link_invitations.
    # Ещё +3 RESTRICT и +1 SET NULL — экраны показа QR. SET NULL в обоих
    # случаях один и тот же по смыслу: учётную запись сотрудника HR можно
    # заблокировать, но запись о том, что он что-то создал, обязана остаться.
    # 113 + 2 — организация и автор у задания на выгрузку.
    # 115 + 1 — попытка отправки ссылается на уведомление: удалить строку,
    # за которой стоят состоявшиеся отправки, нельзя.
    assert actions["r"] == 116, f"RESTRICT: {actions['r']}, ожидалось 116"
    assert actions["n"] == 16, f"SET NULL: {actions['n']}, ожидалось 16"
    assert actions["c"] == 6, f"CASCADE: {actions['c']}, ожидалось 6"
