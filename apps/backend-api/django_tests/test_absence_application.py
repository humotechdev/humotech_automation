"""Печатное заявление и сквозной путь больничного.

Проверяется не «PDF собрался», а то, из-за чего бумажная часть
процесса ломается:

— заявление, которое считается принесённой справкой;
— бланк, разошедшийся с продлённой заявкой;
— кириллица, вышедшая пустыми прямоугольниками;
— отклонённый документ, о котором человеку не сказали;
— отпуск, попавший в табель до одобрения.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from humotech.absences.application_pdf import (
    APPLICATION_DOCUMENT,
    Application,
    build,
    human_date,
    plural_days,
)
from humotech.absences.models import AbsenceRequest, EmployeeAbsence
from humotech.absences.services import AbsenceService
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
def sick_leave(db, organization):
    from humotech.absences.models import AbsenceType

    return AbsenceType.objects.create(
        organization=organization, code="SICK_LEAVE", name="Больничный",
        is_paid=True, requires_approval=True, requires_document=True,
        document_required_after_days=3,
    )


@pytest.fixture()
def annual_leave(db, organization):
    from humotech.absences.models import AbsenceType

    return AbsenceType.objects.create(
        organization=organization, code="ANNUAL_LEAVE",
        name="Ежегодный отпуск", is_paid=True, requires_approval=True,
    )


@pytest.fixture()
def service() -> AbsenceService:
    return AbsenceService()


@pytest.fixture()
def hr(make_actor, organization):
    return make_actor(
        organization,
        permissions=("employees.read", "absences.read", "absences.approve",
                     "absences.documents"),
    )


def sample(**over) -> Application:
    base = dict(
        organization="ООО «HUMOTECH»",
        employee_name="Мурадов Азизбек Шухратович",
        position="Ведущий инженер",
        department="Отдел разработки",
        office="Ташкент",
        absence_name="Больничный",
        absence_code="SICK_LEAVE",
        first_day=date(2026, 3, 10),
        last_day=date(2026, 3, 14),
        days=5,
        comment=None,
        number="A1B2C3D4",
    )
    base.update(over)
    return Application(**base)


# --- бланк -------------------------------------------------------------------


class TestDocument:
    def test_it_is_a_pdf(self):
        data = build(sample())

        assert data.startswith(b"%PDF-")
        assert data.rstrip().endswith(b"%%EOF")

    def test_russian_text_is_embedded_as_a_font_not_as_boxes(self):
        # Во встроенных шрифтах PDF кириллицы нет вовсе. Без TTF
        # заявление вышло бы из пустых прямоугольников — и увидели бы
        # это только на печати.
        data = build(sample())

        assert b"/FontFile2" in data, "шрифт не встроен в документ"

    def test_dates_are_written_the_way_people_write_them(self):
        assert human_date(date(2026, 3, 14)) == "14 марта 2026 г."

    def test_days_are_counted_in_russian(self):
        # «11 календарный день» читается как ошибка данных.
        assert plural_days(1) == "1 календарный день"
        assert plural_days(3) == "3 календарных дня"
        assert plural_days(5) == "5 календарных дней"
        assert plural_days(11) == "11 календарных дней"
        assert plural_days(21) == "21 календарный день"

    def test_missing_fields_do_not_break_the_form(self):
        # Должность и отдел могут быть не назначены — бланк всё равно
        # должен собраться: человеку он нужен именно сейчас.
        data = build(sample(position=None, department=None, office=None))

        assert data.startswith(b"%PDF-")


# --- заявление по заявке -----------------------------------------------------


class TestByRequest:
    def test_employee_gets_the_form_for_their_request(
        self, service, context, sick_leave
    ):
        first = date.today()
        made = service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=first, last_day=first + timedelta(days=2),
        )

        data = service.application(context, made.request.id)

        assert data.startswith(b"%PDF-")

    def test_form_follows_the_request_and_is_not_frozen(
        self, service, context, sick_leave
    ):
        # Бланк собирается заново на каждое обращение. Сохранённый
        # однажды, он молча разошёлся бы с продлённой заявкой.
        first = date.today()
        made = service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=first, last_day=first,
        )
        short = service.application(context, made.request.id)

        request = AbsenceRequest.objects.get(id=made.request.id)
        request.requested_end_at = request.requested_end_at + timedelta(days=5)
        request.save(update_fields=["requested_end_at"])
        longer = service.application(context, made.request.id)

        assert short != longer

    def test_foreign_request_is_not_found(self, service, context, sick_leave):
        from humotech.core.errors import NotFound
        import uuid

        with pytest.raises(NotFound):
            service.application(context, uuid.uuid4())

    def test_form_is_not_a_certificate(self, service, context, sick_leave):
        # Иначе больничный, к которому не принесли ни одной бумаги,
        # выглядел бы подтверждённым: он сам себя и подтвердил бы.
        first = date.today()
        made = service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=first, last_day=first + timedelta(days=5),
        )

        view = service.request(context, made.request.id)
        assert view.documents == 0
        assert APPLICATION_DOCUMENT == "APPLICATION"


# --- через HTTP --------------------------------------------------------------


def test_application_over_http(bot_client, service, context, sick_leave):
    first = date.today()
    made = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=first, last_day=first + timedelta(days=1),
    )

    answer = bot_client.get(
        f"{ABSENCES}/{made.request.id}/application", **bot_headers(TG_ID)
    )

    assert answer.status_code == 200, answer.content
    assert answer["Content-Type"] == "application/pdf"
    # `inline`: человек сперва смотрит заявление на экране и печатает
    # уже оттуда.
    assert answer["Content-Disposition"].startswith("inline")
    assert answer.content.startswith(b"%PDF-")


def test_hr_gets_the_same_form(
    api_client, make_user, organization, service, context, sick_leave
):
    """Кадровик печатает бланк сам, не гоняя человека за телефоном."""
    first = date.today()
    made = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=first, last_day=first + timedelta(days=1),
    )
    user = make_user(
        organization,
        permissions=("absences.read", "absences.approve", "employees.read"),
    )
    api_client.force_authenticate(user=user)

    answer = api_client.get(
        f"/api/v1/absence-requests/{made.request.id}/application"
    )

    assert answer.status_code == 200, answer.content
    assert answer["Content-Type"] == "application/pdf"
    assert answer.content.startswith(b"%PDF-")


def test_hr_without_the_right_is_refused(
    api_client, make_user, organization, service, context, sick_leave
):
    first = date.today()
    made = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=first, last_day=first,
    )
    user = make_user(organization, permissions=("employees.read",))
    api_client.force_authenticate(user=user)

    answer = api_client.get(
        f"/api/v1/absence-requests/{made.request.id}/application"
    )

    assert answer.status_code == 403, answer.content


# --- сквозной путь больничного -----------------------------------------------


def test_sick_leave_end_to_end(
    bot_client, api_client, make_user, organization, service, context,
    sick_leave, hr
):
    """Заявка → заявление → справка → решение HR → табель."""
    first = date.today()
    last = first + timedelta(days=4)

    # 1. Сотрудник оформляет больничный.
    made = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=first, last_day=last, comment="Температура",
    )
    request_id = made.request.id
    assert made.request.status == "SUBMITTED"

    # 2. И сразу получает бланк для подписи.
    assert service.application(context, request_id).startswith(b"%PDF-")

    # 3. Пока справки нет, заявка её ждёт.
    assert service.request(context, request_id).documents == 0

    # 4. Справку приносят через Mini App.
    service.attach_document(
        context, request_id,
        SimpleUploadedFile("spravka.pdf", b"%PDF-1.4 fake", "application/pdf"),
    )
    assert service.request(context, request_id).documents == 1

    # 5. HR решает.
    service.decide(hr, request_id, approve=True, comment="Принято")

    request = AbsenceRequest.objects.get(id=request_id)
    assert request.status == "APPROVED"

    # 6. И только теперь дни попадают в учёт.
    absence = EmployeeAbsence.objects.filter(origin_request_id=request_id).first()
    assert absence is not None
    assert absence.status in {"PLANNED", "ACTIVE"}


def test_vacation_days_do_not_count_before_approval(
    service, context, annual_leave
):
    first = date.today() + timedelta(days=10)
    made = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=first, last_day=first + timedelta(days=4),
    )

    # Пока HR не решил, в табеле этих дней нет: иначе любой оформлял бы
    # себе отпуск сам.
    assert not EmployeeAbsence.objects.filter(
        origin_request_id=made.request.id
    ).exists()


def test_bot_can_attach_a_document(bot_client, service, context, sick_leave):
    """Справку приносят из чата тем же адресом, что и из Mini App.

    Второй путь загрузки означал бы вторую проверку типа и размера,
    которую однажды забудут обновить.
    """
    first = date.today()
    made = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=first, last_day=first + timedelta(days=4),
    )

    answer = bot_client.post(
        f"{ABSENCES}/{made.request.id}/document",
        {"document": SimpleUploadedFile(
            "spravka.jpg", bytes([0xFF, 0xD8, 0xFF]) + b" fake", "image/jpeg"
        )},
        format="multipart",
        **bot_headers(TG_ID),
    )

    assert answer.status_code == 201, answer.content
    assert service.request(context, made.request.id).documents == 1


# --- решение по справке ------------------------------------------------------


class TestDocumentDecision:
    @pytest.fixture()
    def with_document(self, service, context, sick_leave):
        first = date.today()
        made = service.create(
            context, absence_type_code="SICK_LEAVE",
            first_day=first, last_day=first + timedelta(days=4),
        )
        service.attach_document(
            context, made.request.id,
            SimpleUploadedFile("spravka.pdf", b"%PDF-1.4 x", "application/pdf"),
        )
        from humotech.absences.models import AbsenceDocument

        document = AbsenceDocument.objects.get(absence_request_id=made.request.id)
        return made.request, document

    def test_accepted_document_is_marked_and_the_person_is_told(
        self, service, hr, with_document
    ):
        from humotech.notifications.models import Notification

        request, document = with_document

        service.verify_document(hr, request.id, document.id, accept=True)

        document.refresh_from_db()
        assert document.verification_status == "VERIFIED"
        assert Notification.objects.filter(
            notification_type="absence.document_accepted"
        ).exists()

    def test_rejection_without_a_reason_is_refused(
        self, service, hr, with_document
    ):
        from humotech.core.errors import ValidationFailed

        request, document = with_document

        # Отклонение без объяснения — тупик: человек приносит ту же
        # бумагу второй раз и не понимает, почему её опять не берут.
        with pytest.raises(ValidationFailed):
            service.verify_document(hr, request.id, document.id, accept=False)

    def test_reason_reaches_the_person_word_for_word(
        self, service, hr, with_document
    ):
        from humotech.notifications.models import Notification

        request, document = with_document

        service.verify_document(
            hr, request.id, document.id, accept=False,
            comment="Фото нечитаемое, нужен скан",
        )

        document.refresh_from_db()
        assert document.verification_status == "REJECTED"
        note = Notification.objects.get(
            notification_type="absence.document_rejected"
        )
        # Пересказ своими словами однажды смягчит «нечитаемое фото» до
        # «нужен другой документ» — и придёт то же фото.
        assert "Фото нечитаемое, нужен скан" in note.body
        assert "загрузите корректный документ" in note.body

    def test_request_decision_is_not_touched(self, service, hr, with_document):
        request, document = with_document
        service.decide(hr, request.id, approve=True, comment="ок")

        service.verify_document(
            hr, request.id, document.id, accept=False, comment="Не тот бланк",
        )

        request.refresh_from_db()
        # Одобренный больничный с отклонённой справкой — законное
        # состояние: человек всё это время болеет, а не прогуливает.
        assert request.status == "APPROVED"

    def test_decision_over_http(
        self, api_client, make_user, organization, service, hr, with_document
    ):
        request, document = with_document
        user = make_user(
            organization,
            permissions=("absences.read", "absences.documents", "employees.read"),
        )
        api_client.force_authenticate(user=user)

        answer = api_client.post(
            f"/api/v1/absence-requests/{request.id}/documents/{document.id}/reject",
            {"comment": "Нужен оригинал"}, format="json",
        )

        assert answer.status_code == 200, answer.content
        assert answer.json()["verification_status"] == "REJECTED"

    def test_download_route_still_works(
        self, api_client, make_user, organization, service, hr, with_document
    ):
        # `download` стоит раньше маршрута с `<str:decision>`: иначе
        # скачивание разобралось бы как решение по документу.
        request, document = with_document
        user = make_user(
            organization,
            permissions=("absences.read", "absences.documents", "employees.read"),
        )
        api_client.force_authenticate(user=user)

        answer = api_client.get(
            f"/api/v1/absence-requests/{request.id}/documents/{document.id}/download"
        )

        assert answer.status_code == 200, answer.content
