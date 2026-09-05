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

import { useMemo, useState } from 'react';

import * as api from '../api/crm';
import { AppShell } from '../components/AppShell';
import { AttendanceChart, toPoints, type Point } from '../components/AttendanceChart';
import { Icon, type IconName } from '../components/nav-icons';
import {
  formatPercent, formatTime, longDate, percent, shift, today, useBlock,
  type Block,
} from '../features/dashboard/data';

const RANGES = [
  { key: '7', title: '7 дней', days: 7 },
  { key: '14', title: '14 дней', days: 14 },
  { key: '30', title: 'Месяц', days: 30 },
] as const;

/** Шесть карточек образца: ключ сервера -> подпись и иконка. */
const CARDS: { key: string; icon: IconName; note: string; accent?: boolean }[] = [
  { key: 'active_employees', icon: 'users', note: 'В доступной области' },
  { key: 'should_work_today', icon: 'calendar', note: 'Ожидаются на работе' },
  { key: 'in_office', icon: 'building', note: '', accent: true },
  { key: 'not_come', icon: 'clock', note: 'Смена уже началась' },
  { key: 'vacation', icon: 'calendar', note: 'Подтверждено на дату' },
  { key: 'sick_leave', icon: 'doc', note: 'Подтверждено на дату' },
];

export function DashboardPage() {
  const [day, setDay] = useState(today);
  const [region, setRegion] = useState('');
  const [office, setOffice] = useState('');
  const [range, setRange] = useState<(typeof RANGES)[number]>(RANGES[1]);
  const [updated, setUpdated] = useState<Date | null>(null);
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
  const [chart] = useBlock(
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
          <h1 className="head__title">Обзор на сегодня</h1>
          <p className="head__sub">
            {longDate(day)} <span className="dot">·</span> По данным отметок
          </p>
        </div>

        <div className="head__filters">
          <div className="filters">
            <Select
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
            <Select
              label="Офис"
              value={office}
              empty={`Все офисы${visibleOffices.length ? ` · ${visibleOffices.length}` : ''}`}
              options={visibleOffices.map((o) => ({ id: o.id, name: o.name }))}
              onChange={setOffice}
            />
            <label className="pick pick--date">
              <Icon name="calendar" size={16} />
              <input
                type="date"
                value={day}
                aria-label="Дата"
                onChange={(event) => setDay(event.target.value || today())}
              />
            </label>
            <button
              type="button"
              className="pick pick--icon"
              aria-label="Обновить"
              onClick={() => setAttempt((n) => n + 1)}
            >
              <Icon name="refresh" size={16} />
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
              return (
                <li key={card.key} className={card.accent ? 'metric metric--accent' : 'metric'}>
                  <p className="metric__head">
                    <Icon name={card.icon} size={17} />
                    {found.title}
                  </p>
                  <p className="metric__value">{found.value}</p>
                  <p className="metric__note">
                    {card.key === 'in_office'
                      ? share === null
                        ? 'Сравнивать не с чем'
                        : `из ${counts['should_work_today']} · ${formatPercent(share)}`
                      : card.note}
                  </p>
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      <div className="grid">
        <div className="grid__main">
          <section className="panel">
            <div className="panel__head">
              <div>
                <h2 className="panel__title">Явка за {range.title.toLowerCase()}</h2>
                <p className="panel__sub">Отметились хотя бы один раз за день</p>
              </div>
              <div className="switch" role="group" aria-label="Период">
                {RANGES.map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    className={item.key === range.key ? 'switch__on' : ''}
                    onClick={() => setRange(item)}
                  >
                    {item.title}
                  </button>
                ))}
              </div>
            </div>
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
                  </div>
                </>
              )}
            </Section>
          </section>

          <section className="panel">
            <div className="panel__head">
              <div>
                <h2 className="panel__title">
                  Офисы <span className="chip">{visibleOffices.length}</span>
                </h2>
                <p className="panel__sub">{longDate(day)} · сводка по всем офисам</p>
              </div>
            </div>
            <Section block={table} name="офисы">
              {(rows) =>
                rows.length === 0 ? (
                  <p className="empty">В выбранной области нет доступных офисов.</p>
                ) : (
                  <div className="scroller">
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
                            <td className="grid-table__name">{item.name}</td>
                            <td>{staff(c)}</td>
                            <td>{expected(c)}</td>
                            <td>{c['IN_OFFICE'] ?? 0}</td>
                            <td>{c['NOT_COME'] ?? 0}</td>
                            <td>{c['VACATION'] ?? 0}</td>
                            <td>{c['SICK_LEAVE'] ?? 0}</td>
                          </tr>
                        ))}
                      </tbody>
                      <tfoot>
                        <tr>
                          <td>Итого</td>
                          <td>{counts['active_employees'] ?? '—'}</td>
                          <td>{counts['should_work_today'] ?? '—'}</td>
                          <td>{counts['in_office'] ?? '—'}</td>
                          <td>{counts['not_come'] ?? '—'}</td>
                          <td>{counts['vacation'] ?? '—'}</td>
                          <td>{counts['sick_leave'] ?? '—'}</td>
                        </tr>
                      </tfoot>
                    </table>
                  </div>
                )
              }
            </Section>
            {counts['left'] !== undefined && counts['left'] > 0 && (
              <p className="panel__foot">Отметились и вышли: {counts['left']}</p>
            )}
          </section>
        </div>

        <aside className="grid__side">
          <section className="panel">
            <h2 className="panel__title panel__title--row">
              <Icon name="inbox" size={18} /> Требует внимания
            </h2>
            <Section block={queues} name="очереди">
              {(data) => (
                <ul className="queue">
                  <Row icon="doc" title="Заявки на отпуск" note="Ожидают решения"
                       count={data.absences.requests.filter(isLeave).length} />
                  <Row icon="doc" title="Больничные" note="Ожидают решения"
                       count={data.absences.requests.filter((r) => !isLeave(r)).length} />
                  <Row icon="clock" title="Исправления отметок" note="Запросы сотрудников"
                       count={data.fixes.items.length} />
                  <Row icon="send" title="Привязки Telegram" note="Нужно подтверждение"
                       count={data.invites.items.filter(pendingInvite).length} />
                </ul>
              )}
            </Section>
          </section>

          <section className="panel">
            <h2 className="panel__title panel__title--row">
              <Icon name="chat" size={18} /> Обращения
              {talks.state === 'ready' && (
                <span className="chip chip--dark">{talks.data.items.length}</span>
              )}
            </h2>
            <Section block={talks} name="обращения">
              {(data) =>
                data.items.length === 0 ? (
                  <p className="empty">Обращений, ждущих ответа, нет.</p>
                ) : (
                  <ul className="talks">
                    {data.items.slice(0, 3).map((item) => (
                      <li key={item.id}>
                        <span className="avatar avatar--sm">
                          {(item.employee_name ?? '—').slice(0, 2).toUpperCase()}
                        </span>
                        <span>
                          <span className="talks__who">
                            {item.employee_name ?? 'Сотрудник'}
                            {item.office_name ? ` · ${item.office_name}` : ''}
                          </span>
                          <span className="talks__text">{item.question ?? item.text}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                )
              }
            </Section>
          </section>

          <section className="panel">
            <h2 className="panel__title panel__title--row">
              <Icon name="database" size={18} /> Проверить данные
            </h2>
            <Section block={cards} name="проверки">
              {() => (
                <ul className="queue queue--flat">
                  <Row title="Незакрытые посещения" count={counts['open_sessions'] ?? 0} />
                  <Row title="Не назначен график" count={counts['no_schedule'] ?? 0} />
                </ul>
              )}
            </Section>
            <p className="panel__foot">Требуют уточнения, не нарушения</p>
          </section>
        </aside>
      </div>
    </AppShell>
  );
}

// --- мелочи ---------------------------------------------------------------

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

function Row({ icon, title, note, count }: {
  icon?: IconName; title: string; note?: string; count: number;
}) {
  return (
    <li className="queue__row">
      {icon && <Icon name={icon} size={17} />}
      <span className="queue__text">
        <span className="queue__title">{title}</span>
        {note && <span className="queue__note">{note}</span>}
      </span>
      <span className="queue__count">{count}</span>
    </li>
  );
}

function Select({ label, value, empty, options, onChange }: {
  label: string;
  value: string;
  empty: string;
  options: { id: string; name: string }[];
  onChange: (value: string) => void;
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
