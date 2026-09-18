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
import { Field, OfficeForm, Staff } from '../components/OfficeCard';
import { OfficeMap, addressOf, searchAddress, type Found, type Place } from '../components/OfficeMap';
import { QrPointsManager } from '../components/QrPointsManager';
import { useBlock } from '../features/dashboard/data';
import '../styles/offices.css';
import '../styles/office-setup.css';

const TABS = [
  { key: 'main', title: 'Основное', icon: 'building' },
  { key: 'geo', title: 'Геолокация', icon: 'pin' },
  { key: 'qr', title: 'QR-точки', icon: 'grid' },
  { key: 'staff', title: 'Сотрудники', icon: 'users' },
] as const;

type TabKey = (typeof TABS)[number]['key'];

const RADIUS_MIN = 50;
const RADIUS_MAX = 500;
const RADIUS_DEFAULT = 100;

const STATUS_TITLE: Record<string, string> = {
  ACTIVE: 'Активен',
  INACTIVE: 'Отключён',
  CLOSED: 'Закрыт',
  ARCHIVED: 'В архиве',
};

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
            Офисы и регионы
          </Link>
          <div className="ofs-head__row">
            <div className="ofs-head__text">
              <h1 className="ofs-head__title">{office ? office.name : 'Настройка офиса'}</h1>
              {office && (
                <p className="ofs-head__sub">
                  <span className={office.status === 'ACTIVE' ? 'ofs-state ofs-state--ok' : 'ofs-state ofs-state--off'}>
                    <i aria-hidden="true" />
                    {STATUS_TITLE[office.status] ?? office.status}
                  </span>
                  <span>{office.region_name ?? '—'}</span>
                  <span>{office.timezone}</span>
                </p>
              )}
            </div>
          </div>
          <nav className="ofs-tabs" role="tablist" aria-label="Разделы настройки офиса">
            {TABS.map((item) => (
              <button key={item.key} type="button" role="tab" aria-selected={tab === item.key}
                      className={tab === item.key ? 'ofs-tab ofs-tab--on' : 'ofs-tab'}
                      onClick={() => pick(item.key)}>
                <AppIcon name={item.icon} size={16} />
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
          <div className="ofs-grid">
            <Summary office={office} points={points.state === 'ready' ? points.data.items : null}
                     onTab={pick} />
            <section className="ofs-main" aria-label={TABS.find((item) => item.key === tab)?.title}>
              {tab === 'main' && (
                <div className="ofs-card">
                  <h2 className="ofs-title">Основное</h2>
                  <p className="ofs-sub">Название, адрес и часовой пояс. По часовому поясу считаются опоздания.</p>
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
                                 onChanged={() => setPointsAttempt((n) => n + 1)} />
              )}
              {tab === 'staff' && (
                <div className="ofs-card">
                  <h2 className="ofs-title">Сотрудники офиса</h2>
                  <p className="ofs-sub">
                    Кто назначен в этот офис. Графики работы назначаются в карточке
                    сотрудника.
                  </p>
                  <div className="ofs-staff">
                    <Staff officeId={office.id} />
                  </div>
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
      <dl className="ofs-facts">
        <div><dt>Адрес</dt><dd title={office.address ?? undefined}>{office.address || 'Не указан'}</dd></div>
        <div><dt>Регион</dt><dd>{office.region_name ?? '—'}</dd></div>
        <div><dt>Часовой пояс</dt><dd>{office.timezone}</dd></div>
        <div>
          <dt>Геозона</dt>
          <dd>{located ? `${office.geofence_radius_m} м` : 'не настроена'}</dd>
        </div>
        <div>
          <dt>QR-точки</dt>
          <dd>{points === null ? '—' : `${active.length} активных из ${points.length}`}</dd>
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
      <button type="button" className={done ? 'ofs-step ofs-step--done' : 'ofs-step'} onClick={onClick}>
        <span className="ofs-step__mark" aria-hidden="true">
          {done ? <AppIcon name="check" size={16} /> : null}
        </span>
        <span className="ofs-step__title">{title}</span>
        <span className="ofs-step__state">{done ? 'готово' : 'настроить'}</span>
      </button>
    </li>
  );
}

// --- геолокация ------------------------------------------------------------------

type Draft = { place: Place | null; radius: number; address: string };

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
  const [manual, setManual] = useState(false);
  const lookup = useRef<AbortController | null>(null);

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
    addressOf(next, stop.signal)
      .then((text) => {
        if (!text || stop.signal.aborted) return;
        setDraft((was) => (was.address.trim() && was.address !== initial.address ? was : { ...was, address: text }));
      })
      .catch(() => undefined);
  }, [initial.address]);

  async function find() {
    const text = query.trim();
    if (!text) return;
    setSearching(true);
    setFound(null);
    try {
      setFound(await searchAddress(text));
    } catch {
      setFound([]);
    } finally {
      setSearching(false);
    }
  }

  function choose(item: Found) {
    setDraft((was) => ({ ...was, place: item.place, address: shortLabel(item.label) }));
    setFocus(item.place);
    setFound(null);
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
        <OfficeMap place={draft.place} radius={draft.radius} picking={picking}
                   editable={canManage} onPlace={place} focus={focus} />
        {canManage && !draft.place && !picking && (
          <div className="ofs-map__overlay">
            <p>У офиса ещё нет точки на карте.</p>
            <button type="button" className="ofs-btn ofs-btn--blue" onClick={() => setPicking(true)}>
              <AppIcon name="pin" size={16} />
              Указать на карте
            </button>
          </div>
        )}
        {picking && (
          <div className="ofs-map__hint" role="status">
            Нажмите на карту там, где вход в офис
            <button type="button" className="ofs-link" onClick={() => setPicking(false)}>Отмена</button>
          </div>
        )}
      </div>

      <div className="ofs-card ofs-geo__panel">
        <h2 className="ofs-title">Расположение и допустимая зона</h2>

        {canManage && (
          <form className="ofs-search" role="search" onSubmit={(event) => { event.preventDefault(); void find(); }}>
            <AppIcon name="search" size={16} />
            <input value={query} placeholder="Найти адрес: Ташкент, Амира Темура 107"
                   aria-label="Поиск адреса" onChange={(event) => setQuery(event.target.value)} />
            <button type="submit" className="ofs-btn" disabled={searching || !query.trim()}>
              {searching ? 'Ищем…' : 'Найти'}
            </button>
          </form>
        )}
        {found !== null && (
          found.length === 0 ? (
            <p className="ofs-empty">Ничего не нашлось. Попробуйте короче или поставьте точку на карте.</p>
          ) : (
            <ul className="ofs-found">
              {found.map((item) => (
                <li key={`${item.place.lat},${item.place.lon}`}>
                  <button type="button" onClick={() => choose(item)}>
                    <AppIcon name="pin" size={16} />
                    <span>{item.label}</span>
                  </button>
                </li>
              ))}
            </ul>
          )
        )}

        <label className="ofs-field">
          <span className="ofs-field__label">Адрес на карточке офиса</span>
          <input className="ofs-field__input" value={draft.address} disabled={!canManage}
                 aria-label="Адрес офиса" placeholder="Заполнится по точке на карте"
                 onChange={(event) => setDraft((was) => ({ ...was, address: event.target.value }))} />
          {fields['address'] && <span className="ofs-field__error">{fields['address'].join(' ')}</span>}
        </label>

        <div className="ofs-radius">
          <div className="ofs-radius__head">
            <span className="ofs-field__label">Радиус зоны</span>
            <b>{draft.radius} м</b>
          </div>
          <input type="range" min={RADIUS_MIN} max={RADIUS_MAX} step={10} value={draft.radius}
                 disabled={!canManage} aria-label="Радиус зоны, метров"
                 onChange={(event) => setDraft((was) => ({ ...was, radius: Number(event.target.value) }))} />
          <div className="ofs-radius__scale"><span>{RADIUS_MIN} м</span><span>{RADIUS_MAX} м</span></div>
          {fields['geofence_radius_m'] && (
            <span className="ofs-field__error">{fields['geofence_radius_m'].join(' ')}</span>
          )}
        </div>

        <p className="ofs-info">
          <AppIcon name="alert" size={16} />
          Сотрудник сможет отметить вход или выход только в пределах этой зоны.
        </p>

        <p className="ofs-coords">
          {draft.place
            ? <>Точка: {draft.place.lat.toFixed(6)}, {draft.place.lon.toFixed(6)}</>
            : 'Точка не выбрана'}
          {canManage && (
            <button type="button" className="ofs-link" onClick={() => setManual((was) => !was)}>
              {manual ? 'Скрыть ввод координат' : 'Ввести координаты вручную'}
            </button>
          )}
        </p>
        {manual && canManage && (
          <ManualCoordinates place={draft.place} errors={fields}
                             onPlace={(next) => { setDraft((was) => ({ ...was, place: next })); setFocus(next); }} />
        )}

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
            <div className="ofs-actions ofs-actions--wrap">
              <button type="button" className="ofs-btn ofs-btn--blue"
                      disabled={sending || !draft.place || !dirty} onClick={() => void save(draft)}>
                {sending ? 'Сохраняем…' : 'Сохранить расположение'}
              </button>
              <button type="button" className="ofs-btn" disabled={sending || !dirty}
                      onClick={() => { setDraft(initial); setFailed(null); setFields({}); setFocus(initial.place); }}>
                Отменить изменения
              </button>
              <button type="button" className="ofs-btn ofs-btn--ghost"
                      disabled={sending || initial.place === null} onClick={() => setConfirmClear(true)}>
                Очистить геолокацию
              </button>
            </div>
          )
        )}
      </div>
    </div>
  );
}

function ManualCoordinates({ place, errors, onPlace }: {
  place: Place | null;
  errors: Record<string, string[]>;
  onPlace: (place: Place) => void;
}) {
  const [lat, setLat] = useState(place ? String(place.lat) : '');
  const [lon, setLon] = useState(place ? String(place.lon) : '');
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
    <div className="ofs-manual">
      <Field label="Широта" value={lat} inputMode="decimal" placeholder="41.311081"
             errors={errors['latitude']}
             onChange={(value) => { setLat(value); apply(value, lon); }} />
      <Field label="Долгота" value={lon} inputMode="decimal" placeholder="69.240562"
             errors={errors['longitude']}
             onChange={(value) => { setLon(value); apply(lat, value); }} />
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
