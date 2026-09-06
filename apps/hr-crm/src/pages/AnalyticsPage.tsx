/**
 * Аналитика: посещаемость и время в офисе.
 *
 * Каждый процент показан вместе с числами, из которых он получен, и
 * единицей измерения. Единица здесь — СОТРУДНИК-ДЕНЬ: один человек в
 * один день, когда его ждут по графику. Не уникальные люди и не
 * сканирования QR.
 *
 * Ни одна доля не считается в браузере заново: числитель, знаменатель и
 * словесная формула приходят из `/analytics`. Складывать проценты офисов
 * без весов здесь тоже нечем — суммируются числители и знаменатели.
 */

import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell } from '../components/AppShell';
import { Icon } from '../components/nav-icons';
import { AttendanceChart, toPoints } from '../components/AttendanceChart';
import { Compare } from '../components/Compare';
import {
  UNIT_TITLE, averagePerAttendedDay, findRatio, formatCount, formatPercent,
  formatPoints, formatSpan, points, share, weighted,
} from '../features/analytics/metrics';
import { formatTime, longDate, shift, today, useBlock, type Block } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';

/** Готовые периоды. Произвольный диапазон задаётся полями дат. */
const RANGES = [
  { key: '7', title: '7 дней', days: 7 },
  { key: '30', title: '30 дней', days: 30 },
  { key: '90', title: '90 дней', days: 90 },
] as const;

export function AnalyticsPage() {
  const session = useSession();
  const can = (code: string) =>
    session.status === 'authenticated' && session.user.permissions.includes(code);

  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') === 'compare' ? 'compare' : 'overview';
  const to = params.get('date_to') ?? today();
  const from = params.get('date_from') ?? shift(to, -29);
  const region = params.get('region_id') ?? '';
  const office = params.get('office_id') ?? '';
  const group = params.get('group') === 'regions' ? 'regions' : 'offices';
  const chosen = (params.get('pick') ?? '').split(',').filter(Boolean);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [attempt, setAttempt] = useState(0);

  const patch = useCallback(
    (changes: Record<string, string | null>) => {
      setParams(
        (was) => {
          const next = new URLSearchParams(was);
          for (const [key, value] of Object.entries(changes)) {
            if (value) next.set(key, value);
            else next.delete(key);
          }
          return next;
        },
        { replace: false },
      );
    },
    [setParams],
  );

  // Предыдущий сопоставимый период: столько же дней подряд перед текущим.
  const length = days(from, to);
  const prevTo = shift(from, -1);
  const prevFrom = shift(prevTo, -(length - 1));

  const scope = useMemo(
    () => ({
      date_from: from,
      date_to: to,
      ...(region ? { region_id: region } : {}),
      ...(office ? { office_id: office } : {}),
    }),
    [from, to, region, office],
  );
  const key = `${from}|${to}|${region}|${office}|${attempt}`;

  const [now] = useBlock(
    (signal) =>
      api.report(scope, signal).then((body) => {
        setUpdated(new Date());
        return body;
      }),
    key,
    tab === 'overview',
  );

  const [before] = useBlock(
    (signal) => api.report({ ...scope, date_from: prevFrom, date_to: prevTo }, signal),
    `prev|${key}`,
    tab === 'overview',
  );

  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.regions(signal), api.offices(signal)]).then(([r, o]) => ({
        regions: r.items,
        offices: o.items.filter((item) => item.status === 'ACTIVE'),
      })),
    'directory',
  );

  const objects = useMemo(() => {
    if (directory.state !== 'ready') return [];
    if (group === 'regions') return directory.data.regions.map((r) => ({ id: r.id, name: r.name }));
    const all = directory.data.offices;
    const inRegion = region ? all.filter((o) => o.region_id === region) : all;
    return inRegion.map((o) => ({ id: o.id, name: o.name }));
  }, [directory, group, region]);

  // Строка таблицы = отдельный отчёт по объекту. Числа точные, а не доли
  // общего ответа: делить агрегат между офисами нечем.
  const [table] = useBlock(
    (signal) =>
      Promise.all(
        objects.map((item) =>
          Promise.all([
            api.report(
              { date_from: from, date_to: to, [group === 'regions' ? 'region_id' : 'office_id']: item.id },
              signal,
            ),
            api.report(
              { date_from: prevFrom, date_to: prevTo, [group === 'regions' ? 'region_id' : 'office_id']: item.id },
              signal,
            ).catch(() => null),
          ]).then(([current, previous]) => ({ item, current, previous })),
        ),
      ),
    `table|${key}|${group}|${objects.map((o) => o.id).join(',')}`,
    tab === 'overview' && directory.state === 'ready',
  );

  const rows = table.state === 'ready' ? table.data : [];

  return (
    <AppShell breadcrumb="Аналитика" section="analytics">
      <header className="head head--tight">
        <div>
          <h1 className="head__title">Аналитика</h1>
          <p className="head__sub">Посещаемость и время в офисе</p>
        </div>
        <div className="head__filters">
          <div className="filters">
            <label className="pick pick--date">
              <Icon name="calendar" size={16} />
              <span className="visually-hidden">Начало периода</span>
              <input type="date" value={from} aria-label="Начало периода"
                     onChange={(event) => patch({ date_from: event.target.value })} />
            </label>
            <label className="pick pick--date">
              <span className="visually-hidden">Конец периода</span>
              <input type="date" value={to} aria-label="Конец периода"
                     onChange={(event) => patch({ date_to: event.target.value })} />
            </label>
            {RANGES.map((range) => (
              <button key={range.key} type="button" className="chip-btn"
                      onClick={() => patch({
                        date_to: today(),
                        date_from: shift(today(), -(range.days - 1)),
                      })}>
                {range.title}
              </button>
            ))}
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
          { key: 'overview', title: 'Обзор' },
          { key: 'compare', title: 'Сравнение' },
        ].map((item) => (
          <button key={item.key} type="button" role="tab" aria-selected={item.key === tab}
                  className={item.key === tab ? 'tab tab--on' : 'tab'}
                  onClick={() => patch({ tab: item.key === 'overview' ? null : item.key })}>
            {item.title}
          </button>
        ))}
      </div>

      <div className="toolbar toolbar--flat">
        <label className="pick">
          <span className="visually-hidden">Регион</span>
          <select value={region} onChange={(event) => {
            // Сбрасываем офис, только если он больше не в этом регионе.
            const next = event.target.value;
            const keep = directory.state === 'ready' && office
              ? directory.data.offices.some((o) => o.id === office && (!next || o.region_id === next))
              : false;
            patch({ region_id: next || null, ...(keep ? {} : { office_id: null }) });
          }}>
            <option value="">Все регионы</option>
            {directory.state === 'ready' &&
              directory.data.regions.map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
          </select>
        </label>
        <label className="pick">
          <span className="visually-hidden">Офис</span>
          <select value={office} onChange={(event) => patch({ office_id: event.target.value || null })}>
            <option value="">Все офисы</option>
            {directory.state === 'ready' &&
              directory.data.offices
                .filter((item) => !region || item.region_id === region)
                .map((item) => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
          </select>
        </label>
        <span className="muted">
          Сравнение с {longDate(prevFrom)} — {longDate(prevTo)}
        </span>
        {can('reports.export') && (
          <button type="button" className="btn" disabled
                  title="Выгрузки аналитики в существующих отчётах пока нет">
            <Icon name="report" size={16} />
            Экспорт
          </button>
        )}
      </div>

      {tab === 'compare' ? (
        <Compare
          from={from}
          to={to}
          prevFrom={prevFrom}
          prevTo={prevTo}
          group={group}
          objects={objects}
          chosen={chosen}
          onPick={(ids) => patch({ pick: ids.join(',') || null })}
        />
      ) : (
        <>
          <Section block={now} name="показатели">
            {(data) => {
              const attendance = findRatio(data.ratios, 'attendance');
              const attended = data.totals['attended_days'] ?? 0;
              const expected = data.totals['expected_working_days'] ?? 0;
              const late = data.totals['late_arrivals'] ?? 0;
              const missed = data.totals['missed_days'] ?? 0;
              const worked = data.totals['worked_seconds'] ?? 0;
              const past = before.state === 'ready' ? before.data : null;

              return (
                <ul className="cards cards--four">
                  <Card
                    icon="users"
                    title="Явка"
                    value={formatPercent(attendance?.percent ?? share(attended, expected))}
                    note={`${formatCount(attended)} из ${formatCount(expected)} сотрудник-дней`}
                    delta={past ? points(
                      share(attended, expected),
                      share(past.totals['attended_days'] ?? 0, past.totals['expected_working_days'] ?? 0),
                    ) : null}
                  />
                  <Card
                    icon="clock"
                    title="Опоздания"
                    value={formatPercent(share(late, attended))}
                    note={`${formatCount(late)} из ${formatCount(attended)} явок`}
                    delta={past ? points(
                      share(late, attended),
                      share(past.totals['late_arrivals'] ?? 0, past.totals['attended_days'] ?? 0),
                    ) : null}
                  />
                  <Card
                    icon="clock"
                    title="Среднее время"
                    value={formatSpan(averagePerAttendedDay(worked, attended))}
                    note="На сотрудник-день с явкой"
                    delta={null}
                  />
                  <Card
                    icon="alert"
                    title="Нет отметки"
                    value={formatPercent(share(missed, expected))}
                    note={`${formatCount(missed)} из ${formatCount(expected)} сотрудник-дней`}
                    delta={past ? points(
                      share(missed, expected),
                      share(past.totals['missed_days'] ?? 0, past.totals['expected_working_days'] ?? 0),
                    ) : null}
                  />
                </ul>
              );
            }}
          </Section>

          <div className="grid grid--analytics">
            <section className="panel">
              <div className="panel__head">
                <div>
                  <h2 className="panel__title">Динамика явки</h2>
                  <p className="panel__sub">Доля ожидаемых сотрудник-дней с отметкой</p>
                </div>
              </div>
              <Section block={now} name="график">
                {(data) => (
                  <>
                    <AttendanceChart
                      points={toPoints(data.series)}
                      previous={
                        before.state === 'ready' && before.data.series.length === data.series.length
                          ? toPoints(before.data.series)
                          : []
                      }
                      label="Динамика явки"
                    />
                    <div className="legend">
                      <span><i className="legend__solid" /> {longDate(from)} — {longDate(to)}</span>
                      {before.state === 'ready' &&
                        before.data.series.length === data.series.length && (
                        <span><i className="legend__dashed" /> {longDate(prevFrom)} — {longDate(prevTo)}</span>
                      )}
                    </div>
                  </>
                )}
              </Section>
            </section>

            <section className="panel">
              <h2 className="panel__title panel__title--row">
                <Icon name="alert" size={18} /> Как считается явка
              </h2>
              <Section block={now} name="расчёт">
                {(data) => {
                  const ratio = findRatio(data.ratios, 'attendance');
                  const attended = data.totals['attended_days'] ?? 0;
                  const expected = data.totals['expected_working_days'] ?? 0;
                  return (
                    <>
                      <p className="formula">
                        {formatCount(attended)} / {formatCount(expected)} × 100%
                      </p>
                      <p className="formula formula--result">
                        = {formatPercent(ratio?.percent ?? share(attended, expected))}
                      </p>
                      <p className="side-panel__text muted">
                        Один сотрудник в один день по графику — один
                        сотрудник-день. Единица показателя: {UNIT_TITLE[ratio?.unit ?? 'days']}.
                      </p>
                      {/* Формулу и оговорки пишет сервер: повторять их своими
                          словами значит однажды разойтись с расчётом. */}
                      <p className="side-panel__text muted">{ratio?.formula}</p>
                      <p className="side-panel__text muted">{data.coverage.note}</p>
                      <a className="linky"
                         href={`/attendance?date=${to}${office ? `&office_id=${office}` : ''}`}>
                        Открыть исходные записи →
                      </a>
                    </>
                  );
                }}
              </Section>
            </section>
          </div>

          <section className="panel">
            <div className="panel__head">
              <div>
                <h2 className="panel__title">
                  {group === 'regions' ? 'По регионам' : 'По офисам'}{' '}
                  <span className="chip">{objects.length}</span>
                </h2>
                <p className="panel__sub">
                  «По графику» и «С явкой» — сотрудник-дни, а не календарные дни
                  и не число людей
                </p>
              </div>
              <div className="switch" role="group" aria-label="Группировка">
                {[
                  { key: 'offices', title: 'Офисы' },
                  { key: 'regions', title: 'Регионы' },
                ].map((item) => (
                  <button key={item.key} type="button"
                          className={item.key === group ? 'switch__on' : ''}
                          onClick={() => patch({
                            group: item.key === 'offices' ? null : item.key,
                            // Выбор из другой группировки несовместим.
                            pick: null,
                          })}>
                    {item.title}
                  </button>
                ))}
              </div>
            </div>

            <Section block={table} name="разбивку">
              {(data) =>
                data.length === 0 ? (
                  <p className="empty">В доступной области нет объектов для разбивки.</p>
                ) : (
                  <>
                    <div className="scroller">
                      <table className="people">
                        <thead>
                          <tr>
                            <th aria-label="Выбор" />
                            <th>{group === 'regions' ? 'Регион' : 'Офис'}</th>
                            <th>По графику, сотр.-дни</th>
                            <th>С явкой, сотр.-дни</th>
                            <th>Явка</th>
                            <th>К прошлому периоду</th>
                            <th>Опоздания</th>
                            <th>Среднее время</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.map(({ item, current, previous }) => {
                            const expected = current.totals['expected_working_days'] ?? 0;
                            const attended = current.totals['attended_days'] ?? 0;
                            const late = current.totals['late_arrivals'] ?? 0;
                            const worked = current.totals['worked_seconds'] ?? 0;
                            const wasPercent = previous
                              ? share(previous.totals['attended_days'] ?? 0,
                                      previous.totals['expected_working_days'] ?? 0)
                              : null;
                            return (
                              <tr key={item.id}>
                                <td>
                                  <input
                                    type="checkbox"
                                    aria-label={`Выбрать ${item.name} для сравнения`}
                                    checked={chosen.includes(item.id)}
                                    onChange={(event) => {
                                      const next = event.target.checked
                                        ? [...chosen, item.id].slice(-2)
                                        : chosen.filter((id) => id !== item.id);
                                      patch({ pick: next.join(',') || null });
                                    }}
                                  />
                                </td>
                                <td className="grid-table__name">{item.name}</td>
                                <td className="num">{formatCount(expected)}</td>
                                <td className="num">{formatCount(attended)}</td>
                                <td className="num">{formatPercent(share(attended, expected))}</td>
                                <td className="num">
                                  {formatPoints(points(share(attended, expected), wasPercent))}
                                </td>
                                <td className="num">
                                  {formatCount(late)} · {formatPercent(share(late, attended))}
                                </td>
                                <td className="num">
                                  {formatSpan(averagePerAttendedDay(worked, attended))}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                        <tfoot>
                          <tr>
                            <td />
                            <td>Итого</td>
                            <td className="num">
                              {formatCount(sum(rows, 'expected_working_days'))}
                            </td>
                            <td className="num">{formatCount(sum(rows, 'attended_days'))}</td>
                            <td className="num">
                              {/* Сумма числителей на сумму знаменателей:
                                  среднее из процентов офисов дало бы другое
                                  число, и оно было бы неверным. */}
                              {formatPercent(
                                weighted(
                                  rows.map((row) => ({
                                    numerator: row.current.totals['attended_days'] ?? 0,
                                    denominator: row.current.totals['expected_working_days'] ?? 0,
                                  })),
                                ).percent,
                              )}
                            </td>
                            <td colSpan={3} />
                          </tr>
                        </tfoot>
                      </table>
                    </div>
                    <p className="panel__foot">
                      {chosen.length === 2
                        ? `Выбрано 2 — вкладка «Сравнение» покажет разницу`
                        : `Отметьте два ${group === 'regions' ? 'региона' : 'офиса'} для сравнения`}
                    </p>
                  </>
                )
              }
            </Section>
          </section>
        </>
      )}
    </AppShell>
  );
}

// --- мелочи ----------------------------------------------------------------

function days(first: string, last: string): number {
  const a = Date.parse(`${first}T00:00:00Z`);
  const b = Date.parse(`${last}T00:00:00Z`);
  return Math.max(1, Math.round((b - a) / 86_400_000) + 1);
}

const sum = (
  rows: { current: api.Report }[],
  field: string,
) => rows.reduce((total, row) => total + (row.current.totals[field] ?? 0), 0);

function Card({ icon, title, value, note, delta }: {
  icon: Parameters<typeof Icon>[0]['name'];
  title: string; value: string; note: string; delta: number | null;
}) {
  return (
    <li className="metric">
      <p className="metric__head">
        <Icon name={icon} size={17} />
        <span>{title}</span>
      </p>
      <p className="metric__value">{value}</p>
      <p className="metric__note">{note}</p>
      {delta !== null && (
        <p className="metric__note">
          {/* Пункты, а не проценты: 96,0% против 94,5% — это 1,5 пункта. */}
          {formatPoints(delta)} к прошлому периоду
        </p>
      )}
    </li>
  );
}

function Section<T>({ block, name, children }: {
  block: Block<T>; name: string; children: (data: T) => React.ReactNode;
}) {
  if (block.state === 'loading') return <p className="empty">Загружаем {name}…</p>;
  if (block.state === 'denied') return <p className="empty">Нет доступа к аналитике.</p>;
  if (block.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось загрузить {name}. Это ошибка запроса, а не нулевые показатели.
      </p>
    );
  }
  return <>{children(block.data)}</>;
}
