/**
 * Счёт для главного экрана: что именно показать и каким цветом.
 *
 * Модуль намеренно без React и без DOM — здесь только разбор ответов
 * сервера. Поэтому все состояния экрана проверяются без браузера,
 * а сами карточки остаются тонкими.
 *
 * Ничего не досчитывается за сервер. Ни направления отметки, ни времени
 * выхода, ни нормы дня здесь не появляется: всё это приходит готовым,
 * а тут только раскладывается по местам.
 */

import type { Day, OpenSession, Status } from '../api';

// --- сегодняшние входы и выходы --------------------------------------------

export interface Punch {
  kind: 'entry' | 'exit';
  /** Метка времени события. */
  at: string;
  /** Название точки прохода. null — сервер её не знает. */
  point: string | null;
  /** Идентификатор сессии, из которой получено событие. */
  sessionId: string;
}

/**
 * Входы и выходы дня, от свежего к старому.
 *
 * Сессия — это пара событий, и раскладывается она обратно в пару: вход
 * по `started_at`, выход по `ended_at`. У открытой сессии выхода нет,
 * и он не дорисовывается — это то же самое, что выдумать человеку конец
 * рабочего дня.
 *
 * Направление берётся из устройства сессии, а не угадывается «по
 * близости к концу смены»: вход открывает сессию, выход её закрывает,
 * и других вариантов у данных нет.
 */
export function punches(sessions: OpenSession[] | undefined): Punch[] {
  if (!sessions?.length) return [];
  const all: Punch[] = [];
  for (const session of sessions) {
    all.push({
      kind: 'entry',
      at: session.started_at,
      point: session.entry_point_name,
      sessionId: session.id,
    });
    if (session.ended_at) {
      all.push({
        kind: 'exit',
        at: session.ended_at,
        point: session.exit_point_name,
        sessionId: session.id,
      });
    }
  }
  return all.sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}

/** Первый вход дня. null — сегодня ещё не отмечались. */
export function firstEntry(sessions: OpenSession[] | undefined): string | null {
  const entries = punches(sessions).filter((p) => p.kind === 'entry');
  return entries.length ? (entries[entries.length - 1]?.at ?? null) : null;
}

// --- прогресс рабочего дня --------------------------------------------------

export interface Shift {
  /** Минут прошло от начала смены. */
  elapsed: number;
  /** Всего минут в смене по часам. */
  total: number;
  /** Минут до конца смены. 0 — смена уже закончилась. */
  left: number;
}

/**
 * Сколько прошло от смены по местным часам.
 *
 * Это утверждение о времени суток, а не о человеке: полоса показывает,
 * сколько осталось до конца рабочего дня, а не сколько выполнено нормы.
 * Перепутать их нельзя, поэтому норма здесь не участвует вовсе —
 * в разницу «конец минус начало» входит обед, которого в графике нет.
 *
 * null, если смены на сегодня нет: полоса без графика показывала бы
 * долю от ничего.
 */
export function shiftProgress(
  status: Status,
  now: Date = new Date(),
): Shift | null {
  if (!status.scheduled_start || !status.scheduled_end) return null;
  const start = minutesOf(status.scheduled_start);
  const end = minutesOf(status.scheduled_end);
  if (start === null || end === null || end <= start) return null;

  const local = officeMinutes(status.timezone, now);
  const elapsed = Math.min(Math.max(local - start, 0), end - start);
  return { elapsed, total: end - start, left: end - start - elapsed };
}

function minutesOf(value: string): number | null {
  const [hours, minutes] = value.split(':');
  const h = Number.parseInt(hours ?? '', 10);
  const m = Number.parseInt(minutes ?? '', 10);
  return Number.isNaN(h) || Number.isNaN(m) ? null : h * 60 + m;
}

/** Минуты с полуночи в поясе офиса — не в поясе телефона. */
export function officeMinutes(timeZone: string, now: Date): number {
  const text = now.toLocaleString('ru-RU', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
  const [h, m] = text.split(':').map((part) => Number.parseInt(part, 10));
  return Number.isNaN(h) || Number.isNaN(m) ? 0 : h * 60 + m;
}

/** «09:00» из «09:00:00». Секунды в подписи графика ничего не добавляют. */
export function clock(value: string | null): string {
  return value ? value.slice(0, 5) : '—';
}

// --- моя неделя -------------------------------------------------------------

/**
 * Чем закончился день недели.
 *
 * Четыре разных вещи, которые легко свести в одну и соврать:
 * `done` — пришёл и выполнил норму; `short` — рабочий день, нормы нет;
 * `off` — выходной по графику; `blank` — данных нет: будущий день,
 * отсутствие или график не назначен.
 */
export type DayKind = 'done' | 'short' | 'off' | 'blank';

export interface WeekDay {
  day: string;
  /** Пн, Вт, … — по календарю, а не по порядку в массиве. */
  label: string;
  kind: DayKind;
  /** Доля нормы от 0 до 1. null — норму не с чем сравнивать. */
  ratio: number | null;
  seconds: number;
  norm: number | null;
  /** Подпись под кольцом: время либо прочерк. */
  isToday: boolean;
  isFuture: boolean;
  absence: string | null;
}

const SHORT_DAYS = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

export function weekdayLabel(iso: string): string {
  return SHORT_DAYS[new Date(`${iso}T00:00:00`).getDay()] ?? '';
}

/**
 * Неделя по дням.
 *
 * `today` приходит снаружи и берётся из ответа сервера (`status.day`):
 * это сегодня по часам офиса, а не по часам телефона. Сотрудник
 * в командировке иначе увидел бы «текущим» не тот день.
 */
export function week(days: Day[], today: string): WeekDay[] {
  return days.map((day) => {
    const isToday = day.day === today;
    const isFuture = day.day > today;
    const norm = day.norm_seconds;

    let kind: DayKind;
    if (norm === null || isFuture) {
      // Графика нет либо день ещё не наступил — это не ноль часов.
      kind = 'blank';
    } else if (day.absence_code) {
      // Отпуск и больничный — не недоработка: норму в эти дни не ждут.
      kind = 'blank';
    } else if (day.is_working_day === false || norm === 0) {
      kind = 'off';
    } else if (day.seconds >= norm) {
      kind = 'done';
    } else {
      // Сюда же попадает сегодняшний день: норма ещё не набрана.
      kind = 'short';
    }

    return {
      day: day.day,
      label: weekdayLabel(day.day),
      kind,
      ratio: norm && norm > 0 ? Math.min(day.seconds / norm, 1) : null,
      seconds: day.seconds,
      norm,
      isToday,
      isFuture,
      absence: day.absence_name,
    };
  });
}

// --- заявки -----------------------------------------------------------------

/** Заявки, о которых ещё есть что сказать, — от свежей к старой. */
export const LIVE_REQUEST_STATUSES = [
  'SUBMITTED',
  'IN_REVIEW',
  'APPROVED',
  'REJECTED',
] as const;

export type Tone = 'success' | 'warning' | 'danger' | 'idle';

/**
 * Цвет статуса заявки.
 *
 * Янтарный означает «решения ещё нет» — и у поданной заявки, и у той,
 * по которой ждут решения о продлении. Отменённая серая: по ней уже
 * ничего не произойдёт.
 *
 * Состояния «отмена ожидает подтверждения» в данных нет и раскрасить
 * его нечем: отмена либо проходит сразу, либо сервер отвечает, что
 * отменяет отдел кадров, — промежуточного положения заявка не
 * принимает. Ждущее решения продление — единственное, что на него
 * похоже, и оно здесь янтарное.
 */
export function requestTone(status: string, extensionPending: boolean): Tone {
  if (extensionPending) return 'warning';
  switch (status) {
    case 'APPROVED':
      return 'success';
    case 'REJECTED':
      return 'danger';
    case 'CANCELLED':
      return 'idle';
    default:
      return 'warning';
  }
}

export const REQUEST_LABEL: Record<string, string> = {
  DRAFT: 'Черновик',
  SUBMITTED: 'На рассмотрении',
  IN_REVIEW: 'На рассмотрении',
  APPROVED: 'Одобрено',
  REJECTED: 'Отклонено',
  CANCELLED: 'Отменено',
};

export function requestLabel(status: string, extensionPending: boolean): string {
  if (extensionPending) return 'Продление на рассмотрении';
  return REQUEST_LABEL[status] ?? status;
}
