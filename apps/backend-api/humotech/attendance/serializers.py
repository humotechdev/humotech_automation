"""Как посещаемость выглядит в ответах CRM.

Время отдаётся в ISO 8601 с поясом — так, как его хранит база (UTC).
Пересчёт в местное время делает клиент: сервер называет пояс офиса рядом
с данными, и этого достаточно. Складывать пояс в строку заранее значило бы
терять момент времени и получать «17:00» без ответа на вопрос «где».

Длительности — в секундах. Ни «8 ч 30 мин», ни «8.5»: округление и
склонение зависят от места показа, а сервер, округлив однажды, теряет
разницу навсегда.
"""

from __future__ import annotations

from rest_framework import serializers


class EmployeeBriefSerializer(serializers.Serializer):
    """Кто это — ровно столько, сколько нужно строке списка."""

    id = serializers.UUIDField()
    full_name = serializers.SerializerMethodField()
    employee_number = serializers.CharField(allow_null=True)

    def get_full_name(self, employee) -> str:
        parts = [employee.last_name, employee.first_name, employee.middle_name]
        return " ".join(part for part in parts if part)


class AttendanceEventSerializer(serializers.Serializer):
    """Сырое событие. Только чтение — строка не меняется никогда."""

    id = serializers.UUIDField()
    employee = EmployeeBriefSerializer()
    office_id = serializers.UUIDField(allow_null=True)
    office_name = serializers.CharField(source="office.name", allow_null=True)
    qr_point_id = serializers.UUIDField(allow_null=True)
    qr_point_name = serializers.CharField(source="qr_point.name", allow_null=True)

    event_type = serializers.CharField()
    source = serializers.CharField()
    verification_status = serializers.CharField()
    occurred_at = serializers.DateTimeField()
    received_at = serializers.DateTimeField()
    rejection_reason = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()

    # Ни `qr_nonce_hash`, ни координат, ни IP: журнал смотрит кадровик,
    # а это технические поля разбора инцидентов. Их место в аудите.


class AttendanceSessionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    employee = EmployeeBriefSerializer()
    office_id = serializers.UUIDField(allow_null=True)
    office_name = serializers.CharField(source="office.name", allow_null=True)

    started_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField(allow_null=True)
    duration_seconds = serializers.IntegerField(allow_null=True)
    status = serializers.CharField()
    is_open = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_is_open(self, session) -> bool:
        return session.ended_at is None


class PresenceRowSerializer(serializers.Serializer):
    employee_id = serializers.UUIDField()
    full_name = serializers.CharField()
    employee_number = serializers.CharField(allow_null=True)
    office_id = serializers.UUIDField(allow_null=True)
    office_name = serializers.CharField(allow_null=True)
    department_name = serializers.CharField(allow_null=True)
    position_name = serializers.CharField(allow_null=True)

    state = serializers.CharField()
    first_entry_at = serializers.DateTimeField(allow_null=True)
    last_exit_at = serializers.DateTimeField(allow_null=True)
    seconds = serializers.IntegerField()
    open_session_id = serializers.UUIDField(allow_null=True)

    # null означает «сравнивать не с чем», а не «не опоздал».
    late_minutes = serializers.IntegerField(allow_null=True)
    scheduled_start = serializers.TimeField(allow_null=True)

    absence_code = serializers.CharField(allow_null=True)
    absence_name = serializers.CharField(allow_null=True)
    conflicting_marks = serializers.BooleanField()


class CorrectionRequestSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    employee = EmployeeBriefSerializer()
    attendance_session_id = serializers.UUIDField(allow_null=True)

    requested_entry_at = serializers.DateTimeField(allow_null=True)
    requested_exit_at = serializers.DateTimeField(allow_null=True)
    reason = serializers.CharField()
    status = serializers.CharField()
    submitted_at = serializers.DateTimeField()
    reviewed_at = serializers.DateTimeField(allow_null=True)
    review_comment = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


# ------------------------------------------------------------------ на вход


class CorrectionDecisionSerializer(serializers.Serializer):
    """Комментарий необязателен при одобрении и обязателен при отказе.

    Человеку, которому отказали, нужно знать причину: без неё он подаст
    ту же заявку заново, и так по кругу.
    """

    comment = serializers.CharField(required=False, allow_blank=True)


class ManualEventSerializer(serializers.Serializer):
    employee_id = serializers.UUIDField()
    office_id = serializers.UUIDField()
    event_type = serializers.ChoiceField(choices=["ENTRY", "EXIT"])
    occurred_at = serializers.DateTimeField()
    # Причина обязательна на уровне схемы, а не только сервиса: по этим
    # отметкам считают рабочее время, и запись без основания в системе
    # существовать не должна.
    reason = serializers.CharField(min_length=3, max_length=1000)
