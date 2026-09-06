/**
 * Отметки сотрудника за выбранный день.
 *
 * Загружается для ОДНОГО выбранного человека, а не для каждой строки
 * таблицы: запрос на строку превратил бы список из десяти человек в
 * двадцать обращений к серверу.
 *
 * Ручная отметка — это ДОБАВЛЕНИЕ события, а не правка существующего.
 * Так устроен backend: `source = MANUAL` навсегда отличает её от
 * сканирования, причина обязательна, а прежнее событие остаётся на
 * месте. Поэтому и кнопка называется «Добавить отметку»: обещать
 * редактирование там, где его нет, — худший вид неточности.
 */

import { useState } from 'react';

import * as api from '../api/crm';
import { Icon } from './nav-icons';
import { initials } from './AppShell';
import { messageFor } from '../api/errors';
import { longDate, useBlock, type Block } from '../features/dashboard/data';
import { clock, span } from '../pages/AttendancePage';

const STATE_TITLE: Record<string, string> = {
  IN_OFFICE: 'В офисе',
  LEFT: 'Ушёл',
  NOT_COME: 'Нет отметки',
  VACATION: 'Отпуск',
  SICK_LEAVE: 'Больничный',
  OTHER_ABSENCE: 'Отсутствие',
  DAY_OFF: 'Выходной по графику',
  NO_SCHEDULE: 'Без графика',
};

type Props = {
  row: api.PresenceRow;
  day: string;
  timezone: string;
  canAdd: boolean;
  onClose: () => void;
  onChanged: () => void;
};

export function DayCard({ row, day, timezone, canAdd, onClose, onChanged }: Props) {
  const [adding, setAdding] = useState(false);

  const [detail] = useBlock(
    (signal) =>
      Promise.all([
        api.events(
          { employee_id: row.employee_id, date_from: day, date_to: day, limit: '50' },
          signal,
        ),
        api.attendanceSessions(
          { employee_id: row.employee_id, date_from: day, date_to: day, limit: '20' },
          signal,
        ),
      ]).then(([log, sessions]) => ({ events: log.items, sessions: sessions.items })),
    // Ключ включает и человека, и день: при быстрой смене строк ответ
    // прошлого сотрудника отменяется и карточку не подменяет.
    `day|${row.employee_id}|${day}`,
  );

  return (
    <aside className="panel side-panel" aria-label="Отметки за день">
      <header className="side-panel__head">
        <span className="side-panel__title">Отметки за день</span>
        <button type="button" className="tool" aria-label="Закрыть" onClick={onClose}>✕</button>
      </header>

      <div className="side-panel__body">
        <div className="who">
          <span className="avatar">{initials(row.full_name)}</span>
          <span className="who__text">
            <span className="who__name">{row.full_name}</span>
            <span className="who__id">{row.office_name ?? '—'}</span>
          </span>
        </div>

        <dl className="facts">
          <div className="facts__row">
            <dt>{STATE_TITLE[row.state] ?? row.state}</dt>
            <dd>{longDate(day)}</dd>
          </div>
          <div className="facts__row">
            <dt>График</dt>
            <dd>{row.scheduled_start ? row.scheduled_start.slice(0, 5) : 'Не задан'}</dd>
          </div>
          <div className="facts__row">
            <dt>В офисе за день</dt>
            <dd>{row.seconds ? span(row.seconds) : '—'}</dd>
          </div>
          {row.late_minutes !== null && row.late_minutes > 0 && (
            <div className="facts__row">
              <dt>Пришёл позже</dt>
              <dd>на {row.late_minutes} мин</dd>
            </div>
          )}
          {row.absence_name && (
            <div className="facts__row">
              <dt>Отсутствие</dt>
              <dd>{row.absence_name}</dd>
            </div>
          )}
        </dl>

        <p className="side-panel__label">История отметок</p>
        <Body block={detail} name="отметки">
          {(data) =>
            data.events.length === 0 ? (
              <p className="empty">Отметок за этот день нет.</p>
            ) : (
              <ul className="marks">
                {data.events.map((event) => (
                  <li key={event.id}>
                    <span className="marks__time">{clock(event.occurred_at, timezone)}</span>
                    <span className="marks__what">
                      <span className="marks__kind">
                        {event.event_type === 'ENTRY' ? 'Вход' : 'Выход'}
                      </span>
                      <span className="marks__where">
                        {[event.office_name, event.qr_point_name, source(event.source)]
                          .filter(Boolean)
                          .join(' · ')}
                      </span>
                    </span>
                  </li>
                ))}
                {data.sessions.some((s) => s.is_open) && (
                  <li className="marks__open">
                    <span className="marks__time">—</span>
                    <span className="marks__what">
                      <span className="marks__kind">Посещение продолжается</span>
                      <span className="marks__where">Отметки выхода ещё нет</span>
                    </span>
                  </li>
                )}
              </ul>
            )
          }
        </Body>

        {canAdd ? (
          adding ? (
            <ManualForm
              row={row}
              day={day}
              onCancel={() => setAdding(false)}
              onDone={() => {
                setAdding(false);
                onChanged();
              }}
            />
          ) : (
            <button type="button" className="btn btn--dark" onClick={() => setAdding(true)}>
              <Icon name="doc" size={16} />
              Добавить отметку
            </button>
          )
        ) : (
          <p className="empty">
            Прав на ручную отметку нет. Исправление проводится заявкой — раздел «Заявки».
          </p>
        )}
      </div>
    </aside>
  );
}

/**
 * Форма ручной отметки.
 *
 * Причина обязательна по правилам сервера: по этим событиям потом
 * считают рабочее время, и молчаливой отметки без основания быть не
 * должно.
 */
function ManualForm({ row, day, onCancel, onDone }: {
  row: api.PresenceRow; day: string; onCancel: () => void; onDone: () => void;
}) {
  const [type, setType] = useState<'ENTRY' | 'EXIT'>('ENTRY');
  const [time, setTime] = useState('09:00');
  const [reason, setReason] = useState('');
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  async function submit() {
    if (sending) return;
    if (!row.office_id) {
      setFailed('У сотрудника нет текущего офиса — отметку некуда записать.');
      return;
    }
    setSending(true);
    setFailed(null);
    try {
      await api.addManualEvent({
        employee_id: row.employee_id,
        office_id: row.office_id,
        event_type: type,
        occurred_at: `${day}T${time}:00`,
        reason,
      });
      onDone();
    } catch (error) {
      // Введённое остаётся в форме: набирать причину заново из-за сбоя —
      // худшее, что можно предложить человеку.
      setFailed(messageFor(error));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="manual">
      <p className="side-panel__label">Добавить отметку</p>
      <p className="side-panel__text">
        {row.full_name} · {longDate(day)} · время офиса
      </p>

      <div className="manual__row">
        <label className="pick">
          <span className="visually-hidden">Направление</span>
          <select value={type} onChange={(event) => setType(event.target.value as 'ENTRY' | 'EXIT')}>
            <option value="ENTRY">Вход</option>
            <option value="EXIT">Выход</option>
          </select>
        </label>
        <label className="pick">
          <span className="visually-hidden">Время</span>
          <input type="time" value={time} aria-label="Время"
                 onChange={(event) => setTime(event.target.value)} />
        </label>
      </div>

      <textarea
        className="area"
        rows={2}
        value={reason}
        placeholder="Причина — обязательна"
        aria-label="Причина"
        onChange={(event) => setReason(event.target.value)}
      />

      {failed && <p className="empty empty--bad" role="alert">{failed}</p>}

      <div className="side-panel__actions">
        <button type="button" className="btn btn--dark" disabled={sending || reason.trim().length < 3}
                onClick={() => void submit()}>
          {sending ? 'Сохраняем…' : 'Сохранить'}
        </button>
        <button type="button" className="btn" disabled={sending} onClick={onCancel}>
          Отмена
        </button>
      </div>
    </div>
  );
}

const source = (code: string) =>
  ({ QR: 'QR', MANUAL: 'вручную', IMPORT: 'импорт' })[code] ?? code;

function Body<T>({ block, name, children }: {
  block: Block<T>; name: string; children: (data: T) => React.ReactNode;
}) {
  if (block.state === 'loading') return <p className="empty">Загружаем {name}…</p>;
  if (block.state === 'denied') return <p className="empty">Нет доступа к отметкам.</p>;
  if (block.state === 'error') return <p className="empty empty--bad">Не удалось загрузить {name}.</p>;
  return <>{children(block.data)}</>;
}
