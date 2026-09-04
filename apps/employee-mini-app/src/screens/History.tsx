/**
 * История посещений: день за днём, каждый вход и каждый выход.
 *
 * Таблицы здесь нет и не будет. Таблица на экране шириной 360 px либо
 * прокручивается вбок, либо режет содержимое; и то и другое хуже, чем
 * карточка на день, где всё видно сразу.
 *
 * Открытая сессия помечается словом, а не выдуманным временем выхода.
 * Прочерк вместо «18:00» — это честный прочерк.
 */

import { useCallback, useEffect, useState } from 'react';

import { api, type Day, type OpenSession } from '../api';
import { dayLabel, duration, time, weekday } from '../format';
import { HistoryIcon } from '../ui/icons';
import {
  Card,
  SecondaryButton,
  SectionHeader,
  StatusBadge,
} from '../ui/primitives';
import { EmptyState, ErrorState, LoadingScreen } from '../ui/states';

const PERIODS = [
  { key: 'week', label: 'Неделя' },
  { key: 'month', label: 'Месяц' },
] as const;

const PAGE = 15;

export function History() {
  const [active, setActive] = useState<string>('month');
  const [days, setDays] = useState<Day[] | null>(null);
  const [timeZone, setTimeZone] = useState('UTC');
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const load = useCallback(
    async (offset: number) => {
      const result = await api.history({
        period: active,
        limit: PAGE,
        offset,
      });
      if (!result.ok) {
        setError(result.message);
        return;
      }
      setError(null);
      setHasMore(result.value.has_more);
      setTimeZone(result.value.period?.timezone ?? 'UTC');
      setDays((previous) =>
        offset === 0 ? result.value.days : [...(previous ?? []), ...result.value.days],
      );
    },
    [active],
  );

  useEffect(() => {
    let alive = true;
    setDays(null);
    setError(null);
    void load(0).finally(() => {
      if (!alive) return;
    });
    return () => {
      alive = false;
    };
  }, [load, attempt]);

  async function more() {
    setLoadingMore(true);
    await load(days?.length ?? 0);
    setLoadingMore(false);
  }

  return (
    <>
      <SectionHeader title="История" />

      <div className="segmented" role="group" aria-label="Период истории">
        {PERIODS.map((item) => (
          <button
            key={item.key}
            type="button"
            aria-pressed={active === item.key}
            onClick={() => setActive(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {error && (
        <ErrorState message={error} onRetry={() => setAttempt((n) => n + 1)} />
      )}
      {!days && !error && <LoadingScreen label="Загружаем историю" cards={3} />}

      {days?.length === 0 && (
        <EmptyState
          icon={<HistoryIcon size={28} />}
          title="Отметок нет"
          description="За выбранный период записей о входах и выходах не найдено."
        />
      )}

      {days?.map((day) => (
        <DayCard key={day.day} day={day} timeZone={timeZone} />
      ))}

      {hasMore && (
        <SecondaryButton onClick={() => void more()} disabled={loadingMore} wide>
          {loadingMore ? 'Загружаем…' : 'Показать ещё'}
        </SecondaryButton>
      )}
    </>
  );
}

export function DayCard({ day, timeZone }: { day: Day; timeZone: string }) {
  const sessions = day.sessions ?? [];

  return (
    <Card className="day-card">
      <header>
        <span className="day-date">{dayLabel(day.day)}</span>
        <span className="muted">{weekday(day.day)}</span>
        {day.seconds > 0 && (
          <span className="day-total">{duration(day.seconds)}</span>
        )}
      </header>

      {sessions.map((session) => (
        <Punch key={session.id} session={session} timeZone={timeZone} />
      ))}

      {day.absence_name && (
        <StatusBadge tone="warning">{day.absence_name}</StatusBadge>
      )}
      {day.missed && !day.absence_name && (
        <StatusBadge tone="danger" dot>
          Рабочий день без отметок
        </StatusBadge>
      )}
      {day.has_open_session && (
        <StatusBadge tone="navy" dot>
          Сессия не закрыта — время предварительное
        </StatusBadge>
      )}
    </Card>
  );
}

/** Один вход и парный к нему выход. Выход открытой сессии — прочерк. */
function Punch({
  session,
  timeZone,
}: {
  session: OpenSession;
  timeZone: string;
}) {
  return (
    <>
      <div className="punch">
        <span className="punch-time">{time(session.started_at, timeZone)}</span>
        <span className="punch-kind">вход</span>
        <span className="punch-place">
          {[session.office_name, session.entry_point_name]
            .filter(Boolean)
            .join(', ')}
        </span>
      </div>
      <div className="punch">
        <span className="punch-time">
          {session.is_open ? '—' : time(session.ended_at, timeZone)}
        </span>
        <span className="punch-kind">
          {session.is_open ? 'ещё в офисе' : 'выход'}
        </span>
        <span className="punch-place">
          {session.is_open ? '' : (session.exit_point_name ?? '')}
        </span>
      </div>
    </>
  );
}
