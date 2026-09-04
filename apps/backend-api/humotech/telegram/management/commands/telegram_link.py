"""Привязки Telegram из командной строки.

Нужна по той же причине, что и `qr_display_device`: интерфейса HR ещё нет,
а разворачивать офис надо уже сейчас. Логика не дублируется — команда
зовёт тот же `TelegramLinkService`, которым потом воспользуется кнопка
в CRM. Своей копии здесь нет намеренно: разошлись бы через полгода,
а обнаружилось бы на работающем офисе.

В отличие от команды для экранов, эта требует `--as-user`. Причина не
техническая: подтвердить привязку — решение человека, и в журнале должен
стоять он. Системное действие означало бы, что через год на вопрос
«кто пустил этот Telegram к данным сотрудника» ответа не найдётся.
Права проверяются настоящие, те же, что у CRM.

Ссылка печатается один раз и больше нигде не восстановима — в базе от неё
остаётся хеш.

Запуск:
    python manage.py telegram_link --list --as-user hr@example.com
    python manage.py telegram_link --issue DEMO-001 --as-user hr@example.com
    python manage.py telegram_link --confirm <id> --as-user hr@example.com
    python manage.py telegram_link --reject <id> --as-user hr@example.com
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from humotech.accounts.models import User
from humotech.core.errors import DomainError
from humotech.core.rbac import Actor
from humotech.employees.models import Employee
from humotech.telegram.services import TelegramLinkService


class Command(BaseCommand):
    help = "Привязки Telegram: ожидающие решения, выдача ссылки, подтверждение, отказ"

    def add_arguments(self, parser) -> None:
        action = parser.add_mutually_exclusive_group(required=True)
        action.add_argument(
            "--list", action="store_true", help="показать ожидающие решения"
        )
        action.add_argument(
            "--issue", metavar="EMPLOYEE_NUMBER", help="выдать ссылку привязки"
        )
        action.add_argument("--confirm", metavar="ID", help="подтвердить привязку")
        action.add_argument("--reject", metavar="ID", help="отклонить привязку")
        parser.add_argument(
            "--as-user", required=True, metavar="EMAIL",
            help="кадровик, от имени которого выполняется действие",
        )

    def handle(self, *args, **options) -> None:
        actor, organization = self._actor(options["as_user"])
        service = TelegramLinkService()

        try:
            if options["list"]:
                self._list(service, actor)
            elif options["issue"]:
                self._issue(service, actor, organization, options["issue"])
            elif options["confirm"]:
                account = service.confirm(actor, options["confirm"])
                self._say_decided(account, "подтверждена")
            else:
                account = service.reject(actor, options["reject"])
                if account is None:
                    self.stdout.write("Отклонено: привязки не было.")
                else:
                    self._say_decided(account, "отклонена")
        except DomainError as error:
            # Доменный отказ — не поломка команды: «уже подтверждено»,
            # «нет прав», «чужая организация». Трассировка здесь только
            # мешала бы прочитать причину.
            raise CommandError(str(error)) from None

    def _actor(self, email: str) -> tuple[Actor, object]:
        user = User.objects.select_related("organization").filter(
            email=email, status="ACTIVE"
        ).first()
        if user is None:
            raise CommandError(f"Действующий пользователь {email} не найден")
        return (
            Actor(user_id=user.id, organization_id=user.organization_id),
            user.organization,
        )

    def _list(self, service: TelegramLinkService, actor: Actor) -> None:
        page = service.pending(actor)
        if not page.items:
            self.stdout.write("Решения не ждёт ничего.")
            return
        self.stdout.write("Ждут решения:")
        for invitation in page.items:
            employee = invitation.employee
            self.stdout.write(
                f"  {invitation.id}  {employee.last_name} {employee.first_name} "
                f"(таб. № {employee.employee_number})"
            )
        self.stdout.write(
            "\nПодтвердить:  --confirm <id> --as-user <email>"
            "\nОтклонить:    --reject <id> --as-user <email>"
        )

    def _issue(
        self, service: TelegramLinkService, actor: Actor, organization,
        employee_number: str,
    ) -> None:
        employee = Employee.objects.filter(
            organization=organization, employee_number=employee_number
        ).first()
        if employee is None:
            raise CommandError(
                f"Сотрудник с табельным номером {employee_number} "
                f"в организации {organization.code} не найден"
            )
        issued = service.create_invitation(actor, employee.id)
        self.stdout.write(
            self.style.SUCCESS(f"Ссылка для {employee.last_name} "
                               f"{employee.first_name}:")
        )
        self.stdout.write(f"  {issued.link}")
        self.stdout.write(f"  действует до {issued.invitation.expires_at}")
        self.stdout.write(
            "\nСсылка показывается один раз: в базе от неё остаётся хеш. "
            "Если потеряется — отзовите и выдайте новую."
        )

    def _say_decided(self, account, verb: str) -> None:
        # Только кириллица и ASCII: консоль Windows живёт в cp1251, и одна
        # типографская стрелка роняет команду UnicodeEncodeError уже ПОСЛЕ
        # того, как решение записано в базу. Выглядит это как «не сработало»,
        # хотя сработало.
        employee = account.employee
        self.stdout.write(
            self.style.SUCCESS(
                f"Привязка {verb}: {employee.last_name} {employee.first_name}, "
                f"состояние {account.status}"
            )
        )
