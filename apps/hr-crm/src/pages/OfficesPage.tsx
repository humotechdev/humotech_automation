/**
 * Офисы и регионы: структура компании и настройки отметок.
 *
 * Два состояния, которые нельзя смешивать, и об этом вся страница:
 * СТАТУС офиса (активен или отключён) и СОСТОЯНИЕ ЕГО НАСТРОЙКИ.
 * Действующий офис вполне может требовать настройки — например, его
 * QR-точка требует геолокацию, а координат и радиуса у офиса нет, и
 * проверка молча не выполняется. Одно про другое ничего не говорит.
 *
 * Присутствие берётся у сервера по каждому офису отдельным запросом с
 * точным `counts`, а не делением строк, которые поместились в общий
 * ответ. Процент явки здесь не считается вовсе: числитель и знаменатель
 * относятся к разным группам людей, и такое деление было бы выдумкой.
 */

import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { OfficeCard } from '../components/OfficeCard';
import { useBlock, type Block } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';

const STATUS_TITLE: Record<string, string> = {
  ACTIVE: 'Активен',
  INACTIVE: 'Отключён',
  CLOSED: 'Закрыт',
  ARCHIVED: 'В архиве',
};

export type OfficeStats = {
  office: api.OfficeFull;
  counts: Record<string, number>;
  points: api.QrPoint[];
};

/**
 * Требует настройки: у офиса есть точка с обязательной геолокацией, а
 * координат или радиуса нет — проверка присутствия не выполняется, и
 * человек об этом не узнает. Незаполненное НЕобязательное поле сюда не
 * относится: пустой адрес ничему не мешает.
 */
export function needsSetup(row: OfficeStats): boolean {
  const wants = row.points.some((point) => point.require_geolocation);
  const ready =
    row.office.latitude !== null &&
    row.office.longitude !== null &&
    (row.office.geofence_radius_m ?? 0) > 0;
  return wants && !ready;
}

export function OfficesPage() {
  const session = useSession();
  const can = (code: string) =>
    session.status === 'authenticated' && session.user.permissions.includes(code);

  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') === 'regions' ? 'regions' : 'offices';
  const search = params.get('search') ?? '';
  const region = params.get('region_id') ?? '';
  const status = params.get('status') ?? '';
  const onlySetup = params.get('setup') === '1';
  const picked = params.get('office') ?? '';

  const [draft, setDraft] = useState(search);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => setDraft(search), [search]);

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

  const key = `${search}|${region}|${status}|${attempt}`;

  const [list] = useBlock(
    (signal) =>
      api
        .officesPage(
          {
            limit: '100',
            ...(search ? { search } : {}),
            ...(region ? { region_id: region } : {}),
            ...(status ? { status } : {}),
          },
          signal,
        )
        .then(async (page) => {
          // Присутствие и QR-точки — по офису, с точным `counts`:
          // делить строки общего ответа между офисами нельзя, он
          // обрезается по потолку и итог получился бы заниженным.
          const rows = await Promise.all(
            page.items.map(async (office) => ({
              office,
              counts: await api
                .presenceDay({ office_id: office.id }, signal)
                .then((body) => body.counts)
                .catch(() => ({}) as Record<string, number>),
              points: await api
                .qrPoints({ office_id: office.id }, signal)
                .then((body) => body.items)
                .catch(() => [] as api.QrPoint[]),
            })),
          );
          return { rows, has_more: page.has_more };
        }),
    key,
    tab === 'offices',
  );

  const [regions] = useBlock((signal) => api.regions(signal), `regions|${attempt}`);

  // Сотрудники считаются сервером по текущим назначениям, а не по всей
  // истории: список офисов для этого не годится.
  const [staff] = useBlock(
    (signal) => api.employeeCounts(region ? { region_id: region } : {}, signal),
    `staff|${region}|${attempt}`,
  );

  const [inOffice] = useBlock(
    (signal) => api.dashboard(region ? { region_id: region } : {}, signal),
    `now|${region}|${attempt}`,
  );

  const rows = list.state === 'ready' ? list.data.rows : [];
  const setupCount = rows.filter(needsSetup).length;
  const shown = onlySetup ? rows.filter(needsSetup) : rows;
  const current = rows.find((row) => row.office.id === picked) ?? null;
  const dirty = Boolean(search || region || status || onlySetup);

  const totals = shown.reduce(
    (sum, row) => ({
      inOffice: sum.inOffice + (row.counts['IN_OFFICE'] ?? 0),
      expected:
        sum.expected +
        (row.counts['IN_OFFICE'] ?? 0) +
        (row.counts['LEFT'] ?? 0) +
        (row.counts['NOT_COME'] ?? 0),
      points: sum.points + row.points.length,
    }),
    { inOffice: 0, expected: 0, points: 0 },
  );

  return (
    <AppShell breadcrumb="Офисы и регионы" section="offices">
      <header className="head head--tight">
        <div>
          <h1 className="head__title">Офисы и регионы</h1>
          <p className="head__sub">Структура компании и настройки отметок</p>
        </div>
        {can('offices.manage') && (
          <div className="head__actions">
            <button type="button" className="btn btn--dark" disabled
                    title="Форма создания появится следующим этапом">
              <AppIcon name="building" size={16} />
              Добавить офис
            </button>
          </div>
        )}
      </header>

      <div className="tabs tabs--bare" role="tablist">
        {[
          { key: 'offices', title: 'Офисы', count: rows.length },
          { key: 'regions', title: 'Регионы', count: undefined },
        ].map((item) => (
          <button key={item.key} type="button" role="tab" aria-selected={item.key === tab}
                  className={item.key === tab ? 'tab tab--on' : 'tab'}
                  onClick={() => patch({ tab: item.key === 'offices' ? null : item.key, office: null })}>
            {item.title}
            {item.count !== undefined && list.state === 'ready' && (
              <span className="tab__count">{item.count}</span>
            )}
          </button>
        ))}
      </div>

      {tab === 'regions' ? (
        <RegionsTab canManage={can('regions.manage')} onChanged={() => setAttempt((n) => n + 1)}
                    onPick={(id) => patch({ tab: null, region_id: id })} />
      ) : (
        <div className="queue-grid">
          <section className="sheet">
            <ul className="strip">
              <li>
                <span className="strip__label">Офисы</span>
                <span className="strip__value">
                  {list.state === 'ready' ? rows.length : '—'}
                </span>
                <span className="strip__note">
                  {setupCount === 0 ? 'Настройка полная' : `Требуют настройки: ${setupCount}`}
                </span>
              </li>
              <li>
                <span className="strip__label">Сотрудники</span>
                <span className="strip__value">
                  {staff.state === 'ready' ? (staff.data['total'] ?? 0) : '—'}
                </span>
                <span className="strip__note">По текущим назначениям</span>
              </li>
              <li>
                <span className="strip__label">Сейчас в офисах</span>
                <span className="strip__value">
                  {inOffice.state === 'ready'
                    ? (inOffice.data.cards.find((c) => c.key === 'in_office')?.value ?? '—')
                    : inOffice.state === 'denied'
                      ? 'нет доступа'
                      : '—'}
                </span>
                <span className="strip__note">По данным отметок</span>
              </li>
            </ul>

            <div className="toolbar">
              <label className="find find--wide">
                <AppIcon name="search" size={16} />
                <input type="search" value={draft} placeholder="Поиск офиса"
                       aria-label="Поиск офиса"
                       onChange={(event) => setDraft(event.target.value)} />
              </label>
              <label className="pick">
                <span className="visually-hidden">Регион</span>
                <select value={region} onChange={(event) => patch({ region_id: event.target.value || null })}>
                  <option value="">Все регионы</option>
                  {regions.state === 'ready' &&
                    regions.data.items.map((item) => (
                      <option key={item.id} value={item.id}>{item.name}</option>
                    ))}
                </select>
              </label>
              <label className="pick">
                <span className="visually-hidden">Статус</span>
                <select value={status} onChange={(event) => patch({ status: event.target.value || null })}>
                  <option value="">Все статусы</option>
                  <option value="ACTIVE">Активные</option>
                  <option value="INACTIVE">Отключённые</option>
                </select>
              </label>
              {dirty && (
                <button type="button" className="btn"
                        onClick={() => patch({ search: null, region_id: null, status: null, setup: null })}>
                  Сбросить
                </button>
              )}
            </div>

            {setupCount > 0 && (
              <div className="toolbar toolbar--thin">
                <button
                  type="button"
                  className={onlySetup ? 'chip-btn chip-btn--on' : 'chip-btn'}
                  aria-pressed={onlySetup}
                  onClick={() => patch({ setup: onlySetup ? null : '1' })}
                >
                  <AppIcon name="settings" size={16} />
                  Требует настройки · {setupCount}
                  {onlySetup && <AppIcon name="close" size={16} />}
                </button>
              </div>
            )}

            <Rows block={list} name="офисы">
              {() =>
                shown.length === 0 ? (
                  <p className="empty">
                    {dirty ? 'По этим условиям офисов нет.' : 'Офисов пока нет.'}
                  </p>
                ) : (
                  <div className="scroller">
                    <table className="people">
                      <thead>
                        <tr>
                          <th>Офис / регион</th>
                          <th>Сотрудники</th>
                          <th>В офисе / по графику</th>
                          <th>QR-точки</th>
                          <th>Статус</th>
                        </tr>
                      </thead>
                      <tbody>
                        {shown.map((row) => (
                          <tr key={row.office.id} tabIndex={0}
                              className={row.office.id === picked ? 'is-picked' : undefined}
                              onClick={() => patch({ office: row.office.id })}
                              onKeyDown={(event) =>
                                event.key === 'Enter' && patch({ office: row.office.id })}>
                            <td>
                              <span className="who">
                                <span className="avatar avatar--square">
                                  <AppIcon name="building" size={16} />
                                </span>
                                <span className="who__text">
                                  <span className="who__name">{row.office.name}</span>
                                  <span className="who__id">{row.office.region_name ?? '—'}</span>
                                </span>
                              </span>
                            </td>
                            <td className="num">{staffOf(row)}</td>
                            <td className="num">
                              <b>{row.counts['IN_OFFICE'] ?? 0}</b>
                              <span className="muted"> / {expectedOf(row)}</span>
                            </td>
                            <td className="num">{row.points.length}</td>
                            <td>
                              <span className="state">
                                <i className="state__dot" />
                                {STATUS_TITLE[row.office.status] ?? row.office.status}
                              </span>
                              {needsSetup(row) && (
                                <span className="who__id">Нет геозоны</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                      <tfoot>
                        <tr>
                          <td>Итого</td>
                          <td className="num">
                            {staff.state === 'ready' ? (staff.data['total'] ?? '—') : '—'}
                          </td>
                          <td className="num">{totals.inOffice} / {totals.expected}</td>
                          <td className="num">{totals.points}</td>
                          <td />
                        </tr>
                      </tfoot>
                    </table>
                  </div>
                )
              }
            </Rows>

            <p className="sheet__hint">
              {list.state === 'ready'
                ? list.data.has_more
                  ? `Показаны ${shown.length} офисов — есть ещё, сузьте фильтры`
                  : `Показаны все ${shown.length} офисов`
                : ''}
            </p>
          </section>

          {current && (
            <OfficeCard
              row={current}
              canManage={can('offices.manage')}
              onClose={() => patch({ office: null })}
              onChanged={() => setAttempt((n) => n + 1)}
            />
          )}
        </div>
      )}
    </AppShell>
  );
}

// --- регионы ---------------------------------------------------------------

function RegionsTab({ canManage, onChanged, onPick }: {
  canManage: boolean; onChanged: () => void; onPick: (id: string) => void;
}) {
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState('');
  const [failed, setFailed] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const [list] = useBlock(
    (signal) =>
      api.regionsPage(
        { limit: '100', ...(search ? { search } : {}), ...(status ? { status } : {}) },
        signal,
      ),
    `regions|${search}|${status}|${attempt}`,
  );

  async function toggle(region: api.RegionFull) {
    if (busy) return;
    setBusy(region.id);
    setFailed(null);
    try {
      await api.setRegionActive(region.id, region.status !== 'ACTIVE');
      setAttempt((n) => n + 1);
      onChanged();
    } catch {
      setFailed('Не удалось изменить регион. Данные не тронуты.');
    } finally {
      setBusy('');
    }
  }

  return (
    <section className="sheet">
      <div className="toolbar">
        <label className="find find--wide">
          <AppIcon name="search" size={16} />
          <input type="search" value={search} placeholder="Поиск региона"
                 aria-label="Поиск региона"
                 onChange={(event) => setSearch(event.target.value)} />
        </label>
        <label className="pick">
          <span className="visually-hidden">Статус региона</span>
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">Все статусы</option>
            <option value="ACTIVE">Активные</option>
            <option value="INACTIVE">Отключённые</option>
          </select>
        </label>
      </div>

      {failed && <p className="empty empty--bad" role="alert">{failed}</p>}

      <Rows block={list} name="регионы">
        {(data) =>
          data.items.length === 0 ? (
            <p className="empty">Регионов не нашлось.</p>
          ) : (
            <div className="scroller">
              <table className="people">
                <thead>
                  <tr>
                    <th>Регион</th>
                    <th>Код</th>
                    <th>Статус</th>
                    <th aria-label="Действия" />
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((region) => (
                    <tr key={region.id}>
                      <td>
                        <button type="button" className="linky" onClick={() => onPick(region.id)}>
                          {region.name}
                        </button>
                      </td>
                      <td>{region.code}</td>
                      <td>
                        <span className="state">
                          <i className="state__dot" />
                          {STATUS_TITLE[region.status] ?? region.status}
                        </span>
                      </td>
                      <td className="people__go">
                        {canManage && (
                          <button type="button" className="btn" disabled={busy === region.id}
                                  onClick={() => void toggle(region)}>
                            {region.status === 'ACTIVE' ? 'Отключить' : 'Включить'}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Rows>
      <p className="sheet__hint">
        Отключение региона не закрывает его офисы и никого не переводит.
      </p>
    </section>
  );
}

// --- мелочи ----------------------------------------------------------------

/** В штате офиса: все состояния состава смены вместе. */
const staffOf = (row: OfficeStats) =>
  Object.values(row.counts).reduce((sum, value) => sum + value, 0);

/** По графику: пришли, ушли и не пришли. Выходной и отсутствие сюда не входят. */
const expectedOf = (row: OfficeStats) =>
  (row.counts['IN_OFFICE'] ?? 0) + (row.counts['LEFT'] ?? 0) + (row.counts['NOT_COME'] ?? 0);

function Rows<T>({ block, name, children }: {
  block: Block<T>; name: string; children: (data: T) => React.ReactNode;
}) {
  if (block.state === 'loading') return <p className="empty">Загружаем {name}…</p>;
  if (block.state === 'denied') return <p className="empty">Нет доступа к разделу «{name}».</p>;
  if (block.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось загрузить {name}. Это ошибка запроса, а не «их нет».
      </p>
    );
  }
  return <>{children(block.data)}</>;
}
