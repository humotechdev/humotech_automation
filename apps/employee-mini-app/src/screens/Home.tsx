/**
 * Главный экран: где человек сейчас и что у него сегодня.
 *
 * Открытая сессия и «часы сегодня» — разные строки, и сведены они здесь
 * не будут. В три часа ночи у зашедшего в 22:00 сегодняшних часов честно
 * ноль, а в офисе он пять часов; одно число вместо двух соврало бы
 * в одном из мест.
 *
 * Блоков «план» и «осталось» здесь нет, хотя по виду им самое место.
 * Плановых минут backend не отдаёт: в ответе есть только начало и конец
 * смены (09:00–18:00), а норма за день короче на обед, которого в API
 * тоже нет. Посчитать «осталось» как конец минус начало значило бы
 * поставить неверный знаменатель под экран, по которому считают рабочее
 * время, — та же ложь, что и выдуманное время выхода.
 *
 * Полоса показывает, сколько прошло от смены по часам офиса. Это
 * утверждение о времени суток, а не о человеке, и перепутать его
 * с выполнением нормы нельзя.
 */

import type { Profile, Status, Summary } from '../api';
import { duration, time } from '../format';
import { MedicalIcon, PlaneIcon, QrIcon } from '../ui/icons';
import { officeHour } from '../ui/AppHeader';
import {
  Card,
  MetricCard,
  ProgressBar,
  SectionHeader,
  StatusBadge,
} from '../ui/primitives';
import { StatusCard } from '../ui/StatusCard';

/**
 * Доля прошедшей смены по местным часам.
 *
 * Возвращает null, если смены на сегодня нет: полоса без графика
 * показывала бы долю от ничего.
 */
export function shiftProgress(
  status: Status,
  now: Date = new Date(),
): { elapsed: number; total: number } | null {
  if (!status.scheduled_start || !status.scheduled_end) return null;
  const start = minutesOf(status.scheduled_start);
  const end = minutesOf(status.scheduled_end);
  if (start === null || end === null || end <= start) return null;

  const local = officeMinutes(status.timezone, now);
  return {
    elapsed: Math.min(Math.max(local - start, 0), end - start),
    total: end - start,
  };
}

function minutesOf(value: string): number | null {
  const [hours, minutes] = value.split(':');
  const h = Number.parseInt(hours ?? '', 10);
  const m = Number.parseInt(minutes ?? '', 10);
  return Number.isNaN(h) || Number.isNaN(m) ? null : h * 60 + m;
}

function officeMinutes(timeZone: string, now: Date): number {
  const text = now.toLocaleString('ru-RU', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
  const [h, m] = text.split(':').map((part) => Number.parseInt(part, 10));
  if (Number.isNaN(h) || Number.isNaN(m)) return officeHour(timeZone, now) * 60;
  return h * 60 + m;
}

export function Home({
  profile,
  status,
  today,
  onScan,
  onSickLeave,
  onVacation,
  onHistory,
}: {
  profile: Profile;
  status: Status;
  /** Итог за сегодня. null — ещё грузится или не пришёл. */
  today: Summary | null;
  onScan: () => void;
  onSickLeave: () => void;
  onVacation: () => void;
  onHistory: () => void;
}) {
  const tz = status.timezone;
  const shift = shiftProgress(status);
  const sessions = today
    ? today.completed_sessions + today.open_sessions
    : null;

  return (
    <>
      <StatusCard status={status} />

      <section className="stack">
        <SectionHeader title="Сегодня" />
        <div className="metric-row">
          <MetricCard
            label="Отработано"
            value={duration(status.seconds_today)}
            strong
          />
          <MetricCard
            label="Смена"
            value={
              status.scheduled_start && status.scheduled_end
                ? `${status.scheduled_start.slice(0, 5)}–${status.scheduled_end.slice(0, 5)}`
                : '—'
            }
          />
          <MetricCard
            label="Отметок"
            value={sessions === null ? '—' : sessions}
          />
        </div>

        {shift && (
          <Card>
            <div className="section-header">
              <h3>Рабочий день</h3>
              <span className="muted">
                {duration(shift.elapsed * 60)} из {duration(shift.total * 60)}
              </span>
            </div>
            <ProgressBar
              value={shift.elapsed}
              max={shift.total}
              label={`Прошло ${duration(shift.elapsed * 60)} из смены ${duration(
                shift.total * 60,
              )}`}
            />
            <p className="muted">
              Прошло от смены {status.scheduled_start?.slice(0, 5)}–
              {status.scheduled_end?.slice(0, 5)} по времени офиса.
            </p>
          </Card>
        )}
      </section>

      <section className="stack">
        <SectionHeader title="Быстрые действия" />
        <div className="quick-actions">
          <button type="button" className="quick-action" onClick={onScan}>
            <QrIcon size={22} />
            <span>Сканировать QR</span>
          </button>
          <button type="button" className="quick-action" onClick={onSickLeave}>
            <MedicalIcon size={22} />
            <span>Больничный</span>
          </button>
          <button type="button" className="quick-action" onClick={onVacation}>
            <PlaneIcon size={22} />
            <span>Отпуск</span>
          </button>
        </div>
      </section>

      <section className="stack">
        <SectionHeader title="Последняя отметка" />
        <Card>
          {status.last_entry_at || status.last_exit_at ? (
            <LastPunch status={status} onHistory={onHistory} />
          ) : (
            <p className="muted">Отметок пока нет.</p>
          )}
        </Card>
      </section>

      <p className="muted">
        {profile.office.name}
        {profile.department?.name ? ` · ${profile.department.name}` : ''} ·
        часовой пояс {tz}
      </p>
    </>
  );
}

/**
 * Последняя отметка: вход или выход.
 *
 * Что было последним, определяется по времени, а не по состоянию:
 * состояние может быть «выходной», а последняя отметка — вчерашний вход.
 */
function LastPunch({
  status,
  onHistory,
}: {
  status: Status;
  onHistory: () => void;
}) {
  const entry = status.last_entry_at ? Date.parse(status.last_entry_at) : 0;
  const exit = status.last_exit_at ? Date.parse(status.last_exit_at) : 0;
  const isEntry = entry >= exit;
  const at = isEntry ? status.last_entry_at : status.last_exit_at;
  const open = status.open_session;

  // Офис и точка прохода известны только для открытой сессии: в ответе
  // `/me/status` у закрытой их нет вовсе. Подставить сюда офис из профиля
  // нельзя — он говорит, где человек числится, а не где он приложил
  // пропуск. Поэтому строка либо настоящая, либо её нет.
  const place =
    isEntry && open
      ? [open.office_name, open.entry_point_name].filter(Boolean).join(', ')
      : '';

  return (
    <>
      <div className="punch">
        <span className="punch-time">{time(at, status.timezone)}</span>
        <span className="punch-kind">{isEntry ? 'вход' : 'выход'}</span>
        {place && <span className="punch-place">{place}</span>}
      </div>
      <div className="section-header">
        {/* Отметка есть в базе — значит, сервер её принял: неудачное
            сканирование записью не становится. Отдельного поля
            подтверждения в ответе нет, и придумывать его нечему. */}
        <StatusBadge tone="success" dot>
          Подтверждена
        </StatusBadge>
        <button type="button" className="link-button" onClick={onHistory}>
          Вся история
        </button>
      </div>
    </>
  );
}
