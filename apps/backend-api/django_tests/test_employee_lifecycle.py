"""Стажировка → штат или расставание.

Проверяется не «поле поменялось», а то, из-за чего кадровый учёт
расходится с действительностью:

— уволенный, оставшийся в составе смены и в числе работающих;
— переход, о котором человек узнал от коллег, а не от бота;
— «принять в штат» у того, кто уже в штате, и у уволенного;
— должность, названная в поздравлении, — прежняя, а не новая;
— повторное нажатие, отправившее второе поздравление.

Трудовой статус и дневной здесь намеренно проверяются вместе: смешать
их — главная ошибка в этой области, и тест на «стажёр в офисе» стоит
рядом с тестом на «уволенного в составе нет».
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone as django_timezone

from django_tests.conftest import make_qr_point
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.core.errors import Conflict
from humotech.employees.lifecycle import (
    EMPLOYMENT_TITLES,
    PROBATION_FAILED,
    EmployeeLifecycleService,
    title_of,
)
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.notifications.models import Notification

pytestmark = pytest.mark.django_db

API = "/api/v1"


@pytest.fixture()
def service() -> EmployeeLifecycleService:
    return EmployeeLifecycleService()


@pytest.fixture()
def hr_actor(make_actor, organization):
    return make_actor(
        organization,
        permissions=("employees.read", "employees.manage", "employees.archive",
                     # Дневной слой читается тем же кадровиком: смысл
                     # проверки как раз в том, что трудовой статус и
                     # состав смены — разные ответы на разные вопросы.
                     "attendance.read", "offices.read"),
    )


@pytest.fixture()
def trainee(employee) -> Employee:
    employee.employment_status = "PROBATION"
    employee.save(update_fields=["employment_status"])
    return employee


def open_session(organization, employee, office):
    """Сессия собирается из события, как в бою: `entry_event_id` объявлен
    NOT NULL, и сессии «самой по себе» в базе не бывает."""
    entry = AttendanceEvent.objects.create(
        organization=organization,
        employee=employee,
        office=office,
        qr_point=make_qr_point(organization, office, code="LIFE-1"),
        event_type="ENTRY",
        source="QR",
        verification_status="ACCEPTED",
        occurred_at=django_timezone.now(),
    )
    return AttendanceSession.objects.create(
        organization=organization,
        employee=employee,
        office=office,
        entry_event=entry,
        started_at=entry.occurred_at,
        status="OPEN",
    )


def sent_to(employee) -> list[Notification]:
    return list(
        Notification.objects.filter(employee_id=employee.id).order_by("created_at")
    )


# --- названия статусов -------------------------------------------------------


class TestTitles:
    def test_active_is_shown_as_working_not_as_active(self):
        # «Активен» — это про учётную запись. Рядом со «Стажировкой» и
        # «Уволен» оно читается как слово из другого списка.
        assert title_of("ACTIVE") == "Работает"
        assert title_of("PROBATION") == "Стажировка"
        assert title_of("TERMINATED") == "Уволен"

    def test_unknown_code_is_returned_as_is(self):
        # Неизвестный код лучше показать как есть, чем подменить пустотой:
        # пустая ячейка выглядит как отсутствие данных, а не как новый
        # статус, о котором интерфейс ещё не знает.
        assert title_of("SOMETHING_NEW") == "SOMETHING_NEW"

    def test_every_status_of_the_database_has_a_title(self):
        from humotech.core.enums import EMPLOYMENT_STATUSES

        for code in EMPLOYMENT_STATUSES:
            assert code in EMPLOYMENT_TITLES, code


# --- приём в штат ------------------------------------------------------------


class TestPromotion:
    def test_trainee_becomes_staff(self, service, hr_actor, trainee):
        card = service.promote_to_staff(hr_actor, trainee.id)

        trainee.refresh_from_db()
        assert trainee.employment_status == "ACTIVE"
        assert card.employee.employment_status == "ACTIVE"

    def test_the_person_is_told_by_the_bot(self, service, hr_actor, trainee):
        service.promote_to_staff(hr_actor, trainee.id)

        notes = sent_to(trainee)
        assert [one.notification_type for one in notes] == ["employee.promoted"]
        assert "приняты в штат" in notes[0].body

    def test_second_click_does_not_send_a_second_message(
        self, service, hr_actor, trainee
    ):
        service.promote_to_staff(hr_actor, trainee.id)
        with pytest.raises(Conflict):
            service.promote_to_staff(hr_actor, trainee.id)

        assert len(sent_to(trainee)) == 1

    def test_new_position_is_named_in_the_message(
        self, service, hr_actor, organization, trainee
    ):
        from humotech.positions.models import Position

        position = Position.objects.create(
            organization=organization, code="LEAD", name="Ведущий инженер",
            status="ACTIVE",
        )

        service.promote_to_staff(hr_actor, trainee.id, position_id=position.id)

        # Должность меняется ДО смены статуса — иначе поздравление
        # назвало бы ту, с которой человек стажировался.
        assert "Ведущий инженер" in sent_to(trainee)[0].body
        current = (
            EmployeeAssignment.objects.filter(employee_id=trainee.id, is_primary=True)
            .order_by("-valid_from")
            .first()
        )
        assert current is not None and current.position_id == position.id

    def test_staff_member_is_not_promoted_twice(self, service, hr_actor, employee):
        # `employee` уже в штате: принимать в него второй раз нечего.
        with pytest.raises(Conflict):
            service.promote_to_staff(hr_actor, employee.id)

    def test_dismissed_person_is_not_promoted(self, service, hr_actor, trainee):
        service.end_probation(hr_actor, trainee.id)

        with pytest.raises(Conflict):
            service.promote_to_staff(hr_actor, trainee.id)


# --- расставание -------------------------------------------------------------


class TestEndOfProbation:
    def test_person_is_dismissed_with_the_reason_in_the_card(
        self, service, hr_actor, trainee
    ):
        card = service.end_probation(hr_actor, trainee.id)

        trainee.refresh_from_db()
        assert trainee.employment_status == "TERMINATED"
        # Причина живёт в карточке, а не только в журнале аудита: её
        # спрашивают через год, а журнал читают при разборе спора.
        assert trainee.termination_reason == PROBATION_FAILED
        assert card.employee.termination_reason == PROBATION_FAILED

    def test_message_is_neutral(self, service, hr_actor, trainee):
        service.end_probation(hr_actor, trainee.id)

        body = sent_to(trainee)[0].body
        assert "не продолжаем сотрудничество" in body
        # Ни оценок, ни объяснений, ни сожалений: решение уже принято,
        # а разговор о нём — это разговор с человеком, не сообщение бота.
        for rough in ("к сожалению", "не справились", "не подошли", "увы"):
            assert rough not in body.lower()

    def test_assignments_are_closed_by_the_last_day(
        self, service, hr_actor, trainee
    ):
        day = date.today()
        service.end_probation(hr_actor, trainee.id, last_day=day)

        open_rows = EmployeeAssignment.objects.filter(
            employee_id=trainee.id, valid_to__isnull=True
        )
        # Иначе уволенный навсегда остался бы «работающим сейчас» в любом
        # отчёте по текущему составу.
        assert not open_rows.exists()

    def test_custom_reason_is_kept(self, service, hr_actor, trainee):
        service.end_probation(hr_actor, trainee.id, reason="По соглашению сторон")

        trainee.refresh_from_db()
        assert trainee.termination_reason == "По соглашению сторон"

    def test_staff_member_does_not_end_probation(self, service, hr_actor, employee):
        with pytest.raises(Conflict):
            service.end_probation(hr_actor, employee.id)


# --- два слоя статусов -------------------------------------------------------


class TestTwoLayers:
    def test_trainee_is_part_of_the_shift(
        self, hr_actor, organization, trainee, office
    ):
        # Трудовой статус «стажировка» ничего не говорит о том, где
        # человек сегодня. Он ходит в тот же офис и отмечается так же.
        open_session(organization, trainee, office)

        report = AttendanceHrService().presence(hr_actor, day=date.today())

        assert [row.state for row in report.rows] == ["IN_OFFICE"]

    def test_dismissed_person_leaves_the_shift(
        self, service, hr_actor, organization, trainee, office
    ):
        open_session(organization, trainee, office)
        service.end_probation(hr_actor, trainee.id,
                              last_day=date.today() - timedelta(days=1))

        report = AttendanceHrService().presence(hr_actor, day=date.today())

        # Ни в составе смены, ни в числе работающих: назначение закрыто
        # вчерашним днём, и сегодня человека в компании нет.
        assert report.rows == []


# --- через HTTP --------------------------------------------------------------


class TestHttp:
    @pytest.fixture()
    def client(self, api_client, make_user, organization):
        user = make_user(
            organization,
            permissions=("employees.read", "employees.manage",
                         "employees.archive"),
        )
        api_client.force_authenticate(user=user)
        return api_client

    def test_promote_over_http_returns_the_card(self, client, trainee):
        answer = client.post(f"{API}/employees/{trainee.id}/promote/", {},
                             format="json")

        assert answer.status_code == 200, answer.content
        body = answer.json()
        assert body["employment_status"] == "ACTIVE"
        # Подпись считает сервер: собирать её заново в каждом клиенте
        # значит однажды получить три разных слова.
        assert body["employment_status_title"] == "Работает"

    def test_end_probation_over_http(self, client, trainee):
        answer = client.post(f"{API}/employees/{trainee.id}/end-probation/", {},
                             format="json")

        assert answer.status_code == 200, answer.content
        body = answer.json()
        assert body["employment_status"] == "TERMINATED"
        assert body["employment_status_title"] == "Уволен"
        assert body["termination_reason"] == PROBATION_FAILED

    def test_promoting_a_staff_member_is_refused(self, client, employee):
        answer = client.post(f"{API}/employees/{employee.id}/promote/", {},
                             format="json")

        assert answer.status_code == 409, answer.content
