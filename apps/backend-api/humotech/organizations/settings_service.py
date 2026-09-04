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
from humotech.core.service import BaseService
from humotech.core.validation import validate_timezone
from humotech.organizations.models import Organization

#: Ключи, которые CRM вправе менять. Всё остальное — ошибка, а не
#: молчаливая запись в JSONB.
ORGANIZATION_DEFAULTS_KEY = "organization.defaults"

KNOWN_KEYS = (ABSENCE_POLICY_KEY, ORGANIZATION_DEFAULTS_KEY)

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
                self._absence_section(actor),
                self._defaults_section(actor),
            ],
            "elsewhere": self._elsewhere(),
        }

    def get(self, actor: Actor, key: str) -> dict:
        self.access.require(actor, "settings.manage")
        if key not in KNOWN_KEYS:
            raise NotFound(
                "Неизвестный ключ настроек",
                details={"key": key, "known": list(KNOWN_KEYS)},
            )
        return self._section(actor, key)

    def update(self, actor: Actor, key: str, values: dict) -> dict:
        """Записать настройку. Неизвестный ключ или поле — ошибка."""
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
        """Пояс организации. Пишется в свою колонку, а не в JSONB."""
        unknown = sorted(set(values) - {"default_timezone"})
        if unknown:
            raise ValidationFailed(
                "Неизвестные настройки",
                details={"unknown": unknown, "known": ["default_timezone"]},
            )
        organization = Organization.objects.filter(
            id=actor.organization_id
        ).first()
        if organization is None:
            raise NotFound("Организация не найдена")

        before = {"default_timezone": organization.default_timezone}
        if "default_timezone" in values:
            organization.default_timezone = validate_timezone(
                values["default_timezone"],
                field="default_timezone",
                required=True,
            )

        with self.atomic():
            organization.save(update_fields=["default_timezone", "updated_at"])
            self.audit.record(
                actor,
                action="organization.defaults",
                entity_type="organizations",
                entity_id=organization.id,
                before=before,
                after={"default_timezone": organization.default_timezone},
            )
        return self._defaults_section(actor)

    # ---------------------------------------------------------------- частное

    def _section(self, actor: Actor, key: str) -> dict:
        if key == ORGANIZATION_DEFAULTS_KEY:
            return self._defaults_section(actor)
        return self._absence_section(actor)

    @staticmethod
    def _absence_section(actor: Actor) -> dict:
        policy = policy_for(actor.organization_id)
        defaults = AbsencePolicy().as_dict()
        return {
            "key": ABSENCE_POLICY_KEY,
            "title": "Правила отсутствий",
            "description": (
                "Действуют на решения, принимаемые после изменения. "
                "Уже подтверждённые отсутствия не пересматриваются."
            ),
            "values": policy.as_dict(),
            "defaults": defaults,
            "help": dict(ABSENCE_POLICY_HELP),
        }

    @staticmethod
    def _defaults_section(actor: Actor) -> dict:
        organization = (
            Organization.objects.filter(id=actor.organization_id)
            .only("default_timezone")
            .first()
        )
        return {
            "key": ORGANIZATION_DEFAULTS_KEY,
            "title": "Умолчания организации",
            "description": (
                "Пояс, по которому считается день там, где у офиса "
                "не задан свой."
            ),
            "values": {
                "default_timezone": getattr(
                    organization, "default_timezone", None
                ),
            },
            "defaults": {"default_timezone": "UTC"},
            "help": {
                "default_timezone": (
                    "Название пояса IANA, например Asia/Dushanbe. "
                    "У офиса может быть свой — он и главнее."
                ),
            },
        }

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


__all__ = [
    "ABSENCE_POLICY_HELP",
    "KNOWN_KEYS",
    "ORGANIZATION_DEFAULTS_KEY",
    "OrganizationSettingsService",
]
