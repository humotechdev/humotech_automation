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
import { Link, useParams, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { messageFor } from '../api/errors';
import { AppShell } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { OfficeForm } from '../components/OfficeCard';
import { OfficeStaff } from '../components/OfficeStaff';
import { OfficeMap, addressOf, searchAddress, type Found, type Place } from '../components/OfficeMap';
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

export function OfficeSetupPage() {
  const { id = '' } = useParams();
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

        {block.state === 'loading' && !office && <p className="ofs-empty">Загружаем офис…</p>}
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
            <section className="ofs-main" aria-label={TABS.find((item) => item.key === tab)?.title}>
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
                <GeoEditor office={office} canManage={can('offices.manage')} onSaved={saved} />
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
          <dt>Радиус</dt>
          <dd>{located ? `${office.geofence_radius_m} м` : 'не задан'}</dd>
        </div>
        <div>
          <dt>QR-точки</dt>
          <dd>{points === null ? '—' : `${active.length} активные из ${points.length}`}</dd>
        </div>
      </dl>

      <h3 className="ofs-summary__title">Готовность к отметке</h3>
      <ul className="ofs-steps">
        <Step done={located} title="Точка на карте и радиус" onClick={() => onTab('geo')} />
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

type Draft = { place: Place | null; radius: number; address: string };

/**
 * Пара полей с координатами.
 *
 * Своё состояние строками, а не числами: пока человек набирает
 * «41.3», строка ещё не число, и превращать её в точку рано. Наверх
 * уходит только разобранная пара в допустимых пределах.
 */
function Coordinates({ place, onPlace }: {
  place: Place | null;
  onPlace: (place: Place) => void;
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
    <div className="ofs-coords">
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

function draftOf(office: api.OfficeFull): Draft {
  const lat = office.latitude === null ? NaN : Number(office.latitude);
  const lon = office.longitude === null ? NaN : Number(office.longitude);
  return {
    place: Number.isFinite(lat) && Number.isFinite(lon) ? { lat, lon } : null,
    radius: office.geofence_radius_m ?? RADIUS_DEFAULT,
    address: office.address ?? '',
  };
}

function GeoEditor({ office, canManage, onSaved }: {
  office: api.OfficeFull;
  canManage: boolean;
  onSaved: (office: api.OfficeFull) => void;
}) {
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
  const [confirmClear, setConfirmClear] = useState(false);
  const lookup = useRef<AbortController | null>(null);
  const hunt = useRef<AbortController | null>(null);

  useEffect(() => setDraft(initial), [initial]);

  const dirty =
    (draft.place?.lat ?? null) !== (initial.place?.lat ?? null)
    || (draft.place?.lon ?? null) !== (initial.place?.lon ?? null)
    || draft.radius !== initial.radius
    || draft.address.trim() !== initial.address.trim();

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
    try {
      const rows = await lookupPlaces(text, stop.signal);
      if (!stop.signal.aborted) setFound(rows);
    } catch {
      if (!stop.signal.aborted) setFound([]);
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

  async function save(next: Draft | null) {
    if (sending) return;
    setSending(true);
    setFailed(null);
    setFields({});
    setDone(null);
    try {
      const body: Record<string, unknown> = next?.place
        ? {
            latitude: next.place.lat.toFixed(6),
            longitude: next.place.lon.toFixed(6),
            geofence_radius_m: next.radius,
            ...(next.address.trim() ? { address: next.address.trim() } : {}),
          }
        : { latitude: null, longitude: null, geofence_radius_m: null };
      const savedOffice = await api.updateOffice(office.id, body);
      setDone(next?.place ? 'Расположение сохранено' : 'Геолокация очищена');
      setConfirmClear(false);
      onSaved(savedOffice);
    } catch (error) {
      setFailed(messageFor(error));
      const details = (error as { fields?: Record<string, string[]> }).fields;
      if (details) setFields(details);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="ofs-geo">
      <div className="ofs-map">
        <MapView place={draft.place} radius={draft.radius} picking={picking}
                   editable={canManage} onPlace={place} focus={focus} />

        {canManage && (
          <div className="ofs-map__tools">
            <form className="ofs-find" role="search"
                  onSubmit={(event) => { event.preventDefault(); void find(query.trim()); }}>
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
                  <p className="ofs-found__none">Ничего не нашлось. Попробуйте короче или поставьте точку на карте.</p>
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

        {picking && (
          <div className="ofs-map__hint" role="status">
            Нажмите на карту там, где вход в офис
            <button type="button" className="ofs-link" onClick={() => setPicking(false)}>Отмена</button>
          </div>
        )}
      </div>

      <div className="ofs-geo__panel">
        <h2 className="ofs-title">Точка и зона</h2>

        <div className="ofs-geo__fact">
          <span className="ofs-geo__label">Адрес</span>
          <p className="ofs-geo__value">
            {draft.address || <span className="ofs-geo__none">Появится по точке на карте</span>}
          </p>
        </div>

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
                         onPlace={(next) => { setDraft((was) => ({ ...was, place: next })); setFocus(next); }} />
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

        <p className="ofs-hint">
          Сотрудник может отметить вход или выход только внутри этой зоны.
        </p>

        {failed && <p className="ofs-alert" role="alert">{failed}</p>}
        {done && <p className="ofs-done" role="status">{done}</p>}

        {canManage && (
          confirmClear ? (
            <div className="ofs-confirm" role="alertdialog" aria-label="Очистить геолокацию">
              <span>
                Без точки на карте печатные QR-коды офиса перестанут принимать
                отметки. Очистить?
              </span>
              <button type="button" className="ofs-btn ofs-btn--danger" disabled={sending}
                      onClick={() => void save(null)}>
                {sending ? 'Очищаем…' : 'Очистить'}
              </button>
              <button type="button" className="ofs-btn" onClick={() => setConfirmClear(false)}>Отмена</button>
            </div>
          ) : (
            <div className="ofs-geo__actions">
              <button type="button" className="ofs-btn"
                      disabled={sending || initial.place === null} onClick={() => setConfirmClear(true)}>
                Очистить
              </button>
              <button type="button" className="ofs-btn ofs-btn--blue"
                      disabled={sending || !draft.place || !dirty} onClick={() => void save(draft)}>
                {sending ? 'Сохраняем…' : 'Сохранить'}
              </button>
            </div>
          )
        )}
      </div>
    </div>
  );
}

function isLocated(office: api.OfficeFull): boolean {
  return office.latitude !== null && office.longitude !== null && (office.geofence_radius_m ?? 0) > 0;
}

/** Первые части длинного названия из поиска: «Амира Темура, 107, Ташкент». */
function shortLabel(label: string): string {
  return label.split(',').map((part) => part.trim()).filter(Boolean).slice(0, 4).join(', ');
}
