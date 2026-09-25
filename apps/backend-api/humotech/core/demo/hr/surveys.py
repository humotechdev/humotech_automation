"""Опросы: шаблоны, рассылки во всех состояниях, ответы и автоматизации.

Получатели — те, кому опрос мог дойти: без Telegram человек получает
статус «не доставлено» с причиной, как это делает сама отправка. Ответы
пишутся по вопросам шаблона; процент ответов считает система.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from humotech.core.demo.hr import catalog as cat
from humotech.core.demo.hr.people import Person, Structure, did, rng
from humotech.surveys.models import (
    SurveyAnswer,
    SurveyAutomation,
    SurveyCampaign,
    SurveyQuestion,
    SurveyRecipient,
    SurveyTemplate,
)


def build(org, structure: Structure, people: list[Person], today: date, now: datetime, tz, reviewer) -> dict:
    templates, questions = {}, {}
    for key, title, description, published, items in cat.TEMPLATES:
        made = now - timedelta(days=90 - len(templates) * 7)
        row = SurveyTemplate.objects.create(
            id=did("template", key), organization=org, title=title, description=description,
            status="PUBLISHED" if published else "DRAFT", version=1, published_at=made if published else None,
            created_by_user=reviewer,
        )
        SurveyTemplate.objects.filter(id=row.id).update(created_at=made - timedelta(days=2), updated_at=made)
        templates[key] = row
        questions[key] = []
        for at, (kind, text, options) in enumerate(items, start=1):
            questions[key].append(SurveyQuestion.objects.create(
                id=did("template-question", key, at), organization=org, template=row, position=at, text=text,
                kind=kind, is_required=True, options=options,
            ))

    active = [p for p in people if p.active]
    trainees = [p for p in people if p.status == "PROBATION"]
    sales_like = [p for p in active if p.department in ("SALES", "SUPPORT", "OPS")]

    def at(day: date, hour: int) -> datetime:
        return datetime.combine(day, time(hour, 0), tzinfo=tz)

    campaigns = [
        # ключ, шаблон, название, статус, отправка, срок, круг, получатели, доля ответивших, анонимный
        ("satisfaction", "SATISFACTION", "Удовлетворённость работой — III квартал", "FINISHED",
         at(today - timedelta(days=21), 10), at(today - timedelta(days=7), 18), "ALL", None, active, 0.78, True),
        ("manager", "MANAGER", "Оценка руководителя — продажи, поддержка, операции", "ACTIVE",
         at(today - timedelta(days=4), 10), at(today + timedelta(days=3), 18), "DEPARTMENT",
         [str(structure.departments[c].id) for c in ("SALES", "SUPPORT", "OPS")], sales_like, 0.60, False),
        ("probation", "PROBATION", "Итоги стажировки — сентябрь", "SCHEDULED",
         at(today + timedelta(days=3), 10), at(today + timedelta(days=10), 18), "EMPLOYEES",
         [str(p.employee.id) for p in trainees], [], 0, False),
        ("adapt-aug", "ADAPT", "Оценка адаптации — август", "CANCELLED",
         None, None, "ALL", None, [], 0, False),
    ]
    stats = {}
    for key, template, title, status, sent, due, kind, ids, audience, rate, anonymous in campaigns:
        campaign = SurveyCampaign.objects.create(
            id=did("campaign", key), organization=org, template=templates[template], title=title,
            template_version=1, status=status, audience_kind=kind, audience_ids=ids,
            audience_snapshot=[str(p.employee.id) for p in trainees] if status == "SCHEDULED" else None,
            scheduled_at=sent if status == "SCHEDULED" else None, next_send_at=sent if status == "SCHEDULED" else None,
            sent_at=sent if status in ("FINISHED", "ACTIVE") else None, due_at=due,
            remind_at=(sent + timedelta(days=5)) if status == "ACTIVE" else None,
            reminded_at=None, is_anonymous=anonymous, created_by_user=reviewer,
        )
        created = (sent or now - timedelta(days=35)) - timedelta(days=1)
        SurveyCampaign.objects.filter(id=campaign.id).update(created_at=created, updated_at=created)
        stats[key] = _recipients(org, campaign, questions[template], audience, sent, rate, status, now, key)

    SurveyAutomation.objects.create(
        id=did("automation", "adapt"), organization=org, title="Оценка адаптации через 30 дней после выхода",
        template=templates["ADAPT"], trigger_kind="DAYS_AFTER_HIRE", offset_days=30, send_hour=10, send_minute=0,
        is_active=True, created_by_user=reviewer,
    )
    SurveyAutomation.objects.create(
        id=did("automation", "probation"), organization=org, title="Итоги стажировки в день завершения",
        template=templates["PROBATION"], trigger_kind="PROBATION_END", offset_days=0, send_hour=10, send_minute=0,
        is_active=True, created_by_user=reviewer,
    )
    return stats


def _recipients(org, campaign, questions, audience, sent, rate, status, now, key) -> dict:
    recipients, answers = [], []
    done = 0
    for person in audience:
        r = rng("survey", key, person.number)
        base = dict(id=did("recipient", key, person.number), organization=org, campaign=campaign,
                    employee=person.employee)
        if sent is not None and person.hire > sent.date():
            continue  # пришёл после рассылки — его не спрашивали
        if person.telegram != "ACTIVE":
            recipients.append(SurveyRecipient(**base, status="SKIPPED", skip_reason="Не привязан Telegram"))
            continue
        delivered = sent + timedelta(minutes=r.randint(1, 20))
        if r.random() < rate:  # доля — от получивших, а не от всего круга
            finished = min(delivered + timedelta(hours=r.randint(1, 90)), now - timedelta(minutes=30))
            recipients.append(SurveyRecipient(**base, status="COMPLETED", sent_at=delivered,
                                              started_at=finished - timedelta(minutes=4), completed_at=finished))
            done += 1
            for question in questions:
                value = {}
                if question.kind == "SCALE":
                    value["number"] = r.choices((2, 3, 4, 5), weights=(1, 3, 6, 5))[0]
                elif question.kind == "SINGLE":
                    value["options"] = [r.choices(question.options, weights=range(len(question.options) + 2, 2, -1))[0]]
                else:
                    pool = cat.MANAGER_TEXTS if key == "manager" else cat.SATISFACTION_TEXTS
                    value["text"] = pool[r.randrange(len(pool))]
                answers.append(SurveyAnswer(
                    id=did("answer", key, person.number, question.position), organization=org,
                    recipient_id=base["id"], question=question, answered_at=finished, **value,
                ))
        elif status == "FINISHED":
            recipients.append(SurveyRecipient(**base, status="EXPIRED", sent_at=delivered))
        elif r.random() < 0.25:
            recipients.append(SurveyRecipient(**base, status="STARTED", sent_at=delivered,
                                              started_at=delivered + timedelta(hours=2)))
        else:
            recipients.append(SurveyRecipient(**base, status="SENT", sent_at=delivered))
    SurveyRecipient.objects.bulk_create(recipients, batch_size=500)
    SurveyAnswer.objects.bulk_create(answers, batch_size=1000)
    return {"recipients": len(recipients), "completed": done}
