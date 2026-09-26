"""Безопасность приёма и выдачи файлов (зона files).

Атакующий проход по всем местам, где файл принимается или отдаётся:
справка больничного (CRM и кабинет сотрудника), кадровые документы и
фотография, файл из ответа на обращение. Данные вымышленные.

Что проверяется:
  * содержимое против заявленного типа, polyglot, SVG/HTML, пустой файл,
    «бомба распаковки» по заголовку PNG/JPEG;
  * имя файла: `../`, абсолютный путь, NUL, CR LF, U+202E, очень длинное;
  * заголовки выдачи: nosniff, no-store, inline/attachment, CSP картинкам,
    отсутствие инъекции через имя;
  * `scan_status` и удаление — одна проверка во всех view выдачи;
  * чужая организация, чужой сотрудник, отключённый пользователь,
    уволенный сотрудник, прямой адрес каталога хранилища.
"""

from __future__ import annotations

import struct
import zlib
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from humotech.absences.models import AbsenceDocument, AbsenceType
from humotech.absences.services import AbsenceService
from humotech.core.errors import NotFound, ValidationFailed
from humotech.employees.attachments import EmployeeAttachmentService
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.files.models import File
from humotech.files.serving import (
    EMPLOYEE,
    STAFF,
    display_name,
    require_viewable,
    viewable_for,
)
from humotech.files.storage import store
from humotech.telegram.identity import resolve_by_telegram_user_id

from .conftest import bot_headers, link_telegram

pytestmark = pytest.mark.django_db

API = "/api/v1"
TG_ID = 777_000_111
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF\n"
ABSENCE_HR = ("employees.read", "absences.read", "absences.approve", "absences.documents")
EMPLOYEE_HR = ("employees.read", "employees.manage")


# --- образцы файлов ----------------------------------------------------------


def png(width: int, height: int) -> bytes:
    """Настоящий заголовок PNG заданного размера. Данных кадра почти нет:
    так и выглядит «бомба» — килобайты на диске, гигабайты в памяти."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    body = struct.pack(">I", len(ihdr)) + chunk + struct.pack(">I", zlib.crc32(chunk))
    idat = zlib.compress(b"\x00" * 16)
    body += struct.pack(">I", len(idat)) + b"IDAT" + idat + struct.pack(">I", zlib.crc32(b"IDAT" + idat))
    return b"\x89PNG\r\n\x1a\n" + body + b"\x00\x00\x00\x00IEND\xaeB`\x82"


def jpeg(width: int, height: int) -> bytes:
    """JPEG с APP0 и SOF0: размер кадра стоит после служебного сегмента."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def upload(name: str, content: bytes, mime: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type=mime)


def put(org, name="spravka.pdf", content=PDF, mime="application/pdf", *,
        allowed=("application/pdf", "image/png", "image/jpeg"), max_bytes=10 * 1024 * 1024):
    return store(
        upload(name, content, mime), organization_id=org.id, employee=None,
        allowed_types=allowed, max_bytes=max_bytes, prefix="sec-test",
    ).file


# --- фикстуры ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def private_files(settings, tmp_path):
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path), "SCANNER_ENABLED": False}


@pytest.fixture()
def sick_leave(db, organization):
    return AbsenceType.objects.create(
        organization=organization, code="SICK_LEAVE", name="Больничный",
        is_paid=True, requires_approval=True, requires_document=True,
    )


@pytest.fixture()
def context(db, employee, telegram_settings):
    link_telegram(employee)
    return resolve_by_telegram_user_id(TG_ID)


@pytest.fixture()
def sick(context, sick_leave):
    """Больничный с приложенной справкой — как его подаёт сотрудник."""
    view = AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=date.today(), last_day=date.today() + timedelta(days=2),
        document=upload("spravka.pdf", PDF, "application/pdf"),
    )
    document = AbsenceDocument.objects.get(absence_request=view.request)
    return view.request, document


@pytest.fixture()
def hr_client(make_user, organization):
    client = APIClient()
    client.force_authenticate(user=make_user(organization, permissions=ABSENCE_HR))
    return client


def hr_url(request, document):
    return f"{API}/absence-requests/{request.id}/documents/{document.id}/download"


def me_url(request):
    return f"{API}/me/absences/{request.id}/document"


def body(response) -> bytes:
    return b"".join(response.streaming_content)


# =============================================================================
# Приём: содержимое, тип, размер
# =============================================================================


class TestUploadContent:
    def test_html_declared_as_png_is_refused(self, organization):
        with pytest.raises(ValidationFailed, match="Содержимое"):
            put(organization, "x.png", b"<html><script>alert(1)</script>", "image/png")

    def test_svg_is_not_an_allowed_type(self, organization):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
        with pytest.raises(ValidationFailed, match="формат"):
            put(organization, "x.svg", svg, "image/svg+xml")

    def test_html_is_not_an_allowed_type(self, organization):
        with pytest.raises(ValidationFailed, match="формат"):
            put(organization, "x.html", b"<script>1</script>", "text/html")

    def test_extension_contradicting_type_is_refused(self, organization):
        with pytest.raises(ValidationFailed, match="Расширение"):
            put(organization, "x.png", PDF, "application/pdf")

    def test_empty_file_is_refused(self, organization):
        with pytest.raises(ValidationFailed, match="пустой"):
            put(organization, "x.pdf", b"", "application/pdf")

    def test_size_over_limit_is_refused_before_storing(self, organization, tmp_path):
        with pytest.raises(ValidationFailed, match="большой"):
            put(organization, "x.pdf", PDF + b"0" * 2048, "application/pdf", max_bytes=1024)
        assert not File.objects.exists()
        assert not any(tmp_path.rglob("*.pdf"))

    def test_png_decompression_bomb_is_refused(self, organization):
        with pytest.raises(ValidationFailed, match="точках"):
            put(organization, "bomb.png", png(60_000, 60_000), "image/png")

    def test_jpeg_decompression_bomb_is_refused(self, organization):
        with pytest.raises(ValidationFailed, match="точках"):
            put(organization, "bomb.jpg", jpeg(20_000, 20_000), "image/jpeg")

    def test_zero_sized_png_is_refused(self, organization):
        with pytest.raises(ValidationFailed, match="точках"):
            put(organization, "zero.png", png(0, 10), "image/png")

    def test_ordinary_phone_photo_passes(self, organization):
        assert put(organization, "a.png", png(4000, 3000), "image/png").mime_type == "image/png"
        assert put(organization, "a.jpg", jpeg(4032, 3024), "image/jpeg").mime_type == "image/jpeg"

    def test_unparsable_jpeg_header_still_passes(self, organization):
        """Сигнатура верна, а размер не разобрать — не повод отказать:
        так выглядят снимки части камер, и так устроены старые тесты."""
        assert put(organization, "a.jpg", b"\xff\xd8\xff\xe0garbage", "image/jpeg")

    def test_pdf_polyglot_is_stored_only_as_pdf(self, organization):
        polyglot = b"%PDF-1.4\n<html><script>alert(document.cookie)</script></html>"
        record = put(organization, "x.pdf", polyglot, "application/pdf")
        assert record.mime_type == "application/pdf"

    def test_storage_key_does_not_follow_the_name(self, organization, tmp_path):
        record = put(organization, "../../../../etc/cron.d/evil.pdf")
        assert ".." not in record.storage_key
        assert "evil" not in record.storage_key
        stored = (Path(tmp_path) / record.storage_key).resolve()
        assert str(stored).startswith(str(Path(tmp_path).resolve()))


class TestUploadName:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("../../etc/passwd.pdf", "passwd.pdf"),
            ("/etc/passwd.pdf", "passwd.pdf"),
            ("C:\\Windows\\System32\\x.pdf", "x.pdf"),
            ("spr\x00avka.pdf", "spravka.pdf"),
            ("a\r\nSet-Cookie: x=1.pdf", "aSet-Cookie: x=1.pdf"),
            ("spravka\u202efdp.exe", "spravkafdp.exe"),
            ("..", "документ"),
            ("", "документ"),
        ],
    )
    def test_name_is_cleaned(self, raw, expected):
        assert display_name(raw) == expected

    def test_long_name_is_cut_keeping_extension(self):
        cleaned = display_name("я" * 1000 + ".pdf")
        assert len(cleaned) <= 255
        assert cleaned.endswith(".pdf")

    def test_stored_name_is_cleaned(self, organization):
        record = put(organization, "x\u202e\r\nfdp.pdf")
        assert "\u202e" not in record.original_filename
        assert "\r" not in record.original_filename and "\n" not in record.original_filename

    def test_multipart_name_with_crlf_does_not_break_download(
        self, bot_client, hr_client, context, sick_leave
    ):
        """Имя приходит из multipart. Django разворачивает в нём HTML-сущности,
        и `&#13;&#10;` превращается в перевод строки."""
        made = AbsenceService().create(
            context, absence_type_code="SICK_LEAVE",
            first_day=date.today(), last_day=date.today() + timedelta(days=2),
        )
        answer = bot_client.post(
            me_url(made.request),
            {"document": upload("a&#13;&#10;X-Evil: 1\u202e.pdf", PDF, "application/pdf")},
            format="multipart", **bot_headers(TG_ID),
        )
        assert answer.status_code == 201, answer.content
        document = AbsenceDocument.objects.get(absence_request=made.request)
        assert "\n" not in document.file.original_filename
        response = hr_client.get(hr_url(made.request, document))
        assert response.status_code == 200
        assert "X-Evil" not in response.headers
        assert "\n" not in response["Content-Disposition"]


# =============================================================================
# Выдача: заголовки
# =============================================================================


class TestResponseHeaders:
    def test_hr_download_is_hardened(self, hr_client, sick):
        request, document = sick
        response = hr_client.get(hr_url(request, document))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response["X-Content-Type-Options"] == "nosniff"
        assert "no-store" in response["Cache-Control"]
        assert response["Content-Disposition"].startswith("inline")
        assert body(response).startswith(b"%PDF")

    def test_legacy_record_with_crlf_name_is_served_not_500(self, hr_client, sick):
        """Запись со «сломанным» именем, попавшая в базу до фикса."""
        request, document = sick
        File.objects.filter(id=document.file_id).update(
            original_filename='a"\r\nSet-Cookie: sid=evil; x=".pdf'
        )
        response = hr_client.get(hr_url(request, document))
        assert response.status_code == 200
        assert "Set-Cookie" not in response.headers
        assert "\r" not in response["Content-Disposition"]

    def test_unknown_mime_is_served_as_attachment_octet_stream(self, hr_client, sick):
        """Тип в базе подменён (ручная правка, импорт) — HTML не исполнится."""
        request, document = sick
        File.objects.filter(id=document.file_id).update(mime_type="text/html")
        response = hr_client.get(hr_url(request, document))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/octet-stream"
        assert response["Content-Disposition"].startswith("attachment")
        assert "sandbox" in response["Content-Security-Policy"]

    def test_image_gets_sandbox_csp(self, make_user, organization, employee):
        hr = make_user(organization, permissions=EMPLOYEE_HR)
        from humotech.core.rbac import Actor

        actor = Actor(user_id=hr.id, organization_id=organization.id)
        service = EmployeeAttachmentService()
        record = service.upload(actor, upload=upload("me.png", png(10, 10), "image/png"),
                                purpose="photo")
        service.set_photo(actor, employee.id, record.id)
        client = APIClient()
        client.force_authenticate(user=hr)
        response = client.get(f"{API}/employees/{employee.id}/photo/")
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        assert "sandbox" in response["Content-Security-Policy"]
        assert response["X-Content-Type-Options"] == "nosniff"

    def test_employee_own_certificate_keeps_fixed_name(self, bot_client, sick):
        request, _ = sick
        response = bot_client.get(me_url(request), **bot_headers(TG_ID))
        assert response.status_code == 200
        assert response["Content-Disposition"] == f'inline; filename="certificate-{request.id}"'
        assert response["X-Content-Type-Options"] == "nosniff"


# =============================================================================
# Выдача: scan_status и удаление — одна проверка везде
# =============================================================================


class TestScanStatus:
    @pytest.mark.parametrize("status", ["INFECTED", "FAILED"])
    def test_bad_status_is_never_served(self, hr_client, bot_client, sick, status):
        request, document = sick
        File.objects.filter(id=document.file_id).update(scan_status=status)
        assert hr_client.get(hr_url(request, document)).status_code == 404
        assert bot_client.get(me_url(request), **bot_headers(TG_ID)).status_code == 404

    def test_pending_goes_to_hr_only_as_attachment(self, hr_client, bot_client, sick):
        request, document = sick
        File.objects.filter(id=document.file_id).update(scan_status="PENDING")
        response = hr_client.get(hr_url(request, document))
        assert response.status_code == 200
        assert response["Content-Disposition"].startswith("attachment")
        assert bot_client.get(me_url(request), **bot_headers(TG_ID)).status_code == 404

    def test_deleted_file_is_not_served(self, hr_client, bot_client, sick):
        request, document = sick
        File.objects.filter(id=document.file_id).update(deleted_at=timezone.now())
        assert hr_client.get(hr_url(request, document)).status_code == 404
        assert bot_client.get(me_url(request), **bot_headers(TG_ID)).status_code == 404

    def test_employee_document_respects_scan_status(self, make_user, organization, employee):
        from humotech.core.rbac import Actor

        hr = make_user(organization, permissions=EMPLOYEE_HR)
        actor = Actor(user_id=hr.id, organization_id=organization.id)
        service = EmployeeAttachmentService()
        record = service.upload(actor, upload=upload("pass.pdf", PDF, "application/pdf"),
                                purpose="document")
        row = service.attach_document(actor, employee.id, kind="OTHER", file_id=record.id,
                                      title="Паспорт")
        client = APIClient()
        client.force_authenticate(user=hr)
        url = f"{API}/employees/{employee.id}/documents/{row.id}/download/"
        assert client.get(url).status_code == 200
        File.objects.filter(id=record.id).update(scan_status="INFECTED")
        assert client.get(url).status_code == 404
        # Фотография идёт той же проверкой.
        photo = service.upload(actor, upload=upload("me.jpg", jpeg(10, 10), "image/jpeg"),
                               purpose="photo")
        service.set_photo(actor, employee.id, photo.id)
        File.objects.filter(id=photo.id).update(deleted_at=timezone.now())
        assert client.get(f"{API}/employees/{employee.id}/photo/").status_code == 404

    def test_rules_of_the_helper(self, organization):
        record = put(organization)
        assert viewable_for(record, STAFF) and viewable_for(record, EMPLOYEE)
        record.scan_status = "PENDING"
        assert viewable_for(record, STAFF) and not viewable_for(record, EMPLOYEE)
        record.scan_status = "SOMETHING_NEW"
        assert not viewable_for(record, STAFF)
        with pytest.raises(NotFound):
            require_viewable(record, STAFF)

    def test_scanner_enabled_marks_new_files_pending(self, settings, organization):
        settings.FILES = {**settings.FILES, "SCANNER_ENABLED": True}
        assert put(organization).scan_status == "PENDING"

    def test_employee_upload_still_works_with_scanner_off(
        self, bot_client, context, sick_leave
    ):
        """Главный путь не сломан: справка из чата принимается и видна."""
        made = AbsenceService().create(
            context, absence_type_code="SICK_LEAVE",
            first_day=date.today(), last_day=date.today() + timedelta(days=2),
        )
        answer = bot_client.post(
            me_url(made.request),
            {"document": upload("photo.jpg", jpeg(3000, 4000), "image/jpeg")},
            format="multipart", **bot_headers(TG_ID),
        )
        assert answer.status_code == 201, answer.content
        own = bot_client.get(me_url(made.request), **bot_headers(TG_ID))
        assert own.status_code == 200
        assert own["Content-Type"] == "image/jpeg"


# =============================================================================
# Выдача: чужой файл, отозванные права, прямой адрес
# =============================================================================


class TestAccess:
    def test_anonymous_gets_nothing(self, sick):
        request, document = sick
        client = APIClient()
        assert client.get(hr_url(request, document)).status_code in (401, 403)
        assert client.get(me_url(request)).status_code in (401, 403)

    def test_other_organization_gets_not_found(self, make_user, other_organization, sick):
        request, document = sick
        client = APIClient()
        client.force_authenticate(user=make_user(other_organization, permissions=ABSENCE_HR))
        assert client.get(hr_url(request, document)).status_code == 404

    def test_hr_of_another_office_gets_not_found(self, make_user, organization, other_office, sick):
        request, document = sick
        client = APIClient()
        client.force_authenticate(
            user=make_user(organization, permissions=ABSENCE_HR, office=other_office)
        )
        assert client.get(hr_url(request, document)).status_code == 404

    def test_employee_cannot_open_colleagues_certificate(
        self, bot_client, organization, office, sick, telegram_settings
    ):
        request, _ = sick
        other = Employee.objects.create(
            organization=organization, employee_number="EMP-SEC-2",
            first_name="Пётр", last_name="Петров", hire_date=date(2024, 2, 1),
            employment_status="ACTIVE",
        )
        EmployeeAssignment.objects.create(
            organization=organization, employee=other, office=office,
            employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
            valid_from=date(2024, 2, 1),
        )
        link_telegram(other, telegram_user_id=777_000_222)
        response = bot_client.get(me_url(request), **bot_headers(777_000_222))
        assert response.status_code == 404

    def test_deactivated_hr_loses_access(self, make_user, organization, sick):
        request, document = sick
        user = make_user(organization, permissions=ABSENCE_HR)
        client = APIClient()
        client.force_login(user)
        assert client.get(hr_url(request, document)).status_code == 200
        user.status = "INACTIVE"
        user.save(update_fields=["status"])
        assert client.get(hr_url(request, document)).status_code in (401, 403)

    def test_dismissed_employee_loses_access(self, bot_client, employee, sick):
        request, _ = sick
        Employee.objects.filter(id=employee.id).update(employment_status="TERMINATED")
        assert bot_client.get(me_url(request), **bot_headers(TG_ID)).status_code in (401, 403)

    @pytest.mark.parametrize(
        "path",
        ["/private-media/", "/private-media/absences/x.pdf", "/media/absences/x.pdf",
         "/private-exports/x.xlsx", "/api/v1/private-media/absences/x.pdf"],
    )
    def test_storage_directory_has_no_url(self, path):
        response = APIClient().get(path)
        assert response.status_code == 404

    # Исправлено зоной rbac: open_photo/open_document зовут
    # require_visible_employee. Свой сотрудник вне области отвечает 403 —
    # как и его карточка (контракт core/rbac.py); чужая организация — 404.
    def test_employee_files_respect_office_scope(
        self, make_user, organization, employee, other_office
    ):
        from humotech.core.rbac import Actor

        hr = make_user(organization, permissions=EMPLOYEE_HR)
        actor = Actor(user_id=hr.id, organization_id=organization.id)
        service = EmployeeAttachmentService()
        photo = service.upload(actor, upload=upload("me.png", png(10, 10), "image/png"),
                               purpose="photo")
        service.set_photo(actor, employee.id, photo.id)
        outsider = make_user(organization, permissions=("employees.read",), office=other_office)
        client = APIClient()
        client.force_authenticate(user=outsider)
        assert client.get(f"{API}/employees/{employee.id}/photo/").status_code in (403, 404)
