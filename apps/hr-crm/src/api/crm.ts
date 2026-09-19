/**
 * Запросы главной страницы. Адреса и поля — из существующего backend,
 * ничего не выдумано: `/dashboard` уже отдаёт карточки вместе с адресом
 * списка, из которого сложилось каждое число, `/analytics` — ряд по дням,
 * `/attendance/presence` — состав смены целиком, без страниц.
 *
 * Коллекции DRF-роутера требуют завершающей косой черты: без неё backend
 * отвечает 301, а браузер теряет заголовки при переадресации.
 */

import { apiUrl, request, upload } from './client';

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

// --- обзор аналитики -------------------------------------------------------

/** Доля с числителем и знаменателем. `percent: null` — знаменатель ноль. */
export type Share = { numerator: number; denominator: number; percent: number | null };

export type OverviewDay = {
  day: string;
  /** 1 — понедельник … 7 — воскресенье. */
  weekday: number;
  /** Рабочий ли день: по графику кого-то ждали. */
  working: boolean;
  /** День ещё не наступил. */
  future: boolean;
  /** Входит ли день в детализацию по дню недели. */
  in_detail: boolean;
  expected: number;
  attended: number;
  percent: number | null;
  on_time: number;
  on_time_percent: number | null;
  late: number;
  missed: number;
  vacation: number;
  sick_leave: number;
  other_absence: number;
  average_seconds: number | null;
};

export type OverviewOffice = {
  id: string;
  name: string;
  position: number;
  attendance: Share;
  previous_attendance: Share;
  difference_points: number | null;
};

/** Строка рейтинга сотрудников: явка и опоздания за период. */
export type OverviewPerson = {
  id: string;
  name: string;
  attendance: Share;
  late_days: number;
  /** Минуты сверх допуска, а не вся разница со временем начала смены. */
  late_minutes: number;
  missed_days: number;
};

export type ArrivalBucket = {
  /** Минуты от начала личной смены: −60 … +90. */
  from: number;
  to: number;
  /** До начала смены. */
  early: number;
  /** После начала, но в пределах допуска. */
  grace: number;
  /** Позже допуска. */
  late: number;
};

export type Overview = {
  period: { first: string; last: string; days: number };
  previous_period: { first: string; last: string };
  weekday: number | null;
  generated_at: string;
  timezones: string[];
  summary: {
    attendance: Share;
    previous_attendance: Share;
    difference_points: number | null;
    on_time: Share;
    late: Share;
    /** Только по дням с закрытыми посещениями. */
    average_seconds: number | null;
    open_sessions: number;
    missed_days: number;
    vacation_days: number;
    sick_leave_days: number;
    other_absence_days: number;
  };
  days: OverviewDay[];
  previous_days: { day: string; attended: number; expected: number; percent: number | null }[];
  offices: OverviewOffice[];
  /** Та же явка уровнем выше: регионы собраны из своих офисов. */
  regions: OverviewOffice[];
  /** Худшая явка сверху: страницу открывают, чтобы найти проблему. */
  employees: OverviewPerson[];
  arrivals: {
    bucket_minutes: number;
    from_minutes: number;
    to_minutes: number;
    buckets: ArrivalBucket[];
    start_time: string | null;
    uniform_start: boolean;
    /** Медиана времени первого входа, минуты от полуночи. */
    median_minutes: number | null;
    after_start: number;
    late: Share;
  };
  weekdays: {
    days: { weekday: number; attendance: Share; on_time: Share; average_seconds: number | null }[];
    best: number | null;
  };
};

export type OverviewQuery = {
  date_from: string;
  date_to: string;
  region_id?: string;
  office_id?: string;
  /** Отдел и сотрудник сужают состав, а не пересчитывают правила. */
  department_id?: string;
  employee_id?: string;
  weekday?: string;
};

// --- движение сотрудников --------------------------------------------------

/** Итоги одного периода: сколько пришло, сколько ушло, что осталось. */
export type MovementSpan = {
  first: string;
  last: string;
  hired: number;
  left: number;
  /** Чистое изменение. Отрицательное — людей стало меньше. */
  difference: number;
};

export type MovementReport = {
  current: MovementSpan;
  /** Столько же дней непосредственно перед периодом. */
  previous: MovementSpan;
  month_before: MovementSpan;
  year_before: MovementSpan;
  /** Сколько человек числится на конец периода. */
  headcount: number;
};

/**
 * Движение сотрудников.
 *
 * Отдельно от посещаемости: там единица измерения — дни, здесь — люди,
 * и складывать их в одном блоке значит путать два разных вопроса.
 */
export const analyticsMovement = (
  params: { date_from: string; date_to: string; region_id?: string; office_id?: string },
  signal?: AbortSignal,
) => request<MovementReport>(`/analytics/movement${query(params)}`, signal ? { signal } : {});

export const analyticsOverview = (params: OverviewQuery, signal?: AbortSignal) =>
  request<Overview>(`/analytics/overview${query(params)}`, signal ? { signal } : {});

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
  /** Когда приняли решение. У ждущих заявок пусто. */
  reviewed_at?: string | null;
  /** Нужна ли справка по этому типу отсутствия. */
  requires_document?: boolean;
  documents?: { id: string; verification_status: string }[];
};

export const pendingAbsences = (signal?: AbortSignal) =>
  request<{ requests: AbsenceRequestRow[] }>(
    '/absence-requests/pending',
    signal ? { signal } : {},
  );

/**
 * Исправления отметок, ждущие решения.
 *
 * Статусы перечислены те, что есть у модели. Раньше здесь стоял
 * `status=PENDING` — такого состояния у исправлений не бывает вовсе, и
 * счётчик на главной всегда показывал ноль независимо от очереди.
 */
export const corrections = (signal?: AbortSignal) =>
  request<Items<{ id: string; status: string }>>(
    '/attendance/corrections?status=SUBMITTED,IN_REVIEW',
    signal ? { signal } : {},
  );

export type Invitation = { id: string; status: string; employee_name?: string };

export const invitations = (signal?: AbortSignal) =>
  request<Items<Invitation>>('/telegram/invitations/', signal ? { signal } : {});

/**
 * Обращения, которые ждут кадровика, — для главной.
 *
 * Состояния заданы явно: без них сервер отдаёт очередь целиком, вместе
 * с закрытыми, и счётчик на главной показывал бы архив.
 */
export const escalations = (signal?: AbortSignal) =>
  request<Cursored<QuestionRow>>(
    `/knowledge/escalations/${query({ status: 'NEW,IN_PROGRESS', limit: '50' })}`,
    signal ? { signal } : {},
  );

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
  /** Рабочие дни недели, 1 — понедельник. Из самого графика, а не из названия. */
  weekdays?: number[];
  start_time?: string | null;
  end_time?: string | null;
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
  /** Имя в Telegram. `null` — привязки нет вовсе. */
  telegram_username: string | null;
  birth_date: string | null;
  /** Есть ли снимок. Само изображение приходит отдельным адресом. */
  photo: boolean;
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
  /** Сдвиг для перехода на произвольную страницу. Курсор так не умеет:
   *  он отвечает «дальше вот этой записи», и до тридцать второй страницы
   *  им идут через тридцать одну. */
  offset?: string;
};

export const employees = (params: EmployeeQuery, signal?: AbortSignal) =>
  request<Cursored<EmployeeRow>>(`/employees/${query(params)}`, signal ? { signal } : {});

/** Минимальная строка для верхнего глобального поиска. Контактов здесь нет. */
export type EmployeeSearchResult = {
  id: string;
  employee_number: string;
  full_name: string;
  employment_status: string;
  photo: boolean;
  position_name: string | null;
  department_name: string | null;
  office_name: string | null;
  telegram_username: string | null;
};

export const searchEmployees = (q: string, signal?: AbortSignal) =>
  request<{ items: EmployeeSearchResult[] }>(
    `/employees/search/${query({ q })}`,
    signal ? { signal } : {},
  );

/** Счётчики вкладок. Состояние в параметры НЕ входит: иначе, выбрав
 *  «Активные», человек видел бы нули у остальных вкладок. */
/** Короткая сводка о сотруднике — для аватаров в правой колонке. */
export type EmployeeBrief = {
  id: string;
  full_name: string;
  employee_number: string | null;
  photo: boolean;
};

/** Новички, именинники и люди без графика — по всей видимой области. */
export type EmployeeHighlights = {
  recent_hires: number;
  recent: EmployeeBrief[];
  birthdays_today: number;
  birthdays: EmployeeBrief[];
  without_schedule: number;
  unscheduled: EmployeeBrief[];
};

export const employeeHighlights = (params: EmployeeQuery, signal?: AbortSignal) =>
  request<EmployeeHighlights>(
    `/employees/highlights/${query({ ...params, status: undefined, cursor: undefined, limit: undefined, offset: undefined })}`,
    signal ? { signal } : {},
  );

export const employeeCounts = (params: EmployeeQuery, signal?: AbortSignal) =>
  request<Record<string, number>>(
    `/employees/counts/${query({ ...params, status: undefined, cursor: undefined, limit: undefined })}`,
    signal ? { signal } : {},
  );

export const employee = (id: string, signal?: AbortSignal) =>
  request<Record<string, unknown>>(`/employees/${id}/`, signal ? { signal } : {});

/**
 * Правка карточки: только собственные данные человека.
 *
 * Офис, отдел, должность и график сюда не входят — у них есть период
 * действия, и меняются они переводом (`changeEmployeeAssignment`).
 * Сервер отвергнет весь запрос, если среди полей окажется чужое.
 */
export type EmployeeEdit = {
  first_name?: string;
  last_name?: string;
  middle_name?: string | null;
  phone?: string | null;
  corporate_email?: string | null;
  birth_date?: string | null;
  gender?: string | null;
  marital_status?: string | null;
};

export const updateEmployee = (id: string, body: EmployeeEdit) =>
  request<Record<string, unknown>>(`/employees/${id}/`, { method: 'PATCH', body });

/**
 * Перевод: другой офис, отдел, должность, руководитель или вид занятости.
 *
 * Это НОВЫЙ период назначения, а не правка прежнего: отметки за
 * прошлый месяц обязаны остаться отнесёнными к тому офису, где человек
 * тогда работал. Отсюда и `effective_from` — он должен быть позже
 * начала действующего назначения, иначе сервер откажет.
 */
/**
 * Приложить бумагу уже заведённому сотруднику.
 *
 * Файл сначала уходит `uploadEmployeeFile`, затем привязывается сюда:
 * до привязки это просто файл организации, ни на кого не ссылающийся.
 * Строка чек-листа заполняется, а не дублируется — на сотрудника
 * приходится одна бумага каждого вида, кроме «прочего».
 */
export const attachEmployeeDocument = (
  id: string,
  body: { kind: EmployeeDocumentKind; file_id: string; title?: string },
) => request<Record<string, unknown>>(`/employees/${id}/documents/`, {
  method: 'POST',
  body,
});

/** Снять файл с бумаги. Строка чек-листа остаётся пустой, «прочее» исчезает. */
export const removeEmployeeDocument = (id: string, documentId: string) =>
  request<void>(`/employees/${id}/documents/${documentId}/`, { method: 'DELETE' });

/** Заменить фотографию в карточке. Файл — из `uploadEmployeeFile`. */
export const setEmployeePhoto = (id: string, file_id: string) =>
  request<AttachedFile>(`/employees/${id}/photo-set/`, {
    method: 'POST',
    body: { file_id },
  });

export const changeEmployeeAssignment = (
  id: string,
  body: {
    effective_from: string;
    office_id?: string;
    department_id?: string | null;
    position_id?: string | null;
    manager_employee_id?: string | null;
    employment_type?: string;
  },
) => request<Assignment>(`/employees/${id}/change-assignment/`, {
  method: 'POST',
  body,
});

/**
 * Принять стажёра в штат.
 *
 * Должность передаётся, только если её пересмотрели по итогам
 * стажировки: перевод в штат сам по себе не повод её менять.
 */
export const promoteEmployee = (id: string, body: { position_id?: string } = {}) =>
  request<Record<string, unknown>>(`/employees/${id}/promote/`, {
    method: 'POST',
    body,
  });

/**
 * Завершить стажировку расставанием.
 *
 * Это увольнение с причиной «Не прошёл стажировку» — отдельного статуса
 * для такого случая нет и заводить его не нужно.
 */
export const endProbation = (id: string, body: { reason?: string } = {}) =>
  request<Record<string, unknown>>(`/employees/${id}/end-probation/`, {
    method: 'POST',
    body,
  });

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
/**
 * Выдать ссылку привязки Telegram.
 *
 * `replace` — это «отправить повторно»: действующая ссылка отзывается и
 * тут же выдаётся новая. Без него повтор упирается в ссылку, выданную
 * минуту назад при приёме сотрудника, и не срабатывает никогда.
 */
export const inviteToTelegram = (employee_id: string, replace = false) =>
  request<{ id: string; link?: string; url?: string; token?: string; expires_at?: string }>(
    '/telegram/invitations/',
    { method: 'POST', body: { employee_id, replace } },
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
  /** Что кадровик написал о справке. Уходит сотруднику дословно. */
  verification_comment?: string | null;
  file: {
    id: string;
    name: string;
    mime_type: string;
    size_bytes: number;
    uploaded_at: string;
    scan_status: string;
  };
};

/** Шаг истории заявки. Неизменяемая запись: только добавление. */
export type RequestStep = {
  at: string;
  /** `CREATED`, `SUBMITTED`, `APPROVED`, `DOCUMENT_ATTACHED` и так далее. */
  action: string;
  comment: string | null;
};

export type AbsenceRow = AbsenceRequestRow & {
  /** `CREATE`, `EXTEND` или `CANCEL`: отмена — тоже заявка, со ссылкой на исходную. */
  kind?: string;
  documents: DocumentRow[];
  requires_document: boolean;
  /** Что написал сам сотрудник. Диагноза здесь быть не должно. */
  comment: string | null;
  review_comment: string | null;
  history?: RequestStep[];
};

export type CorrectionRow = {
  id: string;
  status: string;
  employee?: { id: string; full_name: string; employee_number: string | null };
  employee_id?: string;
  reason?: string | null;
  requested_change?: Record<string, unknown> | null;
  requested_entry_at?: string | null;
  requested_exit_at?: string | null;
  submitted_at?: string | null;
  review_comment?: string | null;
  created_at?: string;
};

export type QueueItem = {
  kind: 'absence' | 'correction';
  id: string;
  created_at: string;
  /** Где работает сотрудник сейчас. `null` — назначения нет. */
  place?: { office_name: string | null; department_name: string | null } | null;
  absence?: AbsenceRow;
  correction?: CorrectionRow;
};

export type QueueQuery = {
  kind?: string;
  status?: string;
  type?: string;
  /** Вид заявки на отсутствие: `CREATE`, `EXTEND`, `CANCEL`. */
  request_kind?: string;
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

/**
 * Счётчики вкладок очереди одним ответом: `open`, `all`, `leave`, `sick`,
 * `fixes`, `cancel`. Считает сервер по тем же фильтрам — по одной строке
 * на вкладку их не посчитать, там видно только «есть или нет».
 */
export const queueCounts = (params: QueueQuery, signal?: AbortSignal) =>
  request<Record<string, number>>(
    `/requests/counts${query({ ...params, status: undefined, type: undefined, kind: undefined, request_kind: undefined, cursor: undefined, limit: undefined })}`,
    signal ? { signal } : {},
  );

/** Файл справки к заявке. Право спрашивается при каждом открытии. */
export const absenceDocumentUrl = (requestId: string, documentId: string) =>
  apiUrl(`/absence-requests/${requestId}/documents/${documentId}/download`);

/** Печатное заявление по заявке — тот же бланк, что видит сотрудник. */
export const absenceApplicationUrl = (requestId: string) =>
  apiUrl(`/absence-requests/${requestId}/application`);

/**
 * Решение по справке.
 *
 * Отдельно от решения по заявке: одобренный больничный с отклонённой
 * справкой — законное состояние, HR ждёт правильный документ, а человек
 * всё это время болеет, а не прогуливает.
 *
 * Причина при отказе обязательна — её проверяет сервер и отправляет
 * человеку дословно.
 */
export const decideAbsenceDocument = (
  requestId: string,
  documentId: string,
  decision: 'accept' | 'reject',
  comment?: string,
) =>
  request<{ id: string; verification_status: string; verification_comment: string | null }>(
    `/absence-requests/${requestId}/documents/${documentId}/${decision}`,
    { method: 'POST', body: comment ? { comment } : {} },
  );

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
  department_name: string | null;
  position_name: string | null;
  state: string;
  first_entry_at: string | null;
  last_exit_at: string | null;
  seconds: number;
  open_session_id: string | null;
  /** `null` — сравнивать не с чем, а не «не опоздал». */
  late_minutes: number | null;
  scheduled_start: string | null;
  /** Конец смены по графику. Без него правый край шкалы дня не построить. */
  scheduled_end: string | null;
  absence_code: string | null;
  absence_name: string | null;
  /**
   * Что человек сам сказал про день: `LATE` — задерживается, `ABSENT` —
   * не придёт, `null` — промолчал.
   *
   * Это предупреждение, а не оформленное отсутствие: отпуск и
   * больничный проходят согласование и живут своими заявками.
   */
  notice_kind: string | null;
  notice_comment: string | null;
  conflicting_marks: boolean;
  /**
   * Отрезки присутствия за день, по одному на сессию.
   *
   * Шкале рабочего дня их не собрать из первого входа и последнего
   * выхода: обед между ними пропал бы, а полоса соврала бы про него.
   * `ended_at: null` — сессия ещё открыта.
   */
  intervals: { started_at: string; ended_at: string | null; seconds: number }[];
  /** Хотя бы одна отметка дня пришла из-за границы геозоны офиса.
   *  Не нарушение само по себе — повод посмотреть. */
  outside_geofence: boolean;
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
  params: {
    date?: string;
    region_id?: string;
    office_id?: string;
    /** Отдел сотрудника. Сервер умел его давно — интерфейс не спрашивал. */
    department_id?: string;
    state?: string;
    search?: string;
  },
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
  updated_at?: string;
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

/**
 * Завести регион и офис.
 *
 * Кода в форме нет: его придумывает сервер. Человек опознаёт регион
 * названием, а офис — названием и адресом; `OFF-4` нужен уникальному
 * ключу и выгрузкам, и спрашивать его у кадровика незачем.
 */
export const createRegion = (body: { name: string; timezone?: string | null }) =>
  request<RegionFull>('/regions/', { method: 'POST', body });

export const updateRegion = (
  id: string,
  changes: { name?: string; timezone?: string | null },
) => request<RegionFull>(`/regions/${id}/`, { method: 'PATCH', body: changes });

/**
 * Часового пояса в теле нет: он один на страну и берётся у организации
 * на сервере. Передать его отсюда значило бы дать интерфейсу
 * возможность завести офис, живущий на час в стороне от остальных.
 */
export const createOffice = (body: {
  region_id: string;
  /** Пусто — офис назовётся по региону. */
  name?: string;
  /** Адрес задают в карточке офиса, а не при заведении. */
  address?: string;
  opened_at?: string;
}) => request<OfficeFull>('/offices/', { method: 'POST', body });

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
  description?: string | null;
  created_by_name?: string | null;
  created_at?: string;
  rotated_at?: string | null;
  token_version?: number;
  /** Попыток за сегодня в поясе офиса. `null` — не считали. */
  scans_today?: number | null;
  /** Когда сканировали в последний раз. `null` — ни разу. */
  last_scan_at?: string | null;
};

export const qrPoints = (params: { office_id?: string }, signal?: AbortSignal) =>
  request<Items<QrPoint>>(`/qr-points/${query(params)}`, signal ? { signal } : {});

/**
 * Точка вместе с секретом печатного кода. Секрет и ссылка приходят
 * ТОЛЬКО в ответе на выпуск и перевыпуск — повторно их не узнать.
 */
export type IssuedQrPoint = {
  point: QrPoint;
  static_token: string | null;
  sticker_link: string | null;
};

export const createQrPoint = (body: {
  office_id: string;
  /** Пусто — точка назовётся по своему типу: «Вход», «Выход». */
  name?: string;
  direction_mode: 'ENTRY' | 'EXIT' | 'BOTH';
  /**
   * Режим кода. `STATIC` — печатный лист на стене: секрет выдаётся один
   * раз, и по нему работает ссылка в Telegram. `ROTATING` — экран,
   * который сам меняет код, и ему нужен период смены.
   *
   * Передаётся всегда: без него сервер берёт `ROTATING` по умолчанию и
   * отказывает, потому что периода смены никто не прислал.
   */
  qr_mode?: 'STATIC' | 'ROTATING';
  rotation_seconds?: number;
  description?: string;
}) => request<IssuedQrPoint>('/qr-points/', {
  method: 'POST',
  body: { ...body, qr_mode: 'STATIC' },
});

export const updateQrPoint = (id: string, changes: { name?: string; description?: string; direction_mode?: string }) =>
  request<QrPoint>(`/qr-points/${id}/`, { method: 'PATCH', body: changes });

/**
 * Убрать точку совсем.
 *
 * Сервер откажет, если по ней уже отмечались, и объяснит почему: такая
 * точка — часть истории, её выключают, а не стирают.
 */
export const deleteQrPoint = (id: string) =>
  request<void>(`/qr-points/${id}/`, { method: 'DELETE' });

export const setQrPointActive = (id: string, active: boolean) =>
  request<QrPoint>(`/qr-points/${id}/${active ? 'activate' : 'deactivate'}/`, {
    method: 'POST',
    body: {},
  });

export const reissueQrPoint = (id: string) =>
  request<IssuedQrPoint>(`/qr-points/${id}/reissue-token/`, { method: 'POST', body: {} });

/**
 * Ссылка наклейки — посмотреть, скачать или распечатать код заново.
 *
 * Сервер откажет с объяснением, если точка выпущена до того, как коды
 * стали храниться: такой код не восстановить, его заменяют новым.
 */
export const qrPointSticker = (id: string, signal?: AbortSignal) =>
  request<{ sticker_link: string | null }>(
    `/qr-points/${id}/sticker/`,
    signal ? { signal } : {},
  );

export const office = (id: string, signal?: AbortSignal) =>
  request<OfficeFull>(`/offices/${id}/`, signal ? { signal } : {});

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

/*
 * Обращение — переписка сотрудника с HR через Telegram. Не заявка: отпуск
 * и больничный оформляются в «Заявках», здесь о них только спрашивают.
 *
 * Любое действие возвращает обращение целиком — с лентой и тем, что
 * с ним можно сделать дальше. Интерфейс не угадывает, что изменилось.
 */

export type QuestionStatus = 'NEW' | 'IN_PROGRESS' | 'WAITING_EMPLOYEE' | 'CLOSED';
export type QuestionPriority = 'LOW' | 'NORMAL' | 'HIGH' | 'URGENT';
export type QuestionCategory =
  | 'VACATION' | 'SICK_LEAVE' | 'ATTENDANCE' | 'SCHEDULE' | 'SALARY'
  | 'DOCUMENTS' | 'TELEGRAM' | 'OTHER';
export type Person = { id: string; name: string };

export type QuestionRow = {
  id: string;
  number: number;
  employee: { id: string; full_name: string; employee_number: string | null; has_photo: boolean };
  office: { id: string; name: string } | null;
  topic: string;
  /** Последнее сообщение сотрудника или HR, коротко. */
  snippet: string;
  last_message_kind: 'EMPLOYEE' | 'HR';
  category: QuestionCategory;
  priority: QuestionPriority;
  status: QuestionStatus;
  unread: boolean;
  /** Последнее слово за сотрудником, ответа на него не было. */
  awaiting_reply: boolean;
  due_at: string | null;
  overdue: boolean;
  last_message_at: string;
  created_at: string;
  assignee: Person | null;
};

export type DeliveryStatus = 'QUEUED' | 'DELIVERED' | 'READ' | 'FAILED' | 'UNKNOWN';

export type QuestionMessage = {
  id: string;
  kind: 'EMPLOYEE' | 'HR' | 'SYSTEM';
  source: 'TELEGRAM' | 'CRM' | 'SYSTEM';
  body: string | null;
  event: string | null;
  details: Record<string, unknown> | null;
  author: { type: 'user' | 'employee' | 'system'; id: string | null; name: string };
  created_at: string;
  delivery: {
    status: DeliveryStatus;
    sent_at: string | null;
    read_at: string | null;
    error: string | null;
  } | null;
};

export type KnowledgeRef = {
  id: string;
  title: string;
  source_type: string;
  version: number;
  status: string;
  published_at: string | null;
  updated_at: string;
};

export type QuestionDraft = {
  status: 'READY' | 'LOW_CONFIDENCE' | 'CONFLICT' | 'NO_SOURCES';
  text: string | null;
  /** Десятичная строка 0…1 — оценка поиска, а не самооценка модели. */
  confidence: string | null;
  generated_at: string | null;
  /** Документ черновика снят с публикации. */
  outdated: boolean;
  sources: KnowledgeRef[];
};

export type QuestionActions = Record<
  'take' | 'assign' | 'priority' | 'category' | 'wait' | 'close' | 'reopen' | 'reply' | 'draft',
  boolean
>;

export type Question = QuestionRow & {
  question_text: string;
  channel: string;
  first_response_at: string | null;
  closed_at: string | null;
  closed_by: Person | null;
  close_reason: string | null;
  telegram: { connected: boolean; reason: string | null; status: string | null };
  messages: QuestionMessage[];
  draft: QuestionDraft | null;
  actions: QuestionActions;
};

export type QuestionCounts = {
  statuses: Record<QuestionStatus, number>;
  total: number;
  quick: { all: number; unanswered: number; mine: number; urgent: number; unread: number };
};

export type QuestionQuery = {
  status?: string;
  office_id?: string;
  /** `me`, `none` или идентификатор пользователя. */
  assignee?: string;
  category?: string;
  priority?: string;
  date_from?: string;
  date_to?: string;
  search?: string;
  quick?: string;
  limit?: string;
  cursor?: string;
};

export type QuestionContext = {
  employee: {
    id: string;
    full_name: string;
    employee_number: string | null;
    employment_status: string;
    has_photo: boolean;
    position: string | null;
    department: string | null;
    office: { id: string; name: string } | null;
    schedule: { name: string; flexible: boolean; summary: string | null } | null;
    telegram: { connected: boolean; reason: string | null; status: string | null; username: string | null };
  };
  links: { employee_card: boolean; attendance: boolean; requests: boolean };
  /** `null` — нет права видеть раздел; пустой список — видеть можно, но нечего. */
  requests: {
    id: string; type: string; type_code: string; kind: string; status: string;
    start: string | null; end: string | null; created_at: string;
  }[] | null;
  balance: {
    type: string; year: number; allocated_days: number; used_days: number;
    reserved_days: number; available_days: number;
  }[] | null;
  corrections: {
    id: string; status: string; submitted_at: string;
    requested_entry_at: string | null; requested_exit_at: string | null; reason: string;
  }[] | null;
  documents: {
    id: string; title: string; kind: string; status: string; has_file: boolean; updated_at: string;
  }[] | null;
  history: {
    total: number;
    closed: number;
    open: number;
    recent: { id: string; number: number; topic: string; status: QuestionStatus; created_at: string }[];
  };
  materials: KnowledgeRef[] | null;
};

export type ReplyAfter = 'KEEP' | 'WAIT' | 'CLOSE';

const QUESTIONS = '/knowledge/escalations';

export const questionList = (params: QuestionQuery, signal?: AbortSignal) =>
  request<Cursored<QuestionRow>>(`${QUESTIONS}/${query(params)}`, signal ? { signal } : {});

/** Вкладки — без учёта состояния, быстрые фильтры — внутри выбранного. */
export const questionCounts = (params: QuestionQuery, signal?: AbortSignal) =>
  request<QuestionCounts>(
    `${QUESTIONS}/counts/${query({ ...params, quick: undefined, cursor: undefined, limit: undefined })}`,
    signal ? { signal } : {},
  );

export const questionAssignees = (signal?: AbortSignal) =>
  request<Items<Person>>(`${QUESTIONS}/assignees/`, signal ? { signal } : {});

export const question = (id: string, signal?: AbortSignal) =>
  request<Question>(`${QUESTIONS}/${id}/`, signal ? { signal } : {});

export const questionContext = (id: string, signal?: AbortSignal) =>
  request<QuestionContext>(`${QUESTIONS}/${id}/context/`, signal ? { signal } : {});

export const readQuestion = (id: string) =>
  request<{ id: string; unread: boolean }>(`${QUESTIONS}/${id}/read/`, { method: 'POST', body: {} });

const questionAction = (id: string, action: string, body: Record<string, unknown> = {}) =>
  request<Question>(`${QUESTIONS}/${id}/${action}/`, { method: 'POST', body });

export const takeQuestion = (id: string) => questionAction(id, 'take');
export const assignQuestion = (id: string, toUserId: string) =>
  questionAction(id, 'assign', { to_user_id: toUserId });
export const setQuestionPriority = (id: string, priority: QuestionPriority) =>
  questionAction(id, 'priority', { priority });
export const setQuestionCategory = (id: string, category: QuestionCategory) =>
  questionAction(id, 'category', { category });
export const waitForEmployee = (id: string) => questionAction(id, 'wait');
export const closeQuestion = (id: string, reason: string) =>
  questionAction(id, 'close', { reason });
export const reopenQuestion = (id: string) => questionAction(id, 'reopen');
export const refreshQuestionDraft = (id: string) => questionAction(id, 'draft');

/**
 * Ответ в Telegram. `client_request_id` — ключ повтора: второе нажатие
 * с тем же ключом не даёт второго сообщения человеку.
 */
export const replyQuestion = (
  id: string,
  body: { text: string; after: ReplyAfter; client_request_id: string; close_reason?: string },
) => questionAction(id, 'reply', body);

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
 * Процент готовности — `progress_done / progress_total`. Знаменатель
 * сервер пишет до первой строки, и только у заказов конструктора; у
 * старых заказов его нет, и процента на экране тоже нет.
 */
export type ExportJob = {
  id: string;
  kind: string;
  fmt: 'csv' | 'xlsx';
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED';
  /** SUCCEEDED с прошедшим сроком хранения приходит как EXPIRED. */
  display_status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED' | 'EXPIRED';
  filters: ExportFilters | null;
  title: string | null;
  progress_done: number;
  progress_total: number | null;
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

/** Параметры заказа. У заказа конструктора `builder === 2`. */
export type ExportFilters = {
  builder?: number;
  date?: string;
  date_from?: string;
  date_to?: string;
  period?: ReportPeriod;
  region_id?: string | null;
  office_id?: string | null;
  office_ids?: string[];
  department_ids?: string[];
  employee_id?: string | null;
  include_inactive?: boolean;
  fields?: string[];
  name?: string | null;
};

export type ExportCounts = {
  total: number;
  QUEUED: number;
  RUNNING: number;
  SUCCEEDED: number;
  FAILED: number;
  CANCELLED: number;
  EXPIRED: number;
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

/** Короткий заказ со страниц разделов: вид, формат и один офис или регион. */
export type SimpleExportOrder = {
  kind: string;
  fmt: 'csv' | 'xlsx';
  date?: string;
  date_from?: string;
  date_to?: string;
  office_id?: string;
  region_id?: string;
};

/** Заказ конструктора: поля, несколько офисов и отделов, ключ повтора. */
export type BuilderExportOrder = ReportSpec & {
  fmt: 'csv' | 'xlsx';
  builder: true;
  client_request_id: string;
};

export type ExportOrder = SimpleExportOrder | BuilderExportOrder;

export const orderExport = (body: ExportOrder) =>
  request<ExportJob>('/export-jobs/', { method: 'POST', body });

export const cancelExport = (id: string) =>
  request<ExportJob>(`/export-jobs/${id}/cancel/`, { method: 'POST', body: {} });

export const retryExport = (id: string) =>
  request<ExportJob>(`/export-jobs/${id}/retry/`, { method: 'POST', body: {} });

/** Убрать из своей истории. Файл сервер удаляет сразу. */
export const hideExport = (id: string) =>
  request<null>(`/export-jobs/${id}/hide/`, { method: 'POST', body: {} });

// --- конструктор отчётов ---------------------------------------------------

export type ReportKindKey = 'attendance' | 'worktime' | 'lateness' | 'absences' | 'employees';
export type ReportPeriod = 'custom' | 'this_month' | 'last_month';

export type ReportField = {
  key: string;
  title: string;
  default: boolean;
  columns: string[];
};

export type ReportCatalog = {
  kinds: { key: ReportKindKey; title: string; permission: string; fields: ReportField[] }[];
  max_period_days: number;
  xlsx_max_rows: number;
  retention_hours: number;
  preview_min_rows: number;
  preview_max_rows: number;
};

export type ReportSpec = {
  kind: ReportKindKey;
  date_from: string;
  date_to: string;
  period: ReportPeriod;
  region_id: string | null;
  office_ids: string[];
  department_ids: string[];
  employee_id: string | null;
  include_inactive: boolean;
  fields: string[];
  name: string | null;
};

export type ReportCell = { text: string | null; tone: string | null };

export type ReportPreview = {
  kind: ReportKindKey;
  title: string;
  date_from: string;
  date_to: string;
  fmt: 'csv' | 'xlsx';
  file_name: string;
  offices: number;
  employees: number;
  employee_name: string | null;
  days: number | null;
  rows_estimate: number;
  estimate_exact: boolean;
  sampled_days: number | null;
  columns: { key: string; title: string; type: string }[];
  rows: ReportCell[][];
  sheets: string[];
  timezones: string[];
  warnings: string[];
};

export const reportCatalog = (signal?: AbortSignal) =>
  request<ReportCatalog>('/reports/catalog', signal ? { signal } : {});

export const reportPreview = (
  body: ReportSpec & { fmt: 'csv' | 'xlsx'; limit: number },
  signal?: AbortSignal,
) =>
  request<ReportPreview>('/reports/preview', {
    method: 'POST', body, ...(signal ? { signal } : {}),
  });

export type ReportTemplate = {
  id: string;
  name: string;
  kind: ReportKindKey;
  fmt: 'csv' | 'xlsx';
  filters: ExportFilters;
  created_at: string;
  updated_at: string;
};

export const reportTemplates = (signal?: AbortSignal) =>
  request<{ items: ReportTemplate[] }>('/report-templates/', signal ? { signal } : {});

export const saveReportTemplate = (
  body: ReportSpec & { fmt: 'csv' | 'xlsx'; template_name: string },
) => request<ReportTemplate>('/report-templates/', { method: 'POST', body });

export const deleteReportTemplate = (id: string) =>
  request<null>(`/report-templates/${id}/`, { method: 'DELETE' });

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

// --- лента событий кадровика ------------------------------------------------

/**
 * Лента — НЕ очередь отправки выше.
 *
 * Очередь отвечает на вопрос «ушло ли сообщение сотруднику»; лента — на
 * вопрос «что случилось в кадровом контуре и ждёт человека». Разные
 * данные, разные адреса, и складывать их в один список нельзя.
 */
export type FeedType =
  | 'absence_request'
  | 'sick_leave'
  | 'absence_cancel'
  | 'absence_document'
  | 'attendance_correction'
  | 'question';

export type FeedGroup =
  | 'requests' | 'documents' | 'questions';

export type FeedEvent = {
  /** Составной ключ «вид:запись»: событий своей таблицы у ленты нет. */
  id: string;
  type: FeedType;
  group: FeedGroup;
  title: string;
  short_text: string;
  employee_id: string | null;
  employee_name: string;
  office_id: string | null;
  office_name: string | null;
  status: string;
  status_label: string;
  priority: 'NORMAL' | 'HIGH' | 'CRITICAL';
  requires_action: boolean;
  created_at: string;
  /** Прочтение ЭТОГО пользователя. У коллеги оно своё. */
  read_at: string | null;
  related_entity_type: string;
  related_entity_id: string;
  action_url: string;
  action_title: string;
};

export type FeedCounts = {
  all: number;
  unread: number;
  action: number;
  requests: number;
  documents: number;
  questions: number;
};

export type FeedPage = {
  items: FeedEvent[];
  counts: FeedCounts;
  next_cursor: string | null;
  has_more: boolean;
  window_days: number;
};

/** Безопасные сведения о приложенном файле. Самого файла здесь нет. */
export type FeedDocument = {
  id: string;
  document_type: string;
  verification_status: string;
  verification_label: string;
  verified_at: string | null;
  file_name: string;
  size_bytes: number;
  uploaded_at: string;
  scan_status: string;
};

export type FeedPerson = { id: string; name: string };

export type FeedDetail = FeedEvent & {
  employee: {
    id: string;
    full_name: string;
    office_id?: string | null;
    office_name?: string | null;
    department_name?: string | null;
    position_name?: string | null;
  } | null;
  author: FeedPerson | null;
  comment: string | null;
  occurred_at: string;
  absence?: {
    type_name: string;
    type_code: string;
    request_kind: string;
    is_extension: boolean;
    first_day: string | null;
    last_day: string | null;
    requires_document: boolean;
    document: FeedDocument | null;
    review_comment: string | null;
    calendar_days: number | null;
    working_days: number | null;
    balance_before_days: number | null;
    balance_after_days: number | null;
    overlaps: boolean;
  };
  correction?: {
    day: string | null;
    current_entry_at: string | null;
    current_exit_at: string | null;
    requested_entry_at: string | null;
    requested_exit_at: string | null;
    event_kind: string;
    review_comment: string | null;
    has_document: boolean;
  };
  session?: {
    started_at: string;
    open_minutes: number;
    office_name: string | null;
    schedule_name: string | null;
    qr_point_name: string | null;
    last_event_at: string | null;
    last_event_type: string | null;
  };
  question?: {
    topic: string | null;
    channel: string;
    category: string;
    priority: string;
    last_message_at: string;
    assigned_to: FeedPerson | null;
  };
  delivery?: {
    channel: string;
    notification_type: string;
    attempts: number;
    last_attempt_at: string;
    next_attempt_at: string | null;
    will_retry: boolean;
  };
  report?: {
    kind: string;
    fmt: string;
    file_name: string | null;
    size_bytes: number | null;
    expires_at: string | null;
    rows: number | null;
  };
  new_employee?: {
    employee_number: string;
    hire_date: string;
    employment_status: string;
    telegram_connected: boolean;
  };
};

export type FeedQuery = { scope?: string; limit?: string; cursor?: string };

export const feed = (params: FeedQuery = {}, signal?: AbortSignal) =>
  request<FeedPage>(`/notification-feed${query(params)}`, signal ? { signal } : {});

export const feedCounts = (signal?: AbortSignal) =>
  request<FeedCounts>('/notification-feed/counts', signal ? { signal } : {});

export const feedEvent = (id: string, signal?: AbortSignal) =>
  request<FeedDetail>(
    `/notification-feed/${encodeURIComponent(id)}`,
    signal ? { signal } : {},
  );

export const readFeedEvent = (id: string) =>
  request<FeedCounts>(`/notification-feed/${encodeURIComponent(id)}`, {
    method: 'POST',
    body: {},
  });

export const readAllFeed = () =>
  request<FeedCounts & { marked: number }>('/notification-feed/read-all', {
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

/**
 * `email` — это логин. Адресом почты он быть не обязан: `malika.hr`
 * ничем не хуже. Без пароля запись создаётся отключённой.
 */
export const createCrmUser = (body: {
  email: string;
  full_name?: string;
  password?: string;
  employee_id?: string;
}) =>
  request<CrmUser>('/users/', { method: 'POST', body });

export const updateCrmUser = (
  id: string,
  body: {
    /** Логин. Адресом почты быть не обязан. */
    email?: string;
    full_name?: string;
    employee_id?: string;
    unlink_employee?: boolean;
  },
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
  /** Предлагается ли роль в форме выдачи доступа. Каталог шире набора. */
  offered?: boolean;
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

/**
 * Отозвать роль.
 *
 * Сервер не стирает строку, а закрывает её срок: «кто и когда дал
 * человеку этот доступ» — вопрос, ответ на который должен пережить
 * отзыв. Отказ, если это последний суперадминистратор.
 */
export const revokeRole = (grant_id: string) =>
  request<Grant>(`/grants/${grant_id}`, { method: 'DELETE' });

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
  /** Перерывы дня. Приходят только в карточке графика, не в списке. */
  breaks?: Array<{
    id: string;
    name: string;
    start_time: string;
    end_time: string;
    is_paid: boolean;
  }>;
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

// --- приём сотрудника ------------------------------------------------------

export type Position = { id: string; name: string; code?: string; status?: string };

/**
 * Должности для выбора при приёме — только действующие.
 *
 * Архивную должность потому и архивировали, что на неё больше не
 * принимают: показать её в списке значит предложить завести человека
 * на упразднённую роль. У тех, кто уже на ней работает, она остаётся —
 * это история, и её карточка берёт название отдельно.
 */
export const positions = (signal?: AbortSignal) =>
  request<Cursored<Position>>(
    '/positions/?status=ACTIVE&limit=200',
    signal ? { signal } : {},
  );

export type ScheduleRow = {
  id: string;
  name: string;
  timezone: string;
  status: string;
  weekly_minutes?: number;
};

export const workSchedules = (signal?: AbortSignal) =>
  request<Cursored<ScheduleRow>>(
    '/work-schedules/?status=ACTIVE&limit=200',
    signal ? { signal } : {},
  );

/**
 * Отделы одного офиса: состав отделов у офисов разный.
 *
 * Только действующие — по той же причине, что и должности: в
 * расформированный отдел человека не принимают.
 */
export const officeDepartments = (office_id: string, signal?: AbortSignal) =>
  request<Cursored<Department>>(
    `/departments/${query({ office_id, status: 'ACTIVE', limit: '200' })}`,
    signal ? { signal } : {},
  );

/** Файл, принятый сервером, но ещё ни к кому не привязанный. */
export type AttachedFile = {
  id: string;
  name: string;
  mime_type: string;
  size_bytes: number;
};

/** Вид бумаги в чек-листе сотрудника. */
export type EmployeeDocumentKind = 'IDENTITY' | 'CONTRACT' | 'HIRE_ORDER' | 'OTHER';

/** Приложенная бумага в форме приёма: вид плюс уже загруженный файл. */
export type EmployeeDocumentInput = {
  kind: EmployeeDocumentKind;
  file_id: string;
  title?: string | null;
};

/**
 * Отдать файл серверу до создания сотрудника.
 *
 * Размер и тип проверяет сервер: браузеру верить в этом нельзя, а
 * второй набор правил в двух местах однажды разойдётся.
 */
export const uploadEmployeeFile = (
  file: File,
  purpose: 'photo' | 'document',
  signal?: AbortSignal,
) => {
  const form = new FormData();
  form.append('file', file);
  form.append('purpose', purpose);
  return upload<AttachedFile>('/employees/attachments/', form, signal);
};

/** Адрес фотографии. Открывается с cookie сессии, как и всё остальное. */
export const employeePhotoUrl = (employeeId: string) =>
  apiUrl(`/employees/${employeeId}/photo/`);

/** Адрес приложенной бумаги. */
export const employeeDocumentUrl = (employeeId: string, documentId: string) =>
  apiUrl(`/employees/${employeeId}/documents/${documentId}/download/`);

export type OnboardBody = {
  idempotency_key: string;
  last_name: string;
  first_name: string;
  middle_name?: string | null;
  birth_date?: string | null;
  pinfl: string;
  phone: string;
  corporate_email?: string | null;
  telegram_username?: string | null;
  hire_date: string;
  region_id?: string | null;
  office_id: string;
  department_id: string;
  position_id: string;
  manager_employee_id?: string | null;
  employment_type: string;
  employment_status?: string;
  schedule_id: string;
  gender?: string | null;
  marital_status?: string | null;
  photo_file_id?: string | null;
  documents?: EmployeeDocumentInput[];
};

export type Onboarded = {
  employee: Record<string, unknown> & {
    id: string;
    employee_number: string;
    full_name: string;
  };
  created: boolean;
  schedule_assigned: boolean;
  telegram: { state: string; link: string | null; message: string };
  documents: { id: string; kind: string; title: string; status: string }[];
};

/**
 * Приём сотрудника. Один адрес на всю операцию: карточка, назначение,
 * график, документы и заготовка доступа к боту создаются вместе или не
 * создаются вовсе.
 */
export const onboardEmployee = (body: OnboardBody) =>
  request<Onboarded>('/employees/onboard/', { method: 'POST', body });

// --- справочники раздела «Администрирование» --------------------------------
/*
 * Структура компании: отделы, должности, графики и причины отсутствия.
 * Всё это настраивается в одном месте и нигде больше — кроме офисов:
 * офис создаётся и настраивается только на странице «Офисы и регионы»,
 * отсюда его можно лишь выбрать.
 *
 * Удаления ни у одного из справочников нет. На отдел, должность, график
 * и причину ссылаются закрытые назначения и заявки прошлых лет: стереть
 * их значило бы потерять ответ на вопрос, кем человек работал и почему
 * его не было в марте. Есть только перевод в неактивные.
 */

export type DepartmentRow = {
  id: string;
  /** null — отдел общий для компании, а не подразделение одного офиса. */
  office_id: string | null;
  office_name: string | null;
  parent_department_id: string | null;
  name: string;
  description: string | null;
  head_employee_id: string | null;
  head_employee_name: string | null;
  /** Сколько человек числится в отделе сегодня. */
  staff?: number;
  status: string;
};

export const departmentsPage = (
  params: { search?: string; status?: string; office_id?: string; limit?: string },
  signal?: AbortSignal,
) =>
  request<Cursored<DepartmentRow>>(
    `/departments/${query({ limit: '200', ...params })}`,
    signal ? { signal } : {},
  );

/**
 * Офис необязателен: обычный отдел общий для всей компании. Указывают
 * его только для подразделения, которое существует в одном месте.
 */
export const createDepartment = (body: {
  name: string;
  office_id?: string;
  description?: string;
  head_employee_id?: string;
}) => request<DepartmentRow>('/departments/', { method: 'POST', body });

export const updateDepartment = (
  id: string,
  changes: {
    name?: string;
    description?: string;
    head_employee_id?: string;
    clear_head?: boolean;
  },
) => request<DepartmentRow>(`/departments/${id}/`, { method: 'PATCH', body: changes });

/**
 * Убрать совсем. Сервер откажет, если на запись уже ссылались: тогда
 * остаётся архив. Кнопка «Удалить», которая на самом деле прячет,
 * обманывает — человек считает, что убрал опечатку, а она в отчётах.
 */
export const deleteDepartment = (id: string) =>
  request<void>(`/departments/${id}/`, { method: 'DELETE' });

export const deletePosition = (id: string) =>
  request<void>(`/positions/${id}/`, { method: 'DELETE' });

export const deleteAbsenceType = (id: string) =>
  request<void>(`/absence-types/${id}/`, { method: 'DELETE' });

export const deleteSchedule = (id: string) =>
  request<void>(`/work-schedules/${id}/`, { method: 'DELETE' });

export const setDepartmentActive = (id: string, active: boolean) =>
  request<DepartmentRow>(
    `/departments/${id}/${active ? 'reactivate' : 'deactivate'}/`,
    { method: 'POST' },
  );

export type PositionRow = {
  id: string;
  name: string;
  description: string | null;
  /** Сколько человек занимают должность сегодня. */
  staff?: number;
  status: string;
};

export const positionsPage = (
  params: { search?: string; status?: string; limit?: string },
  signal?: AbortSignal,
) =>
  request<Cursored<PositionRow>>(
    `/positions/${query({ limit: '200', ...params })}`,
    signal ? { signal } : {},
  );

export const createPosition = (body: { name: string; description?: string }) =>
  request<PositionRow>('/positions/', { method: 'POST', body });

export const updatePosition = (
  id: string,
  changes: { name?: string; description?: string },
) => request<PositionRow>(`/positions/${id}/`, { method: 'PATCH', body: changes });

export const setPositionActive = (id: string, active: boolean) =>
  request<PositionRow>(
    `/positions/${id}/${active ? 'reactivate' : 'deactivate'}/`,
    { method: 'POST' },
  );

export type AbsenceTypeRow = {
  id: string;
  name: string;
  is_paid: boolean;
  requires_approval: boolean;
  requires_document: boolean;
  document_required_after_days: number | null;
  deducts_leave_balance: boolean;
  is_active: boolean;
  /** Сколько раз причина уже встречается в заявках и периодах. */
  used?: number;
};

export const absenceTypes = (
  params: { search?: string; status?: string; limit?: string } = {},
  signal?: AbortSignal,
) =>
  request<Cursored<AbsenceTypeRow>>(
    `/absence-types/${query({ limit: '200', ...params })}`,
    signal ? { signal } : {},
  );

export type AbsenceTypeDraft = {
  name: string;
  is_paid?: boolean;
  requires_approval?: boolean;
  requires_document?: boolean;
  document_required_after_days?: number | null;
  deducts_leave_balance?: boolean;
};

export const createAbsenceType = (body: AbsenceTypeDraft) =>
  request<AbsenceTypeRow>('/absence-types/', { method: 'POST', body });

export const updateAbsenceType = (id: string, changes: Partial<AbsenceTypeDraft>) =>
  request<AbsenceTypeRow>(`/absence-types/${id}/`, {
    method: 'PATCH',
    body: changes,
  });

export const setAbsenceTypeActive = (id: string, active: boolean) =>
  request<AbsenceTypeRow>(
    `/absence-types/${id}/${active ? 'reactivate' : 'deactivate'}/`,
    { method: 'POST' },
  );

export const schedulesPage = (
  params: { search?: string; status?: string; limit?: string } = {},
  signal?: AbortSignal,
) =>
  request<Cursored<ScheduleRow>>(
    `/work-schedules/${query({ limit: '200', ...params })}`,
    signal ? { signal } : {},
  );

export type DayDraft = {
  weekday: number;
  is_working_day: boolean;
  start_time?: string | null;
  end_time?: string | null;
  crosses_midnight?: boolean;
  breaks?: Array<{
    name: string;
    start_time: string;
    end_time: string;
    is_paid?: boolean;
  }>;
};

export type ScheduleDraft = {
  name: string;
  timezone: string;
  weekly_minutes: number;
  late_grace_minutes?: number;
  early_leave_grace_minutes?: number;
  is_flexible?: boolean;
  days: DayDraft[];
};

export const createSchedule = (body: ScheduleDraft) =>
  request<WorkScheduleDetail>('/work-schedules/', { method: 'POST', body });

export const updateSchedule = (id: string, changes: Partial<ScheduleDraft>) =>
  request<WorkScheduleDetail>(`/work-schedules/${id}/`, {
    method: 'PATCH',
    body: changes,
  });

export const setScheduleActive = (id: string, active: boolean) =>
  request<ScheduleRow>(
    `/work-schedules/${id}/${active ? 'reactivate' : 'deactivate'}/`,
    { method: 'POST' },
  );

export const assignScheduleToEmployee = (
  id: string,
  body: { employee_id: string; valid_from: string },
) => request<ScheduleAssignment>(`/work-schedules/${id}/assign/`, {
  method: 'POST',
  body,
});

/**
 * Назначить график отделу — это снимок состава на дату, а не правило.
 * Пришедший в отдел завтра графика от этого назначения не получит:
 * иначе табель за прошлый месяц менялся бы от каждого перевода.
 */
export const assignScheduleToDepartment = (
  id: string,
  body: { department_id: string; valid_from: string },
) =>
  request<{ assigned: string[]; skipped: Array<{ employee_id: string; reason: string }> }>(
    `/work-schedules/${id}/assign-department/`,
    { method: 'POST', body },
  );

// --- опросы сотрудников ------------------------------------------------------
/*
 * Шаблон — набор вопросов, который переиспользуют; рассылка — одно
 * обращение к названному кругу людей. Разделены не для симметрии:
 * правка шаблона не должна менять то, что уже спросили.
 *
 * Опрос ИМЕННОЙ. Ответы приходят вместе с именем, офисом и отделом — HR
 * идёт по ним разговаривать с человеком, а не считает настроение в
 * среднем. Сводка рядом их не заменяет и анонимности не добавляет.
 */

export type SurveyQuestionKind = 'SINGLE' | 'MULTI' | 'SCALE' | 'TEXT';

export type SurveyQuestion = {
  id: string;
  position: number;
  text: string;
  kind: SurveyQuestionKind;
  is_required: boolean;
  options: string[] | null;
};

export type SurveyTemplate = {
  id: string;
  title: string;
  description: string | null;
  questions: SurveyQuestion[];
  created_at: string;
  updated_at: string;
};

export type SurveyQuestionDraft = {
  text: string;
  kind: SurveyQuestionKind;
  is_required?: boolean;
  options?: string[] | null;
};

export const surveyTemplates = (
  params: { search?: string; limit?: string } = {},
  signal?: AbortSignal,
) =>
  request<Cursored<SurveyTemplate>>(
    `/surveys/templates/${query({ limit: '100', ...params })}`,
    signal ? { signal } : {},
  );

export const surveyTemplate = (id: string, signal?: AbortSignal) =>
  request<SurveyTemplate>(`/surveys/templates/${id}/`, signal ? { signal } : {});

export const createSurveyTemplate = (body: {
  title: string;
  description?: string;
  questions: SurveyQuestionDraft[];
}) => request<SurveyTemplate>('/surveys/templates/', { method: 'POST', body });

export const updateSurveyTemplate = (
  id: string,
  body: { title?: string; description?: string; questions?: SurveyQuestionDraft[] },
) => request<SurveyTemplate>(`/surveys/templates/${id}/`, {
  method: 'PATCH',
  body,
});

/** Копия со всеми вопросами: основа для правки уже отвеченного шаблона. */
export const copySurveyTemplate = (id: string) =>
  request<SurveyTemplate>(`/surveys/templates/${id}/copy/`, { method: 'POST' });

export const archiveSurveyTemplate = (id: string) =>
  request<SurveyTemplate>(`/surveys/templates/${id}/archive/`, { method: 'POST' });

export type SurveyAudienceKind = 'EMPLOYEES' | 'DEPARTMENT' | 'OFFICE' | 'ALL';

export type SurveyCampaign = {
  id: string;
  template_id: string;
  template_title: string;
  title: string;
  status: 'DRAFT' | 'SCHEDULED' | 'ACTIVE' | 'FINISHED' | 'CANCELLED';
  audience_kind: SurveyAudienceKind;
  audience_ids: string[] | null;
  scheduled_at: string | null;
  repeat_months: number | null;
  next_send_at: string | null;
  sent_at: string | null;
  total?: number;
  done?: number;
  created_at: string;
};

export const surveyCampaigns = (
  params: { status?: string; limit?: string } = {},
  signal?: AbortSignal,
) =>
  request<Cursored<SurveyCampaign>>(
    `/surveys/campaigns/${query({ limit: '100', ...params })}`,
    signal ? { signal } : {},
  );

export const surveyCampaign = (id: string, signal?: AbortSignal) =>
  request<SurveyCampaign>(`/surveys/campaigns/${id}/`, signal ? { signal } : {});

export const createSurveyCampaign = (body: {
  template_id: string;
  title?: string;
  audience_kind: SurveyAudienceKind;
  audience_ids?: string[];
  scheduled_at?: string | null;
  repeat_months?: number | null;
  send_now?: boolean;
}) => request<SurveyCampaign>('/surveys/campaigns/', { method: 'POST', body });

export const sendSurveyCampaign = (id: string) =>
  request<SurveyCampaign>(`/surveys/campaigns/${id}/send/`, { method: 'POST' });

export const cancelSurveyCampaign = (id: string) =>
  request<SurveyCampaign>(`/surveys/campaigns/${id}/cancel/`, { method: 'POST' });

export type SurveyRecipient = {
  id: string;
  employee_id: string;
  full_name: string;
  status: 'PENDING' | 'SENT' | 'STARTED' | 'COMPLETED';
  sent_at: string | null;
  started_at: string | null;
  completed_at: string | null;
};

export const surveyRecipients = (
  id: string,
  params: { status?: string } = {},
  signal?: AbortSignal,
) =>
  request<Items<SurveyRecipient>>(
    `/surveys/campaigns/${id}/recipients/${query(params)}`,
    signal ? { signal } : {},
  );

export type SurveyFilledAnswer = {
  question_id: string;
  question_text: string;
  kind: SurveyQuestionKind;
  text: string | null;
  number: number | null;
  options: string[] | null;
};

/** Пройденный опрос конкретного человека. Имя — по замыслу, не по недосмотру. */
export type SurveyFilled = SurveyRecipient & {
  office_name: string | null;
  department_name: string | null;
  answers: SurveyFilledAnswer[];
};

export const surveyAnswers = (id: string, signal?: AbortSignal) =>
  request<Items<SurveyFilled>>(
    `/surveys/campaigns/${id}/answers/`,
    signal ? { signal } : {},
  );

export type SurveySummary = {
  progress: { total: number; sent: number; started: number; completed: number };
  questions: Array<{
    id: string;
    text: string;
    kind: SurveyQuestionKind;
    answered: number;
    average?: number | null;
    distribution?: Record<string, number>;
    texts?: string[];
  }>;
  offices: Array<{ name: string; total: number; completed: number }>;
  departments: Array<{ name: string; total: number; completed: number }>;
};

export const surveySummary = (id: string, signal?: AbortSignal) =>
  request<SurveySummary>(
    `/surveys/campaigns/${id}/summary/`,
    signal ? { signal } : {},
  );
