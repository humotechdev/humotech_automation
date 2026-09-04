"""Одноразовые ссылки привязки Telegram и статус PENDING у самой привязки.

Обратима полностью: откат сносит `telegram_link_invitations` и возвращает
прежний CHECK статусов привязки. Единственное, чего откат вернуть не может, —
строки со статусом PENDING: если такие есть, прежний CHECK их не примет.
Данных на момент перехода нет, а появиться они могут только через новый код.

Внешние ключи новой таблицы переобъявляет `0003_link_invitation_foreign_keys`:
`ON DELETE` Django в DDL не выводит, а вся защита истории держится именно
на нём (см. core/0002).
"""


import django.contrib.postgres.functions
import django.db.models.deletion
import humotech.core.functions
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0001_initial'),
        ('organizations', '0001_initial'),
        ('telegram', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramLinkInvitation',
            fields=[
                ('id', models.UUIDField(db_default=django.contrib.postgres.functions.RandomUUID(), editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(db_default=humotech.core.functions.TransactionNow(), editable=False)),
                ('updated_at', models.DateTimeField(db_default=humotech.core.functions.TransactionNow(), editable=False)),
                ('token_hash', models.CharField(max_length=64)),
                ('status', models.CharField(choices=[('ACTIVE', 'ACTIVE'), ('PENDING_CONFIRMATION', 'PENDING_CONFIRMATION'), ('USED', 'USED'), ('REJECTED', 'REJECTED'), ('REVOKED', 'REVOKED'), ('EXPIRED', 'EXPIRED')], max_length=30)),
                ('expires_at', models.DateTimeField()),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('consumed_by_telegram_user_id', models.BigIntegerField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'приглашение к привязке Telegram',
                'verbose_name_plural': 'приглашения к привязке Telegram',
                'db_table': 'telegram_link_invitations',
            },
        ),
        migrations.RemoveConstraint(
            model_name='telegramaccount',
            name='ck_telegram_accounts_status',
        ),
        migrations.AlterField(
            model_name='telegramaccount',
            name='status',
            field=models.CharField(choices=[('PENDING', 'PENDING'), ('ACTIVE', 'ACTIVE'), ('REVOKED', 'REVOKED'), ('BLOCKED', 'BLOCKED')], max_length=20),
        ),
        migrations.AddConstraint(
            model_name='telegramaccount',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['PENDING', 'ACTIVE', 'REVOKED', 'BLOCKED'])), name='ck_telegram_accounts_status'),
        ),
        migrations.AddField(
            model_name='telegramlinkinvitation',
            name='created_by_user',
            field=models.ForeignKey(db_column='created_by_user_id', db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='telegramlinkinvitation',
            name='employee',
            field=models.ForeignKey(db_column='employee_id', db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='telegram_invitations', to='employees.employee'),
        ),
        migrations.AddField(
            model_name='telegramlinkinvitation',
            name='organization',
            field=models.ForeignKey(db_column='organization_id', db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='telegramlinkinvitation',
            name='reviewed_by_user',
            field=models.ForeignKey(blank=True, db_column='reviewed_by_user_id', db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddIndex(
            model_name='telegramlinkinvitation',
            index=models.Index(fields=['organization'], name='ix_telegram_link_invitations_org_id'),
        ),
        migrations.AddIndex(
            model_name='telegramlinkinvitation',
            index=models.Index(fields=['employee'], name='ix_telegram_link_invitations_employee_id'),
        ),
        migrations.AddIndex(
            model_name='telegramlinkinvitation',
            index=models.Index(fields=['organization', 'status', '-created_at'], name='ix_telegram_link_invitations_status'),
        ),
        migrations.AddConstraint(
            model_name='telegramlinkinvitation',
            constraint=models.UniqueConstraint(fields=('token_hash',), name='uq_telegram_link_invitations_token'),
        ),
        migrations.AddConstraint(
            model_name='telegramlinkinvitation',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['ACTIVE', 'PENDING_CONFIRMATION', 'USED', 'REJECTED', 'REVOKED', 'EXPIRED'])), name='ck_telegram_link_invitations_status'),
        ),
        migrations.AddConstraint(
            model_name='telegramlinkinvitation',
            constraint=models.UniqueConstraint(condition=models.Q(('status__in', ('ACTIVE', 'PENDING_CONFIRMATION'))), fields=('employee',), name='uq_telegram_link_invitations_open_employee'),
        ),
    ]
