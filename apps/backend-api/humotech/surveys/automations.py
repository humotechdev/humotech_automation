"""Автоматические опросы: правило, событие, отправка.

Третья сущность рядом с шаблоном и рассылкой. Шаблон отвечает на «что
спрашивают», рассылка — на «кого спросили в тот день», автоматизация —
на «почему спросят завтра». Свести её с рассылкой нельзя: у правила нет
получателей и не будет до самого события.

Круг людей выбирается В МОМЕНТ СОБЫТИЯ, а не при создании правила: к
концу стажировки отдел у человека бывает уже другой, а кого-то из
списка успевают уволить.

Один и тот же опрос по одному и тому же поводу человек получает ровно
один раз. Держится это не на памяти воркера, а на самих данных: у пары
«правило + повод» уникальный ключ, и у пары «рассылка + сотрудник» свой.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.timeframes import organization_zone
from humotech.core.validation import clean_text
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.surveys.models import (
    SurveyAutomation,
    SurveyCampaign,
    SurveyRecipient,
    SurveyTemplate,
)
from humotech.surveys.services import (
    dispatch,
    require_whole_organization,
    visible_employee_ids,
)

logger = logging.getLogger("humotech.surveys")

#: Ключи условия правила и потолок длины каждого списка.
SCOPE_KEYS = ("office_ids", "department_ids", "position_ids")
SCOPE_MAX_IDS = 500
#: Сдвиг от события — не больше десяти лет. Больше не имеет смысла, а
#: миллион дней валил расчёт даты прямо в очереди уведомлений.
MAX_OFFSET_DAYS = 3650

AUDITED = (
    "title", "trigger_kind", "offset_days", "send_hour", "send_minute",
    "repeat_months", "is_active",
)

#: Названия событий для человека. Совпадают с тем, что кадровик видит в
#: списке правил: два разных слова про одно событие означали бы, что
#: настраивал он одно, а сработало другое.
TRIGGER_TITLES = {
    "PROBATION_END": "Окончание стажировки",
    "FIRST_DAY": "Первый рабочий день",
    "DAYS_AFTER_HIRE": "Через N дней после выхода",
    "BIRTHDAY": "День рождения",
    "SCHEDULE": "Регулярно по расписанию",
}


class SurveyAutomationService(BaseService):
    """Правила автоматической рассылки опросов."""

    def list(self, actor: Actor) -> list[SurveyAutomation]:
        self.access.require(actor, "surveys.read")
        return list(
            SurveyAutomation.objects
            .filter(organization_id=actor.organization_id)
            .select_related("template")
            .order_by("-is_active", "title")
        )

    def get(self, actor: Actor, automation_id: uuid.UUID) -> SurveyAutomation:
        self.access.require(actor, "surveys.read")
        return self._require(actor, automation_id)

    def create(self, actor: Actor, payload: dict) -> SurveyAutomation:
        self.access.require(actor, "surveys.manage")
        require_whole_organization(self.access, actor)
        template = self._template(actor, payload.get("template_id"))
        scope = self._scope(actor, payload.get("scope"))
        kind = self._kind(payload)
        repeat = self._repeat(kind, payload.get("repeat_months"))
        hour, minute = self._clock(payload)

        with self.atomic():
            row = SurveyAutomation.objects.create(
                organization_id=actor.organization_id,
                title=(
                    self._title(payload.get("title"))
                    or f"{template.title}: {TRIGGER_TITLES[kind].lower()}"
                ),
                template=template,
                trigger_kind=kind,
                offset_days=self._offset(payload.get("offset_days")),
                send_hour=hour,
                send_minute=minute,
                repeat_months=repeat,
                scope=scope,
                is_active=bool(payload.get("is_active", True)),
                created_by_user_id=actor.user_id,
            )
            self.audit.record(
                actor,
                action="survey.automation.create",
                entity_type="survey_automations",
                entity_id=row.id,
                after=snapshot(row, AUDITED),
            )
        return row

    def update(
        self, actor: Actor, automation_id: uuid.UUID, payload: dict
    ) -> SurveyAutomation:
        """Поправить правило.

        Событие и шаблон меняются свободно: правило смотрит вперёд, и
        прошлые рассылки от его правки не меняются — у каждой свой
        снимок получателей и своя редакция вопросов.
        """
        self.access.require(actor, "surveys.manage")
        require_whole_organization(self.access, actor)
        row = self._require(actor, automation_id)
        kind = self._kind(payload) if "trigger_kind" in payload else row.trigger_kind
        hour, minute = self._clock(payload, default=(row.send_hour, row.send_minute))

        with self.atomic():
            before = snapshot(row, AUDITED)
            if "template_id" in payload:
                row.template = self._template(actor, payload["template_id"])
            if "title" in payload:
                row.title = self._title(payload["title"]) or row.title
            if "scope" in payload:
                row.scope = self._scope(actor, payload["scope"])
            if "offset_days" in payload:
                row.offset_days = self._offset(payload["offset_days"])
            row.trigger_kind = kind
            row.send_hour = hour
            row.send_minute = minute
            row.repeat_months = self._repeat(
                kind, payload.get("repeat_months", row.repeat_months)
            )
            row.save()
            self.audit.record(
                actor,
                action="survey.automation.update",
                entity_type="survey_automations",
                entity_id=row.id,
                before=before,
                after=snapshot(row, AUDITED),
            )
        return row

    def toggle(
        self, actor: Actor, automation_id: uuid.UUID, *, active: bool
    ) -> SurveyAutomation:
        """Включить или выключить правило.

        Выключенное не удаляется: у него есть история отправок, и
        удалять правило ради паузы значило бы потерять её вместе с ним.
        """
        self.access.require(actor, "surveys.manage")
        require_whole_organization(self.access, actor)
        row = self._require(actor, automation_id)
        with self.atomic():
            before = snapshot(row, AUDITED)
            row.is_active = active
            row.save(update_fields=["is_active", "updated_at"])
            self.audit.record(
                actor,
                action="survey.automation.toggle",
                entity_type="survey_automations",
                entity_id=row.id,
                before=before,
                after=snapshot(row, AUDITED),
            )
        return row

    def history(
        self, actor: Actor, automation_id: uuid.UUID,
        *, now: datetime | None = None, limit: int = 300,
    ) -> dict:
        """Что правило уже сделало: по людям и за последние 30 дней.

        Строки — получатели рассылок, которые правило завело само. У
        каждой свой исход и, если опрос не ушёл, своя причина: «без
        Telegram» и «уже не работает» — не ошибки правила, а ответ на
        вопрос «почему этот человек опроса не получил».
        """
        self.access.require(actor, "surveys.read")
        row = self._require(actor, automation_id)
        moment = now or timezone.now()
        since = moment - timedelta(days=30)

        mine = SurveyRecipient.objects.filter(campaign__automation=row)
        people = visible_employee_ids(self.access, actor)
        if people is not None:
            mine = mine.filter(employee_id__in=people)
        recipients = list(
            mine
            .select_related("employee", "campaign")
            .order_by("-campaign__scheduled_at", "employee__last_name")[:limit]
        )
        items = []
        for one in recipients:
            person = one.employee
            kind, _, day = (one.campaign.trigger_key or "").partition(":")
            items.append({
                "id": str(one.id),
                "campaign_id": str(one.campaign_id),
                "employee_id": str(one.employee_id),
                "full_name": " ".join(
                    part for part in (
                        person.last_name, person.first_name, person.middle_name,
                    ) if part
                ),
                "event_kind": kind or row.trigger_kind,
                "event_day": day or None,
                "fired_at": (
                    one.campaign.sent_at or one.campaign.scheduled_at
                ).isoformat() if (one.campaign.sent_at or one.campaign.scheduled_at) else None,
                "status": one.status,
                "skip_reason": one.skip_reason,
                "completed_at": one.completed_at.isoformat() if one.completed_at else None,
            })

        recent = mine.filter(campaign__sent_at__gte=since)
        stats = {
            "fired": SurveyCampaign.objects.filter(
                automation=row, sent_at__gte=since,
            ).count(),
            "sent": recent.filter(
                status__in=("SENT", "STARTED", "COMPLETED", "EXPIRED"),
            ).count(),
            "completed": recent.filter(status="COMPLETED").count(),
            "skipped": recent.filter(status="SKIPPED").count(),
        }
        return {"items": items, "stats": stats}

    def delete(self, actor: Actor, automation_id: uuid.UUID) -> None:
        self.access.require(actor, "surveys.manage")
        require_whole_organization(self.access, actor)
        row = self._require(actor, automation_id)
        if row.campaigns.exists():
            raise Conflict(
                "По правилу уже были рассылки — его можно выключить, "
                "но не удалить",
                details={"reason": "has_campaigns"},
            )
        with self.atomic():
            self.audit.record(
                actor,
                action="survey.automation.delete",
                entity_type="survey_automations",
                entity_id=row.id,
                before=snapshot(row, AUDITED),
            )
            row.delete()

    # --- разбор того, что прислали ---

    def _kind(self, payload: dict) -> str:
        kind = payload.get("trigger_kind")
        if kind not in TRIGGER_TITLES:
            raise ValidationFailed(
                "Неизвестное событие", details={"field": "trigger_kind"}
            )
        return kind

    def _repeat(self, kind: str, raw) -> int | None:
        """Период повтора — только у регулярной отправки.

        У события период не просто лишний, а противоречив: «раз в три
        месяца по окончании стажировки» не значит ничего.
        """
        value = _int(raw, "repeat_months") if raw else None
        if kind != "SCHEDULE":
            return None
        if not value:
            raise ValidationFailed(
                "У регулярной отправки нужен период",
                details={"field": "repeat_months"},
            )
        if value < 1 or value > 12:
            raise ValidationFailed(
                "Период повтора — от одного месяца до года",
                details={"field": "repeat_months"},
            )
        return value

    def _clock(self, payload: dict, default: tuple[int, int] = (10, 0)):
        hour = _int(payload.get("send_hour", default[0]) or 0, "send_hour")
        minute = _int(payload.get("send_minute", default[1]) or 0, "send_minute")
        if not (0 <= hour <= 23) or not (0 <= minute <= 59):
            raise ValidationFailed(
                "Такого времени не бывает", details={"field": "send_hour"}
            )
        return hour, minute

    def _offset(self, raw) -> int:
        """Сдвиг от события в днях.

        Назад сдвигать нечего: спрашивать об итогах стажировки за три
        дня до её конца — значит спрашивать о том, чего ещё не было.
        """
        value = _int(raw or 0, "offset_days")
        if value < 0:
            raise ValidationFailed(
                "Опрос отправляют в день события или после него",
                details={"field": "offset_days"},
            )
        if value > MAX_OFFSET_DAYS:
            raise ValidationFailed(
                f"Сдвиг — не больше {MAX_OFFSET_DAYS} дней",
                details={"field": "offset_days"},
            )
        return value

    def _title(self, raw) -> str | None:
        if raw is not None and not isinstance(raw, str):
            raise ValidationFailed("Название — строка", details={"field": "title"})
        return clean_text(raw, field="title", max_length=255)

    def _scope(self, actor: Actor, raw) -> dict | None:
        """Условие правила: офисы, отделы, должности своей организации.

        Строгая проверка здесь, а не «как-нибудь разберётся очередь»:
        правило исполняется внутри опроса очереди уведомлений для ВСЕХ
        организаций, и строка, которую нельзя разобрать, раньше роняла
        её целиком. Чужие идентификаторы тоже отклоняются: условие с
        офисом соседней организации — признак подстановки, а не ошибки.
        """
        if raw in (None, {}, ""):
            return None
        if not isinstance(raw, dict):
            raise ValidationFailed(
                "Условие правила — объект с office_ids, department_ids, position_ids",
                details={"field": "scope"},
            )
        unknown = [key for key in raw if key not in SCOPE_KEYS]
        if unknown:
            raise ValidationFailed(
                "Неизвестное условие правила",
                details={"field": "scope", "allowed": list(SCOPE_KEYS)},
            )
        from humotech.departments.models import Department
        from humotech.offices.models import Office
        from humotech.positions.models import Position

        models = {"office_ids": Office, "department_ids": Department,
                  "position_ids": Position}
        cleaned: dict[str, list[str]] = {}
        for key, values in raw.items():
            if values in (None, []):
                continue
            if not isinstance(values, list) or len(values) > SCOPE_MAX_IDS:
                raise ValidationFailed(
                    "Условие правила — список идентификаторов",
                    details={"field": f"scope.{key}", "max": SCOPE_MAX_IDS},
                )
            try:
                ids = sorted({str(uuid.UUID(str(one))) for one in values})
            except (ValueError, TypeError, AttributeError):
                raise ValidationFailed(
                    "Идентификатор в условии не распознан",
                    details={"field": f"scope.{key}"},
                ) from None
            found = models[key].objects.filter(
                organization_id=actor.organization_id, id__in=ids,
            ).count()
            if found != len(ids):
                raise ValidationFailed(
                    "В условии правила есть чужие или несуществующие записи",
                    details={"field": f"scope.{key}"},
                )
            cleaned[key] = ids
        return cleaned or None

    def _template(self, actor: Actor, template_id) -> SurveyTemplate:
        row = SurveyTemplate.objects.filter(
            id=template_id, organization_id=actor.organization_id
        ).first()
        if row is None:
            raise NotFound("Шаблон не найден")
        if row.status != "PUBLISHED":
            raise ValidationFailed(
                "Автоматически рассылают только опубликованный шаблон",
                details={"field": "template_id", "reason": "not_published"},
            )
        return row

    def _require(self, actor: Actor, automation_id: uuid.UUID) -> SurveyAutomation:
        row = (
            SurveyAutomation.objects
            .filter(id=automation_id, organization_id=actor.organization_id)
            .select_related("template")
            .first()
        )
        if row is None:
            raise NotFound("Автоматизация не найдена")
        return row


# --- срабатывание ---------------------------------------------------------


def run_due(*, now: datetime | None = None) -> int:
    """Отправить всё, чему сегодня подошёл срок.

    Возвращает число заведённых рассылок. Вызывается из той же очереди,
    что и рассылка по расписанию: отдельный планировщик ради одной
    проверки в сутки был бы лишней движущейся частью.
    """
    moment = now or timezone.now()
    made = 0
    for automation in (
        SurveyAutomation.objects
        .filter(is_active=True, template__status="PUBLISHED")
        .select_related("template")
    ):
        # Одно сломанное правило не останавливает остальные: вызов идёт
        # из очереди уведомлений всех организаций, и исключение здесь
        # раньше оставляло без рассылок всех сразу.
        try:
            # Точка отката: сбой базы на одном правиле не должен оставить
            # транзакцию вызывающего сломанной для следующих.
            with transaction.atomic():
                made += _run_one(automation, moment)
        except Exception:  # noqa: BLE001
            logger.exception("survey automation failed: automation=%s", automation.id)
    return made


def _run_one(automation: SurveyAutomation, moment: datetime) -> int:
    """Одно правило на один тик очереди. Возвращает число срабатываний."""
    tz = organization_zone(automation.organization_id)
    today = moment.astimezone(tz).date()
    # До назначенного часа не трогаем: правило обещает «в 10:00»,
    # и отправка в 00:05 была бы другим обещанием.
    send_at = datetime.combine(
        today, time(automation.send_hour, automation.send_minute), tzinfo=tz
    )
    if moment < send_at:
        return 0

    fired = 0
    for event_day, people in _due_for(automation, today, tz).items():
        if _send(automation, event_day, people, send_at):
            fired += 1

    if fired:
        # Отметка о срабатывании — для показа кадровику, и пишется
        # она только когда правило действительно сработало. Писать
        # её каждым тиком значило бы обновлять строку несколько раз
        # в секунду ради поля, на которое никто не смотрит.
        automation.last_run_at = moment
        automation.save(update_fields=["last_run_at", "updated_at"])
    return fired


def _int(raw, field: str) -> int:
    """Целое из запроса — или понятный отказ вместо 500."""
    if isinstance(raw, bool):
        raise ValidationFailed("Ожидалось число", details={"field": field})
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        raise ValidationFailed("Ожидалось число", details={"field": field}) from None
    if abs(value) > 1_000_000:
        raise ValidationFailed("Слишком большое число", details={"field": field})
    return value


def _due_for(
    automation: SurveyAutomation, today: date, tz
) -> dict[date, list[Employee]]:
    """Кого сегодня спрашивать, разложенных по дню события.

    Ключ — день повода, а не день отправки. У сдвинутого правила
    («через неделю после выхода») в один день сходятся люди с разными
    событиями, и свалить их в одну рассылку значило бы отчитаться за
    две разные истории одной строкой.
    """
    kind = automation.trigger_kind

    if kind == "SCHEDULE":
        # Регулярный опрос: повод один на всех — очередной день
        # расписания. Он вычисляется от дня создания правила, а не от
        # отметки о прошлом запуске: отметка живёт в строке, которую
        # переписывает каждый воркер, а день расписания выводится из
        # неизменного и потому совпадает у всех.
        #
        # День считается ДО того, как трогать сотрудников: в любой
        # другой день квартала спрашивать список незачем.
        day = _schedule_day(automation, today, tz)
        if day is None:
            return {}
        people = list(_scoped(automation))
        return {day: people} if people else {}

    # Событие отбирается в запросе, а не перебором в памяти.
    # Это не оптимизация ради оптимизации: проверка идёт на каждый
    # опрос очереди ботом — то есть каждые несколько секунд, и
    # вытягивать ради неё всю компанию значило бы держать базу занятой
    # работой, которая почти всегда ничего не находит.
    target = today - timedelta(days=automation.offset_days)
    people = _on_event(_scoped(automation), kind, target)

    due: dict[date, list[Employee]] = {}
    for person in people:
        event = _event_day(kind, person)
        if event is None:
            continue
        if kind == "BIRTHDAY":
            # Год рождения не совпадает никогда: повод — день
            # рождения в этом году, а не сама дата из карточки.
            event = _birthday_near(event, target)
            if event is None:
                continue
        due.setdefault(event, []).append(person)
    return due


def _on_event(people, kind: str, target: date):
    """Сузить выборку до тех, у кого событие именно в этот день."""
    if kind == "PROBATION_END":
        return people.filter(probation_to=target)
    if kind in ("FIRST_DAY", "DAYS_AFTER_HIRE"):
        return people.filter(hire_date=target)
    if kind == "BIRTHDAY":
        # 29 февраля в невисокосный год справляют 28-го: если цель —
        # 28 февраля и год невисокосный, возьмём оба числа. Иначе
        # таких людей забывали бы три года из четырёх.
        days = [target.day]
        if (target.month, target.day) == (2, 28) and not _leap(target.year):
            days.append(29)
        return people.filter(birth_date__month=target.month, birth_date__day__in=days)
    return people.none()


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _schedule_day(automation: SurveyAutomation, today: date, tz) -> date | None:
    """Последний наступивший день регулярной отправки.

    Считается от дня создания правила шагом в календарный месяц, а не
    «тридцать дней»: у правила «раз в квартал» тридцатидневный шаг за
    год уезжает на полторы недели, и опрос, назначенный на начало
    квартала, приходит посреди него.

    Возвращается именно день расписания, а не сегодняшний: он идёт в
    ключ повода, и повтор ловится уникальным ключом в базе, а не
    отметкой о прошлом запуске.
    """
    anchor = automation.created_at.astimezone(tz).date()
    if today < anchor:
        return None
    months = automation.repeat_months or 1
    passed = (today.year - anchor.year) * 12 + (today.month - anchor.month)
    steps = passed // months
    day = _shift_months(anchor, steps * months)
    if day > today:
        steps -= 1
        day = _shift_months(anchor, steps * months)
    return day


def next_run(automation: SurveyAutomation, *, now: datetime | None = None):
    """Когда правило сработает в следующий раз — для показа кадровику.

    У события ответа нет и быть не может: он зависит от того, кого
    наймут завтра. Пусто здесь честнее выдуманной даты.
    """
    if automation.trigger_kind != "SCHEDULE" or not automation.is_active:
        return None
    tz = organization_zone(automation.organization_id)
    today = (now or timezone.now()).astimezone(tz).date()
    anchor = automation.created_at.astimezone(tz).date()
    if today < anchor:
        return anchor
    day = _schedule_day(automation, today, tz)
    if day is None:
        return None
    return _shift_months(day, automation.repeat_months or 1)


def _shift_months(day: date, count: int) -> date:
    """Та же дата через `count` месяцев, с поправкой на короткий месяц.

    31 января плюс месяц — 28 или 29 февраля: правило, назначенное на
    последнее число, не должно раз в год перепрыгивать через месяц.
    """
    total = (day.year * 12 + day.month - 1) + count
    year, month = divmod(total, 12)
    month += 1
    last = _days_in_month(year, month)
    return date(year, month, min(day.day, last))


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


def _birthday_near(born: date, target: date) -> date | None:
    """День рождения в году цели — если он приходится ровно на неё.

    Рождённым 29 февраля в невисокосный год днём считается 28-е.
    Пропускать человека три года из четырёх — это не про календарь, а
    про то, что его забыли.
    """
    day = born.day
    if (born.month, born.day) == (2, 29):
        try:
            date(target.year, 2, 29)
        except ValueError:
            day = 28
    if (born.month, day) != (target.month, target.day):
        return None
    return date(target.year, born.month, day)


def _event_day(kind: str, person: Employee) -> date | None:
    """День самого события у конкретного человека."""
    if kind == "PROBATION_END":
        # Конец стажировки необязателен: человека берут стажёром и
        # тогда, когда срок ещё не назван. Без даты события нет.
        return person.probation_to
    if kind in ("FIRST_DAY", "DAYS_AFTER_HIRE"):
        return person.hire_date
    if kind == "BIRTHDAY":
        return person.birth_date
    return None


def _scoped(automation: SurveyAutomation):
    """Работающие сотрудники организации под условиями правила.

    Возвращается выборка, а не список: поверх неё ещё ляжет
    отбор по дню события, и вытаскивать до него всю компанию незачем.

    Стажёры входят наравне с остальными — иначе опрос об итогах
    стажировки не достался бы ровно тем, ради кого он написан.
    Уволенные и архивные не входят: опрос человеку, которого уже нет,
    это не опрос, а письмо в пустоту.
    """
    people = Employee.objects.filter(
        organization_id=automation.organization_id,
        archived_at__isnull=True,
        employment_status__in=("ACTIVE", "PROBATION"),
    )

    scope = automation.scope or {}
    offices = scope.get("office_ids") or []
    departments = scope.get("department_ids") or []
    positions = scope.get("position_ids") or []
    if offices or departments or positions:
        # Офис, отдел и должность живут в назначении, а не в карточке:
        # людей переводят, и правило обязано смотреть на то, где человек
        # числится сейчас, а не где числился при найме.
        places = EmployeeAssignment.objects.filter(is_primary=True)
        if offices:
            places = places.filter(office_id__in=offices)
        if departments:
            places = places.filter(department_id__in=departments)
        if positions:
            places = places.filter(position_id__in=positions)
        people = people.filter(
            id__in=places.values_list("employee_id", flat=True)
        )
    return people


def _send(
    automation: SurveyAutomation,
    event_day: date,
    people: list[Employee],
    send_at: datetime,
) -> bool:
    """Завести рассылку на один повод и разослать её.

    Повторный запуск того же дня ничего не дублирует, и держится это не
    на памяти воркера: у пары «правило + повод» уникальный ключ в базе,
    а у пары «рассылка + сотрудник» — свой. Второй воркер, перезапуск
    очереди и ручной повтор упираются в одно и то же ограничение.
    """
    key = f"{automation.trigger_kind}:{event_day.isoformat()}"
    title = f"{automation.template.title} · {_occasion(automation, event_day)}"

    try:
        with transaction.atomic():
            campaign, is_new = SurveyCampaign.objects.get_or_create(
                organization_id=automation.organization_id,
                automation=automation,
                trigger_key=key,
                defaults={
                    "template": automation.template,
                    "title": title,
                    "status": "SCHEDULED",
                    "audience_kind": "EMPLOYEES",
                    "audience_ids": [],
                    "scheduled_at": send_at,
                },
            )
            if campaign.status == "CANCELLED":
                # Кадровик отменил эту рассылку руками. Правило не
                # вправе воскресить её следующим же тиком.
                return False

            known = {str(one) for one in (campaign.audience_ids or [])}
            fresh = {str(one.id) for one in people} - known
            if not fresh and not is_new:
                return False
            campaign.audience_ids = sorted(known | fresh)
            campaign.save(update_fields=["audience_ids", "updated_at"])
            dispatch(campaign, now=send_at)
            return is_new
    except IntegrityError:
        # Второй воркер пришёл на тот же повод. Он здесь лишний.
        return False


def _occasion(automation: SurveyAutomation, event_day: date) -> str:
    """Человеческая подпись повода для заголовка рассылки."""
    day = event_day.strftime("%d.%m.%Y")
    if automation.trigger_kind == "SCHEDULE":
        return day
    return f"{TRIGGER_TITLES[automation.trigger_kind].lower()} {day}"
