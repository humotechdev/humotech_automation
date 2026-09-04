"""Заведение экрана показа QR из командной строки.

Нужна потому, что интерфейса HR для экранов ещё нет, а разворачивать офис
надо уже сейчас. Команда делает ровно то же, что потом будет делать кнопка
в CRM, — и делает это ТЕМ ЖЕ сервисом: своей копии логики здесь нет, иначе
команда и CRM через полгода разошлись бы, а обнаружилось бы это на
работающем офисе.

Прав команда не проверяет, и проверять ей нечего: у неё нет ни сессии,
ни роли. Доступ к командной строке сервера здесь и есть право.

Код сопряжения печатается в вывод и больше нигде не восстановим — в базе
от него остаётся хеш. Это неудобно намеренно: восстановимый код означал бы,
что дампа базы достаточно, чтобы подключить свой экран.

Запуск:
    python manage.py qr_display_device --list --organization-code HUMO
    python manage.py qr_display_device --create \\
        --organization-code HUMO --office-code MAIN \\
        --qr-point-code MAIN_ENTRANCE --name "Планшет у входа"
    python manage.py qr_display_device --revoke <id>
    python manage.py qr_display_device --reissue <id>
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from humotech.core.errors import DomainError
from humotech.organizations.models import Organization
from humotech.qr_codes.models import OfficeQrPoint, QrDisplayDevice
from humotech.qr_codes.services import QrDisplayService


class Command(BaseCommand):
    help = "Экраны показа QR: список, заведение, отзыв, перевыпуск кода"

    def add_arguments(self, parser) -> None:
        action = parser.add_mutually_exclusive_group(required=True)
        action.add_argument("--list", action="store_true", help="показать экраны")
        action.add_argument("--create", action="store_true", help="завести экран")
        action.add_argument("--revoke", metavar="ID", help="отозвать доступ экрана")
        action.add_argument(
            "--reissue", metavar="ID", help="новый код сопряжения для экрана"
        )

        parser.add_argument("--organization-code", help="код организации")
        parser.add_argument("--office-code", help="код офиса")
        parser.add_argument("--qr-point-code", help="код QR-точки")
        parser.add_argument("--name", help="как назвать экран")

    def handle(self, *args, **options) -> None:
        service = QrDisplayService()
        try:
            if options["list"]:
                self._list(options)
            elif options["create"]:
                self._create(service, options)
            elif options["revoke"]:
                self._revoke(service, options["revoke"])
            else:
                self._reissue(service, options["reissue"])
        except DomainError as error:
            # Доменная ошибка — это сообщение человеку, а не сбой программы.
            raise CommandError(str(error.message)) from error

    # ------------------------------------------------------------------

    def _list(self, options) -> None:
        devices = QrDisplayDevice.objects.select_related(
            "qr_point", "qr_point__office", "organization"
        ).order_by("organization__code", "qr_point__code", "name")
        if options.get("organization_code"):
            devices = devices.filter(organization__code=options["organization_code"])

        rows = list(devices)
        if not rows:
            self.stdout.write("Экранов нет")
            return
        for device in rows:
            self.stdout.write(
                f"{device.id}  {device.status:<8} "
                f"{device.qr_point.office.name} / {device.qr_point.name} "
                f"— {device.name}"
            )

    def _create(self, service: QrDisplayService, options) -> None:
        for required in ("organization_code", "office_code", "qr_point_code", "name"):
            if not options.get(required):
                raise CommandError(
                    f"--{required.replace('_', '-')} обязателен при --create"
                )

        organization = Organization.objects.filter(
            code=options["organization_code"]
        ).first()
        if organization is None:
            raise CommandError(f"Организация {options['organization_code']} не найдена")

        point = (
            OfficeQrPoint.objects.select_related("office")
            .filter(
                organization=organization,
                office__code=options["office_code"],
                code=options["qr_point_code"],
            )
            .first()
        )
        if point is None:
            raise CommandError(
                "QR-точка не найдена: проверьте --office-code и --qr-point-code"
            )

        issued = service.create_device_for_point(point, name=options["name"])

        self.stdout.write(self.style.SUCCESS(f"Экран заведён: {issued.device.id}"))
        self.stdout.write(f"Офис:  {point.office.name}")
        self.stdout.write(f"Точка: {point.name} ({point.direction_mode})")
        self._print_code(issued.pairing_code)
        self.stdout.write(
            "Введите его на экране. Повторно получить этот код нельзя — "
            "в базе лежит только хеш; при утере запустите --reissue."
        )

    def _revoke(self, service: QrDisplayService, device_id: str) -> None:
        device = service.revoke_the_device(self._device(device_id))
        self.stdout.write(self.style.SUCCESS(f"Экран {device.id} отозван"))
        self.stdout.write("Кодов он больше не получает — со следующего запроса.")

    def _reissue(self, service: QrDisplayService, device_id: str) -> None:
        issued = service.reissue_pairing_for_device(self._device(device_id))
        self._print_code(issued.pairing_code)
        self.stdout.write(
            "Прежний доступ экрана снят: старое устройство кодов больше "
            "не получает."
        )

    # ------------------------------------------------------------------

    def _print_code(self, code: str) -> None:
        self.stdout.write("")
        self.stdout.write("Код сопряжения (показывается один раз):")
        self.stdout.write(self.style.WARNING(code))
        self.stdout.write("")

    def _device(self, device_id: str) -> QrDisplayDevice:
        device = QrDisplayDevice.objects.filter(id=device_id).first()
        if device is None:
            raise CommandError(f"Экран {device_id} не найден")
        return device
