/**
 * Обращения к личному кабинету.
 *
 * Ни один запрос не несёт ни `employee_id`, ни `organization_id`,
 * ни офиса, ни направления отметки. Подделать можно только то, что
 * где-то принимается, — поэтому их здесь просто нет.
 *
 * Токен уходит заголовком, а не в адресе: адрес попадает в журналы
 * сервера, в историю и в заголовок Referer целиком.
 */

import { recallToken } from './auth';

const API_URL = import.meta.env.VITE_API_URL ?? '/api/v1';

/** Ответ либо разобранная ошибка. Исключений наружу не летит. */
export type Result<T> =
  | { ok: true; value: T }
  | { ok: false; kind: 'auth' | 'network' | 'server' | 'refused'; message: string;
      reason?: string };

const MESSAGES = {
  network: 'Не удалось связаться с сервером. Проверьте связь.',
  server: 'Сервер временно недоступен. Попробуйте через пару минут.',
  auth: 'Сессия истекла. Откройте приложение заново из чата с ботом.',
} as const;

export async function call<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    form?: FormData;
    query?: Record<string, string | number | undefined>;
    fetchImpl?: typeof fetch;
    token?: string | null;
  } = {},
): Promise<Result<T>> {
  const doFetch = options.fetchImpl ?? fetch;
  const token = options.token !== undefined ? options.token : recallToken();
  if (!token) {
    return { ok: false, kind: 'auth', message: MESSAGES.auth };
  }

  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  let body: BodyInit | undefined;
  if (options.form) {
    // Content-Type у multipart выставляет браузер: он добавляет boundary,
    // и заданный вручную заголовок сломал бы разбор на сервере.
    body = options.form;
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await doFetch(`${API_URL}${path}${query(options.query)}`, {
      method: options.method ?? 'GET',
      headers,
      body,
    });
  } catch {
    return { ok: false, kind: 'network', message: MESSAGES.network };
  }

  if (response.status === 401) {
    return { ok: false, kind: 'auth', message: MESSAGES.auth };
  }

  if (!response.ok) {
    const error = await readError(response);
    if (response.status >= 500) {
      return { ok: false, kind: 'server', message: MESSAGES.server };
    }
    return {
      ok: false,
      kind: 'refused',
      message: error.message,
      reason: error.reason,
    };
  }

  return { ok: true, value: (await response.json()) as T };
}

async function readError(
  response: Response,
): Promise<{ message: string; reason?: string }> {
  try {
    const body = await response.json();
    const error = body?.error ?? {};
    return {
      message: error.message || MESSAGES.server,
      reason: error.details?.reason,
    };
  } catch {
    // Тело может оказаться не JSON — например, от промежуточного прокси.
    return { message: MESSAGES.server };
  }
}

function query(params?: Record<string, string | number | undefined>): string {
  if (!params) return '';
  const pairs = Object.entries(params).filter(
    ([, value]) => value !== undefined && value !== '',
  );
  if (!pairs.length) return '';
  return `?${pairs.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')}`;
}

// --- типы ответов ----------------------------------------------------------

/**
 * Профиль.
 *
 * Поля описаны такими, какими их отдаёт `ProfileView`: `assignment`
 * и `telegram` в ответе есть давно, просто не были объявлены. Отсутствие
 * поля в типе не мешает серверу его прислать — мешает клиенту им
 * пользоваться, не обходя проверку типов приведением.
 */
export interface Profile {
  employee: {
    id: string;
    full_name: string;
    employee_number: string;
    employment_status?: string;
    preferred_language?: string | null;
  };
  office: { id: string; name: string; timezone: string };
  position: { name: string } | null;
  department: { name: string } | null;
  assignment?: {
    employment_type: string;
    work_mode: string;
    valid_from: string;
  };
  telegram?: { status: string; username: string | null };
}

export interface OpenSession {
  id: string;
  day: string;
  started_at: string;
  ended_at: string | null;
  seconds: number;
  is_open: boolean;
  is_preliminary: boolean;
  office_name: string | null;
  entry_point_name: string | null;
  exit_point_name: string | null;
}

export interface Status {
  state: string;
  day: string;
  timezone: string;
  seconds_today: number;
  open_session: OpenSession | null;
  last_entry_at: string | null;
  last_exit_at: string | null;
  scheduled_start: string | null;
  scheduled_end: string | null;
  absence_name: string | null;
}

export interface Summary {
  first: string;
  last: string;
  timezone: string;
  seconds: number;
  completed_sessions: number;
  open_sessions: number;
  /** null означает «график не назначен», а не ноль рабочих дней. */
  working_days: number | null;
  attended_days: number;
  missed_days: number | null;
  sick_leave_days: number;
  vacation_days: number;
  other_absence_days: number;
  has_schedule: boolean;
}

export interface Day {
  day: string;
  seconds: number;
  sessions_count: number;
  has_open_session: boolean;
  is_working_day: boolean | null;
  attended: boolean;
  missed: boolean;
  absence_code: string | null;
  absence_name: string | null;
  /**
   * Норма дня в секундах: 0 у выходного, null — графика на этот день нет.
   *
   * Разные вещи, и путать их нельзя: ноль означает «работать не нужно»,
   * null — «сколько нужно, неизвестно». Кольцо недели красится по этому
   * числу, а не по «обычным восьми часам».
   */
  norm_seconds: number | null;
  sessions?: OpenSession[];
}

/** Уведомление, которое уже дошло до сотрудника. */
export interface Note {
  id: string;
  notification_type: string;
  title: string | null;
  body: string;
  sent_at: string | null;
  read_at: string | null;
  is_read: boolean;
}

export interface ScanResponse {
  status: string;
  accepted: boolean;
  office_name: string | null;
  point_name: string | null;
  occurred_at: string | null;
  /** Сколько метров до офиса вышло по координатам. `null` — не проверялось. */
  distance_m: number | null;
  /** Радиус офиса, с которым сравнивали. */
  radius_m: number | null;
  session: {
    id: string;
    started_at: string;
    ended_at: string | null;
    duration_seconds: number | null;
    status: string;
  } | null;
}

export interface AbsenceRequest {
  id: string;
  kind: string;
  absence_type: {
    code: string;
    name: string;
    requires_document: boolean;
    deducts_leave_balance: boolean;
  };
  status: string;
  /**
   * Состояние словами человека, посчитанное сервером.
   *
   * Не то же, что `status`. `status` отвечает на вопрос «рассмотрена
   * ли», а человек спрашивает другое: чего ждут и от кого. Разница
   * видна на больничном: подтвердить его можно только после справки,
   * заявления и фактических дат, и до тех пор он «ожидаем документы»
   * или «на проверке HR», а не «на согласовании».
   */
  stage:
    | 'WAITING_DOCUMENTS'
    | 'HR_REVIEW'
    | 'NEEDS_FIX'
    | 'PENDING'
    | 'APPROVED'
    | 'REJECTED'
    | 'CANCELLED';
  /** Судьба справки. `null` — её ещё не приносили. */
  certificate_status: 'PENDING' | 'VERIFIED' | 'REJECTED' | null;
  /**
   * Что кадровик сказал о непринятой справке — дословно.
   *
   * Та же причина приходит сообщением в чат, но сообщение теряется в
   * переписке к следующему дню, а заявка лежит перед глазами.
   */
  certificate_comment: string | null;
  extension_pending: boolean;
  first_day: string | null;
  last_day: string | null;
  working_days: number;
  comment: string | null;
  review_comment: string | null;
  documents: number;
  can_cancel: boolean;
  submitted_at: string | null;
  reviewed_at: string | null;
  absence_status: string | null;
}

export interface AbsenceOptions {
  types: Array<{
    code: string;
    name: string;
    requires_document: boolean;
    deducts_leave_balance: boolean;
  }>;
  policy: {
    require_hr_approval: boolean;
    document_required: boolean;
    /** Можно ли донести справку после того, как заявку одобрили. */
    document_can_be_added_later: boolean;
    employee_may_cancel_pending: boolean;
    cancelling_approved_requires_hr: boolean;
    extensions_allowed: boolean;
    max_document_bytes: number;
    allowed_document_types: string[];
  };
}

// --- запросы ---------------------------------------------------------------

/**
 * Опрос сотрудника.
 *
 * Именной: ответ привязан к человеку, и HR видит, кто что ответил. Это
 * решение, а не недосмотр — сотрудник должен понимать, что подписывается
 * своим именем, и приложение говорит об этом прямо на первом экране.
 */
export interface SurveyQuestion {
  id: string;
  position: number;
  text: string;
  /** SINGLE — один вариант, MULTI — несколько, SCALE — 1..5, TEXT — свой ответ. */
  kind: 'SINGLE' | 'MULTI' | 'SCALE' | 'TEXT';
  is_required: boolean;
  options: string[] | null;
}

export interface SurveyAnswer {
  question_id: string;
  text?: string | null;
  number?: number | null;
  options?: string[] | null;
}

export interface Survey {
  id: string;
  title: string;
  description: string | null;
  status: string;
  questions: SurveyQuestion[];
  /** Уже сохранённое: начатый опрос не начинается заново. */
  answers: SurveyAnswer[];
}

export interface SurveyBrief {
  id: string;
  title: string;
  status: string;
  sent_at: string | null;
  questions_total: number;
}

/**
 * Координаты в том виде, в каком их принимает сервер.
 *
 * Округление здесь не косметика. Поле объявлено как `DecimalField` с
 * шестью знаками после запятой, и лишние знаки оно не отбрасывает, а
 * отвергает запрос целиком. Телефон же выдаёт широту с тринадцатью
 * знаками не в виде исключения, а всегда — то есть без округления
 * отметка ломалась бы ровно там, где мы её чиним, и выглядела бы так
 * же: «не получилось».
 *
 * Шесть знаков — это примерно десять сантиметров на местности. Для
 * проверки «человек в пределах ста метров от офиса» больше не нужно.
 */
export function placeOf(
  position?: { latitude: number; longitude: number; accuracy: number } | null,
): { latitude: number; longitude: number; accuracy_m: number } | Record<string, never> {
  if (!position) return {};
  const { latitude, longitude, accuracy } = position;
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return {};
  return {
    latitude: Number(latitude.toFixed(6)),
    longitude: Number(longitude.toFixed(6)),
    // Погрешность сервер сравнивает с радиусом офиса, поэтому её тоже
    // надо уложить в объявленную точность — двух знаков хватает с
    // запасом, метры здесь не делят на сотые всерьёз.
    accuracy_m: Number(
      (Number.isFinite(accuracy) && accuracy > 0 ? accuracy : 30).toFixed(2),
    ),
  };
}

/**
 * Файл с сервера — как файл, а не как JSON.
 *
 * Отдельно от `call`, потому что тут всё другое: ответ не разбирается,
 * ошибка приходит текстом, а результат живёт в памяти вкладки, пока
 * адрес не отозван.
 *
 * Токен уходит заголовком, а не в адресе: ссылку с токеном человек
 * перешлёт в чат, и она будет работать у всех, кто её открыл.
 */
export async function fetchFile(
  path: string,
  options: { fetchImpl?: typeof fetch; token?: string | null } = {},
): Promise<Result<Blob>> {
  const doFetch = options.fetchImpl ?? fetch;
  const token = options.token !== undefined ? options.token : recallToken();
  if (!token) return { ok: false, kind: 'auth', message: MESSAGES.auth };

  let response: Response;
  try {
    response = await doFetch(`${API_URL}${path}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    return { ok: false, kind: 'network', message: MESSAGES.network };
  }

  if (response.status === 401) {
    return { ok: false, kind: 'auth', message: MESSAGES.auth };
  }
  if (!response.ok) {
    return { ok: false, kind: 'server', message: MESSAGES.server };
  }
  return { ok: true, value: await response.blob() };
}

export const api = {
  profile: () => call<Profile>('/me/profile'),
  status: () => call<Status>('/me/status'),
  statistics: (params: { period?: string; date_from?: string; date_to?: string }) =>
    call<{ summary: Summary; days: Day[] }>('/me/statistics', { query: params }),
  history: (params: { date_from?: string; date_to?: string; limit?: number;
                      offset?: number; period?: string }) =>
    // `period` в ответе несёт пояс офиса: без него клиент подписал бы
    // отметки временем телефона, а не временем места, где они сделаны.
    call<{
      period: { first: string; last: string; timezone: string };
      days: Day[];
      total: number;
      offset: number;
      limit: number;
      has_more: boolean;
    }>('/me/history', { query: params }),
  /**
   * Отметка. Наружу уходит один код — ни офиса, ни направления,
   * ни времени: всё это решает сервер.
   */
  scan: (
    token: string,
    clientEventId: string,
    position?: { latitude: number; longitude: number; accuracy: number } | null,
  ) =>
    call<ScanResponse>('/me/attendance/scan', {
      method: 'POST',
      body: { token, client_event_id: clientEventId, ...placeOf(position) },
    }),
  /**
   * Печатное заявление по заявке.
   *
   * Собирается на сервере из самой заявки: бланк, сохранённый однажды,
   * молча разошёлся бы с продлением.
   */
  absenceApplication: (id: string) =>
    fetchFile(`/me/absences/${id}/application`),
  /**
   * Прислать бумагу по заявке в чат.
   *
   * Сохранить файл прямо здесь нельзя: вебвью Telegram не отдаёт blob,
   * и нажатие «скачать» заканчивается ничем. Поэтому бумагу присылает
   * бот сообщением — оттуда её и пересылают, и печатают, и она
   * остаётся в переписке.
   */
  sendAbsencePaper: (id: string, paper: 'application' | 'certificate') =>
    call<null>(`/me/absences/${id}/papers/${paper}`, { method: 'POST' }),
  absences: () =>
    call<{ requests: AbsenceRequest[]; total: number }>('/me/absences'),
  absenceOptions: () => call<AbsenceOptions>('/me/absences/options'),
  createAbsence: (form: FormData) =>
    call<AbsenceRequest>('/me/absences', { method: 'POST', form }),
  cancelAbsence: (id: string) =>
    call<AbsenceRequest>(`/me/absences/${id}`, { method: 'DELETE' }),
  /**
   * Приложить справку к уже созданной заявке.
   *
   * Тот же адрес, что и при создании: справку выдают в день выписки, а
   * заявку подают в первый день болезни, и между ними проходит неделя.
   */
  attachAbsenceDocument: (id: string, file: File) => {
    const form = new FormData();
    form.append('document', file);
    return call<AbsenceRequest>(`/me/absences/${id}/document`, {
      method: 'POST',
      form,
    });
  },
  /**
   * Лента уведомлений. Отдельный запрос, а не часть профиля: она
   * обновляется по своим поводам и не должна ронять весь экран, если
   * упадёт.
   */
  notifications: (limit = 20) =>
    call<{ unread: number; items: Note[] }>('/me/notifications', {
      query: { limit },
    }),
  readNotification: (id: string) =>
    call<Note>(`/me/notifications/${id}/read`, { method: 'POST' }),
  /** Опросы, которые ещё предстоит пройти. */
  surveys: () => call<{ items: SurveyBrief[] }>('/me/surveys'),
  /**
   * Открыть опрос. Не просто чтение: с этого начинается отсчёт «начал
   * проходить», и HR видит разницу между «не открывал» и «бросил».
   */
  survey: (id: string) => call<Survey>(`/me/surveys/${encodeURIComponent(id)}`),
  /** Ответы уходят целиком: опрос проходят за раз, а не по вопросу. */
  submitSurvey: (id: string, answers: SurveyAnswer[]) =>
    call<{ id: string; status: string; completed_at: string | null }>(
      `/me/surveys/${encodeURIComponent(id)}`,
      { method: 'POST', body: { answers } },
    ),
  leaveBalance: () =>
    call<{
      balances: Array<{
        absence_type: { code: string; name: string };
        year: number;
        allocated_days: number;
        used_days: number;
        reserved_days: number;
        available_days: number;
      }>;
    }>('/me/leave-balance'),
};
