"""Больничные и отпуска: правила организации, остаток, продление, отмена.

Главная проверяемая мысль — правил в коде нет. Одна и та же операция
разрешена или запрещена в зависимости от настроек организации, и тесты
гоняют её при обеих настройках. Захардкоженное правило прошло бы половину
из них.

Вторая тема — разделение просьбы и факта. `AbsenceRequest` может быть
отклонена; `EmployeeAbsence` существует только после согласования и только
он попадает в статистику. Их легко перепутать, и тогда неподтверждённая
заявка начинает считаться отпуском.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from humotech.absences.models import (
    AbsenceRequest,
    AbsenceType,
    EmployeeAbsence,
    LeaveBalance,
)
from humotech.absences.policy import policy_for, save_policy
from humotech.absences.services import MINUTES_PER_WORKING_DAY, AbsenceService
from humotech.attendance import statistics
from humotech.core.errors import Conflict, PermissionDenied, ValidationFailed
from humotech.telegram.identity import resolve_by_telegram_user_id

from .conftest import bot_headers, link_telegram

pytestmark = pytest.mark.django_db

TG_ID = 777_000_111
ABSENCES = "/api/v1/me/absences"


@pytest.fixture()
def context(db, employee, telegram_settings):
    link_telegram(employee)
    return resolve_by_telegram_user_id(TG_ID)


@pytest.fixture()
def sick_leave(db, organization) -> AbsenceType:
    return AbsenceType.objects.create(
        organization=organization, code="SICK_LEAVE", name="Больничный",
        is_paid=True, requires_approval=True, requires_document=True,
        document_required_after_days=3,
    )


@pytest.fixture()
def annual_leave(db, organization) -> AbsenceType:
    return AbsenceType.objects.create(
        organization=organization, code="ANNUAL_LEAVE", name="Ежегодный отпуск",
        is_paid=True, requires_approval=True, deducts_leave_balance=True,
    )


@pytest.fixture()
def balance(db, organization, employee, annual_leave) -> LeaveBalance:
    """Двадцать восемь рабочих дней — обычная годовая норма."""
    return LeaveBalance.objects.create(
        organization=organization, employee=employee, absence_type=annual_leave,
        year=date.today().year,
        allocated_minutes=28 * MINUTES_PER_WORKING_DAY,
    )


@pytest.fixture()
def service() -> AbsenceService:
    return AbsenceService()


# Общий `hr` в наборе намеренно узкий: он не должен уметь всё подряд,
# иначе тесты чужих границ перестают что-либо проверять. Согласование
# отсутствий добавляется здесь, а не там.
ABSENCE_HR = (
    "employees.read", "absences.read", "absences.approve", "absences.documents",
)


@pytest.fixture()
def hr(make_actor, organization):
    """Кадровик, которому можно согласовывать отсутствия."""
    return make_actor(organization, permissions=ABSENCE_HR)


def soon(days: int) -> date:
    return date.today() + timedelta(days=days)


def certificate(name="spravka.pdf") -> SimpleUploadedFile:
    """Настоящий PDF по первым байтам: содержимое проверяется."""
    return SimpleUploadedFile(name, b"%PDF-1.4\nfake but correctly shaped",
                              content_type="application/pdf")


# --- умолчания политики ----------------------------------------------------

def test_defaults_are_the_cautious_ones(organization):
    """Организация, ничего не настраивавшая, живёт по осторожным правилам."""
    policy = policy_for(organization.id)

    assert policy.require_hr_approval is True
    assert policy.cancelling_approved_requires_hr is True
    assert policy.employee_may_cancel_pending is True
    assert policy.document_required is False
    assert policy.allow_negative_leave_balance is False


def test_broken_setting_falls_back_to_defaults(organization):
    """Кривой флаг в JSONB не должен ронять просмотр своих больничных."""
    save_policy(organization.id, {"require_hr_approval": "да, конечно"})

    assert policy_for(organization.id).require_hr_approval is True


# --- подача заявки ---------------------------------------------------------

def test_sick_leave_request_waits_for_hr(service, context, sick_leave):
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2), comment="Простуда",
    )

    assert view.request.status == "SUBMITTED"
    # Просьба ещё не факт: подтверждённого отсутствия нет.
    assert not EmployeeAbsence.objects.filter(
        origin_request=view.request
    ).exists()


def test_pending_request_does_not_count_as_absence(service, context, sick_leave):
    """Неподтверждённая заявка не должна попадать в статистику."""
    service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )

    report = statistics.for_period(context, soon(0), soon(2))
    assert report.sick_leave_days == 0


def test_without_approval_policy_the_absence_starts_at_once(
    service, context, sick_leave, organization
):
    """Организация может обходиться без согласования — тогда «оформил
    и ушёл»."""
    save_policy(organization.id, {"require_hr_approval": False})

    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )

    assert view.request.status == "APPROVED"
    assert EmployeeAbsence.objects.filter(origin_request=view.request).exists()


def test_backwards_period_is_refused(service, context, sick_leave):
    with pytest.raises(ValidationFailed):
        service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=soon(5), last_day=soon(1),
        )


def test_overlapping_request_is_refused(service, context, sick_leave):
    service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(1), last_day=soon(5),
    )
    with pytest.raises(Conflict) as exc:
        service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=soon(3), last_day=soon(7),
        )
    assert exc.value.details["reason"] == "duplicate_request"


def test_unknown_absence_type_is_refused(service, context):
    from humotech.core.errors import NotFound

    with pytest.raises(NotFound):
        service.create(
            context, absence_type_code="ВЫДУМАННЫЙ",
            first_day=soon(0), last_day=soon(1),
        )


# --- справка ---------------------------------------------------------------

def test_document_can_be_required_by_policy(
    service, context, sick_leave, organization
):
    save_policy(organization.id, {"document_required": True})

    with pytest.raises(ValidationFailed) as exc:
        service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=soon(0), last_day=soon(2),
        )
    assert exc.value.details["reason"] == "document_required"


def test_document_attaches_and_is_stored_privately(
    service, context, sick_leave, settings, tmp_path
):
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path)}

    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2), document=certificate(),
    )

    assert view.documents == 1
    stored = view.request.documents.first().file
    # Имя на диске не связано с исходным: ни фамилии, ни диагноза, ни даты.
    assert "spravka" not in stored.storage_key
    assert stored.mime_type == "application/pdf"
    assert len(stored.checksum_sha256) == 64


def test_a_file_lying_about_its_type_is_refused(
    service, context, sick_leave, settings, tmp_path
):
    """Заявленный тип проверяется по содержимому.

    Первые два признака — расширение и MIME — присылает клиент. Третий
    он подделать не может, не сделав файл настоящим PDF.
    """
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path)}
    fake = SimpleUploadedFile(
        "spravka.pdf", b"<html>not a pdf at all</html>",
        content_type="application/pdf",
    )

    with pytest.raises(ValidationFailed):
        service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=soon(0), last_day=soon(2), document=fake,
        )


def test_oversized_file_is_refused(
    service, context, sick_leave, organization, settings, tmp_path
):
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path)}
    save_policy(organization.id, {"max_document_bytes": 1024})
    big = SimpleUploadedFile(
        "spravka.pdf", b"%PDF-1.4\n" + b"x" * 5000,
        content_type="application/pdf",
    )

    with pytest.raises(ValidationFailed) as exc:
        service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=soon(0), last_day=soon(2), document=big,
        )
    assert exc.value.details["max_bytes"] == 1024


def test_disallowed_format_is_refused(
    service, context, sick_leave, settings, tmp_path
):
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path)}
    doc = SimpleUploadedFile(
        "spravka.docx", b"PK\x03\x04",
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )

    with pytest.raises(ValidationFailed):
        service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=soon(0), last_day=soon(2), document=doc,
        )


def test_extension_contradicting_the_declared_type_is_refused(
    service, context, sick_leave, settings, tmp_path
):
    """Расширение и MIME спорят между собой.

    Содержимое здесь настоящий PDF, и проверка по первым байтам такой
    файл пропустила бы: она сравнивает байты с ЗАЯВЛЕННЫМ типом, а
    заявлен PDF. Ловится именно противоречие в том, что прислал клиент.
    """
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path)}
    mismatched = SimpleUploadedFile(
        "spravka.png", b"%PDF-1.4\nreal pdf bytes",
        content_type="application/pdf",
    )

    with pytest.raises(ValidationFailed):
        service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=soon(0), last_day=soon(2), document=mismatched,
        )


def test_camera_file_without_extension_is_accepted(
    service, context, sick_leave, settings, tmp_path
):
    """Имя без расширения — не повод для отказа.

    Так справку отдаёт камера на части Android: имя без точки вовсе.
    Запрет по этому признаку сломал бы главный путь — съёмку справки
    телефоном, ради которого поле и переделано.
    """
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path)}
    shot = SimpleUploadedFile(
        "image", b"\xff\xd8\xff\xe0jpeg bytes",
        content_type="image/jpeg",
    )

    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2), document=shot,
    )

    assert view.documents == 1
    assert view.request.documents.first().file.mime_type == "image/jpeg"


def test_jpeg_is_accepted_under_both_of_its_extensions(
    service, context, sick_leave, settings, tmp_path
):
    """У JPEG два расширения, и оба настоящие."""
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path)}

    # Периоды разные: два больничных на одни и те же дни — это дубль,
    # и отказ пришёл бы раньше, чем дело дошло бы до расширения.
    for offset, name in enumerate(("spravka.jpg", "spravka.JPEG")):
        shot = SimpleUploadedFile(
            name, b"\xff\xd8\xffjpeg bytes", content_type="image/jpeg",
        )
        view = service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=soon(offset * 10), last_day=soon(offset * 10 + 2),
            document=shot,
        )
        assert view.documents == 1


def test_attached_document_leaves_no_trace_in_the_request_history(
    service, context, sick_leave, settings, tmp_path
):
    """Ни имени файла, ни содержимого в истории заявки.

    История видна шире, чем сама справка: имя вида
    «Иванов-туберкулёз.pdf» рассказало бы диагноз тем, кому файл
    показывать не собирались.
    """
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path)}
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
        document=certificate("Иванов-диагноз.pdf"),
    )

    trail = view.request.actions.filter(action="DOCUMENT_ATTACHED")
    assert trail.exists()
    for row in trail:
        assert row.comment is None
        assert "Иванов" not in str(row.__dict__)


def test_document_can_be_added_later_when_policy_allows(
    service, context, sick_leave, hr, settings, tmp_path
):
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path)}
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    service.decide(hr, view.request.id, approve=True)

    updated = service.attach_document(context, view.request.id, certificate())
    assert updated.documents == 1


def test_late_document_is_refused_when_policy_forbids(
    service, context, sick_leave, hr, organization, settings, tmp_path
):
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path)}
    save_policy(organization.id, {"document_can_be_added_later": False})

    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    service.decide(hr, view.request.id, approve=True)

    with pytest.raises(Conflict) as exc:
        service.attach_document(context, view.request.id, certificate())
    assert exc.value.details["reason"] == "too_late"


# --- решение отдела кадров -------------------------------------------------

def test_approval_creates_the_absence(service, context, sick_leave, hr):
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    service.decide(hr, view.request.id, approve=True)

    absence = EmployeeAbsence.objects.get(origin_request=view.request)
    assert absence.status in ("PLANNED", "ACTIVE")


def test_rejection_creates_nothing(service, context, sick_leave, hr):
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    service.decide(hr, view.request.id, approve=False, comment="Нет справки")

    view.request.refresh_from_db()
    assert view.request.status == "REJECTED"
    assert not EmployeeAbsence.objects.filter(origin_request=view.request).exists()


def test_a_decided_request_cannot_be_decided_twice(
    service, context, sick_leave, hr
):
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    service.decide(hr, view.request.id, approve=True)

    with pytest.raises(Conflict):
        service.decide(hr, view.request.id, approve=False)


def test_hr_cannot_approve_their_own_absence(
    service, context, sick_leave, make_actor, organization, employee
):
    """Кадровик, у которого есть и учётная запись, и карточка сотрудника,
    не должен подтверждать сам себе отпуск."""
    from humotech.accounts.models import User

    actor = make_actor(organization, permissions=ABSENCE_HR)
    User.objects.filter(id=actor.user_id).update(employee_id=employee.id)

    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    with pytest.raises(PermissionDenied) as exc:
        service.decide(actor, view.request.id, approve=True)
    assert exc.value.details["reason"] == "self_approval"


def test_approval_requires_the_permission(service, context, sick_leave,
                                          nobody_actor):
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    with pytest.raises(PermissionDenied):
        service.decide(nobody_actor, view.request.id, approve=True)


def test_foreign_organization_cannot_decide(service, context, sick_leave,
                                            foreign_actor):
    from humotech.core.errors import NotFound

    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    with pytest.raises((NotFound, PermissionDenied)):
        service.decide(foreign_actor, view.request.id, approve=True)


# --- отмена ----------------------------------------------------------------

def test_employee_cancels_a_pending_request(service, context, sick_leave):
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(1), last_day=soon(3),
    )
    cancelled = service.cancel(context, view.request.id)

    assert cancelled.request.status == "CANCELLED"


def test_employee_cannot_cancel_pending_when_policy_forbids(
    service, context, sick_leave, organization
):
    save_policy(organization.id, {"employee_may_cancel_pending": False})
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(1), last_day=soon(3),
    )
    with pytest.raises(PermissionDenied) as exc:
        service.cancel(context, view.request.id)
    assert exc.value.details["reason"] == "hr_required"


def test_cancelling_an_approved_absence_needs_hr_by_default(
    service, context, sick_leave, hr
):
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(1), last_day=soon(3),
    )
    service.decide(hr, view.request.id, approve=True)

    with pytest.raises(PermissionDenied):
        service.cancel(context, view.request.id)

    service.cancel_approved(hr, view.request.id, comment="По просьбе")
    absence = EmployeeAbsence.objects.get(origin_request=view.request)
    assert absence.status == "CANCELLED"


def test_employee_may_cancel_approved_when_policy_allows(
    service, context, sick_leave, hr, organization
):
    save_policy(organization.id, {"cancelling_approved_requires_hr": False})
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(1), last_day=soon(3),
    )
    service.decide(hr, view.request.id, approve=True)

    cancelled = service.cancel(context, view.request.id)
    assert cancelled.request.status == "CANCELLED"


# --- продление -------------------------------------------------------------

def test_extension_is_a_separate_request(service, context, sick_leave, hr):
    """Подтверждённые даты — документ: по ним посчитана статистика."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    service.decide(hr, view.request.id, approve=True)

    extension = service.extend(context, view.request.id, new_last_day=soon(5))

    assert extension.request.id != view.request.id
    assert extension.request.request_kind == "EXTEND"
    assert extension.request.parent_request_id == view.request.id
    assert extension.request.status == "SUBMITTED"


def test_pending_extension_is_visible_on_the_parent(
    service, context, sick_leave, hr
):
    """«Продление ждёт» — производное состояние, а не отдельный статус."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    service.decide(hr, view.request.id, approve=True)
    service.extend(context, view.request.id, new_last_day=soon(5))

    parent = service.request(context, view.request.id)
    assert parent.extension_pending is True


def test_approved_extension_moves_the_original_end(
    service, context, sick_leave, hr
):
    """У человека один непрерывный больничный, а не два подряд."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    service.decide(hr, view.request.id, approve=True)
    extension = service.extend(context, view.request.id, new_last_day=soon(5))
    service.decide(hr, extension.request.id, approve=True)

    absences = EmployeeAbsence.objects.filter(employee=context.employee)
    assert absences.count() == 1, "продление создало второе отсутствие"
    assert absences.first().end_at.date() >= soon(5)


def test_extension_shorter_than_the_original_is_refused(
    service, context, sick_leave, hr
):
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(5),
    )
    service.decide(hr, view.request.id, approve=True)

    with pytest.raises(ValidationFailed) as exc:
        service.extend(context, view.request.id, new_last_day=soon(3))
    assert exc.value.details["reason"] == "not_longer"


def test_extension_can_be_switched_off(
    service, context, sick_leave, hr, organization
):
    save_policy(organization.id, {"extensions_allowed": False})
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    service.decide(hr, view.request.id, approve=True)

    with pytest.raises(Conflict) as exc:
        service.extend(context, view.request.id, new_last_day=soon(5))
    assert exc.value.details["reason"] == "extensions_disabled"


def test_unapproved_request_cannot_be_extended(service, context, sick_leave):
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    with pytest.raises(Conflict) as exc:
        service.extend(context, view.request.id, new_last_day=soon(5))
    assert exc.value.details["reason"] == "not_approved"


# --- отпуск и остаток ------------------------------------------------------

def test_vacation_reserves_but_does_not_spend_the_balance(
    service, context, annual_leave, balance
):
    """Заявку ещё могут отклонить: списывать за то, чего не случилось,
    нельзя. Но и не резервировать нельзя — иначе на один остаток подадут
    пять заявок."""
    service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(16),
    )

    balance.refresh_from_db()
    assert balance.used_minutes == 0
    assert balance.reserved_minutes > 0


def test_approval_turns_the_reservation_into_spending(
    service, context, annual_leave, balance, hr
):
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(16),
    )
    reserved = LeaveBalance.objects.get(id=balance.id).reserved_minutes
    service.decide(hr, view.request.id, approve=True)

    balance.refresh_from_db()
    assert balance.reserved_minutes == 0
    assert balance.used_minutes == reserved


def test_rejection_gives_the_reservation_back(
    service, context, annual_leave, balance, hr
):
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(16),
    )
    service.decide(hr, view.request.id, approve=False)

    balance.refresh_from_db()
    assert balance.reserved_minutes == 0
    assert balance.used_minutes == 0


def test_cancelling_gives_the_reservation_back(
    service, context, annual_leave, balance
):
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(16),
    )
    service.cancel(context, view.request.id)

    balance.refresh_from_db()
    assert balance.reserved_minutes == 0


def test_cancelling_an_approved_vacation_gives_the_days_back(
    service, context, annual_leave, balance, hr
):
    """Отмена подтверждённого отпуска возвращает дни, а не съедает их.

    После подтверждения дни лежат уже не в резерве, а в израсходованном.
    Если отмену вычесть из резерва, он и так ноль — вычитание ничего не
    изменит, израсходованное останется начисленным, и сотрудник насовсем
    потеряет отпуск, в котором не был.
    """
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(16),
    )
    service.decide(hr, view.request.id, approve=True)
    service.cancel_approved(hr, view.request.id, comment="Перенос")

    balance.refresh_from_db()
    assert balance.reserved_minutes == 0
    assert balance.used_minutes == 0


def test_employee_cancelling_an_approved_vacation_gives_the_days_back(
    service, context, annual_leave, balance, hr, organization
):
    """Тот же возврат на втором пути отмены — когда её разрешили сотруднику."""
    save_policy(organization.id, {"cancelling_approved_requires_hr": False})
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(16),
    )
    service.decide(hr, view.request.id, approve=True)
    service.cancel(context, view.request.id)

    balance.refresh_from_db()
    assert balance.reserved_minutes == 0
    assert balance.used_minutes == 0


def test_vacation_beyond_the_balance_is_refused(
    service, context, annual_leave, balance
):
    balance.allocated_minutes = 2 * MINUTES_PER_WORKING_DAY
    balance.save(update_fields=["allocated_minutes", "updated_at"])

    with pytest.raises(Conflict) as exc:
        service.create(
            context, absence_type_code="ANNUAL_LEAVE",
            first_day=soon(10), last_day=soon(30),
        )
    assert exc.value.details["reason"] == "insufficient_balance"


def test_negative_balance_can_be_allowed_by_policy(
    service, context, annual_leave, balance, organization
):
    save_policy(organization.id, {"allow_negative_leave_balance": True})
    balance.allocated_minutes = 0
    balance.save(update_fields=["allocated_minutes", "updated_at"])

    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(12),
    )
    assert view.request.status == "SUBMITTED"


def test_vacation_without_a_balance_row_is_refused(
    service, context, annual_leave
):
    with pytest.raises(Conflict) as exc:
        service.create(
            context, absence_type_code="ANNUAL_LEAVE",
            first_day=soon(10), last_day=soon(12),
        )
    assert exc.value.details["reason"] == "no_balance"


def test_days_off_inside_a_vacation_do_not_cost_balance(
    service, context, annual_leave, balance, organization, employee
):
    """Суббота, попавшая в отпуск, не тратит остаток.

    Без учёта графика две недели отпуска стоили бы четырнадцать дней
    вместо десяти.
    """
    from .test_statistics import five_day_schedule

    five_day_schedule(organization, employee)
    # Понедельник 2026-09-07 — воскресенье 2026-09-20: две календарные недели
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=date(2026, 9, 7), last_day=date(2026, 9, 20),
    )

    assert view.working_days == 10


# --- через API -------------------------------------------------------------

def test_employee_sees_only_their_own_requests(
    bot_client, service, context, sick_leave, organization
):
    from humotech.employees.models import Employee

    stranger = Employee.objects.create(
        organization=organization, employee_number="EMP-0555",
        first_name="Чужой", last_name="Сотрудник",
        hire_date=date(2024, 1, 1), employment_status="ACTIVE",
    )
    AbsenceRequest.objects.create(
        organization=organization, employee=stranger, absence_type=sick_leave,
        request_kind="CREATE", status="SUBMITTED",
    )
    service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )

    body = bot_client.get(ABSENCES, **bot_headers(TG_ID)).json()

    assert body["total"] == 1


def test_creating_through_the_api_ignores_a_supplied_employee_id(
    bot_client, context, sick_leave, organization
):
    from humotech.employees.models import Employee

    stranger = Employee.objects.create(
        organization=organization, employee_number="EMP-0556",
        first_name="Чужой", last_name="Сотрудник",
        hire_date=date(2024, 1, 1), employment_status="ACTIVE",
    )

    response = bot_client.post(
        ABSENCES,
        {
            "absence_type_code": "SICK_LEAVE",
            "first_day": soon(0).isoformat(),
            "last_day": soon(2).isoformat(),
            "employee_id": str(stranger.id),
        },
        format="json",
        **bot_headers(TG_ID),
    )

    assert response.status_code == 201
    assert AbsenceRequest.objects.filter(employee=stranger).count() == 0
    assert AbsenceRequest.objects.filter(employee=context.employee).count() == 1


def test_options_expose_the_policy_to_the_client(bot_client, context, sick_leave):
    body = bot_client.get(
        f"{ABSENCES}/options", **bot_headers(TG_ID)
    ).json()

    assert [row["code"] for row in body["types"]] == ["SICK_LEAVE"]
    assert body["policy"]["require_hr_approval"] is True


def test_leave_balance_is_shown_in_days(bot_client, context, annual_leave,
                                        balance):
    body = bot_client.get("/api/v1/me/leave-balance", **bot_headers(TG_ID)).json()

    row = body["balances"][0]
    assert row["allocated_days"] == 28.0
    assert row["available_days"] == 28.0


def test_hr_endpoints_reject_an_employee_token(bot_client, context, sick_leave):
    """Токеном сотрудника в кадровый API не пройти.

    `EmployeePrincipal` намеренно не умеет строить `Actor`; проверка
    держится на том, что классы аутентификации сотрудника к этим view
    не подключены вовсе.
    """
    response = bot_client.get(
        "/api/v1/absence-requests/pending", **bot_headers(TG_ID)
    )
    assert response.status_code in (401, 403)


def test_approved_absence_shows_up_in_statistics(
    service, context, sick_leave, hr
):
    """Замыкание круга: подтверждённая заявка становится днями в отчёте."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    service.decide(hr, view.request.id, approve=True)

    report = statistics.for_period(context, soon(0), soon(2))
    assert report.sick_leave_days == 3


# --- продление и остаток ---------------------------------------------------

def test_extending_a_vacation_costs_the_balance(
    service, context, annual_leave, balance, hr
):
    """Продлённые дни списываются так же, как исходные.

    Иначе неделю берут заявкой, а месяц — продлением, и остаток
    уменьшается только на неделю.
    """
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(16),
    )
    service.decide(hr, view.request.id, approve=True)
    balance.refresh_from_db()
    for_the_original = balance.used_minutes

    extension = service.extend(context, view.request.id, new_last_day=soon(23))
    balance.refresh_from_db()
    assert balance.reserved_minutes > 0, "продление не зарезервировало ничего"

    service.decide(hr, extension.request.id, approve=True)
    balance.refresh_from_db()
    assert balance.reserved_minutes == 0
    assert balance.used_minutes > for_the_original, (
        "продлённые дни не списались с остатка"
    )


def test_extension_does_not_count_the_last_day_twice(
    service, context, annual_leave, balance, hr
):
    """Период продления начинается со СЛЕДУЮЩЕГО дня.

    Прежний конец принадлежит исходной заявке, и включать его в продление
    значит списать один день дважды.
    """
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(16),
    )
    service.decide(hr, view.request.id, approve=True)
    extension = service.extend(context, view.request.id, new_last_day=soon(23))
    service.decide(hr, extension.request.id, approve=True)

    balance.refresh_from_db()
    whole = service._working_days(context, soon(10), soon(23))
    assert balance.used_minutes == whole * MINUTES_PER_WORKING_DAY


def test_extension_beyond_the_balance_is_refused(
    service, context, annual_leave, balance, hr
):
    """Запрет уходить в минус нельзя обойти продлением."""
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(16),
    )
    service.decide(hr, view.request.id, approve=True)

    with pytest.raises(Conflict) as exc:
        service.extend(context, view.request.id, new_last_day=soon(400))
    assert exc.value.details["reason"] == "insufficient_balance"


def test_cancelling_a_vacation_extended_and_approved_gives_everything_back(
    service, context, annual_leave, balance, hr
):
    """Отмена возвращает и исходные дни, и добавленные продлением."""
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(16),
    )
    service.decide(hr, view.request.id, approve=True)
    extension = service.extend(context, view.request.id, new_last_day=soon(23))
    service.decide(hr, extension.request.id, approve=True)

    service.cancel_approved(hr, view.request.id, comment="Перенос")

    balance.refresh_from_db()
    assert balance.used_minutes == 0
    assert balance.reserved_minutes == 0


def test_a_rejected_extension_gives_its_reservation_back(
    service, context, annual_leave, balance, hr
):
    """Отказ в продлении возвращает ровно то, что продление отложило."""
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(16),
    )
    service.decide(hr, view.request.id, approve=True)
    balance.refresh_from_db()
    for_the_original = balance.used_minutes

    extension = service.extend(context, view.request.id, new_last_day=soon(23))
    service.decide(hr, extension.request.id, approve=False)

    balance.refresh_from_db()
    assert balance.reserved_minutes == 0
    assert balance.used_minutes == for_the_original


def test_a_vacation_is_charged_to_the_year_it_falls_in(
    service, context, annual_leave, balance, organization, employee
):
    """Остаток берётся за год отпуска, а не за год подачи заявки.

    В декабре просят январь. Если зарезервировать в старом году, а списать
    в новом, то в старом останется вечный резерв, а в новом — списание
    без резерва, и обе строки разойдутся с действительностью.

    Фикстура `balance` — строка текущего года, и до исправления резерв
    ложился именно в неё.
    """
    from humotech.absences.models import LeaveBalance as Balance

    january = date(date.today().year + 1, 1, 12)
    next_year = Balance.objects.create(
        organization=organization, employee=employee, absence_type=annual_leave,
        year=january.year, allocated_minutes=28 * MINUTES_PER_WORKING_DAY,
    )

    service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=january, last_day=january + timedelta(days=6),
    )

    next_year.refresh_from_db()
    balance.refresh_from_db()
    assert next_year.reserved_minutes > 0, (
        "резерв ушёл не в тот год: заявку подают сейчас, а отпуск в январе"
    )
    assert balance.reserved_minutes == 0, "резерв лёг в год подачи заявки"


# --- организационные правила сроков ------------------------------------------
#
# Три настройки из `absences.policy`, каждая проверяется поведением, а не
# тем, что значение записалось. Настройка, на которую никто не смотрит,
# ничем не лучше опечатки.


def test_backdating_is_unlimited_by_default(service, context, sick_leave):
    """Ноль означает «без ограничения», а не «ничего задним числом».

    Больничный по своей природе оформляется после болезни: человек
    заболел, вышел и принёс справку. Закрытое умолчание здесь сломало бы
    главный сценарий продукта, поэтому его нет.
    """
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-30), last_day=soon(-28),
    )
    assert view.request.status == "SUBMITTED"


def test_backdating_limit_refuses_an_old_absence(
    service, context, sick_leave, organization
):
    save_policy(organization.id, {"backdating_days_allowed": 3})

    with pytest.raises(ValidationFailed) as exc:
        service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=soon(-10), last_day=soon(-8),
        )
    assert exc.value.details["reason"] == "backdating_not_allowed"


def test_backdating_limit_allows_what_fits(
    service, context, sick_leave, organization
):
    save_policy(organization.id, {"backdating_days_allowed": 5})

    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-3), last_day=soon(-1),
    )
    assert view.request.status == "SUBMITTED"


def test_document_required_only_from_the_configured_day(
    service, context, sick_leave, organization
):
    """«Справка с четвёртого дня» — распространённое правило.

    Двумя булевыми его не выразить, поэтому это число: короткий
    больничный принимается без справки, длинный — нет.
    """
    save_policy(
        organization.id,
        {"document_required": True, "document_required_from_day": 4},
    )

    short = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(1),
    )
    assert short.request.status == "SUBMITTED"

    with pytest.raises(ValidationFailed) as exc:
        service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=soon(10), last_day=soon(20),
        )
    assert exc.value.details["reason"] == "document_required"


def test_vacation_lead_time_applies_only_to_leave_types(
    service, context, sick_leave, annual_leave, balance, organization
):
    """Срок подачи — про отпуск, а не про болезнь.

    Требовать заявку за две недели от больничного означало бы требовать
    планировать болезнь.
    """
    save_policy(organization.id, {"vacation_min_days_ahead": 14})

    # Больничный на завтра проходит: он под это правило не подпадает.
    assert service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(1), last_day=soon(2),
    ).request.status == "SUBMITTED"

    with pytest.raises(ValidationFailed) as exc:
        service.create(
            context, absence_type_code="ANNUAL_LEAVE",
            first_day=soon(3), last_day=soon(5),
        )
    assert exc.value.details["reason"] == "lead_time_required"


def test_vacation_lead_time_allows_a_timely_request(
    service, context, annual_leave, balance, organization
):
    save_policy(organization.id, {"vacation_min_days_ahead": 14})

    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(20), last_day=soon(22),
    )
    assert view.request.status == "SUBMITTED"
