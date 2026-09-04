"""Правила отсутствий — настройка организации, а не константы в коде.

Организации живут по разным правилам, и различия здесь не косметические:
где-то справку требуют с первого дня, где-то её приносят после выхода;
где-то сотрудник сам отменяет заявку, где-то это может только отдел кадров.
Зашить одно из этих правил в код значит объявить, что второй организации
не будет.

Хранятся в `organization_settings` — это уже готовый key/value с JSONB.
Заводить ради девяти флагов новую таблицу и миграцию было бы хуже: ключ
один, читается он редко, а схему пришлось бы менять на каждое новое правило.

**Умолчания безопасные.** Организация, которая ничего не настраивала, живёт
по самым осторожным правилам: согласование HR обязательно, отменить
подтверждённое отсутствие сотрудник сам не может. Неизвестное правило
должно закрывать, а не открывать.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from humotech.organizations.models import OrganizationSetting

SETTING_KEY = "absences.policy"

# Что разрешено прикладывать. Три формата, все просматриваются штатными
# средствами и все проверяются по расширению, MIME и содержимому.
DEFAULT_DOCUMENT_TYPES = ("application/pdf", "image/jpeg", "image/png")
DEFAULT_MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class AbsencePolicy:
    """Как организация обращается с больничными и отпусками."""

    # Заявка ждёт решения HR. Выключение означает «оформил — значит ушёл».
    require_hr_approval: bool = True
    # Справка обязательна сразу при подаче.
    document_required: bool = False
    # Справку можно донести позже — например, после выхода с больничного.
    document_can_be_added_later: bool = True
    # Сотрудник сам снимает свою НЕрассмотренную заявку.
    employee_may_cancel_pending: bool = True
    # Отменить уже подтверждённое отсутствие может только отдел кадров.
    cancelling_approved_requires_hr: bool = True
    # Продление больничного разрешено.
    extensions_allowed: bool = True
    max_document_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES
    allowed_document_types: tuple[str, ...] = DEFAULT_DOCUMENT_TYPES
    # Уход в минус по остатку отпуска. По умолчанию нельзя: минус —
    # это решение, которое принимает человек, а не следствие опечатки.
    allow_negative_leave_balance: bool = False

    def as_dict(self) -> dict:
        return {
            "require_hr_approval": self.require_hr_approval,
            "document_required": self.document_required,
            "document_can_be_added_later": self.document_can_be_added_later,
            "employee_may_cancel_pending": self.employee_may_cancel_pending,
            "cancelling_approved_requires_hr": self.cancelling_approved_requires_hr,
            "extensions_allowed": self.extensions_allowed,
            "max_document_bytes": self.max_document_bytes,
            "allowed_document_types": list(self.allowed_document_types),
            "allow_negative_leave_balance": self.allow_negative_leave_balance,
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


def policy_for(organization_id) -> AbsencePolicy:
    """Правила организации. Ничего не настроено — безопасные умолчания."""
    row = OrganizationSetting.objects.filter(
        organization_id=organization_id, key=SETTING_KEY
    ).first()
    if row is None or not isinstance(row.value, dict):
        return AbsencePolicy()
    return _from_dict(row.value)


def save_policy(organization_id, values: dict) -> AbsencePolicy:
    """Записать правила. Возвращает то, что получилось после разбора."""
    policy = _from_dict(values)
    OrganizationSetting.objects.update_or_create(
        organization_id=organization_id,
        key=SETTING_KEY,
        defaults={"value": policy.as_dict()},
    )
    return policy


def _from_dict(values: dict) -> AbsencePolicy:
    """Разбор с приведением типов и откатом на умолчание.

    Значение в JSONB могло быть записано чем угодно — руками, миграцией,
    будущим интерфейсом. Кривой флаг здесь означает «берём умолчание»,
    а не исключение при попытке посмотреть свои больничные.
    """
    known = {field.name for field in fields(AbsencePolicy)}
    defaults = AbsencePolicy()
    parsed: dict = {}

    for key, value in values.items():
        if key not in known:
            continue
        if key in _BOOL_FIELDS:
            if isinstance(value, bool):
                parsed[key] = value
        elif key == "max_document_bytes":
            if isinstance(value, int) and 0 < value <= 100 * 1024 * 1024:
                parsed[key] = value
        elif key == "allowed_document_types":
            if isinstance(value, (list, tuple)):
                cleaned = tuple(
                    str(item) for item in value if isinstance(item, str) and item
                )
                if cleaned:
                    parsed[key] = cleaned

    return AbsencePolicy(
        **{
            field.name: parsed.get(field.name, getattr(defaults, field.name))
            for field in fields(AbsencePolicy)
        }
    )


__all__ = ["AbsencePolicy", "SETTING_KEY", "policy_for", "save_policy"]
