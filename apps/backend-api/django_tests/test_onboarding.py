"""Первичное ознакомление: от ссылки кадровика до открытого меню бота.

Проверяется главное свойство раздела — то, ради которого он вообще
устроен именно так: **завершение вычисляется, а не хранится**. Отсюда
два теста, которые легко забыть и без которых всё остальное бессмысленно:

  * публикация новой редакции обязательного документа ЗАКРЫВАЕТ бота
    тому, кто уже всё прошёл, — и просит подтвердить только новый текст,
    а не перечитывать десять карточек;
  * сотрудник, которого в программу не звали, не закрыт вовсе.

И третье, не менее важное: **ознакомление ничего не закрывает.**
Проверка «пока не дочитал — нельзя» здесь стояла и была снята: она
означала, что новичок в первый день не отметится на входе. Тесты
доступа теперь проверяют обратное — что рабочие экраны открыты и
непрошедшему, и отказавшемуся.

Остальное — порядок шагов, идемпотентность двойного нажатия и
напоминания, которыми дело доводится до конца вместо запертой двери.
"""

from __future__ import annotations

from datetime import date

import pytest
from rest_framework.test import APIClient

from django_tests.conftest import HR_FULL_PERMISSIONS, TEST_BOT_SECRET
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.onboarding import progress as progress_module
from humotech.onboarding.models import (
    EmployeeOnboarding,
    EmployeePolicyAcceptance,
    OnboardingSection,
    PolicyDocument,
    PolicyDocumentVersion,
)
from humotech.onboarding.services import (
    OnboardingService,
    PolicyService,
    seed_organization,
)
from humotech.telegram.models import TelegramAccount

pytestmark = pytest.mark.django_db

API = "/api/v1"
BOT_HEADERS = {"HTTP_X_BOT_TOKEN": TEST_BOT_SECRET}
ONBOARDING_PERMISSIONS = HR_FULL_PERMISSIONS + (
    "telegram.read", "telegram.manage",
    "onboarding.read", "onboarding.manage", "policies.publish",
)
TELEGRAM_ID = 880_001


# --- обстановка -------------------------------------------------------------


@pytest.fixture()
def content(organization):
    """Стартовые десять карточек и три документа."""
    return seed_organization(organization.id)


@pytest.fixture()
def hr(make_user, organization, telegram_settings):
    client = APIClient()
    client.force_authenticate(
        user=make_user(organization, permissions=ONBOARDING_PERMISSIONS)
    )
    return client


@pytest.fixture()
def hr_actor_full(make_actor, organization, telegram_settings):
    return make_actor(organization, permissions=ONBOARDING_PERMISSIONS)


def bot(api_client, method, path, payload=None, telegram_id=TELEGRAM_ID):
    """Запрос бота за сотрудника: общий секрет плюс подтверждённый Telegram."""
    headers = {**BOT_HEADERS, "HTTP_X_TELEGRAM_USER_ID": str(telegram_id)}
    if method == "get":
        return api_client.get(f"{API}{path}", **headers)
    return api_client.post(f"{API}{path}", payload or {}, format="json", **headers)


def link_employee(employee, telegram_id=TELEGRAM_ID) -> TelegramAccount:
    """Рабочая привязка. Дорога к ней проверяется отдельно, в тестах Telegram."""
    from django.utils import timezone

    return TelegramAccount.objects.create(
        organization_id=employee.organization_id,
        employee=employee,
        telegram_user_id=telegram_id,
        telegram_chat_id=telegram_id,
        status="ACTIVE",
        connected_at=timezone.now(),
    )


def walk_sections(api_client, employee, telegram_id=TELEGRAM_ID) -> dict:
    """Пройти все информационные карточки по порядку."""
    state = bot(api_client, "post", "/me/onboarding/start", telegram_id=telegram_id).json()
    while state["section"] is not None:
        response = bot(
            api_client, "post", "/me/onboarding/acknowledge",
            {"section_id": state["section"]["id"]}, telegram_id=telegram_id,
        )
        assert response.status_code == 200, response.json()
        state = response.json()
    return state


def accept_policies(api_client, state, telegram_id=TELEGRAM_ID) -> dict:
    while state["policy"] is not None:
        response = bot(
            api_client, "post", "/me/onboarding/decision",
            {"version_id": state["policy"]["version_id"], "decision": "ACCEPTED"},
            telegram_id=telegram_id,
        )
        assert response.status_code == 200, response.json()
        state = response.json()
    return state


# --- наполнение -------------------------------------------------------------


def test_seed_creates_ten_sections_and_three_published_documents(
    organization, content
):
    assert OnboardingSection.objects.filter(
        organization_id=organization.id, archived_at__isnull=True
    ).count() == 10
    assert PolicyDocument.objects.filter(organization_id=organization.id).count() == 3
    assert PolicyDocumentVersion.objects.filter(
        organization_id=organization.id, status="PUBLISHED"
    ).count() == 3


def test_seed_is_idempotent_and_keeps_edited_text(organization, content):
    section = OnboardingSection.objects.get(
        organization_id=organization.id, position=1
    )
    section.title = "Переписано кадровиком"
    section.save(update_fields=["title", "updated_at"])

    seed_organization(organization.id)

    section.refresh_from_db()
    assert section.title == "Переписано кадровиком"
    assert OnboardingSection.objects.filter(
        organization_id=organization.id
    ).count() == 10


# --- приглашение ------------------------------------------------------------


def test_invitation_link_carries_the_onboarding_prefix(hr, employee, content):
    response = hr.post(f"{API}/employees/{employee.id}/onboarding/invite")

    assert response.status_code == 201, response.json()
    body = response.json()
    assert "?start=onboarding_" in body["link"]
    assert EmployeeOnboarding.objects.filter(employee_id=employee.id).exists()


def test_invitation_lives_a_week_not_a_day(hr, employee, content, telegram_settings):
    from django.utils import timezone

    response = hr.post(f"{API}/employees/{employee.id}/onboarding/invite")
    expires = response.json()["expires_at"]

    from datetime import datetime

    left = datetime.fromisoformat(expires) - timezone.now()
    # Неделя, а не сутки: ссылку отправляют до выхода на работу.
    # Сравнение в часах: «семь суток минус миллисекунда» — это шесть
    # полных дней, и проверка по `.days` ловила бы округление, а не срок.
    assert 167 <= left.total_seconds() / 3600 <= 168


def test_invite_to_an_already_linked_person_enrols_without_a_link(
    hr, employee, content
):
    """Ссылка существует, чтобы привязку СОЗДАТЬ.

    У того, кто уже пользуется ботом, её выдавать не из чего: раньше
    здесь был тупик — кадровик жал «Отправить ознакомление» и получал
    «сначала отключите Telegram», хотя отключать ничего не требовалось.
    """
    link_employee(employee)

    response = hr.post(f"{API}/employees/{employee.id}/onboarding/invite")

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["linked"] is True
    assert body["link"] is None
    # Главное: в программу человек всё равно включён.
    assert EmployeeOnboarding.objects.filter(employee_id=employee.id).exists()


def test_expired_link_is_not_shown_as_live(hr, employee, content):
    """«Действует до» о вчерашней ссылке — обещание, которого нет.

    Срок держится на `expires_at`, а не на статусе: строку переводят
    в EXPIRED, когда на неё натыкаются. Список только читает, поэтому
    поправку делает он сам.
    """
    from datetime import timedelta

    from django.utils import timezone

    from humotech.telegram.models import TelegramLinkInvitation

    hr.post(f"{API}/employees/{employee.id}/onboarding/invite")
    TelegramLinkInvitation.objects.filter(employee_id=employee.id).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    row = hr.get(f"{API}/onboarding/progress").json()["items"][0]

    assert row["invitation_status"] == "EXPIRED"


def test_reminder_refuses_when_there_is_no_chat(hr, employee, content):
    """Бот не может написать первым — это правило Telegram.

    Отказ с причиной честнее молчания: без него уведомление ушло бы в
    очередь и было бы снято там как недоставляемое, а кадровик считал
    бы, что напомнил.
    """
    hr.post(f"{API}/employees/{employee.id}/onboarding/invite")

    response = hr.post(f"{API}/employees/{employee.id}/onboarding/remind")

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "not_linked"


def test_reminder_queues_a_message_when_telegram_is_linked(hr, employee, content):
    from humotech.notifications.models import Notification

    hr.post(f"{API}/employees/{employee.id}/onboarding/invite")
    link_employee(employee)

    response = hr.post(f"{API}/employees/{employee.id}/onboarding/remind")

    assert response.status_code == 200, response.json()
    assert Notification.objects.filter(
        employee_id=employee.id, notification_type="onboarding.reminder"
    ).count() == 1

    # Второе нажатие в тот же день — то же самое напоминание.
    hr.post(f"{API}/employees/{employee.id}/onboarding/remind")
    assert Notification.objects.filter(
        employee_id=employee.id, notification_type="onboarding.reminder"
    ).count() == 1


# --- гейт -------------------------------------------------------------------


def test_employee_outside_the_programme_works_as_before(
    api_client, employee, content, telegram_settings
):
    """Появление раздела ничего не меняет тем, кто работает давно."""
    link_employee(employee)

    assert bot(api_client, "get", "/me/status").status_code == 200
    profile = bot(api_client, "get", "/me/profile").json()
    assert profile["onboarding"]["enrolled"] is False
    assert profile["onboarding"]["completed"] is True


def test_work_endpoints_stay_open_while_onboarding_is_unfinished(
    api_client, hr_actor_full, employee, content, telegram_settings
):
    """Главная проверка раздела.

    Новичок отмечается на входе в первое же утро — до того, как прочёл
    хоть одну карточку. Ознакомление доводится напоминанием, а не
    запертой дверью.
    """
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)

    for path in ("/me/status", "/me/profile", "/me/absences",
                 "/me/statistics?period=today"):
        assert bot(api_client, "get", path).status_code == 200, path


def test_profile_carries_the_progress_for_the_reminder(
    api_client, hr_actor_full, employee, content, telegram_settings
):
    """Из этого блока бот собирает «Пройти ознакомление: 3 из 10»."""
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)

    response = bot(api_client, "get", "/me/profile")

    assert response.status_code == 200
    block = response.json()["onboarding"]
    assert block["enrolled"] is True
    assert block["required"] is True
    assert block["stage"] == "SECTIONS"
    assert block["sections_total"] == 10
    assert block["policies_total"] == 3


# --- шаги сотрудника --------------------------------------------------------


def test_sections_are_walked_in_order_and_jumping_ahead_is_refused(
    api_client, hr_actor_full, employee, content, telegram_settings
):
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)

    state = bot(api_client, "post", "/me/onboarding/start").json()
    assert state["section"]["position"] == 1
    assert state["section"]["button_label"] == "Я ознакомился"

    far = OnboardingSection.objects.get(
        organization_id=employee.organization_id, position=7
    )
    refused = bot(
        api_client, "post", "/me/onboarding/acknowledge", {"section_id": str(far.id)}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["details"]["expected_position"] == 1


def test_double_tap_on_the_same_button_changes_nothing(
    api_client, hr_actor_full, employee, content, telegram_settings
):
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)

    state = bot(api_client, "post", "/me/onboarding/start").json()
    first = state["section"]["id"]

    once = bot(
        api_client, "post", "/me/onboarding/acknowledge", {"section_id": first}
    ).json()
    twice = bot(
        api_client, "post", "/me/onboarding/acknowledge", {"section_id": first}
    ).json()

    assert once["sections_done"] == 1
    assert twice["sections_done"] == 1


def test_already_read_section_comes_back_with_its_confirmation(
    api_client, hr_actor_full, employee, content, telegram_settings
):
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    state = bot(api_client, "post", "/me/onboarding/start").json()
    bot(
        api_client, "post", "/me/onboarding/acknowledge",
        {"section_id": state["section"]["id"]},
    )

    again = bot(api_client, "get", "/me/onboarding/sections/1").json()

    assert again["acknowledged_at"] is not None
    assert again["acknowledged_version"] == 1


def test_documents_are_refused_before_the_cards_are_read(
    api_client, hr_actor_full, employee, content, telegram_settings
):
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    bot(api_client, "post", "/me/onboarding/start")
    version = PolicyDocumentVersion.objects.filter(
        organization_id=employee.organization_id, status="PUBLISHED"
    ).first()

    refused = bot(
        api_client, "post", "/me/onboarding/decision",
        {"version_id": str(version.id), "decision": "ACCEPTED"},
    )

    assert refused.status_code == 409
    assert refused.json()["error"]["details"]["sections_done"] == 0


def test_full_walk_ends_in_completed(
    api_client, hr_actor_full, employee, content, telegram_settings
):
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)

    state = walk_sections(api_client, employee)
    assert state["sections_done"] == 10
    assert state["stage"] == "POLICIES"
    assert state["status"] == "INFO_COMPLETED"

    state = accept_policies(api_client, state)
    assert state["completed"] is True
    assert state["status"] == "COMPLETED"
    assert state["policies_done"] == 3

    # Доступ был открыт и до этого — проверка на то, что он не
    # «включился» сейчас, а был всё время.
    assert bot(api_client, "get", "/me/status").status_code == 200


def test_declining_a_document_is_recorded_but_takes_nothing_away(
    api_client, hr_actor_full, employee, content, telegram_settings
):
    """Отказ — повод для разговора с кадровиком, а не для блокировки.

    Человек, нажавший «Не согласен», продолжает отмечаться и подавать
    заявки; кадровик видит его среди требующих внимания и приходит
    разбираться сам.
    """
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    state = walk_sections(api_client, employee)

    refused = bot(
        api_client, "post", "/me/onboarding/decision",
        {"version_id": state["policy"]["version_id"], "decision": "DECLINED"},
    ).json()

    assert refused["status"] == "BLOCKED_BY_DECLINED_POLICY"
    assert refused["has_declined"] is True
    assert EmployeePolicyAcceptance.objects.filter(
        employee_id=employee.id, decision="DECLINED"
    ).count() == 1

    assert bot(api_client, "get", "/me/status").status_code == 200


def test_changing_your_mind_updates_the_same_decision(
    api_client, hr_actor_full, employee, content, telegram_settings
):
    """Отказ и последующее согласие — одно решение, которое изменилось."""
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    state = walk_sections(api_client, employee)
    version_id = state["policy"]["version_id"]

    bot(
        api_client, "post", "/me/onboarding/decision",
        {"version_id": version_id, "decision": "DECLINED"},
    )
    bot(
        api_client, "post", "/me/onboarding/decision",
        {"version_id": version_id, "decision": "ACCEPTED"},
    )

    rows = EmployeePolicyAcceptance.objects.filter(
        employee_id=employee.id, version_id=version_id
    )
    assert rows.count() == 1
    assert rows.first().decision == "ACCEPTED"


def test_decline_shows_up_in_the_hr_feed(
    api_client, hr, hr_actor_full, employee, content, telegram_settings
):
    """«HR получает немедленное уведомление» — это событие ленты.

    Своей таблицы у ленты нет: она читает те же строки, поэтому событие
    появляется той же транзакцией, что и сам отказ.
    """
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    state = walk_sections(api_client, employee)
    bot(
        api_client, "post", "/me/onboarding/decision",
        {"version_id": state["policy"]["version_id"], "decision": "DECLINED"},
    )

    feed = hr.get(f"{API}/notification-feed").json()
    kinds = [one["type"] for one in feed["items"]]

    assert "policy_declined" in kinds
    found = next(one for one in feed["items"] if one["type"] == "policy_declined")
    assert found["requires_action"] is True
    assert found["priority"] == "CRITICAL"


# --- новая редакция ---------------------------------------------------------


def test_new_version_reopens_only_the_document(
    api_client, hr, hr_actor_full, employee, content, telegram_settings
):
    """Главный тест раздела.

    Выпуск новой редакции обязан вернуть к подтверждению того, кто уже
    всё прошёл, — и попросить подтвердить ТОЛЬКО новый текст. Если бы
    состояние читалось из колонки `status`, человек остался бы
    COMPLETED и ничего бы не заметил.

    Доступа при этом он не теряет: обновившийся документ — повод
    напомнить и показать кадровику, а не отобрать отметку присутствия.
    """
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    state = accept_policies(api_client, walk_sections(api_client, employee))
    assert state["completed"] is True

    document = PolicyDocument.objects.get(
        organization_id=employee.organization_id, code="LABOUR_RULES"
    )
    made = hr.post(
        f"{API}/onboarding/documents/{document.id}/versions/",
        {"version": "2.0", "summary": "Новая редакция", "body": "Текст",
         "agree_label": "С правилами согласен"},
        format="json",
    )
    assert made.status_code == 201, made.json()
    published = hr.post(f"{API}/onboarding/versions/{made.json()['id']}/publish")
    assert published.status_code == 200, published.json()

    # Бот работает как работал, но состояние сменилось на «требуется».
    assert bot(api_client, "get", "/me/status").status_code == 200

    state = bot(api_client, "get", "/me/onboarding").json()
    assert state["status"] == "UPDATE_REQUIRED"
    assert state["completed"] is False
    # Карточки перечитывать не просят.
    assert state["sections_done"] == 10
    assert state["info_completed"] is True
    assert state["policies_done"] == 2
    assert state["policy"]["version"] == "2.0"

    # Кадровик видит это отдельным состоянием, а не «в процессе»:
    # человек своё сделал, и просят у него теперь другое.
    row = hr.get(f"{API}/onboarding/progress").json()["items"][0]
    assert row["status"] == "UPDATE_REQUIRED"

    # Прежнее согласие с версией 1.0 не стёрто: это состоявшийся факт.
    assert EmployeePolicyAcceptance.objects.filter(
        employee_id=employee.id, version__version="1.0", decision="ACCEPTED"
    ).count() == 3


def test_old_version_button_no_longer_confirms_anything(
    api_client, hr, hr_actor_full, employee, content, telegram_settings
):
    """Кнопка под старым сообщением в чате живёт вечно — и не должна работать."""
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    state = walk_sections(api_client, employee)
    stale_version_id = state["policy"]["version_id"]

    document = PolicyDocument.objects.get(
        organization_id=employee.organization_id,
        id=state["policy"]["document_id"],
    )
    made = hr.post(
        f"{API}/onboarding/documents/{document.id}/versions/",
        {"version": "2.0", "summary": "Новая редакция", "agree_label": "Согласен"},
        format="json",
    ).json()
    hr.post(f"{API}/onboarding/versions/{made['id']}/publish")

    refused = bot(
        api_client, "post", "/me/onboarding/decision",
        {"version_id": stale_version_id, "decision": "ACCEPTED"},
    )

    assert refused.status_code == 409
    assert refused.json()["error"]["details"]["reason"] == "version_outdated"


def test_published_version_cannot_be_edited(hr, organization, content):
    document = PolicyDocument.objects.get(
        organization_id=organization.id, code="ETHICS_CODE"
    )
    live = PolicyDocumentVersion.objects.get(
        document_id=document.id, status="PUBLISHED"
    )

    response = hr.patch(
        f"{API}/onboarding/versions/{live.id}",
        {"summary": "Тихая правка"}, format="json",
    )

    assert response.status_code == 409


def test_publishing_needs_its_own_permission(
    make_user, organization, content, telegram_settings
):
    """Читать прогресс можно многим, публиковать — нет.

    Публикация закрывает бота всем, кто не подтвердил новый текст.
    """
    client = APIClient()
    client.force_authenticate(
        user=make_user(organization, permissions=("onboarding.read",))
    )
    document = PolicyDocument.objects.get(
        organization_id=organization.id, code="PRIVACY_POLICY"
    )

    response = client.post(
        f"{API}/onboarding/documents/{document.id}/versions/",
        {"version": "2.0", "summary": "Текст"}, format="json",
    )

    assert response.status_code == 403


# --- автоматические напоминания ---------------------------------------------


@pytest.fixture()
def quiet_hours(settings):
    """Рабочие часы пошире, чтобы тест не зависел от времени прогона."""
    settings.ONBOARDING = {**settings.ONBOARDING,
                           "REMINDER_FROM_HOUR": 0, "REMINDER_TO_HOUR": 24}
    return settings.ONBOARDING


def test_reminder_goes_out_after_the_grace_period(
    hr_actor_full, employee, content, quiet_hours
):
    """Ознакомление доводится напоминанием, раз уж оно ничего не закрывает."""
    from humotech.notifications.models import Notification
    from humotech.onboarding.reminders import run_once

    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    _age(employee, hours=48)

    assert run_once() == 1
    row = Notification.objects.get(
        employee_id=employee.id, notification_type="onboarding.reminder"
    )
    assert "10 коротких разделов" in row.body


def test_fresh_invitation_is_not_hurried(
    hr_actor_full, employee, content, quiet_hours
):
    """Ссылку могли выдать в конце дня — сутки на то, чтобы открыть."""
    from humotech.onboarding.reminders import run_once

    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)

    assert run_once() == 0


def test_reminder_is_not_repeated_the_next_hour(
    hr_actor_full, employee, content, quiet_hours
):
    from humotech.onboarding.reminders import run_once

    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    _age(employee, hours=48)

    assert run_once() == 1
    assert run_once() == 0


def test_nobody_is_reminded_without_a_chat(
    hr_actor_full, employee, content, quiet_hours
):
    """Бот не может написать первым — это правило Telegram."""
    from humotech.onboarding.reminders import run_once

    OnboardingService().enrol(hr_actor_full, employee.id)
    _age(employee, hours=48)

    assert run_once() == 0


def test_the_one_who_refused_is_left_to_hr(
    api_client, hr_actor_full, employee, content, quiet_hours, telegram_settings
):
    """Отказавшемуся третье уведомление не поможет — поможет разговор."""
    from humotech.onboarding.reminders import run_once

    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    state = walk_sections(api_client, employee)
    bot(api_client, "post", "/me/onboarding/decision",
        {"version_id": state["policy"]["version_id"], "decision": "DECLINED"})
    _age(employee, hours=48)

    assert run_once() == 0


def test_no_reminders_at_night(hr_actor_full, employee, content, settings):
    """«Дочитайте правила» в три ночи — причина отключить бота."""
    from humotech.onboarding.reminders import run_once

    settings.ONBOARDING = {**settings.ONBOARDING,
                           "REMINDER_FROM_HOUR": 10, "REMINDER_TO_HOUR": 10}
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    _age(employee, hours=48)

    assert run_once() == 0


def test_reminder_names_what_is_actually_left(
    api_client, hr_actor_full, employee, content, quiet_hours, telegram_settings
):
    """«Завершите ознакомление» тому, кому остался один документ,
    говорит, что его труд не заметили."""
    from humotech.notifications.models import Notification
    from humotech.onboarding.reminders import run_once

    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    walk_sections(api_client, employee)
    _age(employee, hours=48)

    run_once()
    body = Notification.objects.get(
        employee_id=employee.id, notification_type="onboarding.reminder"
    ).body
    assert "обязательные документы" in body


def test_new_version_reminder_says_so(
    api_client, hr, hr_actor_full, employee, content, quiet_hours,
    telegram_settings,
):
    from humotech.notifications.models import Notification
    from humotech.onboarding.reminders import run_once

    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    accept_policies(api_client, walk_sections(api_client, employee))

    document = PolicyDocument.objects.get(
        organization_id=employee.organization_id, code="LABOUR_RULES"
    )
    made = hr.post(f"{API}/onboarding/documents/{document.id}/versions/",
                   {"version": "2.0", "summary": "Новая", "agree_label": "Да"},
                   format="json").json()
    hr.post(f"{API}/onboarding/versions/{made['id']}/publish")
    _age(employee, hours=48)

    assert run_once() == 1
    body = Notification.objects.get(
        employee_id=employee.id, notification_type="onboarding.reminder"
    ).body
    assert "Обновился обязательный документ" in body


def _age(employee, *, hours: int) -> None:
    """Отодвинуть приглашение в прошлое: напоминание ждёт сутки."""
    from datetime import timedelta

    from django.utils import timezone

    EmployeeOnboarding.objects.filter(employee_id=employee.id).update(
        invited_at=timezone.now() - timedelta(hours=hours)
    )


# --- списки кадровика -------------------------------------------------------


def test_progress_list_shows_counters_and_filters_by_state(
    hr, hr_actor_full, organization, office, employee, content, api_client,
    telegram_settings,
):
    second = Employee.objects.create(
        organization=organization,
        employee_number="EMP-0002",
        first_name="Пётр",
        last_name="Петров",
        hire_date=date(2024, 3, 1),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=second, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=date(2024, 3, 1),
    )
    OnboardingService().enrol(hr_actor_full, employee.id)
    OnboardingService().enrol(hr_actor_full, second.id)
    link_employee(employee)
    accept_policies(api_client, walk_sections(api_client, employee))

    everyone = hr.get(f"{API}/onboarding/progress").json()
    assert len(everyone["items"]) == 2

    done = hr.get(f"{API}/onboarding/progress?status=COMPLETED").json()
    assert [one["employee_id"] for one in done["items"]] == [str(employee.id)]
    assert done["items"][0]["sections_done"] == 10
    assert done["items"][0]["policies_done"] == 3

    counts = hr.get(f"{API}/onboarding/counts").json()
    assert counts["all"] == 2
    assert counts["COMPLETED"] == 1
    assert counts["NOT_STARTED"] == 1


def test_timeline_tells_what_happened_and_when(
    hr, hr_actor_full, employee, content, api_client, telegram_settings
):
    hr.post(f"{API}/employees/{employee.id}/onboarding/invite")
    link_employee(employee)
    accept_policies(api_client, walk_sections(api_client, employee))

    body = hr.get(f"{API}/employees/{employee.id}/onboarding").json()
    kinds = [one["kind"] for one in body["timeline"]]

    assert kinds[0] == "invited"
    assert "started" in kinds
    assert kinds.count("section") == 10
    assert kinds.count("policy") == 3
    assert kinds[-1] == "completed"


def test_pending_list_names_who_has_not_accepted_the_live_version(
    hr, hr_actor_full, employee, content
):
    OnboardingService().enrol(hr_actor_full, employee.id)
    document = PolicyDocument.objects.get(
        organization_id=employee.organization_id, code="LABOUR_RULES"
    )

    body = hr.get(f"{API}/onboarding/documents/{document.id}/pending/").json()

    assert body["total"] == 1
    assert body["items"][0]["employee_id"] == str(employee.id)


def test_export_covers_everyone_in_the_programme(
    hr, hr_actor_full, employee, content
):
    OnboardingService().enrol(hr_actor_full, employee.id)

    body = hr.get(f"{API}/onboarding/export").json()

    assert body["total"] == 1
    assert body["items"][0]["sections"] == "0/10"
    assert body["items"][0]["policies"] == "0/3"


# --- тексты карточек --------------------------------------------------------


def test_editing_a_card_raises_its_version_but_reopens_nothing(
    hr, hr_actor_full, employee, content, api_client, telegram_settings
):
    """Карточка сообщает, а не обязывает.

    Правка текста поднимает редакцию — чтобы через год было видно, что
    человек читал, — но перечитывать заново никого не заставляет.
    Обязывают документы, и версионируются они иначе.
    """
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    accept_policies(api_client, walk_sections(api_client, employee))

    section = OnboardingSection.objects.get(
        organization_id=employee.organization_id, position=3
    )
    response = hr.patch(
        f"{API}/onboarding/sections/{section.id}/",
        {"body": "Обновлённый текст"}, format="json",
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert bot(api_client, "get", "/me/onboarding").json()["completed"] is True


def test_adding_a_section_does_not_kick_out_those_who_finished(
    hr, hr_actor_full, employee, content, api_client, telegram_settings
):
    """Одиннадцатая карточка не отбирает бота у всей компании.

    «Прочитал десять карточек» — утверждение о прошлом, и оно не
    перестаёт быть правдой. Обязывает документ, и только он возвращает
    к подтверждению.
    """
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    accept_policies(api_client, walk_sections(api_client, employee))

    made = hr.post(
        f"{API}/onboarding/sections/",
        {"title": "Новая карточка", "body": "Текст"}, format="json",
    )
    assert made.status_code == 201, made.json()

    state = bot(api_client, "get", "/me/onboarding").json()
    assert state["sections_total"] == 11
    assert state["completed"] is True


def test_adding_a_section_still_applies_to_those_mid_way(
    hr, hr_actor_full, employee, content, api_client, telegram_settings
):
    """Тот, кто ещё читает, получает новую карточку наравне с остальными."""
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    bot(api_client, "post", "/me/onboarding/start")

    hr.post(f"{API}/onboarding/sections/",
            {"title": "Новая карточка", "body": "Текст"}, format="json")

    state = bot(api_client, "get", "/me/onboarding").json()
    assert state["sections_total"] == 11
    assert state["completed"] is False


def test_section_list_is_ordered_and_carries_button_labels(hr, content):
    body = hr.get(f"{API}/onboarding/sections/").json()

    positions = [one["position"] for one in body["items"]]
    assert positions == list(range(1, 11))
    assert body["items"][6]["button_label"] == "С правилами ознакомился"
    assert body["items"][9]["button_label"] == "Завершить ознакомление"


# --- пересчёт напрямую ------------------------------------------------------


def test_status_column_is_a_shop_window_not_the_truth(
    hr_actor_full, employee, content
):
    """Колонка может устареть — пересчёт нет.

    Тест намеренно портит витрину руками и проверяет, что состояние
    считается заново. Именно на этом держится повторное подтверждение
    новых редакций.
    """
    OnboardingService().enrol(hr_actor_full, employee.id)
    EmployeeOnboarding.objects.filter(employee_id=employee.id).update(
        status="COMPLETED"
    )

    assert progress_module.is_unfinished(employee.id) is True
    assert progress_module.of_employee(employee.id).status == "NOT_STARTED"


def test_document_without_a_published_version_requires_nothing(
    hr, hr_actor_full, employee, content, api_client, telegram_settings
):
    """Черновик в CRM не закрывает бота никому.

    Иначе половина компании оставалась бы без доступа, пока юрист правит
    запятую.
    """
    OnboardingService().enrol(hr_actor_full, employee.id)
    link_employee(employee)
    PolicyService().create_document(
        hr_actor_full,
        {"code": "NEW_RULES", "title": "Ещё не выпущено", "position": 9},
    )

    state = bot(api_client, "get", "/me/onboarding").json()

    assert state["policies_total"] == 3
