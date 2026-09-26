from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('telegram', '0004_invitation_expected_username'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramlinkinvitation',
            name='consumed_via',
            field=models.CharField(
                blank=True,
                choices=[('LINK', 'Ссылка'), ('USERNAME', 'Имя в Telegram')],
                max_length=20,
                null=True,
            ),
        ),
    ]
