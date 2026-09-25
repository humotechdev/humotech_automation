"""Опрос целиком: HR создаёт → офис → отправка → бот → сотрудник → HR.

Отдельно от `test_surveys.py`: там проверяются правила по одному, здесь —
что они соединяются. Между правильной службой и работающим опросом стоит
ещё четыре слоя: права, маршруты, сериализаторы, очередь отправки и
подпись Telegram. Каждый из них умеет сломаться молча.

Путь проходится настоящим: HTTP-запросы по настоящим адресам, очередь
забирается тем же вызовом, что и ботом, а сотрудник входит по подписанной
строке `initData`, а не подстановкой в состояние.
"""

from __future__ import annotations

import pytest

from django_tests.conftest import bot_headers, build_init_data, link_telegram
from humotech.notifications.outbox import claim
from humotech.surveys.services import INVITE_TYPE, THANKS_TYPE

API = "/api/v1"


@pytest.fixture()
def hr_client(api_client, make_user, organization):
    """Кадровик, которому разрешены опросы, — с сессией."""
    user = make_user(
        organization,
        permissions=(
            "surveys.read", "surveys.manage", "offices.read", "employees.read",
        ),
    )
    api_client.force_authenticate(user=user)
    return api_client


@pytest.mark.django_db
def test_hr_asks_an_office_and_reads_the_answers_by_name(
    hr_client, bot_client, employee, office, telegram_settings,
):
    """Сценарий из задания, без единого шага в обход.

    1. HR создаёт шаблон с тремя видами вопросов.
    2. Выбирает офис и отправляет.
    3. Бот забирает ОДНО сообщение с кнопкой.
    4. Сотрудник открывает Mini App и отвечает.
    5. HR видит его ответы — с именем, офисом и отделом.
    """
    link_telegram(employee)

    # 1. Шаблон -------------------------------------------------------------
    made = hr_client.post(
        f"{API}/surveys/templates/",
        {
            "title": "Пульс-опрос",
            "description": "Короткий опрос о работе",
            "questions": [
                {"text": "Насколько комфортно в команде?", "kind": "SCALE"},
                {
                    "text": "Что помогает в работе?",
                    "kind": "MULTI",
                    "options": ["Коллеги", "График", "Задачи"],
                },
                {
                    "text": "Что бы вы изменили?",
                    "kind": "TEXT",
                    "is_required": False,
                },
            ],
        },
        format="json",
    )
    assert made.status_code == 201, made.content
    template = made.json()
    assert [one["position"] for one in template["questions"]] == [1, 2, 3]

    # 2. Рассылка на офис ----------------------------------------------------
    sent = hr_client.post(
        f"{API}/surveys/campaigns/",
        {
            "template_id": template["id"],
            "audience_kind": "OFFICE",
            "audience_ids": [str(office.id)],
            "send_now": True,
        },
        format="json",
    )
    assert sent.status_code == 201, sent.content
    campaign = sent.json()
    assert campaign["status"] == "ACTIVE"

    # 3. Очередь: одно сообщение с кнопкой -----------------------------------
    batch = claim(limit=10)
    invites = [one for one in batch if one.notification_type == INVITE_TYPE]
    assert len(invites) == 1
    invite = invites[0]
    assert "Пульс-опрос" in invite.text
    assert "2–3 минуты" in invite.text
    # Вопросы в чат не сыплются: их показывает Mini App.
    for question in template["questions"]:
        assert question["text"] not in invite.text
    # Ссылка на опрос есть — по ней бот строит кнопку «Пройти опрос».
    assert invite.related_entity_id

    recipient_id = invite.related_entity_id

    # 4. Сотрудник: вход по подписанной строке и ответы -----------------------
    token = bot_client.post(
        f"{API}/telegram/mini-app/auth",
        {"init_data": build_init_data()},
        format="json",
    )
    assert token.status_code == 200, token.content
    bot_client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {token.json()['access_token']}"
    )

    mine = bot_client.get(f"{API}/me/surveys")
    assert mine.status_code == 200, mine.content
    assert [one["id"] for one in mine.json()["items"]] == [recipient_id]

    opened = bot_client.get(f"{API}/me/surveys/{recipient_id}")
    assert opened.status_code == 200, opened.content
    survey = opened.json()
    assert survey["title"] == "Пульс-опрос"
    assert len(survey["questions"]) == 3
    # Открытие отмечено: HR отличит «не открывал» от «бросил на середине».
    assert survey["status"] == "STARTED"

    by_kind = {one["kind"]: one for one in survey["questions"]}
    answered = bot_client.post(
        f"{API}/me/surveys/{recipient_id}",
        {
            "answers": [
                {"question_id": by_kind["SCALE"]["id"], "number": 5},
                {"question_id": by_kind["MULTI"]["id"],
                 "options": ["Коллеги", "Задачи"]},
                {"question_id": by_kind["TEXT"]["id"],
                 "text": "Больше времени на задачи"},
            ]
        },
        format="json",
    )
    assert answered.status_code == 200, answered.content
    assert answered.json()["status"] == "COMPLETED"

    # Бот говорит спасибо — отдельным сообщением, а не молча.
    thanks = [
        one for one in claim(limit=10)
        if one.notification_type == THANKS_TYPE
    ]
    assert len(thanks) == 1
    assert "Спасибо" in thanks[0].text

    # 5. HR видит ответы этого человека --------------------------------------
    seen = hr_client.get(f"{API}/surveys/campaigns/{campaign['id']}/answers/")
    assert seen.status_code == 200, seen.content
    rows = seen.json()["items"]
    assert len(rows) == 1

    row = rows[0]
    # Опрос именной: фамилия, офис и отдел стоят рядом с ответами.
    assert row["full_name"] == "Иванов Иван"
    assert row["office_name"] == office.name
    assert row["completed_at"]

    answers = {one["question_text"]: one for one in row["answers"]}
    assert answers["Насколько комфортно в команде?"]["number"] == 5
    assert answers["Что помогает в работе?"]["options"] == ["Коллеги", "Задачи"]
    assert answers["Что бы вы изменили?"]["text"] == "Больше времени на задачи"

    # Список рассылок несёт то же число, что и карточка: два разных
    # ответа на «сколько прошли» кадровик читает как поломку.
    listed = hr_client.get(f"{API}/surveys/campaigns/")
    assert listed.status_code == 200, listed.content
    mine_row = next(
        one for one in listed.json()["items"] if one["id"] == campaign["id"]
    )
    assert mine_row["total"] == 1
    assert mine_row["done"] == 1
    assert mine_row["template_title"] == "Пульс-опрос"

    # Шаблоны читаются поиском по названию.
    found = hr_client.get(f"{API}/surveys/templates/?search=Пульс")
    assert found.status_code == 200, found.content
    assert [one["title"] for one in found.json()["items"]] == ["Пульс-опрос"]

    # И сводка рядом — она ответы не заменяет.
    summary = hr_client.get(f"{API}/surveys/campaigns/{campaign['id']}/summary/")
    assert summary.status_code == 200, summary.content
    body = summary.json()
    # Пропущенные считаются отдельно и не входят в достижимых:
    # «прошли 6 из 10» при двух, до кого опрос не дошёл, занижает
    # результат и ставит кадровику не тот вопрос.
    assert body["progress"] == {
        "total": 1, "reachable": 1, "sent": 1, "started": 1,
        "completed": 1, "skipped": 0, "expired": 0,
    }
    scale = next(one for one in body["questions"] if one["kind"] == "SCALE")
    assert scale["average"] == 5.0
    assert body["offices"][0]["name"] == office.name


@pytest.mark.django_db
def test_another_employee_cannot_open_someone_elses_survey(
    hr_client, bot_client, employee, office, organization, telegram_settings,
):
    """Чужой опрос не открывается подстановкой идентификатора в адрес."""
    from datetime import date

    from humotech.employees.models import Employee, EmployeeAssignment

    link_telegram(employee)
    stranger = Employee.objects.create(
        organization=organization,
        employee_number="EMP-9001",
        first_name="Пётр",
        last_name="Петров",
        hire_date=date(2024, 3, 1),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization,
        employee=stranger,
        office=office,
        employment_type="FULL_TIME",
        work_mode="ONSITE",
        is_primary=True,
        valid_from=date(2024, 3, 1),
    )
    link_telegram(stranger, telegram_user_id=777_000_222, username="petr")

    template = hr_client.post(
        f"{API}/surveys/templates/",
        {
            "title": "Один вопрос",
            "questions": [{"text": "Как дела?", "kind": "SCALE"}],
        },
        format="json",
    ).json()
    campaign = hr_client.post(
        f"{API}/surveys/campaigns/",
        {
            "template_id": template["id"],
            "audience_kind": "EMPLOYEES",
            "audience_ids": [str(employee.id)],
            "send_now": True,
        },
        format="json",
    ).json()

    recipients = hr_client.get(
        f"{API}/surveys/campaigns/{campaign['id']}/recipients/"
    ).json()["items"]
    mine = recipients[0]["id"]

    token = bot_client.post(
        f"{API}/telegram/mini-app/auth",
        {"init_data": build_init_data(telegram_user_id=777_000_222,
                                      username="petr")},
        format="json",
    )
    assert token.status_code == 200, token.content
    bot_client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {token.json()['access_token']}"
    )

    # Чужая строка отвечает как отсутствие записи, а не «нет доступа»:
    # так по ответу нельзя узнать, что такой опрос вообще существует.
    assert bot_client.get(f"{API}/me/surveys/{mine}").status_code == 404
    assert bot_client.post(
        f"{API}/me/surveys/{mine}", {"answers": []}, format="json",
    ).status_code == 404


@pytest.mark.django_db
def test_survey_needs_permission(api_client, make_user, organization):
    """Без права `surveys.read` ответы не отдаются."""
    nobody = make_user(organization, permissions=("employees.read",))
    api_client.force_authenticate(user=nobody)

    assert api_client.get(f"{API}/surveys/templates/").status_code == 403
    assert api_client.get(f"{API}/surveys/campaigns/").status_code == 403


@pytest.mark.django_db
def test_employee_without_telegram_is_not_asked(
    hr_client, employee, office, organization,
):
    """Строка получателя есть, а сообщение никому не уходит.

    Это не потеря: HR должен видеть, что человек в круге опроса, и что
    уведомление до него не дошло. Пустой список получателей выглядел бы
    так, будто его и не собирались спрашивать.
    """
    template = hr_client.post(
        f"{API}/surveys/templates/",
        {"title": "Тихий", "questions": [{"text": "Как дела?", "kind": "SCALE"}]},
        format="json",
    ).json()
    campaign = hr_client.post(
        f"{API}/surveys/campaigns/",
        {
            "template_id": template["id"],
            "audience_kind": "ALL",
            "send_now": True,
        },
        format="json",
    ).json()

    rows = hr_client.get(
        f"{API}/surveys/campaigns/{campaign['id']}/recipients/"
    ).json()["items"]
    assert len(rows) == 1
    assert rows[0]["employee_id"] == str(employee.id)

    # Очередь отказывается от строки: живого адресата у неё нет.
    assert claim(limit=10) == []


@pytest.mark.django_db
def test_scheduled_survey_is_sent_when_the_bot_asks_for_the_queue(
    hr_client, bot_client, employee, office, telegram_settings,
):
    """Запланированный опрос уходит сам, без отдельного планировщика.

    Срок наступает — и первый же запрос бота за очередью его отправляет.
    Без этой связки рассылка с датой лежала бы вечно, а узнали бы об
    этом по ненаступившему опросу.
    """
    from datetime import timedelta

    from django.utils import timezone

    link_telegram(employee)

    template = hr_client.post(
        f"{API}/surveys/templates/",
        {"title": "По расписанию",
         "questions": [{"text": "Как дела?", "kind": "SCALE"}]},
        format="json",
    ).json()
    campaign = hr_client.post(
        f"{API}/surveys/campaigns/",
        {
            "template_id": template["id"],
            "audience_kind": "ALL",
            "scheduled_at": (
                timezone.now() + timedelta(minutes=1)
            ).isoformat(),
        },
        format="json",
    ).json()
    assert campaign["status"] == "SCHEDULED"

    # Срока ещё нет — очередь пуста.
    early = bot_client.get(f"{API}/telegram/bot/outbox", **bot_headers())
    assert early.status_code == 200, early.content
    assert early.json()["messages"] == []

    # Срок наступил: тот же запрос бота и отправляет, и забирает.
    campaign_row = _campaign(campaign["id"])
    campaign_row.next_send_at = timezone.now() - timedelta(seconds=1)
    campaign_row.save(update_fields=["next_send_at"])

    due = bot_client.get(f"{API}/telegram/bot/outbox", **bot_headers())
    assert due.status_code == 200, due.content
    messages = due.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["type"] == INVITE_TYPE
    # Ссылка на опрос доезжает до бота: по ней он строит кнопку.
    assert messages[0]["entity_id"]


def _campaign(campaign_id):
    from humotech.surveys.models import SurveyCampaign

    return SurveyCampaign.objects.get(id=campaign_id)
