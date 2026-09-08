"""Настройки организации через CRM.

Хранилище — `organization_settings`: key/value с JSONB. Отсюда главная
опасность и главное решение этого модуля.

**Белый список, а не свободный key/value.** `settings.manage` без него
означал бы «пиши любой JSON под любым ключом»: опечатка в имени ключа
создавала бы настройку, которую никто не читает, а кривое значение
уезжало бы в JSONB и всплывало через месяц при расчёте. Поэтому здесь
перечислены и ключи, и форма каждого значения, а всё остальное
отвергается с ошибкой, а не записывается.

**Пояс по умолчанию настраивается, но живёт в своей колонке.**
`organizations.default_timezone` — не строка в JSONB, и копии рядом
не заводится: два источника правды хуже, чем один в неудобном месте.
Меняется он через тот же endpoint, под своим ключом.

**Что сюда не попало и почему.** Допуск опоздания принадлежит графику
(`work_schedules.late_grace_minutes`): у разных смен он разный, и
организационный дубль сделал бы неоднозначным вопрос «какой допуск у
этого человека». Радиус геозоны принадлежит офису
(`offices.geofence_radius_m`), а сроки жизни QR-кодов — развёртыванию:
их читает подписывающий код на старте, до всякой организации. Эти три
показываются в ответе указателями на владельца, а не значениями:
настройка, у которой два места, рано или поздно разъезжается.

**Прошлое не переписывается.** Правила отсутствий читаются в момент
действия и решают, что можно сделать СЕЙЧАС: подать заявку, продлить,
отменить, приложить справку. Ни одна из них не пересчитывает уже
принятое решение — подтверждённое отсутствие остаётся подтверждённым,
даже если правило потом изменилось.
"""

from __future__ import annotations

from dataclasses import fields

from humotech.absences.policy import (
    AbsencePolicy,
    SETTING_KEY as ABSENCE_POLICY_KEY,
    policy_for,
    save_policy,
)
from humotech.core.errors import NotFound, ValidationFailed
from humotech.core.rbac import Actor
from humotech.core.service import BaseService, refuse_stale
from humotech.core.timeframes import organization_zone
from humotech.core.validation import clean_text, validate_timezone
from humotech.files.storage import EXTENSIONS
from humotech.organizations.models import Organization, OrganizationSetting

#: Форматы, которые хранилище умеет проверить по содержимому. Список
#: не настройка, а факт: `store()` сверяет сигнатуру начала файла, и
#: тип, которого здесь нет, будет отвергнут при загрузке.
STORABLE_DOCUMENT_TYPES = frozenset(EXTENSIONS)

#: Ключи, которые CRM вправе менять. Всё остальное — ошибка, а не
#: молчаливая запись в JSONB.
ORGANIZATION_DEFAULTS_KEY = "organization.defaults"

KNOWN_KEYS = (ABSENCE_POLICY_KEY, ORGANIZATION_DEFAULTS_KEY)

#: Поля группы «Организация». Название живёт в своей колонке, остальное —
#: в JSONB под тем же ключом: колонку заводят под то, что ищут и по чему
#: соединяют, а описание и пояс отображения ни там, ни там не нужны.
DEFAULTS_FIELDS = ("name", "description", "crm_timezone", "default_timezone")

DEFAULTS_HELP = {
    "name": "Название рабочего пространства. Видно в шапке и в письмах",
    "description": "Одна-две строки о том, чем занята организация",
    "crm_timezone": (
        "Пояс, в котором CRM ПОКАЗЫВАЕТ время там, где у строки нет "
        "своего офиса: журнал действий, список уведомлений, карточки "
        "учётных записей. Пусто — берётся пояс первого офиса, а если "
        "офисов нет, то пояс организации"
    ),
    "default_timezone": (
        "Запасной пояс организации. Действует там, где у офиса не задан "
        "свой. Отметки и графики считаются по поясу ОФИСА и от этой "
        "настройки не зависят"
    ),
}

_BOOL_FIELDS = {
    "require_hr_approval",
    "document_required",
    "document_can_be_added_later",
    "employee_may_cancel_pending",
    "cancelling_approved_requires_hr",
    "extensions_allowed",
    "allow_negative_leave_balance",
}
_DAY_FIELDS = {
    "document_required_from_day",
    "backdating_days_allowed",
    "vacation_min_days_ahead",
}

#: Что означает каждая настройка — для интерфейса и OpenAPI. Описание
#: живёт рядом со значением намеренно: настройка, смысл которой знает
#: только автор кода, настраивается наугад.
ABSENCE_POLICY_HELP = {
    "require_hr_approval": "Заявка ждёт решения HR. Выключено — «оформил, значит ушёл»",
    "document_required": "Справка обязательна",
    "document_required_from_day": (
        "С какой длительности справка обязательна. "
        "0 или 1 — всегда; 4 — только если отсутствие длиннее трёх дней"
    ),
    "document_can_be_added_later": "Справку можно донести после выхода",
    "employee_may_cancel_pending": "Сотрудник сам снимает нерассмотренную заявку",
    "cancelling_approved_requires_hr": (
        "Отменить подтверждённое отсутствие может только отдел кадров"
    ),
    "extensions_allowed": "Продление больничного разрешено",
    "max_document_bytes": "Предельный размер справки в байтах",
    "allowed_document_types": "Разрешённые MIME-типы справок",
    "allow_negative_leave_balance": "Уход в минус по остатку отпуска",
    "backdating_days_allowed": (
        "На сколько дней назад оформляется отсутствие. 0 — без ограничения: "
        "больничный по своей природе оформляется задним числом"
    ),
    "vacation_min_days_ahead": (
        "За сколько дней подаётся заявка на отпуск. 0 — требования нет"
    ),
}


class OrganizationSettingsService(BaseService):
    """Чтение по `settings.manage`, изменение по нему же.

    Отдельного разрешения на чтение нет намеренно: настройки видит тот,
    кто их меняет. Значения политики, нужные сотруднику, он и так
    получает через `/me/absences/options` — там своя проверка.
    """

    def all(self, actor: Actor) -> dict:
        """Все известные настройки со значениями, умолчаниями и описанием."""
        self.access.require(actor, "settings.manage")
        return {
            "items": [
                self._defaults_section(actor),
                self._absence_section(actor),
            ],
            "elsewhere": self._elsewhere(),
            "last_change": self._last_change(actor),
        }

    def get(self, actor: Actor, key: str) -> dict:
        self.access.require(actor, "settings.manage")
        if key not in KNOWN_KEYS:
            raise NotFound(
                "Неизвестный ключ настроек",
                details={"key": key, "known": list(KNOWN_KEYS)},
            )
        return self._section(actor, key)

    def update(
        self,
        actor: Actor,
        key: str,
        values: dict,
        *,
        expected_updated_at=None,
        check_expected: bool = False,
    ) -> dict:
        """Записать настройку. Неизвестный ключ или поле — ошибка.

        `expected_updated_at` — редакция, которую видел правящий. Второй
        администратор, открывший ту же страницу минутой раньше, иначе
        молча отменил бы работу первого: он отправил бы свою копию
        значений целиком.
        """
        self.access.require(actor, "settings.manage")
        if key not in KNOWN_KEYS:
            raise NotFound(
                "Неизвестный ключ настроек",
                details={"key": key, "known": list(KNOWN_KEYS)},
            )
        if not isinstance(values, dict):
            raise ValidationFailed(
                "Значение настройки должно быть объектом",
                details={"key": key},
            )
        refuse_stale(
            self._section_updated_at(actor, key),
            expected_updated_at,
            enabled=check_expected,
        )
        if key == ORGANIZATION_DEFAULTS_KEY:
            return self._update_defaults(actor, values)

        self._validate_absence_policy(values)
        before = policy_for(actor.organization_id).as_dict()

        with self.atomic():
            policy = save_policy(actor.organization_id, {**before, **values})
            self.audit.record(
                actor,
                action="organization_setting.update",
                entity_type="organization_settings",
                entity_id=actor.organization_id,
                before=before,
                after=policy.as_dict(),
            )
        return self._absence_section(actor)

    def _update_defaults(self, actor: Actor, values: dict) -> dict:
        """Группа «Организация» целиком, одной транзакцией.

        Название лежит в колонке `organizations.name`, описание и пояс
        отображения — в JSONB под тем же ключом. Разными запросами их
        сохранять нельзя: половина применённой группы — это состояние,
        которого человек не выбирал.
        """
        unknown = sorted(set(values) - set(DEFAULTS_FIELDS))
        if unknown:
            raise ValidationFailed(
                "Неизвестные настройки",
                details={"unknown": unknown, "known": list(DEFAULTS_FIELDS)},
            )
        organization = Organization.objects.filter(
            id=actor.organization_id
        ).first()
        if organization is None:
            raise NotFound("Организация не найдена")

        stored = self._stored_defaults(actor.organization_id)
        before = {
            "name": organization.name,
            "description": stored.get("description"),
            "crm_timezone": stored.get("crm_timezone"),
            "default_timezone": organization.default_timezone,
        }
        columns: list[str] = []
        after_stored = dict(stored)

        if "name" in values:
            organization.name = clean_text(
                values["name"], field="name", required=True, max_length=255
            )
            columns.append("name")
        if "default_timezone" in values:
            organization.default_timezone = validate_timezone(
                values["default_timezone"],
                field="default_timezone",
                required=True,
            )
            columns.append("default_timezone")
        if "description" in values:
            after_stored["description"] = clean_text(
                values["description"], field="description", max_length=500
            )
        if "crm_timezone" in values:
            # Пусто — законный выбор: он означает «как раньше, по офису».
            after_stored["crm_timezone"] = validate_timezone(
                values["crm_timezone"], field="crm_timezone", required=False
            )

        with self.atomic():
            if columns:
                organization.save(update_fields=[*columns, "updated_at"])
            if after_stored != stored:
                OrganizationSetting.objects.update_or_create(
                    organization_id=actor.organization_id,
                    key=ORGANIZATION_DEFAULTS_KEY,
                    defaults={"value": after_stored},
                )
            after = {
                "name": organization.name,
                "description": after_stored.get("description"),
                "crm_timezone": after_stored.get("crm_timezone"),
                "default_timezone": organization.default_timezone,
            }
            # Одна запись на всю группу: три строки журнала про одно
            # нажатие кнопки читаются как три разных решения.
            self.audit.record(
                actor,
                action="organization.defaults",
                entity_type="organizations",
                entity_id=organization.id,
                before=before,
                after=after,
            )
        return self._defaults_section(actor)

    @staticmethod
    def _stored_defaults(organization_id) -> dict:
        row = OrganizationSetting.objects.filter(
            organization_id=organization_id, key=ORGANIZATION_DEFAULTS_KEY
        ).first()
        value = getattr(row, "value", None)
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _setting_updated_at(organization_id, key: str):
        row = OrganizationSetting.objects.filter(
            organization_id=organization_id, key=key
        ).only("updated_at").first()
        return getattr(row, "updated_at", None)

    def _section_updated_at(self, actor: Actor, key: str):
        """Редакция группы — самое позднее изменение её источников.

        У группы «Организация» источников два: колонка и строка JSONB.
        Брать только один значило бы не заметить правку соседа, если он
        поменял другую половину.
        """
        stamp = self._setting_updated_at(actor.organization_id, key)
        if key != ORGANIZATION_DEFAULTS_KEY:
            return stamp
        organization = (
            Organization.objects.filter(id=actor.organization_id)
            .only("updated_at")
            .first()
        )
        column = getattr(organization, "updated_at", None)
        if stamp is None:
            return column
        if column is None:
            return stamp
        return max(stamp, column)

    # ---------------------------------------------------------------- частное

    def _section(self, actor: Actor, key: str) -> dict:
        if key == ORGANIZATION_DEFAULTS_KEY:
            return self._defaults_section(actor)
        return self._absence_section(actor)

    def _absence_section(self, actor: Actor) -> dict:
        policy = policy_for(actor.organization_id)
        defaults = AbsencePolicy().as_dict()
        return {
            "key": ABSENCE_POLICY_KEY,
            "title": "Заявки и документы",
            "description": (
                "Действуют на решения, принимаемые после изменения. "
                "Уже подтверждённые отсутствия не пересматриваются, а "
                "загруженные справки не перепроверяются."
            ),
            "values": policy.as_dict(),
            "defaults": defaults,
            "help": dict(ABSENCE_POLICY_HELP),
            "effective": {
                # Хранилище проверяет содержимое по сигнатуре и умеет
                # ровно эти три формата. Предлагать четвёртый значило бы
                # обещать приём файла, который сервер потом отвергнет.
                "storable_document_types": sorted(STORABLE_DOCUMENT_TYPES),
            },
            "updated_at": self._setting_updated_at(
                actor.organization_id, ABSENCE_POLICY_KEY
            ),
        }

    def _defaults_section(self, actor: Actor) -> dict:
        organization = Organization.objects.filter(
            id=actor.organization_id
        ).first()
        stored = self._stored_defaults(actor.organization_id)
        return {
            "key": ORGANIZATION_DEFAULTS_KEY,
            "title": "Организация",
            "description": (
                "Название, описание и пояс, в котором CRM показывает "
                "время. Отметки и графики считаются по поясу офиса и "
                "отсюда не меняются."
            ),
            "values": {
                "name": getattr(organization, "name", None),
                "description": stored.get("description"),
                "crm_timezone": stored.get("crm_timezone"),
                "default_timezone": getattr(
                    organization, "default_timezone", None
                ),
            },
            "defaults": {
                "name": None,
                "description": None,
                "crm_timezone": None,
                "default_timezone": "UTC",
            },
            "help": dict(DEFAULTS_HELP),
            "effective": {
                # Что действует НА САМОМ ДЕЛЕ. Пустой `crm_timezone`
                # означает не «UTC», а «как у первого офиса», и без
                # этого поля страница не могла бы этого показать.
                "timezone": str(organization_zone(actor.organization_id)),
                "code": getattr(organization, "code", None),
            },
            "updated_at": self._section_updated_at(
                actor, ORGANIZATION_DEFAULTS_KEY
            ),
        }

    def _last_change(self, actor: Actor) -> dict | None:
        """Кто и когда менял настройки в последний раз.

        Читается из журнала действий и только тем, кому журнал открыт:
        «кто это сделал» — сведение из аудита, и показывать его в обход
        `audit.read` значило бы выдать это право страницей настроек.
        Записи нет — возвращается `null`, а не выдуманные дата и автор.
        """
        if not self.access.has(actor, "audit.read"):
            return None
        from humotech.audit.models import AuditLog

        row = (
            AuditLog.objects.filter(
                organization_id=actor.organization_id,
                action__in=("organization.defaults", "organization_setting.update"),
            )
            .select_related("actor_user")
            .order_by("-occurred_at", "-id")
            .first()
        )
        if row is None:
            return None
        return {
            "at": row.occurred_at,
            "action": row.action,
            "actor_email": getattr(row.actor_user, "email", None),
        }

    def integrations(self, actor: Actor) -> dict:
        """Состояние подключений. Только чтение, только по `settings.manage`.

        Три вещи, которые здесь принципиально не делаются.

        **Ни одного исходящего запроса.** Открытая страница настроек не
        обязана дёргать Telegram и тем более платного провайдера AI.
        Поэтому «работает» здесь не проверяется, а выводится из следов,
        которые система оставила сама: успешная попытка отправки — это
        доказательство, а переменная окружения — только намерение.

        **Ни одного секрета наружу.** Ни токена, ни его длины, ни первых
        символов: по префиксу токен не восстановить, но и пользы от него
        нет никакой, а в журнале браузера он останется.

        **Разница между «настроено» и «проверено».** Заполненная
        переменная означает, что администратор что-то ввёл, — и ничего
        не говорит о том, отвечает ли сервис. Пока подтверждения нет,
        состояние честно называется «не удалось проверить».
        """
        self.access.require(actor, "settings.manage")
        from django.conf import settings as django_settings

        from humotech.ai_assistant.config import ai_settings
        from humotech.notifications.models import Notification, NotificationAttempt

        telegram = django_settings.TELEGRAM
        bot_configured = bool((telegram.get("BOT_TOKEN") or "").strip())
        mini_app_configured = bool((telegram.get("MINI_APP_URL") or "").strip())

        # Доказательство работы — состоявшаяся отправка, а не настройка.
        last_sent = (
            NotificationAttempt.objects.filter(
                notification__organization_id=actor.organization_id,
                notification__channel="TELEGRAM",
                outcome="SENT",
            )
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        pending = Notification.objects.filter(
            organization_id=actor.organization_id,
            channel="TELEGRAM",
            status__in=("PENDING", "RUNNING"),
        ).count()

        return {
            "items": [
                {
                    "key": "telegram_bot",
                    "title": "Telegram-бот",
                    "configured": bot_configured,
                    "state": self._state(bot_configured, last_sent is not None),
                    # Пояснение обязано следовать за состоянием, а не жить
                    # своей жизнью: «выключено» рядом с «доставка
                    # подтверждена» — это два взаимоисключающих ответа на
                    # один вопрос, и читатель вправе не поверить обоим.
                    "note": (
                        "Токен бота не задан развёртыванием: отправлять "
                        "нечем, что бы ни лежало в очереди"
                        if not bot_configured
                        else "Доставка подтверждена успешной отправкой"
                        if last_sent is not None
                        else "Настроено, но успешных отправок ещё не было"
                    ),
                    # Факт из прошлого показывается только тогда, когда он
                    # не спорит с настоящим.
                    "confirmed_at": last_sent if bot_configured else None,
                    "queued": pending,
                    "link": "/notifications",
                },
                {
                    "key": "mini_app",
                    "title": "Mini App",
                    "configured": mini_app_configured,
                    "state": self._state(mini_app_configured, False),
                    "note": (
                        "Адрес задан развёртыванием. Доступность извне "
                        "отсюда не проверяется"
                    ),
                    "confirmed_at": None,
                    "queued": None,
                    "link": None,
                },
                {
                    "key": "ai_assistant",
                    "title": "AI-ассистент",
                    "configured": bool(ai_settings.has_credentials),
                    # Выключенный ассистент — это не сбой и не «не удалось
                    # проверить»: это принятое решение, и называть его
                    # надо своим словом.
                    "state": (
                        "off"
                        if not ai_settings.ai_assistant_enabled
                        else self._state(ai_settings.has_credentials, False)
                    ),
                    "note": (
                        "Выключен рубильником. Запросы к провайдеру не "
                        "уходят вовсе"
                        if not ai_settings.ai_assistant_enabled
                        else "Включён. Проверка доступности здесь не выполняется"
                    ),
                    "confirmed_at": None,
                    "queued": None,
                    "link": "/knowledge",
                },
            ],
        }

    @staticmethod
    def _state(configured: bool, confirmed: bool) -> str:
        if not configured:
            return "off"
        return "working" if confirmed else "unknown"

    @staticmethod
    def _elsewhere() -> list[dict]:
        """Настройки, которые кадровик ищет здесь, а живут они не здесь.

        Отдаются указателями на владельца, а не значениями: копия рядом
        рано или поздно разъезжается с оригиналом, и тогда непонятно,
        какая из двух работает.
        """
        return [
            {
                "name": "late_grace_minutes",
                "owner": "work_schedules.late_grace_minutes",
                "hint": "Допуск опоздания задаётся графиком: у смен он разный.",
            },
            {
                "name": "geofence_radius_m",
                "owner": "offices.geofence_radius_m",
                "hint": "Радиус геозоны принадлежит офису.",
            },
            {
                "name": "qr_token_ttl_seconds",
                "owner": "deployment",
                "hint": "Сроки жизни QR-кодов задаются развёртыванием.",
            },
        ]

    @staticmethod
    def _validate_absence_policy(values: dict) -> None:
        """Строгая проверка ВХОДА.

        `policy.py` разбирает мягко и намеренно: значение в JSONB могло
        появиться откуда угодно, и кривой флаг не должен ронять просмотр
        своих больничных. Но то, что приходит из CRM, — другое дело:
        молча проглоченная опечатка означала бы настройку, которая
        выглядит записанной и не работает.
        """
        known = {field.name for field in fields(AbsencePolicy)}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValidationFailed(
                "Неизвестные настройки",
                details={"unknown": unknown, "known": sorted(known)},
            )

        for key, value in values.items():
            if key in _BOOL_FIELDS:
                if not isinstance(value, bool):
                    raise ValidationFailed(
                        f"Настройка «{key}» — да или нет",
                        details={"field": key, "expected": "boolean"},
                    )
            elif key in _DAY_FIELDS:
                # `bool` — подкласс `int`: без явной проверки `true`
                # молча стало бы «одним днём».
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValidationFailed(
                        f"Настройка «{key}» — целое число дней",
                        details={"field": key, "expected": "integer"},
                    )
                if not 0 <= value <= 366:
                    raise ValidationFailed(
                        f"Настройка «{key}» — от 0 до 366 дней",
                        details={"field": key, "value": value},
                    )
            elif key == "max_document_bytes":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValidationFailed(
                        "Предельный размер справки — целое число байт",
                        details={"field": key},
                    )
                if not 0 < value <= 100 * 1024 * 1024:
                    raise ValidationFailed(
                        "Предельный размер справки — от 1 байта до 100 МБ",
                        details={"field": key, "value": value},
                    )
            elif key == "allowed_document_types":
                if not isinstance(value, (list, tuple)) or not value:
                    raise ValidationFailed(
                        "Разрешённые типы справок — непустой список строк",
                        details={"field": key},
                    )
                if any(not isinstance(item, str) or not item for item in value):
                    raise ValidationFailed(
                        "Разрешённые типы справок — непустой список строк",
                        details={"field": key},
                    )
                # Формат, который хранилище не умеет проверить, принимать
                # нельзя: настройка выглядела бы применённой, а загрузка
                # отвергала бы файл со ссылкой на «неверное содержимое».
                unsupported = sorted(set(value) - STORABLE_DOCUMENT_TYPES)
                if unsupported:
                    raise ValidationFailed(
                        "Такой формат хранилище не проверяет и не примет",
                        details={
                            "field": key,
                            "unsupported": unsupported,
                            "supported": sorted(STORABLE_DOCUMENT_TYPES),
                        },
                    )


__all__ = [
    "ABSENCE_POLICY_HELP",
    "STORABLE_DOCUMENT_TYPES",
    "KNOWN_KEYS",
    "ORGANIZATION_DEFAULTS_KEY",
    "OrganizationSettingsService",
]
