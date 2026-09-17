/**
 * Страница «Офисы и регионы»: карта сети, список и регионы.
 *
 * Вёрстка повторяет эталон `Interactive Regional Office Network.png`, но
 * карта в нём нарисована неправильно, и её здесь нет: границы берутся из
 * настоящего GeoJSON (см. `components/UzbekistanMap.tsx`). Размеры — в
 * `styles/offices.css`, классы с префиксом `of-`.
 *
 * Два состояния, которые нельзя смешивать: СТАТУС офиса (активен или
 * отключён) и СОСТОЯНИЕ ЕГО НАСТРОЙКИ. Действующий офис вполне может
 * требовать настройки — его QR-точка требует геолокацию, а геозоны нет.
 *
 * Числа — только с сервера. Присутствие берётся по каждому офису отдельным
 * запросом с точным `counts`, а не делением строк общего ответа.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { AppFilterButton, AppSegmentedControl, AppSelectField } from '../components/AppSelect';
import { OfficeCard } from '../components/OfficeCard';
import {
  AREA_NAMES, UzbekistanMap, areaOf, loadAreas, type Area, type MapOffice,
} from '../components/UzbekistanMap';
import { formatTime, useBlock, type Block } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';
import '../styles/offices.css';

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
  /**
   * Опоздавшие сегодня — по строкам состава смены. `null`, если ответ
   * обрезан: часть людей не пришла, и считать по ней значит занизить.
   */
  late?: number | null;
  /**
   * Получены ли точки. `false` — сервер отказал (например, нет права на
   * QR-точки). Пустой список тогда означает «неизвестно», а не «ноль».
   */
  pointsKnown?: boolean;
};

/**
 * Требует настройки: у офиса есть точка с обязательной геолокацией, а
 * координат или радиуса нет — проверка присутствия не выполняется, и
 * человек об этом не узнает. Пустой адрес сюда не относится.
 */
export function needsSetup(row: OfficeStats): boolean {
  if (row.pointsKnown === false) return false;
  const wants = row.points.some((point) => point.require_geolocation);
  const ready =
    row.office.latitude !== null &&
    row.office.longitude !== null &&
    (row.office.geofence_radius_m ?? 0) > 0;
  return wants && !ready;
}

type View = 'map' | 'list' | 'regions';

export function OfficesPage() {
  const session = useSession();
  const can = (code: string) =>
    session.status === 'authenticated' && session.user.permissions.includes(code);

  const [params, setParams] = useSearchParams();
  const raw = params.get('view') ?? (params.get('tab') === 'regions' ? 'regions' : 'map');
  const view: View = raw === 'list' || raw === 'regions' ? raw : 'map';
  const search = params.get('search') ?? '';
  const region = params.get('region_id') ?? '';
  const status = params.get('status') ?? '';
  const area = params.get('area') ?? '';
  const strip = params.get('strip') ?? 'all';
  const onlySetup = params.get('setup') === '1';
  const picked = params.get('office') ?? '';

  const [draft, setDraft] = useState(search);
  const [attempt, setAttempt] = useState(0);
  const [updated, setUpdated] = useState<Date | null>(null);
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
          next.delete('tab');
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
          // Присутствие и QR-точки — по офису, с точным `counts`: делить
          // строки общего ответа нельзя, он обрезается по потолку.
          const rows = await Promise.all(
            page.items.map(async (office) => {
              const points = await api
                .qrPoints({ office_id: office.id }, signal)
                .then((body) => body.items)
                // Отказ — не «точек нет»: без права на QR-точки число
                // неизвестно, и нулём оно не показывается.
                .catch(() => null);
              const presence = await api
                .presenceDay({ office_id: office.id }, signal)
                .catch(() => null);
              return {
                office,
                counts: presence?.counts ?? ({} as Record<string, number>),
                late: presence && !presence.truncated
                  ? presence.items.filter((one) => (one.late_minutes ?? 0) > 0).length
                  : null,
                points: points ?? [],
                pointsKnown: points !== null,
              };
            }),
          );
          setUpdated(new Date());
          return { rows, has_more: page.has_more };
        }),
    key,
    view !== 'regions',
  );

  const [regions] = useBlock((signal) => api.regions(signal), `regions|${attempt}`);
  const [staff] = useBlock(
    (signal) => api.employeeCounts(region ? { region_id: region } : {}, signal),
    `staff|${region}|${attempt}`,
  );
  const [today] = useBlock(
    (signal) => api.dashboard(region ? { region_id: region } : {}, signal),
    `now|${region}|${attempt}`,
  );
  const [areas] = useBlock(() => loadAreas(), 'areas');

  const rows = useMemo(() => (list.state === 'ready' ? list.data.rows : []), [list]);
  const areaList = areas.state === 'ready' ? areas.data : [];

  // Административная единица каждого офиса — по его координатам, а не по
  // названию бизнес-региона. Без координат офис ни в какую область не входит.
  const areaById = useMemo(() => {
    const map = new Map<string, string | null>();
    for (const row of rows) {
      const point = coordinates(row.office);
      map.set(row.office.id, point && areaList.length ? areaOf(areaList, point[1], point[0]) : null);
    }
    return map;
  }, [rows, areaList]);

  const inArea = area ? rows.filter((row) => areaById.get(row.office.id) === area) : rows;
  const shown = onlySetup ? inArea.filter(needsSetup) : inArea;
  const stripRows = strip === 'active'
    ? shown.filter((row) => row.office.status === 'ACTIVE')
    : strip === 'attention' ? shown.filter((row) => attentionOf(row) !== null)
      : strip === 'inactive' ? shown.filter((row) => row.office.status !== 'ACTIVE') : shown;
  const current = rows.find((row) => row.office.id === picked) ?? null;
  const withoutCoords = rows.filter((row) => coordinates(row.office) === null);
  const setupCount = rows.filter(needsSetup).length;

  const inOffice = today.state === 'ready'
    ? today.data.cards.find((card) => card.key === 'in_office')?.value ?? null
    : null;
  const pointsTotal = rows.some((row) => row.pointsKnown === false)
    ? null
    : rows.reduce((sum, row) => sum + row.points.length, 0);

  // Кешируется по данным: новый массив на каждой отрисовке сбрасывал бы
  // кеш карты, и она пересчитывала бы подписи при любом изменении адреса.
  const mapOffices: MapOffice[] = useMemo(() => rows.flatMap((row) => {
    const point = coordinates(row.office);
    if (!point) return [];
    return [{
      id: row.office.id,
      name: row.office.name === 'Головной офис' ? (row.office.region_name ?? row.office.name) : row.office.name,
      latitude: point[0],
      longitude: point[1],
      tone: toneOf(row),
      head: row.office.name === 'Головной офис',
    }];
  }), [rows]);
  const pickArea = useCallback((iso: string | null) => patch({ area: iso, office: null }), [patch]);
  const pickOfficeFromMap = useCallback((id: string) => {
    // Keep the selected marker represented in the carousel even when the
    // current status filter would otherwise hide its card.
    if (stripRows.some((row) => row.office.id === id)) patch({ office: id });
    else patch({ office: id, strip: null });
  }, [patch, stripRows]);

  const pickOfficeFromStrip = useCallback((id: string) => {
    // Только выбор. Прокрутки к карте здесь больше нет: нажатие на
    // карточку меняло положение страницы под курсором, и следующий
    // щелчок попадал не туда, куда человек целился. Карта и так
    // подсвечивает выбранный офис — уезжать к ней незачем.
    patch({ office: id });
  }, [patch]);

  return (
    <AppShell breadcrumb="Офисы и регионы" section="offices">
      <div className="of">
        <header className="of-head">
          <div>
            <h1 className="of-head__title">Офисы и регионы</h1>
            <p className="of-head__sub">Сеть компании в реальном времени</p>
            <p className="of-head__facts">
              <span><b>{list.state === 'ready' ? rows.length : '—'}</b> {officesWord(rows.length)}</span>
              <i>·</i>
              <span><b>{staff.state === 'ready' ? staff.data['total'] ?? 0 : '—'}</b> {staffWord(staff.state === 'ready' ? staff.data['total'] ?? 0 : 0)}</span>
              <i>·</i>
              <span><b>{inOffice ?? '—'}</b> сейчас в офисах</span>
              <i>·</i>
              <span><b>{list.state === 'ready' && pointsTotal !== null ? pointsTotal : '—'}</b> QR-точек</span>
            </p>
          </div>
          <div className="of-head__side">
            <div className="of-head__tools">
              <label className="of-search">
                <AppIcon name="search" size={18} />
                <input type="search" value={draft} placeholder="Найти офис" aria-label="Найти офис"
                       onChange={(event) => setDraft(event.target.value)} />
              </label>
              <Link className="of-btn of-btn--light" to="/reports">
                <AppIcon name="download" size={20} />
                Экспорт
              </Link>
              {can('offices.manage') && (
                <button type="button" className="of-btn of-btn--blue" disabled
                        title="Форма создания офиса появится следующим этапом">
                  <AppIcon name="plus" size={20} />
                  Добавить офис
                </button>
              )}
            </div>
            <AppSegmentedControl className="of-views" role="tablist" label="Вид офисов" value={view}
              options={[
                { value: 'map', label: 'Карта сети', icon: 'pin' },
                { value: 'list', label: 'Список', icon: 'list' },
                { value: 'regions', label: 'Регионы', icon: 'chart' },
              ]}
              onChange={(next) => patch({ view: next === 'map' ? null : next, office: null })} />
          </div>
        </header>

        {view === 'regions' ? (
          <RegionsTab canManage={can('regions.manage')} onChanged={() => setAttempt((n) => n + 1)}
                      onPick={(id) => patch({ view: 'list', region_id: id })} />
        ) : view === 'list' ? (
          <div className="of-grid">
            <ListView rows={shown} block={list} picked={picked} setupCount={setupCount}
                      onlySetup={onlySetup} regions={regions} region={region} status={status}
                      onPatch={patch} />
            {current ? (
              <OfficeCard row={current} canManage={can('offices.manage')} updated={updated}
                          onClose={() => patch({ office: null })} />
            ) : (
              <Network rows={rows} updated={updated} onPick={(id) => patch({ office: id })} />
            )}
          </div>
        ) : (
          <>
            <div className="of-grid">
              <section className="of-map" aria-label="Карта сети">
                {areas.state === 'error' ? (
                  <p className="of-empty of-empty--bad">Не удалось загрузить границы. Список офисов ниже работает.</p>
                ) : areas.state !== 'ready' ? (
                  <p className="of-empty">Загружаем карту…</p>
                ) : (
                  <UzbekistanMap
                    areas={areas.data}
                    offices={mapOffices}
                    area={area}
                    office={picked}
                    onPickArea={pickArea}
                    onPickOffice={pickOfficeFromMap}
                    popup={current ? <Popup row={current} onClose={() => patch({ office: null })} /> : null}
                  />
                )}
                {area && (
                  <button type="button" className="of-area" onClick={() => patch({ area: null })}>
                    {AREA_NAMES[area] ?? area}
                    <AppIcon name="close" size={16} />
                  </button>
                )}
                <ul className="of-legend">
                  <li><i className="of-dot of-dot--ok" />Активный офис</li>
                  <li><i className="of-dot of-dot--warn" />Требует внимания</li>
                  <li><i className="of-dot of-dot--off" />Неактивный офис</li>
                  {withoutCoords.length > 0 && (
                    <li className="of-legend__note">Без координат: {withoutCoords.length}</li>
                  )}
                </ul>
              </section>

              {current ? (
                <OfficeCard row={current} canManage={can('offices.manage')} updated={updated}
                            onClose={() => patch({ office: null })} />
              ) : (
                <Network rows={rows} updated={updated} onPick={(id) => patch({ office: id })} />
              )}
            </div>

            <OfficeStrip rows={stripRows} total={shown.length} block={list} strip={strip}
                         picked={picked} onStrip={(value) => patch({ strip: value === 'all' ? null : value })}
                         onPick={pickOfficeFromStrip} />
          </>
        )}
      </div>
    </AppShell>
  );
}

// --- карта ---------------------------------------------------------------------

function Popup({ row, onClose }: { row: OfficeStats; onClose: () => void }) {
  const tone = toneOf(row);
  return (
    <div className="of-popup">
      <div className="of-popup__head">
        <b>{row.office.name}</b>
        <button type="button" aria-label="Закрыть" onClick={onClose}>
          <AppIcon name="close" size={16} />
        </button>
      </div>
      <p><AppIcon name="users" size={16} />{staffOf(row)} {staffWord(staffOf(row))}</p>
      <p><AppIcon name="clock" size={16} />{row.counts['IN_OFFICE'] ?? 0} сейчас в офисе</p>
      <span className={`of-state of-state--${tone}`}>
        {tone === 'ok' ? 'Работает' : tone === 'warn' ? attentionOf(row) : STATUS_TITLE[row.office.status] ?? row.office.status}
      </span>
    </div>
  );
}

// --- правая колонка --------------------------------------------------------------

function Network({ rows, updated, onPick }: {
  rows: OfficeStats[];
  updated: Date | null;
  onPick: (id: string) => void;
}) {
  const active = rows.filter((row) => row.office.status === 'ACTIVE' && !attentionOf(row)).length;
  const warn = rows.filter((row) => attentionOf(row) !== null).length;
  const off = rows.length - active - warn;
  const attention = rows.filter((row) => attentionOf(row) !== null).slice(0, 3);
  const load = rows
    .filter((row) => expectedOf(row) > 0)
    .sort((a, b) => expectedOf(b) - expectedOf(a))
    .slice(0, 3);
  const share = (n: number) => (rows.length ? `${(n / rows.length) * 100}%` : '0');

  return (
    <aside className="of-side" aria-label="Состояние сети">
      <div className="of-side__head">
        <h2>Состояние сети</h2>
        <span className="of-side__updated">
          <i className="of-dot of-dot--ok" />
          {updated ? `Обновлено в ${formatTime(updated)}` : 'Загружаем…'}
        </span>
      </div>
      <p className="of-side__sum"><b>{active}</b> из <b>{rows.length}</b> офисов активны</p>
      <div className="of-bar" role="img" aria-label={`Активны ${active}, требуют внимания ${warn}, неактивны ${off}`}>
        <i className="of-bar__ok" style={{ width: share(active) }} />
        <i className="of-bar__warn" style={{ width: share(warn) }} />
        <i className="of-bar__off" style={{ width: share(off) }} />
      </div>
      <ul className="of-split">
        <li><span><i className="of-dot of-dot--ok" />Активные</span><b>{active}</b></li>
        <li><span><i className="of-dot of-dot--warn" />Требуют внимания</span><b>{warn}</b></li>
        <li><span><i className="of-dot of-dot--off" />Неактивные</span><b>{off}</b></li>
      </ul>

      <div className="of-side__block">
        <h3>Требуют внимания <span className="of-count">{warn}</span></h3>
        {attention.length === 0 ? (
          <p className="of-empty">Все офисы в порядке.</p>
        ) : attention.map((row) => (
          <button key={row.office.id} type="button"
                  className={`of-alert of-alert--${row.office.status === 'ACTIVE' ? 'warn' : 'off'}`}
                  onClick={() => onPick(row.office.id)}>
            <AppIcon name={row.office.status === 'ACTIVE' ? 'alert' : 'lock'} size={20} />
            <span>
              <b>{row.office.name}</b>
              <small>{attentionOf(row)}</small>
            </span>
            <AppIcon name="next" size={18} />
          </button>
        ))}
      </div>

      <div className="of-side__block">
        <h3>Нагрузка сегодня</h3>
        {load.length === 0 ? (
          <p className="of-empty">Сегодня по графику никого нет.</p>
        ) : load.map((row) => {
          const here = row.counts['IN_OFFICE'] ?? 0;
          const expected = expectedOf(row);
          return (
            <div key={row.office.id} className="of-load">
              <span>{row.office.region_name ?? row.office.name}</span>
              <i className="of-load__bar"><i style={{ width: `${Math.min((here / expected) * 100, 100)}%` }} /></i>
              <b>{here} / {expected}</b>
              <small>{percent(here, expected)}</small>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

// --- лента офисов ---------------------------------------------------------------

function OfficeStrip({ rows, total, block, strip, picked, onStrip, onPick }: {
  rows: OfficeStats[];
  total: number;
  block: Block<unknown>;
  strip: string;
  picked: string;
  onStrip: (value: string) => void;
  onPick: (id: string) => void;
}) {
  const track = useRef<HTMLDivElement>(null);
  const [visibleCount, setVisibleCount] = useState(4);
  const [startIndex, setStartIndex] = useState(0);
  const maxStart = Math.max(0, rows.length - visibleCount);
  const rangeStart = rows.length ? Math.min(startIndex + 1, rows.length) : 0;
  const rangeEnd = rows.length ? Math.min(startIndex + visibleCount, rows.length) : 0;

  useEffect(() => {
    const element = track.current;
    if (!element) return;
    const measure = () => {
      const card = element.querySelector<HTMLElement>('.of-card');
      if (!card) return;
      const styles = getComputedStyle(element);
      const gap = Number.parseFloat(styles.columnGap || styles.gap) || 12;
      const count = Math.max(1, Math.round(element.clientWidth / (card.getBoundingClientRect().width + gap)));
      setVisibleCount(count);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    measure();
    return () => observer.disconnect();
  }, [rows.length]);

  useEffect(() => {
    const element = track.current;
    if (!element) return;
    element.scrollTo({ left: 0, behavior: 'smooth' });
    setStartIndex(0);
  }, [strip, rows.length]);

  useEffect(() => {
    const element = track.current;
    if (!element || !picked) return;
    const card = [...element.querySelectorAll<HTMLElement>('.of-card')]
      .find((item) => item.dataset.officeId === picked);
    if (!card) return;
    const trackBounds = element.getBoundingClientRect();
    const cardBounds = card.getBoundingClientRect();
    if (cardBounds.left < trackBounds.left) {
      element.scrollBy({ left: cardBounds.left - trackBounds.left, behavior: 'smooth' });
    } else if (cardBounds.right > trackBounds.right) {
      element.scrollBy({ left: cardBounds.right - trackBounds.right, behavior: 'smooth' });
    }
  }, [picked, rows]);

  const scroll = (direction: number) => {
    const element = track.current;
    if (!element) return;
    const previousStart = startIndex >= maxStart && maxStart % visibleCount !== 0
      ? Math.floor(maxStart / visibleCount) * visibleCount
      : Math.max(0, startIndex - visibleCount);
    const targetIndex = direction > 0
      ? Math.min(startIndex + visibleCount, maxStart)
      : previousStart;
    const card = element.querySelectorAll<HTMLElement>('.of-card')[targetIndex];
    if (!card) return;
    const shift = card.getBoundingClientRect().left - element.getBoundingClientRect().left;
    element.scrollBy({ left: shift, behavior: 'smooth' });
  };

  const onTrackScroll = () => {
    const element = track.current;
    if (!element) return;
    const trackLeft = element.getBoundingClientRect().left;
    const cards = [...element.querySelectorAll<HTMLElement>('.of-card')];
    const first = cards.findIndex((card) => card.getBoundingClientRect().left >= trackLeft - 1);
    if (first >= 0) setStartIndex(first);
  };

  return (
    <section className="of-strip" aria-label="Офисы">
      <div className="of-strip__head">
        <div className="of-strip__identity">
          <h2>Офисы <span className="of-count">{total}</span></h2>
          <p>Состояние офисов на выбранную дату</p>
        </div>
        <div className="of-strip__controls">
          <div className="of-strip__filters" role="group" aria-label="Отбор офисов">
          {[
            { key: 'all', title: 'Все' },
            { key: 'active', title: 'Активные' },
            { key: 'attention', title: 'Требуют внимания' },
            { key: 'inactive', title: 'Неактивные' },
          ].map((item) => (
            <AppFilterButton key={item.key} className="of-chip" active={strip === item.key} onClick={() => onStrip(item.key)}>{item.title}</AppFilterButton>
          ))}
          </div>
          <div className="of-strip__navigation">
            <button type="button" className="of-strip__arrow" aria-label="Предыдущие офисы"
                    disabled={startIndex <= 0} onClick={() => scroll(-1)}>
              <AppIcon name="back" size={18} />
            </button>
            <button type="button" className="of-strip__arrow" aria-label="Следующие офисы"
                    disabled={startIndex >= maxStart} onClick={() => scroll(1)}>
              <AppIcon name="next" size={18} />
            </button>
            <span className="of-strip__range">{rangeStart}–{rangeEnd} из {rows.length}</span>
          </div>
        </div>
      </div>
      <div className="of-strip__track" ref={track} onScroll={onTrackScroll}>
          <Rows block={block} name="офисы">
            {() => rows.length === 0 ? (
              <p className="of-empty">По этим условиям офисов нет.</p>
            ) : rows.map((row) => {
              const here = row.counts['IN_OFFICE'] ?? 0;
              const expected = expectedOf(row);
              const offSchedule = row.counts['NO_SCHEDULE'] ?? 0;
              const dayOff = row.counts['DAY_OFF'] ?? 0;
              const cardTone = row.office.status !== 'ACTIVE' || expected === 0
                ? 'off' : attentionOf(row) ? 'warn' : 'ok';
              const nonWorkingLabel = row.office.status !== 'ACTIVE'
                ? STATUS_TITLE[row.office.status] ?? 'Неактивен'
                : expected > 0 ? null
                  : dayOff > 0 && offSchedule === 0 ? 'Выходной'
                    : offSchedule > 0 && dayOff === 0 ? 'Нет графика'
                      : 'Нет данных на выбранную дату';
              const cardStatus = cardTone === 'warn'
                ? attentionOf(row) ?? 'Требует внимания'
                : cardTone === 'ok' ? 'Работает' : nonWorkingLabel ?? 'Нет данных';
              return (
                <button key={row.office.id} type="button" data-office-id={row.office.id}
                        className={`of-card of-card--${cardTone}${row.office.id === picked ? ' of-card--on' : ''}`}
                        onClick={() => onPick(row.office.id)}>
                  <span className="of-card__head">
                    <i className={`of-dot of-dot--${cardTone}`} />
                    <b>{row.office.name}</b>
                    <AppIcon name="next" size={16} />
                  </span>
                  <small className="of-card__region">{row.office.region_name ?? '—'}</small>
                  <span className={`of-card__status of-card__status--${cardTone}`}>{cardStatus}</span>
                  <span className="of-card__load">
                    {expected > 0 ? <>
                      <span>{here} из {expected} в офисе</span>
                      <b>{percent(here, expected)}</b>
                    </> : <span className="of-card__schedule">{nonWorkingLabel}</span>}
                  </span>
                  <i className={`of-card__bar of-card__bar--${cardTone}`}>
                    {expected > 0 && <i style={{ width: `${Math.min((here / expected) * 100, 100)}%` }} />}
                  </i>
                  <span className="of-card__points">
                    <AppIcon name="grid" size={16} />
                    <span>{row.pointsKnown === false
                      ? 'QR-точки: нет доступа'
                      : `${row.points.length} ${pointsWord(row.points.length)}`}</span>
                    <AppIcon name="next" size={16} />
                  </span>
                </button>
              );
            })}
          </Rows>
      </div>
    </section>
  );
}

// --- список ------------------------------------------------------------------------

function ListView({ rows, block, picked, setupCount, onlySetup, regions, region, status, onPatch }: {
  rows: OfficeStats[];
  block: Block<unknown>;
  picked: string;
  setupCount: number;
  onlySetup: boolean;
  regions: Block<api.Items<api.Region>>;
  region: string;
  status: string;
  onPatch: (changes: Record<string, string | null>) => void;
}) {
  return (
    <section className="of-list" aria-label="Список офисов">
      <div className="of-list__tools">
        <AppSelectField className="of-select" label="Регион" value={region} onChange={(value) => onPatch({ region_id: value || null })}>
            <option value="">Все регионы</option>
            {regions.state === 'ready' && regions.data.items.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
        </AppSelectField>
        <AppSelectField className="of-select" label="Статус" value={status} onChange={(value) => onPatch({ status: value || null })}>
            <option value="">Все статусы</option>
            <option value="ACTIVE">Активные</option>
            <option value="INACTIVE">Отключённые</option>
        </AppSelectField>
        {setupCount > 0 && (
          <button type="button" aria-pressed={onlySetup}
                  className={onlySetup ? 'of-chip of-chip--on' : 'of-chip'}
                  onClick={() => onPatch({ setup: onlySetup ? null : '1' })}>
            <AppIcon name="settings" size={16} />
            Требует настройки · {setupCount}
          </button>
        )}
      </div>
      <Rows block={block} name="офисы">
        {() => rows.length === 0 ? (
          <p className="of-empty">По этим условиям офисов нет.</p>
        ) : (
          <div className="of-table-wrap">
            <table className="of-table table-cards">
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
                {rows.map((row) => (
                  <tr key={row.office.id} tabIndex={0}
                      className={row.office.id === picked ? 'of-table__on' : undefined}
                      onClick={() => onPatch({ office: row.office.id })}
                      onKeyDown={(event) => { if (event.key === 'Enter') onPatch({ office: row.office.id }); }}>
                    <td><b>{row.office.name}</b><small>{row.office.region_name ?? '—'}</small></td>
                    <td data-label="Сотрудники">{staffOf(row)}</td>
                    <td data-label="В офисе / по графику"><b>{row.counts['IN_OFFICE'] ?? 0}</b> / {expectedOf(row)}</td>
                    <td data-label="QR-точки">{row.pointsKnown === false ? '—' : row.points.length}</td>
                    <td>
                      <span className={`of-state of-state--${row.office.status === 'ACTIVE' ? 'ok' : 'off'}`}>
                        {STATUS_TITLE[row.office.status] ?? row.office.status}
                      </span>
                      {needsSetup(row) && <small>Нет геозоны</small>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Rows>
    </section>
  );
}

// --- регионы ------------------------------------------------------------------------

function RegionsTab({ canManage, onChanged, onPick }: {
  canManage: boolean;
  onChanged: () => void;
  onPick: (id: string) => void;
}) {
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState('');
  const [failed, setFailed] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const [list] = useBlock(
    (signal) => api.regionsPage(
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
    <section className="of-list of-list--regions" aria-label="Регионы">
      <p className="of-note">
        Регионы — деление компании, а не административные области: у одного
        региона может быть несколько областей, у области — несколько регионов.
      </p>
      <div className="of-list__tools">
        <label className="of-search">
          <AppIcon name="search" size={18} />
          <input type="search" value={search} placeholder="Поиск региона" aria-label="Поиск региона"
                 onChange={(event) => setSearch(event.target.value)} />
        </label>
        <AppSelectField className="of-select" label="Статус региона" value={status} onChange={setStatus}>
            <option value="">Все статусы</option>
            <option value="ACTIVE">Активные</option>
            <option value="INACTIVE">Отключённые</option>
        </AppSelectField>
      </div>
      {failed && <p className="of-empty of-empty--bad" role="alert">{failed}</p>}
      <Rows block={list} name="регионы">
        {(data) => data.items.length === 0 ? (
          <p className="of-empty">Регионов не нашлось.</p>
        ) : (
          <div className="of-table-wrap">
            <table className="of-table">
              <thead>
                <tr><th>Регион</th><th>Код</th><th>Статус</th><th aria-label="Действия" /></tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <button type="button" className="of-linky" onClick={() => onPick(item.id)}>{item.name}</button>
                    </td>
                    <td>{item.code}</td>
                    <td>
                      <span className={`of-state of-state--${item.status === 'ACTIVE' ? 'ok' : 'off'}`}>
                        {STATUS_TITLE[item.status] ?? item.status}
                      </span>
                    </td>
                    <td>
                      {canManage && (
                        <button type="button" className="of-btn of-btn--light" disabled={busy === item.id}
                                onClick={() => void toggle(item)}>
                          {item.status === 'ACTIVE' ? 'Отключить' : 'Включить'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Rows>
      <p className="of-note">Отключение региона не закрывает его офисы и никого не переводит.</p>
    </section>
  );
}

// --- мелочи ------------------------------------------------------------------------

/** Координаты офиса числами. `null` — у офиса их нет, и на карту он не ставится. */
function coordinates(office: api.OfficeFull): [number, number] | null {
  if (office.latitude === null || office.longitude === null) return null;
  const lat = Number(office.latitude);
  const lon = Number(office.longitude);
  return Number.isFinite(lat) && Number.isFinite(lon) ? [lat, lon] : null;
}

/**
 * Что в офисе требует внимания. Только то, что видно из данных: отключён,
 * геозона не настроена при обязательной геолокации, сегодня ждали людей,
 * а в офисе никого.
 */
function attentionOf(row: OfficeStats): string | null {
  if (row.office.status !== 'ACTIVE') return null;
  if (needsSetup(row)) return 'Геозона не настроена';
  if (expectedOf(row) > 0 && (row.counts['IN_OFFICE'] ?? 0) === 0 && (row.counts['LEFT'] ?? 0) === 0) {
    return 'Сегодня нет отметок';
  }
  return null;
}

export function toneOf(row: OfficeStats): 'ok' | 'warn' | 'off' {
  if (row.office.status !== 'ACTIVE') return 'off';
  return attentionOf(row) ? 'warn' : 'ok';
}

/** В штате офиса: все состояния состава смены вместе. */
const staffOf = (row: OfficeStats) =>
  Object.values(row.counts).reduce((sum, value) => sum + value, 0);

/** По графику: пришли, ушли и не пришли. Выходной и отсутствие сюда не входят. */
const expectedOf = (row: OfficeStats) =>
  (row.counts['IN_OFFICE'] ?? 0) + (row.counts['LEFT'] ?? 0) + (row.counts['NOT_COME'] ?? 0);

function percent(part: number, whole: number): string {
  if (!whole) return '—';
  return `${((part / whole) * 100).toFixed(1).replace('.', ',')}%`;
}

function plural(n: number, forms: [string, string, string]): string {
  if (n % 10 === 1 && n % 100 !== 11) return forms[0];
  if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return forms[1];
  return forms[2];
}

const officesWord = (n: number) => plural(n, ['офис', 'офиса', 'офисов']);
const staffWord = (n: number) => plural(n, ['сотрудник', 'сотрудника', 'сотрудников']);
const pointsWord = (n: number) => plural(n, ['QR-точка', 'QR-точки', 'QR-точек']);

function Rows<T>({ block, name, children }: {
  block: Block<T>;
  name: string;
  children: (data: T) => React.ReactNode;
}) {
  if (block.state === 'loading') return <p className="of-empty">Загружаем {name}…</p>;
  if (block.state === 'denied') return <p className="of-empty">Нет доступа к разделу «{name}».</p>;
  if (block.state === 'error') {
    return <p className="of-empty of-empty--bad">Не удалось загрузить {name}. Это ошибка запроса, а не «их нет».</p>;
  }
  return <>{children(block.data)}</>;
}

export type { Area };
