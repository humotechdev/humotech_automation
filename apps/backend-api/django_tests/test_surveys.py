"""Опросы сотрудников: шаблон, рассылка, ответ, сводка.

Проверяется то, что легче всего сломать и труднее всего заметить:

— опрос, переставший быть именным: ответ без человека;
— круг получателей, посчитанный при создании, а не при отправке;
— повторная отправка, задвоившая получателей и сообщения;
— вопросы шаблона, переписанные поверх уже полученных ответов;
— оценка вне шкалы и вариант, которого у вопроса нет;
— чужой опрос, открытый подстановкой идентификатора;
— опрос, пройденный дважды;
— вопросы, присланные сотруднику отдельными сообщениями вместо одного.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone

from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.notifications.models import Notification
from humotech.surveys import services
from humotech.surveys.models import SurveyAnswer, SurveyRecipient
from humotech.surveys.services import (
    SurveyCampaignService,
    SurveyTemplateService,
)

QUESTIONS = [
    {"text": "Насколько вам комфортно в команде?", "kind": "SCALE"},
    {
        "text": "Что помогает в работе?",
        "kind": "MULTI",
        "options": ["Коллеги", "График", "Задачи"],
    },
    {"text": "Что бы вы изменили?", "kind": "TEXT", "is_required": False},
]


@pytest.fixture()
def hr_actor(make_actor, organization):
    """Кадровик, которому разрешены опросы.

    Общий `hr_actor` из conftest несёт минимальный набор прав и опросов
    в нём нет: право на чужие ответы не должно приезжать в тест само
    собой вместе со всем остальным.
    """
    return make_actor(
        organization,
        permissions=(
            "surveys.read", "surveys.manage",
            "offices.read", "employees.read",
        ),
    )


@pytest.fixture()
def templates():
    return SurveyTemplateService()


@pytest.fixture()
def campaigns():
    return SurveyCampaignService()


@pytest.fixture()
def template(templates, hr_actor):
    return templates.create(
        hr_actor, title="Пульс-опрос", description="Короткий опрос о работе",
        questions=QUESTIONS,
    )


def second_employee(organization, office, number="EMP-0002"):
    person = Employee.objects.create(
        organization=organization,
        employee_number=number,
        first_name="Пётр",
        last_name="Петров",
        hire_date=date(2024, 3, 1),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization,
        employee=person,
        office=office,
        employment_type="FULL_TIME",
        work_mode="ONSITE",
        is_primary=True,
        valid_from=date(2024, 3, 1),
    )
    return person


# --- шаблон -----------------------------------------------------------------


@pytest.mark.django_db
class TestTemplate:
    def test_questions_keep_their_order(self, template):
        rows = list(template.questions.order_by("position"))
        assert [row.position for row in rows] == [1, 2, 3]
        assert rows[0].kind == "SCALE"
        # У шкалы и текста вариантов нет вовсе — это проверяет и база.
        assert rows[0].options is None
        assert rows[1].options == ["Коллеги", "График", "Задачи"]

    def test_survey_without_questions_is_refused(self, templates, hr_actor):
        with pytest.raises(ValidationFailed):
            templates.create(hr_actor, title="Пустой", questions=[])

    def test_choice_question_needs_at_least_two_options(self, templates, hr_actor):
        # Один вариант — это не выбор, а утверждение.
        with pytest.raises(ValidationFailed):
            templates.create(
                hr_actor, title="Один вариант",
                questions=[{"text": "Да?", "kind": "SINGLE", "options": ["Да"]}],
            )

    def test_copy_repeats_every_question(self, templates, hr_actor, template):
        copy = templates.copy(hr_actor, template.id)
        assert copy.id != template.id
        assert "копия" in copy.title
        assert [one.text for one in copy.questions.order_by("position")] == [
            one.text for one in template.questions.order_by("position")
        ]

    def test_answered_questions_are_not_rewritten(
        self, templates, campaigns, hr_actor, template, employee, office,
    ):
        """Правка текста задним числом превратила бы прежние ответы в
        ответы на другой вопрос. Такой шаблон копируют, а не правят."""
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL", send_now=True,
        )
        recipient = SurveyRecipient.objects.get(campaign_id=campaign.id)
        first = template.questions.order_by("position").first()
        services.submit(
            employee_id=employee.id, recipient_id=recipient.id,
            answers=[
                {"question_id": str(first.id), "number": 4},
                {"question_id": str(
                    template.questions.order_by("position")[1].id
                ), "options": ["Коллеги"]},
            ],
        )

        with pytest.raises(Conflict):
            templates.update(
                hr_actor, template.id,
                questions=[{"text": "Совсем другой вопрос", "kind": "TEXT"}],
            )
        # Название при этом поменять можно: оно ответов не касается.
        templates.update(hr_actor, template.id, title="Пульс-опрос, сентябрь")
        template.refresh_from_db()
        assert template.title == "Пульс-опрос, сентябрь"


# --- рассылка ---------------------------------------------------------------


@pytest.mark.django_db
class TestCampaign:
    def test_audience_is_resolved_at_send_time(
        self, campaigns, hr_actor, template, employee, office, organization,
    ):
        """Кто получит опрос, решается при отправке, а не при создании.

        Иначе человек, пришедший между планированием и рассылкой, опрос
        не получил бы — и это молча.
        """
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="OFFICE",
            audience_ids=[str(office.id)],
        )
        assert SurveyRecipient.objects.filter(campaign_id=campaign.id).count() == 0

        # Новый сотрудник появляется ПОСЛЕ создания рассылки.
        late = second_employee(organization, office)
        campaigns.send(hr_actor, campaign.id)

        got = set(
            SurveyRecipient.objects.filter(campaign_id=campaign.id).values_list(
                "employee_id", flat=True
            )
        )
        assert got == {employee.id, late.id}

    def test_office_audience_leaves_other_offices_alone(
        self, campaigns, hr_actor, template, employee, office, other_office,
        organization,
    ):
        stranger = second_employee(organization, other_office, number="EMP-0003")
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="OFFICE",
            audience_ids=[str(office.id)], send_now=True,
        )
        got = set(
            SurveyRecipient.objects.filter(campaign_id=campaign.id).values_list(
                "employee_id", flat=True
            )
        )
        assert employee.id in got
        assert stranger.id not in got

    def test_dismissed_employees_are_not_asked(
        self, campaigns, hr_actor, template, employee, office, organization,
    ):
        gone = second_employee(organization, office, number="EMP-0004")
        gone.employment_status = "TERMINATED"
        gone.save(update_fields=["employment_status"])

        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL", send_now=True,
        )
        got = set(
            SurveyRecipient.objects.filter(campaign_id=campaign.id).values_list(
                "employee_id", flat=True
            )
        )
        assert gone.id not in got

    def test_one_message_with_a_button_not_a_question_per_message(
        self, campaigns, hr_actor, template, employee,
    ):
        """Сотруднику уходит ОДНО сообщение, а не по одному на вопрос."""
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL", send_now=True,
        )
        rows = list(
            Notification.objects.filter(
                employee_id=employee.id, notification_type=services.INVITE_TYPE
            )
        )
        assert len(rows) == 1
        assert campaign.title in rows[0].body
        assert "2–3 минуты" in rows[0].body
        # Текст вопроса в сообщение не попадает: его показывает Mini App.
        for question in template.questions.all():
            assert question.text not in rows[0].body
        # Сообщение ссылается на получателя — по нему бот строит кнопку.
        recipient = SurveyRecipient.objects.get(campaign_id=campaign.id)
        assert rows[0].related_entity_type == "survey_recipients"
        assert str(rows[0].related_entity_id) == str(recipient.id)

    def test_sending_twice_does_not_double_anything(
        self, campaigns, hr_actor, template, employee,
    ):
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL", send_now=True,
        )
        campaigns.send(hr_actor, campaign.id)

        assert SurveyRecipient.objects.filter(campaign_id=campaign.id).count() == 1
        assert Notification.objects.filter(
            employee_id=employee.id, notification_type=services.INVITE_TYPE
        ).count() == 1

    def test_repeat_sets_the_next_date(
        self, campaigns, hr_actor, template, employee,
    ):
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL",
            repeat_months=2, send_now=True,
        )
        campaign.refresh_from_db()
        assert campaign.next_send_at is not None
        assert campaign.next_send_at > timezone.now() + timedelta(days=50)

    def test_one_off_campaign_has_no_next_date(
        self, campaigns, hr_actor, template, employee,
    ):
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL", send_now=True,
        )
        campaign.refresh_from_db()
        assert campaign.next_send_at is None

    def test_odd_repeat_period_is_refused(self, campaigns, hr_actor, template):
        with pytest.raises(ValidationFailed):
            campaigns.create(
                hr_actor, template_id=template.id, audience_kind="ALL",
                repeat_months=7,
            )

    def test_backdated_schedule_is_refused(self, campaigns, hr_actor, template):
        with pytest.raises(ValidationFailed):
            campaigns.create(
                hr_actor, template_id=template.id, audience_kind="ALL",
                scheduled_at=timezone.now() - timedelta(days=1),
            )

    def test_cancelled_campaign_stops_repeating(
        self, campaigns, hr_actor, template, employee,
    ):
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL",
            repeat_months=3, send_now=True,
        )
        campaigns.cancel(hr_actor, campaign.id)
        campaign.refresh_from_db()
        assert campaign.status == "CANCELLED"
        assert campaign.next_send_at is None
        with pytest.raises(Conflict):
            campaigns.send(hr_actor, campaign.id)

    def test_due_campaigns_are_sent_by_the_queue(
        self, campaigns, hr_actor, template, employee,
    ):
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL",
            scheduled_at=timezone.now() + timedelta(minutes=1),
        )
        assert campaign.status == "SCHEDULED"
        assert services.dispatch_due(now=timezone.now()) == 0

        sent = services.dispatch_due(now=timezone.now() + timedelta(minutes=2))
        assert sent == 1
        campaign.refresh_from_db()
        assert campaign.status == "ACTIVE"


# --- сторона сотрудника ------------------------------------------------------


@pytest.mark.django_db
class TestEmployeeSide:
    @pytest.fixture()
    def sent(self, campaigns, hr_actor, template, employee):
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL", send_now=True,
        )
        return SurveyRecipient.objects.get(campaign_id=campaign.id)

    def test_opening_marks_the_start(self, sent, employee):
        assert sent.status == "SENT"
        opened = services.open_survey(
            employee_id=employee.id, recipient_id=sent.id,
        )
        assert opened.status == "STARTED"
        assert opened.started_at is not None

    def test_another_persons_survey_does_not_open(
        self, sent, organization, office,
    ):
        stranger = second_employee(organization, office, number="EMP-0005")
        with pytest.raises(NotFound):
            services.open_survey(
                employee_id=stranger.id, recipient_id=sent.id,
            )

    def test_answers_are_saved_with_the_person(self, sent, employee, template):
        rows = list(template.questions.order_by("position"))
        services.submit(
            employee_id=employee.id, recipient_id=sent.id,
            answers=[
                {"question_id": str(rows[0].id), "number": 5},
                {"question_id": str(rows[1].id), "options": ["Коллеги", "Задачи"]},
                {"question_id": str(rows[2].id), "text": "Больше времени"},
            ],
        )
        sent.refresh_from_db()
        assert sent.status == "COMPLETED"
        assert sent.completed_at is not None

        answers = {
            one.question_id: one
            for one in SurveyAnswer.objects.filter(recipient_id=sent.id)
        }
        assert len(answers) == 3
        # Ответ без человека здесь невозможен: получатель обязателен.
        for answer in answers.values():
            assert answer.recipient_id == sent.id
        assert answers[rows[0].id].number == 5
        assert answers[rows[1].id].options == ["Коллеги", "Задачи"]
        assert answers[rows[2].id].text == "Больше времени"

    def test_required_questions_cannot_be_skipped(self, sent, employee, template):
        rows = list(template.questions.order_by("position"))
        with pytest.raises(ValidationFailed) as failure:
            services.submit(
                employee_id=employee.id, recipient_id=sent.id,
                answers=[{"question_id": str(rows[0].id), "number": 3}],
            )
        assert rows[1].text in str(failure.value.details)
        # Ничего не сохранилось: половина ответов хуже, чем ни одного.
        assert SurveyAnswer.objects.filter(recipient_id=sent.id).count() == 0
        sent.refresh_from_db()
        assert sent.status != "COMPLETED"

    def test_optional_question_may_stay_empty(self, sent, employee, template):
        rows = list(template.questions.order_by("position"))
        services.submit(
            employee_id=employee.id, recipient_id=sent.id,
            answers=[
                {"question_id": str(rows[0].id), "number": 3},
                {"question_id": str(rows[1].id), "options": ["График"]},
                {"question_id": str(rows[2].id), "text": "   "},
            ],
        )
        # Пустой ответ не хранится: «ответил ничем» — не ответ.
        assert SurveyAnswer.objects.filter(recipient_id=sent.id).count() == 2

    def test_scale_outside_its_range_is_refused(self, sent, employee, template):
        rows = list(template.questions.order_by("position"))
        with pytest.raises(ValidationFailed):
            services.submit(
                employee_id=employee.id, recipient_id=sent.id,
                answers=[{"question_id": str(rows[0].id), "number": 9}],
            )

    def test_option_that_does_not_exist_is_refused(self, sent, employee, template):
        rows = list(template.questions.order_by("position"))
        with pytest.raises(ValidationFailed):
            services.submit(
                employee_id=employee.id, recipient_id=sent.id,
                answers=[
                    {"question_id": str(rows[0].id), "number": 3},
                    {"question_id": str(rows[1].id), "options": ["Зарплата"]},
                ],
            )

    def test_single_choice_takes_one_option(
        self, campaigns, templates, hr_actor, employee,
    ):
        one = templates.create(
            hr_actor, title="Один из",
            questions=[{
                "text": "Где удобнее?", "kind": "SINGLE",
                "options": ["Офис", "Дома"],
            }],
        )
        campaign = campaigns.create(
            hr_actor, template_id=one.id, audience_kind="ALL", send_now=True,
        )
        recipient = SurveyRecipient.objects.get(campaign_id=campaign.id)
        question = one.questions.first()
        with pytest.raises(ValidationFailed):
            services.submit(
                employee_id=employee.id, recipient_id=recipient.id,
                answers=[{
                    "question_id": str(question.id),
                    "options": ["Офис", "Дома"],
                }],
            )

    def test_completed_survey_is_not_taken_twice(self, sent, employee, template):
        rows = list(template.questions.order_by("position"))
        payload = [
            {"question_id": str(rows[0].id), "number": 4},
            {"question_id": str(rows[1].id), "options": ["График"]},
        ]
        services.submit(
            employee_id=employee.id, recipient_id=sent.id, answers=payload,
        )
        # Иначе человек переписал бы ответ после разговора с руководителем.
        with pytest.raises(Conflict):
            services.submit(
                employee_id=employee.id, recipient_id=sent.id, answers=payload,
            )

    def test_thanks_is_sent_once(self, sent, employee, template):
        rows = list(template.questions.order_by("position"))
        services.submit(
            employee_id=employee.id, recipient_id=sent.id,
            answers=[
                {"question_id": str(rows[0].id), "number": 4},
                {"question_id": str(rows[1].id), "options": ["График"]},
            ],
        )
        thanks = Notification.objects.filter(
            employee_id=employee.id, notification_type=services.THANKS_TYPE
        )
        assert thanks.count() == 1
        assert "Спасибо" in thanks.first().body

    def test_pending_list_shows_only_unfinished(self, sent, employee, template):
        assert [one.id for one in services.pending_for(employee.id)] == [sent.id]
        rows = list(template.questions.order_by("position"))
        services.submit(
            employee_id=employee.id, recipient_id=sent.id,
            answers=[
                {"question_id": str(rows[0].id), "number": 2},
                {"question_id": str(rows[1].id), "options": ["Задачи"]},
            ],
        )
        assert services.pending_for(employee.id) == []


# --- что видит HR ------------------------------------------------------------


@pytest.mark.django_db
class TestWhatHrSees:
    def test_answers_carry_the_name_office_and_department(
        self, campaigns, hr_actor, template, employee, office,
    ):
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="OFFICE",
            audience_ids=[str(office.id)], send_now=True,
        )
        recipient = SurveyRecipient.objects.get(campaign_id=campaign.id)
        rows = list(template.questions.order_by("position"))
        services.submit(
            employee_id=employee.id, recipient_id=recipient.id,
            answers=[
                {"question_id": str(rows[0].id), "number": 5},
                {"question_id": str(rows[1].id), "options": ["Коллеги"]},
                {"question_id": str(rows[2].id), "text": "Всё устраивает"},
            ],
        )

        filled = campaigns.answers(hr_actor, campaign.id)
        assert len(filled) == 1
        assert filled[0].employee_id == employee.id
        # Опрос именной: фамилия стоит рядом с ответом.
        assert filled[0].employee.last_name == "Иванов"
        places = services.places_of([employee.id])
        assert places[employee.id]["office"] == office.name

    def test_progress_separates_sent_started_and_done(
        self, campaigns, hr_actor, template, employee, office, organization,
    ):
        second_employee(organization, office, number="EMP-0006")
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL", send_now=True,
        )
        mine = SurveyRecipient.objects.get(
            campaign_id=campaign.id, employee_id=employee.id
        )
        services.open_survey(employee_id=employee.id, recipient_id=mine.id)

        progress = services.progress_of(campaign)
        assert progress["total"] == 2
        assert progress["sent"] == 2
        assert progress["started"] == 1
        assert progress["completed"] == 0

    def test_summary_counts_scale_and_options(
        self, campaigns, hr_actor, template, employee, office, organization,
    ):
        other = second_employee(organization, office, number="EMP-0007")
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL", send_now=True,
        )
        rows = list(template.questions.order_by("position"))
        for person, mark, option in ((employee, 5, "Коллеги"), (other, 3, "График")):
            recipient = SurveyRecipient.objects.get(
                campaign_id=campaign.id, employee_id=person.id
            )
            services.submit(
                employee_id=person.id, recipient_id=recipient.id,
                answers=[
                    {"question_id": str(rows[0].id), "number": mark},
                    {"question_id": str(rows[1].id), "options": [option]},
                ],
            )

        summary = campaigns.summary(hr_actor, campaign.id)
        scale = next(
            one for one in summary["questions"] if one["kind"] == "SCALE"
        )
        assert scale["average"] == 4.0
        assert scale["distribution"]["5"] == 1
        assert scale["distribution"]["3"] == 1

        choice = next(
            one for one in summary["questions"] if one["kind"] == "MULTI"
        )
        assert choice["distribution"]["Коллеги"] == 1
        assert choice["distribution"]["Задачи"] == 0

        assert summary["offices"][0]["name"] == office.name
        assert summary["offices"][0]["completed"] == 2

    def test_reading_needs_permission(
        self, campaigns, hr_actor, nobody_actor, template,
    ):
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL",
        )
        with pytest.raises(Exception):
            campaigns.answers(nobody_actor, campaign.id)
