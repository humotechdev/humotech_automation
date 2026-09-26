"""Опросы: редакции шаблона, пропуски, сроки и автоматические рассылки.

Проверяется то, что легче всего сломать и труднее всего заметить:

— опубликованный шаблон, переписанный на месте поверх уже заданных
  вопросов;
— человек без Telegram, навсегда зависший в «отправляем» и тихо
  занижающий долю прошедших;
— напоминание, пришедшее тому, кто уже ответил;
— опрос по событию, пришедший одному человеку дважды;
— регулярный опрос, сработавший один раз и больше никогда;
— правило, сработавшее до назначенного часа или по уволенному.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from django.utils import timezone

from humotech.core.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from humotech.core.timeframes import organization_zone
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.notifications.models import Notification
from humotech.surveys import services
from humotech.surveys.automations import (
    SurveyAutomationService,
    next_run,
    run_due,
)
from humotech.surveys.models import SurveyCampaign, SurveyRecipient
from humotech.surveys.services import (
    SurveyCampaignService,
    SurveyTemplateService,
)

from django_tests.test_surveys import QUESTIONS, link_telegram


@pytest.fixture()
def hr_actor(make_actor, organization):
    return make_actor(
        organization,
        permissions=(
            "surveys.read", "surveys.manage", "offices.read", "employees.read",
        ),
    )


@pytest.fixture()
def stranger_actor(make_actor, other_organization):
    """Кадровик чужой организации с теми же правами.

    Права ему нужны именно те же: иначе тест проверял бы
    отсутствие разрешения, а не границу между организациями.
    """
    return make_actor(
        other_organization,
        permissions=("surveys.read", "surveys.manage"),
    )


@pytest.fixture()
def templates():
    return SurveyTemplateService()


@pytest.fixture()
def campaigns():
    return SurveyCampaignService()


@pytest.fixture()
def rules():
    return SurveyAutomationService()


@pytest.fixture()
def template(templates, hr_actor):
    return templates.create(
        hr_actor, title="Пульс-опрос", description="Короткий опрос о работе",
        questions=QUESTIONS,
    )


@pytest.fixture()
def published(templates, hr_actor, template):
    return templates.publish(hr_actor, template.id)


@pytest.fixture()
def tz(organization):
    return organization_zone(organization.id)


def hire(
    organization, office, *, number, hire_date=date(2024, 1, 9),
    probation_to=None, birth_date=None, status="ACTIVE",
    department=None, telegram=True,
):
    """Завести сотрудника с нужными для события датами."""
    person = Employee.objects.create(
        organization=organization,
        employee_number=number,
        first_name="Сотрудник",
        last_name=number,
        hire_date=hire_date,
        probation_to=probation_to,
        birth_date=birth_date,
        employment_status=status,
    )
    EmployeeAssignment.objects.create(
        organization=organization,
        employee=person,
        office=office,
        department=department,
        employment_type="FULL_TIME",
        work_mode="ONSITE",
        is_primary=True,
        valid_from=hire_date,
    )
    if telegram:
        link_telegram(organization, person)
    return person


def at(tz, day: date, hour: int = 10, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


# --- редакции шаблона -------------------------------------------------------


@pytest.mark.django_db
class TestVersions:
    def test_empty_template_is_not_published(self, templates, hr_actor):
        """Опрос без вопросов — не опрос, а просьба ответить ни на что."""
        empty = templates.create(hr_actor, title="Пустой", questions=QUESTIONS)
        empty.questions.all().delete()
        with pytest.raises(ValidationFailed):
            templates.publish(hr_actor, empty.id)

    def test_published_template_is_not_rewritten_in_place(
        self, templates, hr_actor, published,
    ):
        """Правка на месте превратила бы прежние ответы в ответы на другое."""
        with pytest.raises(Conflict):
            templates.update(
                hr_actor, published.id,
                questions=[{"text": "Совсем другой вопрос", "kind": "TEXT"}],
            )

    def test_new_version_copies_questions_and_leaves_the_old_alone(
        self, templates, hr_actor, published,
    ):
        fresh = templates.new_version(hr_actor, published.id)

        assert fresh.id != published.id
        assert fresh.version == published.version + 1
        assert fresh.status == "DRAFT"
        assert fresh.previous_version_id == published.id
        assert [one.text for one in fresh.questions.order_by("position")] == [
            one.text for one in published.questions.order_by("position")
        ]
        # Прежняя редакция цела вместе со своими вопросами.
        published.refresh_from_db()
        assert published.status == "PUBLISHED"
        assert published.questions.count() == len(QUESTIONS)

    def test_draft_has_no_new_version(self, templates, hr_actor, template):
        """Черновик правят как есть: копия ради копии только путает."""
        with pytest.raises(Conflict):
            templates.new_version(hr_actor, template.id)

    def test_campaign_remembers_the_version_it_asked_by(
        self, campaigns, templates, hr_actor, published, organization, office,
    ):
        hire(organization, office, number="EMP-V1")
        campaign = campaigns.create(
            hr_actor, template_id=published.id, audience_kind="ALL",
            send_now=True,
        )
        campaign.refresh_from_db()
        assert campaign.template_version == published.version

        # Новая редакция шаблона не переписывает того, что уже спросили.
        templates.new_version(hr_actor, published.id)
        campaign.refresh_from_db()
        assert campaign.template_version == published.version


# --- пропуски и сроки -------------------------------------------------------


@pytest.mark.django_db
class TestSkipAndDeadline:
    def test_person_without_telegram_is_skipped_with_a_reason(
        self, campaigns, hr_actor, template, organization, office,
    ):
        """Опрос живёт в Telegram: без привязки его отправить некуда.

        «Отправляем» на такой строке — вечное ожидание: очередь не
        выдаст сообщение, у которого нет живого адресата.
        """
        lost = hire(organization, office, number="EMP-NOTG", telegram=False)
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL",
            send_now=True,
        )

        row = SurveyRecipient.objects.get(
            campaign_id=campaign.id, employee_id=lost.id
        )
        assert row.status == "SKIPPED"
        assert row.skip_reason
        assert not Notification.objects.filter(
            employee_id=lost.id, notification_type=services.INVITE_TYPE
        ).exists()

    def test_skipped_do_not_spoil_the_count(
        self, campaigns, hr_actor, template, organization, office,
    ):
        """«Прошли 1 из 2» при недостижимом втором ставит не тот вопрос."""
        hire(organization, office, number="EMP-OK")
        hire(organization, office, number="EMP-NOTG2", telegram=False)
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL",
            send_now=True,
        )

        count = services.progress_of(campaign)
        assert count["total"] == 2
        assert count["skipped"] == 1
        assert count["reachable"] == 1
        assert count["sent"] == 1

    def test_reminder_goes_only_to_those_who_have_not_finished(
        self, campaigns, hr_actor, template, organization, office,
    ):
        """Второе «пройдите опрос» ответившему говорит, что ответ потеряли."""
        done = hire(organization, office, number="EMP-DONE")
        waiting = hire(organization, office, number="EMP-WAIT")
        moment = timezone.now()
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL",
            remind_at=moment + timedelta(days=2),
            due_at=moment + timedelta(days=5),
            send_now=True,
        )
        SurveyRecipient.objects.filter(
            campaign_id=campaign.id, employee_id=done.id
        ).update(status="COMPLETED", completed_at=moment)

        services.remind_due(now=moment + timedelta(days=3))

        assert Notification.objects.filter(
            employee_id=waiting.id, body__contains="Напоминаем"
        ).count() == 1
        assert not Notification.objects.filter(
            employee_id=done.id, body__contains="Напоминаем"
        ).exists()

    def test_reminder_is_not_sent_twice(
        self, campaigns, hr_actor, template, organization, office,
    ):
        person = hire(organization, office, number="EMP-ONCE")
        moment = timezone.now()
        campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL",
            remind_at=moment + timedelta(days=1), send_now=True,
        )
        services.remind_due(now=moment + timedelta(days=2))
        services.remind_due(now=moment + timedelta(days=2, hours=1))

        assert Notification.objects.filter(
            employee_id=person.id, body__contains="Напоминаем"
        ).count() == 1

    def test_expired_survey_gets_an_outcome_not_endless_waiting(
        self, campaigns, hr_actor, template, organization, office,
    ):
        person = hire(organization, office, number="EMP-LATE")
        moment = timezone.now()
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL",
            due_at=moment + timedelta(days=3), send_now=True,
        )

        services.remind_due(now=moment + timedelta(days=4))

        row = SurveyRecipient.objects.get(
            campaign_id=campaign.id, employee_id=person.id
        )
        assert row.status == "EXPIRED"
        campaign.refresh_from_db()
        assert campaign.status == "FINISHED"

    def test_answer_after_the_deadline_is_refused(
        self, campaigns, hr_actor, template, organization, office,
    ):
        """«Закрыть через 14 дней» — обещание обеим сторонам.

        Ответ, пришедший после срока, меняет отчёт, который
        кадровик уже показал вчера.
        """
        person = hire(organization, office, number="EMP-SHUT")
        moment = timezone.now()
        campaign = campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL",
            due_at=moment + timedelta(days=1), send_now=True,
        )
        row = SurveyRecipient.objects.get(
            campaign_id=campaign.id, employee_id=person.id
        )
        services.remind_due(now=moment + timedelta(days=2))

        with pytest.raises(Conflict):
            services.open_survey(employee_id=person.id, recipient_id=row.id)
        with pytest.raises(Conflict):
            services.submit(
                employee_id=person.id, recipient_id=row.id, answers=[],
                now=moment + timedelta(days=2),
            )

    def test_closed_survey_disappears_from_the_employee_list(
        self, campaigns, hr_actor, template, organization, office,
    ):
        """Показать опрос и отказать на первом нажатии хуже,

        чем не показывать вовсе: человек открыл приложение
        ради него. Очередь может ещё не успеть проставить исход.
        """
        person = hire(organization, office, number="EMP-HIDE")
        moment = timezone.now()
        campaigns.create(
            hr_actor, template_id=template.id, audience_kind="ALL",
            due_at=moment + timedelta(days=1), send_now=True,
        )

        assert services.pending_for(person.id, now=moment)
        assert services.pending_for(person.id, now=moment + timedelta(days=2)) == []

    def test_deadline_before_the_send_is_refused(
        self, campaigns, hr_actor, template,
    ):
        moment = timezone.now()
        with pytest.raises(ValidationFailed):
            campaigns.create(
                hr_actor, template_id=template.id, audience_kind="ALL",
                scheduled_at=moment + timedelta(days=3),
                due_at=moment + timedelta(days=1),
            )

    def test_reminder_after_the_deadline_is_refused(
        self, campaigns, hr_actor, template,
    ):
        """Напоминать о закрытом опросе не о чем."""
        moment = timezone.now()
        with pytest.raises(ValidationFailed):
            campaigns.create(
                hr_actor, template_id=template.id, audience_kind="ALL",
                due_at=moment + timedelta(days=2),
                remind_at=moment + timedelta(days=4),
            )


# --- правила ----------------------------------------------------------------


@pytest.mark.django_db
class TestRules:
    def test_rule_needs_a_published_template(
        self, rules, hr_actor, template,
    ):
        """Правило работает без присмотра: черновик оно рассылать не вправе."""
        with pytest.raises(ValidationFailed):
            rules.create(hr_actor, {
                "template_id": str(template.id),
                "trigger_kind": "PROBATION_END",
            })

    def test_schedule_needs_a_period_and_an_event_does_not(
        self, rules, hr_actor, published,
    ):
        with pytest.raises(ValidationFailed):
            rules.create(hr_actor, {
                "template_id": str(published.id),
                "trigger_kind": "SCHEDULE",
            })
        rule = rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "BIRTHDAY",
            "repeat_months": 3,
        })
        assert rule.repeat_months is None

    def test_unknown_event_is_refused(self, rules, hr_actor, published):
        with pytest.raises(ValidationFailed):
            rules.create(hr_actor, {
                "template_id": str(published.id),
                "trigger_kind": "FULL_MOON",
            })

    def test_rule_with_history_is_switched_off_not_deleted(
        self, rules, hr_actor, published, organization, office, tz,
    ):
        person = hire(
            organization, office, number="EMP-HIST",
            probation_to=date(2026, 9, 22),
        )
        rule = rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "PROBATION_END",
        })
        run_due(now=at(tz, date(2026, 9, 22), 11))

        with pytest.raises(Conflict):
            rules.delete(hr_actor, rule.id)
        assert rules.toggle(hr_actor, rule.id, active=False).is_active is False

    def test_someone_elses_rule_is_not_found(
        self, rules, hr_actor, stranger_actor, published,
    ):
        rule = rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "BIRTHDAY",
        })
        with pytest.raises(NotFound):
            rules.get(stranger_actor, rule.id)

    def test_reading_rules_needs_permission(self, rules, nobody_actor):
        with pytest.raises(PermissionDenied):
            rules.list(nobody_actor)


# --- срабатывание -----------------------------------------------------------


@pytest.mark.django_db
class TestFiring:
    def test_probation_end_asks_exactly_that_person(
        self, rules, hr_actor, published, organization, office, tz,
    ):
        ending = hire(
            organization, office, number="EMP-END",
            probation_to=date(2026, 9, 22), status="PROBATION",
        )
        other = hire(
            organization, office, number="EMP-OTHER",
            probation_to=date(2026, 12, 1), status="PROBATION",
        )
        rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "PROBATION_END",
        })

        assert run_due(now=at(tz, date(2026, 9, 22), 10)) == 1

        asked = set(
            SurveyRecipient.objects.values_list("employee_id", flat=True)
        )
        assert asked == {ending.id}
        assert other.id not in asked

    def test_nothing_happens_before_the_appointed_hour(
        self, rules, hr_actor, published, organization, office, tz,
    ):
        """Правило обещает «в 10:00». Отправка в 00:05 — другое обещание."""
        hire(
            organization, office, number="EMP-EARLY",
            probation_to=date(2026, 9, 22), status="PROBATION",
        )
        rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "PROBATION_END",
            "send_hour": 10,
        })

        assert run_due(now=at(tz, date(2026, 9, 22), 9, 59)) == 0
        assert SurveyRecipient.objects.count() == 0
        assert run_due(now=at(tz, date(2026, 9, 22), 10, 0)) == 1

    def test_the_same_event_does_not_ask_twice(
        self, rules, hr_actor, published, organization, office, tz,
    ):
        """Очередь опрашивается каждые несколько секунд весь день."""
        person = hire(
            organization, office, number="EMP-TWICE",
            probation_to=date(2026, 9, 22), status="PROBATION",
        )
        rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "PROBATION_END",
        })

        run_due(now=at(tz, date(2026, 9, 22), 10))
        run_due(now=at(tz, date(2026, 9, 22), 10, 5))
        run_due(now=at(tz, date(2026, 9, 22), 18))

        assert SurveyCampaign.objects.filter(automation__isnull=False).count() == 1
        assert SurveyRecipient.objects.filter(employee_id=person.id).count() == 1
        assert Notification.objects.filter(
            employee_id=person.id, notification_type=services.INVITE_TYPE
        ).count() == 1

    def test_offset_moves_the_day_but_keeps_the_occasion(
        self, rules, hr_actor, published, organization, office, tz,
    ):
        """«Через неделю после выхода»: спрашивают 16-го о событии 9-го."""
        hire(organization, office, number="EMP-WEEK", hire_date=date(2026, 9, 15))
        rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "DAYS_AFTER_HIRE",
            "offset_days": 7,
        })

        assert run_due(now=at(tz, date(2026, 9, 21), 10)) == 0
        assert run_due(now=at(tz, date(2026, 9, 22), 10)) == 1

        campaign = SurveyCampaign.objects.get(automation__isnull=False)
        assert campaign.trigger_key.endswith("2026-09-15")

    def test_people_with_different_events_get_different_campaigns(
        self, rules, hr_actor, published, organization, office, tz,
    ):
        """Один день отправки — ещё не один повод."""
        hire(
            organization, office, number="EMP-A",
            probation_to=date(2026, 9, 22), status="PROBATION",
        )
        hire(
            organization, office, number="EMP-B",
            probation_to=date(2026, 9, 22), status="PROBATION",
        )
        rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "PROBATION_END",
        })
        run_due(now=at(tz, date(2026, 9, 22), 10))

        # Один день события — одна рассылка на двоих.
        assert SurveyCampaign.objects.filter(automation__isnull=False).count() == 1
        assert SurveyRecipient.objects.count() == 2

    def test_dismissed_person_is_not_asked(
        self, rules, hr_actor, published, organization, office, tz,
    ):
        """Опрос человеку, которого уже нет, — письмо в пустоту."""
        gone = hire(
            organization, office, number="EMP-GONE",
            probation_to=date(2026, 9, 22), status="TERMINATED",
        )
        rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "PROBATION_END",
        })

        assert run_due(now=at(tz, date(2026, 9, 22), 10)) == 0
        assert not SurveyRecipient.objects.filter(employee_id=gone.id).exists()

    def test_switched_off_rule_does_not_fire(
        self, rules, hr_actor, published, organization, office, tz,
    ):
        hire(
            organization, office, number="EMP-OFF",
            probation_to=date(2026, 9, 22), status="PROBATION",
        )
        rule = rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "PROBATION_END",
        })
        rules.toggle(hr_actor, rule.id, active=False)

        assert run_due(now=at(tz, date(2026, 9, 22), 10)) == 0

    def test_birthday_comes_back_every_year(
        self, rules, hr_actor, published, organization, office, tz,
    ):
        """Год рождения не совпадает никогда: сравнивают день и месяц."""
        hire(
            organization, office, number="EMP-BDAY",
            birth_date=date(1994, 9, 22),
        )
        rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "BIRTHDAY",
        })

        assert run_due(now=at(tz, date(2026, 9, 21), 10)) == 0
        assert run_due(now=at(tz, date(2026, 9, 22), 10)) == 1
        # Через год — снова, и это отдельный повод.
        assert run_due(now=at(tz, date(2027, 9, 22), 10)) == 1
        assert SurveyCampaign.objects.filter(automation__isnull=False).count() == 2

    def test_leap_day_birthday_is_not_forgotten_three_years_of_four(
        self, rules, hr_actor, published, organization, office, tz,
    ):
        """Родившихся 29 февраля поздравляют 28-го.

        Сравнение «день и месяц совпали» в невисокосный год не
        совпадает никогда — и человека тихо пропускают. Это не про
        календарь, а про то, что его забыли.
        """
        hire(
            organization, office, number="EMP-LEAP",
            birth_date=date(1996, 2, 29),
        )
        rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "BIRTHDAY",
        })

        # 2027 — невисокосный: повод приходится на 28 февраля.
        assert run_due(now=at(tz, date(2027, 2, 27), 10)) == 0
        assert run_due(now=at(tz, date(2027, 2, 28), 10)) == 1
        # 2028 — високосный: 28-го ничего, 29-го — свой день.
        assert run_due(now=at(tz, date(2028, 2, 28), 10)) == 0
        assert run_due(now=at(tz, date(2028, 2, 29), 10)) == 1

    def test_schedule_repeats_after_its_period_not_the_next_day(
        self, rules, hr_actor, published, organization, office, tz,
    ):
        """Регулярный опрос — раз в квартал, а не каждый опрос очереди."""
        hire(organization, office, number="EMP-REG")
        rule = rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "SCHEDULE",
            "repeat_months": 3,
        })
        start = rule.created_at.astimezone(tz).date()

        assert run_due(now=at(tz, start, 10)) == 1
        assert run_due(now=at(tz, start + timedelta(days=1), 10)) == 0
        assert run_due(now=at(tz, start + timedelta(days=30), 10)) == 0

        # Календарный квартал, а не «девяносто дней».
        month = start.month + 3
        year = start.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(start.day, 28)
        assert run_due(now=at(tz, date(year, month, day), 10)) == 1

    def test_next_run_is_shown_only_where_it_is_known(
        self, rules, hr_actor, published,
    ):
        """У события даты нет: она зависит от того, кого наймут завтра."""
        regular = rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "SCHEDULE",
            "repeat_months": 2,
        })
        by_event = rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "PROBATION_END",
        })

        assert next_run(regular) is not None
        assert next_run(by_event) is None

    def test_scope_narrows_by_where_the_person_works_now(
        self, rules, hr_actor, published, organization, office, other_office, tz,
    ):
        """Отдел берётся из назначения: людей переводят."""
        ours = hire(
            organization, office, number="EMP-HERE",
            probation_to=date(2026, 9, 22), status="PROBATION",
        )
        hire(
            organization, other_office, number="EMP-THERE",
            probation_to=date(2026, 9, 22), status="PROBATION",
        )
        rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "PROBATION_END",
            "scope": {"office_ids": [str(office.id)]},
        })

        run_due(now=at(tz, date(2026, 9, 22), 10))
        asked = set(SurveyRecipient.objects.values_list("employee_id", flat=True))
        assert asked == {ours.id}

    def test_cancelled_campaign_is_not_resurrected(
        self, rules, campaigns, hr_actor, published, organization, office, tz,
    ):
        """Кадровик отменил рассылку — правило не вправе вернуть её тиком."""
        hire(
            organization, office, number="EMP-CANCEL",
            probation_to=date(2026, 9, 22), status="PROBATION",
        )
        rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "PROBATION_END",
        })
        run_due(now=at(tz, date(2026, 9, 22), 10))

        campaign = SurveyCampaign.objects.get(automation__isnull=False)
        campaigns.cancel(hr_actor, campaign.id)
        late = hire(
            organization, office, number="EMP-LATER",
            probation_to=date(2026, 9, 22), status="PROBATION",
        )

        run_due(now=at(tz, date(2026, 9, 22), 12))
        assert not SurveyRecipient.objects.filter(employee_id=late.id).exists()


# --- история правила ----------------------------------------------------------


@pytest.mark.django_db
class TestHistory:
    def test_history_names_each_person_and_why_the_survey_did_not_go(
        self, rules, hr_actor, published, organization, office, tz,
    ):
        """Без Telegram — пропуск с причиной, а не «успешно отправлено»."""
        hire(
            organization, office, number="EMP-ON",
            probation_to=date(2026, 9, 22), status="PROBATION",
        )
        hire(
            organization, office, number="EMP-OFF",
            probation_to=date(2026, 9, 22), status="PROBATION", telegram=False,
        )
        rule = rules.create(hr_actor, {
            "template_id": str(published.id),
            "trigger_kind": "PROBATION_END",
        })
        moment = at(tz, date(2026, 9, 22), 10)
        run_due(now=moment)

        seen = rules.history(hr_actor, rule.id, now=moment)

        by_status = {one["status"]: one for one in seen["items"]}
        assert set(by_status) == {"SENT", "SKIPPED"}
        assert by_status["SKIPPED"]["skip_reason"] == "Нет привязки Telegram"
        assert by_status["SENT"]["event_day"] == "2026-09-22"
        assert seen["stats"] == {"fired": 1, "sent": 1, "completed": 0, "skipped": 1}
