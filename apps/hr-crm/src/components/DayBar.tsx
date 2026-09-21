/**
 * Шкала рабочего дня: назначенный график серым, присутствие зелёным.
 *
 * Рисуется по ОТРЕЗКАМ сессий, а не по первому входу и последнему
 * выходу. Разница не косметическая: между ними бывает обед, и сплошная
 * полоса утверждала бы, что человек не выходил. Разрыв на шкале —
 * это и есть выход.
 *
 * Ни одного правила учёта здесь нет: длительности, границы суток и
 * опоздание считает сервер. Компонент переводит готовые времена в
 * проценты ширины.
 *
 * Для отпуска, больничного и выходного шкалы нет вовсе — вместо неё
 * подпись. Пустая полоса в такой день читалась бы как прогул.
 */

import type { PresenceRow } from '../api/crm';

/** Минуты от полуночи в поясе офиса. `null` — момента нет. */
function minutes(at: string | null, zone: string): number | null {
  if (!at) return null;
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return null;
  const text = new Intl.DateTimeFormat('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    ...(zone ? { timeZone: zone } : {}),
  }).format(date);
  const [h, m] = text.split(':').map((part) => Number.parseInt(part, 10));
  if (Number.isNaN(h as number) || Number.isNaN(m as number)) return null;
  return (h as number) * 60 + (m as number);
}

/** «09:06» из минут. */
export function atClock(value: number | null): string {
  if (value === null) return '—';
  const whole = Math.max(0, Math.round(value));
  return `${String(Math.floor(whole / 60) % 24).padStart(2, '0')}:${String(
    whole % 60,
  ).padStart(2, '0')}`;
}

const fromTime = (value: string | null): number | null => {
  if (!value) return null;
  const [h, m] = value.split(':').map((part) => Number.parseInt(part, 10));
  if (Number.isNaN(h as number) || Number.isNaN(m as number)) return null;
  return (h as number) * 60 + (m as number);
};

/** Состояния, в которые шкалу не рисуют: работы в этот день не было. */
const LABELS: Record<string, string> = {
  DAY_OFF: 'Выходной',
  NO_SCHEDULE: 'График не назначен',
  VACATION: 'В отпуске',
  SICK_LEAVE: 'На больничном',
  OTHER_ABSENCE: 'Отсутствие',
};

export function DayBar({
  row,
  zone,
  big = false,
}: {
  row: PresenceRow;
  zone: string;
  /** Увеличенная шкала для карточки выбранного сотрудника. */
  big?: boolean;
}) {
  const label = LABELS[row.state];
  if (label) {
    return <span className={`day-bar day-bar--off${big ? ' day-bar--big' : ''}`}>{label}</span>;
  }

  const planStart = fromTime(row.scheduled_start);
  const planEnd = fromTime(row.scheduled_end);

  // Тело без отрезков не должно ронять таблицу: шкала — одна колонка,
  // а не вся страница.
  const parts = (row.intervals ?? [])
    .map((one) => ({
      from: minutes(one.started_at, zone),
      to: one.ended_at ? minutes(one.ended_at, zone) : null,
      open: one.ended_at === null,
    }))
    .filter((one): one is { from: number; to: number | null; open: boolean } =>
      one.from !== null,
    );

  // Окно шкалы: график плюс всё, что вышло за его пределы. Обрезать
  // ранний приход или поздний уход значило бы спрятать именно то,
  // на что смотрят.
  const marks = [
    ...(planStart !== null ? [planStart] : []),
    ...(planEnd !== null ? [planEnd] : []),
    ...parts.map((one) => one.from),
    ...parts.map((one) => one.to).filter((one): one is number => one !== null),
  ];
  const low = marks.length ? Math.min(...marks) - 30 : 8 * 60;
  const high = marks.length ? Math.max(...marks) + 30 : 19 * 60;
  const span = Math.max(1, high - low);
  const at = (value: number) => ((value - low) / span) * 100;

  if (parts.length === 0) {
    return (
      <span className={`day-bar${big ? ' day-bar--big' : ''}`}>
        {/* Пунктир, а не пустая полоса: у дня без отметок график всё
            равно есть, и видно, что человек в него не попал. */}
        <span className="day-bar__track day-bar__track--empty" />
        <span className="day-bar__times">
          <span className="muted">Отметок нет</span>
        </span>
      </span>
    );
  }

  const late = (row.late_minutes ?? 0) > 0;
  const open = parts.some((one) => one.open);

  return (
    <span className={`day-bar${big ? ' day-bar--big' : ''}`}>
      <span className="day-bar__track">
        {planStart !== null && planEnd !== null && (
          <span
            className="day-bar__plan"
            style={{ left: `${at(planStart)}%`, width: `${at(planEnd) - at(planStart)}%` }}
          />
        )}

        {parts.map((one, index) => (
          <span
            key={index}
            className={`day-bar__run${one.open ? ' day-bar__run--open' : ''}`}
            style={{
              left: `${at(one.from)}%`,
              // У открытой сессии правого края нет: её конец — «сейчас»,
              // и дорисовывать его временем браузера нельзя.
              width: `${Math.max(at(one.to ?? high - 30) - at(one.from), 1.5)}%`,
            }}
          />
        ))}

        {late && parts[0] && (
          <span className="day-bar__late" style={{ left: `${at(parts[0].from)}%` }} />
        )}
        {open && parts[parts.length - 1] && (
          <span className="day-bar__now" style={{ left: `${at(high - 30)}%` }} />
        )}
      </span>

      <span className="day-bar__times">
        {parts.map((one, index) => (
          <span key={index} className="day-bar__time">
            {atClock(one.from)}
            {one.open ? ' — сейчас' : ` — ${atClock(one.to)}`}
          </span>
        ))}
      </span>
    </span>
  );
}
