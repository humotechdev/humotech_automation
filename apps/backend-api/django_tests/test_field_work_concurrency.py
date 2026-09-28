"""Выездная работа под гонками: два действия одновременно.

Отдельный файл по той же причине, что test_absence_concurrency.py:
обычный тест идёт в одной транзакции, и «параллельные» вызовы в нём
делят соединение — блокировка не мешает сама себе, и зелёный тест ничего
не значит. Здесь `transaction=True`, у каждого потока своё соединение.

Каждый тест прогоняется несколько раз: исход гонки зависит от того, кто
первым возьмёт блокировку, и инвариант обязан держаться при любом
порядке, а не при удачном.
"""

from __future__ import annotations

from datetime import timedelta
from threading import Barrier

import pytest
from django.utils import timezone

from django_tests.test_absence_concurrency import in_parallel
from django_tests.test_field_work import field_setup  # noqa: F401 — фикстура
from humotech.attendance.field_work import FieldWorkService
from humotech.attendance.models import AttendanceSession, FieldWorkRequest
from humotech.attendance.services import register_scan
from humotech.audit.models import AuditLog

pytestmark = pytest.mark.django_db(transaction=True)

TELEGRAM_ID = 777_000_111
ROUNDS = 4


def _race(*actions):
    """Запустить действия одновременно: барьер выравнивает старт."""
    barrier = Barrier(len(actions))
    queue = list(actions)

    def work():
        action = queue.pop()
        barrier.wait(timeout=10)
        return action()

    return in_parallel(work, times=len(actions))


def _pending(actor, employee, day):
    FieldWorkRequest.objects.filter(employee=employee).delete()
    return FieldWorkService().create(actor, employee_id=employee.id, day=day,
                                     manager_confirmed=True)


def test_confirm_and_cancel_at_once_always_end_cancelled(
    field_setup, employee  # noqa: F811
):
    actor, day = field_setup
    for _ in range(ROUNDS):
        row = _pending(actor, employee, day)

        def confirm():
            FieldWorkService().decide(row.id, telegram_user_id=TELEGRAM_ID, decision="CONFIRM")
            return "confirmed"

        def cancel():
            FieldWorkService().cancel(actor, row.id)
            return "cancelled"

        results = _race(confirm, cancel)

        # Либо отмена успела первой и подтверждение отказано, либо
        # подтверждение прошло и отмена сняла его. Засчитанного дня после
        # отмены не остаётся ни при каком порядке.
        assert "cancelled" in results, results
        assert results in (["cancelled", "confirmed"], ["cancelled", "closed_or_expired"]), results
        assert FieldWorkRequest.objects.get(id=row.id).status == "CANCELLED"


def test_repeated_callback_confirms_once(field_setup, employee):  # noqa: F811
    actor, day = field_setup
    for _ in range(ROUNDS):
        row = _pending(actor, employee, day)

        def confirm():
            return FieldWorkService().decide(
                row.id, telegram_user_id=TELEGRAM_ID, decision="CONFIRM").status

        assert _race(confirm, confirm, confirm) == ["CONFIRMED"] * 3
        assert AuditLog.objects.filter(action="field_work.confirmed", entity_id=row.id).count() == 1


def test_confirm_and_decline_at_once_leave_one_answer(field_setup, employee):  # noqa: F811
    actor, day = field_setup
    for _ in range(ROUNDS):
        row = _pending(actor, employee, day)

        def answer(decision):
            def run():
                return FieldWorkService().decide(
                    row.id, telegram_user_id=TELEGRAM_ID, decision=decision).status
            return run

        results = _race(answer("CONFIRM"), answer("DECLINE"))

        final = FieldWorkRequest.objects.get(id=row.id).status
        assert final in ("CONFIRMED", "DECLINED")
        assert sorted([final, "closed_or_expired"]) == results, results
        assert AuditLog.objects.filter(entity_id=row.id,
                                       action__in=("field_work.confirmed", "field_work.declined")).count() == 1


# Раунд — отдельный тест: принятая отметка неизменна и не удаляется,
# а база между тестами с `transaction=True` очищается целиком.
@pytest.mark.parametrize("attempt", range(ROUNDS))
def test_confirm_and_qr_entry_at_once_never_both(
    field_setup, employee, qr_point, attempt  # noqa: F811
):
    actor, day = field_setup
    row = _pending(actor, employee, day)
    moment = timezone.now()

    def confirm():
        FieldWorkService().decide(row.id, telegram_user_id=TELEGRAM_ID, decision="CONFIRM")
        return "confirmed"

    def scan():
        result = register_scan(
            employee_id=employee.id, qr_point=qr_point, now=moment,
            qr_nonce_hash=f"race-{attempt}", qr_issued_at=moment,
            qr_expires_at=moment + timedelta(seconds=45))
        return "scanned" if result.accepted else f"scan:{result.rejection_reason}"

    results = _race(confirm, scan)

    confirmed = FieldWorkRequest.objects.get(id=row.id).status == "CONFIRMED"
    present = AttendanceSession.objects.filter(employee=employee).exists()
    # Ровно одно основание зачёта: выезд или физическое присутствие.
    assert confirmed != present, results
    # Отметка первой сама отменяет ждущий запрос (field_work.cancelled_by_qr),
    # и нажатие «Подтверждаю» после неё получает «запрос уже закрыт».
    assert results in (
        ["confirmed", "scan:FIELD_WORK_CONFIRMED"],
        ["closed_or_expired", "scanned"],
    ), results
    if present:
        assert FieldWorkRequest.objects.get(id=row.id).status == "CANCELLED"


def test_parallel_create_makes_one_request(field_setup, employee):  # noqa: F811
    actor, day = field_setup

    def create():
        FieldWorkService().create(actor, employee_id=employee.id, day=day,
                                  manager_confirmed=True)
        return "ok"

    results = _race(create, create)

    assert results.count("ok") == 1, results
    assert FieldWorkRequest.objects.filter(employee=employee, date=day).count() == 1
