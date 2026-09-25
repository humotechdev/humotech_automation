/**
 * Общее у всех экранов модуля «Опросы»: вкладки, названия состояний и
 * несколько кусков разметки, которые иначе разошлись бы между страницами.
 *
 * Порядок вкладок задан здесь одним списком и берётся из него везде.
 * Порядок — это утверждение о том, с чего начинают: сначала готовят
 * вопросы, потом рассылают, потом поручают это правилу. Два разных
 * порядка на двух экранах означали бы, что модуль рассказывает о себе
 * две разные истории.
 */

import { Fragment, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

import * as api from '../../api/crm';
import { AppIcon, type AppIconName } from '../../components/AppIcon';
import { AppShell } from '../../components/AppShell';

export type SurveyTab = 'templates' | 'campaigns' | 'automations';

/**
 * Вкладки модуля в обязательном порядке: Шаблоны → Рассылки →
 * Автоматизации. Первая — то, что открывается при входе в раздел.
 */
export const TABS: Array<{ key: SurveyTab; title: string; to: string }> = [
  { key: 'templates', title: 'Шаблоны', to: '/surveys' },
  { key: 'campaigns', title: 'Рассылки', to: '/surveys/campaigns' },
  { key: 'automations', title: 'Автоматизации', to: '/surveys/automations' },
];

/** Вкладка по умолчанию. Тот же адрес, что и у раздела целиком. */
export const DEFAULT_TAB: SurveyTab = 'templates';

export const TEMPLATE_STATUS: Record<api.SurveyTemplateStatus, string> = {
  DRAFT: 'Черновик',
  PUBLISHED: 'Опубликован',
  ARCHIVED: 'В архиве',
};

export const CAMPAIGN_STATUS: Record<string, string> = {
  DRAFT: 'Черновик',
  SCHEDULED: 'Запланирована',
  ACTIVE: 'Идёт',
  FINISHED: 'Завершена',
  CANCELLED: 'Отменена',
};

export const RECIPIENT_STATUS: Record<api.SurveyRecipientStatus, string> = {
  PENDING: 'Не отправлено',
  SENT: 'Ожидает',
  STARTED: 'Начал',
  COMPLETED: 'Завершил',
  SKIPPED: 'Пропущен',
  EXPIRED: 'Не успел',
};

export const AUDIENCE: Record<api.SurveyAudienceKind, string> = {
  EMPLOYEES: 'Выбранные сотрудники',
  DEPARTMENT: 'Отдел',
  OFFICE: 'Офис',
  REGION: 'Регион',
  POSITION: 'Должность',
  ALL: 'Все действующие',
};

/**
 * События автоматизаций. Названия те же, что кадровик выбирает в форме:
 * два разных слова про одно событие означали бы, что настраивал он
 * одно, а сработало другое.
 */
export const TRIGGER: Record<api.SurveyTriggerKind, string> = {
  PROBATION_END: 'Окончание стажировки',
  FIRST_DAY: 'Первый рабочий день',
  DAYS_AFTER_HIRE: 'Через N дней после выхода',
  BIRTHDAY: 'День рождения',
  SCHEDULE: 'Регулярно по расписанию',
};

/** Что именно произойдёт — одной строкой, под выбором события. */
export const TRIGGER_ABOUT: Record<api.SurveyTriggerKind, string> = {
  PROBATION_END:
    'Сработает у тех, у кого в этот день заканчивается стажировка.',
  FIRST_DAY: 'Сработает в день выхода нового сотрудника.',
  DAYS_AFTER_HIRE:
    'Сработает через указанное число дней после выхода на работу.',
  BIRTHDAY: 'Сработает в день рождения сотрудника, каждый год.',
  SCHEDULE: 'Сработает по расписанию, независимо от событий у людей.',
};

/** Цветная точка состояния: `ok`, `wait`, `bad`, `off`. */
export type Tone = 'ok' | 'wait' | 'bad' | 'off';

export function State({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`sv-state sv-state--${tone}`}>
      <i className="sv-state__dot" />
      {children}
    </span>
  );
}

export function campaignTone(status: string): Tone {
  if (status === 'FINISHED') return 'ok';
  if (status === 'ACTIVE') return 'wait';
  if (status === 'CANCELLED') return 'off';
  return 'off';
}

export function recipientTone(status: api.SurveyRecipientStatus): Tone {
  if (status === 'COMPLETED') return 'ok';
  if (status === 'STARTED' || status === 'SENT') return 'wait';
  if (status === 'EXPIRED') return 'bad';
  return 'off';
}

/** Значок в круге — слева от названия в списках. */
export function Mark({ icon }: { icon: AppIconName }) {
  return (
    <span className="sv-mark" aria-hidden="true">
      <AppIcon name={icon} size={18} />
    </span>
  );
}

/**
 * Полоса прохождения.
 *
 * У неотправленной рассылки полоса пустая, а вместо доли стоит прочерк:
 * «0%» здесь означало бы, что никто не ответил, — а её не отправляли.
 */
export function Progress({ done, total, sent }: {
  done: number;
  total: number;
  sent: boolean;
}) {
  const share = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="sv-progress">
      <span className="sv-progress__count">
        {sent && total > 0 ? `${done} из ${total}` : '—'}
      </span>
      <span className="sv-progress__track">
        <span className="sv-progress__fill"
              style={{ width: sent ? `${share}%` : '0%' }} />
      </span>
      <span className="sv-progress__share">
        {sent && total > 0 ? `${share}%` : '—'}
      </span>
    </div>
  );
}

/**
 * Предпросмотр в Telegram — карточка сообщения, а не рисованный телефон.
 *
 * Не украшение: кадровик пишет вопрос в широком поле на большом экране,
 * а читают его в узком пузыре с телефона. Половина неудачных
 * формулировок видна только здесь. Рамка телефона при этом ничего
 * не добавляет к проверке и занимает треть колонки.
 */
export function TelegramPreview({ title, children }: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="sv-tg">
      {/* Шапка чата — как её видит сотрудник: бот, от которого пришёл
          опрос. Экран телефона вокруг не рисуем: рамка с часами и
          батарейкой спорила бы с самим вопросом. */}
      <div className="sv-tg__from">
        <span className="sv-tg__avatar" aria-hidden="true">H</span>
        <span>
          <b>humotech</b>
          <i>бот</i>
        </span>
      </div>
      <div className="sv-tg__chat">
        <div className="sv-tg__bubble">
          <p className="sv-tg__title">{title}</p>
          {children}
        </div>
      </div>
    </div>
  );
}

/**
 * Рамка внутренней страницы модуля: белый лист, путь, заголовок,
 * действия справа.
 *
 * Одна на все страницы раздела — шаблон, рассылку, мастер, правило. Пять
 * своих шапок однажды разошлись бы на пиксель, и модуль читался бы как
 * собранный из разных наборов.
 */
export function SurveyFrame({
  crumbs, title, back, meta, actions, children, breadcrumb,
}: {
  /** Путь внутри листа: пары «подпись — адрес»; у последней адреса нет. */
  crumbs: Array<[string, string?]>;
  title: ReactNode;
  back?: [string, string];
  meta?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  /** Путь в верхней панели. */
  breadcrumb: string;
}) {
  return (
    <AppShell breadcrumb={breadcrumb} section="surveys">
      <div className="sv-page">
        <section className="sv-sheet">
          <p className="sv-crumbs">
            {crumbs.map(([label, to], at) => (
              <Fragment key={`${label}-${at}`}>
                {at > 0 && <i aria-hidden="true">/</i>}
                {to ? <Link to={to}>{label}</Link> : <b>{label}</b>}
              </Fragment>
            ))}
          </p>
          {back && (
            <Link className="sv-backlink" to={back[1]}>
              <AppIcon name="back" size={16} /> {back[0]}
            </Link>
          )}
          <header className="sv-top sv-top--inner">
            <div className="sv-top__main">
              <h1 className="sv-top__title">{title}</h1>
              {meta && <div className="sv-top__meta">{meta}</div>}
            </div>
            {actions && <div className="sv-top__tools">{actions}</div>}
          </header>
          {children}
        </section>
      </div>
    </AppShell>
  );
}

/** Черновик вопроса в редакторе шаблона. */
export type QuestionDraft = {
  text: string;
  kind: api.SurveyQuestionKind;
  is_required: boolean;
  options: string[];
};

/** Сколько вопросов допускается. Столько же проверяет сервер. */
export const MAX_QUESTIONS = 20;

export const QUESTION_KINDS: Array<[api.SurveyQuestionKind, string]> = [
  ['SINGLE', 'Один вариант'],
  ['MULTI', 'Несколько вариантов'],
  ['SCALE', 'Шкала оценки'],
  ['TEXT', 'Свободный ответ'],
];

export function blankQuestion(): QuestionDraft {
  return { text: '', kind: 'SINGLE', is_required: true, options: ['', ''] };
}

/**
 * Дата правки строкой: «22 сентября, 12:18».
 *
 * Месяц полным словом, а не «22 сент.»: в списке шаблонов
 * эта дата стоит одна на строке и читается спокойно, а сокращение
 * экономит три буквы там, где места хватает.
 */
/**
 * «22 сентября в 14:33» — дата и время одной фразой, как их говорят.
 *
 * Через запятую («22 сентября, 14:33») строка читается как два
 * отдельных значения, а у изменения шаблона оно одно — момент.
 */
export function atMoment(at: string, zone: string): string {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return '—';
  const where = zone ? { timeZone: zone } : {};
  const day = new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric', month: 'long', ...where,
  }).format(date);
  const time = new Intl.DateTimeFormat('ru-RU', {
    hour: '2-digit', minute: '2-digit', ...where,
  }).format(date);
  return `${day} в ${time}`;
}

export function longMoment(at: string, zone: string): string {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    hour: '2-digit',
    minute: '2-digit',
    ...(zone ? { timeZone: zone } : {}),
  }).format(date);
}

/**
 * Введённое время — время ОРГАНИЗАЦИИ, а не браузера.
 *
 * `datetime-local` отдаёт часы без пояса, а `new Date(...)` читает их в
 * поясе машины. Кадровик в командировке задал бы отправку на два
 * часа раньше, чем обещал экран, и узнал бы об этом по жалобе
 * сотрудников. «Отправим в 10:00» — это обещание, и держать его
 * надо в том поясе, который написан рядом.
 */
export function atZone(local: string, zone: string): Date {
  // `local` приходит как `YYYY-MM-DDTHH:mm`. Сначала читаем его как UTC,
  // потом смотрим, какие часы этот момент даёт в нужном поясе, и сдвигаем
  // на разницу. Смещение берётся на ЭТОТ день: у поясов с переводом
  // стрелок оно меняется среди года.
  const asUtc = Date.parse(local.length === 16 ? `${local}:00Z` : `${local}Z`);
  if (Number.isNaN(asUtc)) return new Date(local);
  if (!zone) return new Date(local);
  const shown = new Date(
    new Date(asUtc).toLocaleString('en-US', { timeZone: zone }),
  ).getTime();
  return new Date(asUtc - (shown - asUtc));
}

/** Название пояса и его смещение словами: «Ташкент, UTC+5». */
export function zoneLine(zone: string): string {
  if (!zone) return 'по часовому поясу организации';
  const city = zone.split('/').pop()?.replace(/_/g, ' ') ?? zone;
  const now = new Date();
  const utc = new Date(now.toLocaleString('en-US', { timeZone: 'UTC' })).getTime();
  const here = new Date(now.toLocaleString('en-US', { timeZone: zone })).getTime();
  const hours = Math.round((here - utc) / 3_600_000);
  return `${city}, UTC${hours >= 0 ? '+' : ''}${hours}`;
}

/** «1 вопрос», «2 вопроса», «5 вопросов». */
export function plural(value: number, forms: [string, string, string]): string {
  const tail = value % 100;
  if (tail >= 11 && tail <= 14) return forms[2];
  switch (value % 10) {
    case 1:
      return forms[0];
    case 2:
    case 3:
    case 4:
      return forms[1];
    default:
      return forms[2];
  }
}

export function questionsLine(count: number): string {
  return `${count} ${plural(count, ['вопрос', 'вопроса', 'вопросов'])}`;
}

export function peopleLine(count: number): string {
  return `${count} ${plural(count, ['сотрудник', 'сотрудника', 'сотрудников'])}`;
}

export function templatesLine(count: number): string {
  return `${count} ${plural(count, ['шаблон', 'шаблона', 'шаблонов'])}`;
}

/**
 * Когда правило отправит опрос — человеческими словами.
 *
 * Час показывается всегда: «на следующий день» без времени оставляет
 * кадровика гадать, придёт ли опрос в полночь.
 */
export function whenLine(rule: {
  trigger_kind: api.SurveyTriggerKind;
  offset_days: number;
  send_hour: number;
  send_minute: number;
  repeat_months: number | null;
}): string {
  const clock =
    `${String(rule.send_hour).padStart(2, '0')}:`
    + `${String(rule.send_minute).padStart(2, '0')}`;
  if (rule.trigger_kind === 'SCHEDULE') {
    const months = rule.repeat_months ?? 1;
    return `Раз в ${months} ${plural(months, ['месяц', 'месяца', 'месяцев'])}, ${clock}`;
  }
  if (rule.offset_days === 0) return `В день события, ${clock}`;
  if (rule.offset_days === 1) return `На следующий день, ${clock}`;
  const days = rule.offset_days;
  return `Через ${days} ${plural(days, ['день', 'дня', 'дней'])}, ${clock}`;
}
