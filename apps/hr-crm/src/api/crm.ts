/**
 * Запросы главной страницы. Адреса и поля — из существующего backend,
 * ничего не выдумано: `/dashboard` уже отдаёт карточки вместе с адресом
 * списка, из которого сложилось каждое число, `/analytics` — ряд по дням,
 * `/attendance/presence` — состав смены целиком, без страниц.
 *
 * Коллекции DRF-роутера требуют завершающей косой черты: без неё backend
 * отвечает 301, а браузер теряет заголовки при переадресации.
 */

import { request } from './client';

// --- дашборд ---------------------------------------------------------------

export type Card = {
  key: string;
  title: string;
  value: number;
  endpoint: string | null;
  params: Record<string, string>;
  attention: boolean;
};

export type Dashboard = {
  date: string;
  timezone: string;
  cards: Card[];
  warnings: { code?: string; message?: string }[];
};

export const dashboard = (filters: Filters, signal?: AbortSignal) =>
  request<Dashboard>(`/dashboard${query(filters)}`, signal ? { signal } : {});

// --- фильтры ---------------------------------------------------------------

export type Filters = {
  date?: string;
  region_id?: string;
  office_id?: string;
};

export function query(filters: Record<string, string | undefined>): string {
  const parts = Object.entries(filters).filter(([, v]) => v);
  if (parts.length === 0) return '';
  return '?' + parts.map(([k, v]) => `${k}=${encodeURIComponent(v as string)}`).join('&');
}

// --- аналитика -------------------------------------------------------------

export type DayPoint = {
  day: string;
  worked_seconds: number;
  attended: number;
  expected: number;
  late: number;
};

export type Analytics = {
  period: { first: string; last: string; timezone: string };
  headcount: number;
  series: DayPoint[];
};

export const analytics = (
  from: string,
  to: string,
  filters: Filters,
  signal?: AbortSignal,
) =>
  request<Analytics>(
    `/analytics${query({ ...filters, date: undefined, date_from: from, date_to: to })}`,
    signal ? { signal } : {},
  );

// --- справочники -----------------------------------------------------------

export type Region = { id: string; code: string; name: string; status: string };
export type Office = {
  id: string;
  code: string;
  name: string;
  region_id: string | null;
  region_name: string | null;
  status: string;
};

export type Items<T> = { items: T[]; has_more?: boolean };

export const regions = (signal?: AbortSignal) =>
  request<Items<Region>>('/regions/', signal ? { signal } : {});

export const offices = (signal?: AbortSignal) =>
  request<Items<Office>>('/offices/', signal ? { signal } : {});

// --- состав смены ----------------------------------------------------------

export type Presence = {
  date: string;
  timezone: string;
  counts: Record<string, number>;
  total: number;
  /** Строк отдано меньше, чем есть. `counts` при этом всё равно полный. */
  truncated: boolean;
  items: {
    employee_id: string;
    full_name: string;
    employee_number: string | null;
    office_name: string | null;
    state: string;
    late_minutes: number | null;
  }[];
};

export const presence = (filters: Filters & { state?: string }, signal?: AbortSignal) =>
  request<Presence>(`/attendance/presence${query(filters)}`, signal ? { signal } : {});

// --- очереди на решение ----------------------------------------------------

export type AbsenceRequestRow = {
  id: string;
  employee: { id: string; full_name: string; employee_number: string | null };
  absence_type: { code: string; name: string };
  status: string;
  first_day: string | null;
  last_day: string | null;
  submitted_at: string | null;
};

export const pendingAbsences = (signal?: AbortSignal) =>
  request<{ requests: AbsenceRequestRow[] }>(
    '/absence-requests/pending',
    signal ? { signal } : {},
  );

export const corrections = (signal?: AbortSignal) =>
  request<Items<{ id: string; status: string }>>(
    '/attendance/corrections?status=PENDING',
    signal ? { signal } : {},
  );

export type Invitation = { id: string; status: string; employee_name?: string };

export const invitations = (signal?: AbortSignal) =>
  request<Items<Invitation>>('/telegram/invitations/', signal ? { signal } : {});

export type Escalation = {
  id: string;
  status?: string;
  question?: string;
  text?: string;
  created_at?: string;
  employee_name?: string;
  office_name?: string;
};

export const escalations = (signal?: AbortSignal) =>
  request<Items<Escalation>>('/knowledge/escalations/', signal ? { signal } : {});

export const sessions = (filters: Filters, signal?: AbortSignal) =>
  request<Items<{ id: string }>>(
    `/attendance/sessions${query({ ...filters, date: undefined, open: 'true' })}`,
    signal ? { signal } : {},
  );

// --- сотрудники ------------------------------------------------------------

export type Assignment = {
  id: string;
  office_id: string | null;
  office_name: string | null;
  region_id: string | null;
  region_name: string | null;
  department_id: string | null;
  department_name: string | null;
  position_id: string | null;
  position_name: string | null;
  employment_type: string | null;
  work_mode: string | null;
  is_primary: boolean;
  valid_from: string | null;
  valid_to: string | null;
};

export type Schedule = {
  id: string;
  name: string;
  timezone: string;
  weekly_minutes: number;
  is_flexible: boolean;
};

export type EmployeeRow = {
  id: string;
  employee_number: string | null;
  full_name: string;
  first_name: string;
  last_name: string;
  phone: string | null;
  corporate_email: string | null;
  employment_status: string;
  hire_date: string | null;
  termination_date: string | null;
  telegram_connected: boolean;
  /** Состояние привязки из самих привязок, а не из денормализованного флага. */
  telegram_state: string | null;
  current_assignment: Assignment | null;
  current_schedule: Schedule | null;
};

export type Cursored<T> = { items: T[]; next_cursor: string | null; has_more: boolean };

export type EmployeeQuery = {
  search?: string;
  status?: string;
  region_id?: string;
  office_id?: string;
  department_id?: string;
  limit?: string;
  cursor?: string;
};

export const employees = (params: EmployeeQuery, signal?: AbortSignal) =>
  request<Cursored<EmployeeRow>>(`/employees/${query(params)}`, signal ? { signal } : {});

/** Счётчики вкладок. Состояние в параметры НЕ входит: иначе, выбрав
 *  «Активные», человек видел бы нули у остальных вкладок. */
export const employeeCounts = (params: EmployeeQuery, signal?: AbortSignal) =>
  request<Record<string, number>>(
    `/employees/counts/${query({ ...params, status: undefined, cursor: undefined, limit: undefined })}`,
    signal ? { signal } : {},
  );

export const employee = (id: string, signal?: AbortSignal) =>
  request<Record<string, unknown>>(`/employees/${id}/`, signal ? { signal } : {});

export const employeeAssignments = (id: string, signal?: AbortSignal) =>
  request<Items<Assignment>>(`/employees/${id}/assignments/`, signal ? { signal } : {});

export const employeeSchedules = (id: string, signal?: AbortSignal) =>
  request<Items<Record<string, unknown>>>(
    `/employees/${id}/schedules`,
    signal ? { signal } : {},
  );

export type TelegramLink = {
  state: string;
  account: { status: string; telegram_username: string | null; connected_at: string | null } | null;
  invitation?: { id: string; status: string; expires_at: string | null } | null;
};

export const employeeTelegram = (id: string, signal?: AbortSignal) =>
  request<TelegramLink>(`/employees/${id}/telegram`, signal ? { signal } : {});

/** Приглашение создаётся ТОЛЬКО по нажатию — не при открытии страницы. */
export const inviteToTelegram = (employee_id: string) =>
  request<{ id: string; link?: string; url?: string; token?: string; expires_at?: string }>(
    '/telegram/invitations/',
    { method: 'POST', body: { employee_id } },
  );

export type Department = { id: string; name: string; office_id?: string | null };

export const departments = (signal?: AbortSignal) =>
  request<Items<Department>>('/departments/', signal ? { signal } : {});
