/**
 * Правила страницы уведомлений, вынесенные из разметки.
 *
 * Два разбирательства, ради которых страница существует, и оба
 * упираются в слова:
 *
 *   «ушло ли сообщение» — это статус отправки, и он НЕ равен прочтению;
 *   «почему не ушло» — это причина, и придумывать её нельзя.
 *
 * Поэтому здесь нет ни одной формулировки, за которой не стоит
 * серверное поле.
 */

import type { Attempt, Notification, NotificationCounts } from '../../api/crm';

/** Подпись состояния. Шесть, ровно как в очереди. */
export const STATUS: Record<string, string> = {
  PENDING: 'В очереди',
  RUNNING: 'Отправляется',
  SENT: 'Отправлено',
  READ: 'Прочитано',
  FAILED: 'Ошибка',
  CANCELLED: 'Снято',
};

/**
 * Вкладки. Каждая берёт те же состояния, что считает её счётчик, —
 * иначе число рядом с вкладкой не совпадёт с числом строк под ней.
 *
 * «Снято» — отдельная вкладка, а не часть ошибок. Чаще всего это
 * ставит очередь, когда у сотрудника нет живой привязки Telegram:
 * называть неполадкой обычное положение дел нельзя, а прятать
 * такие строки — тем более.
 */
export const TABS = [
  { key: 'all', title: 'Все', statuses: '', count: 'total' },
  { key: 'sent', title: 'Отправлено', statuses: 'SENT,READ', count: 'sent' },
  { key: 'queued', title: 'В очереди', statuses: 'PENDING,RUNNING', count: 'queued' },
  { key: 'failed', title: 'С ошибкой', statuses: 'FAILED', count: 'failed' },
  { key: 'cancelled', title: 'Снято', statuses: 'CANCELLED', count: 'cancelled' },
] as const;

export type Tab = (typeof TABS)[number]['key'];

export const tabCount = (tab: Tab, counts: NotificationCounts | null): number | null => {
  if (!counts) return null;
  const found = TABS.find((item) => item.key === tab);
  return found ? counts[found.count] : null;
};

/** Ждём ли мы ещё чего-то от очереди. Пока нет — опрос не нужен. */
const MOVING = new Set(['PENDING', 'RUNNING']);
export const moving = (rows: { status: string }[]): boolean =>
  rows.some((row) => MOVING.has(row.status));

/** Канал доставки словами. */
export const CHANNEL: Record<string, string> = {
  TELEGRAM: 'Telegram',
  EMAIL: 'Почта',
  PUSH: 'Push',
  IN_APP: 'В интерфейсе',
};

/**
 * Название события по типу уведомления.
 *
 * Тип — это ключ вроде `absence.request.approved`, и показывать его
 * человеку незачем. Заголовок сервер хранит рядом; словарь нужен для
 * строк, у которых заголовка нет.
 */
const EVENTS: [string, string][] = [
  ['telegram.link.confirmed', 'Привязка подтверждена'],
  ['telegram.link.rejected', 'Привязка отклонена'],
  ['absence.request.approved', 'Заявка согласована'],
  ['absence.request.rejected', 'Заявка отклонена'],
  ['absence.document', 'Нужна справка'],
  ['absence.', 'Заявка на отсутствие'],
  ['question.', 'Ответ на обращение'],
  ['attendance.', 'Отметка исправлена'],
];

export function eventTitle(row: Pick<Notification, 'title' | 'notification_type'>): string {
  if (row.title) return row.title;
  const found = EVENTS.find(([prefix]) => row.notification_type.startsWith(prefix));
  // Ключ показываем, только если ничего лучше нет: выдумывать название
  // события по коду хуже, чем показать код.
  return found ? found[1] : row.notification_type;
}

/**
 * Причина неудачи понятными словами.
 *
 * Сервер хранит короткий код, а не ответ Telegram: ответ может
 * содержать эхо запроса, то есть текст уведомления целиком. Здесь
 * коды переводятся, и ничего сверх словаря не выдумывается — код,
 * которого тут нет, показывается как «причина не распознана», а не как
 * правдоподобная фраза.
 */
const REASONS: Record<string, string> = {
  // очередь сняла строку сама
  not_linked: 'Сотрудник не привязал Telegram',
  pending_confirmation: 'Привязка Telegram ещё не подтверждена',
  link_revoked: 'Привязка Telegram отозвана',
  link_blocked: 'Привязка Telegram заблокирована',
  employee_inactive: 'Сотрудник неактивен',
  organization_inactive: 'Организация неактивна',
  no_assignment: 'У сотрудника нет действующего назначения',
  cancelled_by_operator: 'Снято вручную',
  // отчёт отправщика
  blocked_by_user: 'Получатель заблокировал бота',
  TelegramNetworkError: 'Не удалось соединиться с Telegram',
  TelegramServerError: 'Telegram ответил ошибкой',
  TelegramRetryAfter: 'Отправка временно ограничена',
  TelegramForbiddenError: 'Получатель заблокировал бота',
  TelegramUnauthorizedError: 'Бот не авторизован в Telegram',
  TelegramBadRequest: 'Telegram отклонил сообщение',
  TelegramNotFound: 'Чат получателя не найден',
  TelegramConflictError: 'Одновременно работает другой экземпляр бота',
  TelegramEntityTooLarge: 'Сообщение слишком велико',
  unknown: 'Причина не распознана',
};

export function reasonTitle(code: string | null): string | null {
  if (!code) return null;
  return REASONS[code] ?? 'Причина не распознана';
}

/**
 * Почему повтор сейчас недоступен. `null` — доступен.
 *
 * Разрешение считает сервер (`can_retry`), здесь только объяснение:
 * кнопка, погашенная без причины, читается как неполадка.
 */
export function retryBlockedBecause(row: Notification, mayManage: boolean): string | null {
  if (!mayManage) return 'нужно право «Управление уведомлениями»';
  if (row.can_retry) return null;
  if (row.status === 'SENT' || row.status === 'READ') {
    return 'сообщение уже в чате у человека — повтор прислал бы второе';
  }
  if (row.status === 'PENDING') return 'уведомление и так в очереди';
  if (row.status === 'RUNNING') return 'строку прямо сейчас держит отправщик';
  return 'текущее состояние не позволяет';
}

export function cancelBlockedBecause(row: Notification, mayManage: boolean): string | null {
  if (!mayManage) return 'нужно право «Управление уведомлениями»';
  if (row.can_cancel) return null;
  if (row.status === 'SENT' || row.status === 'READ') {
    return 'сообщение уже отправлено — снять его из Telegram нельзя';
  }
  if (row.status === 'RUNNING') return 'строку прямо сейчас держит отправщик';
  if (row.status === 'CANCELLED') return 'уже снято';
  return 'текущее состояние не позволяет';
}

/**
 * Что сказать про доставку.
 *
 * Отправка и прочтение — разные вещи, и подпись обязана их разделять.
 * `read_at` ставит не отправщик: успешный ответ Telegram означает
 * «принято к доставке», а не «человек прочитал».
 */
export function deliveryNote(row: Notification): string {
  if (row.status === 'READ') return 'Сотрудник открыл сообщение.';
  if (row.status === 'SENT') {
    return 'Сообщение передано в Telegram. Прочтение этим не подтверждается.';
  }
  if (row.status === 'RUNNING') return 'Отправляется прямо сейчас.';
  if (row.status === 'PENDING') return 'Ждёт очереди отправки.';
  if (row.status === 'FAILED') return 'Отправить не удалось.';
  return 'Снято с отправки — сотруднику не уйдёт.';
}

/** Итог одной попытки словами. */
export function attemptTitle(attempt: Attempt): string {
  if (attempt.outcome === 'SENT') return 'Отправлено';
  const reason = reasonTitle(attempt.reason);
  if (attempt.outcome === 'CANCELLED') return reason ?? 'Снято с отправки';
  return reason ?? 'Отправка не удалась';
}

/**
 * Куда ведёт связанный объект.
 *
 * Только те виды, для которых в CRM ЕСТЬ страница. Для остальных
 * ссылки нет: «открыть» на несуществующем адресе хуже, чем её
 * отсутствие.
 */
export function relatedLink(row: Notification): { to: string; title: string } | null {
  if (!row.related_entity_id) return null;
  if (row.related_entity_type === 'absence_requests') {
    return { to: `/requests?request=${row.related_entity_id}`, title: 'Открыть заявку' };
  }
  if (row.related_entity_type === 'employee_questions') {
    return { to: `/questions?id=${row.related_entity_id}`, title: 'Открыть обращение' };
  }
  if (row.related_entity_type === 'telegram_accounts') {
    // У привязки своей страницы нет — ведём в карточку сотрудника,
    // где привязка и живёт.
    return {
      to: `/employees/${row.employee_id}`,
      title: 'Открыть карточку сотрудника',
    };
  }
  return null;
}
