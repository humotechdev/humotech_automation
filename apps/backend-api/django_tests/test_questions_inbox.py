"""Обращения сотрудников: очередь, переписка, ответ в Telegram, приём от бота.

Что здесь закреплено:

  * срочные и просроченные — сверху, счётчики вкладок не зависят от вкладки;
  * «взять в работу» = ответственный + IN_PROGRESS, с событием и журналом;
  * ответ без живой привязки Telegram не сохраняется и не отправляется;
  * у каждого ответа своя строка очереди — второй ответ доходит;
  * повторное нажатие «Отправить» не даёт второго сообщения;
  * сообщение сотрудника возвращает WAITING_EMPLOYEE в работу и помечает
    непрочитанным; закрытое недавно — переоткрывает, давно — даёт новое;
  * чужой офис не видит обращения, без права раздел контекста — `null`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone

from humotech.audit.models import AuditLog
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.notifications.models import Notification
from humotech.questions.inbox import SLA, InboxService, next_number
from humotech.questions.models import EmployeeQuestion, QuestionMessage

from .conftest import bot_headers, link_telegram

pytestmark = pytest.mark.django_db

API = "/api/v1"
URL = f"{API}/knowledge/escalations"
ANSWER = ("questions.read", "questions.answer", "employees.read")
READ = ("questions.read", "employees.read")


def ask(employee, text="Как перенести отпуск?", **fields) -> EmployeeQuestion:
    """Обращение так, как его заводит приём сообщения: с первой репликой."""
    moment = fields.pop("last_message_at", timezone.now())
    question = EmployeeQuestion.objects.create(
        organization=employee.organization,
        employee=employee,
        number=next_number(employee.organization_id),
        question_text=text,
        normalized_topic=text,
        status=fields.pop("status", "NEW"),
        last_message_at=moment,
        **fields,
    )
    QuestionMessage.objects.create(
        organization=employee.organization, question=question, kind="EMPLOYEE",
        source="TELEGRAM", body=text, author_employee=employee, created_at=moment,
    )
    return question


@pytest.fixture()
def hr(api_client, make_user, organization):
    user = make_user(organization, permissions=ANSWER)
    api_client.force_authenticate(user=user)
    api_client.user = user
    return api_client


@pytest.fixture()
def other_employee(organization, office):
    person = Employee.objects.create(
        organization=organization, employee_number="EMP-0002",
        first_name="Шахноза", last_name="Облокулова",
        hire_date=date(2024, 2, 1), employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=person, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE",
        is_primary=True, valid_from=date(2024, 2, 1),
    )
    return person


# --- очередь -----------------------------------------------------------------


class TestQueue:
    def test_urgent_and_overdue_go_first(self, hr, employee, other_employee):
        now = timezone.now()
        fresh = ask(employee, "Свежий", last_message_at=now)
        overdue = ask(
            other_employee, "Просроченный",
            last_message_at=now - timedelta(hours=6),
            due_at=now - timedelta(hours=2),
        )
        urgent = ask(
            employee, "Срочный", priority="URGENT",
            last_message_at=now - timedelta(hours=1),
        )

        items = hr.get(f"{URL}/").json()["items"]

        ids = [item["id"] for item in items]
        assert ids[-1] == str(fresh.id)
        assert set(ids[:2]) == {str(overdue.id), str(urgent.id)}
        # Среди срочных — сначала более свежее сообщение.
        assert ids[0] == str(urgent.id)
        assert next(i for i in items if i["id"] == str(overdue.id))["overdue"] is True

    def test_tab_counts_do_not_depend_on_the_tab(self, hr, employee):
        ask(employee, "Новый")
        ask(employee, "В работе", status="IN_PROGRESS")
        ask(employee, "Ждёт", status="WAITING_EMPLOYEE")
        ask(employee, "Закрыт", status="CLOSED", closed_at=timezone.now())

        body = hr.get(f"{URL}/counts/", {"status": "NEW"}).json()

        assert body["statuses"] == {
            "NEW": 1, "IN_PROGRESS": 1, "WAITING_EMPLOYEE": 1, "CLOSED": 1,
        }
        assert body["quick"]["all"] == 1

    def test_search_finds_by_number_name_and_message(self, hr, employee, other_employee):
        first = ask(employee, "Расчётный лист")
        second = ask(other_employee, "Отпуск")
        QuestionMessage.objects.create(
            organization=employee.organization, question=first, kind="EMPLOYEE",
            source="TELEGRAM", body="Уточнение про премию", author_employee=employee,
        )

        def found(search):
            return [i["id"] for i in hr.get(f"{URL}/", {"search": search}).json()["items"]]

        assert found(str(second.number)) == [str(second.id)]
        assert found("Облокулова") == [str(second.id)]
        assert found("премию") == [str(first.id)]
        assert found("EMP-0002") == [str(second.id)]

    def test_quick_filter_mine(self, hr, employee):
        mine = ask(employee, "Моё", status="IN_PROGRESS", assigned_to_user=hr.user)
        ask(employee, "Ничьё")

        items = hr.get(f"{URL}/", {"quick": "mine"}).json()["items"]

        assert [i["id"] for i in items] == [str(mine.id)]

    def test_another_office_does_not_see_the_question(
        self, api_client, make_user, organization, other_office, employee
    ):
        ours = ask(employee)
        api_client.force_authenticate(
            user=make_user(organization, permissions=ANSWER, office=other_office)
        )

        assert api_client.get(f"{URL}/").json()["items"] == []
        assert api_client.get(f"{URL}/{ours.id}/").status_code == 403


# --- действия ----------------------------------------------------------------


class TestActions:
    def test_taking_assigns_me_and_starts_work(self, hr, employee):
        question = ask(employee)

        body = hr.post(f"{URL}/{question.id}/take/").json()

        assert body["status"] == "IN_PROGRESS"
        assert body["assignee"]["id"] == str(hr.user.id)
        assert [m["event"] for m in body["messages"] if m["kind"] == "SYSTEM"] == ["TAKEN"]
        entry = AuditLog.objects.get(entity_id=question.id, action="question.take")
        assert entry.old_values["status"] == "NEW"
        assert entry.new_values["status"] == "IN_PROGRESS"

    def test_transfer_to_someone_who_cannot_answer_is_refused(
        self, hr, make_user, organization, employee
    ):
        question = ask(employee)
        reader = make_user(organization, permissions=READ)

        response = hr.post(
            f"{URL}/{question.id}/assign/", {"to_user_id": str(reader.id)},
            format="json",
        )

        assert response.status_code == 400

    def test_transfer_is_an_event(self, hr, make_user, organization, employee):
        question = ask(employee, status="IN_PROGRESS", assigned_to_user=hr.user)
        colleague = make_user(organization, permissions=ANSWER)

        body = hr.post(
            f"{URL}/{question.id}/assign/", {"to_user_id": str(colleague.id)},
            format="json",
        ).json()

        assert body["assignee"]["id"] == str(colleague.id)
        assert body["messages"][-1]["event"] == "TRANSFERRED"
        assert AuditLog.objects.filter(
            entity_id=question.id, action="question.transfer"
        ).exists()

    def test_priority_moves_the_deadline_from_the_message(self, hr, employee):
        moment = timezone.now() - timedelta(minutes=30)
        question = ask(employee, last_message_at=moment, due_at=moment + SLA["NORMAL"])

        hr.post(f"{URL}/{question.id}/priority/", {"priority": "URGENT"}, format="json")

        question.refresh_from_db()
        assert question.due_at == moment + SLA["URGENT"]

    def test_closing_keeps_author_date_and_reason(self, hr, employee):
        question = ask(employee, status="IN_PROGRESS")

        assert hr.post(f"{URL}/{question.id}/close/", {}, format="json").status_code == 400
        body = hr.post(
            f"{URL}/{question.id}/close/", {"reason": "Дубликат"}, format="json"
        ).json()

        assert body["status"] == "CLOSED"
        assert body["close_reason"] == "Дубликат"
        assert body["closed_by"]["id"] == str(hr.user.id)
        assert body["closed_at"]

    def test_reopening_returns_to_work(self, hr, employee):
        question = ask(
            employee, status="CLOSED", closed_at=timezone.now(),
            assigned_to_user=hr.user, close_reason="Решено",
        )

        body = hr.post(f"{URL}/{question.id}/reopen/").json()

        assert body["status"] == "IN_PROGRESS"
        assert body["close_reason"] is None
        assert body["messages"][-1]["event"] == "REOPENED"

    def test_reading_needs_only_read_but_acting_needs_answer(
        self, api_client, make_user, organization, employee
    ):
        question = ask(employee)
        api_client.force_authenticate(user=make_user(organization, permissions=READ))

        body = api_client.get(f"{URL}/{question.id}/").json()

        assert body["actions"]["take"] is False
        assert api_client.post(f"{URL}/{question.id}/take/").status_code == 403


# --- ответ -------------------------------------------------------------------


class TestReply:
    def test_without_telegram_nothing_is_saved_or_sent(self, hr, employee):
        question = ask(employee)

        response = hr.post(
            f"{URL}/{question.id}/reply/", {"text": "Ответ"}, format="json"
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "telegram_not_connected"
        assert not QuestionMessage.objects.filter(question=question, kind="HR").exists()
        assert Notification.objects.count() == 0
        question.refresh_from_db()
        assert question.status == "NEW"

    def test_revoked_telegram_is_not_connected(self, hr, employee, telegram_settings):
        link_telegram(employee, status="REVOKED")
        question = ask(employee)

        response = hr.post(
            f"{URL}/{question.id}/reply/", {"text": "Ответ"}, format="json"
        )

        assert response.status_code == 409

    def test_reply_goes_to_history_queue_and_journal(self, hr, employee, linked_account):
        question = ask(employee, due_at=timezone.now() + SLA["NORMAL"], unread=True)

        body = hr.post(
            f"{URL}/{question.id}/reply/",
            {"text": "Даты можно изменить до начала отпуска."}, format="json",
        ).json()

        reply = [m for m in body["messages"] if m["kind"] == "HR"]
        assert len(reply) == 1
        assert reply[0]["author"]["id"] == str(hr.user.id)
        assert reply[0]["delivery"]["status"] == "QUEUED"
        assert body["status"] == "IN_PROGRESS"
        assert body["assignee"]["id"] == str(hr.user.id)
        assert body["due_at"] is None and body["awaiting_reply"] is False

        notification = Notification.objects.get()
        # В чат — с номером обращения и цитатой вопроса, в ленте — чистый
        # текст. Цитата нужна не для красоты: человек задал вопрос неделю
        # назад, с тех пор написал ещё два и без неё не поймёт, на какой
        # именно пришёл ответ.
        assert notification.body == (
            f"💬 Ответ HR по обращению №{question.number}\n\n"
            "Вы спрашивали: «Как перенести отпуск?»\n\n"
            "Даты можно изменить до начала отпуска."
        )
        assert reply[0]["body"] == "Даты можно изменить до начала отпуска."
        assert notification.related_entity_id == question.id
        assert AuditLog.objects.filter(entity_id=question.id, action="question.reply").exists()

    def test_second_reply_is_also_sent(self, hr, employee, linked_account):
        question = ask(employee)

        hr.post(f"{URL}/{question.id}/reply/", {"text": "Первый"}, format="json")
        hr.post(f"{URL}/{question.id}/reply/", {"text": "Второй"}, format="json")

        bodies = sorted(
            body.rsplit("\n", 1)[-1]
            for body in Notification.objects.values_list("body", flat=True)
        )
        assert bodies == ["Второй", "Первый"]

    def test_double_click_sends_once(self, hr, employee, linked_account):
        question = ask(employee)
        payload = {"text": "Ответ", "client_request_id": "click-1"}

        hr.post(f"{URL}/{question.id}/reply/", payload, format="json")
        hr.post(f"{URL}/{question.id}/reply/", payload, format="json")

        assert Notification.objects.count() == 1
        assert QuestionMessage.objects.filter(question=question, kind="HR").count() == 1

    def test_asking_for_details_waits_for_the_employee(self, hr, employee, linked_account):
        question = ask(employee)

        body = hr.post(
            f"{URL}/{question.id}/reply/",
            {"text": "Уточните даты, пожалуйста.", "after": "WAIT"}, format="json",
        ).json()

        assert body["status"] == "WAITING_EMPLOYEE"

    def test_close_after_sending(self, hr, employee, linked_account):
        question = ask(employee)

        body = hr.post(
            f"{URL}/{question.id}/reply/",
            {"text": "Готово.", "after": "CLOSE"}, format="json",
        ).json()

        assert body["status"] == "CLOSED"
        assert body["close_reason"]
        assert Notification.objects.count() == 1

    def test_closed_question_is_not_answered(self, hr, employee, linked_account):
        question = ask(employee, status="CLOSED", closed_at=timezone.now())

        response = hr.post(f"{URL}/{question.id}/reply/", {"text": "Ответ"}, format="json")

        assert response.status_code == 409
        assert Notification.objects.count() == 0

    def test_draft_without_assistant_says_so(self, hr, employee):
        question = ask(employee)

        response = hr.post(f"{URL}/{question.id}/draft/")

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "ai_disabled"


# --- сообщение сотрудника ----------------------------------------------------


class TestEmployeeMessage:
    @pytest.fixture()
    def context(self, linked_account):
        from humotech.telegram.identity import resolve_account

        return resolve_account(linked_account)

    def test_first_message_opens_a_question(self, context, employee):
        question, created = InboxService().receive(context, text="Как перенести отпуск?")

        assert created is True
        assert question.status == "NEW"
        assert question.unread is True
        assert question.due_at is not None
        assert [m.kind for m in question.messages.order_by("created_at")] == ["SYSTEM", "EMPLOYEE"]

    def test_answer_to_a_waiting_question_resumes_work(self, context, employee):
        question = ask(employee, status="WAITING_EMPLOYEE", awaiting_reply=False)

        same, created = InboxService().receive(context, text="Даты: 3–5 октября")

        assert created is False and same.id == question.id
        question.refresh_from_db()
        assert question.status == "IN_PROGRESS"
        assert question.unread is True
        assert question.messages.filter(event="RESUMED").exists()

    def test_recently_closed_question_is_reopened(self, context, employee):
        question = ask(
            employee, status="CLOSED", closed_at=timezone.now() - timedelta(days=1),
            close_reason="Решено",
        )

        same, created = InboxService().receive(context, text="А ещё вопрос")

        assert created is False and same.id == question.id
        question.refresh_from_db()
        assert question.status == "NEW"
        assert question.messages.filter(event="REOPENED").exists()

    def test_long_closed_question_stays_closed(self, context, employee):
        old = ask(
            employee, status="CLOSED", closed_at=timezone.now() - timedelta(days=10),
            last_message_at=timezone.now() - timedelta(days=10),
        )

        question, created = InboxService().receive(context, text="Новый вопрос")

        assert created is True and question.id != old.id
        old.refresh_from_db()
        assert old.status == "CLOSED"

    def test_the_same_telegram_message_is_not_duplicated(self, context, employee):
        InboxService().receive(context, text="Привет", telegram_message_id=42)
        InboxService().receive(context, text="Привет", telegram_message_id=42)

        assert QuestionMessage.objects.filter(kind="EMPLOYEE").count() == 1

    def test_bot_endpoint(self, bot_client, linked_account):
        response = bot_client.post(
            f"{API}/me/questions/messages",
            {"text": "Не сохранилась отметка выхода", "telegram_message_id": 7},
            format="json", **bot_headers(),
        )

        assert response.status_code == 201
        body = response.json()
        assert body["created"] is True
        assert EmployeeQuestion.objects.get(id=body["question_id"]).number == body["number"]


# --- контекст ----------------------------------------------------------------


class TestContext:
    def test_sections_without_permission_are_null(
        self, api_client, make_user, organization, employee
    ):
        question = ask(employee)
        api_client.force_authenticate(user=make_user(organization, permissions=READ))

        body = api_client.get(f"{URL}/{question.id}/context/").json()

        assert body["requests"] is None
        assert body["balance"] is None
        assert body["corrections"] is None
        assert body["employee"]["full_name"] == "Иванов Иван"
        assert body["history"]["total"] == 1

    def test_history_counts_the_employee_questions(self, hr, employee):
        current = ask(employee, "Текущий")
        ask(employee, "Старый", status="CLOSED", closed_at=timezone.now())

        body = hr.get(f"{URL}/{current.id}/context/").json()

        assert body["history"]["total"] == 2
        assert body["history"]["closed"] == 1
        assert [one["topic"] for one in body["history"]["recent"]] == ["Старый"]


# --- «в работу» без передачи -------------------------------------------------


class TestStart:
    def test_start_keeps_the_assignee(self, hr, make_user, organization, employee):
        colleague = make_user(organization, permissions=ANSWER)
        question = ask(employee, assigned_to_user=colleague)

        body = hr.post(f"{URL}/{question.id}/start/").json()

        # Выбрать статус — не значит забрать чужое обращение себе.
        assert body["status"] == "IN_PROGRESS"
        assert body["assignee"]["id"] == str(colleague.id)
        started = [m for m in body["messages"] if m["event"] == "STARTED"]
        assert len(started) == 1
        assert started[0]["author"]["id"] == str(hr.user.id)
        assert AuditLog.objects.filter(entity_id=question.id, action="question.start").exists()

    def test_waiting_question_goes_back_to_work(self, hr, employee):
        question = ask(employee, status="WAITING_EMPLOYEE")

        body = hr.post(f"{URL}/{question.id}/start/").json()

        assert body["status"] == "IN_PROGRESS"
        assert body["actions"]["start"] is False

    def test_closed_question_is_reopened_not_started(self, hr, employee):
        question = ask(employee, status="CLOSED", closed_at=timezone.now())

        response = hr.post(f"{URL}/{question.id}/start/")

        assert response.status_code == 409


# --- файл к ответу -----------------------------------------------------------


def blank(name="Бланк заявления.pdf"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, b"%PDF-1.4\nblank form", content_type="application/pdf")


class TestReplyFile:
    def test_file_without_text_is_a_reply(self, hr, employee, linked_account):
        question = ask(employee)

        response = hr.post(
            f"{URL}/{question.id}/reply/",
            {"file": blank(), "client_request_id": "file-1"}, format="multipart",
        )

        assert response.status_code == 200, response.content
        reply = [m for m in response.json()["messages"] if m["kind"] == "HR"]
        assert reply[0]["body"] == ""
        assert reply[0]["attachment"]["name"] == "Бланк заявления.pdf"
        # Бот забирает файл по сообщению, а не по обращению.
        notification = Notification.objects.get()
        assert notification.notification_type == "question.reply.file"
        assert str(notification.related_entity_id) == reply[0]["id"]
        assert notification.body.startswith(f"💬 Ответ HR по обращению №{question.number}")

    def test_empty_reply_without_file_is_refused(self, hr, employee, linked_account):
        question = ask(employee)

        response = hr.post(f"{URL}/{question.id}/reply/", {"text": "  "}, format="json")

        assert response.status_code == 400
        assert not Notification.objects.exists()

    def test_hr_downloads_the_file(self, hr, employee, linked_account):
        question = ask(employee)
        body = hr.post(
            f"{URL}/{question.id}/reply/",
            {"text": "Заполните бланк", "file": blank()}, format="multipart",
        ).json()
        message = next(m for m in body["messages"] if m["kind"] == "HR")

        response = hr.get(f"{URL}/{question.id}/messages/{message['id']}/file/")

        assert response.status_code == 200
        assert b"".join(response.streaming_content).startswith(b"%PDF")

    def test_bot_gets_only_the_employees_own_file(
        self, hr, bot_client, employee, other_employee, linked_account,
    ):
        mine = ask(employee)
        body = hr.post(
            f"{URL}/{mine.id}/reply/", {"file": blank()}, format="multipart",
        ).json()
        message = next(m for m in body["messages"] if m["kind"] == "HR")

        own = bot_client.get(
            f"{API}/me/questions/replies/{message['id']}/file", **bot_headers(),
        )
        assert own.status_code == 200
        assert "filename*=UTF-8''" in own["Content-Disposition"]

        theirs = ask(other_employee)
        other = hr.post(
            f"{URL}/{theirs.id}/reply/", {"file": blank()}, format="multipart",
        )
        # У другого сотрудника нет Telegram — ответ не ушёл, файла нет.
        assert other.status_code == 409


# --- где сотрудник сегодня ---------------------------------------------------


class TestToday:
    def test_without_attendance_right_there_is_no_answer(self, hr, employee):
        question = ask(employee)

        body = hr.get(f"{URL}/{question.id}/context/").json()

        assert body["today"] is None

    def test_with_attendance_right_the_state_comes_from_attendance(
        self, api_client, make_user, organization, employee,
    ):
        from humotech.attendance.hr import PRESENCE_STATES

        question = ask(employee)
        api_client.force_authenticate(
            user=make_user(organization, permissions=ANSWER + ("attendance.read",)),
        )

        today = api_client.get(f"{URL}/{question.id}/context/").json()["today"]

        assert today is not None
        assert today["state"] in PRESENCE_STATES
        assert today["first_entry_at"] is None
