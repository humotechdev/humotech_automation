"""Безопасность заявок на отсутствие: переходы состояния, границы, ввод.

Главная мысль: больничный не становится `APPROVED` и не попадает в табель,
пока кадровик не принял справку, не отметил заявление и не проставил
период, — и ни один путь в API этого не обходит: ни чужой офис, ни
незнакомое слово в адресе решения, ни второе решение, пришедшее
одновременно с первым.

Гонки проверяются симуляцией перекрытия: конкурирующее действие
вклинивается в момент, когда первое уже прочитало заявку, но ещё не
записало решение (подмена `lock_employee`). В одной транзакции теста
настоящей параллельности нет, но порядок «прочитал — другой записал —
записал поверх» воспроизводится точно.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from humotech.absences import services as absence_services
from humotech.absences.application_pdf import Application, build
from humotech.absences.models import (
    AbsenceRequest,
    AbsenceType,
    EmployeeAbsence,
    LeaveBalance,
)
from humotech.absences.services import MINUTES_PER_WORKING_DAY, AbsenceService
from humotech.core.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)

from .conftest import bot_headers, create_actor
from .test_absences import (  # noqa: F401 — фикстуры набора
    ABSENCE_HR,
    ABSENCES,
    TG_ID,
    annual_leave,
    balance,
    certificate,
    context,
    hr,
    latest_certificate,
    private_files,
    service,
    sick_leave,
    soon,
)

pytestmark = pytest.mark.django_db

API = "/api/v1"


# --------------------------------------------------------------- помощники


@pytest.fixture()
def day_off(db, organization) -> AbsenceType:
    return AbsenceType.objects.create(
        organization=organization, code="DAY_OFF", name="Отгул",
        requires_approval=True,
    )


@pytest.fixture()
def office_hr(make_actor, organization, other_office):
    """Кадровик ЧУЖОГО офиса той же организации."""
    return make_actor(organization, permissions=ABSENCE_HR, office=other_office)


def sick_ready(service, context, hr, *, days=(-3, -1)):
    """Больничный, у которого кадровик сделал все три шага."""
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(days[0]), last_day=soon(days[1]),
    )
    request = view.request
    service.attach_document(context, request.id, certificate())
    service.verify_document(
        hr, request.id, latest_certificate(request.id).id, accept=True,
    )
    service.mark_application_received(hr, request.id)
    service.set_period(hr, request.id, first_day=soon(days[0]), last_day=soon(days[1]))
    return request


def live_absences(request):
    return EmployeeAbsence.objects.filter(
        origin_request=request, status__in=("PLANNED", "ACTIVE", "COMPLETED"),
    )


@pytest.fixture()
def interleave(monkeypatch):
    """Вклинить действие в момент первого `lock_employee`.

    Первое действие к этому моменту уже прочитало заявку, но ещё ничего
    не записало. Конкурент выполняется ровно там — как второй запрос,
    пришедший между «посмотрел» и «записал».
    """
    original = absence_services.lock_employee

    def arm(competitor):
        state = {"fired": False}

        def patched(employee_id):
            if not state["fired"]:
                state["fired"] = True
                competitor()
            return original(employee_id)

        monkeypatch.setattr(absence_services, "lock_employee", patched)
        return state

    return arm


def hr_client(api_client, organization, **kwargs):
    user, _ = create_actor(organization, permissions=ABSENCE_HR, **kwargs)
    api_client.force_authenticate(user=user)
    return api_client


# ------------------------------------------- область видимости кадровика


class TestOfficeScope:
    """Кадровик чужого офиса не решает чужие заявки.

    Раньше `decide`, `set_period`, `mark_application_received` и
    `cancel_approved` проверяли только организацию: кадровик офиса Б
    подтверждал больничный сотрудника офиса А, которого даже не видит.
    """

    def test_other_office_cannot_approve(self, service, context, hr, office_hr, sick_leave):
        request = sick_ready(service, context, hr)

        with pytest.raises(NotFound):
            service.decide(office_hr, request.id, approve=True)
        request.refresh_from_db()
        assert request.status == "SUBMITTED"
        assert not live_absences(request).exists()

    def test_other_office_cannot_reject(self, service, context, office_hr, day_off):
        view = service.create(
            context, absence_type_code="DAY_OFF", first_day=soon(5), last_day=soon(5),
        )
        with pytest.raises(NotFound):
            service.decide(office_hr, view.request.id, approve=False)

    def test_other_office_cannot_complete_the_paperwork(
        self, service, context, office_hr, sick_leave
    ):
        view = service.create(context, absence_type_code="SICK_LEAVE")
        with pytest.raises(NotFound):
            service.set_period(
                office_hr, view.request.id, first_day=soon(-2), last_day=soon(-1),
            )
        with pytest.raises(NotFound):
            service.mark_application_received(office_hr, view.request.id)

    def test_other_office_cannot_cancel_approved(
        self, service, context, hr, office_hr, sick_leave
    ):
        request = sick_ready(service, context, hr)
        service.decide(hr, request.id, approve=True)

        with pytest.raises(NotFound):
            service.cancel_approved(office_hr, request.id)
        assert live_absences(request).exists()

    def test_pending_list_hides_other_office(
        self, api_client, organization, other_office, service, context, sick_leave
    ):
        service.create(context, absence_type_code="SICK_LEAVE", comment="диагноз")
        client = hr_client(api_client, organization, office=other_office)

        answer = client.get(f"{API}/absence-requests/pending")

        assert answer.status_code == 200
        assert answer.json()["requests"] == []

    def test_other_office_http_approve_is_not_found(
        self, api_client, organization, other_office, service, context, hr, sick_leave
    ):
        request = sick_ready(service, context, hr)
        client = hr_client(api_client, organization, office=other_office)

        answer = client.post(f"{API}/absence-requests/{request.id}/approve", {}, format="json")

        assert answer.status_code == 404
        request.refresh_from_db()
        assert request.status == "SUBMITTED"

    def test_foreign_organization_cannot_approve(
        self, service, context, hr, foreign_actor, sick_leave
    ):
        """Атака не удалась и раньше: организация проверялась всегда."""
        request = sick_ready(service, context, hr)
        with pytest.raises((PermissionDenied, NotFound)):
            service.decide(foreign_actor, request.id, approve=True)


# ------------------------------------------------------- слово решения


class TestDecisionWord:
    @pytest.mark.parametrize("word", ["APPROVE", "approved", "yes", "accept"])
    def test_unknown_word_does_not_reject(
        self, api_client, organization, service, context, hr, sick_leave, word
    ):
        """Раньше всё, что не `cancel`, шло в `decide(approve=word=="approve")`:
        опечатка в адресе молча отклоняла заявку."""
        request = sick_ready(service, context, hr)
        client = hr_client(api_client, organization)

        answer = client.post(f"{API}/absence-requests/{request.id}/{word}", {}, format="json")

        assert answer.status_code == 400
        request.refresh_from_db()
        assert request.status == "SUBMITTED"


# ------------------------------------------------ замок больничного


class TestSickLeaveLock:
    def test_approve_without_paperwork_is_refused(self, service, context, hr, sick_leave):
        view = service.create(
            context, absence_type_code="SICK_LEAVE", first_day=soon(-2), last_day=soon(-1),
        )
        with pytest.raises(Conflict) as failure:
            service.decide(hr, view.request.id, approve=True)
        assert set(failure.value.details["missing"]) == {"certificate", "application", "period"}
        assert not live_absences(view.request).exists()

    def test_http_approve_without_paperwork_is_refused(
        self, api_client, organization, service, context, sick_leave
    ):
        view = service.create(context, absence_type_code="SICK_LEAVE")
        client = hr_client(api_client, organization)

        answer = client.post(
            f"{API}/absence-requests/{view.request.id}/approve",
            {"override_marks": True, "comment": "x", "status": "APPROVED"},
            format="json",
        )

        assert answer.status_code == 409
        assert not live_absences(view.request).exists()

    def test_double_approve_is_refused(self, service, context, hr, sick_leave):
        request = sick_ready(service, context, hr)
        service.decide(hr, request.id, approve=True)
        with pytest.raises(Conflict):
            service.decide(hr, request.id, approve=True)
        assert live_absences(request).count() == 1

    def test_reject_then_approve_is_refused(self, service, context, hr, sick_leave):
        request = sick_ready(service, context, hr)
        service.decide(hr, request.id, approve=False, comment="нет")
        with pytest.raises(Conflict):
            service.decide(hr, request.id, approve=True)
        assert not live_absences(request).exists()

    def test_period_cannot_change_after_approval(self, service, context, hr, sick_leave):
        request = sick_ready(service, context, hr)
        service.decide(hr, request.id, approve=True)
        with pytest.raises(Conflict):
            service.set_period(hr, request.id, first_day=soon(-30), last_day=soon(-1))

    def test_application_mark_cannot_change_after_decision(
        self, service, context, hr, sick_leave
    ):
        request = sick_ready(service, context, hr)
        service.decide(hr, request.id, approve=True)
        with pytest.raises(Conflict):
            service.mark_application_received(hr, request.id, received=False)
        request.refresh_from_db()
        assert request.application_received_at is not None

    def test_employee_cannot_cancel_approved_by_default(
        self, service, context, hr, sick_leave
    ):
        request = sick_ready(service, context, hr)
        service.decide(hr, request.id, approve=True)
        with pytest.raises(PermissionDenied):
            service.cancel(context, request.id)
        assert live_absences(request).exists()

    def test_me_endpoint_ignores_status_fields(
        self, bot_client, context, sick_leave, organization
    ):
        answer = bot_client.post(
            ABSENCES,
            {
                "absence_type_code": "SICK_LEAVE",
                "status": "APPROVED", "approved": True, "stage": "APPROVED",
                "application_received_at": "2026-01-01T00:00:00Z",
                "organization_id": "00000000-0000-0000-0000-000000000000",
            },
            format="json",
            **bot_headers(TG_ID),
        )
        assert answer.status_code == 201
        row = AbsenceRequest.objects.get(id=answer.json()["id"])
        assert row.status == "SUBMITTED"
        assert row.application_received_at is None
        assert row.organization_id == organization.id
        assert not live_absences(row).exists()

    def test_me_patch_is_not_allowed(self, bot_client, context, sick_leave):
        view = AbsenceService().create(context, absence_type_code="SICK_LEAVE")
        answer = bot_client.patch(
            f"{ABSENCES}/{view.request.id}", {"status": "APPROVED"}, format="json",
            **bot_headers(TG_ID),
        )
        assert answer.status_code == 405
        view.request.refresh_from_db()
        assert view.request.status == "SUBMITTED"


# ----------------------------------------------------------- гонки


class TestRaces:
    """Второе решение, пришедшее между «прочитал» и «записал» первого."""

    def test_reject_during_approve(self, service, context, hr, make_actor, organization,
                                   sick_leave, interleave):
        request = sick_ready(service, context, hr)
        second = make_actor(organization, permissions=ABSENCE_HR)
        interleave(lambda: AbsenceService().decide(second, request.id, approve=False,
                                                   comment="отказ"))

        with pytest.raises(Conflict):
            service.decide(hr, request.id, approve=True)

        request.refresh_from_db()
        assert request.status != "APPROVED"
        assert not live_absences(request).exists()

    def test_certificate_revoked_during_approve(
        self, service, context, hr, make_actor, organization, sick_leave, interleave
    ):
        request = sick_ready(service, context, hr)
        second = make_actor(organization, permissions=ABSENCE_HR)
        paper = latest_certificate(request.id)
        interleave(lambda: AbsenceService().verify_document(
            second, request.id, paper.id, accept=False, comment="нечитаемо",
        ))

        with pytest.raises(Conflict) as failure:
            service.decide(hr, request.id, approve=True)
        assert "certificate" in failure.value.details.get("missing", [])
        assert not live_absences(request).exists()

    def test_application_unmarked_during_approve(
        self, service, context, hr, make_actor, organization, sick_leave, interleave
    ):
        request = sick_ready(service, context, hr)
        second = make_actor(organization, permissions=ABSENCE_HR)
        interleave(lambda: AbsenceService().mark_application_received(
            second, request.id, received=False,
        ))

        with pytest.raises(Conflict) as failure:
            service.decide(hr, request.id, approve=True)
        assert "application" in failure.value.details.get("missing", [])
        assert not live_absences(request).exists()

    def test_employee_cancel_during_approve(
        self, service, context, hr, annual_leave, balance, interleave
    ):
        """Сотрудник отменил отпуск, пока кадровик его подтверждал.

        Раньше подтверждение записывалось поверх отмены: заявка
        `APPROVED`, отсутствие в табеле, а резерв уже возвращён — и
        списание уходило в `used` мимо него."""
        view = service.create(
            context, absence_type_code="ANNUAL_LEAVE", first_day=soon(30), last_day=soon(34),
        )
        interleave(lambda: AbsenceService().cancel(context, view.request.id))

        with pytest.raises(Conflict):
            service.decide(hr, view.request.id, approve=True)

        assert not live_absences(view.request).exists()


# ------------------------------------------------------ сам себе кадровик


class TestSelfService:
    @pytest.fixture()
    def hr_is_employee(self, organization, employee):
        user, actor = create_actor(organization, permissions=ABSENCE_HR)
        user.employee = employee
        user.save(update_fields=["employee"])
        return actor

    def test_hr_cannot_approve_own(self, service, context, hr, hr_is_employee, sick_leave):
        request = sick_ready(service, context, hr)
        with pytest.raises(PermissionDenied):
            service.decide(hr_is_employee, request.id, approve=True)

    def test_hr_cannot_accept_own_certificate(
        self, service, context, hr_is_employee, sick_leave
    ):
        view = service.create(context, absence_type_code="SICK_LEAVE")
        service.attach_document(context, view.request.id, certificate())
        with pytest.raises(PermissionDenied):
            service.verify_document(
                hr_is_employee, view.request.id,
                latest_certificate(view.request.id).id, accept=True,
            )

    def test_hr_cannot_mark_own_application(
        self, service, context, hr_is_employee, sick_leave
    ):
        view = service.create(context, absence_type_code="SICK_LEAVE")
        with pytest.raises(PermissionDenied):
            service.mark_application_received(hr_is_employee, view.request.id)

    def test_hr_cannot_set_own_period(self, service, context, hr_is_employee, sick_leave):
        view = service.create(context, absence_type_code="SICK_LEAVE")
        with pytest.raises(PermissionDenied):
            service.set_period(
                hr_is_employee, view.request.id, first_day=soon(-2), last_day=soon(-1),
            )


# --------------------------------------------------- заявка на отмену


def test_approving_a_cancel_request_cancels_the_parent(
    service, context, hr, organization, employee, annual_leave, balance
):
    """`CANCEL` — просьба снять отпуск, а не новый отпуск.

    Раньше её подтверждение шло общим путём и пыталось записать в табель
    ВТОРОЕ отсутствие на те же дни.
    """
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE", first_day=soon(30), last_day=soon(34),
    )
    parent = service.decide(hr, view.request.id, approve=True)
    used = LeaveBalance.objects.get(id=balance.id).used_minutes
    assert used > 0

    child = AbsenceRequest.objects.create(
        organization=organization, employee=employee, absence_type=annual_leave,
        request_kind="CANCEL", parent_request=parent, status="SUBMITTED",
        requested_start_at=parent.requested_start_at,
        requested_end_at=parent.requested_end_at,
    )

    service.decide(hr, child.id, approve=True)

    parent.refresh_from_db()
    child.refresh_from_db()
    assert child.status == "APPROVED"
    assert parent.status == "CANCELLED"
    assert not EmployeeAbsence.objects.filter(
        employee=employee, status__in=("PLANNED", "ACTIVE"),
    ).exists()
    assert not EmployeeAbsence.objects.filter(origin_request=child).exists()
    assert LeaveBalance.objects.get(id=balance.id).used_minutes == 0


def test_rejecting_a_cancel_request_keeps_other_reserves(
    service, context, hr, organization, employee, annual_leave, balance
):
    """Отклонённая просьба об отмене не «возвращает» дни, которых не брала.

    Раньше `_release` вычитал её дни из резерва — и освобождал резерв
    другой, ещё не рассмотренной заявки на отпуск.
    """
    first = service.create(
        context, absence_type_code="ANNUAL_LEAVE", first_day=soon(30), last_day=soon(34),
    )
    parent = service.decide(hr, first.request.id, approve=True)
    service.create(
        context, absence_type_code="ANNUAL_LEAVE", first_day=soon(60), last_day=soon(64),
    )
    reserved = LeaveBalance.objects.get(id=balance.id).reserved_minutes
    assert reserved > 0

    child = AbsenceRequest.objects.create(
        organization=organization, employee=employee, absence_type=annual_leave,
        request_kind="CANCEL", parent_request=parent, status="SUBMITTED",
        requested_start_at=parent.requested_start_at,
        requested_end_at=parent.requested_end_at,
    )
    service.decide(hr, child.id, approve=False, comment="нет")

    assert LeaveBalance.objects.get(id=balance.id).reserved_minutes == reserved


# ---------------------------------------------------------- даты и числа


class TestDates:
    @pytest.mark.parametrize("day", ["9999-12-31", "0001-01-01", "1800-01-01"])
    def test_absurd_dates_are_400(self, bot_client, context, day_off, day):
        answer = bot_client.post(
            ABSENCES,
            {"absence_type_code": "DAY_OFF", "first_day": day, "last_day": day},
            format="json", **bot_headers(TG_ID),
        )
        assert answer.status_code == 400

    def test_crooked_date_is_400(self, bot_client, context, day_off):
        answer = bot_client.post(
            ABSENCES,
            {"absence_type_code": "DAY_OFF", "first_day": "2026-02-30",
             "last_day": "2026-13-01"},
            format="json", **bot_headers(TG_ID),
        )
        assert answer.status_code == 400

    def test_huge_range_is_refused(self, service, context, day_off):
        with pytest.raises(ValidationFailed):
            service.create(
                context, absence_type_code="DAY_OFF",
                first_day=soon(1), last_day=soon(1) + timedelta(days=5000),
            )

    @pytest.mark.parametrize("extra", [None, 3 * 365])
    def test_extension_is_bounded(self, bot_client, service, context, hr, day_off, extra):
        """Продление не проверяло ни длину, ни год: 9999-12-31 давал 500,
        а десятилетия — долгий обход дней."""
        view = service.create(
            context, absence_type_code="DAY_OFF", first_day=soon(2), last_day=soon(3),
        )
        service.decide(hr, view.request.id, approve=True)
        last = "9999-12-31" if extra is None else (soon(3) + timedelta(days=extra)).isoformat()

        answer = bot_client.post(
            f"{ABSENCES}/{view.request.id}/extend", {"new_last_day": last},
            format="json", **bot_headers(TG_ID),
        )
        assert answer.status_code == 400

    def test_period_by_hr_rejects_absurd_dates(self, api_client, organization, service,
                                               context, sick_leave):
        view = service.create(context, absence_type_code="SICK_LEAVE")
        client = hr_client(api_client, organization)

        answer = client.post(
            f"{API}/absence-requests/{view.request.id}/period",
            {"first_day": "9999-12-31", "last_day": "9999-12-31"}, format="json",
        )
        assert answer.status_code == 400

    @pytest.mark.parametrize("query", ["limit=abc", "offset=x", "limit=1e9", "limit=-5&offset=-1"])
    def test_list_paging_garbage_is_not_500(self, bot_client, context, query):
        answer = bot_client.get(f"{ABSENCES}?{query}", **bot_headers(TG_ID))
        assert answer.status_code in (200, 400)

    @pytest.mark.parametrize("value", ["garbage", "2026-02-30", "'; DROP TABLE x;--"])
    def test_queue_date_filter_garbage_is_400(self, hr, value):
        with pytest.raises(ValidationFailed):
            list(AbsenceService().queue(hr, date_from=value))
        with pytest.raises(ValidationFailed):
            list(AbsenceService().queue(hr, date_to=value))

    def test_queue_search_is_parameterised(self, service, context, hr, sick_leave):
        service.create(context, absence_type_code="SICK_LEAVE")
        rows = AbsenceService().queue(hr, search="' OR 1=1 --")
        assert list(rows) == []


# ------------------------------------------------------------ комментарии


class TestComments:
    PAYLOAD = '<script>alert(1)</script><img src=x onerror=alert(2)>{{7*7}}${7*7}'

    def test_xss_is_stored_as_plain_text(self, bot_client, context, sick_leave):
        answer = bot_client.post(
            ABSENCES, {"absence_type_code": "SICK_LEAVE", "comment": self.PAYLOAD},
            format="json", **bot_headers(TG_ID),
        )
        assert answer.status_code == 201
        assert answer.json()["comment"] == self.PAYLOAD

    def test_comment_too_long_is_400(self, bot_client, context, sick_leave):
        answer = bot_client.post(
            ABSENCES, {"absence_type_code": "SICK_LEAVE", "comment": "я" * 2001},
            format="json", **bot_headers(TG_ID),
        )
        assert answer.status_code == 400

    def test_hr_comment_too_long_is_400(self, api_client, organization, service,
                                        context, day_off):
        view = service.create(
            context, absence_type_code="DAY_OFF", first_day=soon(2), last_day=soon(2),
        )
        client = hr_client(api_client, organization)
        answer = client.post(
            f"{API}/absence-requests/{view.request.id}/reject",
            {"comment": "x" * 2001}, format="json",
        )
        assert answer.status_code == 400

    def test_null_byte_is_400(self, bot_client, context, sick_leave):
        answer = bot_client.post(
            ABSENCES, {"absence_type_code": "SICK_LEAVE", "comment": "a\x00b"},
            format="json", **bot_headers(TG_ID),
        )
        assert answer.status_code == 400

    def test_lone_surrogate_is_400(self, bot_client, context, sick_leave):
        answer = bot_client.post(
            ABSENCES,
            b'{"absence_type_code": "SICK_LEAVE", "comment": "a\\ud800b"}',
            content_type="application/json", **bot_headers(TG_ID),
        )
        assert answer.status_code == 400


# ------------------------------------------------------------------ PDF


def _application(comment):
    return Application(
        organization="ООО (Тест) \\ ) Tj ET BT", employee_name="Иванов <b>И</b>",
        position=None, department=None, office=None,
        absence_name="Отгул", absence_code="DAY_OFF",
        first_day=date(2026, 10, 1), last_day=date(2026, 10, 2), days=2,
        comment=comment, number="ABCDEF12",
    )


def test_pdf_survives_pdf_syntax_in_text():
    pdf = build(_application(") Tj /JavaScript (app.alert(1)) >> endobj %%EOF ("))
    assert pdf.startswith(b"%PDF")
    assert pdf.rstrip().endswith(b"%%EOF")
    # Текст не вырвался в структуру документа: активного содержимого нет.
    assert b"/JavaScript" not in pdf
    assert b"/OpenAction" not in pdf


def test_long_comment_goes_to_the_next_page_instead_of_off_it():
    import re

    # 2000 знаков широких букв — предел поля комментария.
    pdf = build(_application("ШЩЖ " * 500))
    pages = len(re.findall(rb"/Type\s*/Page(?!s)", pdf))
    assert pages >= 2


# ----------------------------------------------------------- документы


class TestDocuments:
    def test_download_of_other_office_is_not_found(
        self, api_client, organization, other_office, service, context, sick_leave
    ):
        view = service.create(context, absence_type_code="SICK_LEAVE", document=certificate())
        paper = latest_certificate(view.request.id)
        client = hr_client(api_client, organization, office=other_office)

        answer = client.get(
            f"{API}/absence-requests/{view.request.id}/documents/{paper.id}/download"
        )
        assert answer.status_code == 404

    def test_download_has_nosniff(self, api_client, organization, service, context,
                                  sick_leave):
        view = service.create(context, absence_type_code="SICK_LEAVE", document=certificate())
        paper = latest_certificate(view.request.id)
        client = hr_client(api_client, organization)

        answer = client.get(
            f"{API}/absence-requests/{view.request.id}/documents/{paper.id}/download"
        )
        assert answer.status_code == 200
        assert answer["X-Content-Type-Options"] == "nosniff"

    def test_employee_cannot_download_someone_elses(
        self, bot_client, organization, office, context, sick_leave
    ):
        from humotech.employees.models import Employee, EmployeeAssignment

        other = Employee.objects.create(
            organization=organization, employee_number="EMP-0777",
            first_name="Пётр", last_name="Петров",
            hire_date=date(2024, 1, 1), employment_status="ACTIVE",
        )
        EmployeeAssignment.objects.create(
            organization=organization, employee=other, office=office,
            employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
            valid_from=date(2024, 1, 1),
        )
        foreign = AbsenceRequest.objects.create(
            organization=organization, employee=other, absence_type=sick_leave,
            request_kind="CREATE", status="SUBMITTED",
        )

        for path in ("document", "application", ""):
            url = f"{ABSENCES}/{foreign.id}/{path}".rstrip("/")
            answer = bot_client.get(url, **bot_headers(TG_ID))
            assert answer.status_code == 404, path
        answer = bot_client.delete(f"{ABSENCES}/{foreign.id}", **bot_headers(TG_ID))
        assert answer.status_code == 404
        foreign.refresh_from_db()
        assert foreign.status == "SUBMITTED"


def test_balance_is_not_double_released_by_sequential_cancel(
    service, context, hr, annual_leave, balance
):
    view = service.create(
        context, absence_type_code="ANNUAL_LEAVE", first_day=soon(30), last_day=soon(34),
    )
    service.decide(hr, view.request.id, approve=True)
    service.cancel_approved(hr, view.request.id)
    with pytest.raises(Conflict):
        service.cancel_approved(hr, view.request.id)
    row = LeaveBalance.objects.get(id=balance.id)
    assert row.used_minutes == 0 and row.reserved_minutes == 0
    assert row.allocated_minutes == 28 * MINUTES_PER_WORKING_DAY
