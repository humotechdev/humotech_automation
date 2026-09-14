"""Представление сотрудников в API.

Карточка собирается сервисом из нескольких таблиц; сериализатор только
раскладывает готовое. Ходить в базу отсюда нельзя — иначе контроль над
числом запросов уезжает из сервиса в слой представления.
"""

from __future__ import annotations

from rest_framework import serializers

from humotech.core.enums import (
    EMPLOYEE_DOCUMENT_KINDS,
    GENDERS,
    MARITAL_STATUSES,
)
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
    telegram_username = serializers.SerializerMethodField()
    # Признак наличия снимка, а не сам снимок: список из двухсот строк с
    # картинками внутри весил бы мегабайты. Изображение отдаётся
    # отдельным адресом, где право спрашивается при каждом открытии.
    photo = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = (
            "id", "organization_id", "employee_number",
            "first_name", "last_name", "middle_name", "full_name",
            "phone", "corporate_email", "employment_status",
            "birth_date", "hire_date", "termination_date",
            "telegram_connected", "created_at", "current_assignment",
            "current_schedule", "telegram_state", "telegram_username",
            "photo",
        )
        read_only_fields = fields

    def get_full_name(self, employee: Employee) -> str:
        parts = [employee.last_name, employee.first_name, employee.middle_name]
        return " ".join(part for part in parts if part)

    def get_photo(self, employee: Employee) -> bool:
        return employee.photo_id is not None

    def get_telegram_username(self, employee: Employee) -> str | None:
        """Имя в Telegram или `null`.

        Берётся у привязки, а не у сотрудника: у сотрудника такого поля
        нет вовсе, и подставить сюда что-то другое значило бы показать
        имя, которого не существует.
        """
        return getattr(employee, "telegram_username", None)

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
    gender = serializers.CharField(source="employee.gender", allow_null=True)
    marital_status = serializers.CharField(source="employee.marital_status",
                                           allow_null=True)
    # Само изображение здесь не отдаётся: карточка ходит по сети часто, а
    # фотография весит мегабайты. Отдаётся отдельный адрес, который
    # спрашивает право на каждое открытие.
    photo = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()
    archived_at = serializers.DateTimeField(source="employee.archived_at",
                                            allow_null=True)
    created_at = serializers.DateTimeField(source="employee.created_at")
    updated_at = serializers.DateTimeField(source="employee.updated_at")

    current_assignment = AssignmentSerializer(allow_null=True)
    current_schedule = serializers.SerializerMethodField()
    telegram = TelegramBindingSerializer()
    assignment_history = AssignmentSerializer(many=True)

    def get_photo(self, card) -> dict | None:
        record = card.employee.photo
        if record is None:
            return None
        return AttachedFileSerializer(record).data

    def get_documents(self, card) -> list[dict]:
        return EmployeeDocumentSerializer(card.documents, many=True).data

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


class EmployeeDocumentInputSerializer(serializers.Serializer):
    """Одна приложенная бумага: вид и уже загруженный файл."""

    kind = serializers.ChoiceField(choices=EMPLOYEE_DOCUMENT_KINDS)
    file_id = serializers.UUIDField()
    title = serializers.CharField(max_length=255, required=False,
                                  allow_null=True, allow_blank=True)


class EmployeeOnboardSerializer(serializers.Serializer):
    """Одна форма приёма: всё, что HR заполняет на странице «Новый сотрудник».

    Табельного номера здесь нет намеренно — его выдаёт система, и поле для
    него означало бы, что кто-то должен помнить, какой номер свободен.
    """

    # Ключ придумывает клиент один раз на форму. Повтор с тем же ключом
    # отдаёт того же сотрудника, а не заводит второго.
    idempotency_key = serializers.CharField(max_length=100)

    last_name = serializers.CharField(max_length=100)
    first_name = serializers.CharField(max_length=100)
    middle_name = serializers.CharField(max_length=100, required=False,
                                        allow_null=True, allow_blank=True)
    birth_date = serializers.DateField(required=False, allow_null=True)
    pinfl = serializers.CharField(max_length=32)
    phone = serializers.CharField(max_length=30)
    corporate_email = serializers.CharField(max_length=255, required=False,
                                            allow_null=True, allow_blank=True)
    telegram_username = serializers.CharField(max_length=255, required=False,
                                              allow_null=True, allow_blank=True)
    # Числовой идентификатор Telegram. Приходит только когда он уже известен
    # достоверно; по `@username` его не восстанавливают — имя можно сменить.
    telegram_user_id = serializers.IntegerField(required=False, allow_null=True)

    hire_date = serializers.DateField()
    region_id = serializers.UUIDField(required=False, allow_null=True)
    office_id = serializers.UUIDField()
    department_id = serializers.UUIDField()
    position_id = serializers.UUIDField()
    manager_employee_id = serializers.UUIDField(required=False, allow_null=True)
    employment_type = serializers.CharField(max_length=30, required=False,
                                            default="FULL_TIME")
    work_mode = serializers.CharField(max_length=30, required=False,
                                      default="ONSITE")
    # Испытательный срок отражается статусом, а не отдельным полем: в базе
    # есть PROBATION, и второе место для той же мысли разошлось бы с ним.
    employment_status = serializers.CharField(max_length=30, required=False,
                                              default="ACTIVE")
    schedule_id = serializers.UUIDField()
    preferred_language = serializers.CharField(max_length=10, required=False,
                                               default="ru")

    gender = serializers.ChoiceField(choices=GENDERS, required=False,
                                     allow_null=True, allow_blank=True)
    marital_status = serializers.ChoiceField(choices=MARITAL_STATUSES,
                                             required=False, allow_null=True,
                                             allow_blank=True)
    # Файлы приходят идентификаторами: их тело загружено отдельным запросом
    # до нажатия кнопки. Это позволяет показать фотографию и размер файла
    # ещё в форме, а сам приём оставить одной операцией.
    photo_file_id = serializers.UUIDField(required=False, allow_null=True)
    documents = EmployeeDocumentInputSerializer(many=True, required=False)

    def validate_documents(self, rows):
        """Один файл на вид бумаги, кроме «прочего».

        Это правило базы (`uq_employee_documents_kind`). Проверка здесь
        нужна, чтобы человек увидел понятный отказ, а не поломанный приём
        на IntegrityError внутри транзакции.
        """
        seen = set()
        for row in rows:
            kind = row["kind"]
            if kind == "OTHER":
                continue
            if kind in seen:
                raise serializers.ValidationError(
                    "Документ такого вида можно приложить только один"
                )
            seen.add(kind)
        return rows


class AttachedFileSerializer(serializers.Serializer):
    """Загруженный файл — то, что о нём знает форма до сохранения."""

    id = serializers.UUIDField()
    name = serializers.CharField(source="original_filename")
    mime_type = serializers.CharField()
    size_bytes = serializers.IntegerField()


class EmployeeDocumentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.CharField()
    title = serializers.CharField()
    status = serializers.CharField()
    file_id = serializers.UUIDField(allow_null=True)
    file = AttachedFileSerializer(allow_null=True)


class TelegramOutcomeSerializer(serializers.Serializer):
    """Чем закончилась подготовка доступа к боту."""

    state = serializers.CharField()
    link = serializers.CharField(allow_null=True)
    message = serializers.CharField()


class OnboardedSerializer(serializers.Serializer):
    """Ответ приёма: карточка плюс отчёт по каждому шагу.

    Шаги перечислены отдельно, а не свёрнуты в «успех»: подготовка Telegram
    может не пройти, не отменяя приёма, и различать это нужно на экране.
    """

    employee = EmployeeCardSerializer(source="card")
    created = serializers.BooleanField()
    schedule_assigned = serializers.BooleanField()
    telegram = TelegramOutcomeSerializer()
    documents = EmployeeDocumentSerializer(many=True)
