"""Гонки: два одновременных запроса не должны пробить инварианты.

Отдельный файл, потому что отдельное окружение. Обычный тест Django
идёт в одной транзакции, и «параллельные» запросы в нём делят и
соединение, и транзакцию: блокировка не мешает сама себе, а вторая
вставка видит первую до всякого коммита. Проверка гонки в таком тесте
зелёная всегда и не значит ничего.

Здесь `transaction=True`: база настоящая, потоки настоящие, у каждого
своё соединение. Именно так выглядят два касания кнопки на плохой
связи, повтор Telegram и две вкладки CRM.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connections

from humotech.absences.models import AbsenceRequest, EmployeeAbsence
from humotech.absences.services import AbsenceService
from humotech.core.errors import Conflict

from .test_absences import (  # noqa: F401 — фикстуры
    annual_leave,
    balance,
    context,
    private_files,
    sick_leave,
    soon,
)

pytestmark = pytest.mark.django_db(transaction=True)


def in_parallel(work, times: int = 2) -> list[str]:
    """Запустить `work` в нескольких потоках и собрать исходы.

    Соединение закрывается на входе и на выходе: у потока его быть не
    должно ни до работы, ни после. Без первого закрытия Django отдаёт
    потоку соединение главного потока, и оба снова оказываются в одной
    транзакции — то есть гонки опять нет.
    """
    def once(_):
        connections.close_all()
        try:
            return work()
        except Conflict as error:
            # Причина, а не текст: по ней видно, какой именно барьер
            # сработал — проверка сервиса или ограничение базы.
            return str(error.details.get("reason") or f"conflict:{error}")
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=times) as pool:
        return sorted(pool.map(once, range(times)))


def test_two_parallel_requests_create_only_one_sick_leave(
    context, sick_leave
):
    """Незакрытый больничный остаётся один.

    Проверка «прочитал — не нашёл — создал» без блокировки проходит у
    обоих потоков: каждый читает до того, как второй записал.
    """
    def create():
        AbsenceService().create(context, absence_type_code="SICK_LEAVE")
        return "ok"

    results = in_parallel(create)

    assert results.count("ok") == 1, results
    assert "sick_leave_in_progress" in results
    assert AbsenceRequest.objects.filter(employee=context.employee).count() == 1


def test_two_parallel_vacations_do_not_overlap(
    context, annual_leave, balance
):
    """Два одинаковых отпуска не проходят оба.

    Тот же самый разрыв между чтением и записью, но последствие другое:
    пересечение подтверждённых периодов, которое потом видно в табеле
    как два отсутствия на одни сутки.
    """
    def create():
        AbsenceService().create(
            context, absence_type_code="ANNUAL_LEAVE",
            first_day=soon(20), last_day=soon(24),
        )
        return "ok"

    results = in_parallel(create)

    assert results.count("ok") == 1, results
    assert "overlap" in results
    assert AbsenceRequest.objects.filter(employee=context.employee).count() == 1


def test_a_repeated_request_does_not_duplicate_the_absence(
    context, annual_leave, balance
):
    """Повтор из-за плохой связи не создаёт второй отпуск.

    Отдельного ключа идемпотентности здесь нет и не нужно: повтор — это
    та же заявка на те же даты, а второй такой не даёт появиться общее
    правило пересечения. Механизм, дублирующий уже работающее правило,
    добавил бы второе место, где это можно сломать.
    """
    service = AbsenceService()
    service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(30), last_day=soon(34),
    )

    with pytest.raises(Conflict) as exc:
        service.create(
            context, absence_type_code="ANNUAL_LEAVE",
            first_day=soon(30), last_day=soon(34),
        )
    assert exc.value.details["reason"] == "overlap"
    assert AbsenceRequest.objects.filter(employee=context.employee).count() == 1
    assert not EmployeeAbsence.objects.filter(employee=context.employee).exists()
