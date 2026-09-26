"""Безопасность обращений и кластеров неизвестных вопросов.

Атакующий проход по зоне questions: чужие переписки, подмена отправителя,
содержимое сообщений, файлы, поиск и фильтры. Каждый тест — либо
регрессия исправленной уязвимости, либо закреплённая неудачная атака.
Все данные вымышленные.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import date

import pytest
from django.utils import timezone

from humotech.ai_assistant.models import UnansweredQuestion
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.knowledge.models import FaqEntry
from humotech.notifications.models import Notification
from humotech.questions.inbox import next_number
from humotech.questions.models import EmployeeQuestion, QuestionMessage

from .conftest import bot_headers, link_telegram

pytestmark = pytest.mark.django_db

API = "/api/v1"
URL = f"{API}/knowledge/escalations"
UNANSWERED = f"{API}/knowledge/unanswered-questions"
ANSWER = ("questions.read", "questions.answer", "employees.read")
READ = ("questions.read", "employees.read")


def ask(employee, text="Как перенести отпуск?", **fields) -> EmployeeQuestion:
    moment = fields.pop("last_message_at", timezone.now())
    question = EmployeeQuestion.objects.create(
        organization=employee.organization,
        employee=employee,
        number=next_number(employee.organization_id),
        question_text=text,
        normalized_topic=text[:255],
        status=fields.pop("status", "NEW"),
        last_message_at=moment,
        **fields,
    )
    QuestionMessage.objects.create(
        organization=employee.organization, question=question, kind="EMPLOYEE",
        source="TELEGRAM", body=text, author_employee=employee, created_at=moment,
    )
    return question


def login(client, user):
    client.force_authenticate(user=user)
    client.user = user
    return client


@pytest.fixture()
def hr(api_client, make_user, organization):
    return login(api_client, make_user(organization, permissions=ANSWER))


@pytest.fixture()
def branch_employee(organization, other_office):
    """Сотрудник другого офиса (и другого региона) той же организации."""
    person = Employee.objects.create(
        organization=organization, employee_number="EMP-0901",
        first_name="Тест", last_name="Филиалов",
        hire_date=date(2024, 2, 1), employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=person, office=other_office,
        employment_type="FULL_TIME", work_mode="ONSITE",
        is_primary=True, valid_from=date(2024, 2, 1),
    )
    return person


@pytest.fixture()
def colleague(organization, office):
    person = Employee.objects.create(
        organization=organization, employee_number="EMP-0902",
        first_name="Тестовый", last_name="Коллегов",
        hire_date=date(2024, 2, 1), employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=person, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE",
        is_primary=True, valid_from=date(2024, 2, 1),
    )
    return person


# --- чужие переписки ----------------------------------------------------------

MUTATIONS = [
    ("post", "read/", None),
    ("post", "take/", None),
    ("post", "assign/", "assign"),
    ("post", "priority/", {"priority": "HIGH"}),
    ("post", "category/", {"category": "OTHER"}),
    ("post", "start/", None),
    ("post", "wait/", None),
    ("post", "close/", {"reason": "Закрыто атакой"}),
    ("post", "reopen/", None),
    ("post", "reply/", {"text": "Ответ из чужой организации"}),
    ("get", "", None),
    ("get", "context/", None),
]


def _call(client, method, question, suffix, payload, target_user_id):
    if payload == "assign":
        payload = {"to_user_id": str(target_user_id)}
    url = f"{URL}/{question.id}/{suffix}"
    if method == "get":
        return client.get(url)
    return client.post(url, payload or {}, format="json")


class TestForeignConversations:
    @pytest.mark.parametrize("method,suffix,payload", MUTATIONS)
    def test_foreign_organization_gets_404(
        self, api_client, make_user, other_organization, employee, linked_account,
        method, suffix, payload,
    ):
        question = ask(employee)
        intruder = make_user(other_organization, permissions=ANSWER)
        login(api_client, intruder)

        response = _call(api_client, method, question, suffix, payload, intruder.id)

        assert response.status_code == 404
        question.refresh_from_db()
        assert question.assigned_to_user_id is None
        assert question.status == "NEW"
        assert not QuestionMessage.objects.filter(question=question, kind="HR").exists()
        assert not Notification.objects.exists()

    @pytest.mark.parametrize("method,suffix,payload", MUTATIONS)
    def test_other_office_hr_gets_403(
        self, api_client, make_user, organization, other_office, employee,
        linked_account, method, suffix, payload,
    ):
        question = ask(employee)
        branch_hr = make_user(organization, permissions=ANSWER, office=other_office)
        login(api_client, branch_hr)

        response = _call(api_client, method, question, suffix, payload, branch_hr.id)

        assert response.status_code in (403, 404)
        question.refresh_from_db()
        assert question.assigned_to_user_id is None
        assert question.status == "NEW"
        assert not Notification.objects.exists()

    @pytest.mark.parametrize("method,suffix,payload", [m for m in MUTATIONS if m[0] == "post" and m[1] != "read/"])
    def test_read_only_hr_cannot_change(
        self, api_client, make_user, organization, employee, linked_account,
        method, suffix, payload,
    ):
        question = ask(employee)
        viewer = make_user(organization, permissions=READ)
        login(api_client, viewer)

        response = _call(api_client, method, question, suffix, payload, viewer.id)

        assert response.status_code == 403
        assert not Notification.objects.exists()

    def test_other_office_list_does_not_show_question(
        self, api_client, make_user, organization, other_office, employee,
    ):
        ask(employee, "Секретный вопрос про зарплату")
        login(api_client, make_user(organization, permissions=ANSWER, office=other_office))

        body = api_client.get(f"{URL}/", {"search": "зарплат"}).json()

        assert body["items"] == []

    def test_assign_to_foreign_organization_user_is_refused(
        self, hr, make_user, other_organization, employee,
    ):
        question = ask(employee)
        stranger = make_user(other_organization, permissions=ANSWER)

        response = hr.post(
            f"{URL}/{question.id}/assign/", {"to_user_id": str(stranger.id)},
            format="json",
        )

        assert response.status_code == 404
        question.refresh_from_db()
        assert question.assigned_to_user_id is None

    def test_foreign_message_file_is_not_served(
        self, api_client, make_user, other_organization, hr, employee, linked_account,
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        question = ask(employee)
        body = hr.post(
            f"{URL}/{question.id}/reply/",
            {"file": SimpleUploadedFile("f.pdf", b"%PDF-1.4\nx", content_type="application/pdf")},
            format="multipart",
        ).json()
        message_id = next(m["id"] for m in body["messages"] if m["kind"] == "HR")

        from rest_framework.test import APIClient

        intruder = login(APIClient(), make_user(other_organization, permissions=ANSWER))
        response = intruder.get(f"{URL}/{question.id}/messages/{message_id}/file/")
        assert response.status_code == 404

        # Своё обращение в чужой организации + чужой message_id тоже не отдают.
        other_employee = Employee.objects.create(
            organization=other_organization, employee_number="F-1",
            first_name="Ч", last_name="Ужой", hire_date=date(2024, 1, 1),
            employment_status="ACTIVE",
        )
        own = ask(other_employee)
        response = intruder.get(f"{URL}/{own.id}/messages/{message_id}/file/")
        assert response.status_code == 404


# --- бот: сотрудник пишет и забирает файлы -------------------------------------


class TestEmployeeSide:
    def test_body_fields_cannot_spoof_author(
        self, bot_client, linked_account, employee, colleague, hr,
    ):
        response = bot_client.post(
            f"{API}/me/questions/messages",
            {
                "text": "Мой вопрос",
                "employee_id": str(colleague.id),
                "author_user_id": str(hr.user.id),
                "author_employee_id": str(colleague.id),
                "kind": "HR", "source": "CRM", "from_hr": True,
                "organization_id": str(uuid.uuid4()),
                "question_id": str(uuid.uuid4()),
            },
            format="json", **bot_headers(),
        )

        assert response.status_code == 201
        question = EmployeeQuestion.objects.get(id=response.json()["question_id"])
        assert question.employee_id == employee.id
        message = question.messages.get(kind="EMPLOYEE")
        assert message.author_employee_id == employee.id
        assert message.author_user_id is None
        assert message.source == "TELEGRAM"

    def test_reply_body_fields_cannot_spoof_author(
        self, hr, employee, colleague, linked_account,
    ):
        question = ask(employee)
        body = hr.post(
            f"{URL}/{question.id}/reply/",
            {"text": "Ответ", "author_user_id": str(uuid.uuid4()),
             "author_employee_id": str(colleague.id), "kind": "EMPLOYEE",
             "source": "TELEGRAM", "employee_id": str(colleague.id)},
            format="json",
        ).json()

        reply = QuestionMessage.objects.get(question=question, kind="HR")
        assert reply.author_user_id == hr.user.id
        assert reply.author_employee_id is None
        assert reply.source == "CRM"
        assert Notification.objects.get().employee_id == employee.id
        assert body["employee"]["id"] == str(employee.id)

    def test_telegram_message_id_of_another_employee_does_not_leak(
        self, bot_client, linked_account, employee, colleague, telegram_settings,
    ):
        theirs = ask(colleague, "Чужой вопрос")
        QuestionMessage.objects.filter(question=theirs).update(telegram_message_id=4242)

        response = bot_client.post(
            f"{API}/me/questions/messages",
            {"text": "Мой вопрос", "telegram_message_id": 4242},
            format="json", **bot_headers(),
        )

        assert response.status_code == 201
        assert response.json()["question_id"] != str(theirs.id)

    def test_other_employee_cannot_fetch_reply_file(
        self, hr, bot_client, employee, colleague, linked_account,
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        link_telegram(colleague, telegram_user_id=777_000_222, username="colleague")
        question = ask(employee)
        body = hr.post(
            f"{URL}/{question.id}/reply/",
            {"file": SimpleUploadedFile("f.pdf", b"%PDF-1.4\nx", content_type="application/pdf")},
            format="multipart",
        ).json()
        message_id = next(m["id"] for m in body["messages"] if m["kind"] == "HR")

        theirs = bot_client.get(
            f"{API}/me/questions/replies/{message_id}/file",
            **bot_headers(777_000_222),
        )
        assert theirs.status_code == 404

        # Сообщение сотрудника (не HR) по этому маршруту не отдаётся никогда.
        employee_message = QuestionMessage.objects.get(question=question, kind="EMPLOYEE")
        own = bot_client.get(
            f"{API}/me/questions/replies/{employee_message.id}/file", **bot_headers(),
        )
        assert own.status_code == 404

    @pytest.mark.parametrize("value", [2**63, -(2**63) - 1, 10**30])
    def test_huge_telegram_message_id_is_400(self, bot_client, linked_account, value):
        response = bot_client.post(
            f"{API}/me/questions/messages",
            {"text": "Вопрос", "telegram_message_id": value},
            format="json", **bot_headers(),
        )

        assert response.status_code == 400
        assert not EmployeeQuestion.objects.exists()

    @pytest.mark.parametrize("text", ["\x00", "a\x00b", "a" * 4001, "   ", ""])
    def test_bad_text_is_400(self, bot_client, linked_account, text):
        response = bot_client.post(
            f"{API}/me/questions/messages", {"text": text},
            format="json", **bot_headers(),
        )

        assert response.status_code == 400

    def test_unusual_unicode_is_stored_as_is(self, bot_client, linked_account):
        text = "RTL‮ тест 👨‍👩‍👧 <script>alert(1)</script> {{7*7}} %s {0}"
        response = bot_client.post(
            f"{API}/me/questions/messages", {"text": text},
            format="json", **bot_headers(),
        )

        assert response.status_code == 201
        question = EmployeeQuestion.objects.get(id=response.json()["question_id"])
        assert question.messages.get(kind="EMPLOYEE").body == text


# --- содержимое ответа и очередь Telegram ---------------------------------------


class TestReplyContent:
    def test_template_like_text_is_not_interpreted(self, hr, employee, linked_account):
        question = ask(employee, "Вопрос {number} {0} %(x)s {{7*7}}")
        text = "Ответ {number} {0} %s {{7*7}} ${x}"

        hr.post(f"{URL}/{question.id}/reply/", {"text": text}, format="json")

        body = Notification.objects.get().body
        assert body.endswith(text)
        assert "Вопрос {number} {0} %(x)s {{7*7}}" in body
        assert "49" not in body

    def test_reply_fits_telegram_message_limit(self, hr, employee, linked_account):
        question = ask(employee, "Очень длинный вопрос " * 20)
        text = "ж" * 4000

        response = hr.post(f"{URL}/{question.id}/reply/", {"text": text}, format="json")

        assert response.status_code == 200
        body = Notification.objects.get().body
        # Предел Telegram — 4096 единиц UTF-16 на сообщение.
        assert len(body.encode("utf-16-le")) // 2 <= 4096
        assert body.endswith(text)
        assert body.startswith(f"💬 Ответ HR по обращению №{question.number}")

    def test_emoji_reply_fits_telegram_message_limit(self, hr, employee, linked_account):
        question = ask(employee)
        text = "👍" * 2000  # 4000 единиц UTF-16

        response = hr.post(f"{URL}/{question.id}/reply/", {"text": text}, format="json")

        assert response.status_code == 200
        body = Notification.objects.get().body
        assert len(body.encode("utf-16-le")) // 2 <= 4096
        assert body.endswith(text)

    def test_reply_beyond_telegram_limit_is_400(self, hr, employee, linked_account):
        question = ask(employee)
        text = "👍" * 3000  # 3000 символов, но 6000 единиц UTF-16

        response = hr.post(f"{URL}/{question.id}/reply/", {"text": text}, format="json")

        assert response.status_code == 400
        assert not Notification.objects.exists()
        assert not QuestionMessage.objects.filter(question=question, kind="HR").exists()

    def test_short_reply_keeps_quote(self, hr, employee, linked_account):
        question = ask(employee, "Как перенести отпуск?")

        hr.post(f"{URL}/{question.id}/reply/", {"text": "Можно."}, format="json")

        assert "Вы спрашивали: «Как перенести отпуск?»" in Notification.objects.get().body

    def test_html_is_passed_as_plain_text(self, hr, employee, linked_account):
        """Сервер не превращает текст в разметку: экранирование — дело отправщика.

        Бот шлёт очередь с parse_mode=HTML по умолчанию (передано зоне bot):
        ответ с «<» сейчас не доходит, а `<a href>` становится ссылкой.
        """
        question = ask(employee)
        text = 'Сумма < 5000 & <a href="https://example.invalid">ссылка</a>'

        hr.post(f"{URL}/{question.id}/reply/", {"text": text}, format="json")

        assert Notification.objects.get().body.endswith(text)
        assert QuestionMessage.objects.get(question=question, kind="HR").body == text

    @pytest.mark.parametrize("field,value", [
        ("text", "a" * 4001), ("text", "a\x00b"), ("after", "DROP"),
        ("client_request_id", "x" * 101), ("close_reason", "x" * 1001),
    ])
    def test_bad_reply_input_is_400(self, hr, employee, linked_account, field, value):
        question = ask(employee)
        payload = {"text": "Ответ", field: value}

        response = hr.post(f"{URL}/{question.id}/reply/", payload, format="json")

        assert response.status_code == 400
        assert not Notification.objects.exists()

    def test_nested_json_is_400(self, hr, employee, linked_account):
        question = ask(employee)
        nested: dict = {}
        cursor = nested
        for _ in range(200):
            cursor["a"] = {}
            cursor = cursor["a"]

        response = hr.post(
            f"{URL}/{question.id}/reply/", {"text": nested}, format="json",
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("name,content,mime", [
        ("evil.html", b"<script>alert(1)</script>", "text/html"),
        ("evil.svg", b"<svg onload=alert(1)>", "image/svg+xml"),
        ("evil.pdf", b"<html><script>alert(1)</script>", "application/pdf"),
    ])
    def test_reply_file_type_is_checked(self, hr, employee, linked_account, name, content, mime):
        from django.core.files.uploadedfile import SimpleUploadedFile

        question = ask(employee)
        response = hr.post(
            f"{URL}/{question.id}/reply/",
            {"file": SimpleUploadedFile(name, content, content_type=mime)},
            format="multipart",
        )

        assert response.status_code == 400
        assert not Notification.objects.exists()


# --- поиск, фильтры, курсор, идентификаторы -----------------------------------


class TestQueryInput:
    @pytest.mark.parametrize("needle", [
        "'", "%", "_", "' OR 1=1--", "\\", "'); DROP TABLE employee_questions;--",
        "№", "#1", "9" * 30, "Иван Иванов", "a " * 5,
    ])
    def test_search_is_parameterized(self, hr, employee, needle):
        ask(employee, "Обычный вопрос")

        response = hr.get(f"{URL}/", {"search": needle})
        counts = hr.get(f"{URL}/counts/", {"search": needle})

        assert response.status_code == 200
        assert counts.status_code == 200
        if needle in ("'", "%", "_", "' OR 1=1--"):
            assert response.json()["items"] == []

    @pytest.mark.parametrize("url", [f"{URL}/", f"{URL}/counts/", f"{UNANSWERED}/"])
    @pytest.mark.parametrize("param", ["search", "language"])
    def test_nul_in_query_is_400(self, hr, url, param):
        response = hr.get(url, {param: "a\x00b"})

        assert response.status_code in (200, 400)
        if param == "search" or url == f"{UNANSWERED}/":
            assert response.status_code == 400

    def test_huge_search_is_400(self, hr, employee):
        ask(employee)

        response = hr.get(f"{URL}/", {"search": "a " * 2000})

        assert response.status_code == 400

    @pytest.mark.parametrize("params", [
        {"status": "NEW,BOGUS"}, {"category": "X"}, {"priority": "' OR 1=1"},
        {"quick": "everything"}, {"assignee": "root"}, {"office_id": "1"},
        {"date_from": "2024-13-01"}, {"date_to": "0000-00-00"},
        {"limit": "abc"}, {"limit": "0"}, {"limit": "-1"},
        {"cursor": "!!!"}, {"cursor": "e30="},
    ])
    def test_bad_filters_are_400(self, hr, params):
        response = hr.get(f"{URL}/", params)

        assert response.status_code == 400

    def test_huge_limit_is_capped(self, hr, employee):
        ask(employee)

        response = hr.get(f"{URL}/", {"limit": str(10**30)})

        assert response.status_code in (200, 400)

    @pytest.mark.parametrize("payload", [
        {"r": 1e999, "t": "2024-01-01T00:00:00+00:00", "id": str(uuid.uuid4())},
        {"r": "NaN", "t": "2024-01-01T00:00:00+00:00", "id": str(uuid.uuid4())},
        {"r": 0, "t": "2024-01-01T00:00:00", "id": str(uuid.uuid4())},
        {"r": 0, "t": "9999-12-31T23:59:59+14:00", "id": str(uuid.uuid4())},
        {"r": [], "t": {}, "id": 1},
        [1, 2, 3],
    ])
    def test_crafted_cursor_is_not_500(self, hr, employee, payload):
        ask(employee)
        raw = json.dumps(payload).replace("Infinity", "1e999")
        cursor = base64.urlsafe_b64encode(raw.encode()).decode()

        response = hr.get(f"{URL}/", {"cursor": cursor})

        assert response.status_code in (200, 400)

    @pytest.mark.parametrize("raw", [
        json.dumps({"t": 1e999, "id": str(uuid.uuid4())}).replace("Infinity", "1e999"),
        "[1, 2, 3]",
        "\udcff",
    ])
    def test_crafted_unanswered_cursor_is_not_500(self, hr, organization, raw):
        cluster(organization)
        cursor = base64.urlsafe_b64encode(raw.encode("utf-8", "surrogateescape")).decode()

        response = hr.get(f"{UNANSWERED}/", {"cursor": cursor})

        assert response.status_code in (200, 400)

    def test_undecodable_cursor_bytes_are_400(self, hr):
        cursor = base64.urlsafe_b64encode(b"\xff\xfe\x00").decode()

        assert hr.get(f"{URL}/", {"cursor": cursor}).status_code == 400

    @pytest.mark.parametrize("suffix,method", [
        ("", "get"), ("context/", "get"), ("read/", "post"), ("take/", "post"),
        ("start/", "post"), ("wait/", "post"), ("reopen/", "post"),
        ("draft/", "post"), ("close/", "post"), ("reply/", "post"),
    ])
    def test_non_uuid_pk_is_404(self, hr, suffix, method):
        response = getattr(hr, method)(f"{URL}/not-a-uuid/{suffix}", {}, format="json") \
            if method == "post" else hr.get(f"{URL}/not-a-uuid/{suffix}")

        assert response.status_code == 404

    @pytest.mark.parametrize("suffix,method", [
        ("", "get"), ("assign/", "post"), ("close/", "post"), ("make-faq/", "post"),
    ])
    def test_non_uuid_unanswered_pk_is_404(self, hr, suffix, method):
        if method == "post":
            response = hr.post(f"{UNANSWERED}/not-a-uuid/{suffix}", {}, format="json")
        else:
            response = hr.get(f"{UNANSWERED}/not-a-uuid/{suffix}")

        assert response.status_code == 404

    def test_dash_only_message_id_is_404(self, hr, employee):
        question = ask(employee)

        response = hr.get(f"{URL}/{question.id}/messages/{'-' * 36}/file/")

        assert response.status_code == 404


# --- кластеры неизвестных вопросов ---------------------------------------------


def cluster(organization, *, office=None, region=None, text="Как получить справку?"):
    return UnansweredQuestion.objects.create(
        organization=organization,
        normalized_hash=uuid.uuid4().hex * 2,
        question_text=text,
        language="ru",
        status="NEW",
        office=office,
        region=region,
    )


class TestUnansweredScope:
    def test_office_hr_does_not_see_other_office_clusters(
        self, api_client, make_user, organization, office, other_office, region,
    ):
        mine = cluster(organization, office=office, region=region, text="Мой офис")
        theirs = cluster(organization, office=other_office, text="Вопрос филиала")
        login(api_client, make_user(organization, permissions=ANSWER, office=office))

        ids = [item["id"] for item in api_client.get(f"{UNANSWERED}/").json()["items"]]

        assert str(mine.id) in ids
        assert str(theirs.id) not in ids
        for suffix, method, payload in [
            ("", "get", None),
            ("assign/", "post", {"to_user_id": str(api_client.user.id)}),
            ("close/", "post", {"as_answered": False}),
            ("make-faq/", "post", {"canonical_question": "В", "approved_answer": "О",
                                   "language": "ru"}),
        ]:
            url = f"{UNANSWERED}/{theirs.id}/{suffix}"
            response = (
                api_client.get(url) if method == "get"
                else api_client.post(url, payload, format="json")
            )
            assert response.status_code in (403, 404), (suffix, response.status_code)
        theirs.refresh_from_db()
        assert theirs.status == "NEW" and theirs.assigned_to_user_id is None
        assert not FaqEntry.objects.exists()

    def test_organization_wide_hr_sees_everything(
        self, hr, organization, office, other_office,
    ):
        one = cluster(organization, office=office)
        two = cluster(organization, office=other_office)
        three = cluster(organization)

        ids = {item["id"] for item in hr.get(f"{UNANSWERED}/").json()["items"]}

        assert {str(one.id), str(two.id), str(three.id)} <= ids

    def test_make_faq_rejects_foreign_office_and_region(
        self, hr, organization, foreign_office, foreign_region,
    ):
        item = cluster(organization)
        for extra in ({"office_id": str(foreign_office.id)},
                      {"region_id": str(foreign_region.id)}):
            response = hr.post(
                f"{UNANSWERED}/{item.id}/make-faq/",
                {"canonical_question": "Вопрос", "approved_answer": "Ответ",
                 "language": "ru", **extra},
                format="json",
            )
            assert response.status_code in (403, 404), extra
        assert not FaqEntry.objects.exists()

    def test_make_faq_rejects_office_outside_scope(
        self, api_client, make_user, organization, office, other_office, region,
    ):
        item = cluster(organization, office=office, region=region)
        login(api_client, make_user(organization, permissions=ANSWER, office=office))

        response = api_client.post(
            f"{UNANSWERED}/{item.id}/make-faq/",
            {"canonical_question": "Вопрос", "approved_answer": "Ответ",
             "language": "ru", "office_id": str(other_office.id)},
            format="json",
        )

        assert response.status_code == 403
        assert not FaqEntry.objects.exists()

    def test_make_faq_for_own_office_works(
        self, api_client, make_user, organization, office, region,
    ):
        item = cluster(organization, office=office, region=region)
        login(api_client, make_user(organization, permissions=ANSWER, office=office))

        response = api_client.post(
            f"{UNANSWERED}/{item.id}/make-faq/",
            {"canonical_question": "Вопрос", "approved_answer": "Ответ",
             "language": "ru", "office_id": str(office.id)},
            format="json",
        )

        assert response.status_code == 201

    @pytest.mark.parametrize("extra", [
        {"canonical_question": "в" * 1001},
        {"approved_answer": "о" * 3501},
        {"priority": 2**40},
        {"priority": -(2**40)},
        {"priority": 1_000_001},
    ])
    def test_make_faq_bounds_are_400(self, hr, organization, extra):
        item = cluster(organization)
        payload = {"canonical_question": "Вопрос", "approved_answer": "Ответ",
                   "language": "ru", **extra}

        response = hr.post(f"{UNANSWERED}/{item.id}/make-faq/", payload, format="json")

        assert response.status_code == 400, response.content
        assert not FaqEntry.objects.exists()
        item.refresh_from_db()
        assert item.status == "NEW"

    def test_make_faq_at_the_limits_works(self, hr, organization):
        item = cluster(organization)

        response = hr.post(
            f"{UNANSWERED}/{item.id}/make-faq/",
            {"canonical_question": "в" * 1000, "approved_answer": "о" * 3500,
             "language": "ru", "priority": 1_000_000},
            format="json",
        )

        assert response.status_code == 201, response.content

    def test_assign_needs_permission_before_lookup(
        self, api_client, make_user, organization, other_organization,
    ):
        item = cluster(organization)
        login(api_client, make_user(organization, permissions=READ))
        real = make_user(organization, permissions=ANSWER)

        for target in (real.id, uuid.uuid4()):
            response = api_client.post(
                f"{UNANSWERED}/{item.id}/assign/", {"to_user_id": str(target)},
                format="json",
            )
            assert response.status_code == 403

    def test_assign_to_foreign_user_is_404(self, hr, make_user, organization, other_organization):
        item = cluster(organization)
        stranger = make_user(other_organization, permissions=ANSWER)

        response = hr.post(
            f"{UNANSWERED}/{item.id}/assign/", {"to_user_id": str(stranger.id)},
            format="json",
        )

        assert response.status_code == 404
        item.refresh_from_db()
        assert item.assigned_to_user_id is None
