"""Представление сотрудников в API.

Карточка собирается сервисом из нескольких таблиц; сериализатор только
раскладывает готовое. Ходить в базу отсюда нельзя — иначе контроль над
числом запросов уезжает из сервиса в слой представления.
"""

from __future__ import annotations

from rest_framework import serializers

from humotech.employees.models import Employee, EmployeeAssignment


class AssignmentSerializer(serializers.ModelSerializer):
    office_code = serializers.CharField(source="office.code", read_only=True)
    office_name = serializers.CharField(source="office.name", read_only=True)
    office_timezone = serializers.CharField(source="office.timezone",
                                            read_only=True)
    region_id = serializers.UUIDField(source="office.region_id", read_only=True)
    region_name = serializers.CharField(source="office.region.name", read_only=True)
    department_name = serializers.CharField(source="department.name",
                                            read_only=True, default=None)
    position_name = serializers.CharField(source="position.name",
                                          read_only=True, default=None)

    class Meta:
        model = EmployeeAssignment
        fields = (
            "id", "office_id", "office_code", "office_name", "office_timezone",
            "region_id", "region_name",
            "department_id", "department_name",
            "position_id", "position_name",
            "manager_employee_id", "employment_type", "work_mode",
            "is_primary", "valid_from", "valid_to",
        )
        read_only_fields = fields


class EmployeeListItemSerializer(serializers.ModelSerializer):
    """Строка списка.

    Текущее назначение прикладывает сервис одним запросом на всю страницу,
    поэтому здесь оно уже есть и дополнительных обращений не вызывает.
    """

    full_name = serializers.SerializerMethodField()
    current_assignment = AssignmentSerializer(read_only=True, default=None)
    current_schedule = serializers.SerializerMethodField()
    telegram_state = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = (
            "id", "organization_id", "employee_number",
            "first_name", "last_name", "middle_name", "full_name",
            "phone", "corporate_email", "employment_status",
            "hire_date", "termination_date", "telegram_connected",
            "created_at", "current_assignment", "current_schedule",
            "telegram_state",
        )
        read_only_fields = fields

    def get_full_name(self, employee: Employee) -> str:
        parts = [employee.last_name, employee.first_name, employee.middle_name]
        return " ".join(part for part in parts if part)

    def get_telegram_state(self, employee: Employee) -> str | None:
        """Состояние привязки или `null`, если её нет вовсе.

        Берётся из самих привязок: поле `telegram_connected` рядом —
        денормализованный флаг, который ни одна операция не обновляет.
        """
        return getattr(employee, "telegram_state", None)

    def get_current_schedule(self, employee: Employee) -> dict | None:
        """Действующий график или `null`.

        `null` означает «график не назначен», а не «работает как все»:
        подставить сюда общее расписание значило бы придумать сотруднику
        рабочее время, которого ему никто не назначал.
        """
        schedule = getattr(employee, "current_schedule", None)
        if schedule is None:
            return None
        return {
            "id": str(schedule.id),
            "name": schedule.name,
            "timezone": schedule.timezone,
            "weekly_minutes": schedule.weekly_minutes,
            "is_flexible": schedule.is_flexible,
        }


class TelegramBindingSerializer(serializers.Serializer):
    """Состояние привязки. Сам идентификатор наружу не отдаётся: для CRM
    важен факт привязки, а не номер аккаунта."""

    connected = serializers.BooleanField()
    status = serializers.CharField(allow_null=True)
    username = serializers.CharField(allow_null=True)
    connected_at = serializers.DateTimeField(allow_null=True)


class CurrentScheduleSerializer(serializers.Serializer):
    schedule_id = serializers.UUIDField()
    name = serializers.CharField()
    timezone = serializers.CharField()
    status = serializers.CharField()
    valid_from = serializers.DateField()
    valid_to = serializers.DateField(allow_null=True)


class EmployeeCardSerializer(serializers.Serializer):
    """Полная карточка. На вход принимает `EmployeeCard` из сервиса."""

    id = serializers.UUIDField(source="employee.id")
    organization_id = serializers.UUIDField(source="employee.organization_id")
    employee_number = serializers.CharField(source="employee.employee_number")
    first_name = serializers.CharField(source="employee.first_name")
    last_name = serializers.CharField(source="employee.last_name")
    middle_name = serializers.CharField(source="employee.middle_name",
                                        allow_null=True)
    full_name = serializers.CharField()
    phone = serializers.CharField(source="employee.phone", allow_null=True)
    corporate_email = serializers.CharField(source="employee.corporate_email",
                                            allow_null=True)
    personal_email = serializers.CharField(source="employee.personal_email",
                                           allow_null=True)
    birth_date = serializers.DateField(source="employee.birth_date",
                                       allow_null=True)
    hire_date = serializers.DateField(source="employee.hire_date")
    termination_date = serializers.DateField(source="employee.termination_date",
                                             allow_null=True)
    preferred_language = serializers.CharField(
        source="employee.preferred_language"
    )
    employment_status = serializers.CharField(source="employee.employment_status")
    archived_at = serializers.DateTimeField(source="employee.archived_at",
                                            allow_null=True)
    created_at = serializers.DateTimeField(source="employee.created_at")
    updated_at = serializers.DateTimeField(source="employee.updated_at")

    current_assignment = AssignmentSerializer(allow_null=True)
    current_schedule = serializers.SerializerMethodField()
    telegram = TelegramBindingSerializer()
    assignment_history = AssignmentSerializer(many=True)

    def get_current_schedule(self, card) -> dict | None:
        row = card.current_schedule
        if row is None:
            return None
        return CurrentScheduleSerializer(
            {
                "schedule_id": row.schedule_id,
                "name": row.schedule.name,
                "timezone": row.schedule.timezone,
                "status": row.schedule.status,
                "valid_from": row.valid_from,
                "valid_to": row.valid_to,
            }
        ).data


class EmployeeCreateSerializer(serializers.Serializer):
    employee_number = serializers.CharField(max_length=100)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    middle_name = serializers.CharField(max_length=100, required=False,
                                        allow_null=True)
    hire_date = serializers.DateField()
    office_id = serializers.UUIDField()
    employment_type = serializers.CharField(max_length=30, required=False,
                                            default="FULL_TIME")
    work_mode = serializers.CharField(max_length=30, required=False,
                                      default="ONSITE")
    # регион можно не передавать: он определяется офисом. Если передан —
    # обязан совпасть с регионом офиса, иначе это ошибка в данных вызывающего.
    region_id = serializers.UUIDField(required=False, allow_null=True)
    department_id = serializers.UUIDField(required=False, allow_null=True)
    position_id = serializers.UUIDField(required=False, allow_null=True)
    manager_employee_id = serializers.UUIDField(required=False, allow_null=True)
    phone = serializers.CharField(max_length=30, required=False, allow_null=True)
    corporate_email = serializers.CharField(max_length=255, required=False,
                                            allow_null=True)
    personal_email = serializers.CharField(max_length=255, required=False,
                                           allow_null=True)
    birth_date = serializers.DateField(required=False, allow_null=True)
    preferred_language = serializers.CharField(max_length=10, required=False,
                                               default="ru")
    employment_status = serializers.CharField(max_length=30, required=False,
                                              default="ACTIVE")


class EmployeeUpdateSerializer(serializers.Serializer):
    """Только собственные данные сотрудника.

    Офис, отдел, должность и график живут в назначениях и меняются
    отдельными действиями: у них есть период действия, а у поля карточки его
    нет — правкой карточки историю не построить.
    """

    first_name = serializers.CharField(max_length=100, required=False)
    last_name = serializers.CharField(max_length=100, required=False)
    middle_name = serializers.CharField(max_length=100, required=False,
                                        allow_null=True)
    phone = serializers.CharField(max_length=30, required=False, allow_null=True)
    corporate_email = serializers.CharField(max_length=255, required=False,
                                            allow_null=True)
    personal_email = serializers.CharField(max_length=255, required=False,
                                           allow_null=True)
    birth_date = serializers.DateField(required=False, allow_null=True)
    preferred_language = serializers.CharField(max_length=10, required=False)
    employee_number = serializers.CharField(max_length=100, required=False)


class AssignmentChangeSerializer(serializers.Serializer):
    """Перевод: новый офис, отдел, должность или руководитель с указанной даты."""

    effective_from = serializers.DateField()
    office_id = serializers.UUIDField(required=False)
    department_id = serializers.UUIDField(required=False, allow_null=True)
    position_id = serializers.UUIDField(required=False, allow_null=True)
    manager_employee_id = serializers.UUIDField(required=False, allow_null=True)
    employment_type = serializers.CharField(max_length=30, required=False)
    work_mode = serializers.CharField(max_length=30, required=False)


class TerminateSerializer(serializers.Serializer):
    termination_date = serializers.DateField()
    reason = serializers.CharField(required=False, allow_null=True)
