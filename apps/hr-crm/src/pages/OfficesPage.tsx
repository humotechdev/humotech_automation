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
import { NewOfficeDialog } from '../components/NewOfficeDialog';
import { AppFilterButton } from '../components/AppSelect';
import { OfficeCard } from '../components/OfficeCard';
import {
  AREA_NAMES, UzbekistanMap, areaOf, loadAreas, type Area,
} from '../components/UzbekistanMap';
import { useBlock, type Block } from '../features/dashboard/data';
import '../styles/offices.css';


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

export function OfficesPage() {
  /*
   * Прав в интерфейсе нет: администратор один, и ему открыто всё.
   * Проверку исполняет сервер — он и ответит отказом, если когда-нибудь
   * появится учётная запись с урезанным доступом.
   */
  const can = (_code: string) => true;

  const [params, setParams] = useSearchParams();
  /*
   * Вид один — карта. Список повторял её же данные таблицей и заводил
   * второй способ сказать «где»: там отбирали по региону из списка,
   * здесь — по контуру области, и два отбора спорили между собой.
   *
   * Старые ссылки с `view=list` или `tab=regions` открывают карту:
   * ломать сохранённый адрес незачем.
   */
  const search = params.get('search') ?? '';
  const area = params.get('area') ?? '';
  const strip = params.get('strip') ?? 'all';
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

  const key = `${search}|${attempt}`;

  const [list] = useBlock(
    (signal) =>
      api
        .officesPage(
          {
            limit: '100',
            ...(search ? { search } : {}),
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
  );

  const [regions] = useBlock((signal) => api.regions(signal), `regions|${attempt}`);
  // Окно «Добавить офис». Регион заводится в нём же: без региона офис
  // создать не во что, а пустая система начинается именно с этого.
  const [adding, setAdding] = useState(false);
  const [addRegion, setAddRegion] = useState('');
  const [regionRows] = useBlock(
    (signal) => api.regionsPage({ status: 'ACTIVE', limit: '200' }, signal),
    `regions-full|${attempt}`,
  );
  const [staff] = useBlock(
    (signal) => api.employeeCounts({}, signal),
    `staff|${attempt}`,
  );
  const [today] = useBlock(
    (signal) => api.dashboard({}, signal),
    `now|${attempt}`,
  );
  const [areas] = useBlock(() => loadAreas(), 'areas');

  const rows = useMemo(() => (list.state === 'ready' ? list.data.rows : []), [list]);
  const areaList = areas.state === 'ready' ? areas.data : [];

  /**
   * Область каждого офиса — сперва по его региону, потом по координатам.
   *
   * Регион офис получает при создании: его выбирает человек, и это
   * утверждение о том, где офис числится. Координаты — производное, и
   * их может не быть вовсе: точку на карте ставят позже.
   *
   * Раньше считалось только по координатам, и офис без точки не попадал
   * никуда: выбрав на карте Бухарскую область, человек видел под ней
   * один офис вместо трёх — остальные просто выпадали из выборки.
   *
   * Координаты остаются запасным путём: у офиса может быть регион,
   * которого нет среди областей (компания делит сеть по-своему), и
   * тогда его место определяет точка на карте.
   */
  const areaById = useMemo(() => {
    const byName = new Map(
      Object.entries(AREA_NAMES).map(([iso, name]) => [name.toLowerCase(), iso]),
    );
    const map = new Map<string, string | null>();
    for (const row of rows) {
      const named = row.office.region_name
        ? byName.get(row.office.region_name.trim().toLowerCase()) ?? null
        : null;
      if (named) {
        map.set(row.office.id, named);
        continue;
      }
      const point = coordinates(row.office);
      map.set(row.office.id, point && areaList.length ? areaOf(areaList, point[1], point[0]) : null);
    }
    return map;
  }, [rows, areaList]);

  /**
   * Регион справочника, отвечающий выбранной области.
   *
   * Нужен, когда в области офисов нет: взять его из офиса не выйдет,
   * а завести офис здесь человек хочет именно в этот регион. Сходство
   * — по названию: областей четырнадцать, и называются они так же.
   */
  const areaRegionId = useMemo(() => {
    if (!area || regionRows.state !== 'ready') return '';
    const title = (AREA_NAMES[area] ?? '').trim().toLowerCase();
    return regionRows.data.items.find(
      (one) => one.name.trim().toLowerCase() === title,
    )?.id ?? '';
  }, [area, regionRows]);

  const inArea = area ? rows.filter((row) => areaById.get(row.office.id) === area) : rows;
  const stripRows = strip === 'active'
    ? inArea.filter((row) => row.office.status === 'ACTIVE')
    : strip === 'inactive' ? inArea.filter((row) => row.office.status !== 'ACTIVE') : inArea;
  const current = rows.find((row) => row.office.id === picked) ?? null;
  const withoutCoords = rows.filter((row) => coordinates(row.office) === null);

  const inOffice = today.state === 'ready'
    ? today.data.cards.find((card) => card.key === 'in_office')?.value ?? null
    : null;
  const pointsTotal = rows.some((row) => row.pointsKnown === false)
    ? null
    : rows.reduce((sum, row) => sum + row.points.length, 0);

  const pickArea = useCallback((iso: string | null) => patch({ area: iso, office: null }), [patch]);

  const pickOfficeFromStrip = useCallback((id: string) => {
    // Только выбор. Прокрутки к карте здесь больше нет: нажатие на
    // карточку меняло положение страницы под курсором, и следующий
    // щелчок попадал не туда, куда человек целился. Карта и так
    // подсвечивает выбранный офис — уезжать к ней незачем.
    patch({ office: id });
  }, [patch]);

  return (
    <AppShell breadcrumb="Офисы и регионы" section="offices">
      <div className="orp">
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
                <button type="button" className="of-btn of-btn--blue"
                        onClick={() => { setAddRegion(''); setAdding(true); }}>
                  <AppIcon name="plus" size={20} />
                  Добавить офис
                </button>
              )}
            </div>
          </div>
        </header>

        {adding && (
          <NewOfficeDialog
            regions={regionRows.state === 'ready' ? regionRows.data.items : []}
            region={addRegion || undefined}
            onClose={() => { setAdding(false); setAddRegion(''); }}
            onCreated={() => setAttempt((n) => n + 1)}
          />
        )}

        <div className="of-grid">
              <section className="of-map" aria-label="Карта сети">
                {areas.state === 'error' ? (
                  <p className="of-empty of-empty--bad">Не удалось загрузить границы. Список офисов ниже работает.</p>
                ) : areas.state !== 'ready' ? (
                  <p className="of-empty">Загружаем карту…</p>
                ) : (
                  <UzbekistanMap
                    areas={areas.data}
                    area={area}
                    onPickArea={pickArea}
                  />
                )}
                {area && (
                  /* Название — подпись, а не кнопка: нажатие по нему
                     снимало выбор случайно, когда человек просто хотел
                     прочитать, что выбрано. Снимает только крестик. */
                  <span className="of-area">
                    {AREA_NAMES[area] ?? area}
                    <button type="button" className="of-area__close"
                            aria-label="Показать все области"
                            onClick={() => patch({ area: null })}>
                      <AppIcon name="close" size={16} />
                    </button>
                  </span>
                )}
                <ul className="of-legend">
                  {/* Два состояния вместо трёх: «требует внимания» —
                      это про настройку конкретного офиса, а не про то,
                      что показывает карта, и в легенде областей ему
                      места нет. */}
                  <li><i className="of-box of-box--ok" />Активный офис</li>
                  <li><i className="of-box of-box--off" />Неактивный офис</li>
                  {withoutCoords.length > 0 && (
                    <li className="of-legend__note">Без координат: {withoutCoords.length}</li>
                  )}
                </ul>
              </section>

              {current ? (
                <OfficeCard row={current} canManage={can('offices.manage')} updated={updated}
                            onClose={() => patch({ office: null })} />
              ) : area ? (
                <RegionPanel name={AREA_NAMES[area] ?? area} rows={inArea}
                             canAdd={can('offices.manage')}
                             onClose={() => patch({ area: null, office: null })}
                             onPick={(id) => patch({ office: id })}
                             onAdd={() => { setAddRegion(areaRegionId); setAdding(true); }} />
              ) : (
                /* Область не выбрана — показываем, из чего состоит сеть:
                   регион и сколько в нём офисов. Сводка «сколько активно»
                   тут ни о чём не говорит, пока не видно, о каком месте
                   речь. */
                <RegionTable rows={rows} regions={regions}
                             onPick={(iso) => patch({ area: iso, office: null })} />
              )}
            </div>

            <OfficeStrip rows={stripRows} total={inArea.length} block={list} strip={strip}
                         picked={picked} hasAny={rows.length > 0} canAdd={can('offices.manage')}
                         onStrip={(value) => patch({ strip: value === 'all' ? null : value })}
                         onPick={pickOfficeFromStrip} onAdd={() => setAdding(true)}
                         onReset={() => {
                           setDraft('');
                           patch({ area: null, search: null, strip: null, office: null });
                         }} />
      </div>
    </AppShell>
  );
}

// --- карта ---------------------------------------------------------------------

/**
 * Выбран регион — и справа то, что про него спрашивают.
 *
 * Сводка всей сети («сколько офисов активно») здесь была не к месту:
 * человек ткнул в область и хочет знать про неё, а не про компанию.
 *
 * «Требует внимания» ведёт на посещаемость с уже поставленным отбором
 * по региону: увидев число людей без отметки, первым делом хотят
 * посмотреть, кто это.
 */
function RegionPanel({ name, rows, canAdd, onClose, onPick, onAdd }: {
  name: string;
  rows: OfficeStats[];
  canAdd: boolean;
  onClose: () => void;
  onPick: (id: string) => void;
  onAdd: () => void;
}) {
  const staff = rows.reduce((sum, row) => sum + staffOf(row), 0);
  const here = rows.reduce((sum, row) => sum + (row.counts['IN_OFFICE'] ?? 0), 0);
  const missing = rows.reduce((sum, row) => sum + (row.counts['NOT_COME'] ?? 0), 0);
  const unset = rows.filter(needsSetup);
  const regionId = rows.find((row) => row.office.region_id)?.office.region_id ?? '';

  // В области ничего не открыли. Плитки с нулями и пустые списки
  // здесь только занимают место: единственное, что тут можно сделать,
  // — завести первый офис.
  if (rows.length === 0) {
    return (
      <aside className="of-side of-region" aria-label="Выбранный регион">
        <header className="of-region__head">
          <div className="of-region__who-region">
            <h2>{name}</h2>
          </div>
          <button type="button" className="of-btn--icon" aria-label="Показать всю сеть"
                  onClick={onClose}>
            <AppIcon name="close" size={20} />
          </button>
        </header>

        <div className="of-region__nothing">
          <div className="of-blank">
            <AppIcon name="building" size={20} />
            <p className="of-blank__title">В этом регионе пока нет офисов</p>
            <p className="of-blank__text">
              Добавьте первый офис, чтобы он появился на карте и в списке.
            </p>
            {canAdd && (
              <>
                <button type="button" className="of-btn of-btn--blue" onClick={onAdd}>
                  <AppIcon name="plus" size={20} />
                  Добавить офис
                </button>
                <p className="of-blank__hint">Регион будет выбран автоматически</p>
              </>
            )}
          </div>
        </div>
      </aside>
    );
  }

  return (
    <aside className="of-side of-region" aria-label="Выбранный регион">
      <header className="of-region__head">
        <div className="of-region__who-region">
          <h2>{name}</h2>
          {/* Под областью — что именно в ней стоит. «Выбранный регион»
              повторяло то, что человек только что сделал сам, и места
              занимало столько же. */}
          <p>{rows.length === 0
            ? 'Офисов здесь нет'
            : rows.map((row) => row.office.name).join(' · ')}</p>
        </div>
        <button type="button" className="of-btn--icon" aria-label="Показать всю сеть"
                onClick={onClose}>
          <AppIcon name="close" size={20} />
        </button>
      </header>

      {/* Плитки без значков: рисунок отнимал половину ширины, и
          «Сотрудников» не помещалось — оставалось «Сотрудни…». */}
      <ul className="of-region__tiles">
        <li><span>Офисов</span><b>{rows.length}</b></li>
        <li><span>Сотрудников</span><b>{staff}</b></li>
        <li><span>Сейчас в офисе</span><b>{here}</b></li>
      </ul>

      <h3 className="of-region__title">Офисы региона</h3>
      {(
        <ul className="of-region__list">
          {rows.map((row) => {
            const active = row.office.status === 'ACTIVE';
            return (
              <li key={row.office.id}>
                <button type="button" onClick={() => onPick(row.office.id)}>
                  {/* Только название. Адрес второй строкой сдвигал всё
                      вправо по-разному у каждого офиса, и «Активен»
                      вставало вразнобой; смотрят же тут на состояние. */}
                  <b className="of-region__who">
                    {row.office.name || row.office.region_name || '—'}
                  </b>
                  {/* Состояние офиса — это «включён или выключен», а не
                      «работает ли он сейчас»: отметок может не быть и в
                      действующем офисе, просто день ещё не начался. */}
                  <span className="of-region__state">
                    <i className={`of-box of-box--${active ? 'ok' : 'off'}`} />
                    {active ? 'Активен' : 'Неактивен'}
                  </span>
                  <span className="of-region__here">
                    <b>{row.counts['IN_OFFICE'] ?? 0} из {staffOf(row)}</b>
                    <i>в офисе</i>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {/*
        «Требует внимания» стоит всегда, даже когда всё в порядке.
        Пропадающий раздел заставляет гадать: его нет, потому что всё
        хорошо, или потому что данные не дошли.

        Две вещи, которые видно из имеющегося: люди без отметки и офисы,
        где геозона не настроена — в таком отметиться у двери нельзя, и
        человек об этом не узнает.
      */}
      <h3 className="of-region__title">Требует внимания</h3>
      {missing === 0 && unset.length === 0 ? (
        <p className="of-region__none">Пусто</p>
      ) : (
        <div className="of-region__cares">
          {missing > 0 && (
            <Link className="of-region__alert"
                  to={`/attendance?state=NOT_COME${regionId ? `&region_id=${regionId}` : ''}`}>
              <AppIcon name="alert" size={20} />
              <span>{missing} {staffWord(missing)} без отметки</span>
              <b>{missing}</b>
            </Link>
          )}
          {unset.length > 0 && (
            <button type="button" className="of-region__alert"
                    onClick={() => onPick(unset[0]!.office.id)}>
              <AppIcon name="alert" size={20} />
              <span>{unset.length} {officesWord(unset.length)} без геозоны</span>
              <b>{unset.length}</b>
            </button>
          )}
        </div>
      )}
    </aside>
  );
}

// --- правая колонка --------------------------------------------------------------

/**
 * Регионы сети: сколько офисов и людей в каждом.
 *
 * Список строится по справочнику регионов, а не по офисам: регион без
 * единого офиса — это тоже факт сети, и именно с него начинают, когда
 * открывают новое место. Офисы, чей регион в справочнике не найден,
 * собираются в строку «Без региона»: потерять их совсем нельзя.
 */
function RegionTable({ rows, regions, onPick }: {
  rows: OfficeStats[];
  regions: Block<api.Items<api.Region>>;
  onPick: (iso: string) => void;
}) {
  const byName = useMemo(() => new Map(
    Object.entries(AREA_NAMES).map(([iso, name]) => [name.trim().toLowerCase(), iso]),
  ), []);

  const list = useMemo(() => {
    const known = regions.state === 'ready' ? regions.data.items : [];
    const counted = new Map<string, { offices: number; staff: number }>();
    let strayOffices = 0;
    let strayStaff = 0;
    for (const row of rows) {
      const id = row.office.region_id;
      const found = id ? counted.get(id) ?? { offices: 0, staff: 0 } : null;
      if (!id || !found || !known.some((one) => one.id === id)) {
        strayOffices += 1;
        strayStaff += staffOf(row);
        continue;
      }
      found.offices += 1;
      found.staff += staffOf(row);
      counted.set(id, found);
    }

    const made = known.map((one) => {
      const counts = counted.get(one.id) ?? { offices: 0, staff: 0 };
      return {
        id: one.id,
        name: one.name,
        iso: byName.get(one.name.trim().toLowerCase()) ?? null,
        ...counts,
      };
    }).sort((a, b) => b.offices - a.offices || a.name.localeCompare(b.name));

    if (strayOffices > 0) {
      made.push({
        id: 'stray', name: 'Без региона', iso: null,
        offices: strayOffices, staff: strayStaff,
      });
    }
    return made;
  }, [rows, regions, byName]);

  return (
    <aside className="of-regions" aria-label="Регионы сети">
      <h2 className="of-regions__title">Регионы</h2>
      <div className="of-regions__wrap">
        <table className="of-regions__table">
          <thead>
            <tr>
              <th scope="col">Регион</th>
              <th scope="col">Офисы</th>
              <th scope="col">Сотрудники</th>
            </tr>
          </thead>
          <tbody>
            {list.length === 0 ? (
              <tr><td colSpan={3}>Регионов пока нет.</td></tr>
            ) : list.map((one) => (
              <tr key={one.id}>
                <th scope="row">
                  {/* Без области на карте строка не нажимается:
                      «Без региона» и регионы, которых нет среди
                      областей, показываются, но никуда не ведут. */}
                  {one.iso ? (
                    <button type="button" onClick={() => onPick(one.iso as string)}>
                      <AppIcon name="pin" size={18} />
                      <span>{one.name}</span>
                    </button>
                  ) : (
                    <span className="of-regions__plain">
                      <AppIcon name="pin" size={18} />
                      <span>{one.name}</span>
                    </span>
                  )}
                </th>
                <td>{one.offices}</td>
                <td>{one.staff}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="of-regions__foot">
        Показано {list.length} из {list.length} регионов
      </p>
    </aside>
  );
}

function OfficeBlank({ kind, strip, total, canAdd, onAdd, onStrip, onReset }: {
  kind: 'none' | 'filter' | 'area';
  strip: string;
  total: number;
  canAdd: boolean;
  onAdd: () => void;
  onStrip: (value: string) => void;
  onReset: () => void;
}) {
  if (kind === 'none') {
    return (
      <div className="of-blank">
        <AppIcon name="building" size={20} />
        <p className="of-blank__title">Офисов пока нет</p>
        <p className="of-blank__text">
          Создайте первый офис, затем настройте карту, QR-точки и сотрудников.
        </p>
        {canAdd && (
          <>
            <button type="button" className="of-btn of-btn--blue" onClick={onAdd}>
              <AppIcon name="plus" size={20} />
              Добавить офис
            </button>
            <p className="of-blank__hint">Регион выбирается при создании офиса</p>
          </>
        )}
      </div>
    );
  }

  if (kind === 'filter') {
    const inactive = strip === 'inactive';
    return (
      <div className="of-blank">
        <AppIcon name="filter" size={20} />
        <p className="of-blank__title">
          {inactive ? 'Нет неактивных офисов' : 'Нет активных офисов'}
        </p>
        <p className="of-blank__text">
          {inactive
            ? `Все ${total} ${officesWord(total)} сейчас активны.`
            : `Все ${total} ${officesWord(total)} сейчас отключены.`}
        </p>
        <button type="button" className="of-blank__more" onClick={() => onStrip('all')}>
          Показать все офисы
        </button>
      </div>
    );
  }

  return (
    <div className="of-blank">
      <AppIcon name="pin" size={20} />
      <p className="of-blank__title">Здесь офисов нет</p>
      <p className="of-blank__text">
        В выбранной области пока ничего не открыли. Выберите другую или
        вернитесь ко всей сети.
      </p>
      <button type="button" className="of-blank__more" onClick={onReset}>
        Показать все офисы
      </button>
    </div>
  );
}

// --- лента офисов ---------------------------------------------------------------

function OfficeStrip({
  rows, total, block, strip, picked, hasAny, canAdd, onStrip, onPick, onAdd, onReset,
}: {
  rows: OfficeStats[];
  total: number;
  block: Block<unknown>;
  strip: string;
  picked: string;
  /** Есть ли офисы вообще — не в этом отборе, а во всей сети. */
  hasAny: boolean;
  canAdd: boolean;
  onStrip: (value: string) => void;
  onPick: (id: string) => void;
  onAdd: () => void;
  /** Снять область, поиск и «требует настройки» разом. */
  onReset: () => void;
}) {
  const track = useRef<HTMLDivElement>(null);

  // Смена отбора возвращает ленту к началу: иначе человек нажимает
  // «Неактивные» и смотрит в пустоту там, где раньше были карточки.
  useEffect(() => {
    track.current?.scrollTo({ left: 0, behavior: 'smooth' });
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

  return (
    <section className="of-strip" aria-label="Офисы">
      <div className="of-strip__head">
        <div className="of-strip__identity">
          <h2>Офисы <span className="of-count">{total}</span></h2>
        </div>
        <div className="of-strip__controls">
          {/* Три отбора и прокрутка — больше здесь ничего не нужно.
              Стрелки и «1–3 из 7» повторяли то, что человек и так
              делает пальцем или колесом, и занимали место рядом с
              фильтрами, по которым действительно нажимают. */}
          <div className="of-strip__filters" role="group" aria-label="Отбор офисов">
          {[
            { key: 'all', title: 'Все' },
            { key: 'active', title: 'Активные' },
            { key: 'inactive', title: 'Неактивные' },
          ].map((item) => (
            <AppFilterButton key={item.key} className="of-chip" active={strip === item.key} onClick={() => onStrip(item.key)}>{item.title}</AppFilterButton>
          ))}
          </div>
        </div>
      </div>
      <Rows block={block} name="офисы">
          {() => rows.length === 0 ? (
            <OfficeBlank kind={!hasAny ? 'none' : total > 0 ? 'filter' : 'area'}
                         strip={strip} total={total} canAdd={canAdd}
                         onAdd={onAdd} onStrip={onStrip} onReset={onReset} />
          ) : (
            <div className="of-strip__track" ref={track}>
            {rows.map((row) => {
              const staff = staffOf(row);
              const located = coordinates(row.office) !== null
                && (row.office.geofence_radius_m ?? 0) > 0;
              return (
                <button key={row.office.id} type="button" data-office-id={row.office.id}
                        className={`of-card${row.office.id === picked ? ' of-card--on' : ''}`}
                        onClick={() => onPick(row.office.id)}>
                  {/* Регион сверху, мельче: он отвечает на вопрос «где»,
                      а название — на вопрос «какой именно». */}
                  <span className="of-card__region">{row.office.region_name ?? '—'}</span>
                  <b className="of-card__name">{row.office.name}</b>
                  <span className="of-card__staff">
                    Сотрудники: <b>{staff}</b>
                  </span>
                  {/* Три числа дня. «Сотрудники» — это сколько людей за
                      офисом числится, а не сколько их там сегодня: без
                      этой тройки карточка не отвечает на первый же
                      вопрос, с которым на неё смотрят. */}
                  <span className="of-card__now">
                    <span className="of-card__now-one">
                      <span className="of-card__now-title">В офисе</span>
                      <b>{row.counts['IN_OFFICE'] ?? 0}</b>
                    </span>
                    <span className="of-card__now-one">
                      <span className="of-card__now-title">Отпуск</span>
                      <b>{row.counts['VACATION'] ?? 0}</b>
                    </span>
                    <span className="of-card__now-one">
                      <span className="of-card__now-title">Больничный</span>
                      <b>{row.counts['SICK_LEAVE'] ?? 0}</b>
                    </span>
                  </span>
                  <span className="of-card__foot">
                    <span>
                      QR: <b>{row.pointsKnown === false ? '—' : row.points.length}</b>
                    </span>
                    <span>
                      {/* Геопозиция — это «настроена или нет», а не
                          число: без точки и радиуса отметка по коду
                          у двери не примется вовсе. */}
                      Геопозиция: <b>{located ? 'есть' : 'нет'}</b>
                    </span>
                  </span>
                </button>
              );
            })}
            </div>
          )}
      </Rows>
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

function plural(n: number, forms: [string, string, string]): string {
  if (n % 10 === 1 && n % 100 !== 11) return forms[0];
  if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return forms[1];
  return forms[2];
}

const officesWord = (n: number) => plural(n, ['офис', 'офиса', 'офисов']);
const staffWord = (n: number) => plural(n, ['сотрудник', 'сотрудника', 'сотрудников']);
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
