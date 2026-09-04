/**
 * Статистика и история.
 *
 * Оба экрана показывают то, что посчитал сервер, и ничего не складывают
 * сами. «Рабочих дней: не назначен график» — это null с сервера, а не
 * ноль: ноль рабочих дней при нуле пропусков читался бы как безупречная
 * посещаемость.
 */

import { useEffect, useState } from 'react';

import { api, type Day, type Summary } from '../api';
import { dayLabel, duration, period, time, weekday } from '../format';

const PERIODS = [
  { key: 'today', label: 'Сегодня' },
  { key: 'week', label: 'Неделя' },
  { key: 'month', label: 'Месяц' },
] as const;

export function Stats({ onBack }: { onBack: () => void }) {
  const [active, setActive] = useState<string>('month');
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setSummary(null);
    setError(null);
    void api.statistics({ period: active }).then((result) => {
      if (!alive) return;
      if (result.ok) setSummary(result.value.summary);
      else setError(result.message);
    });
    return () => {
      alive = false;
    };
  }, [active]);

  return (
    <div className="stack">
      <div className="tabs">
        {PERIODS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={active === item.key ? 'tab on' : 'tab'}
            onClick={() => setActive(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {error && <p className="error">{error}</p>}
      {!summary && !error && <p className="waiting">Считаем…</p>}

      {summary && (
        <section className="card">
          <p className="muted">{period(summary.first, summary.last)}</p>
          <p className="big-number">{duration(summary.seconds)}</p>
          {summary.open_sessions > 0 && (
            <p className="muted">
              Есть незакрытая сессия — время предварительное
            </p>
          )}

          <dl className="facts">
            <div>
              <dt>Завершённых сессий</dt>
              <dd>{summary.completed_sessions}</dd>
            </div>
            <div>
              <dt>Дней с отметками</dt>
              <dd>{summary.attended_days}</dd>
            </div>
            <div>
              <dt>Рабочих дней</dt>
              {/* null означает «график не назначен», а не ноль. */}
              <dd>{summary.has_schedule ? summary.working_days : 'не назначен'}</dd>
            </div>
            <div>
              <dt>Пропущено</dt>
              <dd>{summary.has_schedule ? summary.missed_days : '—'}</dd>
            </div>
            {summary.sick_leave_days > 0 && (
              <div>
                <dt>Больничный</dt>
                <dd>{summary.sick_leave_days} дн.</dd>
              </div>
            )}
            {summary.vacation_days > 0 && (
              <div>
                <dt>Отпуск</dt>
                <dd>{summary.vacation_days} дн.</dd>
              </div>
            )}
            {summary.other_absence_days > 0 && (
              <div>
                <dt>Прочие отсутствия</dt>
                <dd>{summary.other_absence_days} дн.</dd>
              </div>
            )}
          </dl>
        </section>
      )}

      <button type="button" onClick={onBack}>
        Назад
      </button>
    </div>
  );
}

export function History({ onBack }: { onBack: () => void }) {
  const [days, setDays] = useState<Day[] | null>(null);
  const [timeZone, setTimeZone] = useState('UTC');
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void api
      .history({ period: 'month', limit: 31 })
      .then((result) => {
        if (!alive) return;
        if (!result.ok) {
          setError(result.message);
          return;
        }
        setDays(result.value.days);
        setHasMore(result.value.has_more);
        const zone = (result.value as unknown as {
          period?: { timezone?: string };
        }).period?.timezone;
        if (zone) setTimeZone(zone);
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="stack">
      <h2>История посещений</h2>
      {error && <p className="error">{error}</p>}
      {!days && !error && <p className="waiting">Загружаем…</p>}
      {days && days.length === 0 && (
        <p className="muted">За этот месяц отметок нет.</p>
      )}

      {days?.map((day) => (
        <section key={day.day} className="card day">
          <header>
            <strong>{dayLabel(day.day)}</strong>
            <span className="muted">{weekday(day.day)}</span>
            <span className="total">{duration(day.seconds)}</span>
          </header>

          {day.sessions?.map((session) => (
            <p key={session.id} className="session">
              {time(session.started_at, timeZone)} —{' '}
              {session.is_open ? (
                <em>ещё в офисе</em>
              ) : (
                time(session.ended_at, timeZone)
              )}
              <span className="muted">
                {' '}
                {[session.office_name, session.entry_point_name]
                  .filter(Boolean)
                  .join(', ')}
              </span>
            </p>
          ))}

          {day.absence_name && <p className="muted">{day.absence_name}</p>}
          {day.missed && <p className="muted">Рабочий день без отметок</p>}
        </section>
      ))}

      {hasMore && (
        <p className="muted">Показан последний месяц.</p>
      )}

      <button type="button" onClick={onBack}>
        Назад
      </button>
    </div>
  );
}
