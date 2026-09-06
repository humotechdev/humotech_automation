/**
 * Посещаемость: сводка за день, состав смены и журнал отметок.
 *
 * Ни одна величина здесь не считается заново. Присутствие, время в
 * офисе, опоздания, отсутствия и календарные исключения считает сервер;
 * повторить эти правила в браузере значило бы завести второе место, где
 * та же цифра получается по другим правилам, — и однажды они разойдутся.
 *
 * Чего страница НЕ делает, намеренно:
 * — не называет «нет отметки» прогулом: причин может быть много;
 * — не считает опозданием отсутствие графика: сравнивать не с чем;
 * — не выдаёт разницу между первым входом и последним выходом за время
 *   в офисе — посещений за день может быть несколько;
 * — не называет промежуток между посещениями обедом.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell, initials } from '../components/AppShell';
import { Icon } from '../components/nav-icons';
import { DayCard } from '../components/DayCard';
import {
  formatTime, longDate, today, useBlock, type Block,
} from '../features/dashboard/data';
import { useSession } from '../features/auth/session';

/** Состояния состава смены. Взаимоисключающими они не являются. */
const STATE_TITLE: Record<string, string> = {
  IN_OFFICE: 'В офисе',
  LEFT: 'Ушёл',
  NOT_COME: 'Нет отметки',
  VACATION: 'Отпуск',
  SICK_LEAVE: 'Больничный',
  OTHER_ABSENCE: 'Отсутствие',
  DAY_OFF: 'Выходной по графику',
  NO_SCHEDULE: 'Без графика',
};

const PAGE = 10;

export function AttendancePage() {
  const session = useSession();
  const can = (code: string) =>
    session.status === 'authenticated' && session.user.permissions.includes(code);

  const [params, setParams] = useSearchParams();
  const day = params.get('date') ?? today();
  const tab = params.get('tab') === 'log' ? 'log' : 'day';
  const search = params.get('search') ?? '';
  const region = params.get('region_id') ?? '';
  const office = params.get('office_id') ?? '';
  const state = params.get('state') ?? '';
  const page = Number(params.get('page') ?? '1');
  const picked = params.get('employee') ?? '';

  const [draft, setDraft] = useState(search);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => setDraft(search), [search]);

  const patch = useCallback(
    (changes: Record<string, string | null>, keepPage = false) => {
      setParams(
        (was) => {
          const next = new URLSearchParams(was);
          for (const [key, value] of Object.entries(changes)) {
            if (value) next.set(key, value);
            else next.delete(key);
          }
          if (!keepPage) next.delete('page');
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  useEffect(() => {
    if (draft === search) return;
    const timer = setTimeout(() => patch({ search: draft || null }), 350);
    return () => clearTimeout(timer);
  }, [draft, search, patch]);

  const scope = useMemo(
    () => ({
      date: day,
      ...(region ? { region_id: region } : {}),
      ...(office ? { office_id: office } : {}),
    }),
    [day, region, office],
  );
  const key = `${day}|${region}|${office}|${state}|${search}|${attempt}`;

  // Пять показателей берутся у дашборда: он считает их по ВСЕМУ составу,
  // а не по строкам, которые поместились в ответ.
  const [cards] = useBlock(
    (signal) =>
      api.dashboard(scope, signal).then((body) => {
        setUpdated(new Date());
        return body;
      }),
    key,
  );

  const [shift] = useBlock(
    (signal) =>
      api.presenceDay(
        { ...scope, ...(state ? { state } : {}), ...(search ? { search } : {}) },
        signal,
      ),
    key,
    tab === 'day',
  );

  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.regions(signal), api.offices(signal)]).then(([r, o]) => ({
        regions: r.items,
        offices: o.items,
      })),
    'directory',
  );

  const offices = useMemo(() => {
    if (directory.state !== 'ready') return [];
    const all = directory.data.offices.filter((o) => o.status === 'ACTIVE');
    return region ? all.filter((o) => o.region_id === region) : all;
  }, [directory, region]);

  const rows = shift.state === 'ready' ? shift.data.items : [];
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const slice = rows.slice((page - 1) * PAGE, page * PAGE);
  const current = rows.find((row) => row.employee_id === picked) ?? null;
  const dirty = Boolean(search || region || office || state);
  const counts = cards.state === 'ready'
    ? Object.fromEntries(cards.data.cards.map((c) => [c.key, c.value]))
    : {};
  const past = day < today();

  return (
    <AppShell breadcrumb="Посещаемость" section="attendance">
      <header className="head head--tight">
        <div>
          <h1 className="head__title">Посещаемость</h1>
          <p className="head__sub">
            {longDate(day)} <span className="dot">·</span> По данным отметок
          </p>
        </div>
        <div className="head__filters">
          <div className="filters">
            <label className="pick pick--date">
              <Icon name="calendar" size={16} />
              <input type="date" value={day} aria-label="Дата"
                     onChange={(event) => patch({ date: event.target.value || today() })} />
            </label>
            {can('reports.export') && (
              <button type="button" className="btn" disabled
                      title="Выгрузка появится следующим этапом">
                <Icon name="report" size={16} />
                Экспорт
              </button>
            )}
            <button type="button" className="pick pick--icon" aria-label="Обновить"
                    onClick={() => setAttempt((n) => n + 1)}>
              <Icon name="refresh" size={16} />
            </button>
          </div>
          <p className="head__updated">
            {updated ? `Обновлено в ${formatTime(updated)}` : 'Загружаем…'}
          </p>
        </div>
      </header>

      <div className="tabs tabs--bare" role="tablist">
        {[
          { key: 'day', title: 'За день' },
          { key: 'log', title: 'Журнал отметок' },
        ].map((item) => (
          <button key={item.key} type="button" role="tab" aria-selected={item.key === tab}
                  className={item.key === tab ? 'tab tab--on' : 'tab'}
                  onClick={() => patch({ tab: item.key === 'day' ? null : item.key })}>
            {item.title}
          </button>
        ))}
      </div>

      {tab === 'day' ? (
        <>
          <Section block={cards} name="показатели">
            {(data) => (
              <ul className="cards cards--five">
                <Metric icon="calendar" title="По графику"
                        value={counts['should_work_today']} note="Ожидаются на работе" />
                <Metric
                  icon="building"
                  title={past ? 'Были в офисе' : 'Сейчас в офисах'}
                  value={counts['in_office']}
                  note="По открытым посещениям"
                  accent
                />
                <Metric icon="logout" title="Уже ушли" value={counts['left']}
                        note="Закрыли посещение" />
                <Metric icon="clock" title="Нет отметки" value={counts['not_come']}
                        note="Смена уже началась" />
                {/* Признак, а не отдельная категория: человек может
                    одновременно опоздать и находиться в офисе. */}
                <Metric icon="alert" title="Пришли позже" value={counts['late']}
                        note="Из отметившихся" />
                <span className="visually-hidden">{data.date}</span>
              </ul>
            )}
          </Section>

          <div className="queue-grid">
            <section className="sheet">
              <div className="toolbar">
                <label className="find find--wide">
                  <Icon name="search" size={16} />
                  <input type="search" value={draft} placeholder="Поиск сотрудника"
                         aria-label="Поиск сотрудника"
                         onChange={(event) => setDraft(event.target.value)} />
                </label>
                <Picker label="Регион" value={region} empty="Все регионы"
                        options={directory.state === 'ready' ? directory.data.regions : []}
                        onChange={(value) => patch({ region_id: value || null, office_id: null })} />
                <Picker label="Офис" value={office} empty="Все офисы" options={offices}
                        onChange={(value) => patch({ office_id: value || null })} />
                <label className="pick">
                  <span className="visually-hidden">Статус</span>
                  <select value={state} onChange={(event) => patch({ state: event.target.value || null })}>
                    <option value="">Все статусы</option>
                    {Object.entries(STATE_TITLE).map(([code, title]) => (
                      <option key={code} value={code}>{title}</option>
                    ))}
                  </select>
                </label>
                {dirty && (
                  <button type="button" className="btn" onClick={() =>
                    patch({ search: null, region_id: null, office_id: null, state: null })}>
                    Сбросить
                  </button>
                )}
              </div>

              <Section block={shift} name="состав смены">
                {(data) =>
                  data.items.length === 0 ? (
                    <p className="empty">
                      {dirty ? 'По этим условиям никого нет.' : 'На выбранный день отметок нет.'}
                    </p>
                  ) : (
                    <>
                      {data.truncated && (
                        <p className="empty empty--bad">
                          Показаны не все: состав больше, чем помещается в один ответ.
                          Сузьте фильтры — иначе список неполон.
                        </p>
                      )}
                      <div className="scroller">
                        <table className="people">
                          <thead>
                            <tr>
                              <th>Сотрудник</th>
                              <th>Офис</th>
                              <th>График</th>
                              <th>Первый вход</th>
                              <th>Последний выход</th>
                              <th>В офисе</th>
                              <th>Статус</th>
                            </tr>
                          </thead>
                          <tbody>
                            {slice.map((row) => (
                              <tr key={row.employee_id} tabIndex={0}
                                  className={row.employee_id === picked ? 'is-picked' : undefined}
                                  onClick={() => patch({ employee: row.employee_id }, true)}
                                  onKeyDown={(event) =>
                                    event.key === 'Enter' && patch({ employee: row.employee_id }, true)}>
                                <td>
                                  <span className="who">
                                    <span className="avatar">{initials(row.full_name)}</span>
                                    <span className="who__text">
                                      <span className="who__name">{row.full_name}</span>
                                      <span className="who__id">{row.employee_number ?? '—'}</span>
                                    </span>
                                  </span>
                                </td>
                                <td>{row.office_name ?? '—'}</td>
                                <td>{row.scheduled_start ? row.scheduled_start.slice(0, 5) : 'Не задан'}</td>
                                <td className="num">{clock(row.first_entry_at, data.timezone)}</td>
                                <td className="num">{clock(row.last_exit_at, data.timezone)}</td>
                                <td className="num">{row.seconds ? span(row.seconds) : '—'}</td>
                                <td>
                                  <span className="state">
                                    <i className="state__dot" />
                                    {STATE_TITLE[row.state] ?? row.state}
                                  </span>
                                  {row.late_minutes !== null && row.late_minutes > 0 && (
                                    <span className="who__id">Позже на {row.late_minutes} мин</span>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )
                }
              </Section>

              <div className="pager">
                <p className="pager__note">
                  {shift.state === 'ready'
                    ? `Показано ${slice.length} из ${rows.length}`
                    : ''}
                </p>
                <div className="pager__tools">
                  <button type="button" className="btn" disabled={page <= 1}
                          onClick={() => patch({ page: String(page - 1) }, true)}>
                    Назад
                  </button>
                  <button type="button" className="btn btn--dark" disabled={page >= pages}
                          onClick={() => patch({ page: String(page + 1) }, true)}>
                    Далее
                  </button>
                </div>
              </div>
              <p className="sheet__hint">Нажмите на сотрудника, чтобы увидеть отметки.</p>
            </section>

            {current && (
              <DayCard
                row={current}
                day={day}
                timezone={shift.state === 'ready' ? shift.data.timezone : ''}
                canAdd={can('attendance.manual')}
                onClose={() => patch({ employee: null }, true)}
                onChanged={() => setAttempt((n) => n + 1)}
              />
            )}
          </div>
        </>
      ) : (
        <Journal day={day} region={region} office={office} />
      )}
    </AppShell>
  );
}

// --- журнал ----------------------------------------------------------------

function Journal({ day, region, office }: { day: string; region: string; office: string }) {
  const [cursor, setCursor] = useState('');
  const [direction, setDirection] = useState('');
  const [onlyAccepted, setOnlyAccepted] = useState(true);

  const [log] = useBlock(
    (signal) =>
      api.events(
        {
          date_from: day,
          date_to: day,
          limit: '20',
          ...(region ? { region_id: region } : {}),
          ...(office ? { office_id: office } : {}),
          ...(direction ? { event_type: direction } : {}),
          // Успешные отметки и отклонённые попытки — разные вещи,
          // и складывать их в один список нельзя.
          ...(onlyAccepted ? { verification_status: 'ACCEPTED' } : {}),
        },
        signal,
      ),
    `log|${day}|${region}|${office}|${direction}|${onlyAccepted}|${cursor}`,
  );

  return (
    <section className="sheet">
      <div className="toolbar">
        <label className="pick">
          <span className="visually-hidden">Направление</span>
          <select value={direction} onChange={(event) => { setDirection(event.target.value); setCursor(''); }}>
            <option value="">Вход и выход</option>
            <option value="ENTRY">Только входы</option>
            <option value="EXIT">Только выходы</option>
          </select>
        </label>
        <label className="remember">
          <input type="checkbox" checked={onlyAccepted}
                 onChange={(event) => { setOnlyAccepted(event.target.checked); setCursor(''); }} />
          <span>Только успешные отметки</span>
        </label>
      </div>

      <Section block={log} name="журнал">
        {(data) =>
          data.items.length === 0 ? (
            <p className="empty">За выбранный день отметок нет.</p>
          ) : (
            <div className="scroller">
              <table className="people">
                <thead>
                  <tr>
                    <th>Время</th>
                    <th>Сотрудник</th>
                    <th>Офис и точка</th>
                    <th>Направление</th>
                    <th>Источник</th>
                    <th>Состояние</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((event) => (
                    <tr key={event.id}>
                      <td className="num">{event.occurred_at.slice(11, 16)}</td>
                      <td>{event.employee_id.slice(0, 8)}</td>
                      <td>
                        <span className="two">
                          <span className="two__first">{event.office_name ?? '—'}</span>
                          {event.qr_point_name && (
                            <span className="two__second">{event.qr_point_name}</span>
                          )}
                        </span>
                      </td>
                      <td>{event.event_type === 'ENTRY' ? 'Вход' : 'Выход'}</td>
                      <td>{event.source}</td>
                      <td>{event.verification_status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Section>

      <div className="pager">
        <p className="pager__note" />
        <div className="pager__tools">
          <button type="button" className="btn" disabled={!cursor} onClick={() => setCursor('')}>
            Назад
          </button>
          <button type="button" className="btn btn--dark"
                  disabled={log.state !== 'ready' || !log.data.has_more}
                  onClick={() => log.state === 'ready' && setCursor(log.data.next_cursor ?? '')}>
            Далее
          </button>
        </div>
      </div>
    </section>
  );
}

// --- мелочи ----------------------------------------------------------------

export function clock(at: string | null, _timezone: string): string {
  // Сервер уже отдал момент в нужной зоне; свою арифметику над часовыми
  // поясами здесь заводить нельзя — она разошлась бы с серверной.
  return at ? at.slice(11, 16) : '—';
}

export function span(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours ? `${hours} ч ${String(minutes).padStart(2, '0')} м` : `${minutes} м`;
}

function Metric({ icon, title, value, note, accent }: {
  icon: Parameters<typeof Icon>[0]['name'];
  title: string; value: number | undefined; note: string; accent?: boolean;
}) {
  return (
    <li className={accent ? 'metric metric--accent' : 'metric'}>
      <p className="metric__head">
        <Icon name={icon} size={17} />
        <span>{title}</span>
      </p>
      <p className="metric__value">{value ?? '—'}</p>
      <p className="metric__note">{note}</p>
    </li>
  );
}

function Picker({ label, value, empty, options, onChange }: {
  label: string; value: string; empty: string;
  options: { id: string; name: string }[]; onChange: (value: string) => void;
}) {
  return (
    <label className="pick">
      <span className="visually-hidden">{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">{empty}</option>
        {options.map((option) => (
          <option key={option.id} value={option.id}>{option.name}</option>
        ))}
      </select>
    </label>
  );
}

function Section<T>({ block, name, children }: {
  block: Block<T>; name: string; children: (data: T) => React.ReactNode;
}) {
  if (block.state === 'loading') return <p className="empty">Загружаем {name}…</p>;
  if (block.state === 'denied') return <p className="empty">Нет доступа к разделу «{name}».</p>;
  if (block.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось загрузить {name}. Это ошибка запроса, а не «данных нет».
      </p>
    );
  }
  return <>{children(block.data)}</>;
}
