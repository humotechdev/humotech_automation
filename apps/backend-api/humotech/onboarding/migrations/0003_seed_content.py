"""Стартовое наполнение для организаций, которые уже существуют.

Новой организации это же наполнение ставит команда `seed_onboarding` —
миграция знает только те компании, что были заведены на день её
применения, и одной её мало.

Никого в программу эта миграция НЕ включает. Появление раздела не должно
закрыть бота работающим людям: строка `employee_onboarding` заводится
кадровиком по одному человеку, и это отдельное решение.

Откат удаляет только то, что здесь создано, и только если этим ещё никто
не пользовался: карточку с подтверждениями и редакцию с согласиями
внешние ключи удалить не дадут — и правильно сделают.
"""

from django.db import migrations
from django.utils import timezone

from humotech.onboarding.content import DOCUMENTS, PROGRAM_CODE
from humotech.onboarding.seeding import seed


def fill(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    models = {
        "Program": apps.get_model("onboarding", "OnboardingProgram"),
        "Section": apps.get_model("onboarding", "OnboardingSection"),
        "Document": apps.get_model("onboarding", "PolicyDocument"),
        "Version": apps.get_model("onboarding", "PolicyDocumentVersion"),
    }
    now = timezone.now()
    for organization_id in Organization.objects.values_list("id", flat=True):
        seed(organization_id, now=now, **models)


def drop(apps, schema_editor):
    Program = apps.get_model("onboarding", "OnboardingProgram")
    Section = apps.get_model("onboarding", "OnboardingSection")
    Document = apps.get_model("onboarding", "PolicyDocument")
    Version = apps.get_model("onboarding", "PolicyDocumentVersion")

    codes = [item["code"] for item in DOCUMENTS]
    Version.objects.filter(document__code__in=codes).delete()
    Document.objects.filter(code__in=codes).delete()
    Section.objects.filter(program__code=PROGRAM_CODE).delete()
    Program.objects.filter(code=PROGRAM_CODE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("onboarding", "0002_foreign_keys"),
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(fill, drop),
    ]
