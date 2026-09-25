from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_user_full_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='AuthRateCounter',
            fields=[
                ('key', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('hits', models.IntegerField(default=0)),
                ('window_started_at', models.DateTimeField()),
                ('locked_until', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField()),
            ],
            options={
                'verbose_name': 'счётчик попыток',
                'verbose_name_plural': 'счётчики попыток',
                'db_table': 'auth_rate_counters',
                'indexes': [models.Index(fields=['updated_at'], name='ix_auth_rate_counters_upd')],
            },
        ),
    ]
