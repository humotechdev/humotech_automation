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
import { Icon } from '../components/nav-icons';
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
  lateness,
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
        <Link className="link" to={back || '/employees'}>← Все сотрудники</Link>
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
            <Attendance id={id} rights={rights} zone={zone} />
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
  return (
    <header className="who who--card">
      <span className="avatar avatar--big" aria-hidden="true">
        {initials(text(person, 'first_name'), text(person, 'last_name'))}
      </span>
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
          <Link className="btn" to="/employees">Редактировать</Link>
        </span>
      )}
    </header>
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
        <Tile title="Сегодня в офисе" note={`За ${today}`}
              value={todayRow ? duration(todayRow.seconds) : '—'}
              hint={todayRow ? dayState(todayRow) : 'Нет данных за сегодня'}
              onGo={() => onGo('attendance')} />
        <Tile title="За месяц" note={`${month.first} — ${month.last}`}
              value={report ? duration(report.totals.seconds) : '—'}
              hint="Сумма учитываемых интервалов"
              onGo={() => onGo('attendance')} />
        <Tile title="Дней с отметками" note="За тот же месяц"
              value={report ? String(report.totals.days_with_marks) : '—'}
              hint={report ? `Рабочих дней ${report.totals.working_days}` : ''}
              onGo={() => onGo('attendance')} />
        <Tile title="Заявок ждут решения" note="Все открытые"
              value={waiting === null ? '—' : String(waiting)}
              hint="Отпуска, больничные и исправления"
              onGo={() => onGo('requests')} />
      </ul>

      <div className="split split--open">
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
          </dl>
        </section>

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

      <section className="panel">
        <h3 className="panel__title">Последние заявки</h3>
        {!rights.absences ? (
          <p className="muted">Нет права на заявки.</p>
        ) : (
          <RequestList block={requests} zone={zone} picked={null}
                       onPick={() => onGo('requests')} compact />
        )}
      </section>
    </>
  );
}

function Tile({ title, note, value, hint, onGo }: {
  title: string; note: string; value: string; hint: string;
  onGo: () => void;
}) {
  return (
    <li className="card">
      <button type="button" className="card__go" onClick={onGo}>
        <span className="card__title">{title}</span>
        <span className="card__value">{value}</span>
        <span className="card__note">{note}</span>
        {hint && <span className="card__hint">{hint}</span>}
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
    <>
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
    </>
  );
}

// --- вкладка «Посещаемость» --------------------------------------------------

function Attendance({ id, rights, zone }: {
  id: string; rights: Rights; zone: string;
}) {
  const month = useMemo(() => currentMonth(), []);
  const [first, setFirst] = useState(month.first);
  const [last, setLast] = useState(month.last);
  const [state, setState] = useState('');
  const [picked, setPicked] = useState<string | null>(null);

  const [journal, reloadJournal] = useBlock(
    (signal) => api.attendanceDaily(
      { employee_id: id, date_from: first, date_to: last }, signal,
    ),
    `journal|${id}|${first}|${last}`,
    rights.attendance,
  );

  if (!rights.attendance) {
    return (
      <p className="empty empty--bad">
        Нет права на посещаемость. Это отдельное разрешение —
        попросите <span className="mono">attendance.read</span>.
      </p>
    );
  }

  const report = journal.state === 'ready' ? journal.data : null;
  const rows = (report?.days ?? []).filter(
    (row) => !state || row.state === state,
  );

  return (
    <>
      <div className="toolbar toolbar--thin">
        <label className="pick">
          <span className="muted">С</span>
          <input type="date" value={first} aria-label="Начало периода"
                 onChange={(event) => setFirst(event.target.value)} />
        </label>
        <label className="pick">
          <span className="muted">по</span>
          <input type="date" value={last} aria-label="Конец периода"
                 onChange={(event) => setLast(event.target.value)} />
        </label>
        <label className="pick">
          <select value={state} aria-label="Состояние"
                  onChange={(event) => setState(event.target.value)}>
            <option value="">Все состояния</option>
            <option value="IN_OFFICE">В офисе</option>
            <option value="LEFT">Ушёл</option>
            <option value="NOT_COME">Нет отметки</option>
            <option value="DAY_OFF">Выходной</option>
            <option value="NO_SCHEDULE">Без графика</option>
            <option value="VACATION">Отпуск</option>
            <option value="SICK_LEAVE">Больничный</option>
          </select>
        </label>
        {rights.export && (
          <Link className="btn"
                to={`/reports?kind=sessions&employee_id=${id}&date_from=${first}&date_to=${last}`}>
            Выгрузка
          </Link>
        )}
      </div>

      {journal.state === 'loading' && (
        <p className="empty" role="status">Считаем журнал…</p>
      )}
      {journal.state === 'error' && (
        <p className="empty empty--bad">
          Не удалось получить журнал.{' '}
          <button type="button" className="link" onClick={reloadJournal}>
            Повторить
          </button>
        </p>
      )}

      {report && (
        <>
          {report.note && <p className="note note--dim">{report.note}</p>}

          <ul className="cards cards--four" aria-label="Показатели за период">
            <Tile title="Время в офисе" note="За весь период"
                  value={duration(report.totals.seconds)}
                  hint="Сумма учитываемых интервалов" onGo={() => undefined} />
            <Tile title="Дней с отметками" note="За весь период"
                  value={String(report.totals.days_with_marks)}
                  hint={`Рабочих дней ${report.totals.working_days}`}
                  onGo={() => undefined} />
            <Tile title="Опозданий" note="Сверх допуска графика"
                  value={String(report.totals.late_days)}
                  hint={report.totals.late_minutes
                    ? `Всего ${report.totals.late_minutes} мин` : ''}
                  onGo={() => undefined} />
            <Tile title="Незакрытых сессий" note="Без отметки выхода"
                  value={String(report.totals.open_sessions)}
                  hint="Требуют разбора" onGo={() => undefined} />
          </ul>

          <div className="split split--open">
            <section className="panel panel--list">
              <div className="scroller">
                <table className="grid-table" aria-label="Журнал по дням">
                  <thead>
                    <tr>
                      <th scope="col">Дата</th>
                      <th scope="col">Первый вход</th>
                      <th scope="col">Последний выход</th>
                      <th scope="col">В офисе</th>
                      <th scope="col">Сессий</th>
                      <th scope="col">Состояние</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.day}
                          className={`row${picked === row.day ? ' row--on' : ''}`}>
                        <td>
                          <button type="button" className="linky"
                                  onClick={() => setPicked(row.day)}>
                            {row.day}
                          </button>
                        </td>
                        <td>{row.first_entry_at
                          ? clockOnDay(row.first_entry_at, row.timezone, row.day) : '—'}</td>
                        <td>{row.last_exit_at
                          ? clockOnDay(row.last_exit_at, row.timezone, row.day)
                          : row.open_session_id ? 'открыта' : '—'}</td>
                        <td>{duration(row.seconds)}</td>
                        <td>{row.sessions || '—'}</td>
                        <td>{dayState(row)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {rows.length === 0 && (
                <p className="empty">За выбранный период и состояние строк нет.</p>
              )}
              <HoursChart rows={report.days} today={todayIso()} />
            </section>

            <section className="panel panel--view">
              {picked ? (
                <DayDetails id={id} day={picked}
                            row={report.days.find((r) => r.day === picked) ?? null}
                            zone={zone} rights={rights}
                            onClose={() => setPicked(null)} />
              ) : (
                <p className="empty">Выберите день, чтобы увидеть его отметки.</p>
              )}
            </section>
          </div>
        </>
      )}
    </>
  );
}

/**
 * Компактный график часов по дням.
 *
 * Ноль и отсутствие данных различаются: у дня без отметок столбца нет
 * вовсе, а у дня с нулём — тонкая черта у основания. Будущие дни не
 * показываются пропусками, а сегодняшний помечен отдельно: он ещё
 * не кончился, и сравнивать его с полными днями нельзя.
 */
function HoursChart({ rows, today }: { rows: api.DailyRow[]; today: string }) {
  const shown = rows.filter((row) => row.day <= today);
  const top = Math.max(1, ...shown.map((row) => row.seconds));
  if (shown.length === 0) return null;
  return (
    <div className="spark" aria-label="Часы по дням">
      {shown.map((row) => {
        const height = Math.round((row.seconds / top) * 100);
        const unfinished = row.day === today;
        return (
          <span key={row.day} className="spark__slot"
                title={`${row.day} · ${duration(row.seconds)}${unfinished ? ' · день не закончен' : ''}`}>
            <span className={`spark__bar${unfinished ? ' spark__bar--now' : ''}`}
                  style={{ height: `${row.seconds > 0 ? Math.max(height, 2) : 0}%` }} />
          </span>
        );
      })}
    </div>
  );
}

function DayDetails({ id, day, row, zone, rights, onClose }: {
  id: string; day: string; row: api.DailyRow | null; zone: string;
  rights: Rights; onClose: () => void;
}) {
  const [detail] = useBlock(
    (signal) => Promise.all([
      api.events({ employee_id: id, date_from: day, date_to: day, limit: '50' }, signal),
      api.attendanceSessions({ employee_id: id, date_from: day, date_to: day, limit: '20' }, signal),
    ]).then(([events, sessions]) => ({ events: events.items, sessions: sessions.items })),
    `day|${id}|${day}`,
  );

  return (
    <>
      <div className="side-panel__head">
        <h3 className="side-panel__title">{day}</h3>
        <button type="button" className="nav" onClick={onClose} aria-label="Закрыть">
          <Icon name="cross" size={16} />
        </button>
      </div>

      {row && (
        <dl className="facts">
          <Fact label="Состояние" value={dayState(row)} />
          <Fact label="Офис" value={row.office_name} />
          <Fact label="Пояс дня" value={row.timezone} />
          <Fact label="Время в офисе" value={duration(row.seconds)} />
          <Fact label="Опоздание" value={lateness(row.late_minutes)} />
          <Fact label="Начало по графику" value={row.scheduled_start} />
        </dl>
      )}
      {row?.conflicting_marks && (
        <p className="note note--dim">
          В этот день есть и подтверждённое отсутствие, и отметки. Расхождение
          разбирает человек — само оно не исчезнет.
        </p>
      )}

      {detail.state === 'loading' && <p className="empty" role="status">Читаем отметки…</p>}
      {detail.state === 'error' && (
        <p className="empty empty--bad">Не удалось получить отметки дня.</p>
      )}
      {detail.state === 'ready' && (
        <>
          <h4 className="side-panel__label">Сессии</h4>
          {detail.data.sessions.length === 0 ? (
            <p className="muted">Сессий нет.</p>
          ) : (
            <ul className="marks">
              {detail.data.sessions.map((session) => (
                <li key={session.id} className="marks__row">
                  <span className="marks__time">
                    {clockOnDay(session.started_at, zone, day)} —{' '}
                    {session.ended_at ? clockOnDay(session.ended_at, zone, day) : 'открыта'}
                  </span>
                  <span className="marks__what">{duration(session.duration_seconds)}</span>
                  <span className="marks__where">{orDash(session.office_name)}</span>
                </li>
              ))}
            </ul>
          )}

          <h4 className="side-panel__label">Отметки</h4>
          {detail.data.events.length === 0 ? (
            <p className="muted">Событий нет.</p>
          ) : (
            <ul className="marks">
              {detail.data.events.map((mark) => (
                <li key={mark.id} className="marks__row">
                  <span className="marks__time">
                    {clockOnDay(mark.occurred_at, zone, day)}
                  </span>
                  <span className="marks__kind">
                    {mark.event_type === 'ENTRY' ? 'Вход' : 'Выход'}
                  </span>
                  <span className="marks__what">
                    {mark.source === 'MANUAL' ? 'Вручную' : mark.source}
                  </span>
                  <span className="marks__where">{orDash(mark.office_name)}</span>
                  <span className="marks__check">
                    {verification(mark)}
                  </span>
                </li>
              ))}
            </ul>
          )}

          <p className="field__hint">
            Исправление отметки — это решение по заявке, а не правка события:
            сырое событие не меняется никогда. Кадровик может добавить
            недостающую отметку отдельным действием с обязательной причиной,
            и она навсегда останется помеченной как ручная.
          </p>
          {rights.correct && (
            <Link className="link" to={`/requests?kind=correction&employee_id=${id}`}>
              Заявки на исправление →
            </Link>
          )}
        </>
      )}
    </>
  );
}

/** Что известно о проверке отметки. «Не проводилась» — честный ответ. */
function verification(mark: api.EventRow): string {
  const parts: string[] = [];
  if (mark.verification_status) parts.push(mark.verification_status);
  if (mark.inside_geofence === true) parts.push('в геозоне');
  else if (mark.inside_geofence === false) parts.push('вне геозоны');
  else parts.push('геопроверка не проводилась');
  return parts.join(' · ');
}

// --- вкладка «График и назначения» -------------------------------------------

function Schedule({ id }: { id: string }) {
  const [data] = useBlock(
    (signal) => Promise.all([
      api.employeeAssignments(id, signal),
      api.employeeSchedules(id, signal),
    ]).then(([assignments, schedules]) => ({
      assignments: assignments.items,
      schedules: schedules.items,
    })),
    `schedule|${id}`,
  );

  if (data.state === 'loading') return <p className="empty" role="status">Загружаем…</p>;
  if (data.state !== 'ready') {
    return <p className="empty empty--bad">Не удалось загрузить назначения.</p>;
  }

  const current = data.data.assignments.find((row) => !row.valid_to) ?? null;

  return (
    <>
      <div className="split split--open">
        <section className="panel">
          <h3 className="panel__title">Текущее назначение</h3>
          {current ? (
            <dl className="facts">
              <Fact label="Регион" value={current.region_name} />
              <Fact label="Офис" value={current.office_name} />
              <Fact label="Отдел" value={current.department_name} />
              <Fact label="Должность" value={current.position_name} />
              <Fact label="Действует с" value={current.valid_from} />
            </dl>
          ) : (
            <p className="muted">Действующего назначения нет.</p>
          )}
          <p className="field__hint">
            Перевод создаёт новый период и закрывает прежний — история
            назначений при этом сохраняется целиком и прошлые расчёты
            не меняются.
          </p>
        </section>

        <section className="panel">
          <h3 className="panel__title">Текущий график</h3>
          {data.data.schedules.length === 0 ? (
            <p className="empty">График не назначен.</p>
          ) : (
            <dl className="facts">
              {Object.entries(data.data.schedules[0] ?? {})
                .filter(([key]) => ['name', 'timezone', 'weekly_minutes',
                                    'late_grace_minutes', 'valid_from'].includes(key))
                .map(([key, value]) => (
                  <Fact key={key} label={key} value={value === null ? null : String(value)} />
                ))}
            </dl>
          )}
        </section>
      </div>

      <section className="panel">
        <h3 className="panel__title">История назначений</h3>
        <div className="scroller">
          <table className="grid-table" aria-label="История назначений">
            <thead>
              <tr>
                <th scope="col">Период</th>
                <th scope="col">Офис</th>
                <th scope="col">Отдел</th>
                <th scope="col">Должность</th>
                <th scope="col">Состояние</th>
              </tr>
            </thead>
            <tbody>
              {data.data.assignments.map((row) => (
                <tr key={row.id}>
                  <td>{row.valid_from} — {row.valid_to ?? 'по настоящее время'}</td>
                  <td>{orDash(row.office_name)}</td>
                  <td>{orDash(row.department_name)}</td>
                  <td>{orDash(row.position_name)}</td>
                  <td>{row.valid_to ? 'Завершено' : 'Действует'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.data.assignments.length === 0 && (
          <p className="empty">Записей о назначениях нет.</p>
        )}
      </section>
    </>
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
              <span className="roster__name">
                {item.kind === 'absence'
                  ? orDash((item.absence as Record<string, unknown> | undefined)?.['absence_type_name'])
                  : 'Исправление отметки'}
              </span>
              <span className="roster__note">
                {moment(item.created_at, zone, false)}
                {compact ? '' : ` · ${item.kind === 'absence'
                  ? (REQUEST_STATUS[status] ?? status)
                  : (CORRECTION_STATUS[status] ?? status)}`}
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
          <Icon name="cross" size={16} />
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
                <li key={String(doc['id'])} className="marks__row">
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
                    <span className="roster__name">{row.action}</span>
                    <span className="roster__note">
                      {moment(row.occurred_at, zone, false)}
                      {row.actor_email ? ` · ${row.actor_email}` : ''}
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
                  <Icon name="cross" size={16} />
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
        <li key={key} className="marks__row">
          <span className="marks__kind">{key}</span>
          <span className="marks__what">{orDash(before?.[key])}</span>
          <span className="marks__time">→ {orDash(after?.[key])}</span>
        </li>
      ))}
    </ul>
  );
}
