/**
 * Главный экран: где человек сейчас и что у него сегодня.
 *
 * Незакрытая сессия показывается как незакрытая и помечается словом
 * «пока»: выдуманный конец рабочего дня в документе, по которому считают
 * зарплату, — это ложь, а не удобство.
 *
 * Открытая сессия и «часы сегодня» — разные строки. В три часа ночи
 * у зашедшего в 22:00 сегодняшних часов честно ноль, а в офисе он пять
 * часов; свести их в одно число значило бы соврать в одном из мест.
 */

import type { Profile, Status } from '../api';
import { PRESENCE, duration, time } from '../format';

export function Home({
  profile,
  status,
  onScan,
  onHistory,
  onSickLeave,
  onVacation,
}: {
  profile: Profile;
  status: Status;
  onScan: () => void;
  onHistory: () => void;
  onSickLeave: () => void;
  onVacation: () => void;
}) {
  const tz = status.timezone;
  const inside = status.state === 'IN_OFFICE';

  return (
    <div className="stack">
      <section className="card">
        <h2 className="name">{profile.employee.full_name}</h2>
        <p className="muted">
          {[profile.position?.name, profile.office.name]
            .filter(Boolean)
            .join(' · ')}
        </p>
      </section>

      <section className={`card status ${inside ? 'inside' : ''}`}>
        <p className="state">{PRESENCE[status.state] ?? 'Состояние неизвестно'}</p>
        {status.absence_name && ['SICK_LEAVE', 'VACATION', 'OTHER_ABSENCE'].includes(
          status.state,
        ) && <p className="muted">{status.absence_name}</p>}

        {status.open_session && (
          <p className="muted">
            С {time(status.open_session.started_at, tz)} — пока{' '}
            {duration(status.open_session.seconds)}
            {status.open_session.day !== status.day && ' (со вчера)'}
          </p>
        )}

        <dl className="facts">
          <div>
            <dt>Сегодня в офисе</dt>
            <dd>{duration(status.seconds_today)}</dd>
          </div>
          {status.scheduled_start && status.scheduled_end && (
            <div>
              <dt>График</dt>
              <dd>
                {status.scheduled_start.slice(0, 5)}–
                {status.scheduled_end.slice(0, 5)}
              </dd>
            </div>
          )}
          <div>
            <dt>Последний вход</dt>
            <dd>{time(status.last_entry_at, tz)}</dd>
          </div>
          <div>
            <dt>Последний выход</dt>
            <dd>{time(status.last_exit_at, tz)}</dd>
          </div>
        </dl>
      </section>

      <button type="button" className="primary big" onClick={onScan}>
        {inside ? 'Отметить выход' : 'Отметить вход'}
      </button>

      <div className="grid">
        <button type="button" onClick={onHistory}>
          История
        </button>
        <button type="button" onClick={onSickLeave}>
          Больничный
        </button>
        <button type="button" onClick={onVacation}>
          Отпуск
        </button>
      </div>
    </div>
  );
}
