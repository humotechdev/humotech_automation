"""Безопасность опросов и ознакомления: атаки и регрессии.

Каждый тест — воспроизведённая атака. До исправлений они падали
(протокол — в отчёте зоны surveys), после — держат границу:

— область офиса: VIEWER одного офиса видел ответы всей организации;
— анонимный опрос: порядок строк выгрузки выводился из id получателей,
  а малая группа раскрывала ответ одного человека;
— выгрузка CSV: формулы в тексте вопросов и ответов;
— автоматизации: сырой PATCH и кривая `scope` роняли очередь;
— ответы сотрудника: дубли вопроса, лишние варианты, чужие статусы;
— файл редакции документа: сохранялся до проверки права;
— ознакомление: мусор в параметрах списка давал 500.
"""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from django_tests.test_survey_automations import at, hire
from django_tests.test_surveys import QUESTIONS, link_telegram
from humotech.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.surveys import services
from humotech.surveys.automations import SurveyAutomationService, run_due
from humotech.surveys.models import (
    SurveyAnswer,
    SurveyAutomation,
    SurveyCampaign,
    SurveyRecipient,
)
from humotech.surveys.services import SurveyCampaignService, SurveyTemplateService

API = "/api/v1"
SURVEY_PERMS = ("surveys.read", "surveys.manage", "offices.read", "employees.read")

pytestmark = pytest.mark.django_db


# --- обстановка -------------------------------------------------------------


def person(organization, office, number, *, last_name=None, telegram=True):
    row = Employee.objects.create(
        organization=organization,
        employee_number=number,
        first_name="Тест",
        last_name=last_name or f"Фамилия{number}",
        hire_date=date(2024, 1, 10),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=row, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE",
        is_primary=True, valid_from=date(2024, 1, 10),
    )
    if telegram:
        link_telegram(organization, row)
    return row


@pytest.fixture()
def employee(employee, organization):
    """Общий сотрудник, но с Telegram: без привязки опрос до него не доходит."""
    link_telegram(organization, employee)
    return employee


@pytest.fixture()
def hr(make_actor, organization):
    return make_actor(organization, permissions=SURVEY_PERMS)


@pytest.fixture()
def templates():
    return SurveyTemplateService()


@pytest.fixture()
def campaigns():
    return SurveyCampaignService()


@pytest.fixture()
def template(templates, hr):
    return templates.create(hr, title="Пульс", questions=QUESTIONS)


def answer_all(template, recipient, employee_id, *, mark=4, option="Коллеги", text=None):
    rows = list(template.questions.order_by("position"))
    items = [
        {"question_id": str(rows[0].id), "number": mark},
        {"question_id": str(rows[1].id), "options": [option]},
    ]
    if text is not None:
        items.append({"question_id": str(rows[2].id), "text": text})
    return services.submit(
        employee_id=employee_id, recipient_id=recipient.id, answers=items,
    )


def recipient_of(campaign, employee):
    return SurveyRecipient.objects.get(campaign_id=campaign.id, employee_id=employee.id)


def client_for(user) -> APIClient:
    client = APIClient(raise_request_exception=False)
    client.force_authenticate(user=user)
    return client


# --- S1. область офиса ------------------------------------------------------


class TestOfficeScope:
    @pytest.fixture()
    def two_offices(self, organization, office, other_office, campaigns, hr, template):
        mine = person(organization, office, "S1-A", last_name="Свой")
        theirs = person(organization, other_office, "S1-B", last_name="Чужой")
        campaign = campaigns.create(
            hr, template_id=template.id, audience_kind="ALL", send_now=True,
        )
        answer_all(template, recipient_of(campaign, mine), mine.id, mark=5, text="мой")
        answer_all(template, recipient_of(campaign, theirs), theirs.id, mark=1,
                   text="секрет чужого офиса")
        return campaign, mine, theirs

    @pytest.fixture()
    def viewer(self, make_actor, organization, office):
        return make_actor(organization, permissions=("surveys.read",), office=office)

    def test_viewer_sees_only_people_of_own_office(
        self, campaigns, viewer, two_offices,
    ):
        campaign, mine, theirs = two_offices
        assert {r.employee_id for r in campaigns.recipients(viewer, campaign.id)} == {mine.id}
        assert {r.employee_id for r in campaigns.answers(viewer, campaign.id)} == {mine.id}

        header, rows = campaigns.export(viewer, campaign.id)
        flat = " ".join(" ".join(row) for row in rows)
        assert "Чужой" not in flat and "секрет чужого офиса" not in flat

        summary = campaigns.summary(viewer, campaign.id)
        assert summary["progress"]["total"] == 1
        scale = next(q for q in summary["questions"] if q["kind"] == "SCALE")
        assert scale["distribution"]["1"] == 0
        text = next(q for q in summary["questions"] if q["kind"] == "TEXT")
        assert "секрет чужого офиса" not in text["texts"]
        assert [g["name"] for g in summary["offices"]] == ["Офис MAIN"]

        listed = campaigns.list(viewer).items
        row = next(one for one in listed if one.id == campaign.id)
        assert row.total == 1 and row.done == 1

    def test_viewer_preview_hides_other_offices(self, campaigns, viewer, two_offices):
        seen = campaigns.preview(viewer, audience_kind="ALL", audience_ids=[])
        assert seen["total"] == 1
        assert all(one["full_name"].startswith("Свой") for one in seen["people"])

    def test_viewer_over_http(self, make_user, organization, office, two_offices):
        campaign, mine, theirs = two_offices
        user = make_user(organization, permissions=("surveys.read",), office=office)
        client = client_for(user)
        got = client.get(f"{API}/surveys/campaigns/{campaign.id}/answers/")
        assert got.status_code == 200, got.content
        assert [one["employee_id"] for one in got.json()["items"]] == [str(mine.id)]
        exported = client.get(f"{API}/surveys/campaigns/{campaign.id}/export/")
        assert "Чужой" not in exported.content.decode("utf-8")

    def test_office_scoped_manager_cannot_ask_the_whole_company(
        self, make_actor, organization, office, template,
    ):
        manager = make_actor(
            organization, permissions=("surveys.read", "surveys.manage"), office=office,
        )
        with pytest.raises(PermissionDenied):
            SurveyCampaignService().create(
                manager, template_id=template.id, audience_kind="ALL", send_now=True,
            )

    def test_office_scoped_manager_cannot_send_foreign_campaign(
        self, make_actor, organization, office, campaigns, hr, template,
    ):
        draft = campaigns.create(hr, template_id=template.id, audience_kind="ALL")
        manager = make_actor(
            organization, permissions=("surveys.read", "surveys.manage"), office=office,
        )
        with pytest.raises(PermissionDenied):
            SurveyCampaignService().send(manager, draft.id)

    def test_scoped_actor_cannot_edit_automations(
        self, make_actor, organization, office, templates, hr, template,
    ):
        templates.publish(hr, template.id)
        manager = make_actor(
            organization, permissions=("surveys.read", "surveys.manage"), office=office,
        )
        with pytest.raises(PermissionDenied):
            SurveyAutomationService().create(manager, {
                "template_id": str(template.id), "trigger_kind": "FIRST_DAY",
            })

    def test_foreign_organization_gets_not_found(
        self, make_actor, other_organization, campaigns, hr, template,
    ):
        campaign = campaigns.create(hr, template_id=template.id, audience_kind="ALL")
        stranger = make_actor(other_organization, permissions=SURVEY_PERMS)
        for call in (campaigns.answers, campaigns.summary, campaigns.export,
                     campaigns.recipients, campaigns.send):
            with pytest.raises(NotFound):
                call(stranger, campaign.id)


# --- S2. анонимный опрос ----------------------------------------------------


class TestAnonymity:
    def _anonymous(self, organization, office, campaigns, hr, template, count):
        people = [person(organization, office, f"AN-{i}") for i in range(count)]
        campaign = campaigns.create(
            hr, template_id=template.id, audience_kind="ALL",
            send_now=True, is_anonymous=True,
        )
        return campaign, people

    def test_export_order_is_not_derived_from_recipient_ids(
        self, organization, office, employee, campaigns, hr, template,
    ):
        """Атака: HR знает id получателей (`/recipients`), считает sha256 и
        сопоставляет строки анонимной выгрузки с фамилиями."""
        campaign, people = self._anonymous(organization, office, campaigns, hr, template, 4)
        rows = SurveyRecipient.objects.filter(campaign=campaign, employee__in=people)
        by_hash = sorted(rows, key=lambda r: hashlib.sha256(str(r.id).encode()).hexdigest())
        # Ответы подобраны так, что порядок по хешу обратен алфавиту.
        texts = ["я-4", "в-3", "б-2", "а-1"]
        for recipient, text in zip(by_hash, texts):
            answer_all(template, recipient, recipient.employee_id, text=text)

        _, body = campaigns.export(hr, campaign.id)
        order = [row[-1] for row in body]
        assert order != texts, "строки выгрузки идут в порядке sha256(id)"
        assert order == sorted(order)

    def test_summary_texts_do_not_follow_recipient_order(
        self, organization, office, employee, campaigns, hr, template,
    ):
        campaign, people = self._anonymous(organization, office, campaigns, hr, template, 3)
        for recipient, text in zip(
            SurveyRecipient.objects.filter(campaign=campaign, employee__in=people)
            .order_by("created_at"),
            ["я", "б", "а"],
        ):
            answer_all(template, recipient, recipient.employee_id, text=text)
        summary = campaigns.summary(hr, campaign.id)
        texts = next(q for q in summary["questions"] if q["kind"] == "TEXT")["texts"]
        assert texts == sorted(texts)

    def test_small_group_is_not_shown(
        self, organization, office, employee, campaigns, hr, template,
    ):
        """Атака: в анонимном опросе ответил один человек — сводка и
        выгрузка показывают его ответ, а `/recipients` — кто он."""
        campaign, people = self._anonymous(organization, office, campaigns, hr, template, 2)
        lone = recipient_of(campaign, people[0])
        answer_all(template, lone, people[0].id, mark=1, text="жалоба на начальника")

        summary = campaigns.summary(hr, campaign.id)
        assert summary["suppressed"] is True
        scale = next(q for q in summary["questions"] if q["kind"] == "SCALE")
        assert scale["average"] is None and scale["distribution"]["1"] == 0
        text = next(q for q in summary["questions"] if q["kind"] == "TEXT")
        assert text["texts"] == []
        _, body = campaigns.export(hr, campaign.id)
        assert body == []

        for other in people[1:] + [employee]:
            answer_all(template, recipient_of(campaign, other), other.id, mark=3)
        summary = campaigns.summary(hr, campaign.id)
        assert summary["suppressed"] is False
        scale = next(q for q in summary["questions"] if q["kind"] == "SCALE")
        assert scale["distribution"]["1"] == 1

    def test_recipients_of_anonymous_survey_carry_no_timestamps(
        self, make_user, organization, office, employee, campaigns, hr, template,
    ):
        campaign, people = self._anonymous(organization, office, campaigns, hr, template, 1)
        answer_all(template, recipient_of(campaign, employee), employee.id)
        client = client_for(make_user(organization, permissions=SURVEY_PERMS))
        got = client.get(f"{API}/surveys/campaigns/{campaign.id}/recipients/")
        assert got.status_code == 200, got.content
        for row in got.json()["items"]:
            assert row["completed_at"] is None and row["started_at"] is None


# --- S3. формулы в CSV ------------------------------------------------------


def test_export_neutralises_spreadsheet_formulas(
    organization, office, employee, templates, campaigns, hr,
):
    template = templates.create(hr, title="Формулы", questions=[
        {"text": "=HYPERLINK(\"http://evil.test\",\"клик\")", "kind": "TEXT"},
    ])
    employee.last_name = "@SUM(1+1)"
    employee.save(update_fields=["last_name"])
    campaign = campaigns.create(hr, template_id=template.id, audience_kind="ALL", send_now=True)
    question = template.questions.get()
    services.submit(
        employee_id=employee.id, recipient_id=recipient_of(campaign, employee).id,
        answers=[{"question_id": str(question.id), "text": "=cmd|' /C calc'!A0"}],
    )
    header, rows = campaigns.export(hr, campaign.id)
    for cell in header + [c for row in rows for c in row]:
        assert not cell or cell[0] not in "=+-@\t\r", cell


def test_export_over_http_is_csv_without_formulas(
    make_user, organization, office, employee, templates, campaigns, hr,
):
    template = templates.create(hr, title="Ф", questions=[
        {"text": "+1+1", "kind": "TEXT"},
    ])
    campaign = campaigns.create(hr, template_id=template.id, audience_kind="ALL", send_now=True)
    client = client_for(make_user(organization, permissions=SURVEY_PERMS))
    got = client.get(f"{API}/surveys/campaigns/{campaign.id}/export/")
    assert got.status_code == 200
    parsed = list(csv.reader(io.StringIO(got.content.decode("utf-8-sig")), delimiter=";"))
    assert parsed[0][-1] == "'+1+1"


# --- S4. автоматизации ------------------------------------------------------


class TestAutomationInput:
    @pytest.fixture()
    def rule(self, templates, hr, template):
        templates.publish(hr, template.id)
        return SurveyAutomationService().create(hr, {
            "template_id": str(template.id), "trigger_kind": "FIRST_DAY",
        })

    @pytest.mark.parametrize("payload", [
        {"send_hour": "x"},
        {"offset_days": "abc"},
        {"repeat_months": "много"},
        {"title": {"a": 1}},
        {"scope": ["x"]},
        {"scope": "abc"},
        {"scope": {"office_ids": "abc"}},
        {"scope": {"office_ids": ["not-a-uuid"]}},
        {"scope": {"evil": [1]}},
        {"offset_days": 10_000_000},
    ])
    def test_patch_garbage_is_400_not_500(self, make_user, organization, rule, payload):
        client = client_for(make_user(organization, permissions=SURVEY_PERMS))
        got = client.patch(
            f"{API}/surveys/automations/{rule.id}/", payload, format="json",
        )
        assert got.status_code == 400, (payload, got.status_code, got.content[:300])

    def test_create_rejects_bad_scope_and_huge_offset(self, make_user, organization, rule):
        client = client_for(make_user(organization, permissions=SURVEY_PERMS))
        for payload in (
            {"scope": {"office_ids": "abc"}},
            {"scope": [1, 2]},
            {"offset_days": 10_000_000},
        ):
            got = client.post(f"{API}/surveys/automations/", {
                "template_id": str(rule.template_id), "trigger_kind": "FIRST_DAY",
                **payload,
            }, format="json")
            assert got.status_code == 400, (payload, got.status_code, got.content[:300])

    def test_scope_with_foreign_office_is_refused(
        self, hr, rule, foreign_office,
    ):
        with pytest.raises(ValidationFailed):
            SurveyAutomationService().update(
                hr, rule.id, {"scope": {"office_ids": [str(foreign_office.id)]}},
            )

    def test_one_broken_rule_does_not_stop_the_others(
        self, organization, office, rule, template,
    ):
        """Строка с кривой `scope` уже в базе (например, до этой правки).
        Очередь не должна падать на ней для всех организаций сразу."""
        SurveyAutomation.objects.create(
            organization=organization, title="Сломанное", template=template,
            trigger_kind="FIRST_DAY", scope="abc", send_hour=0, send_minute=0,
        )
        SurveyAutomation.objects.create(
            organization=organization, title="Сломанное 2", template=template,
            trigger_kind="DAYS_AFTER_HIRE", offset_days=10_000_000,
            send_hour=0, send_minute=0,
        )
        tz = ZoneInfo(organization.default_timezone)
        today = timezone.now().astimezone(tz).date()
        hire(organization, office, number="AUTO-1", hire_date=today)
        SurveyAutomation.objects.filter(id=rule.id).update(send_hour=0, send_minute=0)

        run_due(now=at(tz, today, 23, 30))
        assert SurveyCampaign.objects.filter(automation_id=rule.id).exists()


# --- S5. ответы сотрудника --------------------------------------------------


class TestSubmit:
    @pytest.fixture()
    def sent(self, campaigns, hr, template, employee, organization):
        campaign = campaigns.create(hr, template_id=template.id, audience_kind="ALL", send_now=True)
        return recipient_of(campaign, employee)

    def test_duplicate_question_is_400_not_500(self, sent, employee, template):
        rows = list(template.questions.order_by("position"))
        with pytest.raises(ValidationFailed):
            services.submit(employee_id=employee.id, recipient_id=sent.id, answers=[
                {"question_id": str(rows[0].id), "number": 5},
                {"question_id": str(rows[0].id), "number": 1},
                {"question_id": str(rows[1].id), "options": ["Коллеги"]},
            ])
        assert not SurveyAnswer.objects.filter(recipient_id=sent.id).exists()

    def test_duplicate_question_over_http(
        self, bot_client, sent, employee, template, telegram_settings,
    ):
        from django_tests.conftest import build_init_data

        from humotech.telegram.models import TelegramAccount

        tg = TelegramAccount.objects.get(employee=employee).telegram_user_id
        token = bot_client.post(
            f"{API}/telegram/mini-app/auth",
            {"init_data": build_init_data(telegram_user_id=tg)}, format="json",
        )
        assert token.status_code == 200, token.content
        client = APIClient(raise_request_exception=False)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.json()['access_token']}")
        rows = list(template.questions.order_by("position"))
        got = client.post(f"{API}/me/surveys/{sent.id}", {"answers": [
            {"question_id": str(rows[0].id), "number": 5},
            {"question_id": str(rows[0].id), "number": 1},
            {"question_id": str(rows[1].id), "options": ["Коллеги"]},
        ]}, format="json")
        assert got.status_code == 400, got.content[:300]

    def test_repeated_option_is_not_counted_twice(
        self, sent, employee, template, campaigns, hr,
    ):
        rows = list(template.questions.order_by("position"))
        with pytest.raises(ValidationFailed):
            services.submit(employee_id=employee.id, recipient_id=sent.id, answers=[
                {"question_id": str(rows[0].id), "number": 5},
                {"question_id": str(rows[1].id), "options": ["Коллеги"] * 50},
            ])

    def test_too_many_answers_are_refused(self, sent, employee, template):
        rows = list(template.questions.order_by("position"))
        junk = [{"question_id": str(uuid.uuid4()), "text": "x"} for _ in range(500)]
        with pytest.raises(ValidationFailed):
            services.submit(employee_id=employee.id, recipient_id=sent.id, answers=[
                {"question_id": str(rows[0].id), "number": 5},
                {"question_id": str(rows[1].id), "options": ["Коллеги"]},
                *junk,
            ])

    def test_skipped_recipient_cannot_answer(self, sent, employee, template):
        SurveyRecipient.objects.filter(id=sent.id).update(
            status="SKIPPED", skip_reason="Нет привязки Telegram",
        )
        with pytest.raises(Conflict):
            answer_all(template, sent, employee.id)
        with pytest.raises(Conflict):
            services.open_survey(employee_id=employee.id, recipient_id=sent.id)

    def test_someone_elses_recipient_is_not_found(
        self, sent, template, organization, office,
    ):
        intruder = person(organization, office, "INTR")
        with pytest.raises(NotFound):
            answer_all(template, sent, intruder.id)


# --- S6. повторная отправка -------------------------------------------------


def test_finished_campaign_is_not_reopened_by_send(
    campaigns, hr, template, employee, organization, office,
):
    campaign = campaigns.create(
        hr, template_id=template.id, audience_kind="ALL", send_now=True,
        due_at=timezone.now() + timedelta(days=1),
    )
    SurveyCampaign.objects.filter(id=campaign.id).update(status="FINISHED")
    person(organization, office, "LATE")
    with pytest.raises(Conflict):
        campaigns.send(hr, campaign.id)


def test_send_twice_does_not_duplicate(
    campaigns, hr, template, employee, organization,
):
    from humotech.notifications.models import Notification

    campaign = campaigns.create(hr, template_id=template.id, audience_kind="ALL", send_now=True)
    campaigns.send(hr, campaign.id)
    assert SurveyRecipient.objects.filter(campaign=campaign).count() == 1
    assert Notification.objects.filter(
        related_entity_id=recipient_of(campaign, employee).id,
        notification_type=services.INVITE_TYPE,
    ).count() == 1


# --- S7. мусор в адресе -----------------------------------------------------


@pytest.mark.parametrize("path", [
    "surveys/campaigns/abc/summary/",
    "surveys/campaigns/abc/",
    "surveys/templates/abc/",
    "surveys/automations/abc/history/",
])
def test_non_uuid_id_is_404_not_500(make_user, organization, path):
    client = client_for(make_user(organization, permissions=SURVEY_PERMS))
    got = client.get(f"{API}/{path}")
    assert got.status_code in (400, 404), (path, got.status_code)


# --- S8. разметка Telegram (передано outbox/bot) ----------------------------


def test_invite_text_is_plain_text_not_html(
    campaigns, hr, templates, employee, organization,
):
    """Контракт: outbox хранит и отдаёт ОБЫЧНЫЙ текст, без HTML-экранирования.

    Тот же текст показывает Mini App, и `&lt;` там был бы порчей. Разметку
    отключает бот: тексты очереди уходят с `parse_mode=None`, это держит
    `apps/employee-telegram-bot/tests/test_security_bot.py::
    TestOutboxIsPlainText::test_message_goes_without_markup`. Поэтому
    `<a href>` в названии опроса дойдёт до человека буквами, а не ссылкой,
    и непарный `<` не сорвёт доставку.
    """
    from humotech.notifications.outbox import claim

    title = '<a href="http://evil.test">Пароль</a>'
    template = templates.create(hr, title="t", questions=QUESTIONS)
    campaigns.create(
        hr, template_id=template.id, title=title,
        audience_kind="ALL", send_now=True,
    )
    invite = next(one for one in claim(limit=10) if one.notification_type == services.INVITE_TYPE)
    assert title in invite.text
    assert "&lt;" not in invite.text and "&amp;" not in invite.text


# --- ознакомление -----------------------------------------------------------


ONB_PERMS = ("onboarding.read", "onboarding.manage", "policies.publish")


@pytest.fixture()
def seeded(organization):
    from humotech.onboarding.services import seed_organization

    return seed_organization(organization.id)


def _draft_version(organization):
    from humotech.onboarding.models import PolicyDocumentVersion

    return PolicyDocumentVersion.objects.filter(
        organization_id=organization.id,
    ).first()


class TestOnboarding:
    def test_upload_without_permission_stores_nothing(
        self, make_user, organization, seeded, settings, tmp_path,
    ):
        from humotech.files.models import File
        from humotech.onboarding.models import PolicyDocumentVersion

        settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path)}
        document = PolicyDocumentVersion.objects.filter(
            organization_id=organization.id
        ).first().document
        draft = PolicyDocumentVersion.objects.create(
            organization_id=organization.id, document=document, version="9.9",
            summary="черновик", status="DRAFT",
        )
        before = File.objects.count()
        client = client_for(make_user(organization, permissions=("onboarding.read",)))
        upload = SimpleUploadedFile("a.pdf", b"%PDF-1.4\n%fake\n", content_type="application/pdf")
        got = client.post(
            f"{API}/onboarding/versions/{draft.id}/file", {"document": upload},
            format="multipart",
        )
        assert got.status_code == 403, got.content[:300]
        assert File.objects.count() == before
        assert not any(tmp_path.rglob("*.pdf"))

    @pytest.mark.parametrize("query", [
        "limit=abc", "office_id=abc", "department_id=abc", "cursor=abc&group=done",
    ])
    def test_progress_garbage_is_not_500(self, make_user, organization, seeded, query):
        client = client_for(make_user(organization, permissions=ONB_PERMS))
        for path in ("onboarding/progress", "onboarding/counts", "onboarding/export"):
            got = client.get(f"{API}/{path}?{query}")
            assert got.status_code in (200, 400), (path, query, got.status_code)

    def test_decision_on_archived_document_is_refused(
        self, organization, seeded, employee,
    ):
        from humotech.onboarding.flow import OnboardingFlow
        from humotech.onboarding.models import (
            EmployeeOnboarding,
            OnboardingProgram,
            OnboardingSection,
            PolicyDocumentVersion,
        )

        program = OnboardingProgram.objects.get(organization_id=organization.id)
        row = EmployeeOnboarding.objects.create(
            organization_id=organization.id, employee=employee, program=program,
            status="NOT_STARTED",
        )
        flow = OnboardingFlow()
        for section in OnboardingSection.objects.filter(
            program=program, archived_at__isnull=True,
        ).order_by("position"):
            flow.acknowledge(employee.id, section.id)
        version = PolicyDocumentVersion.objects.filter(
            organization_id=organization.id, status="PUBLISHED",
        ).first()
        version.document.archived_at = timezone.now()
        version.document.save(update_fields=["archived_at"])
        with pytest.raises(Conflict):
            flow.decide(employee.id, version.id, "ACCEPTED")
        assert row.pk

    def test_foreign_section_and_draft_version_are_refused(
        self, organization, other_organization, seeded, employee,
    ):
        from humotech.onboarding.flow import OnboardingFlow
        from humotech.onboarding.models import (
            EmployeeOnboarding,
            OnboardingProgram,
            OnboardingSection,
            PolicyDocumentVersion,
        )
        from humotech.onboarding.services import seed_organization

        seed_organization(other_organization.id)
        program = OnboardingProgram.objects.get(organization_id=organization.id)
        EmployeeOnboarding.objects.create(
            organization_id=organization.id, employee=employee, program=program,
            status="NOT_STARTED",
        )
        foreign = OnboardingSection.objects.filter(
            organization_id=other_organization.id,
        ).order_by("position").first()
        flow = OnboardingFlow()
        with pytest.raises(NotFound):
            flow.acknowledge(employee.id, foreign.id)
        for section in OnboardingSection.objects.filter(
            program=program, archived_at__isnull=True,
        ).order_by("position"):
            flow.acknowledge(employee.id, section.id)
        document = PolicyDocumentVersion.objects.filter(
            organization_id=organization.id,
        ).first().document
        draft = PolicyDocumentVersion.objects.create(
            organization_id=organization.id, document=document, version="7.7",
            summary="черновик", status="DRAFT",
        )
        with pytest.raises(Conflict):
            flow.decide(employee.id, draft.id, "ACCEPTED")
        foreign_version = PolicyDocumentVersion.objects.filter(
            organization_id=other_organization.id, status="PUBLISHED",
        ).first()
        with pytest.raises(Conflict):
            flow.decide(employee.id, foreign_version.id, "ACCEPTED")


def test_onboarding_export_neutralises_formulas(make_actor, organization, seeded, employee):
    """Выгрузку ознакомления CRM кладёт в CSV как есть: имя `=...`
    исполнялось бы в Excel. Сервер отдаёт уже обезвреженную строку."""
    from humotech.onboarding.services import OnboardingService

    actor = make_actor(organization, permissions=ONB_PERMS)
    employee.last_name = "=HYPERLINK(\"http://evil.test\")"
    employee.save(update_fields=["last_name"])
    OnboardingService().enrol(actor, employee.id)
    rows = OnboardingService().export_rows(actor)
    assert rows and rows[0]["full_name"].startswith("'=")
