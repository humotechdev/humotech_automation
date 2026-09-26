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

from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from humotech.absences.application_pdf import APPLICATION_DOCUMENT
from humotech.absences.models import (
    AbsenceDocument,
    AbsenceRequest,
    AbsenceType,
    EmployeeAbsence,
    LeaveBalance,
)
from humotech.absences.policy import policy_for, save_policy
from humotech.absences.services import (
    MINUTES_PER_WORKING_DAY,
    AbsenceService,
    approval_blockers,
    stage_of,
)
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
def day_off_type(db, organization) -> AbsenceType:
    """Отсутствие, под которое не нужно ни справки, ни заявления.

    Нужен там, где проверяется сама механика согласования, а не правила
    больничного: на больничном любой такой тест упёрся бы в справку.
    """
    return AbsenceType.objects.create(
        organization=organization, code="DAY_OFF", name="Отгул",
        requires_approval=True,
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


@pytest.fixture(autouse=True)
def private_files(settings, tmp_path):
    """Приложенные файлы пишутся во временную папку набора.

    Раньше это делал каждый тест, который вообще трогал документы. Теперь
    справка нужна и там, где её никто не проверяет — чтобы дойти до
    подтверждения, — и десять одинаковых строк настройки означали бы
    десять мест, где о ней забудут.
    """
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path)}


def latest_certificate(request_id) -> AbsenceDocument:
    """Последняя принесённая человеком бумага. Системный бланк не в счёт."""
    return (
        AbsenceDocument.objects
        .filter(absence_request_id=request_id)
        .exclude(document_type=APPLICATION_DOCUMENT)
        .latest("created_at")
    )


def local_day(moment, context) -> date:
    """Дата момента в поясе офиса. В UTC она бывает вчерашней."""
    return moment.astimezone(context.timezone).date()


def employee_of(context):
    return context.employee


def mark(organization, employee, office, qr_point, day: date):
    """Настоящая отметка входа в этот день.

    Событие, а не собранная из него сессия: сессия — это вывод, а
    спорить с больничным должен факт.
    """
    from humotech.attendance.models import AttendanceEvent

    return AttendanceEvent.objects.create(
        organization=organization,
        employee=employee,
        office=office,
        qr_point=qr_point,
        event_type="ENTRY",
        source="QR",
        verification_status="ACCEPTED",
        occurred_at=datetime.combine(day, time(9, 0), tzinfo=dt_timezone.utc),
    )


def hr_approves(service, context, hr, view):
    """Все три действия кадровика и подтверждение — одной строкой.

    Больничный переходит в `APPROVED` только после того, как кадровик
    принял справку, отметил пришедшее по почте заявление и проставил
    фактические даты. Тестам про продление, остаток и отмену это не
    интересно — они проверяют другое, и три вызова в каждом спрятали бы
    их собственную мысль. Сам замок проверяется отдельно, ниже.

    Период подтверждается тем же, что был в заявке: проверяется здесь не
    он, а то, что кадровик его посмотрел.
    """
    request = view.request
    if request.absence_type.requires_document and request.request_kind == "CREATE":
        service.attach_document(context, request.id, certificate())
        service.verify_document(
            hr, request.id, latest_certificate(request.id).id, accept=True,
        )
        service.mark_application_received(hr, request.id)
        if request.requested_start_at is not None:
            tz = context.timezone
            service.set_period(
                hr, request.id,
                first_day=request.requested_start_at.astimezone(tz).date(),
                last_day=request.requested_end_at.astimezone(tz).date(),
            )
    return service.decide(hr, request.id, approve=True)


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


# --- больничный без дат ----------------------------------------------------
#
# Человек заболел в пятницу вечером и не знает, выйдет ли он во вторник
# или в четверг. Требовать от него число — значит получить выдуманное,
# которое потом всё равно исправят по справке.


def test_sick_leave_is_created_without_dates(service, context, sick_leave):
    view = service.create(context, absence_type_code="SICK_LEAVE")

    assert view.request.status == "SUBMITTED"
    assert view.request.requested_start_at is None
    assert view.request.requested_end_at is None
    assert view.working_days == 0


def test_half_a_period_is_refused(service, context, sick_leave):
    """Одна дата из двух — недописанная форма, а не «неизвестно»."""
    with pytest.raises(ValidationFailed) as failure:
        service.create(
            context, absence_type_code="SICK_LEAVE", first_day=soon(0)
        )

    assert failure.value.details["reason"] == "half_period"


def test_leave_still_needs_its_dates(service, context, annual_leave, balance):
    """У отпуска даты обязательны: из них считается остаток."""
    with pytest.raises(ValidationFailed) as failure:
        service.create(context, absence_type_code="ANNUAL_LEAVE")

    assert failure.value.details["reason"] == "period_required"


def test_dateless_request_waits_for_hr_even_without_approval(
    service, context, sick_leave, organization
):
    """Отсутствие — это период в табеле, и «примерно» его не записать.

    Организация могла отключить согласование, но заявка без дат всё
    равно ждёт кадровика: ему нужно перенести период из справки.
    """
    save_policy(organization.id, {"require_hr_approval": False})

    view = service.create(context, absence_type_code="SICK_LEAVE")

    assert view.request.status == "SUBMITTED"
    assert not EmployeeAbsence.objects.filter(
        origin_request=view.request
    ).exists()


def test_hr_sets_the_real_period_from_the_certificate(
    service, context, sick_leave, hr
):
    view = service.create(context, absence_type_code="SICK_LEAVE")

    service.set_period(
        hr, view.request.id, first_day=soon(-3), last_day=soon(1),
    )

    row = AbsenceRequest.objects.get(id=view.request.id)
    # Период хранится моментами и сравнивается в поясе офиса: в UTC
    # начало суток Душанбе приходится на предыдущий день, и сравнение
    # «как есть» поймало бы разницу поясов, а не ошибку в коде.
    tz = context.timezone
    assert row.requested_start_at.astimezone(tz).date() == soon(-3)
    assert row.requested_end_at.astimezone(tz).date() == soon(1)
    # В истории заявки видно, что период взят из документа, а не со слов.
    assert row.actions.filter(action="PERIOD_SET").exists()


def test_period_is_not_editable_after_the_decision(
    service, context, sick_leave, hr
):
    """У подтверждённой заявки период уже стал строкой табеля."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-4), last_day=soon(-2),
    )
    hr_approves(service, context, hr, view)

    with pytest.raises(Conflict):
        service.set_period(
            hr, view.request.id, first_day=soon(0), last_day=soon(5),
        )


def test_application_and_certificate_are_two_separate_marks(
    service, context, sick_leave, hr
):
    """Бумага подтверждает намерение, справка — факт болезни.

    Одна отметка на оба пункта означала бы, что половину работы
    кадровик подтверждает не глядя.
    """
    view = service.create(context, absence_type_code="SICK_LEAVE")
    assert view.request.application_received_at is None

    row = service.mark_application_received(hr, view.request.id)
    assert row.application_received_at is not None
    # Справки при этом по-прежнему нет.
    assert row.documents.count() == 0

    back = service.mark_application_received(hr, view.request.id, received=False)
    assert back.application_received_at is None


def test_pending_request_does_not_count_as_absence(service, context, sick_leave):
    """Неподтверждённая заявка не должна попадать в статистику."""
    service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )

    report = statistics.for_period(context, soon(0), soon(2))
    assert report.sick_leave_days == 0


def test_without_approval_policy_the_absence_starts_at_once(
    service, context, organization, day_off_type
):
    """Организация может обходиться без согласования — тогда «оформил
    и ушёл». Но только там, где нечего проверять: у отгула бумаг нет."""
    save_policy(organization.id, {"require_hr_approval": False})

    view = service.create(
        context, absence_type_code="DAY_OFF",
        first_day=soon(0), last_day=soon(2),
    )

    assert view.request.status == "APPROVED"
    assert EmployeeAbsence.objects.filter(origin_request=view.request).exists()


def test_sick_leave_waits_for_hr_even_without_approval_policy(
    service, context, sick_leave, organization
):
    """Настройка «без согласования» больничного не касается.

    Справку принимает человек, а не флаг. Если бы флаг это обходил,
    организация одной галочкой получила бы больничные, подтверждённые
    самим фактом подачи, — и табель, в котором отсутствие есть, а
    основания под ним нет.
    """
    save_policy(organization.id, {"require_hr_approval": False})

    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )

    # Заявка не сломалась и не отклонилась — она просто ждёт кадровика.
    assert view.request.status == "SUBMITTED"
    assert not EmployeeAbsence.objects.filter(origin_request=view.request).exists()


def test_backwards_period_is_refused(service, context, sick_leave):
    with pytest.raises(ValidationFailed):
        service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=soon(5), last_day=soon(1),
        )


def test_overlapping_request_is_refused(
    service, context, annual_leave, balance
):
    """Один общий день — уже пересечение.

    Проверяется на отпуске, а не на больничном: у второго раньше
    срабатывает правило «незакрытый больничный только один», и до
    пересечения дело не доходит. Само правило пересечения при этом
    общее для всех видов отсутствия.
    """
    first = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(1), last_day=soon(5),
    )
    with pytest.raises(Conflict) as exc:
        service.create(
            context, absence_type_code="ANNUAL_LEAVE",
            first_day=soon(5), last_day=soon(7),
        )

    details = exc.value.details
    assert details["reason"] == "overlap"
    # Человеку и кадровику должно быть видно, С ЧЕМ спор: без этого
    # остаётся искать самому.
    assert details["request_id"] == str(first.request.id)
    assert details["first_day"] == soon(1).isoformat()
    assert details["last_day"] == soon(5).isoformat()


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

    # Первая заявка отменяется перед второй: незакрытый больничный у
    # человека может быть только один, и без отмены отказ пришёл бы
    # раньше, чем дело дошло бы до расширения файла.
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
        service.cancel(context, view.request.id)


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
        first_day=soon(-4), last_day=soon(-2),
    )
    hr_approves(service, context, hr, view)

    # Первую справку кадровик уже принял — без этого заявку не
    # подтвердить. Проверяется, что и после решения бумагу принимают:
    # так доносят исправленный документ.
    updated = service.attach_document(
        context, view.request.id, certificate("spravka-2.pdf"),
    )
    assert updated.documents == 2


def test_late_document_is_refused_when_policy_forbids(
    service, context, sick_leave, hr, organization, settings, tmp_path
):
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path)}
    save_policy(organization.id, {"document_can_be_added_later": False})

    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-4), last_day=soon(-2),
    )
    hr_approves(service, context, hr, view)

    with pytest.raises(Conflict) as exc:
        service.attach_document(context, view.request.id, certificate())
    assert exc.value.details["reason"] == "too_late"


# --- три замка на подтверждении больничного --------------------------------
#
# `APPROVED` — это строка в табеле, а не отметка «кадровик увидел».
# Поставить её можно только после трёх действий, и все три делает
# человек: принял справку, подтвердил, что подписанное заявление пришло
# по почте, проставил фактические даты по справке.
#
# Каждый тест ниже снимает ровно один замок и проверяет, что остальные
# держат. Проверять их скопом бессмысленно: тогда достаточно, чтобы
# работал любой один.


def test_a_sick_leave_without_dates_and_papers_cannot_be_approved(
    service, context, sick_leave, hr
):
    view = service.create(context, absence_type_code="SICK_LEAVE")

    with pytest.raises(Conflict) as exc:
        service.decide(hr, view.request.id, approve=True)

    assert exc.value.details["missing"] == [
        "certificate", "application", "period",
    ]
    view.request.refresh_from_db()
    assert view.request.status == "SUBMITTED"
    assert stage_of(view.request) == "WAITING_DOCUMENTS"


def test_the_signed_application_alone_is_not_enough(
    service, context, sick_leave, hr
):
    """Заявление пришло, справки нет — ждут по-прежнему сотрудника."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    service.mark_application_received(hr, view.request.id)

    with pytest.raises(Conflict) as exc:
        service.decide(hr, view.request.id, approve=True)
    # Период тоже в списке: даты в заявке названы сотрудником, а не
    # взяты из справки, и кадровик их ещё не сверял.
    assert exc.value.details["missing"] == ["certificate", "period"]

    view.request.refresh_from_db()
    assert stage_of(view.request) == "WAITING_DOCUMENTS"


def test_the_certificate_alone_is_not_enough(
    service, context, sick_leave, hr
):
    """Справка у кадровика, заявления нет — ждут уже его."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2), document=certificate(),
    )
    service.verify_document(
        hr, view.request.id, latest_certificate(view.request.id).id, accept=True,
    )

    with pytest.raises(Conflict) as exc:
        service.decide(hr, view.request.id, approve=True)
    assert exc.value.details["missing"] == ["application", "period"]

    view.request.refresh_from_db()
    assert stage_of(view.request) == "HR_REVIEW"


def test_both_papers_without_the_real_period_are_not_enough(
    service, context, sick_leave, hr
):
    """Обе бумаги на руках, но период неизвестен.

    Указанные сотрудником даты — ориентир, а не факт: он подал заявку в
    первый день болезни и не знал, когда выйдет. Пока кадровик не
    перенёс период из справки, записывать отсутствие в табель нечем.
    """
    view = service.create(
        context, absence_type_code="SICK_LEAVE", document=certificate(),
    )
    service.verify_document(
        hr, view.request.id, latest_certificate(view.request.id).id, accept=True,
    )
    service.mark_application_received(hr, view.request.id)

    with pytest.raises(Conflict) as exc:
        service.decide(hr, view.request.id, approve=True)
    assert exc.value.details["missing"] == ["period"]

    view.request.refresh_from_db()
    assert view.request.status == "SUBMITTED"
    assert stage_of(view.request) == "HR_REVIEW"


def test_the_employees_own_dates_are_only_a_hint(
    service, context, sick_leave, hr
):
    """Даты из заявки не заменяют собой период по справке.

    Человек называл их в первый день болезни, наугад. Принять их за
    факт значило бы записать в табель срок, который никто не сверял, —
    и третий пункт проверки перестал бы существовать ровно там, где
    заявку подали «с запасом».
    """
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2), document=certificate(),
    )
    service.verify_document(
        hr, view.request.id, latest_certificate(view.request.id).id, accept=True,
    )
    service.mark_application_received(hr, view.request.id)

    with pytest.raises(Conflict) as exc:
        service.decide(hr, view.request.id, approve=True)
    assert exc.value.details["missing"] == ["period"]

    # Кадровик подтвердил тот же период — этого достаточно: проверяется
    # не то, что даты изменились, а то, что их сверили со справкой.
    service.set_period(hr, view.request.id, first_day=soon(0), last_day=soon(2))
    assert approval_blockers(
        AbsenceRequest.objects.get(id=view.request.id)
    ) == ()


def test_extension_is_not_asked_for_papers_of_its_own(
    service, context, sick_leave, hr
):
    """Продление подтверждается по исходной заявке.

    Своего комплекта бумаг у него нет, и словарь стадий больничного к
    нему не применяется: «ожидаем документы» на продлении означало бы
    просьбу принести справку, которой никто не ждёт.
    """
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-4), last_day=soon(-2),
    )
    hr_approves(service, context, hr, view)
    extension = service.extend(context, view.request.id, new_last_day=soon(5))

    assert stage_of(extension.request) == "PENDING"
    assert approval_blockers(extension.request) == ()


def test_all_three_done_approves_and_reaches_the_timesheet(
    service, context, sick_leave, hr
):
    """Круг замыкается: три действия кадровика — и отсутствие в отчёте."""
    view = service.create(context, absence_type_code="SICK_LEAVE",
                          document=certificate())
    service.verify_document(
        hr, view.request.id, latest_certificate(view.request.id).id, accept=True,
    )
    service.mark_application_received(hr, view.request.id)
    service.set_period(hr, view.request.id, first_day=soon(-4), last_day=soon(-2))

    row = service.decide(hr, view.request.id, approve=True)

    assert row.status == "APPROVED"
    assert approval_blockers(row) == ()
    assert stage_of(row) == "APPROVED"
    assert EmployeeAbsence.objects.filter(origin_request=row).exists()
    report = statistics.for_period(context, soon(-4), soon(-2))
    assert report.sick_leave_days == 3


def test_an_unapproved_sick_leave_stays_out_of_the_timesheet(
    service, context, sick_leave, hr
):
    """Пока замки не сняты, периода в табеле нет — даже с датами.

    Это главное следствие всей конструкции: без него «ожидаем
    документы» было бы подписью под отсутствием, которое уже
    посчитано.
    """
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2), document=certificate(),
    )
    service.mark_application_received(hr, view.request.id)

    report = statistics.for_period(context, soon(0), soon(2))
    assert report.sick_leave_days == 0


def test_a_rejected_certificate_asks_for_a_new_one(
    service, context, sick_leave, hr
):
    """Отказ по справке — отдельное состояние, а не отказ по заявке.

    Человек всё это время болеет, а не числится прогулявшим: заявка
    остаётся активной, и от него ждут другую бумагу.
    """
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2), document=certificate(),
    )
    service.verify_document(
        hr, view.request.id, latest_certificate(view.request.id).id,
        accept=False, comment="Фото нечитаемое",
    )

    view.request.refresh_from_db()
    assert view.request.status == "SUBMITTED"
    assert stage_of(view.request) == "NEEDS_FIX"
    assert "certificate" in approval_blockers(view.request)

    # Принесли вторую бумагу — снова ждут кадровика, а не сотрудника.
    service.attach_document(context, view.request.id, certificate("ещё.pdf"))
    view.request.refresh_from_db()
    assert stage_of(view.request) == "HR_REVIEW"


def test_a_vacation_keeps_its_own_words(
    service, context, annual_leave, balance, hr
):
    """Словарь состояний больничного к отпуску не применяется.

    У отпуска нет ни справки, ни заявления, и «ожидаем документы» на нём
    означало бы, что человек должен принести неизвестно что.
    """
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(14),
    )

    assert stage_of(view.request) == "PENDING"
    assert approval_blockers(view.request) == ()

    row = service.decide(hr, view.request.id, approve=True)
    assert stage_of(row) == "APPROVED"


def test_a_sick_leave_ending_in_the_future_can_be_approved(
    service, context, sick_leave, hr
):
    """Справку выписывают на закрытый срок, и он уходит вперёд.

    «Нетрудоспособен с 21 по 28» приносят 23-го. Ждать 28-го, чтобы
    подтвердить заявку, значит все эти дни держать человека в табеле
    неоправданно отсутствующим — а потом не забыть вернуться.

    Отсутствие при этом ложится как запланированное: оно ещё не
    наступило целиком, и делать вид, что наступило, незачем.
    """
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-2), last_day=soon(5),
    )
    row = hr_approves(service, context, hr, view)

    assert row.status == "APPROVED"
    absence = EmployeeAbsence.objects.get(origin_request=row)
    assert absence.end_date == soon(5)
    assert absence.status in ("PLANNED", "ACTIVE")


# --- своя справка -----------------------------------------------------------


def test_the_employee_opens_the_certificate_he_brought(
    service, context, sick_leave
):
    """Бумагу приносил сам человек — смотреть на неё вправе он же.

    Иначе единственный способ её перечитать — искать файл в собственной
    переписке, а оттуда во второй раз уходит не та бумага.
    """
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-3), last_day=soon(-1),
        document=certificate(),
    )

    stream, meta = service.own_document(context, view.request.id)

    assert meta.mime_type == "application/pdf"
    assert stream.read(5) == b"%PDF-"


def test_after_a_replacement_the_employee_gets_the_new_certificate(
    service, context, sick_leave, hr
):
    """Отклонённая из заявки не пропадает — но отдают ту, что в деле."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-3), last_day=soon(-1),
        document=certificate("staraya.pdf"),
    )
    first = latest_certificate(view.request.id)
    service.verify_document(
        hr, view.request.id, first.id, accept=False, comment="Не видно дат",
    )
    service.attach_document(context, view.request.id, certificate("novaya.pdf"))

    _, meta = service.own_document(context, view.request.id)

    assert meta.id != first.file_id


def test_without_a_certificate_there_is_nothing_to_open(
    service, context, sick_leave
):
    """Справки нет — и ответ такой же, как у несуществующей записи."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-3), last_day=soon(-1),
    )

    from humotech.core.errors import NotFound

    with pytest.raises(NotFound):
        service.own_document(context, view.request.id)


# --- показания кадровику до решения -----------------------------------------
#
# Остаток и пересечения кадровик должен видеть ДО подтверждения, а не
# узнавать отказом в момент нажатия. Оба показания считаются на месте,
# из тех же данных, по которым сервис потом откажет.


def test_the_balance_reading_counts_the_request_itself_as_available(
    service, context, annual_leave, balance, hr
):
    """Свои же забронированные дни не считаются чужим расходом.

    При подаче отпуск резервирует дни. Если сравнивать потребность с
    остатком «как есть», собственный резерв выглядел бы чужим расходом,
    и «не хватает» стояло бы на каждой заявке.
    """
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(14),
    )

    reading = service.leave_check(view.request)

    assert reading["enough"] is True
    assert reading["needed_days"] > 0
    assert reading["available_days"] == 28


def test_the_balance_reading_says_when_the_days_do_not_fit(
    service, context, annual_leave, balance, hr
):
    """Остаток меньше периода — показание красное, но не отказ."""
    balance.allocated_minutes = 2 * MINUTES_PER_WORKING_DAY
    balance.save(update_fields=["allocated_minutes"])

    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(11),
    )
    # Тот же остаток, но период вырос: подтверждать такое уже нельзя.
    service.set_period(
        hr, view.request.id, first_day=soon(10), last_day=soon(25),
    )
    view.request.refresh_from_db()

    reading = service.leave_check(view.request)

    assert reading["enough"] is False
    assert reading["needed_days"] > reading["available_days"]


def test_a_sick_leave_has_no_balance_reading(service, context, sick_leave):
    """Больничный остаток не тратит — и сравнивать ему не с чем."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(1), last_day=soon(3),
    )

    assert service.leave_check(view.request) is None


def test_the_overlap_reading_names_what_the_period_argues_with(
    service, context, annual_leave, balance, sick_leave, hr
):
    """Пересечение видно до решения — и названо, а не «есть конфликт»."""
    vacation = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(14),
    )
    assert service.overlap_check(vacation.request) is None

    sick = AbsenceRequest.objects.create(
        organization_id=vacation.request.organization_id,
        employee_id=vacation.request.employee_id,
        absence_type=sick_leave,
        status="SUBMITTED",
        request_kind="CREATE",
        requested_start_at=datetime.combine(
            soon(12), time(0, 0), tzinfo=dt_timezone.utc,
        ),
        requested_end_at=datetime.combine(
            soon(16), time(18, 0), tzinfo=dt_timezone.utc,
        ),
    )

    clash = service.overlap_check(vacation.request)

    assert clash is not None
    assert clash.absence_type_name == "Больничный"
    assert sick.id is not None


def test_moving_a_vacation_period_moves_its_reservation(
    service, context, annual_leave, balance, hr
):
    """Резерв держится за конкретные дни, а не за факт заявки.

    Кадровик правит период отпуска — и старая неделя должна вернуться в
    остаток, а новая уйти в резерв. Иначе остаток расходится с табелем
    ровно на разницу.
    """
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(11),
    )
    balance.refresh_from_db()
    was = balance.reserved_minutes
    assert was > 0

    service.set_period(
        hr, view.request.id, first_day=soon(10), last_day=soon(17),
    )

    balance.refresh_from_db()
    assert balance.reserved_minutes > was
    view.request.refresh_from_db()
    reading = service.leave_check(view.request)
    # Остаток и показание обязаны сойтись: одно считает база, другое —
    # сервис, и разойтись им негде.
    assert reading["available_days"] == 28


# --- один незакрытый больничный ---------------------------------------------


def test_a_second_sick_leave_is_refused_while_the_first_is_open(
    service, context, sick_leave
):
    """Второй больничный поверх незакрытого — всегда ошибка.

    Либо человек забыл, что уже оформил, либо нажал дважды. Разбирать
    потом два комплекта справок на один период дороже, чем не дать
    создать второй.
    """
    first = service.create(context, absence_type_code="SICK_LEAVE")

    with pytest.raises(Conflict) as exc:
        service.create(context, absence_type_code="SICK_LEAVE")

    details = exc.value.details
    assert details["reason"] == "sick_leave_in_progress"
    # Приложению нужно открыть ту самую заявку, а не показать форму.
    assert details["request_id"] == str(first.request.id)
    assert details["stage"] == "WAITING_DOCUMENTS"
    assert AbsenceRequest.objects.filter(employee=context.employee).count() == 1


def test_a_closed_sick_leave_does_not_block_the_next_one(
    service, context, sick_leave
):
    """Отменённый больничный дорогу не перекрывает."""
    first = service.create(context, absence_type_code="SICK_LEAVE")
    service.cancel(context, first.request.id)

    again = service.create(context, absence_type_code="SICK_LEAVE")
    assert again.request.status == "SUBMITTED"


def test_vacations_are_not_limited_to_one_at_a_time(
    service, context, annual_leave, balance
):
    """Правило «только один» — про бумаги, а не про все отсутствия.

    Отпусков на разные даты человек вправе запланировать сколько
    угодно; мешает им друг другу только пересечение.
    """
    service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(10), last_day=soon(14),
    )
    second = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(40), last_day=soon(44),
    )
    assert second.request.status == "SUBMITTED"


# --- пересечения -------------------------------------------------------------


def test_sick_leave_cannot_be_created_over_a_confirmed_vacation(
    service, context, sick_leave, annual_leave, balance, hr
):
    """Больничный поверх подтверждённого отпуска не создаётся."""
    leave = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(-10), last_day=soon(-6),
    )
    service.decide(hr, leave.request.id, approve=True)

    with pytest.raises(Conflict) as exc:
        service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=soon(-8), last_day=soon(-4),
        )

    details = exc.value.details
    assert details["reason"] == "overlap"
    assert details["conflict_kind"] == "absence"
    assert details["request_id"] == str(leave.request.id)
    # Сообщение называет вид и период, а не «на эти даты уже что-то есть»:
    # человеку иначе остаётся искать самому.
    assert "«Ежегодный отпуск»" in str(exc.value)
    assert "подтверждённым отсутствием" in str(exc.value)


def test_hr_cannot_lay_the_real_period_over_another_absence(
    service, context, sick_leave, annual_leave, balance, hr
):
    """Даты по справке проверяются так же, как любые другие.

    Это главный случай, ради которого больничный без дат вообще
    существует: человек оформил его наугад, а справка оказалась
    длиннее и накрыла отпуск. Молча положить одно на другое нельзя.
    """
    leave = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(-10), last_day=soon(-6),
    )
    service.decide(hr, leave.request.id, approve=True)

    sick = service.create(context, absence_type_code="SICK_LEAVE")

    with pytest.raises(Conflict) as exc:
        service.set_period(
            hr, sick.request.id, first_day=soon(-8), last_day=soon(-4),
        )
    assert exc.value.details["request_id"] == str(leave.request.id)

    # Период, который никого не задевает, ставится без возражений.
    service.set_period(
        hr, sick.request.id, first_day=soon(-4), last_day=soon(-2),
    )
    row = AbsenceRequest.objects.get(id=sick.request.id)
    assert local_day(row.requested_start_at, context) == soon(-4)


def test_a_dateless_sick_leave_does_not_block_a_future_vacation(
    service, context, sick_leave, annual_leave, balance
):
    """Больничный без дат не забирает у человека весь календарь.

    Он не занимает ни одного дня, пока кадровик не проставил период по
    справке: запрещать из-за него отпуск через два месяца значило бы
    наказывать за то, что человек не знает, когда выздоровеет.
    """
    service.create(context, absence_type_code="SICK_LEAVE")

    planned = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(60), last_day=soon(64),
    )
    assert planned.request.status == "SUBMITTED"


def test_an_extension_over_someone_elses_days_is_refused(
    service, context, sick_leave, annual_leave, balance, hr
):
    """Продление добавляет сутки — и они тоже могут быть заняты."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-6), last_day=soon(-4),
    )
    hr_approves(service, context, hr, view)

    leave = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(-2), last_day=soon(2),
    )
    service.decide(hr, leave.request.id, approve=True)

    with pytest.raises(Conflict) as exc:
        service.extend(context, view.request.id, new_last_day=soon(0))
    assert exc.value.details["request_id"] == str(leave.request.id)


def make_absence(organization, employee, absence_type):
    """Прямая вставка отсутствия — мимо сервиса, как это сделал бы
    импорт, фоновая задача или чужая рука в базе.

    `at_hour` задаёт час КОНЦА последнего дня. Он и есть предмет спора:
    пока период сравнивался моментами, отсутствие до 18:00 и второе с
    20:00 тех же суток формально не пересекались.
    """
    def _make(first: date, last: date, *, at_hour: int = 23, status="ACTIVE"):
        request = AbsenceRequest.objects.create(
            organization=organization, employee=employee,
            absence_type=absence_type, request_kind="CREATE", status="APPROVED",
        )
        return EmployeeAbsence.objects.create(
            organization=organization, employee=employee,
            absence_type=absence_type, origin_request=request,
            start_at=datetime.combine(first, time(9, 0), tzinfo=dt_timezone.utc),
            end_at=datetime.combine(last, time(at_hour, 0), tzinfo=dt_timezone.utc),
            start_date=first, end_date=last,
            status=status,
        )
    return _make


def test_the_database_refuses_overlapping_absences_too(
    db, organization, employee, sick_leave
):
    """Последнее слово — за базой.

    Проверка в сервисе объясняет человеку, что он перекрывает, и без
    неё не обойтись. Но таблицу правят ещё и миграции, импорт и любой
    будущий сервис, поэтому пересечение запрещено самой схемой.
    """
    from django.db import IntegrityError, transaction

    absence = make_absence(organization, employee, sick_leave)
    absence(date(2026, 10, 1), date(2026, 10, 5))

    # Один общий день — пятое октября.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            absence(date(2026, 10, 5), date(2026, 10, 10))


def test_periods_that_only_touch_do_not_conflict(
    db, organization, employee, sick_leave
):
    """1–5 и 6–10 октября — соседи, а не спорщики.

    Граница диапазона полуоткрытая: `[01.10, 06.10)` кончается ДО
    шестого, и второе отсутствие начинается ровно там, где первое
    закончилось.
    """
    absence = make_absence(organization, employee, sick_leave)
    absence(date(2026, 10, 1), date(2026, 10, 5))
    second = absence(date(2026, 10, 6), date(2026, 10, 10))

    assert second.pk is not None


def test_the_hour_of_the_day_does_not_decide_the_conflict(
    db, organization, employee, sick_leave
):
    """Спор идёт за сутки, а не за часы.

    Это и есть причина перехода на календарные дни. Первое отсутствие
    кончается пятого в 18:00, второе начинается пятого же в 20:00 —
    по моментам они не пересекались, и база их пропускала. В табеле
    это один день, и двух отсутствий в нём быть не может.
    """
    from django.db import IntegrityError, transaction

    absence = make_absence(organization, employee, sick_leave)
    absence(date(2026, 10, 1), date(2026, 10, 5), at_hour=18)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            absence(date(2026, 10, 5), date(2026, 10, 10), at_hour=23)


def test_a_cancelled_absence_frees_its_days(
    db, organization, employee, sick_leave
):
    """Отменённое отсутствие сохраняет период, но суток не занимает."""
    absence = make_absence(organization, employee, sick_leave)
    absence(date(2026, 10, 1), date(2026, 10, 5), status="CANCELLED")

    again = absence(date(2026, 10, 3), date(2026, 10, 7))
    assert again.pk is not None


def test_the_database_refuses_a_second_open_sick_leave(
    db, organization, employee, sick_leave
):
    """Барьер на заявках: второй незакрытый больничный не вставится.

    Сервис проверяет это раньше и отвечает понятнее. Но заявку заводит
    не только личный кабинет, и путь, который о правиле не знает,
    должен упереться в базу.
    """
    from django.db import IntegrityError, transaction

    def submit(status="SUBMITTED", kind="CREATE"):
        return AbsenceRequest.objects.create(
            organization=organization, employee=employee,
            absence_type=sick_leave, request_kind=kind, status=status,
        )

    submit()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            submit()


def test_terminal_requests_do_not_hold_the_place(
    db, organization, employee, sick_leave, annual_leave
):
    """Барьер держат только незакрытые заявки исходного вида.

    Решённая заявка дорогу не перекрывает, продление и отмена — тоже
    (у них нет своего комплекта бумаг), и отпуск в правило не входит
    вовсе: их на разные даты можно запланировать сколько угодно.
    """
    def submit(status, kind="CREATE", kind_of=None, parent=None):
        return AbsenceRequest.objects.create(
            organization=organization, employee=employee,
            absence_type=kind_of or sick_leave, request_kind=kind,
            status=status, parent_request=parent,
        )

    done = submit("APPROVED")
    submit("REJECTED")
    submit("CANCELLED")

    # Место свободно: ни одна из трёх не ждёт.
    live = submit("SUBMITTED")

    # Продление и отмена ссылаются на решённую — и не считаются.
    submit("SUBMITTED", kind="EXTEND", parent=done)
    submit("SUBMITTED", kind="CANCEL", parent=done)

    # Отпусков можно подать хоть три.
    submit("SUBMITTED", kind_of=annual_leave)
    submit("SUBMITTED", kind_of=annual_leave)

    assert live.pk is not None


def test_a_lost_race_answers_like_the_service(
    service, context, sick_leave
):
    """Отказ базы доходит до человека теми же словами.

    Проверка в сервисе почти всегда срабатывает первой. Сюда доходит
    проигранная гонка — и для человека это ровно тот же случай, а
    значит и ответ должен быть тот же, а не «внутренняя ошибка».
    """
    from humotech.absences.services import guard_of

    first = service.create(context, absence_type_code="SICK_LEAVE")

    # Прямая вставка мимо сервиса: так сделала бы фоновая задача.
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError) as exc:
        with transaction.atomic():
            AbsenceRequest.objects.create(
                organization_id=context.organization_id,
                employee_id=context.employee.id,
                absence_type=sick_leave,
                request_kind="CREATE",
                status="SUBMITTED",
            )
    # База назвала своё ограничение — по имени, а не по тексту.
    assert guard_of(exc.value) == "sick_leave_in_progress"

    # А через сервис человек получает объяснение и ссылку на заявку.
    with pytest.raises(Conflict) as domain:
        service.create(context, absence_type_code="SICK_LEAVE")
    assert domain.value.details["request_id"] == str(first.request.id)


# --- «справку не приняли» — это не отклонённая заявка -------------------------
#
# Разница здесь не в формулировке, а в последствиях. Отклонённая заявка
# заканчивает процесс: ни бумаг, ни повторной загрузки, человек подаёт
# всё заново. Непринятая справка не заканчивает ничего — заявка живёт,
# и ждут от человека одну вещь: другую бумагу.


def test_a_refused_certificate_keeps_the_whole_request_alive(
    service, context, sick_leave, hr
):
    """Отказ по бумаге не трогает решения по заявке."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-6), last_day=soon(-4), document=certificate(),
    )
    service.verify_document(
        hr, view.request.id, latest_certificate(view.request.id).id,
        accept=False, comment="В справке не видны даты периода",
    )

    row = AbsenceRequest.objects.get(id=view.request.id)
    assert row.status == "SUBMITTED", "заявка не должна закрываться"
    assert stage_of(row) == "NEEDS_FIX"
    # Ни отсутствия, ни строки в табеле: подтверждения не было.
    assert not EmployeeAbsence.objects.filter(origin_request=row).exists()
    assert statistics.for_period(context, soon(-6), soon(-4)).sick_leave_days == 0
    # Файл на месте: его не выбрасывают, по нему потом разбираются.
    assert AbsenceDocument.objects.filter(absence_request=row).exists()


def test_a_new_certificate_keeps_the_old_one_in_history(
    service, context, sick_leave, hr
):
    """Новая бумага добавляется версией, а не заменяет прежнюю.

    Прошлую справку и комментарий к ней нельзя «стереть» повторной
    загрузкой: спор о больничном — это спор о том, что именно принесли
    и что об этом сказали.
    """
    view = service.create(
        context, absence_type_code="SICK_LEAVE", document=certificate("первая.pdf"),
    )
    first = latest_certificate(view.request.id)
    service.verify_document(
        hr, view.request.id, first.id, accept=False, comment="Нечитаемое фото",
    )

    service.attach_document(context, view.request.id, certificate("вторая.pdf"))

    papers = list(
        AbsenceDocument.objects
        .filter(absence_request_id=view.request.id)
        .exclude(document_type=APPLICATION_DOCUMENT)
    )
    assert len(papers) == 2, "прежняя справка исчезла"
    old = next(one for one in papers if one.id == first.id)
    assert old.verification_status == "REJECTED"
    assert old.verification_comment == "Нечитаемое фото"

    # И заявка возвращается к кадровику, а не остаётся «нужны исправления».
    row = AbsenceRequest.objects.get(id=view.request.id)
    assert stage_of(row) == "HR_REVIEW"

    # В истории видны оба шага: отказ и новая загрузка.
    actions = list(row.actions.values_list("action", flat=True))
    assert "DOCUMENT_REJECTED" in actions
    assert actions.count("DOCUMENT_ATTACHED") == 2


def test_the_refusal_reason_goes_to_the_employee(
    service, context, sick_leave, hr
):
    """Причина уходит человеку дословно — вместе со ссылкой на заявку."""
    from humotech.notifications.models import Notification

    view = service.create(
        context, absence_type_code="SICK_LEAVE", document=certificate(),
    )
    service.verify_document(
        hr, view.request.id, latest_certificate(view.request.id).id,
        accept=False, comment="В справке не видны даты периода",
    )

    note = Notification.objects.filter(
        employee=context.employee, notification_type="absence.document_rejected"
    ).latest("created_at")
    assert "В справке не видны даты периода" in note.body
    assert str(note.related_entity_id) == str(view.request.id)


def test_a_closed_request_gives_out_no_paperwork(
    service, context, sick_leave, hr, hr_actor, make_actor, organization
):
    """У отклонённой и отменённой заявки бумаг больше нет.

    Подписанное заявление по отказанной заявке потом всплывает в
    переписке как действующее. Интерфейсы это и так прячут — но прячет
    интерфейс, а отвечает сервер.
    """
    reader = make_actor(
        organization, permissions=("absences.read", "employees.read"),
    )

    view = service.create(context, absence_type_code="SICK_LEAVE")
    service.decide(hr, view.request.id, approve=False, comment="Не подтверждено")

    with pytest.raises(Conflict) as exc:
        service.application(context, view.request.id)
    assert exc.value.details["reason"] == "request_closed"

    with pytest.raises(Conflict):
        service.hr_application(reader, view.request.id)

    with pytest.raises(Conflict):
        service.attach_document(context, view.request.id, certificate())


def test_hr_reads_one_request_by_its_own_address(
    service, context, sick_leave, hr, make_actor, organization, foreign_actor
):
    """Страница заявки читает её отдельно от очереди.

    Искать заявку перебором очереди значит зависеть от того, попала ли
    она в текущую выборку фильтров.
    """
    from humotech.core.errors import NotFound

    reader = make_actor(
        organization, permissions=("absences.read", "employees.read"),
    )
    view = service.create(context, absence_type_code="SICK_LEAVE")

    row = service.for_hr(reader, view.request.id)
    assert row.id == view.request.id

    # Чужая организация отвечает так же, как несуществующая.
    with pytest.raises((NotFound, PermissionDenied)):
        service.for_hr(foreign_actor, view.request.id)


# --- отметки посещаемости ----------------------------------------------------


def test_real_marks_inside_the_period_stop_the_approval(
    service, context, sick_leave, hr, office, qr_point, organization
):
    """Отработанный день нельзя молча списать в больничный."""
    view = service.create(context, absence_type_code="SICK_LEAVE")
    mark(organization, employee_of(context), office, qr_point, soon(-3))

    service.attach_document(context, view.request.id, certificate())
    service.verify_document(
        hr, view.request.id, latest_certificate(view.request.id).id, accept=True,
    )
    service.mark_application_received(hr, view.request.id)
    service.set_period(hr, view.request.id, first_day=soon(-4), last_day=soon(-2))

    with pytest.raises(Conflict) as exc:
        service.decide(hr, view.request.id, approve=True)

    details = exc.value.details
    assert details["reason"] == "attendance_conflict"
    assert soon(-3).isoformat() in details["days"]
    # Заявка осталась активной: решение за кадровиком, а не за системой.
    view.request.refresh_from_db()
    assert view.request.status == "SUBMITTED"


def test_hr_may_override_the_marks_with_a_reason(
    service, context, sick_leave, hr, office, qr_point, organization
):
    """Осознанное решение проходит — и остаётся в истории заявки."""
    view = service.create(context, absence_type_code="SICK_LEAVE")
    mark(organization, employee_of(context), office, qr_point, soon(-3))

    service.attach_document(context, view.request.id, certificate())
    service.verify_document(
        hr, view.request.id, latest_certificate(view.request.id).id, accept=True,
    )
    service.mark_application_received(hr, view.request.id)
    service.set_period(hr, view.request.id, first_day=soon(-4), last_day=soon(-2))

    # Без причины решение не принимается: оно останется в деле.
    with pytest.raises(ValidationFailed):
        service.decide(
            hr, view.request.id, approve=True, override_marks=True,
        )

    row = service.decide(
        hr, view.request.id, approve=True, override_marks=True,
        comment="Отметил коллега по ошибке",
    )
    assert row.status == "APPROVED"
    step = row.actions.filter(action="MARKS_OVERRIDDEN").first()
    assert step is not None
    assert step.comment == "Отметил коллега по ошибке"

    # Сырые события не тронуты: отметка — исторический факт.
    from humotech.attendance.models import AttendanceEvent

    assert AttendanceEvent.objects.filter(employee=context.employee).count() == 1


# --- табель и аналитика -------------------------------------------------------


def test_a_backdated_sick_leave_repaints_the_past(
    service, context, sick_leave, hr
):
    """Дни «нет отметки» становятся больничным — и только после решения.

    Заявку подают, выйдя с больничного: человек заболел, отлежался и
    принёс справку. До подтверждения эти дни в табеле остаются такими,
    какими были.
    """
    before = statistics.for_period(context, soon(-6), soon(-4))
    assert before.sick_leave_days == 0

    view = service.create(context, absence_type_code="SICK_LEAVE")
    service.attach_document(context, view.request.id, certificate())
    service.verify_document(
        hr, view.request.id, latest_certificate(view.request.id).id, accept=True,
    )
    service.mark_application_received(hr, view.request.id)
    service.set_period(hr, view.request.id, first_day=soon(-6), last_day=soon(-4))

    # Период проставлен, решения ещё нет — табель прежний.
    assert statistics.for_period(context, soon(-6), soon(-4)).sick_leave_days == 0

    service.decide(hr, view.request.id, approve=True)
    after = statistics.for_period(context, soon(-6), soon(-4))
    assert after.sick_leave_days == 3
    # Дни перестали быть «рабочий день без отметок».
    assert all(not day.missed for day in after.days if day.absence_code)


def test_a_rejected_sick_leave_leaves_the_timesheet_alone(
    service, context, sick_leave, hr
):
    """Отказ ничего не перекрашивает."""
    view = service.create(context, absence_type_code="SICK_LEAVE")
    service.set_period(hr, view.request.id, first_day=soon(-6), last_day=soon(-4))
    service.decide(hr, view.request.id, approve=False, comment="Не тот бланк")

    report = statistics.for_period(context, soon(-6), soon(-4))
    assert report.sick_leave_days == 0
    assert not EmployeeAbsence.objects.filter(origin_request=view.request).exists()


def test_an_unapproved_sick_leave_is_invisible_to_analytics(
    service, context, sick_leave, hr, make_actor, organization
):
    """Показатель «На больничном» считает подтверждённые отсутствия.

    Дашборд строится по тем же `EmployeeAbsence`, что и табель, — и это
    не совпадение, а то, ради чего заявка и отсутствие разделены:
    неподтверждённая просьба не должна попадать ни туда, ни туда.
    """
    from humotech.attendance.hr import AttendanceHrService

    watcher = make_actor(
        organization, permissions=("attendance.read", "employees.read"),
    )

    view = service.create(context, absence_type_code="SICK_LEAVE")
    service.set_period(
        hr, view.request.id, first_day=soon(-1), last_day=soon(1),
    )

    def on_sick_leave() -> int:
        report = AttendanceHrService().presence(
            watcher, day=date.today(), state="SICK_LEAVE",
        )
        return len(report.rows)

    assert on_sick_leave() == 0

    service.attach_document(context, view.request.id, certificate())
    service.verify_document(
        hr, view.request.id, latest_certificate(view.request.id).id, accept=True,
    )
    service.mark_application_received(hr, view.request.id)
    service.set_period(
        hr, view.request.id, first_day=soon(-2), last_day=soon(0),
    )
    service.decide(hr, view.request.id, approve=True)

    assert on_sick_leave() == 1


# --- решение отдела кадров -------------------------------------------------

def test_approval_creates_the_absence(service, context, sick_leave, hr):
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-4), last_day=soon(-2),
    )
    hr_approves(service, context, hr, view)

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
        first_day=soon(-4), last_day=soon(-2),
    )
    hr_approves(service, context, hr, view)

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
        first_day=soon(-4), last_day=soon(-2),
    )
    hr_approves(service, context, hr, view)

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
        first_day=soon(-4), last_day=soon(-2),
    )
    hr_approves(service, context, hr, view)

    cancelled = service.cancel(context, view.request.id)
    assert cancelled.request.status == "CANCELLED"


# --- продление -------------------------------------------------------------

def test_extension_is_a_separate_request(service, context, sick_leave, hr):
    """Подтверждённые даты — документ: по ним посчитана статистика."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-4), last_day=soon(-2),
    )
    hr_approves(service, context, hr, view)

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
        first_day=soon(-4), last_day=soon(-2),
    )
    hr_approves(service, context, hr, view)
    service.extend(context, view.request.id, new_last_day=soon(5))

    parent = service.request(context, view.request.id)
    assert parent.extension_pending is True


def test_approved_extension_moves_the_original_end(
    service, context, sick_leave, hr
):
    """У человека один непрерывный больничный, а не два подряд."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-4), last_day=soon(-2),
    )
    hr_approves(service, context, hr, view)
    extension = service.extend(context, view.request.id, new_last_day=soon(5))
    service.decide(hr, extension.request.id, approve=True)

    absences = EmployeeAbsence.objects.filter(employee=context.employee)
    assert absences.count() == 1, "продление создало второе отсутствие"
    row = absences.first()
    assert row.end_at.date() >= soon(5)
    # Календарный конец двигается вместе с моментом: по нему считаются
    # пересечения, и разойтись им нельзя.
    assert row.end_date == soon(5)


def test_extension_shorter_than_the_original_is_refused(
    service, context, sick_leave, hr
):
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-5), last_day=soon(-1),
    )
    hr_approves(service, context, hr, view)

    with pytest.raises(ValidationFailed) as exc:
        service.extend(context, view.request.id, new_last_day=soon(-3))
    assert exc.value.details["reason"] == "not_longer"


def test_extension_can_be_switched_off(
    service, context, sick_leave, hr, organization
):
    save_policy(organization.id, {"extensions_allowed": False})
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(-4), last_day=soon(-2),
    )
    hr_approves(service, context, hr, view)

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


def test_a_stranger_request_cannot_be_touched_by_its_id(
    service, context, sick_leave, organization
):
    """Подменённый идентификатор не открывает чужую заявку.

    Ни прочитать, ни приложить справку, ни отменить: у сотрудника есть
    ровно его собственные заявки, и «не твоя» отвечает тем же, чем
    «не существует», — иначе по ответу можно перебрать чужие.
    """
    from humotech.employees.models import Employee

    stranger = Employee.objects.create(
        organization=organization, employee_number="EMP-0777",
        first_name="Чужой", last_name="Сотрудник",
        hire_date=date(2024, 1, 1), employment_status="ACTIVE",
    )
    alien = AbsenceRequest.objects.create(
        organization=organization, employee=stranger, absence_type=sick_leave,
        request_kind="CREATE", status="SUBMITTED",
    )

    from humotech.core.errors import NotFound

    with pytest.raises(NotFound):
        service.request(context, alien.id)
    with pytest.raises(NotFound):
        service.attach_document(context, alien.id, certificate())
    with pytest.raises(NotFound):
        service.cancel(context, alien.id)
    with pytest.raises(NotFound):
        service.application(context, alien.id)


def test_hr_actions_refuse_a_request_from_another_organization(
    service, context, sick_leave, foreign_actor
):
    """Кадровик соседней организации не подтвердит ни бумагу, ни даты.

    Проверка организации стоит в каждом действии отдельно, а не один
    раз при входе: подтверждение заявления, период и решение по заявке
    — три разных вызова, и общий фильтр в одном из них ничего не
    говорит про остальные два.
    """
    from humotech.core.errors import NotFound

    view = service.create(context, absence_type_code="SICK_LEAVE")

    with pytest.raises((NotFound, PermissionDenied)):
        service.mark_application_received(foreign_actor, view.request.id)
    with pytest.raises((NotFound, PermissionDenied)):
        service.set_period(
            foreign_actor, view.request.id,
            first_day=soon(-4), last_day=soon(-2),
        )
    with pytest.raises((NotFound, PermissionDenied)):
        service.decide(foreign_actor, view.request.id, approve=True)


def test_a_closed_request_takes_no_more_documents(
    service, context, sick_leave, hr
):
    """В отклонённую и отменённую заявку файл не загрузить.

    Она уже ничего не ждёт, и приложенная к ней бумага просто исчезнет
    из виду — вместе с временем, которое человек на неё потратил.
    """
    rejected = service.create(context, absence_type_code="SICK_LEAVE")
    service.decide(hr, rejected.request.id, approve=False, comment="Не та")

    with pytest.raises(Conflict):
        service.attach_document(context, rejected.request.id, certificate())

    cancelled = service.create(context, absence_type_code="SICK_LEAVE")
    service.cancel(context, cancelled.request.id)

    with pytest.raises(Conflict):
        service.attach_document(context, cancelled.request.id, certificate())


def test_a_terminal_request_cannot_be_cancelled_twice(
    service, context, sick_leave
):
    """Отменённую заявку нельзя отменить ещё раз."""
    view = service.create(context, absence_type_code="SICK_LEAVE")
    service.cancel(context, view.request.id)

    with pytest.raises(Conflict):
        service.cancel(context, view.request.id)


def test_the_server_checks_the_file_itself(service, context, sick_leave):
    """Размер и тип проверяет сервер, а не форма.

    Форма — подсказка: её можно обойти, отправив запрос мимо интерфейса.
    """
    from humotech.absences.policy import save_policy

    save_policy(context.organization_id, {"max_document_bytes": 1024})

    heavy = SimpleUploadedFile(
        "spravka.pdf", b"%PDF-1.4" + b"x" * 2048, content_type="application/pdf",
    )
    with pytest.raises(ValidationFailed):
        service.create(
            context, absence_type_code="SICK_LEAVE", document=heavy,
        )

    # Переименованный исполняемый файл — тоже не справка.
    fake = SimpleUploadedFile(
        "spravka.pdf", b"MZ\x90\x00executable", content_type="application/pdf",
    )
    with pytest.raises(ValidationFailed):
        service.create(
            context, absence_type_code="SICK_LEAVE", document=fake,
        )


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
        first_day=soon(-4), last_day=soon(-2),
    )
    hr_approves(service, context, hr, view)

    report = statistics.for_period(context, soon(-4), soon(-2))
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
    # Незакрытый больничный только один — короткий надо закрыть, иначе
    # длинный не дойдёт до проверки справки.
    service.cancel(context, short.request.id)

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
