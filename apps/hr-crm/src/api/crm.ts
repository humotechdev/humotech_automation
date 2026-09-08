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
  /** Заявки одного сотрудника. Фильтр сужает уже разрешённое. */
  employee_id?: string;
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
  /**
   * Результаты проверок — признаками, а не координатами.
   *
   * `null` означает «проверка не проводилась» и это НЕ то же самое,
   * что «не прошла»: у ручной отметки кадровика геопроверки нет вовсе.
   * Широта и долгота наружу не отдаются — карточке достаточно ответа
   * «внутри или снаружи».
   */
  inside_geofence: boolean | null;
  inside_office_network: boolean | null;
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

// --- аналитика: доли с числителем и знаменателем ---------------------------

export type Ratio = {
  key: string;
  title: string;
  /** `null` — знаменатель нулевой. Это НЕ ноль процентов. */
  percent: number | null;
  numerator: number;
  denominator: number;
  formula: string;
  unit: 'days' | 'hours' | 'people' | string;
};

export type Report = {
  scope: { kind: string; id: string | null; name: string };
  period: { first: string; last: string; timezone: string };
  generated_at: string;
  headcount: number;
  coverage: { employees_total: number; employees_with_schedule: number; percent: number | null; note: string };
  totals: Record<string, number>;
  ratios: Ratio[];
  series: DayPoint[];
};

export const report = (
  params: { date_from: string; date_to: string; office_id?: string; region_id?: string },
  signal?: AbortSignal,
) => request<Report>(`/analytics${query(params)}`, signal ? { signal } : {});

export type Difference = {
  key: string;
  title: string;
  left: Ratio;
  right: Ratio;
  /** Разница в процентных ПУНКТАХ, а не в процентах. */
  points: number | null;
  comparable: boolean;
};

export type Comparison = {
  kind: string;
  left: Report;
  right: Report;
  differences: Difference[];
};

export const compare = (
  params: {
    kind: 'office' | 'region' | 'period';
    date_from: string;
    date_to: string;
    left_id?: string;
    right_id?: string;
    right_first?: string;
    right_last?: string;
  },
  signal?: AbortSignal,
) => request<Comparison>(`/analytics/compare${query(params)}`, signal ? { signal } : {});

// --- выгрузки --------------------------------------------------------------

/**
 * Состояние задания очереди — ровно то, что отдаёт `/export-jobs/`.
 *
 * `total_rows` объявлено полем ответа, но исполнитель его нигде не
 * пишет, а `progress_rows` считается только у CSV. Процента готовности
 * из этого не получится, и выдумывать его нельзя: «43%» рядом с
 * неизвестной величиной — это не оценка, а неправда.
 */
export type ExportJob = {
  id: string;
  kind: string;
  fmt: 'csv' | 'xlsx';
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED';
  filters: Record<string, string> | null;
  requested_by_user_id: string;
  requested_by: string | null;
  attempts: number;
  progress_rows: number;
  total_rows: number | null;
  file_name: string | null;
  size_bytes: number | null;
  expires_at: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ExportCounts = {
  total: number;
  QUEUED: number;
  RUNNING: number;
  SUCCEEDED: number;
  FAILED: number;
  CANCELLED: number;
};

export type ExportQuery = {
  status?: string;
  kind?: string;
  mine_only?: string;
  limit?: string;
  cursor?: string;
};

export const exportJobs = (params: ExportQuery, signal?: AbortSignal) =>
  request<Cursored<ExportJob>>(`/export-jobs/${query(params)}`, signal ? { signal } : {});

export const exportCounts = (params: ExportQuery, signal?: AbortSignal) =>
  request<ExportCounts>(
    `/export-jobs/counts/${query({ kind: params.kind, mine_only: params.mine_only })}`,
    signal ? { signal } : {},
  );

export const exportJob = (id: string, signal?: AbortSignal) =>
  request<ExportJob>(`/export-jobs/${id}/`, signal ? { signal } : {});

export type ExportOrder = {
  kind: string;
  fmt: 'csv' | 'xlsx';
  date?: string;
  date_from?: string;
  date_to?: string;
  office_id?: string;
  region_id?: string;
};

export const orderExport = (body: ExportOrder) =>
  request<ExportJob>('/export-jobs/', { method: 'POST', body });

export const cancelExport = (id: string) =>
  request<ExportJob>(`/export-jobs/${id}/cancel/`, { method: 'POST', body: {} });

export const retryExport = (id: string) =>
  request<ExportJob>(`/export-jobs/${id}/retry/`, { method: 'POST', body: {} });

/**
 * Адрес готового файла.
 *
 * Файл забирает сам браузер по обычной ссылке: сессионная cookie уходит
 * вместе с запросом, а `Content-Disposition: attachment` от сервера сам
 * открывает сохранение — с тем именем, которое сервер и придумал.
 * Читать файл в память через `fetch` ради этого незачем: выгрузка за год
 * весит десятки мегабайт, и держать их в heap вкладки нет причины.
 */
export const downloadUrl = (id: string): string =>
  `${apiBase()}/export-jobs/${id}/download/`;

function apiBase(): string {
  return (import.meta.env['VITE_API_URL'] as string | undefined) ?? '/api/v1';
}

// --- база знаний -----------------------------------------------------------

/**
 * Документ в списке — без текста: он приходит с карточкой.
 *
 * Строка списка — это ДОКУМЕНТ, а не версия: сервер отдаёт самую новую
 * версию каждой линейки «заголовок + язык». История версий — отдельный
 * запрос.
 */
export type SourceRow = {
  id: string;
  title: string;
  source_type: 'FAQ' | 'POLICY' | 'INSTRUCTION' | 'DOCUMENT';
  language: string;
  status: 'DRAFT' | 'INDEXING' | 'ACTIVE' | 'ARCHIVED' | 'ERROR';
  version: number;
  priority: number;
  office_id: string | null;
  region_id: string | null;
  department_id: string | null;
  office_name: string | null;
  region_name: string | null;
  created_by: string | null;
  effective_from: string | null;
  effective_to: string | null;
  parent_source_id: string | null;
  created_at: string;
  updated_at: string;
};

export type Source = SourceRow & { content: string };

export type SourceCounts = {
  total: number;
  DRAFT: number;
  INDEXING: number;
  ACTIVE: number;
  ARCHIVED: number;
  ERROR: number;
};

export type SourceQuery = {
  status?: string;
  search?: string;
  office_id?: string;
  region_id?: string;
  language?: string;
  limit?: string;
  cursor?: string;
};

export const sources = (params: SourceQuery, signal?: AbortSignal) =>
  request<Cursored<SourceRow>>(
    `/knowledge/sources/${query(params)}`,
    signal ? { signal } : {},
  );

export const sourceCounts = (params: SourceQuery, signal?: AbortSignal) =>
  request<SourceCounts>(
    `/knowledge/sources/counts/${query({ ...params, status: undefined })}`,
    signal ? { signal } : {},
  );

export const source = (id: string, signal?: AbortSignal) =>
  request<Source>(`/knowledge/sources/${id}/`, signal ? { signal } : {});

export const sourceVersions = (id: string, signal?: AbortSignal) =>
  request<SourceRow[]>(
    `/knowledge/sources/${id}/versions/`,
    signal ? { signal } : {},
  );

export type SourceDraft = {
  title: string;
  source_type: string;
  language: string;
  content: string;
  office_id?: string;
  region_id?: string;
  parent_source_id?: string;
};

export const createSource = (body: SourceDraft) =>
  request<Source>('/knowledge/sources/', { method: 'POST', body });

export const updateSource = (id: string, body: { title?: string; content?: string }) =>
  request<Source>(`/knowledge/sources/${id}/`, { method: 'PATCH', body });

export const archiveSource = (id: string) =>
  request<Source>(`/knowledge/sources/${id}/archive/`, { method: 'POST', body: {} });

/**
 * Поставить документ на индексацию.
 *
 * Сервер отказывает кодами `ai_disabled` и `provider_not_configured` и
 * НЕ ставит задание в очередь: висящее в QUEUED выглядело бы принятым.
 */
export const indexSource = (id: string) =>
  request<{ status: string }>(`/knowledge/sources/${id}/index/`, {
    method: 'POST',
    body: {},
  });

export const publishSource = (id: string) =>
  request<Source>(`/knowledge/sources/${id}/publish/`, { method: 'POST', body: {} });

/**
 * Можно ли сейчас индексировать и включать материалы в автоответы.
 *
 * Настройки провайдера сюда не приходят и не должны: интерфейсу нужно
 * знать «нельзя и почему», а не чем именно не настроено.
 */
export type Capability = {
  embeddings_available: boolean;
  reason: 'ai_disabled' | 'provider_not_configured' | null;
};

export const knowledgeCapability = (signal?: AbortSignal) =>
  request<Capability>('/knowledge/sources/capability/', signal ? { signal } : {});

export type FaqRow = {
  id: string;
  canonical_question: string;
  approved_answer: string;
  language: string;
  status: 'DRAFT' | 'ACTIVE' | 'ARCHIVED';
  priority: number;
  office_id: string | null;
  region_id: string | null;
  source_id: string | null;
  office_name: string | null;
  region_name: string | null;
  source_title: string | null;
  created_by: string | null;
  indexed: boolean;
  created_at: string;
  updated_at: string;
};

export type FaqCounts = {
  total: number;
  DRAFT: number;
  ACTIVE: number;
  ARCHIVED: number;
};

export type FaqQuery = {
  status?: string;
  search?: string;
  office_id?: string;
  region_id?: string;
  source_id?: string;
  limit?: string;
  cursor?: string;
};

export const faqList = (params: FaqQuery, signal?: AbortSignal) =>
  request<Cursored<FaqRow>>(`/knowledge/faq/${query(params)}`, signal ? { signal } : {});

export const faqCounts = (params: FaqQuery, signal?: AbortSignal) =>
  request<FaqCounts>(
    `/knowledge/faq/counts/${query({ ...params, status: undefined })}`,
    signal ? { signal } : {},
  );

export const faqItem = (id: string, signal?: AbortSignal) =>
  request<FaqRow>(`/knowledge/faq/${id}/`, signal ? { signal } : {});

export const createFaq = (body: {
  canonical_question: string;
  approved_answer: string;
  language: string;
  source_id?: string;
  office_id?: string;
  region_id?: string;
}) => request<FaqRow>('/knowledge/faq/', { method: 'POST', body });

export const updateFaq = (
  id: string,
  body: { canonical_question?: string; approved_answer?: string },
) => request<FaqRow>(`/knowledge/faq/${id}/`, { method: 'PATCH', body });

export const archiveFaq = (id: string) =>
  request<FaqRow>(`/knowledge/faq/${id}/archive/`, { method: 'POST', body: {} });

export const activateFaq = (id: string) =>
  request<FaqRow>(`/knowledge/faq/${id}/activate/`, { method: 'POST', body: {} });

// --- уведомления ------------------------------------------------------------

export type Notification = {
  id: string;
  employee_id: string;
  employee: { id: string; full_name: string; employee_number: string | null };
  /** Офис получателя НА МОМЕНТ уведомления, а не сегодняшний. */
  office_id: string | null;
  office_name: string | null;
  region_name: string | null;
  channel: 'TELEGRAM' | 'EMAIL' | 'PUSH' | 'IN_APP';
  notification_type: string;
  title: string | null;
  body: string;
  status: 'PENDING' | 'RUNNING' | 'SENT' | 'FAILED' | 'CANCELLED' | 'READ';
  attempts: number;
  error_message: string | null;
  scheduled_at: string | null;
  next_attempt_at: string | null;
  sent_at: string | null;
  read_at: string | null;
  related_entity_type: string | null;
  related_entity_id: string | null;
  created_at: string;
  updated_at: string;
  /** Что разрешает ТЕКУЩЕЕ состояние. Считает сервер, не интерфейс. */
  can_retry: boolean;
  can_cancel: boolean;
};

export type NotificationCounts = {
  total: number;
  PENDING: number;
  RUNNING: number;
  SENT: number;
  FAILED: number;
  CANCELLED: number;
  READ: number;
  sent: number;
  queued: number;
  failed: number;
  cancelled: number;
  /** Пояс организации: в нём показывается время и режется период. */
  timezone: string;
};

export type NotificationQuery = {
  status?: string;
  search?: string;
  office_id?: string;
  region_id?: string;
  employee_id?: string;
  channel?: string;
  date_from?: string;
  date_to?: string;
  limit?: string;
  cursor?: string;
};

export const notifications = (params: NotificationQuery, signal?: AbortSignal) =>
  request<Cursored<Notification>>(
    `/notifications/${query(params)}`,
    signal ? { signal } : {},
  );

export const notificationCounts = (
  params: NotificationQuery,
  signal?: AbortSignal,
) =>
  request<NotificationCounts>(
    `/notifications/counts/${query({ ...params, status: undefined })}`,
    signal ? { signal } : {},
  );

export const notification = (id: string, signal?: AbortSignal) =>
  request<Notification>(`/notifications/${id}/`, signal ? { signal } : {});

/** Одна СОСТОЯВШАЯСЯ попытка отправки. */
export type Attempt = {
  number: number;
  attempted_at: string;
  outcome: 'SENT' | 'FAILED' | 'CANCELLED';
  reason: string | null;
};

export type Attempts = {
  items: Attempt[];
  /** Велась ли история для этой строки. false — уведомление старше истории. */
  kept: boolean;
};

export const notificationAttempts = (id: string, signal?: AbortSignal) =>
  request<Attempts>(`/notifications/${id}/attempts/`, signal ? { signal } : {});

export const retryNotification = (id: string) =>
  request<Notification>(`/notifications/${id}/retry/`, {
    method: 'POST',
    body: {},
  });

export const cancelNotification = (id: string) =>
  request<Notification>(`/notifications/${id}/cancel/`, {
    method: 'POST',
    body: {},
  });

// --- администрирование: учётные записи, роли, журнал ------------------------

/** Назначение в строке списка: роль и область, без подробностей. */
export type ShortGrant = {
  id: string;
  role_id: string;
  role_name: string;
  role_code: string;
  region_id: string | null;
  region_name: string | null;
  office_id: string | null;
  office_name: string | null;
};

export type CrmUser = {
  id: string;
  email: string;
  status: 'ACTIVE' | 'INACTIVE' | 'LOCKED';
  mfa_enabled: boolean;
  employee_id: string | null;
  /** Имя есть у СОТРУДНИКА. У технической записи его нет вовсе. */
  full_name: string | null;
  last_login: string | null;
  created_at: string;
  updated_at: string;
  /** Только ДЕЙСТВУЮЩИЕ назначения. Истёкшие и отозванные сюда не идут. */
  active_grants: ShortGrant[];
  /** false — назначения не показаны из-за прав, а не отсутствуют. */
  grants_visible: boolean;
};

export type CrmUserCounts = {
  active: number;
  inactive: number;
  total: number;
  /** null — каталог ролей смотрящему не показывают. */
  roles: number | null;
};

export type CrmUserQuery = {
  search?: string;
  status?: string;
  role_id?: string;
  limit?: string;
  cursor?: string;
};

export const crmUsers = (params: CrmUserQuery, signal?: AbortSignal) =>
  request<Cursored<CrmUser>>(`/users/${query(params)}`, signal ? { signal } : {});

export const crmUserCounts = (params: CrmUserQuery, signal?: AbortSignal) =>
  request<CrmUserCounts>(
    `/users/counts/${query({ ...params, status: undefined })}`,
    signal ? { signal } : {},
  );

export const crmUser = (id: string, signal?: AbortSignal) =>
  request<CrmUser>(`/users/${id}/`, signal ? { signal } : {});

export const createCrmUser = (body: { email: string; employee_id?: string }) =>
  request<CrmUser>('/users/', { method: 'POST', body });

export const updateCrmUser = (
  id: string,
  body: { email?: string; employee_id?: string; unlink_employee?: boolean },
) => request<CrmUser>(`/users/${id}/`, { method: 'PATCH', body });

/**
 * Пароль уходит только сюда и только один раз.
 *
 * Обратно он не приходит ни в каком виде: ответ — это учётная запись,
 * в которой пароля нет и не бывает.
 */
export const setCrmUserPassword = (id: string, password: string) =>
  request<CrmUser>(`/users/${id}/set-password/`, {
    method: 'POST',
    body: { password },
  });

export const activateCrmUser = (id: string) =>
  request<CrmUser>(`/users/${id}/activate/`, { method: 'POST', body: {} });

export const deactivateCrmUser = (id: string) =>
  request<CrmUser>(`/users/${id}/deactivate/`, { method: 'POST', body: {} });

export type RoleFull = {
  id: string;
  code: string;
  name: string;
  description: string | null;
  is_system: boolean;
  permissions: string[];
  /** Может ли ЭТОТ пользователь выдать роль. Считает сервер. */
  grantable: boolean;
  missing_permissions: string[];
  /** Редакция: возвращается в PATCH, чтобы не затереть чужую правку. */
  updated_at: string;
};

export type PermissionRow = {
  code: string;
  name: string;
  description: string | null;
};

export const roles = (signal?: AbortSignal) =>
  request<{ items: RoleFull[] }>('/roles', signal ? { signal } : {});

export const role = (id: string, signal?: AbortSignal) =>
  request<RoleFull>(`/roles/${id}`, signal ? { signal } : {});

export const permissionCatalog = (signal?: AbortSignal) =>
  request<{ items: PermissionRow[] }>('/permissions', signal ? { signal } : {});

export const createRole = (body: {
  code: string;
  name: string;
  description?: string;
  permissions?: string[];
}) => request<RoleFull>('/roles', { method: 'POST', body });

export const updateRole = (
  id: string,
  body: {
    name?: string;
    description?: string;
    permissions?: string[];
    expected_updated_at?: string;
  },
) => request<RoleFull>(`/roles/${id}`, { method: 'PATCH', body });

/** Назначение роли с областью и сроком. */
export type Grant = {
  id: string;
  user_id: string;
  role_id: string;
  role_code: string;
  role_name: string;
  region_id: string | null;
  region_name: string | null;
  office_id: string | null;
  office_name: string | null;
  valid_from: string;
  valid_to: string | null;
};

export const userGrants = (
  userId: string,
  history: boolean,
  signal?: AbortSignal,
) =>
  request<{ items: Grant[] }>(
    `/users/${userId}/grants${history ? '?history=true' : ''}`,
    signal ? { signal } : {},
  );

/**
 * Области, которые ЭТОТ пользователь вправе указать в назначении.
 *
 * Не справочник `/regions/` и не `/offices/`: те читаются по
 * `regions.read` и `offices.read`, а роли выдаёт тот, у кого
 * `roles.manage`. У технического администратора `regions.read` нет
 * вовсе, и собирать регионы из видимых офисов, как делала форма
 * раньше, значит терять регион без офисов — вместе с возможностью
 * выдать назначение на него.
 *
 * `all_organization` — отдельный вопрос: вправе ли этот пользователь
 * выдать назначение без региона и офиса. Несколько ограниченных
 * областей такого права не дают.
 */
export type AssignableScopes = {
  all_organization: boolean;
  regions: Array<{ id: string; name: string }>;
  offices: Array<{ id: string; name: string; region_id: string | null }>;
};

export const assignableScopes = (signal?: AbortSignal) =>
  request<AssignableScopes>('/grants/scopes', signal ? { signal } : {});

export const assignRole = (body: {
  user_id: string;
  role_id: string;
  region_id?: string | null;
  office_id?: string | null;
  valid_from?: string | null;
  /**
   * Календарная дата «действует по», включительно, `YYYY-MM-DD`.
   *
   * Конец суток ставит сервер в поясе организации. Считать его здесь
   * нельзя: `new Date('2026-12-31T23:59:59')` разбирается в поясе
   * БРАУЗЕРА, и один и тот же выбранный день у двух администраторов из
   * разных городов дал бы разные моменты.
   */
  valid_to_date?: string;
  valid_to?: string | null;
}) => request<Grant>('/grants', { method: 'POST', body });

export const setGrantValidity = (
  id: string,
  body: {
    /** Точный момент. Взаимоисключающ с `valid_to_date`. */
    valid_to?: string | null;
    /** Календарная дата, `YYYY-MM-DD`: границу суток ставит сервер. */
    valid_to_date?: string;
    expected_valid_to?: string | null;
    check_expected?: boolean;
  },
) => request<Grant>(`/grants/${id}`, { method: 'PATCH', body });

export const revokeGrant = (id: string) =>
  request<Grant>(`/grants/${id}`, { method: 'DELETE' });

export type AuditEntry = {
  id: string;
  action: string;
  entity_type: string;
  entity_id: string;
  occurred_at: string;
  actor_user_id: string | null;
  actor_email: string | null;
  actor_employee_id: string | null;
  old_values: Record<string, unknown> | null;
  new_values: Record<string, unknown> | null;
  ip_address: string | null;
  user_agent: string | null;
};

export type AuditQuery = {
  action?: string;
  entity_type?: string;
  entity_id?: string;
  /** Несколько объектов через запятую: история назначений одного человека. */
  entity_ids?: string;
  /**
   * Всё, что связано с одним сотрудником: он сам, его назначения,
   * графики и заявки. Связанные объекты подбирает СЕРВЕР — клиенту
   * незачем знать их идентификаторы заранее и уж точно незачем
   * вычитывать журнал организации, чтобы отобрать в браузере.
   */
  employee_id?: string;
  actor_user_id?: string;
  date_from?: string;
  date_to?: string;
  limit?: string;
  cursor?: string;
};

export const auditLogs = (params: AuditQuery, signal?: AbortSignal) =>
  request<Cursored<AuditEntry>>(
    `/audit-logs${query(params)}`,
    signal ? { signal } : {},
  );

// --- настройки организации ---------------------------------------------------

/**
 * Одна группа настроек.
 *
 * `values` — то, что записано и что можно менять. `effective` — то, что
 * действует на самом деле: пустой `crm_timezone` означает не «UTC», а
 * «как у первого офиса», и по одним `values` этого не прочесть.
 *
 * `updated_at` возвращается обратно в `expected_updated_at` при
 * сохранении: без него второй администратор, открывший ту же страницу,
 * молча отменил бы работу первого.
 */
export type SettingSection = {
  key: string;
  title: string;
  description: string;
  values: Record<string, unknown>;
  defaults: Record<string, unknown>;
  help: Record<string, string>;
  effective?: Record<string, unknown>;
  updated_at: string | null;
};

/** Настройка, которую ищут здесь, а живёт она у офиса, графика или развёртывания. */
export type SettingElsewhere = {
  name: string;
  owner: string;
  hint: string;
};

export type LastChange = {
  at: string;
  action: string;
  actor_email: string | null;
};

export type SettingsAll = {
  items: SettingSection[];
  elsewhere: SettingElsewhere[];
  /** `null` и когда записи нет, и когда у смотрящего нет `audit.read`. */
  last_change: LastChange | null;
};

export const settings = (signal?: AbortSignal) =>
  request<SettingsAll>('/settings', signal ? { signal } : {});

export const saveSettings = (
  key: string,
  values: Record<string, unknown>,
  expected: string | null,
) =>
  request<SettingSection>(`/settings/${key}`, {
    method: 'PATCH',
    body: {
      values,
      expected_updated_at: expected,
      // Отдельный признак: у ненастроенной группы редакции нет вовсе,
      // и `null` здесь — законное значение, а не «не передали».
      check_expected: true,
    },
  });

/**
 * Состояние подключения.
 *
 * `configured` и `state` — разные вопросы. Заполненная переменная
 * окружения означает, что администратор что-то ввёл, и ничего не
 * говорит о том, отвечает ли сервис.
 */
export type Integration = {
  key: string;
  title: string;
  configured: boolean;
  state: 'working' | 'unknown' | 'off';
  note: string;
  confirmed_at: string | null;
  queued: number | null;
  link: string | null;
};

export const integrations = (signal?: AbortSignal) =>
  request<Items<Integration>>('/settings/integrations', signal ? { signal } : {});


// --- посещаемость сотрудника по дням -----------------------------------------

/**
 * Строка дня. Все величины посчитал сервер тем же кодом, что и присутствие
 * на дашборде: у ночной смены, открытой сессии и допуска опоздания должен
 * быть один ответ, а не два похожих.
 */
export type DailyRow = {
  day: string;
  /** Пояс ОФИСА, по которому определён этот день. */
  timezone: string;
  office_id: string | null;
  office_name: string | null;
  /**
   * `IN_OFFICE`, `LEFT`, `NOT_COME`, `DAY_OFF`, `NO_SCHEDULE`,
   * `SICK_LEAVE`, `VACATION`, `OTHER_ABSENCE`. «Нет графика» и
   * «выходной» — разные состояния, и ни одно из них не равно прогулу.
   */
  state: string;
  first_entry_at: string | null;
  last_exit_at: string | null;
  /** Сумма учитываемых интервалов, а не разница входа и выхода. */
  seconds: number;
  sessions: number;
  open_session_id: string | null;
  /** Минуты СВЕРХ допуска. `null` — сравнивать не с чем. */
  late_minutes: number | null;
  scheduled_start: string | null;
  absence_code: string | null;
  absence_name: string | null;
  conflicting_marks: boolean;
};

/** Итоги за ВЕСЬ период, а не за показанные строки. */
export type DailyTotals = {
  seconds: number;
  days_with_marks: number;
  working_days: number;
  late_days: number;
  late_minutes: number;
  open_sessions: number;
};

export type DailyReport = {
  employee_id: string;
  timezone: string;
  first: string;
  last: string;
  days: DailyRow[];
  totals: DailyTotals;
  note: string | null;
};

export const attendanceDaily = (
  params: { employee_id: string; date_from: string; date_to: string },
  signal?: AbortSignal,
) =>
  request<DailyReport>(
    `/attendance/daily${query(params)}`,
    signal ? { signal } : {},
  );


// --- график работы -----------------------------------------------------------

/** День недели графика. `weekday` — 1 (понедельник) … 7 (воскресенье). */
export type ScheduleDay = {
  weekday: number;
  is_working_day: boolean;
  start_time: string | null;
  end_time: string | null;
};

export type WorkScheduleDetail = {
  id: string;
  name: string;
  timezone: string;
  weekly_minutes: number;
  late_grace_minutes: number | null;
  early_leave_grace_minutes: number | null;
  is_flexible: boolean;
  status: string;
  days: ScheduleDay[];
};

/** Назначение графика сотруднику: период действия и ссылка на график. */
export type ScheduleAssignment = {
  id: string;
  employee_id: string;
  schedule_id: string;
  schedule_name: string | null;
  valid_from: string;
  valid_to: string | null;
};

export const workSchedule = (id: string, signal?: AbortSignal) =>
  request<WorkScheduleDetail>(
    `/work-schedules/${id}/`,
    signal ? { signal } : {},
  );
