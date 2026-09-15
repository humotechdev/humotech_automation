/**
 * Главная страница CRM: обзор на выбранный день.
 *
 * Все числа приходят с сервера. Ни одно не считается здесь заново:
 * `/dashboard` отдаёт карточки вместе с адресом списка, из которого
 * сложилось число, и посчитать иначе значило бы завести второе место,
 * где та же величина получается по другим правилам.
 *
 * Каждый блок грузится сам за себя: упавшая таблица офисов не повод
 * прятать карточки, которые уже пришли. Ошибка нигде не превращается
 * в ноль — у блока своё состояние, и показывается оно.
 */

import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell } from '../components/AppShell';
import { AttendanceChart, toPoints, type Point } from '../components/AttendanceChart';
import { DatePicker } from '../components/DatePicker';
import { Dropdown } from '../components/Dropdown';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import {
  formatPercent, formatTime, longDate, percent, shift, today, useBlock,
  type Block,
} from '../features/dashboard/data';

const RANGES = [
  { key: '7', title: '7 дней', days: 7 },
  { key: '14', title: '14 дней', days: 14 },
  { key: '30', title: 'Месяц', days: 30 },
] as const;

/**
 * Шесть карточек образца: ключ сервера -> короткая подпись и иконка.
 *
 * Подписи здесь свои, а не серверные, по одной причине: длинные
 * («Активные сотрудники», «Должны работать сегодня») переносились на
 * две строки, и числа в первых двух карточках оказывались ниже
 * остальных. Для ПРОШЛОЙ даты слова «сегодня» и «сейчас» снимаются —
 * иначе подпись обещает настоящее время там, где показан архив.
 */
const CARDS: {
  key: string; icon: AppIconName; title: string; past?: string;
  note: string; pastNote?: string; tone?: Tone;
}[] = [
  { key: 'active_employees', icon: 'users', title: 'Сотрудники',
    note: 'Активные сотрудники', tone: 'people' },
  { key: 'should_work_today', icon: 'calendar', title: 'По графику сегодня',
    past: 'По графику', note: 'Ожидаются на работе', pastNote: 'Ожидались на работе',
    tone: 'plan' },
  { key: 'in_office', icon: 'building', title: 'Сейчас в офисах', past: 'В офисах',
    note: '', tone: 'present' },
  { key: 'not_come', icon: 'clock', title: 'Нет отметки',
    note: 'Смена уже началась', pastNote: 'Смена шла без отметки', tone: 'pending' },
  { key: 'vacation', icon: 'calendar', title: 'В отпуске',
    note: 'Подтверждено на дату', tone: 'vacation' },
  { key: 'sick_leave', icon: 'doc', title: 'На больничном',
    note: 'Подтверждено на дату', tone: 'sick' },
];

/**
 * Состояние сотрудника цветом. Смысл каждого оттенка задан один раз в
 * `crm.css`, здесь только имена.
 *
 * Ни одно состояние не различается ОДНИМ цветом: рядом всегда стоит
 * слово — в карточке подпись, в таблице заголовок столбца. Больничный
 * намеренно не красный, а отсутствие отметки намеренно не «прогул»:
 * первое не нарушение, второе пока вопрос.
 */
type Tone = 'people' | 'plan' | 'present' | 'pending' | 'vacation' | 'sick';

/** Какие оттенки красят число и значок, а какие — только значок. */
const TINTED: ReadonlySet<Tone> = new Set(['present', 'pending', 'vacation', 'sick']);

/** Состояния смены, которые открываются списком из таблицы офисов. */
const COLUMN_TONE: Record<string, Tone | undefined> = {
  IN_OFFICE: 'present',
  NOT_COME: 'pending',
  VACATION: 'vacation',
  SICK_LEAVE: 'sick',
};

/**
 * Куда ведёт число.
 *
 * Адрес не собирается заново: `/dashboard` отдаёт у каждой карточки
 * `endpoint` и `params` — тот самый список, из которого сложилось
 * число. Здесь остаётся перевести адрес API в адрес раздела и отбросить
 * параметры, которых раздел не понимает: ссылка, открывающая пустой
 * список, хуже обычного текста.
 */
const SECTION: Record<string, { route: string; accepts: string[] }> = {
  '/api/v1/attendance/presence': {
    route: '/attendance',
    accepts: ['date', 'region_id', 'office_id', 'state'],
  },
  '/api/v1/employees': {
    route: '/employees',
    accepts: ['region_id', 'office_id', 'department_id'],
  },
};

function cardLink(card: api.Card | undefined): string | null {
  if (!card?.endpoint) return null;
  const section = SECTION[card.endpoint];
  if (!section) return null;
  const kept = Object.fromEntries(
    Object.entries(card.params).filter(([name]) => section.accepts.includes(name)),
  );
  return section.route + api.query(kept);
}

export function DashboardPage() {
  const [day, setDay] = useState(today);
  const [region, setRegion] = useState('');
  const [office, setOffice] = useState('');
  const [range, setRange] = useState<(typeof RANGES)[number]>(RANGES[1]);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [officeSearch, setOfficeSearch] = useState('');
  const [attempt, setAttempt] = useState(0);

  const filters: api.Filters = useMemo(
    () => ({
      date: day,
      ...(region ? { region_id: region } : {}),
      ...(office ? { office_id: office } : {}),
    }),
    [day, region, office],
  );
  const key = `${day}|${region}|${office}|${attempt}`;

  const [cards] = useBlock(
    (signal) =>
      api.dashboard(filters, signal).then((body) => {
        setUpdated(new Date());
        return body;
      }),
    key,
  );

  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.regions(signal), api.offices(signal)]).then(([r, o]) => ({
        regions: r.items,
        offices: o.items,
      })),
    'directory',
  );

  const from = shift(day, -(range.days - 1));
  const [chart, reloadChart, chartRefresh] = useBlock(
    (signal) =>
      Promise.all([
        api.analytics(from, day, filters, signal),
        api.analytics(shift(from, -range.days), shift(day, -range.days), filters, signal),
      ]).then(([now, before]) => ({
        points: toPoints(now.series),
        previous: toPoints(before.series),
      })),
    `${key}|${range.key}`,
  );

  const visibleOffices = useMemo(() => {
    if (directory.state !== 'ready') return [];
    const all = directory.data.offices.filter((o) => o.status === 'ACTIVE');
    const inRegion = region ? all.filter((o) => o.region_id === region) : all;
    return office ? inRegion.filter((o) => o.id === office) : inRegion;
  }, [directory, region, office]);

  const [table] = useBlock(
    (signal) =>
      Promise.all(
        visibleOffices.map((item) =>
          api
            .presence({ date: day, office_id: item.id }, signal)
            .then((body) => ({ office: item, counts: body.counts })),
        ),
      ),
    `${key}|offices:${visibleOffices.map((o) => o.id).join(',')}`,
    directory.state === 'ready',
  );

  const [queues] = useBlock(
    (signal) =>
      Promise.all([
        api.pendingAbsences(signal).catch(() => ({ requests: [] })),
        api.corrections(signal).catch(() => ({ items: [] })),
        api.invitations(signal).catch(() => ({ items: [] })),
      ]).then(([absences, fixes, invites]) => ({ absences, fixes, invites })),
    key,
  );

  const [talks] = useBlock((signal) => api.escalations(signal), key);

  /**
   * Последние решения по заявкам.
   *
   * Очередь отдаёт страницу по времени ПОДАЧИ — другого порядка у неё
   * нет. Поэтому берём последние поданные из решённых и раскладываем их
   * по времени РЕШЕНИЯ уже здесь. Ограничение честное и названо в
   * подписи: заявка, поданная давно и решённая сегодня, в эту страницу
   * может не попасть.
   */
  const [decided] = useBlock(
    (signal) =>
      api
        .queue({ kind: 'absence', status: 'APPROVED,REJECTED', limit: '50' }, signal)
        .then(({ items }) =>
          items
            .map((item) => item.absence)
            .filter((row): row is api.AbsenceRow => Boolean(row))
            .sort((a, b) => (b.reviewed_at ?? '').localeCompare(a.reviewed_at ?? ''))
            .slice(0, 2),
        ),
    key,
  );

  const counts = cards.state === 'ready' ? byKey(cards.data.cards) : {};
  const badges: Record<string, number> = {};
  if (queues.state === 'ready') {
    const requests =
      queues.data.absences.requests.length + queues.data.fixes.items.length;
    if (requests > 0) badges['requests'] = requests;
  }
  if (talks.state === 'ready' && talks.data.items.length > 0) {
    badges['questions'] = talks.data.items.length;
  }

  return (
    <AppShell breadcrumb="Главная" badges={badges}>
      <header className="head">
        <div>
          {/* Бейджа «Демо-данные» здесь нет намеренно. Он остался в
              «Администрировании» и «Настройках» — там он предупреждает
              перед действиями над организацией, и это его работа. На
              обзорной странице он ничего не защищает, а заголовок
              главной — первое, что видно в CRM. */}
          <h1 className="head__title">Обзор на сегодня</h1>
          <p className="head__sub">
            {longDate(day)} <span className="dot">·</span> По данным отметок
          </p>
        </div>

        <div className="head__filters">
          <div className="filters">
            <Dropdown
              label="Регион"
              value={region}
              empty="Все регионы"
              options={
                directory.state === 'ready'
                  ? directory.data.regions.map((r) => ({ id: r.id, name: r.name }))
                  : []
              }
              onChange={(value) => {
                setRegion(value);
                // Офис другого региона перестал быть допустимым выбором.
                setOffice('');
              }}
            />
            <Dropdown
              label="Офис"
              value={office}
              empty={`Все офисы${visibleOffices.length ? ` · ${visibleOffices.length}` : ''}`}
              options={visibleOffices.map((o) => ({ id: o.id, name: o.name }))}
              onChange={setOffice}
            />
            <DatePicker
              label="Дата"
              value={day}
              now={today()}
              onChange={setDay}
            />
            <button
              type="button"
              className="pick pick--icon"
              aria-label="Обновить"
              onClick={() => setAttempt((n) => n + 1)}
            >
              <AppIcon name="refresh" size={16} />
            </button>
          </div>
          <p className="head__updated">
            {updated ? `Обновлено в ${formatTime(updated)}` : 'Загружаем…'}
          </p>
        </div>
      </header>

      <Section block={cards} name="показатели">
        {(data) => (
          <ul className="cards">
            {CARDS.map((card) => {
              const found = data.cards.find((c) => c.key === card.key);
              if (!found) return null;
              const share =
                card.key === 'in_office'
                  ? percent(found.value, counts['should_work_today'] ?? 0)
                  : null;
              const past = day < today();
              const href = cardLink(found);
              const note =
                card.key === 'in_office'
                  ? share === null
                    ? 'Сравнивать не с чем'
                    : `из ${counts['should_work_today']} · ${formatPercent(share)}`
                  : (past && card.pastNote) || card.note;
              const inside = (
                <>
                  <p className="metric__head">
                    <span className="metric__mark" aria-hidden="true">
                      <AppIcon name={card.icon} size={18} />
                    </span>
                    <span>{(past && card.past) || card.title}</span>
                  </p>
                  <p className="metric__value">{found.value}</p>
                  <p className="metric__note">
                    <span>{note}</span>
                    {/* Стрелка стоит только там, где нажатие действительно
                        открывает список. Карточка без адреса остаётся
                        текстом и не притворяется кнопкой. */}
                    {href && <AppIcon name="arrow" size={16} />}
                  </p>
                </>
              );
              const shape = card.tone
                ? `metric metric--${card.tone}${TINTED.has(card.tone) ? ' metric--tone' : ''}`
                : 'metric';
              return (
                <li key={card.key}>
                  {href ? (
                    <Link to={href} className={`${shape} metric--go`}>{inside}</Link>
                  ) : (
                    <div className={shape}>{inside}</div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      <div className="grid">
        <div className="grid__main">
          <section className="panel panel--chart">
            <div className="panel__head">
              <div>
                <h2 className="panel__title">Явка за {range.title.toLowerCase()}</h2>
                <p className="panel__sub">Отметились хотя бы один раз за день</p>
              </div>
              <PeriodSwitch value={range.key} onChange={setRange} />
            </div>
            {/* Тонкая полоса под заголовком вместо затемнения панели:
                обновление видно, а читать прежние числа не мешает. */}
            <span
              className={chartRefresh.busy ? 'panel__progress panel__progress--on'
                                           : 'panel__progress'}
              aria-hidden="true"
            />
            {chartRefresh.failed && chart.state === 'ready' && (
              <p className="panel__retry" role="status">
                Не удалось обновить данные
                <button type="button" className="link link--go" onClick={reloadChart}>
                  Повторить
                </button>
              </p>
            )}
            <Section block={chart} name="график">
              {(data) => (
                <>
                  <AttendanceChart
                    points={data.points}
                    previous={comparable(data.points, data.previous)}
                    label={`Явка за ${range.title.toLowerCase()}`}
                  />
                  <div className="legend">
                    <span><i className="legend__solid" /> Явка</span>
                    {comparable(data.points, data.previous).length > 0 && (
                      <span><i className="legend__dashed" /> Предыдущий период</span>
                    )}
                    <span className="legend__tail">
                      {counts['left'] !== undefined && counts['left'] > 0 && (
                        <span className="legend__note">
                          Отметились и вышли: <b>{counts['left']}</b>
                        </span>
                      )}
                      <Link className="link link--go" to="/analytics">
                        Подробная аналитика <AppIcon name="arrow" size={16} />
                      </Link>
                    </span>
                  </div>
                </>
              )}
            </Section>
          </section>

          <section className="panel panel--dense">
            <div className="panel__head">
              <div>
                <h2 className="panel__title">
                  Офисы <span className="chip">{visibleOffices.length}</span>
                </h2>
                <p className="panel__sub">{longDate(day)} · сводка по всем офисам</p>
              </div>
              <div className="panel__tools">
                {/* Отбор идёт по уже загруженным строкам: запрашивать
                    сервер заново незачем, сводка целиком уже здесь. */}
                <label className="find">
                  <AppIcon name="search" size={16} />
                  <input
                    type="search"
                    value={officeSearch}
                    placeholder="Поиск офиса"
                    aria-label="Поиск офиса"
                    onChange={(event) => setOfficeSearch(event.target.value)}
                  />
                </label>
                <Link className="link link--go" to="/analytics">
                  Сравнить офисы <AppIcon name="arrow" size={16} />
                </Link>
              </div>
            </div>
            <Section block={table} name="офисы">
              {(all) => {
                const needle = officeSearch.trim().toLowerCase();
                const rows = needle
                  ? all.filter((r) => r.office.name.toLowerCase().includes(needle))
                  : all;
                return rows.length === 0 ? (
                  <p className="empty">
                    {needle
                      ? `Офис «${officeSearch.trim()}» не найден среди доступных.`
                      : 'В выбранной области нет доступных офисов.'}
                  </p>
                ) : (
                  <div className="scroller scroller--rows">
                    <table className="grid-table">
                      <thead>
                        <tr>
                          <th>Офис</th>
                          <th>В штате</th>
                          <th>По графику</th>
                          <th>В офисе</th>
                          <th>Нет отметки</th>
                          <th>Отпуск</th>
                          <th>Больничный</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map(({ office: item, counts: c }) => (
                          <tr key={item.id}>
                            <td className="grid-table__name">
                              <AppIcon name="building" size={16} />
                              {item.name}
                            </td>
                            <td>{staff(c)}</td>
                            <td>{expected(c)}</td>
                            {COLUMNS.map((state) => (
                              <td key={state}>
                                <Count
                                  value={c[state] ?? 0}
                                  state={state}
                                  day={day}
                                  office={item.id}
                                />
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                      <tfoot>
                        <tr>
                          <td>Итого</td>
                          <td>{counts['active_employees'] ?? '—'}</td>
                          <td>{counts['should_work_today'] ?? '—'}</td>
                          {COLUMNS.map((state) => (
                            <td key={state}>
                              <Count
                                value={counts[TOTAL_KEY[state] as string]}
                                state={state}
                                day={day}
                                office={office}
                                region={region}
                              />
                            </td>
                          ))}
                        </tr>
                      </tfoot>
                    </table>
                  </div>
                );
              }}
            </Section>
            <p className="panel__foot">Числа открывают списки сотрудников.</p>
          </section>
        </div>

        <aside className="grid__side">
          <section className="panel">
            <h2 className="panel__title panel__title--row">
              <span className="panel__mark panel__mark--pending" aria-hidden="true">
                <AppIcon name="alert" size={20} />
              </span>
              Требует внимания
              {queues.state === 'ready' && (
                <span className="chip chip--count">{waiting(queues.data)}</span>
              )}
            </h2>
            <Section block={queues} name="очереди">
              {(data) => (
                <div className="queue-box">
                  <ul className="queue">
                    <Row icon="doc" title="Справки на проверку" note="Новые документы"
                         count={data.absences.requests.filter(hasNewDocument).length}
                         to="/requests?tab=sick&status=open" />
                    <Row icon="sheet" title="Ожидаем справку" note="Больничные без документа"
                         count={data.absences.requests.filter(waitsDocument).length}
                         to="/requests?tab=sick&status=open" />
                    <Row icon="calendar" title="Заявки на отпуск" note="Ожидают решения"
                         count={data.absences.requests.filter(isLeave).length}
                         to="/requests?tab=leave&status=open" />
                    <Row icon="clock" title="Исправления отметок" note="Запросы сотрудников"
                         count={data.fixes.items.length}
                         to="/requests?tab=fixes&status=open" />
                    {/* Подтверждение привязки живёт на карточке человека:
                        отдельного списка ожидающих в системе нет, поэтому
                        строка ведёт в список сотрудников. */}
                    <Row icon="send" title="Привязки Telegram" note="Нужно подтверждение"
                         count={data.invites.items.filter(pendingInvite).length}
                         to="/employees?tab=active" />
                  </ul>
                  <Link className="btn btn--dark btn--wide" to="/requests?status=open">
                    Открыть заявки <AppIcon name="arrow" size={18} />
                  </Link>
                </div>
              )}
            </Section>
          </section>

          <section className="panel">
            <h2 className="panel__title panel__title--row">
              <span className="panel__mark" aria-hidden="true">
                <AppIcon name="chat" size={20} />
              </span>
              Обращения
              {talks.state === 'ready' && (
                <span className="chip chip--count">{talks.data.items.length}</span>
              )}
              <Link className="link link--go panel__aside" to="/questions">
                Все обращения <AppIcon name="arrow" size={16} />
              </Link>
            </h2>
            <Section block={talks} name="обращения">
              {(data) =>
                data.items.length === 0 ? (
                  <p className="empty">Обращений, ждущих ответа, нет.</p>
                ) : (
                  <ul className="feed">
                    {data.items.slice(0, 2).map((item) => (
                      <li key={item.id}>
                        {/* Нажимается вся строка, а не заголовок внутри
                            неё: попасть в строку списка мышью легко, в
                            строчку текста внутри — нет. Ссылка ведёт
                            сразу в это обращение, а не в общий список. */}
                        <Link
                          className="feed__row"
                          to={`/questions?id=${encodeURIComponent(item.id)}`}
                        >
                          <span className="avatar avatar--sm" aria-hidden="true">
                            {initialsOf(item.employee?.full_name)}
                          </span>
                          <span className="feed__text">
                            <span className="feed__title">{item.topic}</span>
                            <span className="feed__note">
                              {item.employee?.full_name ?? 'Сотрудник'}
                            </span>
                          </span>
                          {/* Время последнего сообщения: по нему видно,
                              сколько человек ждёт. */}
                          {item.last_message_at && (
                            <span className="feed__when">{ago(item.last_message_at)}</span>
                          )}
                          <AppIcon name="next" size={20} className="feed__go" />
                        </Link>
                      </li>
                    ))}
                  </ul>
                )
              }
            </Section>
          </section>

          <section className="panel">
            <h2 className="panel__title panel__title--row">
              <span className="panel__mark" aria-hidden="true">
                <AppIcon name="check" size={20} />
              </span>
              Последние решения
              <Link
                className="link link--go panel__aside"
                to="/requests?status=APPROVED,REJECTED"
              >
                Все решения <AppIcon name="arrow" size={16} />
              </Link>
            </h2>
            <Section block={decided} name="решения">
              {(rows) =>
                rows.length === 0 ? (
                  <p className="empty">Решений по заявкам пока нет.</p>
                ) : (
                  <ul className="feed">
                    {rows.map((row) => {
                      const ok = row.status === 'APPROVED';
                      return (
                        <li key={row.id}>
                          {/* Строка открывает ту самую заявку, по которой
                              принято решение. Список заявок показывает её
                              карточку по адресу, а не просто прокручивается
                              к нужному месту. */}
                          <Link
                            className="feed__row"
                            to={
                              // Список отсортирован по дате подачи, а
                              // решения — по дате решения: без сужения по
                              // сотруднику заявка месячной давности лежала
                              // бы на десятой странице и карточка не
                              // открылась бы.
                              `/requests?employee_id=${encodeURIComponent(row.employee.id)}`
                              + `&request=${encodeURIComponent(row.id)}`
                            }
                          >
                            <span
                              className={ok ? 'feed__mark feed__mark--ok'
                                            : 'feed__mark feed__mark--no'}
                              aria-hidden="true"
                            >
                              <AppIcon name={ok ? 'check' : 'cross'} size={20} />
                            </span>
                            <span className="feed__text">
                              <span className="feed__title">
                                {row.absence_type.name}
                                <span className="dot">·</span>
                                {shortName(row.employee.full_name)}
                              </span>
                              <span className="feed__note">
                                {row.reviewed_at ? ago(row.reviewed_at) : 'решение принято'}
                              </span>
                            </span>
                            <span className={ok ? 'verdict verdict--ok' : 'verdict verdict--no'}>
                              {ok ? 'Одобрено' : 'Отклонено'}
                            </span>
                            <AppIcon name="next" size={20} className="feed__go" />
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                )
              }
            </Section>
          </section>
        </aside>
      </div>
    </AppShell>
  );
}

// --- мелочи ---------------------------------------------------------------

/** Столбцы состояний в таблице офисов — те же коды, что у смены. */
const COLUMNS = ['IN_OFFICE', 'NOT_COME', 'VACATION', 'SICK_LEAVE'] as const;

/** Тот же столбец в строке «Итого» приходит с сервера под своим ключом. */
const TOTAL_KEY: Record<string, string> = {
  IN_OFFICE: 'in_office',
  NOT_COME: 'not_come',
  VACATION: 'vacation',
  SICK_LEAVE: 'sick_leave',
};

/**
 * Число в таблице офисов.
 *
 * Ноль ссылкой не делается: открывать пустой список незачем, и ноль не
 * должен выглядеть нажимаемым. Цвет повторяет состояние, но различает их
 * заголовок столбца — цвет здесь добавка, а не единственный признак.
 */
function Count({ value, state, day, office, region }: {
  value: number | undefined;
  state: string;
  day: string;
  office?: string;
  region?: string;
}) {
  if (value === undefined) return <span className="num num--none">—</span>;
  const tone = COLUMN_TONE[state];
  const shape = `num${tone && value > 0 ? ` num--${tone}` : ' num--none'}`;
  if (value === 0) return <span className={shape}>0</span>;
  const href =
    '/attendance' +
    api.query({
      date: day,
      state,
      ...(office ? { office_id: office } : {}),
      ...(region ? { region_id: region } : {}),
    });
  return <Link className={`${shape} num--go`} to={href}>{value}</Link>;
}

/**
 * Переключатель периода графика.
 *
 * Подложка выбранного пункта — один элемент, который переезжает между
 * кнопками, а не появляется и исчезает у каждой. Разница не только в
 * плавности: при смене класса у трёх кнопок браузер перерисовывает три
 * фона, и на слабой машине видно моргание.
 *
 * Переключатель отвечает на нажатие сразу и не ждёт ответа сервера:
 * состояние периода — местное, а загрузка данных идёт своим чередом.
 */
function PeriodSwitch({ value, onChange }: {
  value: string;
  onChange: (item: (typeof RANGES)[number]) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [glider, setGlider] = useState({ left: 0, width: 0 });

  useLayoutEffect(() => {
    const place = () => {
      const frame = box.current;
      const active = frame?.querySelector<HTMLElement>('[aria-pressed="true"]');
      if (!frame || !active) return;
      const outer = frame.getBoundingClientRect();
      const inner = active.getBoundingClientRect();
      setGlider({
        left: Math.round(inner.left - outer.left),
        width: Math.round(inner.width),
      });
    };
    place();
    // Ширина кнопок зависит от шрифта и ширины панели: после смены
    // размеров окна подложка обязана переехать вместе с ними.
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [value]);

  return (
    <div className="switch" role="group" aria-label="Период" ref={box}>
      <span
        className="switch__glider"
        aria-hidden="true"
        style={{ transform: `translateX(${glider.left}px)`, width: `${glider.width}px` }}
      />
      {RANGES.map((item) => (
        <button
          key={item.key}
          type="button"
          aria-pressed={item.key === value}
          className={item.key === value ? 'switch__on' : ''}
          onClick={() => onChange(item)}
        >
          {item.title}
        </button>
      ))}
    </div>
  );
}

/** Сколько всего ждёт решения — счётчик рядом с заголовком. */
function waiting(data: {
  absences: { requests: api.AbsenceRequestRow[] };
  fixes: { items: unknown[] };
  invites: { items: api.Invitation[] };
}): number {
  return (
    data.absences.requests.filter(hasNewDocument).length +
    data.absences.requests.filter(waitsDocument).length +
    data.absences.requests.filter(isLeave).length +
    data.fixes.items.length +
    data.invites.items.filter(pendingInvite).length
  );
}

/**
 * Больничный, к которому приложили справку, и её ещё не проверили.
 *
 * Признак берётся из самой заявки: `/absence-requests/pending` отдаёт
 * документы вместе со строкой, поэтому отдельного запроса не нужно.
 */
const hasNewDocument = (row: api.AbsenceRequestRow) =>
  !isLeave(row) &&
  (row.documents ?? []).some((d) => d.verification_status === 'PENDING');

/** Больничный, по которому справка нужна, но её ещё не прислали. */
const waitsDocument = (row: api.AbsenceRequestRow) =>
  !isLeave(row) && row.requires_document === true &&
  (row.documents ?? []).length === 0;

/**
 * «12 мин назад» по метке времени сервера.
 *
 * Единицы округляются вниз и не смешиваются: «2 ч назад» честнее, чем
 * «2 ч 40 мин назад», когда важно только «давно или нет». Будущая метка
 * не превращается в отрицательное число — такое бывает при расхождении
 * часов, и показывать «-3 мин назад» незачем.
 */
function ago(iso: string): string {
  const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (!Number.isFinite(minutes) || minutes < 1) return 'только что';
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ч назад`;
  return `${Math.floor(hours / 24)} дн назад`;
}

/** Две буквы из ФИО для кружка. Пустое имя даёт прочерк, а не «UN». */
function initialsOf(full: string | undefined): string {
  const parts = (full ?? '').split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '—';
  const [a, b] = parts;
  return ((a?.[0] ?? '') + (b?.[0] ?? '')).toUpperCase() || '—';
}

/** «Каримов Нодир Азизович» -> «Н. Каримов»: в узкой колонке ФИО целиком не помещается. */
function shortName(full: string): string {
  const [last, first] = full.split(/\s+/);
  if (!last) return full;
  return first ? `${first[0]}. ${last}` : last;
}

function byKey(cards: api.Card[]): Record<string, number> {
  return Object.fromEntries(cards.map((card) => [card.key, card.value]));
}

const isLeave = (row: api.AbsenceRequestRow) => row.absence_type.code !== 'SICK_LEAVE';
const pendingInvite = (row: api.Invitation) =>
  row.status === 'PENDING' || row.status === 'PENDING_CONFIRMATION';

/** В штате офиса: все состояния вместе — это и есть его состав на день. */
const staff = (c: Record<string, number>) =>
  Object.values(c).reduce((sum, value) => sum + value, 0);

/** По графику: пришли, ушли и не пришли. Выходной и отсутствие сюда не входят. */
const expected = (c: Record<string, number>) =>
  (c['IN_OFFICE'] ?? 0) + (c['LEFT'] ?? 0) + (c['NOT_COME'] ?? 0);

/** Предыдущий период показываем, только когда он сопоставим. */
function comparable(points: Point[], previous: Point[]): Point[] {
  if (previous.length !== points.length) return [];
  return previous.some((p) => p.value !== null) ? previous : [];
}

/**
 * Строка очереди. Ссылкой становится только та, у которой есть куда
 * вести И есть что показать: ноль открывал бы пустой список.
 */
function Row({ icon, title, note, count, to }: {
  icon?: AppIconName; title: string; note?: string; count: number; to?: string;
}) {
  const inside = (
    <>
      {icon && <AppIcon name={icon} size={18} />}
      <span className="queue__text">
        <span className="queue__title">{title}</span>
        {note && <span className="queue__note">{note}</span>}
      </span>
      <span className={count > 0 ? 'queue__count queue__count--on' : 'queue__count'}>
        {count}
      </span>
      {to && count > 0 && <AppIcon name="chevron" size={16} />}
    </>
  );
  return (
    <li className="queue__row">
      {to && count > 0 ? (
        <Link className="queue__go" to={to}>{inside}</Link>
      ) : (
        inside
      )}
    </li>
  );
}

/**
 * Обёртка блока. Загрузка, отказ и ошибка — три разных вида, и ни один
 * из них не выглядит как ноль.
 */
function Section<T>({ block, name, children }: {
  block: Block<T>;
  name: string;
  children: (data: T) => React.ReactNode;
}) {
  if (block.state === 'loading') return <p className="empty">Загружаем {name}…</p>;
  if (block.state === 'denied') {
    return <p className="empty">Нет доступа к разделу «{name}».</p>;
  }
  if (block.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось загрузить {name}. Данные не показаны — это не ноль.
      </p>
    );
  }
  return <>{children(block.data)}</>;
}
