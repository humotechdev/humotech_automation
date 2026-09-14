/**
 * Полная карточка сотрудника: одна страница, пять вкладок.
 *
 * Шапка и вкладки живут выше содержимого и при переключении не
 * перезагружаются: меняется только то, что под ними. Открытая вкладка
 * лежит в адресе, поэтому прямая ссылка, обновление страницы и кнопки
 * «Назад»/«Вперёд» работают, а возврат в список сохраняет прежние поиск
 * и фильтры — они остаются в адресе списка.
 *
 * Ни одного бизнес-расчёта здесь нет. Время в офисе, опоздание,
 * состояние дня и границы суток считает сервер: у ночной смены,
 * открытой сессии и допуска опоздания должен быть один ответ, а не
 * второй, посчитанный в браузере.
 */

import { useCallback, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { AppShell } from '../components/AppShell';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { Attendance } from '../features/employee/Attendance';
import { useSession } from '../features/auth/session';
import { useBlock, type Block } from '../features/dashboard/data';
import { clockOnDay, moment, shortDate } from '../features/time/zone';
import {
  CORRECTION_STATUS,
  DOCUMENT_STATUS,
  REQUEST_STATUS,
  TABS,
  awaitingDecision,
  currentMonth,
  dayState,
  duration,
  initials,
  isTab,
  orDash,
  todayIso,
  type Tab,
} from '../features/employee/model';

type Person = Record<string, unknown>;

const text = (person: Person, key: string): string | null => {
  const value = person[key];
  return value === null || value === undefined ? null : String(value);
};

export function EmployeePage() {
  const { id = '' } = useParams();
  const session = useSession();
  const mine = useMemo(
    () =>
      new Set(
        session.status === 'authenticated' ? session.user.permissions : [],
      ),
    [session],
  );
  const rights = {
    attendance: mine.has('attendance.read'),
    absences: mine.has('absences.read'),
    audit: mine.has('audit.read'),
    manage: mine.has('employees.manage'),
    manual: mine.has('attendance.manual'),
    correct: mine.has('attendance.correct'),
    decide: mine.has('absences.approve'),
    export: mine.has('reports.export'),
  };

  const [params, setParams] = useSearchParams();
  const raw = params.get('tab');
  const tab: Tab = isTab(raw) ? raw : 'overview';
  // Адрес списка сохраняется целиком: возврат обязан вернуть человека
  // к его поиску и фильтрам, а не к первой странице.
  const back = params.get('back') ?? '';

  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  const open = useCallback(
    (next: Tab) => setParams((was) => {
      const copy = new URLSearchParams(was);
      copy.set('tab', next);
      return copy;
    }, { replace: false }),
    [setParams],
  );

  const [card, reloadCard] = useBlock(
    (signal) => api.employee(id, signal),
    `employee|${id}|${attempt}`,
    Boolean(id),
  );

  const person: Person = card.state === 'ready' ? card.data : {};
  const name = text(person, 'full_name') ?? '';
  const zone = session.status === 'authenticated' ? session.user.timezone : '';

  return (
    <AppShell breadcrumb="Сотрудники" section="employees">
      <p className="crumbs crumbs--inline">
        <Link className="link" to={back || '/employees'}><AppIcon name="back" size={16} /> Все сотрудники</Link>
        <span className="dot">/</span>
        <span>{name || 'Карточка сотрудника'}</span>
      </p>

      {card.state === 'loading' && (
        <p className="empty" role="status">Открываем карточку…</p>
      )}
      {card.state === 'denied' && (
        <p className="empty empty--bad">
          Карточка закрыта вашей областью доступа.
        </p>
      )}
      {card.state === 'error' && (
        <p className="empty empty--bad">
          Не удалось открыть карточку.{' '}
          <button type="button" className="link" onClick={reloadCard}>
            Повторить
          </button>
        </p>
      )}

      {card.state === 'ready' && (
        <>
          <Header person={person} zone={zone} rights={rights} />

          <div className="tabs tabs--bare" role="tablist" aria-label="Разделы карточки">
            {TABS.map((item) => (
              <button key={item.key} type="button" role="tab"
                      aria-selected={tab === item.key}
                      className={`tab${tab === item.key ? ' tab--on' : ''}`}
                      onClick={() => open(item.key)}>
                {item.title}
              </button>
            ))}
          </div>

          {tab === 'overview' && (
            <Overview id={id} person={person} zone={zone} rights={rights}
                      onGo={open} onChanged={reload} />
          )}
          {tab === 'attendance' && (
            <Attendance
              id={id}
              rights={{
                attendance: rights.attendance,
                export: rights.export,
                correct: rights.correct,
              }}
              zone={zone}
            />
          )}
          {tab === 'schedule' && <Schedule id={id} />}
          {tab === 'requests' && (
            <Requests id={id} rights={rights} zone={zone} />
          )}
          {tab === 'history' && (
            <History id={id} rights={rights} zone={zone} />
          )}
        </>
      )}
    </AppShell>
  );
}

type Rights = {
  attendance: boolean; absences: boolean; audit: boolean; manage: boolean;
  manual: boolean; correct: boolean; decide: boolean; export: boolean;
};

// --- общая шапка -------------------------------------------------------------

function Header({ person, zone, rights }: {
  person: Person; zone: string; rights: Rights;
}) {
  const status = text(person, 'employment_status');
  const id = text(person, 'id');
  // Само изображение в карточке не приходит — только признак того, что
  // оно есть. Отдаётся оно отдельным адресом, где право спрашивается
  // при каждом открытии.
  const hasPhoto = person['photo'] !== null && person['photo'] !== undefined;
  return (
    <header className="who who--card">
      {hasPhoto && id ? (
        <img className="avatar avatar--big avatar--photo"
             src={api.employeePhotoUrl(id)} alt="" />
      ) : (
        <span className="avatar avatar--big" aria-hidden="true">
          {initials(text(person, 'first_name'), text(person, 'last_name'))}
        </span>
      )}
      <span className="who__text">
        <span className="who__name">{text(person, 'full_name') ?? '—'}</span>
        <span className="who__id">
          Табельный {orDash(text(person, 'employee_number'))}
        </span>
        <span className="who__where">
          {[
            text(person, 'position_name'),
            text(person, 'department_name'),
            text(person, 'office_name'),
          ].filter(Boolean).join(' · ') || 'Назначение не указано'}
        </span>
      </span>
      <span className="who__side">
        <span className="state">
          <span className="state__dot" aria-hidden="true" />
          {status === 'ACTIVE' ? 'Работает'
            : status === 'TERMINATED' ? 'Уволен'
            : orDash(status)}
        </span>
        {text(person, 'hire_date') && (
          <span className="muted">
            В штате с {shortDate(`${text(person, 'hire_date')}T00:00:00Z`, zone)}
          </span>
        )}
      </span>
      {rights.manage && (
        <span className="who__actions">
          <Link className="btn btn--dark" to="/employees">Редактировать</Link>
        </span>
      )}
    </header>
  );
}

/** Анкетные значения словами. Кода в карточке человек видеть не должен. */
const GENDER: Record<string, string> = {
  MALE: 'Мужской',
  FEMALE: 'Женский',
};

const MARITAL: Record<string, string> = {
  SINGLE: 'Не женат / не замужем',
  MARRIED: 'Женат / замужем',
  DIVORCED: 'Разведён(а)',
  WIDOWED: 'Вдовец / вдова',
};

const PAPER_KIND: Record<string, string> = {
  IDENTITY: 'Паспорт или ID-карта',
  CONTRACT: 'Трудовой договор',
  HIRE_ORDER: 'Приказ о приёме',
  OTHER: 'Другой документ',
};

const PAPER_STATE: Record<string, string> = {
  MISSING: 'Не добавлен',
  UPLOADED: 'Загружен',
  GENERATED_LATER: 'Будет сформирован',
  REVIEW: 'Требует проверки',
};

/**
 * Документы сотрудника.
 *
 * Строка с файлом — ссылка: она открывает и позволяет сохранить. Строка
 * без файла остаётся строкой: чек-лист показывает, чего ещё нет, и
 * кнопка на ней обещала бы открыть несуществующее.
 */
function Papers({ person }: { person: Person }) {
  const id = text(person, 'id');
  const rows = Array.isArray(person['documents'])
    ? (person['documents'] as Record<string, unknown>[])
    : [];

  return (
    <section className="panel">
      <h3 className="panel__title">Документы</h3>
      {rows.length === 0 ? (
        <p className="muted">Документов пока нет.</p>
      ) : (
        <ul className="papers">
          {rows.map((row) => {
            const file = row['file_id'];
            const key = String(row['id'] ?? '');
            const kind = String(row['kind'] ?? '');
            const title = String(row['title'] ?? '') || PAPER_KIND[kind] || 'Документ';
            const state = String(row['status'] ?? '');
            return (
              <li key={key} className="papers__row">
                <AppIcon name="doc" size={18} />
                <span className="papers__title">{title}</span>
                {file && id ? (
                  <a className="link link--go"
                     href={api.employeeDocumentUrl(id, key)}
                     target="_blank" rel="noreferrer">
                    Открыть
                    <AppIcon name="arrow" size={16} />
                  </a>
                ) : (
                  <span className="muted">{PAPER_STATE[state] ?? state}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// --- вкладка «Обзор» ---------------------------------------------------------

function Overview({ id, person, zone, rights, onGo, onChanged }: {
  id: string; person: Person; zone: string; rights: Rights;
  onGo: (tab: Tab) => void; onChanged: () => void;
}) {
  const month = useMemo(() => currentMonth(), []);
  const today = useMemo(() => todayIso(), []);

  const [journal] = useBlock(
    (signal) => api.attendanceDaily(
      { employee_id: id, date_from: month.first, date_to: month.last }, signal,
    ),
    `overview-journal|${id}|${month.first}`,
    rights.attendance,
  );
  const [requests] = useBlock(
    (signal) => api.queue({ employee_id: id, limit: '5' }, signal),
    `overview-requests|${id}`,
    rights.absences,
  );
  const [link] = useBlock(
    (signal) => api.employeeTelegram(id, signal).catch(() => null),
    `overview-telegram|${id}`,
  );

  const report = journal.state === 'ready' ? journal.data : null;
  const todayRow = report?.days.find((row) => row.day === today) ?? null;
  const waiting = requests.state === 'ready'
    ? requests.data.items.filter((item) =>
        item.kind === 'absence'
          ? awaitingDecision(String(item.absence?.status ?? ''))
          : String(item.correction?.status ?? '') === 'PENDING').length
    : null;

  return (
    <>
      <ul className="cards cards--four" aria-label="Показатели сотрудника">
        <Tile icon="clock" title="Сегодня в офисе" note={`За ${today}`}
              value={todayRow ? duration(todayRow.seconds) : '—'}
              hint={todayRow ? dayState(todayRow) : 'нет данных'}
              onGo={() => onGo('attendance')} />
        <Tile icon="calendar" title="За месяц"
              note={`${month.first} — ${month.last}`}
              value={report ? duration(report.totals.seconds) : '—'}
              onGo={() => onGo('attendance')} />
        <Tile icon="check" title="Дней с отметками" note="За тот же месяц"
              value={report ? String(report.totals.days_with_marks) : '—'}
              {...(report ? { hint: `рабочих ${report.totals.working_days}` } : {})}
              onGo={() => onGo('attendance')} />
        <Tile icon="inbox" title="Заявок ждут решения" note="Все открытые"
              value={waiting === null ? '—' : String(waiting)}
              onGo={() => onGo('requests')} />
      </ul>

      <div className="split split--open">
        <div className="setup__forms">
          <section className="panel">
            <h3 className="panel__title">Профиль</h3>
            <dl className="facts">
              <Fact label="Дата приёма" value={text(person, 'hire_date')} />
              <Fact label="Должность" value={text(person, 'position_name')} />
              <Fact label="Отдел" value={text(person, 'department_name')} />
              <Fact label="Офис" value={text(person, 'office_name')} />
              <Fact label="Телефон" value={text(person, 'phone')} />
              <Fact label="Рабочая почта" value={text(person, 'corporate_email')} />
              <Fact label="Табельный номер" value={text(person, 'employee_number')} />
              <Fact label="Пол" value={GENDER[text(person, 'gender') ?? ''] ?? null} />
              <Fact label="Семейное положение"
                    value={MARITAL[text(person, 'marital_status') ?? ''] ?? null} />
            </dl>
          </section>

          <Papers person={person} />

          <section className="panel">
            <h3 className="panel__title">Последние заявки</h3>
            {!rights.absences ? (
              <p className="muted">Нет права на заявки.</p>
            ) : (
              <RequestList block={requests} zone={zone} picked={null}
                           onPick={() => onGo('requests')} compact />
            )}
          </section>
        </div>

        <div className="setup__aside">
          <section className="panel">
            <h3 className="panel__title">Telegram</h3>
            <TelegramBlock id={id} block={link} onChanged={onChanged} />
          </section>

          <section className="panel">
            <h3 className="panel__title">Сегодняшний день</h3>
            {!rights.attendance ? (
              <p className="muted">Нет права на посещаемость.</p>
            ) : journal.state === 'loading' ? (
              <p className="empty" role="status">Считаем…</p>
            ) : journal.state === 'error' ? (
              <p className="empty empty--bad">Не удалось получить отметки.</p>
            ) : todayRow ? (
              <dl className="facts">
                <Fact label="Состояние" value={dayState(todayRow)} />
                <Fact label="Первый вход"
                      value={todayRow.first_entry_at
                        ? clockOnDay(todayRow.first_entry_at, todayRow.timezone, todayRow.day)
                        : null} />
                <Fact label="Последний выход"
                      value={todayRow.last_exit_at
                        ? clockOnDay(todayRow.last_exit_at, todayRow.timezone, todayRow.day)
                        : todayRow.open_session_id ? 'Сессия открыта' : null} />
                <Fact label="Сессий" value={String(todayRow.sessions)} />
                <Fact label="Время в офисе" value={duration(todayRow.seconds)} />
              </dl>
            ) : (
              <p className="muted">За сегодня отметок нет.</p>
            )}
          </section>
        </div>
      </div>

    </>
  );
}

/**
 * Показатель. Оформление — существующий `.metric`, тот же, что на
 * главной и в аналитике.
 *
 * Своих классов здесь заводить было нельзя: `.card` в этом проекте
 * не существует вовсе, и плитка, названная так, оставалась без фона,
 * рамки, отступов и без крупного значения — рисовался голый текст.
 *
 * `onGo` необязателен: показатель без перехода остаётся текстом, а не
 * превращается в кнопку, которая ничего не делает.
 */
function Tile({ icon, title, note, value, hint, onGo }: {
  icon: AppIconName;
  title: string;
  note: string;
  value: string;
  hint?: string;
  onGo?: () => void;
}) {
  const body = (
    <>
      <span className="metric__head">
        <AppIcon name={icon} size={16} />
        <span>{title}</span>
      </span>
      <span className="metric__value">{value}</span>
      <span className="metric__note">{note}{hint ? ` \u00b7 ${hint}` : ''}</span>
    </>
  );
  if (!onGo) return <li className="metric">{body}</li>;
  return (
    <li className="metric-cell">
      <button type="button" className="metric metric--go" onClick={onGo}>
        {body}
      </button>
    </li>
  );
}

function Fact({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="facts__row">
      <dt>{label}</dt>
      <dd>{orDash(value)}</dd>
    </div>
  );
}

function TelegramBlock({ id, block, onChanged }: {
  id: string; block: Block<api.TelegramLink | null>; onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (block.state === 'loading') return <p className="empty" role="status">Проверяем…</p>;
  if (block.state !== 'ready') {
    return <p className="empty empty--bad">Состояние привязки недоступно.</p>;
  }
  const link = block.data;
  const state = link?.state ?? null;

  return (
    <div className="tg-block">
      <p className="tg__state">
        {state === 'ACTIVE' ? 'Привязан'
          : state === 'PENDING' ? 'Приглашение отправлено, ожидает подтверждения'
          : 'Не привязан'}
      </p>
      {state !== 'ACTIVE' && (
        <button type="button" className="btn" disabled={busy}
                onClick={async () => {
                  setBusy(true); setError(null);
                  try {
                    await api.inviteToTelegram(id);
                    onChanged();
                  } catch (failure) {
                    setError(failure instanceof ApiFailure
                      ? messageFor(failure) : 'Не удалось создать приглашение.');
                  } finally { setBusy(false); }
                }}>
          {busy ? 'Создаём…' : 'Пригласить в Telegram'}
        </button>
      )}
      {error && <p className="form-grid__error" role="alert">{error}</p>}
    </div>
  );
}

function Schedule({ id }: { id: string }) {
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

  const current = data.state === 'ready'
    ? data.data.schedules.find((row) => !row.valid_to) ?? null
    : null;

  // Карточка графика — вторым запросом и только когда есть что спрашивать:
  // назначение знает лишь имя графика, а недельная полоса собирается
  // из его дней.
  const [detail] = useBlock(
    (signal) => api.workSchedule(current?.schedule_id ?? '', signal),
    `schedule-detail|${current?.schedule_id ?? 'none'}`,
    Boolean(current?.schedule_id),
  );

  if (data.state === 'loading') return <p className="empty" role="status">Загружаем…</p>;
  if (data.state === 'denied') {
    return <p className="empty empty--bad">Назначения закрыты вашей областью доступа.</p>;
  }
  if (data.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось загрузить назначения.{' '}
        <button type="button" className="link" onClick={reload}>Повторить</button>
      </p>
    );
  }

  const post = data.data.assignments.find((row) => !row.valid_to) ?? null;

  return (
    <>
      <div className="split split--open">
        <section className="panel">
          <h3 className="panel__title">Текущее назначение</h3>
          {post ? (
            <dl className="facts">
              <Fact label="Регион" value={post.region_name} />
              <Fact label="Офис" value={post.office_name} />
              <Fact label="Отдел" value={post.department_name} />
              <Fact label="Должность" value={post.position_name} />
              <Fact label="Действует с" value={post.valid_from} />
            </dl>
          ) : (
            <p className="empty">Действующего назначения нет.</p>
          )}
          <p className="field__hint">
            Перевод создаёт новый период и закрывает прежний: история
            сохраняется целиком, а прошлые расчёты не меняются.
          </p>
        </section>

        <section className="panel">
          <h3 className="panel__title">Текущий график</h3>
          {!current ? (
            <p className="empty">График не назначен.</p>
          ) : detail.state === 'ready' ? (
            <dl className="facts">
              <Fact label="Название" value={detail.data.name} />
              <Fact label="Часовой пояс" value={detail.data.timezone} />
              <Fact label="Часов в неделю"
                    value={`${Math.round(detail.data.weekly_minutes / 60)}`} />
              <Fact label="Допуск опоздания"
                    value={detail.data.late_grace_minutes === null
                      ? null : `${detail.data.late_grace_minutes} мин`} />
              <Fact label="Действует с" value={current.valid_from} />
            </dl>
          ) : detail.state === 'error' ? (
            <p className="empty empty--bad">Карточку графика получить не удалось.</p>
          ) : (
            <p className="empty" role="status">Читаем график…</p>
          )}
        </section>
      </div>

      <section className="panel">
        <h3 className="panel__title">Неделя по графику</h3>
        {!current ? (
          <p className="empty">График не назначен.</p>
        ) : detail.state === 'ready' ? (
          <Week days={detail.data.days} />
        ) : (
          <p className="empty" role="status">Читаем график…</p>
        )}
        <p className="field__hint">
          Полоса показывает график как он назначен. Праздники и переносы
          применяются расчётом на стороне сервера в день события.
        </p>
      </section>

      <div className="split split--open">
        <section className="panel">
          <h3 className="panel__title">История назначений</h3>
          <Periods rows={data.data.assignments.map((row) => ({
            id: row.id,
            from: row.valid_from ?? '',
            to: row.valid_to,
            what: [row.office_name, row.department_name, row.position_name]
              .filter(Boolean).join(' · ') || 'Без подробностей',
          }))} empty="Записей о назначениях нет." />
        </section>

        <section className="panel">
          <h3 className="panel__title">История графиков</h3>
          <Periods rows={data.data.schedules.map((row) => ({
            id: row.id,
            from: row.valid_from,
            to: row.valid_to,
            what: row.schedule_name ?? 'График',
          }))} empty="Графиков не назначалось." />
        </section>
      </div>
    </>
  );
}

const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

/** Неделя рабочих интервалов. Выходной — не «нет данных», и наоборот. */
function Week({ days }: { days: api.ScheduleDay[] }) {
  const byWeekday = new Map(days.map((day) => [day.weekday, day]));
  return (
    <ul className="week" aria-label="Неделя по графику">
      {WEEKDAYS.map((title, index) => {
        const day = byWeekday.get(index + 1);
        const working = Boolean(day?.is_working_day);
        return (
          <li key={title} className={`week__day${working ? ' week__day--on' : ''}`}>
            <span className="week__title">{title}</span>
            <span className="week__hours">
              {!day ? 'нет данных'
                : working ? `${(day.start_time ?? '').slice(0, 5)} — ${(day.end_time ?? '').slice(0, 5)}`
                : 'выходной'}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * История периодов: действующие, будущие и завершённые — разными словами.
 *
 * Даты приходят с сервера и здесь не пересчитываются: смена пояса
 * отображения CRM не должна сдвигать границы кадровых периодов.
 */
function Periods({ rows, empty }: {
  rows: Array<{ id: string; from: string; to: string | null; what: string }>;
  empty: string;
}) {
  const today = todayIso();
  if (rows.length === 0) return <p className="empty">{empty}</p>;
  return (
    <div className="scroller">
      <table className="grid-table" aria-label="История периодов">
        <thead>
          <tr>
            <th scope="col">Период</th>
            <th scope="col">Что</th>
            <th scope="col">Состояние</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.from} — {row.to ?? 'по настоящее время'}</td>
              <td>{row.what}</td>
              <td>
                {row.from > today ? 'Начнётся позже'
                  : row.to && row.to < today ? 'Завершено'
                  : 'Действует'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- вкладка «Заявки и документы» --------------------------------------------

function Requests({ id, rights, zone }: {
  id: string; rights: Rights; zone: string;
}) {
  const [status, setStatus] = useState('');
  const [kind, setKind] = useState('');
  const [picked, setPicked] = useState<string | null>(null);

  const [list, reloadList] = useBlock(
    (signal) => api.queue(
      {
        employee_id: id,
        ...(status ? { status } : {}),
        ...(kind ? { kind } : {}),
        limit: '50',
      },
      signal,
    ),
    `requests|${id}|${status}|${kind}`,
    rights.absences,
  );

  if (!rights.absences) {
    return (
      <p className="empty empty--bad">
        Нет права на заявки. Это отдельное разрешение —
        попросите <span className="mono">absences.read</span>.
      </p>
    );
  }

  const items = list.state === 'ready' ? list.data.items : [];
  const chosen = items.find((item) => item.id === picked) ?? null;

  return (
    <>
      <div className="toolbar toolbar--thin">
        <label className="pick">
          <select value={kind} aria-label="Тип заявки"
                  onChange={(event) => { setKind(event.target.value); setPicked(null); }}>
            <option value="">Все типы</option>
            <option value="absence">Отсутствия</option>
            <option value="correction">Исправления отметок</option>
          </select>
        </label>
        <label className="pick">
          <select value={status} aria-label="Состояние"
                  onChange={(event) => { setStatus(event.target.value); setPicked(null); }}>
            <option value="">Все состояния</option>
            <option value="SUBMITTED">На рассмотрении</option>
            <option value="APPROVED">Подтверждены</option>
            <option value="REJECTED">Отклонены</option>
            <option value="CANCELLED">Отменены</option>
          </select>
        </label>
        <span className="toolbar__note">
          {list.state === 'ready'
            ? `Показано ${items.length}${list.data.has_more ? ' — есть ещё' : ''}`
            : ''}
        </span>
      </div>

      <div className="split split--open">
        <section className="panel panel--list">
          <RequestList block={list} zone={zone} picked={picked}
                       onPick={setPicked} compact={false} />
        </section>
        <section className="panel panel--view">
          {chosen ? (
            <RequestDetails item={chosen} zone={zone} rights={rights}
                            onClose={() => setPicked(null)}
                            onDone={() => { setPicked(null); reloadList(); }} />
          ) : (
            <p className="empty">Выберите заявку, чтобы увидеть подробности.</p>
          )}
        </section>
      </div>
    </>
  );
}

function RequestList({ block, zone, picked, onPick, compact }: {
  block: Block<api.Cursored<api.QueueItem>>;
  zone: string;
  picked: string | null;
  onPick: (id: string) => void;
  compact: boolean;
}) {
  if (block.state === 'loading') return <p className="empty" role="status">Загружаем…</p>;
  if (block.state === 'denied') return <p className="empty empty--bad">Заявки закрыты правами.</p>;
  if (block.state === 'error') return <p className="empty empty--bad">Не удалось загрузить заявки.</p>;
  if (block.data.items.length === 0) return <p className="empty">Заявок нет.</p>;

  return (
    <ul className="roster">
      {block.data.items.map((item) => {
        const body = item.kind === 'absence' ? item.absence : item.correction;
        const status = String(body?.status ?? '');
        return (
          <li key={item.id}>
            <button type="button"
                    className={`roster__go${picked === item.id ? ' roster__go--on' : ''}`}
                    onClick={() => onPick(item.id)}>
              <span className="who">
                <span className="who__text">
                  <span className="who__name">
                    {item.kind === 'absence'
                      ? orDash((item.absence as Record<string, unknown> | undefined)?.['absence_type_name'])
                      : 'Исправление отметки'}
                  </span>
                  <span className="who__id">
                    {moment(item.created_at, zone, false)}
                    {compact ? '' : ` · ${item.kind === 'absence'
                      ? (REQUEST_STATUS[status] ?? status)
                      : (CORRECTION_STATUS[status] ?? status)}`}
                  </span>
                </span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function RequestDetails({ item, zone, rights, onClose, onDone }: {
  item: api.QueueItem; zone: string; rights: Rights;
  onClose: () => void; onDone: () => void;
}) {
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const absence = item.kind === 'absence'
    ? (item.absence as Record<string, unknown> | undefined) : undefined;
  const correction = item.kind === 'correction'
    ? (item.correction as Record<string, unknown> | undefined) : undefined;
  const status = String((absence ?? correction)?.['status'] ?? '');
  const documents = (absence?.['documents'] as Array<Record<string, unknown>>) ?? [];

  const decide = async (decision: 'approve' | 'reject') => {
    setBusy(true);
    setError(null);
    try {
      if (item.kind === 'absence') await api.decideAbsence(item.id, decision, comment);
      else await api.decideCorrection(item.id, decision, comment);
      onDone();
    } catch (failure) {
      // Введённый комментарий переживает отказ: набирать его заново
      // из-за сетевой ошибки человек не должен.
      setError(failure instanceof ApiFailure
        ? messageFor(failure) : 'Не удалось отправить решение.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="side-panel__head">
        <h3 className="side-panel__title">
          {item.kind === 'absence' ? 'Заявка на отсутствие' : 'Исправление отметки'}
        </h3>
        <button type="button" className="nav" onClick={onClose} aria-label="Закрыть">
          <AppIcon name="cross" size={16} />
        </button>
      </div>

      <dl className="facts">
        <Fact label="Состояние"
              value={item.kind === 'absence'
                ? (REQUEST_STATUS[status] ?? status)
                : (CORRECTION_STATUS[status] ?? status)} />
        <Fact label="Подана" value={moment(item.created_at, zone, false)} />
        {absence && (
          <>
            <Fact label="Тип" value={String(absence['absence_type_name'] ?? '')} />
            <Fact label="С" value={String(absence['requested_start_at'] ?? '')} />
            <Fact label="По" value={String(absence['requested_end_at'] ?? '')} />
            <Fact label="Комментарий сотрудника"
                  value={String(absence['employee_comment'] ?? '')} />
          </>
        )}
        {correction && (
          <Fact label="Причина" value={String(correction['reason'] ?? '')} />
        )}
      </dl>

      {absence && (
        <>
          <h4 className="side-panel__label">Документы</h4>
          {documents.length === 0 ? (
            <p className="muted">
              Документа нет. Отсутствие не считается подтверждённым только
              потому, что файл загружен, — и наоборот.
            </p>
          ) : (
            <ul className="marks">
              {documents.map((doc) => (
                <li key={String(doc['id'])} className="marks__line">
                  <span className="marks__what">{orDash(doc['file_name'])}</span>
                  <span className="marks__kind">
                    {DOCUMENT_STATUS[String(doc['verification_status'] ?? '')]
                      ?? orDash(doc['verification_status'])}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {awaitingDecision(status) && rights.decide ? (
        <>
          <label className="form-grid__field">
            <span className="form-grid__label">Комментарий к решению</span>
            <textarea className="form-grid__input area" value={comment} rows={2}
                      aria-label="Комментарий к решению"
                      onChange={(event) => setComment(event.target.value)} />
          </label>
          <div className="side-panel__actions">
            <button type="button" className="btn" disabled={busy}
                    onClick={() => void decide('reject')}>Отклонить</button>
            <button type="button" className="btn btn--dark" disabled={busy}
                    onClick={() => void decide('approve')}>
              {busy ? 'Отправляем…' : 'Подтвердить'}
            </button>
          </div>
        </>
      ) : (
        <p className="field__hint">
          {awaitingDecision(status)
            ? 'Решение принимает тот, у кого есть право рассмотрения.'
            : 'Заявка уже рассмотрена: повторное решение процессом не предусмотрено.'}
        </p>
      )}
      {error && <p className="form-grid__error" role="alert">{error}</p>}
    </>
  );
}

// --- вкладка «История изменений» ---------------------------------------------

function History({ id, rights, zone }: {
  id: string; rights: Rights; zone: string;
}) {
  const [action, setAction] = useState('');
  const [picked, setPicked] = useState<string | null>(null);

  const [log, reloadLog] = useBlock(
    (signal) => api.auditLogs(
      { employee_id: id, ...(action ? { action } : {}), limit: '50' }, signal,
    ),
    `history|${id}|${action}`,
    rights.audit,
  );

  if (!rights.audit) {
    return (
      <p className="empty empty--bad">
        Нет права на журнал изменений. В нём видно, кто и что менял, поэтому
        это отдельное разрешение — попросите <span className="mono">audit.read</span>.
      </p>
    );
  }

  const rows = log.state === 'ready' ? log.data.items : [];
  const chosen = rows.find((row) => row.id === picked) ?? null;

  return (
    <>
      <div className="toolbar toolbar--thin">
        <label className="pick">
          <select value={action} aria-label="Действие"
                  onChange={(event) => { setAction(event.target.value); setPicked(null); }}>
            <option value="">Все действия</option>
            <option value="employee.">Профиль и переводы</option>
            <option value="attendance.">Посещаемость</option>
            <option value="absence">Заявки на отсутствие</option>
            <option value="schedule">Графики</option>
          </select>
        </label>
        <span className="toolbar__note">
          {log.state === 'ready'
            ? `Показано ${rows.length}${log.data.has_more ? ' — есть ещё' : ''}`
            : ''}
        </span>
      </div>

      <div className="split split--open">
        <section className="panel panel--list">
          {log.state === 'loading' && <p className="empty" role="status">Загружаем…</p>}
          {log.state === 'error' && (
            <p className="empty empty--bad">
              Не удалось загрузить журнал.{' '}
              <button type="button" className="link" onClick={reloadLog}>Повторить</button>
            </p>
          )}
          {log.state === 'ready' && rows.length === 0 && (
            <p className="empty">Записей об этом сотруднике пока нет.</p>
          )}
          {log.state === 'ready' && rows.length > 0 && (
            <ul className="roster">
              {rows.map((row) => (
                <li key={row.id}>
                  <button type="button"
                          className={`roster__go${picked === row.id ? ' roster__go--on' : ''}`}
                          onClick={() => setPicked(row.id)}>
                    <span className="who">
                      <span className="who__text">
                        <span className="who__name">{row.action}</span>
                        <span className="who__id">
                          {moment(row.occurred_at, zone, false)}
                          {row.actor_email ? ` · ${row.actor_email}` : ''}
                        </span>
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel panel--view">
          {chosen ? (
            <>
              <div className="side-panel__head">
                <h3 className="side-panel__title">{chosen.action}</h3>
                <button type="button" className="nav" aria-label="Закрыть"
                        onClick={() => setPicked(null)}>
                  <AppIcon name="cross" size={16} />
                </button>
              </div>
              <dl className="facts">
                <Fact label="Когда" value={moment(chosen.occurred_at, zone, false)} />
                <Fact label="Инициатор"
                      value={chosen.actor_email ?? chosen.actor_user_id} />
                <Fact label="Объект" value={chosen.entity_type} />
                <Fact label="Идентификатор объекта" value={chosen.entity_id} />
              </dl>
              <h4 className="side-panel__label">Было → стало</h4>
              <Diff before={chosen.old_values} after={chosen.new_values} />
              <p className="field__hint">
                Показаны значения из журнала на момент события. Сегодняшние
                названия сюда не подставляются: если записан только
                идентификатор, он и показан.
              </p>
            </>
          ) : (
            <p className="empty">Выберите событие, чтобы увидеть подробности.</p>
          )}
        </section>
      </div>
    </>
  );
}

function Diff({ before, after }: {
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}) {
  const keys = [...new Set([
    ...Object.keys(before ?? {}), ...Object.keys(after ?? {}),
  ])];
  if (keys.length === 0) return <p className="muted">Значения не записаны.</p>;
  return (
    <ul className="marks">
      {keys.map((key) => (
        <li key={key} className="marks__line">
          <span className="marks__kind">{key}</span>
          <span className="marks__what">{orDash(before?.[key])}</span>
          <span className="marks__time"><AppIcon name="arrow" size={16} /> {orDash(after?.[key])}</span>
        </li>
      ))}
    </ul>
  );
}
