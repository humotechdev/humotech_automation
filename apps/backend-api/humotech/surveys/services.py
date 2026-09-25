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
from humotech.notifications.models import Notification
from humotech.surveys.models import (
    SurveyAnswer,
    SurveyCampaign,
    SurveyQuestion,
    SurveyRecipient,
    SurveyTemplate,
)

AUDITED_TEMPLATE = ("title", "description", "status", "version", "archived_at")
AUDITED_CAMPAIGN = ("title", "status", "audience_kind", "scheduled_at",
                    "repeat_months", "next_send_at", "sent_at",
                    "remind_at", "due_at")

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
        status: str | None = None,
        limit: int | None = None, cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "surveys.read")
        # Архив — не состояние редакции, а отметка «убрано из работы»:
        # архивным бывает и черновик, и опубликованный. Поэтому
        # `ARCHIVED` отбирает по отметке, а не по полю `status`, и без
        # явной просьбы архивные в список не попадают вовсе.
        archived = status == "ARCHIVED"
        if archived:
            status = None
        queryset = (
            SurveyTemplate.objects.filter(
                organization_id=actor.organization_id,
                archived_at__isnull=not archived,
            )
            .select_related("created_by_user", "created_by_user__employee")
            .prefetch_related("questions")
            .annotate(
                # Сколько раз по шаблону спрашивали. В списке это первое,
                # на что смотрят: шаблон без рассылок и шаблон, которым
                # пользуются каждый месяц, — разные вещи.
                campaigns_count=Count("campaigns", distinct=True),
            )
        )
        if search:
            needle = search.strip()
            # Ищут и по описанию: «адаптация» чаще стоит там, чем в
            # названии вроде «Опрос после первого месяца».
            queryset = queryset.filter(
                Q(title__icontains=needle) | Q(description__icontains=needle)
            )
        if status:
            queryset = queryset.filter(status=status)
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
                if template.status == "PUBLISHED":
                    raise Conflict(
                        "Опубликованный шаблон не правят на месте: по нему "
                        "уже рассылают. Создайте новую редакцию",
                        details={"reason": "published", "id": str(template.id)},
                    )
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

    def publish(self, actor: Actor, template_id: uuid.UUID) -> SurveyTemplate:
        """Опубликовать шаблон: по нему можно рассылать.

        Пустой шаблон опубликовать нельзя — опрос без вопросов это не
        опрос, а сообщение «ответьте» без вопроса.
        """
        self.access.require(actor, "surveys.manage")
        template = self._require(actor, template_id)
        if template.status == "ARCHIVED":
            raise Conflict("Архивный шаблон не публикуют — сделайте копию")
        if not template.questions.exists():
            raise ValidationFailed(
                "В шаблоне нет ни одного вопроса",
                details={"reason": "no_questions"},
            )

        with self.atomic():
            before = snapshot(template, AUDITED_TEMPLATE)
            template.status = "PUBLISHED"
            template.published_at = timezone.now()
            template.save(update_fields=["status", "published_at", "updated_at"])
            self.audit.record(
                actor,
                action="survey.template.publish",
                entity_type="survey_templates",
                entity_id=template.id,
                before=before,
                after=snapshot(template, AUDITED_TEMPLATE),
            )
        return template

    def new_version(self, actor: Actor, template_id: uuid.UUID) -> SurveyTemplate:
        """Новая редакция опубликованного шаблона.

        Правка на месте здесь невозможна не из осторожности: по шаблону
        уже спрашивали людей, и переписанный вопрос превратил бы их
        ответы в ответы на другой вопрос. Редакция — отдельная строка со
        своими вопросами; прежняя остаётся вместе со своими рассылками.
        """
        self.access.require(actor, "surveys.manage")
        source = self._require(actor, template_id)
        if source.status == "DRAFT":
            raise Conflict("Черновик правится как есть — новая редакция не нужна")

        with self.atomic():
            copy = SurveyTemplate.objects.create(
                organization_id=source.organization_id,
                title=source.title,
                description=source.description,
                status="DRAFT",
                version=source.version + 1,
                previous_version=source,
                created_by_user_id=actor.user_id,
            )
            for question in source.questions.order_by("position"):
                SurveyQuestion.objects.create(
                    organization_id=copy.organization_id,
                    template=copy,
                    position=question.position,
                    text=question.text,
                    kind=question.kind,
                    is_required=question.is_required,
                    options=question.options,
                )
            self.audit.record(
                actor,
                action="survey.template.new_version",
                entity_type="survey_templates",
                entity_id=copy.id,
                after=snapshot(copy, AUDITED_TEMPLATE),
            )
        return copy

    def delete(self, actor: Actor, template_id: uuid.UUID) -> None:
        """Удалить черновик, по которому ни разу не спрашивали.

        Удаляется только то, у чего нет следов: опубликованный шаблон
        мог уйти в рассылку в любую минуту, а шаблон, по которому уже
        спрашивали, держит на себе ответы — без него они теряют вопросы.
        Такой шаблон убирают архивом: из списка он пропадает, а история
        остаётся целой.

        Правило о рассылках проверяется и у черновика: черновиком
        становится новая редакция опубликованного, а рассылка могла
        уйти по нему, пока он был опубликован.
        """
        self.access.require(actor, "surveys.manage")
        template = self._require(actor, template_id)
        if template.status != "DRAFT":
            raise Conflict(
                "Опубликованный шаблон не удаляют — его можно архивировать",
                details={"reason": "published"},
            )
        if template.campaigns.exists():
            raise Conflict(
                "Шаблон уже использовался в рассылке — его можно только "
                "архивировать",
                details={"reason": "has_campaigns"},
            )
        if template.automations.exists():
            raise Conflict(
                "Шаблон подключён к автоматизации — сначала уберите его "
                "оттуда или архивируйте",
                details={"reason": "has_automations"},
            )
        with self.atomic():
            self.audit.record(
                actor,
                action="survey.template.delete",
                entity_type="survey_templates",
                entity_id=template.id,
                before=snapshot(template, AUDITED_TEMPLATE),
            )
            # Вопросы держатся за шаблон `PROTECT`: сначала они, потом он.
            template.questions.all().delete()
            template.delete()

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
        remind_at: datetime | None = None, due_at: datetime | None = None,
        send_now: bool = False, is_anonymous: bool = False,
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

        # Срок ответа — после отправки, напоминание — между ними. Срок
        # раньше отправки закрыл бы приём до того, как кого-то спросили;
        # напоминание после срока пришло бы про закрытый опрос.
        start = scheduled_at or now
        if due_at is not None and due_at <= start:
            raise ValidationFailed(
                "Срок ответа раньше отправки",
                details={"field": "due_at"},
            )
        if remind_at is not None:
            if remind_at <= start:
                raise ValidationFailed(
                    "Напоминание раньше самой отправки",
                    details={"field": "remind_at"},
                )
            if due_at is not None and remind_at >= due_at:
                raise ValidationFailed(
                    "Напоминание позже срока ответа: напоминать будет не о чем",
                    details={"field": "remind_at"},
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
                remind_at=remind_at,
                due_at=due_at,
                next_send_at=scheduled_at,
                is_anonymous=bool(is_anonymous),
                created_by_user_id=actor.user_id,
            )
            if scheduled_at is not None:
                campaign.status = "SCHEDULED"
                # Круг фиксируется сейчас, а не в день отправки. У
                # повторяющейся рассылки снимка нет: она и задумана
                # спрашивать тех, кто работает на момент каждого
                # повтора.
                if not repeat_months:
                    campaign.audience_snapshot = [
                        str(person.id) for person in audience_employees(campaign)
                    ]
                campaign.save(
                    update_fields=["status", "audience_snapshot", "updated_at"]
                )
            self.audit.record(
                actor, action="survey.campaign.create",
                entity_type="survey_campaigns", entity_id=campaign.id,
                before=None, after=snapshot(campaign, AUDITED_CAMPAIGN),
            )
            if send_now:
                dispatch(campaign, now=now)
        campaign.refresh_from_db()
        return campaign

    def preview(
        self, actor: Actor, *, audience_kind: str, audience_ids: list[str],
    ) -> dict:
        """Кого спросят и до кого опрос не дойдёт — до отправки.

        Счёт тот же, что при отправке: та же функция круга и та же
        проверка привязки. Иначе на проверке было бы «42 получателя», а
        в отчёте — «40 из 42», и кадровик узнал бы о двух пропущенных
        уже после.
        """
        self.access.require(actor, "surveys.read")
        if audience_kind not in SURVEY_AUDIENCE_KINDS:
            raise ValidationFailed(
                "Неизвестный круг получателей",
                details={"audience_kind": audience_kind},
            )
        probe = SurveyCampaign(
            organization_id=actor.organization_id,
            audience_kind=audience_kind,
            audience_ids=[str(one) for one in audience_ids] or None,
        )
        people = sorted(
            audience_employees(probe),
            key=lambda one: (one.last_name or "", one.first_name or ""),
        )
        reachable = _with_telegram([person.id for person in people])
        # Выбранных руками, но уже не работающих, называем отдельно:
        # «выбрали пятерых — уйдёт четверым» без объяснения выглядит
        # как ошибка.
        gone = (
            len({str(one) for one in audience_ids}) - len(people)
            if audience_kind == "EMPLOYEES" else 0
        )
        places = places_of([person.id for person in people[:200]])
        return {
            "total": len(people),
            "reachable": sum(1 for person in people if person.id in reachable),
            "no_telegram": sum(1 for person in people if person.id not in reachable),
            "not_employed": max(gone, 0),
            "people": [
                {
                    "id": str(person.id),
                    "full_name": " ".join(
                        part for part in (
                            person.last_name, person.first_name, person.middle_name,
                        ) if part
                    ),
                    "office": (places.get(person.id) or {}).get("office"),
                    "department": (places.get(person.id) or {}).get("department"),
                    "telegram": person.id in reachable,
                }
                for person in people[:200]
            ],
        }

    def update(
        self, actor: Actor, campaign_id: uuid.UUID, payload: dict,
    ) -> SurveyCampaign:
        """Правка запланированной рассылки — до её отправки.

        Отправленную не правят: люди уже получили приглашение, и новый
        срок или другой круг сделали бы их ответы ответами на другой
        опрос. Заведённую правилом тоже: её правят через само правило.
        """
        self.access.require(actor, "surveys.manage")
        campaign = self._require(actor, campaign_id)
        if campaign.status != "SCHEDULED":
            raise Conflict(
                "Править можно только запланированную рассылку",
                details={"status": campaign.status},
            )
        if campaign.automation_id is not None:
            raise Conflict("Эту рассылку завело правило — меняйте само правило")

        before = snapshot(campaign, AUDITED_CAMPAIGN)
        now = timezone.now()
        scheduled_at = payload.get("scheduled_at", campaign.scheduled_at)
        remind_at = payload.get("remind_at", campaign.remind_at)
        due_at = payload.get("due_at", campaign.due_at)
        if scheduled_at is None or scheduled_at < now - timedelta(minutes=1):
            raise ValidationFailed(
                "Дата отправки в прошлом", details={"field": "scheduled_at"},
            )
        if due_at is not None and due_at <= scheduled_at:
            raise ValidationFailed(
                "Срок ответа раньше отправки", details={"field": "due_at"},
            )
        if remind_at is not None and (
            remind_at <= scheduled_at or (due_at is not None and remind_at >= due_at)
        ):
            raise ValidationFailed(
                "Напоминание должно быть между отправкой и сроком ответа",
                details={"field": "remind_at"},
            )

        with self.atomic():
            if "title" in payload:
                campaign.title = clean_text(
                    payload["title"] or campaign.template.title,
                    field="title", required=True, max_length=255,
                )
            campaign.scheduled_at = scheduled_at
            campaign.next_send_at = scheduled_at
            campaign.remind_at = remind_at
            campaign.due_at = due_at
            if "audience_kind" in payload:
                kind = payload["audience_kind"]
                ids = [str(one) for one in (payload.get("audience_ids") or [])]
                if kind not in SURVEY_AUDIENCE_KINDS:
                    raise ValidationFailed("Неизвестный круг получателей")
                if kind != "ALL" and not ids:
                    raise ValidationFailed(
                        "Для этого круга нужно выбрать хотя бы одного получателя",
                    )
                campaign.audience_kind = kind
                campaign.audience_ids = ids or None
                if not campaign.repeat_months:
                    campaign.audience_snapshot = None
                    campaign.audience_snapshot = [
                        str(person.id) for person in audience_employees(campaign)
                    ]
            campaign.save()
            self.audit.record(
                actor, action="survey.campaign.update",
                entity_type="survey_campaigns", entity_id=campaign.id,
                before=before, after=snapshot(campaign, AUDITED_CAMPAIGN),
            )
        campaign.refresh_from_db()
        return campaign

    def remind(
        self, actor: Actor, campaign_id: uuid.UUID,
        *, recipient_ids: list[str] | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Напомнить тем, кто ещё не ответил, — по просьбе кадровика.

        Только тем, кому можно: опрос идёт, срок не вышел, приглашение
        дошло, и ответа ещё нет. Раз в сутки на человека: два
        напоминания в один день — уже давление, а не напоминание.
        """
        self.access.require(actor, "surveys.manage")
        moment = now or timezone.now()
        campaign = self._require(actor, campaign_id)
        if campaign.status != "ACTIVE":
            raise Conflict("Напоминают только по идущей рассылке")
        if campaign.due_at is not None and campaign.due_at <= moment:
            raise Conflict("Срок ответа вышел — напоминать не о чем")

        rows = SurveyRecipient.objects.filter(
            campaign=campaign, status__in=("SENT", "STARTED"),
        )
        if recipient_ids:
            rows = rows.filter(id__in=[str(one) for one in recipient_ids])
        rows = list(rows)
        reachable = _with_telegram([row.employee_id for row in rows])
        day = moment.date().isoformat()

        sent = already = 0
        for row in rows:
            if row.employee_id not in reachable:
                continue
            key = f"survey-remind:{row.id}:{day}"
            if Notification.objects.filter(
                organization_id=campaign.organization_id, idempotency_key=key,
            ).exists():
                already += 1
                continue
            outbox.enqueue(
                organization_id=campaign.organization_id,
                employee_id=row.employee_id,
                notification_type=INVITE_TYPE,
                title=campaign.title,
                body=(
                    f"Напоминаем про опрос «{campaign.title}». "
                    "Он займёт 2–3 минуты."
                ),
                idempotency_key=key,
                related_entity_type="survey_recipients",
                related_entity_id=row.id,
            )
            sent += 1
        return {"reminded": sent, "already": already}

    def export(self, actor: Actor, campaign_id: uuid.UUID) -> tuple[list, list]:
        """Таблица ответов: кто, где, когда и что ответил.

        Опрос именной, и выгрузка тоже: без фамилий она ничем не
        отличалась бы от сводки, которая и так видна на странице.
        """
        self.access.require(actor, "surveys.read")
        campaign = self._require(actor, campaign_id)
        questions = list(
            SurveyQuestion.objects.filter(template_id=campaign.template_id)
            .order_by("position")
        )
        if campaign.is_anonymous:
            return _anonymous_export(campaign, questions)
        rows = list(
            SurveyRecipient.objects.filter(campaign_id=campaign.id)
            .select_related("employee")
            .prefetch_related("answers")
            .order_by("employee__last_name", "employee__first_name")
        )
        places = places_of([row.employee_id for row in rows])
        titles = {
            "PENDING": "Ожидает отправки", "SENT": "Ожидает ответа",
            "STARTED": "Начал отвечать", "COMPLETED": "Завершил",
            "SKIPPED": "Не доставлено", "EXPIRED": "Срок истёк",
        }
        header = [
            "Сотрудник", "Офис", "Отдел", "Состояние", "Отправлено",
            "Завершил", "Причина",
        ] + [question.text for question in questions]
        body = []
        for row in rows:
            person = row.employee
            by_question = {answer.question_id: answer for answer in row.answers.all()}
            cells = [
                " ".join(p for p in (person.last_name, person.first_name,
                                     person.middle_name) if p),
                (places.get(row.employee_id) or {}).get("office") or "",
                (places.get(row.employee_id) or {}).get("department") or "",
                titles.get(row.status, row.status),
                row.sent_at.isoformat() if row.sent_at else "",
                row.completed_at.isoformat() if row.completed_at else "",
                row.skip_reason or "",
            ]
            for question in questions:
                answer = by_question.get(question.id)
                if answer is None:
                    cells.append("")
                elif answer.number is not None:
                    cells.append(str(answer.number))
                elif answer.options:
                    cells.append("; ".join(answer.options))
                else:
                    cells.append(answer.text or "")
            body.append(cells)
        return header, body

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
        if campaign.is_anonymous:
            # Анонимный опрос: ответов по людям нет ни у кого, включая
            # кадровика. Сводка по вопросам — в `summary`.
            return []
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


def _anonymous_export(campaign: SurveyCampaign, questions: list) -> tuple[list, list]:
    """Выгрузка анонимного опроса: только ответы, без людей.

    Ни имени, ни офиса, ни времени — по времени ответа человека узнать
    так же легко, как по фамилии. Порядок строк — по хешу идентификатора,
    а не по времени и не по алфавиту.
    """
    import hashlib

    rows = list(
        SurveyRecipient.objects.filter(campaign_id=campaign.id, status="COMPLETED")
        .prefetch_related("answers")
    )
    rows.sort(key=lambda row: hashlib.sha256(str(row.id).encode()).hexdigest())
    header = ["№"] + [question.text for question in questions]
    body = []
    for number, row in enumerate(rows, start=1):
        by_question = {answer.question_id: answer for answer in row.answers.all()}
        cells = [str(number)]
        for question in questions:
            answer = by_question.get(question.id)
            if answer is None:
                cells.append("")
            elif answer.number is not None:
                cells.append(str(answer.number))
            elif answer.options:
                cells.append("; ".join(answer.options))
            else:
                cells.append(answer.text or "")
        body.append(cells)
    return header, body


def audience_employees(campaign: SurveyCampaign) -> list[Employee]:
    """Кого спрашиваем. Работающие — уволенным опрос не отправляют.

    Если у рассылки есть снимок, круг — он: его кадровик утвердил на
    проверке, и в день отправки он не пересчитывается.
    """
    people = Employee.objects.filter(
        organization_id=campaign.organization_id,
        employment_status__in=("ACTIVE", "PROBATION"),
    )
    if campaign.audience_snapshot is not None:
        frozen = [str(one) for one in campaign.audience_snapshot]
        return list(people.filter(id__in=frozen))
    ids = [str(one) for one in (campaign.audience_ids or [])]
    if campaign.audience_kind == "EMPLOYEES":
        return list(people.filter(id__in=ids))
    if campaign.audience_kind == "REGION":
        assigned = EmployeeAssignment.objects.filter(
            office__region_id__in=ids, is_primary=True
        ).values_list("employee_id", flat=True)
        return list(people.filter(id__in=list(assigned)))
    if campaign.audience_kind == "POSITION":
        assigned = EmployeeAssignment.objects.filter(
            position_id__in=ids, is_primary=True
        ).values_list("employee_id", flat=True)
        return list(people.filter(id__in=list(assigned)))
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
    reachable = _with_telegram([person.id for person in people])

    # Кадровик утвердил этих людей, а к дню отправки кто-то уволился.
    # Промолчать нельзя: в отчёте было бы «24 получателя» при 25
    # утверждённых. Такая строка получает исход и причину.
    if campaign.audience_snapshot is not None:
        present = {str(person.id) for person in people}
        for gone in Employee.objects.filter(
            organization_id=campaign.organization_id,
            id__in=[
                one for one in map(str, campaign.audience_snapshot)
                if one not in present
            ],
        ):
            SurveyRecipient.objects.get_or_create(
                organization_id=campaign.organization_id,
                campaign=campaign,
                employee=gone,
                defaults={
                    "status": "SKIPPED",
                    "skip_reason": "Уже не работает в компании",
                },
            )

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

        # Опрос живёт в Telegram. Человеку без привязки его отправить
        # некуда, и «отправляем» на такой строке — вечное ожидание,
        # которое к тому же портит счёт по всей рассылке. Это исход, а
        # не ошибка, и у него есть причина словами.
        if person.id not in reachable:
            recipient.status = "SKIPPED"
            recipient.skip_reason = "Нет привязки Telegram"
            recipient.save(
                update_fields=["status", "skip_reason", "updated_at"]
            )
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
    # Редакция шаблона запоминается здесь и больше не меняется. Вопросы
    # потом могут уйти в новую редакцию — отчёт по этой рассылке обязан
    # показывать то, что спрашивали на самом деле.
    campaign.template_version = campaign.template.version
    campaign.next_send_at = (
        moment + timedelta(days=30 * campaign.repeat_months)
        if campaign.repeat_months
        else None
    )
    campaign.save(
        update_fields=[
            "status", "sent_at", "template_version", "next_send_at", "updated_at",
        ]
    )
    return created


def _with_telegram(employee_ids: list) -> set:
    """Кому вообще можно написать: у кого привязка подтверждена."""
    from humotech.telegram.models import TelegramAccount

    return set(
        TelegramAccount.objects
        .filter(employee_id__in=employee_ids, status="ACTIVE")
        .values_list("employee_id", flat=True)
    )


def remind_due(*, now: datetime | None = None) -> int:
    """Напомнить тем, кто не закончил, и закрыть просроченное.

    Напоминание уходит только незавершившим: человеку, который уже
    ответил, второе «пройдите опрос» говорит, что его ответ потеряли.

    Вызывается оттуда же, откуда рассылка по расписанию, — из очереди
    уведомлений. Отдельный планировщик ради двух дат был бы лишней
    движущейся частью.
    """
    moment = now or timezone.now()
    touched = 0

    waiting = ("PENDING", "SENT", "STARTED")
    for campaign in SurveyCampaign.objects.filter(
        status="ACTIVE", remind_at__isnull=False,
        remind_at__lte=moment, reminded_at__isnull=True,
    ):
        for recipient in SurveyRecipient.objects.filter(
            campaign=campaign, status__in=waiting
        ):
            outbox.enqueue(
                organization_id=campaign.organization_id,
                employee_id=recipient.employee_id,
                notification_type=INVITE_TYPE,
                title=campaign.title,
                body=(
                    f"Напоминаем про опрос «{campaign.title}». "
                    "Он займёт 2–3 минуты."
                ),
                # Ключ повтора свой: напоминание — второе сообщение по
                # той же строке, и общий ключ проглотил бы его.
                idempotency_key=f"survey-remind:{recipient.id}",
                related_entity_type="survey_recipients",
                related_entity_id=recipient.id,
            )
            touched += 1
        campaign.reminded_at = moment
        campaign.save(update_fields=["reminded_at", "updated_at"])

    # Срок вышел — приём закрыт. Строка получает свой исход, а не
    # остаётся «отправлено» навсегда.
    for campaign in SurveyCampaign.objects.filter(
        status="ACTIVE", due_at__isnull=False, due_at__lte=moment,
    ):
        SurveyRecipient.objects.filter(
            campaign=campaign, status__in=waiting
        ).update(status="EXPIRED", updated_at=moment)
        campaign.status = "FINISHED"
        campaign.save(update_fields=["status", "updated_at"])

    return touched


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
    """Счёт по рассылке.

    Пропущенные считаются отдельно и не входят ни в отправленных, ни в
    доли прошедших: «прошли 6 из 10» при двух, до кого опрос не дошёл,
    занижает результат вдвое и ставит кадровику не тот вопрос.
    """
    rows = SurveyRecipient.objects.filter(campaign_id=campaign.id)
    skipped = rows.filter(status="SKIPPED").count()
    total = rows.count()
    return {
        "total": total,
        "reachable": total - skipped,
        "sent": rows.filter(status__in=("SENT", "STARTED", "COMPLETED")).count(),
        "started": rows.filter(status__in=("STARTED", "COMPLETED")).count(),
        "completed": rows.filter(status="COMPLETED").count(),
        "skipped": skipped,
        "expired": rows.filter(status="EXPIRED").count(),
    }


# --- сторона сотрудника --------------------------------------------------------


def pending_for(
    employee_id: uuid.UUID, *, now: datetime | None = None
) -> list[SurveyRecipient]:
    """Опросы, которые человеку ещё предстоит пройти.

    Опросы с вышедшим сроком сюда не попадают, даже если
    очередь ещё не успела проставить исход: показать опрос и
    отказать на первом же нажатии хуже, чем не показывать вовсе.
    """
    moment = now or timezone.now()
    return list(
        SurveyRecipient.objects.filter(
            Q(campaign__due_at__isnull=True) | Q(campaign__due_at__gt=moment),
            employee_id=employee_id,
            status__in=("SENT", "STARTED"),
        )
        .exclude(campaign__status="CANCELLED")
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
    _refuse_if_closed(recipient)
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
    _refuse_if_closed(recipient, now=moment)

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


def _refuse_if_closed(
    recipient: SurveyRecipient, *, now: datetime | None = None
) -> None:
    """Приём закрыт — значит закрыт.

    «Закрыть опрос через 14 дней» — обещание обеим сторонам:
    сотруднику — что после этого с него не спросят, кадровику —
    что числа больше не меняются. Ответ, пришедший после
    срока, нарушил бы второе: отчёт, показанный вчера,
    сегодня стал бы другим.
    """
    campaign = recipient.campaign
    if recipient.status == "EXPIRED":
        raise Conflict("Срок ответа на этот опрос вышел")
    if campaign.due_at is not None and (now or timezone.now()) >= campaign.due_at:
        raise Conflict("Срок ответа на этот опрос вышел")
    if campaign.status == "CANCELLED":
        raise Conflict("Рассылка отменена")


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
    "remind_due",
    "pending_for",
    "progress_of",
    "submit",
]
