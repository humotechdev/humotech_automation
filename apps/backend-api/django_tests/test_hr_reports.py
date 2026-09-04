"""Выгрузки: область видимости, происхождение файла и потоковый CSV.

Главная проверка — что выгрузка не обходит область видимости. Файл уходит
из системы и живёт дальше своей жизнью в почте и мессенджерах; если через
экспорт можно достать то, чего не видно на экране, все ограничения выше
становятся украшением.

Вторая — что файл объясняет сам себя. Через месяц никто не вспомнит, за
какие даты выгружен `report.csv`, а решения по нему принимать будут.
"""

from __future__ import annotations

import io
from datetime import date, time

import pytest

from django_tests.test_hr_analytics import FIRST, LAST, five_day_schedule, worked

pytestmark = pytest.mark.django_db

API = "/api/v1"


def body_of(response) -> str:
    """Тело ответа, потокового или обычного."""
    if hasattr(response, "streaming_content"):
        return b"".join(response.streaming_content).decode("utf-8")
    return response.content.decode("utf-8")


@pytest.fixture()
def exporter(api_client, make_user, organization):
    user = make_user(
        organization,
        permissions=("reports.export", "employees.read", "attendance.read",
                     "analytics.read", "offices.read"),
        raw_password="Пароль-Выгрузки-2026",
    )
    api_client.force_authenticate(user=user)
    api_client.humotech_user = user
    return api_client


# --- права и область ---------------------------------------------------------


class TestScope:
    def test_export_requires_its_own_permission(
        self, api_client, make_user, organization
    ):
        """Право видеть данные не даёт права выгружать их файлом.

        Это разные действия: экран смотрят внутри системы, а файл уносят
        наружу.
        """
        viewer = make_user(
            organization, permissions=("employees.read", "attendance.read")
        )
        api_client.force_authenticate(user=viewer)

        response = api_client.get(f"{API}/reports/employees/export")
        assert response.status_code == 403

    def test_export_does_not_bypass_office_scope(
        self, api_client, make_user, organization, employee, office, other_office
    ):
        stranger = make_user(
            organization,
            permissions=("reports.export", "employees.read"),
            office=other_office,
        )
        api_client.force_authenticate(user=stranger)

        text = body_of(api_client.get(f"{API}/reports/employees/export"))

        # Сотрудник чужого офиса в файл не попал.
        assert employee.employee_number not in text

    def test_export_sees_what_the_screen_sees(
        self, exporter, employee, office
    ):
        text = body_of(exporter.get(f"{API}/reports/employees/export"))
        assert employee.employee_number in text

    def test_unknown_report_is_refused(self, exporter):
        assert exporter.get(f"{API}/reports/выдумка/export").status_code == 400

    def test_unknown_format_is_refused(self, exporter):
        # Параметр называется `fmt`: `format` занят самим DRF под выбор
        # рендерера, и до нашего кода такой запрос не доходит.
        response = exporter.get(
            f"{API}/reports/employees/export", {"fmt": "pdf"}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"


# --- происхождение файла -----------------------------------------------------


class TestProvenance:
    def test_file_explains_itself(
        self, exporter, organization, employee, office
    ):
        five_day_schedule(organization, employee)
        worked(organization, employee, office, FIRST)

        text = body_of(
            exporter.get(
                f"{API}/reports/attendance/export",
                {"date": FIRST.isoformat(), "office_id": str(office.id)},
            )
        )

        assert "Период" in text
        assert FIRST.isoformat() in text
        assert "Часовой пояс" in text
        assert "Asia/Dushanbe" in text
        assert "Сформирован" in text
        # Автор выгрузки называется всегда: файл уходит из системы, и
        # у этого действия должен быть человек.
        assert "Автор выгрузки" in text
        assert exporter.humotech_user.email in text

    def test_summary_carries_the_formulas(
        self, exporter, organization, employee, office
    ):
        five_day_schedule(organization, employee)
        worked(organization, employee, office, FIRST)

        text = body_of(
            exporter.get(
                f"{API}/reports/summary/export",
                {"date_from": FIRST.isoformat(), "date_to": LAST.isoformat()},
            )
        )

        # Спор «как считалась эта цифра» разрешается открытием файла.
        assert "Формула" in text
        assert "рабочие дни по графику" in text
        # И сама дробь рядом с процентом, а не только процент.
        assert "Посещаемость: дробь" in text

    def test_empty_percent_is_explained_not_shown_as_zero(
        self, exporter, office
    ):
        text = body_of(
            exporter.get(
                f"{API}/reports/summary/export",
                {"date_from": FIRST.isoformat(), "date_to": LAST.isoformat()},
            )
        )
        assert "Пустой процент" in text
        assert "Это не ноль процентов" in text

    def test_missing_lateness_is_explained(
        self, exporter, employee, office
    ):
        text = body_of(
            exporter.get(
                f"{API}/reports/attendance/export",
                {"date": FIRST.isoformat(), "office_id": str(office.id)},
            )
        )
        assert "сравнивать не с чем" in text


# --- форматы -----------------------------------------------------------------


class TestFormats:
    def test_csv_is_streamed_not_assembled(self, exporter, employee):
        response = exporter.get(f"{API}/reports/employees/export")

        # Именно потоковый ответ: полумиллионная выгрузка не должна
        # собираться в памяти целиком.
        assert hasattr(response, "streaming_content")
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment" in response["Content-Disposition"]

    def test_csv_starts_with_bom_for_excel(self, exporter, employee):
        text = body_of(exporter.get(f"{API}/reports/employees/export"))
        # Без BOM Excel по-русски читает UTF-8 как cp1251 и показывает
        # «ÐŸÐµÑ€Ð¸Ð¾Ð´» вместо «Период».
        assert text.startswith("﻿")

    def test_xlsx_opens_as_a_real_workbook(self, exporter, employee, office):
        from openpyxl import load_workbook

        response = exporter.get(
            f"{API}/reports/employees/export", {"fmt": "xlsx"}
        )
        assert response.status_code == 200

        book = load_workbook(io.BytesIO(response.content))
        page = book.active
        values = [
            [cell.value for cell in row] for row in page.iter_rows()
        ]
        flat = str(values)
        assert "Автор выгрузки" in flat
        assert employee.employee_number in flat

    def test_xlsx_refuses_to_silently_truncate(self):
        """Молча обрезанный файл выглядит полным. Это хуже отказа."""
        from humotech.core.errors import ValidationFailed
        from humotech.reports.export import XLSX_MAX_ROWS, Sheet, to_xlsx

        sheet = Sheet(
            title="Много",
            columns=["a"],
            rows=[[i] for i in range(XLSX_MAX_ROWS + 1)],
            meta=[],
        )
        with pytest.raises(ValidationFailed) as raised:
            to_xlsx(sheet)
        # И сразу говорит, что делать: CSV отдаётся потоком без предела.
        assert raised.value.details["suggested_format"] == "csv"

    def test_none_becomes_an_empty_cell_not_the_word_none(self):
        from humotech.reports.export import Sheet, to_csv

        sheet = Sheet(
            title="Т", columns=["Колонка"], rows=[[None]], meta=[],
        )
        text = "".join(to_csv(sheet))
        assert "None" not in text
