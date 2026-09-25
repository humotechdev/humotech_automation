"""Ознакомления глазами кадровика: сроки, «требуют внимания», разделы.

Проверяется то, из-за чего экран начал бы врать:

— срок, придуманный тем, у кого его не было;
— «требуют внимания» на чипе и в списке, посчитанные по-разному;
— отбор по группе, теряющий людей на границе страницы;
— черновик, «назначенный» всей компании;
— «Напомнить всем», молча проглотившее отказы;
— раздел, удалённый вместе с материалами.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone

from django_tests.test_onboarding import (
    API,
    accept_policies,
    content,  # noqa: F401
    hr,  # noqa: F401
    hr_actor_full,  # noqa: F401
    link_employee,
    walk_sections,
)
from humotech.departments.models import Department
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.onboarding.models import EmployeeOnboarding, PolicyDocument
from humotech.onboarding.services import DUE_DAYS, OnboardingService

pytestmark = pytest.mark.django_db


def person(organization, office, number: str, department=None) -> Employee:
    one = Employee.objects.create(
        organization=organization, employee_number=number, first_name="Тест",
        last_name=number, hire_date=date(2024, 3, 1), employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=one, office=office, department=department,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=date(2024, 3, 1),
    )
    return one


class TestDeadline:
    def test_enrol_sets_the_default_deadline(self, hr_actor_full, employee, content):
        row = OnboardingService().enrol(hr_actor_full, employee.id)

        assert row.due_date == timezone.localdate() + timedelta(days=DUE_DAYS)

    def test_without_a_deadline_nobody_is_overdue(self, hr, hr_actor_full, employee, content):
        OnboardingService().enrol(hr_actor_full, employee.id)
        EmployeeOnboarding.objects.filter(employee=employee).update(due_date=None)

        row = hr.get(f"{API}/onboarding/progress").json()["items"][0]

        assert row["due_date"] is None
        assert row["overdue"] is False

    def test_past_deadline_is_overdue_and_needs_attention(self, hr, hr_actor_full, employee, content):
        OnboardingService().enrol(hr_actor_full, employee.id)
        moved = hr.post(
            f"{API}/employees/{employee.id}/onboarding/due",
            {"due_date": str(timezone.localdate() - timedelta(days=1))}, format="json",
        )
        assert moved.status_code == 200, moved.json()

        row = hr.get(f"{API}/onboarding/progress").json()["items"][0]

        assert row["overdue"] is True
        assert row["reasons"] == ["overdue"]
        assert row["group"] == "attention"


class TestGroups:
    def test_chip_count_matches_the_rows_under_it(
        self, hr, hr_actor_full, organization, office, employee, content, api_client, telegram_settings,
    ):
        late = person(organization, office, "LATE")
        fresh = person(organization, office, "FRESH")
        service = OnboardingService()
        for one in (employee, late, fresh):
            service.enrol(hr_actor_full, one.id)
        EmployeeOnboarding.objects.filter(employee=late).update(due_date=timezone.localdate() - timedelta(days=2))
        link_employee(employee)
        accept_policies(api_client, walk_sections(api_client, employee))

        counts = hr.get(f"{API}/onboarding/counts").json()["groups"]
        assert counts == {"all": 3, "done": 1, "attention": 1, "waiting": 1,
                          "not_started": 0, "in_progress": 0, "overdue": 1}
        attention = hr.get(f"{API}/onboarding/progress?group=attention").json()
        assert [one["employee_number"] for one in attention["items"]] == ["LATE"]
        # Без Telegram и без начала — ждут подключения, а не «не начали».
        waiting = hr.get(f"{API}/onboarding/progress?group=waiting").json()
        assert [one["employee_number"] for one in waiting["items"]] == ["FRESH"]

    def test_group_filter_pages_without_losing_people(self, hr, hr_actor_full, organization, office, content):
        service = OnboardingService()
        for n in range(5):
            service.enrol(hr_actor_full, person(organization, office, f"P{n}").id)

        first = hr.get(f"{API}/onboarding/progress?group=waiting&limit=2").json()
        second = hr.get(f"{API}/onboarding/progress?group=waiting&limit=2&cursor={first['next_cursor']}").json()
        third = hr.get(f"{API}/onboarding/progress?group=waiting&limit=2&cursor={second['next_cursor']}").json()

        seen = [one["employee_number"] for page in (first, second, third) for one in page["items"]]
        assert sorted(seen) == [f"P{n}" for n in range(5)]
        assert third["has_more"] is False

    def test_department_filter_and_search_by_department(self, hr, hr_actor_full, organization, office, employee, content):
        sales = Department.objects.create(organization=organization, code="S", name="Продажи", status="ACTIVE")
        seller = person(organization, office, "SELL", department=sales)
        service = OnboardingService()
        service.enrol(hr_actor_full, employee.id)
        service.enrol(hr_actor_full, seller.id)

        by_filter = hr.get(f"{API}/onboarding/progress?department_id={sales.id}").json()["items"]
        by_search = hr.get(f"{API}/onboarding/progress?search=Продаж").json()["items"]

        assert [one["employee_number"] for one in by_filter] == ["SELL"]
        assert [one["employee_number"] for one in by_search] == ["SELL"]


class TestRenewal:
    def test_new_version_is_named_per_material(
        self, hr, hr_actor_full, employee, content, api_client, telegram_settings,
    ):
        OnboardingService().enrol(hr_actor_full, employee.id)
        link_employee(employee)
        accept_policies(api_client, walk_sections(api_client, employee))
        document = PolicyDocument.objects.get(organization_id=employee.organization_id, code="LABOUR_RULES")
        made = hr.post(f"{API}/onboarding/documents/{document.id}/versions/",
                       {"version": "2.0", "summary": "Новая"}, format="json").json()
        hr.post(f"{API}/onboarding/versions/{made['id']}/publish")

        row = hr.get(f"{API}/onboarding/progress").json()["items"][0]
        states = {one["title"]: one["state"] for one in row["materials"]}

        assert states[document.title] == "renewal"
        assert "renewal" in row["reasons"]
        listed = next(one for one in hr.get(f"{API}/onboarding/documents/").json()["items"] if one["id"] == str(document.id))
        assert listed["renewal_pending"] == 1
        assert listed["confirmed"] == 0
        assert listed["assigned"] == 1


class TestDocuments:
    def test_draft_is_assigned_to_nobody(self, hr, hr_actor_full, employee, content):
        OnboardingService().enrol(hr_actor_full, employee.id)
        created = hr.post(f"{API}/onboarding/documents/",
                          {"code": "ETHICS", "title": "Кодекс этики"}, format="json").json()
        hr.post(f"{API}/onboarding/documents/{created['id']}/versions/",
                {"version": "1", "summary": "Черновик"}, format="json")

        listed = next(one for one in hr.get(f"{API}/onboarding/documents/").json()["items"] if one["code"] == "ETHICS")

        assert listed["assigned"] == 0
        assert listed["confirmed"] == 0
        # Кто менял — из журнала, настоящий автор.
        assert listed["changed_by"] is not None

    def test_optional_material_is_assigned_to_nobody(self, hr, hr_actor_full, employee, content):
        OnboardingService().enrol(hr_actor_full, employee.id)
        document = PolicyDocument.objects.filter(organization_id=employee.organization_id).first()
        hr.patch(f"{API}/onboarding/documents/{document.id}/", {"is_mandatory": False}, format="json")

        listed = next(one for one in hr.get(f"{API}/onboarding/documents/").json()["items"] if one["id"] == str(document.id))
        row = hr.get(f"{API}/onboarding/progress").json()["items"][0]

        assert listed["assigned"] == 0
        assert document.title not in [one["title"] for one in row["materials"]]

    def test_nearest_due_among_those_who_still_owe(self, hr, hr_actor_full, organization, office, employee, content):
        service = OnboardingService()
        service.enrol(hr_actor_full, employee.id)
        other = person(organization, office, "SOON")
        service.enrol(hr_actor_full, other.id)
        soon = timezone.localdate() + timedelta(days=4)
        EmployeeOnboarding.objects.filter(employee=other).update(due_date=soon)

        listed = hr.get(f"{API}/onboarding/documents/").json()["items"]

        assert {one["nearest_due"] for one in listed if one["assigned"]} == {str(soon)}


class TestCategories:
    def test_category_groups_materials_and_refuses_to_vanish_with_them(
        self, hr, organization, office, employee, content,
    ):
        made = hr.post(f"{API}/onboarding/categories/",
                       {"title": "Охрана труда", "description": "Безопасная работа",
                        "owner_employee_id": str(employee.id)}, format="json")
        assert made.status_code == 201, made.json()
        category = made.json()
        document = PolicyDocument.objects.filter(organization_id=organization.id).first()
        moved = hr.patch(f"{API}/onboarding/documents/{document.id}/", {"category_id": category["id"]}, format="json")
        assert moved.json()["category"] == {"id": category["id"], "title": "Охрана труда"}

        listed = hr.get(f"{API}/onboarding/categories/").json()["items"][0]
        assert listed["documents_count"] == 1
        assert listed["documents"][0]["title"] == document.title
        assert listed["owner"]["id"] == str(employee.id)

        refused = hr.post(f"{API}/onboarding/categories/{category['id']}/archive/")
        assert refused.status_code == 409


class TestRemindAll:
    def test_each_person_gets_an_outcome(
        self, hr, hr_actor_full, organization, office, employee, content, telegram_settings,
    ):
        silent = person(organization, office, "NOTG")
        service = OnboardingService()
        service.enrol(hr_actor_full, employee.id)
        service.enrol(hr_actor_full, silent.id)
        link_employee(employee)

        first = hr.post(f"{API}/onboarding/remind", {"employee_ids": [str(employee.id), str(silent.id)]}, format="json")
        again = hr.post(f"{API}/onboarding/remind", {"employee_ids": [str(employee.id)]}, format="json")

        outcomes = {one["employee_id"]: one["outcome"] for one in first.json()["items"]}
        assert outcomes == {str(employee.id): "sent", str(silent.id): "no_telegram"}
        assert again.json()["items"][0]["outcome"] == "already_today"


def test_export_respects_the_filters(hr, hr_actor_full, organization, office, employee, content):
    service = OnboardingService()
    service.enrol(hr_actor_full, employee.id)
    service.enrol(hr_actor_full, person(organization, office, "OTHER").id)

    body = hr.get(f"{API}/onboarding/export?search=OTHER").json()

    assert body["total"] == 1
    assert body["items"][0]["due_date"] is not None
