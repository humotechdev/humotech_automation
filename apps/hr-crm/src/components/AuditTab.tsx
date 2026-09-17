/**
 * Журнал действий: кто, когда, что и над чем.
 *
 * Только чтение — и не потому, что «пока не сделали». Записи кладут
 * сервисы в той же транзакции, что и само изменение; журнал, который
 * можно поправить тем же ключом, которым делают изменения, ничего не
 * доказывает. Поэтому ни правки, ни удаления здесь нет и не появится.
 *
 * Секретов в значениях нет: пароли, токены и хеши вырезает
 * `AuditTrail.sanitize` при записи. Обратно они не расшифровываются —
 * для установки пароля в журнале остаётся только факт операции.
 */

import { useCallback, useMemo, useRef, useState } from 'react';

import * as api from '../api/crm';
import { ApiFailure } from '../api/errors';
import { AppIcon } from './AppIcon';
import { AppSelectField } from './AppSelect';
import { AppDateRangePicker } from './DateRangePicker';
import { Diff } from './UserCard';
import { today, useBlock } from '../features/dashboard/data';
import {
  AUDIT_FILTERS,
  actionTitle,
  entityTitle,
  shownLine,
} from '../features/admin/model';
import { moment } from '../features/time/zone';

const PAGE = '25';

export function AuditTab({ zone, mayRead }: { zone: string; mayRead: boolean }) {
  const at = useCallback(
    (value: string) => moment(value, zone, false),
    [zone],
  );
  const [action, setAction] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [actor, setActor] = useState('');
  const [tail, setTail] = useState<api.AuditEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [more, setMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const busy = useRef(false);

  const params = useMemo(
    () => ({
      ...(action ? { action } : {}),
      ...(from ? { date_from: from } : {}),
      ...(to ? { date_to: to } : {}),
      ...(actor ? { actor_user_id: actor } : {}),
      limit: PAGE,
    }),
    [action, actor, from, to],
  );

  const key = JSON.stringify(params);

  const [block, reload] = useBlock(
    (signal) => api.auditLogs(params, signal),
    `audit|${key}`,
    mayRead,
  );

  // Смена фильтра — это другой набор: дочитанный хвост от прежнего к нему
  // не относится и обязан исчезнуть.
  const shownKey = useRef(key);
  if (shownKey.current !== key) {
    shownKey.current = key;
    if (tail.length) setTail([]);
  }

  const first = block.state === 'ready' ? block.data.items : [];
  const items = [...first, ...tail];
  const nextCursor = tail.length
    ? cursor
    : block.state === 'ready'
      ? block.data.next_cursor
      : null;
  const hasMore = tail.length
    ? more
    : block.state === 'ready'
      ? block.data.has_more
      : false;

  const loadMore = useCallback(async () => {
    if (busy.current || !nextCursor) return;
    busy.current = true;
    setLoadingMore(true);
    try {
      const page = await api.auditLogs({ ...params, cursor: nextCursor });
      setTail((was) => [...was, ...page.items]);
      setCursor(page.next_cursor);
      setMore(page.has_more);
    } catch {
      // Дочитать не вышло — уже показанное остаётся на месте.
    } finally {
      busy.current = false;
      setLoadingMore(false);
    }
  }, [nextCursor, params]);

  if (!mayRead) {
    return (
      <p className="empty empty--bad">
        Нет права на чтение журнала. В нём видно, кто и что менял по всей
        организации, поэтому это отдельное разрешение — попросите
        <span> </span>
        <span className="mono">audit.read</span> у администратора.
      </p>
    );
  }

  return (
    <section className="panel" aria-label="Журнал действий">
      <div className="toolbar">
        <AppSelectField label="Действие" value={action} onChange={setAction}>
            {AUDIT_FILTERS.map((item) => (
              <option key={item.key} value={item.key}>{item.title}</option>
            ))}
        </AppSelectField>
        <AppDateRangePicker label="Период журнала" now={today()} from={from} to={to} className="audit-date-range"
          onFromChange={setFrom} onToChange={setTo} />
        <label className="find">
          <AppIcon name="search" size={16} />
          <input type="search" value={actor} placeholder="ID инициатора"
                 aria-label="Идентификатор инициатора"
                 onChange={(event) => setActor(event.target.value.trim())} />
        </label>
      </div>

      {block.state === 'loading' && <p className="empty">Читаем журнал…</p>}
      {block.state === 'denied' && (
        <p className="empty">Сессия истекла. Войдите заново.</p>
      )}
      {block.state === 'error' && (
        <p className="empty empty--bad">
          Не удалось загрузить журнал. Это ошибка запроса, а не пустая история.{' '}
          <button type="button" className="link" onClick={reload}>Повторить</button>
        </p>
      )}

      {block.state === 'ready' && items.length === 0 && (
        <p className="empty">
          {action || from || to || actor
            ? 'Под выбранные условия записей нет.'
            : 'Журнал пуст: изменений ещё не было.'}
        </p>
      )}

      {block.state === 'ready' && items.length > 0 && (
        <>
          <div className="scroller">
            <table className="grid-table table-cards" aria-label="Записи журнала">
              <thead>
                <tr>
                  <th scope="col">Когда</th>
                  <th scope="col">Инициатор</th>
                  <th scope="col">Действие</th>
                  <th scope="col">Объект</th>
                  <th scope="col">Что изменилось</th>
                </tr>
              </thead>
              <tbody>
                {items.map((entry) => (
                  <tr key={entry.id}>
                    <td className="num">{at(entry.occurred_at)}</td>
                    <td data-label="Инициатор">{entry.actor_email ?? 'Система'}</td>
                    <td data-label="Действие">{actionTitle(entry.action)}</td>
                    <td data-label="Объект">
                      <span className="who__text">
                        <span className="who__name">
                          {entityTitle(entry.entity_type)}
                        </span>
                        <span className="who__id mono">
                          {entry.entity_id.slice(0, 8)}
                        </span>
                      </span>
                    </td>
                    <td data-label="Что изменилось"><Diff entry={entry} at={at} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="pager">
            {/* Пагинация курсорная: номеров страниц у API нет, и рисовать
                их значило бы обещать переход, которого не существует. */}
            <p className="pager__note">{shownLine(items.length, hasMore, null)}</p>
            {hasMore && (
              <button type="button" className="btn btn--small"
                      disabled={loadingMore} onClick={() => void loadMore()}>
                {loadingMore ? 'Читаем…' : 'Показать ещё'}
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}

export { ApiFailure };
