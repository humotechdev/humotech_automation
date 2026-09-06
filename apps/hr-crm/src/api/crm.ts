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

// --- очередь заявок --------------------------------------------------------

export type DocumentRow = {
  id: string;
  document_type: string;
  verification_status: 'PENDING' | 'VERIFIED' | 'REJECTED' | string;
  verified_at: string | null;
  file: {
    id: string;
    name: string;
    mime_type: string;
    size_bytes: number;
    uploaded_at: string;
    scan_status: string;
  };
};

export type AbsenceRow = AbsenceRequestRow & {
  documents: DocumentRow[];
  requires_document: boolean;
  /** Что написал сам сотрудник. Диагноза здесь быть не должно. */
  comment: string | null;
  review_comment: string | null;
};

export type CorrectionRow = {
  id: string;
  status: string;
  employee?: { id: string; full_name: string; employee_number: string | null };
  employee_id?: string;
  reason?: string | null;
  requested_change?: Record<string, unknown> | null;
  created_at?: string;
};

export type QueueItem = {
  kind: 'absence' | 'correction';
  id: string;
  created_at: string;
  absence?: AbsenceRow;
  correction?: CorrectionRow;
};

export type QueueQuery = {
  kind?: string;
  status?: string;
  type?: string;
  region_id?: string;
  office_id?: string;
  search?: string;
  date_from?: string;
  date_to?: string;
  limit?: string;
  cursor?: string;
};

export const queue = (params: QueueQuery, signal?: AbortSignal) =>
  request<Cursored<QueueItem>>(`/requests${query(params)}`, signal ? { signal } : {});

/** Решение по заявке на отсутствие. `decision` — часть адреса, как у backend. */
export const decideAbsence = (id: string, decision: 'approve' | 'reject', comment: string) =>
  request<unknown>(`/absence-requests/${id}/${decision}`, {
    method: 'POST',
    body: comment ? { comment } : {},
  });

export const decideCorrection = (
  id: string,
  decision: 'approve' | 'reject',
  comment: string,
) =>
  request<unknown>(`/attendance/corrections/${id}/${decision}`, {
    method: 'POST',
    body: comment ? { comment } : {},
  });

// --- посещаемость ----------------------------------------------------------

export type PresenceRow = {
  employee_id: string;
  full_name: string;
  employee_number: string | null;
  office_id: string | null;
  office_name: string | null;
  state: string;
  first_entry_at: string | null;
  last_exit_at: string | null;
  seconds: number;
  open_session_id: string | null;
  /** `null` — сравнивать не с чем, а не «не опоздал». */
  late_minutes: number | null;
  scheduled_start: string | null;
  absence_code: string | null;
  absence_name: string | null;
  conflicting_marks: boolean;
};

export type PresencePage = {
  date: string;
  timezone: string;
  counts: Record<string, number>;
  total: number;
  /** Строк отдано меньше, чем есть: выдавать их за весь состав нельзя. */
  truncated: boolean;
  items: PresenceRow[];
};

export const presenceDay = (
  params: { date?: string; region_id?: string; office_id?: string; state?: string; search?: string },
  signal?: AbortSignal,
) => request<PresencePage>(`/attendance/presence${query(params)}`, signal ? { signal } : {});

export type EventRow = {
  id: string;
  employee_id: string;
  office_id: string | null;
  office_name: string | null;
  qr_point_id: string | null;
  qr_point_name: string | null;
  event_type: 'ENTRY' | 'EXIT' | string;
  source: string;
  verification_status: string;
  occurred_at: string;
  received_at: string;
  rejection_reason: string | null;
};

export type EventQuery = {
  employee_id?: string;
  office_id?: string;
  region_id?: string;
  date_from?: string;
  date_to?: string;
  event_type?: string;
  source?: string;
  verification_status?: string;
  limit?: string;
  cursor?: string;
};

export const events = (params: EventQuery, signal?: AbortSignal) =>
  request<Cursored<EventRow>>(`/attendance/events${query(params)}`, signal ? { signal } : {});

export type SessionRow = {
  id: string;
  employee_id: string;
  office_id: string | null;
  office_name: string | null;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  status: string;
  is_open: boolean;
};

export const attendanceSessions = (
  params: { employee_id?: string; date_from?: string; date_to?: string; limit?: string },
  signal?: AbortSignal,
) => request<Cursored<SessionRow>>(`/attendance/sessions${query(params)}`, signal ? { signal } : {});

/**
 * Ручная отметка кадровика.
 *
 * Это ДОБАВЛЕНИЕ события, а не правка существующего: `source = MANUAL`
 * отличает её от сканирования навсегда, а причина обязательна.
 */
export const addManualEvent = (body: {
  employee_id: string;
  office_id: string;
  event_type: 'ENTRY' | 'EXIT';
  occurred_at: string;
  reason: string;
}) => request<EventRow>('/attendance/manual', { method: 'POST', body });

// --- офисы, регионы и QR-точки ---------------------------------------------

export type OfficeFull = Office & {
  address: string | null;
  timezone: string;
  latitude: string | number | null;
  longitude: string | number | null;
  geofence_radius_m: number | null;
  opened_at: string | null;
  closed_at: string | null;
};

export const officesPage = (
  params: { search?: string; status?: string; region_id?: string; limit?: string; cursor?: string },
  signal?: AbortSignal,
) => request<Cursored<OfficeFull>>(`/offices/${query(params)}`, signal ? { signal } : {});

export const updateOffice = (id: string, changes: Record<string, unknown>) =>
  request<OfficeFull>(`/offices/${id}/`, { method: 'PATCH', body: changes });

export const setOfficeActive = (id: string, active: boolean) =>
  request<OfficeFull>(`/offices/${id}/${active ? 'reactivate' : 'deactivate'}/`, {
    method: 'POST',
    body: {},
  });

export type RegionFull = Region & { timezone: string | null };

export const regionsPage = (
  params: { search?: string; status?: string; limit?: string; cursor?: string },
  signal?: AbortSignal,
) => request<Cursored<RegionFull>>(`/regions/${query(params)}`, signal ? { signal } : {});

export const setRegionActive = (id: string, active: boolean) =>
  request<RegionFull>(`/regions/${id}/${active ? 'reactivate' : 'deactivate'}/`, {
    method: 'POST',
    body: {},
  });

export type QrPoint = {
  id: string;
  office_id: string;
  office_name: string | null;
  code: string;
  name: string;
  direction_mode: 'ENTRY' | 'EXIT' | 'BOTH' | string;
  qr_mode: string;
  rotation_seconds: number | null;
  require_geolocation: boolean;
  require_office_network: boolean;
  is_active: boolean;
};

export const qrPoints = (params: { office_id?: string }, signal?: AbortSignal) =>
  request<Items<QrPoint>>(`/qr-points/${query(params)}`, signal ? { signal } : {});

export type QrDevice = {
  id: string;
  qr_point_id: string | null;
  qr_point_name?: string | null;
  office_name?: string | null;
  name: string;
  status: string;
  /** Когда экран последний раз о себе сообщал. `null` — не сообщал вовсе. */
  last_seen_at: string | null;
  paired_at: string | null;
};

/** Отдаётся голым массивом, без обёртки `items`. */
export const qrDevices = (signal?: AbortSignal) =>
  request<QrDevice[]>('/qr/devices', signal ? { signal } : {});

// --- обращения -------------------------------------------------------------

export type EscalationRow = {
  id: string;
  employee: { id: string; full_name: string; employee_number: string | null };
  question_text: string;
  normalized_topic: string | null;
  status: string;
  ai_answer_text: string | null;
  hr_answer_text: string | null;
  assigned_to_user_id: string | null;
  answered_at: string | null;
  created_at: string;
  updated_at: string;
};

export type EscalationQuery = {
  status?: string;
  employee_id?: string;
  office_id?: string;
  assigned_to_me?: string;
  search?: string;
  limit?: string;
  cursor?: string;
};

export const escalationList = (params: EscalationQuery, signal?: AbortSignal) =>
  request<Cursored<EscalationRow>>(
    `/knowledge/escalations/${query(params)}`,
    signal ? { signal } : {},
  );

/** Счётчики вкладок. Состояние в параметры не входит намеренно. */
export const escalationCounts = (
  params: { office_id?: string; search?: string },
  signal?: AbortSignal,
) =>
  request<Record<string, number>>(
    `/knowledge/escalations/counts/${query(params)}`,
    signal ? { signal } : {},
  );

export const escalation = (id: string, signal?: AbortSignal) =>
  request<EscalationRow>(`/knowledge/escalations/${id}/`, signal ? { signal } : {});

/** Ответ уходит сотруднику в чат той же транзакцией, что и сохранение. */
export const answerEscalation = (id: string, answer: string) =>
  request<EscalationRow>(`/knowledge/escalations/${id}/answer/`, {
    method: 'POST',
    body: { answer },
  });

export const assignEscalation = (id: string, user_id?: string) =>
  request<EscalationRow>(`/knowledge/escalations/${id}/assign/`, {
    method: 'POST',
    body: user_id ? { user_id } : {},
  });

export const closeEscalation = (id: string) =>
  request<EscalationRow>(`/knowledge/escalations/${id}/close/`, {
    method: 'POST',
    body: {},
  });
