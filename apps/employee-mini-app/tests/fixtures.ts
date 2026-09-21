/**
 * Данные для тестов интерфейса.
 *
 * Живут только здесь. В самих экранах ни одной захардкоженной строки
 * с примером быть не должно: подставленное «для наглядности» значение
 * рано или поздно доезжает до боя и показывает человеку чужие часы.
 */

import type {
  AbsenceOptions,
  AbsenceRequest,
  Day,
  Note,
  OpenSession,
  Profile,
  Status,
  Summary,
} from '../src/api';
import type { Section } from '../src/sections';

export const TZ = 'Asia/Dushanbe';

export const profile: Profile = {
  employee: {
    id: 'e1',
    full_name: 'Рахимов Далер',
    employee_number: 'DEMO-001',
  },
  office: { id: 'o1', name: 'Головной офис', timezone: TZ },
  position: { name: 'Инженер-программист' },
  department: { name: 'Отдел разработки' },
  assignment: {
    employment_type: 'FULL_TIME',
    work_mode: 'ONSITE',
    valid_from: '2024-01-01',
  },
  telegram: { status: 'ACTIVE', username: 'daler' },
};

export function session(over: Partial<OpenSession> = {}): OpenSession {
  return {
    id: 's1',
    day: '2026-09-04',
    started_at: '2026-09-04T03:54:00Z',
    ended_at: '2026-09-04T13:07:00Z',
    seconds: 33_180,
    is_open: false,
    is_preliminary: false,
    office_name: 'Головной офис',
    entry_point_name: 'Главный вход',
    exit_point_name: 'Главный вход',
    ...over,
  };
}

export function status(over: Partial<Status> = {}): Status {
  return {
    state: 'OUTSIDE',
    day: '2026-09-04',
    timezone: TZ,
    seconds_today: 0,
    open_session: null,
    last_entry_at: null,
    last_exit_at: null,
    scheduled_start: '09:00:00',
    scheduled_end: '18:00:00',
    absence_name: null,
    ...over,
  };
}

export function summary(over: Partial<Summary> = {}): Summary {
  return {
    first: '2026-09-01',
    last: '2026-09-30',
    timezone: TZ,
    seconds: 137_700,
    completed_sessions: 5,
    open_sessions: 0,
    working_days: 22,
    attended_days: 5,
    missed_days: 1,
    sick_leave_days: 0,
    vacation_days: 0,
    other_absence_days: 0,
    has_schedule: true,
    ...over,
  };
}

export function day(over: Partial<Day> = {}): Day {
  return {
    day: '2026-09-04',
    seconds: 29_640,
    sessions_count: 2,
    has_open_session: false,
    is_working_day: true,
    attended: true,
    missed: false,
    absence_code: null,
    absence_name: null,
    // Норма дня: восемь часов у обычного рабочего дня пятидневки.
    norm_seconds: 28_800,
    ...over,
  };
}

export function request(over: Partial<AbsenceRequest> = {}): AbsenceRequest {
  return {
    id: 'r1',
    kind: 'CREATE',
    absence_type: {
      code: 'ANNUAL_LEAVE',
      name: 'Ежегодный отпуск',
      requires_document: false,
      deducts_leave_balance: true,
    },
    status: 'SUBMITTED',
    extension_pending: false,
    first_day: '2026-10-05',
    last_day: '2026-10-16',
    working_days: 10,
    comment: null,
    review_comment: null,
    documents: 0,
    can_cancel: true,
    submitted_at: '2026-09-04T06:00:00Z',
    reviewed_at: null,
    absence_status: null,
    ...over,
  };
}

export const options: AbsenceOptions = {
  types: [
    {
      code: 'SICK_LEAVE',
      name: 'Больничный',
      requires_document: true,
      deducts_leave_balance: false,
    },
    {
      code: 'ANNUAL_LEAVE',
      name: 'Ежегодный отпуск',
      requires_document: false,
      deducts_leave_balance: true,
    },
  ],
  policy: {
    require_hr_approval: true,
    document_required: false,
    employee_may_cancel_pending: true,
    cancelling_approved_requires_hr: true,
    extensions_allowed: true,
    max_document_bytes: 10_485_760,
    allowed_document_types: ['application/pdf', 'image/jpeg', 'image/png'],
  },
};

/**
 * Ответ на любой запрос по пути.
 *
 * Возвращает и сам `fetch`, и список вызовов: половина проверок здесь —
 * не про то, что нарисовано, а про то, что ушло на сервер.
 */
export function fakeFetch(routes: Record<string, unknown>) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];

  const impl = async (url: string | URL | Request, init?: RequestInit) => {
    const address = String(url);
    calls.push({ url: address, init });
    const key = Object.keys(routes).find((path) => address.includes(path));
    const body = key ? routes[key] : {};
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  return { impl: impl as unknown as typeof fetch, calls };
}

export function note(over: Partial<Note> = {}): Note {
  return {
    id: 'n1',
    notification_type: 'office.announcement',
    title: 'Объявление',
    body: 'Обновлён график работы офиса',
    sent_at: '2026-09-04T05:00:00Z',
    read_at: null,
    is_read: false,
    ...over,
  };
}

// --- секции ----------------------------------------------------------------
//
// Три состояния, в которых карточка бывает: данные пришли, первая
// загрузка идёт, запрос упал. Четвёртое — обновление поверх показанного —
// это `ready` с `refreshing: true`, и оно задаётся вторым аргументом.

export function ready<T>(data: T, refreshing = false): Section<T> {
  return {
    data,
    loading: false,
    refreshing,
    error: null,
    kind: null,
    reload: () => {},
  };
}

export function pending<T>(): Section<T> {
  return {
    data: null,
    loading: true,
    refreshing: false,
    error: null,
    kind: null,
    reload: () => {},
  };
}

export function failed<T>(
  message = 'Сервер временно недоступен',
  reload: () => void = () => {},
): Section<T> {
  return {
    data: null,
    loading: false,
    refreshing: false,
    error: message,
    kind: 'server',
    reload,
  };
}
