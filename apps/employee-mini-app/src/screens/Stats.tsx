/**
 * Статистика за сегодня, неделю и месяц.
 *
 * Ни одна цифра здесь не считается — всё приходит с сервера. «Рабочих
 * дней: график не назначен» — это null, а не ноль: ноль рабочих дней при
 * нуле пропусков читался бы как безупречная посещаемость.
 *
 * Процента выполнения плана нет, хотя по виду ему самое место. Плановых
 * часов за период backend не отдаёт: в `Summary` есть число рабочих дней,
 * но не длительность рабочего дня. Умножить одно на другое значило бы
 * предположить, что все дни одинаковы, и поставить выдуманный знаменатель
 * под процент на экране, по которому считают рабочее время.
 *
 * Столбцы — факт по дням, и только факт. Плановой высоты у столбца нет
 * по той же причине.
 */

import { useEffect, useState } from 'react';

import { api, type Day, type Summary } from '../api';
import { dayLabel, duration, period } from '../format';
import {
  Card,
  MetricCard,
  SectionHeader,
  StatusBadge,
} from '../ui/primitives';
import { ErrorState, LoadingScreen } from '../ui/states';

const PERIODS = [
  { key: 'today', label: 'Сегодня' },
  { key: 'week', label: 'Неделя' },
  { key: 'month', label: 'Месяц' },
] as const;

export function Stats() {
  const [active, setActive] = useState<string>('week');
  const [data, setData] = useState<{ summary: Summary; days: Day[] } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    void api.statistics({ period: active }).then((result) => {
      if (!alive) return;
      if (result.ok) setData(result.value);
      else setError(result.message);
    });
    return () => {
      alive = false;
    };
  }, [active, attempt]);

  return (
    <>
      <SectionHeader title="Статистика" />

      <div className="segmented" role="group" aria-label="Период">
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
      {!data && !error && <LoadingScreen label="Считаем статистику" cards={2} />}

      {data && (
        <>
          <Card>
            <p className="muted">{period(data.summary.first, data.summary.last)}</p>
            <p className="status-duration status-duration-light">
              {duration(data.summary.seconds)}
            </p>
            <p className="muted">
              {data.summary.completed_sessions} завершённых сессий
              {data.summary.open_sessions > 0 &&
                `, ${data.summary.open_sessions} открытых`}
            </p>
            {data.summary.open_sessions > 0 && (
              <StatusBadge tone="warning" dot>
                Есть незакрытая сессия — время предварительное
              </StatusBadge>
            )}
          </Card>

          <DayChart days={data.days} />

          <section className="stack">
            <SectionHeader title="Дни периода" />
            <div className="metric-row">
              <MetricCard
                label="Рабочих"
                value={
                  data.summary.has_schedule
                    ? (data.summary.working_days ?? '—')
                    : '—'
                }
                hint={data.summary.has_schedule ? undefined : 'нет графика'}
              />
              <MetricCard label="С отметками" value={data.summary.attended_days} />
              <MetricCard
                label="Пропущено"
                value={
                  data.summary.has_schedule
                    ? (data.summary.missed_days ?? '—')
                    : '—'
                }
              />
            </div>

            {(data.summary.sick_leave_days > 0 ||
              data.summary.vacation_days > 0 ||
              data.summary.other_absence_days > 0) && (
              <div className="metric-row">
                <MetricCard
                  label="Больничный"
                  value={`${data.summary.sick_leave_days} дн.`}
                />
                <MetricCard
                  label="Отпуск"
                  value={`${data.summary.vacation_days} дн.`}
                />
                <MetricCard
                  label="Прочее"
                  value={`${data.summary.other_absence_days} дн.`}
                />
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}

/**
 * Столбцы по дням.
 *
 * Высота — фактические секунды, приведённые к самому длинному дню
 * периода. Два цвета, как и требуется: тёмно-синий — отработанное,
 * светлый — рабочий день без отметок. Третьего значения у столбца нет.
 */
export function DayChart({ days }: { days: Day[] }) {
  const longest = Math.max(...days.map((day) => day.seconds), 1);
  const worked = days.filter((day) => day.seconds > 0);
  if (!worked.length) {
    return (
      <Card>
        <SectionHeader title="По дням" />
        <p className="muted">За этот период отметок нет.</p>
      </Card>
    );
  }

  return (
    <Card>
      <SectionHeader title="По дням" />
      <div className="chart" role="img" aria-label={chartSummary(days)}>
        {days.map((day) => {
          const share = day.seconds / longest;
          const missed = day.seconds === 0 && day.missed;
          return (
            <div key={day.day} className="chart-day" title={dayTitle(day)}>
              <div
                className={`chart-bar${
                  day.seconds === 0
                    ? missed
                      ? ' chart-bar-missed'
                      : ' chart-bar-empty'
                    : ''
                }`}
                style={{
                  height: day.seconds ? `${Math.max(share * 100, 4)}%` : '4px',
                }}
              />
            </div>
          );
        })}
      </div>
      <div className="chart-legend">
        <span>
          <i className="legend-swatch" style={{ background: 'var(--navy)' }} />
          отработано
        </span>
        <span>
          <i
            className="legend-swatch"
            style={{ background: 'var(--danger-bg)' }}
          />
          рабочий день без отметок
        </span>
      </div>
    </Card>
  );
}

function dayTitle(day: Day): string {
  return `${dayLabel(day.day)}: ${
    day.seconds ? duration(day.seconds) : day.missed ? 'без отметок' : '—'
  }`;
}

/** Текстовая замена графика для экранного диктора. */
function chartSummary(days: Day[]): string {
  const worked = days.filter((day) => day.seconds > 0);
  const total = worked.reduce((sum, day) => sum + day.seconds, 0);
  return `Отметки за ${worked.length} дней, всего ${duration(total)}`;
}
