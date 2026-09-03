from django.contrib import admin

from humotech.core.admin import OperationalAdmin, ReadOnlyAdmin
from humotech.employees.models import (
    Employee,
    EmployeeAssignment,
    EmployeeOfficeAccess,
)


@admin.register(Employee)
class EmployeeAdmin(OperationalAdmin):
    list_display = ("employee_number", "last_name", "first_name",
                    "employment_status", "hire_date", "termination_date",
                    "telegram_connected")
    list_filter = ("employment_status", "organization")
    search_fields = ("employee_number", "last_name", "first_name",
                     "corporate_email", "phone")
    ordering = ("organization", "employee_number")
    date_hierarchy = "hire_date"


@admin.register(EmployeeAssignment)
class EmployeeAssignmentAdmin(ReadOnlyAdmin):
    """Только чтение: назначения — периоды, и править их «на месте» нельзя.

    Перевод создаёт новый период и закрывает прежний; правка одной строки
    из интерфейса разрушила бы историю, по которой считается прошлое
    рабочее время.
    """

    list_display = ("employee", "office", "position", "is_primary",
                    "valid_from", "valid_to")
    list_filter = ("is_primary", "employment_type", "work_mode", "organization")
    search_fields = ("employee__employee_number", "employee__last_name")
    list_select_related = ("employee", "office", "position")


@admin.register(EmployeeOfficeAccess)
class EmployeeOfficeAccessAdmin(OperationalAdmin):
    list_display = ("employee", "office", "access_type", "valid_from", "valid_to")
    list_filter = ("access_type", "organization")
    list_select_related = ("employee", "office")
