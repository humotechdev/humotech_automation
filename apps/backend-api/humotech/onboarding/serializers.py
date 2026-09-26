"""Формы запросов и ответов ознакомления.

Ответы сотруднику собирает `presentation.py` словарями — считающий код
про JSON знать не должен. Здесь то же самое словами, которые понимает
генератор схемы: по схеме собирается клиент, и без описания у него
вместо типов оказывается `unknown`.

Входные сериализаторы — настоящие: они разбирают тело запроса. Ни
`employee_id`, ни `organization_id` среди их полей нет и быть не может.
Сотрудника определяет привязка, организацию — сессия кадровика.
"""

from __future__ import annotations

from rest_framework import serializers


# --- то, что читает сотрудник ------------------------------------------------


class SectionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    position = serializers.IntegerField()
    total = serializers.IntegerField()
    title = serializers.CharField()
    body = serializers.CharField()
    button_label = serializers.CharField(
        help_text="Подпись кнопки подтверждения — часть содержимого карточки",
    )
    version = serializers.IntegerField()
    acknowledged_at = serializers.DateTimeField(allow_null=True)
    acknowledged_version = serializers.IntegerField(allow_null=True)


class PolicySerializer(serializers.Serializer):
    document_id = serializers.UUIDField()
    code = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    index = serializers.IntegerField()
    total = serializers.IntegerField()
    version_id = serializers.UUIDField(allow_null=True)
    version = serializers.CharField(allow_null=True)
    summary = serializers.CharField(allow_null=True)
    agree_label = serializers.CharField(allow_null=True)
    has_body = serializers.BooleanField()
    has_file = serializers.BooleanField()
    published_at = serializers.DateTimeField(allow_null=True)
    decision = serializers.CharField(allow_null=True)
    decided_at = serializers.DateTimeField(allow_null=True)


class OnboardingStateSerializer(serializers.Serializer):
    """Где человек сейчас и что показать дальше."""

    status = serializers.CharField()
    stage = serializers.CharField(
        help_text="SECTIONS, POLICIES, BLOCKED или DONE",
    )
    completed = serializers.BooleanField()
    info_completed = serializers.BooleanField()
    has_declined = serializers.BooleanField()
    sections_done = serializers.IntegerField()
    sections_total = serializers.IntegerField()
    policies_done = serializers.IntegerField()
    policies_total = serializers.IntegerField()
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    section = SectionSerializer(allow_null=True)
    policy = PolicySerializer(allow_null=True)
    policies = PolicySerializer(many=True)
    sections = SectionSerializer(many=True)


class PolicyTextSerializer(serializers.Serializer):
    """Полный текст редакции — то, что открывает «Открыть полный документ»."""

    version_id = serializers.UUIDField()
    title = serializers.CharField()
    version = serializers.CharField()
    body = serializers.CharField(allow_null=True)
    has_file = serializers.BooleanField()
    published_at = serializers.DateTimeField(allow_null=True)


class BeginSerializer(serializers.Serializer):
    """`message_id` — где бот показал карточку.

    Нужен ровно затем, чтобы не засорять чат: показывая следующую,
    бот снимает кнопки с предыдущей.
    """

    message_id = serializers.IntegerField(required=False, allow_null=True)


class AcknowledgeSerializer(serializers.Serializer):
    section_id = serializers.UUIDField()
    message_id = serializers.IntegerField(required=False, allow_null=True)


class PolicyDecisionSerializer(serializers.Serializer):
    version_id = serializers.UUIDField()
    decision = serializers.ChoiceField(choices=["ACCEPTED", "DECLINED"])


# --- то, чем управляет кадровик ----------------------------------------------


class ProgressRowSerializer(serializers.Serializer):
    employee_id = serializers.UUIDField()
    full_name = serializers.CharField()
    employee_number = serializers.CharField(allow_null=True)
    office_name = serializers.CharField(allow_null=True)
    department_name = serializers.CharField(allow_null=True)
    position_name = serializers.CharField(allow_null=True)
    telegram_state = serializers.CharField()
    status = serializers.CharField()
    stage = serializers.CharField()
    completed = serializers.BooleanField()
    sections_done = serializers.IntegerField()
    sections_total = serializers.IntegerField()
    policies_done = serializers.IntegerField()
    policies_total = serializers.IntegerField()
    invited_at = serializers.DateTimeField(allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    last_reminder_at = serializers.DateTimeField(allow_null=True)
    invitation_status = serializers.CharField(allow_null=True)
    invitation_expires_at = serializers.DateTimeField(allow_null=True)
    enrolled_at = serializers.DateTimeField()
    due_date = serializers.DateField(allow_null=True, help_text="Пусто — срок не назначен")
    overdue = serializers.BooleanField()
    reasons = serializers.ListField(
        child=serializers.ChoiceField(choices=["overdue", "declined", "renewal", "silent"]),
        help_text="Почему требует внимания; пусто — не требует",
    )
    group = serializers.ChoiceField(choices=["done", "attention", "waiting", "not_started", "in_progress"])
    materials = serializers.ListField(child=serializers.DictField())


class RemindManySerializer(serializers.Serializer):
    employee_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=500)


class DueSerializer(serializers.Serializer):
    due_date = serializers.DateField(allow_null=True)


class CategoryWriteSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    owner_employee_id = serializers.UUIDField(required=False, allow_null=True)
    position = serializers.IntegerField(required=False, min_value=1)


class CategoryPatchSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    owner_employee_id = serializers.UUIDField(required=False, allow_null=True)
    position = serializers.IntegerField(required=False, min_value=1)


class TimelineEventSerializer(serializers.Serializer):
    kind = serializers.CharField()
    at = serializers.DateTimeField()
    title = serializers.CharField()
    detail = serializers.CharField(allow_null=True)


class InvitationResultSerializer(serializers.Serializer):
    """Ответ на выдачу ссылки. Единственное место, где видна сама ссылка.

    В базе лежит только хеш токена, поэтому показать её второй раз
    невозможно даже суперпользователю: потерянная ссылка отзывается и
    выдаётся заново.
    """

    link = serializers.CharField(allow_null=True)
    expires_at = serializers.DateTimeField(allow_null=True)
    invitation_id = serializers.UUIDField(allow_null=True)
    status = serializers.CharField(allow_null=True)
    linked = serializers.BooleanField(
        help_text="Telegram уже привязан: ссылка не нужна и не выдавалась",
    )


class SectionWriteSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    body = serializers.CharField()
    button_label = serializers.CharField(max_length=100, required=False)
    position = serializers.IntegerField(required=False, min_value=1)


class SectionPatchSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    body = serializers.CharField(required=False)
    button_label = serializers.CharField(max_length=100, required=False)
    position = serializers.IntegerField(required=False, min_value=1)


class DocumentWriteSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True,
                                        allow_null=True)
    is_mandatory = serializers.BooleanField(required=False, default=True)
    position = serializers.IntegerField(required=False, min_value=1)
    category_id = serializers.UUIDField(required=False, allow_null=True)


class DocumentPatchSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True,
                                        allow_null=True)
    is_mandatory = serializers.BooleanField(required=False)
    position = serializers.IntegerField(required=False, min_value=1)
    category_id = serializers.UUIDField(required=False, allow_null=True)


class VersionWriteSerializer(serializers.Serializer):
    version = serializers.CharField(max_length=20)
    summary = serializers.CharField()
    body = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    agree_label = serializers.CharField(max_length=100, required=False)


class VersionPatchSerializer(serializers.Serializer):
    version = serializers.CharField(max_length=20, required=False)
    summary = serializers.CharField(required=False)
    body = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    agree_label = serializers.CharField(max_length=100, required=False)


class VersionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    version = serializers.CharField()
    status = serializers.CharField()
    summary = serializers.CharField()
    body = serializers.CharField(allow_null=True)
    agree_label = serializers.CharField()
    has_file = serializers.BooleanField()
    file_name = serializers.CharField(allow_null=True)
    published_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class DocumentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    is_mandatory = serializers.BooleanField()
    position = serializers.IntegerField()
    archived_at = serializers.DateTimeField(allow_null=True)
    current_version = VersionSerializer(allow_null=True)
    versions = VersionSerializer(many=True)
    category = serializers.DictField(allow_null=True)
    assigned = serializers.IntegerField(help_text="Участникам программы; у черновика — 0")
    confirmed = serializers.IntegerField(help_text="Согласились с действующей редакцией")
    declined = serializers.IntegerField()
    renewal_pending = serializers.IntegerField(help_text="Соглашались с прежней, с новой — ещё нет")
    nearest_due = serializers.DateField(allow_null=True)
    created_by = serializers.CharField(allow_null=True)
    changed_at = serializers.DateTimeField()
    changed_by = serializers.CharField(allow_null=True)


class PendingEmployeeSerializer(serializers.Serializer):
    employee_id = serializers.UUIDField()
    full_name = serializers.CharField()
    employee_number = serializers.CharField(allow_null=True)
    declined_at = serializers.DateTimeField(allow_null=True)


class CrmSectionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    position = serializers.IntegerField()
    title = serializers.CharField()
    body = serializers.CharField()
    button_label = serializers.CharField()
    version = serializers.IntegerField()
    updated_at = serializers.DateTimeField()


__all__ = [
    "AcknowledgeSerializer",
    "BeginSerializer",
    "CrmSectionSerializer",
    "PolicyDecisionSerializer",
    "DocumentPatchSerializer",
    "DocumentSerializer",
    "DocumentWriteSerializer",
    "InvitationResultSerializer",
    "OnboardingStateSerializer",
    "PendingEmployeeSerializer",
    "PolicySerializer",
    "PolicyTextSerializer",
    "ProgressRowSerializer",
    "SectionPatchSerializer",
    "SectionSerializer",
    "SectionWriteSerializer",
    "TimelineEventSerializer",
    "VersionPatchSerializer",
    "VersionSerializer",
    "VersionWriteSerializer",
]
