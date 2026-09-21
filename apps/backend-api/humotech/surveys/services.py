"""Правила опросов: шаблоны, рассылки, ответы.

Главное правило одно и оно про честность перед сотрудником: опрос
именной. Ответ всегда привязан к человеку, и ни один путь не создаёт
ответ «ничей» — получатель обязателен по схеме.

Второе правило — про время. Кто получит опрос, решается в момент
отправки, а не в момент создания рассылки: за неделю состав отдела
меняется, и спрашивать надо тех, кто работает сейчас. Поэтому строки
получателей появляются при отправке, а не при сохранении.

Третье — про повтор. «Раз в два месяца» означает, что после отправки
назначается следующая дата; каждая отправка — своя рассылка получателей
и свои ответы, иначе ответы разных месяцев смешались бы в один.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from humotech.core.enums import (
    SURVEY_AUDIENCE_KINDS,
    SURVEY_QUESTION_KINDS,
)
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import clean_text
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.notifications import outbox
from humotech.surveys.models import (
    SurveyAnswer,
    SurveyCampaign,
    SurveyQuestion,
    SurveyRecipient,
    SurveyTemplate,
)

AUDITED_TEMPLATE = ("title", "description", "archived_at")
AUDITED_CAMPAIGN = ("title", "status", "audience_kind", "scheduled_at",
                    "repeat_months", "next_send_at", "sent_at")

#: Сколько вопросов допускается в шаблоне. Двадцать — это уже не
#: «короткий опрос на две минуты», а анкета, которую бросают на середине.
MAX_QUESTIONS = 20
SCALE_MIN, SCALE_MAX = 1, 5

#: Тип уведомления приглашения. По нему очередь собирает кнопку.
INVITE_TYPE = "survey.invite"
THANKS_TYPE = "survey.completed"


def _questions_payload(raw: list[dict] | None) -> list[dict]:
    """Проверить вопросы целиком, до записи.

    Частично сохранённый шаблон хуже несохранённого: HR отправит его,
    не заметив, что половина вопросов потерялась.
    """
    if not raw:
        raise ValidationFailed(
            "В опросе должен быть хотя бы один вопрос",
            details={"questions": ["Добавьте вопрос."]},
        )
    if len(raw) > MAX_QUESTIONS:
        raise ValidationFailed(
            f"Вопросов не больше {MAX_QUESTIONS}: длинный опрос не проходят",
            details={"questions": [f"Не больше {MAX_QUESTIONS}."]},
        )

    cleaned: list[dict] = []
    for index, item in enumerate(raw, start=1):
        kind = str(item.get("kind") or "").upper()
        if kind not in SURVEY_QUESTION_KINDS:
            raise ValidationFailed(
                "Неизвестный тип вопроса",
                details={"kind": kind, "allowed": list(SURVEY_QUESTION_KINDS)},
            )
        text = clean_text(item.get("text"), field="text", required=True,
                          max_length=500)
        options = item.get("options")
        if kind in ("SINGLE", "MULTI"):
            values = [
                clean_text(one, field="options", required=True, max_length=200)
                for one in (options or [])
            ]
            if len(values) < 2:
                raise ValidationFailed(
                    "У вопроса с вариантами их должно быть хотя бы два",
                    details={"question": text, "options": values},
                )
            options = values
        else:
            options = None
        cleaned.append({
            "position": index,
            "text": text,
            "kind": kind,
            "is_required": bool(item.get("is_required", True)),
            "options": options,
        })
    return cleaned


class SurveyTemplateService(BaseService):
    """Шаблоны. Читает `surveys.read`, меняет `surveys.manage`."""

    def list(
        self, actor: Actor, *, search: str | None = None,
        limit: int | None = None, cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "surveys.read")
        queryset = (
            SurveyTemplate.objects.filter(
                organization_id=actor.organization_id, archived_at__isnull=True
            )
            .select_related("created_by_user", "created_by_user__employee")
            .prefetch_related("questions")
        )
        if search:
            queryset = queryset.filter(title__icontains=search.strip())
        return paginate(queryset, limit=limit, cursor=cursor)

    def get(self, actor: Actor, template_id: uuid.UUID) -> SurveyTemplate:
        self.access.require(actor, "surveys.read")
        return self._require(actor, template_id)

    def create(
        self, actor: Actor, *, title: str, description: str | None = None,
        questions: list[dict] | None = None,
    ) -> SurveyTemplate:
        self.access.require(actor, "surveys.manage")
        payload = _questions_payload(questions)
        with self.atomic():
            template = SurveyTemplate.objects.create(
                organization_id=actor.organization_id,
                title=clean_text(title, field="title", required=True, max_length=255),
                description=clean_text(description, field="description",
                                       max_length=2000),
                created_by_user_id=actor.user_id,
            )
            self._write_questions(actor, template, payload)
            self.audit.record(
                actor, action="survey.template.create",
                entity_type="survey_templates", entity_id=template.id,
                before=None, after=snapshot(template, AUDITED_TEMPLATE),
            )
        return template

    def update(
        self, actor: Actor, template_id: uuid.UUID, *, title: str | None = None,
        description: str | None = None, questions: list[dict] | None = None,
    ) -> SurveyTemplate:
        """Правка шаблона.

        Вопросы, на которые уже отвечали, не переписываются: у них есть
        ответы, и смена текста задним числом превратила бы их в ответы
        на другой вопрос. Такой шаблон копируют, а не правят.
        """
        self.access.require(actor, "surveys.manage")
        template = self._require(actor, template_id)
        before = snapshot(template, AUDITED_TEMPLATE)

        if title is not None:
            template.title = clean_text(title, field="title", required=True,
                                        max_length=255)
        if description is not None:
            template.description = clean_text(
                description, field="description", max_length=2000
            )

        with self.atomic():
            template.save()
            if questions is not None:
                if SurveyAnswer.objects.filter(
                    question__template_id=template.id
                ).exists():
                    raise Conflict(
                        "На вопросы этого шаблона уже отвечали: чтобы изменить "
                        "их, скопируйте шаблон — иначе прежние ответы окажутся "
                        "ответами на другой вопрос",
                        details={"template_id": str(template.id)},
                    )
                SurveyQuestion.objects.filter(template_id=template.id).delete()
                self._write_questions(actor, template, _questions_payload(questions))
            self.audit.record(
                actor, action="survey.template.update",
                entity_type="survey_templates", entity_id=template.id,
                before=before, after=snapshot(template, AUDITED_TEMPLATE),
            )
        return template

    def copy(self, actor: Actor, template_id: uuid.UUID) -> SurveyTemplate:
        """Копия шаблона со всеми вопросами — основа для правки."""
        self.access.require(actor, "surveys.manage")
        source = self._require(actor, template_id)
        questions = [
            {
                "text": question.text,
                "kind": question.kind,
                "is_required": question.is_required,
                "options": question.options,
            }
            for question in source.questions.order_by("position")
        ]
        return self.create(
            actor,
            title=f"{source.title} — копия",
            description=source.description,
            questions=questions,
        )

    def archive(self, actor: Actor, template_id: uuid.UUID) -> SurveyTemplate:
        """Шаблон убирают из списка, а не удаляют: на него ссылаются рассылки."""
        self.access.require(actor, "surveys.manage")
        template = self._require(actor, template_id)
        before = snapshot(template, AUDITED_TEMPLATE)
        with self.atomic():
            template.archived_at = timezone.now()
            template.save(update_fields=["archived_at", "updated_at"])
            self.audit.record(
                actor, action="survey.template.archive",
                entity_type="survey_templates", entity_id=template.id,
                before=before, after=snapshot(template, AUDITED_TEMPLATE),
            )
        return template

    # --- внутреннее ---------------------------------------------------------

    def _write_questions(
        self, actor: Actor, template: SurveyTemplate, payload: list[dict]
    ) -> None:
        SurveyQuestion.objects.bulk_create([
            SurveyQuestion(
                organization_id=actor.organization_id,
                template=template,
                position=item["position"],
                text=item["text"],
                kind=item["kind"],
                is_required=item["is_required"],
                options=item["options"],
            )
            for item in payload
        ])

    def _require(self, actor: Actor, template_id: uuid.UUID) -> SurveyTemplate:
        template = (
            SurveyTemplate.objects
            .select_related("created_by_user", "created_by_user__employee")
            .prefetch_related("questions")
            .filter(id=template_id, organization_id=actor.organization_id)
            .first()
        )
        if template is None:
            raise NotFound("Шаблон опроса не найден")
        return template


@dataclass(frozen=True)
class Progress:
    """Сколько получателей в каком состоянии."""

    total: int
    sent: int
    started: int
    completed: int


class SurveyCampaignService(BaseService):
    """Рассылки: кому, когда и что ответили."""

    def list(
        self, actor: Actor, *, status: str | None = None,
        limit: int | None = None, cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "surveys.read")
        queryset = (
            SurveyCampaign.objects.filter(organization_id=actor.organization_id)
            .select_related("template")
            .annotate(
                total=Count("recipients", distinct=True),
                done=Count(
                    "recipients",
                    filter=Q(recipients__status="COMPLETED"),
                    distinct=True,
                ),
            )
        )
        if status:
            queryset = queryset.filter(status=status)
        return paginate(queryset, limit=limit, cursor=cursor)

    def get(self, actor: Actor, campaign_id: uuid.UUID) -> SurveyCampaign:
        self.access.require(actor, "surveys.read")
        return self._require(actor, campaign_id)

    def create(
        self, actor: Actor, *, template_id: uuid.UUID, title: str | None = None,
        audience_kind: str = "ALL", audience_ids: list[str] | None = None,
        scheduled_at: datetime | None = None, repeat_months: int | None = None,
        send_now: bool = False,
    ) -> SurveyCampaign:
        self.access.require(actor, "surveys.manage")
        template = SurveyTemplateService()._require(actor, template_id)
        if not template.questions.exists():
            raise Conflict("В шаблоне нет вопросов: отправлять нечего")

        if audience_kind not in SURVEY_AUDIENCE_KINDS:
            raise ValidationFailed(
                "Неизвестный круг получателей",
                details={"audience_kind": audience_kind,
                         "allowed": list(SURVEY_AUDIENCE_KINDS)},
            )
        if audience_kind != "ALL" and not audience_ids:
            raise ValidationFailed(
                "Для этого круга нужно выбрать хотя бы одного получателя",
                details={"audience_ids": ["Пусто."]},
            )
        if repeat_months is not None and repeat_months not in (2, 3):
            raise ValidationFailed(
                "Повтор бывает раз в два или три месяца",
                details={"repeat_months": repeat_months},
            )

        now = timezone.now()
        if scheduled_at is not None and scheduled_at < now - timedelta(minutes=1):
            raise ValidationFailed(
                "Дата отправки в прошлом: опрос нельзя отправить задним числом",
                details={"scheduled_at": scheduled_at.isoformat()},
            )

        with self.atomic():
            campaign = SurveyCampaign.objects.create(
                organization_id=actor.organization_id,
                template=template,
                title=clean_text(title or template.title, field="title",
                                 required=True, max_length=255),
                status="DRAFT",
                audience_kind=audience_kind,
                audience_ids=[str(one) for one in (audience_ids or [])] or None,
                scheduled_at=scheduled_at,
                repeat_months=repeat_months,
                next_send_at=scheduled_at,
                created_by_user_id=actor.user_id,
            )
            if scheduled_at is not None:
                campaign.status = "SCHEDULED"
                campaign.save(update_fields=["status", "updated_at"])
            self.audit.record(
                actor, action="survey.campaign.create",
                entity_type="survey_campaigns", entity_id=campaign.id,
                before=None, after=snapshot(campaign, AUDITED_CAMPAIGN),
            )
            if send_now:
                dispatch(campaign, now=now)
        campaign.refresh_from_db()
        return campaign

    def send(self, actor: Actor, campaign_id: uuid.UUID) -> SurveyCampaign:
        """Отправить сейчас — в том числе запланированную раньше срока."""
        self.access.require(actor, "surveys.manage")
        campaign = self._require(actor, campaign_id)
        if campaign.status == "CANCELLED":
            raise Conflict("Рассылка отменена: отправлять нечего")
        with self.atomic():
            dispatch(campaign)
            self.audit.record(
                actor, action="survey.campaign.send",
                entity_type="survey_campaigns", entity_id=campaign.id,
                before=None, after=snapshot(campaign, AUDITED_CAMPAIGN),
            )
        campaign.refresh_from_db()
        return campaign

    def cancel(self, actor: Actor, campaign_id: uuid.UUID) -> SurveyCampaign:
        """Отменить: повторов больше не будет, ответы остаются."""
        self.access.require(actor, "surveys.manage")
        campaign = self._require(actor, campaign_id)
        before = snapshot(campaign, AUDITED_CAMPAIGN)
        with self.atomic():
            campaign.status = "CANCELLED"
            campaign.next_send_at = None
            campaign.save(update_fields=["status", "next_send_at", "updated_at"])
            self.audit.record(
                actor, action="survey.campaign.cancel",
                entity_type="survey_campaigns", entity_id=campaign.id,
                before=before, after=snapshot(campaign, AUDITED_CAMPAIGN),
            )
        return campaign

    def recipients(
        self, actor: Actor, campaign_id: uuid.UUID, *, status: str | None = None,
    ) -> list[SurveyRecipient]:
        self.access.require(actor, "surveys.read")
        campaign = self._require(actor, campaign_id)
        rows = (
            SurveyRecipient.objects.filter(campaign_id=campaign.id)
            .select_related("employee")
            .order_by("employee__last_name", "employee__first_name")
        )
        if status:
            rows = rows.filter(status=status)
        return list(rows)

    def answers(self, actor: Actor, campaign_id: uuid.UUID) -> list[SurveyRecipient]:
        """Ответы по людям. Опрос именной: имя стоит рядом с ответом."""
        self.access.require(actor, "surveys.read")
        campaign = self._require(actor, campaign_id)
        return list(
            SurveyRecipient.objects.filter(
                campaign_id=campaign.id, status="COMPLETED"
            )
            .select_related("employee")
            .prefetch_related("answers", "answers__question")
            .order_by("completed_at")
        )

    def summary(self, actor: Actor, campaign_id: uuid.UUID) -> dict:
        """Сводка по вопросам, офисам и отделам.

        Сводка не заменяет ответы и не делает опрос анонимным: рядом с
        ней в интерфейсе стоят те же ответы с фамилиями.
        """
        self.access.require(actor, "surveys.read")
        campaign = self._require(actor, campaign_id)
        recipients = list(
            SurveyRecipient.objects.filter(campaign_id=campaign.id)
            .select_related("employee")
            .prefetch_related("answers", "answers__question")
        )
        places = places_of([row.employee_id for row in recipients])

        questions = list(
            SurveyQuestion.objects.filter(template_id=campaign.template_id)
            .order_by("position")
        )
        by_question: list[dict] = []
        for question in questions:
            answers = [
                answer
                for row in recipients
                for answer in row.answers.all()
                if answer.question_id == question.id
            ]
            item: dict = {
                "id": str(question.id),
                "text": question.text,
                "kind": question.kind,
                "answered": len(answers),
            }
            if question.kind == "SCALE":
                numbers = [a.number for a in answers if a.number is not None]
                item["average"] = (
                    round(sum(numbers) / len(numbers), 2) if numbers else None
                )
                item["distribution"] = {
                    str(value): sum(1 for n in numbers if n == value)
                    for value in range(SCALE_MIN, SCALE_MAX + 1)
                }
            elif question.kind in ("SINGLE", "MULTI"):
                counts: dict[str, int] = {
                    option: 0 for option in (question.options or [])
                }
                for answer in answers:
                    for option in (answer.options or []):
                        counts[option] = counts.get(option, 0) + 1
                item["distribution"] = counts
            else:
                item["texts"] = [a.text for a in answers if a.text]
            by_question.append(item)

        return {
            "progress": progress_of(campaign),
            "questions": by_question,
            "offices": _group(recipients, places, "office"),
            "departments": _group(recipients, places, "department"),
        }

    # --- внутреннее ---------------------------------------------------------

    def _require(self, actor: Actor, campaign_id: uuid.UUID) -> SurveyCampaign:
        campaign = (
            SurveyCampaign.objects.select_related("template")
            .filter(id=campaign_id, organization_id=actor.organization_id)
            .first()
        )
        if campaign is None:
            raise NotFound("Рассылка опроса не найдена")
        return campaign


# --- отправка ----------------------------------------------------------------


def audience_employees(campaign: SurveyCampaign) -> list[Employee]:
    """Кого спрашиваем сейчас. Считается в момент отправки."""
    people = Employee.objects.filter(
        organization_id=campaign.organization_id,
        employment_status__in=("ACTIVE", "PROBATION"),
    )
    ids = [str(one) for one in (campaign.audience_ids or [])]
    if campaign.audience_kind == "EMPLOYEES":
        return list(people.filter(id__in=ids))
    if campaign.audience_kind == "OFFICE":
        assigned = EmployeeAssignment.objects.filter(
            office_id__in=ids, is_primary=True
        ).values_list("employee_id", flat=True)
        return list(people.filter(id__in=list(assigned)))
    if campaign.audience_kind == "DEPARTMENT":
        assigned = EmployeeAssignment.objects.filter(
            department_id__in=ids, is_primary=True
        ).values_list("employee_id", flat=True)
        return list(people.filter(id__in=list(assigned)))
    return list(people)


def dispatch(campaign: SurveyCampaign, *, now: datetime | None = None) -> int:
    """Разослать опрос: строки получателей и по одному сообщению каждому.

    Сообщение одно — приглашение с кнопкой. Вопросы в чат не сыплются:
    их показывает Mini App, и это не украшение, а условие того, чтобы
    опрос вообще прошли до конца.

    Повторная отправка тем же людям новых строк не создаёт: у пары
    «рассылка + сотрудник» уникальный ключ, а уведомление защищено
    ключом идемпотентности.
    """
    moment = now or timezone.now()
    people = audience_employees(campaign)

    created = 0
    for person in people:
        recipient, is_new = SurveyRecipient.objects.get_or_create(
            organization_id=campaign.organization_id,
            campaign=campaign,
            employee=person,
            defaults={"status": "PENDING"},
        )
        if not is_new and recipient.status != "PENDING":
            continue
        outbox.enqueue(
            organization_id=campaign.organization_id,
            employee_id=person.id,
            notification_type=INVITE_TYPE,
            title=campaign.title,
            body=(
                f"HR просит пройти короткий опрос «{campaign.title}». "
                "Это займёт 2–3 минуты."
            ),
            idempotency_key=f"survey:{recipient.id}",
            related_entity_type="survey_recipients",
            related_entity_id=recipient.id,
        )
        recipient.status = "SENT"
        recipient.sent_at = moment
        recipient.save(update_fields=["status", "sent_at", "updated_at"])
        created += 1

    campaign.status = "ACTIVE"
    campaign.sent_at = moment
    campaign.next_send_at = (
        moment + timedelta(days=30 * campaign.repeat_months)
        if campaign.repeat_months
        else None
    )
    campaign.save(
        update_fields=["status", "sent_at", "next_send_at", "updated_at"]
    )
    return created


def dispatch_due(*, now: datetime | None = None) -> int:
    """Разослать всё, чему подошёл срок: запланированное и повторы.

    Вызывается из очереди уведомлений: она и так опрашивает сервер
    каждые несколько секунд, и отдельный планировщик ради двух дат в
    году был бы лишней движущейся частью.
    """
    moment = now or timezone.now()
    due = SurveyCampaign.objects.filter(
        next_send_at__isnull=False,
        next_send_at__lte=moment,
    ).exclude(status="CANCELLED")

    sent = 0
    for campaign in due:
        with transaction.atomic():
            sent += dispatch(campaign, now=moment)
    return sent


def progress_of(campaign: SurveyCampaign) -> dict:
    rows = SurveyRecipient.objects.filter(campaign_id=campaign.id)
    return {
        "total": rows.count(),
        "sent": rows.filter(status__in=("SENT", "STARTED", "COMPLETED")).count(),
        "started": rows.filter(status__in=("STARTED", "COMPLETED")).count(),
        "completed": rows.filter(status="COMPLETED").count(),
    }


# --- сторона сотрудника --------------------------------------------------------


def pending_for(employee_id: uuid.UUID) -> list[SurveyRecipient]:
    """Опросы, которые человеку ещё предстоит пройти."""
    return list(
        SurveyRecipient.objects.filter(
            employee_id=employee_id, status__in=("SENT", "STARTED")
        )
        .select_related("campaign", "campaign__template")
        .order_by("sent_at")
    )


def open_survey(*, employee_id: uuid.UUID, recipient_id: uuid.UUID) -> SurveyRecipient:
    """Открыть опрос: вопросы и уже сохранённые ответы.

    Чужой опрос не открывается: строка ищется по паре «получатель и
    сотрудник», а сотрудник берётся из проверенной привязки Telegram, а
    не из запроса.
    """
    recipient = (
        SurveyRecipient.objects.select_related("campaign", "campaign__template")
        .filter(id=recipient_id, employee_id=employee_id)
        .first()
    )
    if recipient is None:
        raise NotFound("Опрос не найден")
    if recipient.status == "SENT":
        recipient.status = "STARTED"
        recipient.started_at = timezone.now()
        recipient.save(update_fields=["status", "started_at", "updated_at"])
    return recipient


def submit(
    *, employee_id: uuid.UUID, recipient_id: uuid.UUID, answers: list[dict],
    now: datetime | None = None,
) -> SurveyRecipient:
    """Принять ответы целиком.

    Целиком, а не по одному: опрос проходят за раз, и половина ответов в
    базе означала бы, что HR читает обрывок и считает его мнением.
    Повторная отправка завершённого опроса отклоняется — иначе человек
    мог бы переписать ответ после разговора с руководителем.
    """
    moment = now or timezone.now()
    recipient = (
        SurveyRecipient.objects.select_related("campaign")
        .filter(id=recipient_id, employee_id=employee_id)
        .first()
    )
    if recipient is None:
        raise NotFound("Опрос не найден")
    if recipient.status == "COMPLETED":
        raise Conflict("Этот опрос уже пройден")

    questions = {
        question.id: question
        for question in SurveyQuestion.objects.filter(
            template_id=recipient.campaign.template_id
        )
    }
    prepared: list[SurveyAnswer] = []
    seen: set[uuid.UUID] = set()

    for item in answers:
        try:
            question_id = uuid.UUID(str(item.get("question_id")))
        except (ValueError, TypeError):
            raise ValidationFailed("Вопрос не распознан") from None
        question = questions.get(question_id)
        if question is None:
            raise ValidationFailed(
                "Вопроса нет в этом опросе",
                details={"question_id": str(question_id)},
            )
        value = _answer_value(question, item)
        if value is None:
            continue
        seen.add(question_id)
        prepared.append(
            SurveyAnswer(
                organization_id=recipient.organization_id,
                recipient=recipient,
                question=question,
                answered_at=moment,
                **value,
            )
        )

    missing = [
        question.text
        for question in questions.values()
        if question.is_required and question.id not in seen
    ]
    if missing:
        raise ValidationFailed(
            "Не отвечены обязательные вопросы",
            details={"questions": missing},
        )

    with transaction.atomic():
        SurveyAnswer.objects.filter(recipient_id=recipient.id).delete()
        SurveyAnswer.objects.bulk_create(prepared)
        recipient.status = "COMPLETED"
        recipient.completed_at = moment
        if recipient.started_at is None:
            recipient.started_at = moment
        recipient.save(
            update_fields=["status", "completed_at", "started_at", "updated_at"]
        )
        outbox.enqueue(
            organization_id=recipient.organization_id,
            employee_id=recipient.employee_id,
            notification_type=THANKS_TYPE,
            body="Спасибо, опрос завершён.",
            idempotency_key=f"survey-done:{recipient.id}",
            related_entity_type="survey_recipients",
            related_entity_id=recipient.id,
        )
    return recipient


def _answer_value(question: SurveyQuestion, item: dict) -> dict | None:
    """Одно значение в тех колонках, где его потом ищут."""
    if question.kind == "TEXT":
        text = clean_text(item.get("text"), field="text", max_length=2000)
        return {"text": text} if text else None
    if question.kind == "SCALE":
        raw = item.get("number")
        if raw in (None, ""):
            return None
        try:
            number = int(raw)
        except (TypeError, ValueError):
            raise ValidationFailed("Оценка должна быть числом") from None
        if not SCALE_MIN <= number <= SCALE_MAX:
            raise ValidationFailed(
                f"Оценка бывает от {SCALE_MIN} до {SCALE_MAX}",
                details={"number": number},
            )
        return {"number": number}

    chosen = [str(one) for one in (item.get("options") or []) if str(one).strip()]
    if not chosen:
        return None
    allowed = set(question.options or [])
    unknown = [one for one in chosen if one not in allowed]
    if unknown:
        raise ValidationFailed(
            "Такого варианта у вопроса нет",
            details={"options": unknown},
        )
    if question.kind == "SINGLE" and len(chosen) > 1:
        raise ValidationFailed(
            "В этом вопросе выбирают один вариант",
            details={"options": chosen},
        )
    return {"options": chosen}


# --- сводка по местам ----------------------------------------------------------


def places_of(employee_ids: list[uuid.UUID]) -> dict:
    """Офис и отдел каждого — из действующего основного назначения."""
    rows = (
        EmployeeAssignment.objects.filter(
            employee_id__in=employee_ids, is_primary=True
        )
        .select_related("office", "department")
        .values("employee_id", "office__name", "department__name")
    )
    return {
        row["employee_id"]: {
            "office": row["office__name"],
            "department": row["department__name"],
        }
        for row in rows
    }


def _group(recipients: list[SurveyRecipient], places: dict, key: str) -> list[dict]:
    """Сколько отправлено и сколько прошло — по офисам или отделам."""
    buckets: dict[str, dict] = {}
    for row in recipients:
        name = (places.get(row.employee_id) or {}).get(key) or "Без назначения"
        bucket = buckets.setdefault(
            name, {"name": name, "total": 0, "completed": 0}
        )
        bucket["total"] += 1
        if row.status == "COMPLETED":
            bucket["completed"] += 1
    return sorted(buckets.values(), key=lambda item: item["name"])


__all__ = [
    "INVITE_TYPE",
    "THANKS_TYPE",
    "places_of",
    "SurveyCampaignService",
    "SurveyTemplateService",
    "audience_employees",
    "dispatch",
    "dispatch_due",
    "open_survey",
    "pending_for",
    "progress_of",
    "submit",
]
