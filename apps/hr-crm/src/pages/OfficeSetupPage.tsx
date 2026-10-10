/**
 * «Настроить офис»: основное, расположение на карте, QR-точки, сотрудники.
 *
 * Страница для HR-администратора без технических слов. Координаты он не
 * набирает числами — ставит точку на карте или находит адрес, радиус
 * двигает ползунком и сразу видит круг. QR-точка — это название и тип:
 * код, секрет и ссылку собирает сервер.
 *
 * Вкладка живёт в адресе (`?tab=geo`): со страницы «Офисы» кнопка
 * «Настроить» ведёт сразу к карте, а обновление страницы не сбрасывает
 * человека на первую вкладку.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { AppShell } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { OfficeForm } from '../components/OfficeCard';
import { OfficeStaff } from '../components/OfficeStaff';
import {
  OfficeMap, addressOf, searchAddress, type Found, type Place, type Shape,
} from '../components/OfficeMap';
import {
  GOOGLE_KEY, GoogleOfficeMap, googleAddress, googleSearch,
} from '../components/GoogleOfficeMap';
import { QrPointsManager } from '../components/QrPointsManager';
import { useBlock } from '../features/dashboard/data';
import '../styles/offices.css';
import '../styles/office-setup.css';

const TABS = [
  { key: 'main', title: 'Основное' },
  { key: 'geo', title: 'Геолокация' },
  { key: 'qr', title: 'QR-точки' },
  { key: 'staff', title: 'Сотрудники' },
] as const;

type TabKey = (typeof TABS)[number]['key'];

/*
 * Карта — Google, когда для неё дан ключ.
 *
 * Подложка OpenStreetMap в Узбекистане подписана редко: дома без
 * номеров, организации почти не отмечены, и HR не узнаёт место, где
 * должен поставить точку. Без ключа остаётся OSM: показать карту,
 * пусть и бедную, лучше, чем пустой прямоугольник.
 */
const MapView = GOOGLE_KEY ? GoogleOfficeMap : OfficeMap;
const lookupAddress = GOOGLE_KEY
  ? (place: Place) => googleAddress(place)
  : (place: Place, signal?: AbortSignal) => addressOf(place, signal);
const lookupPlaces = GOOGLE_KEY
  ? (text: string, _signal?: AbortSignal) => googleSearch(text)
  : (text: string, signal?: AbortSignal) => searchAddress(text, signal);

/*
 * Вкладки во всю ширину, без сводки слева.
 *
 * Карте сводка отнимает треть ширины, а точку ставят именно по карте.
 * Таблицам QR-точек и состава офиса она обрезает столбцы. И на всех трёх
 * она повторяет то, что человек прямо сейчас правит.
 */
const WIDE = new Set<TabKey>(['geo', 'qr', 'staff']);

const RADIUS_MIN = 50;
const RADIUS_MAX = 500;
const RADIUS_DEFAULT = 100;

/**
 * Страница настройки пересоздаётся на каждый офис.
 *
 * Маршрут `/offices/:id/setup` при переходе к другому офису страницу не
 * размонтирует. Без ключа до ответа по новому офису под его адресом
 * оставались название, адрес, точка, QR-точки и сотрудники прежнего, а
 * поздний ответ на сохранение прежнего подменял новый. С ключом всё
 * состояние и все запросы относятся только к текущему офису.
 */
export function OfficeSetupPage() {
  const { id = '' } = useParams();
  return <OfficeSetup key={id} id={id} />;
}

function OfficeSetup({ id }: { id: string }) {
  /*
   * Прав в интерфейсе нет: администратор один, и ему открыто всё.
   * Проверку исполняет сервер — он и ответит отказом, если когда-нибудь
   * появится учётная запись с урезанным доступом.
   */
  const can = (_code: string) => true;

  const [params, setParams] = useSearchParams();
  const raw = params.get('tab');
  const tab: TabKey = TABS.some((item) => item.key === raw) ? (raw as TabKey) : 'main';
  const pick = (key: TabKey) =>
    setParams((was) => {
      const next = new URLSearchParams(was);
      if (key === 'main') next.delete('tab');
      else next.set('tab', key);
      return next;
    }, { replace: true });

  const [attempt, setAttempt] = useState(0);
  const [block] = useBlock((signal) => api.office(id, signal), `office-setup|${id}|${attempt}`);
  const [pointsAttempt, setPointsAttempt] = useState(0);
  const [points] = useBlock(
    (signal) => api.qrPoints({ office_id: id }, signal),
    `office-setup-points|${id}|${attempt}|${pointsAttempt}`,
  );
  // Офис, только что сохранённый формой, — сразу в сводку, не дожидаясь
  // повторного запроса.
  const [fresh, setFresh] = useState<api.OfficeFull | null>(null);
  useEffect(() => setFresh(null), [id]);

  const office = fresh ?? (block.state === 'ready' ? block.data : null);
  const saved = (next: api.OfficeFull) => {
    setFresh(next);
    setAttempt((n) => n + 1);
  };

  const breadcrumb = office ? `Офисы и регионы / ${office.name}` : 'Офисы и регионы';

  return (
    <AppShell breadcrumb={breadcrumb} section="offices">
      <div className="ofs">
        <header className="ofs-head">
          <Link className="ofs-back" to="/offices">
            <AppIcon name="back" size={16} />
            К офисам и регионам
          </Link>
          <div className="ofs-head__row">
            <div className="ofs-head__text">
              <h1 className="ofs-head__title">
                {office ? office.name : 'Настройка офиса'}
              </h1>
              {office && (
                <p className="ofs-head__sub">
                  <span className="ofs-head__name">{office.region_name ?? '—'}</span>
                  <i aria-hidden="true">·</i>
                  <span className={office.status === 'ACTIVE' ? 'ofs-live ofs-live--ok' : 'ofs-live ofs-live--off'}>
                    {office.status === 'ACTIVE' ? 'Активен' : 'Неактивен'}
                  </span>
                </p>
              )}
            </div>
          </div>
          <nav className="ofs-tabs" role="tablist" aria-label="Разделы настройки офиса">
            {TABS.map((item) => (
              <button key={item.key} type="button" role="tab" aria-selected={tab === item.key}
                      className={tab === item.key ? 'ofs-tab ofs-tab--on' : 'ofs-tab'}
                      onClick={() => pick(item.key)}>
                {item.title}
              </button>
            ))}
          </nav>
        </header>

        {block.state === 'loading' && !office && (
          <>
            <span className="visually-hidden" role="status">Загружаем офис…</span>
            <div className="ofs-sk" aria-hidden="true">
              <i className="ofs-sk__block ofs-sk__side" />
              <i className="ofs-sk__block ofs-sk__main" />
            </div>
          </>
        )}
        {block.state === 'denied' && <p className="ofs-empty">Нет доступа к этому офису.</p>}
        {block.state === 'error' && !office && (
          <p className="ofs-empty ofs-empty--bad">Не удалось загрузить офис.</p>
        )}

        {office && (
          <div className={WIDE.has(tab) ? 'ofs-grid ofs-grid--wide' : 'ofs-grid'}>
            {/* На карте сводка слева ни к чему: она повторяет то, что
                человек прямо сейчас правит, и отнимает у карты треть
                ширины — а точку ставят именно по ней. */}
            {!WIDE.has(tab) && (
              <Summary office={office} points={points.state === 'ready' ? points.data.items : null}
                       onTab={pick} />
            )}
            {/* Ключ — вкладка: содержимое проявляется при переключении и
                не пересоздаётся при обновлении данных офиса. */}
            <section className="ofs-main" key={tab} aria-label={TABS.find((item) => item.key === tab)?.title}>
              {tab === 'main' && (
                <div className="ofs-card">
                  <h2 className="ofs-title">Основное</h2>
                  <p className="ofs-sub">Название и регион офиса.</p>
                  {can('offices.manage') ? (
                    <OfficeForm key={office.updated_at} office={office} onDone={saved} />
                  ) : (
                    <dl className="ofs-facts">
                      <div><dt>Название</dt><dd>{office.name}</dd></div>
                      <div><dt>Адрес</dt><dd>{office.address || 'Не указан'}</dd></div>
                      <div><dt>Часовой пояс</dt><dd>{office.timezone}</dd></div>
                    </dl>
                  )}
                </div>
              )}
              {tab === 'geo' && (
                <GeoEditor office={office} canManage={can('offices.manage')} onSaved={saved}
                           onRecheck={() => setAttempt((n) => n + 1)} />
              )}
              {tab === 'qr' && (
                <QrPointsManager office={office} canManage={can('qr_points.manage')}
                                 located={isLocated(office)}
                                 onGeo={() => pick('geo')}
                                 onChanged={() => setPointsAttempt((n) => n + 1)} />
              )}
              {tab === 'staff' && (
                <div className="ofs-card">
                  <OfficeStaff officeId={office.id} />
                </div>
              )}
            </section>
          </div>
        )}
      </div>
    </AppShell>
  );
}

// --- новый офис ------------------------------------------------------------------

/**
 * Новый офис: регион и название пришли из диалога, здесь — место.
 *
 * Офис создаётся одним запросом вместе с точкой и радиусом или контуром.
 * Без места сервер его не заведёт: офис, где не работает ни одна проверка
 * присутствия, не должен существовать даже минуту.
 */
export function NewOfficePage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const regionId = params.get('region') ?? '';
  const name = (params.get('name') ?? '').trim();

  return (
    <AppShell breadcrumb="Офисы и регионы / Новый офис" section="offices">
      <div className="ofs">
        <header className="ofs-head">
          <Link className="ofs-back" to="/offices">
            <AppIcon name="back" size={16} />
            К офисам и регионам
          </Link>
          <div className="ofs-head__row">
            <div className="ofs-head__text">
              <h1 className="ofs-head__title">{name || 'Новый офис'}</h1>
              <p className="ofs-head__sub">
                Поставьте точку входа и радиус или обведите контур — без места офис не создаётся.
              </p>
            </div>
          </div>
        </header>
        {!regionId ? (
          <p className="ofs-empty ofs-empty--bad">
            Не выбран регион. <Link className="link" to="/offices">Вернуться к офисам</Link>
          </p>
        ) : (
          <div className="ofs-grid ofs-grid--wide">
            <section className="ofs-main" aria-label="Геолокация нового офиса">
              <GeoEditor
                office={null}
                canManage
                submit={(body) => api.createOffice({
                  region_id: regionId, ...(name ? { name } : {}), ...body,
                })}
                // Сразу к QR-точкам: место есть, без кодов офис ещё не работает.
                onSaved={(made) => navigate(`/offices/${made.id}/setup?tab=qr`, { replace: true })}
              />
            </section>
          </div>
        )}
      </div>
    </AppShell>
  );
}

// --- сводка слева ----------------------------------------------------------------

function Summary({ office, points, onTab }: {
  office: api.OfficeFull;
  points: api.QrPoint[] | null;
  onTab: (key: TabKey) => void;
}) {
  const located = isLocated(office);
  const active = points?.filter((point) => point.is_active) ?? [];
  const entry = active.some((point) => point.direction_mode !== 'EXIT');
  const leave = active.some((point) => point.direction_mode !== 'ENTRY');

  return (
    <aside className="ofs-summary" aria-label="Краткая карточка офиса">
      <h2 className="ofs-title">Офис</h2>
      {/* Часового пояса здесь нет: в стране он один, и строка про него
          только отодвигала вниз то, ради чего сюда смотрят. */}
      <dl className="ofs-facts">
        <div><dt>Регион</dt><dd>{office.region_name ?? '—'}</dd></div>
        <div><dt>Адрес</dt><dd title={office.address ?? undefined}>{office.address || 'Не указан'}</dd></div>
        <div>
          <dt>Геозона</dt>
          <dd>{api.geofenceLabel(office)}</dd>
        </div>
        <div>
          <dt>QR-точки</dt>
          <dd>{points === null ? '—' : `${active.length} активные из ${points.length}`}</dd>
        </div>
      </dl>

      <h3 className="ofs-summary__title">Готовность к отметке</h3>
      <ul className="ofs-steps">
        <Step done={located} title="Геозона на карте" onClick={() => onTab('geo')} />
        <Step done={entry} title="QR-код для входа" onClick={() => onTab('qr')} />
        <Step done={leave} title="QR-код для выхода" onClick={() => onTab('qr')} />
      </ul>
      <p className="ofs-note">
        Сотрудник отмечается, отсканировав код у двери: бот сверяет его геопозицию
        с точкой офиса.
      </p>
    </aside>
  );
}

function Step({ done, title, onClick }: { done: boolean; title: string; onClick: () => void }) {
  return (
    <li>
      {/* Только слово о состоянии. Кружок со галочкой повторял то же
          самое рисунком и занимал место в узкой колонке. */}
      <button type="button" className={done ? 'ofs-step ofs-step--done' : 'ofs-step'} onClick={onClick}>
        <span className="ofs-step__title">{title}</span>
        <span className="ofs-step__state">{done ? 'Готово' : 'Не готово'}</span>
      </button>
    </li>
  );
}

// --- геолокация ------------------------------------------------------------------

type Draft = {
  place: Place | null;
  radius: number;
  address: string;
  /** Круг вокруг точки или контур участка. */
  shape: Shape;
  /** Углы контура по порядку обхода. Хранятся и в режиме круга: вернуться
      к контуру можно, не рисуя его заново. */
  polygon: Place[];
};

/** Контур строкой — чтобы сравнить черновик с сохранённым. */
function cornersKey(corners: Place[]): string {
  return corners.map((one) => `${one.lat.toFixed(6)},${one.lon.toFixed(6)}`).join(';');
}

/**
 * Площадь контура в квадратных метрах — для подписи, не для решения.
 * Плоская проекция вокруг первого угла: на участке меньше километра она
 * ошибается на доли процента.
 */
function areaOf(corners: Place[]): number {
  if (corners.length < 3) return 0;
  const [first] = corners;
  const metres = 6_371_000 * Math.PI / 180;
  const k = Math.cos((first!.lat * Math.PI) / 180);
  const xy = corners.map((one) => [
    (one.lon - first!.lon) * metres * k,
    (one.lat - first!.lat) * metres,
  ] as const);
  let twice = 0;
  xy.forEach(([x1, y1], index) => {
    const [x2, y2] = xy[(index + 1) % xy.length]!;
    twice += x1 * y2 - x2 * y1;
  });
  return Math.abs(twice) / 2;
}

/**
 * Пара полей с координатами.
 *
 * Своё состояние строками, а не числами: пока человек набирает
 * «41.3», строка ещё не число, и превращать её в точку рано. Наверх
 * уходит только разобранная пара в допустимых пределах.
 */
function Coordinates({ place, onPlace, onDone }: {
  place: Place | null;
  onPlace: (place: Place) => void;
  /** Человек закончил ввод (ушёл из полей или нажал Enter): карту — к точке. */
  onDone?: () => void;
}) {
  const [lat, setLat] = useState(place ? String(Number(place.lat.toFixed(6))) : '');
  const [lon, setLon] = useState(place ? String(Number(place.lon.toFixed(6))) : '');

  // Точку могли передвинуть на карте — поля идут следом.
  useEffect(() => {
    if (!place) return;
    setLat(String(Number(place.lat.toFixed(6))));
    setLon(String(Number(place.lon.toFixed(6))));
  }, [place]);

  const apply = (nextLat: string, nextLon: string) => {
    const a = Number(nextLat.replace(',', '.'));
    const b = Number(nextLon.replace(',', '.'));
    if (nextLat.trim() && nextLon.trim() && Number.isFinite(a) && Number.isFinite(b)
        && Math.abs(a) <= 90 && Math.abs(b) <= 180) {
      onPlace({ lat: a, lon: b });
    }
  };

  return (
    <div className="ofs-coords"
         onBlur={(event) => {
           if (!event.currentTarget.contains(event.relatedTarget as Node | null)) onDone?.();
         }}
         onKeyDown={(event) => { if (event.key === 'Enter') onDone?.(); }}>
      <label>
        <span>Широта</span>
        <input className="ofs-field__input" value={lat} inputMode="decimal" placeholder="41.311081"
               onChange={(event) => { setLat(event.target.value); apply(event.target.value, lon); }} />
      </label>
      <label>
        <span>Долгота</span>
        <input className="ofs-field__input" value={lon} inputMode="decimal" placeholder="69.240562"
               onChange={(event) => { setLon(event.target.value); apply(lat, event.target.value); }} />
      </label>
    </div>
  );
}

/** Два черновика совпадают: точка, радиус, адрес, форма и контур. */
function sameDraft(a: Draft, b: Draft): boolean {
  return (a.place?.lat ?? null) === (b.place?.lat ?? null)
    && (a.place?.lon ?? null) === (b.place?.lon ?? null)
    && a.radius === b.radius
    && a.address.trim() === b.address.trim()
    && a.shape === b.shape
    && cornersKey(a.polygon) === cornersKey(b.polygon);
}

/**
 * Черновик карты. Если новое место уже предложено и ждёт подтверждения,
 * на карте — оно: подтверждают то, что видят, а не то, что действует.
 */
function draftOf(office: api.OfficeFull | null): Draft {
  if (!office) {
    return { place: null, radius: RADIUS_DEFAULT, address: '', shape: 'CIRCLE', polygon: [] };
  }
  const zone = office.pending_location ?? office;
  // Пустое значение — «точки нет», а не 0,0: `Number('')` дал бы ноль, и
  // офис встал бы в Гвинейский залив.
  const coordinate = (value: unknown) =>
    value === null || value === undefined || String(value).trim() === '' ? NaN : Number(value);
  const lat = coordinate(zone.latitude);
  const lon = coordinate(zone.longitude);
  return {
    place: Number.isFinite(lat) && Number.isFinite(lon) ? { lat, lon } : null,
    radius: zone.geofence_radius_m ?? RADIUS_DEFAULT,
    address: office.address ?? '',
    shape: zone.geofence_kind === 'POLYGON' ? 'POLYGON' : 'CIRCLE',
    polygon: (zone.geofence_polygon ?? []).map(([lat, lon]) => ({ lat, lon })),
  };
}

/**
 * Карта, точка и зона офиса.
 *
 * `office === null` — офиса ещё нет: редактор работает как первый шаг
 * создания, и `submit` заводит офис вместе с местом. Без места офис не
 * создаётся, а место у существующего офиса нельзя снять — только заменить.
 * Замена не действует сразу: сервер кладёт её в ожидание, и до
 * подтверждения отметки сверяются с прежним местом.
 */
export function GeoEditor({ office, canManage, onSaved, onRecheck, submit, submitLabel }: {
  office: api.OfficeFull | null;
  canManage: boolean;
  onSaved: (office: api.OfficeFull) => void;
  /** Перечитать офис с сервера — после обрыва, когда исход неизвестен. */
  onRecheck?: () => void;
  /** Куда уходит сохранение. По умолчанию — правка существующего офиса. */
  submit?: (body: Record<string, unknown>) => Promise<api.OfficeFull>;
  submitLabel?: string;
}) {
  const creating = office === null;
  const initial = useMemo(() => draftOf(office), [office]);
  const [draft, setDraft] = useState<Draft>(initial);
  const [picking, setPicking] = useState(false);
  const [focus, setFocus] = useState<Place | null>(null);
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<Found[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, string[]>>({});
  const [done, setDone] = useState<string | null>(null);
  const lookup = useRef<AbortController | null>(null);
  const hunt = useRef<AbortController | null>(null);
  /** Поиск адреса не ответил — это не «ничего не нашлось». */
  const [huntFailed, setHuntFailed] = useState(false);
  /** Замок отправки: двойное нажатие быстрее перерисовки. */
  const lock = useRef(false);
  /** Следующий снимок офиса принять в черновик (после своего сохранения). */
  const adopt = useRef(false);
  const shown = useRef(initial);

  // Свежий снимок офиса — в черновик, только если человек его не правил
  // (или это ответ на его же сохранение). Фоновое обновление не затирает
  // несохранённую точку, радиус или контур.
  useEffect(() => {
    setDraft((was) => (adopt.current || sameDraft(was, shown.current) ? initial : was));
    adopt.current = false;
    shown.current = initial;
  }, [initial]);

  const dirty =
    (draft.place?.lat ?? null) !== (initial.place?.lat ?? null)
    || (draft.place?.lon ?? null) !== (initial.place?.lon ?? null)
    || draft.radius !== initial.radius
    || draft.address.trim() !== initial.address.trim()
    || draft.shape !== initial.shape
    || cornersKey(draft.polygon) !== cornersKey(initial.polygon);
  const fenced = draft.shape === 'POLYGON';
  const ready = fenced ? draft.polygon.length >= 3 : draft.place !== null;
  const pending = office?.pending_location ?? null;

  const corners = useCallback((next: Place[]) => {
    setDraft((was) => ({ ...was, polygon: next }));
    setDone(null);
  }, []);

  const place = useCallback((next: Place) => {
    setDraft((was) => ({ ...was, place: next }));
    setPicking(false);
    setDone(null);
    // Адрес по точке — подсказка, а не приказ: если HR уже вписал свой,
    // перетаскивание точки его не затирает.
    lookup.current?.abort();
    const stop = new AbortController();
    lookup.current = stop;
    lookupAddress(next, stop.signal)
      .then((text) => {
        if (!text || stop.signal.aborted) return;
        setDraft((was) => (was.address.trim() && was.address !== initial.address ? was : { ...was, address: text }));
      })
      .catch(() => undefined);
  }, [initial.address]);

  /**
   * Поиск идёт сам, пока человек набирает.
   *
   * Кнопки «Найти» рядом со строкой нет, и её отсутствие читалось как
   * «поиск не работает»: набрал — и ничего. Пауза в полсекунды нужна
   * не для красоты: у Nominatim ограничение в один запрос в секунду,
   * и посылать его на каждую букву нельзя.
   */
  const find = useCallback(async (text: string) => {
    hunt.current?.abort();
    const stop = new AbortController();
    hunt.current = stop;
    setSearching(true);
    setHuntFailed(false);
    try {
      const rows = await lookupPlaces(text, stop.signal);
      if (!stop.signal.aborted) setFound(rows);
    } catch {
      if (!stop.signal.aborted) {
        setFound([]);
        setHuntFailed(true);
      }
    } finally {
      if (!stop.signal.aborted) setSearching(false);
    }
  }, []);

  useEffect(() => {
    const text = query.trim();
    if (text.length < 3) {
      hunt.current?.abort();
      setFound(null);
      setSearching(false);
      return;
    }
    const timer = setTimeout(() => void find(text), 500);
    return () => clearTimeout(timer);
  }, [query, find]);

  function choose(item: Found) {
    setDraft((was) => ({ ...was, place: item.place, address: shortLabel(item.label) }));
    setFocus(item.place);
    setFound(null);
    setQuery('');
    setPicking(false);
    setDone(null);
  }

  async function save(next: Draft) {
    if (lock.current) return;
    lock.current = true;
    setSending(true);
    setFailed(null);
    setFields({});
    setDone(null);
    try {
      const address = next.address.trim() ? { address: next.address.trim() } : {};
      // Контур уходит углами; точку офиса — его центр — ставит сервер.
      // Круг уходит прежним видом. Пустого места не бывает: снять его нельзя.
      const body: Record<string, unknown> = next.shape === 'POLYGON'
        ? {
            geofence_kind: 'POLYGON',
            geofence_polygon: next.polygon.map((one) => [
              Number(one.lat.toFixed(6)), Number(one.lon.toFixed(6)),
            ]),
            ...address,
          }
        : {
            geofence_kind: 'CIRCLE',
            latitude: next.place!.lat.toFixed(6),
            longitude: next.place!.lon.toFixed(6),
            geofence_radius_m: next.radius,
            ...address,
          };
      const send = submit ?? ((payload: Record<string, unknown>) => api.updateOffice(office!.id, payload));
      const savedOffice = await send(body);
      // Сервер, который о контуре не знает, молча отбрасывает его поля и
      // возвращает офис кругом. Писать «сохранён» в этом случае — неправда:
      // человек уходит, уверенный, что зона настроена.
      const zone = savedOffice.pending_location ?? savedOffice;
      if (next.shape === 'POLYGON'
          && (zone.geofence_kind !== 'POLYGON' || !zone.geofence_polygon?.length)) {
        setFailed('Сервер не сохранил контур: эта версия сервера его ещё не поддерживает. '
          + 'Контур на экране остался — сохраните его после обновления сервера.');
        return;
      }
      setDone(
        savedOffice.pending_location
          ? 'Новое местоположение сохранено и ждёт подтверждения. Пока действует прежнее.'
          : next.shape === 'POLYGON' ? 'Контур территории сохранён' : 'Расположение сохранено',
      );
      adopt.current = true;
      onSaved(savedOffice);
    } catch (error) {
      if (error instanceof ApiFailure && error.kind === 'offline') {
        // Запрос мог дойти: повторять вслепую нельзя — у нового офиса это
        // дубль, у существующего — второе ожидающее место. Черновик остаётся.
        setFailed(creating
          ? 'Связь прервалась — неизвестно, создан ли офис. Проверьте список офисов, прежде чем создавать снова.'
          : 'Связь прервалась — неизвестно, сохранилось ли место. Офис перечитан: сверьте его с картой, прежде чем сохранять снова.');
        onRecheck?.();
      } else {
        setFailed(messageFor(error));
      }
      const details = (error as { fields?: Record<string, string[]> }).fields;
      if (details) setFields(details);
    } finally {
      lock.current = false;
      setSending(false);
    }
  }

  /** Решение по ожидающему месту: подтвердить или отбросить. */
  async function settle(confirm: boolean) {
    if (lock.current || !office) return;
    lock.current = true;
    setSending(true);
    setFailed(null);
    setDone(null);
    try {
      const updated = confirm
        ? await api.confirmOfficeLocation(office.id)
        : await api.discardOfficeLocation(office.id);
      setDone(confirm ? 'Новое местоположение действует' : 'Предложение отменено, действует прежнее местоположение');
      adopt.current = true;
      onSaved(updated);
    } catch (error) {
      setFailed(error instanceof ApiFailure && error.kind === 'offline'
        ? 'Связь прервалась — неизвестно, принято ли решение. Офис перечитан: проверьте, какое место действует.'
        : messageFor(error));
      if (error instanceof ApiFailure && ['offline', 'conflict'].includes(error.kind)) onRecheck?.();
    } finally {
      lock.current = false;
      setSending(false);
    }
  }

  return (
    <div className="ofs-geo">
      <div className="ofs-map">
        <MapView place={draft.place} radius={draft.radius} picking={picking}
                   editable={canManage} onPlace={place} focus={focus}
                   shape={draft.shape} polygon={draft.polygon} onPolygon={corners} />

        {canManage && (
          <div className="ofs-map__tools">
            <form className="ofs-find" role="search"
                  onSubmit={(event) => {
                    event.preventDefault();
                    // Те же три буквы, что и у поиска по ходу набора.
                    if (query.trim().length >= 3) void find(query.trim());
                  }}>
              <AppIcon name="search" size={18} />
              <input value={query} placeholder="Найти адрес или место" aria-label="Поиск адреса"
                     onChange={(event) => setQuery(event.target.value)} />
              {searching && <span className="ofs-find__state">Ищем…</span>}
              {!searching && query && (
                <button type="button" className="ofs-find__clear" aria-label="Очистить поиск"
                        onClick={() => { setQuery(''); setFound(null); }}>
                  <AppIcon name="close" size={16} />
                </button>
              )}
            </form>

            {found !== null && (
              <div className="ofs-found">
                {found.length === 0 ? (
                  <p className="ofs-found__none" role="status">
                    {huntFailed
                      ? 'Поиск адреса сейчас недоступен. Поставьте точку на карте или введите координаты.'
                      : 'Ничего не нашлось. Попробуйте короче или поставьте точку на карте.'}
                  </p>
                ) : (
                  <ul>
                    {found.map((item) => (
                      <li key={`${item.place.lat},${item.place.lon}`}>
                        <button type="button" onClick={() => choose(item)}>
                          <AppIcon name="pin" size={16} />
                          <span>{item.label}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}

        {canManage && fenced && draft.polygon.length < 3 && (
          <div className="ofs-map__hint" role="status">
            {draft.polygon.length === 0
              ? 'Нажмите на карту в углу участка — и дальше по порядку обхода'
              : `Отмечено углов: ${draft.polygon.length}. Нужно не меньше трёх`}
          </div>
        )}

        {picking && !fenced && (
          <div className="ofs-map__hint" role="status">
            Нажмите на карту там, где вход в офис
            <button type="button" className="ofs-link" onClick={() => setPicking(false)}>Отмена</button>
          </div>
        )}
      </div>

      <div className="ofs-geo__panel">
        <h2 className="ofs-title">Точка и зона</h2>

        <div className="ofs-shape" role="radiogroup" aria-label="Форма зоны">
          {(['CIRCLE', 'POLYGON'] as const).map((kind) => (
            <button key={kind} type="button" role="radio" aria-checked={draft.shape === kind}
                    className={draft.shape === kind ? 'ofs-shape__opt ofs-shape__opt--on' : 'ofs-shape__opt'}
                    disabled={!canManage}
                    onClick={() => {
                      setDraft((was) => ({ ...was, shape: kind }));
                      setPicking(false);
                      setDone(null);
                    }}>
              {kind === 'CIRCLE' ? 'Круг' : 'Контур территории'}
            </button>
          ))}
        </div>

        <div className="ofs-geo__fact">
          <span className="ofs-geo__label">Адрес</span>
          <p className="ofs-geo__value">
            {draft.address || <span className="ofs-geo__none">Появится по точке на карте</span>}
          </p>
        </div>

        {fenced ? (
          <div className="ofs-geo__fact">
            <span className="ofs-geo__value--row ofs-geo__label">
              Контур территории
              {canManage && (
                <button type="button" className="ofs-link" disabled={draft.polygon.length === 0}
                        onClick={() => corners(draft.polygon.slice(0, -1))}>
                  Отменить точку
                </button>
              )}
            </span>
            <p className="ofs-geo__value">
              {draft.polygon.length === 0
                ? <span className="ofs-geo__none">Углы не отмечены</span>
                : `${draft.polygon.length} ${api.cornersWord(draft.polygon.length)}`
                  + (draft.polygon.length >= 3
                    ? ` · около ${Math.round(areaOf(draft.polygon)).toLocaleString('ru-RU')} м²`
                    : '')}
            </p>
            {fields['geofence_polygon'] && (
              <span className="ofs-field__error">{fields['geofence_polygon'].join(' ')}</span>
            )}
            {canManage && draft.polygon.length > 0 && (
              <button type="button" className="ofs-link ofs-link--start" onClick={() => corners([])}>
                Очистить контур
              </button>
            )}
          </div>
        ) : (<>
        <div className="ofs-geo__fact">
          <span className="ofs-geo__value--row ofs-geo__label">
            Координаты
            {canManage && (
              <button type="button" className="ofs-link" onClick={() => setPicking(true)}>
                {draft.place ? 'Изменить на карте' : 'Указать на карте'}
              </button>
            )}
          </span>
          {canManage ? (
            /* Числа можно и набрать: координаты приходят из договора
               или от службы безопасности готовыми, и заставлять искать
               то же место мышью — лишняя работа с худшей точностью. */
            <Coordinates place={draft.place}
                         onPlace={(next) => { setDraft((was) => ({ ...was, place: next })); setDone(null); }}
                         onDone={() => { if (draft.place) setFocus({ ...draft.place }); }} />
          ) : (
            <p className="ofs-geo__value">
              {draft.place
                ? `${draft.place.lat.toFixed(6)}, ${draft.place.lon.toFixed(6)}`
                : <span className="ofs-geo__none">Точка не выбрана</span>}
            </p>
          )}
          {(fields['latitude'] || fields['longitude']) && (
            <span className="ofs-field__error">
              {[...(fields['latitude'] ?? []), ...(fields['longitude'] ?? [])].join(' ')}
            </span>
          )}
        </div>

        <div className="ofs-radius">
          <div className="ofs-radius__head">
            <span className="ofs-geo__label">Радиус допуска</span>
            <b>{draft.radius} м</b>
          </div>
          <input type="range" min={RADIUS_MIN} max={RADIUS_MAX} step={10} value={draft.radius}
                 disabled={!canManage} aria-label="Радиус допуска, метров"
                 onChange={(event) => setDraft((was) => ({ ...was, radius: Number(event.target.value) }))} />
          <div className="ofs-radius__scale"><span>{RADIUS_MIN} м</span><span>{RADIUS_MAX} м</span></div>
          {fields['geofence_radius_m'] && (
            <span className="ofs-field__error">{fields['geofence_radius_m'].join(' ')}</span>
          )}
        </div>
        </>)}

        <p className="ofs-hint">
          {fenced
            ? 'Обведите по углам само здание или огороженную территорию, без дороги '
              + 'и соседних домов. Угол можно перетащить. Погрешность телефона сервер '
              + 'учитывает сам: у самой границы он попросит сотрудника повторить, а не '
              + 'пропустит и не откажет наугад.'
            : 'Сотрудник может отметить вход или выход только внутри этой зоны.'}
        </p>

        {pending && office && (
          <div className="ofs-confirm ofs-pending" role="region" aria-label="Новое местоположение ждёт подтверждения">
            <span>
              <b>Новое местоположение ждёт подтверждения.</b> На карте — предложенное;
              отметки пока сверяются с прежним ({api.geofenceLabel(office)}).
              {office.pending_location_by_name && <> Предложил(а): {office.pending_location_by_name}.</>}
            </span>
            {canManage && (
              <>
                <button type="button" className="ofs-btn ofs-btn--blue" disabled={sending}
                        onClick={() => void settle(true)}>
                  Подтвердить
                </button>
                <button type="button" className="ofs-btn" disabled={sending}
                        onClick={() => void settle(false)}>
                  Отменить
                </button>
              </>
            )}
          </div>
        )}

        {failed && <p className="ofs-alert" role="alert">{failed}</p>}
        {done && <p className="ofs-done" role="status">{done}</p>}

        {canManage && (
          <div className="ofs-geo__actions">
            {/* «Очистить» нет: офис без места не остаётся — его можно
                только заменить, и замена ждёт подтверждения. */}
            <button type="button" className="ofs-btn ofs-btn--blue"
                    disabled={sending || !ready || (!creating && !dirty)}
                    onClick={() => void save(draft)}>
              {sending ? 'Сохраняем…' : submitLabel ?? (creating ? 'Создать офис' : 'Сохранить')}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function isLocated(office: api.OfficeFull): boolean {
  return api.geofenceReady(office);
}

/** Первые части длинного названия из поиска: «Амира Темура, 107, Ташкент». */
function shortLabel(label: string): string {
  return label.split(',').map((part) => part.trim()).filter(Boolean).slice(0, 4).join(', ');
}
