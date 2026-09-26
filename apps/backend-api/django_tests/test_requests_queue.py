"""Очередь заявок HR: счётчики вкладок, место сотрудника, история и справка.

Три обещания, которые легко нарушить незаметно.

**Счётчики считает сервер по всем заявкам.** По одной строке на вкладку
видно только «есть или нет», и вкладка «Больничные 1» при семи
больничных была бы враньём.

**Справка открывается только тем, кто видит заявку.** Кадровик соседнего
офиса получает «не найдено», а не файл по угаданному адресу и не
«запрещено», по которому перебором считаются чужие заявки.

**История не раскрывает слова сотрудника.** Комментарий к шагу отдаётся
только у решений кадровика: у шагов сотрудника в нём бывает диагноз.
"""

from __future__ import annotations

import pytest

from django_tests.test_absences import (  # noqa: F401 — фикстуры
    annual_leave,
    balance,
    certificate,
    context,
    sick_leave,
    soon,
)
from humotech.absences.models import AbsenceDocument
from humotech.absences.services import AbsenceService

API = "/api/v1"

pytestmark = pytest.mark.django_db

QUEUE_READER = ("absences.read", "attendance.read", "employees.read")


@pytest.fixture()
def queue_client(api_client, make_user, organization):
    api_client.force_authenticate(user=make_user(organization, permissions=QUEUE_READER))
    return api_client


@pytest.fixture()
def sick_request(context, sick_leave):
    view = AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
        comment="Температура 38,5", document=certificate(),
    )
    return view.request


@pytest.fixture()
def leave_request(context, annual_leave, balance):
    view = AbsenceService().create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(20), last_day=soon(24),
    )
    return view.request


class TestCounts:
    def test_tabs_are_counted_by_the_server(
        self, queue_client, sick_request, leave_request
    ):
        answer = queue_client.get(f"{API}/requests/counts")

        assert answer.status_code == 200
        assert answer.json() == {
            "open": 2, "all": 2, "leave": 1, "sick": 1, "fixes": 0, "cancel": 0,
        }

    def test_filters_narrow_the_counts(
        self, queue_client, sick_request, leave_request, other_office
    ):
        """Счётчики живут по тем же фильтрам, что и список."""
        answer = queue_client.get(f"{API}/requests/counts?office_id={other_office.id}")

        assert answer.status_code == 200
        assert answer.json()["all"] == 0

    def test_without_one_of_the_rights_it_is_refused(
        self, api_client, make_user, organization, sick_request
    ):
        """Отказ, а не молча урезанные числа, читающиеся как «заявок нет»."""
        api_client.force_authenticate(
            user=make_user(organization, permissions=("absences.read", "employees.read"))
        )
        answer = api_client.get(f"{API}/requests/counts")

        assert answer.status_code == 403


class TestQueueRow:
    def test_row_names_the_office_and_department_of_today(
        self, queue_client, sick_request, office
    ):
        answer = queue_client.get(f"{API}/requests?kind=absence")

        assert answer.status_code == 200
        row = answer.json()["items"][0]
        assert row["place"]["office_name"] == office.name

    def test_history_does_not_repeat_the_employee_comment(
        self, queue_client, sick_request
    ):
        answer = queue_client.get(f"{API}/requests?kind=absence")

        history = answer.json()["items"][0]["absence"]["history"]
        assert history, "у поданной заявки есть хотя бы один шаг"
        assert all(step["comment"] is None for step in history)
        assert "38,5" not in str(history)

    def test_request_kind_filter_is_applied(self, queue_client, sick_request):
        answer = queue_client.get(f"{API}/requests?kind=absence&request_kind=CANCEL")

        assert answer.status_code == 200
        assert answer.json()["items"] == []


class TestDocument:
    def test_reader_of_the_queue_opens_the_certificate(
        self, queue_client, sick_request
    ):
        document = AbsenceDocument.objects.get(absence_request=sick_request)

        answer = queue_client.get(
            f"{API}/absence-requests/{sick_request.id}/documents/{document.id}/download"
        )

        assert answer.status_code == 200
        assert b"".join(answer.streaming_content).startswith(b"%PDF")

    def test_hr_of_another_office_gets_not_found(
        self, api_client, make_user, organization, other_office, sick_request
    ):
        api_client.force_authenticate(
            user=make_user(organization, permissions=QUEUE_READER, office=other_office)
        )
        document = AbsenceDocument.objects.get(absence_request=sick_request)

        answer = api_client.get(
            f"{API}/absence-requests/{sick_request.id}/documents/{document.id}/download"
        )

        assert answer.status_code == 404

    def test_document_of_another_request_is_not_found(
        self, queue_client, sick_request, leave_request
    ):
        """Пара «заявка — документ» проверяется: чужой документ по своей заявке не отдаётся."""
        document = AbsenceDocument.objects.get(absence_request=sick_request)

        answer = queue_client.get(
            f"{API}/absence-requests/{leave_request.id}/documents/{document.id}/download"
        )

        assert answer.status_code == 404
