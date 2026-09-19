"""Код наклейки хранится, чтобы его можно было показать и распечатать.

Раньше в базе лежал только хеш, и код показывался один раз — при
выпуске. Кадровик, потерявший картинку, вынужден был менять код и
переклеивать наклейки у всех дверей. Колонка `static_token` хранит сам
код; у точек, выпущенных раньше, она пустая — восстановить их код
неоткуда.

Знание кода не позволяет отметиться из дома: сервер принимает отметку
по печатному коду только с координатами внутри радиуса офиса.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qr_codes", "0004_office_setup"),
    ]

    operations = [
        migrations.AddField(
            model_name="officeqrpoint",
            name="static_token",
            field=models.TextField(blank=True, null=True),
        ),
    ]
