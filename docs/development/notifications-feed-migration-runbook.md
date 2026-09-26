# Production runbook: HR notification feed

This runbook covers Django migrations `notifications.0006_feed_reads` and
`notifications.0007_feed_read_foreign_keys`. They add per-user read markers for
the HR event feed; they do not alter, delete, or rewrite rows in `notifications`.
The worker that sends employee messages does not use `notification_feed_reads`.

## Before deployment

1. Take and verify a PostgreSQL backup. Keep the normal retention and restore
   procedure; this migration has not been approved for production in this task.
2. Deploy first to an isolated copy and compare `notifications` row counts and
   idempotency-key counts before and after migration.
3. Confirm the release contains both migrations and run:

   ```powershell
   python manage.py showmigrations notifications
   python manage.py makemigrations --check --dry-run
   python manage.py check
   ```

4. No export or notification worker needs to be stopped for these two schema
   changes. Keep the usual deployment health checks in place.

## Apply and verify

Apply migrations before starting the new backend version:

```powershell
python manage.py migrate notifications
python manage.py showmigrations notifications
python manage.py check
```

Verify that `0006_feed_reads` and `0007_feed_read_foreign_keys` are marked
applied. Confirm these objects exist on `notification_feed_reads`:

* unique constraint `uq_notification_feed_reads_event` on
  `(user_id, event_type, entity_id)`;
* organization FK with `ON DELETE RESTRICT`;
* user FK with `ON DELETE CASCADE`;
* indexes `ix_notification_feed_reads_org` and
  `ix_notification_feed_reads_user`.

Compare `notifications` row and non-null `idempotency_key` counts with the
pre-deploy values. They should be unchanged. Exercise the bell, read-one,
read-all, and delivery queue health checks under the normal permission/scope
matrix. Do not use organization-wide totals as the badge: it is per user and
visible scope.

The previous backend remains compatible after these additive migrations: it
does not reference the new table. The new backend must not be started until both
migrations are applied.

## Rollback

Prefer rolling back the backend application while leaving the additive schema
in place; the previous backend ignores it. If a schema rollback is explicitly
required, stop the new backend release and first preserve any useful
`notification_feed_reads` state. Then, on the intended database only:

```powershell
python manage.py migrate notifications 0005_attempt_history_complete
python manage.py showmigrations notifications
```

This reverses `0007` and drops the table introduced by `0006`; therefore it
deletes per-user feed-read markers. It does not delete or change employee
notifications or export jobs. Restore from the verified backup if any other
unexpected data or schema change is observed. Never run the rollback against
production without separate approval.
