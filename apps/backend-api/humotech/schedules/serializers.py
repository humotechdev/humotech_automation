"""Представление рабочих графиков в API."""

from __future__ import annotations

from rest_framework import serializers

from humotech.schedules.models import (
    EmployeeScheduleAssignment,
    ScheduleBreak,
    ScheduleDay,
    WorkSchedule,
)
from humotech.schedules.services import BreakSpec, DaySpec


class ScheduleBreakSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleBreak
        fields = ("id", "name", "start_time", "end_time", "is_paid")
        read_only_fields = fields


class ScheduleDaySerializer(serializers.ModelSerializer):
    breaks = ScheduleBreakSerializer(many=True, read_only=True)

    class Meta:
        model = ScheduleDay
        fields = (
            "id", "weekday", "is_working_day", "start_time", "end_time",
            "crosses_midnight", "breaks",
        )
        read_only_fields = fields


class WorkScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkSchedule
        fields = (
            "id", "organization_id", "name", "timezone", "weekly_minutes",
            "late_grace_minutes", "early_leave_grace_minutes", "is_flexible",
            "status", "created_at", "updated_at",
            "region_id", "region_name", "can_manage",
        )
        read_only_fields = fields

    region_name = serializers.SerializerMethodField(
        help_text="Регион графика; null — график организации")
    can_manage = serializers.SerializerMethodField(
        help_text="Может ли текущий пользователь менять этот график")

    def get_region_name(self, schedule) -> str | None:
        region = getattr(schedule, "region", None) if schedule.region_id else None
        return region.name if region is not None else None

    def get_can_manage(self, schedule) -> bool | None:
        return getattr(schedule, "can_manage", None)


class WorkScheduleDetailSerializer(WorkScheduleSerializer):
    """Карточка с днями и перерывами.

    Дни отсортированы по номеру, а не по порядку вставки: график читают
    как неделю, а не как журнал изменений.
    """

    days = serializers.SerializerMethodField()

    class Meta(WorkScheduleSerializer.Meta):
        fields = WorkScheduleSerializer.Meta.fields + ("days",)
        read_only_fields = fields

    def get_days(self, schedule: WorkSchedule) -> list[dict]:
        days = sorted(schedule.days.all(), key=lambda day: day.weekday)
        return ScheduleDaySerializer(days, many=True).data


class BreakInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()
    is_paid = serializers.BooleanField(required=False, default=False)


class DayInputSerializer(serializers.Serializer):
    """День недели по ISO-8601: 1 — понедельник, 7 — воскресенье.

    Корректность интервала проверяет сервис: отличить ночную смену от
    перепутанных часов можно только по флагу `crosses_midnight`, и правило
    должно быть одним для всех вызывающих, а не только для HTTP.
    """

    weekday = serializers.IntegerField(min_value=1, max_value=7)
    is_working_day = serializers.BooleanField()
    start_time = serializers.TimeField(required=False, allow_null=True)
    end_time = serializers.TimeField(required=False, allow_null=True)
    crosses_midnight = serializers.BooleanField(required=False, default=False)
    breaks = BreakInputSerializer(many=True, required=False, default=list)


def to_day_specs(days: list[dict] | None) -> list[DaySpec] | None:
    """Разобранный запрос -> DTO сервиса."""
    if days is None:
        return None
    return [
        DaySpec(
            weekday=day["weekday"],
            is_working_day=day["is_working_day"],
            start_time=day.get("start_time"),
            end_time=day.get("end_time"),
            crosses_midnight=day.get("crosses_midnight", False),
            breaks=tuple(
                BreakSpec(
                    name=item["name"],
                    start_time=item["start_time"],
                    end_time=item["end_time"],
                    is_paid=item.get("is_paid", False),
                )
                for item in day.get("breaks", [])
            ),
        )
        for day in days
    ]


class WorkScheduleCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    timezone = serializers.CharField(max_length=100)
    weekly_minutes = serializers.IntegerField()
    late_grace_minutes = serializers.IntegerField(required=False, default=0)
    early_leave_grace_minutes = serializers.IntegerField(required=False, default=0)
    is_flexible = serializers.BooleanField(required=False, default=False)
    days = DayInputSerializer(many=True, required=False, default=list)
    #: Регион графика. Не передан — график организации (нужно право на
    #: всю организацию) или единственного региона регионального HR.
    region_id = serializers.UUIDField(required=False, allow_null=True)


class WorkScheduleUpdateSerializer(serializers.Serializer):
    """Переданный список дней заменяет расписание ЦЕЛИКОМ.

    Частичная правка отдельных дней ввела бы неочевидную семантику слияния:
    непонятно, что означает отсутствие дня в списке.
    """

    name = serializers.CharField(max_length=255, required=False)
    timezone = serializers.CharField(max_length=100, required=False)
    weekly_minutes = serializers.IntegerField(required=False)
    late_grace_minutes = serializers.IntegerField(required=False)
    early_leave_grace_minutes = serializers.IntegerField(required=False)
    is_flexible = serializers.BooleanField(required=False)
    days = DayInputSerializer(many=True, required=False)


class ScheduleAssignmentSerializer(serializers.ModelSerializer):
    schedule_name = serializers.CharField(source="schedule.name", read_only=True)

    class Meta:
        model = EmployeeScheduleAssignment
        fields = (
            "id", "employee_id", "schedule_id", "schedule_name",
            "valid_from", "valid_to", "assigned_by_user_id", "created_at",
        )
        read_only_fields = fields


class ScheduleAssignSerializer(serializers.Serializer):
    employee_id = serializers.UUIDField()
    valid_from = serializers.DateField()


class DepartmentAssignSerializer(serializers.Serializer):
    """Назначение графика отделу — снимок состава на дату."""

    department_id = serializers.UUIDField()
    valid_from = serializers.DateField()
