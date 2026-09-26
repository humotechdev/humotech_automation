/**
 * Один разбор ошибки на всё приложение.
 *
 * Backend отвечает единым телом `{error: {code, message, details}}`, и
 * различать случаи мы будем по `code`, а не по тексту: текст переводится
 * и переписывается, код — нет.
 *
 * Человеку ничего из этого не показывается дословно. Django на некоторых
 * отказах отвечает HTML-страницей, а DRF — служебной фразой вроде
 * «CSRF Failed: CSRF token missing»; и то и другое в интерфейсе выглядит
 * как утечка внутренностей, а не как объяснение.
 */

/** Что именно пошло не так — в терминах, понятных интерфейсу. */
export type FailureKind =
  /** Неверная пара логин/пароль. Намеренно неразличимо: существует ли логин. */
  | 'credentials'
  /** Клиент прислал неполные или неверные поля. */
  | 'validation'
  /** Сессии нет или она истекла. */
  | 'session'
  /** Не прошла проверка CSRF. */
  | 'csrf'
  /** Сеть недоступна, сервер не ответил. */
  | 'offline'
  /**
   * Действие столкнулось с состоянием, в котором его нельзя выполнить:
   * черновик нельзя архивировать, действующую версию нельзя править
   * поверх. Это ответ сервера, а не сбой.
   */
  | 'conflict'
  /**
   * AI-ассистент выключен рубильником. Не поломка: материалы
   * редактируются как обычно, недоступны только индексация и включение
   * записей в автоматические ответы.
   */
  | 'ai_disabled'
  /** Рубильник включён, но провайдер не настроен до конца. */
  | 'ai_unconfigured'
  /**
   * Слишком много попыток (429). Сервер ждёт паузы, а не другого
   * пароля: срок — в `retryAfter`, из заголовка `Retry-After`.
   */
  | 'throttled'
  /** Сервер ответил ошибкой или чем-то неразобранным. */
  | 'server';

export class ApiFailure extends Error {
  readonly kind: FailureKind;
  readonly status: number;
  readonly fields: Record<string, string[]>;
  /**
   * Поле, на которое сервер указал в `details.field`.
   *
   * Нужно, чтобы отказ встал рядом с тем полем, из-за которого он
   * произошёл, а не общей строкой над формой: «Сотрудник с таким ПИНФЛ
   * уже есть» над всей страницей не говорит, какое из четырнадцати полей
   * править. Текст при этом остаётся наш — сюда попадает только имя поля.
   */
  readonly field: string | null;
  /**
   * Код сервера (`error.code`) как есть. Вид отказа отвечает «что делать
   * человеку», код — «что именно случилось»: 409 бывает и «уже закрыто»,
   * и «Telegram не подключён», и совет у них разный.
   */
  readonly code: string | null;
  /**
   * Текст отказа, как его написал сервер.
   *
   * Общая фраза по виду ошибки говорит, ЧТО случилось («конфликт»), но
   * не почему именно: «в отделе ещё числятся семеро» знает только
   * сервер. Без этого поля причина отказа до человека не доходила.
   *
   * `null` — сервер причины не назвал; тогда показывают общую фразу.
   */
  readonly detail: string | null;
  /**
   * `details.reason` — почему именно отказано, внутри одного кода.
   *
   * 409 у заявки бывает «период занят», «больничный уже оформлен» и
   * «в эти дни есть отметки», и делать из них одно и то же нельзя:
   * у последнего есть продолжение — осознанное решение кадровика,
   * — а у первых двух его нет. Различать по тексту нельзя: текст
   * переписывают.
   */
  readonly reason: string | null;
  /** `details` целиком: в нём приходят дни конфликта и ссылка на заявку. */
  readonly details: Record<string, unknown>;

  constructor(
    kind: FailureKind,
    status = 0,
    fields: Record<string, string[]> = {},
    field: string | null = null,
    code: string | null = null,
    detail: string | null = null,
    details: Record<string, unknown> = {},
  ) {
    super(kind);
    this.name = 'ApiFailure';
    this.kind = kind;
    this.status = status;
    this.fields = fields;
    this.field = field;
    this.code = code;
    this.detail = detail;
    this.details = details;
    this.reason = typeof details.reason === 'string' ? details.reason : null;
  }
}

/** Русские формулировки. Ни одного технического текста от сервера. */
const MESSAGES: Record<FailureKind, string> = {
  credentials: 'Неверный логин или пароль',
  validation: 'Заполните логин и пароль',
  session: 'Сессия истекла. Войдите заново',
  csrf: 'Сессия устарела. Обновите страницу и повторите',
  offline: 'Нет связи с сервером. Проверьте подключение',
  conflict: 'Действие сейчас недоступно: состояние материала изменилось',
  ai_disabled:
    'AI-ассистент выключен. Материалы редактируются, но в автоматические '
    + 'ответы сотрудникам они пока не попадают',
  ai_unconfigured:
    'AI-провайдер не настроен. Материалы редактируются, но индексация '
    + 'недоступна',
  throttled: 'Слишком много попыток. Попробуйте позже',
  server: 'Сервер временно недоступен. Попробуйте позже',
};

/** «через 1 минуту», «через 3 минуты», «через 15 минут». */
function inMinutes(seconds: number): string {
  const n = Math.max(1, Math.ceil(seconds / 60));
  const tail = n % 100 >= 11 && n % 100 <= 14 ? 'минут'
    : n % 10 === 1 ? 'минуту'
      : n % 10 >= 2 && n % 10 <= 4 ? 'минуты' : 'минут';
  return `через ${n} ${tail}`;
}

/** Секунды ожидания: заголовок `Retry-After`, иначе `details.retry_after`. */
export function retryAfterOf(error: ApiFailure): number | null {
  const raw = error.details['retry_after'];
  const value = typeof raw === 'number' ? raw : typeof raw === 'string' ? Number(raw) : NaN;
  return Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * Отказ по области видимости: у кадровика доступ к части офисов, а
 * рассылки и автоматизации опросов адресуются всей организации.
 */
export const SCOPE_LIMITED =
  'Рассылки и автоматизации опросов доступны только с доступом ко всей '
  + 'организации. У вас доступ к части офисов — обратитесь к администратору.';

export function messageFor(error: unknown): string {
  if (error instanceof ApiFailure && error.reason === 'scope_limited') return SCOPE_LIMITED;
  if (error instanceof ApiFailure && error.kind === 'throttled') {
    // Свой текст со сроком, а не фраза сервера: срок из заголовка точнее,
    // а текст сервера мы не проверяем на то, что он для человека.
    const wait = retryAfterOf(error);
    return wait === null
      ? MESSAGES.throttled
      : `Слишком много попыток. Попробуйте ${inMinutes(wait)}`;
  }
  return error instanceof ApiFailure ? MESSAGES[error.kind] : MESSAGES.server;
}

/**
 * Отказ словами сервера, если он их сказал.
 *
 * «Состояние материала изменилось» — честный ответ там, где причина
 * техническая: две вкладки, устаревшая страница. Но сервис отказывает и
 * по делу — «больничный закрывается по справке: дата окончания не может
 * быть в будущем», «период пересекается с подтверждённым отсутствием», —
 * и это объяснение написано для человека. Заменять его общей фразой
 * значит прятать от кадровика единственное, что ему нужно знать.
 */
export function reasonFor(error: unknown): string {
  if (error instanceof ApiFailure && error.reason === 'scope_limited') return SCOPE_LIMITED;
  if (error instanceof ApiFailure && error.detail) return error.detail;
  return messageFor(error);
}
