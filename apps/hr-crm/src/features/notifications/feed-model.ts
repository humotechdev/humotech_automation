/**
 * Правила ленты событий, вынесенные из разметки.
 *
 * Лента и очередь отправки (`model.ts` рядом) — разные вещи. Очередь
 * отвечает «ушло ли сообщение сотруднику», лента — «что случилось и
 * ждёт кадровика». Общего словаря у них нет намеренно: одинаковые на
 * вид подписи означали бы разное.
 *
 * Здесь нет ни одной формулировки, за которой не стоит серверное поле.
 * Значок выбирается по виду события, подписи берутся из ответа, а
 * форматированием занимаются общие помощники — свои правила дат рядом
 * с чужими разошлись бы на первой же правке.
 */

import type { AppIconName } from '../../components/AppIcon';
import type { FeedCounts, FeedDetail, FeedEvent, FeedType } from '../../api/crm';
import { CHANNEL } from './model';
import { MONTHS_SHORT, dayTitle, momentTitle, sizeTitle, spanTitle } from '../reports/format';

/** Вкладки фильтра. Ключи те же, что понимает сервер. */
export const FEED_FILTERS = [
  { key: 'all', title: 'Все' },
  { key: 'unread', title: 'Непрочитанные' },
  { key: 'action', title: 'Требуют действия' },
  { key: 'requests', title: 'Заявки' },
  { key: 'documents', title: 'Документы' },
  { key: 'questions', title: 'Обращения' },
] as const;

export type FeedFilter = (typeof FEED_FILTERS)[number]['key'];

export const filterTitle = (key: string): string =>
  FEED_FILTERS.find((one) => one.key === key)?.title ?? 'Все';

export const filterCount = (key: string, counts: FeedCounts | null): number | null =>
  counts ? (counts[key as keyof FeedCounts] ?? null) : null;

/**
 * Значок вида события.
 *
 * Самолётик у отпуска и карандаш у исправления — те же, что на странице
 * «Заявки»: одно событие не должно выглядеть двумя разными вещами в
 * разных местах системы.
 */
export const FEED_ICON: Record<FeedType, AppIconName> = {
  absence_request: 'send',
  sick_leave: 'doc',
  absence_cancel: 'cross',
  absence_document: 'doc',
  attendance_correction: 'pencil',
  question: 'chat',
};

/**
 * Число у колокольчика. Больше девяноста девяти — «99+».
 *
 * Не ради красоты: четырёхзначное число в кружке нечитаемо, а точная
 * величина здесь ничего не решает — решает «много».
 */
export const badgeText = (count: number): string => (count > 99 ? '99+' : String(count));

/**
 * Относительное время события: «2 мин назад», «1 ч назад», «Вчера».
 *
 * Неделю назад относительная подпись перестаёт что-либо значить
 * («168 ч назад»), поэтому дальше показывается дата.
 */
export function since(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '';
  const minutes = Math.floor((now.getTime() - at.getTime()) / 60000);
  if (minutes < 1) return 'Только что';
  if (minutes < 60) return `${minutes} мин назад`;

  const hours = Math.floor(minutes / 60);
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (at.getTime() >= midnight.getTime()) return `${hours} ч назад`;

  const yesterday = new Date(midnight.getTime() - 86400000);
  if (at.getTime() >= yesterday.getTime()) return 'Вчера';
  return `${at.getDate()} ${MONTHS_SHORT[at.getMonth()]}`;
}

/** Сколько длится незакрытая сессия: «3 ч 20 мин». */
export function lasting(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest} мин`;
  return rest === 0 ? `${hours} ч` : `${hours} ч ${rest} мин`;
}

/** Цвет метки состояния. Красный — только у того, что сорвалось. */
export function statusTone(event: FeedEvent): string {
  if (event.priority === 'CRITICAL') return 'red';
  if (event.requires_action) return 'orange';
  if (['APPROVED', 'VERIFIED', 'SUCCEEDED', 'CLOSED'].includes(event.status)) {
    return 'green';
  }
  return 'grey';
}

export type Fact = { key: string; icon: AppIconName; label: string; value: string };

/**
 * Строки правой области — по виду события.
 *
 * Состав задан заданием и собирается ТОЛЬКО из пришедших полей: пустое
 * значение не показывается вовсе, а не подменяется прочерком с видом
 * настоящих данных. Ни номера паспорта, ни ссылки на файл, ни ответа
 * провайдера здесь нет — их нет и в ответе.
 */
export function facts(card: FeedDetail): Fact[] {
  const rows: Fact[] = [];
  const add = (key: string, icon: AppIconName, label: string, value: string | null) => {
    if (value) rows.push({ key, icon, label, value });
  };

  const absence = card.absence;
  if (absence) {
    const period = spanTitle(absence.first_day ?? undefined, absence.last_day ?? undefined);
    add('period', 'calendar', periodLabel(card.type), period === '—' ? null : period);
    if (absence.calendar_days !== null && absence.working_days !== null) {
      add(
        'days', 'clock', 'Дни',
        `${absence.calendar_days} календарных · ${absence.working_days} рабочих`,
      );
    }
    if (absence.balance_before_days !== null && absence.balance_after_days !== null) {
      add(
        'balance', 'chart', 'Остаток отпуска',
        `${plainDays(absence.balance_before_days)} → ${plainDays(absence.balance_after_days)}`,
      );
    }
    if (absence.is_extension) add('extension', 'arrow', 'Вид заявки', 'Продление');
    if (absence.overlaps) {
      add('overlap', 'alert', 'Пересечение', 'Есть другое отсутствие на эти даты');
    }
    const file = absence.document;
    if (file) {
      const size = sizeTitle(file.size_bytes);
      add(
        'document', 'doc', 'Справка',
        `${file.file_name}${size ? ` · ${size}` : ''} · ${file.verification_label}`,
      );
      add('uploaded', 'calendar', 'Загружена', momentTitle(file.uploaded_at));
    } else if (absence.requires_document) {
      add('document', 'doc', 'Справка', 'Не загружена');
    }
  }

  const fix = card.correction;
  if (fix) {
    add('day', 'calendar', 'Рабочий день', fix.day ? dayTitle(fix.day) : null);
    add('kind', 'pencil', 'Тип события', fix.event_kind);
    add(
      'was', 'clock', 'Сейчас в системе',
      pair(fix.current_entry_at, fix.current_exit_at) || 'Отметки нет',
    );
    add(
      'ask', 'clock', 'Просят поставить',
      pair(fix.requested_entry_at, fix.requested_exit_at),
    );
    if (fix.has_document) add('file', 'doc', 'Приложение', 'Документ приложен');
  }

  const session = card.session;
  if (session) {
    add('entry', 'clock', 'Первый вход', momentTitle(session.started_at));
    add('open', 'late', 'Сессия открыта', lasting(session.open_minutes));
    add('schedule', 'calendar', 'График', session.schedule_name ?? 'Не назначен');
    add('qr', 'pin', 'QR-точка', session.qr_point_name);
    if (session.last_event_at) {
      add(
        'last', 'list', 'Последняя отметка',
        `${session.last_event_type === 'ENTRY' ? 'Вход' : 'Выход'}, ${momentTitle(session.last_event_at)}`,
      );
    }
  }

  const question = card.question;
  if (question) {
    add('topic', 'chat', 'Тема', question.topic);
    add('channel', 'send', 'Канал', CHANNEL[question.channel] ?? question.channel);
    add('created', 'calendar', 'Создано', momentTitle(card.occurred_at));
    add('last', 'clock', 'Последнее сообщение', momentTitle(question.last_message_at));
    add(
      'assignee', 'user', 'Ответственный',
      question.assigned_to?.name ?? 'Не назначен',
    );
  }

  const delivery = card.delivery;
  if (delivery) {
    add('to', 'user', 'Получатель', card.employee_name);
    add('channel', 'send', 'Канал', CHANNEL[delivery.channel] ?? delivery.channel);
    add('when', 'clock', 'Последняя попытка', momentTitle(delivery.last_attempt_at));
    add('tries', 'list', 'Попыток', String(delivery.attempts));
    add(
      'retry', 'refresh', 'Повтор',
      delivery.will_retry ? 'Запланирован автоматически' : 'Не запланирован',
    );
  }

  const report = card.report;
  if (report) {
    add('file', 'sheet', 'Файл', report.file_name);
    add('size', 'database', 'Размер', sizeTitle(report.size_bytes));
    add('rows', 'list', 'Строк', report.rows === null ? null : String(report.rows));
    add(
      'expires', 'calendar', 'Хранится до',
      report.expires_at ? momentTitle(report.expires_at) : null,
    );
  }

  const person = card.new_employee;
  if (person) {
    add('number', 'key', 'Табельный номер', person.employee_number);
    add('hired', 'calendar', 'Принят', dayTitle(person.hire_date));
    add(
      'telegram', 'send', 'Telegram',
      person.telegram_connected ? 'Привязан' : 'Не привязан',
    );
  }

  const place = card.employee;
  add('office', 'building', 'Офис', place?.office_name ?? card.office_name ?? null);
  return rows;
}

function periodLabel(type: FeedType): string {
  if (type === 'sick_leave') return 'Период больничного';
  if (type === 'absence_cancel') return 'Отменяемый период';
  if (type === 'absence_document') return 'Период отсутствия';
  return 'Период отпуска';
}

/** «14 дней» без лишнего нуля у целых значений. */
function plainDays(value: number): string {
  const text = Number.isInteger(value) ? String(value) : String(value).replace('.', ',');
  return `${text} дн.`;
}

function pair(entry: string | null, exit: string | null): string {
  const parts = [
    entry ? `вход ${clock(entry)}` : null,
    exit ? `выход ${clock(exit)}` : null,
  ].filter(Boolean);
  return parts.join(', ');
}

function clock(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}
