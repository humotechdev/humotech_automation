/**
 * Карточка сотрудника: один белый лист, общая шапка и пять вкладок.
 *
 * Шапка и вкладки не перезагружаются при переключении — меняется только
 * то, что под ними. Открытая вкладка лежит в адресе, поэтому прямая
 * ссылка, обновление страницы и «Назад»/«Вперёд» работают, а возврат в
 * список сохраняет прежние поиск и фильтры.
 *
 * Ни одного расчёта учёта здесь нет. Время, опоздание, состояние дня,
 * счётчики заявок считает сервер: у ночной смены, открытой сессии и
 * допуска опоздания должен быть один ответ, а не второй, посчитанный в
 * браузере. Карточка различает «ноль», «нет данных», «загрузка», «нет
 * права» и «сбой» — ошибка запроса никогда не выглядит как «0» или «нет».
 *
 * Источники данных:
 *   шапка, основные сведения, документы — GET /employees/{id}/;
 *   «Сегодня», «Отработано» — GET /attendance/daily (пояс офиса);
 *   «Ждут решения» — GET /requests/counts (по всему набору, не по странице);
 *   уведомление о заявке, список заявок — GET /requests;
 *   Telegram — GET /employees/{id}/telegram, ознакомление — /onboarding;
 *   назначения и графики — /employees/{id}/assignments, /schedules, /work-schedules;
 *   история — GET /audit-logs?employee_id=.
 */

import { type ReactNode, useCallback, useMemo, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, messageFor, reasonFor } from '../api/errors';
import { AppShell } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { AppSelectField } from '../components/AppSelect';
import { Attendance } from '../features/employee/Attendance';
import { useSession } from '../features/auth/session';
import { useBlock, type Block } from '../features/dashboard/data';
import {
  TABS,
  dayState,
  duration,
  initials,
  isTab,
  type Tab,
} from '../features/employee/model';
import {
  auditField,
  auditTitle,
  auditValue,
  dayMonth,
  dayMonthYear,
  dayRange,
  monthIn,
  monthStartOf,
  plural,
  scheduleSummary,
  todayIn,
} from '../features/employee/card-model';
import { usePaged } from '../features/employee/paged';
import {
  certificateOf,
  certificateState,
  isOpen,
  kindOf,
  missingText,
  statusOf,
} from '../features/requests/model';
import { clock, clockOnDay } from '../features/time/zone';
import { internalPath } from '../utils/internal-path';
import '../styles/employee-card.css';

type Person = Record<string, unknown>;

const text = (person: Person, key: string): string | null => {
  const value = person[key];
  if (value === null || value === undefined) return null;
  const out = String(value).trim();
  return out || null;
};

/** Текущее назначение: где, кем и в каком отделе человек работает сейчас. */
const assignmentOf = (person: Person): Person | null => {
  const value = person['current_assignment'];
  return value && typeof value === 'object' ? (value as Person) : null;
};

/** Заглушка карточки по её форме: шапка с фото, вкладки, два блока. */
function CardSkeleton() {
  return (
    <div className="ec-sk" aria-hidden="true">
      <div className="ec-sk__head">
        <i className="ec-sk__block ec-sk__photo" />
        <span className="ec-sk__lines">
          <i className="ec-sk__block ec-sk__name" />
          <i className="ec-sk__block ec-sk__line" />
          <i className="ec-sk__block ec-sk__line ec-sk__short" />
        </span>
      </div>
      <i className="ec-sk__block ec-sk__tabs" />
      <div className="ec-sk__grid">
        <i className="ec-sk__block ec-sk__panel" />
        <i className="ec-sk__block ec-sk__panel" />
      </div>
    </div>
  );
}

/** Ответ «нет такого сотрудника или он вне вашей области». */
const MISSING = Symbol('missing');

type Rights = {
  read: boolean; attendance: boolean; absences: boolean; audit: boolean;
  manage: boolean; manual: boolean; correct: boolean; decide: boolean;
  export: boolean; telegramRead: boolean; telegramManage: boolean;
  onboardingRead: boolean; onboardingManage: boolean; schedules: boolean;
};

/**
 * Карточка пересоздаётся на каждого сотрудника.
 *
 * Маршрут `/employees/:id` при переходе к другому человеку страницу не
 * размонтирует. Без ключа под именем нового оставались данные прошлого:
 * блоки вкладок держали прежний ответ до прихода нового (а при сбое —
 * навсегда), одноразовая ссылка Telegram, раскрытые подробности и поздние
 * ответы прошлого запроса переезжали к другому человеку. С ключом всё
 * состояние и все запросы карточки относятся только к текущему id.
 */
export function EmployeePage() {
  const { id = '' } = useParams();
  return <EmployeeCard key={id} id={id} />;
}

function EmployeeCard({ id }: { id: string }) {
  const session = useSession();
  const mine = useMemo(
    () => new Set(session.status === 'authenticated' ? session.user.permissions : []),
    [session],
  );
  const rights: Rights = {
    read: mine.has('employees.read'),
    attendance: mine.has('attendance.read'),
    absences: mine.has('absences.read'),
    audit: mine.has('audit.read'),
    manage: mine.has('employees.manage'),
    manual: mine.has('attendance.manual'),
    correct: mine.has('attendance.correct'),
    decide: mine.has('absences.approve'),
    export: mine.has('reports.export'),
    telegramRead: mine.has('telegram.read'),
    telegramManage: mine.has('telegram.manage'),
    onboardingRead: mine.has('onboarding.read'),
    onboardingManage: mine.has('onboarding.manage'),
    schedules: mine.has('schedules.read'),
  };

  const [params, setParams] = useSearchParams();
  const raw = params.get('tab');
  const tab: Tab = isTab(raw) ? raw : 'overview';
  // Только путь внутри CRM: `?back=https://…` из присланной ссылки
  // превращал «Назад» в переход на чужой сайт.
  const back = internalPath(params.get('back'), '');

  // Счётчик изменений: решение по заявке, привязка, ознакомление —
  // и связанные сведения (счётчик ожиданий, уведомление, сегодняшний
  // день) перечитываются, а не остаются вчерашними.
  const [version, setVersion] = useState(0);
  const changed = useCallback(() => setVersion((n) => n + 1), []);

  const open = useCallback(
    (next: Tab, extra: Record<string, string> = {}) => setParams((was) => {
      const copy = new URLSearchParams();
      const keepBack = was.get('back');
      if (keepBack) copy.set('back', keepBack);
      copy.set('tab', next);
      for (const [key, value] of Object.entries(extra)) copy.set(key, value);
      return copy;
    }),
    [setParams],
  );

  const [card, reloadCard] = useBlock(
    (signal) => api.employee(id, signal).catch((error: unknown) => {
      // 404 — «нет такого или он вне области»: это ответ, а не сбой.
      if (error instanceof ApiFailure && error.status === 404) return MISSING;
      throw error;
    }),
    `employee|${id}|${version}`,
    Boolean(id),
  );

  const person: Person = card.state === 'ready' && card.data !== MISSING
    ? card.data as Person : {};
  const name = text(person, 'full_name') ?? '';
  const post = assignmentOf(person);
  // Пояс офиса — по нему «сегодня» и границы месяца. Без назначения —
  // пояс пользователя CRM: он же пояс организации.
  const zone = (post && text(post, 'office_timezone'))
    || (session.status === 'authenticated' ? session.user.timezone : '')
    || 'UTC';

  return (
    <AppShell
      breadcrumb={['Сотрудники', shortName(name) || 'Карточка сотрудника',
        TABS.find((item) => item.key === tab)?.title ?? ''].filter(Boolean).join(' / ')}
      section="employees"
    >
      <div className="ec">
        <Link className="ec-back" to={back || '/employees'}>
          <AppIcon name="back" size={16} />
          Все сотрудники
        </Link>

        {card.state === 'loading' && (
          <>
            <span className="visually-hidden" role="status">Открываем карточку…</span>
            <CardSkeleton />
          </>
        )}
        {card.state === 'denied' && (
          <p className="ec-empty ec-bad">Сессия закончилась или прав на карточки нет. Войдите заново.</p>
        )}
        {card.state === 'error' && (
          <p className="ec-empty ec-bad" role="alert">
            Не удалось открыть карточку.{' '}
            <button type="button" className="ec-link" onClick={reloadCard}>Повторить</button>
          </p>
        )}
        {card.state === 'ready' && card.data === MISSING && (
          <p className="ec-empty">
            Сотрудник не найден или вне вашей области доступа. Возможно, карточку удалили или
            перенесли.
          </p>
        )}

        {card.state === 'ready' && card.data !== MISSING && (
          <>
            <Header person={person} id={id} canEdit={rights.manage} />

            <div className="ec-tabs" role="tablist" aria-label="Разделы карточки">
              {TABS.map((item) => (
                <button key={item.key} type="button" role="tab"
                        aria-selected={tab === item.key}
                        className={tab === item.key ? 'ec-tab ec-tab--on' : 'ec-tab'}
                        onClick={() => open(item.key)}>
                  {item.title}
                </button>
              ))}
            </div>

            {/* Ключ — вкладка: при переключении содержимое проявляется,
                а обновление данных внутри вкладки его не пересоздаёт. */}
            <div className="ec-body" role="tabpanel" key={tab}>
              {tab === 'overview' && (
                <Overview id={id} person={person} zone={zone} rights={rights}
                          version={version} onGo={open} onChanged={changed} />
              )}
              {tab === 'attendance' && (
                <Attendance id={id} name={name} zone={zone}
                            rights={{ attendance: rights.attendance, export: rights.export,
                              correct: rights.correct }} />
              )}
              {tab === 'schedule' && <Schedule id={id} zone={zone} />}
              {tab === 'requests' && (
                <Requests id={id} person={person} rights={rights} zone={zone}
                          version={version} onChanged={changed} />
              )}
              {tab === 'history' && <History id={id} rights={rights} zone={zone} />}
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}

// --- общая шапка -------------------------------------------------------------

/** «Мирзаева Лола» из «Мирзаева Лола Азизовна». */
function shortName(full: string | null): string {
  return (full ?? '').split(' ').filter(Boolean).slice(0, 2).join(' ');
}

const STATUS_TONE: Record<string, string> = {
  ACTIVE: 'green', ONBOARDING: 'blue', PENDING: 'blue',
  ON_LEAVE: 'amber', SUSPENDED: 'amber', TERMINATED: 'grey', ARCHIVED: 'grey',
};

function Header({ person, id, canEdit }: { person: Person; id: string; canEdit: boolean }) {
  const status = text(person, 'employment_status') ?? '';
  const title = text(person, 'employment_status_title') ?? status;
  const hasPhoto = person['photo'] !== null && person['photo'] !== undefined;
  const post = assignmentOf(person);
  const first = [post && text(post, 'position_name'), post && text(post, 'department_name')]
    .filter(Boolean).join(' · ');
  const second = [post && text(post, 'office_name'), post && text(post, 'region_name')]
    .filter(Boolean).join(' · ');
  const hire = text(person, 'hire_date');
  const archived = text(person, 'archived_at');
  const zone = (post && text(post, 'office_timezone')) || 'UTC';
  const notYet = hire !== null && hire > todayIn(zone);

  return (
    <header className="ec-head">
      {hasPhoto ? (
        <img className="ec-face" src={api.employeePhotoUrl(id)} alt="" />
      ) : (
        <span className="ec-face" aria-hidden="true">
          {initials(text(person, 'first_name'), text(person, 'last_name'))}
        </span>
      )}
      <div className="ec-head__text">
        <div className="ec-head__line">
          <h1 className="ec-name" title={text(person, 'full_name') ?? undefined}>
            {shortName(text(person, 'full_name')) || 'Без имени'}
          </h1>
          {title && <span className={`ec-pill ec-pill--${STATUS_TONE[status] ?? 'grey'}`}>{title}</span>}
          {archived && <span className="ec-pill ec-pill--grey">В архиве</span>}
        </div>
        <p className="ec-sub">{first || 'Действующего назначения нет'}</p>
        {second && <p className="ec-sub ec-sub--muted">{second}</p>}
        {notYet && hire && (
          <p className="ec-sub ec-sub--muted">Выходит на работу {dayMonthYear(hire)}</p>
        )}
      </div>
      {canEdit && (
        <Link className="ec-btn ec-btn--primary ec-head__edit" to={`/employees/${id}/edit`}>
          Редактировать
        </Link>
      )}
    </header>
  );
}

// --- вкладка «Обзор» ---------------------------------------------------------

const GENDER: Record<string, string> = { MALE: 'Мужской', FEMALE: 'Женский' };

const MARITAL: Record<string, string> = {
  SINGLE: 'Не женат / не замужем',
  MARRIED: 'Женат / замужем',
  DIVORCED: 'Разведён(а)',
  WIDOWED: 'Вдовец / вдова',
};

function Overview({ id, person, zone, rights, version, onGo, onChanged }: {
  id: string; person: Person; zone: string; rights: Rights; version: number;
  onGo: (tab: Tab, extra?: Record<string, string>) => void; onChanged: () => void;
}) {
  const today = todayIn(zone);
  const first = monthStartOf(today);
  const post = assignmentOf(person);
  const [contacts, setContacts] = useState(false);

  const [journal, reloadJournal] = useBlock(
    (signal) => api.attendanceDaily({ employee_id: id, date_from: first, date_to: today }, signal),
    `overview-journal|${id}|${first}|${today}|${version}`,
    rights.attendance,
  );
  const [counts, reloadCounts] = useBlock(
    (signal) => api.queueCounts({ employee_id: id }, signal),
    `overview-counts|${id}|${version}`,
    rights.absences,
  );
  const [waiting] = useBlock(
    (signal) => api.queue({ employee_id: id, status: 'SUBMITTED,IN_REVIEW', limit: '1' }, signal),
    `overview-waiting|${id}|${version}`,
    rights.absences,
  );

  const report = journal.state === 'ready' ? journal.data : null;
  const todayRow = report?.days.find((row) => row.day === today) ?? null;
  const fieldSeconds = (report?.days ?? [])
    .filter((row) => row.state === 'FIELD_WORK')
    .reduce((sum, row) => sum + row.seconds, 0);
  const open = counts.state === 'ready' ? counts.data['open'] ?? null : null;
  const pending = waiting.state === 'ready' ? waiting.data.items[0] ?? null : null;
  const unplaced = Boolean(report?.note) && (report?.days.length ?? 0) === 0;

  return (
    <>
      <div className="ec-band" aria-label="Показатели сотрудника">
        <Cell title="Сегодня" note={todayNote(journal, todayRow, report?.note ?? null, rights.attendance)}
              value={!rights.attendance ? '—'
                : todayRow ? duration(todayRow.seconds) : journal.state === 'loading' ? '…' : '—'}
              onRetry={journal.state === 'error' ? reloadJournal : undefined} />
        <Cell title={`Отработано ${monthIn(today)}`}
              value={!rights.attendance ? '—'
                // Без назначения за период сервер отдаёт нули с пояснением:
                // это «не с чем считать», а не подтверждённые «0 ч».
                : report && unplaced ? '—'
                : report ? duration(report.totals.seconds) : journal.state === 'loading' ? '…' : '—'}
              note={!rights.attendance ? 'Нет права на посещаемость'
                : journal.state === 'error' ? 'Не удалось получить'
                : unplaced ? 'Нет назначения за этот период'
                : [dayRange(first, today),
                    fieldSeconds > 0 ? `в т. ч. выезд ${duration(fieldSeconds)}` : '']
                    .filter(Boolean).join(' · ')}
              onRetry={journal.state === 'error' ? reloadJournal : undefined} />
        <Cell title="Ждут решения"
              value={!rights.absences ? '—'
                : open !== null ? `${open} ${plural(open, 'заявка', 'заявки', 'заявок')}`
                : counts.state === 'loading' ? '…' : '—'}
              note={!rights.absences ? 'Нет права на заявки'
                : counts.state === 'error' ? 'Не удалось получить' : ''}
              link={rights.absences && open !== null
                ? { label: 'Открыть заявки', go: () => onGo('requests', open > 0 ? { status: 'open' } : {}) }
                : undefined}
              onRetry={counts.state === 'error' ? reloadCounts : undefined} />
      </div>

      <div className="ec-split">
        <section className="ec-col" aria-labelledby="ec-main-title">
          <h2 id="ec-main-title" className="ec-h2">Основные сведения</h2>
          <dl className="ec-facts">
            <Fact label="Офис" value={post
              ? [text(post, 'office_name'), text(post, 'region_name')].filter(Boolean).join(', ') || null
              : null} empty={post ? 'Не указан' : 'Нет назначения'} />
            <Fact label="Отдел" value={post ? text(post, 'department_name') : null}
                  empty={post ? 'Не указано' : 'Нет назначения'} />
            <Fact label="Должность" value={post ? text(post, 'position_name') : null}
                  empty={post ? 'Не указано' : 'Нет назначения'} />
            <Fact label="Дата приёма" value={text(person, 'hire_date')
              ? dayMonthYear(text(person, 'hire_date') as string) : null} />
            <Fact label="График"
                  value={scheduleSummary(person['current_schedule'] as Parameters<typeof scheduleSummary>[0])}
                  empty="Не назначен" />
          </dl>
          <div className="ec-reveal">
            <span>Контакты и личные данные</span>
            <button type="button" className="ec-link" aria-expanded={contacts}
                    onClick={() => setContacts((was) => !was)}>
              {contacts ? 'Скрыть' : 'Показать'}
            </button>
          </div>
          {contacts && (
            <dl className="ec-facts ec-facts--more">
              <Fact label="Полное имя" value={text(person, 'full_name')} />
              <Fact label="Табельный номер" value={text(person, 'employee_number')} />
              <Fact label="Телефон" value={text(person, 'phone')} />
              <Fact label="Рабочая почта" value={text(person, 'corporate_email')} />
              <Fact label="Личная почта" value={text(person, 'personal_email')} />
              <Fact label="Дата рождения" value={text(person, 'birth_date')
                ? dayMonthYear(text(person, 'birth_date') as string) : null} />
              <Fact label="Пол" value={GENDER[text(person, 'gender') ?? ''] ?? null} />
              <Fact label="Семейное положение" value={MARITAL[text(person, 'marital_status') ?? ''] ?? null} />
            </dl>
          )}
        </section>

        <section className="ec-col ec-col--side" aria-labelledby="ec-link-title">
          <h2 id="ec-link-title" className="ec-h2">Связь с сотрудником</h2>
          <TelegramBlock id={id} rights={rights} version={version} onChanged={onChanged} />
        </section>
      </div>

      {pending && (
        <div className="ec-notice">
          <span className="ec-notice__icon" aria-hidden="true"><AppIcon name="doc" size={20} /></span>
          <span className="ec-notice__text">
            <b>{kindOf(pending).title} ожидает решения</b>
            <span>{noticeLine(pending)}</span>
          </span>
          <button type="button" className="ec-btn ec-btn--primary"
                  onClick={() => onGo('requests', { status: 'open', request: pending.id })}>
            Рассмотреть
          </button>
        </div>
      )}
    </>
  );
}

function todayNote(
  journal: Block<api.DailyReport>, row: api.DailyRow | null, note: string | null, allowed: boolean,
): string {
  if (!allowed) return 'Нет права на посещаемость';
  if (journal.state === 'loading') return 'Загружаем…';
  if (journal.state === 'error') return 'Не удалось получить';
  if (journal.state === 'denied') return 'Закрыто правами';
  if (note) return note;
  if (!row) return 'Нет данных за сегодня';
  const zone = row.timezone;
  if (row.state === 'IN_OFFICE' && row.first_entry_at) {
    return `В офисе с ${clockOnDay(row.first_entry_at, zone, row.day)}`;
  }
  if (row.state === 'LEFT' && row.last_exit_at) {
    return `Ушёл в ${clockOnDay(row.last_exit_at, zone, row.day)}`;
  }
  return dayState(row);
}

function noticeLine(item: api.QueueItem): string {
  const absence = item.absence;
  const days = absence?.first_day
    ? dayRange(absence.first_day, absence.last_day ?? absence.first_day) : '';
  const paper = absence?.requires_document
    ? (certificateOf(item) ? 'справка приложена' : 'справки нет') : '';
  return [days, paper].filter(Boolean).join(' · ') || 'Подробности — на вкладке заявок';
}

function Cell({ title, value, note, link, onRetry }: {
  title: string; value: string; note: string;
  link?: { label: string; go: () => void } | undefined;
  onRetry?: (() => void) | undefined;
}) {
  return (
    <div className="ec-cell">
      <span className="ec-cell__title">{title}</span>
      <strong className="ec-cell__value">{value}</strong>
      {note && <span className="ec-cell__note">{note}</span>}
      {onRetry && <button type="button" className="ec-link" onClick={onRetry}>Повторить</button>}
      {link && !onRetry && (
        <button type="button" className="ec-link" onClick={link.go}>{link.label}</button>
      )}
    </div>
  );
}

function Fact({ label, value, empty = 'Не указано' }: {
  label: string; value: string | null; empty?: string;
}) {
  return (
    <div className="ec-fact">
      <dt>{label}</dt>
      <dd className={value ? undefined : 'ec-muted'}>{value ?? empty}</dd>
    </div>
  );
}

/** Состояние ознакомления словами. Совпадает с разделом «Ознакомление». */
const ONBOARDING_STATE: Record<string, string> = {
  NOT_STARTED: 'Не начал',
  IN_PROGRESS: 'Читает разделы',
  INFO_COMPLETED: 'Ждёт согласия',
  POLICIES_IN_PROGRESS: 'Подтверждает документы',
  COMPLETED: 'Завершил',
  UPDATE_REQUIRED: 'Требуется ознакомление',
  BLOCKED_BY_DECLINED_POLICY: 'Отказался',
};

const TELEGRAM_STATE: Record<string, { title: string; tone: string }> = {
  ACTIVE: { title: 'Привязан', tone: 'green' },
  PENDING: { title: 'Ждёт подтверждения HR', tone: 'amber' },
  INVITED: { title: 'Приглашение отправлено', tone: 'blue' },
  REVOKED: { title: 'Отключён', tone: 'grey' },
  NOT_LINKED: { title: 'Не привязан', tone: 'grey' },
};

/**
 * Telegram и ознакомление — одна дорога: привязка без ознакомления
 * открывает человеку бота, ознакомление без привязки некуда отправить.
 *
 * Ссылка показывается РОВНО ОДИН раз — в ответе на её выдачу: в базе
 * лежит хеш токена, и достать её оттуда нельзя. Потерянная отзывается
 * и выпускается заново.
 */
function TelegramBlock({ id, rights, version, onChanged }: {
  id: string; rights: Rights; version: number; onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [issued, setIssued] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const [block, reloadBlock] = useBlock(
    (signal) => api.employeeTelegram(id, signal),
    `card-telegram|${id}|${version}`,
    rights.telegramRead,
  );
  // 404 у ознакомления — обычный ответ «не назначено»; сбой — это сбой,
  // а не «не назначено».
  const [plan, reloadPlan] = useBlock(
    (signal) => api.employeeOnboarding(id, signal).catch((failure: unknown) => {
      if (failure instanceof ApiFailure && failure.status === 404) return null;
      throw failure;
    }),
    `card-onboarding|${id}|${version}`,
    rights.onboardingRead,
  );

  const link = block.state === 'ready' ? block.data : null;
  const state = link?.state ?? null;
  const card = plan.state === 'ready' ? plan.data : null;
  const linked = state === 'ACTIVE';
  const live = card?.invitation_status === 'ACTIVE';
  // Человек перешёл по ссылке и ждёт решения HR. Подтверждать надо, только
  // если это действительно он: ссылку могли переслать или перехватить.
  const awaiting = state === 'PENDING'
    && link?.invitation?.status === 'PENDING_CONFIRMATION' ? link.invitation : null;
  const claimant = link?.account?.telegram_username
    ? `@${link.account.telegram_username}` : 'Telegram без имени пользователя';
  const pill = state ? TELEGRAM_STATE[state] ?? { title: state, tone: 'grey' } : null;

  const run = async (action: () => Promise<unknown>, after?: (result: unknown) => void) => {
    // Замок синхронный: двойное нажатие быстрее перерисовки.
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const result = await action();
      after?.(result);
      reloadPlan();
      reloadBlock();
      onChanged();
    } catch (failure) {
      setError(reasonFor(failure));
      // Ответ не дошёл — сначала узнаём, что на сервере, а не жмём ещё раз.
      if (failure instanceof ApiFailure && failure.kind === 'offline') {
        reloadBlock();
        reloadPlan();
      }
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };

  return (
    <div className="ec-tg">
      <div className="ec-fact ec-fact--pill">
        <dt>Telegram</dt>
        <dd>
          {!rights.telegramRead ? <span className="ec-muted">Нет права на просмотр</span>
            : block.state === 'loading' ? <span className="ec-muted">Загружаем…</span>
            : block.state === 'error' ? (
              <span className="ec-bad">Не удалось получить{' '}
                <button type="button" className="ec-link" onClick={reloadBlock}>Повторить</button>
              </span>
            )
            : block.state === 'denied' ? <span className="ec-muted">Закрыто правами</span>
            : pill && <span className={`ec-pill ec-pill--${pill.tone}`}>{pill.title}</span>}
          {linked && link?.account?.telegram_username && (
            <span className="ec-muted"> @{link.account.telegram_username}</span>
          )}
        </dd>
      </div>

      {awaiting && (
        <div className="ec-confirm">
          <p>
            По ссылке перешёл <strong>{claimant}</strong>. Подтвердите, только если это
            действительно сотрудник: доступ к кабинету откроется сразу.
          </p>
          {rights.telegramManage && (
            <div className="ec-actions">
              <button type="button" className="ec-btn ec-btn--primary" disabled={busy}
                      onClick={() => run(() => api.confirmTelegramLink(awaiting.id),
                        () => setNote('Привязка подтверждена'))}>
                Подтвердить
              </button>
              <button type="button" className="ec-btn ec-btn--line" disabled={busy}
                      onClick={() => run(() => api.rejectTelegramLink(awaiting.id),
                        () => setNote('Привязка отклонена. Выдайте сотруднику новую ссылку.'))}>
                Это не он — отклонить
              </button>
            </div>
          )}
        </div>
      )}

      {rights.telegramManage && !card && !linked && !awaiting && !rights.onboardingManage && (
        <div className="ec-actions">
          <button type="button" className="ec-btn ec-btn--line" disabled={busy}
                  onClick={() => run(() => api.inviteToTelegram(id), (made) => {
                    const out = made as { link?: string; url?: string } | undefined;
                    setIssued(out?.link ?? out?.url ?? null);
                  })}>
            Пригласить в Telegram
          </button>
        </div>
      )}

      <h2 className="ec-h2 ec-h2--gap">Ознакомление</h2>
      {!rights.onboardingRead ? (
        <p className="ec-muted">Нет права на ознакомление.</p>
      ) : plan.state === 'loading' ? (
        <p className="ec-muted" role="status">Загружаем…</p>
      ) : plan.state === 'error' ? (
        <p className="ec-bad">
          Не удалось получить.{' '}
          <button type="button" className="ec-link" onClick={reloadPlan}>Повторить</button>
        </p>
      ) : plan.state === 'denied' ? (
        <p className="ec-muted">Закрыто правами.</p>
      ) : card ? (
        <dl className="ec-facts">
          <Fact label="Состояние" value={ONBOARDING_STATE[card.status] ?? card.status} />
          <Fact label="Разделы" value={`${card.sections_done} из ${card.sections_total}`} />
          <Fact label="Документы" value={`${card.policies_done} из ${card.policies_total}`} />
        </dl>
      ) : (
        <p className="ec-muted">Не назначено</p>
      )}

      {rights.onboardingManage && plan.state === 'ready' && (
        <div className="ec-actions">
          {!card && (
            <button type="button" className="ec-btn ec-btn--line" disabled={busy}
                    onClick={() => run(() => api.inviteToOnboarding(id), (made) => {
                      // Одним действием: человек включается в программу и
                      // получает ссылку — без состояния «позвали, но не позвали».
                      const out = made as api.OnboardingInvite;
                      setIssued(out.link);
                      if (out.linked) setNote('Ознакомление назначено. Ссылка не нужна — Telegram уже привязан.');
                    })}>
              Назначить ознакомление
            </button>
          )}
          {card && !linked && !live && !awaiting && (
            <button type="button" className="ec-btn ec-btn--line" disabled={busy}
                    onClick={() => run(() => api.inviteToOnboarding(id),
                      (made) => setIssued((made as api.OnboardingInvite).link))}>
              Создать приглашение
            </button>
          )}
          {card && live && (
            <>
              <button type="button" className="ec-btn ec-btn--line" disabled={busy}
                      onClick={() => run(() => api.inviteToOnboarding(id, true),
                        (made) => setIssued((made as api.OnboardingInvite).link))}>
                Новая ссылка
              </button>
              <button type="button" className="ec-btn ec-btn--line" disabled={busy}
                      onClick={() => run(() => api.revokeOnboardingInvite(id), () => setIssued(null))}>
                Отозвать
              </button>
            </>
          )}
          {card && linked && !card.completed && (
            <button type="button" className="ec-btn ec-btn--line" disabled={busy}
                    onClick={() => run(() => api.remindOnboarding(id), () => setNote('Напоминание отправлено'))}>
              Напомнить
            </button>
          )}
          {card && (
            <Link className="ec-link" to={`/onboarding?employee=${id}`}>Открыть ознакомление</Link>
          )}
        </div>
      )}

      {issued && (
        <div className="ec-issued">
          <p className="ec-muted">Ссылка персональная и одноразовая — показывается один раз.</p>
          <input readOnly value={issued} aria-label="Ссылка для сотрудника"
                 onFocus={(event) => event.currentTarget.select()} />
        </div>
      )}
      {note && <p className="ec-muted" role="status">{note}</p>}
      {error && <p className="ec-bad" role="alert">{error}</p>}
    </div>
  );
}

// --- вкладка «График и назначения» -------------------------------------------

const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

const EMPLOYMENT: Record<string, string> = {
  FULL_TIME: 'Полная занятость', PART_TIME: 'Частичная занятость',
  CONTRACT: 'Договор', INTERN: 'Стажёр (прежний тип)',
};
const WORK_MODE: Record<string, string> = {
  ONSITE: 'В офисе', REMOTE: 'Удалённо', HYBRID: 'Гибрид', FIELD: 'Выездной',
};

function Schedule({ id, zone }: { id: string; zone: string }) {
  const [history, setHistory] = useState(false);
  const [data, reload] = useBlock(
    (signal) => Promise.all([
      api.employeeAssignments(id, signal),
      api.employeeSchedules(id, signal),
    ]).then(([assignments, schedules]) => ({
      assignments: assignments.items,
      schedules: schedules.items as unknown as api.ScheduleAssignment[],
    })),
    `schedule|${id}`,
  );

  const today = todayIn(zone);
  const active = (row: { valid_from: string | null; valid_to: string | null }) =>
    (!row.valid_from || row.valid_from <= today) && (!row.valid_to || row.valid_to >= today);
  const current = data.state === 'ready' ? data.data.schedules.find(active) ?? null : null;

  // Карточка графика — вторым запросом и только когда есть что спрашивать.
  const [detail, reloadDetail] = useBlock(
    (signal) => api.workSchedule(current?.schedule_id ?? '', signal),
    `schedule-detail|${current?.schedule_id ?? 'none'}`,
    Boolean(current?.schedule_id),
  );

  if (data.state === 'loading') return <p className="ec-empty" role="status">Загружаем…</p>;
  if (data.state === 'denied') return <p className="ec-empty ec-bad">Назначения закрыты вашими правами.</p>;
  if (data.state === 'error') {
    return (
      <p className="ec-empty ec-bad" role="alert">
        Не удалось загрузить назначения.{' '}
        <button type="button" className="ec-link" onClick={reload}>Повторить</button>
      </p>
    );
  }

  const post = data.data.assignments.find((row) => row.is_primary && active(row))
    ?? data.data.assignments.find(active) ?? null;
  const plan = detail.state === 'ready' ? detail.data : null;

  return (
    <>
      <div className="ec-split">
        <section className="ec-col">
          <h2 className="ec-h2">Текущее назначение</h2>
          {post ? (
            <dl className="ec-facts ec-facts--right">
              <Fact label="Офис" value={[post.office_name, post.region_name].filter(Boolean).join(', ') || null} />
              <Fact label="Отдел" value={post.department_name} />
              <Fact label="Должность" value={post.position_name} />
              <Fact label="Занятость" value={post.employment_type
                ? EMPLOYMENT[post.employment_type] ?? post.employment_type : null} />
              <Fact label="Формат работы" value={post.work_mode
                ? WORK_MODE[post.work_mode] ?? post.work_mode : null} />
              <Fact label="Действует с" value={post.valid_from ? dayMonthYear(post.valid_from) : null} />
            </dl>
          ) : (
            <p className="ec-empty">Действующего назначения нет.</p>
          )}
        </section>

        <section className="ec-col ec-col--side">
          <h2 className="ec-h2">Рабочий график</h2>
          {!current ? (
            <p className="ec-empty">График не назначен.</p>
          ) : detail.state === 'loading' ? (
            <p className="ec-empty" role="status">Читаем график…</p>
          ) : detail.state === 'error' ? (
            <p className="ec-empty ec-bad">
              Карточку графика получить не удалось.{' '}
              <button type="button" className="ec-link" onClick={reloadDetail}>Повторить</button>
            </p>
          ) : detail.state === 'denied' ? (
            <p className="ec-empty">Карточка графика закрыта правами.</p>
          ) : plan && (
            <dl className="ec-facts ec-facts--right">
              <Fact label="Режим" value={plan.name} />
              <Fact label="Рабочее время" value={workHours(plan)} />
              <Fact label="Перерыв" value={breakText(plan)} empty="Не предусмотрен" />
              <Fact label="Допуск опоздания" value={plan.late_grace_minutes === null
                ? null : `${plan.late_grace_minutes} мин`} />
              <Fact label="Действует с" value={dayMonthYear(current.valid_from)} />
            </dl>
          )}
        </section>
      </div>

      {current && plan && (
        <section className="ec-week-wrap">
          <div className="ec-row-head">
            <h2 className="ec-h2">Обычная рабочая неделя</h2>
            <span className="ec-muted">
              {Math.round(plan.weekly_minutes / 60)} {plural(Math.round(plan.weekly_minutes / 60), 'час', 'часа', 'часов')}
              {' · '}время {zoneCity(plan.timezone)}
            </span>
          </div>
          <Week days={plan.days} />
          <p className="ec-hint">
            Это график, как он назначен. Праздники и переносы сервер применяет в день события —
            их видно в журнале посещаемости.
          </p>
        </section>
      )}

      <section className="ec-more">
        <button type="button" className="ec-more__head" aria-expanded={history}
                onClick={() => setHistory((was) => !was)}>
          <AppIcon name={history ? 'chevron' : 'next'} size={18} />
          История назначений и графиков
        </button>
        {history && (
          <div className="ec-more__body ec-split">
            <section className="ec-col">
              <h3 className="ec-h3">Назначения</h3>
              <Periods today={today} empty="Записей о назначениях нет."
                       rows={data.data.assignments.map((row) => ({
                         id: row.id, from: row.valid_from ?? '', to: row.valid_to,
                         what: [row.office_name, row.department_name, row.position_name]
                           .filter(Boolean).join(' · ') || 'Без подробностей',
                       }))} />
            </section>
            <section className="ec-col ec-col--side">
              <h3 className="ec-h3">Графики</h3>
              <Periods today={today} empty="Графиков не назначалось."
                       rows={data.data.schedules.map((row) => ({
                         id: row.id, from: row.valid_from, to: row.valid_to,
                         what: row.schedule_name ?? 'График',
                       }))} />
            </section>
          </div>
        )}
      </section>
    </>
  );
}

/** «время Ташкента». Незнакомый пояс — как есть, без выдуманного города. */
const ZONE_CITY: Record<string, string> = {
  'Asia/Tashkent': 'Ташкента',
  'Asia/Samarkand': 'Самарканда',
  'Asia/Dushanbe': 'Душанбе',
  'Asia/Almaty': 'Алматы',
  'Asia/Bishkek': 'Бишкека',
  'Europe/Moscow': 'Москвы',
};

const zoneCity = (zone: string): string => ZONE_CITY[zone] ?? zone;

/** «09:00–18:00», если у всех рабочих дней одни часы, иначе «разное по дням». */
function workHours(plan: api.WorkScheduleDetail): string | null {
  const days = plan.days.filter((day) => day.is_working_day && day.start_time && day.end_time);
  if (!days.length) return plan.is_flexible ? 'Свободный' : null;
  const spans = new Set(days.map((day) => `${day.start_time?.slice(0, 5)}–${day.end_time?.slice(0, 5)}`));
  return spans.size === 1 ? [...spans][0] ?? null : 'Разное по дням';
}

/** «1 ч, не оплачивается» — по перерывам первого рабочего дня. */
function breakText(plan: api.WorkScheduleDetail): string | null {
  const day = plan.days.find((one) => one.is_working_day && (one.breaks?.length ?? 0) > 0);
  if (!day?.breaks?.length) return null;
  return day.breaks.map((one) => {
    const [h1 = 0, m1 = 0] = one.start_time.split(':').map(Number);
    const [h2 = 0, m2 = 0] = one.end_time.split(':').map(Number);
    const minutes = (h2 * 60 + m2) - (h1 * 60 + m1);
    const length = minutes % 60 === 0 ? `${minutes / 60} ч` : `${minutes} мин`;
    return `${length}, ${one.is_paid ? 'оплачивается' : 'не оплачивается'}`;
  }).join('; ');
}

/** Неделя рабочих интервалов. Выходной — не «нет данных», и наоборот. */
function Week({ days }: { days: api.ScheduleDay[] }) {
  const byWeekday = new Map(days.map((day) => [day.weekday, day]));
  return (
    <ul className="ec-week" aria-label="Обычная рабочая неделя">
      {WEEKDAYS.map((title, index) => {
        const day = byWeekday.get(index + 1);
        const working = Boolean(day?.is_working_day);
        return (
          <li key={title} className={working ? 'ec-week__day ec-week__day--on' : 'ec-week__day'}>
            <b>{title}</b>
            <span>
              {!day ? 'нет данных'
                : working ? `${(day.start_time ?? '').slice(0, 5)}–${(day.end_time ?? '').slice(0, 5)}`
                : 'Выходной'}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** История периодов: действующие, будущие и завершённые — разными словами. */
function Periods({ rows, empty, today }: {
  rows: Array<{ id: string; from: string; to: string | null; what: string }>;
  empty: string;
  today: string;
}) {
  if (rows.length === 0) return <p className="ec-empty">{empty}</p>;
  return (
    <ul className="ec-periods">
      {rows.map((row) => (
        <li key={row.id}>
          <span className="ec-periods__what">{row.what}</span>
          <span className="ec-muted">
            {row.from ? dayMonthYear(row.from) : '—'} — {row.to ? dayMonthYear(row.to) : 'по настоящее время'}
            {' · '}
            {row.from > today ? 'начнётся позже'
              : row.to && row.to < today ? 'завершено' : 'действует'}
          </span>
        </li>
      ))}
    </ul>
  );
}

// --- вкладка «Заявки и документы» --------------------------------------------

const STATUS_FILTER: Record<string, string> = {
  open: 'SUBMITTED,IN_REVIEW',
  APPROVED: 'APPROVED',
  REJECTED: 'REJECTED',
  CANCELLED: 'CANCELLED',
};

const DOC_KIND: Record<string, string> = {
  IDENTITY: 'Паспорт или ID-карта',
  CONTRACT: 'Трудовой договор',
  HIRE_ORDER: 'Приказ о приёме',
  OTHER: 'Другой документ',
};

const DOC_STATE: Record<string, string> = {
  MISSING: 'Не добавлен',
  UPLOADED: 'Загружен',
  GENERATED_LATER: 'Будет сформирован',
  REVIEW: 'Требует проверки',
};

function Requests({ id, person, rights, zone, version, onChanged }: {
  id: string; person: Person; rights: Rights; zone: string; version: number;
  onChanged: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const status = params.get('status') ?? '';
  const kind = params.get('kind') ?? '';
  const picked = params.get('request');
  const [docs, setDocs] = useState(false);

  const patch = (changes: Record<string, string | null>) => setParams((was) => {
    const next = new URLSearchParams(was);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    return next;
  }, { replace: true });

  const [list, { reload, loadMore }] = usePaged(
    (cursor, signal) => api.queue({
      employee_id: id,
      ...(STATUS_FILTER[status] ? { status: STATUS_FILTER[status] } : {}),
      ...(kind ? { kind } : {}),
      limit: '20',
      ...(cursor ? { cursor } : {}),
    }, signal),
    `requests|${id}|${status}|${kind}|${version}`,
    rights.absences,
  );
  const [counts] = useBlock(
    (signal) => api.queueCounts({ employee_id: id }, signal),
    `requests-counts|${id}|${version}`,
    rights.absences,
  );

  const total = counts.state !== 'ready' || kind ? null
    : status === '' ? counts.data['all'] ?? null
    : status === 'open' ? counts.data['open'] ?? null
    : null;
  const shownText = list.state !== 'ready' ? ''
    : total !== null ? `${total} ${plural(total, 'заявка', 'заявки', 'заявок')}`
    : list.hasMore ? `Показано ${list.items.length}, есть ещё`
    : `${list.items.length} ${plural(list.items.length, 'заявка', 'заявки', 'заявок')}`;

  const documents = Array.isArray(person['documents'])
    ? (person['documents'] as Record<string, unknown>[]) : [];

  return (
    <>
      <h2 className="ec-h2">Заявки сотрудника</h2>
      {!rights.absences ? (
        <p className="ec-note ec-note--bad">
          Нет права на заявки. Это отдельное разрешение — absences.read.
        </p>
      ) : (
        <>
          <div className="ec-toolbar">
            <span className="ec-toolbar__label">Статус</span>
            <AppSelectField className="ec-select" label="Статус заявок" value={status}
                            onChange={(value) => patch({ status: value || null, request: null })}>
              <option value="">Все заявки</option>
              <option value="open">Ждут решения</option>
              <option value="APPROVED">Подтверждены</option>
              <option value="REJECTED">Отклонены</option>
              <option value="CANCELLED">Отменены</option>
            </AppSelectField>
            <AppSelectField className="ec-select" label="Вид заявок" value={kind}
                            onChange={(value) => patch({ kind: value || null, request: null })}>
              <option value="">Все виды</option>
              <option value="absence">Отсутствия</option>
              <option value="correction">Исправления отметок</option>
            </AppSelectField>
            <span className="ec-muted">{shownText}</span>
          </div>

          {list.state === 'loading' && <p className="ec-empty" role="status">Загружаем заявки…</p>}
          {list.state === 'denied' && <p className="ec-empty ec-bad">Заявки закрыты вашими правами.</p>}
          {list.state === 'missing' && <p className="ec-empty">Сотрудник вне вашей области.</p>}
          {list.state === 'error' && (
            <p className="ec-empty ec-bad" role="alert">
              Не удалось загрузить заявки.{' '}
              <button type="button" className="ec-link" onClick={reload}>Повторить</button>
            </p>
          )}
          {list.state === 'ready' && list.items.length === 0 && (
            <p className="ec-empty">
              {status || kind ? 'По этому фильтру заявок нет.' : 'Заявок у сотрудника пока нет.'}
            </p>
          )}
          {list.state === 'ready' && list.items.length > 0 && (
            <div className="ec-table-wrap">
              <table className="ec-table ec-table--requests">
                <caption className="ec-sr">Заявки сотрудника</caption>
                <thead>
                  <tr>
                    <th scope="col"><span className="ec-sr">Раскрыть</span></th>
                    <th scope="col">Тип</th>
                    <th scope="col">Даты</th>
                    <th scope="col">Статус</th>
                    <th scope="col"><span className="ec-sr">Подробности</span></th>
                  </tr>
                </thead>
                <tbody>
                  {list.items.map((item) => {
                    const open = picked === item.id;
                    const state = statusOf(item);
                    const toggle = () => patch({ request: open ? null : item.id });
                    return (
                      <RequestRows key={item.id} item={item} open={open} state={state}
                                   onToggle={toggle} rights={rights} zone={zone}
                                   onDone={() => { reload(); onChanged(); }} />
                    );
                  })}
                </tbody>
              </table>
              {list.hasMore && (
                <button type="button" className="ec-btn ec-btn--line ec-more-btn" disabled={list.more}
                        onClick={loadMore}>
                  {list.more ? 'Загружаем…' : 'Показать ещё'}
                </button>
              )}
              {list.moreFailed && (
                <p className="ec-bad" role="alert">Следующую страницу получить не удалось — нажмите ещё раз.</p>
              )}
            </div>
          )}
        </>
      )}

      <section className="ec-more">
        <button type="button" className="ec-more__head" aria-expanded={docs}
                onClick={() => setDocs((was) => !was)}>
          <AppIcon name="doc" size={18} />
          Документы сотрудника · {documents.length}
          <span className="ec-more__arrow"><AppIcon name={docs ? 'chevron' : 'next'} size={18} /></span>
        </button>
        {docs && (
          documents.length === 0 ? (
            <p className="ec-empty">Документов пока нет.</p>
          ) : (
            <ul className="ec-docs">
              {documents.map((row) => {
                const key = String(row['id'] ?? '');
                const kindCode = String(row['kind'] ?? '');
                const title = String(row['title'] ?? '') || DOC_KIND[kindCode] || 'Документ';
                const state = String(row['status'] ?? '');
                return (
                  <li key={key}>
                    <AppIcon name="doc" size={18} />
                    <span className="ec-docs__title" title={title}>{title}</span>
                    {row['file_id'] && rights.manage ? (
                      // Файл отдаёт сервер с проверкой права и области при
                      // каждом открытии — ссылку нельзя «передать» без входа.
                      <a className="ec-link" href={api.employeeDocumentUrl(id, key)}
                         target="_blank" rel="noreferrer">Открыть</a>
                    ) : row['file_id'] ? (
                      <span className="ec-muted">Файл доступен кадровой службе</span>
                    ) : (
                      <span className="ec-muted">{DOC_STATE[state] ?? state}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          )
        )}
      </section>
    </>
  );
}

function RequestRows({ item, open, state, onToggle, rights, zone, onDone }: {
  item: api.QueueItem; open: boolean; state: { title: string; tone: string };
  onToggle: () => void; rights: Rights; zone: string; onDone: () => void;
}) {
  const kind = kindOf(item);
  const absence = item.absence;
  const dates = absence?.first_day
    ? dayRange(absence.first_day, absence.last_day ?? absence.first_day)
    : item.correction?.requested_entry_at
      ? dayMonth((item.correction.requested_entry_at).slice(0, 10)) : '—';
  return (
    <>
      <tr className={open ? 'ec-row ec-row--open' : 'ec-row'}>
        <td className="ec-chev">
          <button type="button" className="ec-icon-btn" aria-expanded={open}
                  aria-label={open ? 'Скрыть подробности' : 'Открыть подробности'} onClick={onToggle}>
            <AppIcon name={open ? 'chevron' : 'next'} size={18} />
          </button>
        </td>
        <td><b>{kind.title}</b></td>
        <td className="ec-nowrap">{dates}</td>
        <td><span className={`ec-pill ec-pill--${toneOf(state.tone)}`}>{state.title}</span></td>
        <td className="ec-act">
          <button type="button" className="ec-link" onClick={onToggle}>{open ? 'Скрыть' : 'Открыть'}</button>
        </td>
      </tr>
      {open && (
        <tr className="ec-row-more">
          <td colSpan={5}>
            <RequestDetails item={item} title={`${kind.title} · ${dates}`} rights={rights}
                            zone={zone} onDone={onDone} />
          </td>
        </tr>
      )}
    </>
  );
}

const toneOf = (tone: string): string =>
  tone === 'orange' ? 'amber' : tone === 'red' ? 'red' : tone === 'green' ? 'green'
    : tone === 'blue' ? 'blue' : 'grey';

function RequestDetails({ item, title, rights, zone, onDone }: {
  item: api.QueueItem; title: string; rights: Rights; zone: string; onDone: () => void;
}) {
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const absence = item.absence;
  const correction = item.correction;
  const submitted = absence?.submitted_at ?? correction?.submitted_at ?? item.created_at;
  const missing = absence?.missing_for_approval ?? [];
  const canDecide = isOpen(item)
    && (item.kind === 'absence' ? rights.decide && absence?.kind !== 'CANCEL' : rights.correct);
  const paper = absence ? certificateOf(item) : null;

  const decide = async (decision: 'approve' | 'reject') => {
    // Замок синхронный: двойное нажатие не отправит второе решение.
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError(null);
    try {
      if (item.kind === 'absence') await api.decideAbsence(item.id, decision, comment);
      else await api.decideCorrection(item.id, decision, comment);
      setDone(decision === 'approve' ? 'Заявка подтверждена' : 'Заявка отклонена');
      onDone();
    } catch (failure) {
      // Связь прервалась — неизвестно, дошло ли решение. Сначала
      // спрашиваем сервер, а не отправляем второй раз вслепую.
      if (failure instanceof ApiFailure && failure.kind === 'offline' && item.kind === 'absence') {
        try {
          const now = await api.absenceRequest(item.id);
          const settled = now.status !== 'SUBMITTED' && now.status !== 'IN_REVIEW';
          setError(settled
            ? 'Связь прерывалась, но решение дошло — список обновлён.'
            : 'Связь прервалась, решение не дошло. Можно отправить ещё раз.');
          if (settled) onDone();
        } catch {
          setError('Связь прервалась. Состояние заявки неизвестно — обновите страницу, прежде чем решать снова.');
        }
      } else if (failure instanceof ApiFailure && failure.kind === 'offline') {
        setError('Связь прервалась. Список перечитан — проверьте состояние, прежде чем решать снова.');
        onDone();
      } else if (failure instanceof ApiFailure
        && (failure.kind === 'conflict' || failure.kind === 'session' || failure.status === 404)) {
        // Другой HR успел решить или запись исчезла — показываем свежее.
        setError(`${reasonFor(failure)} Список обновлён.`);
        onDone();
      } else {
        // Комментарий остаётся: набирать заново после сбоя человек не должен.
        setError(failure instanceof ApiFailure ? reasonFor(failure) : messageFor(failure));
      }
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };

  return (
    <div className="ec-detail">
      <div className="ec-detail__main">
        <b className="ec-detail__title">{title}</b>
        <span className="ec-muted">
          {[
            absence?.requires_document ? `Справка: ${certificateState(item).title.toLowerCase()}` : '',
            submitted ? `отправлено ${whenDay(submitted, zone)}` : '',
          ].filter(Boolean).join(' · ')}
        </span>
        {absence?.comment && <p className="ec-quote">«{absence.comment}»</p>}
        {correction?.reason && <p className="ec-quote">«{correction.reason}»</p>}
        {missing.length > 0 && isOpen(item) && (
          <p className="ec-muted">До подтверждения не хватает: {missingText(missing)}.</p>
        )}
        {absence?.review_comment && <p className="ec-muted">Комментарий к решению: {absence.review_comment}</p>}
        {paper && (
          <a className="ec-link" href={api.absenceDocumentUrl(item.id, paper.id)} target="_blank"
             rel="noreferrer" title={paper.file.name}>
            Открыть справку
          </a>
        )}
        {item.kind === 'absence' && (
          <Link className="ec-link" to={`/requests/${item.id}`}>Открыть заявку полностью</Link>
        )}
      </div>

      {canDecide && !done && (
        <div className="ec-detail__decide">
          <label className="ec-field">
            <span>Комментарий к решению</span>
            <textarea value={comment} rows={2} maxLength={2000}
                      onChange={(event) => setComment(event.target.value)} />
          </label>
          <div className="ec-actions">
            <button type="button" className="ec-btn ec-btn--primary"
                    disabled={busy || (item.kind === 'absence' && missing.length > 0)}
                    title={missing.length > 0 ? `Не хватает: ${missingText(missing)}` : undefined}
                    onClick={() => void decide('approve')}>
              {busy ? 'Отправляем…' : 'Одобрить'}
            </button>
            <button type="button" className="ec-btn ec-btn--line" disabled={busy}
                    onClick={() => void decide('reject')}>
              Отклонить
            </button>
          </div>
        </div>
      )}
      {!canDecide && isOpen(item) && (
        <p className="ec-muted ec-detail__aside">Решение принимает тот, у кого есть право рассмотрения.</p>
      )}
      {done && <p className="ec-ok ec-detail__aside" role="status">{done}</p>}
      {error && <p className="ec-bad ec-detail__aside" role="alert">{error}</p>}
    </div>
  );
}

// --- вкладка «История изменений» ---------------------------------------------

function History({ id, rights, zone }: { id: string; rights: Rights; zone: string }) {
  const [action, setAction] = useState('');
  const [opened, setOpened] = useState<string | null>(null);

  const [log, { reload, loadMore }] = usePaged(
    (cursor, signal) => api.auditLogs({
      employee_id: id, ...(action ? { action } : {}), limit: '25', ...(cursor ? { cursor } : {}),
    }, signal),
    `history|${id}|${action}`,
    rights.audit,
  );

  if (!rights.audit) {
    return (
      <p className="ec-note ec-note--bad">
        Нет права на журнал изменений. В нём видно, кто и что менял, поэтому это отдельное
        разрешение — audit.read.
      </p>
    );
  }

  const count = log.state === 'ready'
    ? (log.hasMore ? `Показано ${log.items.length}, есть ещё`
      : `${log.items.length} ${plural(log.items.length, 'событие', 'события', 'событий')}`)
    : '';

  return (
    <>
      <div className="ec-row-head">
        <div>
          <h2 className="ec-h2">История изменений</h2>
          <span className="ec-muted">{count}</span>
        </div>
        <AppSelectField className="ec-select" label="Раздел журнала" value={action}
                        onChange={(value) => { setAction(value); setOpened(null); }}>
          <option value="">Все события</option>
          <option value="employee.">Профиль и переводы</option>
          <option value="attendance.">Посещаемость</option>
          <option value="absence">Заявки на отсутствие</option>
          <option value="telegram.">Telegram</option>
          <option value="onboarding.">Ознакомление</option>
        </AppSelectField>
      </div>

      {log.state === 'loading' && <p className="ec-empty" role="status">Загружаем…</p>}
      {log.state === 'denied' && <p className="ec-empty ec-bad">Журнал закрыт вашими правами.</p>}
      {log.state === 'missing' && (
        <p className="ec-empty">История этого сотрудника вне области вашего права на журнал.</p>
      )}
      {log.state === 'error' && (
        <p className="ec-empty ec-bad" role="alert">
          Не удалось загрузить журнал.{' '}
          <button type="button" className="ec-link" onClick={reload}>Повторить</button>
        </p>
      )}
      {log.state === 'ready' && log.items.length === 0 && (
        <p className="ec-empty">{action ? 'По этому разделу событий нет.' : 'Записей об этом сотруднике пока нет.'}</p>
      )}
      {log.state === 'ready' && log.items.length > 0 && (
        <ul className="ec-log">
          {log.items.map((row) => {
            const open = opened === row.id;
            return (
              <li key={row.id} className={open ? 'ec-log__item ec-log__item--open' : 'ec-log__item'}>
                <div className="ec-log__row">
                  <span className="ec-log__when">
                    <b>{whenDay(row.occurred_at, zone)}</b>
                    <span className="ec-muted">{clock(row.occurred_at, zone)}</span>
                  </span>
                  <span className="ec-log__what">
                    <b>{auditTitle(row.action)}</b>
                    <span className="ec-muted">{authorOf(row)}</span>
                  </span>
                  <button type="button" className="ec-link" aria-expanded={open}
                          onClick={() => setOpened(open ? null : row.id)}>
                    {open ? 'Скрыть' : 'Подробнее'}
                  </button>
                </div>
                {open && <Diff before={row.old_values} after={row.new_values} action={row.action} />}
              </li>
            );
          })}
        </ul>
      )}
      {log.state === 'ready' && log.hasMore && (
        <button type="button" className="ec-btn ec-btn--line ec-more-btn" disabled={log.more} onClick={loadMore}>
          {log.more ? 'Загружаем…' : 'Показать ещё'}
        </button>
      )}
      {log.moreFailed && <p className="ec-bad" role="alert">Следующую страницу получить не удалось — нажмите ещё раз.</p>}
    </>
  );
}

function whenDay(at: string, zone: string): string {
  try {
    return new Intl.DateTimeFormat('ru-RU', { timeZone: zone, day: 'numeric', month: 'long' }).format(new Date(at));
  } catch {
    return at.slice(0, 10);
  }
}

type AuditRow = api.AuditEntry & { actor_name?: string | null; actor_employee_name?: string | null };

function authorOf(row: AuditRow): string {
  if (row.actor_name) return row.actor_name;
  if (row.actor_email) return row.actor_email;
  if (row.actor_employee_name) return `${row.actor_employee_name} · сотрудник, Telegram`;
  if (row.actor_employee_id) return 'Сотрудник, Telegram';
  return 'Система';
}

/** Было и стало. Пустая запись — словами, без пустой таблицы. */
function Diff({ before, after, action }: {
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  action: string;
}) {
  const keys = [...new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])]
    .filter((key) => JSON.stringify(before?.[key]) !== JSON.stringify(after?.[key]) || !before);
  if (keys.length === 0) {
    return <p className="ec-diff ec-muted">Подробности не записаны. Код события: {action}.</p>;
  }
  const column = (title: string, values: Record<string, unknown> | null): ReactNode => (
    <div className="ec-diff__col">
      <span className="ec-muted">{title}</span>
      {values === null ? <span>не было</span> : (
        <dl>
          {keys.map((key) => (
            <div key={key}>
              <dt>{auditField(key)}</dt>
              <dd>{auditValue(values[key], key)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
  return (
    <div className="ec-diff">
      {column('Было', before)}
      {column('Стало', after)}
    </div>
  );
}
