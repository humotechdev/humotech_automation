/*
 * Вкладка «Обзор» карточки сотрудника (вариант B): «Сегодня» со шкалой
 * рабочего дня, «Эта неделя», последние заявки и последние изменения.
 *
 * Всё — из тех же запросов, что и раньше: посещаемость по дням, сессии
 * дня, очередь заявок, журнал аудита. Норма часов — из графика
 * сотрудника (`current_schedule`): время начала и конца дня, без
 * перерывов — их в карточке нет.
 */
import { type ReactNode } from 'react';

import * as api from '../../api/crm';
import { Isx } from '../../components/Isx';
import { useBlock, type Block } from '../dashboard/data';
import { certificateOf, kindOf, statusOf } from '../requests/model';
import { clock, dayInZone } from '../time/zone';
import {
  auditTitle, dayMonth, dayMonthYear, dayRange, monthIn, monthStartOf, plural, todayIn,
} from './card-model';
import { duration, type Tab } from './model';

export type OverviewRights = {
  attendance: boolean; absences: boolean; audit: boolean; manual: boolean;
};

export type WorkPlan = {
  weekdays?: number[]; start_time?: string | null; end_time?: string | null;
} | null | undefined;

type Go = (tab: Tab, extra?: Record<string, string>) => void;

const WEEKDAY_LONG = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];
const WEEKDAY_SHORT = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

/** «09:00:00» → 540. */
function minutesOf(time: string | null | undefined): number | null {
  const match = /^(\d{1,2}):(\d{2})/.exec(time ?? '');
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

/** Момент ISO → минуты от полуночи в поясе офиса. */
function minutesAt(at: string, zone: string): number | null {
  return minutesOf(clock(at, zone));
}

function hhmm(minutes: number): string {
  const m = ((Math.round(minutes) % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
}

/** Длина рабочего дня по графику в минутах; `null` — графика нет. */
export function planMinutes(plan: WorkPlan): number | null {
  const start = minutesOf(plan?.start_time);
  const end = minutesOf(plan?.end_time);
  if (start === null || end === null) return null;
  const span = end > start ? end - start : end + 1440 - start;
  return span > 0 ? span : null;
}

/** «8 ч», «7,5 ч». */
function hours(minutes: number): string {
  const value = Math.round((minutes / 60) * 10) / 10;
  return `${String(value).replace('.', ',')} ч`;
}

function weekdayOf(day: string): number {
  return new Date(`${day}T12:00:00Z`).getUTCDay();
}

function shift(day: string, by: number): string {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + by);
  return date.toISOString().slice(0, 10);
}

/** Стаж словами: «17 дней», «3 месяца», «1 год 2 месяца». */
export function tenure(hire: string | null, today: string): string | null {
  if (!hire || hire > today) return null;
  const [y1, m1, d1] = hire.split('-').map(Number) as [number, number, number];
  const [y2, m2, d2] = today.split('-').map(Number) as [number, number, number];
  let months = (y2 - y1) * 12 + (m2 - m1) - (d2 < d1 ? 1 : 0);
  if (months < 1) {
    const days = Math.round((Date.parse(`${today}T00:00:00Z`) - Date.parse(`${hire}T00:00:00Z`)) / 86_400_000);
    return `${days} ${plural(days, 'день', 'дня', 'дней')}`;
  }
  const years = Math.floor(months / 12);
  months %= 12;
  const parts = [];
  if (years) parts.push(`${years} ${plural(years, 'год', 'года', 'лет')}`);
  if (months) parts.push(`${months} ${plural(months, 'месяц', 'месяца', 'месяцев')}`);
  return parts.join(' ');
}

/** Период и справка заявки одной строкой. */
export function noticeLine(item: api.QueueItem): string {
  const absence = item.absence;
  const days = absence?.first_day
    ? dayRange(absence.first_day, absence.last_day ?? absence.first_day) : '';
  const paper = absence?.requires_document
    ? (certificateOf(item) ? 'справка приложена' : 'справки нет') : '';
  return [days, paper].filter(Boolean).join(' · ') || 'Подробности — на вкладке заявок';
}

const TONE: Record<string, string> = { orange: 'amber', amber: 'amber', green: 'green', red: 'red', blue: 'blue', grey: 'grey' };

// --- каркас секции -------------------------------------------------------------

function Section({ title, aside, children, label }: {
  title: string; aside?: ReactNode; children: ReactNode; label: string;
}) {
  return (
    <section className="pf-sec" aria-labelledby={label}>
      <header className="pf-sec__head">
        <h2 id={label} className="pf-sec__title">{title}</h2>
        {aside}
      </header>
      {children}
    </section>
  );
}

function More({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button type="button" className="pf-more" onClick={onClick}>
      {children}
      <Isx name="next" size={14} />
    </button>
  );
}

function Failed({ onRetry, children = 'Не удалось получить данные.' }: { onRetry: () => void; children?: ReactNode }) {
  return (
    <p className="pf-failed" role="alert">
      {children}{' '}
      <button type="button" className="pf-link" onClick={onRetry}>Повторить</button>
    </p>
  );
}

// --- вкладка целиком -----------------------------------------------------------

export function ProfileOverview({ id, zone, plan, rights, version, onGo }: {
  id: string; zone: string; plan: WorkPlan; rights: OverviewRights; version: number; onGo: Go;
}) {
  const today = todayIn(zone);
  const first = monthStartOf(today);
  const monday = shift(today, -((weekdayOf(today) + 6) % 7));

  const [journal, reloadJournal] = useBlock(
    (signal) => api.attendanceDaily({ employee_id: id, date_from: first, date_to: today }, signal),
    `overview-journal|${id}|${first}|${today}|${version}`,
    rights.attendance,
  );
  // Неделя целиком внутри месяца — тот же ответ; на стыке месяцев — свой.
  const ownWeek = monday < first;
  const [weekOwn, reloadWeek] = useBlock(
    (signal) => api.attendanceDaily({ employee_id: id, date_from: monday, date_to: today }, signal),
    `overview-week|${id}|${monday}|${today}|${version}`,
    rights.attendance && ownWeek,
  );
  const [sessions] = useBlock(
    (signal) => api.attendanceSessions({ employee_id: id, date_from: today, date_to: today, limit: '20' }, signal),
    `overview-sessions|${id}|${today}|${version}`,
    rights.attendance,
  );
  const [counts, reloadCounts] = useBlock(
    (signal) => api.queueCounts({ employee_id: id }, signal),
    `overview-counts|${id}|${version}`,
    rights.absences,
  );
  const [waiting] = useBlock(
    (signal) => api.queue({ employee_id: id, status: 'SUBMITTED,IN_REVIEW', limit: '1' }, signal),
    `overview-waiting|${id}|${version}`,
    rights.absences,
  );
  const [recent, reloadRecent] = useBlock(
    (signal) => api.queue({ employee_id: id, limit: '3' }, signal),
    `overview-recent|${id}|${version}`,
    rights.absences,
  );
  const [changes, reloadChanges] = useBlock(
    (signal) => api.auditLogs({ employee_id: id, limit: '3' }, signal),
    `overview-changes|${id}|${version}`,
    rights.audit,
  );

  const week = ownWeek ? weekOwn : journal;
  const open = counts.state === 'ready' ? counts.data['open'] ?? null : null;
  const pending = waiting.state === 'ready' ? waiting.data.items[0] ?? null : null;

  return (
    <div className="pf-overview">
      <TodayCard zone={zone} today={today} plan={plan} rights={rights} journal={journal}
                 sessions={sessions} open={open} countsState={counts.state}
                 onRetry={reloadJournal} onRetryCounts={reloadCounts} onGo={onGo} />
      <WeekBars today={today} monday={monday} plan={plan} allowed={rights.attendance}
                block={week} onRetry={ownWeek ? reloadWeek : reloadJournal} />
      <RecentRequests allowed={rights.absences} block={recent} pending={pending}
                      onRetry={reloadRecent} onGo={onGo} />
      <RecentChanges allowed={rights.audit} block={changes} zone={zone}
                     onRetry={reloadChanges} onGo={onGo} />
    </div>
  );
}

// --- «Сегодня» -----------------------------------------------------------------

function TodayCard({ zone, today, plan, rights, journal, sessions, open, countsState, onRetry, onRetryCounts, onGo }: {
  zone: string; today: string; plan: WorkPlan; rights: OverviewRights;
  journal: Block<api.DailyReport>; sessions: Block<api.Cursored<api.SessionRow>>;
  open: number | null; countsState: string;
  onRetry: () => void; onRetryCounts: () => void; onGo: Go;
}) {
  const report = journal.state === 'ready' ? journal.data : null;
  const row = report?.days.find((one) => one.day === today) ?? null;
  const unplaced = Boolean(report?.note) && (report?.days.length ?? 0) === 0;
  const start = minutesOf(plan?.start_time);
  const end = minutesOf(plan?.end_time);
  const daily = planMinutes(plan);
  const working = (plan?.weekdays ?? []).includes(((weekdayOf(today) + 6) % 7) + 1);
  const now = minutesAt(new Date().toISOString(), zone);
  const title = `Сегодня, ${WEEKDAY_LONG[weekdayOf(today)]} ${dayMonth(today)}`;
  const monthNorm = daily !== null && report ? report.totals.working_days * daily : null;

  return (
    <Section label="pf-today" title={title}
             aside={rights.attendance && <More onClick={() => onGo('attendance')}>Вся посещаемость</More>}>
      {rights.attendance && (
        <DayStatus journal={journal} row={row} zone={zone} start={start} now={now}
                   working={working} unplaced={unplaced} note={report?.note ?? null}
                   canMark={rights.manual} onRetry={onRetry}
                   onMark={() => onGo('attendance', { day: today })} />
      )}

      {rights.attendance && start !== null && end !== null && working && !unplaced && (
        <DayScale start={start} end={end} now={now} zone={zone}
                  sessions={sessions.state === 'ready' ? sessions.data.items : []} />
      )}

      <div className="pf-stats" aria-label="Показатели сотрудника">
        <Stat title="Отработано сегодня"
              value={!rights.attendance ? '—'
                : journal.state === 'loading' ? null
                : row && !unplaced ? duration(row.seconds) : '—'}
              of={daily !== null && working ? `из ${hours(daily)}` : ''}
              note={!rights.attendance ? 'Нет права на посещаемость'
                : journal.state === 'error' ? 'Не удалось получить' : ''} />
        <Stat title={`${monthTitle(monthIn(today))}, ${dayRange(monthStartOf(today), today)}`}
              value={!rights.attendance ? '—'
                : journal.state === 'loading' ? null
                // Без назначения за период сервер отдаёт нули с пояснением:
                // это «не с чем считать», а не подтверждённые «0 ч».
                : report && unplaced ? '—'
                : report ? duration(report.totals.seconds) : '—'}
              of={monthNorm !== null && !unplaced ? `из ${hours(monthNorm)}` : ''}
              note={!rights.attendance ? 'Нет права на посещаемость'
                : journal.state === 'error' ? 'Не удалось получить'
                : unplaced ? 'Нет назначения за этот период' : ''}
              onRetry={journal.state === 'error' ? onRetry : undefined} />
        <Stat title="Ждут решения"
              value={!rights.absences ? '—'
                : countsState === 'loading' ? null
                : open !== null ? `${open} ${plural(open, 'заявка', 'заявки', 'заявок')}` : '—'}
              of=""
              note={!rights.absences ? 'Нет права на заявки'
                : countsState === 'error' ? 'Не удалось получить' : ''}
              onRetry={countsState === 'error' ? onRetryCounts : undefined} />
      </div>
    </Section>
  );
}

/** «в октябре» / «октябрь» → «Октябрь». */
function monthTitle(text: string): string {
  const word = text.replace(/^в\s+/, '');
  const base = word.endsWith('е') ? `${word.slice(0, -1)}ь` : word;
  return base.charAt(0).toUpperCase() + base.slice(1);
}

function Stat({ title, value, of, note, onRetry }: {
  title: string; value: string | null; of: string; note: string; onRetry?: (() => void) | undefined;
}) {
  return (
    <div className="pf-stat">
      <span className="pf-stat__title">{title}</span>
      {value === null ? (
        <i className="pf-sk pf-sk--num" aria-hidden="true" />
      ) : (
        <span className="pf-stat__line">
          <strong className="pf-stat__value">{value}</strong>
          {of && value !== '—' && <small className="pf-stat__of">{of}</small>}
        </span>
      )}
      {note && <span className="pf-stat__note">{note}</span>}
      {onRetry && <button type="button" className="pf-link" onClick={onRetry}>Повторить</button>}
    </div>
  );
}

function DayStatus({ journal, row, zone, start, now, working, unplaced, note, canMark, onRetry, onMark }: {
  journal: Block<api.DailyReport>; row: api.DailyRow | null; zone: string;
  start: number | null; now: number | null; working: boolean; unplaced: boolean; note: string | null;
  canMark: boolean; onRetry: () => void; onMark: () => void;
}) {
  if (journal.state === 'loading') return <i className="pf-sk pf-sk--plate" aria-hidden="true" />;
  if (journal.state === 'error') return <Failed onRetry={onRetry}>Не удалось получить отметки за сегодня.</Failed>;
  if (journal.state === 'denied') return null;

  const begins = start !== null ? hhmm(start) : null;
  let tone: 'ok' | 'warn' | 'plain' = 'plain';
  let icon: 'check' | 'alert' | 'clock' = 'clock';
  let head = '';
  let text = '';
  let action = false;

  if (unplaced) {
    head = 'Нет назначения за этот период.';
    text = note ?? '';
  } else if (row?.state === 'IN_OFFICE' && row.first_entry_at) {
    tone = 'ok'; icon = 'check';
    head = 'На месте.';
    text = `Приход в ${clock(row.first_entry_at, zone)}${row.office_name ? `, ${row.office_name}` : ''}.`;
  } else if (row?.state === 'LEFT') {
    tone = 'ok'; icon = 'check';
    head = 'День завершён.';
    text = `Приход в ${clock(row.first_entry_at, zone)}, уход в ${clock(row.last_exit_at, zone)}.`;
  } else if (row && ['SICK_LEAVE', 'VACATION', 'OTHER_ABSENCE'].includes(row.state)) {
    head = row.absence_name ? `${row.absence_name}.` : 'Отсутствует.';
    text = 'Отметок сегодня не ждём.';
  } else if (!working || row?.state === 'DAY_OFF') {
    head = 'Выходной по графику.';
  } else if (row?.state === 'NO_SCHEDULE') {
    head = 'Графика нет.';
    text = 'Отметки не с чем сравнить.';
  } else if (begins && now !== null && start !== null && now < start) {
    head = `Рабочий день начнётся в ${begins}.`;
  } else {
    tone = 'warn'; icon = 'alert'; action = canMark;
    head = 'Нет отметки о приходе.';
    text = begins ? `Рабочий день начался в ${begins}.` : '';
  }

  return (
    <div className={`pf-plate pf-plate--${tone}`} role="status">
      <Isx name={icon} size={20} className="pf-plate__icon" />
      <p className="pf-plate__text"><b>{head}</b>{text && <> {text}</>}</p>
      {action && (
        <button type="button" className="pf-btn pf-btn--line pf-btn--sm" onClick={onMark}>
          Отметить вручную
        </button>
      )}
    </div>
  );
}

function DayScale({ start, end, now, zone, sessions }: {
  start: number; end: number; now: number | null; zone: string; sessions: api.SessionRow[];
}) {
  const stop = end > start ? end : end + 1440;
  const span = stop - start;
  const at = (m: number) => Math.min(100, Math.max(0, ((m - start) / span) * 100));
  const marks = [0, 0.25, 0.5, 0.75, 1].map((part) => start + span * part);
  const pieces = sessions
    .map((one) => {
      const from = minutesAt(one.started_at, zone);
      const to = one.ended_at ? minutesAt(one.ended_at, zone) : now;
      if (from === null || to === null || to <= start || from >= stop) return null;
      return { id: one.id, from: Math.max(from, start), to: Math.min(to, stop) };
    })
    .filter((one): one is { id: string; from: number; to: number } => one !== null && one.to > one.from);
  const inside = now !== null && now >= start && now <= stop;

  return (
    <div className="pf-scale" role="img"
         aria-label={`Рабочий день по графику с ${hhmm(start)} до ${hhmm(end)}`}>
      <div className="pf-scale__track">
        {pieces.map((one) => (
          <span key={one.id} className="pf-scale__done"
                style={{ left: `${at(one.from)}%`, width: `${at(one.to) - at(one.from)}%` }} />
        ))}
        {inside && now !== null && (
          <span className="pf-scale__now" style={{ left: `${at(now)}%` }}>
            <span className="pf-scale__time">{hhmm(now)}</span>
          </span>
        )}
      </div>
      <div className="pf-scale__marks" aria-hidden="true">
        {marks.map((m) => <span key={m}>{hhmm(m)}</span>)}
      </div>
    </div>
  );
}

// --- «Эта неделя» --------------------------------------------------------------

function WeekBars({ today, monday, plan, allowed, block, onRetry }: {
  today: string; monday: string; plan: WorkPlan; allowed: boolean;
  block: Block<api.DailyReport>; onRetry: () => void;
}) {
  const daily = planMinutes(plan);
  const workdays = plan?.weekdays?.length ? plan.weekdays : [1, 2, 3, 4, 5];
  const days = [0, 1, 2, 3, 4, 5, 6]
    .map((n) => shift(monday, n))
    .filter((day) => workdays.includes(((weekdayOf(day) + 6) % 7) + 1));
  const norm = daily !== null ? `Норма ${hours(daily * workdays.length)}` : 'Норма —';
  const rows = block.state === 'ready' ? block.data.days : [];
  const unplaced = block.state === 'ready' && Boolean(block.data.note) && rows.length === 0;

  return (
    <Section label="pf-week" title="Эта неделя" aside={<span className="pf-sec__aside">{norm}</span>}>
      {!allowed ? (
        <p className="pf-quiet">Нет права на посещаемость.</p>
      ) : block.state === 'error' ? (
        <Failed onRetry={onRetry} />
      ) : (
        <ul className="pf-week">
          {days.map((day) => {
            const row = rows.find((one) => one.day === day);
            const future = day > today;
            const done = row && !unplaced && !future ? row.seconds / 60 : null;
            const part = done !== null && daily ? Math.min(100, (done / daily) * 100) : 0;
            const label = `${done === null ? '—' : hours(done).replace(' ч', '')} / ${daily !== null ? hours(daily) : '—'}`;
            return (
              <li key={day} className={day === today ? 'pf-week__row pf-week__row--today' : 'pf-week__row'}>
                <span className="pf-week__day">
                  {WEEKDAY_SHORT[weekdayOf(day)]} {Number(day.slice(8))}
                  {day === today && <span className="visually-hidden"> (сегодня)</span>}
                </span>
                {block.state === 'loading' ? (
                  <i className="pf-sk pf-sk--bar" aria-hidden="true" />
                ) : (
                  <span className="pf-week__bar" aria-hidden="true">
                    <span style={{ width: `${part}%` }} />
                  </span>
                )}
                <span className="pf-week__num">{label}</span>
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}

// --- «Заявки и документы» ------------------------------------------------------

function RecentRequests({ allowed, block, pending, onRetry, onGo }: {
  allowed: boolean; block: Block<api.Cursored<api.QueueItem>>; pending: api.QueueItem | null;
  onRetry: () => void; onGo: Go;
}) {
  return (
    <Section label="pf-requests" title="Заявки и документы"
             aside={allowed && <More onClick={() => onGo('requests')}>Все заявки</More>}>
      {pending && (
        <div className="pf-plate pf-plate--warn">
          <Isx name="doc" size={20} className="pf-plate__icon" />
          <p className="pf-plate__text">
            <b>{kindOf(pending).title} ожидает решения</b> {noticeLine(pending)}
          </p>
          <button type="button" className="pf-btn pf-btn--primary pf-btn--sm"
                  onClick={() => onGo('requests', { status: 'open', request: pending.id })}>
            Рассмотреть
          </button>
        </div>
      )}
      {!allowed ? (
        <p className="pf-quiet">Нет права на заявки.</p>
      ) : block.state === 'loading' ? (
        <div aria-hidden="true">{[0, 1].map((n) => <i key={n} className="pf-sk pf-sk--row" />)}</div>
      ) : block.state === 'error' ? (
        <Failed onRetry={onRetry} />
      ) : block.state === 'ready' && block.data.items.length > 0 ? (
        <ul className="pf-list">
          {block.data.items.map((item) => {
            const status = statusOf(item);
            return (
              <li key={item.id}>
                <button type="button" className="pf-list__row"
                        onClick={() => onGo('requests', { request: item.id })}>
                  <span className="pf-list__icon" aria-hidden="true"><Isx name="doc" size={18} /></span>
                  <span className="pf-list__text">
                    <b>{kindOf(item).title}</b>
                    <small>{noticeLine(item)}</small>
                  </span>
                  <span className={`pf-pill pf-pill--${TONE[status.tone] ?? 'grey'}`}>{status.title}</span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="pf-empty">
          <span className="pf-empty__icon" aria-hidden="true"><Isx name="doc" size={20} /></span>
          <span className="pf-empty__text">
            <b>Заявок нет</b>
            <small>Отпуска, отгулы и справки сотрудника появятся здесь</small>
          </span>
        </div>
      )}
    </Section>
  );
}

// --- «Последние изменения» -----------------------------------------------------

function RecentChanges({ allowed, block, zone, onRetry, onGo }: {
  allowed: boolean; block: Block<api.Cursored<api.AuditEntry>>; zone: string;
  onRetry: () => void; onGo: Go;
}) {
  return (
    <Section label="pf-changes" title="Последние изменения"
             aside={allowed && <More onClick={() => onGo('history')}>Вся история</More>}>
      {!allowed ? (
        <p className="pf-quiet">Нет права на историю изменений.</p>
      ) : block.state === 'loading' ? (
        <div aria-hidden="true">{[0, 1].map((n) => <i key={n} className="pf-sk pf-sk--row" />)}</div>
      ) : block.state === 'error' ? (
        <Failed onRetry={onRetry} />
      ) : block.state === 'ready' && block.data.items.length > 0 ? (
        <ol className="pf-feed">
          {block.data.items.map((row) => {
            const extra = row as api.AuditEntry & { actor_name?: string | null };
            const who = extra.actor_name ?? row.actor_email;
            return (
              <li key={row.id} className="pf-feed__item">
                <span className="pf-feed__dot" aria-hidden="true" />
                <b>{auditTitle(row.action)}</b>
                <small>
                  {[dayMonthYear(dayInZone(row.occurred_at, zone)), clock(row.occurred_at, zone), who]
                    .filter(Boolean).join(' · ')}
                </small>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="pf-quiet">Изменений пока нет.</p>
      )}
    </Section>
  );
}
